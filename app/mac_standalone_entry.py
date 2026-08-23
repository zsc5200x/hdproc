from __future__ import annotations

import atexit
import argparse
import base64
import importlib
import io
import inspect
import json
import mimetypes
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
from html import escape
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from typing import List, Optional, Tuple
from urllib import error, request

DEFAULT_APP_NAME = "AG Matrix Standalone"
DEFAULT_PORT = 9999
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


class WorkerShutdownController:
    def __init__(self, launcher_module, *, enabled: bool) -> None:
        self._launcher_module = launcher_module
        self._enabled = bool(enabled)
        self._stopped = False

    def shutdown(self) -> None:
        if not self._enabled or self._stopped:
            return
        self._stopped = True
        self._launcher_module.stop_all(force=False)

    def build_stop_command(self, *, force: bool) -> List[str]:
        if is_frozen_runtime():
            cmd = [sys.executable, "stop"]
        else:
            launcher_file = Path(getattr(self._launcher_module, "__file__", Path(__file__).resolve().parents[1] / "app.py")).resolve()
            cmd = [sys.executable, str(launcher_file), "stop"]
        if force:
            cmd.append("--force")
        return cmd

    def shutdown_background(self, *, force: bool) -> None:
        if not self._enabled or self._stopped:
            return
        self._stopped = True
        cmd = self.build_stop_command(force=force)
        kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **kwargs)


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def read_frozen_bundle_metadata(executable_path: Optional[str] = None) -> dict:
    if not is_frozen_runtime():
        return {}
    try:
        exe_path = Path(executable_path or sys.executable).resolve()
        info_path = exe_path.parent.parent / "Info.plist"
        if not info_path.is_file():
            return {}
        with info_path.open("rb") as fh:
            obj = plistlib.load(fh)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def resolve_standalone_app_name() -> str:
    env_name = str(os.environ.get("HDZZ_STANDALONE_APP_NAME") or "").strip()
    if env_name:
        return env_name

    meta = read_frozen_bundle_metadata()
    for key in ("HDZZRuntimeAppName", "CFBundleDisplayName", "CFBundleName"):
        value = str(meta.get(key) or "").strip()
        if value:
            return value

    if is_frozen_runtime():
        stem = str(Path(sys.executable).stem or "").strip()
        if stem:
            return stem
    return DEFAULT_APP_NAME


def resolve_default_port() -> int:
    env_port = str(os.environ.get("HDZZ_DEFAULT_PORT") or "").strip()
    if env_port:
        try:
            return int(env_port)
        except Exception:
            pass

    meta = read_frozen_bundle_metadata()
    try:
        value = int(meta.get("HDZZDefaultPort") or 0)
        if value > 0:
            return value
    except Exception:
        pass
    return DEFAULT_PORT


def resolve_startup_splash_data_url(bundle_root: Optional[Path] = None) -> Optional[str]:
    meta = read_frozen_bundle_metadata()
    rel_path = str(meta.get("HDZZSplashAssetRelPath") or "").strip()
    if not rel_path:
        return None
    try:
        root = Path(bundle_root or resolve_bundle_root()).resolve()
        asset_path = (root / rel_path).resolve()
        if not asset_path.is_file():
            return None
        mime_type, _ = mimetypes.guess_type(str(asset_path))
        raw = asset_path.read_bytes()
        encoded = base64.b64encode(raw).decode("ascii")
        return f"data:{mime_type or 'image/png'};base64,{encoded}"
    except Exception:
        return None


def resolve_bundle_root() -> Path:
    if is_frozen_runtime():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parents[1]


def resolve_runtime_root() -> Path:
    return (Path.home() / "Library" / "Application Support" / resolve_standalone_app_name()).resolve()


