import { chartSourceBarSpacingMinutes } from "./chartAggregation.js";

export const OI_CHART_FOUR_HOUR_SEED_MIN_BARS = 80;

// The backend has shipped the study tape at THIRTY- and FIVE-minute cadences.
// It has never shipped a one-minute one: that shape only ever appears when
// normalizeOiChartPayload aliases the live tape in for a response that omits
// studyBars. Five minutes is therefore the finest a genuine seed can be.
export const OI_CHART_STUDY_SEED_MIN_CADENCE_MINUTES = 5;

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

// The seed fetch can resolve with an EMPTY study tape: the server answers from
// a cold cache right after a restart, or a transient 5xx/timeout is laundered
// into a warming payload whose studyBars is []. Both used to strand the pane
// permanently, because the effect that requests the seed re-runs only when
// symbol, timeframe or studyBars.length changes and an empty result changes
// none of them. The pane then showed a populated OHLC header and live price
// lines over a blank canvas with no price axis, while the 5m pane beside it
// (which never needs a seed) rendered normally.
//
// A blank chart is never an acceptable resting state, so retries continue
// indefinitely - but capped, so a server that stays cold is polled gently
// rather than hit in a tight loop.
export const OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS = 20_000;
export const OI_CHART_STUDY_SEED_RETRY_BASE_DELAY_MS = 1_500;

export function nextStudySeedRetryDelayMs(attempt) {
  const parsed = Number(attempt);
  const safeAttempt = Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : 1;
  return Math.min(
    OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS,
    OI_CHART_STUDY_SEED_RETRY_BASE_DELAY_MS * 2 ** (safeAttempt - 1),
  );
}

export function oiChartHasInitialStudySeed(payload) {
  const enoughBars = Array.isArray(payload?.studyBars)
    && payload.studyBars.length >= OI_CHART_FOUR_HOUR_SEED_MIN_BARS;
  if (!enoughBars) return false;
  // normalizeOiChartPayload intentionally aliases bars into studyBars when a
  // compact 5m response omits the study tape. That fallback is useful for
  // ordinary intraday aggregation but is not a genuine 30m seed for 4H.
  if (payload?.initialSlim === true) return payload?.initialStudySeed === true;
  // The flag above is only present on a response. The effect that gates the
  // 4H seed request holds the TAPE, not the response, so it asks with a
  // synthetic `{ studyBars }` rebuilt from React state and that object carries
  // no flags at all. The aliased one-minute tape is 900 rows, which clears the
  // count test above, so a pane opened on 5m and switched to 4H decided it
  // already had a seed and never fetched one - 6 candles instead of ~1,100.
  //
  // Cadence survives losing the flag. Only reject a tape we can positively
  // measure as finer than a real study tape; an unmeasurable one (too few
  // rows, duplicate timestamps) falls back to the limit and stays accepted, so
  // this can never withhold a seed the pane already has.
  return chartSourceBarSpacingMinutes(
    payload.studyBars,
    OI_CHART_STUDY_SEED_MIN_CADENCE_MINUTES,
  ) >= OI_CHART_STUDY_SEED_MIN_CADENCE_MINUTES;
}
