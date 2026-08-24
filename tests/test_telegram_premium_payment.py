from hdzz.api.telegram.premium_payment import build_sign_payload, is_deposit_paid, parse_pay_link_result


def test_build_sign_payload_contains_sign_and_id() -> None:
    p = build_sign_payload(
        app_id="27596",
        token="abc123",
        data={
            "unique_id": "VIP001",
            "amount": "8",
            "coin": "USDT",
        },
    )
    assert p["id"] == "27596"
    assert p["sign"]
    assert len(str(p["sign"])) == 32


def test_parse_pay_link_result() -> None:
    order_id, pay_url = parse_pay_link_result(
        {"data": {"order_id": "777", "pay_url": "https://t.me/okpay?start=abc"}}
    )
    assert order_id == "777"
    assert pay_url.startswith("https://")


def test_is_deposit_paid() -> None:
    assert is_deposit_paid({"data": {"status": 1, "type": "deposit"}}) is True
    assert is_deposit_paid({"status": "success", "code": 10000}) is False
    assert is_deposit_paid({"status": "success", "code": 10000, "type": "deposit"}) is True
    assert is_deposit_paid({"data": {"status": 0, "type": "deposit"}}) is False
