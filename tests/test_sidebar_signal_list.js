const test = require('node:test');
const assert = require('node:assert/strict');

const { buildSidebarItems, computeTradingNodeStatus } = require('../app/webui/sidebar_items.js');

test('prefers configured accounts in sidebar when accounts exist', () => {
  const items = buildSidebarItems({
    accounts: [{ name: 'acc-01' }],
    statuses: { 'acc-01': { status: 'online' } },
    signalPullConfig: { signal_pull_enabled: true, signal_pull_url: 'http://168.93.214.95:8021/signals/json' },
    orders: [{ target: 'sig-01', ts: 1710000000 }],
  });

  assert.equal(items.length, 2);
  assert.deepEqual(items[0], {
    key: 'account:acc-01',
    kind: 'account',
    name: 'acc-01',
    badge: '账号节点',
    statusClass: 'bg-green-500 animate-pulse',
    statusText: '在线',
    metaText: '',
    latestTs: 0,
  });
  assert.deepEqual(items[1], {
    key: 'signal-source:168.93.214.95:8021',
    kind: 'signal-source',
    filterTarget: '__remote_signal_pull__',
    canDelete: true,
    deleteEndpoint: '/api/signal-pull-config/delete',
    name: '远端交易节点',
    badge: '远端交易节点',
    statusClass: 'bg-cyan-400 animate-pulse',
    statusText: '已接入',
    metaText: '168.93.214.95:8021 · 1 条信号',
    latestTs: 1710000000,
  });
});

test('shows remote signal node instead of signal target names when no accounts exist', () => {
  const items = buildSidebarItems({
    accounts: [],
    statuses: {},
    signalPullConfig: {
      signal_pull_enabled: true,
      signal_pull_url: 'http://168.93.214.95:8021/signals/json',
      signal_pull_live_only: true,
    },
    orders: [
      { target: 'sig-a', ts: 1710000123, league: '联赛A' },
      { target: 'sig-a', ts: 1710000223, league: '联赛B' },
      { target: 'sig-b', ts: 1710000023, league: '联赛C' },
    ],
  });

  assert.equal(items.length, 1);
  assert.deepEqual(items[0], {
    key: 'signal-source:168.93.214.95:8021',
    kind: 'signal-source',
    filterTarget: '__remote_signal_pull__',
    canDelete: true,
    deleteEndpoint: '/api/signal-pull-config/delete',
    name: '远端交易节点',
    badge: '远端交易节点',
    statusClass: 'bg-cyan-400 animate-pulse',
    statusText: '已接入',
    metaText: '168.93.214.95:8021 · 3 条信号',
    latestTs: 1710000223,
  });
});

test('shows configured remote node from webui config response and counts remote orders only', () => {
  const items = buildSidebarItems({
    accounts: [{ name: 'acc-01' }],
    statuses: { 'acc-01': { status: 'online' } },
    signalPullConfig: {
      ok: true,
      config: {
        signal_pull_enabled: true,
        signal_pull_url: 'http://168.93.214.95:8021/signals/json',
      },
      configured: true,
    },
    orders: [
      { target: 'acc-01', ts: 1710000100 },
      { target: 'acc-01', ts: 1710000200, _signal_origin: 'remote_signal_pull' },
      { target: 'other-remote', ts: 1710000300, _signal_origin: 'remote_signal_pull' },
    ],
  });

  assert.equal(items.length, 2);
  assert.deepEqual(items[1], {
    key: 'signal-source:168.93.214.95:8021',
    kind: 'signal-source',
    filterTarget: '__remote_signal_pull__',
    canDelete: true,
    deleteEndpoint: '/api/signal-pull-config/delete',
    name: '远端交易节点',
    badge: '远端交易节点',
    statusClass: 'bg-cyan-400 animate-pulse',
    statusText: '已接入',
    metaText: '168.93.214.95:8021 · 2 条信号',
    latestTs: 1710000300,
  });
});

test('counts online trading nodes including enabled remote signal source', () => {
  const status = computeTradingNodeStatus({
    accounts: [
      { name: 'zxcv226699' },
      { name: 'summer001' },
    ],
    statuses: {
      zxcv226699: { status: 'online' },
      summer001: { status: 'online' },
    },
    signalPullConfig: {
      config: {
        signal_pull_enabled: true,
        signal_pull_url: 'http://168.93.214.95:8021/signals/json',
      },
    },
  });

  assert.deepEqual(status, {
    online: 3,
    total: 3,
    label: '3 / 3',
  });
});
