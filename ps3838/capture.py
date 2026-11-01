"""Capture WebSocket frames from an already-open target browser page.

The collector intentionally only observes server-to-browser frames. It does
not log in, send WebSocket messages, or interact with the page.
"""

import asyncio
import atexit
import json
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from playwright.async_api import async_playwright
from project_config import config_path as shared_config_path
from project_config import get_path, load_config, target_hosts as shared_target_hosts


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(
    os.environ.get("PS3838_CONFIG", str(PROJECT_DIR / "config.json"))
).expanduser()


def _env_list(name):
    value = os.environ.get(name, "")
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _load_settings():
    """Load collector settings through the shared project configuration."""
    # CONFIG_PATH remains a patchable compatibility hook for migration tests.
    selected = shared_config_path()
    if CONFIG_PATH != selected:
        previous = os.environ.get("PS3838_CONFIG")
        os.environ["PS3838_CONFIG"] = str(CONFIG_PATH)
        try:
            config = load_config()
        finally:
            if previous is None:
                os.environ.pop("PS3838_CONFIG", None)
            else:
                os.environ["PS3838_CONFIG"] = previous
    else:
        config = load_config()

    cdp_url = config["cdp_url"]
    output_path = get_path(config, "capture_path")
    # Migration leaves a compatibility symlink; rotate the actual data file.
    output_path = output_path.resolve()

    target_hosts = shared_target_hosts(config)

    try:
        rotation_mb = max(0, float(config.get("capture_rotation_mb", 0)))
    except (TypeError, ValueError):
        rotation_mb = 0

    dashboard_url = config.get("dashboard_url") or "http://127.0.0.1:8787"
    try:
        presence_interval = max(
            5.0,
            float(
                config.get("presence_interval_seconds") or 5
            ),
        )
    except (TypeError, ValueError):
        presence_interval = 5.0

    return {
        "cdp_url": str(cdp_url),
        "output": output_path,
        "target_hosts": tuple(target_hosts),
        "rotation_mb": rotation_mb,
        "dashboard_url": str(dashboard_url).rstrip("/"),
        "presence_interval": presence_interval,
    }


SETTINGS = _load_settings()
CDP = SETTINGS["cdp_url"]
OUT = SETTINGS["output"]
TARGET_HOSTS = SETTINGS["target_hosts"]
ROTATION_MB = SETTINGS["rotation_mb"]
DASHBOARD_URL = SETTINGS["dashboard_url"]
PRESENCE_INTERVAL = SETTINGS["presence_interval"]
try:
    STALE_RELOAD_SECONDS = max(60.0, float(os.environ.get("CAPTURE_STALE_RELOAD_SECONDS", "600")))
except ValueError:
    STALE_RELOAD_SECONDS = 600.0

sessions = {}
socket_urls = {}
count = 0
write_errors = 0
dropped_frames = 0
last_no_page_notice = None
last_odds_at = 0.0

# CDP callbacks run on the Playwright event loop.  Writing and flushing a
# complete JSON line from that callback can stall frame delivery when the
# browser sends a burst of odds updates.  Keep the callback non-blocking and
# let one writer preserve ordering on disk.  The queue is deliberately
# bounded: if the disk cannot keep up, dropping is visible in diagnostics
# instead of allowing unbounded memory growth.
_write_queue = queue.Queue(maxsize=4096)
_writer_thread = None
_writer_stop = object()
_writer_lock = threading.Lock()


def _capture_writer_loop():
    global write_errors
    pending = []
    while True:
        try:
            item = _write_queue.get(timeout=0.25)
        except queue.Empty:
            item = None
        if item is _writer_stop:
            if pending:
                _write_batch(pending)
                for _ in pending:
                    _write_queue.task_done()
            _write_queue.task_done()
            return
        if item is not None:
            pending.append(item)
        # Batching reduces open/flush calls while retaining low latency.
        if pending and (len(pending) >= 64 or item is None):
            _write_batch(pending)
            for _ in pending:
                _write_queue.task_done()
            pending.clear()


def _write_batch(records):
    global write_errors
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        rotate_output_if_needed()
        with OUT.open("a", encoding="utf-8") as output_file:
            output_file.write("".join(records))
            output_file.flush()
    except OSError as exc:
        write_errors += 1
        print(f"[写入失败 #{write_errors}] {OUT}: {exc}", flush=True)


def _ensure_capture_writer():
    global _writer_thread
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_capture_writer_loop,
                name="capture-jsonl-writer",
                daemon=True,
            )
            _writer_thread.start()


