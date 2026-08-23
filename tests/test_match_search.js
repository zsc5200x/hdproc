const assert = require('node:assert/strict');
const test = require('node:test');

let tools = {};
try {
  tools = require('../app/webui/match_search.js');
} catch {
  tools = {};
}

test('normalizes chashui game rows into realtime match cards', () => {
  assert.equal(typeof tools.normalizeHgSearchGame, 'function', 'normalizeHgSearchGame should be exported');

  const model = tools.normalizeHgSearchGame(
    {
      gid: '8513893',
      league: '葡萄牙超级联赛',
      teamH: '里斯本竞技',
      teamC: '托德拉',
      score: '1:0',
      time: '04:49:22',
      ecid: '8739623',
      lid: '302',
    },
    { mode: 'live', memberId: 'm1' },
  );

  assert.equal(model.key, 'live:8513893');
  assert.equal(model.homeTeam, '里斯本竞技');
  assert.equal(model.awayTeam, '托德拉');
  assert.equal(model.score, '1 : 0');
  assert.equal(model.statusLine, '滚球 | 04:49:22');
  assert.equal(model.eventId, '8739623');
  assert.equal(model.memberId, 'm1');
});

test('formats raw HGA live clock as running half status', () => {
  assert.equal(typeof tools.resolveMatchStatusDisplay, 'function', 'resolveMatchStatusDisplay should be exported');

  const status = tools.resolveMatchStatusDisplay({
    mode: 'live',
    time: '1H^04:25',
    capturedAt: 1000,
  }, { now: 3000 });

  assert.equal(status.statusLine, '上半场 | 04:27');
  assert.equal(status.statusText, '上半场');
  assert.equal(status.isEnded, false);
});

test('detects ended match status for muted cards', () => {
  assert.equal(typeof tools.resolveMatchStatusDisplay, 'function', 'resolveMatchStatusDisplay should be exported');

  const status = tools.resolveMatchStatusDisplay({
    statusText: '完场',
    statusLine: '完场',
  });

  assert.equal(status.statusLine, '已结束');
  assert.equal(status.statusText, '已结束');
  assert.equal(status.isEnded, true);
});

test('filters games by league team gid score and time', () => {
  assert.equal(typeof tools.filterHgSearchGames, 'function', 'filterHgSearchGames should be exported');

  const games = [
    { gid: '1001', league: '英格兰南部甲组联赛', teamH: '希治', teamC: '卢莎奥林' },
    { gid: '1002', league: '葡萄牙超级联赛', teamH: '里斯本竞技', teamC: '托德拉', score: '1:0' },
  ];

  assert.deepEqual(tools.filterHgSearchGames(games, '葡萄牙').map((g) => g.gid), ['1002']);
  assert.deepEqual(tools.filterHgSearchGames(games, '希治').map((g) => g.gid), ['1001']);
  assert.deepEqual(tools.filterHgSearchGames(games, '1:0').map((g) => g.gid), ['1002']);
});

test('resolves monitored team numbers into both team names for match search', () => {
  assert.equal(typeof tools.buildHgOrderSearchContext, 'function', 'buildHgOrderSearchContext should be exported');

  const order = {
    orderId: '24928965904',
    raw: {
      NUM_H: '700336',
      NUM_C: '700335',
      TEAM_H: '奥勒松 ',
      TEAM_C: '维京 ',
      SHOWTEXT_TEAM_NUM: '<li>[700336] vs [700335]</li>',
    },
  };

  const byHomeId = tools.buildHgOrderSearchContext([order], '700336');
  assert.equal(byHomeId.matchedOrderId, '24928965904');
  assert.deepEqual(byHomeId.matchTerms, ['奥勒松', '维京']);

  const byPair = tools.buildHgOrderSearchContext([order], '[700336] vs [700335]');
  assert.equal(byPair.matchedOrderId, '24928965904');
  assert.deepEqual(byPair.teamIds, ['700336', '700335']);
});

