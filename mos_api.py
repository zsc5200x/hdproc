#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
from typing import Dict, Any, List, Optional
import requests
import xml.etree.ElementTree as ET
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="MOS Odds API", version="1.0")

BASE_URL = "https://mos077.com/transform.php"
DEFAULT_VER = "2026-01-29-flagChg_135"

def post_transform(ver: str, data: Dict[str, str]) -> str:
    cookie = os.environ.get("MOS_COOKIE", "").strip()
    if not cookie:
        raise HTTPException(status_code=500, detail="MOS_COOKIE 环境变量未设置")

    url = f"{BASE_URL}?ver={ver}"
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://mos077.com",
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie,
    }
    r = requests.post(url, headers=headers, data=data, timeout=12)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"上游 HTTP {r.status_code}")
    return r.text

def xml_to_dict(root: ET.Element) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for child in list(root):
        tag = child.tag
        txt = (child.text or "").strip()
        out[tag] = txt
    return out

def parse_game_more_xml(xml_text: str) -> Dict[str, Any]:
    xml_text = (xml_text or "").strip()
    if not xml_text.startswith("<?xml"):
        # 上游偶尔会回一些字符串/HTML
        return {"ok": False, "raw": xml_text[:2000]}

    root = ET.fromstring(xml_text)

    # 你的响应里是 <game ...> ... </game>，也可能外层包着别的节点
    game = root.find(".//game")
    if game is None:
        # 兜底：有些版本可能直接就是 game
        if root.tag == "game":
            game = root
        else:
            return {"ok": False, "raw": xml_text[:2000], "reason": "找不到 <game> 节点"}

    d = xml_to_dict(game)

    # 通用信息
    meta = {
        "gid": d.get("gid"),
        "datetime": d.get("datetime"),
        "league": d.get("league"),
        "lid": d.get("lid"),
        "home": d.get("team_h"),
        "away": d.get("team_c"),
        "score": d.get("score") or "",
        "re_time": d.get("re_time") or "",
    }

    # ---------------------------
    # 解析 大小球 (ROU*)
    # 规律：ratio_rou{X}o / ratio_rou{X}u + ior_ROU{X}O / ior_ROU{X}U
    # 其中 {X} 可能是 H/C/... 代表不同盘组（主盘/附加盘组）
    # 我们把同一组内不同 line 都收集出来
    # ---------------------------
    ou_lines: List[Dict[str, Any]] = []

    ratio_rou_o = {}
    ratio_rou_u = {}
    for k, v in d.items():
        m1 = re.fullmatch(r"ratio_rou([a-z]+)o", k, flags=re.IGNORECASE)
        m2 = re.fullmatch(r"ratio_rou([a-z]+)u", k, flags=re.IGNORECASE)
        if m1:
            ratio_rou_o[m1.group(1).upper()] = v
        elif m2:
            ratio_rou_u[m2.group(1).upper()] = v

    ior_rou_o = {}
    ior_rou_u = {}
    for k, v in d.items():
        m1 = re.fullmatch(r"ior_ROU([A-Z0-9]+)O", k)
        m2 = re.fullmatch(r"ior_ROU([A-Z0-9]+)U", k)
        if m1:
            ior_rou_o[m1.group(1).upper()] = v
        elif m2:
            ior_rou_u[m2.group(1).upper()] = v

    # 组合：同一组 key（比如 HO / HU 里 group=H；CO/CU group=C）
    # 我们用 group=首字母或前缀来对齐。最稳是：从 ior 的 key 里取末尾字母前的部分作为组。
    groups = set()
    for kk in ior_rou_o.keys():
        # 例如 HO -> H, CO -> C
        groups.add(kk[:-1] if len(kk) > 1 else kk)
    for kk in ior_rou_u.keys():
        groups.add(kk[:-1] if len(kk) > 1 else kk)

    for g in sorted(groups):
        # 盘口线位：优先从 ratio_rou.. 取
        line_o = ratio_rou_o.get(g, "")
        line_u = ratio_rou_u.get(g, "")
        # 水位：从 ior_ROU{g}O/U 取（有些站点 ior key 可能是 HO/HU）
        over = ior_rou_o.get(f"{g}O".replace("OO", "O"), "")  # 兜底
        under = ior_rou_u.get(f"{g}U".replace("UU", "U"), "")
        # 更通用的取法：如果 g="H"，实际 key 可能是 "HO"/"HU"
        if not over:
            over = ior_rou_o.get(f"{g}O".replace("O", "O"), "") or ior_rou_o.get(f"{g}O".replace("O", "O"), "")
        # 直接按常见 HO/HU, CO/CU 再兜底
        if not over:
            over = ior_rou_o.get(f"{g}O".replace("O", "O"), "") or ior_rou_o.get(f"{g}O", "")
        if not under:
            under = ior_rou_u.get(f"{g}U".replace("U", "U"), "") or ior_rou_u.get(f"{g}U", "")

        # 常见：g="H" => 盘口在 ratio_rouho / ratio_rouhu（你给的片段就是这样）
        if not line_o and not line_u:
            line_o = ratio_rou_o.get(f"{g}H".upper(), "")  # 极端兜底
            line_u = ratio_rou_u.get(f"{g}H".upper(), "")

        # 如果线位只在一边，取非空的
        line = line_o or line_u

        # 只要有任一水位就收
        if line or over or under:
            ou_lines.append({
                "group": g,
                "line": line,
                "over": over,
                "under": under
            })

    # ---------------------------
    # 解析 让球（这里你站点可能用 RE / R* 相关字段）
    # 由于你还没贴完整让球字段，我先做“自动扫描”：
    # 找出所有 ior_RE... / ratio_re... 的组合，尽量组装。
    # ---------------------------
    hc_lines: List[Dict[str, Any]] = []

    # 线位
    ratio_re = {}
    for k, v in d.items():
        m = re.fullmatch(r"ratio_re([a-z0-9]+)", k, flags=re.IGNORECASE)
        if m:
            ratio_re[m.group(1).upper()] = v

    # 水位
    ior_re = {}
    for k, v in d.items():
        m = re.fullmatch(r"ior_RE([A-Z0-9]+)", k)
        if m:
            ior_re[m.group(1).upper()] = v

    # 尝试按前缀分组（例如 H/C 两边）
    # 你以后再贴一次完整让球字段，我可以把它精确到 “主/客 + 线位”
    if ratio_re or ior_re:
        # 粗暴输出：把所有相关字段原样给出去，确保你网站能先用
        hc_lines.append({
            "note": "handicap_fields_raw",
            "ratio_re": ratio_re,
            "ior_re": ior_re
        })

    return {
        "ok": True,
        "meta": meta,
        "over_under": ou_lines,
        "handicap": hc_lines,
        "raw_keys_count": len(d),
    }

