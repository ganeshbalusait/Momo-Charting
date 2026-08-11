export const OI_CHART_FOUR_HOUR_SEED_MIN_BARS = 80;

export function oiChartNeedsInitialStudySeed(timeframeMinutes) {
  return Number(timeframeMinutes) === 240;
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
