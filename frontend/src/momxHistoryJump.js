// "Jump to" - move the History page to a time, rather than filter to it.
//
// THE DISTINCTION IS THE WHOLE DESIGN. A day holds ~11,770 snapshots and a page
// is 400. A time FILTER would either search the 3% already in the browser and
// report it as the day, or force every counter on that bar - "44 entries",
// "showing 1-44 of 44", Newer/Older, the NEWS count - to be redefined at once.
// Choosing an offset is exactly what the pager already does, so all of them
// stay arithmetically correct with no changes and nothing is ever hidden.
//
// A rival design of preset buttons (OPEN, POWER HOUR...) was built and tested
// and FAILED its own headline case: because pages count backwards from the
// newest row, the page the OPEN button landed on covered 09:36-09:58. A button
// named OPEN could not show the open. Hence a box you type into, plus a
// read-only line of session counts you can click.

/** The page window the server pages in. Sent on the payload; never hardcoded. */
export const FALLBACK_PAGE_SIZE = 400;

/**
 * "945" / "9:45" / "0945" / "09:45" -> "09:45". A bare "9" -> "09:00".
 *
 * Returns null for anything it cannot read, so the box can say so instead of
 * jumping somewhere he did not ask for.
 */
export function parseJumpTime(text) {
  const raw = String(text ?? "").trim();
  if (raw === "") return null;
  const digits = raw.replace(/[^0-9]/g, "");
  if (digits.length === 0 || digits.length > 4) return null;
  let hours;
  let minutes;
  if (raw.includes(":")) {
    const [left, right] = raw.split(":");
    hours = Number(left);
    minutes = Number(right === "" ? 0 : right);
  } else if (digits.length <= 2) {
    hours = Number(digits);
    minutes = 0;
  } else {
    hours = Number(digits.slice(0, digits.length - 2));
    minutes = Number(digits.slice(-2));
  }
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0");
}

/**
 * What the box shows after each keystroke: digits only, colon added for him.
 *
 * The iPhone keypad the box asks for (inputMode="numeric") has NO colon key,
 * so "type 09:30" was physically impossible there (his screenshot, 2026-09-05).
 * Typing 0930 now reads 09:30 as the digits land. Three digits are ambiguous
 * ("093" could be 09:3_ or 0:93) and are split where the hour is legal, so
 * "930" shows 9:30 and "093" shows 09:3. A pasted "09:30" survives unchanged.
 */
export function formatJumpInput(text) {
  const digits = String(text ?? "").replace(/[^0-9]/g, "").slice(0, 4);
  if (digits.length <= 2) return digits;
  if (digits.length === 4) return digits.slice(0, 2) + ":" + digits.slice(2);
  return Number(digits.slice(0, 2)) <= 23
    ? digits.slice(0, 2) + ":" + digits.slice(2)
    : digits.slice(0, 1) + ":" + digits.slice(1);
}

/**
 * The loaded entry at or after `clock`, for the ticker search (where the
 * server sends no timeIndex - a ticker's rows span days).
 *
 * `day` pins the search to one date; without it the NEWEST day among the
 * entries is used, which is the day at the top of the screen. Same
 * "at or after, else the last one" rule as jumpTarget. Null when nothing on
 * that day is loaded at all.
 */
export function jumpTargetInEntries(entries, clock, day) {
  const rows = (Array.isArray(entries) ? entries : []).filter(
    (entry) => entry && typeof entry.timeLabel === "string" && entry.timeLabel.length === 5,
  );
  if (!clock || rows.length === 0) return null;
  let wanted = typeof day === "string" && day ? day : null;
  if (!wanted) {
    for (const entry of rows) {
      if (typeof entry.date === "string" && (wanted === null || entry.date > wanted)) wanted = entry.date;
    }
  }
  const sameDay = rows.filter((entry) => !wanted || entry.date === wanted || entry.date == null);
  if (sameDay.length === 0) return null;
  let best = null;
  let last = null;
  for (const entry of sameDay) {
    if (last === null || entry.timeLabel > last.timeLabel) last = entry;
    if (entry.timeLabel >= clock && (best === null || entry.timeLabel < best.timeLabel)) best = entry;
  }
  const hit = best || last;
  return { clock: hit.timeLabel, day: hit.date || null, past: best === null };
}

