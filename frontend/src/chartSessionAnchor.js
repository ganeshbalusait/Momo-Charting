// MomoX does not run its levels across the whole chart. Every level — OI walls,
// prior-period OHLC, pivots, Persons ranges, MTF MAs, expected-move bands —
// begins at the current session's 4:00 AM ET premarket open and runs right into
// the price axis. That start point is what makes the level read as "today's
// level" rather than a line with no time meaning, so it is computed once here
// and shared by every level family instead of each picking its own anchor.

// HHMM, matching the *RthStartTime option convention used across the studies.
export const MOMOX_LEVEL_ANCHOR_CLOCK = 400;

// A daily, weekly, or monthly bar has no 4:00 AM to anchor to — the whole bar
// IS the session. Anchoring there would collapse every level into a stub at the
// right edge, so those timeframes keep full-width lines.
export const MOMOX_LEVEL_ANCHOR_MAX_MINUTES = 1440;

function clockToHhmm(clock) {
  const [hours, minutes] = String(clock || "").split(":").map(Number);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 100 + minutes;
}

/**
 * Unix time of the bar that MomoX-style levels should start at, or null when
 * levels should stay full-width.
 *
 * Anchors to the latest session present in `bars`, not to "today": scrolling
 * back through history has to keep the levels attached to the session being
 * looked at, and a chart loaded outside market hours has no bar for today.
 */
export function momoxLevelAnchorTime(bars, {
  dateFormatter,
  timeFormatter,
  timeframeMinutes,
  anchorClock = MOMOX_LEVEL_ANCHOR_CLOCK,
} = {}) {
  const source = Array.isArray(bars) ? bars : [];
  if (!source.length || !dateFormatter || !timeFormatter) return null;
  if (Number(timeframeMinutes) >= MOMOX_LEVEL_ANCHOR_MAX_MINUTES) return null;

  const dateOf = (bar) => dateFormatter.format(new Date(Number(bar?.time) * 1000));
  const latestDate = dateOf(source[source.length - 1]);

  let sessionStart = null;
  for (let index = 0; index < source.length; index += 1) {
    const bar = source[index];
    const time = Number(bar?.time);
    if (!Number.isFinite(time) || dateOf(bar) !== latestDate) continue;
    // First bar of the session, kept as the fallback for a ticker whose
    // premarket is empty — a 9:30 open still has to anchor somewhere.
    if (sessionStart == null) sessionStart = time;
    const hhmm = clockToHhmm(timeFormatter.format(new Date(time * 1000)));
    if (hhmm != null && hhmm >= Number(anchorClock)) return time;
  }
  return sessionStart;
}

/**
 * Trim a per-bar study series (Persons ranges, pivot points) to the session
 * anchor. These families draw as line series rather than price lines, so they
 * cannot use levelSegments — dropping the earlier points is what confines them
 * to the current session the way MomoX does.
 *
 * Returns the input untouched when there is no anchor (D/W/M) or when trimming
 * would empty the series: a level that renders full-width is wrong, but a level
 * that silently disappears is worse.
 */
export function trimPointsToAnchor(points, anchorTime) {
  const source = Array.isArray(points) ? points : [];
  const anchor = Number(anchorTime);
  if (!source.length || !Number.isFinite(anchor) || anchor <= 0) return source;
  const trimmed = source.filter((point) => Number(point?.time) >= anchor);
  return trimmed.length ? trimmed : source;
}

// lightweight-charts LineStyle -> the dash pattern the native primitive draws.
// Converting a price line to a segment otherwise silently flattens every dashed
// level (prior high/low, expected move, Persons ranges) to solid.
const DASH_BY_LINE_STYLE = Object.freeze({
  0: [],
  1: [1, 2],
  2: [4, 3],
  3: [8, 4],
  4: [1, 5],
});

export function lineStyleDashArray(lineStyle) {
  return DASH_BY_LINE_STYLE[Number(lineStyle)] || [];
}
