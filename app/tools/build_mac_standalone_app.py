from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from macos_icon import ensure_app_icon_icns


DEFAULT_BUNDLE_NAME = "AG Matrix Standalone"
DEFAULT_BUNDLE_ID = "com.hdproc.agmatrix.standalone"
DEFAULT_BUNDLE_PORT = 9999


def _chromium_revision_from_name(name: str) -> Optional[str]:
    m = re.fullmatch(r"chromium-(\d+)", str(name or ""))
    return m.group(1) if m else None


def _chromium_revision_from_executable(executable: Path) -> Optional[str]:
    for parent in Path(executable).parents:
        revision = _chromium_revision_from_name(parent.name)
        if revision:
            return revision
    return None


def macos_chromium_revisions(browsers_dir: Path) -> set[str]:
    root = Path(browsers_dir)
    if not root.is_dir():
        return set()
    pattern = "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    revisions = set()
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        revision = _chromium_revision_from_executable(path)
        if revision:
            revisions.add(revision)
    return revisions


def has_macos_chromium_browser(browsers_dir: Path, *, expected_revision: Optional[str] = None) -> bool:
    root = Path(browsers_dir)
    if not root.is_dir():
        return False
    revisions = macos_chromium_revisions(root)
    if not expected_revision:
        return bool(revisions)
    return str(expected_revision) in revisions


