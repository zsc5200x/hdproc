import unittest


class TestFollowReconcile(unittest.TestCase):
    def test_matches_recent_wager_by_member_game_and_stake(self) -> None:
        from hdzz.core.follower.reconcile import match_recent_wager

        wagers = [
            {
                "ticket_id": "90001",
                "member_id": "m-1",
                "gid": "12345",
                "gold": "1500",
                "ts": 1_000.0,
            }
        ]
        match = match_recent_wager(
            wagers,
            member_id="m-1",
            gid="12345",
            stake=1500,
            submitted_at=1_005.0,
            window_seconds=30,
        )
        self.assertEqual(match["ticket_id"], "90001")

    def test_does_not_match_wrong_member_or_stale_wager(self) -> None:
        from hdzz.core.follower.reconcile import match_recent_wager

        wagers = [{"ticket_id": "90001", "member_id": "m-2", "gid": "12345", "gold": "1500", "ts": 900.0}]
        self.assertIsNone(
            match_recent_wager(
                wagers,
                member_id="m-1",
                gid="12345",
                stake=1500,
                submitted_at=1_005.0,
                window_seconds=30,
            )
        )

    def test_does_not_match_when_expected_member_is_missing_from_history(self) -> None:
        from hdzz.core.follower.reconcile import match_recent_wager

        self.assertIsNone(
            match_recent_wager(
                [{"ticket_id": "90001", "gid": "12345", "gold": "1500", "ts": 1_000.0}],
                member_id="m-1",
                gid="12345",
                stake=1500,
                submitted_at=1_000.0,
            )
        )

    def test_extracts_nested_wagers_and_common_hg_field_names(self) -> None:
        from hdzz.core.follower.reconcile import extract_wagers, match_recent_wager

        payload = {"response": {"wagers": [{"wagerId": "90002", "UID": "m-1", "GID": "12345", "GOLDS": "1500", "wagerTime": "2026-08-20 23:00:00"}]}}
        wagers = extract_wagers(payload)
        self.assertEqual(len(wagers), 1)
        match = match_recent_wager(
            wagers,
            member_id="m-1",
            gid="12345",
            stake=1500,
            submitted_at=__import__("datetime").datetime(2026, 8, 20, 23, 0, 5).timestamp(),
            window_seconds=30,
        )
        self.assertEqual(match["ticket_id"], "90002")

    def test_engine_history_query_confirms_uncertain_submit(self) -> None:
        from unittest.mock import patch
        from hdzz.core.follower.engine import _query_member_recent_wager

        payload = {"response": {"wagers": [{"wagerId": "90003", "UID": "m-1", "GID": "12345", "GOLDS": "1500", "wagerTime": "2026-08-20 23:00:00"}]}}
        with patch("hdzz.core.follower.engine.member_post_session", return_value=__import__("json").dumps(payload)):
            result = _query_member_recent_wager(
                session=object(),
                base_url="https://member.example",
                ver="1",
                uid="u1",
                member_id="m-1",
                gid="12345",
                stake=1500,
                submitted_at=__import__("datetime").datetime(2026, 8, 20, 23, 0, 5).timestamp(),
                timeout_s=5,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["ticket_id"], "90003")


if __name__ == "__main__":
    unittest.main()
