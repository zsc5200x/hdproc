import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard_server import (
    MAX_JSON_BODY_BYTES,
    MARKET_ROW_MISSES,
    DashboardHandler,
    OddsStore,
    event_has_display_metadata,
    event_scope,
    is_full_time_market,
)


class DashboardStoreTests(unittest.TestCase):
    def make_store(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        store = OddsStore(
            {
                "capture_path": str(root / "capture.jsonl"),
                "live_snapshot_file": str(root / "latest.csv"),
                "dashboard_db_path": str(root / "history.sqlite3"),
            }
        )
        self.addCleanup(store.close)
        return store

    def record(self, captured_at, price):
        kickoff_ms = int(datetime.now().timestamp() * 1000) - 20 * 60 * 1000
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": True,
                "l": [
                    [
                        1,
                        "Soccer",
                        [
                            [
                                10,
                                "Test League",
                                [[100, "Home", "Away", 0, kickoff_ms]],
                            ]
                        ],
                    ]
                ],
                "u": [
                    [
                        1,
                        [[0, 1, 0, None, 100.0, price, 0, 0, 0, 0, "O", 0, 100]],
                    ]
                ],
            },
        }
        return json.dumps(
            {
                "captured_at": captured_at,
                "opcode": 1,
                "payload": json.dumps(payload),
            }
        )

    def test_processes_event_and_odds_change(self):
        store = self.make_store()
        store.process_line(self.record("2026-10-28T05:00:00.000+08:00", "2.000"))
        store.process_line(self.record("2026-10-28T05:00:01.000+08:00", "2.100"))
        snapshot = store.snapshot("100")

        self.assertEqual(snapshot["stats"]["events"], 1)
        self.assertEqual(snapshot["stats"]["rows"], 1)
        self.assertEqual(snapshot["events"][0]["name"], "Home VS Away")
        self.assertEqual(snapshot["events"][0]["sportId"], "1")
        self.assertEqual(snapshot["events"][0]["leagueId"], "10")
        self.assertEqual(snapshot["odds"][0]["sportId"], "1")
        self.assertEqual(snapshot["odds"][0]["leagueId"], "10")
        self.assertEqual(snapshot["events"][0]["metadataStatus"], "complete")
        self.assertEqual(snapshot["odds"][0]["decimal"], 2.1)
        self.assertEqual(snapshot["odds"][0]["direction"], "up")
        self.assertAlmostEqual(snapshot["odds"][0]["delta"], 0.1)
        self.assertEqual(snapshot["changes"][0]["old"], 2.0)
        self.assertEqual(snapshot["changes"][0]["new"], 2.1)
        self.assertGreaterEqual(len(snapshot["odds"][0]["history"]), 2)

        store.flush_history()
        stored = store.history_db.get_event("100")
        self.assertEqual(stored["sportId"], "1")
        self.assertEqual(stored["leagueId"], "10")

    def _total_record(self, captured_at, price):
        kickoff_ms = int(datetime.now().timestamp() * 1000) - 20 * 60 * 1000
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": True,
                "l": [[1, "Soccer", [[10, "Test League", [[100, "Home", "Away", 0, kickoff_ms]]]]]],
                "u": [
                    [1, [[0, 3, 4, 3.0, None, price, 0, 0, 0, 0, "O", 0, 100]]],
                ],
            },
        }
        return json.dumps({
            "captured_at": captured_at,
            "opcode": 1,
            "payload": json.dumps(payload),
        })

    def test_suspension_keeps_the_last_quoted_water_for_the_paired_card(self):
        store = self.make_store()
        store.process_line(self._total_record("2026-10-30T19:57:21.000+08:00", "1.854"))
        store.process_line(self._total_record("2026-10-30T20:31:00.000+08:00", None))

        details = store.event_details("100")
        row = next(item for item in details["odds"] if item["market"] == "3")
        # The live tick is strict: no current quote.
        self.assertFalse(row["quoted"])
        self.assertIsNone(row["hongKong"])
        # The last usable water stays available for the paired card.
        self.assertEqual(row["lastHongKong"], 1.854)
        self.assertEqual(row["lastQuotedAt"], "2026-10-30T19:57:21.000+08:00")
        self.assertTrue(row["suspended"])

    def test_restart_recovers_the_last_water_from_the_history_database(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        config = {
            "capture_path": str(root / "capture.jsonl"),
            "live_snapshot_file": str(root / "latest.csv"),
            "dashboard_db_path": str(root / "history.sqlite3"),
        }
        store = OddsStore(config)
        store.process_line(self._total_record("2026-10-30T19:57:21.000+08:00", "1.854"))
        store.flush_history()
        store.close()

        restarted = OddsStore(config)
        self.addCleanup(restarted.close)
        restarted.process_line(self._total_record("2026-10-30T20:31:00.000+08:00", None))
        details = restarted.event_details("100")
        row = next(item for item in details["odds"] if item["market"] == "3")
        self.assertEqual(row["lastHongKong"], 1.854)
        self.assertTrue(row["suspended"])

    def test_filters_to_full_time_supported_markets(self):
        store = self.make_store()
        kickoff_ms = int(datetime.now().timestamp() * 1000) - 20 * 60 * 1000
        rows = [
            [0, 1, 0, None, 100.0, "2.000", 0, 0, 0, 0, "O", 0, 100],
            [1, 1, 0, None, 100.0, "3.000", 0, 0, 0, 0, "O", 0, 100],
            [0, 4, 0, None, 100.0, "4.000", 0, 0, 0, 0, "O", 0, 100],
        ]
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": True,
                "l": [[1, "Soccer", [[10, "Test League", [[100, "Home", "Away", 0, kickoff_ms]]]]]],
                "u": [[1, rows]],
            },
        }
        store.process_line(
            json.dumps(
                {
                    "captured_at": "2026-10-28T05:00:00.000+08:00",
                    "opcode": 1,
                    "payload": json.dumps(payload),
                }
            )
        )

        snapshot = store.snapshot("100")

        self.assertEqual(snapshot["stats"]["rows"], 1)
        self.assertEqual(len(snapshot["odds"]), 1)
        self.assertEqual(snapshot["odds"][0]["stage"], "0")
        self.assertEqual(snapshot["odds"][0]["market"], "1")

    def test_presence_endpoint_accepts_large_multi_event_body(self):
        body = json.dumps(
            {
                "eventIds": [str(index) for index in range(500)],
                "rowKeysByEvent": {
                    str(index): [
                        "0|1|0|",
                        "0|1|1|",
                        "0|1|2|",
                        *[
                            f"0|3|{option}|{line}"
                            for line in ("1.25", "1.5", "1.75", "2", "2.25")
                            for option in (3, 4)
                        ],
                        "0|2|0|0",
                        "0|2|1|0",
                    ]
                    for index in range(500)
                },
            }
        ).encode("utf-8")
        self.assertGreater(len(body), 64 * 1024)
        self.assertLess(len(body), MAX_JSON_BODY_BYTES)

        handler = DashboardHandler.__new__(DashboardHandler)
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)

        payload = handler.read_json_body()

        self.assertEqual(len(payload["eventIds"]), 500)
        self.assertEqual(len(payload["rowKeysByEvent"]), 500)

    def test_full_time_market_predicate(self):
        self.assertTrue(is_full_time_market("0", "1"))
        self.assertTrue(is_full_time_market("0", "2"))
        self.assertTrue(is_full_time_market("0", "3"))
        self.assertFalse(is_full_time_market("1", "1"))
        self.assertFalse(is_full_time_market("0", "4"))

    def test_parses_live_score_and_match_clock(self):
        store = self.make_store()
        event = [100, "Home", "Away", 0, 1790000000000]
        event.extend([0, 0, 0, 0, [2, 1], 0, 0, 0, None, None, "34'", "2H", "O"])
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": True,
                "l": [[1, "Soccer", [[10, "Argentina - Liga Pro", [event]]]]],
            },
        }
        store.process_line(
            json.dumps(
                {
                    "captured_at": "2026-10-28T05:00:00.000+08:00",
                    "opcode": 1,
                    "payload": json.dumps(payload),
                }
            )
        )

        snapshot = store.snapshot("100")
        current = snapshot["events"][0]

        self.assertEqual(current["league"], "Argentina - Liga Pro")
        self.assertEqual(current["scoreHome"], "2")
        self.assertEqual(current["scoreAway"], "1")
        self.assertEqual(current["matchMinute"], "34'")
        self.assertEqual(current["matchPeriod"], "2H")
        self.assertEqual(current["matchStatus"], "O")

    def test_history_is_persisted_across_store_restarts(self):
        store = self.make_store()
        config = store.config
        store.process_line(self.record("2026-10-28T05:00:00.000+08:00", "2.000"))
        store.process_line(self.record("2026-10-28T05:00:01.000+08:00", "2.100"))
        store.close()

        reopened = OddsStore(config)
        self.addCleanup(reopened.close)
        history = reopened.query_history(
            "100",
            hours=24 * 365,
            limit=100,
            filters={"market": "1", "stage": "0", "option": "0", "line": ""},
        )

        self.assertEqual(history["summary"]["count"], 2)
        self.assertEqual(history["summary"]["first"], 2.0)
        self.assertEqual(history["summary"]["last"], 2.1)
        self.assertAlmostEqual(history["summary"]["move"], 0.1)
        self.assertEqual(history["points"][-1]["direction"], "up")
        self.assertEqual(history["event"]["name"], "Home VS Away")

    def test_reloads_repaired_metadata_into_live_rows(self):
        store = self.make_store()
        captured_at = datetime.now().astimezone().isoformat()
        kickoff = (datetime.now().astimezone() - timedelta(minutes=20)).isoformat()
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "live": True,
                "u": [
                    [
                        1,
                        [[0, 1, 0, None, 100.0, "2.000", 0, 0, 0, 0, "O", 0, 100]],
                    ]
                ],
            },
        }
        store.process_line(
            json.dumps(
                {
                    "captured_at": captured_at,
                    "opcode": 1,
                    "payload": json.dumps(payload),
                }
            )
        )
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_id, name, league, kickoff, metadata_status,
                    last_captured_at, last_changed_at, updated_at
                ) VALUES ('100', ?, ?, ?, 'complete', ?, '', ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    name = excluded.name,
                    league = excluded.league,
                    kickoff = excluded.kickoff,
                    metadata_status = excluded.metadata_status,
                    last_captured_at = excluded.last_captured_at,
                    updated_at = excluded.updated_at
                """,
                (
                    "Home VS Away",
                    "Test League",
                    kickoff,
                    captured_at,
                    captured_at,
                ),
            )

        changed = store.reload_metadata_from_db()
        snapshot = store.snapshot("100")

        self.assertEqual(changed, 1)
        self.assertEqual(snapshot["events"][0]["name"], "Home VS Away")
        self.assertEqual(snapshot["events"][0]["metadataStatus"], "complete")
        self.assertEqual(snapshot["odds"][0]["eventName"], "Home VS Away")
        self.assertEqual(snapshot["stats"]["missing"], 0)

    def test_event_scope_only_keeps_live_and_today_upcoming(self):
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone(timedelta(hours=8)))

        self.assertEqual(
            event_scope(
                {
                    "isLive": True,
                    "kickoff": "2026-10-29T20:00:00+08:00",
                },
                now,
            ),
            "live",
        )
        self.assertEqual(
            event_scope(
                {
                    "isLive": False,
                    "kickoff": "2026-10-28T20:00:00+08:00",
                },
                now,
            ),
            "today",
        )
        self.assertEqual(
            event_scope(
                {
                    "isLive": False,
                    "kickoff": "2026-10-28T10:00:00+08:00",
                },
                now,
            ),
            "",
        )
        self.assertEqual(
            event_scope(
                {
                    "isLive": False,
                    "kickoff": "2026-10-29T09:00:00+08:00",
                },
                now,
            ),
            "",
        )

    def test_finished_and_stale_live_events_disappear(self):
        now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone(timedelta(hours=8)))

        self.assertEqual(
            event_scope(
                {
                    "isLive": True,
                    "kickoff": "2026-10-28T10:00:00+08:00",
                    "lastCapturedAt": "2026-10-28T11:59:55+08:00",
                    "matchMinute": "90'",
                    "matchPeriod": "END 90'",
                    "matchStatus": "H",
                },
                now,
            ),
            "",
        )
        self.assertEqual(
            event_scope(
                {
                    "isLive": True,
                    "kickoff": "2026-10-28T10:00:00+08:00",
                    "lastCapturedAt": "2026-10-28T11:58:20+08:00",
                    "matchMinute": "37'",
                    "matchPeriod": "2H",
                    "matchStatus": "O",
                },
                now,
            ),
            "",
        )
        self.assertEqual(
            event_scope(
                {
                    "isLive": True,
                    "kickoff": "2026-10-28T10:00:00+08:00",
                    "lastCapturedAt": "2026-10-28T11:59:30+08:00",
                    "matchMinute": "37'",
                    "matchPeriod": "2H",
                    "matchStatus": "O",
                },
                now,
            ),
            "live",
        )

    def test_placeholder_metadata_cannot_overwrite_complete_event(self):
        store = self.make_store()
        store.history_db.write_batch(
            [
                {
                    "eventId": "100",
                    "name": "Home VS Away",
                    "league": "Test League",
                    "kickoff": "2026-10-29T20:00:00+08:00",
                    "metadataStatus": "complete",
                    "lastCapturedAt": "2026-10-28T05:00:00+08:00",
                    "updatedAt": "2026-10-28T05:01:00+08:00",
                }
            ],
            [],
        )
        store.history_db.write_batch(
            [
                {
                    "eventId": "100",
                    "name": "赛事 100",
                    "league": "",
                    "kickoff": "",
                    "metadataStatus": "missing",
                    "lastCapturedAt": "2026-10-28T06:00:00+08:00",
                    "updatedAt": "2026-10-28T06:01:00+08:00",
                }
            ],
            [],
        )

        event = store.history_db.get_event("100")

        self.assertEqual(event["name"], "Home VS Away")
        self.assertEqual(event["league"], "Test League")
        self.assertEqual(event["kickoff"], "2026-10-29T20:00:00+08:00")
        self.assertEqual(event["metadataStatus"], "complete")

    def test_odds_only_event_is_registered_for_later_metadata_repair(self):
        store = self.make_store()
        payload = {
            "type": "UPDATE_ODDS",
            "odds": {
                "u": [
                    [
                        1,
                        [[0, 1, 0, None, 100.0, "2.000", 0, 0, 0, 0, "O", 0, 100]],
                    ]
                ]
            },
        }
        store.process_line(
            json.dumps(
                {
                    "captured_at": "2026-10-28T05:00:00.000+08:00",
                    "opcode": 1,
                    "payload": json.dumps(payload),
                }
            )
        )
        store.flush_history()

        event = store.history_db.get_event("100")

        self.assertIsNotNone(event)
        self.assertEqual(event["metadataStatus"], "missing")

    def test_rendered_event_details_replace_placeholder_metadata(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        kickoff = now - timedelta(minutes=3)
        store.register_event("100", "", "", "", "")
        store._set_event_live("100", True)

        hidden = store.snapshot("100")
        self.assertEqual(hidden["stats"]["events"], 0)
        self.assertEqual(hidden["events"], [])

        result = store.reconcile_event_presence(
            ["100", "999"],
            now.isoformat(),
            {},
            {
                "100": {
                    "name": "Home VS Away",
                    "home": "Home",
                    "away": "Away",
                    "sportId": "29",
                    "leagueId": "2436",
                    "league": "Test League",
                    "kickoff": int(kickoff.timestamp() * 1000),
                    "scoreHome": "1",
                    "scoreAway": "0",
                    "matchMinute": "3'",
                    "matchPeriod": "1H",
                    "matchStatus": "O",
                }
            },
        )

        self.assertEqual(result["removedRows"], 0)
        event = store.events["100"]
        self.assertEqual(event["name"], "Home VS Away")
        self.assertEqual(event["sportId"], "29")
        self.assertEqual(event["leagueId"], "2436")
        self.assertEqual(event["league"], "Test League")
        self.assertEqual(event["metadataStatus"], "complete")
        self.assertTrue(event_has_display_metadata(event))
        self.assertEqual(store.snapshot("100")["stats"]["events"], 1)

    def test_event_without_valid_kickoff_is_not_rendered(self):
        store = self.make_store()
        store.register_event("100", "Home", "Away", "Test League", "0")
        store._set_event_live("100", True)

        event = store.events["100"]
        self.assertFalse(event_has_display_metadata(event))
        self.assertEqual(store.snapshot("100")["stats"]["events"], 0)
        self.assertEqual(store.snapshot("100")["events"], [])

    def test_presence_removal_requires_two_consecutive_misses(self):
        store = self.make_store()
        # Keep kickoff within the same local day even when the suite runs just
        # before midnight; event presence is intentionally scoped to today.
        now = datetime.now().astimezone().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        kickoff = (now + timedelta(hours=1)).isoformat()
        store.register_event("100", "Home", "Away", "Test League", kickoff)
        store.flush_history()

        first = store.reconcile_event_presence([], now.isoformat())
        self.assertEqual(first["removed"], 0)
        self.assertIn("100", store.events)

        second = store.reconcile_event_presence([], now.isoformat())
        self.assertEqual(second["removed"], 1)
        self.assertEqual(second["removedEventIds"], ["100"])
        self.assertNotIn("100", store.events)
        self.assertNotIn("100", store.odds_by_event)
        self.assertIsNotNone(store.history_db.get_event("100"))

    def test_present_event_is_not_removed(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        kickoff = (now + timedelta(hours=1)).isoformat()
        store.register_event("100", "Home", "Away", "Test League", kickoff)

        for _ in range(3):
            result = store.reconcile_event_presence(["100"], now.isoformat())
            self.assertEqual(result["removed"], 0)

        self.assertIn("100", store.events)
        self.assertEqual(store.presence_misses, {})

    def test_live_event_is_not_removed_by_presence_reconciliation(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        kickoff = (now - timedelta(minutes=20)).isoformat()
        store.register_event("200", "Live Home", "Live Away", "Live League", kickoff)
        store._set_event_live("200", True)

        for _ in range(3):
            result = store.reconcile_event_presence([], now.isoformat())
            self.assertEqual(result["removed"], 0)

        self.assertIn("200", store.events)
        self.assertTrue(store.events["200"]["isLive"])

    def test_withdrawn_market_rows_are_removed_after_two_snapshots(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        captured_at = now.isoformat()
        store.register_event(
            "100",
            "Home",
            "Away",
            "Test League",
            (now + timedelta(hours=1)).isoformat(),
        )
        store._set_event_live("100", True)
        for option, line in (("3", "3.5"), ("4", "3.5"), ("3", "4.5"), ("4", "4.5")):
            store._update_row(
                event_id="100",
                stage="0",
                market="3",
                option=option,
                line=line,
                american="",
                raw="2.000",
                captured_at=captured_at,
                decimal=2.0,
                hong_kong=1.0,
                quoted=True,
                change_source="test",
            )

        current = {
            "100": ["0|3|3|3.5", "0|3|4|3.5"],
        }
        for _ in range(MARKET_ROW_MISSES - 1):
            unchanged = store.reconcile_event_presence(["100"], captured_at, current)
            self.assertEqual(unchanged["removedRows"], 0)
        self.assertIn("0|3|3|4.5", store.odds_by_event["100"])

        fourth = store.reconcile_event_presence(["100"], captured_at, current)
        self.assertEqual(fourth["removedRows"], 2)
        self.assertEqual(
            set(store.odds_by_event["100"]),
            {"0|3|3|3.5", "0|3|4|3.5"},
        )
        self.assertEqual(store.events["100"]["rowCount"], 2)
        self.assertEqual(store.events["100"]["quoteCount"], 2)

    def test_empty_market_snapshot_does_not_remove_rows(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        captured_at = now.isoformat()
        store.register_event(
            "100",
            "Home",
            "Away",
            "Test League",
            (now + timedelta(hours=1)).isoformat(),
        )
        store._update_row(
            event_id="100",
            stage="0",
            market="3",
            option="3",
            line="4.5",
            american="",
            raw="2.000",
            captured_at=captured_at,
            decimal=2.0,
            hong_kong=1.0,
            quoted=True,
            change_source="test",
        )

        for _ in range(3):
            result = store.reconcile_event_presence(
                ["100"],
                captured_at,
                {"100": []},
            )
            self.assertEqual(result["removedRows"], 0)

        self.assertIn("0|3|3|4.5", store.odds_by_event["100"])

    def test_market_alias_compares_kickoff_without_observed_timezone_drift(self):
        store = self.make_store()
        store.register_event(
            "200",
            "Live Home",
            "Live Away",
            "Live League",
            "2026-10-28 07:30:00",
        )
        store._set_event_live("200", True)
        for option, line in (("3", "3.5"), ("4", "3.5"), ("3", "4.5"), ("4", "4.5")):
            store._update_row(
                event_id="200",
                stage="0",
                market="3",
                option=option,
                line=line,
                american="",
                raw="2.000",
                captured_at="2026-10-28T07:30:00.000+08:00",
                decimal=2.0,
                hong_kong=1.0,
                quoted=True,
                change_source="test",
            )

        current = {"201": ["0|3|3|3.5", "0|3|4|3.5"]}
        details = {
            "201": {
                "name": "Live Home VS Live Away",
                "kickoff": 1793143800000,
            }
        }
        observed_at = "2026-10-28T01:35:00+00:00"
        first = store.reconcile_event_presence(
            ["201"], observed_at, current, details
        )
        self.assertEqual(first["matchedMarketEventIds"], ["200"])
        self.assertEqual(first["removedRows"], 0)
        second = store.reconcile_event_presence(
            ["201"], observed_at, current, details
        )
        self.assertEqual(second["removedRows"], 2)
        self.assertEqual(
            set(store.odds_by_event["200"]),
            {"0|3|3|3.5", "0|3|4|3.5"},
        )

    def test_market_alias_prefers_current_live_duplicate(self):
        store = self.make_store()
        for event_id in ("200", "201"):
            store.register_event(
                event_id,
                "Live Home",
                "Live Away",
                "Live League",
                "2026-10-28 07:30:00",
            )
        store._set_event_live("200", True)
        store.events["201"]["lastCapturedAt"] = "2026-10-28T07:25:00.000+08:00"
        store.events["200"]["lastCapturedAt"] = "2026-10-28T09:05:00.000+08:00"
        for option, line in (("3", "3.5"), ("4", "3.5"), ("3", "4.5"), ("4", "4.5")):
            store._update_row(
                event_id="200",
                stage="0",
                market="3",
                option=option,
                line=line,
                american="",
                raw="2.000",
                captured_at="2026-10-28T09:05:00.000+08:00",
                decimal=2.0,
                hong_kong=1.0,
                quoted=True,
                change_source="test",
            )

        current = {"999": ["0|3|3|3.5", "0|3|4|3.5"]}
        details = {
            "999": {
                "name": "Live Home VS Live Away",
                "kickoff": 1793143800000,
            }
        }
        first = store.reconcile_event_presence(
            ["999"],
            "2026-10-28T09:05:10.000+08:00",
            current,
            details,
        )
        self.assertEqual(first["matchedMarketEventIds"], ["200"])
        for second_number in range(2, MARKET_ROW_MISSES):
            unchanged = store.reconcile_event_presence(
                ["999"],
                f"2026-10-28T09:05:{second_number * 10:02d}.000+08:00",
                current,
                details,
            )
            self.assertEqual(unchanged["removedRows"], 0)
        final = store.reconcile_event_presence(
            ["999"],
            "2026-10-28T09:05:50.000+08:00",
            current,
            details,
        )
        self.assertEqual(final["removedRows"], 2)
        self.assertEqual(
            set(store.odds_by_event["200"]),
            {"0|3|3|3.5", "0|3|4|3.5"},
        )

    def test_market_snapshot_alias_matches_live_feed_event(self):
        store = self.make_store()
        now = datetime.now().astimezone().replace(microsecond=0)
        kickoff_at = now - timedelta(minutes=20)
        store.register_event(
            "200",
            "Live Home",
            "Live Away",
            "Live League",
            kickoff_at.isoformat(),
        )
        store._set_event_live("200", True)
        for option, line in (("3", "3.5"), ("4", "3.5"), ("3", "4.5"), ("4", "4.5")):
            store._update_row(
                event_id="200",
                stage="0",
                market="3",
                option=option,
                line=line,
                american="",
                raw="2.000",
                captured_at=now.isoformat(),
                decimal=2.0,
                hong_kong=1.0,
                quoted=True,
                change_source="test",
            )

        current = {
            "201": ["0|3|3|3.5", "0|3|4|3.5"],
        }
        details = {
            "201": {
                "name": "Live Home VS Live Away",
                "kickoff": int(kickoff_at.timestamp() * 1000),
            }
        }
        for _ in range(MARKET_ROW_MISSES - 1):
            unchanged = store.reconcile_event_presence(
                ["201"], now.isoformat(), current, details
            )
            self.assertEqual(unchanged["removedRows"], 0)
        final = store.reconcile_event_presence(
            ["201"], now.isoformat(), current, details
        )
        self.assertEqual(final["removedRows"], 2)
        self.assertEqual(
            set(store.odds_by_event["200"]),
            {"0|3|3|3.5", "0|3|4|3.5"},
        )


class DashboardHomeUiTests(unittest.TestCase):
    @staticmethod
    def _dashboard_static_path(*parts):
        return Path(__file__).resolve().parents[1] / "dashboard" / "static" / Path(*parts)

    def test_dashboard_exposes_only_retained_pages(self):
        html = self._dashboard_static_path("index.html").read_text(encoding="utf-8")
        for page in ("my-events",):
            self.assertIn(f'data-page-panel="{page}"', html)
        for removed in ("home", "crown", "pinnacle", "merge", "settings"):
            self.assertNotIn(f'data-page-panel="{removed}"', html)
            self.assertNotIn(f'id="{removed}Page"', html)
        self.assertNotIn('data-crown-frame', html)

    def test_crown_iframe_and_merge_router_were_removed(self):
        script = "\n".join(
            self._dashboard_static_path("modules", name).read_text(encoding="utf-8")
            for name in ("dom.js", "router.js", "shared/routes.js")
        )
        for removed in ("activeCrownFrame", "syncCrownFrameActivity", "data-crown-frame", "refreshMerge"):
            self.assertNotIn(removed, script)
        self.assertIn('["my-events"]', script)


if __name__ == "__main__":
    unittest.main()
