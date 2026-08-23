import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from follow_engine import (
    BetParams,
    atomic_write_json,
    make_member_session,
    now_iso,
    now_ts,
    try_refresh_total_odds_via_game_list,
)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _cache_key_for_total(*, gid: str, gtype: str, wtype: str, chose_team: str, total_abs: float) -> str:
    return f"{str(gid).strip()}|{str(gtype).strip().upper()}|{str(wtype).strip().upper()}|{str(chose_team).strip().upper()}|{round(float(total_abs), 4)}"


def _parse_watch_item(obj: Dict[str, Any]) -> Optional[Tuple[str, BetParams, float, bool]]:
    """
    watch item -> (cache_key, params, want_total_abs, is_live_order)
    """
    try:
        gid = str(obj.get("gid") or "").strip()
        gtype = str(obj.get("gtype") or "FT").strip().upper() or "FT"
        wtype = str(obj.get("wtype") or "").strip().upper()
        chose_team = str(obj.get("chose_team") or "").strip().upper()
        if not (gid and wtype and chose_team):
            return None
        if not wtype.endswith("OU"):
            return None
        want_total_abs = _safe_float(obj.get("want_total_abs"), 0.0)
        if want_total_abs <= 0:
            return None
        # 这里的 con/ratio/ioratio 只是初始化；真正刷新以 get_game_list 为准
        params = BetParams(
            gid=gid,
            gtype=gtype,
            wtype=wtype,
            rtype=f"{wtype}{chose_team}",
            chose_team=chose_team,
            ioratio=str(obj.get("ioratio") or "").strip(),
            con=str(obj.get("con") or "").strip(),
            ratio=str(obj.get("ratio") or "").strip(),
            f=str(obj.get("f") or "1R").strip() or "1R",
        )
        is_live_order = bool(obj.get("live") if "live" in obj else True)
        ck = _cache_key_for_total(gid=gid, gtype=gtype, wtype=wtype, chose_team=chose_team, total_abs=want_total_abs)
        return ck, params, float(want_total_abs), is_live_order
    except Exception:
        return None


def _pick_quote_session_from_arsenal(data_dir: Path) -> Optional[Dict[str, str]]:
    """
    从弹药库里选择“指定的”role=quote 会员会话，用于报价抓水位。
    由 member_arsenal_watch 负责登录/掉线重登与会话维护（ver/uid/cookie）。
    """
    # 用户明确：报价号必须是“专门报价号”（你指定的那一个），不能自动挑任何在线会员
    quote_member_path = data_dir / "quote_member.json"
    quote_member_id = ""
    if quote_member_path.exists():
        try:
            qdoc = json.loads(quote_member_path.read_text(encoding="utf-8-sig"))
            if isinstance(qdoc, dict):
                quote_member_id = str(qdoc.get("member_id") or "").strip()
        except Exception:
            quote_member_id = ""
    if not quote_member_id:
        return None

    p = data_dir / "arsenal.json"
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    members = doc.get("members") or []
    if not isinstance(members, list):
        return None

    m0 = next((m for m in members if isinstance(m, dict) and str(m.get("id") or "").strip() == quote_member_id), None)
    if not isinstance(m0, dict):
        return None
    role = str(m0.get("role") or "bet").strip().lower()
    if role != "quote":
        return None
    if str(m0.get("status") or "").strip().lower() != "online":
        return None
    base_url = str(m0.get("base_url") or "").strip().rstrip("/")
    ver = str(m0.get("ver") or "").strip()
    uid = str(m0.get("uid") or "").strip()
    cookie = str(m0.get("cookie") or "").strip()
    if not (base_url and ver and uid and cookie):
        return None

    return {
        "source": "arsenal",
        "member_id": str(m0.get("id") or ""),
        "member_label": str(m0.get("label") or m0.get("username") or ""),
        "base_url": base_url,
        "ver": ver,
        "uid": uid,
        "cookie": cookie,
        "proxy": str(m0.get("proxy") or "").strip(),
    }


