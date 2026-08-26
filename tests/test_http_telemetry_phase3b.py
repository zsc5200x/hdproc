import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hdzz.common.http_telemetry import TelemetryManager


BLOCKED_HASH = "5cabae7fe50d472590c91a058986facf6fe866165574b7895047abd6f3638637"


class HttpTelemetryPhase3BTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._incident_threads_before = sum(
            1 for thread in threading.enumerate() if thread.name == "hdzz-http-incident-finalizer"
        )
        self.manager = TelemetryManager().configure(Path(self.tmp.name))

    def tearDown(self):
        try:
            self.manager._queue.join()
        finally:
            self.manager.close()
            # The writer is intentionally a process daemon; drain anything
            # already queued before removing the temporary directory.
            try:
                self.manager._queue.join()
            except Exception:
                pass
            self.tmp.cleanup()

    def _blocked(self, *, host="host", when=None, request_id="req", member="member", request_type="FT_order_view"):
        when = time.time() if when is None else float(when)
        return {
            "event": "http_request",
            "timestamp": when,
            "response_finished_at": when,
            "http_status": 502,
            "response_body_length": 301,
            "response_body_sha256": BLOCKED_HASH,
            "host": host,
            "request_id": request_id,
            "request_type": request_type,
            "member_hash": member,
        }

    def _wait_for_incident(self, timeout=1.5):
        directory = Path(self.tmp.name) / "502_incidents"
        deadline = time.time() + timeout
        while time.time() < deadline:
            files = list(directory.glob("*.json")) if directory.exists() else []
            if files:
                return json.loads(files[0].read_text(encoding="utf-8"))
            time.sleep(0.01)
        self.fail("incident finalizer did not write an incident file")

    def test_single_blocked_502_is_finalized_by_one_worker(self):
        self.manager._ring.append({"timestamp": time.time() - 5, "request_type": "FT_order_view"})
        self.manager._schedule_incident(self._blocked(when=time.time() - 11))
        incident = self._wait_for_incident()
        self.assertEqual(incident["trigger_count"], 1)
        self.assertEqual(incident["affected_request_types"], ["FT_order_view"])
        self.assertTrue(any(row["request_type"] == "FT_order_view" for row in incident["records"]))
        self.assertEqual(
            sum(1 for t in threading.enumerate() if t.name == "hdzz-http-incident-finalizer"),
            self._incident_threads_before + 1,
        )

    def test_same_failure_window_is_merged_without_heap_growth(self):
        now = time.time()
        self.manager._schedule_incident(self._blocked(when=now, request_id="one", member="m1"))
        self.manager._schedule_incident(self._blocked(when=now + 1, request_id="two", member="m2", request_type="get_game_more"))
        with self.manager._incident_cv:
            self.assertEqual(len(self.manager._incident_groups), 1)
            self.assertEqual(len(self.manager._incident_heap), 1)
            group = next(iter(self.manager._incident_groups.values()))
            self.assertEqual(group["trigger_count"], 2)
            self.assertEqual(group["request_types"], {"FT_order_view", "get_game_more"})
            self.assertEqual(group["members"], {"m1", "m2"})

    def test_incident_capacity_is_bounded_and_overflow_is_non_blocking(self):
        manager = TelemetryManager(incident_queue_capacity=3).configure(Path(self.tmp.name) / "bounded")
        try:
            started = time.monotonic()
            for index in range(20):
                manager._schedule_incident(self._blocked(host=f"host-{index}", when=time.time()))
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.2)
            stats = manager.stats()
            self.assertLessEqual(stats["incident_queue_size"], 3)
            self.assertGreaterEqual(stats["dropped_incident_count"], 17)
            self.assertEqual(stats["dropped_incident_reasons"].get("incident_queue_full"), stats["dropped_incident_count"])
        finally:
            manager.close()
            manager._queue.join()

    def test_finalizer_error_is_observational(self):
        with patch.object(self.manager, "_enqueue", side_effect=RuntimeError("telemetry sink unavailable")):
            self.manager._finalize_incident("incident", {"triggered_at": time.time()})
        self.assertGreaterEqual(self.manager.stats()["telemetry_error_count"], 1)

    def test_writer_error_is_counted_without_affecting_caller(self):
        payload = {"incident_id": "incident-writer-error"}
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            self.manager._enqueue(("incident", payload))
            self.manager._queue.join()
        self.assertGreaterEqual(self.manager.stats()["telemetry_error_count"], 1)

    def test_window_uses_trigger_time_not_finalizer_time(self):
        now = time.time()
        self.manager._ring.extend(
            [
                {"timestamp": now - 25, "request_type": "before"},
                {"timestamp": now - 5, "request_type": "trigger"},
                {"timestamp": now + 2, "request_type": "after"},
                {"timestamp": now + 12, "request_type": "outside"},
            ]
        )
        payload = {
            "triggered_at": now - 5,
            "last_triggered_at": now - 5,
            "pre_window_seconds": 30,
            "post_window_seconds": 10,
        }
        self.manager._finalize_incident("window", payload)
        self.manager._queue.join()
        self.assertEqual(
            {row["request_type"] for row in payload["records"]},
            {"before", "trigger", "after"},
        )

    def test_non_blocked_502_does_not_schedule_incident(self):
        record = self._blocked()
        record["response_body_length"] = 302
        self.assertFalse(self.manager._is_blocked_502(record))
        self.manager._finish(record)
        with self.manager._incident_cv:
            self.assertEqual(self.manager._incident_groups, {})


if __name__ == "__main__":
    unittest.main()
