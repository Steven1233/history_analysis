# -*- coding: utf-8 -*-
"""数据源登记、覆盖探测与元数据库（SQLite）。

设计取舍（对应方案 F1「不搬移数据、不预处理」）：
* 覆盖信息按 ``要素 × 年 × 月`` **聚合**存储，而不是逐文件建索引——
  逐文件登记 GMCP 全年目录要 20 万行，聚合后只有几百行；
* 定位具体文件时不查库，按命名规则**构造路径 + 判存在**，
  既省索引又天然支持「同要素多目录按日期自动合并」。
"""
from __future__ import annotations

import calendar
import functools
import os
import re
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, Iterator, List, Optional

from .config import ELEMENTS, SearchConfig, split_key, var_key
from .precip import PRECIP_PRODUCTS, match_filename, product_path

ERA5_RE = re.compile(r"^era5\.([a-z_]+)\.(\d{8})\.nc$", re.I)
GMCP_RE = re.compile(r"^GMCP_(\d{4})_(\d{2})_(\d{2})_(\d{2})\.nc$", re.I)

# ERA5 文件 element 串 -> 要素 key
ELEMENT_BY_FILE = {v[0]: k for k, v in ELEMENTS.items()}

SCHEMA = """
CREATE TABLE IF NOT EXISTS data_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- era5 | gmcp
    element TEXT NOT NULL,           -- u/v/t/q/rh | rain
    root TEXT NOT NULL,
    deeplink TEXT,                   -- era5 文件名模板 / gmcp 年目录模板
    note TEXT,
    registered_at TEXT NOT NULL,
    UNIQUE(kind, element, root)
);
CREATE TABLE IF NOT EXISTS data_coverage (
    source_id INTEGER NOT NULL,
    element TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    n_files INTEGER NOT NULL,
    PRIMARY KEY (source_id, element, year, month)
);
CREATE TABLE IF NOT EXISTS search_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    target_date TEXT,
    config_name TEXT,
    config_fingerprint TEXT,
    config_json TEXT,
    status TEXT,
    elapsed_s REAL,
    result_dir TEXT,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS operation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    op TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS quality_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source_id INTEGER,
    path TEXT,
    kind TEXT,
    detail TEXT
);
"""


def default_data_dir() -> str:
    """程序数据目录：默认 <项目根>/.histsim，可用环境变量 HISTSIM_HOME 覆盖。

    打包成 exe 后「项目根」落在安装目录下，普通用户无权写入，因此冻结态改用
    ``%LOCALAPPDATA%\\Histsim``；HISTSIM_HOME 对两种形态都优先。
    """
    env = os.environ.get("HISTSIM_HOME")
    if env:
        return env
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Histsim")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, ".histsim")


def _expected_dates(cfg: SearchConfig) -> Iterator[date]:
    """按配置枚举候选日期（不含「文件是否存在」判断）。

    * ``calendar``：年 × 月 × 当月每天（现有行为，含目标年）；
    * ``same_period``：历史同期——以目标日「月/日」为锚点，各年份取
      ``anchor ± period_days`` 天，**跳过目标年**、**忽略 months**；
      ``2/29`` 在非闰年落到 ``2/28``。
    """
    if cfg.sample_mode == "same_period" and cfg.target_date:
        tgt = datetime.strptime(cfg.target_date, "%Y-%m-%d").date()
        for year in range(cfg.year_from, cfg.year_to + 1):
            if year == tgt.year:          # 历史同期：目标年自身不作为历史候选
                continue
            try:
                anchor = tgt.replace(year=year)
            except ValueError:            # 2/29 → 2/28
                anchor = date(year, 2, 28)
            for off in range(-cfg.period_days, cfg.period_days + 1):
                yield anchor + timedelta(days=off)
        return
    for year in range(cfg.year_from, cfg.year_to + 1):
        for month in sorted(set(cfg.months)):
            if not 1 <= month <= 12:
                continue
            for d in range(1, calendar.monthrange(year, month)[1] + 1):
                yield date(year, month, d)


