"""Build the macOS arm64 headless backend resource for the Tauri POC."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import re
from pathlib import Path


def _chromium_revision_from_path(path: Path) -> str | None:
    for parent in Path(path).parents:
        match = re.fullmatch(r"chromium-(\d+)", parent.name)
        if match:
            return match.group(1)
    return None


def _browser_revisions(browsers_dir: Path) -> set[str]:
    pattern = "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    revisions: set[str] = set()
    for executable in browsers_dir.glob(pattern):
        if executable.is_file():
            revision = _chromium_revision_from_path(executable)
            if revision:
                revisions.add(revision)
    return revisions


def _expected_playwright_revision(python: Path) -> str | None:
    probe = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p: print(p.chromium.executable_path)\n"
    )
    try:
        output = subprocess.check_output([str(python), "-c", probe], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    return _chromium_revision_from_path(Path(output.strip()))


def build(source_root: Path, *, python: Path, output_dir: Path, browsers_dir: Path | None = None) -> Path:
    source_root = Path(source_root).resolve()
    python_value = str(python)
    python_path = shutil.which(python_value) if not Path(python_value).is_file() else python_value
    # Keep the venv launcher path instead of resolving its symlink to the
    # system interpreter; this preserves the selected build environment when
    # PYTHONNOUSERSITE is enabled for reproducible bundles.
    python = Path(python_path or python_value)
    output_dir = Path(output_dir).resolve()
    if not python.is_file():
        raise FileNotFoundError(f"未找到构建 Python: {python}")
    if browsers_dir is None:
        from build_mac_standalone_app import ensure_macos_playwright_browsers

        browsers_dir = ensure_macos_playwright_browsers(
            source_root,
            output_dir / "_browser-build",
            python,
            env=os.environ,
        )
    browsers_dir = Path(browsers_dir).resolve()
    if not browsers_dir.is_dir():
        raise FileNotFoundError(f"未找到 macOS Playwright 浏览器: {browsers_dir}")
    expected_revision = _expected_playwright_revision(python)
    available_revisions = _browser_revisions(browsers_dir)
    if expected_revision and expected_revision not in available_revisions:
        raise RuntimeError(
            "Playwright Python 与浏览器 revision 不匹配："
            f"Python 需要 chromium-{expected_revision}，目录提供 {sorted(available_revisions) or '无 macOS Chromium'}"
        )

    build_root = output_dir / "_pyinstaller"
    dist_dir = output_dir / "backend"
    build_root.mkdir(parents=True, exist_ok=True)
    dist_dir.parent.mkdir(parents=True, exist_ok=True)
    if dist_dir.exists():
        shutil.rmtree(dist_dir)

    archive_path = output_dir / "_browser-build" / "ms-playwright.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(str(browsers_dir), arcname="ms-playwright")

    env = dict(os.environ)
    env.update(
        {
            "HDZZ_SOURCE_ROOT": str(source_root),
            "HDZZ_PLAYWRIGHT_BROWSERS_DIR": str(browsers_dir),
            "HDZZ_PLAYWRIGHT_BROWSERS_ARCHIVE": str(archive_path),
        }
    )
    spec = source_root / "app" / "tools" / "hdzz_tauri_backend.spec"
    subprocess.run(
        [
            str(python),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_dir),
            "--workpath",
            str(build_root),
            str(spec),
        ],
        cwd=str(source_root),
        env=env,
        check=True,
    )
    result = dist_dir / "hdzz-backend"
    if not result.is_dir() or not (result / "hdzz-backend").is_file():
        raise RuntimeError(f"PyInstaller 未生成 sidecar: {result}")
    config_candidates = list(result.rglob("config.json"))
    if config_candidates:
        raise RuntimeError(f"安全检查失败，bundle 包含 config.json: {config_candidates}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "experiments/hd-push-tauri-preview/src-tauri/resources",
    )
    parser.add_argument("--browsers-dir", type=Path)
    args = parser.parse_args(argv)
    result = build(args.source_root, python=args.python, output_dir=args.output_dir, browsers_dir=args.browsers_dir)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
