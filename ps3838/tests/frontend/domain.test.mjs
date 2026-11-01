import test from "node:test";
import assert from "node:assert/strict";
import { resolvePage } from "../../dashboard/static/modules/shared/routes.js";
import { quoteAgeInfo, historyRowLabel, leagueDisplayName, formatTotalLine, formatHandicapLine } from "../../dashboard/static/modules/shared/format.js";
import { mergeEventTime, mergeMappedByPinnacle, mergeCrownById } from "../../dashboard/static/modules/merge/helpers.js";
import { describeSource } from "../../dashboard/static/modules/shared/source-status.js";
import { crownPinnaclePrice, crownPinnacleQuote, crownPinnacleChange } from "../../crown/static/modules/pinnacle-format.js";

test("only the retained pages resolve, and the default is my-events", () => {
  for (const page of ["my-events", "settings"]) assert.equal(resolvePage(page), page);
  for (const retired of ["home", "crown", "pinnacle", "merge", "unknown"]) assert.equal(resolvePage(retired), "my-events");
});

test("homepage Pinnacle prices never fall back to decimal odds", () => {
  assert.equal(crownPinnaclePrice({ decimal: 1.95 }), "—");
  assert.equal(crownPinnaclePrice({ hongKong: 0.95, decimal: 8 }), "0.950");
  assert.equal(crownPinnaclePrice({ hongKong: 0.95, quoted: false }), "—");
  assert.equal(crownPinnaclePrice({ hongKongLabel: "0.88" }), "0.880");
});

test("handicap direction and split totals use the existing home/away semantics", () => {
  const event = { home: "主队甲", away: "客队乙" };
  assert.deepEqual(crownPinnacleQuote({ market: "2", option: "0", line: -0.5 }, event), { label: "主队甲 -0.5", line: "让球" });
  assert.deepEqual(crownPinnacleQuote({ market: "2", option: "1", line: -0.5 }, event), { label: "客队乙 +0.5", line: "受让" });
  assert.equal(crownPinnacleQuote({ market: "3", option: "3", line: 2.25 }, event).label, "大2/2.5");
  assert.equal(formatTotalLine(2.25), "2/2.5");
});

test("quarter-goal lines use Crown's split notation on both providers", () => {
  const event = { home: "主队甲", away: "客队乙" };
  assert.equal(crownPinnacleQuote({ market: "2", option: "0", line: -0.25 }, event).label, "主队甲 -0/0.5");
  assert.equal(crownPinnacleQuote({ market: "2", option: "0", line: -0.75 }, event).label, "主队甲 -0.5/1");
  assert.equal(crownPinnacleQuote({ market: "2", option: "0", line: 1.25 }, event).label, "主队甲 +1/1.5");
  assert.equal(crownPinnacleQuote({ market: "2", option: "0", line: 0.25 }, event).label, "主队甲 +0/0.5");
  assert.equal(formatHandicapLine(-0.25), "-0/0.5");
  assert.equal(formatHandicapLine(0.75), "+0.5/1");
  assert.equal(formatHandicapLine(-1.25), "-1/1.5");
  assert.equal(formatHandicapLine(0.5), "+0.5");
  assert.equal(formatHandicapLine(0), "0");
  assert.equal(historyRowLabel({ market: "2", stage: "0", option: "0", line: -0.75 }, "甲 VS 乙"), "让球 · 全场 · 甲 -0.5/1");
});

test("one-x-two selections follow Pinnacle's home/away/draw option codes", () => {
  const event = { home: "主队甲", away: "客队乙" };
  assert.equal(crownPinnacleQuote({ market: "1", option: "0" }, event).label, "主队甲 主胜");
  assert.equal(crownPinnacleQuote({ market: "1", option: "1" }, event).label, "客队乙 客胜");
  assert.equal(crownPinnacleQuote({ market: "1", option: "2" }, event).label, "和局");
  assert.equal(historyRowLabel({ market: "1", stage: "0", option: "1" }, "甲 VS 乙"), "胜平负 · 全场 · 客胜");
  assert.equal(historyRowLabel({ market: "1", stage: "0", option: "2" }, "甲 VS 乙"), "胜平负 · 全场 · 和局");
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

test("paired movement badges mirror the Pinnacle data panel", () => {
  assert.deepEqual(crownPinnacleChange({ quoted: true, direction: "up", delta: 0.012 }), { direction: "up", text: "▲ +0.012", delta: 0.012 });
  assert.deepEqual(crownPinnacleChange({ quoted: true, direction: "down", delta: -0.009 }), { direction: "down", text: "▼ -0.009", delta: -0.009 });
  assert.deepEqual(crownPinnacleChange({ quoted: true, direction: "new" }), { direction: "new", text: "新盘", delta: null });
  assert.equal(crownPinnacleChange({ quoted: true, direction: "flat", delta: 0 }), null);
  assert.equal(crownPinnacleChange({ quoted: false, direction: "up", delta: 0.1 }), null);
});

import { crownHandicapLine, crownNormalizeLine, crownQuarterLine } from "../../crown/static/modules/line-format.js";

test("Crown handicap lines keep the receiving sign when paired with Pinnacle", () => {
  // Crown writes a receiving home line as "+1.5"; the paired lookup must keep
  // the plus, otherwise it searches for -1.5 and never finds the Pinnacle row.
  assert.equal(crownHandicapLine("+1.5", "home"), "+1.5");
  assert.equal(crownHandicapLine("+1.5", "away"), "-1.5");
  assert.equal(crownHandicapLine("-1.5", "home"), "-1.5");
  assert.equal(crownHandicapLine("-1.5", "away"), "+1.5");
  assert.equal(crownHandicapLine("+0/0.5", "home"), "+0/0.5");
  assert.equal(crownHandicapLine("+0/0.5", "away"), "-0/0.5");
  assert.equal(crownHandicapLine("0", "home"), "0");
  assert.equal(crownNormalizeLine(" 3 / 3.5 "), "3/3.5");
  assert.equal(crownQuarterLine("+0.75"), "+0.5/1");
  assert.equal(crownQuarterLine("-0.25"), "-0/0.5");
  assert.equal(crownQuarterLine("0.25"), "0/0.5");
});
