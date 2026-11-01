"""In-memory odds state and capture replay, independent from HTTP."""
from __future__ import annotations
import csv
import json
import sqlite3
import threading
from zoneinfo import ZoneInfo
from collections import Counter, deque
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from odds_utils import normalize_odds
from project_config import get_path
from src.core.constants import (RECENT_BOOTSTRAP_BYTES, HISTORY_LIMIT, CHANGE_LIMIT, MARKET_ROW_MISSES, MARKET_ROW_STALE_AFTER_SECONDS, MARKET_ROW_STALE_MISSES)
from src.core.time import now_iso
from src.providers.pinnacle.parser import (as_text, float_or_none, iter_event_records, iter_leaf_rows, iter_messages, metadata_status)
from src.services.event_visibility import (event_has_display_metadata, event_is_visible_today_upcoming, event_scope, is_full_time_market, parse_kickoff)
from src.storage.history import HistoryDatabase, HistoryWriter

class OddsStore:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.capture_path = get_path(config, "capture_path")
        self.seed_path = get_path(config, "live_snapshot_file")
        self.database_path = get_path(config, "dashboard_db_path")
        self.history_db = HistoryDatabase(self.database_path)
        self.history_writer = HistoryWriter(self.history_db)
        self.history_writer.start()
        self.lock = threading.RLock()
        self.events: Dict[str, Dict[str, Any]] = {}
        self.odds_by_event: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.history: Dict[str, Dict[str, list]] = {}
        self.changes: deque = deque(maxlen=CHANGE_LIMIT)
        self.revision = 0
        self.change_serial = 0
        self.last_captured_at = ""
        self.last_change_at = ""
        self.seeded_rows = 0
        self.recent_rows = 0
        self.message_count = 0
        self.parse_errors = 0
        self.presence_misses: Dict[str, int] = {}
        self.row_presence_misses: Dict[tuple[str, str], int] = {}
        self.started_at = now_iso()

    def close(self) -> None:
        self.history_writer.stop()

    def flush_history(self) -> None:
        self.history_writer.flush()

    def _persist_event(self, event_id: str) -> None:
        event = self.events.get(event_id)
        if event is None:
            return
        self.history_writer.enqueue_event(
            {
                **event,
                "eventId": event_id,
                "updatedAt": now_iso(),
            }
        )

    def _resolve_market_event_id(
        self,
        source_event_id: str,
        details: Any,
        local_now: datetime,
    ) -> str:
        """Match compact-feed IDs to WebSocket event IDs when they differ."""
        if source_event_id in self.events:
            return source_event_id
        if not isinstance(details, dict):
            return ""

        name = as_text(details.get("name"))
        if not name:
            return ""
        candidates = [
            (event_id, event)
            for event_id, event in self.events.items()
            if as_text(event.get("name")) == name
        ]
        if not candidates:
            return ""

        try:
            kickoff_ms = float(details.get("kickoff"))
        except (TypeError, ValueError):
            kickoff_ms = 0.0
        if kickoff_ms > 0:
            timed_candidates = []
            for event_id, event in candidates:
                kickoff = parse_kickoff(as_text(event.get("kickoff")))
                if not kickoff:
                    continue
                target = datetime.fromtimestamp(
                    kickoff_ms / 1000, tz=kickoff.tzinfo
                )
                kickoff_values = [kickoff]
                # The compact browser snapshot uses PS3838 local time while
                # cloud hosts commonly run in UTC.  Try the provider timezone
                # for naive stored strings as well, without changing the
                # dashboard-wide parsing contract.
                raw_kickoff = as_text(event.get("kickoff"))
                if "T" not in raw_kickoff and "+" not in raw_kickoff and "Z" not in raw_kickoff:
                    try:
                        local_value = datetime.fromisoformat(raw_kickoff).replace(
                            tzinfo=ZoneInfo("Asia/Shanghai")
                        ).astimezone(kickoff.tzinfo)
                        kickoff_values.append(local_value)
                    except ValueError:
                        pass
                if any(abs((value - target).total_seconds()) <= 600 for value in kickoff_values):
                    timed_candidates.append(event_id)
            if timed_candidates:
                live_candidates = [
                    event_id
                    for event_id in timed_candidates
                    if bool(self.events[event_id].get("isLive"))
                ]
                if len(live_candidates) == 1:
                    return live_candidates[0]
                # PS3838 can reuse the same teams and kickoff for an old feed
                # event and a current live event. Prefer the instance that is
                # still receiving updates instead of abandoning the match.
                return max(
                    timed_candidates,
                    key=lambda event_id: as_text(
                        self.events[event_id].get("lastCapturedAt")
                    ),
                )

        return candidates[0][0] if len(candidates) == 1 else ""

    def _apply_event_details(
        self,
        event_id: str,
        details: Any,
        local_now: datetime,
    ) -> bool:
        """Merge metadata from the compact snapshot or rendered event row."""
        if not isinstance(details, dict):
            return False
        name = as_text(details.get("name"))
        home = as_text(details.get("home"))
        away = as_text(details.get("away"))
        if (not home or not away) and " VS " in name:
            parts = name.split(" VS ", 1)
            home = home or parts[0].strip()
            away = away or parts[1].strip()

        kickoff_value = details.get("kickoff")
        kickoff = ""
        kickoff_ms = 0.0
        try:
            kickoff_ms = float(kickoff_value)
        except (TypeError, ValueError):
            kickoff = as_text(kickoff_value)
        if kickoff_ms > 100_000_000_000:
            kickoff = datetime.fromtimestamp(
                kickoff_ms / 1000, tz=local_now.tzinfo
            ).strftime("%Y-%m-%d %H:%M:%S")
        elif kickoff_ms > 0:
            kickoff = datetime.fromtimestamp(
                kickoff_ms, tz=local_now.tzinfo
            ).strftime("%Y-%m-%d %H:%M:%S")

        return self.register_event(
            event_id,
            home,
            away,
            as_text(details.get("league")),
            kickoff,
            as_text(details.get("scoreHome")),
            as_text(details.get("scoreAway")),
            as_text(details.get("matchMinute")),
            as_text(details.get("matchPeriod")),
            as_text(details.get("matchStatus")),
            as_text(details.get("sportId")),
            as_text(details.get("leagueId")),
        )

    def reconcile_event_presence(
        self,
        event_ids: Iterable[Any],
        observed_at: str = "",
        row_keys_by_event: Optional[Dict[str, Iterable[Any]]] = None,
        event_details_by_id: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Remove stale events and market rows after two absent presences.

        PS3838 exposes only the currently rendered event list. The dashboard
        otherwise retains every event it has seen, so finished or removed
        upcoming events can remain visible indefinitely. Live events are
        intentionally excluded here because their own staleness/finish guards
        are authoritative and event IDs can change across feed variants.

        The compact event snapshot also contains the currently offered
        full-time market rows. They are reconciled separately because odds
        updates are incremental and never announce withdrawn lines.
        """
        visible_ids = {
            as_text(event_id) for event_id in event_ids if as_text(event_id)
        }
        local_now = parse_kickoff(observed_at) or datetime.now().astimezone()
        removed: list[str] = []
        removed_rows: list[Dict[str, str]] = []
        unresolved_market_ids: list[str] = []
        resolved_keys: Dict[str, set[str]] = {}

        with self.lock:
            for event_id, event in list(self.events.items()):
                if event_id in visible_ids:
                    self.presence_misses.pop(event_id, None)
                    continue
                if bool(event.get("isLive")):
                    self.presence_misses.pop(event_id, None)
                    continue
                if not event_is_visible_today_upcoming(event, local_now):
                    self.presence_misses.pop(event_id, None)
                    continue

                misses = self.presence_misses.get(event_id, 0) + 1
                if misses < 2:
                    self.presence_misses[event_id] = misses
                    continue

                self.presence_misses.pop(event_id, None)
                self.events.pop(event_id, None)
                self.odds_by_event.pop(event_id, None)
                self.history.pop(event_id, None)
                for miss_key in list(self.row_presence_misses):
                    if miss_key[0] == event_id:
                        self.row_presence_misses.pop(miss_key, None)
                removed.append(event_id)

            for event_id in visible_ids:
                self.presence_misses.pop(event_id, None)

            if isinstance(row_keys_by_event, dict):
                details_by_id = (
                    event_details_by_id
                    if isinstance(event_details_by_id, dict)
                    else {}
                )
                for raw_event_id, details in details_by_id.items():
                    source_event_id = as_text(raw_event_id)
                    event_id = self._resolve_market_event_id(
                        source_event_id,
                        details,
                        local_now,
                    )
                    if event_id:
                        self._apply_event_details(
                            event_id,
                            details,
                            local_now,
                        )
                for raw_event_id, raw_keys in row_keys_by_event.items():
                    source_event_id = as_text(raw_event_id)
                    if not isinstance(raw_keys, list):
                        continue
                    event_id = self._resolve_market_event_id(
                        source_event_id,
                        details_by_id.get(source_event_id),
                        local_now,
                    )
                    if not event_id:
                        unresolved_market_ids.append(source_event_id)
                        continue
                    current_keys = {
                        as_text(key) for key in raw_keys if as_text(key)
                    }
                    # An empty list is treated as an invalid/partial snapshot;
                    # event-level reconciliation handles fully removed events.
                    if current_keys:
                        resolved_keys.setdefault(event_id, set()).update(current_keys)

                for event_id, current_keys in resolved_keys.items():
                    rows = self.odds_by_event.get(event_id, {})
                    event = self.events[event_id]
                    for key, row in list(rows.items()):
                        if key in current_keys:
                            self.row_presence_misses.pop((event_id, key), None)
                            continue
                        miss_key = (event_id, key)
                        misses = self.row_presence_misses.get(miss_key, 0) + 1
                        captured_at = parse_kickoff(row.get("capturedAt"), local_now)
                        recently_updated = bool(
                            captured_at
                            and (
                                local_now - captured_at
                            ).total_seconds()
                            < MARKET_ROW_STALE_AFTER_SECONDS
                        )
                        required_misses = (
                            MARKET_ROW_MISSES
                            if recently_updated
                            else MARKET_ROW_STALE_MISSES
                        )
                        if misses < required_misses:
                            self.row_presence_misses[miss_key] = misses
                            continue

                        self.row_presence_misses.pop(miss_key, None)
                        rows.pop(key, None)
                        self.history.get(event_id, {}).pop(key, None)
                        event["rowCount"] = max(0, int(event["rowCount"]) - 1)
                        if row.get("quoted"):
                            event["quoteCount"] = max(
                                0, int(event["quoteCount"]) - 1
                            )
                        removed_rows.append({"eventId": event_id, "key": key})

            if removed or removed_rows:
                self.revision += 1

        return {
            "ok": True,
            "observedAt": local_now.isoformat(timespec="milliseconds"),
            "present": len(visible_ids),
            "removed": len(removed),
            "removedEventIds": removed,
            "removedRows": len(removed_rows),
            "removedRowKeys": removed_rows,
            "matchedMarketEventIds": sorted(resolved_keys),
            "unmatchedMarketEventIds": sorted(unresolved_market_ids),
        }

    def _persist_point(
        self,
        event_id: str,
        stage: str,
        market: str,
        option: str,
        line: str,
        captured_at: str,
        decimal: Optional[float],
        hong_kong: Optional[float],
        quoted: bool,
        direction: str,
        delta: Optional[float],
        source: str,
    ) -> None:
        if decimal is None:
            return
        self.history_writer.enqueue_point(
            {
                "eventId": event_id,
                "stage": stage,
                "market": market,
                "option": option,
                "line": line,
                "capturedAt": captured_at,
                "decimal": decimal,
                "hongKong": hong_kong,
                "quoted": quoted,
                "direction": direction,
                "delta": delta,
                "source": source,
            }
        )

    def _ensure_event(self, event_id: str) -> Dict[str, Any]:
        event = self.events.get(event_id)
        if event is None:
            name = f"赛事 {event_id}"
            event = {
                "id": event_id,
                "name": name,
                "league": "",
                "sportId": "",
                "leagueId": "",
                "kickoff": "",
                "metadataStatus": "missing",
                "isLive": False,
                "scoreHome": "",
                "scoreAway": "",
                "matchMinute": "",
                "matchPeriod": "",
                "matchStatus": "",
                "rowCount": 0,
                "quoteCount": 0,
                "lastCapturedAt": "",
                "lastChangedAt": "",
            }
            self.events[event_id] = event
            self.odds_by_event[event_id] = {}
            self.history[event_id] = {}
            self._persist_event(event_id)
        return event

    def _set_event_live(self, event_id: str, is_live: bool) -> bool:
        event = self._ensure_event(event_id)
        if event.get("isLive") == bool(is_live):
            return False
        event["isLive"] = bool(is_live)
        self._persist_event(event_id)
        return True

    def _sync_event_rows(self, event_id: str) -> None:
        event = self._ensure_event(event_id)
        ev = self.events[event_id]
        for row in self.odds_by_event.get(event_id, {}).values():
            row["eventName"] = ev["name"]
            row["league"] = ev["league"]
            row["sportId"] = ev.get("sportId", "")
            row["leagueId"] = ev.get("leagueId", "")
            row["kickoff"] = ev["kickoff"]
            row["metadataStatus"] = ev["metadataStatus"]
            row["isLive"] = ev["isLive"]
            row["scoreHome"] = ev.get("scoreHome", "")
            row["scoreAway"] = ev.get("scoreAway", "")
            row["matchMinute"] = ev.get("matchMinute", "")
            row["matchPeriod"] = ev.get("matchPeriod", "")
            row["matchStatus"] = ev.get("matchStatus", "")

    def register_event(
        self,
        event_id: str,
        home: str,
        away: str,
        league: str,
        kickoff: str,
        score_home: str = "",
        score_away: str = "",
        match_minute: str = "",
        match_period: str = "",
        match_status: str = "",
        sport_id: str = "",
        league_id: str = "",
    ) -> bool:
        event_id = as_text(event_id)
        if not event_id:
            return False
        event = self._ensure_event(event_id)
        old = dict(event)
        home = as_text(home)
        away = as_text(away)
        name = f"{home} VS {away}" if home and away else event["name"]
        league = as_text(league) or event["league"]
        sport_id = as_text(sport_id) or event.get("sportId", "")
        league_id = as_text(league_id) or event.get("leagueId", "")
        kickoff = as_text(kickoff) or event["kickoff"]
        event.update(
            {
                "name": name,
                "league": league,
                "sportId": sport_id,
                "leagueId": league_id,
                "kickoff": kickoff,
                "metadataStatus": metadata_status(name, league, kickoff),
                "scoreHome": as_text(score_home) or event.get("scoreHome", ""),
                "scoreAway": as_text(score_away) or event.get("scoreAway", ""),
                "matchMinute": as_text(match_minute) or event.get("matchMinute", ""),
                "matchPeriod": as_text(match_period) or event.get("matchPeriod", ""),
                "matchStatus": as_text(match_status) or event.get("matchStatus", ""),
            }
        )
        changed = any(event[key] != old[key] for key in (
            "name", "league", "sportId", "leagueId", "kickoff", "metadataStatus",
            "scoreHome", "scoreAway", "matchMinute", "matchPeriod",
            "matchStatus",
        ))
        if changed:
            self._sync_event_rows(event_id)
            self._persist_event(event_id)
            self.revision += 1
        return changed

    def reload_metadata_from_db(self) -> int:
        """Merge metadata repaired outside this process into the live store."""
        changed_events = 0
        with self.lock:
            for stored in self.history_db.list_events():
                event_id = stored["id"]
                event = self._ensure_event(event_id)
                old = {
                    key: event.get(key)
                    for key in (
                        "name", "league", "sportId", "leagueId", "kickoff",
                        "metadataStatus",
                    )
                }
                stored_status = as_text(stored.get("metadataStatus")) or "missing"
                current_status = as_text(event.get("metadataStatus")) or "missing"
                if stored_status == "complete" or current_status != "complete":
                    event["name"] = as_text(stored.get("name")) or event["name"]
                    event["league"] = as_text(stored.get("league")) or event["league"]
                    event["kickoff"] = as_text(stored.get("kickoff")) or event["kickoff"]
                    event["metadataStatus"] = stored_status
                event["sportId"] = as_text(stored.get("sportId")) or event.get("sportId", "")
                event["leagueId"] = as_text(stored.get("leagueId")) or event.get("leagueId", "")
                event["lastCapturedAt"] = max(
                    as_text(event.get("lastCapturedAt")),
                    as_text(stored.get("lastCapturedAt")),
                )
                event["lastChangedAt"] = max(
                    as_text(event.get("lastChangedAt")),
                    as_text(stored.get("lastChangedAt")),
                )
                if any(event.get(key) != value for key, value in old.items()):
                    self._sync_event_rows(event_id)
                    changed_events += 1
            if changed_events:
                self.revision += 1
        return changed_events

    def load_seed(self) -> None:
        if not self.seed_path.exists():
            return
        persisted_events: set[str] = set()
        try:
            with self.seed_path.open(encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    event_id = as_text(raw.get("event_id"))
                    if not event_id:
                        continue
                    name = as_text(raw.get("比赛")) or f"赛事 {event_id}"
                    league = as_text(raw.get("联赛"))
                    sport_id = as_text(raw.get("sport_id"))
                    league_id = as_text(raw.get("league_id"))
                    kickoff = as_text(raw.get("开赛时间"))
                    status = as_text(raw.get("元数据状态")) or metadata_status(
                        name, league, kickoff
                    )
                    event = self._ensure_event(event_id)
                    event.update(
                        {
                            "name": name,
                            "league": league,
                            "sportId": sport_id,
                            "leagueId": league_id,
                            "kickoff": kickoff,
                            "metadataStatus": status,
                        }
                    )
                    if event_id not in persisted_events:
                        self._persist_event(event_id)
                        persisted_events.add(event_id)
                    stage = as_text(raw.get("阶段代码"))
                    market = as_text(raw.get("盘口代码"))
                    if not is_full_time_market(stage, market):
                        continue
                    option = as_text(raw.get("选项代码"))
                    line = as_text(raw.get("盘口线"))
                    captured_at = as_text(raw.get("采集时间"))
                    decimal = float_or_none(as_text(raw.get("decimal_odds")))
                    hong_kong = float_or_none(
                        as_text(raw.get("hong_kong_odds"))
                    )
                    quote_status = as_text(raw.get("报价状态"))
                    quoted = quote_status == "有报价" and decimal is not None
                    key = self._row_key(stage, market, option, line)
                    rows = self.odds_by_event[event_id]
                    if key in rows:
                        rows[key].update(
                            {
                                "eventName": name,
                                "league": league,
                                "sportId": sport_id,
                                "leagueId": league_id,
                                "kickoff": kickoff,
                                "metadataStatus": status,
                                "capturedAt": captured_at,
                                "decimal": decimal,
                                "hongKong": hong_kong,
                                "quoted": quoted,
                            }
                        )
                        continue
                    self._add_row(
                        event_id=event_id,
                        stage=stage,
                        market=market,
                        option=option,
                        line=line,
                        american=as_text(raw.get("美式赔率")),
                        raw=as_text(raw.get("原始赔率值")),
                        captured_at=captured_at,
                        decimal=decimal,
                        hong_kong=hong_kong,
                        quoted=quoted,
                        direction="flat",
                        previous_decimal=decimal,
                        delta=0.0,
                        serial=0,
                        history_seed=True,
                    )
                    self._persist_point(
                        event_id=event_id,
                        stage=stage,
                        market=market,
                        option=option,
                        line=line,
                        captured_at=captured_at,
                        decimal=decimal,
                        hong_kong=hong_kong,
                        quoted=quoted,
                        direction="seed",
                        delta=None,
                        source="seed",
                    )
                    self.seeded_rows += 1
                    if captured_at > self.last_captured_at:
                        self.last_captured_at = captured_at
            self.revision += 1
        except (OSError, csv.Error) as exc:
            print(f"[快照种子读取失败] {self.seed_path}: {exc}", flush=True)

    def _row_key(self, stage: str, market: str, option: str, line: str) -> str:
        return f"{stage}|{market}|{option}|{line}"

    def _add_row(
        self,
        event_id: str,
        stage: str,
        market: str,
        option: str,
        line: str,
        american: str,
        raw: str,
        captured_at: str,
        decimal: Optional[float],
        hong_kong: Optional[float],
        quoted: bool,
        direction: str,
        previous_decimal: Optional[float],
        delta: Optional[float],
        serial: int,
        history_seed: bool = False,
    ) -> Dict[str, Any]:
        event = self._ensure_event(event_id)
        key = self._row_key(stage, market, option, line)
        row = {
            "key": key,
            "eventId": event_id,
            "eventName": event["name"],
            "league": event["league"],
            "sportId": event.get("sportId", ""),
            "leagueId": event.get("leagueId", ""),
            "kickoff": event["kickoff"],
            "metadataStatus": event["metadataStatus"],
            "isLive": event["isLive"],
            "stage": stage,
            "market": market,
            "option": option,
            "line": line,
            "american": american,
            "raw": raw,
            "capturedAt": captured_at,
            "decimal": decimal,
            "hongKong": hong_kong,
            "quoted": quoted,
            # The latest *quoted* water is kept separately: Pinnacle suspends a
            # selection by sending an empty price, and blanking the value makes
            # a paired card look unquoted even though a recent water exists.
            "lastDecimal": decimal if quoted else None,
            "lastHongKong": hong_kong if quoted else None,
            "lastQuotedAt": captured_at if quoted else "",
            "direction": direction,
            "previousDecimal": previous_decimal,
            "delta": delta,
            "serial": serial,
        }
        self.odds_by_event[event_id][key] = row
        event["rowCount"] += 1
        if quoted:
            event["quoteCount"] += 1
        if captured_at > event["lastCapturedAt"]:
            event["lastCapturedAt"] = captured_at
        hist = self.history[event_id].setdefault(key, [])
        if history_seed and decimal is not None:
            hist.append({"t": captured_at, "v": decimal})
        return row

    def _update_row(
        self,
        event_id: str,
        stage: str,
        market: str,
        option: str,
        line: str,
        american: str,
        raw: str,
        captured_at: str,
        decimal: Optional[float],
        hong_kong: Optional[float],
        quoted: bool,
        change_source: str,
    ) -> None:
        self._ensure_event(event_id)
        rows = self.odds_by_event[event_id]
        key = self._row_key(stage, market, option, line)
        old = rows.get(key)
        if old and captured_at and old["capturedAt"] and captured_at < old["capturedAt"]:
            return
        if old is None:
            self._add_row(
                event_id, stage, market, option, line, american, raw,
                captured_at, decimal, hong_kong, quoted, "new",
                None, None, 0,
            )
            event = self.events[event_id]
            event["lastCapturedAt"] = max(event["lastCapturedAt"], captured_at)
            self._append_history(event_id, key, captured_at, decimal)
            self._record_change(
                event_id, stage, market, option, line, None, decimal,
                "new", None, captured_at, change_source,
            )
            self._persist_point(
                event_id, stage, market, option, line, captured_at,
                decimal, hong_kong, quoted, "new", None, change_source,
            )
            self.revision += 1
            return

        old_quoted = bool(old["quoted"])
        previous_decimal = old["decimal"]
        direction = "flat"
        delta: Optional[float] = None
        if previous_decimal is None and decimal is not None:
            direction = "new"
        elif previous_decimal is not None and decimal is None:
            direction = "down"
        elif previous_decimal is not None and decimal is not None:
            delta = round(decimal - previous_decimal, 4)
            if delta > 0.00005:
                direction = "up"
            elif delta < -0.00005:
                direction = "down"
        serial = old.get("serial", 0)
        if direction in {"up", "down", "new"}:
            self.change_serial += 1
            serial = self.change_serial
        # A suspension must not erase the last usable water: keep it for the
        # paired card (marked as suspended) while ``quoted``/``hongKong`` stay
        # strict so the arbitrage scanner never consumes a stale price.
        last_decimal = decimal if quoted else old.get("lastDecimal")
        last_hong_kong = hong_kong if quoted else old.get("lastHongKong")
        last_quoted_at = captured_at if quoted else old.get("lastQuotedAt", "")
        old.update(
            {
                "eventName": self.events[event_id]["name"],
                "league": self.events[event_id]["league"],
                "sportId": self.events[event_id].get("sportId", ""),
                "leagueId": self.events[event_id].get("leagueId", ""),
                "kickoff": self.events[event_id]["kickoff"],
                "metadataStatus": self.events[event_id]["metadataStatus"],
                "isLive": self.events[event_id]["isLive"],
                "stage": stage,
                "market": market,
                "option": option,
                "line": line,
                "american": american,
                "raw": raw,
                "capturedAt": captured_at,
                "decimal": decimal,
                "hongKong": hong_kong,
                "quoted": quoted,
                "lastDecimal": last_decimal,
                "lastHongKong": last_hong_kong,
                "lastQuotedAt": last_quoted_at,
                "direction": direction,
                "previousDecimal": previous_decimal,
                "delta": delta,
                "serial": serial,
            }
        )
        event = self.events[event_id]
        if quoted != old_quoted:
            event["quoteCount"] += 1 if quoted else -1
        event["lastCapturedAt"] = max(event["lastCapturedAt"], captured_at)
        self._append_history(event_id, key, captured_at, decimal)
        if direction in {"up", "down", "new"}:
            event["lastChangedAt"] = captured_at
            self.last_change_at = max(self.last_change_at, captured_at)
            self._record_change(
                event_id, stage, market, option, line, previous_decimal,
                decimal, direction, delta, captured_at, change_source,
            )
            self._persist_point(
                event_id, stage, market, option, line, captured_at,
                decimal, hong_kong, quoted, direction, delta, change_source,
            )
        self.revision += 1

    def _append_history(
        self, event_id: str, key: str, captured_at: str, decimal: Optional[float]
    ) -> None:
        if decimal is None:
            return
        hist = self.history[event_id].setdefault(key, [])
        if hist and hist[-1]["t"] == captured_at and hist[-1]["v"] == decimal:
            return
        hist.append({"t": captured_at, "v": decimal})
        if len(hist) > HISTORY_LIMIT:
            del hist[:-HISTORY_LIMIT]

    def _record_change(
        self,
        event_id: str,
        stage: str,
        market: str,
        option: str,
        line: str,
        old: Optional[float],
        new: Optional[float],
        direction: str,
        delta: Optional[float],
        captured_at: str,
        source: str,
    ) -> None:
        self.change_serial += 1
        self.changes.append(
            {
                "serial": self.change_serial,
                "eventId": event_id,
                "stage": stage,
                "market": market,
                "option": option,
                "line": line,
                "old": old,
                "new": new,
                "direction": direction,
                "delta": delta,
                "capturedAt": captured_at,
                "source": source,
            }
        )

    def process_line(self, line: str, source: str = "tail") -> bool:
        line = line.strip()
        if not line:
            return False
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            with self.lock:
                self.parse_errors += 1
            return False
        if not isinstance(record, dict):
            return False
        opcode = record.get("opcode")
        if opcode not in (None, 1):
            return False
        payload = record.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                return False
        if not isinstance(payload, (dict, list)):
            return False
        captured_at = as_text(record.get("captured_at")) or now_iso()
        messages = list(iter_messages(payload))
        if not messages:
            return False
        with self.lock:
            self.message_count += 1
            self.last_captured_at = max(self.last_captured_at, captured_at)
            for message in messages:
                odds = message.get("odds") or {}
                if not isinstance(odds, dict):
                    continue
                event_ids = set()
                for event_meta in iter_event_records(odds.get("l") or []):
                    self.register_event(
                        event_meta["eventId"],
                        event_meta["home"],
                        event_meta["away"],
                        event_meta["league"],
                        event_meta["kickoff"],
                        event_meta["scoreHome"],
                        event_meta["scoreAway"],
                        event_meta["matchMinute"],
                        event_meta["matchPeriod"],
                        event_meta["matchStatus"],
                        event_meta["sportId"],
                        event_meta["leagueId"],
                    )
                    event_ids.add(event_meta["eventId"])
                for event_meta in iter_event_records(odds.get("n") or []):
                    self.register_event(
                        event_meta["eventId"],
                        event_meta["home"],
                        event_meta["away"],
                        event_meta["league"],
                        event_meta["kickoff"],
                        event_meta["scoreHome"],
                        event_meta["scoreAway"],
                        event_meta["matchMinute"],
                        event_meta["matchPeriod"],
                        event_meta["matchStatus"],
                        event_meta["sportId"],
                        event_meta["leagueId"],
                    )
                    event_ids.add(event_meta["eventId"])
                rows = list(iter_leaf_rows(odds.get("u") or []))
                for row in rows:
                    event_id = as_text(row[12])
                    if not event_id:
                        continue
                    event_ids.add(event_id)
                live_value = odds.get("live")
                if isinstance(live_value, bool):
                    for event_id in event_ids:
                        if event_id:
                            self._set_event_live(event_id, live_value)
                for row in rows:
                    event_id = as_text(row[12])
                    if not event_id:
                        continue
                    stage = as_text(row[0])
                    market = as_text(row[1])
                    if not is_full_time_market(stage, market):
                        continue
                    option = as_text(row[2])
                    line_value = row[3]
                    if line_value is None:
                        line_value = ""
                    else:
                        try:
                            line_value = format(float(line_value), ".8g")
                        except (ValueError, TypeError):
                            line_value = as_text(line_value)
                    raw = as_text(row[5])
                    american = as_text(row[4])
                    normalized = normalize_odds(market, raw)
                    quoted = normalized["报价状态"] == "有报价"
                    decimal = float_or_none(normalized["decimal_odds"])
                    hong_kong = float_or_none(normalized["hong_kong_odds"])
                    self._update_row(
                        event_id=event_id,
                        stage=stage,
                        market=market,
                        option=option,
                        line=line_value,
                        american=american,
                        raw=raw,
                        captured_at=captured_at,
                        decimal=decimal,
                        hong_kong=hong_kong,
                        quoted=quoted,
                        change_source=source,
                    )
        return True

    def bootstrap_recent(self) -> None:
        if not self.capture_path.exists():
            return
        try:
            size = self.capture_path.stat().st_size
            if size <= 0:
                return
            start = max(0, size - RECENT_BOOTSTRAP_BYTES)
            with self.capture_path.open("rb") as handle:
                handle.seek(start)
                if start > 0:
                    handle.readline()
                data = handle.read()
            for raw_line in data.splitlines():
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if self.process_line(line, source="recent"):
                    self.recent_rows += 1
        except OSError as exc:
            print(f"[近期数据预载失败] {self.capture_path}: {exc}", flush=True)

    def serialize_row(self, event_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
        hist = self.history.get(event_id, {}).get(row["key"], [])
        return {
            **row,
            "decimalLabel": "" if row["decimal"] is None else f'{row["decimal"]:.4f}',
            "hongKongLabel": "" if row["hongKong"] is None else f'{row["hongKong"]:.4f}',
            "lastDecimalLabel": "" if row.get("lastDecimal") is None else f'{row["lastDecimal"]:.4f}',
            "lastHongKongLabel": "" if row.get("lastHongKong") is None else f'{row["lastHongKong"]:.4f}',
            "suspended": bool(row.get("quoted") is False and row.get("lastQuotedAt")),
            "history": hist[-HISTORY_LIMIT:],
        }

    def query_history(
        self,
        event_id: str,
        hours: float,
        limit: int,
        filters: Dict[str, str],
    ) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        since = now - timedelta(hours=hours)
        points, truncated = self.history_db.query_points(
            event_id=event_id,
            since=since.isoformat(timespec="milliseconds"),
            until=now.isoformat(timespec="milliseconds"),
            limit=limit,
            filters=filters,
        )
        event = self.events.get(event_id) or self.history_db.get_event(event_id)
        values = [
            float(point["decimal"])
            for point in points
            if point.get("decimal") is not None
        ]
        summary = {
            "count": len(points),
            "changes": sum(
                1 for point in points if point.get("direction") in {"up", "down", "new"}
            ),
            "first": values[0] if values else None,
            "last": values[-1] if values else None,
            "high": max(values) if values else None,
            "low": min(values) if values else None,
            "move": round(values[-1] - values[0], 4) if len(values) > 1 else 0.0,
        }
        return {
            "generatedAt": now_iso(),
            "eventId": event_id,
            "event": event,
            "filters": filters,
            "range": {
                "hours": hours,
                "from": since.isoformat(timespec="milliseconds"),
                "to": now.isoformat(timespec="milliseconds"),
            },
            "summary": summary,
            "truncated": truncated,
            "points": points,
            "database": {
                "path": str(self.database_path),
                "bytes": self.history_db.disk_bytes(),
                "points": self.history_writer.written_points,
                "queued": self.history_writer.tasks.qsize(),
                "dropped": self.history_writer.dropped,
                "errors": self.history_writer.errors,
            },
        }

    def snapshot(self, selected_event_id: str = "") -> Dict[str, Any]:
        with self.lock:
            now = datetime.now().astimezone()
            events = []
            visible_ids = set()
            for event in self.events.values():
                scope = event_scope(event, now)
                if not scope or not event_has_display_metadata(event):
                    continue
                visible_ids.add(event["id"])
                events.append({**event, "scope": scope})
            events.sort(
                key=lambda event: (
                    event.get("lastCapturedAt") or "",
                    event.get("quoteCount") or 0,
                ),
                reverse=True,
            )
            if not selected_event_id or selected_event_id not in visible_ids:
                selected_event_id = events[0]["id"] if events else ""
            rows = sorted(
                (
                    row
                    for row in self.odds_by_event.get(selected_event_id, {}).values()
                    if is_full_time_market(row.get("stage"), row.get("market"))
                ),
                key=lambda row: (
                    row["market"], row["stage"], row["option"],
                    row["line"]
                ),
            )
            changes = []
            change_count = 0
            for item in reversed(self.changes):
                if item["eventId"] not in visible_ids:
                    continue
                if not is_full_time_market(item.get("stage"), item.get("market")):
                    continue
                change_count += 1
                event = self.events.get(item["eventId"], {})
                if len(changes) < 24:
                    changes.append(
                        {
                            **item,
                            "eventName": event.get("name", f'赛事 {item["eventId"]}'),
                        }
                    )
            quote_count = sum(event["quoteCount"] for event in events)
            row_count = sum(event["rowCount"] for event in events)
            status_counts = Counter(event["metadataStatus"] for event in events)
            scope_counts = Counter(event["scope"] for event in events)
            try:
                capture_bytes = self.capture_path.stat().st_size
            except OSError:
                capture_bytes = 0
            lag_seconds = None
            if self.last_captured_at:
                try:
                    captured = datetime.fromisoformat(self.last_captured_at)
                    lag_seconds = max(
                        0, int((datetime.now().astimezone() - captured).total_seconds())
                    )
                except ValueError:
                    pass
            if lag_seconds is None:
                freshness = "no-data"
            elif lag_seconds <= 30:
                freshness = "live"
            elif lag_seconds <= 120:
                freshness = "delayed"
            else:
                freshness = "stale"
            return {
                "serverTime": now_iso(),
                "revision": self.revision,
                "freshness": freshness,
                "lagSeconds": lag_seconds,
                "stats": {
                    "events": len(events),
                    "live": scope_counts.get("live", 0),
                    "todayUpcoming": scope_counts.get("today", 0),
                    "rows": row_count,
                    "quotes": quote_count,
                    "complete": status_counts.get("complete", 0),
                    "partial": status_counts.get("partial", 0),
                    "missing": status_counts.get("missing", 0),
                    "changes": change_count,
                    "messages": self.message_count,
                    "parseErrors": self.parse_errors,
                },
                "source": {
                    "capturePath": str(self.capture_path),
                    "seedPath": str(self.seed_path),
                    "databasePath": str(self.database_path),
                    "databaseBytes": self.history_db.disk_bytes(),
                    "persistedPoints": self.history_writer.written_points,
                    "databaseQueued": self.history_writer.tasks.qsize(),
                    "databaseDropped": self.history_writer.dropped,
                    "databaseErrors": self.history_writer.errors,
                    "captureBytes": capture_bytes,
                    "lastCapturedAt": self.last_captured_at,
                    "lastChangeAt": self.last_change_at,
                    "seededRows": self.seeded_rows,
                    "recentMessages": self.recent_rows,
                    "startedAt": self.started_at,
                },
                "selectedEventId": selected_event_id,
                "events": events,
                "odds": [
                    self.serialize_row(selected_event_id, row) for row in rows
                ],
                "changes": changes,
            }

    def merge_events(self, horizon_hours: float = 12.0) -> List[Dict[str, Any]]:
        """Events the cross-book mapper may pair with a Crown fixture.

        The dashboard itself only lists live and same-day fixtures, but Crown
        publishes several days of fixtures at once.  Restricted to the daily
        view, an early-morning Pinnacle fixture is invisible and the mapper can
        only offer namesake competitions (a senior Crown fixture fell through to
        a U19 event this way).  Widen the candidate pool to the next few hours
        without changing the dashboard's own scope.
        """
        with self.lock:
            now = datetime.now().astimezone()
            horizon = now + timedelta(hours=max(0.0, float(horizon_hours)))
            events: List[Dict[str, Any]] = []
            for event in self.events.values():
                scope = event_scope(event, now)
                if scope:
                    if not event_has_display_metadata(event):
                        continue
                else:
                    if not event_has_display_metadata(event):
                        continue
                    kickoff = parse_kickoff(as_text(event.get("kickoff")), now)
                    if kickoff is None or not (now <= kickoff <= horizon):
                        continue
                    # A live-flagged event outside the daily scope is still
                    # eligible; mark it so the merge page can tell it apart.
                    scope = "upcoming"
                events.append({**event, "scope": scope})
            events.sort(
                key=lambda item: (
                    item.get("lastCapturedAt") or "",
                    item.get("quoteCount") or 0,
                ),
                reverse=True,
            )
            return events

    def _confirmed_event_variant_id(
        self, event_id: str, reference_event: Any = None
    ) -> str:
        """Find the freshest exact feed ID for a manually mapped fixture."""
        key = str(event_id or "").strip()
        if not key:
            return ""
        with self.lock:
            reference = reference_event if isinstance(reference_event, dict) else None
            if reference is None:
                reference = self.events.get(key) or self.history_db.get_event(key)
            if not isinstance(reference, dict):
                return key

            home = as_text(reference.get("home"))
            away = as_text(reference.get("away"))
            if not home or not away:
                name_parts = as_text(reference.get("name")).split(" VS ", 1)
                if len(name_parts) == 2:
                    home = home or name_parts[0].strip()
                    away = away or name_parts[1].strip()
            kickoff = parse_kickoff(as_text(reference.get("kickoff")))
            if not home or not away or kickoff is None:
                return key

            home_key, away_key = home.casefold(), away.casefold()
            league_id = as_text(reference.get("leagueId") or reference.get("league_id"))
            sport_id = as_text(reference.get("sportId") or reference.get("sport_id"))
            candidates = []
            for candidate_id, candidate in self.events.items():
                candidate_home = as_text(candidate.get("home"))
                candidate_away = as_text(candidate.get("away"))
                if not candidate_home or not candidate_away:
                    name_parts = as_text(candidate.get("name")).split(" VS ", 1)
                    if len(name_parts) == 2:
                        candidate_home = candidate_home or name_parts[0].strip()
                        candidate_away = candidate_away or name_parts[1].strip()
                if (candidate_home.casefold(), candidate_away.casefold()) != (home_key, away_key):
                    continue

                candidate_league = as_text(candidate.get("leagueId"))
                candidate_sport = as_text(candidate.get("sportId"))
                if league_id and candidate_league and league_id != candidate_league:
                    continue
                if sport_id and candidate_sport and sport_id != candidate_sport:
                    continue
                candidate_kickoff = parse_kickoff(as_text(candidate.get("kickoff")))
                if candidate_kickoff is None or abs((candidate_kickoff - kickoff).total_seconds()) > 600:
                    continue
                candidates.append((candidate_id, candidate))

            if not candidates:
                return key
            return max(
                candidates,
                key=lambda item: (
                    as_text(item[1].get("lastCapturedAt")),
                    bool(item[1].get("isLive")),
                ),
            )[0]

    def event_details(
        self, event_id: str, reference_event: Any = None
    ) -> Optional[Dict[str, Any]]:
        """Return one Pinnacle event and its current full-time market rows.

        The regular snapshot intentionally filters out stale or incomplete
        events.  Confirmed merge mappings still need a direct lookup so the
        homepage can keep showing the provider pair while a feed is catching
        up. If Pinnacle changes the fixture ID, the exact same fixture variant
        with the freshest captured odds is used while the persisted mapping
        remains stable; the feed's live flag can lag behind its odds updates.
        """
        key = str(event_id or "").strip()
        if not key:
            return None
        key = self._confirmed_event_variant_id(key, reference_event)
        with self.lock:
            event = self.events.get(key)
            if event is None:
                event = self.history_db.get_event(key)
            if event is None:
                return None
            current = [
                row
                for row in self.odds_by_event.get(key, {}).values()
                if is_full_time_market(row.get("stage"), row.get("market"))
            ]
            # After a restart the in-memory row can be rebuilt from a bare
            # suspension tick, losing the quote it had before. Recover the last
            # persisted water so the paired card can keep showing it (badged as
            # suspended) instead of a dash.
            if any(row.get("lastHongKong") is None for row in current):
                try:
                    persisted = self.history_db.latest_points(key)
                except (sqlite3.Error, OSError):
                    persisted = {}
                for row in current:
                    if row.get("lastHongKong") is not None:
                        continue
                    point = persisted.get(
                        (
                            str(row.get("stage") or ""), str(row.get("market") or ""),
                            str(row.get("option") or ""), str(row.get("line") or ""),
                        )
                    )
                    if not point or point.get("hongKong") is None:
                        continue
                    row["lastHongKong"] = point["hongKong"]
                    row["lastDecimal"] = point["decimal"]
                    row["lastQuotedAt"] = point["capturedAt"]
            rows = sorted(
                (self.serialize_row(key, row) for row in current),
                key=lambda row: (
                    row.get("market", ""), row.get("stage", ""),
                    row.get("option", ""), row.get("line", ""),
                ),
            )
            return {"event": {**event}, "odds": rows}

    def event_rows(
        self, event_id: str, reference_event: Any = None
    ) -> Optional[Dict[str, Any]]:
        """Lean event + current rows for the cross-book opportunity scan.

        Unlike :meth:`event_details` this deliberately omits per-row history,
        because the scanner reads every mapped fixture and only needs the
        current water, direction and capture time.
        """
        key = str(event_id or "").strip()
        if not key:
            return None
        key = self._confirmed_event_variant_id(key, reference_event)
        with self.lock:
            event = self.events.get(key)
            if event is None:
                event = self.history_db.get_event(key)
            if event is None:
                return None
            rows = [
                {
                    "market": row.get("market"),
                    "option": row.get("option"),
                    "line": row.get("line"),
                    "hongKong": row.get("hongKong"),
                    "quoted": row.get("quoted"),
                    "capturedAt": row.get("capturedAt"),
                    "direction": row.get("direction"),
                    "delta": row.get("delta"),
                }
                for row in self.odds_by_event.get(key, {}).values()
                if is_full_time_market(row.get("stage"), row.get("market"))
            ]
            return {"event": {**event}, "rows": rows}

    def freshness_snapshot(self) -> Dict[str, Any]:
        """Minimal snapshot for source freshness without serialising events."""
        with self.lock:
            last_captured_at = self.last_captured_at
        lag_seconds: Optional[int] = None
        if last_captured_at:
            try:
                captured = datetime.fromisoformat(last_captured_at)
                lag_seconds = max(
                    0, int((datetime.now().astimezone() - captured).total_seconds())
                )
            except ValueError:
                pass
        if lag_seconds is None:
            freshness = "no-data"
        elif lag_seconds <= 30:
            freshness = "live"
        elif lag_seconds <= 120:
            freshness = "delayed"
        else:
            freshness = "stale"
        return {
            "source": {"lastCapturedAt": last_captured_at},
            "freshness": freshness,
            "lagSeconds": lag_seconds,
        }
