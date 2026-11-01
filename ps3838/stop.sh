#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/scripts/runtime.sh"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" && -x "$PROJECT_DIR/../app/.venv/bin/python" ]]; then
  # Merged layout: the main runtime lives in hdproc/app/.venv.
  PYTHON_BIN="$PROJECT_DIR/../app/.venv/bin/python"
fi
runtime_load_config "$PYTHON_BIN"

# Stop writers before servers and only touch processes owned by this project.
runtime_stop live_snapshot "$PROJECT_DIR/live_snapshot.py"
runtime_stop capture "$PROJECT_DIR/capture.py"
runtime_stop partner_orders "$PROJECT_DIR/partner_orders_poll.py"
runtime_stop dashboard "$PROJECT_DIR/dashboard_server.py"
rm -f "${DASHBOARD_SOCKET:-$RUN_DIR/dashboard.sock}"
runtime_stop crown "$PROJECT_DIR/crown/app.py" "$CROWN_PORT"
rm -f "$RUN_DIR/chrome.pid"
echo "已处理本项目服务停止请求。无法确认归属的进程已跳过，Chrome 不会被关闭。"
