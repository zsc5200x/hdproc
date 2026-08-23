import threading
import time
import unittest


class TestFollowXmlCache(unittest.TestCase):
    def test_get_or_fetch_coalesces_concurrent_misses(self) -> None:
        from hdzz.core.follower.game_ctx_cache import XmlResponseCache

        cache = XmlResponseCache(ttl_seconds=1.0)
        calls = []
        barrier = threading.Barrier(2)

        def fetch() -> str:
            calls.append(1)
            time.sleep(0.05)
            return "<serverresponse><code>501</code></serverresponse>"

        outputs = []

        def worker() -> None:
            barrier.wait(timeout=1)
            outputs.append(cache.get_or_fetch("k", 1.0, fetch))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(calls, [1])
        self.assertEqual(outputs, ["<serverresponse><code>501</code></serverresponse>"] * 2)


if __name__ == "__main__":
    unittest.main()