def _locked(method):
    """串行化对共享 sqlite 连接的访问（见 ``Store.__init__`` 的说明）。

    可重入：被装饰的方法可能互相调用（如 ``coverage_grid`` → ``coverage_of``）。
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Store:
    """SQLite 元数据库 + 数据源登记/定位。"""

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or default_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.results_dir = os.path.join(self.data_dir, "results")
        os.makedirs(self.results_dir, exist_ok=True)
        # 切片缓存：把「一次打开 NC 只为几十个格点」的开销摊到多次检索上
        self.cache_dir = os.path.join(self.data_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.db_path = os.path.join(self.data_dir, "index.db")
        # Web 后端是 ThreadingHTTPServer：界面一次加载就会并发打 /api/sources、
        # /api/dashboard、/api/tasks 等，而全部请求共用这一个连接。
        # check_same_thread=False 只是关掉了 Python 的线程检查，并不能让连接变
        # 线程安全；并发使用同一个游标会互相踩踏，随机抛出 sqlite3.InterfaceError、
        # 行对象取值为 None（如 root=None → os.path.isdir 报 TypeError）、
        # IndexError 等。所有连接访问一律经 self._lock 串行化。
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @_locked
    def close(self) -> None:
        self.conn.close()

    # ── 基础 ──────────────────────────────────────────────────────────
    @_locked
    def _exec(self, sql: str, args: Iterable = ()):
        cur = self.conn.execute(sql, tuple(args))
        self.conn.commit()
        return cur

    @_locked
    def log(self, op: str, target: str = "", detail: str = "", result: str = "") -> None:
        self._exec(
            "INSERT INTO operation_log(ts, op, target, detail, result) VALUES(?,?,?,?,?)",
            [datetime.now().isoformat(timespec="seconds"), op, target, detail, result],
        )

    @_locked
    def operations(self, limit: int = 200) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM operation_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── 数据源登记 ────────────────────────────────────────────────────
    @_locked
    def registered_sources(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM data_sources ORDER BY kind, element, id"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            cov = self.conn.execute(
                "SELECT year, month, n_files FROM data_coverage WHERE source_id=?",
                (r["id"],),
            ).fetchall()
            d["coverage"] = [dict(c) for c in cov]
            d["status"] = "有效" if os.path.isdir(r["root"]) else "路径不可用"
            out.append(d)
        return out

    @_locked
    def add_source(self, kind: str, element: str, root: str, note: str = "",
                   coverage: Optional[Dict] = None) -> int:
        """登记或更新一个数据源，并写入覆盖聚合。coverage: {year: {month: n}}"""
        cur = self._exec(
            """INSERT INTO data_sources(kind, element, root, note, registered_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(kind, element, root)
               DO UPDATE SET note=excluded.note, registered_at=excluded.registered_at""",
            [kind, element, root, note, datetime.now().isoformat(timespec="seconds")],
        )
        row = self.conn.execute(
            "SELECT id FROM data_sources WHERE kind=? AND element=? AND root=?",
            (kind, element, root),
        ).fetchone()
        sid = int(row["id"])
        self._exec("DELETE FROM data_coverage WHERE source_id=?", [sid])
        if coverage:
            self.conn.executemany(
                "INSERT OR REPLACE INTO data_coverage(source_id,element,year,month,n_files)"
                " VALUES(?,?,?,?,?)",
                [(sid, element, y, m, n) for y, ms in coverage.items() for m, n in ms.items()],
            )
            self.conn.commit()
        return sid

    @_locked
    def remove_source(self, source_id: int) -> None:
        self._exec("DELETE FROM data_coverage WHERE source_id=?", [source_id])
        self._exec("DELETE FROM data_sources WHERE id=?", [source_id])

    # ── 文件定位（构造路径 + 判存在）──────────────────────────────────

    @_locked
    def era5_roots(self, element: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT root FROM data_sources WHERE kind='era5' AND element=? ORDER BY id",
            (element,),
        ).fetchall()
        return [r["root"] for r in rows]

    @_locked
    def gmcp_roots(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT root FROM data_sources WHERE kind='gmcp' ORDER BY id"
        ).fetchall()
        return [r["root"] for r in rows]

    @_locked
    def precip_roots(self, product_key: str) -> List[str]:
        """累计降水产品的登记目录（kind='gmcp_rain'，element=产品 key）。"""
        rows = self.conn.execute(
            "SELECT root FROM data_sources WHERE kind='gmcp_rain' AND element=? "
            "ORDER BY id", (product_key,)
        ).fetchall()
        return [r["root"] for r in rows]

    def find_precip(self, product_key: str, day: date) -> Optional[str]:
        if product_key not in PRECIP_PRODUCTS:
            return None
        name = PRECIP_PRODUCTS[product_key].filename(day)
        for root in self.precip_roots(product_key):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                return p
        return None

    def find_era5(self, element: str, day: date) -> Optional[str]:
        """同要素可登记多个目录，按登记顺序取第一个命中的。"""
        name = f"era5.{ELEMENTS[element][0]}.{day:%Y%m%d}.nc"
        for root in self.era5_roots(element):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                return p
        return None

    def find_gmcp(self, dt: datetime) -> Optional[str]:
        rel = os.path.join(f"{dt.year:04d}", f"GMCP_{dt:%Y_%m_%d_%H}.nc")
        for root in self.gmcp_roots():
            p = os.path.join(root, rel)
            if os.path.isfile(p):
                return p
        return None

    # ── 覆盖统计与就绪检查 ────────────────────────────────────────────

    @_locked
    def coverage_of(self, element: str) -> Dict[int, Dict[int, int]]:
        rows = self.conn.execute(
            """SELECT c.year, c.month, SUM(c.n_files) AS n
               FROM data_coverage c JOIN data_sources s ON s.id=c.source_id
               WHERE c.element=? GROUP BY c.year, c.month""",
            (element,),
        ).fetchall()
        out: Dict[int, Dict[int, int]] = {}
        for r in rows:
            out.setdefault(int(r["year"]), {})[int(r["month"])] = int(r["n"])
        return out

    def coverage_grid(self) -> Dict:
        """看板用：要素 -> {year: {month: {'n':可用文件数, 'pct':占该月天数比例}}}"""
        grid = {}
        for element in list(ELEMENTS) + list(PRECIP_PRODUCTS):
            cov = self.coverage_of(element)
            if not cov:
                continue
            grid[element] = {
                y: {
                    m: {
                        "n": n,
                        "pct": round(min(1.0, n / calendar.monthrange(y, m)[1]), 3),
                    }
                    for m, n in sorted(ms.items())
                }
                for y, ms in sorted(cov.items())
            }
        return grid

    def available_precip_dates(self, cfg: SearchConfig,
                               keys: Optional[List[str]] = None) -> List[date]:
        """列出所选累计降水产品全部齐备的日期（取样方式与 ERA5 一致）。"""
        keys = [k for k in (keys if keys is not None else cfg.precip_windows)
                if k in PRECIP_PRODUCTS]
        if not keys:
            return []
        out = []
        for day in _expected_dates(cfg):
            if all(self.find_precip(k, day) for k in keys):
                out.append(day)
        return out

    def available_dates(self, cfg: SearchConfig) -> List[date]:
        """列出配置所需全部要素都齐备的日期。"""
        need = cfg.elements()
        out = set()
        for day in _expected_dates(cfg):
            if all(self.find_era5(e, day) for e in need):
                out.add(day)
        return sorted(out)

    def readiness(self, cfg: SearchConfig) -> Dict:
        """模板就绪检查：给出缺口清单（方案 F1.4）。

        同期模式的候选集是「目标日 ± x 天 × 各年份」，不是整月，按整月算会误报
        覆盖度，因此逐日判存在后按 (要素, 年, 月) 聚合；日历模式候选集等于整月，
        继续走覆盖聚合表（``coverage_of``），避免在慢盘上逐日探测上万次。
        """
        gaps: List[Dict] = []
        total = avail = 0
        if cfg.sample_mode == "same_period":
            buckets: Dict[tuple, List[int]] = {}
            for element in cfg.elements():
                for day in _expected_dates(cfg):
                    b = buckets.setdefault((element, day.year, day.month), [0, 0])
                    b[0] += 1
                    if self.find_era5(element, day):
                        b[1] += 1
            for (element, year, month), (expect, have) in sorted(buckets.items()):
                total += expect
                avail += have
                if have < expect:
                    gaps.append({"element": element, "year": year, "month": month,
                                 "have": have, "expect": expect})
        else:
            for element in cfg.elements():
                cov = self.coverage_of(element)
                for year in range(cfg.year_from, cfg.year_to + 1):
                    for month in sorted(set(cfg.months)):
                        if not 1 <= month <= 12:
                            continue
                        expect = calendar.monthrange(year, month)[1]
                        have = cov.get(year, {}).get(month, 0)
                        total += expect
                        avail += min(expect, have)
                        if have < expect:
                            gaps.append({
                                "element": element,
                                "year": year,
                                "month": month,
                                "have": have,
                                "expect": expect,
                            })
        # ── GMCP 累计降水产品（kind=gmcp_rain）──────────────────────────
        precip_keys = ([k for k in cfg.precip_windows if k in PRECIP_PRODUCTS]
                       if getattr(cfg, "precip_active", False) else [])
        if precip_keys and cfg.sample_mode == "same_period":
            buckets: Dict[tuple, List[int]] = {}
            for key in precip_keys:
                for day in _expected_dates(cfg):
                    b = buckets.setdefault((key, day.year, day.month), [0, 0])
                    b[0] += 1
                    if self.find_precip(key, day):
                        b[1] += 1
            for (key, year, month), (expect, have) in sorted(buckets.items()):
                total += expect
                avail += have
                if have < expect:
                    gaps.append({"element": key, "year": year, "month": month,
                                 "have": have, "expect": expect})
        elif precip_keys:
            for key in precip_keys:
                cov = self.coverage_of(key)
                for year in range(cfg.year_from, cfg.year_to + 1):
                    for month in sorted(set(cfg.months)):
                        if not 1 <= month <= 12:
                            continue
                        expect = calendar.monthrange(year, month)[1]
                        have = cov.get(year, {}).get(month, 0)
                        total += expect
                        avail += min(expect, have)
                        if have < expect:
                            gaps.append({"element": key, "year": year, "month": month,
                                         "have": have, "expect": expect})
        ratio = round(avail / total, 3) if total else 0.0
        return {
            "ready": not gaps,
            "coverage_pct": round(ratio * 100, 1),
            "gaps": gaps,
            "note": "数据就绪" if not gaps else f"存在 {len(gaps)} 个要素·年月缺口",
        }

    # ── 检索任务记录 ──────────────────────────────────────────────────

    @_locked
    def record_task(self, cfg: SearchConfig, status: str, elapsed: float,
                    result_dir: str, summary: str) -> int:
        cur = self._exec(
            """INSERT INTO search_tasks(created_at,target_date,config_name,
               config_fingerprint,config_json,status,elapsed_s,result_dir,summary)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            [datetime.now().isoformat(timespec="seconds"), cfg.target_date, cfg.name,
             cfg.fingerprint(), _dumps(cfg.to_dict()), status, round(elapsed, 2),
             result_dir, summary],
        )
        return int(cur.lastrowid)

    @_locked
    def tasks(self, limit: int = 100) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT id,created_at,target_date,config_name,config_fingerprint,status,"
            "elapsed_s,result_dir,summary FROM search_tasks ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 结果目录名即任务 ID，界面据此回看已落盘的历史结果
            d["task_id"] = os.path.basename(d["result_dir"] or "")
            out.append(d)
        # task_id 前缀是 YYYYMMDD_HHMMSS（任务发起时刻），字典序即时间序。
        # 统一按任务时间倒序（最新在前），与界面下拉的排序口径一致；行按 id DESC
        # 取出的顺序会随任务完成先后漂移，这里以发起时刻为准更稳定。
        out.sort(key=lambda d: d["task_id"], reverse=True)
        return out


