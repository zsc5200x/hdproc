"""Build metadata embedded in bundled backends.

The source checkout has a deterministic fallback, while PyInstaller builds
embed ``hdzz-build-info.json`` next to the frozen application resources.  The
metadata contains no runtime configuration or credentials.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from hdzz import __version__


BUILD_INFO_FILENAME = "hdzz-build-info.json"


def _candidate_paths(explicit: Optional[Path] = None) -> list[Path]:
    paths: list[Path] = []
    if explicit is not None:
        paths.append(Path(explicit))
    env_path = str(os.environ.get("HDZZ_BUILD_INFO_PATH") or "").strip()
    if env_path:
        paths.append(Path(env_path))
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(str(meipass)) / BUILD_INFO_FILENAME)
    executable_dir = Path(sys.executable).resolve().parent
    paths.append(executable_dir / BUILD_INFO_FILENAME)
    return paths


def _fallback() -> Dict[str, str]:
    commit = str(os.environ.get("HDZZ_GIT_COMMIT") or "unknown").strip() or "unknown"
    build_id = str(os.environ.get("HDZZ_BUILD_ID") or "source").strip() or "source"
    return {
        "version": str(__version__),
        "git_commit": commit,
        "build_id": build_id,
        "source": "source",
    }


def load_build_info(path: Optional[Path] = None) -> Dict[str, str]:
    """Load only non-sensitive build identity fields."""

    fallback = _fallback()
    for candidate in _candidate_paths(path):
        try:
            payload: Any = json.loads(Path(candidate).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        result = dict(fallback)
        for key in ("version", "git_commit", "build_id", "source", "built_at"):
            value = payload.get(key)
            if value not in (None, ""):
                result[key] = str(value)
        result["source"] = str(result.get("source") or "embedded")
        return result
    return fallback


def current_build_info() -> Dict[str, str]:
    """Return a copy so callers cannot mutate the module-level metadata."""

    return dict(load_build_info())


__all__ = ["BUILD_INFO_FILENAME", "current_build_info", "load_build_info"]
