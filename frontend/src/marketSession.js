// Is the US equity market open right now, and what is the newest bar it could
// possibly have produced? Pure, clock-injected, no network.
//
// WHY THIS EXISTS. The MomX board showed an amber "DATA AS OF FRI 8:00 PM ET"
// chip on a Saturday and the trader read it as a fault: "it's scanner is not
// running?" (2026-09-05). It was running - verified live: the worker rebuilt
// and wrote artifacts/momx_board_cache/Watchlist.json at 13:29 ET while the
// board reported generatedAt Sat 13:28 ET, tapeAsOf Fri 20:00 ET, errors 0.
// Friday 20:00 ET is simply the last bar of the week.
//
// The old rule was "newest bar older than 30 minutes = stale, paint it amber",
// which is true every single evening and all weekend. A warning that fires on
// the normal case teaches you to ignore it, and this one instead made him
// think the scanner had died. The board must still SHOUT when the tape really
// has stalled mid-session, so the question this module answers is not "how old
// is the bar" but "has the market had a chance to produce a newer one".

export const MARKET_TIMEZONE = "America/New_York";

// The app's REGULAR session: premarket from 04:00 ET, post-market to 20:00 ET.
// This is the window the chip calls "open" for TONE purposes only.
export const SESSION_OPEN_MINUTE = 4 * 60;
export const SESSION_CLOSE_MINUTE = 20 * 60;

// THE DATA WEEK IS NOT THE SESSION, and conflating them was a real bug.
//
// The first cut of this module treated 20:00-04:00 on a weeknight as "market
// closed" and therefore expected no newer bar until 04:00. That is wrong: the
// board ingests Alpaca's overnight (BOATS) feed across exactly that window -
// momx/feed.py:133 INTRADAY_OVERNIGHT_FEED = "boats", and feed.py:1075
// documents the 5m tape as "SIP 04:00-20:00 + BOATS 20:00-04:00". Measured on
// 2026-09-05: BOATS returned 94 five-minute bars from 20:00 to 03:55 ET on
// each of the 09-01, 09-02 and 09-03 nights, and a board built Thu 09-03 at
// 21:01 ET carried a 20:40 ET bar.
//
// So under the first cut, a tape that died at the overnight open showed a grey
// "nothing is wrong" chip until 04:00 - a REGRESSION against the plain
// 30-minute rule it replaced, in the window that feeds the 2H/4H labels.
//
// The data week runs CONTINUOUSLY from Sunday 20:00 ET to Friday 20:00 ET.
// Only the Friday-night-to-Sunday-night gap has no bars.
export const DATA_WEEK_OPEN_WEEKDAY = "Sun";
export const DATA_WEEK_CLOSE_WEEKDAY = "Fri";

// How far behind the newest POSSIBLE bar the tape may sit before it is called
// stale. 30 minutes inside regular hours, as before. The overnight branch is
// wider because BOATS is published late by design - momx/feed.py:129 sets
// FEED_RECENT_DELAY_MINUTES["boats"] = 16 - and a build cycle sits on top of
// that, so 30 would cry wolf every night.
export const TAPE_SLACK_MS = 30 * 60 * 1000;
export const OVERNIGHT_SLACK_MS = 45 * 60 * 1000;

const DAY_MS = 24 * 60 * 60 * 1000;

const ET_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: MARKET_TIMEZONE,
  weekday: "short",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Wall-clock ET fields for an instant. */
export function etParts(ms) {
  const out = {};
  for (const part of ET_PARTS.formatToParts(new Date(ms))) out[part.type] = part.value;
  return {
    weekday: out.weekday,
    year: Number(out.year),
    month: Number(out.month),
    day: Number(out.day),
    // en-US hour12:false emits "24" for midnight in some engines.
    hour: Number(out.hour) % 24,
    minute: Number(out.minute),
  };
}

/**
 * The instant at which the ET wall clock reads the given date and time.
 *
 * Works by treating the fields as if they were UTC, reading what ET calls that
 * instant, and correcting by the difference - so it tracks DST without a table.
 * A single correction is exact except within the one ambiguous hour of a DST
 * transition, which is 01:00-02:00 ET on two Sundays a year and never a
 * session boundary.
 */
export function etWallClockMs(year, month, day, hour, minute) {
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  const seen = etParts(guess);
  const asUtc = Date.UTC(seen.year, seen.month - 1, seen.day, seen.hour, seen.minute);
  return guess + (guess - asUtc);
}

export function isTradingDay(weekday) {
  return weekday !== "Sat" && weekday !== "Sun";
}

