import { aggregateChartBars } from "./chartAggregation.js";

export const SQUEEZE_LENGTH = 20;

export function calculateRollingAverage(values, period) {
  const length = Math.max(1, Number(period) || 1);
  let total = 0;
  const window = [];
  return (Array.isArray(values) ? values : []).map((value) => {
    const numeric = Number(value || 0);
    window.push(numeric);
    total += numeric;
    if (window.length > length) total -= window.shift();
    return total / window.length;
  });
}

export function calculateRollingStdDev(values, period) {
  const length = Math.max(1, Number(period) || 1);
  return (Array.isArray(values) ? values : []).map((_, index) => {
    const window = values.slice(Math.max(0, index - length + 1), index + 1).map((value) => Number(value || 0));
    const average = window.reduce((sum, value) => sum + value, 0) / Math.max(window.length, 1);
    return Math.sqrt(window.reduce((sum, value) => sum + (value - average) ** 2, 0) / Math.max(window.length, 1));
  });
}

export function calculateTrueRanges(bars) {
  return (Array.isArray(bars) ? bars : []).map((bar, index, source) => {
    const high = Number(bar?.high || 0);
    const low = Number(bar?.low || 0);
    if (index === 0) return high - low;
    const previousClose = Number(source[index - 1]?.close || 0);
    return Math.max(high - low, Math.abs(high - previousClose), Math.abs(low - previousClose));
  });
}

/**
 * Squeeze releases for one aggregation, with NO chart-session cutoff.
 *
 * The cutoff, anchor price and bubble label stay in App.jsx: they are chart
 * presentation. This function is the part the Python scanner mirrors
 * (premarket_scanner.squeeze_release_events), so it must contain only facts
 * about the tape. tests/test_premarket_scanner.py pins the two together with
 * a fixture generated from this function, so the scanner table can never
 * disagree with the flame the chart draws.
 */
export function squeezeReleaseEvents(bars, minutes, { includeForming = false, timeframeBars: suppliedBars = null } = {}) {
  const source = Array.isArray(bars) ? bars : [];
  const span = Math.max(1, Number(minutes) || 1);
  // suppliedBars: an already-aggregated tape (the real daily bars TOS uses
  // for DAY). Defaults keep the scanner pinned to the original behaviour.
  const timeframeBars = Array.isArray(suppliedBars) && suppliedBars.length
    ? suppliedBars
    : aggregateChartBars(source, span);
  if (timeframeBars.length <= SQUEEZE_LENGTH) return [];

  const closes = timeframeBars.map((bar) => Number(bar.close || 0));
  const average = calculateRollingAverage(closes, SQUEEZE_LENGTH);
  const standardDeviation = calculateRollingStdDev(closes, SQUEEZE_LENGTH);
  const averageTrueRange = calculateRollingAverage(calculateTrueRanges(timeframeBars), SQUEEZE_LENGTH);
  const inSqueeze = timeframeBars.map((_, index) => (
    average[index] + 2 * standardDeviation[index] - (average[index] + 1.5 * averageTrueRange[index]) <= 0
  ));

  const lastSourceTime = Number(source[source.length - 1]?.time || timeframeBars[timeframeBars.length - 1]?.time || 0);
  return timeframeBars.flatMap((bar, index) => {
    if (index < SQUEEZE_LENGTH || inSqueeze[index] || !inSqueeze[index - 1]) return [];
    const bucketTime = Number(bar.time);
    const closeTime = bucketTime + span * 60;
    // Fire only when the bucket has CLOSED (live commit 461d4af): a forming
    // bucket's bands and ATR move with every tick, so its "release" flickered
    // in and out and the flame badge wandered between its live high and low.
    // The chart passes includeForming (2026-10-01, TOS parity): the TOS
    // script evaluates the developing bucket, so the flame fires - and can
    // vanish again - on the forming candle, exactly as TOS repaints it.
    if (!includeForming && closeTime > lastSourceTime) return [];
    return [{
      minutes: span,
      bucketTime,
      closeTime,
      tone: Number(bar.close) > Number(timeframeBars[index - 1]?.close) ? "bull" : "bear",
    }];
  });
}
