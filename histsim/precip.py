# -*- coding: utf-8 -*-
"""GMCP 累计降水产品：北京时间窗口定义、预处理引擎与产品命名。

业务口径（V2.1.0）：

* 源数据为 GMCP 世界时（UTC）逐小时降水，文件名 ``<root>/<yyyy>/GMCP_<yyyy>_<mm>_<dd>_<hh>.nc``；
* 产品按北京时间（LST = UTC+8）累加，文件名按业务约定的日期锚点命名：

  - 24h：``GMCP_24h_Rain_<yyyymmdd>_20_20LST.nc``（日期=窗口结束日）
          ``GMCP_24h_Rain_<yyyymmdd>_08_08LST.nc``（日期=窗口开始日）
  - 12h：``GMCP_12h_Rain_<yyyymmdd>_08LST.nc`` / ``_20LST.nc``（日期=窗口结束日）
  - 6h ：``GMCP_6h_Rain_<yyyymmdd>_02LST.nc`` / ``08LST`` / ``14LST`` / ``20LST``
          （日期=窗口结束日）

* 每个产品是窗口内各小时降水之和（mm）；小时文件的取值时刻对应「该小时结束」，
  因此窗口内取 ``start_utc+1h … end_utc`` 共 ``duration_h`` 个时次——这与需求中
  给出的 UTC 时次区间逐条对齐（见 ``_self_check`` 的断言）。

预处理框架按「产品定义表 + 通用执行器」组织：新增一种累计窗口只需在
``PRECIP_PRODUCTS`` 里加一条定义，执行器、登记与检索无需改动。
"""
from __future__ import annotations

import calendar
import os
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

TZ_OFFSET_H = 8                     # 北京时间 = UTC+8
HOURLY_RE = re.compile(r"^GMCP_(\d{4})_(\d{2})_(\d{2})_(\d{2})\.nc$", re.I)


class PreprocessCancelled(RuntimeError):
    """预处理任务被用户取消。"""


@dataclass(frozen=True)
class PrecipProduct:
    """一种累计降水产品定义（窗口长度 + 北京时间锚点 + 命名规则）。"""

    key: str
    group: str          # 24h | 12h | 6h
    duration_h: int
    anchor: str         # end：文件名日期为窗口结束日；start：为开始日
    hour: int           # anchor=end 时为结束小时；anchor=start 时为开始小时（LST）
    name: str

    @property
    def folder(self) -> str:
        return f"GMCP_{self.group}_Rain"

    def filename(self, day: date) -> str:
        d = f"{day:%Y%m%d}"
        if self.group == "24h":
            hh = self.hour
            return f"GMCP_24h_Rain_{d}_{hh:02d}_{hh:02d}LST.nc"
        return f"GMCP_{self.group}_Rain_{d}_{self.hour:02d}LST.nc"

    def bounds_utc(self, day: date) -> Tuple[datetime, datetime]:
        """返回窗口的 UTC 起止边界（左开右闭：起 + 1h … 止）。"""
        base = datetime(day.year, day.month, day.day)
        if self.anchor == "end":
            end = base + timedelta(hours=self.hour)
            start = end - timedelta(hours=self.duration_h)
        else:
            start = base + timedelta(hours=self.hour)
            end = start + timedelta(hours=self.duration_h)
        off = timedelta(hours=TZ_OFFSET_H)
        return start - off, end - off

    def hours_utc(self, day: date) -> List[datetime]:
        start, end = self.bounds_utc(day)
        return [start + timedelta(hours=i) for i in range(1, self.duration_h + 1)]

    def _regex(self) -> re.Pattern:
        if self.group == "24h":
            hh = self.hour
            return re.compile(
                rf"^GMCP_24h_Rain_(\d{{8}})_{hh:02d}_{hh:02d}LST\.nc$", re.I)
        return re.compile(
            rf"^GMCP_{self.group}_Rain_(\d{{8}})_{self.hour:02d}LST\.nc$", re.I)

    def match_filename(self, name: str) -> Optional[date]:
        m = self._regex().match(name)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            return None

    def label(self) -> str:
        table = {
            "24h_2020": "24h 累计降水（前一日20时—当日20时，北京时）",
            "24h_0808": "24h 累计降水（当日08时—次日08时，北京时）",
            "12h_08": "12h 累计降水（前一日20时—当日08时，北京时）",
            "12h_20": "12h 累计降水（当日08时—20时，北京时）",
            "6h_02": "6h 累计降水（前一日20时—当日02时，北京时）",
            "6h_08": "6h 累计降水（当日02时—08时，北京时）",
            "6h_14": "6h 累计降水（当日08时—14时，北京时）",
            "6h_20": "6h 累计降水（当日14时—20时，北京时）",
        }
        return table.get(self.key, self.name)


