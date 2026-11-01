import test from "node:test";
import assert from "node:assert/strict";
import { fetchJson } from "../../dashboard/static/modules/shared/http.js";

test("JSON helper returns API errors for the caller to interpret", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ error: "关联已被占用" }), { status: 409 }));
  const { response, payload } = await fetchJson("/test");
  assert.equal(response.status, 409);
  assert.equal(payload.error, "关联已被占用");
});

test("JSON helper deadline covers delayed body consumption after response headers", async t => {
  t.mock.method(globalThis, "fetch", async (_url, { signal }) => ({
    json: () => new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("request aborted", "AbortError")), { once: true })),
  }));
  await assert.rejects(fetchJson("/test", {}, { timeoutMs: 20, allowInvalidJson: true }), { name: "AbortError" });
});

test("JSON fallback is allowed for invalid bodies but cancellation is not swallowed", async t => {
  t.mock.method(globalThis, "fetch", async () => new Response("not JSON", { status: 500 }));
  assert.deepEqual((await fetchJson("/test", {}, { allowInvalidJson: true })).payload, {});
  await assert.rejects(fetchJson("/test"), SyntaxError);
});