def build_startup_html(app_name: str, splash_data_url: Optional[str] = None) -> str:
    hero = ""
    if splash_data_url:
        hero = f"""
        <div class="hero-frame" aria-hidden="true">
          <div class="hero-glow"></div>
          <img class="hero-image" src="{escape(splash_data_url, quote=True)}" alt="{escape(app_name)} 启动图">
        </div>
        """
    return f"""
    <html><body class="shell">
      <style>
        :root {{
          --bg: #07111d;
          --panel: rgba(9, 23, 38, 0.82);
          --line: rgba(122, 193, 255, 0.18);
          --accent: #58b9ff;
          --accent-2: #89d2ff;
          --text: #f3f9ff;
          --muted: #90a9c2;
        }}
        * {{ box-sizing: border-box; }}
        body.shell {{
          margin: 0;
          min-height: 100vh;
          display: grid;
          place-items: center;
          overflow: hidden;
          font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", sans-serif;
          background:
            radial-gradient(circle at top, rgba(88, 185, 255, 0.24), transparent 38%),
            radial-gradient(circle at 85% 18%, rgba(137, 210, 255, 0.14), transparent 28%),
            linear-gradient(180deg, #030c14 0%, var(--bg) 52%, #040b12 100%);
          color: var(--text);
        }}
        .shell::before {{
          content: "";
          position: fixed;
          inset: 0;
          background:
            linear-gradient(135deg, rgba(255,255,255,0.04) 0, transparent 28%),
            repeating-linear-gradient(90deg, rgba(255,255,255,0.025) 0 1px, transparent 1px 120px);
          pointer-events: none;
        }}
        .panel {{
          position: relative;
          width: min(920px, calc(100vw - 48px));
          padding: 34px;
          border-radius: 32px;
          border: 1px solid var(--line);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01)),
            var(--panel);
          box-shadow:
            0 32px 90px rgba(0, 0, 0, 0.45),
            inset 0 1px 0 rgba(255,255,255,0.06);
          backdrop-filter: blur(20px);
          display: grid;
          grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
          gap: 28px;
          align-items: center;
        }}
        .panel::after {{
          content: "";
          position: absolute;
          inset: 10px;
          border-radius: 24px;
          border: 1px solid rgba(255,255,255,0.03);
          pointer-events: none;
        }}
        .hero-frame {{
          position: relative;
          aspect-ratio: 1 / 1;
          border-radius: 28px;
          overflow: hidden;
          background: radial-gradient(circle at top, rgba(88,185,255,0.25), rgba(5, 15, 25, 0.85));
          border: 1px solid rgba(137, 210, 255, 0.18);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.07);
        }}
        .hero-glow {{
          position: absolute;
          inset: -18%;
          background: radial-gradient(circle, rgba(88,185,255,0.28) 0%, transparent 58%);
          filter: blur(20px);
          animation: breathe 4.2s ease-in-out infinite;
        }}
        .hero-image {{
          position: relative;
          z-index: 1;
          width: 100%;
          height: 100%;
          object-fit: cover;
          display: block;
          transform: scale(1.02);
        }}
        .eyebrow {{
          display: inline-flex;
          align-items: center;
          gap: 10px;
          padding: 8px 14px;
          border-radius: 999px;
          font-size: 12px;
          letter-spacing: .14em;
          text-transform: uppercase;
          color: var(--accent-2);
          border: 1px solid rgba(137,210,255,0.2);
          background: rgba(9, 25, 40, 0.72);
        }}
        .eyebrow::before {{
          content: "";
          width: 9px;
          height: 9px;
          border-radius: 999px;
          background: linear-gradient(180deg, #9fddff, #4fb5ff);
          box-shadow: 0 0 12px rgba(88,185,255,0.7);
        }}
        h1 {{
          margin: 16px 0 10px;
          font-size: clamp(30px, 5vw, 52px);
          line-height: 1.02;
          letter-spacing: -0.04em;
        }}
        .subcopy {{
          margin: 0;
          color: var(--muted);
          font-size: 15px;
          line-height: 1.7;
          max-width: 420px;
        }}
        .status-row {{
          margin-top: 24px;
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
        }}
        .status-chip {{
          padding: 10px 14px;
          border-radius: 14px;
          font-size: 13px;
          color: #d9edff;
          border: 1px solid rgba(88,185,255,0.16);
          background: rgba(7, 20, 32, 0.78);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
        }}
        .loader {{
          margin-top: 22px;
          width: min(360px, 100%);
          height: 8px;
          border-radius: 999px;
          overflow: hidden;
          background: rgba(255,255,255,0.08);
          position: relative;
        }}
        .loader::before {{
          content: "";
          position: absolute;
          inset: 0;
          width: 38%;
          border-radius: inherit;
          background: linear-gradient(90deg, rgba(88,185,255,0.1), #58b9ff, #9eddff);
          box-shadow: 0 0 24px rgba(88,185,255,0.35);
          animation: glide 1.9s ease-in-out infinite;
        }}
        @keyframes glide {{
          0% {{ transform: translateX(-110%); }}
          55% {{ transform: translateX(185%); }}
          100% {{ transform: translateX(185%); }}
        }}
        @keyframes breathe {{
          0%, 100% {{ transform: scale(0.96); opacity: 0.75; }}
          50% {{ transform: scale(1.05); opacity: 1; }}
        }}
        @media (max-width: 780px) {{
          .panel {{
            grid-template-columns: 1fr;
            padding: 24px;
          }}
          .hero-frame {{
            max-width: 320px;
            margin: 0 auto;
          }}
        }}
      </style>
      <main class="panel">
        {hero}
        <section>
          <div class="eyebrow">HD Sports Matrix</div>
          <h1>{escape(app_name)}</h1>
          <p class="subcopy">正在连接后台服务、同步运行环境并唤起控制台。首屏完成后会自动切换到系统主界面。</p>
          <div class="status-row">
            <div class="status-chip">独立实例</div>
            <div class="status-chip">后台自检中</div>
            <div class="status-chip">正在连接后台服务</div>
          </div>
          <div class="loader" aria-hidden="true"></div>
        </section>
      </main>
    </body></html>
    """


