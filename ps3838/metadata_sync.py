"""Repair missing event metadata through the PS3838 sports service.

The page already has an authenticated sports-service endpoint that accepts an
event id through ``ev``.  This tool connects to the same Chrome CDP session,
queries the unresolved event ids, and optionally writes the result back to the
SQLite event table and ``events_list.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from playwright.sync_api import sync_playwright

from src.providers.pinnacle.parser import iter_event_records, metadata_status
from src.core.time import now_iso
from project_config import (
    PROJECT_DIR,
    get_path,
    load_config,
    target_hosts as configured_target_hosts,
)


DEFAULT_STATUS_PATH = PROJECT_DIR / ".run" / "metadata_sync.status.json"
CSV_FIELDS = [
    "event_id", "sport_id", "league_id", "来源", "体育分类",
    "联赛", "主队", "客队", "开赛时间",
]
EVENT_ENDPOINT = "/sports-service/sv/compact/events"


def write_status(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def load_candidates(
    config: Dict[str, Any],
    limit: int = 0,
    event_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    database_path = get_path(config, "dashboard_db_path")
    with sqlite3.connect(str(database_path), timeout=15) as connection:
        connection.row_factory = sqlite3.Row
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            rows = connection.execute(
                f"SELECT event_id FROM events WHERE event_id IN ({placeholders}) "
                "ORDER BY event_id",
                list(event_ids),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT event_id FROM events WHERE metadata_status != 'complete' "
                "ORDER BY last_captured_at DESC, event_id"
            ).fetchall()
    values = [str(row["event_id"]) for row in rows]
    if limit and limit > 0:
        values = values[:limit]
    return values


def normalize_kickoff(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            continue
    return value


def parse_event_payload(payload: Dict[str, Any], event_id: str) -> Optional[Dict[str, Any]]:
    for source in ("l", "n"):
        for event in iter_event_records(payload.get(source) or []):
            if event["eventId"] != str(event_id):
                continue
            name = (
                f'{event["home"]} VS {event["away"]}'
                if event["home"] and event["away"]
                else ""
            )
            status = metadata_status(name, event["league"], event["kickoff"])
            return {
                "eventId": event["eventId"],
                "home": event["home"],
                "away": event["away"],
                "name": name,
                "sportId": event["sportId"],
                "leagueId": event["leagueId"],
                "league": event["league"],
                "kickoff": normalize_kickoff(event["kickoff"]),
                "metadataStatus": status,
                "source": f"compact-events:{source}",
            }
    return None


def fetch_metadata_batch(
    page: Any,
    event_ids: Sequence[str],
    concurrency: int,
) -> List[Dict[str, Any]]:
    # The endpoint only resolves old events when it receives the complete
    # filter set used by the soccer page.  A bare ``?ev=<id>`` request falls
    # back to the currently featured event, which would otherwise poison the
    # metadata for every unresolved historical event.
    script = """
    async ({ ids, concurrency }) => {
      const results = new Array(ids.length);
      let cursor = 0;
      const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const hasEvent = (payload, id) => {
        for (const source of ['l', 'n']) {
          for (const sport of (payload[source] || [])) {
            for (const league of (sport[2] || [])) {
              for (const event of (league[2] || [])) {
                if (String(event[0]) === String(id)) return true;
              }
            }
          }
        }
        return false;
      };
      const query = async (id) => {
        let last = { id, status: 0, text: '', attempts: 0 };
        for (let attempt = 1; attempt <= 3; attempt += 1) {
          last.attempts = attempt;
          try {
            const url = new URL('/sports-service/sv/compact/events', location.origin);
            url.search = new URLSearchParams({
              btg: '1', c: '', cl: '5', d: '', ec: '', ev: id,
              g: 'QQ==', hle: 'false', ic: 'false', ice: 'false',
              inl: 'false', l: '5', lang: '', lg: '', lv: '0',
              me: '0', me01: '', mk: '1', more: 'false', o: '1',
              ot: '2', pa: '0', pimo: '0,1,8,39,2,3,6,7,4,5',
              pn: '-1', pv: '1', ru: '', sp: '29', tm: '0', v: '0',
              locale: 'zh_CN', _: String(Date.now()),
              withCredentials: 'true',
            }).toString();
            const response = await fetch(url, {
              credentials: 'include',
              cache: 'no-store',
            });
            const text = await response.text();
            last = { id, status: response.status, text, attempts: attempt };
            if (response.ok) {
              try {
                if (hasEvent(JSON.parse(text), id)) return last;
              } catch (_) {}
            }
          } catch (error) {
            last = {
              id,
              status: 0,
              text: '',
              attempts: attempt,
              error: String(error && error.message ? error.message : error),
            };
          }
          if (attempt < 3) await delay(250 * attempt);
        }
        return last;
      };
      async function worker() {
        while (true) {
          const index = cursor++;
          if (index >= ids.length) return;
          const id = ids[index];
          results[index] = await query(id);
        }
      }
      const workers = Math.max(1, Math.min(concurrency, ids.length));
      await Promise.all(Array.from({ length: workers }, () => worker()));
      return results;
    }
    """
    return page.evaluate(script, {"ids": list(event_ids), "concurrency": concurrency})


def find_target_page(
    browser: Any,
    target_hosts: Iterable[str],
    preferred_path: str = "",
) -> Any:
    """Return the PS3838 page that can serve the sports-service API.

    ``target_hosts`` also contains the partner backend (``ps38ag.com``).
    Chrome tab order is not stable, so returning the first host match can pick
    the partner page; the relative ``/sports-service`` fetch then receives the
    partner app's HTML instead of event JSON.  When a preferred path is given,
    only that page is accepted.
    """
    hosts = target_hosts
    if isinstance(hosts, str):
        hosts = hosts.split(",")
    hosts = tuple(
        str(host).strip().lower().lstrip(".")
        for host in hosts
        if str(host).strip()
    )
    fallback = None
    for context in browser.contexts:
        for page in context.pages:
            try:
                url = str(page.url or "")
                host = (url.split("://", 1)[-1].split("/", 1)[0]).lower()
            except Exception:
                continue
            if not any(host == item or host.endswith("." + item) for item in hosts):
                continue
            if preferred_path:
                if preferred_path in url:
                    return page
                continue
            if fallback is None:
                fallback = page
    return fallback


def update_events_csv(path: Path, records: Sequence[Dict[str, Any]]) -> int:
    existing: Dict[str, Dict[str, str]] = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                event_id = str(row.get("event_id") or "").strip()
                if event_id:
                    existing[event_id] = {key: str(row.get(key) or "") for key in CSV_FIELDS}

    changed = 0
    for record in records:
        event_id = record["eventId"]
        row = {
            "event_id": event_id,
            "sport_id": record.get("sportId", ""),
            "league_id": record.get("leagueId", ""),
            "来源": "api",
            "体育分类": "Soccer",
            "联赛": record.get("league", ""),
            "主队": record.get("home", ""),
            "客队": record.get("away", ""),
            "开赛时间": record.get("kickoff", ""),
        }
        if existing.get(event_id) != row:
            existing[event_id] = row
            changed += 1

    rows = sorted(
        existing.values(),
        key=lambda item: (item.get("开赛时间", ""), item.get("event_id", "")),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp_path, path)
    return changed


def update_event_database(config: Dict[str, Any], records: Sequence[Dict[str, Any]]) -> int:
    database_path = get_path(config, "dashboard_db_path")
    updated_at = now_iso()
    changed = 0
    with sqlite3.connect(str(database_path), timeout=30) as connection:
        connection.execute("PRAGMA busy_timeout=30000")
        for record in records:
            event_id = record["eventId"]
            previous = connection.execute(
                "SELECT last_captured_at, last_changed_at FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            last_captured_at = previous[0] if previous else ""
            last_changed_at = previous[1] if previous else ""
            before_changes = connection.total_changes
            connection.execute(
                """
                INSERT INTO events (
                    event_id, name, league, sport_id, league_id, kickoff, metadata_status,
                    last_captured_at, last_changed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    name = CASE
                        WHEN excluded.name <> ''
                             AND excluded.name <> ('赛事 ' || excluded.event_id)
                        THEN excluded.name
                        ELSE events.name
                    END,
                    league = CASE
                        WHEN excluded.league <> '' THEN excluded.league
                        ELSE events.league
                    END,
                    sport_id = CASE
                        WHEN excluded.sport_id <> '' THEN excluded.sport_id
                        ELSE events.sport_id
                    END,
                    league_id = CASE
                        WHEN excluded.league_id <> '' THEN excluded.league_id
                        ELSE events.league_id
                    END,
                    kickoff = CASE
                        WHEN excluded.kickoff <> '' THEN excluded.kickoff
                        ELSE events.kickoff
                    END,
                    metadata_status = CASE
                        WHEN excluded.metadata_status = 'complete' THEN 'complete'
                        WHEN events.metadata_status = 'complete' THEN 'complete'
                        WHEN excluded.metadata_status = 'partial' THEN 'partial'
                        WHEN events.metadata_status = 'partial' THEN 'partial'
                        ELSE 'missing'
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    event_id,
                    record.get("name", ""),
                    record.get("league", ""),
                    record.get("sportId", ""),
                    record.get("leagueId", ""),
                    record.get("kickoff", ""),
                    record.get("metadataStatus", "partial"),
                    last_captured_at,
                    last_changed_at,
                    updated_at,
                ),
            )
            changed += connection.total_changes > before_changes
        connection.commit()
    return changed


