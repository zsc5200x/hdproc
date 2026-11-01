import argparse
import json
import os
import re
import subprocess
import sys
import time
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

# 避免 macOS 系统 Python（LibreSSL）触发 urllib3 v2 的提示刷屏
warnings.filterwarnings("ignore", message=r"urllib3 v2 only supports OpenSSL.*")

# 允许从 app/tools 直接运行：把 app 目录加到 sys.path
APP_DIR = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_NAME = "HD跟单系统"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from follow_engine import (  # noqa: E402
    fetch_game_list_game_context,
    fetch_game_more_xml_text,
    make_member_session,
    member_warmup_sport_menu,
    now_iso,
    parse_split_line_avg,
)


def _read_text(node: ET.Element, names: List[str]) -> str:
    for n in names:
        try:
            t = node.findtext(n)
        except Exception:
            t = None
        if t is None:
            continue
        s = str(t).strip()
        if s:
            return s
    return ""


def _ior_text(game: ET.Element, wtype: str, side: str) -> str:
    wt_up = str(wtype or "").strip().upper()
    wt_low = wt_up.lower()
    sd_up = str(side or "").strip().upper()
    sd_low = sd_up.lower()
    if not wt_up or sd_up not in {"H", "C"}:
        return ""
    return _read_text(
        game,
        [
            f"ior_{wt_up}{sd_up}",
            f"IOR_{wt_up}{sd_up}",
            f"ior_{wt_up}{sd_low}",
            f"IOR_{wt_up}{sd_low}",
            f"ior_{wt_low}{sd_low}",
            f"IOR_{wt_low}{sd_low}",
            f"ior_{wt_low}{sd_up}",
            f"IOR_{wt_low}{sd_up}",
        ],
    )


def _is_handicap_wtype(wtype: str) -> bool:
    wt = str(wtype or "").strip().upper()
    if not wt:
        return False
    prefixes = {"A", "B", "C", "D", "E", "F"}
    base = wt[1:] if (len(wt) > 1 and wt[0] in prefixes) else wt
    return base in {"RE", "R", "HRE", "HR"}


def _is_total_wtype(wtype: str) -> bool:
    wt = str(wtype or "").strip().upper()
    if not wt:
        return False
    prefixes = {"A", "B", "C", "D", "E", "F"}
    base = wt[1:] if (len(wt) > 1 and wt[0] in prefixes) else wt
    if base in {"OU", "ROU", "FOU", "HOU", "HROU", "HFOU"}:
        return True
    # 兜底：只要以 OU 结尾，也认为是“大小盘家族”
    return bool(base.endswith("OU"))


def _describe_total_wtype(wtype: str) -> str:
    wt = str(wtype or "").strip().upper()
    if not wt:
        return "-"
    prefixes = {"A", "B", "C", "D", "E", "F"}
    base = wt
    pf = ""
    if len(wt) > 1 and wt[0] in prefixes and _is_total_wtype(wt[1:]):
        pf = wt[0]
        base = wt[1:]
    name_map = {
        "ROU": "滚球大小盘",
        "OU": "大小盘(OU)",
        "FOU": "全场大小盘(FOU)",
        "HOU": "上半场大小盘(HOU)",
        "HROU": "上半场滚球大小盘(HROU)",
        "HFOU": "上半场大小盘(HFOU)",
    }
    base_name = name_map.get(base) or ("大小盘" if base.endswith("OU") else base)
    if pf:
        return f"{base_name}·附加{pf}"
    return base_name


