import assert from "node:assert/strict";
import test from "node:test";

import { chartAggregationBucketTime } from "./chartAggregation.js";
import {
  buildChartPatternOverlay,
  chartPatternSignature,
  chartPatternTimeframe,
  patternSegment,
  periodBucketTime,
} from "./chartPattern.js";

const MONTH = 43200;
const WEEK = 10080;
const DAY = 1440;

// Monthly chart candles Jan 2021 .. Nov 2026, timed like the chart's own.
function monthBars(fromYear = 2021, count = 71) {
  return Array.from({ length: count }, (_, i) => {
    const year = fromYear + Math.floor(i / 12);
    const month = i % 12;
    return { time: chartAggregationBucketTime(Date.UTC(year, month, 3, 15) / 1000, MONTH), close: 100 };
  });
}

// The shape the server sends (momx/monthly_breakout.chart_patterns).
function payload() {
  return {
    ok: true,
    symbol: "DLTR",
    source: "scanner",
    patterns: {
      Monthly: {
        up: {
          pattern: "SYM",
          label: "Symmetrical triangle",
          direction: "up",
          status: "live",
          period: "2026-11",
          level: 134.79,
          target: 190.5,
          lines: [
            { from: "2025-02", fromPrice: 160, to: "2026-11", toPrice: 134.79, bars: 21, role: "falling highs",
              touches: [{ period: "2025-02", price: 160 }, { period: "2025-09", price: 152 }, { period: "2026-06", price: 141 }] },
            { from: "2025-04", fromPrice: 60, to: "2026-11", toPrice: 100, bars: 19, role: "rising lows",
              touches: [{ period: "2025-04", price: 60 }, { period: "2026-01", price: 80 }, { period: "1999-01", price: 1 }] },
          ],
        },
        down: null,
      },
      Weekly: null,
      Daily: { up: null, down: null },
    },
  };
}

test("only Daily, Weekly and Monthly charts get a pattern", () => {
  assert.equal(chartPatternTimeframe(DAY), "Daily");
  assert.equal(chartPatternTimeframe(WEEK), "Weekly");
  assert.equal(chartPatternTimeframe(MONTH), "Monthly");
  assert.equal(chartPatternTimeframe(5), null);
  assert.equal(chartPatternTimeframe(240), null);
});

test("server periods land on the chart's own candles (Eastern calendar buckets)", () => {
  assert.equal(periodBucketTime("2026-11", MONTH), chartAggregationBucketTime(Date.UTC(2026, 10, 20, 15) / 1000, MONTH));
  assert.equal(periodBucketTime("2026-11-16", WEEK), chartAggregationBucketTime(Date.UTC(2026, 10, 20, 15) / 1000, WEEK));
  assert.equal(periodBucketTime("2026-11-20", DAY), chartAggregationBucketTime(Date.UTC(2026, 10, 20, 20) / 1000, DAY));
  assert.equal(periodBucketTime("junk", DAY), null);
});

test("a line ends on the breakout candle and keeps the server's slope per candle", () => {
  const bars = monthBars();
  const segment = patternSegment(payload().patterns.Monthly.up.lines[0], bars, MONTH);
  assert.equal(segment.endTime, bars.at(-1).time);
  assert.equal(segment.startTime, bars.at(-1 - 21).time);
  assert.ok(Math.abs(segment.startPrice - 160) < 1e-9);
  assert.equal(segment.endPrice, 134.79);
});

test("a base older than the loaded history is cut at the first candle, on the line", () => {
  const bars = monthBars(2026, 11); // Jan..Nov 2026 only
  const segment = patternSegment({ from: "2025-02", fromPrice: 160, to: "2026-11", toPrice: 134.79, bars: 21 }, bars, MONTH);
  assert.equal(segment.startTime, bars[0].time);
  const perCandle = (134.79 - 160) / 21;
  assert.ok(Math.abs(segment.startPrice - (134.79 - perCandle * 10)) < 1e-9);
});

