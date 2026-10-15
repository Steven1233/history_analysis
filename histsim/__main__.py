# -*- coding: utf-8 -*-
"""``python -m histsim`` 一键启动本地 Web 界面（等价于 histsim.web.server）。"""
import sys

from .web.server import main

if __name__ == "__main__":
    sys.exit(main())