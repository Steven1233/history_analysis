# -*- coding: utf-8 -*-
"""命令行入口：服务启停、数据源登记、代价预估、后台检索、结果导出。

    python -m histsim.cli serve    --port 8770     # 完全启动（后台常驻）
    python -m histsim.cli stop     [--force]       # 完全暂停（含 IO 子进程）
    python -m histsim.cli register I:\\
    python -m histsim.cli dashboard
    python -m histsim.cli preview  --template plan_A --date 2024-04-27
    python -m histsim.cli search   --template plan_A --date 2024-04-27 --out 结果.json

注意：``serve`` / ``stop`` 只依赖标准库（不 import numpy/matplotlib），
这样即使运行环境依赖损坏，也能把已启动的服务停下来。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Dict, Optional


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_register(args) -> int:
    from .api import SearchSession
    s = SearchSession()
    rep = s.register_root(args.root, depth=args.depth)
    print(f"扫描根目录: {rep['root']}")
    for r in rep["registered"]:
        print(f"  + [{r['kind']:4}] {r['element']:5} {r['n_files']:>7} 文件  {r['root']}")
    for sk in rep["skipped"]:
        print(f"  - 跳过 {sk['path']}（{sk['reason'][0]}）")
    for er in rep["errors"]:
        print(f"  ! {er}")
    print(f"共登记 {len(rep['registered'])} 项")
    s.close()
    return 0 if rep["registered"] else 1


def cmd_preprocess(args) -> int:
    from .api import SearchSession
    from .precip import GROUPS, run_preprocess
    groups = [g for g in (args.groups or "24h,12h,6h").split(",") if g]
    bad = [g for g in groups if g not in GROUPS]
    if bad:
        print("未知产品分组:", ", ".join(bad))
        return 1
    keys = [k for g in groups for k in GROUPS[g]]
    last = [""]

    def report(stage, label, done, total, msg=""):
        line = f"[{stage}] {label} {done}/{total} {msg}"
        if line != last[0]:
            print(line, flush=True)
            last[0] = line

    rep = run_preprocess(args.source_root, args.output_root, keys,
                         args.year_from, args.year_to,
                         overwrite=args.overwrite, progress=report)
    print(f"写出 {rep['n_written']} · 跳过 {rep['n_skipped']} · "
          f"缺测 {rep['n_incomplete']} · 失败 {rep['n_errors']}")
    print(f"输出目录: {rep['output_root']}")
    if args.register:
        s = SearchSession()
        r = s.register_root(rep["output_root"], depth=1)
        for item in r["registered"]:
            print(f"  + [{item['kind']}] {item['element']} {item['n_files']} 文件")
        s.close()
    return 0


def cmd_dashboard(args) -> int:
    from .api import SearchSession
    s = SearchSession()
    d = s.dashboard()
    print(f"程序数据目录: {d['data_dir']}")
    print(f"数据源: ERA5 {d['counts']['n_era5']} 项 / GMCP {d['counts']['n_gmcp']} 项，"
          f"共 {d['counts']['n_files']:,} 个文件")
    if d["years"]["from"]:
        print(f"覆盖年份: {d['years']['from']} – {d['years']['to']}")
    for el, years in d["coverage"].items():
        name = next((e["name"] for e in d["elements"] if e["key"] == el), el)
        months = sorted({m for ms in years.values() for m in ms})
        print(f"  {name:8} 年份 {min(years)}–{max(years)} · 月份 {months}")
    s.close()
    return 0


def cmd_preview(args) -> int:
    from .api import SearchSession
    s = SearchSession()
    pv = s.preview(_raw(args))
    if not pv["ok"]:
        print("配置校验失败:")
        for e in pv["errors"]:
            print("  !", e)
        s.close()
        return 1
    print(f"配置: {pv['config']['name']}（指纹 {pv['config']['fingerprint']}）")
    print(f"候选历史样本: {pv['n_samples']}"
          + (f"（{pv['sample_range'][0]} ~ {pv['sample_range'][1]}）"
             if pv["sample_range"] else ""))
    print(f"待读 ERA5 文件: {pv['n_era5_files']}（要素×层次 {len(pv['config']['variable_keys'])}"
          f" × 时次 {len(pv['config']['times'])}）")
    print(f"数据覆盖度: {pv['coverage_pct']}% · 缺口 {pv['n_gaps']} 个（要素·年月）"
          f" · {'数据就绪' if pv['ready'] else '存在缺口'}")
    s.close()
    return 0


def cmd_search(args) -> int:
    from .api import SearchSession
    s = SearchSession()
    raw = _raw(args)
    t0 = time.time()
    info = s.start(raw)
    tid = info["task_id"]
    print(f"任务已启动: {tid}")
    last = ""
    while True:
        p = s.progress(tid)
        line = f"[{p['elapsed_s']:7.1f}s] {p['label']:<32} {p['done']:>6}/{p['total']:<6} {p['msg']}"
        if line != last:
            print(line, flush=True)
            last = line
        if p["state"] != "running":
            break
        time.sleep(1.0)
    res = s.result(tid)
    if not res.get("ok"):
        print("检索未成功:", res)
        s.close()
        return 1
    print(f"\n耗时 {res['elapsed_s']}s · 样本 {res['samples']['n_used']} 个"
          f"（读取失败 {res['samples']['n_read_errors']}）")
    for ts, c in res["combine"].items():
        print(f"  锚定 {ts:>4}: 交集 {c['n_inter']} · 直取 {c['n_direct']} · 合并 {c['n_final']}")
    for ts, p in res["products"]["by_time"].items():
        probs = " · ".join(f"≥{th}mm {v['prob']*100:.1f}%" for th, v in p["stats"].items())
        print(f"  {ts:>4} 降水: n={p['n_valid']} 均值 {p['mean_mm']}mm 最大 {p['max_mm']}mm | {probs}")
    print("图表:")
    for name, url in res["figures"].items():
        print(f"  {name:14} {url}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2, default=str)
        print(f"结果已写入 {os.path.abspath(args.out)}")
    s.close()
    return 0


# ── 本地服务：完全启动 / 完全暂停 ─────────────────────────────────────
# 运行记录放在 <.histsim>/run/：启动脚本写下 pid 与端口，暂停脚本据此定位进程。
# 只记一个 pid 不够健壮（旧的前台启动方式不会留记录），所以暂停时还会用
# netstat 反查端口归属做兜底。
RUN_FILE = os.path.join("run", "server.json")
DEFAULT_WEB_PORT = 8770     # 与 histsim.web.server.DEFAULT_PORT 保持一致
PORT_TRIES = 20             # 端口顺延次数，与 server._bind 一致


def _run_path() -> str:
    from .datasource import default_data_dir
    path = os.path.join(default_data_dir(), RUN_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _read_run() -> Dict:
    try:
        with open(_run_path(), encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_run(info: Dict) -> None:
    with open(_run_path(), "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)


def _get(port: int, path: str, timeout: float = 2.0) -> Optional[Dict]:
    """向本地服务发一个 GET 并解析 JSON；未启动/端口不对/非 JSON 一律返回 None。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _image_of(pid: int) -> str:
    """pid 的映像名（空串=进程不存在）。用于确认不是 pid 复用后的无关进程。"""
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, errors="replace")
    except OSError:
        return ""
    cols = r.stdout.strip().split(",")
    if len(cols) >= 2 and cols[1].strip().strip('"') == str(pid):
        return cols[0].strip().strip('"')
    return ""


