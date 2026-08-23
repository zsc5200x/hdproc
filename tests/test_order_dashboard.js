const test = require('node:test');
const assert = require('node:assert/strict');

const {
  AMOUNT_BUCKETS,
  DEFAULT_ORDER_DASHBOARD_FILTERS,
  buildOrderDashboardModel,
  filterAndSortOrders,
  formatDashboardMoney,
  getOrderPlayCategory,
  getOrderType,
} = require('../app/webui/order_dashboard.js');

const nowMs = new Date('2026-04-30T10:00:00+08:00').getTime();

const orders = [
  {
    orderId: '1001',
    ts: Math.floor(new Date('2026-04-30T09:00:00+08:00').getTime() / 1000),
    league: '日本J联赛',
    sides: '东京 v 大阪',
    betType: '大球',
    line: '大 2.5',
    stake: 5000,
    status: '已接收',
    orderType: '滚球',
  },
  {
    orderId: '1002',
    ts: Math.floor(new Date('2026-04-30T08:00:00+08:00').getTime() / 1000),
    league: '日本J联赛',
    sides: '横滨 v 神户',
    betType: '让球',
    line: '横滨 -0.5',
    stake: 1000,
    status: '待确认',
    orderType: '早盘',
  },
  {
    orderId: '1003',
    ts: Math.floor(new Date('2026-04-28T08:00:00+08:00').getTime() / 1000),
    league: '埃及超级联赛',
    sides: '现代体育 v 塔拉雅',
    betType: '小球',
    line: '小 1.5',
    stake: 800,
    status: '已接收',
    raw: { MARKET: 'RB' },
  },
];

test('normalizes order type and play category for dashboard filters', () => {
  assert.equal(getOrderType(orders[0]), '滚球');
  assert.equal(getOrderType(orders[1]), '早盘');
  assert.equal(getOrderType(orders[2]), '滚球');
  assert.equal(getOrderPlayCategory(orders[0]), '大小球');
  assert.equal(getOrderPlayCategory(orders[1]), '让球');
});

test('filters and sorts realtime orders for dashboard', () => {
  const filtered = filterAndSortOrders(orders, {
    ...DEFAULT_ORDER_DASHBOARD_FILTERS,
    timeRange: 'today',
    orderType: '滚球',
    playCategory: '大小球',
    sortBy: 'amount-desc',
  }, { nowMs });

  assert.deepEqual(filtered.map((order) => order.orderId), ['1001']);
});

test('builds dashboard stats and chart data from filtered orders', () => {
  const model = buildOrderDashboardModel(orders, {
    ...DEFAULT_ORDER_DASHBOARD_FILTERS,
    timeRange: 'history',
  }, { nowMs });

  assert.equal(model.stats.totalOrders, 3);
  assert.equal(model.stats.liveOrders, 2);
  assert.equal(model.stats.earlyOrders, 1);
  assert.equal(model.stats.totalStake, 6800);
  assert.equal(model.filters.leagues.length, 2);
  assert.deepEqual(model.charts.playDistribution, [
    { label: '大小球', count: 2, stake: 5800 },
    { label: '让球', count: 1, stake: 1000 },
  ]);
  assert.equal(model.charts.leagueDistribution[0].label, '日本J联赛');
  assert.equal(model.charts.amountDistribution.length, AMOUNT_BUCKETS.length);
});

test('formats dashboard money compactly', () => {
  assert.equal(formatDashboardMoney(0), '¥0');
  assert.equal(formatDashboardMoney(980), '¥980');
  assert.equal(formatDashboardMoney(12000), '¥12.0k');
});
