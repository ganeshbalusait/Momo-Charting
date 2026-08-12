export const OI_CHART_FOUR_HOUR_SEED_MIN_BARS = 80;

// The 30-minute study tape is the history source for EVERY timeframe at least
// as coarse as its own cadence, not just 4H:
//
//   30m / 1h / 2h / 4h  aggregate their candles from it
//   D / W / M           draw candles from dailyBars, but their studies read it
//
// This returned true only for 240, so every other higher timeframe rendered
// from the fast-paint slice, whose studyBars is empty. Those views then fell
// back to the one-minute live tape - capped at ~5 days by the ingestion
// contract - which is roughly 17-20 candles on 4H and fewer still above it.
// That is the "higher timeframes only show ~20 candles" report.
export function oiChartNeedsInitialStudySeed(timeframeMinutes) {
  return Number(timeframeMinutes) >= 30;
}

export function oiChartHasInitialStudySeed(payload) {
  const enoughBars = Array.isArray(payload?.studyBars)
    && payload.studyBars.length >= OI_CHART_FOUR_HOUR_SEED_MIN_BARS;
  if (!enoughBars) return false;
  // normalizeOiChartPayload intentionally aliases bars into studyBars when a
  // compact 5m response omits the study tape. That fallback is useful for
  // ordinary intraday aggregation but is not a genuine 30m seed for 4H.
  if (payload?.initialSlim === true) return payload?.initialStudySeed === true;
  return true;
}
