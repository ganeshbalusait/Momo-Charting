import test from "node:test";
import assert from "node:assert/strict";

import {
  CHART_PANE_SIZING_VERSION,
  chartAnchorTranslation,
  chartBodyDragLogicalRange,
  chartBodyDragPriceRange,
  chartCandleLogicalWindow,
  chartHostContentBoxSize,
  chartHostMinimumWidth,
  chartLowerStudyHeaderOffsets,
  chartPaneOverlayTop,
  chartTimeViewportRange,
  clearChartTimeViewport,
  chartDefaultHistorySlots,
  chartIndicatorProfileStorageKey,
  chartDefaultFutureSlots,
  TRADINGVIEW_MAX_BAR_SPACING_PX,
  TRADINGVIEW_MIN_BAR_SPACING_PX,
  chartLayoutAutosaveContextMatches,
  chartLayoutProfileStorageKey,
  chartLogicalSlotsPerCandle,
  chartLogicalTimeScaleSpacing,
  chartMinimumLogicalBarSpacing,
  chartExplicitSavedLogicalRange,
  chartOpeningHistorySignature,
  chartShouldFrameAutomaticViewport,
  chartTrailingSessionHistorySlots,
  chartZoomOutMaximumHalfRange,
  chartZoomLogicalRange,
  chartPanStepLogicalRange,
  chartLiveEdgeFutureSlots,
  chartPanForwardLastIndex,
  chartTimeViewportToPersist,
  chartPanBoundsCacheUsable,
  chartWholeBarLogicalRange,
  CHART_PAN_STEP_FRACTION,
  CHART_LAYOUT_VIEWPORT_VERSION,
  clampExpandedPriceRangeToCandles,
  defaultChartPaneFactors,
  paneFactorsMateriallyDiffer,
  priceRangeNeedsUpdate,
  readChartTimeViewport,
  readStoredChartPaneFactors,
  restoreChartPaneFactors,
  sanitizeChartLayoutVisibleSpan,
  storeChartTimeViewport,
  storeChartPaneFactors,
  workspaceCompanionWidthAtPointer,
} from "./chartViewport.js";
import { chartZoomMemoryFromLogicalRange, chartZoomMemoryLogicalRange } from "./chartViewport.js";

test("a measurable host never forces the chart wider than its own content box", () => {
  // "6 across" desktop column: 308px box with 10px padding each side. The old
  // 320px floor produced a 320px canvas whose right price scale (OI strike
  // labels) was clipped off the panel.
  const dense = chartHostMinimumWidth({ clientWidth: 308, paddingLeft: "10px", paddingRight: "10px" });
  assert.equal(dense, 288);
  assert.equal(chartHostContentBoxSize({
    clientWidth: 308,
    clientHeight: 600,
    paddingLeft: "10px",
    paddingRight: "10px",
    minimumWidth: dense,
  }).width, 288);
  // A wide host keeps the historical floor as a no-op minimum.
  assert.equal(chartHostMinimumWidth({ clientWidth: 1400, paddingLeft: 10, paddingRight: 10 }), 320);
  // Hidden tab / first mount (no measurable width) keeps the safety floor.
  assert.equal(chartHostMinimumWidth({ clientWidth: 0 }), 320);
  assert.equal(chartHostMinimumWidth({ clientWidth: "auto" }), 320);
  // Phone panel: 320px viewport minus shell gutters -> the plot box, not 320.
  assert.equal(chartHostMinimumWidth({ clientWidth: 292, paddingLeft: 10, paddingRight: 10 }), 272);
});

test("sizes the native chart from the padded host content box without resize feedback", () => {
  assert.deepEqual(chartHostContentBoxSize({
    clientWidth: 390,
    clientHeight: 520,
    paddingLeft: "10px",
    paddingRight: "10px",
    paddingTop: "8px",
    paddingBottom: "10px",
  }), { width: 370, height: 502 });

  // A second pass receives the same host size. It must not add the padding
  // back and grow to 410px, which was the phone chart's runaway loop.
  assert.equal(chartHostContentBoxSize({
    clientWidth: 390,
    clientHeight: 520,
    paddingLeft: 10,
    paddingRight: 10,
  }).width, 370);
  assert.equal(chartHostContentBoxSize({
    clientWidth: 272,
    clientHeight: 260,
    minimumWidth: 272,
  }).width, 272);
  assert.deepEqual(chartHostContentBoxSize({ clientWidth: 0, clientHeight: 0 }), {
    width: 320,
    height: 240,
  });
});

test("anchors lower-study captions to native pane tops across chart resizes", () => {
  assert.equal(chartPaneOverlayTop({ chartTop: 256, paneTop: 425, fallbackTop: 999, inset: 1 }), 170);
  assert.equal(chartPaneOverlayTop({ chartTop: 120, paneTop: 458, fallbackTop: 999, inset: 8 }), 346);
  assert.equal(chartPaneOverlayTop({ chartTop: undefined, paneTop: undefined, fallbackTop: 210 }), 210);
  assert.equal(chartPaneOverlayTop({ chartTop: undefined, paneTop: undefined }), null);
});

test("fits all lower-study headers inside a compact phone pane", () => {
  assert.deepEqual(chartLowerStudyHeaderOffsets({
    isPhone: true,
    hasAdx: true,
    hasCloudLabels: true,
  }), { cloudTop: 9, squeezeTop: 21 });
  assert.deepEqual(chartLowerStudyHeaderOffsets({
    hasAdx: true,
    hasCloudLabels: true,
  }), { cloudTop: 18, squeezeTop: 40 });
  assert.deepEqual(chartLowerStudyHeaderOffsets({
    isPhone: true,
    hasCloudLabels: true,
  }), { cloudTop: 0, squeezeTop: 12 });
});

test("keeps the default projection compact on every chart mode", () => {
  // Every projection slot is empty space - a candle not shown. The
  // big-screen branch used history * 0.9 capped at 32, so a ~600px pane
  // spent a THIRD of its width on blank canvas and read as zoomed in.
  // Assert the property (small, bounded, same in both modes) rather than
  // exact numbers.
  for (const history of [32, 72, 150, 400]) {
    for (const isBigScreen of [false, true]) {
      const future = chartDefaultFutureSlots({ historySlots: history, isBigScreen });
      assert.ok(future >= 5, `projection ${future} too small to hold session lines`);
      assert.ok(future <= 10, `projection ${future} exceeds the TradingView-style limit`);
      assert.ok(
        future / (history + future) <= 0.25,
        `projection is ${Math.round(100 * future / (history + future))}% of the view`,
      );
    }
  }
  // A tiny history still gets a usable forward edge.
  assert.ok(chartDefaultFutureSlots({ historySlots: 1 }) >= 5);
});

