import unittest
import json
import tempfile
from pathlib import Path


class TestWebuiOrderDisplayLine(unittest.TestCase):
    def test_read_jsonl_file_reads_recent_tail_records(self) -> None:
        from hdzz.api.webui.server import read_jsonl_file

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            rows = [{"ts": index, "value": f"记录-{index}"} for index in range(5000)]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) + "unfinished",
                encoding="utf-8",
            )
            latest = read_jsonl_file(path, limit=3)

        self.assertEqual([row["ts"] for row in latest], [4999, 4998, 4997])

    def test_follow_order_summaries_share_one_scan_result(self) -> None:
        from hdzz.api.webui.server import _load_follow_order_summaries

        rows = [
            {
                "orderId": "1001",
                "ok": True,
                "ts_ms": 1000,
                "ticket_id": "9001",
                "submit_params": {"ioratio": "0.60", "wtype": "ROU", "chose_team": "C"},
            },
            {
                "orderId": "1001",
                "ok": True,
                "ts_ms": 2000,
                "ticket_id": "9002",
                "submit_params": {"ioratio": "0.80", "wtype": "ROU", "chose_team": "H"},
            },
            {
                "orderId": "1001",
                "ok": False,
                "ts_ms": 3000,
                "submit_params": {"ioratio": "1.20"},
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "follow_results.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            averages, latest = _load_follow_order_summaries(path, ["1001"])

        self.assertEqual(averages["1001"]["count"], 2)
        self.assertAlmostEqual(averages["1001"]["avg"], 0.7)
        self.assertEqual(latest["1001"]["ts_ms"], 2000)

    def test_follow_display_line_uses_submit_handicap_side_and_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 让球",
            "line": "阿兰亚体育 -0.5",
            "raw": {
                "TEAM_H": "阿兰亚体育 ",
                "TEAM_C": "高卡尔利士邦 ",
            },
        }
        follow_row = {
            "submit_params": {
                "wtype": "RE",
                "chose_team": "C",
                "con": "-1",
                "ratio": "-100",
            },
            "reverse_rule": {"applied": True},
        }
        line, reversed_applied = compose_follow_display_line(order, follow_row)
        self.assertEqual(line, "高卡尔利士邦 +0.5")
        self.assertTrue(reversed_applied)

    def test_follow_display_line_uses_submit_total_side_and_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 大 / 小",
            "line": "大 2.5",
        }
        follow_row = {
            "submit_params": {
                "wtype": "ROU",
                "chose_team": "H",
                "con": "-3",
                "ratio": "-100",
            },
            "reverse_rule": {"applied": True},
        }
        line, reversed_applied = compose_follow_display_line(order, follow_row)
        self.assertEqual(line, "小 2.5")
        self.assertTrue(reversed_applied)

    def test_follow_display_line_falls_back_to_backend_line(self) -> None:
        from hdzz.api.webui.server import compose_follow_display_line

        order = {
            "betType": "(滚球) 让球",
            "line": "杰志 -0.5",
        }
        line, reversed_applied = compose_follow_display_line(order, None)
        self.assertEqual(line, "杰志 -0.5")
        self.assertFalse(reversed_applied)


if __name__ == "__main__":
    unittest.main()
