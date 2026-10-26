# -*- coding: utf-8 -*-
"""本地 Web 后端：用标准库 ``http.server`` 承载 JSON API + 静态界面 + 图表。

不引入 Flask/FastAPI —— 本系统是**本机单用户**工具，标准库足够，且免去依赖安装。
并发模型用 ``ThreadingHTTPServer``：数据源扫描、检索任务本身都在独立线程里跑，
界面轮询进度不会被阻塞。

启动：
    python -m histsim.web.server              # 默认 http://127.0.0.1:8770
    python -m histsim.web.server --port 9000 --no-browser
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import __version__
from ..api import SearchSession
from ..config import BUILTIN_KEYS, ELEMENTS, PRECIP_ELEMENT_KEY
from ..precip import PRECIP_PRODUCTS

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
MAX_BODY = 8 * 1024 * 1024

CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".zip": "application/zip",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


# ── 路由表 ────────────────────────────────────────────────────────────
# 每个处理函数签名: (session, query, body) -> (status, payload)
API: Dict[Tuple[str, str], Callable] = {}


def route(method: str, path: str):
    def deco(fn: Callable) -> Callable:
        API[(method, path)] = fn
        return fn
    return deco


def _q(query: Dict[str, List[str]], key: str, default: Any = None) -> Any:
    vals = query.get(key)
    return vals[0] if vals else default


def _config_of(body: Dict) -> Dict:
    """允许 ``{"config": {...}}`` 与直接把配置字段放在顶层两种写法。"""
    if isinstance(body.get("config"), dict):
        return body["config"]
    return {k: v for k, v in body.items() if k != "config"}


@route("GET", "/api/health")
def _health(s: SearchSession, q, b):
    return 200, {"ok": True, "version": __version__,
                 "data_dir": s.store.data_dir,
                 "results_dir": s.store.results_dir,
                 "live_tasks": len(s.live_tasks()),
                 # live_tasks 是「本次会话跟踪过的任务」（含已完成），
                 # running_tasks 才是真正在跑的，界面提示用的是后者
                 "running_tasks": sum(1 for t in s.live_tasks()
                                      if t.get("state") == "running")}


@route("GET", "/api/meta")
def _meta(s: SearchSession, q, b):
    """界面初始化所需的静态字典：要素表、内置模板、可选时次。"""
    return 200, {
        "ok": True,
        "version": __version__,
        "data_dir": s.store.data_dir,
        "elements": [{"key": k, "name": v[2], "units": v[3], "nc_var": v[1],
                      "file_element": v[0]} for k, v in ELEMENTS.items()],
        # 基本要素选择区的第一要素：GMCP 累计降水（无气压层，独立完整检索流程）
        "precip_element": {"key": PRECIP_ELEMENT_KEY, "name": "GMCP 累计降水",
                           "first": True, "no_level": True},
        "precip_windows": [{"key": k, "name": p.label(), "group": p.group,
                            "duration_h": p.duration_h, "folder": p.folder}
                           for k, p in PRECIP_PRODUCTS.items()],
        "default_precip_windows": ["24h_2020", "24h_0808"],
        "templates": s.templates(),
        "builtin_keys": BUILTIN_KEYS,
        "times": [0, 6, 12, 18],
        "levels": [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300,
                   250, 200, 150, 100, 70, 50, 30, 20, 10],
        "weight_modes": [{"key": "zones", "name": "距离分区（由近及远分段赋权）"},
                         {"key": "gauss", "name": "高斯衰减（半径 r0 内权重高）"}],
        "similarities": [{"key": "weighted_abs", "name": "加权绝对差（Σw·|Δx|）"},
                         {"key": "weighted_sq", "name": "加权平方差（Σw·Δx²）"},
                         {"key": "euclidean", "name": "欧氏距离（√ΣΔx²）"},
                         {"key": "cosine", "name": "余弦距离（1 − cos）"},
                         {"key": "correlation", "name": "相关距离（1 − r）"},
                         {"key": "custom", "name": "自定义公式"}],
        "feature_processes": [{"key": "none", "name": "不处理（保留原始量纲）"},
                              {"key": "standardize", "name": "标准化（除以历史标准差）"},
                              {"key": "normalize", "name": "归一化（min-max 到 0–1）"}],
        "sample_modes": [{"key": "calendar", "name": "日历全量（年 × 月 × 当月每天）"},
                         {"key": "same_period",
                          "name": "历史同期（目标日月日 ± x 天，忽略月份）"}],
        # 自定义公式的帮助文字：变量与可用函数必须与 formula.py 的白名单一致
        "formula_help": "变量：d=逐格点差(历史−目标)、w=距离权重、n=格点数；"
                        "函数：abs/sqrt/exp/log/log1p/power/clip/minimum/maximum/"
                        "sum/mean；结果必须归约为每个样本一个数值，"
                        "例如 sqrt(sum(w*d*d))",
    }


@route("GET", "/api/region/bounds")
def _region_bounds(s: SearchSession, q, b):
    """地图圈选底图的范围与像素尺寸（第 5 项）。"""
    return 200, {"ok": True, **s.region_bounds()}


@route("GET", "/api/dashboard")
def _dashboard(s: SearchSession, q, b):
    return 200, {"ok": True, **s.dashboard()}


@route("GET", "/api/sources")
def _sources(s: SearchSession, q, b):
    return 200, {"ok": True, **s.sources()}


@route("POST", "/api/sources/register")
def _register(s: SearchSession, q, b):
    root = str(b.get("root") or "").strip().strip('"')
    if not root:
        raise ValueError("请填写数据源根目录")
    if not os.path.isdir(root):
        raise ValueError(f"目录不存在或当前不可访问：{root}")
    depth = int(b.get("depth") or 1)
    rep = s.register_root(root, depth=max(0, min(2, depth)))
    return 200, {"ok": True, **rep, "sources": s.sources()}


@route("POST", "/api/sources/remove")
def _remove_source(s: SearchSession, q, b):
    return 200, s.remove_source(int(b["id"]))


# ── 数据预处理 ────────────────────────────────────────────────────────
@route("GET", "/api/preprocess/meta")
def _preprocess_meta(s: SearchSession, q, b):
    return 200, s.preprocess_meta()


@route("POST", "/api/preprocess/scan")
def _preprocess_scan(s: SearchSession, q, b):
    return 200, s.preprocess_scan(b)


@route("POST", "/api/preprocess/start")
def _preprocess_start(s: SearchSession, q, b):
    return 200, {"ok": True, **s.preprocess_start(b)}


@route("GET", "/api/preprocess/progress")
def _preprocess_progress(s: SearchSession, q, b):
    return 200, {"ok": True, **s.preprocess_progress(str(_q(q, "job_id", "")))}


@route("POST", "/api/preprocess/cancel")
def _preprocess_cancel(s: SearchSession, q, b):
    return 200, s.preprocess_cancel(str(b.get("job_id") or ""))


@route("POST", "/api/preprocess/register")
def _preprocess_register(s: SearchSession, q, b):
    return 200, s.preprocess_register(b)


@route("GET", "/api/preprocess/jobs")
def _preprocess_jobs(s: SearchSession, q, b):
    return 200, {"ok": True, "jobs": s.preprocess_jobs()}


@route("POST", "/api/preview")
def _preview(s: SearchSession, q, b):
    return 200, s.preview(_config_of(b))


@route("POST", "/api/search/start")
def _start(s: SearchSession, q, b):
    return 200, {"ok": True, **s.start(_config_of(b))}


@route("GET", "/api/search/progress")
def _progress(s: SearchSession, q, b):
    return 200, {"ok": True, **s.progress(str(_q(q, "task_id", "")))}


@route("POST", "/api/search/cancel")
def _cancel(s: SearchSession, q, b):
    return 200, s.cancel(str(b.get("task_id") or ""))


@route("GET", "/api/search/result")
def _result(s: SearchSession, q, b):
    return 200, s.result(str(_q(q, "task_id", "")))


@route("POST", "/api/search/recompute")
def _recompute(s: SearchSession, q, b):
    """V2.2.0：改 TopN / 自定义百分位后重算产品图，不重新走检索。"""
    return 200, s.recompute(b)


@route("POST", "/api/search/export")
def _export(s: SearchSession, q, b):
    """V2.2.0：把 result.json、全部产品图与累计降水排名 CSV 打包下载。"""
    return 200, s.export(b)


@route("GET", "/api/tasks")
def _tasks(s: SearchSession, q, b):
    return 200, {"ok": True, **s.tasks(int(_q(q, "limit", 100)))}


@route("GET", "/api/operations")
def _operations(s: SearchSession, q, b):
    return 200, {"ok": True, "items": s.operations(int(_q(q, "limit", 200)))}


# ── 请求处理 ──────────────────────────────────────────────────────────
def make_handler(session: SearchSession):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = f"histsim/{__version__}"

        def log_message(self, fmt, *args):  # 静音：进度靠界面轮询，不刷控制台
            pass

        def handle_one_request(self):
            """浏览器/脚本主动断开（换页、取消轮询）是常态，不该打印堆栈。"""
            try:
                super().handle_one_request()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                self.close_connection = True

        # ── 响应工具 ─────────────────────────────────────────────────
        def _send(self, status: int, body: bytes, ctype: str,
                  cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            blob = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            self._send(status, blob, CTYPES[".json"])

        def _fail(self, status: int, msg: str) -> None:
            self._json(status, {"ok": False, "error": msg})

        # ── 入口 ─────────────────────────────────────────────────────
        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _read_body(self) -> Dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            if n > MAX_BODY:
                raise ValueError("请求体过大")
            raw = self.rfile.read(n)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"请求体不是合法 JSON: {exc}") from exc
            return obj if isinstance(obj, dict) else {"value": obj}

        def _dispatch(self, method: str):
            parsed = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(parsed.path)
            query = urllib.parse.parse_qs(parsed.query)

            if method == "GET" and path.startswith("/results/"):
                return self._serve_file(session.store.results_dir,
                                        path[len("/results/"):])
            if method == "GET" and (path == "/" or path == "/index.html"):
                return self._serve_static("index.html")
            if method == "GET" and path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if method == "GET" and path == "/favicon.ico":
                return self._send(204, b"", CTYPES[".ico"])
            if method == "GET" and path == "/api/region/map.png":
                # 路由表只支持 JSON 返回，PNG 在这里特判（第 5 项）
                try:
                    return self._send(200, session.region_map_png(), "image/png",
                                      cache="no-store")
                except Exception as exc:  # 兜底：单次请求失败不应带崩服务
                    return self._fail(500, f"{type(exc).__name__}: {exc}")

            fn = API.get((method, path))
            if fn is None:
                return self._fail(404, f"未知接口：{method} {path}")
            try:
                body = self._read_body() if method == "POST" else {}
                status, payload = fn(session, query, body)
            except KeyError as exc:
                return self._fail(404, f"未找到：{exc}")
            except ValueError as exc:
                return self._fail(400, str(exc))
            except Exception as exc:  # 兜底：单次请求失败不应带崩服务
                tb = traceback.format_exc(limit=6)
                session.store.log("接口异常", f"{method} {path}", tb, "失败")
                return self._fail(500, f"{type(exc).__name__}: {exc}")
            return self._json(status, payload)

        # ── 静态与图表 ───────────────────────────────────────────────
        def _serve_static(self, rel: str) -> None:
            self._serve_file(STATIC_DIR, rel)

        def _serve_file(self, base: str, rel: str) -> None:
            rel = rel.replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
            if not parts:
                return self._fail(404, "路径为空")
            full = os.path.join(base, *parts)
            if not os.path.isfile(full):
                return self._fail(404, f"文件不存在：{rel}")
            ctype = CTYPES.get(os.path.splitext(full)[1].lower(),
                               "application/octet-stream")
            cache = "max-age=3600" if ctype.startswith("image/") else "no-store"
            with open(full, "rb") as fh:
                self._send(200, fh.read(), ctype, cache=cache)

    return Handler


# ── 启动 ──────────────────────────────────────────────────────────────
def _bind(host: str, port: int, handler, tries: int = 20):
    """端口被占则顺延，避免用户被「10048 地址已占用」挡住。"""
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            return ThreadingHTTPServer((host, port + i), handler), port + i
        except OSError as exc:
            last = exc
    raise OSError(f"{host}:{port}~{port + tries - 1} 均被占用（{last}）")


def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
        open_browser: bool = True, data_dir: Optional[str] = None) -> int:
    try:  # Windows 控制台默认 GBK，中文横幅会乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    session = SearchSession(data_dir)
    handler = make_handler(session)
    httpd, actual = _bind(host, port, handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{actual}/"

    print("=" * 62)
    print(f"  历史相似检索系统 v{__version__}  ·  本地服务已启动")
    print("=" * 62)
    print(f"  访问地址   {url}")
    print(f"  程序数据   {session.store.data_dir}")
    print(f"  结果目录   {session.store.results_dir}")
    print(f"  并行核数   {os.cpu_count()}  （解释器 {sys.executable}）")
    srcs = session.sources()
    print(f"  已登记源   ERA5 {srcs['n_era5']} 项 / GMCP {srcs['n_gmcp']} 项"
          f" · {srcs['n_files']:,} 个文件")
    if not srcs["sources"]:
        print("  提示       尚未登记数据源，请在「数据源」页填写目录后扫描")
    print("-" * 62)
    print("  关闭服务：在本窗口按 Ctrl+C")
    print("=" * 62)
    sys.stdout.flush()  # 重定向到管道/日志时 stdout 是块缓冲，不 flush 横幅不显示

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭服务…")
    finally:
        httpd.shutdown()
        httpd.server_close()
        session.close()
    print("服务已关闭。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="histsim.web",
                                description="历史相似检索系统 · 本地 Web 界面")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--data-dir", default=None, help="程序数据目录（默认 .histsim）")
    args = ap.parse_args(argv)
    return run(args.host, args.port, not args.no_browser, args.data_dir)


if __name__ == "__main__":
    sys.exit(main())