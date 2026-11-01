"""Candidate ranking and durable manual links for cross-provider fixtures.

Provider event IDs are opaque and scoped to their provider. Suggestions are
review aids only; this module never creates a mapping from a score.
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_TEAM_SUFFIXES = re.compile(
    r"\b(?:association football club|football club|soccer club|club|fc|sc|afc|cf)\b",
    re.IGNORECASE,
)
_MATCH_SPLIT = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)
_CROWN_DATE = re.compile(
    r"(?P<month>\d{1,2})[-/](?P<day>\d{1,2})\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?:\s*(?P<ampm>[ap])\.?m?\.?)?",
    re.IGNORECASE,
)


def _configured_timezone(env_name: str, default: str) -> ZoneInfo:
    """Load an IANA timezone while keeping the merge usable on minimal hosts."""
    name = os.environ.get(env_name, default).strip() or default
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(default)


# The providers render local times without carrying the timezone in the
# payload. These defaults match the live feeds; environment overrides keep the
# parser usable if a provider account is configured for another display zone.
PINNACLE_TIMEZONE = _configured_timezone("PS3838_EVENT_TIMEZONE", "Asia/Shanghai")
CROWN_TIMEZONE = _configured_timezone("CROWN_EVENT_TIMEZONE", "Etc/GMT+4")
MERGE_DISPLAY_TIMEZONE = _configured_timezone("MERGE_DISPLAY_TIMEZONE", "Asia/Shanghai")


def _timezone_name(value: ZoneInfo) -> str:
    return str(getattr(value, "key", value))


class MappingConflict(ValueError):
    """A user-visible conflict when either source fixture is already linked."""


_CN_TEAM_SUFFIXES = (
    "足球俱乐部", "体育俱乐部", "足球会", "体育会", "俱乐部",
    "预备队", "后备队", "青年队", "女足", "女子",
)
_STATE_CODES = frozenset({
    "rj", "rs", "pa", "sp", "mg", "ba", "ce", "go", "pr", "sc", "pe", "es",
    "mt", "ms", "df", "am", "ro", "ac", "ap", "rr", "to", "ma", "pi", "rn",
    "pb", "al", "se",
})
_TOKEN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+")
_LEAGUE_SYNONYMS = {
    "甲组": "甲级", "乙组": "乙级", "丙组": "丙级",
    "杯赛": "杯", "足球": "", "足联": "", "职业": "",
    "解放者": "自由", "全国": "国家", "女足": "女子", "欧足联": "欧洲",
}
_EN_WORDS = {
    "brazil": "巴西", "india": "印度", "italy": "意大利", "spain": "西班牙",
    "england": "英格兰", "germany": "德国", "france": "法国", "portugal": "葡萄牙",
    "netherlands": "荷兰", "belgium": "比利时", "scotland": "苏格兰",
    "argentina": "阿根廷", "colombia": "哥伦比亚", "mexico": "墨西哥",
    "uruguay": "乌拉圭", "paraguay": "巴拉圭", "chile": "智利", "peru": "秘鲁",
    "ecuador": "厄瓜多尔", "venezuela": "委内瑞拉", "united states": "美国",
    "usa": "美国", "women": "女子", "womens": "女子", "cup": "杯",
    "league": "联赛", "super": "超级", "first": "甲级", "second": "乙级",
    "third": "丙级", "youth": "青年", "reserve": "预备", "reserves": "预备",
    "division": "", "primera": "甲级",
    "austria": "奥地利", "el salvador": "萨尔瓦多", "jordan": "约旦",
    "kosovo": "科索沃", "czech republic": "捷克", "czechia": "捷克",
}


@lru_cache(maxsize=32768)
def _normalize_name_text(text: str, strip_team_suffixes: bool) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(character for character in text if not unicodedata.combining(character))
    if strip_team_suffixes:
        text = _TEAM_SUFFIXES.sub(" ", text)
        for suffix in _CN_TEAM_SUFFIXES:
            text = text.replace(suffix, "")
        if text.endswith("队"):
            text = text[:-1]
    tokens = _TOKEN.findall(text)
    if strip_team_suffixes and len(tokens) > 1 and tokens[-1] in _STATE_CODES:
        tokens.pop()
    return "".join(tokens)


def normalize_name(value: Any, *, strip_team_suffixes: bool = True) -> str:
    """Normalize a team label, dropping club suffixes and state/region codes."""
    return _normalize_name_text(str(value or ""), strip_team_suffixes)


@lru_cache(maxsize=4096)
def _normalize_league_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    text = "".join(character for character in text if not unicodedata.combining(character))
    for english, chinese in sorted(_EN_WORDS.items(), key=lambda item: -len(item[0])):
        if chinese:
            text = re.sub(rf"\b{re.escape(english)}\b", chinese, text)
    tokens = _TOKEN.findall(text)
    if len(tokens) >= 2 and len(tokens[0]) >= 2 and tokens[1].startswith(tokens[0]):
        tokens[1] = tokens[1][len(tokens[0]):]
    collapsed: list[str] = []
    for token in tokens:
        if not collapsed or collapsed[-1] != token:
            collapsed.append(token)
    text = "".join(collapsed)
    for source, target in _LEAGUE_SYNONYMS.items():
        text = text.replace(source, target)
    return text


def normalize_league(value: Any) -> str:
    """Normalize a league label, including common translation synonyms."""
    return _normalize_league_text(str(value or ""))


# Competition class markers.  A senior Crown league can field exactly the same
# club names as a Pinnacle youth / reserve / women's competition (for example
# 土耳其超级联赛 against 土耳其 - 超级联赛U19).  Name similarity alone cannot tell
# them apart, and mixing them pairs water levels from two different matches, so
# the class markers are compared explicitly by the suggester and by the
# cross-book scanner.
_GENDER_MARKERS = re.compile(r"女子|女足|women'?s?|ladies|w-league|\(女\)|（女）", re.IGNORECASE)
_YOUTH_MARKERS = re.compile(r"(?<![a-z])u\s?-?\s?(?:1[5-9]|2[0-4])(?!\d)|青年|青少年", re.IGNORECASE)
_RESERVE_MARKERS = re.compile(r"预备|后备|储备|reserve|reserves", re.IGNORECASE)


def competition_profile(value: Any) -> dict[str, str]:
    """Classify a league label by gender, age group and squad level."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return {
        "gender": "women" if _GENDER_MARKERS.search(text) else "men",
        "age": "youth" if _YOUTH_MARKERS.search(text) else "senior",
        "squad": "reserve" if _RESERVE_MARKERS.search(text) else "senior",
    }