test("intraday fills the pane; higher timeframes open on ~40 fat candles", () => {
  // Grounded in TWO of the user's TradingView charts:
  //   AMZN 5m ~1460px -> ~105 candles filling the pane
  //   AMZN 4h ~1460px -> ~38 fat candles in the left ~45%, big forward space
  // So the opening view is NOT uniform: higher timeframes open fewer + fatter.
  for (const minutes of [3, 5, 15, 60]) {
    const h = chartDefaultHistorySlots({ timeframeMinutes: minutes, chartWidth: 1_460 });
    assert.ok(h >= 90 && h <= 140, `${minutes}m opened on ${h} candles`);
  }
  for (const minutes of [240, 1_440, 10_080]) {
    const h = chartDefaultHistorySlots({ timeframeMinutes: minutes, chartWidth: 1_460 });
    assert.equal(h, 40, `${minutes}m must open on the ~40-candle higher-timeframe view`);
    const f = chartDefaultFutureSlots({ historySlots: h, timeframeMinutes: minutes });
    assert.ok(f >= 12, `${minutes}m must keep generous forward space, got ${f}`);
  }
});

test("selects exactly the latest five chart sessions for the 4H opening view", () => {
  const bars = [
    ...Array.from({ length: 4 }, (_, index) => ({ session: "2026-08-05", time: index })),
    ...Array.from({ length: 3 }, (_, index) => ({ session: "2026-08-06", time: 10 + index })),
    ...Array.from({ length: 5 }, (_, index) => ({ session: "2026-08-07", time: 20 + index })),
    ...Array.from({ length: 4 }, (_, index) => ({ session: "2026-08-10", time: 30 + index })),
    ...Array.from({ length: 3 }, (_, index) => ({ session: "2026-08-11", time: 40 + index })),
    ...Array.from({ length: 4 }, (_, index) => ({ session: "2026-08-12", time: 50 + index })),
  ];

  assert.equal(chartTrailingSessionHistorySlots({
    bars,
    sessionCount: 5,
    sessionKey: (bar) => bar.session,
  }), 19);
  assert.equal(chartTrailingSessionHistorySlots({ bars: [], sessionKey: () => "day" }), 0);
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

  assert.deepEqual(range, { from: 231, to: 975 });
  const visibleCandles = bars.filter((bar) => {
    const index = logicalIndex(bar.time);
    return index >= range.from && index <= range.to;
  });
  assert.equal(visibleCandles.length, 72);
});

test("converts a 4H candle pitch into shared 30-minute logical spacing", () => {
  const bars = Array.from({ length: 20 }, (_, index) => ({ time: (index + 1) * 14_400 }));
  const spacing = chartLogicalTimeScaleSpacing({
    candlePitch: 4,
    futureSlots: 10,
    bars,
    timeToIndex: (time) => Number(time) / 1_800,
  });

  assert.deepEqual(spacing, {
    barSpacing: 0.5,
    rightOffset: 80,
    minBarSpacing: 0.25,
    slotsPerCandle: 8,
  });
});

test("keeps 5-10 right-side candle widths on a denser shared study scale", () => {
  const bars = Array.from({ length: 100 }, (_, index) => ({ time: (index + 1) * 14_400 }));
  const timeToIndex = (time) => (Number(time) / 1_800) - 1;
  assert.equal(chartLogicalSlotsPerCandle({ bars, timeToIndex }), 8);
  const range = chartCandleLogicalWindow({ bars, historySlots: 100, futureSlots: 8, timeToIndex });
  const latest = timeToIndex(bars.at(-1).time);
  assert.equal(range.to - latest, 64, "eight future 4H bars need 8 x 8 logical slots");
});

test("scales the logical spacing floor down for denser higher-timeframe studies", () => {
  const bars = Array.from({ length: 100 }, (_, index) => ({ time: (index + 1) * 14_400 }));
  const timeToIndex = (time) => (Number(time) / 1_800) - 1;
  assert.equal(chartLogicalSlotsPerCandle({ bars, timeToIndex }), 8);
  assert.equal(chartMinimumLogicalBarSpacing({ bars, timeToIndex }), 0.25);
  assert.equal(
    chartMinimumLogicalBarSpacing({ bars, timeToIndex: (time) => Number(time) / 14_400 }),
    TRADINGVIEW_MIN_BAR_SPACING_PX,
  );
});

