import assert from "node:assert/strict";
import test from "node:test";

import {
  alignNativeCloudPair,
  expandNativeSignalPriceRange,
  nativeFireGlyphPoints,
  nativeFireMotion,
  nativeGeometry,
  nativeHighlightFrame,
  nativeHighlightShows,
  nativeCloudUnionRuns,
  mergeCandleHighlights,
  nativeHighlightPulse,
  nativeLevelLabelGutter,
  nativeCloudBandShapes,
  NATIVE_SIGNAL_GRADE_RADIUS,
  nativeSignalAutoscaleMargins,
  nativeSignalBoxLeft,
  nativeSignalLabelTops,
  nativeSignalStackScope,
  TosNativeChartPrimitive,
} from "./tosNativeChartPrimitive.js";

test("ends each level line at the left edge of its own name", () => {
  assert.equal(nativeLevelLabelGutter(""), 92);
  assert.equal(nativeLevelLabelGutter("dL"), 14);
  assert.ok(Math.abs(nativeLevelLabelGutter("44.4K 8/21") - 60.6) < 0.01);
  assert.ok(nativeLevelLabelGutter("pmH") < nativeLevelLabelGutter("+EM 275.93"));
});

test("aligns native cloud boundaries only on identical TOS chart bars", () => {
  assert.deepEqual(alignNativeCloudPair(
    [
      { time: 100, value: 10 },
      { time: 200, value: 11 },
      { time: 300, value: 12 },
    ],
    [
      { time: 100, value: 9 },
      { time: 300, value: 10 },
    ],
  ), [
    { time: 100, fast: 10, slow: 9 },
    { time: 300, fast: 12, slow: 10 },
  ]);
});

test("pulses only while a newly fired candle highlight is active", () => {
  assert.ok(nativeHighlightPulse(2_000, 1_000) > 0);
  assert.equal(nativeHighlightPulse(1_000, 1_000), 0);
  assert.equal(nativeHighlightPulse(0, 1_000), 0);
});

test("builds a compact TOS-style lightning trace beside a fire candle", () => {
  const points = nativeFireGlyphPoints({
    x: 40,
    top: 20,
    bottom: 50,
    width: 8,
    bullish: true,
  });
  assert.equal(points.length, 6);
  assert.ok(points.every(([x, y]) => Number.isFinite(x) && Number.isFinite(y)));
  assert.ok(points.every(([x]) => x < 40));
  assert.deepEqual(nativeFireGlyphPoints({ x: 10, top: 20, bottom: 20 }), []);
});

test("one candle with several signals gets one highlight, and fire wins (NVDA 09:30, 2026-11-19)", () => {
  const merged = mergeCandleHighlights([
    { time: 300, color: "#18f0ff", intensity: 1.3 },               // CALL5
    { time: 300, color: "#18f0ff", intensity: 1.3 },               // CALL15
    { time: 300, color: "#00e5ff", intensity: 1.45, fire: true, flashUntilMs: 50 }, // 🔥5
    { time: 300, color: "#00e5ff", intensity: 1.45, fire: true, flashUntilMs: 90 }, // 🔥15
    { time: 600, color: "#ff20b8", intensity: 1.3 },
  ]);
  assert.equal(merged.length, 2);
  assert.deepEqual(
    { time: merged[0].time, fire: merged[0].fire, color: merged[0].color, intensity: merged[0].intensity, flash: merged[0].flashUntilMs },
    { time: 300, fire: true, color: "#00e5ff", intensity: 1.45, flash: 90 },
  );
  assert.equal(merged[1].fire, undefined);
  assert.deepEqual(mergeCandleHighlights(null), []);
});

test("the highlight frame sits outside the candle so its body and wick stay visible", () => {
  const candle = { x: 40, top: 20, bottom: 80, width: 8 };
  const frame = nativeHighlightFrame(candle);
  assert.ok(frame.left < candle.x - candle.width / 2);
  assert.ok(frame.left + frame.width > candle.x + candle.width / 2);
  assert.ok(frame.top < candle.top);
  assert.ok(frame.top + frame.height > candle.bottom);
  assert.equal(nativeHighlightFrame({ x: Number.NaN, top: 1, bottom: 2 }), null);
  // The flame trace stands outside that frame, not on the candle.
  const flame = nativeFireGlyphPoints({ ...candle, bullish: true });
  assert.ok(flame.every(([x]) => x < frame.left));
});

