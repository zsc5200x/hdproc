const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function makeElement() {
  return {
    className: '',
    dataset: {},
    disabled: false,
    innerHTML: '',
    textContent: '',
    value: '',
    checked: false,
    style: {},
    children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    appendChild(child) { this.children.push(child); },
    replaceChildren() {},
    querySelector() { return makeElement(); },
    querySelectorAll() { return []; },
  };
}

function loadQueueContext() {
  const queueJs = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'chashui', 'queue.js'), 'utf8');
  const elements = new Map();
  const context = {
    console,
    setTimeout,
    clearTimeout,
    setInterval() { return 1; },
    clearInterval() {},
    fetch: async (url) => {
      const u = String(url || '');
      if (u.includes('/api/follow-settings')) {
        return { ok: true, json: async () => ({ ok: true, settings: {} }), text: async () => '' };
      }
      if (u.includes('/api/follow-queue')) {
        return { ok: true, json: async () => ({ ok: true, items: [], counts: {} }), text: async () => '' };
      }
      if (u.includes('/api/arsenal')) {
        return { ok: true, json: async () => ({ ok: true, members: [] }), text: async () => '' };
      }
      if (u.includes('/api/chashui/status')) {
        return { ok: true, json: async () => ({ ok: true, member: {} }), text: async () => '' };
      }
      return { ok: true, json: async () => ({ ok: true }), text: async () => '' };
    },
    document: {
      getElementById(id) {
        const key = String(id || '');
        if (!elements.has(key)) elements.set(key, makeElement());
        return elements.get(key);
      },
      createElement() { return makeElement(); },
      querySelector() { return makeElement(); },
      querySelectorAll() { return []; },
    },
    window: {},
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(queueJs, context, { filename: 'queue.js' });
  return context;
}

