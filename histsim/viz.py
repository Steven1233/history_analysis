# -*- coding: utf-8 -*-
"""可视化模块：要素场对比图、降水统计图、概率空间分布图。

底图规范（方案 F4.0）：所有产品图统一叠加内置省界 shapefile。
这里用纯标准库 ``struct`` 解析 `.shp`（避免引入 cartopy/shapely 依赖），
首次解析后缓存为 npz，绘图时按视窗范围裁剪。
"""
from __future__ import annotations

import glob
import math
import os
import struct
from typing import Dict, List, Optional, Sequence

import matplotlib
matplotlib.use("Agg")  # 离屏渲染，与 GUI/Web 主线程解耦
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm
from matplotlib.patches import Rectangle

from .config import ELEMENTS, SearchConfig, split_key, var_label, var_units

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DengXian", "Arial"]
plt.rcParams["axes.unicode_minus"] = False

PAPER = "#F5F3EE"      # 暖白纸面（数据底板）
INK = "#1A1D22"
ACCENT = "#C2703A"
GRID_C = "#8A9099"

# ── 产品图配色 ────────────────────────────────────────────────────────
# 要素场图：每个要素一种配色。可正负的分量用发散型（浅色居中），非负
# 物理量用顺序型；均取 ColorBrewer 色系，兼顾打印、投影与色觉友好。
ELEMENT_CMAP = {
    "u": "RdBu_r",      # U 风分量：蓝(负) ↔ 红(正)
    "v": "PRGn",        # V 风分量：紫(负) ↔ 绿(正)
    "t": "RdYlBu_r",    # 温度：蓝(冷) → 红(暖)
    "q": "YlGnBu",      # 比湿：黄 → 绿 → 蓝（顺序）
    "rh": "BuPu",       # 相对湿度：白 → 紫（顺序）
}
ELEMENT_CMAP_DEFAULT = "Blues"   # 兜底（要素表外的键）

# 概率空间图三块面板三种配色：平均场 / 出现概率 / 强降水概率
SPATIAL_CMAP_MEAN = "YlGnBu"          # 平均降水：青蓝（水汽）
SPATIAL_CMAP_PROB_LOW = "BuPu"        # 出现概率：紫
SPATIAL_CMAP_PROB_STRONG = "YlOrRd"   # 强降水概率：橙红（风险）

_SHAPE_TYPES = {1: "point", 3: "polyline", 5: "polygon"}

# ── 广东省行政底图（实测常量，见方案 V2.0.0 第 3 项）──────────────────
# 底图数据本身已具备：Province_9.shp 含广东省 347 个地市级面（SHENG=="44"），
# 之前看不到地市是因为视野只有 5°/8° 的小窗，把地市裁在窗外。
GD_PROVINCE_CODE = "44"                       # Province_9.dbf 的 SHENG 字段
GD_BBOX = [109.665, 117.310, 20.223, 25.520]  # SHENG=="44" 的并集 bbox
PROVINCE_SHP = "china_province2"              # 省级轮廓（34 条，字段 name/gb）
PROVINCE_NAME = "广东省"
# 地市界线与省界同色同宽就无从区分，故分三层：邻省淡化 / 地市界中灰 / 省界加粗
GD_VIEW_PAD = 0.4
# 圈选用的固定底图视野（GD_BBOX 外扩 0.6°）与像素尺寸
REGION_VIEW = [109.06, 117.91, 19.62, 26.12]
REGION_PX = (1000, 798)
_REGION_PNG: Optional[bytes] = None


# ── 极简 shapefile / dbf 解析 ─────────────────────────────────────────
def _decode_dbf(raw: bytes) -> str:
    """DBF 字段值解码：实测 Province_9.dbf / china_province2.dbf 均为 UTF-8。"""
    raw = raw.strip(b"\x00").strip()
    if not raw:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").strip()


def parse_dbf(path: str) -> List[Dict[str, str]]:
    """解析 .dbf 属性表（纯标准库），返回逐记录字典列表。"""
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        blob = fh.read()
    if len(blob) < 32:
        return []
    n_rec, header_len, rec_len = struct.unpack("<IHH", blob[4:12])
    if not rec_len:
        return []
    fields: List[tuple] = []
    off = 32
    while off + 32 <= min(header_len, len(blob)) and blob[off] != 0x0D:
        name = blob[off:off + 11].split(b"\x00")[0].decode("ascii", "replace")
        fields.append((name, blob[off + 16]))
        off += 32
    out: List[Dict[str, str]] = []
    pos = header_len
    for _ in range(n_rec):
        if pos + rec_len > len(blob):
            break
        chunk = blob[pos:pos + rec_len]
        pos += rec_len
        p = 1                       # 跳过 1 字节删除标志，否则字段整体错位
        rec: Dict[str, str] = {}
        for name, flen in fields:
            rec[name] = _decode_dbf(chunk[p:p + flen])
            p += flen
        out.append(rec)
    return out


