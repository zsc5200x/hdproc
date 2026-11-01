"""One-time historical backfill for the partner order store.

Reads the configured agent scope from the shared Chrome session and writes the
settled orders from ``partner_history_from`` up to today, one calendar month
at a time, plus the current OPEN snapshot as the baseline.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date

import partner_orders as po


async def run() -> int:
    settings = po.load_settings()
    store = po.OrderStore(settings["db_path"])
    print(f"[backfill] 数据库：{settings['db_path']}", flush=True)
    try:
        async with po.PartnerSession(settings["cdp_url"], settings["backend_url"]) as session:
            profile = await session.profile()
            print(f"[backfill] 账号 {profile['user_code']}（userId={profile['user_id']}）", flush=True)
            agents, missing = await po.resolve_agents(
                session, profile["user_id"], settings["agents"], settings["history_from"]
            )
            print(f"[backfill] 代理：{[agent['code'] for agent in agents]}", flush=True)
            if missing:
                print(f"[backfill] 未找到配置的代理：{missing}", flush=True)
            if not agents:
                print("[backfill] 没有可用代理，退出", flush=True)
                return 2
            store.save_agents(agents)
            page_size = settings["page_size"]

            # Baseline OPEN snapshot: the first one never emits new-order events.
            for agent in agents:
                started, begin = po.now_iso(), time.monotonic()
                try:
                    rows, requests = await po.fetch_status(
                        session, profile["user_id"], agent, "OPEN", page_size
                    )
                    result = store.commit_snapshot(agent["code"], "OPEN", rows, {
                        "started_at": started,
                        "finished_at": po.now_iso(),
                        "elapsed_seconds": time.monotonic() - begin,
                        "request_count": requests,
                    })
                    print(
                        f"[open] {agent['code']} 记录={result['records']} 新增={result['new']} "
                        f"基线={result['baseline']} 请求={requests}",
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001 - report and continue
                    print(f"[open] {agent['code']} 失败：{exc}", flush=True)

            start = date.fromisoformat(settings["history_from"])
            end = date.today()
            for agent in agents:
                for chunk_start, chunk_end in po.month_chunks(start, end):
                    for attempt in range(1, 4):
                        started, begin = po.now_iso(), time.monotonic()
                        try:
                            rows, requests = await po.fetch_status(
                                session, profile["user_id"], agent, "SETTLED", page_size,
                                chunk_start.isoformat(), chunk_end.isoformat(),
                            )
                            result = store.commit_snapshot(agent["code"], "SETTLED", rows, {
                                "started_at": started,
                                "finished_at": po.now_iso(),
                                "elapsed_seconds": time.monotonic() - begin,
                                "request_count": requests,
                            })
                            print(
                                f"[settled] {agent['code']} {chunk_start}~{chunk_end} "
                                f"记录={result['records']} 新增={result['new']} "
                                f"更新={result['changed']} 请求={requests} "
                                f"耗时={time.monotonic() - begin:.1f}s",
                                flush=True,
                            )
                            break
                        except po.IncompletePage as exc:
                            print(
                                f"[settled] {agent['code']} {chunk_start}~{chunk_end} "
                                f"第{attempt}次校验失败：{exc}",
                                flush=True,
                            )
                            if attempt == 3:
                                store.record_failure(agent["code"], "SETTLED", {
                                    "started_at": started,
                                    "finished_at": po.now_iso(),
                                    "elapsed_seconds": time.monotonic() - begin,
                                    "request_count": 0,
                                }, "incomplete_page")
                        except Exception as exc:  # noqa: BLE001 - report and continue
                            print(
                                f"[settled] {agent['code']} {chunk_start}~{chunk_end} 失败：{exc}",
                                flush=True,
                            )
                            store.record_failure(agent["code"], "SETTLED", {
                                "started_at": started,
                                "finished_at": po.now_iso(),
                                "elapsed_seconds": time.monotonic() - begin,
                                "request_count": 0,
                            }, "request_error")
                            break
    finally:
        store.close()
    print("[backfill] 完成", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
