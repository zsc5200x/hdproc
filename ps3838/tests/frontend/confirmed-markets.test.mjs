import test from "node:test";
import assert from "node:assert/strict";
import { createConfirmedMarkets } from "../../crown/static/modules/confirmed-markets.js";

const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, value => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[value]));

test("standalone Crown mode does not fetch or display confirmed Pinnacle markets", async t => {
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => { requests++; throw new Error("unexpected request"); });
  const view = createConfirmedMarkets({ enabled: false, escapeHtml });
  await view.load({ id: "9" }, true);
  assert.equal(requests, 0);
  assert.equal(view.get({ id: "9" }), null);
  assert.equal(view.renderMarkets({ pinnacle: { id: "101", odds: [] } }, {}), "");
  assert.equal(view.quote({ pinnacle: { odds: [{ market: "1", option: "0", hongKong: 0.8 }] } }, "1", "0", ""), null);
  assert.equal(view.renderSummary({ pinnacle: { id: "101" } }, {}), "");
});

test("homepage loads confirmed prices and matches by market, option and normalized line", async t => {
  let requests = 0;
  let changes = 0;
  const source = { dataUpdatedAt: "2026-10-30T00:00:00Z", status: "stale", cached: true };
  const item = {
    crownId: "9",
    pinnacleId: "101",
    crown: { home: "旧主队", markets: { old: true } },
    pinnacle: { id: "101", odds: [
      { market: "1", option: "0", line: "", hongKong: 0.98, capturedAt: "2026-10-30T00:01:00+08:00" },
      { market: "1", option: "2", line: "", decimal: 2.10 },
      { market: "2", option: "0", line: "-0.25", hongKong: 0.81 },
      { market: "2", option: "1", line: "+0.25", hongKong: 0.85 },
      { market: "3", option: "3", line: "2.25", hongKong: 0.91 },
      { market: "3", option: "4", line: "2.5", hongKong: 0.92 },
    ] },
  };
  t.mock.method(globalThis, "fetch", async url => {
    requests++;
    assert.equal(url, "/api/merge/confirmed?crownId=9");
    return new Response(JSON.stringify({ items: [item], sources: { pinnacle: source } }));
  });
  const view = createConfirmedMarkets({ enabled: true, escapeHtml, onChange: () => changes++ });
  const live = { id: "9", home: "当前主队", markets: { current: true } };
  await view.load(live);
  await view.load(live);
  assert.equal(requests, 1);
  assert.equal(changes, 1);
  const confirmed = view.get(live);
  assert.deepEqual(confirmed.sources.pinnacle, source);
  assert.deepEqual(view.mergeEvent(live, confirmed).markets, { current: true });
  assert.equal(view.renderMarkets(confirmed, live), "");
  assert.deepEqual(view.quote(confirmed, "1", "0", ""), {
    price: "0.980", line: "", lineDiff: false, quoted: true,
    reason: "", capturedAt: "2026-10-30T00:01:00+08:00", direction: "flat", delta: null,
  });
  assert.equal(view.quote(confirmed, "1", "2", "").reason, "no_quote");
  assert.deepEqual(view.quote(confirmed, "2", "0", "-0/0.5"), {
    price: "0.810", line: "-0/0.5", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
  assert.deepEqual(view.quote(confirmed, "2", "1", "+0/0.5"), {
    price: "0.850", line: "+0/0.5", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
  assert.equal(view.quote(confirmed, "2", "0", "-0.5").reason, "no_matching_line");
  assert.deepEqual(view.quote(confirmed, "3", "3", "2/2.5"), {
    price: "0.910", line: "2/2.5", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
  assert.equal(view.quote(confirmed, "3", "4", "2.25").reason, "no_matching_line");
  assert.equal(view.mergeEvent(live, confirmed).home, "当前主队");
});

test("matches selection-specific away handicap and quarter-goal totals", () => {
  const view = createConfirmedMarkets({ enabled: true, escapeHtml });
  const confirmed = { pinnacle: { odds: [
    { market: "2", option: "1", line: "0.5", hongKong: 0.653, quoted: true },
    { market: "3", option: "3", line: "2.75", hongKong: 0.813, quoted: true },
    { market: "3", option: "4", line: "2.75", hongKong: 1.07, quoted: true },
  ] } };

  assert.deepEqual(view.quote(confirmed, "2", "1", "+0.5"), {
    price: "0.653", line: "+0.5", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
  assert.deepEqual(view.quote(confirmed, "3", "3", "2.5/3"), {
    price: "0.813", line: "2.5/3", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
  assert.deepEqual(view.quote(confirmed, "3", "4", "2.5/3"), {
    price: "1.070", line: "2.5/3", lineDiff: false, quoted: true, reason: "", capturedAt: "", direction: "flat", delta: null,
  });
});

test("a suspended selection keeps its last usable water for the paired card", () => {
  const view = createConfirmedMarkets({ enabled: true, escapeHtml });
  const confirmed = { pinnacle: { odds: [
    {
      market: "3", option: "4", line: "3", quoted: false, hongKong: null,
      lastHongKong: 0.854, lastQuotedAt: "2026-10-30T19:57:21.000+08:00",
      capturedAt: "2026-10-30T20:31:00.000+08:00",
    },
    { market: "3", option: "3", line: "3", quoted: false, hongKong: null },
  ] } };

  const suspended = view.quote(confirmed, "3", "4", "3");
  assert.equal(suspended.quoted, false);
  assert.equal(suspended.price, "—");
  assert.equal(suspended.suspended, true);
  assert.equal(suspended.lastPrice, "0.854");
  assert.equal(suspended.lastCapturedAt, "2026-10-30T19:57:21.000+08:00");

  const neverQuoted = view.quote(confirmed, "3", "3", "3");
  assert.equal(neverQuoted.quoted, false);
  assert.equal(neverQuoted.suspended, undefined);
  assert.equal(neverQuoted.lastPrice, undefined);
});

test("removed mappings clear cached confirmed data", async t => {
  const view = createConfirmedMarkets({ enabled: true, escapeHtml });
  let payload = { items: [{ crownId: "9", pinnacleId: "101" }] };
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify(payload)));
  await view.load({ id: "9" }, true);
  assert.equal(view.get({ id: "9" }).pinnacleId, "101");
  payload = { items: [] };
  await view.load({ id: "9" }, true);
  assert.equal(view.get({ id: "9" }), null);
});

test("a missing same line reports the closest Pinnacle line in Crown notation", () => {
  const view = createConfirmedMarkets({ enabled: true, escapeHtml });
  const confirmed = { pinnacle: { odds: [
    { market: "2", option: "0", line: "-0.75", hongKong: 1.05, capturedAt: "2026-10-30T08:00:00+08:00" },
    { market: "2", option: "0", line: "0", hongKong: 0.33 },
    { market: "3", option: "3", line: "2.75", hongKong: 0.81 },
  ] } };

  const handicap = view.quote(confirmed, "2", "0", "-0.5");
  assert.equal(handicap.reason, "no_matching_line");
  assert.equal(handicap.price, "—");
  assert.deepEqual(handicap.alternate, {
    line: "-0.5/1", price: "1.050", quoted: true, capturedAt: "2026-10-30T08:00:00+08:00",
    lastPrice: "", lastCapturedAt: "",
  });

  const suspendedNearest = view.quote({ pinnacle: { odds: [
    {
      market: "2", option: "1", line: "0.5", quoted: false, hongKong: null,
      lastHongKong: 0.91, lastQuotedAt: "2026-10-30T19:00:00+08:00",
    },
  ] } }, "2", "1", "1.25");
  assert.equal(suspendedNearest.alternate.line, "+0.5");
  assert.equal(suspendedNearest.alternate.price, "—");
  assert.equal(suspendedNearest.alternate.lastPrice, "0.910");
  assert.equal(suspendedNearest.alternate.lastCapturedAt, "2026-10-30T19:00:00+08:00");

  const total = view.quote(confirmed, "3", "3", "2.5");
  assert.equal(total.alternate.line, "2.5/3");
  assert.equal(total.alternate.price, "0.810");

  assert.equal(view.quote(confirmed, "3", "4", "2.5").alternate, null);
});

test("quote exposes the WSS movement used by the homepage delta pill", () => {
  const view = createConfirmedMarkets({ enabled: true, escapeHtml });
  const confirmed = { pinnacle: { odds: [
    { market: "2", option: "0", line: "-0.5", hongKong: 0.826, direction: "up", delta: 0.012, quoted: true },
    { market: "2", option: "1", line: "+0.5", hongKong: 0.943, direction: "down", delta: -0.009, quoted: true },
    { market: "3", option: "3", line: "2.5", hongKong: 0.9, direction: "new", quoted: true },
  ] } };

  const up = view.quote(confirmed, "2", "0", "-0.5");
  assert.equal(up.direction, "up");
  assert.equal(up.delta, 0.012);
  const down = view.quote(confirmed, "2", "1", "+0.5");
  assert.equal(down.direction, "down");
  assert.equal(down.delta, -0.009);
  assert.equal(view.quote(confirmed, "3", "3", "2.5").direction, "new");
  // Unquoted rows never carry a movement pill.
  assert.equal(view.quote(confirmed, "3", "4", "2.5").direction, "");
});