def competition_conflict(left: Any, right: Any) -> bool:
    """True when two league labels describe different competition classes."""
    return competition_profile(left) != competition_profile(right)


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if a in b or b in a:
        ratio = max(ratio, min(len(a), len(b)) / max(len(a), len(b)) * 0.92)
    return ratio


def _similarity(left: Any, right: Any) -> float:
    return _ratio(normalize_name(left), normalize_name(right))


def _alias_hit(
    left: Any, right: Any, aliases: set | None, normalize, *, symmetric: bool = True
) -> bool:
    if not aliases:
        return False
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return False
    if symmetric:
        return (a, b) in aliases or (b, a) in aliases
    # League aliases are directional: 甲级 and 乙级 must never match just
    # because a confirmed pair happens to be stored in the opposite order.
    return (a, b) in aliases


def _team_similarity(left: Any, right: Any, aliases: set | None = None) -> float:
    if _alias_hit(left, right, aliases, normalize_name):
        return 1.0
    return _similarity(left, right)


def _league_similarity(left: Any, right: Any, aliases: set | None = None) -> float:
    if _alias_hit(left, right, aliases, normalize_league, symmetric=False):
        return 1.0
    return _ratio(normalize_league(left), normalize_league(right))


def build_alias_index(league_pairs: Iterable[tuple[Any, Any]] = (),
                      team_pairs: Iterable[tuple[Any, Any]] = ()) -> dict[str, set[tuple[str, str]]]:
    """Build exact alias sets from already-confirmed cross-provider mappings."""
    leagues: set[tuple[str, str]] = set()
    teams: set[tuple[str, str]] = set()
    for left, right in league_pairs:
        a, b = normalize_league(left), normalize_league(right)
        if a and b:
            leagues.add((a, b))
    for left, right in team_pairs:
        a, b = normalize_name(left), normalize_name(right)
        if a and b:
            teams.add((a, b))
    return {"leagues": leagues, "teams": teams}


