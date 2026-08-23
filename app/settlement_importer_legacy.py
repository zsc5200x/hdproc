import argparse
import json
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


def now_ts() -> int:
    return int(time.time())


def now_ms() -> int:
    return int(time.time() * 1000)


def today_ymd() -> str:
    return datetime.now().strftime("%Y%m%d")


def digits_only(v: Any) -> str:
    s = str(v or "")
    return "".join(ch for ch in s if ch.isdigit())


def read_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else dict(default)
    except Exception:
        return dict(default)


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except Exception:
        return


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex}")
    tmp.write_text(text, encoding="utf-8")

    last_err: Optional[Exception] = None
    for i in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 + i * 0.02)
        except Exception as e:
            last_err = e
            break
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    if last_err:
        raise last_err


def settlement_rank(rec: Dict[str, Any]) -> Tuple[int, int, int]:
    """
    用于合并：优先保留更“可信/完整”的结算记录。
    rank 越大越优先。
    """
    status = str(rec.get("status") or "").strip().lower()
    profit_known = 1 if rec.get("profit") is not None else 0
    settled = 1 if status and status not in {"pending", "unsettled", "running"} else 0
    ts = 0
    try:
        ts = int(float(rec.get("ts") or 0))
    except Exception:
        ts = 0
    return (profit_known, settled, ts)


