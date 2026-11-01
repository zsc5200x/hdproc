import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from hdzz.core.follower.models import RetryJob
from hdzz.core.follower.retry_scheduler import RetryScheduler

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _mk_job(*, err: str = "") -> RetryJob:
    return RetryJob(
        key="k1",
        order_id="o1",
        target="t1",
        stake=100.0,
        order={},
        created_at=100.0,
        last_err=err,
    )


class TestFollowRetryPolicy(unittest.TestCase):
    def test_context_structure_change_waits_without_submitting(self) -> None:
        from hdzz.core.follower.engine import is_retryable_follow_result

        for status in ("BET_CONTEXT_MISMATCH", "MARKET_CHANGED_DURING_CONTEXT"):
            result = {
                "ok": False,
                "snapshot_direct_status": status,
                "snapshot_direct_result_status": "WAITING_MARKET",
                "snapshot_direct_submission": False,
                "real_submission": "none",
            }
            self.assertTrue(is_retryable_follow_result(result), status)

    def test_optional_telemetry_float_accepts_a_fallback(self) -> None:
        from hdzz.core.follower.engine import _to_float

        self.assertEqual(_to_float(None, 17.5), 17.5)
        self.assertEqual(_to_float("", 17.5), 17.5)
        self.assertEqual(_to_float("not-a-number", 17.5), 17.5)
        self.assertEqual(_to_float("12.25", 17.5), 12.25)

    def test_network_errors_are_classified_and_backed_off(self) -> None:
        from hdzz.core.follower.engine import extract_err_from_result, retry_delay_for_result

        result = {
            "ok": False,
            "error_type": "HTTPError",
            "exception": "HTTPError('502 Server Error: Bad Gateway')",
            "message": "系统异常（HTTPError），正在重试。",
        }
        self.assertEqual(extract_err_from_result(result), "UPSTREAM_502")
        self.assertGreater(retry_delay_for_result(result, 2), retry_delay_for_result(result, 1))
        self.assertLessEqual(retry_delay_for_result(result, 99), 12.0)

    def test_reconcile_error_keeps_longer_safety_delay(self) -> None:
        from hdzz.core.follower.engine import retry_delay_for_result

        self.assertEqual(
            retry_delay_for_result(
                {"ok": False, "reconcile_pending": True, "message": "结果不确定"},
                1,
            ),
            15.0,
        )

    def test_unknown_result_bypasses_generic_attempt_and_time_caps(self) -> None:
        job = _mk_job(err="UNKNOWN_RESULT")
        job.attempts = 99
        scheduler = RetryScheduler({job.key: job})

        attempts, seconds = scheduler.get_effective_limits(
            job,
            retry_max_attempts=1,
            retry_max_seconds=1.0,
            wait_line_not_found_max=30.0,
        )

        self.assertEqual((attempts, seconds), (0, 0.0))

    def test_dispatch_snapshot_refresh_does_not_reject_network_cooldown_marker(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from hdzz.core.follower import engine

        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            members = [
                {
                    "id": "member-a",
                    "label": "a",
                    "status": "online",
                    "maxcredit": 1000,
                    "base_url": "https://a.example",
                    "username": "a",
                    "password": "a",
                    "ver": "v",
                    "uid": "u",
                    "cookie": "c=1",
                    "session_ready": True,
                    "session_stable": True,
                },
                {
                    "id": "member-b",
                    "label": "b",
                    "status": "online",
                    "maxcredit": 1000,
                    "base_url": "https://b.example",
                    "username": "b",
                    "password": "b",
                    "ver": "v",
                    "uid": "u",
                    "cookie": "c=2",
                    "session_ready": True,
                    "session_stable": True,
                },
            ]
            arsenal = data_dir / "arsenal.json"
            arsenal.write_text(json.dumps({"members": members}), encoding="utf-8")

            candidate_snapshot = engine._load_current_members(data_dir)
            self.assertEqual(candidate_snapshot[0]["id"], "member-a")

            # Simulate the member watcher writing cooldown after candidate
            # selection but before dispatch.
            engine.patch_member(
                arsenal,
                "member-a",
                {"network_health": "cooldown", "network_cooldown_until": engine.now_ts() + 30},
            )
            latest_snapshot = engine._load_current_members(data_dir)
            self.assertFalse(engine._member_has_transient_network_issue(latest_snapshot[0]))
            self.assertTrue(
                engine._member_eligible_for_stake(
                    latest_snapshot[0],
                    stake=50.0,
                    avoid_ids=set(),
                    target="",
                    mappings={},
                )
            )

            self.assertIsNotNone(selected)
            self.assertEqual(selected["id"], "member-b")
            self.assertFalse(engine._member_has_transient_network_issue(latest_snapshot[0]))

    def test_all_cooldown_members_enter_wait_pool(self) -> None:
        from hdzz.core.follower import engine

        now = engine.now_ts()
        members = [
            {
                "id": "a",
                "status": "online",
                "maxcredit": 1000,
                "base_url": "https://a.example",
                "ver": "v", "uid": "u", "cookie": "c=1",
                "network_cooldown_until": now + 30,
            },
            {
                "id": "b",
                "status": "online",
                "maxcredit": 1000,
                "base_url": "https://b.example",
                "ver": "v", "uid": "u", "cookie": "c=2",
                "request_cooldown_until": now + 30,
            },
        ]

        selected = engine.pick_random_member_for_stake(
            members,
            50.0,
            target="",
            mappings={},
        )
        self.assertIsNotNone(selected)

    def test_diagnostic_unknown_result_stays_reconcile_only_retryable(self) -> None:
        from hdzz.core.follower.engine import is_retryable_follow_result

        result = {
            "ok": False,
            "bet_context_mode": "diagnostic_order_view",
            "snapshot_direct_submission": True,
            "snapshot_direct_result_status": "UNKNOWN_RESULT",
            "unknown_result": True,
            "reconcile_pending": True,
            "ft_bet_request": {"p": "FT_bet"},
        }

        self.assertTrue(is_retryable_follow_result(result))

    def test_diagnostic_explicit_market_rejection_is_retryable(self) -> None:
        from hdzz.core.follower.engine import (
            _apply_market_submit_breaker,
            _schedule_explicit_market_rejection_retry,
            is_retryable_follow_result,
        )

        job = _mk_job()
        job.order["_wait_for_snapshot_revision"] = 7
        result = {
            "ok": False,
            "bet_context_mode": "diagnostic_order_view",
            "snapshot_direct_submission": True,
            "snapshot_direct_result_status": "MARKET_ERROR",
            "error_code": "betError9487",
            "ft_bet_request": {"p": "FT_bet", "gid": "g1"},
            "ft_bet_raw_response": {"http_status": 200, "errormsg": "betError9487"},
            "market_row_fingerprint": "fp-1",
        }

        self.assertFalse(_apply_market_submit_breaker(job, result, max_attempts=3, retry_delay_seconds=0.5))
        self.assertTrue(result["market_submit_reject_retryable"])
        self.assertTrue(is_retryable_follow_result(result))
        self.assertTrue(_schedule_explicit_market_rejection_retry(job, result, now=200.0))
        self.assertNotIn("_wait_for_snapshot_revision", job.order)
        self.assertEqual(job.order["_market_reject_retry_not_before"], 200.5)
        self.assertEqual(job.next_try_at, 200.5)

    def test_explicit_market_rejection_stops_on_third_identical_rejection(self) -> None:
        from hdzz.core.follower.engine import (
            MARKET_SUBMIT_REJECTED,
            _apply_market_submit_breaker,
            is_retryable_follow_result,
        )

        job = _mk_job()

        def rejection() -> dict:
            return {
                "ok": False,
                "bet_context_mode": "diagnostic_order_view",
                "snapshot_direct_submission": True,
                "snapshot_direct_result_status": "MARKET_ERROR",
                "error_code": "betError9487",
                "ft_bet_request": {"p": "FT_bet", "gid": "g1"},
                "ft_bet_raw_response": {
                    "http_status": 200,
                    "errormsg": "betError9487",
                    "parsed_fields": {},
                },
                "market_row_fingerprint": "fp-1",
            }

        first = rejection()
        self.assertFalse(_apply_market_submit_breaker(job, first, max_attempts=3, retry_delay_seconds=0.5))
        self.assertEqual(first["market_submit_reject_count"], 1)
        self.assertTrue(is_retryable_follow_result(first))

        second = rejection()
        self.assertFalse(_apply_market_submit_breaker(job, second, max_attempts=3, retry_delay_seconds=0.5))
        self.assertEqual(second["market_submit_reject_count"], 2)
        self.assertTrue(is_retryable_follow_result(second))

        third = rejection()
        self.assertTrue(_apply_market_submit_breaker(job, third, max_attempts=3, retry_delay_seconds=0.5))
        self.assertEqual(third["market_submit_reject_count"], 3)
        self.assertEqual(third["snapshot_direct_result_status"], MARKET_SUBMIT_REJECTED)
        self.assertFalse(is_retryable_follow_result(third))

    def test_explicit_market_rejection_change_starts_new_count_window(self) -> None:
        from hdzz.core.follower.engine import _apply_market_submit_breaker

        job = _mk_job()

        def rejection(*, fingerprint: str, code: str) -> dict:
            return {
                "ok": False,
                "snapshot_direct_submission": True,
                "snapshot_direct_result_status": "MARKET_ERROR",
                "error_code": code,
                "ft_bet_request": {"p": "FT_bet", "gid": "g1"},
                "ft_bet_raw_response": {"http_status": 200, "errormsg": code},
                "market_row_fingerprint": fingerprint,
            }

        first = rejection(fingerprint="fp-1", code="betError9487")
        second = rejection(fingerprint="fp-2", code="betError9487")
        third = rejection(fingerprint="fp-2", code="betError000")
        _apply_market_submit_breaker(job, first, max_attempts=3)
        _apply_market_submit_breaker(job, second, max_attempts=3)
        _apply_market_submit_breaker(job, third, max_attempts=3)

        self.assertEqual(first["market_submit_reject_count"], 1)
        self.assertEqual(second["market_submit_reject_count"], 1)
        self.assertEqual(third["market_submit_reject_count"], 1)

    def test_unknown_result_502_never_enters_explicit_rejection_retry(self) -> None:
        from hdzz.core.follower.engine import (
            _apply_market_submit_breaker,
            _schedule_explicit_market_rejection_retry,
            is_retryable_follow_result,
        )

        job = _mk_job()
        result = {
            "ok": False,
            "bet_context_mode": "diagnostic_order_view",
            "snapshot_direct_submission": True,
            "snapshot_direct_result_status": "UNKNOWN_RESULT",
            "error_code": "HTTPError",
            "unknown_result": True,
            "reconcile_pending": True,
            "ft_bet_request": {"p": "FT_bet", "gid": "g1"},
            "ft_bet_raw_response": {
                "http_status": 502,
                "errormsg": "",
                "response_body": "Blocked",
            },
            "market_row_fingerprint": "fp-1",
        }

        self.assertFalse(_apply_market_submit_breaker(job, result, max_attempts=3))
        self.assertNotIn("market_submit_reject_retryable", result)
        self.assertFalse(_schedule_explicit_market_rejection_retry(job, result, now=200.0))
        self.assertTrue(is_retryable_follow_result(result))
        self.assertEqual(job.market_submit_reject_count, 0)

    def test_explicit_rejection_with_nested_ticket_is_not_retryable(self) -> None:
        from hdzz.core.follower.engine import _apply_market_submit_breaker

        job = _mk_job()
        result = {
            "ok": False,
            "snapshot_direct_submission": True,
            "snapshot_direct_result_status": "MARKET_ERROR",
            "error_code": "betError9487",
            "ft_bet_request": {"p": "FT_bet", "gid": "g1"},
            "ft_bet_raw_response": {
                "http_status": 200,
                "errormsg": "betError9487",
                "parsed_fields": {"ticket_id": "ticket-1"},
            },
            "market_row_fingerprint": "fp-1",
        }

        self.assertFalse(_apply_market_submit_breaker(job, result, max_attempts=3))
        self.assertNotIn("market_submit_reject_retryable", result)
        self.assertEqual(job.market_submit_reject_count, 0)

    def test_diagnostic_non_explicit_submitted_failure_does_not_resubmit(self) -> None:
        from hdzz.core.follower.engine import is_retryable_follow_result

        self.assertFalse(is_retryable_follow_result({
            "ok": False,
            "bet_context_mode": "diagnostic_order_view",
            "snapshot_direct_submission": True,
            "snapshot_direct_result_status": "MARKET_ERROR",
        }))

    def test_get_ready_jobs_excludes_inflight_task_keys(self) -> None:
        active_job = _mk_job()
        active_job.key = "active"
        active_job.next_try_at = 100.0
        waiting_job = _mk_job()
        waiting_job.key = "waiting"
        waiting_job.next_try_at = 100.0
        scheduler = RetryScheduler({active_job.key: active_job, waiting_job.key: waiting_job})

        ready = scheduler.get_ready_jobs(now=101.0, exclude_keys={active_job.key})

        self.assertEqual([job.key for job in ready], [waiting_job.key])

    def test_build_independent_split_jobs_creates_persistable_part_tasks(self) -> None:
        from hdzz.core.follower.retry_scheduler import (
            build_independent_split_jobs,
            retry_job_from_dict,
            retry_job_to_dict,
        )

        jobs = build_independent_split_jobs(
            parent_key="source|order-1",
            order_id="order-1",
            target="source",
            total_stake=300.0,
            order={"orderId": "order-1"},
            parts=[100.0, 100.0, 100.0],
            created_at=1000.0,
        )

        self.assertEqual([job.key for job in jobs], [
            "source|order-1#part:0",
            "source|order-1#part:1",
            "source|order-1#part:2",
        ])
        self.assertEqual([job.split_index for job in jobs], [0, 1, 2])
        self.assertTrue(all(job.parent_key == "source|order-1" for job in jobs))
        self.assertTrue(all(job.independent_split for job in jobs))
        self.assertTrue(all(job.next_try_at == 1000.0 for job in jobs))
        self.assertIsNot(jobs[0].order, jobs[1].order)
        for job in jobs[:2]:
            job.order["_unknown_result_gid"] = f"gid-{job.split_index}"
            job.order["_unknown_result_submitted_at"] = 123.0 + job.split_index

        from hdzz.core.follower.engine import _clear_unknown_result_state

        _clear_unknown_result_state(jobs[0])
        self.assertNotIn("_unknown_result_gid", jobs[0].order)
        self.assertEqual(jobs[1].order["_unknown_result_gid"], "gid-1")
        self.assertEqual(jobs[1].order["_unknown_result_submitted_at"], 124.0)

        restored = retry_job_from_dict(retry_job_to_dict(jobs[1]), now_value=2000.0)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.parent_key, "source|order-1")
        self.assertTrue(restored.independent_split)
        self.assertEqual(restored.split_index, 1)

    def test_market_rejection_state_survives_retry_job_persistence(self) -> None:
        from hdzz.core.follower.retry_scheduler import retry_job_from_dict, retry_job_to_dict

        job = _mk_job()
        job.market_submit_reject_fingerprint = "fp-1"
        job.market_submit_reject_code = "betError9487"
        job.market_submit_reject_count = 2

        restored = retry_job_from_dict(retry_job_to_dict(job), now_value=200.0)

        self.assertIsNotNone(restored)
        self.assertEqual(restored.market_submit_reject_fingerprint, "fp-1")
        self.assertEqual(restored.market_submit_reject_code, "betError9487")
        self.assertEqual(restored.market_submit_reject_count, 2)

    def test_aggregate_independent_split_done_waits_and_collects_all_tickets(self) -> None:
        from hdzz.core.follower.retry_scheduler import aggregate_independent_split_done

        done = {
            "source|order-1#part:0": {"status": "ok", "ticket_id": "T1", "ts": 101},
            "source|order-1#part:1": {"status": "ok", "ticket_id": "T2", "ts": 102},
        }
        self.assertIsNone(
            aggregate_independent_split_done(
                parent_key="source|order-1",
                split_count=3,
                done=done,
                active_keys={"source|order-1#part:2"},
                created_at=100.0,
                now=102.0,
            )
        )

        done["source|order-1#part:2"] = {"status": "ok", "ticket_id": "T3", "ts": 103}
        aggregate = aggregate_independent_split_done(
            parent_key="source|order-1",
            split_count=3,
            done=done,
            active_keys=set(),
            created_at=100.0,
            now=103.0,
        )

        self.assertEqual(aggregate["status"], "ok")
        self.assertEqual(aggregate["tickets"], ["T1", "T2", "T3"])
        self.assertEqual(aggregate["ticket_id"], "T3")
        self.assertEqual(aggregate["split_done"], 3)
        self.assertEqual(aggregate["split_total"], 3)

    def test_get_effective_limits_1x001_wait_enabled_disables_generic_limits(self) -> None:
        scheduler = RetryScheduler()
        job = _mk_job(err="1X001")

        got_attempts, got_seconds = scheduler.get_effective_limits(
            job,
            retry_max_attempts=9,
            retry_max_seconds=180.0,
            retry_max_attempts_on_1x001=3,
            retry_max_seconds_on_1x001=60.0,
            wait_1x001_max=300.0,
            wait_line_not_found_max=160.0,
        )

        self.assertEqual(got_attempts, 0)
        self.assertEqual(got_seconds, 0.0)

    def test_snapshot_wait_status_uses_market_wait_window(self) -> None:
        from hdzz.core.follower.models import RetryJob
        from hdzz.core.follower.retry_scheduler import RetryScheduler

        job = RetryJob(
            key="snapshot-job",
            order_id="o1",
            target="t1",
            stake=50,
            order={},
            created_at=100.0,
            last_err="SNAPSHOT_STALE",
        )
        scheduler = RetryScheduler({job.key: job})
        attempts, seconds = scheduler.get_effective_limits(
            job,
            retry_max_attempts=5,
            retry_max_seconds=60,
            wait_line_not_found_max=10,
        )
        self.assertEqual(attempts, 0)
        self.assertEqual(seconds, 0.0)
        state, remain, delay = scheduler.schedule_wait_by_err(
            job,
            err="SNAPSHOT_STALE",
            now=100.0,
            wait_max=10,
            probe_seconds=1,
        )
        self.assertEqual(state, "scheduled")
        self.assertEqual(remain, 10.0)
        self.assertEqual(delay, 1.0)
        self.assertEqual(job.next_try_at, 101.0)

    def test_get_effective_limits_1x001_wait_disabled_uses_1x001_specific_limits(self) -> None:
        scheduler = RetryScheduler()
        job = _mk_job(err="1X001")

        got_attempts, got_seconds = scheduler.get_effective_limits(
            job,
            retry_max_attempts=9,
            retry_max_seconds=180.0,
            retry_max_attempts_on_1x001=3,
            retry_max_seconds_on_1x001=60.0,
            wait_1x001_max=0.0,
            wait_line_not_found_max=160.0,
        )

        self.assertEqual(got_attempts, 3)
        self.assertEqual(got_seconds, 60.0)

    def test_check_wait_timeout_uses_policy_reason(self) -> None:
        scheduler = RetryScheduler()
        job = _mk_job(err="LINE_NOT_FOUND")
        job.line_not_found_wait_deadline_at = 200.0

        reason = scheduler.check_wait_timeout(job, now=200.0)
        self.assertEqual(reason, "目标盘口线等待超时，已停止该订单跟单。")

    def test_schedule_wait_by_err_sets_market_closed_window(self) -> None:
        scheduler = RetryScheduler()
        job = _mk_job(err="1X001")

        status, remain, next_delay = scheduler.schedule_wait_by_err(
            job,
            err="1X001",
            now=100.0,
            wait_max=300.0,
            probe_seconds=1.0,
        )

        self.assertEqual(status, "scheduled")
        self.assertAlmostEqual(remain, 300.0, places=6)
        self.assertAlmostEqual(next_delay, 1.0, places=6)
        self.assertAlmostEqual(job.market_closed_wait_started_at, 100.0, places=6)
        self.assertAlmostEqual(job.market_closed_wait_deadline_at, 400.0, places=6)
        self.assertAlmostEqual(job.next_try_at, 101.0, places=6)

        status, remain, next_delay = scheduler.schedule_wait_by_err(
            job,
            err="1X001",
            now=150.0,
            wait_max=300.0,
            probe_seconds=1.0,
        )

        self.assertEqual(status, "scheduled")
        self.assertAlmostEqual(remain, 250.0, places=6)
        self.assertAlmostEqual(next_delay, 1.0, places=6)
        self.assertAlmostEqual(job.market_closed_wait_started_at, 100.0, places=6)
        self.assertAlmostEqual(job.market_closed_wait_deadline_at, 400.0, places=6)
        self.assertAlmostEqual(job.next_try_at, 151.0, places=6)

    def test_schedule_wait_by_err_unsupported_is_immediate(self) -> None:
        scheduler = RetryScheduler()
        job = _mk_job(err="OTHER")

        status, remain, next_delay = scheduler.schedule_wait_by_err(
            job,
            err="OTHER",
            now=123.0,
            wait_max=300.0,
            probe_seconds=1.0,
        )

        self.assertEqual(status, "unsupported")
        self.assertEqual(remain, 0.0)
        self.assertEqual(next_delay, 0.0)
        self.assertEqual(job.next_try_at, 123.0)

    def test_context_retry_uses_bounded_fast_backoff_without_market_wait(self) -> None:
        from hdzz.core.follower.retry_scheduler import CONTEXT_RETRYABLE

        scheduler = RetryScheduler()
        job = _mk_job(err=CONTEXT_RETRYABLE)
        self.assertEqual(
            scheduler.get_effective_limits(
                job,
                retry_max_attempts=1,
                retry_max_seconds=1,
                wait_line_not_found_max=30,
            ),
            (0, 0.0),
        )
        self.assertEqual(scheduler.schedule_context_retry(job, now=100.0), (1, 0.15, True))
        self.assertEqual(scheduler.schedule_context_retry(job, now=100.0), (2, 0.30, True))
        self.assertEqual(scheduler.schedule_context_retry(job, now=100.0), (3, 0.50, True))
        self.assertEqual(scheduler.schedule_context_retry(job, now=100.0), (4, 1.0, False))

    def test_context_retry_slow_path_stays_out_of_market_wait_window(self) -> None:
        from hdzz.core.follower.retry_scheduler import CONTEXT_RETRYABLE

        scheduler = RetryScheduler()
        job = _mk_job(err=CONTEXT_RETRYABLE)
        job.context_retry_count = 3
        count, delay, fast = scheduler.schedule_context_retry(job, now=200.0, slow_delay=1.25)

        self.assertEqual((count, delay, fast), (4, 1.25, False))
        self.assertEqual(job.next_try_at, 201.25)
        self.assertEqual(job.line_not_found_wait_started_at, 0.0)
        self.assertEqual(job.line_not_found_wait_deadline_at, 0.0)

    def test_context_retry_slow_path_exponentially_backs_off_and_caps(self) -> None:
        from hdzz.core.follower.retry_scheduler import CONTEXT_RETRYABLE

        scheduler = RetryScheduler()
        job = _mk_job(err=CONTEXT_RETRYABLE)
        job.context_retry_count = 3
        delays = [
            scheduler.schedule_context_retry(job, now=200.0)[1]
            for _ in range(7)
        ]

        self.assertEqual(delays[:4], [1.0, 2.0, 4.0, 8.0])
        self.assertEqual(delays[-1], 30.0)

    def test_context_network_error_is_classified_for_member_cooldown(self) -> None:
        from hdzz.core.follower.engine import _context_network_error_class

        self.assertEqual(_context_network_error_class(http_status=502, error="CONTEXT_RETRYABLE"), "UPSTREAM_502")
        self.assertEqual(_context_network_error_class(error="ReadTimeout: read timed out"), "READ_TIMEOUT")

    def test_snapshot_publish_event_before_waiter_is_consumed_once(self) -> None:
        from hdzz.core.follower.engine import _consume_snapshot_publish_wakeup

        job = _mk_job(err="SNAPSHOT_STALE")
        job.next_try_at = 202.0
        events = {
            "live|g1": {
                "event_seq": 7,
                "publish_at": 201.5,
            }
        }

        with patch("hdzz.core.follower.engine._job_active_market_key", return_value="live|g1"):
            self.assertTrue(
                _consume_snapshot_publish_wakeup(
                    job,
                    settings={},
                    latest_events=events,
                    now_value=201.6,
                )
            )
            self.assertEqual(job.next_try_at, 201.6)
            self.assertEqual(job.last_seen_event_seq, 7)
            self.assertEqual(job.trigger_reason, "SNAPSHOT_EVENT")
            self.assertEqual(job.trigger_event_seq, 7)
            self.assertEqual(job.trigger_publish_at, 201.5)
            self.assertEqual(job.trigger_wakeup_at, 201.6)

            self.assertFalse(
                _consume_snapshot_publish_wakeup(
                    job,
                    settings={},
                    latest_events=events,
                    now_value=201.7,
                )
            )
            self.assertEqual(job.trigger_wakeup_at, 201.6)

    def test_member_max_limit_splits_current_part_without_changing_total(self) -> None:
        from hdzz.core.follower.engine import split_job_current_part_on_member_max_limit

        job = _mk_job()
        job.stake = 28000.0
        job.split_stakes = [14000.0, 14000.0]
        job.split_count = 2
        job.split_index = 0
        result = {
            "stake": 14000.0,
            "message": "跟单失败：下注金额超过最大投注（14000 > 13274）",
            "order_view_min": {"gold_gmax": "13274"},
        }

        changed = split_job_current_part_on_member_max_limit(job, result)

        self.assertTrue(changed)
        self.assertEqual(job.split_stakes, [7000.0, 7000.0, 14000.0])
        self.assertEqual(job.split_count, 3)
        self.assertEqual(job.split_index, 0)
        self.assertEqual(job.stake, 28000.0)
        self.assertEqual(sum(job.split_stakes), 28000.0)
        self.assertEqual(result["dynamic_split"]["gold_gmax"], 13274.0)
        self.assertIn("已按会员最大投注自动拆分当前分单", result["message"])

    def test_member_max_limit_can_split_a_second_time(self) -> None:
        from hdzz.core.follower.engine import split_job_current_part_on_member_max_limit

        job = _mk_job()
        job.stake = 14001.0
        job.split_stakes = [7001.0, 7000.0]
        job.split_count = 2
        job.split_index = 0
        result = {
            "stake": 7001.0,
            "message": "跟单失败：下注金额超过最大投注（7001 > 7000）",
            "order_view_min": {"gold_gmax": "7000"},
        }

        changed = split_job_current_part_on_member_max_limit(job, result)

        self.assertTrue(changed)
        self.assertEqual(job.split_stakes, [3501.0, 3500.0, 7000.0])
        self.assertEqual(job.split_count, 3)
        self.assertEqual(sum(job.split_stakes), 14001.0)

    def test_member_max_limit_does_not_split_when_current_part_fits(self) -> None:
        from hdzz.core.follower.engine import split_job_current_part_on_member_max_limit

        job = _mk_job()
        job.stake = 14000.0
        job.split_stakes = [7000.0, 7000.0]
        job.split_count = 2
        job.split_index = 0
        result = {
            "stake": 7000.0,
            "message": "",
            "order_view_min": {"gold_gmax": "13274"},
        }

        changed = split_job_current_part_on_member_max_limit(job, result)

        self.assertFalse(changed)
        self.assertEqual(job.split_stakes, [7000.0, 7000.0])
        self.assertNotIn("dynamic_split", result)


if __name__ == "__main__":
    unittest.main()