def _dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


# ── 目录探测 ──────────────────────────────────────────────────────────
def probe_directory(path: str, sample_read: bool = True) -> Dict:
    """探测一个目录是 ERA5 逐日目录还是 GMCP 逐年目录，并统计覆盖。"""
    path = os.path.abspath(path)
    res: Dict = {"root": path, "ok": False, "kind": None, "elements": [],
                 "coverage": {}, "warnings": [], "sample": None}
    if not os.path.isdir(path):
        res["warnings"].append("路径不存在或不是目录")
        return res

    try:
        entries = list(os.scandir(path))
    except OSError as exc:
        res["warnings"].append(f"目录不可读: {exc}")
        return res
    names = [e.name for e in entries if e.is_file()]
    subdirs = [e.name for e in entries if e.is_dir()]

    era5_files: Dict[str, List[str]] = {}
    for n in names:
        m = ERA5_RE.match(n)
        if m:
            era5_files.setdefault(m.group(1), []).append(n)

    if era5_files:
        res["kind"] = "era5"
        for elem_file, files in sorted(era5_files.items()):
            key = ELEMENT_BY_FILE.get(elem_file)
            if key is None:
                res["warnings"].append(f"未知 ERA5 要素 {elem_file}，已跳过")
                continue
            cov: Dict[int, Dict[int, int]] = {}
            for f in files:
                ds = ERA5_RE.match(f).group(2)
                cov.setdefault(int(ds[:4]), {})[int(ds[4:6])] = \
                    cov.setdefault(int(ds[:4]), {}).get(int(ds[4:6]), 0) + 1
            res["elements"].append({"key": key, "file_element": elem_file,
                                    "n_files": len(files)})
            res["coverage"][key] = cov
        res["ok"] = bool(res["elements"])
        if sample_read and res["elements"]:
            res["sample"] = _verify_era5(path, era5_files[res["elements"][0]["file_element"]][0])
        return res

    # GMCP 累计降水预处理产物：GMCP_24h_Rain_*.nc / GMCP_12h_Rain_*.nc / GMCP_6h_Rain_*.nc
    precip_files: Dict[str, List[str]] = {}
    for n in names:
        hit = match_filename(n)
        if hit:
            precip_files.setdefault(hit[0], []).append(n)
    if precip_files:
        res["kind"] = "gmcp_rain"
        for key, files in sorted(precip_files.items()):
            cov: Dict[int, Dict[int, int]] = {}
            for f in files:
                hit = match_filename(f)
                if not hit:
                    continue
                d = hit[1]
                cov.setdefault(d.year, {})[d.month] = \
                    cov.setdefault(d.year, {}).get(d.month, 0) + 1
            res["elements"].append({"key": key, "file_element": "rain",
                                    "n_files": len(files)})
            res["coverage"][key] = cov
        res["ok"] = bool(res["elements"])
        if sample_read and res["elements"]:
            first = res["elements"][0]
            res["sample"] = _verify_precip(
                os.path.join(path, sorted(precip_files[first["key"]])[0]))
        return res

    # GMCP 逐小时源数据：<root>/<yyyy>/GMCP_*.nc
    cov_g: Dict[int, Dict[int, int]] = {}
    n_files = 0
    for sub in sorted(subdirs):
        subp = os.path.join(path, sub)
        if not (len(sub) == 4 and sub.isdigit() and os.path.isdir(subp)):
            continue
        try:
            for n in os.listdir(subp):
                m = GMCP_RE.match(n)
                if m and m.group(1) == sub:
                    n_files += 1
                    cov_g.setdefault(int(m.group(1)), {})[int(m.group(2))] = \
                        cov_g.setdefault(int(m.group(1)), {}).get(int(m.group(2)), 0) + 1
        except OSError:
            res["warnings"].append(f"{sub} 年目录不可读")
    if n_files:
        res["kind"] = "gmcp"
        res["ok"] = True
        res["elements"].append({"key": "rain", "file_element": "rain_rate",
                                "n_files": n_files})
        res["coverage"]["rain"] = cov_g
        if sample_read:
            first_year = sorted(cov_g)[0]
            cand = os.path.join(path, f"{first_year:04d}")
            f0 = next((n for n in os.listdir(cand) if GMCP_RE.match(n)), None)
            if f0:
                res["sample"] = _verify_gmcp(os.path.join(cand, f0))
        return res

    res["warnings"].append("目录内既未发现 era5.<element>.<yyyymmdd>.nc，"
                           "也未发现 <yyyy>/GMCP_<yyyy>_<mm>_<dd>_<hh>.nc")
    return res


