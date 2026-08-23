(function () {
  "use strict";

  const state = {
    status: null,
    chats: [],
    events: [],
    dryRun: true,
    sessionValid: null,
    bound: false,
    refreshTimer: 0,
  };
  const byId = (id) => document.getElementById(id);

  const messages = {
    credentials_missing: "软件预置凭据缺失",
    telegram_login_required: "请先登录 Telegram 账号",
    source_chats_missing: "请至少选择一个来源群",
    telegram_session_expired: "Telegram 会话已失效，请重新登录",
    invalid_phone: "手机号格式不正确，请包含国家区号",
    invalid_login_code: "验证码格式不正确",
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
      && state.sessionValid === true
      && hasSources;
  }

  function updateMode(dryRun) {
    state.dryRun = !!dryRun;
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
    const running = !!data.worker_running;
    const mode = state.dryRun ? "干跑检查" : "自动跟单";
    setStatus("tg-signal-worker-status", running ? `监听中 · ${mode}` : `已关闭 · ${mode}`, running ? "ok" : "off");
    const live = byId("tg-signal-live-label");
    if (live) {
      live.classList.toggle("running", running);
      live.innerHTML = `<i></i>${running ? `监听中 · PID ${data.worker_pid || "-"}` : "未监听"}`;
    }
    const start = byId("tg-signal-start");
    const stop = byId("tg-signal-stop");
    const ready = canStartListening();
    if (start) start.disabled = running || !ready;
    if (stop) stop.disabled = !running;
    const overall = byId("sc-tg-overall-status");
    if (overall) {
      overall.textContent = running ? "运行中" : (ready ? "已就绪" : "待配置");
      overall.className = running || ready ? "badge badge-success" : "badge badge-dim";
    }
    if (!running && !ready) {
      const missing = [];
      if (!data.credentials_configured) missing.push("API 凭据");
      if (!data.session_configured || state.sessionValid !== true) missing.push("有效会话");
      if (!selectedChatIds().length && !(data.settings?.source_chat_ids || []).length) missing.push("来源群");
      setHint("tg-signal-action-hint", `启动前请完成：${missing.join("、")}。`, "");
    }
  }

  function renderStatus() {
    const data = state.status || {};
    const settings = data.settings || {};
    const connected = !!data.session_configured;
    const selected = (settings.source_chat_ids || []).map(String);
    state.dryRun = settings.dry_run !== false;

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
        item.status || "-",
        [[item.league, item.match].filter(Boolean).join(" · "), item.line].filter(Boolean).join(" / ") || "-",
        item.amount || "-",
        item.reason || item.order_id || "-",
      ];
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        if (index === 2) {
          const badge = document.createElement("span");
          const status = String(item.status || "").toLowerCase().replace(/[^a-z_]/g, "");
          badge.className = `tg-signal-event-status ${status}`.trim();
          badge.textContent = value;
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
      state.sessionValid = true;
      renderStatus();
      renderChats();
      return result;
    } catch (error) {
      state.chats = [];
      state.sessionValid = false;
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
    const ids = selectedChatIds();
    const sourceChats = state.chats.filter((chat) => ids.includes(String(chat.id)));
    const result = await api("/api/telegram-signal/settings", {
      method: "POST",
      body: JSON.stringify({ dry_run: state.dryRun, source_chat_ids: ids, source_chats: sourceChats }),
    });
    state.status = state.status || {};
    state.status.settings = result.settings;
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
    byId("tg-signal-dry-run")?.addEventListener("click", () => updateMode(true));
    byId("tg-signal-automatic")?.addEventListener("click", () => updateMode(false));
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
    }, 5000);
  }

  window.loadTelegramSignalPopup = loadTelegramSignalPopup;
  window.bindTelegramSignalPopup = bindTelegramSignalPopup;
  window.saveTelegramSignalSettings = saveSettings;
})();
