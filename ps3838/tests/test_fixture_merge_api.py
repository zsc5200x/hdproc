import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from dashboard_server import DashboardHandler, OddsStore, now_iso
from fixture_merge import FixtureMergeStore, prepare_crown_event, prepare_pinnacle_event


class FixtureMergeApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.odds_store = OddsStore({
            "capture_path": str(root / "capture.jsonl"),
            "live_snapshot_file": str(root / "latest.csv"),
            "dashboard_db_path": str(root / "history.sqlite3"),
        })
        self.merge_store = FixtureMergeStore(root / "fixture_merge.sqlite3")
        self.addCleanup(self.odds_store.close)
        self.addCleanup(self.merge_store.close)

        kickoff_ms = int((datetime.now().astimezone() + timedelta(minutes=20)).timestamp() * 1000)
        odds_payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": False,
                "l": [[29, "Soccer", [[197248, "厄瓜多尔 - 乙级联赛", [[100, "十月九", "昆卡青年队", 0, kickoff_ms]]]]]],
                "u": [],
            },
        }
        self.odds_store.process_line(json.dumps({
            "captured_at": datetime.now().astimezone().isoformat(),
            "opcode": 1,
            "payload": json.dumps(odds_payload),
        }))

        self.old_handler_values = (
            getattr(DashboardHandler, "store", None),
            getattr(DashboardHandler, "merge_store", None),
        )
        DashboardHandler.store = self.odds_store
        DashboardHandler.merge_store = self.merge_store
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._close_server)

    def _close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        DashboardHandler.store, DashboardHandler.merge_store = self.old_handler_values

    def _request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as response:
            return response.code, json.loads(response.read().decode("utf-8"))

    def test_merge_excludes_configured_fantasy_league(self):
        crown_events = [
            {
                "id": "200",
                "home": "十月九",
                "away": "昆卡青年队",
                "league": "厄瓜多尔乙组联赛-附加赛",
                "league_id": "108994",
                "start_label": datetime.now().strftime("%m-%d 01:00p"),
                "is_live": False,
            },
            {
                "id": "201",
                "home": "虚构主队",
                "away": "虚构客队",
                "league": "奇幻赛事",
                "league_id": "108688",
                "start_label": datetime.now().strftime("%m-%d 01:00p"),
                "is_live": False,
            },
        ]
        with patch.object(DashboardHandler, "_crown_fixture_events", return_value=(crown_events, None)),                 patch.object(DashboardHandler, "config", {"merge_excluded_leagues": ["奇幻赛事"]}, create=True):
            status, payload = self._request("/api/merge")

        self.assertEqual(status, 200)
        self.assertEqual([event["id"] for event in payload["crownEvents"]], ["200"])
        self.assertNotIn("奇幻赛事", [league["name"] for league in payload["crownLeagues"]])

    def test_candidate_is_suggestion_until_user_confirms_mapping(self):
        crown_events = [{
            "id": "200",
            "home": "十月九",
            "away": "库恩卡青年",
            "league": "厄瓜多尔乙组联赛-附加赛",
            "league_id": "108994",
            "start_label": datetime.now().strftime("%m-%d 01:00p"),
            "is_live": False,
        }]
        with patch.object(DashboardHandler, "_crown_fixture_events", return_value=(crown_events, None)):
            status, payload = self._request("/api/merge")
            self.assertEqual(status, 200)
            self.assertEqual(payload["mappings"], [])
            self.assertEqual(payload["pinnacleEvents"][0]["id"], "100")
            self.assertEqual(payload["pinnacleEvents"][0]["leagueId"], "197248")
            self.assertEqual(payload["crownEvents"][0]["leagueId"], "108994")
            self.assertEqual(payload["pinnacleLeagues"][0]["id"], "197248")
            self.assertEqual(payload["crownLeagues"][0]["id"], "108994")
            self.assertEqual(payload["leagueMappings"], [])
            self.assertEqual(payload["suggestions"], [])
            league_candidate = payload["leagueSuggestions"][0]
            self.assertEqual(league_candidate["pinnacleLeagueId"], "197248")
            self.assertEqual(league_candidate["crownLeagueId"], "108994")
            self.assertGreater(league_candidate["score"], 60)

            status, rejected = self._request("/api/merge/mapping", {
                "action": "map", "pinnacleId": "100", "crownId": "200",
            })
            self.assertEqual(status, 409)
            self.assertIn("请先确认", rejected["error"])

            status, league_saved = self._request("/api/merge/league-mapping", {
                "action": "map", "pinnacleLeagueId": "197248", "crownLeagueId": "108994",
            })
            self.assertEqual(status, 200)
            self.assertEqual(league_saved["leagueMappings"][0]["pinnacleId"], "197248")

            _status, payload = self._request("/api/merge")
            self.assertEqual(payload["suggestions"][0]["crownId"], "200")

            status, saved = self._request("/api/merge/mapping", {
                "action": "map", "pinnacleId": "100", "crownId": "200",
            })
            self.assertEqual(status, 200)
            self.assertEqual(saved["mappings"][0]["pinnacleId"], "100")
            self.assertEqual(saved["mappings"][0]["crownId"], "200")

            status, blocked = self._request("/api/merge/league-mapping", {
                "action": "unmap", "crownLeagueId": "108994",
            })
            self.assertEqual(status, 409)
            self.assertIn("先解除赛事关联", blocked["error"])

            status, removed = self._request("/api/merge/mapping", {
                "action": "unmap", "pinnacleId": "100",
            })
            self.assertEqual(status, 200)
            self.assertEqual(removed["mappings"], [])

            status, league_removed = self._request("/api/merge/league-mapping", {
                "action": "unmap", "crownLeagueId": "108994",
            })
            self.assertEqual(status, 200)
            self.assertEqual(league_removed["leagueMappings"], [])

    def test_confirmed_endpoint_returns_crown_display_and_pinnacle_odds(self):
        crown_event = {
            "id": "200",
            "home": "十月九",
            "away": "库恩卡青年",
            "league": "厄瓜多尔乙组联赛-附加赛",
            "league_id": "108994",
            "start_label": datetime.now().strftime("%m-%d 01:00p"),
            "is_live": False,
            "markets": {
                "handicap": [{"line": "0", "a": "0.91", "b": "0.93"}],
                "total": [],
                "moneyline": ["1.20", "3.40", "5.80"],
            },
        }
        pinnacle_event = self.odds_store.events["100"]
        self.merge_store.map_leagues(
            {"id": "197248", "name": pinnacle_event["league"], "eventCount": 1},
            {"id": "108994", "name": crown_event["league"], "eventCount": 1},
        )
        self.merge_store.map_events(
            prepare_pinnacle_event(pinnacle_event),
            prepare_crown_event(crown_event),
        )
        with self.odds_store.lock:
            self.odds_store._add_row(
                "100", "0", "1", "1", "0", "", "", now_iso(),
                1.91, 0.91, True, "new", None, None, 1,
            )
        with patch.object(DashboardHandler, "_crown_fixture_events", return_value=([crown_event], None)):
            status, payload = self._request("/api/merge/confirmed?crownId=200")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["crown"]["id"], "200")
        self.assertEqual(item["crown"]["home"], "十月九")
        self.assertEqual(item["crown"]["markets"]["handicap"][0]["a"], "0.91")
        self.assertEqual(item["pinnacle"]["id"], "100")
        self.assertEqual(item["pinnacle"]["odds"][0]["hongKong"], 0.91)

        with patch.object(DashboardHandler, "_crown_fixture_events", return_value=([crown_event], None)):
            status, payload = self._request("/api/merge/confirmed?crownId=missing")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])

    def test_confirmed_endpoint_uses_fresh_variant_even_when_feed_marks_it_not_live(self):
        pinnacle_event = self.odds_store.events["100"]
        crown_event = {
            "id": "200",
            "home": "十月九",
            "away": "昆卡青年队",
            "league": "厄瓜多尔乙组联赛",
            "league_id": "108994",
            "start_label": datetime.now().strftime("%m-%d 01:00p"),
            "is_live": True,
        }
        self.merge_store.map_leagues(
            {"id": pinnacle_event["leagueId"], "name": pinnacle_event["league"], "eventCount": 1},
            {"id": "108994", "name": crown_event["league"], "eventCount": 1},
        )
        self.merge_store.map_events(
            prepare_pinnacle_event(pinnacle_event),
            prepare_crown_event(crown_event),
        )

        fresh_variant_id = "101"
        self.odds_store.register_event(
            fresh_variant_id,
            pinnacle_event["name"].split(" VS ", 1)[0],
            pinnacle_event["name"].split(" VS ", 1)[1],
            pinnacle_event["league"],
            pinnacle_event["kickoff"],
            sport_id=pinnacle_event["sportId"],
            league_id=pinnacle_event["leagueId"],
        )
        # The replacement event can have fresh odds before its live flag flips.
        later_kickoff = (
            datetime.fromisoformat(pinnacle_event["kickoff"]) + timedelta(days=1)
        ).strftime("%Y-%m-%d %H:%M:%S")
        self.odds_store.register_event(
            "102",
            pinnacle_event["name"].split(" VS ", 1)[0],
            pinnacle_event["name"].split(" VS ", 1)[1],
            pinnacle_event["league"],
            later_kickoff,
            sport_id=pinnacle_event["sportId"],
            league_id=pinnacle_event["leagueId"],
        )
        self.odds_store._set_event_live("102", True)
        with self.odds_store.lock:
            for option, line, price in (("3", "2.75", 0.813), ("4", "2.75", 1.07)):
                self.odds_store._add_row(
                    fresh_variant_id, "0", "3", option, line, "", str(price), now_iso(),
                    1 + price, price, True, "new", None, None, 1,
                )

        with patch.object(DashboardHandler, "_crown_fixture_events", return_value=([crown_event], None)):
            status, payload = self._request("/api/merge/confirmed?crownId=200")
        self.assertEqual(status, 200)
        item = payload["items"][0]
        self.assertEqual(item["pinnacleId"], "100")
        self.assertEqual(item["pinnacle"]["id"], fresh_variant_id)
        self.assertFalse(item["pinnacle"]["isLive"])
        quotes = {
            (row["option"], row["line"]): row["hongKong"]
            for row in item["pinnacle"]["odds"]
        }
        self.assertEqual(quotes[("3", "2.75")], 0.813)
        self.assertEqual(quotes[("4", "2.75")], 1.07)

    def test_merge_pool_reaches_past_the_daily_dashboard_scope(self):
        kickoff = datetime.now().astimezone() + timedelta(hours=30)
        self.odds_store.register_event(
            "p-far", "科尼亚体育", "加拉塔萨雷", "土耳其 - 超级联赛",
            kickoff.strftime("%Y-%m-%d %H:%M:%S"),
        )
        snapshot_ids = {item["id"] for item in self.odds_store.snapshot()["events"]}
        merge_ids = {item["id"] for item in self.odds_store.merge_events(horizon_hours=48)}
        self.assertNotIn("p-far", snapshot_ids)
        self.assertIn("p-far", merge_ids)
        default_ids = {item["id"] for item in self.odds_store.merge_events()}
        self.assertNotIn("p-far", default_ids)

    def test_auto_map_leaves_a_new_league_for_manual_confirmation(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-new", "home": "Arsenal", "away": "Chelsea",
            "league": "Premier League", "leagueId": "p-new-league",
            "kickoff": "2026-10-30T12:00:00+08:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-new", "home": "Arsenal", "away": "Chelsea",
            "league": "Premier League", "league_id": "c-new-league",
            "start_label": "10-30 12:00p", "is_live": False,
        })
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.rank_candidates", return_value={}):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["leagueAutoConfirm"])
        self.assertEqual(payload["mappedLeagues"], [])
        self.assertEqual(self.merge_store.list_league_mappings(), [])

    def test_auto_map_confirms_unambiguous_league_and_fixture_scores_above_85(self):
        pinnacle_events = [{
            "id": "p-high",
            "home": "Arsenal",
            "away": "Chelsea",
            "league": "Premier League",
            "leagueId": "p-high-league",
            "kickoff": "2026-10-30T12:00:00+08:00",
            "isLive": False,
        }]
        crown_events = [{
            "id": "c-high",
            "home": "Arsenal",
            "away": "Chelsea",
            "league": "Premier League",
            "league_id": "c-high-league",
            "start_label": "10-30 12:00p",
            "is_live": False,
        }]
        pinnacle = prepare_pinnacle_event(pinnacle_events[0])
        crown = prepare_crown_event(crown_events[0])
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.AUTO_CONFIRM_LEAGUES", True), patch(
            "dashboard_server.rank_candidates", return_value={
                "p-high": [{"pinnacleId": "p-high", "crownId": "c-high", "score": 92.0,
                            "reversedOrder": False}],
            }
        ):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["leagueThreshold"], 85.0)
        self.assertEqual(payload["fixtureThreshold"], 85.0)
        self.assertEqual(len(payload["mappedLeagues"]), 1)
        self.assertGreater(payload["mappedLeagues"][0]["score"], 85)
        self.assertEqual(payload["mappedLeagues"][0]["pinnacleId"], "p-high-league")
        self.assertEqual(payload["mappedLeagues"][0]["crownId"], "c-high-league")
        self.assertEqual(len(payload["mappedFixtures"]), 1)
        self.assertGreater(payload["mappedFixtures"][0]["score"], 85)
        self.assertEqual(self.merge_store.list_mappings()[0]["pinnacleId"], "p-high")

    def test_auto_map_does_not_confirm_a_league_on_country_boost_alone(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-egy", "home": "Egypt A", "away": "Egypt B",
            "league": "埃及 - 第二甲级联赛", "leagueId": "p-egy",
            "kickoff": "2026-10-30T12:00:00+08:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-egy", "home": "Egypt C", "away": "Egypt D",
            "league": "埃及乙组联赛A", "league_id": "c-egy",
            "start_label": "10-30 12:00p", "is_live": False,
        })
        boosted = {
            "c-egy": [{
                "crownLeagueId": "c-egy",
                "pinnacleLeagueId": "p-egy",
                "score": 94.7,
                "baseScore": 72.7,
                "evidenceCount": 1,
                "reasons": ["同一国家（埃及）", "赛事数量一致（7 场）"],
            }],
        }
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.rank_league_candidates", return_value=boosted), \
                patch("dashboard_server.rank_candidates", return_value={}):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["mappedLeagues"], [])

    def test_auto_map_skips_fixture_at_85_and_ambiguous_crown_candidate(self):
        pinnacle_events = [
            prepare_pinnacle_event({
                "id": event_id, "home": "Arsenal", "away": "Chelsea",
                "league": "Premier League", "leagueId": "p-league",
                "kickoff": "2026-10-30T12:00:00+08:00", "isLive": False,
            })
            for event_id in ("p-1", "p-2", "p-3")
        ]
        crown_events = [
            prepare_crown_event({
                "id": event_id, "home": "Arsenal", "away": "Chelsea",
                "league": "Premier League", "league_id": "c-league",
                "start_label": "10-30 12:00p", "is_live": False,
            })
            for event_id in ("c-1", "c-2")
        ]
        self.merge_store.map_leagues(
            {"id": "p-league", "name": "Premier League"},
            {"id": "c-league", "name": "Premier League"},
        )
        ranked = {
            "p-1": [{"pinnacleId": "p-1", "crownId": "c-1", "score": 91.0,
                     "reversedOrder": False}],
            "p-2": [{"pinnacleId": "p-2", "crownId": "c-1", "score": 90.0,
                     "reversedOrder": False}],
            "p-3": [{"pinnacleId": "p-3", "crownId": "c-2", "score": 85.0,
                     "reversedOrder": False}],
        }
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            pinnacle_events, crown_events, None,
        )), patch("dashboard_server.rank_candidates", return_value=ranked):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["mappedFixtures"], [])
        self.assertEqual(self.merge_store.list_mappings(), [])


    def test_auto_map_uses_time_gate_for_translated_team_names(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-t", "home": "桑托斯足球俱乐部", "away": "帕尔梅拉斯",
            "league": "巴西 - 甲级联赛", "leagueId": "p-br",
            "kickoff": "2026-10-30T12:00:00+08:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-t", "home": "山度士SP", "away": "彭美拉斯SP",
            "league": "巴西甲组联赛", "league_id": "c-br",
            "start_label": "10-30 12:00p", "is_live": False,
        })
        self.merge_store.map_leagues(
            {"id": "p-br", "name": "巴西 - 甲级联赛"},
            {"id": "c-br", "name": "巴西甲组联赛"},
        )
        ranked = {"p-t": [{
            "pinnacleId": "p-t", "crownId": "c-t", "score": 50.0,
            "reversedOrder": False, "teamScore": 0.6, "kickoffDeltaMinutes": 0.0,
        }]}
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.rank_candidates", return_value=ranked):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["fixtureTimeMinutes"], 15.0)
        self.assertEqual(payload["fixtureTeamFloor"], 0.15)
        self.assertEqual(len(payload["mappedFixtures"]), 1)
        self.assertEqual(payload["mappedFixtures"][0]["pinnacleId"], "p-t")
        self.assertEqual(self.merge_store.list_mappings()[0]["crownId"], "c-t")

    def test_auto_map_rejects_time_mismatch_even_with_high_team_score(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-m", "home": "瓦斯科达伽马", "away": "弗拉门戈",
            "league": "巴西 - 甲级联赛", "leagueId": "p-br",
            "kickoff": "2026-10-30T12:00:00+08:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-m", "home": "甘美奥RS", "away": "帕拉尼恩斯",
            "league": "巴西甲组联赛", "league_id": "c-br",
            "start_label": "10-30 12:00p", "is_live": False,
        })
        self.merge_store.map_leagues(
            {"id": "p-br", "name": "巴西 - 甲级联赛"},
            {"id": "c-br", "name": "巴西甲组联赛"},
        )
        ranked = {"p-m": [{
            "pinnacleId": "p-m", "crownId": "c-m", "score": 60.0,
            "reversedOrder": False, "teamScore": 0.9, "kickoffDeltaMinutes": 90.0,
        }]}
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.rank_candidates", return_value=ranked):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["mappedFixtures"], [])
        self.assertEqual(self.merge_store.list_mappings(), [])

    def test_auto_map_refuses_high_score_when_the_kickoffs_are_five_hours_apart(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-youth", "home": "科尼亚体育", "away": "加拉塔萨雷",
            "league": "土耳其 - 超级联赛U19", "leagueId": "p-u19",
            "kickoff": "2026-10-30 20:00:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-senior", "home": "科尼亚体育", "away": "加拉塔萨雷",
            "league": "土耳其超级联赛", "league_id": "c-tr",
            "start_label": "10-30 01:00p", "is_live": False,
        })
        self.merge_store.map_leagues(
            {"id": "p-u19", "name": "土耳其 - 超级联赛U19"},
            {"id": "c-tr", "name": "土耳其超级联赛"},
        )
        ranked = {"p-youth": [{
            "pinnacleId": "p-youth", "crownId": "c-senior", "score": 91.0,
            "reversedOrder": False, "teamScore": 1.0,
            "kickoffDeltaMinutes": 300.0,
        }]}
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch("dashboard_server.rank_candidates", return_value=ranked):
            status, payload = self._request("/api/merge/auto-map", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["mappedFixtures"], [])
        self.assertEqual(self.merge_store.list_mappings(), [])

    def test_manual_mapping_refuses_a_youth_namesake_fixture(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-youth", "home": "科尼亚体育", "away": "加拉塔萨雷",
            "league": "土耳其 - 超级联赛U19", "leagueId": "p-u19",
            "kickoff": "2026-10-30T20:00:00+08:00", "isLive": False,
        })
        crown = prepare_crown_event({
            "id": "c-senior", "home": "科尼亚体育", "away": "加拉塔萨雷",
            "league": "土耳其超级联赛", "league_id": "c-tr",
            "start_label": "10-30 01:00p", "is_live": False,
        })
        self.merge_store.map_leagues(
            {"id": "p-u19", "name": "土耳其 - 超级联赛U19"},
            {"id": "c-tr", "name": "土耳其超级联赛"},
        )
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )), patch.object(DashboardHandler, "_crown_fixture_events", return_value=(
            [crown], None,
        )):
            status, payload = self._request("/api/merge/mapping", {
                "action": "map", "pinnacleId": "p-youth", "crownId": "c-senior",
            })
        self.assertEqual(status, 409)
        self.assertIn("类别不一致", payload["error"])
        self.assertEqual(self.merge_store.list_mappings(), [])

    def test_league_mapping_refuses_a_youth_league_for_a_senior_league(self):
        pinnacle = prepare_pinnacle_event({
            "id": "p-youth", "home": "科尼亚体育U19", "away": "加拉塔萨雷U19",
            "league": "土耳其 - 超级联赛U19", "leagueId": "p-u19",
            "kickoff": "2026-10-30 20:00:00", "isLive": True,
        })
        crown = prepare_crown_event({
            "id": "c-senior", "home": "科尼亚体育", "away": "加拉塔萨雷",
            "league": "土耳其超级联赛", "league_id": "c-tr",
            "start_label": "10-30 01:00p", "is_live": False,
        })
        with patch.object(DashboardHandler, "_merge_event_lists", return_value=(
            [pinnacle], [crown], None,
        )):
            status, payload = self._request("/api/merge/league-mapping", {
                "action": "map",
                "pinnacleLeagueId": "p-u19",
                "crownLeagueId": "c-tr",
            })
        self.assertEqual(status, 409)
        self.assertIn("类别不一致", payload["error"])
        self.assertEqual(self.merge_store.list_league_mappings(), [])

    def test_opportunities_endpoint_ranks_same_line_cross_book_pairs(self):
        # Crown renders kickoff in its own display zone; the label must describe
        # the same instant as the Pinnacle kickoff or the pair guard drops it.
        # Crown is parsed with a fixed GMT-4 offset (matching the partner backend).
        kickoff = datetime.now().astimezone() + timedelta(minutes=20)
        crown_kickoff = kickoff.astimezone(ZoneInfo("Etc/GMT+4"))
        crown_label = crown_kickoff.strftime("%m-%d %I:%M") + (
            "p" if crown_kickoff.hour >= 12 else "a"
        )
        self.merge_store.map_leagues(
            {"id": "197248", "name": "厄瓜多尔 - 乙级联赛", "eventCount": 1},
            {"id": "108994", "name": "厄瓜多尔乙级联赛", "eventCount": 1},
        )
        self.merge_store.map_events(
            {
                "id": "100", "home": "十月九", "away": "库恩卡青年",
                "league": "厄瓜多尔 - 乙级联赛", "leagueId": "197248",
                "kickoff": kickoff.isoformat(),
            },
            {
                "id": "200", "home": "十月九", "away": "库恩卡青年",
                "league": "厄瓜多尔乙级联赛", "leagueId": "108994",
                "start_label": crown_label,
            },
        )
        captured = datetime.now().astimezone().isoformat()
        rows = [
            [0, 2, 0, -0.5, 105.0, "1.05", 1, 1, 0, 0, "O", 0, 100],
            [0, 2, 1, 0.5, 103.0, "1.03", 1, 1, 0, 0, "O", 0, 100],
        ]
        self.odds_store.process_line(json.dumps({
            "captured_at": captured,
            "opcode": 1,
            "payload": json.dumps({
                "type": "UPDATE_ODDS",
                "odds": {"live": False, "l": [], "n": [], "u": [[29, rows]]},
            }),
        }))
        crown_event = {
            "id": "200", "league_id": "108994", "league": "厄瓜多尔乙级联赛",
            "home": "十月九", "away": "库恩卡青年", "is_live": False,
            "start_label": crown_label, "score": "", "minute": "",
            "markets": {"handicap": [{"line": "-0.5", "a": "1.05", "b": "0.90"}], "total": []},
            "extra_markets": {"status": "not_requested"},
        }
        with patch.object(
            DashboardHandler, "_crown_fixture_events", return_value=([crown_event], None)
        ):
            status, payload = self._request("/api/merge/opportunities")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(payload["count"], 1)
        best = payload["items"][0]
        self.assertEqual((best["crownSide"], best["pinnacleSide"]), ("home", "away"))
        self.assertEqual(best["lineText"], "-0.5")
        self.assertAlmostEqual(best["edge"], 0.0196, places=3)
        self.assertEqual(best["tier"], "arb")
        self.assertEqual(best["crownId"], "200")


if __name__ == "__main__":
    unittest.main()