test("restores only the explicitly saved logical zoom at the current live edge", () => {
  assert.deepEqual(chartExplicitSavedLogicalRange({
    latestIndex: 800,
    visibleSpan: 200,
    latestRatio: 0.8,
    candleCount: 207,
    slotsPerCandle: 8,
    futureSlots: 5,
  }), { from: 640, to: 840 });
  assert.equal(chartExplicitSavedLogicalRange({ latestIndex: 10, visibleSpan: 0, candleCount: 20 }), null);
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

test("a live quote stage restart never resets the current zoom", () => {
  assert.equal(chartShouldFrameAutomaticViewport({
    followsLatest: true,
    studyStage: 0,
    previousStudyStage: 20,
  }), false);
  assert.equal(chartShouldFrameAutomaticViewport({
    followsLatest: true,
    studyStage: 8,
    previousStudyStage: 7,
  }), false, "a genuinely new indicator stage must not repaint the opening time window");
  assert.equal(chartShouldFrameAutomaticViewport({
    initialView: true,
    manualNavigation: true,
  }), false, "manual pan or zoom always wins");
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
  // Not pinned to a literal: this version is bumped deliberately to retire
  // saved viewports captured under superseded framing rules, and a hard
  // assertion here only adds friction to a bump that is the whole point.
  assert.ok(Number.isInteger(CHART_LAYOUT_VIEWPORT_VERSION) && CHART_LAYOUT_VIEWPORT_VERSION >= 1);
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

test("zoom-out expands toward history while keeping the live edge visible", () => {
  const next = chartZoomLogicalRange({
    logicalRange: { from: 1_035, to: 1_126 },
    direction: "out",
    firstCandleIndex: 0,
    latestCandleIndex: 1_099,
    futureSlots: 27,
  });
  assert.equal(next.to, 1_126, "zoom must not create more blank future space");
  assert.ok(next.from < 1_035, "the added width must reveal older candles");
  assert.equal(next.to - next.from, 182);
});

test("repeated 4H zoom-out stops at the complete logical candle tape", () => {
  let range = { from: 8_100, to: 8_932 };
  for (let index = 0; index < 8; index += 1) {
    range = chartZoomLogicalRange({
      logicalRange: range,
      direction: "out",
      firstCandleIndex: 100,
      latestCandleIndex: 8_900,
      futureSlots: 32,
    });
  }
  assert.deepEqual(range, { from: 100, to: 8_932 });
});

test("zoom-in keeps the current chart center", () => {
  const next = chartZoomLogicalRange({
    logicalRange: { from: 100, to: 300 },
    direction: "in",
  });
  assert.equal(next.to - next.from, 136);
  assert.equal((next.from + next.to) / 2, 200);
});

// At the live edge the toolbar zoom-in behaves like TradingView: the newest
// candle stays where it is and the older candles leave on the left. The centre
// zoom moved the right edge left by a sixth of the view on every click, so one
// click from the opening view pushed the newest candle's breathing room off
// screen and lit the "pan forward" arrow although the trader never left the
// live edge (charts-core-navigation, 2026-11-14).
test("zoom-in at the live edge keeps the right edge and trims older candles", () => {
  // Measured on the real AAPL 5m chart: the opening view is [134, 207] with
  // the newest candle at 207. The centre zoom returned [145.68, 195.32], which
  // hid the 12 newest candles after ONE click.
  const opening = chartZoomLogicalRange({
    logicalRange: { from: 134, to: 207 },
    direction: "in",
    firstCandleIndex: 0,
    latestCandleIndex: 207,
    futureSlots: 6,
  });
  assert.equal(opening.to, 207, "the newest candle must stay at the right edge");
  assert.ok(Math.abs((opening.to - opening.from) - 73 * 0.68) < 1e-9);
  // Parked by Return to latest, six blank slots ahead of the newest candle.
  const next = chartZoomLogicalRange({
    logicalRange: { from: 231, to: 340 },
    direction: "in",
    firstCandleIndex: 127,
    latestCandleIndex: 334,
    futureSlots: 6,
  });
  assert.equal(next.to, 340, "the live edge must not move");
  assert.ok(Math.abs((next.to - next.from) - 109 * 0.68) < 1e-9);
  // Repeating it never loses the newest candle or lets blank space take over.
  let range = { from: 231, to: 340 };
  for (let click = 0; click < 12; click += 1) {
    range = chartZoomLogicalRange({
      logicalRange: range,
      direction: "in",
      firstCandleIndex: 127,
      latestCandleIndex: 334,
      futureSlots: 6,
    });
    const span = range.to - range.from;
    assert.ok(range.from < 334 && range.to >= 334, "newest candle left the view");
    assert.ok(range.to - 334 <= Math.max(2, span * 0.3) + 1e-9, "blank space above 30% of the view");
  }
  assert.equal(range.to - range.from, 8);
});

test("zoom-in at the live edge caps the blank space the way Return to latest does", () => {
  // 4H opens with generous room ahead (24 slots here). Zooming in must shrink
  // that room with the view instead of keeping all 24 slots of nothing.
  const next = chartZoomLogicalRange({
    logicalRange: { from: 60, to: 124 },
    direction: "in",
    firstCandleIndex: 0,
    latestCandleIndex: 100,
    futureSlots: 24,
  });
  const span = next.to - next.from;
  assert.ok(Math.abs(span - 64 * 0.68) < 1e-9);
  assert.ok(Math.abs(next.to - (100 + chartLiveEdgeFutureSlots({ visibleSpan: span, futureSlots: 24 }))) < 1e-9);
});

test("zoom-in away from the live edge still zooms around the centre", () => {
  // The newest candle (334) is off screen to the right: the trader is reading
  // history, so the middle of what he is looking at must stay put.
  const next = chartZoomLogicalRange({
    logicalRange: { from: 100, to: 300 },
    direction: "in",
    firstCandleIndex: 0,
    latestCandleIndex: 334,
    futureSlots: 6,
  });
  assert.equal(next.to - next.from, 136);
  assert.equal((next.from + next.to) / 2, 200);
});

test("the live-edge room is one rule shared by zoom, pan and Return to latest", () => {
  assert.equal(chartLiveEdgeFutureSlots({ visibleSpan: 109, futureSlots: 6 }), 6);
  assert.equal(chartLiveEdgeFutureSlots({ visibleSpan: 40, futureSlots: 24 }), 12);
  assert.equal(chartLiveEdgeFutureSlots({ visibleSpan: 4, futureSlots: 24 }), 2);
  assert.equal(chartLiveEdgeFutureSlots({ visibleSpan: 40, futureSlots: 0 }), 0);
});

// "Pan forward to newer candles" is lit only while newer candles are hidden to
// the right. The old bound was "newest candle + the configured projection",
// which the opening view never reaches (it opens with the newest candle AT the
// right edge), so the arrow was lit on every freshly opened chart and led only
// to blank space.
test("pan forward is finished once the newest candle is on screen", () => {
  const forward = (logicalRange) => chartPanStepLogicalRange({
    logicalRange,
    direction: "forward",
    firstIndex: 0,
    lastIndex: chartPanForwardLastIndex({ logicalRange, latestIndex: 207, futureSlots: 6 }),
  });
  // Opening view and the first toolbar zoom-in: newest candle at the edge.
  assert.equal(forward({ from: 134, to: 207 }), null);
  assert.equal(forward({ from: 157, to: 207 }), null);
  // Parked by Return to latest (six blank slots ahead).
  assert.equal(forward({ from: 163, to: 213 }), null);
  // Scrolled or dragged out past it: still nothing newer.
  assert.equal(forward({ from: 180, to: 230 }), null);
});

test("pan forward walks back to exactly where Return to latest parks the view", () => {
  // One step back from [157, 207] is a third of the view.
  let range = { from: 157 - 50 / 3, to: 207 - 50 / 3 };
  const lastIndex = chartPanForwardLastIndex({ logicalRange: range, latestIndex: 207, futureSlots: 6 });
  assert.equal(lastIndex, 213);
  range = chartPanStepLogicalRange({ logicalRange: range, direction: "forward", firstIndex: 0, lastIndex });
  assert.ok(range, "newer candles were hidden, so forward must move");
  assert.ok(Math.abs(range.to - 207) < 1e-9);
  assert.ok(Math.abs((range.to - range.from) - 50) < 1e-9, "a pan never changes the zoom");
  // A deeper view takes several steps and its last one stops at the park
  // position: newest candle plus the shared live-edge room, never further.
  range = { from: 20, to: 60 };
  for (let step = 0; step < 40; step += 1) {
    const next = chartPanStepLogicalRange({
      logicalRange: range,
      direction: "forward",
      firstIndex: 0,
      lastIndex: chartPanForwardLastIndex({ logicalRange: range, latestIndex: 207, futureSlots: 24 }),
    });
    if (!next) break;
    range = next;
  }
  assert.ok(range.to >= 207, "stopped before the newest candle");
  assert.ok(range.to <= 207 + chartLiveEdgeFutureSlots({ visibleSpan: 40, futureSlots: 24 }) + 1e-9);
  assert.ok(Math.abs((range.to - range.from) - 40) < 1e-9);
});

// The pan bounds are cached for a beat because the arrows' greyed state is
// probed on every scroll frame. The cache used to be trusted on age alone, so
// a bound taken while a timeframe switch was still loading (other bars, other
// index of the newest candle) decided the arrow for the settled chart:
// measured 2026-11-14, forward lit on a 5m view [173, 207] whose newest candle
// was 207, after a 15m -> 5m switch.
test("cached pan bounds are dropped as soon as the tape or its newest index changes", () => {
  const bars = [{ time: 60 }, { time: 120 }];
  const cache = { at: 1_000, bars, bounds: { firstIndex: 0, latestIndex: 207, futureLogicalSlots: 6 } };
  assert.equal(chartPanBoundsCacheUsable({ cache, nowMs: 1_100, bars, latestIndex: 207 }), true);
  // Too old.
  assert.equal(chartPanBoundsCacheUsable({ cache, nowMs: 1_401, bars, latestIndex: 207 }), false);
  // Another tape (timeframe or ticker switch), even with the same numbers.
  assert.equal(chartPanBoundsCacheUsable({ cache, nowMs: 1_100, bars: bars.slice(), latestIndex: 207 }), false);
  // Same tape, but the time scale now puts the newest candle elsewhere.
  assert.equal(chartPanBoundsCacheUsable({ cache, nowMs: 1_100, bars, latestIndex: 69 }), false);
  assert.equal(chartPanBoundsCacheUsable({ cache: { at: 0, bounds: null }, nowMs: 1, bars, latestIndex: 207 }), false);
  assert.equal(chartPanBoundsCacheUsable({ cache: null, nowMs: 1, bars, latestIndex: 207 }), false);
});

// THE BLANK ROOM AHEAD OF THE NEWEST CANDLE HAS NO HONEST LENGTH IN SECONDS.
// Its slots carry the times of the NEXT session's bars, so on a Friday evening
// six 5m slots "last" 56.5 hours. Measured 2026-11-14 on AAPL 5m: a 29-candle
// view parked by Return to latest was stored as spanSeconds 211800 /
// forwardSeconds 203400; the restore clamps forward to half the span, so the
// chart came back showing all 208 candles - the zoom was simply gone.
test("the remembered zoom counts candle time only, never the blank room ahead", () => {
  const latest = 1_794_617_700;            // Fri 19:55 ET
  const from = 1_794_609_300;              // 28 five-minute bars earlier
  const mondayPremarket = 1_794_821_100;   // the time carried by blank slot 6
  const stored = chartTimeViewportToPersist({
    visibleFrom: from,
    visibleTo: mondayPremarket,
    latestCandleTime: latest,
  });
  assert.deepEqual(stored, { spanSeconds: latest - from, forwardSeconds: 0 });
  // Restoring it frames the same first candle the trader was looking at...
  const restored = chartTimeViewportRange(latest, stored);
  assert.equal(restored.from, from);
  // ...and saving that restored view (it ends on the newest candle) returns
  // the same numbers: a fixed point, so nothing can creep.
  assert.deepEqual(chartTimeViewportToPersist({
    visibleFrom: restored.from,
    visibleTo: latest,
    latestCandleTime: latest,
  }), stored);
});

test("a view that ends on or before the newest candle is remembered unchanged", () => {
  assert.deepEqual(chartTimeViewportToPersist({
    visibleFrom: 1_000_000,
    visibleTo: 1_010_200,
    latestCandleTime: 1_010_200,
  }), { spanSeconds: 10_200, forwardSeconds: 0 });
  // Panned back into history: the span is what is on screen.
  assert.deepEqual(chartTimeViewportToPersist({
    visibleFrom: 1_000_000,
    visibleTo: 1_006_000,
    latestCandleTime: 1_010_200,
  }), { spanSeconds: 6_000, forwardSeconds: 0 });
  // No tape yet: nothing to measure against, keep the visible span.
  assert.deepEqual(chartTimeViewportToPersist({
    visibleFrom: 1_000_000,
    visibleTo: 1_006_000,
    latestCandleTime: 0,
  }), { spanSeconds: 6_000, forwardSeconds: 0 });
});

test("a view with no candle in it is not remembered at all", () => {
  assert.equal(chartTimeViewportToPersist({
    visibleFrom: 1_010_500,
    visibleTo: 1_020_000,
    latestCandleTime: 1_010_200,
  }), null);
  assert.equal(chartTimeViewportToPersist({ visibleFrom: 5, visibleTo: 5, latestCandleTime: 9 }), null);
  assert.equal(chartTimeViewportToPersist({ visibleFrom: null, visibleTo: 5, latestCandleTime: 9 }), null);
});

test("the forward bound falls back to the widest region when the indexes are unusable", () => {
  assert.equal(chartPanForwardLastIndex({ logicalRange: null, latestIndex: 207, futureSlots: 6 }), 213);
  assert.ok(Number.isNaN(chartPanForwardLastIndex({ logicalRange: { from: 1, to: 9 }, latestIndex: null, futureSlots: 6 })));
});

// ZOOM MEMORY IS EXACT ONLY FOR WHOLE BARS. The zoom is remembered as the
// TIMES of the first and last visible candle, and Lightweight Charts reports
// those by rounding a fractional logical range outwards (measured: a view of
// [153.62, 187.38] is reported as candles 153..188). Restoring those times
// frames 35 slots where the trader had 33.76 - about one extra candle on every
// first restore (141 px -> 146 px on the scrollbar thumb). A toolbar zoom that
// lands on whole bars is reported exactly and restored exactly, and saving the
// restored view returns the same times, so nothing can creep.
test("a toolbar zoom lands on whole bars so the remembered zoom restores exactly", () => {
  assert.deepEqual(chartWholeBarLogicalRange({ from: 157.36, to: 207 }), { from: 157, to: 207 });
  assert.deepEqual(chartWholeBarLogicalRange({ from: 153.6224, to: 187.3776 }), { from: 154, to: 187 });
  // Idempotent: a whole-bar view is its own fixed point (no ratchet).
  const once = chartWholeBarLogicalRange({ from: 172.5, to: 206.49 });
  assert.deepEqual(chartWholeBarLogicalRange(once), once);
  assert.deepEqual(chartWholeBarLogicalRange({ from: 173, to: 207 }), { from: 173, to: 207 });
  // Never collapses or flips a tiny view; unusable input passes through.
  assert.deepEqual(chartWholeBarLogicalRange({ from: 10.1, to: 10.4 }), { from: 10.1, to: 10.4 });
  assert.equal(chartWholeBarLogicalRange(null), null);
});

test("repeated toolbar zoom-in from the opening view stays on whole bars at the live edge", () => {
  let range = { from: 134, to: 207 };
  const spans = [];
  for (let click = 0; click < 4; click += 1) {
    range = chartWholeBarLogicalRange(chartZoomLogicalRange({
      logicalRange: range,
      direction: "in",
      firstCandleIndex: 0,
      latestCandleIndex: 207,
      futureSlots: 6,
    }));
    assert.equal(range.to, 207);
    assert.ok(Number.isInteger(range.from));
    spans.push(range.to - range.from);
  }
  assert.deepEqual(spans, [50, 34, 23, 16]);
});

test("stores manual horizontal zoom per chart for the browser session", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const viewportKey = "fullscreen:workspace:quad:panel-0:4h:MSFT";

  // v2 payload: SECONDS, not logical indexes. The original assertion used
  // { span, latestRatio } in index units, which is the shape that drifted
  // across index reflow and pinned charts to a stale zoom.
  assert.equal(storeChartTimeViewport(storage, viewportKey, {
    spanSeconds: 420 * 3_600,
    forwardSeconds: 8 * 3_600,
  }), true);
  assert.deepEqual(readChartTimeViewport(storage, viewportKey), {
    spanSeconds: 420 * 3_600,
    forwardSeconds: 8 * 3_600,
  });
  assert.equal(clearChartTimeViewport(storage, viewportKey), true);
  assert.equal(readChartTimeViewport(storage, viewportKey), null);
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
  }), 240);
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

test("a clamped time scale still frames the requested candle count", () => {
  // Supersedes "collapsed time scale is rejected...". Rejecting the clamp was
  // right; returning null was not - the caller's fallback re-frames from
  // barSpacing alone and does not restore the candle count, so the chart kept
  // whatever slice the clamp produced. Measured live on MSFT 4H: 1,107 candles
  // loaded, "150 candles ago" clamped to index 1055, chart opened on the last
  // 52. Deriving the window by index recovers the full request.
  const bars = Array.from({ length: 1_107 }, (_, i) => ({ time: 1_000 + i * 14_400 }));
  const clampedAt = 1_055;
  const clamped = (time) => {
    const index = Math.round((time - 1_000) / 14_400);
    return index < clampedAt ? clampedAt : index;
  };

  const window = chartCandleLogicalWindow({
    bars, historySlots: 150, futureSlots: 10, timeToIndex: clamped,
  });
  assert.ok(window, "a clamped scale must still produce a window");
  assert.ok(
    window.to - window.from >= 150,
    `clamped scale framed only ${Math.round(window.to - window.from)} slots`,
  );

  // A healthy scale is unchanged and agrees with the clamped result.
  const healthy = (time) => Math.round((time - 1_000) / 14_400);
  const clean = chartCandleLogicalWindow({
    bars, historySlots: 150, futureSlots: 10, timeToIndex: healthy,
  });
  assert.ok(Math.abs((clean.to - clean.from) - (window.to - window.from)) <= 2);
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

test("intraday opening view keeps candles narrow and adds history as the pane grows", () => {
  // DENSITY, not candle count, is what makes a chart read as zoomed out.
  // Capping the count at 120 meant a 1920px pane spread 120 bars across the
  // full width at ~15px each: "120 candles visible" yet visually zoomed in.
  // The pitch is now fixed and the count follows the pane.
  let previous = 0;
  for (const minutes of [5, 15, 60, 120]) {
    for (const width of [343, 700, 1_280, 1_920, 2_560]) {
      const history = chartDefaultHistorySlots({ timeframeMinutes: minutes, chartWidth: width });
      const future = chartDefaultFutureSlots({ historySlots: history });
      const pitch = Math.max(
        TRADINGVIEW_MIN_BAR_SPACING_PX,
        Math.min(TRADINGVIEW_MAX_BAR_SPACING_PX, width / (history + future)),
      );
      assert.ok(pitch <= TRADINGVIEW_MAX_BAR_SPACING_PX, `${width}px gave ${pitch.toFixed(1)}px candles`);
      // Candles must also stay THICK enough to read. The count floor used to be
      // a constant 70 calibrated on 700-1520px panes, so a ~370px phone crammed
      // the desktop count into a third of the width and opened at ~4.9px per
      // candle - a grey smear. TradingView mobile keeps ~8-10px candles and
      // shows fewer bars instead, so the floor now tapers with the pane.
      assert.ok(pitch >= 6, `${minutes}m at ${width}px gave ${pitch.toFixed(1)}px candles - too thin to read`);
      // Desktop panes keep the original generous history; phones trade count
      // for legibility rather than showing an unreadable 60+.
      const expectedFloor = Math.min(60, Math.max(24, Math.round(width / 9)));
      assert.ok(history >= expectedFloor, `${minutes}m at ${width}px opened on only ${history} candles`);
      assert.ok(future >= 5 && future <= 10, `${minutes}m at ${width}px reserved ${future} bars`);
    }
  }
  // A wider intraday pane shows MORE history at the same candle width.
  for (const width of [343, 700, 1_280, 1_920, 2_560]) {
    const history = chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: width });
    assert.ok(history >= previous, "a wider pane must not show fewer candles");
    previous = history;
  }
  assert.ok(
    chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: 1_920 })
      > chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: 700 }),
  );
});

