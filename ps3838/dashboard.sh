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
mkdir -p "$RUN_DIR" "$LOG_DIR"

# The dashboard keeps the existing same-origin Crown proxy available.
if ! "$PROJECT_DIR/crown.sh"; then
  echo "皇冠服务未能就绪（不影响看板），请查看 $LOG_DIR/crown.log。" >&2
fi
RUNTIME_READY_ATTEMPTS="${RUNTIME_READY_ATTEMPTS:-90}"
DASHBOARD_SOCKET="${DASHBOARD_SOCKET:-$RUN_DIR/dashboard.sock}"
RUNTIME_HEALTH_SOCKET="$DASHBOARD_SOCKET"
runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" "$PYTHON_BIN" \
  "http://localhost/api/health" "" --socket "$DASHBOARD_SOCKET"
unset RUNTIME_HEALTH_SOCKET
echo "看板：unix:${DASHBOARD_SOCKET}（由 HD 9999 的 /ps3838/ 转发）"
