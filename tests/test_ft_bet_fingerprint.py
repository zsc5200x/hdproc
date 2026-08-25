from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

import requests


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
TOOLS_DIR = APP_DIR / "tools"
SRC_DIR = ROOT / "src"
for path in (APP_DIR, TOOLS_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


class _Response:
    status_code = 200
    text = "<serverresponse><code>560</code><ticket_id>T-1</ticket_id></serverresponse>"
    headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "Server": "test-server",
        "Via": "test-via",
        "Set-Cookie": "secret=must-not-be-recorded",
    }

    def raise_for_status(self) -> None:
        return None


class FtBetFingerprintTest(TestCase):
    def _member(self) -> dict[str, str]:
        return {
            "id": "member-secret-id",
            "base_url": "https://member.example.test",
            "ver": "ver-secret",
            "uid": "uid-secret",
            "cookie": "login_mid=secret-cookie; other=second-secret",
            "proxy": "",
        }

    def _template(self):
        from hdzz.core.follower.snapshot_direct import SubmitTemplate

        return SubmitTemplate(
            gid="gid-1",
            gtype="FT",
            wtype="ROU",
            rtype="ROUH",
            chose_team="H",
            con="-2",
            ratio="-100",
            ioratio="0.83",
            is_rb="Y",
            f="1R",
            snapshot_revision=1,
            snapshot_fetched_at=1.0,
            line=1.5,
            snapshot_fingerprint="fp",
        )

    def test_member_executor_records_hashed_fingerprint_without_secret_values(self) -> None:
        from hdzz.core.follower.snapshot_direct import MemberExecutor, SUCCESS

        session = requests.Session()
        session.post = Mock(return_value=_Response())  # type: ignore[method-assign]
        session.cookies.set("login_mid", "secret-cookie")
        session.cookies.set("other", "second-secret")

        result = MemberExecutor(session_factory=lambda **_: session).submit(self._member(), self._template(), 50)

        self.assertEqual(result.status, SUCCESS)
        request_fp = result.session_meta["ft_bet_request_fingerprint"]
        response_fp = result.session_meta["ft_bet_response_fingerprint"]
        self.assertEqual(request_fp["payload"]["golds"], "50")
        self.assertEqual(request_fp["payload"]["gid"], "gid-1")
        wire_form = session.post.call_args.kwargs["data"]
        self.assertEqual(wire_form["uid"], "uid-secret")
        self.assertEqual(wire_form["ver"], "ver-secret")
        self.assertEqual(wire_form["p"], "FT_bet")
        self.assertEqual(result.request["uid"], "[REDACTED]")
        self.assertEqual(result.request["ver"], "[REDACTED]")
        self.assertEqual(request_fp["member_id_sha256"], hashlib.sha256(b"member-secret-id").hexdigest())
        self.assertEqual(request_fp["uid_sha256"], hashlib.sha256(b"uid-secret").hexdigest())
        self.assertEqual(request_fp["ver_sha256"], hashlib.sha256(b"ver-secret").hexdigest())
        serialized = json.dumps(result.session_meta, ensure_ascii=False)
        for secret in ("member-secret-id", "uid-secret", "ver-secret", "secret-cookie", "second-secret"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(response_fp["status"], 200)
        self.assertEqual(response_fp["content_type"], "text/xml; charset=UTF-8")
        self.assertEqual(response_fp["server"], "test-server")
        self.assertEqual(response_fp["via"], "test-via")
        self.assertTrue(response_fp["ticket_present"])
        self.assertEqual(response_fp["body_length"], len(_Response.text.encode("utf-8")))
        self.assertEqual(response_fp["body_sha256"], hashlib.sha256(_Response.text.encode()).hexdigest())
        self.assertNotIn("Cookie", request_fp["headers"].get("other_header_names", []))

    def test_persisted_result_keeps_response_hash_but_not_xml_body(self) -> None:
        from hdzz.core.follower.snapshot_direct import MemberExecutor
        from hdzz.core.follower.engine import _snapshot_direct_result

        session = requests.Session()
        session.post = Mock(return_value=_Response())  # type: ignore[method-assign]
        result = MemberExecutor(session_factory=lambda **_: session).submit(self._member(), self._template(), 50)
        row = _snapshot_direct_result(result=result, member=self._member(), order={"orderId": "o1"}, stake=50)

        raw = row["ft_bet_raw_response"]
        self.assertNotIn("response_body", raw)
        self.assertEqual(raw["response_body_length"], len(_Response.text.encode("utf-8")))
        self.assertEqual(raw["response_body_sha256"], hashlib.sha256(_Response.text.encode()).hexdigest())