test("big screen no longer shows fewer candles than a normal one", () => {
  const width = 1_920;
  const normal = chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: width });
  const big = chartDefaultHistorySlots({ timeframeMinutes: 5, chartWidth: width, isBigScreen: true });
  assert.equal(big, normal, "at equal width the profile must not shrink the view");
});





test("the opening projection does not eat a third of a big-screen pane", () => {
  // Projection stays inside the requested 5-10 bar TradingView-style gap.
  for (const width of [343, 660, 960]) {
    const history = chartDefaultHistorySlots({ timeframeMinutes: 240, chartWidth: width, isBigScreen: true });
    const future = chartDefaultFutureSlots({ historySlots: history, isBigScreen: true });
    const blankShare = future / (history + future);
    assert.ok(
      blankShare <= 0.25,
      `${width}px: ${Math.round(blankShare * 100)}% of the pane opened blank`,
    );
  }
});


test("stored zoom survives logical-index reflow (the 2026-08-05 ratchet)", () => {
  // v1 stored the span in LOGICAL INDEX units. Study series adding or dropping
  // points, or the fast slice being replaced by full history, moved indexes -
  // so save -> restore -> remeasure -> save drifted with no restoring force.
  // Seconds cannot reflow.
  const values = new Map();
  const storage = { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  const key = "big:panel-0:4h:AMZN";
  const HOUR = 3_600;

  assert.equal(storeChartTimeViewport(storage, key, { spanSeconds: 200 * HOUR, forwardSeconds: 8 * HOUR }), true);

  // Round-trip repeatedly, as a reload would.
  let latest = 1_800_000_000;
  let previous = null;
  for (let pass = 0; pass < 5; pass += 1) {
    const saved = readChartTimeViewport(storage, key);
    const range = chartTimeViewportRange(latest, saved);
    const span = range.to - range.from;
    if (previous !== null) assert.equal(span, previous, "span drifted across a reload");
    previous = span;
    // Re-persist exactly what was restored, and advance the live edge.
    storeChartTimeViewport(storage, key, { spanSeconds: span, forwardSeconds: range.to - latest });
    latest += 4 * HOUR;
  }
  assert.equal(previous, 200 * HOUR);
});

test("restored window follows the live edge instead of stranding on old data", () => {
  const HOUR = 3_600;
  const latest = 1_800_000_000;
  const range = chartTimeViewportRange(latest, { spanSeconds: 100 * HOUR, forwardSeconds: 5 * HOUR });
  // An AMZN 4H chart reopened on 2026-08-06 because v1 also restored the
  // scroll POSITION. The zoom is remembered; the anchor is always the newest
  // candle plus its forward space.
  assert.equal(range.to, latest + 5 * HOUR);
  assert.equal(range.to - range.from, 100 * HOUR);
});

test("rejects v1 index-based entries and unusable spans", () => {
  const values = new Map();
  const storage = { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  const key = "big:panel-0:4h:SLV";

  // A v1 payload (span in index units, latestRatio) must not be honoured.
  values.set("oiFinderChartTimeViewports.v2", JSON.stringify({ [key]: { span: 40, latestRatio: 0.78 } }));
  assert.equal(readChartTimeViewport(storage, key), null);

  assert.equal(storeChartTimeViewport(storage, key, { spanSeconds: 30 }), false, "sub-minute span");
  assert.equal(chartTimeViewportRange(1_800_000_000, { spanSeconds: 10 }), null);
  assert.equal(chartTimeViewportRange(0, { spanSeconds: 100_000 }), null);
});

// ---- toolbar pan arrows -----------------------------------------------------

test("one pan click travels a third of the visible span without resizing it", () => {
  assert.equal(CHART_PAN_STEP_FRACTION, 1 / 3);
  const back = chartPanStepLogicalRange({
    logicalRange: { from: 300, to: 390 },
    direction: "back",
    firstIndex: 0,
    lastIndex: 1_000,
  });
  assert.deepEqual(back, { from: 270, to: 360 });
  const forward = chartPanStepLogicalRange({
    logicalRange: { from: 300, to: 390 },
    direction: "forward",
    firstIndex: 0,
    lastIndex: 1_000,
  });
  assert.deepEqual(forward, { from: 330, to: 420 });
  assert.equal(forward.to - forward.from, 90, "a pan must never zoom");
});

test("panning back stops on the oldest loaded candle and then reports no room", () => {
  const partial = chartPanStepLogicalRange({
    logicalRange: { from: 20, to: 110 },
    direction: "back",
    firstIndex: 0,
    lastIndex: 1_000,
  });
  assert.deepEqual(partial, { from: 0, to: 90 }, "the last step shortens instead of overshooting");
  assert.equal(
    chartPanStepLogicalRange({
      logicalRange: partial,
      direction: "back",
      firstIndex: 0,
      lastIndex: 1_000,
    }),
    null,
    "null is what disables the button at the edge",
  );
});

test("panning forward stops at the configured right offset", () => {
  const partial = chartPanStepLogicalRange({
    logicalRange: { from: 950, to: 1_010 },
    direction: "forward",
    firstIndex: 0,
    // 1_000 loaded candles plus 27 slots of future projection.
    lastIndex: 1_026,
  });
  assert.deepEqual(partial, { from: 966, to: 1_026 });
  assert.equal(
    chartPanStepLogicalRange({ logicalRange: partial, direction: "forward", firstIndex: 0, lastIndex: 1_026 }),
    null,
  );
});

test("a view already parked past an edge is never yanked back the other way", () => {
  // A drag can leave the window left of the first candle. Pressing pan-left
  // there must do nothing, not jump the chart to the right.
  assert.equal(
    chartPanStepLogicalRange({
      logicalRange: { from: -40, to: 50 },
      direction: "back",
      firstIndex: 0,
      lastIndex: 1_000,
    }),
    null,
  );
  assert.equal(
    chartPanStepLogicalRange({
      logicalRange: { from: 1_100, to: 1_190 },
      direction: "forward",
      firstIndex: 0,
      lastIndex: 1_026,
    }),
    null,
  );
  // The opposite arrow still works from out there.
  assert.deepEqual(
    chartPanStepLogicalRange({
      logicalRange: { from: -40, to: 50 },
      direction: "forward",
      firstIndex: 0,
      lastIndex: 1_000,
    }),
    { from: -10, to: 80 },
  );
});

test("pans unbounded while the candle indices are unknown, and refuses junk", () => {
  assert.deepEqual(
    chartPanStepLogicalRange({ logicalRange: { from: 0, to: 30 }, direction: "back" }),
    { from: -10, to: 20 },
  );
  assert.equal(chartPanStepLogicalRange({ logicalRange: { from: 10, to: 10 }, direction: "back" }), null);
  assert.equal(chartPanStepLogicalRange({ logicalRange: { from: 10, to: 5 }, direction: "back" }), null);
  assert.equal(chartPanStepLogicalRange({ logicalRange: { from: 0, to: 30 }, direction: "sideways" }), null);
  assert.equal(chartPanStepLogicalRange(), null);
});

// ---- Zoom memory in candles (2026-11-14) -----------------------------------
// The remembered zoom is a fractional CANDLE count back from the newest candle
// plus the blank candle slots ahead of it, so a wheel or drag zoom restores
// exactly and a view parked by Return to latest keeps its blank room.

// A shared time scale as Lightweight Charts presents it: `density` logical
// slots per candle (denser 30m study points), `offset` extra slots before the
// first candle, and an optional clamp that mimics a series not yet fully on
// the scale (older timestamps resolve to the nearest index it has).
function zoomMemoryScale({ count = 208, step = 300, density = 1, offset = 0, clampBelow = null, gapAt = -1, gapSeconds = 0 } = {}) {
  const start = 1_794_555_600;
  const bars = Array.from({ length: count }, (_, index) => ({
    time: start + index * step + (gapAt >= 0 && index >= gapAt ? gapSeconds : 0),
  }));
  const byTime = new Map(bars.map((bar, index) => [bar.time, index]));
  const timeToIndex = (time) => {
    const at = byTime.get(Number(time));
    if (at === undefined) return null;
    const logical = offset + at * density;
    return clampBelow !== null && logical < clampBelow ? clampBelow : logical;
  };
  return { bars, timeToIndex, latestIndex: offset + (count - 1) * density, density };
}

test("a fractional wheel zoom is remembered exactly and restores to the same width", () => {
  const { bars, timeToIndex } = zoomMemoryScale();
  // Measured 2026-11-14: this view used to be remembered as candles 153..188.
  const saved = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 153.6224, to: 187.3776 }, bars, timeToIndex });
  assert.equal(saved.candleSpan, 33.755);
  assert.equal(saved.aheadSlots, 0);
  const restored = chartZoomMemoryLogicalRange({ viewport: saved, bars, timeToIndex });
  assert.ok(Math.abs((restored.to - restored.from) - 33.755) < 1e-9, "the restored view is not one candle wider");
  assert.equal(restored.to, 207);
  assert.equal(restored.wholeBars, false);
});

