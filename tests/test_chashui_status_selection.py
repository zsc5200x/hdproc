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


class TestChashuiStatusSelection(unittest.TestCase):
    def test_selects_readonly_quote_member_only(self) -> None:
        from hdzz.api.webui.server import select_readonly_member_from_members

        members = [
            {"id": "bet-1", "usage": "bet", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://bet"},
            {"id": "read-1", "usage": "read", "role": "quote", "status": "online", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://read"},
        ]

        got = select_readonly_member_from_members(members)
        self.assertEqual(str((got or {}).get("id") or ""), "read-1")

    def test_returns_none_when_only_bet_members_exist(self) -> None:
        from hdzz.api.webui.server import select_readonly_member_from_members

        members = [
            {"id": "bet-1", "usage": "bet", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://bet"},
        ]

        got = select_readonly_member_from_members(members)
        self.assertIsNone(got)

    def test_chashui_falls_back_to_online_bet_member_when_read_pool_is_empty(self) -> None:
        from hdzz.api.webui.server import select_chashui_member_from_members

        members = [
            {"id": "bet-1", "usage": "bet", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://bet"},
            {"id": "offline", "usage": "bet", "status": "offline", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://offline"},
        ]

        got = select_chashui_member_from_members(members)
        self.assertEqual(str((got or {}).get("id") or ""), "bet-1")

    def test_chashui_still_prefers_online_read_member_over_bet_fallback(self) -> None:
        from hdzz.api.webui.server import select_chashui_member_from_members

        members = [
            {"id": "bet-1", "usage": "bet", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://bet"},
            {"id": "read-1", "usage": "read", "role": "quote", "status": "online", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://read"},
        ]

        got = select_chashui_member_from_members(members)
        self.assertEqual(str((got or {}).get("id") or ""), "read-1")


if __name__ == "__main__":
    unittest.main()
