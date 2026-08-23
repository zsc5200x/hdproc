const $ = (id) => document.getElementById(id);

const state = {
  orders: [],
  followQueue: [],
  followQueueCounts: {},
  orderFollowByOid: {},
  railFailureFilter: "",
  availableTargets: [],
  accounts: [],
  statuses: {},
  members: [],
  followSettings: null,
  signalPullConfig: {},
  tg: null,
  selectedTarget: "",
  search: "",
  lastTs: 0,
  auto: true,
  view: "orders",
  poolView: "overview",
  poolSelection: { kind: "", id: "" },
  poolDashboard: {
    search: "",
    source: "all",
    status: "all",
    channel: "all",
    memberStatus: "all",
  },
  systemStatus: {},
  sessionStartTs: Math.floor(Date.now() / 1000),
  mappings: {},
  orderDashboard: {
    chartsVisible: false,
    filters: {
      keyword: "",
      timeRange: "today",
      orderType: "",
      status: "",
      matchProgress: "",
      playCategory: "",
      league: "",
      amountBucket: "",
      sortBy: "time-desc",
    },
  },
  matchSearch: {
    query: "",
    mode: "all",
    loading: false,
    error: "",
    memberId: "",
    memberLabel: "",
    readLanes: {
      games: { memberId: "", memberLabel: "" },
      odds: { memberId: "", memberLabel: "" },
      events: { memberId: "", memberLabel: "" },
    },
    results: [],
    retained: [],
  },
  orderEventSnapshots: {},
  followEngine: {
    known: false,
    // Do not assume a running engine before the first /api/status response.
    // The old optimistic value made the control look like "stop" during a
    // slow initial load, so an early click could stop a healthy engine.
    running: false,
    pid: null,
    serverTime: "",
    updatedAt: 0,
    controlBusy: "",
  },
  systemCenter: {
    uptimeText: "--",
    version: "未提供",
    syncTone: "pending",
    lastSyncAt: 0,
    activity: [],
  },
};

if (typeof window !== "undefined") window.state = state;

const REMOTE_SIGNAL_FILTER_TARGET = "__remote_signal_pull__";
const MATCH_SEARCH_RETAINED_KEY = "hdproc.matchSearch.retained.v1";
const MATCH_SEARCH_MODES = ["live", "today", "early"];
const MATCH_ODDS_REFRESH_MS = 2000;

let addNodeFormState = null;
let matchSearchDebounceTimer = null;
let matchSearchRequestSeq = 0;
let matchOddsRefreshTimer = null;
let matchOddsActiveToken = "";
let matchOddsRequestSeq = 0;
let matchOddsRefreshing = false;
let matchOddsLastSnapshot = null;
let matchEventRefreshTimer = null;
let matchEventActiveToken = "";
let matchEventRequestSeq = 0;
let matchEventRefreshing = false;
let matchEventLastSnapshot = null;
let matchRetainedRefreshInFlight = false;
let refreshViewInFlight = false;
let orderStreamReady = false;
let realtimeOrderListRenderSignature = "";
let orderModalReturnFocus = null;
const followFormTools = (typeof window !== "undefined" && window.HDFollowSettingsForm) || {};
const followFormRenderGuard = typeof followFormTools.createFollowFormRenderGuard === "function"
  ? followFormTools.createFollowFormRenderGuard()
  : { dirty: false, rendering: false };
const followFormUiState = { saving: false };
const reportCardTools = (typeof window !== "undefined" && window.HDReportCards) || {};
const orderDashboardTools = (typeof window !== "undefined" && window.HDOrderDashboard) || {};
const orderListTools = (typeof window !== "undefined" && window.HDOrderList) || {};
const matchSearchTools = (typeof window !== "undefined" && window.HDMatchSearch) || {};
const orderRealtimeTools = (typeof window !== "undefined" && window.HDOrderRealtimeBridge) || {};

function updateFollowEngineState(data) {
  const payload = data || {};
  const hasRunning = Object.prototype.hasOwnProperty.call(payload, "running");
  const prev = state.followEngine || {};
  state.followEngine = {
    known: hasRunning,
    running: hasRunning ? !!payload.running : true,
    pid: payload.pid == null ? null : payload.pid,
    serverTime: String(payload.server_time || ""),
    updatedAt: Date.now(),
    controlBusy: String(prev.controlBusy || ""),
  };
  return state.followEngine;
}

function isFollowEngineStopped() {
  return !!(state.followEngine && state.followEngine.known && state.followEngine.running === false);
}

function buildFollowEngineControlModel(engineState, busyAction = "") {
  const source = engineState || {};
  const known = !!source.known;
  const running = !!source.running;
  const busy = String(busyAction || "").trim();
  const action = !known ? "start" : (running ? "stop" : "start");
  const isBusy = busy === "start" || busy === "stop";

  let statusText = "跟单状态检测中";
  let statusTone = "unknown";
  let actionLabel = action === "stop" ? "停止跟单" : "启动跟单";
  let buttonTone = action === "stop" ? "danger" : "primary";
  let iconClass = action === "stop" ? "fa-power-off" : "fa-play";

  if (known) {
    statusText = running ? "跟单运行中" : "跟单未启动";
    statusTone = running ? "running" : "stopped";
  }
  if (busy === "start") {
    statusText = "启动中...";
    statusTone = "busy";
    actionLabel = "启动中...";
    buttonTone = "primary";
    iconClass = "fa-rotate";
  } else if (busy === "stop") {
    statusText = "停止中...";
    statusTone = "busy";
    actionLabel = "停止中...";
    buttonTone = "danger";
    iconClass = "fa-rotate";
  }

  return {
    known,
    running,
    busy,
    isBusy,
    statusText,
    statusTone,
    action,
    actionLabel,
    buttonTone,
    iconClass,
    disabled: isBusy,
  };
}

function renderFollowEngineControls() {
  const wrap = $("follow-engine-controls");
  const statusEl = $("follow-engine-status");
  const statusTextEl = $("follow-engine-status-text");
  const toggleBtn = $("follow-engine-toggle");
  const toggleLabelEl = $("follow-engine-toggle-label");
  const toggleIconEl = $("follow-engine-toggle-icon");
  if (!wrap && !statusEl && !toggleBtn) return;

  const model = buildFollowEngineControlModel(
    state.followEngine,
    state.followEngine && state.followEngine.controlBusy,
  );

  if (wrap) {
    wrap.dataset.running = model.running ? "true" : "false";
    wrap.dataset.busy = model.isBusy ? "true" : "false";
  }
  if (statusEl) {
    statusEl.dataset.tone = model.statusTone;
    statusEl.title = model.statusText;
  }
  if (statusTextEl) statusTextEl.textContent = model.statusText;
  if (toggleBtn) {
    toggleBtn.disabled = !!model.disabled;
    toggleBtn.dataset.action = model.action;
    toggleBtn.setAttribute("aria-label", model.actionLabel);
    toggleBtn.title = model.actionLabel;
    toggleBtn.classList.remove("btn-primary", "btn-danger", "is-busy");
    toggleBtn.classList.add(model.buttonTone === "danger" ? "btn-danger" : "btn-primary");
    if (model.isBusy) toggleBtn.classList.add("is-busy");
  }
  if (toggleLabelEl) toggleLabelEl.textContent = model.actionLabel;
  if (toggleIconEl) {
    toggleIconEl.className = `fa-solid ${model.iconClass}`;
  }
  renderSystemCenterStatusBar();
}

async function performFollowEngineAction(action, deps = {}) {
  const actionName = String(action || "").trim() === "stop" ? "stop" : "start";
  if (!state.followEngine) return null;
  if (state.followEngine.controlBusy) return null;

  const request = typeof deps.request === "function" ? deps.request : fetchJson;
  const reload = typeof deps.reload === "function" ? deps.reload : loadStatuses;
  const notify = typeof deps.notify === "function" ? deps.notify : toast;
  const endpoint = actionName === "stop" ? "/api/stop" : "/api/start";

  state.followEngine.controlBusy = actionName;
  renderFollowEngineControls();

  try {
    const data = await request(endpoint, { method: "POST" });
    await reload();

    const status = String((data && data.status) || "").trim();
    if (notify) {
      if (actionName === "start") {
        notify(status === "already_running" ? "跟单引擎已在运行" : "已请求启动跟单引擎", "success");
      } else {
        notify(status === "not_running" ? "跟单引擎当前未运行" : "已请求停止跟单引擎", "info");
      }
    }
    return data;
  } catch (e) {
    if (notify) {
      notify(`跟单${actionName === "start" ? "启动" : "停止"}失败: ${String(e && e.message ? e.message : e)}`, "error");
    }
    throw e;
  } finally {
    state.followEngine.controlBusy = "";
    renderFollowEngineControls();
  }
}

function bindFollowEngineToggle() {
  const toggle = $("follow-engine-toggle");
  if (!toggle || toggle.dataset.bound === "true") return;
  toggle.dataset.bound = "true";
  toggle.addEventListener("click", async () => {
    const model = buildFollowEngineControlModel(
      state.followEngine,
      state.followEngine && state.followEngine.controlBusy,
    );
    try {
      await performFollowEngineAction(model.action);
    } catch {
      // toast handled in performFollowEngineAction
    }
  });
}

const VIEW_TITLES = {
  orders: "实时订单流",
  queue: "跟单队列",
  arsenal: "号池管理",
  "system-center": "系统中心",
  "follow-settings": "跟单策略",
  telegram: "消息通知配置",
  "signal-pull": "信号拉取配置",
  "tg-signal": "TG 信号接入",
};

// ── Toast ──────────────────────────────────────────────────────
function toast(message, type = "info", duration = 3500) {
  const icons = { success: "fa-circle-check", error: "fa-circle-xmark", info: "fa-circle-info", warning: "fa-triangle-exclamation" };
  const container = document.getElementById("toasts") || document.getElementById("toast-container");
  if (!container) { console.warn(message); return; }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `
    <i class="fa-solid ${icons[type] || icons.info} t-icon"></i>
    <span class="t-text">${escapeHtml(message)}</span>
    <span class="t-close" onclick="this.parentElement.remove()">×</span>
  `;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add("leaving");
    el.addEventListener("animationend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 300);
  }, duration);
}

// ── Formatters ─────────────────────────────────────────────────
function fmtTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtIso(ts) {
  if (!ts) return "-";
  try { return fmtTime(ts); } catch { return "-"; }
}

function orderReceivedTs(o) {
  const ts = Number(o && o.ts ? o.ts : 0);
  return Number.isFinite(ts) ? ts : 0;
}

function fmtOrderTime(o) {
  const ts = orderReceivedTs(o);
  return ts ? fmtTime(ts) : "-";
}

function accountLabel(a) {
  return (a && (a.username || a.name)) || "-";
}

function findAccountByTargetName(name) {
  return state.accounts.find((a) => (a.name || "") === (name || "")) || null;
}

function backendAccountOfOrder(o) {
  const a = findAccountByTargetName(o && o.target ? String(o.target) : "");
  const label = accountLabel(a);
  return label && label !== "-" ? label : (o && o.account ? String(o.account) : "-");
}

function includesText(o, q) {
  if (!q) return true;
  const s = q.toLowerCase();
  const fields = [o.orderId, backendAccountOfOrder(o), o.account, o.sport, o.league, o.sides, o.betType, o.line, o.odds, o.stake]
    .filter(Boolean).join(" ").toLowerCase();
  return fields.includes(s);
}

function mapFollowResultsByOrder(rows) {
  const map = {};
  for (const row of Array.isArray(rows) ? rows : []) {
    const oid = String((row && (row.orderId || row.order_id)) || "").trim();
    if (!oid) continue;
    if (!map[oid]) map[oid] = [];
    map[oid].push(row);
  }
  return map;
}

function setOrderFollowResults(rows) {
  const map = mapFollowResultsByOrder(rows);
  state.orderFollowByOid = map;
  if (typeof reportState !== "undefined") {
    reportState.followByOid = map;
  }
  return map;
}

function mergeTerminalQueueFollowRows(queueItems, resultRows) {
  const rows = Array.isArray(resultRows) ? resultRows : [];
  const map = mapFollowResultsByOrder(rows);
  for (const item of Array.isArray(queueItems) ? queueItems : []) {
    const queue = item && typeof item === "object" ? item : {};
    const order = queue.order && typeof queue.order === "object" ? queue.order : {};
    const oid = String(order.orderId || order.order_id || "").trim();
    if (!oid || map[oid]?.length) continue; // real FT_bet rows are authoritative

    const status = String(queue.status || "").trim().toLowerCase();
    if (!["done", "ok", "success", "succeeded", "failed", "error", "fail"].includes(status)) continue;
    const job = queue.job && typeof queue.job === "object" ? queue.job : {};
    const tickets = Array.isArray(job.tickets)
      ? job.tickets
      : (Array.isArray(job.split_tickets) ? job.split_tickets : []);
    const splitCount = Number(job.split_total || job.split_count || tickets.length || 0);
    if (tickets.length) {
      map[oid] = tickets.map((ticket, index) => ({
        orderId: oid,
        ok: true,
        ticket_id: typeof ticket === "object" ? ticket.ticket_id : ticket,
        split_index: index + 1,
        split_count: splitCount || tickets.length,
        recovered_from_queue_state: true,
      }));
    } else {
      map[oid] = [{
        orderId: oid,
        ok: ["done", "ok", "success", "succeeded"].includes(status),
        message: String(queue.reason || job.reason || job.last_message || ""),
        recovered_from_queue_state: true,
      }];
    }
  }
  const merged = Object.values(map).flat();
  setOrderFollowResults(merged);
  return merged;
}

function baseFilterOrders() {
  return state.orders.filter((o) => {
    if (state.selectedTarget === REMOTE_SIGNAL_FILTER_TARGET) {
      if (!isRemoteSignalOrder(o)) return false;
    } else if (state.selectedTarget && (o.target || "") !== state.selectedTarget) return false;
    if (!includesText(o, state.search)) return false;
    return true;
  });
}

function filterOrders() {
  const rows = baseFilterOrders();
  if (orderDashboardTools && typeof orderDashboardTools.filterAndSortOrders === "function") {
    return orderDashboardTools.filterAndSortOrders(rows, state.orderDashboard.filters);
  }
  return rows;
}

function isRemoteSignalOrder(row) {
  return !!(row && (row._skip_signal_forward === true || String(row._signal_origin || "").trim() === "remote_signal_pull"));
}

function getSignalSourceSidebarItems() {
  const helper = window.HDSidebarItems;
  if (!helper || typeof helper.buildSidebarItems !== "function") return [];
  return helper.buildSidebarItems({
    accounts: [],
    statuses: state.statuses,
    signalPullConfig: state.signalPullConfig,
    orders: state.orders,
  }).filter((item) => item && item.kind === "signal-source");
}

function computeTradingNodeStatus() {
  const helper = window.HDSidebarItems;
  if (helper && typeof helper.computeTradingNodeStatus === "function") {
    return helper.computeTradingNodeStatus({
      accounts: state.accounts,
      statuses: state.statuses,
      signalPullConfig: state.signalPullConfig,
    });
  }
  const onlineAccounts = state.accounts.filter((a) => {
    const name = String((a && a.name) || "").trim();
    return name && state.statuses[name] && state.statuses[name].status === "online";
  }).length;
  return { online: onlineAccounts, total: state.accounts.length, label: `${onlineAccounts} / ${state.accounts.length}` };
}

function renderTradingNodeKpi() {
  const el = $("kpi-trading-nodes");
  if (!el) return;
  const nodeStatus = computeTradingNodeStatus();
  el.textContent = nodeStatus.label;
  el.style.color = nodeStatus.online > 0 ? "var(--success)" : "var(--text-3)";
}

function computeKpis(orders) {
  const session = orders.length;
  return { today: session, all: orders.length };
}

function formatOrderDashboardMoney(value) {
  if (orderDashboardTools && typeof orderDashboardTools.formatDashboardMoney === "function") {
    return orderDashboardTools.formatDashboardMoney(value);
  }
  const n = Math.floor(Number(value || 0));
  return n >= 1000 ? `¥${(n / 1000).toFixed(1)}k` : `¥${n}`;
}

function formatDashboardAxisMoney(value) {
  const n = Math.floor(Number(value || 0));
  if (n >= 1000) return `¥${Math.round(n / 1000)}k`;
  return `¥${n}`;
}

function buildOrderDashboardModel() {
  if (orderDashboardTools && typeof orderDashboardTools.buildOrderDashboardModel === "function") {
    return orderDashboardTools.buildOrderDashboardModel(baseFilterOrders(), state.orderDashboard.filters);
  }
  const rows = filterOrders();
  return {
    filters: { leagues: [], playCategories: [] },
    stats: { totalOrders: rows.length, liveOrders: 0, earlyOrders: 0, totalStake: 0, averageStake: 0 },
    charts: { playDistribution: [], leagueDistribution: [], amountDistribution: [] },
    orders: rows,
  };
}

function pct(value, max) {
  const n = Number(value || 0);
  const m = Number(max || 0);
  if (!m || n <= 0) return 0;
  return Math.max(4, Math.min(100, Math.round((n / m) * 100)));
}

