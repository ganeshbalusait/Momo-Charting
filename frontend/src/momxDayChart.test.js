import test from "node:test";
import assert from "node:assert/strict";
import { dayChartModel } from "./momxDayChart.js";

const at = (hhmm) => Date.parse("2026-10-02T" + hhmm + ":00-04:00") / 1000;

test("NBIS Fri Oct 2: 4:00-20:00 ET line, walls and ±EM2 edges inside the box", () => {
  const bars = [[at("04:00"), 233], [at("09:30"), 236], [at("10:25"), 246.99], [at("15:55"), 242.95], [at("19:55"), 243]];
  const walls = { spot: 246.99, expectedMove: 2.6, calls: [{ strike: 245, openInterest: 3200 }, { strike: 240, openInterest: 19500 }],
    puts: [{ strike: 150, openInterest: 9000 }] };          // far away - must not squash the chart
  const m = dayChartModel(bars, walls, { width: 160, height: 100 });
  assert.equal(m.segments.length, 4);
  assert.equal(m.segments[0].x1, 0);                      // 04:00 at the left edge
  assert.ok(Math.abs(m.shade[0].w - 55) < 0.01);         // premarket shade ends at 09:30 (330 of 960 min)
  assert.equal(m.segments[1].up, true);                  // 246.99 vs 233 three bars back
  assert.equal(m.segments[3].up, true);                  // 243 vs 236 three bars back (15-min trend, not flicker)
  assert.deepEqual(m.walls.map((w) => w.label), ["245C 3.2k", "240C 20k"]);
  assert.ok(m.walls.every((w) => w.y >= 0 && w.y <= 100));
  assert.ok(m.band.top !== null && m.band.bottom !== null);
  assert.equal(m.low, 233);
  assert.equal(m.high, 246.99);
});

test("too few bars or junk draws nothing", () => {
  assert.equal(dayChartModel([], null), null);
  assert.equal(dayChartModel([[at("03:00"), 230], [at("20:30"), 231]], null), null);  // outside 4:00-20:00
});

test("a History snapshot marks its moment on its own day; another day's moment is not drawn", () => {
  const bars = [[at("09:30"), 236], [at("10:25"), 246.99], [at("15:55"), 242.95]];
  const m = dayChartModel(bars, null, { width: 960, height: 100, markMs: Date.parse("2026-10-02T09:47:00-04:00") });
  assert.equal(m.mark, 9 * 60 + 47 - 240);                  // minutes after 04:00 on a 960-wide box
  assert.equal(dayChartModel(bars, null, { markMs: Date.parse("2026-10-04T00:00:15-04:00") }).mark, null);
  assert.equal(dayChartModel(bars, null).mark, null);
});

test("ARQQ: a far wall is labelled at the edge, not dropped", () => {
  const bars = [[at("09:30"), 22.44], [at("10:30"), 24.86], [at("15:55"), 24.8]];
  const m = dayChartModel(bars, { calls: [{ strike: 35, openInterest: 75 }], puts: [{ strike: 22.5, openInterest: 58 }] });
  assert.deepEqual(m.walls.map((w) => w.label), ["22.5P 58"]);
  assert.equal(m.offAbove.label, "35C 75");
  assert.equal(m.offBelow, null);
});
