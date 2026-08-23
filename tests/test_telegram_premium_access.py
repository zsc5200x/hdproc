from hdzz.api.telegram.premium_access import ensure_access_doc, grant_order_access, has_order_access, revoke_order_access


def test_grant_and_check_order_access() -> None:
    doc = ensure_access_doc({})
    doc, changed = grant_order_access(doc, user_key="uid:1001", order_id="234", pay_no="VIP001")
    assert changed is True
    assert has_order_access(doc, user_key="uid:1001", order_id="234") is True


def test_revoke_order_access() -> None:
    doc = ensure_access_doc({})
    doc, _ = grant_order_access(doc, user_key="uid:1001", order_id="234", pay_no="VIP001")
    doc, changed = revoke_order_access(doc, user_key="uid:1001", order_id="234")
    assert changed is True
    assert has_order_access(doc, user_key="uid:1001", order_id="234") is False

