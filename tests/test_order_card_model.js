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
      HDMatchSearch: {},
      HDOrderRealtimeBridge: {
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