function setOrderDashboardPresetActive(key, value) {
  document.querySelectorAll(`[data-od-filter="${key}"]`).forEach((btn) => {
    const active = String(btn.getAttribute("data-value") || "") === String(value || "");
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function setSelectOptions(el, values, allLabel, current) {
  if (!el) return;
  const options = [`<option value="">${escapeHtml(allLabel)}</option>`]
    .concat((Array.isArray(values) ? values : []).map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`));
  el.innerHTML = options.join("");
  el.value = current || "";
}

function renderHorizontalBars(container, rows, options = {}) {
  if (!container) return;
  const items = Array.isArray(rows) ? rows : [];
  if (!items.length) {
    container.innerHTML = `<div class="od-empty-mini">暂无数据</div>`;
    return;
  }
  const max = Math.max(...items.map((item) => Number(options.valueKey === "stake" ? item.stake : item.count) || 0), 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const value = max * ratio;
    return `<span>${options.valueKey === "stake" ? escapeHtml(formatDashboardAxisMoney(value)) : escapeHtml(String(Math.round(value)))}</span>`;
  }).join("");
  const rowsHtml = items.slice(0, options.limit || 6).map((item) => {
    const value = Number(options.valueKey === "stake" ? item.stake : item.count) || 0;
    const label = String(item.label || "未分类");
    const filterAttr = options.filterKey ? ` data-od-chart-filter="${options.filterKey}" data-value="${escapeHtml(label)}"` : "";
    const colorClass = options.valueKey === "stake" ? "green" : "blue";
    return `
      <button type="button" class="od-axis-row"${filterAttr}>
        <span class="od-axis-label" title="${escapeHtml(label)}">${escapeHtml(label)}</span>
        <span class="od-axis-track">
          <span class="od-axis-fill ${colorClass}" style="width:${pct(value, max)}%"></span>
        </span>
        <span class="od-axis-value">${options.valueKey === "stake" ? escapeHtml(formatOrderDashboardMoney(value)) : escapeHtml(String(value))}</span>
        <span class="od-axis-tooltip">
          <strong>${escapeHtml(label)}</strong><br>
          订单数：${escapeHtml(String(item.count || 0))}<br>
          金额：${escapeHtml(formatOrderDashboardMoney(item.stake || 0))}
        </span>
      </button>
    `;
  }).join("");
  container.innerHTML = `
    <div class="od-axis-chart">
      <div class="od-axis-plot">
        <div class="od-axis-rows">${rowsHtml}</div>
      </div>
      <div class="od-axis-scale">${ticks}</div>
    </div>
  `;
}

function renderPlayDonut(container, rows) {
  if (!container) return;
  const items = Array.isArray(rows) ? rows : [];
  const total = items.reduce((sum, item) => sum + Number(item.count || 0), 0);
  if (!total) {
    container.innerHTML = `<div class="od-empty-mini">暂无数据</div>`;
    return;
  }
  const colorByLabel = { "大小球": "#57B5FF", "让球": "#37D4A3", "独赢": "#A78BFA", "其他": "#A78BFA" };
  let cursor = 0;
  const segments = items.map((item) => {
    const count = Number(item.count || 0);
    const start = cursor;
    const end = cursor + (count / total) * 360;
    cursor = end;
    return `${colorByLabel[item.label] || "#A78BFA"} ${start.toFixed(2)}deg ${end.toFixed(2)}deg`;
  }).join(",");
  const first = items[0];
  const second = items[1];
  container.innerHTML = `
    <div class="od-donut-scene">
      ${second ? `<button type="button" class="od-donut-callout left" data-od-chart-filter="playCategory" data-value="${escapeHtml(second.label)}">${escapeHtml(second.label)}: ${escapeHtml(String(second.count || 0))}</button>` : ""}
      <div class="od-donut" style="background:conic-gradient(${segments})" aria-label="玩法总数 ${escapeHtml(String(total))}"></div>
      ${first ? `<button type="button" class="od-donut-callout right" data-od-chart-filter="playCategory" data-value="${escapeHtml(first.label)}">${escapeHtml(first.label)}: ${escapeHtml(String(first.count || 0))}</button>` : ""}
    </div>
    <div class="od-donut-legend">
      ${items.map((item, idx) => `
        <button type="button" class="od-legend-item" data-od-chart-filter="playCategory" data-value="${escapeHtml(item.label)}">
          <span class="od-legend-dot ${item.label === "大小球" ? "blue" : item.label === "让球" ? "green" : "other"}"></span>
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(String(item.count || 0))}</strong>
        </button>
      `).join("")}
    </div>
  `;
}

function renderAmountDistribution(container, rows) {
  if (!container) return;
  const items = Array.isArray(rows) ? rows : [];
  const max = Math.max(...items.map((item) => Number(item.total || 0)), 1);
  const maxTick = Math.max(max, 1);
  const bars = items.map((item) => {
    const total = Number(item.total || 0);
    const height = pct(total, max);
    const sizeH = total ? Math.round((Number(item.sizeCount || 0) / total) * 100) : 0;
    const handicapH = total ? Math.round((Number(item.handicapCount || 0) / total) * 100) : 0;
    const otherH = Math.max(0, 100 - sizeH - handicapH);
    return `
      <button type="button" class="od-column-item" data-od-chart-filter="amountBucket" data-value="${escapeHtml(item.label)}">
        <span class="od-column-total">${escapeHtml(String(total))}</span>
        <span class="od-column-track" style="height:${height}%">
          <span class="od-column-part other" style="height:${otherH}%"></span>
          <span class="od-column-part handicap" style="height:${handicapH}%"></span>
          <span class="od-column-part size" style="height:${sizeH}%"></span>
        </span>
        <span class="od-column-label">${escapeHtml(item.label)}</span>
      </button>
    `;
  }).join("");
  container.innerHTML = `
    <div class="od-column-chart">
      <div class="od-column-legend">
        <span><i style="background:#57B5FF"></i>大小球</span>
        <span><i style="background:#37D4A3"></i>让球</span>
      </div>
      <div class="od-column-plot">
        <div class="od-y-axis">
          <span>${escapeHtml(String(maxTick))}</span>
          <span>${escapeHtml(String(Math.round(maxTick * 0.66)))}</span>
          <span>${escapeHtml(String(Math.round(maxTick * 0.33)))}</span>
          <span>0</span>
        </div>
        <div class="od-column-bars">${bars}</div>
      </div>
    </div>
  `;
}

// ── Execution insight rail (real follow-queue / follow-results data) ────
// Categories mirror src/hdzz/core/follower/engine.py::classify_execution_error()
// verbatim — no invented buckets.
const EXEC_ERROR_LABELS = {
  MEMBER_ERROR: { label: "会员异常", tone: "amber" },
  MARKET_ERROR: { label: "盘口异常", tone: "red" },
  MARKET_SUBMIT_REJECTED: { label: "盘口拒绝", tone: "red" },
  CONTEXT_RETRYABLE: { label: "可重试", tone: "amber" },
  INFRA_ERROR: { label: "网络/基础设施", tone: "red" },
  CONFIRMED_FAILED: { label: "确认失败", tone: "red" },
  STILL_UNKNOWN: { label: "结果未知", tone: "gray" },
  UNKNOWN_RESULT: { label: "结果未知", tone: "gray" },
  UNKNOWN_ERROR: { label: "其他异常", tone: "gray" },
};
const EXEC_TONE_COLOR = { green: "#37D4A3", amber: "#f7b83d", red: "#ff596b", gray: "#8ea8ff", cyan: "#57B5FF" };

function execErrorMeta(code) {
  const key = String(code || "").trim().toUpperCase();
  return EXEC_ERROR_LABELS[key] || { label: key || "未分类", tone: "gray" };
}

function latestFollowRowForOrder(oid) {
  const rows = state.orderFollowByOid && state.orderFollowByOid[oid];
  if (!Array.isArray(rows) || !rows.length) return null;
  let best = rows[0];
  for (const row of rows) {
    if (Number(row.ts_ms || row.ts || 0) >= Number(best.ts_ms || best.ts || 0)) best = row;
  }
  return best;
}

function computeFailureDistribution() {
  const byOid = state.orderFollowByOid || {};
  const buckets = {};
  let total = 0;
  for (const oid of Object.keys(byOid)) {
    const row = latestFollowRowForOrder(oid);
    // Rows recovered from follow_state.json (mergeTerminalQueueFollowRows) don't
    // carry a real error_class from the executor — skip rather than guess.
    if (!row || row.ok === true || row.recovered_from_queue_state) continue;
    const code = String(row.error_class || "UNKNOWN_ERROR").trim().toUpperCase() || "UNKNOWN_ERROR";
    buckets[code] = (buckets[code] || 0) + 1;
    total += 1;
  }
  const items = Object.keys(buckets).map((code) => {
    const meta = execErrorMeta(code);
    return { code, label: meta.label, tone: meta.tone, count: buckets[code] };
  }).sort((a, b) => b.count - a.count);
  return { items, total };
}

function renderExecutionPerformance() {
  const grid = $("od-perf-grid");
  if (!grid) return;
  const counts = state.followQueueCounts || {};
  const pending = Number(counts.pending_orders || 0);
  const jobs = Number(counts.jobs || 0);
  const doneOk = Number(counts.done_ok || 0);
  const doneFailed = Number(counts.done_failed || 0);
  const doneTotal = doneOk + doneFailed;
  const rate = doneTotal ? Math.round((doneOk / doneTotal) * 100) : null;
  const backlog = pending + jobs;

  const items = [
    { label: "等待提交", value: String(pending), tone: pending > 0 ? "amber" : "muted" },
    { label: "执行中", value: String(jobs), tone: jobs > 0 ? "cyan" : "muted" },
    { label: "已完成成功", value: String(doneOk), tone: "green" },
    { label: "已完成失败", value: String(doneFailed), tone: doneFailed > 0 ? "red" : "muted" },
    { label: "成功率", value: rate == null ? "—" : `${rate}%`, tone: rate == null ? "muted" : (rate >= 90 ? "green" : rate >= 60 ? "amber" : "red") },
    { label: "队列积压", value: String(backlog), tone: backlog > 0 ? "amber" : "muted" },
  ];
  grid.innerHTML = items.map((item) => `
    <div class="od-perf-item" data-tone="${item.tone}">
      <span class="od-perf-val">${escapeHtml(item.value)}</span>
      <span class="od-perf-label">${escapeHtml(item.label)}</span>
    </div>
  `).join("");

  const statusText = $("od-perf-status-text");
  const statusEl = $("od-perf-status");
  if (statusText && statusEl) {
    if (jobs > 0) { statusText.textContent = `执行中 ${jobs} 单`; statusEl.setAttribute("data-tone", "success"); }
    else if (pending > 0) { statusText.textContent = `等待 ${pending} 单`; statusEl.setAttribute("data-tone", "warning"); }
    else { statusText.textContent = "队列空闲"; statusEl.setAttribute("data-tone", "muted"); }
  }
  const updatedEl = $("od-perf-updated");
  if (updatedEl) updatedEl.textContent = new Date().toLocaleTimeString();

  const kpiPending = $("od-kpi-pending");
  if (kpiPending) kpiPending.textContent = String(pending);
  const kpiDoneOk = $("od-kpi-done-ok");
  if (kpiDoneOk) kpiDoneOk.textContent = String(doneOk);
  const kpiDoneFailed = $("od-kpi-done-failed");
  if (kpiDoneFailed) {
    kpiDoneFailed.textContent = String(doneFailed);
    kpiDoneFailed.className = "od-kpi-val" + (doneFailed > 0 ? " neg" : "");
  }
  const kpiRate = $("od-kpi-rate");
  if (kpiRate) kpiRate.textContent = rate == null ? "—" : `${rate}%`;
}

function renderFailureDonut() {
  const container = $("od-fail-donut");
  const totalEl = $("od-fail-total");
  if (!container) return;
  const { items, total } = computeFailureDistribution();
  if (totalEl) totalEl.textContent = `${total} 条`;
  if (!total) {
    container.innerHTML = `<div class="od-empty-mini">暂无失败样本</div>`;
    return;
  }
  let cursor = 0;
  const segments = items.map((item) => {
    const start = cursor;
    const end = cursor + (item.count / total) * 360;
    cursor = end;
    return `${EXEC_TONE_COLOR[item.tone] || "#8ea8ff"} ${start.toFixed(2)}deg ${end.toFixed(2)}deg`;
  }).join(",");
  const top = items[0];
  container.innerHTML = `
    <div class="od-donut-scene">
      <div class="od-donut" style="background:conic-gradient(${segments})" aria-label="失败总数 ${escapeHtml(String(total))}"></div>
      ${top ? `<button type="button" class="od-donut-callout right" data-perf-filter="${escapeHtml(top.code)}">${escapeHtml(top.label)}: ${escapeHtml(String(top.count))}</button>` : ""}
    </div>
    <div class="od-donut-legend">
      ${items.map((item) => `
        <button type="button" class="od-legend-item${state.railFailureFilter === item.code ? " is-active" : ""}" data-perf-filter="${escapeHtml(item.code)}" title="点击筛选下方任务队列">
          <span class="od-legend-dot" style="background:${EXEC_TONE_COLOR[item.tone] || "#8ea8ff"}"></span>
          <span>${escapeHtml(item.label)}</span>
          <strong>${escapeHtml(String(item.count))}</strong>
        </button>
      `).join("")}
    </div>
  `;
}

function railTaskStatusMeta(item) {
  const status = String((item && item.status) || "").trim().toLowerCase();
  if (status === "job") {
    const job = item.job || {};
    const attempts = Number(job.attempts || 0);
    return { text: attempts > 1 ? `执行中 · 第${attempts}次` : "执行中", tone: "cyan" };
  }
  if (status === "pending") return { text: "排队中", tone: "amber" };
  if (status === "done") return { text: "已成功", tone: "green" };
  if (status === "failed") return { text: "已失败", tone: "red" };
  return { text: status || "未知", tone: "gray" };
}

function renderTaskQueueRail() {
  const list = $("od-task-queue-list");
  const countEl = $("od-task-queue-count");
  if (!list) return;
  const items = Array.isArray(state.followQueue) ? state.followQueue : [];
  const active = items.filter((it) => {
    const status = String((it && it.status) || "").toLowerCase();
    return status === "job" || status === "pending";
  });
  if (countEl) countEl.textContent = `${active.length} 单`;

  let rows = active;
  if (state.railFailureFilter) {
    rows = rows.filter((it) => {
      const order = (it && it.order) || {};
      const oid = String(order.orderId || order.order_id || "").trim();
      const row = oid ? latestFollowRowForOrder(oid) : null;
      const code = (row && !row.recovered_from_queue_state) ? String(row.error_class || "").trim().toUpperCase() : "";
      return code === state.railFailureFilter;
    });
  }

  rows = rows.slice().sort((a, b) => {
    const ta = Number((a.job && (a.job.last_attempted_at || a.job.created_at)) || (a.order && a.order.ts) || 0);
    const tb = Number((b.job && (b.job.last_attempted_at || b.job.created_at)) || (b.order && b.order.ts) || 0);
    return tb - ta;
  }).slice(0, 8);

  if (!rows.length) {
    list.innerHTML = `<div class="pool-alert-empty">${state.railFailureFilter ? "该分类下暂无进行中任务" : "暂无进行中的执行任务"}</div>`;
    return;
  }

  list.innerHTML = rows.map((item) => {
    const order = item.order || {};
    const job = item.job || {};
    const oid = String(order.orderId || order.order_id || "").trim();
    const shortId = oid ? oid.slice(-6) : "——";
    const target = String(job.member_id || order.target || "").trim() || "—";
    const stake = order.stake != null ? formatOrderDashboardMoney(order.stake) : "—";
    const market = String(order.market || order.betType || order.sides || "").trim();
    const meta = railTaskStatusMeta(item);
    return `
      <div class="od-task-row" title="${escapeHtml(oid)}">
        <span class="od-task-id">#${escapeHtml(shortId)}</span>
        <span class="od-task-mid">
          <span class="od-task-target">${escapeHtml(target)}</span>
          <span class="od-task-market">${escapeHtml(market || "—")}</span>
        </span>
        <span class="od-task-stake">${escapeHtml(stake)}</span>
        <span class="od-task-status" data-tone="${meta.tone}">${escapeHtml(meta.text)}</span>
      </div>
    `;
  }).join("");
}

function renderExecutionRail() {
  renderExecutionPerformance();
  renderFailureDonut();
  renderTaskQueueRail();
}

function bindExecutionRailControls() {
  const rail = $("od-exec-row") || $("od-insight-rail");
  if (!rail || rail.dataset.railBound === "true") return;
  rail.dataset.railBound = "true";
  rail.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-perf-filter]");
    if (!btn) return;
    const code = btn.getAttribute("data-perf-filter") || "";
    state.railFailureFilter = state.railFailureFilter === code ? "" : code;
    renderExecutionRail();
  });
}

function renderOrderDashboard(model = buildOrderDashboardModel()) {
  const panel = $("order-dashboard");
  if (!panel) return;
  const filters = state.orderDashboard.filters;
  if ($("od-keyword") && document.activeElement !== $("od-keyword")) $("od-keyword").value = filters.keyword || "";
  setSelectOptions($("od-league"), model.filters.leagues, "全部联赛", filters.league);
  setSelectOptions($("od-play"), model.filters.playCategories, "全部玩法", filters.playCategory);
  if ($("od-sort")) $("od-sort").value = filters.sortBy || "time-desc";

  ["timeRange", "orderType", "status", "matchProgress"].forEach((key) => setOrderDashboardPresetActive(key, filters[key]));
  if ($("od-stat-total")) $("od-stat-total").textContent = String(model.stats.totalOrders || 0);
  if ($("od-stat-live")) $("od-stat-live").textContent = String(model.stats.liveOrders || 0);
  if ($("od-stat-early")) $("od-stat-early").textContent = String(model.stats.earlyOrders || 0);
  if ($("od-stat-stake")) $("od-stat-stake").textContent = formatOrderDashboardMoney(model.stats.totalStake || 0);
  if ($("od-filter-count")) $("od-filter-count").textContent = `${model.stats.totalOrders || 0} 单`;

  const charts = $("od-insights");
  if (charts) charts.hidden = !state.orderDashboard.chartsVisible;
  const toggleText = $("od-toggle-text");
  if (toggleText) toggleText.textContent = state.orderDashboard.chartsVisible ? "收起分析" : "展开分析";
  const toggleBtn = $("od-toggle-charts");
  if (toggleBtn) {
    toggleBtn.classList.toggle("is-collapsed", !state.orderDashboard.chartsVisible);
    toggleBtn.setAttribute("aria-expanded", state.orderDashboard.chartsVisible ? "true" : "false");
    toggleBtn.title = state.orderDashboard.chartsVisible ? "收起分析" : "展开分析";
  }

  renderHorizontalBars($("od-play-ranking"), model.charts.playDistribution, { valueKey: "stake", filterKey: "playCategory", limit: 5 });
  renderHorizontalBars($("od-league-ranking"), model.charts.leagueDistribution, { valueKey: "count", filterKey: "league", limit: 6 });
  renderPlayDonut($("od-play-donut"), model.charts.playDistribution);
  renderAmountDistribution($("od-amount-distribution"), model.charts.amountDistribution);
}

function renderOrdersView() {
  const model = buildOrderDashboardModel();
  renderOrderReportStats();
  renderOrderDashboard(model);
  renderTable(model.orders);
  renderMatchSearchToolbar();
  renderKpis(model.orders);
  renderExecutionRail();
}

function updateOrderDashboardFilter(key, value) {
  if (!state.orderDashboard || !state.orderDashboard.filters) return;
  state.orderDashboard.filters[key] = value || "";
  renderOrdersView();
}

function bindOrderDashboardControls() {
  const panel = $("order-dashboard");
  if (!panel || panel.dataset.dashboardBound === "true") return;
  panel.dataset.dashboardBound = "true";
  panel.addEventListener("click", (ev) => {
    const toggle = ev.target.closest("#od-toggle-charts");
    if (toggle) {
      state.orderDashboard.chartsVisible = !state.orderDashboard.chartsVisible;
      renderOrderDashboard();
      return;
    }
    const filterBtn = ev.target.closest("[data-od-filter]");
    if (filterBtn) {
      updateOrderDashboardFilter(filterBtn.getAttribute("data-od-filter"), filterBtn.getAttribute("data-value") || "");
      return;
    }
    const chartFilter = ev.target.closest("[data-od-chart-filter]");
    if (chartFilter) {
      updateOrderDashboardFilter(chartFilter.getAttribute("data-od-chart-filter"), chartFilter.getAttribute("data-value") || "");
    }
  });

  $("od-keyword")?.addEventListener("input", (ev) => updateOrderDashboardFilter("keyword", ev.target.value.trim()));
  $("od-league")?.addEventListener("change", (ev) => updateOrderDashboardFilter("league", ev.target.value));
  $("od-play")?.addEventListener("change", (ev) => updateOrderDashboardFilter("playCategory", ev.target.value));
  $("od-sort")?.addEventListener("change", (ev) => updateOrderDashboardFilter("sortBy", ev.target.value || "time-desc"));
  $("od-reset")?.addEventListener("click", () => {
    const defaults = orderDashboardTools.DEFAULT_ORDER_DASHBOARD_FILTERS || {};
    state.orderDashboard.filters = Object.assign({
      keyword: "",
      timeRange: "today",
      orderType: "",
      status: "",
      matchProgress: "",
      playCategory: "",
      league: "",
      amountBucket: "",
      sortBy: "time-desc",
    }, defaults);
    renderOrdersView();
  });
}

// ── Sidebar accounts ───────────────────────────────────────────
function renderAccountSelect() {
  const sel = $("account");
  if (!sel) return;
  const current = state.selectedTarget;
  const signalOptions = getSignalSourceSidebarItems()
    .map((item) => `<option value="${escapeHtml(item.filterTarget || REMOTE_SIGNAL_FILTER_TARGET)}">${escapeHtml(item.name)}</option>`)
    .join("");
  sel.innerHTML = `<option value="">全部目标</option>` +
    signalOptions +
    state.accounts.map((a) => `<option value="${escapeHtml(String(a.name || ""))}">${escapeHtml(accountLabel(a))}</option>`).join("");
  sel.value = current;
}

function renderAccountsSidebar() {
  const wrap = $("accounts-list");
  if (!wrap) return;
  wrap.innerHTML = "";

  const signalItems = getSignalSourceSidebarItems();

  if (state.accounts.length === 0 && signalItems.length === 0) {
    wrap.innerHTML = `<div style="padding:10px 8px; font-size:11px; color:var(--text-3); text-align:center">暂无监控节点</div>`;
    return;
  }

  // "All" item
  const allItem = document.createElement("div");
  allItem.className = "account-item" + (!state.selectedTarget ? " active" : "");
  allItem.innerHTML = `
    <div class="account-info">
      <div class="account-status-dot" style="background:var(--text-3); opacity:0.5"></div>
      <div class="account-name">全部</div>
    </div>
  `;
  allItem.addEventListener("click", () => {
    Array.from(wrap.children).forEach(c => c.classList.remove("active"));
    allItem.classList.add("active");
    state.selectedTarget = "";
    const sel = $("account"); if (sel) sel.value = "";
    refreshView();
  });
  wrap.appendChild(allItem);

  for (const item of signalItems) {
    const filterTarget = item.filterTarget || REMOTE_SIGNAL_FILTER_TARGET;
    const div = document.createElement("div");
    const isActive = state.selectedTarget === filterTarget;
    const isOnline = String(item.statusText || "") === "已接入";
    const statusTitle = [item.statusText, item.metaText].filter(Boolean).join(" · ");
    div.className = "account-item signal-source-item" + (isActive ? " active" : "");
    div.innerHTML = `
      <div class="account-info" style="flex:1; min-width:0">
        <div class="account-status-dot ${isOnline ? "st-online" : "st-offline"}" title="${escapeHtml(statusTitle || item.name)}"></div>
        <div class="account-copy">
          <div class="account-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</div>
          ${item.metaText ? `<div class="account-status-meta" title="${escapeHtml(item.metaText)}">${escapeHtml(item.metaText)}</div>` : ""}
        </div>
      </div>
      ${item.canDelete ? `
      <div class="acct-actions" style="opacity:0; transition:0.15s">
        <button class="acct-btn danger" data-action="delete-signal" title="删除远端交易节点">×</button>
      </div>` : ""}
    `;
    div.addEventListener("mouseenter", () => {
      const actions = div.querySelector(".acct-actions");
      if (actions) actions.style.opacity = 1;
    });
    div.addEventListener("mouseleave", () => {
      const actions = div.querySelector(".acct-actions");
      if (actions && !div.classList.contains("active")) actions.style.opacity = 0;
    });
    div.addEventListener("click", () => {
      Array.from(wrap.children).forEach(c => c.classList.remove("active"));
      div.classList.add("active");
      const actions = div.querySelector(".acct-actions");
      if (actions) actions.style.opacity = 1;
      state.selectedTarget = filterTarget;
      const sel = $("account"); if (sel) sel.value = filterTarget;
      refreshView();
    });
    div.querySelector("[data-action='delete-signal']")?.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await deleteSignalSource(item);
    });
    wrap.appendChild(div);
  }

  for (const a of state.accounts) {
    if (!a.name) continue;
    const div = document.createElement("div");
    const isActive = (a.name || "") === (state.selectedTarget || "");
    div.className = "account-item" + (isActive ? " active" : "");

    const st = state.statuses[String(a.name || "")] || null;
    const stClass = st && st.status ? String(st.status) : "offline";
    const needsManualLogin = !!(st && st.needs_manual_login);
    const statusMessage = String((st && st.status_message) || "").trim();
    const statusTitle = statusMessage || (stClass === "online" ? "在线" : stClass === "relogin" ? "正在重登" : "离线");
    const showStatusMeta = stClass === "relogin" || (stClass === "offline" && statusMessage);
    let dotClass = "st-offline";
    if (stClass === "online")  dotClass = "st-online";
    if (stClass === "relogin") dotClass = "st-relogin";
    div.className = [
      "account-item",
      isActive ? "active" : "",
      stClass === "relogin" ? "is-relogin" : "",
      needsManualLogin ? "needs-login" : "",
    ].filter(Boolean).join(" ");

    div.innerHTML = `
      <div class="account-info" style="flex:1; min-width:0">
        <div class="account-status-dot ${dotClass}" title="${escapeHtml(statusTitle)}"></div>
        <div class="account-copy">
          <div class="account-name" title="${escapeHtml(accountLabel(a))}">${escapeHtml(accountLabel(a))}</div>
          ${showStatusMeta ? `<div class="account-status-meta" title="${escapeHtml(statusTitle)}">${escapeHtml(statusTitle)}</div>` : ""}
        </div>
      </div>
      <div class="acct-actions" style="opacity:0; transition:0.15s">
        <button class="acct-btn" data-action="login" title="${escapeHtml(stClass === "relogin" ? "登录窗口已发起，请完成验证码/登录" : "重新登录")}">↻</button>
        <button class="acct-btn danger" data-action="delete" title="删除">×</button>
      </div>
    `;

    div.addEventListener("mouseenter", () => div.querySelector(".acct-actions").style.opacity = 1);
    div.addEventListener("mouseleave", () => { if (!div.classList.contains("active")) div.querySelector(".acct-actions").style.opacity = 0; });
    div.addEventListener("click", () => {
      Array.from(wrap.children).forEach(c => c.classList.remove("active"));
      div.classList.add("active");
      div.querySelector(".acct-actions").style.opacity = 1;
      state.selectedTarget = String(a.name || "");
      const sel = $("account"); if (sel) sel.value = state.selectedTarget;
      refreshView();
    });
    div.querySelector("[data-action='login']").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await reloginAccount(String(a.name || ""), accountLabel(a));
    });
    div.querySelector("[data-action='delete']").addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (confirm(`确定要删除监控目标 ${accountLabel(a)} 吗？`)) {
        await deleteAccount(String(a.name || ""), accountLabel(a));
      }
    });
    wrap.appendChild(div);
  }
}

// ── Bet type helpers ───────────────────────────────────────────
function getBetTypeColor(betType) {
  const bt = String(betType || "").toLowerCase();
  if (bt.includes("card")     || bt.includes("罚牌") || bt.includes("牌数")) return "#EF4444";
  if (bt.includes("corner")   || bt.includes("角球")) return "#EC4899";
  if (bt.includes("handicap") || bt.includes("让球") || bt.includes("让分") || bt.includes("亚盘")) return "#3B82F6";
  if (bt.includes("over")     || bt.includes("大球") || bt.match(/\b大\b/))  return "#10B981";
  if (bt.includes("under")    || bt.includes("小球") || bt.match(/\b小\b/))  return "#F97316";
  if (bt.includes("moneyline")|| bt.includes("独赢") || bt.includes("胜平负")|| bt.includes("1x2")) return "#A855F7";
  return "#3B82F6";
}

function getBetTypeLabel(betType) {
  return parseBetDisplay({ betType }).betLabel;
}

function parseBetDisplay(source) {
  const current = source || {};
  const betTypeRaw = String(current.betType || "").trim();
  const lineText = String(current.display_line || current.line || "").trim();
  const orderKind = String(current.orderKind || "").trim() || (/滚球/i.test(betTypeRaw) ? "滚球" : "早盘");
  const normalized = betTypeRaw
    .replace(/[()（）]/g, " ")
    .replace(/-/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const low = normalized.toLowerCase();
  const isTotal = /大\s*\/\s*小|over\s*\/\s*under|over|under|大小/.test(low);
  const period = /下半场|2nd|second half|\b2h\b/i.test(betTypeRaw)
    ? "下半场"
    : (/上半场|半场|1st|first half|\b1h\b/i.test(betTypeRaw) ? "上半场" : "全场");

  let marketName = "盘口";
  let betLabel = betTypeRaw.slice(0, 6) || "OTHER";
  if (/card|罚牌|牌数/i.test(betTypeRaw)) {
    marketName = "罚牌数";
    betLabel = isTotal ? "罚牌大小" : "罚牌";
  } else if (/corner|角球/i.test(betTypeRaw)) {
    marketName = "角球数";
    betLabel = isTotal ? "角球大小" : "角球";
  } else if (/(handicap|让球|让分|亚盘)/i.test(betTypeRaw)) {
    marketName = "让球";
    betLabel = "让球";
  } else if (/(moneyline|独赢|胜平负|1x2)/i.test(betTypeRaw)) {
    marketName = "独赢";
    betLabel = "独赢";
  } else if (isTotal || /大球|小球|进球/i.test(betTypeRaw)) {
    marketName = "进球数";
    betLabel = "进球大小";
  } else if (/over|大/i.test(betTypeRaw)) {
    marketName = "进球数";
    betLabel = "大球";
  } else if (/under|小/i.test(betTypeRaw)) {
    marketName = "进球数";
    betLabel = "小球";
  }

  const selection = String(current.selection || "").trim();
  const selectionBase = [
    selection,
    lineText && !selection.includes(lineText) ? lineText : "",
  ].filter(Boolean).join(" ") || lineText;
  const selectionText = selectionBase
    ? `${selectionBase}${current.odds ? ` @ ${current.odds}` : ""}`
    : (betLabel || "--");
  const explicitMarket = String(current.marketLabel || current.market_label || "").trim();
  const marketText = [orderKind, period, explicitMarket || marketName].filter(Boolean).join(" · ");

  return {
    betLabel,
    marketName: explicitMarket || marketName,
    marketText,
    orderKind,
    period,
    selectionText,
  };
}

function getSportIcon(sport) {
  const s = String(sport || "").toLowerCase();
  if (s.includes("football") || s.includes("足球") || s.includes("soccer")) return "fa-futbol";
  if (s.includes("basketball") || s.includes("篮球")) return "fa-basketball";
  if (s.includes("tennis") || s.includes("网球")) return "fa-circle-dot";
  if (s.includes("baseball") || s.includes("棒球")) return "fa-baseball-bat-ball";
  if (s.includes("volleyball") || s.includes("排球")) return "fa-volleyball";
  return "fa-trophy";
}

function stripInlineHtml(value) {
  return String(value || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseOrderMatchInfo(order) {
  const o = order || {};
  const raw = o.raw || {};
  const rawTeams = stripInlineHtml(raw.SHOWTEXT_TEAM || raw.TEAM_TEXT || "");
  const source = stripInlineHtml(o.sides || o.match || rawTeams || raw.TNAME || "");
  const scoreMatch = source.match(/\((\d+)\s*[-:]\s*(\d+)\)/);
  const homeScore = scoreMatch ? scoreMatch[1] : String(raw.SCORE_H || raw.score_h || "").trim();
  const awayScore = scoreMatch ? scoreMatch[2] : String(raw.SCORE_C || raw.score_c || "").trim();
  const withoutScore = source
    .replace(/\([^)]*\)/g, " ")
    .replace(/\[[^\]]*\]\s*vs\s*\[[^\]]*\]/i, " ")
    .replace(/\s+/g, " ")
    .trim();

  let homeTeam = "";
  let awayTeam = "";
  const parts = withoutScore.split(/\s+(?:v|vs|VS|V)\s+/).map((part) => part.trim()).filter(Boolean);
  if (parts.length >= 2) {
    homeTeam = parts[0];
    awayTeam = parts.slice(1).join(" v ");
  }
  if (!homeTeam || !awayTeam) {
    homeTeam = String(raw.TEAM_H || raw.team_h || "").trim();
    awayTeam = String(raw.TEAM_C || raw.team_c || "").trim();
  }

  return {
    homeTeam: homeTeam || "主队",
    awayTeam: awayTeam || "客队",
    score: homeScore !== "" || awayScore !== "" ? `${homeScore || "0"} : ${awayScore || "0"}` : "0 : 0",
  };
}

function buildOrderCardModel(order) {
  const o = order || {};
  const raw = o.raw || {};
  const liveMatch = o._live_match && typeof o._live_match === "object" ? o._live_match : null;
  const timeStr = liveMatch && liveMatch.time
    ? String(liveMatch.time)
    : (fmtOrderTime(o).split(" ")[1] || String(o.time || raw.TIME || "-"));
  const acct = backendAccountOfOrder(o);
  const stake = Math.floor(Number(o.stake) || 0);
  const stakeStr = stake >= 1000 ? `¥${(stake / 1000).toFixed(1)}k` : `¥${stake}`;
  const orderId = o.orderId || o.order_id || raw.orderId || raw.order_id || raw.ORDER_ID || raw.TID || "N/A";
  const eventId = (liveMatch && liveMatch.gid) || o.eventId || o.event_id || o.gid || raw._EVENT || raw.EVENT || raw.eventId || raw.event_id || "-";
  const lineText = String(o.display_line || o.line || "").trim();
  const rawKind = stripInlineHtml(o.orderType || o.order_type || raw.orderType || raw.ORDER_TYPE || "");
  const isLiveOrder = (liveMatch && liveMatch.mode === "live") || /滚球|live/i.test(rawKind) || /滚球/i.test(String(o.betType || "")) || String(raw.MARKET || "").toLowerCase() === "rb";
  const liveLookup = o._live_match_lookup && typeof o._live_match_lookup === "object" ? o._live_match_lookup : null;
  const liveLookupMiss = Boolean(isLiveOrder && !liveMatch && liveLookup && liveLookup.mode === "live" && liveLookup.matched === false);
  const orderKind = liveMatch && liveMatch.modeLabel
    ? liveMatch.modeLabel
    : (isLiveOrder ? "滚球" : (rawKind && !/^n(o)?$/i.test(rawKind) ? rawKind : "早盘"));
  const betDisplay = parseBetDisplay(Object.assign({}, o, { line: lineText, orderKind }));
  const betLabel = betDisplay.betLabel;
  const marketText = betDisplay.marketText || "盘口信息";
  const selectionText = betDisplay.selectionText || "--";
  const queueLabel = o._follow_queue && o._follow_queue.label ? String(o._follow_queue.label) : "";
  const statusText = queueLabel || stripInlineHtml(o.status_label || o.status || raw.status_label || raw.status || raw.BALL_ACT || raw.ORDER_STATUS) || "已接收";
  const sourceName = o.backendName || o.backend_name || o.target || o.backend || o.source || "-";
  const parsedMatch = parseOrderMatchInfo(o);
  const match = liveMatch ? {
    homeTeam: parsedMatch.homeTeam !== "主队" ? parsedMatch.homeTeam : (liveMatch.homeTeam || parsedMatch.homeTeam),
    awayTeam: parsedMatch.awayTeam !== "客队" ? parsedMatch.awayTeam : (liveMatch.awayTeam || parsedMatch.awayTeam),
    score: liveMatch.score || parsedMatch.score,
  } : parsedMatch;
  const rawMatchStatusLine = liveLookupMiss ? "已结束" : (liveMatch && liveMatch.statusLine ? liveMatch.statusLine : [orderKind, timeStr].filter(Boolean).join(" | "));
  const matchStatusDisplay = resolveCardMatchStatusDisplay({
    mode: liveMatch && liveMatch.mode ? liveMatch.mode : (isLiveOrder ? "live" : "today"),
    time: liveLookupMiss ? "" : timeStr,
    statusLine: rawMatchStatusLine,
    statusText: liveLookupMiss ? "已结束" : (liveMatch && liveMatch.statusText ? liveMatch.statusText : ""),
    capturedAt: liveMatch && liveMatch.capturedAt,
  });

  return {
    account: acct,
    betLabel,
    capturedAt: liveMatch && liveMatch.capturedAt,
    eventId,
    homeTeam: match.homeTeam,
    awayTeam: match.awayTeam,
    isEnded: Boolean(liveLookupMiss || matchStatusDisplay.isEnded),
    isLiveOrder,
    league: (liveMatch && liveMatch.league) || o.league || raw.LEAGUE || raw.SHOWTEXT_LEAGUE || "未知联赛",
    marketText,
    matchStatusText: matchStatusDisplay.statusText,
    mode: liveMatch && liveMatch.mode ? liveMatch.mode : (isLiveOrder ? "live" : "today"),
    orderId,
    orderKind,
    score: match.score,
    orderScore: parsedMatch.score,
    selectionText,
    sourceName,
    stakeStr,
    statusLine: matchStatusDisplay.statusLine || rawMatchStatusLine,
    statusText,
    timeStr,
    oddsText: String(o.odds || "--"),
  };
}

function renderOrderDetailCards(model, order, group) {
  const o = order || {};
  const raw = o.raw || {};
  const dateText = [o.date || raw.DATE || "", o.time || raw.TIME || model.timeStr].filter(Boolean).join(" ");
  const itemsByGroup = {
    "盘口信息": [
      ["玩法", model.betLabel],
      ["下注盘口", model.marketText],
      ["下注选择", model.selectionText],
      ["赔率", `@ ${model.oddsText}`],
      ["赛事号", model.eventId],
      ["球种", o.sport || raw.GT || raw.GTYPE || "-"],
    ],
    "投注信息": [
      ["订单状态", model.statusText],
      ["金额", model.stakeStr],
      ["账号", model.account],
      ["来源", model.sourceName],
      ["订单号", model.orderId],
      ["时间", dateText || "-"],
    ],
    "比赛事件": [
      ["赛事号", model.eventId],
      ["当前比分", model.score],
      ["比赛状态", model.statusLine],
      ["主队", model.homeTeam],
      ["客队", model.awayTeam],
      ["事件源", raw.MT_ID || raw.mtId || "待接入"],
    ],
    "注单详情": [
      ["订单号", model.orderId],
      ["赛事号", model.eventId],
      ["联赛", model.league],
      ["比分", model.score],
      ["金额", model.stakeStr],
      ["赔率", `@ ${model.oddsText}`],
    ],
  };
  const items = itemsByGroup[group] || itemsByGroup["注单详情"];
  return items.map(([label, value]) => `
    <article class="order-detail-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "-")}</strong>
    </article>
  `).join("");
}

function buildOrderBetInfoReportCardModel(model, order) {
  const o = order || {};
  const raw = o.raw || {};
  const oid = String(model && model.orderId || o.orderId || o.order_id || "").trim();
  const fallbackMatchup = `${model.homeTeam || "主队"} v ${model.awayTeam || "客队"} (${String(model.score || "0 : 0").replace(/\s*:\s*/g, " - ")})`;
  const cardOrder = Object.assign({}, o, {
    orderId: oid,
    league: model.league,
    sides: o.sides || o.match || raw.SHOWTEXT_TEAM || fallbackMatchup,
    betType: o.betType || model.betLabel,
    display_line: o.display_line || o.line || "",
    marketLabel: o.marketLabel || o.market_label || "",
    selection: o.selection || "",
    odds: o.odds || model.oddsText,
  });
  const timeText = model.timeStr || (fmtOrderTime(o).split(" ")[1] || fmtOrderTime(o));
  return buildReportCardModelCompat({
    order: cardOrder,
    followSummary: getFollowSummary(oid),
    settlement: getSettlement(oid),
    followStake: getFollowStake(oid),
    account: model.account,
    source: model.sourceName,
    timeText,
    eventId: model.eventId,
    betLabel: model.betLabel,
    betColor: getBetTypeColor(o.betType),
    orderKind: model.orderKind,
    isLiveOrder: model.isLiveOrder,
  });
}

function renderOrderBetInfoCard(model, order) {
  const card = buildOrderBetInfoReportCardModel(model, order);
  const profitClass = card.profitText && card.profitText.startsWith("+")
    ? "pos"
    : card.profitText && card.profitText.startsWith("-")
      ? "neg"
      : "";
  return `
    <section class="bet-ticket-card-shell" aria-label="投注订单卡片">
      <div class="${escapeHtml(card.cardClass)} bet-ticket-order-card">
        <div class="mc-inner">
          <header class="mc-card-head">
            <span class="mc-chip">${escapeHtml(card.headerChip)}</span>
            <div class="mc-top-right">
              <span class="mc-order-type ${card.isLiveOrder ? "is-live" : "is-early"}">${escapeHtml(card.orderKind)}</span>
              <span class="mc-card-time">${escapeHtml(card.timeText)}</span>
            </div>
          </header>

          <section class="mc-card-main">
            <h3 class="mc-league">${escapeHtml(card.league)}</h3>
            <p class="mc-matchup">${escapeHtml(card.matchup)}</p>
            <div class="mc-tags-row">
              <span class="mc-meta-pill" style="border-color:${escapeHtml(card.betColor)}40;color:${escapeHtml(card.betColor)};background:${escapeHtml(card.betColor)}14">${escapeHtml(card.betLabel)}</span>
              <span class="mc-meta-text">${escapeHtml(card.marketText)}</span>
            </div>
            <p class="mc-selection-strip">${escapeHtml(card.selectionText)}</p>
          </section>

          <div class="mc-status-row rpt-status-row">
            <div class="mc-status-item">
              <span class="mc-status-label">跟单状态</span>
              <span class="mc-status-badge">${escapeHtml(card.reportStatusText)}</span>
            </div>
            <div class="mc-status-item">
              <span class="mc-status-label">结算状态</span>
              <span class="mc-match-state settle-badge ${escapeHtml(card.settlementTone)}">${escapeHtml(card.settlementLabel)}</span>
            </div>
          </div>

          <dl class="mc-detail-grid">
            <div>
              <dt>赔率</dt>
              <dd>${escapeHtml(card.oddsText)}</dd>
            </div>
            <div>
              <dt>跟单金额</dt>
              <dd>${escapeHtml(card.stakeText)}</dd>
            </div>
            <div>
              <dt>账号</dt>
              <dd>${escapeHtml(card.account)}</dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd>${escapeHtml(card.source)}</dd>
            </div>
            <div>
              <dt>订单号</dt>
              <dd>${escapeHtml(card.orderId || "—")}</dd>
            </div>
            <div>
              <dt>赛事号</dt>
              <dd>${escapeHtml(card.eventId)}</dd>
            </div>
          </dl>

          <div class="rpt-card-foot bet-ticket-card-foot">
            <span class="rpt-settle-profit ${profitClass}">${escapeHtml(card.profitText || "未结算")}</span>
          </div>
        </div>
      </div>
    </section>
  `;
}

function buildFollowExecutionModelCompat(rows) {
  if (typeof reportCardTools.buildFollowExecutionModel === "function") {
    return reportCardTools.buildFollowExecutionModel(rows, { engineStopped: isFollowEngineStopped() });
  }
  const source = Array.isArray(rows) ? rows : [];
  if (!source.length) {
    return {
      rows: [],
      summaryText: isFollowEngineStopped() ? "跟单引擎未运行" : "待处理",
      emptyText: isFollowEngineStopped()
        ? "该订单尚未产生跟单记录，当前跟单引擎未运行。"
        : "该订单尚未产生跟单执行记录，等待引擎处理或结果回写。",
    };
  }
  const mapped = source.map((row) => ({
    ok: !!row.ok,
    status: row.ok ? "success" : "failed",
    statusLabel: row.ok ? "成功" : "失败",
    member: String(row.member || row.member_id || "未知会员"),
    stakeText: row.stake ? formatOrderDashboardMoney(row.stake) : "—",
    ticketText: String(row.ticket_id || (row.bet_result_head && row.bet_result_head.ticket_id) || "—"),
    reason: row.ok ? "已成功出票" : String(row.message || row.error_type || row.exception || "失败原因未返回"),
    attemptText: row.attempt ? `第 ${row.attempt} 次` : "",
    splitText: row.split_count > 1 ? `${row.split_index || 1}/${row.split_count}` : "",
  }));
  const ok = mapped.filter((row) => row.ok).length;
  return { rows: mapped, summaryText: `成功 ${ok} · 失败 ${mapped.length - ok}`, emptyText: "" };
}

function renderFollowExecutionPanel(model) {
  const oid = String(model && model.orderId || "").trim();
  const reportMap = typeof reportState !== "undefined" ? (reportState.followByOid || {}) : {};
  const rows = state.orderFollowByOid[oid] || reportMap[oid] || [];
  const execution = buildFollowExecutionModelCompat(rows);
  const rowsHtml = (execution.rows || []).map((row) => {
    const meta = [row.member, row.stakeText, row.attemptText, row.splitText ? `分单 ${row.splitText}` : ""]
      .filter(Boolean)
      .join(" · ");
    return `
      <article class="bet-ticket-follow-row ${row.ok ? "success" : "failed"}">
        <div class="bet-ticket-follow-main">
          <span class="bet-ticket-follow-status">${escapeHtml(row.statusLabel)}</span>
          <strong>${escapeHtml(row.ok ? `票号 ${row.ticketText || "—"}` : row.reason || "失败原因未返回")}</strong>
          <em>${escapeHtml(meta || "-")}</em>
        </div>
        ${row.ok ? `<div class="bet-ticket-follow-ticket">${escapeHtml(row.ticketText || "—")}</div>` : ""}
      </article>
    `;
  }).join("");

  return `
    <section class="bet-ticket-follow-panel" aria-label="跟单执行结果">
      <div class="bet-ticket-follow-head">
        <span>执行结果</span>
        <strong>${escapeHtml(execution.summaryText || "待处理")}</strong>
      </div>
      ${rowsHtml || `<div class="bet-ticket-follow-empty">${escapeHtml(execution.emptyText || "该订单暂无跟单执行记录。")}</div>`}
    </section>
  `;
}

function renderOrderBetInfoView(model, order) {
  return `
    ${renderOrderBetInfoCard(model, order)}
    ${renderFollowExecutionPanel(model)}
  `;
}

// ── Match search / retained matches ───────────────────────────
function getMatchTool(name, fallback) {
  return typeof matchSearchTools[name] === "function" ? matchSearchTools[name] : fallback;
}

const normalizeHgSearchGame = getMatchTool("normalizeHgSearchGame", (game, options = {}) => {
  const g = game || {};
  const mode = String(options.mode || g.mode || "live").trim().toLowerCase() || "live";
  const scoreText = String(g.score || "").replace(/\s*[:：-]\s*/g, " : ").trim() || "0 : 0";
  const modeLabel = mode === "today" ? "今日" : mode === "early" ? "早盘" : "滚球";
  const time = String(g.time || "").trim();
  return {
    key: `${mode}:${g.gid || g.mtId || `${g.teamH || ""}-${g.teamC || ""}`}`,
    mode,
    modeLabel,
    memberId: String(options.memberId || g.memberId || "").trim(),
    memberLabel: String(options.memberLabel || g.memberLabel || "").trim(),
    gid: String(g.gid || "").trim(),
    eventId: String(g.ecid || g.mtId || g.gid || "-").trim(),
    ecid: String(g.ecid || "").trim(),
    lid: String(g.lid || "").trim(),
    mtId: String(g.mtId || "").trim(),
    league: String(g.league || "未命名联赛").trim(),
    homeTeam: String(g.teamH || g.homeTeam || "主队").trim(),
    awayTeam: String(g.teamC || g.awayTeam || "客队").trim(),
    gnumH: String(g.gnumH || g.GNUM_H || "").trim(),
    gnumC: String(g.gnumC || g.GNUM_C || "").trim(),
    score: scoreText,
    time,
    statusLine: [modeLabel, time].filter(Boolean).join(" | "),
    statusText: String(g.statusText || "可保留").trim(),
    moreCount: g.moreCount == null ? null : Number(g.moreCount),
    retainedAt: Number(g.retainedAt || 0) || 0,
    raw: g.raw || g,
  };
});
const filterHgSearchGames = getMatchTool("filterHgSearchGames", (games, query) => {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return Array.isArray(games) ? games.slice() : [];
  return (Array.isArray(games) ? games : []).filter((g) => [g.gid, g.gnumH, g.gnumC, g.league, g.teamH, g.teamC, g.score, g.time].filter(Boolean).join(" ").toLowerCase().includes(q));
});
const buildHgOrderSearchContext = getMatchTool("buildHgOrderSearchContext", (_orders, query) => ({
  query: String(query || "").trim(), teamIds: [], matchTerms: [], matchedOrderId: "",
}));
const upsertRetainedHgMatch = getMatchTool("upsertRetainedHgMatch", (retained, match) => {
  const rows = Array.isArray(retained) ? retained.slice() : [];
  const model = normalizeHgSearchGame(match, match || {});
  const idx = rows.findIndex((row) => row && row.key === model.key);
  if (idx >= 0) rows[idx] = Object.assign({}, rows[idx], model, { retainedAt: rows[idx].retainedAt || model.retainedAt || Date.now() });
  else rows.unshift(Object.assign({}, model, { retainedAt: model.retainedAt || Date.now() }));
  return rows;
});
const removeRetainedHgMatch = getMatchTool("removeRetainedHgMatch", (retained, key) => (Array.isArray(retained) ? retained : []).filter((row) => row && row.key !== key));
const mergeRetainedHgMatches = getMatchTool("mergeRetainedHgMatches", (retained) => (Array.isArray(retained) ? retained : []));
const isHgMatchRetained = getMatchTool("isHgMatchRetained", (retained, matchOrKey) => {
  const key = typeof matchOrKey === "string" ? matchOrKey : normalizeHgSearchGame(matchOrKey || {}).key;
  return (Array.isArray(retained) ? retained : []).some((row) => row && row.key === key);
});
const buildHgEventDetailItems = getMatchTool("buildHgEventDetailItems", (match) => {
  const model = normalizeHgSearchGame(match || {}, match || {});
  return [
    { label: "事件ID", value: model.ecid || model.eventId || model.gid || "-" },
    { label: "数据ID", value: model.mtId || "-" },
    { label: "当前比分", value: model.score || "-" },
    { label: "比赛状态", value: model.statusLine || "-" },
    { label: "主队", value: model.homeTeam || "-" },
    { label: "客队", value: model.awayTeam || "-" },
  ];
});
const mergeHgEventTimeline = getMatchTool("mergeHgEventTimeline", (previous, incoming) => Object.assign({}, previous || {}, incoming || {}));
const formatHgEventErrorMessage = getMatchTool("formatHgEventErrorMessage", (message) => {
  const text = String(message == null ? "" : message).replace(/\s+/g, " ").trim();
  if (!text) return "比赛事件读取失败，请稍后重试。";
  if (text.includes("missing_betradar_feed_token")) return "当前缺少比赛事件源授权，请先采集或配置 BETRADAR_FEED_TOKEN。";
  if (text.includes("missing_match_id")) return "当前比赛没有可用的事件流编号，暂时无法读取比赛事件。";
  if (text.includes("missing_member_id")) return "当前没有可用的在线会员会话，无法解析事件流编号。";
  if (text.includes("member_not_found")) return "当前会员会话已失效，请刷新会员状态后重试。";
  if (text.includes("member_missing_uid_cookie_ver")) return "当前会员会话缺少 UID/Cookie/版本信息，无法解析事件流编号。";
  if (text.includes("resolve_match_id_failed: game_not_found")) return "当前赛事不在该会员实时列表中，暂时无法解析事件流编号。";
  if (text.includes("resolve_match_id_failed")) return "事件流编号解析失败，请刷新比赛列表后重试。";
  if (text.includes("Unauthorized") || text.includes("403")) return "比赛事件源授权已失效，请重新采集 BETRADAR_FEED_TOKEN。";
  return text;
});
const buildHgOddsSections = getMatchTool("buildHgOddsSections", () => []);

function loadRetainedHgMatches() {
  try {
    const raw = localStorage.getItem(MATCH_SEARCH_RETAINED_KEY);
    const rows = raw ? JSON.parse(raw) : [];
    return (Array.isArray(rows) ? rows : []).map((row) => normalizeHgSearchGame(row, row));
  } catch {
    return [];
  }
}

function saveRetainedHgMatches() {
  try {
    localStorage.setItem(MATCH_SEARCH_RETAINED_KEY, JSON.stringify(state.matchSearch.retained || []));
  } catch {
    // localStorage may be unavailable in private/embedded contexts.
  }
}

function getDisplayedSearchMatches() {
  const ms = state.matchSearch || {};
  const query = String(ms.query || "").trim();
  const rows = query ? ms.results : ms.retained;
  return Array.isArray(rows) ? rows : [];
}

function renderMatchSearchToolbar() {
  const retainedCount = $("hg-match-retained-count");
  if (retainedCount) retainedCount.textContent = `${(state.matchSearch.retained || []).length} 场已保留`;

  const status = $("hg-match-search-status");
  const strip = $("hg-match-search-strip");
  const clear = $("hg-match-clear-search");
  if (!status || !strip) return;

  const query = String(state.matchSearch.query || "").trim();
  const shown = getDisplayedSearchMatches().length;
  const hasRetained = (state.matchSearch.retained || []).length > 0;
  strip.hidden = !(query || state.matchSearch.loading || state.matchSearch.error || hasRetained);

  if (state.matchSearch.loading) {
    status.textContent = "搜索中…";
  } else if (state.matchSearch.error) {
    status.textContent = state.matchSearch.error;
  } else if (query) {
    status.textContent = `比赛搜索：${shown} 场`;
  } else {
    status.textContent = hasRetained ? "保留列表" : "";
  }
  if (clear) {
    clear.hidden = !query;
  }
}

function renderMatchSearchStatusCard(grid) {
  if (!grid) return;
  const query = String(state.matchSearch.query || "").trim();
  if (!query && !state.matchSearch.loading && !state.matchSearch.error) return;
  const card = document.createElement("div");
  card.className = "match-search-state-card";
  const icon = state.matchSearch.error ? "fa-triangle-exclamation" : state.matchSearch.loading ? "fa-spinner fa-spin" : "fa-magnifying-glass";
  const title = state.matchSearch.error ? "比赛搜索失败" : state.matchSearch.loading ? "正在搜索比赛" : "没有命中比赛";
  const sub = state.matchSearch.error || (query ? `关键词：${query}` : "请输入比赛关键词");
  card.innerHTML = `
    <i class="fa-solid ${icon}"></i>
    <div>
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(sub)}</span>
    </div>
  `;
  grid.appendChild(card);
}

async function ensureMatchSearchMember() {
  return ensureReadLaneMember("games");
}

function getReadLanes() {
  if (!state.matchSearch.readLanes) {
    state.matchSearch.readLanes = { games: { memberId: "", memberLabel: "" }, odds: { memberId: "", memberLabel: "" }, events: { memberId: "", memberLabel: "" } };
  }
  return state.matchSearch.readLanes;
}

function collectLaneExcludes(lane) {
  const lanes = getReadLanes();
  if (matchSearchTools && typeof matchSearchTools.collectReadLaneExcludes === "function") {
    return matchSearchTools.collectReadLaneExcludes(lanes, lane);
  }
  const cur = String(lane || "games");
  return Object.keys(lanes)
    .filter((k) => k !== cur)
    .map((k) => String((lanes[k] || {}).memberId || "").trim())
    .filter(Boolean);
}

function setLaneMember(lane, member) {
  const lanes = getReadLanes();
  if (matchSearchTools && typeof matchSearchTools.setReadLaneMember === "function") {
    matchSearchTools.setReadLaneMember(lanes, lane, member);
  } else {
    const k = String(lane || "games");
    if (!lanes[k]) lanes[k] = { memberId: "", memberLabel: "" };
    lanes[k].memberId = String(member && (member.id || member.memberId) || "").trim();
    lanes[k].memberLabel = String(member && (member.label || member.memberLabel || member.username || member.id) || "").trim();
  }
}

function resetReadLaneMembers(lane) {
  const lanes = getReadLanes();
  if (matchSearchTools && typeof matchSearchTools.resetReadLaneState === "function") {
    matchSearchTools.resetReadLaneState(lanes, lane);
  } else if (!lane) {
    lanes.games = { memberId: "", memberLabel: "" };
    lanes.odds = { memberId: "", memberLabel: "" };
    lanes.events = { memberId: "", memberLabel: "" };
  } else {
    const k = String(lane || "games").trim().toLowerCase() || "games";
    lanes[k] = { memberId: "", memberLabel: "" };
  }
  if (!lane || String(lane) === "games") {
    state.matchSearch.memberId = "";
    state.matchSearch.memberLabel = "";
  }
}

async function ensureReadLaneMember(lane, options = {}) {
  const k = String(lane || "games").trim().toLowerCase() || "games";
  const lanes = getReadLanes();
  if (!options.force && lanes[k] && lanes[k].memberId) return lanes[k].memberId;
  const params = new URLSearchParams({ lane: k });
  if (lanes[k] && lanes[k].memberId) params.set("member_id", lanes[k].memberId);
  const excludes = collectLaneExcludes(k);
  if (excludes.length) params.set("exclude_member_ids", excludes.join(","));
  const data = await fetchJson(`/api/chashui/status?${params.toString()}`);
  const members = Array.isArray(data.members) ? data.members : [];
  const selected = data.member || members.find((m) => String(m.status || "").toLowerCase() === "online") || members[0];
  if (!selected || !selected.id) throw new Error("没有可用拉数据会员，请先在号池管理里保持至少一个拉数据用账号在线。");
  setLaneMember(k, selected);
  if (k === "games") {
    state.matchSearch.memberId = String(selected.id || "");
    state.matchSearch.memberLabel = String(selected.label || selected.username || selected.id || "");
  }
  return String(selected.id || "");
}

async function searchHgMatches() {
  const query = String(state.matchSearch.query || "").trim();
  const seq = ++matchSearchRequestSeq;
  if (!query) {
    state.matchSearch.results = [];
    state.matchSearch.error = "";
    state.matchSearch.loading = false;
    resetReadLaneMembers();
    renderOrdersView();
    return;
  }

  state.matchSearch.loading = true;
  state.matchSearch.error = "";
  renderOrdersView();

  try {
    const memberId = await ensureMatchSearchMember();
    const mode = String(state.matchSearch.mode || "all").trim().toLowerCase();
    const modes = mode === "all" ? MATCH_SEARCH_MODES : [mode || "live"];
    const orderSearchContext = buildHgOrderSearchContext(state.orders, query);
    const settled = await Promise.allSettled(
      modes.map(async (m) => {
        const data = await fetchJson(`/api/chashui/games?member_id=${encodeURIComponent(memberId)}&mode=${encodeURIComponent(m)}`);
        if (data && data.readonly_member) setLaneMember("games", data.readonly_member);
        const rows = filterHgSearchGames(data.games || [], query, orderSearchContext).map((game) => normalizeHgSearchGame(game, {
          mode: data.mode || m,
          memberId: String((data.readonly_member && data.readonly_member.id) || memberId || "").trim(),
          memberLabel: String((data.readonly_member && data.readonly_member.label) || state.matchSearch.memberLabel || "").trim(),
          matchedOrderId: orderSearchContext.matchedOrderId,
          teamIds: orderSearchContext.teamIds,
        }));
        return rows;
      })
    );
    if (seq !== matchSearchRequestSeq) return;
    const summary = matchSearchTools && typeof matchSearchTools.collectHgSearchSettledResults === "function"
      ? matchSearchTools.collectHgSearchSettledResults(settled)
      : { rows: [], error: "比赛搜索失败", warning: "" };
    if (summary.error) throw new Error(summary.error);
    state.matchSearch.results = summary.rows.slice(0, 24);
    if (summary.warning) console.warn(summary.warning);
    state.matchSearch.retained = mergeRetainedHgMatches(state.matchSearch.retained, state.matchSearch.results);
    saveRetainedHgMatches();
  } catch (e) {
    if (seq !== matchSearchRequestSeq) return;
    state.matchSearch.results = [];
    state.matchSearch.error = e && e.message ? e.message : String(e || "比赛搜索失败");
  } finally {
    if (seq === matchSearchRequestSeq) {
      state.matchSearch.loading = false;
      renderOrdersView();
    }
  }
}

async function refreshRetainedHgMatches(options = {}) {
  const retained = Array.isArray(state.matchSearch.retained) ? state.matchSearch.retained : [];
  if (!retained.length || matchRetainedRefreshInFlight) return false;
  matchRetainedRefreshInFlight = true;
  try {
    const memberId = await ensureMatchSearchMember();
    const modes = Array.from(new Set(retained
      .map((row) => String(row && row.mode || "live").trim().toLowerCase())
      .filter((mode) => mode === "live" || mode === "today" || mode === "early")));
    if (!modes.length) modes.push("live");

    const updates = [];
    const updatedKeys = new Set();
    const successfulModes = new Set();
    const settled = await Promise.allSettled(modes.map(async (mode) => {
      const data = await fetchJson(`/api/chashui/games?member_id=${encodeURIComponent(memberId)}&mode=${encodeURIComponent(mode)}`);
      if (data && data.readonly_member) setLaneMember("games", data.readonly_member);
      successfulModes.add(data.mode || mode);
      return (Array.isArray(data.games) ? data.games : []).map((game) => normalizeHgSearchGame(game, {
        mode: data.mode || mode,
        memberId: String((data.readonly_member && data.readonly_member.id) || memberId || "").trim(),
        memberLabel: String((data.readonly_member && data.readonly_member.label) || state.matchSearch.memberLabel || "").trim(),
      }));
    }));

    for (const item of settled) {
      if (item.status !== "fulfilled") continue;
      for (const row of item.value || []) {
        if (!row || !row.key) continue;
        updates.push(row);
        updatedKeys.add(row.key);
      }
    }
    if (!updates.length && !successfulModes.size) return false;

    let next = mergeRetainedHgMatches(retained, updates);
    const liveChecked = successfulModes.has("live");
    if (liveChecked) {
      next = next.map((row) => {
        if (!row || row.mode !== "live" || updatedKeys.has(row.key)) return row;
        return Object.assign({}, row, {
          statusLine: "已结束",
          statusText: "已结束",
          isEnded: true,
          isRunningClock: false,
          capturedAt: Date.now(),
        });
      });
    }

    state.matchSearch.retained = next;
    saveRetainedHgMatches();
    if (options.render !== false && state.view === "orders") renderOrdersView();
    return true;
  } catch (e) {
    console.warn("retained match refresh failed:", e);
    return false;
  } finally {
    matchRetainedRefreshInFlight = false;
  }
}

function scheduleHgMatchSearch() {
  if (matchSearchDebounceTimer) window.clearTimeout(matchSearchDebounceTimer);
  matchSearchDebounceTimer = window.setTimeout(() => {
    searchHgMatches().catch(() => {});
  }, 320);
}

function toggleRetainHgSearchMatch(match) {
  const model = normalizeHgSearchGame(match, match || {});
  if (isHgMatchRetained(state.matchSearch.retained, model.key)) {
    state.matchSearch.retained = removeRetainedHgMatch(state.matchSearch.retained, model.key);
  } else {
    state.matchSearch.retained = upsertRetainedHgMatch(state.matchSearch.retained, Object.assign({}, model, { retainedAt: Date.now() }));
  }
  saveRetainedHgMatches();
  renderOrdersView();
}

function renderHgSearchMatchCard(match) {
  const model = normalizeHgSearchGame(match, match || {});
  const card = document.createElement("div");
  const retained = isHgMatchRetained(state.matchSearch.retained, model.key);
  const statusDisplay = resolveCardMatchStatusDisplay(model);
  card.className = `match-card hg-order-card hg-search-match-card${retained ? " is-retained" : ""}${statusDisplay.isEnded ? " is-ended" : ""}`;
  card.innerHTML = `
    <div class="hg-card-topline">
      <span class="hg-league-name" title="${escapeHtml(model.league)}">${escapeHtml(model.league)}</span>
      <button class="hg-retain-button ${retained ? "is-retained" : ""}" type="button" data-action="retain-match">
        ${retained ? "✓ 已保留" : "+ 保留"}
      </button>
    </div>
    <div class="hg-scoreboard">
      <div class="hg-team hg-team-home" title="${escapeHtml(model.homeTeam)}">${escapeHtml(model.homeTeam)}</div>
      <div class="hg-score-cluster">
        <strong class="hg-score">${escapeHtml(model.score)}</strong>
        <span class="hg-status ${statusDisplay.isEnded ? "hg-status--ended" : ""}" ${matchStatusDataAttrs(model)}>${escapeHtml(statusDisplay.statusLine || model.statusLine)}</span>
      </div>
      <div class="hg-team hg-team-away" title="${escapeHtml(model.awayTeam)}">${escapeHtml(model.awayTeam)}</div>
    </div>
    <div class="hg-action-grid">
      <button class="hg-action-btn hg-action-btn--primary" type="button" data-detail-group="盘口信息">
        <span class="hg-action-icon">&#9783;</span>
        盘口信息
      </button>
      <button class="hg-action-btn hg-action-btn--placeholder" type="button" data-detail-group="投注信息">
        <span class="hg-action-icon">&#128176;</span>
        投注信息
      </button>
      <button class="hg-action-btn hg-action-btn--placeholder" type="button" data-detail-group="比赛事件">
        <span class="hg-action-icon">&#9917;</span>
        比赛事件
      </button>
    </div>
  `;
  card.addEventListener("click", () => openHgMatchModal(model, "比赛详情"));
  card.querySelector("[data-action='retain-match']")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    toggleRetainHgSearchMatch(model);
  });
  card.querySelectorAll("[data-detail-group]").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openHgMatchModal(model, btn.getAttribute("data-detail-group") || "比赛详情");
    });
  });
  return card;
}

function renderHgMatchDetailCards(model, group) {
  if (group === "比赛事件") {
    return buildHgEventDetailItems(model).map((item) => `
      <article class="order-detail-card">
        <span>${escapeHtml(item.label || "-")}</span>
        <strong>${escapeHtml(item.value || "-")}</strong>
      </article>
    `).join("");
  }
  const itemsByGroup = {
    "盘口信息": [
      ["gid", model.gid],
      ["联赛ID", model.lid],
      ["事件ID", model.ecid || model.eventId],
      ["数据ID", model.mtId],
      ["更多盘", model.moreCount == null ? "-" : String(model.moreCount)],
      ["查水会员", model.memberLabel || model.memberId || "-"],
    ],
    "投注信息": [
      ["保留状态", isHgMatchRetained(state.matchSearch.retained, model.key) ? "已保留" : "未保留"],
      ["来源", model.provider || "chashui"],
      ["模式", model.modeLabel],
      ["gid", model.gid],
      ["主队", model.homeTeam],
      ["客队", model.awayTeam],
    ],
    "比赛详情": [
      ["gid", model.gid],
      ["联赛", model.league],
      ["比分", model.score],
      ["状态", model.statusLine],
      ["主队", model.homeTeam],
      ["客队", model.awayTeam],
    ],
  };
  const items = itemsByGroup[group] || itemsByGroup["比赛详情"];
  return items.map(([label, value]) => `
    <article class="order-detail-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "-")}</strong>
    </article>
  `).join("");
}

function formatHgOddsScore(value, fallback) {
  const text = String(value == null ? "" : value).trim();
  if (!text) return fallback || "0 : 0";
  return text.replace(/\s*[:：-]\s*/g, " : ");
}

function mergeHgMatchModelWithOdds(model, snapshot) {
  const meta = snapshot && typeof snapshot === "object" && snapshot.meta && typeof snapshot.meta === "object" ? snapshot.meta : {};
  const readonlyMember = snapshot && typeof snapshot === "object" && snapshot.readonly_member && typeof snapshot.readonly_member === "object" ? snapshot.readonly_member : {};
  const time = String(meta.reTime || model.time || "").trim();
  const mode = String(meta.mode || model.mode || "live").trim().toLowerCase() || "live";
  const modeLabel = mode === "today" ? "今日" : mode === "early" ? "早盘" : "滚球";
  return Object.assign({}, model, {
    mode,
    modeLabel,
    gid: String(meta.masterGid || model.gid || "").trim(),
    ecid: String(meta.ecid || model.ecid || "").trim(),
    lid: String(meta.lid || model.lid || "").trim(),
    mtId: String(meta.mtId || model.mtId || "").trim(),
    league: String(meta.league || model.league || "未命名联赛").trim(),
    homeTeam: String(meta.teamH || model.homeTeam || "主队").trim(),
    awayTeam: String(meta.teamC || model.awayTeam || "客队").trim(),
    dataMemberId: String(readonlyMember.id || model.dataMemberId || model.memberId || "").trim(),
    dataMemberLabel: String(readonlyMember.label || model.dataMemberLabel || model.memberLabel || "").trim(),
    score: formatHgOddsScore(meta.score, model.score),
    time,
    statusLine: [modeLabel, time].filter(Boolean).join(" | ") || model.statusLine,
  });
}

function formatHgEventStatusDisplay(value) {
  const text = String(value || "").trim();
  const low = text.toLowerCase();
  if (!text) return "";
  if (low.includes("1st") || low.includes("first half")) return "上半场";
  if (low.includes("2nd") || low.includes("second half")) return "下半场";
  if (low.includes("half time") || low === "ht") return "中场";
  if (low.includes("ended") || low.includes("finished") || low === "ft") return "已结束";
  return text;
}

function mergeHgMatchModelWithEvent(model, snapshot) {
  const source = snapshot && typeof snapshot === "object" ? snapshot : {};
  const timeline = source.timeline && typeof source.timeline === "object" ? source.timeline : {};
  const meta = source.meta && typeof source.meta === "object" ? source.meta : {};
  const readonlyMember = source.readonly_member && typeof source.readonly_member === "object" ? source.readonly_member : {};
  const mode = String(meta.mode || model.mode || "live").trim().toLowerCase() || "live";
  const modeLabel = mode === "today" ? "今日" : mode === "early" ? "早盘" : "滚球";
  const statusText = formatHgEventStatusDisplay(timeline.statusText) || model.statusText || model.statusLine || "";
  return Object.assign({}, model, {
    mode,
    modeLabel,
    gid: String(meta.masterGid || model.gid || "").trim(),
    mtId: String(timeline.matchId || meta.mtId || model.mtId || "").trim(),
    homeTeam: String(timeline.homeTeam || model.homeTeam || "主队").trim(),
    awayTeam: String(timeline.awayTeam || model.awayTeam || "客队").trim(),
    dataMemberId: String(readonlyMember.id || model.dataMemberId || model.memberId || "").trim(),
    dataMemberLabel: String(readonlyMember.label || model.dataMemberLabel || model.memberLabel || "").trim(),
    score: formatHgOddsScore(timeline.score, model.score),
    statusText,
    statusLine: statusText || [modeLabel, model.time].filter(Boolean).join(" | ") || model.statusLine,
  });
}

function formatHgMatchStatusDisplay(model) {
  const text = String(model && (model.time || model.statusLine || "") || "").trim();
  const normalized = text.replace(/\s+/g, " ");
  const half = normalized.match(/\b([12])H\^+([^|\s]+)/i);
  if (half) {
    return `${half[1] === "2" ? "下半场" : "上半场"} | ${half[2]}`;
  }
  if (/HT\^*/i.test(normalized)) return "中场";
  return normalized || String(model && model.statusLine || "").trim() || "-";
}

function resolveCardMatchStatusDisplay(model, options = {}) {
  const source = model && typeof model === "object" ? model : {};
  const helper = matchSearchTools && typeof matchSearchTools.resolveMatchStatusDisplay === "function"
    ? matchSearchTools.resolveMatchStatusDisplay
    : (orderRealtimeTools && typeof orderRealtimeTools.resolveMatchStatusDisplay === "function" ? orderRealtimeTools.resolveMatchStatusDisplay : null);
  if (helper) {
    return helper({
      mode: source.mode || source.orderKind,
      time: source.time || source.timeStr,
      statusLine: source.statusLine,
      statusText: source.matchStatusText || source.statusText,
      capturedAt: source.capturedAt,
      lastSeenAt: source.lastSeenAt,
    }, { now: options.now || Date.now() });
  }
  const statusLine = String(source.statusLine || [source.orderKind, source.time || source.timeStr].filter(Boolean).join(" | ") || "-").trim();
  const ended = /完场|已结束|结束/i.test(statusLine) || /完场|已结束|结束/i.test(String(source.matchStatusText || source.statusText || ""));
  return {
    statusLine: ended ? "已结束" : statusLine,
    statusText: ended ? "已结束" : String(source.matchStatusText || source.statusText || statusLine).trim(),
    isEnded: ended,
    isRunningClock: false,
  };
}

function matchStatusDataAttrs(model) {
  const source = model && typeof model === "object" ? model : {};
  const attrs = {
    "data-match-clock": "1",
    "data-match-mode": source.mode || source.orderKind || "",
    "data-match-time": source.time || source.timeStr || "",
    "data-match-status-line": source.statusLine || "",
    "data-match-status-text": source.matchStatusText || source.statusText || "",
    "data-match-captured-at": source.capturedAt || "",
  };
  return Object.entries(attrs)
    .map(([key, value]) => `${key}="${escapeHtml(value)}"`)
    .join(" ");
}

function updateVisibleMatchClocks() {
  const nodes = document.querySelectorAll("[data-match-clock='1']");
  if (!nodes.length) return;
  nodes.forEach((node) => {
    const source = {
      mode: node.getAttribute("data-match-mode") || "",
      time: node.getAttribute("data-match-time") || "",
      statusLine: node.getAttribute("data-match-status-line") || "",
      statusText: node.getAttribute("data-match-status-text") || "",
      capturedAt: Number(node.getAttribute("data-match-captured-at") || 0),
    };
    const display = resolveCardMatchStatusDisplay(source, { now: Date.now() });
    node.textContent = display.statusLine || "-";
    node.classList.toggle("hg-status--ended", Boolean(display.isEnded));
    const card = node.closest(".hg-order-card");
    if (card) card.classList.toggle("is-ended", Boolean(display.isEnded));
  });
}

function getHgEventConfig(type, card) {
  const eventType = String(type || "").trim().toLowerCase();
  const cardType = String(card || "").trim().toLowerCase();
  if (eventType === "goal") return { icon: "fa-futbol", label: "进球", tier: "key" };
  if (eventType === "owngoal") return { icon: "fa-futbol", label: "乌龙球", tier: "key" };
  if (eventType === "goal_cancelled") return { icon: "fa-ban", label: "进球取消", tier: "normal" };
  if (eventType === "card") {
    if (cardType === "red" || cardType === "yellowred") return { icon: "fa-square", label: cardType === "yellowred" ? "二黄变红" : "红牌", tier: "key" };
    return { icon: "fa-square", label: "黄牌", tier: "normal" };
  }
  if (eventType === "corner") return { icon: "fa-flag", label: "角球", tier: "normal" };
  if (eventType === "freekick") return { icon: "fa-location-arrow", label: "任意球", tier: "normal" };
  if (eventType === "shotontarget") return { icon: "fa-bullseye", label: "射正", tier: "normal" };
  if (eventType === "shotofftarget") return { icon: "fa-arrow-up-right-dots", label: "射偏", tier: "minor" };
  if (eventType === "goal_kick") return { icon: "fa-circle", label: "球门球", tier: "minor" };
  if (eventType === "throwin") return { icon: "fa-circle", label: "界外球", tier: "minor" };
  if (eventType === "video_assistant_referee") return { icon: "fa-video", label: "VAR", tier: "normal" };
  if (eventType === "injurytimeshown") return { icon: "fa-plus", label: "伤停补时", tier: "minor" };
  if (eventType === "periodscore") return { icon: "fa-list", label: "半场", tier: "normal" };
  if (eventType === "substitution") return { icon: "fa-rotate", label: "换人", tier: "normal" };
  return { icon: "fa-circle", label: "事件", tier: "minor" };
}

function hgEventTeamClass(team) {
  const side = String(team || "").trim().toLowerCase();
  if (side === "home") return "home";
  if (side === "away") return "away";
  return "neutral";
}

function renderHgEventTimeline(timeline) {
  const events = timeline && Array.isArray(timeline.events) ? timeline.events : [];
  if (!events.length) return `<article class="hg-detail-drawer-state">当前暂无比赛事件数据</article>`;
  return `
    <section class="hg-events-timeline">
      ${events.map((event) => {
        const cfg = getHgEventConfig(event.type, event.card);
        const minute = event.minuteLabel || (event.minute == null ? "-" : `${event.minute}`);
        const teamClass = hgEventTeamClass(event.type === "owngoal" ? (event.team === "home" ? "away" : event.team === "away" ? "home" : event.team) : event.team);
        const desc = event.description || event.label || "";
        if (event.type === "goal" || event.type === "owngoal") {
          return `
            <div class="hg-event-goal is-${teamClass}">
              <div class="hg-event-goal-top">
                <span class="hg-event-goal-min">${escapeHtml(minute)}'</span>
                <span class="hg-event-goal-icon"><i class="fa-solid ${cfg.icon}"></i></span>
                <span class="hg-event-goal-label">${escapeHtml(cfg.label)}</span>
              </div>
              ${event.score ? `<div class="hg-event-goal-score">${escapeHtml(event.score)}</div>` : ""}
              ${desc ? `<div class="hg-event-goal-desc">${escapeHtml(desc)}</div>` : ""}
            </div>
          `;
        }
        if (event.type === "periodscore") {
          return `
            <div class="hg-event-period">
              <div class="hg-event-period-line"></div>
              <span class="hg-event-period-label">${escapeHtml(minute)}</span>
              <div class="hg-event-period-line"></div>
            </div>
          `;
        }
        if (cfg.tier === "minor") {
          return `
            <div class="hg-event-minor is-${hgEventTeamClass(event.team)}">
              <span class="hg-event-minor-min">${escapeHtml(minute)}'</span>
              <span class="hg-event-minor-label">${escapeHtml(cfg.label)}</span>
            </div>
          `;
        }
        return `
          <div class="hg-event-normal is-${hgEventTeamClass(event.team)}">
            <span class="hg-event-normal-min">${escapeHtml(minute)}'</span>
            <span class="hg-event-normal-icon"><i class="fa-solid ${cfg.icon}"></i></span>
            <div class="hg-event-normal-body">
              <span class="hg-event-normal-label">${escapeHtml(cfg.label)}</span>
              ${desc ? `<span class="hg-event-normal-desc">${escapeHtml(desc)}</span>` : ""}
            </div>
          </div>
        `;
      }).join("")}
    </section>
  `;
}

function renderHgOddsSections(snapshot) {
  const sections = buildHgOddsSections(snapshot || {});
  if (!sections.length) return "";
  return sections.map((section) => `
    <section class="hg-odds-section">
      <div class="hg-odds-section-header">
        <span class="hg-odds-section-label">${escapeHtml(section.label || "盘口")}</span>
        <span class="hg-odds-section-count">${(section.entries || []).length} 项</span>
      </div>
      <div class="hg-detail-drawer-grid">
        ${(section.entries || []).map((entry) => `
          <article class="hg-detail-drawer-card">
            <span class="hg-detail-drawer-card-label">${escapeHtml(entry.label || "-")}</span>
            <strong class="hg-detail-drawer-card-odd">${escapeHtml(entry.odd || "-")}</strong>
          </article>
        `).join("")}
      </div>
    </section>
  `).join("");
}

function renderHgOddsPanel(model, options = {}) {
  const snapshot = options.snapshot || null;
  const error = String(options.error || "").trim();
  const loading = !!options.loading;
  const sectionsHtml = renderHgOddsSections(snapshot);

  if (loading && !snapshot) {
    return `
      <article class="hg-detail-drawer-state">
        <div class="hg-detail-loading"></div>
        <span>加载盘口详情中...</span>
      </article>
    `;
  }
  if (error && !snapshot) {
    return `<article class="hg-detail-drawer-state is-error"><i class="fa-solid fa-triangle-exclamation"></i>${escapeHtml(error)}</article>`;
  }
  if (!sectionsHtml) {
    return `<article class="hg-detail-drawer-state">当前暂无盘口数据</article>`;
  }
  return `
    ${sectionsHtml}
    ${error ? `<div class="hg-detail-error-banner"><i class="fa-solid fa-triangle-exclamation"></i> 上次刷新失败：${escapeHtml(error)}</div>` : ""}
  `;
}

function renderHgEventPanel(model, options = {}) {
  const snapshot = options.snapshot || null;
  const error = String(options.error || "").trim();
  const loading = !!options.loading;
  const timeline = snapshot && typeof snapshot === "object" ? snapshot.timeline : null;

  if (loading && !snapshot) {
    return `
      <article class="hg-detail-drawer-state">
        <div class="hg-detail-loading"></div>
        <span>加载比赛事件中...</span>
      </article>
    `;
  }
  if (error && !snapshot) {
    return `<article class="hg-detail-drawer-state is-error"><i class="fa-solid fa-triangle-exclamation"></i>${escapeHtml(error)}</article>`;
  }
  return `
    ${timeline ? renderHgEventTimeline(timeline) : `<article class="hg-detail-drawer-state">当前暂无比赛事件数据</article>`}
    ${error ? `<div class="hg-detail-error-banner"><i class="fa-solid fa-triangle-exclamation"></i> 上次刷新失败：${escapeHtml(error)}</div>` : ""}
  `;
}

function renderHgMatchModalContent(match, group = "比赛详情", options = {}) {
  const baseModel = normalizeHgSearchGame(match, match || {});
  const model = group === "比赛事件"
    ? mergeHgMatchModelWithEvent(baseModel, options.snapshot)
    : mergeHgMatchModelWithOdds(baseModel, options.snapshot);
  const content = $("order-detail-content");
  if (!content) return;
  const isOddsPanel = group === "盘口信息";
  const isEventPanel = group === "比赛事件";
  content.innerHTML = `
    <header class="order-detail-hero">
      <span class="order-detail-eyebrow">${escapeHtml(model.league)}</span>
      <div class="order-detail-match">
        <span>${escapeHtml(model.homeTeam)}</span>
        <strong>${escapeHtml(model.score)}</strong>
        <span>${escapeHtml(model.awayTeam)}</span>
      </div>
      <div class="order-detail-meta-line">
        <span>${escapeHtml(model.statusLine)}</span>
        <span>#${escapeHtml(model.gid || model.eventId || "-")}</span>
      </div>
    </header>
    <section class="order-detail-summary">
      <div class="order-detail-summary-item">
        <span>当前比分</span>
        <strong>${escapeHtml(model.score)}</strong>
      </div>
      <div class="order-detail-summary-item">
        <span>${isOddsPanel || isEventPanel ? "比赛状态" : "保留状态"}</span>
        <strong>${isOddsPanel ? escapeHtml(formatHgMatchStatusDisplay(model)) : isEventPanel ? escapeHtml(formatHgEventStatusDisplay(model.statusText || model.statusLine) || "-") : (isHgMatchRetained(state.matchSearch.retained, model.key) ? "已保留" : "未保留")}</strong>
      </div>
      ${(isOddsPanel || isEventPanel) ? `
      <div class="order-detail-summary-item">
        <span>当前拉数据号</span>
        <strong>${escapeHtml(model.dataMemberLabel || model.memberLabel || model.memberId || "-")}</strong>
      </div>` : ""}
      <div class="order-detail-summary-item">
        <span>面板类型</span>
        <strong>${escapeHtml(group)}</strong>
      </div>
    </section>
    <section class="order-detail-section ${isOddsPanel ? "hg-odds-live-panel" : isEventPanel ? "hg-event-live-panel" : ""}">
      ${isOddsPanel ? renderHgOddsPanel(model, options) : `
        <div class="order-detail-section-head">
          <span>${escapeHtml(group)}</span>
          <em>${escapeHtml(model.modeLabel)}</em>
        </div>
        ${isEventPanel ? renderHgEventPanel(model, options) : `
          <div class="order-detail-grid">
            ${renderHgMatchDetailCards(model, group)}
          </div>
        `}
      `}
    </section>
  `;
  if ($("modal-pre")) $("modal-pre").textContent = JSON.stringify(options.snapshot || model.raw || model, null, 2);
}

function hgOddsToken(model) {
  return [model.key, model.memberId || "", model.gid || model.eventId || "", model.mode || "live"].join("|");
}

function hgEventToken(model) {
  return [model.key, model.memberId || "", model.gid || model.ecid || model.mtId || "", model.mode || "live"].join("|");
}

function stopHgMatchOddsRefresh() {
  if (matchOddsRefreshTimer) {
    window.clearInterval(matchOddsRefreshTimer);
    matchOddsRefreshTimer = null;
  }
  matchOddsActiveToken = "";
  matchOddsLastSnapshot = null;
  matchOddsRefreshing = false;
  matchOddsRequestSeq += 1;
}

function stopHgMatchEventRefresh() {
  if (matchEventRefreshTimer) {
    window.clearInterval(matchEventRefreshTimer);
    matchEventRefreshTimer = null;
  }
  matchEventActiveToken = "";
  matchEventLastSnapshot = null;
  matchEventRefreshing = false;
  matchEventRequestSeq += 1;
}

async function fetchHgMatchOddsSnapshot(model, options = {}) {
  const memberId = await ensureReadLaneMember("odds");
  const gid = model.gid || model.eventId;
  if (!gid) throw new Error("当前比赛缺少 gid，无法读取盘口。");
  const mode = model.mode || "live";
  const confirm = options.confirm === false ? "0" : "1";
  const url = `/api/chashui/odds?member_id=${encodeURIComponent(memberId)}&gid=${encodeURIComponent(gid)}&mode=${encodeURIComponent(mode)}&confirm=${confirm}&raw=0`;
  const data = await fetchJson(url);
  if (!data || data.ok === false) throw new Error((data && data.message) || "盘口读取失败");
  if (data && data.readonly_member) setLaneMember("odds", data.readonly_member);
  return data;
}

async function fetchHgMatchEventSnapshot(model) {
  const memberId = await ensureReadLaneMember("events");
  const marketMode = model.mode || "live";
  const eventMode = matchEventLastSnapshot && matchEventLastSnapshot.timeline ? "delta" : "full";
  const matchId = String(model.mtId || model.eventMatchId || (model.raw && (model.raw.MT_ID || model.raw.mtId)) || "").trim();
  const gid = String(model.gid || model.eventId || "").trim();
  const params = new URLSearchParams({
    member_id: memberId,
    mode: eventMode,
    market_mode: marketMode,
  });
  if (matchId) params.set("match_id", matchId);
  else if (gid) params.set("gid", gid);
  else throw new Error(formatHgEventErrorMessage("missing_match_id"));

  let response;
  try {
    response = await fetch(`/api/chashui/events?${params.toString()}`, { cache: "no-store" });
  } catch {
    throw new Error("网络请求失败：请确认后台服务正在运行，稍后刷新。");
  }
  const text = await response.text().catch(() => "");
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok || !data || data.ok === false) {
    const message = data && data.message ? data.message : (text || `HTTP ${response.status}`);
    throw new Error(formatHgEventErrorMessage(message));
  }
  if (data && data.readonly_member) setLaneMember("events", data.readonly_member);
  return data;
}

async function refreshHgMatchOdds(model, options = {}) {
  const token = hgOddsToken(model);
  if (!matchOddsActiveToken || token !== matchOddsActiveToken || matchOddsRefreshing) return;
  const seq = ++matchOddsRequestSeq;
  matchOddsRefreshing = true;
  if (options.first && !matchOddsLastSnapshot) {
    renderHgMatchModalContent(model, "盘口信息", { loading: true, snapshot: null, error: "" });
  }
  try {
    const snapshot = await fetchHgMatchOddsSnapshot(model);
    if (token !== matchOddsActiveToken || seq !== matchOddsRequestSeq) return;
    matchOddsLastSnapshot = snapshot;
    renderHgMatchModalContent(model, "盘口信息", { loading: false, snapshot, error: "" });
  } catch (e) {
    if (token !== matchOddsActiveToken || seq !== matchOddsRequestSeq) return;
    renderHgMatchModalContent(model, "盘口信息", {
      loading: false,
      snapshot: matchOddsLastSnapshot,
      error: e && e.message ? e.message : String(e || "盘口读取失败"),
    });
  } finally {
    if (token === matchOddsActiveToken) matchOddsRefreshing = false;
  }
}

function startHgMatchOddsRefresh(model) {
  stopHgMatchEventRefresh();
  stopHgMatchOddsRefresh();
  matchOddsActiveToken = hgOddsToken(model);
  renderHgMatchModalContent(model, "盘口信息", { loading: true, snapshot: null, error: "" });
  refreshHgMatchOdds(model, { first: true }).catch(() => {});
  matchOddsRefreshTimer = window.setInterval(() => {
    refreshHgMatchOdds(model).catch(() => {});
  }, MATCH_ODDS_REFRESH_MS);
}

async function refreshHgMatchEvent(model, options = {}) {
  const token = hgEventToken(model);
  if (!matchEventActiveToken || token !== matchEventActiveToken || matchEventRefreshing) return;
  const seq = ++matchEventRequestSeq;
  matchEventRefreshing = true;
  if (options.first && !matchEventLastSnapshot) {
    renderHgMatchModalContent(model, "比赛事件", { loading: true, snapshot: null, error: "" });
  }
  try {
    let snapshot = await fetchHgMatchEventSnapshot(model);
    if (token !== matchEventActiveToken || seq !== matchEventRequestSeq) return;
    if (snapshot && snapshot.timeline && matchEventLastSnapshot && matchEventLastSnapshot.timeline) {
      snapshot = Object.assign({}, snapshot, {
        timeline: mergeHgEventTimeline(matchEventLastSnapshot.timeline, snapshot.timeline),
      });
    }
    if (snapshot && snapshot.meta && snapshot.meta.mtId && !model.mtId) {
      model.mtId = String(snapshot.meta.mtId || "").trim();
    }
    matchEventLastSnapshot = snapshot;
    rememberOrderEventSnapshot(model, snapshot);
    applyEventSnapshotToRetainedMatches(model, snapshot);
    state.orders = applyOrderEventSnapshotsForDisplay(state.orders);
    renderHgMatchModalContent(model, "比赛事件", { loading: false, snapshot, error: "" });
    if (state.view === "orders") renderOrdersView();
  } catch (e) {
    if (token !== matchEventActiveToken || seq !== matchEventRequestSeq) return;
    renderHgMatchModalContent(model, "比赛事件", {
      loading: false,
      snapshot: matchEventLastSnapshot,
      error: e && e.message ? e.message : String(e || "比赛事件读取失败"),
    });
  } finally {
    if (token === matchEventActiveToken) matchEventRefreshing = false;
  }
}

function startHgMatchEventRefresh(model) {
  stopHgMatchOddsRefresh();
  stopHgMatchEventRefresh();
  matchEventActiveToken = hgEventToken(model);
  renderHgMatchModalContent(model, "比赛事件", { loading: true, snapshot: null, error: "" });
  refreshHgMatchEvent(model, { first: true }).catch(() => {});
  matchEventRefreshTimer = window.setInterval(() => {
    refreshHgMatchEvent(model).catch(() => {});
  }, MATCH_ODDS_REFRESH_MS);
}

function showOrderDetailModal(mode = "drawer") {
  const modal = $("modal");
  if (!modal) return;
  const active = document.activeElement;
  if (active instanceof HTMLElement && active !== document.body && !modal.contains(active)) {
    orderModalReturnFocus = active;
  }
  modal.classList.toggle("is-bet-info", mode === "bet-info");
  modal.classList.add("show");
  window.requestAnimationFrame(() => {
    const closeButton = modal.querySelector(".modal-close");
    if (closeButton) closeButton.focus({ preventScroll: true });
  });
}

function openHgMatchModal(match, group = "比赛详情") {
  const model = normalizeHgSearchGame(match, match || {});
  const title = $("modal-title");
  if (title) {
    title.innerHTML = `<i class="fa-solid fa-futbol"></i><span>${escapeHtml(group)}</span>${group === "盘口信息" || group === "比赛事件" ? `<span class="hg-title-live-dot" title="数据实时更新中"></span>` : ""}`;
  }
  if (group === "盘口信息") {
    startHgMatchOddsRefresh(model);
  } else if (group === "比赛事件") {
    startHgMatchEventRefresh(model);
  } else {
    stopHgMatchOddsRefresh();
    stopHgMatchEventRefresh();
    renderHgMatchModalContent(model, group, {});
  }
  showOrderDetailModal();
}

function buildRealtimeOrderListRow(input) {
  if (typeof orderListTools.buildRealtimeOrderListRow === "function") {
    return orderListTools.buildRealtimeOrderListRow(input);
  }
  const model = input && input.model ? input.model : {};
  return {
    league: model.league || "未知联赛",
    orderKind: model.orderKind || model.modeLabel || "滚球",
    homeTeam: model.homeTeam || "主队",
    awayTeam: model.awayTeam || "客队",
    score: model.score || "0 : 0",
    statusLine: model.statusLine || "—",
    marketText: model.selectionText || model.marketText || "—",
    marketCaption: model.marketText || "",
    betAmountText: model.stakeStr || "—",
    followAmountText: "—",
    followStatusText: input && input.searchOnly ? "比赛查询" : "待处理",
    settlementText: "—",
    settlementTone: "pending",
    isSearchOnly: !!(input && input.searchOnly),
  };
}

function renderRealtimeOrderListRow(rowData, options = {}) {
  const row = document.createElement("div");
  row.className = `hg-order-list-row${options.ended ? " is-ended" : ""}${rowData.isSearchOnly ? " is-search-row" : ""}`;
  row.innerHTML = `
    <div class="hg-list-cell hg-cell-league" title="${escapeHtml(rowData.league)}"><strong>${escapeHtml(rowData.league)}</strong></div>
    <div class="hg-list-cell hg-cell-kind"><span class="hg-order-kind ${rowData.orderKind === "滚球" ? "live" : "early"}">${escapeHtml(rowData.orderKind)}</span></div>
    <div class="hg-list-cell hg-cell-match">
      <span class="hg-list-team" title="${escapeHtml(rowData.homeTeam)}">${escapeHtml(rowData.homeTeam)}</span>
      <strong class="hg-list-score">${escapeHtml(rowData.score)}</strong>
      <span class="hg-list-team" title="${escapeHtml(rowData.awayTeam)}">${escapeHtml(rowData.awayTeam)}</span>
    </div>
    <div class="hg-list-cell hg-cell-progress" data-match-clock="${escapeHtml(options.clockKey || "")}">${escapeHtml(rowData.statusLine)}</div>
    <div class="hg-list-cell hg-cell-market"><strong>${escapeHtml(rowData.marketText)}</strong>${rowData.marketCaption ? `<small>${escapeHtml(rowData.marketCaption)}</small>` : ""}</div>
    <div class="hg-list-cell hg-cell-money">${escapeHtml(rowData.betAmountText)}</div>
    <div class="hg-list-cell hg-cell-money">${escapeHtml(rowData.followAmountText)}</div>
    <div class="hg-list-cell hg-cell-follow tone-${escapeHtml(rowData.followTone || "idle")}">${escapeHtml(rowData.followStatusText)}</div>
    <div class="hg-list-cell hg-cell-settlement settlement-${escapeHtml(rowData.settlementTone)}">${escapeHtml(rowData.settlementText)}</div>
    <div class="hg-list-cell hg-cell-action">
      <button class="hg-details-trigger" type="button" data-detail-group="盘口信息"><i class="fa-solid fa-chart-line"></i><span>盘口信息</span></button>
      <button class="hg-details-trigger" type="button" data-detail-group="投注信息"><i class="fa-solid fa-receipt"></i><span>投注信息</span></button>
      <button class="hg-details-trigger" type="button" data-detail-group="比赛事件"><i class="fa-solid fa-futbol"></i><span>比赛事件</span></button>
    </div>
  `;
  return row;
}

function appendRealtimeOrderListHeader(grid) {
  const header = document.createElement("div");
  header.className = "hg-match-list-head";
  header.innerHTML = "<span>联赛</span><span>类型</span><span>主队 / 实时比分 / 客队</span><span>比赛状态</span><span>投注盘口（水位）</span><span>投注金额</span><span>跟单金额</span><span>投注状态</span><span>结算状态</span><span>操作</span>";
  grid.appendChild(header);
}

// ── Order stream feedback states ─────────────────────────────
function hasActiveOrderFilters() {
  const f = (state.orderDashboard && state.orderDashboard.filters) || {};
  return !!(
    state.search || state.selectedTarget ||
    f.keyword || f.orderType || f.status || f.matchProgress ||
    f.playCategory || f.league || f.amountBucket ||
    (f.timeRange && f.timeRange !== "today")
  );
}

function canShowOrderStreamState() {
  return !state.matchSearch.query && !(state.matchSearch.retained || []).length;
}

function renderOrderStreamSkeleton(rows = 5) {
  const grid = $("matches-grid");
  if (!grid) return;
  const bar = (w, sm) => `<span class="sk-bar${sm ? " sk-sm" : ""}" style="width:${w}px"></span>`;
  let html = "";
  for (let i = 0; i < rows; i++) {
    html += `<div class="hg-skeleton-row">${bar(88)}${bar(44, 1)}<span class="sk-bar sk-grow"></span>${bar(64, 1)}<span class="sk-bar sk-grow sk-sm"></span>${bar(56)}${bar(56)}${bar(72, 1)}</div>`;
  }
  grid.innerHTML = html;
  realtimeOrderListRenderSignature = grid.innerHTML;
}

function renderOrderStreamError(err) {
  const grid = $("matches-grid");
  if (!grid) return;
  const msg = escapeHtml((err && err.message) || "加载失败，请稍后重试");
  grid.innerHTML = `
    <div class="empty-state error-state">
      <div class="empty-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
      <div class="empty-title">订单数据加载失败</div>
      <div class="empty-sub">${msg}</div>
      <div class="empty-actions">
        <button class="btn btn-primary btn-sm" type="button" id="order-stream-retry"><i class="fa-solid fa-arrows-rotate"></i>重新加载</button>
      </div>
    </div>
  `;
  realtimeOrderListRenderSignature = grid.innerHTML;
  $("order-stream-retry")?.addEventListener("click", () => refreshView({ manual: true }));
}

function updateStreamMeta() {
  const meta = document.querySelector(".od-stream-meta");
  if (!meta) return;
  const t = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  meta.innerHTML = `按最新接收时间排序 · <span class="upd-time">更新于 ${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}</span>`;
}

// ── Realtime order list ───────────────────────────────────────
function renderTable(orders) {
  const grid = $("matches-grid");
  if (!grid) return;
  const nextGrid = document.createElement("div");

  const commit = () => {
    const nextSignature = nextGrid.innerHTML;
    const shouldReplace = orderListTools && typeof orderListTools.shouldReplaceRealtimeOrderList === "function"
      ? orderListTools.shouldReplaceRealtimeOrderList(realtimeOrderListRenderSignature, nextSignature, grid.childElementCount > 0)
      : !grid.childElementCount || realtimeOrderListRenderSignature !== nextSignature;
    if (shouldReplace) {
      grid.replaceChildren(...Array.from(nextGrid.childNodes));
      realtimeOrderListRenderSignature = nextSignature;
    }
    return shouldReplace;
  };

  const searchMatches = getDisplayedSearchMatches();

  if (searchMatches.length === 0) {
    renderMatchSearchStatusCard(nextGrid);
  }

  if ((!orders || orders.length === 0) && searchMatches.length === 0 && !state.matchSearch.loading && !state.matchSearch.error) {
    if (hasActiveOrderFilters()) {
      nextGrid.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon"><i class="fa-solid fa-filter-circle-xmark"></i></div>
          <div class="empty-title">没有符合条件的订单</div>
          <div class="empty-sub">当前筛选或搜索条件下暂无订单，可调整条件后重试</div>
          <div class="empty-actions">
            <button class="btn btn-primary btn-sm" type="button" id="order-empty-reset"><i class="fa-solid fa-rotate-left"></i>重置筛选</button>
            <button class="btn btn-ghost btn-sm" type="button" id="order-empty-history"><i class="fa-solid fa-clock-rotate-left"></i>查看历史</button>
          </div>
        </div>
      `;
      nextGrid.querySelector("#order-empty-reset")?.addEventListener("click", () => {
        $("od-reset")?.click();
        if ($("search")) $("search").value = "";
        state.search = "";
        renderOrdersView();
      });
      nextGrid.querySelector("#order-empty-history")?.addEventListener("click", () => {
        updateOrderDashboardFilter("timeRange", "history");
      });
    } else {
      nextGrid.innerHTML = `
        <div class="empty-state">
          <div class="scanner-beam"></div>
          <div class="empty-icon"><i class="fa-solid fa-satellite-dish"></i></div>
          <div class="empty-title">等待注单信号</div>
          <div class="empty-sub">正在等待新的注单信号...</div>
        </div>
      `;
    }
    commit();
    return;
  }

  appendRealtimeOrderListHeader(nextGrid);

  const linkedOrderIds = new Set();

  for (const match of searchMatches) {
    const model = normalizeHgSearchGame(match, match || {});
    const linkedOrder = model.matchedOrderId
      ? state.orders.find((order) => String(order && (order.orderId || order.order_id) || "").trim() === model.matchedOrderId)
      : null;
    const linkedModel = linkedOrder ? buildOrderCardModel(linkedOrder) : null;
    const displayModel = linkedModel ? Object.assign({}, linkedModel, {
      league: model.league,
      homeTeam: model.homeTeam,
      awayTeam: model.awayTeam,
      score: model.score,
      statusLine: model.statusLine,
      isEnded: model.isEnded,
    }) : model;
    const rowData = buildRealtimeOrderListRow({
      model: displayModel,
      searchOnly: !linkedOrder,
      followSummary: linkedOrder ? getFollowSummary(model.matchedOrderId) : null,
      followStake: linkedOrder ? getFollowStake(model.matchedOrderId) : 0,
      settlement: linkedOrder ? getSettlement(model.matchedOrderId) : null,
    });
    const row = renderRealtimeOrderListRow(rowData, { ended: model.isEnded, clockKey: model.key });
    row.querySelectorAll("[data-detail-group]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const group = btn.getAttribute("data-detail-group") || "比赛详情";
        if (linkedOrder && group === "投注信息") openModal(linkedOrder, group);
        else openHgMatchModal(model, group);
      });
    });
    if (linkedOrder) linkedOrderIds.add(model.matchedOrderId);
    nextGrid.appendChild(row);
  }

  for (const o of orders) {
    const model = buildOrderCardModel(o);
    const oid = String(model.orderId || o.orderId || o.order_id || "").trim();
    if (linkedOrderIds.has(oid)) continue;
    const rowData = buildRealtimeOrderListRow({
      model,
      followSummary: getFollowSummary(oid),
      followStake: getFollowStake(oid),
      settlement: getSettlement(oid),
    });
    const row = renderRealtimeOrderListRow(rowData, { ended: model.isEnded, clockKey: model.orderId });
    row.querySelectorAll("[data-detail-group]").forEach((btn) => {
      btn.addEventListener("click", () => {
        openModal(o, btn.getAttribute("data-detail-group") || "注单详情");
      });
    });
    nextGrid.appendChild(row);
  }
  commit();
  renderMatchSearchToolbar();
}

