import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


class TestTelegramOrderButtons(unittest.TestCase):
    def test_parse_order_callback_data(self) -> None:
        from hdzz.api.telegram.bot import parse_order_callback_data, parse_report_callback_data

        self.assertEqual(parse_order_callback_data("odr|res|23405596788"), ("res", "23405596788"))
        self.assertEqual(parse_order_callback_data("odr|amt|abc234"), ("amt", "234"))
        self.assertIsNone(parse_order_callback_data("odr|unknown|123"))
        self.assertIsNone(parse_order_callback_data("bad|res|123"))
        self.assertEqual(parse_report_callback_data("rpt|yday"), "yday")
        self.assertEqual(parse_report_callback_data("rpt|today"), "today")
        self.assertIsNone(parse_report_callback_data("rpt|bad"))
        self.assertIsNone(parse_report_callback_data("odr|res|123"))

    def test_today_and_yesterday_follow_report(self) -> None:
        from hdzz.api.telegram.bot import build_today_follow_report, report_day_window

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)

            day_start, day_end = report_day_window()
            ts_today = int((day_start + ((day_end - day_start) / 2)).timestamp())
            y_start = day_start - timedelta(days=1)
            y_end = day_end - timedelta(days=1)
            ts_yday = int((y_start + ((y_end - y_start) / 2)).timestamp())

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "10001", "ok": True, "live": True, "stake": 50, "ts": ts_today}),
                        json.dumps({"orderId": "10002", "ok": True, "live": True, "stake": 60, "ts": ts_yday}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hxjk06wc1y.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "10001", "stake": "50", "sides": "A v B"}),
                        json.dumps({"orderId": "10002", "stake": "60", "sides": "C v D"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            today_report = build_today_follow_report(data_dir=data_dir, orders_dir=orders_dir, day_shift=0)
            self.assertIn("今日跟单报表", today_report)
            self.assertIn("10001", today_report)
            self.assertNotIn("10002", today_report)

            yday_report = build_today_follow_report(data_dir=data_dir, orders_dir=orders_dir, day_shift=-1)
            self.assertIn("昨日跟单报表", yday_report)
            self.assertIn("10002", yday_report)
            self.assertNotIn("10001", yday_report)

    def test_today_follow_report_image_model_limits_to_30_rows(self) -> None:
        from hdzz.api.telegram.bot import build_today_follow_report_image_model, report_day_window

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            settlements_dir = data_dir / "settlements"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)
            settlements_dir.mkdir(parents=True, exist_ok=True)

            day_start, day_end = report_day_window()
            ts_today = int((day_start + ((day_end - day_start) / 2)).timestamp())
            follow_rows = []
            order_rows = []
            settlement_rows = []
            for i in range(31):
                oid = str(10000 + i)
                ticket_id = str(90000 + i)
                follow_rows.append(
                    {
                        "orderId": oid,
                        "ok": True,
                        "live": True,
                        "stake": 100 + i,
                        "ticket_id": ticket_id,
                        "ts": ts_today + i,
                        "params": {"ioratio": "0.85"},
                    }
                )
                order_rows.append(
                    {
                        "orderId": oid,
                        "league": "日本J2",
                        "sides": f"主队{i} v 客队{i} (0-0)",
                        "line": "大 2.5",
                        "odds": "0.85",
                    }
                )
                settlement_rows.append(
                    {
                        "ticket_id": ticket_id,
                        "status": "settled",
                        "profit": 10 if i % 2 == 0 else -5,
                        "ts": ts_today + i,
                    }
                )

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in follow_rows) + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hx.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in order_rows) + "\n",
                encoding="utf-8",
            )
            (settlements_dir / "wagers_today.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in settlement_rows) + "\n",
                encoding="utf-8",
            )

            model = build_today_follow_report_image_model(data_dir=data_dir, orders_dir=orders_dir, day_shift=0)

            self.assertEqual(model["title"], "HD Sport报表")
            self.assertEqual(model["total_rows"], 31)
            self.assertEqual(len(model["rows"]), 30)
            self.assertTrue(model["truncated"])
            self.assertIn("本图仅展示前 30 场", model["footer_note"])
            self.assertEqual(model["stats"]["success_count"], 31)
            self.assertEqual(model["stats"]["settled_count"], 31)

    def test_send_today_follow_report_prefers_photo_and_keeps_inline_keyboard(self) -> None:
        from hdzz.api.telegram.bot import follow_report_inline_keyboard, send_today_follow_report

        with tempfile.TemporaryDirectory() as td:
            image_path = Path(td) / "today.png"
            image_path.write_bytes(b"png")
            with patch("hdzz.api.telegram.bot.build_today_follow_report_image", return_value=image_path), patch(
                "hdzz.api.telegram.bot.tg_send_photo"
            ) as send_photo, patch("hdzz.api.telegram.bot.tg_send_message") as send_message:
                send_today_follow_report(
                    bot_token="token",
                    chat_id="chat",
                    data_dir=Path(td) / "data",
                    orders_dir=Path(td) / "orders",
                    day_shift=0,
                    proxy="",
                )

            send_photo.assert_called_once()
            self.assertEqual(send_photo.call_args.kwargs["reply_markup"], follow_report_inline_keyboard(day_shift=0))
            self.assertEqual(send_photo.call_args.kwargs["photo_path"], image_path)
            send_message.assert_not_called()

    def test_send_today_follow_report_falls_back_to_text_when_image_fails(self) -> None:
        from hdzz.api.telegram.bot import follow_report_inline_keyboard, send_today_follow_report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)
            with patch("hdzz.api.telegram.bot.build_today_follow_report_image", side_effect=RuntimeError("boom")), patch(
                "hdzz.api.telegram.bot.tg_send_photo"
            ) as send_photo, patch("hdzz.api.telegram.bot.tg_send_message") as send_message:
                send_today_follow_report(
                    bot_token="token",
                    chat_id="chat",
                    data_dir=data_dir,
                    orders_dir=orders_dir,
                    day_shift=0,
                    proxy="",
                )

            send_photo.assert_not_called()
            self.assertGreaterEqual(send_message.call_count, 1)
            last_call = send_message.call_args.kwargs
            self.assertIn("今日跟单报表", last_call["html_text"])
            self.assertEqual(last_call["reply_markup"], follow_report_inline_keyboard(day_shift=0))

    def test_roi_button_and_command_aliases(self) -> None:
        from hdzz.api.telegram.bot import BTN_WINRATE, is_winrate_command, main_keyboard

        kb = main_keyboard()
        self.assertIn("投资回报率", BTN_WINRATE)
        self.assertTrue(any("投资回报率" in str(btn.get("text") or "") for row in (kb.get("keyboard") or []) for btn in row))
        self.assertTrue(is_winrate_command("投资回报率"))
        self.assertTrue(is_winrate_command("/roi"))
        self.assertFalse(is_winrate_command("胜率统计"))
        self.assertFalse(is_winrate_command("/stats"))
        self.assertFalse(is_winrate_command("/winrate"))

    def test_node_status_button_and_command_aliases(self) -> None:
        from hdzz.api.telegram.bot import BTN_NODE_STATUS, is_node_status_command, main_keyboard

        kb = main_keyboard()
        self.assertIn("节点状态", BTN_NODE_STATUS)
        self.assertTrue(any("节点状态" in str(btn.get("text") or "") for row in (kb.get("keyboard") or []) for btn in row))
        self.assertTrue(is_node_status_command(BTN_NODE_STATUS))
        self.assertTrue(is_node_status_command("节点状态"))
        self.assertTrue(is_node_status_command("登入3状态"))
        self.assertTrue(is_node_status_command("/node"))
        self.assertFalse(is_node_status_command("余额状态"))
        self.assertFalse(is_node_status_command("/status"))

    def test_build_project_roi_template(self) -> None:
        from hdzz.api.telegram.bot import build_project_winrate_report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            settlements_dir = data_dir / "settlements"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)
            settlements_dir.mkdir(parents=True, exist_ok=True)

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "10001", "ok": True, "stake": 50, "ticket_id": "8881", "ts": 1}),
                        json.dumps({"orderId": "10002", "ok": True, "stake": 50, "ticket_id": "8882", "ts": 2}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (settlements_dir / "wagers_2026-02-13.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ticket_id": "8881", "status": "settled", "profit": 40, "ts": 1}),
                        json.dumps({"ticket_id": "8882", "status": "settled", "profit": -50, "ts": 2}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hx.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "10001", "stake": "100"}),
                        json.dumps({"orderId": "10002", "stake": "100"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            txt = build_project_winrate_report(data_dir=data_dir, orders_dir=orders_dir)
            self.assertIn("👑 HD 跟单数据总揽 👑", txt)
            self.assertIn("投资回报率", txt)
            self.assertIn("全赢 1", txt)
            self.assertIn("全输 1", txt)

    def test_order_amount_and_pnl_reports(self) -> None:
        from hdzz.api.telegram.bot import build_order_amount_report, build_order_pnl_report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "23405596788", "ok": True, "stake": 50, "ticket_id": "8881", "ts": 1}),
                        json.dumps({"orderId": "23405596788", "ok": False, "stake": 50, "ticket_id": "", "ts": 2}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hxjk06wc1y.jsonl").write_text(
                json.dumps({"orderId": "23405596788", "stake": "100.0", "sides": "A v B"}) + "\n",
                encoding="utf-8",
            )

            amount_text = build_order_amount_report(data_dir=data_dir, orders_dir=orders_dir, order_id="23405596788")
            self.assertIn("后台单号：23405596788", amount_text)
            self.assertIn("跟单成功金额：50", amount_text)
            self.assertIn("跟单失败尝试金额：50", amount_text)

            pnl_text = build_order_pnl_report(data_dir=data_dir, order_id="23405596788")
            self.assertIn("状态：未结算", pnl_text)
            self.assertIn("已结算：0 / 未结算：0 / 未匹配：1", pnl_text)

    def test_order_pnl_alert_formats(self) -> None:
        from hdzz.api.telegram.bot import build_order_pnl_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            settlements_dir = data_dir / "settlements"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)
            settlements_dir.mkdir(parents=True, exist_ok=True)

            (data_dir / "follow_results.jsonl").write_text(
                json.dumps(
                    {
                        "orderId": "23409794460",
                        "ok": True,
                        "stake": 50,
                        "ticket_id": "8888",
                        "ts": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hxjk06wc1y.jsonl").write_text(
                json.dumps(
                    {
                        "orderId": "23409794460",
                        "league": "西班牙杯",
                        "sides": "马竞 v 巴萨 (1-0)",
                        "line": "大 4",
                        "odds": "1.01",
                        "stake": "100",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (settlements_dir / "wagers_2026-02-13.jsonl").write_text(
                json.dumps({"ticket_id": "8888", "status": "pending", "profit": None, "ts": 1}) + "\n",
                encoding="utf-8",
            )

            pending_text = build_order_pnl_alert(
                data_dir=data_dir,
                orders_dir=orders_dir,
                order_id="23409794460",
                max_chars=180,
            )
            self.assertIn("⏳ 注单已提交", pending_text)
            self.assertIn("💰 金额：50", pending_text)
            self.assertIn("⌛ 状态：等待结算", pending_text)

            (settlements_dir / "wagers_2026-02-13.jsonl").write_text(
                json.dumps({"ticket_id": "8888", "status": "settled", "profit": 0.5, "ts": 2}) + "\n",
                encoding="utf-8",
            )
            settled_text = build_order_pnl_alert(
                data_dir=data_dir,
                orders_dir=orders_dir,
                order_id="23409794460",
                max_chars=180,
            )
            self.assertIn("✅ 注单结算成功", settled_text)
            self.assertIn("💰 盈利：+0.50", settled_text)

    def test_order_follow_alert_template(self) -> None:
        from hdzz.api.telegram.bot import build_order_follow_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)

            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "orderId": "23409904922",
                                "ok": True,
                                "member": "summer001",
                                "stake": 50,
                                "params": {"ioratio": "1.01"},
                                "ts": 1,
                            }
                        ),
                        json.dumps(
                            {
                                "orderId": "23409904922",
                                "ok": True,
                                "member": "summer008",
                                "stake": 50,
                                "params": {"ioratio": "1.01"},
                                "ts": 2,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hxjk06wc1y.jsonl").write_text(
                json.dumps({"orderId": "23409904922", "stake": "150.0"}) + "\n",
                encoding="utf-8",
            )

            txt = build_order_follow_alert(
                data_dir=data_dir,
                orders_dir=orders_dir,
                order_id="23409904922",
                max_rows=3,
                max_chars=180,
            )
            self.assertIn("📋 跟单详细", txt)
            self.assertIn("🆔 单号：23409904922", txt)
            self.assertIn("💰 原单金额：150.0", txt)
            self.assertIn("✔ summer001", txt)
            self.assertIn("📊 平均水位：1.01", txt)

    def test_order_follow_alert_keep_avg_visible_when_more_than_three_rows(self) -> None:
        from hdzz.api.telegram.bot import build_order_follow_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            orders_dir = root / "orders"
            data_dir.mkdir(parents=True, exist_ok=True)
            orders_dir.mkdir(parents=True, exist_ok=True)

            rows = []
            for idx, member in enumerate(["c8hu555", "c8hu665", "c77hu443", "c66hu441"], start=1):
                rows.append(
                    {
                        "orderId": "23411066152",
                        "ok": True,
                        "member": member,
                        "stake": 3600,
                        "params": {"ioratio": "1.07" if idx < 4 else "1.01"},
                        "ts": idx,
                    }
                )
            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                encoding="utf-8",
            )
            (orders_dir / "hxjk06wc1y.jsonl").write_text(
                json.dumps({"orderId": "23411066152", "stake": "40000"}) + "\n",
                encoding="utf-8",
            )

            txt = build_order_follow_alert(
                data_dir=data_dir,
                orders_dir=orders_dir,
                order_id="23411066152",
                max_rows=3,
                max_chars=180,
            )
            self.assertIn("📊 平均水位：1.05", txt)
            self.assertIn("... 还有1笔", txt)

    def test_order_log_alert_template(self) -> None:
        from hdzz.api.telegram.bot import build_order_log_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            rows = [
                {"orderId": "23409904922", "event": "job_created", "split_count": 3, "ts": 1770929161},
                {"orderId": "23409904922", "event": "attempt_start", "split_index": 1, "split_count": 3, "attempt": 1, "ts": 1770929161},
                {"orderId": "23409904922", "event": "attempt_result", "split_index": 1, "split_count": 3, "ok": True, "member": "summer0010", "message": "分单成功 1/3，下一笔将在 3s 后执行。", "ts": 1770929165},
                {"orderId": "23409904922", "event": "attempt_start", "split_index": 2, "split_count": 3, "attempt": 1, "ts": 1770929170},
                {"orderId": "23409904922", "event": "attempt_result", "split_index": 2, "split_count": 3, "ok": True, "member": "summer008", "message": "分单成功 2/3，下一笔将在 3s 后执行。", "ts": 1770929174},
                {"orderId": "23409904922", "event": "attempt_start", "split_index": 3, "split_count": 3, "attempt": 1, "ts": 1770929179},
                {"orderId": "23409904922", "event": "attempt_result", "split_index": 3, "split_count": 3, "ok": True, "member": "summer009", "ts": 1770929183},
                {"orderId": "23409904922", "event": "job_done", "ts": 1770929185},
            ]
            (data_dir / "follow_trace.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                encoding="utf-8",
            )

            txt = build_order_log_alert(
                data_dir=data_dir,
                order_id="23409904922",
                max_rows=3,
                max_chars=400,
            )
            self.assertIn("🧾 跟单日志", txt)
            self.assertIn("🆔 后台单号：23409904922", txt)
            self.assertIn("📌 1/3", txt)
            self.assertIn("✅ 任务完成", txt)

    def test_order_log_alert_failed_reason(self) -> None:
        from hdzz.api.telegram.bot import build_order_log_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            rows = [
                {"orderId": "23410000001", "event": "job_created", "split_count": 2, "ts": 1770929200},
                {"orderId": "23410000001", "event": "attempt_start", "split_index": 1, "split_count": 2, "attempt": 1, "ts": 1770929201},
                {
                    "orderId": "23410000001",
                    "event": "attempt_result",
                    "split_index": 1,
                    "split_count": 2,
                    "ok": False,
                    "member": "summer009",
                    "message": "会员登录状态异常，无法下注",
                    "ts": 1770929203,
                },
                {
                    "orderId": "23410000001",
                    "event": "job_done",
                    "status": "failed",
                    "reason": "会员登录状态异常，任务停止",
                    "ts": 1770929205,
                },
            ]
            (data_dir / "follow_trace.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                encoding="utf-8",
            )

            txt = build_order_log_alert(
                data_dir=data_dir,
                order_id="23410000001",
                max_rows=3,
                max_chars=400,
            )
            self.assertIn("🧾 跟单日志", txt)
            self.assertIn("❌ 任务已停止", txt)
            self.assertIn("原因：会员登录状态异常，任务停止", txt)
            self.assertIn("📌 1/2", txt)

    def test_order_log_alert_fallback_to_follow_state_done(self) -> None:
        from hdzz.api.telegram.bot import build_order_log_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            rows = [
                {"orderId": "23410000002", "event": "job_created", "split_count": 3, "ts": 1770929300},
                {"orderId": "23410000002", "event": "attempt_start", "split_index": 1, "split_count": 3, "attempt": 1, "ts": 1770929301},
                {
                    "orderId": "23410000002",
                    "event": "attempt_result",
                    "split_index": 1,
                    "split_count": 3,
                    "ok": False,
                    "member": "summer008",
                    "message": "单次尝试超时，正在重试。",
                    "ts": 1770929305,
                },
            ]
            (data_dir / "follow_trace.jsonl").write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                encoding="utf-8",
            )
            (data_dir / "follow_state.json").write_text(
                json.dumps(
                    {
                        "done": {
                            "hxjk06wc1y|23410000002": {
                                "status": "failed",
                                "reason": "重试次数已达上限",
                                "ts": 1770929310,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            txt = build_order_log_alert(
                data_dir=data_dir,
                order_id="23410000002",
                max_rows=3,
                max_chars=400,
            )
            self.assertIn("❌ 任务已停止", txt)
            self.assertIn("原因：重试次数已达上限", txt)
            self.assertNotIn("⏳ 任务进行中", txt)

    def test_follow_summary_alert_template(self) -> None:
        from hdzz.api.telegram.bot import build_follow_summary_alert

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            settlements_dir = data_dir / "settlements"
            data_dir.mkdir(parents=True, exist_ok=True)
            settlements_dir.mkdir(parents=True, exist_ok=True)

            now_ts = int(time.time())
            (data_dir / "follow_results.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"orderId": "A1001", "ok": True, "ticket_id": "t1", "ts": now_ts}),
                        json.dumps({"orderId": "A1002", "ok": True, "ticket_id": "t2", "ts": now_ts}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (settlements_dir / "wagers_now.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"ticket_id": "t1", "status": "settled", "profit": 1.2, "ts": now_ts}),
                        json.dumps({"ticket_id": "t2", "status": "pending", "profit": None, "ts": now_ts}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            txt = build_follow_summary_alert(data_dir=data_dir, max_chars=180)
            self.assertIn("📉 输赢报表", txt)
            self.assertIn("📅 今日（", txt)
            self.assertIn("🗓 本周累计", txt)
            self.assertIn("🎮 场次：2", txt)

    def test_parse_member_history_report_xml(self) -> None:
        from hdzz.api.telegram.bot import parse_member_history_report_xml

        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <serverresponse>
          <code>607</code>
          <total_gold>1,400</total_gold>
          <total_vgold>311</total_vgold>
          <total_winloss>-89.00</total_winloss>
          <history><date>2026-04-29</date><date_name>4月29日 星期三</date_name><gold>1,100</gold><vgold>100</vgold><winloss>-100.00</winloss></history>
          <history><date>2026-04-28</date><date_name>4月28日 星期二</date_name><gold>300</gold><vgold>211</vgold><winloss>11.00</winloss></history>
          <history><date>2026-04-27</date><date_name>4月27日 星期一</date_name><gold>-</gold><vgold>-</vgold><winloss>-</winloss></history>
        </serverresponse>"""

        report = parse_member_history_report_xml(xml)

        self.assertEqual(report["total_gold"], 1400.0)
        self.assertEqual(report["total_vgold"], 311.0)
        self.assertEqual(report["total_winloss"], -89.0)
        self.assertEqual(len(report["rows"]), 3)
        self.assertEqual(report["rows"][0]["date"], "2026-04-29")
        self.assertEqual(report["rows"][0]["gold"], 1100.0)
        self.assertEqual(report["rows"][2]["gold"], 0.0)

    def test_remote_member_pnl_report_sums_online_members(self) -> None:
        from hdzz.api.telegram.bot import BJ_TZ, build_remote_member_pnl_report_message

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "arsenal.json").write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "label": "A",
                                "base_url": "https://a.example",
                                "ver": "v1",
                                "uid": "uid-a",
                                "cookie": "cookie-a",
                                "status": "online",
                            },
                            {
                                "label": "B",
                                "base_url": "https://b.example",
                                "ver": "v1",
                                "uid": "uid-b",
                                "cookie": "cookie-b",
                                "status": "online",
                            },
                            {
                                "label": "C",
                                "base_url": "https://c.example",
                                "ver": "v1",
                                "uid": "uid-c",
                                "cookie": "cookie-c",
                                "status": "offline",
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            def fake_fetch(*, member, startdate, enddate, timeout_s=15):
                label = member["label"]
                self.assertEqual(startdate, "2026-04-27")
                self.assertEqual(enddate, "2026-04-29")
                if label == "A":
                    return {
                        "ok": True,
                        "total_gold": 500.0,
                        "total_vgold": 450.0,
                        "total_winloss": 120.0,
                        "rows": [
                            {"date": "2026-04-29", "gold": 100.0, "vgold": 100.0, "winloss": 80.0, "has_data": True},
                            {"date": "2026-04-28", "gold": 400.0, "vgold": 350.0, "winloss": 40.0, "has_data": True},
                        ],
                    }
                return {
                    "ok": True,
                    "total_gold": 300.0,
                    "total_vgold": 250.0,
                    "total_winloss": -20.0,
                    "rows": [
                        {"date": "2026-04-29", "gold": 200.0, "vgold": 180.0, "winloss": -40.0, "has_data": True},
                        {"date": "2026-04-28", "gold": 100.0, "vgold": 70.0, "winloss": 20.0, "has_data": True},
                    ],
                }

            with patch("hdzz.api.telegram.bot.fetch_member_history_report", side_effect=fake_fetch) as fetch_mock:
                msg = build_remote_member_pnl_report_message(
                    data_dir=data_dir,
                    now=datetime(2026, 4, 30, 8, 0, tzinfo=BJ_TZ),
                    enable_effect=True,
                )
                txt = msg["text"]

            self.assertEqual(fetch_mock.call_count, 2)
            self.assertIn("🗓今日数据（2026-04-29）", txt)
            self.assertIn("💸 账面亏盈：  +40.0", txt)
            self.assertIn("🏦投注金额：  300.0", txt)
            self.assertIn("✅️有效金额：  280.0", txt)
            self.assertIn("🗓本周数据（2026-04-27 ~ 2026-04-29）", txt)
            self.assertIn("🤣账面亏盈：  +100.0", txt)
            self.assertIn("🏦投注金额：  800.0", txt)
            self.assertIn("✅️有效金额：  700.0", txt)
            self.assertIn("⭐️查询会员总数:2", txt)
            self.assertEqual(msg.get("message_effect_id"), "5046509860389126442")
            self.assertEqual(len(msg.get("entities") or []), 8)

    def test_remote_member_pnl_report_falls_back_when_week_end_has_no_data(self) -> None:
        from hdzz.api.telegram.bot import BJ_TZ, build_remote_member_pnl_report

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "arsenal.json").write_text(
                json.dumps(
                    {
                        "members": [
                            {
                                "label": "A",
                                "base_url": "https://a.example",
                                "ver": "v1",
                                "uid": "uid-a",
                                "cookie": "cookie-a",
                                "status": "online",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            calls = []

            def fake_fetch(*, member, startdate, enddate, timeout_s=15):
                calls.append((startdate, enddate))
                if enddate == "2026-04-30":
                    return {"ok": True, "total_gold": 0.0, "total_vgold": 0.0, "total_winloss": 0.0, "rows": []}
                return {
                    "ok": True,
                    "total_gold": 1000.0,
                    "total_vgold": 820.0,
                    "total_winloss": 820.0,
                    "rows": [
                        {"date": "2026-04-29", "gold": 1000.0, "vgold": 820.0, "winloss": 820.0, "has_data": True},
                    ],
                }

            with patch("hdzz.api.telegram.bot.fetch_member_history_report", side_effect=fake_fetch):
                txt = build_remote_member_pnl_report(
                    data_dir=data_dir,
                    now=datetime(2026, 4, 30, 13, 0, tzinfo=BJ_TZ),
                )

            self.assertEqual(calls, [("2026-04-27", "2026-04-30"), ("2026-04-27", "2026-04-29")])
            self.assertIn("🗓今日数据（2026-04-30）", txt)
            self.assertIn("💸 账面亏盈：  0.0", txt)
            self.assertIn("🗓本周数据（2026-04-27 ~ 2026-04-30）", txt)
            self.assertIn("🤣账面亏盈：  +820.0", txt)


if __name__ == "__main__":
    unittest.main()
