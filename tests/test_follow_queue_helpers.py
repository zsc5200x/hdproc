import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowQueueHelpers(unittest.TestCase):
    def test_hydrate_done_requires_every_independent_split_part(self) -> None:
        from hdzz.core.follower.engine import hydrate_done_from_results

        with tempfile.TemporaryDirectory() as td:
            results_path = Path(td) / "follow_results.jsonl"
            first = {
                "target": "source",
                "orderId": "OID-SPLIT",
                "ok": True,
                "ticket_id": "T1",
                "split_count": 3,
                "split_index": 1,
                "ts": 101,
            }
            results_path.write_text(json.dumps(first) + "\n", encoding="utf-8")
            partial_done = {}
            hydrate_done_from_results(partial_done, results_path)

            self.assertNotIn("source|OID-SPLIT", partial_done)
            self.assertEqual(partial_done["source|OID-SPLIT#part:0"]["ticket_id"], "T1")

            rest = [
                {**first, "ticket_id": "T2", "split_index": 2, "ts": 102},
                {**first, "ticket_id": "T3", "split_index": 3, "ts": 103},
            ]
            with results_path.open("a", encoding="utf-8") as handle:
                for row in rest:
                    handle.write(json.dumps(row) + "\n")
            complete_done = {}
            hydrate_done_from_results(complete_done, results_path)

        aggregate = complete_done["source|OID-SPLIT"]
        self.assertEqual(aggregate["status"], "ok")
        self.assertEqual(aggregate["ticket_id"], "T3")
        self.assertEqual(aggregate["tickets"], ["T1", "T2", "T3"])
        self.assertEqual(aggregate["split_done"], 3)
        self.assertEqual(aggregate["split_total"], 3)

    def test_group_split_jobs_returns_one_parent_with_task_details(self) -> None:
        from hdzz.api.webui.server import _follow_queue_group_jobs

        jobs = {
            "source|OID-1#part:0": {
                "key": "source|OID-1#part:0",
                "parent_key": "source|OID-1",
                "independent_split": True,
                "order_id": "OID-1",
                "target": "source",
                "stake": 300,
                "split_count": 3,
                "split_index": 0,
                "split_stakes": [100, 100, 100],
                "attempts": 1,
                "next_try_at": 105,
                "member_id": "member-a",
                "order": {"orderId": "OID-1", "target": "source"},
            },
            "source|OID-1#part:1": {
                "key": "source|OID-1#part:1",
                "parent_key": "source|OID-1",
                "independent_split": True,
                "order_id": "OID-1",
                "target": "source",
                "stake": 300,
                "split_count": 3,
                "split_index": 1,
                "split_stakes": [100, 100, 100],
                "attempts": 2,
                "next_try_at": 103,
                "member_id": "member-b",
                "order": {"orderId": "OID-1", "target": "source"},
            },
            "source|OID-1#part:2": {
                "key": "source|OID-1#part:2",
                "parent_key": "source|OID-1",
                "independent_split": True,
                "order_id": "OID-1",
                "target": "source",
                "stake": 300,
                "split_count": 3,
                "split_index": 2,
                "split_stakes": [100, 100, 100],
                "attempts": 0,
                "next_try_at": 104,
                "member_id": "",
                "order": {"orderId": "OID-1", "target": "source"},
            },
        }

        grouped = _follow_queue_group_jobs(jobs)

        self.assertEqual(list(grouped), ["source|OID-1"])
        parent = grouped["source|OID-1"]
        self.assertEqual(parent["key"], "source|OID-1")
        self.assertEqual(parent["next_try_at"], 103)
        self.assertEqual(parent["attempts"], 3)
        self.assertEqual(parent["active_task_count"], 3)
        self.assertEqual([task["split_index"] for task in parent["split_tasks"]], [0, 1, 2])

    def test_terminal_item_keeps_original_order_and_maps_success(self) -> None:
        from hdzz.api.webui.server import _follow_queue_terminal_item

        order = {
            "target": "summer774411",
            "orderId": "OID-OK",
            "sides": "费哈 v 希拉尔 (0 - 0)",
            "line": "大 2.5",
        }
        out = _follow_queue_terminal_item(
            "summer774411|OID-OK",
            {"status": "ok", "ticket_id": "T-OK", "ts": 123},
            order,
        )

        self.assertEqual(out["key"], "summer774411|OID-OK")
        self.assertEqual(out["status"], "done")
        self.assertTrue(out["terminal"])
        self.assertEqual(out["order"], order)
        self.assertEqual(out["job"]["ticket_id"], "T-OK")

    def test_terminal_item_keeps_failure_reason_and_original_order(self) -> None:
        from hdzz.api.webui.server import _follow_queue_terminal_item

        order = {
            "target": "summer774411",
            "orderId": "OID-FAIL",
            "sides": "费哈 v 希拉尔 (0 - 0)",
            "line": "大 2.5 / 3",
        }
        out = _follow_queue_terminal_item(
            "summer774411|OID-FAIL",
            {"status": "failed", "reason": "目标盘口线等待超时", "ts": 456},
            order,
        )

        self.assertEqual(out["status"], "failed")
        self.assertTrue(out["terminal"])
        self.assertEqual(out["order"], order)
        self.assertEqual(out["job"]["last_message"], "目标盘口线等待超时")
        self.assertEqual(out["job"]["reason"], "目标盘口线等待超时")

    def test_done_order_map_recovers_latest_original_order(self) -> None:
        from hdzz.api.webui.server import _follow_queue_done_order_map

        with tempfile.TemporaryDirectory() as td:
            orders_dir = Path(td)
            path = orders_dir / "summer774411.jsonl"
            path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in [
                        {"target": "summer774411", "orderId": "OID-1", "ts": 10, "line": "大 2.5"},
                        {"target": "summer774411", "orderId": "OID-1", "ts": 20, "line": "大 2.5 / 3"},
                    ]
                ),
                encoding="utf-8",
            )

            out = _follow_queue_done_order_map(orders_dir, ["summer774411|OID-1"])

        self.assertEqual(out["summer774411|OID-1"]["line"], "大 2.5 / 3")

    def test_collect_follow_generated_order_keys_uses_done_and_results(self) -> None:
        from hdzz.core.follower.engine import collect_follow_generated_order_keys

        with tempfile.TemporaryDirectory() as td:
            results_path = Path(td) / "follow_results.jsonl"
            rows = [
                {"target": "srcA", "ok": True, "ticket_id": "T3001"},
                {"target": "srcA", "ok": False, "ticket_id": "T3002"},
                {"target": "srcB", "ok": True, "ticket_id": ""},
            ]
            results_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

            done = {
                "srcA|A1001": {"status": "ok", "ticket_id": "T1001", "tickets": ["T1001", "T1002"]},
                "srcB|B2001": {"status": "ok", "ticket_id": "T2001"},
                "srcC|C3001": {"status": "failed", "ticket_id": ""},
            }

            out = collect_follow_generated_order_keys(done=done, results_path=results_path)

        self.assertEqual(
            out,
            {
                "srcA|T1001",
                "srcA|T1002",
                "srcA|T3001",
                "srcB|T2001",
                "srcA|T3002",
            },
        )

    def test_simplify_order_over_under_live(self) -> None:
        from hdzz.api.webui.server import _follow_queue_simplify_order

        order = {
            "ts": 1770557999,
            "bet_ts": 1770514793,
            "target": "zxcv226699",
            "orderId": "23392039657",
            "sport": "足球",
            "stake": "100.0",
            "odds": "0.76",
            "league": "比利时甲组联赛A",
            "sides": "亨克 v 安德莱赫特 (0 - 0)",
            "betType": "(滚球) 大 / 小",
            "line": "小 1 / 1.5",
            "raw": {
                "GTYPE": "FT",
                "MARKET": "rb",
                "_EVENT": "8517475",
                "WAGERSTYPE": "(滚球) 大 / 小",
                "ORDER_TYPE": "小",
                "ORDER_CON": "1 / 1.5",
                "TEAM_H": "亨克",
                "TEAM_C": "安德莱赫特",
            },
        }

        out = _follow_queue_simplify_order(order)
        self.assertEqual(out.get("gid"), "8517475")
        self.assertEqual(out.get("market"), "rb")
        self.assertIn("derived", out)
        derived = out["derived"]
        self.assertEqual(derived.get("wtype"), "ROU")
        self.assertEqual(derived.get("chose_team"), "H")  # 小
        self.assertEqual(derived.get("mode"), "live")
        self.assertAlmostEqual(float(derived.get("target_line_val")), 1.25, places=6)
        self.assertEqual(derived.get("target_line_txt"), "1.25")
        self.assertEqual(bool(derived.get("is_over")), False)

    def test_simplify_order_handicap_live(self) -> None:
        from hdzz.api.webui.server import _follow_queue_simplify_order

        order = {
            "ts": 1770557260,
            "bet_ts": 1770514050,
            "target": "zxcv226699",
            "orderId": "23391961386",
            "sport": "足球",
            "stake": "100.0",
            "odds": "0.79",
            "league": "西班牙甲组联赛",
            "sides": "阿拉维斯 v 赫塔费 (0 - 0)",
            "betType": "(滚球) 让球",
            "line": "赫塔费 +0 / 0.5",
            "raw": {
                "GTYPE": "FT",
                "MARKET": "rb",
                "_EVENT": "8514043",
                "WAGERSTYPE": "(滚球) 让球",
                "ORDER_TYPE": "赫塔费",
                "ORDER_CON": "0 / 0.5",
                "TEAM_H": "阿拉维斯",
                "TEAM_C": "赫塔费",
            },
        }

        out = _follow_queue_simplify_order(order)
        derived = out["derived"]
        self.assertEqual(derived.get("wtype"), "RE")
        self.assertEqual(derived.get("chose_team"), "C")  # 客队
        self.assertEqual(derived.get("mode"), "live")
        self.assertAlmostEqual(float(derived.get("target_line_val")), 0.25, places=6)
        self.assertEqual(derived.get("target_line_txt"), "+0 / 0.5")

    def test_load_latest_follow_rows_by_order_keeps_latest_failure_details(self) -> None:
        from hdzz.api.webui.server import _load_latest_follow_rows_by_order

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "follow_results.jsonl"
            rows = [
                {
                    "ts_ms": 1000,
                    "orderId": "23650069461",
                    "ok": False,
                    "message": "系统异常（ConnectionError），正在重试。",
                    "error_type": "ConnectionError",
                    "exception": "ConnectionError('boom')",
                    "response_head": "<html>blocked</html>",
                },
                {
                    "ts_ms": 2000,
                    "orderId": "23650069461",
                    "ok": False,
                    "message": "系统异常（ReadTimeout），正在重试。",
                    "error_type": "ReadTimeout",
                    "exception": "ReadTimeout('slow')",
                    "response_head": "",
                },
            ]
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

            out = _load_latest_follow_rows_by_order(path, ["23650069461"], ok_only=False)

        self.assertIn("23650069461", out)
        self.assertEqual(out["23650069461"]["error_type"], "ReadTimeout")
        self.assertIn("ReadTimeout", out["23650069461"]["message"])
        self.assertIn("slow", out["23650069461"]["exception"])

    def test_clear_follow_queue_state_removes_pending_and_jobs_only(self) -> None:
        from hdzz.api.webui.server import _clear_follow_queue_state, read_json_file

        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            state_path = data_dir / "follow_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "pending_orders": {
                            "qax228|111": {"orderId": "111", "target": "qax228", "stake": "1000"}
                        },
                        "jobs": {
                            "qax228|222": {
                                "order": {"orderId": "222", "target": "qax228"},
                                "attempts": 2,
                            }
                        },
                        "done": {
                            "old|1": {"status": "ok", "ts": 100}
                        },
                        "offsets": {"qax228": 123},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = _clear_follow_queue_state(data_dir, req_at=1777667000.0)
            updated = read_json_file(state_path, {})

        self.assertEqual(result["pending_orders_before"], 1)
        self.assertEqual(result["jobs_before"], 1)
        self.assertEqual(updated.get("pending_orders"), {})
        self.assertEqual(updated.get("jobs"), {})
        self.assertEqual(updated["offsets"], {"qax228": 123})
        self.assertEqual(updated["done"]["old|1"], {"status": "ok", "ts": 100})
        self.assertEqual(updated["done"]["qax228|111"]["status"], "cleared")
        self.assertEqual(updated["done"]["qax228|111"]["reason"], "manual_queue_clear")
        self.assertEqual(updated["done"]["qax228|111"]["order"]["orderId"], "111")
        self.assertEqual(updated["done"]["qax228|222"]["status"], "cleared")
        self.assertEqual(updated["done"]["qax228|222"]["job"]["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