// ── KPIs ───────────────────────────────────────────────────────
function renderKpis(orders) {
  const k = computeKpis(orders);
  if ($("kpi-today")) $("kpi-today").textContent = String(k.today);
  if ($("kpi-all"))   $("kpi-all").textContent = String(k.all);

  const memberStats = computeArsenalStats(state.members);
  const onlineCount = memberStats.online;
  const totalCount  = memberStats.total;
  if ($("kpi-arsenal")) {
    $("kpi-arsenal").textContent = `${onlineCount} / ${totalCount}`;
    $("kpi-arsenal").style.color = onlineCount > 0 ? "var(--success)" : "var(--text-3)";
  }
  renderTradingNodeKpi();

  const a = state.selectedTarget ? findAccountByTargetName(state.selectedTarget) : null;
  if ($("kpi-today-sub")) {
    $("kpi-today-sub").textContent = state.selectedTarget === REMOTE_SIGNAL_FILTER_TARGET
      ? "信号源: 远端交易节点"
      : state.selectedTarget ? `账号: ${accountLabel(a)}` : "全部目标";
  }
}

// ── Modals ─────────────────────────────────────────────────────
function buildHgModelFromOrder(order, model) {
  const raw = order && order.raw && typeof order.raw === "object" ? order.raw : {};
  const liveMatch = order && order._live_match && typeof order._live_match === "object" ? order._live_match : {};
  const mode = liveMatch.mode || (model.isLiveOrder ? "live" : "today");
  return normalizeHgSearchGame({
    gid: liveMatch.gid || model.eventId,
    mtId: liveMatch.mtId || raw.MT_ID || raw.mtId || "",
    ecid: liveMatch.ecid || raw.ECID || raw.ecid || "",
    lid: liveMatch.lid || raw.LID || raw.lid || "",
    league: liveMatch.league || model.league,
    teamH: liveMatch.homeTeam || model.homeTeam,
    teamC: liveMatch.awayTeam || model.awayTeam,
    score: liveMatch.score || model.score,
    time: liveMatch.time || model.timeStr,
    statusLine: liveMatch.statusLine || model.statusLine,
    statusText: liveMatch.statusText || model.statusText,
    memberId: liveMatch.memberId || state.matchSearch.memberId,
    memberLabel: liveMatch.memberLabel || state.matchSearch.memberLabel,
    raw: Object.assign({}, raw, liveMatch.raw || {}),
  }, {
    mode,
    memberId: liveMatch.memberId || state.matchSearch.memberId,
    memberLabel: liveMatch.memberLabel || state.matchSearch.memberLabel,
  });
}

