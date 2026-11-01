import csv
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dashboard_server import HistoryDatabase
from metadata_sync import (
    find_target_page,
    parse_event_payload,
    update_event_database,
    update_events_csv,
)


class _FakePage:
    def __init__(self, url):
        self.url = url


class _FakeContext:
    def __init__(self, pages):
        self.pages = pages


class _FakeBrowser:
    def __init__(self, pages):
        self.contexts = [_FakeContext(pages)]


class MetadataSyncTests(unittest.TestCase):
    def test_find_target_page_prefers_soccer_over_partner(self):
        partner = _FakePage(
            "https://www.ps38ag.com/partner-ui-v2/report-outstanding-v2"
        )
        soccer = _FakePage("https://www.ps3838.com/zh-cn/sports/soccer")
        browser = _FakeBrowser([partner, soccer])

        page = find_target_page(
            browser,
            ["ps3838.com", "ps38ag.com"],
            preferred_path="/sports/soccer",
        )

        self.assertIs(page, soccer)

    def test_find_target_page_accepts_comma_separated_hosts(self):
        soccer = _FakePage("https://www.ps3838.com/zh-cn/sports/soccer")
        browser = _FakeBrowser([soccer])

        page = find_target_page(
            browser,
            "ps3838.com,ps38ag.com",
            preferred_path="/sports/soccer",
        )

        self.assertIs(page, soccer)

    def test_find_target_page_requires_preferred_page_when_set(self):
        partner = _FakePage(
            "https://www.ps38ag.com/partner-ui-v2/report-outstanding-v2"
        )
        browser = _FakeBrowser([partner])

        page = find_target_page(
            browser,
            ["ps3838.com", "ps38ag.com"],
            preferred_path="/sports/soccer",
        )

        self.assertIsNone(page)

    def test_parse_event_payload(self):
        payload = {
            "n": [
                [
                    29,
                    "Soccer",
                    [
                        [
                            10,
                            "Test League",
                            [[100, "Home", "Away", 0, 1790000000000]],
                        ]
                    ],
                ]
            ]
        }

        record = parse_event_payload(payload, "100")

        self.assertIsNotNone(record)
        self.assertEqual(record["name"], "Home VS Away")
        self.assertEqual(record["sportId"], "29")
        self.assertEqual(record["leagueId"], "10")
        self.assertEqual(record["league"], "Test League")
        self.assertEqual(record["metadataStatus"], "complete")
        self.assertIn("2026-09-", record["kickoff"])

    def test_updates_database_and_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database_path = root / "history.sqlite3"
            csv_path = root / "events.csv"
            HistoryDatabase(database_path)
            csv_path.write_text(
                "event_id,来源,体育分类,联赛,主队,客队,开赛时间\n"
                "1,old,Soccer,Old League,Old Home,Old Away,2026-01-01T00:00:00+08:00\n",
                encoding="utf-8-sig",
            )
            config = {"dashboard_db_path": str(database_path)}
            records = [
                {
                    "eventId": "1",
                    "name": "Home VS Away",
                    "home": "Home",
                    "away": "Away",
                    "sportId": "29",
                    "leagueId": "2436",
                    "league": "Test League",
                    "kickoff": "2026-10-28T20:00:00+08:00",
                    "metadataStatus": "complete",
                }
            ]

            self.assertEqual(update_event_database(config, records), 1)
            self.assertEqual(update_events_csv(csv_path, records), 1)

            with sqlite3.connect(database_path) as connection:
                event = connection.execute(
                    "SELECT name, league, sport_id, league_id, kickoff, metadata_status FROM events"
                ).fetchone()
            self.assertEqual(
                event,
                (
                    "Home VS Away", "Test League", "29", "2436",
                    "2026-10-28T20:00:00+08:00", "complete",
                ),
            )
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["来源"], "api")
            self.assertEqual(rows[0]["主队"], "Home")
            self.assertEqual(rows[0]["联赛"], "Test League")
            self.assertEqual(rows[0]["sport_id"], "29")
            self.assertEqual(rows[0]["league_id"], "2436")


if __name__ == "__main__":
    unittest.main()