def _pick_quote_session_from_quote_session_json(data_dir: Path) -> Optional[Dict[str, str]]:
    p = data_dir / "quote_session.json"
    if not p.exists():
        return None
    qs = _read_json(p)
    base_url = str(qs.get("base_url") or "").strip().rstrip("/")
    ver = str(qs.get("ver") or "").strip()
    uid = str(qs.get("uid") or "").strip()
    cookie = str(qs.get("cookie") or "").strip()
    proxy = str(qs.get("proxy") or "").strip()
    if not (base_url and ver and uid and cookie):
        return None
    return {
        "source": "quote_session",
        "member_id": "",
        "member_label": "",
        "base_url": base_url,
        "ver": ver,
        "uid": uid,
        "cookie": cookie,
        "proxy": proxy,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--watchlist", default="odds_watchlist.jsonl")
    ap.add_argument("--cache", default="odds_cache.json")
    ap.add_argument("--state", default=".run/odds_quote_state.json")
    ap.add_argument("--cache_ttl_seconds", type=float, default=2.0)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    watch_path = data_dir / str(args.watchlist)
    cache_path = data_dir / str(args.cache)
    state_path = Path(str(args.state))
    state_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[{now_iso()}] odds_quote_watch 启动 data_dir={data_dir} watch={watch_path} cache={cache_path}", flush=True)
    print(f"[{now_iso()}] 会话来源：指定报价会员(data/quote_member.json) -> quote_session.json(兼容)", flush=True)

    state = _read_json(state_path)
    offset = int(state.get("offset") or 0)

    tracked: Dict[str, Dict[str, Any]] = {}  # cache_key -> {params, want_total_abs, live, last_seen}
    cache: Dict[str, Any] = _read_json(cache_path) if cache_path.exists() else {}
    last_session_hint = ""

    while True:
        sess_info = _pick_quote_session_from_arsenal(data_dir) or _pick_quote_session_from_quote_session_json(data_dir)
        if not sess_info:
            time.sleep(max(0.5, float(args.interval)))
            continue

        base_url = str(sess_info.get("base_url") or "").strip()
        ver = str(sess_info.get("ver") or "").strip()
        uid = str(sess_info.get("uid") or "").strip()
        cookie = str(sess_info.get("cookie") or "").strip()
        proxy = str(sess_info.get("proxy") or "").strip()
        hint = f"{sess_info.get('source')}:{sess_info.get('member_label') or sess_info.get('member_id') or base_url}"
        if hint != last_session_hint:
            last_session_hint = hint
            print(f"[{now_iso()}] 报价会话已切换 -> {hint}", flush=True)

        # tail watchlist
        if watch_path.exists():
            try:
                with watch_path.open("r", encoding="utf-8-sig") as f:
                    f.seek(max(0, offset))
                    while True:
                        line = f.readline()
                        if not line:
                            break
                        offset = f.tell()
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        parsed = _parse_watch_item(obj)
                        if not parsed:
                            continue
                        ck, params, want_total_abs, is_live = parsed
                        tracked[ck] = {
                            "params": asdict(params),
                            "want_total_abs": float(want_total_abs),
                            "live": bool(is_live),
                            "last_seen": float(now_ts()),
                        }
            except Exception:
                pass

        # round-robin：每轮只刷新一个 key，避免压爆接口
        now0 = float(now_ts())
        candidates = [
            (ck, it)
            for ck, it in tracked.items()
            if float(it.get("last_seen") or 0.0) >= (now0 - 600.0)  # 只保留最近 10 分钟的需求
        ]
        candidates.sort(key=lambda x: float(x[1].get("last_refreshed") or 0.0))
        if not candidates:
            time.sleep(max(0.1, float(args.interval)))
            continue

        ck, it = candidates[0]
        it["last_refreshed"] = float(now0)

        # 缓存 TTL：很新就不刷新
        try:
            cur = cache.get(ck) if isinstance(cache, dict) else None
        except Exception:
            cur = None
        if isinstance(cur, dict) and float(cur.get("ts") or 0.0) >= (now0 - float(args.cache_ttl_seconds)):
            time.sleep(max(0.05, float(args.interval)))
            continue

        p0 = it.get("params") or {}
        if not isinstance(p0, dict):
            time.sleep(max(0.05, float(args.interval)))
            continue
        try:
            params = BetParams(
                gid=str(p0.get("gid") or "").strip(),
                gtype=str(p0.get("gtype") or "FT").strip().upper() or "FT",
                wtype=str(p0.get("wtype") or "").strip().upper(),
                rtype=str(p0.get("rtype") or "").strip(),
                chose_team=str(p0.get("chose_team") or "").strip().upper(),
                ioratio=str(p0.get("ioratio") or "").strip(),
                con=str(p0.get("con") or "").strip(),
                ratio=str(p0.get("ratio") or "").strip(),
                f=str(p0.get("f") or "1R").strip() or "1R",
            )
            want_total_abs = float(it.get("want_total_abs") or 0.0)
        except Exception:
            time.sleep(max(0.05, float(args.interval)))
            continue

        sess = make_member_session(base_url=base_url, proxy=proxy, cookie=cookie)
        # 只做大小球报价：用 get_game_list 刷新 ioratio +（可能）con/ratio
        ok, msg = False, ""
        try:
            ok, msg = try_refresh_total_odds_via_game_list(
                session=sess,
                base_url=base_url,
                ver=ver,
                uid=uid,
                params=params,
                is_live_order=bool(it.get("live")),
                want_total_abs=float(abs(want_total_abs)),
                timeout_s=6,
                ior_hint=_safe_float(params.ioratio, 0.0),
                quick=True,
            )
        except Exception as e:
            ok, msg = False, f"quote_refresh_failed: {e}"

        if ok:
            cache[ck] = {
                "ts": float(now_ts()),
                "gid": params.gid,
                "gtype": params.gtype,
                "wtype": params.wtype,
                "chose_team": params.chose_team,
                "want_total_abs": float(abs(want_total_abs)),
                "ioratio": str(params.ioratio or ""),
                "con": str(params.con or ""),
                "ratio": str(params.ratio or ""),
                "msg": str(msg or ""),
            }
            try:
                atomic_write_json(cache_path, cache)
            except Exception:
                pass

        try:
            state_path.write_text(json.dumps({"offset": int(offset), "updated_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        time.sleep(max(0.01, float(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
