from __future__ import annotations

import argparse
import plistlib
import shutil
import stat
from pathlib import Path
from typing import Iterable, Optional

from macos_icon import APP_ICON_NAME, ensure_app_icon_icns


DEFAULT_BUNDLE_NAME = "HD跟单系统v1.0"
DEFAULT_BUNDLE_ID = "com.hdproc.agmatrix"
DEFAULT_VERSION = "1.0.0"
PAYLOAD_DIRNAME = "app_payload"
INCLUDED_TOP_LEVEL = ("app", "scripts", "src", "ook", "ps3838", "README_PORTABLE.txt")
EXCLUDED_RELATIVE_PREFIXES = (
    Path("app/.venv"),
    Path("app/.run"),
    Path("app/orders"),
    Path("app/state"),
    Path("ps3838/.venv"),
    Path("ps3838/venv"),
    Path("ps3838/.run"),
    Path("ps3838/data"),
    Path("ps3838/var"),
    Path("ps3838/crown/data"),
    Path("ps3838/crown/.run"),
    Path("ps3838/crown/.venv"),
    Path("ps3838/crown/venv"),
)
EXCLUDED_NAMES = {
    ".DS_Store",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def should_exclude(rel_path: Path) -> bool:
    if not rel_path.parts:
        return False

    if any(part in EXCLUDED_NAMES for part in rel_path.parts):
        return True

    if rel_path.suffix in EXCLUDED_SUFFIXES:
        return True

    if any(_is_relative_to(rel_path, prefix) for prefix in EXCLUDED_RELATIVE_PREFIXES):
        return True

    # Release bundles must never carry live credentials or runtime state.
    if rel_path == Path("app/config.json") or rel_path == Path("ps3838/config.json"):
        return True
    if _is_relative_to(rel_path, Path("app/data")):
        return True

    if rel_path.parts[:2] == ("cc", "node_modules"):
        return True

    if rel_path.parts[:1] == ("data",) and len(rel_path.parts) > 1 and rel_path.parts[1] == "logs":
        return True

    name = rel_path.name
    return False


def _iter_included_entries(source_root: Path) -> Iterable[Path]:
    for name in INCLUDED_TOP_LEVEL:
        path = source_root / name
        if path.exists():
            yield path


def _copy_entry(source_root: Path, src: Path, dst: Path) -> None:
    rel_path = src.relative_to(source_root)
    if should_exclude(rel_path):
        return

    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_entry(source_root, child, dst / child.name)
        return

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def write_info_plist(
    plist_path: Path,
    *,
    bundle_name: str,
    bundle_id: str,
    version: str,
    icon_file: Optional[str] = None,
) -> None:
    info = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleDisplayName": bundle_name,
        "CFBundleExecutable": bundle_name,
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": bundle_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
    }
    if icon_file:
        info["CFBundleIconFile"] = icon_file
    plist_path.write_bytes(plistlib.dumps(info))


def write_launcher(launcher_path: Path, *, bundle_name: str) -> None:
    launcher = f"""#!/usr/bin/env bash
set -euo pipefail

CONTENTS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PAYLOAD_ROOT="${{CONTENTS_DIR}}/Resources/{PAYLOAD_DIRNAME}"
START_SCRIPT="${{PAYLOAD_ROOT}}/scripts/Start_AG_Matrix_ALL.command"

if [[ ! -f "${{START_SCRIPT}}" ]]; then
  echo "[ERROR] {bundle_name} 缺少启动脚本：${{START_SCRIPT}}" >&2
  exit 1
fi

exec bash "${{START_SCRIPT}}" "$@"
"""
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_mac_app_bundle(
    source_root: Path,
    output_root: Path,
    *,
    bundle_name: str = DEFAULT_BUNDLE_NAME,
    bundle_id: str = DEFAULT_BUNDLE_ID,
    version: str = DEFAULT_VERSION,
) -> Path:
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()

    if not (source_root / "app" / "app.py").is_file():
        raise FileNotFoundError(f"未找到应用入口：{source_root / 'app' / 'app.py'}")
    if not (source_root / "scripts" / "Start_AG_Matrix_ALL.command").is_file():
        raise FileNotFoundError(
            f"未找到 macOS 启动脚本：{source_root / 'scripts' / 'Start_AG_Matrix_ALL.command'}"
        )

    bundle_root = output_root / f"{bundle_name}.app"
    contents_dir = bundle_root / "Contents"
    macos_dir = contents_dir / "MacOS"
    resources_dir = contents_dir / "Resources"
    payload_root = resources_dir / PAYLOAD_DIRNAME

    if bundle_root.exists():
        shutil.rmtree(bundle_root)

    macos_dir.mkdir(parents=True, exist_ok=True)
    payload_root.mkdir(parents=True, exist_ok=True)

    icon_path = ensure_app_icon_icns(source_root)
    shutil.copy2(icon_path, resources_dir / APP_ICON_NAME)

    for entry in _iter_included_entries(source_root):
        _copy_entry(source_root, entry, payload_root / entry.relative_to(source_root))

    write_info_plist(
        contents_dir / "Info.plist",
        bundle_name=bundle_name,
        bundle_id=bundle_id,
        version=version,
        icon_file=APP_ICON_NAME,
    )
    write_launcher(macos_dir / bundle_name, bundle_name=bundle_name)
    return bundle_root


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可双击启动的 macOS .app 包")
    parser.add_argument(
        "--source-root",
        default=str(Path(__file__).resolve().parents[2]),
        help="项目根目录，默认使用当前仓库根目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[2] / "_dist"),
        help="输出目录，默认写入仓库 _dist/",
    )
    parser.add_argument("--bundle-name", default=DEFAULT_BUNDLE_NAME, help="应用名，例如 HD跟单系统v1.0")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID, help="Bundle Identifier")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="应用版本号")
    args = parser.parse_args()

    bundle_path = build_mac_app_bundle(
        Path(args.source_root),
        Path(args.output_dir),
        bundle_name=args.bundle_name,
        bundle_id=args.bundle_id,
        version=args.version,
    )
    print(f"[INFO] 已生成：{bundle_path}")
    print(f"[INFO] 可将该 .app 拖入 Applications，然后双击启动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
