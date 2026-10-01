// Choosing which snapshot day the live OI heatmap renders.
//
// The recorder keeps writing rows after the session ends, but the broker stops
// publishing greeks overnight: every contract comes back delta 0. Just past
// midnight ET that lands a brand-new day in the payload whose rows all fall
// outside the 0.20-0.80 delta band the heatmap displays. Taking the newest day
// unconditionally therefore emptied the panel every night - measured
// 2026-08-20 00:19 ET, MRNA day 2026-08-20 held 694 rows with 0 in band while
// 2026-08-19 held 694 rows with 165 in band.
//
// Prefer the newest day that can actually render; fall back to the newest day
// so a genuinely empty payload still reports its own latest date.
export function latestUsableHeatmapDay(days, rows, { minDelta = 0.2, maxDelta = 0.8 } = {}) {
  const ordered = (Array.isArray(days) ? days : []).filter(Boolean);
  if (!ordered.length) return "";
  const newest = ordered[ordered.length - 1];
  const sourceRows = Array.isArray(rows) ? rows : [];
  if (!sourceRows.length) return newest;
  const usableDays = new Set();
  sourceRows.forEach((row) => {
    const delta = Math.abs(Number(row?.delta || 0));
    const strike = Number(row?.strike || 0);
    if (!Number.isFinite(strike) || strike <= 0) return;
    if (!Number.isFinite(delta) || delta < minDelta || delta > maxDelta) return;
    usableDays.add(String(row?.date || ""));
  });
  for (let index = ordered.length - 1; index >= 0; index -= 1) {
    if (usableDays.has(ordered[index])) return ordered[index];
  }
  return newest;
}
