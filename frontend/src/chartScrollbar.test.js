import test from "node:test";
import assert from "node:assert/strict";

import {
  CHART_SCROLLBAR_MIN_THUMB_PX,
  CHART_SCROLLBAR_MIN_VISIBLE_BARS,
  chartScrollbarArrowStep,
  chartScrollbarLastIndex,
  chartScrollbarLiveEdgeRange,
  chartScrollbarMetrics,
  chartScrollbarPageStep,
  chartScrollbarRangeFromThumb,
  chartScrollbarTrackRange,
  chartWheelPanLogicalRange,
  clampChartScrollLogicalRange,
} from "./chartScrollbar.js";

function assertClose(actual, expected, tolerance = 1e-6) {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `expected ${actual} to be within ${tolerance} of ${expected}`,
  );
}

test("last index covers the loaded tape plus reserved forward slots", () => {
  assert.equal(chartScrollbarLastIndex({ barCount: 100, futureSlots: 0 }), 99);
  assert.equal(chartScrollbarLastIndex({ barCount: 100, futureSlots: 5 }), 104);
  assert.equal(chartScrollbarLastIndex({ barCount: 0, futureSlots: 0 }), 0);
  assert.equal(chartScrollbarLastIndex({}), 0);
});

test("track grows to contain a window zoomed out past the tape", () => {
  const track = chartScrollbarTrackRange({
    logicalRange: { from: -40, to: 260 },
    barCount: 100,
    futureSlots: 2,
  });
  // Without this the thumb would have to render outside its own rail.
  assert.equal(track.from, -40);
  assert.equal(track.to, 260);
  assert.equal(track.span, 300);
});

test("a window filling the whole tape disables scrolling and fills the track", () => {
  const metrics = chartScrollbarMetrics({
    logicalRange: { from: 0, to: 99 },
    barCount: 100,
    trackWidthPx: 200,
  });
  assert.equal(metrics.disabled, true);
  assert.equal(metrics.thumbWidthPx, 200);
  assert.equal(metrics.thumbLeftPx, 0);
});

test("thumb width is the visible share of the tape, and sits left at the oldest bar", () => {
  const metrics = chartScrollbarMetrics({
    logicalRange: { from: 0, to: 49 },
    barCount: 100,
    trackWidthPx: 200,
  });
  assert.equal(metrics.disabled, false);
  assertClose(metrics.thumbWidthPx, (200 * 49) / 99);
  assertClose(metrics.thumbLeftPx, 0);
});

test("thumb travels to the right edge when the window sits at the live edge", () => {
  const metrics = chartScrollbarMetrics({
    logicalRange: { from: 50, to: 99 },
    barCount: 100,
    trackWidthPx: 200,
  });
  assertClose(metrics.thumbLeftPx, 200 - metrics.thumbWidthPx);
});

test("a tiny visible share still renders a grabbable thumb", () => {
  const metrics = chartScrollbarMetrics({
    logicalRange: { from: 0, to: 20 },
    barCount: 10000,
    trackWidthPx: 200,
  });
  // Proportional width here is under half a pixel.
  assert.equal(metrics.thumbWidthPx, CHART_SCROLLBAR_MIN_THUMB_PX);
});

test("metrics degrade to disabled instead of throwing on unusable input", () => {
  for (const input of [
    { logicalRange: null, barCount: 100, trackWidthPx: 200 },
    { logicalRange: { from: 10, to: 10 }, barCount: 100, trackWidthPx: 200 },
    { logicalRange: { from: Number.NaN, to: 40 }, barCount: 100, trackWidthPx: 200 },
    { logicalRange: { from: 0, to: 40 }, barCount: 100, trackWidthPx: 0 },
  ]) {
    assert.equal(chartScrollbarMetrics(input).disabled, true);
  }
});

test("dragging the thumb round-trips back to the same logical window", () => {
  const logicalRange = { from: 32, to: 81 };
  const shared = { barCount: 100, futureSlots: 3, trackWidthPx: 240 };
  const metrics = chartScrollbarMetrics({ logicalRange, ...shared });
  const restored = chartScrollbarRangeFromThumb({
    thumbLeftPx: metrics.thumbLeftPx,
    logicalRange,
    ...shared,
  });
  assertClose(restored.from, logicalRange.from, 1e-9);
  assertClose(restored.to, logicalRange.to, 1e-9);
});

test("dragging preserves span - the scrollbar scrolls, it never zooms", () => {
  const logicalRange = { from: 10, to: 60 };
  const restored = chartScrollbarRangeFromThumb({
    thumbLeftPx: 999,
    logicalRange,
    barCount: 400,
    trackWidthPx: 200,
  });
  assertClose(restored.to - restored.from, 50);
});

test("scrolling left keeps the minimum bars of tape on screen", () => {
  const clamped = clampChartScrollLogicalRange({
    logicalRange: { from: -500, to: -450 },
    barCount: 100,
  });
  assertClose(clamped.from, -(50 - CHART_SCROLLBAR_MIN_VISIBLE_BARS));
  assertClose(clamped.to, CHART_SCROLLBAR_MIN_VISIBLE_BARS);
});

test("scrolling right keeps the minimum bars of tape on screen", () => {
  const clamped = clampChartScrollLogicalRange({
    logicalRange: { from: 900, to: 950 },
    barCount: 100,
  });
  assertClose(clamped.from, 99 - CHART_SCROLLBAR_MIN_VISIBLE_BARS);
  assertClose(clamped.to, 99 - CHART_SCROLLBAR_MIN_VISIBLE_BARS + 50);
});

