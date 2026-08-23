import json
import sys
from pathlib import Path

# 允许从 app/tools 直接运行：把 app 目录加到 sys.path
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from follow_engine import (
    BetParams,
    encode_total_line,
    make_member_session,
    try_refresh_total_odds_via_game_more,
)


def _load_first_online_member(arsenal_path: Path) -> dict:
    obj = json.loads(arsenal_path.read_text(encoding="utf-8"))
    members = obj.get("members") or []
    if not isinstance(members, list) or not members:
        raise SystemExit(f"未找到 members：{arsenal_path}")
    for m in members:
        if isinstance(m, dict) and str(m.get("status") or "").strip().lower() == "online":
            return m
    return members[0] if isinstance(members[0], dict) else {}


def main() -> int:
    gid = "8514741"
    want_total_abs = 1.25
    is_live_order = True

    arsenal_path = Path("app/data/arsenal.json")
    m = _load_first_online_member(arsenal_path)

    base_url = str(m.get("base_url") or "").strip()
    ver = str(m.get("ver") or "").strip()
    uid = str(m.get("uid") or "").strip()
    cookie = str(m.get("cookie") or "").strip()
    proxy = str(m.get("proxy") or "").strip()

    if not (base_url and ver and uid and cookie):
        raise SystemExit("会员缺少 base_url/ver/uid/cookie，无法做在线回归；请先确保 member_arsenal_watch 已登录。")

    session = make_member_session(base_url=base_url, proxy=proxy, cookie=cookie)
    con, ratio = encode_total_line(float(want_total_abs), is_over=False)
    params = BetParams(
        gid=gid,
        gtype="FT",
        wtype="ROU",
        rtype="ROUH",
        chose_team="H",
        ioratio="0",
        con=con,
        ratio=ratio,
        f="1R",
    )

    ok, used_wtype, msg = try_refresh_total_odds_via_game_more(
        session=session,
        base_url=base_url,
        ver=ver,
        uid=uid,
        params=params,
        is_live_order=is_live_order,
        want_total_abs=float(want_total_abs),
        timeout_s=30,
    )

    print("ok=", ok)
    print("used_wtype=", used_wtype)
    print("msg=", msg)
    print("updated_params=", {"con": params.con, "ratio": params.ratio, "ioratio": params.ioratio, "wtype": params.wtype, "rtype": params.rtype})
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
