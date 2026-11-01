import assert from "node:assert/strict";
import test from "node:test";

import { calculateStochasX, weightedMovingAverage } from "./stochasX.js";

const ET = (iso) => Math.floor(Date.parse(iso) / 1000);

test("TOS WMA weights 1..n and is NaN until n values exist", () => {
  const out = weightedMovingAverage([1, 2, 3, 4], 3);
  assert.ok(Number.isNaN(out[0]) && Number.isNaN(out[1]));
  assert.equal(out[2], (1 * 1 + 2 * 2 + 3 * 3) / 6);
  assert.equal(out[3], (2 * 1 + 3 * 2 + 4 * 3) / 6);
});

// shared_StochasX_v20263: FullD crosses BELOW 90 -> LIGHT_RED wedge above the
// bar (BOOLEAN_WEDGE_UP); crosses ABOVE 10 -> LIME wedge below (WEDGE_DOWN).
test("prints a red wedge above when FullD drops through 90 and lime below through 10", () => {
  const start = ET("2026-10-01T13:00:00Z");
  const bars = [];
  let price = 100;
  const push = (delta) => {
    const open = price;
    price += delta;
    bars.push({ time: start + bars.length * 300, open, high: Math.max(open, price) + 0.05, low: Math.min(open, price) - 0.05, close: price });
  };
  for (let i = 0; i < 30; i += 1) push(0.5);   // strong rise: FullD pinned near 100
  for (let i = 0; i < 30; i += 1) push(-0.5);  // strong fall: FullD drops through 90, then near 0
  for (let i = 0; i < 30; i += 1) push(0.5);   // rise again: FullD climbs through 10
  const { wedges } = calculateStochasX(bars, { stochasXCopies: [{ kPeriod: 8, dPeriod: 8, averageType: "WEIGHTED" }] });
  const red = wedges.filter((w) => w.position === "above");
  const lime = wedges.filter((w) => w.position === "below");
  assert.ok(red.length >= 1, "a FullD cross below 90");
  assert.ok(lime.length >= 1, "a FullD cross above 10");
  assert.equal(red[0].color, "#ff3f00");
  assert.ok(red[0].time > start + 30 * 300 && red[0].time < start + 45 * 300);
  assert.ok(lime[0].time > start + 60 * 300);
});

test("limitToLastNDays keeps only the last 2 calendar days (ET)", () => {
  const bars = [];
  let price = 100;
  ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"].forEach((day) => {
    for (let i = 0; i < 40; i += 1) {
      const delta = i < 20 ? 0.5 : -0.5;
      const open = price;
      price += delta;
      bars.push({ time: ET(`${day}T14:00:00Z`) + i * 300, open, high: Math.max(open, price) + 0.05, low: Math.min(open, price) - 0.05, close: price });
    }
  });
  const all = calculateStochasX(bars, {}).wedges;
  const limited = calculateStochasX(bars, { stochasXLimitDays: true, stochasXDaysBack: 2 }).wedges;
  assert.ok(all.length > limited.length);
  assert.ok(limited.every((w) => w.time >= ET("2026-09-30T04:00:00Z")));
});

// 2026-10-01 his TOS settings screenshots: TWO StochasX copies on the chart,
// both 90/10, K 8, "limit to last n days" = No:
//   copy 1: D 8, WEIGHTED      copy 2: D 2, HULL
// and plot colours UpSignal (191,255,0) lime, DownSignal (255,63,0) red.
test("default copies are his TOS settings: WMA 8 and HULL 2, no day limit", async () => {
  const { STOCHASX_COPIES, hullMovingAverage } = await import("./stochasX.js");
  assert.deepEqual(STOCHASX_COPIES.map((c) => [c.kPeriod, c.dPeriod, c.averageType]), [[8, 8, "WEIGHTED"], [8, 2, "HULL"]]);
  // TOS HullMovingAvg(2) = WMA(2*WMA(x,1) - WMA(x,2), 1) = (4x - x[1]) / 3.
  const h = hullMovingAverage([3, 6, 9], 2);
  assert.ok(Number.isNaN(h[0]));
  assert.equal(h[1], (4 * 6 - 3) / 3);
  assert.equal(h[2], (4 * 9 - 6) / 3);
});

test("both copies print, a bar is wedged once per side, colours are his TOS colours", () => {
  const start = ET("2026-10-01T13:00:00Z");
  const bars = [];
  let price = 100;
  for (let i = 0; i < 120; i += 1) {
    const open = price;
    price += Math.sin(i / 3) * 0.8 + 0.05;
    bars.push({ time: start + i * 300, open, high: Math.max(open, price) + 0.05, low: Math.min(open, price) - 0.05, close: price });
  }
  const { wedges } = calculateStochasX(bars, {});
  const keys = wedges.map((w) => `${w.time}-${w.position}`);
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(wedges.some((w) => w.position === "above" && w.color === "#ff3f00"));
  assert.ok(wedges.some((w) => w.position === "below" && w.color === "#bfff00"));
});

test("a profile saved by the first build cannot pin one slow copy or the 2-day limit", () => {
  const start = ET("2026-09-28T13:00:00Z");
  const bars = [];
  let price = 100;
  for (let i = 0; i < 400; i += 1) {
    const open = price;
    price += Math.sin(i / 3) * 0.8;
    bars.push({ time: start + i * 300, open, high: Math.max(open, price) + 0.05, low: Math.min(open, price) - 0.05, close: price });
  }
  const stale = { stochasXKPeriod: 8, stochasXDPeriod: 8, stochasXLimitToLastNDays: true, stochasXDaysBack: 2 };
  assert.deepEqual(calculateStochasX(bars, stale).wedges, calculateStochasX(bars, {}).wedges);
});
