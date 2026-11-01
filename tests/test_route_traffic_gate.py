import tempfile
import threading
import time
import unittest
import sys
import multiprocessing
import queue
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hdzz.common.route_traffic_gate import BLOCKED_502_BODY_SHA256, RouteTrafficGate


def _acquire_context_in_process(data_dir: str, ready: object) -> None:
    gate = RouteTrafficGate().configure(
        Path(data_dir),
        max_read_inflight=1,
        context_hold_s=0.05,
    )
    permit = gate.acquire(
        base_url="https://member.example",
        request_type="FT_order_view",
        timeout_s=2,
    )
    ready.put(time.monotonic())
    permit.finish(http_status=200)


class RouteTrafficGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.gate = RouteTrafficGate().configure(
            Path(self.tmp.name),
            max_read_inflight=1,
            max_context_inflight=2,
            submit_min_interval_s=0.12,
            context_hold_s=0.05,
            post_submit_hold_s=0.05,
            blocked_502_backoff_s=0.15,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_normal_reads_respect_route_concurrency_limit(self) -> None:
        first = self.gate.acquire(
            base_url="https://member.example",
            request_type="get_game_more",
            timeout_s=2,
        )
        acquired = threading.Event()
        permits = []

        def take_second() -> None:
            permits.append(
                self.gate.acquire(
                    base_url="https://member.example",
                    request_type="sport_menu",
                    timeout_s=2,
                )
            )
            acquired.set()

        worker = threading.Thread(target=take_second)
        worker.start()
        self.assertFalse(acquired.wait(0.04))
        first.finish(http_status=200)
        self.assertTrue(acquired.wait(0.5))
        permits[0].finish(http_status=200)
        worker.join(timeout=0.5)

    def test_context_waiter_drains_reads_and_blocks_new_reads(self) -> None:
        existing_read = self.gate.acquire(
            base_url="https://member.example",
            request_type="get_game_more",
            timeout_s=2,
        )
        context_ready = threading.Event()
        read_ready = threading.Event()
        permits = {}

        def take_context() -> None:
            permits["context"] = self.gate.acquire(
                base_url="https://member.example",
                request_type="FT_order_view",
                timeout_s=2,
            )
            context_ready.set()

        def take_read() -> None:
            permits["read"] = self.gate.acquire(
                base_url="https://member.example",
                request_type="get_game_more",
                timeout_s=2,
            )
            read_ready.set()

        context_worker = threading.Thread(target=take_context)
        context_worker.start()
        time.sleep(0.02)
        read_worker = threading.Thread(target=take_read)
        read_worker.start()
        self.assertFalse(context_ready.wait(0.04))
        self.assertFalse(read_ready.is_set())

        existing_read.finish(http_status=200)
        self.assertTrue(context_ready.wait(0.5))
        self.assertFalse(read_ready.is_set())
        permits["context"].finish(http_status=200)
        self.assertTrue(read_ready.wait(0.5))
        permits["read"].finish(http_status=200)
        context_worker.join(timeout=0.5)
        read_worker.join(timeout=0.5)

    def test_ft_bet_starts_are_spaced_on_same_route(self) -> None:
        first = self.gate.acquire(
            base_url="https://member.example",
            request_type="FT_bet",
            timeout_s=2,
        )
        first.finish(http_status=200)
        started = time.monotonic()
        second = self.gate.acquire(
            base_url="https://member.example",
            request_type="FT_bet",
            timeout_s=2,
        )
        elapsed = time.monotonic() - started
        second.finish(http_status=200)
        self.assertGreaterEqual(elapsed, 0.09)

    def test_blocked_502_pauses_following_submit(self) -> None:
        read = self.gate.acquire(
            base_url="https://member.example",
            request_type="get_game_more",
            timeout_s=2,
        )
        read.finish(
            http_status=502,
            response_body_length=301,
            response_body_sha256=BLOCKED_502_BODY_SHA256,
        )
        started = time.monotonic()
        submit = self.gate.acquire(
            base_url="https://member.example",
            request_type="FT_bet",
            timeout_s=2,
        )
        elapsed = time.monotonic() - started
        submit.finish(http_status=200)
        self.assertGreaterEqual(elapsed, 0.12)

    def test_distinct_proxy_routes_do_not_share_submit_interval(self) -> None:
        direct = self.gate.acquire(
            base_url="https://member.example",
            request_type="FT_bet",
            timeout_s=2,
        )
        direct.finish(http_status=200)
        started = time.monotonic()
        proxied = self.gate.acquire(
            base_url="https://member.example",
            request_type="FT_bet",
            proxy="socks5://127.0.0.1:1080",
            timeout_s=2,
        )
        elapsed = time.monotonic() - started
        proxied.finish(http_status=200)
        self.assertLess(elapsed, 0.08)

    def test_context_priority_is_shared_between_processes(self) -> None:
        existing_read = self.gate.acquire(
            base_url="https://member.example",
            request_type="get_game_more",
            timeout_s=2,
        )
        context = multiprocessing.get_context("fork")
        ready = context.Queue()
        worker = context.Process(
            target=_acquire_context_in_process,
            args=(self.tmp.name, ready),
        )
        worker.start()
        with self.assertRaises(queue.Empty):
            ready.get(timeout=0.08)
        existing_read.finish(http_status=200)
        self.assertIsInstance(ready.get(timeout=1.0), float)
        worker.join(timeout=1.0)
        self.assertEqual(worker.exitcode, 0)


if __name__ == "__main__":
    unittest.main()
