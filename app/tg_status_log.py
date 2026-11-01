import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def now_ts() -> float:
    return time.time()


def now_cn() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def append_status_event(data_dir: Path, event: Dict[str, Any]) -> None:
    """Keep account status history locally for diagnostics only."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "telegram_status_log.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        # A diagnostic log failure must never interrupt order polling/login recovery.
        pass


def log_status_event(
    *,
    config_path: Path,
    data_dir: Path,
    kind: str,
    account: str,
    status: str,
    message: str,
    chat_id_override: str = "",
    proxy_override: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    del config_path, chat_id_override, proxy_override
    event: Dict[str, Any] = {
        "ts": int(now_ts()),
        "time": now_cn(),
        "kind": str(kind or ""),
        "account": str(account or ""),
        "status": str(status or ""),
        "message": str(message or ""),
    }
    if isinstance(extra, dict) and extra:
        event["extra"] = extra
    append_status_event(data_dir, event)


def load_tg_push_config(config_path: Path) -> Tuple[bool, str, str, str]:
    """Legacy compatibility: account-status Telegram delivery has been removed."""
    del config_path
    return False, "", "", ""


def log_and_maybe_push(**kwargs: Any) -> None:
    """Legacy compatibility alias; status events are now local-only."""
    log_status_event(**kwargs)
