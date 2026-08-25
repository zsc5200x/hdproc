"""Headless bundled backend supervisor used by the Tauri macOS POC.

This module deliberately has no GUI imports.  It prepares a writable runtime
directory, starts the existing ``app.py`` supervisor, and keeps an ownership
lock for the lifetime of the backend supervisor process.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import fcntl
import importlib
import inspect
import json
import os
import re
import shutil
import signal
import sys
import tarfile
import time
from urllib import request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from hdzz.build_info import current_build_info
except ModuleNotFoundError:  # source-mode direct invocation from app/
    _SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
    if str(_SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(_SOURCE_ROOT))
    from hdzz.build_info import current_build_info


DEFAULT_RUNTIME_NAME = "HD跟单系统"
DEFAULT_PORT = 9999
BACKEND_STARTUP_GRACE_SECONDS = 90.0
CONFIG_BACKUP_DIRNAME = "config-backups"
WORKER_MODULES = {
    "ag_wmc_watch": "ag_wmc_watch",
    "ook_signal_forwarder": "signal_forwarder",
    "ook_signal_puller": "signal_puller",
    "webui_server": "webui_server",
    "member_arsenal_watch": "member_arsenal_watch",
    "odds_snapshot_watch": "odds_snapshot_watch",
    "settlement_importer": "settlement_importer",
    "follow_engine": "follow_engine",
    "telegram_bot": "telegram_bot",
    "telegram_signal_watch": "telegram_signal_watch",
}

_LOCK_HANDLE: Optional[Any] = None
_STOP_REQUESTED = False


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resolve_bundle_root() -> Path:
    if is_frozen_runtime():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[1]


def resolve_runtime_root(home: Optional[Path] = None) -> Path:
    override = str(os.environ.get("HDZZ_RUNTIME_ROOT") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = Path(home) if home is not None else Path.home()
    return (base / "Library" / "Application Support" / DEFAULT_RUNTIME_NAME).resolve()


def _copy_tree(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _read_config(path: Path) -> Dict[str, Any]:
    """Load a config without logging its contents or secrets."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"配置文件不存在: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件不是有效 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("配置根节点必须是 JSON 对象")
    targets = value.get("targets")
    if targets is not None and not isinstance(targets, list):
        raise ValueError("配置 targets 必须是数组")
    return value


def configuration_summary(runtime_root: Path) -> Dict[str, Any]:
    """Return non-sensitive first-run state for the launcher/UI."""

    path = Path(runtime_root) / "config.json"
    try:
        config = _read_config(path)
    except ValueError as exc:
        return {"exists": path.is_file(), "valid": False, "configured": False, "target_count": 0, "message": str(exc)}
    targets = config.get("targets") or []
    return {
        "exists": True,
        "valid": True,
        "configured": bool(targets),
        "target_count": len(targets),
        "message": "已配置监听目标" if targets else "尚未配置监听目标",
    }


def _config_backup_path(runtime_root: Path) -> Path:
    stamp = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path(runtime_root) / ".run" / CONFIG_BACKUP_DIRNAME / f"config-{stamp}-{os.getpid()}.json"


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(data)
        try:
            temp.chmod(0o600)
        except OSError:
            pass
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def import_config(source: Path, runtime_root: Path) -> Path:
    """Safely import a user config, preserving the current one first."""

    source = Path(source).expanduser().resolve()
    runtime_root = Path(runtime_root).expanduser().resolve()
    payload = _read_config(source)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    target = runtime_root / "config.json"
    if target.exists():
        if not target.is_file():
            raise ValueError("运行目录中的 config.json 不是普通文件")
        backup = _config_backup_path(runtime_root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        try:
            backup.chmod(0o600)
        except OSError:
            pass
    _write_private(target, encoded)
    return target


def _legacy_config_candidate(bundle: Path) -> Optional[Path]:
    explicit = str(os.environ.get("HDZZ_LEGACY_CONFIG") or "").strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() else None
    # Source/dev runs may opt into a project config; frozen apps never scan the
    # source tree and therefore cannot accidentally ingest developer secrets.
    if not is_frozen_runtime():
        candidate = bundle / "app" / "config.json"
        return candidate if candidate.is_file() else None
    return None


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        root = destination.parent.resolve()
        for member in tf.getmembers():
            target = (root / member.name).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"非法资源路径: {member.name}")
        tf.extractall(root)