def parse_shp(path: str, where=None) -> np.ndarray:
    """解析 .shp 中的线/面环，返回 ``(N, 2)`` float32 数组，环之间以 NaN 分隔。

    ``where`` 为可选属性过滤：按记录顺序与 ``.dbf`` 记录一一对应（含 null
    shape，必须同步计数），仅保留命中记录的环。
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    if len(blob) < 100:
        raise ValueError("shapefile 过短")
    file_code = struct.unpack(">i", blob[0:4])[0]
    if file_code != 9994:
        raise ValueError(f"不是 ESRI shapefile（file code={file_code}）")
    recs = parse_dbf(os.path.splitext(path)[0] + ".dbf") if where is not None else None
    segs: List[np.ndarray] = []
    pos, total, idx = 100, len(blob), -1
    while pos + 8 <= total:
        _rec, rec_len = struct.unpack(">ii", blob[pos:pos + 8])
        pos += 8
        end = pos + rec_len * 2
        if end > total:
            break
        idx += 1
        keep = True
        if recs is not None:
            rec = recs[idx] if idx < len(recs) else {}
            try:
                keep = bool(where(rec))
            except Exception:
                keep = False
        stype = struct.unpack("<i", blob[pos:pos + 4])[0]
        if keep and stype in (3, 5):
            n_parts, n_pts = struct.unpack("<ii", blob[pos + 36:pos + 44])
            if n_pts > 0:
                parts = struct.unpack(f"<{n_parts}i", blob[pos + 44:pos + 44 + 4 * n_parts])
                off = pos + 44 + 4 * n_parts
                pts = np.frombuffer(blob, dtype="<f8", count=n_pts * 2, offset=off)
                pts = pts.reshape(-1, 2).astype(np.float32)
                marks = list(parts) + [n_pts]
                for a, b in zip(marks[:-1], marks[1:]):
                    if b - a >= 2:
                        segs.append(pts[a:b])
        elif not keep:
            pass  # 属性过滤未命中
        elif stype == 0:
            pass  # null shape
        pos = end
    if not segs:
        return np.zeros((0, 2), dtype=np.float32)
    gap = np.full((1, 2), np.nan, dtype=np.float32)
    chunks: List[np.ndarray] = []
    for s in segs:
        chunks.append(s)
        chunks.append(gap)
    return np.concatenate(chunks, axis=0)


def load_basemap(shp_dir: Optional[str] = None, name: str = "Province_9",
                 cache_dir: Optional[str] = None, where=None,
                 tag: Optional[str] = None) -> np.ndarray:
    """加载省界线（带 npz 缓存）。默认取项目内 ``图层调用/shp``。

    ``where`` / ``tag`` 用于按属性表过滤（如广东省地市）并把结果缓存到带 tag 的
    npz；同时把该层 bbox 写进 npz，便于核对实际裁出的范围。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shp_dir = shp_dir or os.path.join(root, "图层调用", "shp")
    shp = os.path.join(shp_dir, name, f"{name}.shp")
    if not os.path.isfile(shp):
        # 兼容「同名 shp 放在子目录里」的图层组织方式（如
        # 图层调用/shp/china_pro/china_province2.shp），只下探一层。
        hits = glob.glob(os.path.join(shp_dir, "*", f"{name}.shp"))
        if not hits:
            raise FileNotFoundError(f"底图不存在: {shp}")
        shp = hits[0]
    # 缓存必须落在「程序数据目录」而不是项目根：打包成 exe 后 root 是安装目录，
    # 往那里写既污染安装目录、又会让卸载残留目录删不干净。
    if not cache_dir:
        from .datasource import default_data_dir
        cache_dir = os.path.join(default_data_dir(), "basemap")
    os.makedirs(cache_dir, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    npz = os.path.join(cache_dir, f"{name}{suffix}.npz")
    if os.path.isfile(npz) and os.path.getmtime(npz) >= os.path.getmtime(shp):
        return np.load(npz)["xy"]
    xy = parse_shp(shp, where=where)
    valid = xy[~np.isnan(xy[:, 0])] if xy.size else xy.reshape(0, 2)
    bbox = ([float(valid[:, 0].min()), float(valid[:, 0].max()),
             float(valid[:, 1].min()), float(valid[:, 1].max())]
            if valid.size else [0.0, 0.0, 0.0, 0.0])
    np.savez_compressed(npz, xy=xy, bbox=np.asarray(bbox, dtype=np.float64))
    return xy


def _plot_clipped(ax, xy, extent: Sequence[float], color: str,
                  linewidth: float, alpha: float = 1.0,
                  zorder: float = 5) -> bool:
    """按 extent 裁剪后绘制一条折线束；返回是否有内容被画出。"""
    if xy is None or xy.size == 0:
        return False
    lon0, lon1, lat0, lat1 = extent
    pad_lon = (lon1 - lon0) * 0.05
    pad_lat = (lat1 - lat0) * 0.05
    m = ((xy[:, 0] >= lon0 - pad_lon) & (xy[:, 0] <= lon1 + pad_lon) &
         (xy[:, 1] >= lat0 - pad_lat) & (xy[:, 1] <= lat1 + pad_lat))
    # 用 NaN 分隔重建段：保留裁剪点与其相邻点，避免长直线穿越
    keep = m | np.isnan(xy[:, 0])
    sub = xy[keep]
    if not sub.size:
        return False
    ax.plot(sub[:, 0], sub[:, 1], color=color, linewidth=linewidth, alpha=alpha,
            zorder=zorder, solid_capstyle="round")
    return True


def add_provinces(ax, extent: Sequence[float], color: str = "#2B2F36",
                  linewidth: float = 0.6, basemap: Optional[np.ndarray] = None) -> None:
    """在坐标轴（等经纬度）上叠加省界，按 extent=[lon0,lon1,lat0,lat1] 裁剪。"""
    try:
        xy = basemap if basemap is not None else load_basemap()
    except Exception:
        ax.text(0.99, 0.01, "底图降级", transform=ax.transAxes, ha="right",
                va="bottom", fontsize=7, color=ACCENT)
        return
    _plot_clipped(ax, xy, extent, color, linewidth)


def _is_gd_city(rec: Dict[str, str]) -> bool:
    return rec.get("SHENG") == GD_PROVINCE_CODE


def _is_gd_province(rec: Dict[str, str]) -> bool:
    return rec.get("name") == PROVINCE_NAME


def _is_neighbor_province(rec: Dict[str, str]) -> bool:
    return rec.get("name") != PROVINCE_NAME


def add_admin_layers(ax, view: Sequence[float]) -> None:
    """三层行政底图：邻省淡化 → 广东地市界 → 广东省轮廓加粗。

    全部失败时退回 ``add_provinces``（沿用「底图降级」提示）。
    """
    layers = (
        (PROVINCE_SHP, _is_neighbor_province, "neighbors", "#8A9099", 0.5, 0.35),
        ("Province_9", _is_gd_city, "gd_city", "#6B7280", 0.5, 1.0),
        (PROVINCE_SHP, _is_gd_province, "gd_prov", INK, 1.1, 1.0),
    )
    drew = False
    for name, where, tag, color, lw, alpha in layers:
        try:
            xy = load_basemap(name=name, where=where, tag=tag)
        except Exception:
            continue
        if _plot_clipped(ax, xy, view, color, lw, alpha):
            drew = True
    if not drew:
        add_provinces(ax, view)


def basemap_view(extent: Sequence[float], min_span_deg: float = 8.0) -> List[float]:
    """检索区与广东相交时用「整省视野」，否则退回通用外扩视窗。"""
    lon0, lon1, lat0, lat1 = (float(v) for v in extent)
    if not (lon1 < GD_BBOX[0] or lon0 > GD_BBOX[1] or
            lat1 < GD_BBOX[2] or lat0 > GD_BBOX[3]):
        return [GD_BBOX[0] - GD_VIEW_PAD, GD_BBOX[1] + GD_VIEW_PAD,
                GD_BBOX[2] - GD_VIEW_PAD, GD_BBOX[3] + GD_VIEW_PAD]
    return _view_extent(extent, min_span_deg)


def render_region_map_png(view: Optional[Sequence[float]] = None,
                          px: Sequence[int] = REGION_PX) -> bytes:
    """渲染「地图圈选」用的广东省底图 PNG（不含检索数据）。

    坐标轴铺满画布且不设 aspect，因此经纬度 → 像素是**严格线性**映射，
    前端据此把鼠标位置换算成经纬度（与 ``/api/region/bounds`` 返回的
    ``REGION_VIEW`` / ``REGION_PX`` 必须保持一致）。
    """
    global _REGION_PNG
    if _REGION_PNG is not None:
        return _REGION_PNG
    import io
    view = list(view or REGION_VIEW)
    w_px, h_px = int(px[0]), int(px[1])
    dpi = 125.0
    fig = plt.figure(figsize=(w_px / dpi, h_px / dpi), dpi=dpi)
    fig.patch.set_facecolor(PAPER)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(PAPER)
    ax.set_xlim(float(view[0]), float(view[1]))
    ax.set_ylim(float(view[2]), float(view[3]))
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ax.spines.values():
        side.set_visible(False)
    add_admin_layers(ax, view)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches=None, pad_inches=0,
                facecolor=PAPER)
    plt.close(fig)
    _REGION_PNG = buf.getvalue()
    return _REGION_PNG