def _teams(event: dict[str, Any], source: str) -> tuple[str, str]:
    home = str(event.get("home") or "").strip()
    away = str(event.get("away") or "").strip()
    if source == "pinnacle" and (not home or not away):
        match = _MATCH_SPLIT.split(str(event.get("name") or ""), maxsplit=1)
        if len(match) == 2:
            home, away = home or match[0].strip(), away or match[1].strip()
    return home, away


def prepare_pinnacle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a dashboard Pinnacle event for the merge API."""
    home, away = _teams(event, "pinnacle")
    kickoff = str(event.get("kickoff") or "")
    normalized_kickoff = _format_display_datetime(_parse_pinnacle_datetime(kickoff))
    return {
        "id": str(event.get("id") or ""),
        "name": str(event.get("name") or ""),
        "home": home,
        "away": away,
        "league": str(event.get("league") or ""),
        "sportId": str(event.get("sportId") or ""),
        "leagueId": str(event.get("leagueId") or ""),
        "kickoff": kickoff,
        "normalizedKickoff": normalized_kickoff,
        "normalizedTimezone": _timezone_name(MERGE_DISPLAY_TIMEZONE),
        "sourceTimezone": _timezone_name(PINNACLE_TIMEZONE),
        "isLive": bool(event.get("isLive")),
        "lastCapturedAt": str(event.get("lastCapturedAt") or ""),
        "scoreHome": str(event.get("scoreHome") or ""),
        "scoreAway": str(event.get("scoreAway") or ""),
        "minute": str(event.get("matchMinute", event.get("minute")) or ""),
    }


def prepare_crown_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Crown event for the merge API while retaining its IDs."""
    home, away = _teams(event, "crown")
    start_label = str(event.get("startLabel", event.get("start_label")) or "")
    normalized_kickoff = _format_display_datetime(_parse_crown_datetime(start_label))
    return {
        "id": str(event.get("id") or ""),
        "name": str(event.get("name") or ""),
        "home": home,
        "away": away,
        "league": str(event.get("league") or ""),
        "leagueId": str(event.get("leagueId", event.get("league_id")) or ""),
        "startLabel": start_label,
        "normalizedKickoff": normalized_kickoff,
        "normalizedTimezone": _timezone_name(MERGE_DISPLAY_TIMEZONE),
        "sourceTimezone": _timezone_name(CROWN_TIMEZONE),
        "isLive": bool(event.get("isLive", event.get("is_live"))),
        "isFinished": bool(event.get("isFinished", event.get("is_finished"))),
        "score": str(event.get("score") or ""),
        "minute": str(event.get("minute") or ""),
    }


def _parse_pinnacle_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PINNACLE_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _parse_crown_datetime(value: Any, reference: datetime | None = None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CROWN_TIMEZONE)
        return parsed.astimezone(timezone.utc)

    match = _CROWN_DATE.search(text)
    if not match:
        return None
    month, day = int(match.group("month")), int(match.group("day"))
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    ampm = (match.group("ampm") or "").lower().replace(".", "")
    if ampm:
        # Crown live rows use 00:01a / 00:01p for 12:01 AM / PM.
        if hour == 0:
            hour = 12
        if not 1 <= hour <= 12:
            return None
        if ampm == "a" and hour == 12:
            hour = 0
        elif ampm == "p" and hour != 12:
            hour += 12
    elif not 0 <= hour <= 23:
        return None

    reference_local = (reference or datetime.now(timezone.utc)).astimezone(CROWN_TIMEZONE)
    options: list[datetime] = []
    for year in (reference_local.year - 1, reference_local.year, reference_local.year + 1):
        try:
            options.append(datetime(year, month, day, hour, minute, tzinfo=CROWN_TIMEZONE))
        except ValueError:
            continue
    if not options:
        return None
    selected = min(options, key=lambda item: abs((item - reference_local).total_seconds()))
    return selected.astimezone(timezone.utc)


