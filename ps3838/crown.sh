#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"
source "$PROJECT_DIR/scripts/runtime.sh"
CROWN_PYTHON_BIN="${CROWN_PYTHON_BIN:-$PROJECT_DIR/crown/.venv/bin/python}"
runtime_load_config "$CROWN_PYTHON_BIN"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# Reuse the configured normal browser; never submit a login form here.
echo "皇冠将复用 Chrome：$CROWN_CDP_URL"
runtime_start crown "$PROJECT_DIR/crown/app.py" "$CROWN_PYTHON_BIN" \
  "$CROWN_URL/api/crown/session" "$CROWN_PORT"
echo "皇冠服务：$CROWN_URL/crown"
