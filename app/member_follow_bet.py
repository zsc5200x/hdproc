import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

def load_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    # 有些复制出来可能带 BOM 或前后空白
    return p.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff").strip()

def normalize_line(s: str) -> str:
    """把盘口线格式统一：去掉多余空格"""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\\/", "/")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def pick_backend_order(backend_json: dict) -> dict:
    wagers = backend_json.get("wagers") or []
    if not wagers:
        raise ValueError("backend_signal.json 里没有 wagers")
    # 取第一条做测试
    w = wagers[0]

    gid = str(w.get("_EVENT") or w.get("GID") or "").strip()
    if not gid:
        raise ValueError("后台信号里找不到 gid（_EVENT/GID）")

    gtype = str(w.get("GTYPE") or "FT").strip()

    # 玩法：让球/大小/独赢
    market = str(w.get("WAGERSTYPE") or "").strip()

    ratio_line = normalize_line(w.get("ORDER_CON") or "")

    stake_raw = w.get("GOLD") or w.get("STAKE") or "0"
    try:
        stake = float(stake_raw)
    except Exception:
        stake = 0.0

    team_h = str(w.get("TEAM_H") or "").strip()
    team_c = str(w.get("TEAM_C") or "").strip()
    pick_team = str(w.get("ORDER_TYPE") or "").strip()

    # 推断方向：下注球队名==主队 => H，否则如果==客队 => C
    side = ""
    if pick_team and team_h and pick_team in team_h:
        side = "H"
    elif pick_team and team_c and pick_team in team_c:
        side = "C"
    else:
        # 兜底：从 SHOWTEXT 里猜（不保证）
        show = str(w.get("SHOWTEXT_ORDER_TYPE_IORATIO") or "")
        if team_h and team_h in show:
            side = "H"
        elif team_c and team_c in show:
            side = "C"

    return {
        "gid": gid,
        "gtype": gtype,
        "market": market,
        "ratio_line": ratio_line,
        "side": side,
        "stake": stake,
        "team_h": team_h,
        "team_c": team_c,
        "pick_team": pick_team,
    }

def parse_member_games(xml_text: str):
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        head = xml_text[:200].replace("\n", "\\n")
        raise ValueError(f"会员端 XML 解析失败: {e}\n返回前200字符：{head}")

    games = []
    for game in root.iter("game"):
        gid = (game.findtext("GID") or "").strip()
        if not gid:
            continue
        games.append({
            "gid": gid,
            "league": (game.findtext("LEAGUE") or "").strip(),
            "team_h": (game.findtext("TEAM_H") or "").strip(),
            "team_c": (game.findtext("TEAM_C") or "").strip(),
            "ratio_r": normalize_line(game.findtext("RATIO_R") or ""),
            "ior_rh": (game.findtext("IOR_RH") or "").strip(),
            "ior_rc": (game.findtext("IOR_RC") or "").strip(),
            "systime": (game.findtext("SYSTIME") or "").strip(),
        })
    return games

def match_order_to_member(order: dict, member_games: list) -> dict | None:
    gid = order["gid"]
    line = order["ratio_line"]

    # 注意：你后台可能显示 -0.5/1，但会员端 ratio_r 可能是 0.5/1（符号展示差异）
    # 这里先做“只比数值线”的匹配：把前面的负号去掉再比
    def line_key(x: str) -> str:
        x = normalize_line(x)
        x = x.replace("-", "").strip()
        return x

    for g in member_games:
        if g["gid"] != gid:
            continue
        if line and line_key(g["ratio_r"]) != line_key(line):
            continue

        # 方向 -> 取当前水位
        side = order["side"]
        if side == "H":
            current_odd = g["ior_rh"]
        elif side == "C":
            current_odd = g["ior_rc"]
        else:
            current_odd = ""

        return {
            "gid": gid,
            "league": g["league"],
            "team_h": g["team_h"],
            "team_c": g["team_c"],
            "ratio_line_member": g["ratio_r"],
            "ratio_line_signal": order["ratio_line"],
            "side": side,
            "stake": order["stake"],
            "current_odd": current_odd,
            "systime": g["systime"],
        }

    return None

def main():
    if len(sys.argv) != 3:
        print("用法：python match_only_test.py backend_signal.json member_markets.xml")
        sys.exit(2)

    backend_path = sys.argv[1]
    member_path = sys.argv[2]

    backend_text = load_text(backend_path)
    member_xml = load_text(member_path)

    backend_json = json.loads(backend_text)

    order = pick_backend_order(backend_json)
    games = parse_member_games(member_xml)
    matched = match_order_to_member(order, games)

    print("=== 标准化信号 ===")
    print(json.dumps(order, ensure_ascii=False, indent=2))

    print("\n=== 匹配结果 ===")
    if not matched:
        print("❌ 没匹配到：可能是 gid 不一致，或盘口线变化（例如 0.5/1 变成 1），或赛事已关闭。")
        sys.exit(1)

    print(json.dumps(matched, ensure_ascii=False, indent=2))

    print("\n=== 跟单建议（人工确认/手动下注）===")
    print(f"比赛：{matched['team_h']} vs {matched['team_c']}  联赛：{matched['league']}")
    print(f"盘口线：信号 {matched['ratio_line_signal']} | 会员端当前 {matched['ratio_line_member']}")
    print(f"方向：{matched['side']}  金额：{matched['stake']}")
    print(f"会员端当前水位：{matched['current_odd']}  (SYSTIME={matched['systime']})")
    print("\n✅ 这说明：你要的“只认方向、不认水位（取当前水位）”在匹配层面是跑通的。")

if __name__ == "__main__":
    main()
