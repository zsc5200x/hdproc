const assert = require("node:assert/strict");
const cards = require("./report_cards.js");

const stoppedModel = cards.buildReportCardModel({
  order: { orderId: "23821968101" },
  followSummary: { ok: 0, fail: 0, pending: 1, engineStopped: true },
});

assert.equal(
  stoppedModel.reportStatusText,
  "跟单引擎未运行",
  "engine-stopped orders without result rows must not be shown as pending",
);

const pendingModel = cards.buildReportCardModel({
  order: { orderId: "23821968101" },
  followSummary: { ok: 0, fail: 0, pending: 1 },
});

assert.equal(
  pendingModel.reportStatusText,
  "成功 0 · 失败 0 · 待处理 1",
  "normal pending display remains unchanged while the engine is running",
);

const execution = cards.buildFollowExecutionModel([
  {
    ok: true,
    member: "summer8889",
    stake: 5350,
    ticket_id: "23827223392",
    attempt: 2,
    split_index: 1,
    split_count: 2,
    ts_ms: 2000,
  },
  {
    ok: false,
    member: "jjr123",
    stake: 5350,
    message: "盘口封盘，正在等待开盘重试（剩余300秒）",
    attempt: 1,
    split_index: 2,
    split_count: 2,
    ts_ms: 1000,
  },
]);

assert.equal(execution.summaryText, "成功 1 · 失败 1");
assert.equal(execution.rows.length, 2);
assert.equal(execution.rows[0].statusLabel, "失败");
assert.equal(execution.rows[0].reason, "盘口封盘，正在等待开盘重试（剩余300秒）");
assert.equal(execution.rows[1].ticketText, "23827223392");
assert.equal(execution.rows[1].statusLabel, "成功");

const retrySummary = cards.buildFollowExecutionModel([
  { ok: false, member: "m1", message: "502", attempt: 1, split_index: 1, split_count: 2, ts_ms: 1000 },
  { ok: false, member: "m2", message: "水位变化", attempt: 2, split_index: 1, split_count: 2, ts_ms: 2000 },
  { ok: true, member: "m3", ticket_id: "T2", attempt: 3, split_index: 2, split_count: 2, ts_ms: 3000 },
]);
assert.equal(retrySummary.summaryText, "成功 1 · 失败 1");

const stoppedExecution = cards.buildFollowExecutionModel([], { engineStopped: true });
assert.equal(stoppedExecution.summaryText, "跟单引擎未运行");
assert.equal(stoppedExecution.emptyText, "该订单尚未产生跟单记录，当前跟单引擎未运行。");

console.log("report_cards tests passed");
