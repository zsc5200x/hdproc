import os
import stat
import sys
from pathlib import Path
from typing import Dict, Optional, Union


def _is_special_playwright_browsers_path(value: str) -> bool:
    # Playwright supports special values like "0" to use its driver default.
    v = str(value or "").strip()
    return v in ("0", "1")


def repair_playwright_browsers_permissions(browsers_root: Union[Path, str]) -> bool:
    """
    macOS 便携包场景下，浏览器二进制可能丢失可执行位（EACCES）。
    这里做一次 best-effort 自愈，避免 relogin 启动浏览器直接失败。
    """
    if sys.platform != "darwin":
        return False
    try:
        root = Path(browsers_root)
    except Exception:
        return False
    if not root.is_dir():
        return False

    changed = False
    patterns = [
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/Frameworks/Google Chrome for Testing Framework.framework/Versions/*/Google Chrome for Testing Framework",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/Frameworks/Google Chrome for Testing Framework.framework/Versions/*/Helpers/*",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/Frameworks/Google Chrome for Testing Framework.framework/Versions/*/Libraries/*.dylib",
        "chromium_headless_shell-*/chrome-headless-shell-mac*/chrome-headless-shell",
        "ffmpeg-*/ffmpeg-mac",
    ]
    for pat in patterns:
        for p in root.glob(pat):
            try:
                if not p.is_file():
                    continue
                mode = stat.S_IMODE(p.stat().st_mode)
                want = mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                if want != mode:
                    p.chmod(want)
                    changed = True
            except Exception:
                continue
    return changed


def detect_playwright_browsers_path(app_root: Path) -> Optional[str]:
    """
    Best-effort detection for PLAYWRIGHT_BROWSERS_PATH.

    Dev layout:
      hdydf/ (app_root) + %LOCALAPPDATA%/ms-playwright

    Portable layout:
      <bundle>/app (app_root)
      <bundle>/ms-playwright
    """
    env_val = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_val:
        if _is_special_playwright_browsers_path(env_val):
            return env_val
        try:
            if Path(env_val).is_dir():
                return env_val
        except Exception:
            pass

    # Prefer the per-app runtime cache first (used by macOS launcher):
    #   <bundle>/app/.run/ms-playwright
    # Fallbacks:
    #   <bundle>/app/ms-playwright
    #   <bundle>/ms-playwright
    candidates = [
        app_root / ".run" / "ms-playwright",
        app_root / "ms-playwright",
        app_root.parent / "ms-playwright",
    ]
    local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "ms-playwright")

    for p in candidates:
        try:
            if p.is_dir():
                return str(p)
        except Exception:
            continue
    return None


def apply_playwright_browsers_path(env: Dict[str, str], app_root: Path) -> Optional[str]:
    """
    Apply PLAYWRIGHT_BROWSERS_PATH into an env dict (subprocess-safe).
    Returns the resolved value (or None).
    """
    existing = (env.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if existing:
        if _is_special_playwright_browsers_path(existing):
            return existing
        try:
            if Path(existing).is_dir():
                return existing
        except Exception:
            pass

    resolved = detect_playwright_browsers_path(app_root)
    if resolved:
        env["PLAYWRIGHT_BROWSERS_PATH"] = resolved
        repair_playwright_browsers_permissions(resolved)
    return resolved


def ensure_playwright_browsers_path(app_root: Path) -> Optional[str]:
    """
    Ensure current process has a usable PLAYWRIGHT_BROWSERS_PATH.
    Returns the resolved value (or None).
    """
    resolved = detect_playwright_browsers_path(app_root)
    if resolved and (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip() != resolved:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = resolved
    if resolved:
        repair_playwright_browsers_permissions(resolved)
    return resolved
