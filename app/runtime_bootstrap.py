from __future__ import annotations

import os
import sys
from pathlib import Path


def _set_sys_path(path: Path, *, prepend: bool = True) -> None:
    text = str(path.resolve())
    if not path.exists():
        return
    while text in sys.path:
        sys.path.remove(text)
    if prepend:
        sys.path.insert(0, text)
    else:
        sys.path.append(text)


def bootstrap_app_runtime(default_file: Path) -> Path:
    file_path = Path(default_file).resolve()
    app_dir = file_path.parent
    repo_root = file_path.parents[1]

    portable_site = repo_root / "python" / "Lib" / "site-packages"
    venv_lib = app_dir / ".venv" / "lib"

    # On development machines the local venv contains platform-specific
    # extensions (for example greenlet on macOS). Prefer it over the bundled
    # portable site-packages, which may target a different OS/Python ABI.
    if venv_lib.exists():
        for site_dir in sorted(venv_lib.glob("python*/site-packages"), reverse=True):
            _set_sys_path(site_dir)
    _set_sys_path(portable_site, prepend=not venv_lib.exists())

    _set_sys_path(repo_root / "src")
    os.environ.setdefault("HDZZ_APP_DIR", str(app_dir))
    return app_dir
