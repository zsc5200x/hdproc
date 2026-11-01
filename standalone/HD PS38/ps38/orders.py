"""PS3838 订单解析：字段取自原项目已使用的接口响应，保留 raw。"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple

def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(mapping: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: Any) -> Any:
    if value in (None, ""):
        return value
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except Exception:
        return value


def _choice_text(choice: Dict[str, Any]) -> str:
    selection = _text(_first(choice, "selection", "selectionName", "betSelection"))
    handicap = _text(_first(choice, "handicap", "line", "points"))
    if selection and handicap:
        return f"{selection} {handicap}"
    return selection or handicap or _text(_first(choice, "eventName", "description"))


def _ps_display_fields(choice: Dict[str, Any], sides: str) -> Tuple[str, str, str]:
    """把 PS3838 的代码字段翻译成中文展示字段。"""
    code = _text(_first(choice, "btName", "marketType", "market")).upper()
    market = {"HDP": "让球", "OU": "进球大小", "TOTAL": "进球大小", "ML": "独赢", "1X2": "独赢"}.get(code, code)
    if not market:
        market = "玩法未提供"
    inplay = choice.get("inplay")
    kind = "滚球" if inplay is True else "早盘" if inplay is False else "状态未提供"
    period = _text(choice.get("periodName"))
    if not period or period in {"比赛", "全场", "常规赛"}:
        period = "全场"
    elif "上半场" in period:
        period = "上半场"
    elif "下半场" in period:
        period = "下半场"
    bet_type = f"({kind}) {market}" + (f"-{period}" if period != "全场" else "")
    side = _text(_first(choice, "selectionName", "selection", "betSelection"))
    selection_map = {"HOME": 0, "AWAY": 1}
    if side.upper() in selection_map:
        parts = re.split(r"\s*-vs-\s*|\s+vs\s+", sides, maxsplit=1, flags=re.IGNORECASE)
        side = parts[selection_map[side.upper()]] if len(parts) > selection_map[side.upper()] else side
    elif side.upper() == "OVER":
        side = "大"
    elif side.upper() == "UNDER":
        side = "小"
    handicap_value = _first(choice, "handicap", "line", "points")
    handicap = str(handicap_value).strip() if handicap_value is not None else ""
    # NORMAL 是下注单类型，不是玩法；推送模板应使用 HDP/OU 的中文名称。
    return bet_type, side, handicap


def _placed_parts(value: Any) -> Tuple[str, str]:
    text = _text(value)
    if not text:
        return "", ""
    text = text.replace("T", " ").replace("Z", "").strip()
    if " " in text:
        date, clock = text.split(" ", 1)
        return date, clock[:8]
    return text, ""


def normalize_order(raw: Dict[str, Any], target_name: str) -> Dict[str, Any]:
    """将 PS3838 原始订单映射成独立的只读展示格式，并保留完整 raw。"""
    choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    order_id = _text(_first(raw, "playerWagerId", "betId", "wagerId", "id", "ticketNo", "orderId"))
    if not order_id:
        raise ValueError("订单缺少真实唯一 ID，不能使用内容哈希冒充单号")
    placed = _first(raw, "placed", "createdAt", "betTime", "wagerTime")
    date, clock = _placed_parts(placed)
    event_name = _text(_first(choice, "eventName", "description"))
    home = _text(_first(choice, "home", "homeTeam"))
    away = _text(_first(choice, "away", "awayTeam"))
    sides = event_name or " vs ".join(part for part in (home, away) if part)
    bet_type = _text(_first(raw, "wagerType", "betType")) or _text(_first(choice, "btName", "marketType"))
    line = _choice_text(choice)
    odds = _first(choice, "finalOdds", "odds")
    return {
        "target": target_name,
        "orderId": order_id,
        "playerWagerId": order_id,
        "account": _text(_first(raw, "player", "playerName", "nickName", "username", "userName", "customer")),
        "memberId": _first(raw, "memberId", "playerId"),
        "agent": _text(_first(raw, "agent", "agentName", "agentId", "parent", "upline")),
        "stake": _number(_first(raw, "stake", "amount", "riskAmount", "toRisk")),
        "toRisk": _number(_first(raw, "toRisk", "riskAmount", "stake", "amount")),
        "toWin": _number(raw.get("toWin")),
        "risk": _number(_first(raw, "toRisk", "riskAmount", "stake", "amount")),
        "win": _number(_first(raw, "win", "winAmount", "payout", "winLoss")),
        "currency": _text(raw.get("currency")),
        "sport": _text(_first(choice, "sportName", "sport")),
        "league": _text(_first(choice, "leagueName", "league", "competition")),
        "sides": sides,
        "betType": bet_type,
        "line": line,
        "odds": _number(odds),
        "score": _first(choice, "score") or _first(raw, "score"),
        "status": _text(_first(raw, "status", "state", "wagerStatus")),
        "result": _first(choice, "resultEvent", "result", "settlement") or _first(raw, "result", "settlement"),
        "date": date,
        "time": clock,
        "placed": placed,
        "updatedAt": _first(raw, "updatedAt", "updateTime"),
        "choices": choices,
        "raw": raw,
    }


def extract_orders(payload: Any, target_name: str) -> List[Dict[str, Any]]:
    """从接口可能存在的包装层中提取订单列表。

    平博前端版本之间可能使用 data、result、rows 或 bets 包装数组；只接受
    含订单标识的对象，避免把统计、分页或其他列表误当成订单。
    """
    rows: List[Dict[str, Any]] = []
    seen: set[int] = set()

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 6 or id(value) in seen:
            return
        if isinstance(value, (dict, list)):
            seen.add(id(value))
        if isinstance(value, list):
            candidates = [item for item in value if isinstance(item, dict)]
            if candidates and any(_first(item, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, "") for item in candidates):
                rows.extend(item for item in candidates if _first(item, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, ""))
                return
            for item in value:
                walk(item, depth + 1)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"data", "result", "rows", "bets", "wagers", "items", "content", "body"}:
                    walk(child, depth + 1)
            if _first(value, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, ""):
                rows.append(value)

    walk(payload)
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        order = normalize_order(row, target_name)
        order_id = str(order.get("orderId") or "")
        if order_id:
            unique[order_id] = order
    return list(unique.values())


def order_changes(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    fields = (
        "status", "toRisk", "toWin", "stake", "risk", "win", "score", "result", "updatedAt",
        "choices", "odds", "line", "currency", "account", "memberId", "agent", "placed",
    )
    return {
        key: (previous.get(key), current.get(key))
        for key in fields
        if previous.get(key) != current.get(key)
    }


def build_poll_body() -> Dict[str, Any]:
    return {
        "downlineIds": None,
        "isInPlay": -1,
        "userIds": None,
        "groupType": "OWN_DOWNLINE",
        "excludeLeagueIds": None,
        "excludeSportIds": None,
        "productCodes": ["SB"],
        "ownerUserId": None,
        "minRisk": 0,
        "historyTime": 60,
    }


def has_order_list(payload: Any, depth: int = 0) -> bool:
    """空订单列表也是有效会话；普通 JSON 错误对象不能证明登录成功。"""
    if depth > 6:
        return False
    if isinstance(payload, list):
        return all(
            isinstance(row, dict)
            and _first(row, "playerWagerId", "betId", "wagerId", "ticketNo", "orderId", "id") not in (None, "")
            for row in payload
        )
    if not isinstance(payload, dict) or payload.get("error") or payload.get("errors") or payload.get("success") is False:
        return False
    return any(
        has_order_list(child, depth + 1)
        for key, child in payload.items()
        if str(key).lower() in {"data", "result", "rows", "bets", "wagers", "items", "content", "body"}
    )

