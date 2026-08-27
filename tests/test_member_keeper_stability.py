import unittest
import time
from pathlib import Path
from unittest.mock import patch


class TestMemberKeeperStability(unittest.TestCase):
    def test_platform_maintenance_pauses_member_without_relogin_or_failure_count(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m-maintenance",
                "label": "maintenance-user",
                "base_url": "https://example.test",
                "username": "maintenance-user",
                "password": "p",
                "uid": "u-maintenance",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "request_fail_count": 4,
                "relogin_fail_count": 2,
            }
        )

        with (
            patch("hdzz.core.member.keeper.read_platform_state", return_value={
                "status": "maintenance",
                "maintenance_start_at": 100.0,
                "maintenance_until": 200.0,
                "maintenance_message": "平台系统维护",
            }),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.member_ping_member_data") as ping_mock,
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
            patch("hdzz.core.member.keeper.relogin_member_via_adspower") as ads_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(ping_mock.called)
        self.assertFalse(relogin_mock.called)
        self.assertFalse(ads_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "maintenance")
        self.assertEqual(final_patch.get("request_cooldown_until"), 0)
        self.assertEqual(final_patch.get("relogin_backoff_until"), 0)
        self.assertIn("平台系统维护", str(final_patch.get("last_error") or ""))

    def test_process_members_concurrently_does_not_serialize_slow_relogin(self) -> None:
        from hdzz.core.member import keeper

        members = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
        entered = []

        def slow_process(**kwargs):
            entered.append(kwargs["m"]["id"])
            time.sleep(0.05)

        started = time.monotonic()
        with patch("hdzz.core.member.keeper.process_member", side_effect=slow_process):
            keeper.process_members_concurrently(
                doc_path=Path("/tmp/arsenal.json"),
                members=members,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )
        elapsed = time.monotonic() - started

        self.assertEqual(sorted(entered), ["m1", "m2", "m3"])
        self.assertLess(elapsed, 0.12)

    def test_login_response_listener_is_armed_before_submit(self) -> None:
        from hdzz.core.member import keeper

        events = []

        class ResponseInfo:
            @property
            def value(self):
                events.append("response")
                return "login-response"

        class ResponseContext:
            def __enter__(self):
                events.append("listener-armed")
                return ResponseInfo()

            def __exit__(self, exc_type, exc, traceback):
                return False

        class Page:
            def expect_response(self, predicate, timeout):
                self.predicate = predicate
                self.timeout = timeout
                return ResponseContext()

        page = Page()

        def submit_login() -> None:
            events.append("submit")

        response = keeper._trigger_and_wait_for_response(
            page,
            lambda candidate: candidate == "login-response",
            submit_login,
            timeout_ms=5_000,
        )

        self.assertEqual(response, "login-response")
        self.assertEqual(events, ["listener-armed", "submit", "response"])

    def test_extract_helpers_for_uid_and_ver(self) -> None:
        from hdzz.core.member import keeper

        cookies = [
            {"name": "login_40586994", "value": "x"},
            {"name": "login_40587001", "value": "y"},
            {"name": "foo", "value": "z"},
        ]
        html = '.../transform.php?ver=2026-02-10-newYear_136"...'
        uids = keeper._extract_uid_candidates_from_cookies(cookies)
        vers = keeper._extract_ver_candidates_from_text(html)
        self.assertEqual(uids, ["40586994", "40587001"])
        self.assertEqual(vers, ["2026-02-10-newYear_136"])

    def test_infer_base_url_from_page_urls_prefers_real_site(self) -> None:
        from hdzz.core.member import keeper

        urls = [
            "about:blank",
            "chrome://newtab/",
            "http://127.0.0.1:50325",
            "https://service.adspower.com/welcome",
            "https://123.108.119.168/#/sport",
        ]
        self.assertEqual(keeper._infer_base_url_from_page_urls(urls), "https://123.108.119.168")

    def test_should_navigate_ads_page_only_for_blank_or_local(self) -> None:
        from hdzz.core.member import keeper

        base = "https://mos011.com"
        self.assertTrue(keeper._should_navigate_ads_page("", base))
        self.assertTrue(keeper._should_navigate_ads_page("about:blank", base))
        self.assertTrue(keeper._should_navigate_ads_page("http://127.0.0.1:50325", base))
        self.assertFalse(keeper._should_navigate_ads_page("https://mos011.com/#/login", base))
        self.assertFalse(keeper._should_navigate_ads_page("https://hga038.com/", base))

    def test_defaults_normalize_null_fields(self) -> None:
        from hdzz.core.member import keeper

        out = keeper.ensure_member_defaults(
            {
                "id": "m0",
                "relogin_fail_count": None,
                "relogin_backoff_until": None,
                "request_fail_count": None,
                "request_cooldown_until": None,
                "relogin_headless": None,
                "auto_relogin": None,
            }
        )
        self.assertEqual(out.get("relogin_fail_count"), 0)
        self.assertEqual(out.get("relogin_backoff_until"), 0)
        self.assertEqual(out.get("request_fail_count"), 0)
        self.assertEqual(out.get("request_cooldown_until"), 0)
        self.assertEqual(out.get("relogin_headless"), True)
        self.assertEqual(out.get("auto_relogin"), True)

    def test_defaults_adspower_ping_interval_is_fast(self) -> None:
        from hdzz.core.member import keeper

        out = keeper.ensure_member_defaults({"id": "m0a", "session_provider": "adspower"})
        self.assertEqual(int(out.get("ping_min_interval_seconds") or 0), 5)

    def test_defaults_native_ping_interval_is_fast(self) -> None:
        from hdzz.core.member import keeper

        out = keeper.ensure_member_defaults({"id": "m0n", "session_provider": "native"})
        self.assertEqual(int(out.get("ping_min_interval_seconds") or 0), 5)

    def test_login_form_wait_timeout_respects_relogin_timeout(self) -> None:
        from hdzz.core.member import keeper

        self.assertEqual(keeper._login_form_wait_timeout_ms(5), 15_000)
        self.assertEqual(keeper._login_form_wait_timeout_ms(30), 30_000)

    def test_transient_request_error_keeps_online_within_grace(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1",
                "label": "summer001",
                "base_url": "https://example.test",
                "username": "summer001",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 950,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "request_failed: 502 Server Error: Bad Gateway"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(relogin_mock.called)
        self.assertGreaterEqual(patch_member_mock.call_count, 1)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("request_fail_count"), 1)
        self.assertGreater(float(final_patch.get("request_cooldown_until") or 0), 1000.0)
        self.assertEqual(final_patch.get("network_health"), "cooldown")
        self.assertEqual(final_patch.get("network_last_error"), "UPSTREAM_502")
        self.assertGreater(float(final_patch.get("network_cooldown_until") or 0), 1000.0)
        self.assertIn("network_transient:", str(final_patch.get("last_error") or ""))

    def test_stale_transient_request_does_not_drop_online_before_threshold(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1-stale",
                "label": "summer001-stale",
                "base_url": "https://example.test",
                "username": "summer001-stale",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 100.0,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "request_failed: HTTPSConnectionPool: Read timed out"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("request_fail_count"), 1)
        self.assertEqual(final_patch.get("network_health"), "cooldown")
        self.assertEqual(final_patch.get("network_last_error"), "READ_TIMEOUT")
        self.assertGreater(float(final_patch.get("network_cooldown_until") or 0), 1000.0)
        self.assertIn("network_transient:", str(final_patch.get("last_error") or ""))

    def test_stale_transient_request_keeps_session_after_threshold(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1-threshold",
                "label": "summer001-threshold",
                "base_url": "https://example.test",
                "username": "summer001-threshold",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 100.0,
                "request_fail_count": keeper.TRANSIENT_FAILS_BEFORE_RELOGIN - 1,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "request_failed: HTTPSConnectionPool: Read timed out"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertNotIn("uid", final_patch)
        self.assertIn("network_transient:", str(final_patch.get("last_error") or ""))

    def test_transient_outage_keeps_existing_session_after_threshold(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1-outage",
                "label": "summer001-outage",
                "base_url": "https://example.test",
                "username": "summer001-outage",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 100.0,
                "request_fail_count": keeper.TRANSIENT_FAILS_BEFORE_RELOGIN - 1,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "request_failed: 502 Server Error: Bad Gateway"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertNotIn("uid", final_patch)
        self.assertNotIn("cookie", final_patch)
        self.assertIn("network_transient:", str(final_patch.get("last_error") or ""))
        self.assertGreater(float(final_patch.get("request_cooldown_until") or 0), 1000.0)

    def test_server_error_first_time_only_marks_offline_and_no_relogin(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1b",
                "label": "summer001b",
                "base_url": "https://example.test",
                "username": "summer001b",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 100.0,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "server_error"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "offline")
        self.assertEqual(final_patch.get("request_fail_count"), 1)
        self.assertGreater(float(final_patch.get("request_cooldown_until") or 0), 1000.0)

    def test_double_login_error_relogins_immediately(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1c",
                "label": "summer001c",
                "base_url": "https://example.test",
                "username": "summer001c",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "last_ok_at": 100.0,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        rr = keeper.ReloginResult(
            cookie="c=2",
            uid="u2",
            mid="m2",
            ver="v2",
            base_url="https://example.test",
            proxy="",
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "double_login", "data": {"msg": "doubleLogin"}},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright", return_value=rr) as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertTrue(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("uid"), "u2")

    def test_manual_login_request_bypasses_auto_relogin_toggle(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1d",
                "label": "manual-login",
                "base_url": "https://example.test",
                "username": "manual-login",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "relogin",
                "auto_relogin": False,
                "manual_login_requested_at": 999.0,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        rr = keeper.ReloginResult(
            cookie="c=2",
            uid="u2",
            mid="m2",
            ver="v2",
            base_url="https://example.test",
            proxy="",
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright", return_value=rr) as relogin_mock,
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertTrue(relogin_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("uid"), "u2")

    def test_online_headful_auto_switches_back_to_headless(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m1d",
                "label": "summer001d",
                "base_url": "https://example.test",
                "username": "summer001d",
                "password": "p",
                "uid": "u1",
                "cookie": "c=1",
                "ver": "v1",
                "status": "online",
                "relogin_headless": False,
                "headful_online_since": 100.0,
                "auto_switch_back_headless_seconds": 60,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=170.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": True, "systemtime": "t"}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": True, "data": {"currency": "RMB", "cash": "100", "maxcredit": "100"}},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("relogin_headless"), True)
        self.assertEqual(final_patch.get("headful_online_since"), 0)

    def test_relogin_failure_sets_fail_count_and_backoff(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m2",
                "label": "summer002",
                "base_url": "https://example.test",
                "username": "summer002",
                "password": "p",
                "status": "offline",
                "relogin_fail_count": 1,
                "relogin_min_interval_seconds": 15,
            }
        )

        timeout_err = RuntimeError("network_unreachable")
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=2000.0),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright", side_effect=timeout_err),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "offline")
        self.assertEqual(final_patch.get("relogin_fail_count"), 2)
        self.assertEqual(final_patch.get("relogin_backoff_until"), 2030.0)
        self.assertIn("约 30s", str(final_patch.get("last_error") or ""))

    def test_first_login_forces_headful_relogin(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m2b",
                "label": "summer002b",
                "base_url": "https://example.test",
                "username": "summer002b",
                "password": "p",
                "status": "offline",
                "relogin_headless": True,
                "last_ok_at": 0,
            }
        )

        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=2100.0),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright", side_effect=RuntimeError("boom")) as relogin_mock,
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertTrue(relogin_mock.called)
        self.assertEqual(relogin_mock.call_args.kwargs.get("headless"), False)
        first_patch = patch_member_mock.call_args_list[0][0][2]
        self.assertEqual(first_patch.get("relogin_headless"), False)

    def test_relogin_timeout_switches_to_visible_after_threshold(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m3",
                "label": "summer003",
                "base_url": "https://example.test",
                "username": "summer003",
                "password": "p",
                "status": "offline",
                "relogin_headless": True,
                "relogin_fail_count": 2,
                "relogin_min_interval_seconds": 15,
                "last_ok_at": 2500.0,
            }
        )

        timeout_err = RuntimeError('等待登录响应超时/失败：Timeout 120000ms exceeded while waiting for event "response"')
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=3000.0),
            patch("hdzz.core.member.keeper.relogin_member_via_playwright", side_effect=timeout_err),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("relogin_fail_count"), 3)
        self.assertEqual(final_patch.get("relogin_headless"), False)
        self.assertEqual(final_patch.get("relogin_backoff_until"), 3030.0)
        self.assertIn("连续超时", str(final_patch.get("last_error") or ""))

    def test_adspower_member_relogin_path(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m4",
                "label": "ads-01",
                "session_provider": "adspower",
                "adspower_profile_id": "pid-001",
                "adspower_api_base": "http://127.0.0.1:50325",
                "status": "offline",
                "auto_relogin": True,
                "relogin_min_interval_seconds": 5,
            }
        )
        rr = keeper.ReloginResult(
            cookie="a=1",
            uid="u001",
            mid="m001",
            ver="v001",
            base_url="https://member.test",
            proxy="socks5h://1.2.3.4:1080",
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=5000.0),
            patch("hdzz.core.member.keeper.relogin_member_via_adspower", return_value=rr) as ads_mock,
            patch("hdzz.core.member.keeper.relogin_member_via_playwright") as pw_mock,
            patch("hdzz.core.member.keeper.member_ping_member_data", return_value={"ok": True, "data": {}}),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertTrue(ads_mock.called)
        self.assertFalse(pw_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        patch_with_uid = next((c[0][2] for c in patch_member_mock.call_args_list if c[0][2].get("uid") == "u001"), None)
        self.assertIsNotNone(patch_with_uid)
        self.assertEqual((patch_with_uid or {}).get("ver"), "v001")

    def test_adspower_online_member_caps_ping_interval_for_fast_detection(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m4b",
                "label": "ads-fast-ping",
                "session_provider": "adspower",
                "adspower_profile_id": "pid-004b",
                "adspower_api_base": "http://127.0.0.1:50325",
                "status": "online",
                "uid": "u004",
                "cookie": "a=1",
                "ver": "v004",
                "ping_min_interval_seconds": 60,
                "last_check_at": 8994.0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=9000.0),
            patch("hdzz.core.member.keeper.relogin_member_via_adspower") as ads_mock,
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": True, "systemtime": "t"}),
            patch("hdzz.core.member.keeper.member_ping_member_data", return_value={"ok": True, "data": {}}) as ping_mock,
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(ads_mock.called)
        self.assertTrue(ping_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")

    def test_adspower_transient_request_uses_short_cooldown(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m4c",
                "label": "ads-transient",
                "session_provider": "adspower",
                "adspower_profile_id": "pid-004c",
                "adspower_api_base": "http://127.0.0.1:50325",
                "status": "online",
                "uid": "u004c",
                "cookie": "a=1",
                "ver": "v004c",
                "last_ok_at": 995.0,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": False}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "request_failed: 502 Server Error: Bad Gateway"},
            ),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        cooldown_until = float(final_patch.get("request_cooldown_until") or 0.0)
        self.assertLessEqual(cooldown_until, 1003.1)

    def test_adspower_online_member_does_not_force_sync_when_session_valid(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m5",
                "label": "ads-online",
                "session_provider": "adspower",
                "adspower_profile_id": "pid-005",
                "adspower_api_base": "http://127.0.0.1:50325",
                "status": "online",
                "uid": "u005",
                "cookie": "a=1",
                "ver": "v005",
                "adspower_last_sync_at": 0,
                "adspower_sync_interval_seconds": 5,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=8000.0),
            patch("hdzz.core.member.keeper.relogin_member_via_adspower") as ads_mock,
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": True, "systemtime": "t"}),
            patch("hdzz.core.member.keeper.member_ping_member_data", return_value={"ok": True, "data": {}}),
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertFalse(ads_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")

    def test_adspower_double_login_bypasses_backoff_and_relogin_immediately(self) -> None:
        from hdzz.core.member import keeper

        member = keeper.ensure_member_defaults(
            {
                "id": "m6",
                "label": "ads-double-login",
                "session_provider": "adspower",
                "adspower_profile_id": "pid-006",
                "adspower_api_base": "http://127.0.0.1:50325",
                "status": "online",
                "uid": "u006",
                "cookie": "a=1",
                "ver": "v006",
                "relogin_backoff_until": 9999.0,
                "last_relogin_at": 999.0,
                "relogin_min_interval_seconds": 30,
                "ping_min_interval_seconds": 0,
                "systemtime_min_interval_seconds": 0,
            }
        )
        rr = keeper.ReloginResult(
            cookie="a=2",
            uid="u006n",
            mid="m006",
            ver="v006n",
            base_url="https://member.test",
            proxy="",
        )
        with (
            patch("hdzz.core.member.keeper.now_ts", return_value=1000.0),
            patch("hdzz.core.member.keeper.member_ping_system_time", return_value={"ok": True, "systemtime": "t"}),
            patch(
                "hdzz.core.member.keeper.member_ping_member_data",
                return_value={"ok": False, "error": "double_login", "data": {"msg": "doubleLogin"}},
            ),
            patch("hdzz.core.member.keeper.relogin_member_via_adspower", return_value=rr) as ads_mock,
            patch("hdzz.core.member.keeper.patch_member") as patch_member_mock,
            patch("hdzz.core.member.keeper.log_and_maybe_push"),
        ):
            keeper.process_member(
                doc_path=Path("/tmp/arsenal.json"),
                m=member,
                user_data_root=Path("/tmp/_pw_member"),
                ping_timeout_s=8,
                debug=False,
            )

        self.assertTrue(ads_mock.called)
        final_patch = patch_member_mock.call_args_list[-1][0][2]
        self.assertEqual(final_patch.get("status"), "online")
        self.assertEqual(final_patch.get("uid"), "u006n")


if __name__ == "__main__":
    unittest.main()
