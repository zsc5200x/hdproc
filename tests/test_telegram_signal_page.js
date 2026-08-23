const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(root, name), "utf8");

test("main dashboard embeds TG signal intake in the system center", () => {
  const html = read("app/webui/index.html");
  assert.match(html, /class="view system-center-frame"\s+data-view="system-center"/);
  assert.match(html, /class="view"\s+data-view="tg-signal"/);
  assert.doesNotMatch(html, /href="\/tg-signal\/"/);
  assert.match(html, /TG\s*信号接入/);
  assert.doesNotMatch(html, /href="\/manual-signal\/"/);
  assert.doesNotMatch(html, /class="sc-page-header"/);
  assert.doesNotMatch(html, /class="sc-actionbar"/);
  assert.doesNotMatch(html, /id="system-check-connections"/);
  assert.doesNotMatch(html, /id="system-save-all"/);
});

test("main dashboard embeds complete TG setup and runtime controls", () => {
  const html = read("app/webui/index.html");
  assert.match(html, /class="view"\s+data-view="tg-signal"/);
  for (const id of [
    "tg-signal-credential-status", "tg-signal-session-status", "tg-signal-source-status",
    "tg-signal-worker-status", "tg-signal-phone", "tg-signal-send-code",
    "tg-signal-code", "tg-signal-password", "tg-signal-verify-code", "tg-signal-logout",
    "tg-signal-group-search", "tg-signal-group-list", "tg-signal-save-groups",
    "tg-signal-dry-run", "tg-signal-automatic", "tg-signal-start", "tg-signal-stop",
    "tg-signal-event-rows",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
});

test("main dashboard wires TG popup APIs without persisting secrets", () => {
  const html = read("app/webui/index.html");
  const app = read("app/webui/app.js");
  const popup = read("app/webui/tg_signal_popup.js");
  assert.match(html, /tg_signal_popup\.css/);
  assert.match(html, /tg_signal_popup\.js/);
  assert.match(app, /tg-signal/);
  assert.match(app, /loadTelegramSignalPopup/);
  assert.match(app, /bindTelegramSignalPopup/);
  for (const endpoint of ["status", "chats", "logs", "send-code", "verify-code", "settings", "start", "stop", "logout"]) {
    assert.match(popup, new RegExp(`/api/telegram-signal/${endpoint}`), endpoint);
  }
  assert.doesNotMatch(popup, /localStorage|sessionStorage|document\.cookie/);
  assert.doesNotMatch(html, /api[_-]?hash/i);
});

test("TG popup blocks listening until credentials, session, and sources are ready", () => {
  const popup = read("app/webui/tg_signal_popup.js");
  assert.match(popup, /function canStartListening\(/);
  assert.match(popup, /credentials_configured/);
  assert.match(popup, /session_configured/);
  assert.match(popup, /source_chat_ids/);
});

test("TG signal page exposes login, source, mode, control, and event views", () => {
  const html = read("app/webui/tg-signal/index.html");
  for (const id of [
    "phoneInput", "sendCodeBtn", "codeInput", "passwordInput", "verifyCodeBtn",
    "groupSearch", "groupList", "dryRunMode", "automaticMode", "startBtn", "stopBtn", "eventRows",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  assert.match(html, /账号连接/);
  assert.match(html, /来源群/);
  assert.match(html, /信号入单/);
  assert.doesNotMatch(html, /api[_-]?id|api[_-]?hash/i);
  assert.doesNotMatch(html, /目标群|转发规则/);
});

test("browser code uses TG APIs and never persists secrets", () => {
  const js = read("app/webui/tg-signal/app.js");
  for (const endpoint of ["status", "chats", "logs", "send-code", "verify-code", "settings", "start", "stop", "logout"]) {
    assert.match(js, new RegExp(`/api/telegram-signal/${endpoint}`), endpoint);
  }
  assert.doesNotMatch(js, /localStorage|sessionStorage|document\.cookie/);
});

test("hidden account states cannot be overridden by component display rules", () => {
  const css = read("app/webui/tg-signal/style.css");
  assert.match(css, /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/);
});

test("TG popup stacks sections without shrinking them on narrow screens", () => {
  const css = read("app/webui/tg_signal_popup.css");
  assert.match(css, /@media\s*\(max-width:\s*920px\)[\s\S]*?\.tg-signal-layout\s*\{[\s\S]*?display:\s*flex;[\s\S]*?flex-direction:\s*column;/);
  assert.match(css, /\.tg-signal-setup,\s*\.tg-signal-runtime\s*\{\s*flex:\s*0 0 auto;\s*min-height:\s*auto;/);
});
