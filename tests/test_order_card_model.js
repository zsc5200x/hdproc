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
      HDOrderList: require('../app/webui/order_list.js'),
      HDMatchSearch: {},
      HDOrderRealtimeBridge: {
        ...require('../app/webui/order_realtime_bridge.js'),
        resolveMatchStatusDisplay(source) {
          return {
            statusLine: source.statusLine || '上半场 | 63:24',
            statusText: source.statusText || '上半场',
            isEnded: false,
          };
        },
      },
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(appJs, context, { filename: 'app.js' });
  return context;
}

test('order card model keeps backend Chinese teams when live snapshot has English aliases', () => {
  const context = loadAppContext();
  const model = context.buildOrderCardModel({
    orderId: '23820130492',
    league: '土耳其乙级联赛-附加赛',
    sides: '阿里亚加 v 穆斯波尔 (0 - 1)',
    betType: '(滚球) 让球',
    line: '穆斯波尔 +0.5',
    odds: '0.78',
    stake: '50',
    raw: {
      TEAM_H: 'Aliaga',
      TEAM_C: 'Mus',
      MARKET: 'rb',
      _EVENT: '8754835',
    },
    _live_match: {
      mode: 'live',
      modeLabel: '滚球',
      league: '土耳其乙级联赛-附加赛',
      homeTeam: 'Aliaga',
      awayTeam: 'Mus',
      score: '0 : 1',
      statusLine: '上半场 | 63:24',
      statusText: '上半场',
      gid: '8754835',
    },
  });

  assert.equal(model.homeTeam, '阿里亚加');
  assert.equal(model.awayTeam, '穆斯波尔');
  assert.equal(model.score, '0 : 1');
});

test('order card model separates corner total market period and selection', () => {
  const context = loadAppContext();
  const model = context.buildOrderCardModel({
    orderId: '23888880001',
    league: '测试联赛',
    sides: '主队 v 客队 (0 - 0)',
    betType: '(滚球) 角球数 - 大 / 小',
    line: '大 10.5',
    odds: '0.88',
    stake: '100',
    raw: { MARKET: 'rb', _EVENT: '8754001' },
  });

  assert.equal(model.orderKind, '滚球');
  assert.equal(model.betLabel, '角球大小');
  assert.equal(model.marketText, '滚球 · 全场 · 角球数');
  assert.equal(model.selectionText, '大 10.5 @ 0.88');
});

test('order card model separates first-half card total market period and selection', () => {
  const context = loadAppContext();
  const model = context.buildOrderCardModel({
    orderId: '23888880002',
    league: '测试联赛',
    sides: '主队 v 客队 (0 - 0)',
    betType: '罚牌数 大 / 小-上半场',
    line: '小 1.5',
    odds: '1.01',
    stake: '100',
    raw: { _EVENT: '8754002' },
  });

  assert.equal(model.orderKind, '早盘');
  assert.equal(model.betLabel, '罚牌大小');
  assert.equal(model.marketText, '早盘 · 上半场 · 罚牌数');
  assert.equal(model.selectionText, '小 1.5 @ 1.01');
});

const psOrder = {
  provider: 'ps3838', read_only: true, target: 'ps_demo', backendName: 'monitor',
  orderId: '3799987725', ts: 1791376506, event: 'new', account: 'customer', agent: 'agent',
  league: '中国-甲级联赛', sides: '佛山南狮-vs-广西恒宸', score: '0-0',
  betType: 'NORMAL', marketLabel: '让球', line: 'AWAY -0.25', odds: 1.884,
  stake: 150, toRisk: 150, toWin: 132.6, currency: 'THB', status: '',
  choices: [{ inplay: true, selection: 'AWAY', handicap: '-0.25', btName: 'HDP', periodName: '比赛' }],
};

test('PS3838 dashboard row shows real teams, currency and read-only status without follow data', () => {
  const context = loadAppContext();
  const model = context.buildOrderCardModel(psOrder);
  assert.equal(model.homeTeam, '佛山南狮');
  assert.equal(model.awayTeam, '广西恒宸');
  assert.equal(model.score, '0 : 0');
  assert.equal(model.orderKind, '滚球');
  assert.equal(model.selectionText, '广西恒宸 -0.25 @ 1.884');
  assert.equal(model.stakeStr, '150 THB');
  assert.equal(model.statusText, '接口未提供');
  const row = context.buildRealtimeOrderListRow({
    model, followSummary: { ok: 8 }, followStake: 900,
    settlement: { status: 'win', profit_sum: 500, status_label: '盈利' },
  });
  assert.equal(row.followAmountText, '—');
  assert.equal(row.followStatusText, '只读监控');
  assert.equal(row.settlementText, '未提供');
  assert.match(row.sourceLabel, /平博.*3799987725/);
  const html = context.renderRealtimeOrderListRow(row).innerHTML;
  assert.match(html, /订单详情/);
  assert.doesNotMatch(html, /比赛事件|盘口信息/);
  const detail = context.renderReadOnlyOrderDetails(model, psOrder);
  assert.match(detail, /150 THB/);
  assert.match(detail, /132.6 THB/);
  assert.match(detail, /customer/);
  assert.doesNotMatch(detail, /跟单执行|待处理|未结算|¥/);
});

test('PS ticket survives a Crown queue collision and skips Crown event enrichment', () => {
  const context = loadAppContext();
  const crown = { orderId: psOrder.orderId, target: 'crown', ts: 1 };
  const rows = context.mergeFollowQueueOrdersForDisplay([psOrder, crown], [{
    order: crown, status: 'pending',
  }]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0], psOrder);
  assert.equal(rows[0]._follow_queue, undefined);
  assert.equal(rows[1]._follow_queue.status, 'pending');
  vm.runInContext('state.orderEventSnapshots = { match: { snapshot: { timeline: { score: "9 : 9" } } } }', context);
  assert.equal(context.applyOrderEventSnapshotsForDisplay(rows)[0], psOrder);
});

test('updated PS status and unknown scores are displayed without invented match results', () => {
  const context = loadAppContext();
  const model = context.buildOrderCardModel({ ...psOrder, event: 'update', status: 'settled', result: 'WIN', score: null });
  const row = context.buildRealtimeOrderListRow({ model });
  assert.equal(row.statusLine, '订单已更新');
  assert.equal(row.followStatusText, '只读 · settled');
  assert.equal(row.settlementText, 'WIN');
  assert.equal(row.score, '—');
  assert.equal(context.formatDashboardCurrencyTotals({ THB: 150.25, CNY: 100 }, 250.25), '150.25 THB / ¥100');
});

test('opening PS details never starts a Crown match request', () => {
  const context = loadAppContext();
  let shown = 0;
  context.showOrderDetailModal = () => shown++;
  context.startHgMatchEventRefresh = () => { throw new Error('unexpected Crown request'); };
  context.buildHgModelFromOrder = () => { throw new Error('unexpected Crown conversion'); };
  context.openModal(psOrder, '比赛事件');
  context.openModal(psOrder, '盘口信息');
  assert.equal(shown, 2);
});
