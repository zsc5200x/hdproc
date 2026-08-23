"""
兼容入口：原实现已迁移到 `src/hdzz/core/follower/engine.py`。

目的：
- 保留 `app/follow_engine.py` 文件名，避免启动脚本 / `app/app.py` worker 调用方式变化。
- 未显式设置 PYTHONPATH 时，也能自动找到仓库根目录的 `src/`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_bootstrap import bootstrap_app_runtime


def _ensure_src_on_syspath() -> None:
    bootstrap_app_runtime(Path(__file__))


_ensure_src_on_syspath()

from hdzz.core.follower import engine as _impl  # noqa: E402
from hdzz.core.follower.engine import *  # noqa: F401,F403,E402


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
