import inspect
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class SnapshotDirectTest(unittest.TestCase):
    def _order(self, *, line: str = "主队 +0 / 0.5"):
        return {
            "orderId": "order-1",
            "target": "target-1",
            "stake": "100",
            "odds": "0.91",
            "line": line,
            "betType": "(滚球) 让球",
            "raw": {
                "_EVENT": "gid-1",
                "GTYPE": "FT",
                "MARKET": "rb",
                "WAGERSTYPE": "(滚球) 让球",
                "ORDER_TYPE": "主队",
                "TEAM_H": "主队",
                "TEAM_C": "客队",
            },
        }

    def _member(self, member_id: str = "member-1"):
        return {
            "id": member_id,
            "label": member_id,
            "status": "online",
            "base_url": "https://example.invalid",
            "ver": "v1",
            "uid": f"uid-{member_id}",
            "cookie": "SESSION=test",
            "proxy": "",
            "maxcredit": "10000",
        }

    def _write_arsenal(self, data_dir: Path, member_ids=("member-1",)):
        (data_dir / "arsenal.json").write_text(
            json.dumps({"members": [self._member(member_id) for member_id in member_ids]}),
            encoding="utf-8",
        )

    def _total_order(self):
        return {
            "orderId": "total-row-1",
            "target": "target-1",
            "stake": "100",
            "odds": "0.85",
            "line": "小 2.5",
            "betType": "足球 (滚球) 大 / 小",
            "raw": {
                "_EVENT": "8993471",
                "GTYPE": "FT",
                "MARKET": "rb",
                "WAGERSTYPE": "足球 (滚球) 大 / 小",
                "ORDER_TYPE": "小",
            },
        }

    def _corner_half_total_order(self, *, line: str = "小 5"):
        return {
            "orderId": "corner-half-1",
            "target": "target-1",
            "stake": "100",
            "odds": "0.93",
            "line": line,
            "betType": "(滚球) 角球数 - 大 / 小-上半场",
            "raw": {
                "_EVENT": "8996432",
                "GTYPE": "FT",
                "MARKET": "rb",
                "WAGERSTYPE": "(滚球) 角球数 - 大 / 小-上半场",
                "ORDER_TYPE": "小",
            },
        }

    def _total_snapshot(self, *, odd_20="0.90", odd_25="0.77"):
        return {
            "meta": {"masterGid": "8993471", "requestedGid": "8993471"},
            "ftGids": [
                {"gid": "8993471", "master": "Y"},
                {"gid": "8993473", "master": "N"},
                {"gid": "8993475", "master": "N"},
            ],
            "simple": {
                "overUnder": {
                    "lines": [
                        {
                            "gid": "8993471",
                            "rawLine": "2/2.5",
                            "under": {"params": {
                                "gid": "8993471", "wtype": "ROU", "rtype": "ROUH",
                                "chose_team": "H", "con": "-2", "ratio": "-50",
                                "ioratio": "1.02",
                            }},
                        },
                        {
                            "gid": "8993473",
                            "rawLine": "2",
                            "under": {"params": {
                                "gid": "8993473", "wtype": "ROU", "rtype": "ROUH",
                                "chose_team": "H", "con": "-2", "ratio": "0",
                                "ioratio": odd_20,
                            }},
                        },
                        {
                            "gid": "8993475",
                            "rawLine": "2.5",
                            "under": {"params": {
                                "gid": "8993475", "wtype": "ROU", "rtype": "ROUH",
                                "chose_team": "H", "con": "-3", "ratio": "-100",
                                "ioratio": odd_25,
                            }},
                        },
                    ]
                }
            },
        }

    def _store_handicap(
        self,
        path: Path,
        *,
        fetched_at=None,
        line="0/0.5",
        odd="0.85",
        member_ids=("member-1",),
    ):
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios

        snapshot = {
                "simple": {
                    "handicap": {
                        "lines": [{
                            "gid": "gid-1",
                            "rawLine": line,
                            "wtype": "RE",
                            "home": {"params": {
                                "gid": "gid-1", "wtype": "RE", "rtype": "REH",
                                "chose_team": "H", "con": "0", "ratio": "50", "ioratio": odd,
                            }},
                            "away": {"params": {
                                "gid": "gid-1", "wtype": "RE", "rtype": "REC",
                                "chose_team": "C", "con": "0", "ratio": "-50", "ioratio": "0.75",
                            }},
                        }]
                    }
                }
            }
        fetched = time.time() if fetched_at is None else fetched_at
        stored = store_snapshot(
            path,
            snapshot,
            gid="gid-1",
            mode="live",
            now=fetched,
        )
        for member_id in member_ids:
            store_member_snapshot_ioratios(
                path.parent / "member_ioratio_cache.json",
                member={"id": member_id, "label": member_id},
                snapshot=snapshot,
                mode="live",
                requested_gid="gid-1",
                public_snapshot_revision=stored["revision"],
                fetched_at=fetched,
            )
        self._write_arsenal(path.parent, member_ids)
        return stored

    def test_market_intent_contains_demand_not_submit_odds(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent, intent_market_fields

        intent = MarketIntent.from_order(self._order(), split_amount=50)
        fields = intent_market_fields(intent)
        self.assertEqual(intent.market_type, "HANDICAP")
        self.assertEqual(intent.side, "HOME")
        self.assertEqual(intent.target_line, 0.25)
        self.assertEqual(fields["wtype"], "RE")
        self.assertEqual(fields["rtype"], "REH")
        self.assertNotIn("ioratio", intent.to_dict())
        self.assertFalse(hasattr(intent, "uid"))

    def test_team_digits_do_not_become_handicap_line(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent

        order = self._order(line="波特兰木材2队 0")
        order["sides"] = "文图拉 v 波特兰木材2队 (2 - 2)"
        order["raw"].update({
            "TEAM_H": "文图拉",
            "TEAM_C": "波特兰木材2队",
            "ORDER_TYPE": "波特兰木材2队",
        })

        intent = MarketIntent.from_order(order)

        self.assertEqual(intent.side, "AWAY")
        self.assertEqual(intent.target_line, 0.0)

    def test_trailing_line_parser_ignores_score_and_odds_numbers(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent

        order = self._order(line="波特兰木材2队 0 @0.94")
        order["sides"] = "文图拉 v 波特兰木材2队 (2 - 2)"
        order["raw"].update({
            "TEAM_H": "文图拉",
            "TEAM_C": "波特兰木材2队",
            "ORDER_TYPE": "波特兰木材2队",
        })

        intent = MarketIntent.from_order(order)

        self.assertEqual(intent.target_line, 0.0)

    def test_corner_half_integer_display_matches_crown_half_line(self):
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.snapshot_direct import (
            MarketIntent,
            MarketSnapshotService,
            SNAPSHOT_VALID,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(
                path,
                {
                    "simple": {
                        "halfOverUnder": {
                            "lines": [{
                                "gid": "8996432",
                                "rawLine": "4.5",
                                "under": {"params": {
                                    "gid": "8996432", "wtype": "HROU", "rtype": "HROUH",
                                    "chose_team": "H", "con": "-5", "ratio": "-100",
                                }},
                            }]
                        }
                    }
                },
                gid="8996432",
                mode="live",
                now=100.0,
            )
            intent = MarketIntent.from_order(self._corner_half_total_order())
            self.assertTrue(intent.is_corner)
            self.assertEqual(intent.period, "HALF_TIME")
            self.assertEqual(intent.target_line, 5.0)
            result = MarketSnapshotService(path).lookup(intent, max_age_ms=5000, now=100.0)
            self.assertEqual(result.status, SNAPSHOT_VALID)
            self.assertEqual(result.snapshot.line, 4.5)

    def test_corner_half_line_alias_does_not_apply_to_regular_football(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent, line_matches

        corner = MarketIntent.from_order(self._corner_half_total_order())
        regular = MarketIntent.from_order({
            **self._total_order(),
            "line": "小 5",
            "betType": "足球 (滚球) 大 / 小-上半场",
            "raw": {
                **self._total_order()["raw"],
                "WAGERSTYPE": "足球 (滚球) 大 / 小-上半场",
            },
        })
        self.assertTrue(line_matches(5.0, 4.5, market_type=corner.market_type, period=corner.period, is_corner=corner.is_corner))
        self.assertFalse(line_matches(5.0, 4.5, market_type=regular.market_type, period=regular.period, is_corner=regular.is_corner))
        self.assertFalse(line_matches(0.5, 0.0, market_type="TOTAL", period="HALF_TIME", is_corner=True))
        self.assertFalse(line_matches(10.0, 9.5, market_type="TOTAL", period="FULL_TIME", is_corner=True))

    def test_corner_lookup_does_not_select_same_line_from_standard_board(self):
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.snapshot_direct import MarketIntent, MarketSnapshotService, SNAPSHOT_VALID

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            store_snapshot(
                path,
                {
                    "simple": {"halfOverUnder": {"lines": [
                        {
                            "gid": "standard-half", "rawLine": "4.5",
                            "under": {"params": {"gid": "standard-half", "wtype": "HROU", "rtype": "HROUH", "chose_team": "H", "con": "-5", "ratio": "-100"}},
                        },
                        {
                            "gid": "corner-half", "rawLine": "4.5",
                            "under": {"params": {"gid": "corner-half", "wtype": "HROU", "rtype": "HROUH", "chose_team": "H", "con": "-5", "ratio": "-100"}},
                        },
                    ]}},
                },
                gid="corner-half",
                mode="live",
                now=100.0,
            )
            order = self._corner_half_total_order()
            order["raw"]["_EVENT"] = "corner-half"
            result = MarketSnapshotService(path).lookup(
                MarketIntent.from_order(order), max_age_ms=5000, now=100.0,
            )

        self.assertEqual(result.status, SNAPSHOT_VALID)
        self.assertEqual(result.snapshot.gid, "corner-half")

    def test_snapshot_builds_immutable_submit_template(self):
        from hdzz.core.follower.snapshot_direct import (
            MarketIntent,
            MarketSnapshotService,
            SNAPSHOT_VALID,
            SnapshotValidator,
            build_submit_template,
        )
        from hdzz.core.follower.member_ioratio import MemberIoratioService

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            self._store_handicap(path)
            intent = MarketIntent.from_order(self._order())
            lookup = MarketSnapshotService(path).lookup(intent, max_age_ms=5000)
            self.assertEqual(lookup.status, SNAPSHOT_VALID)
            validated = SnapshotValidator().validate(intent, lookup.snapshot, max_age_ms=5000)
            quote = MemberIoratioService(path.parent / "member_ioratio_cache.json").lookup(
                "member-1", validated.snapshot, max_age_ms=5000
            ).quote
            template = build_submit_template(
                validated.snapshot,
                quote,
                execution_member="member-1",
            )
            self.assertEqual(template.rtype, "REH")
            self.assertEqual(template.line, 0.25)
            self.assertTrue(template.snapshot_fingerprint)
            with self.assertRaises(Exception):
                template.ioratio = "0.10"

    def test_member_quote_allows_public_revision_to_advance_for_same_market_row(self):
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.member_ioratio import MemberIoratioService, store_member_snapshot_ioratios
        from hdzz.core.follower.snapshot_direct import MarketIntent, MarketSnapshotService, SnapshotValidator, SNAPSHOT_VALID, build_submit_template

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            public_path = data_dir / "odds_snapshot_cache.json"
            quote_path = data_dir / "member_ioratio_cache.json"
            snapshot = self._total_snapshot(odd_25="0.77")
            first = store_snapshot(public_path, snapshot, gid="8993471", mode="live", now=100.0)
            store_member_snapshot_ioratios(
                quote_path,
                member={"id": "member-a", "label": "member-a"},
                snapshot=snapshot,
                mode="live",
                requested_gid="8993471",
                public_snapshot_revision=first["revision"],
                fetched_at=100.0,
            )
            changed_board = dict(snapshot)
            changed_board["meta"] = {"score": "1:0"}
            second = store_snapshot(public_path, changed_board, gid="8993471", mode="live", now=101.0)
            self.assertEqual(second["revision"], 2)
            intent = MarketIntent.from_order(self._total_order())
            lookup = MarketSnapshotService(public_path).lookup(intent, max_age_ms=5000, now=101.0)
            self.assertEqual(lookup.status, SNAPSHOT_VALID)
            validated = SnapshotValidator().validate(intent, lookup.snapshot, max_age_ms=5000, now=101.0)
            quote = MemberIoratioService(quote_path, clock=lambda: 101.0).lookup(
                "member-a", validated.snapshot, max_age_ms=5000
            )
            self.assertEqual(quote.status, "VALID")
            template = build_submit_template(validated.snapshot, quote.quote, execution_member="member-a")
            self.assertEqual(template.ioratio, "0.77")
            self.assertEqual(template.snapshot_revision, 2)
            self.assertEqual(template.market_row_fingerprint, template.snapshot_fingerprint)

    def test_snapshot_current_read_can_skip_age_only_rejection(self):
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.snapshot_direct import (
            MarketIntent,
            MarketSnapshotService,
            SNAPSHOT_STALE,
            SNAPSHOT_VALID,
            SnapshotValidator,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            snapshot = {
                "simple": {
                    "handicap": {
                        "lines": [{
                            "gid": "gid-1", "rawLine": "0/0.5",
                            "home": {"params": {
                                "gid": "gid-1", "wtype": "RE", "rtype": "REH",
                                "chose_team": "H", "con": "0", "ratio": "50",
                                "ioratio": "0.85",
                            }},
                        }]
                    }
                }
            }
            store_snapshot(path, snapshot, gid="gid-1", mode="live", now=100.0)
            intent = MarketIntent.from_order(self._order())
            service = MarketSnapshotService(path, clock=lambda: 101.2)
            stale = service.lookup(intent, max_age_ms=500, now=101.2)
            self.assertEqual(stale.status, SNAPSHOT_STALE)
            current = service.lookup(intent, max_age_ms=None, now=101.2)
            self.assertEqual(current.status, SNAPSHOT_VALID)
            validated = SnapshotValidator().validate(intent, current.snapshot, max_age_ms=None, now=101.2)
            self.assertEqual(validated.status, SNAPSHOT_VALID)

    def test_snapshot_candidate_age_is_bounded_only_for_diagnostic_context(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.odds_snapshot import store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "odds_snapshot_cache.json"
            snapshot = {
                "simple": {
                    "handicap": {
                        "lines": [{
                            "gid": "gid-1", "rawLine": "0/0.5",
                            "home": {"params": {
                                "gid": "gid-1", "wtype": "RE", "rtype": "REH",
                                "chose_team": "H", "con": "0", "ratio": "50",
                                "ioratio": "0.85",
                            }},
                        }]
                    }
                }
            }
            store_snapshot(path, snapshot, gid="gid-1", mode="live", now=100.0)
            intent = engine.MarketIntent.from_order(self._order())
            service = engine.MarketSnapshotService(path, clock=lambda: 101.2)
            diagnostic, diagnostic_age = engine._lookup_snapshot_candidate(
                service, intent, settings={"snapshot_max_age_ms": 500, "bet_context_mode": "diagnostic_order_view"}, context_mode="diagnostic_order_view"
            )
            self.assertEqual(diagnostic.status, engine.SNAPSHOT_VALID)
            self.assertEqual(diagnostic_age, 15000)
            direct, direct_age = engine._lookup_snapshot_candidate(
                service, intent, settings={"snapshot_max_age_ms": 500, "bet_context_mode": "disabled"}, context_mode="disabled"
            )
            self.assertEqual(direct.status, engine.SNAPSHOT_STALE)
            self.assertEqual(direct_age, 500)

    def test_member_executor_makes_exactly_one_ft_bet_request(self):
        from hdzz.core.follower.snapshot_direct import MemberExecutor, SUCCESS, SubmitTemplate

        calls = []

        def post(**kwargs):
            calls.append(dict(kwargs["form"]))
            return "<serverresponse><code>560</code><ticket_id>T-1</ticket_id></serverresponse>"

        template = SubmitTemplate(
            gid="gid-1", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            con="-2", ratio="0", ioratio="0.83", is_rb="Y", f="1R",
            snapshot_revision=7, snapshot_fetched_at=time.time(), line=2.0,
            snapshot_fingerprint="fp-7",
        )
        result = MemberExecutor(session_factory=lambda **kwargs: object(), post=post).submit(
            self._member(), template, 50,
        )
        self.assertEqual(result.status, SUCCESS)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["p"], "FT_bet")
        self.assertEqual(calls[0]["ioratio"], "0.83")
        self.assertEqual(calls[0]["golds"], "50")
        self.assertNotIn("cookie", calls[0])
        self.assertEqual(result.request["p"], "FT_bet")
        self.assertEqual(result.request["uid"], "[REDACTED]")
        self.assertEqual(result.http_status, 200)
        self.assertIn("ticket_id", result.raw_response)
        self.assertEqual(result.xml_code, "560")
        source = inspect.getsource(MemberExecutor)
        for forbidden in ("FT_order_view", "get_game_list", "get_game_more", "warmup"):
            self.assertNotIn(forbidden, source)

    def test_member_executor_uses_fresh_session_per_attempt_and_closes_it(self):
        from hdzz.core.follower.snapshot_direct import MemberExecutor, SUCCESS, SubmitTemplate

        class FakeSession:
            def __init__(self):
                self.closed = False
                self.cookies = []

            def close(self):
                self.closed = True

        sessions = []

        def factory(**_kwargs):
            session = FakeSession()
            sessions.append(session)
            return session

        template = SubmitTemplate(
            gid="gid-1", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            con="-2", ratio="0", ioratio="0.83", is_rb="Y", f="1R",
            snapshot_revision=7, snapshot_fetched_at=time.time(), line=2.0,
            snapshot_fingerprint="fp-7",
        )
        executor = MemberExecutor(
            session_factory=factory,
            post=lambda **_kwargs: "<serverresponse><code>560</code><ticket_id>T-1</ticket_id></serverresponse>",
        )

        first = executor.submit(self._member(), template, 50)
        second = executor.submit(self._member(), template, 50)

        self.assertEqual(first.status, SUCCESS)
        self.assertEqual(second.status, SUCCESS)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(all(session.closed for session in sessions))
        self.assertEqual(first.session_meta["session_mode"], "fresh_per_attempt")
        self.assertNotEqual(first.session_meta["session_object_id"], second.session_meta["session_object_id"])
        self.assertGreaterEqual(first.ft_bet_elapsed_ms, 0)

    def test_member_executor_closes_fresh_session_on_hard_timeout(self):
        from hdzz.common.http import HardTimeout
        from hdzz.core.follower.snapshot_direct import MemberExecutor, UNKNOWN_RESULT, SubmitTemplate

        class FakeSession:
            def __init__(self):
                self.closed = False
                self.cookies = []

            def close(self):
                self.closed = True

        session = FakeSession()
        template = SubmitTemplate(
            gid="gid-1", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            con="-2", ratio="0", ioratio="0.83", is_rb="Y", f="1R",
            snapshot_revision=7, snapshot_fetched_at=time.time(), line=2.0,
            snapshot_fingerprint="fp-7",
        )
        result = MemberExecutor(
            session_factory=lambda **_kwargs: session,
            post=lambda **_kwargs: (_ for _ in ()).throw(HardTimeout("hard_timeout")),
        ).submit(self._member(), template, 50)

        self.assertEqual(result.status, UNKNOWN_RESULT)
        self.assertTrue(session.closed)
        self.assertEqual(result.session_meta["session_mode"], "fresh_per_attempt")

    def test_bet_context_strictly_matches_market_row_fields(self):
        from hdzz.core.follower.market_rows import MarketRow
        from hdzz.core.follower.snapshot_direct import BET_CONTEXT_MISMATCH, BetContext

        row = MarketRow(
            gid="gid-1", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            market_type="HANDICAP", period="FULL_TIME", side="HOME", line=0.25,
            con="0", ratio="50", revision=1, fetched_at=time.time(),
        )
        session = object()
        response = (
            "<serverresponse><code>501</code><fast_check>ROUH</fast_check>"
            "<con>0</con><ratio>50</ratio><ioratio>0.85</ioratio>"
            "<spread>0 / 0.5</spread></serverresponse>"
        )
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=response) as post:
            result = BetContext.prepare(session=session, member=self._member(), market_row=row, expected_ioratio="0.85", timeout_s=2)
        self.assertTrue(result.matches_market_row)
        self.assertEqual(result.error, "")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.response["context_observed"]["gid"], "")
        self.assertEqual(result.response["request_binding"]["gid"], "gid-1")
        post.assert_called_once()
        self.assertIs(post.call_args.kwargs["session"], session)

        changed_quote = response.replace("<ioratio>0.85</ioratio>", "<ioratio>0.90</ioratio>")
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=changed_quote):
            changed = BetContext.prepare(session=session, member=self._member(), market_row=row, expected_ioratio="0.85", timeout_s=2)
        self.assertTrue(changed.matches_market_row)
        self.assertEqual(changed.error, "")
        self.assertFalse(changed.response["cached_ioratio"] == changed.response["context_ioratio"])
        self.assertTrue(changed.response["context_ioratio_match"])

        missing = "<serverresponse><code>501</code></serverresponse>"
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=missing):
            incomplete = BetContext.prepare(session=session, member=self._member(), market_row=row, expected_ioratio="0.85", timeout_s=2)
        self.assertFalse(incomplete.matches_market_row)
        self.assertEqual(incomplete.error, BET_CONTEXT_MISMATCH)
        self.assertEqual(incomplete.response["context_observed"]["rtype"], "")

        conflicting = (
            "<serverresponse><code>501</code><gid>other-gid</gid>"
            "<fast_check>ROUH</fast_check><con>0</con><ratio>50</ratio>"
            "<ioratio>0.85</ioratio></serverresponse>"
        )
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=conflicting):
            mismatch = BetContext.prepare(session=session, member=self._member(), market_row=row, expected_ioratio="0.85", timeout_s=2)
        self.assertFalse(mismatch.matches_market_row)
        self.assertEqual(mismatch.error, BET_CONTEXT_MISMATCH)
        self.assertEqual(mismatch.response["context_optional_fields"]["gid"], "other-gid")

    def test_moneyline_context_uses_dynamic_ratio_for_submit(self):
        from hdzz.core.follower.market_rows import MarketRow
        from hdzz.core.follower.snapshot_direct import BetContext, build_submit_template

        row = MarketRow(
            gid="8994665", gtype="FT", wtype="RM", rtype="RMC", chose_team="C",
            market_type="MONEYLINE", period="FULL_TIME", side="AWAY", line=None,
            con="0", ratio="0", revision=1, fetched_at=time.time(),
        )
        response = (
            "<serverresponse><code>501</code><fast_check>RMC</fast_check>"
            "<con>0</con><ratio>1630</ratio><ioratio>1.59</ioratio></serverresponse>"
        )
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=response):
            context = BetContext.prepare(
                session=object(), member=self._member(), market_row=row, timeout_s=2,
            )
        self.assertTrue(context.matches_market_row)
        self.assertEqual(context.error, "")
        self.assertEqual(context.response["context_observed"]["ratio"], "1630")
        template = build_submit_template(
            row,
            None,
            execution_member="member-1",
            ratio_override=context.response["context_observed"]["ratio"],
            ioratio_override=context.response["context_ioratio"],
            ioratio_source="ft_order_view",
        )
        self.assertEqual(template.ratio, "1630")
        self.assertEqual(template.ioratio, "1.59")

    def test_non_moneyline_context_ratio_mismatch_remains_hard_failure(self):
        from hdzz.core.follower.market_rows import MarketRow
        from hdzz.core.follower.snapshot_direct import BET_CONTEXT_MISMATCH, BetContext

        row = MarketRow(
            gid="gid-1", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            market_type="TOTAL", period="FULL_TIME", side="UNDER", line=2.5,
            con="-2", ratio="50", revision=1, fetched_at=time.time(),
        )
        response = (
            "<serverresponse><code>501</code><fast_check>ROUH</fast_check>"
            "<con>-2</con><ratio>999</ratio><ioratio>1.02</ioratio></serverresponse>"
        )
        with patch("hdzz.core.follower.snapshot_direct.member_post_session_once", return_value=response):
            context = BetContext.prepare(session=object(), member=self._member(), market_row=row, timeout_s=2)
        self.assertFalse(context.matches_market_row)
        self.assertEqual(context.error, BET_CONTEXT_MISMATCH)

    def test_bet_context_transport_failure_is_context_retryable(self):
        import requests
        from hdzz.core.follower.market_rows import MarketRow
        from hdzz.core.follower.snapshot_direct import CONTEXT_RETRYABLE, BetContext

        row = MarketRow(
            gid="gid-transport", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            market_type="HANDICAP", period="FULL_TIME", side="HOME", line=0.25,
            con="0", ratio="50", revision=1, fetched_at=time.time(),
        )
        with patch(
            "hdzz.core.follower.snapshot_direct.member_post_session_once",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ):
            result = BetContext.prepare(
                session=object(), member=self._member(), market_row=row,
                expected_ioratio="0.85", timeout_s=2,
            )
        self.assertEqual(result.error, CONTEXT_RETRYABLE)
        self.assertFalse(result.matches_market_row)
        self.assertIsNone(result.http_status)

    def test_diagnostic_bet_context_and_ft_bet_share_one_fresh_session(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import MEMBER_IORATIO_VALID, MemberIoratio, MemberIoratioLookup
        from hdzz.core.follower.snapshot_direct import BetContextResult, ExecutionResult, SUCCESS

        submitted = []

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                submitted.append(kwargs.get("session"))
                return ExecutionResult(SUCCESS, member["id"], ticket_id="CTX-1")

        session = FakeSession()
        context_sessions = []
        stale_quote = MemberIoratio(
            member_id="member-1",
            member_label="member-1",
            gid="old-gid",
            market_type="HANDICAP",
            period="FULL_TIME",
            side="HOME",
            line=9.0,
            ioratio="1.01",
            fetched_at=1.0,
            age_ms=99999,
            public_snapshot_revision=0,
            market_row_fingerprint="old-fingerprint",
        )

        def prepare(*, session, member, market_row, expected_ioratio=None, timeout_s=0):
            context_sessions.append(session)
            return BetContextResult(
                request={"p": "FT_order_view", "gid": market_row.gid},
                response={
                    "code": "501",
                    "context_observed": {
                        "gid": market_row.gid,
                        "rtype": market_row.rtype,
                        "chose_team": market_row.chose_team,
                        "con": market_row.con,
                        "ratio": market_row.ratio,
                        "ioratio": "0.77",
                    },
                    "context_ioratio": "0.77",
                },
                raw_response="<serverresponse><code>501</code></serverresponse>",
                http_status=200,
                elapsed_ms=4,
                matches_market_row=True,
            )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "make_member_session", return_value=session) as make_session, \
                patch.object(engine, "MemberIoratioService") as quote_service_cls, \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "member_warmup_menu") as warmup, \
                patch.object(engine, "MemberExecutor", return_value=FakeExecutor()):
                quote_service_cls.return_value.lookup.return_value = MemberIoratioLookup(
                    status=MEMBER_IORATIO_VALID,
                    quote=stale_quote,
                )
                context_cls.prepare.side_effect = prepare
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "bet_context_mode": "diagnostic_order_view"},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket_id"], "CTX-1")
        self.assertEqual(make_session.call_count, 1)
        self.assertEqual(context_cls.prepare.call_count, 1)
        warmup.assert_not_called()
        self.assertEqual(context_sessions, [session])
        self.assertEqual(submitted, [session])
        self.assertTrue(session.closed)
        self.assertEqual(result["session_mode"], "fresh_per_attempt")
        self.assertEqual(result["bet_context_mode"], "diagnostic_order_view")
        self.assertEqual(result["bet_context_http_status"], 200)
        self.assertEqual(result["bet_context_elapsed_ms"], 4)
        self.assertNotIn("session_warmup", result)
        self.assertEqual(result["submit_params"]["ioratio"], "0.77")
        self.assertEqual(result["final_submit_ioratio"], "0.77")
        self.assertEqual(result["ioratio_source"], "ft_order_view")

    def test_diagnostic_context_rejects_stale_candidate_before_submit(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import MEMBER_IORATIO_VALID, MemberIoratioLookup
        from hdzz.core.follower.snapshot_direct import BetContextResult, ExecutionResult, SUCCESS

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        stale_session = FakeSession()
        fake_executor = type(
            "Exec",
            (),
            {"submit": lambda self, *a, **k: ExecutionResult(SUCCESS, "member-1", ticket_id="T-stale")},
        )()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json", fetched_at=time.time() - 1.2)
            with patch.object(engine, "make_member_session", return_value=stale_session), \
                patch.object(engine, "MemberIoratioService") as quote_service_cls, \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "MemberExecutor", return_value=fake_executor) as executor_patch:
                quote_service_cls.return_value.lookup.return_value = MemberIoratioLookup(status=MEMBER_IORATIO_VALID, quote=None)
                context_cls.prepare.return_value = BetContextResult(
                    request={"p": "FT_order_view"},
                    response={"code": "501", "context_ioratio": "0.77", "context_observed": {"rtype": "REH", "con": "0", "ratio": "50", "ioratio": "0.77"}},
                    raw_response="<serverresponse><code>501</code></serverresponse>",
                    http_status=200,
                    elapsed_ms=8,
                    matches_market_row=True,
                )
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 500, "bet_context_mode": "diagnostic_order_view", "snapshot_structure_max_age_ms": 3000},
                    member=self._member(), order=self._order(), stake=50, timeout_s=2, debug=False,
                )
        self.assertFalse(result["ok"])
        self.assertEqual(result["snapshot_direct_status"], "SNAPSHOT_STALE")
        self.assertEqual(result["real_submission"], "none")
        executor_patch.assert_not_called()

    def test_diagnostic_bet_context_mismatch_blocks_ft_bet(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import BET_CONTEXT_MISMATCH, BetContextResult

        submitted = []

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        class FakeExecutor:
            def submit(self, *args, **kwargs):
                submitted.append(True)
                raise AssertionError("BET_CONTEXT_MISMATCH 后不得调用 FT_bet")

        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "make_member_session", return_value=session), \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "MemberExecutor", return_value=FakeExecutor()):
                context_cls.prepare.return_value = BetContextResult(
                    request={"p": "FT_order_view"},
                    response={"code": "501", "context_observed": {"gid": "wrong-gid"}},
                    raw_response="<serverresponse><code>501</code><gid>wrong-gid</gid></serverresponse>",
                    http_status=200,
                    elapsed_ms=2,
                    error=BET_CONTEXT_MISMATCH,
                    matches_market_row=False,
                )
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "bet_context_mode": "diagnostic_order_view"},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["snapshot_direct_status"], BET_CONTEXT_MISMATCH)
        self.assertEqual(result["real_submission"], "none")
        self.assertEqual(submitted, [])
        self.assertTrue(session.closed)
        self.assertEqual(result["bet_context_raw_response"], "<serverresponse><code>501</code><gid>wrong-gid</gid></serverresponse>")

    def test_diagnostic_context_502_cools_member_without_ft_bet(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import CONTEXT_RETRYABLE, BetContextResult

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "make_member_session", return_value=session), \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "_mark_member_network_cooldown") as cooldown, \
                patch.object(engine, "MemberExecutor") as executor:
                context_cls.prepare.return_value = BetContextResult(
                    request={"p": "FT_order_view"},
                    response={"http_status": 502},
                    raw_response="Blocked",
                    http_status=502,
                    elapsed_ms=2,
                    error=CONTEXT_RETRYABLE,
                    matches_market_row=False,
                )
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "bet_context_mode": "diagnostic_order_view"},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )

        executor.assert_not_called()
        cooldown.assert_called_once_with(
            data_dir=data_dir,
            member_id="member-1",
            error_class="UPSTREAM_502",
        )
        self.assertEqual(result["snapshot_direct_status"], CONTEXT_RETRYABLE)
        self.assertEqual(result["real_submission"], "none")
        self.assertEqual(result["network_error_class"], "UPSTREAM_502")
        self.assertTrue(session.closed)

    def test_diagnostic_double_login_context_never_reaches_ft_bet(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import BetContextResult

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "make_member_session", return_value=session), \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "MemberExecutor") as executor:
                context_cls.prepare.return_value = BetContextResult(
                    request={"p": "FT_order_view"},
                    response={"code": "error", "msg": "doubleLogin"},
                    raw_response="<serverresponse><code>error</code><msg>doubleLogin</msg></serverresponse>",
                    http_status=200,
                    elapsed_ms=2,
                    error="double_login",
                    matches_market_row=False,
                )
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "bet_context_mode": "diagnostic_order_view"},
                    member=self._member(),
                    order=self._order(),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )

        executor.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["real_submission"], "none")
        self.assertEqual(result["snapshot_direct_status"], "BET_CONTEXT_FAILED")
        self.assertTrue(session.closed)

    def test_bet_error_preserves_raw_response_and_final_params(self):
        from hdzz.core.follower.snapshot_direct import MARKET_ERROR, MemberExecutor, SubmitTemplate

        body = (
            "<serverresponse><code>555</code><errormsg>betError000</errormsg>"
            "<msg>market rejected</msg><extra>raw-value</extra></serverresponse>"
        )
        template = SubmitTemplate(
            gid="8993475", gtype="FT", wtype="ROU", rtype="ROUH", chose_team="H",
            con="-3", ratio="-100", ioratio="0.77", is_rb="Y", f="1R",
            snapshot_revision=9, snapshot_fetched_at=time.time(), line=2.5,
        )
        result = MemberExecutor(
            session_factory=lambda **kwargs: object(),
            post=lambda **kwargs: body,
        ).submit(self._member(), template, 50)

        self.assertEqual(result.status, MARKET_ERROR)
        self.assertEqual(result.error_code, "betError000")
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.raw_response, body)
        self.assertEqual(result.xml_code, "555")
        self.assertEqual(result.xml_errormsg, "betError000")
        self.assertEqual(result.request["gid"], "8993475")
        self.assertEqual(result.request["ioratio"], "0.77")
        self.assertEqual(result.request["uid"], "[REDACTED]")

    def test_snapshot_missing_and_stale_never_reach_member_executor(self):
        from hdzz.core.follower import engine

        for stale in (False, True):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as tmp:
                data_dir = Path(tmp)
                if stale:
                    self._store_handicap(data_dir / "odds_snapshot_cache.json", fetched_at=time.time() - 2)
                with patch.object(engine, "MemberExecutor") as executor:
                    result = engine.place_bet(
                        data_dir=data_dir,
                        settings={"snapshot_max_age_ms": 500},
                        member=self._member(), order=self._order(), stake=50,
                        timeout_s=2, debug=False,
                    )
                executor.assert_not_called()
                self.assertEqual(result["execution_state"], "WAITING_MARKET")
                self.assertEqual(result["real_submission"], "none")
                self.assertEqual(
                    result["snapshot_direct_status"],
                    "SNAPSHOT_STALE" if stale else "SNAPSHOT_MISSING",
                )

    def test_target_line_missing_waits_without_submission(self):
        from hdzz.core.follower import engine

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor") as executor:
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "allow_line_change": False},
                    member=self._member(), order=self._order(line="主队 +1 / 1.5"), stake=50,
                    timeout_s=2, debug=False,
                )
            executor.assert_not_called()
            self.assertEqual(result["execution_state"], "WAITING_MARKET")
            self.assertEqual(result["snapshot_direct_status"], "TARGET_LINE_MISSING")

    def test_allow_line_change_cannot_replace_exact_market_row(self):
        from hdzz.core.follower import engine

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor") as executor:
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000, "allow_line_change": True},
                    member=self._member(),
                    order=self._order(line="主队 +1 / 1.5"),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )
            executor.assert_not_called()
            self.assertEqual(result["snapshot_direct_status"], "TARGET_LINE_MISSING")

    def test_valid_snapshot_executes_direct_and_records_template(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import ExecutionResult, SUCCESS

        submitted = []

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                submitted.append((member["id"], template, amount))
                return ExecutionResult(SUCCESS, member["id"], ticket_id="DIRECT-1")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()), patch.object(
                engine, "_get_member_session", return_value=object()
            ), patch.object(engine, "member_warmup_menu"):
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["execution_path"], "snapshot_direct")
        self.assertEqual(result["real_submission"], "snapshot_direct")
        self.assertEqual(result["ticket_id"], "DIRECT-1")
        self.assertEqual(result["submit_params"]["snapshot_revision"], 1)
        self.assertEqual(result["submit_params"]["execution_member"], "member-1")
        self.assertEqual(result["submit_params"]["member_ioratio"], "0.85")
        self.assertEqual(len(submitted), 1)

    def test_total_line_uses_its_own_child_gid_and_execution_member_quote(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios
        from hdzz.core.follower.odds_snapshot import store_snapshot
        from hdzz.core.follower.snapshot_direct import ExecutionResult, SUCCESS

        submitted = []

        class FakeExecutor:
            def submit(self, member, template, amount, **kwargs):
                submitted.append((member["id"], template.to_dict()))
                return ExecutionResult(SUCCESS, member["id"], ticket_id=f"T-{member['id']}")

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            public = self._total_snapshot(odd_25="0.99")
            stored = store_snapshot(
                data_dir / "odds_snapshot_cache.json",
                public,
                gid="8993471",
                mode="live",
            )
            for member_id, odd in (("member-a", "0.77"), ("member-b", "0.73")):
                member_snapshot = self._total_snapshot(odd_25=odd)
                store_member_snapshot_ioratios(
                    data_dir / "member_ioratio_cache.json",
                    member={"id": member_id, "label": member_id},
                    snapshot=member_snapshot,
                    mode="live",
                    requested_gid="8993471",
                    public_snapshot_revision=stored["revision"],
                )
            self._write_arsenal(data_dir, ("member-a", "member-b"))

            public_text = (data_dir / "odds_snapshot_cache.json").read_text(encoding="utf-8")
            self.assertNotIn('"ioratio"', public_text)
            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()), patch.object(
                engine, "_get_member_session", return_value=object()
            ), patch.object(engine, "member_warmup_menu"):
                result_a = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-a"),
                    order=self._total_order(),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )
                result_b = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-b"),
                    order=self._total_order(),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )

        self.assertTrue(result_a["ok"])
        self.assertTrue(result_b["ok"])
        self.assertEqual(
            [(member_id, row["market_row_gid"], row["market_row_line"], row["member_ioratio"])
             for member_id, row in submitted],
            [
                ("member-a", "8993475", 2.5, "0.77"),
                ("member-b", "8993475", 2.5, "0.73"),
            ],
        )
        self.assertEqual(submitted[0][1]["market_row_con"], "-3")
        self.assertEqual(submitted[0][1]["market_row_ratio"], "-100")
        self.assertEqual(submitted[0][1]["execution_member"], "member-a")
        self.assertEqual(submitted[0][1]["member_ioratio_source_member"], "member-a")

    def test_wrong_line_member_quote_never_reaches_ft_bet(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import MEMBER_IORATIO_MISSING, store_member_snapshot_ioratios
        from hdzz.core.follower.odds_snapshot import store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            public = self._total_snapshot()
            stored = store_snapshot(
                data_dir / "odds_snapshot_cache.json", public, gid="8993471", mode="live"
            )
            wrong_line = self._total_snapshot()
            wrong_line["simple"]["overUnder"]["lines"] = [
                wrong_line["simple"]["overUnder"]["lines"][1]
            ]
            store_member_snapshot_ioratios(
                data_dir / "member_ioratio_cache.json",
                member={"id": "member-a", "label": "member-a"},
                snapshot=wrong_line,
                mode="live",
                requested_gid="8993471",
                public_snapshot_revision=stored["revision"],
            )
            self._write_arsenal(data_dir, ("member-a",))
            with patch.object(engine, "MemberExecutor") as executor, patch.object(
                engine, "_get_member_session", return_value=object()
            ):
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-a"),
                    order=self._total_order(),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )

        executor.assert_not_called()
        self.assertEqual(result["execution_state"], "WAITING_MARKET")
        self.assertEqual(result["snapshot_direct_status"], MEMBER_IORATIO_MISSING)
        self.assertEqual(result["market_row_gid"], "8993475")

    def test_direct_path_uses_fresh_session_before_ft_bet(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.member_ioratio import store_member_snapshot_ioratios
        from hdzz.core.follower.odds_snapshot import store_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            cache = data_dir / "odds_snapshot_cache.json"
            original = self._total_snapshot(odd_25="0.77")
            stored = store_snapshot(cache, original, gid="8993471", mode="live")
            store_member_snapshot_ioratios(
                data_dir / "member_ioratio_cache.json",
                member={"id": "member-a", "label": "member-a"},
                snapshot=original,
                mode="live",
                requested_gid="8993471",
                public_snapshot_revision=stored["revision"],
            )
            self._write_arsenal(data_dir, ("member-a",))

            class FakeExecutor:
                def submit(self, member, template, amount, **_kwargs):
                    from hdzz.core.follower.snapshot_direct import ExecutionResult, SUCCESS
                    return ExecutionResult(SUCCESS, member["id"], ticket_id="DIRECT-1")

            with patch.object(engine, "MemberExecutor", return_value=FakeExecutor()), patch.object(
                engine, "_get_member_session", side_effect=AssertionError("Direct must not acquire pooled Session")
            ), patch.object(engine, "member_warmup_menu") as warmup:
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member("member-a"),
                    order=self._total_order(),
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket_id"], "DIRECT-1")
        warmup.assert_not_called()
        self.assertNotIn("session_warmup", result)
        self.assertEqual(result["session_mode"], "fresh_per_attempt")

    def test_unknown_result_reconciles_and_never_submits_twice(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import ExecutionResult, UNKNOWN_RESULT

        submit = Mock(return_value=ExecutionResult(
            UNKNOWN_RESULT, "member-1", error_code="ReadTimeout", error_message="timeout"
        ))
        fake_executor = Mock()
        fake_executor.submit = submit
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            self._store_handicap(data_dir / "odds_snapshot_cache.json")
            with patch.object(engine, "MemberExecutor", return_value=fake_executor), patch.object(
                engine, "_get_member_session", return_value=object()
            ), patch.object(
                engine, "_query_member_recent_wager", return_value={"status": "unavailable", "error": "slow"}
            ) as reconcile, patch.object(engine, "member_warmup_menu"):
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"snapshot_max_age_ms": 5000},
                    member=self._member(), order=self._order(), stake=50,
                    timeout_s=2, debug=False,
                )
        submit.assert_called_once()
        reconcile.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertEqual(result["result_status"], UNKNOWN_RESULT)
        self.assertTrue(result["reconcile_pending"])
        self.assertEqual(result["network_health"], "cooldown")
        self.assertEqual(result["network_error_class"], "READ_TIMEOUT")

    def test_reconcile_only_path_does_not_construct_member_executor(self):
        from hdzz.core.follower import engine

        order = self._order()
        order.update({
            "_unknown_result_submitted_at": time.time() - 1,
            "_unknown_result_gid": "gid-1",
            "_unknown_result_started_at": time.time() - 1,
            "_unknown_result_deadline_at": time.time() + 60,
            "_unknown_result_check_count": 0,
        })
        with patch.object(engine, "MemberExecutor") as executor, patch.object(
            engine, "_get_member_session", return_value=object()
        ), patch.object(
            engine, "_query_member_recent_wager", return_value={"status": "matched", "ticket_id": "REC-1"}
        ):
            result = engine.place_bet(
                data_dir=Path(tempfile.gettempdir()),
                settings={"_reconcile_only": True},
                member=self._member(), order=order, stake=50,
                timeout_s=2, debug=False,
            )
        executor.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reconcile_terminal_status"], "SUCCESS")
        self.assertEqual(result["real_submission"], "reconcile_only")

    def test_production_source_has_no_legacy_or_shadow_execution_function(self):
        from hdzz.core.follower import engine

        source = inspect.getsource(engine)
        self.assertNotIn("def _place_bet_legacy", source)
        self.assertNotIn("legacy_fallback", source)
        self.assertNotIn("def _attach_snapshot_shadow", source)


if __name__ == "__main__":
    unittest.main()
