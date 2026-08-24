const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const page = fs.readFileSync(path.join(root, 'app/webui/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'app/webui/app.js'), 'utf8');
const redesign = fs.readFileSync(path.join(root, 'app/webui/redesign.css'), 'utf8');

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
  const firstNetworkLoad = script.indexOf('await Promise.allSettled([', loadInitialStart);

  assert.notEqual(loadInitialStart, -1, 'loadInitial should exist');
  assert.match(script, /function bindNavigation\(\)/);
  assert.notEqual(bindNavigationCall, -1, 'loadInitial should bind navigation');
  assert.notEqual(firstNetworkLoad, -1, 'loadInitial should load initial data');
  assert.ok(
    bindNavigationCall < firstNetworkLoad,
    'navigation must be bound before initial API requests'
  );
});

test('starts control binding immediately without waiting for the load event', () => {
  assert.match(script, /function startInitialLoad\(\)/);
  assert.match(script, /typeof document\.readyState === "string"[\s\S]*?startInitialLoad\(\)/);
  assert.doesNotMatch(script, /addEventListener\("DOMContentLoaded"[\s\S]*?loadInitial/);
});

test('binds primary navigation before every optional feature script', () => {
  const bootstrapStart = page.indexOf('(function bootstrapPrimaryNavigation()');
  const firstFeatureScript = page.indexOf('<script src="add_node_form.js">');

  assert.notEqual(bootstrapStart, -1, 'the inline primary-navigation bootstrap should exist');
  assert.notEqual(firstFeatureScript, -1, 'the feature scripts should exist');
  assert.ok(bootstrapStart < firstFeatureScript, 'primary navigation must bind before feature scripts load');
  assert.match(page, /button\.dataset\.navBound = 'true'/);
  assert.match(page, /typeof window\.activateView === 'function'/);
  assert.match(page, /activateFallback\(document\.body\.dataset\.view \|\| 'orders'\)/);
  assert.match(script, /window\.activateView = activateView/);
});

test('loads optional external visual styles without blocking controls', () => {
  const externalStyles = Array.from(page.matchAll(/<link[^>]+href="https:\/\/[^>]+rel="stylesheet"[^>]*>/g), (match) => match[0]);
  const blockingStyles = externalStyles.filter((tag) => !tag.includes('media="print"'));

  assert.ok(externalStyles.length >= 2, 'the page should still load its external font and icon styles');
  assert.equal(blockingStyles.length, 2, 'only the noscript fallbacks may use blocking styles');
  assert.match(page, /media="print" onload="this\.media='all'"/);
  assert.match(page, /<noscript>[\s\S]*?fonts\.googleapis\.com[\s\S]*?cdnjs\.cloudflare\.com[\s\S]*?<\/noscript>/);
});

test('binds the add-node dialog before initial network refresh can block it', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const bindDialogCall = script.indexOf('bindAddNodeModalControls();', loadInitialStart);
  const firstNetworkLoad = script.indexOf('await Promise.allSettled([', loadInitialStart);

  assert.match(script, /function bindAddNodeModalControls\(\)/);
  assert.notEqual(bindDialogCall, -1, 'loadInitial should bind the add-node dialog');
  assert.notEqual(firstNetworkLoad, -1, 'loadInitial should load initial data');
  assert.ok(
    bindDialogCall < firstNetworkLoad,
    'the add-node dialog must be operable before initial API requests'
  );
});

test('uses a non-blocking in-page confirmation dialog for monitor account deletion', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const bindDialogCall = script.indexOf('bindAccountDeleteDialogControls();', loadInitialStart);
  const firstNetworkLoad = script.indexOf('await Promise.allSettled([', loadInitialStart);

  assert.match(page, /id="modal-account-delete"/);
  assert.match(page, /role="alertdialog"/);
  assert.match(page, /id="account-delete-confirm"[^>]+aria-busy="false"/);
  assert.match(script, /function requestAccountDeleteConfirmation\(label\)/);
  assert.match(script, /function bindAccountDeleteDialogControls\(\)/);
  assert.match(script, /function confirmDeleteAccount\(name, label\)/);
  assert.doesNotMatch(script, /confirm\(`确定要删除监控目标/);
  assert.match(script, /fetchJson\("\/api\/accounts\/delete"/);
  assert.notEqual(bindDialogCall, -1, 'loadInitial should bind the account delete dialog');
  assert.notEqual(firstNetworkLoad, -1, 'loadInitial should load initial data');
  assert.ok(
    bindDialogCall < firstNetworkLoad,
    'the account delete dialog must be operable before initial API requests'
  );
});

test('loads initial shell data in parallel before refreshing orders', () => {
  const loadInitialStart = script.indexOf('async function loadInitial');
  const initialBatchCall = script.indexOf('await Promise.allSettled([', loadInitialStart);
  const loadArsenalCall = script.indexOf('loadArsenal(),', initialBatchCall);
  const refreshViewCall = script.indexOf('await refreshView();', loadInitialStart);

  assert.notEqual(initialBatchCall, -1, 'initial load should start a parallel request batch');
  assert.notEqual(loadArsenalCall, -1, 'initial load should fetch the member pool');
  assert.notEqual(refreshViewCall, -1, 'initial load should refresh orders');
  assert.ok(
    initialBatchCall < loadArsenalCall && loadArsenalCall < refreshViewCall,
    'shell data should settle before the order refresh starts'
  );
});

test('renders local orders before slow live-match enrichment', () => {
  const refreshStart = script.indexOf('async function refreshView');
  const assignLocalOrders = script.indexOf('state.orders = applyOrderEventSnapshotsForDisplay(mergedOrders);', refreshStart);
  const renderOrders = script.indexOf('renderOrdersView();', assignLocalOrders);
  const scheduleEnrichment = script.indexOf('refreshOrderLiveContext(state.orders, refreshSeq)', renderOrders);

  assert.notEqual(assignLocalOrders, -1, 'refresh should assign local orders immediately');
  assert.notEqual(renderOrders, -1, 'refresh should render local orders');
  assert.notEqual(scheduleEnrichment, -1, 'refresh should schedule progressive live enrichment');
  assert.ok(assignLocalOrders < renderOrders && renderOrders < scheduleEnrichment);
  assert.doesNotMatch(script.slice(refreshStart, scheduleEnrichment), /await enrichOrdersWithLiveMatches/);
});

test('limits polling and pauses order refresh outside the visible dashboard', () => {
  assert.match(script, /const ORDER_AUTO_REFRESH_MS = 10000;/);
  assert.match(script, /const LIVE_CONTEXT_REFRESH_MS = 30000;/);
  assert.match(script, /document\.visibilityState === "hidden" \|\| state\.view !== "orders"/);
  assert.match(script, /LIVE_CONTEXT_FETCH_TIMEOUT_MS/);
  assert.doesNotMatch(script, /\}, 2000\);[\s\S]{0,120}refreshView/);
});

test('keeps the public top navigation at one size across views', () => {
  assert.match(redesign, /#topnav\s*\{[\s\S]*?height:\s*60px;\s*min-height:\s*60px;/);
  assert.match(redesign, /\.tn-item\s*\{[\s\S]*?min-width:\s*108px;\s*height:\s*36px;/);
  assert.doesNotMatch(redesign, /body\[data-view="orders"\]\s+#topnav\s*\{/);
  assert.doesNotMatch(redesign, /body\[data-view="orders"\]\s+\.tn-(?:logo|name|nav|item)\s*\{/);
});

test('notifies the embedded strategy queue so hidden views stop polling', () => {
  const queueScript = fs.readFileSync(path.join(root, 'app/webui/chashui/queue.js'), 'utf8');

  assert.match(script, /function syncQueueFrameActivity\(viewName = state\.view\)/);
  assert.match(script, /type: "hdproc:queue-visibility", active: viewName === "queue"/);
  assert.match(queueScript, /function queuePollingAllowed\(\)/);
  assert.match(queueScript, /window\.parent\.document\.body\.dataset\.view === 'queue'/);
  assert.match(queueScript, /if \(!queuePollingAllowed\(\)\) return;/);
  assert.match(queueScript, /const STATUS_REFRESH_MS = 10000;/);
  assert.match(queueScript, /const EXECUTION_RESULTS_LIMIT = 300;/);
  assert.doesNotMatch(queueScript, /follow-results\?limit=1000/);
});
