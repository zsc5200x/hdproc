const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = fs.readFileSync(path.join(__dirname, '../app/webui/app.js'), 'utf8');
const between = (start, end) => script.slice(script.indexOf(start), script.indexOf(end, script.indexOf(start)));

function poolContext() {
  const elements = new Map();
  const actions = [];
  const node = () => ({
    innerHTML: '', hidden: false, textContent: '',
    classList: { toggle() {}, add() {} },
    buttons: new Map(), children: [],
    setAttribute() {}, addEventListener() {},
    appendChild(child) { this.children.push(child); },
    querySelector(selector) {
      if (!this.buttons.has(selector)) {
        this.buttons.set(selector, { handlers: {}, addEventListener(event, handler) { this.handlers[event] = handler; } });
      }
      return this.buttons.get(selector);
    },
  });
  const state = {
    accounts: [], ps3838Accounts: [{ name: 'ps_demo', username: 'zero.99', enabled: true, interval_seconds: 15 }],
    statuses: {}, ps3838Statuses: { ps_demo: { status: 'online', last_poll_at: 100, last_ok_at: 100, order_count: 0 } },
    orders: [], signalPullConfig: {}, poolDashboard: { source: 'all', status: 'all', channel: 'all', search: '' },
    poolSelection: { kind: 'monitor', id: '' },
  };
  const context = vm.createContext({
    state, Date, Math, Number, String, Array, Object,
    $: (id) => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    document: { createElement: node, querySelectorAll: () => [] },
    accountLabel: (account) => account.username || account.name,
    formatMonitorTimestamp: (ts) => ts ? `时间 ${ts}` : '还没扫到',
    platformMaintenanceModel: () => ({}),
    getSignalSourceSidebarItems: () => [],
    escapeHtml: (value) => String(value ?? ''),
    renderPoolOverview() {}, toast() {}, loadStatuses: async () => {},
    updateAccountEnabled: async (...args) => actions.push(['toggle', ...args]),
    reloginAccount: async (...args) => actions.push(['login', ...args]),
    confirmDeleteAccount: async (...args) => { actions.push(['delete', ...args]); return true; },
    renderPoolManagement() {},
  });
  vm.runInContext([
    between('function monitorAccountLoginTab(', 'function renderLegacyMonitorPoolStats('),
    between('function poolPercent(', 'function renderPoolTrendChart('),
  ].join('\n'), context);
  return { context, state, elements, actions };
}

test('logged-in PS3838 account renders in the pool even when there are zero orders', () => {
  const { context, elements } = poolContext();
  vm.runInContext('renderMonitorPool()', context);
  const grid = elements.get('monitor-pool-cards');
  assert.equal(grid.children.length, 1);
  assert.equal(grid.hidden, false);
  assert.equal(elements.get('monitor-pool-empty').hidden, true);
  assert.match(grid.children[0].innerHTML, /zero\.99/);
  assert.match(grid.children[0].innerHTML, /平博 \/ PS3838/);
  assert.match(grid.children[0].innerHTML, /在线扫单/);
  assert.match(grid.children[0].innerHTML, /当前 0 笔/);
  assert.equal(elements.get('monitor-pool-count').textContent, '1');
  assert.equal(elements.get('monitor-pool-normal-count').textContent, '1');
  assert.equal(elements.get('monitor-pool-concurrency').textContent, '1 / 1');
});

test('pool status uses the PS3838 status map independently of AG maintenance', () => {
  const { context, state } = poolContext();
  state.statuses.ps_demo = { status: 'maintenance', last_poll_at: 1 };
  const source = vm.runInContext('buildMonitorPoolSources()[0]', context);
  assert.equal(source.normal, true);
  assert.equal(source.maintenance, false);
  assert.equal(vm.runInContext('monitorSourceStatus(buildMonitorPoolSources()[0]).last_poll_at', context), 100);
});

test('PS3838 channel filter does not classify it as AG login3', () => {
  const { context, state } = poolContext();
  state.poolDashboard.channel = 'ps3838';
  assert.equal(vm.runInContext('filterMonitorPoolSources(buildMonitorPoolSources()).length', context), 1);
  state.poolDashboard.channel = '3';
  assert.equal(vm.runInContext('filterMonitorPoolSources(buildMonitorPoolSources()).length', context), 0);
});

test('waiting for 2FA is visible and the login button cannot interrupt it', () => {
  const { context, state, elements } = poolContext();
  state.ps3838Statuses.ps_demo = { status: 'relogin', relogin_in_progress: true };
  vm.runInContext('renderMonitorPool()', context);
  const html = elements.get('monitor-pool-cards').children[0].innerHTML;
  assert.match(html, /等待登录 \/ 2FA/);
  assert.match(html, /data-action="login"[^>]*disabled/);
  assert.equal(elements.get('monitor-pool-normal-count').textContent, '0');
});

test('paused PS3838 account remains visible and can be filtered', () => {
  const { context, state } = poolContext();
  state.ps3838Accounts[0].enabled = false;
  state.poolDashboard.status = 'paused';
  const sources = vm.runInContext('filterMonitorPoolSources(buildMonitorPoolSources())', context);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].statusText, '已暂停');
  assert.equal(sources[0].normal, false);
});

test('PS3838 pool buttons pass the provider to pause, login, and delete actions', async () => {
  const { context, elements, actions } = poolContext();
  vm.runInContext('renderMonitorPool()', context);
  const card = elements.get('monitor-pool-cards').children[0];
  for (const action of ['toggle', 'login', 'delete']) {
    await card.buttons.get(`[data-action='${action}']`).handlers.click({ stopPropagation() {} });
  }
  assert.deepEqual(actions, [
    ['toggle', 'ps_demo', 'zero.99', false, 'ps3838'],
    ['login', 'ps_demo', 'zero.99', undefined, 'ps3838'],
    ['delete', 'ps_demo', 'zero.99', 'ps3838'],
  ]);
});