test("the native geometry draws one highlight for a candle that carries four signals", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 300, open: 10, high: 12, low: 9, close: 11.5 }],
    timeframeMinutes: 5,
    candleHighlights: [
      { time: 300, color: "#18f0ff", intensity: 1.3 },
      { time: 300, color: "#18f0ff", intensity: 1.3 },
      { time: 300, color: "#00e5ff", intensity: 1.45, fire: true },
      { time: 300, color: "#00e5ff", intensity: 1.45, fire: true },
    ],
  });
  const chart = {
    paneSize: () => ({ width: 300, height: 200 }),
    timeScale: () => ({
      timeToCoordinate: (time) => Number(time) / 2,
      getVisibleRange: () => ({ from: 0, to: 1_000 }),
      options: () => ({ barSpacing: 10 }),
    }),
  };
  const series = { priceToCoordinate: (price) => 200 - Number(price) * 10 };
  const geometry = nativeGeometry(primitive.model, chart, series);
  assert.equal(geometry.candleHighlights.length, 1);
  assert.equal(geometry.candleHighlights[0].fire, true);
});

test("overlapping signal and 🔥 clouds are drawn one layer deep, not stacked brighter (NVDA 09:30, 2026-11-19)", () => {
  const primitive = new TosNativeChartPrimitive();
  const families = ["mtf-squeeze-release", "mtf-ema920", "mtf-4x8", "mtf-squeeze-release"];
  let made = 0;
  const squeeze = (startTime, endTime, anchor) => ({
    family: families[made++ % families.length], tone: "bull", startTime, endTime, anchor, color: "#00ffff", opacity: 0.2,
  });
  primitive.setData({
    bars: [
      { time: 300, open: 10, high: 12, low: 9, close: 11.5 },
      { time: 600, open: 11.5, high: 12.5, low: 11, close: 12 },
      { time: 900, open: 12, high: 13, low: 11.5, close: 12.8 },
    ],
    timeframeMinutes: 5,
    oneSidedClouds: [
      squeeze(300, 600, 9),    // 🔥5: the 09:30 candle only
      squeeze(300, 1200, 9),   // 🔥15: 09:30-09:45
      squeeze(300, 1200, 9),   // 🔥30
      squeeze(300, 1200, 8.5), // 🔥1h, anchored lower
    ],
  });
  const chart = {
    paneSize: () => ({ width: 300, height: 200 }),
    timeScale: () => ({
      timeToCoordinate: (time) => Number(time) / 10,
      getVisibleRange: () => ({ from: 0, to: 1_000 }),
      options: () => ({ barSpacing: 30 }),
    }),
  };
  const series = { priceToCoordinate: (price) => 200 - Number(price) * 10 };
  const fills = nativeGeometry(primitive.model, chart, series).fills;
  // One cloud over all three candles: the union reaches the lowest anchor.
  assert.equal(fills.length, 1);
  assert.equal(fills[0].opacity, 0.2);
  const ys = fills[0].points.map(([, y]) => y);
  assert.deepEqual([Math.min(...ys), Math.max(...ys)], [0, 115]);
});

test("one-sided cloud columns join into one shape per run of candles", () => {
  const runs = nativeCloudUnionRuns([
    { index: 2, bull: true, anchorY: 50, color: "#0ff", opacity: 0.2 },
    { index: 1, bull: true, anchorY: 50, color: "#0ff", opacity: 0.2 },
    { index: 3, bull: true, anchorY: 70, color: "#0ff", opacity: 0.2 },
    { index: 1, bull: false, anchorY: 10, color: "#f0f", opacity: 0.2 },
  ]);
  assert.deepEqual(runs.map((run) => [run.bull, run.from, run.to]), [[true, 1, 2], [true, 3, 3], [false, 1, 1]]);
  assert.deepEqual(nativeCloudUnionRuns(null), []);
});

test("continuously bends and flickers a visible fire trace", () => {
  const first = nativeFireMotion(1_000, 42);
  const second = nativeFireMotion(1_100, 42);
  assert.notDeepEqual(first, second);
  assert.ok(first.flicker >= 0 && first.flicker <= 1);
  assert.deepEqual(nativeFireMotion(Number.NaN, 42), { bend: 0, lift: 0, flicker: 0 });
});