test('resolves team names from the normalized order shape stored by the watcher', () => {
  const order = {
    orderId: '24928639926',
    sides: '霍森斯 v 洛斯查兰特 (1 - 1)',
    raw: {
      SHOWTEXT_TEAM_NUM: '<li class="re_betdetail_liMini">[700034] vs [700033]</li>',
      SHOWTEXT_TEAM: '<li>霍森斯 v 洛斯查兰特 <tt>(1 - 1)</tt></li>',
    },
  };

  const context = tools.buildHgOrderSearchContext([order], '700033');
  assert.equal(context.matchedOrderId, '24928639926');
  assert.deepEqual(context.teamIds, ['700034', '700033']);
  assert.deepEqual(context.matchTerms, ['霍森斯', '洛斯查兰特']);
});

test('uses resolved team-number aliases to identify the exact game', () => {
  const games = [
    { gid: '2001', league: '挪威超级联赛', teamH: '奥勒松', teamC: '维京' },
    { gid: '2002', league: '挪威超级联赛', teamH: '奥勒松', teamC: '利勒斯特罗姆' },
  ];

  assert.deepEqual(
    tools.filterHgSearchGames(games, '700336', { matchTerms: ['奥勒松', '维京'] }).map((g) => g.gid),
    ['2001'],
  );
});

test('searches provider team numbers directly from the game list', () => {
  const games = [
    { gid: '2001', gnumH: '700336', gnumC: '700335', league: '挪威超级联赛', teamH: '奥勒松', teamC: '维京' },
    { gid: '2002', gnumH: '700142', gnumC: '700141', league: '挪威超级联赛', teamH: '奥勒松', teamC: '利勒斯特罗姆' },
  ];

  assert.deepEqual(tools.filterHgSearchGames(games, '700335').map((g) => g.gid), ['2001']);
  const model = tools.normalizeHgSearchGame(games[0]);
  assert.equal(model.gnumH, '700336');
  assert.equal(model.gnumC, '700335');
});

test('keeps successful match search modes when another mode fails', () => {
  assert.equal(typeof tools.collectHgSearchSettledResults, 'function', 'collectHgSearchSettledResults should be exported');

  const result = tools.collectHgSearchSettledResults([
    { status: 'fulfilled', value: [] },
    { status: 'fulfilled', value: [] },
    { status: 'rejected', reason: new Error('请求失败（HTTP 502）：coupon_date error') },
  ]);

  assert.deepEqual(result.rows, []);
  assert.equal(result.error, '');
  assert.equal(result.warning, '部分比赛分类暂不可用，已展示其余分类的查询结果。');
});

test('reports an error only when every match search mode fails', () => {
  const result = tools.collectHgSearchSettledResults([
    { status: 'rejected', reason: new Error('请求失败（HTTP 502）：coupon_date error') },
  ]);

  assert.deepEqual(result.rows, []);
  assert.equal(result.warning, '');
  assert.equal(result.error, '请求失败（HTTP 502）：coupon_date error');
});

test('retained match helpers dedupe and refresh existing rows', () => {
  assert.equal(typeof tools.upsertRetainedHgMatch, 'function', 'upsertRetainedHgMatch should be exported');
  assert.equal(typeof tools.mergeRetainedHgMatches, 'function', 'mergeRetainedHgMatches should be exported');
  assert.equal(typeof tools.removeRetainedHgMatch, 'function', 'removeRetainedHgMatch should be exported');

  const retained = tools.upsertRetainedHgMatch([], { key: 'live:1', gid: '1', score: '0 : 0', retainedAt: 100 });
  const deduped = tools.upsertRetainedHgMatch(retained, { key: 'live:1', gid: '1', score: '1 : 0', retainedAt: 200 });
  assert.equal(deduped.length, 1);
  assert.equal(deduped[0].score, '1 : 0');
  assert.equal(deduped[0].retainedAt, 100);

  const refreshed = tools.mergeRetainedHgMatches(deduped, [{ key: 'live:1', gid: '1', score: '2 : 0', statusLine: '滚球 | 10:00' }]);
  assert.equal(refreshed[0].score, '2 : 0');
  assert.equal(refreshed[0].statusLine, '滚球 | 10:00');

  assert.deepEqual(tools.removeRetainedHgMatch(refreshed, 'live:1'), []);
});

