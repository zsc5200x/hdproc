from __future__ import annotations

import base64
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

DEFAULT_ADSPOWER_API_BASE = "http://127.0.0.1:50325"

_TRACKING_DOMAIN_SUFFIX = (
    "tawk.to",
    "tawk.link",
    "google.com",
    "gstatic.com",
    "facebook.com",
    "doubleclick.net",
    "googletagmanager.com",
    "google-analytics.com",
)


def _uniq_keep_order(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        s = str(x or "").strip()
        if not s:
            continue
        k = s.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def normalize_adspower_api_base(api_base: str) -> str:
    raw = str(api_base or "").strip() or DEFAULT_ADSPOWER_API_BASE
    if "://" not in raw:
        raw = f"http://{raw}"
    u = urlparse(raw)
    if not u.scheme or not u.netloc:
        return DEFAULT_ADSPOWER_API_BASE
    path = str(u.path or "").strip().rstrip("/")
    return f"{u.scheme}://{u.netloc}{path}"


def _new_session() -> requests.Session:
    sess = requests.Session()
    sess.trust_env = False
    return sess


def _adspower_headers(api_key: str = "") -> Dict[str, str]:
    key = str(api_key or "").strip()
    if not key:
        return {}
    return {"api-key": key}


def _parse_code_msg(data: Any) -> Tuple[int, str]:
    if not isinstance(data, dict):
        return -999999, "invalid_response"
    code = data.get("code")
    try:
        code_i = int(code)
    except Exception:
        code_i = 0 if code in (None, "") else -999998
    msg = str(data.get("msg") or "")
    return code_i, msg


def _extract_profile_items(raw_item: Any) -> Dict[str, Any]:
    it = raw_item if isinstance(raw_item, dict) else {}
    profile_id = str(it.get("profile_id") or it.get("user_id") or "").strip()
    profile_no = str(it.get("profile_no") or it.get("serial_number") or "").strip()
    return {
        "profile_id": profile_id,
        "profile_no": profile_no,
        "name": str(it.get("name") or "").strip(),
        "group_name": str(it.get("group_name") or "").strip(),
        "group_id": str(it.get("group_id") or "").strip(),
        "domain_name": str(it.get("domain_name") or "").strip(),
        "platform": str(it.get("platform") or "").strip(),
        "username": str(it.get("username") or "").strip(),
        "user_proxy_config": it.get("user_proxy_config") if isinstance(it.get("user_proxy_config"), dict) else {},
    }


def adspower_list_profiles(
    *,
    api_base: str,
    api_key: str = "",
    page_size: int = 100,
    max_pages: int = 50,
    timeout_s: int = 10,
) -> Tuple[List[Dict[str, Any]], str]:
    base = normalize_adspower_api_base(api_base)
    size = max(1, min(500, int(page_size or 100)))
    pages = max(1, min(200, int(max_pages or 50)))
    headers = _adspower_headers(api_key)
    out: List[Dict[str, Any]] = []
    seen = set()
    sess = _new_session()
    try:
        for page in range(1, pages + 1):
            url = f"{base}/api/v1/user/list"
            params = {"page": page, "page_size": size}
            payload: Optional[Dict[str, Any]] = None
            last_err = ""
            for attempt in range(3):
                try:
                    resp = sess.get(url, params=params, headers=headers, timeout=max(3, int(timeout_s)))
                    payload = resp.json()
                except Exception as e:
                    last_err = str(e)
                    payload = None
                    break
                code, msg = _parse_code_msg(payload)
                if code == 0:
                    break
                msg_l = msg.lower()
                if ("too many request" in msg_l or "rate" in msg_l) and attempt < 2:
                    time.sleep(1.05)
                    continue
                last_err = msg or f"code={code}"
                payload = None
                break
            if payload is None:
                return out, f"list_profiles_failed: {last_err}" if last_err else "list_profiles_failed"

            data = payload.get("data") if isinstance(payload, dict) else {}
            rows = data.get("list") if isinstance(data, dict) else []
            if not isinstance(rows, list):
                rows = []
            if not rows:
                break
            for raw in rows:
                item = _extract_profile_items(raw)
                pid = item.get("profile_id")
                pno = item.get("profile_no")
                dedup_key = str(pid or pno or "").strip()
                if not dedup_key or dedup_key in seen:
                    continue
                seen.add(dedup_key)
                out.append(item)
            if len(rows) < size:
                break
    finally:
        try:
            sess.close()
        except Exception:
            pass
    return out, ""


def adspower_find_profile(
    *,
    api_base: str,
    profile_id: str = "",
    profile_no: str = "",
    api_key: str = "",
    timeout_s: int = 10,
) -> Tuple[Optional[Dict[str, Any]], str]:
    pid = str(profile_id or "").strip()
    pno = str(profile_no or "").strip()
    if not (pid or pno):
        return None, "missing_profile_id_or_profile_no"
    rows, err = adspower_list_profiles(api_base=api_base, api_key=api_key, timeout_s=timeout_s)
    if err:
        return None, err
    for row in rows:
        if pid and str(row.get("profile_id") or "").strip() == pid:
            return row, ""
        if pno and str(row.get("profile_no") or "").strip() == pno:
            return row, ""
    return None, "profile_not_found"


def _cookie_params(profile_id: str, profile_no: str) -> Dict[str, Any]:
    pid = str(profile_id or "").strip()
    pno = str(profile_no or "").strip()
    out: Dict[str, Any] = {}
    if pid:
        out["profile_id"] = pid
    elif pno:
        out["profile_no"] = pno
    return out


def adspower_get_profile_cookies(
    *,
    api_base: str,
    profile_id: str = "",
    profile_no: str = "",
    api_key: str = "",
    timeout_s: int = 10,
) -> Tuple[List[Dict[str, Any]], str]:
    params = _cookie_params(profile_id, profile_no)
    if not params:
        return [], "missing_profile_id_or_profile_no"

    base = normalize_adspower_api_base(api_base)
    url = f"{base}/api/v2/browser-profile/cookies"
    headers = _adspower_headers(api_key)
    sess = _new_session()
    try:
        resp = sess.get(url, params=params, headers=headers, timeout=max(3, int(timeout_s)))
        data = resp.json()
    except Exception as e:
        return [], f"get_profile_cookies_failed: {e}"
    finally:
        try:
            sess.close()
        except Exception:
            pass
    code, msg = _parse_code_msg(data)
    if code != 0:
        return [], f"get_profile_cookies_failed: {msg or f'code={code}'}"
    payload = data.get("data") if isinstance(data, dict) else {}
    raw_cookies = payload.get("cookies") if isinstance(payload, dict) else None
    cookies: List[Dict[str, Any]] = []
    if isinstance(raw_cookies, list):
        cookies = [c for c in raw_cookies if isinstance(c, dict)]
    elif isinstance(raw_cookies, str):
        try:
            parsed = json.loads(raw_cookies)
            if isinstance(parsed, list):
                cookies = [c for c in parsed if isinstance(c, dict)]
        except Exception:
            cookies = []
    return cookies, ""


def adspower_start_profile(
    *,
    api_base: str,
    profile_id: str = "",
    profile_no: str = "",
    api_key: str = "",
    timeout_s: int = 30,
) -> Tuple[Dict[str, Any], str]:
    params = _cookie_params(profile_id, profile_no)
    if not params:
        return {}, "missing_profile_id_or_profile_no"

    base = normalize_adspower_api_base(api_base)
    url = f"{base}/api/v2/browser-profile/start"
    headers = _adspower_headers(api_key)
    sess = _new_session()
    try:
        resp = sess.post(url, json=params, headers=headers, timeout=max(5, int(timeout_s)))
        data = resp.json()
    except Exception as e:
        return {}, f"start_profile_failed: {e}"
    finally:
        try:
            sess.close()
        except Exception:
            pass
    code, msg = _parse_code_msg(data)
    if code != 0:
        return {}, f"start_profile_failed: {msg or f'code={code}'}"
    payload = data.get("data") if isinstance(data, dict) else {}
    ws = payload.get("ws") if isinstance(payload, dict) else {}
    ws_puppeteer = ""
    if isinstance(ws, dict):
        ws_puppeteer = str(ws.get("puppeteer") or "").strip()
    out = {
        "debug_port": str(payload.get("debug_port") or "").strip() if isinstance(payload, dict) else "",
        "webdriver": str(payload.get("webdriver") or "").strip() if isinstance(payload, dict) else "",
        "ws_puppeteer": ws_puppeteer,
        "raw": payload if isinstance(payload, dict) else {},
    }
    return out, ""


def adspower_stop_profile(
    *,
    api_base: str,
    profile_id: str = "",
    profile_no: str = "",
    api_key: str = "",
    timeout_s: int = 20,
) -> str:
    params = _cookie_params(profile_id, profile_no)
    if not params:
        return "missing_profile_id_or_profile_no"

    base = normalize_adspower_api_base(api_base)
    url = f"{base}/api/v2/browser-profile/stop"
    headers = _adspower_headers(api_key)
    sess = _new_session()
    try:
        resp = sess.post(url, json=params, headers=headers, timeout=max(5, int(timeout_s)))
        data = resp.json()
    except Exception as e:
        return f"stop_profile_failed: {e}"
    finally:
        try:
            sess.close()
        except Exception:
            pass
    code, msg = _parse_code_msg(data)
    if code != 0:
        msg_s = str(msg or "").strip()
        if "not open" in msg_s.lower():
            return ""
        return f"stop_profile_failed: {msg_s or f'code={code}'}"
    return ""


def adspower_profile_proxy_url(profile: Dict[str, Any]) -> str:
    if not isinstance(profile, dict):
        return ""
    cfg = profile.get("user_proxy_config")
    if not isinstance(cfg, dict):
        return ""
    host = str(cfg.get("proxy_host") or "").strip()
    port = str(cfg.get("proxy_port") or "").strip()
    if not (host and port):
        return ""
    tp = str(cfg.get("proxy_type") or "").strip().lower()
    if tp in {"socks5", "socks5h"}:
        scheme = "socks5h"
    elif tp in {"http", "https"}:
        scheme = tp
    else:
        scheme = "http"
    user = str(cfg.get("proxy_user") or "").strip()
    pwd = str(cfg.get("proxy_password") or "").strip()
    if user and pwd:
        return f"{scheme}://{user}:{pwd}@{host}:{port}"
    return f"{scheme}://{host}:{port}"


def adspower_cookie_header_from_cookies(cookies: List[Dict[str, Any]]) -> str:
    pairs: List[Tuple[str, str]] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if not name:
            continue
        pairs.append((name, value))
    dedup: Dict[str, str] = {}
    order: List[str] = []
    for k, v in pairs:
        lk = k.casefold()
        if lk not in dedup:
            order.append(k)
        dedup[lk] = v
    out_parts: List[str] = []
    for k in order:
        lk = k.casefold()
        if lk not in dedup:
            continue
        out_parts.append(f"{k}={dedup[lk]}")
    return "; ".join(out_parts)


def _decode_cookie_value(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    pads = ("", "=", "==")
    for suf in pads:
        try:
            raw = base64.b64decode(s + suf, validate=False)
            txt = raw.decode("utf-8", "ignore").strip()
            if txt:
                return txt
        except Exception:
            continue
    return ""


def adspower_extract_ver_candidates_from_cookies(cookies: List[Dict[str, Any]]) -> List[str]:
    vals: List[str] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if not (name or value):
            continue
        if re.search(r"mygamever|gamever|ver", name, flags=re.IGNORECASE):
            if value:
                vals.append(value)
            dec = _decode_cookie_value(value)
            if dec:
                vals.append(dec)
        vals.extend(re.findall(r"(?:^|[;,&\\s])(?:mygamever|gamever|ver)=([0-9A-Za-z._-]{4,})", value, flags=re.IGNORECASE))
        dec2 = _decode_cookie_value(value)
        if dec2:
            vals.extend(
                re.findall(r"(?:^|[;,&\\s])(?:mygamever|gamever|ver)=([0-9A-Za-z._-]{4,})", dec2, flags=re.IGNORECASE)
            )
    cleaned: List[str] = []
    for v in vals:
        s = str(v or "").strip()
        if not s:
            continue
        if re.fullmatch(r"[_0-9A-Za-z.-]{4,}", s):
            cleaned.append(s)
    return _uniq_keep_order(cleaned)


def adspower_extract_uid_candidates_from_cookies(cookies: List[Dict[str, Any]]) -> List[str]:
    vals: List[str] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        value = str(c.get("value") or "").strip()
        if re.fullmatch(r"(?:uid|userid|user_id|loginuid)", name, flags=re.IGNORECASE):
            vals.append(value)
        m_login = re.fullmatch(r"login_(\d{4,})", name, flags=re.IGNORECASE)
        if m_login:
            vals.append(m_login.group(1))
        vals.extend(re.findall(r"(?:^|[;,&\\s])(?:uid|userid|user_id|loginuid)=([0-9A-Za-z_-]{4,})", value, flags=re.IGNORECASE))
        dec = _decode_cookie_value(value)
        if dec:
            vals.extend(re.findall(r"(?:^|[;,&\\s])(?:uid|userid|user_id|loginuid)=([0-9A-Za-z_-]{4,})", dec, flags=re.IGNORECASE))
    cleaned: List[str] = []
    for v in vals:
        s = str(v or "").strip()
        if re.fullmatch(r"[0-9A-Za-z_-]{4,}", s):
            cleaned.append(s)
    return _uniq_keep_order(cleaned)


def adspower_infer_base_url_from_cookies(cookies: List[Dict[str, Any]]) -> str:
    primary: List[str] = []
    fallback: List[str] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        domain = str(c.get("domain") or "").strip().lstrip(".").lower()
        if not domain:
            continue
        if any(domain.endswith(suf) for suf in _TRACKING_DOMAIN_SUFFIX):
            continue
        name = str(c.get("name") or "").strip().lower()
        if re.search(r"(?:^login_|mygamever|gamever|iorchg|cookiechk|protocolstr|uid|cu$)", name):
            primary.append(domain)
        else:
            fallback.append(domain)
    domains = _uniq_keep_order(primary or fallback)
    if not domains:
        return ""
    return f"https://{domains[0]}"
