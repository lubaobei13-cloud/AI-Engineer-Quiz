const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function harness(fetch) {
  const elements = new Map(), timers = new Map();
  let timerId = 0;
  function element(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      innerHTML: "", textContent: "", value: "", disabled: false,
      selectedOptions: [{ textContent: "Quiz main" }], listeners: {},
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, fn) { this.listeners[type] = fn; },
      setAttribute() {}, appendChild() {}, click() {}, remove() {},
    });
    return elements.get(selector);
  }
  const context = vm.createContext({
    document: { querySelector: element, querySelectorAll: () => [],
      createElement: () => element("anchor"), body: element("body") },
    localStorage: { getItem: () => null, removeItem() {}, setItem() {} },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    URLSearchParams, URL, AbortController, fetch,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), context);
  return { element, timers, run: code => vm.runInContext(code, context) };
}
const json = data => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
const result = total => ({ total, items: [], facets: {} });
const tick = () => new Promise(resolve => setImmediate(resolve));

test("late response cannot replace newer results", async () => {
  const pending = [];
  const h = harness(() => new Promise(resolve => pending.push(resolve)));
  const old = h.run('state.q = "old"; loadLibrary()');
  const latest = h.run('state.q = "new"; refreshLibrary()');
  pending[1](json(result(2))); await latest;
  pending[0](json(result(99))); await old;
  assert.equal(h.element("#result-title").textContent, "Results (2)");
  assert.equal(h.element("#btn-export").disabled, false);
});

test("typing invalidates in-flight response before debounce expires", async () => {
  const pending = [];
  const h = harness(() => new Promise(resolve => pending.push(resolve)));
  const old = h.run('loadLibrary()');
  h.element("#search").listeners.input({ target: { value: "latest" } });
  pending[0](json(result(99))); await old;
  assert.equal(h.element("#result-title").textContent, "");
  assert.equal(h.element("#btn-export").disabled, true);
  const next = [...h.timers.values()][0]();
  pending[1](json(result(3))); await next;
  assert.equal(h.element("#result-title").textContent, "Results (3)");
});

test("filter changes reset pagination; export shares filters and auth without paging", async () => {
  const requests = [];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    return url.startsWith("/api/assets/export") ? new Response("uuid\r\n") : json(result(48));
  });
  await h.run('state.token = "test-token"; state.offset = 24; state.q = "harbor"; state.media = "image"; state.collection = "1"; state.includeTags = ["approved", "campaign"]; state.excludeTags = ["internal"]; refreshLibrary()');
  const listing = new URL(requests[0].url, "http://test");
  assert.equal(listing.searchParams.get("offset"), "0");
  assert.equal(h.element("#page-prev").disabled, true);
  assert.equal(h.element("#page-next").disabled, false);
  await h.element("#btn-export").listeners.click();
  const download = new URL(requests[1].url, "http://test");
  listing.searchParams.delete("offset"); listing.searchParams.delete("limit");
  assert.equal(download.search, listing.search);
  assert.equal(requests[1].options.headers.Authorization, "Bearer test-token");
  assert.equal(h.element("anchor").download, "assets.csv");
});

test("loading and download failures recover with visible feedback", async () => {
  let fail = true;
  const h = harness(async url => {
    if (fail || url.startsWith("/api/assets/export")) throw new Error("offline");
    return json(result(0));
  });
  await h.run('loadLibrary()');
  assert.match(h.element("#search-status").textContent, /Could not load/);
  assert.equal(h.element("#btn-export").disabled, true);
  fail = false;
  h.element("#search-retry").listeners.click(); await tick();
  assert.equal(h.element("#btn-export").disabled, false);
  assert.equal(h.element("#page-next").disabled, true);
  await h.element("#btn-export").listeners.click();
  assert.match(h.element("#toast").textContent, /download failed/);
  assert.equal(h.element("#btn-export").textContent, "Export all matches");
});

test("JSON actions default to POST and preserve explicit PATCH", async () => {
  const requests = [];
  const h = harness(async (url, options) => { requests.push(options); return json({ ok: true }); });
  await h.run('api("/api/auth/login", { json: { username: "admin", password: "admin" } })');
  await h.run('api("/api/assets/one", { method: "PATCH", json: { title: "Changed" } })');
  assert.equal(requests[0].method, "POST");
  assert.equal(requests[1].method, "PATCH");
});
