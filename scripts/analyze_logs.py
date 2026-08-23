#!/usr/bin/env python3
"""
Analyze follow_results.jsonl and generate tuning suggestions.

Outputs:
- Success/failure summary
- Error distribution
- LINE_NOT_FOUND recovery window analysis
- Split strategy effect analysis
- Suggested config JSON
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _pct(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return (float(a) / float(b)) * 100.0


def _percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    if p <= 0:
        return float(min(data))
    if p >= 100:
        return float(max(data))
    arr = sorted(float(x) for x in data)
    idx = (len(arr) - 1) * (float(p) / 100.0)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(arr[lo])
    w = idx - lo
    return float(arr[lo] * (1.0 - w) + arr[hi] * w)


def _detect_log_path(explicit_path: Optional[str], data_dir: str) -> Path:
    if explicit_path:
        return Path(explicit_path)
    candidates = [
        Path(data_dir) / "follow_results.jsonl",
        Path("app") / data_dir / "follow_results.jsonl",
        Path("app/data/follow_results.jsonl"),
        Path("data/follow_results.jsonl"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _detect_arsenal_path(explicit_path: Optional[str], data_dir: str) -> Path:
    if explicit_path:
        return Path(explicit_path)
    candidates = [
        Path(data_dir) / "arsenal.json",
        Path("app") / data_dir / "arsenal.json",
        Path("app/data/arsenal.json"),
        Path("data/arsenal.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _extract_err(row: Dict[str, Any]) -> str:
    code = str(row.get("last_err") or row.get("fast_retry_reason") or row.get("error_code") or "").strip()
    msg = str(row.get("message") or "").strip()
    if not code and "err=" in msg:
        m = re.search(r"err=([A-Za-z0-9_]+)", msg)
        if m:
            code = str(m.group(1) or "").strip()
    up = str(code or "").upper()
    if not up:
        return ""
    if up in {"1X003", "1X015"}:
        return "LINE_NOT_FOUND"
    if up.startswith("BETERROR"):
        return "BETERROR"
    return up


def _order_key(row: Dict[str, Any]) -> str:
    target = str(row.get("target") or "").strip()
    order_id = str(row.get("orderId") or row.get("order_id") or "").strip()
    return f"{target}|{order_id}" if (target or order_id) else ""


def _event_ts(row: Dict[str, Any]) -> float:
    ts_ms = _safe_float(row.get("ts_ms"), 0.0)
    if ts_ms > 0:
        return ts_ms / 1000.0
    return _safe_float(row.get("ts"), 0.0)


def _load_arsenal_roles(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {"quote": 0, "bet": 0, "both": 0, "unknown": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"quote": 0, "bet": 0, "both": 0, "unknown": 0}
    members = doc.get("members") if isinstance(doc, dict) else []
    if not isinstance(members, list):
        members = []
    out = Counter()
    for m in members:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        if role in {"quote", "bet", "both"}:
            out[role] += 1
        else:
            out["unknown"] += 1
    return {
        "quote": int(out.get("quote", 0)),
        "bet": int(out.get("bet", 0)),
        "both": int(out.get("both", 0)),
        "unknown": int(out.get("unknown", 0)),
    }


def analyze_rows(rows: List[Dict[str, Any]], arsenal_roles: Dict[str, int]) -> Dict[str, Any]:
    total = len(rows)
    ok_count = sum(1 for r in rows if bool(r.get("ok")))
    fail_count = total - ok_count

    err_counter: Counter[str] = Counter()
    for r in rows:
        if bool(r.get("ok")):
            continue
        err = _extract_err(r)
        if err:
            err_counter[err] += 1

    order_events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    split_by_order: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        k = _order_key(r)
        if not k:
            continue
        order_events[k].append(r)
        sc = _safe_int(r.get("split_count"), 0)
        sd = _safe_float(r.get("split_delay_seconds"), 0.0)
        if sc > 0:
            split_by_order[k] = {"split_count": sc, "split_delay_seconds": round(sd, 2)}

    # LINE_NOT_FOUND window analysis
    lnf_windows: List[float] = []
    lnf_dead_count = 0
    lnf_total_orders = 0
    lnf_remaining_samples: List[int] = []
    for evs in order_events.values():
        evs.sort(key=_event_ts)
        first_lnf_ts = None
        recovered = False
        for r in evs:
            err = _extract_err(r)
            if err == "LINE_NOT_FOUND":
                if first_lnf_ts is None:
                    first_lnf_ts = _event_ts(r)
                    lnf_total_orders += 1
                m = re.search(r"剩余(\d+)秒", str(r.get("message") or ""))
                if m:
                    lnf_remaining_samples.append(_safe_int(m.group(1), 0))
            if first_lnf_ts is not None and bool(r.get("ok")):
                dt = max(0.0, _event_ts(r) - first_lnf_ts)
                lnf_windows.append(dt)
                recovered = True
                break
        if first_lnf_ts is not None and not recovered:
            lnf_dead_count += 1

    # split strategy analysis (order-level)
    combo_orders: Dict[Tuple[int, float], set] = defaultdict(set)
    combo_success_orders: Dict[Tuple[int, float], set] = defaultdict(set)
    count_orders: Dict[int, set] = defaultdict(set)
    count_success_orders: Dict[int, set] = defaultdict(set)
    delay_orders: Dict[float, set] = defaultdict(set)
    delay_success_orders: Dict[float, set] = defaultdict(set)
    for k, evs in order_events.items():
        if k not in split_by_order:
            continue
        sc = int(split_by_order[k]["split_count"])
        sd = float(split_by_order[k]["split_delay_seconds"])
        combo = (sc, sd)
        combo_orders[combo].add(k)
        count_orders[sc].add(k)
        delay_orders[sd].add(k)
        if any(bool(x.get("ok")) for x in evs):
            combo_success_orders[combo].add(k)
            count_success_orders[sc].add(k)
            delay_success_orders[sd].add(k)

    combo_stats: List[Dict[str, Any]] = []
    for combo, keys in combo_orders.items():
        c, d = combo
        order_n = len(keys)
        succ_n = len(combo_success_orders.get(combo, set()))
        combo_stats.append(
            {
                "split_count": int(c),
                "split_delay_seconds": float(d),
                "orders": int(order_n),
                "success_orders": int(succ_n),
                "order_success_rate": round(_pct(succ_n, order_n), 2),
            }
        )
    combo_stats.sort(key=lambda x: (-x["order_success_rate"], x["split_delay_seconds"], x["split_count"]))

    count_stats: List[Dict[str, Any]] = []
    for c, keys in count_orders.items():
        order_n = len(keys)
        succ_n = len(count_success_orders.get(c, set()))
        count_stats.append(
            {
                "split_count": int(c),
                "orders": int(order_n),
                "success_orders": int(succ_n),
                "order_success_rate": round(_pct(succ_n, order_n), 2),
            }
        )
    count_stats.sort(key=lambda x: (-x["order_success_rate"], -x["orders"], abs(int(x["split_count"]) - 3)))

    delay_stats: List[Dict[str, Any]] = []
    for d, keys in delay_orders.items():
        order_n = len(keys)
        succ_n = len(delay_success_orders.get(d, set()))
        delay_stats.append(
            {
                "split_delay_seconds": float(d),
                "orders": int(order_n),
                "success_orders": int(succ_n),
                "order_success_rate": round(_pct(succ_n, order_n), 2),
            }
        )
    delay_stats.sort(
        key=lambda x: (
            -x["order_success_rate"],
            -x["orders"],
            abs(float(x["split_delay_seconds"]) - 0.5),
        )
    )

    # choose recommendation from sufficiently sampled groups
    min_combo_orders = max(5, int(max(1, len(order_events)) * 0.03))
    min_group_orders = max(10, int(max(1, len(order_events)) * 0.02))
    candidates = [x for x in combo_stats if int(x["orders"]) >= min_combo_orders]
    if candidates:
        best = candidates[0]
    elif combo_stats:
        best = combo_stats[0]
    else:
        best = {
            "split_count": 3,
            "split_delay_seconds": 0.5,
            "orders": 0,
            "success_orders": 0,
            "order_success_rate": 0.0,
        }

    count_candidates = [
        x
        for x in count_stats
        if int(x["orders"]) >= min_group_orders and 1 <= int(x["split_count"]) <= 5
    ]
    if count_candidates:
        selected_count = int(count_candidates[0]["split_count"])
    elif count_stats:
        selected_count = int(max(1, min(5, int(count_stats[0]["split_count"]))))
    else:
        selected_count = 3

    delay_candidates = [
        x
        for x in delay_stats
        if int(x["orders"]) >= min_group_orders and 0.0 <= float(x["split_delay_seconds"]) <= 10.0
    ]
    if delay_candidates:
        selected_delay = float(delay_candidates[0]["split_delay_seconds"])
    elif delay_stats:
        selected_delay = float(delay_stats[0]["split_delay_seconds"])
    else:
        selected_delay = 0.5
    selected_delay = max(0.5, min(10.0, round(selected_delay, 2)))

    # recommended wait_line_not_found_max_seconds
    if lnf_total_orders <= 0:
        recommended_wait = 45
    elif len(lnf_windows) >= 5:
        p75 = _percentile(lnf_windows, 75.0)
        recommended_wait = int(max(40, min(90, round(p75 + 6))))
    else:
        bad_ratio = _pct(lnf_dead_count, max(1, lnf_total_orders))
        if bad_ratio >= 50.0:
            recommended_wait = 60
        elif bad_ratio >= 20.0:
            recommended_wait = 50
        else:
            recommended_wait = 45

    quote_count = int(arsenal_roles.get("quote", 0)) + int(arsenal_roles.get("both", 0))
    lnf_rate = _pct(err_counter.get("LINE_NOT_FOUND", 0), max(1, fail_count))
    need_more_quote_members = bool(
        quote_count < 1
        or (lnf_total_orders >= 10 and _pct(lnf_dead_count, max(1, lnf_total_orders)) >= 40.0 and quote_count < 2)
        or (lnf_rate >= 8.0 and quote_count < 2)
    )

    suggestion = {
        "split_count": int(selected_count),
        "split_delay_seconds": float(selected_delay),
        "wait_line_not_found_max_seconds": int(recommended_wait),
        "need_more_quote_members": bool(need_more_quote_members),
    }

    return {
        "summary": {
            "total_records": int(total),
            "success_records": int(ok_count),
            "failed_records": int(fail_count),
            "success_rate_percent": round(_pct(ok_count, total), 2),
            "fail_rate_percent": round(_pct(fail_count, total), 2),
            "unique_orders": int(len(order_events)),
        },
        "error_distribution": dict(err_counter.most_common()),
        "line_not_found": {
            "orders_with_line_not_found": int(lnf_total_orders),
            "recovered_orders": int(len(lnf_windows)),
            "timeout_or_unrecovered_orders": int(lnf_dead_count),
            "recovery_window_seconds_p50": round(_percentile(lnf_windows, 50.0), 2) if lnf_windows else 0.0,
            "recovery_window_seconds_p75": round(_percentile(lnf_windows, 75.0), 2) if lnf_windows else 0.0,
            "recovery_window_seconds_p95": round(_percentile(lnf_windows, 95.0), 2) if lnf_windows else 0.0,
            "remaining_seconds_samples_p50": round(_percentile([float(x) for x in lnf_remaining_samples], 50.0), 2)
            if lnf_remaining_samples
            else 0.0,
        },
        "split_effect": {
            "min_combo_orders": int(min_combo_orders),
            "min_group_orders": int(min_group_orders),
            "top_combos": combo_stats[:10],
            "selected_combo": best,
            "top_split_count_groups": count_stats[:10],
            "top_split_delay_groups": delay_stats[:10],
            "selected_split_count": int(selected_count),
            "selected_split_delay_seconds": float(selected_delay),
        },
        "quote_member": {
            "roles": arsenal_roles,
            "quote_like_count": int(quote_count),
            "line_not_found_rate_in_fail_percent": round(float(lnf_rate), 2),
        },
        "suggestion": suggestion,
    }


def print_report(path: Path, arsenal_path: Path, report: Dict[str, Any]) -> None:
    summary = report["summary"]
    print("=" * 68)
    print("HDZZ FOLLOW LOG ANALYSIS")
    print("=" * 68)
    print(f"log_file: {path.as_posix()}")
    print(f"arsenal_file: {arsenal_path.as_posix()}")
    print("")

    print("[1] Summary")
    print("-" * 68)
    print(f"total_records     : {summary['total_records']}")
    print(f"success_records   : {summary['success_records']}")
    print(f"failed_records    : {summary['failed_records']}")
    print(f"success_rate      : {summary['success_rate_percent']:.2f}%")
    print(f"fail_rate         : {summary['fail_rate_percent']:.2f}%")
    print(f"unique_orders     : {summary['unique_orders']}")
    print("")

    print("[2] Error Distribution (top 12)")
    print("-" * 68)
    err_items = list(report["error_distribution"].items())[:12]
    if not err_items:
        print("no_error_code_found")
    else:
        for code, cnt in err_items:
            print(f"{code:20s} {cnt}")
    print("")

    lnf = report["line_not_found"]
    print("[3] LINE_NOT_FOUND Window")
    print("-" * 68)
    print(f"orders_with_line_not_found : {lnf['orders_with_line_not_found']}")
    print(f"recovered_orders           : {lnf['recovered_orders']}")
    print(f"timeout_or_unrecovered     : {lnf['timeout_or_unrecovered_orders']}")
    print(f"recovery_p50_seconds       : {lnf['recovery_window_seconds_p50']}")
    print(f"recovery_p75_seconds       : {lnf['recovery_window_seconds_p75']}")
    print(f"recovery_p95_seconds       : {lnf['recovery_window_seconds_p95']}")
    print("")

    split_effect = report["split_effect"]
    print("[4] Split Strategy Effect (top 5 combos)")
    print("-" * 68)
    top = split_effect.get("top_combos") or []
    if not top:
        print("no_split_combo_data")
    else:
        for row in top[:5]:
            print(
                f"split={row['split_count']}, delay={row['split_delay_seconds']}, "
                f"orders={row['orders']}, success={row['order_success_rate']:.2f}%"
            )
    print("")

    quote = report["quote_member"]
    print("[5] Quote Member")
    print("-" * 68)
    print(f"roles                     : {quote['roles']}")
    print(f"quote_like_count          : {quote['quote_like_count']}")
    print(f"line_not_found_fail_rate  : {quote['line_not_found_rate_in_fail_percent']:.2f}%")
    print("")

    print("[6] Suggested Config JSON")
    print("-" * 68)
    print(json.dumps(report["suggestion"], ensure_ascii=False, indent=2))
    print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze follow_results.jsonl and suggest tuning config")
    parser.add_argument("--data-dir", default="data", help="Data directory containing follow_results.jsonl/arsenal.json")
    parser.add_argument("--log-file", default="", help="Explicit follow_results.jsonl path")
    parser.add_argument("--arsenal-file", default="", help="Explicit arsenal.json path")
    parser.add_argument("--json-out", default="", help="Optional output path for JSON report")
    args = parser.parse_args()

    log_path = _detect_log_path(args.log_file or None, args.data_dir)
    if args.arsenal_file:
        arsenal_path = Path(args.arsenal_file)
    else:
        log_side_arsenal = log_path.parent / "arsenal.json"
        if log_side_arsenal.exists():
            arsenal_path = log_side_arsenal
        else:
            arsenal_path = _detect_arsenal_path(None, args.data_dir)
    if not log_path.exists():
        print(f"ERROR: log file not found: {log_path.as_posix()}")
        return 2

    rows = list(_iter_jsonl(log_path))
    if not rows:
        print(f"ERROR: no valid rows in log file: {log_path.as_posix()}")
        return 3

    roles = _load_arsenal_roles(arsenal_path)
    report = analyze_rows(rows, roles)
    print_report(log_path, arsenal_path, report)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report_json_saved: {out_path.as_posix()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
