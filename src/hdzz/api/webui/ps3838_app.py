"""Embed and control the merged ``ps3838`` data terminal.

The ps3838 source now lives in ``hdproc/ps3838``.  Its dashboard, capture,
Crown service and Chrome/CDP bridge remain separate worker processes, but they
share the HD repository and are started/stopped by HD's ``app.py``.  This
module only owns the dashboard health check and the manual start/stop hooks
used by the PS3838 tab.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from hdzz.api.webui import ps3838_transport


BEIJING = timezone(timedelta(hours=8))
_MERGED_PS3838_DIR = Path(__file__).resolve().parents[4] / "ps3838"
_LEGACY_PS3838_DIR = Path.home() / "ps3838-wss"
DEFAULT_PS3838_APP_DIR = str(_MERGED_PS3838_DIR if _MERGED_PS3838_DIR.is_dir() else _LEGACY_PS3838_DIR)
DEFAULT_PS3838_APP_URL = "http://127.0.0.1:8787"

PS3838_APP_DEFAULTS: Dict[str, Any] = {
    "ps3838_app_dir": DEFAULT_PS3838_APP_DIR,
    "ps3838_app_url": DEFAULT_PS3838_APP_URL,
    "ps3838_app_auto_start": True,
}

_START_LOCK = threading.Lock()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def ensure_ps3838_app_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
    for key, value in PS3838_APP_DEFAULTS.items():
        out.setdefault(key, value)
    out["ps3838_app_dir"] = _text(out.get("ps3838_app_dir")) or DEFAULT_PS3838_APP_DIR
    out["ps3838_app_url"] = (_text(out.get("ps3838_app_url")) or DEFAULT_PS3838_APP_URL).rstrip("/")
    out["ps3838_app_auto_start"] = _as_bool(out.get("ps3838_app_auto_start"), True)
    return out


def _health_url(url: str) -> str:
    return f"{url.rstrip('/')}/api/health"


def dashboard_socket_path(cfg: Dict[str, Any]) -> Path:
    """Resolve ``<ps3838>/<run_dir>/dashboard.sock`` from the PS3838 config."""
    merged = ensure_ps3838_app_defaults(cfg)
    directory = Path(merged["ps3838_app_dir"]).expanduser()
    run_dir = "var/run"
    try:
        doc = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("run_dir"):
            run_dir = str(doc["run_dir"])
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    base = Path(run_dir).expanduser()
    if not base.is_absolute():
        base = directory / base
    return base / "dashboard.sock"


def check_health(url: str, *, timeout: float = 2.0, socket_path: Optional[Path] = None) -> Dict[str, Any]:
    # The merged layout talks to the dashboard over a Unix socket, so no TCP
    # port (8787) is required anymore.  Keep the HTTP path for legacy setups.
    if socket_path is not None and Path(socket_path).exists():
        return ps3838_transport.health(Path(socket_path), timeout=timeout)
    request = Request(_health_url(url), headers={"Accept": "application/json"}, method="GET")
    try:
        # Dashboard is loopback; do not route it through a global HTTP proxy.
        opener = build_opener(ProxyHandler({}))
        with opener.open(request, timeout=max(0.5, float(timeout))) as response:
            raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            if isinstance(payload, dict):
                payload.setdefault("ok", True)
                return payload
            return {"ok": True, "raw": raw[:500]}
    except URLError as exc:
        return {"ok": False, "error": str(getattr(exc, "reason", exc))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def status(cfg: Dict[str, Any]) -> Dict[str, Any]:
    cfg = ensure_ps3838_app_defaults(cfg)
    directory = Path(cfg["ps3838_app_dir"]).expanduser()
    url = cfg["ps3838_app_url"]
    socket_path = dashboard_socket_path(cfg)
    health = check_health(url, socket_path=socket_path)
    running = bool(health.get("ok"))
    start_script = directory / "start.sh"
    stop_script = directory / "stop.sh"
    return {
        "ok": True,
        "running": running,
        "healthy": running,
        "url": url,
        "socket": str(socket_path),
        "transport": "unix" if socket_path.exists() else "tcp",
        "embed_url": "/ps3838/",
        "dir": str(directory),
        "auto_start": bool(cfg.get("ps3838_app_auto_start")),
        "dir_exists": directory.is_dir(),
        "start_script_exists": start_script.is_file(),
        "stop_script_exists": stop_script.is_file(),
        "health": health,
        "checked_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _spawn_script(
    *,
    script: Path,
    directory: Path,
    log_path: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    if not directory.is_dir():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not script.is_file():
        raise FileNotFoundError(f"脚本不存在: {script}")
    if os.name == "nt":
        raise RuntimeError("当前系统不是 macOS/Linux，暂不支持直接运行 .sh 脚本")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})
    log_handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            ["/bin/bash", str(script)],
            cwd=str(directory),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    return {"pid": int(process.pid), "script": str(script), "log": str(log_path)}


def start(cfg: Dict[str, Any], *, data_dir: Path) -> Dict[str, Any]:
    cfg = ensure_ps3838_app_defaults(cfg)
    current = status(cfg)
    if current.get("running"):
        return {
            "ok": True,
            "already_running": True,
            "message": "ps3838-wss 已在运行",
            "status": current,
        }
    directory = Path(cfg["ps3838_app_dir"]).expanduser()
    script = directory / "start.sh"
    with _START_LOCK:
        # Re-check under the lock: another click may have just started it.
        current = status(cfg)
        if current.get("running"):
            return {"ok": True, "already_running": True, "message": "ps3838-wss 已在运行", "status": current}
        info = _spawn_script(
            script=script,
            directory=directory,
            log_path=Path(data_dir) / "ps3838_app_start.log",
            extra_env={"PYTHONUNBUFFERED": "1"},
        )
    return {
        "ok": True,
        "started": True,
        "message": "已发起 ps3838-wss 启动，正在等待看板就绪",
        **info,
    }


def stop(cfg: Dict[str, Any], *, data_dir: Path) -> Dict[str, Any]:
    cfg = ensure_ps3838_app_defaults(cfg)
    directory = Path(cfg["ps3838_app_dir"]).expanduser()
    script = directory / "stop.sh"
    with _START_LOCK:
        info = _spawn_script(
            script=script,
            directory=directory,
            log_path=Path(data_dir) / "ps3838_app_stop.log",
        )
    return {
        "ok": True,
        "stopping": True,
        "message": "已发起 ps3838-wss 停止",
        **info,
    }


_PS3838_CONFIG_NAME = "config.json"
_TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
_TELEGRAM_CHAT_RE = re.compile(r"^(?:-?\d+|@[A-Za-z0-9_]{5,})$")
_TELEGRAM_PROXY_RE = re.compile(r"^https?://\S+$")


def ps3838_config_path(cfg: Dict[str, Any]) -> Path:
    """Return the active ps3838 config.json (same resolution as the app)."""
    merged = ensure_ps3838_app_defaults(cfg)
    return Path(merged["ps3838_app_dir"]).expanduser() / _PS3838_CONFIG_NAME


def _read_ps3838_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 PS3838 配置：{exc}") from exc
    if not isinstance(doc, dict):
        raise RuntimeError("PS3838 配置文件顶层必须是 JSON 对象")
    return doc


def _write_ps3838_config(path: Path, doc: Dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    try:
        # The file also stores the bot token, so keep it owner-only.
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_telegram_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read the PS3838 Telegram push settings without exposing the token."""
    path = ps3838_config_path(cfg)
    doc = _read_ps3838_config(path)
    return {
        "path": str(path),
        "exists": path.exists(),
        "enabled": bool(doc.get("telegram_enabled")),
        "token": _text(doc.get("telegram_bot_token")),
        "chat_id": _text(doc.get("telegram_chat_id")),
        "proxy": _text(doc.get("telegram_proxy")),
    }