# ── 视窗与配色 ────────────────────────────────────────────────────────
def _cell_extent(lon: np.ndarray, lat: np.ndarray) -> List[float]:
    """pcolormesh(shading="auto") 的真实覆盖范围：格点 ± 半格（nearest 口径）。

    直接用 lon.min()/max() 画检索框会内缩半格，与数据块边缘错开。
    """
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    dx = abs(float(lon[1] - lon[0])) / 2.0 if lon.size > 1 else 0.0
    dy = abs(float(lat[1] - lat[0])) / 2.0 if lat.size > 1 else 0.0
    return [float(lon.min()) - dx, float(lon.max()) + dx,
            float(lat.min()) - dy, float(lat.max()) + dy]


def _view_extent(extent: Sequence[float], min_span_deg: float = 8.0) -> List[float]:
    """把地图显示范围外扩到最小边长。

    检索窗口（half_span_deg=1.0 → 2°×2°）太小，框内几乎没有行政边界，
    底图（F4.0）就形同虚设；外扩后省界/海岸线可见，数据仍只画在检索窗口内。
    """
    lon0, lon1, lat0, lat1 = (float(v) for v in extent)
    clon, clat = (lon0 + lon1) / 2.0, (lat0 + lat1) / 2.0
    half = max(min_span_deg, lon1 - lon0, lat1 - lat0) / 2.0
    return [clon - half, clon + half, clat - half, clat + half]


