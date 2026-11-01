import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backfill_history import run_backfill


def record(captured_at, price):
    payload = {
        "type": "UPDATE_ODDS",
        "odds": {
            "live": True,
            "l": [
                [
                    1,
                    "Soccer",
                    [[10, "Test League", [[100, "Home", "Away", 0, 1790000000000]]]],
                ]
            ],
            "u": [
                [[0, 1, 0, None, 100.0, price, 0, 0, 0, 0, "O", 0, 100]],
            ],
        },
    }
    return json.dumps(
        {
            "captured_at": captured_at,
            "opcode": 1,
            "payload": json.dumps(payload),
        },
        ensure_ascii=False,
    ) + "\n"


class BackfillHistoryTests(unittest.TestCase):
    def test_resumes_without_duplicate_points(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        rotated = root / "capture-20261028-010000.jsonl.gz"
        active = root / "capture.jsonl"
        state_path = root / ".run" / "backfill.json"
        with gzip.open(rotated, "wt", encoding="utf-8") as handle:
            handle.write(
                record("2026-10-28T05:00:00.000+08:00", "2.000")
                + record("2026-10-28T05:00:01.000+08:00", "2.100")
            )
        active.write_text(
            record("2026-10-28T05:00:02.000+08:00", "2.200"),
            encoding="utf-8",
        )
        config = {
            "capture_path": str(active),
            "live_snapshot_file": str(root / "latest.csv"),
            "dashboard_db_path": str(root / "history.sqlite3"),
        }

        first = run_backfill(
            config,
            files=[rotated, active],
            state_path=state_path,
            batch_size=1,
            report=lambda _: None,
        )
        second = run_backfill(
            config,
            files=[rotated, active],
            state_path=state_path,
            batch_size=1,
            report=lambda _: None,
        )

        self.assertEqual(first["pointsAdded"], 3)
        self.assertEqual(second["pointsAdded"], 0)
        self.assertEqual(second["lines"], 0)
        with sqlite3.connect(root / "history.sqlite3") as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM odds_history"
            ).fetchone()[0]
            prices = [
                row[0]
                for row in connection.execute(
                    "SELECT decimal FROM odds_history ORDER BY captured_at"
                )
            ]
        self.assertEqual(count, 3)
        self.assertEqual(prices, [2.0, 2.1, 2.2])


if __name__ == "__main__":
    unittest.main()
