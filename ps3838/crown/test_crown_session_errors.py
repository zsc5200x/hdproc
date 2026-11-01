import unittest
from unittest.mock import patch

from crown_local.backend.crown_member import (
    MemberSessionConflict,
    _allow_legacy_ip_certificate,
)
from crown_local.backend.crown_odds import CrownOddsClient, OddsUnavailable, safe_failure
from crown_runtime import _safe_reason


class CrownSessionErrorTests(unittest.TestCase):
    def client(self, response):
        session = {
            "member_origin": "https://m321.mos077.com",
            "member_ui_verified": True,
            "uid": "123456",
            "member_protocol": {
                "origin": "https://m321.mos077.com",
                "ver": "1.0",
                "langx": "zh",
            },
            "member_resume_query": {"ltype": "1", "odd_f_type": "H"},
            "storage_state": {"cookies": [], "origins": []},
        }
        account = {"base_url": "https://m321.mos077.com"}

        def transport(_url, _payload, _headers, _timeout):
            return 200, response

        with patch("crown_local.backend.storage.validate_target_url", return_value=True):
            return CrownOddsClient(account, session, transport=transport, max_retries=0)

    def test_double_login_is_not_reported_as_incomplete_catalog(self):
        client = self.client(
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<serverresponse><code>error</code><msg>doubleLogin</msg></serverresponse>'
        )

        with self.assertRaises(MemberSessionConflict):
            client.list_games("today")

        failure = safe_failure(MemberSessionConflict("safe"))
        self.assertEqual(failure, {
            "reason": "session_conflict",
            "error_type": "MemberSessionConflict",
        })
        self.assertEqual(_safe_reason(MemberSessionConflict("safe")), "session_conflict")

    def test_unknown_provider_error_is_not_reported_as_incomplete_catalog(self):
        client = self.client(
            b'<serverresponse><code>error</code><msg>maintenance</msg></serverresponse>'
        )

        with self.assertRaises(OddsUnavailable) as raised:
            client.list_games("today")

        self.assertEqual(raised.exception.reason, "upstream_response_invalid")

    def test_connection_reset_gets_one_extra_short_retry(self):
        calls = []
        session = {
            "member_origin": "https://m321.mos077.com",
            "member_ui_verified": True,
            "uid": "123456",
            "member_protocol": {
                "origin": "https://m321.mos077.com",
                "ver": "1.0",
                "langx": "zh",
            },
            "member_resume_query": {"ltype": "1", "odd_f_type": "H"},
            "storage_state": {"cookies": [], "origins": []},
        }
        account = {"base_url": "https://m321.mos077.com"}

        def transport(_url, _payload, _headers, _timeout):
            calls.append(1)
            if len(calls) <= 3:
                raise ConnectionResetError("connection reset")
            return 200, b"<serverresponse/>"

        with patch("crown_local.backend.storage.validate_target_url", return_value=True), \
                patch("crown_local.backend.crown_odds.parse_games_xml", return_value={"metadata": {}, "games": []}):
            client = CrownOddsClient(
                account,
                session,
                transport=transport,
                sleep_fn=lambda _delay: None,
            )
            result = client.request_odds({"p": "get_game_list"})

        self.assertEqual(len(calls), 4)
        self.assertEqual(result["_request_attempts"], 4)

    def test_legacy_certificate_is_allowed_only_for_crown_public_ip_pair(self):
        account = {"kind": "crown_odds", "base_url": "https://64.188.47.32"}
        self.assertTrue(_allow_legacy_ip_certificate(account, "https://43.229.3.127"))
        self.assertFalse(_allow_legacy_ip_certificate(account, "https://m321.mos077.com"))
        self.assertFalse(_allow_legacy_ip_certificate(
            {"kind": "crown_odds", "base_url": "https://entry.example.com"},
            "https://43.229.3.127",
        ))
        self.assertFalse(_allow_legacy_ip_certificate(
            {"kind": "crown_odds", "base_url": "https://64.188.47.32"},
            "https://127.0.0.1",
        ))


if __name__ == "__main__":
    unittest.main()