def _mark_extent(ax, extent: Sequence[float], label: Optional[str] = None) -> None:
    """虚线框标出真正的检索窗口，避免把外扩后的底图范围当成数据范围。"""
    lon0, lon1, lat0, lat1 = (float(v) for v in extent)
    ax.add_patch(Rectangle((lon0, lat0), lon1 - lon0, lat1 - lat0, fill=False,
                           edgecolor="#3A4048", linewidth=0.9,
                           linestyle=(0, (4, 3)), zorder=6))
    if label:
        # 标注放在框内左上角：框下方的位置常被海岸线压住，读不出来
        ax.text(lon0 + (lon1 - lon0) * 0.03, lat1 - (lat1 - lat0) * 0.05, label,
                ha="left", va="top", fontsize=7.5, color="#3A4048", zorder=7,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.75, pad=1.5))


def _geo_aspect(extent: Sequence[float]) -> float:
    """等经纬度底图按中心纬度修正纵横比，避免省界被横向拉扁。"""
    clat = (float(extent[2]) + float(extent[3])) / 2.0
    return 1.0 / max(0.2, math.cos(math.radians(clat)))


def _prob_threshold(ths: Sequence[float], target: float = 50.0) -> float:
    """主概率场取哪一档阈值：规范 F3.4 要的是 ≥50mm 的强降水出现频率场。"""
    return min((float(t) for t in ths), key=lambda t: (abs(t - target), t))


# ── 通用样式 ──────────────────────────────────────────────────────────
def _paper_axes(ax, title: str = "", fontsize: float = 9.0, grid: bool = True) -> None:
    ax.set_facecolor(PAPER)
    ax.set_title(title, fontsize=fontsize, color=INK, pad=4)
    if grid:
        ax.tick_params(labelsize=7, colors="#4A5058", length=2)
        ax.grid(True, linestyle=":", linewidth=0.4, color=GRID_C, alpha=0.6)


def _save(fig, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path, dpi=135, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


def _field_2d(fields, element, sample_i, ti, li, nlat, nlon):
    return fields[element][sample_i, ti, li, :].reshape(nlat, nlon)


# ── 要素场对比图 ──────────────────────────────────────────────────────
def render_field_comparison(cfg: SearchConfig, grid, fields, read_dates,
                            target_i: int, ranking: Dict, combine: Dict,
                            out_dir: str, time_hour: int, k: int = 5,
                            keys: Optional[List[str]] = None,
                            picks: Optional[List[str]] = None,
                            tag: Optional[str] = None) -> Optional[str]:
    """首行 = 目标日，其后每行一个相似时刻；列为要素（≤6 列）。

    ``keys`` / ``picks`` / ``tag`` 供「直取要素场图」复用同一套绘制逻辑：
    直取组要素、直取排名的时刻都可覆盖，输出文件名用 ``tag`` 区分。
    """
    if keys is None:
        keys = cfg.inter_group[:6] if cfg.inter_group else cfg.variable_keys[:6]
    ts = str(time_hour)
    if picks is None:
        picks = [d for d in combine.get(ts, {}).get("final", [])[:k]]
    if not picks or not keys:
        return None
    known = {d.isoformat(): i for i, d in enumerate(read_dates)}
    rows = [target_i] + [known[d] for d in picks if d in known]
    row_dates = [cfg.target_date] + [d for d in picks if d in known]

    n_rows, n_cols = len(rows), len(keys)
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(3.0 * n_cols, 2.6 * n_rows),
                             squeeze=False)
    fig.patch.set_facecolor(PAPER)
    extent = _cell_extent(grid.lon, grid.lat)
    # 2° 检索窗太小，外扩到 8° 后底图才看得出来（整省视野会让 2° 数据块只占
    # 面板约 23%，场的细节受损，故这里不用 basemap_view 的整省视野）
    view = _view_extent(extent, 8.0)

    for ci, key in enumerate(keys):
        element, level = split_key(key)
        li = cfg.levels_of(element).index(float(level))
        ti = cfg.times.index(time_hour)
        stack = np.stack([_field_2d(fields, element, r, ti, li,
                                    grid.nlat, grid.nlon) for r in rows])
        finite = stack[np.isfinite(stack)]
        if finite.size:
            vmin, vmax = float(np.percentile(finite, 1)), float(np.percentile(finite, 99))
        else:
            vmin, vmax = 0.0, 1.0
        if vmax <= vmin:
            vmax = vmin + 1e-6
        for ri, r in enumerate(rows):
            ax = axes[ri][ci]
            im = ax.pcolormesh(grid.lon, grid.lat, stack[ri],
                               cmap=ELEMENT_CMAP.get(element, ELEMENT_CMAP_DEFAULT),
                               vmin=vmin, vmax=vmax, shading="auto")
            ax.contour(grid.lon, grid.lat, stack[ri], levels=6,
                       colors="#3A4048", linewidths=0.35, alpha=0.7)
            add_admin_layers(ax, view)
            _mark_extent(ax, extent)
            ax.set_xlim(view[0], view[1])
            ax.set_ylim(view[2], view[3])
            ax.set_aspect(_geo_aspect(view))
            label = f"{var_label(key)} [{var_units(key)}]"
            if ri == 0:
                _paper_axes(ax, f"{label}\n目标 {row_dates[0]} {time_hour:02d}UTC")
            else:
                si = next((row["si"] for row in ranking.get(key, {}).get(ts, [])
                           if row["date"] == row_dates[ri]), float("nan"))
                _paper_axes(ax, f"#{ri} {row_dates[ri]} {time_hour:02d}UTC\nSI={si:.1f}",
                            fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).ax.tick_params(labelsize=6)
    fig.suptitle(f"要素场对比 · {cfg.name} · {time_hour:02d}UTC（按相似度排名）",
                 fontsize=12, color=INK)
    fig.text(0.5, 0.005, "虚线框为检索数据区（格点覆盖范围）；"
                         "底图：广东省地市界 + 广东省轮廓（邻省淡化）",
             ha="center", fontsize=8, color="#4A5058")
    name = f"field_{tag}.png" if tag else f"field_{time_hour:02d}utc.png"
    return _save(fig, os.path.join(out_dir, name))


