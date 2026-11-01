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
"$PROJECT_DIR/crown.sh"
runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" "$PYTHON_BIN" \
  "$DASHBOARD_URL/api/health" "$DASHBOARD_PORT" \
  --host "$DASHBOARD_HOST" --port "$DASHBOARD_PORT"
echo "统一看板：$DASHBOARD_URL"