function openModal(order, group = "注单详情") {
  stopHgMatchOddsRefresh();
  stopHgMatchEventRefresh();
  const model = buildOrderCardModel(order);
  const title = $("modal-title");
  if (title) {
    const icon = group === "盘口信息" ? "fa-list-ul" : group === "投注信息" ? "fa-sack-dollar" : group === "比赛事件" ? "fa-futbol" : "fa-file-invoice-dollar";
    title.innerHTML = `<i class="fa-solid ${icon}"></i><span>${escapeHtml(group)}</span>${group === "盘口信息" || group === "比赛事件" ? `<span class="hg-title-live-dot" title="数据实时更新中"></span>` : ""}`;
  }
  if (group === "盘口信息") {
    const hgModel = buildHgModelFromOrder(order, model);
    startHgMatchOddsRefresh(hgModel);
    if ($("modal-pre")) $("modal-pre").textContent = JSON.stringify(order, null, 2);
    showOrderDetailModal();
    return;
  }
  if (group === "比赛事件") {
    const hgModel = buildHgModelFromOrder(order, model);
    startHgMatchEventRefresh(hgModel);
    if ($("modal-pre")) $("modal-pre").textContent = JSON.stringify(order, null, 2);
    showOrderDetailModal();
    return;
  }
  const content = $("order-detail-content");
  if (content) {
    content.innerHTML = group === "投注信息" ? renderOrderBetInfoView(model, order) : `
      <header class="order-detail-hero">
        <span class="order-detail-eyebrow">${escapeHtml(model.league)}</span>
        <div class="order-detail-match">
          <span>${escapeHtml(model.homeTeam)}</span>
          <strong>${escapeHtml(model.score)}</strong>
          <span>${escapeHtml(model.awayTeam)}</span>
        </div>
        <div class="order-detail-meta-line">
          <span>${escapeHtml(model.statusLine)}</span>
          <span>#${escapeHtml(model.orderId)}</span>
        </div>
      </header>
      <section class="order-detail-summary">
        <div class="order-detail-summary-item">
          <span>当前比分</span>
          <strong>${escapeHtml(model.score)}</strong>
        </div>
        <div class="order-detail-summary-item">
          <span>订单状态</span>
          <strong>${escapeHtml(model.statusText)}</strong>
        </div>
        <div class="order-detail-summary-item">
          <span>面板类型</span>
          <strong>${escapeHtml(group)}</strong>
        </div>
      </section>
      <section class="order-detail-section">
        <div class="order-detail-section-head">
          <span>${escapeHtml(group)}</span>
          <em>${escapeHtml(model.selectionText)}</em>
        </div>
        <div class="order-detail-grid">
          ${renderOrderDetailCards(model, order, group)}
        </div>
      </section>
      ${group === "比赛事件" ? `
        <section class="order-detail-note">
          当前已拿到赛事号 ${escapeHtml(model.eventId)}，可继续接入事件源后展示进球、红黄牌、角球等时间轴。
        </section>
      ` : ""}
    `;
  }
  if ($("modal-pre")) $("modal-pre").textContent = JSON.stringify(order, null, 2);
  showOrderDetailModal(group === "投注信息" ? "bet-info" : "drawer");
}
function closeModal() {
  stopHgMatchOddsRefresh();
  stopHgMatchEventRefresh();
  const modal = $("modal");
  if (modal) {
    modal.classList.remove("show", "is-bet-info");
  }
  const returnTarget = orderModalReturnFocus;
  orderModalReturnFocus = null;
  if (returnTarget && returnTarget.isConnected) {
    window.requestAnimationFrame(() => returnTarget.focus({ preventScroll: true }));
  }
}

