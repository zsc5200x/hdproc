"""
兼容入口：原实现已迁移到 `src/hdzz/core/watcher/ag_watcher.py`。

目的：
- 保留 `app/ag_wmc_watch.py` 文件名，避免启动脚本 / `app/app.py` worker 调用方式变化。
- 自动把仓库根目录的 `src/` 加入 sys.path。
- 通过环境变量 `HDZZ_APP_DIR` 把真实 app 目录传给新实现（避免新模块误判 __file__ 所在目录）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_bootstrap import bootstrap_app_runtime


def _bootstrap() -> None:
    bootstrap_app_runtime(Path(__file__))


_bootstrap()

from hdzz.core.watcher import ag_watcher as _impl  # noqa: E402
from hdzz.core.watcher.ag_watcher import *  # noqa: F401,F403,E402


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
