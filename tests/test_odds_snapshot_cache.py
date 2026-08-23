import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
APP = ROOT / "app"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class OddsSnapshotCacheTest(unittest.TestCase):
    def test_member_odds_change_does_not_change_public_structure_revision(self) -> None:
        from hdzz.core.follower.odds_snapshot import store_snapshot

        def snapshot(odd):
            return {"simple": {"overUnder": {"lines": [{
                "gid": "line-25", "rawLine": "2.5",
                "under": {"params": {
                    "gid": "line-25", "wtype": "ROU", "rtype": "ROUH",
                    "chose_team": "H", "con": "-3", "ratio": "50", "ioratio": odd,
                }},
            }]}}}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            first = store_snapshot(path, snapshot("0.77"), gid="match-1", mode="live", now=100.0)
            second = store_snapshot(path, snapshot("0.73"), gid="match-1", mode="live", now=101.0)

        self.assertEqual(first["revision"], 1)
        self.assertEqual(second["revision"], 1)

    def test_store_increments_revision_only_when_market_or_score_changes(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            first = store_snapshot(
                path,
                {"meta": {"score": "0:0"}, "simple": {"overUnder": {"lines": [{"rawLine": "2.5", "over": {"odd": "0.90"}}]}}},
                gid="g1",
                mode="live",
                now=time.time(),
            )
            same = store_snapshot(
                path,
                {"meta": {"score": "0:0"}, "simple": {"overUnder": {"lines": [{"rawLine": "2.5", "over": {"odd": "0.90"}}]}}},
                gid="g1",
                mode="live",
                now=101.0,
            )
            changed = store_snapshot(
                path,
                {"meta": {"score": "1:0"}, "simple": {"overUnder": {"lines": [{"rawLine": "2.5", "over": {"odd": "0.86"}}]}}},
                gid="g1",
                mode="live",
                now=102.0,
            )

            self.assertEqual(first["revision"], 1)
            self.assertEqual(same["revision"], 1)
            self.assertEqual(changed["revision"], 2)
            hit = load_snapshot(path, gid="g1", mode="live", now=102.2, max_age_s=2.5)
            self.assertIsNotNone(hit)
            self.assertFalse(hit["stale"])
            self.assertEqual(hit["revision"], 2)
            self.assertNotIn("odd", hit["simple"]["overUnder"]["lines"][0]["over"])
            self.assertNotIn("ioratio", str(hit.get("market_rows") or []))

    def test_expired_snapshot_is_rejected_unless_short_stale_window_allows_it(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(path, {"simple": {}}, gid="g2", mode="live", now=100.0)

            self.assertIsNone(load_snapshot(path, gid="g2", mode="live", now=104.0, max_age_s=2.5))
            stale = load_snapshot(path, gid="g2", mode="live", now=104.0, max_age_s=2.5, allow_stale_s=5.0)
            self.assertIsNotNone(stale)
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["age_ms"], 4000)

    def test_mode_and_gid_are_isolated(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(path, {"simple": {"value": "live"}}, gid="g3", mode="live", now=100.0)
            store_snapshot(path, {"simple": {"value": "today"}}, gid="g3", mode="today", now=100.0)

            self.assertEqual(load_snapshot(path, gid="g3", mode="live", now=100.1, max_age_s=2.0)["simple"]["value"], "live")
            self.assertEqual(load_snapshot(path, gid="g3", mode="today", now=100.1, max_age_s=2.0)["simple"]["value"], "today")
            self.assertIsNone(load_snapshot(path, gid="missing", mode="live", now=100.1, max_age_s=2.0))

    def test_load_or_refresh_reuses_fresh_snapshot_without_calling_provider(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_or_refresh_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(path, {"simple": {"value": "fresh"}}, gid="g4", mode="live", now=100.0)
            calls = []

            result = load_or_refresh_snapshot(
                path,
                gid="g4",
                mode="live",
                fetcher=lambda: calls.append(True) or {"simple": {"value": "network"}},
                now=101.0,
                max_age_s=2.5,
            )

            self.assertEqual(calls, [])
            self.assertTrue(result["cached"])
            self.assertEqual(result["simple"]["value"], "fresh")

    def test_load_or_refresh_returns_stale_for_display_when_provider_fails(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_or_refresh_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(path, {"simple": {"value": "last-good"}}, gid="g5", mode="live", now=100.0)

            def fail():
                raise RuntimeError("provider_down")

            result = load_or_refresh_snapshot(
                path,
                gid="g5",
                mode="live",
                fetcher=fail,
                now=104.0,
                max_age_s=2.5,
                allow_stale_s=10.0,
            )

            self.assertTrue(result["cached"])
            self.assertTrue(result["stale"])
            self.assertEqual(result["status"], "stale")
            self.assertIn("provider_down", result["warning"])

    def test_snapshot_params_are_readable_by_gid_alias(self) -> None:
        from hdzz.core.follower.odds_snapshot import load_snapshot, store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(
                path,
                {
                    "meta": {"masterGid": "master-1", "requestedGid": "extra-1"},
                    "ftGids": [{"gid": "master-1", "master": "Y"}, {"gid": "extra-1", "master": "N"}],
                    "simple": {"overUnder": {"lines": []}},
                },
                gid="extra-1",
                mode="live",
                now=100.0,
            )

            hit = load_snapshot(path, gid="master-1", mode="live", now=100.1, max_age_s=2.0)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["master_gid"], "master-1")

    def test_snapshot_service_builds_latest_total_submit_template(self) -> None:
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.member_ioratio import (
            MemberIoratioService,
            store_member_snapshot_ioratios,
        )
        from hdzz.core.follower.snapshot_direct import (
            MarketIntent,
            MarketSnapshotService,
            SNAPSHOT_VALID,
            SnapshotValidator,
            build_submit_template,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            snapshot = {
                    "simple": {
                        "overUnder": {
                            "lines": [
                                {
                                    "rawLine": "2/2.5",
                                    "over": {
                                        "params": {
                                            "gid": "g6",
                                            "wtype": "AROU",
                                            "rtype": "AROUC",
                                            "chose_team": "C",
                                            "con": "2",
                                            "ratio": "-50",
                                            "ioratio": "0.85",
                                        }
                                    },
                                    "under": {"params": {"ioratio": "1.02"}},
                                }
                            ]
                        }
                    }
                }
            stored = store_snapshot(
                data_dir / "odds_snapshot_cache.json",
                snapshot,
                gid="g6",
                mode="live",
                now=time.time(),
            )
            store_member_snapshot_ioratios(
                data_dir / "member_ioratio_cache.json",
                member={"id": "member-6", "label": "member-6"},
                snapshot=snapshot,
                mode="live",
                requested_gid="g6",
                public_snapshot_revision=stored["revision"],
            )
            intent = MarketIntent(
                order_id="o6", target="t", gid="g6", gtype="FT", is_live=True,
                period="FULL_TIME", market_type="TOTAL", side="OVER",
                target_line=2.25, source_odds="0.60",
            )
            lookup = MarketSnapshotService(data_dir / "odds_snapshot_cache.json").lookup(
                intent, max_age_ms=2500
            )
            self.assertEqual(lookup.status, SNAPSHOT_VALID)
            validated = SnapshotValidator().validate(intent, lookup.snapshot, max_age_ms=2500)
            self.assertEqual(validated.status, SNAPSHOT_VALID)
            quote = MemberIoratioService(data_dir / "member_ioratio_cache.json").lookup(
                "member-6", validated.snapshot, max_age_ms=2500
            ).quote
            template = build_submit_template(validated.snapshot, quote, execution_member="member-6")
            self.assertEqual(template.wtype, "AROU")
            self.assertEqual(template.rtype, "AROUC")
            self.assertEqual(template.ioratio, "0.85")
            self.assertEqual(template.line, 2.25)

    def test_snapshot_service_builds_latest_handicap_submit_template(self) -> None:
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.member_ioratio import (
            MemberIoratioService,
            store_member_snapshot_ioratios,
        )
        from hdzz.core.follower.snapshot_direct import (
            MarketIntent,
            MarketSnapshotService,
            SNAPSHOT_VALID,
            SnapshotValidator,
            build_submit_template,
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            snapshot = {
                    "simple": {
                        "handicap": {
                            "lines": [
                                {
                                    "home": {
                                        "params": {
                                            "gid": "g7",
                                            "wtype": "ARE",
                                            "rtype": "AREH",
                                            "chose_team": "H",
                                            "con": "1",
                                            "ratio": "100",
                                            "ioratio": "0.91",
                                        }
                                    },
                                    "away": {"params": {"ioratio": "1.01"}},
                                }
                            ]
                        }
                    }
                }
            stored = store_snapshot(
                data_dir / "odds_snapshot_cache.json",
                snapshot,
                gid="g7",
                mode="live",
                now=time.time(),
            )
            store_member_snapshot_ioratios(
                data_dir / "member_ioratio_cache.json",
                member={"id": "member-7", "label": "member-7"},
                snapshot=snapshot,
                mode="live",
                requested_gid="g7",
                public_snapshot_revision=stored["revision"],
            )
            intent = MarketIntent(
                order_id="o7", target="t", gid="g7", gtype="FT", is_live=True,
                period="FULL_TIME", market_type="HANDICAP", side="HOME",
                target_line=-0.5, source_odds="0.60",
            )
            lookup = MarketSnapshotService(data_dir / "odds_snapshot_cache.json").lookup(
                intent, max_age_ms=2500
            )
            self.assertEqual(lookup.status, SNAPSHOT_VALID)
            validated = SnapshotValidator().validate(intent, lookup.snapshot, max_age_ms=2500)
            self.assertEqual(validated.status, SNAPSHOT_VALID)
            quote = MemberIoratioService(data_dir / "member_ioratio_cache.json").lookup(
                "member-7", validated.snapshot, max_age_ms=2500
            ).quote
            template = build_submit_template(validated.snapshot, quote, execution_member="member-7")
            self.assertEqual(template.wtype, "ARE")
            self.assertEqual(template.rtype, "AREH")
            self.assertEqual(template.ioratio, "0.91")
            self.assertEqual(template.line, -0.5)


if __name__ == "__main__":
    unittest.main()
