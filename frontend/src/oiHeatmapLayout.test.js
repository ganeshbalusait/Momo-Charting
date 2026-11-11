import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { formatHeatmapSmallNumber, liveDteGridColumns, liveDteGridMinWidth, LIVE_DTE_MIN_COLUMN_PX, LIVE_DTE_STRIKE_COLUMN_PX } from "./oiHeatmapLayout.js";

test("live DTE heatmap columns are compact", () => {
  assert.equal(liveDteGridColumns(10), "64px repeat(10, minmax(98px, 1fr))");
  assert.ok(LIVE_DTE_MIN_COLUMN_PX < 118, "columns must stay narrower than the old 118px boxes");
  assert.ok(LIVE_DTE_STRIKE_COLUMN_PX < 96);
});

test("bad expiry counts never produce a broken grid", () => {
  assert.equal(liveDteGridColumns(undefined), "64px repeat(0, minmax(98px, 1fr))");
  assert.equal(liveDteGridColumns(-3), "64px repeat(0, minmax(98px, 1fr))");
  assert.equal(liveDteGridColumns("4"), "64px repeat(4, minmax(98px, 1fr))");
});

test("compact CSS keeps rows short and the live price line aligned to the strike column", () => {
  const css = readFileSync(new URL("./oiHeatmapCompact.css", import.meta.url), "utf8");
  const heights = [...css.matchAll(/min-height:\s*(\d+)px/g)].map((m) => Number(m[1]));
  assert.ok(heights.length > 0 && heights.every((h) => h <= 48), `rows must be <=48px, got ${heights}`);
  assert.match(css, new RegExp(`live-price::before \\{ left: ${LIVE_DTE_STRIKE_COLUMN_PX}px; \\}`));
  const app = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(app, /gridTemplateColumns: liveDteGridColumns\(model\.expiries\.length\)/);
  assert.match(app, /import "\.\/oiHeatmapCompact\.css";/);
});

test("grid scrolls sideways instead of squeezing columns on a phone", () => {
  assert.equal(liveDteGridMinWidth(10), "max(100%, 1044px)");
  assert.equal(liveDteGridMinWidth(0), "max(100%, 64px)");
  assert.ok(LIVE_DTE_MIN_COLUMN_PX >= 96, "the VOL/OI line needs ~90px plus padding");
});

test("compact rules out-rank the equal-specificity index.css rules", () => {
  const css = readFileSync(new URL("./oiHeatmapCompact.css", import.meta.url), "utf8");
  const selectors = css.replace(/\/\*[\s\S]*?\*\//g, "").split("}").map((r) => r.split("{")[0]).join(",").split(",").map((s) => s.trim()).filter(Boolean);
  assert.ok(selectors.length > 10);
  for (const s of selectors) assert.match(s, /^section\.oi-finder-strike-heatmap\.is-live-dte /, s);
});

test("small heatmap lines stay exact below 100,000 and shorten above it", () => {
  assert.equal(formatHeatmapSmallNumber(99_999), "99,999");
  assert.equal(formatHeatmapSmallNumber(0), "0");
  assert.equal(formatHeatmapSmallNumber(100_000), "100K");
  assert.equal(formatHeatmapSmallNumber(483_880), "484K");
  assert.equal(formatHeatmapSmallNumber(999_499), "999K");
  assert.equal(formatHeatmapSmallNumber(999_500), "1M");
  assert.equal(formatHeatmapSmallNumber(1_240_000), "1.2M");
});

test("missing heatmap numbers show a dash, never a false zero", () => {
  for (const value of [null, undefined, "", "abc", NaN]) assert.equal(formatHeatmapSmallNumber(value), "--");
});
