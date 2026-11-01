#!/usr/bin/env python3
"""Daily database backups and safe compression of rotated capture files."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from project_config import PROJECT_DIR, get_path, load_config
from src.core.runtime_lock import runtime_data_lock


CAPTURE_FILE_RE = re.compile(r"^capture-\d{8}-\d{6}(?:-\d+)?\.jsonl$")
BACKUP_FILE_RE = re.compile(r"^odds_history-\d{8}-\d{6}(?:-\d+)?\.sqlite3$")


def timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def find_zstd() -> Optional[str]:
    discovered = shutil.which("zstd")
    if discovered:
        return discovered
    for candidate in ("/opt/homebrew/bin/zstd", "/usr/local/bin/zstd"):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def verify_database(
    path: Path, *, immutable: bool = True, quick: bool = False
) -> str:
    # immutable=1 skips WAL reads and is only safe for closed/offline files
    # (for example, a just-created backup). A live dashboard database can have
    # uncheckpointed pages in -wal; checking it with immutable=1 reports false
    # "invalid page number" errors. Use a read-only WAL-aware connection there.
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    uri = f"file:{path.resolve().as_posix()}?{query}"
    pragma = "PRAGMA quick_check" if quick else "PRAGMA integrity_check"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as connection:
        row = connection.execute(pragma).fetchone()
    result = str(row[0]) if row else "no result"
    if result != "ok":
        raise RuntimeError(f"数据库完整性检查失败：{path}: {result}")
    return result


def prune_backups(backup_dir: Path, keep: int) -> List[str]:
    if keep <= 0:
        return []
    backups = sorted(
        (
            path
            for path in backup_dir.glob("odds_history-*.sqlite3")
            if path.is_file() and BACKUP_FILE_RE.match(path.name)
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    removed: List[str] = []
    for path in backups[keep:]:
        path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def backup_database(
    config: Dict[str, Any], keep: Optional[int] = None
) -> Dict[str, Any]:
    source = get_path(config, "dashboard_db_path")
    if not source.exists():
        raise FileNotFoundError(f"数据库不存在：{source}")
    backup_dir = get_path(config, "backup_dir")
    backup_dir.mkdir(parents=True, exist_ok=True)
    keep_value = int(config.get("backup_keep", 7) if keep is None else keep)
    keep_value = max(1, keep_value)
    destination = unique_path(backup_dir / f"odds_history-{timestamp()}.sqlite3")
    temporary = destination.with_name(destination.name + ".tmp")

    try:
        with closing(sqlite3.connect(str(source), timeout=30)) as source_db:
            with closing(sqlite3.connect(str(temporary), timeout=30)) as target_db:
                source_db.backup(target_db)
                target_db.execute("PRAGMA journal_mode=DELETE")
                target_db.commit()
        verify_database(temporary)
        os.replace(temporary, destination)
    finally:
        for path in (
            temporary,
            Path(str(temporary) + "-wal"),
            Path(str(temporary) + "-shm"),
        ):
            path.unlink(missing_ok=True)

    for suffix in ("-wal", "-shm"):
        Path(str(destination) + suffix).unlink(missing_ok=True)

    removed = prune_backups(backup_dir, keep_value)
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "kept": keep_value,
        "removed": removed,
    }


def file_digest(handle: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    lines = 0
    while True:
        chunk = handle.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        lines += chunk.count(b"\n")
    return digest.hexdigest(), lines


def compressed_digest(path: Path, zstd: str = "zstd") -> tuple[str, int]:
    process = subprocess.Popen(
        [zstd, "-dc", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        digest, lines = file_digest(process.stdout)
    finally:
        process.stdout.close()
    stderr = ""
    if process.stderr is not None:
        try:
            stderr = process.stderr.read().decode("utf-8", errors="replace")
        finally:
            process.stderr.close()
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"zstd 解压失败：{path}: {stderr.strip()}")
    return digest, lines


def verify_compressed_copy(
    source: Path, compressed: Path, zstd: str = "zstd"
) -> Dict[str, Any]:
    subprocess.run(
        [zstd, "-t", str(compressed)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    with source.open("rb") as handle:
        source_hash, source_lines = file_digest(handle)
    compressed_hash, compressed_lines = compressed_digest(compressed, zstd=zstd)
    if source_hash != compressed_hash or source_lines != compressed_lines:
        raise RuntimeError(
            f"压缩校验不一致：{source.name} "
            f"lines={source_lines}/{compressed_lines} sha256={source_hash[:12]}/{compressed_hash[:12]}"
        )
    return {
        "sha256": source_hash,
        "lines": source_lines,
        "sourceBytes": source.stat().st_size,
        "compressedBytes": compressed.stat().st_size,
    }


def compress_capture_files(
    config: Dict[str, Any],
    force: bool = False,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    capture_path = get_path(config, "capture_path").expanduser().resolve()
    root = capture_path.parent
    hours = max(0.0, float(config.get("capture_compress_after_hours", 24)))
    min_bytes = max(0, int(float(config.get("capture_compress_min_mb", 16)) * 1024 * 1024))
    cutoff = time.time() - hours * 3600
    zstd = find_zstd()
    if zstd is None:
        raise RuntimeError("找不到 zstd，请先安装后再启用旧采集文件压缩")

    results: List[Dict[str, Any]] = []
    for source in sorted(root.glob("capture-*.jsonl")):
        if not CAPTURE_FILE_RE.match(source.name) or source.resolve() == capture_path:
            continue
        stat = source.stat()
        if not source.is_file() or source.is_symlink():
            continue
        if not force and stat.st_mtime > cutoff:
            continue
        if stat.st_size < min_bytes:
            continue
        destination = source.with_name(source.name + ".zst")
        temporary = source.with_name(source.name + ".zst.tmp")
        item: Dict[str, Any] = {
            "source": str(source),
            "destination": str(destination),
            "sourceBytes": stat.st_size,
        }
        if destination.exists():
            verification = verify_compressed_copy(source, destination, zstd=zstd)
            item.update(verification)
            item["action"] = "removed-source-after-existing-copy-verified"
            if not dry_run:
                source.unlink()
            results.append(item)
            continue
        if dry_run:
            item["action"] = "would-compress"
            results.append(item)
            continue

        try:
            subprocess.run(
                [zstd, "-3", "-T0", "--no-progress", "-f", "-o", str(temporary), str(source)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            verification = verify_compressed_copy(source, temporary, zstd=zstd)
            os.replace(temporary, destination)
            source.unlink()
            item.update(verification)
            item["action"] = "compressed"
            results.append(item)
        except Exception as exc:
            item["action"] = "failed"
            item["error"] = str(exc)
            results.append(item)
            raise
        finally:
            if temporary.exists():
                temporary.unlink()
    return results


def run_maintenance(
    config: Dict[str, Any],
    do_database: bool = True,
    do_capture: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "startedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dryRun": dry_run,
    }
    if do_database:
        result["database"] = (
            {"action": "would-backup", "source": str(get_path(config, "dashboard_db_path"))}
            if dry_run
            else backup_database(config)
        )
    if do_capture:
        result["captures"] = compress_capture_files(
            config, force=force, dry_run=dry_run
        )
    result["finishedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def acquire_lock(config: Optional[Dict[str, Any]] = None) -> Any:
    lock_path = get_path(load_config() if config is None else config, "run_dir") / "maintenance.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("已有维护任务正在运行") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(description="PS3838 数据库备份与采集文件维护")
    parser.add_argument("--db-only", action="store_true", help="只备份数据库")
    parser.add_argument("--capture-only", action="store_true", help="只压缩旧采集文件")
    parser.add_argument("--force", action="store_true", help="忽略文件年龄阈值")
    parser.add_argument("--dry-run", action="store_true", help="只展示将要执行的操作")
    args = parser.parse_args()
    if args.db_only and args.capture_only:
        parser.error("--db-only 与 --capture-only 不能同时使用")

    with runtime_data_lock(PROJECT_DIR):
        config = load_config()
        lock = acquire_lock(config)
        try:
            result = run_maintenance(
                config,
                do_database=not args.capture_only,
                do_capture=not args.db_only,
                force=args.force,
                dry_run=args.dry_run,
            )
        finally:
            lock.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