def _verify_era5(root: str, fname: str) -> Dict:
    """抽样打开一个文件，核对内部结构与变量名。

    同一要素目录内可能混用 valid_time/pressure_level 与 time/level 两套命名，
    因此坐标轴按候选名逐个匹配。
    """
    from netCDF4 import Dataset
    p = os.path.join(root, fname)
    try:
        with Dataset(p) as ds:
            m = ERA5_RE.match(fname)
            elem_file = m.group(1)
            key = ELEMENT_BY_FILE.get(elem_file)
            short = ELEMENTS[key][1] if key else None
            names = set(ds.variables)

            def pick(cands):
                return next((c for c in cands if c in names), None)

            tname = pick(("valid_time", "time"))
            lname = pick(("pressure_level", "level", "isobaricInhPa", "plev"))
            latname = pick(("latitude", "lat"))
            lonname = pick(("longitude", "lon"))
            dims = {k: v.size for k, v in ds.dimensions.items()}
            return {
                "file": fname, "var": short,
                "var_present": short in names if short else False,
                "axes": {"time": tname, "level": lname,
                         "lat": latname, "lon": lonname},
                "time_units": getattr(ds.variables[tname], "units", None) if tname else None,
                "dims": dims,
                "levels": [round(float(l), 1) for l in ds.variables[lname][:]] if lname else [],
                "lat_range": [float(ds.variables[latname][:].min()),
                              float(ds.variables[latname][:].max())] if latname else None,
                "lon_range": [float(ds.variables[lonname][:].min()),
                              float(ds.variables[lonname][:].max())] if lonname else None,
            }
    except Exception as exc:  # 坏文件不应让探测整体失败
        return {"file": fname, "error": f"{type(exc).__name__}: {exc}"}


