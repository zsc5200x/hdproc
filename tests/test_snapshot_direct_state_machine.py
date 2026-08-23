import tempfile
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT / "app"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from hdzz.core.follower.models import RetryJob
from hdzz.core.follower.retry_scheduler import retry_job_from_dict, retry_job_to_dict


class SnapshotDirectStateMachineTest(unittest.TestCase):
    def _member(self):
        return {
            "id": "m1", "label": "m1", "base_url": "https://example.invalid",
            "ver": "v", "uid": "u", "cookie": "c",
        }

    def test_unknown_state_survives_scheduler_persistence(self):
        job = RetryJob(
            key="k", order_id="o", target="t", stake=50, order={}, created_at=1,
            unknown_result_started_at=2, unknown_result_deadline_at=122,
            unknown_result_check_count=3, unknown_result_status="UNKNOWN_RESULT",
            unknown_result_last_check_at=10,
        )
        restored = retry_job_from_dict(retry_job_to_dict(job))
        self.assertEqual(restored.unknown_result_status, "UNKNOWN_RESULT")
        self.assertEqual(restored.unknown_result_check_count, 3)

    def test_reconcile_deadline_not_found_is_confirmed_failed_without_resubmit(self):
        from hdzz.core.follower.engine import _reconcile_only_result

        order = {
            "orderId": "o1", "target": "t", "_unknown_result_submitted_at": 1000,
            "_unknown_result_gid": "g1", "_unknown_result_deadline_at": time.time() - 1,
            "_unknown_result_check_count": 0,
        }
        with patch("hdzz.core.follower.engine._get_member_session", return_value=object()), patch(
            "hdzz.core.follower.engine._query_member_recent_wager", return_value={"status": "not_found"}
        ) as reconcile:
            result = _reconcile_only_result(
                data_dir=Path(tempfile.gettempdir()), settings={"unknown_result_reconcile_max_attempts": 5},
                member=self._member(), order=order, stake=50, timeout_s=2,
            )
        reconcile.assert_called_once()
        self.assertEqual(result["reconcile_terminal_status"], "CONFIRMED_FAILED")
        self.assertEqual(result["real_submission"], "reconcile_only")

    def test_reconcile_unavailable_at_deadline_is_still_unknown(self):
        from hdzz.core.follower.engine import _reconcile_only_result

        order = {
            "orderId": "o1", "target": "t", "_unknown_result_submitted_at": 1000,
            "_unknown_result_gid": "g1", "_unknown_result_deadline_at": time.time() - 1,
            "_unknown_result_check_count": 0,
        }
        with patch("hdzz.core.follower.engine._get_member_session", return_value=object()), patch(
            "hdzz.core.follower.engine._query_member_recent_wager", return_value={"status": "unavailable", "error": "timeout"}
        ):
            result = _reconcile_only_result(
                data_dir=Path(tempfile.gettempdir()), settings={"unknown_result_reconcile_max_attempts": 5},
                member=self._member(), order=order, stake=50, timeout_s=2,
            )
        self.assertEqual(result["reconcile_terminal_status"], "STILL_UNKNOWN")
        self.assertFalse(result["reconcile_pending"])


if __name__ == "__main__":
    unittest.main()
