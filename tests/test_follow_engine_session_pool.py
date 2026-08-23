import tempfile
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class TestSnapshotDirectSessionLifecycle(unittest.TestCase):
    def _order(self):
        return {
            "orderId": "session-pool-1", "target": "source", "stake": "50",
            "odds": "0.85", "line": "小 2", "betType": "(滚球) 大 / 小",
            "raw": {
                "_EVENT": "gid-1", "GTYPE": "FT", "MARKET": "rb",
                "WAGERSTYPE": "(滚球) 大 / 小", "ORDER_TYPE": "小",
            },
        }

    def _member(self):
        return {
            "id": "member-1", "label": "member-1", "status": "online",
            "base_url": "https://example.invalid", "ver": "v", "uid": "u",
            "cookie": "SESSION=test", "proxy": "", "maxcredit": "10000",
        }

    def _store(self, path):
        from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios
        from hdzz.core.follower.odds_snapshot import store_snapshot

        snapshot = {
            "simple": {"overUnder": {"lines": [{
                "rawLine": "2",
                "under": {"params": {
                    "gid": "gid-1", "wtype": "ROU", "rtype": "ROUH",
                    "chose_team": "H", "con": "-2", "ratio": "0", "ioratio": "0.83",
                }},
            }]}},
        }
        stored = store_snapshot(path, snapshot, gid="gid-1", mode="live", now=time.time())
        store_member_snapshot_ioratios(
            path.parent / "member_ioratio_cache.json",
            member={"id": "member-1", "label": "member-1"},
            snapshot=snapshot,
            mode="live",
            requested_gid="gid-1",
            public_snapshot_revision=stored["revision"],
        )

    def test_direct_attempt_does_not_use_session_pool_session(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import ExecutionResult, SUCCESS

        seen_kwargs = []

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                seen_kwargs.append(kwargs)
                return ExecutionResult(SUCCESS, member["id"], ticket_id="T-1")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()):
                result = engine.place_bet(
                    data_dir=data_dir, settings={"snapshot_max_age_ms": 5000},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )
        self.assertTrue(result["ok"])
        self.assertEqual(len(seen_kwargs), 1)
        self.assertNotIn("session", seen_kwargs[0])

    def test_member_error_does_not_invalidate_pool_for_direct(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.session_pool import MemberSessionPool
        from hdzz.core.follower.snapshot_direct import ExecutionResult, MEMBER_ERROR

        invalidations = []
        pool = MemberSessionPool(
            session_factory=lambda member: object(),
            invalidate_callback=lambda member_id, reason: invalidations.append((member_id, reason)),
        )

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                return ExecutionResult(MEMBER_ERROR, member["id"], error_code="doubleLogin")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()):
                result = engine.place_bet(
                    data_dir=data_dir, settings={"snapshot_max_age_ms": 5000},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False, session_pool=pool,
                )
        self.assertEqual(result["result_status"], MEMBER_ERROR)
        self.assertEqual(invalidations, [])


if __name__ == "__main__":
    unittest.main()
