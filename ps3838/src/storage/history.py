"""SQLite history persistence and its queued writer."""
from __future__ import annotations
import queue
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from src.core.time import now_iso

class HistoryDatabase:
    """SQLite storage for event metadata and price-change points."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    league TEXT NOT NULL DEFAULT '',
                    sport_id TEXT NOT NULL DEFAULT '',
                    league_id TEXT NOT NULL DEFAULT '',
                    kickoff TEXT NOT NULL DEFAULT '',
                    metadata_status TEXT NOT NULL DEFAULT 'missing',
                    is_live INTEGER NOT NULL DEFAULT 0,
                    score_home TEXT NOT NULL DEFAULT '',
                    score_away TEXT NOT NULL DEFAULT '',
                    match_minute TEXT NOT NULL DEFAULT '',
                    match_period TEXT NOT NULL DEFAULT '',
                    match_status TEXT NOT NULL DEFAULT '',
                    last_captured_at TEXT NOT NULL DEFAULT '',
                    last_changed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS odds_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    market TEXT NOT NULL,
                    option TEXT NOT NULL,
                    line TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    decimal REAL NOT NULL,
                    hong_kong REAL,
                    quoted INTEGER NOT NULL DEFAULT 1,
                    direction TEXT NOT NULL DEFAULT 'flat',
                    delta REAL,
                    source TEXT NOT NULL DEFAULT 'tail',
                    created_at TEXT NOT NULL,
                    UNIQUE (
                        event_id, stage, market, option, line,
                        captured_at, decimal
                    )
                );

                CREATE INDEX IF NOT EXISTS idx_history_event_time
                    ON odds_history(event_id, captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_history_row_time
                    ON odds_history(
                        event_id, stage, market, option, line,
                        captured_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_history_time
                    ON odds_history(captured_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(events)").fetchall()
            }
            additions = {
                "sport_id": "TEXT NOT NULL DEFAULT ''",
                "league_id": "TEXT NOT NULL DEFAULT ''",
                "is_live": "INTEGER NOT NULL DEFAULT 0",
                "score_home": "TEXT NOT NULL DEFAULT ''",
                "score_away": "TEXT NOT NULL DEFAULT ''",
                "match_minute": "TEXT NOT NULL DEFAULT ''",
                "match_period": "TEXT NOT NULL DEFAULT ''",
                "match_status": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in additions.items():
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE events ADD COLUMN {column} {declaration}"
                    )
            connection.commit()

    def write_batch(
        self,
        events: list[Dict[str, Any]],
        points: list[Dict[str, Any]],
    ) -> int:
        if not events and not points:
            return 0
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA synchronous=NORMAL")
            if events:
                connection.executemany(
                    """
                    INSERT INTO events (
                        event_id, name, league, sport_id, league_id, kickoff,
                        metadata_status, is_live, score_home, score_away,
                        match_minute, match_period, match_status,
                        last_captured_at, last_changed_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_id) DO UPDATE SET
                        name = CASE
                            WHEN excluded.name <> ''
                                 AND excluded.name <> ('赛事 ' || excluded.event_id)
                            THEN excluded.name
                            ELSE events.name
                        END,
                        league = CASE
                            WHEN excluded.league <> '' THEN excluded.league
                            ELSE events.league
                        END,
                        sport_id = CASE
                            WHEN excluded.sport_id <> '' THEN excluded.sport_id
                            ELSE events.sport_id
                        END,
                        league_id = CASE
                            WHEN excluded.league_id <> '' THEN excluded.league_id
                            ELSE events.league_id
                        END,
                        kickoff = CASE
                            WHEN excluded.kickoff <> '' THEN excluded.kickoff
                            ELSE events.kickoff
                        END,
                        metadata_status = CASE
                            WHEN excluded.metadata_status = 'complete' THEN 'complete'
                            WHEN events.metadata_status = 'complete' THEN 'complete'
                            WHEN excluded.metadata_status = 'partial' THEN 'partial'
                            WHEN events.metadata_status = 'partial' THEN 'partial'
                            ELSE 'missing'
                        END,
                        is_live = excluded.is_live,
                        score_home = CASE
                            WHEN excluded.score_home <> '' THEN excluded.score_home
                            ELSE events.score_home
                        END,
                        score_away = CASE
                            WHEN excluded.score_away <> '' THEN excluded.score_away
                            ELSE events.score_away
                        END,
                        match_minute = CASE
                            WHEN excluded.match_minute <> '' THEN excluded.match_minute
                            ELSE events.match_minute
                        END,
                        match_period = CASE
                            WHEN excluded.match_period <> '' THEN excluded.match_period
                            ELSE events.match_period
                        END,
                        match_status = CASE
                            WHEN excluded.match_status <> '' THEN excluded.match_status
                            ELSE events.match_status
                        END,
                        last_captured_at = CASE
                            WHEN excluded.last_captured_at >= events.last_captured_at
                            THEN excluded.last_captured_at
                            ELSE events.last_captured_at
                        END,
                        last_changed_at = CASE
                            WHEN excluded.last_changed_at >= events.last_changed_at
                            THEN excluded.last_changed_at
                            ELSE events.last_changed_at
                        END,
                        updated_at = excluded.updated_at
                    WHERE excluded.updated_at >= events.updated_at
                    """,
                    [
                        (
                            item["eventId"],
                            item.get("name", ""),
                            item.get("league", ""),
                            item.get("sportId", ""),
                            item.get("leagueId", ""),
                            item.get("kickoff", ""),
                            item.get("metadataStatus", "missing"),
                            1 if item.get("isLive") else 0,
                            item.get("scoreHome", ""),
                            item.get("scoreAway", ""),
                            item.get("matchMinute", ""),
                            item.get("matchPeriod", ""),
                            item.get("matchStatus", ""),
                            item.get("lastCapturedAt", ""),
                            item.get("lastChangedAt", ""),
                            item.get("updatedAt", now_iso()),
                        )
                        for item in events
                    ],
                )
            before_points = connection.total_changes
            if points:
                created_at = now_iso()
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO odds_history (
                        event_id, stage, market, option, line,
                        captured_at, decimal, hong_kong, quoted,
                        direction, delta, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item["eventId"],
                            item.get("stage", ""),
                            item.get("market", ""),
                            item.get("option", ""),
                            item.get("line", ""),
                            item["capturedAt"],
                            item["decimal"],
                            item.get("hongKong"),
                            1 if item.get("quoted") else 0,
                            item.get("direction", "flat"),
                            item.get("delta"),
                            item.get("source", "tail"),
                            created_at,
                        )
                        for item in points
                    ],
                )
            inserted_points = connection.total_changes - before_points
            connection.commit()
            return inserted_points

    def point_count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM odds_history"
            ).fetchone()
        return int(row["count"]) if row else 0

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT event_id, name, league, sport_id, league_id, kickoff,
                       metadata_status, is_live, score_home, score_away,
                       match_minute, match_period, match_status,
                       last_captured_at, last_changed_at
                FROM events WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["event_id"],
            "name": row["name"],
            "league": row["league"],
            "sportId": row["sport_id"],
            "leagueId": row["league_id"],
            "kickoff": row["kickoff"],
            "metadataStatus": row["metadata_status"],
            "isLive": bool(row["is_live"]),
            "scoreHome": row["score_home"],
            "scoreAway": row["score_away"],
            "matchMinute": row["match_minute"],
            "matchPeriod": row["match_period"],
            "matchStatus": row["match_status"],
            "lastCapturedAt": row["last_captured_at"],
            "lastChangedAt": row["last_changed_at"],
        }

    def list_events(self) -> list[Dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT event_id, name, league, sport_id, league_id, kickoff,
                       metadata_status, is_live, score_home, score_away,
                       match_minute, match_period, match_status,
                       last_captured_at, last_changed_at
                FROM events
                ORDER BY event_id
                """
            ).fetchall()
        return [
            {
                "id": row["event_id"],
                "name": row["name"],
                "league": row["league"],
                "sportId": row["sport_id"],
                "leagueId": row["league_id"],
                "kickoff": row["kickoff"],
                "metadataStatus": row["metadata_status"],
                "isLive": bool(row["is_live"]),
                "scoreHome": row["score_home"],
                "scoreAway": row["score_away"],
                "matchMinute": row["match_minute"],
                "matchPeriod": row["match_period"],
                "matchStatus": row["match_status"],
                "lastCapturedAt": row["last_captured_at"],
                "lastChangedAt": row["last_changed_at"],
            }
            for row in rows
        ]

    def query_points(
        self,
        event_id: str,
        since: str,
        until: str,
        limit: int,
        filters: Dict[str, str],
    ) -> Tuple[list[Dict[str, Any]], bool]:
        clauses = [
            "event_id = ?",
            "captured_at >= ?",
            "captured_at <= ?",
        ]
        params: list[Any] = [event_id, since, until]
        for column in ("stage", "market", "option", "line"):
            value = filters.get(column)
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        query = f"""
            SELECT event_id, stage, market, option, line, captured_at,
                   decimal, hong_kong, quoted, direction, delta, source
            FROM odds_history
            WHERE {' AND '.join(clauses)}
            ORDER BY captured_at DESC, id DESC
            LIMIT ?
        """
        params.append(limit + 1)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        points = [
            {
                "eventId": row["event_id"],
                "stage": row["stage"],
                "market": row["market"],
                "option": row["option"],
                "line": row["line"],
                "capturedAt": row["captured_at"],
                "decimal": row["decimal"],
                "hongKong": row["hong_kong"],
                "quoted": bool(row["quoted"]),
                "direction": row["direction"],
                "delta": row["delta"],
                "source": row["source"],
            }
            for row in reversed(rows)
        ]
        return points, truncated

    def latest_points(self, event_id: str) -> Dict[Tuple[str, str, str, str], Dict[str, Any]]:
        """Latest persisted quote per (stage, market, option, line).

        Used by the confirmed-fixture card to recover the last usable water
        after a restart, when the in-memory row was rebuilt from a suspension
        tick and lost its previous quote.
        """
        key = str(event_id or "").strip()
        if not key:
            return {}
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT stage, market, option, line, captured_at, decimal, hong_kong
                FROM odds_history
                WHERE event_id = ? AND id IN (
                    SELECT MAX(id) FROM odds_history
                    WHERE event_id = ?
                    GROUP BY stage, market, option, line
                )
                """,
                (key, key),
            ).fetchall()
        return {
            (
                str(row["stage"]), str(row["market"]),
                str(row["option"]), str(row["line"]),
            ): {
                "capturedAt": str(row["captured_at"]),
                "decimal": row["decimal"],
                "hongKong": row["hong_kong"],
            }
            for row in rows
        }

    def disk_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
        return total


class HistoryWriter(threading.Thread):
    """Batch SQLite writes on a background thread."""

    def __init__(self, database: HistoryDatabase):
        super().__init__(name="history-writer", daemon=True)
        self.database = database
        self.tasks: queue.Queue = queue.Queue(maxsize=50_000)
        self.stop_event = threading.Event()
        self.written_points = database.point_count()
        self.written_events = 0
        self.dropped = 0
        self.errors = 0

    def enqueue_event(self, event: Dict[str, Any]) -> None:
        self._put(("event", event))

    def enqueue_point(self, point: Dict[str, Any]) -> None:
        self._put(("point", point))

    def _put(self, item: Tuple[str, Dict[str, Any]]) -> None:
        if self.stop_event.is_set():
            return
        try:
            self.tasks.put(item, timeout=0.5)
        except queue.Full:
            self.dropped += 1

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        try:
            self.tasks.put(None, timeout=1)
        except queue.Full:
            pass
        self.join()

    def flush(self) -> None:
        """Wait until all queued database writes have completed."""
        self.tasks.join()

    def _flush_batch(self, batch: list[Tuple[str, Dict[str, Any]]]) -> None:
        events = [item[1] for item in batch if item[0] == "event"]
        points = [item[1] for item in batch if item[0] == "point"]
        try:
            inserted_points = self.database.write_batch(events, points)
            self.written_points += max(0, inserted_points)
            self.written_events += len(events)
        except Exception as exc:
            self.errors += 1
            print(f"[历史数据库写入失败] {exc}", flush=True)

    def run(self) -> None:
        batch: list[Tuple[str, Dict[str, Any]]] = []
        deadline = time.monotonic() + 0.6
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                item = self.tasks.get(timeout=timeout or 0.01)
            except queue.Empty:
                if batch:
                    self._flush_batch(batch)
                    for _ in batch:
                        self.tasks.task_done()
                    batch = []
                if self.stop_event.is_set() and self.tasks.empty():
                    break
                deadline = time.monotonic() + 0.6
                continue

            if item is None:
                self.tasks.task_done()
                if batch:
                    self._flush_batch(batch)
                    for _ in batch:
                        self.tasks.task_done()
                    batch = []
                break

            batch.append(item)
            if len(batch) >= 500 or time.monotonic() >= deadline:
                self._flush_batch(batch)
                for _ in batch:
                    self.tasks.task_done()
                batch = []
                deadline = time.monotonic() + 0.6

        while True:
            try:
                item = self.tasks.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                batch.append(item)
            self.tasks.task_done()
        if batch:
            self._flush_batch(batch)

