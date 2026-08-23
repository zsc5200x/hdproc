import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TestHttpTimeoutBudget(unittest.TestCase):
    def test_requests_timeout_without_deadline_uses_requested_timeout(self) -> None:
        from hdzz.common.http import requests_timeout

        self.assertEqual(requests_timeout(10), (10, 10))

    def test_requests_timeout_is_clamped_by_active_hard_timeout_budget(self) -> None:
        from hdzz.common.http import hard_timeout, requests_timeout

        with patch("hdzz.common.http.time.monotonic", return_value=100.0):
            with hard_timeout(3):
                self.assertEqual(requests_timeout(10), (3, 3))

    def test_requests_timeout_raises_when_budget_is_exhausted(self) -> None:
        from hdzz.common.http import HardTimeout, hard_timeout, requests_timeout

        with patch("hdzz.common.http.time.monotonic", return_value=100.0):
            with hard_timeout(3):
                with patch("hdzz.common.http.time.monotonic", return_value=103.1):
                    with self.assertRaises(HardTimeout):
                        requests_timeout(10)

    def test_hard_timeout_does_not_raise_reset_error_after_signal_timeout(self) -> None:
        from hdzz.common.http import HardTimeout, hard_timeout

        class FakeSignal:
            SIGALRM = object()
            ITIMER_REAL = object()

            def __init__(self) -> None:
                self.handler = None
                self.old_handler = object()
                self.old_itimer = (0.0, 0.0)

            def getsignal(self, _sig):
                return self.old_handler

            def getitimer(self, _timer):
                return self.old_itimer

            def signal(self, _sig, handler):
                self.handler = handler
                return self.old_handler

            def setitimer(self, _timer, _value, _interval=0.0):
                return None

        fake_signal = FakeSignal()

        with patch("hdzz.common.http.threading.current_thread", return_value=threading.main_thread()):
            with patch("hdzz.common.http.signal", fake_signal):
                with self.assertRaises(HardTimeout):
                    with hard_timeout(3):
                        raise HardTimeout("hard_timeout")

    def test_hard_timeout_propagates_body_exception_without_yielding_twice(self) -> None:
        from hdzz.common.http import hard_timeout

        worker = object()
        with patch("hdzz.common.http.threading.current_thread", return_value=worker):
            with self.assertRaisesRegex(ValueError, "body failed"):
                with hard_timeout(3):
                    raise ValueError("body failed")


if __name__ == "__main__":
    unittest.main()