def _verify_gmcp(p: str) -> Dict:
    from netCDF4 import Dataset
    try:
        with Dataset(p) as ds:
            return {
                "file": os.path.basename(p),
                "var": "rain_rate",
                "var_present": "rain_rate" in ds.variables,
                "dims": {k: v.size for k, v in ds.dimensions.items()},
                "lat_range": [float(ds.variables["lat"][:].min()),
                              float(ds.variables["lat"][:].max())],
                "lon_range": [float(ds.variables["lon"][:].min()),
                              float(ds.variables["lon"][:].max())],
                "description": getattr(ds, "description", ""),
            }
    except Exception as exc:
        return {"file": os.path.basename(p), "error": f"{type(exc).__name__}: {exc}"}


def _verify_precip(p: str) -> Dict:
    """抽样打开一个累计降水产品，核对变量与网格。"""
    from netCDF4 import Dataset
    try:
        with Dataset(p) as ds:
            var = "rain" if "rain" in ds.variables else (
                "rain_rate" if "rain_rate" in ds.variables else None)
            out = {
                "file": os.path.basename(p),
                "var": var,
                "var_present": var is not None,
                "dims": {k: v.size for k, v in ds.dimensions.items()},
                "description": getattr(ds, "description", ""),
                "product": getattr(ds, "product", ""),
                "duration_h": int(getattr(ds, "duration_h", 0) or 0),
            }
            for name in ("lat", "lon"):
                if name in ds.variables:
                    vals = ds.variables[name][:]
                    out[f"{name}_range"] = [float(vals.min()), float(vals.max())]
            return out
    except Exception as exc:
        return {"file": os.path.basename(p), "error": f"{type(exc).__name__}: {exc}"}