def render_direct_field(cfg: SearchConfig, grid, fields, read_dates, target_i: int,
                        ranking: Dict, out_dir: str, time_hour: int,
                        k: Optional[int] = None) -> Optional[str]:
    """直取要素场图（可选项）：与交集口径无关。

    行取「直取组第一个要素」的排名 Top-k（排除目标日 ±N 天后的自然结果），
    列取直取组要素；直取组为空时不出图。
    """
    keys = cfg.direct_group[:6]
    if not keys:
        return None
    topk = int(k or cfg.direct_topn)
    ts = str(time_hour)
    picks = [row["date"] for row in ranking.get(keys[0], {}).get(ts, [])[:topk]]
    return render_field_comparison(cfg, grid, fields, read_dates, target_i, ranking,
                                   {}, out_dir, time_hour, k=topk, keys=keys,
                                   picks=picks, tag=f"direct_{time_hour:02d}utc")


# ── 降水统计图 ────────────────────────────────────────────────────────
def render_rain_bars(cfg: SearchConfig, products: Dict, out_dir: str) -> Optional[str]:
    by_time = products["by_time"]
    ts_list = [t for t in by_time if t != "24h"]
    if not ts_list and "24h" not in by_time:
        return None
    panels = sorted(ts_list, key=int) + (["24h"] if "24h" in by_time else [])
    n = len(panels)
    fig, axes = plt.subplots(1, n, figsize=(4.6 * n, 4.0), squeeze=False)
    fig.patch.set_facecolor(PAPER)
    th_hi = products["thresholds"][-1] if products["thresholds"] else 50.0
    th_lo = products["thresholds"][0] if products["thresholds"] else 0.1
    for i, ts in enumerate(panels):
        ax = axes[0][i]
        items = by_time[ts]["samples"]
        if not items:
            _paper_axes(ax, f"{ts} 无有效样本")
            continue
        vals = np.array([s["mm"] for s in items], dtype=float)
        xs = np.arange(vals.size)
        colors = np.where(vals >= th_hi, ACCENT,
                          np.where(vals >= th_lo, "#7FA8C9", "#C9CFD6"))
        ax.bar(xs, vals, color=colors, width=0.85, linewidth=0)
        ax.axhline(th_hi, color=ACCENT, linestyle="--", linewidth=0.8)
        ax.text(0.98, 0.96, f"n={by_time[ts]['n_valid']} · 均值 {by_time[ts]['mean_mm']}mm",
                transform=ax.transAxes, ha="right", va="top", fontsize=8, color="#4A5058")
        _paper_axes(ax, f"相似时刻降水 (锚定 {ts})" if ts != "24h" else "24 小时降水")
        ax.set_xlabel("相似时刻（按降水量降序）", fontsize=8)
        ax.set_ylabel("降水量 (mm)", fontsize=8)
        ax.set_xticks([])
    return _save(fig, os.path.join(out_dir, "rain_bars.png"))


