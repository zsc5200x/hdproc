"""用离线 Telegram 响应验证拒绝、重试、持久化和送达确认。"""

import json
from unittest.mock import Mock

import pytest
import requests

from hdzz.core.watcher import ps3838_watch as watch


CFG = {"telegram_bot_token": "123:test-secret", "telegram_chat_id": "test-chat"}
ACCOUNT = {"name": "demo", "enabled": True}


def order_row(oid="3800809347", event="new", ts=100):
    return {
        "orderId": oid, "league": "芬兰 - 全国联赛", "sides": "HJK赫尔辛基-vs-VPS",
        "stake": 150, "currency": "THB", "odds": 2,
        "choices": [{"btName": "HDP", "selection": "HOME", "handicap": "-1.00", "inplay": False}],
        "event": event, "ts": ts, "changes": {},
    }


def http_error(code, description, **parameters):
    response = requests.Response()
    response.status_code = code
    response._content = json.dumps({
        "ok": False, "error_code": code, "description": description, "parameters": parameters,
    }).encode()
    return requests.HTTPError("HTTP failure at https://api.telegram.org/bot123:test-secret/sendMessage", response=response)


def test_rejected_message_keeps_api_description_and_hides_token(monkeypatch, capsys):
    monkeypatch.setattr(watch, "tg_send", Mock(side_effect=http_error(400, "Bad Request: CUSTOM_EMOJI_INVALID")))
    result = watch._send_order_telegram(CFG, ACCOUNT, order_row(), "new", {})
    assert not result["ok"]
    assert result["error_code"] == 400
    assert result["error"] == "Bad Request: CUSTOM_EMOJI_INVALID"
    log = capsys.readouterr().err
    assert "3800809347" in log
    assert "CUSTOM_EMOJI_INVALID" in log
    assert CFG["telegram_bot_token"] not in log


def test_network_error_hides_token_even_without_response(monkeypatch, capsys):
    monkeypatch.setattr(watch, "tg_send", Mock(side_effect=requests.ConnectionError(
        "failed https://api.telegram.org/bot123:test-secret/sendMessage",
    )))
    result = watch._send_order_telegram(CFG, ACCOUNT, order_row(), "new", {})
    assert CFG["telegram_bot_token"] not in result["error"]
    assert CFG["telegram_bot_token"] not in capsys.readouterr().err


def test_group_upgrade_preserves_telegram_migration_destination():
    result = watch._telegram_error_details(
        http_error(400, "Bad Request: group chat was upgraded to a supergroup chat", migrate_to_chat_id=-1001234567890),
        CFG["telegram_bot_token"],
    )
    assert result["error_code"] == 400
    assert result["migrate_to_chat_id"] == -1001234567890


@pytest.mark.parametrize("message_id", [None, 0, -1])
def test_missing_message_id_does_not_mark_delivered(monkeypatch, message_id):
    monkeypatch.setattr(watch, "tg_send", Mock(return_value=message_id))
    assert not watch._send_order_telegram(CFG, ACCOUNT, order_row(), "new", {})["ok"]


def test_pending_survives_restart_and_retries_without_order_changes(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(watch.time, "time", lambda: now[0])
    sender = Mock(side_effect=[http_error(400, "Bad Request: temporary failure"), 8080])
    monkeypatch.setattr(watch, "tg_send", sender)
    state = {"orders": {"3800809347": order_row()}}
    state_path = tmp_path / "state.json"
    receipts = tmp_path / "delivery.jsonl"
    watch._queue_order_telegram(state, order_row())
    watch._queue_order_telegram(state, order_row())  # 同一事件不能重复排队。
    assert len(state["telegram_outbox"]) == 1
    save = lambda: watch.atomic_write_json(state_path, state)
    save()
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, save, receipts)
    state = watch.read_json(state_path, {})  # 模拟退出后从磁盘恢复。
    assert state["telegram_pending_count"] == 1
    assert state["telegram_outbox"][0]["attempts"] == 1
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, save, receipts)
    assert sender.call_count == 1  # 退避期间不能忙重试。
    now[0] = 116
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, save, receipts)
    assert sender.call_count == 2
    assert state["telegram_outbox"] == []
    assert state["telegram_pending_count"] == 0
    assert state["telegram_last_message_id"] == 8080
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, save, receipts)
    assert sender.call_count == 2
    events = [json.loads(line) for line in receipts.read_text().splitlines()]
    assert [r["ok"] for r in events] == [False, True]
    assert events[-1]["message_id"] == 8080


