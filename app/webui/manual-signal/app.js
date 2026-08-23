const state = {
  member: null,
  games: [],
  selectedGame: null,
  odds: null,
  selectedPick: null,
  previewPayload: null,
  restoringSession: false,
};

const SESSION_KEY = "manualSignalSession";

const $ = (id) => document.getElementById(id);

function text(id, value) {
  const el = $(id);
  if (el) el.textContent = value == null || value === "" ? "-" : String(value);
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => null);
  if (!res.ok || (data && data.ok === false)) {
    const msg = data && (data.message || data.error) ? data.message || data.error : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function selectedMode() {
  return $("selMode").value || "live";
}

function modeLabel(mode) {
  if (mode === "today") return "今日";
  if (mode === "early") return "早盘";
  return "滚球";
}

function trimValue(id) {
  return String($(id).value || "").trim();
}

function matchText(game) {
  return `${game && game.teamH ? game.teamH : ""} v ${game && game.teamC ? game.teamC : ""}`.trim();
}

function statusLabel(status) {
  const s = String(status || "").trim().toLowerCase();
  if (s === "online") return "在线，可读取比赛";
  if (s === "relogin" || s === "login" || s === "logging_in") return "登录中，请在浏览器完成登录";
  if (s === "offline") return "会话失效，请点击登录会员重新登录";
  if (s === "unknown" || !s) return "未检查，请点击检查心跳/会话";
  return `状态异常：${status}`;
}

function memberHint(m) {
  if (!m) return "请先输入会员账号并点击登录会员";
  const raw = String(m.lastError || m.last_error || "").trim();
  if (raw) return raw;
  const s = String(m.status || "").trim().toLowerCase();
  if (s === "online") return "心跳正常，会话可用";
  if (s === "offline" || s === "unknown") return "会话失效，请点击登录会员重新登录";
  if (s === "relogin" || s === "login" || s === "logging_in") return "正在登录，请在弹出的浏览器完成登录";
  return "";
}

function renderMember(m) {
  text("stMember", m ? (m.label || m.username || m.id) : "会员未登录");
  text("memberStatus", statusLabel(m && m.status));
  text("memberBase", m && m.base_url);
  text("memberUid", m && m.uidMasked);
  text("memberError", memberHint(m));
}

function memberId() {
  return state.member && state.member.id ? String(state.member.id) : "";
}

function safeStorage() {
  try {
    return window.localStorage || null;
  } catch (e) {
    return null;
  }
}

function saveSessionText(value) {
  const textValue = String(value || "");
  const storage = safeStorage();
  if (storage) {
    storage.setItem(SESSION_KEY, textValue);
    return;
  }
  document.cookie = `${SESSION_KEY}=${encodeURIComponent(textValue)}; Max-Age=2592000; Path=/manual-signal/; SameSite=Lax`;
}

function loadSessionText() {
  const storage = safeStorage();
  if (storage) return storage.getItem(SESSION_KEY);
  const prefix = `${SESSION_KEY}=`;
  const item = String(document.cookie || "")
    .split(";")
    .map((x) => x.trim())
    .find((x) => x.startsWith(prefix));
  if (!item) return "";
  try {
    return decodeURIComponent(item.slice(prefix.length));
  } catch (e) {
    return "";
  }
}

function clearSessionText() {
  const storage = safeStorage();
  if (storage) storage.removeItem(SESSION_KEY);
  document.cookie = `${SESSION_KEY}=; Max-Age=0; Path=/manual-signal/; SameSite=Lax`;
}

function saveSessionState() {
  if (!state.member || !state.member.id) return;
  const payload = {
    member: {
      id: state.member.id,
      label: state.member.label || "",
      username: state.member.username || trimValue("inUsername"),
      base_url: state.member.base_url || trimValue("inBaseUrl"),
      status: state.member.status || "unknown",
      uidMasked: state.member.uidMasked || "",
      lastError: state.member.lastError || "",
    },
    form: {
      base_url: trimValue("inBaseUrl") || state.member.base_url || "",
      username: trimValue("inUsername") || state.member.username || "",
      backend_name: trimValue("inBackendName") || "manual",
      mode: selectedMode(),
      amount: $("inAmount").value || "1000",
      order_id: trimValue("inOrderId"),
      search: $("inSearch").value || "",
    },
    saved_at: Date.now(),
  };
  saveSessionText(JSON.stringify(payload));
}

function clearSessionState() {
  clearSessionText();
}

function resetMarketState() {
  state.games = [];
  state.selectedGame = null;
  state.odds = null;
  state.selectedPick = null;
  state.previewPayload = null;
  renderGames();
  renderOdds();
  renderSelection();
  renderPayload({});
}

function resetLoginStateForInputChange() {
  if (state.restoringSession) return;
  if (!state.member && !state.games.length && !state.selectedGame && !state.odds && !state.selectedPick) {
    return;
  }
  state.member = null;
  clearSessionState();
  resetMarketState();
  renderMember(null);
  text("memberStatus", "账号已修改");
  text("memberError", "已修改会员端网址/账号/密码，请重新点击登录会员");
}

function loginInput() {
  const baseUrl = trimValue("inBaseUrl");
  const username = trimValue("inUsername");
  const password = trimValue("inPassword");
  if (!baseUrl) throw new Error("请填写会员端网址");
  if (!username) throw new Error("请填写会员账号");
  if (!password) throw new Error("请填写会员密码");
  return {
    label: `manual-signal-${username}`,
    base_url: baseUrl,
    username,
    password,
    backend_name: $("inBackendName").value || "manual",
    usage: "read",
  };
}

async function checkMember() {
  if (!memberId()) throw new Error("请先登录会员");
  text("memberStatus", "正在检查心跳/会话");
  text("memberError", "正在确认会员会话是否可用");
  const data = await fetchJson(`/api/chashui/status?member_id=${encodeURIComponent(memberId())}`);
  const member = data.member || (Array.isArray(data.members) ? data.members.find((m) => String(m.id) === memberId()) : null);
  if (member) {
    state.member = { ...state.member, ...member };
    renderMember(state.member);
    saveSessionState();
  } else if (data.status) {
    state.member = { ...state.member, status: data.status, lastError: data.message || "" };
    renderMember(state.member);
    saveSessionState();
  }
  return data;
}

async function restoreSessionState() {
  const raw = loadSessionText();
  if (!raw) return restoreServerSessionState();
  let saved = null;
  try {
    saved = JSON.parse(raw);
  } catch (e) {
    clearSessionText();
    return restoreServerSessionState();
  }
  const member = saved && saved.member ? saved.member : null;
  if (!member || !member.id) {
    clearSessionText();
    return restoreServerSessionState();
  }
  const form = saved.form || {};
  state.restoringSession = true;
  try {
    if (form.base_url) $("inBaseUrl").value = form.base_url;
    if (form.username) $("inUsername").value = form.username;
    if (form.backend_name) $("inBackendName").value = form.backend_name;
    if (form.mode) $("selMode").value = form.mode;
    if (form.amount) $("inAmount").value = form.amount;
    if (form.order_id) $("inOrderId").value = form.order_id;
    if (form.search) $("inSearch").value = form.search;
  } finally {
    state.restoringSession = false;
  }
  state.member = {
    id: String(member.id || ""),
    label: member.label || member.username || member.id,
    username: member.username || "",
    base_url: member.base_url || form.base_url || "",
    status: member.status || "unknown",
    uidMasked: member.uidMasked || "",
    lastError: "已恢复上次会员会话，正在检查心跳",
  };
  renderMember(state.member);
  text("stMode", modeLabel(selectedMode()));
  try {
    await checkMember();
  } catch (e) {
    state.member = { ...state.member, status: "offline", lastError: "上次会话不可用，请点击登录会员重新登录" };
    renderMember(state.member);
    saveSessionState();
  }
  return true;
}

async function restoreServerSessionState() {
  let data = null;
  try {
    data = await fetchJson("/api/manual-signal/session");
  } catch (e) {
    return false;
  }
  const member = data && data.member ? data.member : null;
  if (!member || !member.id) return false;
  state.restoringSession = true;
  try {
    if (member.base_url) $("inBaseUrl").value = member.base_url;
    if (member.username) $("inUsername").value = member.username;
    if (!$("inBackendName").value) $("inBackendName").value = "manual";
  } finally {
    state.restoringSession = false;
  }
  state.member = {
    id: String(member.id || ""),
    label: member.label || member.username || member.id,
    username: member.username || "",
    base_url: member.base_url || "",
    status: member.status || "unknown",
    uidMasked: member.uidMasked || "",
    lastError: "已恢复上次会员会话，正在检查心跳",
  };
  renderMember(state.member);
  try {
    await checkMember();
  } catch (e) {
    state.member = { ...state.member, status: "offline", lastError: "上次会话不可用，请点击登录会员重新登录" };
    renderMember(state.member);
    saveSessionState();
  }
  return true;
}

function filterGames() {
  const q = String($("inSearch").value || "").trim().toLowerCase();
  if (!q) return state.games;
  return state.games.filter((g) => [g.gid, g.league, g.teamH, g.teamC, g.score, g.time].join(" ").toLowerCase().includes(q));
}

function renderGames() {
  const tbody = $("tblGames").querySelector("tbody");
  tbody.innerHTML = "";
  const rows = filterGames();
  text("gameCount", `${rows.length}/${state.games.length}`);
  for (const g of rows) {
    const tr = document.createElement("tr");
    if (state.selectedGame && String(state.selectedGame.gid) === String(g.gid)) tr.classList.add("selected");
    tr.innerHTML = `
      <td class="mono">${g.gid || ""}</td>
      <td>${g.league || ""}</td>
      <td>${matchText(g)}</td>
      <td class="mono">${g.score || ""}</td>
      <td class="mono">${g.time || ""}</td>
      <td><button class="btn ghost" type="button">选择</button></td>
    `;
    tr.querySelector("button").addEventListener("click", () => selectGameAndLoadOdds(g));
    tbody.appendChild(tr);
  }
}

async function selectGameAndLoadOdds(game) {
  state.selectedGame = game;
  state.odds = null;
  state.selectedPick = null;
  state.previewPayload = null;
  renderGames();
  renderOdds();
  renderSelection();
  renderPayload({});
  text("stPush", "正在读取盘口 JSON");
  try {
    await loadOdds();
  } catch (e) {
    text("stPush", e.message || "盘口读取失败");
    renderList("handicapBox", [], "盘口读取失败，请重试");
    renderList("overUnderBox", [], "盘口读取失败，请重试");
    renderList("moneyLineBox", [], "盘口读取失败，请重试");
  }
}

async function loadGames() {
  const currentMemberId = memberId();
  if (!currentMemberId) throw new Error("请先登录会员");
  const mode = selectedMode();
  text("stMode", modeLabel(mode));
  const data = await fetchJson(`/api/chashui/games?member_id=${encodeURIComponent(currentMemberId)}&mode=${encodeURIComponent(mode)}`);
  state.games = Array.isArray(data.games) ? data.games : [];
  state.selectedGame = null;
  state.odds = null;
  state.selectedPick = null;
  renderGames();
  renderSelection();
}

async function loginMember() {
  const input = loginInput();
  state.member = null;
  resetMarketState();
  renderMember({
    label: input.label,
    username: input.username,
    base_url: input.base_url,
    status: "relogin",
    uidMasked: "",
    lastError: "正在保存当前账号并停用旧手动登录任务",
  });
  text("memberStatus", "发起登录");
  const data = await fetchJson("/api/manual-signal/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  const member = data.member || {};
  if (!String(data.id || member.id || "")) throw new Error("会员保存成功但没有返回 id");
  state.member = {
    id: String(data.id || member.id || ""),
    label: member.label || input.label,
    username: member.username || input.username,
    base_url: member.base_url || input.base_url,
    status: member.status || "relogin",
    uidMasked: member.uidMasked || "",
    lastError: data.core_started ? "已启动后台并发起登录，请在弹出的浏览器完成当前账号登录" : "已发起登录，请在弹出的浏览器完成当前账号登录",
  };
  if (data.disabled_old_manual_members) {
    state.member.lastError += `；已停用 ${data.disabled_old_manual_members} 个旧手动账号登录任务`;
  }
  renderMember(state.member);
  saveSessionState();
}

async function loadOdds() {
  const currentMemberId = memberId();
  if (!currentMemberId) throw new Error("请先登录会员");
  if (!state.selectedGame || !state.selectedGame.gid) throw new Error("请先选择比赛");
  const mode = selectedMode();
  text("stPush", "正在读取盘口 JSON");
  const data = await fetchJson(
    `/api/chashui/odds?member_id=${encodeURIComponent(currentMemberId)}&gid=${encodeURIComponent(state.selectedGame.gid)}&mode=${encodeURIComponent(mode)}&confirm=1&raw=1`
  );
  state.odds = data;
  state.selectedPick = null;
  renderSelection();
  renderOdds();
  const simple = data && data.simple ? data.simple : {};
  const count =
    (((simple.handicap || {}).lines || []).length || 0) +
    (((simple.overUnder || {}).lines || []).length || 0) +
    ((simple.moneyLine && !simple.moneyLine.empty) ? 1 : 0);
  text("stPush", count ? "盘口读取成功" : "未读取到盘口，请稍后重试或切换盘口模式");
}

function makePick(kind, label, line, odd, extra = {}) {
  const livePrefix = modeLabel(selectedMode()) === "滚球" ? "(滚球) " : "";
  const betTypeMap = {
    handicap: `${livePrefix}让球`,
    overUnder: `${livePrefix}大 / 小`,
    halfHandicap: `${livePrefix}上半场让球`,
    halfOverUnder: `${livePrefix}上半场大 / 小`,
    moneyLine: `${livePrefix}独赢`,
  };
  return {
    kind,
    label: label || "",
    line: line || "",
    odds: odd || "",
    bet_type: betTypeMap[kind] || `${livePrefix}独赢`,
    ...extra,
  };
}

function pickButton(pick) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pick";
  const key = `${pick.kind}|${pick.label}|${pick.line}|${pick.odds}`;
  const selected = state.selectedPick && `${state.selectedPick.kind}|${state.selectedPick.label}|${state.selectedPick.line}|${state.selectedPick.odds}` === key;
  if (selected) btn.classList.add("selected");
  btn.innerHTML = `
    <div class="pick-top"><span>${pick.label}</span><span>${pick.line}</span></div>
    <div class="odd">${pick.odds}</div>
  `;
  btn.addEventListener("click", () => {
    state.selectedPick = pick;
    state.previewPayload = null;
    renderSelection();
    renderOdds();
    renderPayload({});
  });
  return btn;
}

function renderList(id, children, emptyText) {
  const box = $(id);
  box.innerHTML = "";
  if (!children.length) {
    const div = document.createElement("div");
    div.className = "chip";
    div.textContent = emptyText;
    box.appendChild(div);
    return;
  }
  children.forEach((child) => box.appendChild(child));
}

function renderOdds() {
  const simple = state.odds && state.odds.simple ? state.odds.simple : null;
  if (!simple) {
    renderList("handicapBox", [], "暂无让球");
    renderList("overUnderBox", [], "暂无大小");
    renderList("halfHandicapBox", [], "暂无上半场让球");
    renderList("halfOverUnderBox", [], "暂无上半场大小");
    renderList("moneyLineBox", [], "暂无独赢");
    return;
  }

  const handicap = [];
  for (const row of (simple.handicap && simple.handicap.lines) || []) {
    if (row.home) handicap.push(pickButton(makePick("handicap", row.home.team, `${row.home.team || ""} ${row.home.line || ""}`.trim(), row.home.odd, { wtype: row.wtype, side: "H", gid: row.gid || "" })));
    if (row.away) handicap.push(pickButton(makePick("handicap", row.away.team, `${row.away.team || ""} ${row.away.line || ""}`.trim(), row.away.odd, { wtype: row.wtype, side: "C", gid: row.gid || "" })));
  }

  const overUnder = [];
  for (const row of (simple.overUnder && simple.overUnder.lines) || []) {
    if (row.over) overUnder.push(pickButton(makePick("overUnder", row.over.label, `${row.over.label || "大"} ${row.over.line || ""}`.trim(), row.over.odd, { wtype: row.wtype, side: "C", gid: row.gid || "" })));
    if (row.under) overUnder.push(pickButton(makePick("overUnder", row.under.label, `${row.under.label || "小"} ${row.under.line || ""}`.trim(), row.under.odd, { wtype: row.wtype, side: "H", gid: row.gid || "" })));
  }

  const halfHandicap = [];
  for (const row of (simple.halfHandicap && simple.halfHandicap.lines) || []) {
    if (row.home) halfHandicap.push(pickButton(makePick("halfHandicap", row.home.team, `${row.home.team || ""} ${row.home.line || ""}`.trim(), row.home.odd, { wtype: row.wtype, side: "H", gid: row.gid || "" })));
    if (row.away) halfHandicap.push(pickButton(makePick("halfHandicap", row.away.team, `${row.away.team || ""} ${row.away.line || ""}`.trim(), row.away.odd, { wtype: row.wtype, side: "C", gid: row.gid || "" })));
  }

  const halfOverUnder = [];
  for (const row of (simple.halfOverUnder && simple.halfOverUnder.lines) || []) {
    if (row.over) halfOverUnder.push(pickButton(makePick("halfOverUnder", row.over.label, `${row.over.label || "大"} ${row.over.line || ""}`.trim(), row.over.odd, { wtype: row.wtype, side: "C", gid: row.gid || "" })));
    if (row.under) halfOverUnder.push(pickButton(makePick("halfOverUnder", row.under.label, `${row.under.label || "小"} ${row.under.line || ""}`.trim(), row.under.odd, { wtype: row.wtype, side: "H", gid: row.gid || "" })));
  }

  const moneyLine = [];
  const ml = simple.moneyLine || {};
  if (!ml.empty) {
    if (ml.home) moneyLine.push(pickButton(makePick("moneyLine", ml.home.team, ml.home.team, ml.home.odd, { wtype: "RM", side: "H", gid: ml.gid || "" })));
    if (ml.draw) moneyLine.push(pickButton(makePick("moneyLine", ml.draw.label, ml.draw.label, ml.draw.odd, { wtype: "RM", side: "N", gid: ml.gid || "" })));
    if (ml.away) moneyLine.push(pickButton(makePick("moneyLine", ml.away.team, ml.away.team, ml.away.odd, { wtype: "RM", side: "C", gid: ml.gid || "" })));
  }

  renderList("handicapBox", handicap, "暂无让球");
  renderList("overUnderBox", overUnder, "暂无大小");
  renderList("halfHandicapBox", halfHandicap, "暂无上半场让球");
  renderList("halfOverUnderBox", halfOverUnder, "暂无上半场大小");
  renderList("moneyLineBox", moneyLine, "暂无独赢");
  renderMeta();
}

function renderMeta() {
  const meta = state.odds && state.odds.meta ? state.odds.meta : {};
  const rows = [
    ["联赛", meta.league],
    ["主队", meta.teamH],
    ["客队", meta.teamC],
    ["比分", meta.score],
    ["主盘GID", meta.masterGid],
    ["LID", meta.lid],
    ["ECID", meta.ecid],
    ["时间", meta.reTime || meta.datetime],
  ];
  $("metaBox").innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><strong>${v || "-"}</strong></div>`).join("");
}

function renderSelection() {
  text("selectedGame", state.selectedGame ? `${state.selectedGame.league || ""} ${matchText(state.selectedGame)}` : "未选择比赛");
  text("selectedPick", state.selectedPick ? `${state.selectedPick.bet_type} ${state.selectedPick.line} @${state.selectedPick.odds}` : "未选择盘口");
}

function buildPayloadInput() {
  if (!state.selectedGame) throw new Error("请先选择比赛");
  if (!state.selectedPick) throw new Error("请先选择盘口");
  const meta = (state.odds && state.odds.meta) || {};
  const amount = Number($("inAmount").value || 0);
  return {
    backend_name: $("inBackendName").value || "manual",
    order_id: trimValue("inOrderId"),
    league: meta.league || state.selectedGame.league || "",
    match: `${meta.teamH || state.selectedGame.teamH || ""} v ${meta.teamC || state.selectedGame.teamC || ""}`.trim(),
    sport: "足球",
    mode: selectedMode(),
    bet_type: state.selectedPick.bet_type,
    line: state.selectedPick.line,
    odds: state.selectedPick.odds,
    amount,
    play: `${state.selectedPick.line} @${state.selectedPick.odds}`,
    gid: state.selectedPick.gid || meta.masterGid || state.selectedGame.gid || "",
    wtype: state.selectedPick.wtype || "",
    side: state.selectedPick.side || "",
    lid: meta.lid || state.selectedGame.lid || "",
    ecid: meta.ecid || state.selectedGame.ecid || "",
    team_h: meta.teamH || state.selectedGame.teamH || "",
    team_c: meta.teamC || state.selectedGame.teamC || "",
    match_time: meta.reTime || state.selectedGame.time || "",
    match_datetime: meta.datetime || "",
    raw: state.odds && state.odds.raw ? state.odds.raw : {},
  };
}

function renderPayload(payload) {
  $("payloadBox").textContent = JSON.stringify(payload || {}, null, 2);
}

async function previewPayload() {
  const payloadInput = buildPayloadInput();
  const data = await fetchJson("/api/manual-signal/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadInput),
  });
  state.previewPayload = data.payload;
  renderPayload(data.payload);
}

async function pushSignal() {
  const payloadInput = buildPayloadInput();
  text("stPush", "推送中");
  const data = await fetchJson("/api/manual-signal/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payloadInput),
  });
  state.previewPayload = data.payload;
  renderPayload(data.payload);
  text("stPush", data.remote && data.remote.id ? `已推送 #${data.remote.id}` : "已推送");
}

function bind() {
  $("btnCheckMember").addEventListener("click", () => checkMember().catch((e) => text("memberError", e.message)));
  $("btnLoginMember").addEventListener("click", () => loginMember().catch((e) => text("memberError", e.message)));
  $("btnLoadGames").addEventListener("click", () => loadGames().catch((e) => text("stPush", e.message)));
  $("btnLoadOdds").addEventListener("click", () => loadOdds().catch((e) => text("stPush", e.message)));
  $("btnPreview").addEventListener("click", () => previewPayload().catch((e) => text("stPush", e.message)));
  $("btnPush").addEventListener("click", () => pushSignal().catch((e) => text("stPush", e.message)));
  $("selMode").addEventListener("change", () => {
    text("stMode", modeLabel(selectedMode()));
    resetMarketState();
  });
  ["inBaseUrl", "inUsername", "inPassword"].forEach((id) => {
    $(id).addEventListener("input", resetLoginStateForInputChange);
    $(id).addEventListener("change", resetLoginStateForInputChange);
  });
  $("inSearch").addEventListener("input", renderGames);
  $("inAmount").addEventListener("input", () => {
    state.previewPayload = null;
  });
  $("inOrderId").addEventListener("input", () => {
    state.previewPayload = null;
    saveSessionState();
  });
}

async function main() {
  bind();
  renderOdds();
  renderSelection();
  renderMember(null);
  await restoreSessionState();
}

main().catch((e) => text("stPush", e.message || String(e)));
