#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _ensure_src_on_syspath() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src_dir = repo_root / "src"
    if src_dir.exists():
        import sys

        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))


_ensure_src_on_syspath()

from hdzz.core.follower.strategy import (  # noqa: E402
    decode_handicap_line,
    decode_total_line,
    format_handicap_line_value,
    format_total_line_value,
)


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                yield obj


def _pct(xs: List[float], p: float) -> Optional[float]:
    if not xs:
        return None
    xs2 = sorted(xs)
    k = (len(xs2) - 1) * float(p)
    f = int(math.floor(k))
    c = int(math.ceil(k))
    if f == c:
        return float(xs2[f])
    return float(xs2[f] * (c - k) + xs2[c] * (k - f))


def _line_val_from_con_ratio(wtype: Any, con: Any, ratio: Any) -> Optional[float]:
    wt = str(wtype or "").strip().upper()
    if not wt:
        return None
    if wt.endswith("OU"):
        lv = decode_total_line(con, ratio)
        if lv is None:
            return None
        try:
            return abs(float(lv))
        except Exception:
            return None
    if wt.endswith("RE") or wt.endswith("R"):
        lv = decode_handicap_line(con, ratio)
        if lv is None:
            return None
        try:
            return float(lv)
        except Exception:
            return None
    return None


def _fmt_line(wtype: Any, lv: Optional[float]) -> str:
    wt = str(wtype or "").strip().upper()
    if lv is None:
        return "未知"
    try:
        x = float(lv)
    except Exception:
        return str(lv)
    if wt.endswith("OU"):
        return format_total_line_value(abs(x))
    if wt.endswith("RE") or wt.endswith("R"):
        return format_handicap_line_value(x)
    return f"{x:g}"


def main() -> int:
    ap = argparse.ArgumentParser(description="分析 follow_results.jsonl：耗时分布 + 是否存在“严格跟原线但打错盘”")
    ap.add_argument("--file", default="data/follow_results.jsonl", help="结果文件路径（默认：app/data/follow_results.jsonl 里的 data/）")
    ap.add_argument("--limit", type=int, default=0, help="只分析最近 N 条（0=全部）")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[ERROR] 未找到文件：{path}")
        return 2

    timings_keys = [
        "attempt_wall_s",
        "menu_warmup_s",
        "order_view_s",
        "odds_refresh_gl_s",
        "odds_refresh_gm_s",
        "bet_request_s",
    ]
    series: Dict[str, List[float]] = {k: [] for k in timings_keys}

    drift_any: List[Dict[str, Any]] = []
    drift_strict: List[Dict[str, Any]] = []
    total = 0

    rows = list(_iter_jsonl(path))
    if int(args.limit or 0) > 0:
        rows = rows[-int(args.limit) :]

    for rec in rows:
        total += 1
        t = rec.get("timings") if isinstance(rec.get("timings"), dict) else {}
        if isinstance(t, dict):
            for k in timings_keys:
                v = t.get(k)
                if isinstance(v, (int, float)):
                    series[k].append(float(v))

        strict_raw = rec.get("strict_line")
        strict: Optional[bool]
        if strict_raw is True:
            strict = True
        elif strict_raw is False:
            strict = False
        else:
            strict = None
        backend = rec.get("backend_params") if isinstance(rec.get("backend_params"), dict) else {}
        submit = rec.get("submit_params") if isinstance(rec.get("submit_params"), dict) else {}
        params = rec.get("params") if isinstance(rec.get("params"), dict) else {}
        wtype = params.get("wtype") or backend.get("wtype") or submit.get("wtype") or ""

        want_lv = _line_val_from_con_ratio(wtype, backend.get("con"), backend.get("ratio"))
        got_lv = _line_val_from_con_ratio(wtype, submit.get("con"), submit.get("ratio"))
        if got_lv is None:
            got_lv = _line_val_from_con_ratio(wtype, params.get("con"), params.get("ratio"))

        if want_lv is not None and got_lv is not None:
            if abs(float(want_lv) - float(got_lv)) >= 1e-6:
                row = {
                    "orderId": str(rec.get("orderId") or ""),
                    "member": str(rec.get("member") or ""),
                    "wtype": str(wtype),
                    "want": _fmt_line(wtype, want_lv),
                    "got": _fmt_line(wtype, got_lv),
                    "strict_line": strict,
                    "backend": {"con": backend.get("con"), "ratio": backend.get("ratio")},
                    "submit": {"con": submit.get("con"), "ratio": submit.get("ratio")},
                    "message": str(rec.get("message") or ""),
                }
                drift_any.append(row)
                if strict is True:
                    drift_strict.append(row)

    print(f"[INFO] 文件：{path}")
    print(f"[INFO] 记录数：{total}")
    print("")
    for k in timings_keys:
        xs = series.get(k) or []
        if not xs:
            continue
        p50 = _pct(xs, 0.5)
        p90 = _pct(xs, 0.9)
        p99 = _pct(xs, 0.99)
        print(
            f"[TIMING] {k}: n={len(xs)} p50={p50:.3f} p90={p90:.3f} p99={p99:.3f} max={max(xs):.3f}"
            if (p50 is not None and p90 is not None and p99 is not None)
            else f"[TIMING] {k}: n={len(xs)}"
        )

    print("")
    print(f"[CHECK] 后台原线 != 实际提交盘口线：{len(drift_any)} 条")
    print(f"[CHECK] 其中 strict_line=true 的打错盘：{len(drift_strict)} 条")
    if drift_any:
        print("[TOP] 示例（最多 20 条）：")
        for d in drift_any[:20]:
            print(
                f"  - order={d.get('orderId')} member={d.get('member')} strict={d.get('strict_line')} wtype={d.get('wtype')} "
                f"want={d.get('want')} got={d.get('got')} msg={d.get('message')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
