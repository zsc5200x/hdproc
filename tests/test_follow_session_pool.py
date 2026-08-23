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


class _DummySession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestFollowSessionPool(unittest.TestCase):
    def _member(self, **overrides):
        base = {
            "id": "member-1",
            "base_url": "https://example.test",
            "proxy": "socks5h://127.0.0.1:1080",
            "cookie": "k=v",
            "uid": "uid-1",
            "ver": "ver-1",
        }
        base.update(overrides)
        return base

    def test_get_or_create_reuses_same_member_session_when_fingerprint_unchanged(self) -> None:
        from hdzz.core.follower.session_pool import MemberSessionPool

        created = []

        def factory(member):
            sess = _DummySession(f"s{len(created) + 1}")
            created.append(sess)
            return sess

        pool = MemberSessionPool(session_factory=factory)
        member = self._member()

        s1 = pool.get_or_create(member)
        s2 = pool.get_or_create(member)

        self.assertIs(s1, s2)
        self.assertEqual(len(created), 1)

    def test_get_or_create_rebuilds_when_fingerprint_changes(self) -> None:
        from hdzz.core.follower.session_pool import MemberSessionPool

        created = []

        def factory(member):
            sess = _DummySession(f"s{len(created) + 1}")
            created.append(sess)
            return sess

        pool = MemberSessionPool(session_factory=factory)
        member = self._member()

        s1 = pool.get_or_create(member)
        s2 = pool.get_or_create(self._member(cookie="k=v2"))

        self.assertIsNot(s1, s2)
        self.assertEqual(len(created), 2)
        self.assertTrue(s1.closed)

    def test_invalidate_closes_and_removes_session(self) -> None:
        from hdzz.core.follower.session_pool import MemberSessionPool

        created = []

        def factory(member):
            sess = _DummySession(f"s{len(created) + 1}")
            created.append(sess)
            return sess

        pool = MemberSessionPool(session_factory=factory)
        member = self._member()
        s1 = pool.get_or_create(member)

        removed = pool.invalidate("member-1", reason="1X014")

        self.assertTrue(removed)
        self.assertTrue(s1.closed)
        self.assertEqual(pool.size(), 0)

    def test_prune_idle_closes_unused_sessions(self) -> None:
        from hdzz.core.follower.session_pool import MemberSessionPool

        created = []

        def factory(member):
            sess = _DummySession(f"s{len(created) + 1}")
            created.append(sess)
            return sess

        pool = MemberSessionPool(session_factory=factory, now_func=lambda: 100.0)
        pool.get_or_create(self._member(id="member-1"))
        pool.get_or_create(self._member(id="member-2"))

        pool._entries["member-1"].last_used_at = 10.0
        pool._entries["member-2"].last_used_at = 95.0

        removed = pool.prune_idle(max_idle_s=20.0)

        self.assertEqual(removed, 1)
        self.assertEqual(pool.size(), 1)
        self.assertTrue(created[0].closed)
        self.assertFalse(created[1].closed)


if __name__ == "__main__":
    unittest.main()
