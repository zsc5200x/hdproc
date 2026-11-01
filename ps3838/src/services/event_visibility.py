"""Existing dashboard event and market visibility rules."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
from src.core.constants import (FULL_TIME_STAGE, SUPPORTED_MARKETS, FINISHED_MATCH_STATUSES, LIVE_EVENT_STALE_SECONDS, LIVE_EVENT_LATE_MINUTE, LIVE_EVENT_MAX_WALL_MINUTES)
from src.providers.pinnacle.parser import as_text


def is_full_time_market(stage: Any, market: Any) -> bool:
    """Return whether a row belongs to a supported full-time market."""
    return as_text(stage) == FULL_TIME_STAGE and as_text(market) in SUPPORTED_MARKETS


def parse_kickoff(value: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse PS3838 kickoff values into the dashboard's local timezone."""
    text = as_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    local_now = now or datetime.now().astimezone()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=local_now.tzinfo)
    return parsed.astimezone(local_now.tzinfo)


def match_elapsed_minute(event: Dict[str, Any]) -> Optional[int]:
    """Return an approximate elapsed match minute for live-event staleness."""
    period = as_text(event.get("matchPeriod")).upper()
    if period.startswith("END"):
        return 999
    digits = "".join(
        character
        for character in as_text(event.get("matchMinute"))
        if character.isdigit()
    )
    if not digits:
        return None
    minute = int(digits)
    if period.startswith("2H"):
        minute += 45
    elif period.startswith("ET") or period.startswith("HT ET"):
        minute += 90
    return minute


def event_is_finished(event: Dict[str, Any]) -> bool:
    period = as_text(event.get("matchPeriod")).upper()
    status = as_text(event.get("matchStatus")).upper()
    return period.startswith("END") or status in FINISHED_MATCH_STATUSES


def live_event_is_stale(
    event: Dict[str, Any], now: Optional[datetime] = None
) -> bool:
    """Detect ended events that vanished without a final match-state message."""
    if not bool(event.get("isLive")) or event_is_finished(event):
        return False
    local_now = now or datetime.now().astimezone()
    last_captured = parse_kickoff(as_text(event.get("lastCapturedAt")), local_now)
    if last_captured is None:
        return False
    idle_seconds = (local_now - last_captured).total_seconds()
    if idle_seconds < LIVE_EVENT_STALE_SECONDS:
        return False
    elapsed_minute = match_elapsed_minute(event)
    if elapsed_minute is not None and elapsed_minute >= LIVE_EVENT_LATE_MINUTE:
        return True
    kickoff = parse_kickoff(as_text(event.get("kickoff")), local_now)
    return bool(
        kickoff
        and (local_now - kickoff).total_seconds()
        >= LIVE_EVENT_MAX_WALL_MINUTES * 60
    )


def event_scope(event: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """Return the dashboard display scope for an event.

    Only live events and events that have not started today are shown. The
    categories are mutually exclusive so metric totals do not count an event
    twice.
    """
    local_now = now or datetime.now().astimezone()
    if event_is_finished(event) or live_event_is_stale(event, local_now):
        return ""
    if bool(event.get("isLive")):
        return "live"
    kickoff = parse_kickoff(as_text(event.get("kickoff")), local_now)
    if kickoff and kickoff.date() == local_now.date() and kickoff >= local_now:
        return "today"
    return ""


def event_is_visible_today_upcoming(
    event: Dict[str, Any], now: Optional[datetime] = None
) -> bool:
    """Return whether an event is currently shown as a today-upcoming event."""
    return event_scope(event, now) == "today"


def event_has_display_metadata(event: Dict[str, Any]) -> bool:
    """Return whether an event has enough identity to render without placeholders."""
    name = as_text(event.get("name"))
    league = as_text(event.get("league"))
    kickoff = parse_kickoff(as_text(event.get("kickoff")))
    return bool(
        name
        and not name.startswith("赛事 ")
        and league
        and league != "联赛未知"
        and kickoff
    )