PRECIP_PRODUCTS: Dict[str, PrecipProduct] = {
    p.key: p for p in (
        PrecipProduct("24h_2020", "24h", 24, "end", 20, "24h 20-20"),
        PrecipProduct("24h_0808", "24h", 24, "start", 8, "24h 08-08"),
        PrecipProduct("12h_08", "12h", 12, "end", 8, "12h 08"),
        PrecipProduct("12h_20", "12h", 12, "end", 20, "12h 20"),
        PrecipProduct("6h_02", "6h", 6, "end", 2, "6h 02"),
        PrecipProduct("6h_08", "6h", 6, "end", 8, "6h 08"),
        PrecipProduct("6h_14", "6h", 6, "end", 14, "6h 14"),
        PrecipProduct("6h_20", "6h", 6, "end", 20, "6h 20"),
    )
}

# 24h 默认两套都作为目标检索（需求：24h 为第一优先）
DEFAULT_WINDOWS: List[str] = ["24h_2020", "24h_0808"]
GROUPS: Dict[str, List[str]] = {
    "24h": [k for k, p in PRECIP_PRODUCTS.items() if p.group == "24h"],
    "12h": [k for k, p in PRECIP_PRODUCTS.items() if p.group == "12h"],
    "6h": [k for k, p in PRECIP_PRODUCTS.items() if p.group == "6h"],
}


def match_filename(name: str) -> Optional[Tuple[str, date]]:
    """识别一个预处理产品文件名，返回 ``(product_key, 日期)``。"""
    for key, prod in PRECIP_PRODUCTS.items():
        d = prod.match_filename(name)
        if d is not None:
            return key, d
    return None


def hourly_path(root: str, dt: datetime) -> str:
    return os.path.join(root, f"{dt.year:04d}", f"GMCP_{dt:%Y_%m_%d_%H}.nc")


def product_path(root: str, key: str, day: date) -> str:
    prod = PRECIP_PRODUCTS[key]
    return os.path.join(root, prod.folder, prod.filename(day))


def dates_between(year_from: int, year_to: int):
    for year in range(int(year_from), int(year_to) + 1):
        for month in range(1, 13):
            for d in range(1, calendar.monthrange(year, month)[1] + 1):
                yield date(year, month, d)


# ── 目录扫描 ──────────────────────────────────────────────────────────
def _first_hourly(root: str, year_from: int, year_to: int) -> Optional[str]:
    for year in range(int(year_from), int(year_to) + 1):
        yp = os.path.join(root, f"{year:04d}")
        if not os.path.isdir(yp):
            continue
        for name in sorted(os.listdir(yp)):
            if HOURLY_RE.match(name):
                return os.path.join(yp, name)
    return None


