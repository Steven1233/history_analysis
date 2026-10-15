# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包描述：生成「onedir」发行目录，供 Inno Setup 打成安装包。

用 onedir 而不是 onefile：
* 安装器本来就要铺一整个目录，onefile 的「启动时解压到临时目录」纯属额外开销；
* 本程序自带 27MB 省界底图与 matplotlib，onefile 每次启动都要解压数百 MB；
* onedir 下 `sys._MEIPASS` 即 `_internal`，模块与数据保持相对布局，
  代码里基于 `__file__` 定位的 static / 图层调用 无需改写。

构建：  python -m PyInstaller packaging/histsim.spec --noconfirm
产物：  dist/Histsim/Histsim.exe 与 dist/Histsim/_internal/
"""
import os

# SPECPATH 由 PyInstaller 注入，就是本 spec 所在目录（packaging/）
SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)
assert os.path.isfile(os.path.join(SPEC_DIR, "app_entry.py")), SPEC_DIR
assert os.path.isdir(os.path.join(ROOT, "histsim")), ROOT

# 界面静态资源与省界底图：都靠 __file__ 相对定位，必须保持原始相对路径
datas = [
    (os.path.join(ROOT, "histsim", "web", "static"), os.path.join("histsim", "web", "static")),
    (os.path.join(ROOT, "图层调用"), "图层调用"),
]

# netCDF4 的编译扩展与时间轴、matplotlib 的 Agg 后端都不会被静态分析发现
hiddenimports = [
    "netCDF4",
    "cftime",
    "matplotlib.backends.backend_agg",
]

# 本程序用不到的重型可选依赖：排除后体积明显下降
excludes = [
    "tkinter", "_tkinter",
    "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "jupyter", "notebook", "nbformat",
    "pytest", "sphinx", "docutils",
    "pandas", "scipy",
]

_icon = os.path.join(SPEC_DIR, "assets", "app.ico")

# VC++ 运行时：PyInstaller 只会带上 vcruntime140*.dll，而 netCDF4/hdf5 这些用
# MSVC 编译的扩展还会动态依赖 msvcp140 系列。目标机若没装「VC++ 2015-2022
# 可再发行组件」就会启动失败，这里把 System32 里的运行时一并打进包内。
binaries = []
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for _dll in ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
             "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll"):
    _p = os.path.join(_sys32, _dll)
    if os.path.isfile(_p):
        binaries.append((_p, "."))

a = Analysis(
    [os.path.join(SPEC_DIR, "app_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Histsim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压缩后极易被安全软件误报，且对启动速度有负面影响，这里关闭
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    icon=_icon if os.path.isfile(_icon) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Histsim",
)