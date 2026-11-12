import test from "node:test";
import assert from "node:assert/strict";
import { ChartProjectRoiPrimitive, PROJECT_ROI_LABEL as BOX, layoutProjectRoiLabels, projectRoiStartTime } from "./chartProjectRoiPrimitive.js";

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

test("draws three lines and three label boxes from the plan's bar, no background zone", () => {
  const primitive = new ChartProjectRoiPrimitive();
  attached(primitive);
  primitive.setModel({ lines: LINES, planTimeSec: 750 });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  // No shaded zones (his call, 2026-11-11): the only fills are the 3 label boxes.
  assert.equal(calls.rects.length, 3);
  assert.ok(calls.rects.every((rect) => rect.h === BOX.height), "a background zone was drawn");
  assert.equal(calls.lines, 3);
  assert.deepEqual(calls.texts.map((item) => item.text), ["Target 110.00  +250%", "Entry 100.00  105C $1.20", "Stop 95.00  −50%"]);
  assert.ok(calls.texts.every((item) => item.x === 200 + 4 + BOX.padX), "labels sit at the left end of the lines");
  assert.equal(calls.texts[0].fill, "#3dff8a");
});

test("a plan older than the window starts at the left edge; labels never run off the right", () => {
  const primitive = new ChartProjectRoiPrimitive();
  attached(primitive, { startX: 590 });
  primitive.setModel({ lines: LINES, planTimeSec: 750 });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  // fake measureText: 7 px a character, +12 padding; text sits 6 px inside its box.
  const widest = Math.max(...LINES.map((line) => line.title.length * 7 + BOX.padX * 2));
  assert.ok(calls.texts.every((item) => item.x - BOX.padX + widest <= 600 - 4), "a box runs off the right edge");

  const older = new ChartProjectRoiPrimitive();
  older.attached({
    chart: { timeScale: () => ({ timeToCoordinate: () => null }) },
    series: { priceToCoordinate: (price) => ({ 100: 150, 110: 50, 95: 200 })[price], data: () => BARS },
    requestUpdate() {},
  });
  older.setModel({ lines: LINES, planTimeSec: 750 });
  const drawn = fakeTarget();
  older.views[0].renderer().draw(drawn.target);
  assert.ok(drawn.calls.texts.every((item) => item.x === 4 + BOX.padX));
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

test("on a narrow phone pane the long broker-price boxes shrink their font and stay inside", () => {
  const long = [
    { key: "entry", price: 100, title: "Entry 217.30  222.5C $1.31" },
    { key: "target", price: 110, title: "Target 222.80  +158%  222.5C $3.38" },
    { key: "stop", price: 95, title: "Stop 214.84  −50%  222.5C $0.66" },
  ];
  const primitive = new ChartProjectRoiPrimitive();
  attached(primitive, { startX: 200 });
  primitive.setModel({ lines: long, planTimeSec: 750 });
  const fonts = [];
  const { calls, target } = fakeTarget(220, 300);
  const ctx = {};
  const realDraw = target.useMediaCoordinateSpace;
  target.useMediaCoordinateSpace = (fn) => realDraw((scope) => {
    // Width per character follows the font size, like a real canvas.
    let size = 11;
    Object.defineProperty(scope.context, "font", { set(value) { size = Number(/(\d+)px/.exec(value)[1]); fonts.push(size); }, get() { return `${size}px`; } });
    scope.context.measureText = (text) => ({ width: String(text).length * size * 0.62 });
    fn(scope);
  });
  primitive.views[0].renderer().draw(target);
  assert.ok(fonts.at(-1) < 11, "the font did not step down on a narrow pane");
  const boxes = calls.rects.filter((rect) => rect.h === BOX.height);
  assert.equal(boxes.length, 3);
  assert.ok(boxes.every((box) => box.x >= 0 && box.x + box.w <= 220), "a box runs off the pane");
  assert.ok(calls.texts.some((item) => item.text.endsWith("222.5C $3.38")));
  void ctx;
});

test("three targets: three green boxes, no background zone", () => {
  const prices = { 100: 150, 104: 110, 110: 50, 116: 20, 95: 200 };
  const primitive = new ChartProjectRoiPrimitive();
  primitive.attached({
    chart: { timeScale: () => ({ timeToCoordinate: (time) => (time === 700 ? 200 : null) }) },
    series: { priceToCoordinate: (price) => prices[price] ?? null, data: () => BARS },
    requestUpdate() {},
  });
  primitive.setModel({ planTimeSec: 750, lines: [
    { key: "entry", price: 100, title: "Entry 100.00  105C $1.20" },
    { key: "t1", price: 104, title: "T1 104.00  +80%  105C $2.16" },
    { key: "target", price: 110, title: "T2 110.00  +250%  105C $4.20" },
    { key: "t3", price: 116, title: "T3 116.00  +500%  105C $7.20" },
    { key: "stop", price: 95, title: "Stop 95.00  −50%  105C $0.60" },
  ] });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  assert.equal(calls.rects.filter((rect) => rect.h !== BOX.height).length, 0, "a background zone was drawn");
  assert.deepEqual(calls.texts.map((item) => item.text.split(" ")[0]), ["T3", "T2", "T1", "Entry", "Stop"]);
});

test("both sides: seven boxes, no background zone, prefixed keys keep their colors", () => {
  const prices = { 100: 150, 110: 50, 116: 20, 90: 250, 84: 280, 96: 180, 104: 120 };
  const primitive = new ChartProjectRoiPrimitive();
  primitive.attached({
    chart: { timeScale: () => ({ timeToCoordinate: (time) => (time === 700 ? 200 : null) }) },
    series: { priceToCoordinate: (price) => prices[price] ?? null, data: () => BARS },
    requestUpdate() {},
  });
  primitive.setModel({ planTimeSec: 750, lines: [
    { key: "entry", price: 100, title: "Entry 100.00  105C $1.20 · 95P $1.10" },
    { key: "c-target", price: 110, title: "T2 110.00  +250%  105C $4.20" },
    { key: "c-t3", price: 116, title: "T3 116.00  +500%  105C $7.20" },
    { key: "c-stop", price: 96, title: "Stop 96.00  −50%  105C $0.60" },
    { key: "p-target", price: 90, title: "T2 90.00  +240%  95P $3.70" },
    { key: "p-t3", price: 84, title: "T3 84.00  +480%  95P $6.40" },
    { key: "p-stop", price: 104, title: "Stop 104.00  −50%  95P $0.55" },
  ] });
  const { calls, target } = fakeTarget();
  primitive.views[0].renderer().draw(target);
  // No shaded zones ("no need background color"): only the 7 label boxes fill.
  assert.equal(calls.rects.filter((rect) => rect.h !== BOX.height).length, 0, "a background zone was drawn");
  assert.equal(calls.texts.length, 7);
  const stopText = calls.texts.find((item) => item.text.startsWith("Stop 104"));
  assert.equal(stopText.fill, "#ff8a95", "a prefixed stop keeps the stop color");
});
