"""Durable manual provider mappings; scoring lives in the services layer."""
from __future__ import annotations
import csv
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from src.services.fixture_matching import MappingConflict, _snapshot

class FixtureMergeStore:
    """SQLite persistence for manually confirmed cross-provider fixture links."""

    def __init__(self, path: str | Path, seed_path: str | Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The mapping tables are tiny and read on every merge refresh; cache the
        # parsed rows and invalidate on any store write instead of re-reading
        # and JSON-decoding both tables several times per request.
        self._cache_lock = threading.Lock()
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_generation = 0
        self.seed_path = Path(seed_path) if seed_path else None
        self._initialize()
        if seed_path:
            self.seed_league_mappings(seed_path)
            with self._connection() as connection:
                self._seed_catalog_from_mappings(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        # WAL already allows concurrent readers with one writer; NORMAL drops
        # the extra fsync per commit while keeping WAL crash safety.
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA cache_size = -16000")
        try:
            connection.execute("PRAGMA mmap_size = 67108864")
        except sqlite3.DatabaseError:
            pass
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA wal_autocheckpoint = 1000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fixture_mappings (
                    pinnacle_event_id TEXT PRIMARY KEY,
                    crown_event_id TEXT NOT NULL UNIQUE,
                    pinnacle_snapshot TEXT NOT NULL,
                    crown_snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS league_mappings (
                    pinnacle_league_id TEXT PRIMARY KEY,
                    crown_league_id TEXT NOT NULL UNIQUE,
                    pinnacle_snapshot TEXT NOT NULL,
                    crown_snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS league_catalog (
                    provider TEXT NOT NULL,
                    league_id TEXT NOT NULL,
                    league_name TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    last_event_count INTEGER NOT NULL DEFAULT 0,
                    max_event_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (provider, league_id)
                )
                """
            )
            self._seed_catalog_from_mappings(connection)
            connection.execute(
                """
                CREATE VIEW IF NOT EXISTS league_dictionary AS
                SELECT
                    crown_league_id AS crownLeagueId,
                    json_extract(crown_snapshot, '$.name') AS crownLeagueName,
                    json_extract(crown_snapshot, '$.eventCount') AS crownEventCount,
                    pinnacle_league_id AS pinnacleLeagueId,
                    json_extract(pinnacle_snapshot, '$.name') AS pinnacleLeagueName,
                    json_extract(pinnacle_snapshot, '$.eventCount') AS pinnacleEventCount,
                    updated_at AS updatedAt
                FROM league_mappings
                """
            )
            connection.execute("PRAGMA optimize")
        with self._cache_lock:
            self._cache_generation += 1
            self._cache.clear()

    def _invalidate(self) -> None:
        with self._cache_lock:
            self._cache_generation += 1
            self._cache.clear()

    def _cached_list(self, key: str, loader) -> list[dict[str, Any]]:
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            generation = self._cache_generation
        value = loader()
        with self._cache_lock:
            if self._cache_generation == generation:
                self._cache[key] = value
        return value

    def close(self) -> None:
        """Keep the same lifecycle API as the other stores; connections are per-operation."""
        return None

    @staticmethod
    def _seed_catalog_from_mappings(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT crown_league_id, crown_snapshot, pinnacle_league_id, pinnacle_snapshot, updated_at "
            "FROM league_mappings"
        ).fetchall()
        values = []
        for row in rows:
            for provider, league_id, snapshot_key in (
                ("crown", row["crown_league_id"], "crown_snapshot"),
                ("pinnacle", row["pinnacle_league_id"], "pinnacle_snapshot"),
            ):
                try:
                    snapshot = json.loads(row[snapshot_key] or "{}")
                except (TypeError, ValueError):
                    snapshot = {}
                league_id = str(league_id or snapshot.get("id") or "").strip()
                if not league_id:
                    continue
                name = str(snapshot.get("name") or "未命名联赛")
                count = int(snapshot.get("eventCount") or 0)
                values.append((provider, league_id, name, row["updated_at"], row["updated_at"], count, count))
        if values:
            connection.executemany(
                """
                INSERT OR IGNORE INTO league_catalog (
                    provider, league_id, league_name, first_seen, last_seen,
                    last_event_count, max_event_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    def seed_league_mappings(
        self, seed_path: str | Path, *, replace_conflicts: bool = False
    ) -> dict[str, int]:
        """Seed the durable Crown <-> Pinnacle league dictionary.

        By default only missing pairs are added, so a normal restart never
        overwrites a newer manual choice. The one-time import passes
        ``replace_conflicts=True`` to replace stale wrong pairs.
        """
        path = Path(seed_path)
        if not path.exists():
            return {"added": 0, "replaced": 0, "skipped": 0, "conflicts": 0}
        existing = self.list_league_mappings()
        by_crown = {str(item.get("crownId") or ""): str(item.get("pinnacleId") or "") for item in existing}
        by_pinnacle = {str(item.get("pinnacleId") or ""): str(item.get("crownId") or "") for item in existing}
        with self._connection() as connection:
            count_rows = connection.execute(
                "SELECT provider, league_id, max_event_count FROM league_catalog"
            ).fetchall()
        counts = {
            (row["provider"], row["league_id"]): int(row["max_event_count"] or 0)
            for row in count_rows
        }
        added = replaced = skipped = conflicts = 0
        with open(path, encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                crown_id = str(row.get("crown_id") or "").strip()
                pinnacle_id = str(row.get("pinnacle_id") or "").strip()
                crown_name = str(row.get("crown_name") or "").strip()
                pinnacle_name = str(row.get("pinnacle_name") or "").strip()
                if not crown_id or not pinnacle_id:
                    continue
                if by_crown.get(crown_id) == pinnacle_id:
                    skipped += 1
                    continue
                if crown_id in by_crown or pinnacle_id in by_pinnacle:
                    if not replace_conflicts:
                        conflicts += 1
                        continue
                    # Remove every stale pair touching either side, plus the
                    # fixtures that belonged to it; the seed is authoritative.
                    targets = set()
                    if crown_id in by_crown:
                        targets.add((crown_id, by_crown[crown_id]))
                    if pinnacle_id in by_pinnacle:
                        targets.add((by_pinnacle[pinnacle_id], pinnacle_id))
                    for old_crown, old_pinnacle in targets:
                        for mapping in self.list_mappings():
                            crown_snapshot = mapping.get("crownSnapshot") or {}
                            pinnacle_snapshot = mapping.get("pinnacleSnapshot") or {}
                            if (
                                str(crown_snapshot.get("leagueId") or "") == old_crown
                                or (
                                    old_pinnacle
                                    and str(pinnacle_snapshot.get("leagueId") or "") == old_pinnacle
                                )
                            ):
                                self.unmap(str(mapping.get("pinnacleId") or ""))
                        try:
                            self.unmap_league(old_crown)
                        except MappingConflict:
                            pass
                        by_crown.pop(old_crown, None)
                        by_pinnacle.pop(old_pinnacle, None)
                    if by_crown.get(crown_id) or pinnacle_id in by_pinnacle:
                        conflicts += 1
                        continue
                    replaced += 1
                self.map_leagues(
                    {
                        "id": pinnacle_id,
                        "name": pinnacle_name,
                        "eventCount": counts.get(("pinnacle", pinnacle_id), 0),
                    },
                    {
                        "id": crown_id,
                        "name": crown_name,
                        "eventCount": counts.get(("crown", crown_id), 0),
                    },
                )
                by_crown[crown_id] = pinnacle_id
                by_pinnacle[pinnacle_id] = crown_id
                added += 1
        return {
            "added": added,
            "replaced": replaced,
            "skipped": skipped,
            "conflicts": conflicts,
        }

    def append_league_seed_mapping(
        self, pinnacle_league: dict[str, Any], crown_league: dict[str, Any]
    ) -> bool:
        """Keep manual league confirmations in the versioned seed file.

        The live SQLite table is the authority at runtime; this makes a manual
        confirmation survive a fresh DB / new installation as well.
        """
        if self.seed_path is None:
            return False
        path = self.seed_path
        if not path.exists():
            return False
        crown_id = str(crown_league.get("id") or "").strip()
        pinnacle_id = str(pinnacle_league.get("id") or "").strip()
        if not crown_id or not pinnacle_id:
            return False
        crown_name = str(crown_league.get("name") or "").strip()
        pinnacle_name = str(pinnacle_league.get("name") or "").strip()
        fieldnames = ["crown_id", "crown_name", "pinnacle_id", "pinnacle_name"]
        rows: list[dict[str, str]] = []
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                existing_fields = reader.fieldnames or fieldnames
                for row in reader:
                    if (
                        str(row.get("crown_id") or "").strip() == crown_id
                        or str(row.get("pinnacle_id") or "").strip() == pinnacle_id
                    ):
                        continue
                    rows.append({name: str(row.get(name) or "") for name in fieldnames})
            rows.append({
                "crown_id": crown_id,
                "crown_name": crown_name,
                "pinnacle_id": pinnacle_id,
                "pinnacle_name": pinnacle_name,
            })
            temporary = path.with_name(path.name + ".tmp")
            with open(temporary, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=existing_fields, delimiter="\t", lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(path)
        except OSError:
            return False
        return True

    def remove_league_seed_mapping(self, crown_league_id: str) -> bool:
        """Remove an intentionally unmapped pair from the versioned seed."""
        if self.seed_path is None:
            return False
        path = self.seed_path
        if not path.exists():
            return False
        crown_id = str(crown_league_id or "").strip()
        if not crown_id:
            return False
        fieldnames = ["crown_id", "crown_name", "pinnacle_id", "pinnacle_name"]
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                rows = [
                    {name: str(row.get(name) or "") for name in fieldnames}
                    for row in reader
                    if str(row.get("crown_id") or "").strip() != crown_id
                ]
            temporary = path.with_name(path.name + ".tmp")
            with open(temporary, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n"
                )
                writer.writeheader()
                writer.writerows(rows)
            temporary.replace(path)
        except OSError:
            return False
        return True

    def record_leagues(self, provider: str, leagues: list[dict[str, Any]]) -> int:
        """Persist every league seen in a provider feed, including after it ends."""
        provider = str(provider or "").strip().lower()
        if provider not in {"crown", "pinnacle"}:
            raise ValueError("联赛来源必须是 crown 或 pinnacle")
        now = self._timestamp()
        values = []
        for league in leagues:
            league_id = str(league.get("id") or "").strip()
            if not league_id or league_id == "--":
                continue
            name = str(league.get("name") or "未命名联赛")
            count = int(league.get("eventCount") or 0)
            values.append((provider, league_id, name, now, now, count, count))
        if not values:
            return 0
        with self._connection() as connection:
            connection.executemany(
                """
                INSERT INTO league_catalog (
                    provider, league_id, league_name, first_seen, last_seen,
                    last_event_count, max_event_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, league_id) DO UPDATE SET
                    league_name = excluded.league_name,
                    last_seen = excluded.last_seen,
                    last_event_count = excluded.last_event_count,
                    max_event_count = max(league_catalog.max_event_count, excluded.last_event_count)
                """,
                values,
            )
        return len(values)

    def list_league_catalog(self, provider: str = "") -> list[dict[str, Any]]:
        provider = str(provider or "").strip().lower()
        query = (
            "SELECT provider, league_id AS leagueId, league_name AS leagueName, "
            "first_seen AS firstSeen, last_seen AS lastSeen, "
            "last_event_count AS lastEventCount, max_event_count AS maxEventCount "
            "FROM league_catalog"
        )
        params: tuple[Any, ...] = ()
        if provider:
            query += " WHERE provider = ?"
            params = (provider,)
        query += " ORDER BY provider, league_name, league_id"
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def list_league_dictionary(self) -> list[dict[str, Any]]:
        """Readable Crown <-> Pinnacle league reference view."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM league_dictionary ORDER BY crownLeagueName"
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def list_mappings(self) -> list[dict[str, Any]]:
        return self._cached_list("fixtures", self._load_mappings)

    def _load_mappings(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT pinnacle_event_id, crown_event_id, pinnacle_snapshot,
                       crown_snapshot, created_at, updated_at
                FROM fixture_mappings
                ORDER BY pinnacle_event_id
                """
            ).fetchall()
        return [
            {
                "pinnacleId": row["pinnacle_event_id"],
                "crownId": row["crown_event_id"],
                "pinnacleSnapshot": json.loads(row["pinnacle_snapshot"]),
                "crownSnapshot": json.loads(row["crown_snapshot"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def list_league_mappings(self) -> list[dict[str, Any]]:
        return self._cached_list("leagues", self._load_league_mappings)

    def _load_league_mappings(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT pinnacle_league_id, crown_league_id, pinnacle_snapshot,
                       crown_snapshot, created_at, updated_at
                FROM league_mappings
                ORDER BY crown_league_id
                """
            ).fetchall()
        return [
            {
                "pinnacleId": row["pinnacle_league_id"],
                "crownId": row["crown_league_id"],
                "pinnacleSnapshot": json.loads(row["pinnacle_snapshot"]),
                "crownSnapshot": json.loads(row["crown_snapshot"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def league_mapping_for_crown(self, crown_league_id: str) -> dict[str, Any] | None:
        key = str(crown_league_id or "").strip()
        if not key:
            return None
        return next(
            (item for item in self.list_league_mappings() if item["crownId"] == key),
            None,
        )

    @staticmethod
    def _league_mapping_has_fixtures(
        connection: sqlite3.Connection, pinnacle_league_id: str, crown_league_id: str
    ) -> bool:
        rows = connection.execute(
            "SELECT pinnacle_snapshot, crown_snapshot FROM fixture_mappings"
        ).fetchall()
        for row in rows:
            pinnacle = json.loads(row["pinnacle_snapshot"])
            crown = json.loads(row["crown_snapshot"])
            if (
                str(pinnacle.get("leagueId") or "") == pinnacle_league_id
                or str(crown.get("leagueId") or "") == crown_league_id
            ):
                return True
        return False

    def map_leagues(
        self, pinnacle_league: dict[str, Any], crown_league: dict[str, Any]
    ) -> dict[str, Any]:
        pinnacle_id = str(pinnacle_league.get("id") or "").strip()
        crown_id = str(crown_league.get("id") or "").strip()
        if not pinnacle_id or not crown_id:
            raise ValueError("双方联赛 ID 均不能为空")
        pinnacle = {"id": pinnacle_id, "name": str(pinnacle_league.get("name") or ""),
                    "eventCount": int(pinnacle_league.get("eventCount") or 0)}
        crown = {"id": crown_id, "name": str(crown_league.get("name") or ""),
                 "eventCount": int(crown_league.get("eventCount") or 0)}
        now = self._timestamp()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_crown = connection.execute(
                "SELECT pinnacle_league_id FROM league_mappings WHERE crown_league_id = ?",
                (crown_id,),
            ).fetchone()
            existing_pinnacle = connection.execute(
                "SELECT crown_league_id FROM league_mappings WHERE pinnacle_league_id = ?",
                (pinnacle_id,),
            ).fetchone()
            if existing_crown and existing_crown["pinnacle_league_id"] != pinnacle_id:
                if self._league_mapping_has_fixtures(
                    connection, existing_crown["pinnacle_league_id"], crown_id
                ):
                    raise MappingConflict("该皇冠联赛下已有赛事关联，请先解除这些赛事关联。")
            if existing_pinnacle and existing_pinnacle["crown_league_id"] != crown_id:
                raise MappingConflict("该平博联赛已关联其他皇冠联赛，请先解除原联赛关联。")
            connection.execute(
                """
                INSERT INTO league_mappings (
                    pinnacle_league_id, crown_league_id, pinnacle_snapshot,
                    crown_snapshot, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(crown_league_id) DO UPDATE SET
                    pinnacle_league_id = excluded.pinnacle_league_id,
                    pinnacle_snapshot = excluded.pinnacle_snapshot,
                    crown_snapshot = excluded.crown_snapshot,
                    updated_at = excluded.updated_at
                """,
                (
                    pinnacle_id,
                    crown_id,
                    json.dumps(pinnacle, ensure_ascii=False, sort_keys=True),
                    json.dumps(crown, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        self._invalidate()
        return {"pinnacleId": pinnacle_id, "crownId": crown_id, "updatedAt": now}

    def unmap_league(self, crown_league_id: str) -> bool:
        key = str(crown_league_id or "").strip()
        if not key:
            raise ValueError("皇冠联赛 ID 不能为空")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT pinnacle_league_id FROM league_mappings WHERE crown_league_id = ?",
                (key,),
            ).fetchone()
            if row is None:
                return False
            if self._league_mapping_has_fixtures(connection, row["pinnacle_league_id"], key):
                raise MappingConflict("该联赛下已有赛事关联，请先解除赛事关联后再解除联赛关联。")
            cursor = connection.execute(
                "DELETE FROM league_mappings WHERE crown_league_id = ?", (key,)
            )
        self._invalidate()
        return cursor.rowcount > 0

    def map_events(
        self, pinnacle_event: dict[str, Any], crown_event: dict[str, Any]
    ) -> dict[str, Any]:
        pinnacle = _snapshot(pinnacle_event, "pinnacle")
        crown = _snapshot(crown_event, "crown")
        pinnacle_id, crown_id = pinnacle["id"], crown["id"]
        if not pinnacle_id or not crown_id:
            raise ValueError("双方赛事 ID 均不能为空")
        pinnacle_league_id = pinnacle["leagueId"]
        crown_league_id = crown["leagueId"]
        if not pinnacle_league_id or not crown_league_id:
            raise MappingConflict("赛事缺少联赛 ID，无法按联赛关系建立关联。")

        now = self._timestamp()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            league_mapping = connection.execute(
                """
                SELECT pinnacle_league_id FROM league_mappings
                WHERE crown_league_id = ?
                """,
                (crown_league_id,),
            ).fetchone()
            if not league_mapping or league_mapping["pinnacle_league_id"] != pinnacle_league_id:
                raise MappingConflict("请先确认这两场赛事所属皇冠联赛与平博联赛的对应关系。")
            existing_c = connection.execute(
                "SELECT pinnacle_event_id FROM fixture_mappings WHERE crown_event_id = ?",
                (crown_id,),
            ).fetchone()
            if existing_c and existing_c["pinnacle_event_id"] != pinnacle_id:
                raise MappingConflict("这场皇冠赛事已关联其他平博赛事，请先解除原关联。")

            connection.execute(
                """
                INSERT INTO fixture_mappings (
                    pinnacle_event_id, crown_event_id, pinnacle_snapshot,
                    crown_snapshot, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pinnacle_event_id) DO UPDATE SET
                    crown_event_id = excluded.crown_event_id,
                    pinnacle_snapshot = excluded.pinnacle_snapshot,
                    crown_snapshot = excluded.crown_snapshot,
                    updated_at = excluded.updated_at
                """,
                (
                    pinnacle_id,
                    crown_id,
                    json.dumps(pinnacle, ensure_ascii=False, sort_keys=True),
                    json.dumps(crown, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        self._invalidate()
        return {
            "pinnacleId": pinnacle_id,
            "crownId": crown_id,
            "updatedAt": now,
        }

    def unmap(self, pinnacle_id: str) -> bool:
        key = str(pinnacle_id or "").strip()
        if not key:
            raise ValueError("平博赛事 ID 不能为空")
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM fixture_mappings WHERE pinnacle_event_id = ?", (key,)
            )
        self._invalidate()
        return cursor.rowcount > 0
