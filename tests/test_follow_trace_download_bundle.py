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


class TestFollowTraceDownloadBundle(unittest.TestCase):
    def test_build_follow_trace_bundle_includes_order_trace_results_and_debug_files(self) -> None:
        from hdzz.api.webui.server import build_follow_trace_download_bundle

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            orders_dir = root / "orders"
            data_dir = root / "data"
            debug_dir = data_dir / "debug"
            orders_dir.mkdir(parents=True, exist_ok=True)
            debug_dir.mkdir(parents=True, exist_ok=True)

            order = {
                "orderId": "23650988901",
                "target": "zxcv226699",
                "line": "韦斯卡 0",
            }
            (orders_dir / "zxcv226699.jsonl").write_text(json.dumps(order, ensure_ascii=False) + "\n", encoding="utf-8")

            trace_rows = [
                {"event": "job_created", "orderId": "23650988901", "target": "zxcv226699"},
                {"event": "attempt_result", "orderId": "23650988901", "target": "zxcv226699", "ok": True},
            ]
            (data_dir / "follow_trace.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in trace_rows),
                encoding="utf-8",
            )

            result_row = {
                "orderId": "23650988901",
                "target": "zxcv226699",
                "ok": True,
                "bet_response_debug_path": "data/debug/bet_response_x.json",
                "bet_result_head": {"code": "560", "ticket_id": "23650989483"},
            }
            (data_dir / "follow_results.jsonl").write_text(json.dumps(result_row, ensure_ascii=False) + "\n", encoding="utf-8")
            (debug_dir / "bet_response_x.json").write_text(
                json.dumps({"response_kind": "xml", "response": "<serverresponse>ok</serverresponse>"}, ensure_ascii=False),
                encoding="utf-8",
            )

            bundle = build_follow_trace_download_bundle(
                data_dir=data_dir,
                orders_dir=orders_dir,
                order_id="23650988901",
                target="zxcv226699",
            )

        self.assertEqual(bundle["order"]["orderId"], "23650988901")
        self.assertEqual(len(bundle["trace_rows"]), 2)
        self.assertEqual(len(bundle["result_rows"]), 1)
        self.assertEqual(len(bundle["debug_files"]), 1)
        self.assertIn("<serverresponse>ok</serverresponse>", bundle["debug_files"][0]["content"])


if __name__ == "__main__":
    unittest.main()