test("zoom memory is a fixed point: save -> restore -> save returns the same value", () => {
  let seed = 20261114;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
  const scales = [
    zoomMemoryScale(),
    zoomMemoryScale({ density: 8 }),
    zoomMemoryScale({ density: 8, offset: 37 }),
    zoomMemoryScale({ count: 70, step: 900, offset: 3 }),
    zoomMemoryScale({ gapAt: 150, gapSeconds: 203_400 }),
  ];
  let checked = 0;
  for (const scale of scales) {
    for (let sample = 0; sample < 200; sample += 1) {
      const span = (8 + random() * 120) * scale.density;
      const to = scale.latestIndex + (random() * 40 - 25) * scale.density;
      const first = chartZoomMemoryFromLogicalRange({
        logicalRange: { from: to - span, to },
        bars: scale.bars,
        timeToIndex: scale.timeToIndex,
      });
      if (!first) continue;
      checked += 1;
      let saved = first;
      let width = null;
      for (let trip = 0; trip < 5; trip += 1) {
        const restored = chartZoomMemoryLogicalRange({ viewport: saved, bars: scale.bars, timeToIndex: scale.timeToIndex });
        assert.ok(restored, "a remembered zoom must restore on a healthy scale");
        if (width !== null) assert.ok(Math.abs((restored.to - restored.from) - width) < 1e-6, "the view crept between round trips");
        width = restored.to - restored.from;
        const again = chartZoomMemoryFromLogicalRange({ logicalRange: restored, bars: scale.bars, timeToIndex: scale.timeToIndex });
        assert.deepEqual(again, first, `drift on trip ${trip}: ${JSON.stringify(first)} -> ${JSON.stringify(again)}`);
        saved = again;
      }
    }
  }
  assert.ok(checked > 900, `only ${checked} views were checked`);
});