def _pick_total_ior_for_ratio_tag(gm_game: ET.Element, wt_up: str, ratio_tag: str, *, is_over: bool) -> Tuple[str, str]:
    """
    game_more 同一 wtype 下可能通过不同后缀存多条盘口线（o/u/ho/hu/co/cu）。
    命中哪条 ratio，就必须取对应后缀的 ior，否则会触发 1X015/betError。
    返回：(ior, used_ior_tag)
    """
    wt2 = str(wt_up or "").strip().lower()
    if not wt2:
        return "", ""
    rt = str(ratio_tag or "").strip().lower()
    is_over2 = bool(is_over)

    def find_tag(tag0: str) -> str:
        tag = str(tag0 or "").strip()
        if not tag:
            return ""
        candidates: List[str] = []
        try:
            candidates.append(tag)
            candidates.append(tag.upper())
            low = tag.lower()
            if low.startswith("ior_") and len(tag) > 4:
                candidates.append("ior_" + tag[4:].upper())
                candidates.append("IOR_" + tag[4:].upper())
                candidates.append("ior_" + tag[4:].lower())
                candidates.append("IOR_" + tag[4:].lower())
            if low.startswith("ratio_") and len(tag) > 6:
                candidates.append("ratio_" + tag[6:].lower())
                candidates.append("RATIO_" + tag[6:].upper())
        except Exception:
            candidates = [tag, tag.upper()]

        seen2 = set()
        for t in candidates:
            t2 = str(t or "").strip()
            if not t2 or t2 in seen2:
                continue
            seen2.add(t2)
            v = str((gm_game.findtext(t2) or "")).strip()
            if v and v != "0":
                return v
        return ""

    prefer: List[str] = []
    # ho/hu、co/cu：按“该盘口线所属家族”取水位
    if rt.endswith(("ho", "hu")):
        prefer.append(f"ior_{wt2}{'ho' if is_over2 else 'hu'}")
        # 部分站点不带 wtype：ior_ho / ior_hu
        prefer.append(f"ior_{'ho' if is_over2 else 'hu'}")
    elif rt.endswith(("co", "cu")):
        prefer.append(f"ior_{wt2}{'co' if is_over2 else 'cu'}")
        prefer.append(f"ior_{'co' if is_over2 else 'cu'}")
    else:
        # 主盘：优先 H/C，其次 O/U（部分站点大小盘用 O/U）
        prefer.append(f"ior_{wt2}{'c' if is_over2 else 'h'}")
        prefer.append(f"ior_{wt2}{'o' if is_over2 else 'u'}")
        # 兼容：极少数站点可能不带 wtype：ior_c/ior_h/ior_o/ior_u
        prefer.append(f"ior_{'c' if is_over2 else 'h'}")
        prefer.append(f"ior_{'o' if is_over2 else 'u'}")

    for t in prefer:
        v = find_tag(t)
        if v:
            return v, t

    # fallback：按常见顺序把能取到的都试一遍
    for t in [
        f"ior_{wt2}{'c' if is_over2 else 'h'}",
        f"ior_{wt2}{'o' if is_over2 else 'u'}",
        f"ior_{wt2}h",
        f"ior_{wt2}c",
        f"ior_{wt2}o",
        f"ior_{wt2}u",
        f"ior_{wt2}ho",
        f"ior_{wt2}hu",
        f"ior_{wt2}co",
        f"ior_{wt2}cu",
        # no-wtype fallback
        f"ior_{'c' if is_over2 else 'h'}",
        f"ior_{'o' if is_over2 else 'u'}",
        f"ior_{'ho' if is_over2 else 'hu'}",
        f"ior_{'co' if is_over2 else 'cu'}",
    ]:
        v = find_tag(t)
        if v:
            return v, t
    return "", ""


def _parse_handicap_markets_from_game_more(gm_game: ET.Element) -> Dict[str, Any]:
    strong = _read_text(gm_game, ["strong", "STRONG"]).upper()
    hstrong = _read_text(gm_game, ["hstrong", "HSTRONG"]).upper()

    markets: List[Dict[str, Any]] = []
    for child in list(gm_game):
        tag = str(child.tag or "")
        tag_low = tag.lower()
        if not tag_low.startswith("ratio_"):
            continue
        suffix_low = tag_low[len("ratio_") :].strip()
        if not suffix_low:
            continue
        wtype = suffix_low.upper()
        if not _is_handicap_wtype(wtype):
            continue
        ratio_txt = str(child.text or "").strip()
        if ratio_txt == "":
            continue

        # 让球盘：ratio 通常不带符号，用 strong/hstrong 来决定主客正负
        ratio_avg = parse_split_line_avg(ratio_txt)
        ratio_abs = abs(float(ratio_avg))
        prefixes = {"A", "B", "C", "D", "E", "F"}
        base = wtype[1:] if (len(wtype) > 1 and wtype[0] in prefixes) else wtype
        is_half = base in {"HRE", "HR"}
        st = (hstrong if is_half else strong).upper()

        home_line: Optional[float] = None
        away_line: Optional[float] = None
        if st in {"H", "C"}:
            if st == "H":
                home_line = -ratio_abs
                away_line = ratio_abs
            else:
                home_line = ratio_abs
                away_line = -ratio_abs

        markets.append(
            {
                "wtype": wtype,
                "ratio_raw": ratio_txt,
                "strong": st,
                "home_line": home_line,
                "away_line": away_line,
                "ior_H": _ior_text(gm_game, wtype, "H"),
                "ior_C": _ior_text(gm_game, wtype, "C"),
            }
        )

    markets.sort(key=lambda x: str(x.get("wtype") or ""))
    return {"strong": strong, "hstrong": hstrong, "markets": markets}