test('builds realtime event detail cards from normalized match data', () => {
  assert.equal(typeof tools.buildHgEventDetailItems, 'function', 'buildHgEventDetailItems should be exported');

  const model = tools.normalizeHgSearchGame({
    gid: '8747559',
    ecid: '11032650',
    mtId: '70989620',
    league: '哥伦比亚乙组联赛-附加赛',
    teamH: '依维加杜',
    teamC: '老虎',
    score: '2:0',
    time: '2H^57:30',
  }, { mode: 'live' });

  assert.deepEqual(tools.buildHgEventDetailItems(model), [
    { label: '事件ID', value: '11032650' },
    { label: '数据ID', value: '70989620' },
    { label: '当前比分', value: '2 : 0' },
    { label: '比赛状态', value: '下半场 | 57:30' },
    { label: '主队', value: '依维加杜' },
    { label: '客队', value: '老虎' },
  ]);
});

test('applies realtime event snapshots to retained match cards', () => {
  assert.equal(typeof tools.applyHgEventSnapshotToMatch, 'function', 'applyHgEventSnapshotToMatch should be exported');

  const updated = tools.applyHgEventSnapshotToMatch({
    key: 'live:8742321',
    gid: '8742321',
    mtId: '71059310',
    league: '乌拉圭乙组联赛',
    homeTeam: '壬提塔斯',
    awayTeam: '米斯奥尼斯队',
    score: '1 : 0',
    statusLine: '滚球 | 2H^87:25',
    mode: 'live',
  }, {
    meta: { mtId: '71059310', mode: 'live' },
    timeline: {
      matchId: '71059310',
      homeTeam: '伦蒂斯塔斯',
      awayTeam: 'Miramar Misiones',
      score: '1 : 1',
      statusText: '完场',
    },
  });

  assert.equal(updated.score, '1 : 1');
  assert.equal(updated.statusLine, '已结束');
  assert.equal(updated.statusText, '已结束');
  assert.equal(updated.mtId, '71059310');
});

test('uses realtime event minutes when retained match clock is stale', () => {
  assert.equal(typeof tools.applyHgEventSnapshotToMatch, 'function', 'applyHgEventSnapshotToMatch should be exported');

  const updated = tools.applyHgEventSnapshotToMatch({
    key: 'live:8730017',
    gid: '8730017',
    mtId: '70075238',
    league: '南美洲球会杯',
    homeTeam: '帕勒斯蒂诺',
    awayTeam: '甘美奥RS',
    score: '0 : 0',
    time: '1H^04:04',
    statusLine: '上半场 | 04:04',
    mode: 'live',
  }, {
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
  });

  assert.equal(updated.time, '1H^34:04');
  assert.equal(updated.statusLine, '上半场 | 34:04');
  assert.equal(updated.statusText, '上半场');
});

test('includes injury time when deriving card clock from events', () => {
  const updated = tools.applyHgEventSnapshotToMatch({
    key: 'live:8730017',
    gid: '8730017',
    time: '1H^45:00',
    statusLine: '上半场 | 45:00',
    mode: 'live',
  }, {
    meta: { mtId: '70075238', mode: 'live' },
    timeline: {
      matchId: '70075238',
      score: '0 : 0',
      statusText: '上半场',
      events: [
        { id: 'injury-45-5', type: 'injurytimeshown', minute: 45, injuryTime: 5, seconds: 2700 },
      ],
    },
  });

  assert.equal(updated.time, '1H^50:00');
  assert.equal(updated.statusLine, '上半场 | 50:00');
});

test('merges realtime event timeline deltas by id and newest match state', () => {
  assert.equal(typeof tools.mergeHgEventTimeline, 'function', 'mergeHgEventTimeline should be exported');

  const previous = {
    matchId: '70989620',
    score: '1 : 0',
    statusText: '上半场',
    events: [
      { id: 'goal-1', type: 'goal', minute: 12, injuryTime: 0, seconds: 720, score: '1 : 0' },
      { id: 'card-1', type: 'card', minute: 8, injuryTime: 0, seconds: 480 },
    ],
  };
  const incoming = {
    matchId: '70989620',
    score: '2 : 0',
    statusText: '下半场',
    events: [
      { id: 'goal-2', type: 'goal', minute: 55, injuryTime: 0, seconds: 3300, score: '2 : 0' },
      { id: 'goal-1', type: 'goal', minute: 12, injuryTime: 0, seconds: 721, score: '1 : 0' },
    ],
  };

  const merged = tools.mergeHgEventTimeline(previous, incoming);
  assert.equal(merged.score, '2 : 0');
  assert.equal(merged.statusText, '下半场');
  assert.deepEqual(merged.events.map((event) => event.id), ['goal-2', 'goal-1', 'card-1']);
  assert.equal(merged.events[1].seconds, 721);
});

