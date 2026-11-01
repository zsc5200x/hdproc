from copy import deepcopy
from unittest.mock import patch

import pytest

from hdzz.core.watcher import ps3838_watch
from hdzz.core.watcher.ps3838_watch import (
    build_poll_body,
    extract_orders,
    normalize_order,
    order_changes,
)


def sample_order(status="pending", score=None, win=None):
    return {
        "playerWagerId": "pw-1001",
        "player": "member01",
        "currency": "CNY",
        "stake": "25.00",
        "toRisk": "25.00",
        "toWin": "21.25",
        "status": status,
        "winLoss": win,
        "placed": "2026-10-06T10:20:30Z",
        "choices": [{
            "sportName": "足球",
            "leagueName": "英超",
            "eventName": "Home FC vs Away FC",
            "btName": "让球",
            "selection": "Home FC",
            "handicap": "-0.5",
            "finalOdds": "1.85",
            "score": score,
        }],
    }


def test_build_poll_body_matches_read_only_snapshot_request():
    assert build_poll_body() == {
        "downlineIds": None,
        "isInPlay": -1,
        "userIds": None,
        "groupType": "OWN_DOWNLINE",
        "excludeLeagueIds": None,
        "excludeSportIds": None,
        "productCodes": ["SB"],
        "ownerUserId": None,
        "minRisk": 0,
        "historyTime": 60,
    }


def test_extract_orders_accepts_nested_data_and_normalizes_fields():
    payload = {"body": {"result": {"rows": [sample_order()]}}}
    orders = extract_orders(payload, "ps3838_demo")
    assert len(orders) == 1
    order = orders[0]
    assert order["orderId"] == "pw-1001"
    assert order["playerWagerId"] == "pw-1001"
    assert order["account"] == "member01"
    assert order["stake"] == 25
    assert order["sport"] == "足球"
    assert order["league"] == "英超"
    assert order["raw"]["playerWagerId"] == "pw-1001"


def test_order_changes_reports_status_and_settlement_changes():
    previous = normalize_order(sample_order(), "ps3838_demo")
    current = normalize_order(sample_order(status="settled", score="2-1", win="46.25"), "ps3838_demo")
    changes = order_changes(previous, current)
    assert changes["status"] == ("pending", "settled")
    assert changes["score"] == (None, "2-1")
    assert changes["win"] == (None, 46.25)


def test_missing_order_id_gets_stable_fallback():
    order = normalize_order({"player": "member01", "stake": 3}, "ps3838_demo")
    assert order["orderId"]
    assert order["orderId"] == order["playerWagerId"]


def test_safe_name_keeps_distinct_unicode_account_names_separate():
    from hdzz.core.watcher.ps3838_watch import safe_name

    assert safe_name("节点一") != safe_name("节点二")
    assert safe_name("节点一") == safe_name("节点一")


@pytest.mark.parametrize("event", ["new", "update"])
def test_telegram_push_uses_shared_theme_and_translates_ps3838_choice(event):
    raw = {
        "playerWagerId": "3800020054",
        "wagerType": "NORMAL",
        "toRisk": 150,
        "currency": "THB",
        "placed": "07/10/2026 09:11:06",
        "choices": [{
            "leagueName": "中国- 甲级联赛",
            "eventName": "南京市-vs-陕西联",
            "btName": "HDP",
            "selection": "HOME",
            "handicap": "0.00",
            "periodName": "比赛\r\n\r\n",
            "inplay": True,
            "odds": 2.84,
        }],
    }
    order = normalize_order(raw, "ps3838_demo")
    original = deepcopy(order)
    cfg = {
        "telegram_order_forward_enabled": True,
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
        "telegram_ui": {
            "order_text_custom_emoji_ids": {"title": "title-id", "league": "league-id", "stake": "stake-id"},
            "order_divider_custom_emoji_ids": ["divider-id"],
        },
    }

    with patch.object(ps3838_watch, "tg_send") as send:
        ps3838_watch._send_order_telegram(cfg, {"name": "ps3838_demo", "username": "demo"}, order, event, {})

    send.assert_called_once()
    message = send.call_args.args[2]
    assert '<tg-emoji emoji-id="title-id">🤣</tg-emoji>新注单提醒' in message
    assert 'emoji-id="divider-id"' in message
    assert 'emoji-id="league-id"' in message
    assert 'emoji-id="stake-id"' in message
    assert "状态：滚球" in message
    assert "玩法：让球" in message
    assert "时段：全场" in message
    assert "选择：南京市 0.00 @2.84" in message
    assert "原注金额：150 THB" in message
    assert "来源后台：平博 · demo" in message
    assert "单号：3800020054" in message
    assert "时间：07/10/2026 09:11:06" in message
    assert "NORMAL" not in message
    assert "HOME" not in message
    assert "跟单金额" not in message
    assert "跟单成功" not in message
    assert order == original


