import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestChashuiQuotePool(unittest.TestCase):
    def test_filters_only_online_read_members_with_complete_session(self) -> None:
        from hdzz.api.webui.chashui_quote_pool import list_readonly_quote_candidates

        members = [
            {"id": "bet-1", "usage": "bet", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://bet"},
            {"id": "read-1", "usage": "read", "status": "online", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://read-1"},
            {"id": "read-2", "usage": "read", "status": "offline", "uid": "u3", "cookie": "c3", "ver": "v3", "base_url": "https://read-2"},
            {"id": "read-3", "usage": "read", "status": "online", "uid": "", "cookie": "c4", "ver": "v4", "base_url": "https://read-3"},
        ]

        out = list_readonly_quote_candidates(members)
        self.assertEqual([m["id"] for m in out], ["read-1"])

    def test_pick_random_prefers_requested_member_when_still_eligible(self) -> None:
        from hdzz.api.webui.chashui_quote_pool import pick_readonly_quote_member

        members = [
            {"id": "read-1", "usage": "read", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://read-1"},
            {"id": "read-2", "usage": "read", "status": "online", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://read-2"},
        ]

        with patch("hdzz.api.webui.chashui_quote_pool.random.choice", return_value=members[1]):
            out = pick_readonly_quote_member(members, preferred_member_id="read-1")
        self.assertEqual(str((out or {}).get("id") or ""), "read-1")

    def test_pick_random_avoids_excluded_members_when_possible(self) -> None:
        from hdzz.api.webui.chashui_quote_pool import pick_readonly_quote_member

        members = [
            {"id": "read-1", "usage": "read", "status": "online", "uid": "u1", "cookie": "c1", "ver": "v1", "base_url": "https://read-1"},
            {"id": "read-2", "usage": "read", "status": "online", "uid": "u2", "cookie": "c2", "ver": "v2", "base_url": "https://read-2"},
            {"id": "read-3", "usage": "read", "status": "online", "uid": "u3", "cookie": "c3", "ver": "v3", "base_url": "https://read-3"},
        ]

        with patch("hdzz.api.webui.chashui_quote_pool.random.choice", side_effect=lambda pool: pool[0]):
            out = pick_readonly_quote_member(members, exclude_member_ids={"read-1", "read-2"})
        self.assertEqual(str((out or {}).get("id") or ""), "read-3")


if __name__ == "__main__":
    unittest.main()
