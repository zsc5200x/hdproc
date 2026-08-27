const state = { status: null, chats: [], chatsLoaded: false, events: [], dryRun: true, modeDirty: false };
const $ = (id) => document.getElementById(id);
const LOG_REFRESH_INTERVAL_MS = 1000;

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
  return payload;
}

const messages = {
  credentials_missing: "软件预置凭据缺失",
  telegram_login_required: "请先连接 Telegram 账号",
  source_chats_missing: "请至少选择一个来源群",
  telegram_session_expired: "Telegram 登录已过期，请重新验证",
  invalid_phone: "手机号格式不正确，请包含国家区号",
  invalid_login_code: "验证码格式不正确",
};
const eventStatusLabels = {
  injected: "已进入跟单",
  dry_run: "干跑匹配成功",
  duplicate_order: "重复订单",
  duplicate_message: "重复消息",
  rejected_parse: "无法解析",
  rejected_match: "比赛未匹配",
  worker_error: "监听异常",
  ignored_disabled: "监听已关闭",
  ignored_source: "非监听来源",
  matched: "匹配成功",
  accepted: "已接收",
  rejected: "已拒绝",
  error: "处理异常",
};
const eventReasonLabels = {
  unsupported_signal: "非注单消息或格式不支持",
  invalid_line: "盘口格式不正确",
  missing_match: "缺少比赛信息",
  missing_bet_line: "缺少盘口信息",
  unsupported_market: "暂不支持该玩法",
  missing_order_id: "缺少有效订单号",
  missing_time: "缺少有效订单时间",
  missing_league: "缺少联赛信息",
  missing_bet_type: "缺少玩法信息",
  missing_content: "缺少注单内容",
  missing_amount: "缺少有效金额",
  ambiguous_number_match: "比赛编号匹配到多场",
  number_pair_not_found: "未找到对应比赛编号",
  ambiguous_name_match: "球队名称匹配到多场",
  name_match_not_found: "未找到对应比赛",
  missing_gid: "比赛缺少 GID",
  telegram_protocol_sync_error: "Telegram 同步异常，监听器已自动重连",
  telegram_login_required: "Telegram 尚未登录",
  telegram_session_expired: "Telegram 登录已过期",
  source_chats_missing: "尚未选择监听来源",
};
const explain = (error) => messages[String(error?.message || error)] || String(error?.message || error || "操作失败");

let toastTimer = 0;
function toast(text) {
  const node = $("toast");
  node.textContent = text;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2600);
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.dataset.label ||= button.textContent;
  button.textContent = busy ? "处理中..." : button.dataset.label;
}

function setHint(id, text, tone = "") {
  const node = $(id);
  node.textContent = text;
  node.className = `hint ${tone}`.trim();
}

function updateMode(dryRun, { markDirty = false } = {}) {
  state.dryRun = !!dryRun;
  if (markDirty) state.modeDirty = true;
  $("dryRunMode").setAttribute("aria-pressed", String(state.dryRun));
  $("automaticMode").setAttribute("aria-pressed", String(!state.dryRun));
  setHint("actionHint", state.dryRun ? "当前为干跑检查，不会产生订单。" : "自动跟单已选择，启动后匹配成功会写入订单。", state.dryRun ? "" : "error");
}

function renderStatus() {
  const data = state.status || {};
  const settings = data.settings || {};
  const connected = !!data.session_configured;
  const selected = settings.source_chat_ids || [];
  const alive = data.worker_alive == null ? !!data.worker_running : !!data.worker_alive;
  const running = !!data.worker_running;
  const unhealthy = alive && !running;
  if (!state.modeDirty) state.dryRun = settings.dry_run !== false;
  updateMode(state.dryRun);

  $("credentialBadge").textContent = data.credentials_configured ? "API 凭据已预置" : "预置凭据缺失";
  $("credentialBadge").classList.toggle("ok", !!data.credentials_configured);
  $("loginForm").hidden = connected;
  $("connectedAccount").hidden = !connected;
  $("phoneMasked").textContent = data.phone_masked || "Telegram 已连接";
  $("workerDot").classList.toggle("running", running);
  $("workerText").textContent = running ? `监听运行中 · PID ${data.worker_pid || "-"}` : unhealthy ? `监听连接异常 · PID ${data.worker_pid || "-"}` : "监听未启动";
  $("accountStage").className = `stage ${connected ? "done" : "active"}`;
  $("accountStageText").textContent = connected ? (data.phone_masked || "已连接") : "等待登录";
  $("sourceStage").className = `stage ${selected.length ? "done" : connected ? "active" : ""}`;
  $("sourceStageText").textContent = selected.length ? `${selected.length} 个来源` : "未选择";
  $("ingestStage").className = `stage ${running ? "active" : ""}`;
  $("ingestStageText").textContent = running ? (state.dryRun ? "干跑监听中" : "自动跟单中") : unhealthy ? "连接异常" : "已停止";
  $("startBtn").disabled = alive;
  $("stopBtn").disabled = !alive;
  $("dryRunMode").disabled = alive;
  $("automaticMode").disabled = alive;
  if (unhealthy) setHint("actionHint", `监听进程仍在，但 Telegram 连接不健康：${data.worker_error || "等待自动恢复"}`, "error");
}

