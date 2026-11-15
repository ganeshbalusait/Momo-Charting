// Hallmark Midnight theme (2026-11-14): navy ground, Mona Sans, one light-blue
// signal. It repaints chrome only: the chart drawing, TOS colours, the Sectors
// heat map and the cyan/magenta signals keep their own colours.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const css = read("./theme.hallmark.css");
const main = read("./main.jsx");
const html = read("../index.html");

test("the Midnight theme loads after theme.css so its tokens win", () => {
  const base = main.indexOf('import "./theme.css";');
  const midnight = main.indexOf('import "./theme.hallmark.css";');
  assert.ok(base >= 0 && midnight > base);
});

test("the Midnight tokens are the navy ground, cool ink and light-blue signal", () => {
  assert.match(css, /theme: midnight/);
  assert.match(css, /--hm-paper: oklch\(14% 0\.048 264\);/);
  assert.match(css, /--hm-paper-3: oklch\(11\.5% 0\.045 264\);/);
  assert.match(css, /--hm-ink: oklch\(95% 0\.016 250\);/);
  assert.match(css, /--hm-accent: oklch\(80% 0\.11 230\);/);
  assert.match(css, /--agx-bg: var\(--hm-paper\);/);
});

test("Mona Sans is the app font and the page asks Google Fonts for it", () => {
  assert.match(css, /--hm-font: "Mona Sans",/);
  assert.match(html, /family=Mona\+Sans:wght@400;500;600;700/);
});

test("the theme leaves chart drawing, TOS colours, Sectors and signals alone", () => {
  const rules = css.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const selector of [/canvas/, /sector-heatmap/, /tv-lightweight/, /\.tos-/]) {
    const hits = rules.split("}").filter((block) => selector.test(block.split("{")[0] || "") && /(?:^|[;{\s])(?:background|color|border-color)\s*:/.test(block.split("{")[1] || ""));
    assert.deepEqual(hits, [], `${selector} must keep its own colours`);
  }
});
