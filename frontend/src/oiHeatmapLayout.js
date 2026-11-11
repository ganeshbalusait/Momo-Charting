// Compact sizing for the Fast Options "live call vs put" heatmap (0-31 DTE).
// 2026-11-10: Ganesh said the heatmap took the whole screen and each column
// had a big box. These numbers are the single source for the grid; the
// matching row heights live in oiHeatmapCompact.css.

export const LIVE_DTE_STRIKE_COLUMN_PX = 64;
// 98px: the widest cell line ("VOL 231,240 · OI 11,8xx") measured 90px on a
// phone; narrower columns cut the OI number off.
export const LIVE_DTE_MIN_COLUMN_PX = 98;

export function liveDteGridColumns(expiryCount) {
  const count = Math.max(0, Math.floor(Number(expiryCount) || 0));
  return `${LIVE_DTE_STRIKE_COLUMN_PX}px repeat(${count}, minmax(${LIVE_DTE_MIN_COLUMN_PX}px, 1fr))`;
}

// Inline min-width so the grid scrolls sideways instead of squeezing columns
// below LIVE_DTE_MIN_COLUMN_PX (the grid itself is overflow:hidden).
export function liveDteGridMinWidth(expiryCount) {
  const count = Math.max(0, Math.floor(Number(expiryCount) || 0));
  return `max(100%, ${LIVE_DTE_STRIKE_COLUMN_PX + count * LIVE_DTE_MIN_COLUMN_PX}px)`;
}

// The small C / P and VOL / OI lines in a heatmap box. Two six-figure numbers
// (SPY, QQQ) measured wider than the box, so from 100,000 up they shorten to
// 484K / 1.2M. Below that the exact number fits and stays exact.
const SMALL_LINE_FORMAT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
export function formatHeatmapSmallNumber(value) {
  if (value == null || value === "") return "--";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  const size = Math.abs(numeric);
  if (size >= 999_500) return `${(numeric / 1_000_000).toFixed(1).replace(/.0$/, "")}M`;
  if (size >= 100_000) return `${Math.round(numeric / 1_000)}K`;
  return SMALL_LINE_FORMAT.format(numeric);
}