@app.get("/odds")
def odds(
    ecid: str,
    gid: Optional[str] = None,
    lid: str = "100123",
    ltype: str = "4",
    showtype: str = "live",
    isRB: str = "Y",
    filter_: str = "Main",
    gtype: str = "ft",
    uid: str = "",
    ver: str = DEFAULT_VER,
    langx: str = "zh-cn",
):
    """
    网站接口：返回 JSON
    - 必填：ecid（你 get_game_more 里有）
    - 可选：gid（仅作为返回 meta 对照用）
    """
    if not ecid:
        raise HTTPException(status_code=400, detail="缺少 ecid")

    if not uid:
        # 你可以不传，直接写死你自己的 uid；但建议传入更灵活
        uid = "kg1dd0spm39084258l130870b0"

    data = {
        "uid": uid,
        "ver": ver,
        "langx": langx,
        "p": "get_game_more",
        "gtype": gtype,
        "showtype": showtype,
        "ltype": ltype,
        "isRB": isRB,
        "lid": lid,
        "specialClick": "",
        "mode": "NORMAL",
        "from": "game_more",
        "filter": filter_,
        "ts": "0",
        "ecid": ecid,
    }

    xml_text = post_transform(ver, data)
    parsed = parse_game_more_xml(xml_text)

    # 可选校验：如果传了 gid，和返回里不一致就提示
    if gid and parsed.get("ok") and parsed.get("meta", {}).get("gid") and parsed["meta"]["gid"] != gid:
        parsed["warning"] = f"你传的 gid={gid}，但返回 meta.gid={parsed['meta']['gid']}（可能不是同一场）"

    return JSONResponse(parsed)