if (typeof document.addEventListener === "function") {
  document.addEventListener("keydown", (event) => {
    const modal = $("modal");
    if (!modal || !modal.classList.contains("show")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeModal();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(modal.querySelectorAll(
      'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )).filter((element) => element.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}
function openAddModal() {
  const helper = window.HDAddNodeForm;
  addNodeFormState = helper && typeof helper.createDefaultAddNodeForm === "function"
    ? helper.createDefaultAddNodeForm()
    : {
        mode: "account",
        account: { base_url: "", username: "", password: "", login_tab: 3, relogin_min_interval_seconds: 5, security_code: "", monitor_members: [], monitor_downlines: [] },
        signal: { enabled: true, url: "", token: "", live_only: true, limit: 200, interval_seconds: 1 },
      };
  renderAddNodeModal();
  const m = $("modal-add"); if (m) m.classList.add("show");
  if ($("add-base-url")) $("add-base-url").focus();
}
function closeAddModal() {
  const m = $("modal-add"); if (m) m.classList.remove("show");
}
function bindAddNodeModalControls() {
  const bindOnce = (element, eventName, handler) => {
    if (!element || element.dataset.addNodeModalBound === "true") return;
    element.addEventListener(eventName, handler);
    element.dataset.addNodeModalBound = "true";
  };
  bindOnce($("btn-add-monitor"), "click", openAddModal);
  bindOnce($("modal-add-close"), "click", closeAddModal);
  bindOnce($("modal-add-close-btn"), "click", closeAddModal);
  bindOnce($("add-submit"), "click", submitAddAccount);
  bindOnce($("add-mode-account"), "click", () => setAddMode("account"));
  bindOnce($("add-mode-signal"), "click", () => setAddMode("signal"));
  bindOnce($("add-login-tab-2"), "click", () => setAddLoginTab(2));
  bindOnce($("add-login-tab-3"), "click", () => setAddLoginTab(3));
}
function openMemberModal() {
  setMemberHint("");
  setMemberPasswordVisible(false);
  const m = $("modal-member"); if (m) m.classList.add("show");
  $("mem-base-url")?.focus();
}
function closeMemberModal() {
  const m = $("modal-member"); if (m) m.classList.remove("show");
}

function setMemberPasswordVisible(visible) {
  const input = $("mem-password");
  const button = $("mem-password-toggle");
  const isVisible = !!visible;
  if (input) input.type = isVisible ? "text" : "password";
  if (button) {
    const label = isVisible ? "隐藏会员密码" : "显示会员密码";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.setAttribute("aria-pressed", String(isVisible));
    const icon = button.querySelector("i");
    if (icon) icon.className = `fa-solid ${isVisible ? "fa-eye-slash" : "fa-eye"}`;
  }
}

function toggleMemberPasswordVisibility() {
  setMemberPasswordVisible($("mem-password")?.type === "password");
}

function bindMemberModalControls() {
  const modal = $("modal-member");
  if (!modal || modal.dataset.memberModalBound === "true") return;
  $("btn-add-member")?.addEventListener("click", openMemberModal);
  $("modal-member-close")?.addEventListener("click", closeMemberModal);
  $("modal-member-close-btn")?.addEventListener("click", closeMemberModal);
  $("mem-password-toggle")?.addEventListener("click", toggleMemberPasswordVisibility);
  $("mem-proxy-test")?.addEventListener("click", testMemberProxy);
  $("mem-submit")?.addEventListener("click", submitMember);
  modal.dataset.memberModalBound = "true";
}

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function parseListField(value) {
  return String(value || "")
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function setAddMode(mode) {
  if (!addNodeFormState) return;
  addNodeFormState.mode = mode === "signal" ? "signal" : "account";
  renderAddNodeModal();
}

function setAddLoginTab(tab) {
  if (!addNodeFormState) return;
  addNodeFormState.account.login_tab = Number(tab) === 2 ? 2 : 3;
  renderAddNodeModal();
}

window.setAddMode = setAddMode;
window.setAddLoginTab = setAddLoginTab;

function renderAddNodeModal() {
  if (!addNodeFormState) return;
  const isSignal = addNodeFormState.mode === "signal";
  $("add-mode-account")?.classList.toggle("active", !isSignal);
  $("add-mode-signal")?.classList.toggle("active", isSignal);
  $("add-mode-account")?.setAttribute("aria-pressed", String(!isSignal));
  $("add-mode-signal")?.setAttribute("aria-pressed", String(isSignal));
  if ($("add-account-fields")) $("add-account-fields").hidden = isSignal;
  if ($("add-signal-fields")) $("add-signal-fields").hidden = !isSignal;

  const loginTab = Number(addNodeFormState.account?.login_tab || 3);
  const isLogin2 = loginTab === 2;
  const supportsOrderSource = isLogin2 || loginTab === 3;
  $("add-login-tab-2")?.classList.toggle("active", isLogin2);
  $("add-login-tab-3")?.classList.toggle("active", !isLogin2);
  $("add-login-tab-2")?.setAttribute("aria-pressed", String(isLogin2));
  $("add-login-tab-3")?.setAttribute("aria-pressed", String(!isLogin2));
  if ($("add-login2-extra")) $("add-login2-extra").hidden = !isLogin2;
  if ($("add-login-member-extra")) $("add-login-member-extra").hidden = !isLogin2;
  if ($("add-order-source-extra")) $("add-order-source-extra").hidden = !supportsOrderSource;

  if ($("add-base-url")) $("add-base-url").value = String(addNodeFormState.account?.base_url || "");
  if ($("add-username")) $("add-username").value = String(addNodeFormState.account?.username || "");
  if ($("add-password")) $("add-password").value = String(addNodeFormState.account?.password || "");
  if ($("add-relogin-min-interval")) $("add-relogin-min-interval").value = String(addNodeFormState.account?.relogin_min_interval_seconds ?? 5);
  if ($("add-security-code")) $("add-security-code").value = String(addNodeFormState.account?.security_code || "");
  if ($("add-monitor-members")) $("add-monitor-members").value = (addNodeFormState.account?.monitor_members || []).join("\n");
  if ($("add-monitor-downlines")) $("add-monitor-downlines").value = (addNodeFormState.account?.monitor_downlines || []).join("\n");

  if ($("add-signal-url")) $("add-signal-url").value = String(addNodeFormState.signal?.url || "");
  if ($("add-signal-token")) $("add-signal-token").value = String(addNodeFormState.signal?.token || "");
  if ($("add-signal-limit")) $("add-signal-limit").value = String(addNodeFormState.signal?.limit ?? 200);
  if ($("add-signal-interval")) $("add-signal-interval").value = String(addNodeFormState.signal?.interval_seconds ?? 1);
  if ($("add-signal-live-only")) $("add-signal-live-only").checked = addNodeFormState.signal?.live_only !== false;

  const submitLabel = $("add-submit-label");
  if (submitLabel) submitLabel.textContent = isSignal ? "保存信号源" : "立即接入";
  const submitIcon = $("add-submit")?.querySelector("i");
  if (submitIcon) submitIcon.className = `fa-solid ${isSignal ? "fa-floppy-disk" : "fa-plug"}`;
  if ($("add-hint")) {
    $("add-hint").textContent = "";
    $("add-hint").className = "form-hint";
  }
}

// ── Fetch helper ───────────────────────────────────────────────
async function fetchJson(url, init) {
  let r;
  try {
    r = await fetch(url, { cache: "no-store", ...init });
  } catch {
    throw new Error("网络请求失败：请确认后台服务正在运行，稍后刷新。");
  }
  const text = await r.text().catch(() => "");
  if (!r.ok) {
    const detail = text ? `：${text.slice(0, 200)}` : "";
    throw new Error(`请求失败（HTTP ${r.status}）${detail}`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`响应格式错误：${text.slice(0, 200) || "空响应"}`);
  }
}

// ── Accounts ───────────────────────────────────────────────────
async function loadAccounts() {
  const data = await fetchJson("/api/accounts");
  state.accounts = (data.accounts || []).filter((a) => a && (a.name || a.username));
  renderAccountsSidebar();
  renderAccountSelect();
  renderTradingNodeKpi();
  if (state.view === "arsenal") renderPoolManagement();
}

async function loadStatuses() {
  try {
    const data = await fetchJson("/api/status");
    updateFollowEngineState(data);
    state.statuses = data.statuses || {};
    state.systemStatus = {
      serverTime: String(data.server_time || ""),
      uptime: Number(data.uptime || 0),
      latency: Number(data.latency || 0),
      running: !!data.running,
      snapshotWatchRunning: !!data.snapshot_watch_running,
      coreRunning: !!data.core_running,
      updatedAt: Date.now(),
    };
    if ($("server-time")) $("server-time").textContent = new Date().toLocaleTimeString();
    if (typeof data.uptime === "number") {
      const d = Math.floor(data.uptime / 86400);
      const h = Math.floor((data.uptime % 86400) / 3600);
      const m = Math.floor((data.uptime % 3600) / 60);
      const s = Math.floor(data.uptime % 60);
      const pad = (n) => n.toString().padStart(2, "0");
      const text = d > 0 ? `${d}d ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
      if ($("kpi-uptime")) $("kpi-uptime").textContent = text;
      state.systemCenter.uptimeText = text;
    }
    state.systemCenter.version = String(data.version || data.ver || "未提供");
    state.systemCenter.syncTone = "ok";
    state.systemCenter.lastSyncAt = Date.now();
    renderTradingNodeKpi();
  } catch {
    state.systemCenter.syncTone = "error";
  }
  renderFollowEngineControls();
  renderSystemCenterStatusBar();
  if (state.view === "arsenal") renderPoolManagement();
}

async function reloginAccount(name, label, successMessage) {
  if (!name) return;
  try {
    const data = await fetchJson("/api/accounts/relogin", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!data.ok) throw new Error(data.message || "relogin_failed");
    await loadStatuses();
    renderAccountsSidebar();
    const st = state.statuses[String(name || "")] || {};
    const message = String(st.status_message || data.message || successMessage || `已发起重新登录: ${label}`).trim();
    toast(message, "info", 6500);
  } catch (e) {
    toast(`登录失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

async function deleteAccount(name, label) {
  if (!name) return;
  try {
    const data = await fetchJson("/api/accounts/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!data.ok) throw new Error(data.message || "delete_failed");
    await loadAccounts();
    toast(`已删除账号: ${label}`, "success");
  } catch (e) {
    toast(`删除失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

async function deleteSignalSource(item) {
  const endpoint = item && item.deleteEndpoint ? String(item.deleteEndpoint) : "/api/signal-pull-config/delete";
  const label = (item && item.name) || "远端交易节点";
  const meta = item && item.metaText ? `（${item.metaText}）` : "";
  if (!confirm(`确定要删除${label}${meta}吗？删除后将停止接收这个远端交易节点的信号。`)) return;
  try {
    const data = await fetchJson(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!data.ok) throw new Error(data.message || "delete_signal_source_failed");
    state.signalPullConfig = normalizeSignalPullConfig(data);
    if (state.selectedTarget === REMOTE_SIGNAL_FILTER_TARGET) {
      state.selectedTarget = "";
      const sel = $("account"); if (sel) sel.value = "";
    }
    renderAccountsSidebar();
    renderAccountSelect();
    renderTradingNodeKpi();
    await refreshView();
    toast("远端交易节点已删除，已停止接收该远端信号", "success");
  } catch (e) {
    toast(`删除失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

// ── Telegram ───────────────────────────────────────────────────
async function loadTelegramConfig() {
  const data = await fetchJson("/api/telegram-config");
  state.tg = data || null;
  const configured = !!data.configured;
  if ($("tg-status")) {
    $("tg-status").textContent = configured ? "已配置" : "未配置";
    $("tg-status").className = configured ? "badge badge-success" : "badge badge-secondary";
  }
  if ($("tg-token-hint")) $("tg-token-hint").textContent = data.token_mask || (configured ? "Token 已设置" : "");
  if ($("tg-chat"))  $("tg-chat").value  = String(data.chat_id || "");
  if ($("tg-proxy")) $("tg-proxy").value = String(data.proxy || "");
  const pushEl = $("tg-status-push"); if (pushEl) pushEl.checked = !!data.status_push_enabled;
  const sHint  = $("tg-status-token-hint"); if (sHint) sHint.textContent = data.status_token_mask || "";
  if ($("tg-status-chat"))  $("tg-status-chat").value  = String(data.status_chat_id || "");
  if ($("tg-status-proxy")) $("tg-status-proxy").value = String(data.status_proxy || "");
  renderSystemCenterTelegramSummary();
}

async function saveTelegramConfig() {
  const token = ($("tg-token")?.value || "").trim();
  const chatId = ($("tg-chat")?.value  || "").trim();
  const proxy  = ($("tg-proxy")?.value || "").trim();
  const statusPushEnabled = !!$("tg-status-push")?.checked;
  const statusChatId = ($("tg-status-chat")?.value || "").trim();
  const statusProxy = ($("tg-status-proxy")?.value || "").trim();
  const hint = $("tg-hint");
  if (hint) { hint.textContent = "正在保存..."; hint.className = "form-hint"; }
  try {
    const data = await fetchJson("/api/telegram-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_token: token,
        chat_id: chatId,
        proxy,
        status_push_enabled: statusPushEnabled,
        status_chat_id: statusChatId,
        status_proxy: statusProxy,
      }),
    });
    if (!data.ok) throw new Error(data.message || "保存失败");
    if ($("tg-token")) $("tg-token").value = "";
    await loadTelegramConfig();
    if (hint) { hint.textContent = "保存成功"; hint.className = "form-hint success"; }
    toast("消息通知配置已保存", "success");
    recordSystemCenterActivity("消息通知配置已保存", "success");
    return true;
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    if (hint) { hint.textContent = `错误: ${msg}`; hint.className = "form-hint error"; }
    toast(`保存失败: ${msg}`, "error");
    recordSystemCenterActivity(`消息通知保存失败：${msg}`, "error");
    return false;
  }
}

async function clearTelegramConfig() {
  const ok = window.confirm("确认清除当前 Telegram 配置？清除后需要重新填写 Bot Token、Chat ID 和代理。");
  if (!ok) return;
  const hint = $("tg-hint");
  if (hint) { hint.textContent = "正在清除当前配置..."; hint.className = "form-hint"; }
  try {
    const data = await fetchJson("/api/telegram-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ "clear_config": true }),
    });
    if (!data.ok) throw new Error(data.message || "清除失败");
    ["tg-token", "tg-chat", "tg-proxy", "tg-status-chat", "tg-status-proxy"].forEach((id) => {
      const el = $(id);
      if (el) el.value = "";
    });
    const pushEl = $("tg-status-push");
    if (pushEl) pushEl.checked = false;
    await loadTelegramConfig();
    if (hint) { hint.textContent = "当前配置已清除，请填写新的机器人信息"; hint.className = "form-hint success"; }
    toast("Telegram 当前配置已清除", "success");
    recordSystemCenterActivity("Telegram 当前配置已清除", "success");
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    if (hint) { hint.textContent = `错误: ${msg}`; hint.className = "form-hint error"; }
    toast(`清除失败: ${msg}`, "error");
  }
}

async function testTelegram() {
  const hint = $("tg-hint");
  if (hint) { hint.textContent = "正在发送测试消息..."; hint.className = "form-hint"; }
  try {
    const payload = { message: "HD 推送系统测试消息。" };
    const token = ($("tg-token")?.value || "").trim();
    const chatId = ($("tg-chat")?.value || "").trim();
    const proxy  = ($("tg-proxy")?.value || "").trim();
    if (token)  payload.bot_token = token;
    if (chatId) payload.chat_id   = chatId;
    if (proxy)  payload.proxy     = proxy;
    const data = await fetchJson("/api/telegram-test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.ok) throw new Error(data.message || "发送失败");
    if (hint) { hint.textContent = "测试消息已发送"; hint.className = "form-hint success"; }
    toast("消息通知测试发送成功", "success");
    recordSystemCenterActivity("测试通知发送成功", "success");
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    if (hint) { hint.textContent = `错误: ${msg}`; hint.className = "form-hint error"; }
    toast(`发送失败: ${msg}`, "error");
    recordSystemCenterActivity(`测试通知发送失败：${msg}`, "error");
  }
}

// ── Arsenal ────────────────────────────────────────────────────
function computeArsenalStats(members) {
  const helper = window.HDArsenalCards && window.HDArsenalCards.computeArsenalMemberStats;
  if (typeof helper === "function") return helper(members, {});
  const list = Array.isArray(members) ? members.filter(Boolean) : [];
  const online = list.filter((m) => String(m.status || "").trim().toLowerCase() === "online").length;
  return {
    total: list.length,
    online,
    ready: 0,
    recovering: 0,
    manual: 0,
    totalBalance: 0,
  };
}

function renderArsenalStats() {
  const stats = computeArsenalStats(state.members);
  const count = $("arsenal-count");
  const online = $("arsenal-online-count");
  const ready = $("arsenal-ready-count");
  const recovering = $("arsenal-recovering-count");
  const manual = $("arsenal-manual-count");
  const totalBalance = $("arsenal-total-balance");
  const badge = $("badge-arsenal");
  if (count) count.textContent = String(stats.total);
  if (online) online.textContent = String(stats.online);
  if (ready) ready.textContent = String(stats.ready || 0);
  if (recovering) recovering.textContent = String(stats.recovering || 0);
  if (manual) manual.textContent = String(stats.manual || 0);
  if (totalBalance) totalBalance.textContent = String(stats.totalBalance || 0);
  if ($("member-summary-online")) $("member-summary-online").textContent = String(stats.online || 0);
  if ($("pool-member-online-meta")) $("pool-member-online-meta").textContent = `${stats.online || 0} / ${stats.total || 0} 个会员`;
  if (badge) badge.textContent = String(stats.total);
}

async function loadArsenal() {
  const data = await fetchJson("/api/arsenal");
  state.members = (data.members || []).filter((m) => m && m.id);
  renderArsenalStats();
  await loadMappings();
}

async function loadMappings() {
  try {
    const data = await fetchJson("/api/mapping");
    state.mappings = data || {};
  } catch { state.mappings = {}; }
}

function memberLabel(m) { return (m && (m.label || m.username)) || "-"; }

function renderLegacyArsenalCards() {
  const grid = $("arsenal-cards");
  const empty = $("arsenal-empty");
  if (!grid || !empty) return;
  renderArsenalStats();
  grid.innerHTML = "";
  const visibleMembers = Array.isArray(state.members) ? state.members.filter(Boolean) : [];
  if (!visibleMembers.length) { empty.style.display = "block"; renderPoolOverview(); return; }
  empty.style.display = "none";

  if (state.poolSelection.kind !== "member" || !visibleMembers.some((member) => String(member.id) === state.poolSelection.id)) {
    state.poolSelection = { kind: "member", id: String(visibleMembers[0].id || "") };
  }

  for (const m of visibleMembers) {
    const mappedTarget = String(state.mappings[m.id] || "");
    const mappedAccount = state.accounts.find(a => String(a.name || "") === mappedTarget);
    const helper = window.HDArsenalCards && window.HDArsenalCards.buildArsenalCardModel;
    const card = helper
      ? helper(m, { mappingTarget: mappedTarget, mappingLabel: mappedAccount ? accountLabel(mappedAccount) : "" })
      : {
          id: String(m.id || ""),
          label: memberLabel(m),
          platformUrl: String(m.base_url || "-"),
          platformHost: String(m.base_url || "-"),
          statusText: String(m.status || "未知"),
          statusTone: String(m.status || "muted"),
          proxyText: String(m.proxy_ip || m.proxy_label || "未配置代理"),
          geoText: "未识别地区",
          mappingText: "全部跟单",
          selectValue: mappedTarget,
          usageValue: String(m.usage || (m.role === "quote" ? "read" : "bet") || "bet"),
          usageText: String(m.usage === "read" || m.role === "quote" ? "拉数据用" : "跟单用"),
          balanceText: "0",
          autoReloginText: "未知",
          eligibilityTone: "manual",
          eligibilityText: "未知",
          providerText: String(m.session_provider || "native"),
          lastError: String(m.last_error || ""),
          requestFailCount: 0,
          reloginFailCount: 0,
        };

    const article = document.createElement("article");
    const statusTone = String(card.statusTone || "muted").replace(/[^a-z0-9_-]/gi, "");
    const eligibilityTone = String(card.eligibilityTone || "manual").replace(/[^a-z0-9_-]/gi, "");
    const lastErrorBlock = card.lastError
      ? `<div class="arsenal-error-note" title="${escapeHtml(card.lastError)}">${escapeHtml(card.lastError)}</div>`
      : `<div class="arsenal-error-note is-empty">最近无异常</div>`;
    article.className = `arsenal-member-card status-${statusTone || "muted"} eligibility-${eligibilityTone || "manual"}`;
    article.classList.toggle("is-selected", state.poolSelection.kind === "member" && state.poolSelection.id === String(m.id || ""));
    article.innerHTML = `
      <div class="arsenal-card-head">
        <div class="arsenal-member-main">
          <div class="arsenal-avatar"><i class="fa-solid fa-user-shield"></i></div>
          <div class="arsenal-title-wrap">
            <div class="arsenal-member-name" title="${escapeHtml(card.label)}">${escapeHtml(card.label)}</div>
            <div class="arsenal-member-url" title="${escapeHtml(card.platformUrl)}">${escapeHtml(card.platformHost)}</div>
            <div class="arsenal-status-meta">${escapeHtml(card.statusMetaText || "")}</div>
          </div>
        </div>
        <div class="arsenal-chip-stack">
          <div class="arsenal-status-chip ${statusTone || "muted"}">
            ${card.statusTone === "online" ? '<span class="pulse-win"></span>' : ""}
            ${escapeHtml(card.statusText)}
          </div>
          <div class="arsenal-eligibility-chip ${eligibilityTone || "manual"}">${escapeHtml(card.eligibilityText || "未知")}</div>
        </div>
      </div>

      <div class="arsenal-card-body">
        <div class="arsenal-balance-band ${eligibilityTone || "manual"}">
          <div class="arsenal-balance-label">当前随机池可用额度</div>
          <div class="arsenal-balance-value">${escapeHtml(card.balanceText || "0")}</div>
          <div class="arsenal-balance-meta">${escapeHtml(card.balanceStateText || "当前可用额度")}</div>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-shuffle"></i>随机池资格</span>
          <span class="arsenal-info-value">${escapeHtml(card.eligibilityText || "未知")}</span>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-rotate"></i>自动恢复</span>
          <span class="arsenal-info-value">${escapeHtml(card.autoReloginText || "未知")}</span>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-plug-circle-bolt"></i>登录通道</span>
          <span class="arsenal-info-value">${escapeHtml(card.providerText || "未知")}</span>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-network-wired"></i>代理 IP</span>
          <span class="arsenal-info-value mono">${escapeHtml(card.proxyText)}</span>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-location-dot"></i>地区</span>
          <span class="arsenal-info-value">${escapeHtml(card.geoText)}</span>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-layer-group"></i>账号用途</span>
          <select class="form-input usage-select arsenal-mapping-select" data-id="${escapeHtml(card.id)}">
            <option value="bet" ${String(card.usageValue || "bet") === "bet" ? "selected" : ""}>跟单用</option>
            <option value="read" ${String(card.usageValue || "") === "read" ? "selected" : ""}>拉数据用</option>
          </select>
        </div>
        <div class="arsenal-info-row">
          <span class="arsenal-info-label"><i class="fa-solid fa-route"></i>跟单映射</span>
          <select class="form-input mapping-select arsenal-mapping-select" data-id="${escapeHtml(card.id)}">
          <option value="">全部跟单</option>
          ${state.accounts.map(a => `<option value="${escapeHtml(a.name||'')}" ${String(card.selectValue||'')===String(a.name||'')?'selected':''}>${escapeHtml(accountLabel(a))}</option>`).join('')}
          </select>
        </div>
        <div class="arsenal-info-row is-multi-line">
          <span class="arsenal-info-label"><i class="fa-solid fa-triangle-exclamation"></i>最近状态</span>
          <div class="arsenal-info-stack">
            <span class="arsenal-info-value">${escapeHtml(`失败计数 ${Number(card.requestFailCount || 0)} / 重登失败 ${Number(card.reloginFailCount || 0)}`)}</span>
            ${lastErrorBlock}
          </div>
        </div>
      </div>

      <div class="arsenal-card-actions">
        <button class="btn btn-primary btn-sm" data-action="login">
          <i class="fa-solid fa-right-to-bracket"></i> 重登
        </button>
        <button class="btn btn-ghost btn-sm" data-action="check">
            <i class="fa-solid fa-plug"></i> 检测
        </button>
        <button class="btn btn-danger btn-sm icon-only" data-action="delete" title="删除会员">
          <i class="fa-solid fa-trash"></i>
        </button>
      </div>
    `;
    article.querySelector("[data-action='login']")?.addEventListener("click", async (ev) => { ev.stopPropagation(); await loginArsenalMember(m.id, memberLabel(m)); });
    article.querySelector("[data-action='check']")?.addEventListener("click", async (ev) => { ev.stopPropagation(); await checkMember(m.id); });
    article.querySelector("[data-action='delete']")?.addEventListener("click", async (ev) => { ev.stopPropagation(); await deleteMember(m.id, memberLabel(m)); });
    article.querySelector(".mapping-select")?.addEventListener("change", async (ev) => { await saveMapping(m.id, ev.target.value); });
    article.querySelector(".usage-select")?.addEventListener("change", async (ev) => { await saveMemberUsage(m.id, ev.target.value); });
    article.addEventListener("click", (ev) => {
      if (ev.target.closest("button, select, input, a")) return;
      state.poolSelection = { kind: "member", id: String(m.id || "") };
      renderArsenalTable();
    });
    grid.appendChild(article);
  }
  renderPoolOverview();
}

function memberHealthScore(member, card) {
  const status = String(member && member.status || "").trim().toLowerCase();
  const failures = Number(card && card.requestFailCount || 0) + Number(card && card.reloginFailCount || 0);
  if (status === "online") return Math.max(40, 100 - Math.min(45, failures * 12) - (card && card.lastError ? 20 : 0));
  if (status === "relogin") return Math.max(25, 58 - Math.min(25, failures * 8));
  return card && card.canAutoRelogin ? 35 : 18;
}

function buildMemberPoolRow(member) {
  const mappedTarget = String(state.mappings[member.id] || "");
  const mappedAccount = state.accounts.find((account) => String(account.name || "") === mappedTarget);
  const helper = window.HDArsenalCards && window.HDArsenalCards.buildArsenalCardModel;
  const card = helper
    ? helper(member, { mappingTarget: mappedTarget, mappingLabel: mappedAccount ? accountLabel(mappedAccount) : "" })
    : {
        id: String(member.id || ""),
        label: memberLabel(member),
        platformHost: String(member.base_url || "-"),
        statusText: String(member.status || "未知"),
        statusTone: String(member.status || "muted"),
        usageValue: String(member.usage || "bet"),
        usageText: String(member.usage === "read" ? "拉数据用" : "跟单用"),
        selectValue: mappedTarget,
        mappingText: mappedAccount ? accountLabel(mappedAccount) : "全部跟单",
        balanceText: String(Number(member.balance || 0)),
        lastError: String(member.last_error || ""),
        requestFailCount: Number(member.request_fail_count || 0),
        reloginFailCount: Number(member.relogin_fail_count || 0),
        canAutoRelogin: member.auto_relogin !== false,
      };
  return {
    member,
    card,
    health: memberHealthScore(member, card),
    lastActive: Number(member.last_check_at || member.last_ok_at || 0),
  };
}

function renderArsenalTable() {
  const body = $("arsenal-cards");
  const empty = $("arsenal-empty");
  if (!body || !empty) return;
  renderArsenalStats();

  const dashboard = state.poolDashboard || {};
  const statusFilter = String(dashboard.memberStatus || "all");
  const rows = (Array.isArray(state.members) ? state.members.filter(Boolean) : [])
    .map(buildMemberPoolRow)
    .filter((row) => {
      if (statusFilter === "online" && String(row.member.status || "").toLowerCase() !== "online") return false;
      return true;
    });

  body.innerHTML = "";
  empty.hidden = rows.length > 0;
  const table = body.closest("table");
  if (table) table.hidden = rows.length === 0;

  for (const row of rows) {
    const { member, card, health } = row;
    const statusTone = String(card.statusTone || "muted").replace(/[^a-z0-9_-]/gi, "");
    const tr = document.createElement("tr");
    tr.className = `member-pool-row status-${statusTone || "muted"}`;
    tr.classList.toggle("is-selected", state.poolSelection.kind === "member" && state.poolSelection.id === String(member.id || ""));
    tr.innerHTML = `
      <td>
        <button class="member-identity" type="button" data-action="focus-member" title="查看 ${escapeHtml(card.label)} 详情">
          <span class="member-avatar"><i class="fa-solid fa-user-shield" aria-hidden="true"></i></span>
          <span><b>${escapeHtml(card.label)}</b><small title="${escapeHtml(card.platformHost)}">${escapeHtml(card.platformHost)}</small></span>
        </button>
      </td>
      <td><span class="member-status-badge ${statusTone || "muted"}"><i aria-hidden="true"></i>${escapeHtml(card.statusText || "未知")}</span></td>
      <td>
        <select class="pool-table-select usage-select" data-id="${escapeHtml(card.id)}" aria-label="${escapeHtml(card.label)} 的账号用途">
          <option value="bet" ${String(card.usageValue || "bet") === "bet" ? "selected" : ""}>跟单用</option>
          <option value="read" ${String(card.usageValue || "") === "read" ? "selected" : ""}>拉数据用</option>
        </select>
      </td>
      <td><span class="member-balance">¥ ${escapeHtml(card.balanceText || "0")}</span></td>
      <td>
        <div class="member-health" title="由在线状态、失败计数和最近错误实时派生">
          <span><i style="width:${health}%"></i></span><b>${health}%</b>
        </div>
      </td>
      <td>
        <select class="pool-table-select mapping-select" data-id="${escapeHtml(card.id)}" aria-label="${escapeHtml(card.label)} 的跟单映射">
          <option value="">全部跟单</option>
          ${state.accounts.map((account) => `<option value="${escapeHtml(account.name || "")}" ${String(card.selectValue || "") === String(account.name || "") ? "selected" : ""}>${escapeHtml(accountLabel(account))}</option>`).join("")}
        </select>
      </td>
      <td><time class="member-last-active" datetime="${row.lastActive ? new Date(row.lastActive * 1000).toISOString() : ""}">${escapeHtml(row.lastActive ? fmtTime(row.lastActive) : "暂无记录")}</time></td>
      <td>
        <div class="member-row-actions">
          <button type="button" data-action="login" title="重登 ${escapeHtml(card.label)}" aria-label="重登 ${escapeHtml(card.label)}"><i class="fa-solid fa-right-to-bracket" aria-hidden="true"></i></button>
          <button type="button" data-action="check" title="检测 ${escapeHtml(card.label)}" aria-label="检测 ${escapeHtml(card.label)}"><i class="fa-solid fa-plug" aria-hidden="true"></i></button>
          <button class="danger" type="button" data-action="delete" title="删除 ${escapeHtml(card.label)}" aria-label="删除 ${escapeHtml(card.label)}"><i class="fa-solid fa-trash" aria-hidden="true"></i></button>
        </div>
      </td>`;

    const focusMember = () => {
      state.poolSelection = { kind: "member", id: String(member.id || "") };
      document.querySelectorAll(".member-pool-row.is-selected").forEach((item) => item.classList.remove("is-selected"));
      tr.classList.add("is-selected");
      renderPoolOverview();
    };
    tr.querySelector("[data-action='focus-member']")?.addEventListener("click", focusMember);
    tr.addEventListener("click", (event) => {
      if (event.target.closest("button, select, input, a")) return;
      focusMember();
    });
    tr.querySelector("[data-action='login']")?.addEventListener("click", async (event) => { event.stopPropagation(); await loginArsenalMember(member.id, memberLabel(member)); });
    tr.querySelector("[data-action='check']")?.addEventListener("click", async (event) => { event.stopPropagation(); await checkMember(member.id); });
    tr.querySelector("[data-action='delete']")?.addEventListener("click", async (event) => { event.stopPropagation(); await deleteMember(member.id, memberLabel(member)); });
    tr.querySelector(".mapping-select")?.addEventListener("change", async (event) => { await saveMapping(member.id, event.target.value); });
    tr.querySelector(".usage-select")?.addEventListener("change", async (event) => { await saveMemberUsage(member.id, event.target.value); });
    body.appendChild(tr);
  }
}

function normalizePoolView(poolView) {
  return poolView === "monitors" ? "monitors" : "members";
}

function setPoolView(poolView) {
  const nextView = normalizePoolView(poolView);
  state.poolView = "overview";
  const target = document.querySelector(nextView === "monitors" ? ".pool-monitor-section" : ".pool-member-section");
  target?.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function bindPoolTabs() {
  const dashboard = state.poolDashboard;
  const bindOnce = (element, eventName, handler) => {
    if (!element || element.dataset.poolDashboardBound === "true") return;
    element.addEventListener(eventName, handler);
    element.dataset.poolDashboardBound = "true";
  };

  bindOnce($("pool-search"), "input", (event) => {
    dashboard.search = event.target.value || "";
    renderMonitorPool();
  });
  [["pool-source-filter", "source"], ["pool-status-filter", "status"], ["pool-channel-filter", "channel"]].forEach(([id, key]) => {
    bindOnce($(id), "change", (event) => {
      dashboard[key] = event.target.value || "all";
      renderMonitorPool();
    });
  });
  bindOnce($("pool-refresh"), "click", async () => {
    const button = $("pool-refresh");
    button?.classList.add("is-loading");
    try {
      await refreshView({ manual: true });
    } finally {
      button?.classList.remove("is-loading");
    }
  });
  document.querySelectorAll("[data-pool-kpi-filter]").forEach((button) => {
    bindOnce(button, "click", () => {
      const filter = button.getAttribute("data-pool-kpi-filter") || "all";
      dashboard.status = filter;
      if ($("pool-status-filter")) $("pool-status-filter").value = filter;
      renderMonitorPool();
    });
  });
  document.querySelectorAll("[data-member-status-filter]").forEach((button) => {
    bindOnce(button, "click", () => {
      dashboard.memberStatus = dashboard.memberStatus === "online" ? "all" : "online";
      button.classList.toggle("is-active", dashboard.memberStatus === "online");
      renderArsenalTable();
      setPoolView("members");
    });
  });
  bindOnce($("pool-source-legend"), "click", (event) => {
    const button = event.target.closest("button[data-source]");
    if (!button) return;
    dashboard.source = button.dataset.source || "all";
    if ($("pool-source-filter")) $("pool-source-filter").value = dashboard.source;
    renderMonitorPool();
  });
  bindOnce($("pool-source-donut"), "click", () => {
    const sequence = ["all", "backend", "signal"];
    dashboard.source = sequence[(sequence.indexOf(dashboard.source) + 1) % sequence.length];
    if ($("pool-source-filter")) $("pool-source-filter").value = dashboard.source;
    renderMonitorPool();
  });
}

function renderPoolManagement() {
  renderMonitorPool();
  renderArsenalTable();
  renderPoolOverview();
}

if (typeof window !== "undefined") window.setPoolView = setPoolView;

function formatMonitorTimestamp(value, emptyText = "还没扫到") {
  const ts = Number(value || 0);
  return Number.isFinite(ts) && ts > 0 ? fmtTime(ts) : emptyText;
}

function monitorAccountLoginTab(account) {
  const tab = Number(account && (account.relogin_login_tab || account.login_tab || 3));
  return tab === 2 ? 2 : 3;
}

function summarizeMonitorWatchScope(account) {
  const tab = monitorAccountLoginTab(account);
  const members = Array.isArray(account && account.login2_watch_members)
    ? account.login2_watch_members.filter(Boolean)
    : [];
  const downlines = Array.isArray(account && account.login2_watch_downlines)
    ? account.login2_watch_downlines.filter(Boolean)
    : [];
  const parts = [];
  if (tab === 2) {
    parts.push(members.length ? `会员 ${members.length} 个` : "全部会员");
  }
  parts.push(downlines.length ? `下线 ${downlines.length} 个` : "全部下线");
  return parts.join(" / ");
}

function classifyMonitorIssue(status, enabled = true) {
  if (!enabled) return "已暂停，不参与扫单";
  const st = status || {};
  const code = String(st.last_error_code || st.last_relogin_error_code || "").trim();
  const message = String(st.status_message || st.last_error_msg || st.last_relogin_error_msg || "").trim();
  const raw = [code, message].filter(Boolean).join(": ");
  if (!raw) return "";
  const lower = raw.toLowerCase();
  if (!code && ["在线", "正常", "运行中", "扫描中", "online", "ok", "running", "healthy"].includes(lower)) return "";
  if (code === "0X001" || raw.includes("流量较高") || raw.includes("系统繁忙") || lower.includes("busy")) {
    return `系统繁忙：${message || code}`;
  }
  if (code === "timeout" || raw.includes("超时") || lower.includes("timeout") || lower.includes("timed out")) {
    return `超时：${message || code}`;
  }
  if (raw.includes("验证码") || lower.includes("captcha")) return `需要验证码：${message || code}`;
  if (raw.includes("登录") || lower.includes("login")) return `登录异常：${message || code}`;
  return message && code ? `${code}: ${message}` : (message || code);
}

function buildBackendMonitorSource(account) {
  const id = String(account && account.name || "").trim();
  if (!id) return null;
  const status = state.statuses[id] || {};
  const enabled = account.enabled !== false;
  const rawStatus = String(status.status || "").trim().toLowerCase();
  const reloginBusy = !!status.relogin_in_progress || rawStatus === "relogin";
  const needsManual = !!status.needs_manual_login;
  let statusText = "离线";
  let statusTone = "offline";
  if (!enabled) {
    statusText = "已暂停";
  } else if (needsManual) {
    statusText = "需人工登录";
  } else if (reloginBusy) {
    statusText = "重登中";
    statusTone = "warning";
  } else if (rawStatus === "online") {
    statusText = "在线扫单";
    statusTone = "online";
  } else if (!rawStatus) {
    statusText = "等待状态";
    statusTone = "warning";
  }

  const lastPollAt = Number(status.last_poll_at || 0);
  const lastOkAt = Number(status.last_ok_at || 0);
  const issueText = classifyMonitorIssue(status, enabled);
  const tab = monitorAccountLoginTab(account);
  const scanScope = summarizeMonitorWatchScope(account);
  const proxyText = String(account.proxy || "").trim() || "未配置代理";
  return {
    kind: "backend",
    id,
    label: accountLabel(account),
    title: accountLabel(account),
    typeText: `登入${tab}后台监控号`,
    icon: "fa-tower-broadcast",
    avatarClass: "",
    statusText,
    statusTone,
    enabled,
    normal: enabled && statusTone === "online",
    primaryLabel: "最后扫单时间",
    primaryValue: formatMonitorTimestamp(lastPollAt),
    primaryMeta: lastOkAt ? `最后成功：${formatMonitorTimestamp(lastOkAt)}` : "还没有成功扫到订单接口",
    rows: [
      { icon: "fa-right-to-bracket", label: "登录通道", value: `登入${tab}` },
      { icon: "fa-list-check", label: "监控范围", value: scanScope },
      { icon: "fa-network-wired", label: "代理", value: proxyText, mono: true },
      { icon: "fa-circle-info", label: "运行状态", value: String(status.status_message || statusText) },
      { icon: "fa-triangle-exclamation", label: "失败原因", value: issueText || "最近无异常", warning: !!issueText },
    ],
    rawAccount: account,
  };
}

function buildSignalMonitorSource(item) {
  const cfg = state.signalPullConfig || {};
  const url = String(cfg.signal_pull_url || cfg.url || "").trim();
  if (!url && !item) return null;
  const enabled = Object.prototype.hasOwnProperty.call(cfg, "signal_pull_enabled")
    ? !!cfg.signal_pull_enabled
    : !!cfg.enabled;
  const limit = Number(cfg.signal_pull_limit || cfg.limit || 200);
  const interval = Number(cfg.signal_pull_interval_seconds || cfg.interval || cfg.interval_seconds || 1);
  const liveOnly = cfg.signal_pull_live_only !== false && cfg.live_only !== false;
  const label = (item && item.name) || "远端交易节点";
  const metaText = (item && item.metaText) || (url || "未配置 URL");
  return {
    kind: "signal",
    id: "remote-signal-source",
    label,
    title: label,
    typeText: "远端信号源",
    icon: "fa-satellite-dish",
    avatarClass: "signal",
    statusText: enabled ? "已接入" : "已暂停",
    statusTone: enabled ? "online" : "offline",
    enabled,
    normal: enabled,
    primaryLabel: "拉取节奏",
    primaryValue: `${interval}s / 次`,
    primaryMeta: `每次最多 ${Number.isFinite(limit) ? limit : 200} 条 · ${liveOnly ? "只拉滚球" : "滚球+早盘"}`,
    rows: [
      { icon: "fa-link", label: "信号地址", value: metaText, mono: true },
      { icon: "fa-filter", label: "拉取模式", value: liveOnly ? "只接滚球信号" : "接滚球和早盘信号" },
      { icon: "fa-clock", label: "最后信号", value: item && item.latestTs ? formatMonitorTimestamp(item.latestTs) : "等待远端推送" },
      { icon: "fa-triangle-exclamation", label: "失败原因", value: enabled ? "远端源状态由拉取进程记录，当前页面显示配置是否接入" : "已暂停，不会拉取远端信号", warning: !enabled },
    ],
    rawItem: item || {
      name: label,
      metaText,
      canDelete: true,
      deleteEndpoint: "/api/signal-pull-config/delete",
    },
  };
}

function buildMonitorPoolSources() {
  const sources = [];
  for (const account of Array.isArray(state.accounts) ? state.accounts : []) {
    const source = buildBackendMonitorSource(account);
    if (source) sources.push(source);
  }
  const signalItems = getSignalSourceSidebarItems();
  if (signalItems.length) {
    for (const item of signalItems) {
      const source = buildSignalMonitorSource(item);
      if (source) sources.push(source);
    }
  } else if (state.signalPullConfig && String(state.signalPullConfig.signal_pull_url || "").trim()) {
    const source = buildSignalMonitorSource(null);
    if (source) sources.push(source);
  }
  return sources;
}

function renderLegacyMonitorPoolStats(sources) {
  const list = Array.isArray(sources) ? sources : [];
  const backendCount = list.filter((s) => s.kind === "backend").length;
  const signalCount = list.filter((s) => s.kind === "signal").length;
  const normalCount = list.filter((s) => s.normal).length;
  const enabledBackendCount = list.filter((s) => s.kind === "backend" && s.enabled).length;
  const values = {
    "monitor-pool-count": list.length,
    "monitor-pool-normal-count": normalCount,
    "monitor-pool-error-count": Math.max(0, list.length - normalCount),
    "monitor-pool-backend-count": backendCount,
    "monitor-pool-signal-count": signalCount,
    "monitor-pool-concurrency": `${enabledBackendCount} / ${backendCount}`,
  };
  for (const [id, value] of Object.entries(values)) {
    const el = $(id);
    if (el) el.textContent = String(value);
  }
  const badge = $("badge-arsenal");
  if (badge) badge.textContent = String(list.length);
}

function renderLegacyMonitorSourceCard(source) {
  const article = document.createElement("article");
  const statusTone = String(source.statusTone || "offline").replace(/[^a-z0-9_-]/gi, "");
  const kind = String(source.kind || "backend").replace(/[^a-z0-9_-]/gi, "");
  const balanceTone = statusTone === "online" ? "ready" : (statusTone === "warning" ? "low" : "manual");
  const rows = (Array.isArray(source.rows) ? source.rows : []).map((row) => `
    <div class="arsenal-info-row${row.warning ? " is-multi-line" : ""}">
      <span class="arsenal-info-label"><i class="fa-solid ${escapeHtml(row.icon || "fa-circle-info")}"></i>${escapeHtml(row.label || "")}</span>
      <span class="arsenal-info-value ${row.mono ? "mono" : ""}" title="${escapeHtml(row.value || "")}">${escapeHtml(row.value || "-")}</span>
    </div>
  `).join("");
  article.className = `arsenal-member-card monitor-source-card kind-${kind} status-${statusTone || "offline"} eligibility-${source.enabled ? "ready" : "manual"}`;
  article.innerHTML = `
    <div class="arsenal-card-head">
      <div class="arsenal-member-main">
        <div class="arsenal-avatar ${escapeHtml(source.avatarClass || "")}"><i class="fa-solid ${escapeHtml(source.icon || "fa-tower-broadcast")}"></i></div>
        <div class="arsenal-title-wrap">
          <div class="arsenal-member-name" title="${escapeHtml(source.title || source.label)}">${escapeHtml(source.label || "-")}</div>
          <div class="arsenal-member-url" title="${escapeHtml(source.typeText || "")}">${escapeHtml(source.typeText || "")}</div>
          <div class="arsenal-status-meta">${escapeHtml(source.enabled ? "已启用扫描" : "已暂停扫描")}</div>
        </div>
      </div>
      <div class="arsenal-chip-stack">
        <div class="arsenal-status-chip ${statusTone || "offline"}">
          ${statusTone === "online" ? '<span class="pulse-win"></span>' : ""}
          ${escapeHtml(source.statusText || "未知")}
        </div>
        <div class="arsenal-eligibility-chip ${source.enabled ? "ready" : "manual"}">${escapeHtml(source.enabled ? "参与号池" : "暂停")}</div>
      </div>
    </div>

    <div class="arsenal-card-body">
      <div class="arsenal-balance-band ${balanceTone}">
        <div class="arsenal-balance-label">${escapeHtml(source.primaryLabel || "当前状态")}</div>
        <div class="arsenal-balance-value">${escapeHtml(source.primaryValue || "-")}</div>
        <div class="arsenal-balance-meta">${escapeHtml(source.primaryMeta || "")}</div>
      </div>
      ${rows}
    </div>

    <div class="arsenal-card-actions">
      <button class="btn btn-ghost btn-sm" data-action="refresh">
        <i class="fa-solid fa-arrows-rotate"></i> 刷新
      </button>
      <button class="btn btn-ghost btn-sm" data-action="toggle">
        <i class="fa-solid ${source.enabled ? "fa-pause" : "fa-play"}"></i> ${source.enabled ? "暂停" : "启用"}
      </button>
      ${source.kind === "backend" ? `
      <button class="btn btn-primary btn-sm" data-action="login">
        <i class="fa-solid fa-right-to-bracket"></i> 重登
      </button>` : ""}
      <button class="btn btn-danger btn-sm icon-only" data-action="delete" title="删除监控来源">
        <i class="fa-solid fa-trash"></i>
      </button>
    </div>
  `;
  article.querySelector("[data-action='refresh']")?.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (source.kind === "signal") await loadSignalPullConfig();
    else await loadStatuses();
    renderMonitorPool();
    toast("监控状态已刷新", "info");
  });
  article.querySelector("[data-action='toggle']")?.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (source.kind === "signal") await updateSignalPullEnabled(!source.enabled);
    else await updateAccountEnabled(source.id, source.label, !source.enabled);
  });
  article.querySelector("[data-action='login']")?.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    await reloginAccount(source.id, source.label);
    renderMonitorPool();
  });
  article.querySelector("[data-action='delete']")?.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    if (source.kind === "signal") {
      await deleteSignalSource(source.rawItem);
      renderMonitorPool();
      return;
    }
    if (confirm(`确定要删除监控目标 ${source.label} 吗？`)) {
      await deleteAccount(source.id, source.label);
      renderMonitorPool();
    }
  });
  return article;
}

function renderLegacyMonitorPool() {
  const grid = $("monitor-pool-cards");
  const empty = $("monitor-pool-empty");
  if (!grid || !empty) return;
  const sources = buildMonitorPoolSources();
  renderMonitorPoolStats(sources);
  grid.innerHTML = "";
  if (!sources.length) {
    empty.style.display = "block";
    renderPoolOverview();
    return;
  }
  empty.style.display = "none";
  if (state.poolSelection.kind !== "monitor" || !sources.some((source) => String(source.id) === state.poolSelection.id)) {
    state.poolSelection = { kind: "monitor", id: String(sources[0].id || "") };
  }
  for (const source of sources) {
    const card = renderMonitorSourceCard(source);
    card.classList.toggle("is-selected", state.poolSelection.kind === "monitor" && state.poolSelection.id === String(source.id || ""));
    card.addEventListener("click", (ev) => {
      if (ev.target.closest("button, select, input, a")) return;
      state.poolSelection = { kind: "monitor", id: String(source.id || "") };
      renderMonitorPool();
    });
    grid.appendChild(card);
  }
  renderPoolOverview(sources);
}

function renderLegacyPoolOverview(monitorSources = null) {
  const title = $("pool-overview-title");
  const status = $("pool-overview-status");
  const healthRing = $("pool-health-ring");
  const healthValue = $("pool-health-value");
  const healthLabel = $("pool-health-label");
  const healthMeta = $("pool-health-meta");
  const details = $("pool-overview-details");
  const alertCount = $("pool-alert-count");
  const alertList = $("pool-alert-list");
  if (!title || !status || !healthRing || !healthValue || !healthLabel || !healthMeta || !details || !alertCount || !alertList) return;

  const detailRows = [];
  const alerts = [];
  let label = "暂无号池";
  let statusText = "暂无状态";
  let tone = "muted";
  let total = 0;
  let normal = 0;

  if (state.poolView === "monitors") {
    const sources = Array.isArray(monitorSources) ? monitorSources : buildMonitorPoolSources();
    total = sources.length;
    normal = sources.filter((source) => source.normal).length;
    const selected = sources.find((source) => String(source.id) === String(state.poolSelection.id)) || sources[0];
    if (selected) {
      label = selected.label || "监控来源";
      statusText = selected.statusText || "未知";
      tone = selected.statusTone === "online" ? "success" : selected.statusTone === "warning" ? "warning" : "danger";
      detailRows.push(
        ["来源类型", selected.typeText || "--"],
        [selected.primaryLabel || "最近状态", selected.primaryValue || "--"],
        ["运行模式", selected.enabled ? "已启用" : "已暂停"],
      );
      for (const row of Array.isArray(selected.rows) ? selected.rows : []) {
        if (row.label === "失败原因") {
          if (row.warning) alerts.push(row.value || "监控来源异常");
          continue;
        }
        detailRows.push([row.label || "信息", row.value || "--"]);
      }
    }
  } else {
    const members = Array.isArray(state.members) ? state.members.filter(Boolean) : [];
    total = members.length;
    normal = members.filter((member) => String(member.status || "").toLowerCase() === "online").length;
    const selected = members.find((member) => String(member.id) === String(state.poolSelection.id)) || members[0];
    if (selected) {
      const mappedTarget = String(state.mappings[selected.id] || "");
      const mappedAccount = state.accounts.find((account) => String(account.name || "") === mappedTarget);
      const helper = window.HDArsenalCards && window.HDArsenalCards.buildArsenalCardModel;
      const model = helper ? helper(selected, { mappingTarget: mappedTarget, mappingLabel: mappedAccount ? accountLabel(mappedAccount) : "" }) : null;
      label = memberLabel(selected);
      statusText = model ? model.statusText : String(selected.status || "未知");
      tone = String(selected.status || "").toLowerCase() === "online" ? "success" : String(selected.status || "").toLowerCase() === "relogin" ? "warning" : "danger";
      detailRows.push(
        ["平台", model ? model.platformHost : String(selected.base_url || "--")],
        ["可用额度", model ? model.balanceText : String(selected.balance || 0)],
        ["账号用途", model ? model.usageText : "--"],
        ["跟单映射", model ? model.mappingText : "全部跟单"],
        ["登录通道", model ? model.providerText : "--"],
        ["代理 / 地区", model ? `${model.proxyText} · ${model.geoText}` : "--"],
      );
      if (model && model.lastError) alerts.push(model.lastError);
    }
  }

  const percent = total > 0 ? Math.round((normal / total) * 100) : null;
  title.textContent = label;
  status.dataset.tone = tone;
  status.querySelector("b").textContent = statusText;
  healthRing.style.setProperty("--health", String(percent == null ? 0 : percent));
  healthValue.textContent = percent == null ? "--" : `${percent}%`;
  healthLabel.textContent = percent == null ? "暂无数据" : percent >= 80 ? "运行健康" : percent >= 50 ? "需要关注" : "存在异常";
  healthMeta.textContent = total ? `${normal} 个正常 / ${total} 个总计` : "等待号池数据加载";
  details.innerHTML = detailRows.length
    ? detailRows.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`).join("")
    : "<div><dt>状态</dt><dd>--</dd></div>";
  alertCount.textContent = `${alerts.length} 条`;
  alertList.innerHTML = alerts.length
    ? alerts.map((message) => `<div class="pool-alert-item"><i class="fa-solid fa-triangle-exclamation"></i><span>${escapeHtml(message)}</span></div>`).join("")
    : '<div class="pool-alert-empty"><i class="fa-solid fa-circle-check"></i>当前没有异常告警</div>';
}

function poolPercent(value, total) {
  return total > 0 ? Math.round((Number(value || 0) / total) * 100) : 0;
}

function poolUptimeText(seconds) {
  const total = Math.max(0, Number(seconds || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return days > 0 ? `${days} 天 ${hours} 小时 ${minutes} 分` : `${hours} 小时 ${minutes} 分`;
}

function poolTodayOrderCount() {
  const now = new Date();
  return (Array.isArray(state.orders) ? state.orders : []).filter((order) => {
    const ts = Number(order && (order.ts || order.bet_ts) || 0);
    if (!ts) return false;
    const date = new Date(ts * 1000);
    return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  }).length;
}

function renderMonitorPoolStats(sources) {
  const list = Array.isArray(sources) ? sources : [];
  const backendCount = list.filter((source) => source.kind === "backend").length;
  const signalCount = list.filter((source) => source.kind === "signal").length;
  const normalCount = list.filter((source) => source.normal).length;
  const errorCount = Math.max(0, list.length - normalCount);
  const enabledBackendCount = list.filter((source) => source.kind === "backend" && source.enabled).length;
  const values = {
    "monitor-pool-count": list.length,
    "monitor-pool-normal-count": normalCount,
    "monitor-pool-error-count": errorCount,
    "monitor-pool-concurrency": `${enabledBackendCount} / ${backendCount}`,
    "pool-today-order-count": poolTodayOrderCount(),
  };
  Object.entries(values).forEach(([id, value]) => {
    if ($(id)) $(id).textContent = String(value);
  });
  if ($("monitor-pool-total-meta")) $("monitor-pool-total-meta").textContent = `${backendCount} 后台 / ${signalCount} 信号`;
  if ($("monitor-pool-normal-meta")) $("monitor-pool-normal-meta").textContent = `活跃率 ${poolPercent(normalCount, list.length)}%`;
  if ($("monitor-pool-error-meta")) $("monitor-pool-error-meta").textContent = `异常占比 ${poolPercent(errorCount, list.length)}%`;
  if ($("monitor-pool-concurrency-meta")) $("monitor-pool-concurrency-meta").textContent = backendCount ? "启用后台 / 后台总数" : "暂无后台监控通道";
  if ($("pool-today-order-meta")) $("pool-today-order-meta").textContent = `实时流已加载 ${state.orders.length || 0} 条`;
  document.querySelectorAll("[data-pool-kpi-filter]").forEach((button) => {
    button.classList.toggle("is-active", button.getAttribute("data-pool-kpi-filter") === String(state.poolDashboard.status || "all"));
  });
  const badge = $("badge-arsenal");
  if (badge) badge.textContent = String(list.length);
}

function filterMonitorPoolSources(sources) {
  const dashboard = state.poolDashboard || {};
  const query = String(dashboard.search || "").trim().toLowerCase();
  return (Array.isArray(sources) ? sources : []).filter((source) => {
    if (dashboard.source && dashboard.source !== "all" && source.kind !== dashboard.source) return false;
    if (dashboard.status === "online" && !source.normal) return false;
    if (dashboard.status === "error" && (source.normal || !source.enabled)) return false;
    if (dashboard.status === "paused" && source.enabled) return false;
    if (dashboard.channel && dashboard.channel !== "all") {
      const channel = source.kind === "signal" ? "signal" : String(monitorAccountLoginTab(source.rawAccount));
      if (channel !== String(dashboard.channel)) return false;
    }
    if (query) {
      const haystack = [source.label, source.title, source.typeText, source.statusText, source.primaryValue]
        .concat((source.rows || []).flatMap((row) => [row.label, row.value]))
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
}

function monitorRowValue(source, labels, fallback = "-") {
  const row = (Array.isArray(source.rows) ? source.rows : []).find((item) => labels.includes(String(item.label || "")));
  return row ? String(row.value || fallback) : fallback;
}

function renderMonitorSourceCard(source) {
  const article = document.createElement("article");
  const statusTone = String(source.statusTone || "offline").replace(/[^a-z0-9_-]/gi, "");
  const kind = String(source.kind || "backend").replace(/[^a-z0-9_-]/gi, "");
  const scanScope = monitorRowValue(source, ["监控范围", "拉取模式"], source.kind === "signal" ? "远端信号" : "全部订单");
  const proxy = monitorRowValue(source, ["代理", "信号地址"], "未配置");
  const runState = monitorRowValue(source, ["运行状态", "最后信号"], source.statusText || "未知");
  article.className = `pool-monitor-card kind-${kind} status-${statusTone || "offline"}`;
  article.classList.toggle("is-selected", state.poolSelection.kind === "monitor" && state.poolSelection.id === String(source.id || ""));
  article.tabIndex = 0;
  article.setAttribute("aria-label", `${source.label || "监控来源"}，${source.statusText || "未知状态"}`);
  article.innerHTML = `
    <header class="pool-monitor-card-head">
      <span class="pool-monitor-signal"><i class="fa-solid ${escapeHtml(source.icon || "fa-tower-broadcast")}" aria-hidden="true"></i></span>
      <span class="pool-monitor-name"><b title="${escapeHtml(source.title || source.label)}">${escapeHtml(source.label || "-")}</b><small>${escapeHtml(source.typeText || "监控来源")}</small></span>
      <button class="pool-monitor-status ${statusTone || "offline"}" type="button" data-action="focus" title="查看状态详情"><i aria-hidden="true"></i>${escapeHtml(source.statusText || "未知")}</button>
    </header>
    <div class="pool-monitor-time">
      <small>${escapeHtml(source.primaryLabel || "最近扫描")}</small>
      <strong>${escapeHtml(source.primaryValue || "-")}</strong>
      <span title="${escapeHtml(source.primaryMeta || "")}">${escapeHtml(source.primaryMeta || "暂无成功记录")}</span>
    </div>
    <dl class="pool-monitor-facts">
      <div><dt><i class="fa-solid fa-list-check" aria-hidden="true"></i>监控范围</dt><dd title="${escapeHtml(scanScope)}">${escapeHtml(scanScope)}</dd></div>
      <div><dt><i class="fa-solid fa-network-wired" aria-hidden="true"></i>${source.kind === "signal" ? "信号地址" : "代理通道"}</dt><dd title="${escapeHtml(proxy)}">${escapeHtml(proxy)}</dd></div>
      <div><dt><i class="fa-solid fa-wave-square" aria-hidden="true"></i>运行状态</dt><dd title="${escapeHtml(runState)}">${escapeHtml(runState)}</dd></div>
    </dl>
    <footer class="pool-monitor-actions">
      <button type="button" data-action="refresh" title="刷新状态" aria-label="刷新 ${escapeHtml(source.label)} 状态"><i class="fa-solid fa-arrows-rotate" aria-hidden="true"></i></button>
      <button type="button" data-action="toggle" title="${source.enabled ? "暂停" : "启用"}" aria-label="${source.enabled ? "暂停" : "启用"} ${escapeHtml(source.label)}"><i class="fa-solid ${source.enabled ? "fa-pause" : "fa-play"}" aria-hidden="true"></i></button>
      ${source.kind === "backend" ? `<button class="primary" type="button" data-action="login" title="重新登录" aria-label="重新登录 ${escapeHtml(source.label)}"><i class="fa-solid fa-rotate" aria-hidden="true"></i><span>重登</span></button>` : ""}
      <button class="danger" type="button" data-action="delete" title="删除监控来源" aria-label="删除 ${escapeHtml(source.label)}"><i class="fa-solid fa-trash" aria-hidden="true"></i></button>
    </footer>`;

  const focusSource = () => {
    state.poolSelection = { kind: "monitor", id: String(source.id || "") };
    document.querySelectorAll(".pool-monitor-card.is-selected").forEach((item) => item.classList.remove("is-selected"));
    article.classList.add("is-selected");
    renderPoolOverview();
  };
  article.querySelector("[data-action='focus']")?.addEventListener("click", (event) => { event.stopPropagation(); focusSource(); });
  article.addEventListener("click", (event) => {
    if (event.target.closest("button, select, input, a")) return;
    focusSource();
  });
  article.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      focusSource();
    }
  });
  article.querySelector("[data-action='refresh']")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (source.kind === "signal") await loadSignalPullConfig();
    else await loadStatuses();
    renderPoolManagement();
    toast("监控状态已刷新", "info");
  });
  article.querySelector("[data-action='toggle']")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (source.kind === "signal") await updateSignalPullEnabled(!source.enabled);
    else await updateAccountEnabled(source.id, source.label, !source.enabled);
  });
  article.querySelector("[data-action='login']")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    await reloginAccount(source.id, source.label);
    renderPoolManagement();
  });
  article.querySelector("[data-action='delete']")?.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (source.kind === "signal") {
      await deleteSignalSource(source.rawItem);
    } else if (confirm(`确定要删除监控目标 ${source.label} 吗？`)) {
      await deleteAccount(source.id, source.label);
    }
    renderPoolManagement();
  });
  return article;
}

function renderMonitorPool() {
  const grid = $("monitor-pool-cards");
  const empty = $("monitor-pool-empty");
  if (!grid || !empty) return;
  const sources = buildMonitorPoolSources();
  renderMonitorPoolStats(sources);
  const filtered = filterMonitorPoolSources(sources);

  grid.innerHTML = "";
  filtered.forEach((source) => grid.appendChild(renderMonitorSourceCard(source)));
  grid.hidden = filtered.length === 0;
  empty.hidden = filtered.length > 0;
  renderPoolOverview(sources);
}

function renderPoolTrendChart() {
  const host = $("pool-trend-chart");
  if (!host) return;
  const now = new Date();
  const days = [];
  for (let offset = 6; offset >= 0; offset -= 1) {
    const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() - offset);
    days.push({
      date,
      key: `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`,
      label: `${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`,
      count: 0,
    });
  }
  const dayMap = new Map(days.map((item) => [item.key, item]));
  (Array.isArray(state.orders) ? state.orders : []).forEach((order) => {
    const ts = Number(order && (order.ts || order.bet_ts) || 0);
    if (!ts) return;
    const date = new Date(ts * 1000);
    const item = dayMap.get(`${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`);
    if (item) item.count += 1;
  });
  const max = Math.max(1, ...days.map((item) => item.count));
  const points = days.map((item, index) => ({ x: 18 + index * 48, y: 67 - (item.count / max) * 44, ...item }));
  const line = points.map((point) => `${point.x},${point.y}`).join(" ");
  const area = `18,72 ${line} ${points[points.length - 1].x},72`;
  const total = days.reduce((sum, item) => sum + item.count, 0);
  host.setAttribute("aria-label", `近 7 日真实订单共 ${total} 条，${days.map((item) => `${item.label} ${item.count} 条`).join("，")}`);
  host.innerHTML = `
    <svg viewBox="0 0 324 92" role="presentation" aria-hidden="true" preserveAspectRatio="none">
      <line x1="18" y1="23" x2="306" y2="23" class="grid-line"></line>
      <line x1="18" y1="45" x2="306" y2="45" class="grid-line"></line>
      <line x1="18" y1="67" x2="306" y2="67" class="grid-line"></line>
      <polygon points="${area}" class="trend-area"></polygon>
      <polyline points="${line}" class="trend-line"></polyline>
      ${points.map((point) => `<circle cx="${point.x}" cy="${point.y}" r="2.4"><title>${point.label}: ${point.count} 条</title></circle>`).join("")}
      ${points.map((point) => `<text x="${point.x}" y="86">${point.label.slice(3)}</text>`).join("")}
    </svg>`;
}

function renderPoolSourceDistribution(sources) {
  const donut = $("pool-source-donut");
  const legend = $("pool-source-legend");
  if (!donut || !legend) return;
  const list = Array.isArray(sources) ? sources : [];
  const backend = list.filter((source) => source.kind === "backend").length;
  const signal = list.filter((source) => source.kind === "signal").length;
  const total = backend + signal;
  const backendPercent = poolPercent(backend, total);
  donut.style.setProperty("--backend-share", String(backendPercent));
  donut.dataset.empty = total ? "false" : "true";
  donut.innerHTML = `<span><b>${total}</b><small>来源</small></span>`;
  const active = String(state.poolDashboard.source || "all");
  legend.innerHTML = [
    ["backend", "后台监控", backend, "cyan"],
    ["signal", "远端信号", signal, "lime"],
  ].map(([kind, label, count, tone]) => `<button type="button" data-source="${kind}" class="${active === kind ? "is-active" : ""}"><i data-tone="${tone}" aria-hidden="true"></i><span>${label}</span><b>${count}</b></button>`).join("");
}

function renderPoolOverview(monitorSources = null) {
  const sources = Array.isArray(monitorSources) ? monitorSources : buildMonitorPoolSources();
  const members = Array.isArray(state.members) ? state.members.filter(Boolean) : [];
  const memberStats = computeArsenalStats(members);
  const total = sources.length;
  const normal = sources.filter((source) => source.normal).length;
  const health = total > 0 ? poolPercent(normal, total) : 0;
  const status = $("pool-overview-status");
  const tone = !total ? "muted" : health >= 80 ? "success" : health >= 50 ? "warning" : "danger";
  const statusText = !total ? "暂无来源" : health === 100 ? "扫描正常" : health >= 50 ? "部分异常" : "扫描异常";
  if (status) {
    status.dataset.tone = tone;
    const label = status.querySelector("b");
    if (label) label.textContent = statusText;
  }

  const ring = $("pool-health-ring");
  const miniRing = $("pool-mini-health");
  [ring, miniRing].filter(Boolean).forEach((element) => element.style.setProperty("--health", String(health)));
  if ($("pool-health-value")) $("pool-health-value").textContent = total ? `${health}%` : "--";
  if ($("pool-mini-health-value")) $("pool-mini-health-value").textContent = total ? `${health}%` : "--";
  const healthLabel = !total ? "暂无数据" : health >= 80 ? "运行健康" : health >= 50 ? "需要关注" : "存在异常";
  if ($("pool-health-label")) $("pool-health-label").textContent = healthLabel;
  if ($("pool-mini-health-label")) $("pool-mini-health-label").textContent = healthLabel;
  if ($("pool-health-meta")) $("pool-health-meta").textContent = total ? `${normal} 个正常 / ${total} 个总计` : "等待真实监控来源接入";
  if ($("pool-scan-time")) {
    const timeText = state.systemStatus && state.systemStatus.serverTime ? state.systemStatus.serverTime.split(" ").pop() : "等待同步";
    $("pool-scan-time").textContent = timeText;
  }

  const focusSource = state.poolSelection.kind === "monitor"
    ? sources.find((source) => String(source.id || "") === String(state.poolSelection.id || ""))
    : null;
  const focusMember = state.poolSelection.kind === "member"
    ? members.find((member) => String(member.id || "") === String(state.poolSelection.id || ""))
    : null;
  const latestPoll = Math.max(0, ...sources.map((source) => Number(source.rawAccount && state.statuses[source.id] && state.statuses[source.id].last_poll_at || 0)));
  const latestOk = Math.max(0, ...sources.map((source) => Number(source.rawAccount && state.statuses[source.id] && state.statuses[source.id].last_ok_at || 0)));
  const focusLabel = focusSource ? focusSource.label : focusMember ? memberLabel(focusMember) : "未选择";
  const details = [
    ["当前聚焦", focusLabel],
    ["最后扫描时间", latestPoll ? fmtTime(latestPoll) : "暂无记录"],
    ["最后成功时间", latestOk ? fmtTime(latestOk) : "暂无记录"],
    ["监控 / 在线", `${total} / ${normal}`],
    ["会员 / 在线", `${memberStats.total || 0} / ${memberStats.online || 0}`],
    ["运行时长", poolUptimeText(state.systemStatus && state.systemStatus.uptime)],
  ];
  const detailsHost = $("pool-overview-details");
  if (detailsHost) detailsHost.innerHTML = details.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`).join("");

  const events = [];
  sources.forEach((source) => {
    const sourceStatus = state.statuses[source.id] || {};
    const ts = Number(sourceStatus.last_poll_at || sourceStatus.last_ok_at || 0);
    if (ts) events.push({ ts, label: source.normal ? `${source.label} 扫描成功` : `${source.label} 状态异常`, tone: source.normal ? "success" : "danger" });
  });
  members.forEach((member) => {
    const ts = Number(member.last_check_at || member.last_ok_at || 0);
    if (ts) events.push({ ts, label: `${memberLabel(member)} 状态更新`, tone: String(member.status || "").toLowerCase() === "online" ? "success" : "warning" });
  });
  events.sort((a, b) => b.ts - a.ts);
  const recentHost = $("pool-recent-events");
  if (recentHost) {
    recentHost.innerHTML = events.length
      ? events.slice(0, 4).map((event) => `<li data-tone="${event.tone}"><i aria-hidden="true"></i><time>${escapeHtml(new Date(event.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }))}</time><span title="${escapeHtml(event.label)}">${escapeHtml(event.label)}</span></li>`).join("")
      : '<li><i aria-hidden="true"></i><time>--:--:--</time><span>暂无真实运行事件</span></li>';
  }

  const alerts = [];
  sources.forEach((source) => {
    if (source.normal) return;
    const issue = monitorRowValue(source, ["失败原因", "运行状态"], source.statusText || "监控异常");
    alerts.push({ kind: "monitor", id: source.id, label: source.label, message: issue, tone: source.enabled ? "danger" : "warning" });
  });
  members.forEach((member) => {
    const statusValue = String(member.status || "").toLowerCase();
    if (statusValue === "online" && !String(member.last_error || "").trim()) return;
    alerts.push({ kind: "member", id: member.id, label: memberLabel(member), message: String(member.last_error || (statusValue === "relogin" ? "会员正在自动恢复" : "会员当前离线")), tone: statusValue === "relogin" ? "warning" : "danger" });
  });
  const alertHost = $("pool-alert-list");
  if ($("pool-alert-count")) $("pool-alert-count").textContent = `${alerts.length} 条`;
  if (alertHost) {
    alertHost.innerHTML = alerts.length
      ? alerts.map((alert) => `<button type="button" class="pool-alert-item" data-kind="${escapeHtml(alert.kind)}" data-id="${escapeHtml(alert.id)}" data-tone="${escapeHtml(alert.tone)}"><i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i><span><b>${escapeHtml(alert.label)}</b><small title="${escapeHtml(alert.message)}">${escapeHtml(alert.message)}</small></span><i class="fa-solid fa-chevron-right" aria-hidden="true"></i></button>`).join("")
      : '<div class="pool-alert-empty"><i class="fa-solid fa-circle-check" aria-hidden="true"></i>当前没有异常告警</div>';
    alertHost.querySelectorAll("button[data-kind][data-id]").forEach((button) => {
      button.addEventListener("click", () => {
        state.poolSelection = { kind: button.dataset.kind, id: button.dataset.id };
        renderPoolOverview(sources);
        setPoolView(button.dataset.kind === "member" ? "members" : "monitors");
      });
    });
  }

  renderPoolTrendChart();
  renderPoolSourceDistribution(sources);
}

async function updateAccountEnabled(name, label, enabled) {
  if (!name) return;
  try {
    const data = await fetchJson("/api/accounts/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, patch: { enabled: !!enabled } }),
    });
    if (!data.ok) throw new Error(data.message || "update_failed");
    await loadAccounts();
    await loadStatuses();
    renderMonitorPool();
    toast(`${enabled ? "已启用" : "已暂停"}监控: ${label || name}`, "success");
  } catch (e) {
    toast(`切换监控状态失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

async function updateSignalPullEnabled(enabled) {
  const cfg = state.signalPullConfig || {};
  const url = String(cfg.signal_pull_url || cfg.url || "").trim();
  if (!url) {
    toast("远端信号源还没有配置 URL", "warning");
    return;
  }
  try {
    const payload = {
      signal_pull_enabled: !!enabled,
      signal_pull_url: url,
      signal_pull_limit: Number(cfg.signal_pull_limit || cfg.limit || 200),
      signal_pull_interval_seconds: Number(cfg.signal_pull_interval_seconds || cfg.interval || cfg.interval_seconds || 1),
      signal_pull_live_only: cfg.signal_pull_live_only !== false && cfg.live_only !== false,
    };
    const token = String(cfg.signal_pull_token || cfg.token || "").trim();
    if (token) payload.signal_pull_token = token;
    const data = await fetchJson("/api/signal-pull-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.ok) throw new Error(data.message || "update_signal_failed");
    state.signalPullConfig = normalizeSignalPullConfig(data);
    renderAccountsSidebar();
    renderAccountSelect();
    renderTradingNodeKpi();
    renderMonitorPool();
    toast(`${enabled ? "已启用" : "已暂停"}远端信号源`, "success");
  } catch (e) {
    toast(`切换远端信号源失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

async function requestArsenalLogin(id) {
  return fetchJson("/api/arsenal/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

async function loginArsenalMember(id, label) {
  try {
    await requestArsenalLogin(id);
    await loadArsenal();
    renderArsenalTable();
    toast(`已发起会员登录: ${label || id}`, "info");
  } catch (e) { toast(`登录失败: ${String(e && e.message ? e.message : e)}`, "error"); }
}

async function saveMapping(mid, target) {
  try {
    await fetchJson("/api/mapping", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ member_id: mid, target }),
    });
    state.mappings[mid] = target;
  } catch (e) { toast("保存映射失败: " + e.message, "error"); }
}

async function saveMemberUsage(id, usage) {
  try {
    await fetchJson("/api/arsenal/update", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, usage }),
    });
    await loadArsenal();
    renderArsenalTable();
    toast(`已更新账号用途: ${usage === "read" ? "拉数据用" : "跟单用"}`, "success");
  } catch (e) {
    toast(`保存账号用途失败: ${String(e && e.message ? e.message : e)}`, "error");
  }
}

async function requestArsenalCheck(id) {
  return fetchJson("/api/arsenal/check", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
}

async function checkMember(id) {
  try {
    await requestArsenalCheck(id);
    await loadArsenal();
    renderArsenalTable();
    toast("会员状态检测完成", "info");
  } catch (e) { toast(`检测失败: ${String(e)}`, "error"); }
}


function setMemberHint(message, status = "") {
  const hint = $("mem-hint");
  if (!hint) return;
  hint.textContent = message || "";
  hint.className = status ? `form-hint ${status}` : "form-hint";
}

function formatProxyTestResult(data) {
  const parts = ["代理可用"];
  if (data.test_url) parts.push(`测试: ${String(data.test_url)}`);
  if (data.elapsed_ms != null) parts.push(`耗时: ${String(data.elapsed_ms)}ms`);
  if (data.ip) {
    const geoParts = [];
    if (data.geo?.ok && data.geo.iso) geoParts.push(String(data.geo.iso).trim());
    if (data.geo?.ok && data.geo.isp) geoParts.push(String(data.geo.isp).trim());
    parts.push(`出口IP: ${String(data.ip)}${geoParts.length ? ` (${geoParts.join(" ")})` : ""}`);
  }
  if (data.proxy_ip) {
    const proxyGeoParts = [];
    if (data.proxy_geo?.ok && data.proxy_geo.iso) proxyGeoParts.push(String(data.proxy_geo.iso).trim());
    if (data.proxy_geo?.ok && data.proxy_geo.isp) proxyGeoParts.push(String(data.proxy_geo.isp).trim());
    parts.push(`代理节点: ${String(data.proxy_ip)}${proxyGeoParts.length ? ` (${proxyGeoParts.join(" ")})` : ""}`);
  }
  return parts.join(" | ");
}

async function testMemberProxy() {
  const proxy = ($("mem-proxy")?.value || "").trim();
  const baseUrl = ($("mem-base-url")?.value || "").trim();
  if (!proxy) {
    setMemberHint("请先填写代理（Proxy）。", "err");
    toast("请先填写代理", "warning");
    return;
  }

  const btn = $("mem-proxy-test");
  const label = $("mem-proxy-test-label");
  const icon = btn?.querySelector("i");
  if (btn) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
  }
  if (label) label.textContent = "测试中...";
  if (icon) icon.className = "fa-solid fa-spinner";
  setMemberHint("正在测试代理...", "");

  try {
    const payload = { proxy, timeout_s: 10 };
    if (baseUrl) payload.base_url = baseUrl;
    const data = await fetchJson("/api/proxy-test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.ok) {
      const message = data.message || data.error || "proxy_test_failed";
      setMemberHint(`代理不可用: ${message}`, "err");
      toast(`代理不可用: ${message}`, "error");
      return;
    }
    const message = formatProxyTestResult(data);
    setMemberHint(message, "ok");
    toast("代理可用", "success");
  } catch (e) {
    const message = e?.message || String(e);
    setMemberHint(`代理测试失败: ${message}`, "err");
    toast(`代理测试失败: ${message}`, "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.setAttribute("aria-busy", "false");
    }
    if (label) label.textContent = "测试代理";
    if (icon) icon.className = "fa-solid fa-plug";
  }
}

async function deleteMember(id, label) {
  if (!confirm(`确定删除会员: ${label} ?`)) return;
  try {
    await fetchJson("/api/arsenal/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    await loadArsenal();
    renderArsenalTable();
    toast(`已删除会员: ${label}`, "success");
  } catch (e) { toast(`删除失败: ${String(e)}`, "error"); }
}

async function submitMember() {
  const baseUrl   = $("mem-base-url")?.value.trim();
  const username  = $("mem-username")?.value.trim();
  const password  = $("mem-password")?.value.trim();
  const proxy     = ($("mem-proxy")?.value || "").trim();
  const usage     = ($("mem-usage")?.value || "bet").trim() || "bet";
  if (!baseUrl || !username || !password) {
    setMemberHint("请填写所有必填字段", "err");
    if (!baseUrl) $("mem-base-url")?.focus();
    else if (!username) $("mem-username")?.focus();
    else $("mem-password")?.focus();
    return;
  }
  const btn = $("mem-submit");
  const label = $("mem-submit-label");
  const icon = btn?.querySelector("i");
  if (btn) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
  }
  if (label) label.textContent = "添加中...";
  if (icon) icon.className = "fa-solid fa-spinner";
  try {
    await fetchJson("/api/arsenal", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ base_url: baseUrl, username, password, proxy, usage }),
    });
    closeMemberModal();
    await loadArsenal();
    renderArsenalTable();
    toast(`会员 ${username} 已添加`, "success");
  } catch (e) {
    setMemberHint(`添加失败：${e?.message || String(e)}`, "err");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.setAttribute("aria-busy", "false");
    }
    if (label) label.textContent = "添加会员";
    if (icon) icon.className = "fa-solid fa-user-plus";
  }
}