/**
 * The index entry at or after `clock`, or null.
 *
 * "At or AFTER" on purpose: the archive has quiet stretches, and landing on the
 * next moment that WAS recorded keeps them visible. Reporting "nothing found"
 * for 09:45 on a day whose next entry is 09:46 would be a lie about the data.
 */
export function jumpTarget(timeIndex, clock) {
  const index = Array.isArray(timeIndex) ? timeIndex : [];
  if (!clock || index.length === 0) return null;
  for (const entry of index) {
    if (Array.isArray(entry) && typeof entry[0] === "string" && entry[0] >= clock) {
      return { clock: entry[0], position: entry[1], rows: entry[2] };
    }
  }
  // Past the last entry: land on the final page rather than refusing.
  const last = index[index.length - 1];
  return { clock: last[0], position: last[1], rows: last[2], past: true };
}

/**
 * The offset that puts `position` at the TOP of the page.
 *
 * Inverts the server's window (momx/history.py::_page, ascending branch):
 * `end = total - offset; window = rows[end - size : end]`. So for the window to
 * begin at `position`, `offset = total - position - size`, floored at zero.
 *
 * Verified against the live archive: 2026-09-04, 11,770 rows, page size 400,
 * "09:45" -> offset 10572 -> page runs 09:45..10:04, pager reads
 * "showing 799-1198 of 11770".
 */
export function jumpOffset(position, totalRows, pageSize) {
  const total = Number(totalRows);
  const size = Number(pageSize) || FALLBACK_PAGE_SIZE;
  const at = Number(position);
  if (!Number.isFinite(total) || !Number.isFinite(at)) return 0;
  return Math.max(0, Math.trunc(total - at - size));
}

/** Everything the box needs for one keystroke: where to go, and what it hit. */
export function resolveJump(text, payload) {
  const clock = parseJumpTime(text);
  if (!clock) return { error: "Enter a time like 09:45" };
  const target = jumpTarget(payload && payload.timeIndex, clock);
  if (!target) return { error: "No times recorded for this day yet" };
  return {
    clock,
    landedOn: target.clock,
    offset: jumpOffset(target.position, payload.totalRows, payload.pageSize),
    // True when he asked for a moment the archive skipped - the box repaints to
    // the time he actually got, so the gap is visible rather than silent.
    moved: target.clock !== clock,
  };
}

/**
 * The clock the current page STARTS at - the box's resting value.
 *
 * Taken as the MINIMUM `at` on the page, not `rows[0]`, so it survives any
 * client-side sort. The box is therefore never empty: the format he should
 * type is always sitting in it.
 */
export function pageStartClock(rows) {
  let best = null;
  for (const row of Array.isArray(rows) ? rows : []) {
    const stamp = String((row && row.at) || "");
    const clock = stamp.slice(11, 16);
    if (clock.length !== 5) continue;
    if (best === null || clock < best) best = clock;
  }
  return best;
}

/** `[[name, rows], ...]` -> the visible session line, zero counts dropped. */
export function sessionLine(sessions) {
  return (Array.isArray(sessions) ? sessions : [])
    .filter((entry) => Array.isArray(entry) && Number(entry[1]) > 0)
    .map((entry) => ({ name: entry[0], rows: Number(entry[1]) }));
}

/** The first index position inside a named session window, for its click. */
export function sessionStart(timeIndex, sessions, name) {
  const index = Array.isArray(timeIndex) ? timeIndex : [];
  const bounds = SESSION_BOUNDS[name];
  if (!bounds || index.length === 0) return null;
  for (const entry of index) {
    const clock = entry && entry[0];
    if (typeof clock !== "string" || clock.length !== 5) continue;
    const minute = Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3));
    if (minute >= bounds[0] && minute < bounds[1]) return entry[1];
  }
  return null;
}

// Mirrors momx/history.py::SESSION_WINDOWS. Kept in step by
// test_history_session_windows_match_the_server in momxHistoryJump.test.js,
// which reads the python constant rather than trusting this copy.
export const SESSION_BOUNDS = {
  Overnight: [0, 4 * 60],
  Pre: [4 * 60, 9 * 60 + 30],
  Open: [9 * 60 + 30, 10 * 60],
  Morning: [10 * 60, 12 * 60],
  Midday: [12 * 60, 15 * 60],
  Power: [15 * 60, 16 * 60],
  After: [16 * 60, 24 * 60],
};