test("sorts and de-duplicates native cloud points before matching bars", () => {
  assert.deepEqual(alignNativeCloudPair(
    [
      { time: 300, value: 12 },
      { time: 100, value: 10 },
      { time: 300, value: 13 },
    ],
    [
      { time: 300, value: 9 },
      { time: 100, value: 8 },
    ],
  ), [
    { time: 100, fast: 10, slow: 8 },
    { time: 300, fast: 13, slow: 9 },
  ]);
});

test("updates the live candle anchor without rebuilding the native study model", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [
      { time: 100, high: 11, low: 9 },
      { time: 200, high: 12, low: 10 },
    ],
    signals: [{ time: 200, position: "belowBar", text: "C2H", color: "#00ffff" }],
    candleHighlights: [{ time: 200, color: "#00ffff", fire: true }],
  });
  const cloudPairs = primitive.model.cloudPairs;
  primitive.updateLastBar({ time: 200, high: 12.5, low: 9.75 });
  assert.equal(primitive.model.bars.at(-1).high, 12.5);
  assert.equal(primitive.model.bars.at(-1).low, 9.75);
  assert.equal(primitive.model.cloudPairs, cloudPairs);
  assert.deepEqual(primitive.model.candleHighlights, [{ time: 200, color: "#00ffff", fire: true }]);
});

test("an old fire draws no box and no flame, and stops repainting (2026-11-19 'box around the candle')", () => {
  const now = 1_000_000;
  assert.equal(nativeHighlightShows({ fire: true, flashUntilMs: now + 2_000 }, now), true);
  assert.equal(nativeHighlightShows({ fire: true, flashUntilMs: now - 1 }, now), false);
  assert.equal(nativeHighlightShows({ fire: true }, now), false);
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 200, high: 12, low: 10 }],
    candleHighlights: [{ time: 200, color: "#00ffff", fire: true }],
  });
  primitive.signalRenderer.setGeometry({ candleHighlights: [{ time: 200, fire: true, flashUntilMs: 0 }] });
  assert.equal(primitive.hasActiveAnimation(), false);
});

test("pauses decorative fire animation while the trader pans or zooms", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 200, high: 12, low: 10 }],
    candleHighlights: [{ time: 200, color: "#00ffff", fire: true }],
  });
  primitive.signalRenderer.setGeometry({
    // A brand-new fire, still in its flash.
    candleHighlights: [{ time: 200, fire: true, flashUntilMs: Date.now() + 60_000 }],
  });

  assert.equal(primitive.hasActiveAnimation(), true);
  primitive.setInteractionActive(true);
  assert.equal(primitive.hasActiveAnimation(), false);
  primitive.setInteractionActive(false);
  assert.equal(primitive.hasActiveAnimation(), true);
});

test("sleeps between decorative fire paints instead of polling every display frame", () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  const originalRequestAnimationFrame = globalThis.requestAnimationFrame;
  const originalCancelAnimationFrame = globalThis.cancelAnimationFrame;
  let timerCallback = null;
  let frameCallback = null;
  let timerCalls = 0;
  let frameCalls = 0;
  let updateCalls = 0;
  try {
    globalThis.setTimeout = (callback) => {
      timerCalls += 1;
      timerCallback = callback;
      return 11;
    };
    globalThis.clearTimeout = () => {};
    globalThis.requestAnimationFrame = (callback) => {
      frameCalls += 1;
      frameCallback = callback;
      return 22;
    };
    globalThis.cancelAnimationFrame = () => {};

    const primitive = new TosNativeChartPrimitive();
    primitive.setData({
      bars: [{ time: 200, high: 12, low: 10 }],
      candleHighlights: [{ time: 200, color: "#00ffff", fire: true }],
    });
    primitive.signalRenderer.setGeometry({ candleHighlights: [{ time: 200, fire: true, flashUntilMs: Date.now() + 60_000 }] });
    primitive.requestUpdate = () => { updateCalls += 1; };

    primitive.ensureAnimationLoop();
    assert.equal(timerCalls, 1);
    assert.equal(frameCalls, 0);
    timerCallback();
    assert.equal(frameCalls, 1);
    frameCallback(1_000);
    assert.equal(updateCalls, 1);
    assert.equal(timerCalls, 2);
    primitive.detached();
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
    globalThis.requestAnimationFrame = originalRequestAnimationFrame;
    globalThis.cancelAnimationFrame = originalCancelAnimationFrame;
  }
});

