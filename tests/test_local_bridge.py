import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestLocalBridge(unittest.TestCase):
    def test_defaults_and_merge(self) -> None:
        from hdzz.api.webui.local_bridge import (
            ensure_local_bridge_defaults,
            merge_local_bridge_config,
            reset_local_bridge_config,
        )

        out = ensure_local_bridge_defaults({})
        self.assertFalse(out["local_bridge_configured"])
        self.assertEqual(out["local_bridge_target"], "ps3838")

        out = merge_local_bridge_config(
            out,
            {
                "local_bridge_enabled": True,
                "local_bridge_target": "ps3838-bridge",
                "local_bridge_token": "abc123",
                "local_bridge_allow_remote": False,
            },
        )
        self.assertTrue(out["local_bridge_configured"])
        self.assertTrue(out["local_bridge_enabled"])
        self.assertEqual(out["local_bridge_target"], "ps3838-bridge")
        self.assertEqual(out["local_bridge_token"], "abc123")

        out = reset_local_bridge_config(out)
        self.assertFalse(out["local_bridge_configured"])
        self.assertFalse(out["local_bridge_enabled"])
        self.assertEqual(out["local_bridge_target"], "")
        self.assertEqual(out["local_bridge_token"], "")

    def test_builds_order_with_crown_opponent_metadata(self) -> None:
        from hdzz.api.webui.local_bridge import build_local_bridge_order

        payload = {
            "target": "ps3838",
            "orderId": "12345678",
            "league": "英格兰超级联赛",
            "sides": "阿森纳 vs 切尔西",
            "betType": "(滚球) 让球",
            "line": "切尔西 +0.5",
            "odds": "0.97",
            "stake": "150",
            "gid": "9141295",
            "crownLeague": "英格兰超级联赛",
            "crownHome": "阿森纳",
            "crownAway": "切尔西",
            "crownOpponent": "切尔西 +0.5",
            "crownOdds": "0.97",
            "crownSnapshotAt": "2026-11-01 23:00:00",
        }

        order = build_local_bridge_order(payload, target_override="ps3838", now_ts=1793550000)

        self.assertEqual(order["target"], "ps3838")
        self.assertEqual(order["orderId"], "12345678")
        self.assertEqual(order["line"], "切尔西 +0.5")
        self.assertEqual(order["raw"]["_EVENT"], "9141295")
        self.assertEqual(order["raw"]["MARKET"], "rb")
        self.assertTrue(order["_skip_signal_forward"])
        self.assertEqual(order["_signal_origin"], "local_bridge")
        self.assertEqual(order["_local_bridge"]["crown_opponent"], "切尔西 +0.5")
        self.assertEqual(order["_local_bridge"]["crown_odds"], "0.97")

    def test_receive_writes_once_and_dedupes_retry(self) -> None:
        from hdzz.api.webui.local_bridge import (
            load_state,
            receive_local_bridge_signal,
        )

        payload = {
            "target": "ps3838",
            "orderId": "87654321",
            "league": "西班牙甲组联赛",
            "sides": "皇家马德里 vs 巴塞罗那",
            "betType": "(滚球) 大 / 小",
            "line": "大 3.5",
            "odds": "0.91",
            "stake": "200",
            "gid": "9141300",
            "crownOpponent": "大 3.5",
            "crownOdds": "0.91",
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"

            first = receive_local_bridge_signal(
                data_dir=data_dir,
                orders_dir=orders_dir,
                payload=payload,
                default_target="ps3838",
                remote="127.0.0.1",
                now_ts=1793550100,
            )
            second = receive_local_bridge_signal(
                data_dir=data_dir,
                orders_dir=orders_dir,
                payload=payload,
                default_target="ps3838",
                remote="127.0.0.1",
                now_ts=1793550110,
            )

            self.assertFalse(first["duplicate"])
            self.assertTrue(second["duplicate"])
            rows = (orders_dir / "ps3838.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            order = json.loads(rows[0])
            self.assertEqual(order["orderId"], "87654321")
            self.assertEqual(order["_signal_origin"], "local_bridge")

            state = load_state(data_dir)
            self.assertEqual(state["received_count"], 1)
            self.assertEqual(state["duplicate_count"], 1)
            self.assertEqual(state["last_signal"]["crownOpponent"], "大 3.5")

    def test_token_matching(self) -> None:
        from hdzz.api.webui.local_bridge import token_matches

        self.assertTrue(token_matches("", "anything"))
        self.assertTrue(token_matches("secret", "secret"))
        self.assertFalse(token_matches("secret", "wrong"))

    def test_webui_exposes_local_bridge_entry_and_receiver(self) -> None:
        page = (ROOT / "app" / "webui" / "index.html").read_text(encoding="utf-8")
        server = (ROOT / "src" / "hdzz" / "api" / "webui" / "server.py").read_text(encoding="utf-8")
        self.assertIn("本地桥接", page)
        self.assertIn('id="add-mode-bridge"', page)
        self.assertIn('id="add-bridge-fields"', page)
        self.assertIn('"/api/local-signal/push"', server)
        self.assertIn('"/api/local-bridge-config"', server)


if __name__ == "__main__":
    unittest.main()