def scan_source(root: str, year_from: int, year_to: int) -> Dict:
    """扫描小时源目录：统计可用的年/月文件数，并抽样读出网格坐标。"""
    root = os.path.abspath(root)
    out: Dict = {"root": root, "ok": False, "n_files": 0, "years": {},
                 "axes": None, "warning": ""}
    if not os.path.isdir(root):
        out["warning"] = "目录不存在或不可访问"
        return out
    for year in range(int(year_from), int(year_to) + 1):
        yp = os.path.join(root, f"{year:04d}")
        if not os.path.isdir(yp):
            continue
        try:
            names = list(os.listdir(yp))
        except OSError as exc:
            out["warning"] = f"{year} 年目录不可读: {exc}"
            continue
        for name in names:
            m = HOURLY_RE.match(name)
            if m and int(m.group(1)) == year:
                month = int(m.group(2))
                out["years"].setdefault(year, {})
                out["years"][year][month] = out["years"][year].get(month, 0) + 1
                out["n_files"] += 1
    sample = _first_hourly(root, year_from, year_to)
    if sample:
        try:
            from .readers import gmcp_axes
            lat, lon = gmcp_axes(sample)
            out["axes"] = {"n_lat": int(lat.size), "n_lon": int(lon.size),
                           "lat_range": [round(float(lat.min()), 3),
                                         round(float(lat.max()), 3)],
                           "lon_range": [round(float(lon.min()), 3),
                                         round(float(lon.max()), 3)]}
        except Exception as exc:  # 坏样本不应让扫描整体失败
            out["warning"] = f"样本文件读取失败: {type(exc).__name__}: {exc}"
    out["ok"] = bool(out["n_files"])
    return out


# ── 预处理执行器 ──────────────────────────────────────────────────────
def _noop(*_a, **_kw) -> None:
    pass