# ── 概率空间分布图 ────────────────────────────────────────────────────
def render_spatial(cfg: SearchConfig, products: Dict, glat: np.ndarray,
                   glon: np.ndarray, out_dir: str, tag: str) -> Optional[str]:
    """降水概率空间分布：相似时刻平均降水场 + 出现概率场 + 强降水概率场。

    面板口径按 F3.1：6h 降水概率（最小阈值，默认 ≥0.1mm）与强降水概率（取
    ≥50mm 档，F3.4），两者都来自相似时刻降水窗口累加场；最终集合有 1 个
    相似时刻也照常出图。
    """
    ts = "24h" if tag == "24h" else str(int(tag[:2]))   # "06utc" -> "6"（products 的键）
    prod = products["by_time"].get(ts)
    if not prod or not prod["spatial"]:
        return None
    ths = sorted({float(t) for t in products["thresholds"]})
    mean = prod["spatial"].get("mean")
    if mean is None or not ths:
        return None
    strong_th = _prob_threshold(ths)
    picks = list(dict.fromkeys([ths[0], strong_th]))  # 出现概率 + 强降水概率
    panels = [("mean", None)] + [(f"prob_{th}", th) for th in picks]

    extent = _cell_extent(glon, glat)
    view = basemap_view(extent, 8.0)   # 检索区与广东相交时用整省视野

    win_txt = "24 小时" if ts == "24h" else f"{ts}UTC 6 小时窗口"
    fig, axes = plt.subplots(1, len(panels), figsize=(6.5 * len(panels), 5.4),
                             squeeze=False)
    axes = axes[0]
    fig.patch.set_facecolor(PAPER)
    levels = np.linspace(0, 1, 11)
    for i, (key, th) in enumerate(panels):
        ax = axes[i]
        if key == "mean":
            im = ax.pcolormesh(glon, glat, mean, cmap=SPATIAL_CMAP_MEAN, shading="auto")
            _paper_axes(ax, f"相似时刻平均降水量 (mm)\n{win_txt} · n={prod['n_valid']}")
            fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02).ax.tick_params(labelsize=7)
        else:
            field_p = prod["spatial"].get(key)
            pmax = float(np.max(field_p)) if field_p is not None else 0.0
            # 概率常落在色标首档（0–10%），与 0% 肉眼难分，标题里直接给出样本集最大值
            tip = ("（样本集内无达到该阈值的格点）" if pmax <= 0
                   else f"（样本集最大 {pmax * 100:.1f}%）")
            _paper_axes(ax, f"降水量 ≥ {th:g}mm 出现概率{tip}\n{win_txt} · n={prod['n_valid']}")
            if field_p is None:
                ax.text(0.5, 0.5, "该阈值概率场缺失", transform=ax.transAxes,
                        ha="center", va="center", fontsize=9, color=ACCENT)
            else:
                # vmin/vmax 必须交给 Normalize（BoundaryNorm 已含区间），不能再单独传
                cmap_p = (SPATIAL_CMAP_PROB_STRONG if th == strong_th
                          else SPATIAL_CMAP_PROB_LOW)
                im = ax.pcolormesh(glon, glat, field_p, cmap=cmap_p, shading="auto",
                                   norm=BoundaryNorm(levels, 256))
                cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02, ticks=levels)
                cb.ax.set_yticklabels([f"{int(l * 100)}%" for l in levels], fontsize=7)
                if pmax <= 0.0:
                    # 全 0 不等于缺测：是样本集内确实没有达到该阈值的个例
                    ax.text(0.5, 0.5, f"本样本集内无格点达到 {th:g}mm\n（强度不足，非缺测）",
                            transform=ax.transAxes, ha="center", va="center",
                            fontsize=9, color="#8A5A28",
                            bbox=dict(facecolor="white", edgecolor="none", alpha=0.85))
        _mark_extent(ax, extent, label="检索数据区" if i == 0 else None)
        add_admin_layers(ax, view)
        ax.set_xlim(view[0], view[1])
        ax.set_ylim(view[2], view[3])
        ax.set_aspect(_geo_aspect(view))
        ax.set_xlabel("经度 (°E)", fontsize=9)
        ax.set_ylabel("纬度 (°N)", fontsize=9)
    fig.suptitle(f"降水概率空间分布 · {cfg.name} · 目标 {cfg.target_date}",
                 fontsize=12, color=INK)
    fig.text(0.5, 0.005, f"由 {prod['n_valid']} 个相似时刻的降水窗口累加场统计；"
                         "虚线框为检索数据区（格点覆盖范围），"
                         "底图：广东省地市界 + 广东省轮廓（邻省淡化）",
             ha="center", fontsize=8, color="#4A5058")
    return _save(fig, os.path.join(out_dir, f"spatial_{tag}.png"))


# ── 统一入口 ──────────────────────────────────────────────────────────
def render_all(cfg: SearchConfig, grid, glat, glon, fields, read_dates, target_i,
               ranking: Dict, combine: Dict, products: Dict, out_dir: str,
               progress=None) -> Dict[str, str]:
    out: Dict[str, str] = {}
    steps = []
    for t in cfg.times:
        steps.append(("field", t))
    if cfg.plot_direct_fields and cfg.direct_group:
        for t in cfg.times:
            steps.append(("direct", t))
    steps.append(("bars", None))
    # 每个时次各出一张概率空间图（原实现只出 cfg.times[0]，见说明文档疑点 12）
    for t in cfg.times:
        steps.append(("spatial", f"{t:02d}utc"))
    if cfg.enable_24h:
        steps.append(("spatial", "24h"))
    total = len(steps)
    for i, (kind, arg) in enumerate(steps, 1):
        if progress:
            progress("figures", "生成图表", i - 1, total,
                     {"field": "要素场对比图", "direct": "直取要素场图",
                      "bars": "降水统计图",
                      "spatial": "概率空间分布图"}[kind])
        try:
            if kind == "field":
                p = render_field_comparison(cfg, grid, fields, read_dates, target_i,
                                            ranking, combine, out_dir, arg)
                if p:
                    out[f"field_{arg:02d}"] = p
            elif kind == "direct":
                p = render_direct_field(cfg, grid, fields, read_dates, target_i,
                                        ranking, out_dir, arg)
                if p:
                    out[f"field_direct_{arg:02d}utc"] = p
            elif kind == "bars":
                p = render_rain_bars(cfg, products, out_dir)
                if p:
                    out["rain_bars"] = p
            else:
                p = render_spatial(cfg, products, glat, glon, out_dir, arg)
                if p:
                    out[f"spatial_{arg}"] = p
        except Exception as exc:  # 单张图失败不应中断整轮检索
            out[f"error_{kind}_{arg}"] = f"{type(exc).__name__}: {exc}"
    if progress:
        progress("figures", "生成图表", total, total, "完成")
    return out


