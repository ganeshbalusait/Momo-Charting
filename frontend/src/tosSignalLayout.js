const HIGHER_TIMEFRAME_RANK = Object.freeze({
  "15": 0,
  "30": 1,
  "1H": 2,
  "2H": 3,
  "4H": 4,
});
const TOS_FAMILY_STACK_RANK = Object.freeze({
  "4x8": 0,
  "9x20": 1,
});

/**
 * Preserve the exact ThinkScript meaning of every bubble. The bubble suffix is
 * the aggregation whose EMA crossed; the next aggregation only determines
 * compact C/P versus confirmed CALL/PUT. Simultaneous bubbles stay on the same
 * source candle and are ordered yellow 4x8 before cyan 9x20 for stable stacking.
 */
export function layoutTosMtfChartSignals(signals) {
  return (Array.isArray(signals) ? signals : []).filter(Boolean).sort((left, right) => (
    (Number(left.time) || 0) - (Number(right.time) || 0)
    || (TOS_FAMILY_STACK_RANK[String(left.family || "")] ?? 99)
      - (TOS_FAMILY_STACK_RANK[String(right.family || "")] ?? 99)
    || (HIGHER_TIMEFRAME_RANK[String(left.timeframe || "").toUpperCase()] ?? 99)
      - (HIGHER_TIMEFRAME_RANK[String(right.timeframe || "").toUpperCase()] ?? 99)
  ));
}

/**
 * Pin every signal to the chart candle that contains it. The backend stamps a
 * TOS MTF label on the 5-minute candle where the developing cross turned true
 * (09:10, say); the native primitive only draws a bubble whose time equals a
 * candle's time, so on a 15m/30m/1H chart - or a 1m chart with a quiet minute -
 * the label would vanish. Snap to the last candle at or before the stamp.
 * Bars must be sorted ascending; signals older than the first bar stay as is.
 */
export function snapTosMtfSignalsToBars(signals, bars) {
  const list = (Array.isArray(signals) ? signals : []).filter(Boolean);
  const times = (Array.isArray(bars) ? bars : [])
    .map((bar) => Number(bar?.time))
    .filter((time) => Number.isFinite(time));
  if (!list.length || !times.length) return list;
  return list.map((signal) => {
    const time = Number(signal.time);
    if (!Number.isFinite(time) || time < times[0]) return signal;
    let low = 0;
    let high = times.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if (times[mid] <= time) low = mid;
      else high = mid - 1;
    }
    return times[low] === time ? signal : { ...signal, time: times[low] };
  });
}
