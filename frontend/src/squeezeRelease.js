// Explicit ".js" extension: node --test (and the fixture generator) resolve
// ESM imports literally; only Vite tolerates the bare specifier.
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
 * about the tape.
 *
 * A release is a bucket-CLOSE fact: the forming bucket's bands and ATR move
 * with every tick, so a release on it would flicker in and out.
 */
export function squeezeReleaseEvents(bars, minutes) {
  const source = Array.isArray(bars) ? bars : [];
  const span = Math.max(1, Number(minutes) || 1);
  const timeframeBars = aggregateChartBars(source, span);
  if (timeframeBars.length <= SQUEEZE_LENGTH) return [];

  const closes = timeframeBars.map((bar) => Number(bar.close || 0));
  const average = calculateRollingAverage(closes, SQUEEZE_LENGTH);
  const standardDeviation = calculateRollingStdDev(closes, SQUEEZE_LENGTH);
  const averageTrueRange = calculateRollingAverage(calculateTrueRanges(timeframeBars), SQUEEZE_LENGTH);
  const inSqueeze = timeframeBars.map((_, index) => (
    average[index] + 2 * standardDeviation[index] - (average[index] + 1.5 * averageTrueRange[index]) <= 0
  ));

  const lastSourceTime = Number(source[source.length - 1]?.time || 0);
  return timeframeBars.flatMap((bar, index) => {
    if (index < SQUEEZE_LENGTH || inSqueeze[index] || !inSqueeze[index - 1]) return [];
    const bucketTime = Number(bar.time);
    const closeTime = bucketTime + span * 60;
    if (closeTime > lastSourceTime) return [];
    return [{
      minutes: span,
      bucketTime,
      closeTime,
      tone: Number(bar.close) > Number(timeframeBars[index - 1]?.close) ? "bull" : "bear",
    }];
  });
}
