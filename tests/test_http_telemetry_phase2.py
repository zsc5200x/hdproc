import json
import queue
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import requests

from hdzz.common.http_telemetry import TelemetryManager


class _Response:
    status_code = 200
    text = '<xml><code>501</code></xml>'
    headers = {"Content-Type": "text/xml", "Content-Length": "28"}
    raw = None


class HttpTelemetryPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = TelemetryManager().configure(Path(self.tmp.name))

    def tearDown(self):
        try:
            self.manager._queue.join()
        except Exception:
            pass
        self.manager.close()
        time.sleep(0.03)
        self.tmp.cleanup()

    def _wait_file(self, name="http_network_telemetry.jsonl"):
        path = Path(self.tmp.name) / name
        for _ in range(30):
            if path.exists() and path.stat().st_size:
                return path
            time.sleep(0.02)
        self.fail(f"telemetry file not written: {path}")

    def test_request_value_and_return_are_observational(self):
        session = requests.Session()
        self.manager.record_session_created(session)
        form = {"p": "FT_order_view", "gid": "g1", "wtype": "R", "rtype": "RH", "chose_team": "H"}
        handle = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php?ver=x", form=form)
        handle.finish(response=_Response())
        path = self._wait_file()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        request = next(row for row in rows if row.get("event") == "http_request")
        self.assertEqual(request["request_type"], "FT_order_view")
        self.assertEqual(request["http_status"], 200)
        self.assertEqual(request["gid"], "g1")
        self.assertEqual(request["response_body_length"], len(_Response.text.encode()))
        self.assertNotIn("cookie", json.dumps(request).lower())
        self.assertEqual(self.manager.stats()["inflight"]["total_transform"], 0)

    def test_success_control_sample_fields_are_derived_without_body_persistence(self):
        class BetResponse:
            status_code = 200
            text = "<xml><code>560</code><ticket_id>12345</ticket_id></xml>"
            headers = {"Content-Type": "text/xml"}
            raw = None

        session = requests.Session()
        handle = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php", form={"p": "FT_bet"})
        handle.finish(response=BetResponse())
        path = self._wait_file()
        row = next(json.loads(line) for line in path.read_text().splitlines() if json.loads(line).get("event") == "http_request")
        self.assertEqual(row["xml_code"], "560")
        self.assertTrue(row["ticket_present"])
        self.assertTrue(row["control"])
        self.assertNotIn("12345", json.dumps(row))

    def test_context_and_bet_share_session_with_monotonic_index(self):
        session = requests.Session()
        self.manager.record_session_created(session)
        for p in ("FT_order_view", "FT_bet"):
            handle = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php", form={"p": p})
            handle.finish(response=_Response())
        path = self._wait_file()
        rows = [json.loads(line) for line in path.read_text().splitlines() if json.loads(line).get("event") == "http_request"]
        self.assertEqual([row["session_request_index"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["session_hash"], rows[1]["session_hash"])

    def test_exception_is_recorded_without_being_raised_by_telemetry(self):
        session = requests.Session()
        handle = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php", form={"p": "FT_bet"})
        exc = requests.exceptions.ReadTimeout("read timed out")
        handle.finish(exception=exc)
        path = self._wait_file()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        request = next(row for row in rows if row.get("event") == "http_request")
        self.assertEqual(request["exception_type"], "ReadTimeout")

    def test_blocked_fingerprint_is_strict(self):
        record = {"http_status": 502, "response_body_length": 301, "response_body_sha256": "5cabae7fe50d472590c91a058986facf6fe866165574b7895047abd6f3638637"}
        self.assertTrue(self.manager._is_blocked_502(record))
        record["response_body_length"] = 302
        self.assertFalse(self.manager._is_blocked_502(record))

    def test_queue_full_is_non_blocking_and_sensitive_values_are_not_recorded(self):
        self.manager._queue = queue.Queue(maxsize=1)
        started = time.monotonic()
        self.manager._enqueue(("record", {"event": "filler"}))
        self.manager._enqueue(("record", {"event": "dropped"}))
        self.assertLess(time.monotonic() - started, 0.2)
        session = requests.Session()
        self.manager.record_session_created(session)
        handle = self.manager.record_request_start(
            session=session,
            url="https://example.invalid/transform.php",
            form={"p": "FT_bet", "uid": "secret-uid", "ver": "secret-ver", "cookie": "secret-cookie"},
        )
        handle.finish(response=_Response())
        path = self._wait_file()
        contents = path.read_text()
        self.assertNotIn("secret-cookie", contents)
        self.assertNotIn("secret-uid", contents)
        self.assertNotIn("secret-token", contents)

    def test_502_incident_contains_ring_history(self):
        session = requests.Session()
        self.manager.record_session_created(session)
        first = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php", form={"p": "FT_order_view"})
        first.finish(response=_Response())
        # Use a synthetic response metadata record; no network request is sent.
        handle = self.manager.record_request_start(session=session, url="https://example.invalid/transform.php", form={"p": "FT_bet"})
        with patch("hdzz.common.http_telemetry.threading.Timer"):
            handle.finish(response_meta={"http_status": 502, "response_body_length": 301, "response_body_sha256": "5cabae7fe50d472590c91a058986facf6fe866165574b7895047abd6f3638637"})
        self.assertTrue(any(item.get("request_type") == "FT_order_view" for item in self.manager._ring))


if __name__ == "__main__":
    unittest.main()
