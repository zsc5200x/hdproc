#!/usr/bin/env python3
"""
HDZZ configuration diagnostics.

Checks:
- data/follow_settings.json
- data/arsenal.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _load_json(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    issues: List[str] = []
    if not path.exists():
        return None, [f"Missing file: {path.as_posix()}"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive
        return None, [f"Invalid JSON in {path.as_posix()}: {exc}"]
    if not isinstance(raw, dict):
        return None, [f"Top-level JSON must be an object: {path.as_posix()}"]
    return raw, issues


def check_follow_settings(data_dir: Path) -> Tuple[List[str], List[str]]:
    issues: List[str] = []
    suggestions: List[str] = []

    path = data_dir / "follow_settings.json"
    doc, load_issues = _load_json(path)
    if load_issues:
        return load_issues, suggestions
    assert doc is not None

    split_count = int(doc.get("split_count") or 1)
    split_delay = float(doc.get("split_delay_seconds") or 0.0)
    wait_max = float(doc.get("wait_line_not_found_max_seconds") or 30.0)
    wait_probe = float(doc.get("wait_line_not_found_probe_seconds") or 2.0)

    if split_count > 3:
        issues.append(f"split_count={split_count} is too large; recommended 2-3")
        suggestions.append("Set split_count to 3")
    if split_delay > 2.0:
        issues.append(
            f"split_delay_seconds={split_delay:.3g} is too large; recommended 0.5-1.0"
        )
        suggestions.append("Set split_delay_seconds to 0.5")
    if wait_max < 40:
        issues.append(
            f"wait_line_not_found_max_seconds={wait_max:.3g} is too small; recommended 45-60"
        )
        suggestions.append("Set wait_line_not_found_max_seconds to 45")
    if wait_probe <= 0:
        issues.append(
            f"wait_line_not_found_probe_seconds={wait_probe:.3g} must be greater than 0"
        )
        suggestions.append("Set wait_line_not_found_probe_seconds to 2")
    if wait_probe >= wait_max:
        issues.append(
            "wait_line_not_found_probe_seconds should be smaller than wait_line_not_found_max_seconds"
        )
        suggestions.append("Keep probe window small (e.g. 2s)")

    if not bool(doc.get("game_more_scan_all_gids", False)):
        issues.append("game_more_scan_all_gids=false; cross-gid scan should be enabled")
        suggestions.append("Set game_more_scan_all_gids to true")
    if not bool(doc.get("use_quote_member_for_total_odds", False)):
        issues.append("use_quote_member_for_total_odds=false; quote-member lookup should be enabled")
        suggestions.append("Set use_quote_member_for_total_odds to true")
    if not bool(doc.get("order_view_confirm_before_bet", False)):
        issues.append("order_view_confirm_before_bet=false; confirm-before-bet is recommended")
        suggestions.append("Set order_view_confirm_before_bet to true")

    return issues, suggestions


def check_arsenal(data_dir: Path) -> Tuple[List[str], List[str]]:
    issues: List[str] = []
    suggestions: List[str] = []

    path = data_dir / "arsenal.json"
    doc, load_issues = _load_json(path)
    if load_issues:
        return load_issues, suggestions
    assert doc is not None

    members = doc.get("members")
    if not isinstance(members, list):
        return ["members must be an array in arsenal.json"], []

    if not members:
        issues.append("No members configured in arsenal.json")
        suggestions.append("Add at least one betting member and one quote member")
        return issues, suggestions

    valid_roles = {"quote", "bet", "both"}
    quote_members = 0
    missing_label = 0
    invalid_role_ids: List[str] = []

    for m in members:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        if role in {"quote", "both"}:
            quote_members += 1
        if role and role not in valid_roles:
            member_id = str(m.get("id") or m.get("username") or "<unknown>")
            invalid_role_ids.append(member_id)
        if not str(m.get("label") or "").strip():
            missing_label += 1

    if quote_members == 0:
        issues.append("No role=quote (or role=both) member found")
        suggestions.append("Configure at least one quote member for odds lookup")
    if missing_label > 0:
        issues.append(f"{missing_label} member(s) missing label")
        suggestions.append("Add readable labels for all members")
    if invalid_role_ids:
        issues.append(
            "Invalid member roles found for: " + ", ".join(sorted(set(invalid_role_ids)))
        )
        suggestions.append("Allowed roles are: quote, bet, both")

    return issues, suggestions


def _print_section(title: str, issues: List[str], suggestions: List[str]) -> None:
    print(title)
    print("-" * 60)
    if not issues:
        print("OK: no issue found")
    else:
        print(f"Found {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        if suggestions:
            print("")
            print("Suggestions:")
            for item in suggestions:
                print(f"  * {item}")
    print("")


def main() -> int:
    parser = argparse.ArgumentParser(description="HDZZ config diagnostics")
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Directory containing follow_settings.json and arsenal.json",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print("=" * 60)
    print("HDZZ CONFIG DIAGNOSTIC REPORT")
    print("=" * 60)
    print(f"Data dir: {data_dir.as_posix()}")
    print("")

    follow_issues, follow_suggestions = check_follow_settings(data_dir)
    arsenal_issues, arsenal_suggestions = check_arsenal(data_dir)

    _print_section("[1] follow_settings.json", follow_issues, follow_suggestions)
    _print_section("[2] arsenal.json", arsenal_issues, arsenal_suggestions)

    total_issues = len(follow_issues) + len(arsenal_issues)
    print("=" * 60)
    print(f"Done. Total issues: {total_issues}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
