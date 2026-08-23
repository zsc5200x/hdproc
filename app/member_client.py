import base64
import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_xml(text: str) -> Dict[str, str]:
    root = ET.fromstring(text)
    out: Dict[str, str] = {}
    for child in list(root):
        if child.tag:
            out[str(child.tag)] = (child.text or "").strip()
    return out


@dataclass
class MemberSession:
    mid: str
    uid: str
    username: str


class MemberClient:
    """
    会员端 transform.php 客户端（requests 版本）。

    注意：
    - 登录（chk_login）需要 blackbox/auto/userAgent，这些通常由网页端生成。
    - 提交下注（FT_bet）需要 cookie（login_<mid>）和 uid。
    """

    def __init__(self, base_url: str, ver: str, proxy: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.ver = ver
        self.session = requests.Session()
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    @property
    def transform_url(self) -> str:
        return f"{self.base_url}/transform.php?ver={self.ver}"

    def post(self, form: Dict[str, Any], timeout_s: int = 20) -> str:
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "origin": self.base_url,
            "referer": self.base_url + "/",
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
        }
        r = self.session.post(
            self.transform_url,
            data=form,
            headers=headers,
            timeout=timeout_s,
            proxies=self.proxies,
        )
        r.raise_for_status()
        return r.text or ""

    def chk_login(
        self,
        *,
        username: str,
        password: str,
        auto: str,
        blackbox: str,
        user_agent: str,
        langx: str = "zh-cn",
        app: str = "N",
        timeout_s: int = 30,
    ) -> MemberSession:
        # 会员端参数 userAgent 是 base64 的 UA
        ua_b64 = base64.b64encode(user_agent.encode("utf-8")).decode("ascii")
        form = {
            "ver": self.ver,
            "p": "chk_login",
            "langx": langx,
            "username": username,
            "password": password,
            "app": app,
            "auto": auto,
            "blackbox": blackbox,
            "userAgent": ua_b64,
        }
        text = self.post(form, timeout_s=timeout_s)
        data = _parse_xml(text)
        if data.get("status") != "200":
            raise RuntimeError(f"登录失败: {data}")
        return MemberSession(mid=data.get("mid", ""), uid=data.get("uid", ""), username=data.get("username", ""))

    def ft_bet(
        self,
        *,
        uid: str,
        golds: str,
        gid: str,
        gtype: str,
        wtype: str,
        rtype: str,
        chose_team: str,
        ioratio: str,
        con: str,
        ratio: str,
        odd_f_type: str = "H",
        autoOdd: str = "Y",
        isRB: str = "N",
        imp: str = "N",
        ptype: str = "",
        isYesterday: str = "N",
        f: str = "1M",
        timestamp_ms: Optional[int] = None,
        langx: str = "zh-cn",
        timeout_s: int = 30,
    ) -> Dict[str, str]:
        ts = str(int(timestamp_ms or _now_ms()))
        form = {
            "ver": self.ver,
            "p": "FT_bet",
            "uid": uid,
            "langx": langx,
            "odd_f_type": odd_f_type,
            "golds": str(golds),
            "gid": str(gid),
            "gtype": str(gtype),
            "wtype": str(wtype),
            "rtype": str(rtype),
            "chose_team": str(chose_team),
            "ioratio": str(ioratio),
            "con": str(con),
            "ratio": str(ratio),
            "autoOdd": autoOdd,
            "timestamp": ts,
            "timestamp2": "",
            "isRB": isRB,
            "imp": imp,
            "ptype": ptype,
            "isYesterday": isYesterday,
            "f": f,
        }
        text = self.post(form, timeout_s=timeout_s)
        data = _parse_xml(text)
        if data.get("code") != "560":
            raise RuntimeError(f"下注失败: {data}")
        return data

    def get_today_wagers(
        self,
        *,
        uid: str,
        selGtype: str = "ALL",
        LS: str = "g",
        chk_cw: str = "N",
        fmt: str = "json",
        db_slow: str = "N",
        langx: str = "zh-cn",
        timeout_s: int = 20,
    ) -> Dict[str, Any]:
        form = {
            "ver": self.ver,
            "p": "get_today_wagers",
            "uid": uid,
            "langx": langx,
            "LS": LS,
            "selGtype": selGtype,
            "chk_cw": chk_cw,
            "ts": str(_now_ms()),
            "format": fmt,
            "db_slow": db_slow,
        }
        text = self.post(form, timeout_s=timeout_s)
        return json.loads(text)

