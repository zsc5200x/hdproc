"""Resident partner order poller.

OPEN snapshots run on a one-second target and SETTLED reconciliation runs on a
slow, independent cadence so a long settled sweep never blocks new-order
detection.  A cycle never overlaps itself: a slow upstream response delays the
next cycle instead of stacking.  If the partner tab loses its session, the
supervisor retries instead of exiting so a manual re-login recovers the
collector without a restart.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

import partner_orders as po

RETRY_SECONDS = 15


async def open_loop(session: po.PartnerSession, profile, agents, store, settings) -> None:
    page_size = settings["page_size"]
    while True:
        cycle_start = time.monotonic()
        started = po.now_iso()
        try:
            results = await po.fetch_open_all(session, profile["user_id"], agents, page_size)
            for agent, rows, requests in results:
                result = store.commit_snapshot(agent["code"], "OPEN", rows, {
                    "started_at": started,
                    "finished_at": po.now_iso(),
                    "elapsed_seconds": time.monotonic() - cycle_start,
                    "request_count": requests,
                })
                if result["new"] or result["events"]:
                    print(
                        f"[open] {agent['code']} 记录={result['records']} "
                        f"新增={result['new']} 新单事件={result['events']}",
                        flush=True,
                    )
        except po.PollError as exc:
            print(f"[open] 本轮校验失败：{exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the poller alive
            print(f"[open] 本轮请求失败：{exc}", flush=True)
        elapsed = time.monotonic() - cycle_start
        await asyncio.sleep(max(0.0, settings["open_interval"] - elapsed))


async def settled_loop(session: po.PartnerSession, profile, agents, store, settings) -> None:
    page_size = settings["page_size"]
    while True:
        await asyncio.sleep(settings["settled_interval"])
        end = date.today()
        begin = end - timedelta(days=settings["settled_window_days"])
        for agent in agents:
            settle_started, settle_begin = po.now_iso(), time.monotonic()
            try:
                rows, requests = await po.fetch_status(
                    session, profile["user_id"], agent, "SETTLED", page_size,
                    begin.isoformat(), end.isoformat(),
                )
                result = store.commit_snapshot(agent["code"], "SETTLED", rows, {
                    "started_at": settle_started,
                    "finished_at": po.now_iso(),
                    "elapsed_seconds": time.monotonic() - settle_begin,
                    "request_count": requests,
                })
                print(
                    f"[settled] {agent['code']} 记录={result['records']} "
                    f"更新={result['changed']} 请求={requests}",
                    flush=True,
                )
            except po.PollError as exc:
                print(f"[settled] {agent['code']} 校验失败：{exc}", flush=True)
            except Exception as exc:  # noqa: BLE001 - keep the poller alive
                print(f"[settled] {agent['code']} 请求失败：{exc}", flush=True)


async def poll_forever(store: po.OrderStore, settings) -> None:
    async with po.PartnerSession(settings["cdp_url"], settings["backend_url"]) as session:
        profile = await session.profile()
        print(f"[poll] 账号 {profile['user_code']}（userId={profile['user_id']}）", flush=True)
        agents, missing = await po.resolve_agents(
            session, profile["user_id"], settings["agents"], settings["history_from"]
        )
        if missing:
            print(f"[poll] 未找到配置的代理：{missing}（将跳过）", flush=True)
        if not agents:
            raise po.PollError("没有可用代理")
        store.save_agents(agents)
        print(f"[poll] 代理：{[agent['code'] for agent in agents]}", flush=True)
        await asyncio.gather(
            open_loop(session, profile, agents, store, settings),
            settled_loop(session, profile, agents, store, settings),
        )


async def run() -> int:
    settings = po.load_settings()
    store = po.OrderStore(settings["db_path"])
    print(f"[poll] 数据库：{settings['db_path']}", flush=True)
    print(
        f"[poll] OPEN 目标间隔 {settings['open_interval']}s，"
        f"SETTLED 间隔 {settings['settled_interval']}s（窗口 {settings['settled_window_days']} 天）",
        flush=True,
    )
    try:
        while True:
            try:
                await poll_forever(store, settings)
            except po.PollError as exc:
                print(f"[poll] 采集器暂停：{exc}；{RETRY_SECONDS} 秒后重试", flush=True)
            except Exception as exc:  # noqa: BLE001 - supervisor must survive
                print(f"[poll] 采集器异常：{exc}；{RETRY_SECONDS} 秒后重试", flush=True)
            await asyncio.sleep(RETRY_SECONDS)
    finally:
        store.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        raise SystemExit(0)