# ── 面向引擎的辅助 ────────────────────────────────────────────────────
def discover_and_register(store: "Store", root: str, depth: int = 1) -> Dict:
    """扫描根目录（及其下一层子目录），自动识别并登记 ERA5 要素目录 / GMCP 目录。

    一个目录里可以同时含多个要素（如 u、v 合放），会按要素分别登记；
    同一要素多目录并存时，检索按登记顺序取第一个命中的文件。
    """
    root = os.path.abspath(root)
    report: Dict = {"root": root, "registered": [], "skipped": [], "errors": []}
    if not os.path.isdir(root):
        report["errors"].append("路径不存在或不是目录")
        return report

    candidates = [root]
    if depth >= 1:
        try:
            candidates += [os.path.join(root, d) for d in sorted(os.listdir(root))
                           if os.path.isdir(os.path.join(root, d))]
        except OSError as exc:
            report["errors"].append(f"无法列出子目录: {exc}")

    for path in candidates:
        if not os.path.isdir(path):
            continue
        try:
            info = probe_directory(path, sample_read=False)
        except Exception as exc:  # 单个目录探测失败不影响整轮扫描
            report["errors"].append(f"{path}: {type(exc).__name__}: {exc}")
            continue
        if not info["ok"]:
            report["skipped"].append({"path": path,
                                      "reason": info["warnings"][:1] or ["未识别"]})
            continue
        note = f"自动扫描登记（{info['kind']}）"
        for item in info["elements"]:
            key = item["key"]
            sid = store.add_source(info["kind"], key, path, note=note,
                                   coverage=info["coverage"].get(key, {}))
            report["registered"].append({
                "id": sid, "kind": info["kind"], "element": key,
                "root": path, "n_files": item["n_files"],
            })
    store.log("数据源扫描", root,
              f"登记 {len(report['registered'])} 项 / 跳过 {len(report['skipped'])} 个目录",
              "成功" if report["registered"] else "无命中")
    return report


