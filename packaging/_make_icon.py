# -*- coding: utf-8 -*-
"""把生成的图标位图处理成 app.ico：去右下角水印 → 裁掉圆角外的白边 → 多尺寸打包。

实测背景：水平方向在 x<0.85 基本平坦，只在右缘一圈暗角；垂直方向才是主渐变。
因此逐行取「同行左侧平坦带」的逐通道中位数回填即可，色差在 3/255 以内、肉眼不可见。
"""
from __future__ import annotations

import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "assets", "app_icon.jpg")
OUT = os.path.join(HERE, "assets", "app.ico")
PREVIEW = os.path.join(HERE, "assets", "app_preview.png")

im = Image.open(SRC).convert("RGB")
w, h = im.size
px = im.load()

# ── 1. 抹掉右下角水印：逐行取同行左侧平坦带的背景中位数回填 ──────────────
bx0, by0, bx1, by1 = int(w * 0.625), int(h * 0.675), int(w * 0.785), int(h * 0.760)
ref0, ref1 = int(w * 0.10), int(w * 0.22)     # 与右侧同色、且不含海岸线的平坦带


def _median_color(y: int, x0: int, x1: int):
    """取某行一条横向带的逐通道中位数：细等值线只占少数像素，中位数即背景色。"""
    cols = [px[x, y] for x in range(x0, x1)]
    return tuple(sorted(c[i] for c in cols)[len(cols) // 2] for i in range(3))


for y in range(by0, by1):
    px_row = _median_color(y, ref0, ref1)
    for x in range(bx0, bx1):
        px[x, y] = px_row

# ── 2. 裁掉圆角方块之外的白边，取正方形 ────────────────────────────────
box = (int(w * 0.035), int(h * 0.05), int(w * 0.965), int(h * 0.98))
im = im.crop(box)
side = min(im.size)
left = (im.width - side) // 2
top = (im.height - side) // 2
im = im.crop((left, top, left + side, top + side))

# ── 3. 多尺寸打包成 ico（Windows 会按显示场景挑合适的一档）─────────────
sizes = [16, 24, 32, 48, 64, 128, 256]
im.resize((256, 256), Image.LANCZOS).save(OUT, format="ICO",
                                          sizes=[(s, s) for s in sizes])
im.resize((256, 256), Image.LANCZOS).save(PREVIEW)
print("ico:", OUT, os.path.getsize(OUT), "bytes")
print("preview:", PREVIEW)
with Image.open(OUT) as chk:
    print("ico 内含尺寸:", sorted(chk.info.get("sizes", [])))