function selectedChatIds() {
  return [...document.querySelectorAll("#groupList input[type=checkbox]:checked")].map((node) => node.value);
}

function updateSelectedCount() {
  $("selectedCount").textContent = `已选择 ${selectedChatIds().length} 个`;
}

function renderChats() {
  const list = $("groupList");
  const query = $("groupSearch").value.trim().toLowerCase();
  const selected = new Set(state.status?.settings?.source_chat_ids || []);
  const rows = state.chats.filter((chat) => `${chat.title} ${chat.id}`.toLowerCase().includes(query));
  list.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.status?.session_configured ? "没有匹配的群组" : "账号连接后读取群组";
    list.append(empty);
    updateSelectedCount();
    return;
  }
  for (const chat of rows) {
    const label = document.createElement("label");
    label.className = "group-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = String(chat.id);
    checkbox.checked = selected.has(String(chat.id));
    checkbox.addEventListener("change", updateSelectedCount);
    const copy = document.createElement("span");
    copy.className = "group-copy";
    const title = document.createElement("b");
    title.textContent = chat.title || "未命名群组";
    const meta = document.createElement("small");
    meta.textContent = `${chat.type || "group"} · ${chat.id}`;
    copy.append(title, meta);
    label.append(checkbox, copy);
    list.append(label);
  }
  updateSelectedCount();
}