def _read_hour(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    from netCDF4 import Dataset
    with Dataset(path) as ds:
        lat = np.asarray(ds.variables["lat"][:], dtype=np.float64)
        lon = np.asarray(ds.variables["lon"][:], dtype=np.float64)
        var = "rain_rate" if "rain_rate" in ds.variables else (
            "rain" if "rain" in ds.variables else None)
        if var is None:
            raise KeyError(f"{os.path.basename(path)} 中不存在 rain_rate/rain 变量")
        arr = np.asarray(ds.variables[var][:], dtype=np.float32)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr[arr < 0] = 0.0
    return arr, lat, lon


def _write_product(path: str, acc: np.ndarray, lat: np.ndarray, lon: np.ndarray,
                   prod: PrecipProduct, day: date) -> None:
    from netCDF4 import Dataset
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    start, end = prod.bounds_utc(day)
    with Dataset(tmp, "w", format="NETCDF4") as ds:
        ds.createDimension("lat", lat.size)
        ds.createDimension("lon", lon.size)
        vlat = ds.createVariable("lat", "f4", ("lat",))
        vlon = ds.createVariable("lon", "f4", ("lon",))
        vlat[:] = lat
        vlon[:] = lon
        var = ds.createVariable("rain", "f4", ("lat", "lon"),
                                zlib=True, complevel=4)
        var[:] = acc
        var.units = "mm"
        var.long_name = prod.label()
        ds.description = "GMCP accumulated rainfall product"
        ds.product = prod.key
        ds.duration_h = prod.duration_h
        ds.start_utc = start.strftime("%Y-%m-%d %H:%M")
        ds.end_utc = end.strftime("%Y-%m-%d %H:%M")
        ds.start_lst = (start + timedelta(hours=TZ_OFFSET_H)).strftime("%Y-%m-%d %H:%M")
        ds.end_lst = (end + timedelta(hours=TZ_OFFSET_H)).strftime("%Y-%m-%d %H:%M")
        ds.n_hours = prod.duration_h
    os.replace(tmp, path)


# 并行参数：以「日」为分发单位（同一天内多产品共享小时读取），主进程只做汇总
MAX_WORKERS = 8
_WORKER_MEMORY_BUDGET = 1_500_000_000  # 约 1.5 GB，按单日累加量估算并行度


def _default_workers(shape: Tuple[int, int], n_products: int) -> int:
    """按 CPU 核数与单日内存占用确定默认并行进程数。"""
    cpu = os.cpu_count() or 2
    per_day = max(1, int(shape[0]) * int(shape[1]) * 4 * max(1, n_products))
    cap = max(1, int(_WORKER_MEMORY_BUDGET // (per_day * 2)))  # 累加器 + 单小时场
    return max(1, min(cpu, MAX_WORKERS, cap))


def _process_day(task: Tuple) -> Dict:
    """进程池任务：累加并写出某一天的所有待生成产品。

    必须在模块级定义（Windows spawn 要求可 pickle）；只返回统计与少量错误样例，
    避免把大数组跨进程搬运。
    """
    source_root, output_root, day_iso, keys, shape = task
    day = date.fromisoformat(day_iso)
    stats: Dict = {"day": day_iso, "written": 0, "incomplete": 0, "errors": 0,
                   "error_samples": []}

    # 小时 -> 需要该小时的产品（跨日窗口也在此自然展开）
    inverse: Dict[datetime, List[str]] = {}
    for key in keys:
        for h in PRECIP_PRODUCTS[key].hours_utc(day):
            inverse.setdefault(h, []).append(key)
    acc = {k: np.zeros(shape, dtype=np.float32) for k in keys}
    missing: Dict[str, List[str]] = {k: [] for k in keys}
    lat = lon = None

    for h in sorted(inverse):
        path = hourly_path(source_root, h)
        if not os.path.isfile(path):
            for k in inverse[h]:
                missing[k].append(h.strftime("%Y%m%d%H"))
            continue
        try:
            arr, hlat, hlon = _read_hour(path)
        except Exception as exc:
            for k in inverse[h]:
                missing[k].append(h.strftime("%Y%m%d%H"))
            if len(stats["error_samples"]) < 8:
                stats["error_samples"].append(
                    f"{os.path.basename(path)}: {type(exc).__name__}: {exc}")
            continue
        if arr.shape != shape:
            for k in inverse[h]:
                missing[k].append(h.strftime("%Y%m%d%H") + "(网格不一致)")
            continue
        if lat is None:
            lat, lon = hlat, hlon
        for k in inverse[h]:
            acc[k] += arr

    for key in keys:
        if missing[key]:
            stats["incomplete"] += 1
            if len(stats["error_samples"]) < 8:
                stats["error_samples"].append(
                    f"{key} {day} 缺 {len(missing[key])} 小时："
                    + ",".join(missing[key][:4]))
            continue
        if lat is None:
            stats["incomplete"] += 1
            continue
        try:
            _write_product(product_path(output_root, key, day), acc[key],
                           lat, lon, PRECIP_PRODUCTS[key], day)
            stats["written"] += 1
        except Exception as exc:
            stats["errors"] += 1
            if len(stats["error_samples"]) < 8:
                stats["error_samples"].append(
                    f"{key} {day} 写出失败: {type(exc).__name__}: {exc}")
    return stats


def run_preprocess(source_root: str, output_root: str, keys: Sequence[str],
                   year_from: int, year_to: int, overwrite: bool = False,
                   progress: Callable[[str, str, int, int, str], None] = _noop,
                   should_stop: Optional[Callable[[], bool]] = None,
                   workers: Optional[int] = None) -> Dict:
    """把 GMCP 逐小时降水累加为指定累计产品（按日并行）。

    同一天的所有产品共享一次小时读取（取窗口并集），避免同一小时文件被反复打开；
    不同日期之间没有共享，按「日」分发到进程池即可近似线性加速。
    Windows 冻结态依赖入口的 ``multiprocessing.freeze_support()``。
    """
    keys = [k for k in keys if k in PRECIP_PRODUCTS]
    if not keys:
        raise ValueError("未选择任何累计降水产品")
    source_root = os.path.abspath(source_root)
    output_root = os.path.abspath(output_root)
    if not os.path.isdir(source_root):
        raise ValueError(f"源目录不存在：{source_root}")

    days = list(dates_between(year_from, year_to))
    total = len(days) * len(keys)
    done = 0
    written = skipped = incomplete = errors = 0
    error_samples: List[str] = []
    axes = None
    progress("scan", "扫描源数据网格", 0, 1, "定位首个可用小时文件")
    sample = _first_hourly(source_root, year_from, year_to)
    if sample:
        try:
            _, lat, lon = _read_hour(sample)
            axes = (lat, lon)
        except Exception as exc:
            error_samples.append(f"{os.path.basename(sample)}: {exc}")
    if axes is None:
        raise ValueError("源目录中没有可读的 GMCP 小时文件")
    lat, lon = axes
    shape = (int(lat.size), int(lon.size))

    n_workers = int(workers) if workers else _default_workers(shape, len(keys))
    n_workers = max(1, min(n_workers, os.cpu_count() or 1, MAX_WORKERS))

    # 先在主进程完成「已存在则跳过」的判定，再把待处理日期分发给进程池
    jobs: List[Tuple] = []
    for day in days:
        if should_stop and should_stop():
            raise PreprocessCancelled("预处理已取消")
        pending = []
        for key in keys:
            if os.path.isfile(product_path(output_root, key, day)) and not overwrite:
                skipped += 1
                done += 1
            else:
                pending.append(key)
        if pending:
            jobs.append((source_root, output_root, day.isoformat(),
                         tuple(pending), shape))
    n_workers = max(1, min(n_workers, len(jobs))) if jobs else 1

    progress("run", "并行累加并写出累计降水产品", done, total,
             f"{len(days)} 天 × {len(keys)} 个产品 · {n_workers} 个并行进程"
             + (f" · 跳过 {skipped} 个已存在产品" if skipped else ""))

    def absorb(st: Dict) -> None:
        nonlocal written, incomplete, errors
        written += int(st.get("written", 0))
        incomplete += int(st.get("incomplete", 0))
        errors += int(st.get("errors", 0))
        for item in st.get("error_samples") or []:
            if len(error_samples) < 30:
                error_samples.append(item)

    if jobs and n_workers > 1:
        ex = ProcessPoolExecutor(max_workers=n_workers)
        cancelled = False
        try:
            futures = {ex.submit(_process_day, job): job for job in jobs}
            while futures:
                if should_stop and should_stop():
                    cancelled = True
                    for f in futures:
                        f.cancel()
                    raise PreprocessCancelled("预处理已取消")
                finished, _ = wait(futures, timeout=0.5,
                                   return_when=FIRST_COMPLETED)
                for f in finished:
                    job = futures.pop(f)
                    try:
                        st = f.result()
                    except Exception as exc:  # 单个日期失败不影响整批
                        errors += len(job[3])
                        if len(error_samples) < 30:
                            error_samples.append(
                                f"{job[2]} 处理失败: {type(exc).__name__}: {exc}")
                        st = None
                    if st is not None:
                        absorb(st)
                    done += len(job[3])
                    progress("run", "并行累加并写出累计降水产品", done, total,
                             f"{job[2]} · 已写 {written} · 跳过 {skipped}"
                             f" · 并行 {n_workers}")
        finally:
            ex.shutdown(wait=not cancelled, cancel_futures=True)
    else:
        for job in jobs:
            if should_stop and should_stop():
                raise PreprocessCancelled("预处理已取消")
            absorb(_process_day(job))
            done += len(job[3])
            progress("run", "并行累加并写出累计降水产品", done, total,
                     f"{job[2]} · 已写 {written} · 跳过 {skipped}")

    progress("done", "预处理完成", total, total,
             f"写出 {written} · 跳过 {skipped} · 缺测 {incomplete}"
             f" · 并行 {n_workers}")
    return {
        "source_root": source_root,
        "output_root": output_root,
        "products": list(keys),
        "year_from": int(year_from), "year_to": int(year_to),
        "n_days": len(days),
        "n_expected": total,
        "n_written": written,
        "n_skipped": skipped,
        "n_incomplete": incomplete,
        "n_errors": errors,
        "error_samples": error_samples,
        "workers": n_workers,
        "axes": {"n_lat": int(lat.size), "n_lon": int(lon.size)},
    }


# ── 自检 ──────────────────────────────────────────────────────────────
def _self_check() -> None:
    # 1) 需求中的 UTC 时次区间逐条对齐
    d = date(2000, 6, 1)
    h = PRECIP_PRODUCTS["24h_2020"].hours_utc(d)
    assert len(h) == 24 and h[0] == datetime(2000, 5, 31, 13) \
        and h[-1] == datetime(2000, 6, 1, 12), (h[0], h[-1])
    h = PRECIP_PRODUCTS["24h_0808"].hours_utc(d)
    assert len(h) == 24 and h[0] == datetime(2000, 6, 1, 1) \
        and h[-1] == datetime(2000, 6, 2, 0), (h[0], h[-1])
    h = PRECIP_PRODUCTS["12h_08"].hours_utc(d)
    assert len(h) == 12 and h[0] == datetime(2000, 5, 31, 13) \
        and h[-1] == datetime(2000, 6, 1, 0), (h[0], h[-1])
    h = PRECIP_PRODUCTS["12h_20"].hours_utc(d)
    assert len(h) == 12 and h[0] == datetime(2000, 6, 1, 1) \
        and h[-1] == datetime(2000, 6, 1, 12), (h[0], h[-1])
    for key, first, last in (
            ("6h_02", datetime(2000, 5, 31, 13), datetime(2000, 5, 31, 18)),
            ("6h_08", datetime(2000, 5, 31, 19), datetime(2000, 6, 1, 0)),
            ("6h_14", datetime(2000, 6, 1, 1), datetime(2000, 6, 1, 6)),
            ("6h_20", datetime(2000, 6, 1, 7), datetime(2000, 6, 1, 12)),
    ):
        h = PRECIP_PRODUCTS[key].hours_utc(d)
        assert len(h) == 6 and h[0] == first and h[-1] == last, (key, h[0], h[-1])
    # 2) 命名
    assert PRECIP_PRODUCTS["24h_2020"].filename(d) == \
        "GMCP_24h_Rain_20000601_20_20LST.nc"
    assert PRECIP_PRODUCTS["24h_0808"].filename(d) == \
        "GMCP_24h_Rain_20000601_08_08LST.nc"
    assert PRECIP_PRODUCTS["12h_08"].filename(d) == \
        "GMCP_12h_Rain_20000601_08LST.nc"
    assert PRECIP_PRODUCTS["6h_02"].filename(d) == \
        "GMCP_6h_Rain_20000601_02LST.nc"
    for key, prod in PRECIP_PRODUCTS.items():
        day = date(2001, 3, 4)
        assert match_filename(prod.filename(day)) == (key, day), key

    # 3) 小网格端到端：48 小时全为 1mm → 各窗口累加值等于窗口小时数
    import tempfile
    from netCDF4 import Dataset
    tmp = tempfile.mkdtemp(prefix="histsim_precip_")
    src = os.path.join(tmp, "hourly")
    out = os.path.join(tmp, "products")
    lat = np.array([30.0, 29.9], dtype=np.float32)
    lon = np.array([110.0, 110.1], dtype=np.float32)
    # 覆盖 2000-05-30 12UTC ~ 2000-06-02 12UTC
    start = datetime(2000, 5, 30, 12)
    for i in range(73):
        t = start + timedelta(hours=i)
        p = hourly_path(src, t)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with Dataset(p, "w", format="NETCDF4") as ds:
            ds.createDimension("lat", lat.size)
            ds.createDimension("lon", lon.size)
            ds.createVariable("lat", "f4", ("lat",))[:] = lat
            ds.createVariable("lon", "f4", ("lon",))[:] = lon
            v = ds.createVariable("rain_rate", "f4", ("lat", "lon"))
            v[:] = np.ones((lat.size, lon.size), dtype=np.float32)
    rep = run_preprocess(src, out, ["24h_2020", "24h_0808", "12h_08",
                                    "12h_20", "6h_02", "6h_08", "6h_14", "6h_20"],
                         2000, 2000)
    # 源数据只覆盖 5/30 12UTC ~ 6/2 12UTC，6/1 的 8 个窗口必然齐备
    assert rep["n_written"] >= 8, rep
    for key, prod in PRECIP_PRODUCTS.items():
        p = product_path(out, key, d)
        assert os.path.isfile(p), p
        with Dataset(p) as ds:
            val = float(ds.variables["rain"][0, 0])
            assert abs(val - prod.duration_h) < 1e-4, (key, val)
            assert ds.product == key
    # 4) 再次运行应跳过已有文件
    rep2 = run_preprocess(src, out, ["24h_2020"], 2000, 2000)
    assert rep2["n_written"] == 0 and rep2["n_skipped"] >= 1, rep2
    print("precip self-check OK ->", tmp)


if __name__ == "__main__":
    _self_check()
