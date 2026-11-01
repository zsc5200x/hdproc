#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
LABEL="com.ps3838-wss.maintenance"
SOURCE="$PROJECT_DIR/$LABEL.plist"
DESTINATION="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
DRY_RUN=0

case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=1 ;;
  -h|--help) echo "用法：./install_maintenance.sh [--dry-run]"; exit 0 ;;
  *) echo "未知参数：$1" >&2; exit 2 ;;
esac
[[ "$#" -le 1 ]] || { echo "参数过多" >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "找不到 Python：$PYTHON_BIN" >&2; exit 1; }

render_plist() {
  "$PYTHON_BIN" - "$SOURCE" "$1" <<'PY'
import os
import plistlib
import sys
from pathlib import Path

from project_config import PROJECT_DIR, config_path, get_path, load_config

config = load_config()
with Path(sys.argv[1]).open("rb") as handle:
    job = plistlib.load(handle)
job["ProgramArguments"] = [str(Path(sys.executable).absolute()), "-u", str(PROJECT_DIR / "maintenance.py")]
job["WorkingDirectory"] = str(PROJECT_DIR)
log_dir = get_path(config, "log_dir")
job["StandardOutPath"] = str(log_dir / "maintenance.log")
job["StandardErrorPath"] = str(log_dir / "maintenance.log")
environment = dict(job.get("EnvironmentVariables", {}))
selected = config_path()
if selected.exists():
    environment["PS3838_CONFIG"] = str(selected)
# Preserve only explicitly supplied maintenance overrides, never arbitrary secrets.
for name in ("DASHBOARD_DB_PATH", "BACKUP_DIR", "CAPTURE_OUTPUT", "CAPTURE_PATH", "RUN_DIR", "LOG_DIR"):
    if os.environ.get(name):
        environment[name] = os.environ[name]
job["EnvironmentVariables"] = environment
encoded = plistlib.dumps(job, sort_keys=False)
if sys.argv[2] == "-":
    sys.stdout.buffer.write(encoded)
else:
    log_dir.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[2]).write_bytes(encoded)
PY
}

if [[ "$DRY_RUN" == 1 ]]; then
  render_plist -
  exit 0
fi

[[ "$(uname -s)" == Darwin ]] || { echo "定时维护安装仅支持 macOS；其他系统请手动执行 ./maintenance.sh。" >&2; exit 1; }
TEMP_PLIST="$(mktemp -t ps3838-maintenance)"
trap 'rm -f "$TEMP_PLIST"' EXIT
render_plist "$TEMP_PLIST"
plutil -lint "$TEMP_PLIST" >/dev/null
mkdir -p "$(dirname "$DESTINATION")"
chmod 600 "$TEMP_PLIST"
cp "$TEMP_PLIST" "$DESTINATION"
chmod 600 "$DESTINATION"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$DESTINATION"
launchctl enable "gui/$(id -u)/$LABEL"

echo "已安装每日维护任务：$LABEL"
echo "执行时间：每天 04:15（本机时区）；本次安装不会立即运行维护任务。"
echo "任务文件：$DESTINATION"
echo "项目目录或日志配置变化后，请重新运行本安装脚本。"
