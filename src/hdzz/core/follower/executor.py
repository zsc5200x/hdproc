from __future__ import annotations

import random
import time
from typing import Any, Dict, Optional

import requests

from hdzz.common.http import hard_timeout, requests_timeout
from hdzz.common.parse import parse_cookie_header


def make_member_session(*, base_url: str, proxy: str, cookie: str) -> requests.Session:
    base = str(base_url or "").rstrip("/")
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update(
        {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "origin": base,
            "referer": base + "/",
            "content-type": "application/x-www-form-urlencoded",
            "x-requested-with": "XMLHttpRequest",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        }
    )
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    for k, v in parse_cookie_header(cookie).items():
        try:
            sess.cookies.set(k, v)
        except Exception:
            pass
    return sess


def close_member_session(session: Optional[requests.Session]) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


def member_post_session(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    form: Dict[str, Any],
    timeout_s: int,
    response_meta: Optional[Dict[str, Any]] = None,
) -> str:
    base = str(base_url or "").rstrip("/")
    url = f"{base}/transform.php?ver={ver}"
    hard_s = max(2, int(timeout_s or 0) + 2)
    # 站点偶发 SSL EOF/连接中断：对“读接口”和预校验会造成连锁失败（拿不到盘口/水位 -> LINE_NOT_FOUND -> 队列变慢）。
    # 这里做一次轻量重试（不吞硬超时 HardTimeout），并在重试时强制 Connection: close 以避免复用坏连接。
    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            if response_meta is not None:
                response_meta.clear()
                response_meta["attempt"] = attempt
            with hard_timeout(hard_s):
                extra_headers = {"Connection": "close"} if attempt >= 2 else None
                r = session.post(
                    url,
                    data=form,
                    timeout=requests_timeout(timeout_s),
                    verify=False,
                    headers=extra_headers,
                )
            if response_meta is not None:
                response_meta["http_status"] = int(r.status_code)
                response_meta["response_body"] = str(r.text or "")
            r.raise_for_status()
            text = r.text or ""
            t = str(text or "").strip()
            # 站点偶发返回短字符串（非 XML），通常代表“上下文/负载”问题；做一次重试更容易恢复。
            if t and (not t.startswith("<")):
                low = t.lower()
                if ("code_type" in low and "null" in low) or ("loading" in low and "high" in low) or ("checkemnu" in low) or ("variablestandard" in low):
                    # 不直接 raise：让上层根据具体接口决定是否 warmup/menu 修复；这里仅做连接池清理 + 短退避后重试。
                    if attempt >= 3:
                        return text
                    try:
                        for ad in getattr(session, "adapters", {}).values():
                            try:
                                ad.close()
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        time.sleep(0.12 + (0.22 * random.random()))
                    except Exception:
                        pass
                    continue
            return text
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            last_exc = e
            if attempt >= 3:
                raise
            # 清理连接池，避免复用到坏连接（部分站点/网络环境下会触发 SSLEOFError）
            try:
                for ad in getattr(session, "adapters", {}).values():
                    try:
                        ad.close()
                    except Exception:
                        pass
            except Exception:
                pass
            # jitter backoff: keep it short to avoid拖慢 one-shot/队列
            try:
                time.sleep(0.12 + (0.22 * random.random()))
            except Exception:
                pass
            continue
    if last_exc is not None:
        raise last_exc
    return ""


def member_post_session_once(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    form: Dict[str, Any],
    timeout_s: int,
    response_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Send exactly one member request without the normal retry wrapper.

    The diagnostic BetContext experiment needs to distinguish one
    ``FT_order_view`` request from connection-level retries.  It still uses
    the same Session, endpoint, form encoding, timeout and TLS settings as
    the normal member transport.
    """
    base = str(base_url or "").rstrip("/")
    url = f"{base}/transform.php?ver={ver}"
    hard_s = max(2, int(timeout_s or 0) + 2)
    try:
        with hard_timeout(hard_s):
            response = session.post(
                url,
                data=form,
                timeout=requests_timeout(timeout_s),
                verify=False,
            )
        if response_meta is not None:
            response_meta.clear()
            response_meta["http_status"] = int(response.status_code)
            response_meta["response_body"] = str(response.text or "")
        response.raise_for_status()
        return str(response.text or "")
    except BaseException as exc:
        if response_meta is not None:
            response_meta.setdefault("exception_type", type(exc).__name__)
            response_meta.setdefault("exception", str(exc))
        raise


def member_warmup_sport_menu(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    uid: str,
    gtype: str,
    showtype: str,
    rtype: str,
    timeout_s: int,
) -> None:
    """
    轻量预热：只绑定 sport_menu（gtype/showtype/rtype）。

    适用场景：
    - get_game_list / get_game_more 这类“读接口”频繁切换 showtype/rtype；
    - full warmup( right_menu + sport_menu + order ) 代价较高，容易把队列拖慢。
    """
    gtype2 = str(gtype or "").strip().lower() or "ft"
    showtype2 = str(showtype or "").strip().lower() or "today"
    rtype2 = str(rtype or "").strip().lower() or "r"
    member_post_session(
        session=session,
        base_url=base_url,
        ver=ver,
        form={
            "p": "sport_menu",
            "ver": ver,
            "langx": "zh-cn",
            "uid": uid,
            "ts": str(int(time.time() * 1000)),
            "gtype": gtype2,
            "showtype": showtype2,
            "rtype": rtype2,
        },
        timeout_s=timeout_s,
    )


def member_warmup_menu(
    *,
    session: requests.Session,
    base_url: str,
    ver: str,
    uid: str,
    gtype: str,
    showtype: str,
    rtype: str,
    timeout_s: int,
) -> None:
    """
    会员端在某些情况下会对下注/预校验返回非 XML（如 CheckEMNU / VariableStandard），
    通常与“菜单上下文/当前运动页”有关。这里用轻量的 menu 请求做一次预热，尽量贴近浏览器行为。
    """
    gtype2 = str(gtype or "").strip().lower() or "ft"
    showtype2 = str(showtype or "").strip().lower() or "today"
    rtype2 = str(rtype or "").strip().lower() or "r"

    # right_menu：浏览器常见的初始化请求
    try:
        member_post_session(
            session=session,
            base_url=base_url,
            ver=ver,
            form={"p": "right_menu", "ver": ver, "langx": "zh-cn", "uid": uid},
            timeout_s=timeout_s,
        )
    except Exception:
        pass

    # sport_menu：绑定 gtype/showtype/rtype 到当前会话
    try:
        member_warmup_sport_menu(
            session=session,
            base_url=base_url,
            ver=ver,
            uid=uid,
            gtype=gtype2,
            showtype=showtype2,
            rtype=rtype2,
            timeout_s=timeout_s,
        )
    except Exception:
        pass

    # order：浏览器下注页常见会先加载 order 页面，部分站点否则可能对下注返回非 XML（如 VariableStandard）
    try:
        member_post_session(
            session=session,
            base_url=base_url,
            ver=ver,
            form={"p": "order", "ver": ver, "langx": "zh-cn", "uid": uid},
            timeout_s=timeout_s,
        )
    except Exception:
        pass
