import argparse
import html
import json
import os
import re
import time
import warnings

# 避免 macOS 系统 Python（LibreSSL）触发 urllib3 v2 的提示刷屏
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL.*")

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse, urlencode, unquote
import requests
import subprocess
import signal
import sys
import socket
import threading

from portable_env import apply_playwright_browsers_path, detect_playwright_browsers_path, ensure_playwright_browsers_path

from hdzz.core.follower.executor import (
    make_member_session as _make_member_session,
    member_post_session as _member_post_session,
    member_warmup_sport_menu as _member_warmup_sport_menu,
)
from hdzz.core.follower.engine import is_live_backend_order as _is_live_backend_order
from hdzz.core.follower.snapshot_direct import MarketIntent, intent_market_fields, line_matches
from hdzz.core.follower.strategy import decode_handicap_line as _decode_handicap_line
from hdzz.core.follower.strategy import decode_total_line as _decode_total_line
from hdzz.core.follower.strategy import encode_handicap_line as _encode_handicap_line
from hdzz.core.follower.strategy import encode_total_line as _encode_total_line
from hdzz.core.follower.strategy import format_handicap_line_value as _format_handicap_line_value
from hdzz.core.follower.strategy import format_total_line_value as _format_total_line_value
from hdzz.core.watcher.order_filter import normalize_member_names
from hdzz.core.watcher.ps3838_watch import safe_name as ps3838_safe_name
from hdzz.core.watcher.ag_watcher import (
    _load_tg_ui_theme as _load_order_tg_ui_theme,
    format_backend_order_telegram_message,
)
from hdzz.api.webui.follow_settings import FOLLOW_SETTINGS_DEFAULTS, ensure_follow_settings_defaults, merge_follow_settings
from hdzz.api.webui.manual_signal import build_manual_signal_payload, send_manual_signal
from hdzz.api.webui.local_bridge import (
    LocalBridgeSignalError,
    LOCAL_BRIDGE_PATH,
    config_payload as build_local_bridge_config_payload,
    ensure_local_bridge_defaults,
    load_state as load_local_bridge_state,
    merge_local_bridge_config,
    receive_local_bridge_signal,
    record_rejection as record_local_bridge_rejection,
    reset_local_bridge_config,
    token_matches as local_bridge_token_matches,
)
from hdzz.api.webui.signal_pull_config import ensure_signal_pull_config_defaults, merge_signal_pull_config, reset_signal_pull_config
from hdzz.api.webui.chashui_quote_pool import (
    list_readonly_quote_candidates as _list_readonly_quote_candidates,
    load_readonly_quote_pool,
    pick_readonly_quote_member as _pick_readonly_quote_member,
)
from hdzz.core.member.adspower_api import (
    DEFAULT_ADSPOWER_API_BASE,
    adspower_find_profile,
    adspower_list_profiles,
    adspower_profile_proxy_url,
    normalize_adspower_api_base,
)
from hdzz.core.member.keeper import normalize_member_base_url
from hdzz.common.platform_maintenance import (
    PLATFORM_MAINTENANCE_CODE,
    is_platform_maintenance,
    maintenance_status_message,
    read_platform_state,
)
from hdzz.core.telegram_signal.client import (
    TelegramSignalClientError,
    clear_login_session,
    list_source_dialogs,
    send_login_code,
    verify_login_code,
)
from hdzz.core.telegram_signal.store import (
    apply_settings_patch as apply_telegram_signal_settings_patch,
    load_private_json as load_telegram_signal_secrets,
    load_settings as load_telegram_signal_settings,
    read_recent_events as read_telegram_signal_events,
    sanitize_secret_status as sanitize_telegram_signal_secret_status,
    save_settings as save_telegram_signal_settings,
)

try:
    import urllib3  # type: ignore

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


SERVER_START_TIME = time.time()
WATCH_PROCESS = None
WATCH_LOG_FILE = Path("ag_wmc_watch.log")
TARGET_ONLINE_TTL_S = 20.0
APP_ROOT = Path(os.environ.get("HDZZ_APP_DIR") or Path.cwd()).resolve()
ensure_playwright_browsers_path(APP_ROOT)

ACCOUNT_RELOGIN_PENDING_MSG = "已发起浏览器登录，请在弹出的 Chrome 窗口完成验证码/登录。"
CHASHUI_GAMES_CACHE_TTL_S = 2.0
CHASHUI_GAMES_CACHE_STALE_TTL_S = 30.0
CHASHUI_GAMES_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
CHASHUI_GAMES_CACHE_LOCK = threading.Lock()
CHASHUI_WARMUP_CACHE_TTL_S = 30.0
CHASHUI_WARMUP_CACHE: Dict[Tuple[int, str, str, str], float] = {}
CHASHUI_WARMUP_CACHE_LOCK = threading.Lock()
FOLLOW_RESULTS_HTTP_CACHE: Dict[Tuple[str, int, int], Tuple[float, bytes]] = {}
FOLLOW_RESULTS_HTTP_CACHE_LOCK = threading.Lock()
FOLLOW_QUEUE_HTTP_CACHE: Dict[Tuple[str, int, int, bool, int], Tuple[float, bytes]] = {}
FOLLOW_QUEUE_HTTP_CACHE_LOCK = threading.Lock()
FOLLOW_HTTP_CACHE_TTL_S = 0.75


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return bool(default)