def detect_playwright_browsers_dir(
    source_root: Path,
    env: Optional[Mapping[str, str]] = None,
    *,
    expected_revision: Optional[str] = None,
) -> Path:
    env_map = dict(env or {})
    candidates: List[Path] = []
    rejected: List[Path] = []

    env_value = str(env_map.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if env_value and env_value not in {"0", "1"}:
        candidates.append(Path(env_value))

    candidates.extend(
        [
            source_root / "ms-playwright",
            source_root / "app" / ".run" / "ms-playwright",
        ]
    )

    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if has_macos_chromium_browser(candidate, expected_revision=expected_revision):
            return candidate.resolve()
        rejected.append(candidate)
    detail = ""
    if rejected:
        detail = "；已忽略非 macOS 浏览器目录：" + "，".join(str(path) for path in rejected)
        if expected_revision:
            detail = f"；需要 chromium-{expected_revision}" + detail
    raise FileNotFoundError(f"未找到可用的 macOS Playwright 浏览器目录（ms-playwright）{detail}。")


def _playwright_chromium_executable_path(build_python: Path, env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    probe = (
        "from playwright.sync_api import sync_playwright\n"
        "from pathlib import Path\n"
        "with sync_playwright() as p:\n"
        "    print(p.chromium.executable_path)\n"
    )
    try:
        out = subprocess.check_output(
            [str(build_python), "-c", probe],
            text=True,
            stderr=subprocess.DEVNULL,
            env=dict(env or os.environ),
        )
    except Exception:
        return None
    text = str(out or "").strip()
    return Path(text) if text else None


def expected_playwright_chromium_revision(build_python: Path, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    executable = _playwright_chromium_executable_path(build_python, env=env)
    if executable is None:
        return None
    return _chromium_revision_from_executable(executable)


def _copy_installed_macos_playwright_browsers(
    build_python: Path,
    target_dir: Path,
    *,
    env: Optional[Mapping[str, str]] = None,
    expected_revision: Optional[str] = None,
) -> bool:
    executable = _playwright_chromium_executable_path(build_python, env=env)
    if executable is None:
        return False
    chromium_dir = None
    for parent in executable.parents:
        if parent.name.startswith("chromium-"):
            chromium_dir = parent
            break
    if chromium_dir is None or not chromium_dir.is_dir():
        return False
    if expected_revision and _chromium_revision_from_name(chromium_dir.name) != str(expected_revision):
        return False
    cache_root = chromium_dir.parent
    if not cache_root.is_dir():
        return False

    target_dir.mkdir(parents=True, exist_ok=True)
    revision_suffix = chromium_dir.name.split("-", 1)[-1]
    names = [chromium_dir.name]
    for sibling in cache_root.iterdir():
        if sibling.name == f"chromium_headless_shell-{revision_suffix}" or sibling.name.startswith("ffmpeg-"):
            names.append(sibling.name)
    for name in names:
        src = cache_root / name
        dst = target_dir / name
        if dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dst)
    return has_macos_chromium_browser(target_dir, expected_revision=expected_revision)


_PLAYWRIGHT_PROFILE_NAMES = {
    "Cookies",
    "Cookies-journal",
    "Login Data",
    "Login Data-journal",
    "History",
    "History-journal",
    "Local Storage",
    "Session Storage",
    "GPUCache",
}


def _is_playwright_ffmpeg_name(name: str) -> bool:
    return bool(re.fullmatch(r"ffmpeg-\d+", str(name or "")))


def validate_macos_playwright_staging(
    browsers_dir: Path,
    *,
    expected_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate that a browser directory is safe to embed in a release.

    A developer Playwright cache may contain several browser revisions and
    persistent MCP/browser profiles.  Those are never valid application
    resources, so the staging directory is intentionally strict: only the
    selected Chromium revision, its matching headless shell, and ffmpeg roots
    are accepted.
    """

    root = Path(browsers_dir).resolve()
    if not root.is_dir():
        raise ValueError(f"Playwright staging 不是目录：{root}")
    revisions = macos_chromium_revisions(root)
    if expected_revision:
        expected = str(expected_revision)
        if revisions != {expected}:
            raise ValueError(
                f"Playwright staging revision 不匹配：需要 chromium-{expected}，实际为 {sorted(revisions) or '无'}"
            )
    elif len(revisions) != 1:
        raise ValueError(f"Playwright staging 必须只有一个 Chromium revision，实际为 {sorted(revisions)}")

    revision = str(expected_revision or next(iter(revisions)))
    allowed_roots = {
        f"chromium-{revision}",
        f"chromium_headless_shell-{revision}",
    }
    allowed_roots.update(path.name for path in root.iterdir() if _is_playwright_ffmpeg_name(path.name))
    unexpected = sorted(path.name for path in root.iterdir() if path.name not in allowed_roots)
    if unexpected:
        raise ValueError(f"Playwright staging 含未允许资源：{unexpected}")

    unsafe_paths: List[str] = []
    for path in root.rglob("*"):
        name = path.name
        if name.startswith("mcp-chrome") or name in _PLAYWRIGHT_PROFILE_NAMES:
            unsafe_paths.append(str(path.relative_to(root)))
    if unsafe_paths:
        raise ValueError(f"Playwright staging 含浏览器 profile/敏感运行数据：{unsafe_paths[:12]}")

    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_file():
            file_count += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return {
        "root": str(root),
        "revision": revision,
        "roots": sorted(allowed_roots),
        "file_count": file_count,
        "bytes": total_bytes,
    }


def stage_macos_playwright_browsers(
    source_dir: Path,
    target_dir: Path,
    *,
    expected_revision: Optional[str] = None,
) -> Path:
    """Copy only release-safe Playwright roots into a clean staging directory."""

    source = Path(source_dir).resolve()
    target = Path(target_dir).resolve()
    if source == target:
        validate_macos_playwright_staging(target, expected_revision=expected_revision)
        return target
    source_revisions = macos_chromium_revisions(source)
    revision = str(expected_revision or (next(iter(source_revisions)) if len(source_revisions) == 1 else ""))
    if not revision or revision not in source_revisions:
        raise ValueError(
            f"无法从 Playwright 缓存选择唯一 macOS Chromium revision：需要 {expected_revision or '单一 revision'}，"
            f"实际为 {sorted(source_revisions)}"
        )
    selected_names = [f"chromium-{revision}"]
    headless_name = f"chromium_headless_shell-{revision}"
    if (source / headless_name).exists():
        selected_names.append(headless_name)
    selected_names.extend(sorted(path.name for path in source.iterdir() if _is_playwright_ffmpeg_name(path.name)))

    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    for name in selected_names:
        src = source / name
        dst = target / name
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        elif src.is_file():
            shutil.copy2(src, dst)
    validate_macos_playwright_staging(target, expected_revision=revision)
    return target


def ensure_macos_playwright_browsers(
    source_root: Path,
    build_root: Path,
    build_python: Path,
    env: Optional[Mapping[str, str]] = None,
) -> Path:
    expected_revision = expected_playwright_chromium_revision(build_python, env=env)
    target_dir = (Path(build_root) / "ms-playwright-macos").resolve()

    # Reuse only a previously validated staging directory.  Never return the
    # developer's PLAYWRIGHT_BROWSERS_PATH directly: it can contain profiles
    # and multiple revisions unrelated to this build.
    if target_dir.is_dir():
        try:
            validate_macos_playwright_staging(target_dir, expected_revision=expected_revision)
            return target_dir
        except ValueError:
            shutil.rmtree(target_dir)

    source_dir: Optional[Path] = None
    try:
        source_dir = detect_playwright_browsers_dir(source_root, env=env, expected_revision=expected_revision)
    except FileNotFoundError:
        source_dir = None

    if source_dir is not None:
        try:
            return stage_macos_playwright_browsers(
                source_dir,
                target_dir,
                expected_revision=expected_revision,
            )
        except ValueError:
            # A matching Chromium executable may be discoverable from the
            # Python installation even when the repository candidate is not.
            pass

    if not _copy_installed_macos_playwright_browsers(
        build_python,
        target_dir,
        env=env,
        expected_revision=expected_revision,
    ):
        install_env = dict(env or os.environ)
        install_env["PLAYWRIGHT_BROWSERS_PATH"] = str(target_dir)
        subprocess.run([str(build_python), "-m", "playwright", "install", "chromium"], check=True, env=install_env)
    try:
        validate_macos_playwright_staging(target_dir, expected_revision=expected_revision)
    except ValueError as exc:
        raise FileNotFoundError(f"未能准备安全的 macOS Playwright staging：{target_dir}：{exc}") from exc
    return target_dir


def create_playwright_browsers_archive(build_root: Path, browsers_dir: Path) -> Path:
    archive_path = build_root / "bundle-assets" / "ms-playwright.tar.gz"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()
    stats = validate_macos_playwright_staging(browsers_dir)
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(str(browsers_dir), arcname="ms-playwright")
    print(
        "[INFO] Playwright staging: "
        f"revision={stats['revision']} files={stats['file_count']} bytes={stats['bytes']} archive={archive_path.stat().st_size}"
    )
    return archive_path


def resolve_bundle_icon_path(
    source_root: Path,
    build_root: Path,
    *,
    bundle_name: str,
    icon_source: Optional[Path] = None,
) -> Path:
    if icon_source is None:
        return ensure_app_icon_icns(source_root)
    icon_output = build_root / "icons" / f"{bundle_name}.icns"
    return ensure_app_icon_icns(source_root, icon_source=Path(icon_source), output_path=icon_output)


def resolve_bundle_splash_asset_path(
    build_root: Path,
    *,
    splash_source: Optional[Path] = None,
) -> Optional[Path]:
    if splash_source is None:
        return None
    src = Path(splash_source).resolve()
    if not src.is_file():
        raise FileNotFoundError(f"未找到启动首屏素材：{src}")
    out_dir = build_root / "bundle-assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"splash{src.suffix.lower() or '.png'}"
    shutil.copy2(src, out_path)
    return out_path


def prepare_telegram_credentials_seed(build_root: Path, credentials_source: Path) -> Path:
    source = Path(credentials_source).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"未找到 TG API 凭据：{source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("TG API 凭据格式错误")
    api_id = str(payload.get("api_id") or "").strip()
    api_hash = str(payload.get("api_hash") or "").strip()
    if not re.fullmatch(r"\d{5,12}", api_id) or int(api_id) <= 0:
        raise ValueError("TG api_id 无效")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", api_hash):
        raise ValueError("TG api_hash 无效")

    out_path = Path(build_root) / "bundle-assets" / "telegram_signal_credentials.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"api_id": api_id, "api_hash": api_hash}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out_path.chmod(0o600)
    return out_path


def build_spec_inputs(
    source_root: Path,
    browsers_dir: Path,
    *,
    bundle_name: str,
    icon_path: Optional[Path] = None,
    splash_asset_path: Optional[Path] = None,
    browsers_archive_path: Optional[Path] = None,
) -> Dict[str, Any]:
    app_dir = source_root / "app"
    resolved_icon = Path(icon_path).resolve() if icon_path is not None else ensure_app_icon_icns(source_root)
    browser_data = (
        (str(Path(browsers_archive_path).resolve()), ".")
        if browsers_archive_path is not None
        else (str(browsers_dir), "ms-playwright")
    )
    spec_inputs: Dict[str, Any] = {
        "pathex": [str(app_dir), str(source_root / "src"), str(source_root / "ook")],
        "datas": [
            (str(app_dir / "webui"), "app/webui"),
            (str(app_dir / "config.example.json"), "app"),
            (str(app_dir / "README.md"), "app"),
            browser_data,
        ],
        "hiddenimports": [
            "ag_wmc_watch",
            "webui_server",
            "member_arsenal_watch",
            "odds_snapshot_watch",
            "follow_engine",
            "telegram_bot",
            "telegram_signal_watch",
            "settlement_importer",
            "signal_forwarder",
            "signal_puller",
            "PySide6.QtCore",
            "PySide6.QtGui",
            "PySide6.QtWidgets",
            "PySide6.QtWebEngineCore",
            "PySide6.QtWebEngineWidgets",
        ],
        "collect_submodules": ["playwright"],
        "bundle_name": [bundle_name],
        "icon_path": str(resolved_icon),
        "build_requirements": ["PySide6"],
    }
    for preview_name in ("gdcg.jpg", "gdcc.jpg", "gdcg.jpeg", "gdcc.jpeg", "gdcg.png", "gdcc.png"):
        preview_path = source_root / preview_name
        if preview_path.is_file():
            spec_inputs["datas"].append((str(preview_path), "."))
    config_json = app_dir / "config.json"
    if config_json.is_file():
        spec_inputs["datas"].append((str(config_json), "app"))
    puller_state_seed = app_dir / "runtime_seeds" / "ook_signal_puller_state.json"
    if puller_state_seed.is_file():
        spec_inputs["datas"].append((str(puller_state_seed), "app/runtime_seeds"))
    tg_auth_seed = app_dir / "data" / "tg_auth.json"
    if tg_auth_seed.is_file():
        spec_inputs["datas"].append((str(tg_auth_seed), "app/data"))
    if splash_asset_path is not None:
        spec_inputs["datas"].append((str(Path(splash_asset_path).resolve()), "bundle_assets"))
    return spec_inputs


def ensure_build_venv(build_root: Path, requirements_file: Path, python_bin: str, extra_packages: List[str]) -> Path:
    venv_dir = build_root / "venv"
    python_path = venv_dir / "bin" / "python3"
    if not python_path.exists():
        subprocess.run([python_bin, "-m", "venv", str(venv_dir)], check=True)

    install_stamp = build_root / ".install.stamp"
    needs_install = not install_stamp.exists() or requirements_file.stat().st_mtime > install_stamp.stat().st_mtime
    if not needs_install and extra_packages:
        check_code = (
            "import importlib.util, sys\n"
            f"mods = {repr(extra_packages)}\n"
            "missing = [m for m in mods if importlib.util.find_spec(m) is None]\n"
            "raise SystemExit(0 if not missing else 1)\n"
        )
        probe = subprocess.run([str(python_path), "-c", check_code], check=False)
        needs_install = probe.returncode != 0
    if needs_install:
        subprocess.run([str(python_path), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], check=True)
        subprocess.run([str(python_path), "-m", "pip", "install", "pyinstaller"], check=True)
        subprocess.run([str(python_path), "-m", "pip", "install", "-r", str(requirements_file)], check=True)
        if extra_packages:
            subprocess.run([str(python_path), "-m", "pip", "install", *extra_packages], check=True)
        install_stamp.write_text("ok\n", encoding="utf-8")
    return python_path


def build_pyinstaller_command(
    build_python: Path,
    spec_path: Path,
    output_root: Path,
    work_root: Path,
) -> List[str]:
    return [
        str(build_python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(output_root),
        "--workpath",
        str(work_root),
        str(spec_path),
    ]


def build_bundle_info_plist(
    *,
    bundle_name: str,
    runtime_app_name: str,
    default_port: int,
    splash_asset_rel_path: str = "",
) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "CFBundleDisplayName": str(bundle_name),
        "CFBundleName": str(bundle_name),
        "HDZZRuntimeAppName": str(runtime_app_name),
        "HDZZDefaultPort": int(default_port),
        "NSHighResolutionCapable": True,
    }
    if str(splash_asset_rel_path).strip():
        info["HDZZSplashAssetRelPath"] = str(splash_asset_rel_path).strip()
    return info


def update_bundle_metadata(
    bundle_path: Path,
    *,
    runtime_app_name: str,
    default_port: int,
    splash_asset_rel_path: str = "",
) -> None:
    info_path = Path(bundle_path) / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise FileNotFoundError(f"未找到 Info.plist：{info_path}")
    with info_path.open("rb") as fh:
        info = plistlib.load(fh)
    if not isinstance(info, dict):
        info = {}
    info["HDZZRuntimeAppName"] = str(runtime_app_name)
    info["HDZZDefaultPort"] = int(default_port)
    if str(splash_asset_rel_path).strip():
        info["HDZZSplashAssetRelPath"] = str(splash_asset_rel_path).strip()
    with info_path.open("wb") as fh:
        plistlib.dump(info, fh)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建独立的 Apple Silicon macOS .app（无需目标机器安装 Python）")
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parents[2] / "_dist"))
    parser.add_argument("--bundle-name", default=DEFAULT_BUNDLE_NAME)
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID)
    parser.add_argument("--runtime-app-name", default="")
    parser.add_argument("--default-port", type=int, default=DEFAULT_BUNDLE_PORT)
    parser.add_argument("--icon-source", default="")
    parser.add_argument("--splash-source", default="")
    parser.add_argument("--telegram-credentials-source", default="")
    parser.add_argument("--python-bin", default=sys.executable)
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_root = Path(args.output_dir).resolve()
    build_root = source_root / ".build" / "mac-standalone"
    requirements_file = source_root / "app" / "requirements.txt"
    spec_path = source_root / "app" / "tools" / "hdzz_mac_standalone.spec"

    output_root.mkdir(parents=True, exist_ok=True)
    build_root.mkdir(parents=True, exist_ok=True)

    if not requirements_file.is_file():
        raise FileNotFoundError(f"未找到 requirements.txt: {requirements_file}")
    if not spec_path.is_file():
        raise FileNotFoundError(f"未找到 PyInstaller spec: {spec_path}")

    icon_source = Path(str(args.icon_source).strip()).resolve() if str(args.icon_source).strip() else None
    splash_source = Path(str(args.splash_source).strip()).resolve() if str(args.splash_source).strip() else icon_source
    icon_path = resolve_bundle_icon_path(
        source_root,
        build_root,
        bundle_name=str(args.bundle_name),
        icon_source=icon_source,
    )
    splash_asset_path = resolve_bundle_splash_asset_path(build_root, splash_source=splash_source)
    telegram_credentials_seed = (
        prepare_telegram_credentials_seed(build_root, Path(args.telegram_credentials_source))
        if str(args.telegram_credentials_source).strip()
        else None
    )
    runtime_app_name = str(args.runtime_app_name).strip() or str(args.bundle_name)
    splash_asset_rel_path = (
        f"bundle_assets/{Path(splash_asset_path).name}" if splash_asset_path is not None else ""
    )
    info_plist = build_bundle_info_plist(
        bundle_name=str(args.bundle_name),
        runtime_app_name=runtime_app_name,
        default_port=int(args.default_port),
        splash_asset_rel_path=splash_asset_rel_path,
    )
    build_python = ensure_build_venv(
        build_root,
        requirements_file,
        args.python_bin,
        extra_packages=["PySide6"],
    )
    browsers_dir = ensure_macos_playwright_browsers(source_root, build_root, build_python, env=os.environ)
    browsers_archive_path = create_playwright_browsers_archive(build_root, browsers_dir)
    spec_inputs = build_spec_inputs(
        source_root,
        browsers_dir,
        bundle_name=args.bundle_name,
        icon_path=icon_path,
        splash_asset_path=splash_asset_path,
        browsers_archive_path=browsers_archive_path,
    )

    env = dict(os.environ)
    env["HDZZ_SOURCE_ROOT"] = str(source_root)
    env["HDZZ_PLAYWRIGHT_BROWSERS_DIR"] = str(browsers_dir)
    env["HDZZ_PLAYWRIGHT_BROWSERS_ARCHIVE"] = str(browsers_archive_path)
    env["HDZZ_BUNDLE_NAME"] = str(args.bundle_name)
    env["HDZZ_BUNDLE_ID"] = str(args.bundle_id)
    env["HDZZ_APP_ICON_PATH"] = spec_inputs["icon_path"]
    env["HDZZ_INFO_PLIST_JSON"] = json.dumps(info_plist, ensure_ascii=False)
    if telegram_credentials_seed is not None:
        env["HDZZ_TELEGRAM_CREDENTIALS_SEED"] = str(telegram_credentials_seed)

    work_root = build_root / "pyinstaller-work"
    cmd = build_pyinstaller_command(build_python, spec_path, output_root, work_root)
    subprocess.run(cmd, check=True, env=env)

    bundle_path = output_root / f"{args.bundle_name}.app"
    print(f"[INFO] 已生成独立应用：{bundle_path}")
    print("[INFO] 朋友电脑无需额外安装 Python，即可双击运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