def save_telegram_config(cfg: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    """Persist PS3838 Telegram push settings into ps3838/config.json.

    The live pusher re-reads this file on every poll, so no PS3838 restart is
    needed after saving.  Unknown keys in the file are preserved.
    """
    if not isinstance(data, dict):
        raise ValueError("请求数据格式不正确")
    path = ps3838_config_path(cfg)
    current = _read_ps3838_config(path)
    if not path.exists():
        raise RuntimeError(f"PS3838 配置文件不存在：{path}")

    token = _text(current.get("telegram_bot_token"))
    if data.get("clear_token"):
        token = ""
    else:
        new_token = _text(data.get("bot_token"))
        if new_token:
            if not _TELEGRAM_TOKEN_RE.fullmatch(new_token):
                raise ValueError("机器人 Token 格式不正确")
            token = new_token

    chat_id = _text(current.get("telegram_chat_id"))
    if "chat_id" in data:
        chat_id = _text(data.get("chat_id"))
        if chat_id and not _TELEGRAM_CHAT_RE.fullmatch(chat_id):
            raise ValueError("请填写群 Chat ID（-100 开头）或公开群 @用户名")

    proxy = _text(current.get("telegram_proxy"))
    if "proxy" in data:
        proxy = _text(data.get("proxy"))
        if proxy and not _TELEGRAM_PROXY_RE.fullmatch(proxy):
            raise ValueError("代理地址必须以 http:// 或 https:// 开头")

    enabled = bool(current.get("telegram_enabled"))
    if "enabled" in data:
        if not isinstance(data.get("enabled"), bool):
            raise ValueError("推送开关必须是布尔值")
        enabled = bool(data["enabled"])

    if enabled and not (token and chat_id):
        raise ValueError("开启推送前请先填写 Bot Token 和 Chat ID")

    updated = dict(current)
    updated.update(
        {
            "telegram_enabled": enabled,
            "telegram_bot_token": token,
            "telegram_chat_id": chat_id,
            "telegram_proxy": proxy,
        }
    )
    _write_ps3838_config(path, updated)
    return read_telegram_config(cfg)
