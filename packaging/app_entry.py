# -*- coding: utf-8 -*-
"""打包后的程序入口（PyInstaller 的入口脚本，不参与源码运行流程）。

用法：
    双击 exe（无参数）  启动本地服务并打开浏览器，等价于 ``python -m histsim.cli serve``
    exe --stop          停止后台服务，等价于 ``python -m histsim.cli stop``
    exe --serve ...     内部用法：由 cli.cmd_serve 拉起，在子进程里前台跑服务器。
                        冻结后没有 ``-m histsim.web.server`` 可用，只能让 exe 调自己。

说明：命令行接口仍然全部保留在 ``histsim.cli`` 里，这里只做一层参数分发，
避免冻结态与源码态出现两套行为。
"""
from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    argv = list(sys.argv[1:])

    if argv and argv[0] == "--serve":
        from histsim.web.server import main as server_main
        return server_main(argv[1:])

    from histsim.cli import main as cli_main
    for flag, cmd in (("--stop", "stop"), ("--dashboard", "dashboard")):
        if argv and argv[0] == flag:
            return cli_main([cmd] + argv[1:])
    return cli_main(["serve"] + argv)


if __name__ == "__main__":
    # 必须在任何业务代码之前调用：冻结态下 multiprocessing 用 spawn 拉起子进程时
    # 会带 ``--multiprocessing-fork`` 重新执行本 exe，freeze_support() 负责截住这
    # 类调用并就地跑 worker；少了它，这些参数会落到 argparse 上，所有 IO 子进程
    # 一启动就报「unrecognized arguments」而全数失败。
    multiprocessing.freeze_support()
    sys.exit(main())