"""Cross-book same-line arbitrage detection for confirmed fixtures.

Both providers are normalised to Hong Kong water by their collectors, so a
pair of opposite selections on the same line pays out
``1/(1+crown) + 1/(1+pinnacle)``.  A sum below 1 is a strict, no-risk
arbitrage; the shortfall is the edge.

Only identical lines are paired here.  A cross-line combination (for example
Crown home -1 against Pinnacle home +1) is a middle: it can double up on some
scorelines but cannot guarantee a profit, so it must not be reported as an
arbitrage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from src.services.fixture_matching import fixture_pair_problem


MARKET_CODES = {"handicap": "2", "total": "3"}
MARKET_LABELS = {"2": "让球", "3": "大小球"}
SIDE_LABELS = {
    "2": {"home": "主", "away": "客"},
    "3": {"over": "大", "under": "小"},
}
TIER_ARB = 0.005
TIER_CRITICAL = 0.0
TIER_NEAR = -0.03
# An in-play pair is only actionable while both quotes are alive. A live Crown
# price against a Pinnacle row that stopped updating before kickoff can look
# like a huge arbitrage (for example 1.02 against a pre-match 1.56) while the
# Pinnacle bet cannot be placed any more. Rows are refreshed on every feed
# heartbeat, so this is a presence signal rather than a "price changed" signal.
LIVE_QUOTE_MAX_AGE_SECONDS = 180


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_line(value: Any) -> Optional[float]:
    """Parse a line string into the signed home-perspective number.

    ``0.5 / 1`` (Crown) and ``0.75`` (Pinnacle) both become ``0.75``.
    A leading minus applies to both halves, matching the collectors.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    for dash in ("\u2212", "\u2013", "\u2014"):
        text = text.replace(dash, "-")
    text = "".join(text.split())
    if not text:
        return None
    parts = text.split("/")
    if len(parts) > 2 or any(not part for part in parts):
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        if parts[0].startswith("-") and not parts[1].startswith(("+", "-")):
            numbers[1] = -abs(numbers[1])
        elif parts[0].startswith("+") and not parts[1].startswith(("+", "-")):
            numbers[1] = abs(numbers[1])
    return sum(numbers) / len(numbers)


def _trim(number: float) -> str:
    text = f"{number:.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def format_line_text(value: Optional[float], market: str) -> str:
    """Crown-style display text: 0.25 -> ``0/0.5``, 0.75 -> ``0.5/1``."""
    if value is None:
        return ""
    if abs(value) < 0.000001:
        return "0"
    magnitude = abs(value)
    steps = round(magnitude * 4)
    sign = "-" if value < 0 else ("+" if market == "2" else "")
    if abs(magnitude * 4 - steps) < 0.000001 and steps % 2 == 1:
        return f"{sign}{_trim(magnitude - 0.25)}/{_trim(magnitude + 0.25)}"
    return f"{sign}{_trim(magnitude)}"


def tier_for(edge: float) -> str:
    if edge >= TIER_ARB:
        return "arb"
    if edge >= TIER_CRITICAL:
        return "critical"
    if edge >= TIER_NEAR:
        return "near"
    return "watch"