// ── Follow settings ────────────────────────────────────────────
function beginFollowFormDomRender(options = {}) {
  if (typeof followFormTools.beginFollowFormRender === "function") {
    return followFormTools.beginFollowFormRender(followFormRenderGuard, options);
  }
  if (followFormRenderGuard.dirty && !options.force) return false;
  followFormRenderGuard.rendering = true;
  return true;
}

function finishFollowFormDomRender() {
  if (typeof followFormTools.finishFollowFormRender === "function") {
    followFormTools.finishFollowFormRender(followFormRenderGuard);
    return;
  }
  followFormRenderGuard.rendering = false;
}

function markFollowFormDirty() {
  if (typeof followFormTools.markFollowFormDirty === "function") {
    followFormTools.markFollowFormDirty(followFormRenderGuard);
    renderFollowFormStatus();
    return;
  }
  if (!followFormRenderGuard.rendering) followFormRenderGuard.dirty = true;
  renderFollowFormStatus();
}

function markFollowFormClean() {
  if (typeof followFormTools.markFollowFormClean === "function") {
    followFormTools.markFollowFormClean(followFormRenderGuard);
    renderFollowFormStatus();
    return;
  }
  followFormRenderGuard.dirty = false;
  renderFollowFormStatus();
}

function renderFollowFormStatus() {
  const updatedAt = String((state.followSettings && state.followSettings.updated_at) || "-");
  const model = typeof followFormTools.buildFollowFormStatusModel === "function"
    ? followFormTools.buildFollowFormStatusModel({
        updatedAt,
        dirty: !!followFormRenderGuard.dirty,
        saving: !!followFormUiState.saving,
      })
    : {
        updatedText: updatedAt,
        saveLabel: "保存策略",
        saveDisabled: !!followFormUiState.saving,
      };
  const updatedEl = $("follow-updated");
  if (updatedEl) updatedEl.textContent = String(model.updatedText || "-");
  const saveBtn = $("follow-save");
  if (saveBtn) {
    saveBtn.disabled = !!model.saveDisabled;
    saveBtn.innerHTML = `<i class="fa-solid fa-floppy-disk"></i>${escapeHtml(String(model.saveLabel || "保存策略"))}`;
  }
}

