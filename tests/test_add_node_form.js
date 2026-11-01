const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createDefaultAddNodeForm,
  buildBridgePayload,
  buildAddNodeSubmitRequest,
} = require('../app/webui/add_node_form.js');

test('builds account request in account mode', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'account';
  form.account.base_url = 'https://ag.example.com';
  form.account.username = 'demo';
  form.account.password = 'secret';
  form.account.login_tab = 3;

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/accounts/add');
  assert.deepEqual(req.payload, {
    base_url: 'https://ag.example.com',
    username: 'demo',
    password: 'secret',
    tab: 3,
    security_code: '',
    relogin_min_interval_seconds: 5,
  });
});

test('builds remote signal request in signal mode', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'signal';
  form.signal.url = 'http://168.93.214.95:8021/signals/json';
  form.signal.token = 'abc123';
  form.signal.enabled = true;
  form.signal.live_only = true;
  form.signal.limit = 120;
  form.signal.interval_seconds = 2;

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/signal-pull-config');
  assert.deepEqual(req.payload, {
    signal_pull_enabled: true,
    signal_pull_url: 'http://168.93.214.95:8021/signals/json',
    signal_pull_token: 'abc123',
    signal_pull_live_only: true,
    signal_pull_limit: 120,
    signal_pull_interval_seconds: 2,
  });
});

test('builds read-only PS3838 request with the default base URL', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'ps3838';
  form.ps3838.username = 'pb-demo';
  form.ps3838.password = 'secret';
  form.ps3838.interval_seconds = 20;
  form.ps3838.telegram_chat_id = '-100123';

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/ps3838/add');
  assert.deepEqual(req.payload, {
    base_url: 'https://www.ps38ag.com',
    username: 'pb-demo',
    password: 'secret',
    interval_seconds: 20,
    telegram_chat_id: '-100123',
  });
});

test('builds login2 account request with monitor rules', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'account';
  form.account.base_url = 'https://ag.example.com';
  form.account.username = 'demo2';
  form.account.password = 'secret2';
  form.account.login_tab = 2;
  form.account.security_code = '1234';
  form.account.relogin_min_interval_seconds = 12;
  form.account.monitor_members = ['abc001', ' abc002 '];
  form.account.monitor_downlines = ['9025869', ' zk1115 '];

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/accounts/add');
  assert.deepEqual(req.payload, {
    base_url: 'https://ag.example.com',
    username: 'demo2',
    password: 'secret2',
    tab: 2,
    security_code: '1234',
    relogin_min_interval_seconds: 12,
    login2_watch_members: ['abc001', 'abc002'],
    login2_watch_downlines: ['9025869', 'zk1115'],
  });
});

test('builds login3 account request with order-source downlines', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'account';
  form.account.base_url = 'https://ag.example.com';
  form.account.username = 'demo3';
  form.account.password = 'secret3';
  form.account.login_tab = 3;
  form.account.relogin_min_interval_seconds = 18;
  form.account.monitor_members = ['should-not-apply'];
  form.account.monitor_downlines = ['9025869', ' zk1115 '];

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/accounts/add');
  assert.deepEqual(req.payload, {
    base_url: 'https://ag.example.com',
    username: 'demo3',
    password: 'secret3',
    tab: 3,
    security_code: '',
    relogin_min_interval_seconds: 18,
    login2_watch_downlines: ['9025869', 'zk1115'],
  });
});

test('builds local bridge request in bridge mode', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'bridge';
  form.bridge.enabled = true;
  form.bridge.target = 'ps3838';
  form.bridge.token = 'abc123';
  form.bridge.allow_remote = false;

  const req = buildAddNodeSubmitRequest(form);

  assert.equal(req.endpoint, '/api/local-bridge-config');
  assert.deepEqual(req.payload, {
    local_bridge_enabled: true,
    local_bridge_configured: true,
    local_bridge_target: 'ps3838',
    local_bridge_token: 'abc123',
    local_bridge_allow_remote: false,
  });
});

test('rejects unsafe local bridge target names', () => {
  const form = createDefaultAddNodeForm();
  form.mode = 'bridge';
  form.bridge.target = '../orders';

  assert.throws(() => buildBridgePayload(form.bridge), /订单目标/);
});
