"""Bridge merged partner-order groups to the HD local signal node.

Orders on the same match, same line and same in-play score within a 20-second
window become ONE signal: total stake, merged order id (random, stable across
retries) and the Crown opposite-side water.  A Pinnacle/PS3838 event id is not
a valid HD/HGA gid, so unresolved groups are rejected when ``require_gid`` is
enabled (default).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import random
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request

import project_config
from telegram_push import _crown_opposite, format_pick


BEIJING = timezone(timedelta(hours=8))
WINDOW_MS = 20_000
DEFAULT_ENDPOINT = "http://127.0.0.1:9999/api/local-signal/push"
DEFAULT_TARGET = "ps3838"


class LocalSignalPushError(ValueError):
    """A source order cannot be converted to a safe local signal."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> Optional[float]:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _line_text(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    absolute = abs(number)
    whole = int(absolute)
    fraction = round(absolute - whole, 2)
    if fraction == 0:
        return str(whole)
    if fraction == 0.5:
        return f"{whole}.5"
    if fraction == 0.25:
        return f"{whole}/{whole}.5"
    if fraction == 0.75:
        return f"{whole}.5/{whole + 1}"
    return str(number).rstrip("0").rstrip(".")


def _signed_line(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    text = _line_text(number)
    if number > 0:
        return f"+{text}"
    if number < 0:
        return f"-{text}"
    return "0"


def _split_teams(value: Any) -> Tuple[str, str]:
    text = _text(value)
    for sep in ("-vs-", " vs ", " VS ", " v ", " V ", " 对 "):
        if sep in text:
            left, right = text.split(sep, 1)
            return left.strip(), right.strip()
    return text, ""


def _market_kind(market: Any) -> str:
    value = _text(market).lower()
    if value.startswith(("大小", "ou")):
        return "ou"
    if value.startswith(("让球", "hdp", "re", "r")):
        return "hdp"
    if value.startswith(("独赢", "1x2")):
        return "1x2"
    return "other"


def _bet_type(row: Dict[str, Any]) -> str:
    market = _text(row.get("market"))
    kind = _market_kind(market)
    label = {"ou": "大小", "hdp": "让球", "1x2": "独赢"}.get(kind, market or "其他")
    period = _period(row)
    suffix = " - 上半场" if period == "1H" else " - 下半场" if period == "2H" else ""
    return (f"(滚球) {label}" if bool(row.get("inplay")) else label) + suffix


def _selection(row: Dict[str, Any]) -> str:
    value = _text(row.get("selection")).upper()
    return {
        "UNDER": "小", "OVER": "大", "HOME": "主", "AWAY": "客",
        "H": "主", "C": "客",
    }.get(value, _text(row.get("selection")))


def _period(row: Dict[str, Any]) -> str:
    """FT / 1H / 2H from the PS3838 choice; unknown stays empty."""
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
    name = _text(row.get("period_name"))
    if "上半" in name:
        return "1H"
    if "下半" in name:
        return "2H"
    if "全场" in name or "全場" in name:
        return "FT"
    return ""


def _line(row: Dict[str, Any]) -> str:
    kind = _market_kind(row.get("market"))
    selection = _selection(row)
    handicap = _line_text(row.get("handicap"))
    if kind == "ou":
        return " ".join(part for part in (selection or "大小", handicap) if part)
    if kind == "hdp":
        home, away = _split_teams(row.get("teams"))
        team = home if selection == "主" else away if selection == "客" else selection
        signed = _signed_line(row.get("handicap"))
        return " ".join(part for part in (team or selection, signed) if part)
    if kind == "1x2":
        return selection or "独赢"
    return " ".join(part for part in (selection, handicap) if part) or _text(row.get("market"))


def _raw(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = json.loads(_text(row.get("raw_json")) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _source_event(row: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    try:
        choices = raw.get("choices")
        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    except Exception:
        first = {}
    return first


def build_payload(
    row: Dict[str, Any],
    *,
    target: str = DEFAULT_TARGET,
    gid_resolver: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
    require_gid: bool = True,
) -> Dict[str, Any]:
    """Convert one partner-order row to the HD local-signal schema.

    ``gid_resolver`` is deliberately an injection point.  It must return an
    HGA/HD event id, never the PS3838 ``eventDate`` or order id.
    """
    league = _text(row.get("league"))
    teams = _text(row.get("teams"))
    odds = row.get("odds")
    stake = row.get("stake")
    if not league or not teams:
        raise LocalSignalPushError("missing league or teams")
    if _number(odds) is None or (_number(odds) or 0) <= 0:
        raise LocalSignalPushError("missing or invalid odds")
    if _number(stake) is None or (_number(stake) or 0) <= 0:
        raise LocalSignalPushError("missing or invalid stake")

    raw = _raw(row)
    event_id = _text(raw.get("_EVENT") or raw.get("GID") or raw.get("gid"))
    if not event_id and gid_resolver is not None:
        event_id = _text(gid_resolver(row) or "")
    if require_gid and not event_id:
        raise LocalSignalPushError("missing HD/HGA gid; refusing to use PS3838 eventDate")

    out_raw: Dict[str, Any] = dict(raw)
    if event_id:
        out_raw["_EVENT"] = event_id
        out_raw["GID"] = event_id
    out_raw.setdefault("GTYPE", "FT")
    out_raw.setdefault("MARKET", "rb" if bool(row.get("inplay")) else "")
    out_raw.setdefault("WAGERSTYPE", _bet_type(row))
    out_raw.setdefault("SHOWTEXT_ORDER_TYPE_IORATIO", f"{_line(row)} @{_text(odds)}")

    source_order_id = _text(row.get("order_id"))
    if not source_order_id:
        raise LocalSignalPushError("missing order id")
    agent = _text(row.get("agent_code")) or "agent"
    member = _text(row.get("member_user_code") or row.get("member_identity")) or "member"
    # Keep the source id visible while avoiding collisions between agents or
    # members that happen to reuse an upstream order number.
    order_id = f"{target}:{agent}:{member}:{source_order_id}"
    created_ms = int(row.get("created_ms") or 0)
    dt = datetime.fromtimestamp(created_ms / 1000, BEIJING) if created_ms else datetime.now(BEIJING)
    return {
        "target": target,
        "orderId": order_id,
        "sport": _text(row.get("sport")) or "足球",
        "league": league,
        "sides": teams.replace("-vs-", " vs "),
        "betType": _bet_type(row),
        "line": _line(row),
        "odds": str(odds),
        "stake": str(stake),
        "date": dt.strftime("%m-%d-%Y"),
        "time": dt.strftime("%H:%M:%S"),
        "raw": {**out_raw, "source_order_id": source_order_id,
                "source_agent": agent, "source_member": member},
    }



def _fmt_number(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def build_merged_payload(
    rows: List[Dict[str, Any]],
    *,
    target: str = DEFAULT_TARGET,
    gid: str = "",
    require_gid: bool = True,
    crown: Optional[Dict[str, Any]] = None,
    signal_id: str = "",
) -> Dict[str, Any]:
    """Send only the Crown opposite-side bet for one 20-second group."""
    if not rows:
        raise LocalSignalPushError("empty merged group")
    if not signal_id:
        signal_id = f"{random.randint(10000000, 99999999)}"
    first = min(rows, key=lambda item: int(item.get("created_ms") or 0))
    teams = _text(first.get("teams"))
    if not teams:
        raise LocalSignalPushError("missing teams")

    stake_total = 0.0
    for row in rows:
        stake = _number(row.get("stake")) or 0.0
        if stake <= 0:
            raise LocalSignalPushError("missing or invalid stake")
        stake_total += stake
    if stake_total <= 0:
        raise LocalSignalPushError("missing or invalid stake")

    raw_first = _raw(first)
    event_id = _text(gid)
    if not event_id:
        event_id = _text(raw_first.get("_EVENT") or raw_first.get("GID") or raw_first.get("gid"))
    if require_gid and not event_id:
        raise LocalSignalPushError("missing HD/HGA gid; refusing to use PS3838 eventDate")
    if not crown:
        raise LocalSignalPushError("missing Crown snapshot; no opponent line to bet")
    opposite = _crown_opposite(first, crown)
    if not opposite:
        raise LocalSignalPushError("missing Crown opponent line; refusing to send")

    market = str(first.get("market") or "")
    selection = str(first.get("selection") or "").upper()
    try:
        line_value = float(first.get("handicap"))
    except (TypeError, ValueError):
        raise LocalSignalPushError("missing or invalid line") from None
    if market.startswith("大小") or market.startswith("OU"):
        is_under = selection in ("小", "UNDER")
        line_text = f"{'大' if is_under else '小'} {_line_text(line_value)}"
        bet_kind = "大小"
    else:
        is_home = selection in ("主", "HOME")
        team = crown.get("away") if is_home else crown.get("home")
        line_text = f"{team or ('客队' if is_home else '主队')} {_signed_line(-line_value)}"
        bet_kind = "让球"

    water = f"{float(opposite['water']):.2f}"
    period = _period(first)
    period_suffix = " - 上半场" if period == "1H" else " - 下半场" if period == "2H" else ""
    return {
        "target": target,
        "orderId": signal_id,
        "league": _text(crown.get("league")),
        "sides": f'{_text(crown.get("home"))} vs {_text(crown.get("away"))}',
        "betType": ("(滚球) " if bool(first.get("inplay")) else "") + bet_kind + period_suffix,
        "line": line_text,
        "odds": water,
        "stake": _fmt_number(stake_total),
        "gid": event_id,
        "crownLeague": _text(crown.get("league")),
        "crownHome": _text(crown.get("home")),
        "crownAway": _text(crown.get("away")),
        "crownOpponent": line_text,
        "crownOdds": water,
        "crownSnapshotAt": _text(crown.get("captured_at")),
    }


def _base_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        _text(row.get("teams")),
        int(row.get("event_ms") or 0),
        format_pick(row),
        _text(row.get("score")),
        _period(row),
    )


class LocalSignalPusher(threading.Thread):
    """Poll partner events, batch each 20-second group, and POST to HD."""

    _EVENT_QUERY = """
        SELECT e.event_id, e.agent_code, e.observed_at,
               o.member_user_code, o.member_name, o.league, o.teams, o.market,
               o.selection, o.handicap, o.odds, o.stake, o.currency,
               o.sport, o.inplay, o.created_at, o.created_ms, o.order_id,
               o.status,
               CAST(json_extract(o.raw_json,'$.choices[0].eventDate') AS INTEGER) AS event_ms,
               json_extract(o.raw_json,'$.choices[0].score') AS score,
               json_extract(o.raw_json,'$.choices[0].periodName') AS period_name,
               json_extract(o.raw_json,'$.choices[0].periodType') AS period_type,
               o.raw_json
          FROM partner_order_events e
          LEFT JOIN partner_orders o
            ON o.source=e.source AND o.member_identity=e.member_identity
           AND o.order_id=e.order_id
         WHERE e.event_id > ?
         ORDER BY e.event_id ASC LIMIT ?
    """

    def __init__(self, *, interval: float = 3.0, stop_event: Optional[threading.Event] = None):
        super().__init__(name="local-signal-push", daemon=True)
        self.interval = max(0.5, float(interval))
        self.stop_event = stop_event or threading.Event()
        self._active: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        self._cursor = 0
        self._db: Optional[sqlite3.Connection] = None
        self._mapping: Dict[str, Any] = {}
        self._reconciled_order_paths: set[str] = set()

    @staticmethod
    def _config() -> Dict[str, Any]:
        cfg = project_config.load_config()
        return {
            "enabled": _bool_value(cfg.get("local_signal_push_enabled"), False),
            "endpoint": _text(cfg.get("local_signal_push_url") or DEFAULT_ENDPOINT),
            "token": _text(cfg.get("local_signal_push_token")),
            "target": _text(cfg.get("local_signal_push_target") or DEFAULT_TARGET),
            "orders_dir": str(
                Path(str(cfg.get("local_signal_push_orders_dir") or "app/orders")).expanduser()
                if Path(str(cfg.get("local_signal_push_orders_dir") or "")).expanduser().is_absolute()
                else project_config.PROJECT_DIR.parent / str(cfg.get("local_signal_push_orders_dir") or "app/orders")
            ),
            "require_gid": _bool_value(cfg.get("local_signal_push_require_gid"), True),
            "gid_map_file": _text(cfg.get("local_signal_push_gid_map_file")),
            "partner_db": project_config.get_path(cfg, "partner_orders_db_path"),
            "state_db": project_config.get_path(cfg, "local_signal_push_state_db")
            if cfg.get("local_signal_push_state_db")
            else project_config.PROJECT_DIR / "var/db/local_signal_push.sqlite3",
            "merge_db": project_config.get_path(cfg, "merge_db_path"),
            "crown_host": str(cfg.get("crown_host") or "127.0.0.1"),
            "crown_port": int(cfg.get("crown_port") or 9223),
        }

    def _open_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS rejections ("
            "event_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS signal_ids ("
            "order_id TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS written_orders ("
            "order_id TEXT PRIMARY KEY, written_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS deliveries ("
            "event_id INTEGER PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL, "
            "next_try REAL NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS pending_events ("
            "event_id INTEGER PRIMARY KEY, row_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        columns = {str(row[1]) for row in self._db.execute("PRAGMA table_info(pending_events)")}
        if "signal_id" not in columns:
            self._db.execute("ALTER TABLE pending_events ADD COLUMN signal_id TEXT NOT NULL DEFAULT ''")
        row = self._db.execute("SELECT value FROM state WHERE key='last_event_id'").fetchone()
        try:
            self._cursor = int(row[0]) if row else 0
        except (TypeError, ValueError):
            self._cursor = 0

    def _load_gid_mapping(self, path_value: str) -> None:
        self._mapping = {}
        if not path_value:
            return
        try:
            path = Path(path_value).expanduser()
            if not path.is_absolute():
                path = project_config.PROJECT_DIR / path
            data = json.loads(path.read_text(encoding="utf-8"))
            self._mapping = data if isinstance(data, (dict, list)) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            self._mapping = {}

    @staticmethod
    def _mapping_gid(row: Dict[str, Any], mapping: Any) -> str:
        """Resolve a target HGA gid from a small local JSON mapping.

        Accepted dict keys include ``order_id``, ``agent:member:order_id`` and
        ``teams|event_ms``.  A list may contain objects with those fields and
        ``gid``/``event_id``.  This deliberately never falls back to
        ``eventDate``.
        """
        source_id = _text(row.get("order_id"))
        agent = _text(row.get("agent_code"))
        member = _text(row.get("member_user_code") or row.get("member_identity"))
        teams = _text(row.get("teams"))
        event_ms = _text(row.get("event_ms"))
        keys = [source_id, f"{agent}:{member}:{source_id}", f"{teams}|{event_ms}"]

        def extract(value: Any) -> str:
            if isinstance(value, dict):
                return _text(value.get("gid") or value.get("event_id") or value.get("eventId"))
            return _text(value)

        if isinstance(mapping, dict):
            for key in keys:
                if key in mapping:
                    gid = extract(mapping[key])
                    if gid:
                        return gid
            # Also allow a nested ``mappings`` object for readability.
            nested = mapping.get("mappings")
            if nested is not mapping:
                return LocalSignalPusher._mapping_gid(row, nested)
        elif isinstance(mapping, list):
            for item in mapping:
                if not isinstance(item, dict):
                    continue
                item_id = _text(item.get("order_id") or item.get("orderId"))
                item_agent = _text(item.get("agent") or item.get("agent_code"))
                item_member = _text(item.get("member") or item.get("member_user_code"))
                item_teams = _text(item.get("teams"))
                item_event = _text(item.get("event_ms") or item.get("eventDate"))
                matches = (
                    (item_id and item_id == source_id)
                    or (item_agent and item_member and item_id
                        and (item_agent, item_member, item_id) == (agent, member, source_id))
                    or (item_teams and item_event and (item_teams, item_event) == (teams, event_ms))
                )
                if matches:
                    gid = extract(item)
                    if gid:
                        return gid
        return ""

    def _save_cursor(self) -> None:
        if self._db is not None:
            self._db.execute("INSERT OR REPLACE INTO state VALUES('last_event_id', ?)", (str(self._cursor),))

    def _stage_event(self, row: Dict[str, Any]) -> None:
        """Persist a fetched source event before advancing the source cursor."""
        if self._db is None:
            return
        event_id = int(row.get("event_id") or 0)
        if event_id <= 0:
            return
        self._db.execute(
            "INSERT OR IGNORE INTO pending_events (event_id,row_json,created_at,signal_id) VALUES(?,?,?,?)",
            (
                event_id,
                json.dumps(row, ensure_ascii=False, separators=(",", ":")),
                datetime.now(BEIJING).isoformat(timespec="seconds"),
                _text(row.get("_pending_signal_id")),
            ),
        )
        if row.get("_pending_signal_id"):
            self._db.execute(
                "UPDATE pending_events SET signal_id=? WHERE event_id=? AND signal_id=''",
                (_text(row.get("_pending_signal_id")), event_id),
            )

    def _delivery_status(self, event_id: int) -> Optional[str]:
        if self._db is None:
            return None
        row = self._db.execute(
            "SELECT status FROM deliveries WHERE event_id=?", (int(event_id),)
        ).fetchone()
        return str(row[0]) if row else None

    def _load_pending_events(self) -> None:
        """Rehydrate staged events after a crash before the next poll."""
        if self._db is None:
            return
        rows = self._db.execute(
            "SELECT row_json,signal_id FROM pending_events ORDER BY event_id"
        ).fetchall()
        for raw_row, signal_id in rows:
            try:
                row = json.loads(str(raw_row))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                if signal_id:
                    row["_pending_signal_id"] = str(signal_id)
                self._enqueue(row)

    def _delivery(self, event_id: int, status: str, attempts: int = 0,
                  next_try: float = 0.0, message: str = "") -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?,?)",
            (int(event_id), status, int(attempts), float(next_try), str(message),
             datetime.now(BEIJING).isoformat(timespec="seconds")),
        )
        if status in {"sent", "rejected", "skipped", "failed"}:
            self._db.execute("DELETE FROM pending_events WHERE event_id=?", (int(event_id),))

    @staticmethod
    def _internal_order(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build the HD order row directly, without the HTTP bridge."""
        now = datetime.now(BEIJING)
        target = _text(payload.get("target")) or DEFAULT_TARGET
        order_id = _text(payload.get("orderId"))
        gid = _text(payload.get("gid"))
        if not order_id or not gid:
            raise RuntimeError("internal order missing orderId/gid")
        bet_type = _text(payload.get("betType")) or "让球"
        line = _text(payload.get("line"))
        odds = _text(payload.get("odds"))
        league = _text(payload.get("league"))
        sides = _text(payload.get("sides"))
        crown_league = _text(payload.get("crownLeague"))
        crown_home = _text(payload.get("crownHome"))
        crown_away = _text(payload.get("crownAway"))
        crown_opponent = _text(payload.get("crownOpponent"))
        crown_odds = _text(payload.get("crownOdds"))
        crown_snapshot_at = _text(payload.get("crownSnapshotAt"))
        live = "(滚球)" in bet_type
        raw = {
            "_EVENT": gid,
            "GID": gid,
            "GTYPE": "FT",
            "MARKET": "rb" if live else "",
            "WAGERSTYPE": bet_type,
            "SHOWTEXT_ORDER_TYPE_IORATIO": f"{line} @{odds}",
            "SHOWTEXT_LEAGUE": f"{league}<br>",
            "SOURCE": "ps38-internal",
        }
        if crown_league:
            raw["CROWN_LEAGUE"] = crown_league
        if crown_opponent:
            raw["CROWN_OPPONENT"] = crown_opponent
        if crown_odds:
            raw["CROWN_ODDS"] = crown_odds
        if crown_home:
            raw["TEAM_H"] = crown_home
        if crown_away:
            raw["TEAM_C"] = crown_away
        return {
            "ts": int(time.time()),
            "target": target,
            "orderId": order_id,
            "sport": _text(payload.get("sport")) or "足球",
            "stake": _text(payload.get("stake")),
            "odds": odds,
            "league": league,
            "sides": sides,
            "betType": bet_type,
            "line": line,
            "date": now.strftime("%m-%d-%Y"),
            "time": now.strftime("%H:%M:%S"),
            "gid": gid,
            "raw": raw,
            "_skip_signal_forward": True,
            "_signal_origin": "ps38_internal",
            "_ps38": {
                "crown_league": crown_league,
                "crown_home": crown_home,
                "crown_away": crown_away,
                "crown_opponent": crown_opponent,
                "crown_odds": crown_odds,
                "crown_snapshot_at": crown_snapshot_at,
            },
        }

    def _write_order(self, cfg: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        order = self._internal_order(payload)
        order_id = _text(order.get("orderId"))
        target = _text(order.get("target")) or DEFAULT_TARGET
        path = Path(str(cfg.get("orders_dir") or "")) / f"{target}.jsonl"
        self._reconcile_written_orders(path)
        if self._db is not None:
            exists = self._db.execute(
                "SELECT 1 FROM written_orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if exists:
                return {"ok": True, "duplicate": True, "order": order}
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(order, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        if self._db is not None:
            self._db.execute(
                "INSERT OR IGNORE INTO written_orders VALUES(?,?)",
                (order_id, datetime.now(BEIJING).isoformat(timespec="seconds")),
            )
        return {"ok": True, "duplicate": False, "order": order}

    def _reconcile_written_orders(self, path: Path, *, max_bytes: int = 4 * 1024 * 1024) -> None:
        """Recover the DB index after a crash between JSONL append and SQLite insert."""
        if self._db is None:
            return
        key = str(path.resolve())
        if key in self._reconciled_order_paths or not path.is_file():
            return
        self._reconciled_order_paths.add(key)
        try:
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - max_bytes), os.SEEK_SET)
                chunk = handle.read()
            if size > max_bytes:
                chunk = chunk.splitlines()[1:]  # discard a potentially partial first line
            rows = []
            for raw_line in chunk.splitlines():
                try:
                    item = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    continue
                order_id = _text(item.get("orderId")) if isinstance(item, dict) else ""
                if order_id:
                    rows.append((order_id, datetime.now(BEIJING).isoformat(timespec="seconds")))
            if rows:
                self._db.executemany("INSERT OR IGNORE INTO written_orders VALUES(?,?)", rows)
        except (OSError, sqlite3.Error):
            return

    def _fetch(self, path: Path, after: int) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(self._EVENT_QUERY, (after, 100))]

    def _new_signal_id(self) -> str:
        """Allocate an unused random 8-digit order id and remember it."""
        for _ in range(100):
            candidate = f"{random.randint(10000000, 99999999)}"
            if self._db is None:
                return candidate
            try:
                self._db.execute(
                    "INSERT INTO signal_ids VALUES(?,?)",
                    (candidate, datetime.now(BEIJING).isoformat(timespec="seconds")),
                )
                return candidate
            except sqlite3.IntegrityError:
                continue
        return f"{random.randint(10000000, 99999999)}"

    def _enqueue(self, row: Dict[str, Any]) -> None:
        event_id = int(row.get("event_id") or 0)
        status = self._delivery_status(event_id)
        if status in {"sent", "rejected", "skipped", "failed"}:
            return
        if any(
            any(int(item.get("event_id") or 0) == event_id for item in candidate["rows"])
            for candidate in self._active.values()
        ):
            return
        created = int(row.get("created_ms") or 0)
        key = _base_key(row)
        group = None
        for candidate in self._active.values():
            if candidate["key"] == key and 0 <= created - candidate["first_ms"] <= WINDOW_MS:
                group = candidate
                break
        if group is None:
            signal_id = _text(row.get("_pending_signal_id")) or self._new_signal_id()
            group = {
                "key": key, "first_ms": created, "rows": [], "attempts": 0,
                "next_try": 0.0, "signal_id": signal_id,
            }
            self._active[(key, created)] = group
        row["_pending_signal_id"] = str(group["signal_id"])
        group["rows"].append(row)
        if status is None:
            self._delivery(event_id, "queued")

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
    def _save_crown_snapshot(merge_db: str, teams: Any, event_ms: Any,
                             crown: Dict[str, Any]) -> None:
        if not merge_db or not crown:
            return
        try:
            connection = sqlite3.connect(str(merge_db), timeout=5)
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS crown_fixture_snapshots ("
                    "teams TEXT NOT NULL, event_ms INTEGER NOT NULL, crown_event_id TEXT, "
                    "crown_league TEXT, crown_home TEXT, crown_away TEXT, markets TEXT, "
                    "captured_at TEXT NOT NULL, PRIMARY KEY (teams, event_ms))"
                )
                connection.execute(
                    "INSERT OR REPLACE INTO crown_fixture_snapshots VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(teams), int(event_ms), str(crown.get("id") or ""),
                        str(crown.get("league") or ""), str(crown.get("home") or ""),
                        str(crown.get("away") or ""),
                        json.dumps(crown.get("markets") or {}, ensure_ascii=False),
                        str(crown.get("captured_at") or datetime.now(BEIJING).isoformat(timespec="seconds")),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
        except (sqlite3.Error, OSError, TypeError, ValueError):
            return

    @classmethod
    def _refresh_crown_snapshot(cls, cfg: Dict[str, Any], gid: str,
                                teams: Any, event_ms: Any) -> Optional[Dict[str, Any]]:
        """Force-load this one fixture's alternate lines, then update its snapshot."""
        host = str(cfg.get("crown_host") or "127.0.0.1")
        try:
            port = int(cfg.get("crown_port") or 9223)
        except (TypeError, ValueError):
            port = 9223
        base = f"http://{host}:{port}"
        try:
            from urllib.request import ProxyHandler, build_opener
            opener = build_opener(ProxyHandler({}))
            request = Request(
                f"{base}/api/crown/events/{gid}/details?force=1",
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with opener.open(request, timeout=8) as response:
                response.read()
            with opener.open(f"{base}/api/crown/events?compact=1", timeout=8) as response:
                data = json.load(response)
        except (OSError, HTTPError, URLError, ValueError):
            return None
        event = next(
            (item for item in data.get("events") or [] if str(item.get("id")) == str(gid)),
            None,
        )
        if event is None:
            return None
        markets = event.get("markets") or {}
        extra = event.get("extra_markets") or {}
        handicap = list(markets.get("handicap") or [])
        total = list(markets.get("total") or [])
        half_handicap = list(markets.get("half_handicap") or [])
        half_total = list(markets.get("half_total") or [])
        if str(extra.get("status")) == "loaded":
            handicap += list(extra.get("handicap") or [])
            total += list(extra.get("total") or [])
            half_handicap += list(extra.get("half_handicap") or [])
            half_total += list(extra.get("half_total") or [])
        crown = {
            "id": str(gid),
            "home": str(event.get("home") or ""),
            "away": str(event.get("away") or ""),
            "league": str(event.get("league") or ""),
            "markets": {
                "handicap": handicap,
                "total": total,
                "half_handicap": half_handicap,
                "half_total": half_total,
            },
            "captured_at": datetime.now(BEIJING).isoformat(timespec="seconds"),
        }
        cls._save_crown_snapshot(str(cfg.get("merge_db") or ""), teams, event_ms, crown)
        return crown

    def _reject(self, event_id: int, reason: str) -> None:
        if self._db is not None:
            self._db.execute(
                "INSERT OR REPLACE INTO rejections VALUES(?,?,?)",
                (int(event_id), str(reason), datetime.now(BEIJING).isoformat(timespec="seconds")),
            )
        self._delivery(int(event_id), "rejected", 0, 0, str(reason))

    def _retry_group(self, group_key: Any, group: Dict[str, Any], event_ids: List[int], reason: str) -> None:
        attempts = int(group.get("attempts") or 0) + 1
        if attempts >= 5:
            for event_id in event_ids:
                self._delivery(event_id, "failed", attempts, 0, str(reason)[:300])
            self._active.pop(group_key, None)
            return
        next_try = time.time() + min(300.0, 15.0 * (2 ** attempts))
        group["attempts"] = attempts
        group["next_try"] = next_try
        for event_id in event_ids:
            self._delivery(event_id, "retry", attempts, next_try, str(reason)[:300])

    def _flush(self, cfg: Dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        for group_key, group in list(self._active.items()):
            if float(group.get("next_try") or 0) > time.time():
                continue
            if now_ms - int(group["first_ms"] or 0) < WINDOW_MS:
                continue
            rows = group["rows"]
            event_ids = [int(row.get("event_id") or 0) for row in rows]
            first = min(rows, key=lambda item: int(item.get("created_ms") or 0))
            crown = self._load_crown_snapshot(
                str(cfg.get("merge_db") or ""), first.get("teams"),
                int(first.get("event_ms") or 0),
            )
            # The Crown event id is an HGA/HD id, so it is a valid gid even
            # without a manual mapping file; the mapping still wins.
            gid = self._mapping_gid(first, self._mapping) or str(
                (crown or {}).get("id") or ""
            )
            try:
                payload = build_merged_payload(
                    rows, target=cfg["target"], gid=gid,
                    require_gid=cfg["require_gid"], crown=crown,
                    signal_id=str(group.get("signal_id") or ""),
                )
            except LocalSignalPushError as exc:
                refreshed = None
                if gid:
                    refreshed = self._refresh_crown_snapshot(
                        cfg, gid, first.get("teams"), int(first.get("event_ms") or 0),
                    )
                if refreshed is None:
                    if gid:
                        self._retry_group(group_key, group, event_ids, str(exc))
                    else:
                        for event_id in event_ids:
                            self._reject(event_id, str(exc))
                        del self._active[group_key]
                    continue
                crown = refreshed
                try:
                    payload = build_merged_payload(
                        rows, target=cfg["target"], gid=gid,
                        require_gid=cfg["require_gid"], crown=crown,
                        signal_id=str(group.get("signal_id") or ""),
                    )
                except LocalSignalPushError as retry_exc:
                    self._retry_group(group_key, group, event_ids, str(retry_exc))
                    continue
            if not cfg["enabled"]:
                del self._active[group_key]
                continue
            try:
                self._write_order(cfg, payload)
                for event_id in event_ids:
                    self._delivery(event_id, "sent", group["attempts"] + 1)
                del self._active[group_key]
            except Exception as exc:  # noqa: BLE001 - retry the same merged signal
                self._retry_group(group_key, group, event_ids, str(exc))

    def poll_once(self) -> None:
        cfg = self._config()
        if self._db is None:
            self._open_state(Path(cfg["state_db"]))
        self._load_gid_mapping(cfg["gid_map_file"])
        rows = self._fetch(Path(cfg["partner_db"]), self._cursor)
        if not cfg["enabled"]:
            # Pausing ingestion must not acknowledge or discard events. The
            # persisted cursor remains unchanged so enabling the bridge later
            # can replay them from the partner event table.
            return
        for row in rows:
            self._enqueue(row)
            self._stage_event(row)
            self._cursor = max(self._cursor, int(row.get("event_id") or 0))
        self._save_cursor()
        self._load_pending_events()
        self._flush(cfg)

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - keep bridge alive
                print(f"[local-signal] bridge error: {exc!r}", flush=True)

    def stop(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        if self.is_alive():
            self.join(max(0.0, float(timeout)))
        if self._db is not None:
            self._db.close()
            self._db = None

    def state(self) -> Dict[str, Any]:
        try:
            cfg = self._config()
        except Exception:
            cfg = {
                "enabled": False,
                "endpoint": DEFAULT_ENDPOINT,
                "target": DEFAULT_TARGET,
                "require_gid": True,
            }
        if self._db is None:
            return {
                "worker_alive": self.is_alive(),
                "cursor": self._cursor,
                "pending": 0,
                "enabled": bool(cfg.get("enabled")),
                "mode": "internal",
                "orders_dir": str(cfg.get("orders_dir") or ""),
                "target": str(cfg.get("target") or DEFAULT_TARGET),
                "require_gid": bool(cfg.get("require_gid", True)),
            }
        try:
            rows = self._db.execute(
                "SELECT status, COUNT(*) FROM deliveries GROUP BY status"
            ).fetchall()
            counts = {str(status): int(count) for status, count in rows}
        except sqlite3.Error:
            counts = {}
        return {
            "worker_alive": self.is_alive(),
            "cursor": self._cursor,
            "pending": sum(1 for group in self._active.values() for _ in group["rows"]),
            "deliveries": counts,
            "rejections": counts.get("rejected", 0),
            "enabled": bool(cfg.get("enabled")),
            "mode": "internal",
            "orders_dir": str(cfg.get("orders_dir") or ""),
            "target": str(cfg.get("target") or DEFAULT_TARGET),
            "require_gid": bool(cfg.get("require_gid", True)),
        }


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="将我的赛事新单批量推送到 HD 本地交易节点")
    parser.add_argument("--once", action="store_true", help="只轮询一次，不启动常驻线程")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(list(argv) if argv is not None else None)
    bridge = LocalSignalPusher(interval=args.interval)
    if args.once:
        bridge.poll_once()
        return 0
    bridge.start()
    try:
        while bridge.is_alive():
            bridge.join(1.0)
    except KeyboardInterrupt:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
