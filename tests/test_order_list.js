const assert = require('node:assert/strict');
const test = require('node:test');

const tools = require('../app/webui/order_list.js');

test('builds a compact realtime order list row', () => {
  const row = tools.buildRealtimeOrderListRow({
    model: {
      league: '俄罗斯超级联赛',
      orderKind: '早盘',
      homeTeam: '奥伦堡',
      awayTeam: '罗斯托夫',
      score: '0 : 0',
      statusLine: '上半场 | 20:54',
      marketText: '让球',
      selectionText: '奥伦堡 0',
      oddsText: '0.83',
      stakeStr: '¥5.8k',
      statusText: '已接收',
    },
    followSummary: { ok: 1, fail: 0, pending: 0 },
    followStake: 3000,
    settlement: { status: 'win', status_label: '赢', profit_sum: 2490 },
  });

  assert.equal(row.league, '俄罗斯超级联赛');
  assert.equal(row.orderKind, '早盘');
  assert.equal(row.matchText, '奥伦堡|0 : 0|罗斯托夫');
  assert.equal(row.marketText, '奥伦堡 0@0.83');
  assert.equal(row.betAmountText, '¥5.8k');
  assert.equal(row.followAmountText, '¥3.0k');
  assert.equal(row.followStatusText, '成功 1 单');
  assert.equal(row.settlementText, '赢 · +¥2.5k');
});

test('shows embedded odds once and removes insignificant trailing zeroes', () => {
  const row = tools.buildRealtimeOrderListRow({
    model: {
      marketText: '滚球 · 上半场 · 角球数',
      selectionText: '小 5.5 @ 0.60',
      oddsText: '0.60',
    },
  });

  assert.equal(row.marketText, '小5.5@0.6');
  assert.equal(row.marketCaption, '滚球 · 上半场 · 角球数');
});

test('adds valid odds once when the selection does not include them', () => {
  const row = tools.buildRealtimeOrderListRow({
    model: {
      marketText: '独赢',
      selectionText: '霍森斯',
      oddsText: '@ 1.00',
    },
  });

  assert.equal(row.marketText, '霍森斯@1');
});

test('keeps non-numeric odds text instead of losing source information', () => {
  const row = tools.buildRealtimeOrderListRow({
    model: {
      marketText: '让球',
      selectionText: '主队 -0.5',
      oddsText: '待定',
    },
  });

  assert.equal(row.marketText, '主队 -0.5@待定');
});

test('builds a search-only list row without fake betting data', () => {
  const row = tools.buildRealtimeOrderListRow({
    model: {
      league: '俄罗斯超级联赛',
      modeLabel: '滚球',
      homeTeam: '奥伦堡',
      awayTeam: '罗斯托夫',
      score: '0 : 0',
      statusLine: '上半场 | 20:54',
    },
    searchOnly: true,
  });

  assert.equal(row.orderKind, '滚球');
  assert.equal(row.marketText, '—');
  assert.equal(row.betAmountText, '—');
  assert.equal(row.followStatusText, '比赛查询');
  assert.equal(row.settlementText, '—');
});

test('keeps the existing realtime list DOM when rendered content is unchanged', () => {
  assert.equal(tools.shouldReplaceRealtimeOrderList('same-content', 'same-content', true), false);
  assert.equal(tools.shouldReplaceRealtimeOrderList('old-content', 'new-content', true), true);
  assert.equal(tools.shouldReplaceRealtimeOrderList('same-content', 'same-content', false), true);
});
