import test from "node:test";
import assert from "node:assert/strict";

import {
  CHART_PANE_SIZING_VERSION,
  chartAnchorTranslation,
  chartBodyDragLogicalRange,
  chartBodyDragPriceRange,
  chartCandleLogicalWindow,
  chartDefaultHistorySlots,
  chartIndicatorProfileStorageKey,
  chartDefaultFutureSlots,
  chartLayoutAutosaveContextMatches,
  chartLayoutProfileStorageKey,
  chartOpeningHistorySignature,
  chartZoomOutMaximumHalfRange,
  clampExpandedPriceRangeToCandles,
  defaultChartPaneFactors,
  paneFactorsMateriallyDiffer,
  priceRangeNeedsUpdate,
  readStoredChartPaneFactors,
  restoreChartPaneFactors,
  sanitizeChartLayoutVisibleSpan,
  storeChartPaneFactors,
  workspaceCompanionWidthAtPointer,
} from "./chartViewport.js";

test("keeps the default 5-minute projection compact on every chart mode", () => {
  assert.equal(chartDefaultFutureSlots({ historySlots: 72 }), 22);
  assert.equal(chartDefaultFutureSlots({ historySlots: 32, isBigScreen: true }), 29);
  assert.equal(chartDefaultFutureSlots({ historySlots: 1 }), 8);
});

test("opens 4H with a wider calendar view than the intraday panes", () => {
  assert.equal(chartDefaultHistorySlots({ timeframeMinutes: 5 }), 72);
  assert.equal(chartDefaultHistorySlots({ timeframeMinutes: 240 }), 160);
  assert.equal(chartDefaultHistorySlots({ timeframeMinutes: 5, isBigScreen: true }), 32);
  assert.equal(chartDefaultHistorySlots({ timeframeMinutes: 240, isBigScreen: true }), 120);
});

test("frames higher-timeframe candles by candle timestamps, not denser study indexes", () => {
  const bars = Array.from({ length: 100 }, (_, index) => ({ time: (index + 1) * 14_400 }));
  const logicalIndex = (time) => (Number(time) / 1_800) - 1;
  const range = chartCandleLogicalWindow({
    bars,
    historySlots: 72,
    futureSlots: 22,
    timeToIndex: logicalIndex,
  });

  assert.deepEqual(range, { from: 231, to: 821 });
  const visibleCandles = bars.filter((bar) => {
    const index = logicalIndex(bar.time);
    return index >= range.from && index <= range.to;
  });
  assert.equal(visibleCandles.length, 72);
});

test("uses the same candle-count framing for every selectable timeframe", () => {
  for (const minutes of [3, 5, 10, 15, 30, 60, 120, 240, 1440, 10080, 43200]) {
    const bars = Array.from({ length: 90 }, (_, index) => ({ time: (index + 1) * minutes * 60 }));
    const logicalIndex = (time) => Number(time) / 300;
    const range = chartCandleLogicalWindow({
      bars,
      historySlots: 72,
      futureSlots: 22,
      timeToIndex: logicalIndex,
    });
    const visible = bars.filter((bar) => {
      const index = logicalIndex(bar.time);
      return index >= range.from && index <= range.to;
    });
    assert.equal(visible.length, 72, `${minutes}m should show 72 candles`);
  }
});

test("does not treat an appended live candle as a new opening viewport", () => {
  const first = [{ time: 100 }, { time: 200 }];
  const live = [...first, { time: 300 }];
  assert.equal(chartOpeningHistorySignature("amzn", first), chartOpeningHistorySignature("AMZN", live));
});

test("does reframe when older history arrives or the ticker changes", () => {
  const initial = [{ time: 200 }, { time: 300 }];
  const backfilled = [{ time: 100 }, ...initial];
  assert.notEqual(chartOpeningHistorySignature("AMZN", initial), chartOpeningHistorySignature("AMZN", backfilled));
  assert.notEqual(chartOpeningHistorySignature("AMZN", initial), chartOpeningHistorySignature("AAPL", initial));
});

test("keeps candles dominant when walls and signals inflate the scale", () => {
  // NVDA-like day: candles span $3 but walls/signals demanded a $29 window.
  const clamped = clampExpandedPriceRangeToCandles({
    candleLow: 219,
    candleHigh: 222,
    low: 209,
    high: 238,
  });
  assert.ok(clamped.high - clamped.low <= 3 * 3 + 1e-9);
  assert.ok(clamped.low <= 219 && clamped.high >= 222);
  // The clamp preserves the expansion's above/below proportions.
  const belowShare = (219 - clamped.low) / (clamped.high - clamped.low - 3);
  const aboveShare = (clamped.high - 222) / (clamped.high - clamped.low - 3);
  assert.ok(Math.abs(belowShare / (belowShare + aboveShare) - 10 / 26) < 0.01);
});

