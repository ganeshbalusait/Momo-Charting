/**
 * Stitch a deep (often 30-minute) study tape in front of the fine live tape.
 *
 * TOS seeds `ExpAverage(close(period = FOUR_HOURS), 20)` and friends from the
 * chart's full secondary history. The live one-minute tape covers about two
 * days, so on its own it leaves 1h/2h/4h/Day studies barely warmed up (or, for
 * a 20-bar squeeze, never warmed up at all). The deep tape supplies only the
 * history BEFORE the first live bar, so recent candles keep their fine
 * resolution and no time is counted twice.
 *
 * Use the result for secondary aggregations at or above the deep tape's
 * cadence; the chart's own timeframe should keep using the live bars.
 *
 * Only `maxHistoryDays` in front of the live tape are kept: 60 days is still
 * 150+ four-hour candles (an EMA20 is fully converged long before that), while
 * the full ~260-day tape made every study re-aggregate thousands of extra bars
 * on each timeframe switch.
 */
export const MTF_STUDY_HISTORY_DAYS = 60;

export function mergeStudyHistoryBars(historyBars, liveBars, maxHistoryDays = MTF_STUDY_HISTORY_DAYS) {
  const live = (Array.isArray(liveBars) ? liveBars : [])
    .filter((bar) => Number.isFinite(Number(bar?.time)));
  const history = Array.isArray(historyBars) ? historyBars : [];
  if (!history.length) return live;
  if (!live.length) return history;
  const firstLiveTime = live.reduce(
    (earliest, bar) => Math.min(earliest, Number(bar.time)),
    Number.POSITIVE_INFINITY,
  );
  const sortedHistory = history
    .filter((bar) => Number.isFinite(Number(bar?.time)))
    .slice()
    .sort((left, right) => Number(left.time) - Number(right.time));
  // A history bar spans [time, nextTime). Drop the one that straddles the
  // first live bar as well, otherwise its OHLC would be counted twice.
  // Use the smallest recent step as the cadence so an overnight or weekend
  // gap between the last two bars is never mistaken for the bar width.
  const recentSteps = sortedHistory.slice(-12)
    .map((bar, index, items) => (index ? Number(bar.time) - Number(items[index - 1].time) : 0))
    .filter((step) => step > 0);
  const spacing = recentSteps.length ? Math.max(60, Math.min(...recentSteps)) : 60;
  const earliestTime = Number.isFinite(Number(maxHistoryDays)) && Number(maxHistoryDays) > 0
    ? firstLiveTime - Number(maxHistoryDays) * 86_400
    : Number.NEGATIVE_INFINITY;
  const prefix = sortedHistory.filter((bar) => (
    Number(bar.time) >= earliestTime && Number(bar.time) + spacing <= firstLiveTime
  ));
  if (!prefix.length) return live;
  return [...prefix, ...live].sort((left, right) => Number(left.time) - Number(right.time));
}
