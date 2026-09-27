// Where the scanner's A+ / A letter APPEARS, as chart markers.
//
// His ask (2026-09-24): "Fix the chart A/A+ inside the chart". Until now the
// chart only printed the letter on a CALL bubble/arrow, so TSLA's A at 09:47
// ET on 2026-09-23 (Daily 2 #4) and FSLY's afternoon A+ at 13:19 showed no
// letter at all - no arrow fired on those candles.
//
// Input: the /api/momx-scanner/grade-tape entries the chart already loads
// ({t, letter, _sec}, one every ~5 minutes). Output: one marker each time the
// letter STEPS UP into A or A+ (from nothing / B, or A -> A+), at most one per
// letter per MIN_GAP_SECONDS, so a letter flickering A -> none -> A on
// neighbouring snapshots does not paint a row of bubbles.

export const GRADE_MARKER_LETTERS = Object.freeze(["A+", "A"]);
const RANK = { "A+": 2, A: 1 };
const MIN_GAP_SECONDS = 60 * 60;

function entrySeconds(entry) {
  if (!entry || typeof entry !== "object") return NaN;
  if (Number.isFinite(entry._sec)) return entry._sec;
  const ms = typeof entry.t === "string" ? Date.parse(entry.t) : NaN;
  return Number.isFinite(ms) ? ms / 1000 : NaN;
}

/** [{ time (epoch s of the snapshot), letter }] - the step-ups into A / A+. */
export function gradeStepMarkers(entries) {
  const list = (Array.isArray(entries) ? entries : [])
    .map((entry) => ({ sec: entrySeconds(entry), letter: entry && entry.letter }))
    .filter((entry) => Number.isFinite(entry.sec))
    .sort((a, b) => a.sec - b.sec);
  const out = [];
  const lastShown = {};
  let previousRank = 0;
  let previousDay = null;
  for (const entry of list) {
    const day = new Date(entry.sec * 1000).toLocaleDateString("en-US", { timeZone: "America/New_York" });
    if (day !== previousDay) {
      previousRank = 0; // a new ET day starts from nothing
      previousDay = day;
    }
    const rank = RANK[entry.letter] || 0;
    if (rank > previousRank && rank > 0) {
      const last = lastShown[entry.letter];
      if (last === undefined || entry.sec - last >= MIN_GAP_SECONDS) {
        out.push({ time: entry.sec, letter: entry.letter });
        lastShown[entry.letter] = entry.sec;
      }
    }
    previousRank = rank;
  }
  return out;
}

/** The chart candle (bar.time, epoch s) a snapshot falls in: the last bar at or before it. */
export function candleTimeFor(seconds, barTimesAscending) {
  const times = Array.isArray(barTimesAscending) ? barTimesAscending : [];
  let lo = 0;
  let hi = times.length - 1;
  let found = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= seconds) {
      found = times[mid];
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}