test("leaves modest wall expansion untouched and spans the candles", () => {
  const modest = clampExpandedPriceRangeToCandles({
    candleLow: 100,
    candleHigh: 104,
    low: 98,
    high: 106,
  });
  assert.deepEqual(modest, { low: 98, high: 106 });
  const outside = clampExpandedPriceRangeToCandles({
    candleLow: 100,
    candleHigh: 104,
    low: 101,
    high: 103,
  });
  assert.deepEqual(outside, { low: 100, high: 104 });
  assert.deepEqual(
    clampExpandedPriceRangeToCandles({ candleLow: Number.NaN, candleHigh: 104, low: 98, high: 106 }),
    { low: 98, high: 106 },
  );
});

test("skips effectively identical price-scale writes during drag", () => {
  assert.equal(priceRangeNeedsUpdate({ from: 90, to: 110 }, { from: 90.001, to: 110.001 }), false);
  assert.equal(priceRangeNeedsUpdate({ from: 90, to: 110 }, { from: 91, to: 111 }), true);
  assert.equal(priceRangeNeedsUpdate(null, { from: 90, to: 110 }), true);
});

test("converts a chart-body mouse drag into horizontal logical movement only", () => {
  assert.deepEqual(
    chartBodyDragLogicalRange(
      { from: 20, to: 120 },
      { startX: 600, currentX: 400, plotWidth: 1000 },
    ),
    { from: 40, to: 140 },
  );
  assert.deepEqual(
    chartBodyDragLogicalRange(
      { from: 20, to: 120 },
      { startX: 400, currentX: 600, plotWidth: 1000 },
    ),
    { from: 0, to: 100 },
  );
  assert.equal(chartBodyDragLogicalRange({ from: 20, to: 120 }, { startX: 1, currentX: 2, plotWidth: 0 }), null);
});

test("keeps a fast chart-body pan from stranding every candle off-screen", () => {
  assert.deepEqual(
    chartBodyDragLogicalRange(
      { from: 80, to: 180 },
      {
        startX: 500,
        currentX: 0,
        plotWidth: 500,
        firstCandleIndex: 0,
        latestCandleIndex: 100,
        minimumVisibleCandles: 20,
      },
    ),
    { from: 81, to: 181 },
  );
  assert.deepEqual(
    chartBodyDragLogicalRange(
      { from: 0, to: 100 },
      {
        startX: 0,
        currentX: 1_000,
        plotWidth: 500,
        firstCandleIndex: 0,
        latestCandleIndex: 100,
        minimumVisibleCandles: 20,
      },
    ),
    { from: -81, to: 19 },
  );
});

test("converts a chart-body mouse drag into vertical price movement", () => {
  // Dragging down (cursor y grows) slides the plot down: range moves up.
  assert.deepEqual(
    chartBodyDragPriceRange(
      { from: 90, to: 110 },
      { startY: 200, currentY: 300, plotHeight: 500 },
    ),
    { from: 94, to: 114 },
  );
  assert.deepEqual(
    chartBodyDragPriceRange(
      { from: 90, to: 110 },
      { startY: 300, currentY: 200, plotHeight: 500 },
    ),
    { from: 86, to: 106 },
  );
  assert.equal(chartBodyDragPriceRange({ from: 90, to: 110 }, { startY: 1, currentY: 2, plotHeight: 0 }), null);
  assert.equal(chartBodyDragPriceRange({ from: 110, to: 90 }, { startY: 1, currentY: 2, plotHeight: 500 }), null);
});

test("moves price-anchored overlays by the chart's exact coordinate delta", () => {
  assert.deepEqual(
    chartAnchorTranslation(
      { x: 400.5, y: 220.25 },
      { x: 345.25, y: 286.75 },
    ),
    { x: -55.25, y: 66.5 },
  );
  assert.equal(chartAnchorTranslation({ x: 1, y: 2 }, { x: null, y: 3 }), null);
});

test("shares saved chart and indicator profiles across every ticker", () => {
  assert.equal(chartLayoutProfileStorageKey(false, "5m"), "standard:5m");
  assert.equal(chartLayoutProfileStorageKey(true, "5m"), "fullscreen:5m");
  assert.equal(
    chartLayoutProfileStorageKey(false, "5m", "workspace:quad:panel-0"),
    "standard:workspace:quad:panel-0:5m",
  );
  assert.equal(
    chartLayoutProfileStorageKey(true, "5m", "workspace:quad:panel-0"),
    "fullscreen:workspace:quad:panel-0:5m",
  );
  assert.equal(chartIndicatorProfileStorageKey("5m"), "5m");
  assert.notEqual(chartLayoutProfileStorageKey(false, "5m"), chartLayoutProfileStorageKey(true, "5m"));
  assert.notEqual(
    chartLayoutProfileStorageKey(true, "5m", "workspace:quad:panel-0"),
    chartLayoutProfileStorageKey(true, "5m", "workspace:quad:panel-1"),
  );
});

