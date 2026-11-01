import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from hdzz.common.platform_maintenance import (  # noqa: E402
    classify_maintenance_payload,
    is_platform_maintenance,
    parse_backend_maintenance_response,
    parse_maintenance_window,
    read_platform_state,
    record_platform_business_success,
    write_platform_state,
)


class TestPlatformMaintenance(unittest.TestCase):
    def test_backend_csv_exposes_active_window(self) -> None:
        parsed = parse_backend_maintenance_response(
            "2026-08-27 13:30:00~16:30:00,Y,13:05:00,N"
        )

        self.assertTrue(parsed["active"])
        self.assertGreater(parsed["maintenance_start_at"], 0)
        self.assertGreater(parsed["maintenance_until"], parsed["maintenance_start_at"])

    def test_inactive_backend_csv_does_not_report_maintenance(self) -> None:
        parsed = parse_backend_maintenance_response(
            "2026-08-27 13:30:00~16:30:00,N,13:05:00,N"
        )

        self.assertFalse(parsed["active"])

    def test_hidden_member_template_is_not_a_maintenance_signal(self) -> None:
        html = """
        <div id="div_regular" style="display:none">系统维护</div>
        <span>2019年6月20日13:00:00~18:00:00</span>
        <script>if (postHash["maintain_sw"] == "Y") openMsg();</script>
        """

        parsed = parse_backend_maintenance_response(html)

        self.assertFalse(parsed["active"])
        self.assertEqual(parsed["maintenance_start_at"], 0)

    def test_structured_member_signal_requires_enabled_flag(self) -> None:
        active = classify_maintenance_payload(
            {
                "code": "619",
                "maintain_sw": "Y",
                "maintain_time": "2026-08-27 13:30:00~16:30:00",
            }
        )
        inactive = classify_maintenance_payload(
            {
                "code": "619",
                "maintain_sw": "N",
                "maintain_time": "2019-06-20 13:00:00~18:00:00",
            }
        )

        self.assertTrue(active["active"])
        self.assertFalse(inactive["active"])

    def test_window_parser_supports_end_time_without_date(self) -> None:
        start_at, until = parse_maintenance_window(
            "2026-08-27 13:30:00~16:30:00"
        )

        self.assertEqual(until - start_at, 3 * 3600)

    def test_recovery_requires_two_business_successes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            write_platform_state(
                data_dir,
                {
                    "status": "recovering",
                    "recovery_success_count": 0,
                    "recovery_success_required": 2,
                },
            )

            first = record_platform_business_success(data_dir, kind="backend", now=100, required=2)
            duplicate = record_platform_business_success(data_dir, kind="backend", now=100.5, required=2)
            second = record_platform_business_success(data_dir, kind="member", now=101, required=2)

            self.assertEqual(first["status"], "recovering")
            self.assertEqual(first["recovery_success_count"], 1)
            self.assertEqual(duplicate["status"], "recovering")
            self.assertEqual(duplicate["recovery_success_count"], 1)
            self.assertEqual(second["status"], "online")
            self.assertEqual(second["recovery_success_count"], 2)
            self.assertFalse(is_platform_maintenance(read_platform_state(data_dir)))


if __name__ == "__main__":
    unittest.main()
