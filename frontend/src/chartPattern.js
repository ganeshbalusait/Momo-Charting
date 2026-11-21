// Chart patterns on the Daily / Weekly / Monthly chart (Ganesh, 2026-11-20:
// "in scanner any tickers can we do in chart patterns in D/W/M when we open
// the chart? That would be good so I know which pattern").
//
// The server (GET /api/momx-scanner/chart-pattern?symbol=X, momx/
// monthly_breakout.chart_patterns) runs the SAME pattern rules the
// Breakout / Breakdown lists use, on the scanner's full history (monthly
// since 2015 - the chart only loads ~5 years, too few months for the 50 EMA),
// and sends each break with its lines:
//   {pattern, label, direction, status, period, level, target,
//    lines: [{from, fromPrice, to, toPrice, bars, role}]}
// Periods are "YYYY-MM" (monthly) or "YYYY-MM-DD" (weekly = the Monday,
// daily = the session). This module turns that into what the chart draws:
// sloped/flat segments anchored to the chart's own candles, a dashed target,
// and one price-axis tag at the line that broke.
import { chartAggregationBucketTime } from "./chartAggregation.js";

export const CHART_PATTERN_ENDPOINT = "/api/momx-scanner/chart-pattern";

const TIMEFRAMES = Object.freeze({ 1440: "Daily", 10080: "Weekly", 43200: "Monthly" });
const LETTERS = Object.freeze({ Daily: "D", Weekly: "W", Monthly: "M" });
const SHORT = Object.freeze({
  BOTTOM: "HL + Resistance",
  SYM: "Triangle",
  MIDDLE: "Range",
  TOP: "LH + Support",
});
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const CHART_PATTERN_COLORS = Object.freeze({ up: "#00e5ff", down: "#ff3fb4" });

// "Daily" | "Weekly" | "Monthly" for a D / W / M chart, else null.
export function chartPatternTimeframe(chartMinutes) {
  return TIMEFRAMES[Number(chartMinutes)] || null;
}

// The chart candle (calendar bucket) a server period falls in. Noon UTC is
// inside the same Eastern calendar day, so the bucket is never the day before.
export function periodBucketTime(period, chartMinutes) {
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(String(period || ""));
  if (!match) return null;
  const seconds = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3] || 1), 12) / 1000;
  return chartAggregationBucketTime(seconds, Number(chartMinutes));
}

function when(period, timeframe) {
  const match = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(String(period || ""));
  if (!match) return "";
  const month = MONTHS[Number(match[2]) - 1] || "";
  if (!match[3]) return `${month} ${match[1].slice(2)}`;
  return `${timeframe === "Weekly" ? "wk " : ""}${month} ${Number(match[3])}`;
}

function money(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  return number.toFixed(number >= 1 ? 2 : 4);
}

function indexAtOrBefore(times, target) {
  let low = 0;
  let high = times.length - 1;
  let found = -1;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (times[middle] <= target) {
      found = middle;
      low = middle + 1;
    } else high = middle - 1;
  }
  return found;
}

// One line -> one segment on THIS chart's candles. Anchored at its END (the
// breakout candle) and walked back `bars` candles, so the slope per candle is
// the server's; a start older than the loaded history is cut at the first
// candle, at the price the line had there.
export function patternSegment(line, chartBars, chartMinutes) {
  const bars = Array.isArray(chartBars) ? chartBars : [];
  if (!line || bars.length < 2) return null;
  const times = bars.map((bar) => Number(bar.time));
  const endBucket = periodBucketTime(line.to, chartMinutes);
  if (endBucket == null) return null;
  const endIndex = indexAtOrBefore(times, endBucket);
  // The breakout candle must be on the chart (not a stale/other ticker's tape).
  if (endIndex < 0 || times[endIndex] !== endBucket) return null;
  const span = Math.max(1, Math.round(Number(line.bars) || 0));
  const fromPrice = Number(line.fromPrice);
  const toPrice = Number(line.toPrice);
  if (!Number.isFinite(fromPrice) || !Number.isFinite(toPrice)) return null;
  const slope = (toPrice - fromPrice) / span;
  const startIndex = Math.max(0, endIndex - span);
  const startPrice = toPrice - slope * (endIndex - startIndex);
  if (startIndex === endIndex) return null;
  return {
    startTime: times[startIndex],
    startPrice,
    endTime: times[endIndex],
    endPrice: toPrice,
    role: String(line.role || ""),
  };
}

// Everything the chart draws for one payload on one D / W / M chart.
// Returns {timeframe, segments, labels}; empty when nothing broke.
export function buildChartPatternOverlay(payload, chartMinutes, chartBars, { symbol = "" } = {}) {
  const empty = { timeframe: null, segments: [], labels: [] };
  const timeframe = chartPatternTimeframe(chartMinutes);
  if (!timeframe || !payload || payload.ok !== true) return empty;
  if (symbol && String(payload.symbol || "").toUpperCase() !== String(symbol).toUpperCase()) return empty;
  const verdict = payload.patterns && payload.patterns[timeframe];
  if (!verdict || typeof verdict !== "object") return { ...empty, timeframe };
  const segments = [];
  const labels = [];
  for (const direction of ["up", "down"]) {
    const signal = verdict[direction];
    if (!signal || typeof signal !== "object") continue;
    const color = CHART_PATTERN_COLORS[direction];
    const up = direction === "up";
    const drawn = (Array.isArray(signal.lines) ? signal.lines : [])
      .map((line) => patternSegment(line, chartBars, chartMinutes))
      .filter(Boolean);
    if (!drawn.length) continue;
    drawn.forEach((segment) => segments.push({ ...segment, color, lineWidth: 2, dash: [], direction }));
    const short = SHORT[signal.pattern] || String(signal.label || signal.pattern || "");
    // "live" = the candle has not closed, so the break can still fail.
    const open = signal.status === "live" ? ` (${when(signal.period, timeframe)} open)` : "";
    const level = money(signal.level);
    const target = money(signal.target);
    // The measured target, dashed, from the breakout candle to the axis.
    if (target) {
      segments.push({
        startTime: drawn[0].endTime,
        startPrice: Number(signal.target),
        endTime: null,
        endPrice: Number(signal.target),
        color,
        lineWidth: 1,
        dash: [6, 4],
        direction,
        role: "target",
      });
    }
    const letter = LETTERS[timeframe];
    labels.push({
      key: `chart-pattern-${direction}-level`,
      price: Number(signal.level),
      color,
      title: `${letter} ${up ? "▲" : "▼"} ${short} ${level}${open}`,
    });
    if (target) {
      labels.push({
        key: `chart-pattern-${direction}-target`,
        price: Number(signal.target),
        color,
        title: `${letter} ${short} target ${target}`,
      });
    }
  }
  return { timeframe, segments, labels };
}

// A short signature so a live-tick redraw notices a new or removed pattern.
export function chartPatternSignature(overlay) {
  if (!overlay || !overlay.segments?.length) return "";
  return overlay.segments
    .map((segment) => `${segment.direction}:${segment.role}:${segment.startTime}:${Number(segment.endPrice).toFixed(4)}`)
    .join(",");
}
