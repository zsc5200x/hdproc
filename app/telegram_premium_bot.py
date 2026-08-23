"""
独立付费信号机器人入口。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_dir = repo_root / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    os.environ.setdefault("HDZZ_APP_DIR", str(Path(__file__).resolve().parent))


_bootstrap()

from hdzz.api.telegram.premium_bot import *  # noqa: F401,F403


if __name__ == "__main__":
    raise SystemExit(main())