def _macos_chromium_revisions(root: Path) -> set[str]:
    """Return the Playwright Chromium revisions with a usable macOS binary."""

    root = Path(root)
    if not root.is_dir():
        return set()
    pattern = "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    revisions: set[str] = set()
    for executable in root.glob(pattern):
        if not executable.is_file():
            continue
        for parent in executable.parents:
            match = re.fullmatch(r"chromium-(\d+)", parent.name or "")
            if match:
                revisions.add(match.group(1))
                break
    return revisions


def _has_macos_chromium(root: Path) -> bool:
    return bool(_macos_chromium_revisions(root))


def _archive_macos_chromium_revisions(archive: Path) -> set[str]:
    """Inspect an archive without extracting it, keeping the check read-only."""

    archive = Path(archive)
    if not archive.is_file():
        return set()
    pattern = re.compile(
        r"(?:^|/)ms-playwright/chromium-(\d+)/chrome-mac[^/]*/"
        r"Google Chrome for Testing\.app/Contents/MacOS/Google Chrome for Testing$"
    )
    revisions: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                match = pattern.search(str(member.name or ""))
                if match:
                    revisions.add(match.group(1))
    except (OSError, tarfile.TarError):
        return set()
    return revisions


def prepare_playwright_browsers(bundle_root: Path, runtime_root: Path) -> Optional[Path]:
    """Copy the bundled Chromium into runtime, replacing stale revisions safely.

    Playwright resolves its executable by revision (for example, chromium-1217).
    A previous app version may have left another revision in Application Support;
    treating any Chromium directory as valid makes relogin fail later with a
    missing executable.  Only the generated ``.run/ms-playwright`` cache is ever
    replaced here; user config, accounts, orders, and state remain untouched.
    """

    bundled = bundle_root / "ms-playwright"
    archive = bundle_root / "ms-playwright.tar.gz"
    runtime = runtime_root / ".run" / "ms-playwright"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    lock_path = runtime.parent / ".ms-playwright.prepare.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            bundled_revisions = _macos_chromium_revisions(bundled)
            archive_revisions = _archive_macos_chromium_revisions(archive)
            expected_revisions = bundled_revisions or archive_revisions
            runtime_revisions = _macos_chromium_revisions(runtime)
            runtime_usable = bool(runtime_revisions)
            if expected_revisions and runtime_usable and not (runtime_revisions & expected_revisions):
                runtime_usable = False

            if bundled_revisions and not runtime_usable:
                if runtime.exists():
                    shutil.rmtree(runtime)
                shutil.copytree(bundled, runtime, symlinks=True)
                return runtime

            if not runtime_usable and archive.is_file():
                if runtime.exists():
                    shutil.rmtree(runtime)
                _safe_extract_tar(archive, runtime)
                if _has_macos_chromium(runtime):
                    return runtime

            if runtime_usable:
                return runtime
            return None
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def seed_runtime(root: Path, bundle_root: Optional[Path] = None) -> Path:
    """Create runtime directories and copy only non-sensitive seed assets."""

    bundle = Path(bundle_root or resolve_bundle_root())
    root.mkdir(parents=True, exist_ok=True)
    for name in ("data", "orders", "state", ".run"):
        (root / name).mkdir(parents=True, exist_ok=True)

    webui_src = bundle / "app" / "webui"
    webui_dst = root / "webui"
    # WebUI is application code, not user data.  Always refresh it from the
    # bundled resources so an upgraded app cannot keep serving an older
    # index/app.js from a previous Application Support runtime.
    if webui_src.is_dir():
        _copy_tree(webui_src, webui_dst)

    config_dst = root / "config.json"
    if not config_dst.exists():
        legacy = _legacy_config_candidate(bundle)
        if legacy is not None:
            import_config(legacy, root)
        else:
            config_src = bundle / "app" / "tauri_config.example.json"
            if not config_src.is_file():
                config_src = bundle / "app" / "config.example.json"
            if config_src.is_file():
                # Validate the template before it becomes the user's config.
                _read_config(config_src)
                _write_private(config_dst, config_src.read_bytes())
    return root


