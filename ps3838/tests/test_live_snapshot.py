import csv
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_config import PROJECT_DIR


def payload_line(captured_at, payload):
    return json.dumps(
        {
            "captured_at": captured_at,
            "opcode": 1,
            "payload": json.dumps(payload),
        },
        ensure_ascii=False,
    ) + "\n"


class LiveSnapshotTests(unittest.TestCase):
    def test_metadata_update_repairs_tracked_rows(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        capture_path = root / "capture.jsonl"
        rotated_path = root / "capture-20261028-045959.jsonl.gz"
        events_path = root / "events.csv"
        output_path = root / "latest.csv"
        config_path = root / "config.json"

        events_path.write_text(
            "event_id,比赛,联赛,开赛时间\n", encoding="utf-8"
        )
        odds_only = {
            "type": "UPDATE_ODDS",
            "odds": {
                "u": [[
                    [0, 1, 0, None, 100.0, 2.0, 0, 0, 0, 0, "O", 0, 200],
                    [1, 1, 0, None, 100.0, 3.0, 0, 0, 0, 0, "O", 0, 200],
                ]]
            },
        }
        metadata_only = {
            "type": "UPDATE_ODDS",
            "odds": {
                "l": [
                    [
                        1,
                        "Soccer",
                        [[10, "Test League", [[200, "Home", "Away", 0, 1790000000000]]]],
                    ]
                ]
            },
        }
        with gzip.open(rotated_path, "wt", encoding="utf-8") as handle:
            handle.write(payload_line("2026-10-28T05:00:00.000+08:00", odds_only))
        capture_path.write_text(
            payload_line("2026-10-28T05:00:01.000+08:00", metadata_only),
            encoding="utf-8",
        )
        config_path.write_text(
            json.dumps(
                {
                    "capture_path": str(capture_path),
                    "events_file": str(events_path),
                    "live_snapshot_file": str(output_path),
                    "dashboard_db_path": str(root / "history.sqlite3"),
                }
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["PS3838_CONFIG"] = str(config_path)
        result = subprocess.run(
            [sys.executable, "live_snapshot.py", "--once"],
            cwd=PROJECT_DIR,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        with output_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["比赛"], "Home VS Away")
        self.assertEqual(rows[0]["联赛"], "Test League")
        self.assertEqual(rows[0]["sport_id"], "1")
        self.assertEqual(rows[0]["league_id"], "10")
        self.assertEqual(rows[0]["元数据状态"], "complete")


if __name__ == "__main__":
    unittest.main()