def _timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_seconds(value: Any, now: datetime) -> Optional[int]:
    parsed = _timestamp(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _iter_crown_markets(crown: Dict[str, Any]) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Yield deduplicated main and (when loaded) alternate Crown markets."""
    markets = crown.get("markets") if isinstance(crown.get("markets"), dict) else {}
    extra = crown.get("extra_markets") if isinstance(crown.get("extra_markets"), dict) else {}
    extras_loaded = str(extra.get("status") or "") == "loaded"
    for kind, market in MARKET_CODES.items():
        seen: set[float] = set()
        for source in (markets, extra if extras_loaded else {}):
            for item in source.get(kind) or []:
                if not isinstance(item, dict):
                    continue
                line = parse_line(item.get("line"))
                if line is None or line in seen:
                    continue
                seen.add(line)
                yield market, item


def _pinnacle_lines(rows: Iterable[Dict[str, Any]], market: str) -> Dict[Tuple[str, float], Dict[str, Any]]:
    index: Dict[Tuple[str, float], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market") or "") != market:
            continue
        if row.get("quoted") is False:
            continue
        water = _number(row.get("hongKong"))
        line = parse_line(row.get("line"))
        if water is None or line is None:
            continue
        index.setdefault((str(row.get("option") or ""), round(line, 4)), row)
    return index


def _build(
    *,
    crown: Dict[str, Any],
    pinnacle: Dict[str, Any],
    mapping: Dict[str, Any],
    market: str,
    line: float,
    crown_side: str,
    crown_water: float,
    row: Dict[str, Any],
    pinnacle_side: str,
    crown_updated_at: Any,
    now: datetime,
) -> Dict[str, Any]:
    pinnacle_water = _number(row.get("hongKong"))
    assert pinnacle_water is not None
    implied = 1.0 / (1.0 + crown_water) + 1.0 / (1.0 + pinnacle_water)
    edge = 1.0 - implied
    crown_age = _age_seconds(crown_updated_at, now)
    pinnacle_age = _age_seconds(row.get("capturedAt"), now)
    ages = [value for value in (crown_age, pinnacle_age) if value is not None]
    return {
        "id": f"{crown.get('id')}|{market}|{line:.4f}|{crown_side}",
        "crownId": str(crown.get("id") or ""),
        "pinnacleId": str(pinnacle.get("id") or mapping.get("pinnacleId") or ""),
        "league": str(crown.get("league") or ""),
        "home": str(crown.get("home") or ""),
        "away": str(crown.get("away") or ""),
        "isLive": bool(crown.get("is_live")),
        "score": str(crown.get("score") or ""),
        "minute": str(crown.get("minute") or ""),
        "startLabel": str(crown.get("start_label") or ""),
        "kickoff": str(pinnacle.get("kickoff") or ""),
        "market": market,
        "marketLabel": MARKET_LABELS.get(market, market),
        "line": round(line, 4),
        "lineText": format_line_text(line, market),
        "crownSide": crown_side,
        "crownSideLabel": SIDE_LABELS[market][crown_side],
        "crownWater": round(crown_water, 3),
        "pinnacleSide": pinnacle_side,
        "pinnacleSideLabel": SIDE_LABELS[market][pinnacle_side],
        "pinnacleWater": round(pinnacle_water, 3),
        "crownCapturedAt": crown_updated_at,
        "pinnacleCapturedAt": row.get("capturedAt"),
        "waterSum": round(crown_water + pinnacle_water, 3),
        "payout": round(implied, 4),
        "edge": round(edge, 4),
        "tier": tier_for(edge),
        "ageSeconds": max(ages) if ages else None,
    }


def find_opportunities(
    crown_events: Iterable[Dict[str, Any]],
    rows_by_crown_id: Dict[str, Dict[str, Any]],
    mappings: Iterable[Dict[str, Any]],
    *,
    crown_updated_at: Any = None,
    now: Optional[datetime] = None,
    min_edge: float = -0.05,
    limit: int = 50,
) -> Dict[str, Any]:
    """Return the closest same-line cross-book pairs, best edge first."""
    local_now = now or datetime.now().astimezone()
    mappings = list(mappings)
    crown_by_id = {
        str(event.get("id") or ""): event
        for event in crown_events
        if isinstance(event, dict) and str(event.get("id") or "")
    }
    items: List[Dict[str, Any]] = []
    pairs = 0
    scanned = 0
    stale = 0
    mismatch_counts: Dict[str, int] = {}

    for mapping in mappings:
        crown_id = str(mapping.get("crownId") or "")
        crown = crown_by_id.get(crown_id)
        bundle = rows_by_crown_id.get(crown_id) if crown_id else None
        if not isinstance(crown, dict) or not isinstance(bundle, dict):
            continue
        rows = bundle.get("rows") or []
        pinnacle = bundle.get("event") or {}
        if not rows:
            continue
        # A stored mapping can still be wrong (stale auto-confirm, fixture ID
        # reuse). Never price two different matches against each other: the
        # water would look like a large arbitrage while the two bets settle on
        # separate fixtures. ``competition`` and ``kickoff`` are the only
        # reasons the pairing layer can verify without extra feed calls.
        problem = fixture_pair_problem(crown, pinnacle)
        if problem:
            mismatch_counts[problem] = mismatch_counts.get(problem, 0) + 1
            continue
        scanned += 1
        indexes = {market: _pinnacle_lines(rows, market) for market in MARKET_CODES.values()}
        for market, item in _iter_crown_markets(crown):
            line = parse_line(item.get("line"))
            if line is None:
                continue
            index = indexes[market]
            if market == "2":
                home_water = _number(item.get("a"))
                away_water = _number(item.get("b"))
                candidates = (
                    ("home", home_water, ("1", round(-line, 4)), "away"),
                    ("away", away_water, ("0", round(line, 4)), "home"),
                )
            else:
                over_water = _number(item.get("a"))
                under_water = _number(item.get("b"))
                candidates = (
                    ("over", over_water, ("4", round(line, 4)), "under"),
                    ("under", under_water, ("3", round(line, 4)), "over"),
                )
            for crown_side, crown_water, key, pinnacle_side in candidates:
                if crown_water is None:
                    continue
                row = index.get(key)
                if row is None:
                    continue
                pairs += 1
                item = _build(
                    crown=crown,
                    pinnacle=pinnacle,
                    mapping=mapping,
                    market=market,
                    line=line,
                    crown_side=crown_side,
                    crown_water=crown_water,
                    row=row,
                    pinnacle_side=pinnacle_side,
                    crown_updated_at=crown_updated_at,
                    now=local_now,
                )
                if (
                    item["isLive"]
                    and item["ageSeconds"] is not None
                    and item["ageSeconds"] > LIVE_QUOTE_MAX_AGE_SECONDS
                ):
                    stale += 1
                    continue
                items.append(item)

    items = [item for item in items if item["edge"] >= min_edge]
    items.sort(
        key=lambda item: (
            -item["edge"],
            item["ageSeconds"] if item["ageSeconds"] is not None else 10**6,
            item["league"],
            item["home"],
            item["line"],
        )
    )
    stats = {
        "mapped": sum(1 for mapping in mappings if str(mapping.get("crownId") or "")),
        "scanned": scanned,
        "pairs": pairs,
        "arb": sum(1 for item in items if item["tier"] == "arb"),
        "critical": sum(1 for item in items if item["tier"] == "critical"),
        "near": sum(1 for item in items if item["tier"] == "near"),
        "mismatch": sum(mismatch_counts.values()),
        "mismatchReasons": dict(mismatch_counts),
        "stale": stale,
    }
    return {"items": items[: max(0, limit)], "stats": stats}