test("a view parked by Return to latest keeps its candles AND its blank room", () => {
  const { bars, timeToIndex } = zoomMemoryScale();
  // Measured: Return to latest parks a 34-slot view at [179, 213] (28 candle
  // slots + 6 blank). It used to come back as [179, 207], then 22, then 16.
  let range = { from: 179, to: 213 };
  for (let trip = 0; trip < 3; trip += 1) {
    const saved = chartZoomMemoryFromLogicalRange({ logicalRange: range, bars, timeToIndex });
    assert.deepEqual(saved, { spanSeconds: 28 * 300, forwardSeconds: 0, candleSpan: 28, aheadSlots: 6 });
    const restored = chartZoomMemoryLogicalRange({ viewport: saved, bars, timeToIndex });
    assert.equal(restored.from, 179);
    assert.equal(restored.to, 213);
    assert.equal(restored.wholeBars, false);
    range = { from: restored.from, to: restored.to };
  }
});

test("blank room is remembered in candle widths on a scale made denser by study points", () => {
  const { bars, timeToIndex, latestIndex } = zoomMemoryScale({ count: 120, step: 14_400, density: 8, offset: 5 });
  const saved = chartZoomMemoryFromLogicalRange({
    logicalRange: { from: latestIndex - 40 * 8, to: latestIndex + 12 * 8 },
    bars,
    timeToIndex,
  });
  assert.equal(saved.candleSpan, 40);
  assert.equal(saved.aheadSlots, 12);
  // Restored before the 30m studies are back on the scale: one slot a candle.
  const bare = zoomMemoryScale({ count: 120, step: 14_400 });
  const restored = chartZoomMemoryLogicalRange({ viewport: saved, bars: bare.bars, timeToIndex: bare.timeToIndex });
  assert.equal(restored.from, 119 - 40);
  assert.equal(restored.to, 119 + 12);
});