def _parse_total_markets_from_game_more(gm_game: ET.Element) -> Dict[str, Any]:
    """
    解析大小盘盘口线与水位（输出“所有可见盘口线”，而不是只匹配某一条）。
    规则参考引擎：ratio_{wtype}o/u/ho/hu/co/cu + 对应 ior_*
    """
    endings = ["ho", "hu", "co", "cu", "o", "u"]  # 注意：长后缀优先
    ratio_map: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    # key: (wtype, family, side) family: ou|h|c  side: O|U

    def add_ratio(*, wtype: str, family: str, side: str, tag: str, ratio_txt: str) -> None:
        key = (str(wtype or "").strip().upper(), str(family or "").strip().lower(), str(side or "").strip().upper())
        ratio_map[key] = {"tag": str(tag or "").strip(), "ratio": str(ratio_txt or "").strip()}

    for child in list(gm_game):
        tag = str(child.tag or "").strip()
        tag_low = tag.lower()
        if not tag_low.startswith("ratio_"):
            continue
        ratio_txt = str(child.text or "").strip()
        if ratio_txt == "":
            continue
        suf = tag_low[len("ratio_") :].strip()
        if not suf:
            continue

        # 无 wtype 的“主大小盘”：ratio_o / ratio_u
        if suf in {"o", "u"}:
            add_ratio(wtype="OU", family="ou", side=suf.upper(), tag=tag, ratio_txt=ratio_txt)
            continue
        # 无 wtype 的“上半场大小盘”：ratio_ho / ratio_hu
        if suf in {"ho", "hu"}:
            add_ratio(wtype="HOU", family="h", side=("O" if suf == "ho" else "U"), tag=tag, ratio_txt=ratio_txt)
            continue

        hit = False
        for ed in endings:
            if not suf.endswith(ed):
                continue
            core = suf[: -len(ed)]
            if not core:
                continue
            wt = str(core).upper()
            if not _is_total_wtype(wt):
                continue
            if ed in {"o", "u"}:
                add_ratio(wtype=wt, family="ou", side=ed.upper(), tag=tag, ratio_txt=ratio_txt)
            elif ed in {"ho", "hu"}:
                add_ratio(wtype=wt, family="h", side=("O" if ed == "ho" else "U"), tag=tag, ratio_txt=ratio_txt)
            else:  # co/cu
                add_ratio(wtype=wt, family="c", side=("O" if ed == "co" else "U"), tag=tag, ratio_txt=ratio_txt)
            hit = True
            break
        if hit:
            continue

    markets: List[Dict[str, Any]] = []
    # 组合成 (wtype, family) 的盘口展示
    combos = sorted({(k[0], k[1]) for k in ratio_map.keys()})
    for wtype, family in combos:
        ro = ratio_map.get((wtype, family, "O")) or {}
        ru = ratio_map.get((wtype, family, "U")) or {}
        ratio_txt = str(ro.get("ratio") or ru.get("ratio") or "").strip()
        if not ratio_txt:
            continue
        try:
            line_avg = float(parse_split_line_avg(ratio_txt))
        except Exception:
            line_avg = 0.0
        # 选一个 ratio_tag 作为 ior 对应依据（存在任一侧即可）
        ratio_tag = str(ro.get("tag") or ru.get("tag") or "").strip()
        ior_o, used_ior_o = _pick_total_ior_for_ratio_tag(gm_game, wtype, ratio_tag, is_over=True)
        ior_u, used_ior_u = _pick_total_ior_for_ratio_tag(gm_game, wtype, ratio_tag, is_over=False)

        family_label = {"ou": "主", "h": "HO/HU", "c": "CO/CU"}.get(family, family)
        markets.append(
            {
                "wtype": wtype,
                "wtype_desc": _describe_total_wtype(wtype),
                "family": family,
                "family_label": family_label,
                "ratio_raw": ratio_txt,
                "line_avg": line_avg,
                "ior_O": ior_o,
                "ior_U": ior_u,
                "ratio_tag": ratio_tag,
                "ior_tag_O": used_ior_o,
                "ior_tag_U": used_ior_u,
            }
        )

    def sort_key(x: Dict[str, Any]) -> Tuple[str, str]:
        wt = str(x.get("wtype") or "")
        fam = str(x.get("family") or "")
        return wt, fam

    markets.sort(key=sort_key)
    return {"markets": markets}


def _parse_game_more_ft_group(root: ET.Element, *, requested_gid: str) -> List[Dict[str, Any]]:
    """Return every FT board in FT_gid order, including master=N boards."""

    gid2 = str(requested_gid or "").strip()
    ft_gids: List[Dict[str, str]] = []
    for node in list(root):
        if str(node.tag or "").strip().lower() != "ft_gid":
            continue
        for item in list(node):
            if str(item.tag or "").strip().lower() != "gid":
                continue
            child_gid = str(item.get("id") or item.get("ID") or "").strip()
            if not child_gid:
                continue
            ft_gids.append(
                {
                    "gid": child_gid,
                    "master": str(item.get("master") or item.get("MASTER") or "").strip().upper(),
                }
            )
        break

    games_by_gid: Dict[str, ET.Element] = {}
    for game in root.iter("game"):
        game_gid = str((game.findtext("gid") or game.findtext("GID") or "")).strip()
        if not game_gid:
            continue
        games_by_gid.setdefault(game_gid, game)

    if ft_gids:
        ft_gid_values = {item["gid"] for item in ft_gids}
        if gid2 and gid2 not in ft_gid_values:
            return []
        out: List[Dict[str, Any]] = []
        for item in ft_gids:
            game = games_by_gid.get(item["gid"])
            if game is None:
                continue
            out.append({"gid": item["gid"], "master": item["master"] or str(game.get("master") or ""), "game": game})
        return out

    # Older/simplified responses may omit FT_gid. Preserve the exact-board
    # behavior instead of accidentally mixing corner/extra-time boards.
    exact = games_by_gid.get(gid2) if gid2 else None
    if exact is None:
        return []
    return [
        {
            "gid": gid2,
            "master": str(exact.get("master") or "").strip().upper(),
            "game": exact,
        }
    ]


def _parse_handicap_market_group(ft_group: List[Dict[str, Any]]) -> Dict[str, Any]:
    markets: List[Dict[str, Any]] = []
    for board in ft_group:
        game = board.get("game")
        if not isinstance(game, ET.Element):
            continue
        parsed = _parse_handicap_markets_from_game_more(game)
        for market in parsed.get("markets") or []:
            if not isinstance(market, dict):
                continue
            row = dict(market)
            row["gid"] = str(board.get("gid") or "")
            row["master"] = str(board.get("master") or "").upper()
            markets.append(row)
    return {
        "ft_gids": [
            {"gid": str(board.get("gid") or ""), "master": str(board.get("master") or "").upper()}
            for board in ft_group
        ],
        "markets": markets,
    }


