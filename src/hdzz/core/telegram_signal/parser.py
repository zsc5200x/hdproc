from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class SignalParseError(ValueError):
    pass


def _nonempty_lines(text: Any) -> List[str]:
    normalized = str(text or "").replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in normalized.split("\n") if line.strip()]


def _label_value(lines: List[str], label: str) -> str:
    pattern = re.compile(rf"^{re.escape(label)}\s*[:：]\s*(.+)$")
    values = []
    for line in lines:
        match = pattern.match(line)
        if match:
            values.append(str(match.group(1) or "").strip())
    return values[-1] if values else ""


def _decorated_line(line: Any) -> str:
    """Remove Telegram emoji/HTML decoration while keeping the visible text."""
    value = re.sub(r"<[^>]+>", "", str(line or ""))
    # The order cards use emoji (including custom emoji rendered as text) as a
    # prefix.  Strip punctuation/emoji only at the beginning so team names
    # containing punctuation remain untouched.
    value = re.sub(r"^[^\w\u3400-\u9fff+\-]+", "", value, flags=re.UNICODE)
    return value.strip()


def _decorated_label_value(lines: List[str], label: str) -> str:
    pattern = re.compile(rf"(?:^|\s|[^\w\u3400-\u9fff]){re.escape(label)}\s*[:：]\s*(.*?)\s*$")
    values: List[str] = []
    for line in lines:
        match = pattern.search(_decorated_line(line))
        if match:
            values.append(str(match.group(1) or "").strip())
    return values[-1] if values else ""


def _parse_split_line(text: str) -> float:
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(?:\s*/\s*([+-]?\d+(?:\.\d+)?))?", str(text or "").strip())
    if not match:
        raise SignalParseError("invalid_line")
    left_text = match.group(1)
    right_text = match.group(2)
    left = float(left_text)
    if right_text is None:
        return left
    right = float(right_text)
    if left_text.startswith("-") and not right_text.startswith(("-", "+")):
        right = -abs(right)
    elif left_text.startswith("+") and not right_text.startswith(("-", "+")):
        right = abs(right)
    return (left + right) / 2.0


def _content_lines(lines: List[str]) -> List[str]:
    for index, line in enumerate(lines):
        if re.fullmatch(r"内容\s*[:：]?", line):
            return lines[index + 1 :]
    return []


def _parse_match_line(line: str) -> Tuple[str, str, int, int]:
    line = _decorated_line(line)
    match = re.match(
        r"^(.+?)\s+v(?:s)?\s+(.+?)(?:\s*\(\s*(\d+)\s*[-:]\s*(\d+)\s*\))?\s*$",
        line,
        re.IGNORECASE,
    )
    if not match:
        raise SignalParseError("missing_match")
    return (
        str(match.group(1) or "").strip(),
        str(match.group(2) or "").strip(),
        int(match.group(3) or 0),
        int(match.group(4) or 0),
    )


def _parse_bet_line(line: str) -> Tuple[str, str, str]:
    line = _decorated_line(line)
    match = re.match(
        r"^(.+?)\s+([+-]?\d+(?:\.\d+)?(?:\s*/\s*[+-]?\d+(?:\.\d+)?)?)\s*@\s*([+-]?\d+(?:\.\d+)?)\s*$",
        line,
    )
    if not match:
        raise SignalParseError("missing_bet_line")
    return (
        str(match.group(1) or "").strip(),
        re.sub(r"\s*/\s*", " / ", str(match.group(2) or "").strip()),
        str(match.group(3) or "").strip(),
    )


def _market_from(bet_type: str, selection: str) -> str:
    if "让球" in bet_type:
        return "handicap"
    if any(token in bet_type for token in ("大 / 小", "大小", "大球", "小球")):
        return "total_under" if str(selection).startswith("小") else "total_over"
    if "独赢" in bet_type or "獨贏" in bet_type:
        return "moneyline"
    raise SignalParseError("unsupported_market")


def _card_bet_type(play: str, *, status: str, period: str) -> str:
    text = str(play or "").strip()
    if "角球" in text and "大小" in text:
        market = "角球数 - 大 / 小"
    elif any(token in text for token in ("进球大小", "大 / 小", "大小", "大球", "小球")):
        market = "大 / 小"
    elif "让球" in text:
        market = "让球"
    elif "独赢" in text or "獨贏" in text:
        market = "独赢"
    else:
        market = text
    prefix = "(滚球) " if "滚球" in str(status or "") else ""
    period_text = str(period or "").strip()
    suffix = f" - {period_text}" if period_text and period_text != "全场" else ""
    return f"{prefix}{market}{suffix}".strip()


