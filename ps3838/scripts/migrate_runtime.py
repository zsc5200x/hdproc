#!/usr/bin/env python3
"""Move local runtime data into var/ with an explicit, reversible manifest."""
from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from project_config import PATH_ALIASES, config_path as selected_config_path, get_path, load_config
from src.core.runtime_lock import runtime_data_lock
CONFIG_PATHS = {
    "capture_path": "var/capture/capture.jsonl",
    "events_file": "var/exports/events_list.csv",
    "live_snapshot_file": "var/exports/latest_odds_live.csv",
    "latest_output_file": "var/exports/latest_odds_10_matches.csv",
    "normalized_output_file": "var/exports/latest_odds_normalized.csv",
    "dashboard_db_path": "var/db/odds_history.sqlite3",
    "merge_db_path": "var/db/fixture_merge.sqlite3",
    "log_dir": "var/logs",
    "backup_dir": "var/backups",
    "run_dir": "var/run",
    "crown_data_dir": "var/crown",
}
LEGACY_PATHS = {key: Path(value).name for key, value in CONFIG_PATHS.items()}
LEGACY_PATHS.update(crown_data_dir="crown/data", run_dir=".run")


def configured_value(config, key):
    for candidate in (key,) + PATH_ALIASES.get(key, ()):
        if config.get(candidate):
            return config[candidate]
    return LEGACY_PATHS[key]


def build_plan(root, config=None):
    """Only move known project-owned paths; never walk arbitrary config paths."""
    entries = {}
    protected = set()
    for key in CONFIG_PATHS:
        configured = Path(str(configured_value(config or {}, key))).expanduser()
        configured = configured if configured.is_absolute() else root / configured
        if configured.absolute() != (root / LEGACY_PATHS[key]).absolute():
            protected.add(configured.absolute())
    for key, target in CONFIG_PATHS.items():
        source = LEGACY_PATHS[key]
        path = root / source
        if path.exists() and not path.is_symlink():
            entries[source] = {"source": source, "target": target, "link": True}
    for pattern, folder in (("capture-*.jsonl*", "capture"), ("latest_odds*.csv", "exports"), ("*.sqlite3", "db")):
        for path in sorted(root.glob(pattern)):
            if path.is_file() and not path.is_symlink() and path.name not in entries:
                entries[path.name] = {"source": path.name, "target": f"var/{folder}/{path.name}", "link": False}
    for source, entry in list(entries.items()):
        if source.endswith(".sqlite3"):
            for suffix in ("-wal", "-shm"):
                if (root / (source + suffix)).exists():
                    entries[source + suffix] = {"source": source + suffix, "target": entry["target"] + suffix, "link": False}
    return [entry for entry in entries.values()
            if not any((root / entry["source"]).absolute() == path
                       or path in (root / entry["source"]).absolute().parents
                       or (root / entry["source"]).absolute() in path.parents
                       or str((root / entry["source"]).absolute()) in {str(path) + "-wal", str(path) + "-shm"}
                       for path in protected)]


def ensure_stopped(root, config=None):
    """Refuse migration while known services or files are still in use."""
    process_ids = set()
    directories = {root / ".run", root / "var/run", root / "crown/.run"}
    if config is not None:
        directories.add(get_path(config, "run_dir"))
    for directory in directories:
        for name in ("capture.pid", "live_snapshot.pid", "dashboard.pid", "crown.pid"):
            path = directory / name
            if path.exists():
                value = path.read_text().strip()
                if value.isdigit() and int(value) > 0:
                    process_ids.add(int(value))
    for pid in process_ids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise RuntimeError(f"进程 {pid} 仍存活；先运行 ./stop.sh，再执行迁移。")
    for directory in (root / "crown/data", root / "var/crown"):
        if directory.exists():
            result = subprocess.run(["lsof", "-t", "+D", str(directory.resolve())], capture_output=True, text=True)
            if result.stdout.strip():
                raise RuntimeError("皇冠数据目录仍被进程使用；先停止服务。")
            if result.returncode not in (0, 1) or result.stderr.strip():
                raise RuntimeError("无法确认皇冠数据目录是否仍被使用，未迁移。")
    # File checks also catch processes launched without PID files.
    files = [str(path) for pattern in ("*.sqlite3", "capture.jsonl", "var/db/*.sqlite3", "var/capture/capture.jsonl") for path in root.glob(pattern)]
    if files:
        result = subprocess.run(["lsof", "-t", "--", *files], capture_output=True, text=True)
        if result.stdout.strip():
            raise RuntimeError("采集或数据库文件仍被使用；先停止相关进程。")
        if result.returncode not in (0, 1) or result.stderr.strip():
            raise RuntimeError("无法确认数据文件是否仍被使用，未迁移。")


