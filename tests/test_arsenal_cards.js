const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildArsenalCardModel,
  computeArsenalMemberStats,
  computePoolEligibility,
  filterPoolMembers,
} = require('../app/webui/arsenal_cards.js');

test('builds a localized arsenal member card model', () => {
  const model = buildArsenalCardModel(
    {
      id: 'm1',
      label: 'summer011',
      username: 'summer011',
      base_url: 'https://205.201.0.120',
      status: 'online',
      proxy_ip: '本地代理',
      proxy_geo: { ok: true, country: '中国', region: '香港' },
      auto_relogin: true,
      can_auto_relogin: true,
      maxcredit: '15000',
      nowcredit: '12000',
      session_provider: 'native',
      usage: 'bet',
    },
    {
      mappingTarget: 'backend-01',
      mappingLabel: '后台一号',
    },
  );

  assert.equal(model.id, 'm1');
  assert.equal(model.label, 'summer011');
  assert.equal(model.platformHost, '205.201.0.120');
  assert.equal(model.statusText, '在线');
  assert.equal(model.statusTone, 'online');
  assert.equal(model.proxyText, '本地代理');
  assert.equal(model.geoText, '中国 香港');
  assert.equal(model.mappingText, '后台一号');
  assert.equal(model.selectValue, 'backend-01');
  assert.equal(model.balanceText, '15000');
  assert.equal(model.eligibilityTone, 'ready');
  assert.equal(model.eligibilityText, '自动候选中');
  assert.equal(model.autoReloginText, '自动重登');
  assert.equal(model.statusMetaText, '在线 · 原生登录');
  assert.equal(model.balanceStateText, '系统会按订单金额自动筛选');
  assert.equal(model.usageText, '跟单用');
});

test('falls back to all-follow mapping and relogin status', () => {
  const model = buildArsenalCardModel(
    {
      id: 'm2',
      username: 'fallback-user',
      base_url: 'not a url',
      status: 'relogin',
      usage: 'read',
    },
    {},
  );

  assert.equal(model.label, 'fallback-user');
  assert.equal(model.platformHost, 'not a url');
  assert.equal(model.statusText, '重登中');
  assert.equal(model.statusTone, 'warning');
  assert.equal(model.mappingText, '全部跟单');
  assert.equal(model.proxyText, '未配置代理');
  assert.equal(model.geoText, '未识别地区');
  assert.equal(model.eligibilityTone, 'recovering');
  assert.equal(model.autoReloginText, '重登中');
  assert.equal(model.statusMetaText, '重登中 · 原生登录');
  assert.equal(model.balanceStateText, '恢复后自动回到候选池');
  assert.equal(model.usageText, '拉数据用');
});

test('computes pool stats by candidate and recoverability', () => {
  const stats = computeArsenalMemberStats([
    { id: 'm1', status: 'online', maxcredit: '15000', auto_relogin: true, session_provider: 'native', base_url: 'https://a', username: 'u1', password: 'p1' },
    { id: 'm2', status: 'online', maxcredit: '0', auto_relogin: true, session_provider: 'native', base_url: 'https://b', username: 'u2', password: 'p2' },
    { id: 'm3', status: 'relogin', maxcredit: '5000', auto_relogin: true, session_provider: 'native', base_url: 'https://c', username: 'u3', password: 'p3' },
    { id: 'm4', status: 'offline', maxcredit: '9000', auto_relogin: false, session_provider: 'native', base_url: 'https://d', username: 'u4', password: 'p4' },
  ]);

  assert.deepEqual(stats, {
    total: 4,
    online: 2,
    offline: 2,
    maintenance: 0,
    ready: 1,
    lowBalance: 1,
    recovering: 1,
    manual: 1,
    totalBalance: 29000,
  });
});

test('models maintenance separately from offline and disables pool eligibility', () => {
  const members = [
    { id: 'm1', status: 'maintenance', maxcredit: '12000', auto_relogin: true },
    { id: 'm2', status: 'offline', maxcredit: '5000', auto_relogin: true },
  ];
  const model = buildArsenalCardModel(members[0], {});
  const stats = computeArsenalMemberStats(members);

  assert.equal(model.statusText, '平台维护');
  assert.equal(model.statusTone, 'maintenance');
  assert.equal(model.eligibilityTone, 'maintenance');
  assert.equal(model.canJoinRandomPool, false);
  assert.equal(model.autoReloginText, '维护后自动验证');
  assert.equal(stats.maintenance, 1);
  assert.equal(stats.offline, 1);
});

test('computes eligibility using balance and recoverability rules', () => {
  const ready = computePoolEligibility({
    id: 'm1',
    status: 'online',
    maxcredit: '12000',
    auto_relogin: true,
    session_provider: 'native',
    base_url: 'https://a',
    username: 'u1',
    password: 'p1',
  }, {});
  assert.equal(ready.tone, 'ready');

  const low = computePoolEligibility({
    id: 'm2',
    status: 'online',
    maxcredit: '0',
    auto_relogin: true,
    session_provider: 'native',
    base_url: 'https://b',
    username: 'u2',
    password: 'p2',
  }, {});
  assert.equal(low.tone, 'low');

  const manual = computePoolEligibility({
    id: 'm3',
    status: 'offline',
    maxcredit: '8000',
    auto_relogin: false,
    session_provider: 'native',
    base_url: 'https://c',
    username: 'u3',
    password: 'p3',
  }, {});
  assert.equal(manual.tone, 'manual');
});

test('filters pool members by health view', () => {
  const members = [
    { id: 'm1', status: 'online', maxcredit: '12000', auto_relogin: true, session_provider: 'native', base_url: 'https://a', username: 'u1', password: 'p1' },
    { id: 'm2', status: 'online', maxcredit: '0', auto_relogin: true, session_provider: 'native', base_url: 'https://b', username: 'u2', password: 'p2' },
    { id: 'm3', status: 'relogin', maxcredit: '5000', auto_relogin: true, session_provider: 'native', base_url: 'https://c', username: 'u3', password: 'p3' },
  ];
  const filtered = filterPoolMembers(members, { filter: 'ready' });
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].id, 'm1');
});