function marketRuleToggleLabel(enabled) {
  if (typeof followFormTools.getMarketRuleToggleLabel === "function") {
    return followFormTools.getMarketRuleToggleLabel(enabled);
  }
  return enabled ? "启用" : "禁跟";
}

function marketRuleToggleTitle(name, enabled) {
  if (typeof followFormTools.getMarketRuleToggleTitle === "function") {
    return followFormTools.getMarketRuleToggleTitle(name, enabled);
  }
  return enabled ? `${name}当前启用，点击后设为禁跟` : `${name}当前禁跟，点击后恢复启用`;
}

function bindFollowFormDirtyTracking() {
  [
    "follow-mode",
    "follow-fixed",
    "follow-ratio",
    "follow-max",
    "follow-match-limit",
    "follow-market-limit",
    "follow-min-odds",
    "follow-max-odds",
    "follow-split-count",
    "follow-split-delay",
  ].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", markFollowFormDirty);
    el.addEventListener("change", markFollowFormDirty);
  });
}

async function loadFollowSettings() {
  const [data] = await Promise.all([
    fetchJson("/api/follow-settings"),
    loadTargets(),
  ]);
  state.followSettings = data.settings || null;
  renderFollowFormStatus();
}

async function loadTargets() {
  try {
    const data = await fetchJson("/api/targets");
    state.availableTargets = Array.isArray(data.targets) ? data.targets : [];
  } catch {
    state.availableTargets = [];
  }
  return state.availableTargets;
}

async function loadFollowResults() {
  const data = await fetchJson("/api/follow-results?limit=80");
  return data.results || [];
}

function renderFollowResults(rows) {
  const tbody = $("follow-results-tbody");
  const empty = $("follow-results-empty");
  if (!tbody || !empty) return;
  tbody.innerHTML = "";
  if (!rows || !rows.length) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  for (const r of rows) {
    const ok = !!r.ok;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono" style="font-size:10px">${escapeHtml(fmtIso(r.ts || 0))}</td>
      <td>${escapeHtml(r.member || "-")}</td>
      <td>${escapeHtml(r.target || "-")}</td>
      <td class="mono stat-number">${escapeHtml(String(r.stake ?? "-"))}</td>
      <td><span class="${ok ? 'text-green' : 'text-red'}">${ok ? "✓ 成功" : "✗ 失败"}</span></td>
      <td class="mono" style="font-size:10px">${escapeHtml(r.ticket_id || "")}</td>
    `;
    tbody.appendChild(tr);
  }
  if ($("follow-results-updated")) $("follow-results-updated").textContent = new Date().toLocaleTimeString();
}

// ── Follow form — full render ──────────────────────────────────
function renderFollowForm(options = {}) {
  const s = state.followSettings;
  if (!s) return;
  if (!beginFollowFormDomRender(options)) return;

  try {
    // 基础字段
    setFollowModeValue(s.mode);
    if ($("follow-fixed"))       $("follow-fixed").value       = String(s.fixed_amount ?? 0);
    if ($("follow-ratio"))       $("follow-ratio").value       = String(s.ratio_percent ?? 0);
    if ($("follow-max"))         $("follow-max").value         = String(s.max_amount ?? 0);
    if ($("follow-match-limit")) $("follow-match-limit").value = String(s.match_limit_amount ?? 0);
    if ($("follow-market-limit")) $("follow-market-limit").value = String(s.market_limit_amount ?? 0);
    if ($("follow-split-count")) $("follow-split-count").value = String(s.split_count ?? 1);
    setFollowSplitDelayValue(s.split_delay_seconds);
    applyFollowMode();

    // 反单规则
    const rr = s.reverse_rules || {};
    setRevBtn("rev-all",      !!rr.all);
    setRevBtn("rev-total",    !!rr.total);
    setRevBtn("rev-handicap", !!rr.handicap);

    const minOdds = $("follow-min-odds");
    if (minOdds) minOdds.value = s.follow_min_odds == null ? "" : String(s.follow_min_odds);
    const maxOdds = $("follow-max-odds");
    if (maxOdds) maxOdds.value = s.follow_max_odds == null ? "" : String(s.follow_max_odds);

    // 玩法比例
    renderMarketRules(s.market_rules || {});

    // 后台过滤
    renderTargetFilters(s.targets || []);
    renderTargetRatios(s.targets || []);
    renderFollowFormStatus();
  } finally {
    finishFollowFormDomRender();
  }
}

function setRevBtn(id, active) {
  const btn = $(id);
  if (!btn) return;
  btn.classList.toggle("active", active);
  btn.setAttribute("aria-pressed", active ? "true" : "false");
  const stateEl = btn.querySelector(".rev-btn-state");
  if (stateEl) stateEl.textContent = active ? "已开启" : "关闭";
}

// 玩法比例渲染
const MARKET_LABELS = {
  handicap:    { name: "让球", color: "#3B82F6" },
  total_over:  { name: "大球", color: "#10B981" },
  total_under: { name: "小球", color: "#F97316" },
  moneyline:   { name: "独赢", color: "#A855F7" },
};

function renderMarketRules(mr) {
  const container = $("market-rules-container");
  if (!container) return;
  container.innerHTML = "";
  for (const [key, meta] of Object.entries(MARKET_LABELS)) {
    const rule = (mr && mr[key]) || { enabled: true, ratio_percent: 100 };
    const enabled = rule.enabled !== false && normalizeTargetRatioPercent(rule.ratio_percent) !== 0;
    const ratio   = Number(rule.ratio_percent ?? 100);
    const badgeStyle = enabled
      ? `background:${escapeHtml(meta.color)}18;color:${escapeHtml(meta.color)};border:1px solid ${escapeHtml(meta.color)}40`
      : "";
    const row = document.createElement("div");
    row.className = `market-rule-row ${enabled ? "market-rule-enabled" : "market-rule-disabled"}`;
    row.dataset.key = key;
    row.innerHTML = `
      <div class="market-rule-badge"${badgeStyle ? ` style="${badgeStyle}"` : ""}>
        ${escapeHtml(meta.name)}
      </div>
      <button type="button" class="market-toggle-btn" aria-pressed="${enabled ? "true" : "false"}" title="${escapeHtml(marketRuleToggleTitle(meta.name, enabled))}">${marketRuleToggleLabel(enabled)}</button>
      <div class="market-rule-stepper">
        <button type="button" class="step-btn" data-step="-10" ${!enabled ? "disabled" : ""}>−</button>
        <input class="step-input" type="number" value="${ratio}" min="0" max="500" step="10" ${!enabled ? "disabled" : ""}>
        <button type="button" class="step-btn" data-step="+10" ${!enabled ? "disabled" : ""}>+</button>
        <span class="step-pct">%</span>
      </div>
    `;
    // 启用/禁跟切换
    row.querySelector(".market-toggle-btn").addEventListener("click", () => {
      const newEnabled = !row.classList.contains("market-rule-enabled");
      row.classList.toggle("market-rule-enabled",  newEnabled);
      row.classList.toggle("market-rule-disabled", !newEnabled);
      const toggleBtn = row.querySelector(".market-toggle-btn");
      toggleBtn.textContent = marketRuleToggleLabel(newEnabled);
      toggleBtn.setAttribute("aria-pressed", newEnabled ? "true" : "false");
      toggleBtn.title = marketRuleToggleTitle(meta.name, newEnabled);
      row.querySelectorAll(".step-btn, .step-input").forEach(el => el.disabled = !newEnabled);
      markFollowFormDirty();
    });
    // 步进按钮
    row.querySelectorAll(".step-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const inp = row.querySelector(".step-input");
        const delta = parseInt(btn.dataset.step);
        const cur = parseFloat(inp.value) || 0;
        inp.value = String(Math.max(0, Math.min(500, cur + delta)));
        markFollowFormDirty();
      });
    });
    const stepInput = row.querySelector(".step-input");
    if (stepInput) {
      stepInput.addEventListener("input", markFollowFormDirty);
      stepInput.addEventListener("change", markFollowFormDirty);
    }
    container.appendChild(row);
  }
}

function normalizeTargetRatioPercent(value) {
  if (value == null || String(value).trim() === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return Math.max(0, Math.min(500, n));
}

function targetDisplayMeta(name) {
  const account = findAccountByTargetName(name);
  const label = account ? accountLabel(account) : name;
  const orderCount = (Array.isArray(state.orders) ? state.orders : []).filter((order) => String(order?.target || "").trim() === String(name || "").trim()).length;
  const meta = account && account.base_url
    ? account.base_url
    : orderCount
      ? `远端交易节点 · ${orderCount} 条近期订单`
      : "已保存规则";
  return { label: label || name, meta };
}

function buildTargetRulesForUi(targets) {
  const map = new Map();
  const addRule = (rule) => {
    const name = String(rule?.name || "").trim();
    if (!name) return;
    const existing = map.get(name) || { name, enabled: true, only_live: false, ratio_percent: null };
    map.set(name, {
      name,
      enabled: !!(rule?.enabled ?? existing.enabled),
      only_live: !!(rule?.only_live ?? existing.only_live),
      ratio_percent: normalizeTargetRatioPercent(rule?.ratio_percent ?? existing.ratio_percent),
    });
  };

  (Array.isArray(targets) ? targets : []).forEach(addRule);
  (Array.isArray(state.accounts) ? state.accounts : []).forEach((account) => {
    const name = String(account?.name || "").trim();
    if (!name || map.has(name)) return;
    map.set(name, { name, enabled: true, only_live: false, ratio_percent: null });
  });
  (Array.isArray(state.availableTargets) ? state.availableTargets : []).forEach((targetName) => {
    const name = String(targetName || "").trim();
    if (!name || map.has(name)) return;
    map.set(name, { name, enabled: true, only_live: false, ratio_percent: null });
  });
  (Array.isArray(state.orders) ? state.orders : []).forEach((order) => {
    const name = String(order?.target || "").trim();
    if (!name || map.has(name)) return;
    map.set(name, { name, enabled: true, only_live: false, ratio_percent: null });
  });
  return Array.from(map.values()).sort((a, b) => String(a.name).localeCompare(String(b.name)));
}

// 后台过滤渲染
function renderTargetFilters(targets) {
  const container = $("target-filters-container");
  if (!container) return;
  const rows = buildTargetRulesForUi(targets);
  if (!rows.length) {
    container.innerHTML = `<div style="font-size:11px;color:var(--text-3)">暂无监控节点，请先添加信号源账号。</div>`;
    return;
  }

  container.innerHTML = "";
  for (const rule of rows) {
    const name = rule.name;
    const enabled = rule.enabled !== false && normalizeTargetRatioPercent(rule.ratio_percent) !== 0;
    const onlyLive = !!rule.only_live;
    const display = targetDisplayMeta(name);
    const row = document.createElement("div");
    row.className = "target-filter-row";
    row.dataset.name = name;
    row.innerHTML = `
      <div style="overflow:hidden;min-width:0;flex:1">
        <div class="target-filter-name">${escapeHtml(display.label)}</div>
        <div class="target-filter-url">${escapeHtml(display.meta)}</div>
      </div>
      <button type="button" class="live-filter-btn ${onlyLive ? "active" : ""}">
        <i class="fa-solid ${onlyLive ? "fa-circle-check" : "fa-toggle-off"}"></i>
        ${onlyLive ? "只跟滚球" : "全部盘口"}
      </button>
      <button type="button" class="target-enabled-btn ${enabled ? "active" : ""}">
        <i class="fa-solid ${enabled ? "fa-circle-check" : "fa-ban"}"></i>
        ${enabled ? "跟单" : "禁跟"}
      </button>
    `;
    row.querySelector(".target-enabled-btn").addEventListener("click", (ev) => {
      const btn = ev.currentTarget;
      const nowEnabled = !btn.classList.contains("active");
      btn.classList.toggle("active", nowEnabled);
      btn.innerHTML = `<i class="fa-solid ${nowEnabled ? "fa-circle-check" : "fa-ban"}"></i> ${nowEnabled ? "跟单" : "禁跟"}`;
      const ratioRow = document.querySelector(`.target-ratio-row[data-name="${CSS.escape(name)}"]`);
      const ratioInput = ratioRow?.querySelector(".target-ratio-input");
      const ratioMode = ratioRow?.querySelector(".target-ratio-mode");
      if (ratioInput && !nowEnabled) {
        ratioInput.value = "0";
        if (ratioMode) {
          ratioMode.textContent = "单独";
          ratioMode.classList.remove("active");
        }
      }
      markFollowFormDirty();
    });
    row.querySelector(".live-filter-btn").addEventListener("click", (ev) => {
      const btn = ev.currentTarget;
      const nowLive = !btn.classList.contains("active");
      btn.classList.toggle("active", nowLive);
      btn.innerHTML = `<i class="fa-solid ${nowLive ? "fa-circle-check" : "fa-toggle-off"}"></i> ${nowLive ? "只跟滚球" : "全部盘口"}`;
      markFollowFormDirty();
    });
    container.appendChild(row);
  }
}

function renderTargetRatios(targets) {
  const container = $("target-ratios-container");
  if (!container) return;
  const rows = buildTargetRulesForUi(targets);
  const globalRatio = normalizeTargetRatioPercent(state.followSettings?.ratio_percent) ?? 100;
  if (!rows.length) {
    container.innerHTML = `<div style="font-size:11px;color:var(--text-3)">暂无可配置账号。</div>`;
    return;
  }

  container.innerHTML = "";
  for (const rule of rows) {
    const display = targetDisplayMeta(rule.name);
    const enabled = rule.enabled !== false && normalizeTargetRatioPercent(rule.ratio_percent) !== 0;
    const ratio = enabled ? normalizeTargetRatioPercent(rule.ratio_percent) : 0;
    const row = document.createElement("div");
    row.className = "target-ratio-row";
    row.dataset.name = rule.name;
    row.innerHTML = `
      <div class="target-ratio-main">
        <div class="target-ratio-name">${escapeHtml(display.label)}</div>
        <div class="target-ratio-meta">${escapeHtml(display.meta)}</div>
      </div>
      <div class="target-ratio-controls">
        <button type="button" class="target-ratio-mode ${ratio == null ? "active" : ""}">${ratio == null ? "全局" : "单独"}</button>
        <input class="target-ratio-input" type="number" min="0" max="500" step="10" placeholder="${escapeHtml(String(globalRatio))}" value="${ratio == null ? "" : escapeHtml(String(ratio))}">
        <span class="target-ratio-pct">%</span>
      </div>
    `;
    const input = row.querySelector(".target-ratio-input");
    const modeBtn = row.querySelector(".target-ratio-mode");
    const refreshMode = () => {
      const custom = String(input?.value || "").trim() !== "";
      if (modeBtn) {
        modeBtn.textContent = custom ? "单独" : "全局";
        modeBtn.classList.toggle("active", !custom);
      }
    };
    const setTargetRatioCustom = () => {
      if (!input) return;
      if (String(input.value || "").trim() === "") {
        input.value = String(globalRatio);
      }
      refreshMode();
      input.focus();
      input.select();
    };
    input?.addEventListener("focus", () => {
      if (String(input.value || "").trim() === "") {
        setTargetRatioCustom();
        markFollowFormDirty();
      }
    });
    input?.addEventListener("input", () => {
      refreshMode();
      markFollowFormDirty();
    });
    input?.addEventListener("change", () => {
      const normalized = normalizeTargetRatioPercent(input.value);
      input.value = normalized == null ? "" : String(normalized);
      refreshMode();
      markFollowFormDirty();
    });
    modeBtn?.addEventListener("click", () => {
      if (!input) return;
      if (String(input.value || "").trim() !== "") {
        input.value = "";
        refreshMode();
      } else {
        setTargetRatioCustom();
      }
      markFollowFormDirty();
    });
    container.appendChild(row);
  }
}

function getFollowModeValue() {
  return document.querySelector('input[name="follow-mode-choice"]:checked')?.value || "fixed";
}

function setFollowModeValue(value) {
  const mode = value === "ratio" ? "ratio" : "fixed";
  document.querySelectorAll('input[name="follow-mode-choice"]').forEach((input) => {
    input.checked = input.value === mode;
  });
}

function applyFollowMode() {
  const mode  = getFollowModeValue();
  const fixed = $("follow-fixed");
  const ratio = $("follow-ratio");
  const fixedField = $("follow-fixed-field");
  const ratioField = $("follow-ratio-field");
  if (fixedField) fixedField.hidden = mode !== "fixed";
  if (ratioField) ratioField.hidden = mode !== "ratio";
  if (fixed) fixed.disabled = mode !== "fixed";
  if (ratio) ratio.disabled = mode !== "ratio";
}

function formatFollowSplitDelay(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function syncFollowSplitDelayOutput() {
  const input = $("follow-split-delay");
  if (!input) return;
  const min = Number(input.min || 0);
  const max = Number(input.max || 60);
  const value = Number(input.value || 0);
  const progress = max > min ? Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100)) : 0;
  const valueText = `${formatFollowSplitDelay(value)} 秒`;
  input.style.setProperty("--range-progress", `${progress}%`);
  input.setAttribute("aria-valuetext", valueText);
  if ($("follow-split-delay-output")) $("follow-split-delay-output").textContent = valueText;
  const scale = document.querySelector(".sc-range-scale span:last-child");
  if (scale) scale.textContent = `${formatFollowSplitDelay(max)} 秒`;
}

function setFollowSplitDelayValue(value) {
  const input = $("follow-split-delay");
  if (!input) return;
  const numericValue = Math.max(0, Number(value) || 0);
  const currentMax = Number(input.max || 60);
  if (numericValue > currentMax) input.max = String(Math.ceil(numericValue / 10) * 10);
  input.value = String(numericValue);
  syncFollowSplitDelayOutput();
}

function getFollowFormData() {
  const enabled     = true;
  const mode        = getFollowModeValue();
  const fixedAmount = parseFloat(($("follow-fixed")?.value || "0").trim()) || 0;
  const ratioPercent= parseFloat(($("follow-ratio")?.value || "0").trim()) || 0;
  const maxAmount   = parseFloat(($("follow-max")?.value   || "0").trim()) || 0;
  const matchLimitAmount = parseFloat(($("follow-match-limit")?.value || "0").trim()) || 0;
  const marketLimitAmount = parseFloat(($("follow-market-limit")?.value || "0").trim()) || 0;
  const minOddsRaw  = ($("follow-min-odds")?.value || "").trim();
  const maxOddsRaw  = ($("follow-max-odds")?.value || "").trim();
  const splitCount  = parseInt(($("follow-split-count")?.value  || "1").trim()) || 1;
  const splitDelay  = parseFloat(($("follow-split-delay")?.value || "0").trim()) || 0;

  // 反单
  const reverse_rules = {
    all:      !!$("rev-all")?.classList.contains("active"),
    total:    !!$("rev-total")?.classList.contains("active"),
    handicap: !!$("rev-handicap")?.classList.contains("active"),
  };

  // 玩法比例
  const market_rules = {};
  document.querySelectorAll(".market-rule-row[data-key]").forEach(row => {
    const key     = row.dataset.key;
    const enabled = row.classList.contains("market-rule-enabled");
    const ratio   = parseFloat(row.querySelector(".step-input")?.value || "100") || 0;
    market_rules[key] = { enabled, ratio_percent: ratio };
  });

  // 后台过滤 + 单账号比例。先放入现有规则，避免旧配置在 UI 局部缺失时被清空。
  const targetMap = new Map();
  const upsertTarget = (name) => {
    const key = String(name || "").trim();
    if (!key) return null;
    if (!targetMap.has(key)) {
      targetMap.set(key, { name: key, enabled: true, only_live: false, ratio_percent: null });
    }
    return targetMap.get(key);
  };
  (Array.isArray(state.followSettings?.targets) ? state.followSettings.targets : []).forEach((rule) => {
    const item = upsertTarget(rule?.name);
    if (!item) return;
    item.enabled = rule.enabled !== false;
    item.only_live = !!rule.only_live;
    item.ratio_percent = normalizeTargetRatioPercent(rule.ratio_percent);
  });
  document.querySelectorAll(".target-filter-row[data-name]").forEach(row => {
    const name      = row.dataset.name;
    const enabled   = !!row.querySelector(".target-enabled-btn.active");
    const only_live = !!row.querySelector(".live-filter-btn.active");
    const item = upsertTarget(name);
    if (item) {
      item.enabled = enabled;
      item.only_live = only_live;
    }
  });
  document.querySelectorAll(".target-ratio-row[data-name]").forEach(row => {
    const item = upsertTarget(row.dataset.name);
    if (!item) return;
    item.ratio_percent = normalizeTargetRatioPercent(row.querySelector(".target-ratio-input")?.value);
  });
  targetMap.forEach((item) => {
    if (item && item.enabled === false) {
      item.ratio_percent = 0;
    }
  });
  const targets = Array.from(targetMap.values());

  const follow_min_odds = minOddsRaw === "" ? null : (parseFloat(minOddsRaw) || 0);
  const follow_max_odds = maxOddsRaw === "" ? null : (parseFloat(maxOddsRaw) || 0);

  return {
    enabled, mode, fixed_amount: fixedAmount, ratio_percent: ratioPercent,
    max_amount: maxAmount, match_limit_amount: matchLimitAmount, market_limit_amount: marketLimitAmount,
    split_count: splitCount, split_delay_seconds: splitDelay,
    follow_min_odds, follow_max_odds,
    reverse_rules, market_rules, targets,
  };
}

async function saveFollowSettings() {
  followFormUiState.saving = true;
  renderFollowFormStatus();
  try {
    const settings = getFollowFormData();
    const data = await fetchJson("/api/follow-settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings }),
    });
    state.followSettings = data.settings || null;
    markFollowFormClean();
    renderFollowForm({ force: true });
    toast("跟单策略已保存", "success");
    recordSystemCenterActivity("跟单策略已保存", "success");
    return true;
  } catch (e) {
    const message = String(e && e.message ? e.message : e);
    toast(`保存失败: ${message}`, "error");
    recordSystemCenterActivity(`跟单策略保存失败：${message}`, "error");
    return false;
  } finally {
    followFormUiState.saving = false;
    renderFollowFormStatus();
  }
}

// ── Report ─────────────────────────────────────────────────────
const REPORT_METRICS_STALE_MS = 15000;
const reportState = {
  range: "today",          // today | week | all
  orders: [],
  followByOid: {},         // orderId → [result rows]
  settlementByOid: {},     // orderId → {status, status_label, profit_sum}
  metrics: null,
  metricsLoadedAt: 0,
  metricsLoading: false,
  deletingOids: new Set(),
};

function fmtMoney(v) {
  const n = Number(v || 0);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n).toFixed(2);
  return (n >= 0 ? "+" : "-") + "¥" + abs;
}
function fmtPercent(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  return (Number(v) * 100).toFixed(1) + "%";
}
function fmtNetValue(v) {
  if (v == null || !Number.isFinite(Number(v))) return "—";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(3);
}

async function loadReportData() {
  const [ordersRes, followRes, metricsRes, statusRes] = await Promise.allSettled([
    fetchJson("/api/orders?limit=200"),
    fetchJson("/api/follow-results?limit=500"),
    fetchJson("/api/follow-report-metrics"),
    fetchJson("/api/status"),
  ]);

  if (ordersRes.status === "fulfilled") {
    reportState.orders = (ordersRes.value.orders || []);
  }
  if (followRes.status === "fulfilled") {
    const rows = followRes.value.results || [];
    setOrderFollowResults(rows);
  }
  if (metricsRes.status === "fulfilled") {
    reportState.metrics = metricsRes.value.metrics || null;
    reportState.metricsLoadedAt = Date.now();
  }
  if (statusRes.status === "fulfilled") {
    updateFollowEngineState(statusRes.value);
  }
  // Settlement statuses — batch per orderId
  const oids = reportState.orders.map(o => String(o.orderId || "")).filter(Boolean);
  if (oids.length) {
    try {
      const params = new URLSearchParams();
      params.set("limit", "200");
      params.set("order_ids", oids.join(","));
      const settleRes = await fetchJson("/api/follow-settlement-status?" + params.toString());
      const smap = {};
      const statusRows = settleRes.statuses && typeof settleRes.statuses === "object"
        ? Object.values(settleRes.statuses)
        : (Array.isArray(settleRes.results) ? settleRes.results : []);
      for (const item of statusRows) {
        const oid = String(item.orderId || item.order_id || "");
        if (oid) smap[oid] = item;
      }
      reportState.settlementByOid = smap;
    } catch { /* settlement optional */ }
  }

  renderReport();
}

async function loadReportMetrics(options = {}) {
  const force = !!(options && options.force);
  const now = Date.now();
  if (reportState.metricsLoading) return reportState.metrics;
  if (!force && reportState.metrics && (now - Number(reportState.metricsLoadedAt || 0)) < REPORT_METRICS_STALE_MS) {
    return reportState.metrics;
  }
  reportState.metricsLoading = true;
  try {
    const data = await fetchJson("/api/follow-report-metrics");
    reportState.metrics = data.metrics || null;
    reportState.metricsLoadedAt = Date.now();
    renderOrderReportStats();
    if (state.view === "report") renderReportStats();
    return reportState.metrics;
  } finally {
    reportState.metricsLoading = false;
  }
}

function getReportMetrics(range = reportState.range) {
  const m = reportState.metrics;
  if (!m) return {};
  const periods = m.periods || {};
  return periods[range] || {};
}

function reportRangeLabel(range) {
  return { today: "今日", week: "本周", all: "历史" }[range] || "";
}

function getOrderDashboardReportRange() {
  const timeRange = String(state.orderDashboard && state.orderDashboard.filters && state.orderDashboard.filters.timeRange || "today");
  if (timeRange === "week") return "week";
  if (timeRange === "history") return "all";
  return "today";
}

function renderReportStatsGroup(prefix, range) {
  const p = getReportMetrics(range);
  const pnl = Number(p.pnl || 0);
  const pnlEl = $(`${prefix}-pnl`);
  if (pnlEl) {
    pnlEl.textContent = pnl === 0 ? "¥0.00" : fmtMoney(pnl);
    pnlEl.className = "rpt-stat-val" + (pnl > 0 ? " pos" : pnl < 0 ? " neg" : "");
    const card = pnlEl.closest(".rpt-stat-card");
    if (card) {
      card.classList.toggle("pos", pnl > 0);
      card.classList.toggle("neg", pnl < 0);
    }
  }
  const rangeLabel = reportRangeLabel(range);
  const sc = $(`${prefix}-settled-count`);
  if (sc) sc.textContent = `${rangeLabel}已结算 ${p.settled_orders || 0} 场`;
  const oc = $(`${prefix}-order-count`); if (oc) oc.textContent = String(p.order_count || 0);
  const so = $(`${prefix}-settled-orders`); if (so) so.textContent = String(p.settled_orders || 0);
  const pt = $(`${prefix}-pending-tickets`); if (pt) pt.textContent = String(p.pending_ticket_count || 0);
  const wr = $(`${prefix}-win-rate`); if (wr) wr.textContent = fmtPercent(p.win_rate);
  const nv = $(`${prefix}-net-value`);
  if (nv) nv.textContent = `净胜值 ${fmtNetValue(p.net_value)} · 已结算票 ${p.settled_ticket_count || 0}`;
}

function renderOrderReportStats() {
  const range = getOrderDashboardReportRange();
  renderReportStatsGroup("od-rpt", range);
  const p = getReportMetrics(range);
  const rangeLabel = reportRangeLabel(range);
  const sc = $("od-rpt-settled-count");
  if (sc) {
    sc.textContent = `${rangeLabel}已结算 ${p.settled_orders || 0} 场 · 跟单 ${p.order_count || 0} 场 · 待结算 ${p.pending_ticket_count || 0} 票`;
  }
  const rateSub = $("od-kpi-rate-sub");
  if (rateSub) rateSub.textContent = `胜率 ${fmtPercent(p.win_rate)} · 净胜值 ${fmtNetValue(p.net_value)}`;
}

function renderReportStats() {
  renderReportStatsGroup("rpt", reportState.range);
  renderOrderReportStats();
}

function orderInRange(o, range) {
  const ts = Number(o.ts || 0);
  if (!ts) return range === "all";
  const now = Date.now() / 1000;
  if (range === "today") {
    const d = new Date(); d.setHours(0, 0, 0, 0);
    return ts >= d.getTime() / 1000;
  }
  if (range === "week") { return ts >= now - 7 * 86400; }
  return true;
}

function getFollowSummary(oid) {
  const rows = reportState.followByOid[String(oid || "")] || [];
  if (!rows.length) return { ok: 0, fail: 0, pending: 1, engineStopped: isFollowEngineStopped() };
  const splitTotal = rows.reduce((max, row) => {
    const count = Number(row && (row.split_count || row.split_total) || 0);
    const index = Number(row && row.split_index || 0);
    return Math.max(max, Number.isFinite(count) ? count : 0, Number.isFinite(index) ? index : 0);
  }, 0);
  const splitStates = new Map();
  const legacyStates = [];
  const ticketOf = (row) => String(row && (row.ticket_id || (row.bet_result_head && row.bet_result_head.ticket_id) || "") || "").trim();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const index = Number(row.split_index || 0);
    const ticket = ticketOf(row);
    if (splitTotal > 1 && Number.isFinite(index) && index > 0) {
      const previous = splitStates.get(index);
      if (!previous || (!previous.ok && !!row.ok) || (!previous.ok && !row.ok && Number(row.ts_ms || row.ts || 0) >= Number(previous.ts_ms || previous.ts || 0))) {
        splitStates.set(index, row);
      }
    } else if (ticket) {
      if (!legacyStates.some((item) => ticketOf(item) === ticket)) legacyStates.push(row);
    } else if (!legacyStates.length || row.ok || !legacyStates[0].ok) {
      legacyStates[0] = row;
    }
  }
  const groupedStates = splitTotal > 1
    ? Array.from({ length: splitTotal }, (_, offset) => splitStates.get(offset + 1) || null)
    : legacyStates;
  return {
    ok: groupedStates.filter((row) => row && row.ok).length,
    fail: groupedStates.filter((row) => row && !row.ok).length,
    pending: groupedStates.filter((row) => !row).length,
  };
}

function buildReportStatusText(summary) {
  const current = summary || {};
  if (current.engineStopped && Number(current.ok || 0) <= 0 && Number(current.fail || 0) <= 0 && Number(current.pending || 0) > 0) {
    return "跟单引擎未运行";
  }
  return `成功 ${Number(current.ok || 0)} · 失败 ${Number(current.fail || 0)}${Number(current.pending || 0) > 0 ? ` · 待处理 ${Number(current.pending || 0)}` : ""}`;
}

function getFollowStake(oid) {
  const rows = reportState.followByOid[String(oid || "")] || [];
  let sum = 0;
  const seen = new Set();
  for (const r of rows) {
    if (!r || !r.ok) continue;
    const key = [String(r.member||""), String(r.ticket_id||""), String(r.ts_ms||r.ts||""), String(r.stake||"")].join("|");
    if (seen.has(key)) continue; seen.add(key);
    const n = Number(String(r.stake||"").replace(/,/g,""));
    if (Number.isFinite(n)) sum += n;
  }
  return sum;
}

function getSettlement(oid) {
  const rec = reportState.settlementByOid[String(oid || "")];
  if (!rec) return { status: "pending", status_label: "未结算", profit_sum: 0 };
  return {
    status: String(rec.status || "pending"),
    status_label: String(rec.status_label || "未结算"),
    profit_sum: Number(rec.profit_sum || 0),
  };
}

function settleBadgeClass(status) {
  if (typeof reportCardTools.settlementTone === "function") {
    return reportCardTools.settlementTone(status);
  }
  const s = String(status || "").toLowerCase();
  if (s === "win") return "win";
  if (s === "lose") return "lose";
  return "pending";
}

function buildReportCardModelCompat(input) {
  if (typeof reportCardTools.buildReportCardModel === "function") {
    return reportCardTools.buildReportCardModel(input);
  }
  const o = input.order || {};
  const oid = String(o.orderId || o.order_id || "");
  const settle = input.settlement || {};
  const bc = settleBadgeClass(settle.status);
  const showProfit = bc === "win" || bc === "lose";
  return {
    cardClass: "match-card rpt-card rpt-order-card",
    headerChip: `#${oid || "—"}`,
    orderId: oid,
    league: String(o.league || "未知联赛"),
    matchup: String(o.sides || o.match_name || "—"),
    betLabel: String(input.betLabel || o.betType || ""),
    betColor: String(input.betColor || "#3B82F6"),
    marketText: [String(o.display_line || o.line || "").trim(), String(o.marketLabel || o.market_label || "").trim()].filter(Boolean).join(" · ") || "盘口信息",
    selectionText: `${String(o.selection || input.betLabel || o.betType || "").trim()} ${String(o.display_line || o.line || "").trim()}${o.odds ? ` @ ${o.odds}` : ""}`.replace(/\s+/g, " ").trim(),
    statusText: String(o.status_label || o.status || "已接收"),
    reportStatusText: buildReportStatusText(input.followSummary),
    eventId: String(input.eventId || o.eventId || o.event_id || o.gid || "-"),
    stakeText: input.followStake > 0 ? (input.followStake >= 1000 ? `¥${(Math.floor(input.followStake) / 1000).toFixed(1)}k` : `¥${Math.floor(input.followStake)}`) : "—",
    oddsText: `@ ${String(o.odds || "—")}`,
    account: String(input.account || "-"),
    source: String(input.source || o.target || "-"),
    timeText: String(input.timeText || "-"),
    orderKind: String(input.orderKind || "实时"),
    isLiveOrder: !!input.isLiveOrder,
    settlementTone: bc,
    settlementLabel: String(settle.status_label || "未结算"),
    profitText: showProfit ? fmtMoney(Number(settle.profit_sum || 0)) : "",
  };
}

