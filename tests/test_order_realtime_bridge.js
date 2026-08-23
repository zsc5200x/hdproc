const test = require('node:test');
const assert = require('node:assert/strict');

const {
  applyEventSnapshotsToOrders,
  applyLiveMatchSnapshotsToOrders,
  mergeFollowQueueOrders,
} = require('../app/webui/order_realtime_bridge.js');

test('merges pending follow queue orders into realtime order list', () => {
  const rows = mergeFollowQueueOrders([], [
    {
      key: 'qax228|23817899020',
      status: 'pending',
      order: {
        target: 'qax228',
        orderId: '23817899020',
        ts: 1777505712,
        league: '南美自由杯',
        sides: '迈拉索尔SP v 阿拉维预备 (1 - 0)',
        betType: '让球',
        line: '迈拉索尔SP -0 / 0.5',
        gid: '8729941',
        derived: { gid: '8729941', mode: 'live' },
      },
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].orderId, '23817899020');
  assert.equal(rows[0].raw._EVENT, '8729941');
  assert.equal(rows[0].orderType, '滚球');
  assert.equal(rows[0].status_label, '待入队');
  assert.equal(rows[0]._follow_queue.status, 'pending');
});

test('dedupes queue rows against existing orders while keeping queue status', () => {
  const rows = mergeFollowQueueOrders([
    {
      target: 'qax228',
      orderId: '23817899020',
      ts: 1777505712,
      league: '南美自由杯',
      sides: '迈拉索尔SP v 阿拉维预备 (1 - 0)',
      raw: { _EVENT: '8729941' },
      orderType: '滚球',
    },
  ], [
    {
      key: 'qax228|23817899020',
      status: 'pending',
      order: {
        target: 'qax228',
        orderId: '23817899020',
        gid: '8729941',
        derived: { gid: '8729941', mode: 'live' },
      },
    },
  ]);

  assert.equal(rows.length, 1);
  assert.equal(rows[0]._follow_queue.status, 'pending');
  assert.equal(rows[0].status_label, '待入队');
  assert.equal(rows[0].raw._EVENT, '8729941');
});

test('dedupes realtime order rows by order id before rendering cards', () => {
  const rows = mergeFollowQueueOrders([
    {
      target: 'zxcv226699',
      orderId: '23818561034',
      ts: 1777518703,
      league: '英格兰超级联赛',
      sides: '主队 v 客队',
      raw: { _EVENT: '8730079' },
      orderType: '滚球',
    },
    {
      target: 'zxcv226699',
      orderId: '23818561034',
      ts: 1777518703,
      league: '英格兰超级联赛',
      sides: '主队 v 客队',
      raw: { _EVENT: '8730079' },
      orderType: '滚球',
    },
  ], []);

  assert.equal(rows.length, 1);
  assert.equal(rows[0].orderId, '23818561034');
  assert.equal(rows[0].target, 'zxcv226699');
});

test('applies live chashui match snapshots to orders by gid', () => {
  const rows = applyLiveMatchSnapshotsToOrders([
    {
      target: 'qax228',
      orderId: '23817899020',
      league: '南美自由杯',
      sides: '迈拉索尔SP v 阿拉维预备 (1 - 0)',
      raw: { _EVENT: '8729941' },
      orderType: '滚球',
    },
  ], [
    {
      gid: '8729941',
      league: '南美自由杯',
      teamH: '迈拉索尔SP',
      teamC: '阿拉维预备',
      score: '2:0',
      time: '2H^87:37',
      ecid: '10985694',
      mtId: '70076014',
    },
  ], { mode: 'live', memberId: 'm1', memberLabel: 'summer011' });

  assert.equal(rows[0]._live_match.gid, '8729941');
  assert.equal(rows[0]._live_match.score, '2 : 0');
  assert.equal(rows[0]._live_match.statusLine, '下半场 | 87:37');
  assert.equal(rows[0]._live_match.memberId, 'm1');
});

test('falls back to league and team matching when order gid differs from live master gid', () => {
  const rows = applyLiveMatchSnapshotsToOrders([
    {
      target: 'qax228',
      orderId: '23817821521',
      league: '巴西南东南杯',
      sides: '乔沛高恩斯SC v 阿美利加MG (1 - 1)',
      raw: { _EVENT: '8740379' },
      orderType: '滚球',
    },
  ], [
    {
      gid: '8740377',
      league: '巴西南东南杯',
      teamH: '乔沛高恩斯SC',
      teamC: '阿美利加MG',
      score: '2:1',
      time: '2H^71:43',
      ecid: '11011436',
      mtId: '69977744',
    },
  ], { mode: 'live', memberId: 'm1' });

  assert.equal(rows[0]._live_match.gid, '8740377');
  assert.equal(rows[0]._live_match.score, '2 : 1');
  assert.equal(rows[0].raw._EVENT, '8740377');
  assert.equal(rows[0].raw.ORDER_EVENT, '8740379');
});

test('marks unmatched live orders after a completed live lookup', () => {
  const rows = applyLiveMatchSnapshotsToOrders([
    {
      target: 'qax228',
      orderId: '23817899020',
      league: '南美自由杯',
      sides: '迈拉索尔SP v 阿拉维预备 (1 - 0)',
      raw: { _EVENT: '8729941', MARKET: 'rb' },
      orderType: '滚球',
    },
  ], [
    {
      gid: '8729953',
      league: '南美自由杯',
      teamH: '波特诺山丘',
      teamC: '玻利瓦尔',
      score: '0:0',
      time: '1H^24:49',
    },
  ], { mode: 'live', memberId: 'm1', checkedAt: 1234 });

  assert.equal(rows[0]._live_match, undefined);
  assert.equal(rows[0]._live_match_lookup.matched, false);
  assert.equal(rows[0]._live_match_lookup.mode, 'live');
  assert.equal(rows[0]._live_match_lookup.gid, '8729941');
});

test('applies realtime event snapshot score back to order cards', () => {
  assert.equal(typeof applyEventSnapshotsToOrders, 'function', 'event snapshot bridge should be exported');

  const rows = applyEventSnapshotsToOrders([
    {
      target: 'qax228',
      orderId: '23817753583',
      league: '乌拉圭乙组联赛',
      sides: '壬提塔斯 v 米斯奥尼斯队 (0 - 0)',
      raw: { _EVENT: '8742321' },
      orderType: '滚球',
    },
  ], [
    {
      gid: '8742321',
      snapshot: {
        meta: { mtId: '71059310', mode: 'live' },
        timeline: {
          matchId: '71059310',
          homeTeam: '伦蒂斯塔斯',
          awayTeam: 'Miramar Misiones',
          score: '1 : 1',
          statusText: '完场',
        },
      },
    },
  ], { memberId: 'm1', memberLabel: 'summer011' });

  assert.equal(rows[0]._live_match.gid, '8742321');
  assert.equal(rows[0]._live_match.mtId, '71059310');
  assert.equal(rows[0]._live_match.score, '1 : 1');
  assert.equal(rows[0]._live_match.statusLine, '已结束');
  assert.equal(rows[0].raw.MT_ID, '71059310');
});

test('derives live order card clock from realtime event minutes', () => {
  const rows = applyEventSnapshotsToOrders([
    {
      target: 'qax228',
      orderId: '23817900001',
      league: '南美洲球会杯',
      sides: '帕勒斯蒂诺 v 甘美奥RS (0 - 0)',
      raw: { _EVENT: '8730017', MARKET: 'rb' },
      orderType: '滚球',
    },
  ], [
    {
      gid: '8730017',
      snapshot: {
        meta: { mtId: '70075238', mode: 'live' },
        timeline: {
          matchId: '70075238',
          score: '0 : 0',
          statusText: '上半场',
          events: [
            { id: 'corner-34', type: 'corner', minute: 34, seconds: 2044 },
            { id: 'throw-33', type: 'throwin', minute: 33, seconds: 1980 },
          ],
        },
      },
    },
  ], { memberId: 'm1', memberLabel: 'summer011' });

  assert.equal(rows[0]._live_match.time, '1H^34:04');
  assert.equal(rows[0]._live_match.statusLine, '上半场 | 34:04');
  assert.equal(rows[0]._live_match.statusText, '上半场');
});
