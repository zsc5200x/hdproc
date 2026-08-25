# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


SOURCE_ROOT = Path(os.environ["HDZZ_SOURCE_ROOT"]).resolve()
BROWSERS_DIR = Path(os.environ["HDZZ_PLAYWRIGHT_BROWSERS_DIR"]).resolve()
BROWSERS_ARCHIVE = os.environ.get("HDZZ_PLAYWRIGHT_BROWSERS_ARCHIVE", "").strip()
BUILD_INFO_PATH = os.environ.get("HDZZ_BUILD_INFO_PATH", "").strip()
APP_DIR = SOURCE_ROOT / "app"
SRC_DIR = SOURCE_ROOT / "src"
OOK_DIR = SOURCE_ROOT / "ook"
ENTRY_SCRIPT = APP_DIR / "tauri_backend_entry.py"


def production_webui_datas():
    """Collect only runtime WebUI assets, never tests or editor backups."""

    source = APP_DIR / "webui"
    output = []
    excluded_markers = (".bak", ".backup", ".ai_bak")
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if path.name == ".DS_Store" or path.name.endswith(".test.js"):
            continue
        if any(part == ".ux_bak" for part in relative.parts):
            continue
        if any(marker in path.name for marker in excluded_markers):
            continue
        target = Path("app/webui") / relative.parent
        output.append((str(path), str(target)))
    return output


datas = [
    *production_webui_datas(),
    (str(APP_DIR / "tauri_config.example.json"), "app"),
]
if BROWSERS_ARCHIVE:
    datas.append((str(Path(BROWSERS_ARCHIVE).resolve()), "."))
else:
    datas.append((str(BROWSERS_DIR), "ms-playwright"))
if BUILD_INFO_PATH:
    datas.append((str(Path(BUILD_INFO_PATH).resolve()), "."))

hiddenimports = [
    "app",
    "ag_wmc_watch",
    "webui_server",
    "member_arsenal_watch",
    "odds_snapshot_watch",
    "settlement_importer",
    "follow_engine",
    "telegram_bot",
    "telegram_signal_watch",
    "signal_forwarder",
    "signal_puller",
] + collect_submodules("playwright") + collect_submodules("hdzz")

a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(APP_DIR), str(SRC_DIR), str(OOK_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="hdzz-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="hdzz-backend",
)
