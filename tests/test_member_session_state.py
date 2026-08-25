from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from hdzz.core.member.keeper import patch_member, stabilize_browser_session
from hdzz.core.member.session_state import (
    cookie_fingerprint,
    login_cookie_matches_uid,
    session_ready_state,
)


class _FakePage:
    url = "https://www.hga039.com/"

    def __init__(self, context: "_FakeContext") -> None:
        self.context = context

    def wait_for_timeout(self, _ms: int) -> None:
        self.context.advance()


class _FakeContext:
    def __init__(self, samples: list[list[dict[str, str]]]) -> None:
        self.samples = samples
        self.index = 0

    def cookies(self) -> list[dict[str, str]]:
        return self.samples[min(self.index, len(self.samples) - 1)]

    def advance(self) -> None:
        self.index = min(self.index + 1, len(self.samples) - 1)


class _ChangingContext(_FakeContext):
    def __init__(self) -> None:
        super().__init__([_cookies("a")])
        self.counter = 0

    def cookies(self) -> list[dict[str, str]]:
        self.counter += 1
        return _cookies(str(self.counter))


def _cookies(value: str = "a") -> list[dict[str, str]]:
    return [
        {"name": "login_44948299", "value": value},
        {"name": "CookieChk", "value": "Y"},
    ]


def test_session_capture_waits_for_consecutive_stable_cookie_samples() -> None:
    context = _FakeContext([_cookies("a"), _cookies("b"), _cookies("c"), _cookies("c")])
    capture = stabilize_browser_session(
        context,
        _FakePage(context),
        uid="qsscgewdm44948299l305757b0",
        ver="v1",
        timeout_ms=1000,
        interval_ms=1,
        consecutive_samples=2,
    )
    assert capture["session_stable"] is True
    assert capture["cookie_names"] == ["CookieChk", "login_44948299"]
    assert capture["cookie_fingerprint"] == cookie_fingerprint(
        "login_44948299=c; CookieChk=Y"
    )


def test_session_capture_rejects_unstable_cookie_window() -> None:
    context = _ChangingContext()
    try:
        stabilize_browser_session(
            context,
            _FakePage(context),
            uid="qsscgewdm44948299l305757b0",
            ver="v1",
            timeout_ms=2,
            interval_ms=1,
            consecutive_samples=100,
        )
    except RuntimeError as exc:
        assert "SESSION_NOT_STABLE" in str(exc)
    else:
        raise AssertionError("unstable session must not be accepted")


def test_bet_ready_requires_login_cookie_bound_to_uid() -> None:
    member = {
        "status": "online",
        "base_url": "https://www.hga039.com",
        "uid": "qsscgewdm44948299l305757b0",
        "ver": "v1",
        "cookie": "login_45143304=x; CookieChk=Y",
        "session_stable": True,
    }
    assert login_cookie_matches_uid(member) is False
    ready, reason = session_ready_state(member)
    assert ready is False
    assert reason == "BET_SESSION_INCOMPLETE"


def test_bet_ready_requires_all_transport_identity_fields() -> None:
    base = {"status": "online", "session_stable": True}
    for missing in ("base_url", "uid", "ver", "cookie"):
        member = {
            **base,
            "base_url": "https://example.test",
            "uid": "uid-a",
            "ver": "v1",
            "cookie": "SESSION=a",
        }
        member[missing] = ""
        ready, reason = session_ready_state(member)
        assert ready is False
        assert reason == f"SESSION_MISSING_{missing.upper()}"


def test_successful_stable_relogin_advances_generation_even_if_identity_is_same() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "arsenal.json"
        path.write_text(
            json.dumps(
                {
                    "members": [
                        {
                            "id": "member-a",
                            "status": "online",
                            "base_url": "https://example.test",
                            "uid": "uid-a",
                            "ver": "v1",
                            "cookie": "SESSION=a",
                            "session_generation": 4,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        patch_member(
            path,
            "member-a",
            {
                "cookie": "SESSION=a",
                "uid": "uid-a",
                "ver": "v1",
                "session_stable": True,
                "session_ready": True,
                "session_ready_reason": "SESSION_STABLE",
            },
        )
        saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["members"][0]["session_generation"] == 5
