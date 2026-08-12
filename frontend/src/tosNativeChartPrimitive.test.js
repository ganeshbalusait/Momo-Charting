import assert from "node:assert/strict";
import test from "node:test";

import {
  alignNativeCloudPair,
  expandNativeSignalPriceRange,
  nativeFireGlyphPoints,
  nativeFireMotion,
  nativeHighlightPulse,
  nativeLevelLabelGutter,
  nativeCloudBandShapes,
  nativeSignalAutoscaleMargins,
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

test("pauses decorative fire animation while the trader pans or zooms", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.setData({
    bars: [{ time: 200, high: 12, low: 10 }],
    candleHighlights: [{ time: 200, color: "#00ffff", fire: true }],
  });
  primitive.signalRenderer.setGeometry({
    candleHighlights: [{ time: 200, fire: true }],
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
    primitive.signalRenderer.setGeometry({ candleHighlights: [{ time: 200, fire: true }] });
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