def test_telegram_push_translates_over_selection():
    order = normalize_order({
        "playerWagerId": "3800009907",
        "stake": 160,
        "choices": [{
            "eventName": "佛山南狮-vs-广西恒宸",
            "btName": "OU",
            "selection": "OVER",
            "handicap": "1.75",
            "inplay": True,
            "odds": 2.41,
        }],
    }, "ps3838_demo")
    cfg = {"telegram_bot_token": "test-token", "telegram_chat_id": "test-chat"}

    with patch.object(ps3838_watch, "tg_send") as send:
        ps3838_watch._send_order_telegram(cfg, {"name": "ps3838_demo"}, order, "new", {})

    message = send.call_args.args[2]
    assert "玩法：进球大小" in message
    assert "选择：大 1.75 @2.41" in message
    assert "OVER" not in message


@pytest.mark.parametrize("code,selection,handicap,expected_market,expected_selection", [
    ("HDP", "AWAY", "-0.25", "让球", "客队 -0.25"),
    ("HDP", "HOME", 0, "让球", "主队 0"),
    ("OU", "UNDER", "2.5", "进球大小", "小 2.5"),
    ("ML", "AWAY", None, "独赢", "客队"),
])
@pytest.mark.parametrize("inplay,expected_status", [(True, "滚球"), (False, "早盘"), (None, "未提供")])
def test_telegram_choice_handles_team_total_zero_and_status(
    code, selection, handicap, expected_market, expected_selection, inplay, expected_status,
):
    order = normalize_order({
        "playerWagerId": "test-order",
        "wagerType": "NORMAL",
        "choices": [{
            "eventName": "主队-vs-客队",
            "btName": code,
            "selection": selection,
            "handicap": handicap,
            "inplay": inplay,
            "odds": 1.9,
        }],
    }, "demo")
    with patch.object(ps3838_watch, "tg_send") as send:
        ps3838_watch._send_order_telegram(
            {"telegram_bot_token": "test-token", "telegram_chat_id": "test-chat"},
            {"name": "demo"}, order, "new", {},
        )
    message = send.call_args.args[2]
    assert f"玩法：{expected_market}" in message
    assert f"选择：{expected_selection} @1.9" in message
    assert f"状态：{expected_status}" in message


def test_telegram_push_preserves_custom_emoji_html_in_transport():
    cfg = {
        "telegram_bot_token": "test-token",
        "telegram_chat_id": "test-chat",
        "telegram_ui": {"order_text_custom_emoji_ids": {"title": "5935873621749668287"}},
    }
    with patch("hdzz.core.watcher.ag_watcher.requests.post") as post:
        post.return_value.json.return_value = {"ok": True, "result": {"message_id": 1}}
        ps3838_watch._send_order_telegram(cfg, {"name": "demo"}, normalize_order(sample_order(), "demo"), "new", {})
    post.assert_called_once()
    payload = post.call_args.kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    assert '<tg-emoji emoji-id="5935873621749668287">🤣</tg-emoji>' in payload["text"]
    assert payload["chat_id"] == "test-chat"
