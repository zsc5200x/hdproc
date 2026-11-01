(function () {
  "use strict";

  const state = {
    status: null,
    chats: [],
    chatsLoaded: false,
    events: [],
    dryRun: true,
    modeDirty: false,
    sessionValid: null,
    bound: false,
    refreshTimer: 0,
  };
  const LOG_REFRESH_INTERVAL_MS = 1000;
  const byId = (id) => document.getElementById(id);

  const messages = {
    credentials_missing: "软件预置凭据缺失",
    telegram_login_required: "请先登录 Telegram 账号",
    source_chats_missing: "请至少选择一个来源群",
    telegram_session_expired: "Telegram 会话已失效，请重新登录",
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

  function explain(error) {
    const raw = String(error && error.message ? error.message : error || "操作失败");
    return messages[raw] || raw;
  }

  function notify(message, tone = "info") {
    if (typeof window.toast === "function") {
      window.toast(message, tone);
      return;
    }
    console[tone === "error" ? "error" : "log"](message);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function setBusy(button, busy) {
    if (!button) return;
    if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
    button.disabled = !!busy;
    button.innerHTML = busy
      ? '<i class="fa-solid fa-spinner fa-spin"></i>处理中'
      : button.dataset.idleHtml;
  }

  function setHint(id, message, tone = "") {
    const node = byId(id);
    if (!node) return;
    node.textContent = message;
    node.className = `tg-signal-hint ${tone}`.trim();
  }

  function setStatus(id, label, tone) {
    const node = byId(id);
    if (!node) return;
    const value = node.querySelector("b");
    if (value) value.textContent = label;
    node.dataset.tone = tone;
  }

  function selectedChatIds() {
    return [...document.querySelectorAll("#tg-signal-group-list input[type=checkbox]:checked")]
      .map((node) => String(node.value));
  }

  function updateSelectedCount() {
    const node = byId("tg-signal-selected-count");
    if (node) node.textContent = `已选择 ${selectedChatIds().length} 个`;
    renderRuntimeStatus();
  }

  function canStartListening() {
    const data = state.status || {};
    const savedIds = data.settings?.source_chat_ids || [];
    const hasSources = selectedChatIds().length > 0 || savedIds.length > 0;
    return !!data.credentials_configured
      && !!data.session_configured
      && state.sessionValid !== false
      && hasSources;
  }

  function isSessionFailure(error) {
    const reason = String(error && error.message ? error.message : error || "").trim();
    return reason === "telegram_login_required" || reason === "telegram_session_expired";
  }

  function updateMode(dryRun, { markDirty = false } = {}) {
    state.dryRun = !!dryRun;
    if (markDirty) state.modeDirty = true;
    byId("tg-signal-dry-run")?.setAttribute("aria-pressed", String(state.dryRun));
    byId("tg-signal-automatic")?.setAttribute("aria-pressed", String(!state.dryRun));
    setHint(
      "tg-signal-action-hint",
      state.dryRun
        ? "干跑检查只解析和匹配信号，不产生跟单订单。"
        : "自动跟单已选择，启动监听后，匹配成功的信号会进入现有跟单系统。",
      state.dryRun ? "" : "error"
    );
    renderRuntimeStatus();
  }

  function renderRuntimeStatus() {
    const data = state.status || {};
    const alive = data.worker_alive == null ? !!data.worker_running : !!data.worker_alive;
    const running = !!data.worker_running;
    const unhealthy = alive && !running;
    const mode = state.dryRun ? "干跑检查" : "自动跟单";
    setStatus(
      "tg-signal-worker-status",
      running ? `监听中 · ${mode}` : unhealthy ? `连接异常 · ${mode}` : `已关闭 · ${mode}`,
      running ? "ok" : unhealthy ? "error" : "off"
    );
    const live = byId("tg-signal-live-label");
    if (live) {
      live.classList.toggle("running", running);
      live.innerHTML = `<i></i>${running ? `监听中 · PID ${data.worker_pid || "-"}` : unhealthy ? `连接异常 · PID ${data.worker_pid || "-"}` : "未监听"}`;
    }
    const start = byId("tg-signal-start");
    const stop = byId("tg-signal-stop");
    const dryRunMode = byId("tg-signal-dry-run");
    const automaticMode = byId("tg-signal-automatic");
    const ready = canStartListening();
    if (start) start.disabled = alive || !ready;
    if (stop) stop.disabled = !alive;
    if (dryRunMode) dryRunMode.disabled = alive;
    if (automaticMode) automaticMode.disabled = alive;
    const overall = byId("sc-tg-overall-status");
    if (overall) {
      overall.textContent = running ? "运行中" : unhealthy ? "连接异常" : (ready ? "已就绪" : "待配置");
      overall.className = running ? "badge badge-success" : "badge badge-dim";
    }
    if (!alive && !ready) {
      const missing = [];
      if (!data.credentials_configured) missing.push("API 凭据");
      if (!data.session_configured || state.sessionValid === false) missing.push("有效会话");
      if (!selectedChatIds().length && !(data.settings?.source_chat_ids || []).length) missing.push("来源群");
      setHint("tg-signal-action-hint", `启动前请完成：${missing.join("、")}。`, "");
    } else if (unhealthy) {
      setHint("tg-signal-action-hint", `监听进程仍在，但 Telegram 连接不健康：${data.worker_error || "等待自动恢复"}`, "error");
    }
  }

  function renderStatus() {
    const data = state.status || {};
    const settings = data.settings || {};
    const connected = !!data.session_configured;
    const selected = (settings.source_chat_ids || []).map(String);
    if (!state.modeDirty) state.dryRun = settings.dry_run !== false;

    setStatus(
      "tg-signal-credential-status",
      data.credentials_configured ? "已预置" : "预置缺失",
      data.credentials_configured ? "ok" : "error"
    );
    if (!connected) {
      setStatus("tg-signal-session-status", "未登录", "off");
    } else if (state.sessionValid === true) {
      setStatus("tg-signal-session-status", data.phone_masked ? `${data.phone_masked} · 有效` : "会话有效", "ok");
    } else if (state.sessionValid === false) {
      setStatus("tg-signal-session-status", "会话失效", "error");
    } else {
      setStatus("tg-signal-session-status", data.phone_masked || "已保存 · 待验证", "pending");
    }
    setStatus("tg-signal-source-status", selected.length ? `已保存 ${selected.length} 个` : "未保存", selected.length ? "ok" : "off");

    const loginForm = byId("tg-signal-login-form");
    const connectedRow = byId("tg-signal-connected");
    if (loginForm) loginForm.hidden = connected;
    if (connectedRow) connectedRow.hidden = !connected;
    if (byId("tg-signal-account")) byId("tg-signal-account").textContent = data.phone_masked || "Telegram 已登录";
    if (byId("tg-signal-session-detail")) {
      byId("tg-signal-session-detail").textContent = state.sessionValid === false ? "会话无效，请登出后重新验证" : "会话仅保存在本机";
    }

    const canReadChats = connected && state.sessionValid !== false;
    if (byId("tg-signal-reload-groups")) byId("tg-signal-reload-groups").disabled = !canReadChats;
    if (byId("tg-signal-save-groups")) byId("tg-signal-save-groups").disabled = !connected;
    updateMode(state.dryRun);
  }

  function renderChats() {
    const list = byId("tg-signal-group-list");
    if (!list) return;
    const query = String(byId("tg-signal-group-search")?.value || "").trim().toLowerCase();
    const selected = new Set((state.status?.settings?.source_chat_ids || []).map(String));
    const rows = state.chats.filter((chat) => `${chat.title || ""} ${chat.id || ""}`.toLowerCase().includes(query));
    list.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "tg-signal-empty";
      empty.textContent = state.status?.session_configured ? "没有匹配的群组或频道" : "登录后读取可用群组";
      list.append(empty);
      updateSelectedCount();
      return;
    }
    for (const chat of rows) {
      const label = document.createElement("label");
      label.className = "tg-signal-group-row";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = String(chat.id);
      checkbox.checked = selected.has(String(chat.id));
      checkbox.addEventListener("change", updateSelectedCount);
      const copy = document.createElement("span");
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

  function formatTime(timestamp) {
    if (!timestamp) return "-";
    const value = Number(timestamp);
    const date = Number.isFinite(value) ? new Date(value * 1000) : new Date(timestamp);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
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
    const body = byId("tg-signal-event-rows");
    if (!body) return;
    if (byId("tg-signal-event-count")) byId("tg-signal-event-count").textContent = `${state.events.length} 条`;
    body.replaceChildren();
    if (!state.events.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.className = "tg-signal-empty";
      cell.textContent = "暂无解析记录";
      row.append(cell);
      body.append(row);
      return;
    }
    for (const item of state.events.slice(0, 150)) {
      const row = document.createElement("tr");
      const values = [
        formatTime(item.ts),
        item.chat_title || item.chat_id || "-",
        eventStatusLabel(item.status),
        [[item.league, item.match].filter(Boolean).join(" · "), item.line].filter(Boolean).join(" / ") || "-",
        item.amount || "-",
        eventDescription(item),
      ];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        if (index === 2) {
          const badge = document.createElement("span");
          const status = String(item.status || "").toLowerCase().replace(/[^a-z_]/g, "");
          badge.className = `tg-signal-event-status ${status}`.trim();
          badge.textContent = value;
          if (item.status) badge.title = `内部状态：${item.status}`;
          cell.append(badge);
        } else {
          cell.textContent = value;
        }
        row.append(cell);
      });
      body.append(row);
    }
  }

  async function loadStatus() {
    state.status = await api("/api/telegram-signal/status");
    if (!state.status.session_configured) state.sessionValid = null;
    renderStatus();
    return state.status;
  }

  async function loadChats() {
    try {
      const result = await api("/api/telegram-signal/chats");
      state.chats = result.chats || [];
      state.chatsLoaded = true;
      state.sessionValid = true;
      renderStatus();
      renderChats();
      return result;
    } catch (error) {
      state.chats = [];
      state.chatsLoaded = false;
      state.sessionValid = isSessionFailure(error) ? false : null;
      renderStatus();
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
    state.status = state.status || {};
    state.status.settings = result.settings;
    state.modeDirty = false;
    renderStatus();
    renderChats();
    return result;
  }

  async function loadTelegramSignalPopup() {
    try {
      await Promise.all([loadStatus(), loadLogs()]);
      if (state.status?.session_configured) {
        try {
          await loadChats();
        } catch (error) {
          setHint("tg-signal-login-hint", explain(error), "error");
        }
      } else {
        state.chats = [];
        state.chatsLoaded = false;
        renderChats();
      }
    } catch (error) {
      notify(`TG 状态读取失败：${explain(error)}`, "error");
    }
  }

  function bindTelegramSignalPopup() {
    if (state.bound || !byId("tg-signal-phone")) return;
    state.bound = true;

    byId("tg-signal-refresh")?.addEventListener("click", loadTelegramSignalPopup);
    byId("tg-signal-group-search")?.addEventListener("input", renderChats);
    byId("tg-signal-dry-run")?.addEventListener("click", () => updateMode(true, { markDirty: true }));
    byId("tg-signal-automatic")?.addEventListener("click", () => updateMode(false, { markDirty: true }));
    byId("tg-signal-reload-groups")?.addEventListener("click", () => loadChats().catch((error) => notify(explain(error), "error")));
    byId("tg-signal-save-groups")?.addEventListener("click", () => {
      saveSettings().then(() => notify("TG 来源群已保存", "success")).catch((error) => notify(explain(error), "error"));
    });

    byId("tg-signal-send-code")?.addEventListener("click", async () => {
      const button = byId("tg-signal-send-code");
      setBusy(button, true);
      try {
        const phone = String(byId("tg-signal-phone")?.value || "").trim().replace(/\s+/g, "");
        const result = await api("/api/telegram-signal/send-code", { method: "POST", body: JSON.stringify({ phone }) });
        setHint("tg-signal-login-hint", `验证码已发送到 ${result.phone_masked || "Telegram"}`, "ok");
      } catch (error) {
        setHint("tg-signal-login-hint", explain(error), "error");
      } finally {
        setBusy(button, false);
      }
    });

    byId("tg-signal-verify-code")?.addEventListener("click", async () => {
      const button = byId("tg-signal-verify-code");
      setBusy(button, true);
      try {
        const result = await api("/api/telegram-signal/verify-code", {
          method: "POST",
          body: JSON.stringify({
            code: String(byId("tg-signal-code")?.value || "").trim(),
            password: String(byId("tg-signal-password")?.value || ""),
          }),
        });
        if (result.needs_password) throw new Error("请输入二步验证密码后再次验证");
        if (byId("tg-signal-code")) byId("tg-signal-code").value = "";
        if (byId("tg-signal-password")) byId("tg-signal-password").value = "";
        state.sessionValid = null;
        await loadStatus();
        await loadChats();
        notify("Telegram 账号已登录", "success");
      } catch (error) {
        setHint("tg-signal-login-hint", explain(error), "error");
      } finally {
        setBusy(button, false);
      }
    });

    byId("tg-signal-logout")?.addEventListener("click", async () => {
      const button = byId("tg-signal-logout");
      setBusy(button, true);
      try {
        await api("/api/telegram-signal/logout", { method: "POST", body: "{}" });
        state.chats = [];
        state.chatsLoaded = false;
        state.sessionValid = null;
        await loadStatus();
        renderChats();
        notify("Telegram 账号已登出", "success");
      } catch (error) {
        notify(explain(error), "error");
      } finally {
        setBusy(button, false);
      }
    });

    byId("tg-signal-start")?.addEventListener("click", async () => {
      const button = byId("tg-signal-start");
      setBusy(button, true);
      try {
        await saveSettings();
        await api("/api/telegram-signal/start", { method: "POST", body: "{}" });
        await loadStatus();
        notify(state.dryRun ? "TG 干跑监听已启动" : "TG 自动跟单监听已启动", "success");
      } catch (error) {
        notify(explain(error), "error");
      } finally {
        setBusy(button, false);
      }
    });

    byId("tg-signal-stop")?.addEventListener("click", async () => {
      const button = byId("tg-signal-stop");
      setBusy(button, true);
      try {
        await api("/api/telegram-signal/stop", { method: "POST", body: "{}" });
        await loadStatus();
        notify("TG 监听已停止", "success");
      } catch (error) {
        notify(explain(error), "error");
      } finally {
        setBusy(button, false);
      }
    });

    state.refreshTimer = window.setInterval(() => {
      const systemCenterOpen = document.body?.dataset.view === "system-center";
      const standaloneOpen = document.body?.dataset.view === "tg-signal";
      if (systemCenterOpen || standaloneOpen) Promise.all([loadStatus(), loadLogs()]).catch(() => {});
    }, LOG_REFRESH_INTERVAL_MS);
  }

  window.loadTelegramSignalPopup = loadTelegramSignalPopup;
  window.bindTelegramSignalPopup = bindTelegramSignalPopup;
  window.saveTelegramSignalSettings = saveSettings;
})();
