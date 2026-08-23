import json
import tempfile
import time
import unittest
from pathlib import Path


class TestTelegramBalanceSummary(unittest.TestCase):
    def test_online_balance_uses_maxcredit_first(self) -> None:
        from hdzz.api.telegram.bot import arsenal_online_balance_summary

        doc = {
            "members": [
                {
                    "id": "m1",
                    "label": "summer001",
                    "status": "online",
                    "currency": "RMB",
                    "nowcredit": "400.0",
                    "maxcredit": "1",
                    "cash": "0",
                },
                {
                    "id": "m2",
                    "label": "summer002",
                    "status": "online",
                    "currency": "RMB",
                    "nowcredit": "425.0",
                    "maxcredit": "1",
                    "cash": "0",
                },
            ]
        }

        text = arsenal_online_balance_summary(doc)

        self.assertIn("总额度：2.0 RMB", text)
        self.assertIn("summer001（在线）：1 RMB", text)
        self.assertIn("summer002（在线）：1 RMB", text)

    def test_login3_node_status_summary(self) -> None:
        from hdzz.api.telegram.bot import arsenal_login3_status_summary

        doc = {
            "members": [
                {"id": "m1", "label": "login3-a", "status": "online", "relogin_login_tab": 3},
                {"id": "m2", "label": "login3-b", "status": "offline", "relogin_login_tab": 3},
                {"id": "m3", "label": "login2-a", "status": "online", "relogin_login_tab": 2},
            ]
        }

        text = arsenal_login3_status_summary(doc)
        self.assertIn("登入3节点状态", text)
        self.assertIn("在线：1/2", text)
        self.assertIn("login3-a：在线", text)
        self.assertIn("login3-b：离线", text)
        self.assertNotIn("login2-a", text)

    def test_login3_target_status_summary_uses_targets_and_state(self) -> None:
        from hdzz.api.telegram.bot import login3_target_status_summary

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = root / "config.json"
            state_dir = root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)

            config_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {"name": "zxcv226699", "relogin_login_tab": 3},
                            {"name": "legacyNode"},
                        ]
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            (state_dir / "zxcv226699.json").write_text(
                json.dumps({"last_ok_at": time.time(), "last_status": "offline"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (state_dir / "legacyNode.json").write_text(
                json.dumps({"last_status": "offline"}, ensure_ascii=False),
                encoding="utf-8",
            )

            text = login3_target_status_summary(config_path=config_path, state_dir=state_dir)
            self.assertIn("登入3节点状态", text)
            self.assertIn("在线：1/1", text)
            self.assertIn("zxcv226699：在线", text)
            self.assertNotIn("legacyNode", text)


if __name__ == "__main__":
    unittest.main()