function formatTime(ts) {
  if (!ts) return "-";
  return new Date(Number(ts) * 1000).toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)}s` : `${milliseconds.toFixed(milliseconds < 10 ? 1 : 0)}ms`;
}

function eventStatusLabel(status) {
  const key = String(status || "").trim().toLowerCase();
  return eventStatusLabels[key] || (key ? "其他状态" : "-");
}

function eventReasonLabel(reason) {
  const key = String(reason || "").trim();
  return eventReasonLabels[key] || key;
}

function eventDescription(item) {
  const parts = [eventReasonLabel(item.reason) || item.order_id || "-"];
  const processing = formatDuration(item.processing_ms);
  const parse = formatDuration(item.parse_ms);
  const games = formatDuration(item.games_load_ms);
  const refresh = formatDuration(item.games_refresh_ms);
  if (processing) parts.push(`总耗时 ${processing}`);
  if (parse) parts.push(`解析 ${parse}`);
  if (games) parts.push(`比赛 ${games}`);
  if (refresh) parts.push(`强刷 ${refresh}`);
  const sources = {
    snapshot_cache: "本地盘口缓存",
    recent_order: "最近订单",
    webui_member_cache: "会员比赛缓存",
  };
  if (sources[item.match_source]) parts.push(sources[item.match_source]);
  return parts.join(" · ");
}

function renderEvents() {
  const body = $("eventRows");
  body.replaceChildren();
  $("eventCount").textContent = `${state.events.length} 条`;
  if (!state.events.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty";
    cell.textContent = "暂无解析记录";
    row.append(cell);
    body.append(row);
    return;
  }
  for (const item of state.events) {
    const row = document.createElement("tr");
    const values = [
      formatTime(item.ts), item.chat_title || item.chat_id || "-", eventStatusLabel(item.status),
      [item.league, item.match].filter(Boolean).join(" · ") || "-", item.line || "-", item.amount || "-", eventDescription(item),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 0 || index === 5) cell.classList.add("mono");
      if (index === 2) {
        const badge = document.createElement("span");
        badge.className = `status-pill ${String(item.status || "").replace(/[^a-z_]/g, "")}`;
        badge.textContent = value;
        if (item.status) badge.title = `内部状态：${item.status}`;
        cell.append(badge);
      } else cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  }
}

async function loadStatus() {
  state.status = await api("/api/telegram-signal/status");
  renderStatus();
}
async function loadChats() {
  try {
    const result = await api("/api/telegram-signal/chats");
    state.chats = result.chats || [];
    state.chatsLoaded = true;
    renderChats();
    return result;
  } catch (error) {
    state.chats = [];
    state.chatsLoaded = false;
    renderChats();
    throw error;
  }
}
async function loadLogs() {
  const result = await api("/api/telegram-signal/logs?limit=150");
  state.events = result.rows || [];
  renderEvents();
}

async function saveSettings() {
  const savedSettings = state.status?.settings || {};
  const ids = state.chatsLoaded
    ? selectedChatIds()
    : (savedSettings.source_chat_ids || []).map(String);
  const sourceChats = state.chatsLoaded
    ? state.chats.filter((chat) => ids.includes(String(chat.id)))
    : (savedSettings.source_chats || []);
  const result = await api("/api/telegram-signal/settings", {
    method: "POST",
    body: JSON.stringify({ dry_run: state.dryRun, source_chat_ids: ids, source_chats: sourceChats }),
  });
  state.status.settings = result.settings;
  state.modeDirty = false;
  renderStatus();
  renderChats();
  return result;
}

$("refreshBtn").addEventListener("click", () => Promise.all([loadStatus(), loadLogs()]).catch((error) => toast(explain(error))));
$("groupSearch").addEventListener("input", renderChats);
$("dryRunMode").addEventListener("click", () => updateMode(true, { markDirty: true }));
$("automaticMode").addEventListener("click", () => updateMode(false, { markDirty: true }));
$("reloadChatsBtn").addEventListener("click", () => loadChats().catch((error) => toast(explain(error))));
$("saveGroupsBtn").addEventListener("click", () => saveSettings().then(() => toast("来源群已保存")).catch((error) => toast(explain(error))));

$("sendCodeBtn").addEventListener("click", async () => {
  const button = $("sendCodeBtn");
  setBusy(button, true);
  try {
    const result = await api("/api/telegram-signal/send-code", { method: "POST", body: JSON.stringify({ phone: $("phoneInput").value.trim().replace(/\s+/g, "") }) });
    setHint("loginHint", `验证码已发送到 ${result.phone_masked || "Telegram"}`, "ok");
  } catch (error) { setHint("loginHint", explain(error), "error"); }
  finally { setBusy(button, false); }
});

$("verifyCodeBtn").addEventListener("click", async () => {
  const button = $("verifyCodeBtn");
  setBusy(button, true);
  try {
    const result = await api("/api/telegram-signal/verify-code", { method: "POST", body: JSON.stringify({ code: $("codeInput").value.trim(), password: $("passwordInput").value }) });
    if (result.needs_password) throw new Error("请输入二步验证密码后再次验证");
    $("codeInput").value = "";
    $("passwordInput").value = "";
    await loadStatus();
    await loadChats();
    toast("Telegram 账号已连接");
  } catch (error) { setHint("loginHint", explain(error), "error"); }
  finally { setBusy(button, false); }
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/telegram-signal/logout", { method: "POST", body: "{}" });
  state.chats = [];
  state.chatsLoaded = false;
  await loadStatus();
  renderChats();
  toast("Telegram 账号已退出");
});

$("startBtn").addEventListener("click", async () => {
  const button = $("startBtn");
  setBusy(button, true);
  try {
    await saveSettings();
    await api("/api/telegram-signal/start", { method: "POST", body: "{}" });
    await loadStatus();
    toast(state.dryRun ? "干跑监听已启动" : "自动跟单监听已启动");
  } catch (error) { toast(explain(error)); }
  finally { setBusy(button, false); }
});

$("stopBtn").addEventListener("click", async () => {
  const button = $("stopBtn");
  setBusy(button, true);
  try {
    await api("/api/telegram-signal/stop", { method: "POST", body: "{}" });
    await loadStatus();
    toast("监听已停止");
  } catch (error) { toast(explain(error)); }
  finally { setBusy(button, false); }
});

async function initialize() {
  try {
    await Promise.all([loadStatus(), loadLogs()]);
    if (state.status.session_configured) await loadChats();
  } catch (error) { toast(explain(error)); }
}

initialize();
setInterval(() => {
  if (document.visibilityState !== "hidden") Promise.all([loadStatus(), loadLogs()]).catch(() => {});
}, LOG_REFRESH_INTERVAL_MS);