def _format_display_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(MERGE_DISPLAY_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


# Auto-confirm only links fixtures whose kickoff differs by at most this much.
# A higher name score must not buy a wrong match a place in the mapping: the
# Crown senior fixture and the Pinnacle U19 fixture share both club names yet
# start five hours apart.
AUTO_MAP_TIME_MINUTES = 15.0
# Cross-book pairing tolerates a slightly wider drift because the two feeds can
# round or reschedule within the same day, but never a different kickoff slot.
PAIR_TIME_TOLERANCE_MINUTES = 60.0


def _parse_display_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MERGE_DISPLAY_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def crown_kickoff_datetime(event: dict[str, Any]) -> datetime | None:
    """Kickoff of a prepared or raw Crown event, in UTC when known."""
    parsed = _parse_display_datetime(
        event.get("normalizedKickoff") or event.get("normalized_kickoff")
    )
    if parsed is not None:
        return parsed
    return _parse_crown_datetime(
        event.get("startLabel") or event.get("start_label")
    )


def pinnacle_kickoff_datetime(event: dict[str, Any]) -> datetime | None:
    """Kickoff of a prepared or raw Pinnacle event, in UTC when known."""
    parsed = _parse_display_datetime(
        event.get("normalizedKickoff") or event.get("normalized_kickoff")
    )
    if parsed is not None:
        return parsed
    return _parse_pinnacle_datetime(event.get("kickoff"))


def fixture_pair_problem(
    crown: dict[str, Any], pinnacle: dict[str, Any]
) -> str | None:
    """Return why two events cannot be the same fixture, or ``None``.

    ``"competition"`` covers youth / reserve / women's fixtures that mirror a
    senior club name; ``"kickoff"`` covers identical names at a different slot.
    Unknown labels or times are not treated as a conflict.
    """
    if competition_conflict(crown.get("league"), pinnacle.get("league")):
        return "competition"
    crown_kickoff = crown_kickoff_datetime(crown)
    pinnacle_kickoff = pinnacle_kickoff_datetime(pinnacle)
    if crown_kickoff is None or pinnacle_kickoff is None:
        return None
    gap = abs((crown_kickoff - pinnacle_kickoff).total_seconds()) / 60.0
    if gap > PAIR_TIME_TOLERANCE_MINUTES:
        return "kickoff"
    return None


def _pinnacle_date(value: Any) -> date | None:
    parsed = _parse_pinnacle_datetime(value)
    return parsed.date() if parsed else None


def _crown_date_near(value: Any, reference: date | None) -> date | None:
    if reference is not None:
        reference_datetime = datetime.combine(reference, datetime.min.time(), tzinfo=timezone.utc)
        parsed = _parse_crown_datetime(value, reference_datetime)
        if parsed is not None:
            return parsed.date()
    text = str(value or "").strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    match = _CROWN_DATE.search(text)
    if not match:
        return None
    month, day = int(match.group("month")), int(match.group("day"))
    if not reference:
        return None
    options: list[date] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            continue
    if not options:
        return None
    return min(options, key=lambda item: abs((item - reference).days))


def _fixture_features(
    pinnacle: dict[str, Any], crown: dict[str, Any], aliases: dict | None = None
) -> tuple[float, float, int | None, bool, float, float, float | None, bool]:
    p_home, p_away = _teams(pinnacle, "pinnacle")
    c_home, c_away = _teams(crown, "crown")
    team_aliases = (aliases or {}).get("teams")
    league_aliases = (aliases or {}).get("leagues")
    direct_home = _team_similarity(p_home, c_home, team_aliases)
    direct_away = _team_similarity(p_away, c_away, team_aliases)
    reverse_home = _team_similarity(p_home, c_away, team_aliases)
    reverse_away = _team_similarity(p_away, c_home, team_aliases)
    direct = (direct_home + direct_away) / 2
    reverse = (reverse_home + reverse_away) / 2
    reversed_order = reverse > direct
    team_score = max(direct, reverse)
    alias_hit = any(
        _alias_hit(left, right, team_aliases, normalize_name)
        for left, right in ((p_home, c_home), (p_away, c_away), (p_home, c_away), (p_away, c_home))
    )

    # prepared merge events carry the same Beijing-normalized kickoff; use it
    # for both sides so the comparison never depends on a provider's raw zone.
    p_datetime = _parse_display_datetime(
        pinnacle.get("normalizedKickoff") or pinnacle.get("normalized_kickoff")
    ) or _parse_pinnacle_datetime(pinnacle.get("kickoff"))
    c_datetime = _parse_display_datetime(
        crown.get("normalizedKickoff") or crown.get("normalized_kickoff")
    ) or _parse_crown_datetime(
        crown.get("startLabel", crown.get("start_label")), p_datetime
    )
    p_date = p_datetime.date() if p_datetime else _pinnacle_date(pinnacle.get("kickoff"))
    c_date = c_datetime.date() if c_datetime else _crown_date_near(
        crown.get("startLabel", crown.get("start_label")), p_date
    )
    day_delta = abs((c_date - p_date).days) if p_date and c_date else None
    kickoff_delta_minutes = (
        round(abs((c_datetime - p_datetime).total_seconds()) / 60.0, 1)
        if p_datetime and c_datetime
        else None
    )
    league_score = _league_similarity(
        pinnacle.get("league"), crown.get("league"), league_aliases
    )
    same_live_state = bool(pinnacle.get("isLive")) == bool(
        crown.get("isLive", crown.get("is_live"))
    )
    return (
        team_score,
        league_score,
        day_delta,
        reversed_order,
        min(direct_home, direct_away),
        min(reverse_home, reverse_away),
        kickoff_delta_minutes,
        alias_hit,
    )


def _time_score(kickoff_delta_minutes: float | None) -> float | None:
    """Convert normalized kickoff distance into a bounded evidence score."""
    if kickoff_delta_minutes is None:
        return None
    if kickoff_delta_minutes <= 10:
        return 1.0
    if kickoff_delta_minutes <= 60:
        return 0.75
    if kickoff_delta_minutes <= 180:
        return 0.40
    if kickoff_delta_minutes <= 720:
        return 0.15
    return 0.0


def _candidate(
    pinnacle: dict[str, Any], crown: dict[str, Any], aliases: dict | None = None
) -> dict[str, Any] | None:
    (
        team_score,
        league_score,
        day_delta,
        reversed_order,
        direct_floor,
        _reverse_floor,
        kickoff_delta_minutes,
        alias_hit,
    ) = _fixture_features(pinnacle, crown, aliases)
    p_home, p_away = _teams(pinnacle, "pinnacle")
    c_home, c_away = _teams(crown, "crown")
    if not all((p_home, p_away, c_home, c_away)):
        return None
    # Youth, reserve and women's competitions reuse the senior club names, so a
    # perfect team-score is not evidence that the two rows belong together.
    if competition_conflict(pinnacle.get("league"), crown.get("league")):
        return None

    # Crown and Pinnacle render local times in different zones. When both
    # values parse, the normalized time is stronger evidence than local dates;
    # the date score remains a fallback for incomplete feeds.
    date_score = 1.0 if day_delta == 0 else 0.55 if day_delta == 1 else 0.0
    time_score = _time_score(kickoff_delta_minutes)
    temporal_score = time_score if time_score is not None else date_score
    same_live_state = bool(pinnacle.get("isLive")) == bool(
        crown.get("isLive", crown.get("is_live"))
    )
    score = (
        team_score * 0.76
        + league_score * 0.10
        + temporal_score * 0.10
        + (0.04 if same_live_state else 0.0)
    )

    # Keep an alternate only when team text or league/date evidence gives the
    # operator something to inspect. The page can still offer full manual search.
    if team_score < 0.34 and not (league_score >= 0.70 and date_score >= 0.55):
        return None

    reasons: list[str] = []
    if alias_hit:
        reasons.append("命中已确认的名称别名")
    if reversed_order:
        reasons.append("球队名称较接近，但主客顺序相反")
    elif direct_floor >= 0.82:
        reasons.append("主客队名称高度相似")
    elif team_score >= 0.5:
        reasons.append("球队名称较相似")
    if league_score >= 0.65:
        reasons.append("联赛名称相似")
    if day_delta == 0:
        reasons.append("开赛日期相同")
    elif day_delta == 1:
        reasons.append("开赛日期相差一天")
    if kickoff_delta_minutes is not None:
        if kickoff_delta_minutes <= 10:
            reasons.append("统一时区后开赛时间吻合")
        else:
            reasons.append(f"统一时区后开赛时间相差 {kickoff_delta_minutes:g} 分钟")
    if same_live_state:
        reasons.append("双方比赛状态一致")

    time_conflict = (
        kickoff_delta_minutes is not None
        and kickoff_delta_minutes > AUTO_MAP_TIME_MINUTES
    )
    if time_conflict:
        reasons.append("开赛时间不符，不能自动确认")
    confidence = (
        "high"
        if not time_conflict
        and (
            alias_hit
            or (team_score >= 0.86 and direct_floor >= 0.82 and not reversed_order)
        )
        else "medium"
        if team_score >= 0.60
        else "low"
    )
    return {
        "pinnacleId": str(pinnacle.get("id") or ""),
        "crownId": str(crown.get("id") or ""),
        "score": round(score * 100, 1),
        "confidence": confidence,
        "reasons": reasons,
        "reversedOrder": reversed_order,
        "kickoffDeltaMinutes": kickoff_delta_minutes,
        "timeConflict": time_conflict,
        "teamScore": round(team_score, 3),
        "leagueScore": round(league_score, 3),
        "aliasHit": alias_hit,
    }


def rank_candidates(
    pinnacle_events: Iterable[dict[str, Any]],
    crown_events: Iterable[dict[str, Any]],
    limit: int = 5,
    aliases: dict | None = None,
    league_mapping: dict[str, str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return advisory Crown candidates grouped by Pinnacle event ID.

    Cross-provider IDs are intentionally never compared. Each Pinnacle event
    receives up to ``limit`` scored candidates, ordered best first; no mapping
    is written or inferred automatically.

    ``league_mapping`` (Crown league ID -> Pinnacle league ID) restricts the
    scan to events the page can actually pair, which keeps the wider multi-day
    pool cheap to rank.
    """
    max_candidates = max(1, min(20, int(limit)))
    crowns = [event for event in crown_events if str(event.get("id") or "")]
    result: dict[str, list[dict[str, Any]]] = {}
    for pinnacle in pinnacle_events:
        pinnacle_id = str(pinnacle.get("id") or "")
        if not pinnacle_id:
            continue
        pinnacle_league = str(pinnacle.get("leagueId") or "")
        ranked = []
        for crown in crowns:
            if league_mapping is not None:
                crown_league = str(
                    crown.get("leagueId") or crown.get("league_id") or ""
                )
                if league_mapping.get(crown_league) != pinnacle_league:
                    continue
            item = _candidate(pinnacle, crown, aliases)
            if item is not None:
                ranked.append(item)
        ranked.sort(key=lambda item: (-item["score"], item["crownId"]))
        result[pinnacle_id] = ranked[:max_candidates]
    return result


def summarize_leagues(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group current provider events by that provider's league ID."""
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        league_id = str(event.get("leagueId") or event.get("league_id") or "")
        league_name = str(event.get("league") or "未命名联赛")
        if league_id not in groups:
            groups[league_id] = {"id": league_id, "name": league_name, "eventCount": 0}
        elif groups[league_id]["name"] in ("", "未命名联赛") and league_name:
            groups[league_id]["name"] = league_name
        groups[league_id]["eventCount"] += 1
    return sorted(
        groups.values(),
        key=lambda item: (normalize_name(item["name"]), item["id"]),
    )


_LEAGUE_COUNTRY_SPLIT = re.compile(r"\s+[-–—]\s+")


def league_country(name: Any) -> str:
    """Country/region prefix used by a Pinnacle league label.

    Pinnacle writes ``国家 - 联赛``; Crown usually concatenates the same country
    with the competition name.  Translate the English country labels that still
    appear in some Pinnacle rows so both sides can be compared.
    """
    parts = _LEAGUE_COUNTRY_SPLIT.split(str(name or "").strip(), maxsplit=1)
    if len(parts) != 2:
        return ""
    prefix = parts[0].strip()
    if not prefix:
        return ""
    for english, chinese in sorted(_EN_WORDS.items(), key=lambda item: -len(item[0])):
        if chinese:
            prefix = re.sub(rf"\b{re.escape(english)}\b", chinese, prefix, flags=re.IGNORECASE)
    return prefix


def crown_league_country(name: Any, countries: Iterable[str]) -> str:
    """Longest known Pinnacle country prefix that starts a Crown league name."""
    text = str(name or "").strip()
    for country in sorted({item for item in countries if item}, key=len, reverse=True):
        if text.startswith(country):
            return country
    return ""


def event_count_score(crown_count: int, pinnacle_count: int) -> float:
    """How similar the two leagues' current fixture counts are."""
    if crown_count <= 0 or pinnacle_count <= 0:
        return 0.0
    difference = abs(int(crown_count) - int(pinnacle_count))
    if difference == 0:
        return 1.0
    if difference == 1:
        return 0.6
    if difference == 2:
        return 0.3
    return 0.0


def rank_league_candidates(
    pinnacle_events: Iterable[dict[str, Any]],
    crown_events: Iterable[dict[str, Any]],
    limit: int = 5,
    aliases: dict | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Rank Pinnacle leagues for each Crown league as human-reviewed suggestions.

    League labels provide the baseline, with confirmed aliases and fixture
    evidence inside each pair contributing support. No mapping is created here.
    """
    pinnacle = list(pinnacle_events)
    crown = list(crown_events)
    pinnacle_leagues = summarize_leagues(pinnacle)
    crown_leagues = summarize_leagues(crown)
    max_candidates = max(1, min(20, int(limit)))
    league_aliases = (aliases or {}).get("leagues")
    raw_countries = {
        str(league["id"]): league_country(league["name"]) for league in pinnacle_leagues
    }
    known_countries = sorted(
        {country for country in raw_countries.values() if country},
        key=len,
        reverse=True,
    )
    # Some Pinnacle rows already concatenate the country ("丹麦- 超级联赛",
    # "爱尔兰超级联赛"); reuse the country prefixes learned from the rows that
    # still use "国家 - 联赛".
    pinnacle_countries = {
        str(league["id"]): raw_countries[str(league["id"])]
        or crown_league_country(league["name"], known_countries)
        for league in pinnacle_leagues
    }
    country_prefixes = sorted(
        {country for country in pinnacle_countries.values() if country},
        key=len,
        reverse=True,
    )
    crown_countries = {
        str(league["id"]): crown_league_country(league["name"], country_prefixes)
        for league in crown_leagues
    }

    # Comparing every Pinnacle event with every Crown event is unnecessary when
    # the league labels already expose different countries.
    pinnacle_event_countries = {
        str(event.get("id") or ""): league_country(event.get("league"))
        for event in pinnacle
    }
    crown_event_countries = {
        str(event.get("id") or ""): crown_league_country(event.get("league"), country_prefixes)
        for event in crown
    }

    event_evidence: dict[tuple[str, str], list[float]] = {}
    for p_event in pinnacle:
        p_league_id = str(p_event.get("leagueId") or "")
        if not p_league_id:
            continue
        p_country = pinnacle_event_countries.get(str(p_event.get("id") or ""), "")
        for c_event in crown:
            c_league_id = str(c_event.get("leagueId") or c_event.get("league_id") or "")
            if not c_league_id:
                continue
            c_country = crown_event_countries.get(str(c_event.get("id") or ""), "")
            if p_country and c_country and p_country != c_country:
                continue
            candidate = _candidate(p_event, c_event, aliases)
            if candidate and candidate["score"] >= 65:
                event_evidence.setdefault((c_league_id, p_league_id), []).append(
                    float(candidate["score"])
                )

    result: dict[str, list[dict[str, Any]]] = {}
    for crown_league in crown_leagues:
        crown_id = str(crown_league["id"])
        if not crown_id:
            continue
        ranked: list[dict[str, Any]] = []
        for pinnacle_league in pinnacle_leagues:
            pinnacle_id = str(pinnacle_league["id"])
            if not pinnacle_id:
                continue
            # Never suggest a youth / reserve / women's league as the partner of
            # a senior league just because the translated names are similar.
            if competition_conflict(crown_league["name"], pinnacle_league["name"]):
                continue
            pinnacle_country = pinnacle_countries.get(pinnacle_id, "")
            crown_country = crown_countries.get(crown_id, "")
            if pinnacle_country and crown_country and pinnacle_country != crown_country:
                # A known country mismatch is a much stronger signal than a
                # shared "乙级联赛" suffix.  Confirmed aliases still win through
                # the existing mapping page; auto suggestions stay in-country.
                continue
            same_country = bool(
                pinnacle_country and crown_country and pinnacle_country == crown_country
            )
            name_score = _league_similarity(
                pinnacle_league["name"], crown_league["name"], league_aliases
            )
            evidence = sorted(
                event_evidence.get((crown_id, pinnacle_id), []), reverse=True
            )
            event_score = sum(evidence[:3]) / min(len(evidence), 3) if evidence else 0.0
            base_score = (name_score * 100.0) if not evidence else (
                name_score * 45.0 + event_score * 0.55
            )
            score = base_score
            reasons: list[str] = []
            if name_score >= 0.65:
                reasons.append("联赛名称相似")
            elif name_score >= 0.35:
                reasons.append("联赛名称部分相似")
            if _alias_hit(
                pinnacle_league["name"], crown_league["name"], league_aliases, normalize_league,
                symmetric=False,
            ):
                reasons.append("命中已确认的联赛别名")
            if same_country:
                score += 8.0
                reasons.append(f"同一国家（{crown_country}）")
            crown_count = int(crown_league.get("eventCount") or 0)
            pinnacle_count = int(pinnacle_league.get("eventCount") or 0)
            count_score = event_count_score(crown_count, pinnacle_count)
            if count_score:
                score += 14.0 * count_score
                if count_score >= 0.999:
                    reasons.append(f"赛事数量一致（{crown_count} 场）")
                else:
                    reasons.append(
                        f"赛事数量接近（{crown_count} / {pinnacle_count} 场）"
                    )
            elif crown_count and pinnacle_count and abs(crown_count - pinnacle_count) >= 3:
                # 3-vs-7 is a strong signal that these are different tiers, not
                # merely incomplete feed coverage.
                score -= 12.0
                reasons.append(f"赛事数量差异较大（{crown_count} / {pinnacle_count} 场）")
            if len(evidence) >= 2:
                reasons.append(f"组内有 {len(evidence)} 场赛事候选吻合")
            elif evidence:
                reasons.append("组内有 1 场赛事候选吻合")
            ranked.append({
                "crownLeagueId": crown_id,
                "pinnacleLeagueId": pinnacle_id,
                "score": round(min(100.0, max(0.0, score)), 1),
                "baseScore": round(min(100.0, base_score), 1),
                "evidenceCount": len(evidence),
                "reasons": reasons or ["可人工核对联赛和组内赛事"],
            })
        ranked.sort(key=lambda item: (-item["score"], item["pinnacleLeagueId"]))
        result[crown_id] = ranked[:max_candidates]
    return result


def _snapshot(event: dict[str, Any], source: str) -> dict[str, Any]:
    if source == "pinnacle":
        return prepare_pinnacle_event(event)
    return prepare_crown_event(event)


