from __future__ import annotations

import hashlib
from typing import Any, Dict, Tuple
from urllib.parse import unquote_plus, urlencode

import requests


def _norm_base(api_base: str) -> str:
    s = str(api_base or "").strip()
    if not s:
        s = "https://api.okaypay.me/shop"
    return s.rstrip("/")


def build_sign_payload(*, app_id: str, token: str, data: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for k, v in (data or {}).items():
        if v is None:
            continue
        s = str(v).strip()
        if s == "":
            continue
        payload[str(k)] = s
    payload["id"] = str(app_id or "").strip()

    kv = dict(payload)
    kv.pop("sign", None)
    qs = urlencode(sorted(kv.items()))
    # 按官方 PHP 示例：urldecode(http_build_query(...))
    # Python 对齐：先 urlencode 再 unquote_plus（把 %xx 和 + 还原）
    raw = f"{unquote_plus(qs)}&token={str(token or '').strip()}"
    sign = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    payload["sign"] = sign
    return payload


def verify_sign(*, token: str, payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    got = str(payload.get("sign") or "").strip().upper()
    if not got:
        return False
    kv: Dict[str, Any] = {}
    for k, v in payload.items():
        if str(k) == "sign" or v is None:
            continue
        s = str(v).strip()
        if s == "":
            continue
        kv[str(k)] = s
    qs = urlencode(sorted(kv.items()))
    raw = f"{unquote_plus(qs)}&token={str(token or '').strip()}"
    expect = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    return got == expect


def create_pay_link(
    *,
    api_base: str,
    app_id: str,
    token: str,
    unique_id: str,
    amount: str,
    coin: str,
    name: str = "",
    return_url: str = "",
    callback_url: str = "",
    timeout_s: int = 12,
    proxy: str = "",
) -> Dict[str, Any]:
    url = f"{_norm_base(api_base)}/payLink"
    data = build_sign_payload(
        app_id=app_id,
        token=token,
        data={
            "unique_id": unique_id,
            "name": name,
            "amount": amount,
            "coin": coin,
            "return_url": return_url,
            "callback_url": callback_url,
        },
    )
    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    r = requests.post(url, data=data, timeout=max(5, int(timeout_s)), proxies=proxies)
    r.raise_for_status()
    j = r.json()
    if not isinstance(j, dict):
        raise RuntimeError(f"okaypay create_pay_link invalid response: {j}")
    return j


def check_deposit(
    *,
    api_base: str,
    app_id: str,
    token: str,
    unique_id: str,
    timeout_s: int = 12,
    proxy: str = "",
) -> Dict[str, Any]:
    url = f"{_norm_base(api_base)}/checkDeposit"
    data = build_sign_payload(
        app_id=app_id,
        token=token,
        data={"unique_id": unique_id},
    )
    proxies = {"http": proxy, "https": proxy} if str(proxy or "").strip() else None
    r = requests.post(url, data=data, timeout=max(5, int(timeout_s)), proxies=proxies)
    r.raise_for_status()
    j = r.json()
    if not isinstance(j, dict):
        raise RuntimeError(f"okaypay check_deposit invalid response: {j}")
    return j


def _extract_data(obj: Dict[str, Any]) -> Dict[str, Any]:
    data = obj.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return {}


def parse_pay_link_result(resp: Dict[str, Any]) -> Tuple[str, str]:
    obj = resp if isinstance(resp, dict) else {}
    data = _extract_data(obj)
    order_id = str(data.get("order_id") or obj.get("order_id") or "").strip()
    pay_url = str(
        data.get("pay_url")
        or data.get("url")
        or data.get("link")
        or obj.get("pay_url")
        or obj.get("url")
        or obj.get("link")
        or ""
    ).strip()
    return order_id, pay_url


def is_deposit_paid(resp: Dict[str, Any]) -> bool:
    if not isinstance(resp, dict):
        return False
    d = _extract_data(resp)
    status_d = str(d.get("status") or "").strip().lower()
    status_t = str(resp.get("status") or "").strip().lower()
    typ_d = str(d.get("type") or "").strip().lower()
    typ_t = str(resp.get("type") or "").strip().lower()
    code = str(resp.get("code") or "").strip()

    # 明确提现场景直接视为非充值到账
    if typ_d == "withdraw" or typ_t == "withdraw":
        return False

    # 主判据：data.status 表示订单支付态（checkDeposit 文档：0/1）
    if status_d in {"1", "paid", "success", "true"}:
        # 若显式带 type，则必须是 deposit
        if (typ_d and typ_d != "deposit") or (typ_t and typ_t != "deposit"):
            return False
        return True

    # 次判据：顶层状态是明确“已支付”值（注意：不能把 status=success 当作已支付）
    if status_t in {"1", "paid", "true"}:
        if (typ_d and typ_d != "deposit") or (typ_t and typ_t != "deposit"):
            return False
        return True

    # 兼容 SDK 回调示例：code=10000 + status=success + deposit
    if code == "10000" and status_t == "success" and (typ_d == "deposit" or typ_t == "deposit"):
        return True

    return False