def _parse_total_market_group(ft_group: List[Dict[str, Any]]) -> Dict[str, Any]:
    markets: List[Dict[str, Any]] = []
    for board in ft_group:
        game = board.get("game")
        if not isinstance(game, ET.Element):
            continue
        parsed = _parse_total_markets_from_game_more(game)
        for market in parsed.get("markets") or []:
            if not isinstance(market, dict):
                continue
            row = dict(market)
            row["gid"] = str(board.get("gid") or "")
            row["master"] = str(board.get("master") or "").upper()
            markets.append(row)
    return {
        "ft_gids": [
            {"gid": str(board.get("gid") or ""), "master": str(board.get("master") or "").upper()}
            for board in ft_group
        ],
        "markets": markets,
    }


def _curl_transform_post(
    *,
    base_url: str,
    ver: str,
    cookie: str,
    form: Dict[str, Any],
    timeout_s: int,
    max_attempts: int = 3,
) -> Tuple[Optional[str], str]:
    """
    使用 curl 调 transform.php（绕开部分 mac 系统 Python/LibreSSL 的 SSL 抖动）。
    返回：(text, msg)
    """
    base = str(base_url or "").strip().rstrip("/")
    v = str(ver or "").strip()
    if not base or not v:
        return None, "bad_base_or_ver"
    url = f"{base}/transform.php?ver={v}"

    data_raw = urlencode({k: "" if v2 is None else str(v2) for k, v2 in (form or {}).items()})
    cmd0 = [
        "curl",
        "-k",
        "-sS",
        url,
        "-H",
        "Accept: */*",
        "-H",
        "Content-Type: application/x-www-form-urlencoded",
        "-H",
        f"Origin: {base}",
        "-H",
        f"Referer: {base}/",
        "-H",
        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "-b",
        str(cookie or ""),
        "--data-raw",
        data_raw,
        "--max-time",
        str(max(2, int(timeout_s or 0))),
    ]

    last_err = ""
    for attempt in range(max(1, int(max_attempts or 1))):
        res = subprocess.run(cmd0, capture_output=True, text=True)
        if res.returncode == 0:
            out = (res.stdout or "").strip()
            if not out:
                last_err = "empty_response"
            else:
                return out, "ok"
        else:
            err = (res.stderr or "").strip()
            last_err = err or f"curl_rc={res.returncode}"
        # 小退避：避免被站点/网关瞬时抖动打爆
        time.sleep(0.25 + 0.15 * attempt)

    return None, f"curl_failed: {last_err[:200]}"


def _fetch_game_list_game_context_curl(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    gtype: str,
    showtype: str,
    rtype: str,
    timeout_s: int,
    gid: str,
) -> Tuple[Optional[ET.Element], str, str, str]:
    """
    curl 版本的 fetch_game_list_game_context（只用于“找 gid + 提取 lid/ecid”）
    返回：(game_node, ecid_num, lid, msg)
    """
    ts_ms = str(int(time.time() * 1000))
    form = {
        "p": "get_game_list",
        "uid": str(uid or "").strip(),
        "ver": str(ver or "").strip(),
        "langx": "zh-cn",
        "p3type": "",
        "date": "",
        "gtype": str(gtype or "").strip().lower(),
        "showtype": str(showtype or "").strip().lower() or "today",
        "rtype": str(rtype or "").strip().lower() or "r",
        "ltype": "4",
        "filter": "",
        "cupFantasy": "N",
        "sorttype": "L",
        "specialClick": "",
        "isFantasy": "N",
        "ts": ts_ms,
        "chgSortTS": ts_ms,
    }
    text, msg0 = _curl_transform_post(base_url=base_url, ver=ver, cookie=cookie, form=form, timeout_s=timeout_s)
    if not text:
        return None, "", "", f"game_list_request_failed: {msg0}"
    t = str(text or "").strip()
    if not t:
        return None, "", "", "game_list_empty_response"
    if not t.startswith("<"):
        return None, "", "", f"game_list_non_xml: {t[:120]}"
    try:
        root = ET.fromstring(t)
    except Exception as e:
        return None, "", "", f"game_list_xml_parse_failed: {type(e).__name__}: {e}"

    gid2 = str(gid or "").strip()
    for ec in root.iter("ec"):
        ecid_attr = ""
        try:
            ecid_attr = str(ec.get("id") or ec.get("ID") or "").strip()
        except Exception:
            ecid_attr = ""
        ecid_num = ""
        if ecid_attr:
            m = re.search(r"(\\d+)$", ecid_attr)
            if m:
                ecid_num = m.group(1)
        for g in ec.iter("game"):
            gid_text = str((g.findtext("GID") or g.findtext("gid") or "")).strip()
            if gid_text != gid2:
                continue
            lid = str((g.findtext("LID") or g.findtext("lid") or "")).strip()
            return g, ecid_num, lid, "ok"

    for g in root.iter("game"):
        gid_text = str((g.findtext("GID") or g.findtext("gid") or "")).strip()
        if gid_text != gid2:
            continue
        lid = str((g.findtext("LID") or g.findtext("lid") or "")).strip()
        return g, "", lid, "ok_no_ecid"

    return None, "", "", "game_not_found"


