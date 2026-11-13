// The start-up page marks the theme before the app loads, so there is no flash
// of the wrong colours. Since 2026-11-13 the app is dark only: the script always
// picks dark and forgets a Light choice made with the old switch. This runs the
// real script out of index.html.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { THEME_COLORS, THEME_KEY } from "./theme.js";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const match = html.match(/<script data-agx-theme-boot>([\s\S]*?)<\/script>/);
const code = match ? match[1] : null;

function boot({ stored, storageThrows = false, withMeta = true } = {}) {
  assert.ok(code, "index.html has a <script data-agx-theme-boot> element");
  const attrs = {};
  const meta = { content: "#0c0c0d", setAttribute(k, v) { this[k] = v; } };
  const document = {
    documentElement: { setAttribute: (k, v) => { attrs[k] = v; } },
    querySelector: (s) => (withMeta && s === 'meta[name="theme-color"]' ? meta : null),
  };
  const store = stored === undefined ? {} : { [THEME_KEY]: stored };
  const localStorage = {
    getItem(key) { if (storageThrows) throw new Error("blocked"); return key in store ? store[key] : null; },
    removeItem(key) { if (storageThrows) throw new Error("blocked"); delete store[key]; },
  };
  new Function("localStorage", "document", code)(localStorage, document);
  return { theme: attrs["data-theme"], color: meta.content, left: store[THEME_KEY] };
}

test("every start opens dark with the dark status bar, whatever was stored", () => {
  for (const stored of [undefined, "light", "dark", "LIGHT", "", "blue"]) {
    const seen = boot({ stored });
    assert.equal(seen.theme, "dark", `stored ${JSON.stringify(stored)}`);
    assert.equal(seen.color, THEME_COLORS.dark);
  }
});

test("a Light choice made with the old switch is forgotten", () => {
  assert.equal(boot({ stored: "light" }).left, undefined);
});

test("blocked storage opens dark without an error", () => {
  assert.deepEqual(boot({ storageThrows: true }), { theme: "dark", color: THEME_COLORS.dark, left: undefined });
});

test("a page without the status-bar tag still opens dark", () => {
  assert.equal(boot({ withMeta: false }).theme, "dark");
});

test("the script clears the same storage key theme.js uses", () => {
  assert.ok(code && code.includes(`"${THEME_KEY}"`));
});

test("the script runs before the app bundle", () => {
  const bootAt = html.indexOf("<script data-agx-theme-boot>");
  const appAt = html.indexOf('<script type="module"');
  assert.ok(bootAt > 0 && appAt > 0 && bootAt < appAt);
});