def _is_ours(pid: int) -> bool:
    """该 pid 是否为本系统的服务进程。

    源码运行时服务进程是 python.exe；打包成 exe 后它就是本 exe 自身，映像名
    等于 ``sys.executable`` 的文件名，两者都要认。
    """
    img = _image_of(pid).lower()
    if not img:
        return False
    if getattr(sys, "frozen", False):
        return img == os.path.basename(sys.executable).lower()
    return img.startswith("python")


def _listening_pids(ports) -> Dict[int, int]:
    """netstat 反查监听端口的归属进程，返回 ``{pid: port}``。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, errors="replace").stdout
    except OSError:
        return {}
    want = set(ports)
    found: Dict[int, int] = {}
    for line in out.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0].upper() != "TCP" or cols[3].upper() != "LISTENING":
            continue
        try:
            port, pid = int(cols[1].rsplit(":", 1)[1]), int(cols[4])
        except (IndexError, ValueError):
            continue
        if pid and port in want:
            found[pid] = port
    return found


def _free_port(start: int, tries: int = PORT_TRIES) -> Optional[int]:
    """从 start 起找第一个能独占绑定的端口（Windows 下未设 SO_REUSEADDR 即独占）。"""
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return None


def _kill_tree(pid: int) -> bool:
    """结束进程及其子进程：检索时的 mp.Pool IO 工作进程是它的子进程。"""
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, errors="replace")
    except OSError:
        return False
    return r.returncode == 0


def _tail(path: str, lines: int = 12) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).rstrip()
    except OSError:
        return ""


def cmd_serve(args) -> int:
    """完全启动：探查已有实例 → 选空闲端口 → 分离常驻进程 → 等健康检查 → 开浏览器。"""
    import webbrowser
    base = args.port or DEFAULT_WEB_PORT

    prev = _read_run()
    pport, ppid = int(prev.get("port") or 0), int(prev.get("pid") or 0)
    reuse = 0
    if pport and ppid and _is_ours(ppid) \
            and _get(pport, "/api/health", 1.0):
        reuse = pport
    elif _get(base, "/api/health", 1.0):
        reuse = base
    if reuse:
        url = f"http://127.0.0.1:{reuse}/"
        print(f"服务已在运行，无需重复启动：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    port = _free_port(base)
    if port is None:
        print(f"错误：{base}~{base + PORT_TRIES - 1} 端口全部被占用，无法启动。")
        return 1

    run_dir = os.path.dirname(_run_path())
    log = os.path.join(run_dir, "server.out.log")
    err = os.path.join(run_dir, "server.err.log")
    # 冻结态下 sys.executable 是本程序自身，没有模块可 -m 导入：改为用
    # --serve 让 exe 重新拉起自己，在子进程里前台跑服务器。
    frozen = getattr(sys, "frozen", False)
    root = (os.path.dirname(os.path.abspath(sys.executable)) if frozen
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    child = ([sys.executable, "--serve", "--port", str(port), "--no-browser"]
             if frozen else
             [sys.executable, "-m", "histsim.web.server", "--port", str(port),
              "--no-browser"])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    # 用 CREATE_NO_WINDOW 而不是 DETACHED_PROCESS：前者给服务一个「无窗口的控制台」，
    # 检索时的 mp.Pool worker 会继承它；用 DETACHED_PROCESS 则父进程完全没有控制台，
    # worker 是控制台程序、multiprocessing 又硬编码 CreateProcess(creationflags=0)，
    # 于是每个 worker 都会被分配一个新的可见控制台窗口——检索一开就满屏黑框。
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP \
        if os.name == "nt" else 0

    with open(log, "w", encoding="utf-8") as lo, \
            open(err, "w", encoding="utf-8") as le:
        proc = subprocess.Popen(
            child, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=lo,
            stderr=le, creationflags=flags)
    _write_run({"pid": proc.pid, "port": port, "log": log, "err": err,
                "started": time.strftime("%Y-%m-%d %H:%M:%S")})
    print(f"后台进程已拉起：pid {proc.pid} · 端口 {port}（已脱离本窗口，可关闭）")
    print(f"运行日志：{log}")
    print("等待服务就绪", end="", flush=True)

    health, deadline = None, time.time() + args.wait
    while time.time() < deadline:
        health = _get(port, "/api/health", 1.0)
        if health and health.get("ok"):
            break
        print(".", end="", flush=True)
        time.sleep(0.5)
    print()

    if not (health and health.get("ok")):
        print(f"错误：服务未在 {args.wait:g}s 内就绪，已回收进程 {proc.pid}。")
        detail = _tail(err) or _tail(log)
        if detail:
            print("-" * 62)
            print(detail)
            print("-" * 62)
        _kill_tree(proc.pid)
        return 1

    src = _get(port, "/api/sources", 3.0) or {}
    print("=" * 62)
    print(f"  历史相似检索系统 v{health.get('version', '?')}  ·  已完全启动")
    print("=" * 62)
    print(f"  访问地址   http://127.0.0.1:{port}/")
    print(f"  程序数据   {health.get('data_dir', '')}")
    print(f"  结果目录   {health.get('results_dir', '')}")
    if src:
        print(f"  已登记源   ERA5 {src.get('n_era5', 0)} 项 / GMCP {src.get('n_gmcp', 0)} 项"
              f" · {int(src.get('n_files') or 0):,} 个文件")
        if not src.get("sources"):
            print("  提示       尚未登记数据源，请在「数据源」页填写目录后扫描")
    print(f"  后台进程   pid {proc.pid}")
    print("-" * 62)
    stop_hint = ("运行「停止历史相似检索系统」快捷方式" if frozen
                 else "运行 完全暂停历史相似系统.bat（或 python -m histsim.cli stop）")
    print(f"  完全暂停：{stop_hint}")
    print("=" * 62)

    if not args.no_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return 0


def cmd_stop(args) -> int:
    """完全暂停：定位服务（运行记录 + 端口反查）→ 执行中任务保护 → 连子进程结束。"""
    run = _read_run()
    base = args.port or int(run.get("port") or DEFAULT_WEB_PORT)

    targets: Dict[int, int] = {}       # pid -> port
    rpid = int(run.get("pid") or 0)
    if rpid and _is_ours(rpid):
        targets[rpid] = int(run.get("port") or 0)
    # 兜底：用前台脚本或手工命令启动的服务没有运行记录，只能按端口找
    for pid, port in _listening_pids(range(base, base + PORT_TRIES)).items():
        if _is_ours(pid):
            targets.setdefault(pid, port)

    if not targets:
        print(f"没有正在运行的服务（运行记录无有效进程，端口 {base}~{base + PORT_TRIES - 1} "
              "也无 python 监听）。")
        if run:
            os.remove(_run_path())
            print("已清理失效的运行记录。")
        return 0

    print("发现正在运行的服务：")
    running = 0
    for pid, port in sorted(targets.items()):
        info = _get(port, "/api/health", 2.0) if port else None
        n = int((info or {}).get("running_tasks") or 0)
        running += n
        state = f"版本 {info.get('version')}" if info else "探活无响应"
        print(f"  - pid {pid:<8} 端口 {port or '—':<6} {state}"
              + (f" · 正在执行 {n} 个检索" if n else ""))

    if running and not args.force:
        print(f"\n有 {running} 个检索正在执行，强制暂停会丢失其未完成的进度"
              "（已完成的检索结果已落盘，不受影响）。")
        try:
            ans = input("仍要暂停吗？[y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("已取消，服务继续运行。")
            return 1

    failed = []
    for pid, port in sorted(targets.items()):
        killed = _kill_tree(pid)
        print(f"  {'已结束' if killed else '结束失败'} pid {pid}")
        if not killed:
            failed.append(pid)

    if os.path.isfile(_run_path()):
        os.remove(_run_path())

    # 复核：刚结束的端口必须真的不再响应
    left = [p for p in {p for p in targets.values() if p} if _get(p, "/api/health", 0.8)]
    if failed or left:
        print(f"\n警告：仍有残留（进程 {failed or '无'} · 端口 {left or '无'}），"
              "可用任务管理器按 pid 结束。")
        return 1

    print("\n服务已完全暂停：进程与 IO 子进程均已结束，端口已释放。")
    from .datasource import default_data_dir
    print(f"数据源登记、切片缓存与历史检索结果均保留在 {default_data_dir()} 下，"
          "重新启动即可恢复。")
    return 0


def _raw(args) -> dict:
    raw = {"template": args.template, "target_date": args.date}
    if args.span is not None:
        raw["half_span_deg"] = args.span
    if args.months:
        raw["months"] = [int(m) for m in args.months.split(",")]
    if args.years:
        a, b = args.years.split("-")
        raw["year_from"], raw["year_to"] = int(a), int(b)
    if args.times:
        raw["times"] = [int(t) for t in args.times.split(",")]
    if args.lat is not None:
        raw["center_lat"] = args.lat
        raw["point_lat"] = args.lat
    if args.lon is not None:
        raw["center_lon"] = args.lon
        raw["point_lon"] = args.lon
    return raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="histsim", description="历史相似检索系统 · 命令行")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="完全启动：后台拉起本地 Web 服务并等待就绪")
    p.add_argument("--port", type=int, default=0,
                   help=f"起始端口（默认 {DEFAULT_WEB_PORT}，被占用则顺延）")
    p.add_argument("--wait", type=float, default=60.0, help="等待就绪的秒数")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("stop", help="完全暂停：结束后台服务及其 IO 子进程")
    p.add_argument("--port", type=int, default=0,
                   help="端口反查的起始端口（默认沿用运行记录）")
    p.add_argument("--force", action="store_true",
                   help="有检索正在执行时也不询问，直接结束")
    p.set_defaults(func=cmd_stop)

    p = sub.add_parser("register", help="扫描并登记数据源目录")
    p.add_argument("root")
    p.add_argument("--depth", type=int, default=1)
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("dashboard", help="查看数据源与覆盖概况")
    p.set_defaults(func=cmd_dashboard)

    p = sub.add_parser("preprocess", help="GMCP 逐小时降水 → 北京时间累计降水产品")
    p.add_argument("--source-root", required=True, help="GMCP 逐小时源目录")
    p.add_argument("--output-root", required=True, help="预处理产品输出目录")
    p.add_argument("--groups", default="24h,12h,6h", help="产品分组，逗号分隔")
    p.add_argument("--year-from", type=int, default=2000)
    p.add_argument("--year-to", type=int, default=2024)
    p.add_argument("--overwrite", action="store_true", help="覆盖已有产品")
    p.add_argument("--register", action="store_true", help="完成后自动登记为数据源")
    p.set_defaults(func=cmd_preprocess)

    for name, fn in (("preview", cmd_preview), ("search", cmd_search)):
        p = sub.add_parser(name, help="代价预估" if name == "preview" else "执行检索")
        p.add_argument("--template", default="plan_A", choices=["plan_A", "plan_B"])
        p.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
        p.add_argument("--span", type=float, help="矩形半边长（度）")
        p.add_argument("--months", help="月份，逗号分隔，如 4,5,6")
        p.add_argument("--years", help="年份范围，如 2000-2024")
        p.add_argument("--times", help="UTC 时次，逗号分隔，如 0,6,12,18")
        p.add_argument("--lat", type=float, help="中心纬度")
        p.add_argument("--lon", type=float, help="中心经度")
        if name == "search":
            p.add_argument("--out", help="结果 JSON 输出路径")
        p.set_defaults(func=fn)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())