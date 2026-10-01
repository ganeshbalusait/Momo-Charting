export const CLOUD_BAND_STUDIES = Object.freeze([
  // TOS plots the current 5-minute squeeze channel in the price pane.  Keep it
  // independent from the 15m+ MTF channels so a saved profile can control it
  // without changing the higher-timeframe overlays.
  { key: "5m", label: "5m", minutes: 5, indicatorKey: "cloudBands5m" },
  { key: "15m", label: "15m", minutes: 15, indicatorKey: "cloudBands15m" },
  { key: "30m", label: "30m", minutes: 30, indicatorKey: "cloudBands30m" },
  { key: "1h", label: "1h", minutes: 60, indicatorKey: "cloudBands1h" },
  { key: "2h", label: "2h", minutes: 120, indicatorKey: "cloudBands2h" },
  { key: "4h", label: "4h", minutes: 240, indicatorKey: "cloudBands4h" },
  { key: "day", label: "Daily", minutes: 1440, indicatorKey: "cloudBandsDay" },
]);

function average(values) {
  return values.reduce((sum, value) => sum + Number(value || 0), 0) / Math.max(values.length, 1);
}

/**
 * Calculate the fixed-timeframe CloudBands values used by the TOS study.
 *
 * Bollinger: Average(close, length) +/- 2 * StDev(close, length)
 * Keltner:   Average(close, length) +/- Average(TrueRange, length) * 1/1.5/2
 *
 * The caller supplies extended-hours candles and a display cutoff.  Therefore
 * premarket candles remain part of both the rolling calculation and output.
 */
export function calculateTosCloudBandPoints(timeframeBars, definition, length, cutoffTime = 0) {
  const source = Array.isArray(timeframeBars) ? timeframeBars : [];
  const period = Math.max(1, Math.min(200, Number(length) || 20));
  const minutes = Math.max(1, Number(definition?.minutes) || 1);
  const trueRanges = source.map((bar, index) => {
    const high = Number(bar?.high || 0);
    const low = Number(bar?.low || 0);
    if (index === 0) return high - low;
    const previousClose = Number(source[index - 1]?.close || 0);
    return Math.max(high - low, Math.abs(high - previousClose), Math.abs(low - previousClose));
  });

  return source.flatMap((bar, index) => {
    const endTime = Number(bar?.time) + minutes * 60;
    if (index < period - 1 || endTime <= Number(cutoffTime || 0)) return [];
    const startIndex = index - period + 1;
    const closes = source.slice(startIndex, index + 1).map((item) => Number(item?.close || 0));
    const middle = average(closes);
    const deviation = Math.sqrt(average(closes.map((close) => (close - middle) ** 2)));
    const range = average(trueRanges.slice(startIndex, index + 1));
    return [{
      time: Number(bar.time),
      endTime,
      timeframeKey: definition?.key,
      timeframe: definition?.label,
      indicatorKey: definition?.indicatorKey,
      upperBand: middle + 2 * deviation,
      lowerBand: middle - 2 * deviation,
      upperHigh: middle + range,
      lowerHigh: middle - range,
      upperMid: middle + 1.5 * range,
      lowerMid: middle - 1.5 * range,
      upperLow: middle + 2 * range,
      lowerLow: middle - 2 * range,
    }];
  });
}
