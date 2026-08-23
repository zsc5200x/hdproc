import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class OddsSnapshotWatchTest(unittest.TestCase):
    def test_flatten_active_markets_uses_exact_market_key(self) -> None:
        from odds_snapshot_watch import flatten_active_markets

        rows = flatten_active_markets([
            {
                "gid": "match-1",
                "mode": "live",
                "requirements": [
                    {
                        "gid": "match-1",
                        "period": "FULL_TIME",
                        "market_type": "TOTAL",
                        "side": "UNDER",
                        "target_line": 3.5,
                    },
                    {
                        "gid": "match-1",
                        "period": "FULL_TIME",
                        "market_type": "TOTAL",
                        "side": "OVER",
                        "target_line": 3.5,
                    },
                ],
            }
        ])

        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["active_market_key"], rows[1]["active_market_key"])
        self.assertIn("FULL_TIME|TOTAL|UNDER|3.5", rows[0]["active_market_key"])

    def test_requested_rows_accept_corner_half_integer_display_line(self) -> None:
        import odds_snapshot_watch as worker

        rows = worker._requested_market_rows(
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
            requirements=[{
                "market_type": "TOTAL", "period": "HALF_TIME", "side": "UNDER",
                "target_line": 5.0, "wtype": "HROU", "rtype": "HROUH", "is_corner": True,
            }],
            revision=1,
            fetched_at=100.0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line"], 4.5)

    def test_requested_corner_row_binds_exact_child_gid(self) -> None:
        import odds_snapshot_watch as worker

        rows = worker._requested_market_rows(
            {
                "simple": {
                    "halfOverUnder": {
                        "lines": [
                            {
                                "gid": "standard-half",
                                "rawLine": "4.5",
                                "under": {"params": {
                                    "gid": "standard-half", "wtype": "HROU", "rtype": "HROUH",
                                    "chose_team": "H", "con": "-5", "ratio": "-100",
                                }},
                            },
                            {
                                "gid": "corner-half",
                                "rawLine": "4.5",
                                "under": {"params": {
                                    "gid": "corner-half", "wtype": "HROU", "rtype": "HROUH",
                                    "chose_team": "H", "con": "-5", "ratio": "-100",
                                }},
                            },
                        ]
                    }
                }
            },
            gid="corner-half",
            mode="live",
            requirements=[{
                "gid": "corner-half", "market_type": "TOTAL", "period": "HALF_TIME",
                "side": "UNDER", "target_line": 5.0, "wtype": "HROU",
                "rtype": "HROUH", "is_corner": True,
            }],
            revision=1,
            fetched_at=100.0,
        )

        self.assertEqual([row["gid"] for row in rows], ["corner-half"])

    def test_market_demand_keeps_fixture_locator_for_corner_parent_resolution(self) -> None:
        import odds_snapshot_watch as worker

        demand = worker.market_demand({
            "orderId": "25149439616",
            "league": "瑞典超级联赛",
            "sides": "布洛马波卡纳 v 戴戈福斯 (0 - 0)",
            "line": "小 4.5",
            "betType": "(滚球) 角球数 - 大 / 小-上半场",
            "raw": {
                "_EVENT": "8980060", "MARKET": "rb",
                "WAGERSTYPE": "(滚球) 角球数 - 大 / 小-上半场",
                "SHOWTEXT_TEAM_NUM": "[701572] vs [701571]",
            },
        })

        self.assertIsNotNone(demand)
        self.assertEqual(demand["fixture_locator"]["team_h"], "布洛马波卡纳")
        self.assertEqual(demand["fixture_locator"]["team_c"], "戴戈福斯")
        self.assertEqual(demand["fixture_locator"]["gnum_h"], "701572")

    def test_market_scheduler_detaches_expired_attempt_without_global_barrier(self) -> None:
        import odds_snapshot_watch as worker

        slow = {
            "gid": "slow",
            "mode": "live",
            "requirements": [{"gid": "slow", "period": "FULL_TIME", "market_type": "TOTAL", "side": "UNDER", "target_line": 3.5}],
            "requirement": {"gid": "slow", "period": "FULL_TIME", "market_type": "TOTAL", "side": "UNDER", "target_line": 3.5},
            "active_market_key": "live|slow|FULL_TIME|TOTAL|UNDER|3.5",
            "priority": 100,
        }
        fast = {
            "gid": "fast",
            "mode": "live",
            "requirements": [{"gid": "fast", "period": "FULL_TIME", "market_type": "TOTAL", "side": "OVER", "target_line": 2.5}],
            "requirement": {"gid": "fast", "period": "FULL_TIME", "market_type": "TOTAL", "side": "OVER", "target_line": 2.5},
            "active_market_key": "live|fast|FULL_TIME|TOTAL|OVER|2.5",
            "priority": 100,
        }

        def fake_refresh(**kwargs):
            if kwargs["gid"] == "slow":
                time.sleep(0.30)
            return {"revision": 1, "fetched_at": time.time(), "age_ms": 0, "source_member": {}}

        with tempfile.TemporaryDirectory() as tmp:
            scheduler = worker.ActiveMarketScheduler(
                data_dir=Path(tmp),
                member_id="",
                max_workers=2,
                requested_interval_ms=50,
                deadline_ms=50,
                public_timeout_s=1,
            )
            try:
                with patch.object(worker, "refresh_subscription", side_effect=fake_refresh):
                    scheduler.sync([slow, fast], now=0.0)
                    scheduler.start_due(now=0.0)
                    self.assertIsNotNone(scheduler.states["live|slow|FULL_TIME|TOTAL|UNDER|3.5"].inflight)
                    time.sleep(0.13)
                    scheduler.poll(now=0.13)
                    slow_state = scheduler.states["live|slow|FULL_TIME|TOTAL|UNDER|3.5"]
                    fast_state = scheduler.states["live|fast|FULL_TIME|TOTAL|OVER|2.5"]
                    self.assertIsNone(slow_state.inflight)
                    self.assertIsNone(fast_state.inflight)
                    self.assertEqual(slow_state.consecutive_failures, 1)
                    # The expired slow attempt does not hold the scheduler
                    # barrier; a new due attempt can be launched immediately.
                    scheduler.start_due(now=0.24)
                    self.assertIsNotNone(slow_state.inflight)
            finally:
                scheduler.close()

    def test_member_quote_events_do_not_pollute_public_refresh_metrics(self) -> None:
        from hdzz.core.follower.snapshot_metrics import summarise_snapshot_watch_events

        report = summarise_snapshot_watch_events([
            {"gid": "g1", "ok": True, "snapshot_fetched_at": 100.0, "refresh_elapsed_ms": 20},
            {"gid": "g1", "ok": True, "snapshot_fetched_at": 101.0, "refresh_elapsed_ms": 25},
            {
                "event": "member_ioratio_refresh", "gid": "g1", "member_id": "m1",
                "ok": True, "finished_at": 100.5, "elapsed_ms": 80,
            },
        ])

        self.assertEqual(report["total_refresh"], 2)
        self.assertEqual(report["per_match_update_period_ms"]["count"], 1)
        self.assertEqual(report["member_ioratio_refresh"]["total"], 1)

    def test_snapshot_publish_events_have_monotonic_sequence(self) -> None:
        import json
        from hdzz.core.follower.snapshot_metrics import record_snapshot_publish_event

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            record_snapshot_publish_event(data_dir, {"active_market_key": "live|g1", "publish_at": 1.0})
            record_snapshot_publish_event(data_dir, {"active_market_key": "live|g1", "publish_at": 2.0})
            rows = [json.loads(line) for line in (data_dir / "snapshot_publish_events.jsonl").read_text().splitlines()]
        self.assertEqual([row["event_seq"] for row in rows], [1, 2])

    def test_member_quote_scheduler_keeps_exact_rows_independent(self) -> None:
        import odds_snapshot_watch as worker
        from concurrent.futures import ThreadPoolExecutor

        row = {
            "gid": "child-1", "source_gid": "match-1", "market_type": "TOTAL",
            "period": "FULL_TIME", "side": "UNDER", "line": 2.5,
            "market_row_fingerprint": "fp-1", "wtype": "ROU", "rtype": "ROUH",
        }
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            executor = ThreadPoolExecutor(max_workers=2)
            scheduler = worker.MemberQuoteScheduler(
                executor=executor, requested_interval_ms=10, deadline_ms=100, active_ttl_s=2,
            )
            try:
                request = {
                    "request_id": "r1", "member_id": "member-a", "mode": "live",
                    "created_at": time.time(), "source_gid": "match-1", "market_row": row,
                    "priority_refresh": True,
                }
                state = scheduler.register(data_dir, request, now=0.0)
                self.assertIsNotNone(state)
                with patch.object(worker, "_priority_refresh_member_quote", return_value={
                    "ok": True, "target_line_found": True, "elapsed_ms": 1,
                    "refresh_started_at": 0.0, "refresh_finished_at": 0.001,
                    "queue_delay_ms": 0, "upstream_elapsed_ms": 1,
                }) as refresh:
                    self.assertEqual(scheduler.start_due(now=0.0), 1)
                    time.sleep(0.02)
                    scheduler.poll(now=0.02)
                    self.assertEqual(refresh.call_count, 1)
                    self.assertEqual(state.key, ("member-a", "live", "fp-1"))
                    self.assertEqual(state.failure_count, 0)
                    self.assertEqual(state.last_quote_age_ms, 0.0)
                metrics = (data_dir / "member_ioratio_watch_metrics.jsonl").read_text(encoding="utf-8")
                self.assertIn('"market_row_fingerprint":"fp-1"', metrics)
                self.assertIn('"priority_refresh":true', metrics)
            finally:
                scheduler.close()
                executor.shutdown(wait=True)

    def test_refresh_subscription_fails_over_to_second_member(self) -> None:
        import odds_snapshot_watch as worker

        members = [
            {"id": "read-1", "usage": "read", "status": "online", "base_url": "https://one", "uid": "u1", "ver": "v1", "cookie": "c1"},
            {"id": "read-2", "usage": "read", "status": "online", "base_url": "https://two", "uid": "u2", "ver": "v2", "cookie": "c2"},
        ]
        attempts = []

        def resolve(*, member, **_kwargs):
            attempts.append(member["id"])
            if member["id"] == "read-1":
                raise RuntimeError("1X014: login_failed")
            return {"gid": "g1", "lid": "l1", "ecid": "e1", "mode": "live"}

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.object(worker, "_load_quote_members", return_value=members),
                patch.object(worker.webui_server, "_make_member_session", return_value=object()),
                patch.object(worker.webui_server, "_chashui_resolve_game_by_gid", side_effect=resolve),
                patch.object(worker.webui_server, "_chashui_get_odds_snapshot_with_empty_retry", return_value={"simple": {"value": "ok"}}),
            ):
                result = worker.refresh_subscription(data_dir=data_dir, gid="g1", mode="live")

        self.assertEqual(attempts, ["read-1", "read-2"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual((result.get("source_member") or {}).get("id"), "read-2")

    def test_order_subscription_normalizes_live_and_early_modes(self) -> None:
        from odds_snapshot_watch import order_subscription

        self.assertEqual(order_subscription({"gid": "g1", "live": True}), ("g1", "live"))
        self.assertEqual(order_subscription({"gid": "g2", "market": "早盘 让球"}), ("g2", "today"))
        self.assertEqual(
            order_subscription({"betType": "(滚球) 大 / 小", "raw": {"_EVENT": "8995043", "MARKET": "rb"}}),
            ("8995043", "live"),
        )
        self.assertIsNone(order_subscription({"gid": ""}))

    def test_kitchen_refreshes_execution_member_ioratio_separately(self) -> None:
        import odds_snapshot_watch as worker
        from hdzz.core.follower.member_ioratio import MemberIoratioService
        from hdzz.core.follower.snapshot_direct import MarketIntent, MarketSnapshotService

        members = [
            {
                "id": "quote-1", "label": "quote-1", "role": "quote", "status": "online",
                "base_url": "https://quote", "uid": "uq", "ver": "vq", "cookie": "cq",
            },
            {
                "id": "member-a", "label": "member-a", "status": "online",
                "base_url": "https://member", "uid": "ua", "ver": "va", "cookie": "ca",
            },
        ]

        def snapshot_for_member(*, member, **_kwargs):
            odd = "0.99" if member["id"] == "quote-1" else "0.77"
            return {
                "meta": {"masterGid": "8993471"},
                "ftGids": [
                    {"gid": "8993471", "master": "Y"},
                    {"gid": "8993475", "master": "N"},
                ],
                "simple": {"overUnder": {"lines": [{
                    "gid": "8993475", "rawLine": "2.5",
                    "under": {"params": {
                        "gid": "8993475", "wtype": "ROU", "rtype": "ROUH",
                        "chose_team": "H", "con": "-3", "ratio": "-100", "ioratio": odd,
                    }},
                }]}},
            }

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with (
                patch.object(worker, "_load_quote_members", return_value=members),
                patch.object(worker, "_pooled_member_session", return_value=object()),
                patch.object(worker, "_resolve_game_cached", return_value={"gid": "8993471", "mode": "live"}),
                patch.object(worker.webui_server, "_chashui_get_odds_snapshot_with_empty_retry", side_effect=snapshot_for_member),
            ):
                result = worker.refresh_subscription(
                    data_dir=data_dir,
                    gid="8993471",
                    mode="live",
                    requirements=[{
                        "market_type": "TOTAL", "period": "FULL_TIME", "side": "UNDER",
                        "target_line": 2.5,
                    }],
                )
                deadline = time.time() + 2.0
                quote = None
                order = {
                    "orderId": "o1", "line": "小 2.5", "betType": "(滚球) 大 / 小",
                    "raw": {"_EVENT": "8993471", "MARKET": "rb", "ORDER_TYPE": "小"},
                }
                intent = MarketIntent.from_order(order)
                row = MarketSnapshotService(data_dir / "odds_snapshot_cache.json").lookup(
                    intent, max_age_ms=5000
                ).snapshot
                while time.time() < deadline and quote is None:
                    quote = MemberIoratioService(data_dir / "member_ioratio_cache.json").lookup(
                        "member-a", row, max_age_ms=5000
                    ).quote
                    if quote is None:
                        time.sleep(0.01)

            self.assertEqual(result["revision"], 1)
            self.assertIsNotNone(quote)
            self.assertEqual(row.gid, "8993475")
            self.assertEqual(quote.ioratio, "0.77")
            public_text = (data_dir / "odds_snapshot_cache.json").read_text(encoding="utf-8")
            self.assertNotIn('"ioratio"', public_text)

    def test_collect_subscriptions_reads_recent_orders_and_state_jobs(self) -> None:
        from odds_snapshot_watch import collect_subscriptions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orders = root / "orders"
            orders.mkdir()
            (orders / "source.jsonl").write_text(
                '{"gid":"g1","live":true}\n{"gid":"old","live":true,"bet_ts":1}\n',
                encoding="utf-8",
            )
            (root / "follow_state.json").write_text(
                '{"jobs":{"k":{"order":{"gid":"g2","live":false}}},'
                '"pending_orders":{"p":{"raw":{"_EVENT":"g3","MARKET":"rb"}}}}',
                encoding="utf-8",
            )

            rows = collect_subscriptions(
                orders_dir=orders,
                state_path=root / "follow_state.json",
                now=time.time(),
                active_seconds=60.0,
            )
            self.assertEqual({tuple(x) for x in rows}, {("g1", "live"), ("g2", "today"), ("g3", "live")})

    def test_collect_subscriptions_prefers_received_time_over_shifted_bet_time(self) -> None:
        from odds_snapshot_watch import collect_subscriptions

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orders = root / "orders"
            orders.mkdir()
            now = time.time()
            (orders / "source.jsonl").write_text(
                '{"ts":%s,"bet_ts":%s,"raw":{"_EVENT":"recent-gid","MARKET":"rb"}}\n'
                % (now - 5, now - 43205),
                encoding="utf-8",
            )

            rows = collect_subscriptions(
                orders_dir=orders,
                state_path=root / "follow_state.json",
                now=now,
                active_seconds=60.0,
            )

        self.assertEqual(rows, [("recent-gid", "live")])


if __name__ == "__main__":
    unittest.main()
