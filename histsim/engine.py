# -*- coding: utf-8 -*-
"""统一检索流水线（方案 6.1 的 7 步实现，参数驱动，两项需求只是两组取值）。

    数据访问 → 特征处理(由近及远+权重+标准化) → 相似度 → 排名(排除窗)
    → 组合(交集∪直取, 支持部分交集) → 降水统计(GMCP 窗口累加) → 概率产品

每一步都有独立的进度回调，供界面「执行进程」面板实时刷新。
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import viz
from .config import ELEMENTS, SearchConfig, var_key, var_label
from .datasource import GMCP_RE, Store, window_offsets
from .formula import compile_formula
from .readers import Grid, DEFAULT_IO_WORKERS, era5_axes, gmcp_axes, \
    nearest_slice, read_era5_batch, read_gmcp_batch

Progress = Callable[[str, str, int, int, str], None]


class Cancelled(RuntimeError):
    """用户取消检索。"""


def _noop(*_a, **_kw) -> None:
    pass


# ── 流水线主体 ────────────────────────────────────────────────────────
def run_search(cfg: SearchConfig, store: Store, progress: Progress = _noop,
               should_stop: Optional[Callable[[], bool]] = None,
               out_dir: Optional[str] = None) -> Dict:
    t_start = time.time()
    timings: Dict[str, float] = {}

    def tick(key: str, t0: float) -> None:
        timings[key] = round(time.time() - t0, 2)

    def stop_check() -> None:
        if should_stop and should_stop():
            raise Cancelled("检索已取消")

    errs = cfg.validate()
    if errs:
        raise ValueError("配置校验失败: " + "; ".join(errs))
    if not cfg.target_date:
        raise ValueError("未指定目标日期")
    target = datetime.strptime(cfg.target_date, "%Y-%m-%d").date()

    # ── 步骤 0：样本集与网格 ─────────────────────────────────────────
    t0 = time.time()
    progress("samples", "解析历史样本与区域", 0, 1, "扫描数据源覆盖")
    dates = store.available_dates(cfg)
    if not dates:
        raise ValueError("所选年份/月份范围内没有找到任何齐备的历史样本，"
                         "请检查数据源登记与配置")
    axes = _resolve_axes(cfg, store, dates[0])
    grid = Grid(axes["era5_lat"], axes["era5_lon"], cfg)
    gmcp_lat, gmcp_lon = axes["gmcp_lat"], axes["gmcp_lon"]
    gmcp_grid = Grid(gmcp_lat, gmcp_lon, cfg)
    i_pt = int(np.argmin(np.abs(gmcp_lat - cfg.point_lat)))
    j_pt = int(np.argmin(np.abs(gmcp_lon - cfg.point_lon)))
    tick("样本解析", t0)

    read_dates = sorted(set(dates) | {target})
    if target not in read_dates:  # pragma: no cover - 集合语义保证
        read_dates.append(target)
    target_i = read_dates.index(target)
    hist_idx = [i for i in range(len(read_dates)) if i != target_i]
    hist_dates = [read_dates[i] for i in hist_idx]

    # ── 步骤 1：数据访问（直读原始 NC）───────────────────────────────
    t0 = time.time()
    fields, read_errors = _read_fields(cfg, store, read_dates, axes, progress,
                                       should_stop)
    stop_check()
    tick("数据直读", t0)

    if read_errors:
        ok = {d for d, _ in _present_dates(fields, cfg, read_dates, store)}
        keep = [d for d in dates if d in ok]
        dates = keep
        hist_idx = [read_dates.index(d) for d in keep]
        hist_dates = keep

    n_hist = len(hist_dates)
    if n_hist == 0:
        raise ValueError("没有任何可用的历史样本（全部读取失败）")

    # ── 步骤 2：特征处理 ─────────────────────────────────────────────
    t0 = time.time()
    progress("features", "特征处理（由近及远 · 距离权重 · 标准化）", 0, 1, "")
    order = grid.order
    w_ord = grid.weight[order]
    feats = _build_features(cfg, fields, order, hist_idx, target_i)
    stop_check()
    tick("特征处理", t0)

    # ── 步骤 3–4：相似度与排名 ───────────────────────────────────────
    t0 = time.time()
    ranking = _similarity_and_rank(cfg, feats, hist_dates, target, w_ord,
                                   progress, should_stop)
    stop_check()
    tick("相似度与排名", t0)

    # ── 步骤 5：组合策略 ─────────────────────────────────────────────
    t0 = time.time()
    combine = _combine(cfg, ranking)
    tick("组合", t0)

    # ── 步骤 6：降水统计 ─────────────────────────────────────────────
    t0 = time.time()
    rain = _rain_stats(cfg, store, combine, gmcp_lat, gmcp_lon, i_pt, j_pt,
                       progress, should_stop)
    stop_check()
    tick("降水统计", t0)

    # ── 步骤 7：概率产品 ─────────────────────────────────────────────
    t0 = time.time()
    products = _products(cfg, rain)
    tick("概率产品", t0)

    # ── 出图 ─────────────────────────────────────────────────────────
    t0 = time.time()
    progress("figures", "生成图表", 0, 1, "")
    figures = {}
    if out_dir:
        figures = viz.render_all(cfg, grid, gmcp_lat, gmcp_lon, fields, read_dates,
                                 target_i, ranking, combine, products, out_dir,
                                 progress=progress)
    tick("出图", t0)

    result = {
        "config": cfg.to_dict(),
        "target_date": cfg.target_date,
        "grid": grid.desc(),
        "gmcp_grid": gmcp_grid.desc(),
        "samples": {
            "n_candidates": len(read_dates) - 1,
            "n_used": n_hist,
            "years": [min(d.year for d in hist_dates), max(d.year for d in hist_dates)],
            "read_errors": read_errors[:50],
            "n_read_errors": len(read_errors),
        },
        "times": cfg.times,
        "ranking": ranking,
        "combine": combine,
        "products": products,
        "figures": figures,
        "timings": timings,
        "elapsed_s": round(time.time() - t_start, 2),
    }
    progress("done", "完成", 1, 1, f"耗时 {result['elapsed_s']:.1f}s")
    return result


# ── 步骤 0 辅助 ───────────────────────────────────────────────────────
def _resolve_axes(cfg: SearchConfig, store: Store, sample_day: date) -> Dict:
    """确定 ERA5 区域切片与 GMCP 区域切片（只读坐标轴，代价极小）。"""
    lat_lo = cfg.center_lat - cfg.half_span_deg
    lat_hi = cfg.center_lat + cfg.half_span_deg
    lon_lo = cfg.center_lon - cfg.half_span_deg
    lon_hi = cfg.center_lon + cfg.half_span_deg

    probe_path = next(filter(None, (store.find_era5(e, sample_day)
                                    for e in cfg.elements())), None)
    if probe_path is None:
        raise ValueError("无法定位任何 ERA5 文件，请先登记数据源")
    lat_full, lon_full = era5_axes(probe_path)
    lat_sl = nearest_slice(lat_full, lat_lo, lat_hi)
    lon_sl = nearest_slice(lon_full, lon_lo, lon_hi)

    gmcp_file = _first_gmcp_file(store)
    if gmcp_file is None:
        raise ValueError("无法定位任何 GMCP 降水文件，请先登记数据源")
    glat, glon = gmcp_axes(gmcp_file)
    gli = nearest_slice(glat, lat_lo, lat_hi)
    glo = nearest_slice(glon, lon_lo, lon_hi)

    return {
        "era5_lat": lat_full[lat_sl], "era5_lon": lon_full[lon_sl],
        "era5_lat_sl": lat_sl, "era5_lon_sl": lon_sl,
        "gmcp_lat": glat[gli], "gmcp_lon": glon[glo],
    }


def _first_gmcp_file(store: Store) -> Optional[str]:
    """取一个 GMCP 文件用于读取全球网格坐标（网格全球统一，读哪个都一样）。"""
    for root in store.gmcp_roots():
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            yp = os.path.join(root, name)
            if len(name) == 4 and name.isdigit() and os.path.isdir(yp):
                for n in sorted(os.listdir(yp)):
                    if GMCP_RE.match(n):
                        return os.path.join(yp, n)
    return None


# ── 步骤 1 ────────────────────────────────────────────────────────────
def _count_cache_hits(jobs: List[tuple], cache_dir: str) -> int:
    """统计本次读取中已命中磁盘缓存的切片数（用于进度提示，非必需）。"""
    from .readers import _era5_cache_path, _slab_key
    n = 0
    for path, var, hours, levels, lat_sl, lon_sl in jobs:
        cpath = _era5_cache_path(cache_dir, path)
        if not cpath or not os.path.isfile(cpath):
            continue
        key = _slab_key(var, hours, levels, lat_sl, lon_sl)
        try:
            with np.load(cpath) as z:
                if key in z.files:
                    n += 1
        except (OSError, ValueError):
            continue
    return n


def _read_fields(cfg: SearchConfig, store: Store, read_dates: List[date],
                 axes: Dict, progress: Progress,
                 should_stop: Optional[Callable[[], bool]]):
    """并行直读各要素的「样本 × 时次 × 层次 × 区域」切片。"""
    jobs, meta = [], []
    for element in cfg.elements():
        var = ELEMENTS[element][1]
        levels = cfg.levels_of(element)
        for d in read_dates:
            p = store.find_era5(element, d)
            if p is None:
                continue
            jobs.append((p, var, list(cfg.times), levels,
                         axes["era5_lat_sl"], axes["era5_lon_sl"]))
            meta.append((element, d, tuple(levels)))

    total = len(jobs)
    done = {"n": 0}
    hits = _count_cache_hits(jobs, store.cache_dir)

    def on_done(k: int) -> None:
        done["n"] += k
        progress("read", f"直读原始 NC 切片（IO 池 {DEFAULT_IO_WORKERS} 进程）",
                 done["n"], total,
                 f"{done['n']}/{total} 文件 · 缓存命中 {hits}"
                 if hits else f"{done['n']}/{total} 文件")

    by_path = {}
    progress("read", "直读原始 NC 切片", 0, total,
             f"共 {total} 个文件，缓存命中 {hits}，需实读 {total - hits}")
    results, errors = read_era5_batch(jobs, workers=DEFAULT_IO_WORKERS,
                                     on_done=on_done, should_stop=should_stop,
                                     cache_dir=store.cache_dir)
    for path, arr in results:
        by_path[path] = arr

    fields: Dict[str, np.ndarray] = {}
    missing: List[Tuple[str, str]] = []
    for i, (element, d, levels) in enumerate(meta):
        path = jobs[i][0]
        arr = by_path.get(path)
        if arr is None:
            missing.append((element, d.isoformat()))
            continue
        nl = len(levels)
        arr = arr.reshape(len(cfg.times), nl, -1)
        slot = fields.get(element)
        if slot is None:
            slot = np.full((len(read_dates), len(cfg.times), nl, arr.shape[-1]),
                           np.nan, dtype=np.float32)
            fields[element] = slot
        slot[read_dates.index(d)] = arr
    return fields, missing


def _present_dates(fields: Dict[str, np.ndarray], cfg: SearchConfig,
                   read_dates: List[date], store: Store):
    """筛出所有要素都成功读到的日期。"""
    out = []
    for i, d in enumerate(read_dates):
        ok = True
        for element in cfg.elements():
            slot = fields.get(element)
            if slot is None or not np.isfinite(slot[i]).any():
                ok = False
                break
        if ok:
            out.append((d, i))
    return out


# ── 步骤 2 ────────────────────────────────────────────────────────────
def _build_features(cfg: SearchConfig, fields: Dict[str, np.ndarray],
                    order: np.ndarray, hist_idx: List[int], target_i: int):
    """按「由近及远」重排，并按配置做逐要素特征处理。

    返回 ``{(element, level, time): {"x": 目标向量, "H": 历史矩阵}}``，
    其中向量/矩阵均已按由近及远排序。
    """
    feats = {}
    for element, slot in fields.items():
        levels = cfg.levels_of(element)
        for li, level in enumerate(levels):
            for ti, t in enumerate(cfg.times):
                H = slot[hist_idx, ti, li, :][:, order]
                x = slot[target_i, ti, li, :][order]
                if not np.isfinite(H).all() or not np.isfinite(x).all():
                    H = np.nan_to_num(H, nan=0.0)
                    x = np.nan_to_num(x, nan=0.0)
                # 量纲治理：不处理时量级大的要素（如 rh%）会主导相似度，
                # 标准化/归一化把各要素拉到同一量纲。
                if cfg.feature_process == "standardize":
                    sigma = float(np.std(H))
                    if sigma > 1e-9:
                        H, x = H / sigma, x / sigma
                elif cfg.feature_process == "normalize":
                    lo, hi = float(np.min(H)), float(np.max(H))
                    if hi - lo > 1e-9:
                        H = (H - lo) / (hi - lo)
                        x = (x - lo) / (hi - lo)
                feats[(element, float(level), int(t))] = {"x": x, "H": H}
    return feats


# ── 相似度度量 ────────────────────────────────────────────────────────
def _cosine_dist(X: np.ndarray, x: np.ndarray) -> np.ndarray:
    """余弦距离 ``1 - cos``；零范数样本记 inf（等价于不可比）。"""
    nx = float(np.linalg.norm(x))
    nX = np.linalg.norm(X, axis=1)
    den = nX * nx
    out = np.full(X.shape[0], np.inf)
    ok = den > 1e-12
    if ok.any():
        out[ok] = 1.0 - (X[ok] @ x) / den[ok]
    return out


def _corr_dist(X: np.ndarray, x: np.ndarray) -> np.ndarray:
    """相关系数距离 ``1 - r``（在格点轴上各自去均值）；零方差样本记 inf。"""
    Xc = X - X.mean(axis=1, keepdims=True)
    xc = x - x.mean()
    den = np.linalg.norm(Xc, axis=1) * float(np.linalg.norm(xc))
    out = np.full(X.shape[0], np.inf)
    ok = den > 1e-12
    if ok.any():
        out[ok] = 1.0 - (Xc[ok] @ xc) / den[ok]
    return out


def _sim_scores(cfg: SearchConfig, X: np.ndarray, x: np.ndarray,
                w_ord: np.ndarray, formula_fn=None) -> np.ndarray:
    """按配置计算每个历史样本的相似度指数（越小越相似）。"""
    diff = X - x
    if cfg.similarity == "weighted_sq":
        return (diff * diff) @ w_ord
    if cfg.similarity == "euclidean":
        return np.sqrt((diff * diff).sum(axis=1))
    if cfg.similarity == "cosine":
        return _cosine_dist(X, x)
    if cfg.similarity == "correlation":
        return _corr_dist(X, x)
    if cfg.similarity == "custom":
        return formula_fn(diff, w_ord)
    return np.abs(diff) @ w_ord   # weighted_abs（默认）


# ── 步骤 3–4 ──────────────────────────────────────────────────────────
def _similarity_and_rank(cfg: SearchConfig, feats: Dict, hist_dates: List[date],
                         target: date, w_ord: np.ndarray, progress: Progress,
                         should_stop: Optional[Callable[[], bool]]) -> Dict:
    """逐要素计算相似度指数并排名（剔除目标日 ±N 天）。"""
    excl_mask = np.array([abs((d - target).days) <= cfg.exclude_days
                          for d in hist_dates], dtype=bool)
    # 自定义公式整轮只编译一次（编译含 AST 校验与形状探针）
    formula_fn = (compile_formula(cfg.similarity_formula)
                  if cfg.similarity == "custom" else None)

    total = len(cfg.variables) * len(cfg.times)
    done = 0
    ranking: Dict[str, Dict] = {}

    for element, level in cfg.variables:
        level = float(level)
        key = var_key(element, level)
        per_time = {}
        for t in cfg.times:
            if should_stop and should_stop():
                raise Cancelled("检索已取消")
            f = feats[(element, level, int(t))]
            X, x = f["H"], f["x"]
            si = _sim_scores(cfg, X, x, w_ord, formula_fn)
            si = np.where(excl_mask, np.inf, si)
            order_idx = np.argsort(si, kind="stable")
            rows = []
            for rank, i in enumerate(order_idx, 1):
                if not np.isfinite(si[i]):
                    break
                rows.append({"rank": rank, "date": hist_dates[i].isoformat(),
                             "si": float(si[i])})
            per_time[str(t)] = rows
            done += 1
            progress("similarity", "计算相似度并排名", done, total,
                     f"{var_label(key)} · {t:02d}UTC")
        ranking[key] = per_time
    return ranking


# ── 步骤 5 ────────────────────────────────────────────────────────────
def _inter_diagnose(cfg: SearchConfig, ranking: Dict, ts: str) -> Dict:
    """交集可行性诊断：候选池规模、取得出交集所需的最小 TopN、以及 TopN→交集数。

    严格交集在候选池远大于 TopN 时通常为空，这属于口径而非缺陷；这里把
    「TopN 要调到多大才有交集」直接算出来，供界面提示与 TopN 取值。
    """
    ranks: Dict[str, set] = {}
    n_cand = 0
    for key in cfg.inter_group:
        rows = ranking.get(key, {}).get(ts, [])
        n_cand = max(n_cand, len(rows))
        for row in rows:
            ranks.setdefault(row["date"], set()).add(row["rank"])

    need = len(cfg.inter_group)
    feasible = {d: max(rs) for d, rs in ranks.items() if len(rs) == need}
    if not feasible:
        return {"n_candidates": n_cand, "min_feasible_topn": None, "curve": []}

    min_topn = min(feasible.values())
    probes = {min_topn, cfg.inter_topn}
    probes.update(int(round(min_topn * f)) for f in (2, 5, 10))
    curve = [{"topn": p, "n_inter": sum(1 for r in feasible.values() if r <= p)}
             for p in sorted(probes) if 1 <= p <= n_cand]
    return {"n_candidates": n_cand, "min_feasible_topn": min_topn, "curve": curve}


def _combine(cfg: SearchConfig, ranking: Dict) -> Dict:
    """交集组 ∩ + 直取组 ∪；支持部分交集（至少 k 个要素命中）。

    严格交集为空、但确实存在「各要素都命中」的日期时，自动把交集 TopN 放宽到
    最小可行值：TopN 本就是可调系统参数，而产品只要有 1 个相似时刻就能出图
    （其相邻时次的降水窗口照常逐小时累加）。
    """
    out: Dict[str, Dict] = {}
    for t in cfg.times:
        ts = str(t)
        # 每个候选日期在交集组各要素中的位次（该要素未收录此日期则不计）
        per_date: Dict[str, Dict[str, int]] = {}
        for key in cfg.inter_group:
            for row in ranking.get(key, {}).get(ts, []):
                per_date.setdefault(row["date"], {})[key] = row["rank"]

        need = (cfg.partial_min_hits if cfg.partial_intersection
                else len(cfg.inter_group))
        topn = cfg.inter_topn
        auto = False
        if cfg.inter_group and not cfg.partial_intersection:
            # 各要素都命中的日期里「位次最差者」决定最小可行 TopN
            feasible = [max(rk.values()) for rk in per_date.values()
                        if len(rk) == len(cfg.inter_group)]
            if feasible and min(feasible) > topn:
                topn, auto = min(feasible), True

        inter_cnt: Dict[str, int] = {d: sum(1 for r in rk.values() if r <= topn)
                                     for d, rk in per_date.items()}
        if cfg.partial_intersection:
            inter = {d for d, c in inter_cnt.items() if c >= need}
        else:
            inter = {d for d, c in inter_cnt.items() if c == need} if need else set()

        direct: Dict[str, List[str]] = {}
        for key in cfg.direct_group:
            for row in ranking.get(key, {}).get(ts, [])[:cfg.direct_topn]:
                direct.setdefault(row["date"], []).append(key)

        final = sorted(inter | set(direct), key=lambda d: (not d in inter, d))
        source = {}
        for d in final:
            tags = []
            if d in inter:
                tags.append(f"{inter_cnt.get(d, 0)}/{len(cfg.inter_group)} 要素交集"
                            if cfg.partial_intersection else
                            f"{len(cfg.inter_group)} 要素交集")
            if d in direct:
                tags.append("直取: " + ",".join(sorted(direct[d])))
            source[d] = " + ".join(tags)

        out[ts] = {
            "inter": sorted(inter),
            "direct": sorted(direct),
            "final": final,
            "n_inter": len(inter),
            "n_direct": len(direct),
            "n_final": len(final),
            "hits": {d: inter_cnt.get(d, 0) for d in final},
            "source": source,
            "inter_topn_used": topn,
            "inter_topn_auto": auto,
            "diagnose": _inter_diagnose(cfg, ranking, ts),
        }
    return out


# ── 步骤 6 ────────────────────────────────────────────────────────────
def _rain_stats(cfg: SearchConfig, store: Store, combine: Dict,
                gmcp_lat: np.ndarray, gmcp_lon: np.ndarray,
                i_pt: int, j_pt: int, progress: Progress,
                should_stop: Optional[Callable[[], bool]]):
    """对相似时刻集合按窗口累加 GMCP 逐小时降水。"""
    lat_lo, lat_hi = float(gmcp_lat.min()), float(gmcp_lat.max())
    lon_lo, lon_hi = float(gmcp_lon.min()), float(gmcp_lon.max())

    # 汇总所有需要的时刻（跨时次/跨样本去重，窗口重叠只读一次）
    need: Dict[str, datetime] = {}
    plan: Dict[str, List[Tuple[str, List[datetime]]]] = {str(t): [] for t in cfg.times}
    merge_24h: Dict[str, bool] = {}
    for t in cfg.times:
        ts = str(t)
        for ds in combine.get(ts, {}).get("final", []):
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            wins = window_offsets(d, [h + t for h in cfg.rain_window])
            plan[ts].append((ds, wins))
            merge_24h[ds] = True
            for w in wins:
                need[w.strftime("%Y%m%d%H")] = w

    if cfg.enable_24h:
        plan["24h"] = []
        for ds in merge_24h:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            wins = window_offsets(d, cfg.rain_window_24h)
            plan["24h"].append((ds, wins))
            for w in wins:
                need[w.strftime("%Y%m%d%H")] = w

    found = {}
    for k, w in need.items():
        p = store.find_gmcp(w)
        if p:
            found[k] = p

    total = len(found)
    done = {"n": 0}
    progress("rain", "直读 GMCP 逐小时降水", 0, total, f"共 {len(need)} 个时刻")

    def on_done(k: int) -> None:
        done["n"] += k
        progress("rain", "直读 GMCP 逐小时降水", done["n"], total,
                 f"{done['n']}/{total} 小时文件")

    jobs = [(p, lat_lo, lat_hi, lon_lo, lon_hi) for p in found.values()]
    cache = read_gmcp_batch(jobs, workers=DEFAULT_IO_WORKERS, on_done=on_done,
                            should_stop=should_stop)

    def field_of(w: datetime) -> Optional[np.ndarray]:
        p = found.get(w.strftime("%Y%m%d%H"))
        return cache.get(p) if p else None

    shape = (gmcp_lat.size, gmcp_lon.size)
    per_time: Dict[str, Dict] = {}
    for ts, items in plan.items():
        dates_out, point_vals, field_vals, missing = [], [], [], []
        for ds, wins in items:
            acc = np.zeros(shape, dtype=np.float32)
            ok = False
            for w in wins:
                f = field_of(w)
                if f is None:
                    continue
                acc += f
                ok = True
            if not ok:
                missing.append(ds)
                continue
            dates_out.append(ds)
            point_vals.append(float(acc[i_pt, j_pt]))
            field_vals.append(acc)
        per_time[ts] = {
            "dates": dates_out,
            "point": point_vals,
            "fields": np.stack(field_vals) if field_vals else None,
            "missing": missing,
        }

    return {
        "per_time": per_time,
        "n_hours_read": len(cache),
        "n_hours_missing": total - len(cache),
        "n_moments": len(need),
    }


# ── 步骤 7 ────────────────────────────────────────────────────────────
def _products(cfg: SearchConfig, rain: Dict) -> Dict:
    """统计各阈值降水概率、平均值，以及空间概率场。"""
    ths = sorted(set(float(x) for x in cfg.rain_thresholds))
    out: Dict[str, Dict] = {}
    for ts, r in rain["per_time"].items():
        vals = np.asarray(r["point"], dtype=np.float64)
        n = vals.size
        stats = {}
        for th in ths:
            stats[str(th)] = {
                "prob": round(float((vals >= th).mean()), 4) if n else 0.0,
                "count": int((vals >= th).sum()),
                "n": int(n),
            }
        fields = r["fields"]
        spatial = {}
        if fields is not None and fields.size:
            mean_f = fields.mean(axis=0)
            spatial["mean"] = mean_f
            for th in ths:
                spatial[f"prob_{th}"] = (fields >= th).mean(axis=0)
        out[ts] = {
            "n_valid": int(n),
            "n_missing": len(r["missing"]),
            "missing": r["missing"][:20],
            "mean_mm": round(float(vals.mean()), 2) if n else 0.0,
            "max_mm": round(float(vals.max()), 2) if n else 0.0,
            "stats": stats,
            "samples": [{"date": d, "mm": round(float(v), 2)}
                        for d, v in sorted(zip(r["dates"], vals),
                                           key=lambda p: -p[1])],
            "spatial": spatial,
        }
    return {"by_time": out, "thresholds": ths,
            "point": [cfg.point_lat, cfg.point_lon]}


def _self_check() -> None:
    """离线自检：仅验证配置→流水线的算术部分（不读真实数据）。"""
    cfg = SearchConfig()
    cfg.times = [0]
    cfg.variables = [["u", 925]]
    cfg.inter_group = ["u925"]
    cfg.direct_group = []
    hist_dates = [date(2001, 4, 1) + timedelta(days=i) for i in range(20)]
    # 构造 feats：历史第 5 个样本 = 目标
    H = np.arange(20 * 9, dtype=np.float32).reshape(20, 9)
    x = H[5] + 0.01
    feats = {("u", 925.0, 0): {"x": x, "H": H}}
    w = np.ones(9)
    ranking = _similarity_and_rank(cfg, feats, hist_dates, date(2001, 4, 21), w,
                                   _noop, None)
    rows = ranking["u925"]["0"]
    assert rows[0]["date"] == hist_dates[5].isoformat(), rows[0]
    assert rows[0]["si"] < rows[1]["si"]
    comb = _combine(cfg, ranking)
    best = hist_dates[5].isoformat()
    assert best in comb["0"]["final"]
    assert comb["0"]["source"][best] == "1 要素交集"
    # 收紧 TopN 后，只有真正排进前 3 的样本才进交集（final 按日期排序）
    tight = _combine(cfg.copy(inter_topn=3), ranking)
    assert tight["0"]["final"] == sorted(r["date"] for r in rows[:3]), \
        tight["0"]["final"]
    assert tight["0"]["inter_topn_used"] == 3 and not tight["0"]["inter_topn_auto"]
    # 严格交集为空时自动放宽 TopN：v925 反向排序后与 u925 无共同前三名
    feats2 = dict(feats)
    H2 = H[::-1].copy()
    feats2[("v", 925.0, 0)] = {"x": H2[15] + 0.01, "H": H2}
    cfg2 = cfg.copy(variables=[["u", 925], ["v", 925]],
                    inter_group=["u925", "v925"], inter_topn=1)
    comb2 = _combine(cfg2, _similarity_and_rank(cfg2, feats2, hist_dates,
                                                date(2001, 4, 21), w, _noop, None))
    assert comb2["0"]["inter_topn_auto"] and comb2["0"]["inter_topn_used"] > 1, \
        comb2["0"]
    # 放宽后必须真的取到样本——产品只要有 1 个相似时刻就能出图
    assert comb2["0"]["n_inter"] >= 1, comb2["0"]
    # 诊断：单要素交集的最小可行 TopN 必为 1；候选池 = 20 个样本剔除 ±3 天窗
    dg = tight["0"]["diagnose"]
    assert dg["n_candidates"] == 17 and dg["min_feasible_topn"] == 1, dg
    assert dg["curve"][0] == {"topn": 1, "n_inter": 1}, dg
    # 排除窗应剔除目标日附近
    r2 = _similarity_and_rank(cfg, feats, [date(2001, 4, 20)] * 20,
                              date(2001, 4, 21), w, _noop, None)
    assert r2["u925"]["0"] == [], r2["u925"]["0"][:2]
    # ── V2.0.0：相似度度量分派 ──────────────────────────────────────
    for sim in ("weighted_abs", "weighted_sq", "euclidean", "cosine", "correlation"):
        si = _sim_scores(cfg.copy(similarity=sim), H, x, w)
        assert si.shape == (20,) and np.isfinite(si).all(), sim
    # 等权时「加权绝对差」与「欧氏距离」对单格点扰动同序（最小仍在第 5 个样本）
    assert int(np.argmin(_sim_scores(cfg.copy(similarity="euclidean"), H, x, w))) == 5
    # 自定义公式须与等价内建口径一致
    assert np.allclose(_sim_scores(cfg.copy(similarity="custom"), H, x, w,
                                   compile_formula("sqrt(sum(w*d*d))")),
                       _sim_scores(cfg.copy(similarity="euclidean"), H, x, w))
    assert np.allclose(_sim_scores(cfg.copy(similarity="custom"), H, x, w,
                                   compile_formula("sum(w*abs(d))")),
                       _sim_scores(cfg.copy(similarity="weighted_abs"), H, x, w))
    # 零范数样本在余弦/相关下记为 inf，不参与排名
    z = np.zeros((1, 9))
    assert np.isinf(_sim_scores(cfg.copy(similarity="cosine"), z, x, w)[0])
    assert np.isinf(_sim_scores(cfg.copy(similarity="correlation"), z, x, w)[0])
    # ── V2.0.0：特征处理三选一 ──────────────────────────────────────
    fields_demo = {"u": H.reshape(20, 1, 1, 9)}
    order = np.arange(9)
    hist_idx = list(range(20))
    f_none = _build_features(cfg, fields_demo, order, hist_idx, 5)
    assert np.allclose(f_none[("u", 925.0, 0)]["H"], H)
    f_std = _build_features(cfg.copy(feature_process="standardize"),
                            fields_demo, order, hist_idx, 5)
    assert abs(float(np.std(f_std[("u", 925.0, 0)]["H"])) - 1.0) < 1e-6
    f_nrm = _build_features(cfg.copy(feature_process="normalize"),
                            fields_demo, order, hist_idx, 5)
    Hn = f_nrm[("u", 925.0, 0)]["H"]
    assert abs(float(np.min(Hn))) < 1e-9 and abs(float(np.max(Hn)) - 1.0) < 1e-6
    print("engine self-check OK")


if __name__ == "__main__":
    _self_check()