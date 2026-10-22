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

import json
import os
import shutil
import threading
import time
import traceback
import uuid
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional

from . import config as cfgmod
from .config import ELEMENTS, SearchConfig, builtin_template
from .datasource import Store, discover_and_register
from .engine import Cancelled, run_search
from .precip import DEFAULT_WINDOWS, GROUPS, PRECIP_PRODUCTS, PreprocessCancelled, \
    run_preprocess, scan_source

# 界面默认展示的排名条数（后端仍按 TopN 全量计算）
RANK_VIEW_LIMIT = 80

# 结果落盘文件名：检索很贵（冷盘分钟级），进程重启后要还能回看
RESULT_FILE = "result.json"


def _save_result(out_dir: str, view: Dict) -> None:
    with open(os.path.join(out_dir, RESULT_FILE), "w", encoding="utf-8") as fh:
        json.dump(view, fh, ensure_ascii=False, default=str)


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
        w = {k: v for k, v in win.items() if k != "products"}
        prod = dict(win.get("products") or {})
        prod.pop("spatial", None)
        w["products"] = prod
        w["spatial_ready"] = bool((win.get("products") or {}).get("spatial"))
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
    s.close()
    print("api self-check OK ->", tmp)


if __name__ == "__main__":
    _self_check()