test("updates the live RVOL value without rebuilding the complete chart model", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 200, high: 12, low: 10 }],
    signals: [{ time: 200, family: "rvol", text: "2.1", color: "#00ffff" }],
    cloudPairs: [{ key: "ema", fast: [], slow: [] }],
  });
  const cloudPairs = primitive.model.cloudPairs;
  primitive.updateLastBar({ time: 200, high: 12.5, low: 10 }, {
    rvolMarker: { time: 200, family: "rvol", text: "2.6", color: "#00ffff" },
  });
  assert.equal(primitive.model.signals.at(-1).text, "2.6");
  assert.equal(primitive.model.cloudPairs, cloudPairs);
});

test("moves an overflowing historical signal stack as one visible block", () => {
  const labels = [0, 1, 2, 3].map((stackIndex) => ({
    x: 120,
    y: 190,
    placement: "below",
    stackIndex,
    stackKey: "historical-macd-stack",
    family: "ganeshMacd",
    compact: false,
  }));

  assert.deepEqual(nativeSignalLabelTops(labels, 200), [110, 133, 156, 179]);
  assert.ok(nativeSignalLabelTops(labels, 200).every((top) => top >= 2 && top + 19 <= 198));
});

test("keeps native signal rows unchanged when the pane already has room", () => {
  const labels = [0, 1, 2].map((stackIndex) => ({
    x: 120,
    y: 100,
    placement: "below",
    stackIndex,
    stackKey: "visible-macd-stack",
    family: "ganeshMacd",
    compact: false,
  }));

  assert.deepEqual(nativeSignalLabelTops(labels, 240), [113, 136, 159]);
});

test("stacks Ganesh study families on their shared TOS source candle", () => {
  const signals = ["ganesh48", "ganesh920", "ganeshMacd"].map((family) => ({
    family,
    time: 100,
    position: "belowBar",
    text: family,
  }));

  assert.deepEqual(signals.map(nativeSignalStackScope), ["ganesh", "ganesh", "ganesh"]);
  assert.deepEqual(
    nativeSignalAutoscaleMargins(signals, { from: 100, to: 100 }),
    { above: 0, below: 80 },
  );
});

test("preserves spacing when an impossible stack is clipped by a reduced pane", () => {
  const labels = [0, 1, 2, 3].map((stackIndex) => ({
    x: 120,
    y: 40,
    placement: "below",
    stackIndex,
    stackKey: "reduced-pane-stack",
    family: "ganeshMacd",
    compact: false,
  }));

  assert.deepEqual(nativeSignalLabelTops(labels, 50), [2, 25, 48, 71]);
});

test("keeps cumulative gaps for mixed compact and full-size bubbles", () => {
  const labels = [
    { compact: false, stackIndex: 0 },
    { compact: true, stackIndex: 1 },
    { compact: false, stackIndex: 2 },
  ].map((label) => ({
    ...label,
    x: 120,
    y: 100,
    placement: "below",
    stackKey: "mixed-stack",
    family: "shared",
  }));

  assert.deepEqual(nativeSignalLabelTops(labels, 240), [113, 136, 157]);
});

test("mirrors a signal stack away from the top pane edge", () => {
  const labels = [0, 1, 2].map((stackIndex) => ({
    x: 120,
    y: 5,
    placement: "above",
    stackIndex,
    stackKey: "put-stack",
    family: "ganeshMacd",
    compact: false,
  }));

  assert.deepEqual(nativeSignalLabelTops(labels, 200), [48, 25, 2]);
});

test("requests autoscale pixels for each visible TOS signal row", () => {
  const signals = ["D", "2D", "3D", "4D", "W"].map((timeframe) => ({
    time: 200,
    position: "belowBar",
    family: "ganeshMacd",
    text: `MACD-${timeframe}`,
  }));
  signals.push(
    { time: 200, position: "aboveBar", family: "squeeze", compact: true },
    { time: 200, position: "aboveBar", family: "cloudmax", compact: true },
    { time: 400, position: "belowBar", family: "ganeshMacd" },
  );

  assert.deepEqual(nativeSignalAutoscaleMargins(signals, { from: 100, to: 300 }), {
    above: 53,
    below: 126,
  });
});

