import test from "node:test";
import assert from "node:assert/strict";
import { ChartProjectRoiPrimitive, layoutProjectRoiLabels, projectRoiStartTime } from "./chartProjectRoiPrimitive.js";

const BARS = [{ time: 100 }, { time: 400 }, { time: 700 }, { time: 1000 }];

test("the plan starts on the last bar at or before it was made", () => {
  assert.equal(projectRoiStartTime(BARS, 750), 700);
  assert.equal(projectRoiStartTime(BARS, 400), 400);
  assert.equal(projectRoiStartTime(BARS, 50), 100, "older than the tape: first bar");
  assert.equal(projectRoiStartTime(BARS, 5000), 1000);
  assert.equal(projectRoiStartTime([], 500), null);
});

test("label boxes never overlap and stay inside the pane", () => {
  const placed = layoutProjectRoiLabels([
    { key: "entry", y: 100 }, { key: "stop", y: 108 }, { key: "target", y: 20 },
  ], { height: 18, gap: 2, top: 2, bottom: 300 });
  assert.deepEqual(placed.map((item) => item.key), ["target", "entry", "stop"]);
  for (let index = 1; index < placed.length; index += 1) {
    assert.ok(placed[index].boxY >= placed[index - 1].boxY + 20, "boxes overlap");
  }
  const low = layoutProjectRoiLabels([{ key: "a", y: 295 }, { key: "b", y: 296 }], { height: 18, gap: 2, bottom: 300 });
  assert.ok(low.at(-1).boxY + 18 <= 300, "the last box runs off the bottom");
  assert.deepEqual(layoutProjectRoiLabels([{ key: "x", y: null }]), []);
});

function fakeTarget(width = 600, height = 300) {
  const calls = { rects: [], boxes: [], texts: [], lines: 0 };
  const ctx = {
    save() {}, restore() {}, beginPath() {}, moveTo() {}, stroke() { calls.lines += 1; },
    lineTo() {}, setLineDash() {},
    measureText: (text) => ({ width: String(text).length * 7 }),
    fillRect: (x, y, w, h) => calls.rects.push({ x, y, w, h, fill: ctx.fillStyle }),
    strokeRect: (x, y, w, h) => calls.boxes.push({ x, y, w, h }),
    fillText: (text, x, y) => calls.texts.push({ text, x, y, fill: ctx.fillStyle }),
  };
  return { calls, target: { useMediaCoordinateSpace: (fn) => fn({ context: ctx, mediaSize: { width, height } }) } };
}

function attached(primitive, { startX = 200 } = {}) {
  const prices = { 100: 150, 110: 50, 95: 200 };
  primitive.attached({
    chart: { timeScale: () => ({ timeToCoordinate: (time) => (time === 700 ? startX : null) }) },
    series: { priceToCoordinate: (price) => prices[price] ?? null, data: () => BARS },
    requestUpdate() {},
  });
}

const LINES = [
  { key: "entry", price: 100, title: "Entry 100.00  105C $1.20" },
  { key: "target", price: 110, title: "Target 110.00  +250%" },
  { key: "stop", price: 95, title: "Stop 95.00  −50%" },
];

test("draws a green and a red zone from the plan's bar, three lines and three label boxes", () => {
  const primitive = new ChartProjectRoiPrimitive();
  attached(primitive);
  primitive.setModel({ lines: LINES, planTimeSec: 750 });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  const zones = calls.rects.filter((rect) => /rgba\(/.test(rect.fill) && rect.w === 400);
  assert.equal(zones.length, 2, "one zone to the target, one to the stop");
  assert.deepEqual(zones.map((zone) => [zone.x, zone.y, zone.h]), [[200, 50, 100], [200, 150, 50]]);
  assert.equal(calls.lines, 3);
  assert.deepEqual(calls.texts.map((item) => item.text), ["Target 110.00  +250%", "Entry 100.00  105C $1.20", "Stop 95.00  −50%"]);
  assert.ok(calls.texts.every((item) => item.x === 210), "labels sit at the left end of the lines");
  assert.equal(calls.texts[0].fill, "#3dff8a");
});

test("a plan older than the window starts at the left edge; labels never run off the right", () => {
  const primitive = new ChartProjectRoiPrimitive();
  attached(primitive, { startX: 590 });
  primitive.setModel({ lines: LINES, planTimeSec: 750 });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  assert.ok(calls.texts.every((item) => item.x <= 600 - 190 + 10));

  const older = new ChartProjectRoiPrimitive();
  older.attached({
    chart: { timeScale: () => ({ timeToCoordinate: () => null }) },
    series: { priceToCoordinate: (price) => ({ 100: 150, 110: 50, 95: 200 })[price], data: () => BARS },
    requestUpdate() {},
  });
  older.setModel({ lines: LINES, planTimeSec: 750 });
  const drawn = fakeTarget();
  older.views[0].renderer().draw(drawn.target);
  assert.ok(drawn.calls.texts.every((item) => item.x === 10));
});

test("no plan or no series draws nothing; autoscale stays with App.jsx", () => {
  const primitive = new ChartProjectRoiPrimitive();
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  attached(primitive);
  primitive.setModel({ lines: [], planTimeSec: 0 });
  primitive.views[0].renderer().draw(target);
  assert.equal(calls.rects.length + calls.texts.length, 0);
  assert.equal(primitive.autoscaleInfo(), null);
  primitive.detached();
  assert.equal(primitive.series, null);
});

test("reports which label boxes it drew inside the pane, only when that changes", () => {
  const seen = [];
  const primitive = new ChartProjectRoiPrimitive({ onDrawn: (keys) => seen.push(keys) });
  attached(primitive);
  primitive.setModel({ lines: LINES, planTimeSec: 750 });
  primitive.views[0].renderer().draw(fakeTarget().target);
  primitive.views[0].renderer().draw(fakeTarget().target);
  assert.deepEqual(seen, ["target|entry|stop"]);
  // A pane too short for the stop (y 200): only target and entry.
  primitive.views[0].renderer().draw(fakeTarget(600, 180).target);
  assert.deepEqual(seen, ["target|entry|stop", "target|entry"]);
});