def flush_capture_writer(timeout=5.0):
    """Drain queued frames; useful for clean shutdown and deterministic tests."""
    thread = _writer_thread
    if thread is None:
        return
    _write_queue.join()


def stop_capture_writer(timeout=5.0):
    """Stop the background writer after draining frames already accepted."""
    global _writer_thread
    thread = _writer_thread
    if thread is None:
        return
    _write_queue.put(_writer_stop)
    thread.join(timeout=timeout)
    if not thread.is_alive():
        _writer_thread = None


atexit.register(stop_capture_writer)


def stamp():
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def is_target_host(host):
    host = (host or "").lower().rstrip(".")
    return any(
        host == target or host.endswith("." + target)
        for target in TARGET_HOSTS
    )


def on_created(event):
    request_id = event.get("requestId", "")
    page_url = event.get("url", "")
    parsed = urlsplit(page_url)
    socket_urls[request_id] = (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    )
    print("[WSS]", socket_urls[request_id], flush=True)


def rotate_output_if_needed():
    """Rotate the raw JSONL file before it exceeds the configured size."""
    if ROTATION_MB <= 0 or not OUT.exists():
        return

    limit = ROTATION_MB * 1024 * 1024
    try:
        if OUT.stat().st_size < limit:
            return
        stamp_value = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        rotated = OUT.with_name(f"{OUT.stem}-{stamp_value}{OUT.suffix}")
        index = 1
        while rotated.exists():
            rotated = OUT.with_name(
                f"{OUT.stem}-{stamp_value}-{index}{OUT.suffix}"
            )
            index += 1
        OUT.replace(rotated)
        print(f"[文件轮转] {OUT.name} -> {rotated.name}", flush=True)
    except OSError as exc:
        print(f"[文件轮转失败] {OUT}: {exc}", flush=True)


def on_received(event):
    global count, write_errors, dropped_frames, last_odds_at
    count += 1

    frame = event.get("response") or {}
    payload = frame.get("payloadData", "")
    opcode = frame.get("opcode")
    request_id = event.get("requestId", "")
    socket_url = socket_urls.get(request_id, "")

    record = {
        "captured_at": stamp(),
        "request_id": request_id,
        "ws_url": socket_url,
        "opcode": opcode,
        "payload": payload,
    }

    serialized = json.dumps(record, ensure_ascii=False) + "\n"
    _ensure_capture_writer()
    try:
        _write_queue.put_nowait(serialized)
    except queue.Full:
        # Never block CDP delivery on a slow disk.  The counter is emitted in
        # the diagnostic line below so operators can distinguish a quiet feed
        # from a saturated collector.
        dropped_frames += 1

    data = None
    if opcode == 1:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            pass

    if data and data.get("type") == "UPDATE_ODDS":
        last_odds_at = time.monotonic()

    print(
        f"[{count}] opcode={opcode} "
        f"chars={len(payload)} "
        f"type={data.get('type') if data else '?'} "
        f"ssn={data.get('ssn') if data else '?'} "
        f"url={'已捕获' if socket_url else '未捕获'} "
        f"queue_drop={dropped_frames}",
        flush=True,
    )


