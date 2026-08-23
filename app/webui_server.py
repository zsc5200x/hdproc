"""
兼容入口：原实现已迁移到 `src/hdzz/api/webui/server.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_bootstrap import bootstrap_app_runtime


def _bootstrap() -> None:
    bootstrap_app_runtime(Path(__file__))


_bootstrap()

from hdzz.api.webui import server as _impl  # noqa: E402
from hdzz.api.webui.server import *  # noqa: F401,F403,E402


def main(argv=None):
    old_argv = list(sys.argv)
    try:
        if argv is not None:
            sys.argv = [old_argv[0]] + list(argv)
        return _impl.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