def _ensure_sys_path(bundle_root: Path) -> None:
    candidates = [
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parents[1] / "src",
        Path(__file__).resolve().parents[1] / "ook",
        bundle_root / "app",
        bundle_root / "src",
        bundle_root / "ook",
    ]
    for candidate in candidates:
        if candidate.exists():
            text = str(candidate.resolve())
            if text not in sys.path:
                sys.path.insert(0, text)


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _load_json_dict(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def migrate_runtime_config(seed_path: Path, runtime_path: Path) -> None:
    if not seed_path.is_file() or not runtime_path.is_file():
        return
    seed = _load_json_dict(seed_path)
    runtime = _load_json_dict(runtime_path)
    if not seed or not runtime:
        return

    targets = runtime.get("targets") or []
    if isinstance(targets, list) and len(targets) > 0:
        return

    seed_pull_url = str(seed.get("signal_pull_url") or "").strip()
    runtime_pull_url = str(runtime.get("signal_pull_url") or "").strip()
    runtime_pull_enabled = bool(runtime.get("signal_pull_enabled") or False)
    if runtime_pull_enabled and runtime_pull_url:
        return
    if not seed_pull_url:
        return

    changed = False
    migrate_keys = [
        "signal_pull_enabled",
        "signal_pull_url",
        "signal_pull_token",
        "signal_pull_timeout_seconds",
        "signal_pull_interval_seconds",
        "signal_pull_limit",
        "signal_pull_push_history_on_start",
        "signal_pull_live_only",
        "signal_pull_debug",
        "signal_webhook_enabled",
        "signal_webhook_url",
        "signal_webhook_token",
        "signal_webhook_timeout_seconds",
        "signal_webhook_retry_max",
        "signal_webhook_retry_backoff_seconds",
        "signal_webhook_interval_seconds",
        "signal_webhook_push_history_on_start",
        "signal_webhook_debug",
    ]
    for key in migrate_keys:
        if key in seed and runtime.get(key) != seed.get(key):
            runtime[key] = seed.get(key)
            changed = True
    if changed:
        runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_tg_auth_section(obj: dict, key: str) -> dict:
    sec = obj.get(key)
    if not isinstance(sec, dict):
        sec = {}
    ids = sorted({str(x).strip() for x in (sec.get("ids") or []) if str(x).strip()})
    usernames = sorted({str(x).strip() for x in (sec.get("usernames") or []) if str(x).strip()})
    return {"ids": ids, "usernames": usernames}


def merge_tg_auth_seed(seed_path: Path, runtime_path: Path) -> None:
    if not seed_path.is_file():
        return
    seed = _load_json_dict(seed_path)
    runtime = _load_json_dict(runtime_path) if runtime_path.is_file() else {}

    merged = {}
    for section in ("admins", "grants"):
        seed_sec = _normalize_tg_auth_section(seed, section)
        run_sec = _normalize_tg_auth_section(runtime, section)
        merged[section] = {
            "ids": sorted(set(seed_sec["ids"]) | set(run_sec["ids"])),
            "usernames": sorted(set(seed_sec["usernames"]) | set(run_sec["usernames"])),
        }

    updated_at = str(runtime.get("updated_at") or seed.get("updated_at") or "")
    if not updated_at:
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    merged["updated_at"] = updated_at

    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_telegram_signal_credentials_seed(seed_path: Path, runtime_path: Path) -> None:
    if not seed_path.is_file():
        return
    seed = _load_json_dict(seed_path)
    runtime = _load_json_dict(runtime_path) if runtime_path.is_file() else {}
    changed = False
    for key in ("api_id", "api_hash"):
        if not str(runtime.get(key) or "").strip() and str(seed.get(key) or "").strip():
            runtime[key] = str(seed[key]).strip()
            changed = True
    if not changed:
        return
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime_path.chmod(0o600)


def seed_runtime_app(bundle_root: Path, runtime_app_dir: Path) -> None:
    seed_app_dir = bundle_root / "app"
    runtime_app_dir.mkdir(parents=True, exist_ok=True)

    _copy_tree(seed_app_dir / "webui", runtime_app_dir / "webui")

    for file_name in ("config.example.json", "README.md"):
        src = seed_app_dir / file_name
        dst = runtime_app_dir / file_name
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    config_src = seed_app_dir / "config.json"
    config_dst = runtime_app_dir / "config.json"
    if config_src.is_file() and not config_dst.exists():
        shutil.copy2(config_src, config_dst)
    migrate_runtime_config(config_src, config_dst)

    tg_auth_src = seed_app_dir / "data" / "tg_auth.json"
    tg_auth_dst = runtime_app_dir / "data" / "tg_auth.json"
    if tg_auth_src.is_file():
        merge_tg_auth_seed(tg_auth_src, tg_auth_dst)

    telegram_credentials_src = seed_app_dir / "runtime_seeds" / "telegram_signal_credentials.json"
    telegram_credentials_dst = runtime_app_dir / "data" / "telegram_signal_secrets.json"
    merge_telegram_signal_credentials_seed(telegram_credentials_src, telegram_credentials_dst)

    puller_state_src = seed_app_dir / "runtime_seeds" / "ook_signal_puller_state.json"
    puller_state_dst = runtime_app_dir / ".run" / "ook_signal_puller_state.json"
    if puller_state_src.is_file() and not puller_state_dst.exists():
        puller_state_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(puller_state_src, puller_state_dst)


def _has_macos_chromium_browser(browsers_dir: Path) -> bool:
    return bool(_macos_chromium_revisions(browsers_dir))


def _macos_chromium_revisions(browsers_dir: Path) -> set[str]:
    root = Path(browsers_dir)
    if not root.is_dir():
        return set()
    pattern = "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    revisions = set()
    for path in root.glob(pattern):
        if not path.is_file():
            continue
        for parent in path.parents:
            m = re.fullmatch(r"chromium-(\d+)", str(parent.name or ""))
            if m:
                revisions.add(m.group(1))
                break
    return revisions


def _archive_macos_chromium_revisions(archive_path: Path) -> set[str]:
    archive = Path(archive_path)
    if not archive.is_file():
        return set()
    revisions = set()
    pattern = re.compile(
        r"(?:^|/)ms-playwright/chromium-(\d+)/chrome-mac[^/]*/"
        r"Google Chrome for Testing\.app/Contents/MacOS/Google Chrome for Testing$"
    )
    try:
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                m = pattern.search(str(member.name or ""))
                if m:
                    revisions.add(m.group(1))
    except Exception:
        return set()
    return revisions


def _extract_playwright_browsers_archive(archive_path: Path, runtime_browsers: Path) -> bool:
    archive = Path(archive_path)
    if not archive.is_file():
        return False
    if runtime_browsers.exists():
        shutil.rmtree(runtime_browsers)
    runtime_browsers.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(runtime_browsers.parent)
    return _has_macos_chromium_browser(runtime_browsers)


def prepare_playwright_browsers(bundle_root: Path, runtime_app_dir: Path) -> Path:
    bundled = bundle_root / "ms-playwright"
    bundled_archive = bundle_root / "ms-playwright.tar.gz"
    runtime_browsers = runtime_app_dir / ".run" / "ms-playwright"
    bundled_revisions = _macos_chromium_revisions(bundled)
    archive_revisions = _archive_macos_chromium_revisions(bundled_archive)
    expected_revisions = bundled_revisions or archive_revisions
    runtime_revisions = _macos_chromium_revisions(runtime_browsers)
    bundled_usable = bool(bundled_revisions)
    runtime_usable = bool(runtime_revisions)
    if runtime_usable and expected_revisions and not (runtime_revisions & expected_revisions):
        runtime_usable = False
    if bundled_usable and not runtime_usable:
        if runtime_browsers.exists():
            shutil.rmtree(runtime_browsers)
        runtime_browsers.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundled, runtime_browsers)
    elif not runtime_usable and bundled_archive.is_file():
        _extract_playwright_browsers_archive(bundled_archive, runtime_browsers)
    if runtime_browsers.is_dir():
        return runtime_browsers
    return bundled