def verify_databases(root, plan):
    checked = []
    paths = [root / item["source"] for item in plan if item["source"].endswith(".sqlite3")]
    paths += list((root / "crown/data").glob("*.sqlite3"))
    for path in paths:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)) as conn:
            if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise RuntimeError(f"数据库完整性检查失败：{path.name}")
        checked.append(str(path.relative_to(root)))
    return checked


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def update_config(root, config, plan):
    updated = dict(config)
    sources = {item["source"]: item["target"] for item in plan}
    for key, target in CONFIG_PATHS.items():
        old = LEGACY_PATHS[key]
        configured = configured_value(config, key)
        configured_path = Path(str(configured)).expanduser()
        if not configured_path.is_absolute():
            configured_path = root / configured_path
        if old in sources and configured_path.absolute() == (root / old).absolute():
            updated[key] = target
    if "fixture_merge_db_path" in updated and "merge_db_path" in updated:
        updated["fixture_merge_db_path"] = updated["merge_db_path"]
    return updated


def apply(root, config_path, *, check_processes=True, effective_config=None):
    config_before = config_path.read_text(encoding="utf-8") if config_path.exists() else None
    config = json.loads(config_before) if config_before is not None else {}
    if not isinstance(config, dict):
        raise ValueError("配置必须是 JSON 对象")
    planning_config = config if effective_config is None else effective_config
    plan = build_plan(root, planning_config)
    if not plan:
        return None
    if check_processes:
        ensure_stopped(root, effective_config)
    for entry in plan:
        target = root / entry["target"]
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"迁移目标已存在，未覆盖：{entry['target']}")
    checked = verify_databases(root, plan)
    # SQLite may remove an unused WAL/SHM when its last connection closes.
    plan = build_plan(root, planning_config)
    destination = root / "var/migrations" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    manifest_path = destination / "manifest.json"
    manifest = {"version": 1, "root": str(root), "configPath": str(config_path), "configBefore": config_before, "entries": plan, "moved": [], "verifiedDatabases": checked, "status": "running"}
    write_json(manifest_path, manifest)
    try:
        for entry in plan:
            source, target = root / entry["source"], root / entry["target"]
            # Record intent first, so an interrupted rename remains recoverable.
            manifest["moved"].append(entry)
            write_json(manifest_path, manifest)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            if entry["link"]:
                source.symlink_to(os.path.relpath(target, source.parent), target_is_directory=target.is_dir())
        write_json(config_path, update_config(root, config, plan))
        manifest["status"] = "completed"
        write_json(manifest_path, manifest)
    except BaseException:
        rollback(manifest_path, check_processes=False)
        raise
    return manifest_path


def rollback(manifest_path, *, check_processes=True):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = Path(manifest["root"])
    if manifest.get("status") == "rolled_back":
        return
    if check_processes:
        ensure_stopped(root)
    for entry in reversed(manifest["moved"]):
        source, target = root / entry["source"], root / entry["target"]
        if source.is_symlink():
            if source.resolve() != target.resolve():
                raise RuntimeError(f"兼容链接已变化，停止回退：{source}")
            source.unlink()
        if target.exists():
            if source.exists():
                raise RuntimeError(f"旧路径已存在，停止回退：{source}")
            target.rename(source)
    config_path = Path(manifest["configPath"])
    if manifest["configBefore"] is None:
        config_path.unlink(missing_ok=True)
    else:
        temporary = config_path.with_name(config_path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(manifest["configBefore"])
        os.replace(temporary, config_path)
    manifest["status"] = "rolled_back"
    write_json(manifest_path, manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--apply", action="store_true")
    actions.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    config_path = selected_config_path()
    if not args.apply and not args.rollback:
        print(json.dumps(build_plan(ROOT, load_config()), ensure_ascii=False, indent=2))
        return 0
    with runtime_data_lock(ROOT, exclusive=True), ExitStack() as stack:
        config = load_config()
        # Also respect maintenance launched before the shared guard was added.
        for path in {ROOT / ".run/maintenance.lock", get_path(config, "run_dir") / "maintenance.lock"}:
            if path.exists():
                handle = stack.enter_context(path.open("a"))
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError("已有维护任务正在运行，请完成后再迁移。") from exc
        ensure_stopped(ROOT, config)
        if args.rollback:
            rollback(args.rollback)
            print("运行数据迁移已回退")
        else:
            manifest = apply(ROOT, config_path, effective_config=config)
            print(f"迁移完成；回退清单：{manifest}" if manifest else "无需迁移：已使用新目录或没有旧数据")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"迁移失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