test("expands an automatic price range to fit the visible historical ladder", () => {
  const signals = ["D", "2D", "3D", "4D", "W"].map((timeframe) => ({
    time: 200,
    price: 225.44,
    position: "belowBar",
    family: "ganeshMacd",
    text: `MACD-${timeframe}`,
  }));

  const range = expandNativeSignalPriceRange({
    low: 230,
    high: 273,
    signals,
    visibleRange: { from: 100, to: 300 },
    paneHeight: 500,
  });
  assert.ok(range.low < 210);
  assert.equal(range.high, 273);
  assert.deepEqual(expandNativeSignalPriceRange({
    low: 230,
    high: 273,
    signals,
    visibleRange: { from: 300, to: 400 },
    paneHeight: 500,
  }), { low: 230, high: 273 });
});

test("converts the 5-minute TOS squeeze band into upper and lower price-time fills", () => {
  const shapes = nativeCloudBandShapes([{
    time: 100,
    endTime: 400,
    timeframeKey: "5m",
    indicatorKey: "cloudBands5m",
    upperBand: 105,
    lowerBand: 95,
    upperMid: 106,
    lowerMid: 94,
    upperLow: 108,
    lowerLow: 92,
    upperHigh: 104,
    lowerHigh: 96,
  }], {
    cloudBands5mShowHigh: false,
    cloudBands5mShowMid: true,
    cloudBands5mShowLow: true,
    cloudBands5mMidColor: "#fff200",
    cloudBands5mLowColor: "#00ffff",
    cloudBands5mOpacity: 25,
  });

  assert.deepEqual(shapes.map(({ kind, first, second, color }) => ({
    kind,
    first,
    second,
    color,
  })), [
    { kind: "fill", first: 108, second: 105, color: "#00ffff" },
    { kind: "fill", first: 95, second: 92, color: "#00ffff" },
    { kind: "fill", first: 106, second: 105, color: "#fff200" },
    { kind: "fill", first: 95, second: 94, color: "#fff200" },
  ]);
});

test("suspending the pane clamp keeps every bubble glued to its panned candle", () => {
  const labels = [0, 1, 2].map((stackIndex) => ({
    x: 120,
    y: 30,
    placement: "above",
    stackIndex,
    stackKey: "pan-stack",
    family: "squeeze",
    compact: true,
  }));
  const paneHeight = 220;

  // At rest the clamp pins the edge stack inside the pane.
  const atRest = nativeSignalLabelTops(labels, paneHeight);
  assert.deepEqual(atRest, [44, 23, 2]);

  // During a pan (clamp suspended) moving the anchor by dy moves every row by
  // exactly dy - the glue invariant the iPhone vertical-pan report broke.
  const before = nativeSignalLabelTops(labels, paneHeight, false);
  const panned = nativeSignalLabelTops(
    labels.map((label) => ({ ...label, y: label.y - 40 })),
    paneHeight,
    false,
  );
  assert.deepEqual(panned, before.map((top) => top - 40));

  // Clamped layout cannot satisfy that invariant near the edge: the stack
  // stays pinned at the pane padding while the candle moves, which is why the
  // clamp must be suspended while a gesture is in flight.
  const pannedClamped = nativeSignalLabelTops(
    labels.map((label) => ({ ...label, y: label.y - 40 })),
    paneHeight,
  );
  assert.deepEqual(pannedClamped, atRest);
  assert.notDeepEqual(pannedClamped, atRest.map((top) => top - 40));
});

function gradeGeometry(signals, paneWidth = 400) {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [
      { time: 100, high: 11, low: 9 },
      { time: 200, high: 12, low: 10 },
    ],
    signals,
  });
  const chart = {
    paneSize: () => ({ width: paneWidth, height: 200 }),
    timeScale: () => ({
      timeToCoordinate: (time) => (Number(time) === 100 ? 100 : 200),
      options: () => ({ barSpacing: 6 }),
      getVisibleRange: () => null,
    }),
  };
  const series = { priceToCoordinate: (price) => 200 - Number(price) * 10 };
  return nativeGeometry(primitive.model, chart, series);
}

test("carries a scanner grade letter onto the chart label", () => {
  const geometry = gradeGeometry([
    { time: 200, position: "belowBar", text: "CALL1H", color: "#00ffff", grade: { letter: "A+" } },
    { time: 100, position: "belowBar", text: "CALL15", color: "#00ffff" },
  ]);
  const graded = geometry.labels.find((label) => label.text === "CALL1H");
  const plain = geometry.labels.find((label) => label.text === "CALL15");
  assert.equal(graded.grade, "A+");
  assert.equal(plain.grade, undefined);
});

