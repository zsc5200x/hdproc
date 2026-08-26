import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


class SourceOrderDirectTest(unittest.TestCase):
    def _order(self, *, line="主队 -0.5", bet_type="让球", order_type="主队", live=False):
        return {
            "orderId": "source-1",
            "target": "source-target",
            "stake": "50",
            "odds": "0.93",
            "line": line,
            "betType": bet_type,
            "raw": {
                "_EVENT": "8992927",
                "GTYPE": "FT",
                "MARKET": "rb" if live else "ft",
                "WAGERSTYPE": bet_type,
                "ORDER_TYPE": order_type,
                "TEAM_H": "主队",
                "TEAM_C": "客队",
            },
        }

    def _member(self):
        return {
            "id": "member-1",
            "label": "member-1",
            "status": "online",
            "base_url": "https://example.invalid",
            "ver": "v1",
            "uid": "uid-1",
            "cookie": "SESSION=test",
            "proxy": "",
            "maxcredit": "10000",
        }

    def test_source_handicap_line_encoding_round_trips(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent, build_source_order_submit_template

        cases = [
            ("主队 -0.5", "主队", "1", "100", "H"),
            ("主队 -0.5 / 1", "主队", "1", "50", "H"),
            ("客队 +0.5", "客队", "-1", "-100", "C"),
        ]
        for line, order_type, con, ratio, chose_team in cases:
            order = self._order(line=line, order_type=order_type)
            template = build_source_order_submit_template(
                MarketIntent.from_order(order), execution_member="member-1"
            )
            self.assertEqual((template.wtype, template.rtype), ("R", f"R{chose_team}"))
            self.assertEqual((template.con, template.ratio), (con, ratio))
            self.assertEqual(template.ioratio, "0.93")
            self.assertEqual(template.is_rb, "N")
            self.assertEqual(template.f, "1M")

    def test_source_total_direction_and_live_protocol(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent, build_source_order_submit_template

        over = self._order(
            line="大 2.5", bet_type="大 / 小", order_type="大", live=True
        )
        under = self._order(
            line="小 2.5", bet_type="大 / 小", order_type="小", live=True
        )
        over_template = build_source_order_submit_template(
            MarketIntent.from_order(over), execution_member="member-1"
        )
        under_template = build_source_order_submit_template(
            MarketIntent.from_order(under), execution_member="member-1"
        )
        self.assertEqual((over_template.wtype, over_template.rtype, over_template.chose_team), ("ROU", "ROUC", "C"))
        self.assertEqual((over_template.con, over_template.ratio), ("3", "100"))
        self.assertEqual((under_template.wtype, under_template.rtype, under_template.chose_team), ("ROU", "ROUH", "H"))
        self.assertEqual((under_template.con, under_template.ratio), ("-3", "-100"))
        self.assertEqual((over_template.is_rb, over_template.f), ("Y", "1R"))

    def test_unsupported_corner_and_moneyline_are_rejected_before_submit(self):
        from hdzz.core.follower.snapshot_direct import MarketIntent, build_source_order_submit_template

        corner = self._order(line="小 5", bet_type="(滚球) 角球数 - 大 / 小-上半场", order_type="小", live=True)
        moneyline = self._order(line="主队 独赢", bet_type="独赢", order_type="主队")
        for order in (corner, moneyline):
            with self.assertRaises(ValueError):
                build_source_order_submit_template(
                    MarketIntent.from_order(order), execution_member="member-1"
                )

    def test_engine_source_mode_skips_snapshot_and_ft_order_view(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.snapshot_direct import ExecutionResult, SUCCESS

        class FakeSession:
            def __init__(self):
                self.cookies = []
                self.closed = False

            def close(self):
                self.closed = True

        session = FakeSession()
        member = self._member()
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "arsenal.json").write_text(
                json.dumps({"members": [member]}), encoding="utf-8"
            )
            order = self._order()
            with patch.object(engine, "make_member_session", return_value=session), \
                patch.object(engine, "MarketSnapshotService") as snapshot_cls, \
                patch.object(engine, "MemberIoratioService") as quote_cls, \
                patch.object(engine, "BetContext") as context_cls, \
                patch.object(engine, "MemberExecutor") as executor_cls:
                executor_cls.return_value.submit.return_value = ExecutionResult(
                    SUCCESS, "member-1", ticket_id="ticket-1", response={"code": "560"},
                )
                result = engine.place_bet(
                    data_dir=data_dir,
                    settings={"execution_mode": "source_order_direct", "bet_context_mode": "disabled"},
                    member=member,
                    order=order,
                    stake=50,
                    timeout_s=2,
                    debug=False,
                )

        snapshot_cls.assert_not_called()
        quote_cls.assert_not_called()
        context_cls.prepare.assert_not_called()
        executor_cls.return_value.submit.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["ticket_id"], "ticket-1")
        self.assertEqual(result["execution_mode"], "source_order_direct")
        self.assertEqual(result["ioratio_source"], "source_order")
        self.assertEqual(result["submit_params"]["con"], "1")
        self.assertEqual(result["submit_params"]["ratio"], "100")
        self.assertEqual(result["submit_params"]["ioratio"], "0.93")

    def test_final_form_keeps_ft_bet_and_auto_odd(self):
        from hdzz.core.follower.snapshot_direct import MemberExecutor, SUCCESS

        forms = []
        order = self._order()
        template = __import__(
            "hdzz.core.follower.snapshot_direct",
            fromlist=["MarketIntent", "build_source_order_submit_template"],
        ).build_source_order_submit_template(
            __import__("hdzz.core.follower.snapshot_direct", fromlist=["MarketIntent"]).MarketIntent.from_order(order),
            execution_member="member-1",
        )
        result = MemberExecutor(
            session_factory=lambda **_kwargs: object(),
            post=lambda **kwargs: forms.append(dict(kwargs["form"]))
            or "<serverresponse><code>560</code><ticket_id>T-1</ticket_id></serverresponse>",
        ).submit(self._member(), template, 50)
        self.assertEqual(result.status, SUCCESS)
        self.assertEqual(forms[0]["p"], "FT_bet")
        self.assertEqual(forms[0]["autoOdd"], "Y")
        self.assertEqual(forms[0]["gid"], "8992927")
        self.assertEqual(forms[0]["con"], "1")
        self.assertEqual(forms[0]["ratio"], "100")

    def test_bet_error_000_keeps_existing_three_attempt_breaker(self):
        from hdzz.core.follower import engine
        from hdzz.core.follower.models import RetryJob
        from hdzz.core.follower.snapshot_direct import (
            ExecutionResult,
            MARKET_ERROR,
            MARKET_SUBMIT_REJECTED,
        )

        member = self._member()
        rejection = ExecutionResult(
            MARKET_ERROR,
            "member-1",
            error_code="betError000",
            error_message="betError000",
            response={"code": "555", "errormsg": "betError000"},
            request={
                "p": "FT_bet",
                "gid": "8992927",
                "gtype": "FT",
                "wtype": "R",
                "rtype": "RH",
                "chose_team": "H",
                "con": "1",
                "ratio": "100",
            },
            http_status=200,
            raw_response=(
                "<serverresponse><code>555</code>"
                "<errormsg>betError000</errormsg></serverresponse>"
            ),
            xml_code="555",
            xml_errormsg="betError000",
        )
        job = RetryJob(
            key="source-order-1",
            order_id="source-1",
            target="source-target",
            stake=50,
            order=self._order(),
            created_at=time.time(),
        )

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            (data_dir / "arsenal.json").write_text(
                json.dumps({"members": [member]}), encoding="utf-8"
            )
            with patch.object(engine, "make_member_session", return_value=object()), \
                patch.object(engine, "close_member_session"), \
                patch.object(engine, "MemberExecutor") as executor_cls:
                executor_cls.return_value.submit.return_value = rejection
                results = [
                    engine.place_bet(
                        data_dir=data_dir,
                        settings={
                            "execution_mode": "source_order_direct",
                            "bet_context_mode": "disabled",
                        },
                        member=member,
                        order=self._order(),
                        stake=50,
                        timeout_s=2,
                        debug=False,
                    )
                    for _ in range(3)
                ]

        for index, result in enumerate(results, start=1):
            stopped = engine._apply_market_submit_breaker(
                job,
                result,
                max_attempts=3,
                retry_delay_seconds=0.5,
            )
            self.assertEqual(result["execution_mode"], "source_order_direct")
            self.assertEqual(result["market_submit_reject_count"], index)
            self.assertEqual(stopped, index == 3)
        self.assertEqual(
            results[-1]["snapshot_direct_result_status"],
            MARKET_SUBMIT_REJECTED,
        )
        self.assertFalse(engine.is_retryable_follow_result(results[-1]))

    def test_unknown_result_second_phase_is_reconcile_only(self):
        from hdzz.core.follower import engine

        member = self._member()
        order = self._order()
        order.update(
            {
                "_unknown_result_submitted_at": time.time() - 1,
                "_unknown_result_started_at": time.time() - 1,
                "_unknown_result_deadline_at": time.time() + 60,
                "_unknown_result_gid": "8992927",
            }
        )
        with patch.object(engine, "MemberExecutor") as executor_cls, \
            patch.object(engine, "_get_member_session", return_value=object()), \
            patch.object(
                engine,
                "_query_member_recent_wager",
                return_value={"status": "not_found"},
            ) as reconcile:
            result = engine.place_bet(
                data_dir=Path(tempfile.gettempdir()),
                settings={
                    "execution_mode": "source_order_direct",
                    "bet_context_mode": "disabled",
                    "_reconcile_only": True,
                },
                member=member,
                order=order,
                stake=50,
                timeout_s=2,
                debug=False,
            )

        executor_cls.assert_not_called()
        reconcile.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertTrue(result["reconcile_only"])
        self.assertTrue(result["reconcile_pending"])
        self.assertEqual(result["real_submission"], "reconcile_only")
        self.assertEqual(result["execution_mode"], "source_order_direct")


if __name__ == "__main__":
    unittest.main()
