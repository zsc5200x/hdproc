#!/usr/bin/env python3
"""Offline analyzer for HTTP telemetry and sampler output."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    value = json.loads(line)
                except Exception:
                    continue
                if isinstance(value, dict):
                    yield value
    except Exception:
        return


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = max(0, min(len(values) - 1, int(round((len(values) - 1) * q))))
    return float(values[index])


def analyze(data_dir: Path, since_minutes: float) -> Dict[str, Any]:
    cutoff = time.time() - max(0.0, float(since_minutes)) * 60.0
    records = [r for r in read_jsonl(data_dir / "http_network_telemetry.jsonl") if float(r.get("timestamp") or 0) >= cutoff and r.get("event") == "http_request"]
    statuses = Counter(int(r.get("http_status") or 0) for r in records)
    types = Counter(str(r.get("request_type") or "unknown") for r in records)
    blocked = [r for r in records if r.get("blocked_502")]
    context_502 = [r for r in records if r.get("request_type") == "FT_order_view" and int(r.get("http_status") or 0) == 502]
    bet_502 = [r for r in records if r.get("request_type") == "FT_bet" and int(r.get("http_status") or 0) == 502]
    success = [r for r in records if int(r.get("http_status") or 0) in range(200, 300)]
    durations = [float(r.get("total_ms") or 0) for r in records if r.get("total_ms") is not None]
    return {"generated_at": time.time(), "window_minutes": since_minutes, "total_requests": len(records), "request_types": dict(types), "status_counts": dict(statuses), "blocked_502_count": len(blocked), "context_502_count": len(context_502), "bet_502_count": len(bet_502), "http_2xx_count": len(success), "duration_ms": {"median": statistics.median(durations) if durations else 0, "p95": percentile(durations, 0.95), "max": max(durations) if durations else 0}, "blocked_hashes": sorted({str(r.get("response_body_sha256") or "") for r in blocked}), "sessions": {"unique": len({str(r.get("session_hash")) for r in records if r.get("session_hash")}), "creation_records": len([r for r in read_jsonl(data_dir / "http_network_telemetry.jsonl") if r.get("event") == "session_created" and float(r.get("timestamp") or 0) >= cutoff])}, "sampler_records": sum(1 for _ in read_jsonl(data_dir / "network_sampler.jsonl")), "note": "相关性不等于因果；本报告不修改任何网络或下注策略。"}


def render(report: Dict[str, Any]) -> str:
    lines = ["# HTTP 502 Phase 2 Telemetry 报告", "", f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", f"窗口：最近 {report['window_minutes']} 分钟", "", "## 请求概览", "", f"总请求：{report['total_requests']}", f"请求类型：`{json.dumps(report['request_types'], ensure_ascii=False)}`", f"HTTP 状态：`{json.dumps(report['status_counts'], ensure_ascii=False)}`", f"FT_order_view 502：{report['context_502_count']}", f"FT_bet 502：{report['bet_502_count']}", f"已识别 Blocked 502：{report['blocked_502_count']}", f"2xx：{report['http_2xx_count']}", "", "## 时延", "", f"median：{report['duration_ms']['median']:.1f} ms", f"p95：{report['duration_ms']['p95']:.1f} ms", f"max：{report['duration_ms']['max']:.1f} ms", "", "## 结论边界", "", report["note"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--since-minutes", type=float, default=60)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = analyze(Path(args.data_dir).expanduser().resolve(), args.since_minutes)
    Path(args.output).expanduser().resolve().write_text(render(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
