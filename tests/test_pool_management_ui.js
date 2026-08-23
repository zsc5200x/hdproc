const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'app/webui/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'app/webui/app.js'), 'utf8');

test('arsenal view exposes monitor grid, member table, and monitoring rail together', () => {
  assert.match(page, /class="pool-page-shell"/);
  assert.match(page, /class="pool-monitor-section/);
  assert.match(page, /class="pool-monitor-section pool-surface" aria-label="监控号池"/);
  assert.match(page, /class="pool-member-section pool-surface" aria-label="会员号池"/);
  assert.match(page, /class="pool-member-table"/);
  assert.match(page, /id="monitor-pool-cards"/);
  assert.match(page, /id="pool-overview"/);
  assert.match(page, /id="pool-trend-chart"/);
  assert.match(page, /id="pool-source-donut"/);
  assert.match(page, /id="btn-add-monitor"/);
  assert.doesNotMatch(page, /id="pool-tab-members"/);
  assert.doesNotMatch(page, /id="pool-tab-monitors"/);
  assert.doesNotMatch(page, /class="pool-page-heading"/);
  assert.doesNotMatch(page, /监控与会员中心/);
  assert.doesNotMatch(page, /统一管理多号分配/);
  assert.doesNotMatch(page, /id="pool-last-updated"/);
  assert.doesNotMatch(page, /id="monitor-pool-title"/);
  assert.doesNotMatch(page, /id="monitor-pool-filter-summary"/);
  assert.doesNotMatch(page, /class="pool-pagination"/);
  assert.doesNotMatch(page, /id="pool-page-(?:prev|current|total|next|size)"/);
  assert.doesNotMatch(page, /id="pool-filtered-count"/);
  assert.doesNotMatch(page, /会员号池总览/);
  assert.doesNotMatch(page, /id="member-pool-title/);
  assert.doesNotMatch(page, /id="member-pool-search"/);
  assert.doesNotMatch(script, /pool-last-updated/);
  assert.doesNotMatch(script, /monitor-pool-filter-summary/);
  assert.doesNotMatch(script, /pool-page-(?:prev|current|total|next|size)/);
  assert.doesNotMatch(script, /pool-filtered-count/);
  assert.doesNotMatch(script, /member-pool-(?:title|search)/);
  assert.doesNotMatch(script, /memberSearch|pageSize/);
  assert.doesNotMatch(script, /filtered\.slice\(/);
  assert.match(script, /filtered\.forEach\(\(source\) => grid\.appendChild\(renderMonitorSourceCard\(source\)\)\)/);
});

test('sidebar keeps signal source status list without the add shortcut', () => {
  assert.match(page, /信号源节点/);
  assert.match(page, /id="accounts-list"/);
  assert.doesNotMatch(page, /id="btn-add-account"/);
  assert.doesNotMatch(script, /btn-add-account/);
});

test('app script renders monitor pool sources from accounts and signal config', () => {
  assert.match(script, /poolView:\s*"overview"/);
  assert.match(script, /function setPoolView\(poolView\)/);
  assert.match(script, /function buildMonitorPoolSources\(\)/);
  assert.match(script, /function renderMonitorPool\(\)/);
  assert.match(script, /function renderMonitorSourceCard/);
  assert.match(script, /function filterMonitorPoolSources/);
  assert.match(script, /function renderPoolTrendChart/);
  assert.match(script, /state\.accounts/);
  assert.match(script, /state\.signalPullConfig/);
});

test('binds sidebar navigation before initial network refresh can block it', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const bindNavigationCall = script.indexOf('bindNavigation();', loadInitialStart);
  const firstNetworkLoad = script.indexOf('await loadSignalPullConfig();', loadInitialStart);

  assert.notEqual(loadInitialStart, -1, 'loadInitial should exist');
  assert.match(script, /function bindNavigation\(\)/);
  assert.notEqual(bindNavigationCall, -1, 'loadInitial should bind navigation');
  assert.notEqual(firstNetworkLoad, -1, 'loadInitial should load initial data');
  assert.ok(
    bindNavigationCall < firstNetworkLoad,
    'navigation must be bound before initial API requests'
  );
});

test('binds the add-node dialog before initial network refresh can block it', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const bindDialogCall = script.indexOf('bindAddNodeModalControls();', loadInitialStart);
  const firstNetworkLoad = script.indexOf('await loadSignalPullConfig();', loadInitialStart);

  assert.match(script, /function bindAddNodeModalControls\(\)/);
  assert.notEqual(bindDialogCall, -1, 'loadInitial should bind the add-node dialog');
  assert.notEqual(firstNetworkLoad, -1, 'loadInitial should load initial data');
  assert.ok(
    bindDialogCall < firstNetworkLoad,
    'the add-node dialog must be operable before initial API requests'
  );
});

test('loads the member pool before the long order enrichment refresh', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const loadArsenalCall = script.indexOf('await loadArsenal();', loadInitialStart);
  const refreshViewCall = script.indexOf('await refreshView();', loadInitialStart);

  assert.notEqual(loadArsenalCall, -1, 'initial load should fetch the member pool');
  assert.notEqual(refreshViewCall, -1, 'initial load should refresh orders');
  assert.ok(
    loadArsenalCall < refreshViewCall,
    'member pool must render before live match enrichment can delay the page'
  );
});