def bootstrap_runtime_environment() -> Path:
    bundle_root = resolve_bundle_root()
    runtime_root = resolve_runtime_root()
    runtime_app_dir = runtime_root / "app"

    if is_frozen_runtime():
        # Standalone apps bundle a known Chromium runtime. Prefer it over the
        # user's normal Chrome so login windows belong to this app and can be
        # cleaned up safely when the app stops.
        os.environ.setdefault("HDZZ_PLAYWRIGHT_PREFER_BUNDLED", "1")
        os.environ.setdefault("HDZZ_PLAYWRIGHT_BUNDLED_ONLY", "1")

    _ensure_sys_path(bundle_root)
    seed_runtime_app(bundle_root, runtime_app_dir)

    browsers_dir = prepare_playwright_browsers(bundle_root, runtime_app_dir)
    os.environ["HDZZ_APP_DIR"] = str(runtime_app_dir)
    os.environ.setdefault("HDZZ_LOG_DIR", str(runtime_app_dir / ".run" / "logs"))
    if browsers_dir.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    return runtime_app_dir


def split_worker_mode_args(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    if len(argv) >= 2 and argv[0] == "--hdzz-worker":
        return argv[1], argv[2:]
    return None, list(argv)


def run_worker_mode(worker_name: str, argv: List[str]) -> int:
    module_name = WORKER_MODULES.get(worker_name)
    if not module_name:
        print(f"[ERROR] 未知 worker: {worker_name}", file=sys.stderr)
        return 2
    module = importlib.import_module(module_name)
    main = getattr(module, "main", None)
    if main is None:
        print(f"[ERROR] worker 缺少 main(): {worker_name}", file=sys.stderr)
        return 2
    return int(invoke_worker_main(main, argv, worker_name=worker_name))


def invoke_worker_main(main_func, argv: List[str], *, worker_name: str) -> int:
    try:
        sig = inspect.signature(main_func)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        accepts_argv = has_varargs or len(positional) >= 1
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


def parse_launch_request(argv: List[str]) -> Tuple[str, str, int]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("action", nargs="?", default="start")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=resolve_default_port())
    args, _ = parser.parse_known_args(argv)
    return str(args.action), str(args.host), int(args.port)


