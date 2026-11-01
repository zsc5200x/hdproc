"""兼容入口：启动 src/hdzz/core/watcher/ps3838_watch.py。"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_bootstrap import bootstrap_app_runtime

bootstrap_app_runtime(Path(__file__))

from hdzz.core.watcher import ps3838_watch as _impl  # noqa: E402


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