PRESENCE_SCRIPT = r"""
async () => {
  const url = new URL('/sports-service/sv/compact/events', location.origin);
  url.search = new URLSearchParams({
    btg: '1', c: '', cl: '5', d: '', ec: '', ev: '', g: 'QQ==',
    hle: 'false', ic: 'false', ice: 'false', inl: 'false', l: '5',
    lang: '', lg: '', lv: '0', me: '0', me01: '', mk: '1', more: 'false',
    o: '1', ot: '2', pa: '0', pimo: '0,1,8,39,2,3,6,7,4,5', pn: '-1',
    pv: '1', ru: '', sp: '29', tm: '0', v: '0', locale: 'zh_CN',
    _: String(Date.now()), withCredentials: 'true',
  }).toString();

  try {
    const response = await fetch(url, {
      credentials: 'include',
      cache: 'no-store',
    });
    const text = await response.text();
    if (!response.ok) {
      return { ok: false, status: response.status, error: text.slice(0, 160) };
    }

    let payload;
    try {
      payload = JSON.parse(text);
    } catch (error) {
      return { ok: false, status: response.status, error: '返回内容不是 JSON' };
    }
    if (!payload || !Array.isArray(payload.l) || !Array.isArray(payload.n)) {
      return { ok: false, status: response.status, error: '赛事清单结构异常' };
    }

    const eventIds = new Set();
    const rowKeysByEvent = {};
    const eventDetailsById = {};
    const clean = (value) => String(value || '')
      .replace(/[\u200e\u200f]/g, '')
      .trim();
    for (const source of [payload.l, payload.n]) {
      for (const sport of source) {
        for (const league of (sport && sport[2]) || []) {
          for (const event of (league && league[2]) || []) {
            if (event && event[0] !== null && event[0] !== undefined) {
              const eventId = String(event[0]);
              eventIds.add(eventId);
              eventDetailsById[eventId] = {
                name: `${event[1] || ''} VS ${event[2] || ''}`,
                home: event[1] || '',
                away: event[2] || '',
                sportId: String((sport && sport[0]) ?? ''),
                leagueId: String((league && league[0]) ?? ''),
                league: league[1] || '',
                kickoff: Number(event[4]) || 0,
              };

              const stageData = event[8] && event[8]['0'];
              if (!Array.isArray(stageData)) continue;
              const keys = new Set();

              if (Array.isArray(stageData[0])) {
                for (const item of stageData[0]) {
                  if (!Array.isArray(item) || item.length < 2) continue;
                  // The compact snapshot stores each handicap pair from the
                  // favourite's point of view: the side that *gives* goals has
                  // a positive value (home -0.5 arrives as +0.5). The WSS odds
                  // rows use the signed handicap instead (giving is negative),
                  // so negate the compact values into the same key space.
                  // Without this, every non-zero handicap row looks withdrawn
                  // and the dashboard deletes it after a few presence checks,
                  // leaving only the level-ball line visible.
                  const homeLine = -Number(item[0]);
                  const awayLine = -Number(item[1]);
                  if (Number.isFinite(homeLine)) {
                    keys.add(`0|2|0|${String(homeLine)}`);
                  }
                  if (Number.isFinite(awayLine)) {
                    keys.add(`0|2|1|${String(awayLine)}`);
                  }
                }
              }
              if (Array.isArray(stageData[1])) {
                for (const item of stageData[1]) {
                  if (!Array.isArray(item)) continue;
                  const line = Number(item[1]);
                  if (!Number.isFinite(line)) continue;
                  keys.add(`0|3|3|${String(line)}`);
                  keys.add(`0|3|4|${String(line)}`);
                }
              }
              if (Array.isArray(stageData[2]) && stageData[2].length >= 3) {
                keys.add('0|1|0|');
                keys.add('0|1|1|');
                keys.add('0|1|2|');
              }
              if (keys.size) rowKeysByEvent[eventId] = [...keys];
            }
          }
        }
      }
    }

    // Compact snapshots can omit a newly-started live event even though the
    // page already renders it. Read the rendered event row as a fallback so
    // the dashboard does not keep showing a placeholder "赛事 <id>" card.
    if (typeof document !== 'undefined') {
      for (const cell of document.querySelectorAll(
        'td[data-event][data-home-team]'
      )) {
        const eventId = String(cell.getAttribute('data-event') || '');
        if (!eventId) continue;
        const teams = Array.from(
          cell.querySelectorAll('.team-name-container .sel')
        )
          .map((team) => clean(
            team.getAttribute('title') || team.textContent
          ))
          .filter(Boolean);
        // Some event rows repeat the same event ID and only contain one of
        // the two teams. Never map that lone value to the home-team slot.
        const renderedHome = teams.length >= 2 ? teams[0] : '';
        const renderedAway = teams.length >= 2 ? teams[1] : '';
        const table = cell.closest('table.events');
        const leagueBox = table?.parentElement?.previousElementSibling;
        let renderedLeague = '';
        if (leagueBox?.classList?.contains('league')) {
          // The league box also contains tooltip spans. Read only its direct
          // league-title span so status messages can never become league names.
          renderedLeague = Array.from(leagueBox.children || [])
            .filter((element) => element.tagName === 'SPAN')
            .map((element) => clean(element.textContent))
            .find(Boolean) || '';
        }
        const league = renderedLeague
          || clean(cell.getAttribute('data-league-name'));
        const existing = eventDetailsById[eventId] || {};
        const leagueId = clean(cell.getAttribute('data-league-id'))
          || clean(leagueBox?.getAttribute?.('data-league-id'))
          || clean(existing.leagueId);
        const sportId = clean(
          cell.querySelector('[data-sport-id]')?.getAttribute?.('data-sport-id')
        ) || clean(existing.sportId);
        const row = cell.closest('tr');
        const timeText = row?.querySelector('.col-time')?.innerText || '';
        const periodMatch = timeText.match(/\b(1H|2H|HT|ET|PEN)\b/i);
        const minuteMatch = timeText.match(/(\d+)'/);
        const scoreParts = String(row?.getAttribute('data-score') || '')
          .split('-');
        const firstDate = table?.querySelector('[data-date]')
          ?.getAttribute('data-date') || '';
        const home = renderedHome || clean(existing.home)
          || clean(cell.getAttribute('data-home-team'));
        const away = renderedAway || clean(existing.away)
          || clean(cell.getAttribute('data-away-team'));

        eventDetailsById[eventId] = {
          ...existing,
          name: home && away ? `${home} VS ${away}` : existing.name || '',
          home,
          away,
          sportId,
          leagueId,
          league: league || clean(existing.league),
          kickoff: existing.kickoff || Number(firstDate) || 0,
          scoreHome: scoreParts.length === 2
            ? scoreParts[0].trim()
            : clean(existing.scoreHome),
          scoreAway: scoreParts.length === 2
            ? scoreParts[1].trim()
            : clean(existing.scoreAway),
          matchMinute: minuteMatch
            ? `${minuteMatch[1]}'`
            : clean(existing.matchMinute),
          matchPeriod: periodMatch
            ? periodMatch[1].toUpperCase()
            : clean(existing.matchPeriod),
          matchStatus: 'O',
        };
        eventIds.add(eventId);
      }
    }

    // The rendered table can briefly omit league attributes while it is being
    // replaced. Reuse the unique league identity already present in the same
    // compact response so every event still receives its sport and league ID.
    const leagueIdentityByName = new Map();
    for (const details of Object.values(eventDetailsById)) {
      const leagueName = clean(details.league).toLocaleLowerCase();
      if (!leagueName || !details.leagueId) continue;
      const identity = {
        sportId: clean(details.sportId),
        leagueId: clean(details.leagueId),
      };
      const current = leagueIdentityByName.get(leagueName);
      if (!current || (!current.sportId && identity.sportId)) {
        leagueIdentityByName.set(leagueName, identity);
      }
    }
    for (const details of Object.values(eventDetailsById)) {
      if (details.leagueId && details.sportId) continue;
      const identity = leagueIdentityByName.get(
        clean(details.league).toLocaleLowerCase()
      );
      if (!identity) continue;
      details.sportId = details.sportId || identity.sportId;
      details.leagueId = details.leagueId || identity.leagueId;
    }

    return {
      ok: true,
      status: response.status,
      eventIds: [...eventIds],
      rowKeysByEvent,
      eventDetailsById,
      live: payload.l.length,
      upcoming: payload.n.length,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      error: String(error && error.message ? error.message : error),
    };
  }
}
"""


