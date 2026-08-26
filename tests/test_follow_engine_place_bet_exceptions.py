import json
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


class TestSnapshotRevisionRetry(unittest.TestCase):
    def _order(self):
        return {
            "orderId": "snapshot-retry-1",
            "target": "source-a",
            "stake": "100",
            "odds": "1.09",
            "betType": "足球 (滚球) 大 / 小",
            "line": "大 2 / 2.5",
            "raw": {
                "_EVENT": "snapshot-master-1", "GTYPE": "FT", "MARKET": "rb",
                "WAGERSTYPE": "足球 (滚球) 大 / 小", "ORDER_TYPE": "大",
            },
        }

    def _member(self, member_id):
        return {
            "id": member_id, "label": member_id, "status": "online",
            "base_url": "https://example.invalid", "ver": "v", "uid": member_id,
            "cookie": "SESSION=test", "proxy": "", "maxcredit": "10000",
        }

    def _snapshot(self, odd):
        return {
            "meta": {"masterGid": "snapshot-master-1"},
            "ftGids": [
                {"gid": "snapshot-master-1", "master": "Y"},
                {"gid": "snapshot-extra-1", "master": "N"},
            ],
            "simple": {"overUnder": {"lines": [{
                "rawLine": "2/2.5",
                "over": {"params": {
                    "gid": "snapshot-extra-1", "wtype": "AROU", "rtype": "AROUC",
                    "chose_team": "C", "con": "2", "ratio": "-50", "ioratio": odd,
                }},
            }]}},
        }

    def test_member_error_retry_uses_new_snapshot_revision_for_next_member(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.snapshot_direct import ExecutionResult, MEMBER_ERROR, SUCCESS

        seen = []

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                seen.append((member["id"], template.snapshot_revision, template.ioratio))
                if member["id"] == "member-a":
                    return ExecutionResult(MEMBER_ERROR, member["id"], error_code="doubleLogin")
                return ExecutionResult(SUCCESS, member["id"], ticket_id="RETRY-OK")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "arsenal.json").write_text(
                json.dumps({"members": [self._member("member-a"), self._member("member-b")]}),
                encoding="utf-8",
            )
            cache = data_dir / "odds_snapshot_cache.json"
            first_snapshot = self._snapshot("0.85")
            first = store_snapshot(cache, first_snapshot, gid="snapshot-master-1", mode="live")
            store_member_snapshot_ioratios(
                data_dir / "member_ioratio_cache.json",
                member={"id": "member-a", "label": "member-a"},
                snapshot=first_snapshot,
                mode="live",
                requested_gid="snapshot-master-1",
                public_snapshot_revision=first["revision"],
            )
            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()), patch.object(
                engine, "_get_member_session", return_value=object()
            ), patch.object(engine, "_invalidate_member_session"), patch.object(
                engine, "member_warmup_menu"
            ):
                result_a = engine.place_bet(
                    data_dir=data_dir, settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-a"), order=self._order(), stake=100,
                    timeout_s=2, debug=False,
                )
                time.sleep(0.002)
                second_snapshot = self._snapshot("0.90")
                second = store_snapshot(cache, second_snapshot, gid="snapshot-master-1", mode="live")
                store_member_snapshot_ioratios(
                    data_dir / "member_ioratio_cache.json",
                    member={"id": "member-b", "label": "member-b"},
                    snapshot=second_snapshot,
                    mode="live",
                    requested_gid="snapshot-master-1",
                    public_snapshot_revision=second["revision"],
                )
                result_b = engine.place_bet(
                    data_dir=data_dir, settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-b"), order=self._order(), stake=100,
                    timeout_s=2, debug=False,
                )

        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 1)
        self.assertEqual(result_a["result_status"], MEMBER_ERROR)
        self.assertTrue(result_b["ok"])
        self.assertEqual(seen, [("member-a", 1, "0.85"), ("member-b", 1, "0.90")])


if __name__ == "__main__":
    unittest.main()