test("no line when the breakout candle is not on this chart (stale tape or another ticker)", () => {
  const bars = monthBars(2021, 60); // ends Dec 2025
  assert.equal(patternSegment({ from: "2025-02", fromPrice: 1, to: "2026-11", toPrice: 2, bars: 21 }, bars, MONTH), null);
});

test("the overlay draws both lines and the dashed target, and tags the line that broke", () => {
  const overlay = buildChartPatternOverlay(payload(), MONTH, monthBars(), { symbol: "dltr" });
  assert.equal(overlay.timeframe, "Monthly");
  const solid = overlay.segments.filter((segment) => !["target", "measure"].includes(segment.role));
  assert.deepEqual(solid.map((segment) => segment.role), ["falling highs", "rising lows"]);
  // Like his sample pictures: the trendlines orange, thick, named at their start.
  assert.ok(solid.every((segment) => segment.color === "#ffa53a" && segment.lineWidth === 2.5));
  assert.deepEqual(solid.map((segment) => segment.startLabel), ["Trendline (lower highs)", "Trendline (higher lows)"]);
  const target = overlay.segments.find((segment) => segment.role === "target");
  assert.deepEqual([target.startPrice, target.endTime, target.dash, target.color], [190.5, null, [6, 4], "#00e5ff"]);
  assert.deepEqual(overlay.labels.map((label) => label.title), [
    "M ▲ Triangle 134.79 (Nov 26 open)",
    "M Triangle target 190.50",
  ]);
  assert.ok(overlay.labels.every((label) => label.key.startsWith("chart-pattern-")));
});

test("only the line that broke carries the pattern's name for the chart", () => {
  const overlay = buildChartPatternOverlay(payload(), MONTH, monthBars(), { symbol: "DLTR" });
  const named = overlay.segments.filter((segment) => segment.caption);
  assert.equal(named.length, 1);
  assert.equal(named[0].role, "falling highs", "the triangle's upper side is the one that broke");
  assert.equal(named[0].caption, "M ▲ Symmetrical triangle > 134.79 (Nov 26 open)");
  // The caption goes outside (above an up break) in the break's colour; the
  // line's own name moves inside so the two never sit on top of each other.
  assert.deepEqual([named[0].captionColor, named[0].captionSide, named[0].startLabelSide], ["#00e5ff", "above", "below"]);
  const other = overlay.segments.find((segment) => segment.role === "rising lows");
  assert.equal(other.startLabelSide, "below");
});

test("a confirmed break does not say open; a breakdown is pink", () => {
  const data = payload();
  data.patterns.Monthly.down = { ...data.patterns.Monthly.up, direction: "down", pattern: "TOP", status: "confirmed", target: null };
  data.patterns.Monthly.up = null;
  const overlay = buildChartPatternOverlay(data, MONTH, monthBars(), { symbol: "DLTR" });
  assert.deepEqual(overlay.labels.map((label) => label.title), ["M ▼ LH + Support 134.79"]);
  const own = overlay.segments.filter((segment) => !segment.startLabel);
  assert.ok(own.length === 0 || own.every((segment) => segment.color === "#ff3fb4"));
  assert.equal(overlay.markers.find((marker) => marker.kind === "breakout").color, "#ff3fb4");
  assert.equal(overlay.segments.some((segment) => ["target", "measure"].includes(segment.role)), false);
  assert.equal(overlay.markers.some((marker) => marker.kind.startsWith("arrow")), false);
});

test("nothing is drawn for another ticker, a failed read, no break, or a minute chart", () => {
  const bars = monthBars();
  assert.equal(buildChartPatternOverlay(payload(), MONTH, bars, { symbol: "AAPL" }).segments.length, 0);
  assert.equal(buildChartPatternOverlay({ ok: false }, MONTH, bars).segments.length, 0);
  assert.equal(buildChartPatternOverlay(null, MONTH, bars).segments.length, 0);
  assert.equal(buildChartPatternOverlay(payload(), WEEK, bars, { symbol: "DLTR" }).segments.length, 0);
  assert.equal(buildChartPatternOverlay(payload(), DAY, bars, { symbol: "DLTR" }).segments.length, 0);
  assert.equal(buildChartPatternOverlay(payload(), 5, bars, { symbol: "DLTR" }).timeframe, null);
});

