"""Local bridge ingress for the HD trading node.

The ``ps3838-wss`` project merges partner orders on the same match/line/score
and POSTs the *Crown opposite side* (对手盘) to HD.  This module owns the
config, the conversion of that push payload into an HD order row, and the
small state file that backs the node status shown in the WebUI.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


BEIJING = timezone(timedelta(hours=8))
LOCAL_BRIDGE_PATH = "/api/local-signal/push"
LOCAL_BRIDGE_ONLINE_WINDOW_SECONDS = 600
MAX_SEEN_ORDER_IDS = 5000
MAX_EVENTS = 200

LOCAL_BRIDGE_DEFAULTS: Dict[str, Any] = {
    "local_bridge_configured": False,
    "local_bridge_enabled": False,
    "local_bridge_name": "本地桥接",
    "local_bridge_target": "ps3838",
    "local_bridge_token": "",
    "local_bridge_allow_remote": False,
}

_LOCK = threading.Lock()


class LocalBridgeSignalError(ValueError):
    """The bridge payload cannot be converted into a safe HD order."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _bool(value: Any, default: bool = False) -> bool:
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


def safe_target(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", _text(value))
    return text.strip("._") or "ps3838"


def ensure_local_bridge_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
    for key, value in LOCAL_BRIDGE_DEFAULTS.items():
        out.setdefault(key, value)

    out["local_bridge_configured"] = bool(out.get("local_bridge_configured"))
    out["local_bridge_enabled"] = bool(out.get("local_bridge_enabled"))
    out["local_bridge_name"] = _text(out.get("local_bridge_name"))[:40] or "本地桥接"
    raw_target = _text(out.get("local_bridge_target"))
    out["local_bridge_target"] = safe_target(raw_target) if raw_target else ""
    out["local_bridge_token"] = _text(out.get("local_bridge_token"))[:256]
    out["local_bridge_allow_remote"] = bool(out.get("local_bridge_allow_remote"))
    return out


def merge_local_bridge_config(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = ensure_local_bridge_defaults(existing)
    patch = incoming if isinstance(incoming, dict) else {}

    if "local_bridge_enabled" in patch:
        out["local_bridge_enabled"] = _bool(patch.get("local_bridge_enabled"))
    if "local_bridge_allow_remote" in patch:
        out["local_bridge_allow_remote"] = _bool(patch.get("local_bridge_allow_remote"))
    if "local_bridge_name" in patch:
        out["local_bridge_name"] = (_text(patch.get("local_bridge_name"))[:40] or "本地桥接")
    if "local_bridge_target" in patch:
        out["local_bridge_target"] = safe_target(patch.get("local_bridge_target") or "ps3838")
    if "local_bridge_token" in patch:
        out["local_bridge_token"] = _text(patch.get("local_bridge_token"))[:256]
    if "local_bridge_configured" in patch:
        out["local_bridge_configured"] = _bool(patch.get("local_bridge_configured"))
    elif patch:
        # Any explicit save from the dialog creates/keeps the node.
        out["local_bridge_configured"] = True
    return out


def reset_local_bridge_config(existing: Dict[str, Any]) -> Dict[str, Any]:
    out = ensure_local_bridge_defaults(existing)
    out["local_bridge_configured"] = False
    out["local_bridge_enabled"] = False
    out["local_bridge_target"] = ""
    out["local_bridge_token"] = ""
    out["local_bridge_allow_remote"] = False
    return out


def token_matches(expected: str, provided: str) -> bool:
    import hmac

    expected_text = _text(expected)
    if not expected_text:
        return True
    return hmac.compare_digest(expected_text, _text(provided))


def _beijing_now(now_ts: Optional[float] = None) -> datetime:
    return datetime.fromtimestamp(float(time.time() if now_ts is None else now_ts), BEIJING)


def build_local_bridge_order(
    payload: Dict[str, Any],
    *,
    default_target: str = "ps3838",
    target_override: Optional[str] = None,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Convert one ``ps3838-wss`` push into an HD ``orders/*.jsonl`` row."""

    if not isinstance(payload, dict):
        raise LocalBridgeSignalError("invalid_json")

    order_id = _text(payload.get("orderId") or payload.get("order_id"))
    league = _text(payload.get("league"))
    sides = _text(payload.get("sides") or payload.get("match"))
    line = _text(payload.get("line"))
    odds_text = _text(payload.get("odds"))
    stake_text = _text(payload.get("stake") if payload.get("stake") not in (None, "") else payload.get("amount"))
    gid = _text(payload.get("gid") or payload.get("event_id") or payload.get("eventId"))
    target = safe_target(target_override or payload.get("target") or default_target)

    if not order_id:
        raise LocalBridgeSignalError("missing_order_id")
    if not league:
        raise LocalBridgeSignalError("missing_league")
    if not sides:
        raise LocalBridgeSignalError("missing_sides")
    if not line:
        raise LocalBridgeSignalError("missing_line")
    if not gid:
        raise LocalBridgeSignalError("missing_gid")
    odds = _number(odds_text)
    stake = _number(stake_text)
    if odds is None or odds <= 0:
        raise LocalBridgeSignalError("invalid_odds")
    if stake is None or stake <= 0:
        raise LocalBridgeSignalError("invalid_stake")

    bet_type = _text(payload.get("betType") or payload.get("bet_type")) or "让球"
    sport = _text(payload.get("sport")) or "足球"
    raw_in = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    raw = dict(raw_in)

    crown_league = _text(payload.get("crownLeague"))
    crown_home = _text(payload.get("crownHome"))
    crown_away = _text(payload.get("crownAway"))
    crown_opponent = _text(payload.get("crownOpponent"))
    crown_odds = _text(payload.get("crownOdds"))
    crown_snapshot_at = _text(payload.get("crownSnapshotAt"))

    live = bool(payload.get("inplay")) or "(滚球)" in bet_type
    market_code = _text(raw.get("MARKET") or raw.get("market")).lower()
    if market_code in {"rb", "live", "running"}:
        live = True

    raw["_EVENT"] = gid
    raw["GID"] = gid
    raw.setdefault("GTYPE", _text(payload.get("gtype")) or "FT")
    raw.setdefault("MARKET", "rb" if live else "")
    raw.setdefault("WAGERSTYPE", bet_type)
    raw.setdefault("SHOWTEXT_ORDER_TYPE_IORATIO", f"{line} @{odds_text}")
    raw.setdefault("SHOWTEXT_LEAGUE", f"{league}<br>")
    raw.setdefault("SOURCE", "ps3838-wss")
    if crown_league:
        raw.setdefault("CROWN_LEAGUE", crown_league)
    if crown_opponent:
        raw.setdefault("CROWN_OPPONENT", crown_opponent)
    if crown_odds:
        raw.setdefault("CROWN_ODDS", crown_odds)
    if crown_home:
        raw.setdefault("TEAM_H", crown_home)
    if crown_away:
        raw.setdefault("TEAM_C", crown_away)

    now_dt = _beijing_now(now_ts)
    date_text = _text(payload.get("date")) or now_dt.strftime("%m-%d-%Y")
    time_text = _text(payload.get("time")) or now_dt.strftime("%H:%M:%S")
    ts = int(now_ts if now_ts is not None else time.time())

    return {
        "ts": ts,
        "target": target,
        "orderId": order_id,
        "sport": sport,
        "stake": stake_text or f"{stake:.2f}",
        "odds": odds_text,
        "league": league,
        "sides": sides,
        "betType": bet_type,
        "line": line,
        "date": date_text,
        "time": time_text,
        "gid": gid,
        "raw": raw,
        "_skip_signal_forward": True,
        "_signal_origin": "local_bridge",
        "_local_bridge": {
            "source": "ps3838-wss",
            "received_at": ts,
            "crown_league": crown_league,
            "crown_home": crown_home,
            "crown_away": crown_away,
            "crown_opponent": crown_opponent or line,
            "crown_odds": crown_odds or odds_text,
            "crown_snapshot_at": crown_snapshot_at,
        },
    }


def state_path(data_dir: Path) -> Path:
    return Path(data_dir) / "local_bridge_state.json"


def events_path(data_dir: Path) -> Path:
    return Path(data_dir) / "local_bridge_events.jsonl"


def default_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "received_count": 0,
        "duplicate_count": 0,
        "rejected_count": 0,
        "last_received_at": 0.0,
        "last_duplicate_at": 0.0,
        "last_rejected_at": 0.0,
        "last_order_id": "",
        "last_target": "",
        "last_remote": "",
        "last_error": "",
        "last_signal": {},
        "seen_order_ids": [],
    }


def load_state(data_dir: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(state_path(data_dir).read_text(encoding="utf-8-sig"))
    except Exception:
        return default_state()
    if not isinstance(obj, dict):
        return default_state()
    out = default_state()
    out.update(obj)
    if not isinstance(out.get("seen_order_ids"), list):
        out["seen_order_ids"] = []
    return out


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def _signal_summary(order: Dict[str, Any]) -> Dict[str, Any]:
    bridge = order.get("_local_bridge") if isinstance(order.get("_local_bridge"), dict) else {}
    return {
        "orderId": _text(order.get("orderId")),
        "target": _text(order.get("target")),
        "league": _text(order.get("league")),
        "sides": _text(order.get("sides")),
        "line": _text(order.get("line")),
        "odds": _text(order.get("odds")),
        "stake": _text(order.get("stake")),
        "betType": _text(order.get("betType")),
        "crownOpponent": _text(bridge.get("crown_opponent")),
        "crownOdds": _text(bridge.get("crown_odds")),
        "crownSnapshotAt": _text(bridge.get("crown_snapshot_at")),
        "received_at": float(order.get("ts") or 0.0),
    }


def record_rejection(data_dir: Path, message: str, *, remote: str = "") -> None:
    with _LOCK:
        state = load_state(data_dir)
        state["rejected_count"] = int(state.get("rejected_count") or 0) + 1
        state["last_rejected_at"] = time.time()
        state["last_error"] = _text(message)[:500]
        if remote:
            state["last_remote"] = remote
        _atomic_write_json(state_path(data_dir), state)


def receive_local_bridge_signal(
    *,
    data_dir: Path,
    orders_dir: Path,
    payload: Dict[str, Any],
    default_target: str = "ps3838",
    remote: str = "",
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Append an inbound bridge order once and update the node state."""

    order = build_local_bridge_order(
        payload,
        default_target=default_target,
        target_override=default_target,
        now_ts=now_ts,
    )
    target = _text(order.get("target")) or safe_target(default_target)
    order_id = _text(order.get("orderId"))
    key = f"{target}|{order_id}"
    now = float(now_ts if now_ts is not None else time.time())

    with _LOCK:
        state = load_state(data_dir)
        seen = {str(item) for item in (state.get("seen_order_ids") or []) if str(item)}
        if key in seen:
            state["duplicate_count"] = int(state.get("duplicate_count") or 0) + 1
            state["last_duplicate_at"] = now
            state["last_remote"] = remote or _text(state.get("last_remote"))
            _atomic_write_json(state_path(data_dir), state)
            return {"ok": True, "duplicate": True, "order": order, "target": target, "orderId": order_id}

        _append_jsonl(Path(orders_dir) / f"{safe_target(target)}.jsonl", order)
        _append_jsonl(
            events_path(data_dir),
            {
                "ts": now,
                "remote": remote,
                "target": target,
                "orderId": order_id,
                "signal": _signal_summary(order),
            },
        )

        state["received_count"] = int(state.get("received_count") or 0) + 1
        state["last_received_at"] = now
        state["last_order_id"] = order_id
        state["last_target"] = target
        state["last_remote"] = remote
        state["last_error"] = ""
        state["last_signal"] = _signal_summary(order)
        seen_list = list(state.get("seen_order_ids") or [])
        seen_list.append(key)
        state["seen_order_ids"] = seen_list[-MAX_SEEN_ORDER_IDS:]
        _atomic_write_json(state_path(data_dir), state)

    return {"ok": True, "duplicate": False, "order": order, "target": target, "orderId": order_id}


def status_payload(
    cfg: Dict[str, Any],
    state: Dict[str, Any],
    *,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    now = float(time.time() if now_ts is None else now_ts)
    last_received = float(state.get("last_received_at") or 0.0)
    enabled = bool(cfg.get("local_bridge_enabled"))
    configured = bool(cfg.get("local_bridge_configured"))
    online = bool(enabled and last_received > 0 and (now - last_received) <= LOCAL_BRIDGE_ONLINE_WINDOW_SECONDS)
    return {
        "configured": configured,
        "enabled": enabled,
        "online": online,
        "target": _text(cfg.get("local_bridge_target")),
        "last_received_at": last_received,
        "last_received_text": (
            datetime.fromtimestamp(last_received, BEIJING).strftime("%Y-%m-%d %H:%M:%S")
            if last_received > 0
            else ""
        ),
        "received_count": int(state.get("received_count") or 0),
        "duplicate_count": int(state.get("duplicate_count") or 0),
        "rejected_count": int(state.get("rejected_count") or 0),
        "last_order_id": _text(state.get("last_order_id")),
        "last_target": _text(state.get("last_target")),
        "last_remote": _text(state.get("last_remote")),
        "last_error": _text(state.get("last_error")),
        "last_signal": state.get("last_signal") if isinstance(state.get("last_signal"), dict) else {},
    }


def config_payload(
    cfg: Dict[str, Any],
    state: Dict[str, Any],
    *,
    endpoint: str,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    token = _text(cfg.get("local_bridge_token"))
    status = status_payload(cfg, state, now_ts=now_ts)
    return {
        "ok": True,
        "configured": bool(cfg.get("local_bridge_configured")),
        "config": {
            "local_bridge_configured": bool(cfg.get("local_bridge_configured")),
            "local_bridge_enabled": bool(cfg.get("local_bridge_enabled")),
            "local_bridge_name": _text(cfg.get("local_bridge_name")) or "本地桥接",
            "local_bridge_target": _text(cfg.get("local_bridge_target")),
            "local_bridge_token": token,
            "local_bridge_allow_remote": bool(cfg.get("local_bridge_allow_remote")),
        },
        "endpoint": endpoint,
        "token_mask": (token[:4] + "…" + token[-4:]) if len(token) > 8 else ("已设置" if token else ""),
        "status": status,
        "server_time": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
    }


def recent_events(data_dir: Path, limit: int = 20) -> List[Dict[str, Any]]:
    path = events_path(data_dir)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return []
    return rows[-max(1, min(int(limit or 20), MAX_EVENTS)):]