test("uses equal lower study panes on normal and big-screen charts", () => {
  assert.deepEqual(defaultChartPaneFactors(false), [6, 1.2, 1.2]);
  assert.deepEqual(defaultChartPaneFactors(true), [7.5, 1.1, 1.1]);
});

test("migrates unequal legacy lower panes while retaining later manual sizing", () => {
  assert.deepEqual(restoreChartPaneFactors({
    paneFactors: [6, 1.35, 1.1],
    isBigScreen: false,
  }), [6, 1.225, 1.225]);
  assert.deepEqual(restoreChartPaneFactors({
    paneFactors: [7.5, 1.3, 0.9],
    isBigScreen: true,
  }), [7.5, 1.1, 1.1]);
  assert.deepEqual(restoreChartPaneFactors({
    paneFactors: [6, 1.45, 0.95],
    paneSizingVersion: CHART_PANE_SIZING_VERSION,
  }), [6, 1.45, 0.95]);
});

test("bounds zoom-out by selected candles while retaining future projection room", () => {
  const fourHourHalfRange = chartZoomOutMaximumHalfRange({
    candleCount: 24,
    futureSlots: 54,
  });
  assert.equal(fourHourHalfRange, 97.5);
  assert.ok(fourHourHalfRange < 4500 * 1.5);
  assert.equal(chartZoomOutMaximumHalfRange({ candleCount: 4, futureSlots: 0 }), 24);
});

test("sanitizes an oversized saved 4H viewport against current chart capacity", () => {
  assert.equal(sanitizeChartLayoutVisibleSpan({
    visibleSpan: 6750,
    candleCount: 24,
    futureSlots: 54,
  }), 195);
  assert.equal(sanitizeChartLayoutVisibleSpan({
    visibleSpan: 80,
    candleCount: 24,
    futureSlots: 54,
  }), 80);
  assert.equal(sanitizeChartLayoutVisibleSpan({
    visibleSpan: 4,
    candleCount: 24,
    futureSlots: 54,
  }), null);
});

test("rejects delayed layout autosaves after any profile-context boundary", () => {
  const queued = chartLayoutProfileStorageKey(false, "5m", "workspace:quad:panel-0");
  assert.equal(chartLayoutAutosaveContextMatches(queued, queued), true);
  assert.equal(
    chartLayoutAutosaveContextMatches(
      queued,
      chartLayoutProfileStorageKey(false, "4h", "workspace:quad:panel-0"),
    ),
    false,
  );
  assert.equal(
    chartLayoutAutosaveContextMatches(
      queued,
      chartLayoutProfileStorageKey(true, "5m", "workspace:quad:panel-0"),
    ),
    false,
  );
  assert.equal(
    chartLayoutAutosaveContextMatches(
      queued,
      chartLayoutProfileStorageKey(false, "5m", "workspace:quad:panel-1"),
    ),
    false,
  );
});

test("resizes the big-screen option chain while preserving usable chart space", () => {
  assert.equal(workspaceCompanionWidthAtPointer({
    containerLeft: 100,
    containerWidth: 1600,
    pointerX: 1200,
  }), 500);
  assert.equal(workspaceCompanionWidthAtPointer({
    containerLeft: 100,
    containerWidth: 1600,
    pointerX: 1600,
  }), 340);
  assert.equal(workspaceCompanionWidthAtPointer({
    containerLeft: 100,
    containerWidth: 1600,
    pointerX: 200,
  }), 1170);
});

test("storeChartPaneFactors round-trips through readStoredChartPaneFactors", () => {
  const memory = new Map();
  const storage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, value),
  };
  assert.equal(storeChartPaneFactors(storage, "standard:5m", [6.4, 1.1, 1.05]), true);
  const stored = readStoredChartPaneFactors(storage, "standard:5m");
  assert.deepEqual(stored.paneFactors, [6.4, 1.1, 1.05]);
  assert.equal(stored.paneSizingVersion, CHART_PANE_SIZING_VERSION);
  assert.equal(readStoredChartPaneFactors(storage, "fullscreen:5m"), null);
});

test("storeChartPaneFactors rejects invalid input without writing", () => {
  const memory = new Map();
  const storage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, value),
  };
  assert.equal(storeChartPaneFactors(storage, "", [6, 1, 1]), false);
  assert.equal(storeChartPaneFactors(storage, "standard:5m", []), false);
  assert.equal(storeChartPaneFactors(storage, "standard:5m", [6, Number.NaN, 1]), false);
  assert.equal(storeChartPaneFactors(null, "standard:5m", [6, 1, 1]), false);
  assert.equal(memory.size, 0);
});