def inject_default_port_args(argv: List[str]) -> List[str]:
    args = list(argv)
    if any(str(arg).startswith("--port=") for arg in args):
        return args
    for idx, arg in enumerate(args):
        if str(arg) == "--port" and idx + 1 < len(args):
            return args

    action = "start"
    for arg in args:
        text = str(arg)
        if not text.startswith("-"):
            action = text
            break
    if action not in {"start", "restart"}:
        return args
    return args + ["--port", str(resolve_default_port())]


def build_service_url(host: str, port: int) -> str:
    return f"http://{str(host).strip() or '127.0.0.1'}:{int(port)}/"


def resolve_runtime_service_url(launcher_module, *, default_host: str, default_port: int) -> str:
    info = None
    read_pid_info = getattr(launcher_module, "read_pid_info", None)
    if callable(read_pid_info):
        try:
            info = read_pid_info()
        except Exception:
            info = None

    if isinstance(info, dict):
        host = str(info.get("host") or default_host)
        try:
            port = int(info.get("port") or default_port)
        except Exception:
            port = int(default_port)
        return build_service_url(host, port)
    return build_service_url(default_host, default_port)


def should_show_desktop_window(action: str, return_code: int) -> bool:
    return action in {"start", "restart"} and return_code in {0, 2}


def should_capture_launcher_output(action: str) -> bool:
    return action in {"start", "restart"}