def parse_telegram_bet_signal(text: Any) -> Dict[str, Any]:
    lines = _nonempty_lines(text)
    if not lines:
        raise SignalParseError("unsupported_signal")

    # Native order cards sent by ag_watcher have no provider numbers and use
    # labelled fields (状态/玩法/选择/原注金额).  Keep this branch separate
    # from the legacy monitor export below because the two formats have
    # different section boundaries.
    decorated = [_decorated_line(line) for line in lines]
    is_order_card = bool(
        _decorated_label_value(lines, "单号")
        and _decorated_label_value(lines, "时间")
        and _decorated_label_value(lines, "选择")
        and _decorated_label_value(lines, "原注金额")
    )
    if is_order_card:
        order_id = _decorated_label_value(lines, "单号")
        if not re.fullmatch(r"\d{6,}", order_id):
            raise SignalParseError("missing_order_id")
        order_time = _decorated_label_value(lines, "时间")
        time_match = re.fullmatch(r"(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}:\d{2})", order_time)
        if not time_match:
            raise SignalParseError("missing_time")

        league = ""
        match_index = -1
        for index, line in enumerate(decorated):
            if re.fullmatch(
                r".+?\s+v(?:s)?\s+.+?(?:\s*\(\s*\d+\s*[-:]\s*\d+\s*\))?\s*",
                line,
                re.IGNORECASE,
            ):
                match_index = index
                break
        if match_index < 1:
            raise SignalParseError("missing_match")
        # The visible league line is immediately before the fixture.  Ignore
        # blank/label lines if a custom template inserts one.
        for candidate in reversed(decorated[:match_index]):
            if candidate and not re.search(r"^(新注单提醒|状态|玩法|时段|选择|原注金额|跟单金额|跟单成功)", candidate):
                league = re.sub(r"^联赛\s*[:：]\s*", "", candidate).strip()
                break
        if not league:
            raise SignalParseError("missing_league")
        home, away, score_home, score_away = _parse_match_line(decorated[match_index])

        selection_text = _decorated_label_value(lines, "选择")
        selection, line_text, odds = _parse_bet_line(selection_text)
        play = _decorated_label_value(lines, "玩法")
        if not play:
            raise SignalParseError("missing_bet_type")
        status = _decorated_label_value(lines, "状态")
        period = _decorated_label_value(lines, "时段") or "全场"
        sport = "篮球" if any("篮球" in line for line in decorated) else "足球"
        amount_text = _decorated_label_value(lines, "原注金额")
        amount_match = re.search(r"[+-]?[\d,]+(?:\.\d+)?", amount_text)
        if not amount_match:
            raise SignalParseError("missing_amount")
        amount = float(amount_match.group(0).replace(",", ""))
        if amount <= 0:
            raise SignalParseError("missing_amount")
        market = _market_from(play, selection)
        bet_type = _card_bet_type(play, status=status, period=period)
        line_value = _parse_split_line(line_text)
        return {
            "monitor": "",
            "member": "",
            "order_id": order_id,
            "date": str(time_match.group(1)),
            "time": str(time_match.group(2)),
            "sport": sport,
            "mode": "live" if "滚球" in status else "early",
            "bet_type": bet_type,
            "period": period,
            "market": market,
            "league": league,
            "home_number": "",
            "away_number": "",
            "home": home,
            "away": away,
            "score_home": score_home,
            "score_away": score_away,
            "selection": selection,
            "line_text": line_text,
            "line": f"{selection} {line_text}".strip(),
            "line_value": line_value,
            "odds": odds,
            "amount": amount,
            "raw_text": str(text or ""),
        }

    if not any(re.match(r"^单号\s*[:：]", line) for line in lines) or not _content_lines(lines):
        raise SignalParseError("unsupported_signal")

    monitor_match = re.match(r"^(.+?)监控(?:\s|$)", lines[0])
    monitor = str(monitor_match.group(1) or "").strip() if monitor_match else ""

    member = _label_value(lines, "会员")
    order_id = _label_value(lines, "单号")
    if not re.fullmatch(r"\d{6,}", order_id):
        raise SignalParseError("missing_order_id")

    order_time = _label_value(lines, "时间")
    time_match = re.fullmatch(r"(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}:\d{2})", order_time)
    if not time_match:
        raise SignalParseError("missing_time")

    sport = ""
    bet_type = ""
    for line in lines:
        sport_match = re.match(r"^(足球|篮球)\s*[:：]\s*(.+)$", line)
        if sport_match:
            sport = str(sport_match.group(1) or "").strip()
            bet_type = str(sport_match.group(2) or "").strip()
            break
    if not sport or not bet_type:
        raise SignalParseError("missing_bet_type")

    content = _content_lines(lines)
    if len(content) < 4:
        raise SignalParseError("missing_content")
    league = content[0]

    number_match = re.fullmatch(r"\[(\d+)\]\s*(?:vs|v)\s*\[(\d+)\]", content[1], re.IGNORECASE)
    if number_match:
        home_number = str(number_match.group(1) or "")
        away_number = str(number_match.group(2) or "")
        match_index = 2
    else:
        home_number = ""
        away_number = ""
        match_index = 1

    if len(content) <= match_index + 1:
        raise SignalParseError("missing_content")
    home, away, score_home, score_away = _parse_match_line(content[match_index])
    selection, line_text, odds = _parse_bet_line(content[match_index + 1])

    amount_match = None
    for line in lines:
        amount_match = re.match(r"^[￥¥]\s*[:：]?\s*([\d,]+(?:\.\d+)?)\s*$", line)
        if amount_match:
            break
    if not amount_match:
        raise SignalParseError("missing_amount")
    amount = float(str(amount_match.group(1) or "0").replace(",", ""))
    if amount <= 0:
        raise SignalParseError("missing_amount")

    market = _market_from(bet_type, selection)
    line_value = _parse_split_line(line_text)
    return {
        "monitor": monitor,
        "member": member,
        "order_id": order_id,
        "date": str(time_match.group(1)),
        "time": str(time_match.group(2)),
        "sport": sport,
        "mode": "live" if "滚球" in bet_type else "early",
        "bet_type": bet_type,
        "market": market,
        "league": league,
        "home_number": home_number,
        "away_number": away_number,
        "home": home,
        "away": away,
        "score_home": score_home,
        "score_away": score_away,
        "selection": selection,
        "line_text": line_text,
        "line": f"{selection} {line_text}".strip(),
        "line_value": line_value,
        "odds": odds,
        "amount": amount,
        "raw_text": str(text or ""),
    }