def run_sync(
    config: Dict[str, Any],
    apply: bool = False,
    limit: int = 0,
    event_ids: Optional[Sequence[str]] = None,
    concurrency: int = 4,
    batch_size: int = 20,
    status_path: Path = DEFAULT_STATUS_PATH,
) -> Dict[str, Any]:
    candidates = load_candidates(config, limit=limit, event_ids=event_ids)
    started_at = now_iso()
    state: Dict[str, Any] = {
        "running": True,
        "startedAt": started_at,
        "finishedAt": "",
        "apply": apply,
        "total": len(candidates),
        "processed": 0,
        "resolved": 0,
        "failed": 0,
        "updated": 0,
        "unresolvedIds": [],
        "message": "正在连接 CDP",
    }
    write_status(status_path, state)
    if not candidates:
        state.update(
            {
                "running": False,
                "finishedAt": now_iso(),
                "message": "没有需要补全的赛事",
            }
        )
        write_status(status_path, state)
        return state

    records: List[Dict[str, Any]] = []
    failed_ids: List[str] = []
    cdp_url = str(config.get("cdp_url") or "http://127.0.0.1:9222")
    target_hosts = configured_target_hosts(config)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            page = find_target_page(
                browser, target_hosts, preferred_path="/sports/soccer"
            )
            if page is None:
                opened = [
                    str(item.url or "")
                    for context in browser.contexts
                    for item in context.pages
                ]
                print(
                    "[metadata] 未找到足球页面 cdp="
                    f"{cdp_url} hosts={target_hosts!r} pages={opened!r}",
                    flush=True,
                )
                raise RuntimeError("没有找到目标平博页面，请先打开足球赔率页面")
            state["message"] = "正在查询赛事元数据"
            write_status(status_path, state)
            for start in range(0, len(candidates), batch_size):
                batch = candidates[start : start + batch_size]
                results = fetch_metadata_batch(page, batch, concurrency)
                for result in results:
                    event_id = str(result.get("id") or "")
                    state["processed"] += 1
                    payload: Dict[str, Any] = {}
                    try:
                        payload = json.loads(result.get("text") or "{}")
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    record = parse_event_payload(payload, event_id)
                    if record and record.get("name") and record.get("league"):
                        records.append(record)
                        state["resolved"] += 1
                    else:
                        failed_ids.append(event_id)
                        state["failed"] += 1
                state["unresolvedIds"] = failed_ids
                state["message"] = (
                    f"已查询 {state['processed']}/{state['total']}，"
                    f"成功 {state['resolved']}，失败 {state['failed']}"
                )
                write_status(status_path, state)
                time.sleep(0.05)
            browser.close()

        if apply and records:
            state["message"] = "正在写入 SQLite 和 events_list.csv"
            write_status(status_path, state)
            state["updated"] = update_event_database(config, records)
            update_events_csv(get_path(config, "events_file"), records)
        if state["resolved"]:
            state["message"] = (
                f"完成：查询 {state['processed']}，成功 {state['resolved']}，"
                f"失败 {state['failed']}，写入 {state['updated'] if apply else 0}"
            )
        else:
            state["message"] = (
                f"未找到可补全数据：查询 {state['processed']}，"
                f"失败 {state['failed']}（平博已不再返回这些赛事）"
            )
    except Exception as exc:
        state["message"] = f"同步失败：{exc}"
        state["failed"] = len(candidates) - state["resolved"]
        state["unresolvedIds"] = candidates[state["processed"] :]
    finally:
        state["running"] = False
        state["finishedAt"] = now_iso()
        write_status(status_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="通过 PS3838 页面接口补齐赛事元数据")
    parser.add_argument("--apply", action="store_true", help="写入 SQLite 和 events_list.csv")
    parser.add_argument("--limit", type=int, default=0, help="最多检查多少个赛事")
    parser.add_argument("--event-id", action="append", default=[], help="只检查指定赛事 ID")
    parser.add_argument("--concurrency", type=int, default=3, help="并发请求数，1-8")
    parser.add_argument("--batch-size", type=int, default=20, help="每批处理数量")
    parser.add_argument("--status", default=None, help="状态文件路径")
    args = parser.parse_args()
    if args.limit < 0:
        raise SystemExit("--limit 不能小于 0")
    if not 1 <= args.concurrency <= 8:
        raise SystemExit("--concurrency 必须在 1 到 8 之间")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必须大于 0")
    config = load_config()
    status_path = (
        Path(args.status).expanduser() if args.status
        else get_path(config, "run_dir") / "metadata_sync.status.json"
    )
    if not status_path.is_absolute():
        status_path = PROJECT_DIR / status_path
    state = run_sync(
        config=config,
        apply=args.apply,
        limit=args.limit,
        event_ids=args.event_id or None,
        concurrency=args.concurrency,
        batch_size=args.batch_size,
        status_path=status_path,
    )
    print(json.dumps(state, ensure_ascii=False, indent=2))
    if not args.apply:
        print("当前为检查模式；确认结果后加 --apply 写入。")
    if state.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
