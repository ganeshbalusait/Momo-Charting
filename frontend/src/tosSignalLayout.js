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
