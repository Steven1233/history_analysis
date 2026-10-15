# -*- coding: utf-8 -*-
"""历史相似检索系统 —— 统一检索内核。

对外主入口是 :class:`histsim.api.SearchSession`；本包同时被
``histsim.web.server``（本地 Web 界面）与 CLI 复用。

这里用惰性导入（PEP 562），使各子模块可独立自检而不必先构建完整包。
"""
__version__ = "2.0.0"

__all__ = ["SearchSession", "__version__"]


def __getattr__(name):
    if name == "SearchSession":
        from .api import SearchSession
        return SearchSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")