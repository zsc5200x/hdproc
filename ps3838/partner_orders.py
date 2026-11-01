"""Pinnacle partner order ingestion over the shared Chrome CDP session.

The collector only performs read-only page fetches from the already logged-in
partner tab.  Every snapshot is validated strictly before it is committed: a
page that reports inconsistent totals, duplicate order ids or an unexpected
row count aborts the whole round instead of writing partial data.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import project_config

SOURCE = "ps38ag"
DEFAULT_BACKEND_URL = "https://www.ps38ag.com/partner-ui-v2/report-outstanding-v2"
BEIJING = timezone(timedelta(hours=8))
PARTNER_HOST = "ps38ag.com"
PROFILE_PATH = "/partner-account/ddd/user-info/login-user/profile"
BET_LIST_PATH = "/partner-reporting/ddd/common/bet-list/v2"
OUTSTANDING_PATH = "/partner-reporting/ddd/report/outstanding"
HIERARCHY_PATH = "/partner-reporting/ddd/report/win-loss-detail"

PROFILE_JS = """
async () => {
  const res = await fetch('/partner-account/ddd/user-info/login-user/profile', {credentials: 'include'});
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (error) { json = null; }
  return {httpStatus: res.status, json};
}
"""

BET_LIST_JS = """
async ({ownerUserId, playerUserId, selectedUserCode, status, pageNumber, rowsPerPage, fromDate, toDate}) => {
  const body = {
    betListType: 'DETAIL',
    ownerUserId: String(ownerUserId),
    playerUserId: playerUserId,
    selectedUserCode: selectedUserCode,
    product: 'SB',
    status: status,
    paging: {pageNumber: pageNumber, rowsPerPage: rowsPerPage}
  };
  if (status === 'SETTLED') {
    body.dateBy = 'GRADED_DATE';
    body.timezone = '+08:00';
    body.fromDate = fromDate;
    body.toDate = toDate;
  }
  const res = await fetch('/partner-reporting/ddd/common/bet-list/v2', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(body)
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (error) { json = null; }
  return {httpStatus: res.status, json};
}
"""

HIERARCHY_JS = """
async ({ownerUserId, fromDate, toDate}) => {
  const payload = {
    timezone: '+08:00', productCodes: ['SB'],
    fromDate: fromDate, toDate: toDate,
    ownerUserId: String(ownerUserId), drillDownUserId: ownerUserId,
    orderBy: {colName: 'user_code', sort: 'ASC'}, excludeTest: false, dateBy: 'GRADED_DATE',
    searchOption: 'USERNAME', searchByText: '', searchUserIds: '',
    requestSearchByUser: {searchByText: '', selectedLevels: ['ALL']},
    groupType: 'OWN_DOWNLINE', groupId: 0, userIds: null
  };
  const res = await fetch('/partner-reporting/ddd/report/win-loss-detail', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const text = await res.text();
  let json = null;
  try { json = JSON.parse(text); } catch (error) { json = null; }
  return {httpStatus: res.status, json};
}
"""

SELECTION_LABELS = {
    "OVER": "大", "UNDER": "小", "HOME": "主", "AWAY": "客", "DRAW": "和",
    "YES": "是", "NO": "否", "ODD": "单", "EVEN": "双",
}

BET_TYPE_LABELS = {
    "OU": "大小", "AH": "让球", "HDP": "让球", "1X2": "独赢", "EH": "欧赔",
    "DC": "双重机会", "HT_OU": "半场大小", "HT_AH": "半场让球", "CS": "正确比分",
    "TG": "总进球", "OR": "其他",
}


class PollError(Exception):
    """Base error for an unusable upstream response."""


class IncompletePage(PollError):
    """The upstream page cannot be trusted; the round must not be committed."""


def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="milliseconds")


def beijing_iso(epoch_ms: Optional[int]) -> Optional[str]:
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, BEIJING).isoformat(timespec="milliseconds")


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_settings() -> Dict[str, Any]:
    config = project_config.load_config()
    agents = config.get("partner_agents") or []
    if isinstance(agents, str):
        agents = agents.split(",")
    return {
        "cdp_url": str(config["cdp_url"]),
        "db_path": project_config.get_path(config, "partner_orders_db_path"),
        "agents": [str(item).strip() for item in agents if str(item).strip()],
        "history_from": str(config.get("partner_history_from") or "2026-07-01"),
        "backend_url": str(config.get("partner_backend_url") or DEFAULT_BACKEND_URL),
        "open_interval": max(1.0, float(config.get("partner_open_interval_seconds", 1))),
        "settled_interval": max(30.0, float(config.get("partner_settled_interval_seconds", 300))),
        "settled_window_days": max(1, int(config.get("partner_settled_window_days", 7))),
        "page_size": max(20, min(500, int(config.get("partner_page_size", 200)))),
    }


def month_chunks(start: date, end: date) -> List[Tuple[date, date]]:
    """Split a range into calendar-month chunks (the API rejects long spans)."""
    chunks: List[Tuple[date, date]] = []
    cursor = start.replace(day=1)
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_end = min(next_month - timedelta(days=1), end)
        chunks.append((cursor, chunk_end))
        cursor = next_month
    return chunks


def _member_identity(row: Dict[str, Any]) -> Tuple[str, str]:
    code = next((row.get(key) for key in
                 ("userCode", "playerUserCode", "memberUserCode") if row.get(key)), None)
    if code:
        return f"code:{code}", str(code)
    user_id = next((row.get(key) for key in
                    ("playerUserId", "memberUserId") if row.get(key)), None)
    if user_id is None:
        raise IncompletePage("有效订单缺少会员身份，拒绝猜测")
    return f"id:{user_id}", str(user_id)


def order_identity(row: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    order_id = next((row.get(key) for key in
                     ("playerWagerId", "orderNumber", "originalWagerId")
                     if row.get(key) not in (None, "", 0, "0")), None)
    if order_id is None:
        return None  # Aggregate footer rows carry no order id.
    member_identity, _ = _member_identity(row)
    return member_identity, str(order_id)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or value is None or str(value) != str(int(value)):
        raise IncompletePage(f"分页字段 {field} 无效")
    return int(value)


def parse_page(response: Dict[str, Any], page: int, page_size: int,
               status: str) -> Tuple[List[Dict[str, Any]], Tuple[int, int, int]]:
    if response.get("httpStatus") != 200:
        raise PollError(f"订单接口 HTTP {response.get('httpStatus')}")
    payload = response.get("json") or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise IncompletePage("注单响应缺少 body；本轮不提交")
    data, paging = body.get("data"), body.get("paging")
    if not isinstance(data, list) or not isinstance(paging, dict):
        raise IncompletePage("注单响应缺少 data/paging；本轮不提交")

    total = _integer(paging.get("totalItems"), "totalItems")
    pages = _integer(paging.get("totalPages"), "totalPages")
    size = _integer(paging.get("rowsPerPage"), "rowsPerPage")
    begin = _integer(paging.get("beginIndex"), "beginIndex")
    pages_valid = pages in (0, 1) if total == 0 else pages == math.ceil(total / size)
    if (total < 0 or size != page_size or pages < 0 or pages > 1000
            or not pages_valid or begin != (page - 1) * size):
        raise IncompletePage("分页总数、页码或页大小不一致；本轮不提交")
    if "pageNumber" in paging and paging["pageNumber"] != page:
        raise IncompletePage("返回页码与请求不一致；本轮不提交")

    valid: List[Dict[str, Any]] = []
    seen = set()
    for row in data:
        if not isinstance(row, dict):
            raise IncompletePage("注单行格式错误")
        identity = order_identity(row)
        if identity is None:
            continue
        if identity in seen:
            raise IncompletePage("分页存在重复订单；本轮不提交")
        seen.add(identity)
        actual = str(row.get("status") or status).upper()
        if actual not in ("OPEN", "SETTLED"):
            raise IncompletePage("订单状态无法识别")
        valid.append(dict(row, status=actual))

    expected = min(size, max(0, total - (page - 1) * size))
    if len(valid) != expected:
        raise IncompletePage("订单有效行数与分页总数不一致；本轮不提交")
    return valid, (total, pages, size)


def _selection_label(selection: str, name: str) -> str:
    if name:
        return name
    upper = selection.upper()
    return SELECTION_LABELS.get(upper, selection)


def _bet_type_label(value: str) -> str:
    upper = value.upper()
    return BET_TYPE_LABELS.get(upper, value)


RESULT_ALIASES = {
    "WIN": "WIN", "LOSE": "LOSE", "WIN_HALF": "WIN_HALF", "LOSE_HALF": "LOSE_HALF",
    "DRAW": "DRAW", "PUSH": "DRAW", "REFUNDED": "REFUNDED", "VOID": "REFUNDED",
}


def result_of(row: Dict[str, Any]) -> str:
    """Normalise the upstream settlement result to a stable enum."""
    english = str(row.get("wagerStatus") or "").strip().upper()
    if english in RESULT_ALIASES:
        return RESULT_ALIASES[english]
    text = str(row.get("wagerStatusTranslated") or "")
    if "贏半" in text or "赢半" in text:
        return "WIN_HALF"
    if "輸半" in text or "输半" in text:
        return "LOSE_HALF"
    if "退还" in text or "退回" in text:
        return "REFUNDED"
    if "胜" in text:
        return "WIN"
    if "负" in text:
        return "LOSE"
    if "平" in text:
        return "DRAW"
    return "OPEN" if str(row.get("status") or "").upper() == "OPEN" else ""


def win_loss_of(row: Dict[str, Any], result: str) -> Optional[float]:
    """Net profit/loss in the order currency, derived from upstream amounts."""
    stake = _number(row.get("stake")) or 0.0
    to_win = _number(row.get("toWin")) or 0.0
    if result == "WIN":
        return to_win
    if result == "LOSE":
        return -stake
    if result == "WIN_HALF":
        return to_win / 2
    if result == "LOSE_HALF":
        return -stake / 2
    if result in ("DRAW", "REFUNDED"):
        return 0.0
    return None


def parse_order(row: Dict[str, Any], agent_code: str) -> Dict[str, Any]:
    member_identity, member_code = _member_identity(row)
    order_id = order_identity(row)[1]
    choices = row.get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}

    handicap = str(first.get("handicap") or "").strip()
    bet_type = _bet_type_label(str(first.get("btName") or "").strip())
    market = " ".join(part for part in (bet_type, handicap) if part)
    selection = _selection_label(str(first.get("selection") or "").strip(),
                                 str(first.get("selectionName") or "").strip())

    created_ms = row.get("createdDate")
    created_ms = int(created_ms) if isinstance(created_ms, (int, float)) and not isinstance(created_ms, bool) else None
    settled_ms = row.get("settlementDateTime")
    settled_ms = int(settled_ms) if isinstance(settled_ms, (int, float)) and not isinstance(settled_ms, bool) else None

    result = result_of(row)
    win_loss = win_loss_of(row, result)

    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "source": SOURCE,
        "agent_code": agent_code,
        "member_identity": member_identity,
        "member_user_code": member_code,
        "member_name": str(row.get("firstName") or row.get("name") or row.get("nickname") or "").strip(),
        "order_id": order_id,
        "status": str(row.get("status") or "").upper(),
        "inplay": 1 if first.get("inplay") else 0,
        "sport": str(first.get("sportName") or ""),
        "league": str(first.get("leagueName") or ""),
        "teams": str(first.get("eventName") or ""),
        "market": market,
        "selection": selection,
        "handicap": handicap,
        "odds": _number(row.get("odds") if row.get("odds") is not None else first.get("odds")),
        "stake": _number(row.get("stake")),
        "to_win": _number(row.get("toWin")),
        "currency": str(row.get("currencyCode") or ""),
        "wager_status": str(row.get("wagerStatusTranslated") or ""),
        "result": result,
        "win_loss": win_loss,
        "created_ms": created_ms,
        "created_at": beijing_iso(created_ms),
        "settled_at": beijing_iso(settled_ms),
        "content_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "raw_json": raw,
    }


ORDER_COLUMNS = (
    "source", "agent_code", "member_identity", "member_user_code", "member_name",
    "order_id", "status", "inplay", "sport", "league", "teams", "market", "selection",
    "handicap", "odds", "stake", "to_win", "currency", "wager_status", "result", "win_loss",
    "created_ms", "created_at", "settled_at", "content_hash", "raw_json",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS partner_orders (
    source TEXT NOT NULL,
    agent_code TEXT NOT NULL,
    member_identity TEXT NOT NULL,
    member_user_code TEXT,
    member_name TEXT,
    order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    inplay INTEGER NOT NULL DEFAULT 0,
    sport TEXT,
    league TEXT,
    teams TEXT,
    market TEXT,
    selection TEXT,
    handicap TEXT,
    odds REAL,
    stake REAL,
    to_win REAL,
    currency TEXT,
    wager_status TEXT,
    result TEXT,
    win_loss REAL,
    created_ms INTEGER,
    created_at TEXT,
    settled_at TEXT,
    content_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    status_changed_at TEXT NOT NULL,
    PRIMARY KEY (source, member_identity, order_id)
);
CREATE INDEX IF NOT EXISTS partner_orders_status ON partner_orders(status, created_ms);
CREATE INDEX IF NOT EXISTS partner_orders_agent ON partner_orders(agent_code, status);
CREATE INDEX IF NOT EXISTS partner_orders_created ON partner_orders(created_ms);
CREATE INDEX IF NOT EXISTS partner_orders_member ON partner_orders(member_user_code);

CREATE TABLE IF NOT EXISTS partner_order_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    agent_code TEXT NOT NULL,
    member_identity TEXT NOT NULL,
    order_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(source, member_identity, order_id)
);
CREATE INDEX IF NOT EXISTS partner_events_cursor ON partner_order_events(event_id);

CREATE TABLE IF NOT EXISTS partner_poll_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    agent_code TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    elapsed_seconds REAL NOT NULL,
    request_count INTEGER NOT NULL,
    record_count INTEGER NOT NULL,
    new_count INTEGER NOT NULL,
    changed_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    baseline INTEGER NOT NULL,
    complete INTEGER NOT NULL,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS partner_poll_state (
    source TEXT NOT NULL,
    agent_code TEXT NOT NULL,
    snapshot_type TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    pruned_event_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, agent_code, snapshot_type)
);

CREATE TABLE IF NOT EXISTS partner_agents (
    code TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    level TEXT,
    name TEXT,
    resolved_at TEXT NOT NULL
);
"""

EVENT_LIMIT = 5000

ORDER_SELECT = (
    "agent_code, member_user_code, member_name, order_id, status, inplay, "
    "sport, league, teams, market, selection, handicap, odds, stake, to_win, "
    "currency, wager_status, result, win_loss, created_ms, created_at, settled_at"
)


def query_orders(conn: sqlite3.Connection, status: Optional[str] = "OPEN",
                 agent: Optional[str] = None, search: str = "",
                 limit: int = 50, offset: int = 0,
                 from_ms: Optional[int] = None,
                 to_ms: Optional[int] = None,
                 basis: str = "created",
                 from_utc: Optional[str] = None,
                 to_utc: Optional[str] = None) -> Dict[str, Any]:
    clauses = ["source=?"]
    params: List[Any] = [SOURCE]
    if status and str(status).upper() != "ALL":
        clauses.append("status=?")
        params.append(str(status).upper())
    if agent:
        clauses.append("agent_code=?")
        params.append(agent)
    if basis == "settled":
        clauses.append("settled_at IS NOT NULL AND settled_at<>''")
        if from_utc:
            clauses.append("datetime(settled_at)>=?")
            params.append(str(from_utc))
        if to_utc:
            clauses.append("datetime(settled_at)<=?")
            params.append(str(to_utc))
        order_by = "settled_at DESC, created_ms DESC"
    else:
        if from_ms is not None:
            clauses.append("created_ms>=?")
            params.append(int(from_ms))
        if to_ms is not None:
            clauses.append("created_ms<=?")
            params.append(int(to_ms))
        order_by = "created_ms DESC"
    if search:
        like = f"%{search}%"
        clauses.append("(member_user_code LIKE ? OR member_name LIKE ? OR teams LIKE ? "
                       "OR league LIKE ? OR market LIKE ? OR order_id LIKE ? OR agent_code LIKE ?)")
        params.extend([like] * 7)
    where = " AND ".join(clauses)
    total = conn.execute(f"SELECT COUNT(*) FROM partner_orders WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {ORDER_SELECT}, "
        "json_extract(raw_json,'$.choices[0].periodName') AS period_name, "
        "json_extract(raw_json,'$.choices[0].periodType') AS period_type "
        f"FROM partner_orders WHERE {where} "
        f"ORDER BY {order_by} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {"total": total, "rows": [dict(row) for row in rows]}


def query_events(conn: sqlite3.Connection, cursor: Optional[int],
                 limit: int = 50) -> Dict[str, Any]:
    min_id = conn.execute(
        "SELECT COALESCE(MIN(event_id), 0) FROM partner_order_events"
    ).fetchone()[0]
    max_id = conn.execute(
        "SELECT COALESCE(MAX(event_id), 0) FROM partner_order_events"
    ).fetchone()[0]
    if cursor is None:
        return {"cursor": max_id, "reset": False, "events": []}
    if cursor < 0 or cursor > max_id:
        return {"cursor": max_id, "reset": True, "events": []}
    if min_id and cursor < int(min_id) - 1:
        # The caller's cursor is older than the retained event window. Make
        # the gap explicit so clients can show a data-loss warning instead of
        # silently continuing from the first retained event.
        return {
            "cursor": max_id,
            "reset": True,
            "gap": True,
            "requested_after": int(cursor),
            "pruned_through": int(min_id) - 1,
            "events": [],
        }
    rows = conn.execute(
        """SELECT e.event_id, e.agent_code, e.observed_at, e.member_identity, e.order_id,
                  o.member_user_code, o.member_name, o.status, o.league, o.teams, o.market,
                  o.selection, o.handicap, o.odds, o.stake, o.currency, o.wager_status,
                  o.created_ms, o.created_at
            FROM partner_order_events e
            LEFT JOIN partner_orders o
              ON o.source=e.source AND o.member_identity=e.member_identity
             AND o.order_id=e.order_id
            WHERE e.event_id>? ORDER BY e.event_id LIMIT ?""",
        (cursor, limit),
    ).fetchall()
    events = [dict(row) for row in rows]
    return {"cursor": events[-1]["event_id"] if events else cursor,
            "reset": False, "events": events}


def query_summary(conn: sqlite3.Connection, agent: Optional[str] = None,
                  from_ms: Optional[int] = None,
                  to_ms: Optional[int] = None,
                  basis: str = "created",
                  from_utc: Optional[str] = None,
                  to_utc: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate stake and settlement outcome over the same filter scope."""
    clauses = ["source=?"]
    params: List[Any] = [SOURCE]
    if agent:
        clauses.append("agent_code=?")
        params.append(agent)
    if basis == "settled":
        clauses.append("settled_at IS NOT NULL AND settled_at<>''")
        if from_utc:
            clauses.append("datetime(settled_at)>=?")
            params.append(str(from_utc))
        if to_utc:
            clauses.append("datetime(settled_at)<=?")
            params.append(str(to_utc))
    else:
        if from_ms is not None:
            clauses.append("created_ms>=?")
            params.append(int(from_ms))
        if to_ms is not None:
            clauses.append("created_ms<=?")
            params.append(int(to_ms))
    where = " AND ".join(clauses)

    def column(condition: str) -> str:
        return f"COALESCE(SUM(CASE WHEN {condition} THEN 1 ELSE 0 END),0)"

    totals = conn.execute(
        f"""SELECT COUNT(*) AS orders,
                   COALESCE(SUM(stake),0) AS total_stake,
                   COALESCE(SUM(CASE WHEN status='OPEN' THEN stake ELSE 0 END),0) AS open_stake,
                   COALESCE(SUM(CASE WHEN status='SETTLED' THEN stake ELSE 0 END),0) AS settled_stake,
                   COALESCE(SUM(CASE WHEN win_loss>0 THEN win_loss ELSE 0 END),0) AS win_amount,
                   COALESCE(SUM(CASE WHEN win_loss<0 THEN -win_loss ELSE 0 END),0) AS lose_amount,
                   COALESCE(SUM(win_loss),0) AS net,
                   {column("status='OPEN'")} AS open_orders,
                   {column("status='SETTLED'")} AS settled_orders,
                   {column("result='WIN'")} AS win_count,
                   {column("result='LOSE'")} AS lose_count,
                   {column("result='WIN_HALF'")} AS win_half_count,
                   {column("result='LOSE_HALF'")} AS lose_half_count,
                   {column("result='DRAW'")} AS draw_count,
                   {column("result='REFUNDED'")} AS refunded_count
            FROM partner_orders WHERE {where}""",
        params,
    ).fetchone()

    agents = conn.execute(
        f"""SELECT agent_code, COUNT(*) AS orders,
                   COALESCE(SUM(stake),0) AS stake,
                   COALESCE(SUM(CASE WHEN status='OPEN' THEN stake ELSE 0 END),0) AS open_stake,
                   COALESCE(SUM(win_loss),0) AS net,
                   {column("result='WIN'")} AS win_count,
                   {column("result='LOSE'")} AS lose_count
            FROM partner_orders WHERE {where}
            GROUP BY agent_code ORDER BY agent_code""",
        params,
    ).fetchall()

    currency_row = conn.execute(
        f"SELECT currency FROM partner_orders WHERE {where} AND currency<>'' "
        "GROUP BY currency ORDER BY COUNT(*) DESC LIMIT 1",
        params,
    ).fetchone()
    states = conn.execute(
        "SELECT agent_code, snapshot_type, last_success_at, record_count "
        "FROM partner_poll_state ORDER BY agent_code, snapshot_type"
    ).fetchall()

    payload = {key: totals[key] for key in totals.keys()}
    payload["currency"] = currency_row[0] if currency_row else ""
    payload["open"] = payload.pop("open_orders")
    payload["settled"] = payload.pop("settled_orders")
    payload["agents"] = [dict(row) for row in agents]
    payload["states"] = [dict(row) for row in states]
    return payload


# Fixture identity is the event start time (kickoff), so a rematch between the
# same two teams on another date is not merged into the same row.
FIXTURE_EVENT_MS = (
    "COALESCE(CAST(json_extract(raw_json,'$.choices[0].eventDate') AS INTEGER), created_ms)"
)

# Normalized "line" key: market family + side + numeric handicap.  The same
# line can appear as HDP/让球 or 大小/OU in older rows, and 0.5/0.50/1.00 are
# all stored as TEXT, so compare them as REAL.
FIXTURE_LINE_KEY = """
    CASE
        WHEN market LIKE 'HDP%' OR market LIKE '让球%'
            THEN 'HDP|' || selection || '|' || CAST(CAST(handicap AS REAL) AS TEXT)
        WHEN market LIKE '大小%' OR market LIKE 'OU%'
            THEN 'OU|' || selection || '|' || CAST(CAST(handicap AS REAL) AS TEXT)
        ELSE market || '|' || selection || '|' || COALESCE(CAST(CAST(handicap AS REAL) AS TEXT), '')
    END
"""


def _order_scope(status, agent, search, from_ms, to_ms, basis,
                 from_utc, to_utc) -> Tuple[str, List[Any]]:
    """Shared WHERE clause for order-level and fixture-level queries."""
    clauses = ["source=?"]
    params: List[Any] = [SOURCE]
    if status and str(status).upper() != "ALL":
        clauses.append("status=?")
        params.append(str(status).upper())
    if agent:
        clauses.append("agent_code=?")
        params.append(agent)
    if search:
        like = f"%{search}%"
        clauses.append("(member_user_code LIKE ? OR member_name LIKE ? OR teams LIKE ? "
                       "OR league LIKE ? OR market LIKE ? OR order_id LIKE ? OR agent_code LIKE ?)")
        params.extend([like] * 7)
    if basis == "settled":
        clauses.append("settled_at IS NOT NULL AND settled_at<>''")
        if from_utc:
            clauses.append("datetime(settled_at)>=?")
            params.append(str(from_utc))
        if to_utc:
            clauses.append("datetime(settled_at)<=?")
            params.append(str(to_utc))
    else:
        if from_ms is not None:
            clauses.append("created_ms>=?")
            params.append(int(from_ms))
        if to_ms is not None:
            clauses.append("created_ms<=?")
            params.append(int(to_ms))
    return " AND ".join(clauses), params


def query_fixtures(conn: sqlite3.Connection, status: Optional[str] = "ALL",
                   agent: Optional[str] = None, search: str = "",
                   limit: int = 50, offset: int = 0,
                   from_ms: Optional[int] = None,
                   to_ms: Optional[int] = None,
                   basis: str = "created",
                   from_utc: Optional[str] = None,
                   to_utc: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate orders per fixture: ticket count, members, lines, stake."""
    where, params = _order_scope(status, agent, search, from_ms, to_ms, basis,
                                 from_utc, to_utc)
    rows = conn.execute(
        f"""
        WITH o AS (
            SELECT teams, league, market, selection, handicap, odds, stake,
                   created_ms, member_identity, status,
                   {FIXTURE_EVENT_MS} AS ev
            FROM partner_orders WHERE {where}
        )
        SELECT teams, ev, MAX(league) AS league,
               COUNT(*) AS orders,
               COUNT(DISTINCT member_identity) AS members,
               COUNT(DISTINCT {FIXTURE_LINE_KEY}) AS lines,
               COALESCE(SUM(stake),0) AS stake,
               COALESCE(SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END),0) AS open_orders,
               COALESCE(SUM(CASE WHEN status='SETTLED' THEN 1 ELSE 0 END),0) AS settled_orders,
               MIN(created_ms) AS first_ms,
               MAX(created_ms) AS last_ms
        FROM o GROUP BY teams, ev
        """,
        params,
    ).fetchall()

    fixtures: List[Dict[str, Any]] = []
    for row in rows:
        fixtures.append({
            "teams": row["teams"],
            "event_ms": row["ev"],
            "league": row["league"] or "",
            "orders": row["orders"],
            "members": row["members"],
            "lines": row["lines"],
            "stake": row["stake"],
            "open_orders": row["open_orders"],
            "settled_orders": row["settled_orders"],
            "first_ms": row["first_ms"],
            "last_ms": row["last_ms"],
        })

    fixtures.sort(key=lambda item: (-item["last_ms"], -item["orders"]))
    total = len(fixtures)
    return {
        "total": total,
        "rows": fixtures[offset:offset + max(1, limit)],
    }


def query_fixture_orders(conn: sqlite3.Connection, teams: str, event_ms: int,
                         status: Optional[str] = "ALL", agent: Optional[str] = None,
                         search: str = "", limit: int = 500,
                         from_ms: Optional[int] = None,
                         to_ms: Optional[int] = None,
                         basis: str = "created",
                         from_utc: Optional[str] = None,
                         to_utc: Optional[str] = None) -> Dict[str, Any]:
    """Order lines for one fixture, oldest first."""
    where, params = _order_scope(status, agent, search, from_ms, to_ms, basis,
                                 from_utc, to_utc)
    where += f" AND teams=? AND {FIXTURE_EVENT_MS}=?"
    params = params + [teams, int(event_ms)]
    total = conn.execute(
        f"SELECT COUNT(*) FROM partner_orders WHERE {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT {ORDER_SELECT}, "
        "json_extract(raw_json,'$.choices[0].score') AS score, "
        "json_extract(raw_json,'$.choices[0].periodName') AS period_name, "
        "json_extract(raw_json,'$.choices[0].periodType') AS period_type "
        f"FROM partner_orders WHERE {where} "
        "ORDER BY created_ms ASC LIMIT ?",
        params + [max(1, limit)],
    ).fetchall()
    return {
        "teams": teams,
        "event_ms": int(event_ms),
        "total": total,
        "rows": [dict(row) for row in rows],
    }


class OrderStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._run_counter = 0
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add settlement columns and backfill rows written before they existed."""
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(partner_orders)")}
        if "result" not in columns:
            self.db.execute("ALTER TABLE partner_orders ADD COLUMN result TEXT")
        if "win_loss" not in columns:
            self.db.execute("ALTER TABLE partner_orders ADD COLUMN win_loss REAL")
        pending = self.db.execute(
            "SELECT COUNT(*) FROM partner_orders WHERE result IS NULL"
        ).fetchone()[0]
        if not pending:
            return
        rows = self.db.execute(
            "SELECT rowid, raw_json FROM partner_orders WHERE result IS NULL"
        ).fetchall()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                data = json.loads(row["raw_json"])
                result = result_of(data)
                self.db.execute(
                    "UPDATE partner_orders SET result=?, win_loss=? WHERE rowid=?",
                    (result, win_loss_of(data, result), row["rowid"]),
                )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()

    def save_agents(self, agents: Sequence[Dict[str, Any]]) -> None:
        now = now_iso()
        with self.db:
            for agent in agents:
                self.db.execute(
                    """INSERT INTO partner_agents (code, user_id, level, name, resolved_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(code) DO UPDATE SET
                           user_id=excluded.user_id, level=excluded.level,
                           name=excluded.name, resolved_at=excluded.resolved_at""",
                    (agent["code"], int(agent["user_id"]), str(agent.get("level") or ""),
                     str(agent.get("name") or ""), now),
                )

    def _insert_order(self, fields: Dict[str, Any], now: str) -> None:
        columns = ORDER_COLUMNS + ("first_seen_at", "last_seen_at", "changed_at", "status_changed_at")
        values = [fields[column] for column in ORDER_COLUMNS] + [now, now, now, now]
        placeholders = ",".join("?" for _ in columns)
        self.db.execute(
            f"INSERT INTO partner_orders ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def _update_order(self, fields: Dict[str, Any], now: str, changed: bool,
                      status_changed: bool) -> None:
        update_columns = [column for column in ORDER_COLUMNS
                          if column not in ("source", "member_identity", "order_id")]
        assignments = ", ".join(f"{column}=?" for column in update_columns)
        values = [fields[column] for column in update_columns]
        self.db.execute(
            f"""UPDATE partner_orders SET {assignments},
                last_seen_at=?, changed_at=COALESCE(?, changed_at),
                status_changed_at=COALESCE(?, status_changed_at)
                WHERE source=? AND member_identity=? AND order_id=?""",
            values + [now, now if changed else None, now if status_changed else None,
                      SOURCE, fields["member_identity"], fields["order_id"]],
        )

    def commit_snapshot(self, agent_code: str, snapshot_type: str,
                        rows: List[Dict[str, Any]], stats: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically commit one complete OPEN or SETTLED snapshot."""
        snapshot_type = snapshot_type.upper()
        if snapshot_type not in ("OPEN", "SETTLED"):
            raise ValueError("快照类型必须是 OPEN 或 SETTLED")

        merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            key = order_identity(row)
            if key is None:
                continue
            previous = merged.get(key)
            if previous is None or str(row.get("status") or "").upper() == "SETTLED":
                merged[key] = row

        now = now_iso()
        new = changed = events = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            baseline = self.db.execute(
                "SELECT 1 FROM partner_poll_state WHERE source=? AND agent_code=? AND snapshot_type=?",
                (SOURCE, agent_code, snapshot_type),
            ).fetchone() is None
            for (member_identity, order_id), row in merged.items():
                fields = parse_order(row, agent_code)
                old = self.db.execute(
                    "SELECT status, content_hash FROM partner_orders "
                    "WHERE source=? AND member_identity=? AND order_id=?",
                    (SOURCE, member_identity, order_id),
                ).fetchone()
                if old is None:
                    self._insert_order(fields, now)
                    new += 1
                    if snapshot_type == "OPEN" and not baseline and fields["status"] == "OPEN":
                        cursor = self.db.execute(
                            """INSERT OR IGNORE INTO partner_order_events
                               (source, agent_code, member_identity, order_id, observed_at)
                               VALUES (?,?,?,?,?)""",
                            (SOURCE, agent_code, member_identity, order_id, now),
                        )
                        events += cursor.rowcount
                    continue
                if old["status"] == "SETTLED" and fields["status"] == "OPEN":
                    continue  # A stale OPEN response must never undo a settlement.
                different = old["content_hash"] != fields["content_hash"]
                self._update_order(fields, now, different, old["status"] != fields["status"])
                changed += int(different)

            self.db.execute(
                """INSERT INTO partner_poll_state
                   (source, agent_code, snapshot_type, last_success_at, record_count)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(source, agent_code, snapshot_type) DO UPDATE SET
                       last_success_at=excluded.last_success_at,
                       record_count=excluded.record_count""",
                (SOURCE, agent_code, snapshot_type, now, len(merged)),
            )
            self._prune_events(agent_code=agent_code, snapshot_type=snapshot_type)
            self._insert_run(agent_code, snapshot_type, stats, len(merged), new, changed, events, baseline, 1, None)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return {"new": new, "changed": changed, "events": events,
                "baseline": int(baseline), "records": len(merged)}

    def _prune_events(self, *, agent_code: str = "", snapshot_type: str = "") -> None:
        cutoff = self.db.execute(
            "SELECT event_id FROM partner_order_events ORDER BY event_id DESC LIMIT 1 OFFSET ?",
            (EVENT_LIMIT,),
        ).fetchone()
        if cutoff is not None:
            cutoff_id = int(cutoff[0])
            self.db.execute("DELETE FROM partner_order_events WHERE event_id<=?", (cutoff_id,))
            if agent_code and snapshot_type:
                self.db.execute(
                    """UPDATE partner_poll_state
                       SET pruned_event_id=MAX(pruned_event_id, ?)
                       WHERE source=? AND agent_code=? AND snapshot_type=?""",
                    (cutoff_id, SOURCE, agent_code, snapshot_type),
                )

    def _insert_run(self, agent_code: str, snapshot_type: str, stats: Dict[str, Any],
                    record_count: int, new: int, changed: int, events: int,
                    baseline: bool, complete: int, error_code: Optional[str]) -> None:
        self.db.execute(
            """INSERT INTO partner_poll_runs
               (source, agent_code, snapshot_type, started_at, finished_at,
                elapsed_seconds, request_count, record_count, new_count,
                changed_count, event_count, baseline, complete, error_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (SOURCE, agent_code, snapshot_type, stats["started_at"], stats["finished_at"],
             float(stats.get("elapsed_seconds", 0)), int(stats.get("request_count", 0)),
             record_count, new, changed, events, int(baseline), complete, error_code),
        )
        # A one-second OPEN loop writes thousands of runs per day; keep the
        # table bounded without touching the order or event history.
        self._run_counter += 1
        if self._run_counter % 500 == 0:
            self.db.execute(
                "DELETE FROM partner_poll_runs WHERE id <= "
                "(SELECT id FROM partner_poll_runs ORDER BY id DESC LIMIT 1 OFFSET 50000)"
            )

    def record_failure(self, agent_code: str, snapshot_type: str, stats: Dict[str, Any],
                       error_code: str) -> None:
        with self.db:
            self._insert_run(agent_code, snapshot_type, stats, 0, 0, 0, 0, False, 0, error_code)

    def list_orders(self, status: Optional[str] = "OPEN", agent: Optional[str] = None,
                    search: str = "", limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        return query_orders(self.db, status, agent, search, limit, offset)

    def open_events_after(self, cursor: Optional[int], limit: int = 50) -> Dict[str, Any]:
        return query_events(self.db, cursor, limit)

    def summary(self) -> Dict[str, Any]:
        return query_summary(self.db)


class PartnerSession:
    """Read-only CDP session bound to the logged-in partner tab."""

    def __init__(self, cdp_url: str, backend_url: str | None = None):
        self.cdp_url = cdp_url
        self.backend_url = str(backend_url or DEFAULT_BACKEND_URL)
        self._pw = None
        self._browser = None
        self._context = None
        self.page = None

    async def __aenter__(self) -> "PartnerSession":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
        self._context = self._browser.contexts[0]
        self.page = self._find_partner_page()
        if self.page is None:
            self.page = await self._context.new_page()
            await self.page.goto(self.backend_url)
        return self

    def _find_partner_page(self):
        for candidate in self._context.pages:
            host = urlsplit(candidate.url or "").hostname or ""
            if host == PARTNER_HOST or host.endswith("." + PARTNER_HOST):
                return candidate
        return None

    async def __aexit__(self, *exc_info) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._pw is not None:
                await self._pw.stop()

    async def profile(self) -> Dict[str, Any]:
        result = await self.page.evaluate(PROFILE_JS)
        if result.get("httpStatus") != 200:
            raise PollError(f"身份接口 HTTP {result.get('httpStatus')}")
        payload = result.get("json") or {}
        profile = payload.get("profile") if isinstance(payload, dict) else None
        if not isinstance(profile, dict):
            raise PollError("身份接口缺少 profile")
        user_id = profile.get("userId")
        user_code = profile.get("userCode")
        if (isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
                or not isinstance(user_code, str) or not user_code.strip()):
            raise PollError("身份接口缺少有效 userId/userCode")
        return {"user_id": user_id, "user_code": user_code.strip(),
                "login_id": profile.get("loginId") or user_code}

    async def fetch_orders(self, owner_user_id: int, agent: Dict[str, Any], status: str,
                           page: int, page_size: int,
                           from_date: Optional[str] = None,
                           to_date: Optional[str] = None) -> Dict[str, Any]:
        return await self.page.evaluate(BET_LIST_JS, {
            "ownerUserId": owner_user_id,
            "playerUserId": agent["user_id"],
            "selectedUserCode": agent["code"],
            "status": status,
            "pageNumber": page,
            "rowsPerPage": page_size,
            "fromDate": from_date,
            "toDate": to_date,
        })

    async def hierarchy(self, owner_user_id: int, from_date: str, to_date: str) -> Dict[str, Any]:
        return await self.page.evaluate(HIERARCHY_JS, {
            "ownerUserId": owner_user_id,
            "fromDate": from_date,
            "toDate": to_date,
        })


async def resolve_agents(session: PartnerSession, owner_user_id: int,
                         wanted: Sequence[str], history_from: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve configured agent codes to user ids using the member tree.

    The outstanding report only lists agents with a non-zero outstanding
    amount, so the settled win/loss tree is the authoritative source.
    """
    start = date.fromisoformat(history_from)
    end = date.today()
    found: Dict[str, Dict[str, Any]] = {}
    for chunk_start, chunk_end in month_chunks(start, end):
        response = await session.hierarchy(owner_user_id, chunk_start.isoformat(), chunk_end.isoformat())
        if response.get("httpStatus") != 200:
            continue
        payload = response.get("json") or {}
        body = payload.get("body") if isinstance(payload, dict) else None
        data = (body or {}).get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            code = row.get("userCode")
            user_id = row.get("userId")
            if (not isinstance(code, str) or not code.strip()
                    or isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0):
                continue
            found[code.strip()] = {
                "code": code.strip(),
                "user_id": user_id,
                "level": str(row.get("userLevel") or row.get("level") or ""),
                "name": str(row.get("firstName") or row.get("name") or ""),
            }
    agents = [found[code] for code in wanted if code in found]
    missing = [code for code in wanted if code not in found]
    return agents, missing


async def fetch_status(session: PartnerSession, owner_user_id: int, agent: Dict[str, Any],
                       status: str, page_size: int,
                       from_date: Optional[str] = None,
                       to_date: Optional[str] = None,
                       max_pages: int = 1000) -> Tuple[List[Dict[str, Any]], int]:
    """Read and validate every page of one status query."""
    records: List[Dict[str, Any]] = []
    identities = set()
    first_meta = None
    page = 1
    requests = 0
    while True:
        response = await session.fetch_orders(owner_user_id, agent, status, page, page_size,
                                              from_date, to_date)
        requests += 1
        valid, meta = parse_page(response, page, page_size, status)
        if first_meta is not None and meta != first_meta:
            raise IncompletePage("翻页期间总数发生变化；等待下一轮")
        first_meta = meta
        for row in valid:
            identity = order_identity(row)
            if identity in identities:
                raise IncompletePage("分页存在重复订单；本轮不提交")
            identities.add(identity)
        records.extend(valid)
        total, pages, _ = meta
        if page >= max(1, pages):
            break
        if page >= max_pages:
            raise IncompletePage("分页数超过上限；本轮不提交")
        page += 1
    return records, requests


async def fetch_open_all(session: PartnerSession, owner_user_id: int,
                         agents: Sequence[Dict[str, Any]], page_size: int
                         ) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]], int]]:
    """Fetch page 1 for every agent in parallel; paginate further if needed."""
    responses = await asyncio.gather(*[
        session.fetch_orders(owner_user_id, agent, "OPEN", 1, page_size)
        for agent in agents
    ])
    results = []
    for agent, response in zip(agents, responses):
        valid, meta = parse_page(response, 1, page_size, "OPEN")
        _, pages, _ = meta
        if pages > 1:
            rows, requests = await fetch_status(session, owner_user_id, agent, "OPEN", page_size)
            results.append((agent, rows, requests))
        else:
            results.append((agent, valid, 1))
    return results