test("a window wider than the tape is left where it lands", () => {
  const clamped = clampChartScrollLogicalRange({
    logicalRange: { from: 0, to: 500 },
    barCount: 10,
  });
  assertClose(clamped.to - clamped.from, 500);
});

test("wheel pan converts pixels to bars through the chart's own bar spacing", () => {
  const panned = chartWheelPanLogicalRange({
    logicalRange: { from: 0, to: 50 },
    deltaPx: 100,
    barSpacingPx: 10,
    barCount: 1000,
  });
  assertClose(panned.from, 10);
  assertClose(panned.to, 60);
});

test("wheel pan at the same pixel delta moves further when zoomed in", () => {
  const shared = { logicalRange: { from: 100, to: 150 }, deltaPx: 60, barCount: 1000 };
  const zoomedOut = chartWheelPanLogicalRange({ ...shared, barSpacingPx: 12 });
  const zoomedIn = chartWheelPanLogicalRange({ ...shared, barSpacingPx: 3 });
  assertClose(zoomedOut.from, 105);
  assertClose(zoomedIn.from, 120);
});

test("wheel pan ignores a zero or unusable delta", () => {
  const shared = { logicalRange: { from: 0, to: 50 }, barSpacingPx: 10, barCount: 100 };
  assert.equal(chartWheelPanLogicalRange({ ...shared, deltaPx: 0 }), null);
  assert.equal(chartWheelPanLogicalRange({ ...shared, deltaPx: Number.NaN }), null);
});

test("wheel pan falls back to a sane spacing when the chart reports none", () => {
  const panned = chartWheelPanLogicalRange({
    logicalRange: { from: 0, to: 50 },
    deltaPx: 60,
    barSpacingPx: 0,
    barCount: 1000,
  });
  assert.ok(panned && panned.from > 0, "expected a forward pan rather than a divide-by-zero");
});

test("a track click pages just under a full screen in either direction", () => {
  const forward = chartScrollbarPageStep({
    logicalRange: { from: 0, to: 50 },
    direction: 1,
    barCount: 1000,
  });
  assertClose(forward.from, 45);
  const back = chartScrollbarPageStep({
    logicalRange: { from: 100, to: 150 },
    direction: -1,
    barCount: 1000,
  });
  assertClose(back.from, 55);
});

test("arrow steps nudge whole bars and respect the clamp", () => {
  const stepped = chartScrollbarArrowStep({
    logicalRange: { from: 10, to: 60 },
    direction: 1,
    bars: 3,
    barCount: 1000,
  });
  assertClose(stepped.from, 13);
  const pinned = chartScrollbarArrowStep({
    logicalRange: { from: 90, to: 140 },
    direction: 1,
    bars: 50,
    barCount: 100,
  });
  assertClose(pinned.from, 99 - CHART_SCROLLBAR_MIN_VISIBLE_BARS);
});

test("jumping to the live edge keeps the current span", () => {
  const live = chartScrollbarLiveEdgeRange({
    logicalRange: { from: 0, to: 50 },
    barCount: 100,
    futureSlots: 2,
  });
  assertClose(live.to, 101);
  assertClose(live.to - live.from, 50);
});

// --- Scrubber geometry: cases the suite above left to the track-range helper ---

test("a window zoomed out WIDER than the tape still paints a thumb inside its rail", () => {
  // Distinct from "fills the whole tape": here the window overhangs the tape at
  // both ends, so the track has to grow to contain it. The thumb must stay
  // inside the rail rather than render wider than the track it lives in.
  const metrics = chartScrollbarMetrics({
    logicalRange: { from: -60, to: 220 },
    barCount: 100,
    futureSlots: 2,
    trackWidthPx: 200,
  });
  assert.equal(metrics.disabled, true);
  assert.equal(metrics.thumbWidthPx, 200);
  assert.ok(metrics.thumbLeftPx >= 0);
  assert.ok(metrics.thumbLeftPx + metrics.thumbWidthPx <= 200 + 1e-9);
});

test("thumb never leaves the rail at either history edge", () => {
  const shared = { barCount: 100, futureSlots: 3, trackWidthPx: 240 };
  for (const logicalRange of [{ from: 0, to: 30 }, { from: 72, to: 102 }]) {
    const metrics = chartScrollbarMetrics({ logicalRange, ...shared });
    assert.ok(metrics.thumbLeftPx >= 0, `left edge for ${logicalRange.from}`);
    assert.ok(
      metrics.thumbLeftPx + metrics.thumbWidthPx <= 240 + 1e-9,
      `right edge for ${logicalRange.from}`,
    );
  }
});

test("dragging past the LEFT edge preserves the span exactly", () => {
  // The right-overshoot twin is covered above. A drag that undershoots must
  // clamp the position without ever narrowing the window - clamping `from`
  // alone would silently zoom the chart in at the oldest bar.
  const logicalRange = { from: 10, to: 60 };
  const restored = chartScrollbarRangeFromThumb({
    thumbLeftPx: -400,
    logicalRange,
    barCount: 400,
    trackWidthPx: 200,
  });
  assertClose(restored.to - restored.from, 50);
  assertClose(restored.from, 0);
});
