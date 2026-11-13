// The Dark / Light theme helper (clean look, release 1). The theme is one of two
// exact strings; anything else, and any storage failure, means dark.

import assert from "node:assert/strict";
import test from "node:test";

import { SKIN_PREVIEW_PREFIX, THEME_COLORS, THEME_KEY, activeSkins, applySkins, applyTheme, currentTheme, normalizeTheme, readStoredTheme, setTheme, subscribeTheme } from "./theme.js";

const memory = (seed = {}) => {
  const m = { ...seed };
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, m };
};
const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
const fakeDoc = ({ withMeta = true } = {}) => {
  const attrs = {};
  const meta = { content: "#0c0c0d", setAttribute(k, v) { this[k] = v; } };
  return {
    documentElement: { setAttribute: (k, v) => { attrs[k] = v; } },
    querySelector: (s) => (withMeta && s === 'meta[name="theme-color"]' ? meta : null),
    attrs, meta,
  };
};

test("the storage key and the two page colours are the agreed ones", () => {
  assert.equal(THEME_KEY, "agx.theme");
  assert.deepEqual(THEME_COLORS, { dark: "#000000", light: "#f5f5f7" });
});

test("only the exact word light means light", () => {
  assert.equal(normalizeTheme("light"), "light");
  for (const value of ["dark", "LIGHT", "", "blue", null, undefined, 0, {}]) {
    assert.equal(normalizeTheme(value), "dark", `value ${JSON.stringify(value)}`);
  }
});

test("reading the stored theme never throws and falls back to dark", () => {
  assert.equal(readStoredTheme(memory({ "agx.theme": "light" })), "light");
  assert.equal(readStoredTheme(memory()), "dark");
  assert.equal(readStoredTheme(memory({ "agx.theme": "blue" })), "dark");
  assert.equal(readStoredTheme(throwing), "dark");
  assert.equal(readStoredTheme(undefined), "dark");
});

test("applying a theme marks the page and the phone status-bar colour", () => {
  const doc = fakeDoc();
  assert.equal(applyTheme("light", doc), "light");
  assert.equal(doc.attrs["data-theme"], "light");
  assert.equal(doc.meta.content, "#f5f5f7");
  assert.equal(applyTheme("blue", doc), "dark");
  assert.equal(doc.attrs["data-theme"], "dark");
  assert.equal(doc.meta.content, "#000000");
});

test("applying a theme works on a page with no status-bar colour tag", () => {
  const doc = fakeDoc({ withMeta: false });
  assert.equal(applyTheme("light", doc), "light");
  assert.equal(doc.attrs["data-theme"], "light");
});

test("setting a theme applies it and remembers it", () => {
  const storage = memory();
  const doc = fakeDoc();
  assert.equal(setTheme("light", { storage, doc }), "light");
  assert.equal(storage.m["agx.theme"], "light");
  assert.equal(doc.attrs["data-theme"], "light");
});

test("setting a theme still applies it when storage is blocked", () => {
  const doc = fakeDoc();
  assert.equal(setTheme("light", { storage: throwing, doc }), "light");
  assert.equal(doc.attrs["data-theme"], "light");
});

test("a change made in another window is passed on, and only for the theme key", () => {
  const handlers = {};
  const win = {
    addEventListener: (name, fn) => { handlers[name] = fn; },
    removeEventListener: (name, fn) => { if (handlers[name] === fn) delete handlers[name]; },
  };
  const seen = [];
  const stop = subscribeTheme((theme) => seen.push(theme), win);
  handlers.storage({ key: "agx.theme", newValue: "light" });
  handlers.storage({ key: "other", newValue: "light" });
  handlers.storage({ key: "agx.theme", newValue: null });
  assert.deepEqual(seen, ["light", "dark"]);
  stop();
  assert.equal(handlers.storage, undefined);
});

test("a change made in this window reaches every switch on the page, until it unsubscribes", () => {
  const seen = [];
  const stop = subscribeTheme((theme) => seen.push(theme), null);
  setTheme("light", { storage: memory(), doc: fakeDoc() });
  applyTheme("blue", fakeDoc());
  stop();
  setTheme("light", { storage: memory(), doc: fakeDoc() });
  assert.deepEqual(seen, ["light", "dark"]);
});

test("a switch that mounts later reads the theme off the page, so blocked storage cannot leave it stale", () => {
  const page = (value) => ({ documentElement: { getAttribute: (name) => (name === "data-theme" ? value : null) } });
  assert.equal(currentTheme({ doc: page("light"), storage: throwing }), "light");
  assert.equal(currentTheme({ doc: page("dark"), storage: memory({ "agx.theme": "light" }) }), "dark");
  assert.equal(currentTheme({ doc: page(null), storage: memory({ "agx.theme": "light" }) }), "light");
  assert.equal(currentTheme({ doc: page("blue"), storage: memory() }), "dark");
  assert.equal(currentTheme({ doc: undefined, storage: throwing }), "dark");
});

test("subscribing without a window is harmless", () => {
  const stop = subscribeTheme(() => {}, null);
  assert.doesNotThrow(stop);
});

// A screen's new look is switched on by a word in data-agx-skin on <html>.
// 2026-11-13: the Scanner's dark look is live for everyone, whatever the
// browser stored.
test("the scanner look is on for every browser, whatever it stored", () => {
  assert.equal(SKIN_PREVIEW_PREFIX, "agx.skin.");
  for (const storage of [memory(), memory({ "agx.skin.scanner": "0" }), memory({ "agx.skin.scanner": "1" }), throwing, undefined]) {
    assert.deepEqual(activeSkins(storage), ["scanner"]);
  }
  assert.deepEqual(activeSkins(memory({ "agx.skin.nonsense": "1" })), ["scanner"]);
});

test("applying skins writes the words on the page", () => {
  const on = fakeDoc();
  assert.deepEqual(applySkins({ doc: on, storage: memory() }), ["scanner"]);
  assert.equal(on.attrs["data-agx-skin"], "scanner");
  assert.deepEqual(applySkins({ doc: undefined, storage: memory() }), ["scanner"]);
});
