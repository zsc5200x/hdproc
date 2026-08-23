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

    def test_diagnostic_submitted_failure_does_not_resubmit(self) -> None:
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

        restored = retry_job_from_dict(retry_job_to_dict(jobs[1]), now_value=2000.0)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.parent_key, "source|order-1")
        self.assertTrue(restored.independent_split)
        self.assertEqual(restored.split_index, 1)

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