def post_event_presence(
    event_ids,
    observed_at,
    row_keys_by_event=None,
    event_details_by_id=None,
):
    """Publish the current PS3838 event list to the local dashboard."""
    body = json.dumps(
        {
            "eventIds": event_ids,
            "observedAt": observed_at,
            "rowKeysByEvent": row_keys_by_event or {},
            "eventDetailsById": event_details_by_id or {},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        f"{DASHBOARD_URL}/api/event-presence",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # The desktop may export a global HTTP proxy. Local dashboard traffic must
    # bypass it, otherwise 127.0.0.1 requests are sent to the remote proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            detail = ""
        suffix = f" {detail}" if detail else ""
        raise RuntimeError(
            f"看板接口不可用: HTTP {exc.code}{suffix}"
        ) from exc
    except (URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"看板接口不可用: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"看板拒绝赛事校准: {result!r}")
    return result


async def probe_event_presence(page):
    """Read the authenticated event list and publish it without blocking CDP."""
    probe = await page.evaluate(PRESENCE_SCRIPT)
    if not isinstance(probe, dict) or not probe.get("ok"):
        return probe if isinstance(probe, dict) else {"ok": False}

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        post_event_presence,
        list(probe.get("eventIds") or []),
        stamp(),
        probe.get("rowKeysByEvent") or {},
        probe.get("eventDetailsById") or {},
    )
    return {**probe, "sync": result}


async def attach_page(context, page):
    """Attach a CDP Network session to one target page."""
    try:
        session = await context.new_cdp_session(page)
        session.on("Network.webSocketCreated", on_created)
        session.on("Network.webSocketFrameReceived", on_received)
        await session.send("Network.enable")
        sessions[page] = session
        print("[已监听页面]", page.url, flush=True)
    except Exception as exc:  # Playwright raises several transport errors.
        print("[监听失败]", repr(exc), flush=True)


async def main():
    global last_no_page_notice, last_odds_at
    last_presence_state = None
    next_presence_at = 0.0
    next_watchdog_at = time.monotonic() + 60
    last_odds_at = time.monotonic()

    print("CDP 地址:", CDP, flush=True)
    print("目标域名:", ", ".join(TARGET_HOSTS), flush=True)
    print("输出文件:", OUT, flush=True)
    print("赛事校准接口:", f"{DASHBOARD_URL}/api/event-presence", flush=True)

    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.connect_over_cdp(CDP)
        except Exception as exc:
            raise SystemExit(
                "无法连接 Chrome CDP。请先用 --remote-debugging-port=9222 "
                f"启动 Chrome，并确认 {CDP} 可访问。原始错误: {exc}"
            ) from exc

        print("CDP 连接成功，请打开并刷新目标页面。", flush=True)
        print("完整消息保存到:", OUT, flush=True)

        while True:
            # Drop sessions whose pages were closed, allowing a later page to
            # be attached cleanly without growing the dictionary forever.
            for page in list(sessions):
                if page.is_closed():
                    sessions.pop(page, None)

            target_pages = []
            for context in browser.contexts:
                for page in context.pages:
                    host = urlsplit(page.url).hostname or ""
                    if is_target_host(host):
                        target_pages.append((context, page))

            for context, page in target_pages:
                if page not in sessions:
                    await attach_page(context, page)

            if target_pages and time.monotonic() >= next_presence_at:
                next_presence_at = time.monotonic() + PRESENCE_INTERVAL
                _, presence_page = next(
                    (
                        item
                        for item in target_pages
                        if "/sports/soccer" in (item[1].url or "")
                    ),
                    target_pages[0],
                )
                try:
                    probe = await probe_event_presence(presence_page)
                    if probe.get("ok"):
                        sync = probe.get("sync") or {}
                        state = (
                            "ok",
                            int(probe.get("live") or 0),
                            int(probe.get("upcoming") or 0),
                        )
                        if (
                            state != last_presence_state
                            or sync.get("removed")
                            or sync.get("removedRows")
                        ):
                            print(
                                "[赛事校准] 平博当前滚球="
                                f"{state[1]} 今日待开={state[2]}，"
                                f"清理历史残留={sync.get('removed', 0)}，"
                                f"清理失效盘口={sync.get('removedRows', 0)}",
                                flush=True,
                            )
                        if sync.get("removedEventIds"):
                            print(
                                "[赛事校准] 已移除赛事 ID: "
                                + ", ".join(sync["removedEventIds"]),
                                flush=True,
                            )
                    else:
                        state = (
                            "error",
                            int(probe.get("status") or 0),
                            str(probe.get("error") or "未知错误"),
                        )
                        if state != last_presence_state:
                            print(
                                f"[赛事校准失败] status={state[1]} {state[2]}",
                                flush=True,
                            )
                    last_presence_state = state
                except Exception as exc:
                    state = ("error", 0, str(exc))
                    if state != last_presence_state:
                        print(f"[赛事校准失败] {exc}", flush=True)
                    last_presence_state = state

            now_mono = time.monotonic()
            if target_pages and now_mono >= next_watchdog_at:
                next_watchdog_at = now_mono + 60
                idle_seconds = now_mono - last_odds_at
                if idle_seconds >= STALE_RELOAD_SECONDS:
                    print(
                        f"[看门狗] 赔率已 {idle_seconds / 60:.0f} 分钟未更新，"
                        "刷新平博页面以重新订阅。",
                        flush=True,
                    )
                    for _context, page in target_pages:
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=30000)
                        except Exception as exc:
                            print(f"[看门狗] 页面刷新失败：{exc}", flush=True)
                    last_odds_at = time.monotonic()
                    next_watchdog_at = last_odds_at + 120

            if not target_pages:
                now = datetime.now().replace(microsecond=0)
                if last_no_page_notice != now:
                    print(
                        "[等待页面] 未找到目标域名页面，请打开并登录平博页面。",
                        flush=True,
                    )
                    last_no_page_notice = now

            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        stop_capture_writer()
        print(f"\n已停止，文件保存在: {OUT}", flush=True)
    except SystemExit:
        stop_capture_writer()
        raise
    except Exception as exc:
        stop_capture_writer()
        print("运行失败:", repr(exc), flush=True)