def _self_check() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    shp = os.path.join(root, "图层调用", "shp", "Province_9", "Province_9.shp")
    if os.path.isfile(shp):
        xy = parse_shp(shp)
        assert xy.ndim == 2 and xy.shape[1] == 2
        valid = xy[~np.isnan(xy[:, 0])]
        print(f"  省界点数={valid.shape[0]}, 经度范围="
              f"[{valid[:, 0].min():.2f},{valid[:, 0].max():.2f}], 纬度范围="
              f"[{valid[:, 1].min():.2f},{valid[:, 1].max():.2f}]")
        # 快速校验是否为 WGS84 经纬度（中国范围，含南海诸岛至约 3°N）
        assert 70 <= valid[:, 0].min() <= 80 and 130 <= valid[:, 0].max() <= 140, \
            "底图经度疑似非 WGS84"
        assert 0 <= valid[:, 1].min() <= 20 and 45 <= valid[:, 1].max() <= 60, \
            "底图纬度疑似非 WGS84"
    else:
        print("  未找到底图，跳过")
    # 第 3 项：广东省地市界（Province_9 属性表 SHENG=="44"，实测 347 个面）
    dbf = os.path.join(root, "图层调用", "shp", "Province_9", "Province_9.dbf")
    if os.path.isfile(dbf):
        gd_recs = [r for r in parse_dbf(dbf) if _is_gd_city(r)]
        assert len(gd_recs) == 347, f"广东地市面数={len(gd_recs)}，应为 347"
        xy_city = load_basemap(name="Province_9", where=_is_gd_city, tag="gd_city")
        vc = xy_city[~np.isnan(xy_city[:, 0])]
        assert vc.size > 0, "广东地市层解析为空"
        assert abs(float(vc[:, 0].min()) - GD_BBOX[0]) < 0.05
        assert abs(float(vc[:, 0].max()) - GD_BBOX[1]) < 0.05
        assert abs(float(vc[:, 1].min()) - GD_BBOX[2]) < 0.05
        assert abs(float(vc[:, 1].max()) - GD_BBOX[3]) < 0.05
    # 省级层（邻省 / 广东省轮廓）：曾因 shp 落在子目录（china_pro/）而静默失败
    xy_nb = load_basemap(name=PROVINCE_SHP, where=_is_neighbor_province, tag="neighbors")
    vn = xy_nb[~np.isnan(xy_nb[:, 0])]
    assert vn.shape[0] > 1000, "邻省层解析为空"
    xy_gp = load_basemap(name=PROVINCE_SHP, where=_is_gd_province, tag="gd_prov")
    vg = xy_gp[~np.isnan(xy_gp[:, 0])]
    assert vg.shape[0] > 100, "广东省轮廓层解析为空"
    assert abs(float(vg[:, 0].min()) - GD_BBOX[0]) < 0.2, "广东省轮廓范围异常"
    # 配色：要素分色 + 概率面板三色。名称拼错只会在出图时才暴露，这里先兜住
    from matplotlib import colormaps
    for cname in (list(ELEMENT_CMAP.values()) + [ELEMENT_CMAP_DEFAULT,
                 SPATIAL_CMAP_MEAN, SPATIAL_CMAP_PROB_LOW, SPATIAL_CMAP_PROB_STRONG]):
        assert colormaps.get(cname) is not None, f"未知配色: {cname}"
    assert len(set(ELEMENT_CMAP.values())) == len(ELEMENT_CMAP), "要素配色必须互不相同"
    assert len({SPATIAL_CMAP_MEAN, SPATIAL_CMAP_PROB_LOW,
                SPATIAL_CMAP_PROB_STRONG}) == 3, "概率面板配色必须三种不同"
    # 视野：检索区与广东相交 → 整省视野；不相交 → 退回通用外扩
    assert basemap_view([112.0, 114.0, 22.0, 24.0]) == \
        [GD_BBOX[0] - GD_VIEW_PAD, GD_BBOX[1] + GD_VIEW_PAD,
         GD_BBOX[2] - GD_VIEW_PAD, GD_BBOX[3] + GD_VIEW_PAD]
    assert basemap_view([130.0, 132.0, 30.0, 32.0]) == \
        _view_extent([130.0, 132.0, 30.0, 32.0], 8.0)
    # 第 5 项：地图圈选底图 PNG（严格线性映射，前端据此换算经纬度）
    png = render_region_map_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000, "圈选底图 PNG 异常"
    assert render_region_map_png() is png, "圈选底图应命中模块级缓存"
    # 概率空间图回归：曾因 norm 与 vmin/vmax 同时传入而静默失败，用合成数据兜住
    import tempfile
    tmp = tempfile.mkdtemp(prefix="histsim_viz_")
    cfg = SearchConfig(target_date="2024-06-30", times=[0])
    glat = np.linspace(22.0, 24.0, 11)
    glon = np.linspace(112.0, 114.0, 11)
    field = np.random.default_rng(0).random((11, 11))  # spatial 已是集合平均后的二维场
    products = {"thresholds": [0.1, 50.0], "by_time": {"0": {
        "n_valid": 5,
        "spatial": {"mean": field, "prob_0.1": field, "prob_50.0": field * 0},
    }}}
    p = render_spatial(cfg, products, glat, glon, tmp, "00utc")
    assert p and os.path.isfile(p), f"概率空间图未能生成: {p}"
    # tag 归一化：products 的键是 str(t)，"06utc" 必须映射到 "6" 才能命中，
    # 否则多时次配置下每张图都会因查不到键而 return None（疑点 12）
    products6 = {"thresholds": [0.1, 50.0], "by_time": {"6": {
        "n_valid": 3,
        "spatial": {"mean": field, "prob_0.1": field, "prob_50.0": field * 0},
    }}}
    p6 = render_spatial(cfg, products6, glat, glon, tmp, "06utc")
    assert p6 and os.path.isfile(p6), f"多时次概率图未能生成（tag 未归一化）: {p6}"
    assert render_spatial(cfg, products6, glat, glon, tmp, "12utc") is None
    # 最终集合只有 1 个相似时刻时也必须能出图（n=1 是合法输入，不是错误）
    products1 = {"thresholds": [0.1, 50.0, 100.0], "by_time": {"0": {
        "n_valid": 1,
        "spatial": {"mean": field, "prob_0.1": field, "prob_50.0": field * 0,
                    "prob_100.0": field * 0},
    }}}
    p1 = render_spatial(cfg, products1, glat, glon, tmp, "00utc")
    assert p1 and os.path.getsize(p1) > 0, f"单样本概率空间图未能生成: {p1}"
    # 视窗必须比检索窗口大，否则 2°×2° 内底图几乎不可见（F4.0 形同虚设）
    view = _view_extent([112.0, 114.0, 22.0, 24.0], 8.0)
    assert view[1] - view[0] >= 8.0 and view[3] - view[2] >= 8.0, view
    assert view[0] <= 112.0 and view[1] >= 114.0, view
    assert abs(_geo_aspect([0, 1, 22, 24])
               - 1.0 / math.cos(math.radians(23.0))) < 1e-9
    # 检索框按 nearest 口径外扩半格，必须与 pcolormesh 的数据块边缘重合
    ce = _cell_extent(np.array([112.0, 112.5]), np.array([22.0, 22.5]))
    assert ce == [111.75, 112.75, 21.75, 22.75], ce
    # 主概率场取 50mm 档（F3.4 强降水出现频率场），而不是列表里的最大阈值
    assert _prob_threshold([0.1, 50.0, 100.0]) == 50.0
    assert _prob_threshold([0.1, 30.0]) == 30.0
    # 要素场对比图：最终集合仅 1 个相似时刻
    from datetime import date
    from types import SimpleNamespace
    cfg_f = SearchConfig(target_date="2024-06-30", times=[0],
                         variables=[["u", 925]], inter_group=["u925"],
                         direct_group=[], half_span_deg=1.0)
    grid = SimpleNamespace(lat=glat, lon=glon, nlat=glat.size, nlon=glon.size)
    fields = {"u": np.random.default_rng(2).random((2, 1, 1, glat.size * glon.size))}
    ranking = {"u925": {"0": [{"rank": 1, "date": "2005-06-12", "si": 1.5}]}}
    combine = {"0": {"final": ["2005-06-12"], "source": {"2005-06-12": "1 要素交集"}}}
    pf = render_field_comparison(cfg_f, grid, fields,
                                 [date(2024, 6, 30), date(2005, 6, 12)], 0,
                                 ranking, combine, tmp, 0)
    assert pf and os.path.getsize(pf) > 0, f"单相似时刻要素场图未能生成: {pf}"
    # 直取要素场图（第 8 项）：直取组为空 → None；非空 → 按直取排名出图
    dates2 = [date(2024, 6, 30), date(2005, 6, 12)]
    assert render_direct_field(cfg_f, grid, fields, dates2, 0, ranking, tmp, 0) is None
    pd_ = render_direct_field(cfg_f.copy(direct_group=["u925"]), grid, fields,
                              dates2, 0, ranking, tmp, 0)
    assert pd_ and os.path.getsize(pd_) > 0, f"直取要素场图未能生成: {pd_}"
    # render_all：每个时次各出一张概率空间图（第 9 项 / 疑点 12）
    cfg_t = SearchConfig(target_date="2024-06-30", times=[0, 6],
                         variables=[["u", 925]], inter_group=["u925"],
                         direct_group=[], half_span_deg=1.0, enable_24h=False)
    fields2 = {"u": np.random.default_rng(3).random((2, 2, 1, glat.size * glon.size))}
    spatial2 = {"n_valid": 1, "spatial": {"mean": field, "prob_0.1": field,
                                          "prob_50.0": field * 0}}
    figs = render_all(cfg_t, grid, glat, glon, fields2,
                      [date(2024, 6, 30), date(2005, 6, 12)], 0,
                      {"u925": {"0": [{"rank": 1, "date": "2005-06-12", "si": 1.0}],
                                "6": [{"rank": 1, "date": "2005-06-12", "si": 1.0}]}},
                      {"0": {"final": ["2005-06-12"]}, "6": {"final": ["2005-06-12"]}},
                      {"thresholds": [0.1, 50.0], "by_time": {"0": spatial2, "6": spatial2}},
                      tmp)
    assert "spatial_00utc" in figs and "spatial_06utc" in figs, figs
    assert "spatial_24h" not in figs
    print("viz self-check OK")


if __name__ == "__main__":
    _self_check()