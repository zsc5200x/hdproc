"""Telegram delivery for newly observed partner orders.

New orders are merged the same way as the dashboard's 20-second view: the
same match + same pick + same in-play score within 20 seconds becomes one
message with the total stake, member count and the Crown opposite-side water.
Deliveries are recorded locally so a restart never double-sends, and a failed
group is retried with backoff instead of blocking later orders.
"""

from __future__ import annotations

import html
import http.client
import json
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import project_config
from fixture_merge import (
    build_alias_index,
    normalize_league,
    normalize_name,
    prepare_crown_event,
)

BEIJING = timezone(timedelta(hours=8))


def _now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _display_time(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "—")


def _fmt(value: Any, digits: Optional[int] = None) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.0f}" if digits is None else f"{number:,.{digits}f}"


def line_text(value: Any) -> str:
    """Render 0.25 / 0.75 as the adjacent half-goal pair, e.g. 3.25 -> 3/3.5."""
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return ""
    whole = int(number)
    fraction = round(number - whole, 2)
    if fraction == 0:
        return str(whole)
    if fraction == 0.5:
        return f"{whole}.5"
    if fraction == 0.25:
        return f"{whole}/{whole}.5"
    if fraction == 0.75:
        return f"{whole}.5/{whole + 1}"
    return str(number)