test("readStoredChartPaneFactors survives corrupted storage JSON", () => {
  const storage = { getItem: () => "{not json", setItem: () => {} };
  assert.equal(readStoredChartPaneFactors(storage, "standard:5m"), null);
});

test("paneFactorsMateriallyDiffer uses a relative tolerance", () => {
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2, 1.2], [6, 1.2, 1.2]), false);
  assert.equal(paneFactorsMateriallyDiffer([6.001, 1.2, 1.2], [6, 1.2, 1.2]), false);
  assert.equal(paneFactorsMateriallyDiffer([6.5, 1.2, 1.2], [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2], [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer(null, [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2, 1.2], null), true);
});

test("collapsed time scale is rejected instead of framing four giant candles", () => {
  // The 4H regression of 2026-08-10: a full 1,099-bar series, but the time
  // scale did not yet hold the older candles, so timeToIndex(findNearest)
  // clamped "120 candles ago" to a point beside the latest one. The chart
  // opened showing ~4 enormous candles on every ticker.
  const bars = Array.from({ length: 1_099 }, (_, i) => ({ time: 1_000 + i * 14_400, close: i }));
  const latestTime = bars.at(-1).time;

  const collapsed = chartCandleLogicalWindow({
    bars,
    historySlots: 120,
    futureSlots: 30,
    // Everything older than the last few bars clamps to index 900.
    timeToIndex: (time) => (time >= latestTime - 3 * 14_400 ? 903 : 900),
  });
  assert.equal(collapsed, null, "a collapsed scale must fall back, not frame 3 candles");

  const nearlyCollapsed = chartCandleLogicalWindow({
    bars,
    historySlots: 120,
    futureSlots: 30,
    // The real MSFT failure resolved 131 requested candles to 13 slots. It is
    // above the old absolute floor but still visibly a collapsed viewport.
    timeToIndex: (time) => (time >= latestTime - 13 * 14_400 ? 913 : 900),
  });
  assert.equal(nearlyCollapsed, null, "a near-collapsed scale must also fall back");

  // A healthy scale still returns a window spanning the requested history.
  const healthy = chartCandleLogicalWindow({
    bars,
    historySlots: 120,
    futureSlots: 30,
    timeToIndex: (time) => Math.round((time - 1_000) / 14_400),
  });
  assert.ok(healthy, "a healthy scale must still produce a window");
  assert.equal(healthy.to - healthy.from, 120 - 1 + 30);
});

test("denser study points on the shared scale still widen the window", () => {
  const bars = Array.from({ length: 300 }, (_, i) => ({ time: 1_000 + i * 14_400, close: i }));
  // 30-minute study points make each 4H candle 8 logical indexes apart.
  const window = chartCandleLogicalWindow({
    bars,
    historySlots: 120,
    futureSlots: 10,
    timeToIndex: (time) => Math.round((time - 1_000) / 1_800),
  });
  assert.ok(window.to - window.from > 120, "denser scales legitimately span more indexes");
});

test("a wider pane shows more candles, not fatter ones", () => {
  // The fixed counts made a big screen show FEWER candles than a normal one,
  // so giving the chart more room made it look more zoomed in.
  const narrow = chartDefaultHistorySlots({ timeframeMinutes: 240, chartWidth: 700 });
  const wide = chartDefaultHistorySlots({ timeframeMinutes: 240, chartWidth: 1_920 });
  assert.ok(wide > narrow, `wide (${wide}) must exceed narrow (${narrow})`);

  // Candles land near the TradingView pitch rather than the 16px ceiling.
  for (const width of [700, 1_280, 1_920, 2_560]) {
    const history = chartDefaultHistorySlots({ timeframeMinutes: 240, chartWidth: width });
    const future = chartDefaultFutureSlots({ historySlots: history });
    const pitch = width / (history + future);
    assert.ok(pitch >= 4 && pitch <= 9, `${width}px gave a ${pitch.toFixed(1)}px candle pitch`);
  }
});

test("big screen no longer shows fewer candles than a normal one", () => {
  const width = 1_920;
  const normal = chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: width });
  const big = chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: width, isBigScreen: true });
  assert.equal(big, normal, "at equal width the profile must not shrink the view");
});

test("width-less callers still get a usable fallback count", () => {
  // Deliberately property-based: the fallback constants are tuned by hand,
  // so pin the behaviour (a usable count, with 4H at least as wide as the
  // intraday panes) rather than the numbers.
  const intraday = chartDefaultHistorySlots({ timeframeMinutes: 5 });
  const fourHour = chartDefaultHistorySlots({ timeframeMinutes: 240 });
  for (const slots of [intraday, fourHour]) {
    assert.ok(Number.isFinite(slots) && slots >= 30, `unusable fallback: ${slots}`);
  }
  assert.ok(fourHour >= intraday, "4H must not open tighter than the intraday panes");
});
