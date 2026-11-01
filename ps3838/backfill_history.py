"""Import existing capture JSONL files into the dashboard SQLite database.

The importer is resumable: progress is checkpointed after each flushed batch,
and SQLite uniqueness constraints make repeated runs safe.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from src.services.odds_store import OddsStore
from project_config import PROJECT_DIR, get_path, load_config


STATE_VERSION = 1
DEFAULT_BATCH_SIZE = 1000
DEFAULT_STATE_PATH = PROJECT_DIR / ".run" / "backfill_history.state.json"


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def discover_capture_files(active_path: Path) -> list[Path]:
    """Return rotated files in chronological order, followed by the active file."""
    active_path = active_path.expanduser().resolve()
    directory = active_path.parent
    rotated_paths = list(directory.glob("capture-*.jsonl"))
    rotated_paths.extend(directory.glob("capture-*.jsonl.gz"))
    rotated_paths.extend(directory.glob("capture-*.jsonl.zst"))
    rotated = sorted(
        (
            path
            for path in rotated_paths
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    files = [path for path in rotated if path.resolve() != active_path.resolve()]
    if active_path.is_file():
        files.append(active_path)
    return files


@contextmanager
def open_capture(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as handle:
            yield handle
        return
    if path.suffix == ".zst":
        process = subprocess.Popen(
            ["zstd", "-dc", str(path)],
            stdout=subprocess.PIPE,
        )
        try:
            yield process.stdout
        finally:
            if process.stdout is not None:
                process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                raise RuntimeError(f"解压采集文件失败：{path}")
        return
    with path.open("rb") as handle:
        yield handle


def load_checkpoint(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "files": {}}
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return {"version": STATE_VERSION, "files": {}}
    data["version"] = STATE_VERSION
    return data


def save_checkpoint(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)


def format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def is_compressed_capture(path: Path) -> bool:
    return path.suffix in {".gz", ".zst"}


def _file_progress(
    state: Dict[str, Any], path: Path, offset: int, line_count: int
) -> Dict[str, Any]:
    entry = {
        "offset": offset,
        "lines": line_count,
        "size": path.stat().st_size,
        "inode": path.stat().st_ino,
        "compressed": is_compressed_capture(path),
        "updatedAt": now_iso(),
    }
    state["files"][str(path.resolve())] = entry
    return entry


def run_backfill(
    config: Dict[str, Any],
    files: Optional[Iterable[Path]] = None,
    state_path: Optional[Path] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_lines: int = 0,
    reset_checkpoint: bool = False,
    report: Callable[[str], None] = print,
) -> Dict[str, Any]:
    if state_path is None:
        state_path = get_path(config, "run_dir") / "backfill_history.state.json"
    if reset_checkpoint and state_path.exists():
        state_path.unlink()

    active_path = get_path(config, "capture_path").resolve()
    capture_files = list(files) if files is not None else discover_capture_files(active_path)
    state = load_checkpoint(state_path)
    state["version"] = STATE_VERSION

    store = OddsStore(config)
    start_points = store.history_db.point_count()
    started = time.monotonic()
    total_lines = 0
    odds_messages = 0
    processed_files = 0
    interrupted = False
    current_file: Optional[Path] = None
    current_offset = 0
    current_file_lines = 0

    report(
        f"[backfill] 开始: {len(capture_files)} 个文件，"
        f"数据库已有 {start_points:,} 个历史点"
    )

    try:
        for path in capture_files:
            if max_lines and total_lines >= max_lines:
                break
            if not path.is_file():
                continue
            current_file = path
            file_key = str(path.resolve())
            stat = path.stat()
            entry = state["files"].get(file_key, {})
            current_file_lines = int(entry.get("lines", 0) or 0)
            offset = int(entry.get("offset", 0) or 0)
            compressed = is_compressed_capture(path)
            if (
                entry
                and (
                    entry.get("inode") != stat.st_ino
                    or (not compressed and offset > stat.st_size)
                )
            ):
                offset = 0
                current_file_lines = 0
            current_offset = offset
            processed_files += 1
            batch_lines = 0
            if compressed:
                report(
                    f"[backfill] {path.name}: 从解压偏移 "
                    f"{format_bytes(offset)} 开始，压缩大小 "
                    f"{format_bytes(stat.st_size)}"
                )
            else:
                report(
                    f"[backfill] {path.name}: 从 {format_bytes(offset)} 开始，"
                    f"文件大小 {format_bytes(stat.st_size)}"
                )

            with open_capture(path) as handle:
                if compressed:
                    remaining = offset
                    while remaining > 0:
                        chunk = handle.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                else:
                    handle.seek(offset)
                while True:
                    if max_lines and total_lines >= max_lines:
                        break
                    raw_line = handle.readline()
                    if not raw_line:
                        break
                    current_offset += len(raw_line)
                    total_lines += 1
                    current_file_lines += 1
                    batch_lines += 1
                    try:
                        line = raw_line.decode("utf-8")
                    except UnicodeDecodeError:
                        line = raw_line.decode("utf-8", errors="replace")
                    if line.strip() and store.process_line(line, source="backfill"):
                        odds_messages += 1

                    if batch_lines >= batch_size:
                        store.flush_history()
                        _file_progress(
                            state, path, current_offset, current_file_lines
                        )
                        state["updatedAt"] = now_iso()
                        save_checkpoint(state_path, state)
                        batch_lines = 0
                        if compressed:
                            progress = (
                                f"解压进度 {format_bytes(current_offset)} | "
                                f"压缩大小 {format_bytes(stat.st_size)}"
                            )
                        else:
                            progress = (
                                f"{format_bytes(current_offset)} / "
                                f"{format_bytes(stat.st_size)}"
                            )
                        report(
                            f"[backfill] {path.name}: {progress} | "
                            f"总行数 {total_lines:,} | "
                            f"数据库点 {store.history_writer.written_points:,}"
                        )

            store.flush_history()
            _file_progress(state, path, current_offset, current_file_lines)
            state["updatedAt"] = now_iso()
            save_checkpoint(state_path, state)
    except KeyboardInterrupt:
        interrupted = True
        store.flush_history()
        if current_file is not None:
            _file_progress(
                state, current_file, current_offset, current_file_lines
            )
            state["updatedAt"] = now_iso()
            save_checkpoint(state_path, state)
        report("[backfill] 收到中断信号，已保存断点")
    finally:
        store.close()

    end_points = store.history_db.point_count()
    elapsed = time.monotonic() - started
    summary = {
        "files": processed_files,
        "lines": total_lines,
        "oddsMessages": odds_messages,
        "pointsBefore": start_points,
        "pointsAfter": end_points,
        "pointsAdded": max(0, end_points - start_points),
        "elapsedSeconds": round(elapsed, 2),
        "interrupted": interrupted,
        "statePath": str(state_path),
        "databasePath": str(store.database_path),
        "databaseErrors": store.history_writer.errors,
        "databaseDropped": store.history_writer.dropped,
    }
    report(
        f"[backfill] 完成: 文件 {processed_files}，行 {total_lines:,}，"
        f"新增历史点 {summary['pointsAdded']:,}，"
        f"耗时 {elapsed:.1f}s，数据库点 {end_points:,}"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="回填历史赔率到 SQLite")
    parser.add_argument(
        "--state",
        default=None,
        help="断点文件路径",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="每个事务处理的行数",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=0,
        help="本次最多处理的行数，0 表示不限制",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="忽略已有断点并从头读取；数据库唯一约束会防止重复",
    )
    parser.add_argument(
        "--db",
        default="",
        help="覆盖 config.json 中的 dashboard_db_path",
    )
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise SystemExit("--batch-size 必须大于 0")
    if args.max_lines < 0:
        raise SystemExit("--max-lines 不能小于 0")

    config = load_config()
    if args.db:
        config["dashboard_db_path"] = args.db
    state_path = (
        Path(args.state).expanduser() if args.state
        else get_path(config, "run_dir") / "backfill_history.state.json"
    )
    if not state_path.is_absolute():
        state_path = PROJECT_DIR / state_path

    summary = run_backfill(
        config=config,
        state_path=state_path,
        batch_size=args.batch_size,
        max_lines=args.max_lines,
        reset_checkpoint=args.reset_checkpoint,
    )
    if summary["interrupted"]:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
