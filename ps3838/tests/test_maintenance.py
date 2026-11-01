import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from dashboard_server import HistoryDatabase
from maintenance import acquire_lock, backup_database, compress_capture_files, verify_database


class MaintenanceTests(unittest.TestCase):
    def test_maintenance_lock_follows_configured_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "custom-run"
            with acquire_lock({"run_dir": str(directory)}):
                self.assertTrue((directory / "maintenance.lock").exists())
                with self.assertRaisesRegex(RuntimeError, "维护任务"):
                    acquire_lock({"run_dir": str(directory)})

    def test_database_integrity_can_read_pending_wal(self):
        with tempfile.TemporaryDirectory() as temp:
            database_path = Path(temp) / "history.sqlite3"
            database = HistoryDatabase(database_path)
            # Keep a reader open so the committed writes stay in -wal instead
            # of being checkpointed when the writer closes.
            with sqlite3.connect(database_path) as keep_open:
                keep_open.execute("SELECT count(*) FROM events").fetchone()
                database.write_batch(
                    [
                        {
                            "eventId": "100",
                            "name": "Home VS Away",
                            "league": "Test League",
                            "kickoff": "2026-10-29T20:00:00+08:00",
                            "metadataStatus": "complete",
                            "updatedAt": "2026-10-28T05:00:00+08:00",
                        }
                    ],
                    [],
                )
                wal_path = Path(str(database_path) + "-wal")
                self.assertTrue(wal_path.is_file())
                self.assertGreater(wal_path.stat().st_size, 0)
                self.assertEqual(verify_database(database_path, immutable=False), "ok")
                self.assertEqual(
                    verify_database(database_path, immutable=False, quick=True), "ok"
                )
                with sqlite3.connect(
                    f"file:{database_path.as_posix()}?mode=ro", uri=True
                ) as reader:
                    self.assertEqual(
                        reader.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
                    )

    def test_database_backup_is_consistent_and_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database_path = root / "history.sqlite3"
            database = HistoryDatabase(database_path)
            database.write_batch(
                [
                    {
                        "eventId": "100",
                        "name": "Home VS Away",
                        "league": "Test League",
                        "kickoff": "2026-10-29T20:00:00+08:00",
                        "metadataStatus": "complete",
                        "updatedAt": "2026-10-28T05:00:00+08:00",
                    }
                ],
                [],
            )
            config = {
                "dashboard_db_path": str(database_path),
                "backup_dir": str(root / "backups"),
                "backup_keep": 2,
            }

            for _ in range(3):
                backup_database(config)

            backups = sorted((root / "backups").glob("odds_history-*.sqlite3"))
            self.assertEqual(len(backups), 2)
            with sqlite3.connect(backups[-1]) as connection:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT name FROM events").fetchone()[0], "Home VS Away")

    @unittest.skipUnless(shutil.which("zstd"), "需要 zstd")
    def test_rotated_capture_is_verified_before_compression(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "capture.jsonl"
            rotated = root / "capture-20261028-010203.jsonl"
            content = '{"captured_at":"2026-10-28T01:02:03+08:00"}\n' * 5
            current.write_text(content, encoding="utf-8")
            rotated.write_text(content, encoding="utf-8")
            config = {
                "capture_path": str(current),
                "capture_compress_after_hours": 24,
                "capture_compress_min_mb": 0,
            }

            result = compress_capture_files(config, force=True)
            compressed = Path(str(rotated) + ".zst")

            self.assertEqual(result[0]["action"], "compressed")
            self.assertEqual(result[0]["lines"], 5)
            self.assertTrue(current.exists())
            self.assertFalse(rotated.exists())
            self.assertTrue(compressed.exists())
            decompressed = subprocess.run(
                ["zstd", "-dc", str(compressed)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(decompressed, content)


if __name__ == "__main__":
    unittest.main()
