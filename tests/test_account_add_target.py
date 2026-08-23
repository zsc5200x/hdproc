import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestAccountAddTarget(unittest.TestCase):
    def test_build_account_add_target_uses_custom_relogin_interval(self) -> None:
        from hdzz.api.webui.server import build_account_add_target

        target = build_account_add_target(
            name="demo3",
            base_url="https://ag.example.com",
            username="demo3",
            password="secret3",
            security_code="",
            tab=3,
            time_offset_hours=0,
            relogin_min_interval_seconds=18,
            login2_watch_members=[],
            login2_watch_downlines=["9025869", "zk1115"],
        )

        self.assertEqual(target["relogin_login_tab"], 3)
        self.assertEqual(target["relogin_min_interval_seconds"], 18)
        self.assertEqual(target["login2_watch_downlines"], ["9025869", "zk1115"])

    def test_build_account_add_target_defaults_to_five_seconds(self) -> None:
        from hdzz.api.webui.server import build_account_add_target

        target = build_account_add_target(
            name="demo2",
            base_url="https://ag.example.com",
            username="demo2",
            password="secret2",
            security_code="1234",
            tab=2,
            time_offset_hours=0,
            relogin_min_interval_seconds=None,
            login2_watch_members=["abc001"],
            login2_watch_downlines=["9025869"],
        )

        self.assertEqual(target["relogin_min_interval_seconds"], 5)
        self.assertEqual(target["security_code"], "1234")
        self.assertEqual(target["login2_watch_members"], ["abc001"])


if __name__ == "__main__":
    unittest.main()
