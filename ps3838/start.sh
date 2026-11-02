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

if ! "$PYTHON_BIN" -c 'import playwright' >/dev/null 2>&1; then
  echo "当前 Python 环境缺少 playwright：$PYTHON_BIN -m pip install -r requirements.txt" >&2
  exit 1
fi
mkdir -p "$RUN_DIR" "$LOG_DIR"

if [[ ! -f "$EVENTS_FILE" ]]; then
  echo "缺少赛事映射文件：$EVENTS_FILE" >&2
  exit 1
fi

mkdir -p "$(dirname "$CAPTURE_PATH")"
touch "$CAPTURE_PATH"

# 看板优先：PS38 页面只依赖看板；皇冠起不来时看板仍应可用，
# 否则整个标签页只会显示“连接失败”，还得手工再点一次启动。
# 看板改为监听 Unix socket（不占 TCP 端口），由 HD WebUI 的 9999 转发。
# 冷启动要载入 CSV/SQLite 历史，可能超过 30 秒，给足 90 秒，避免把
# 正在启动的看板误判为失败再杀掉（会造成反复“已发起启动”）。
RUNTIME_READY_ATTEMPTS="${RUNTIME_READY_ATTEMPTS:-90}"
DASHBOARD_SOCKET="${DASHBOARD_SOCKET:-$RUN_DIR/dashboard.sock}"
RUNTIME_HEALTH_SOCKET="$DASHBOARD_SOCKET"
DASHBOARD_HEALTH_URL="http://localhost/api/health"
DASHBOARD_OK=0
if [[ -d "$RUN_DIR/dashboard.start.lock" ]]; then
  # 另一个启动进程正在拉起看板：等它就绪，而不是立刻判失败再重复启动。
  echo "另一个启动进程正在拉起看板，等待其就绪..."
  for _ in {1..120}; do
    if runtime_http_ready "$DASHBOARD_HEALTH_URL"; then
      DASHBOARD_OK=1
      break
    fi
    [[ -d "$RUN_DIR/dashboard.start.lock" ]] || break
    sleep 1
  done
fi
if [[ "$DASHBOARD_OK" != 1 ]]; then
  if runtime_start dashboard "$PROJECT_DIR/dashboard_server.py" "$PYTHON_BIN" \
    "$DASHBOARD_HEALTH_URL" "" --socket "$DASHBOARD_SOCKET"; then
    DASHBOARD_OK=1
  else
    echo "dashboard 未能就绪，请查看 $LOG_DIR/dashboard.log。" >&2
  fi
fi
unset RUNTIME_HEALTH_SOCKET

# 平博赔率快照已停用：不再维护 latest_odds_live.csv。
if [[ -f "$PROJECT_DIR/var/run/hd_partner_sync.enabled" ]]; then
  echo "外部 partner_orders 轮询已由 HD 平博节点接管，跳过。"
else
  runtime_start partner_orders "$PROJECT_DIR/partner_orders_poll.py" "$PYTHON_BIN" "" "" \
    || echo "partner_orders 未能启动；请确认第三个标签页已登录代理后台。" >&2
fi

if [[ "$DASHBOARD_OK" != 1 ]]; then
  echo "看板未就绪，启动失败；请查看 $LOG_DIR/dashboard.log 后重试。" >&2
  exit 1
fi

echo
echo "项目进程已启动，看板已就绪。数据是否更新请查看看板状态。"
echo "看板：unix:${DASHBOARD_SOCKET}（由 HD 9999 的 /ps3838/ 转发，不再占用 8787）"
echo "停止命令：$PROJECT_DIR/stop.sh"
