const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const page = fs.readFileSync(path.join(__dirname, '../app/webui/index.html'), 'utf8');

test('arsenal view uses a single-row six-card operations strip', () => {
  assert.match(page, /pool-kpi-strip/);
  assert.equal((page.match(/class="pool-kpi-card/g) || []).length, 6);
  assert.match(page, /monitor-pool-normal-count/);
  assert.match(page, /monitor-pool-error-count/);
  assert.match(page, /pool-today-order-count/);
  assert.match(page, /monitor-pool-concurrency/);
  assert.match(page, /arsenal-total-balance/);
  assert.match(page, /总可用额度/);
  assert.doesNotMatch(page, /系统自动派单说明/);
  assert.doesNotMatch(page, /随机出号会员号池/);
  assert.doesNotMatch(page, /不设主力号/);
});
