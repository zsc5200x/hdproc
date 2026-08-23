import argparse
import json
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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


def settlement_rank(rec: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """
    用于合并：优先保留更“可信/完整”的结算记录。
    rank 越大越优先。
    """
    status = str(rec.get("status") or "").strip().lower()
    is_final = 1 if bool(rec.get("is_final")) else 0
    profit_known = 1 if rec.get("profit") is not None else 0
    settled = 1 if status and status not in {"pending", "unsettled", "running"} else 0
    ts = 0
    try:
        ts = int(float(rec.get("ts") or 0))
    except Exception:
        ts = 0
    return (is_final, profit_known, settled, ts)


def merge_missing_fields(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    for k in (
        "member",
        "w_id",
        "ticket_id",
        "gold",
        "win_gold",
        "profit",
        "status",
        "ball_act_ret",
        "raw_status",
        "source",
        "is_final",
    ):
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


def parse_profit(v: Any) -> Optional[float]:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _pick_first_non_empty(w: Dict[str, Any], keys: List[str]) -> Any:
    for k in keys:
        if k not in w:
            continue
        v = w.get(k)
        if v in (None, ""):
            continue
        return v
    return None


def infer_status(*, profit: Optional[float], ball_act_ret: str, raw_status: str, is_final: bool) -> str:
    rs = str(raw_status or "").strip().lower()
    b = str(ball_act_ret or "").strip()
    if b and any(x in b for x in ("取消", "作废", "無效", "无效")):
        return "cancelled"
    if rs in {"pending", "unsettled", "running", "processing"}:
        return "pending"
    if not is_final:
        return "pending"
    if profit is None:
        return "pending"
    return rs or "settled"


def normalize_wager_record(
    *,
    w: Dict[str, Any],
    member: str,
    import_ts: int,
    source: str,
    is_final: bool,
) -> Optional[Dict[str, Any]]:
    w_id = str(_pick_first_non_empty(w, ["w_id", "wId", "tid", "wager_id", "id"]) or "").strip()
    ticket_id = digits_only(_pick_first_non_empty(w, ["ticket_id", "ticketId", "w_id", "wId", "tid"]) or w_id)
    if not ticket_id:
        return None

    gold = _pick_first_non_empty(w, ["gold", "stake", "bet_gold", "golds", "bet_amount"])
    win_gold = _pick_first_non_empty(w, ["win_gold", "winGold", "profit", "winloss", "net_profit", "result_profit"])
    ball_act_ret = str(_pick_first_non_empty(w, ["ball_act_ret", "ballActRet", "ball_act"]) or "").strip()
    raw_status = str(_pick_first_non_empty(w, ["status", "w_status", "settle_status", "state"]) or "").strip()

    profit = parse_profit(win_gold)
    status = infer_status(
        profit=profit,
        ball_act_ret=ball_act_ret,
        raw_status=raw_status,
        is_final=is_final,
    )

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
        "raw_status": raw_status,
        "source": str(source or ""),
        "is_final": bool(is_final),
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


def parse_609_xml_payload(*, xml_text: str) -> Dict[str, Any]:
    text = (xml_text or "").strip()
    out: Dict[str, Any] = {"meta": {}, "wagers": []}
    if not text.startswith("<"):
        return out
    try:
        root = ET.fromstring(text)
    except Exception:
        return out

    meta: Dict[str, str] = {}
    wagers: List[Dict[str, Any]] = []
    for node in list(root):
        tag = str(node.tag or "")
        if tag == "wagers":
            rec: Dict[str, Any] = {}
            tid_attr = node.attrib.get("tid")
            if tid_attr:
                rec["tid"] = tid_attr
                rec["w_id"] = tid_attr
            for child in list(node):
                ctag = str(child.tag or "")
                if not ctag:
                    continue
                rec[ctag] = (child.text or "").strip()
            wagers.append(rec)
            continue
        meta[tag] = (node.text or "").strip()

    out["meta"] = meta
    out["wagers"] = wagers
    return out


_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def parse_history_dates_from_xml(*, xml_text: str) -> List[str]:
    text = (xml_text or "").strip()
    if not text.startswith("<"):
        return []
    try:
        root = ET.fromstring(text)
    except Exception:
        return []

    out: List[str] = []
    seen: set = set()

    def add_date_token(raw: Any) -> None:
        s = str(raw or "").strip()
        if not s:
            return
        m = _DATE_RE.search(s)
        if not m:
            return
        d = m.group(1)
        if d in seen:
            return
        seen.add(d)
        out.append(d)

    for tag in ("allDateValue", "dateBar", "selDate"):
        node = root.find(tag)
        if node is None:
            continue
        txt = str(node.text or "").strip()
        if not txt:
            continue
        for part in txt.split("|"):
            add_date_token(part)

    for h in root.findall("history"):
        d = h.findtext("date")
        add_date_token(d)

    return out


def recent_dates(*, days: int) -> List[str]:
    d = max(1, int(days or 1))
    now = datetime.now()
    out: List[str] = []
    for i in range(d):
        out.append((now - timedelta(days=i)).strftime("%Y-%m-%d"))
    return out


def member_get_today_wagers(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str,
    ls: str = "g",
    sel_gtype: str = "ALL",
    chk_cw: str = "N",
    db_slow: str = "N",
    timeout_s: int = 20,
) -> Dict[str, Any]:
    base = str(base_url or "").rstrip("/")
    url = f"{base}/transform.php?ver={ver}"
    form = {
        "ver": ver,
        "p": "get_today_wagers",
        "uid": uid,
        "langx": "zh-cn",
        "LS": str(ls or "g"),
        "selGtype": str(sel_gtype or "ALL"),
        "chk_cw": str(chk_cw or "N"),
        "ts": str(now_ms()),
        "format": "json",
        "db_slow": str(db_slow or "N"),
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


def member_post_xml_api(
    *,
    base_url: str,
    ver: str,
    cookie: str,
    proxy: str,
    form: Dict[str, Any],
    timeout_s: int = 20,
) -> Dict[str, Any]:
    base = str(base_url or "").rstrip("/")
    url = f"{base}/transform.php?ver={ver}"
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    }
    if cookie:
        headers["cookie"] = cookie

    sess = requests.Session()
    sess.trust_env = False
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = sess.post(url, data=form, headers=headers, timeout=timeout_s, proxies=proxies, verify=False)
    r.raise_for_status()
    text = (r.text or "").strip()
    if not text:
        return {"ok": False, "error": "empty_response", "raw": ""}
    if text.startswith("<"):
        return {"ok": True, "format": "xml", "data": text}
    return {"ok": False, "error": "non_xml_response", "raw": text[:240]}


def member_get_history_data(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str,
    timeout_s: int = 20,
) -> Dict[str, Any]:
    form = {
        "p": "get_history_data",
        "uid": uid,
        "langx": "zh-cn",
        "gtype": "ALL",
        "isAll": "Y",
        "startdate": "",
        "enddate": "",
        "filter": "N",
    }
    return member_post_xml_api(
        base_url=base_url,
        ver=ver,
        cookie=cookie,
        proxy=proxy,
        form=form,
        timeout_s=timeout_s,
    )


def member_history_switch(
    *,
    base_url: str,
    ver: str,
    uid: str,
    cookie: str,
    proxy: str,
    today_gmt: str,
    timeout_s: int = 20,
) -> Dict[str, Any]:
    form = {
        "p": "history_switch",
        "uid": uid,
        "langx": "zh-cn",
        "LS": "c",
        "today_gmt": str(today_gmt or ""),
        "gtype": "ALL",
        "tmp_flag": "Y",
    }
    return member_post_xml_api(
        base_url=base_url,
        ver=ver,
        cookie=cookie,
        proxy=proxy,
        form=form,
        timeout_s=timeout_s,
    )


def _extract_wagers_from_json_payload(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []

    for k in ("wagers", "rows", "list", "records"):
        v = data.get(k)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]

    for v in data.values():
        if isinstance(v, dict):
            for k in ("wagers", "rows", "list", "records"):
                vv = v.get(k)
                if isinstance(vv, list):
                    return [x for x in vv if isinstance(x, dict)]
    return []


SETTLEMENT_QUERY_VARIANTS: List[Dict[str, Any]] = [
    {"ls": "g", "chk_cw": "N", "source": "today_open", "is_final": False},
    {"ls": "g", "chk_cw": "Y", "source": "today_closed", "is_final": True},
    {"ls": "h", "chk_cw": "Y", "source": "history_closed", "is_final": True},
]

HISTORY_SCAN_MAX_DATES = 8
HISTORY_SCAN_RECENT_DAYS = 7


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

        member_ok = False
        open_records_by_tid: Dict[str, Dict[str, Any]] = {}
        for q in SETTLEMENT_QUERY_VARIANTS:
            try:
                resp = member_get_today_wagers(
                    base_url=base_url,
                    ver=ver,
                    uid=uid,
                    cookie=cookie,
                    proxy=proxy,
                    ls=str(q.get("ls") or "g"),
                    chk_cw=str(q.get("chk_cw") or "N"),
                    timeout_s=20,
                )
            except Exception as e:
                if verbose and str(q.get("chk_cw") or "N") == "N":
                    print(f"[settlement] {member_name} request_failed: {e}", flush=True)
                continue

            if not resp.get("ok"):
                continue
            member_ok = True

            fmt = str(resp.get("format") or "")
            wagers: List[Dict[str, Any]] = []
            if fmt == "json":
                wagers = _extract_wagers_from_json_payload(resp.get("data"))
            elif fmt == "xml":
                wagers = parse_609_xml_wagers(xml_text=str(resp.get("data") or ""))

            for w in wagers:
                if not isinstance(w, dict):
                    continue
                rec = normalize_wager_record(
                    w=w,
                    member=member_name,
                    import_ts=import_ts,
                    source=str(q.get("source") or ""),
                    is_final=bool(q.get("is_final")),
                )
                if not rec:
                    continue
                tid = str(rec.get("ticket_id") or "").strip() or digits_only(rec.get("w_id") or "")
                if not tid:
                    continue
                if str(q.get("source") or "") == "today_open":
                    open_records_by_tid[tid] = dict(rec)
                idx[tid] = choose_settlement(idx.get(tid), rec)
                new_records += 1

        history_date_queue: List[str] = []
        history_date_seen: set = set()

        def enqueue_dates(dates: List[str]) -> None:
            for d in dates:
                ds = str(d or "").strip()
                if not ds or ds in history_date_seen:
                    continue
                history_date_seen.add(ds)
                history_date_queue.append(ds)

        try:
            history_data_resp = member_get_history_data(
                base_url=base_url,
                ver=ver,
                uid=uid,
                cookie=cookie,
                proxy=proxy,
                timeout_s=20,
            )
            if history_data_resp.get("ok"):
                member_ok = True
                enqueue_dates(parse_history_dates_from_xml(xml_text=str(history_data_resp.get("data") or "")))
            elif verbose:
                err = str(history_data_resp.get("error") or "")
                raw = str(history_data_resp.get("raw") or "")
                if err or raw:
                    print(f"[settlement] {member_name} get_history_data_failed: {err or raw}", flush=True)
        except Exception as e:
            if verbose:
                print(f"[settlement] {member_name} get_history_data_error: {e}", flush=True)

        enqueue_dates(recent_dates(days=HISTORY_SCAN_RECENT_DAYS))

        di = 0
        while di < len(history_date_queue) and di < HISTORY_SCAN_MAX_DATES:
            d = history_date_queue[di]
            di += 1
            try:
                sw_resp = member_history_switch(
                    base_url=base_url,
                    ver=ver,
                    uid=uid,
                    cookie=cookie,
                    proxy=proxy,
                    today_gmt=d,
                    timeout_s=20,
                )
            except Exception as e:
                if verbose:
                    print(f"[settlement] {member_name} history_switch_failed[{d}]: {e}", flush=True)
                continue

            if not sw_resp.get("ok"):
                if verbose:
                    err = str(sw_resp.get("error") or "")
                    raw = str(sw_resp.get("raw") or "")
                    if err or raw:
                        print(f"[settlement] {member_name} history_switch_bad[{d}]: {err or raw}", flush=True)
                continue

            member_ok = True
            xml_text = str(sw_resp.get("data") or "")
            payload = parse_609_xml_payload(xml_text=xml_text)
            enqueue_dates(parse_history_dates_from_xml(xml_text=xml_text))

            wagers_hist = payload.get("wagers")
            if not isinstance(wagers_hist, list):
                wagers_hist = []
            for w in wagers_hist:
                if not isinstance(w, dict):
                    continue
                rec = normalize_wager_record(
                    w=w,
                    member=member_name,
                    import_ts=import_ts,
                    source="history_switch",
                    is_final=True,
                )
                if not rec:
                    continue
                tid = str(rec.get("ticket_id") or "").strip() or digits_only(rec.get("w_id") or "")
                if not tid:
                    continue
                idx[tid] = choose_settlement(idx.get(tid), rec)
                new_records += 1

        # 同一轮导入里，只要票据仍出现在 today_open（开放注单）中，就强制保留 pending。
        # 这样可以避免“未结算单被短暂判成已赢/已输”造成报表偏差。
        for tid, open_rec in open_records_by_tid.items():
            forced = dict(open_rec)
            forced["ts"] = int(import_ts)
            forced["status"] = "pending"
            forced["is_final"] = False
            forced["source"] = "today_open"
            old = idx.get(tid)
            if isinstance(old, dict):
                forced = merge_missing_fields(forced, old)
            idx[tid] = forced

        if member_ok:
            ok_members += 1

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
        rec = normalize_wager_record(
            w=w,
            member=member,
            import_ts=import_ts,
            source="xml_fixture",
            is_final=True,
        )
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
