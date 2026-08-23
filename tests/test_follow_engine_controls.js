const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadAppContext() {
  const appJs = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'app.js'), 'utf8');
  const context = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    localStorage: {
      getItem() { return null; },
      setItem() {},
    },
    document: {
      activeElement: null,
      getElementById() { return null; },
      querySelectorAll() { return []; },
      querySelector() { return null; },
      createElement() {
        return {
          className: '',
          dataset: {},
          style: {},
          disabled: false,
          innerHTML: '',
          textContent: '',
          classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
          addEventListener() {},
          appendChild() {},
          querySelectorAll() { return []; },
          querySelector() { return null; },
        };
      },
    },
    window: {
      addEventListener() {},
      setTimeout,
      clearTimeout,
      setInterval,
      clearInterval,
      HDFollowSettingsForm: {},
      HDReportCards: {},
      HDOrderDashboard: {},
      HDMatchSearch: {},
      HDOrderRealtimeBridge: {},
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(appJs, context, { filename: 'app.js' });
  return context;
}

test('builds stopped follow engine control model with start action', () => {
  const context = loadAppContext();
  assert.equal(typeof context.buildFollowEngineControlModel, 'function');

  const model = context.buildFollowEngineControlModel({
    known: true,
    running: false,
    pid: null,
  });

  assert.equal(model.statusText, '跟单未启动');
  assert.equal(model.action, 'start');
  assert.equal(model.actionLabel, '启动跟单');
  assert.equal(model.buttonTone, 'primary');
  assert.equal(model.disabled, false);
});

test('builds running follow engine control model with stop action', () => {
  const context = loadAppContext();

  const model = context.buildFollowEngineControlModel({
    known: true,
    running: true,
    pid: 7788,
  });

  assert.equal(model.statusText, '跟单运行中');
  assert.equal(model.action, 'stop');
  assert.equal(model.actionLabel, '停止跟单');
  assert.equal(model.buttonTone, 'danger');
  assert.equal(model.disabled, false);
});

test('defaults unknown follow engine state to start action', () => {
  const context = loadAppContext();

  const model = context.buildFollowEngineControlModel({
    known: false,
    running: true,
    pid: null,
  });

  assert.equal(model.statusText, '跟单状态检测中');
  assert.equal(model.action, 'start');
  assert.equal(model.actionLabel, '启动跟单');
  assert.equal(model.buttonTone, 'primary');
});

test('performFollowEngineAction posts to matching endpoint and clears busy state', async () => {
  const context = loadAppContext();
  assert.equal(typeof context.performFollowEngineAction, 'function');

  const calls = [];
  const reloads = [];
  const notices = [];
  const appState = context.window.state;

  appState.followEngine.running = false;

  await context.performFollowEngineAction('start', {
    request: async (url, init) => {
      calls.push({ url, method: init && init.method, busy: appState.followEngine.controlBusy });
      return { ok: true, status: 'started' };
    },
    reload: async () => {
      reloads.push('start');
      appState.followEngine.running = true;
    },
    notify: (message, type) => {
      notices.push({ message, type });
    },
  });

  appState.followEngine.running = true;

  await context.performFollowEngineAction('stop', {
    request: async (url, init) => {
      calls.push({ url, method: init && init.method, busy: appState.followEngine.controlBusy });
      return { ok: true, status: 'stopping' };
    },
    reload: async () => {
      reloads.push('stop');
      appState.followEngine.running = false;
    },
    notify: (message, type) => {
      notices.push({ message, type });
    },
  });

  assert.deepEqual(calls.map((item) => item.url), ['/api/start', '/api/stop']);
  assert.deepEqual(calls.map((item) => item.method), ['POST', 'POST']);
  assert.deepEqual(calls.map((item) => item.busy), ['start', 'stop']);
  assert.deepEqual(reloads, ['start', 'stop']);
  assert.equal(appState.followEngine.controlBusy, '');
  assert.equal(notices.length, 2);
});
