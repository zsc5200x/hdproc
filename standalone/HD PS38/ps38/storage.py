"""事务保存订单和变更，JSONL 是可重新生成的导出，不是唯一数据源。"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from .orders import extract_orders, has_order_list, order_changes


def atomic_json(path: Path, payload) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp.chmod(0o600)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


class OrderStore:
    def __init__(self, directory: Path, account: str):
        self.directory = directory
        self.account = account
        directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(directory / "orders.sqlite3"))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
        """)
        # 上次若在事务提交后、JSONL 导出前退出，这里从数据库补齐导出。
        self.export()

    def ingest(self, payload):
        if not has_order_list(payload):
            raise ValueError("响应不是有效订单列表，保留旧快照")
        orders = extract_orders(payload, self.account)
        events = []
        stamp = time.time()
        with self.db:
            first_poll = self.db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone() is None
            for order in orders:
                oid = order["orderId"]
                previous = self.db.execute("SELECT payload FROM orders WHERE id=?", (oid,)).fetchone()
                changes = order_changes(json.loads(previous[0]), order) if previous else {}
                if previous is None or changes:
                    kind = "snapshot" if previous is None and first_poll else "new" if previous is None else "update"
                    event = {"event": kind, "ts": stamp, "orderId": oid, "changes": changes, "order": order}
                    cursor = self.db.execute("INSERT INTO events(payload) VALUES (?)", (json.dumps(event, ensure_ascii=False),))
                    events.append({"event_id": cursor.lastrowid, **event})
                self.db.execute("INSERT OR REPLACE INTO orders(id,payload) VALUES (?,?)", (oid, json.dumps(order, ensure_ascii=False)))
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('initialized','1')")
            snapshot = {"fetched_at": stamp, "count": len(orders), "orders": orders, "response": payload}
            self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('snapshot',?)", (json.dumps(snapshot, ensure_ascii=False),))
        self.export()
        return events, len(orders)

    def export(self):
        def lines(rows):
            return "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
        atomic_text(self.directory / "orders.jsonl", lines(json.loads(row[0]) for row in self.db.execute("SELECT payload FROM orders ORDER BY id")))
        atomic_text(self.directory / "events.jsonl", lines({"event_id": seq, **json.loads(value)} for seq, value in self.db.execute("SELECT seq,payload FROM events ORDER BY seq")))
        snapshot = self.db.execute("SELECT value FROM meta WHERE key='snapshot'").fetchone()
        if snapshot:
            atomic_json(self.directory / "snapshot.json", json.loads(snapshot[0]))

    def close(self):
        self.db.close()


class InstanceLock:
    """同一账号数据目录只允许运行一个实例；退出后系统自动释放锁。"""
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if self.file.read(1) == b"":
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("此账号已有采集进程，请在原终端操作；不要重复启动") from exc
        return self

    def __exit__(self, *args):
        if self.file:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()


def is_running(directory: Path) -> bool:
    if not (directory / "worker.lock").exists():
        return False
    try:
        with InstanceLock(directory / "worker.lock"):
            return False
    except RuntimeError:
        return True