def _fetch_game_more_root_curl(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    gtype: str,
    showtype: str,
    is_live_order: bool,
    lid: str,
    ecid_num: str,
    timeout_s: int,
    filter: str = "Main",
) -> Tuple[Optional[ET.Element], str]:
    ts_ms = str(int(time.time() * 1000))
    form = {
        "p": "get_game_more",
        "uid": str(uid or "").strip(),
        "ver": str(ver or "").strip(),
        "langx": "zh-cn",
        "gtype": str(gtype or "").strip().lower() or "ft",
        "showtype": str(showtype or "").strip().lower() or "today",
        "ltype": "4",
        "isRB": "Y" if bool(is_live_order) else "N",
        "lid": str(lid or "").strip(),
        "specialClick": "",
        "mode": "NORMAL",
        "from": "game_more",
        "filter": str(filter or "Main").strip() or "Main",
        "ts": ts_ms,
        "ecid": str(ecid_num or "").strip(),
    }
    text, msg0 = _curl_transform_post(base_url=base_url, ver=ver, cookie=cookie, form=form, timeout_s=timeout_s)
    if not text:
        return None, f"game_more_request_failed: {msg0}"
    t = str(text or "").strip()
    if not t:
        return None, "game_more_empty_response"
    if not t.startswith("<"):
        return None, f"game_more_non_xml: {t[:120]}"
    try:
        root = ET.fromstring(t)
    except Exception as e:
        return None, f"game_more_xml_parse_failed: {type(e).__name__}: {e}"

    return root, "ok"


def _default_arsenal_candidates() -> List[Path]:
    """Return arsenal locations from the active runtime down to source data."""

    candidates: List[Path] = []
    env_path = str(os.environ.get("HDZZ_ARSENAL_PATH") or "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    runtime_root = str(os.environ.get("HDZZ_RUNTIME_ROOT") or "").strip()
    if runtime_root:
        candidates.append(Path(runtime_root).expanduser() / "data" / "arsenal.json")

    # On macOS the desktop runtime owns the live sessions. Source data remains
    # a last-resort fallback; development callers can select it explicitly.
    if sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / DEFAULT_RUNTIME_NAME / "data" / "arsenal.json"
        )
    candidates.append(APP_DIR / "data" / "arsenal.json")

    unique: List[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate.expanduser().resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate.expanduser())
    return unique