def _compact_status_message(message: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _set_chashui_games_cache(member_id: str, mode: str, games: List[Dict[str, Any]], *, now: Optional[float] = None) -> None:
    key = (str(member_id or "").strip(), str(mode or "").strip().lower() or "live")
    if not key[0]:
        return
    payload = {
        "ts": float(time.time() if now is None else now),
        "games": list(games or []),
    }
    with CHASHUI_GAMES_CACHE_LOCK:
        CHASHUI_GAMES_CACHE[key] = payload


def _get_chashui_games_cache(
    member_id: str,
    mode: str,
    *,
    now: Optional[float] = None,
    max_age_s: float = CHASHUI_GAMES_CACHE_TTL_S,
    allow_stale_s: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    key = (str(member_id or "").strip(), str(mode or "").strip().lower() or "live")
    ts = float(time.time() if now is None else now)
    with CHASHUI_GAMES_CACHE_LOCK:
        rec = dict(CHASHUI_GAMES_CACHE.get(key) or {})
    if not rec:
        return None
    age = max(0.0, ts - float(rec.get("ts") or 0.0))
    stale = age > float(max_age_s)
    if stale and (allow_stale_s is None or age > float(allow_stale_s)):
        return None
    return {
        "games": list(rec.get("games") or []),
        "cached": True,
        "stale": bool(stale),
        "age": age,
    }


def prepare_account_manual_relogin_state(state: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
    patched = dict(state or {})
    ts = float(time.time() if now is None else now)
    patched["manual_relogin"] = 1
    patched["manual_relogin_requested_at"] = ts
    patched["last_relogin_at"] = 0
    patched["last_status"] = "relogin"
    patched["relogin_in_progress"] = 1
    patched["relogin_started_at"] = ts
    patched["last_error_code"] = ""
    patched["last_error_msg"] = ACCOUNT_RELOGIN_PENDING_MSG
    patched["last_relogin_error_code"] = ""
    patched["last_relogin_error_msg"] = ""
    patched["last_relogin_failed_at"] = 0
    return patched


def build_account_status_entry(target: Dict[str, Any], state: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
    t = target or {}
    st = state or {}
    ts = float(time.time() if now is None else now)
    last_ok_at = _as_float(st.get("last_ok_at"))
    last_poll_at = _as_float(st.get("last_poll_at"))
    last_status = str(st.get("last_status") or "").strip().lower()
    last_error_code = str(st.get("last_error_code") or "").strip()
    last_error_msg = _compact_status_message(st.get("last_error_msg"))
    relogin_in_progress = bool(st.get("relogin_in_progress") or False)
    relogin_started_at = _as_float(st.get("relogin_started_at"))
    last_relogin_error_code = str(st.get("last_relogin_error_code") or "").strip()
    last_relogin_error_msg = _compact_status_message(st.get("last_relogin_error_msg"))
    last_relogin_failed_at = _as_float(st.get("last_relogin_failed_at"))
    platform_status = str(st.get("platform_status") or "").strip().lower()
    platform_until = _as_float(st.get("platform_maintenance_until"))
    platform_start = _as_float(st.get("platform_maintenance_start_at"))

    if platform_status in {"maintenance", "recovering"}:
        recovering = platform_status == "recovering"
        message = str(
            st.get("last_error_msg")
            or ("平台维护结束，正在验证原有会话" if recovering else "平台系统维护")
        )
        return {
            "status": "recovering" if recovering else "maintenance",
            "last_ok_at": last_ok_at,
            "last_poll_at": last_poll_at,
            "last_error_code": PLATFORM_MAINTENANCE_CODE,
            "last_error_msg": message,
            "last_relogin_error_code": last_relogin_error_code,
            "last_relogin_error_msg": last_relogin_error_msg,
            "last_relogin_failed_at": last_relogin_failed_at,
            "relogin_in_progress": False,
            "relogin_started_at": 0.0,
            "has_session": bool(str(st.get("cookie") or t.get("cookie") or "").strip() or str(st.get("uid") or t.get("uid") or "").strip()),
            "needs_manual_login": False,
            "status_message": message,
            "platform_status": platform_status,
            "maintenance_start_at": platform_start,
            "maintenance_until": platform_until,
        }

    status = "offline"
    if last_ok_at and (ts - last_ok_at) <= float(TARGET_ONLINE_TTL_S):
        status = "online"
    elif relogin_in_progress or last_status == "relogin":
        status = "relogin" if (relogin_started_at and (ts - relogin_started_at) < 600) else "offline"
    elif last_status == "offline" or last_error_code in {"4X014", "4X013", "logout", "login_required"}:
        status = "offline"
    elif last_poll_at and (ts - last_poll_at) <= float(TARGET_ONLINE_TTL_S):
        status = "offline"

    has_session = bool(
        str(st.get("cookie") or t.get("cookie") or "").strip()
        or str(st.get("uid") or t.get("uid") or "").strip()
    )
    needs_manual_login = status != "online" and (
        not has_session
        or last_relogin_error_code == "relogin_failed"
        or last_error_code in {"4X014", "4X013", "logout", "login_required"}
    )

    if status == "online":
        status_message = "在线"
    elif status == "relogin":
        status_message = ACCOUNT_RELOGIN_PENDING_MSG
    elif last_relogin_error_msg:
        status_message = f"登录失败：{last_relogin_error_msg}；请点击重登并完成验证码/登录。"
    elif not has_session:
        status_message = "未完成首次登录，请点击重登并在弹出的 Chrome 窗口完成验证码/登录。"
    elif last_error_msg:
        status_message = last_error_msg
    elif last_error_code:
        status_message = f"账号离线（{last_error_code}）"
    else:
        status_message = "账号离线，请点击重登。"

    return {
        "status": status,
        "last_ok_at": last_ok_at,
        "last_poll_at": last_poll_at,
        "last_error_code": last_error_code,
        "last_error_msg": last_error_msg,
        "last_relogin_error_code": last_relogin_error_code,
        "last_relogin_error_msg": last_relogin_error_msg,
        "last_relogin_failed_at": last_relogin_failed_at,
        "relogin_in_progress": bool(relogin_in_progress),
        "relogin_started_at": relogin_started_at,
        "has_session": bool(has_session),
        "needs_manual_login": bool(needs_manual_login),
        "status_message": status_message,
        "platform_status": platform_status or "online",
        "maintenance_start_at": platform_start,
        "maintenance_until": platform_until,
    }


def normalize_account_watch_fields(login_tab: int, data: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    tab = int(login_tab or 0)
    src = data if isinstance(data, dict) else {}
    members = normalize_member_names(src.get("login2_watch_members")) if tab == 2 else []
    downlines = normalize_member_names(src.get("login2_watch_downlines")) if tab in (2, 3) else []
    return members, downlines


def normalize_account_relogin_min_interval_seconds(value: Any, default: int = 5) -> int:
    try:
        seconds = int(value)
    except Exception:
        seconds = int(default)
    return max(5, min(3600, int(seconds)))


def build_account_add_target(
    *,
    name: str,
    base_url: str,
    username: str,
    password: str,
    security_code: str,
    tab: int,
    time_offset_hours: int,
    relogin_min_interval_seconds: Any,
    login2_watch_members: List[str],
    login2_watch_downlines: List[str],
) -> Dict[str, Any]:
    return {
        "name": str(name or "").strip(),
        "base_url": str(base_url or "").strip(),
        "ver": "version-10-23",
        "login_layer": "ag",
        "uid": "",
        "langx": "zh-cn",
        "totalBets": "wmc",
        "gtype": "ALL",
        "sel_maxid": "0",
        "cookie": "",
        "username": str(username or "").strip(),
        "password": str(password or ""),
        "enabled": True,
        "interval": 20,
        "timeout": 30,
        "cooldown_on_m_rep": False,
        "offline_alert_seconds": 60,
        "proxy": "",
        "telegram_chat_id": "",
        "relogin_login_tab": int(tab or 3),
        "login2_watch_members": list(login2_watch_members or []),
        "login2_watch_downlines": list(login2_watch_downlines or []),
        "login2_downline_alias_map": {},
        "relogin_mode": "playwright",
        "relogin_headless": False,
        "relogin_timeout_seconds": 300,
        "relogin_min_interval_seconds": normalize_account_relogin_min_interval_seconds(
            relogin_min_interval_seconds,
            default=5,
        ),
        "relogin_on_start": False,
        "write_cookie_to_config": True,
        "security_code": str(security_code or "").strip(),
        "time_offset_hours": int(time_offset_hours or 0),
    }


def build_server_file_metadata(path_hint: Optional[Path] = None) -> Dict[str, Any]:
    try:
        path_obj = Path(path_hint or __file__).resolve()
    except Exception:
        path_obj = Path(str(path_hint or __file__))

    try:
        mtime = int(path_obj.stat().st_mtime) if path_obj.exists() else 0
    except Exception:
        mtime = 0

    return {
        "server_file": str(path_obj),
        "server_mtime": int(mtime),
    }


def safe_name(name: str) -> str:
    s = "".join(c for c in str(name) if c.isalnum() or c in ("-", "_")).strip()
    return s or "default"


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            try:
                stat = subprocess.check_output(["ps", "-p", str(pid), "-o", "stat="], stderr=subprocess.DEVNULL, timeout=2)
                state = str(stat.decode("utf-8", "ignore") or "").strip().upper()
                if state.startswith("Z"):
                    return False
            except Exception:
                pass
            return True
        except Exception:
            return False
    try:
        import ctypes  # type: ignore

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong(0)
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        kernel32.CloseHandle(h)
        if not ok:
            return False
        return int(code.value) == STILL_ACTIVE
    except Exception:
        return False


def core_running_flag() -> Tuple[bool, Optional[int]]:
    """
    Return (running, pid_count) for core workers managed by app.py start-core/stop-core.
    兼容：若是 start_all 模式（只写 .run/pids.json），也视为后台已启动。
    """
    try:
        cands = [Path(".run/pids_core.json"), Path(".run/pids.json")]
        for p in cands:
            if not p.exists():
                continue
            obj = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
            if not isinstance(obj, dict):
                continue
            pids = [int(x) for x in (obj.get("pids") or [])]
            alive = [pid for pid in pids if is_pid_running(pid)]
            if alive:
                return True, len(alive)
        return False, 0
    except Exception:
        return False, 0


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def core_primary_pid() -> Optional[int]:
    try:
        cands = [Path(".run/pids_core.json"), Path(".run/pids.json")]
        for p in cands:
            if not p.exists():
                continue
            obj = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
            if not isinstance(obj, dict):
                continue
            pids = [int(x) for x in (obj.get("pids") or [])]
            alive = [pid for pid in pids if is_pid_running(pid)]
            if alive:
                return int(alive[0])
        return None
    except Exception:
        return None


def _read_follow_engine_pid_info() -> Optional[Dict[str, Any]]:
    try:
        p = Path(".run/follow_engine.pid.json")
        if not p.exists():
            return None
        obj = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _follow_engine_process_pids() -> List[int]:
    """Find a live follow engine when its dedicated PID file is stale."""
    current_dir = Path.cwd().resolve()
    found: List[int] = []
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", "ignore")
    except Exception:
        return found
    for line in output.splitlines():
        text = line.strip()
        if not text or "follow_engine.py" not in text:
            continue
        match = re.match(r"^(\d+)\s+", text)
        if not match:
            continue
        try:
            pid = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if not is_pid_running(pid):
            continue
        try:
            cwd_output = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode("utf-8", "ignore")
            process_cwd = next(
                (item[1:].strip() for item in cwd_output.splitlines() if item.startswith("n")),
                "",
            )
            if process_cwd and Path(process_cwd).resolve() != current_dir:
                continue
        except Exception:
            pass
        if pid not in found:
            found.append(pid)
    return found


def follow_engine_running_flag() -> Tuple[bool, Optional[int]]:
    try:
        obj = _read_follow_engine_pid_info()
        if not isinstance(obj, dict):
            return False, None
        pids = [int(x) for x in (obj.get("pids") or [])]
        alive = [pid for pid in pids if is_pid_running(pid)]
        if alive:
            return True, int(alive[0])
        discovered = _follow_engine_process_pids()
        if discovered:
            atomic_write_json(
                Path(".run/follow_engine.pid.json"),
                {"recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [discovered[0]]},
            )
            return True, int(discovered[0])
        return False, None
    except Exception:
        return False, None


SNAPSHOT_WATCH_PID_FILE = Path(".run/odds_snapshot_watch.pid.json")
SNAPSHOT_WATCH_LIFECYCLE_LOCK = threading.Lock()


def _snapshot_watch_process_pids() -> List[int]:
    """Find an already-running watcher when lifecycle PID files are stale."""
    current_dir = Path.cwd().resolve()
    found: List[int] = []
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,command="],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", "ignore")
    except Exception:
        return found
    for line in output.splitlines():
        text = line.strip()
        if not text or "odds_snapshot_watch.py" not in text:
            continue
        match = re.match(r"^(\d+)\s+(.*)$", text)
        if not match:
            continue
        try:
            pid = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if not is_pid_running(pid) or not _is_snapshot_watch_pid(pid):
            continue
        try:
            cwd_output = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode("utf-8", "ignore")
            process_cwd = next(
                (item[1:].strip() for item in cwd_output.splitlines() if item.startswith("n")),
                "",
            )
            if process_cwd and Path(process_cwd).resolve() != current_dir:
                continue
        except Exception:
            pass
        if pid not in found:
            found.append(pid)
    return found


def _snapshot_watch_candidate_pids() -> List[int]:
    seen: set[int] = set()
    candidates: List[int] = []
    for path in (SNAPSHOT_WATCH_PID_FILE, Path(".run/pids_core.json"), Path(".run/pids.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        for raw_pid in obj.get("pids") or []:
            try:
                pid = int(raw_pid)
            except (TypeError, ValueError):
                continue
            if pid not in seen:
                seen.add(pid)
                candidates.append(pid)
    for pid in _snapshot_watch_process_pids():
        if pid not in seen:
            seen.add(pid)
            candidates.append(pid)
    return candidates


def _is_snapshot_watch_pid(pid: int) -> bool:
    if not is_pid_running(pid):
        return False
    command = _pid_command_line(pid).lower()
    return "odds_snapshot_watch.py" in command or "--hdzz-worker odds_snapshot_watch" in command


def snapshot_watch_running_flag() -> Tuple[bool, Optional[int]]:
    try:
        alive = [pid for pid in _snapshot_watch_candidate_pids() if _is_snapshot_watch_pid(pid)]
        if not alive:
            return False, None
        pid = int(alive[0])
        # Heal a stale dedicated PID file so subsequent status checks and stop
        # operations see the worker that was launched by start/start-core.
        try:
            dedicated = json.loads(SNAPSHOT_WATCH_PID_FILE.read_text(encoding="utf-8-sig") or "{}")
            dedicated_pids = [int(value) for value in (dedicated.get("pids") or [])]
        except Exception:
            dedicated_pids = []
        if dedicated_pids != [pid]:
            atomic_write_json(
                SNAPSHOT_WATCH_PID_FILE,
                {"recovered_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [pid]},
            )
        return True, pid
    except Exception:
        return False, None


def ensure_snapshot_watch_running() -> Tuple[bool, bool, str, Optional[int]]:
    with SNAPSHOT_WATCH_LIFECYCLE_LOCK:
        return _ensure_snapshot_watch_running()


def _ensure_snapshot_watch_running() -> Tuple[bool, bool, str, Optional[int]]:
    """Ensure the per-market public snapshot worker has its own session.

    The follow engine can be healthy while the watcher has been reaped with
    the terminal that launched it. Keep this lifecycle check limited to the
    watcher itself; it does not start legacy workers or place orders.
    """
    running, pid = snapshot_watch_running_flag()
    if running:
        return True, False, "", pid
    try:
        runtime_dir = Path.cwd()
        script = runtime_dir / "odds_snapshot_watch.py"
        if not script.exists():
            script = Path(__file__).resolve().parents[4] / "app" / "odds_snapshot_watch.py"
        log_dir = Path(os.environ.get("HDZZ_LOG_DIR") or ".run/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        out_f = open(log_dir / "odds_snapshot_watch.out.log", "ab")
        err_f = open(log_dir / "odds_snapshot_watch.err.log", "ab")
        env = dict(os.environ)
        env.setdefault("HDZZ_APP_DIR", str(runtime_dir))
        apply_playwright_browsers_path(env, APP_ROOT)
        cmd = [
            sys.executable,
            "-u",
            str(script),
            "--data-dir", "data",
            "--orders-dir", "orders",
            "--interval", os.environ.get("ODDS_SNAPSHOT_INTERVAL", "1.0"),
            "--active-seconds", os.environ.get("ODDS_SNAPSHOT_ACTIVE_SECONDS", "120"),
            "--max-workers", os.environ.get("ODDS_SNAPSHOT_MAX_WORKERS", "4"),
            "--target-interval-ms", os.environ.get("ODDS_SNAPSHOT_TARGET_INTERVAL_MS", "400"),
            "--market-deadline-ms", os.environ.get("ODDS_SNAPSHOT_MARKET_DEADLINE_MS", "6000"),
            "--public-timeout-s", os.environ.get("ODDS_SNAPSHOT_PUBLIC_TIMEOUT_S", "2"),
            "--hedge-delay-ms", os.environ.get("ODDS_SNAPSHOT_HEDGE_DELAY_MS", "350"),
            "--scheduler-tick-ms", os.environ.get("ODDS_SNAPSHOT_SCHEDULER_TICK_MS", "50"),
        ]
        kwargs: Dict[str, Any] = {
            "args": cmd,
            "cwd": str(runtime_dir),
            "env": env,
            "stdout": out_f,
            "stderr": err_f,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(**kwargs)
        atomic_write_json(
            SNAPSHOT_WATCH_PID_FILE,
            {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "live": True, "pids": [proc.pid]},
        )
        deadline = time.time() + 0.75
        while time.time() < deadline:
            if not is_pid_running(int(proc.pid)):
                break
            time.sleep(0.1)
        if not is_pid_running(int(proc.pid)):
            existing_running, existing_pid = snapshot_watch_running_flag()
            if existing_running:
                try:
                    out_f.close()
                    err_f.close()
                except Exception:
                    pass
                return True, False, "", existing_pid
            atomic_write_json(
                SNAPSHOT_WATCH_PID_FILE,
                {
                    "failed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "live": True,
                    "pids": [],
                    "last_exit_code": proc.poll(),
                },
            )
            return False, False, "odds_snapshot_watch 启动后立即退出", None
        return True, True, "", int(proc.pid)
    except Exception as exc:
        return False, False, f"启动 odds_snapshot_watch 失败: {exc}", None


def telegram_signal_worker_running_flag() -> Tuple[bool, Optional[int]]:
    try:
        path = Path(".run/telegram_signal.pid.json")
        if not path.exists():
            return False, None
        obj = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
        alive = [int(pid) for pid in (obj.get("pids") or []) if is_pid_running(int(pid))]
        return (True, alive[0]) if alive else (False, None)
    except Exception:
        return False, None


def telegram_signal_worker_health_status(
    alive: bool,
    pid: Optional[int],
    health_path: Path = Path(".run/telegram_signal_health.json"),
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "worker_alive": bool(alive),
        "worker_running": False,
        "worker_healthy": False,
        "worker_pid": pid,
        "worker_state": "stopped" if not alive else "unknown",
    }
    if not alive or not pid:
        return base
    try:
        payload = json.loads(Path(health_path).read_text(encoding="utf-8-sig") or "{}")
    except Exception:
        # Older listeners do not write health information. Keep the legacy
        # status until the next restart loads the health-aware worker.
        base.update({"worker_running": True, "worker_healthy": None})
        return base
    if not isinstance(payload, dict) or int(payload.get("pid") or 0) != int(pid):
        base["worker_state"] = "stale"
        return base
    try:
        heartbeat_age_ms = max(0, int(time.time() * 1000) - int(payload.get("heartbeat_at_ms") or 0))
    except Exception:
        heartbeat_age_ms = 10**9
    state = str(payload.get("state") or "unknown").strip().lower()
    healthy = state == "healthy" and heartbeat_age_ms <= 15000
    base.update(
        {
            "worker_running": bool(healthy),
            "worker_healthy": bool(healthy),
            "worker_state": "stale" if heartbeat_age_ms > 15000 else state,
            "worker_heartbeat_age_ms": heartbeat_age_ms,
        }
    )
    error = re.sub(r"\s+", " ", str(payload.get("error") or "")).strip()
    if error:
        base["worker_error"] = error[:240]
    return base

def _run_app_command(args: List[str], timeout_s: int = 20) -> Tuple[int, str]:
    runtime_dir = os.getcwd()
    if is_frozen_runtime():
        cmd = [sys.executable] + list(args)
    else:
        launcher_path = Path(runtime_dir) / "app.py"
        if not launcher_path.exists():
            launcher_path = Path(__file__).resolve().parents[4] / "app" / "app.py"
        cmd = [sys.executable, "-u", str(launcher_path)] + list(args)
    env = dict(os.environ)
    env["HDZZ_APP_DIR"] = runtime_dir
    apply_playwright_browsers_path(env, APP_ROOT)
    p = subprocess.run(cmd, cwd=runtime_dir, env=env, capture_output=True, text=True, timeout=max(3, int(timeout_s)))
    out = (p.stderr or p.stdout or "").strip()
    return int(p.returncode), str(out)


def _spawn_app_command(args: List[str]) -> None:
    runtime_dir = os.getcwd()
    if is_frozen_runtime():
        cmd = [sys.executable] + list(args)
    else:
        launcher_path = Path(runtime_dir) / "app.py"
        if not launcher_path.exists():
            launcher_path = Path(__file__).resolve().parents[4] / "app" / "app.py"
        cmd = [sys.executable, "-u", str(launcher_path)] + list(args)
    env = dict(os.environ)
    env["HDZZ_APP_DIR"] = runtime_dir
    apply_playwright_browsers_path(env, APP_ROOT)
    kwargs = {
        "args": cmd,
        "cwd": runtime_dir,
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    subprocess.Popen(**kwargs)


def _pid_command_line(pid: int) -> str:
    try:
        if os.name == "nt":
            command = (
                f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={int(pid)}\";"
                "if($p){[Console]::OutputEncoding=[Text.Encoding]::UTF8;Write-Output $p.CommandLine}"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", command],
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return str(out.decode("utf-8", "ignore") or "").strip()
        out = subprocess.check_output(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return str(out.decode("utf-8", "ignore") or "").strip()
    except Exception:
        return ""


def _is_named_worker_running(worker_name: str) -> bool:
    worker = str(worker_name or "").strip()
    if not worker:
        return False
    try:
        cands = [Path(".run/pids_core.json"), Path(".run/pids.json")]
        for p in cands:
            if not p.exists():
                continue
            obj = json.loads(p.read_text(encoding="utf-8-sig") or "{}")
            if not isinstance(obj, dict):
                continue
            pids = [int(x) for x in (obj.get("pids") or [])]
            for pid in pids:
                if not is_pid_running(pid):
                    continue
                cmd = _pid_command_line(pid)
                if not cmd:
                    continue
                if f"--hdzz-worker {worker}" in cmd or f"{worker}.py" in cmd:
                    return True
    except Exception:
        return False
    return False


def ensure_ag_wmc_watch_running() -> Tuple[bool, bool, str]:
    if _is_named_worker_running("ag_wmc_watch"):
        return True, False, ""
    try:
        env = dict(os.environ)
        apply_playwright_browsers_path(env, APP_ROOT)
        log_dir = Path(os.environ.get("HDZZ_LOG_DIR") or ".run/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        out_path = log_dir / "ag_wmc_watch.out.log"
        err_path = log_dir / "ag_wmc_watch.err.log"
        out_f = open(out_path, "ab")
        err_f = open(err_path, "ab")
        if is_frozen_runtime():
            cmd = [
                sys.executable,
                "--hdzz-worker",
                "ag_wmc_watch",
                "--config",
                "config.json",
                "--timeout",
                "30",
                "--interval",
                "3",
                "--only-orders",
            ]
        else:
            launcher_path = Path(os.getcwd()) / "ag_wmc_watch.py"
            if not launcher_path.exists():
                launcher_path = Path(__file__).resolve().parents[4] / "app" / "ag_wmc_watch.py"
            cmd = [
                sys.executable,
                "-u",
                str(launcher_path),
                "--config",
                "config.json",
                "--timeout",
                "30",
                "--interval",
                "3",
                "--only-orders",
            ]
        kwargs = {
            "args": cmd,
            "cwd": os.getcwd(),
            "env": env,
            "stdout": out_f,
            "stderr": err_f,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        p = subprocess.Popen(**kwargs)
        try:
            pid_file = Path(".run/pids.json")
            info = read_json_file(pid_file, {})
            if not isinstance(info, dict):
                info = {}
            pids = [int(x) for x in (info.get("pids") or []) if str(x).strip()]
            if int(p.pid) not in pids:
                pids.append(int(p.pid))
                info["pids"] = pids
                info.setdefault("started_at", now_iso())
                info.setdefault("host", "127.0.0.1")
                info.setdefault("port", 0)
                info.setdefault("live", True)
                atomic_write_json(pid_file, info)
        except Exception:
            pass
        return True, True, ""
    except Exception as e:
        return False, False, f"start ag_wmc_watch failed: {e}"


def ensure_ps3838_watch_running(config_path: Path, data_dir: Path) -> Tuple[bool, bool, str]:
    """启动独立的 PS3838 只读 worker；不加入 app.py 的跟单 worker 列表。"""
    runtime_dir = Path(data_dir) / "ps3838"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pid_path = runtime_dir / "worker.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip()) if pid_path.exists() else 0
    except Exception:
        pid = 0
    if pid and is_pid_running(pid):
        return True, False, ""
    try:
        env = dict(os.environ)
        apply_playwright_browsers_path(env, APP_ROOT)
        log_dir = Path(os.environ.get("HDZZ_LOG_DIR") or ".run/logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        out_f = open(log_dir / "ps3838_watch.out.log", "ab")
        err_f = open(log_dir / "ps3838_watch.err.log", "ab")
        launcher_path = Path(os.getcwd()) / "ps3838_watch.py"
        if not launcher_path.exists():
            launcher_path = Path(__file__).resolve().parents[4] / "app" / "ps3838_watch.py"
        cmd = [sys.executable, "-u", str(launcher_path), "--config", str(config_path), "--data-dir", str(data_dir)]
        kwargs: Dict[str, Any] = {
            "args": cmd,
            "cwd": str(Path(config_path).resolve().parent),
            "env": env,
            "stdout": out_f,
            "stderr": err_f,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(**kwargs)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        return True, True, ""
    except Exception as exc:
        return False, False, f"start ps3838_watch failed: {exc}"


def ps3838_login_in_progress(data_dir: Path, account: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """重复点击登录不能打断正在输入的 2FA；崩溃后残留状态不能阻止重开。"""
    if not bool(account.get("enabled", True)) or not state.get("relogin_in_progress"):
        return False
    try:
        pid = int((Path(data_dir) / "ps3838" / "worker.pid").read_text(encoding="utf-8").strip())
        return bool(pid and is_pid_running(pid))
    except Exception:
        return False


def read_ps3838_status(data_dir: Path, name: str, *, enabled: bool = True) -> Dict[str, Any]:
    path = Path(data_dir) / "ps3838" / "state" / f"{safe_name(name)}.json"
    state = read_json_file(path, {})
    status = str(state.get("status") or "offline").strip().lower()
    if status not in {"online", "offline", "relogin"}:
        status = "offline"
    message = str(state.get("status_message") or "").strip()
    login_waiting = ps3838_login_in_progress(data_dir, {"enabled": enabled}, state)
    if not enabled:
        status = "offline"
        message = "已停用，不会自动登录或扫描"
    elif status == "relogin" and not login_waiting:
        status = "offline"
        message = "登录进程已停止；请点击重新登录继续"
    return {
        "status": status,
        "status_message": message or ({"online": "在线", "relogin": "登录中"}.get(status) or "未登录"),
        "needs_manual_login": enabled and status != "online",
        "has_session": enabled and bool(state.get("session_ready")),
        "relogin_in_progress": login_waiting,
        "last_ok_at": float(state.get("last_ok_at") or 0),
        "last_poll_at": float(state.get("last_poll_at") or 0),
        "order_count": int(state.get("order_count") or 0),
        "new_count": int(state.get("new_count") or 0),
        "update_count": int(state.get("update_count") or 0),
    }

def _prepare_stop_all_without_killing_self() -> Tuple[bool, str]:
    """
    When WebUI is started by app.py start (ALL mode), current webui_server pid is in .run/pids.json.
    If /api/stop directly calls app.py stop, current process may get killed before HTTP response is sent.
    We remove current pid from pid list first, then stop remaining workers.
    """
    try:
        p = Path(".run/pids.json")
        if not p.exists():
            return True, ""
        obj = read_json_file(p, {})
        if not isinstance(obj, dict):
            return True, ""
        cur = int(os.getpid())
        raw = obj.get("pids") or []
        pids: List[int] = []
        for x in raw:
            try:
                pids.append(int(x))
            except Exception:
                continue
        if cur not in pids:
            return True, ""
        obj["pids"] = [pid for pid in pids if int(pid) != cur]
        atomic_write_json(p, obj)
        return True, ""
    except Exception as e:
        return False, str(e)

LAST_LATENCY = 0
LAST_LATENCY_CHECK = 0

def get_latency(target="www.baidu.com", port=80):
    global LAST_LATENCY, LAST_LATENCY_CHECK
    now = time.time()
    if now - LAST_LATENCY_CHECK < 5:  # Cache for 5 seconds
        return LAST_LATENCY
        
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        t1 = time.time()
        s.connect((target, port))
        t2 = time.time()
        s.close()
        lat = int((t2 - t1) * 1000)
        LAST_LATENCY = lat
        LAST_LATENCY_CHECK = now
        return lat
    except Exception:
        LAST_LATENCY = 999
        LAST_LATENCY_CHECK = now
        return 999

def atomic_write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # IMPORTANT: 多进程同时写同一个 *.tmp 会导致文件内容互相覆盖/交错，最终把 JSON 写坏。
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    last_err: Optional[Exception] = None
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 + i * 0.02)
        except Exception as e:
            last_err = e
            break
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    if last_err:
        raise last_err


def read_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else dict(default)
        except json.JSONDecodeError:
            # 兼容：文件末尾被误追加了多余内容（常见于并发写入临时文件冲突）
            try:
                dec = json.JSONDecoder()
                obj, _ = dec.raw_decode(raw.lstrip("\ufeff").lstrip())
                return obj if isinstance(obj, dict) else dict(default)
            except Exception:
                return dict(default)
    except Exception:
        return dict(default)


def ensure_json_file(path: Path, default: Dict[str, Any]) -> None:
    if path.exists():
        return
    atomic_write_json(path, default)


def mask_secret(s: str, keep: int = 3) -> str:
    s = str(s or "")
    if not s:
        return ""
    if len(s) <= keep:
        return "*" * len(s)
    return ("*" * (len(s) - keep)) + s[-keep:]


def redact_secret_in_text(text: str, secret: str) -> str:
    s = str(text or "")
    sec = str(secret or "")
    if not s or not sec:
        return s
    try:
        return s.replace(sec, "***")
    except Exception:
        return s


def apply_telegram_config_update(cfg: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg if isinstance(cfg, dict) else {})
    # 账号状态 Telegram 推送已下线；保存通知配置时清理旧字段，避免旧配置继续产生歧义。
    for legacy_key in (
        "telegram_status_push_enabled",
        "telegram_status_bot_token",
        "telegram_status_chat_id",
        "telegram_status_proxy",
    ):
        out.pop(legacy_key, None)
    if bool(data.get("clear_config") or False):
        out["telegram_bot_token"] = ""
        out["telegram_chat_id"] = ""
        out["telegram_allow_chat"] = ""
        out["telegram_proxy"] = ""
        out["telegram_backend_order_push_enabled"] = False
        out["telegram_backend_order_bot_token"] = ""
        out["telegram_backend_order_chat_id"] = ""
        out["telegram_backend_order_proxy"] = ""
        return out

    bot_token = str(data.get("bot_token") or "").strip()
    chat_id = str(data.get("chat_id") or "").strip()
    allow_chat = (str(data.get("allow_chat") or "").strip() if "allow_chat" in data else None)
    proxy = str(data.get("proxy") or "").strip()
    clear_token = bool(data.get("clear_token") or False)
    order_forward_enabled = data.get("order_forward_enabled", None)
    backend_order_push_enabled = data.get("backend_order_push_enabled", None)
    backend_order_bot_token = (
        str(data.get("backend_order_bot_token") or "").strip()
        if "backend_order_bot_token" in data
        else None
    )
    backend_order_chat_id = (
        str(data.get("backend_order_chat_id") or "").strip()
        if "backend_order_chat_id" in data
        else None
    )
    backend_order_proxy = (
        str(data.get("backend_order_proxy") or "").strip()
        if "backend_order_proxy" in data
        else None
    )
    clear_backend_order_token = bool(data.get("clear_backend_order_token") or False)

    if clear_token:
        out["telegram_bot_token"] = ""
    elif bot_token:
        out["telegram_bot_token"] = bot_token
    if chat_id:
        out["telegram_chat_id"] = chat_id
    if allow_chat is not None:
        out["telegram_allow_chat"] = allow_chat
    if proxy or proxy == "":
        out["telegram_proxy"] = proxy
    if order_forward_enabled is not None:
        out["telegram_order_forward_enabled"] = bool(order_forward_enabled)
    if backend_order_push_enabled is not None:
        out["telegram_backend_order_push_enabled"] = bool(backend_order_push_enabled)
    if clear_backend_order_token:
        out["telegram_backend_order_bot_token"] = ""
    elif backend_order_bot_token:
        out["telegram_backend_order_bot_token"] = backend_order_bot_token
    if backend_order_chat_id is not None:
        out["telegram_backend_order_chat_id"] = backend_order_chat_id
    if backend_order_proxy is not None:
        out["telegram_backend_order_proxy"] = backend_order_proxy
    return out


def format_backend_order_telegram_test_message(cfg: Dict[str, Any]) -> str:
    return format_backend_order_telegram_message(
        {
            "league": "后台订单推送测试",
            "sides": "主队 v 客队 (0 - 0)",
            "betType": "(滚球) 角球数 - 大 / 小",
            "line": "小 11",
            "odds": "0.70",
            "stake": "100",
        },
        ui_theme=_load_order_tg_ui_theme(cfg),
    )


def tg_send_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    bot_token = str(bot_token or "").strip()
    chat_id = str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return {"ok": False, "error": "missing_bot_token_or_chat_id"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None

    def _post_once(px: Optional[Dict[str, str]]) -> requests.Response:
        # WebUI 的“发送测试”主要用于配置验证；Windows + 本地代理/公司代理场景下
        # 经常出现自签名证书链，这里允许在测试请求里关闭证书校验，避免把用户卡在 UI 上。
        return requests.post(
            url,
            json=payload,
            timeout=max(5, int(timeout_s)),
            proxies=px,
            verify=False,
        )

    try:
        r = _post_once(proxies)
    except Exception as e:
        if proxy:
            try:
                r = _post_once(None)
            except Exception:
                return {"ok": False, "error": _describe_proxy_exc(e)}
        else:
            return {"ok": False, "error": _describe_proxy_exc(e)}

    try:
        r.raise_for_status()
        obj = r.json()
        if isinstance(obj, dict) and obj.get("ok"):
            return {"ok": True}
        return {"ok": False, "error": str(obj)}
    except Exception as e:
        body = ""
        try:
            body = str(r.text or "").strip()
        except Exception:
            body = ""
        if body:
            return {"ok": False, "error": f"{_describe_proxy_exc(e)}; body={body[:300]}"}
        return {"ok": False, "error": _describe_proxy_exc(e)}


def _normalize_proxy_input(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    # 优先使用 socks5h（代理端解析 DNS），对部分环境更稳定
    if proxy.lower().startswith("socks5://"):
        return "socks5h://" + proxy[len("socks5://") :]
    if "|" not in proxy:
        return proxy
    parts = [p.strip() for p in proxy.split("|")]
    while parts and not parts[-1]:
        parts.pop()
    if len(parts) == 2 and parts[0] and parts[1]:
        ip, port = parts
        return f"socks5h://{ip}:{port}"
    # 兼容：你可能会把到期日等备注拼在后面（例如 ip|port|user|pass|2026.1.22|）
    # 这里仅取前4段，多余段忽略
    if len(parts) >= 4 and parts[0] and parts[1]:
        ip, port, user, pwd = parts[:4]
        if user and pwd:
            return f"socks5h://{user}:{pwd}@{ip}:{port}"
        return f"socks5h://{ip}:{port}"
    return proxy


def _adspower_profile_to_payload(profile: Dict[str, Any]) -> Dict[str, Any]:
    p = profile if isinstance(profile, dict) else {}
    profile_id = str(p.get("profile_id") or "").strip()
    profile_no = str(p.get("profile_no") or "").strip()
    name = str(p.get("name") or "").strip()
    group_name = str(p.get("group_name") or "").strip()
    domain_name = str(p.get("domain_name") or "").strip()
    proxy_url = _normalize_proxy_input(adspower_profile_proxy_url(p))
    base_hint = ""
    if domain_name:
        dom = domain_name.strip().lstrip(".")
        if dom:
            base_hint = f"https://{dom}"
    return {
        "profile_id": profile_id,
        "profile_no": profile_no,
        "name": name,
        "group_name": group_name,
        "domain_name": domain_name,
        "base_url_hint": base_hint,
        "proxy": proxy_url,
    }


def _describe_proxy_exc(e: Exception) -> str:
    msg = str(e) or e.__class__.__name__
    try:
        if isinstance(e, requests.exceptions.ConnectTimeout):
            return f"connect_timeout: {msg}"
        if isinstance(e, requests.exceptions.ReadTimeout):
            return f"read_timeout: {msg}"
        if isinstance(e, requests.exceptions.ProxyError):
            return f"proxy_error: {msg}"
        if isinstance(e, requests.exceptions.SSLError):
            return f"ssl_error: {msg}"
        if isinstance(e, requests.exceptions.ConnectionError):
            return f"connection_error: {msg}"
    except Exception:
        pass
    return msg


def _normalize_member_usage(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"read", "quote", "data", "readonly"}:
        return "read"
    return "bet"


def member_usage_from_row(member: Dict[str, Any]) -> str:
    if not isinstance(member, dict):
        return "bet"
    if "usage" in member:
        return _normalize_member_usage(member.get("usage"))
    if str(member.get("role") or "").strip().lower() == "quote":
        return "read"
    return "bet"


def apply_member_usage_patch(patch: Dict[str, Any], usage: Any) -> None:
    normalized = _normalize_member_usage(usage)
    patch["usage"] = normalized
    patch["role"] = "quote" if normalized == "read" else ""


def _is_readonly_member_candidate(member: Dict[str, Any]) -> bool:
    if not isinstance(member, dict):
        return False
    if member_usage_from_row(member) != "read":
        return False
    if str(member.get("status") or "").strip().lower() != "online":
        return False
    return bool(
        str(member.get("base_url") or "").strip()
        and str(member.get("uid") or "").strip()
        and str(member.get("ver") or "").strip()
        and str(member.get("cookie") or "").strip()
    )


def select_readonly_member_from_members(members: List[Dict[str, Any]], member_id: str = "") -> Optional[Dict[str, Any]]:
    return _pick_readonly_quote_member(members, preferred_member_id=member_id)


def _is_online_session_member(member: Dict[str, Any]) -> bool:
    if not isinstance(member, dict):
        return False
    if str(member.get("status") or "").strip().lower() != "online":
        return False
    return bool(
        str(member.get("base_url") or "").strip()
        and str(member.get("uid") or "").strip()
        and str(member.get("ver") or "").strip()
        and str(member.get("cookie") or "").strip()
    )


def select_chashui_member_from_members(
    members: List[Dict[str, Any]],
    member_id: str = "",
) -> Optional[Dict[str, Any]]:
    """选择查水会话：优先只读会员，没有时回退到在线普通会员。"""
    selected = select_readonly_member_from_members(members, member_id=member_id)
    if selected:
        return selected

    candidates = [m for m in (members or []) if _is_online_session_member(m)]
    preferred = str(member_id or "").strip()
    if preferred:
        for member in candidates:
            if str(member.get("id") or "").strip() == preferred:
                return member
    return candidates[0] if candidates else None


def _select_readonly_member_with_excludes(
    members: List[Dict[str, Any]],
    *,
    member_id: str = "",
    exclude_member_ids: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    return _pick_readonly_quote_member(
        members,
        preferred_member_id=member_id,
        exclude_member_ids=exclude_member_ids,
    )


def _readonly_member_ref(member: Optional[Dict[str, Any]]) -> Dict[str, str]:
    row = member if isinstance(member, dict) else {}
    return {
        "id": str(row.get("id") or "").strip(),
        "label": str(row.get("label") or row.get("username") or row.get("id") or "").strip(),
    }


def _patch_arsenal_member_fields(arsenal_path: Path, member_id: str, patch: Dict[str, Any]) -> None:
    mid = str(member_id or "").strip()
    if not mid or not isinstance(patch, dict) or not patch:
        return
    doc = read_json_file(arsenal_path, {"members": []})
    members = doc.get("members") if isinstance(doc, dict) else []
    changed = False
    out: List[Dict[str, Any]] = []
    for row in members if isinstance(members, list) else []:
        if isinstance(row, dict) and str(row.get("id") or "").strip() == mid:
            merged = dict(row)
            merged.update(patch)
            out.append(merged)
            changed = True
        elif isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append(row)
    if not changed:
        return
    doc["members"] = out
    atomic_write_json(arsenal_path, doc)


def _readonly_failure_requires_offline(reason: str) -> bool:
    text = str(reason or "").strip()
    low = text.lower()
    if not low:
        return False
    if "1x014" in low or "doublelogin" in low or "session_missing" in low or "member_missing_uid_cookie_ver" in low:
        return True
    if "当前会员会话缺少" in text or "缺少 uid/cookie" in low or "缺少 uid" in low or "缺少 cookie" in low:
        return True
    return False


def _note_readonly_member_failure(arsenal_path: Path, member: Dict[str, Any], reason: str) -> None:
    member_id = str((member or {}).get("id") or "").strip()
    if not member_id:
        return
    patch: Dict[str, Any] = {"last_error": str(reason or "").strip()[:200]}
    if _readonly_failure_requires_offline(reason):
        patch["status"] = "offline"
    try:
        _patch_arsenal_member_fields(arsenal_path, member_id, patch)
    except Exception:
        pass


def _build_readonly_failover_candidates(
    members: List[Dict[str, Any]],
    *,
    preferred_member_id: str = "",
    exclude_member_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    candidates = [m for m in _list_readonly_quote_candidates(members) if isinstance(m, dict)]
    if not candidates:
        candidates = [m for m in (members or []) if _is_online_session_member(m)]
    if not candidates:
        return []
    excludes = {str(x).strip() for x in (exclude_member_ids or set()) if str(x).strip()}
    pool = [m for m in candidates if str(m.get("id") or "").strip() not in excludes]
    if not pool:
        pool = candidates[:]
    preferred = str(preferred_member_id or "").strip()
    out: List[Dict[str, Any]] = []
    if preferred:
        for member in pool:
            if str(member.get("id") or "").strip() == preferred:
                out.append(member)
                break
    for member in pool:
        if any(str(member.get("id") or "").strip() == str(existing.get("id") or "").strip() for existing in out):
            continue
        out.append(member)
    return out


def _run_readonly_member_with_failover(
    *,
    arsenal_path: Path,
    members: List[Dict[str, Any]],
    preferred_member_id: str = "",
    lane: str = "games",
    exclude_member_ids: Optional[Set[str]] = None,
    runner: Callable[[Dict[str, Any]], Any],
) -> Dict[str, Any]:
    candidates = _build_readonly_failover_candidates(
        members,
        preferred_member_id=preferred_member_id,
        exclude_member_ids=exclude_member_ids,
    )
    if not candidates:
        return {
            "ok": False,
            "member": None,
            "error": "readonly_pool_empty",
            "fallback_used": False,
            "attempted_member_ids": [],
            "lane": str(lane or "games"),
        }

    attempted_member_ids: List[str] = []
    last_error = "readonly_pool_empty"
    last_exc: Optional[Exception] = None
    last_member: Optional[Dict[str, Any]] = None

    for member in candidates:
        member_id = str(member.get("id") or "").strip()
        if member_id:
            attempted_member_ids.append(member_id)
        last_member = member
        try:
            result = runner(member)
            return {
                "ok": True,
                "member": member,
                "result": result,
                "fallback_used": len(attempted_member_ids) > 1,
                "attempted_member_ids": attempted_member_ids[:],
                "lane": str(lane or "games"),
            }
        except Exception as e:
            last_exc = e
            last_error = str(e or "readonly_member_failed")
            _note_readonly_member_failure(arsenal_path, member, last_error)

    return {
        "ok": False,
        "member": last_member,
        "error": last_error,
        "exception": last_exc,
        "fallback_used": len(attempted_member_ids) > 1,
        "attempted_member_ids": attempted_member_ids[:],
        "lane": str(lane or "games"),
    }


def _capture_gismo_token_with_failover(
    *,
    arsenal_path: Path,
    preferred_member_id: str,
    data_dir: Path,
    match_id: str = "",
    members: Optional[List[Dict[str, Any]]] = None,
    exclude_member_ids: Optional[Set[str]] = None,
    timeout_s: int = 16,
    capture_func: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    pool = [m for m in (members or load_readonly_quote_pool(arsenal_path)) if isinstance(m, dict)]
    capture = capture_func or _chashui_capture_gismo_token
    result_by_member_id: Dict[str, Dict[str, Any]] = {}

    def runner(member: Dict[str, Any]) -> Dict[str, Any]:
        result = capture(member, data_dir, match_id=match_id, timeout_s=timeout_s)
        result_by_member_id[str(member.get("id") or "").strip()] = result
        if not bool(result.get("ok")):
            detail = str(result.get("detail") or result.get("message") or "token_capture_failed").strip()
            raise RuntimeError(detail or "token_capture_failed")
        return result

    out = _run_readonly_member_with_failover(
        arsenal_path=arsenal_path,
        members=pool,
        preferred_member_id=preferred_member_id,
        lane="events",
        exclude_member_ids=exclude_member_ids,
        runner=runner,
    )
    if out.get("ok"):
        return out
    member_id = str(((out.get("member") or {}) if isinstance(out.get("member"), dict) else {}).get("id") or "").strip()
    out["result"] = result_by_member_id.get(member_id) or {
        "ok": False,
        "message": str(out.get("error") or "token_capture_failed"),
    }
    return out


def _isp_to_cn(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    mapping = [
        ("huawei cloud service data center", "华为云数据中心"),
        ("huawei cloud", "华为云"),
        ("china telecom", "中国电信"),
        ("chinanet", "中国电信"),
        ("china unicom", "中国联通"),
        ("china mobile", "中国移动"),
        ("alibaba", "阿里云"),
        ("aliyun", "阿里云"),
        ("tencent", "腾讯云"),
        ("baidu", "百度云"),
        ("amazon", "AWS"),
        ("microsoft", "微软 Azure"),
        ("google", "Google"),
    ]
    for k, v in mapping:
        if k in low:
            return v
    return s


def _cn_norm(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    # ip-api 的中文字段偶尔是繁体，这里做少量高频归一化到简体。
    fixes = {
        "中華人民共和國": "中华人民共和国",
        "臺灣": "台湾",
        "香港特別行政區": "香港特别行政区",
        "澳門特別行政區": "澳门特别行政区",
    }
    return fixes.get(s, s)


def _ip_geo(*, ip: str, timeout_s: int = 8) -> Dict[str, Any]:
    ip = str(ip or "").strip()
    if not ip:
        return {"ok": False, "error": "empty_ip"}
    try:
        # ip-api 免费接口（无需 token）；注意其字段名
        url = f"http://ip-api.com/json/{ip}"
        params = {"fields": "status,message,country,regionName,city,isp,org,as,query", "lang": "zh-CN"}
        r = requests.get(url, params=params, timeout=max(3, int(timeout_s)))
        r.raise_for_status()
        obj = r.json()
        if not isinstance(obj, dict):
            return {"ok": False, "error": "bad_geo_response"}
        if str(obj.get("status") or "").lower() != "success":
            return {"ok": False, "error": str(obj.get("message") or "geo_failed")}
        return {
            "ok": True,
            "ip": str(obj.get("query") or ip),
            "country": _cn_norm(str(obj.get("country") or "")),
            "region": _cn_norm(str(obj.get("regionName") or "")),
            "city": _cn_norm(str(obj.get("city") or "")),
            "isp": _isp_to_cn(str(obj.get("isp") or "")),
            "org": str(obj.get("org") or ""),
            "as": str(obj.get("as") or ""),
        }
    except Exception as e:
        return {"ok": False, "error": _describe_proxy_exc(e)}


def _proxy_host_ip(proxy: str) -> str:
    try:
        u = urlparse(str(proxy or "").strip())
        host = u.hostname or ""
        # hostname 只有在 URL 解析成功时才会有；如果用户没写 scheme，忽略
        return str(host or "").strip()
    except Exception:
        return ""


_GEO_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _geo_cached(ip: str, *, ttl_s: int = 86400) -> Dict[str, Any]:
    ip = str(ip or "").strip()
    if not ip:
        return {"ok": False, "error": "empty_ip"}
    now = time.time()
    hit = _GEO_CACHE.get(ip)
    if hit:
        ts, val = hit
        if (now - ts) < max(10, int(ttl_s)):
            return val
    val = _ip_geo(ip=ip, timeout_s=6)
    _GEO_CACHE[ip] = (now, val)
    return val


def _proxy_display(proxy_raw: str) -> Dict[str, Any]:
    proxy_norm = _normalize_proxy_input(str(proxy_raw or "").strip())
    if not proxy_norm:
        return {"label": "本地代理", "proxy_ip": "", "proxy_geo": None}
    ip = _proxy_host_ip(proxy_norm)
    geo = None
    if ip and ip.replace(".", "").isdigit():
        g = _geo_cached(ip)
        if g.get("ok"):
            geo = g
    return {"label": ip or "已配置代理", "proxy_ip": ip, "proxy_geo": geo}


def proxy_test(*, proxy: str, timeout_s: int = 10, base_url: str = "") -> Dict[str, Any]:
    """
    测试代理可用性（支持 http/https；socks5 需要 PySocks 依赖）。
    优先访问 ipify 获取出口 IP；失败则回退到 baidu 做连通性判断。
    """
    proxy = _normalize_proxy_input(proxy)
    if not proxy:
        return {"ok": False, "error": "empty_proxy"}

    sess = requests.Session()
    sess.trust_env = False

    # 返回“代理服务器 IP”的归属地（不依赖通过代理访问外网）
    proxy_ip = _proxy_host_ip(proxy)
    proxy_geo: Optional[Dict[str, Any]] = None
    if proxy_ip and proxy_ip.replace(".", "").isdigit():
        g = _ip_geo(ip=proxy_ip, timeout_s=min(10, max(3, int(timeout_s))))
        if g.get("ok"):
            proxy_geo = g

    # socks5:// 对域名默认本地解析；很多环境需要 socks5h:// 走代理解析 DNS
    candidates = [proxy]
    if proxy.lower().startswith("socks5://"):
        candidates.insert(0, "socks5h://" + proxy[len("socks5://") :])

    def try_get(url: str, *, verify: bool) -> Dict[str, Any]:
        last_err = ""
        for pxy in candidates:
            proxies = {"http": pxy, "https": pxy}
            t0 = time.time()
            try:
                r = sess.get(url, timeout=max(3, int(timeout_s)), proxies=proxies, verify=verify, allow_redirects=True)
                elapsed_ms = int((time.time() - t0) * 1000)
                if int(r.status_code) >= 400:
                    last_err = f"http_{int(r.status_code)}"
                    continue
                ip = ""
                if "ipify" in url:
                    try:
                        obj = r.json()
                        if isinstance(obj, dict) and obj.get("ip"):
                            ip = str(obj.get("ip") or "")
                    except Exception:
                        pass
                return {"ok": True, "test_url": url, "status": int(r.status_code), "elapsed_ms": elapsed_ms, "ip": ip}
            except Exception as e:
                last_err = _describe_proxy_exc(e)
                continue
        return {"ok": False, "error": last_err or "request_failed"}

    base_url = str(base_url or "").strip().rstrip("/")
    base_err = ""
    if base_url.startswith("http://") or base_url.startswith("https://"):
        base_res = try_get(base_url + "/", verify=False)
        if base_res.get("ok"):
            # 目标站点可访问 ≠ 一定能访问外网；但用户希望能看到出口 IP/地区，继续尝试 ipify 获取。
            ip_res = try_get("https://api.ipify.org?format=json", verify=True)
            if ip_res.get("ok") and ip_res.get("ip"):
                geo = _ip_geo(ip=str(ip_res.get("ip") or ""), timeout_s=min(10, max(3, int(timeout_s))))
                base_res["ip"] = str(ip_res.get("ip") or "")
                if geo.get("ok"):
                    base_res["geo"] = geo
            if proxy_ip:
                base_res["proxy_ip"] = proxy_ip
            if proxy_geo:
                base_res["proxy_geo"] = proxy_geo
            return base_res
        base_err = str(base_res.get("error") or "")

    err_by_url: Dict[str, str] = {}
    for url, verify in [
        ("https://api.ipify.org?format=json", True),
        ("https://www.baidu.com", True),
        ("http://www.baidu.com", False),
    ]:
        r1 = try_get(url, verify=verify)
        if r1.get("ok"):
            if r1.get("ip"):
                geo = _ip_geo(ip=str(r1.get("ip") or ""), timeout_s=min(10, max(3, int(timeout_s))))
                if geo.get("ok"):
                    r1["geo"] = geo
            if proxy_ip:
                r1["proxy_ip"] = proxy_ip
            if proxy_geo:
                r1["proxy_geo"] = proxy_geo
            return r1
        err_by_url[url] = str(r1.get("error") or "")

    # 给前端更明确的失败原因（连接/认证/DNS/超时/证书等）
    parts: List[str] = []
    if base_url:
        parts.append(f"base={base_err or 'failed'}")
    parts.append(f"ipify={err_by_url.get('https://api.ipify.org?format=json') or 'failed'}")
    parts.append(f"baidu_https={err_by_url.get('https://www.baidu.com') or 'failed'}")
    parts.append(f"baidu_http={err_by_url.get('http://www.baidu.com') or 'failed'}")
    detail = " | ".join([p for p in parts if p])
    out: Dict[str, Any] = {"ok": False, "error": f"all_tests_failed: {detail}"}
    if proxy_ip:
        out["proxy_ip"] = proxy_ip
    if proxy_geo:
        out["proxy_geo"] = proxy_geo
    return out


def parse_xml_kv(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for child in list(root):
        if child.tag:
            out[str(child.tag)] = (child.text or "").strip()
    return out


def member_ping_system_time(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str = "",
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    会员端在线检测（不下注）：p=get_systemTime
    成功：返回 XML <systemtime>...
    """
    base_url = str(base_url or "").rstrip("/")
    url = f"{base_url}/transform.php?ver={ver}"
    form = {"ver": ver, "p": "get_systemTime", "uid": uid, "langx": "zh-cn"}
    body = urlencode(form).encode("utf-8")
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": base_url,
        "referer": base_url + "/",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    }
    if cookie:
        headers["cookie"] = cookie

    try:
        sess = requests.Session()
        sess.trust_env = False
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        r = sess.post(url, data=form, headers=headers, timeout=timeout_s, proxies=proxies, verify=False)
        r.raise_for_status()
        text = r.text or ""
    except Exception as e:
        return {"ok": False, "error": f"request_failed: {e}"}

    text_strip = (text or "").strip()
    if not text_strip:
        return {"ok": False, "error": "empty_response"}
    if not text_strip.startswith("<"):
        return {"ok": False, "error": "non_xml_response", "raw": text_strip[:200]}

    try:
        data = parse_xml_kv(text_strip)
    except Exception as e:
        return {"ok": False, "error": f"xml_parse_failed: {e}", "raw": text_strip[:200]}

    systemtime = data.get("systemtime") or ""
    if systemtime:
        return {"ok": True, "systemtime": systemtime, "data": data}
    code = data.get("code") or data.get("status") or ""
    return {"ok": False, "error": "no_systemtime", "code": code, "data": data}


def read_orders_file(path: Path, since: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    # 简化：直接顺序读取，忽略最后一行可能的半写入
    try:
        # utf-8-sig 兼容某些工具写入的 BOM
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                ts = int(obj.get("ts") or 0)
                if since and ts <= since:
                    continue
                out.append(obj)
    except Exception:
        return []
    # 最新在前
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    return out[: max(1, int(limit))]


def read_ps3838_dashboard_orders(
    data_dir: Path, accounts: List[Dict[str, Any]], *, target: str = "", since: int = 0, limit: int = 200,
) -> List[Dict[str, Any]]:
    """只读合并平博采集流水；同账号同票号取最新事件，不进入跟单订单目录。"""
    result: List[Dict[str, Any]] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        name = str(account.get("name") or "").strip()
        if not name or (target and target != name):
            continue
        latest: Dict[str, Dict[str, Any]] = {}
        path = Path(data_dir) / "ps3838" / "orders" / f"{ps3838_safe_name(name)}.jsonl"
        try:
            with path.open(encoding="utf-8-sig") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            continue
                        oid = str(row.get("orderId") or "").strip()
                        ts = int(row.get("ts") or 0)
                        if not oid or ts <= 0:
                            continue
                    except (ValueError, TypeError):
                        continue  # 容忍末行尚未写完。
                    previous = latest.get(oid)
                    if previous and int(previous["ts"]) > ts:
                        continue
                    row.update({
                        "ts": ts, "target": name, "provider": "ps3838", "read_only": True,
                        "display_id": json.dumps(["ps3838", name, oid], separators=(",", ":")),
                        "backendName": str(account.get("username") or name),
                        "display_line": row.get("line") or "",
                        "display_reverse_applied": False,
                        "follow_avg_odds": None, "follow_avg_odds_count": 0,
                    })
                    choices = row.get("choices") or []
                    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
                    if len(choices) == 1:
                        row["marketLabel"] = {"HDP": "让球"}.get(choice.get("btName"), choice.get("btName") or "")
                        if isinstance(choice.get("inplay"), bool):
                            row["orderType"] = "滚球" if choice["inplay"] else "早盘"
                    latest[oid] = row
        except OSError:
            continue
        result.extend(row for row in latest.values() if not since or row["ts"] > since)
    result.sort(key=lambda row: row["ts"], reverse=True)
    return result[:max(1, int(limit))]


def _compose_order_play(row: Dict[str, Any]) -> str:
    line = str(row.get("line") or "").strip()
    odds = str(row.get("odds") or "").strip()
    if line and odds:
        return f"{line} @{odds}"
    if line:
        return line
    if odds:
        return f"@{odds}"
    return ""


def _compose_order_time(row: Dict[str, Any]) -> str:
    d = str(row.get("date") or "").strip()
    t = str(row.get("time") or "").strip()
    if d and t:
        return f"{d} {t}"
    return d or t


def _public_signal_from_order(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    bet_type = str(row.get("betType") or "").strip()
    oid = _extract_order_id_from_row(row)
    try:
        ts = int(row.get("ts") or 0)
    except Exception:
        ts = 0
    payload: Dict[str, Any] = {
        "ts": ts,
        "order_id": oid,
        "backend": str(row.get("target") or "").strip(),
        "sport": str(row.get("sport") or "").strip(),
        "league": str(row.get("league") or "").strip(),
        "match": str(row.get("sides") or row.get("match") or "").strip(),
        "status": "滚球" if ("滚球" in bet_type or "(滚球)" in bet_type) else "早盘",
        "bet_type": bet_type,
        "play": _compose_order_play(row),
        "amount": str(row.get("stake") or row.get("amount") or "").strip(),
        "time": _compose_order_time(row),
        "exec_ready": False,
        "exec_params": None,
        "exec_error": "",
    }
    try:
        intent = MarketIntent.from_order(row)
        if not intent.gid or intent.market_type == "OTHER" or not intent.side:
            raise ValueError("订单无法转换为 MarketIntent")
        payload["exec_ready"] = True
        payload["exec_params"] = {**intent.to_dict(), **intent_market_fields(intent)}
    except Exception as e:
        payload["exec_ready"] = False
        payload["exec_error"] = str(e)
    return payload


def read_jsonl_file(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    limit_i = max(1, int(limit))
    out: List[Dict[str, Any]] = []
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            pending = b""
            while position > 0 and len(out) < limit_i:
                chunk_size = min(64 * 1024, position)
                position -= chunk_size
                f.seek(position)
                block = f.read(chunk_size) + pending
                lines = block.split(b"\n")
                if position > 0:
                    pending = lines.pop(0)
                else:
                    pending = b""
                for raw_line in reversed(lines):
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line.decode("utf-8-sig"))
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
                        if len(out) >= limit_i:
                            break
    except Exception:
        return []
    out.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)
    return out[:limit_i]

def read_jsonl_file_filtered(
    path: Path,
    *,
    order_id: str = "",
    target: str = "",
    limit: int = 2000,
) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    order_id = str(order_id or "").strip()
    target = str(target or "").strip()
    limit_i = max(1, min(20000, int(limit or 2000)))

    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if order_id and str(obj.get("orderId") or "").strip() != order_id:
                    continue
                if target and str(obj.get("target") or "").strip() != target:
                    continue
                rows.append(obj)
    except Exception:
        return []

    # 时间线：按时间正序展示（从开始到结束）
    rows.sort(key=lambda x: int(x.get("ts_ms") or x.get("ts") or 0))
    if len(rows) > limit_i:
        rows = rows[-limit_i:]
    return rows


def _normalize_order_id(v: Any) -> str:
    return str(v or "").strip()


def _extract_order_id_from_row(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    return _normalize_order_id(row.get("orderId") or row.get("order_id") or "")


def load_deleted_order_ids(data_dir: Path) -> Set[str]:
    path = data_dir / "deleted_orders.json"
    if not path.exists():
        return set()
    try:
        raw = path.read_text(encoding="utf-8-sig")
        obj = json.loads(raw)
    except Exception:
        return set()

    items: List[Any] = []
    if isinstance(obj, dict):
        cand = obj.get("order_ids")
        if not isinstance(cand, list):
            cand = obj.get("orderIds")
        if isinstance(cand, list):
            items = cand
    elif isinstance(obj, list):
        items = obj

    out: Set[str] = set()
    for item in items:
        oid = _normalize_order_id(item)
        if oid:
            out.add(oid)
    return out


def save_deleted_order_ids(data_dir: Path, order_ids: Set[str]) -> None:
    path = data_dir / "deleted_orders.json"
    payload = {
        "order_ids": sorted({_normalize_order_id(x) for x in (order_ids or set()) if _normalize_order_id(x)}),
        "updated_at": now_iso(),
    }
    atomic_write_json(path, payload)


def queue_arsenal_manual_login(doc_path: Path, member_id: str) -> Tuple[bool, str]:
    doc = read_json_file(doc_path, {"members": []})
    members = doc.get("members") if isinstance(doc, dict) else []
    if not isinstance(members, list):
        members = []
        doc = {"members": members}

    found = False
    changed = False
    now = time.time()
    out_members: List[Any] = []
    for item in members:
        if not isinstance(item, dict):
            out_members.append(item)
            continue
        if str(item.get("id") or "").strip() != str(member_id or "").strip():
            out_members.append(item)
            continue

        found = True
        session_provider = str(item.get("session_provider") or "native").strip().lower() or "native"
        has_native_login = bool(
            str(item.get("base_url") or "").strip()
            and str(item.get("username") or "").strip()
            and str(item.get("password") or "").strip()
        )
        has_ads_login = bool(
            session_provider == "adspower"
            and (
                str(item.get("adspower_profile_id") or "").strip()
                or str(item.get("adspower_profile_no") or "").strip()
            )
        )
        if not (has_native_login or has_ads_login):
            return False, "login_credentials_missing"

        patched = dict(item)
        patched["status"] = "relogin"
        patched["last_relogin_at"] = 0
        patched["last_error"] = ""
        patched["manual_login_requested_at"] = now
        if session_provider != "adspower":
            patched["relogin_headless"] = False
        out_members.append(patched)
        changed = True

    if not found:
        return False, "member_not_found"
    if not changed:
        return False, "member_not_changed"

    doc["members"] = out_members
    atomic_write_json(doc_path, doc)
    return True, ""


def prepare_manual_signal_member_login(
    doc_path: Path,
    *,
    base_url: Any,
    username: Any,
    password: Any,
    backend_name: Any = "",
    now_value: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any], int]:
    base = normalize_member_base_url(base_url)
    user = str(username or "").strip()
    pwd = str(password or "").strip()
    backend = str(backend_name or "manual").strip() or "manual"
    if not base or not user:
        return False, "missing_base_url_or_username", {}, 0
    if not pwd:
        return False, "missing_password", {}, 0

    doc = read_json_file(doc_path, {"members": []})
    members = doc.get("members") if isinstance(doc, dict) else []
    if not isinstance(members, list):
        members = []
        doc = {"members": members}

    label = f"manual-signal-{user}"
    now = float(time.time() if now_value is None else now_value)

    def _same_manual_member(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        item_base = normalize_member_base_url(item.get("base_url"))
        item_user = str(item.get("username") or "").strip()
        item_label = str(item.get("label") or "").strip()
        return item_base == base and (item_user == user or item_label == label)

    current = next((m for m in members if _same_manual_member(m)), None)
    updated = isinstance(current, dict)
    if not updated:
        current = {
            "id": uuid.uuid4().hex,
            "label": label,
            "base_url": base,
            "username": user,
            "password": pwd,
            "proxy": "",
            "usage": "read",
            "role": "quote",
            "uid": "",
            "mid": "",
            "cookie": "",
            "ver": "",
            "status": "unknown",
            "last_check_at": 0,
            "last_ok_at": 0,
            "last_relogin_at": 0,
            "last_error": "",
            "last_systemtime": "",
            "auto_relogin": False,
            "relogin_headless": False,
            "relogin_login_tab": 3,
            "relogin_timeout_seconds": 30,
            "relogin_min_interval_seconds": 5,
            "currency": "",
            "cash": "",
            "maxcredit": "",
            "balance_updated_at": 0,
            "session_provider": "native",
            "adspower_profile_id": "",
            "adspower_profile_no": "",
            "adspower_api_base": DEFAULT_ADSPOWER_API_BASE,
            "adspower_api_key": "",
            "adspower_auto_start": True,
            "adspower_auto_form_login": True,
            "adspower_close_after_sync": True,
            "adspower_sync_proxy": True,
            "adspower_last_sync_at": 0,
            "adspower_sync_interval_seconds": 5,
            "added_at": now,
        }
        members.append(current)

    current["label"] = label
    if not str(current.get("id") or "").strip():
        current["id"] = uuid.uuid4().hex
    current["base_url"] = base
    current["username"] = user
    current["password"] = pwd
    current["usage"] = "read"
    current["role"] = "quote"
    current["status"] = "relogin"
    current["last_relogin_at"] = 0
    current["last_error"] = ""
    current["manual_login_requested_at"] = now
    current["auto_relogin"] = False
    current["relogin_headless"] = False
    current["session_provider"] = str(current.get("session_provider") or "native").strip().lower() or "native"
    current["manual_signal_backend_name"] = backend
    current["updated_at"] = now

    current_id = str(current.get("id") or "")
    disabled = 0
    for item in members:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == current_id:
            continue
        item_label = str(item.get("label") or "").strip()
        if not item_label.startswith("manual-signal-"):
            continue
        if normalize_member_base_url(item.get("base_url")) != base:
            continue
        if item.get("manual_login_requested_at") or str(item.get("status") or "").strip().lower() == "relogin" or bool(item.get("auto_relogin", True)):
            disabled += 1
        item["manual_login_requested_at"] = 0
        item["auto_relogin"] = False
        item["last_relogin_at"] = 0
        if str(item.get("status") or "").strip().lower() == "relogin":
            item["status"] = "unknown"
        item["last_error"] = "已切换到新的手动信号会员登录"
        item["updated_at"] = now

    doc["members"] = members
    atomic_write_json(doc_path, doc)
    current["updated"] = updated
    return True, "", current, disabled


def find_manual_signal_session(members: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in members or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label.startswith("manual-signal-"):
            continue
        if not str(item.get("id") or "").strip():
            continue
        if not str(item.get("base_url") or "").strip():
            continue
        if not str(item.get("username") or "").strip():
            continue
        candidates.append(item)
    if not candidates:
        return None

    def score(item: Dict[str, Any]) -> Tuple[int, float]:
        status = str(item.get("status") or "").strip().lower()
        has_session = bool(str(item.get("uid") or "").strip() and str(item.get("cookie") or "").strip() and str(item.get("ver") or "").strip())
        try:
            updated = float(item.get("updated_at") or item.get("last_ok_at") or item.get("added_at") or 0)
        except Exception:
            updated = 0.0
        status_score = 2 if status == "online" and has_session else (1 if has_session else 0)
        return status_score, updated

    return max(candidates, key=score)


def _digits_only(v: Any) -> str:
    s = str(v or "")
    return "".join(ch for ch in s if ch.isdigit())


def _load_settlements_index(root: Path) -> Dict[str, Dict[str, Any]]:
    """
    读取 data/settlements/wagers_*.jsonl，构建 ticket_id -> 最新记录 索引。
    """
    if not root.exists():
        return {}

    idx: Dict[str, Dict[str, Any]] = {}
    for p in sorted(root.glob("wagers_*.jsonl")):
        try:
            with p.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue

                    ticket_id = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                    if not ticket_id:
                        continue

                    ts = 0
                    try:
                        ts = int(float(obj.get("ts") or 0))
                    except Exception:
                        ts = 0

                    old = idx.get(ticket_id)
                    if old is None:
                        idx[ticket_id] = obj
                        continue

                    old_ts = 0
                    try:
                        old_ts = int(float(old.get("ts") or 0))
                    except Exception:
                        old_ts = 0
                    if ts >= old_ts:
                        idx[ticket_id] = obj
        except Exception:
            continue
    return idx


def _load_ok_ticket_ids_by_order(path: Path, wanted_order_ids: List[str]) -> Dict[str, List[str]]:
    """
    从 follow_results.jsonl 中提取指定 orderId 的成功注单 ticket_id 列表（去重）。
    """
    wanted = {str(x).strip() for x in wanted_order_ids if str(x).strip()}
    if not wanted:
        return {}
    if not path.exists():
        return {oid: [] for oid in wanted}

    out: Dict[str, List[str]] = {oid: [] for oid in wanted}
    seen: Dict[str, set] = {oid: set() for oid in wanted}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                order_id = str(obj.get("orderId") or "").strip()
                if not order_id or order_id not in wanted:
                    continue
                if not bool(obj.get("ok")):
                    continue

                ticket_id = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                if not ticket_id:
                    continue
                if ticket_id in seen[order_id]:
                    continue
                seen[order_id].add(ticket_id)
                out[order_id].append(ticket_id)
    except Exception:
        return {oid: [] for oid in wanted}
    return out


def _load_ok_ticket_meta_by_order(path: Path, wanted_order_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """
    从 follow_results.jsonl 提取成功注单明细（ticket/stake/odds），按 orderId 聚合并去重。
    """
    wanted = {str(x).strip() for x in wanted_order_ids if str(x).strip()}
    if not wanted:
        return {}
    if not path.exists():
        return {oid: [] for oid in wanted}

    out: Dict[str, List[Dict[str, Any]]] = {oid: [] for oid in wanted}
    seen: Dict[str, set] = {oid: set() for oid in wanted}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                order_id = str(obj.get("orderId") or "").strip()
                if not order_id or order_id not in wanted:
                    continue
                if not bool(obj.get("ok")):
                    continue

                ticket_id = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                if not ticket_id:
                    continue
                if ticket_id in seen[order_id]:
                    continue
                seen[order_id].add(ticket_id)

                odd: Optional[float] = _extract_follow_result_ioratio(obj)
                stake: Optional[float] = _to_float_or_none(obj.get("stake"))
                if stake is None:
                    stake = _to_float_or_none(obj.get("source_stake"))

                out[order_id].append(
                    {
                        "ticket_id": ticket_id,
                        "stake": stake,
                        "odds": odd,
                    }
                )
    except Exception:
        return {oid: [] for oid in wanted}
    return out


def _extract_follow_result_ioratio(row: Dict[str, Any]) -> Optional[float]:
    """
    从 follow_results 行里提取“实际跟单使用的水位”。
    仅使用跟单侧字段，不回退 backend 原始订单水位。
    """
    if not isinstance(row, dict):
        return None
    for key in ("submit_params", "params"):
        payload = row.get(key)
        if not isinstance(payload, dict):
            continue
        val = _to_float_or_none(payload.get("ioratio"))
        if val is not None:
            return val
    return _to_float_or_none(row.get("ioratio"))


def compose_follow_display_line(order: Dict[str, Any], follow_row: Optional[Dict[str, Any]]) -> Tuple[str, bool]:
    backend_line = str((order or {}).get("line") or "").strip()
    if not isinstance(follow_row, dict):
        return backend_line, False

    params = follow_row.get("submit_params")
    if not isinstance(params, dict):
        params = follow_row.get("params")
    if not isinstance(params, dict):
        return backend_line, False

    wtype = str(params.get("wtype") or "").strip().upper()
    chose_team = str(params.get("chose_team") or "").strip().upper()
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    reversed_applied = bool((follow_row.get("reverse_rule") or {}).get("applied"))

    if wtype.endswith("OU"):
        lv = _decode_total_line(params.get("con"), params.get("ratio"))
        if lv is None:
            return backend_line, reversed_applied
        prefix = "大" if chose_team in {"C", "O"} else "小"
        return f"{prefix} {_format_total_line_value(abs(float(lv)))}".strip(), reversed_applied

    if wtype.endswith("RE") or wtype.endswith("R"):
        lv = _decode_handicap_line(params.get("con"), params.get("ratio"))
        if lv is None:
            return backend_line, reversed_applied
        team_h = str(raw.get("TEAM_H") or "").strip()
        team_c = str(raw.get("TEAM_C") or "").strip()
        if chose_team == "H":
            prefix = team_h
        elif chose_team == "C":
            prefix = team_c
        else:
            prefix = ""
        if not prefix:
            return backend_line, reversed_applied
        return f"{prefix} {_format_handicap_line_value(float(lv))}".strip(), reversed_applied

    if wtype in {"M", "RM", "HM", "HRM"}:
        team_h = str(raw.get("TEAM_H") or "").strip()
        team_c = str(raw.get("TEAM_C") or "").strip()
        if chose_team == "H" and team_h:
            return team_h, reversed_applied
        if chose_team == "C" and team_c:
            return team_c, reversed_applied
        if chose_team == "N":
            return "和局", reversed_applied

    return backend_line, reversed_applied


def _load_latest_follow_rows_by_order(
    path: Path,
    wanted_order_ids: List[str],
    *,
    ok_only: Optional[bool] = None,
) -> Dict[str, Dict[str, Any]]:
    wanted = {str(x).strip() for x in wanted_order_ids if str(x).strip()}
    if not wanted or not path.exists():
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    best_ts: Dict[str, float] = {}
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if ok_only is True and not bool(obj.get("ok")):
                    continue
                if ok_only is False and bool(obj.get("ok")):
                    continue
                oid = _extract_order_id_from_row(obj)
                if not oid or oid not in wanted:
                    continue
                ts = _to_float_or_none(obj.get("ts_ms"))
                if ts is None:
                    ts = _to_float_or_none(obj.get("ts")) or 0.0
                if oid not in out or float(ts) >= float(best_ts.get(oid) or 0.0):
                    out[oid] = obj
                    best_ts[oid] = float(ts)
    except Exception:
        return {}
    return out


def _load_latest_success_follow_rows_by_order(path: Path, wanted_order_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    return _load_latest_follow_rows_by_order(path, wanted_order_ids, ok_only=True)


def _follow_queue_simplify_result(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    out: Dict[str, Any] = {
        "ok": bool(row.get("ok")),
        "message": str(row.get("message") or "").strip() or None,
        "error_type": str(row.get("error_type") or "").strip() or None,
        "exception": str(row.get("exception") or "").strip() or None,
        "response_head": str(row.get("response_head") or row.get("bet_non_xml_head") or "").strip() or None,
        "attempt": row.get("attempt"),
        "ts": row.get("ts"),
        "ts_ms": row.get("ts_ms"),
    }
    bet_head = row.get("bet_head")
    if isinstance(bet_head, dict) and bet_head:
        out["bet_head"] = bet_head
    ov_head = row.get("order_view_head")
    if isinstance(ov_head, dict) and ov_head:
        out["order_view_head"] = ov_head
    ovc_head = row.get("order_view_confirm_head")
    if isinstance(ovc_head, dict) and ovc_head:
        out["order_view_confirm_head"] = ovc_head
    return out


def _load_latest_snapshot_watch_by_market(
    path: Path,
    market_keys: Set[Tuple[str, str]],
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Return the newest Snapshot watcher event for each queued market."""
    wanted = {(str(gid or "").strip(), str(mode or "").strip().lower()) for gid, mode in market_keys if str(gid or "").strip()}
    if not wanted or not path.exists():
        return {}
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in read_jsonl_file(path, limit=2000):
        if not isinstance(row, dict):
            continue
        key = (str(row.get("gid") or "").strip(), str(row.get("mode") or "").strip().lower())
        if key not in wanted or key in out:
            continue
        out[key] = {
            "ok": bool(row.get("ok")),
            "error_code": str(row.get("error_code") or "").strip() or None,
            "failure_summary": str(row.get("failure_summary") or "").strip() or None,
            "failure_counts": row.get("failure_counts") if isinstance(row.get("failure_counts"), dict) else {},
            "failover_count": int(row.get("failover_count") or 0),
            "attempted_member_ids": row.get("attempted_member_ids") if isinstance(row.get("attempted_member_ids"), list) else [],
            "target_line_found": row.get("target_line_found"),
            "submission": str(row.get("submission") or "").strip() or None,
            "ts": row.get("ts"),
        }
    return out


def _load_ok_follow_avg_odds_by_order(path: Path, wanted_order_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    从 follow_results.jsonl 聚合每个 orderId 的成功跟单平均水位。
    返回：{order_id: {"avg": float, "count": int}}
    """
    wanted = {str(x).strip() for x in wanted_order_ids if str(x).strip()}
    if not wanted:
        return {}
    if not path.exists():
        return {}

    sums: Dict[str, float] = {oid: 0.0 for oid in wanted}
    counts: Dict[str, int] = {oid: 0 for oid in wanted}
    seen: Dict[str, set] = {oid: set() for oid in wanted}

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue

                order_id = str(obj.get("orderId") or "").strip()
                if not order_id or order_id not in wanted:
                    continue
                if not bool(obj.get("ok")):
                    continue

                odd = _extract_follow_result_ioratio(obj)
                if odd is None:
                    continue

                ticket_id = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                if ticket_id:
                    dedup_key = f"ticket:{ticket_id}"
                else:
                    dedup_key = "|".join(
                        [
                            str(obj.get("member") or "").strip(),
                            str(obj.get("ts_ms") or obj.get("ts") or "").strip(),
                            str(obj.get("split_index") or "").strip(),
                            str(obj.get("attempt") or "").strip(),
                            f"{odd:.5f}",
                        ]
                    )
                if dedup_key in seen[order_id]:
                    continue
                seen[order_id].add(dedup_key)

                sums[order_id] += float(odd)
                counts[order_id] += 1
    except Exception:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for oid in wanted:
        c = int(counts.get(oid) or 0)
        if c <= 0:
            continue
        out[oid] = {"avg": float(sums.get(oid) or 0.0) / float(c), "count": c}
    return out


def _load_follow_order_summaries(
    path: Path,
    wanted_order_ids: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """一次扫描同时生成成功水位汇总和每个订单的最新成功记录。"""
    wanted = {str(x).strip() for x in wanted_order_ids if str(x).strip()}
    if not wanted or not path.exists():
        return {}, {}

    sums: Dict[str, float] = {oid: 0.0 for oid in wanted}
    counts: Dict[str, int] = {oid: 0 for oid in wanted}
    seen: Dict[str, set] = {oid: set() for oid in wanted}
    latest: Dict[str, Dict[str, Any]] = {}
    latest_ts: Dict[str, float] = {}

    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict) or not bool(obj.get("ok")):
                    continue

                order_id = _extract_order_id_from_row(obj)
                if not order_id or order_id not in wanted:
                    continue

                ts = _to_float_or_none(obj.get("ts_ms"))
                if ts is None:
                    ts = _to_float_or_none(obj.get("ts")) or 0.0
                if order_id not in latest or float(ts) >= float(latest_ts.get(order_id) or 0.0):
                    latest[order_id] = obj
                    latest_ts[order_id] = float(ts)

                odd = _extract_follow_result_ioratio(obj)
                if odd is None:
                    continue
                ticket_id = _digits_only(obj.get("ticket_id") or obj.get("w_id") or "")
                dedup_key = f"ticket:{ticket_id}" if ticket_id else "|".join(
                    [
                        str(obj.get("member") or "").strip(),
                        str(obj.get("ts_ms") or obj.get("ts") or "").strip(),
                        str(obj.get("split_index") or "").strip(),
                        str(obj.get("attempt") or "").strip(),
                        f"{odd:.5f}",
                    ]
                )
                if dedup_key in seen[order_id]:
                    continue
                seen[order_id].add(dedup_key)
                sums[order_id] += float(odd)
                counts[order_id] += 1
    except Exception:
        return {}, {}

    averages: Dict[str, Dict[str, Any]] = {}
    for order_id in wanted:
        count = int(counts.get(order_id) or 0)
        if count > 0:
            averages[order_id] = {
                "avg": float(sums.get(order_id) or 0.0) / float(count),
                "count": count,
            }
    return averages, latest


def build_follow_trace_download_bundle(
    *,
    data_dir: Path,
    orders_dir: Path,
    order_id: str,
    target: str = "",
) -> Dict[str, Any]:
    oid = str(order_id or "").strip()
    tgt = str(target or "").strip()
    if not oid:
        return {"order": None, "trace_rows": [], "result_rows": [], "debug_files": []}

    order_obj: Optional[Dict[str, Any]] = None
    order_files: List[Path] = []
    if tgt:
        order_files.append(orders_dir / f"{safe_name(tgt)}.jsonl")
    else:
        order_files.extend(sorted(orders_dir.glob("*.jsonl")))

    for path in order_files:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if str(obj.get("orderId") or "").strip() != oid:
                        continue
                    order_obj = obj
                    break
        except Exception:
            continue
        if order_obj:
            break

    trace_rows = read_jsonl_file_filtered(data_dir / "follow_trace.jsonl", order_id=oid, target=tgt, limit=5000)
    result_rows = read_jsonl_file_filtered(data_dir / "follow_results.jsonl", order_id=oid, target=tgt, limit=5000)

    debug_files: List[Dict[str, Any]] = []
    seen_paths: set = set()
    for row in result_rows:
        if not isinstance(row, dict):
            continue
        raw_path = str(row.get("bet_response_debug_path") or "").strip()
        if not raw_path:
            continue
        if raw_path in seen_paths:
            continue
        seen_paths.add(raw_path)
        p = Path(raw_path)
        if not p.is_absolute():
            p = data_dir.parent / raw_path
        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            content = ""
        debug_files.append(
            {
                "path": str(p),
                "content": content,
            }
        )

    return {
        "order": order_obj,
        "trace_rows": trace_rows,
        "result_rows": result_rows,
        "debug_files": debug_files,
    }


def _read_all_backend_orders_by_id(orders_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    读取 orders 目录所有 jsonl，按 orderId 去重（保留 ts 最新记录）。
    """
    if not orders_dir.exists():
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for p in orders_dir.glob("*.jsonl"):
        try:
            with p.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    oid = str(obj.get("orderId") or obj.get("order_id") or "").strip()
                    if not oid:
                        continue
                    try:
                        ts = int(float(obj.get("ts") or 0))
                    except Exception:
                        ts = 0
                    old = out.get(oid)
                    if old is None or ts >= int(old.get("ts") or 0):
                        out[oid] = {"order_id": oid, "ts": ts}
        except Exception:
            continue
    return out


def _is_cancel_or_void_text(v: Any) -> bool:
    s = str(v or "").strip()
    if not s:
        return False
    keys = ("取消", "作废", "無效", "无效", "走水", "平", "和局", "退款", "退回")
    return any(k in s for k in keys)


def _resolve_net_odd_value(odd: Optional[float], profit_ratio: Optional[float]) -> Optional[float]:
    n = _to_float_or_none(odd)
    if n is None or n <= 0:
        return None
    if n <= 1.0:
        return n

    # 兼容两种赔率口径：净水位(0.82) 与十进制赔率(1.82)
    net_candidate = n - 1.0
    if net_candidate <= 0:
        return n
    if profit_ratio is None:
        return net_candidate if n > 1.2 else n

    if abs(profit_ratio - net_candidate) + 0.02 < abs(profit_ratio - n):
        return net_candidate
    return n


def _classify_ticket_outcome_and_score(
    *,
    profit: Optional[float],
    stake: Optional[float],
    odd: Optional[float],
    status: Any,
    ball_act_ret: Any,
) -> Tuple[str, float]:
    """
    输出 (outcome, unit_score)
    outcome: win/lose/half_win/half_lose/draw/pending
    unit_score: 净胜值单位（赢=+水位，输=-1，赢半=+水位/2，输半=-0.5，平=0）
    """
    st = str(status or "").strip().lower()
    bal = str(ball_act_ret or "").strip()
    if st in {"pending", "unsettled", "running", "processing"}:
        return ("pending", 0.0)
    if _is_cancel_or_void_text(bal):
        return ("draw", 0.0)

    p = _to_float_or_none(profit)
    s = _to_float_or_none(stake)
    ratio: Optional[float] = None
    if p is not None and s is not None and s > 0:
        ratio = p / s

    if p is None:
        return ("pending", 0.0)
    if abs(p) <= 1e-9:
        return ("draw", 0.0)

    if p > 0:
        net_odd = _resolve_net_odd_value(odd, ratio)
        if ratio is not None and net_odd is not None and net_odd > 0:
            d_full = abs(ratio - net_odd)
            d_half = abs(ratio - (net_odd / 2.0))
            tol = max(0.04, abs(net_odd) * 0.08)
            if d_half + tol < d_full:
                return ("half_win", net_odd / 2.0)
        if net_odd is not None and net_odd > 0:
            return ("win", net_odd)
        return ("win", max(0.0, float(ratio or 0.0)))

    # p < 0
    if ratio is not None:
        d_full_lose = abs(ratio + 1.0)
        d_half_lose = abs(ratio + 0.5)
        if d_half_lose + 0.06 < d_full_lose:
            return ("half_lose", -0.5)
    return ("lose", -1.0)


def build_follow_report_metrics(
    orders_dir: Path,
    data_dir: Path,
    deleted_order_ids: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    跟单报表顶部统计：
    - 当日账面亏盈
    - 今日跟单场次（后台订单）
    - 本周亏盈
    - 历史跟单场次（后台订单）
    - 历史胜率 + 净胜值
    """
    def _empty_period() -> Dict[str, Any]:
        return {
            "pnl": 0.0,
            "order_count": 0,
            "settled_orders": 0,
            "settled_ticket_count": 0,
            "pending_ticket_count": 0,
            "wins": 0,
            "half_wins": 0,
            "loses": 0,
            "half_loses": 0,
            "draws": 0,
            "_weighted_score_sum": 0.0,
            "_weighted_stake_sum": 0.0,
            "win_rate": None,
            "net_value": None,
        }

    deleted = {_normalize_order_id(x) for x in (deleted_order_ids or set()) if _normalize_order_id(x)}
    orders_by_id = _read_all_backend_orders_by_id(orders_dir)
    if deleted:
        orders_by_id = {oid: rec for oid, rec in orders_by_id.items() if oid not in deleted}
    order_ids = list(orders_by_id.keys())

    # 统计口径：北京时间 中午12点 ~ 次日中午12点 算“一天”
    bj_tz = timezone(timedelta(hours=8))
    now_dt = datetime.now(tz=bj_tz)
    day_start_dt = now_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    if now_dt < day_start_dt:
        day_start_dt = day_start_dt - timedelta(days=1)
    # 本周口径：以“报表日”的周一 12:00 为起点（北京时间）
    week_start_dt = day_start_dt - timedelta(days=day_start_dt.date().weekday())
    day_start_ts = int(day_start_dt.timestamp())
    week_start_ts = int(week_start_dt.timestamp())

    periods: Dict[str, Dict[str, Any]] = {
        "today": _empty_period(),
        "week": _empty_period(),
        "all": _empty_period(),
    }

    def _period_keys_by_ts(ts: int) -> List[str]:
        keys = ["all"]
        if ts >= week_start_ts:
            keys.append("week")
        if ts >= day_start_ts:
            keys.append("today")
        return keys

    if not order_ids:
        out_empty = {
            "periods": periods,
            "today_pnl": 0.0,
            "today_order_count": 0,
            "today_settled_orders": 0,
            "week_pnl": 0.0,
            "week_order_count": 0,
            "week_settled_orders": 0,
            "total_pnl": 0.0,
            "total_order_count": 0,
            "total_settled_orders": 0,
            "win_rate": None,
            "net_value": None,
            "settled_ticket_count": 0,
            "pending_ticket_count": 0,
            "wins": 0,
            "half_wins": 0,
            "loses": 0,
            "half_loses": 0,
            "draws": 0,
        }
        return out_empty

    # 分区计数：后台订单场次
    for orec in orders_by_id.values():
        try:
            ts = int(orec.get("ts") or 0)
        except Exception:
            ts = 0
        for k in _period_keys_by_ts(ts):
            periods[k]["order_count"] = int(periods[k]["order_count"] or 0) + 1

    # 账面亏盈：基于订单聚合后的已结算利润
    settlement_by_order = build_follow_settlement_status_map(data_dir, order_ids)
    for oid, orec in orders_by_id.items():
        try:
            ts = int(orec.get("ts") or 0)
        except Exception:
            ts = 0
        srec = settlement_by_order.get(oid) or {}
        try:
            pending_count = int(srec.get("pending_count") or 0)
            settled_count = int(srec.get("settled_count") or 0)
            profit_known = bool(srec.get("profit_known"))
            profit_sum = float(srec.get("profit_sum") or 0.0)
        except Exception:
            continue
        if pending_count > 0 or settled_count <= 0 or not profit_known:
            continue
        for k in _period_keys_by_ts(ts):
            periods[k]["pnl"] = float(periods[k]["pnl"] or 0.0) + profit_sum
            periods[k]["settled_orders"] = int(periods[k]["settled_orders"] or 0) + 1

    # 胜率/净胜值：基于成功跟单 ticket 的结算，按订单时间归属分区
    ticket_meta_by_order = _load_ok_ticket_meta_by_order(data_dir / "follow_results.jsonl", order_ids)
    settlements_idx = _load_settlements_index(data_dir / "settlements")

    for oid in order_ids:
        orec = orders_by_id.get(oid) or {}
        try:
            ots = int(orec.get("ts") or 0)
        except Exception:
            ots = 0
        period_keys = _period_keys_by_ts(ots)

        rows = ticket_meta_by_order.get(oid) or []
        for row in rows:
            tid = str(row.get("ticket_id") or "").strip()
            if not tid:
                continue
            srec = settlements_idx.get(tid)
            if not isinstance(srec, dict):
                for k in period_keys:
                    periods[k]["pending_ticket_count"] = int(periods[k]["pending_ticket_count"] or 0) + 1
                continue
            if _is_settlement_pending(srec):
                for k in period_keys:
                    periods[k]["pending_ticket_count"] = int(periods[k]["pending_ticket_count"] or 0) + 1
                continue

            stake = _to_float_or_none(row.get("stake"))
            if stake is None:
                stake = _to_float_or_none(srec.get("gold"))
            profit = _to_float_or_none(srec.get("profit"))
            odd = _to_float_or_none(row.get("odds"))

            outcome, unit_score = _classify_ticket_outcome_and_score(
                profit=profit,
                stake=stake,
                odd=odd,
                status=srec.get("status"),
                ball_act_ret=srec.get("ball_act_ret"),
            )
            if outcome == "pending":
                for k in period_keys:
                    periods[k]["pending_ticket_count"] = int(periods[k]["pending_ticket_count"] or 0) + 1
                continue

            for k in period_keys:
                p = periods[k]
                p["settled_ticket_count"] = int(p["settled_ticket_count"] or 0) + 1
                if outcome == "win":
                    p["wins"] = int(p["wins"] or 0) + 1
                elif outcome == "half_win":
                    p["half_wins"] = int(p["half_wins"] or 0) + 1
                elif outcome == "lose":
                    p["loses"] = int(p["loses"] or 0) + 1
                elif outcome == "half_lose":
                    p["half_loses"] = int(p["half_loses"] or 0) + 1
                else:
                    p["draws"] = int(p["draws"] or 0) + 1

                if stake is not None and stake > 0:
                    p["_weighted_score_sum"] = float(p.get("_weighted_score_sum") or 0.0) + float(stake) * float(unit_score)
                    p["_weighted_stake_sum"] = float(p.get("_weighted_stake_sum") or 0.0) + float(stake)

    # 汇总 win_rate / net_value
    for p in periods.values():
        decision_count = int(p.get("wins") or 0) + int(p.get("half_wins") or 0) + int(p.get("loses") or 0) + int(p.get("half_loses") or 0)
        if decision_count > 0:
            p["win_rate"] = (int(p.get("wins") or 0) + 0.5 * int(p.get("half_wins") or 0)) / float(decision_count)
        else:
            p["win_rate"] = None

        wss = float(p.get("_weighted_score_sum") or 0.0)
        wst = float(p.get("_weighted_stake_sum") or 0.0)
        p["net_value"] = (wss / wst) if wst > 0 else None
        p.pop("_weighted_score_sum", None)
        p.pop("_weighted_stake_sum", None)

    p_today = periods["today"]
    p_week = periods["week"]
    p_all = periods["all"]
    return {
        "periods": periods,
        "today_pnl": float(p_today.get("pnl") or 0.0),
        "today_order_count": int(p_today.get("order_count") or 0),
        "today_settled_orders": int(p_today.get("settled_orders") or 0),
        "week_pnl": float(p_week.get("pnl") or 0.0),
        "week_order_count": int(p_week.get("order_count") or 0),
        "week_settled_orders": int(p_week.get("settled_orders") or 0),
        "total_pnl": float(p_all.get("pnl") or 0.0),
        "total_order_count": int(p_all.get("order_count") or 0),
        "total_settled_orders": int(p_all.get("settled_orders") or 0),
        "win_rate": p_all.get("win_rate"),
        "net_value": p_all.get("net_value"),
        "settled_ticket_count": int(p_all.get("settled_ticket_count") or 0),
        "pending_ticket_count": int(p_all.get("pending_ticket_count") or 0),
        "wins": int(p_all.get("wins") or 0),
        "half_wins": int(p_all.get("half_wins") or 0),
        "loses": int(p_all.get("loses") or 0),
        "half_loses": int(p_all.get("half_loses") or 0),
        "draws": int(p_all.get("draws") or 0),
    }


def _to_float_or_none(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _is_settlement_pending(rec: Dict[str, Any]) -> bool:
    """
    结算状态判定：
    - 有 is_final=true（或 source=*_closed）时：优先按最终结算记录判定；
    - 旧数据/开放盘数据（无 final 标识）保持保守策略，避免把“可赢金额”误判成已赢。
    """
    source = str(rec.get("source") or "").strip().lower()
    # today_closed 在实盘里会出现“未完赛但先给可赢金额”的情况：
    # 为避免报表提前判赢/判输，统一按未结算处理，等待 history_switch 最终记录。
    if source == "today_closed":
        return True

    is_final = bool(rec.get("is_final")) or source in {"history_closed", "history_switch", "closed"}
    status = str(rec.get("status") or "").strip().lower()
    if (not status) or status in {"pending", "unsettled", "running", "processing"}:
        return True

    ball_act_ret = str(rec.get("ball_act_ret") or "").strip()
    if ball_act_ret and any(x in ball_act_ret for x in ("取消", "作废", "無效", "无效")):
        return True

    profit = _to_float_or_none(rec.get("profit"))
    if profit is None:
        return True

    if is_final:
        return False

    if ball_act_ret in {"", "确认", "待确认"} and profit >= 0:
        return True

    return False


def build_follow_settlement_status_map(
    data_dir: Path,
    order_ids: List[str],
    deleted_order_ids: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    为一批 backend orderId 聚合结算状态，输出赢/输/未结算。
    """
    order_ids_norm = []
    seen_order: set = set()
    deleted = {_normalize_order_id(x) for x in (deleted_order_ids or set()) if _normalize_order_id(x)}
    for x in order_ids:
        oid = _normalize_order_id(x)
        if not oid or oid in seen_order or (deleted and oid in deleted):
            continue
        seen_order.add(oid)
        order_ids_norm.append(oid)
    if not order_ids_norm:
        return {}

    settlements_idx = _load_settlements_index(data_dir / "settlements")
    ok_tickets_by_order = _load_ok_ticket_ids_by_order(data_dir / "follow_results.jsonl", order_ids_norm)

    out: Dict[str, Dict[str, Any]] = {}
    for oid in order_ids_norm:
        ticket_ids = ok_tickets_by_order.get(oid) or []
        total_ok_tickets = len(ticket_ids)
        settled_count = 0
        pending_count = 0
        matched_count = 0
        profit_sum = 0.0
        profit_known = True

        for tid in ticket_ids:
            srec = settlements_idx.get(str(tid))
            if not isinstance(srec, dict):
                pending_count += 1
                continue
            matched_count += 1
            if _is_settlement_pending(srec):
                pending_count += 1
                continue

            settled_count += 1
            if srec.get("profit") is None:
                profit_known = False
                continue
            try:
                profit_sum += float(srec.get("profit"))
            except Exception:
                profit_known = False

        label = "未结算"
        status_key = "pending"
        if total_ok_tickets > 0 and pending_count == 0 and settled_count > 0 and profit_known:
            if profit_sum > 1e-9:
                label = "赢"
                status_key = "win"
            elif profit_sum < -1e-9:
                label = "输"
                status_key = "lose"

        out[oid] = {
            "order_id": oid,
            "status": status_key,
            "status_label": label,
            "total_ok_tickets": int(total_ok_tickets),
            "matched_count": int(matched_count),
            "settled_count": int(settled_count),
            "pending_count": int(pending_count),
            "profit_sum": float(profit_sum),
            "profit_known": bool(profit_known and settled_count > 0),
        }
    return out


def list_targets(orders_dir: Path) -> List[str]:
    if not orders_dir.exists():
        return []
    names = []
    for p in orders_dir.glob("*.jsonl"):
        names.append(p.stem)
    return sorted(names)


def merge_order_targets_into_follow_settings(doc: Dict[str, Any], orders_dir: Path) -> Dict[str, Any]:
    out = ensure_follow_settings_defaults(doc if isinstance(doc, dict) else {})
    targets = list(out.get("targets") or [])
    seen = {str(item.get("name") or "").strip() for item in targets if isinstance(item, dict)}
    for name in list_targets(orders_dir):
        name = str(name or "").strip()
        if not name or name in seen:
            continue
        targets.append({"name": name, "enabled": True, "only_live": False, "ratio_percent": None})
        seen.add(name)
    out["targets"] = targets
    return out


# ---------------------------------------------------------------------------
# 跟单队列：读取 follow_state.json（pending_orders/jobs）
# ---------------------------------------------------------------------------


def _follow_queue_mode_from_order(order: Dict[str, Any]) -> str:
    try:
        return "live" if _is_live_backend_order(order) else "today"
    except Exception:
        return "today"


def _follow_queue_simplify_order(order: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(order, dict):
        return {}
    raw = order.get("raw") or {}
    if not isinstance(raw, dict):
        raw = {}
    gid = str(raw.get("_EVENT") or order.get("gid") or "").strip()
    gtype = str(raw.get("GTYPE") or order.get("gtype") or "").strip().upper()
    market = str(raw.get("MARKET") or "").strip().lower()

    out: Dict[str, Any] = {
        "target": str(order.get("target") or "").strip() or None,
        "orderId": str(order.get("orderId") or order.get("order_id") or "").strip() or None,
        "ts": order.get("ts"),
        "bet_ts": order.get("bet_ts"),
        "sport": str(order.get("sport") or "").strip() or None,
        "stake": str(order.get("stake") or "").strip() or None,
        "odds": str(order.get("odds") or "").strip() or None,
        "league": str(order.get("league") or "").strip() or None,
        "sides": str(order.get("sides") or "").strip() or None,
        "betType": str(order.get("betType") or "").strip() or None,
        "line": str(order.get("line") or "").strip() or None,
        "gid": gid or None,
        "gtype": gtype or None,
        "market": market or None,
    }

    # 队列只展示 MarketIntent；最终提交参数必须来自最新 Snapshot。
    try:
        intent = MarketIntent.from_order(order)
        if not intent.gid or intent.market_type == "OTHER" or not intent.side:
            raise ValueError("订单无法转换为 MarketIntent")
        out["derived"] = intent_market_fields(intent)
    except Exception as e:
        out["derived_error"] = str(e)
        out["derived"] = {"gid": gid or "", "gtype": gtype or "FT", "wtype": "", "rtype": "", "chose_team": "", "mode": _follow_queue_mode_from_order(order)}

    return out


def _follow_queue_group_jobs(jobs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Collapse independent split Tasks into one parent job for queue display."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    passthrough: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_job in (jobs or {}).items():
        if not isinstance(raw_job, dict):
            continue
        job = dict(raw_job)
        job.setdefault("key", str(raw_key))
        parent_key = str(job.get("parent_key") or "").strip()
        if bool(job.get("independent_split")) and parent_key:
            grouped.setdefault(parent_key, []).append(job)
        else:
            passthrough[str(raw_key)] = job

    for parent_key, tasks in grouped.items():
        tasks.sort(key=lambda item: int(item.get("split_index") or 0))
        parent = dict(tasks[0])
        parent["key"] = parent_key
        parent["parent_key"] = parent_key
        parent["active_task_count"] = len(tasks)
        parent["split_tasks"] = [dict(task) for task in tasks]
        parent["attempts"] = sum(int(task.get("attempts") or 0) for task in tasks)
        next_values = [float(task.get("next_try_at") or 0.0) for task in tasks if float(task.get("next_try_at") or 0.0) > 0.0]
        parent["next_try_at"] = min(next_values) if next_values else 0.0
        created_values = [float(task.get("created_at") or 0.0) for task in tasks if float(task.get("created_at") or 0.0) > 0.0]
        parent["created_at"] = min(created_values) if created_values else parent.get("created_at")
        split_count = max(1, int(parent.get("split_count") or len(tasks) or 1))
        parent["split_index"] = max(0, split_count - len(tasks))
        members = [str(task.get("member_id") or "").strip() for task in tasks]
        parent["member_ids"] = list(dict.fromkeys(member for member in members if member))
        messages = [str(task.get("last_message") or "").strip() for task in tasks]
        parent["last_message"] = next((message for message in messages if message), "")
        passthrough[parent_key] = parent
    return passthrough


def _follow_queue_simplify_job(job: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(job, dict):
        return {}
    out: Dict[str, Any] = {
        "key": str(job.get("key") or "").strip() or None,
        "order_id": str(job.get("order_id") or job.get("orderId") or "").strip() or None,
        "target": str(job.get("target") or "").strip() or None,
        "stake": job.get("stake"),
        "created_at": job.get("created_at"),
        "last_attempted_at": job.get("last_attempted_at"),
        "member_id": str(job.get("member_id") or "").strip() or None,
        "attempts": job.get("attempts"),
        "next_try_at": job.get("next_try_at"),
        "last_message": str(job.get("last_message") or "").strip() or None,
        "last_err": str(job.get("last_err") or "").strip() or None,
        "split_count": job.get("split_count"),
        "split_index": job.get("split_index"),
        "split_delay_seconds": job.get("split_delay_seconds"),
        "split_stakes": job.get("split_stakes") if isinstance(job.get("split_stakes"), list) else [],
        "split_tickets": job.get("split_tickets") if isinstance(job.get("split_tickets"), list) else [],
        "used_member_ids": job.get("used_member_ids") if isinstance(job.get("used_member_ids"), list) else [],
        "active_task_count": job.get("active_task_count"),
        "member_ids": job.get("member_ids") if isinstance(job.get("member_ids"), list) else [],
        "split_tasks": job.get("split_tasks") if isinstance(job.get("split_tasks"), list) else [],
    }
    return out


def _follow_queue_terminal_item(
    key: str,
    done: Dict[str, Any],
    order: Dict[str, Any],
) -> Dict[str, Any]:
    """将 follow_state.done 记录转换为可持续展示的终态队列项。"""
    meta = done if isinstance(done, dict) else {}
    status0 = str(meta.get("status") or "").strip().lower()
    status = "done" if status0 in {"ok", "success", "succeeded", "done"} else "failed"
    reason = str(meta.get("reason") or meta.get("message") or "").strip()
    job: Dict[str, Any] = {
        "terminal": True,
        "ticket_id": str(meta.get("ticket_id") or "").strip() or None,
        "tickets": meta.get("tickets") if isinstance(meta.get("tickets"), list) else [],
        "reason": reason or None,
        "last_message": reason or None,
        "ts": meta.get("ts"),
        "split_done": meta.get("split_done"),
        "split_total": meta.get("split_total"),
    }
    return {
        "key": str(key or ""),
        "status": status,
        "terminal": True,
        "job": job,
        "order": order if isinstance(order, dict) else {},
    }


def _follow_queue_done_order_map(orders_dir: Path, keys: List[str]) -> Dict[str, Dict[str, Any]]:
    """按 done key 找回原始订单；优先读取 target 对应文件，兼容旧数据再扫描全部文件。"""
    wanted: Dict[str, Tuple[str, str]] = {}
    for raw_key in keys:
        key = str(raw_key or "").strip()
        if "|" not in key:
            continue
        target, order_id = key.split("|", 1)
        target = target.strip()
        order_id = order_id.strip()
        if target and order_id:
            wanted[key] = (target, order_id)
    if not wanted or not orders_dir.exists():
        return {}

    paths: List[Path] = []
    seen_paths: Set[Path] = set()
    for target, _ in wanted.values():
        path = orders_dir / f"{safe_name(target)}.jsonl"
        if path not in seen_paths:
            paths.append(path)
            seen_paths.add(path)
    # 旧版本可能把 target 写入值与文件名不一致，必要时兼容全量扫描。
    paths.extend(p for p in sorted(orders_dir.glob("*.jsonl")) if p not in seen_paths)

    out: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        for row in read_orders_file(path, limit=100000):
            if not isinstance(row, dict):
                continue
            target = str(row.get("target") or path.stem or "").strip()
            order_id = _extract_order_id_from_row(row)
            key = f"{target}|{order_id}" if target and order_id else ""
            if key not in wanted:
                continue
            old = out.get(key)
            try:
                row_ts = int(float(row.get("ts") or 0))
                old_ts = int(float((old or {}).get("ts") or 0))
            except Exception:
                row_ts, old_ts = 0, 0
            if old is None or row_ts >= old_ts:
                out[key] = row
        if len(out) >= len(wanted):
            break
    return out


def _clear_follow_queue_state(data_dir: Path, *, req_at: Optional[float] = None) -> Dict[str, Any]:
    state_path = data_dir / "follow_state.json"
    state = read_json_file(state_path, {})
    if not isinstance(state, dict):
        state = {}

    pending_raw = state.get("pending_orders")
    jobs_raw = state.get("jobs")
    done_raw = state.get("done")
    pending: Dict[str, Any] = pending_raw if isinstance(pending_raw, dict) else {}
    jobs: Dict[str, Any] = jobs_raw if isinstance(jobs_raw, dict) else {}
    done: Dict[str, Any] = dict(done_raw) if isinstance(done_raw, dict) else {}

    clear_ts = float(time.time() if req_at is None else req_at)
    cleared_at = now_iso()

    for key, order in pending.items():
        skey = str(key)
        if skey in done:
            continue
        rec: Dict[str, Any] = {
            "status": "cleared",
            "reason": "manual_queue_clear",
            "ts": int(clear_ts),
            "cleared_at": cleared_at,
        }
        if isinstance(order, dict):
            rec["order"] = order
        done[skey] = rec

    for key, job in jobs.items():
        skey = str(key)
        if skey in done:
            continue
        rec = {
            "status": "cleared",
            "reason": "manual_queue_clear",
            "ts": int(clear_ts),
            "cleared_at": cleared_at,
        }
        if isinstance(job, dict):
            rec["job"] = job
            order = job.get("order")
            if isinstance(order, dict):
                rec["order"] = order
        done[skey] = rec

    state["pending_orders"] = {}
    state["jobs"] = {}
    state["done"] = done
    state["updated_at"] = cleared_at
    atomic_write_json(state_path, state)

    pending_before = len(pending)
    jobs_before = len(jobs)
    return {
        "pending_orders_before": int(pending_before),
        "jobs_before": int(jobs_before),
        "cleared_total": int(pending_before + jobs_before),
        "updated_at": cleared_at,
    }


# ---------------------------------------------------------------------------
# 查水（cc/WUI 融合）：只读盘口/水位接口（不下注）
# ---------------------------------------------------------------------------

_GISMO_FEED_BASE_URL = "https://ws-fn-cdn001.akamaized.net/188bet/zh/Etc:UTC/gismo"
_GISMO_BETRADAR_BASE_URL = "https://betradar.hgapp0003.com"
_GISMO_WIDGET_KEY_ID = "2370877a0ce04fbfdc9678dae41d4210"
_GISMO_CAPTURE_LOCK = threading.Lock()
_GISMO_HIDDEN_EVENT_TYPES = {
    "players_on_pitch",
    "match_about_to_start",
    "match_started",
    "possible_event",
    "matchsituation",
    "possession",
    "ballcoordinates",
}


def _mask_uid(uid: str) -> str:
    u = str(uid or "").strip()
    if not u:
        return ""
    if len(u) <= 8:
        return "***"
    return f"{u[:4]}…{u[-4:]}"


def _extract_between(text: str, start_tag: str, end_tag: str) -> str:
    s = str(text or "")
    a = s.find(start_tag)
    if a < 0:
        return ""
    b = s.find(end_tag, a + len(start_tag))
    if b < 0:
        return ""
    return s[a + len(start_tag) : b]


def _strip_cdata(s: str) -> str:
    t = str(s or "").strip()
    if t.startswith("<![CDATA[") and t.endswith("]]>"):
        return t[len("<![CDATA[") : -len("]]>")].strip()
    return t


def _chashui_normalize_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in {"live", "rb", "running"}:
        return "live"
    if m in {"today"}:
        return "today"
    if m in {"early"}:
        return "early"
    return "live"


def _chashui_mode_ctx(mode: str) -> Tuple[str, str, str]:
    """
    返回: (showtype, rtype, isRB)
    """
    m = _chashui_normalize_mode(mode)
    if m == "live":
        return "live", "rb", "Y"
    if m == "early":
        return "early", "r", "N"
    return "today", "r", "N"


def _gismo_clean(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _gismo_score_from_result(result: Any, fallback: str = "0 : 0") -> str:
    if isinstance(result, dict):
        home = _gismo_clean(result.get("home"))
        away = _gismo_clean(result.get("away"))
        if home != "" and away != "":
            return f"{home} : {away}"
    return fallback


def _gismo_minute_label(minute: int, injury_time: int, fallback: str = "") -> str:
    if minute < 0:
        return _gismo_clean(fallback) or "-"
    if injury_time > 0:
        return f"{minute}+{injury_time}"
    return str(minute)


def _gismo_event_sort_key(event: Dict[str, Any]) -> Tuple[int, int, int]:
    try:
        minute = int(float(event.get("minute") or -1))
    except Exception:
        minute = -1
    try:
        injury = int(float(event.get("injuryTime") or 0))
    except Exception:
        injury = 0
    try:
        seconds = int(float(event.get("seconds") or 0))
    except Exception:
        seconds = 0
    return (minute, injury, seconds)


def _gismo_parse_event(raw: Dict[str, Any], phrase_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    event_type = _gismo_clean(raw.get("type"))
    try:
        minute = int(float(raw.get("time") if raw.get("time") is not None else -1))
    except Exception:
        minute = -1
    try:
        injury_time = int(float(raw.get("injurytime") or 0))
    except Exception:
        injury_time = 0
    try:
        seconds = int(float(raw.get("seconds") or 0))
    except Exception:
        seconds = 0

    event_id = _gismo_clean(raw.get("_id")) or f"{event_type}:{minute}:{injury_time}:{seconds}:{_gismo_clean(raw.get('team'))}"
    result = raw.get("result") if isinstance(raw.get("result"), dict) else None
    has_score = event_type in {"goal", "owngoal"} and isinstance(result, dict)
    description = ""
    if phrase_map:
        description = _gismo_clean(phrase_map.get(event_id))

    out: Dict[str, Any] = {
        "id": event_id,
        "type": event_type,
        "team": _gismo_clean(raw.get("team")),
        "label": _gismo_clean(raw.get("name")) or event_type or "事件",
        "minute": minute,
        "injuryTime": injury_time,
        "minuteLabel": _gismo_minute_label(minute, injury_time, _gismo_clean(raw.get("name"))),
        "seconds": seconds,
    }
    card = _gismo_clean(raw.get("card"))
    if card:
        out["card"] = card
    if description:
        out["description"] = description
    if has_score:
        out["score"] = _gismo_score_from_result(result, "")
    return out


def _gismo_parse_phrase_map(raw_text: str) -> Dict[str, str]:
    try:
        payload = json.loads(str(raw_text or ""))
    except Exception:
        return {}
    docs = payload.get("doc") if isinstance(payload, dict) else None
    first = docs[0] if isinstance(docs, list) and docs else {}
    data = first.get("data") if isinstance(first, dict) and isinstance(first.get("data"), dict) else {}
    phrases = data.get("phrases") if isinstance(data, dict) else None
    out: Dict[str, str] = {}
    for phrase in phrases if isinstance(phrases, list) else []:
        if not isinstance(phrase, dict):
            continue
        event = phrase.get("event") if isinstance(phrase.get("event"), dict) else {}
        event_id = _gismo_clean(event.get("_id"))
        sentence = _gismo_clean(phrase.get("sentence"))
        if event_id and sentence:
            out[event_id] = sentence
    return out


def _gismo_parse_match_timeline(raw_text: str, *, match_id: str, phrase_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    payload = json.loads(str(raw_text or ""))
    docs = payload.get("doc") if isinstance(payload, dict) else None
    first = docs[0] if isinstance(docs, list) and docs else {}
    data = first.get("data") if isinstance(first, dict) and isinstance(first.get("data"), dict) else {}
    if (
        isinstance(first, dict)
        and (_gismo_clean(first.get("event")).lower() == "exception" or _gismo_clean(data.get("_doc")).lower() == "exception")
    ):
        code = data.get("code") if isinstance(data, dict) else ""
        message = _gismo_clean(data.get("message") or data.get("name") or "事件源返回异常")
        if str(code) == "403" or "unauthorized" in message.lower():
            raise RuntimeError("事件源授权失败，请更新有效的 BETRADAR_FEED_TOKEN。")
        raise RuntimeError(f"事件源错误 {code}：{message}")

    match = data.get("match") if isinstance(data.get("match"), dict) else {}
    result = match.get("result") if isinstance(match.get("result"), dict) else {}
    status = match.get("status") if isinstance(match.get("status"), dict) else {}
    teams = match.get("teams") if isinstance(match.get("teams"), dict) else {}
    home = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    raw_events = data.get("events") if isinstance(data.get("events"), list) else []
    events = [
        _gismo_parse_event(event, phrase_map)
        for event in raw_events
        if isinstance(event, dict) and _gismo_clean(event.get("type")) not in _GISMO_HIDDEN_EVENT_TYPES
    ]
    events.sort(key=_gismo_event_sort_key, reverse=True)
    return {
        "matchId": _gismo_clean(match.get("_id")) or _gismo_clean(match_id),
        "score": _gismo_score_from_result(result),
        "statusText": _gismo_clean(status.get("name")),
        "homeTeam": _gismo_clean(home.get("name")),
        "awayTeam": _gismo_clean(away.get("name")),
        "events": events,
    }


def _gismo_is_feed_token(value: str) -> bool:
    token = _gismo_clean(value)
    if not token:
        return False
    return bool(re.search(r"(?:^|~)exp=\d+", token) and re.search(r"(?:^|~)hmac=[0-9a-fA-F]{8,}", token))


def _gismo_extract_feed_token_from_text(text: Any) -> str:
    raw = html.unescape(str(text or ""))
    if not raw:
        return ""

    candidates: List[str] = []
    stripped = raw.strip().strip("'\"")
    if _gismo_is_feed_token(stripped):
        candidates.append(stripped)

    for match in re.finditer(r"(?:[?&])T=([^&#\"'<>\s]+)", raw):
        candidates.append(match.group(1))

    for value in candidates:
        token = _gismo_clean(unquote(str(value or ""))).strip("'\"")
        if _gismo_is_feed_token(token):
            return token
    return ""


def _gismo_write_feed_token(data_dir: Path, token: str) -> Path:
    cleaned = _gismo_extract_feed_token_from_text(token) or _gismo_clean(token)
    if not _gismo_is_feed_token(cleaned):
        raise ValueError("invalid_betradar_feed_token")
    path = data_dir / "betradar-feed-token.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned, encoding="utf-8")
    return path


def _gismo_read_feed_token(data_dir: Path) -> str:
    env_token = _gismo_clean(os.environ.get("BETRADAR_FEED_TOKEN"))
    if env_token:
        return env_token
    candidates = [
        data_dir / "betradar-feed-token.txt",
        APP_ROOT / ".data" / "betradar-feed-token.txt",
        Path.cwd() / ".data" / "betradar-feed-token.txt",
    ]
    seen: Set[str] = set()
    for path in candidates:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.exists():
                token = _gismo_clean(path.read_text(encoding="utf-8-sig"))
                if token:
                    return token
        except Exception:
            continue
    return ""


def _chashui_event_match_map_path(data_dir: Path) -> Path:
    return data_dir / "chashui-event-match-map.json"


def _chashui_lookup_event_match_id(data_dir: Path, *, gid: str) -> str:
    gid2 = _gismo_clean(gid)
    if not gid2:
        return ""
    doc = read_json_file(_chashui_event_match_map_path(data_dir), {})
    entry = doc.get(gid2) if isinstance(doc, dict) else None
    if isinstance(entry, dict):
        return _gismo_clean(entry.get("match_id") or entry.get("matchId") or entry.get("mtId"))
    if isinstance(entry, str):
        return _gismo_clean(entry)
    return ""


def _chashui_remember_event_match_id(data_dir: Path, *, gid: str, match_id: str, mode: str = "live") -> bool:
    gid2 = _gismo_clean(gid)
    match_id2 = _gismo_clean(match_id)
    if not gid2 or not match_id2:
        return False
    path = _chashui_event_match_map_path(data_dir)
    doc = read_json_file(path, {})
    if not isinstance(doc, dict):
        doc = {}
    doc[gid2] = {
        "gid": gid2,
        "match_id": match_id2,
        "mode": _chashui_normalize_mode(mode),
        "updated_at": int(time.time()),
    }
    atomic_write_json(path, doc)
    return True


def _gismo_feed_url(match_id: str, endpoint: str, token: str) -> str:
    base_url = _gismo_clean(os.environ.get("BETRADAR_FEED_BASE_URL")) or _GISMO_FEED_BASE_URL
    url = f"{base_url.rstrip('/')}/{endpoint}/{quote(_gismo_clean(match_id), safe='')}"
    if _gismo_clean(token):
        url += f"?T={_gismo_clean(token)}"
    return url


def _gismo_request_feed(match_id: str, *, mode: str, token: str, timeout_s: int) -> Dict[str, Any]:
    endpoint = "match_timelinedelta" if str(mode or "").strip().lower() == "delta" else "match_timeline"
    headers = {
        "accept": "*/*",
        "origin": "https://betradar.hgapp0003.com",
        "referer": "https://betradar.hgapp0003.com/",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    }
    url = _gismo_feed_url(match_id, endpoint, token)
    response = requests.get(url, headers=headers, timeout=max(3, int(timeout_s)), verify=False)
    response.raise_for_status()

    phrase_map: Dict[str, str] = {}
    if endpoint == "match_timeline":
        try:
            phrase_response = requests.get(
                _gismo_feed_url(match_id, "match_phrases", token),
                headers=headers,
                timeout=max(3, min(8, int(timeout_s))),
                verify=False,
            )
            if phrase_response.ok:
                phrase_map = _gismo_parse_phrase_map(phrase_response.text)
        except Exception:
            phrase_map = {}

    return _gismo_parse_match_timeline(response.text, match_id=match_id, phrase_map=phrase_map)


def _gismo_betradar_transform_url(member: Dict[str, Any]) -> str:
    params = {
        "p": "getDataMT",
        "uid": _gismo_clean(member.get("uid")),
        "langx": "zh-cn",
    }
    username = _gismo_clean(member.get("username"))
    version = _gismo_clean(member.get("ver") or member.get("version"))
    if username:
        params["username"] = username
    if version:
        params["version"] = version
    return f"{_GISMO_BETRADAR_BASE_URL}/transform.php?{urlencode(params)}"


def _gismo_launch_chromium_with_fallback(chromium: Any, *, headless: bool = True) -> Any:
    args = ["--disable-dev-shm-usage", "--no-sandbox"]
    if not headless:
        args.extend(
            [
                "--window-position=-32000,-32000",
                "--window-size=1200,900",
                "--disable-backgrounding-occluded-windows",
            ]
        )
    base_kwargs = {
        "headless": bool(headless),
        "args": args,
    }
    errors: List[str] = []

    try:
        return chromium.launch(**base_kwargs)
    except Exception as e:
        errors.append(str(e))

    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env_path:
        try:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            return chromium.launch(**base_kwargs)
        except Exception as e:
            errors.append(str(e))
        finally:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env_path

    try:
        return chromium.launch(**base_kwargs, channel="chrome")
    except Exception as e:
        errors.append(str(e))

    raise RuntimeError("playwright_browser_launch_failed: " + " | ".join(errors[-3:]))


def _gismo_build_match_widget_post_message(match_id: str, *, base_url: str = "", timestamp_ms: Optional[int] = None) -> str:
    ts = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    payload = {
        "Mode": "Match.lmtPlus",
        "KeyID": _gismo_clean(os.environ.get("BETRADAR_WIDGET_KEY_ID")) or _GISMO_WIDGET_KEY_ID,
        "GameID": _gismo_clean(match_id),
        "Gtype": "FT",
        "SpID": "1",
        "TimeZone": "Etc:UTC",
        "Cross": _gismo_clean(base_url) or _GISMO_BETRADAR_BASE_URL,
        "Status": "open",
        "NeedsHeader": "Y",
        "Lang": "zh",
        "Lmt3": False,
    }
    return "456|" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + f"|{ts}|false"


def _gismo_capture_feed_token_with_browser(
    target_url: str,
    *,
    match_id: str = "",
    base_url: str = "",
    timeout_s: int = 12,
    headless: bool = False,
) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return ""

    token_box = {"token": ""}

    def remember(value: Any) -> None:
        if token_box["token"]:
            return
        token = _gismo_extract_feed_token_from_text(value)
        if token:
            token_box["token"] = token

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = _gismo_launch_chromium_with_fallback(p.chromium, headless=headless)
            context = browser.new_context(
                ignore_https_errors=True,
                locale="zh-CN",
                timezone_id="Etc/UTC",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            page.on("request", lambda req: remember(req.url))
            page.on("response", lambda res: remember(res.url))
            page.goto(target_url, wait_until="domcontentloaded", timeout=max(3, int(timeout_s)) * 1000)
            remember(page.url)
            try:
                remember(page.content())
            except Exception:
                pass
            if _gismo_clean(match_id):
                try:
                    page.wait_for_timeout(1000)
                    message = _gismo_build_match_widget_post_message(match_id, base_url=base_url)
                    page.evaluate('(message) => window.postMessage(message, "*")', message)
                except Exception:
                    pass

            deadline = time.time() + max(1, int(timeout_s))
            while not token_box["token"] and time.time() < deadline:
                try:
                    page.wait_for_timeout(250)
                except Exception:
                    break
                remember(page.url)
                try:
                    remember(page.content())
                except Exception:
                    pass
    except Exception:
        return ""
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass

    return token_box["token"]


def _chashui_capture_gismo_token(
    member: Dict[str, Any],
    data_dir: Path,
    *,
    match_id: str = "",
    timeout_s: int = 12,
    request_get: Optional[Callable[..., Any]] = None,
    browser_capture: Optional[Callable[..., str]] = None,
) -> Dict[str, Any]:
    uid = _gismo_clean(member.get("uid"))
    if not uid:
        return {"ok": False, "message": "当前会员会话缺少 UID，无法自动采集比赛事件授权。"}

    target_url = _gismo_betradar_transform_url(member)
    get = request_get or requests.get
    direct_error = ""
    with _GISMO_CAPTURE_LOCK:
        try:
            response = get(
                target_url,
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "referer": _GISMO_BETRADAR_BASE_URL + "/",
                    "user-agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
                    ),
                },
                timeout=max(3, min(15, int(timeout_s))),
                verify=False,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            token = _gismo_extract_feed_token_from_text(getattr(response, "url", "")) or _gismo_extract_feed_token_from_text(getattr(response, "text", ""))
            if token:
                _gismo_write_feed_token(data_dir, token)
                return {"ok": True, "source": "http", "message": "已自动采集比赛事件授权。"}
        except Exception as e:
            direct_error = str(e)

        capture = browser_capture or _gismo_capture_feed_token_with_browser
        try:
            token = _gismo_extract_feed_token_from_text(
                capture(
                    target_url,
                    match_id=_gismo_clean(match_id),
                    base_url=_gismo_clean(member.get("base_url")),
                    timeout_s=timeout_s,
                )
            )
            if token:
                _gismo_write_feed_token(data_dir, token)
                return {"ok": True, "source": "browser", "message": "已自动采集比赛事件授权。"}
        except Exception as e:
            direct_error = direct_error or str(e)

    message = "自动采集事件源授权失败，请打开一场带动画/跟球的比赛后重试。"
    if direct_error:
        return {"ok": False, "message": message, "detail": direct_error}
    return {"ok": False, "message": message}


def _chashui_find_capture_member(arsenal_path: Path, member_id: str = "") -> Optional[Dict[str, Any]]:
    doc = read_json_file(arsenal_path, {"members": []})
    members_raw = doc.get("members") if isinstance(doc, dict) else []
    members = [m for m in (members_raw or []) if isinstance(m, dict)]
    return select_readonly_member_from_members(members, member_id)


def _chashui_parse_game_list_original(xml_text: str) -> List[Dict[str, Any]]:
    t = str(xml_text or "")
    json_str = _extract_between(t, "<original>", "</original>")
    if not json_str:
        return []
    json_str = _strip_cdata(json_str)
    try:
        parsed = json.loads(json_str)
    except Exception:
        return []
    if not isinstance(parsed, dict):
        return []
    out: List[Dict[str, Any]] = []
    for v in parsed.values():
        if isinstance(v, dict) and v:
            out.append(v)
    return out


def _chashui_parse_game_list_xml_games(xml_text: str) -> List[Dict[str, Any]]:
    """
    兼容：少数站点 get_game_list 不带 <original>{JSON}</original>，只返回 XML。
    这里做一个“只取最关键字段”的兜底解析，供查水页面展示/定位 gid 用。
    """
    t = str(xml_text or "").strip()
    if not t or (not t.startswith("<")):
        return []
    try:
        root = ET.fromstring(t)
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    seen = set()

    def _ft(g: ET.Element, tag: str) -> str:
        k = str(tag or "").strip()
        if not k:
            return ""
        v = g.findtext(k) or g.findtext(k.lower()) or ""
        return str(v or "").strip()

    def _add_game(g: ET.Element, ecid_num: str = "") -> None:
        gid = _ft(g, "GID") or _ft(g, "gid")
        gid = str(gid or "").strip()
        if not gid or gid in seen:
            return
        seen.add(gid)
        row = {
            "GID": gid,
            "HGID": _ft(g, "HGID") or _ft(g, "hgid") or _ft(g, "HALF_GID") or _ft(g, "halfGid"),
            "LEAGUE": _ft(g, "LEAGUE"),
            "TEAM_H": _ft(g, "TEAM_H"),
            "TEAM_C": _ft(g, "TEAM_C"),
            "GNUM_H": _ft(g, "GNUM_H"),
            "GNUM_C": _ft(g, "GNUM_C"),
            "SCORE_H": _ft(g, "SCORE_H"),
            "SCORE_C": _ft(g, "SCORE_C"),
            "RETIMESET": _ft(g, "RETIMESET"),
            "DATETIME": _ft(g, "DATETIME"),
            "GAME_DATE_TIME": _ft(g, "GAME_DATE_TIME"),
            "MORE": _ft(g, "MORE"),
            "ECID": _ft(g, "ECID") or str(ecid_num or "").strip(),
            "LID": _ft(g, "LID"),
            "MT_ID": _ft(g, "MT_ID"),
        }
        out.append(row)

    # 优先：ec 包裹结构（更容易拿到 ecid）
    for ec in root.iter("ec"):
        ecid_attr = ""
        try:
            ecid_attr = str(ec.get("id") or ec.get("ID") or "").strip()
        except Exception:
            ecid_attr = ""
        ecid_num = ""
        if ecid_attr:
            m = re.search(r"(\d+)$", ecid_attr)
            if m:
                ecid_num = m.group(1)
        for g in ec.iter("game"):
            _add_game(g, ecid_num=ecid_num)

    # 兜底：无 ec 包裹
    for g in root.iter("game"):
        _add_game(g, ecid_num="")

    return out


def _chashui_simplify_games(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for g in games or []:
        if not isinstance(g, dict):
            continue
        score = None
        sh = str(g.get("SCORE_H") or "").strip()
        sc = str(g.get("SCORE_C") or "").strip()
        if sh != "" and sc != "":
            score = f"{sh}:{sc}"
        gid = str(g.get("GID") or "").strip()
        if not gid:
            continue
        more = g.get("MORE")
        try:
            more_count = int(more) if more is not None and str(more).strip() != "" else None
        except Exception:
            more_count = None
        out.append(
            {
                "gid": gid,
                "league": str(g.get("LEAGUE") or "").strip() or None,
                "teamH": str(g.get("TEAM_H") or "").strip() or None,
                "teamC": str(g.get("TEAM_C") or "").strip() or None,
                "gnumH": str(g.get("GNUM_H") or "").strip() or None,
                "gnumC": str(g.get("GNUM_C") or "").strip() or None,
                "score": score,
                "time": str(g.get("RETIMESET") or g.get("DATETIME") or g.get("GAME_DATE_TIME") or "").strip() or None,
                "moreCount": more_count,
                # 额外字段（不一定展示，但 resolve/调试可用）
                "ecid": str(g.get("ECID") or "").strip() or None,
                "lid": str(g.get("LID") or "").strip() or None,
                "mtId": str(g.get("MT_ID") or "").strip() or None,
            }
        )
    return out


def _telegram_signal_load_games(arsenal_path: Path, *, mode: str) -> Dict[str, Any]:
    """Resolve fixture identities for TG intake without exposing credentials or odds."""
    mode2 = _chashui_normalize_mode(mode)
    doc = read_json_file(Path(arsenal_path), {"members": []})
    members_raw = doc.get("members") if isinstance(doc, dict) else []
    members = [member for member in (members_raw or []) if isinstance(member, dict)]

    def load_for_member(member: Dict[str, Any]) -> Dict[str, Any]:
        member_id = str(member.get("id") or "").strip()
        base_url = str(member.get("base_url") or "").strip()
        uid = str(member.get("uid") or "").strip()
        ver = str(member.get("ver") or "").strip()
        cookie = str(member.get("cookie") or "").strip()
        proxy = str(member.get("proxy") or "").strip()
        if not (member_id and base_url and uid and ver and cookie):
            raise RuntimeError("member_missing_uid_cookie_ver")

        cached = _get_chashui_games_cache(member_id, mode2)
        if cached:
            return {"mode": mode2, "games": cached["games"], "cached": True}

        session = _make_member_session(base_url=base_url, proxy=proxy, cookie=cookie)
        xml_text = _chashui_get_game_list_xml(
            session=session,
            base_url=base_url,
            ver=ver,
            uid=uid,
            mode=mode2,
            timeout_s=10,
        )
        raw_text = str(xml_text or "").strip()
        if not raw_text:
            raise RuntimeError("game_list_empty_response")
        if not raw_text.startswith("<"):
            raise RuntimeError(f"game_list_non_xml: {raw_text[:160]}")
        games_raw = _chashui_parse_game_list_original(raw_text) or _chashui_parse_game_list_xml_games(raw_text)
        games = _chashui_simplify_games(games_raw)
        _set_chashui_games_cache(member_id, mode2, games)
        return {"mode": mode2, "games": games, "cached": False}

    run = _run_readonly_member_with_failover(
        arsenal_path=Path(arsenal_path),
        members=members,
        lane="telegram_signal_games",
        runner=load_for_member,
    )
    if run.get("ok"):
        payload = dict(run.get("result") or {})
        payload.update(
            {
                "ok": True,
                "readonly_member": _readonly_member_ref(run.get("member")),
                "fallback_used": bool(run.get("fallback_used")),
            }
        )
        return payload

    for attempted_id in run.get("attempted_member_ids") or []:
        stale = _get_chashui_games_cache(
            attempted_id,
            mode2,
            allow_stale_s=CHASHUI_GAMES_CACHE_STALE_TTL_S,
        )
        if stale:
            return {
                "ok": True,
                "mode": mode2,
                "games": stale["games"],
                "cached": True,
                "stale": True,
                "warning": str(run.get("error") or "game_list_failed"),
                "fallback_used": bool(run.get("fallback_used")),
            }
    return {
        "ok": False,
        "mode": mode2,
        "games": [],
        "message": str(run.get("error") or "readonly_pool_empty"),
        "fallback_used": bool(run.get("fallback_used")),
    }


def _xml_tag_value(xml_text: str, tag: str) -> str:
    t = str(xml_text or "")
    k = str(tag or "").strip()
    if not k:
        return ""
    m = re.search(rf"<{re.escape(k)}>([\s\S]*?)</{re.escape(k)}>", t)
    return (m.group(1) if m else "").strip()


def _chashui_parse_ft_gid_list(xml_text: str) -> List[Dict[str, str]]:
    seg = _extract_between(str(xml_text or ""), "<FT_gid>", "</FT_gid>")
    if not seg:
        return []
    out: List[Dict[str, str]] = []
    for m in re.finditer(r'<gid\s+id="(\d+)"\s+master="([YN])"\s*/\s*>', seg):
        out.append({"gid": m.group(1), "master": m.group(2)})
    return out


def _chashui_parse_game_more_xml(xml_text: str) -> Dict[str, Any]:
    t = str(xml_text or "").strip()
    if not t.startswith("<"):
        return {"ok": False, "error": "non_xml"}

    code = _xml_tag_value(t, "code") or None
    ecid = _xml_tag_value(t, "ecid") or None
    systime = _xml_tag_value(t, "systime") or None
    ft_gids = _chashui_parse_ft_gid_list(t)

    game_seg_re = re.compile(r"<game\b[^>]*>[\s\S]*?</game>")
    attr_re = re.compile(r'([A-Za-z0-9_:-]+)="([^"]*)"')
    leaf_re = re.compile(r"<([A-Za-z0-9_]+)>([^<]*)</\1>")

    boards: List[Dict[str, Any]] = []
    for m in game_seg_re.finditer(t):
        game_xml = m.group(0)
        head = re.search(r"<game\b([^>]*)>", game_xml)
        attrs_raw = head.group(1) if head else ""
        attrs = {mm.group(1): mm.group(2) for mm in attr_re.finditer(attrs_raw)}
        tags: Dict[str, str] = {}
        for mm in leaf_re.finditer(game_xml):
            k = mm.group(1)
            if k in tags:
                continue
            tags[k] = mm.group(2)

        meta = {
            "gid": (tags.get("gid") or "").strip() or None,
            "lid": (tags.get("lid") or "").strip() or None,
            "league": (tags.get("league") or "").strip() or None,
            "team_h": (tags.get("team_h") or "").strip() or None,
            "team_c": (tags.get("team_c") or "").strip() or None,
            "score_h": (tags.get("score_h") or "").strip() or None,
            "score_c": (tags.get("score_c") or "").strip() or None,
            "re_time": (tags.get("re_time") or "").strip() or None,
            "systime": (tags.get("systime") or "").strip() or None,
            "dangerous": (tags.get("dangerous") or "").strip() or None,
            "strong": (tags.get("strong") or "").strip() or None,
            "datetime": (tags.get("datetime") or "").strip() or None,
        }
        boards.append(
            {
                "attrs": {
                    "id": (attrs.get("id") or "").strip() or None,
                    "master": (attrs.get("master") or "").strip() or None,
                    "mode": (attrs.get("mode") or "").strip() or None,
                    "ptype": (attrs.get("ptype") or "").strip() or None,
                },
                "meta": meta,
                "raw": tags,
            }
        )

    return {"ok": True, "code": code, "ecid": ecid, "systime": systime, "ftGids": ft_gids, "boards": boards}


def _normalize_line(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^\+", "", s)
    return s


def _is_positive_odd(value: Any) -> bool:
    try:
        n = float(str(value or "").strip())
    except Exception:
        return False
    return n > 0


def _line_average(value: Any) -> float:
    s = _normalize_line(value)
    s = re.sub(r"^[+-]", "", s)
    if not s:
        return float("nan")
    parts = [p for p in s.split("/") if p]
    if not parts:
        return float("nan")
    nums: List[float] = []
    for p in parts:
        try:
            nums.append(float(p))
        except Exception:
            return float("nan")
    return sum(nums) / float(len(nums))


def _signed_line_average(value: Any) -> float:
    text = _normalize_line(value)
    sign = -1.0 if text.startswith("-") else 1.0
    return sign * _line_average(text)


def _snapshot_handicap_params(*, gid: Any, wtype: Any, side: str, line: Any, odd: Any) -> Dict[str, str]:
    wt = str(wtype or "").strip().upper()
    side_text = str(side or "").strip().upper()
    con, ratio = _encode_handicap_line(_signed_line_average(line))
    return {
        "gid": str(gid or "").strip(),
        "wtype": wt,
        "rtype": f"{wt}{side_text}",
        "chose_team": side_text,
        "con": str(con),
        "ratio": str(ratio),
        "ioratio": str(odd or "").strip(),
    }


def _snapshot_total_params(*, gid: Any, wtype: Any, is_over: bool, line: Any, odd: Any) -> Dict[str, str]:
    wt = str(wtype or "").strip().upper()
    side = "C" if bool(is_over) else "H"
    con, ratio = _encode_total_line(_line_average(line), is_over=bool(is_over))
    return {
        "gid": str(gid or "").strip(),
        "wtype": wt,
        "rtype": f"{wt}{side}",
        "chose_team": side,
        "con": str(con),
        "ratio": str(ratio),
        "ioratio": str(odd or "").strip(),
    }


def _raw_market_value(raw: Dict[str, Any], key: str) -> str:
    wanted = str(key or "").strip()
    if not wanted:
        return ""
    for candidate in (wanted, wanted.lower(), wanted.upper()):
        value = raw.get(candidate)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _format_handicap_side_line(raw_line: Any, side: str, strong: Any) -> Optional[str]:
    line = _normalize_line(raw_line)
    if not line:
        return None
    if line in {"0", "0.0"}:
        return "0"
    if re.match(r"^[+-]", line):
        return line

    s = str(strong or "").strip().upper()
    side_u = str(side or "").strip().upper()
    if s == "H":
        return ("-" if side_u == "H" else "+") + line
    if s == "C":
        return ("+" if side_u == "H" else "-") + line
    return line


def _uniq_by(items: List[Dict[str, Any]], key_fn) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for it in items or []:
        try:
            k = key_fn(it)
        except Exception:
            k = None
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _chashui_build_simple_markets(boards: List[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    ft_boards = [
        b
        for b in (boards or [])
        if isinstance(b, dict)
        and isinstance(b.get("attrs"), dict)
        and str(b["attrs"].get("mode") or "").upper() in {"FT", "CN"}
    ]

    handicap_lines: List[Dict[str, Any]] = []
    ou_lines: List[Dict[str, Any]] = []
    half_handicap_lines: List[Dict[str, Any]] = []
    half_ou_lines: List[Dict[str, Any]] = []

    for b in ft_boards:
        t = b.get("raw") if isinstance(b.get("raw"), dict) else {}
        board_mode = str((b.get("attrs") or {}).get("mode") or "").upper()
        is_corner_board = board_mode == "CN"
        gid = None
        if isinstance(b.get("meta"), dict) and b["meta"].get("gid") is not None:
            gid = str(b["meta"].get("gid") or "").strip() or None
        hgid = str(t.get("hgid") or "").strip() or gid

        # 让球（滚球：RE）
        # CN boards expose live corner handicaps through explicit RE fields.
        # Keep prematch R restricted below; do not discard a real RE quote.
        if str(t.get("sw_RE") or "") == "Y" and t.get("ratio_re") and _is_positive_odd(t.get("ior_REH")) and _is_positive_odd(t.get("ior_REC")):
            raw_line = _normalize_line(t.get("ratio_re"))
            strong = t.get("strong") or (b.get("meta") or {}).get("strong") if isinstance(b.get("meta"), dict) else None
            home_line = _format_handicap_side_line(raw_line, "H", strong)
            away_line = _format_handicap_side_line(raw_line, "C", strong)
            handicap_lines.append(
                {
                    "gid": gid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "RE",
                    "strong": strong,
                    "home": {"team": meta.get("teamH"), "line": home_line, "odd": str(t.get("ior_REH")), "params": _snapshot_handicap_params(gid=gid, wtype="RE", side="H", line=home_line, odd=t.get("ior_REH"))},
                    "away": {"team": meta.get("teamC"), "line": away_line, "odd": str(t.get("ior_REC")), "params": _snapshot_handicap_params(gid=gid, wtype="RE", side="C", line=away_line, odd=t.get("ior_REC"))},
                }
            )

        # 上半场让球（滚球：HRE）
        # CN half-corner handicaps use hgid and explicit HRE fields on the
        # same board.  No generic prematch field is inferred here.
        if str(t.get("sw_HRE") or "") == "Y" and t.get("ratio_hre") and _is_positive_odd(t.get("ior_HREH")) and _is_positive_odd(t.get("ior_HREC")):
            raw_line = _normalize_line(t.get("ratio_hre"))
            strong = t.get("hstrong") or t.get("strong") or ((b.get("meta") or {}).get("strong") if isinstance(b.get("meta"), dict) else None)
            home_line = _format_handicap_side_line(raw_line, "H", strong)
            away_line = _format_handicap_side_line(raw_line, "C", strong)
            half_handicap_lines.append(
                {
                    "gid": hgid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "HRE",
                    "strong": strong,
                    "home": {"team": meta.get("teamH"), "line": home_line, "odd": str(t.get("ior_HREH")), "params": _snapshot_handicap_params(gid=hgid, wtype="HRE", side="H", line=home_line, odd=t.get("ior_HREH"))},
                    "away": {"team": meta.get("teamC"), "line": away_line, "odd": str(t.get("ior_HREC")), "params": _snapshot_handicap_params(gid=hgid, wtype="HRE", side="C", line=away_line, odd=t.get("ior_HREC"))},
                }
            )

        # 让球（今日/早盘：R）
        prematch_handicap = t.get("ratio") or t.get("ratio_r")
        if not is_corner_board and str(t.get("sw_R") or "") == "Y" and prematch_handicap and _is_positive_odd(t.get("ior_RH")) and _is_positive_odd(t.get("ior_RC")):
            raw_line = _normalize_line(prematch_handicap)
            strong = t.get("strong") or (b.get("meta") or {}).get("strong") if isinstance(b.get("meta"), dict) else None
            home_line = _format_handicap_side_line(raw_line, "H", strong)
            away_line = _format_handicap_side_line(raw_line, "C", strong)
            handicap_lines.append(
                {
                    "gid": gid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "R",
                    "strong": strong,
                    "home": {"team": meta.get("teamH"), "line": home_line, "odd": str(t.get("ior_RH")), "params": _snapshot_handicap_params(gid=gid, wtype="R", side="H", line=home_line, odd=t.get("ior_RH"))},
                    "away": {"team": meta.get("teamC"), "line": away_line, "odd": str(t.get("ior_RC")), "params": _snapshot_handicap_params(gid=gid, wtype="R", side="C", line=away_line, odd=t.get("ior_RC"))},
                }
            )

        # 大小（滚球：ROU）
        ou_line = t.get("ratio_rouo") or t.get("ratio_rouu")
        if str(t.get("sw_ROU") or "") == "Y" and ou_line and _is_positive_odd(t.get("ior_ROUH")) and _is_positive_odd(t.get("ior_ROUC")):
            raw_line = _normalize_line(ou_line)
            ou_lines.append(
                {
                    "gid": gid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "ROU",
                    "over": {"label": "大", "line": raw_line, "odd": str(t.get("ior_ROUC")), "params": _snapshot_total_params(gid=gid, wtype="ROU", is_over=True, line=raw_line, odd=t.get("ior_ROUC"))},
                    "under": {"label": "小", "line": raw_line, "odd": str(t.get("ior_ROUH")), "params": _snapshot_total_params(gid=gid, wtype="ROU", is_over=False, line=raw_line, odd=t.get("ior_ROUH"))},
                }
            )

        # 上半场大小（滚球：HROU）
        half_ou_line = t.get("ratio_hrouo") or t.get("ratio_hrouu")
        if str(t.get("sw_HROU") or "") == "Y" and half_ou_line and _is_positive_odd(t.get("ior_HROUH")) and _is_positive_odd(t.get("ior_HROUC")):
            raw_line = _normalize_line(half_ou_line)
            half_ou_lines.append(
                {
                    "gid": hgid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "HROU",
                    "over": {"label": "大", "line": raw_line, "odd": str(t.get("ior_HROUC")), "params": _snapshot_total_params(gid=hgid, wtype="HROU", is_over=True, line=raw_line, odd=t.get("ior_HROUC"))},
                    "under": {"label": "小", "line": raw_line, "odd": str(t.get("ior_HROUH")), "params": _snapshot_total_params(gid=hgid, wtype="HROU", is_over=False, line=raw_line, odd=t.get("ior_HROUH"))},
                }
            )

        # 大小（今日/早盘：OU）
        prematch_ou = t.get("ratio_o") or t.get("ratio_u") or t.get("ratio_ouo") or t.get("ratio_ouu")
        if not is_corner_board and str(t.get("sw_OU") or "") == "Y" and prematch_ou and _is_positive_odd(t.get("ior_OUH")) and _is_positive_odd(t.get("ior_OUC")):
            raw_line = _normalize_line(prematch_ou)
            ou_lines.append(
                {
                    "gid": gid,
                    "sort": _line_average(raw_line),
                    "rawLine": raw_line,
                    "wtype": "OU",
                    "over": {"label": "大", "line": raw_line, "odd": str(t.get("ior_OUC")), "params": _snapshot_total_params(gid=gid, wtype="OU", is_over=True, line=raw_line, odd=t.get("ior_OUC"))},
                    "under": {"label": "小", "line": raw_line, "odd": str(t.get("ior_OUH")), "params": _snapshot_total_params(gid=gid, wtype="OU", is_over=False, line=raw_line, odd=t.get("ior_OUH"))},
                }
            )

        # 更多盘口（A-F）：主盘切换到附加盘口时，wtype 通常变为 AROU/BOU/ARE 等。
        # 这些字段和主盘共存于同一 <game>，必须全部写入快照，供 Task 直接命中。
        for prefix in (() if is_corner_board else ("A", "B", "C", "D", "E", "F")):
            for base in ("ROU", "OU"):
                wt = f"{prefix}{base}"
                ratio_text = _raw_market_value(t, f"ratio_{wt.lower()}o") or _raw_market_value(t, f"ratio_{wt.lower()}u")
                odd_under = _raw_market_value(t, f"ior_{wt}H")
                odd_over = _raw_market_value(t, f"ior_{wt}C")
                enabled = _raw_market_value(t, f"sw_{wt}").upper() in {"Y", "1"}
                if not (ratio_text and _is_positive_odd(odd_under) and _is_positive_odd(odd_over)):
                    continue
                if not enabled and not ratio_text:
                    continue
                raw_line = _normalize_line(ratio_text)
                ou_lines.append(
                    {
                        "gid": gid,
                        "sort": _line_average(raw_line),
                        "rawLine": raw_line,
                        "wtype": wt,
                        "over": {"label": "大", "line": raw_line, "odd": odd_over, "params": _snapshot_total_params(gid=gid, wtype=wt, is_over=True, line=raw_line, odd=odd_over)},
                        "under": {"label": "小", "line": raw_line, "odd": odd_under, "params": _snapshot_total_params(gid=gid, wtype=wt, is_over=False, line=raw_line, odd=odd_under)},
                    }
                )

            for base in ("RE", "R"):
                wt = f"{prefix}{base}"
                ratio_text = _raw_market_value(t, f"ratio_{wt.lower()}") or _raw_market_value(t, f"ratio_{base.lower()}")
                odd_home = _raw_market_value(t, f"ior_{wt}H")
                odd_away = _raw_market_value(t, f"ior_{wt}C")
                if not (ratio_text and _is_positive_odd(odd_home) and _is_positive_odd(odd_away)):
                    continue
                raw_line = _normalize_line(ratio_text)
                strong = _raw_market_value(t, "strong") or (b.get("meta") or {}).get("strong") if isinstance(b.get("meta"), dict) else ""
                home_line = _format_handicap_side_line(raw_line, "H", strong)
                away_line = _format_handicap_side_line(raw_line, "C", strong)
                handicap_lines.append(
                    {
                        "gid": gid,
                        "sort": _line_average(raw_line),
                        "rawLine": raw_line,
                        "wtype": wt,
                        "strong": strong,
                        "home": {"team": meta.get("teamH"), "line": home_line, "odd": odd_home, "params": _snapshot_handicap_params(gid=gid, wtype=wt, side="H", line=home_line, odd=odd_home)},
                        "away": {"team": meta.get("teamC"), "line": away_line, "odd": odd_away, "params": _snapshot_handicap_params(gid=gid, wtype=wt, side="C", line=away_line, odd=odd_away)},
                    }
                )

    def _pick_main_for(key: str) -> Optional[Dict[str, Any]]:
        def ok_board(b: Dict[str, Any]) -> bool:
            if str((b.get("attrs") or {}).get("mode") or "").upper() == "CN":
                return False
            raw = b.get("raw") if isinstance(b.get("raw"), dict) else {}
            return bool(raw and _is_positive_odd(raw.get(key)))

        for b in ft_boards:
            attrs = b.get("attrs") if isinstance(b.get("attrs"), dict) else {}
            if str(attrs.get("master") or "") == "Y" and ok_board(b):
                return b
        for b in ft_boards:
            if ok_board(b):
                return b
        return None

    def _build_money_line(board: Optional[Dict[str, Any]], *, wtype: str, period: str) -> Optional[Dict[str, Any]]:
        if not board or not isinstance(board.get("raw"), dict):
            return None
        raw = board["raw"]
        wt = str(wtype or "").strip().upper()
        if not (_is_positive_odd(raw.get(f"ior_{wt}H")) and _is_positive_odd(raw.get(f"ior_{wt}N")) and _is_positive_odd(raw.get(f"ior_{wt}C"))):
            return None
        board_meta = board.get("meta") if isinstance(board.get("meta"), dict) else {}
        gid_value = str(board_meta.get("gid") or "").strip() or None
        return {
            "gid": gid_value,
            "period": period,
            "wtype": wt,
            "home": {"team": meta.get("teamH"), "odd": str(raw.get(f"ior_{wt}H"))},
            "draw": {"label": "和局", "odd": str(raw.get(f"ior_{wt}N"))},
            "away": {"team": meta.get("teamC"), "odd": str(raw.get(f"ior_{wt}C"))},
        }

    # Full-time and first-half moneyline are separate protocol fields on the
    # same FT board. Keep both so a HALF_TIME order cannot bind to full-time.
    main_for_rm = _pick_main_for("ior_RMH")
    main_for_m = _pick_main_for("ior_MH")
    main_for_hrm = _pick_main_for("ior_HRMH")
    main_for_hm = _pick_main_for("ior_HMH")
    money_line = _build_money_line(main_for_rm, wtype="RM", period="FULL_TIME")
    if money_line is None:
        money_line = _build_money_line(main_for_m, wtype="M", period="FULL_TIME")
    half_money_line = _build_money_line(main_for_hrm, wtype="HRM", period="HALF_TIME")
    if half_money_line is None:
        half_money_line = _build_money_line(main_for_hm, wtype="HM", period="HALF_TIME")

    # The FT and CN boards can legitimately expose the same wtype/line.  The
    # gid owns the market row, so deduplication must never collapse a corner
    # child row into an ordinary football row.
    handicap = _uniq_by(handicap_lines, lambda x: f"{x.get('gid') or ''}|{x.get('wtype') or ''}|{(x.get('home') or {}).get('line') or ''}|{(x.get('away') or {}).get('line') or ''}")
    over_under = _uniq_by(ou_lines, lambda x: f"{x.get('gid') or ''}|{x.get('wtype') or ''}|{x.get('rawLine') or ''}")
    half_handicap = _uniq_by(half_handicap_lines, lambda x: f"{x.get('gid') or ''}|{x.get('wtype') or ''}|{(x.get('home') or {}).get('line') or ''}|{(x.get('away') or {}).get('line') or ''}")
    half_over_under = _uniq_by(half_ou_lines, lambda x: f"{x.get('gid') or ''}|{x.get('wtype') or ''}|{x.get('rawLine') or ''}")

    return {
        "handicap": {"title": "让球", "lines": handicap},
        "overUnder": {"title": "大小", "lines": over_under},
        "halfHandicap": {"title": "上半场让球", "lines": half_handicap},
        "halfOverUnder": {"title": "上半场大小", "lines": half_over_under},
        "moneyLine": ({"title": "独赢", **money_line} if money_line else {"title": "独赢", "empty": True}),
        "halfMoneyLine": ({"title": "上半场独赢", **half_money_line} if half_money_line else {"title": "上半场独赢", "empty": True}),
    }


def _chashui_warmup(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    uid: str,
    mode: str,
    timeout_s: int,
) -> None:
    cache_key = (
        id(session),
        str(base_url or "").strip().rstrip("/"),
        str(uid or "").strip(),
        _chashui_normalize_mode(mode),
    )
    now_value = float(time.time())
    with CHASHUI_WARMUP_CACHE_LOCK:
        last_warmup = float(CHASHUI_WARMUP_CACHE.get(cache_key) or 0.0)
    if last_warmup > 0 and now_value - last_warmup <= CHASHUI_WARMUP_CACHE_TTL_S:
        return
    showtype, rtype, _ = _chashui_mode_ctx(mode)
    try:
        _member_warmup_sport_menu(
            session=session,
            base_url=str(base_url or "").strip().rstrip("/"),
            ver=str(ver or "").strip(),
            uid=str(uid or "").strip(),
            gtype="ft",
            showtype=showtype,
            rtype=rtype,
            timeout_s=timeout_s,
        )
    except Exception:
        pass
    finally:
        with CHASHUI_WARMUP_CACHE_LOCK:
            CHASHUI_WARMUP_CACHE[cache_key] = now_value
            if len(CHASHUI_WARMUP_CACHE) > 512:
                cutoff = now_value - CHASHUI_WARMUP_CACHE_TTL_S
                for key, value in list(CHASHUI_WARMUP_CACHE.items()):
                    if float(value or 0.0) < cutoff:
                        CHASHUI_WARMUP_CACHE.pop(key, None)


def _chashui_get_game_list_xml(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    uid: str,
    mode: str,
    timeout_s: int,
) -> str:
    showtype, rtype, _ = _chashui_mode_ctx(mode)
    is_early = showtype == "early"
    ts_ms = str(int(time.time() * 1000))
    form = {
        "p": "get_game_list",
        "uid": str(uid or "").strip(),
        "ver": str(ver or "").strip(),
        "langx": "zh-cn",
        "p3type": "",
        "date": "all" if is_early else "",
        "gtype": "ft",
        "showtype": showtype,
        "rtype": rtype,
        "ltype": "4",
        "filter": "FU" if is_early else "",
        "cupFantasy": "N",
        "sorttype": "L",
        "specialClick": "",
        "isFantasy": "N",
        "ts": ts_ms,
        "chgSortTS": ts_ms,
    }

    _chashui_warmup(session=session, base_url=base_url, ver=ver, uid=uid, mode=mode, timeout_s=min(6, timeout_s))
    text = _member_post_session(session=session, base_url=str(base_url or "").strip().rstrip("/"), ver=str(ver or "").strip(), form=form, timeout_s=timeout_s)
    if "<original>" in str(text or ""):
        return str(text or "")

    # 兜底：有些站点需要先 warmup，再重试一次
    _chashui_warmup(session=session, base_url=base_url, ver=ver, uid=uid, mode=mode, timeout_s=min(6, timeout_s))
    return str(_member_post_session(session=session, base_url=str(base_url or "").strip().rstrip("/"), ver=str(ver or "").strip(), form=form, timeout_s=timeout_s) or "")


def _chashui_get_game_more_xml(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    uid: str,
    mode: str,
    lid: str,
    ecid: str,
    timeout_s: int,
    filter: str = "Main",
) -> str:
    showtype, _rtype, is_rb = _chashui_mode_ctx(mode)
    form = {
        "p": "get_game_more",
        "uid": str(uid or "").strip(),
        "ver": str(ver or "").strip(),
        "langx": "zh-cn",
        "gtype": "ft",
        "showtype": showtype,
        "ltype": "4",
        "isRB": str(is_rb),
        "lid": str(lid or "").strip(),
        "specialClick": "",
        "mode": "NORMAL",
        "from": "game_more",
        "filter": str(filter or "Main").strip() or "Main",
        "ts": str(int(time.time() * 1000)),
        "ecid": str(ecid or "").strip(),
    }
    _chashui_warmup(session=session, base_url=base_url, ver=ver, uid=uid, mode=mode, timeout_s=min(6, timeout_s))
    return str(_member_post_session(session=session, base_url=str(base_url or "").strip().rstrip("/"), ver=str(ver or "").strip(), form=form, timeout_s=timeout_s) or "")


def _chashui_resolve_game_by_gid(
    *,
    session: requests.Session,
    member: Dict[str, Any],
    requested_gid: str,
    mode: str,
    timeout_s: int,
    fixture_locator: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    gid = str(requested_gid or "").strip()
    if not gid:
        raise RuntimeError("missing_gid")
    mode2 = _chashui_normalize_mode(mode)

    # A source order can arrive just as a match moves between the today and
    # live lists. Retry only that adjacent list after a successful response
    # without the requested gid. This is locator fallback, not a betting
    # warmup or legacy pre-submit flow.
    modes = [mode2]
    if mode2 == "live":
        modes.append("today")
    elif mode2 == "today":
        # Early fixtures are returned by a separate ``showtype=early``
        # endpoint. Keep today as the first request, then immediately try the
        # early feed before the live list.
        modes.extend(["early", "live"])
    elif mode2 == "early":
        modes.extend(["today", "live"])

    def _field_ci(game: Mapping[str, Any], *names: str) -> str:
        values = {str(key).lower(): value for key, value in game.items()}
        for name in names:
            value = values.get(str(name).lower())
            if value not in (None, ""):
                return str(value).strip()
        return ""

    found = None
    resolved_parent = False
    resolved_by_fixture = False
    resolved_mode = mode2
    last_lookup_error: Optional[BaseException] = None
    locator = fixture_locator if isinstance(fixture_locator, Mapping) else {}

    def _fixture_text(value: Any, *, strip_corner: bool = False) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
        text = re.sub(r"\s+", "", text).strip().lower()
        if strip_corner:
            text = re.sub(r"(?:-|－)?(?:角球数|角球|corner(?:s)?)$", "", text, flags=re.I)
        return text

    wanted_league = _fixture_text(locator.get("league"))
    wanted_home = _fixture_text(locator.get("team_h"), strip_corner=True)
    wanted_away = _fixture_text(locator.get("team_c"), strip_corner=True)
    wanted_num_h = _fixture_text(locator.get("gnum_h"))
    wanted_num_c = _fixture_text(locator.get("gnum_c"))
    locator_is_corner = bool(locator.get("is_corner"))

    def _fixture_candidates(games: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not ((wanted_home and wanted_away) or (wanted_num_h and wanted_num_c)):
            return []
        matches: List[Dict[str, Any]] = []
        for game in games:
            actual_home = _fixture_text(_field_ci(game, "TEAM_H"), strip_corner=True)
            actual_away = _fixture_text(_field_ci(game, "TEAM_C"), strip_corner=True)
            actual_num_h = _fixture_text(_field_ci(game, "GNUM_H", "GNUMH", "TEAM_H_NUM"))
            actual_num_c = _fixture_text(_field_ci(game, "GNUM_C", "GNUMC", "TEAM_C_NUM"))
            names_match = bool(wanted_home and wanted_away and actual_home == wanted_home and actual_away == wanted_away)
            numbers_match = bool(wanted_num_h and wanted_num_c and actual_num_h == wanted_num_h and actual_num_c == wanted_num_c)
            if not (names_match or numbers_match):
                continue
            if wanted_home and wanted_away and actual_home and actual_away and not names_match:
                continue
            # CN corner boards use different provider team numbers from their
            # parent FT fixture. Exact team names plus league identify the
            # parent; get_game_more later proves the requested child gid is on
            # that parent before a snapshot can be published.
            if (
                not locator_is_corner
                and wanted_num_h
                and wanted_num_c
                and actual_num_h
                and actual_num_c
                and not numbers_match
            ):
                continue
            actual_league = _fixture_text(_field_ci(game, "LEAGUE"))
            if wanted_league and actual_league and actual_league != wanted_league:
                continue
            matches.append(game)
        return matches

    for candidate_mode in modes:
        try:
            xml_text = _chashui_get_game_list_xml(
                session=session,
                base_url=str(member.get("base_url") or ""),
                ver=str(member.get("ver") or ""),
                uid=str(member.get("uid") or ""),
                mode=candidate_mode,
                timeout_s=timeout_s,
            )
        except BaseException as exc:
            last_lookup_error = exc
            continue
        t = str(xml_text or "").strip()
        if not t:
            last_lookup_error = RuntimeError("game_list_empty_response")
            continue
        if not t.startswith("<"):
            last_lookup_error = RuntimeError(f"game_list_non_xml: {t[:160]}")
            continue
        # A valid list response supersedes an earlier mode's transport error;
        # if the fixture is absent from every valid list, report game_not_found
        # rather than an unrelated timeout from the first mode.
        last_lookup_error = None
        games = _chashui_parse_game_list_original(t) or _chashui_parse_game_list_xml_games(t)
        for g in games:
            if str(g.get("GID") or "").strip() == gid:
                found = g
                resolved_parent = False
                break
        if found is None:
            for g in games:
                if _field_ci(g, "HGID", "HALF_GID", "halfGid") == gid:
                    found = g
                    resolved_parent = True
                    break
        if found is None:
            fixture_matches = _fixture_candidates(games)
            if len(fixture_matches) == 1:
                found = fixture_matches[0]
                resolved_by_fixture = True
            elif len(fixture_matches) > 1:
                raise RuntimeError("game_parent_ambiguous")
        if found is not None:
            resolved_mode = candidate_mode
            break
    if not found:
        if last_lookup_error is not None:
            raise last_lookup_error
        raise RuntimeError("game_not_found")

    return {
        "requestedGid": gid,
        "gid": str(found.get("GID") or "").strip(),
        "ecid": str(found.get("ECID") or "").strip(),
        "lid": str(found.get("LID") or "").strip(),
        "mtId": str(found.get("MT_ID") or "").strip() or None,
        "league": str(found.get("LEAGUE") or "").strip() or None,
        "teamH": str(found.get("TEAM_H") or "").strip() or None,
        "teamC": str(found.get("TEAM_C") or "").strip() or None,
        "mode": resolved_mode,
        "requestedMode": mode2,
        "modeFallback": resolved_mode != mode2,
        "resolvedFromHalfGid": resolved_parent,
        "resolvedByFixture": resolved_by_fixture,
        "resolvedByTeamNumber": bool(resolved_by_fixture and wanted_num_h and wanted_num_c),
        "fixtureLocator": {
            "league": str(locator.get("league") or "").strip(),
            "teamH": str(locator.get("team_h") or "").strip(),
            "teamC": str(locator.get("team_c") or "").strip(),
            "gnumH": str(locator.get("gnum_h") or "").strip(),
            "gnumC": str(locator.get("gnum_c") or "").strip(),
        } if resolved_by_fixture else None,
    }


def _snapshot_wtype_base(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text[1:] if len(text) > 1 and text[0] in "ABCDEF" else text


def _snapshot_market_matches(snapshot: Dict[str, Any], demand: Dict[str, Any]) -> bool:
    """Check whether a normalized snapshot contains one requested market."""
    if not isinstance(snapshot, dict) or not isinstance(demand, dict):
        return False
    simple = snapshot.get("simple") if isinstance(snapshot.get("simple"), dict) else {}
    market_type = str(demand.get("market_type") or "").strip().upper()
    period = str(demand.get("period") or "FULL_TIME").strip().upper()
    mode = str(demand.get("mode") or "live").strip().lower()
    if market_type == "TOTAL":
        section_name = "halfOverUnder" if period == "HALF_TIME" else "overUnder"
        expected_wtype = "HROU" if period == "HALF_TIME" else "ROU" if mode == "live" else "OU"
        side_key = "over" if str(demand.get("side") or "").upper() == "OVER" else "under"
    elif market_type == "HANDICAP":
        section_name = "halfHandicap" if period == "HALF_TIME" else "handicap"
        expected_wtype = "HRE" if period == "HALF_TIME" and mode == "live" else "RE" if mode == "live" else "R"
        side_key = "home" if str(demand.get("side") or "").upper() == "HOME" else "away"
    else:
        money_key = "halfMoneyLine" if period == "HALF_TIME" else "moneyLine"
        money = simple.get(money_key) if isinstance(simple.get(money_key), dict) else {}
        if not money and period == "HALF_TIME":
            legacy = simple.get("moneyLine") if isinstance(simple.get("moneyLine"), dict) else {}
            if str(legacy.get("period") or "").strip().upper() == "HALF_TIME":
                money = legacy
        if not money or bool(money.get("empty")):
            return False
        expected_wtype = "HRM" if period == "HALF_TIME" and mode == "live" else "HM" if period == "HALF_TIME" else "RM" if mode == "live" else "M"
        side_key = {"HOME": "home", "AWAY": "away", "DRAW": "draw"}.get(str(demand.get("side") or "").upper(), "")
        selected = money.get(side_key) if side_key else None
        if not isinstance(selected, dict):
            return False
        params = selected.get("params") if isinstance(selected.get("params"), dict) else {}
        actual_wtype = _snapshot_wtype_base(params.get("wtype") or money.get("wtype"))
        if actual_wtype != _snapshot_wtype_base(expected_wtype):
            return False
        wanted_gid = str(demand.get("gid") or "").strip()
        actual_gid = str(params.get("gid") or money.get("gid") or "").strip()
        if wanted_gid and actual_gid and wanted_gid != actual_gid:
            return False
        return True

    section = simple.get(section_name) if isinstance(simple.get(section_name), dict) else {}
    lines = section.get("lines") if isinstance(section.get("lines"), list) else []
    target = demand.get("target_line")
    try:
        target_value = float(target) if target not in (None, "") else None
    except Exception:
        target_value = None
    wanted_gid = str(demand.get("gid") or "").strip()
    allowed_gids = {wanted_gid}
    meta = snapshot.get("meta") if isinstance(snapshot.get("meta"), dict) else {}
    for value in (meta.get("requestedGid"), meta.get("masterGid")):
        if str(value or "").strip():
            allowed_gids.add(str(value).strip())
    for item in snapshot.get("ftGids") if isinstance(snapshot.get("ftGids"), list) else []:
        if isinstance(item, dict) and str(item.get("gid") or "").strip():
            allowed_gids.add(str(item.get("gid")).strip())
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_gid = str(line.get("gid") or "").strip()
        if bool(demand.get("is_corner")) and wanted_gid and line_gid != wanted_gid:
            continue
        if wanted_gid and line_gid and line_gid not in allowed_gids:
            continue
        side = line.get(side_key) if isinstance(line.get(side_key), dict) else {}
        params = side.get("params") if isinstance(side.get("params"), dict) else {}
        if expected_wtype and _snapshot_wtype_base(params.get("wtype") or line.get("wtype")) != _snapshot_wtype_base(expected_wtype):
            continue
        if target_value is None:
            return True
        # Handicap ``rawLine`` is only the unsigned board magnitude.  The
        # selected side owns the actual signed line (for example, a home
        # favourite is ``-0.5`` while rawLine is ``0.5``).  Comparing the
        # unsigned value makes a valid favourite order look missing and sends
        # the snapshot worker through every Main/All retry until its deadline.
        current = (
            _signed_line_average(side.get("line") or line.get("rawLine"))
            if market_type == "HANDICAP"
            else _line_average(line.get("rawLine") or side.get("line"))
        )
        if current == current and line_matches(
            target_value,
            current,
            market_type=market_type,
            period=period,
            is_corner=bool(demand.get("is_corner")),
        ):
            return True
    return False


def _snapshot_market_diagnostics(snapshot: Dict[str, Any], requirements: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    simple = snapshot.get("simple") if isinstance(snapshot, dict) and isinstance(snapshot.get("simple"), dict) else {}
    raw = snapshot.get("raw") if isinstance(snapshot.get("raw"), dict) else {}
    raw_market_count = sum(
        1
        for key, value in raw.items()
        if str(key).lower().startswith(("ratio_", "ior_", "sw_"))
        and str(value or "").strip() not in {"", "0", "N"}
    )
    handicap = ((simple.get("handicap") or {}).get("lines") if isinstance(simple.get("handicap"), dict) else None) or []
    half_handicap = ((simple.get("halfHandicap") or {}).get("lines") if isinstance(simple.get("halfHandicap"), dict) else None) or []
    totals = ((simple.get("overUnder") or {}).get("lines") if isinstance(simple.get("overUnder"), dict) else None) or []
    half_totals = ((simple.get("halfOverUnder") or {}).get("lines") if isinstance(simple.get("halfOverUnder"), dict) else None) or []
    parsed_market_count = len(handicap) + len(half_handicap) + len(totals) + len(half_totals)
    for money_key in ("moneyLine", "halfMoneyLine"):
        money = simple.get(money_key) if isinstance(simple.get(money_key), dict) else {}
        if money and not money.get("empty"):
            parsed_market_count += 1
    reqs = [item for item in (requirements or []) if isinstance(item, dict)]
    return {
        "raw_market_count": int(raw_market_count),
        "parsed_market_count": int(parsed_market_count),
        "over_under_line_count": len(totals) + len(half_totals),
        "handicap_line_count": len(handicap) + len(half_handicap),
        "target_line_found": all(_snapshot_market_matches(snapshot, item) for item in reqs) if reqs else None,
        "required_market_count": len(reqs),
    }


def _chashui_get_odds_snapshot_by_resolved(
    *,
    session: requests.Session,
    member: Dict[str, Any],
    resolved: Dict[str, Any],
    include_raw: bool,
    confirm_odds: bool,
    timeout_s: int,
    market_filter: str = "Main",
    required_markets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    gm_xml = _chashui_get_game_more_xml(
        session=session,
        base_url=str(member.get("base_url") or ""),
        ver=str(member.get("ver") or ""),
        uid=str(member.get("uid") or ""),
        mode=str(resolved.get("mode") or "live"),
        lid=str(resolved.get("lid") or ""),
        ecid=str(resolved.get("ecid") or ""),
        timeout_s=timeout_s,
        filter=str(market_filter or "Main").strip() or "Main",
    )
    parsed = _chashui_parse_game_more_xml(gm_xml)
    if not parsed.get("ok"):
        raise RuntimeError(str(parsed.get("error") or "game_more_failed"))

    boards = parsed.get("boards") if isinstance(parsed.get("boards"), list) else []
    if bool(resolved.get("resolvedByFixture")):
        requested_gid = str(resolved.get("requestedGid") or "").strip()
        board_gids = set()
        for board in boards:
            if not isinstance(board, dict):
                continue
            board_meta = board.get("meta") if isinstance(board.get("meta"), dict) else {}
            board_raw = board.get("raw") if isinstance(board.get("raw"), dict) else {}
            for value in (board_meta.get("gid"), board_raw.get("hgid")):
                gid_value = str(value or "").strip()
                if gid_value:
                    board_gids.add(gid_value)
        if requested_gid and requested_gid not in board_gids:
            raise RuntimeError("requested_market_board_not_found")
    main_board = None
    for b in boards:
        if not isinstance(b, dict):
            continue
        attrs = b.get("attrs") if isinstance(b.get("attrs"), dict) else {}
        if str(attrs.get("master") or "") == "Y":
            main_board = b
            break
    if not main_board:
        for b in boards:
            if not isinstance(b, dict):
                continue
            meta = b.get("meta") if isinstance(b.get("meta"), dict) else {}
            if str(meta.get("gid") or "") == str(resolved.get("gid") or ""):
                main_board = b
                break
    if not main_board and boards:
        main_board = boards[0]

    meta_board = main_board.get("meta") if isinstance(main_board, dict) and isinstance(main_board.get("meta"), dict) else {}
    score = None
    if meta_board.get("score_h") is not None and meta_board.get("score_c") is not None:
        sh = str(meta_board.get("score_h") or "").strip()
        sc = str(meta_board.get("score_c") or "").strip()
        if sh != "" and sc != "":
            score = f"{sh}:{sc}"

    meta = {
        "requestedGid": resolved.get("requestedGid"),
        "masterGid": meta_board.get("gid") or resolved.get("gid"),
        "league": meta_board.get("league") or resolved.get("league"),
        "teamH": meta_board.get("team_h") or resolved.get("teamH"),
        "teamC": meta_board.get("team_c") or resolved.get("teamC"),
        "score": score,
        "mode": resolved.get("mode") or "live",
        "reTime": meta_board.get("re_time") or None,
        "datetime": meta_board.get("datetime") or None,
        "systime": meta_board.get("systime") or parsed.get("systime") or None,
        "lid": meta_board.get("lid") or resolved.get("lid"),
        "ecid": parsed.get("ecid") or resolved.get("ecid"),
        "mtId": resolved.get("mtId"),
    }

    simple = _chashui_build_simple_markets(boards, meta)

    out: Dict[str, Any] = {
        "meta": meta,
        "ftGids": parsed.get("ftGids") or [],
        "simple": simple,
        "_parsed_board_count": len(boards),
    }
    if include_raw and isinstance(main_board, dict):
        out["raw"] = main_board.get("raw") if isinstance(main_board.get("raw"), dict) else None
        out["raw_xml"] = gm_xml
    out["_snapshot_diagnostics"] = _snapshot_market_diagnostics(out, required_markets)
    return out


def _chashui_simple_market_count(snapshot: Dict[str, Any]) -> int:
    simple = snapshot.get("simple") if isinstance(snapshot, dict) and isinstance(snapshot.get("simple"), dict) else {}
    handicap_lines = ((simple.get("handicap") or {}).get("lines") if isinstance(simple.get("handicap"), dict) else None) or []
    ou_lines = ((simple.get("overUnder") or {}).get("lines") if isinstance(simple.get("overUnder"), dict) else None) or []
    money_line = simple.get("moneyLine") if isinstance(simple.get("moneyLine"), dict) else {}
    half_money_line = simple.get("halfMoneyLine") if isinstance(simple.get("halfMoneyLine"), dict) else {}
    count = 0
    if isinstance(handicap_lines, list):
        count += len(handicap_lines)
    if isinstance(ou_lines, list):
        count += len(ou_lines)
    if isinstance(money_line, dict) and money_line and not bool(money_line.get("empty", False)):
        count += 1
    if isinstance(half_money_line, dict) and half_money_line and not bool(half_money_line.get("empty", False)):
        count += 1
    return count


def _chashui_get_odds_snapshot_with_empty_retry(
    *,
    session: requests.Session,
    member: Dict[str, Any],
    resolved: Dict[str, Any],
    include_raw: bool,
    confirm_odds: bool,
    timeout_s: int,
    attempts: int = 3,
    sleep_s: float = 0.35,
    required_markets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tries = max(1, int(attempts or 1))
    last: Dict[str, Any] = {}
    for i in range(tries):
        filters = ["Main", "All"] if required_markets else ["Main"]
        for market_filter in filters:
            last = _chashui_get_odds_snapshot_by_resolved(
                session=session,
                member=member,
                resolved=resolved,
                include_raw=include_raw,
                confirm_odds=confirm_odds,
                timeout_s=timeout_s,
                market_filter=market_filter,
                required_markets=required_markets,
            )
            diagnostics = last.get("_snapshot_diagnostics") if isinstance(last.get("_snapshot_diagnostics"), dict) else {}
            target_found = diagnostics.get("target_line_found")
            if _chashui_simple_market_count(last) > 0 and (not required_markets or target_found is True):
                if i > 0:
                    last["empty_retry_used"] = i
                last["snapshot_fetch_filter"] = market_filter
                return last
        if i < tries - 1:
            try:
                time.sleep(float(sleep_s))
            except Exception:
                pass
    if tries > 1:
        last["empty_retry_used"] = tries - 1
    if required_markets:
        last["snapshot_fetch_target_missing"] = True
        last["snapshot_fetch_filter"] = "All"
    return last


class Handler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args,
        directory: Optional[str] = None,
        orders_dir: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        config_path: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        **kwargs,
    ):
        self._orders_dir = orders_dir or Path("orders")
        self._state_dir = state_dir or Path("state")
        self._config_path = config_path or Path("config.json")
        self._data_dir = data_dir or Path("data")
        self._arsenal_path = self._data_dir / "arsenal.json"
        self._follow_settings_path = self._data_dir / "follow_settings.json"
        self._mappings_path = self._data_dir / "mappings.json"
        super().__init__(*args, directory=directory, **kwargs)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_json_bytes(status, body)

    def _send_json_bytes(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        # 避免浏览器缓存旧版 webui/app.js 导致“订单不显示/逻辑不生效”看似没修复。
        try:
            if not str(self.path or "").startswith("/api/"):
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
        except Exception:
            pass
        super().end_headers()

    def handle_one_request(self) -> None:
        """
        处理单个 HTTP 请求。

        说明：
        - SSE/长连接下，浏览器切换页面或网络抖动会导致客户端主动断开；
        - `http.server` 默认会把 BrokenPipe/ConnectionReset 打印为 traceback，污染日志。
        - 这里仅对“客户端断开”类异常做吞掉处理，不影响真实业务异常的暴露。
        """
        try:
            return super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
            return
        except OSError as e:
            # macOS/Linux：有时不会被包装成 ConnectionResetError，而是直接 OSError(errno=54/104/32)
            if getattr(e, "errno", None) in {32, 54, 104}:
                self.close_connection = True
                return
            raise

    def do_GET(self) -> None:
        u = urlparse(self.path)
        if u.path == "/api/telegram-signal/status":
            settings = load_telegram_signal_settings(self._data_dir / "telegram_signal.json")
            secret_status = sanitize_telegram_signal_secret_status(
                load_telegram_signal_secrets(self._data_dir / "telegram_signal_secrets.json")
            )
            alive, pid = telegram_signal_worker_running_flag()
            worker_status = telegram_signal_worker_health_status(
                alive,
                pid,
                self._data_dir.parent / ".run/telegram_signal_health.json",
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "settings": settings,
                    **secret_status,
                    **worker_status,
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/telegram-signal/chats":
            alive, _pid = telegram_signal_worker_running_flag()
            if alive:
                settings = load_telegram_signal_settings(self._data_dir / "telegram_signal.json")
                saved = [dict(item) for item in (settings.get("source_chats") or []) if isinstance(item, dict)]
                known = {str(item.get("id") or "").strip() for item in saved}
                for chat_id in settings.get("source_chat_ids") or []:
                    value = str(chat_id or "").strip()
                    if value and value not in known:
                        saved.append({"id": value, "title": "已保存来源", "type": "group"})
                self._send_json(
                    200,
                    {"ok": True, "chats": saved, "cached": True, "server_time": now_iso()},
                )
                return
            try:
                chats = list_source_dialogs(self._data_dir / "telegram_signal_secrets.json")
            except TelegramSignalClientError as exc:
                self._send_json(409, {"ok": False, "message": str(exc), "chats": []})
                return
            except Exception as exc:
                self._send_json(502, {"ok": False, "message": str(exc), "chats": []})
                return
            self._send_json(200, {"ok": True, "chats": chats, "server_time": now_iso()})
            return

        if u.path == "/api/telegram-signal/logs":
            qs = parse_qs(u.query)
            try:
                limit = max(1, min(500, int((qs.get("limit") or ["100"])[0])))
            except Exception:
                limit = 100
            rows = read_telegram_signal_events(self._data_dir / "telegram_signal_events.jsonl", limit=limit)
            self._send_json(200, {"ok": True, "rows": rows, "server_time": now_iso()})
            return

        if u.path == "/api/targets":
            self._send_json(200, {"ok": True, "targets": list_targets(self._orders_dir), "server_time": now_iso()})
            return

        if u.path == "/api/orders":
            qs = parse_qs(u.query)
            limit = int((qs.get("limit") or ["200"])[0])
            since = int((qs.get("since") or ["0"])[0])
            target = (qs.get("target") or [""])[0].strip()
            deleted_order_ids = load_deleted_order_ids(self._data_dir)

            orders: List[Dict[str, Any]] = []
            if target:
                path = self._orders_dir / f"{safe_name(target)}.jsonl"
                orders = read_orders_file(path, since=since, limit=limit)
            else:
                # 汇总所有 target
                for p in self._orders_dir.glob("*.jsonl"):
                    orders.extend(read_orders_file(p, since=since, limit=limit))

            orders.extend(read_ps3838_dashboard_orders(
                self._data_dir, self._read_config().get("ps3838_accounts") or [],
                target=target, since=since, limit=limit,
            ))
            orders.sort(key=lambda x: int(x.get("ts") or 0), reverse=True)

            if deleted_order_ids:
                orders = [row for row in orders if (row.get("display_id") if row.get("read_only") else _extract_order_id_from_row(row)) not in deleted_order_ids]
            orders = orders[:max(1, int(limit))]

            order_ids: List[str] = []
            seen_order_ids: set = set()
            for row in orders:
                if not isinstance(row, dict) or row.get("read_only"):
                    continue
                oid = _extract_order_id_from_row(row)
                if not oid or oid in seen_order_ids:
                    continue
                seen_order_ids.add(oid)
                order_ids.append(oid)
                if len(order_ids) >= 300:
                    break

            avg_odds_by_order, latest_follow_by_order = _load_follow_order_summaries(
                self._data_dir / "follow_results.jsonl",
                order_ids,
            )
            for row in orders:
                if not isinstance(row, dict) or row.get("read_only"):
                    continue
                oid = _extract_order_id_from_row(row)
                rec = avg_odds_by_order.get(oid)
                if rec:
                    row["follow_avg_odds"] = rec.get("avg")
                    row["follow_avg_odds_count"] = rec.get("count")
                else:
                    row["follow_avg_odds"] = None
                    row["follow_avg_odds_count"] = 0
                display_line, reverse_applied = compose_follow_display_line(row, latest_follow_by_order.get(oid))
                row["display_line"] = display_line
                row["display_reverse_applied"] = bool(reverse_applied)

            self._send_json(
                200,
                {
                    "ok": True,
                    "orders": orders,
                    "targets": list_targets(self._orders_dir),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/follow-queue":
            qs = parse_qs(u.query)
            try:
                limit = int(float(str((qs.get("limit") or ["50"])[0]).strip() or "50"))
            except Exception:
                limit = 50
            limit = max(1, min(200, int(limit)))

            include_done = bool(re.match(r"^(1|true|yes)$", str((qs.get("include_done") or ["0"])[0]), flags=re.IGNORECASE))
            try:
                done_limit = int(float(str((qs.get("done_limit") or ["20"])[0]).strip() or "20"))
            except Exception:
                done_limit = 20
            done_limit = max(0, min(200, int(done_limit)))

            state_path = self._data_dir / "follow_state.json"
            results_path = self._data_dir / "follow_results.jsonl"
            try:
                state_stat = state_path.stat()
                state_revision = (int(state_stat.st_mtime_ns), int(state_stat.st_size))
            except OSError:
                state_revision = (0, 0)
            try:
                results_stat = results_path.stat()
                results_revision = int(results_stat.st_mtime_ns)
            except OSError:
                results_revision = 0
            snapshot_metrics_path = self._data_dir / "snapshot_watch_metrics.jsonl"
            try:
                snapshot_stat = snapshot_metrics_path.stat()
                snapshot_revision = (int(snapshot_stat.st_mtime_ns), int(snapshot_stat.st_size))
            except OSError:
                snapshot_revision = (0, 0)
            queue_cache_key = (
                str(self._data_dir.resolve()),
                state_revision[0] ^ results_revision,
                state_revision[1],
                snapshot_revision[0] ^ snapshot_revision[1],
                include_done,
                done_limit,
            )
            now_cache = time.monotonic()
            with FOLLOW_QUEUE_HTTP_CACHE_LOCK:
                cached_queue = FOLLOW_QUEUE_HTTP_CACHE.get(queue_cache_key)
                if cached_queue and (now_cache - cached_queue[0] <= FOLLOW_HTTP_CACHE_TTL_S):
                    self._send_json_bytes(200, cached_queue[1])
                    return

            st = read_json_file(state_path, {})
            pending_raw = st.get("pending_orders") if isinstance(st, dict) else None
            jobs_raw = st.get("jobs") if isinstance(st, dict) else None
            done_raw = st.get("done") if isinstance(st, dict) else None

            pending: Dict[str, Dict[str, Any]] = pending_raw if isinstance(pending_raw, dict) else {}
            jobs: Dict[str, Dict[str, Any]] = jobs_raw if isinstance(jobs_raw, dict) else {}
            done: Dict[str, Dict[str, Any]] = done_raw if isinstance(done_raw, dict) else {}
            grouped_jobs = _follow_queue_group_jobs(jobs)
            visible_done = {key: value for key, value in done.items() if "#part:" not in str(key)}

            items: List[Dict[str, Any]] = []

            # jobs：按 next_try_at 升序（越早越优先）
            for key, job in grouped_jobs.items():
                if not isinstance(job, dict):
                    continue
                order0 = job.get("order") if isinstance(job.get("order"), dict) else {}
                items.append(
                    {
                        "key": str(key),
                        "status": "job",
                        "job": _follow_queue_simplify_job(job),
                        "order": _follow_queue_simplify_order(order0),
                    }
                )

            # pending_orders：尚未生成 job 的订单
            for key, order0 in pending.items():
                if key in grouped_jobs:
                    continue
                if not isinstance(order0, dict):
                    continue
                items.append(
                    {
                        "key": str(key),
                        "status": "pending",
                        "job": None,
                        "order": _follow_queue_simplify_order(order0),
                    }
                )

            order_ids = []
            seen_order_ids: set = set()
            for it in items:
                order0 = it.get("order") if isinstance(it.get("order"), dict) else {}
                oid = str(order0.get("orderId") or "").strip()
                if not oid or oid in seen_order_ids:
                    continue
                seen_order_ids.add(oid)
                order_ids.append(oid)

            latest_fail_by_order = _load_latest_follow_rows_by_order(
                self._data_dir / "follow_results.jsonl",
                order_ids,
                ok_only=False,
            )
            snapshot_market_keys: Set[Tuple[str, str]] = set()
            for it in items:
                order0 = it.get("order") if isinstance(it.get("order"), dict) else {}
                derived = order0.get("derived") if isinstance(order0.get("derived"), dict) else {}
                gid0 = str(order0.get("gid") or derived.get("gid") or "").strip()
                mode0 = str(derived.get("mode") or ("live" if _is_live_backend_order(order0) else "today")).strip().lower()
                if gid0:
                    snapshot_market_keys.add((gid0, mode0))
            latest_snapshot_by_market = _load_latest_snapshot_watch_by_market(
                self._data_dir / "snapshot_watch_metrics.jsonl",
                snapshot_market_keys,
            )
            for it in items:
                order0 = it.get("order") if isinstance(it.get("order"), dict) else {}
                oid = str(order0.get("orderId") or "").strip()
                row = latest_fail_by_order.get(oid)
                if row:
                    order0["last_result"] = _follow_queue_simplify_result(row)
                derived = order0.get("derived") if isinstance(order0.get("derived"), dict) else {}
                gid0 = str(order0.get("gid") or derived.get("gid") or "").strip()
                mode0 = str(derived.get("mode") or ("live" if _is_live_backend_order(order0) else "today")).strip().lower()
                snapshot_diag = latest_snapshot_by_market.get((gid0, mode0)) if gid0 else None
                if snapshot_diag and (
                    snapshot_diag.get("ok") is False
                    or snapshot_diag.get("target_line_found") is False
                    or snapshot_diag.get("failure_summary")
                ):
                    order0["snapshot_diagnostics"] = snapshot_diag
                    result0 = order0.get("last_result") if isinstance(order0.get("last_result"), dict) else None
                    if result0 is None:
                        result0 = {
                            "ok": False,
                            "message": snapshot_diag.get("failure_summary") or snapshot_diag.get("error_code") or "盘口采集失败",
                            "error_type": snapshot_diag.get("error_code"),
                            "exception": None,
                            "response_head": None,
                        }
                        order0["last_result"] = result0
                    summary = str(snapshot_diag.get("failure_summary") or "").strip()
                    if snapshot_diag.get("submission") == "none" and summary and "未提交下注" not in summary:
                        summary = f"{summary}，未提交下注"
                    result0["snapshot_failure_summary"] = summary or None
                    result0["snapshot_failover_count"] = snapshot_diag.get("failover_count")
                    result0["snapshot_attempted_member_ids"] = snapshot_diag.get("attempted_member_ids")
                    result0["snapshot_submission"] = snapshot_diag.get("submission") or "none"

            def sort_key(it: Dict[str, Any]) -> Tuple[int, float, float]:
                st0 = str(it.get("status") or "")
                if st0 == "job":
                    j = it.get("job") or {}
                    try:
                        n = float(j.get("next_try_at") or 0.0)
                    except Exception:
                        n = 0.0
                    try:
                        c = float(j.get("created_at") or 0.0)
                    except Exception:
                        c = 0.0
                    # jobs 放前面：用 (0, next_try_at, -created_at) 排序
                    return (0, n if n > 0 else 0.0, -c)
                o = it.get("order") or {}
                try:
                    ts0 = float(o.get("ts") or 0.0)
                except Exception:
                    ts0 = 0.0
                # pending 放后面：用 (1, -ts, 0)
                return (1, -ts0, 0.0)

            items.sort(key=sort_key)
            items = items[:limit]

            done_list: List[Dict[str, Any]] = []
            done_items: List[Dict[str, Any]] = []
            done_ok = 0
            done_failed = 0
            if visible_done:
                try:
                    for v in visible_done.values():
                        if not isinstance(v, dict):
                            continue
                        st_done = str(v.get("status") or "").strip().lower()
                        if st_done in {"ok", "success", "succeeded", "done"}:
                            done_ok += 1
                        elif st_done in {"fail", "failed", "error", "err"}:
                            done_failed += 1
                except Exception:
                    done_ok = 0
                    done_failed = 0

            if include_done and done_limit > 0 and visible_done:
                try:
                    rows = sorted(
                        [(k, v) for k, v in visible_done.items() if isinstance(v, dict)],
                        key=lambda kv: float((kv[1] or {}).get("ts") or 0.0),
                        reverse=True,
                    )
                    selected = rows[:done_limit]
                    done_order_map = _follow_queue_done_order_map(self._orders_dir, [str(k) for k, _ in selected])
                    active_keys = {str(item.get("key") or "") for item in items}
                    for k, v in selected:
                        skey = str(k)
                        raw_order = v.get("order") if isinstance(v.get("order"), dict) else done_order_map.get(skey, {})
                        simple_order = _follow_queue_simplify_order(raw_order)
                        reason = str(v.get("reason") or v.get("message") or "").strip()
                        if reason:
                            simple_order["last_result"] = {
                                "ok": False,
                                "message": reason,
                                "error_type": None,
                                "exception": None,
                                "response_head": None,
                            }
                        terminal_item = _follow_queue_terminal_item(skey, v, simple_order)
                        if skey not in active_keys:
                            done_items.append(terminal_item)
                        done_list.append(
                            {
                                "key": skey,
                                "status": v.get("status"),
                                "ticket_id": v.get("ticket_id"),
                                "reason": reason or None,
                                "ts": v.get("ts"),
                            }
                        )
                except Exception:
                    done_list = []
                    done_items = []

            if done_items:
                items.extend(done_items)

            queue_payload = {
                    "ok": True,
                    "items": items,
                    "counts": {
                        "pending_orders": len(pending),
                        "jobs": len(grouped_jobs),
                        "tasks": len(jobs),
                        "done": len(visible_done),
                        "done_ok": done_ok,
                        "done_failed": done_failed,
                    },
                    "done_recent": done_list,
                    "updated_at": st.get("updated_at") if isinstance(st, dict) else None,
                    "server_time": now_iso(),
                }
            queue_body = json.dumps(queue_payload, ensure_ascii=False).encode("utf-8")
            with FOLLOW_QUEUE_HTTP_CACHE_LOCK:
                FOLLOW_QUEUE_HTTP_CACHE[queue_cache_key] = (time.monotonic(), queue_body)
                if len(FOLLOW_QUEUE_HTTP_CACHE) > 24:
                    oldest = sorted(FOLLOW_QUEUE_HTTP_CACHE.items(), key=lambda item: item[1][0])[:8]
                    for old_key, _ in oldest:
                        FOLLOW_QUEUE_HTTP_CACHE.pop(old_key, None)
            self._send_json_bytes(200, queue_body)
            return

        if u.path == "/api/accounts":
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            accounts = []
            for t in targets:
                accounts.append(
                    {
                        "name": t.get("name") or "",
                        "base_url": t.get("base_url") or "",
                        "username": t.get("username") or "",
                        "enabled": bool(t.get("enabled", True)),
                        "proxy": t.get("proxy") or "",
                        "telegram_chat_id": t.get("telegram_chat_id") or "",
                        "relogin_login_tab": t.get("relogin_login_tab") or 0,
                        "login2_watch_members": normalize_member_names(t.get("login2_watch_members")),
                        "login2_watch_downlines": normalize_member_names(t.get("login2_watch_downlines")),
                        "relogin_mode": t.get("relogin_mode") or "none",
                        "time_offset_hours": t.get("time_offset_hours") or 0,
                    }
                )
            ps_accounts = []
            for item in cfg.get("ps3838_accounts") or []:
                if not isinstance(item, dict):
                    continue
                ps_accounts.append(
                    {
                        "name": item.get("name") or "",
                        "provider": "ps3838",
                        "base_url": item.get("base_url") or "https://www.ps38ag.com",
                        "username": item.get("username") or "",
                        "enabled": bool(item.get("enabled", True)),
                        "proxy": item.get("proxy") or "",
                        "interval_seconds": float(item.get("interval_seconds") or 15),
                    }
                )
            self._send_json(
                200,
                {"ok": True, "accounts": accounts, "ps3838_accounts": ps_accounts, "server_time": now_iso()},
            )
            return

        if u.path == "/api/telegram-config":
            cfg = self._read_config()
            bot_token = str(cfg.get("telegram_bot_token") or "").strip()
            chat_id = str(cfg.get("telegram_chat_id") or "").strip()
            allow_chat = str(cfg.get("telegram_allow_chat") or "").strip()
            proxy = str(cfg.get("telegram_proxy") or "").strip()
            order_forward_enabled = bool(cfg.get("telegram_order_forward_enabled", True))
            backend_order_push_enabled = bool(cfg.get("telegram_backend_order_push_enabled") or False)
            backend_order_bot_token = str(cfg.get("telegram_backend_order_bot_token") or "").strip()
            backend_order_chat_id = str(cfg.get("telegram_backend_order_chat_id") or "").strip()
            backend_order_proxy = str(cfg.get("telegram_backend_order_proxy") or "").strip()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(bot_token and chat_id),
                    "token_mask": mask_secret(bot_token, keep=4) if bot_token else "",
                    "chat_id": chat_id,
                    "allow_chat": allow_chat,
                    "proxy": proxy,
                    "order_forward_enabled": order_forward_enabled,
                    "backend_order_push_enabled": backend_order_push_enabled,
                    "backend_order_configured": bool(backend_order_bot_token and backend_order_chat_id),
                    "backend_order_token_mask": mask_secret(backend_order_bot_token, keep=4) if backend_order_bot_token else "",
                    "backend_order_chat_id": backend_order_chat_id,
                    "backend_order_proxy": backend_order_proxy,
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/signal-pull-config":
            cfg = ensure_signal_pull_config_defaults(self._read_config())
            self._send_json(
                200,
                {
                    "ok": True,
                    "config": {
                        "signal_pull_enabled": bool(cfg.get("signal_pull_enabled")),
                        "signal_pull_url": str(cfg.get("signal_pull_url") or "").strip(),
                        "signal_pull_token": str(cfg.get("signal_pull_token") or "").strip(),
                        "signal_pull_live_only": bool(cfg.get("signal_pull_live_only")),
                        "signal_pull_limit": int(cfg.get("signal_pull_limit") or 200),
                        "signal_pull_interval_seconds": float(cfg.get("signal_pull_interval_seconds") or 1.0),
                    },
                    "configured": bool(str(cfg.get("signal_pull_url") or "").strip()),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/local-bridge-config":
            self._send_json(200, self._local_bridge_response())
            return

        if u.path == "/api/telegram-status-log":
            qs = parse_qs(u.query)
            limit = int((qs.get("limit") or ["200"])[0])
            path = self._data_dir / "telegram_status_log.jsonl"
            rows = read_jsonl_file(path, limit=max(1, min(1000, int(limit))))
            self._send_json(200, {"ok": True, "rows": rows, "server_time": now_iso()})
            return

        if u.path == "/api/adspower/profiles":
            qs = parse_qs(u.query)
            api_base = normalize_adspower_api_base(str((qs.get("api_base") or [DEFAULT_ADSPOWER_API_BASE])[0] or DEFAULT_ADSPOWER_API_BASE))
            api_key = str((qs.get("api_key") or [""])[0] or "").strip()
            page_size = 100
            try:
                page_size = int((qs.get("page_size") or ["100"])[0] or 100)
            except Exception:
                page_size = 100
            page_size = max(20, min(500, page_size))

            rows, err = adspower_list_profiles(
                api_base=api_base,
                api_key=api_key,
                page_size=page_size,
                timeout_s=12,
            )
            if err and not rows:
                self._send_json(
                    502,
                    {"ok": False, "message": err, "profiles": [], "api_base": api_base, "server_time": now_iso()},
                )
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [x for x in (doc.get("members") or []) if isinstance(x, dict)]
            imported_pid = {
                str(x.get("adspower_profile_id") or "").strip()
                for x in members
                if str(x.get("session_provider") or "").strip().lower() == "adspower"
                and str(x.get("adspower_profile_id") or "").strip()
            }
            imported_pno = {
                str(x.get("adspower_profile_no") or "").strip()
                for x in members
                if str(x.get("session_provider") or "").strip().lower() == "adspower"
                and str(x.get("adspower_profile_no") or "").strip()
            }

            out: List[Dict[str, Any]] = []
            for row in rows:
                item = _adspower_profile_to_payload(row)
                pid = str(item.get("profile_id") or "").strip()
                pno = str(item.get("profile_no") or "").strip()
                item["imported"] = bool((pid and pid in imported_pid) or (pno and pno in imported_pno))
                out.append(item)

            self._send_json(
                200,
                {
                    "ok": True,
                    "api_base": api_base,
                    "profiles": out,
                    "count": len(out),
                    "warning": err if err else "",
                    "server_time": now_iso(),
                },
            )
            return

        # -------------------------
        # 查水（实时盘口/水位，只读）
        # -------------------------
        if u.path == "/api/telegram-signal/games":
            qs = parse_qs(u.query)
            mode = _chashui_normalize_mode(str((qs.get("mode") or ["live"])[0]).strip())
            payload = _telegram_signal_load_games(self._arsenal_path, mode=mode)
            payload["server_time"] = now_iso()
            if payload.get("ok"):
                self._send_json(200, payload)
            else:
                status = 404 if payload.get("message") == "readonly_pool_empty" else 502
                self._send_json(status, payload)
            return

        if u.path == "/api/chashui/status":
            # 查水已移除：只返回本地会员状态，严禁展示层通过此路由触发上游访问。
            doc = read_json_file(self._arsenal_path, {"members": []})
            members_raw = doc.get("members") if isinstance(doc, dict) else []
            members = []
            for row in members_raw or []:
                if not isinstance(row, dict):
                    continue
                # 兼容旧页面的会员选择，但不再把密码、uid、代理等凭据
                # 通过查水兼容接口回传给浏览器。
                members.append(
                    {
                        "id": str(row.get("id") or ""),
                        "label": str(row.get("label") or row.get("username") or row.get("id") or ""),
                        "username": str(row.get("username") or ""),
                        "status": str(row.get("status") or "unknown"),
                        "role": str(row.get("role") or ""),
                        "usage": str(row.get("usage") or ""),
                        "base_url": str(row.get("base_url") or ""),
                        "last_error": str(row.get("last_error") or row.get("lastError") or ""),
                    }
                )
            self._send_json(200, {"ok": True, "members": members, "server_time": now_iso(), "disabled": True})
            return

        if u.path == "/api/manual-signal/session":
            doc = read_json_file(self._arsenal_path, {"members": []})
            members_raw = doc.get("members") if isinstance(doc, dict) else []
            members: List[Dict[str, Any]] = [m for m in (members_raw or []) if isinstance(m, dict)]
            selected = find_manual_signal_session(members)

            def sanitize_manual(m: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "id": str(m.get("id") or ""),
                    "label": str(m.get("label") or (m.get("username") or "")),
                    "username": str(m.get("username") or ""),
                    "status": str(m.get("status") or "unknown"),
                    "base_url": str(m.get("base_url") or ""),
                    "uidMasked": _mask_uid(str(m.get("uid") or "")),
                    "ver": str(m.get("ver") or ""),
                    "lastError": str(m.get("last_error") or m.get("lastError") or ""),
                }

            payload: Dict[str, Any] = {"ok": True, "server_time": now_iso(), "member": None}
            if selected:
                payload["member"] = sanitize_manual(selected)
            self._send_json(200, payload)
            return

        if u.path == "/api/chashui/games":
            # 比赛列表查水已移除；空列表响应避免旧页面重试时访问上游。
            qs = parse_qs(u.query)
            mode = _chashui_normalize_mode(str((qs.get("mode") or ["live"])[0]).strip())
            self._send_json(200, {"ok": True, "mode": mode, "games": [], "disabled": True, "server_time": now_iso()})
            return

        if u.path == "/api/chashui/odds":
            # 展示查水已移除，避免任何页面请求绕过 Snapshot watcher 触发上游盘口访问。
            self._send_json(410, {"ok": False, "message": "chashui_disabled", "server_time": now_iso()})
            return

        if u.path == "/api/chashui/capture-event-token":
            self._send_json(410, {"ok": False, "message": "chashui_disabled", "server_time": now_iso()})
            return

            qs = parse_qs(u.query)
            member_id = str((qs.get("member_id") or [""])[0]).strip()
            match_id = str(
                (qs.get("match_id") or qs.get("matchId") or qs.get("mt_id") or qs.get("mtId") or [""])[0]
            ).strip()
            capture = _capture_gismo_token_with_failover(
                arsenal_path=self._arsenal_path,
                preferred_member_id=member_id,
                data_dir=self._data_dir,
                match_id=match_id,
                timeout_s=16,
            )
            result = dict(capture.get("result") or {})
            message = str(result.get("message") or "").strip()
            if (not capture.get("ok")) and str(capture.get("error") or "") == "readonly_pool_empty":
                message = message or ("当前会员会话已失效，请刷新会员状态后重试。" if member_id else "当前没有可用的在线会员会话，无法自动采集比赛事件授权。")
                result["message"] = message
            status = 200 if bool(capture.get("ok")) else (404 if str(capture.get("error") or "") == "readonly_pool_empty" else 502)
            self._send_json(
                status,
                {
                    **result,
                    "readonly_member": _readonly_member_ref(capture.get("member")),
                    "fallback_used": bool(capture.get("fallback_used")),
                    "tokenStored": bool(_gismo_read_feed_token(self._data_dir)),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/chashui/events":
            self._send_json(410, {"ok": False, "message": "chashui_disabled", "server_time": now_iso()})
            return

            qs = parse_qs(u.query)
            member_id = str((qs.get("member_id") or [""])[0]).strip()
            gid = str((qs.get("gid") or [""])[0]).strip()
            match_id = str(
                (qs.get("match_id") or qs.get("matchId") or qs.get("mt_id") or qs.get("mtId") or [""])[0]
            ).strip()
            event_mode = "delta" if str((qs.get("mode") or ["full"])[0]).strip().lower() == "delta" else "full"
            market_mode = _chashui_normalize_mode(str((qs.get("market_mode") or qs.get("marketMode") or ["live"])[0]).strip())

            resolved: Dict[str, Any] = {}
            event_member: Optional[Dict[str, Any]] = None
            doc = read_json_file(self._arsenal_path, {"members": []})
            members_raw = doc.get("members") if isinstance(doc, dict) else []
            members = [m for m in (members_raw or []) if isinstance(m, dict)]
            if not match_id and gid:
                def resolve_match_for_member(member: Dict[str, Any]) -> Dict[str, Any]:
                    base_url = str(member.get("base_url") or "").strip()
                    uid = str(member.get("uid") or "").strip()
                    ver = str(member.get("ver") or "").strip()
                    cookie = str(member.get("cookie") or "").strip()
                    proxy = str(member.get("proxy") or "").strip()
                    if not (base_url and uid and ver and cookie):
                        raise RuntimeError("member_missing_uid_cookie_ver")
                    sess = _make_member_session(base_url=base_url, proxy=proxy, cookie=cookie)
                    return _chashui_resolve_game_by_gid(
                        session=sess,
                        member=member,
                        requested_gid=gid,
                        mode=market_mode,
                        timeout_s=10,
                    )

                resolve_run = _run_readonly_member_with_failover(
                    arsenal_path=self._arsenal_path,
                    members=members,
                    preferred_member_id=member_id,
                    lane="events",
                    runner=resolve_match_for_member,
                )
                if bool(resolve_run.get("ok")):
                    event_member = resolve_run.get("member") if isinstance(resolve_run.get("member"), dict) else None
                    resolved = dict(resolve_run.get("result") or {})
                    match_id = str(resolved.get("mtId") or "").strip()
                else:
                    detail = str(resolve_run.get("error") or "")
                    cached_match_id = _chashui_lookup_event_match_id(self._data_dir, gid=gid)
                    if cached_match_id:
                        match_id = cached_match_id
                        resolved = {
                            "requestedGid": gid,
                            "gid": gid,
                            "mtId": cached_match_id,
                            "mode": market_mode,
                            "cached": True,
                        }
                    else:
                        if detail == "readonly_pool_empty":
                            self._send_json(404, {"ok": False, "message": "当前没有可用的拉数据会员会话，无法解析事件流编号。", "server_time": now_iso()})
                            return
                        msg = "当前赛事不在该会员实时列表中，暂时无法解析事件流编号。" if detail == "game_not_found" else "事件流编号解析失败，请刷新比赛列表后重试。"
                        self._send_json(
                            502,
                            {
                                "ok": False,
                                "message": msg,
                                "detail": f"resolve_match_id_failed: {detail}",
                                "readonly_member": _readonly_member_ref(resolve_run.get("member")),
                                "fallback_used": bool(resolve_run.get("fallback_used")),
                                "server_time": now_iso(),
                            },
                        )
                        return

            if not match_id:
                self._send_json(400, {"ok": False, "message": "当前比赛没有可用的事件流编号，暂时无法读取比赛事件。", "server_time": now_iso()})
                return

            token = _gismo_read_feed_token(self._data_dir)
            capture_result: Optional[Dict[str, Any]] = None
            if not token:
                capture_run = _capture_gismo_token_with_failover(
                    arsenal_path=self._arsenal_path,
                    preferred_member_id=str((event_member or {}).get("id") or member_id),
                    data_dir=self._data_dir,
                    match_id=match_id,
                    members=members,
                    timeout_s=16,
                )
                capture_result = dict(capture_run.get("result") or {})
                if bool(capture_run.get("ok")) and isinstance(capture_run.get("member"), dict):
                    event_member = capture_run.get("member")
                    token = _gismo_read_feed_token(self._data_dir)
                if not token:
                    message = (
                        str(capture_result.get("message") or "")
                        if capture_result
                        else "当前缺少比赛事件源授权，请先采集或配置 BETRADAR_FEED_TOKEN。"
                    )
                    payload: Dict[str, Any] = {"ok": False, "message": message, "server_time": now_iso()}
                    if capture_result and capture_result.get("detail"):
                        payload["detail"] = f"token_capture_failed: {capture_result.get('detail')}"
                    self._send_json(400, payload)
                    return

            try:
                if (not event_member) and member_id:
                    event_member = _select_readonly_member_with_excludes(members, member_id=member_id)
                timeline = _gismo_request_feed(match_id, mode=event_mode, token=token, timeout_s=10)
                _chashui_remember_event_match_id(self._data_dir, gid=gid or str(resolved.get("requestedGid") or ""), match_id=match_id, mode=market_mode)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "timeline": timeline,
                        "meta": {
                            "requestedGid": gid or resolved.get("requestedGid"),
                            "masterGid": resolved.get("gid") or gid or None,
                            "mtId": match_id,
                            "mode": market_mode,
                        },
                        "readonly_member": _readonly_member_ref(event_member),
                        "server_time": now_iso(),
                    },
                )
            except Exception as e:
                detail = str(e)
                if "授权失败" in detail or "BETRADAR_FEED_TOKEN" in detail or "403" in detail:
                    capture_run = _capture_gismo_token_with_failover(
                        arsenal_path=self._arsenal_path,
                        preferred_member_id=str((event_member or {}).get("id") or member_id),
                        data_dir=self._data_dir,
                        match_id=match_id,
                        members=members,
                        timeout_s=16,
                    )
                    capture_result = dict(capture_run.get("result") or {})
                    if bool(capture_run.get("ok")) and isinstance(capture_run.get("member"), dict):
                        event_member = capture_run.get("member")
                    refreshed_token = _gismo_read_feed_token(self._data_dir)
                    if refreshed_token and refreshed_token != token:
                        try:
                            timeline = _gismo_request_feed(match_id, mode=event_mode, token=refreshed_token, timeout_s=10)
                            _chashui_remember_event_match_id(self._data_dir, gid=gid or str(resolved.get("requestedGid") or ""), match_id=match_id, mode=market_mode)
                            self._send_json(
                                200,
                                {
                                    "ok": True,
                                    "timeline": timeline,
                                    "meta": {
                                        "requestedGid": gid or resolved.get("requestedGid"),
                                        "masterGid": resolved.get("gid") or gid or None,
                                        "mtId": match_id,
                                        "mode": market_mode,
                                        "tokenRefreshed": True,
                                    },
                                    "readonly_member": _readonly_member_ref(event_member),
                                    "server_time": now_iso(),
                                },
                            )
                            return
                        except Exception as retry_error:
                            detail = str(retry_error)
                if capture_result and capture_result.get("detail"):
                    self._send_json(502, {"ok": False, "message": detail, "detail": f"token_capture_failed: {capture_result.get('detail')}", "server_time": now_iso()})
                    return
                self._send_json(502, {"ok": False, "message": detail, "server_time": now_iso()})
            return

        if u.path == "/api/arsenal":
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = doc.get("members") or []
            # 会员冷却已废弃；旧数据中的时间戳不应再被 WebUI 展示为冷却中。
            request_cooldown_enabled = False
            platform_state = read_platform_state(self._data_dir)
            out: List[Dict[str, Any]] = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                disp = _proxy_display(m.get("proxy") or "")
                try:
                    ads_sync_interval = max(1, int(m.get("adspower_sync_interval_seconds") or 5))
                except Exception:
                    ads_sync_interval = 5
                member_status = m.get("status") or "unknown"
                member_error = m.get("last_error") or ""
                if is_platform_maintenance(platform_state) or str(platform_state.get("status") or "").strip().lower() == "recovering":
                    recovering = str(platform_state.get("status") or "").strip().lower() == "recovering"
                    member_status = "recovering" if recovering else "maintenance"
                    member_error = (
                        "平台维护结束，正在验证原有会话"
                        if recovering
                        else maintenance_status_message(platform_state)
                    )
                out.append(
                    {
                        "id": m.get("id") or "",
                        "label": m.get("label") or (m.get("username") or ""),
                        "base_url": m.get("base_url") or "",
                        "username": m.get("username") or "",
                        "usage": member_usage_from_row(m),
                        "auto_relogin": bool(m.get("auto_relogin", True)),
                        "has_login_seed": bool(
                            str(m.get("base_url") or "").strip()
                            and str(m.get("username") or "").strip()
                            and str(m.get("password") or "").strip()
                        ),
                        "has_session_seed": bool(
                            str(m.get("uid") or "").strip()
                            and str(m.get("cookie") or "").strip()
                            and str(m.get("ver") or "").strip()
                        ),
                        "manual_login_requested_at": float(m.get("manual_login_requested_at") or 0),
                        "last_ok_at": float(m.get("last_ok_at") or 0),
                        "relogin_fail_count": int(m.get("relogin_fail_count") or 0),
                        "request_fail_count": int(m.get("request_fail_count") or 0),
                        "request_cooldown_until": 0.0,
                        "request_cooldown_enabled": request_cooldown_enabled,
                        "relogin_backoff_until": float(m.get("relogin_backoff_until") or 0),
                        "balance": _to_float_or_none(m.get("maxcredit") or m.get("nowcredit") or m.get("cash")),
                        "maxcredit": m.get("maxcredit"),
                        "nowcredit": m.get("nowcredit"),
                        "cash": m.get("cash"),
                        "session_provider": str(m.get("session_provider") or "native"),
                        "adspower_profile_id": str(m.get("adspower_profile_id") or ""),
                        "adspower_profile_no": str(m.get("adspower_profile_no") or ""),
                        "can_auto_relogin": bool(
                            bool(m.get("auto_relogin", True))
                            and (
                                (
                                    str(m.get("session_provider") or "native").strip().lower() == "adspower"
                                    and (
                                        str(m.get("adspower_profile_id") or "").strip()
                                        or str(m.get("adspower_profile_no") or "").strip()
                                    )
                                )
                                or (
                                    str(m.get("session_provider") or "native").strip().lower() != "adspower"
                                    and str(m.get("base_url") or "").strip()
                                    and str(m.get("username") or "").strip()
                                    and str(m.get("password") or "").strip()
                                )
                            )
                        ),
                        "adspower_auto_start": bool(m.get("adspower_auto_start", True)),
                        "adspower_auto_form_login": bool(m.get("adspower_auto_form_login", True)),
                        "adspower_close_after_sync": bool(m.get("adspower_close_after_sync", True)),
                        "adspower_sync_proxy": bool(m.get("adspower_sync_proxy", True)),
                        "adspower_sync_interval_seconds": ads_sync_interval,
                        "proxy_label": disp.get("label") or "",
                        "proxy_ip": disp.get("proxy_ip") or "",
                        "proxy_geo": disp.get("proxy_geo"),
                        "status": member_status,
                        "last_check_at": float(m.get("last_check_at") or 0),
                        "last_error": member_error,
                        "last_systemtime": m.get("last_systemtime") or "",
                        "platform_status": platform_state.get("status") or "unknown",
                        "maintenance_start_at": float(platform_state.get("maintenance_start_at") or 0),
                        "maintenance_until": float(platform_state.get("maintenance_until") or 0),
                    }
                )
            self._send_json(200, {"ok": True, "members": out, "platform": platform_state, "server_time": now_iso()})
            return

        if u.path == "/api/follow-settings":
            doc = read_json_file(self._follow_settings_path, FOLLOW_SETTINGS_DEFAULTS)
            doc = ensure_follow_settings_defaults(doc)
            doc = merge_order_targets_into_follow_settings(doc, self._orders_dir)
            self._send_json(200, {"ok": True, "settings": doc, "server_time": now_iso()})
            return

        if u.path == "/api/mapping":
            doc = read_json_file(self._mappings_path, {})
            self._send_json(200, doc) # doc is directly the mappings object {mid: target}
            return

        if u.path == "/api/follow-results":
            qs = parse_qs(u.query)
            try:
                requested_limit = int((qs.get("limit") or ["80"])[0])
            except Exception:
                requested_limit = 80
            # 兼容旧版队列页：它会固定请求 1000 条并每秒重复拉取，
            # 返回最近 100 条即可满足旧页面展示，同时避免大 JSON 响应拖慢 WebUI。
            limit = max(1, min(100 if requested_limit >= 1000 else 300, requested_limit))
            path = self._data_dir / "follow_results.jsonl"
            deleted_order_ids = load_deleted_order_ids(self._data_dir)
            try:
                result_stat = path.stat()
                result_revision = (int(result_stat.st_mtime_ns), int(result_stat.st_size))
            except OSError:
                result_revision = (0, 0)
            try:
                deleted_stat = (self._data_dir / "deleted_orders.json").stat()
                deleted_revision = int(deleted_stat.st_mtime_ns)
            except OSError:
                deleted_revision = 0
            results_cache_key = (str(path.resolve()), result_revision[0] ^ deleted_revision, int(limit))
            now_cache = time.monotonic()
            with FOLLOW_RESULTS_HTTP_CACHE_LOCK:
                cached_results = FOLLOW_RESULTS_HTTP_CACHE.get(results_cache_key)
                if cached_results and (now_cache - cached_results[0] <= FOLLOW_HTTP_CACHE_TTL_S):
                    self._send_json_bytes(200, cached_results[1])
                    return

            scan_limit = max(1, int(limit)) * 5
            results = read_jsonl_file(path, limit=scan_limit)
            if deleted_order_ids:
                results = [row for row in results if _extract_order_id_from_row(row) not in deleted_order_ids]
            results = results[: max(1, int(limit))]
            results_body = json.dumps(
                {"ok": True, "results": results, "server_time": now_iso()},
                ensure_ascii=False,
            ).encode("utf-8")
            with FOLLOW_RESULTS_HTTP_CACHE_LOCK:
                FOLLOW_RESULTS_HTTP_CACHE[results_cache_key] = (time.monotonic(), results_body)
                if len(FOLLOW_RESULTS_HTTP_CACHE) > 12:
                    oldest = sorted(FOLLOW_RESULTS_HTTP_CACHE.items(), key=lambda item: item[1][0])[:4]
                    for old_key, _ in oldest:
                        FOLLOW_RESULTS_HTTP_CACHE.pop(old_key, None)
            self._send_json_bytes(200, results_body)
            return

        if u.path == "/api/follow-settlement-status":
            qs = parse_qs(u.query)
            raw_values: List[str] = []
            for key in ("order_ids", "orderIds", "order_id", "orderId"):
                raw_values.extend([str(v) for v in (qs.get(key) or []) if str(v).strip()])

            order_ids: List[str] = []
            seen_ids: set = set()
            for raw in raw_values:
                parts = re.split(r"[,\s]+", str(raw or ""))
                for p in parts:
                    oid = str(p or "").strip()
                    if not oid or oid in seen_ids:
                        continue
                    seen_ids.add(oid)
                    order_ids.append(oid)
                    if len(order_ids) >= 300:
                        break
                if len(order_ids) >= 300:
                    break

            if not order_ids:
                self._send_json(400, {"ok": False, "message": "missing_order_ids", "server_time": now_iso()})
                return

            deleted_order_ids = load_deleted_order_ids(self._data_dir)
            if deleted_order_ids:
                order_ids = [oid for oid in order_ids if oid not in deleted_order_ids]
            if not order_ids:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "statuses": {},
                        "count": 0,
                        "server_time": now_iso(),
                    },
                )
                return

            status_map = build_follow_settlement_status_map(
                self._data_dir,
                order_ids,
                deleted_order_ids=deleted_order_ids,
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "statuses": status_map,
                    "count": len(status_map),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/follow-report-metrics":
            deleted_order_ids = load_deleted_order_ids(self._data_dir)
            metrics = build_follow_report_metrics(
                self._orders_dir,
                self._data_dir,
                deleted_order_ids=deleted_order_ids,
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "metrics": metrics,
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/follow-trace":
            qs = parse_qs(u.query)
            order_id = str((qs.get("orderId") or [""])[0]).strip()
            target = str((qs.get("target") or [""])[0]).strip()
            limit = int((qs.get("limit") or ["2000"])[0])
            if not order_id:
                self._send_json(400, {"ok": False, "message": "missing_orderId", "server_time": now_iso()})
                return
            path = self._data_dir / "follow_results.jsonl"
            rows = read_jsonl_file_filtered(path, order_id=order_id, target=target, limit=limit)
            summary: Dict[str, Any] = {"total": len(rows), "ok": 0, "fail": 0, "errs": {}}
            for r in rows:
                ok = bool(r.get("ok"))
                if ok:
                    summary["ok"] += 1
                else:
                    summary["fail"] += 1
                err = str(r.get("fast_retry_reason") or r.get("last_err") or "")
                if not err:
                    # 尝试从 message 末尾提取 err=XXX
                    msg = str(r.get("message") or "")
                    if "err=" in msg:
                        err = msg.split("err=")[-1].strip().split()[0]
                if err:
                    summary["errs"][err] = int(summary["errs"].get(err) or 0) + 1
            self._send_json(200, {"ok": True, "orderId": order_id, "target": target, "summary": summary, "rows": rows, "server_time": now_iso()})
            return

        if u.path == "/api/follow-trace-download":
            qs = parse_qs(u.query)
            order_id = str((qs.get("orderId") or [""])[0]).strip()
            target = str((qs.get("target") or [""])[0]).strip()
            if not order_id:
                self._send_json(400, {"ok": False, "message": "missing_orderId", "server_time": now_iso()})
                return
            bundle = build_follow_trace_download_bundle(
                data_dir=self._data_dir,
                orders_dir=self._orders_dir,
                order_id=order_id,
                target=target,
            )
            payload = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"follow-trace-{safe_name(order_id)}.json"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if u.path.rstrip("/") == "/api/diagnostics":
            browsers = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
            browsers_exists = False
            if browsers and browsers not in ("0", "1"):
                try:
                    browsers_exists = Path(browsers).is_dir()
                except Exception:
                    browsers_exists = False
            detected = detect_playwright_browsers_path(APP_ROOT)
            core_running, core_pid_count = core_running_flag()
            self._send_json(
                200,
                {
                    "ok": True,
                    "server_time": now_iso(),
                    "cwd": os.getcwd(),
                    "python": sys.executable,
                    "app_root": str(APP_ROOT),
                    "playwright_browsers_path": browsers or "",
                    "playwright_browsers_path_exists": bool(browsers_exists),
                    "playwright_browsers_path_detected": detected or "",
                    "core_running": bool(core_running),
                    "core_pid_count": int(core_pid_count or 0),
                },
            )
            return

        if u.path == "/api/status":
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            # WebUI 可能在 app.py 重启后先收到状态请求；确保已保存的平博账号
            # 仍有独立只读 worker，不需要用户重新点击“接入节点”。
            ps_accounts = [
                item for item in (cfg.get("ps3838_accounts") or [])
                if isinstance(item, dict) and bool(item.get("enabled", True))
            ]
            if ps_accounts:
                try:
                    ensure_ps3838_watch_running(self._config_path, self._data_dir)
                except Exception:
                    pass
            now = time.time()
            platform_state = read_platform_state(self._data_dir)
            statuses: Dict[str, Any] = {}
            for t in targets:
                name = str(t.get("name") or "").strip()
                if not name:
                    continue
                st = self._read_state(name)
                if is_platform_maintenance(platform_state) or str(platform_state.get("status") or "").strip().lower() == "recovering":
                    st = dict(st)
                    recovering = str(platform_state.get("status") or "").strip().lower() == "recovering"
                    st.update(
                        {
                            "platform_status": "recovering" if recovering else "maintenance",
                            "platform_maintenance_start_at": platform_state.get("maintenance_start_at") or 0,
                            "platform_maintenance_until": platform_state.get("maintenance_until") or 0,
                            "last_error_code": PLATFORM_MAINTENANCE_CODE,
                            "last_error_msg": "平台维护结束，正在验证原有会话" if recovering else maintenance_status_message(platform_state),
                        }
                    )
                statuses[name] = build_account_status_entry(t, st, now=now)

            ps_statuses: Dict[str, Any] = {}
            for account in cfg.get("ps3838_accounts") or []:
                if not isinstance(account, dict):
                    continue
                ps_name = str(account.get("name") or "").strip()
                if ps_name:
                    ps_statuses[ps_name] = read_ps3838_status(self._data_dir, ps_name, enabled=bool(account.get("enabled", True)))

            core_running, core_pid_count = core_running_flag()
            follow_running, follow_pid = follow_engine_running_flag()
            snapshot_running, snapshot_pid = snapshot_watch_running_flag()
            server_meta = build_server_file_metadata()
            self._send_json(200, {
                "ok": True,
                "statuses": statuses,
                "ps3838_statuses": ps_statuses,
                "server_time": now_iso(),
                "uptime": int(time.time() - SERVER_START_TIME),
                "latency": get_latency(), 
                "running": bool(follow_running),
                "pid": int(follow_pid) if follow_pid else None,
                "snapshot_watch_running": bool(snapshot_running),
                "snapshot_watch_pid": int(snapshot_pid) if snapshot_pid else None,
                "core_running": bool(core_running),
                "core_pid_count": int(core_pid_count or 0),
                # 用于确认当前运行的 WebUI 版本/路径（避免启动到别的目录的旧代码）
                "server_file": server_meta["server_file"],
                "server_mtime": server_meta["server_mtime"],
                "platform": platform_state,
            })
            return



        return super().do_GET()

    def _local_bridge_endpoint(self) -> str:
        host = str(self.headers.get("Host") or "").strip()
        if not host:
            host = f"127.0.0.1:{int(getattr(self.server, 'server_port', 9999) or 9999)}"
        return f"http://{host}{LOCAL_BRIDGE_PATH}"

    @staticmethod
    def _is_loopback_client(remote: str) -> bool:
        text = str(remote or "").strip().lower()
        if text in {"127.0.0.1", "::1", "localhost"}:
            return True
        if text.startswith("127."):
            return True
        return text.startswith("::ffff:127.")

    def _handle_local_bridge_push(self, data: Dict[str, Any]) -> None:
        cfg = ensure_local_bridge_defaults(self._read_config())
        remote = str((self.client_address or ("", 0))[0] or "")
        if not cfg.get("local_bridge_configured"):
            # Not configured yet: ask the bridge to keep the batch pending so
            # signals are not lost while the user finishes the node setup.
            self._send_json(503, {"ok": False, "message": "local_bridge_not_configured"})
            return
        if not cfg.get("local_bridge_enabled"):
            # Configured but intentionally paused: ack to stop retrying.
            self._send_json(200, {"ok": True, "skipped": True, "message": "local_bridge_disabled"})
            return
        if not bool(cfg.get("local_bridge_allow_remote")) and not self._is_loopback_client(remote):
            self._send_json(403, {"ok": False, "message": "local_bridge_remote_not_allowed"})
            return
        provided = (
            str(self.headers.get("X-HDZZ-Signal-Token") or "").strip()
            or str(self.headers.get("X-Signal-Token") or "").strip()
            or str(data.get("token") or "").strip()
        )
        if not local_bridge_token_matches(str(cfg.get("local_bridge_token") or ""), provided):
            self._send_json(403, {"ok": False, "message": "local_bridge_token_invalid"})
            return

        # ps3838-wss posts {"signals": [one merged group]}; accept a flat object
        # too so manual/older bridge builds keep working.
        raw_signals = data.get("signals") if isinstance(data.get("signals"), list) else None
        signals = raw_signals if raw_signals is not None else [data]
        if not signals:
            self._send_json(400, {"ok": False, "message": "empty_signals"})
            return
        if len(signals) > 100:
            self._send_json(400, {"ok": False, "message": "too_many_signals"})
            return

        results = []
        accepted = 0
        duplicates = 0
        for index, signal in enumerate(signals):
            if not isinstance(signal, dict):
                record_local_bridge_rejection(self._data_dir, "signal_not_object", remote=remote)
                self._send_json(400, {"ok": False, "message": f"signal_{index}_not_object", "results": results})
                return
            try:
                result = receive_local_bridge_signal(
                    data_dir=self._data_dir,
                    orders_dir=self._orders_dir,
                    payload=signal,
                    default_target=str(cfg.get("local_bridge_target") or "ps3838"),
                    remote=remote,
                )
            except LocalBridgeSignalError as exc:
                record_local_bridge_rejection(self._data_dir, str(exc), remote=remote)
                self._send_json(400, {"ok": False, "message": str(exc), "index": index, "results": results})
                return
            except Exception as exc:
                record_local_bridge_rejection(self._data_dir, f"write_failed: {exc}", remote=remote)
                self._send_json(500, {"ok": False, "message": f"local_bridge_write_failed: {exc}", "index": index, "results": results})
                return
            if bool(result.get("duplicate")):
                duplicates += 1
            else:
                accepted += 1
            results.append(
                {
                    "orderId": str(result.get("orderId") or ""),
                    "target": str(result.get("target") or ""),
                    "duplicate": bool(result.get("duplicate")),
                }
            )

        self._send_json(
            200,
            {
                "ok": True,
                "accepted": accepted,
                "duplicates": duplicates,
                "results": results,
                "server_time": now_iso(),
            },
        )

    def _local_bridge_response(self) -> Dict[str, Any]:
        cfg = ensure_local_bridge_defaults(self._read_config())
        state = load_local_bridge_state(self._data_dir)
        return build_local_bridge_config_payload(cfg, state, endpoint=self._local_bridge_endpoint())

    def _read_config(self) -> Dict[str, Any]:
        try:
            raw = self._config_path.read_text(encoding="utf-8-sig")
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        return {"targets": []}

    def _read_state(self, name: str) -> Dict[str, Any]:
        try:
            p = self._state_dir / f"{safe_name(name)}.json"
            if not p.exists():
                return {}
            raw = p.read_text(encoding="utf-8-sig")
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _make_unique_name(self, cfg: Dict[str, Any], base: str) -> str:
        targets = cfg.get("targets") or []
        existing = {str(t.get("name") or "") for t in targets}
        if base not in existing:
            return base
        i = 2
        while True:
            cand = f"{base}-{i}"
            if cand not in existing:
                return cand
            i += 1

    def _queue_manual_relogin(self, name: str) -> Tuple[bool, str]:
        try:
            platform_state = read_platform_state(self._data_dir)
            if is_platform_maintenance(platform_state):
                return False, maintenance_status_message(platform_state)
            p = self._state_dir / f"{safe_name(name)}.json"
            st = read_json_file(p, {})
            if not isinstance(st, dict):
                st = {}
            now = time.time()
            st = prepare_account_manual_relogin_state(st, now=now)
            atomic_write_json(p, st)
            return True, ""
        except Exception as e:
            return False, str(e)

    def _ensure_core_running(self) -> Tuple[bool, bool, str]:
        core_running, _n = core_running_flag()
        if core_running:
            return True, False, ""
        try:
            app_dir = Path(self._config_path).resolve().parent
            app_py = app_dir / "app.py"
            cmd = [sys.executable, "-u", str(app_py), "start-core"]
            env = dict(os.environ)
            apply_playwright_browsers_path(env, APP_ROOT)
            p = subprocess.run(cmd, cwd=str(app_dir), env=env, capture_output=True, text=True, timeout=20)
            if p.returncode in (0, 2):
                return True, True, ""
            err = (p.stderr or p.stdout or "").strip()
            return False, False, (err[:500] or "start-core failed")
        except Exception as e:
            return False, False, f"start-core failed: {e}"

    def do_POST(self) -> None:
        global WATCH_PROCESS
        u = urlparse(self.path)
        if u.path not in (
            "/api/accounts",
            "/api/accounts/relogin",
            "/api/accounts/delete",
            "/api/accounts/update",
            "/api/orders/delete",
            "/api/follow-queue/clear",
            "/api/arsenal",
            "/api/arsenal/delete",
            "/api/arsenal/check",
            "/api/arsenal/login",
            "/api/arsenal/update",
            "/api/proxy-test",
            "/api/follow-settings",
            "/api/telegram-config",
            "/api/telegram-test",
            "/api/telegram-backend-order-test",
            "/api/signal-pull-config",
            "/api/signal-pull-config/delete",
            "/api/local-bridge-config",
            "/api/local-bridge-config/delete",
            "/api/local-signal/push",
            "/api/local-bridge/push",
            "/api/manual-signal/login",
            "/api/manual-signal/preview",
            "/api/manual-signal/push",
            "/api/telegram-signal/send-code",
            "/api/telegram-signal/verify-code",
            "/api/telegram-signal/settings",
            "/api/telegram-signal/start",
            "/api/telegram-signal/stop",
            "/api/telegram-signal/logout",
            "/api/mapping",
            "/api/accounts/add",
            "/api/ps3838/add",
            "/api/ps3838/relogin",
            "/api/ps3838/update",
            "/api/ps3838/delete",
            "/api/start",
            "/api/stop",
            "/api/arsenal/add",
            "/api/arsenal/add-from-ads",
        ):
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("content-length") or "0")
            body = self.rfile.read(length) if length > 0 else b"{}"
            data = json.loads(body.decode("utf-8"))
        except Exception:
            self._send_json(400, {"ok": False, "message": "无效JSON"})
            return

        if u.path in ("/api/local-signal/push", "/api/local-bridge/push"):
            self._handle_local_bridge_push(data if isinstance(data, dict) else {})
            return

        telegram_settings_path = self._data_dir / "telegram_signal.json"
        telegram_secrets_path = self._data_dir / "telegram_signal_secrets.json"
        telegram_pending_path = self._data_dir / "telegram_signal_login_pending.json"

        if u.path == "/api/telegram-signal/send-code":
            try:
                result = send_login_code(telegram_secrets_path, telegram_pending_path, phone=data.get("phone"))
                self._send_json(200, result)
            except TelegramSignalClientError as exc:
                self._send_json(400, {"ok": False, "message": str(exc)})
            except Exception as exc:
                self._send_json(502, {"ok": False, "message": str(exc)})
            return

        if u.path == "/api/telegram-signal/verify-code":
            try:
                result = verify_login_code(
                    telegram_secrets_path,
                    telegram_pending_path,
                    code=data.get("code"),
                    password=data.get("password") or "",
                )
                self._send_json(200, result)
            except TelegramSignalClientError as exc:
                self._send_json(400, {"ok": False, "message": str(exc)})
            except Exception as exc:
                self._send_json(502, {"ok": False, "message": str(exc)})
            return

        if u.path == "/api/telegram-signal/settings":
            allowed = {key: data[key] for key in ("dry_run", "source_chat_ids", "source_chats", "target_name") if key in data}
            current = load_telegram_signal_settings(telegram_settings_path)
            saved = save_telegram_signal_settings(
                telegram_settings_path,
                apply_telegram_signal_settings_patch(current, allowed),
            )
            self._send_json(200, {"ok": True, "settings": saved, "server_time": now_iso()})
            return

        if u.path == "/api/telegram-signal/start":
            secret_status = sanitize_telegram_signal_secret_status(load_telegram_signal_secrets(telegram_secrets_path))
            settings = load_telegram_signal_settings(telegram_settings_path)
            missing = ""
            if not secret_status.get("credentials_configured"):
                missing = "credentials_missing"
            elif not secret_status.get("session_configured"):
                missing = "telegram_login_required"
            elif not settings.get("source_chat_ids"):
                missing = "source_chats_missing"
            if missing:
                self._send_json(409, {"ok": False, "message": missing})
                return
            save_telegram_signal_settings(
                telegram_settings_path,
                apply_telegram_signal_settings_patch(settings, {"enabled": True}),
            )
            rc, output = _run_app_command(["start-telegram-signal"], timeout_s=20)
            if rc in (0, 2):
                self._send_json(200, {"ok": True, "status": "started" if rc == 0 else "already_running"})
            else:
                self._send_json(500, {"ok": False, "message": output[:500] or "start_failed"})
            return

        if u.path == "/api/telegram-signal/stop":
            settings = load_telegram_signal_settings(telegram_settings_path)
            save_telegram_signal_settings(
                telegram_settings_path,
                apply_telegram_signal_settings_patch(settings, {"enabled": False}),
            )
            rc, output = _run_app_command(["stop-telegram-signal", "--force"], timeout_s=20)
            if rc in (0, 1):
                self._send_json(200, {"ok": True, "status": "stopped"})
            else:
                self._send_json(500, {"ok": False, "message": output[:500] or "stop_failed"})
            return

        if u.path == "/api/telegram-signal/logout":
            _run_app_command(["stop-telegram-signal", "--force"], timeout_s=20)
            result = clear_login_session(telegram_secrets_path, telegram_pending_path)
            self._send_json(200, result)
            return

        if u.path == "/api/start":
            # 仅启动跟单引擎和公共 Snapshot watcher；抓单/机器人等保持独立运行。
            snapshot_ok, snapshot_started, snapshot_err, snapshot_pid = ensure_snapshot_watch_running()
            if not snapshot_ok:
                self._send_json(503, {"ok": False, "error": snapshot_err or "snapshot_watch_not_running"})
                return
            follow_running, _pid = follow_engine_running_flag()
            if follow_running:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "status": "already_running",
                        "snapshot_watch_running": True,
                        "snapshot_watch_pid": snapshot_pid,
                    },
                )
                return
            try:
                rc, out = _run_app_command(["start-follow-engine"], timeout_s=20)
                if rc in (0, 2):
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "status": "started",
                            "snapshot_watch_running": True,
                            "snapshot_watch_pid": snapshot_pid,
                        },
                    )
                else:
                    self._send_json(500, {"ok": False, "error": out[:500]})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if u.path == "/api/stop":
            follow_running, _pid = follow_engine_running_flag()
            if not follow_running:
                self._send_json(200, {"ok": True, "status": "not_running"})
                return
            try:
                if is_frozen_runtime():
                    _spawn_app_command(["stop-follow-engine", "--force"])
                    self._send_json(200, {"ok": True, "status": "stopping"})
                    return
                rc1, out1 = _run_app_command(["stop-follow-engine", "--force"], timeout_s=20)
                if rc1 in (0, 1):
                    self._send_json(200, {"ok": True, "status": "stopped"})
                else:
                    self._send_json(500, {"ok": False, "error": out1[:500]})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if u.path == "/api/manual-signal/login":
            try:
                ok, err, member, disabled = prepare_manual_signal_member_login(
                    self._arsenal_path,
                    base_url=data.get("base_url"),
                    username=data.get("username"),
                    password=data.get("password"),
                    backend_name=data.get("backend_name"),
                )
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存会员登录请求失败: {e}"})
                return

            if not ok:
                if err == "missing_base_url_or_username":
                    self._send_json(400, {"ok": False, "message": "缺少会员端网址或会员账号"})
                    return
                if err == "missing_password":
                    self._send_json(400, {"ok": False, "message": "缺少会员密码"})
                    return
                self._send_json(400, {"ok": False, "message": err or "触发会员登录失败"})
                return

            core_ok, core_started, core_err = self._ensure_core_running()
            if not core_ok:
                self._send_json(500, {"ok": False, "message": core_err})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "id": str(member.get("id") or ""),
                    "updated": bool(member.get("updated")),
                    "disabled_old_manual_members": int(disabled or 0),
                    "core_started": bool(core_started),
                    "member": {
                        "id": str(member.get("id") or ""),
                        "label": str(member.get("label") or ""),
                        "username": str(member.get("username") or ""),
                        "base_url": str(member.get("base_url") or ""),
                        "status": str(member.get("status") or "unknown"),
                        "uidMasked": _mask_uid(str(member.get("uid") or "")),
                        "lastError": str(member.get("last_error") or ""),
                    },
                },
            )
            return

        if u.path == "/api/manual-signal/preview":
            try:
                payload = build_manual_signal_payload(data)
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
                return
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"payload build failed: {e}"})
                return
            self._send_json(200, {"ok": True, "payload": payload, "server_time": now_iso()})
            return

        if u.path == "/api/manual-signal/push":
            try:
                cfg = self._read_config()
                result = send_manual_signal(cfg, data)
            except ValueError as e:
                self._send_json(400, {"ok": False, "message": str(e)})
                return
            except Exception as e:
                self._send_json(502, {"ok": False, "message": f"manual_signal_push_failed: {e}"})
                return
            status = 200 if result.get("ok") else 502
            result["server_time"] = now_iso()
            self._send_json(status, result)
            return

        if u.path == "/api/orders/delete":
            order_id = _normalize_order_id(data.get("order_id") or data.get("orderId") or "")
            if not order_id:
                self._send_json(400, {"ok": False, "message": "missing_order_id"})
                return
            try:
                deleted_order_ids = load_deleted_order_ids(self._data_dir)
                already_deleted = order_id in deleted_order_ids
                deleted_order_ids.add(order_id)
                save_deleted_order_ids(self._data_dir, deleted_order_ids)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"delete_order_failed: {e}"})
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "deleted": True,
                    "already_deleted": bool(already_deleted),
                    "order_id": order_id,
                    "count": len(deleted_order_ids),
                },
            )
            return

        if u.path == "/api/ps3838/add":
            base_url = str(data.get("base_url") or "https://www.ps38ag.com").strip().rstrip("/")
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "")
            if not username or not password:
                self._send_json(400, {"ok": False, "message": "请填写平博账号和密码"})
                return
            cfg = self._read_config()
            accounts = cfg.setdefault("ps3838_accounts", [])
            if not isinstance(accounts, list):
                accounts = []
                cfg["ps3838_accounts"] = accounts
            existing = {str(item.get("name") or "") for item in accounts if isinstance(item, dict)}
            base_name = f"ps3838_{safe_name(username)}"
            name = base_name
            suffix = 2
            while name in existing:
                name = f"{base_name}_{suffix}"
                suffix += 1
            try:
                interval = max(5.0, min(300.0, float(data.get("interval_seconds") or 15)))
            except Exception:
                interval = 15.0
            account = {
                "name": name,
                "provider": "ps3838",
                "base_url": base_url or "https://www.ps38ag.com",
                "username": username,
                "password": password,
                "enabled": True,
                "interval_seconds": interval,
                "headless": bool(data.get("headless", False)),
                "login_timeout_seconds": 0,
                "telegram_chat_id": str(data.get("telegram_chat_id") or "").strip(),
                "x_intellifend_secret": "",
            }
            accounts.append(account)
            try:
                atomic_write_json(self._config_path, cfg)
                state_path = self._data_dir / "ps3838" / "state" / f"{safe_name(name)}.json"
                st = read_json_file(state_path, {})
                st.update({"status": "relogin", "status_message": "登录窗口已发起，请完成登录/2FA；窗口不会自动关闭", "relogin_requested_at": time.time(), "relogin_in_progress": True, "session_ready": False, "login_required": False})
                atomic_write_json(state_path, st)
                ok, started, err = ensure_ps3838_watch_running(self._config_path, self._data_dir)
                payload: Dict[str, Any] = {
                    "ok": True,
                    "name": name,
                    "provider": "ps3838",
                    "message": "已发起平博登录，请在弹出的浏览器窗口完成验证码/登录",
                    "watch_started": bool(started),
                }
                if not ok:
                    payload["warning"] = err
                self._send_json(200, payload)
            except Exception as exc:
                self._send_json(500, {"ok": False, "message": f"保存平博配置失败: {exc}"})
            return

        if u.path == "/api/ps3838/relogin":
            name = str(data.get("name") or "").strip()
            cfg = self._read_config()
            account = next((x for x in (cfg.get("ps3838_accounts") or []) if isinstance(x, dict) and str(x.get("name") or "") == name), None)
            if not account:
                self._send_json(404, {"ok": False, "message": "未找到平博账号"})
                return
            state_path = self._data_dir / "ps3838" / "state" / f"{safe_name(name)}.json"
            st = read_json_file(state_path, {})
            if ps3838_login_in_progress(self._data_dir, account, st):
                self._send_json(200, {"ok": True, "provider": "ps3838", "watch_started": False, "message": "登录窗口已打开，请在原窗口完成登录/2FA；不会重新打开或重复提交"})
                return
            st.update({"status": "relogin", "status_message": "等待你在浏览器完成登录/2FA，窗口不会自动关闭", "relogin_requested_at": time.time(), "relogin_in_progress": True, "session_ready": False, "login_required": False})
            try:
                # 点击“重新登录”是明确恢复停用账号的请求。
                account["enabled"] = True
                account["login_timeout_seconds"] = 0
                atomic_write_json(self._config_path, cfg)
                atomic_write_json(state_path, st)
                ok, started, err = ensure_ps3838_watch_running(self._config_path, self._data_dir)
                if not ok:
                    self._send_json(500, {"ok": False, "message": err})
                    return
                self._send_json(200, {"ok": True, "provider": "ps3838", "watch_started": bool(started), "message": "已发起平博重新登录"})
            except Exception as exc:
                self._send_json(500, {"ok": False, "message": f"发起平博登录失败: {exc}"})
            return

        if u.path == "/api/ps3838/update":
            name = str(data.get("name") or "").strip()
            patch = data.get("patch")
            if not isinstance(patch, dict) or not isinstance(patch.get("enabled"), bool):
                self._send_json(400, {"ok": False, "message": "请提供 enabled 布尔值"})
                return
            cfg = self._read_config()
            account = next((item for item in cfg.get("ps3838_accounts") or [] if isinstance(item, dict) and str(item.get("name") or "") == name), None)
            if account is None:
                self._send_json(404, {"ok": False, "message": "未找到平博账号"})
                return
            account["enabled"] = patch["enabled"]
            try:
                atomic_write_json(self._config_path, cfg)
                if account["enabled"]:
                    ok, started, err = ensure_ps3838_watch_running(self._config_path, self._data_dir)
                    if not ok:
                        self._send_json(500, {"ok": False, "message": err})
                        return
                self._send_json(200, {"ok": True, "provider": "ps3838", "enabled": account["enabled"]})
            except Exception as exc:
                self._send_json(500, {"ok": False, "message": f"切换平博监控状态失败: {exc}"})
            return

        if u.path == "/api/ps3838/delete":
            name = str(data.get("name") or "").strip()
            cfg = self._read_config()
            accounts = cfg.get("ps3838_accounts") or []
            kept = [x for x in accounts if not (isinstance(x, dict) and str(x.get("name") or "") == name)]
            if len(kept) == len(accounts):
                self._send_json(404, {"ok": False, "message": "未找到平博账号"})
                return
            cfg["ps3838_accounts"] = kept
            try:
                atomic_write_json(self._config_path, cfg)
                self._send_json(200, {"ok": True, "removed": name, "provider": "ps3838"})
            except Exception as exc:
                self._send_json(500, {"ok": False, "message": f"删除平博账号失败: {exc}"})
            return

        if u.path == "/api/accounts/add":
            base_url = str(data.get("base_url") or "").strip()
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "").strip()
            security_code = str(data.get("security_code") or "").strip()
            tab_raw = data.get("tab")
            if tab_raw is None:
                tab_raw = data.get("relogin_login_tab")

            if not base_url or not username or not password:
                self._send_json(400, {"ok": False, "message": "Missing required fields"})
                return

            cfg = self._read_config()
            cfg.setdefault("targets", [])
            
            # Generate unique name (username based)
            safe_user = safe_name(username)
            name = self._make_unique_name(cfg, safe_user)

            try:
                tab = int(tab_raw) if tab_raw is not None else 3
            except Exception:
                tab = 3
            if tab not in (0, 1, 2, 3):
                tab = 3
            login2_watch_members, login2_watch_downlines = normalize_account_watch_fields(tab, data)
            try:
                time_offset_hours = int(data.get("time_offset_hours") or 0)
            except Exception:
                time_offset_hours = 0
            new_target = build_account_add_target(
                name=name,
                base_url=base_url,
                username=username,
                password=password,
                security_code=security_code,
                tab=tab,
                time_offset_hours=time_offset_hours,
                relogin_min_interval_seconds=data.get("relogin_min_interval_seconds"),
                login2_watch_members=login2_watch_members,
                login2_watch_downlines=login2_watch_downlines,
            )
            
            cfg["targets"].append(new_target)
            
            try:
                atomic_write_json(self._config_path, cfg)
                queued, err = self._queue_manual_relogin(name)
                core_ok, core_started, core_err = self._ensure_core_running()
                watch_ok, watch_started, watch_err = ensure_ag_wmc_watch_running()
                payload: Dict[str, Any] = {
                    "ok": True,
                    "name": name,
                    "relogin_queued": bool(queued),
                    "core_started": bool(core_started),
                    "watch_started": bool(watch_started),
                    "message": ACCOUNT_RELOGIN_PENDING_MSG,
                }
                warns: List[str] = []
                if not queued:
                    warns.append(f"queue relogin failed: {err}")
                if not core_ok:
                    warns.append(core_err)
                if not watch_ok and watch_err:
                    warns.append(watch_err)
                if warns:
                    payload["warning"] = " | ".join(warns)
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"Save failed: {e}"})
            return

        if u.path == "/api/accounts/update":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return

            patch = data.get("patch")
            if not isinstance(patch, dict):
                patch = {}

            # Allow-list fields only (avoid exposing passwords through UI).
            allowed: Dict[str, Any] = {}
            if "telegram_chat_id" in patch:
                allowed["telegram_chat_id"] = str(patch.get("telegram_chat_id") or "").strip()
            if "proxy" in patch:
                allowed["proxy"] = str(patch.get("proxy") or "").strip()
            if "enabled" in patch:
                allowed["enabled"] = bool(patch.get("enabled"))

            if not allowed:
                self._send_json(400, {"ok": False, "message": "no_patch"})
                return

            cfg = self._read_config()
            targets = cfg.get("targets") or []
            updated = False
            for t in targets:
                if not isinstance(t, dict):
                    continue
                if str(t.get("name") or "").strip() != name:
                    continue
                t.update(allowed)
                updated = True
                break

            if not updated:
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return

            cfg["targets"] = targets
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                return

            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/accounts/delete":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            before = len(targets)
            targets = [t for t in targets if str(t.get("name") or "") != name]
            if len(targets) == before:
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return
            cfg["targets"] = targets
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return
            self._send_json(200, {"ok": True, "removed": name})
            return

        if u.path == "/api/mapping":
            member_id = str(data.get("member_id") or "").strip()
            target = str(data.get("target") or "").strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "missing member_id"})
                return
            
            doc = read_json_file(self._mappings_path, {})
            if not isinstance(doc, dict):
                doc = {}
            
            if target:
                doc[member_id] = target
            else:
                # empty target means remove mapping (follow all)
                doc.pop(member_id, None)
            
            try:
                atomic_write_json(self._mappings_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"save failed: {e}"})
                return
            
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/accounts/relogin":
            name = str((data.get("name") or "")).strip()
            if not name:
                self._send_json(400, {"ok": False, "message": "缺少 name"})
                return
            cfg = self._read_config()
            targets = cfg.get("targets") or []
            if not any(str(t.get("name") or "") == name for t in targets):
                self._send_json(404, {"ok": False, "message": "未找到该账号"})
                return
            platform_state = read_platform_state(self._data_dir)
            if is_platform_maintenance(platform_state):
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "status": "maintenance",
                        "message": maintenance_status_message(platform_state),
                        "platform": platform_state,
                    },
                )
                return

            # 若 core worker 未启动，则“重登”只会写 state，不会被执行（没有进程去消费）。
            # 这里 best-effort 拉起 core，避免用户误以为“点了没反应”。
            core_ok, core_started, core_err = self._ensure_core_running()
            if not core_ok:
                self._send_json(500, {"ok": False, "message": core_err})
                return
            watch_ok, watch_started, watch_err = ensure_ag_wmc_watch_running()
            if not watch_ok:
                self._send_json(500, {"ok": False, "message": watch_err})
                return

            # 通过 state 文件给 ag_wmc_watch 发信号：下一轮强制弹浏览器登录一次（你可手动点验证码）。
            try:
                p = self._state_dir / f"{safe_name(name)}.json"
                st = read_json_file(p, {})
                if not isinstance(st, dict):
                    st = {}
                # 绕过最小间隔：用户点了就希望立刻弹窗登录
                st = prepare_account_manual_relogin_state(st)
                atomic_write_json(p, st)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入 state 失败: {e}"})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "name": name,
                    "core_started": bool(core_started),
                    "watch_started": bool(watch_started),
                    "message": ACCOUNT_RELOGIN_PENDING_MSG,
                },
            )
            return

        if u.path == "/api/arsenal/delete":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "缺少 id"})
                return
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            before = len(members)
            members = [m for m in members if str(m.get("id") or "") != member_id]
            if len(members) == before:
                self._send_json(404, {"ok": False, "message": "未找到该会员账号"})
                return
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
                # Remove the deleted member's optional target mapping from the
                # same runtime data directory.  Orders and member browser
                # profiles are intentionally left untouched.
                mappings = read_json_file(self._mappings_path, {})
                if member_id in mappings:
                    mappings.pop(member_id, None)
                    atomic_write_json(self._mappings_path, mappings)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return
            self._send_json(200, {"ok": True, "removed": member_id})
            return

        if u.path == "/api/arsenal/add-from-ads":
            profile_id = str(data.get("profile_id") or "").strip()
            profile_no = str(data.get("profile_no") or "").strip()
            api_base = normalize_adspower_api_base(str(data.get("api_base") or DEFAULT_ADSPOWER_API_BASE))
            api_key = str(data.get("api_key") or "").strip()
            if not (profile_id or profile_no):
                self._send_json(400, {"ok": False, "message": "missing profile_id/profile_no"})
                return

            profile, err = adspower_find_profile(
                api_base=api_base,
                profile_id=profile_id,
                profile_no=profile_no,
                api_key=api_key,
                timeout_s=12,
            )
            if err and not profile:
                status_code = 404 if err == "profile_not_found" else 502
                self._send_json(status_code, {"ok": False, "message": err})
                return
            ads_row = _adspower_profile_to_payload(profile if isinstance(profile, dict) else {})
            if not ads_row.get("profile_id"):
                ads_row["profile_id"] = profile_id
            if not ads_row.get("profile_no"):
                ads_row["profile_no"] = profile_no

            label = str(data.get("label") or "").strip()
            base_url = normalize_member_base_url(data.get("base_url"))
            sync_proxy = bool(data.get("sync_proxy", True))
            if not label:
                label = str(ads_row.get("name") or "").strip()
            if not label:
                label = f"ads-{ads_row.get('profile_no') or ads_row.get('profile_id') or 'member'}"
            if not base_url:
                base_url = normalize_member_base_url(ads_row.get("base_url_hint"))
            proxy = _normalize_proxy_input(str(ads_row.get("proxy") or "")) if sync_proxy else ""

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = doc.get("members")
            if not isinstance(members, list):
                members = []
                doc["members"] = members

            def _same_ads_member(x: Any) -> bool:
                if not isinstance(x, dict):
                    return False
                if str(x.get("session_provider") or "").strip().lower() != "adspower":
                    return False
                xid = str(x.get("adspower_profile_id") or "").strip()
                xno = str(x.get("adspower_profile_no") or "").strip()
                if profile_id and xid and profile_id == xid:
                    return True
                if profile_no and xno and profile_no == xno:
                    return True
                return False

            existing = next((m for m in members if _same_ads_member(m)), None)
            if isinstance(existing, dict):
                existing["label"] = label
                if base_url:
                    existing["base_url"] = base_url
                existing["username"] = str(ads_row.get("name") or existing.get("username") or "").strip()
                existing["session_provider"] = "adspower"
                existing["adspower_profile_id"] = str(ads_row.get("profile_id") or profile_id or "").strip()
                existing["adspower_profile_no"] = str(ads_row.get("profile_no") or profile_no or "").strip()
                existing["adspower_api_base"] = api_base
                if api_key:
                    existing["adspower_api_key"] = api_key
                existing["adspower_auto_start"] = True
                existing["adspower_auto_form_login"] = bool(existing.get("adspower_auto_form_login", True))
                existing["adspower_close_after_sync"] = bool(existing.get("adspower_close_after_sync", True))
                existing["adspower_sync_proxy"] = bool(sync_proxy)
                existing["adspower_last_sync_at"] = 0
                existing["adspower_sync_interval_seconds"] = max(1, int(existing.get("adspower_sync_interval_seconds") or 5))
                if sync_proxy and proxy:
                    existing["proxy"] = proxy
                existing["auto_relogin"] = True
                existing["status"] = "relogin"
                existing["last_relogin_at"] = 0
                existing["last_error"] = ""
                existing["updated_at"] = time.time()
                try:
                    atomic_write_json(self._arsenal_path, doc)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "id": str(existing.get("id") or ""),
                            "updated": True,
                            "member": {
                                "label": existing.get("label") or "",
                                "session_provider": "adspower",
                                "adspower_profile_id": existing.get("adspower_profile_id") or "",
                                "adspower_profile_no": existing.get("adspower_profile_no") or "",
                            },
                        },
                    )
                except Exception as e:
                    self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                return

            mid = uuid.uuid4().hex
            new_member = {
                "id": mid,
                "label": label,
                "base_url": base_url,
                "username": str(ads_row.get("name") or "").strip(),
                "password": "",
                "proxy": proxy,
                "uid": "",
                "mid": "",
                "cookie": "",
                "ver": "",
                "status": "relogin",
                "last_check_at": 0,
                "last_ok_at": 0,
                "last_relogin_at": 0,
                "last_error": "",
                "last_systemtime": "",
                "auto_relogin": True,
                "relogin_headless": True,
                "relogin_login_tab": 3,
                "relogin_timeout_seconds": 30,
                "relogin_min_interval_seconds": 5,
                "currency": "",
                "cash": "",
                "maxcredit": "",
                "balance_updated_at": 0,
                "session_provider": "adspower",
                "adspower_profile_id": str(ads_row.get("profile_id") or profile_id or "").strip(),
                "adspower_profile_no": str(ads_row.get("profile_no") or profile_no or "").strip(),
                "adspower_api_base": api_base,
                "adspower_api_key": api_key,
                "adspower_auto_start": True,
                "adspower_auto_form_login": True,
                "adspower_close_after_sync": True,
                "adspower_sync_proxy": bool(sync_proxy),
                "adspower_last_sync_at": 0,
                "adspower_sync_interval_seconds": 5,
                "added_at": time.time(),
            }
            members.append(new_member)
            try:
                atomic_write_json(self._arsenal_path, doc)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "id": mid,
                        "updated": False,
                        "member": {
                            "label": new_member.get("label") or "",
                            "session_provider": "adspower",
                            "adspower_profile_id": new_member.get("adspower_profile_id") or "",
                            "adspower_profile_no": new_member.get("adspower_profile_no") or "",
                        },
                    },
                )
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
            return

        if u.path == "/api/arsenal/add":
            label = str(data.get("label") or "").strip()
            base_url = normalize_member_base_url(data.get("base_url"))
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "").strip()
            if not base_url or not username:
                self._send_json(400, {"ok": False, "message": "缺少 base_url 或 username"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = doc.get("members")
            if not isinstance(members, list):
                members = []
                doc["members"] = members

            uid = str(data.get("uid") or "").strip()
            cookie = str(data.get("cookie") or "").strip()
            proxy = str(data.get("proxy") or "").strip()
            usage = _normalize_member_usage(data.get("usage"))

            def _same_member(x: Any) -> bool:
                if not isinstance(x, dict):
                    return False
                x_base = normalize_member_base_url(x.get("base_url"))
                x_user = str(x.get("username") or "").strip()
                return (x_base == base_url) and (x_user == username)

            existing = next((m for m in members if _same_member(m)), None)
            if isinstance(existing, dict):
                if label:
                    existing["label"] = label
                if proxy:
                    existing["proxy"] = proxy
                if uid:
                    existing["uid"] = uid
                if cookie:
                    existing["cookie"] = cookie
                if password:
                    existing["password"] = password
                    if not (cookie or str(existing.get("cookie") or "").strip()):
                        existing["status"] = "relogin"
                        existing["last_relogin_at"] = 0
                        existing["last_error"] = ""
                apply_member_usage_patch(existing, usage)

                existing["base_url"] = base_url
                existing["username"] = username
                existing["updated_at"] = time.time()
                try:
                    atomic_write_json(self._arsenal_path, doc)
                    self._send_json(200, {"ok": True, "id": str(existing.get("id") or ""), "updated": True})
                except Exception as e:
                    self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                return

            # 兼容迁移：如果用户之前误把会员 base_url 填成后台域名（如 ag.hga030.com），
            # 允许用“同 username”覆盖更新 base_url（前提：旧记录还没形成有效会话，避免误改已在线账号）。
            def _same_username(x: Any) -> bool:
                if not isinstance(x, dict):
                    return False
                return str(x.get("username") or "").strip() == username

            existing_user = next((m for m in members if _same_username(m)), None)
            if isinstance(existing_user, dict):
                has_session = bool(str(existing_user.get("uid") or "").strip()) and bool(str(existing_user.get("cookie") or "").strip()) and bool(str(existing_user.get("ver") or "").strip())
                if not has_session:
                    existing_user["base_url"] = base_url
                    if label:
                        existing_user["label"] = label
                    if proxy:
                        existing_user["proxy"] = proxy
                    if uid:
                        existing_user["uid"] = uid
                    if cookie:
                        existing_user["cookie"] = cookie
                    if password:
                        existing_user["password"] = password
                    apply_member_usage_patch(existing_user, usage)
                    existing_user["status"] = ("online" if has_session else ("relogin" if (password and not cookie) else "unknown"))
                    existing_user["last_relogin_at"] = 0
                    existing_user["last_error"] = ""
                    existing_user["updated_at"] = time.time()
                    try:
                        atomic_write_json(self._arsenal_path, doc)
                        self._send_json(200, {"ok": True, "id": str(existing_user.get("id") or ""), "updated": True, "migrated_base_url": True})
                    except Exception as e:
                        self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
                    return

            mid = uuid.uuid4().hex

            new_member = {
                "id": mid,
                "label": label or username,
                "base_url": base_url,
                "username": username,
                "password": password,
                "proxy": proxy,
                "usage": usage,
                "role": "quote" if usage == "read" else "",
                "uid": uid,
                "mid": "",
                "cookie": cookie,
                "ver": "",
                "status": ("relogin" if (password and not cookie) else "unknown"),
                "last_check_at": 0,
                "last_ok_at": 0,
                "last_relogin_at": 0,
                "last_error": "",
                "last_systemtime": "",
                "auto_relogin": True,
                "relogin_headless": True,
                "relogin_login_tab": 3,
                "relogin_timeout_seconds": 30,
                "relogin_min_interval_seconds": 5,
                "currency": "",
                "cash": "",
                "maxcredit": "",
                "balance_updated_at": 0,
                "session_provider": "native",
                "adspower_profile_id": "",
                "adspower_profile_no": "",
                "adspower_api_base": DEFAULT_ADSPOWER_API_BASE,
                "adspower_api_key": "",
                "adspower_auto_start": True,
                "adspower_auto_form_login": True,
                "adspower_close_after_sync": True,
                "adspower_sync_proxy": True,
                "adspower_last_sync_at": 0,
                "adspower_sync_interval_seconds": 5,
                "added_at": time.time(),
            }
            members.append(new_member)

            try:
                atomic_write_json(self._arsenal_path, doc)
                self._send_json(200, {"ok": True, "id": mid, "updated": False})
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"保存失败: {e}"})
            return

        if u.path == "/api/arsenal/update":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "missing id"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            m = next((x for x in members if str(x.get("id") or "") == member_id), None)
            if not m:
                self._send_json(404, {"ok": False, "message": "member not found"})
                return

            patch: Dict[str, Any] = {}
            for k in ("label", "base_url", "username", "password", "proxy", "uid", "cookie", "ver"):
                if k in data:
                    patch[k] = str(data.get(k) or "").strip()
            if "base_url" in patch:
                patch["base_url"] = normalize_member_base_url(patch.get("base_url"))
            if "usage" in data:
                apply_member_usage_patch(patch, data.get("usage"))
            if "session_provider" in data:
                patch["session_provider"] = (str(data.get("session_provider") or "native").strip().lower() or "native")
            for k in ("adspower_profile_id", "adspower_profile_no", "adspower_api_base", "adspower_api_key"):
                if k in data:
                    patch[k] = str(data.get(k) or "").strip()
            if "adspower_auto_start" in data:
                patch["adspower_auto_start"] = bool(data.get("adspower_auto_start"))
            if "adspower_auto_form_login" in data:
                patch["adspower_auto_form_login"] = bool(data.get("adspower_auto_form_login"))
            if "adspower_close_after_sync" in data:
                patch["adspower_close_after_sync"] = bool(data.get("adspower_close_after_sync"))
            if "adspower_sync_proxy" in data:
                patch["adspower_sync_proxy"] = bool(data.get("adspower_sync_proxy"))
            if "adspower_sync_interval_seconds" in data:
                try:
                    patch["adspower_sync_interval_seconds"] = max(1, int(data.get("adspower_sync_interval_seconds") or 5))
                except Exception:
                    patch["adspower_sync_interval_seconds"] = 5
            if "adspower_api_base" in patch:
                patch["adspower_api_base"] = normalize_adspower_api_base(str(patch.get("adspower_api_base") or DEFAULT_ADSPOWER_API_BASE))

            if patch.get("password") and not (patch.get("cookie") or str(m.get("cookie") or "").strip()):
                patch.setdefault("status", "relogin")
                patch.setdefault("last_relogin_at", 0)
                patch.setdefault("last_error", "")

            m.update(patch)
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"save failed: {e}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/arsenal/check":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "缺少 id"})
                return
            doc = read_json_file(self._arsenal_path, {"members": []})
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            m = next((x for x in members if str(x.get("id") or "") == member_id), None)
            if not m:
                self._send_json(404, {"ok": False, "message": "未找到该会员账号"})
                return

            platform_state = read_platform_state(self._data_dir)
            if is_platform_maintenance(platform_state):
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "status": "maintenance",
                        "message": maintenance_status_message(platform_state),
                        "platform": platform_state,
                    },
                )
                return

            base_url = normalize_member_base_url(m.get("base_url"))
            username = str(m.get("username") or "").strip()
            password = str(m.get("password") or "").strip()
            session_provider = str(m.get("session_provider") or "native").strip().lower() or "native"
            ver = str(m.get("ver") or "").strip()
            uid = str(m.get("uid") or "").strip()
            cookie = str(m.get("cookie") or "").strip()
            proxy_raw = str(m.get("proxy") or "").strip()
            proxy = _normalize_proxy_input(proxy_raw)
            if proxy and proxy_raw != proxy:
                m["proxy"] = proxy

            m["last_check_at"] = time.time()

            if not (base_url and ver and uid and cookie):
                core_running, _core_pid_count = core_running_flag()
                if session_provider == "adspower":
                    has_profile = bool(str(m.get("adspower_profile_id") or "").strip() or str(m.get("adspower_profile_no") or "").strip())
                    if has_profile:
                        if core_running:
                            m["status"] = "relogin"
                            m["last_error"] = "等待后台自动同步 AdsPower 会话"
                            m["last_relogin_at"] = 0
                        else:
                            m["status"] = "unknown"
                            m["last_error"] = "核心后台未启动：请先点击右上角“启动”"
                    else:
                        m["status"] = "unknown"
                        m["last_error"] = "缺少 AdsPower profile_id/profile_no：请先从 Ads 环境导入"
                elif base_url and username and password:
                    if core_running:
                        m["status"] = "relogin"
                        m["last_error"] = "等待后台自动登录刷新会话"
                        m["last_relogin_at"] = 0
                    else:
                        m["status"] = "unknown"
                        m["last_error"] = "核心后台未启动：请先点击右上角“启动”，再自动登录刷新会话"
                else:
                    m["status"] = "unknown"
                    if not base_url:
                        m["last_error"] = "缺少 base_url"
                    elif not username:
                        m["last_error"] = "缺少 username"
                    else:
                        m["last_error"] = "缺少登录密码：请在“添加会员”里填写密码（或填入 uid/cookie/ver）"
                try:
                    atomic_write_json(self._arsenal_path, doc)
                except Exception:
                    pass
                self._send_json(200, {"ok": True, "status": m["status"], "message": m["last_error"]})
                return

            # WebUI 手动“检查”应尽量快返回；但走代理时网络抖动更常见，超时过短会误判离线
            timeout_s = 6 if not proxy else 15
            res = member_ping_system_time(base_url=base_url, ver=ver, uid=uid, cookie=cookie, proxy=proxy, timeout_s=timeout_s)
            if res.get("ok"):
                m["status"] = "online"
                m["last_error"] = ""
                m["last_systemtime"] = res.get("systemtime") or ""
            else:
                err = str(res.get("error") or res.get("code") or "offline")
                # 对“刚刚还在线”的会员：若只是短暂超时/代理抖动，不要立刻把状态从在线打成离线（避免 UI 抖动）
                try:
                    last_ok_at = float(m.get("last_ok_at") or 0)
                except Exception:
                    last_ok_at = 0.0
                now2 = time.time()
                if str(m.get("status") or "") == "online" and last_ok_at and (now2 - last_ok_at) <= 180 and err.startswith("request_failed:"):
                    m["last_error"] = err
                else:
                    m["status"] = "offline"
                    m["last_error"] = err

            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "status": m.get("status") or "unknown",
                    "systemtime": m.get("last_systemtime") or "",
                    "message": m.get("last_error") or "",
                },
            )
            return

        if u.path == "/api/arsenal/login":
            member_id = str((data.get("id") or "")).strip()
            if not member_id:
                self._send_json(400, {"ok": False, "message": "缺少 id"})
                return

            platform_state = read_platform_state(self._data_dir)
            if is_platform_maintenance(platform_state):
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "status": "maintenance",
                        "message": maintenance_status_message(platform_state),
                        "platform": platform_state,
                    },
                )
                return

            core_ok, core_started, core_err = self._ensure_core_running()
            if not core_ok:
                self._send_json(500, {"ok": False, "message": core_err})
                return

            try:
                ok, err = queue_arsenal_manual_login(self._arsenal_path, member_id)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入会员登录请求失败: {e}"})
                return

            if not ok:
                if err == "member_not_found":
                    self._send_json(404, {"ok": False, "message": "未找到该会员账号"})
                    return
                if err == "login_credentials_missing":
                    self._send_json(400, {"ok": False, "message": "缺少登录信息，无法触发会员登录"})
                    return
                self._send_json(400, {"ok": False, "message": err or "触发会员登录失败"})
                return

            self._send_json(200, {"ok": True, "id": member_id, "core_started": bool(core_started)})
            return

        if u.path == "/api/follow-queue/clear":
            # 只清“排队中的订单”（pending/jobs），不清历史 done / results
            cmd_path = self._data_dir / "follow_queue_cmd.json"
            req_at = float(time.time())
            try:
                cmd = read_json_file(cmd_path, {})
                if not isinstance(cmd, dict):
                    cmd = {}
                cmd["clear_pending_requested_at"] = req_at
                cmd["clear_pending_requested_by"] = "webui"
                cmd["updated_at"] = now_iso()
                atomic_write_json(cmd_path, cmd)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入清空指令失败: {e}"})
                return

            try:
                clear_result = _clear_follow_queue_state(self._data_dir, req_at=req_at)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"清空队列状态失败: {e}"})
                return

            self._send_json(
                200,
                {
                    "ok": True,
                    "requested_at": req_at,
                    "state_cleared": True,
                    **clear_result,
                },
            )
            return

        if u.path == "/api/follow-settings":
            # 兼容两种前端 payload：
            # 1) { "settings": {...} }（推荐）
            # 2) 直接提交 settings 对象（旧前端/误提交）
            settings = data.get("settings") if isinstance(data, dict) else None
            if not isinstance(settings, dict) and isinstance(data, dict):
                settings = data
            if not isinstance(settings, dict):
                self._send_json(400, {"ok": False, "message": "缺少 settings"})
                return

            doc = read_json_file(self._follow_settings_path, FOLLOW_SETTINGS_DEFAULTS)
            doc = ensure_follow_settings_defaults(doc)
            doc = merge_follow_settings(doc, settings)
            doc = merge_order_targets_into_follow_settings(doc, self._orders_dir)
            doc["updated_at"] = now_iso()

            try:
                atomic_write_json(self._follow_settings_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return
            self._send_json(200, {"ok": True, "settings": doc})
            return

        if u.path == "/api/telegram-config":
            cfg = self._read_config()
            cfg = apply_telegram_config_update(cfg, data)
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return
            bt = str(cfg.get("telegram_bot_token") or "").strip()
            cid = str(cfg.get("telegram_chat_id") or "").strip()
            allow_chat2 = str(cfg.get("telegram_allow_chat") or "").strip()
            backend_token = str(cfg.get("telegram_backend_order_bot_token") or "").strip()
            backend_chat_id = str(cfg.get("telegram_backend_order_chat_id") or "").strip()
            self._send_json(
                200,
                {
                    "ok": True,
                    "configured": bool(bt and cid),
                    "token_mask": mask_secret(bt, keep=4) if bt else "",
                    "chat_id": cid,
                    "allow_chat": allow_chat2,
                    "proxy": str(cfg.get("telegram_proxy") or "").strip(),
                    "order_forward_enabled": bool(cfg.get("telegram_order_forward_enabled", True)),
                    "backend_order_push_enabled": bool(cfg.get("telegram_backend_order_push_enabled") or False),
                    "backend_order_configured": bool(backend_token and backend_chat_id),
                    "backend_order_token_mask": mask_secret(backend_token, keep=4) if backend_token else "",
                    "backend_order_chat_id": backend_chat_id,
                    "backend_order_proxy": str(cfg.get("telegram_backend_order_proxy") or "").strip(),
                },
            )
            return

        if u.path == "/api/signal-pull-config":
            cfg = self._read_config()
            cfg = merge_signal_pull_config(cfg, data)
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return

            cfg = ensure_signal_pull_config_defaults(cfg)
            self._send_json(
                200,
                {
                    "ok": True,
                    "config": {
                        "signal_pull_enabled": bool(cfg.get("signal_pull_enabled")),
                        "signal_pull_url": str(cfg.get("signal_pull_url") or "").strip(),
                        "signal_pull_token": str(cfg.get("signal_pull_token") or "").strip(),
                        "signal_pull_live_only": bool(cfg.get("signal_pull_live_only")),
                        "signal_pull_limit": int(cfg.get("signal_pull_limit") or 200),
                        "signal_pull_interval_seconds": float(cfg.get("signal_pull_interval_seconds") or 1.0),
                    },
                    "configured": bool(str(cfg.get("signal_pull_url") or "").strip()),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/local-bridge-config":
            cfg = ensure_local_bridge_defaults(self._read_config())
            cfg = merge_local_bridge_config(cfg, data)
            target = str(cfg.get("local_bridge_target") or "").strip()
            if cfg.get("local_bridge_enabled") and not target:
                self._send_json(400, {"ok": False, "message": "请填写本地桥接的订单目标"})
                return
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return

            # Make the bridge target a followable source immediately, even
            # before the first signal creates orders/<target>.jsonl.
            if cfg.get("local_bridge_configured") and target:
                try:
                    doc = read_json_file(self._follow_settings_path, FOLLOW_SETTINGS_DEFAULTS)
                    doc = ensure_follow_settings_defaults(doc)
                    doc = merge_order_targets_into_follow_settings(doc, self._orders_dir)
                    targets = doc.setdefault("targets", [])
                    if not isinstance(targets, list):
                        targets = []
                        doc["targets"] = targets
                    if not any(
                        isinstance(item, dict) and str(item.get("name") or "").strip() == target
                        for item in targets
                    ):
                        targets.append({"name": target, "enabled": True, "only_live": False, "ratio_percent": None})
                        doc["updated_at"] = now_iso()
                    atomic_write_json(self._follow_settings_path, doc)
                except Exception:
                    # The bridge still ingests orders if follow settings are
                    # temporarily unavailable; the next save retries this.
                    pass

            self._send_json(200, self._local_bridge_response())
            return

        if u.path == "/api/local-bridge-config/delete":
            cfg = reset_local_bridge_config(self._read_config())
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return
            self._send_json(200, self._local_bridge_response())
            return

        if u.path == "/api/signal-pull-config/delete":
            cfg = self._read_config()
            cfg = reset_signal_pull_config(cfg)
            try:
                atomic_write_json(self._config_path, cfg)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
                return

            cfg = ensure_signal_pull_config_defaults(cfg)
            self._send_json(
                200,
                {
                    "ok": True,
                    "config": {
                        "signal_pull_enabled": bool(cfg.get("signal_pull_enabled")),
                        "signal_pull_url": str(cfg.get("signal_pull_url") or "").strip(),
                        "signal_pull_token": str(cfg.get("signal_pull_token") or "").strip(),
                        "signal_pull_live_only": bool(cfg.get("signal_pull_live_only")),
                        "signal_pull_limit": int(cfg.get("signal_pull_limit") or 200),
                        "signal_pull_interval_seconds": float(cfg.get("signal_pull_interval_seconds") or 1.0),
                    },
                    "configured": bool(str(cfg.get("signal_pull_url") or "").strip()),
                    "server_time": now_iso(),
                },
            )
            return

        if u.path == "/api/telegram-backend-order-test":
            cfg = self._read_config()
            bot_token = str(data.get("bot_token") or cfg.get("telegram_backend_order_bot_token") or "").strip()
            chat_id = str(data.get("chat_id") or cfg.get("telegram_backend_order_chat_id") or "").strip()
            proxy = str(data.get("proxy") or cfg.get("telegram_backend_order_proxy") or "").strip()
            msg = str(data.get("message") or "").strip()
            if not msg:
                msg = format_backend_order_telegram_test_message(cfg)
            if not (bot_token and chat_id):
                self._send_json(400, {"ok": False, "message": "请先配置后台订单 Bot Token 和群 Chat ID"})
                return
            res = tg_send_message(bot_token=bot_token, chat_id=chat_id, text=msg, proxy=proxy, timeout_s=15)
            if not res.get("ok"):
                self._send_json(500, {"ok": False, "message": f"发送失败: {redact_secret_in_text(str(res.get('error') or ''), bot_token)}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/telegram-test":
            cfg = self._read_config()
            # 允许测试时使用前端“当前输入值”（未保存也能测试），否则回退到 config.json
            bot_token = str(data.get("bot_token") or cfg.get("telegram_bot_token") or "").strip()
            chat_id = str(data.get("chat_id") or cfg.get("telegram_chat_id") or "").strip()
            proxy = str(data.get("proxy") or cfg.get("telegram_proxy") or "").strip()
            msg = str(data.get("message") or "测试消息：TG 推送正常").strip()
            if not (bot_token and chat_id):
                self._send_json(400, {"ok": False, "message": "请先在 Telegram 页面配置 bot_token + chat_id"})
                return
            res = tg_send_message(bot_token=bot_token, chat_id=chat_id, text=msg, proxy=proxy, timeout_s=15)
            if not res.get("ok"):
                self._send_json(500, {"ok": False, "message": f"发送失败: {redact_secret_in_text(str(res.get('error') or ''), bot_token)}"})
                return
            self._send_json(200, {"ok": True})
            return

        if u.path == "/api/proxy-test":
            proxy = str(data.get("proxy") or "").strip()
            timeout_s = int(data.get("timeout_s") or 10)
            base_url = str(data.get("base_url") or "").strip()
            if not proxy:
                self._send_json(200, {"ok": False, "message": "缺少 proxy"})
                return
            res = proxy_test(proxy=proxy, timeout_s=timeout_s, base_url=base_url)
            if not res.get("ok"):
                self._send_json(200, {"ok": False, "message": str(res.get("error") or "proxy_test_failed")})
                return
            self._send_json(200, res)
            return

        if u.path == "/api/arsenal":
            base_url = normalize_member_base_url(data.get("base_url"))
            ver = str((data.get("ver") or "")).strip()
            username = str((data.get("username") or "")).strip()
            password = str((data.get("password") or "")).strip()
            proxy = _normalize_proxy_input(str((data.get("proxy") or "")).strip())
            uid = str((data.get("uid") or "")).strip()
            mid = str((data.get("mid") or "")).strip()
            cookie = str((data.get("cookie") or "")).strip()
            label = str((data.get("label") or "")).strip() or username

            # ver/uid/cookie 可后续由 member_arsenal_watch.py 自动登录补齐
            if not base_url or not username or not password:
                self._send_json(400, {"ok": False, "message": "缺少 base_url/username/password"})
                return

            doc = read_json_file(self._arsenal_path, {"members": []})
            doc.setdefault("members", [])
            members = [m for m in (doc.get("members") or []) if isinstance(m, dict)]
            member_id = uuid.uuid4().hex
            members.append(
                {
                    "id": member_id,
                    "label": label,
                    "base_url": base_url,
                    "ver": ver,
                    "username": username,
                    "password": password,
                    "proxy": proxy,
                    "uid": uid,
                    "mid": mid,
                    "cookie": cookie,
                    "status": "unknown",
                    "last_check_at": 0,
                    "last_ok_at": 0,
                    "last_relogin_at": 0,
                    "last_error": "",
                    "last_systemtime": "",
                    "auto_relogin": True,
                    "relogin_headless": True,
                    "relogin_timeout_seconds": 30,
                    "relogin_min_interval_seconds": 5,
                    "session_provider": "native",
                    "adspower_profile_id": "",
                    "adspower_profile_no": "",
                    "adspower_api_base": DEFAULT_ADSPOWER_API_BASE,
                    "adspower_api_key": "",
                    "adspower_auto_start": True,
                    "adspower_auto_form_login": True,
                    "adspower_close_after_sync": True,
                    "adspower_sync_proxy": True,
                    "adspower_last_sync_at": 0,
                    "adspower_sync_interval_seconds": 5,
                }
            )
            doc["members"] = members
            try:
                atomic_write_json(self._arsenal_path, doc)
            except Exception as e:
                self._send_json(500, {"ok": False, "message": f"写入失败: {e}"})
                return

            self._send_json(200, {"ok": True, "member": {"id": member_id, "label": label, "username": username}})
            return

        base_url = str((data.get("base_url") or "")).strip()
        username = str((data.get("username") or "")).strip()
        password = str((data.get("password") or "")).strip()
        security_code = str((data.get("security_code") or "")).strip()
        login_tab = int(data.get("relogin_login_tab") or 3)
        login2_watch_members, login2_watch_downlines = normalize_account_watch_fields(login_tab, data)
        time_offset_hours = int(data.get("time_offset_hours") or 0)
        relogin_min_interval_seconds = data.get("relogin_min_interval_seconds")
        if not base_url or not username or not password:
            self._send_json(400, {"ok": False, "message": "缺少 base_url/username/password"})
            return

        cfg = self._read_config()
        cfg.setdefault("targets", [])

        # 新账号：先让 watcher 自动登录生成 cookie/uid
        base_name = safe_name(f"{username}")
        name = self._make_unique_name(cfg, base_name)
        target = build_account_add_target(
            name=name,
            base_url=base_url,
            username=username,
            password=password,
            security_code=security_code,
            tab=login_tab,
            time_offset_hours=time_offset_hours,
            relogin_min_interval_seconds=relogin_min_interval_seconds,
            login2_watch_members=login2_watch_members,
            login2_watch_downlines=login2_watch_downlines,
        )
        target["offline_alert_seconds"] = 30

        cfg["targets"].append(target)
        try:
            atomic_write_json(self._config_path, cfg)
        except Exception as e:
            self._send_json(500, {"ok": False, "message": f"写入配置失败: {e}"})
            return

        self._send_json(200, {"ok": True, "account": {"name": name, "username": username, "base_url": base_url}})


def main() -> int:
    parser = argparse.ArgumentParser(description="AG Orders WebUI (no deps)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--orders-dir", default="orders")
    parser.add_argument("--webui-dir", default="webui")
    parser.add_argument("--state-dir", default="state")
    parser.add_argument("--config", default="config.json", help="监听脚本使用的 config.json（用于添加账号）")
    parser.add_argument("--data-dir", default="data", help="WebUI 本地数据目录（弹药库/跟单设置）")
    args = parser.parse_args()

    orders_dir = Path(args.orders_dir)
    webui_dir = Path(args.webui_dir)
    state_dir = Path(args.state_dir)
    config_path = Path(args.config)
    data_dir = Path(args.data_dir)
    if not webui_dir.exists():
        raise SystemExit(f"WebUI 目录不存在: {webui_dir}")

    data_dir.mkdir(parents=True, exist_ok=True)
    ensure_json_file(data_dir / "arsenal.json", {"members": []})
    ensure_json_file(data_dir / "mappings.json", {})
    ensure_json_file(
        data_dir / "follow_settings.json",
        {
            "enabled": False,
            "mode": "fixed",
            "fixed_amount": 50,
            "ratio_percent": 100,
            "match_limit_amount": 0,
            "market_limit_amount": 0,
            "split_count": 2,
            "split_delay_seconds": 0,
            "targets": [],
            "members": [],
            "updated_at": "",
        },
    )

    # 服务重启后自动恢复已保存的平博只读监听。没有账号时不启动空 worker。
    saved_cfg = read_json_file(config_path, {})
    if isinstance(saved_cfg, dict) and any(
        isinstance(item, dict) and bool(item.get("enabled", True))
        for item in (saved_cfg.get("ps3838_accounts") or [])
    ):
        ensure_ps3838_watch_running(config_path, data_dir)

    def handler_factory(*h_args, **h_kwargs):
        return Handler(
            *h_args,
            directory=str(webui_dir),
            orders_dir=orders_dir,
            state_dir=state_dir,
            config_path=config_path,
            data_dir=data_dir,
            **h_kwargs,
        )

    httpd = ThreadingHTTPServer((args.host, args.port), handler_factory)
    print(f"[{now_iso()}] WebUI: http://{args.host}:{args.port}  orders_dir={orders_dir}")
    httpd.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
