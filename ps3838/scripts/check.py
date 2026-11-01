#!/usr/bin/env python3
"""Run isolated unit suites and static checks with the two existing runtimes."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "var",
             "backups", "logs", ".run", ".playwright-mcp", "screenshots"}


def source_files(suffix):
    for directory, folders, files in os.walk(ROOT):
        folders[:] = [name for name in folders if name not in SKIP_DIRS
                      and not name.startswith((".crown-", "_archive_cleanup_"))
                      and not Path(directory, name).is_symlink()]
        for name in sorted(files):
            path = Path(directory, name)
            if path.suffix == suffix and not path.is_symlink():
                yield path


def run(label, args, *, cwd=ROOT, env=None, timeout=180):
    print(f"\n[{label}]", flush=True)
    try:
        result = subprocess.run(args, cwd=cwd, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"检查失败：{exc}", file=sys.stderr)
        return False
    return result.returncode == 0


def isolated_env(directory):
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("PS3838_", "CAPTURE_", "CROWN_", "DASHBOARD_", "MERGE_", "FIXTURE_MERGE_", "HD_REPORT_")) or key in {"CDP_URL", "CHROME_BINARY", "CHROME_PROFILE_DIR", "START_URL", "EVENTS_FILE", "RUN_DIR", "LOG_DIR"}:
            env.pop(key, None)
    config = directory / "config.json"
    config.write_text(json.dumps({
        "capture_path": str(directory / "capture.jsonl"),
        "events_file": str(directory / "events.csv"),
        "live_snapshot_file": str(directory / "latest.csv"),
        "dashboard_db_path": str(directory / "history.sqlite3"),
        "merge_db_path": str(directory / "merge.sqlite3"),
        "crown_data_dir": str(directory / "crown-data"),
        "backup_dir": str(directory / "backups"),
        "run_dir": str(directory / "run"),
        "log_dir": str(directory / "logs"),
    }), encoding="utf-8")
    (directory / "events.csv").write_text("event_id,比赛,联赛,开赛时间\n", encoding="utf-8")
    (directory / "capture.jsonl").touch()
    env.update(PS3838_CONFIG=str(config), CROWN_DATA_DIR=str(directory / "crown-data"),
               PYTHONDONTWRITEBYTECODE="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--python", default=os.environ.get("PYTHON_BIN", str(ROOT / ".venv/bin/python")))
    parser.add_argument("--crown-python", default=os.environ.get("CROWN_PYTHON_BIN", str(ROOT / "crown/.venv/bin/python")))
    args = parser.parse_args()
    ok = True
    paths = list(source_files(".py"))
    for path in paths:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            print(exc, file=sys.stderr)
            ok = False
    print(f"Python 静态解析：{len(paths)} 个文件", flush=True)
    node = shutil.which("node")
    if not node:
        print("缺少 Node.js，无法校验前端", file=sys.stderr)
        ok = False
    else:
        for path in source_files(".js"):
            result = subprocess.run([node, "--input-type=module", "--check"], input=path.read_text(), text=True, capture_output=True)
            if result.returncode:
                print(f"{path}: {result.stderr}", file=sys.stderr)
                ok = False
        for path in sorted((ROOT / "tests/frontend").glob("*.test.*")):
            ok = run(f"前端 {path.name}", [node, "--test", str(path)]) and ok
    shell_files = [str(path) for path in source_files(".sh")]
    for path in shell_files:
        ok = run(f"Shell {Path(path).relative_to(ROOT)}", ["bash", "-n", path]) and ok
    ok = run("差异格式", ["git", "diff", "--check"]) and ok
    if not args.static_only:
        with tempfile.TemporaryDirectory(prefix="ps3838-check-") as temporary:
            env = isolated_env(Path(temporary))
            ok = run("平博、合并与公共模块测试", [args.python, "-m", "unittest", "discover", "-s", "tests", "-v"], env=env) and ok
            ok = run("皇冠测试", [args.crown_python, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py", "-v"], cwd=ROOT / "crown", env=env) and ok
    print("\n全部检查通过" if ok else "\n检查未通过，请修复以上失败", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
