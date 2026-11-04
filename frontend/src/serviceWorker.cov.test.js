// Unit: the installable-app pieces (coverage 2026-11-04, group "shell").
// public/sw.js is loaded into a fake service-worker global, so its three
// handlers run for real without a browser; the manifest and index.html are
// read as the browser would read them.
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const SW_SOURCE = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");
const MANIFEST = JSON.parse(readFileSync(new URL("../public/manifest.webmanifest", import.meta.url), "utf8"));
const INDEX = readFileSync(new URL("../index.html", import.meta.url), "utf8");

function loadWorker({ cacheKeys = [], fetchImpl } = {}) {
  const listeners = {};
  const calls = { skipWaiting: 0, claim: 0, deleted: [], fetched: [] };
  const self = {
    addEventListener: (type, handler) => { listeners[type] = handler; },
    skipWaiting: () => { calls.skipWaiting += 1; return Promise.resolve(); },
    clients: { claim: async () => { calls.claim += 1; } },
  };
  const caches = {
    keys: async () => [...cacheKeys],
    delete: async (key) => { calls.deleted.push(key); return true; },
  };
  const ERROR = { type: "error" };
  const Response = { error: () => ERROR };
  const fetch = (request) => { calls.fetched.push(request.url); return fetchImpl ? fetchImpl(request) : Promise.resolve({ ok: true, from: "network" }); };
  new Function("self", "caches", "fetch", "Response", SW_SOURCE)(self, caches, fetch, Response);
  return { listeners, calls, ERROR };
}

function fetchEvent(url) {
  const event = { request: { url }, responded: null };
  event.respondWith = (promise) => { event.responded = promise; };
  return event;
}

test("install takes over at once instead of waiting for every tab to close", () => {
  const { listeners, calls } = loadWorker();
  listeners.install({});
  assert.equal(calls.skipWaiting, 1);
});

test("activate wipes every cache an older worker left behind, then claims the page", async () => {
  const { listeners, calls } = loadWorker({ cacheKeys: ["agx-v1-assets", "workbox-precache"] });
  let pending = null;
  listeners.activate({ waitUntil: (promise) => { pending = promise; } });
  await pending;
  assert.deepEqual(calls.deleted, ["agx-v1-assets", "workbox-precache"]);
  assert.equal(calls.claim, 1);
});

test("activate still claims the page when the cache API is refused", async () => {
  const listeners = {};
  let claimed = 0;
  const self = { addEventListener: (type, handler) => { listeners[type] = handler; }, skipWaiting() {}, clients: { claim: async () => { claimed += 1; } } };
  const deniedCaches = { keys: () => Promise.reject(new Error("denied")), delete: async () => true };
  new Function("self", "caches", "fetch", "Response", SW_SOURCE)(self, deniedCaches, () => {}, {});
  let pending = null;
  listeners.activate({ waitUntil: (promise) => { pending = promise; } });
  await pending;
  assert.equal(claimed, 1);
});

test("API calls are never touched by the worker", () => {
  const { listeners, calls } = loadWorker();
  for (const url of ["https://app.agxtrade.com/api/oi-finder-chart?symbol=SPY", "http://127.0.0.1:5173/api/live-market-stream"]) {
    const event = fetchEvent(url);
    listeners.fetch(event);
    assert.equal(event.responded, null, `${url} must go to the browser natively`);
  }
  assert.deepEqual(calls.fetched, []);
});

test("every other request goes straight to the network - nothing is ever served from a cache", async () => {
  const { listeners, calls } = loadWorker();
  const event = fetchEvent("https://app.agxtrade.com/assets/index-abc123.js");
  listeners.fetch(event);
  assert.ok(event.responded, "the handler must answer so the app stays installable");
  assert.deepEqual(await event.responded, { ok: true, from: "network" });
  assert.deepEqual(calls.fetched, ["https://app.agxtrade.com/assets/index-abc123.js"]);
});

test("offline, the browser's own error comes back - never a stale copy", async () => {
  const { listeners, ERROR } = loadWorker({ fetchImpl: () => Promise.reject(new TypeError("Failed to fetch")) });
  const event = fetchEvent("https://app.agxtrade.com/");
  listeners.fetch(event);
  assert.equal(await event.responded, ERROR);
});

test("the worker source holds no cache-write call at all", () => {
  assert.doesNotMatch(SW_SOURCE, /caches\.open|cache\.put|cache\.add/);
});

test("the manifest makes a standalone app rooted at /", () => {
  assert.equal(MANIFEST.start_url, "/");
  assert.equal(MANIFEST.scope, "/");
  assert.equal(MANIFEST.id, "/");
  assert.equal(MANIFEST.display, "standalone");
  assert.ok(MANIFEST.name && MANIFEST.short_name);
});

test("every manifest icon exists, with any + maskable at 192 and 512", () => {
  for (const icon of MANIFEST.icons) {
    assert.ok(existsSync(new URL(`../public${icon.src}`, import.meta.url)), `${icon.src} is missing from public/`);
    assert.equal(icon.type, "image/png");
  }
  const has = (size, purpose) => MANIFEST.icons.some((icon) => icon.sizes === size && icon.purpose === purpose);
  for (const size of ["192x192", "512x512"]) {
    assert.ok(has(size, "any"), `no ${size} any icon`);
    assert.ok(has(size, "maskable"), `no ${size} maskable icon`);
  }
});

test("index.html links the manifest WITH credentials and the Home Screen icon", () => {
  // Without crossorigin="use-credentials" Cloudflare Access redirects the
  // manifest fetch and Chrome stops offering Install (2026-09-01).
  assert.match(INDEX, /<link rel="manifest" href="\/manifest\.webmanifest" crossorigin="use-credentials"\s*\/>/);
  const touch = /<link rel="apple-touch-icon" href="([^"]+)"/.exec(INDEX);
  assert.ok(touch, "no apple-touch-icon link");
  assert.ok(existsSync(new URL(`../public${touch[1]}`, import.meta.url)), `${touch[1]} is missing`);
  assert.match(INDEX, /viewport-fit=cover/);
});
