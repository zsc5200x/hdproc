import csv
import gzip
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from collections import Counter

from odds_utils import normalize_odds
from project_config import get_path, load_config
from src.core.constants import FULL_TIME_STAGE, SUPPORTED_MARKETS
from src.providers.pinnacle.parser import iter_event_records, iter_messages, iter_leaf_rows, metadata_status

# Runtime paths are populated by main(); importing this module performs no I/O.
CONFIG = {}
CAPTURE = EVENTS = OUTPUT = TEMP = None
SNAPSHOT_INTERVAL = 2.0
ONCE = False


def iter_events(container):
    """Keep the legacy tuple shape over the shared complete event parser."""
    for event in iter_event_records(container):
        yield (event["eventId"], event["home"], event["away"], event["league"],
               event["kickoff"], event["sportId"], event["leagueId"])


# Historical helper names remain available to callers.
walk = iter_messages
leaf_rows = iter_leaf_rows


def discover_rotated_capture_files(active_path):
    """Return rotated capture files in replay order, excluding the active file."""
    active_path = active_path.expanduser().resolve()
    active_resolved = active_path
    rotated_paths = list(active_path.parent.glob("capture-*.jsonl"))
    rotated_paths.extend(active_path.parent.glob("capture-*.jsonl.gz"))
    rotated_paths.extend(active_path.parent.glob("capture-*.jsonl.zst"))
    return sorted(
        (
            path
            for path in rotated_paths
            if path.is_file() and path.resolve() != active_resolved
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )


@contextmanager
def open_rotated_capture(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            yield handle
        return
    if path.suffix == ".zst":
        process = subprocess.Popen(
            ["zstd", "-dc", str(path)],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            yield process.stdout
        finally:
            if process.stdout is not None:
                process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"解压轮转采集文件失败：{path}")
        return
    with path.open(encoding="utf-8") as handle:
        yield handle

def get(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


event_map = {}


def register_event(eid, home, away, league, kickoff, sport_id="", league_id=""):
    """更新赛事映射，并修正此前已保存的占位赛事信息。"""
    eid = str(eid or "").strip()
    if not eid:
        return False

    old = event_map.get(eid, {})
    home = str(home or "").strip()
    away = str(away or "").strip()

    name = (
        f"{home} VS {away}"
        if home and away
        else old.get("比赛") or f"赛事 {eid}"
    )

    new = {
        "比赛": name,
        "sport_id": str(sport_id or "").strip() or old.get("sport_id", ""),
        "league_id": str(league_id or "").strip() or old.get("league_id", ""),
        "联赛": str(league or "").strip() or old.get("联赛", ""),
        "开赛时间": str(kickoff or "").strip()
        or old.get("开赛时间", ""),
    }
    new["元数据状态"] = metadata_status(
        new["比赛"], new["联赛"], new["开赛时间"]
    )

    if new == old:
        return False

    event_map[eid] = new

    # 只遍历该赛事自己的盘口，避免每次元数据更新都扫描全部盘口。
    current_latest = globals().get("latest", {})
    for key in globals().get("event_rows", {}).get(eid, ()):
        row = current_latest.get(key)
        if row is None:
            continue
        for field, value in new.items():
            if row.get(field) != value:
                row[field] = value

    return True


latest = {}
event_rows = {}
updates = Counter()
valid_updates = Counter()
message_count = 0
parse_errors = 0

def process_line(line):
    global message_count, parse_errors

    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        parse_errors += 1
        return False

    captured = str(record.get("captured_at") or "")
    changed = False

    for msg in walk(record):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") != "UPDATE_ODDS":
            continue

        message_count += 1
        odds = msg.get("odds") or {}
        if not isinstance(odds, dict):
            continue

        # 先注册 l/n 中的赛事，再处理本条消息的 u 盘口。
        for source in ("l", "n"):
            for meta in iter_events(odds.get(source) or []):
                if register_event(*meta):
                    changed = True

        for r in leaf_rows(odds.get("u") or []):
            eid = str(r[12]).strip()
            stage = str(r[0]).strip()
            market = str(r[1]).strip()

            if stage != FULL_TIME_STAGE or market not in SUPPORTED_MARKETS:
                continue

            # 赛事元数据尚未到达时，先保留盘口；
            # 后续 l/n 消息会自动补齐名称、联赛和开赛时间。
            if eid not in event_map:
                if register_event(eid, "", "", "", ""):
                    changed = True

            option = str(r[2]).strip()
            line_value = r[3]

            if line_value is None:
                line_value = ""
            else:
                try:
                    line_value = format(float(line_value), ".8g")
                except (ValueError, TypeError):
                    line_value = str(line_value).strip()

            raw_text = "" if r[5] is None else str(r[5]).strip()
            american = "" if r[4] is None else str(r[4]).strip()

            normalized = normalize_odds(market, raw_text)
            good = normalized["报价状态"] == "有报价"

            updates[eid] += 1
            if good:
                valid_updates[eid] += 1

            key = (eid, stage, market, option, str(line_value))
            previous = latest.get(key)

            if (
                previous is not None
                and captured
                and previous["采集时间"]
                and captured < previous["采集时间"]
            ):
                continue

            if previous is None:
                event_rows.setdefault(eid, set()).add(key)
            latest[key] = {
                "event_id": eid,
                "比赛": event_map[eid]["比赛"],
                "sport_id": event_map[eid]["sport_id"],
                "league_id": event_map[eid]["league_id"],
                "联赛": event_map[eid]["联赛"],
                "开赛时间": event_map[eid]["开赛时间"],
                "元数据状态": event_map[eid]["元数据状态"],
                "采集时间": captured,
                "阶段代码": stage,
                "盘口代码": market,
                "选项代码": option,
                "盘口线": line_value,
                "美式赔率": american,
                "原始赔率值": raw_text,
                "odds_format": normalized["odds_format"],
                "decimal_odds": normalized["decimal_odds"],
                "hong_kong_odds": normalized["hong_kong_odds"],
                "报价状态": normalized["报价状态"],
            }
            changed = True

    return changed

fields = [
    "event_id", "sport_id", "league_id", "比赛", "联赛", "开赛时间",
    "元数据状态", "采集时间",
    "阶段代码", "盘口代码", "选项代码", "盘口线",
    "美式赔率", "原始赔率值", "odds_format",
    "decimal_odds", "hong_kong_odds", "报价状态",
]

def write_snapshot():
    rows = sorted(
        latest.values(),
        key=lambda r: (
            r["event_id"], r["阶段代码"], r["盘口代码"],
            str(r["盘口线"]), r["选项代码"]
        )
    )

    with TEMP.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    os.replace(TEMP, OUTPUT)

    quoted = [r for r in rows if r["报价状态"] == "有报价"]
    quoted_events = {r["event_id"] for r in quoted}

    print(
        f"[{time.strftime('%H:%M:%S')}] "
        f"快照={len(rows)} 条 | "
        f"有效报价={len(quoted)} 条 | "
        f"有报价赛事={len(quoted_events)} 场 | "
        f"文件={OUTPUT.name}",
        flush=True
    )


def main(argv=None):
    """Load configured files, build a snapshot, and optionally follow new data."""
    global CONFIG, CAPTURE, EVENTS, OUTPUT, TEMP, SNAPSHOT_INTERVAL, ONCE
    global message_count, parse_errors
    event_map.clear()
    latest.clear()
    event_rows.clear()
    updates.clear()
    valid_updates.clear()
    message_count = parse_errors = 0
    CONFIG = load_config()
    CAPTURE = get_path(CONFIG, "capture_path").resolve()
    EVENTS = get_path(CONFIG, "events_file")
    OUTPUT = get_path(CONFIG, "live_snapshot_file")
    TEMP = OUTPUT.with_name(OUTPUT.name + ".tmp")

    try:
        SNAPSHOT_INTERVAL = max(
            0.1, float(CONFIG.get("snapshot_interval_seconds", 2))
        )
    except (TypeError, ValueError):
        SNAPSHOT_INTERVAL = 2.0

    args = sys.argv[1:] if argv is None else argv
    ONCE = "--once" in args
    SKIP_ROTATED = "--skip-rotated" in args

    if not CAPTURE.exists() or not EVENTS.exists():
        raise SystemExit(f"缺少采集文件或赛事文件：{CAPTURE} / {EVENTS}")

    with EVENTS.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            eid = get(row, "event_id", "id")
            if not eid:
                continue

            name = get(row, "比赛")
            if not name:
                name = (
                    get(row, "主队", "home")
                    + " VS "
                    + get(row, "客队", "away")
                ).strip()

            event_map[eid] = {
                "比赛": name,
                "sport_id": get(row, "sport_id"),
                "league_id": get(row, "league_id"),
                "联赛": get(row, "联赛", "league"),
                "开赛时间": get(row, "开赛时间", "kickoff"),
            }
            event_map[eid]["元数据状态"] = metadata_status(
                event_map[eid]["比赛"],
                event_map[eid]["联赛"],
                event_map[eid]["开赛时间"],
            )

    print("正在读取现有历史采集文件，请稍等……", flush=True)

    rotated_files = [] if SKIP_ROTATED else discover_rotated_capture_files(CAPTURE)
    for rotated_path in rotated_files:
        print(f"回放轮转采集文件：{rotated_path.name}", flush=True)
        with open_rotated_capture(rotated_path) as rotated_handle:
            pending = ""
            while True:
                line = rotated_handle.readline()
                if not line:
                    break
                pending += line
                if pending.endswith("\n"):
                    process_line(pending)
                    pending = ""
            if pending:
                process_line(pending)

    with CAPTURE.open(encoding="utf-8") as f:
        try:
            file_inode = CAPTURE.stat().st_ino
        except OSError:
            file_inode = None
        pending = ""

        # 先建立现有历史快照，保留最后一条未完成的行
        while True:
            line = f.readline()
            if not line:
                break

            pending += line
            if pending.endswith("\n"):
                process_line(pending)
                pending = ""

        write_snapshot()
        if ONCE:
            print("单次回放完成，未继续监听新增数据。", flush=True)
            return 0

        print("初始化完成，开始监听新增采集数据。", flush=True)

        dirty = False
        last_write = time.monotonic()
        last_status = time.monotonic()

        while True:
            line = f.readline()

            if line:
                pending += line

                if pending.endswith("\n"):
                    if process_line(pending):
                        dirty = True
                    pending = ""
            else:
                # capture.py may rotate the JSONL file. Reopen the new inode so
                # the live process continues following fresh messages.
                try:
                    current_inode = CAPTURE.stat().st_ino
                except OSError:
                    current_inode = file_inode
                if file_inode is not None and current_inode != file_inode:
                    f.close()
                    f = CAPTURE.open(encoding="utf-8")
                    file_inode = current_inode
                    pending = ""
                    continue
                time.sleep(0.25)

            now = time.monotonic()

            if dirty and now - last_write >= SNAPSHOT_INTERVAL:
                write_snapshot()
                dirty = False
                last_write = now

            if now - last_status >= 15:
                print(
                    f"监听中 | UPDATE_ODDS={message_count} | "
                    f"已跟踪赛事={len(updates)} | "
                    f"解析错误={parse_errors}",
                    flush=True
                )
                last_status = now


if __name__ == "__main__":
    raise SystemExit(main())
