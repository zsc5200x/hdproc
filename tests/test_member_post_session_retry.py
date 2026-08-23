import unittest
from unittest.mock import patch


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if int(self.status_code) >= 400:
            raise RuntimeError(f"http_status:{self.status_code}")


class _FakeAdapter:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeSession:
    def __init__(self, seq):
        self._seq = list(seq or [])
        self.calls = 0
        self.adapters = {"http://": _FakeAdapter(), "https://": _FakeAdapter()}

    def post(self, url, data=None, timeout=None, verify=None, headers=None):  # noqa: ANN001
        self.calls += 1
        idx = self.calls - 1
        item = self._seq[idx] if idx < len(self._seq) else self._seq[-1]
        if isinstance(item, BaseException):
            raise item
        return item


class TestMemberPostSessionRetry(unittest.TestCase):
    def test_transient_non_xml_retries_then_returns_xml(self) -> None:
        from hdzz.core.follower.executor import member_post_session

        sess = _FakeSession(
            [
                _FakeResponse("code_type is null"),
                _FakeResponse("loading High"),
                _FakeResponse("<serverresponse><code>0</code></serverresponse>"),
            ]
        )
        with patch("hdzz.core.follower.executor.time.sleep", return_value=None):
            out = member_post_session(
                session=sess,
                base_url="https://example.test",
                ver="v",
                form={"p": "get_game_list", "uid": "u"},
                timeout_s=3,
            )
        self.assertTrue(str(out).lstrip().startswith("<serverresponse"))
        self.assertEqual(sess.calls, 3)
        self.assertGreaterEqual(sess.adapters["https://"].closed, 1)

    def test_transient_non_xml_returns_last_after_max_attempts(self) -> None:
        from hdzz.core.follower.executor import member_post_session

        sess = _FakeSession([_FakeResponse("code_type is null")] * 3)
        with patch("hdzz.core.follower.executor.time.sleep", return_value=None):
            out = member_post_session(
                session=sess,
                base_url="https://example.test",
                ver="v",
                form={"p": "get_game_list", "uid": "u"},
                timeout_s=3,
            )
        self.assertEqual(str(out).strip(), "code_type is null")
        self.assertEqual(sess.calls, 3)

    def test_non_transient_non_xml_does_not_retry(self) -> None:
        from hdzz.core.follower.executor import member_post_session

        sess = _FakeSession([_FakeResponse("some plain text error")])
        with patch("hdzz.core.follower.executor.time.sleep", return_value=None):
            out = member_post_session(
                session=sess,
                base_url="https://example.test",
                ver="v",
                form={"p": "get_game_list", "uid": "u"},
                timeout_s=3,
            )
        self.assertEqual(str(out).strip(), "some plain text error")
        self.assertEqual(sess.calls, 1)


if __name__ == "__main__":
    unittest.main()