def window_offsets(base: date, hours: List[int]) -> List[datetime]:
    """把「日期 + 小时偏移列表」展开成真实时刻列表（可跨日）。"""
    return [datetime(base.year, base.month, base.day) + timedelta(hours=h) for h in hours]


def _self_check() -> None:
    import tempfile
    tmp = tempfile.mkdtemp(prefix="histsim_store_")
    st = Store(tmp)
    assert st.registered_sources() == []
    st.add_source("era5", "u", r"I:\u_winds_2000_2024",
                  coverage={2000: {4: 30, 5: 31}})
    assert st.era5_roots("u") == [r"I:\u_winds_2000_2024"]
    cov = st.coverage_of("u")
    assert cov[2000][4] == 30
    assert st.coverage_grid()["u"][2000][5]["pct"] == 1.0
    before = len(st.operations())
    st.log("自检", "u", "登记测试", "成功")
    assert len(st.operations()) == before + 1
    # 就绪检查应报出缺口（只有 2000 年 4-5 月）
    cfg = SearchConfig(year_from=2000, year_to=2000, months=[4, 5, 6],
                       variables=[["u", 925]], inter_group=["u925"],
                       direct_group=[])
    rd = st.readiness(cfg)
    assert not rd["ready"] and rd["gaps"], rd
    assert rd["coverage_pct"] < 100
    # ── V2.0.0：历史同期取样（same_period）──────────────────────────
    sp = SearchConfig(year_from=2000, year_to=2004, months=[4, 5, 6],
                      variables=[["u", 925]], inter_group=["u925"],
                      direct_group=[], sample_mode="same_period",
                      period_days=5, target_date="2002-06-15")
    days = list(_expected_dates(sp))
    assert len(days) == 4 * 11, len(days)        # 除目标年外的 4 年 × (2×5+1) 天
    assert all(d.year != 2002 for d in days)     # 跳过目标年
    assert min(days) == date(2000, 6, 10) and max(days) == date(2004, 6, 20)
    assert {d.month for d in days} == {6}        # 忽略 months 勾选
    # 2/29 在非闰年落到 2/28
    sp2 = sp.copy(year_from=2002, year_to=2003, target_date="2004-02-29")
    d2 = list(_expected_dates(sp2))
    assert len(d2) == 2 * 11 and date(2003, 2, 28) in d2, len(d2)
    # 日历模式口径不变：含目标年、按整月枚举
    cal = sp.copy(sample_mode="calendar", year_from=2000, year_to=2000)
    assert len(list(_expected_dates(cal))) == 30 + 31 + 30
    # 同期模式就绪检查按实际日期集合统计（不依赖本机是否真有数据，只看口径）
    av = st.available_dates(sp)
    assert all(d in set(days) for d in av)      # 只可能落在同期候选集内
    assert all(d.year != 2002 for d in av)
    rd2 = st.readiness(sp)
    assert set(rd2) >= {"ready", "coverage_pct", "gaps"}, rd2
    # 并发回归：Web 后端是 ThreadingHTTPServer，界面一次加载就并发打多个接口，
    # 全部请求共用这一个连接。此前未串行化会随机抛出 InterfaceError /
    # 行取值为 None（root=None → os.path.isdir 报 TypeError）/ IndexError。
    errors: List[str] = []

    def _hammer() -> None:
        try:
            for _ in range(60):
                st.registered_sources()
                st.coverage_grid()
                st.operations(50)
                st.log("并发自检", "u", "", "成功")
        except Exception as exc:  # noqa: BLE001 —— 自检要收集全部异常形态
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=_hammer) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"并发访问共享连接失败 {len(errors)} 次，例：{errors[:3]}"
    st.close()
    print("datasource self-check OK ->", tmp)


if __name__ == "__main__":
    _self_check()