test('member execution center replaces the legacy queue page structure', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'chashui', 'queue.html'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'chashui', 'queue.css'), 'utf8');

  assert.match(page, /会员执行调度中心/);
  assert.match(page, /id="memberPool"/);
  assert.match(page, /id="orderPool"/);
  assert.match(page, /id="executionLog"/);
  assert.doesNotMatch(page, /路由中枢|id="routingHub"|class="controlPanel routingPanel/);
  assert.doesNotMatch(page, /id="connectionLayer"/);
  assert.doesNotMatch(styles, /\.routingPanel(?:Static)?\s*\{/);
  assert.match(styles, /\.workspaceGrid\s*\{[\s\S]*?grid-template-columns:\s*minmax\(260px, 0\.72fr\) minmax\(560px, 2\.8fr\)/);
  assert.match(styles, /\.failureOverview\s*\{[\s\S]*?align-items:\s*stretch/);
  assert.match(styles, /\.failureOverview\s*\{[\s\S]*?align-content:\s*stretch/);
  assert.match(styles, /\.failureLegend\s*\{[\s\S]*?align-content:\s*space-between/);
  assert.match(styles, /\.orderPanel\s*\{[\s\S]*?height:\s*620px/);
  assert.match(styles, /\.executionLog\s*\{[\s\S]*?min-height:\s*158px/);
  assert.doesNotMatch(styles, /animation:\s*(ring|route)/);
});

test('labels legacy precheck history without exposing a removed execution stage', () => {
  const context = loadQueueContext();

  assert.equal(
    context.displayExecutionMessage('跟单失败：会员端预校验失败，正在重试。'),
    '历史旧链执行失败（重构前记录）'
  );
  assert.equal(context.displayExecutionMessage('盘口变化，等待更新'), '盘口变化，等待更新');
});

test('skips identical html renders and preserves scroll position when content changes', () => {
  const context = loadQueueContext();
  let writes = 0;
  let html = '';
  const element = {
    scrollTop: 42,
    scrollLeft: 7,
    get innerHTML() { return html; },
    set innerHTML(value) {
      writes += 1;
      html = String(value);
      this.scrollTop = 0;
      this.scrollLeft = 0;
    },
  };

  assert.equal(context.updateHtmlIfChanged(element, '<p>稳定内容</p>'), true);
  assert.equal(writes, 1);
  assert.equal(element.scrollTop, 42);
  assert.equal(element.scrollLeft, 7);

  element.scrollTop = 88;
  element.scrollLeft = 9;
  assert.equal(context.updateHtmlIfChanged(element, '<p>稳定内容</p>'), false);
  assert.equal(writes, 1);
  assert.equal(element.scrollTop, 88);
  assert.equal(element.scrollLeft, 9);

  assert.equal(context.updateHtmlIfChanged(element, '<p>更新内容</p>'), true);
  assert.equal(writes, 2);
  assert.equal(element.scrollTop, 88);
  assert.equal(element.scrollLeft, 9);
});

test('recognizes read-only members and excludes them from betting', () => {
  const context = loadQueueContext();

  assert.equal(context.memberUsage({ usage: 'read', status: 'online' }), 'read');
  assert.equal(context.memberUsage({ role: 'quote', status: 'online' }), 'read');
  assert.equal(context.isBetExecutionMember({ usage: 'read', status: 'online' }), false);
  assert.equal(context.memberCanBet({ usage: 'read', status: 'online' }), false);
  assert.equal(context.memberCanBet({ role: 'quote', status: 'online' }), false);
  assert.equal(context.memberCanBet({ usage: 'bet', status: 'online' }), true);
});

test('does not couple queue tests to the ignored runtime arsenal', () => {
  const queuePage = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'chashui', 'queue.html'), 'utf8');

  assert.match(queuePage, /后台 Snapshot（唯一生产链）/);
  assert.doesNotMatch(queuePage, /id="selMember"/);
});

test('expands independent split jobs into execution task models', () => {
  const context = loadQueueContext();
  const groups = context.buildExecutionTaskModels([
    {
      key: 'source|OID-1',
      status: 'job',
      order: {
        orderId: 'OID-1',
        sides: '费哈 v 希拉尔 (0 - 0)',
        betType: '(滚球) 大 / 小',
        line: '大 2 / 2.5',
        odds: '1.09',
        stake: 300,
      },
      job: {
        split_count: 3,
        split_stakes: [100, 100, 100],
        split_tasks: [
          { split_index: 0, member_id: 'member-a', stake: 300, attempts: 1 },
          { split_index: 1, member_id: '', stake: 300, attempts: 0 },
          { split_index: 2, member_id: 'member-b', stake: 300, attempts: 2, last_err: 'ODDS_CHANGED' },
        ],
      },
    },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].tasks.length, 3);
  assert.deepEqual(groups[0].tasks.map((task) => task.status), ['running', 'waiting', 'retry']);
  assert.deepEqual(groups[0].tasks.map((task) => task.amount), [100, 100, 100]);
  assert.deepEqual(groups[0].tasks.map((task) => task.label), ['拆分单一', '拆分单二', '拆分单三']);
  assert.equal(groups[0].tasks[0].market, '大 / 小 大 2 / 2.5 @ 1.09');
});

test('shows the source team and line instead of generic home/away labels', () => {
  const context = loadQueueContext();
  const groups = context.buildExecutionTaskModels([
    {
      key: 'source|OID-TEAM-DIGIT',
      status: 'job',
      order: {
        orderId: 'OID-TEAM-DIGIT',
        sides: '文图拉 v 波特兰木材2队 (2 - 2)',
        betType: '(滚球) 让球',
        line: '波特兰木材2队 0',
        odds: '0.94',
        raw: { TEAM_H: '文图拉', TEAM_C: '波特兰木材2队' },
      },
      job: { split_count: 1, split_tasks: [{}] },
    },
  ]);

  assert.equal(context.orderCardMarket(groups[0]).direction, '波特兰木材2队 0');
  assert.doesNotMatch(context.orderCardMarket(groups[0]).direction, /客队|主队/);
});

test('uses terminal split_total for display without inventing split amounts', () => {
  const context = loadQueueContext();
  const groups = context.buildExecutionTaskModels([
    {
      key: 'source|OID-HISTORY',
      status: 'failed',
      order: { orderId: 'OID-HISTORY', sides: '主队 v 客队', stake: 100 },
      job: { terminal: true, split_total: 2, split_done: 0, reason: '重试次数已达上限' },
    },
  ]);

  assert.equal(groups[0].tasks.length, 2);
  assert.equal(JSON.stringify(groups[0].tasks.map((task) => task.label)), JSON.stringify(['拆分单一', '拆分单二']));
  assert.equal(JSON.stringify(groups[0].tasks.map((task) => task.amount)), JSON.stringify([null, null]));
  assert.equal(JSON.stringify(groups[0].tasks.map((task) => task.status)), JSON.stringify(['failed', 'failed']));
  assert.equal(groups[0].status, 'failed');
});

test('aggregates mixed split statuses as a partial failure', () => {
  const context = loadQueueContext();
  const groups = context.buildExecutionTaskModels([
    {
      key: 'source|OID-PARTIAL-2',
      status: 'job',
      order: { orderId: 'OID-PARTIAL-2', sides: '主队 v 客队', stake: 200 },
      job: {
        split_count: 2,
        split_stakes: [120, 80],
        split_tasks: [
          { split_index: 0, status: 'success', ticket_id: 'T-1' },
          { split_index: 1, status: 'failed', last_err: 'ODDS_CHANGED' },
        ],
      },
    },
  ]);

  assert.equal(groups[0].status, 'partial');
  assert.equal(groups[0].tasks[0].amount, 120);
  assert.equal(groups[0].tasks[1].amount, 80);
});

test('derives member execution state from task ownership and cooldown', () => {
  const context = loadQueueContext();
  const tasks = [
    { status: 'running', memberId: 'member-a' },
    { status: 'retry', memberId: 'member-b' },
  ];

  assert.equal(context.deriveMemberExecutionState({ id: 'member-a', status: 'online' }, tasks, 100), 'running');
  assert.equal(
    context.deriveMemberExecutionState({ id: 'member-c', status: 'online', request_cooldown_until: 130 }, tasks, 100),
    'cooldown'
  );
  assert.equal(context.deriveMemberExecutionState({ id: 'member-d', status: 'offline' }, tasks, 100), 'offline');
  assert.equal(context.deriveMemberExecutionState({ id: 'member-e', status: 'online' }, tasks, 100), 'idle');
});

test('keeps a terminal failed order failed when a partial ticket exists', () => {
  const context = loadQueueContext();
  const groups = context.buildExecutionTaskModels([
    {
      key: 'source|OID-PARTIAL',
      status: 'failed',
      order: { orderId: 'OID-PARTIAL', sides: '主队 v 客队', stake: 100 },
      job: { ticket_id: 'T-PARTIAL', reason: '后续分单失败' },
    },
  ]);

  assert.equal(groups[0].tasks[0].status, 'failed');
});

test('classifies execution results using active orders to identify retries', () => {
  const context = loadQueueContext();
  const active = new Set(['OID-RETRY']);

  assert.equal(context.classifyExecutionResult({ ok: true, orderId: 'OID-OK' }, active), 'success');
  assert.equal(context.classifyExecutionResult({ ok: false, orderId: 'OID-RETRY' }, active), 'retry');
  assert.equal(context.classifyExecutionResult({ ok: false, orderId: 'OID-FAIL' }, active), 'failed');
  assert.equal(context.classifyExecutionResult({ status: 'running', orderId: 'OID-RUN' }, active), 'running');
});

test('classifies betError as odds change instead of score change', () => {
  const context = loadQueueContext();

  assert.equal(context.extractErrorCode({ last_err: 'betError000' }), 'ODDS_CHANGED');
  assert.match(context.formatError('ODDS_CHANGED', '跟单失败：当前水位已变化'), /水位已变化/);
  assert.doesNotMatch(context.formatError('ODDS_CHANGED', '跟单失败：当前水位已变化'), /比分已变化/);
});

test('keeps legacy live chashui requests out of the execution queue', () => {
  const queueJs = fs.readFileSync(path.join(__dirname, '..', 'app', 'webui', 'chashui', 'queue.js'), 'utf8');

  assert.doesNotMatch(queueJs, /\/api\/chashui\/odds/);
  assert.doesNotMatch(queueJs, /function\s+renderOverUnder\s*\(/);
  assert.doesNotMatch(queueJs, /function\s+buildMarketSnapshotBadges\s*\(/);
  assert.doesNotMatch(queueJs, /function\s+shouldRequestTaskSnapshot\s*\(/);
});

test('exposes snapshot request failures instead of treating them as pending', () => {
  const context = loadQueueContext();

  assert.equal(context.formatSnapshotError('game_not_found'), '比赛已不在当前盘口列表');
  assert.equal(context.formatSnapshotError('readonly_pool_empty'), '没有可用查水会员');
  assert.equal(context.formatSnapshotError('network timeout'), '查水请求超时');
});

test('formats the original follow market without rendering market snapshots', () => {
  const context = loadQueueContext();

  assert.equal(
    context.formatFollowMarketText({ betType: '(滚球) 大 / 小', line: '大 2.5 / 3', odds: '1.09' }),
    '大 / 小 大 2.5 / 3 @ 1.09'
  );
  assert.equal(
    context.formatFollowMarketText({ betType: '(滚球) 让球', line: '主队 -0.5', odds: '0.85' }),
    '让球 主队 -0.5 @ 0.85'
  );
});

test('renders only the original follow market in the queue card', () => {
  const context = loadQueueContext();
  const box = makeElement();
  const order = { betType: '(滚球) 大 / 小', line: '大 3', odds: '1.09' };

  context.renderFollowMarketSnapshot(box, order);

  assert.equal(box.children.length, 1);
  assert.match(box.children[0].textContent, /原单：大 \/ 小 大 3 @ 1.09/);
});