def merge_missing_fields(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("member", "w_id", "ticket_id", "gold", "win_gold", "profit", "status", "ball_act_ret"):
        if dst.get(k) in (None, "", [], {}):
            if src.get(k) not in (None, "", [], {}):
                dst[k] = src.get(k)
    return dst


def choose_settlement(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Dict[str, Any]:
    if old is None:
        return new
    ro = settlement_rank(old)
    rn = settlement_rank(new)
    if rn > ro:
        return merge_missing_fields(dict(new), old)
    if ro > rn:
        return merge_missing_fields(dict(old), new)
    # rank 相同：尽量合并字段，保持 old 的 ts 稳定
    return merge_missing_fields(dict(old), new)


def parse_profit(win_gold: Any) -> Optional[float]:
    s = str(win_gold or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def infer_status(*, profit: Optional[float], ball_act_ret: str) -> str:
    b = str(ball_act_ret or "").strip()
    if profit is None:
        return "pending"
    if b and any(x in b for x in ("取消", "作废", "無效", "无效")):
        return "cancelled"
    return "settled"


def normalize_wager_record(*, w: Dict[str, Any], member: str, import_ts: int) -> Optional[Dict[str, Any]]:
    w_id = str(w.get("w_id") or w.get("wId") or w.get("tid") or "").strip()
    ticket_id = digits_only(w.get("ticket_id") or w.get("ticketId") or w_id)
    if not ticket_id:
        return None

    gold = w.get("gold")
    win_gold = w.get("win_gold")
    ball_act_ret = str(w.get("ball_act_ret") or w.get("ballActRet") or "").strip()

    profit = parse_profit(win_gold)
    status = infer_status(profit=profit, ball_act_ret=ball_act_ret)

    out: Dict[str, Any] = {
        "ts": int(import_ts),
        "member": str(member or ""),
        "ticket_id": ticket_id,
        "w_id": w_id,
        "gold": gold,
        "win_gold": win_gold,
        "profit": profit,
        "status": status,
        "ball_act_ret": ball_act_ret,
    }
    return out


def parse_609_xml_wagers(*, xml_text: str) -> List[Dict[str, Any]]:
    text = (xml_text or "").strip()
    if not text.startswith("<"):
        return []
    try:
        root = ET.fromstring(text)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for node in list(root):
        if str(node.tag) != "wagers":
            continue
        rec: Dict[str, Any] = {}
        tid_attr = node.attrib.get("tid")
        if tid_attr:
            rec["tid"] = tid_attr
            rec["w_id"] = tid_attr
        for child in list(node):
            if not child.tag:
                continue
            rec[str(child.tag)] = (child.text or "").strip()
        out.append(rec)
    return out


def member_get_today_wagers(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str,
    timeout_s: int = 20,
) -> Dict[str, Any]:
    base = str(base_url or "").rstrip("/")
    url = f"{base}/transform.php?ver={ver}"
    form = {
        "ver": ver,
        "p": "get_today_wagers",
        "uid": uid,
        "langx": "zh-cn",
        "LS": "g",
        "selGtype": "ALL",
        "chk_cw": "N",
        "ts": str(now_ms()),
        "format": "json",
        "db_slow": "N",
    }
    headers = {
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
    if cookie:
        headers["cookie"] = cookie

    sess = requests.Session()
    sess.trust_env = False
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = sess.post(url, data=form, headers=headers, timeout=timeout_s, proxies=proxies, verify=False)
    r.raise_for_status()
    text = r.text or ""
    s = text.strip()
    if not s:
        return {"ok": False, "error": "empty_response", "raw": ""}
    try:
        obj = json.loads(s)
        return {"ok": True, "format": "json", "data": obj}
    except Exception:
        if s.startswith("<"):
            return {"ok": True, "format": "xml", "data": s}
        return {"ok": False, "error": "non_json_response", "raw": s[:200]}


def load_existing_settlements(path: Path) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for obj in iter_jsonl(path):
        tid = digits_only(obj.get("ticket_id") or obj.get("w_id") or obj.get("tid") or "")
        if not tid:
            continue
        idx[tid] = choose_settlement(idx.get(tid), obj)
    return idx


def write_settlements_jsonl(path: Path, idx: Dict[str, Dict[str, Any]]) -> None:
    items = list(idx.items())
    items.sort(key=lambda kv: kv[0])
    lines: List[str] = []
    for _, rec in items:
        lines.append(json.dumps(rec, ensure_ascii=False))
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def import_settlements_once(*, data_dir: Path, interval_s: float = 60.0, verbose: bool = True) -> None:
    arsenal_path = data_dir / "arsenal.json"
    arsenal = read_json_file(arsenal_path, default={"members": []})
    members = arsenal.get("members")
    if not isinstance(members, list):
        members = []

    out_path = data_dir / "settlements" / f"wagers_{today_ymd()}.jsonl"
    idx = load_existing_settlements(out_path) if out_path.exists() else {}

    import_ts = now_ts()
    ok_members = 0
    new_records = 0
    for m in members:
        if not isinstance(m, dict):
            continue
        if str(m.get("status") or "").strip().lower() != "online":
            continue
        base_url = str(m.get("base_url") or "").strip()
        ver = str(m.get("ver") or "").strip()
        uid = str(m.get("uid") or "").strip()
        cookie = str(m.get("cookie") or "").strip()
        proxy = str(m.get("proxy") or "").strip()
        member_name = str(m.get("label") or m.get("username") or "").strip() or "unknown"
        if not (base_url and ver and uid and cookie):
            continue

        try:
            resp = member_get_today_wagers(base_url=base_url, ver=ver, uid=uid, cookie=cookie, proxy=proxy)
        except Exception as e:
            if verbose:
                print(f"[settlement] {member_name} request_failed: {e}", flush=True)
            continue

        if not resp.get("ok"):
            if verbose:
                print(f"[settlement] {member_name} bad_response: {resp.get('error')}", flush=True)
            continue

        ok_members += 1
        fmt = str(resp.get("format") or "")
        if fmt == "json":
            data = resp.get("data") or {}
            wagers = data.get("wagers") if isinstance(data, dict) else None
            if not isinstance(wagers, list):
                continue
            for w in wagers:
                if not isinstance(w, dict):
                    continue
                rec = normalize_wager_record(w=w, member=member_name, import_ts=import_ts)
                if not rec:
                    continue
                tid = str(rec.get("ticket_id") or "").strip() or digits_only(rec.get("w_id") or "")
                if not tid:
                    continue
                idx[tid] = choose_settlement(idx.get(tid), rec)
                new_records += 1
            continue

        if fmt == "xml":
            raw_xml = str(resp.get("data") or "")
            wagers0 = parse_609_xml_wagers(xml_text=raw_xml)
            for w in wagers0:
                rec = normalize_wager_record(w=w, member=member_name, import_ts=import_ts)
                if not rec:
                    continue
                tid = str(rec.get("ticket_id") or "").strip() or digits_only(rec.get("w_id") or "")
                if not tid:
                    continue
                idx[tid] = choose_settlement(idx.get(tid), rec)
                new_records += 1
            continue

    write_settlements_jsonl(out_path, idx)
    if verbose:
        print(
            f"[settlement] ok_members={ok_members} records_seen={new_records} saved={len(idx)} file={out_path.as_posix()}",
            flush=True,
        )


def import_from_xml_file(*, xml_path: Path, data_dir: Path, member: str = "fixture") -> Path:
    text = xml_path.read_text(encoding="utf-8-sig")
    wagers = parse_609_xml_wagers(xml_text=text)
    import_ts = now_ts()
    idx: Dict[str, Dict[str, Any]] = {}
    for w in wagers:
        rec = normalize_wager_record(w=w, member=member, import_ts=import_ts)
        if not rec:
            continue
        tid = str(rec.get("ticket_id") or "").strip() or digits_only(rec.get("w_id") or "")
        if not tid:
            continue
        idx[tid] = choose_settlement(idx.get(tid), rec)

    out_path = data_dir / "settlements" / f"wagers_fixture_{today_ymd()}.jsonl"
    write_settlements_jsonl(out_path, idx)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto import member wagers settlements into data/settlements/*.jsonl")
    ap.add_argument("--data-dir", default="data", help="state dir (contains arsenal.json)")
    ap.add_argument("--interval", type=float, default=60.0, help="loop interval seconds")
    ap.add_argument("--once", action="store_true", help="run once then exit")
    ap.add_argument("--from-xml", default="", help="offline: parse 609 XML file and write settlements jsonl")
    args = ap.parse_args()

    data_dir = Path(str(args.data_dir or "data")).resolve()
    if str(args.from_xml or "").strip():
        out = import_from_xml_file(xml_path=Path(str(args.from_xml)).resolve(), data_dir=data_dir)
        print(f"[settlement] wrote fixture: {out.as_posix()}", flush=True)
        return 0

    while True:
        try:
            import_settlements_once(data_dir=data_dir)
        except Exception as e:
            print(f"[settlement] loop_error: {e}", flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, float(args.interval or 60.0)))


if __name__ == "__main__":
    raise SystemExit(main())

