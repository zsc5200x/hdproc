#!/usr/bin/env bash
set -euo pipefail

# Shared macOS bootstrap for Start_AG_Matrix_* scripts.
# It self-heals broken venvs after folder copy to another Mac.

PYTHON_CMD=""

hdzz_prepare_mac_runtime() {
  local root="${1:?missing root}"
  local app_dir="${2:?missing app_dir}"

  if [[ ! -f "${app_dir}/requirements.txt" ]]; then
    echo "[ERROR] 未找到 ${app_dir}/requirements.txt"
    return 2
  fi

  local python_bin="${PYTHON_BIN:-python3}"
  if ! command -v "${python_bin}" >/dev/null 2>&1; then
    echo "[ERROR] 未找到 ${python_bin}。请先安装 Python 3（推荐 3.11+）。"
    return 2
  fi

  local venv_dir="${app_dir}/.venv"
  local recreate_venv="0"
  local venv_python=""

  if [[ -x "${venv_dir}/bin/python3" ]]; then
    venv_python="${venv_dir}/bin/python3"
  elif [[ -x "${venv_dir}/bin/python" ]]; then
    venv_python="${venv_dir}/bin/python"
  else
    recreate_venv="1"
  fi

  if [[ "${recreate_venv}" == "0" ]]; then
    if ! "${venv_python}" - <<PY >/dev/null 2>&1
import os, sys
venv = os.path.realpath("${venv_dir}")
prefix = os.path.realpath(getattr(sys, "prefix", "") or "")
exe = os.path.realpath(getattr(sys, "executable", "") or "")
ok = prefix.startswith(venv) and exe.startswith(venv)
raise SystemExit(0 if ok else 1)
PY
    then
      recreate_venv="1"
    fi
  fi

  if [[ "${recreate_venv}" == "1" ]]; then
    echo "[INFO] 检测到虚拟环境不可用，自动重建：${venv_dir}"
    rm -rf "${venv_dir}"
    "${python_bin}" -m venv "${venv_dir}"
  fi

  # shellcheck disable=SC1091
  source "${venv_dir}/bin/activate"

  local py_cmd="python"
  if ! command -v "${py_cmd}" >/dev/null 2>&1; then
    py_cmd="python3"
  fi
  if ! command -v "${py_cmd}" >/dev/null 2>&1; then
    echo "[ERROR] 虚拟环境内未找到 python/python3。"
    return 2
  fi
  PYTHON_CMD="${py_cmd}"

  export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-${app_dir}/.run/ms-playwright}"
  mkdir -p "${PLAYWRIGHT_BROWSERS_PATH}"

  export PYTHONUTF8=1
  export PYTHONIOENCODING="utf-8"
  export PYTHONPATH="${root}/src:${app_dir}"
  export NO_PROXY="127.0.0.1,localhost"
  export no_proxy="127.0.0.1,localhost"

  # 0=自动优先系统 Chrome，失败再回退内置 Chromium（跨机器更稳）。
  export HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY="${HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY:-0}"

  export HDZZ_LOG_DIR="${HDZZ_LOG_DIR:-${app_dir}/.run/logs}"
  mkdir -p "${HDZZ_LOG_DIR}"

  # Seed a private first-run config from the template. Live credentials are
  # intentionally excluded from release bundles and must never be copied into
  # an installed app by the build step.
  local config_path="${app_dir}/config.json"
  local config_seed="${app_dir}/config.example.json"
  if [[ ! -f "${config_path}" && -f "${config_seed}" ]]; then
    cp "${config_seed}" "${config_path}"
    chmod 600 "${config_path}" || true
  fi

  local stamp="${venv_dir}/.bootstrap.stamp"
  local need_bootstrap="0"
  if [[ ! -f "${stamp}" ]]; then
    need_bootstrap="1"
  elif [[ "${app_dir}/requirements.txt" -nt "${stamp}" ]]; then
    need_bootstrap="1"
  elif [[ "${BASH_SOURCE[0]}" -nt "${stamp}" ]]; then
    need_bootstrap="1"
  fi

  if [[ "${need_bootstrap}" == "0" ]]; then
    if ! "${PYTHON_CMD}" - <<'PY' >/dev/null 2>&1
import importlib.util
mods = ("requests", "playwright")
ok = all(importlib.util.find_spec(m) is not None for m in mods)
raise SystemExit(0 if ok else 1)
PY
    then
      need_bootstrap="1"
    fi
  fi

  if [[ "${need_bootstrap}" == "1" ]]; then
    echo "[INFO] 初始化依赖（首次/依赖变更时执行）..."
    "${PYTHON_CMD}" -m pip install -U pip setuptools wheel
    "${PYTHON_CMD}" -m pip install -r "${app_dir}/requirements.txt"
    "${PYTHON_CMD}" -m playwright install chromium
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "${stamp}"
  fi

  echo "[INFO] Python: $("${PYTHON_CMD}" -V 2>&1)"
  echo "[INFO] Playwright 模式：HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY=${HDZZ_PLAYWRIGHT_SYSTEM_CHROME_ONLY}"
  echo "[INFO] 日志目录：${HDZZ_LOG_DIR}"
}

