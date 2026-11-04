// Unit (2026-11-04 coverage): the update bar and the service worker - the two
// pieces of the new-version watch that the existing appVersion tests do not
// reach. A second bar, a second hide-reload listener, or a caching worker
// would each put a stale or doubled screen in front of the trader.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { offerUpdate, showUpdateBar } from "./appVersion.js";

const SW = readFileSync(new URL("../public/sw.js", import.meta.url), "utf8");
// Comments explain the history at length and name the APIs they forbid, so
// only the executable code is checked.
const SW_CODE = SW.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

test("sw.js stays network-only: it never stores or serves from a cache", () => {
  for (const forbidden of [/caches\.open\s*\(/, /\.put\s*\(/, /\.addAll\s*\(/, /caches\.match\s*\(/, /\.add\s*\(/]) {
    assert.doesNotMatch(SW_CODE, forbidden, `sw.js uses ${forbidden}`);
  }
  assert.match(SW_CODE, /caches\.delete\s*\(/, "activate still wipes caches an older worker left behind");
  assert.match(SW_CODE, /respondWith\(fetch\(event\.request\)/, "every non-API request goes to the network");
  assert.match(SW_CODE, /startsWith\("\/api\/"\)\) return;/, "API calls are not touched at all");
});

function fakeWindow() {
  const listeners = [];
  const elements = new Map();
  const appended = [];
  const document = {
    visibilityState: "visible",
    getElementById: (id) => elements.get(id) || null,
    createElement: (tag) => {
      const element = {
        tag, attributes: {}, clicks: [],
        set id(value) { this._id = value; elements.set(value, this); },
        get id() { return this._id; },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, fn) { if (name === "click") this.clicks.push(fn); },
      };
      return element;
    },
    body: { appendChild: (element) => appended.push(element) },
    addEventListener: (name, fn) => listeners.push({ name, fn }),
  };
  const reloads = [];
  return { win: { document, location: { reload: () => reloads.push(1) } }, appended, listeners, reloads };
}

test("asking twice shows ONE update bar with the tap-to-update words", () => {
  const { win, appended } = fakeWindow();
  const taps = [];
  showUpdateBar(win, () => taps.push("first"));
  showUpdateBar(win, () => taps.push("second"));
  assert.equal(appended.length, 1);
  const bar = appended[0];
  assert.equal(bar.id, "agx-update-bar");
  assert.equal(bar.textContent, "New version available - tap to update");
  assert.match(bar.attributes.style, /position:fixed/);
  bar.clicks.forEach((fn) => fn());
  assert.deepEqual(taps, ["first"], "the tap runs the first offer's action once");
});

test("offering twice adds ONE hide-to-reload listener, and hiding reloads once", () => {
  const { win, appended, listeners, reloads } = fakeWindow();
  offerUpdate(win);
  offerUpdate(win);
  assert.equal(appended.length, 1);
  const hideListeners = listeners.filter((entry) => entry.name === "visibilitychange");
  assert.equal(hideListeners.length, 1, "a second listener would reload twice");
  win.document.visibilityState = "visible";
  hideListeners[0].fn();
  assert.equal(reloads.length, 0, "never reloads in front of him");
  win.document.visibilityState = "hidden";
  hideListeners[0].fn();
  assert.equal(reloads.length, 1);
});

test("the default tap of an offered update reloads", () => {
  const { win, appended, reloads } = fakeWindow();
  offerUpdate(win);
  appended[0].clicks.forEach((fn) => fn());
  assert.equal(reloads.length, 1);
});