def run_launcher_action(
    argv: List[str],
    launcher_module,
    *,
    capture_output: bool,
    log_path: Path,
) -> int:
    if not capture_output:
        return int(launcher_module.main(argv))

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== launcher start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        fh.flush()
        with redirect_stdout(fh), redirect_stderr(fh):
            return int(launcher_module.main(argv))


def probe_service_ready(url: str, timeout_s: float = 1.5) -> bool:
    try:
        with request.urlopen(url, timeout=max(0.2, float(timeout_s))) as resp:
            return int(getattr(resp, "status", 200) or 200) < 500
    except Exception:
        return False


def launch_desktop_window(
    *,
    service_url: str,
    launcher_module,
    stop_workers_on_close: bool,
) -> int:
    app_name = resolve_standalone_app_name()
    splash_data_url = resolve_startup_splash_data_url()
    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QAction
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
    except Exception as exc:
        print(f"[ERROR] 桌面窗口依赖未就绪（PySide6）：{exc}", file=sys.stderr)
        return 3

    class DesktopPage(QWebEnginePage):
        def acceptNavigationRequest(self, url: QUrl, navigation_type, is_main_frame: bool) -> bool:  # type: ignore[override]
            if url.scheme() in {"http", "https"} and url.toString().startswith(service_url):
                return super().acceptNavigationRequest(url, navigation_type, is_main_frame)
            if url.scheme() in {"http", "https"}:
                subprocess.run(["open", url.toString()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                return False
            return super().acceptNavigationRequest(url, navigation_type, is_main_frame)

    class DesktopWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self._target_url = QUrl(service_url)
            self._view = QWebEngineView(self)
            self._view.setPage(DesktopPage(self._view))
            self.setCentralWidget(self._view)
            self.setWindowTitle(app_name)
            self.resize(1360, 900)
            self.statusBar().showMessage("正在启动服务，请稍候...")
            self._ready_deadline = time.time() + 25.0
            self._loaded = False
            self._timer = QTimer(self)
            self._timer.setInterval(400)
            self._timer.timeout.connect(self._check_and_load)
            self._timer.start()
            self._view.loadFinished.connect(self._on_load_finished)
            self._view.setHtml(build_startup_html(app_name, splash_data_url=splash_data_url), self._target_url)

            refresh_action = QAction("刷新", self)
            refresh_action.triggered.connect(self._reload_page)
            self.menuBar().addAction(refresh_action)

        def _reload_page(self) -> None:
            self._loaded = False
            self.statusBar().showMessage("正在重新连接服务...")
            self._timer.start()
            self._check_and_load()

        def _check_and_load(self) -> None:
            if self._loaded:
                return
            if probe_service_ready(service_url, timeout_s=0.8):
                self._loaded = True
                self._timer.stop()
                self.statusBar().showMessage("界面已就绪")
                self._view.load(self._target_url)
                return
            if time.time() >= self._ready_deadline:
                self._timer.stop()
                self.statusBar().showMessage("服务启动超时")
                self._view.setHtml(
                    f"""
                    <html><body style="margin:0;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
                    background:#f9fafb;color:#111827;display:flex;align-items:center;justify-content:center;height:100vh;">
                    <div style="max-width:620px;padding:28px 32px;background:white;border-radius:18px;box-shadow:0 20px 50px rgba(15,23,42,.08);">
                    <div style="font-size:26px;font-weight:700;margin-bottom:12px;">界面没有正常启动</div>
                    <div style="font-size:15px;line-height:1.6;color:#4b5563;">软件已经启动了后台流程，但桌面界面没有在预期时间内响应。</div>
                    <div style="margin-top:16px;font-size:14px;color:#6b7280;">尝试地址：{escape(service_url)}</div>
                    <div style="margin-top:8px;font-size:14px;color:#6b7280;">你可以关闭窗口后重试；如果仍然失败，检查日志目录中的 err.log。</div>
                    </div></body></html>
                    """,
                    self._target_url,
                )

        def _on_load_finished(self, ok: bool) -> None:
            if ok:
                self.statusBar().showMessage("界面已加载")
            else:
                self.statusBar().showMessage("页面加载失败，正在重试...")
                self._loaded = False
                if not self._timer.isActive():
                    self._timer.start()

        def closeEvent(self, event) -> None:  # type: ignore[override]
            if stop_workers_on_close:
                try:
                    shutdown_controller.shutdown_background(force=True)
                except Exception as exc:
                    QMessageBox.warning(self, "停止后台失败", f"关闭窗口时停止后台进程失败：{exc}")
            super().closeEvent(event)

    app = QApplication.instance() or QApplication(sys.argv[:1] or [app_name])
    app.setApplicationName(app_name)
    shutdown_controller = WorkerShutdownController(launcher_module, enabled=stop_workers_on_close)
    app.aboutToQuit.connect(lambda: shutdown_controller.shutdown_background(force=True))
    atexit.register(lambda: shutdown_controller.shutdown_background(force=True))

    window = DesktopWindow()
    window._shutdown_controller = shutdown_controller  # keep strong reference on window/app lifetime
    window.show()
    return int(app.exec())


def main(argv: Optional[List[str]] = None) -> int:
    args = inject_default_port_args(list(sys.argv[1:] if argv is None else argv))
    runtime_app_dir = bootstrap_runtime_environment()

    worker_name, worker_argv = split_worker_mode_args(args)
    if worker_name:
        return run_worker_mode(worker_name, worker_argv)

    action, host, port = parse_launch_request(args)
    import app as launcher

    launcher_log = runtime_app_dir / ".run" / "logs" / "desktop_launcher.log"
    result = run_launcher_action(
        args,
        launcher,
        capture_output=should_capture_launcher_output(action),
        log_path=launcher_log,
    )
    if should_show_desktop_window(action, result):
        return launch_desktop_window(
            service_url=resolve_runtime_service_url(launcher, default_host=host, default_port=port),
            launcher_module=launcher,
            stop_workers_on_close=(result == 0),
        )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
