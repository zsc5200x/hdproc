"""Migration compatibility links must not split active files from their archives."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture
from backfill_history import discover_capture_files, run_backfill
from live_snapshot import discover_rotated_capture_files


class CapturePathTests(unittest.TestCase):
    def test_snapshot_and_backfill_discover_archives_beside_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            actual = root / "var/capture/capture.jsonl"
            actual.parent.mkdir(parents=True)
            actual.write_text("")
            archive = actual.with_name("capture-20261030-010000.jsonl")
            archive.write_text("")
            legacy = root / "capture.jsonl"
            legacy.symlink_to(actual)
            self.assertEqual(discover_rotated_capture_files(legacy), [archive])
            self.assertEqual(discover_capture_files(legacy), [archive, actual])

    def test_collector_rotates_target_without_moving_compatibility_link(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            actual = root / "var/capture/capture.jsonl"
            actual.parent.mkdir(parents=True)
            actual.write_text("previous\n")
            legacy = root / "capture.jsonl"
            legacy.symlink_to(actual)
            config_path = root / "config.json"
            config_path.write_text("{}")
            with patch.object(capture, "CONFIG_PATH", config_path), patch.dict(os.environ, {"CAPTURE_OUTPUT": str(legacy)}):
                settings = capture._load_settings()
            self.assertEqual(settings["output"], actual)
            with patch.object(capture, "OUT", settings["output"]), patch.object(capture, "ROTATION_MB", 0.000001):
                capture.rotate_output_if_needed()
                settings["output"].write_text("next\n")
            self.assertTrue(legacy.is_symlink())
            self.assertEqual(legacy.read_text(), "next\n")
            archives = list(actual.parent.glob("capture-*.jsonl"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_text(), "previous\n")
            self.assertFalse(archives[0].is_symlink())
            self.assertEqual(list(root.glob("capture-*.jsonl")), [])

    def test_backfill_uses_configured_run_directory_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            active = root / "capture.jsonl"
            active.write_text("")
            config = {
                "capture_path": str(active), "run_dir": str(root / "custom-run"),
                "live_snapshot_file": str(root / "latest.csv"),
                "dashboard_db_path": str(root / "history.sqlite3"),
            }
            summary = run_backfill(config, report=lambda _: None)
            expected = root / "custom-run/backfill_history.state.json"
            self.assertEqual(summary["statePath"], str(expected))
            self.assertTrue(expected.is_file())
