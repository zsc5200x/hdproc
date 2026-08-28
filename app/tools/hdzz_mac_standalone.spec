# -*- mode: python ; coding: utf-8 -*-

import json
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


SOURCE_ROOT = Path(os.environ["HDZZ_SOURCE_ROOT"]).resolve()
BROWSERS_DIR = Path(os.environ["HDZZ_PLAYWRIGHT_BROWSERS_DIR"]).resolve()
BROWSERS_ARCHIVE = os.environ.get("HDZZ_PLAYWRIGHT_BROWSERS_ARCHIVE", "").strip()
APP_NAME = os.environ.get("HDZZ_BUNDLE_NAME", "HD跟单系统v1.0")
BUNDLE_ID = os.environ.get("HDZZ_BUNDLE_ID", "com.hdproc.agmatrix.standalone")
APP_ICON_PATH = os.environ.get("HDZZ_APP_ICON_PATH") or None
INFO_PLIST_JSON = os.environ.get("HDZZ_INFO_PLIST_JSON", "").strip()
TELEGRAM_CREDENTIALS_SEED = os.environ.get("HDZZ_TELEGRAM_CREDENTIALS_SEED", "").strip()
APP_INFO_PLIST = json.loads(INFO_PLIST_JSON) if INFO_PLIST_JSON else None

APP_DIR = SOURCE_ROOT / "app"
SRC_DIR = SOURCE_ROOT / "src"
OOK_DIR = SOURCE_ROOT / "ook"
ENTRY_SCRIPT = APP_DIR / "mac_standalone_entry.py"

datas = [
    (str(APP_DIR / "webui"), "app/webui"),
    (str(APP_DIR / "config.example.json"), "app"),
    (str(APP_DIR / "README.md"), "app"),
]
if BROWSERS_ARCHIVE:
    datas.append((str(Path(BROWSERS_ARCHIVE).resolve()), "."))
else:
    datas.append((str(BROWSERS_DIR), "ms-playwright"))
if (APP_DIR / "config.json").is_file():
    datas.append((str(APP_DIR / "config.json"), "app"))
if (APP_DIR / "runtime_seeds" / "ook_signal_puller_state.json").is_file():
    datas.append((str(APP_DIR / "runtime_seeds" / "ook_signal_puller_state.json"), "app/runtime_seeds"))
if (APP_DIR / "data" / "tg_auth.json").is_file():
    datas.append((str(APP_DIR / "data" / "tg_auth.json"), "app/data"))
if TELEGRAM_CREDENTIALS_SEED:
    datas.append((str(Path(TELEGRAM_CREDENTIALS_SEED).resolve()), "app/runtime_seeds"))
for preview_name in ("gdcg.jpg", "gdcc.jpg", "gdcg.jpeg", "gdcc.jpeg", "gdcg.png", "gdcc.png"):
    preview_path = SOURCE_ROOT / preview_name
    if preview_path.is_file():
        datas.append((str(preview_path), "."))

hiddenimports = [
    "ag_wmc_watch",
    "webui_server",
    "member_arsenal_watch",
    "odds_snapshot_watch",
    "follow_engine",
    "telegram_bot",
    "telegram_signal_watch",
    "settlement_importer",
    "tools.hd_network_telemetry_sampler",
    "signal_forwarder",
    "signal_puller",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
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
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon=APP_ICON_PATH,
    bundle_identifier=BUNDLE_ID,
    info_plist=APP_INFO_PLIST,
)
