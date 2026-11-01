import copy
import unittest
import threading
import time
from unittest.mock import patch

import app
import crown_runtime
from crown_local.backend.crown_odds import OddsUnavailable
from event_filters import is_excluded_league


class EventFilterTests(unittest.TestCase):
    def restore_crown_state(self, previous):
        with crown_runtime.lock:
            crown_runtime.state.clear()
            crown_runtime.state.update(previous)

    def test_more_details_are_batched_live_first(self):
        games = [({"GID": f"today-{index}"}, "today") for index in range(24)]
        games += [({"GID": f"live-{index}"}, "live") for index in range(4)]

        batch = crown_runtime._select_more_refresh_batch(games)

        self.assertEqual(len(batch), crown_runtime._MORE_BATCH_SIZE)
        self.assertEqual([mode for _, mode in batch[:2]], ["live"] * 2)
        self.assertTrue(all(mode == "today" for _, mode in batch[2:]))

    def test_more_details_rotate_to_unseen_fixtures_without_live_starvation(self):
        games = [({"GID": f"today-{index}"}, "today") for index in range(24)]
        games += [({"GID": f"live-{index}"}, "live") for index in range(8)]
        previous_cache = dict(crown_runtime._more_cache)
        self.addCleanup(
            lambda: (crown_runtime._more_cache.clear(), crown_runtime._more_cache.update(previous_cache))
        )
        with crown_runtime._more_cache_lock:
            crown_runtime._more_cache.clear()

        first = crown_runtime._select_more_refresh_batch(games, "CROWN_HK")
        now = time.monotonic()
        with crown_runtime._more_cache_lock:
            for game, mode in first:
                gid = game["GID"]
                crown_runtime._more_cache[(gid, mode, "CROWN_HK")] = {
                    "created_at": now,
                    "value": {"status": "loaded"},
                }

        second = crown_runtime._select_more_refresh_batch(games, "CROWN_HK")
        first_keys = {(game["GID"], mode) for game, mode in first}
        second_keys = {(game["GID"], mode) for game, mode in second}

        self.assertFalse(first_keys & second_keys)
        self.assertEqual(sum(mode == "live" for _, mode in second), 2)
        self.assertEqual(sum(mode == "today" for _, mode in second), 4)

    def test_on_demand_event_details_update_only_the_selected_fixture(self):
        previous_state = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_crown_state, previous_state)
        with crown_runtime._more_sources_lock:
            previous_sources = dict(crown_runtime._more_sources)
            previous_games = dict(crown_runtime._more_sources.get("games") or {})
        self.addCleanup(
            lambda: crown_runtime._more_sources.update({
                **previous_sources,
                "games": previous_games,
            })
        )
        with crown_runtime.lock:
            crown_runtime.state["events"] = [
                {"id": "123", "home": "主队", "away": "客队", "extra_markets": {"status": "not_requested"}},
                {"id": "456", "home": "另一队", "away": "对手", "extra_markets": {"status": "loaded"}},
            ]
        crown_runtime._register_more_sources(
            object(),
            [({"GID": "123", "ECID": "1", "LID": "2"}, "today")],
            "CROWN_HK",
        )
        loaded = {
            "status": "loaded",
            "handicap": [{"line": "0.25", "a": "0.80", "b": "1.02", "master": "N"}],
            "total": [{"line": "2.5", "a": "0.90", "b": "0.90", "master": "Y"}],
            "moneyline": [],
            "rows": [],
        }

        with patch("crown_runtime.load_config", return_value={"crown_extra_markets_enabled": True}), \
                patch("crown_runtime._load_more_markets", return_value={"123": loaded}):
            result = crown_runtime.refresh_event_more("123")

        self.assertEqual(result["status"], "loaded")
        self.assertEqual(result["counts"], {
            "handicap": 1,
            "total": 1,
            "half_handicap": 0,
            "half_total": 0,
            "moneyline": 0,
        })
        self.assertEqual(crown_runtime.state["events"][0]["extra_markets"]["handicap"][0]["line"], "0.25")
        self.assertEqual(crown_runtime.state["events"][1]["extra_markets"], {"status": "loaded"})

    def test_disabled_extra_markets_skip_on_demand_and_background_fetches(self):
        class RecordingClient:
            def __init__(self):
                self.calls = 0

            def game_more(self, game, mode):
                self.calls += 1
                return {"games": []}

        client = RecordingClient()
        config = {"crown_extra_markets_enabled": False}
        with patch("crown_runtime.load_config", return_value=config), \
                patch("crown_runtime._load_more_markets") as load_more:
            selected = crown_runtime.refresh_event_more("missing")
            scheduled = crown_runtime._start_more_markets_refresh(
                client, [({"GID": "123"}, "live")], "CROWN_HK"
            )

        self.assertEqual(selected["status"], "disabled")
        self.assertFalse(scheduled)
        load_more.assert_not_called()
        self.assertEqual(client.calls, 0)

    def test_queued_extra_market_job_rechecks_switch_before_upstream_request(self):
        class RecordingClient:
            def __init__(self):
                self.calls = 0

            def game_more(self, game, mode):
                self.calls += 1
                return {"games": []}

        client = RecordingClient()
        with patch("crown_runtime.load_config", return_value={"crown_extra_markets_enabled": False}):
            result = crown_runtime._cached_more_markets(
                client, {"GID": "123"}, "live", "CROWN_HK"
            )

        self.assertEqual(result["status"], "disabled")
        self.assertEqual(client.calls, 0)

    def test_transient_detail_failure_keeps_last_good_alternate_lines(self):
        previous_cache = dict(crown_runtime._more_cache)
        self.addCleanup(
            lambda: (crown_runtime._more_cache.clear(), crown_runtime._more_cache.update(previous_cache))
        )
        loaded = {"status": "loaded", "handicap": [{"line": "0.5"}], "total": [], "moneyline": [], "rows": []}
        with crown_runtime._more_cache_lock:
            crown_runtime._more_cache.clear()
            crown_runtime._more_cache[("123", "today", "CROWN_HK")] = {
                "created_at": time.monotonic() - 999,
                "value": loaded,
            }

        class FailingClient:
            def game_more(self, game, mode):
                raise OddsUnavailable("upstream_timeout")

        with patch("crown_runtime.load_config", return_value={"crown_extra_markets_enabled": True}):
            value = crown_runtime._cached_more_markets(
                FailingClient(), {"GID": "123"}, "today", "CROWN_HK"
            )
        self.assertEqual(value, loaded)

    def test_odds_failure_keeps_previous_events_and_delay_status(self):
        class FakeStore:
            def accounts(self):
                return [{"id": "test", "kind": "crown_odds", "enabled": True, "has_session": True}]

            def load_session(self, account_id):
                return {}

        class FailingClient:
            odds_format = "HK"
            raw_odds_format = "CROWN_HK"

            def __init__(self, account, session):
                pass

            def list_games(self, mode):
                raise OddsUnavailable("upstream_timeout")

        previous = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_crown_state, previous)
        with crown_runtime.lock:
            crown_runtime.state.update({
                "events": [{"id": "previous-event"}],
                "leagues": ["测试联赛"],
                "updated_at": "2026-10-28T18:00:00+08:00",
                "status": "已连接",
                "error": None,
            })

        with patch("crown_runtime.initialize"), \
                patch("crown_runtime.store", FakeStore()), \
                patch("crown_runtime.CrownOddsClient", FailingClient):
            result = crown_runtime.refresh_once()

        self.assertEqual(result["reason"], "upstream_timeout")
        self.assertEqual(crown_runtime.state["status"], "数据延迟")
        self.assertEqual(crown_runtime.state["error"], "upstream_timeout")
        self.assertEqual(crown_runtime.state["events"], [{"id": "previous-event"}])
        self.assertEqual(crown_runtime.state["updated_at"], "2026-10-28T18:00:00+08:00")
        self.assertEqual(crown_runtime.state["diagnostics"]["consecutive_failures"], 1)

    def test_refresh_lock_rejects_overlapping_refresh(self):
        started = threading.Event()
        release = threading.Event()
        result_holder = {}

        class FakeStore:
            def accounts(self):
                return [{"id": "test", "kind": "crown_odds", "enabled": True, "has_session": True}]

            def load_session(self, account_id):
                return {}

        class SlowClient:
            odds_format = "HK"
            raw_odds_format = "CROWN_HK"

            def __init__(self, account, session):
                pass

            def list_games(self, mode):
                started.set()
                release.wait(1)
                return {"games": []}

        previous = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_crown_state, previous)

        def worker():
            with patch("crown_runtime.initialize"), \
                    patch("crown_runtime.store", FakeStore()), \
                    patch("crown_runtime.CrownOddsClient", SlowClient):
                result_holder["result"] = crown_runtime.refresh_once()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            overlapping = crown_runtime.refresh_once()
            self.assertEqual(overlapping["reason"], "refresh_in_progress")
            self.assertTrue(overlapping["in_progress"])
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result_holder["result"]["ok"])

    def test_compact_snapshot_strips_unused_payload(self):
        previous = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_crown_state, previous)
        with crown_runtime.lock:
            crown_runtime.state["events"] = [{
                "id": "123",
                "league_id": "1",
                "league": "测试联赛",
                "is_live": False,
                "home": "主队",
                "away": "客队",
                "start_label": "10-28 20:00",
                "score": "",
                "minute": "",
                "period": "",
                "odds_format": "HK",
                "odds_format_label": "皇冠港赔（HK）",
                "markets": {
                    "handicap": [{
                        "line": "0.5", "a": "0.90", "b": "0.90",
                        "raw_a": "1.90", "raw_b": "1.90", "gidm": "987",
                    }],
                    "total": [],
                    "moneyline": ["1.10", "2.20", "3.30"],
                },
                "extra_markets": {
                    "status": "loaded",
                    "handicap": [{
                        "line": "0.25", "raw_line": "0 / 0.5", "a": "0.80", "b": "1.02",
                        "market_type": "handicap", "market_key": "R", "master": "N",
                        "gidm": "987", "payload": "large",
                    }],
                    "total": [],
                    "moneyline": [],
                    "rows": [{"payload": "large"}],
                },
            }]

        snapshot = app._snapshot_crown(compact=True)
        event = snapshot["events"][0]

        self.assertEqual(event["extra_markets"]["status"], "loaded")
        self.assertEqual(event["extra_markets"]["handicap"][0]["raw_line"], "0 / 0.5")
        self.assertEqual(event["extra_markets"]["handicap"][0]["market_type"], "handicap")
        self.assertNotIn("payload", event["extra_markets"]["handicap"][0])
        self.assertNotIn("rows", event["extra_markets"])
        self.assertNotIn("odds_format", event)
        self.assertNotIn("raw_a", event["markets"]["handicap"][0])
        self.assertEqual(event["markets"]["handicap"][0]["gidm"], "987")
        self.assertEqual(event["markets"]["moneyline"], ["1.10", "2.20", "3.30"])

    def test_upstream_failures_use_capped_backoff(self):
        previous = copy.deepcopy(crown_runtime.state)
        self.addCleanup(self.restore_crown_state, previous)
        with crown_runtime.lock:
            diagnostics = dict(crown_runtime.state["diagnostics"])
            diagnostics["consecutive_failures"] = 1
            crown_runtime.state["diagnostics"] = diagnostics
        delays = []
        for failures in range(1, 6):
            with crown_runtime.lock:
                diagnostics = dict(crown_runtime.state["diagnostics"])
                diagnostics["consecutive_failures"] = failures
                crown_runtime.state["diagnostics"] = diagnostics
            delays.append(app._crown_retry_delay({"ok": False, "reason": "upstream_timeout"}))
        self.assertEqual(delays, [4, 8, 16, 30, 30])

    def test_excludes_special_betting_marker_but_keeps_regular_league(self):
        self.assertTrue(is_excluded_league("阿根廷职业联赛-特别投注"))
        self.assertTrue(is_excluded_league("阿根廷职业联赛 - 特别投注"))
        self.assertTrue(is_excluded_league("电竞足球-GT体育联赛(2 x 6分钟)"))
        self.assertTrue(is_excluded_league("足球电竞联赛"))
        self.assertTrue(is_excluded_league("电子足球联赛"))
        self.assertFalse(is_excluded_league("阿根廷职业联赛"))
        self.assertFalse(is_excluded_league("英格兰 - 超级联赛"))
        self.assertFalse(is_excluded_league("克罗地亚足球联赛"))


    def test_crown_refresh_does_not_publish_special_betting_league(self):
        class FakeStore:
            def accounts(self):
                return [{"id": "test", "kind": "crown_odds", "enabled": True, "has_session": True}]

            def load_session(self, account_id):
                return {}

        class FakeClient:
            odds_format = "HK"
            raw_odds_format = "CROWN_HK"

            def __init__(self, account, session):
                pass

            def list_games(self, mode):
                if mode == "live":
                    return {"games": []}
                return {"games": [
                    {"GID": "201", "LEAGUE": "阿根廷职业联赛", "TEAM_H": "主队甲", "TEAM_C": "客队甲"},
                    {"GID": "202", "LEAGUE": "阿根廷职业联赛-特别投注", "TEAM_H": "主队乙", "TEAM_C": "客队乙"},
                    {"GID": "203", "LEAGUE": "电竞足球-GT体育联赛(2 x 6分钟)", "TEAM_H": "虚拟主队", "TEAM_C": "虚拟客队"},
                ]}

        old_state = crown_runtime.state.copy()
        self.addCleanup(crown_runtime.state.update, old_state)
        with patch("crown_runtime.initialize"), \
                patch("crown_runtime.store", FakeStore()), \
                patch("crown_runtime.CrownOddsClient", FakeClient):
            result = crown_runtime.refresh_once()

        self.assertTrue(result["ok"])
        self.assertEqual([event["id"] for event in crown_runtime.state["events"]], ["201"])
        self.assertEqual(crown_runtime.state["leagues"], ["阿根廷职业联赛"])

    def test_crown_refresh_publishes_base_odds_before_slow_details(self):
        details_started = threading.Event()
        release_details = threading.Event()
        details_finished = threading.Event()

        class FakeStore:
            def accounts(self):
                return [{"id": "test", "kind": "crown_odds", "enabled": True, "has_session": True}]

            def load_session(self, account_id):
                return {}

        class SlowDetailsClient:
            odds_format = "HK"
            raw_odds_format = "CROWN_HK"

            def __init__(self, account, session):
                pass

            def list_games(self, mode):
                if mode == "live":
                    return {"games": []}
                return {"games": [{
                    "GID": "slow-details-test",
                    "LID": "1",
                    "ECID": "2",
                    "LEAGUE": "测试联赛",
                    "TEAM_H": "主队",
                    "TEAM_C": "客队",
                    "RATIO_R": "0",
                    "IOR_RH": "0.90",
                    "IOR_RC": "0.90",
                }]}

            def game_more(self, game, mode):
                details_started.set()
                release_details.wait(3)
                details_finished.set()
                return {"games": []}

        old_state = crown_runtime.state.copy()
        self.addCleanup(crown_runtime.state.update, old_state)
        try:
            with patch("crown_runtime.initialize"), \
                    patch("crown_runtime.load_config", return_value={"crown_extra_markets_enabled": True}), \
                    patch("crown_runtime.store", FakeStore()), \
                    patch("crown_runtime.CrownOddsClient", SlowDetailsClient):
                started = time.monotonic()
                result = crown_runtime.refresh_once()
                elapsed = time.monotonic() - started

                self.assertTrue(result["ok"])
                self.assertLess(elapsed, 0.5)
                self.assertTrue(details_started.wait(1))
                self.assertTrue(crown_runtime.state["updated_at"])
                self.assertEqual(len(crown_runtime.state["events"]), 1)
        finally:
            release_details.set()
            self.assertTrue(details_finished.wait(2))


if __name__ == "__main__":
    unittest.main()
