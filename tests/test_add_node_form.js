const test = require('node:test');
const assert = require('node:assert/strict');

const {
  createDefaultAddNodeForm,
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
