#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import sys
import requests
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

DEFAULT_VER = "2026-01-29-flagChg_135"
BASE_URL = "https://mos077.com/transform.php"

@dataclass
class OddsRow:
    market: str          # "大小球" / "让球"
    side: str            # "C" / "H"（后面你想映射成 大/小、主/客也可以）
    spread: str          # 盘口线位，比如 "1.5 / 2" 或 "0"
    ioratio: str         # 水位
    score: str           # 比分
    league: str
    home: str
    away: str
    code: str            # server code
    raw_note: str = ""   # 如果返回不是XML，就放这里

def http_post(cookie_header: str, data: Dict[str, str], ver: str) -> str:
    url = f"{BASE_URL}?ver={ver}"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://mos077.com",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie_header.strip(),
    }
    r = requests.post(url, headers=headers, data=data, timeout=12)
    r.raise_for_status()
    return r.text.strip()

def _xml_get(root: ET.Element, tag: str) -> str:
    el = root.find(tag)
    return el.text.strip() if el is not None and el.text else ""

def parse_ft_order_view(text: str, market_name: str, side: str) -> OddsRow:
    t = (text or "").strip()

    # 站点有时会回一些短字符串
    if not t.startswith("<?xml"):
        return OddsRow(
            market=market_name,
            side=side,
            spread="",
            ioratio="",
            score="",
            league="",
            home="",
            away="",
            code="",
            raw_note=t,
        )

    root = ET.fromstring(t)

    code = _xml_get(root, "code")
    league = _xml_get(root, "league_name")
    home = _xml_get(root, "team_name_h")
    away = _xml_get(root, "team_name_c")
    score = _xml_get(root, "score")
    spread = _xml_get(root, "spread")
    ioratio = _xml_get(root, "ioratio")

    return OddsRow(
        market=market_name,
        side=side,
        spread=spread,
        ioratio=ioratio,
        score=score,
        league=league,
        home=home,
        away=away,
        code=code,
    )

def print_table(title: str, rows: List[OddsRow]) -> None:
    print("\n" + title)
    print("-" * len(title))

    # 表头
    headers = ["玩法", "方向", "盘口", "水位", "比分", "返回码"]
    widths  = [6,   6,    12,   8,    8,     8]

    def fmt_line(cols: List[str]) -> str:
        out = ""
        for s, w in zip(cols, widths):
            out += (s if s is not None else "").ljust(w)
        return out

    print(fmt_line(headers))
    print(fmt_line(["-" * w for w in widths]))

    for r in rows:
        if r.raw_note:
            # 例如 CheckEMNU / loading High
            print(fmt_line([r.market, r.side, "", "", "", ""]) + f"  NOTE: {r.raw_note}")
        else:
            print(fmt_line([r.market, r.side, r.spread, r.ioratio, r.score, r.code]))

def fetch_market(cookie: str, gid: str, uid: str, ver: str, langx: str, odd_f_type: str,
                 gtype: str, wtype: str, market_name: str, sides: List[str]) -> List[OddsRow]:
    rows: List[OddsRow] = []
    for side in sides:
        data = {
            "p": "FT_order_view",
            "uid": uid,
            "ver": ver,
            "langx": langx,
            "odd_f_type": odd_f_type,
            "gid": gid,
            "gtype": gtype,
            "wtype": wtype,
            "chose_team": side,
        }
        try:
            txt = http_post(cookie, data, ver)
            rows.append(parse_ft_order_view(txt, market_name, side))
        except Exception as e:
            rows.append(OddsRow(
                market=market_name, side=side, spread="", ioratio="", score="",
                league="", home="", away="", code="", raw_note=f"ERROR: {e}"
            ))
    return rows

def main():
    ap = argparse.ArgumentParser(description="按 gid 拉取 大小球(ROU) + 让球(RE)（FT_order_view）并表格输出")
    ap.add_argument("--gid", required=True, help="比赛 gid，例如 8527105")
    ap.add_argument("--uid", required=True, help="你的 uid，例如 kg1dd0spm39084258l130870b0")
    ap.add_argument("--ver", default=DEFAULT_VER, help=f"ver（默认 {DEFAULT_VER}）")
    ap.add_argument("--langx", default="zh-cn", help="语言（默认 zh-cn）")
    ap.add_argument("--odd_f_type", default="H", help="odd_f_type（默认 H）")
    ap.add_argument("--gtype", default="FT", help="球类（默认 FT=足球）")
    ap.add_argument("--cookie_env", default="MOS_COOKIE", help="读取 cookie 的环境变量名（默认 MOS_COOKIE）")
    ap.add_argument("--sides", default="C,H", help="要查询的两边，默认 C,H（用逗号分隔）")
    args = ap.parse_args()

    cookie = os.environ.get(args.cookie_env, "").strip()
    if not cookie:
        print(f"缺少 Cookie：请先设置环境变量 {args.cookie_env}（整段 Cookie）。")
        print(f"例：export {args.cookie_env}='protocolstr=...; CookieChk=...; login_39084258=...;'")
        sys.exit(1)

    sides = [s.strip() for s in args.sides.split(",") if s.strip()]
    if not sides:
        sides = ["C", "H"]

    # 取两类玩法
    all_rows: List[OddsRow] = []
    all_rows += fetch_market(cookie, args.gid, args.uid, args.ver, args.langx, args.odd_f_type, args.gtype,
                             wtype="ROU", market_name="大小球", sides=sides)
    all_rows += fetch_market(cookie, args.gid, args.uid, args.ver, args.langx, args.odd_f_type, args.gtype,
                             wtype="RE", market_name="让球", sides=sides)

    # 标题（从任意一条有效 XML 里拿队名）
    title = f"gid={args.gid}"
    for r in all_rows:
        if r.league and r.home and r.away:
            title = f"{r.league}  {r.home} vs {r.away}   (gid={args.gid})"
            break

    print_table(title, all_rows)

    print("\n提示：方向 side=C/H 的含义（主/客 或 大/小）不同站点会变化。")
    print("你对照网页看一下：哪个 side 对应网页左边/右边，我再帮你把 C/H 改成“主/客”“大/小”的中文。")

if __name__ == "__main__":
    main()