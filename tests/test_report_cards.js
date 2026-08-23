const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildReportCardModel,
  formatCompactMoney,
} = require('../app/webui/report_cards.js');

test('formats report stake like realtime order cards', () => {
  assert.equal(formatCompactMoney(980), '¥980');
  assert.equal(formatCompactMoney(2000), '¥2.0k');
  assert.equal(formatCompactMoney(0), '—');
});

test('builds report card model with realtime order card classes', () => {
  const model = buildReportCardModel({
    order: {
      orderId: '23817049323',
      league: '埃及超级联赛-附加赛',
      sides: '现代体育 v 塔拉雅 (0 - 0)',
      betType: '大球',
      line: '大 0.5',
      selection: '大球',
      odds: '0.86',
      status: '已接收',
    },
    followSummary: { ok: 2, fail: 1, pending: 0 },
    settlement: { status: 'win', status_label: '已结算', profit_sum: 120.5 },
    followStake: 2000,
    account: 'zxcv226699',
    source: 'zxcv226699',
    timeText: '02:18:02',
    eventId: '8747907',
    betLabel: '大球',
    betColor: '#10B981',
    orderKind: '滚球',
    isLiveOrder: true,
  });

  assert.equal(model.cardClass, 'match-card rpt-card rpt-order-card');
  assert.equal(model.headerChip, '#23817049323');
  assert.equal(model.selectionText, '大球 大 0.5 @ 0.86');
  assert.equal(model.stakeText, '¥2.0k');
  assert.equal(model.reportStatusText, '成功 2 · 失败 1');
  assert.equal(model.settlementTone, 'win');
  assert.equal(model.profitText, '+¥120.50');
});

test('builds report card model with clear first-half card market text', () => {
  const model = buildReportCardModel({
    order: {
      orderId: '23888880003',
      league: '测试联赛',
      sides: '主队 v 客队 (0 - 0)',
      betType: '罚牌数 大 / 小-上半场',
      line: '小 1.5',
      odds: '1.01',
      status: '已接收',
    },
    followSummary: { ok: 0, fail: 0, pending: 1 },
    settlement: { status: 'pending', status_label: '未结算', profit_sum: 0 },
    followStake: 0,
    account: 'zxcv226699',
    source: 'zxcv226699',
    timeText: '14:18:02',
    eventId: '8754003',
    orderKind: '早盘',
    isLiveOrder: false,
  });

  assert.equal(model.betLabel, '罚牌大小');
  assert.equal(model.marketText, '早盘 · 上半场 · 罚牌数');
  assert.equal(model.selectionText, '小 1.5 @ 1.01');
});
