#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "${ROOT}/scripts/Stop_AG_Matrix.command" "$@"