test("a whole-bar toolbar zoom is remembered as before and restores by candle time", () => {
  const { bars, timeToIndex } = zoomMemoryScale();
  const saved = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 173, to: 207 }, bars, timeToIndex });
  // The same 10200 s the time-range reading stored for this view.
  assert.deepEqual(saved, { spanSeconds: 10_200, forwardSeconds: 0, candleSpan: 34, aheadSlots: 0 });
  const restored = chartZoomMemoryLogicalRange({ viewport: saved, bars, timeToIndex });
  assert.equal(restored.wholeBars, true);
  assert.equal(restored.firstTime, bars[173].time);
  assert.equal(restored.latestTime, bars[207].time);
  assert.deepEqual([restored.from, restored.to], [173, 207]);
});

test("remembered seconds count candles only, never the gap a view happens to span", () => {
  // Panned back across a weekend gap: 34 candles, 56.5 h of clock time.
  const { bars, timeToIndex } = zoomMemoryScale({ gapAt: 150, gapSeconds: 203_400 });
  const saved = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 130, to: 164 }, bars, timeToIndex });
  assert.equal(saved.candleSpan, 34);
  assert.equal(saved.spanSeconds, 34 * 300);
});

test("zoom memory refuses a scale that is still clamping, a view with no candle, and junk", () => {
  const healthy = zoomMemoryScale();
  const saved = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 150.5, to: 207 }, bars: healthy.bars, timeToIndex: healthy.timeToIndex });
  // Older candles not on the scale yet: every old time resolves to index 190.
  const clamped = zoomMemoryScale({ clampBelow: 190 });
  assert.equal(chartZoomMemoryLogicalRange({ viewport: saved, bars: clamped.bars, timeToIndex: clamped.timeToIndex }), null);
  assert.equal(chartZoomMemoryFromLogicalRange({ logicalRange: { from: 150.5, to: 180.2 }, bars: clamped.bars, timeToIndex: clamped.timeToIndex }), null);
  // An empty scale answers null; Number(null) is 0 and must not pass as index 0.
  assert.equal(chartZoomMemoryLogicalRange({ viewport: saved, bars: healthy.bars, timeToIndex: () => null }), null);
  assert.equal(chartZoomMemoryFromLogicalRange({ logicalRange: { from: 150.5, to: 207 }, bars: healthy.bars, timeToIndex: () => null }), null);
  // Entirely in the blank room ahead of the newest candle.
  assert.equal(chartZoomMemoryFromLogicalRange({ logicalRange: { from: 208, to: 230 }, bars: healthy.bars, timeToIndex: healthy.timeToIndex }), null);
  assert.equal(chartZoomMemoryFromLogicalRange({ logicalRange: { from: 9, to: 9 }, bars: healthy.bars, timeToIndex: healthy.timeToIndex }), null);
  assert.equal(chartZoomMemoryFromLogicalRange(), null);
  // An entry saved before candle memory existed has no candle span.
  assert.equal(chartZoomMemoryLogicalRange({ viewport: { spanSeconds: 10_200, forwardSeconds: 0 }, bars: healthy.bars, timeToIndex: healthy.timeToIndex }), null);
  assert.equal(chartZoomMemoryLogicalRange(), null);
});