test('formats realtime event source errors in readable Chinese', () => {
  assert.equal(typeof tools.formatHgEventErrorMessage, 'function', 'formatHgEventErrorMessage should be exported');

  assert.equal(
    tools.formatHgEventErrorMessage('missing_match_id'),
    '当前比赛没有可用的事件流编号，暂时无法读取比赛事件。',
  );
  assert.equal(
    tools.formatHgEventErrorMessage('missing_betradar_feed_token'),
    '当前缺少比赛事件源授权，请先采集或配置 BETRADAR_FEED_TOKEN。',
  );
  assert.equal(
    tools.formatHgEventErrorMessage('resolve_match_id_failed: game_not_found'),
    '当前赛事不在该会员实时列表中，暂时无法解析事件流编号。',
  );
});

test('builds qhg style odds sections from chashui odds snapshot', () => {
  assert.equal(typeof tools.buildHgOddsSections, 'function', 'buildHgOddsSections should be exported');

  const sections = tools.buildHgOddsSections({
    meta: {
      teamH: '哈科尼斯乌鲁阿潘',
      teamC: '亚拉克莱恩斯',
    },
    simple: {
      handicap: {
        lines: [
          {
            wtype: 'RE',
            home: { team: '哈科尼斯乌鲁阿潘', line: '+0/0.5', odd: '0.96' },
            away: { team: '亚拉克莱恩斯', line: '-0/0.5', odd: '0.88' },
          },
        ],
      },
      overUnder: {
        lines: [
          {
            wtype: 'ROU',
            over: { label: '大', line: '2', odd: '1.04' },
            under: { label: '小', line: '2', odd: '0.78' },
          },
        ],
      },
      moneyLine: {
        home: { team: '哈科尼斯乌鲁阿潘', odd: '3.100' },
        draw: { label: '和局', odd: '2.840' },
        away: { team: '亚拉克莱恩斯', odd: '2.110' },
      },
    },
  });

  assert.deepEqual(sections.map((section) => section.label), ['全场让球', '全场大小', '独赢']);
  assert.deepEqual(sections[0].entries, [
    { label: '哈科尼斯乌鲁阿潘 +0/0.5', odd: '0.96' },
    { label: '亚拉克莱恩斯 -0/0.5', odd: '0.88' },
  ]);
  assert.deepEqual(sections[1].entries, [
    { label: '大 2', odd: '1.04' },
    { label: '小 2', odd: '0.78' },
  ]);
  assert.deepEqual(sections[2].entries, [
    { label: '哈科尼斯乌鲁阿潘', odd: '3.100' },
    { label: '和局', odd: '2.840' },
    { label: '亚拉克莱恩斯', odd: '2.110' },
  ]);
});

test('keeps half-time odds in separate qhg style sections', () => {
  assert.equal(typeof tools.buildHgOddsSections, 'function', 'buildHgOddsSections should be exported');

  const sections = tools.buildHgOddsSections({
    simple: {
      handicap: {
        lines: [
          {
            wtype: 'HRE',
            home: { team: '主队', line: '0', odd: '0.91' },
            away: { team: '客队', line: '0', odd: '0.93' },
          },
        ],
      },
      overUnder: {
        lines: [
          {
            wtype: 'HROU',
            over: { label: '大', line: '0.5', odd: '0.82' },
            under: { label: '小', line: '0.5', odd: '1.02' },
          },
        ],
      },
    },
  });

  assert.deepEqual(sections.map((section) => section.label), ['上半场让球', '上半场大小']);
});