def _resolve_arsenal_path(raw_path: str, *, default_candidates: Optional[List[Path]] = None) -> Path:
    explicit = str(raw_path or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise SystemExit(f"指定的号池文件不存在：{path}")
        return path.resolve()

    candidates = list(default_candidates) if default_candidates is not None else _default_arsenal_candidates()
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()

    checked = "\n  - ".join(str(Path(p).expanduser()) for p in candidates) or "(无候选路径)"
    raise SystemExit(f"未找到号池文件，已检查：\n  - {checked}\n请使用 --arsenal 指定当前运行号池。")


def _member_label(member: Dict[str, Any]) -> str:
    return str(member.get("label") or member.get("username") or member.get("id") or "-").strip() or "-"


def _member_session_complete(member: Dict[str, Any]) -> bool:
    return all(str(member.get(key) or "").strip() for key in ("base_url", "ver", "uid", "cookie"))


def _load_first_online_member(arsenal_path: Path, *, member_query: str = "") -> Dict[str, Any]:
    obj = json.loads(arsenal_path.read_text(encoding="utf-8-sig") or "{}")
    members = obj.get("members") or []
    if not isinstance(members, list) or not members:
        raise SystemExit(f"未找到 members：{arsenal_path}")

    member_rows = [m for m in members if isinstance(m, dict)]
    online = [m for m in member_rows if str(m.get("status") or "").strip().lower() == "online"]

    q = str(member_query or "").strip().lower()
    if q:
        exact_hits = [
            m
            for m in member_rows
            if q
            in {
                str(m.get("id") or "").strip().lower(),
                str(m.get("label") or "").strip().lower(),
                str(m.get("username") or "").strip().lower(),
            }
        ]
        hits = exact_hits
        if not hits:
            hits = [
                m
                for m in member_rows
                if q
                in " ".join(
                    [
                        str(m.get("id") or ""),
                        str(m.get("label") or ""),
                        str(m.get("username") or ""),
                        str(m.get("base_url") or ""),
                    ]
                ).lower()
            ]
        if not hits:
            available = "、".join(_member_label(m) for m in online) or "无"
            raise SystemExit(f"指定会员不存在：{member_query}；当前在线会员：{available}")

        online_hits = [m for m in hits if str(m.get("status") or "").strip().lower() == "online"]
        if not online_hits:
            matched = "、".join(f"{_member_label(m)}({m.get('status') or '-'})" for m in hits)
            raise SystemExit(f"指定会员当前不是 online，拒绝使用：{matched}")
        if len(online_hits) > 1:
            matched = "、".join(_member_label(m) for m in online_hits)
            raise SystemExit(f"指定会员匹配不唯一：{member_query} -> {matched}；请填写完整 id、标签或用户名。")
        selected = online_hits[0]
        if not _member_session_complete(selected):
            raise SystemExit(f"指定会员会话不完整，拒绝使用：{_member_label(selected)}")
        return selected

    ready_online = [m for m in online if _member_session_complete(m)]
    if ready_online:
        return ready_online[0]
    if online:
        labels = "、".join(_member_label(m) for m in online)
        raise SystemExit(f"在线会员均缺少有效会话字段，拒绝回退到其他状态会员：{labels}")
    raise SystemExit(f"号池中没有 online 会员，拒绝使用离线或已失效账号：{arsenal_path}")


def _resolve_lid_ecid(
    *,
    session,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    gtype: str,
    gid: str,
    timeout_s: int,
    prefer_live: bool,
    transport: str = "auto",  # auto | requests | curl
    debug: bool = False,
) -> Tuple[str, str, str, str, str]:
    """
    返回：(lid, ecid_num, showtype, rtype, detail)
    """
    tr = str(transport or "auto").strip().lower()
    showtypes = ["live", "hot", "today", "early"]
    if not prefer_live:
        showtypes = ["today", "hot", "early", "live"]

    last_detail = ""
    for st in showtypes:
        # live 一般用 rb；非 live 站点可能要求 r 才返回
        rtypes = ["rb", "r"] if st == "live" else ["r", "rb"]
        for rt in rtypes:
            g = None
            ecid_num = ""
            lid = ""
            msg = ""

            # requests 路径（可选）
            if tr in {"auto", "requests"}:
                try:
                    member_warmup_sport_menu(
                        session=session,
                        base_url=base_url,
                        ver=ver,
                        uid=uid,
                        gtype=gtype,
                        showtype=st,
                        rtype=rt,
                        timeout_s=timeout_s,
                    )
                except Exception:
                    pass
                try:
                    g, ecid_num, lid, msg = fetch_game_list_game_context(
                        session=session,
                        base_url=base_url,
                        ver=ver,
                        uid=uid,
                        gtype=gtype,
                        showtype=st,
                        rtype=rt,
                        timeout_s=timeout_s,
                        gid=gid,
                    )
                except Exception as e:
                    g, ecid_num, lid, msg = None, "", "", f"game_list_request_failed: {type(e).__name__}: {e}"
                if debug:
                    print(
                        f"[{now_iso()}] resolve(req) gid={gid} st={st} rt={rt} lid={lid or '-'} ecid={ecid_num or '-'} msg={msg}",
                        flush=True,
                    )

            # curl 路径（强制/或 auto 下 requests 失败时兜底）
            need_curl = tr == "curl"
            if tr == "auto":
                low = str(msg or "").lower()
                if ("request_failed" in low) or ("ssl" in low) or ("eof" in low):
                    need_curl = True
            if need_curl:
                g2, ecid2, lid2, msg2 = _fetch_game_list_game_context_curl(
                    base_url=base_url,
                    ver=ver,
                    uid=uid,
                    cookie=cookie,
                    gtype=gtype,
                    showtype=st,
                    rtype=rt,
                    timeout_s=timeout_s,
                    gid=gid,
                )
                # 只有在 curl 真正成功命中/或 requests 完全没结果时才覆盖
                if (g is None) or (msg.startswith("game_list_request_failed")):
                    g, ecid_num, lid, msg = g2, ecid2, lid2, msg2
                if debug:
                    print(
                        f"[{now_iso()}] resolve(curl) gid={gid} st={st} rt={rt} lid={lid2 or '-'} ecid={ecid2 or '-'} msg={msg2}",
                        flush=True,
                    )

            if g and lid and ecid_num:
                return str(lid), str(ecid_num), str(st), str(rt), "ok"

            # 记录最后一次失败原因
            last_detail = f"{st}/{rt} {msg or '-'}"

    return "", "", "", "", last_detail


def _fetch_game_more_best_effort(
    *,
    session,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    gtype: str,
    gid: str,
    lid: str,
    ecid_num: str,
    timeout_s: int,
    prefer_showtype: str,
    transport: str = "auto",  # auto | requests | curl
) -> Tuple[Optional[ET.Element], Dict[str, str]]:
    """
    返回：(gm_game, meta)
    meta: showtype / rb / filter / msg
    """
    tr = str(transport or "auto").strip().lower()
    st0 = str(prefer_showtype or "").strip().lower()
    showtype_candidates = [st0] if st0 else []
    for st in ["live", "hot", "today", "early"]:
        if st not in showtype_candidates:
            showtype_candidates.append(st)

    filter_candidates = ["All", "Main"]

    last_msg = ""
    for st in showtype_candidates:
        rb_flags = [True] if st == "live" else [True, False]
        for rb_flag in rb_flags:
            rb_tag = "rb" if bool(rb_flag) else "r"
            for flt in filter_candidates:
                gm_root = None
                gm_msg = ""
                if tr in {"auto", "requests"}:
                    try:
                        xml_text, gm_msg = fetch_game_more_xml_text(
                            session=session,
                            base_url=base_url,
                            ver=ver,
                            uid=uid,
                            gtype=gtype,
                            showtype=st,
                            is_live_order=bool(rb_flag),
                            lid=lid,
                            ecid_num=ecid_num,
                            timeout_s=timeout_s,
                            filter=flt,
                        )
                        if xml_text:
                            gm_root = ET.fromstring(str(xml_text).strip())
                    except Exception as e:
                        gm_root, gm_msg = None, f"game_more_request_failed: {type(e).__name__}: {e}"

                need_curl = tr == "curl"
                if tr == "auto":
                    low = str(gm_msg or "").lower()
                    if ("request_failed" in low) or ("ssl" in low) or ("eof" in low):
                        need_curl = True
                if need_curl:
                    gm2, msg2 = _fetch_game_more_root_curl(
                        base_url=base_url,
                        ver=ver,
                        uid=uid,
                        cookie=cookie,
                        gtype=gtype,
                        showtype=st,
                        is_live_order=bool(rb_flag),
                        lid=lid,
                        ecid_num=ecid_num,
                        timeout_s=timeout_s,
                        filter=flt,
                    )
                    if (gm_root is None) or (str(gm_msg or "").startswith("game_more_request_failed")):
                        gm_root, gm_msg = gm2, msg2

                last_msg = gm_msg
                if gm_root is not None and _parse_game_more_ft_group(gm_root, requested_gid=gid):
                    return gm_root, {"showtype": st, "rb": rb_tag, "filter": str(flt), "msg": str(gm_msg)}
                if gm_root is not None:
                    last_msg = "game_not_found"
    return None, {"showtype": "", "rb": "", "filter": "", "msg": str(last_msg)}


def _format_line(v: Optional[float]) -> str:
    if v is None:
        return "-"
    try:
        if abs(float(v)) < 1e-9:
            return "0"
        return f"{float(v):g}"
    except Exception:
        return "-"


def _print_snapshot(
    *,
    gid: str,
    meta: Dict[str, str],
    market: str,
    handicap_snap: Optional[Dict[str, Any]] = None,
    total_snap: Optional[Dict[str, Any]] = None,
) -> None:
    header = f"[{now_iso()}] gid={gid} showtype={meta.get('showtype')} rb={meta.get('rb')} filter={meta.get('filter')}"
    print(header, flush=True)

    mk = str(market or "handicap").strip().lower()

    if mk in {"handicap", "both"}:
        snap = handicap_snap or {}
        ft_gids = snap.get("ft_gids") or []
        group_text = ", ".join(
            f"{str(item.get('gid') or '-')}({str(item.get('master') or '-')})"
            for item in ft_gids
            if isinstance(item, dict)
        )
        print(f"  【让球盘】FT盘口组={group_text or '-'}", flush=True)

        markets = snap.get("markets") or []
        if not markets:
            print("    (无让球盘口字段，或暂时拉不到 game_more 数据)", flush=True)
        else:
            for m in markets:
                wt = str(m.get("wtype") or "")
                market_gid = str(m.get("gid") or "-")
                master = str(m.get("master") or "-").upper()
                board_label = "主盘" if master == "Y" else "附加盘"
                ratio_raw = str(m.get("ratio_raw") or "")
                st = str(m.get("strong") or "")
                hl = _format_line(m.get("home_line"))
                al = _format_line(m.get("away_line"))
                iH = str(m.get("ior_H") or "").strip() or "-"
                iC = str(m.get("ior_C") or "").strip() or "-"
                print(
                    f"    - [{board_label}] gid={market_gid} master={master} {wt:<4} "
                    f"ratio={ratio_raw:<8} st={st or '-'}  主({hl})@{iH}  客({al})@{iC}",
                    flush=True,
                )

    if mk in {"total", "both"}:
        snap2 = total_snap or {}
        print("  【大小盘】", flush=True)
        markets2 = snap2.get("markets") or []
        if not markets2:
            print("    (无大小盘盘口字段，或暂时拉不到 game_more 数据)", flush=True)
            return
        for m in markets2:
            wt = str(m.get("wtype") or "")
            market_gid = str(m.get("gid") or "-")
            master = str(m.get("master") or "-").upper()
            board_label = "主盘" if master == "Y" else "附加盘"
            desc = str(m.get("wtype_desc") or "").strip() or "-"
            fam = str(m.get("family_label") or "").strip() or "-"
            ratio_raw = str(m.get("ratio_raw") or "")
            line_avg = m.get("line_avg")
            try:
                line_txt = f"{float(line_avg):g}" if line_avg is not None else "-"
            except Exception:
                line_txt = "-"
            iO = str(m.get("ior_O") or "").strip() or "-"
            iU = str(m.get("ior_U") or "").strip() or "-"
            print(
                f"    - [{board_label}] gid={market_gid} master={master} {wt:<5} {fam:<5} "
                f"线={ratio_raw}({line_txt})  大@{iO}  小@{iU}  [{desc}]",
                flush=True,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="实时监控指定 gid 的盘口(水位)变动（基于 get_game_list/get_game_more）")
    ap.add_argument("--gid", required=True, help="比赛 gid（例如 8517417）")
    ap.add_argument("--gtype", default="FT", help="球种：FT/BK... 默认 FT")
    ap.add_argument("--interval", type=float, default=1.0, help="刷新间隔秒（默认 1.0）")
    ap.add_argument("--timeout", type=int, default=12, help="单次接口超时秒（默认 12）")
    ap.add_argument("--prefer-live", action="store_true", help="优先按滚球(live/rb) 去找比赛上下文")
    ap.add_argument("--no-cache", action="store_true", help="禁用内部短TTL缓存（更实时，但请求更频繁）")
    ap.add_argument("--base-url", default="", help="覆盖会员 base_url（例如 https://mos077.com；默认使用弹药库内的 base_url）")
    ap.add_argument("--transport", default="auto", choices=["auto", "requests", "curl"], help="接口请求方式：auto/requests/curl（默认 auto）")
    ap.add_argument(
        "--arsenal",
        default="",
        help="arsenal.json 路径（默认优先使用 HDZZ_RUNTIME_ROOT 或桌面应用当前运行号池）",
    )
    ap.add_argument("--member", default="", help="指定在线会员（匹配 id/label/username；不存在或离线时立即退出）")
    ap.add_argument("--market", default="handicap", choices=["handicap", "total", "both"], help="监控玩法：handicap(让球)/total(大小)/both(两者)")
    ap.add_argument("--print-every", action="store_true", help="每次轮询都打印（默认仅变化时打印）")
    ap.add_argument("--debug", action="store_true", help="输出更详细的失败原因（定位 gid/lid/ecid 为什么拿不到）")
    args = ap.parse_args()

    gid = str(args.gid or "").strip()
    if not gid:
        raise SystemExit("缺少 --gid")
    gtype = str(args.gtype or "FT").strip().upper() or "FT"
    gtype_low = gtype.lower()

    if args.no_cache:
        try:
            import hdzz.core.follower.engine as eng  # type: ignore

            eng._GAME_LIST_XML_CACHE_TTL_S = 0.0
            eng._GAME_MORE_XML_CACHE_TTL_S = 0.0
        except Exception:
            pass

    arsenal_path = _resolve_arsenal_path(str(args.arsenal or ""))
    m = _load_first_online_member(arsenal_path, member_query=str(args.member or ""))

    base_url = str(args.base_url or "").strip() or str(m.get("base_url") or "").strip()
    ver = str(m.get("ver") or "").strip()
    uid = str(m.get("uid") or "").strip()
    cookie = str(m.get("cookie") or "").strip()
    proxy = str(m.get("proxy") or "").strip()
    label = str(m.get("label") or m.get("username") or m.get("id") or "").strip() or "-"

    if not (base_url and ver and uid and cookie):
        raise SystemExit("会员缺少 base_url/ver/uid/cookie，无法抓取；请先确保 member_arsenal_watch 已登录并把该会员置为 online。")

    transport = str(args.transport or "auto").strip().lower()
    print(f"[{now_iso()}] 会员号池：{arsenal_path}", flush=True)
    print(f"[{now_iso()}] 使用会员：{label} base_url={base_url} transport={transport}", flush=True)

    session = None
    if transport in {"auto", "requests"}:
        session = make_member_session(base_url=base_url, proxy=proxy, cookie=cookie)

    lid = ""
    ecid_num = ""
    showtype = "live" if bool(args.prefer_live) else "today"

    last_key = ""
    while True:
        try:
            if not (lid and ecid_num):
                lid, ecid_num, showtype, _rt, detail = _resolve_lid_ecid(
                    session=session,
                    base_url=base_url,
                    ver=ver,
                    uid=uid,
                    cookie=cookie,
                    gtype=gtype_low,
                    gid=gid,
                    timeout_s=int(args.timeout),
                    prefer_live=bool(args.prefer_live),
                    transport=transport,
                    debug=bool(args.debug),
                )
                if not (lid and ecid_num):
                    if bool(args.debug) and detail:
                        print(f"[{now_iso()}] 仍未拿到 lid/ecid（gid={gid}），最后尝试：{detail}，继续重试...", flush=True)
                    else:
                        print(f"[{now_iso()}] 仍未拿到 lid/ecid（gid={gid}），继续重试...", flush=True)
                    time.sleep(max(0.2, float(args.interval)))
                    continue
                print(f"[{now_iso()}] 已获取上下文：showtype={showtype} lid={lid} ecid={ecid_num}", flush=True)

            gm_root, meta = _fetch_game_more_best_effort(
                session=session,
                base_url=base_url,
                ver=ver,
                uid=uid,
                cookie=cookie,
                gtype=gtype_low,
                gid=gid,
                lid=lid,
                ecid_num=ecid_num,
                timeout_s=int(args.timeout),
                prefer_showtype=str(showtype or ""),
                transport=transport,
            )
            if gm_root is None:
                print(f"[{now_iso()}] get_game_more 拉取失败：{meta.get('msg')}", flush=True)
                # 可能是上下文变了（比赛从 live 掉到 today/hot/early）
                lid, ecid_num = "", ""
                time.sleep(max(0.2, float(args.interval)))
                continue

            showtype = str(meta.get("showtype") or showtype).strip().lower() or showtype
            mk = str(args.market or "handicap").strip().lower()
            ft_group = _parse_game_more_ft_group(gm_root, requested_gid=gid)
            handicap_snap = _parse_handicap_market_group(ft_group) if mk in {"handicap", "both"} else None
            total_snap = _parse_total_market_group(ft_group) if mk in {"total", "both"} else None
            key = json.dumps({"meta": meta, "mk": mk, "handicap": handicap_snap, "total": total_snap}, ensure_ascii=False, sort_keys=True)
            if args.print_every or (key != last_key):
                _print_snapshot(gid=gid, meta=meta, market=mk, handicap_snap=handicap_snap, total_snap=total_snap)
                last_key = key

            time.sleep(max(0.2, float(args.interval)))
        except KeyboardInterrupt:
            print("\n已退出。", flush=True)
            return 0
        except Exception as e:
            print(f"[{now_iso()}] 异常：{type(e).__name__}: {e}", flush=True)
            time.sleep(max(0.2, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
