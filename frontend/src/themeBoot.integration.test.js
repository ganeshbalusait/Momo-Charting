// The start-up page sets the theme before the app loads, so there is no flash of
// the wrong colours. This runs the real script out of index.html and checks it
// agrees with theme.js on every input.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { THEME_COLORS, THEME_KEY, normalizeTheme } from "./theme.js";

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
  const localStorage = {
    getItem(key) {
      if (storageThrows) throw new Error("blocked");
      return key === THEME_KEY && stored !== undefined ? stored : null;
    },
  };
  new Function("localStorage", "document", code)(localStorage, document);
  return { theme: attrs["data-theme"], color: meta.content };
}

test("a stored light choice opens light with the light status bar", () => {
  assert.deepEqual(boot({ stored: "light" }), { theme: "light", color: THEME_COLORS.light });
});

test("nothing stored opens dark", () => {
  assert.deepEqual(boot(), { theme: "dark", color: THEME_COLORS.dark });
});

test("a stored value that is not a theme opens the same way theme.js reads it", () => {
  for (const stored of ["LIGHT", "", "blue", "dark"]) {
    assert.equal(boot({ stored }).theme, normalizeTheme(stored), `stored ${JSON.stringify(stored)}`);
  }
});

test("blocked storage opens dark without an error", () => {
  assert.deepEqual(boot({ storageThrows: true }), { theme: "dark", color: THEME_COLORS.dark });
});

test("a page without the status-bar tag still gets its theme", () => {
  assert.equal(boot({ stored: "light", withMeta: false }).theme, "light");
});

test("the script reads the same storage key as theme.js", () => {
  assert.ok(code && code.includes(`"${THEME_KEY}"`));
});

test("the script runs before the app bundle", () => {
  const bootAt = html.indexOf("<script data-agx-theme-boot>");
  const appAt = html.indexOf('<script type="module"');
  assert.ok(bootAt > 0 && appAt > 0 && bootAt < appAt);
});
