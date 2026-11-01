"""Pure Pinnacle message and event parsers shared by all consumers."""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def metadata_status(name: str, league: str, kickoff: str) -> str:
    has_name = bool(name and not name.startswith("赛事 "))
    if has_name and league and kickoff:
        return "complete"
    if has_name or league or kickoff:
        return "partial"
    return "missing"


def iter_messages(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        if node.get("type") == "UPDATE_ODDS":
            yield node
            return
        for value in node.values():
            yield from iter_messages(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_messages(value)
    elif isinstance(node, str):
        text = node.strip()
        if text.startswith(("{", "[")):
            try:
                yield from iter_messages(json.loads(text))
            except (json.JSONDecodeError, RecursionError):
                pass


def iter_leaf_rows(node: Any) -> Iterable[list]:
    if isinstance(node, list):
        if len(node) >= 13 and all(
            not isinstance(value, (dict, list)) for value in node
        ):
            yield node
            return
        for value in node:
            yield from iter_leaf_rows(value)
    elif isinstance(node, dict):
        for value in node.values():
            yield from iter_leaf_rows(value)


def iter_event_records(container: Any) -> Iterable[Dict[str, str]]:
    """Yield full event metadata from PS3838 ``l`` / ``n`` containers."""
    if not isinstance(container, list):
        return
    for sport in container:
        if not isinstance(sport, list) or len(sport) < 3:
            continue
        sport_id = as_text(sport[0])
        leagues = sport[2]
        if not isinstance(leagues, list):
            continue
        for league in leagues:
            if not isinstance(league, list) or len(league) < 3:
                continue
            league_id = as_text(league[0])
            league_name = as_text(league[1])
            events = league[2]
            if not isinstance(events, list):
                continue
            for event in events:
                if not isinstance(event, list) or len(event) < 5:
                    continue
                event_id = as_text(event[0])
                if not event_id:
                    continue
                home = as_text(event[1])
                away = as_text(event[2])
                kickoff = ""
                try:
                    stamp = float(event[4])
                    if stamp > 100_000_000_000:
                        stamp /= 1000
                    if stamp > 0:
                        kickoff = datetime.fromtimestamp(
                            stamp
                        ).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError, OverflowError):
                    pass

                score_home = ""
                score_away = ""
                if len(event) > 9 and isinstance(event[9], list) and len(event[9]) >= 2:
                    score_home = as_text(event[9][0])
                    score_away = as_text(event[9][1])

                yield {
                    "eventId": event_id,
                    "home": home,
                    "away": away,
                    "sportId": sport_id,
                    "leagueId": league_id,
                    "league": league_name,
                    "kickoff": kickoff,
                    "scoreHome": score_home,
                    "scoreAway": score_away,
                    "matchMinute": as_text(event[15]) if len(event) > 15 else "",
                    "matchPeriod": as_text(event[16]) if len(event) > 16 else "",
                    "matchStatus": as_text(event[17]) if len(event) > 17 else "",
                }


def iter_events(container: Any) -> Iterable[Tuple[str, str, str, str, str]]:
    for record in iter_event_records(container):
        yield (
            record["eventId"],
            record["home"],
            record["away"],
            record["league"],
            record["kickoff"],
        )


def float_or_none(value: str) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number

