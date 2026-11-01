import unittest
from unittest.mock import patch
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestFollowEngineMemberPick(unittest.TestCase):
    def test_member_transient_fields_never_create_cooldown(self) -> None:
        from hdzz.core.follower import engine

        m = {"last_error": "", "request_cooldown_until": 120}
        self.assertFalse(engine._member_has_transient_network_issue(m, now_s=100))
        self.assertFalse(engine._member_has_transient_network_issue(m, now_s=130))

    def test_member_transient_cooldown_can_be_disabled(self) -> None:
        from hdzz.core.follower import engine

        m = {
            "last_error": "network_transient: request_failed: 502 Bad Gateway",
            "request_cooldown_until": 120,
            "request_cooldown_enabled": False,
        }
        self.assertFalse(engine._member_has_transient_network_issue(m, now_s=100))

    def test_network_route_cooldown_field_is_diagnostic_only(self) -> None:
        from hdzz.core.follower import engine

        self.assertFalse(
            engine._member_has_transient_network_issue(
                {"network_health": "cooldown", "network_cooldown_until": 130},
                now_s=100,
            )
        )
        self.assertFalse(
            engine._member_has_transient_network_issue(
                {"network_health": "cooldown", "network_cooldown_until": 130},
                now_s=131,
            )
        )

    def test_pick_member_randomly_selects_from_eligible_online_pool(self) -> None:
        from hdzz.core.follower import engine

        m1 = {
            "id": "m-1",
            "status": "online",
            "maxcredit": "1000",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test", "ver": "v", "uid": "u1", "cookie": "c=1",
        }
        m2 = {
            "id": "m-2",
            "status": "online",
            "maxcredit": "8000",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test", "ver": "v", "uid": "u2", "cookie": "c=2",
        }
        m3 = {
            "id": "m-3",
            "status": "online",
            "maxcredit": "40",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test", "ver": "v", "uid": "u3", "cookie": "c=3",
        }
        with patch.object(engine, "random", SimpleNamespace(choice=lambda seq: m2), create=True):
            got = engine.pick_random_member_for_stake([m1, m2, m3], 50.0, target="", mappings={})
        self.assertEqual(str((got or {}).get("id") or ""), "m-2")

    def test_pick_member_ignores_higher_balance_preference_when_random_choice_selects_other(self) -> None:
        from hdzz.core.follower import engine

        m1 = {
            "id": "m-low-margin",
            "status": "online",
            "maxcredit": "10000",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test", "ver": "v", "uid": "u1", "cookie": "c=1",
        }
        m2 = {
            "id": "m-high-margin",
            "status": "online",
            "maxcredit": "99999",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test", "ver": "v", "uid": "u2", "cookie": "c=2",
        }
        with patch.object(engine, "random", SimpleNamespace(choice=lambda seq: m1), create=True):
            got = engine.pick_random_member_for_stake([m1, m2], 10000.0, target="", mappings={})
        self.assertEqual(str((got or {}).get("id") or ""), "m-low-margin")

    def test_pick_member_never_falls_back_to_offline_login_seed(self) -> None:
        from hdzz.core.follower import engine

        offline_ready = {
            "id": "m-offline",
            "status": "offline",
            "maxcredit": "5000",
            "last_error": "",
            "request_cooldown_until": 0,
            "base_url": "https://example.test",
            "username": "u1",
            "password": "p1",
        }
        with patch.object(engine, "random", SimpleNamespace(choice=lambda seq: offline_ready), create=True):
            got = engine.pick_random_member_for_stake([offline_ready], 2000.0, target="", mappings={})
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()