test("the signature changes when the pattern does, so a live tick redraws it", () => {
  const bars = monthBars();
  const one = chartPatternSignature(buildChartPatternOverlay(payload(), MONTH, bars, { symbol: "DLTR" }));
  const data = payload();
  data.patterns.Monthly.up.lines[0].toPrice = 135;
  const two = chartPatternSignature(buildChartPatternOverlay(data, MONTH, bars, { symbol: "DLTR" }));
  assert.ok(one && two && one !== two);
  assert.equal(chartPatternSignature(buildChartPatternOverlay(null, MONTH, bars)), "");
});

test("a range is red resistance over green support, shaded between them", () => {
  const data = payload();
  data.patterns.Monthly.up = {
    ...data.patterns.Monthly.up,
    pattern: "MIDDLE",
    label: "Range",
    level: 1096.5,
    target: 1348.94,
    lines: [
      { from: "2025-01", fromPrice: 1096.5, to: "2026-11", toPrice: 1096.5, bars: 22, role: "resistance",
        touches: [{ period: "2025-02", price: 1078 }, { period: "2025-06", price: 1096.5 }] },
      { from: "2025-01", fromPrice: 844, to: "2026-11", toPrice: 844, bars: 22, role: "support",
        touches: [{ period: "2025-04", price: 850 }, { period: "2026-03", price: 844 }] },
    ],
  };
  const bars = monthBars();
  const overlay = buildChartPatternOverlay(data, MONTH, bars, { symbol: "DLTR" });
  const [resistance, support] = overlay.segments;
  assert.deepEqual([resistance.color, resistance.startLabel], ["#ff4d5e", "Resistance"]);
  assert.deepEqual([support.color, support.startLabel, support.startLabelSide], ["#2fe07f", "Support", "below"]);
  assert.equal(overlay.fills.length, 1);
  assert.deepEqual(overlay.fills[0].points.map((point) => point.price), [1096.5, 1096.5, 844, 844]);
  assert.equal(overlay.fills[0].points[1].time, bars.at(-1).time);
  const touches = overlay.markers.filter((marker) => marker.kind === "touch");
  assert.deepEqual(touches.map((marker) => [marker.price, marker.color]), [
    [1078, "#ff4d5e"], [1096.5, "#ff4d5e"], [850, "#2fe07f"], [844, "#2fe07f"],
  ]);
  assert.equal(touches[0].time, periodBucketTime("2025-02", MONTH));
});

test("the breakout candle is circled and an arrow points from the break to the target", () => {
  const bars = monthBars();
  const overlay = buildChartPatternOverlay(payload(), MONTH, bars, { symbol: "DLTR" });
  const breakout = overlay.markers.find((marker) => marker.kind === "breakout");
  assert.deepEqual([breakout.time, breakout.price, breakout.color], [bars.at(-1).time, 134.79, "#00e5ff"]);
  const measure = overlay.segments.find((segment) => segment.role === "measure");
  assert.deepEqual([measure.startTime, measure.endTime, measure.startPrice, measure.endPrice],
    [bars.at(-1).time, bars.at(-1).time, 134.79, 190.5]);
  const arrow = overlay.markers.find((marker) => marker.kind === "arrowUp");
  assert.deepEqual([arrow.time, arrow.price], [bars.at(-1).time, 190.5]);
});

test("touch points off the loaded chart are not circled", () => {
  const overlay = buildChartPatternOverlay(payload(), MONTH, monthBars(), { symbol: "DLTR" });
  const prices = overlay.markers.filter((marker) => marker.kind === "touch").map((marker) => marker.price);
  assert.deepEqual(prices, [160, 152, 141, 60, 80], "the 1999 point is before the chart's history");
});