def bootstrap_runtime_environment() -> Path:
    root = seed_runtime(resolve_runtime_root())
    bundle_root = resolve_bundle_root()
    browsers = prepare_playwright_browsers(bundle_root, root)
    os.environ["HDZZ_RUNTIME_ROOT"] = str(root)
    os.environ["HDZZ_APP_DIR"] = str(root)
    os.environ.setdefault("HDZZ_LOG_DIR", str(root / ".run" / "logs"))
    os.environ.setdefault("HDZZ_PLAYWRIGHT_PREFER_BUNDLED", "1")
    os.environ.setdefault("HDZZ_PLAYWRIGHT_BUNDLED_ONLY", "1")
    if browsers:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    bundle_app = bundle_root / "app"
    if bundle_app.is_dir() and str(bundle_app) not in sys.path:
        sys.path.insert(0, str(bundle_app))
    return root


def split_worker_mode_args(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    if len(argv) >= 2 and argv[0] == "--hdzz-worker":
        return argv[1], argv[2:]
    return None, list(argv)


def _invoke_worker(main_func: Any, argv: List[str], worker_name: str) -> int:
    try:
        signature = inspect.signature(main_func)
        positional = [
            item
            for item in signature.parameters.values()
            if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        accepts_argv = bool(positional) or any(
            item.kind == inspect.Parameter.VAR_POSITIONAL for item in signature.parameters.values()
        )
    except Exception:
        accepts_argv = True
    if accepts_argv:
        return int(main_func(argv))
    old_argv = list(sys.argv)
    try:
        sys.argv = [worker_name] + list(argv)
        return int(main_func())
    finally:
        sys.argv = old_argv


def run_worker_mode(worker_name: str, argv: List[str]) -> int:
    module_name = WORKER_MODULES.get(worker_name)
    if not module_name:
        print(f"[ERROR] 未知 worker: {worker_name}", file=sys.stderr)
        return 2
    module = importlib.import_module(module_name)
    main_func = getattr(module, "main", None)
    if main_func is None:
        print(f"[ERROR] worker 缺少 main(): {worker_name}", file=sys.stderr)
        return 2
    return _invoke_worker(main_func, argv, worker_name)


def _atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def ownership_path(runtime_root: Path) -> Path:
    return runtime_root / ".run" / "backend-ownership.json"


def lock_path(runtime_root: Path) -> Path:
    return runtime_root / ".run" / "backend.lock"


def read_ownership(runtime_root: Path) -> Dict[str, Any]:
    try:
        value = json.loads(ownership_path(runtime_root).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def acquire_ownership(runtime_root: Path) -> bool:
    """Acquire an advisory lock that survives the short app.py start call."""

    global _LOCK_HANDLE
    path = lock_path(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False
    _LOCK_HANDLE = handle
    owner = {
        "pid": os.getpid(),
        "owner": "tauri-bundled-backend" if is_frozen_runtime() else "tauri-dev-backend",
        "backend_path": str(Path(sys.executable).resolve()),
        "started_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        **current_build_info(),
    }
    _atomic_json(ownership_path(runtime_root), owner)
    return True


def release_ownership(runtime_root: Path) -> None:
    global _LOCK_HANDLE
    try:
        current = read_ownership(runtime_root)
        if int(current.get("pid") or 0) == os.getpid():
            ownership_path(runtime_root).unlink(missing_ok=True)
    finally:
        if _LOCK_HANDLE is not None:
            try:
                fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
            finally:
                _LOCK_HANDLE.close()
                _LOCK_HANDLE = None


def _request_stop(_signum: int, _frame: Any) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _backend_is_ready(port: int = DEFAULT_PORT) -> bool:
    try:
        with request.urlopen(f"http://127.0.0.1:{int(port)}/api/status", timeout=0.8) as response:
            return int(getattr(response, "status", 200) or 200) < 500
    except Exception:
        return False


def _action(argv: List[str]) -> str:
    for value in argv:
        if not str(value).startswith("-"):
            return str(value)
    return "start"


def _launch_args(argv: List[str]) -> List[str]:
    args = list(argv)
    if not any(str(arg) == "--port" or str(arg).startswith("--port=") for arg in args):
        args.extend(["--port", str(DEFAULT_PORT)])
    return args


def probe_playwright() -> int:
    """Run a tiny bundled-browser smoke test without touching business data."""

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<title>HD backend probe</title>")
        title = page.title()
        browser.close()
    if title != "HD backend probe":
        print("Playwright 浏览器 smoke test 失败", file=sys.stderr)
        return 1
    print("Playwright bundled Chromium ready")
    return 0


def _supervise(runtime_root: Path, argv: List[str]) -> int:
    global _STOP_REQUESTED
    if not acquire_ownership(runtime_root):
        owner = read_ownership(runtime_root)
        print(json.dumps({"error": "backend_already_owned", "ownership": owner}, ensure_ascii=False), file=sys.stderr)
        return 2

    try:
        import app as launcher

        result = int(launcher.main(_launch_args(argv)))
        if result != 0:
            return result
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)
        unhealthy_since: Optional[float] = None
        while not _STOP_REQUESTED:
            if _backend_is_ready():
                unhealthy_since = None
            elif unhealthy_since is None:
                unhealthy_since = time.monotonic()
            elif time.monotonic() - unhealthy_since >= BACKEND_STARTUP_GRACE_SECONDS:
                print("backend 健康检查持续失败，释放 ownership", file=sys.stderr)
                return 1
            time.sleep(1.0)
        return 0
    finally:
        # Closing the Tauri window does not send this signal; if a supervisor
        # is stopped explicitly, release only ownership and leave workers to
        # the existing app.py lifecycle commands.
        release_ownership(runtime_root)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--build-info":
        print(json.dumps(current_build_info(), ensure_ascii=False, sort_keys=True))
        return 0
    runtime_root = bootstrap_runtime_environment()
    worker_name, worker_argv = split_worker_mode_args(args)
    if worker_name:
        return run_worker_mode(worker_name, worker_argv)
    if args and args[0] == "--probe-playwright":
        return probe_playwright()

    if "--import-config" in args:
        index = args.index("--import-config")
        if index + 1 >= len(args) or str(args[index + 1]).startswith("-"):
            print("--import-config 需要一个 config.json 路径", file=sys.stderr)
            return 2
        try:
            target = import_config(Path(args[index + 1]), runtime_root)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"配置已安全导入: {target}")
        return 0

    if args and args[0] == "--config-status":
        print(json.dumps(configuration_summary(runtime_root), ensure_ascii=False))
        return 0

    if args and args[0] == "--restart-workers":
        # The backend supervisor keeps ownership; this delegates only the
        # existing app.py worker lifecycle after a new config is imported.
        import app as launcher

        return int(launcher.main(["restart", "--host", "127.0.0.1", "--port", str(DEFAULT_PORT)]))

    action = _action(args)
    if action in {"start", "restart"}:
        return _supervise(runtime_root, args)

    import app as launcher

    return int(launcher.main(args))


if __name__ == "__main__":
    raise SystemExit(main())
