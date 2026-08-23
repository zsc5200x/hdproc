"""Telegram 来源群信号监听兼容入口。"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_bootstrap import bootstrap_app_runtime


bootstrap_app_runtime(Path(__file__))

from hdzz.core.telegram_signal import watcher as _impl  # noqa: E402


def main(argv=None):
    return _impl.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
