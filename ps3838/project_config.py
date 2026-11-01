"""Shared configuration: environment overrides > selected JSON file > defaults.

The optional default config.json may be absent. An explicitly selected config
file must exist, so a typo cannot silently launch another browser or database.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.json"

DEFAULTS: Dict[str, Any] = {
    "cdp_url": "http://127.0.0.1:9222",
    "crown_cdp_url": "",
    "chrome_binary": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "chrome_profile_dir": "~/.ps3838-wss-chrome-profile",
    "start_url": "https://www.ps3838.com/zh-cn/sports/soccer",
    "target_hosts": ["ps3838.com", "ps38ag.com"],
    "capture_path": "capture.jsonl",
    "events_file": "events_list.csv",
    "live_snapshot_file": "latest_odds_live.csv",
    "latest_output_file": "latest_odds_10_matches.csv",
    "normalized_output_file": "latest_odds_normalized.csv",
    "dashboard_db_path": "odds_history.sqlite3",
    "merge_db_path": "fixture_merge.sqlite3",
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8787,
    "crown_host": "127.0.0.1",
    "crown_port": 9223,
    "crown_extra_markets_enabled": True,
    "backup_dir": "backups",
    "run_dir": ".run",
    "log_dir": "logs",
    "crown_data_dir": "crown/data",
    "backup_keep": 7,
    "capture_compress_after_hours": 24,
    "capture_compress_min_mb": 16,
    "presence_interval_seconds": 5,
    "snapshot_interval_seconds": 2,
    "capture_rotation_mb": 0,
    "partner_orders_db_path": "var/db/partner_orders.sqlite3",
    "partner_backend_url": "https://www.ps38ag.com/partner-ui-v2/report-outstanding-v2",
    "partner_agents": ["TLH0R02", "TLH0R03", "TLH0R04", "TLH0R28"],
    "partner_history_from": "2026-07-01",
    "partner_open_interval_seconds": 1,
    "partner_settled_interval_seconds": 300,
    "partner_settled_window_days": 7,
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_proxy": "",
    # Optional bridge to the HD project's local signal receiver.  It is
    # disabled by default and requires an explicit HGA/HD gid mapping before
    # an order can be forwarded.
    "local_signal_push_enabled": False,
    "local_signal_push_url": "http://127.0.0.1:9999/api/local-signal/push",
    "local_signal_push_token": "",
    "local_signal_push_target": "ps3838",
    "local_signal_push_require_gid": True,
    "local_signal_push_interval_seconds": 3,
    "local_signal_push_gid_map_file": "var/db/local_signal_gid_map.json",
    "local_signal_push_state_db": "var/db/local_signal_push.sqlite3",
}

# The first nonempty alias wins; source-specific overrides precede shared ones.
ENV_OVERRIDES = {
    "cdp_url": ("CAPTURE_CDP_URL", "CDP_URL"),
    "crown_cdp_url": ("CROWN_CDP_URL",),
    "capture_path": ("CAPTURE_OUTPUT", "CAPTURE_PATH"),
    "events_file": ("EVENTS_FILE",),
    "live_snapshot_file": ("LIVE_SNAPSHOT_FILE",),
    "dashboard_db_path": ("DASHBOARD_DB_PATH",),
    "merge_db_path": ("MERGE_DB_PATH", "FIXTURE_MERGE_DB_PATH"),
    "backup_dir": ("BACKUP_DIR",),
    "run_dir": ("RUN_DIR",),
    "log_dir": ("LOG_DIR",),
    "crown_data_dir": ("CROWN_DATA_DIR",),
    "chrome_binary": ("CHROME_BINARY",),
    "chrome_profile_dir": ("CHROME_PROFILE_DIR",),
    "start_url": ("START_URL",),
    "target_hosts": ("CAPTURE_TARGET_HOSTS",),
    "dashboard_host": ("DASHBOARD_HOST",),
    "dashboard_port": ("DASHBOARD_PORT",),
    "crown_host": ("CROWN_HOST",),
    "crown_port": ("CROWN_PORT",),
    "dashboard_url": ("CAPTURE_DASHBOARD_URL",),
    "presence_interval_seconds": ("CAPTURE_PRESENCE_INTERVAL_SECONDS",),
    "partner_orders_db_path": ("PARTNER_ORDERS_DB_PATH",),
    "partner_backend_url": ("PARTNER_BACKEND_URL",),
    "partner_agents": ("PARTNER_AGENTS",),
    "partner_history_from": ("PARTNER_HISTORY_FROM",),
    "partner_open_interval_seconds": ("PARTNER_OPEN_INTERVAL_SECONDS",),
    "partner_settled_interval_seconds": ("PARTNER_SETTLED_INTERVAL_SECONDS",),
    "telegram_bot_token": ("TELEGRAM_BOT_TOKEN",),
    "telegram_chat_id": ("TELEGRAM_CHAT_ID",),
    "telegram_proxy": ("TELEGRAM_PROXY",),
    "local_signal_push_enabled": ("LOCAL_SIGNAL_PUSH_ENABLED",),
    "local_signal_push_url": ("LOCAL_SIGNAL_PUSH_URL",),
    "local_signal_push_token": ("LOCAL_SIGNAL_PUSH_TOKEN",),
    "local_signal_push_target": ("LOCAL_SIGNAL_PUSH_TARGET",),
    "local_signal_push_interval_seconds": ("LOCAL_SIGNAL_PUSH_INTERVAL_SECONDS",),
    "local_signal_push_gid_map_file": ("LOCAL_SIGNAL_PUSH_GID_MAP_FILE",),
    "local_signal_push_state_db": ("LOCAL_SIGNAL_PUSH_STATE_DB",),
}

PATH_ALIASES = {
    "events_file": ("events_path",),
    "live_snapshot_file": ("output",),
    "latest_output_file": ("latest_output",),
    "normalized_output_file": ("normalized_output",),
    "merge_db_path": ("fixture_merge_db_path",),
}


def config_path() -> Path:
    """Resolve the selected config relative to the project, not shell cwd."""
    path = Path(os.environ.get("PS3838_CONFIG") or DEFAULT_CONFIG_PATH).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


def _port(value: Any, key: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"配置 {key} 必须是 1–65535 的端口号") from exc
    if isinstance(value, bool) or str(value).strip() != str(number) or not 1 <= number <= 65535:
        raise RuntimeError(f"配置 {key} 必须是 1–65535 的端口号")
    return number


def load_config() -> Dict[str, Any]:
    """Load shared defaults, selected JSON, then explicit environment overrides."""
    config = copy.deepcopy(DEFAULTS)
    active_path = config_path()
    loaded: Dict[str, Any] = {}
    if active_path.exists() or os.environ.get("PS3838_CONFIG"):
        try:
            with active_path.open(encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("顶层必须是 JSON 对象")
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"无法读取配置文件 {active_path}: {exc}") from exc
    for key, aliases in PATH_ALIASES.items():
        if not loaded.get(key):
            for alias in aliases:
                if loaded.get(alias):
                    loaded[key] = loaded[alias]
                    break
    if not loaded.get("start_url") and loaded.get("target_url"):
        loaded["start_url"] = loaded["target_url"]
    config.update(loaded)
    for key, names in ENV_OVERRIDES.items():
        for name in names:
            if os.environ.get(name):
                config[key] = os.environ[name]
                break
    for source in ("dashboard", "crown"):
        key = f"{source}_host"
        host = str(config[key]).strip()
        if not host or any(char.isspace() for char in host) or "/" in host:
            raise RuntimeError(f"配置 {key} 必须是有效主机名或 IP 地址")
        config[key] = host
        config[f"{source}_port"] = _port(config[f"{source}_port"], f"{source}_port")
    config["crown_cdp_url"] = config.get("crown_cdp_url") or config["cdp_url"]
    config["fixture_merge_db_path"] = config["merge_db_path"]
    config["dashboard_url"] = config.get("dashboard_url") or service_url(config, "dashboard")
    return config


def resolve_path(value: Any, fallback: str) -> Path:
    """Resolve runtime paths relative to the project, preserving legacy layout."""
    path = Path(str(value or fallback)).expanduser()
    return path if path.is_absolute() else PROJECT_DIR / path


def get_path(config: Dict[str, Any], key: str) -> Path:
    if key == "fixture_merge_db_path":
        key = "merge_db_path"
    fallback = DEFAULTS[key]
    for candidate in (key,) + PATH_ALIASES.get(key, ()):
        if config.get(candidate):
            return resolve_path(config[candidate], str(fallback))
    return resolve_path(fallback, str(fallback))


def target_hosts(config: Dict[str, Any]):
    value = config.get("target_hosts", DEFAULTS["target_hosts"])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        value = DEFAULTS["target_hosts"]
    return tuple(str(item).strip().lower().lstrip(".") for item in value if str(item).strip())


def service_url(config: Dict[str, Any], source: str, *, connect: bool = True) -> str:
    """Return HTTP origin; readiness checks connect to loopback for wildcard binds."""
    if source not in {"dashboard", "crown"}:
        raise ValueError("未知服务")
    host = str(config.get(f"{source}_host") or DEFAULTS[f"{source}_host"])
    if connect and host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{config.get(f'{source}_port', DEFAULTS[f'{source}_port'])}"


def shell_config(config: Dict[str, Any]) -> str:
    """Emit only known launcher variables, escaped as shell assignments."""
    values = {
        "PS3838_CONFIG": str(config_path()) if config_path().exists() or os.environ.get("PS3838_CONFIG") else "",
        "CDP_URL": config["cdp_url"],
        "CAPTURE_CDP_URL": config["cdp_url"],
        "CROWN_CDP_URL": config["crown_cdp_url"],
        "CHROME_BINARY": str(resolve_path(config["chrome_binary"], DEFAULTS["chrome_binary"])),
        "CHROME_PROFILE_DIR": str(resolve_path(config["chrome_profile_dir"], DEFAULTS["chrome_profile_dir"])),
        "START_URL": config["start_url"],
        "CAPTURE_DASHBOARD_URL": config["dashboard_url"],
        "CAPTURE_TARGET_HOSTS": ",".join(target_hosts(config)),
        "DASHBOARD_HOST": config["dashboard_host"],
        "DASHBOARD_PORT": config["dashboard_port"],
        "DASHBOARD_SOCKET": str(get_path(config, "run_dir") / "dashboard.sock"),
        "CROWN_HOST": config["crown_host"],
        "CROWN_PORT": config["crown_port"],
        "DASHBOARD_URL": service_url(config, "dashboard"),
        "CROWN_URL": service_url(config, "crown"),
    }
    agents = config.get("partner_agents") or []
    if isinstance(agents, str):
        agents = agents.split(",")
    values["PARTNER_AGENTS"] = ",".join(str(item).strip() for item in agents if str(item).strip())
    values["PARTNER_HISTORY_FROM"] = str(config.get("partner_history_from") or "")
    values["PARTNER_OPEN_INTERVAL_SECONDS"] = str(config.get("partner_open_interval_seconds", 1))
    values["PARTNER_SETTLED_INTERVAL_SECONDS"] = str(config.get("partner_settled_interval_seconds", 300))
    for variable, key in {
        "CAPTURE_PATH": "capture_path", "CAPTURE_OUTPUT": "capture_path",
        "EVENTS_FILE": "events_file", "LIVE_SNAPSHOT_FILE": "live_snapshot_file",
        "DASHBOARD_DB_PATH": "dashboard_db_path", "MERGE_DB_PATH": "merge_db_path",
        "BACKUP_DIR": "backup_dir", "RUN_DIR": "run_dir",
        "LOG_DIR": "log_dir", "CROWN_DATA_DIR": "crown_data_dir",
        "PARTNER_ORDERS_DB_PATH": "partner_orders_db_path",
    }.items():
        values[variable] = str(get_path(config, key))
    return "\n".join(f"export {name}={shlex.quote(str(value))}" for name, value in values.items())


def main() -> int:
    parser = argparse.ArgumentParser(description="项目配置的统一启动入口")
    parser.add_argument("command", choices=["shell"])
    parser.parse_args()
    try:
        print(shell_config(load_config()))
    except (RuntimeError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
