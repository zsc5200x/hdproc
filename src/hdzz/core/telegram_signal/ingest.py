from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class MatchResolutionError(ValueError):
    pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _game_value(game: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(game.get(key))
        if value:
            return value
    return ""


def _normalized_name(value: Any) -> str:
    return "".join(_text(value).lower().split())


def resolve_signal_match(signal: Dict[str, Any], games: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = [game for game in games if isinstance(game, dict) and _game_value(game, "gid", "GID")]
    home_number = _text(signal.get("home_number"))
    away_number = _text(signal.get("away_number"))

    if home_number or away_number:
        matches = [
            game
            for game in rows
            if _game_value(game, "gnumH", "GNUM_H") == home_number
            and _game_value(game, "gnumC", "GNUM_C") == away_number
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise MatchResolutionError("ambiguous_number_match")
        raise MatchResolutionError("number_pair_not_found")

    home = _normalized_name(signal.get("home"))
    away = _normalized_name(signal.get("away"))
    league = _normalized_name(signal.get("league"))
    matches = []
    for game in rows:
        if _normalized_name(_game_value(game, "teamH", "TEAM_H")) != home:
            continue
        if _normalized_name(_game_value(game, "teamC", "TEAM_C")) != away:
            continue
        game_league = _normalized_name(_game_value(game, "league", "LEAGUE"))
        if league and game_league and league != game_league:
            continue
        matches.append(game)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise MatchResolutionError("ambiguous_name_match")
    raise MatchResolutionError("name_match_not_found")


def find_recent_confirmed_match(
    signal: Dict[str, Any],
    orders_dir: Path,
    *,
    target_name: str = "telegram_signal",
    received_ts: int,
    max_age_seconds: int = 6 * 60 * 60,
    max_lines: int = 500,
) -> Optional[Dict[str, Any]]:
    home_number = _text(signal.get("home_number"))
    away_number = _text(signal.get("away_number"))
    if not home_number or not away_number:
        return None

    path = Path(orders_dir) / f"{_text(target_name) or 'telegram_signal'}.jsonl"
    rows: deque = deque(maxlen=max(1, int(max_lines or 1)))
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
    except OSError:
        return None

    expected_home = _normalized_name(signal.get("home"))
    expected_away = _normalized_name(signal.get("away"))
    expected_league = _normalized_name(signal.get("league"))
    now_value = int(received_ts or 0)
    max_age = max(1, int(max_age_seconds or 1))

    for order in reversed(rows):
        raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
        if _game_value(raw, "GNUM_H", "gnumH") != home_number:
            continue
        if _game_value(raw, "GNUM_C", "gnumC") != away_number:
            continue
        if expected_home and _normalized_name(_game_value(raw, "TEAM_H", "teamH")) != expected_home:
            continue
        if expected_away and _normalized_name(_game_value(raw, "TEAM_C", "teamC")) != expected_away:
            continue
        if expected_league and _normalized_name(order.get("league")) != expected_league:
            continue
        try:
            order_ts = int(float(order.get("ts") or 0))
        except Exception:
            order_ts = 0
        if now_value > 0 and (order_ts <= 0 or order_ts > now_value + 300 or now_value - order_ts > max_age):
            continue
        gid = _game_value(order, "gid", "GID") or _game_value(raw, "GID", "_EVENT")
        if not gid:
            continue
        return {
            "gid": gid,
            "league": _text(order.get("league")) or _text(signal.get("league")),
            "teamH": _game_value(raw, "TEAM_H", "teamH") or _text(signal.get("home")),
            "teamC": _game_value(raw, "TEAM_C", "teamC") or _text(signal.get("away")),
            "gnumH": home_number,
            "gnumC": away_number,
        }
    return None


def _amount_text(value: Any) -> str:
    amount = float(value or 0)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def build_follow_order(
    signal: Dict[str, Any],
    game: Dict[str, Any],
    *,
    chat_id: Any,
    message_id: Any,
    chat_title: Any = "",
    received_ts: int,
    target_name: str = "telegram_signal",
) -> Dict[str, Any]:
    gid = _game_value(game, "gid", "GID")
    if not gid:
        raise MatchResolutionError("missing_gid")

    home = _game_value(game, "teamH", "TEAM_H") or _text(signal.get("home"))
    away = _game_value(game, "teamC", "TEAM_C") or _text(signal.get("away"))
    gnum_h = _game_value(game, "gnumH", "GNUM_H") or _text(signal.get("home_number"))
    gnum_c = _game_value(game, "gnumC", "GNUM_C") or _text(signal.get("away_number"))
    score_home = int(signal.get("score_home") or 0)
    score_away = int(signal.get("score_away") or 0)
    line = _text(signal.get("line"))
    odds = _text(signal.get("odds"))
    bet_type = _text(signal.get("bet_type"))
    selection = _text(signal.get("selection"))
    sides = f"{home} v {away} ({score_home} - {score_away})"

    raw = {
        "GTYPE": "FT" if _text(signal.get("sport")) == "足球" else "BK",
        "MARKET": "rb" if _text(signal.get("mode")) == "live" else "today",
        "WAGERSTYPE": bet_type,
        "ORDER_TYPE": selection,
        "ORDER_CON": _text(signal.get("line_text")),
        "ODDS": odds,
        "ioratio": odds,
        "_EVENT": gid,
        "GID": gid,
        "GNUM_H": gnum_h,
        "GNUM_C": gnum_c,
        "TEAM_H": home,
        "TEAM_C": away,
        "SCORE_H": str(score_home),
        "SCORE_C": str(score_away),
        "SHOWTEXT_TEAM": sides,
        "SHOWTEXT_TEAM_NUM": f"[{gnum_h}] vs [{gnum_c}]" if gnum_h and gnum_c else "",
        "SHOWTEXT_ORDER_TYPE_IORATIO": f"{line} @ {odds}".strip(),
        "_TG_SOURCE_CHAT_ID": _text(chat_id),
        "_TG_SOURCE_MESSAGE_ID": _text(message_id),
        "_TG_SOURCE_CHAT_TITLE": _text(chat_title),
        "_TG_SOURCE_MONITOR": _text(signal.get("monitor")),
        "_TG_SOURCE_MEMBER": _text(signal.get("member")),
        "_TG_RAW_TEXT": _text(signal.get("raw_text")),
    }
    return {
        "ts": int(received_ts),
        "target": _text(target_name) or "telegram_signal",
        "orderId": _text(signal.get("order_id")),
        "sport": _text(signal.get("sport")),
        "stake": _amount_text(signal.get("amount")),
        "odds": odds,
        "league": _text(signal.get("league")),
        "sides": sides,
        "betType": bet_type,
        "line": line,
        "date": _text(signal.get("date")),
        "time": _text(signal.get("time")),
        "gid": gid,
        "raw": raw,
        "_skip_signal_forward": True,
        "_signal_origin": "telegram_group",
    }
