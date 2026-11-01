"""Shared odds parsing and conversion helpers."""

from __future__ import annotations

from typing import Any, Dict


def normalize_odds(market: Any, raw_value: Any) -> Dict[str, str]:
    """Normalize the currently supported market formats.

    Market 1 is treated as decimal odds; markets 2 and 3 are treated as
    Hong Kong odds. Unknown or invalid values are retained as empty fields.
    """
    market_code = str(market or "").strip()
    raw_text = "" if raw_value is None else str(raw_value).strip()
    result = {
        "odds_format": "",
        "decimal_odds": "",
        "hong_kong_odds": "",
        "报价状态": "空值或暂停",
    }

    if not raw_text or raw_text.lower() in {"none", "null"}:
        return result

    try:
        raw = float(raw_text)
    except (TypeError, ValueError):
        return result

    if raw <= 0:
        return result

    if market_code == "1":
        decimal, hong_kong, fmt = raw, raw - 1, "decimal"
    elif market_code in {"2", "3"}:
        hong_kong, decimal, fmt = raw, raw + 1, "hong_kong"
    else:
        return result

    result.update(
        {
            "odds_format": fmt,
            "decimal_odds": f"{decimal:.4f}",
            "hong_kong_odds": f"{hong_kong:.4f}",
            "报价状态": "有报价",
        }
    )
    return result
