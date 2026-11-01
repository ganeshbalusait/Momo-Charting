// Poll a known gap independently of quote arrival. A broker refresh can
// acknowledge before its new candles reach the cache; waiting for another
// throttled quote request then adds ten seconds to an otherwise ready chart.
export function nextChartGapPoll(previous, liveTime, historyTime) {
  const live = Number(liveTime), history = Number(historyTime);
  if (!Number.isFinite(live) || !Number.isFinite(history)
      || history <= 0 || live - history <= 90) return null;
  const attempts = previous?.historyTime === history ? previous.attempts + 1 : 1;
  return { historyTime: history, attempts,
    delayMs: attempts <= 5 ? 1000 : attempts <= 8 ? 2500 : 10000 };
}
