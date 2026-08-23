import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestChashuiMatchEvents(unittest.TestCase):
    def test_extracts_betradar_feed_token_from_urls_and_html(self) -> None:
        from hdzz.api.webui.server import _gismo_extract_feed_token_from_text

        token = "exp=1770569846~acl=/*~data=abc~hmac=deadbeef"
        direct_url = f"https://ws-fn-cdn001.akamaized.net/188bet/zh/Etc:UTC/gismo/match_info/61513974?T={token}"
        html = f'<script src="/188bet/zh/Etc:UTC/gismo/match_timeline/61513974?foo=1&amp;T={token}&amp;x=2"></script>'

        self.assertEqual(_gismo_extract_feed_token_from_text(direct_url), token)
        self.assertEqual(_gismo_extract_feed_token_from_text(html), token)
        self.assertEqual(_gismo_extract_feed_token_from_text("https://example.test/no-token"), "")

    def test_writes_and_reads_betradar_feed_token(self) -> None:
        from pathlib import Path

        from hdzz.api.webui.server import _gismo_read_feed_token, _gismo_write_feed_token

        token = "exp=1770569846~acl=/*~data=abc~hmac=deadbeef"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch.dict(os.environ, {"BETRADAR_FEED_TOKEN": ""}):
                self.assertEqual(_gismo_write_feed_token(data_dir, token), data_dir / "betradar-feed-token.txt")
                self.assertEqual(_gismo_read_feed_token(data_dir), token)

    def test_remembers_event_match_id_by_gid(self) -> None:
        from pathlib import Path

        from hdzz.api.webui.server import _chashui_lookup_event_match_id, _chashui_remember_event_match_id

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            _chashui_remember_event_match_id(data_dir, gid="8742321", match_id="71059310", mode="live")

            self.assertEqual(_chashui_lookup_event_match_id(data_dir, gid="8742321"), "71059310")

    def test_ignores_invalid_event_match_id_cache_rows(self) -> None:
        from pathlib import Path

        from hdzz.api.webui.server import _chashui_lookup_event_match_id, _chashui_remember_event_match_id

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)

            _chashui_remember_event_match_id(data_dir, gid="8742321", match_id="", mode="live")

            self.assertEqual(_chashui_lookup_event_match_id(data_dir, gid="8742321"), "")

    def test_captures_gismo_token_from_betradar_page_text(self) -> None:
        from pathlib import Path

        from hdzz.api.webui.server import _chashui_capture_gismo_token, _gismo_read_feed_token

        token = "exp=1770569846~acl=/*~data=abc~hmac=deadbeef"
        calls = []

        class FakeResponse:
            url = "https://betradar.hgapp0003.com/transform.php"
            text = f"<html><script>window.feed='/gismo/match_info/61513974?T={token}'</script></html>"

            def raise_for_status(self) -> None:
                return None

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            result = _chashui_capture_gismo_token(
                {"uid": "uid-1", "ver": "ver-1", "username": "user-1"},
                data_dir,
                request_get=fake_get,
                browser_capture=lambda *args, **kwargs: "",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["source"], "http")
            self.assertEqual(_gismo_read_feed_token(data_dir), token)
            self.assertEqual(len(calls), 1)
            self.assertIn("p=getDataMT", calls[0][0])
            self.assertIn("uid=uid-1", calls[0][0])
            self.assertIn("username=user-1", calls[0][0])
            self.assertIn("version=ver-1", calls[0][0])

    def test_captures_gismo_token_with_browser_fallback(self) -> None:
        from pathlib import Path

        from hdzz.api.webui.server import _chashui_capture_gismo_token, _gismo_read_feed_token

        token = "exp=1770569846~acl=/*~data=abc~hmac=deadbeef"

        class FakeResponse:
            url = "https://betradar.hgapp0003.com/transform.php"
            text = "<html></html>"

            def raise_for_status(self) -> None:
                return None

        def fake_get(url, **kwargs):
            return FakeResponse()

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            result = _chashui_capture_gismo_token(
                {"uid": "uid-1", "ver": "ver-1", "username": "user-1"},
                data_dir,
                request_get=fake_get,
                browser_capture=lambda *args, **kwargs: token,
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["source"], "browser")
            self.assertEqual(_gismo_read_feed_token(data_dir), token)

    def test_launches_chromium_with_cache_fallback_when_env_browser_is_stale(self) -> None:
        from hdzz.api.webui.server import _gismo_launch_chromium_with_fallback

        calls = []

        class FakeChromium:
            def launch(self, **kwargs):
                calls.append(dict(kwargs))
                if len(calls) == 1:
                    raise RuntimeError("Executable doesn't exist")
                return "browser"

        with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/tmp/stale-ms-playwright"}):
            browser = _gismo_launch_chromium_with_fallback(FakeChromium(), headless=True)

        self.assertEqual(browser, "browser")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("channel", calls[1])

    def test_launches_headed_capture_window_offscreen_by_default(self) -> None:
        from hdzz.api.webui.server import _gismo_launch_chromium_with_fallback

        class FakeChromium:
            def __init__(self) -> None:
                self.kwargs = None

            def launch(self, **kwargs):
                self.kwargs = dict(kwargs)
                return "browser"

        fake = FakeChromium()
        browser = _gismo_launch_chromium_with_fallback(fake, headless=False)

        self.assertEqual(browser, "browser")
        self.assertFalse(fake.kwargs["headless"])
        self.assertIn("--window-position=-32000,-32000", fake.kwargs["args"])
        self.assertIn("--window-size=1200,900", fake.kwargs["args"])

    def test_builds_betradar_match_widget_post_message(self) -> None:
        from hdzz.api.webui.server import _gismo_build_match_widget_post_message

        message = _gismo_build_match_widget_post_message("70076014", base_url="https://205.201.0.120", timestamp_ms=123456)
        parts = message.split("|", 3)
        payload = json.loads(parts[1])

        self.assertEqual(parts[0], "456")
        self.assertEqual(parts[2], "123456")
        self.assertEqual(parts[3], "false")
        self.assertEqual(payload["Mode"], "Match.lmtPlus")
        self.assertEqual(payload["GameID"], "70076014")
        self.assertEqual(payload["KeyID"], "2370877a0ce04fbfdc9678dae41d4210")
        self.assertEqual(payload["Gtype"], "FT")
        self.assertEqual(payload["SpID"], "1")
        self.assertEqual(payload["TimeZone"], "Etc:UTC")
        self.assertEqual(payload["Cross"], "https://205.201.0.120")

    def test_parse_gismo_timeline_filters_and_sorts_events(self) -> None:
        from hdzz.api.webui.server import _gismo_parse_match_timeline

        payload = {
            "doc": [
                {
                    "event": "match_timeline",
                    "data": {
                        "match": {
                            "_id": "70989620",
                            "result": {"home": 2, "away": 0},
                            "status": {"name": "2nd half"},
                            "teams": {
                                "home": {"name": "依维加杜"},
                                "away": {"name": "老虎"},
                            },
                        },
                        "events": [
                            {"_id": "ignore-possession", "type": "possession", "time": 58, "seconds": 3480},
                            {
                                "_id": "goal-2",
                                "type": "goal",
                                "team": "home",
                                "name": "Goal",
                                "time": 57,
                                "seconds": 3420,
                                "result": {"home": 2, "away": 0},
                            },
                            {
                                "_id": "card-1",
                                "type": "card",
                                "team": "away",
                                "name": "Yellow card",
                                "time": 45,
                                "injurytime": 2,
                                "seconds": 2820,
                                "card": "yellow",
                            },
                        ],
                    },
                }
            ]
        }

        timeline = _gismo_parse_match_timeline(json.dumps(payload), match_id="70989620")

        self.assertEqual(timeline["matchId"], "70989620")
        self.assertEqual(timeline["score"], "2 : 0")
        self.assertEqual(timeline["homeTeam"], "依维加杜")
        self.assertEqual(timeline["awayTeam"], "老虎")
        self.assertEqual([event["id"] for event in timeline["events"]], ["goal-2", "card-1"])
        self.assertEqual(timeline["events"][0]["score"], "2 : 0")
        self.assertEqual(timeline["events"][1]["minuteLabel"], "45+2")


if __name__ == "__main__":
    unittest.main()