def test_one_failed_order_does_not_block_other_orders_or_reorder_its_updates(tmp_path, monkeypatch):
    state = {}
    for row in (order_row(), order_row(event="update", ts=101), order_row("another")):
        watch._queue_order_telegram(state, row)
    sender = Mock(side_effect=[http_error(400, "Bad Request"), 8888])
    monkeypatch.setattr(watch, "tg_send", sender)
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, lambda: None, tmp_path / "delivery.jsonl")
    assert sender.call_count == 2
    assert all(e["order"]["orderId"] == "3800809347" for e in state["telegram_outbox"])
    assert [e["event"] for e in state["telegram_outbox"]] == ["new", "update"]


def test_rate_limit_applies_to_pending_and_newly_queued_messages(tmp_path, monkeypatch):
    state = {}
    now = [100.0]
    monkeypatch.setattr(watch.time, "time", lambda: now[0])
    sender = Mock(side_effect=[http_error(429, "Too Many Requests", retry_after=600), 90, 91, 92])
    monkeypatch.setattr(watch, "tg_send", sender)
    watch._queue_order_telegram(state, order_row())
    watch._queue_order_telegram(state, order_row("another"))
    path = tmp_path / "delivery.jsonl"
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, lambda: None, path)
    assert sender.call_count == 1
    watch._queue_order_telegram(state, order_row("arrived-later"))
    now[0] = 400
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, lambda: None, path)
    assert sender.call_count == 1
    now[0] = 701
    watch._flush_telegram_outbox(CFG, ACCOUNT, state, lambda: None, path)
    assert sender.call_count == 4
    assert state["telegram_outbox"] == []


def test_disabled_push_keeps_pending_without_sending(tmp_path, monkeypatch):
    state = {}
    watch._queue_order_telegram(state, order_row())
    sender = Mock()
    monkeypatch.setattr(watch, "tg_send", sender)
    watch._flush_telegram_outbox(
        {**CFG, "telegram_order_forward_enabled": False}, ACCOUNT, state,
        lambda: None, tmp_path / "delivery.jsonl",
    )
    sender.assert_not_called()
    assert len(state["telegram_outbox"]) == 1


def test_worker_persists_queue_before_send_and_retries_identical_poll(tmp_path, monkeypatch):
    raw = {"playerWagerId": "3800809347", "stake": 150, "choices": order_row()["choices"]}
    cfg = {**CFG, "ps3838_accounts": [dict(ACCOUNT)]}
    config_path = tmp_path / "config.json"
    state_path = tmp_path / "ps3838/state/demo.json"
    watch.atomic_write_json(config_path, cfg)
    watch.atomic_write_json(state_path, {"initialized": True, "orders": {}})
    client = Mock(session_ready=False, ctx=None, secret="", browser_hidden=True)
    def login(**kwargs):
        client.session_ready = True
    client.login.side_effect = login
    client.poll.return_value = ({}, {"data": [raw]})
    monkeypatch.setattr(watch, "Ps3838Client", lambda *args, **kwargs: client)
    now = [100.0]
    monkeypatch.setattr(watch.time, "time", lambda: now[0])
    attempts = []
    def send(*args, **kwargs):
        saved = watch.read_json(state_path, {})
        assert "3800809347" in saved["orders"]
        assert saved["telegram_pending_count"] == 1
        attempts.append(1)
        if len(attempts) == 1:
            raise http_error(400, "Bad Request")
        return 1122
    monkeypatch.setattr(watch, "tg_send", send)
    def pause(seconds):
        now[0] += seconds
        if len(attempts) >= 2:
            cfg["ps3838_accounts"][0]["enabled"] = False
            watch.atomic_write_json(config_path, cfg)
    monkeypatch.setattr(watch.time, "sleep", pause)
    watch.process_account(ACCOUNT, cfg, tmp_path, config_path=config_path)
    assert len(attempts) == 2
    assert watch.read_json(state_path, {})["telegram_pending_count"] == 0
    rows = (tmp_path / "ps3838/orders/demo.jsonl").read_text().splitlines()
    assert len(rows) == 1  # 重试不需要重新发现订单，也不会再次写一笔新单。
