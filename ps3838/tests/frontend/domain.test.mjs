import test from "node:test";
import assert from "node:assert/strict";
import { resolvePage } from "../../dashboard/static/modules/shared/routes.js";
import { quoteAgeInfo, historyRowLabel, leagueDisplayName, formatTotalLine, formatHandicapLine } from "../../dashboard/static/modules/shared/format.js";
import { mergeEventTime, mergeMappedByPinnacle, mergeCrownById } from "../../dashboard/static/modules/merge/helpers.js";
import { describeSource } from "../../dashboard/static/modules/shared/source-status.js";

test("only the retained pages resolve, and the default is my-events", () => {
  assert.equal(resolvePage("my-events"), "my-events");
  for (const retired of ["settings", "home", "crown", "pinnacle", "merge", "unknown"]) {
    assert.equal(resolvePage(retired), "my-events");
  }
});

test("quote age depends on explicit source reference and handles missing capture time", () => {
  assert.deepEqual(quoteAgeInfo("2026-10-30T00:00:00Z", "2026-10-30T00:42:00Z"), { text: "42 分前", level: "aged", seconds: 2520 });
  assert.deepEqual(quoteAgeInfo(null, "2026-10-30T00:42:00Z"), { text: "时间未知", level: "unknown", seconds: null });
  assert.equal(historyRowLabel({ market: "1", stage: "0", option: "0" }, "甲 VS 乙"), "胜平负 · 全场 · 主胜");
  assert.equal(leagueDisplayName("国家 - U-20"), "国家U-20");
});

test("matching displays the normalized kickoff and keeps provider ID namespaces distinct", () => {
  assert.equal(mergeEventTime({ normalizedKickoff: "10-30 19:00", startLabel: "10-30 03:00" }, "crown"), "10-30 19:00");
  const data = { mappings: [{ pinnacleId: "101", crownId: "9" }], crownEvents: [{ id: "9" }] };
  assert.equal(mergeMappedByPinnacle(data).get("101").crownId, "9");
  assert.equal(mergeCrownById(data).has("101"), false);
});

test("source status never substitutes response generation time for source data time", () => {
  const missing = describeSource("皇冠", { status: "无数据", generatedAt: "2026-10-30T00:42:00Z", updatedAt: "2026-10-30T00:42:00Z" });
  assert.match(missing.text, /时间未知/);
  assert.equal(missing.level, "unknown");
  const stale = describeSource("平博", { status: "stale", cached: true, dataUpdatedAt: "2026-10-30T00:00:00Z" });
  assert.match(stale.text, /已过期.*缓存.*08:00:00 北京时间/);
  assert.equal(stale.level, "warning");
  assert.equal(describeSource("皇冠", { error: "会话同步失败" }).level, "error");
});
