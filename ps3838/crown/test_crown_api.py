import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app


class CrownApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def request(self, method, path, payload=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=3)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        response_headers = dict(response.getheaders())
        raw = response.read()
        connection.close()
        return status, response_headers, raw.decode("utf-8")

    def test_root_redirects_to_crown(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/crown")
        self.assertEqual(body, "")

    def test_session_conflict_retry_is_fast(self):
        self.assertEqual(app._crown_retry_delay({"ok": False, "reason": "session_conflict"}), 5)

    def test_crown_page_has_only_crown_data(self):
        status, headers, body = self.request("GET", "/crown")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store, no-cache, must-revalidate")
        self.assertIn("皇冠赛事", body)
        self.assertIn('href="/crown"', body)
        self.assertRegex(body, r'href="/crown/static/app[.]css(?:[?][^"]*)?"')
        self.assertRegex(body, r'<script type="module" src="/crown/static/app[.]js(?:[?][^"]*)?"')
        self.assertNotIn("crownPassword", body)
        self.assertNotIn("crownTotp", body)
        self.assertNotIn("TOTP 密钥", body)
        for forbidden in ("同场对照", "/combined", "平博联赛", "pinnacleLoginForm"):
            self.assertNotIn(forbidden, body)

    def test_embed_mode_marks_page_for_single_shell_layout(self):
        status, _headers, body = self.request("GET", "/crown?embed=1")
        self.assertEqual(status, 200)
        self.assertIn('<body class="crown-embedded">', body)
        self.assertRegex(body, r'<script type="module" src="/crown/static/app[.]js(?:[?][^"]*)?"')

        status, headers, script = self.request("GET", "/crown/static/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertIn("crown:command", script)

        status, headers, stylesheet = self.request("GET", "/crown/static/app.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertIn("body.crown-embedded .topbar", stylesheet)

    def test_crown_modules_are_served_without_exposing_parent_paths(self):
        status, headers, script = self.request("GET", "/crown/static/modules/confirmed-markets.js")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertIn("createConfirmedMarkets", script)
        for path in ("/crown/static/modules/../../app.py", "/crown/static/modules/%2e%2e/app.js", "/crown/static/modules/missing.js"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404)

    def test_fixtures_mode_hides_duplicate_shell_chrome(self):
        status, _headers, body = self.request("GET", "/crown?embed=1&fixtures=1")
        self.assertEqual(status, 200)
        self.assertIn('<body class="crown-embedded crown-fixtures">', body)

        status, _headers, stylesheet = self.request("GET", "/crown/static/app.css")
        self.assertEqual(status, 200)
        self.assertIn("body.crown-fixtures .topbar-telemetry", stylesheet)
        self.assertIn("body.crown-fixtures .workspace", stylesheet)

    def test_unknown_crown_static_asset_is_rejected(self):
        status, _headers, _body = self.request("GET", "/crown/static/../app.py")
        self.assertEqual(status, 404)

    def test_crown_event_endpoints_remain_available(self):
        for path in ("/api/crown/events", "/api/crown/events?compact=1"):
            with self.subTest(path=path):
                status, _headers, body = self.request("GET", path)
                self.assertEqual(status, 200)
                payload = json.loads(body)
                self.assertIn("events", payload)
                self.assertIn("status", payload)
        status, _headers, body = self.request("GET", "/api/crown/session")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertNotIn("events", payload)
        self.assertNotIn("leagues", payload)
        self.assertIn("cached", payload)

    def test_event_snapshot_exposes_live_extra_market_setting(self):
        with patch.object(app, "extra_markets_enabled", return_value=False):
            status, _headers, body = self.request("GET", "/api/crown/events?compact=1")

        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["extra_markets_enabled"])

    def test_disabled_extra_market_endpoint_returns_success_without_loading(self):
        result = {"status": "disabled", "gid": "123", "counts": {"handicap": 0, "total": 0, "moneyline": 0}}
        with patch.object(app, "refresh_event_more", return_value=result) as refresh:
            status, _headers, body = self.request("POST", "/api/crown/events/123/details")

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "disabled")
        refresh.assert_called_once_with("123")

    def test_combined_pages_and_endpoints_are_removed(self):
        for method, path in (
            ("GET", "/combined"),
            ("GET", "/api/combined"),
            ("POST", "/api/matches/decision"),
        ):
            with self.subTest(method=method, path=path):
                status, _headers, _body = self.request(method, path, {} if method == "POST" else None)
                self.assertEqual(status, 404)

    def test_legacy_matchmaking_and_pinnacle_routes_are_removed(self):
        for path in (
            "/api/events",
            "/api/pinnacle/session",
            "/api/pinnacle/credentials",
        ):
            with self.subTest(method="GET", path=path):
                status, _headers, _body = self.request("GET", path)
                self.assertEqual(status, 404)
        for path in (
            "/api/pinnacle/login",
            "/api/pinnacle/session/check",
            "/api/pinnacle/session/clear",
            "/api/pinnacle/credentials",
            "/api/pinnacle/browser/open",
            "/api/pinnacle/browser/refresh",
        ):
            with self.subTest(method="POST", path=path):
                status, _headers, _body = self.request("POST", path, {})
                self.assertEqual(status, 404)

    def test_otp_submission_route_is_removed(self):
        status, _headers, _body = self.request(
            "POST", "/api/crown/accounts/example/otp", {"code": "123456"}
        )
        self.assertEqual(status, 404)

    def test_account_creation_needs_only_login_url_and_username(self):
        account = {"id": "account", "status": "logging_in"}
        with patch.object(app, "add_account", return_value=account) as add, \
                patch.object(app, "account_action", return_value={**account, "status": "awaiting_manual"}):
            status, _headers, body = self.request(
                "POST", "/api/crown/accounts",
                {"login_url": "https://hga038.com", "username": "member"},
            )

        self.assertEqual(status, 201)
        add.assert_called_once_with("https://hga038.com", "member")
        self.assertEqual(json.loads(body)["account"]["status"], "awaiting_manual")


if __name__ == "__main__":
    unittest.main()