/**
 * Is this instant inside the continuous data week - Sunday 20:00 ET through
 * Friday 20:00 ET - during which SOME feed is producing bars?
 *
 * Monday to Thursday are covered all 24 hours (BOATS overnight either side of
 * the SIP day). Friday stops at 20:00. Saturday has nothing. Sunday only picks
 * up once the overnight session opens at 20:00.
 */
export function inDataWeek(parts) {
  const minuteOfDay = parts.hour * 60 + parts.minute;
  if (parts.weekday === "Sat") return false;
  if (parts.weekday === "Sun") return minuteOfDay >= SESSION_CLOSE_MINUTE;
  if (parts.weekday === "Fri") return minuteOfDay < SESSION_CLOSE_MINUTE;
  return true;
}

/** Inside the data week but outside regular hours - i.e. on the BOATS tape. */
export function isOvernight(parts) {
  const minuteOfDay = parts.hour * 60 + parts.minute;
  return (
    inDataWeek(parts)
    && (minuteOfDay >= SESSION_CLOSE_MINUTE || minuteOfDay < SESSION_OPEN_MINUTE)
  );
}

/**
 * Is the extended session running right now?
 *
 * Weekends are closed; weekdays are open between 04:00 and 20:00 ET. Market
 * HOLIDAYS are deliberately not modelled - there is no holiday calendar in
 * this repo, and getting one wrong in the un-safe direction would silence a
 * real stall. On a holiday this returns `open` and the board falls back to the
 * old loud behaviour, which is the safe way to be wrong.
 */
export function marketSessionState(nowMs) {
  const parts = etParts(nowMs);
  const minuteOfDay = parts.hour * 60 + parts.minute;
  const open =
    isTradingDay(parts.weekday)
    && minuteOfDay >= SESSION_OPEN_MINUTE
    && minuteOfDay < SESSION_CLOSE_MINUTE;
  return { open, weekday: parts.weekday, minuteOfDay, parts };
}

/**
 * The newest bar the market could have produced by `nowMs`.
 *
 * Inside the DATA WEEK (Sun 20:00 -> Fri 20:00 ET) that is simply now, because
 * either the SIP day or the BOATS overnight tape is producing bars. Only the
 * Friday-night-to-Sunday-night gap has a fixed answer: Friday's 20:00 close.
 *
 * Two cases a plain age threshold gets wrong, both covered by tests:
 *   - Tuesday 23:55 holding a tape frozen at Tuesday 20:00 is only ~4 hours
 *     old, but the overnight feed has been dead for four hours. STALE.
 *   - Saturday afternoon holding Friday 20:00 is 17 hours old and perfectly
 *     healthy, because nothing has traded since. NOT stale.
 */
export function lastExpectedBarMs(nowMs) {
  const parts = etParts(nowMs);
  // Inside the data week SOMETHING is trading - regular hours or the overnight
  // tape - so the newest possible bar is simply now. This is what makes a dead
  // overnight feed visible; capping at 20:00 hid it until 04:00.
  if (inDataWeek(parts)) return nowMs;
  // The weekend gap. Walk back to the most recent Friday's 20:00 close, today
  // included - Friday 22:00 expects Friday's own close, not Thursday's.
  let cursor = nowMs;
  for (let step = 0; step < 8; step += 1) {
    const day = etParts(cursor);
    if (day.weekday === DATA_WEEK_CLOSE_WEEKDAY) {
      return etWallClockMs(day.year, day.month, day.day, 20, 0);
    }
    cursor -= DAY_MS;
  }
  return nowMs;
}

/**
 * How the tape stands against the market, as one of three states:
 *
 *   "live"   - a session is running and the tape is keeping up
 *   "closed" - the market is shut and the tape holds the last session's data.
 *              NORMAL. Every evening and every weekend lands here.
 *   "stale"  - the tape is more than TAPE_SLACK_MS behind the newest bar the
 *              market could have produced. A real problem, whatever the hour.
 */
export function tapeState(tapeMs, nowMs) {
  if (!Number.isFinite(tapeMs)) return null;
  const parts = etParts(nowMs);
  const expected = lastExpectedBarMs(nowMs);
  const behindMs = expected - tapeMs;
  const slack = isOvernight(parts) ? OVERNIGHT_SLACK_MS : TAPE_SLACK_MS;
  if (behindMs > slack) return { state: "stale", behindMs, ageMs: nowMs - tapeMs };
  const session = marketSessionState(nowMs);
  return {
    state: session.open ? "live" : "closed",
    behindMs: Math.max(0, behindMs),
    ageMs: nowMs - tapeMs,
  };
}
