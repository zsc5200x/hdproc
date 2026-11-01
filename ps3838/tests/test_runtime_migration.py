import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import os
from unittest.mock import patch
from src.core.runtime_lock import runtime_data_lock

spec = importlib.util.spec_from_file_location("migrate_runtime", Path(__file__).resolve().parents[1] / "scripts/migrate_runtime.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class RuntimeMigrationTests(unittest.TestCase):
    def test_roundtrip_preserves_bytes_mapping_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            original = '{"capture_path":"capture.jsonl","custom":42}'
            config.write_text(original)
            (root / "capture.jsonl").write_bytes(b"capture\n")
            (root / "capture-20261029-010000.jsonl").write_bytes(b"archive\n")
            (root / "logs").mkdir()
            (root / "logs/service.log").write_text("log")
            with sqlite3.connect(root / "fixture_merge.sqlite3") as db:
                db.execute("CREATE TABLE links (id TEXT)")
                db.execute("INSERT INTO links VALUES ('saved')")
            manifest = migration.apply(root, config, check_processes=False)
            self.assertTrue((root / "capture.jsonl").is_symlink())
            self.assertEqual((root / "var/capture/capture.jsonl").read_bytes(), b"capture\n")
            self.assertEqual(json.loads(config.read_text())["custom"], 42)
            self.assertEqual(json.loads(config.read_text())["capture_path"], "var/capture/capture.jsonl")
            self.assertEqual(migration.build_plan(root), [])
            with sqlite3.connect(root / "var/db/fixture_merge.sqlite3") as db:
                self.assertEqual(db.execute("SELECT id FROM links").fetchone(), ("saved",))
            migration.rollback(manifest, check_processes=False)
            self.assertFalse((root / "capture.jsonl").is_symlink())
            self.assertEqual(config.read_text(), original)
            self.assertEqual((root / "capture-20261029-010000.jsonl").read_bytes(), b"archive\n")

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "capture.jsonl").write_text("old")
            (root / "var/capture").mkdir(parents=True)
            (root / "var/capture/capture.jsonl").write_text("new")
            with self.assertRaises(RuntimeError):
                migration.apply(root, root / "config.json", check_processes=False)
            self.assertEqual((root / "capture.jsonl").read_text(), "old")

    def test_custom_paths_remain_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(json.dumps({"capture_path": "/elsewhere/custom.jsonl"}))
            (root / "capture.jsonl").write_text("legacy")
            migration.apply(root, config, check_processes=False)
            self.assertEqual(json.loads(config.read_text())["capture_path"], "/elsewhere/custom.jsonl")

    def test_custom_legacy_aliases_are_not_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            original = {"events_path": "custom.csv", "output": "custom-live.csv", "fixture_merge_db_path": "custom.sqlite3"}
            config.write_text(json.dumps(original))
            (root / "events_list.csv").write_text("old")
            (root / "latest_odds_live.csv").write_text("old")
            with sqlite3.connect(root / "custom.sqlite3") as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            migration.apply(root, config, check_processes=False)
            self.assertEqual(json.loads(config.read_text()), original)
            self.assertTrue((root / "custom.sqlite3").exists())
            self.assertFalse((root / "var/db/custom.sqlite3").exists())

    def test_interrupted_move_restores_files_and_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text('{"capture_path": "capture.jsonl"}')
            original = config.read_bytes()
            (root / "capture.jsonl").write_bytes(b"capture")
            (root / "events_list.csv").write_bytes(b"events")
            rename = Path.rename
            def fail_second(path, target):
                if path == root / "events_list.csv":
                    raise OSError("injected rename failure")
                return rename(path, target)
            with patch.object(Path, "rename", fail_second):
                with self.assertRaisesRegex(OSError, "injected"):
                    migration.apply(root, config, check_processes=False)
            self.assertEqual(config.read_bytes(), original)
            self.assertEqual((root / "capture.jsonl").read_bytes(), b"capture")
            self.assertFalse((root / "capture.jsonl").is_symlink())
            self.assertEqual((root / "events_list.csv").read_bytes(), b"events")
            manifest = next((root / "var/migrations").glob("*/manifest.json"))
            self.assertEqual(json.loads(manifest.read_text())["status"], "rolled_back")
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)

    def test_effective_environment_path_is_not_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.json"
            config.write_text("{}")
            (root / "capture.jsonl").write_text("capture")
            with sqlite3.connect(root / "custom.sqlite3") as db:
                db.execute("CREATE TABLE example (id INTEGER)")
            migration.apply(root, config, check_processes=False,
                            effective_config={"dashboard_db_path": str(root / "custom.sqlite3")})
            self.assertTrue((root / "custom.sqlite3").exists())
            self.assertFalse((root / "var/db/custom.sqlite3").exists())

    def test_running_service_prevents_any_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "capture.jsonl").write_text("capture")
            (root / ".run").mkdir()
            (root / ".run/capture.pid").write_text(str(os.getpid()))
            with self.assertRaisesRegex(RuntimeError, "仍存活"):
                migration.apply(root, root / "config.json")
            self.assertFalse((root / "var").exists())
            self.assertFalse((root / "capture.jsonl").is_symlink())

    def test_migration_and_maintenance_exclude_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with runtime_data_lock(root):
                with self.assertRaisesRegex(RuntimeError, "迁移或维护"):
                    with runtime_data_lock(root, exclusive=True):
                        self.fail("exclusive lock must not be acquired")
            with runtime_data_lock(root, exclusive=True):
                with self.assertRaisesRegex(RuntimeError, "迁移或维护"):
                    with runtime_data_lock(root):
                        self.fail("maintenance must not start")