function renderReport() {
  renderReportStats();
  renderReportEngineWarning();

  const grid = $("rpt-cards-grid");
  if (!grid) return;
  const filtered = reportState.orders.filter(o => orderInRange(o, reportState.range));

  if (!filtered.length) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1">
        <div class="empty-icon"><i class="fa-solid fa-chart-column"></i></div>
        <div class="empty-title">暂无报表数据</div>
        <div class="empty-sub">${reportState.range === "today" ? "今日" : reportState.range === "week" ? "本周" : "历史"}暂无注单</div>
      </div>`;
    return;
  }

  grid.innerHTML = "";
  for (const o of filtered) {
    const raw = o.raw || {};
    const oid = String(o.orderId || o.order_id || "");
    const sum = getFollowSummary(oid);
    const stake = getFollowStake(oid);
    const settle = getSettlement(oid);
    const timeStr = fmtOrderTime(o);
    const acct = backendAccountOfOrder(o);
    const betColor = getBetTypeColor(o.betType);
    const betLabel = getBetTypeLabel(o.betType);
    const eventId = o.eventId || o.event_id || o.gid || raw._EVENT || raw.EVENT || raw.eventId || raw.event_id || "-";
    const sourceName = o.backendName || o.backend_name || o.target || o.backend || o.source || "-";
    const rawKind = String(o.orderType || o.order_type || raw.orderType || raw.ORDER_TYPE || "").replace(/<[^>]*>/g, "").trim();
    const isLiveOrder = /滚球|live/i.test(rawKind) || /滚球/i.test(String(o.betType || "")) || String(raw.MARKET || "").toLowerCase() === "rb";
    const orderKind = isLiveOrder ? "滚球" : (rawKind && !/^n(o)?$/i.test(rawKind) ? rawKind : "实时");
    const model = buildReportCardModelCompat({
      order: o,
      followSummary: sum,
      settlement: settle,
      followStake: stake,
      account: acct,
      source: sourceName,
      timeText: timeStr.split(" ")[1] || timeStr,
      eventId,
      betLabel,
      betColor,
      orderKind,
      isLiveOrder,
    });

    const card = document.createElement("div");
    card.className = model.cardClass;
    card.innerHTML = `
      <div class="mc-inner">
        <header class="mc-card-head">
          <span class="mc-chip">${escapeHtml(model.headerChip)}</span>
          <div class="mc-top-right">
            <span class="mc-order-type ${model.isLiveOrder ? "is-live" : "is-early"}">${escapeHtml(model.orderKind)}</span>
            <span class="mc-card-time">${escapeHtml(model.timeText)}</span>
          </div>
        </header>

        <section class="mc-card-main">
          <h3 class="mc-league">${escapeHtml(model.league)}</h3>
          <p class="mc-matchup">${escapeHtml(model.matchup)}</p>
          <div class="mc-tags-row">
            <span class="mc-meta-pill" style="border-color:${escapeHtml(model.betColor)}40;color:${escapeHtml(model.betColor)};background:${escapeHtml(model.betColor)}14">${escapeHtml(model.betLabel)}</span>
            <span class="mc-meta-text">${escapeHtml(model.marketText)}</span>
          </div>
          <p class="mc-selection-strip">${escapeHtml(model.selectionText)}</p>
        </section>

        <div class="mc-status-row rpt-status-row">
          <div class="mc-status-item">
            <span class="mc-status-label">跟单状态</span>
            <span class="mc-status-badge">${escapeHtml(model.reportStatusText)}</span>
          </div>
          <div class="mc-status-item">
            <span class="mc-status-label">结算状态</span>
            <span class="mc-match-state settle-badge ${escapeHtml(model.settlementTone)}">${escapeHtml(model.settlementLabel)}</span>
          </div>
        </div>

        <dl class="mc-detail-grid">
          <div>
            <dt>赔率</dt>
            <dd>${escapeHtml(model.oddsText)}</dd>
          </div>
          <div>
            <dt>跟单金额</dt>
            <dd>${escapeHtml(model.stakeText)}</dd>
          </div>
          <div>
            <dt>账号</dt>
            <dd>${escapeHtml(model.account)}</dd>
          </div>
          <div>
            <dt>来源</dt>
            <dd>${escapeHtml(model.source)}</dd>
          </div>
          <div>
            <dt>订单号</dt>
            <dd>${escapeHtml(model.orderId || "—")}</dd>
          </div>
          <div>
            <dt>赛事号</dt>
            <dd>${escapeHtml(model.eventId)}</dd>
          </div>
        </dl>

        <div class="rpt-card-foot">
          <span class="rpt-settle-profit ${model.profitText.startsWith("+") ? "pos" : model.profitText.startsWith("-") ? "neg" : ""}">${escapeHtml(model.profitText || "未结算")}</span>
          <button class="rpt-del-btn" title="删除该订单" data-oid="${escapeHtml(model.orderId)}">
            <i class="fa-solid fa-trash-can"></i>
          </button>
        </div>
      </div>
    `;
    card.querySelector(".rpt-del-btn")?.addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      const delOid = btn.dataset.oid;
      if (!delOid || reportState.deletingOids.has(delOid)) return;
      if (!confirm(`确定删除订单 ${delOid}？此操作不可撤销。`)) return;
      reportState.deletingOids.add(delOid);
      btn.disabled = true;
      try {
        const res = await fetchJson("/api/orders/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ order_id: delOid }),
        });
        if (!res.ok) throw new Error(res.message || "delete failed");
        toast("订单已删除", "success");
        await loadReportData();
      } catch (e) {
        toast("删除失败: " + String(e.message || e), "error");
        btn.disabled = false;
      } finally { reportState.deletingOids.delete(delOid); }
    });
    grid.appendChild(card);
  }
}

function renderReportEngineWarning() {
  const el = $("rpt-engine-warning");
  if (!el) return;
  const stopped = isFollowEngineStopped();
  el.hidden = !stopped;
  if (!stopped) return;
  el.innerHTML = `
    <div class="rpt-engine-warning-icon"><i class="fa-solid fa-circle-exclamation"></i></div>
    <div>
      <div class="rpt-engine-warning-title">跟单引擎未运行</div>
      <div class="rpt-engine-warning-text">当前报表中没有跟单结果的订单不会再按“待处理”判断；这通常表示订单已进入本机，但没有 follow_engine 消费。</div>
    </div>
  `;
}

// ── Signal pull config ─────────────────────────────────────────
async function loadSignalPullConfig() {
  try {
    const data = await fetchJson("/api/signal-pull-config");
    const cfg = normalizeSignalPullConfig(data);
    state.signalPullConfig = cfg;
    if ($("signal-pull-enabled")) $("signal-pull-enabled").checked = !!cfg.signal_pull_enabled;
    if ($("signal-pull-url"))     $("signal-pull-url").value       = String(cfg.signal_pull_url || "");
    if ($("signal-pull-limit"))   $("signal-pull-limit").value     = String(cfg.signal_pull_limit || 50);
    if ($("signal-pull-interval"))$("signal-pull-interval").value  = String(cfg.signal_pull_interval_seconds || 30);
    if ($("signal-pull-live-only")) $("signal-pull-live-only").checked = !!cfg.signal_pull_live_only;
    const hint = $("signal-pull-hint");
    if (hint) hint.textContent = "";
    renderAccountsSidebar();
    renderAccountSelect();
    renderTradingNodeKpi();
    if (state.view === "arsenal") renderPoolManagement();
  } catch { /* endpoint may not exist yet */ }
}

function normalizeSignalPullConfig(data) {
  const cfg = data && typeof data === "object" && data.config && typeof data.config === "object" ? data.config : (data || {});
  const hasEnabled = Object.prototype.hasOwnProperty.call(cfg, "signal_pull_enabled");
  const hasLiveOnly = Object.prototype.hasOwnProperty.call(cfg, "signal_pull_live_only");
  return {
    signal_pull_enabled: hasEnabled ? !!cfg.signal_pull_enabled : !!cfg.enabled,
    signal_pull_url: String(cfg.signal_pull_url || cfg.url || "").trim(),
    signal_pull_token: String(cfg.signal_pull_token || cfg.token || "").trim(),
    signal_pull_live_only: hasLiveOnly ? !!cfg.signal_pull_live_only : !!cfg.live_only,
    signal_pull_limit: Number(cfg.signal_pull_limit || cfg.limit || 50),
    signal_pull_interval_seconds: Number(cfg.signal_pull_interval_seconds || cfg.interval || cfg.interval_seconds || 30),
  };
}

async function saveSignalPullConfig() {
  const hint = $("signal-pull-hint");
  if (hint) { hint.textContent = "正在保存..."; hint.className = "form-hint"; }
  try {
    const payload = {
      signal_pull_enabled:  !!$("signal-pull-enabled")?.checked,
      signal_pull_url:      ($("signal-pull-url")?.value     || "").trim(),
      signal_pull_token:    ($("signal-pull-token")?.value   || "").trim() || undefined,
      signal_pull_limit:    parseInt($("signal-pull-limit")?.value || "50")  || 50,
      signal_pull_interval_seconds: parseInt($("signal-pull-interval")?.value || "30") || 30,
      signal_pull_live_only: !!$("signal-pull-live-only")?.checked,
    };
    if (!payload.signal_pull_token) delete payload.signal_pull_token;
    const data = await fetchJson("/api/signal-pull-config", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!data.ok) throw new Error(data.message || "保存失败");
    state.signalPullConfig = normalizeSignalPullConfig(data);
    renderAccountsSidebar();
    renderAccountSelect();
    renderTradingNodeKpi();
    if (state.view === "arsenal") renderPoolManagement();
    if ($("signal-pull-token")) $("signal-pull-token").value = "";
    if (hint) { hint.textContent = "保存成功"; hint.className = "form-hint success"; }
    toast("信号拉取配置已保存", "success");
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    if (hint) { hint.textContent = `错误: ${msg}`; hint.className = "form-hint error"; }
    toast(`保存失败: ${msg}`, "error");
  }
}

// ── Add account ────────────────────────────────────────────────
async function submitAddAccount() {
  const helper = window.HDAddNodeForm;
  if (!helper || typeof helper.buildAddNodeSubmitRequest !== "function") {
    if ($("add-hint")) { $("add-hint").textContent = "表单模块未加载"; $("add-hint").className = "form-hint error"; }
    return;
  }
  if (!addNodeFormState) openAddModal();
  if (!addNodeFormState) return;

  addNodeFormState.account.base_url = $("add-base-url")?.value.trim() || "";
  addNodeFormState.account.username = $("add-username")?.value.trim() || "";
  addNodeFormState.account.password = $("add-password")?.value || "";
  addNodeFormState.account.relogin_min_interval_seconds = parseInt($("add-relogin-min-interval")?.value || "5", 10) || 5;
  addNodeFormState.account.security_code = $("add-security-code")?.value.trim() || "";
  addNodeFormState.account.monitor_members = parseListField($("add-monitor-members")?.value || "");
  addNodeFormState.account.monitor_downlines = parseListField($("add-monitor-downlines")?.value || "");

  addNodeFormState.signal.url = $("add-signal-url")?.value.trim() || "";
  addNodeFormState.signal.token = $("add-signal-token")?.value.trim() || "";
  addNodeFormState.signal.limit = parseInt($("add-signal-limit")?.value || "200", 10) || 200;
  addNodeFormState.signal.interval_seconds = parseInt($("add-signal-interval")?.value || "1", 10) || 1;
  addNodeFormState.signal.live_only = !!$("add-signal-live-only")?.checked;

  const isSignalMode = addNodeFormState.mode === "signal";
  const idleLabel = isSignalMode ? "保存信号源" : "立即接入";
  const btn = $("add-submit");
  const submitLabel = $("add-submit-label");
  const submitIcon = btn?.querySelector("i");
  if (btn) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
  }
  if (submitLabel) submitLabel.textContent = isSignalMode ? "保存中..." : "接入中...";
  if (submitIcon) submitIcon.className = "fa-solid fa-spinner";
  try {
    const req = helper.buildAddNodeSubmitRequest(addNodeFormState);
    const data = await fetchJson(req.endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.payload),
    });
    closeAddModal();
    if (req.endpoint === "/api/accounts/add") {
      await loadAccounts();
      const targetName = String((data && data.name) || addNodeFormState.account.username);
      await reloginAccount(
        targetName,
        addNodeFormState.account.username,
        `已发起浏览器登录：${addNodeFormState.account.username || targetName}`,
      );
    } else {
      await loadSignalPullConfig();
      toast("远端交易信号配置已保存", "success");
    }
  } catch (e) {
    if ($("add-hint")) { $("add-hint").textContent = `接入失败：${e.message}`; $("add-hint").className = "form-hint error"; }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.setAttribute("aria-busy", "false");
    }
    if (submitLabel) submitLabel.textContent = idleLabel;
    if (submitIcon) submitIcon.className = `fa-solid ${isSignalMode ? "fa-floppy-disk" : "fa-plug"}`;
  }
}

// ── View refresh ───────────────────────────────────────────────
function mergeFollowQueueOrdersForDisplay(orders, queueItems) {
  if (orderRealtimeTools && typeof orderRealtimeTools.mergeFollowQueueOrders === "function") {
    return orderRealtimeTools.mergeFollowQueueOrders(orders, queueItems);
  }
  return Array.isArray(orders) ? orders : [];
}

async function fetchFollowQueueItemsForOrders() {
  try {
    const data = await fetchJson("/api/follow-queue?limit=80&include_done=1&done_limit=200");
    state.followQueue = Array.isArray(data.items) ? data.items : [];
    state.followQueueCounts = (data.counts && typeof data.counts === "object") ? data.counts : {};
    return state.followQueue;
  } catch (e) {
    console.warn("follow queue display merge failed:", e);
    state.followQueue = [];
    state.followQueueCounts = {};
    return [];
  }
}

async function fetchFollowResultsForOrders() {
  try {
    const data = await fetchJson("/api/follow-results?limit=1000");
    const rows = Array.isArray(data.results) ? data.results : [];
    setOrderFollowResults(rows);
    return rows;
  } catch (e) {
    console.warn("follow result display merge failed:", e);
    state.orderFollowByOid = {};
    return [];
  }
}

function getOrderRealtimeGid(order) {
  if (orderRealtimeTools && typeof orderRealtimeTools.extractOrderGid === "function") {
    return orderRealtimeTools.extractOrderGid(order);
  }
  const raw = order && order.raw && typeof order.raw === "object" ? order.raw : {};
  return String((order && (order.gid || order.eventId || order.event_id)) || raw._EVENT || raw.EVENT || "").trim();
}

function getOrderRealtimeMode(order) {
  const queue = order && order._follow_queue && typeof order._follow_queue === "object" ? order._follow_queue : {};
  const derived = queue.derived && typeof queue.derived === "object" ? queue.derived : {};
  const raw = order && order.raw && typeof order.raw === "object" ? order.raw : {};
  const modeText = derived.mode || (order && order.orderType) || raw.orderType || raw.ORDER_TYPE || raw.MARKET || "";
  if (orderRealtimeTools && typeof orderRealtimeTools.normalizeMode === "function") {
    return orderRealtimeTools.normalizeMode(modeText);
  }
  return /滚球|live|rb/i.test(String(modeText)) ? "live" : "today";
}

function rememberOrderEventSnapshot(model, snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  const timeline = snapshot.timeline && typeof snapshot.timeline === "object" ? snapshot.timeline : {};
  const meta = snapshot.meta && typeof snapshot.meta === "object" ? snapshot.meta : {};
  if (!timeline.score && !timeline.statusText && !timeline.matchId && !meta.mtId) return;
  const gid = String((model && (model.gid || model.eventId)) || meta.masterGid || meta.requestedGid || "").trim();
  const mtId = String(timeline.matchId || meta.mtId || (model && model.mtId) || "").trim();
  const key = gid || mtId;
  if (!key) return;
  state.orderEventSnapshots[key] = {
    gid,
    mtId,
    mode: (model && model.mode) || meta.mode || "live",
    memberId: (model && model.memberId) || state.matchSearch.memberId,
    memberLabel: (model && model.memberLabel) || state.matchSearch.memberLabel,
    league: (model && model.league) || meta.league || "",
    homeTeam: timeline.homeTeam || (model && model.homeTeam) || "",
    awayTeam: timeline.awayTeam || (model && model.awayTeam) || "",
    snapshot,
  };
}

function applyEventSnapshotToRetainedMatches(model, snapshot) {
  if (!matchSearchTools || typeof matchSearchTools.applyHgEventSnapshotToMatch !== "function") return;
  if (!snapshot || typeof snapshot !== "object") return;
  const modelKey = String(model && model.key || "").trim();
  const modelGid = String(model && model.gid || "").trim();
  const modelMtId = String(model && model.mtId || "").trim();
  const meta = snapshot.meta && typeof snapshot.meta === "object" ? snapshot.meta : {};
  const timeline = snapshot.timeline && typeof snapshot.timeline === "object" ? snapshot.timeline : {};
  const snapGid = String(meta.masterGid || meta.requestedGid || "").trim();
  const snapMtId = String(timeline.matchId || meta.mtId || "").trim();

  const shouldUpdate = (row) => {
    if (!row || typeof row !== "object") return false;
    return (
      (modelKey && row.key === modelKey) ||
      (modelGid && String(row.gid || "") === modelGid) ||
      (snapGid && String(row.gid || "") === snapGid) ||
      (modelMtId && String(row.mtId || "") === modelMtId) ||
      (snapMtId && String(row.mtId || "") === snapMtId)
    );
  };

  let changed = false;
  const applyOne = (row) => {
    if (!shouldUpdate(row)) return row;
    changed = true;
    return matchSearchTools.applyHgEventSnapshotToMatch(row, snapshot);
  };
  state.matchSearch.retained = (state.matchSearch.retained || []).map(applyOne);
  state.matchSearch.results = (state.matchSearch.results || []).map(applyOne);
  if (changed) saveRetainedHgMatches();
}

function applyOrderEventSnapshotsForDisplay(orders) {
  if (!orderRealtimeTools || typeof orderRealtimeTools.applyEventSnapshotsToOrders !== "function") {
    return Array.isArray(orders) ? orders : [];
  }
  const snapshots = Object.values(state.orderEventSnapshots || {});
  if (!snapshots.length) return Array.isArray(orders) ? orders : [];
  return orderRealtimeTools.applyEventSnapshotsToOrders(orders, snapshots, {
    memberId: state.matchSearch.memberId,
    memberLabel: state.matchSearch.memberLabel,
  });
}

async function enrichOrdersWithLiveMatches(orders) {
  if (!orderRealtimeTools || typeof orderRealtimeTools.applyLiveMatchSnapshotsToOrders !== "function") {
    return Array.isArray(orders) ? orders : [];
  }
  const rows = Array.isArray(orders) ? orders : [];
  const liveRows = rows.filter((row) => getOrderRealtimeMode(row) === "live");
  const wantedGids = new Set(liveRows.map(getOrderRealtimeGid).filter(Boolean));
  if (!wantedGids.size) return rows;

  try {
    const memberId = await ensureMatchSearchMember();
    const games = [];
    const data = await fetchJson(`/api/chashui/games?member_id=${encodeURIComponent(memberId)}&mode=live`);
    for (const game of Array.isArray(data.games) ? data.games : []) {
      games.push(Object.assign({}, game, { mode: data.mode || "live" }));
    }
    return orderRealtimeTools.applyLiveMatchSnapshotsToOrders(rows, games, {
      memberId,
      memberLabel: state.matchSearch.memberLabel,
    });
  } catch (e) {
    console.warn("live order match refresh failed:", e);
    return rows;
  }
}

async function refreshView(options = {}) {
  const manual = !!(options && options.manual);
  if (refreshViewInFlight) return;
  refreshViewInFlight = true;
  const btn = $("btn-refresh");
  const blocking = !orderStreamReady && !state.orders.length && canShowOrderStreamState();
  if (blocking) renderOrderStreamSkeleton();
  if (btn) btn.classList.add("is-loading");
  try {
    const since  = 0;
    const limit  = 200;
    const target = state.selectedTarget === REMOTE_SIGNAL_FILTER_TARGET ? "" : (state.selectedTarget || "");
    const url = `/api/orders?since=${since}&limit=${limit}&target=${encodeURIComponent(target)}`;
    const [data, queueItems, followRows] = await Promise.all([
      fetchJson(url),
      fetchFollowQueueItemsForOrders(),
      fetchFollowResultsForOrders(),
    ]);
    mergeTerminalQueueFollowRows(queueItems, followRows);
    const mergedOrders = mergeFollowQueueOrdersForDisplay(data.orders || [], queueItems);
    state.orders = applyOrderEventSnapshotsForDisplay(await enrichOrdersWithLiveMatches(mergedOrders));
    await refreshRetainedHgMatches({ render: false });
    renderOrdersView();
    loadReportMetrics().catch(() => {});
    await loadArsenal();
    await loadStatuses();
    renderAccountsSidebar();
    if (state.view === "arsenal") renderPoolManagement();
    if (state.view === "follow-settings" || state.view === "system-center") {
      await loadFollowSettings();
      renderFollowForm();
    }
    orderStreamReady = true;
    updateStreamMeta();
  } catch (e) {
    console.error("refreshView error:", e);
    if ((blocking || !state.orders.length) && canShowOrderStreamState()) {
      renderOrderStreamError(e);
    }
    if (manual) toast((e && e.message) || "刷新失败，请稍后重试", "error");
  } finally {
    if (btn) btn.classList.remove("is-loading");
    refreshViewInFlight = false;
  }
}

// ── System center ──────────────────────────────────────────────
const SYSTEM_CENTER_PART_VIEWS = new Set(["system-center", "follow-settings", "telegram", "tg-signal"]);

function setSystemCenterSummaryState(id, label, tone) {
  const node = $(id);
  if (!node) return;
  const detail = node.querySelector("small");
  if (detail) detail.textContent = label;
  node.dataset.tone = tone;
}

function renderSystemCenterTelegramSummary() {
  const data = state.tg || {};
  const configured = !!data.configured;
  const hasChat = !!String(data.chat_id || "").trim();
  const hasProxy = !!String(data.proxy || "").trim();
  const pushEnabled = !!data.status_push_enabled;
  setSystemCenterSummaryState("sc-notify-bot", configured ? "已认证" : "未配置", configured ? "ok" : "off");
  setSystemCenterSummaryState("sc-notify-chat", hasChat ? "已设置" : "未设置", hasChat ? "ok" : "off");
  setSystemCenterSummaryState("sc-notify-proxy", hasProxy ? "已配置" : "直连", hasProxy ? "ok" : "off");
  setSystemCenterSummaryState("sc-notify-push", pushEnabled ? "已启用" : "已关闭", pushEnabled ? "ok" : "off");
}

function renderSystemCenterStatusBar() {
  const health = $("system-health-status");
  if (health) {
    const known = !!state.followEngine?.known;
    const running = !!state.followEngine?.running;
    health.dataset.tone = !known ? "pending" : (running ? "ok" : "error");
    const label = health.querySelector("b");
    if (label) label.textContent = !known ? "系统状态检查中" : (running ? "系统安全运行中" : "跟单引擎未运行");
  }
  if ($("system-uptime")) $("system-uptime").textContent = state.systemCenter?.uptimeText || "--";
  if ($("system-version")) $("system-version").textContent = state.systemCenter?.version || "未提供";
  const sync = $("system-sync-status");
  if (sync) {
    const tone = state.systemCenter?.syncTone || "pending";
    sync.dataset.tone = tone;
    const icon = sync.querySelector("i");
    const label = sync.querySelector("b");
    if (icon) icon.className = `fa-solid ${tone === "ok" ? "fa-circle-check" : tone === "error" ? "fa-circle-exclamation" : "fa-circle-notch"}`;
    if (label) label.textContent = tone === "ok" ? "数据同步正常" : tone === "error" ? "数据同步异常" : "数据同步检查中";
  }
}

function renderSystemCenterActivity() {
  const list = $("system-notification-activity");
  if (!list) return;
  const rows = Array.isArray(state.systemCenter?.activity) ? state.systemCenter.activity : [];
  if (!rows.length) return;
  list.innerHTML = "";
  rows.slice(0, 8).forEach((item) => {
    const row = document.createElement("div");
    row.className = "sc-activity-row";
    row.dataset.tone = item.tone || "info";
    const time = document.createElement("time");
    time.dateTime = item.iso;
    time.textContent = item.time;
    const dot = document.createElement("span");
    dot.className = "sc-activity-dot";
    dot.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = item.message;
    row.append(time, dot, text);
    list.append(row);
  });
}

function recordSystemCenterActivity(message, tone = "info") {
  if (!state.systemCenter) return;
  const now = new Date();
  state.systemCenter.activity.unshift({
    message: String(message || ""),
    tone,
    iso: now.toISOString(),
    time: now.toLocaleTimeString("zh-CN", { hour12: false }),
  });
  state.systemCenter.activity = state.systemCenter.activity.slice(0, 20);
  renderSystemCenterActivity();
}

function openSystemCenter() {
  activateView("system-center", { syncNav: true, updateTitle: true });
}

function bindSystemCenter() {
  $("tg-token-toggle")?.addEventListener("click", (event) => {
    const button = event.currentTarget;
    const input = $("tg-token");
    if (!input) return;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.setAttribute("aria-pressed", reveal ? "true" : "false");
    button.setAttribute("aria-label", reveal ? "隐藏 Bot Token" : "显示 Bot Token");
    const icon = button.querySelector("i");
    if (icon) icon.className = `fa-solid ${reveal ? "fa-eye-slash" : "fa-eye"}`;
  });
  [
    "tg-token", "tg-chat", "tg-proxy", "tg-status-push", "tg-status-chat", "tg-status-proxy",
  ].forEach((id) => {
    const field = $(id);
    if (!field || field.dataset.systemSummaryBound === "true") return;
    field.dataset.systemSummaryBound = "true";
    const onChange = () => renderSystemCenterTelegramSummary();
    field.addEventListener("input", onChange);
    field.addEventListener("change", onChange);
  });
  renderSystemCenterTelegramSummary();
  renderSystemCenterStatusBar();
}

if (typeof window !== "undefined") window.openSystemCenter = openSystemCenter;

// ── View switching ─────────────────────────────────────────────
function applyViewVisibility() {
  for (const el of document.querySelectorAll(".view[data-view]")) {
    const viewName = el.getAttribute("data-view");
    const isActive = state.view === "system-center"
      ? SYSTEM_CENTER_PART_VIEWS.has(viewName)
      : viewName === state.view;
    if (isActive) {
      el.style.display = "block";
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      requestAnimationFrame(() => {
        el.style.transition = "opacity 0.2s ease, transform 0.2s ease";
        el.style.opacity    = "1";
        el.style.transform  = "translateY(0)";
      });
    } else {
      el.style.display = "none";
    }
  }
}

function setHeaderTitle(viewName) {
  const el = $("header-title");
  if (el) el.textContent = VIEW_TITLES[viewName] || viewName || "实时订单流";
}

// ── Mobile drawer navigation ─────────────────────────────────
function setMobileNavOpen(open) {
  const backdrop = $("sidebar-backdrop");
  const buttons = [$("btn-nav-toggle"), $("btn-topnav-drawer")].filter(Boolean);
  document.body.classList.toggle("nav-open", !!open);
  if (backdrop) backdrop.hidden = !open;
  buttons.forEach((btn) => btn.setAttribute("aria-expanded", open ? "true" : "false"));
}

function bindMobileNav() {
  const buttons = [$("btn-nav-toggle"), $("btn-topnav-drawer")].filter(Boolean);
  const backdrop = $("sidebar-backdrop");
  buttons.forEach((btn) => {
    if (btn.dataset.navDrawerBound === "true") return;
    btn.dataset.navDrawerBound = "true";
    btn.addEventListener("click", () => setMobileNavOpen(!document.body.classList.contains("nav-open")));
  });
  if (backdrop) {
    backdrop.addEventListener("click", () => setMobileNavOpen(false));
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && document.body.classList.contains("nav-open")) setMobileNavOpen(false);
  });
  const sidebar = $("sidebar");
  if (sidebar) {
    sidebar.addEventListener("click", (ev) => {
      if (window.innerWidth > 1100) return;
      if (ev.target.closest(".sb-nav-item, .account-item")) setMobileNavOpen(false);
    });
  }
  window.addEventListener("resize", () => {
    if (window.innerWidth > 1100 && document.body.classList.contains("nav-open")) setMobileNavOpen(false);
  });
}

function bindNavigation() {
  document.querySelectorAll("[data-view].sb-nav-item, [data-view].sidebar-nav-item").forEach((btn) => {
    if (btn.dataset && btn.dataset.navBound === "true") return;
    btn.addEventListener("click", () => {
      const viewName = btn.getAttribute("data-view");
      activateView(viewName, { syncNav: true, updateTitle: true });
    });
    if (btn.dataset) btn.dataset.navBound = "true";
  });
}

function ensureViewData(viewName) {
  if (viewName === "system-center") {
    loadTelegramConfig().catch(() => {});
    loadFollowSettings().then(() => renderFollowForm()).catch(() => {});
    if (typeof window.loadTelegramSignalPopup === "function") window.loadTelegramSignalPopup();
  }
  if (viewName === "telegram") loadTelegramConfig().catch(() => {});
  if (viewName === "signal-pull") loadSignalPullConfig().catch(() => {});
  if (viewName === "tg-signal" && typeof window.loadTelegramSignalPopup === "function") {
    window.loadTelegramSignalPopup();
  }
  if (viewName === "follow-settings") {
    loadFollowSettings().then(() => renderFollowForm()).catch(() => {});
  }
  if (viewName === "arsenal") renderPoolManagement();
}

function activateView(viewName, options = {}) {
  const { syncNav = true, updateTitle = true } = options;
  state.view = viewName;
  if (document.body) document.body.dataset.view = viewName || "orders";
  applyViewVisibility();
  if (syncNav) {
    document.querySelectorAll(".sb-nav-item, .sidebar-nav-item").forEach((b) => {
      const same = b.getAttribute("data-view") === viewName;
      b.classList.toggle("active", !!same);
    });
  }
  if (updateTitle) setHeaderTitle(viewName);
  ensureViewData(viewName);
  if (viewName === "system-center") renderSystemCenterStatusBar();
}

// ── Init ───────────────────────────────────────────────────────
async function loadInitial(opts = {}) {
  if (document.body) document.body.dataset.view = state.view || "orders";
  // Bind navigation before any API request so a slow or unavailable data source
  // cannot leave the first screen's buttons inert.
  bindNavigation();
  bindMobileNav();
  bindSystemCenter();
  bindPoolTabs();
  bindAddNodeModalControls();
  bindMemberModalControls();
  bindOrderDashboardControls();
  bindExecutionRailControls();
  renderOrderDashboard();
  // Bind the engine control before any slow account/odds request. The
  // handler is idempotent and the button remains safe while status is unknown.
  bindFollowEngineToggle();
  if (typeof window.bindTelegramSignalPopup === "function") {
    window.bindTelegramSignalPopup();
  }
  state.matchSearch.retained = loadRetainedHgMatches();
  renderFollowEngineControls();
  const updateTopnavClock = () => {
    const text = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    const clock = $("topnav-clock");
    if (clock) clock.textContent = text;
    if ($("system-current-time")) $("system-current-time").textContent = text;
  };
  updateTopnavClock();
  window.setInterval(updateTopnavClock, 1000);
  await loadSignalPullConfig();
  await loadAccounts();
  await loadStatuses();
  // Render the member pool independently before order enrichment. A slow
  // live-odds request must not make the account pool appear empty.
  await loadArsenal();
  await refreshView();

  document.querySelectorAll(".top-config-btn[data-config-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const viewName = btn.getAttribute("data-config-view");
      if (viewName === "signal-pull") {
        activateView("signal-pull", { syncNav: true, updateTitle: true });
      } else {
        openSystemCenter(viewName);
      }
    });
  });
  $("btn-refresh")?.addEventListener("click", () => refreshView({ manual: true }));
  bindFollowEngineToggle();
  bindOrderDashboardControls();
  bindExecutionRailControls();

  $("hg-match-query")?.addEventListener("input", (ev) => {
    state.matchSearch.query = ev.target.value.trim();
    if (!state.matchSearch.query) {
      state.matchSearch.results = [];
      state.matchSearch.error = "";
      state.matchSearch.loading = false;
      renderOrdersView();
      return;
    }
    scheduleHgMatchSearch();
  });
  $("hg-match-query")?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      if (matchSearchDebounceTimer) window.clearTimeout(matchSearchDebounceTimer);
      searchHgMatches().catch(() => {});
    }
  });
  $("hg-match-mode")?.addEventListener("change", (ev) => {
    state.matchSearch.mode = ev.target.value || "all";
    if (state.matchSearch.query) searchHgMatches().catch(() => {});
    else renderOrdersView();
  });
  $("hg-match-clear-search")?.addEventListener("click", () => {
    state.matchSearch.query = "";
    state.matchSearch.results = [];
    state.matchSearch.error = "";
    state.matchSearch.loading = false;
    resetReadLaneMembers();
    if ($("hg-match-query")) $("hg-match-query").value = "";
    renderOrdersView();
  });

  // Search
  $("search")?.addEventListener("input", (e) => {
    state.search = e.target.value.trim();
    renderOrdersView();
  });

  // Account select
  $("account")?.addEventListener("change", (e) => {
    state.selectedTarget = e.target.value;
    refreshView();
  });

  // Follow settings
  $("follow-mode")?.addEventListener("change", applyFollowMode);
  $("follow-split-delay")?.addEventListener("input", syncFollowSplitDelayOutput);
  $("follow-split-delay")?.addEventListener("change", syncFollowSplitDelayOutput);
  bindFollowFormDirtyTracking();
  $("follow-save")?.addEventListener("click", saveFollowSettings);
  $("follow-refresh-settings")?.addEventListener("click", async () => {
    await loadAccounts();
    await loadFollowSettings();
    renderFollowForm({ force: true });
    toast("策略配置已刷新", "info");
  });

  // 反单按钮
  ["rev-all", "rev-total", "rev-handicap"].forEach(id => {
    $(id)?.addEventListener("click", () => {
      const btn = $(id);
      const active = !btn.classList.contains("active");
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      const stEl = btn.querySelector(".rev-btn-state");
      if (stEl) stEl.textContent = active ? "已开启" : "关闭";
      markFollowFormDirty();
    });
  });

  // Telegram
  $("tg-save")?.addEventListener("click", saveTelegramConfig);
  $("tg-test")?.addEventListener("click", testTelegram);
  $("tg-clear")?.addEventListener("click", clearTelegramConfig);

  // Signal pull
  $("signal-pull-save")?.addEventListener("click", saveSignalPullConfig);

  // Report
  $("btn-report-refresh")?.addEventListener("click", () => loadReportData().catch(() => {}));
  document.querySelectorAll(".rpt-range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".rpt-range-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      reportState.range = btn.dataset.range || "today";
      renderReport();
    });
  });

  // Auto refresh
  setInterval(() => {
    const autoRefresh = $("auto");
    if (!autoRefresh || autoRefresh.checked) {
      refreshView().catch(() => {});
    }
  }, 2000);
  setInterval(() => {
    if (state.view === "orders") updateVisibleMatchClocks();
  }, 1000);

  // Preload telegram and signal config
  loadTelegramConfig().catch(() => {});
  loadSignalPullConfig().catch(() => {});
}

window.addEventListener("DOMContentLoaded", () => { loadInitial(); });
