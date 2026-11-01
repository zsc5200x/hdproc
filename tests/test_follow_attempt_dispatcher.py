import threading
import time
import unittest


class TestAttemptDispatcher(unittest.TestCase):
    def test_effective_parallel_attempts_scales_to_split_count(self) -> None:
        from hdzz.core.follower.engine import effective_parallel_attempts

        self.assertEqual(effective_parallel_attempts(2, 10), 10)
        self.assertEqual(effective_parallel_attempts(12, 3), 12)
        self.assertEqual(effective_parallel_attempts(100, 100), 50)

    def test_dispatches_different_members_concurrently_but_serializes_same_member(self) -> None:
        from hdzz.core.follower.attempt_dispatcher import AttemptDispatcher

        dispatcher = AttemptDispatcher(max_workers=2, per_member_limit=1)
        started = []
        release = threading.Event()

        def attempt(label: str) -> str:
            started.append(label)
            release.wait(timeout=2)
            return label

        try:
            first = dispatcher.submit("order-1", "member-a", attempt, "a")
            second = dispatcher.submit("order-2", "member-b", attempt, "b")
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            deadline = time.time() + 1
            while len(started) < 2 and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(set(started), {"a", "b"})

            duplicate = dispatcher.submit("order-3", "member-a", attempt, "a2")
            self.assertIsNone(duplicate)
            release.set()
            self.assertEqual(dispatcher.wait(first), "a")
            self.assertEqual(dispatcher.wait(second), "b")
        finally:
            release.set()
            dispatcher.shutdown()

    def test_allows_two_attempts_for_one_member_but_blocks_third(self) -> None:
        from hdzz.core.follower.attempt_dispatcher import AttemptDispatcher

        dispatcher = AttemptDispatcher(max_workers=3, per_member_limit=2)
        release = threading.Event()

        def attempt() -> None:
            release.wait(timeout=2)

        try:
            first = dispatcher.submit("order-1", "member-a", attempt)
            second = dispatcher.submit("order-2", "member-a", attempt)
            third = dispatcher.submit("order-3", "member-a", attempt)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(third)
            self.assertTrue(dispatcher.is_member_busy("member-a"))
            self.assertEqual(dispatcher.busy_member_ids(), {"member-a"})
            release.set()
            dispatcher.wait(first)
            dispatcher.wait(second)
        finally:
            release.set()
            dispatcher.shutdown()

    def test_busy_member_ids_returns_a_snapshot(self) -> None:
        from hdzz.core.follower.attempt_dispatcher import AttemptDispatcher

        dispatcher = AttemptDispatcher(max_workers=1)
        release = threading.Event()

        def attempt() -> None:
            release.wait(timeout=2)

        try:
            future = dispatcher.submit("order-1", "member-a", attempt)
            self.assertIsNotNone(future)
            self.assertEqual(dispatcher.busy_member_ids(), {"member-a"})
            snapshot = dispatcher.busy_member_ids()
            snapshot.add("member-b")
            self.assertEqual(dispatcher.busy_member_ids(), {"member-a"})
            release.set()
            dispatcher.wait(future)
            self.assertEqual(dispatcher.busy_member_ids(), set())
        finally:
            release.set()
            dispatcher.shutdown()

if __name__ == "__main__":
    unittest.main()
