from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests


BEIJING_TZ = timezone(timedelta(hours=8))


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_amount(value: Any) -> float:
    try:
        text = _clean(value).replace(",", "")
        return float(text or 0)
    except Exception:
        return 0.0


def _mode_to_market(mode: Any) -> str:
    mode_s = _clean(mode).lower()
    if mode_s in {"live", "rb", "running"}:
        return "rb"
    return mode_s or "manual"


def _mode_to_bet_prefix(mode: Any) -> str:
    return "(滚球) " if _mode_to_market(mode) == "rb" else ""


def _today_date(now_ts: int) -> str:
    return datetime.fromtimestamp(int(now_ts), BEIJING_TZ).strftime("%m-%d-%Y")


def _time_text(now_ts: int) -> str:
    return datetime.fromtimestamp(int(now_ts), BEIJING_TZ).strftime("%H:%M:%S")


def generate_manual_order_id(now_ts: Optional[int] = None) -> str:
    ts = int(time.time() if now_ts is None else now_ts)
    return str(ts)


def build_manual_signal_payload(data: Dict[str, Any], *, now_ts: Optional[int] = None) -> Dict[str, Any]:
    ts = int(time.time() if now_ts is None else now_ts)
    order_id = _clean(data.get("order_id") or data.get("orderId") or "") or generate_manual_order_id(ts)
    backend_name = _clean(data.get("backend_name")) or "manual"
    league = _clean(data.get("league"))
    match = _clean(data.get("match") or data.get("sides"))
    sport = _clean(data.get("sport")) or "足球"
    mode = _clean(data.get("mode")) or "live"
    bet_type = _clean(data.get("bet_type") or data.get("betType"))
    if bet_type and mode == "live" and "滚球" not in bet_type:
        bet_type = f"{_mode_to_bet_prefix(mode)}{bet_type}".strip()
    line = _clean(data.get("line"))
    odds = _clean(data.get("odds"))
    amount = _as_amount(data.get("amount") if data.get("amount") not in (None, "") else data.get("stake"))
    play = _clean(data.get("play")) or " ".join(x for x in [line, f"@{odds}" if odds else ""] if x).strip()
    event = _clean(data.get("event_id") or data.get("gid"))

    if not league:
        raise ValueError("missing_league")
    if not match:
        raise ValueError("missing_match")
    if not bet_type:
        raise ValueError("missing_bet_type")
    if not line:
        raise ValueError("missing_line")
    if not odds:
        raise ValueError("missing_odds")
    if amount <= 0:
        raise ValueError("invalid_amount")

    raw = data.get("raw") if isinstance(data.get("raw"), dict) else {}
    raw_out: Dict[str, Any] = dict(raw)
    raw_out.setdefault("GTYPE", _clean(data.get("gtype")) or "FT")
    raw_out.setdefault("MARKET", _mode_to_market(mode))
    raw_out.setdefault("WAGERSTYPE", bet_type)
    raw_out.setdefault("SHOWTEXT_ORDER_TYPE_IORATIO", play)
    match_time = _clean(data.get("match_time") or data.get("matchTime") or data.get("match_clock") or data.get("matchClock") or data.get("time"))
    if match_time:
        raw_out.setdefault("MATCH_TIME", match_time)
    if event:
        raw_out.setdefault("_EVENT", event)
        raw_out.setdefault("GID", event)
    if _clean(data.get("lid")):
        raw_out.setdefault("LID", _clean(data.get("lid")))
    if _clean(data.get("ecid")):
        raw_out.setdefault("ECID", _clean(data.get("ecid")))
    if _clean(data.get("wtype")):
        raw_out.setdefault("WTYPE", _clean(data.get("wtype")))
    if _clean(data.get("side")):
        raw_out.setdefault("SIDE", _clean(data.get("side")))
    if _clean(data.get("team_h")):
        raw_out.setdefault("TEAM_H", _clean(data.get("team_h")))
    if _clean(data.get("team_c")):
        raw_out.setdefault("TEAM_C", _clean(data.get("team_c")))

    source_order = {
        "ts": ts,
        "target": backend_name,
        "orderId": order_id,
        "sport": sport,
        "stake": str(data.get("amount") if data.get("amount") not in (None, "") else amount),
        "odds": odds,
        "league": league,
        "sides": match,
        "betType": bet_type,
        "line": line,
        "date": _clean(data.get("date")) or _today_date(ts),
        "time": _clean(data.get("order_time") or data.get("orderTime")) or _time_text(ts),
        "raw": raw_out,
    }

    if event:
        source_order["gid"] = event

    return {
        "backend_name": backend_name,
        "order_id": order_id,
        "league": league,
        "match": match,
        "play": play,
        "amount": amount,
        "time": f"{source_order['date']} {source_order['time']}",
        "sport": sport,
        "bet_type": bet_type,
        "line": line,
        "odds": odds,
        "source": _clean(data.get("source")) or "manual_local",
        "source_ts": ts,
        "source_order": source_order,
    }


def send_manual_signal(config: Dict[str, Any], data: Dict[str, Any], *, now_ts: Optional[int] = None) -> Dict[str, Any]:
    url = _clean(config.get("signal_webhook_url"))
    if not url:
        raise ValueError("signal_webhook_url 未配置")
    payload = build_manual_signal_payload(data, now_ts=now_ts)
    token = _clean(config.get("signal_webhook_token"))
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-TGGD-Token"] = token
    try:
        timeout_s = max(3, int(config.get("signal_webhook_timeout_seconds") or 8))
    except Exception:
        timeout_s = 8
    resp = requests.post(url, json=payload, headers=headers, timeout=timeout_s)
    body = resp.text or ""
    if not (200 <= int(resp.status_code) < 300):
        return {"ok": False, "message": f"http_{resp.status_code}:{body[:300]}", "payload": payload}
    try:
        remote = resp.json()
    except Exception:
        remote = {"raw": body[:500]}
    if isinstance(remote, dict) and remote.get("ok") is False:
        return {"ok": False, "message": _clean(remote.get("message") or remote.get("error") or body[:300]), "payload": payload, "remote": remote}
    return {"ok": True, "payload": payload, "remote": remote}


def payload_preview(data: Dict[str, Any], *, now_ts: Optional[int] = None) -> str:
    return json.dumps(build_manual_signal_payload(data, now_ts=now_ts), ensure_ascii=False, indent=2)
