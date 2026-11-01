#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" && -x "$PROJECT_DIR/../app/.venv/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/../app/.venv/bin/python"
fi
exec "$PYTHON_BIN" "$PROJECT_DIR/scripts/check.py" "$@"