test("blank room never outgrows the candles, and a span deeper than the tape shows the whole tape", () => {
  const { bars, timeToIndex } = zoomMemoryScale();
  // Dragged far into the future: 10 candles and 30 blank slots on screen.
  const dragged = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 197, to: 237 }, bars, timeToIndex });
  assert.deepEqual([dragged.candleSpan, dragged.aheadSlots], [10, 10]);
  const again = chartZoomMemoryFromLogicalRange({
    logicalRange: chartZoomMemoryLogicalRange({ viewport: dragged, bars, timeToIndex }),
    bars,
    timeToIndex,
  });
  assert.deepEqual(again, dragged);
  // Remembered on a 208-candle tape, restored on a 60-candle one.
  const deep = chartZoomMemoryFromLogicalRange({ logicalRange: { from: 20.4, to: 207 }, bars, timeToIndex });
  const short = zoomMemoryScale({ count: 60 });
  const restored = chartZoomMemoryLogicalRange({ viewport: deep, bars: short.bars, timeToIndex: short.timeToIndex });
  assert.deepEqual([restored.from, restored.to], [0, 59]);
});

test("the stored zoom keeps its candle fields and still reads entries saved without them", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const viewport = { spanSeconds: 8_400, forwardSeconds: 0, candleSpan: 27.5, aheadSlots: 6 };
  assert.equal(storeChartTimeViewport(storage, "normal:panel-0:5m:AAPL", viewport), true);
  assert.deepEqual(readChartTimeViewport(storage, "normal:panel-0:5m:AAPL"), viewport);
  assert.equal(storeChartTimeViewport(storage, "normal:panel-0:15m:AAPL", { spanSeconds: 9_000, forwardSeconds: 0 }), true);
  assert.deepEqual(readChartTimeViewport(storage, "normal:panel-0:15m:AAPL"), { spanSeconds: 9_000, forwardSeconds: 0 });
  // Junk candle fields are dropped, not trusted.
  assert.equal(storeChartTimeViewport(storage, "normal:panel-0:1h:AAPL", { spanSeconds: 9_000, candleSpan: "wide", aheadSlots: -3 }), true);
  assert.deepEqual(readChartTimeViewport(storage, "normal:panel-0:1h:AAPL"), { spanSeconds: 9_000, forwardSeconds: 0 });
});
