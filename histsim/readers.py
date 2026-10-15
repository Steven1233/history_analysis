# -*- coding: utf-8 -*-
"""直读访问层：ERA5 / GMCP 原始 NetCDF 的 hyperslab 切片读取。

* ERA5 日文件结构固定为 ``valid_time=24 × pressure_level=23 × lat=281 × lon=401``，
  任何「时次 × 层次 × 区域」切片都可在原文件上直接读，**无需预处理**；
* 同一文件的多个时次、多个层次一次读出（合并切片），把 N 次文件打开降为 1 次；
* 按文件分片交给进程池并行——瓶颈是 HDF5 分块解压与文件打开（实测 ~127ms/文件），
  多进程各自打开文件可绕开单进程 HDF5 串行锁。
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from netCDF4 import Dataset, num2date

from .config import ELEMENTS, SearchConfig, split_key


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


# 读取是「文件打开 + HDF5 解压」主导，多进程各自打开文件可绕开单进程串行锁；
# 但同盘并发过高会互相拖慢，因此按核数折中并允许用环境变量覆盖。
DEFAULT_IO_WORKERS = _env_int("HISTSIM_IO_WORKERS",
                              min(6, max(4, (mp.cpu_count() or 4) // 4)))
CPU_WORKERS = max(1, (mp.cpu_count() or 4) - 1)

# 进程池回收参数：轮询间隔，以及「连续多久没有任何 worker 交回结果」判为失联。
POOL_POLL_S = 0.2
POOL_STALL_S = float(os.environ.get("HISTSIM_IO_STALL_S") or 600)


def _pool_map(jobs: List[tuple], func, workers: int = 0, on_done=None,
              should_stop=None) -> Tuple[List, List]:
    """把一批任务交给进程池，边完成边回收，使取消与 worker 失联都能立刻生效。

    **不用 ``Pool.imap_unordered``**：那个迭代器只在下一条结果**到达**时才把控制权
    交回主循环，worker 一旦被关闭（例如用户关掉了它弹出的控制台窗口）或卡在坏文件
    上，主循环就再也等不到结果，而 ``Pool.terminate()`` 也唤不醒阻塞中的迭代——
    用户看到的现象正是「进度停在最后一个文件、点取消毫无反应」。这里改为轮询式
    回收：每 ``POOL_POLL_S`` 秒查一次取消标志；连续 ``POOL_STALL_S`` 秒没有任何
    结果就判为 worker 失联，记下剩余任务后放弃等待，不再无限卡住。

    返回 ``(results, errors)``：results 为 ``[(path, payload), ...]``，
    errors 为 ``[(path, 原因), ...]``；顺序与 ``jobs`` 无关，由调用方按 path 对应。
    """
    jobs = list(jobs)
    results: List[Tuple[str, object]] = []
    errors: List[Tuple[str, str]] = []
    if not jobs:
        return results, errors

    def collect(job, value=None, exc=None) -> None:
        if exc is not None:
            errors.append((job[0], f"{type(exc).__name__}: {exc}"))
        else:
            path, payload, err = value
            if err:
                errors.append((path, err))
            else:
                results.append((path, payload))
        if on_done:
            on_done(1)

    workers = max(1, workers or DEFAULT_IO_WORKERS)
    if workers == 1 or len(jobs) == 1:  # 单文件/单进程：不起池，省掉 spawn 开销
        for job in jobs:
            if should_stop and should_stop():
                break
            try:
                collect(job, value=func(job))
            except Exception as exc:
                collect(job, exc=exc)
        return results, errors

    workers = min(workers, len(jobs))
    cap = workers * 4          # 在飞任务上限：GMCP 一批可上万个任务，不能一次全提交
    queue = list(jobs)
    pending: Dict[object, tuple] = {}
    pool = mp.Pool(processes=workers)
    try:
        last = time.monotonic()
        while queue or pending:
            if should_stop and should_stop():
                break
            while queue and len(pending) < cap:
                job = queue.pop()
                pending[pool.apply_async(func, (job,))] = job
            got = False
            for ar in list(pending):
                if not ar.ready():
                    continue
                got = True
                job = pending.pop(ar)
                try:
                    collect(job, value=ar.get())
                except Exception as exc:  # worker 崩溃时 get() 会抛出
                    collect(job, exc=exc)
            if got:
                last = time.monotonic()
                continue
            if time.monotonic() - last > POOL_STALL_S:
                for job in pending.values():
                    collect(job, exc=TimeoutError(
                        f"worker 连续 {POOL_STALL_S:g}s 未交回结果"))
                pending.clear()
                queue.clear()
                break
            time.sleep(POOL_POLL_S)
    finally:
        pool.terminate()   # 硬杀 worker：卡死的 worker 也能立即回收，不再等它
        pool.join()
    return results, errors


# ── 区域与权重网格 ────────────────────────────────────────────────────
def nearest_slice(axis: np.ndarray, lo: float, hi: float) -> slice:
    """返回覆盖 [lo, hi] 的**连续**下标切片（轴可为升序或降序）。"""
    idx = np.nonzero((axis >= lo) & (axis <= hi))[0]
    if idx.size == 0:  # 区域落在网格外时取最近一点
        i = int(np.argmin(np.abs(axis - (lo + hi) / 2.0)))
        return slice(i, i + 1)
    return slice(int(idx[0]), int(idx[-1]) + 1)


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    """点（标量或数组）到中心的球面距离（km）。"""
    r = 6371.0088
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


class Grid:
    """区域网格：格点坐标、由近及远排序索引、距离权重向量。

    只依赖「中心 / 范围 / 权重模式」，与具体数据无关，因此每个进程算一次即可。
    """

    def __init__(self, lat: np.ndarray, lon: np.ndarray, cfg: SearchConfig):
        lat2d, lon2d = np.meshgrid(lat, lon, indexing="ij")
        self.lat = np.asarray(lat, dtype=np.float64)
        self.lon = np.asarray(lon, dtype=np.float64)
        self.nlat, self.nlon = self.lat.size, self.lon.size
        self.dist_km = haversine_km(cfg.center_lat, cfg.center_lon,
                                    lat2d, lon2d).ravel()
        self.order = np.argsort(self.dist_km, kind="stable")  # 由近及远
        self.weight = self._weights(cfg)

    def _weights(self, cfg: SearchConfig) -> np.ndarray:
        d = self.dist_km
        if cfg.weight_mode == "gauss":
            r0 = max(cfg.gauss_r0_km, 1e-6)
            return np.exp(-(d / r0) ** 2).astype(np.float64)
        w = np.full_like(d, cfg.zones[-1][1] if cfg.zones else 1.0)
        for radius, weight in sorted(cfg.zones):
            w[d <= radius] = weight
        return w

    @property
    def n_points(self) -> int:
        return self.nlat * self.nlon

    def apply_order(self, flat: np.ndarray) -> np.ndarray:
        """把 [..., n_points] 按「由近及远」重排（fancy indexing，一次完成）。"""
        return flat[..., self.order]

    def desc(self) -> Dict:
        return {"n_lat": self.nlat, "n_lon": self.nlon, "n_points": self.n_points,
                "lat_range": [round(float(self.lat.min()), 3), round(float(self.lat.max()), 3)],
                "lon_range": [round(float(self.lon.min()), 3), round(float(self.lon.max()), 3)],
                "max_dist_km": round(float(self.dist_km.max()), 1)}


# ── ERA5 ──────────────────────────────────────────────────────────────
# 同一要素目录内混用两套命名：早期文件是 valid_time / pressure_level，
# 后期文件是 time / level（units 也不同），因此一律按名字+单位动态解析。
TIME_NAMES = ("valid_time", "time")
LEVEL_NAMES = ("pressure_level", "level", "isobaricInhPa", "plev")
LAT_NAMES = ("latitude", "lat")
LON_NAMES = ("longitude", "lon")


def _pick(names, available) -> Optional[str]:
    for n in names:
        if n in available:
            return n
    return None


def era5_layout(path: str) -> Dict:
    """读出一个 ERA5 文件的坐标轴名与维度顺序（只开一次文件）。"""
    with Dataset(path) as ds:
        names = set(ds.variables)
        return {
            "time": _pick(TIME_NAMES, names),
            "level": _pick(LEVEL_NAMES, names),
            "lat": _pick(LAT_NAMES, names),
            "lon": _pick(LON_NAMES, names),
        }


def _hour_indices(ds, time_name: str, hours: Sequence[int]) -> List[int]:
    """把时间轴换算成 UTC 小时再匹配，兼容不同 units/calendar。"""
    var = ds.variables[time_name]
    dts = num2date(np.asarray(var[:]), getattr(var, "units", "hours since 1970-01-01"),
                   getattr(var, "calendar", "standard"), only_use_cftime_datetimes=False)
    hh = np.array([int(getattr(d, "hour", 0)) for d in np.atleast_1d(dts)])
    out = []
    for h in hours:
        hit = np.nonzero(hh == int(h))[0]
        if hit.size == 0:
            raise KeyError(f"文件中不存在 {h:02d}UTC 时次")
        out.append(int(hit[0]))
    return out


def read_era5_slab(path: str, var: str, hours: Sequence[int],
                   level_hpa: Sequence[float], lat_sl: slice, lon_sl: slice):
    """读一个 ERA5 日文件的切片。

    返回 ``(arr, levels)``，arr 形状 ``(n_hours, n_levels, n_lat, n_lon)``，
    与文件内部维度顺序无关（按轴名归位后统一转置）。
    """
    want = [float(l) for l in level_hpa]
    with Dataset(path) as ds:
        if var not in ds.variables:
            raise KeyError(f"{path} 中不存在变量 {var}")
        names = set(ds.variables)
        tname, lname = _pick(TIME_NAMES, names), _pick(LEVEL_NAMES, names)
        latname, lonname = _pick(LAT_NAMES, names), _pick(LON_NAMES, names)
        if not (tname and lname and latname and lonname):
            raise KeyError(f"{path} 缺少必要坐标轴: "
                           f"time={tname} level={lname} lat={latname} lon={lonname}")
        h_idx = _hour_indices(ds, tname, hours)
        levels_all = np.asarray(ds.variables[lname][:], dtype=np.float64)
        l_idx = [int(np.argmin(np.abs(levels_all - l))) for l in want]

        variable = ds.variables[var]
        tags, sel = [], []
        for d in variable.dimensions:
            if d == tname:
                tags.append("t"); sel.append(h_idx)
            elif d == lname:
                tags.append("l"); sel.append(l_idx)
            elif d == latname:
                tags.append("y"); sel.append(lat_sl)
            elif d == lonname:
                tags.append("x"); sel.append(lon_sl)
            else:  # number / expver 等标量维：取第 0 个，该维随之消失
                tags.append("z"); sel.append(0)
        raw = np.asarray(variable[tuple(sel)], dtype=np.float32)
        kept = [tg for tg in tags if tg != "z"]
        arr = np.transpose(raw, [kept.index(k) for k in ("t", "l", "y", "x")])
    return arr, levels_all[l_idx]


def era5_axes(path: str):
    """只读取坐标轴（用于确定区域切片），不碰数据变量。"""
    with Dataset(path) as ds:
        names = set(ds.variables)
        latname = _pick(LAT_NAMES, names)
        lonname = _pick(LON_NAMES, names)
        return (np.asarray(ds.variables[latname][:], dtype=np.float64),
                np.asarray(ds.variables[lonname][:], dtype=np.float64))


def _era5_cache_path(cache_dir: str, path: str) -> Optional[str]:
    """切片缓存路径：按 要素目录 / 年月 分桶，避免单目录文件过多。"""
    base = os.path.basename(path)
    if not base.endswith(".nc"):
        return None
    stem = base[:-3]
    parts = stem.split(".")
    if len(parts) < 3:
        return None
    elem, datestr = parts[1], parts[-1]
    if len(datestr) != 8 or not datestr.isdigit():
        return None
    return os.path.join(cache_dir, elem, datestr[:6], stem + ".npz")


def _slab_key(var: str, hours: Sequence[int], levels: Sequence[float],
              lat_sl: slice, lon_sl: slice) -> str:
    return "|".join([
        var,
        ",".join(str(int(h)) for h in hours),
        ",".join(f"{float(l):g}" for l in levels),
        f"{lat_sl.start}:{lat_sl.stop}", f"{lon_sl.start}:{lon_sl.stop}",
    ])


def _read_slab_cached(path: str, var: str, hours: Sequence[int],
                      levels: Sequence[float], lat_sl: slice, lon_sl: slice,
                      cache_dir: Optional[str]):
    """带磁盘缓存的切片读取：命中则跳过 NetCDF 打开（冷盘开销的主要来源）。"""
    key = None
    cpath = None
    if cache_dir:
        cpath = _era5_cache_path(cache_dir, path)
        if cpath:
            key = _slab_key(var, hours, levels, lat_sl, lon_sl)
            if os.path.isfile(cpath):
                try:
                    with np.load(cpath) as z:
                        if key in z.files:
                            return z[key], z["levels"]
                except (OSError, ValueError):
                    pass  # 缓存损坏则回退到直读
    arr, lv = read_era5_slab(path, var, hours, levels, lat_sl, lon_sl)
    if cpath and key:
        try:
            os.makedirs(os.path.dirname(cpath), exist_ok=True)
            np.savez_compressed(cpath, levels=lv, **{key: arr})
        except OSError:
            pass  # 缓存写失败不影响检索
    return arr, lv


def _era5_worker(job):
    """进程池 worker（模块顶层定义以便 Windows spawn 下 pickle）。"""
    path, var, hours, levels, lat_sl, lon_sl, cache_dir = job
    try:
        arr, _ = _read_slab_cached(path, var, hours, levels, lat_sl, lon_sl, cache_dir)
        return path, arr.reshape(arr.shape[0] * arr.shape[1], -1), None
    except Exception as exc:  # 坏文件不应中断整体检索
        return path, None, f"{type(exc).__name__}: {exc}"


def read_era5_batch(jobs: List[tuple], workers: int = 0, on_done=None,
                    should_stop=None, cache_dir: Optional[str] = None) -> Tuple[List, List]:
    """并行读取一批 ERA5 切片。

    ``jobs`` 每项为 ``(path, var, hours, levels, lat_sl, lon_sl)``；
    返回 ``(results, errors)``，results 为 ``[(path, arr2d), ...]``，
    arr2d 形状 ``(n_hours*n_levels, n_points)`` —— 顺序与 jobs 无关，
    由调用方按 path 自行对应。
    """
    jobs = [tuple(j) + (cache_dir,) for j in jobs]
    return _pool_map(jobs, _era5_worker, workers, on_done, should_stop)


# ── GMCP ──────────────────────────────────────────────────────────────
def gmcp_axes(path: str):
    with Dataset(path) as ds:
        return (np.asarray(ds.variables["lat"][:], dtype=np.float64),
                np.asarray(ds.variables["lon"][:], dtype=np.float64))


def read_gmcp_region(path: str, lat_lo: float, lat_hi: float,
                     lon_lo: float, lon_hi: float):
    """读 GMCP 单时次文件的目标区域，返回 ``(arr2d, lat, lon)``（mm/h）。"""
    with Dataset(path) as ds:
        lat = np.asarray(ds.variables["lat"][:], dtype=np.float64)
        lon = np.asarray(ds.variables["lon"][:], dtype=np.float64)
        li = nearest_slice(lat, lat_lo, lat_hi)
        lo = nearest_slice(lon, lon_lo, lon_hi)
        arr = ds.variables["rain_rate"][li, lo]
        out_lat, out_lon = lat[li], lon[lo]
    a = np.asarray(arr, dtype=np.float32)
    a[~np.isfinite(a)] = 0.0
    a[a < 0] = 0.0
    return a, out_lat, out_lon


def _gmcp_worker(job):
    path, lat_lo, lat_hi, lon_lo, lon_hi = job
    try:
        arr, _, _ = read_gmcp_region(path, lat_lo, lat_hi, lon_lo, lon_hi)
        return path, arr, None
    except Exception as exc:
        return path, None, f"{type(exc).__name__}: {exc}"


def read_gmcp_batch(jobs: List[tuple], workers: int = 0,
                    on_done=None, should_stop=None) -> Dict[str, np.ndarray]:
    """并行读取一批 GMCP 小时文件，返回 ``{path: arr2d}``（坏文件自动跳过）。"""
    results, _ = _pool_map(list(jobs), _gmcp_worker, workers, on_done, should_stop)
    return {p: arr for p, arr in results}


def _self_check() -> None:
    import glob
    import time
    cfg = SearchConfig()
    cfg.half_span_deg = 1.0
    lat = np.arange(24.25, 21.9, -0.25)
    lon = np.arange(112.25, 114.5, 0.25)
    g = Grid(lat, lon, cfg)
    assert g.n_points == len(lat) * len(lon)
    assert np.all(np.diff(g.dist_km[g.order]) >= 0), "必须由近及远"
    assert g.weight.max() <= 1.0 and g.weight.min() > 0
    # 中心格点距离应最小
    i_center = int(np.argmin(np.abs(lat - cfg.center_lat)))
    j_center = int(np.argmin(np.abs(lon - cfg.center_lon)))
    assert g.order[0] == i_center * len(lon) + j_center

    lat_sl = nearest_slice(np.arange(60, -10.01, -0.25), 22.16, 24.16)
    lon_sl = nearest_slice(np.arange(50, 150.01, 0.25), 112.34, 114.34)
    # 同一目录内存在两套命名（valid_time/pressure_level 与 time/level），两者都必须能读
    cases = [("u", r"I:\u_winds_2000_2024", "era5.u_component_of_wind.20000401.nc"),
             ("u", r"I:\u_winds_2000_2024", "era5.u_component_of_wind.20240630.nc"),
             ("r", r"I:\relative_humidity_2000_2024", "era5.relative_humidity.20000201.nc"),
             ("r", r"I:\relative_humidity_2000_2024", "era5.relative_humidity.20240630.nc")]
    n_ok = 0
    for var, root, fname in cases:
        p = os.path.join(root, fname)
        if not os.path.isfile(p):
            continue
        t0 = time.perf_counter()
        arr, lv = read_era5_slab(p, var, [0, 6], [925, 500], lat_sl, lon_sl)
        dt = (time.perf_counter() - t0) * 1000
        assert arr.shape[0] == 2 and arr.shape[1] == 2, (fname, arr.shape)
        assert set(np.round(lv, 0).tolist()) == {925.0, 500.0}, (fname, lv)
        assert np.isfinite(arr).all(), fname
        assert not np.allclose(arr[0], arr[1]), fname  # 不同时次必须有差异
        print(f"  {fname:44} shape={arr.shape} {dt:6.0f} ms")
        n_ok += 1
    if n_ok:
        p = os.path.join(cases[0][1], cases[0][2])
        r = read_era5_batch([(p, "u", [0], [925], lat_sl, lon_sl),
                             (p, "u", [12], [850], lat_sl, lon_sl)], workers=2)
        assert len(r[0]) == 2 and not r[1], r[1]
    gp = r"I:\GMCP\2000\GMCP_2000_01_01_00.nc"
    if os.path.isfile(gp):
        a, gla, glo = read_gmcp_region(gp, 22.16, 24.16, 112.34, 114.34)
        assert a.ndim == 2 and a.shape == (gla.size, glo.size), a.shape
        assert (a >= 0).all()
        d = read_gmcp_batch([(gp, 22.16, 24.16, 112.34, 114.34)], workers=1)
        assert list(d.values())[0].shape == a.shape
    print("readers self-check OK")


if __name__ == "__main__":
    _self_check()