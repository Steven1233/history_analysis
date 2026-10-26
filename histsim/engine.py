# -*- coding: utf-8 -*-
"""统一检索流水线（方案 6.1 的 7 步实现，参数驱动，两项需求只是两组取值）。

    数据访问 → 特征处理(由近及远+权重+标准化) → 相似度 → 排名(排除窗)
    → 组合(交集∪直取, 支持部分交集) → 降水统计(GMCP 窗口累加) → 概率产品

每一步都有独立的进度回调，供界面「执行进程」面板实时刷新。
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from . import viz
from .config import ELEMENTS, SearchConfig, var_key, var_label
from .datasource import GMCP_RE, Store, window_offsets
from .formula import compile_formula
from .precip import DEFAULT_PERCENTILE, PRECIP_PRODUCTS, PRECIP_QUANTILES, product_path
from .readers import Grid, DEFAULT_IO_WORKERS, era5_axes, gmcp_axes, \
    nearest_slice, precip_axes, read_era5_batch, read_gmcp_batch, read_precip_batch

Progress = Callable[[str, str, int, int, str], None]

# ── V2.2.0：累计降水产品后处理 ────────────────────────────────────────
# 固定分位数面板（P50 即中位数）与自定义百分位初值由 precip.py 统一提供。
# 检索时保留全部相似日排名；前 N 名的降水场写入缓存加速重算，超出缓存的名次
# 在重算时按排名重新读取产品文件，因此 TopN 不再受缓存大小限制。
PRECIP_FIELD_CACHE = 300
# 累计降水产品直读的进程数：实测 8 进程较单进程快一个量级（HDF5 打开占主导）。
PRECIP_IO_WORKERS = max(DEFAULT_IO_WORKERS, min(8, os.cpu_count() or 4))
try:
    PRECIP_IO_WORKERS = max(1, int(os.environ.get("HISTSIM_PRECIP_IO_WORKERS",
                                                  PRECIP_IO_WORKERS)))
except ValueError:
    pass


def _score_workers(n_windows: int) -> int:
    """窗口级并行度：numpy 计算释放 GIL，线程池即可；限制并发以控内存峰值。"""
    try:
        env = int(os.environ.get("HISTSIM_PRECIP_WORKERS", ""))
    except ValueError:
        env = 0
    cpu = os.cpu_count() or 2
    return max(1, min(n_windows, env or 4, max(1, cpu)))


def _chunk_workers() -> int:
    """单窗口内按候选样本分块的并行度（GMCP 单要素检索时启用）。"""
    try:
        env = int(os.environ.get("HISTSIM_PRECIP_CHUNKS", ""))
    except ValueError:
        env = 0
    return max(1, min(env or 4, max(1, os.cpu_count() or 2)))


def precip_products(top: np.ndarray, target_field: Optional[np.ndarray],
                    i_pt: int, j_pt: int, thresholds: List[float],
                    percentile: float = DEFAULT_PERCENTILE) -> Tuple[Dict, Dict]:
    """由相似日累计降水场统计产品（V2.2.0：目标日、分位数、自定义百分位）。

    ``top`` 形状 ``(n, nlat, nlon)``，按相似度升序；``target_field`` 为目标日
    同一窗口的累计降水场（用于并排对比）。返回 ``(jsonable, spatial)``：
    jsonable 为可直接落盘的统计量，spatial 为二维场（不进入 JSON）。
    """
    top = np.asarray(top, dtype=np.float32)
    n = int(top.shape[0])
    vals = top[:, i_pt, j_pt].astype(np.float64) if n else np.zeros(0)
    mean_f = top.mean(axis=0) if n else np.zeros(top.shape[1:], dtype=np.float32)
    median_f = np.median(top, axis=0) if n else np.zeros_like(mean_f)
    spatial: Dict[str, np.ndarray] = {"mean": mean_f, "median": median_f}
    quantiles: Dict[str, float] = {}
    for q in PRECIP_QUANTILES:
        f = np.percentile(top, q, axis=0) if n else np.zeros_like(mean_f)
        spatial[f"q{q}"] = np.asarray(f, dtype=np.float32)
        quantiles[str(q)] = round(float(np.percentile(vals, q)), 2) if n else 0.0
    pc = float(percentile)
    custom_f = np.percentile(top, pc, axis=0) if n else np.zeros_like(mean_f)
    spatial["custom"] = np.asarray(custom_f, dtype=np.float32)
    if target_field is not None:
        spatial["target"] = np.asarray(target_field, dtype=np.float32)

    ths = sorted({float(t) for t in thresholds})
    stats: Dict[str, Dict] = {}
    for th in ths:
        spatial[f"prob_{th}"] = (top >= th).mean(axis=0) if n else np.zeros_like(mean_f)
        stats[str(th)] = {
            "prob": round(float((vals >= th).mean()), 4) if n else 0.0,
            "count": int((vals >= th).sum()),
            "n": int(vals.size),
        }
    target_mm = (round(float(np.asarray(target_field)[i_pt, j_pt]), 2)
                 if target_field is not None else None)
    out = {
        "n_valid": n,
        "mean_mm": round(float(vals.mean()), 2) if n else 0.0,
        "median_mm": round(float(np.median(vals)), 2) if n else 0.0,
        "max_mm": round(float(vals.max()), 2) if n else 0.0,
        "target_mm": target_mm,
        "percentile": pc,
        "pctl_mm": round(float(np.percentile(vals, pc)), 2) if n else 0.0,
        "quantiles": quantiles,
        "stats": stats,
        "samples": [],
    }
    return out, spatial


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

    errs = cfg.normalize().validate()
    if errs:
        raise ValueError("配置校验失败: " + "; ".join(errs))
    if not cfg.target_date:
        raise ValueError("未指定目标日期")
    target = datetime.strptime(cfg.target_date, "%Y-%m-%d").date()

    has_era5 = bool(cfg.variables)
    has_precip = cfg.precip_active
    if not has_era5 and not has_precip:
        raise ValueError("至少需要选择 ERA5 检索要素或 GMCP 累计降水目标变量")

    grid = None
    fields: Dict[str, np.ndarray] = {}
    read_dates: List[date] = []
    hist_dates: List[date] = []
    hist_idx: List[int] = []
    target_i = 0
    n_hist = 0
    ranking: Dict[str, Dict] = {}
    combine: Dict[str, Dict] = {}
    gmcp_grid_desc: Dict = {}
    samples: Dict = {"n_candidates": 0, "n_used": 0, "years": [0, 0],
                     "read_errors": [], "n_read_errors": 0}

    # ── 步骤 0–5：ERA5 要素检索（可选）──────────────────────────────
    if has_era5:
        t0 = time.time()
        progress("samples", "解析历史样本与区域", 0, 1, "扫描数据源覆盖")
        dates = store.available_dates(cfg)
        if not dates:
            raise ValueError("所选年份/月份范围内没有找到任何齐备的 ERA5 历史样本，"
                             "请检查数据源登记与配置")
        axes = _resolve_axes(cfg, store, dates[0])
        grid = Grid(axes["era5_lat"], axes["era5_lon"], cfg)
        gmcp_grid_desc = Grid(axes["gmcp_lat"], axes["gmcp_lon"], cfg).desc()
        tick("样本解析", t0)

        read_dates = sorted(set(dates) | {target})
        target_i = read_dates.index(target)
        hist_idx = [i for i in range(len(read_dates)) if i != target_i]
        hist_dates = [read_dates[i] for i in hist_idx]

        # 步骤 1：数据访问（直读原始 NC）
        t0 = time.time()
        fields, read_errors = _read_fields(cfg, store, read_dates, axes, progress,
                                           should_stop)
        stop_check()
        tick("数据直读", t0)
        if read_errors:
            ok = {d for d, _ in _present_dates(fields, cfg, read_dates, store)}
            dates = [d for d in dates if d in ok]
            hist_idx = [read_dates.index(d) for d in dates]
            hist_dates = dates
        n_hist = len(hist_dates)
        if n_hist == 0:
            raise ValueError("没有任何可用的 ERA5 历史样本（全部读取失败）")

        # 步骤 2：特征处理
        t0 = time.time()
        progress("features", "特征处理（由近及远 · 距离权重 · 标准化）", 0, 1, "")
        order = grid.order
        w_ord = grid.weight[order]
        feats = _build_features(cfg, fields, order, hist_idx, target_i)
        stop_check()
        tick("特征处理", t0)

        # 步骤 3–4：相似度与排名
        t0 = time.time()
        ranking = _similarity_and_rank(cfg, feats, hist_dates, target, w_ord,
                                       progress, should_stop)
        stop_check()
        tick("相似度与排名", t0)

        # 步骤 5：组合策略
        t0 = time.time()
        combine = _combine(cfg, ranking)
        tick("组合", t0)

        samples = {
            "n_candidates": len(read_dates) - 1,
            "n_used": n_hist,
            "years": [min(d.year for d in hist_dates), max(d.year for d in hist_dates)],
            "read_errors": read_errors[:50],
            "n_read_errors": len(read_errors),
        }

    # ── 步骤 6：GMCP 累计降水目标检索（第一优先）─────────────────────
    precip: Dict = {"enabled": has_precip, "available": False, "windows": {}}
    precip_lat = precip_lon = None
    if has_precip:
        t0 = time.time()
        precip = _precip_search(cfg, store, target, progress, should_stop)
        # _lat/_lon 既供出图，也供结果页重算缓存落盘（不进入结果 JSON）
        precip_lat = precip.get("_lat")
        precip_lon = precip.get("_lon")
        stop_check()
        tick("累计降水检索", t0)
        if not has_era5 and precip.get("available"):
            all_similar = sorted({r["date"] for w in precip["windows"].values()
                                  for r in w.get("ranking", [])})
            years = sorted({int(d[:4]) for d in all_similar}) or [0]
            samples = {
                "n_candidates": int(precip.get("n_candidates") or 0),
                "n_used": len(all_similar),
                "years": [years[0], years[-1]],
                "read_errors": [],
                "n_read_errors": 0,
            }

    if not has_era5 and not precip.get("available"):
        raise ValueError("GMCP 累计降水目标检索不可用："
                         + "；".join(w.get("reason", "") for w in
                                    (precip.get("skipped") or [])[:3])
                         or "目标日期或历史年份缺少累计降水产品，请先完成数据预处理并登记")

    # ── 步骤 7：ERA5 路径的 GMCP 小时窗口降水统计 ────────────────────
    t0 = time.time()
    rain: Dict = {"per_time": {}, "n_hours_read": 0, "n_hours_missing": 0,
                  "n_moments": 0}
    if has_era5 and combine:
        glat, glon = _hourly_axes(store) or (axes["gmcp_lat"], axes["gmcp_lon"])
        i_pt = int(np.argmin(np.abs(glat - cfg.point_lat)))
        j_pt = int(np.argmin(np.abs(glon - cfg.point_lon)))
        rain = _rain_stats(cfg, store, combine, glat, glon, i_pt, j_pt,
                           progress, should_stop)
        stop_check()
    tick("降水统计", t0)

    # ── 步骤 8：概率产品 ─────────────────────────────────────────────
    t0 = time.time()
    products = _products(cfg, rain)
    tick("概率产品", t0)

    # ── 出图 ─────────────────────────────────────────────────────────
    t0 = time.time()
    progress("figures", "生成图表", 0, 1, "")
    figures: Dict[str, str] = {}
    if out_dir:
        if has_era5 and grid is not None:
            figures = viz.render_all(cfg, grid, axes["gmcp_lat"], axes["gmcp_lon"],
                                     fields, read_dates, target_i, ranking, combine,
                                     products, out_dir, progress=progress)
        if precip.get("available") and precip_lat is not None:
            figures.update(viz.render_precip_all(cfg, precip, precip_lat, precip_lon,
                                                 out_dir, progress=progress))
    tick("出图", t0)

    result = {
        "config": cfg.to_dict(),
        "target_date": cfg.target_date,
        "grid": grid.desc() if grid is not None else None,
        "gmcp_grid": precip.get("grid") or gmcp_grid_desc,
        "samples": samples,
        "times": cfg.times,
        "ranking": ranking,
        "combine": combine,
        "precip": precip,
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

    gmcp_file = _first_hourly_file(store)
    if gmcp_file is not None:
        glat, glon = gmcp_axes(gmcp_file)
    else:
        gp = _first_precip_file(store)
        if gp is not None:
            glat, glon = precip_axes(gp)
        else:  # 没有降水数据时仍用 ERA5 网格占位，降水统计自然为空
            glat, glon = lat_full, lon_full
    gli = nearest_slice(glat, lat_lo, lat_hi)
    glo = nearest_slice(glon, lon_lo, lon_hi)

    return {
        "era5_lat": lat_full[lat_sl], "era5_lon": lon_full[lon_sl],
        "era5_lat_sl": lat_sl, "era5_lon_sl": lon_sl,
        "gmcp_lat": glat[gli], "gmcp_lon": glon[glo],
    }


def _first_hourly_file(store: Store) -> Optional[str]:
    """取一个 GMCP 逐小时文件用于读取全球网格坐标（网格全球统一）。"""
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


def _hourly_axes(store: Store):
    p = _first_hourly_file(store)
    return gmcp_axes(p) if p else None


def _first_precip_file(store: Store, keys: Optional[List[str]] = None) -> Optional[str]:
    """取一个已登记的累计降水产品文件（用于读取网格坐标）。"""
    keys = keys or list(PRECIP_PRODUCTS)
    for key in keys:
        for root in store.precip_roots(key):
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                if PRECIP_PRODUCTS[key].match_filename(name) is not None:
                    return os.path.join(root, name)
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


def _sim_scores_chunked(cfg: SearchConfig, X: np.ndarray, x: np.ndarray,
                        w_ord: np.ndarray, formula_fn=None,
                        workers: int = 1) -> np.ndarray:
    """按候选样本行分块并行计算相似度（单窗口 GMCP 检索的并行点）。

    SciPy 类度量（余弦/相关）与自定义公式都按样本独立归约，分块后拼接与整块
    计算结果完全一致；行数较少（<512）时分块开销大于收益，直接串行。
    """
    n = int(X.shape[0])
    workers = max(1, min(int(workers or 1), n // 512 or 1))
    if workers <= 1 or n < 512:
        return _sim_scores(cfg, X, x, w_ord, formula_fn)
    idx = np.array_split(np.arange(n), workers)
    out = np.empty(n, dtype=np.float64)
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="histsim-score") as ex:
        futs = {ex.submit(_sim_scores, cfg, X[part], x, w_ord, formula_fn): part
                for part in idx if part.size}
        for fut in as_completed(futs):
            out[futs[fut]] = fut.result()
    return out


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


def _combine_single(cfg: SearchConfig, ranking: Dict, ts: str, key: str) -> Dict:
    """单要素模式：直接采用该要素的 TopN 排名，不做交集与自动放宽。"""
    topn = cfg.inter_topn if key in cfg.inter_group else cfg.direct_topn
    rows = list(ranking.get(key, {}).get(ts, []))[:max(1, topn)]
    final = [r["date"] for r in rows]
    as_inter = key in cfg.inter_group
    return {
        "inter": final if as_inter else [],
        "direct": final if not as_inter else [],
        "final": final,
        "n_inter": len(final) if as_inter else 0,
        "n_direct": len(final) if not as_inter else 0,
        "n_final": len(final),
        "hits": {d: 1 for d in final},
        "source": {d: f"单要素 TopN（{var_label(key)}，不做交集）" for d in final},
        "single_element": True,
        "inter_topn_used": max(1, topn),
        "inter_topn_auto": False,
        "diagnose": {"n_candidates": len(ranking.get(key, {}).get(ts, [])),
                     "min_feasible_topn": 1, "curve": []},
    }


def _combine(cfg: SearchConfig, ranking: Dict) -> Dict:
    """交集组 ∩ + 直取组 ∪；支持部分交集（至少 k 个要素命中）。

    只选择一个要素时不做交集：直接采用该要素的 TopN 榜单执行完整流程。
    严格交集为空、但确实存在「各要素都命中」的日期时，自动把交集 TopN 放宽到
    最小可行值：TopN 本就是可调系统参数，而产品只要有 1 个相似时刻就能出图
    （其相邻时次的降水窗口照常逐小时累加）。
    """
    out: Dict[str, Dict] = {}
    single_key = (cfg.participating_keys[0]
                  if len(cfg.participating_keys) == 1 else None)
    for t in cfg.times:
        ts = str(t)
        if single_key is not None:
            out[ts] = _combine_single(cfg, ranking, ts, single_key)
            continue
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


# ── 步骤 6：GMCP 累计降水目标检索 ─────────────────────────────────────
def _precip_search(cfg: SearchConfig, store: Store, target: date,
                   progress: Progress,
                   should_stop: Optional[Callable[[], bool]]) -> Dict:
    """以预处理的北京时间累计降水产品为目标变量做相似检索并统计产品。

    每个窗口（24h 两套、12h 两段、6h 四段）独立检索 TopN，再由相似日期的
    累计降水场给出平均场、中位数场与各阈值概率场。
    """
    keys = [k for k in cfg.precip_windows if k in PRECIP_PRODUCTS]
    progress("precip", "GMCP 累计降水目标检索", 0, max(1, len(keys)),
             "定位目标日产品")
    skipped: List[Dict] = []
    target_paths: Dict[str, str] = {}
    for key in keys:
        p = store.find_precip(key, target)
        if p:
            target_paths[key] = p
        else:
            skipped.append({"key": key, "reason": f"{PRECIP_PRODUCTS[key].label()} "
                                                   f"目标日产品缺失"})
    usable = list(target_paths)
    if not usable:
        return {"enabled": True, "available": False, "windows": {},
                "skipped": skipped, "reason": "目标日期缺少全部累计降水产品"}

    lat_lo = cfg.center_lat - cfg.half_span_deg
    lat_hi = cfg.center_lat + cfg.half_span_deg
    lon_lo = cfg.center_lon - cfg.half_span_deg
    lon_hi = cfg.center_lon + cfg.half_span_deg

    probe = target_paths[usable[0]]
    plat, plon = precip_axes(probe)
    lsl = nearest_slice(plat, lat_lo, lat_hi)
    osl = nearest_slice(plon, lon_lo, lon_hi)
    p_lat, p_lon = plat[lsl], plon[osl]
    p_grid = Grid(p_lat, p_lon, cfg)
    order = p_grid.order
    w_ord = p_grid.weight[order]
    shape = (p_lat.size, p_lon.size)
    i_pt = int(np.argmin(np.abs(p_lat - cfg.point_lat)))
    j_pt = int(np.argmin(np.abs(p_lon - cfg.point_lon)))

    dates = store.available_precip_dates(cfg, usable)
    read_dates = sorted(set(dates) | {target})
    target_i = read_dates.index(target)

    jobs, path_meta = [], {}
    for key in usable:
        for d in read_dates:
            p = store.find_precip(key, d)
            if not p:
                continue
            jobs.append((p, lat_lo, lat_hi, lon_lo, lon_hi))
            path_meta[p] = (key, d)

    total = len(jobs)
    done = {"n": 0}
    progress("precip", "直读累计降水产品切片", 0, total,
             f"共 {total} 个产品文件")

    def on_done(k: int) -> None:
        done["n"] += k
        progress("precip", "直读累计降水产品切片", done["n"], total,
                 f"{done['n']}/{total} 文件")

    cache = read_precip_batch(jobs, workers=PRECIP_IO_WORKERS, on_done=on_done,
                              should_stop=should_stop)
    arrays: Dict[str, np.ndarray] = {
        k: np.full((len(read_dates),) + shape, np.nan, dtype=np.float32)
        for k in usable
    }
    for p, (key, d) in path_meta.items():
        arr = cache.get(p)
        if arr is None or arr.shape != shape:
            continue
        arrays[key][read_dates.index(d)] = arr

    # 目标日必须可读，否则该窗口无法作为检索锚点
    usable = [k for k in usable if np.isfinite(arrays[k][target_i]).all()]
    skipped += [{"key": k, "reason": f"{PRECIP_PRODUCTS[k].label()} 目标日读取失败"}
                for k in target_paths if k not in usable]
    if not usable:
        return {"enabled": True, "available": False, "windows": {},
                "skipped": skipped, "reason": "目标日累计降水产品读取失败",
                "_lat": p_lat, "_lon": p_lon, "grid": p_grid.desc()}

    ok_idx = [i for i, d in enumerate(read_dates)
              if all(np.isfinite(arrays[k][i]).all() for k in usable)]
    hist_idx = [i for i in ok_idx if i != target_i]
    hist_dates = [read_dates[i] for i in hist_idx]
    if not hist_dates:
        return {"enabled": True, "available": False, "windows": {},
                "skipped": skipped, "reason": "没有可用的历史累计降水样本",
                "_lat": p_lat, "_lon": p_lon, "grid": p_grid.desc()}

    excl_mask = np.array([abs((d - target).days) <= cfg.exclude_days
                          for d in hist_dates], dtype=bool)
    formula_fn = (compile_formula(cfg.similarity_formula)
                  if cfg.similarity == "custom" else None)
    ths = sorted({float(t) for t in cfg.precip_thresholds})
    # 单窗口时按候选样本分块并行；多窗口时由窗口级线程池并行，避免嵌套过载。
    chunk_workers = _chunk_workers() if len(usable) == 1 else 1

    def build_window(key: str) -> Dict:
        """单个窗口的相似度排名 + 产品统计（可在独立线程中执行）。"""
        H = arrays[key][hist_idx].reshape(len(hist_idx), -1)[:, order]
        x = arrays[key][target_i].reshape(-1)[order]
        if cfg.feature_process == "standardize":
            sigma = float(np.std(H))
            if sigma > 1e-9:
                H, x = H / sigma, x / sigma
        elif cfg.feature_process == "normalize":
            lo, hi = float(np.min(H)), float(np.max(H))
            if hi - lo > 1e-9:
                H = (H - lo) / (hi - lo)
                x = (x - lo) / (hi - lo)
        si = _sim_scores_chunked(cfg, H, x, w_ord, formula_fn, chunk_workers)
        si = np.where(excl_mask, np.inf, si)
        n_available = int(np.isfinite(si).sum())
        order_idx = np.argsort(si, kind="stable")
        rows, used_pos = [], []
        for rank, pos in enumerate(order_idx, 1):
            if not np.isfinite(si[pos]):
                break
            arr_i = hist_idx[pos]
            rows.append({"rank": rank, "date": hist_dates[pos].isoformat(),
                         "si": float(si[pos]),
                         "mm": round(float(arrays[key][arr_i, i_pt, j_pt]), 2)})
            used_pos.append(arr_i)
        if not rows:
            return {"key": key, "empty": True, "n_available": n_available}

        topn = min(int(cfg.precip_topn), len(rows))
        # 全部排名都保留；仅前 N 名的二维场进入缓存（超出部分重算时按排名回读）
        cache_n = min(len(rows), max(topn, PRECIP_FIELD_CACHE))
        fields_all = arrays[key][used_pos[:cache_n]].astype(np.float32)
        field_top = fields_all[:topn]
        target_field = arrays[key][target_i]
        prod, spatial = precip_products(field_top, target_field, i_pt, j_pt,
                                        ths, DEFAULT_PERCENTILE)
        prod["samples"] = [{"date": r["date"], "mm": r["mm"]}
                           for r in sorted(rows[:topn], key=lambda r: -r["mm"])]
        win = PRECIP_PRODUCTS[key]
        start, end = win.bounds_utc(target)
        lst = timedelta(hours=8)
        return {
            "key": key,
            "label": win.label(),
            "group": win.group,
            "file": os.path.basename(target_paths[key]),
            "start_utc": start.strftime("%Y-%m-%d %H:%M"),
            "end_utc": end.strftime("%Y-%m-%d %H:%M"),
            "start_lst": (start + lst).strftime("%Y-%m-%d %H:%M"),
            "end_lst": (end + lst).strftime("%Y-%m-%d %H:%M"),
            "n_candidates": len(hist_dates),
            "n_available": n_available,
            "n_valid": topn,
            "topn": topn,
            "max_topn": n_available,
            "cached_topn": len(fields_all),
            "percentile": DEFAULT_PERCENTILE,
            "thresholds": ths,
            "ranking": rows,
            "target_file": target_paths[key],
            "target_field": target_field,
            "_fields": fields_all,
            "_dates": [r["date"] for r in rows[:cache_n]],
            "_rank_dates": [r["date"] for r in rows],
            "_rank_si": np.asarray([r["si"] for r in rows], dtype=np.float64),
            "_rank_mm": np.asarray([r["mm"] for r in rows], dtype=np.float32),
            "products": {**prod, "spatial": spatial},
        }

    workers = _score_workers(len(usable))
    windows: Dict[str, Dict] = {}
    done_n = 0
    label_p = f"窗口并行 ×{workers}" + (f" · 分块 ×{chunk_workers}"
                                        if chunk_workers > 1 else "")
    progress("precip", f"累计降水相似排名（{label_p}）", 0, len(usable),
             "各窗口独立排名与统计")
    if workers <= 1 or len(usable) == 1:
        for key in usable:
            if should_stop and should_stop():
                raise Cancelled("检索已取消")
            try:
                win = build_window(key)
            except Exception as exc:  # 单窗口失败不中断其余窗口
                skipped.append({"key": key, "reason":
                                f"{PRECIP_PRODUCTS[key].label()} 计算失败："
                                f"{type(exc).__name__}: {exc}"})
                done_n += 1
                continue
            if win.get("empty"):
                skipped.append({"key": key, "reason": f"{PRECIP_PRODUCTS[key].label()} "
                                                       "没有可用的相似历史日"})
            else:
                windows[key] = win
            done_n += 1
            progress("precip", f"累计降水相似排名（{label_p}）",
                     done_n, len(usable), f"{PRECIP_PRODUCTS[key].label()}")
    else:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="histsim-precip") as ex:
            futs = {ex.submit(build_window, key): key for key in usable}
            for fut in as_completed(futs):
                key = futs[fut]
                if should_stop and should_stop():
                    for f in futs:
                        f.cancel()
                    raise Cancelled("检索已取消")
                try:
                    win = fut.result()
                except Exception as exc:
                    skipped.append({"key": key, "reason":
                                    f"{PRECIP_PRODUCTS[key].label()} 计算失败："
                                    f"{type(exc).__name__}: {exc}"})
                else:
                    if win.get("empty"):
                        skipped.append({"key": key, "reason":
                                        f"{PRECIP_PRODUCTS[key].label()} "
                                        "没有可用的相似历史日"})
                    else:
                        windows[key] = win
                done_n += 1
                progress("precip", f"累计降水相似排名（{label_p}）",
                         done_n, len(usable),
                         f"{PRECIP_PRODUCTS[key].label()} 完成")

    # 按配置顺序输出，避免线程完成顺序影响界面 tab 顺序
    windows = {k: windows[k] for k in usable if k in windows}

    return {
        "enabled": True,
        "available": bool(windows),
        "windows": windows,
        "skipped": skipped,
        "n_candidates": len(hist_dates),
        "n_windows": len(usable),
        "parallel_workers": workers,
        "chunk_workers": chunk_workers,
        "io_workers": PRECIP_IO_WORKERS,
        "percentile": DEFAULT_PERCENTILE,
        "point": [cfg.point_lat, cfg.point_lon],
        "thresholds": ths,
        "grid": p_grid.desc(),
        "_lat": p_lat, "_lon": p_lon,
    }


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
    # 单要素：不做交集，直接采用该要素 TopN 榜单
    assert comb["0"]["single_element"]
    assert "不做交集" in comb["0"]["source"][best]
    # 收紧 TopN 后只保留该要素排名前 3 的样本（保持相似度顺序）
    tight = _combine(cfg.copy(inter_topn=3), ranking)
    assert tight["0"]["final"] == [r["date"] for r in rows[:3]], \
        tight["0"]["final"]
    assert tight["0"]["inter_topn_used"] == 3 and not tight["0"]["inter_topn_auto"]
    # 单要素模式不计算交集可行性：候选池 = 20 个样本剔除 ±3 天窗
    dg = tight["0"]["diagnose"]
    assert dg["n_candidates"] == 17 and dg["min_feasible_topn"] == 1, dg
    assert dg["curve"] == [], dg
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
    # ── V2.2.0：单窗口分块并行与整块计算必须一致 ────────────────────
    Xb = np.random.default_rng(7).random((1200, 9)).astype(np.float32)
    xb = Xb[42] + 0.01
    for sim in ("weighted_abs", "weighted_sq", "euclidean", "cosine", "correlation"):
        full = _sim_scores(cfg.copy(similarity=sim), Xb, xb, w)
        part = _sim_scores_chunked(cfg.copy(similarity=sim), Xb, xb, w, None, 4)
        assert np.allclose(full, part), sim
    full = _sim_scores(cfg.copy(similarity="custom"), Xb, xb, w,
                       compile_formula("sqrt(sum(w*d*d))"))
    part = _sim_scores_chunked(cfg.copy(similarity="custom"), Xb, xb, w,
                               compile_formula("sqrt(sum(w*d*d))"), 4)
    assert np.allclose(full, part)
    assert np.allclose(_sim_scores_chunked(cfg, Xb, xb, w, None, 1),
                       _sim_scores(cfg, Xb, xb, w))
    assert _chunk_workers() >= 1
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
    # ── V2.2.0：目标日 + 分位数 + 自定义百分位产品 ──────────────────
    top = np.arange(4 * 2 * 3, dtype=np.float32).reshape(4, 2, 3)
    target_f = top[-1] + 1.0
    prod, spatial = precip_products(top, target_f, 0, 1, [0.1, 50.0], 80.0)
    assert prod["n_valid"] == 4 and prod["target_mm"] == float(target_f[0, 1])
    assert set(prod["quantiles"]) == {"10", "25", "50", "75", "90"}
    for k in ("mean", "median", "q10", "q25", "q50", "q75", "q90",
              "custom", "target", "prob_0.1", "prob_50.0"):
        assert spatial[k].shape == (2, 3), k
    assert prod["pctl_mm"] == round(float(np.percentile(top[:, 0, 1], 80.0)), 2)
    assert prod["percentile"] == 80.0
    # 中位数与 P50 分位场必须一致（同一口径的两种入口）
    assert np.allclose(spatial["median"], spatial["q50"])
    assert abs(prod["median_mm"] - prod["quantiles"]["50"]) < 1e-9
    assert _score_workers(8) >= 1 and _score_workers(1) == 1
    print("engine self-check OK")


if __name__ == "__main__":
    _self_check()