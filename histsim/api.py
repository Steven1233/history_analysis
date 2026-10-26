# -*- coding: utf-8 -*-
"""检索会话 API：把数据源、配置、引擎、结果收敛成一个可被界面/脚本共用的大门面。

界面（Web 或桌面）只依赖这里的方法；命令行脚本也可以直接用：
    from histsim import SearchSession
    s = SearchSession()
    s.register_root(r"I:\\")
    tid = s.start({"template": "plan_A", "target_date": "2024-04-27"})
    while s.progress(tid)["state"] == "running": ...
    print(s.result(tid)["products"])
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
import traceback
import uuid
import zipfile
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from . import config as cfgmod
from .config import ELEMENTS, SearchConfig, builtin_template
from .datasource import Store, discover_and_register
from .engine import DEFAULT_PERCENTILE, PRECIP_IO_WORKERS, Cancelled, precip_products, \
    run_search
from .precip import DEFAULT_WINDOWS, GROUPS, PRECIP_PRODUCTS, PreprocessCancelled, \
    run_preprocess, scan_source
from .readers import nearest_slice, precip_axes, read_precip_batch, read_precip_region

# 界面默认展示的排名条数（后端仍按 TopN 全量计算）
RANK_VIEW_LIMIT = 80

# 结果落盘文件名：检索很贵（冷盘分钟级），进程重启后要还能回看
RESULT_FILE = "result.json"


def _save_result(out_dir: str, view: Dict) -> None:
    with open(os.path.join(out_dir, RESULT_FILE), "w", encoding="utf-8") as fh:
        json.dump(view, fh, ensure_ascii=False, default=str)


PRECIP_CACHE_FILE = "precip_cache.npz"
# result.json 里展示/回放的排名条数；完整排名存在 npz，重算不受该值限制
PRECIP_RANK_VIEW_LIMIT = 300


def _save_precip_cache(out_dir: str, res: Dict) -> None:
    """把完整相似日排名 + 前 N 名降水场落盘，供结果页改 TopN/百分位时重算。

    排名数组（日期/SI/统计点 mm）保存**全部**候选，因此 TopN 可任意放大；
    二维场只缓存前 ``PRECIP_FIELD_CACHE`` 名，超出部分重算时按排名回读产品文件。
    """
    precip = res.get("precip") or {}
    windows = precip.get("windows") or {}
    if not windows:
        return
    payload: Dict[str, np.ndarray] = {
        "lat": np.asarray(precip.get("_lat"), dtype=np.float64),
        "lon": np.asarray(precip.get("_lon"), dtype=np.float64),
    }
    n_rank = 0
    for key, win in windows.items():
        rank_dates = win.get("_rank_dates") or []
        if rank_dates:
            payload[f"{key}__rank_dates"] = np.asarray(rank_dates, dtype="U10")
            payload[f"{key}__rank_si"] = np.asarray(win.get("_rank_si"),
                                                    dtype=np.float64)
            payload[f"{key}__rank_mm"] = np.asarray(win.get("_rank_mm"),
                                                    dtype=np.float32)
            n_rank += len(rank_dates)
        fields = win.get("_fields")
        if fields is None:
            continue
        payload[f"{key}__fields"] = np.asarray(fields, dtype=np.float32)
        payload[f"{key}__target"] = np.asarray(win.get("target_field"),
                                               dtype=np.float32)
        payload[f"{key}__dates"] = np.asarray(win.get("_dates") or [], dtype="U10")
    if len(payload) <= 2 or not n_rank:
        return
    try:
        np.savez_compressed(os.path.join(out_dir, PRECIP_CACHE_FILE), **payload)
    except OSError:
        pass  # 缓存写失败不影响检索主流程，重算退回直读产品文件


def _load_precip_cache(task_dir: str) -> Dict[str, np.ndarray]:
    path = os.path.join(task_dir, PRECIP_CACHE_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with np.load(path) as z:
            return {k: z[k] for k in z.files}
    except (OSError, ValueError):
        return {}


def _rank_rows(cache: Dict[str, np.ndarray], key: str, w: Dict) -> List[Dict]:
    """取窗口的**完整**相似日排名：优先读 npz，旧结果退回 result.json。

    新结果在 npz 中保存 ``__rank_dates/__rank_si/__rank_mm``（全部候选），
    因此 TopN 可超过二维场缓存条数；旧结果只有 result.json 里的排名子集。
    """
    dates = cache.get(f"{key}__rank_dates")
    if dates is not None and len(dates):
        si = cache.get(f"{key}__rank_si")
        mm = cache.get(f"{key}__rank_mm")
        rows: List[Dict] = []
        for i, d in enumerate(dates):
            rows.append({
                "rank": i + 1,
                "date": str(d),
                "si": float(si[i]) if si is not None and i < len(si) else 0.0,
                "mm": (round(float(mm[i]), 2)
                       if mm is not None and i < len(mm) else None),
            })
        return rows
    return list(w.get("ranking") or [])


class TaskState:
    """一次检索任务的运行态（进度、日志、结果）。"""

    def __init__(self, task_id: str, cfg: SearchConfig, out_dir: str):
        self.id = task_id
        self.cfg = cfg
        self.out_dir = out_dir
        self.state = "running"            # running | done | failed | cancelled
        self.stage = "init"
        self.label = "准备中"
        self.done = 0
        self.total = 0
        self.msg = ""
        self.log: List[Dict[str, Any]] = []
        self.error = ""
        self.result: Optional[Dict] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None

    # ── 进度快照（供界面轮询）─────────────────────────────────────────
    def snapshot(self) -> Dict:
        elapsed = (self.finished_at or time.time()) - self.started_at
        pct = round(100.0 * self.done / self.total, 1) if self.total else 0.0
        return {
            "task_id": self.id,
            "state": self.state,
            "stage": self.stage,
            "label": self.label,
            "done": self.done,
            "total": self.total,
            "pct": pct,
            "msg": self.msg,
            "error": self.error,
            "elapsed_s": round(elapsed, 1),
            "log": self.log[-40:],
            "config": self.cfg.to_dict(),
            "target_date": self.cfg.target_date,
        }


class PreprocessJob:
    """一次数据预处理任务的运行态（进度、日志、结果）。"""

    def __init__(self, job_id: str, params: Dict):
        self.id = job_id
        self.params = params
        self.state = "running"            # running | done | failed | cancelled
        self.stage = "init"
        self.label = "准备中"
        self.done = 0
        self.total = 0
        self.msg = ""
        self.log: List[Dict[str, Any]] = []
        self.error = ""
        self.result: Optional[Dict] = None
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.cancel = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def snapshot(self) -> Dict:
        elapsed = (self.finished_at or time.time()) - self.started_at
        pct = round(100.0 * self.done / self.total, 1) if self.total else 0.0
        return {
            "job_id": self.id, "state": self.state, "stage": self.stage,
            "label": self.label, "done": self.done, "total": self.total,
            "pct": pct, "msg": self.msg, "error": self.error,
            "elapsed_s": round(elapsed, 1), "log": self.log[-40:],
            "params": self.params, "result": self.result,
        }


class SearchSession:
    """统一门面：数据源管理 → 配置 → 检索 → 结果。"""

    def __init__(self, data_dir: Optional[str] = None):
        self.store = Store(data_dir)
        self._tasks: Dict[str, TaskState] = {}
        self._lock = threading.Lock()
        self._order: List[str] = []
        self._jobs: Dict[str, PreprocessJob] = {}
        self._job_order: List[str] = []
        self._job_lock = threading.Lock()

    # ══ 数据源 ════════════════════════════════════════════════════════
    def sources(self) -> Dict:
        srcs = self.store.registered_sources()
        return {
            "sources": srcs,
            "n_era5": sum(1 for s in srcs if s["kind"] == "era5"),
            "n_gmcp": sum(1 for s in srcs if s["kind"] == "gmcp"),
            "n_gmcp_rain": sum(1 for s in srcs if s["kind"] == "gmcp_rain"),
            "n_files": sum(int(c["n_files"]) for s in srcs for c in s["coverage"]),
        }

    def register_root(self, root: str, depth: int = 1) -> Dict:
        return discover_and_register(self.store, root, depth=depth)

    def remove_source(self, source_id: int) -> Dict:
        self.store.remove_source(int(source_id))
        self.store.log("移除数据源", str(source_id), "", "成功")
        return {"ok": True, "sources": self.sources()}

    # ══ 看板 ══════════════════════════════════════════════════════════
    def dashboard(self) -> Dict:
        srcs = self.sources()
        coverage = self.store.coverage_grid()
        years = sorted({y for el in coverage.values() for y in el})
        try:
            du = shutil.disk_usage(self.store.data_dir)
            disk = {"free_gb": round(du.free / 2**30, 1),
                    "total_gb": round(du.total / 2**30, 1)}
        except OSError:
            disk = {}
        return {
            "data_dir": self.store.data_dir,
            "counts": {"n_era5": srcs["n_era5"], "n_gmcp": srcs["n_gmcp"],
                       "n_gmcp_rain": srcs["n_gmcp_rain"],
                       "n_files": srcs["n_files"]},
            "coverage": coverage,
            "elements": [{"key": k, "name": v[2], "units": v[3],
                          "file_element": v[0]} for k, v in ELEMENTS.items()],
            "precip_windows": [{"key": k, "name": p.label(), "group": p.group,
                                 "duration_h": p.duration_h}
                                for k, p in PRECIP_PRODUCTS.items()],
            "years": {"from": years[0] if years else None,
                      "to": years[-1] if years else None, "list": years},
            "disk": disk,
            "recent_ops": self.store.operations(10),
            "recent_tasks": self.store.tasks(8),
        }

    # ══ 模板与配置 ════════════════════════════════════════════════════
    def templates(self) -> Dict:
        return {k: builtin_template(k).to_dict() for k in cfgmod.BUILTIN_KEYS}

    def resolve_config(self, raw: Optional[Dict]) -> SearchConfig:
        """把界面提交的字典解析为配置：可带 template 预设，其余字段覆盖预设。"""
        raw = dict(raw or {})
        tpl = raw.pop("template", None)
        base = builtin_template(tpl) if tpl in cfgmod.BUILTIN_KEYS else SearchConfig()
        cfg = base.copy()
        allowed = set(SearchConfig.__dataclass_fields__)  # type: ignore[attr-defined]
        for k, v in raw.items():
            if k in allowed and v is not None:
                setattr(cfg, k, v)
        cfg.variables = [[str(e), float(l)] for e, l in cfg.variables]
        cfg.months = [int(m) for m in cfg.months]
        cfg.times = sorted({int(t) for t in cfg.times})
        cfg.rain_thresholds = [float(x) for x in cfg.rain_thresholds]
        cfg.sample_mode = str(cfg.sample_mode)
        cfg.feature_process = str(cfg.feature_process)
        cfg.similarity = str(cfg.similarity)
        cfg.similarity_formula = str(cfg.similarity_formula or "").strip()
        cfg.period_days = int(cfg.period_days)
        cfg.plot_direct_fields = bool(cfg.plot_direct_fields)
        cfg.precip_enabled = bool(cfg.precip_enabled)
        cfg.precip_windows = [str(k) for k in (cfg.precip_windows or [])]
        cfg.precip_topn = int(cfg.precip_topn)
        cfg.precip_thresholds = [float(x) for x in (cfg.precip_thresholds or [])]
        return cfg.normalize()

    # ══ 地图圈选（第 5 项）════════════════════════════════════════════
    def region_map_png(self) -> bytes:
        """广东省底图 PNG（坐标轴铺满画布，经纬度→像素严格线性）。"""
        from . import viz
        return viz.render_region_map_png()

    def region_bounds(self) -> Dict:
        """圈选底图的地理范围与像素尺寸，供前端换算鼠标位置 → 经纬度。"""
        from . import viz
        return {"view": list(viz.REGION_VIEW),
                "width": int(viz.REGION_PX[0]), "height": int(viz.REGION_PX[1])}

    def readiness(self, raw: Optional[Dict]) -> Dict:
        cfg = self.resolve_config(raw)
        out = self.store.readiness(cfg)
        out["config"] = cfg.to_dict()
        out["errors"] = cfg.validate()
        return out

    def preview(self, raw: Optional[Dict]) -> Dict:
        """不执行检索的代价预估：候选样本数、待读文件数、缺口。"""
        cfg = self.resolve_config(raw)
        errors = cfg.validate()
        if errors:
            return {"ok": False, "errors": errors, "config": cfg.to_dict()}
        dates = self.store.available_dates(cfg) if cfg.elements() else []
        precip_dates = (self.store.available_precip_dates(cfg)
                        if cfg.precip_active else [])
        # 每个「要素×日期」只需打开一次文件（同文件的多个时次/层次合并读）
        n_files = len(dates) * len(cfg.elements())
        n_gmcp = 0
        if cfg.enable_24h and cfg.elements():
            n_gmcp = max(1, cfg.inter_topn) * len(cfg.rain_window_24h)
        n_precip_files = len(precip_dates) * len(cfg.precip_windows)
        read = self.store.readiness(cfg)
        all_dates = dates or precip_dates
        return {
            "ok": True,
            "errors": [],
            "config": cfg.to_dict(),
            "n_samples": len(all_dates),
            "n_era5_samples": len(dates),
            "n_precip_samples": len(precip_dates),
            "sample_range": ([all_dates[0].isoformat(), all_dates[-1].isoformat()]
                             if all_dates else None),
            "n_era5_files": n_files,
            "n_era5_files_per_variable": len(dates),
            "n_gmcp_hours_est": n_gmcp,
            "n_precip_files": n_precip_files,
            "n_precip_windows": len(cfg.precip_windows) if cfg.precip_active else 0,
            "precip_ready": bool(cfg.precip_active and precip_dates),
            "coverage_pct": read["coverage_pct"],
            "ready": read["ready"],
            "n_gaps": len(read["gaps"]),
        }

    # ══ 数据预处理 ════════════════════════════════════════════════════
    def preprocess_meta(self) -> Dict:
        """预处理页面所需的静态字典：产品窗口、分组与默认目录。"""
        return {
            "ok": True,
            "products": [{
                "key": k, "group": p.group, "label": p.label(),
                "duration_h": p.duration_h, "folder": p.folder,
                "filename_example": p.filename(date(2000, 6, 1)),
                "anchor": p.anchor, "hour": p.hour,
            } for k, p in PRECIP_PRODUCTS.items()],
            "groups": [{"key": g, "name": {"24h": "24 小时", "12h": "12 小时",
                                           "6h": "6 小时"}[g],
                        "keys": keys} for g, keys in GROUPS.items()],
            "default_windows": list(DEFAULT_WINDOWS),
            "defaults": {
                "output_root": os.path.join(self.store.data_dir, "preprocessed"),
                "year_from": 2000, "year_to": 2024,
                "groups": list(GROUPS), "overwrite": False,
                # 留空＝按 CPU 核数与网格大小自动确定；可按需覆盖
                "workers": min(8, os.cpu_count() or 1),
            },
        }

    def preprocess_scan(self, raw: Dict) -> Dict:
        root = str((raw or {}).get("source_root") or "").strip().strip('"')
        if not root:
            raise ValueError("请填写 GMCP 逐小时源目录")
        if not os.path.isdir(root):
            raise ValueError(f"源目录不存在或不可访问：{root}")
        y1 = int((raw or {}).get("year_from") or 2000)
        y2 = int((raw or {}).get("year_to") or 2024)
        if y1 > y2:
            raise ValueError("起始年份不能大于结束年份")
        return self.preprocess_meta() | scan_source(root, y1, y2)

    def _preprocess_params(self, raw: Dict) -> Dict:
        raw = dict(raw or {})
        src = str(raw.get("source_root") or "").strip().strip('"')
        out = str(raw.get("output_root") or "").strip().strip('"') or \
            os.path.join(self.store.data_dir, "preprocessed")
        if not src:
            raise ValueError("请填写 GMCP 逐小时源目录")
        if not os.path.isdir(src):
            raise ValueError(f"源目录不存在或不可访问：{src}")
        groups = [str(g) for g in (raw.get("groups") or list(GROUPS))]
        bad = [g for g in groups if g not in GROUPS]
        if bad:
            raise ValueError("未知的降水产品分组：" + ", ".join(bad))
        keys = [k for g in groups for k in GROUPS[g]]
        y1 = int(raw.get("year_from") or 2000)
        y2 = int(raw.get("year_to") or 2024)
        if y1 > y2:
            raise ValueError("起始年份不能大于结束年份")
        if not (1900 <= y1 <= 2200 and 1900 <= y2 <= 2200):
            raise ValueError("年份超出合理范围")
        try:
            workers = int(raw.get("workers") or 0)
        except (TypeError, ValueError):
            raise ValueError("并行进程数必须是整数")
        if workers and not (1 <= workers <= 32):
            raise ValueError("并行进程数必须在 1 ～ 32 之间")
        return {
            "source_root": os.path.abspath(src),
            "output_root": os.path.abspath(out),
            "groups": groups, "products": keys,
            "year_from": y1, "year_to": y2,
            "overwrite": bool(raw.get("overwrite")),
            "workers": workers or None,
        }

    def preprocess_start(self, raw: Dict) -> Dict:
        params = self._preprocess_params(raw)
        job_id = "prep_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + \
            uuid.uuid4().hex[:6]
        st = PreprocessJob(job_id, params)
        with self._job_lock:
            self._jobs[job_id] = st
            self._job_order.append(job_id)
        st.thread = threading.Thread(target=self._run_preprocess, args=(st,),
                                     daemon=True, name=f"histsim-{job_id}")
        st.thread.start()
        return {"job_id": job_id, "params": params}

    def _run_preprocess(self, st: PreprocessJob) -> None:
        def report(stage: str, label: str, done: int, total: int, msg: str = "") -> None:
            if st.stage != stage or st.label != label:
                st.log.append({"t": round(time.time() - st.started_at, 1),
                               "stage": stage, "label": label})
            st.stage, st.label, st.done, st.total, st.msg = stage, label, done, total, msg

        p = st.params
        try:
            res = run_preprocess(p["source_root"], p["output_root"], p["products"],
                                 p["year_from"], p["year_to"],
                                 overwrite=p["overwrite"], progress=report,
                                 should_stop=st.cancel.is_set,
                                 workers=p.get("workers"))
            st.result = res
            st.state = "done"
            st.log.append({"t": round(time.time() - st.started_at, 1),
                           "stage": "done",
                           "label": f"完成：写出 {res['n_written']} 个产品"})
            self.store.log("数据预处理", p["output_root"],
                           f"{p['year_from']}–{p['year_to']} · 写出 {res['n_written']}",
                           "成功")
        except PreprocessCancelled:
            st.state = "cancelled"
            st.log.append({"t": round(time.time() - st.started_at, 1),
                           "stage": "cancelled", "label": "已取消"})
            self.store.log("数据预处理", p["output_root"], "用户取消", "已取消")
        except Exception as exc:
            st.state = "failed"
            st.error = f"{type(exc).__name__}: {exc}"
            st.log.append({"t": round(time.time() - st.started_at, 1),
                           "stage": "error", "label": st.error})
            self.store.log("数据预处理", p["output_root"], st.error, "失败")
        finally:
            st.finished_at = time.time()

    def preprocess_progress(self, job_id: str) -> Dict:
        st = self._jobs.get(job_id)
        if st is None:
            raise KeyError(f"未知预处理任务: {job_id}")
        return st.snapshot()

    def preprocess_cancel(self, job_id: str) -> Dict:
        st = self._jobs.get(job_id)
        if st is None:
            raise KeyError(f"未知预处理任务: {job_id}")
        st.cancel.set()
        return {"ok": True, "job_id": job_id}

    def preprocess_register(self, raw: Dict) -> Dict:
        root = str((raw or {}).get("output_root") or "").strip().strip('"') or \
            os.path.join(self.store.data_dir, "preprocessed")
        if not os.path.isdir(root):
            raise ValueError(f"预处理输出目录不存在：{root}")
        rep = discover_and_register(self.store, root, depth=1)
        return {"ok": True, **rep, "sources": self.sources()}

    def preprocess_jobs(self) -> List[Dict]:
        with self._job_lock:
            return [self._jobs[j].snapshot() for j in self._job_order[-20:]]

    # ══ 检索 ══════════════════════════════════════════════════════════
    def start(self, raw: Optional[Dict]) -> Dict:
        cfg = self.resolve_config(raw)
        errors = cfg.validate()
        if errors:
            raise ValueError("；".join(errors))
        if not cfg.target_date:
            raise ValueError("未指定目标日期")
        datetime.strptime(cfg.target_date, "%Y-%m-%d")  # 格式校验

        task_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        out_dir = os.path.join(self.store.results_dir, task_id)
        os.makedirs(out_dir, exist_ok=True)
        st = TaskState(task_id, cfg, out_dir)
        with self._lock:
            self._tasks[task_id] = st
            self._order.append(task_id)
        st.thread = threading.Thread(target=self._run, args=(st,), daemon=True,
                                     name=f"histsim-{task_id}")
        st.thread.start()
        return {"task_id": task_id, "config": cfg.to_dict()}

    def _run(self, st: TaskState) -> None:
        def report(stage: str, label: str, done: int, total: int, msg: str = "") -> None:
            if st.stage != stage or st.label != label:
                st.log.append({"t": round(time.time() - st.started_at, 1),
                               "stage": stage, "label": label})
            st.stage, st.label, st.done, st.total, st.msg = stage, label, done, total, msg

        try:
            res = run_search(st.cfg, self.store, progress=report,
                             should_stop=st.cancel.is_set, out_dir=st.out_dir)
            _save_precip_cache(st.out_dir, res)
            st.result = _result_view(st, res)
            _save_result(st.out_dir, st.result)
            st.state = "done"
            st.log.append({"t": round(time.time() - st.started_at, 1), "stage": "done",
                           "label": f"完成，耗时 {res['elapsed_s']}s"})
            n_final = ((res.get("combine") or {}).get(str(st.cfg.times[0]), {})
                       .get("n_final", 0))
            n_precip = len((res.get("precip") or {}).get("windows") or {})
            summary = f"样本 {res['samples']['n_used']} · 入组 {n_final} 个相似时刻"
            if n_precip:
                summary += f" · 累计降水窗口 {n_precip} 个"
            self.store.record_task(st.cfg, "成功", res["elapsed_s"], st.out_dir, summary)
            self.store.log("相似检索", st.cfg.target_date, st.cfg.name,
                           f"完成 {res['elapsed_s']}s")
        except Cancelled:
            st.state = "cancelled"
            st.log.append({"t": round(time.time() - st.started_at, 1), "stage": "cancelled",
                           "label": "已取消"})
            self.store.record_task(st.cfg, "已取消",
                                   time.time() - st.started_at, st.out_dir, "用户取消")
            self.store.log("相似检索", st.cfg.target_date, st.cfg.name, "已取消")
        except Exception as exc:
            st.state = "failed"
            st.error = f"{type(exc).__name__}: {exc}"
            st.log.append({"t": round(time.time() - st.started_at, 1), "stage": "error",
                           "label": st.error})
            st.log.append({"t": round(time.time() - st.started_at, 1), "stage": "error",
                           "label": traceback.format_exc(limit=3)})
            self.store.record_task(st.cfg, "失败", time.time() - st.started_at,
                                   st.out_dir, st.error)
            self.store.log("相似检索", st.cfg.target_date, st.cfg.name, st.error)
        finally:
            st.finished_at = time.time()

    def progress(self, task_id: str) -> Dict:
        st = self._tasks.get(task_id)
        if st is None:
            raise KeyError(f"未知任务: {task_id}")
        return st.snapshot()

    def cancel(self, task_id: str) -> Dict:
        st = self._tasks.get(task_id)
        if st is None:
            raise KeyError(f"未知任务: {task_id}")
        st.cancel.set()
        return {"ok": True, "task_id": task_id}

    def result(self, task_id: str) -> Dict:
        st = self._tasks.get(task_id)
        if st is None:
            return self._result_from_disk(task_id)
        if st.state == "running":
            return {"ok": False, "state": "running", "progress": st.snapshot()}
        if st.state == "failed":
            return {"ok": False, "state": "failed", "error": st.error}
        if st.state == "cancelled":
            return {"ok": False, "state": "cancelled"}
        return {"ok": True, "state": "done", **st.result}

    def _result_from_disk(self, task_id: str) -> Dict:
        """本会话没有该任务时，读回落盘结果——重启后仍能回看历史检索。"""
        if not task_id or os.path.basename(task_id) != task_id:  # 挡住路径穿越
            raise KeyError(f"未知任务: {task_id}")
        path = os.path.join(self.store.results_dir, task_id, RESULT_FILE)
        if not os.path.isfile(path):
            raise KeyError(f"未知任务: {task_id}")
        with open(path, encoding="utf-8") as fh:
            return {"ok": True, "state": "done", **json.load(fh)}

    def _view_of(self, task_id: str) -> tuple:
        """取任务结果视图：本会话直接复用内存对象，历史任务从 result.json 回读。"""
        st = self._tasks.get(task_id)
        if st is not None:
            if st.state == "running":
                raise ValueError("任务仍在运行，完成后才能重算产品")
            if st.state != "done" or st.result is None:
                raise ValueError(f"任务状态为 {st.state}，没有可重算的结果")
            return st, copy.deepcopy(st.result)
        view = self._result_from_disk(task_id)
        view.pop("ok", None)
        view.pop("state", None)
        return None, view

    # ══ 结果后处理：改 TopN / 百分位重算，不重新检索（V2.2.0）════════
    def recompute(self, raw: Optional[Dict]) -> Dict:
        raw = dict(raw or {})
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("缺少 task_id")
        st, view = self._view_of(task_id)
        P = view.get("precip") or {}
        windows = P.get("windows") or {}
        if not windows:
            raise ValueError("该结果没有 GMCP 累计降水产品，无法重算")
        task_dir = os.path.join(self.store.results_dir, task_id)
        cache = _load_precip_cache(task_dir)

        cur_topn = int(next(iter(windows.values())).get("topn") or 1)
        # 上限＝检索时保留的完整排名条数（不是二维场缓存条数），各窗口取交集
        max_topn = min(len(_rank_rows(cache, key, w))
                       for key, w in windows.items())
        if max_topn < 1:
            raise ValueError("该结果没有可用于重算的相似日排名")
        try:
            topn = int(raw.get("precip_topn") or cur_topn)
        except (TypeError, ValueError):
            raise ValueError("TopN 必须是整数")
        # 不再限制固定区间：超过可用排名时收敛到实际可用数；超出二维场缓存
        # 的部分在 _recompute_window 中按排名回读产品文件。
        topn = min(max(1, topn), max_topn)
        try:
            pct = float(raw.get("percentile")
                        if raw.get("percentile") is not None
                        else P.get("percentile", DEFAULT_PERCENTILE))
        except (TypeError, ValueError):
            raise ValueError("百分位必须是数字")
        if not 1 <= pct <= 99:
            raise ValueError("百分位必须在 1 ～ 99 之间")

        cfg = SearchConfig.from_dict(view.get("config") or {})
        target = date.fromisoformat(str(view.get("target_date"))[:10])

        fig_paths: Dict[str, str] = {}
        errors: Dict[str, str] = {}
        topn_done: List[int] = []
        figures_now = view.get("figures") or {}
        for key, w in list(windows.items()):
            # 只改百分位时主产品图（目标/平均/中位数/概率）不变，跳过重绘
            redraw_main = (topn != int(w.get("topn") or 0)
                           or f"precip_{key}" not in figures_now)
            try:
                updated, paths = self._recompute_window(
                    cfg, target, task_dir, key, w, topn, pct, cache,
                    redraw_main=redraw_main)
            except Exception as exc:  # 单窗口失败不中断其余窗口
                errors[f"error_precip_{key}"] = f"{type(exc).__name__}: {exc}"
                continue
            windows[key] = updated
            topn_done.append(int(updated.get("topn") or 0))
            fig_paths.update(paths)

        if not fig_paths and errors:
            raise ValueError("重算失败：" + "；".join(errors.values()))

        actual_topn = min(topn_done) if topn_done else topn

        version = int(time.time())
        figures = view.get("figures") or {}
        for name, path in fig_paths.items():
            figures[name] = (f"/results/{task_id}/{os.path.basename(path)}"
                             f"?v={version}")
        view["figures"] = figures
        fig_errors = view.get("figure_errors") or {}
        for name in list(fig_errors):
            if name.startswith("error_precip_"):
                fig_errors.pop(name, None)
        fig_errors.update(errors)
        view["figure_errors"] = fig_errors
        P["percentile"] = pct
        P["recompute"] = {"topn": actual_topn, "percentile": pct,
                          "at": datetime.now().isoformat(timespec="seconds")}
        view["precip"] = P
        _save_result(task_dir, view)
        if st is not None:
            st.result = view
        self.store.log("结果重算", task_id,
                       f"TopN={actual_topn} · P{pct:g}（未重新检索）", "成功")
        return {"ok": True, **view}

    def _read_ranked_fields(self, cfg: SearchConfig, key: str,
                            rows: List[Dict], lat: np.ndarray,
                            lon: np.ndarray) -> tuple:
        """按检索排名并行回读产品二维场，返回 ``(fields, dates)``。

        读取失败或网格不一致的日期直接跳过，由调用方继续向后取排名补足。
        """
        empty = np.zeros((0, lat.size, lon.size), dtype=np.float32)
        if not rows:
            return empty, []
        lat_lo, lat_hi = float(np.min(lat)), float(np.max(lat))
        lon_lo, lon_hi = float(np.min(lon)), float(np.max(lon))
        shape = (lat.size, lon.size)
        jobs, found = [], []
        for row in rows:
            try:
                d = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            p = self.store.find_precip(key, d)
            if p:
                jobs.append((p, lat_lo, lat_hi, lon_lo, lon_hi))
                found.append(str(row.get("date")))
        if not jobs:
            return empty, []
        got = read_precip_batch(jobs, workers=PRECIP_IO_WORKERS)
        fields, dates = [], []
        for p, d in zip([j[0] for j in jobs], found):
            arr = got.get(p)
            if arr is not None and np.asarray(arr).shape == shape:
                fields.append(np.asarray(arr, dtype=np.float32))
                dates.append(d)
        if not fields:
            return empty, []
        return np.stack(fields, axis=0), dates

    def _recompute_window(self, cfg: SearchConfig, target: date,
                          task_dir: str, key: str, w: Dict, topn: int,
                          pct: float, cache: Dict[str, np.ndarray],
                          redraw_main: bool = True):
        """单个窗口的产品重算：缓存场 + 按排名回读补齐，TopN 不受缓存限制。"""
        rows_all = _rank_rows(cache, key, w)
        n_avail = len(rows_all)
        if n_avail < 1:
            raise ValueError("该窗口没有可用于重算的相似日排名")
        n = min(max(1, int(topn)), n_avail)

        fields_cached = cache.get(f"{key}__fields")
        target_cached = cache.get(f"{key}__target")
        lat = cache.get("lat")
        lon = cache.get("lon")
        use_cache = (fields_cached is not None and target_cached is not None
                     and lat is not None and lon is not None
                     and np.isfinite(lat).all() and np.isfinite(lon).all())
        cached_n = 0
        if use_cache:
            fields_all = np.asarray(fields_cached, dtype=np.float32)
            target_field = np.asarray(target_cached, dtype=np.float32)
            lat = np.asarray(lat, dtype=np.float64)
            lon = np.asarray(lon, dtype=np.float64)
            cached_n = len(fields_all)
            take = min(n, cached_n)
            parts = [fields_all[:take]] if take else []
            used = rows_all[:take]
        else:
            # 旧结果没有可用 npz 缓存：按配置区域直读目标日与相似日产品
            target_path = self.store.find_precip(key, target)
            if target_path is None:
                raise ValueError(f"{PRECIP_PRODUCTS[key].label()} 目标日产品缺失")
            lat_full, lon_full = precip_axes(target_path)
            lat_lo = cfg.center_lat - cfg.half_span_deg
            lat_hi = cfg.center_lat + cfg.half_span_deg
            lon_lo = cfg.center_lon - cfg.half_span_deg
            lon_hi = cfg.center_lon + cfg.half_span_deg
            lat = lat_full[nearest_slice(lat_full, lat_lo, lat_hi)]
            lon = lon_full[nearest_slice(lon_full, lon_lo, lon_hi)]
            target_field, _, _ = read_precip_region(target_path, lat_lo, lat_hi,
                                                    lon_lo, lon_hi)
            parts, used = [], []
            pos = 0

        # 超出二维场缓存的名次按检索排名回读；个别文件缺失时继续向后补足
        pos = len(used)
        need = n - len(used)
        for _ in range(3):
            if need <= 0 or pos >= n_avail:
                break
            chunk = rows_all[pos:min(pos + need, n_avail)]
            extra, dates = self._read_ranked_fields(cfg, key, chunk, lat, lon)
            if len(extra):
                parts.append(extra)
                used.extend({"rank": r["rank"], "date": d,
                             "si": r["si"], "mm": None}
                            for r, d in zip(chunk, dates))
                need -= len(extra)
            pos += len(chunk)
        if not parts:
            raise ValueError("没有可读取的相似日产品文件，无法重算")

        top = np.concatenate(parts, axis=0).astype(np.float32)
        n_valid = len(top)
        used = used[:n_valid]
        by_date = {r["date"]: r for r in rows_all}
        for r in used:
            src = by_date.get(r["date"])
            if src is not None and r.get("mm") is None:
                r["mm"] = src.get("mm")

        i_pt = int(np.argmin(np.abs(lat - cfg.point_lat)))
        j_pt = int(np.argmin(np.abs(lon - cfg.point_lon)))
        ths = sorted({float(t) for t in (w.get("thresholds") or [])})
        prod, spatial = precip_products(top, target_field, i_pt, j_pt, ths, pct)
        prod["samples"] = [{"date": r["date"], "mm": r["mm"]}
                           for r in sorted(used, key=lambda r: -(r.get("mm") or 0))]

        win = dict(w)
        win.update({
            "topn": int(n_valid),
            "n_valid": int(n_valid),
            "percentile": float(pct),
            "products": prod,
            "target_field": target_field,
            "spatial_ready": True,
            "max_topn": int(n_avail),
            "n_available": int(n_avail),
            "cached_topn": int(cached_n),
        })
        win["ranking"] = used[:PRECIP_RANK_VIEW_LIMIT]
        from . import viz
        paths: Dict[str, str] = {}
        if redraw_main:
            p1 = viz.render_precip_product(cfg, {**win, "products": {**prod,
                                           "spatial": spatial}}, lat, lon, task_dir)
            if p1:
                paths[f"precip_{key}"] = p1
        p2 = viz.render_precip_quantile(cfg, {**win, "products": {**prod,
                                           "spatial": spatial}}, lat, lon,
                                       task_dir, percentile=pct)
        if p2:
            paths[f"precip_quantile_{key}"] = p2
        return win, paths

    # ══ 结果导出：JSON + 全部产品图 + 累计降水排名 CSV（V2.2.0）══════
    def export(self, raw: Optional[Dict]) -> Dict:
        raw = dict(raw or {})
        task_id = str(raw.get("task_id") or "").strip()
        if not task_id or os.path.basename(task_id) != task_id:
            raise ValueError("缺少或非法的 task_id")
        task_dir = os.path.join(self.store.results_dir, task_id)
        if not os.path.isdir(task_dir):
            raise KeyError(f"未知任务: {task_id}")
        _, view = self._view_of(task_id)

        lines = ["窗口,名次,日期,相似度指数SI,点累计降水(mm)"]
        for key, w in ((view.get("precip") or {}).get("windows") or {}).items():
            for row in w.get("ranking") or []:
                lines.append(",".join([
                    key, str(row.get("rank", "")), str(row.get("date", "")),
                    f"{float(row.get('si', 0)):.6f}", str(row.get("mm", "")),
                ]))
        csv_text = "\ufeff" + "\n".join(lines) + "\n"

        name = f"histsim_{task_id}_export.zip"
        path = os.path.join(task_dir, name)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("result.json",
                       json.dumps(view, ensure_ascii=False, indent=2, default=str))
            z.writestr("GMCP_累计降水相似排名.csv", csv_text.encode("utf-8"))
            for fn in sorted(os.listdir(task_dir)):
                if fn.lower().endswith(".png"):
                    z.write(os.path.join(task_dir, fn), arcname=f"figures/{fn}")
        self.store.log("结果导出", task_id, f"{len(view.get('figures') or {})} 张图",
                       "成功")
        return {"ok": True, "url": f"/results/{task_id}/{name}",
                "filename": name, "size": os.path.getsize(path),
                "n_figures": len([f for f in os.listdir(task_dir)
                                  if f.lower().endswith(".png")])}

    def tasks(self, limit: int = 100) -> List[Dict]:
        with self._lock:
            live = {t: self._tasks[t].snapshot() for t in self._order[-20:]}
        return {"history": self.store.tasks(limit), "live": live}

    def operations(self, limit: int = 200) -> List[Dict]:
        return self.store.operations(limit)

    def live_tasks(self) -> List[Dict]:
        with self._lock:
            return [self._tasks[t].snapshot() for t in self._order[-20:]]

    def close(self) -> None:
        for st in self._tasks.values():
            st.cancel.set()
        for st in self._jobs.values():
            st.cancel.set()
        try:
            self.store.close()
        except Exception:
            pass


# ── 结果视图：剥离 numpy 大数组，图片转为可访问 URL ────────────────────
def _result_view(st: TaskState, res: Dict) -> Dict:
    ranking = {
        key: {ts: rows[:RANK_VIEW_LIMIT] for ts, rows in per_time.items()}
        for key, per_time in res["ranking"].items()
    }
    products = {}
    for ts, p in res["products"]["by_time"].items():
        products[ts] = {k: v for k, v in p.items() if k != "spatial"}
        products[ts]["spatial_ready"] = bool(p.get("spatial"))
    figures = {name: f"/results/{st.id}/{os.path.basename(path)}"
               for name, path in res["figures"].items() if not name.startswith("error")}
    fig_errors = {name: msg for name, msg in res["figures"].items()
                  if name.startswith("error")}
    src_precip = res.get("precip") or {}
    precip_windows = {}
    for key, win in (src_precip.get("windows") or {}).items():
        # target_field/_fields/_dates/_rank_* 是重算缓存，落盘到 npz，不进 JSON
        w = {k: v for k, v in win.items()
             if k not in ("products", "target_field", "_fields", "_dates",
                          "_rank_dates", "_rank_si", "_rank_mm")}
        prod = dict(win.get("products") or {})
        prod.pop("spatial", None)
        w["products"] = prod
        w["spatial_ready"] = bool((win.get("products") or {}).get("spatial"))
        # 完整排名保存在 npz，result.json 只展示前 N 条
        w["ranking"] = list(win.get("ranking") or [])[:PRECIP_RANK_VIEW_LIMIT]
        w["max_topn"] = int(win.get("max_topn")
                            or win.get("n_available") or len(win.get("ranking") or []))
        w["cached_topn"] = int(win.get("cached_topn") or len(w["ranking"]))
        w["n_available"] = int(win.get("n_available") or len(win.get("ranking") or []))
        w["percentile"] = float(win.get("percentile", DEFAULT_PERCENTILE))
        precip_windows[key] = w
    grid = res.get("grid") or res.get("gmcp_grid") or {}
    return {
        "task_id": st.id,
        "target_date": res["target_date"],
        "config": res["config"],
        "grid": grid,
        "gmcp_grid": res["gmcp_grid"] or grid,
        "samples": res["samples"],
        "times": res["times"],
        "ranking": ranking,
        "rank_view_limit": RANK_VIEW_LIMIT,
        "combine": res["combine"],
        "products": {"by_time": products, "thresholds": res["products"]["thresholds"],
                     "point": res["products"]["point"]},
        "rain_meta": {"n_hours_read": res["products"].get("n_hours_read"),
                      "n_moments": res["products"].get("n_moments")},
        "precip": {
            "enabled": bool(src_precip.get("enabled")),
            "available": bool(src_precip.get("available")),
            "reason": src_precip.get("reason", ""),
            "skipped": src_precip.get("skipped") or [],
            "n_candidates": src_precip.get("n_candidates", 0),
            "n_windows": src_precip.get("n_windows", 0),
            "parallel_workers": src_precip.get("parallel_workers", 1),
            "chunk_workers": src_precip.get("chunk_workers", 1),
            "io_workers": src_precip.get("io_workers", 1),
            "percentile": float(src_precip.get("percentile", DEFAULT_PERCENTILE)),
            "recompute": src_precip.get("recompute") or {},
            "point": src_precip.get("point") or [],
            "thresholds": src_precip.get("thresholds") or [],
            "grid": src_precip.get("grid") or {},
            "windows": precip_windows,
        },
        "figures": figures,
        "figure_errors": fig_errors,
        "timings": res["timings"],
        "elapsed_s": res["elapsed_s"],
    }


def _self_check() -> None:
    import tempfile
    tmp = tempfile.mkdtemp(prefix="histsim_api_")
    s = SearchSession(tmp)
    assert s.templates().keys() == {"plan_A", "plan_B"}
    assert s.dashboard()["counts"]["n_gmcp"] == 0
    # 模板解析：模板字段可被覆盖
    cfg = s.resolve_config({"template": "plan_A", "target_date": "2024-04-27",
                            "half_span_deg": 2.0, "times": [0, 6, 6]})
    assert cfg.half_span_deg == 2.0 and cfg.times == [0, 6]
    assert cfg.inter_group == ["u925", "v925", "rh500"]  # 来自模板
    assert cfg.name == "暖区降水检索"
    # 未知字段与模板均被忽略，退回默认配置
    cfg2 = s.resolve_config({"template": "不存在", "不存在字段": 1})
    assert cfg2.name == SearchConfig().name
    # 校验失败应抛出可读错误
    try:
        s.start({"template": "plan_A", "target_date": "2024-04-27",
                 "inter_group": ["不存在的要素"]})
        raise AssertionError("应该抛出校验错误")
    except ValueError as exc:
        assert "未勾选" in str(exc), exc
    # 未登记数据源时 preview 应给出 0 样本而不崩
    pv = s.preview({"template": "plan_A", "target_date": "2024-04-27"})
    assert pv["ok"] and pv["n_samples"] == 0, pv
    # V2.0.0 新字段的类型收敛（界面以字符串/数字提交）
    cfg3 = s.resolve_config({"template": "plan_A", "sample_mode": "same_period",
                             "period_days": "7", "feature_process": "normalize",
                             "similarity": "custom",
                             "similarity_formula": " sqrt(sum(w*d*d)) ",
                             "plot_direct_fields": True})
    assert cfg3.sample_mode == "same_period" and cfg3.period_days == 7
    assert cfg3.feature_process == "normalize" and cfg3.similarity == "custom"
    assert cfg3.similarity_formula == "sqrt(sum(w*d*d))" and cfg3.plot_direct_fields
    # V2.1.0：预处理元数据与累计降水配置
    pm = s.preprocess_meta()
    assert len(pm["products"]) == 8 and {g["key"] for g in pm["groups"]} == \
        {"24h", "12h", "6h"}, pm
    cfg4 = s.resolve_config({"template": "plan_A", "precip_enabled": False,
                             "precip_windows": [], "precip_topn": 12,
                             "precip_thresholds": [0.1, 30]})
    assert cfg4.precip_enabled is False and cfg4.precip_windows == []
    assert cfg4.precip_topn == 12 and cfg4.precip_thresholds == [0.1, 30.0]
    # 仅累计降水检索：不勾选 ERA5 要素也应通过校验
    only = s.resolve_config({"template": "plan_A", "variables": [],
                             "inter_group": [], "direct_group": [],
                             "target_date": "2024-06-01"})
    assert not only.validate(), only.validate()
    # 圈选底图接口（第 5 项）：范围与像素尺寸必须与 viz 的常量一致
    b = s.region_bounds()
    assert b["width"] == 1000 and b["height"] > 0 and len(b["view"]) == 4
    png = s.region_map_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000
    # ── V2.2.0：结果页改 TopN / 百分位重算 + 结果包导出 ─────────────
    tid = "20260101_000000_selftest"
    tdir = os.path.join(s.store.results_dir, tid)
    os.makedirs(tdir, exist_ok=True)
    cfg_s = builtin_template("plan_A").copy(
        target_date="2024-06-30", variables=[], inter_group=[], direct_group=[],
        precip_windows=["24h_2020"], precip_topn=5)
    # 完整排名 8 条，但二维场缓存只有前 5 条：超出部分重算时必须按排名回读
    ranking = [{"rank": i + 1, "date": f"2019-06-{i + 1:02d}", "si": float(i),
                "mm": float(i)} for i in range(8)]
    win_view = {"key": "24h_2020", "label": "24h 自检窗口", "topn": 5,
                "n_valid": 5, "max_topn": 8, "n_available": 8,
                "cached_topn": 5, "percentile": 90.0,
                "thresholds": [0.1, 50.0], "ranking": ranking[:5],
                "spatial_ready": True,
                "products": {"n_valid": 5, "spatial": None}}
    view = {"task_id": tid, "target_date": "2024-06-30",
            "config": cfg_s.to_dict(), "times": [0],
            "precip": {"enabled": True, "available": True, "windows":
                       {"24h_2020": win_view}, "percentile": 90.0},
            "figures": {}, "figure_errors": {}}
    _save_result(tdir, view)
    lat = np.linspace(22.0, 24.0, 2)
    lon = np.linspace(112.0, 114.0, 2)
    fields = np.arange(5 * 2 * 2, dtype=np.float32).reshape(5, 2, 2)
    np.savez_compressed(os.path.join(tdir, PRECIP_CACHE_FILE),
                        lat=lat, lon=lon,
                        **{"24h_2020__fields": fields,
                           "24h_2020__target": fields[-1] + 1,
                           "24h_2020__dates": np.array(
                               [r["date"] for r in ranking[:5]], dtype="U10"),
                           "24h_2020__rank_dates": np.array(
                               [r["date"] for r in ranking], dtype="U10"),
                           "24h_2020__rank_si": np.array(
                               [r["si"] for r in ranking], dtype=np.float64),
                           "24h_2020__rank_mm": np.array(
                               [r["mm"] for r in ranking], dtype=np.float32)})
    rc = s.recompute({"task_id": tid, "precip_topn": 3, "percentile": 60})
    assert rc["ok"] and rc["precip"]["percentile"] == 60.0
    assert rc["precip"]["windows"]["24h_2020"]["n_valid"] == 3
    assert len(rc["precip"]["windows"]["24h_2020"]["ranking"]) == 3
    assert rc["precip"]["windows"]["24h_2020"]["max_topn"] == 8
    assert "precip_24h_2020" in rc["figures"]
    assert "precip_quantile_24h_2020" in rc["figures"]
    assert rc["precip"]["windows"]["24h_2020"]["products"]["target_mm"] is not None
    # 重算结果必须落盘，重启后读回仍是新参数
    back = s.result(tid)
    assert back["precip"]["recompute"]["topn"] == 3
    exp = s.export({"task_id": tid})
    assert exp["url"].endswith(".zip") and exp["size"] > 0

    # TopN 超过二维场缓存（5 条）但不超过完整排名（8 条）：按排名回读补足，不报错
    orig_read = SearchSession._read_ranked_fields

    def _fake_read(self, cfg, key, rows, lat_, lon_):
        if not rows:
            return np.zeros((0, len(lat_), len(lon_)), dtype=np.float32), []
        arr = np.tile(fields[0], (len(rows), 1, 1)).astype(np.float32)
        return arr, [str(r["date"]) for r in rows]

    SearchSession._read_ranked_fields = _fake_read
    try:
        rc2 = s.recompute({"task_id": tid, "precip_topn": 8})
        w2 = rc2["precip"]["windows"]["24h_2020"]
        assert w2["n_valid"] == 8 and w2["max_topn"] == 8, w2
        assert w2["cached_topn"] == 5 and len(w2["ranking"]) == 8, w2
        # 超过可用排名：收敛到 8，不报错、不受缓存限制
        rc3 = s.recompute({"task_id": tid, "precip_topn": 999,
                           "percentile": 75})
        w3 = rc3["precip"]["windows"]["24h_2020"]
        assert rc3["precip"]["recompute"]["topn"] == 8 and w3["n_valid"] == 8
    finally:
        SearchSession._read_ranked_fields = orig_read
    s.close()
    print("api self-check OK ->", tmp)


if __name__ == "__main__":
    _self_check()