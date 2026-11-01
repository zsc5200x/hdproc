import io
import unittest
from unittest.mock import patch

import crown_proxy


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"{}"):
        self.status = status
        self._headers = headers or [("Content-Type", "application/json")]
        self._body = body

    def getheaders(self):
        return self._headers

    def read(self):
        return self._body


class FakeHandler:
    def __init__(self, path, body=b"", headers=None):
        self.path = path
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []
        self.finished = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        self.finished = True

    def send_error(self, status, message=None):
        self.status = status


class CrownProxyTests(unittest.TestCase):
    def test_accepts_only_crown_page_and_api_paths(self):
        for path in (
            "/crown", "/crown/", "/crown/static/app.css", "/crown/static/app.js",
            "/api/crown", "/api/crown/events",
            "/crown/static/modules/confirmed-markets.js",
        ):
            with self.subTest(path=path):
                self.assertTrue(crown_proxy.is_crown_path(path))
        for path in (
            "/", "/api/snapshot", "/api/crownish/events",
            "/crown/static/secret.txt", "/crown/static/../app.py",
            "/crown/static/modules/%2e%2e/app.js",
            "/crown/static/modules/../../app.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(crown_proxy.is_crown_path(path))

    def test_forwards_get_query_and_response_headers(self):
        handler = FakeHandler("/api/crown/events?compact=1")
        connection = unittest.mock.MagicMock()
        connection.getresponse.return_value = FakeResponse(
            headers=[("Content-Type", "application/json"), ("Cache-Control", "no-store")],
            body=b'{"events":[]}',
        )
        with patch("crown_proxy.http.client.HTTPConnection", return_value=connection) as ctor:
            crown_proxy.proxy_crown(handler, "GET")

        ctor.assert_called_once_with("127.0.0.1", 9223, timeout=20)
        connection.request.assert_called_once_with(
            "GET", "/api/crown/events?compact=1", body=None, headers={"Accept": "*/*"}
        )
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.wfile.getvalue(), b'{"events":[]}')
        self.assertIn(("Content-Type", "application/json"), handler.response_headers)
        self.assertIn(("Content-Length", "13"), handler.response_headers)
        connection.close.assert_called_once_with()

    def test_forwards_post_body_and_content_type(self):
        body = b'{"code":"123456"}'
        handler = FakeHandler(
            "/api/crown/accounts/account-1/otp",
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Cookie": "session=test",
            },
        )
        connection = unittest.mock.MagicMock()
        connection.getresponse.return_value = FakeResponse()
        with patch("crown_proxy.http.client.HTTPConnection", return_value=connection):
            crown_proxy.proxy_crown(handler, "POST")

        connection.request.assert_called_once_with(
            "POST",
            "/api/crown/accounts/account-1/otp",
            body=body,
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                "Cookie": "session=test",
            },
        )

    def test_returns_friendly_503_when_crown_is_down(self):
        handler = FakeHandler("/crown?embed=1")
        connection = unittest.mock.MagicMock()
        connection.getresponse.side_effect = OSError("connection refused")
        with patch("crown_proxy.http.client.HTTPConnection", return_value=connection):
            crown_proxy.proxy_crown(handler, "GET")

        self.assertEqual(handler.status, 503)
        self.assertIn(("Content-Type", "text/html; charset=utf-8"), handler.response_headers)
        body = handler.wfile.getvalue()
        self.assertIn("皇冠服务暂不可用".encode("utf-8"), body)
        self.assertIn(b"location.reload()", body)

    def test_rejects_non_crown_path(self):
        handler = FakeHandler("/api/snapshot")
        with self.assertRaises(ValueError):
            crown_proxy.proxy_crown(handler, "GET")


if __name__ == "__main__":
    unittest.main()