def signed_line(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    text = line_text(number)
    if number > 0:
        return f"+{text}"
    if number < 0:
        return f"-{text}"
    return "0"


def split_teams(value: Any) -> tuple:
    text = str(value or "")
    parts = re.split(r"\s*-\s*vs\s*-\s*", text, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


def format_pick(row: Dict[str, Any]) -> str:
    """Mirror the dashboard's Chinese market notation for the push message."""
    market = str(row.get("market") or "").strip()
    lower = market.lower()
    selection = str(row.get("selection") or "").strip()
    if lower.startswith("ou") or market.startswith("大小"):
        prefix = "小" if selection in ("小", "UNDER") else "大"
        return f"{prefix}{line_text(row.get('handicap'))}"
    if lower.startswith("hdp") or market.startswith("让球"):
        home, away = split_teams(row.get("teams"))
        is_home = selection in ("主", "HOME")
        team = home if is_home else away
        return f"{team or ('主队' if is_home else '客队')}{signed_line(row.get('handicap'))}"
    if lower.startswith("1x2") or market.startswith("独赢"):
        return selection or "独赢"
    if lower.startswith("or"):
        return selection or market or "—"
    label = re.sub(r"\s*0(?:\.0+)?$", "", market).strip()
    return " ".join(part for part in (label, selection) if part) or "—"


def _escape(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"), quote=False)


def format_message(row: Dict[str, Any]) -> str:
    home, away = split_teams(row.get("teams"))
    teams = f"{home} vs {away}" if away else (home or "—")
    member_code = str(row.get("member_user_code") or "—")
    member_name = str(row.get("member_name") or "")
    member = f"{member_code} · {member_name}" if member_name else member_code
    return "\n".join([
        "🆕 <b>平博新单</b>",
        "━━━━━━━━━━━━━━",
        f"🏆 <b>联赛</b>：{_escape(row.get('league'))}",
        f"⚽ <b>比赛</b>：{_escape(teams)}",
        f"🎯 <b>盘口</b>：{_escape(format_pick(row))}",
        f"💧 <b>水位</b>：{_fmt(row.get('odds'), 2)}",
        f"💰 <b>金额</b>：{_fmt(row.get('stake'))}",
        f"👤 <b>会员</b>：{_escape(member)}",
        f"🏷️ <b>代理</b>：{_escape(row.get('agent_code'))}",
        f"🧾 <b>单号</b>：{_escape(row.get('order_id'))}",
        f"🕒 <b>时间</b>：{_escape(_display_time(row.get('created_at')))}",
    ])


def _parse_crown_line(value: Any) -> Optional[float]:
    """Parse Crown line text like ``2.5``, ``3``, ``+0 / 0.5`` or ``-1``."""
    text = str(value if value is not None else "").strip()
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


def _row_period(row: Dict[str, Any]) -> str:
    """Return FT / 1H / 2H for a partner-order row."""
    value = row.get("period_type")
    try:
        number = int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        number = None
    if number == 0:
        return "FT"
    if number == 1:
        return "1H"
    if number == 2:
        return "2H"
    name = str(row.get("period_name") or "").strip()
    if not name:
        try:
            raw = json.loads(str(row.get("raw_json") or "{}"))
            choices = raw.get("choices") if isinstance(raw, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            name = str(choice.get("periodName") or "").strip()
            period_type = choice.get("periodType")
            if period_type == 0:
                return "FT"
            if period_type == 1:
                return "1H"
            if period_type == 2:
                return "2H"
        except (TypeError, ValueError, json.JSONDecodeError):
            name = ""
    if "上半" in name:
        return "1H"
    if "下半" in name:
        return "2H"
    if "全场" in name or "全場" in name:
        return "FT"
    return ""


def _crown_opposite(row: Dict[str, Any], crown: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Crown water for the opposite selection on the same line and period."""
    if not crown:
        return None
    markets = crown.get("markets") or {}
    market = str(row.get("market") or "")
    selection = str(row.get("selection") or "").upper()
    period = _row_period(row)
    try:
        line = float(row.get("handicap"))
    except (TypeError, ValueError):
        return None

    if market.startswith("大小") or market.startswith("OU"):
        if period == "1H":
            items = markets.get("half_total") or []
        elif period in ("", "FT"):
            items = markets.get("total") or []
        else:
            return None
        for item in items:
            parsed = _parse_crown_line(item.get("line"))
            if parsed is None or abs(parsed - line) >= 0.01:
                continue
            is_under = selection in ("小", "UNDER")
            try:
                water = float(item.get("a") if is_under else item.get("b"))
            except (TypeError, ValueError):
                return None
            return {
                "label": f"{'大' if is_under else '小'}{line_text(line)}",
                "water": water,
                "period": period,
            }
        return None

    if market.startswith("让球") or market.startswith("HDP"):
        is_home = selection in ("主", "HOME")
        home_line = line if is_home else -line
        if period == "1H":
            items = markets.get("half_handicap") or []
        elif period in ("", "FT"):
            items = markets.get("handicap") or []
        else:
            return None
        for item in items:
            parsed = _parse_crown_line(item.get("line"))
            if parsed is None or abs(parsed - home_line) >= 0.01:
                continue
            try:
                water = float(item.get("b") if is_home else item.get("a"))
            except (TypeError, ValueError):
                return None
            team = crown.get("away") if is_home else crown.get("home")
            return {
                "label": f"{team or ('客队' if is_home else '主队')} {signed_line(-line)}",
                "water": water,
                "period": period,
            }
        return None
    return None


def _pinnacle_pick_display(row: Dict[str, Any]) -> str:
    """Pinnacle side with a space before the handicap, e.g. ``阿拉维斯 +2.5/3``."""
    market = str(row.get("market") or "")
    lower = market.lower()
    selection = str(row.get("selection") or "").strip()
    period = _row_period(row)
    suffix = "（上半场）" if period == "1H" else "（下半场）" if period == "2H" else ""
    if market.startswith("让球") or lower.startswith("hdp"):
        home, away = split_teams(row.get("teams"))
        is_home = selection in ("主", "HOME")
        team = home if is_home else away
        return f"{team or ('主队' if is_home else '客队')} {signed_line(row.get('handicap'))}{suffix}"
    if market.startswith("大小") or lower.startswith("ou"):
        prefix = "小" if selection in ("小", "UNDER") else "大"
        return f"{prefix}{line_text(row.get('handicap'))}{suffix}"
    return format_pick(row) + suffix


def format_group_message(rows: List[Dict[str, Any]],
                         crown: Optional[Dict[str, Any]]) -> str:
    """20-second merged message: same pick + same score, with Crown opposite water."""
    first = rows[0]
    ordered = sorted(rows, key=lambda row: int(row.get("created_ms") or 0))
    home, away = split_teams(first.get("teams"))
    pinnacle_teams = f"{home} VS {away}" if away else (home or "—")
    league = str(first.get("league") or "—").replace(" - ", " · ")
    score = str(first.get("score") or "").strip() or "—"
    total = sum(float(row.get("stake") or 0) for row in rows)
    members = {
        str(row.get("member_user_code") or row.get("member_name") or row.get("order_id") or "")
        for row in rows
    }
    odds = sorted(float(row["odds"]) for row in rows if row.get("odds") is not None)
    if odds:
        odds_text = (
            f"{odds[0]:.2f}" if abs(odds[0] - odds[-1]) < 1e-9
            else f"{odds[0]:.2f} – {odds[-1]:.2f}"
        )
    else:
        odds_text = "—"

    crown_league = str((crown or {}).get("league") or "")
    crown_teams = f'{crown.get("home")} VS {crown.get("away")}' if crown else ""
    league_text = f"{league}（{crown_league}）" if crown_league else f"{league}（未匹配）"
    teams_text = f"{crown_teams or pinnacle_teams}（{score}）"

    opposite = _crown_opposite(first, crown)
    if not crown:
        crown_line = "🔴 皇冠：未匹配"
    elif not opposite:
        crown_line = "🔴 皇冠：没挂同一条线"
    else:
        crown_line = f'🔴 皇冠：{_escape(opposite["label"])}（{opposite["water"]:.2f}）'

    if opposite and odds:
        low = odds[0] + opposite["water"]
        high = odds[-1] + opposite["water"]
        combined = f"{low:.2f} – {high:.2f}" if abs(high - low) > 1e-9 else f"{low:.2f}"
    else:
        combined = "—"

    stamps = sorted(_display_time(row.get("created_at")) for row in rows)
    start = stamps[0] if stamps else "—"
    end = stamps[-1] if stamps else "—"
    try:
        elapsed = max(0, round(
            (int(ordered[-1].get("created_ms") or 0) - int(ordered[0].get("created_ms") or 0)) / 1000
        ))
    except (TypeError, ValueError):
        elapsed = 0

    return "\n".join([
        "🆕 <b>新订单提醒</b>",
        "🏆 <b>赛事信息</b>",
        _escape(league_text),
        f"⚽ {_escape(teams_text)}",
        "🎯 <b>盘口对照</b>",
        f"🔵 平博：{_escape(_pinnacle_pick_display(first))}（{odds_text}）",
        crown_line,
        f"📊 <b>合水：{combined}</b>",
        f"💰 <b>合并总金额</b>：{_fmt(total)}",
        f"👥 <b>订单汇总</b>：{len(rows)} 单 / {len(members)} 会员",
        "🕒 <b>合并时间</b>",
        f"开始：{_escape(start)}",
        f"结束：{_escape(end)}",
        f"⏱️ <b>合并耗时：{elapsed} 秒</b>",
    ])


_CHAT_HINTS = {
    "chat not found": "Telegram 找不到这个 Chat ID：先把机器人拉进群并在群里发一条消息，或私聊机器人点 Start 后再试；群 ID 通常是 -100 开头的负数。",
    "bot was blocked by the user": "该账号把机器人拉黑了，请先解除拉黑。",
    "bot is not a member of the chat": "机器人不在该群里，请先把机器人拉进群。",
    "not enough rights to send text messages to the chat": "机器人在该群没有发言权限。",
    "user not found": "Telegram 找不到该用户，请确认账号。",
}


def _hint_for(description: str) -> str:
    text = str(description or "").lower()
    for needle, hint in _CHAT_HINTS.items():
        if needle in text:
            return hint
    return ""


def _describe(result: Dict[str, Any], fallback: str) -> str:
    description = str(result.get("description") or "").strip() or fallback
    hint = _hint_for(description)
    return f"{description}（{hint}）" if hint else description


def _telegram_request(
    config: Dict[str, Any],
    method: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Call one Bot API method and surface Telegram's own error text."""
    token = str(config.get("token") or "").strip()
    if not token:
        raise RuntimeError("请先填写机器人 Token")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    proxy = str(config.get("proxy") or "").strip()
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy})) if proxy else build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except HTTPError as exc:
        description = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
            description = str(body.get("description") or "")
        except (ValueError, OSError):
            description = ""
        if description:
            hint = _hint_for(description)
            raise RuntimeError(f"{description}（{hint}）" if hint else description) from None
        raise RuntimeError(f"HTTP {exc.code}：Telegram 返回异常状态") from None
    except URLError as exc:
        reason = str(getattr(exc, "reason", exc) or exc)
        hint = "；请检查代理地址或本机网络" if proxy else "；请检查本机网络"
        raise RuntimeError(f"无法连接 Telegram：{reason}{hint}") from None
    except OSError as exc:
        raise RuntimeError(f"无法连接 Telegram：{exc}") from None


def list_chats(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return chats the bot can already see, so the UI can fill the Chat ID.

    ``getUpdates`` only reports chats where the bot has received a message
    (or was added to a group), which is exactly the set that can be pushed to.
    """
    result = _telegram_request(
        config,
        "getUpdates",
        {"limit": 100, "allowed_updates": ["message", "channel_post", "my_chat_member"]},
    )
    if result.get("ok") is not True:
        raise RuntimeError(_describe(result, "读取 Telegram 会话失败"))
    chats: Dict[str, Dict[str, Any]] = {}
    for update in result.get("result") or []:
        if not isinstance(update, dict):
            continue
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            entry = update.get(key)
            if not isinstance(entry, dict):
                continue
            chat = entry.get("chat")
            if not isinstance(chat, dict) and isinstance(entry.get("from"), dict):
                chat = entry.get("from")
            if not isinstance(chat, dict) or chat.get("id") is None:
                continue
            chat_id = str(chat.get("id"))
            title = (
                str(chat.get("title") or "")
                or str(chat.get("username") or "")
                or " ".join(
                    part for part in (chat.get("first_name"), chat.get("last_name")) if part
                )
                or chat_id
            )
            chats[chat_id] = {
                "id": chat_id,
                "type": str(chat.get("type") or ""),
                "title": title,
            }
    items = sorted(chats.values(), key=lambda item: (item["type"] != "group", item["title"]))
    hint = ""
    if not items:
        hint = "没有发现可用的会话：先把机器人拉进群并在群里发一条消息，或私聊机器人发送 /start 后再点一次。"
    return {"chats": items, "hint": hint}


class TelegramPusher(threading.Thread):
    def __init__(self, db_path: Path, interval: float = 3.0):
        super().__init__(name="telegram-push", daemon=True)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.interval = max(2.0, float(interval))
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            str(self.db_path), timeout=10, isolation_level=None, check_same_thread=False
        )
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                event_id INTEGER PRIMARY KEY, status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                message TEXT, created_at TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT ''
            );
        """)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(deliveries)")}
        if "text" not in columns:
            self._db.execute("ALTER TABLE deliveries ADD COLUMN text TEXT NOT NULL DEFAULT ''")
        self._cursor = self._load_cursor()
        self._was_enabled = False
        self._last_poll_at = 0.0
        # 20-second grouped pushes: base key -> pending group.
        self._active: Dict[tuple, Dict[str, Any]] = {}
        self._restored = False
        self._crown_cache: Dict[str, Any] = {"at": 0.0, "value": None}
        self._snapshot_cursor = self._load_snapshot_cursor()
        self._snapshot_pending: List[Dict[str, Any]] = []

    # -- persistence -----------------------------------------------------
    def _load_cursor(self) -> int:
        row = self._db.execute("SELECT value FROM state WHERE key='last_event_id'").fetchone()
        try:
            return int(row["value"]) if row else 0
        except (TypeError, ValueError):
            return 0

    def _save_cursor(self, value: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO state VALUES('last_event_id', ?)", (str(int(value)),)
        )

    def _load_snapshot_cursor(self) -> Optional[int]:
        row = self._db.execute(
            "SELECT value FROM state WHERE key='snapshot_event_id'"
        ).fetchone()
        try:
            return int(row["value"]) if row else None
        except (TypeError, ValueError):
            return None

    def _save_snapshot_cursor(self) -> None:
        if self._snapshot_cursor is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO state VALUES('snapshot_event_id', ?)",
            (str(int(self._snapshot_cursor)),),
        )

    def _record(self, event_id: int, status: str, attempts: int, next_try: float,
                message: str, text: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO deliveries"
            "(event_id,status,attempts,next_try,message,created_at,text) "
            "VALUES(?,?,?,?,?,?,?)",
            (event_id, status, attempts, next_try, message, _now_iso(), text),
        )
        self._db.execute(
            "DELETE FROM deliveries WHERE event_id <= "
            "(SELECT event_id FROM deliveries ORDER BY event_id DESC LIMIT 1 OFFSET 200)"
        )

    # -- config ----------------------------------------------------------
    def _config(self) -> Dict[str, Any]:
        config = project_config.load_config()
        return {
            "enabled": bool(config.get("telegram_enabled")),
            "token": str(config.get("telegram_bot_token") or "").strip(),
            "chat_id": str(config.get("telegram_chat_id") or "").strip(),
            "proxy": str(config.get("telegram_proxy") or "").strip(),
            "partner_db": project_config.get_path(config, "partner_orders_db_path"),
            "merge_db": project_config.get_path(config, "merge_db_path"),
            "crown_host": str(config.get("crown_host") or "127.0.0.1"),
            "crown_port": int(config.get("crown_port") or 9223),
        }

    def state(self) -> Dict[str, Any]:
        # Use a separate read-only connection so a slow Telegram send in the
        # worker thread can never block the settings page.
        try:
            config = self._config()
        except Exception:  # noqa: BLE001 - settings read must not crash the UI
            config = {"enabled": False, "token": "", "chat_id": "", "proxy": ""}
        records: List[Dict[str, Any]] = []
        try:
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)) as connection:
                connection.row_factory = sqlite3.Row
                records = [dict(row) for row in connection.execute(
                    "SELECT event_id, status, attempts, message, created_at "
                    "FROM deliveries ORDER BY event_id DESC LIMIT 10"
                )]
        except sqlite3.Error:
            records = []
        pending: Optional[int] = None
        partner_db = str(config.get("partner_db") or "")
        if partner_db:
            max_event_id = self._max_event_id(Path(partner_db))
            if max_event_id is not None:
                pending = max(0, max_event_id - self._cursor)
        last_poll = (
            datetime.fromtimestamp(self._last_poll_at, BEIJING).isoformat(timespec="seconds")
            if self._last_poll_at else None
        )
        return {
            "enabled": bool(config["enabled"]),
            "has_token": bool(config["token"]),
            "chat_id": config["chat_id"],
            "proxy": config.get("proxy", ""),
            "records": records,
            "worker_alive": self.is_alive(),
            "last_poll_at": last_poll,
            "pending": pending,
            "cursor": self._cursor,
        }

    # -- partner database ------------------------------------------------
    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _max_event_id(self, path: Path) -> Optional[int]:
        try:
            with closing(self._connect(path)) as connection:
                row = connection.execute(
                    "SELECT COALESCE(MAX(event_id),0) FROM partner_order_events"
                ).fetchone()
                return int(row[0])
        except (sqlite3.Error, OSError):
            return None

    def _advance_cursor(self, path: Path) -> None:
        max_id = self._max_event_id(path)
        if max_id is not None and max_id > self._cursor:
            self._cursor = max_id
            self._save_cursor(max_id)

    _EVENT_QUERY = """
        SELECT e.event_id, e.agent_code, e.observed_at,
               o.member_user_code, o.member_name, o.league, o.teams, o.market,
               o.selection, o.handicap, o.odds, o.stake, o.currency,
               o.created_at, o.created_ms, o.order_id, o.status,
               CAST(json_extract(o.raw_json,'$.choices[0].eventDate') AS INTEGER) AS event_ms,
               json_extract(o.raw_json,'$.choices[0].score') AS score,
               json_extract(o.raw_json,'$.choices[0].periodName') AS period_name,
               json_extract(o.raw_json,'$.choices[0].periodType') AS period_type
          FROM partner_order_events e
          LEFT JOIN partner_orders o
            ON o.source=e.source AND o.member_identity=e.member_identity
           AND o.order_id=e.order_id
         WHERE {where}
         ORDER BY e.event_id {order} LIMIT ?
    """

    def _fetch_events(self, path: Path, after: int, limit: int = 50) -> List[sqlite3.Row]:
        with closing(self._connect(path)) as connection:
            return connection.execute(
                self._EVENT_QUERY.format(where="e.event_id > ?", order="ASC"),
                (after, limit),
            ).fetchall()

    def _fetch_event(self, path: Path, event_id: int) -> Optional[sqlite3.Row]:
        with closing(self._connect(path)) as connection:
            return connection.execute(
                self._EVENT_QUERY.format(where="e.event_id = ?", order="ASC"),
                (event_id, 1),
            ).fetchone()

    # -- delivery --------------------------------------------------------
    @staticmethod
    def _send(config: Dict[str, Any], text: str) -> None:
        result = _telegram_request(
            config,
            "sendMessage",
            {
                "chat_id": config["chat_id"],
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if result.get("ok") is not True:
            raise RuntimeError(_describe(result, "Telegram 拒绝发送"))

    @staticmethod
    def _base_key(row: Dict[str, Any]) -> tuple:
        return (
            str(row.get("teams") or ""),
            int(row.get("event_ms") or 0),
            format_pick(row),
            _row_period(row),
            str(row.get("score") or "").strip(),
        )

    def _enqueue(self, row: Dict[str, Any]) -> None:
        event_id = int(row["event_id"])
        base = self._base_key(row)
        created = int(row.get("created_ms") or 0)
        group = None
        for candidate in self._active.values():
            if candidate["base"] == base and created - candidate["first_ms"] <= 20000:
                group = candidate
                break
        if group is None:
            group = {
                "base": base, "first_ms": created, "rows": [],
                "attempts": 0, "next_try": 0.0,
            }
            self._active[(base, created)] = group
        group["rows"].append(row)
        self._record(event_id, "queued", 0, 0, "")

    def _restore_pending(self, path: Path) -> None:
        rows = self._db.execute(
            "SELECT event_id FROM deliveries WHERE status IN ('queued','failed') "
            "AND attempts<5 ORDER BY event_id DESC LIMIT 200"
        ).fetchall()
        for item in rows:
            event_id = int(item["event_id"])
            row = self._fetch_event(path, event_id)
            if row is None:
                self._record(event_id, "sent", 0, 0, "订单已不存在，跳过")
                continue
            data = dict(row)
            if time.time() * 1000 - int(data.get("created_ms") or 0) > 10 * 60 * 1000:
                self._record(event_id, "sent", 0, 0, "超过 10 分钟，过期跳过")
                continue
            self._enqueue(data)

    def _crown_context(self, config: Dict[str, Any]) -> Dict[str, Any]:
        if not bool(config.get("crown_enabled", True)):
            return {"events": [], "leagues": {}, "aliases": {}}
        now = time.monotonic()
        if self._crown_cache["value"] is not None and now - self._crown_cache["at"] < 5.0:
            return self._crown_cache["value"]
        context = {"events": [], "leagues": {}, "aliases": {}}
        try:
            opener = build_opener(ProxyHandler({}))
            url = (
                f"http://{config.get('crown_host') or '127.0.0.1'}:"
                f"{int(config.get('crown_port') or 9223)}/api/crown/events?compact=1"
            )
            with opener.open(url, timeout=4) as response:
                payload = json.load(response)
            raw = payload.get("events") if isinstance(payload, dict) else []
            events = []
            for item in raw or []:
                if not isinstance(item, dict):
                    continue
                try:
                    prepared = prepare_crown_event(item)
                    kickoff = datetime.strptime(
                        str(prepared.get("normalizedKickoff") or ""), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=BEIJING)
                except (TypeError, ValueError):
                    continue
                markets = item.get("markets") if isinstance(item.get("markets"), dict) else {}
                extra = item.get("extra_markets") if isinstance(item.get("extra_markets"), dict) else {}
                handicap = list(markets.get("handicap") or [])
                total = list(markets.get("total") or [])
                half_handicap = list(markets.get("half_handicap") or [])
                half_total = list(markets.get("half_total") or [])
                if str(extra.get("status") or "") == "loaded":
                    handicap += list(extra.get("handicap") or [])
                    total += list(extra.get("total") or [])
                    half_handicap += list(extra.get("half_handicap") or [])
                    half_total += list(extra.get("half_total") or [])
                events.append({
                    "id": str(item.get("id") or "").strip(),
                    "home": str(prepared.get("home") or "").strip(),
                    "away": str(prepared.get("away") or "").strip(),
                    "league": str(prepared.get("league") or "").strip(),
                    "league_norm": normalize_league(prepared.get("league")),
                    "kickoff": kickoff,
                    "markets": {
                        "handicap": handicap,
                        "total": total,
                        "half_handicap": half_handicap,
                        "half_total": half_total,
                    },
                })
            try:
                leagues, aliases = self._merge_name_index(str(config.get("merge_db") or ""))
            except (OSError, sqlite3.Error, ValueError):
                leagues, aliases = {}, {}
            context = {"events": events, "leagues": leagues, "aliases": aliases}
        except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError):
            context = {"events": [], "leagues": {}, "aliases": {}}
        self._crown_cache = {"at": now, "value": context}
        return context

    @staticmethod
    def _merge_name_index(merge_db: str) -> tuple:
        leagues: Dict[str, str] = {}
        pairs = []
        if not merge_db:
            return leagues, {}
        with closing(sqlite3.connect(f"file:{merge_db}?mode=ro", uri=True, timeout=5)) as connection:
            for pinnacle_json, crown_json in connection.execute(
                "SELECT pinnacle_snapshot, crown_snapshot FROM league_mappings"
            ):
                pinnacle = json.loads(pinnacle_json)
                crown = json.loads(crown_json)
                key = normalize_league(pinnacle.get("name"))
                name = str(crown.get("name") or "").strip()
                if key and name:
                    leagues[key] = name
            for pinnacle_json, crown_json in connection.execute(
                "SELECT pinnacle_snapshot, crown_snapshot FROM fixture_mappings"
            ):
                pinnacle = json.loads(pinnacle_json)
                crown = json.loads(crown_json)
                pairs.append((pinnacle.get("home"), crown.get("home")))
                pairs.append((pinnacle.get("away"), crown.get("away")))
        aliases: Dict[str, set] = {}
        for left, right in build_alias_index((), pairs)["teams"]:
            aliases.setdefault(left, set()).add(right)
        return leagues, aliases

    @staticmethod
    def _name_score(left: Any, right: Any, aliases: Dict[str, set]) -> int:
        a = normalize_name(left)
        b = normalize_name(right)
        if not a or not b:
            return 0
        if a == b or b in aliases.get(a, ()):
            return 2
        if a in b or b in a:
            return 1
        return 0

    def _match_crown(self, row: Dict[str, Any], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        home, away = split_teams(row.get("teams"))
        if not home or not away:
            return None
        try:
            kickoff = datetime.fromtimestamp(int(row.get("event_ms") or 0) / 1000, BEIJING)
        except (TypeError, ValueError, OSError):
            return None
        league = str(row.get("league") or "")
        target = normalize_league(context["leagues"].get(normalize_league(league), ""))
        best = None
        for event in context["events"]:
            if abs((event["kickoff"] - kickoff).total_seconds()) > 20 * 60:
                continue
            if target and event["league_norm"] != target:
                continue
            score = (
                self._name_score(home, event["home"], context["aliases"])
                + self._name_score(away, event["away"], context["aliases"])
            )
            if score >= 3 and (best is None or score > best[0]):
                best = (score, event)
        return best[1] if best else None

    @staticmethod
    def _save_crown_snapshot(merge_db: str, teams: Any, event_ms: Any,
                             crown: Dict[str, Any]) -> None:
        if not merge_db or not crown:
            return
        existing = TelegramPusher._load_crown_snapshot(merge_db, teams, event_ms)
        if existing:
            markets = existing.get("markets") or {}
            if markets.get("handicap") or markets.get("total"):
                return
        try:
            connection = sqlite3.connect(str(merge_db), timeout=5)
            try:
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS crown_fixture_snapshots (
                           teams TEXT NOT NULL, event_ms INTEGER NOT NULL,
                           crown_event_id TEXT, crown_league TEXT,
                           crown_home TEXT, crown_away TEXT,
                           markets TEXT, captured_at TEXT NOT NULL,
                           PRIMARY KEY (teams, event_ms)
                       )"""
                )
                connection.execute(
                    "INSERT OR REPLACE INTO crown_fixture_snapshots VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(teams), int(event_ms), str(crown.get("id") or ""),
                        str(crown.get("league") or ""), str(crown.get("home") or ""),
                        str(crown.get("away") or ""),
                        json.dumps(crown.get("markets") or {}, ensure_ascii=False),
                        _now_iso(),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        except (sqlite3.Error, OSError, TypeError, ValueError):
            return

    @staticmethod
    def _load_crown_snapshot(merge_db: str, teams: Any, event_ms: Any) -> Optional[Dict[str, Any]]:
        if not merge_db:
            return None
        try:
            connection = sqlite3.connect(f"file:{merge_db}?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                row = connection.execute(
                    "SELECT crown_event_id, crown_league, crown_home, crown_away, "
                    "markets, captured_at FROM crown_fixture_snapshots "
                    "WHERE teams=? AND event_ms=?",
                    (str(teams), int(event_ms)),
                ).fetchone()
            finally:
                connection.close()
        except (sqlite3.Error, TypeError, ValueError):
            return None
        if row is None:
            return None
        try:
            markets = json.loads(row["markets"] or "{}")
        except (TypeError, ValueError):
            markets = {}
        return {
            "id": row["crown_event_id"] or "",
            "league": row["crown_league"] or "",
            "home": row["crown_home"] or "",
            "away": row["crown_away"] or "",
            "markets": markets,
            "captured_at": row["captured_at"] or "",
        }

    @staticmethod
    def _force_crown_details(config: Dict[str, Any], gid: str) -> bool:
        gid = str(gid or "").strip()
        if not gid:
            return False
        status = 0
        try:
            connection = http.client.HTTPConnection(
                str(config.get("crown_host") or "127.0.0.1"),
                int(config.get("crown_port") or 9223), timeout=8,
            )
            connection.request(
                "POST", f"/api/crown/events/{gid}/details?force=1",
                body=b"{}", headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            status = response.status
            connection.close()
        except (OSError, http.client.HTTPException):
            return False
        return 200 <= status < 300

    def _snapshot_new_events(self, path: Path, config: Dict[str, Any]) -> None:
        """Capture the Crown fixture for the first order of every new match."""
        merge_db = str(config.get("merge_db") or "")
        if not merge_db:
            return
        if self._snapshot_cursor is None:
            self._snapshot_cursor = self._max_event_id(path) or 0
            self._save_snapshot_cursor()
            return
        for row in self._fetch_events(path, self._snapshot_cursor, limit=100):
            data = dict(row)
            event_id = int(data["event_id"])
            if event_id > self._snapshot_cursor:
                self._snapshot_cursor = event_id
                self._save_snapshot_cursor()
            teams = str(data.get("teams") or "")
            event_ms = int(data.get("event_ms") or 0)
            if not teams or not event_ms:
                continue
            if self._load_crown_snapshot(merge_db, teams, event_ms) is not None:
                continue
            if any(
                item["teams"] == teams and item["event_ms"] == event_ms
                for item in self._snapshot_pending
            ):
                continue
            self._snapshot_pending.append({
                "teams": teams,
                "event_ms": event_ms,
                "sample": data,
                "at": time.time(),
            })
        if not self._snapshot_pending:
            return
        context = self._crown_context(config)
        for item in list(self._snapshot_pending):
            if time.time() - item["at"] > 30 * 60:
                self._snapshot_pending.remove(item)
                continue
            crown = self._match_crown(item["sample"], context)
            if crown is None:
                continue
            if crown.get("id") and _crown_opposite(item["sample"], crown) is None:
                if self._force_crown_details(config, crown["id"]):
                    self._crown_cache = {"at": 0.0, "value": None}
                    context = self._crown_context(config)
                    crown = self._match_crown(item["sample"], context)
            if crown:
                self._save_crown_snapshot(merge_db, item["teams"], item["event_ms"], crown)
                self._snapshot_pending.remove(item)

    def _flush_due(self, config: Dict[str, Any]) -> None:
        if not self._active:
            return
        now = time.time()
        context = self._crown_context(config)
        for base, group in list(self._active.items()):
            if group["next_try"] > now:
                continue
            if group["attempts"] == 0 and now * 1000 - group["first_ms"] < 20000:
                continue
            sample = group["rows"][0]
            crown = self._match_crown(sample, context)
            if crown and crown.get("id") and _crown_opposite(sample, crown) is None:
                # Bet line is not in Crown's main-line feed: load this fixture's
                # alternate lines once, then re-match before composing the push.
                if self._force_crown_details(config, crown["id"]):
                    self._crown_cache = {"at": 0.0, "value": None}
                    context = self._crown_context(config)
                    crown = self._match_crown(sample, context)
            if crown:
                self._save_crown_snapshot(
                    str(config.get("merge_db") or ""), sample.get("teams"),
                    sample.get("event_ms"), crown,
                )
            else:
                snapshot = self._load_crown_snapshot(
                    str(config.get("merge_db") or ""), sample.get("teams"),
                    sample.get("event_ms"),
                )
                if snapshot:
                    crown = snapshot
            text = format_group_message(group["rows"], crown)
            try:
                self._send(config, text)
                for row in group["rows"]:
                    self._record(int(row["event_id"]), "sent", group["attempts"] + 1, 0,
                                 "推送成功", text=text)
                del self._active[base]
            except Exception as exc:  # noqa: BLE001 - retry the same group later
                group["attempts"] += 1
                delay = min(300, 15 * (2 ** group["attempts"]))
                group["next_try"] = now + delay
                for row in group["rows"]:
                    self._record(
                        int(row["event_id"]), "failed", group["attempts"],
                        group["next_try"], str(exc)[:200],
                    )
                if group["attempts"] >= 5:
                    del self._active[base]

    def _poll(self) -> None:
        self._last_poll_at = time.time()
        try:
            config = self._config()
        except Exception:  # noqa: BLE001 - ignore a transient bad config file
            return
        path = Path(config["partner_db"])
        self._snapshot_new_events(path, config)
        if not config["enabled"] or not config["token"] or not config["chat_id"]:
            self._advance_cursor(path)
            for group in self._active.values():
                for row in group["rows"]:
                    self._record(int(row["event_id"]), "sent", 0, 0, "推送已关闭，跳过")
            self._active.clear()
            self._was_enabled = False
            return
        if not self._was_enabled:
            # Opening the switch must not dump the existing backlog into the group.
            self._advance_cursor(path)
            self._was_enabled = True
        if not self._restored:
            self._restore_pending(path)
            self._restored = True
        for row in self._fetch_events(path, self._cursor):
            data = dict(row)
            event_id = int(data["event_id"])
            known = self._db.execute(
                "SELECT 1 FROM deliveries WHERE event_id=?", (event_id,)
            ).fetchone()
            if not known:
                self._enqueue(data)
            if event_id > self._cursor:
                self._cursor = event_id
                self._save_cursor(event_id)
        self._flush_due(config)

    def arm(self) -> None:
        """Called when the switch turns on so the very next event is delivered."""
        self._was_enabled = True

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            try:
                self._poll()
            except Exception as exc:  # noqa: BLE001 - the thread must survive
                print(f"[telegram] 推送循环异常：{exc!r}", flush=True)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=5)
        self._db.close()

    # -- settings actions ------------------------------------------------
    def test(self) -> Dict[str, Any]:
        config = self._config()
        if not config["token"]:
            raise ValueError("请先填写机器人 Token")
        if not config["chat_id"]:
            raise ValueError("请先填写群 Chat ID")
        self._send(config, "🆕 平博订单看板：Telegram 测试推送成功。")
        return {"ok": True}
