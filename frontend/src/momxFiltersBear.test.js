import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_MOMX_FILTERS, sqzGroupResult, skittlesGroupResult, rvolGroupResult, isStarred, describeFilters, mirroredState,
} from "./momxFilters.js";
import { buildCondition } from "./momxHistoryQuery.js";

// "Filter should work on bull and bear - no guess work" (2026-09-26): one
// filter setting; on a BEAR row each ticked colour means its down-side twin.
const cfg = (over) => JSON.parse(JSON.stringify({ ...DEFAULT_MOMX_FILTERS, ...over }));
const sqzCfg = cfg({ sqz: { on: true, mode: "any", timeframes: { "2h": true, "4h": false, D: false, Wk: false }, pass: { cyan: true } } });
const skitCfg = cfg({ skittles: { on: true, mode: "any", rankToTop: true, timeframes: { D: true },
  pass: { cyan: true, green: true, lime: true, dark_green: true } } });
const row = (direction, sqzBg, skitBg) => ({
  symbol: "X", direction, sqz: { "2h": { bg: sqzBg } }, skittles: { D: { bg: skitBg } },
});

test("SQZ: bull passes a fired-UP squeeze (cyan) only; bear passes fired-down (magenta) only", () => {
  assert.equal(sqzGroupResult(row("bull", "cyan"), sqzCfg), true);
  assert.equal(sqzGroupResult(row("bull", "magenta"), sqzCfg), false);
  assert.equal(sqzGroupResult(row("bear", "magenta"), sqzCfg), true);
  assert.equal(sqzGroupResult(row("bear", "cyan"), sqzCfg), false);      // the old bug: bullish fire on the bear board
});

test("SQZ: a still-squeezed state means the same on both boards", () => {
  const c = cfg({ sqz: { ...sqzCfg.sqz, pass: { orange: true } } });
  assert.equal(sqzGroupResult(row("bear", "orange"), c), true);
  assert.equal(sqzGroupResult(row("bull", "orange"), c), true);
});

test("Skittles: the four bullish crosses on bull become the four bearish crosses on bear", () => {
  for (const bg of ["cyan", "green", "lime", "dark_green"]) {
    assert.equal(skittlesGroupResult(row("bull", null, bg), skitCfg), true, "bull " + bg);
    assert.equal(skittlesGroupResult(row("bear", null, bg), skitCfg), false, "bear " + bg);
  }
  for (const bg of ["magenta", "red", "light_red", "plum"]) {
    assert.equal(skittlesGroupResult(row("bear", null, bg), skitCfg), true, "bear " + bg);
    assert.equal(skittlesGroupResult(row("bull", null, bg), skitCfg), false, "bull " + bg);
  }
});

test("the star follows the same mirror", () => {
  assert.equal(isStarred(row("bear", null, "red"), skitCfg), true);
  assert.equal(isStarred(row("bear", null, "green"), skitCfg), false);
});

test("RVOL 'bullish only' already reads sellers on a bear row (unchanged)", () => {
  const c = cfg({ rvol: { on: true, mode: "any", bullishOnly: true, timeframes: { "5m": { on: true, min: 2 } } } });
  assert.equal(rvolGroupResult({ direction: "bear", rvol: { "5m": { value: 3, bg: "magenta" } } }, c), true);
  assert.equal(rvolGroupResult({ direction: "bear", rvol: { "5m": { value: 3, bg: "cyan" } } }, c), false);
  assert.equal(rvolGroupResult({ direction: "bull", rvol: { "5m": { value: 3, bg: "cyan" } } }, c), true);
});

test("the summary sentence speaks the board's direction", () => {
  const both = cfg({ sqz: sqzCfg.sqz, skittles: skitCfg.skittles });
  assert.match(describeFilters(both, "bull"), /SQZ .* is cyan/);
  assert.match(describeFilters(both, "bull"), /a bullish Skittles cross/);
  assert.match(describeFilters(both, "bear"), /SQZ .* is magenta/);
  assert.match(describeFilters(both, "bear"), /a bearish Skittles cross/);
});

test("History FIND: 'buyers winning' becomes sellers on the bear archive", () => {
  const state = { section: "rvol", timeframe: "1h", min: 3, bullishOnly: true };
  assert.deepEqual(buildCondition(state, "bull").bg, ["cyan", "green"]);
  assert.deepEqual(buildCondition(state, "bear").bg, ["magenta", "red"]);
  assert.deepEqual(buildCondition(state).bg, ["cyan", "green"]);          // default unchanged
});

test("mirror pairs are the app's own bear sets", () => {
  assert.equal(mirroredState("sqz", "cyan", true), "magenta");
  assert.equal(mirroredState("sqz", "white", true), "white");
  assert.equal(mirroredState("skittles", "dark_green", true), "plum");
  assert.equal(mirroredState("skittles", "cyan", false), "cyan");
});