test("ignores a grade without a letter string", () => {
  const geometry = gradeGeometry([
    { time: 200, position: "belowBar", text: "CALL1H", color: "#00ffff", grade: { letter: null } },
  ]);
  assert.equal(geometry.labels[0].grade, undefined);
});

test("keeps a graded label and its circle inside the pane at the right edge", () => {
  const paneWidth = 300;
  const boxWidth = 40;
  const radius = NATIVE_SIGNAL_GRADE_RADIUS;
  const left = nativeSignalBoxLeft(paneWidth - 4, boxWidth, paneWidth, "A+");
  const circleRight = left + boxWidth + 3 + radius * 2;
  assert.ok(circleRight <= paneWidth - 2, `circle right ${circleRight} past pane ${paneWidth}`);
  assert.ok(left >= 2);
  // Ungraded labels keep their original clamp.
  assert.equal(nativeSignalBoxLeft(paneWidth - 4, boxWidth, paneWidth, undefined), paneWidth - boxWidth - 2);
  // Away from the edge a grade does not move the box off its candle.
  assert.equal(nativeSignalBoxLeft(100, boxWidth, paneWidth, "A"), 80);
});

test("a level segment's explicit gutter lets it run to the price axis (AutoFib expansion)", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 100, high: 11, low: 9 }, { time: 200, high: 12, low: 10 }],
    levelSegments: [
      { price: 10, startTime: 200, color: "#00ffff", gutter: 0 },
      { price: 11, startTime: 200, color: "#ff00ff" },
      { price: 9, startTime: 200, color: "#ff00ff", gutter: null },
    ],
  });
  const chart = {
    paneSize: () => ({ width: 400, height: 200 }),
    timeScale: () => ({
      timeToCoordinate: (time) => (Number(time) === 100 ? 100 : 200),
      options: () => ({ barSpacing: 6 }),
      getVisibleRange: () => null,
    }),
  };
  const series = { priceToCoordinate: (price) => 200 - Number(price) * 10 };
  const lines = nativeGeometry(primitive.model, chart, series).lines.filter((line) => line.y1 === line.y2);
  const byY = new Map(lines.map((line) => [line.y1, line]));
  assert.equal(byY.get(100).x1, 200);
  assert.equal(byY.get(100).x2, 400);            // gutter 0: meets the axis
  assert.equal(byY.get(90).x2, 400 - 92);         // no label: default reserve
  assert.equal(byY.get(110).x2, 400 - 92);        // null is not "0" (Number(null) === 0)
});

test("a chart-pattern trend segment slopes between its two points, and a target with no end runs to its tag", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 100, high: 11, low: 9 }, { time: 200, high: 12, low: 10 }, { time: 300, high: 13, low: 11 }],
    trendSegments: [
      { startTime: 100, startPrice: 12, endTime: 300, endPrice: 10, color: "#00e5ff", lineWidth: 2 },
      { startTime: 300, startPrice: 15, endTime: null, endPrice: 15, color: "#00e5ff", dash: [6, 4] },
      { startTime: 300, startPrice: 9, endTime: 100, endPrice: 9 }, // backwards: dropped
      { startTime: "x", startPrice: 9, endTime: 300, endPrice: 9 }, // junk: dropped
    ],
  });
  const chart = {
    paneSize: () => ({ width: 400, height: 200 }),
    timeScale: () => ({
      timeToCoordinate: (time) => Number(time),
      options: () => ({ barSpacing: 6 }),
      getVisibleRange: () => null,
    }),
  };
  const series = { priceToCoordinate: (price) => 200 - Number(price) * 10 };
  const lines = nativeGeometry(primitive.model, chart, series).lines;
  const sloped = lines.find((line) => line.y1 !== line.y2);
  assert.deepEqual([sloped.x1, sloped.y1, sloped.x2, sloped.y2], [100, 80, 300, 100]);
  assert.equal(sloped.lineWidth, 2);
  const target = lines.find((line) => line.y1 === 50);
  assert.equal(target.x1, 300);
  assert.equal(target.x2, 400 - 92);
  assert.deepEqual(target.dash, [6, 4]);
  assert.equal(lines.length, 2);
});
