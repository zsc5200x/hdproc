from __future__ import annotations

import re
from typing import Any, Dict


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _extract_nth_corner_label(bet_type: str) -> str:
    text = str(bet_type or "").strip()
    if not text or "角球" not in text:
        return ""
    m = re.search(r"第\s*([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*个角球", text)
    if not m:
        return ""
    token = str(m.group(1) or "").strip()
    return f"第{token}个角球" if token else ""


def parse_bet_display(order: Dict[str, Any], order_kind: str = "") -> Dict[str, str]:
    current = order or {}
    bet_type = _text(current.get("betType") or current.get("bet_type"))
    line = _text(current.get("display_line") or current.get("line"))
    selection = _text(current.get("selection"))
    odds = _text(current.get("odds"))
    kind = _text(order_kind) or ("滚球" if "滚球" in bet_type else "早盘")
    normalized = re.sub(r"\s+", " ", re.sub(r"[()（）-]", " ", bet_type)).strip()
    low = normalized.lower()
    is_total = bool(re.search(r"大\s*/\s*小|over\s*/\s*under|over|under|大小", low))

    if re.search(r"下半场|2nd|second half|\b2h\b", bet_type, flags=re.I):
        period = "下半场"
    elif re.search(r"上半场|半场|1st|first half|\b1h\b", bet_type, flags=re.I):
        period = "上半场"
    else:
        period = "全场"

    market_name = "盘口"
    bet_label = bet_type[:6] or "OTHER"
    if re.search(r"card|罚牌|牌数", bet_type, flags=re.I):
        market_name = "罚牌数"
        bet_label = "罚牌大小" if is_total else "罚牌"
    elif re.search(r"corner|角球", bet_type, flags=re.I):
        market_name = "角球数"
        nth_corner_label = _extract_nth_corner_label(bet_type)
        bet_label = "角球大小" if is_total else (nth_corner_label or "角球")
    elif re.search(r"handicap|让球|让分|亚盘", bet_type, flags=re.I):
        market_name = "让球"
        bet_label = "让球"
    elif re.search(r"moneyline|独赢|胜平负|1x2", bet_type, flags=re.I):
        market_name = "独赢"
        bet_label = "独赢"
    elif is_total or re.search(r"大球|小球|进球", bet_type, flags=re.I):
        market_name = "进球数"
        bet_label = "进球大小"
    elif re.search(r"over|大", bet_type, flags=re.I):
        market_name = "进球数"
        bet_label = "大球"
    elif re.search(r"under|小", bet_type, flags=re.I):
        market_name = "进球数"
        bet_label = "小球"

    selection_base = " ".join(part for part in (selection, line if line and line not in selection else "") if part) or line
    selection_text = f"{selection_base} @{odds}" if selection_base and odds else (selection_base or bet_label)

    return {
        "bet_label": bet_label,
        "market_name": market_name,
        "market_text": " · ".join(part for part in (kind, period, market_name) if part),
        "order_kind": kind,
        "period": period,
        "selection_text": selection_text,
    }
