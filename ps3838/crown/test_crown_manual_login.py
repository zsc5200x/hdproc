import unittest
from unittest.mock import Mock

from crown_local.backend.crown_member import (
    CrownMemberMonitor,
    MemberExpired,
    MemberSessionConflict,
)


class ExpiredSessionStore:
    def __init__(self):
        self.account = {
            "id": "crown-account",
            "kind": "crown_odds",
            "enabled": True,
            "has_session": True,
            "status": "authenticated",
            "revision": 1,
        }
        self.session = {
            "uid": "member-1",
            "saved_at": "2026-10-29T00:00:00+08:00",
            "member_ui_verified": True,
            "member_protocol": {"origin": "https://crown.example", "ver": "1", "langx": "zh"},
        }

    def accounts(self):
        return [dict(self.account)]

    def get_account(self, _account_id):
        return dict(self.account)

    def load_session(self, _account_id):
        return dict(self.session)

    def apply_session_check(self, _account_id, _session, status, message, **_kwargs):
        self.account["status"] = status
        self.account["last_error"] = message
        return True


class CrownManualLoginTests(unittest.TestCase):
    def test_expired_session_does_not_start_an_automatic_login(self):
        store = ExpiredSessionStore()
        auth = Mock()
        auth.shared_browser_configured = False

        def expired_client_factory(_account, _session):
            raise MemberExpired("expired")

        monitor = CrownMemberMonitor(
            store,
            auth,
            client_factory=expired_client_factory,
            monotonic_fn=lambda: 100,
        )

        monitor.check_once()
        monitor.check_once()

        self.assertEqual(store.account["status"], "expired")
        self.assertIn("手动重新登录", store.account["last_error"])
        auth.start.assert_not_called()
        auth.test.assert_not_called()

    def test_session_conflict_rechecks_shared_browser_without_reopening_login(self):
        store = ExpiredSessionStore()
        auth = Mock()
        auth.shared_browser_configured = True

        def conflicting_client_factory(_account, _session):
            raise MemberSessionConflict("saved session was superseded")

        monitor = CrownMemberMonitor(
            store,
            auth,
            client_factory=conflicting_client_factory,
            monotonic_fn=lambda: 100,
        )

        monitor.check_once()

        self.assertEqual(store.account["status"], "session_received")
        self.assertIn("同步最新会话", store.account["last_error"])
        self.assertEqual(monitor._next_due["crown-account"], 110)
        auth.test.assert_called_once_with("crown-account", background=True)
        auth.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
