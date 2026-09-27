import test from "node:test";
import assert from "node:assert/strict";

import {
  OVERNIGHT_SLACK_MS,
  SESSION_CLOSE_MINUTE,
  SESSION_OPEN_MINUTE,
  inDataWeek,
  isOvernight,
  etParts,
  etWallClockMs,
  isTradingDay,
  lastExpectedBarMs,
  marketSessionState,
  tapeState,
} from "./marketSession.js";

// Every instant below is written as an explicit UTC offset so the suite does
// not depend on the machine's zone. This machine runs CDT, one hour behind ET,
// which has produced wrong answers here before.
const at = (iso) => Date.parse(iso);

// 2026-09-05 is a Saturday; 2026-09-04 a Friday. September is EDT (-04:00).
const SAT_1328 = at("2026-09-05T13:28:00-04:00");
const FRI_2000 = at("2026-09-04T20:00:00-04:00");

test("etParts reads ET wall clock, not the machine zone", () => {
  const p = etParts(SAT_1328);
  assert.equal(p.weekday, "Sat");
  assert.equal(p.year, 2026);
  assert.equal(p.month, 9);
  assert.equal(p.day, 5);
  assert.equal(p.hour, 13);
  assert.equal(p.minute, 28);
});

test("etWallClockMs round-trips through etParts", () => {
  const ms = etWallClockMs(2026, 9, 4, 20, 0);
  assert.equal(ms, FRI_2000);
  const p = etParts(ms);
  assert.equal(p.hour, 20);
  assert.equal(p.minute, 0);
  assert.equal(p.weekday, "Fri");
});

// EST, not EDT - the offset changes and the helper must follow it.
test("etWallClockMs handles the winter offset too", () => {
  const ms = etWallClockMs(2026, 1, 15, 20, 0);
  assert.equal(ms, at("2026-01-15T20:00:00-05:00"));
});

test("isTradingDay excludes the weekend", () => {
  assert.equal(isTradingDay("Mon"), true);
  assert.equal(isTradingDay("Fri"), true);
  assert.equal(isTradingDay("Sat"), false);
  assert.equal(isTradingDay("Sun"), false);
});

// --------------------------------------------------------------------------
// session window
// --------------------------------------------------------------------------

test("the session runs 04:00 to 20:00 ET on weekdays only", () => {
  assert.equal(marketSessionState(at("2026-09-02T03:59:00-04:00")).open, false);
  assert.equal(marketSessionState(at("2026-09-02T04:00:00-04:00")).open, true);
  assert.equal(marketSessionState(at("2026-09-02T11:00:00-04:00")).open, true);
  assert.equal(marketSessionState(at("2026-09-02T19:59:00-04:00")).open, true);
  // 20:00 is the CLOSE - the boundary is exclusive, matching the last bar.
  assert.equal(marketSessionState(at("2026-09-02T20:00:00-04:00")).open, false);
  assert.equal(marketSessionState(SAT_1328).open, false);
  assert.equal(marketSessionState(at("2026-09-06T11:00:00-04:00")).open, false, "Sunday");
  assert.equal(SESSION_OPEN_MINUTE, 240);
  assert.equal(SESSION_CLOSE_MINUTE, 1200);
});

// --------------------------------------------------------------------------
// the newest bar the market could have made
// --------------------------------------------------------------------------

test("mid-session, the newest possible bar is now", () => {
  const now = at("2026-09-02T11:00:00-04:00");
  assert.equal(lastExpectedBarMs(now), now);
});

// --------------------------------------------------------------------------
// the DATA week - Sun 20:00 -> Fri 20:00 ET, continuous
// --------------------------------------------------------------------------
// Conflating this with the 04:00-20:00 session was a real regression: the
// board ingests Alpaca BOATS bars 20:00-04:00 on weeknights (measured 94
// five-minute bars per night), so a tape that died at the overnight open used
// to show a grey "nothing is wrong" chip until 04:00.

test("the data week covers weeknights but not the weekend gap", () => {
  const p = (iso) => etParts(at(iso));
  assert.equal(inDataWeek(p("2026-09-02T22:30:00-04:00")), true, "Wed night");
  assert.equal(inDataWeek(p("2026-09-03T02:00:00-04:00")), true, "Thu pre-dawn");
  assert.equal(inDataWeek(p("2026-09-04T19:59:00-04:00")), true, "Fri before close");
  assert.equal(inDataWeek(p("2026-09-04T20:01:00-04:00")), false, "Fri after close");
  assert.equal(inDataWeek(p("2026-09-05T13:28:00-04:00")), false, "Saturday");
  assert.equal(inDataWeek(p("2026-09-06T19:59:00-04:00")), false, "Sun before the open");
  assert.equal(inDataWeek(p("2026-09-06T20:01:00-04:00")), true, "Sun overnight opens");
});

test("isOvernight is the BOATS window only", () => {
  const p = (iso) => etParts(at(iso));
  assert.equal(isOvernight(p("2026-09-02T22:30:00-04:00")), true);
  assert.equal(isOvernight(p("2026-09-03T03:59:00-04:00")), true);
  assert.equal(isOvernight(p("2026-09-02T11:00:00-04:00")), false, "regular hours");
  assert.equal(isOvernight(p("2026-09-05T13:28:00-04:00")), false, "weekend is not overnight");
});

// THE REGRESSION THIS BLOCK EXISTS TO PREVENT.
test("a tape dead since the overnight open is STALE, not 'nothing is wrong'", () => {
  const tueClose = at("2026-09-01T20:00:00-04:00");
  assert.equal(tapeState(tueClose, at("2026-09-01T23:55:00-04:00")).state, "stale",
    "four hours into a dead overnight feed");
  assert.equal(tapeState(tueClose, at("2026-09-02T03:55:00-04:00")).state, "stale",
    "eight hours into a dead overnight feed");
});

test("a healthy overnight tape reads closed, not stale", () => {
  const now = at("2026-09-02T22:30:00-04:00");
  assert.equal(tapeState(now - 20 * 60 * 1000, now).state, "closed");
});

// BOATS publishes ~16 minutes late by design, plus a build cycle - a 30-minute
// slack would cry wolf every single night.
test("the overnight slack is wider than the session slack", () => {
  const now = at("2026-09-02T22:30:00-04:00");
  assert.equal(OVERNIGHT_SLACK_MS, 45 * 60 * 1000);
  assert.equal(tapeState(now - 40 * 60 * 1000, now).state, "closed", "40m overnight is fine");
  assert.equal(tapeState(now - 50 * 60 * 1000, now).state, "stale", "50m overnight is not");
});

test("the weekend gap expects the previous Friday's 20:00 close", () => {
  assert.equal(lastExpectedBarMs(SAT_1328), FRI_2000);
  assert.equal(lastExpectedBarMs(at("2026-09-06T12:00:00-04:00")), FRI_2000, "Sunday midday");
  assert.equal(lastExpectedBarMs(at("2026-09-06T19:59:00-04:00")), FRI_2000, "Sunday, one minute out");
});

// Sunday 20:00 is when the overnight tape opens, so from that moment a Friday
// bar is genuinely behind - the gap ENDS, it does not run to Monday morning.
test("Sunday night is inside the data week, so it expects now", () => {
  const sunNight = at("2026-09-06T23:00:00-04:00");
  assert.equal(lastExpectedBarMs(sunNight), sunNight);
});

// A weeknight is NOT a gap - the overnight tape is running, so the newest
// possible bar is now and a tape sitting on the 20:00 close is behind.
test("a weeknight is INSIDE the data week, so the newest possible bar is now", () => {
  const tueNight = at("2026-09-01T22:00:00-04:00");
  assert.equal(lastExpectedBarMs(tueNight), tueNight);
});

test("Friday after 20:00 expects Friday's own close, not Thursday's", () => {
  assert.equal(
    lastExpectedBarMs(at("2026-09-04T22:00:00-04:00")),
    at("2026-09-04T20:00:00-04:00"),
  );
});

test("pre-dawn on a weekday is still the overnight tape, so it expects now", () => {
  const wedPreDawn = at("2026-09-02T02:00:00-04:00");
  assert.equal(lastExpectedBarMs(wedPreDawn), wedPreDawn);
  // Monday pre-dawn too - BOATS opened on Sunday evening.
  const monPreDawn = at("2026-09-07T02:00:00-04:00");
  assert.equal(lastExpectedBarMs(monPreDawn), monPreDawn);
});

// --------------------------------------------------------------------------
// tapeState - the three-way verdict the banner renders
// --------------------------------------------------------------------------

// The exact situation he reported. The scanner was healthy; the banner was not.
test("Saturday afternoon with Friday's close is CLOSED, not stale", () => {
  const v = tapeState(FRI_2000, SAT_1328);
  assert.equal(v.state, "closed");
  assert.equal(v.behindMs, 0);
});

// Deliberately INVERTED from the first cut of this suite, which asserted
// "closed" here. On a Wednesday at 22:30 the overnight tape should be minutes
// old; one still holding 20:00 means the feed died two and a half hours ago.
test("a weekday evening still holding the 20:00 close is STALE", () => {
  const v = tapeState(at("2026-09-02T20:00:00-04:00"), at("2026-09-02T22:30:00-04:00"));
  assert.equal(v.state, "stale");
});

test("mid-session and keeping up is LIVE", () => {
  const now = at("2026-09-02T11:00:00-04:00");
  const v = tapeState(at("2026-09-02T10:56:00-04:00"), now);
  assert.equal(v.state, "live");
});

// The alarm must still work: this is the case the amber chip exists for.
test("mid-session and an hour behind is STALE", () => {
  const now = at("2026-09-02T11:00:00-04:00");
  const v = tapeState(at("2026-09-02T10:00:00-04:00"), now);
  assert.equal(v.state, "stale");
});

test("a tape a whole session behind is STALE even out of hours", () => {
  // Tuesday 22:00 holding MONDAY's close - only ~26h old, but a session behind.
  const v = tapeState(at("2026-08-31T20:00:00-04:00"), at("2026-09-01T22:00:00-04:00"));
  assert.equal(v.state, "stale");
});

// The weekend is the ONE window with a fixed expectation, and it must stay
// quiet - this is the case that started all of it.
test("the whole weekend stays quiet on Friday's close", () => {
  for (const iso of [
    "2026-09-04T20:30:00-04:00", "2026-09-05T00:30:00-04:00",
    "2026-09-05T13:28:00-04:00", "2026-09-06T12:00:00-04:00",
    "2026-09-06T19:30:00-04:00",
  ]) {
    assert.equal(tapeState(FRI_2000, at(iso)).state, "closed", iso);
  }
  // ...and the moment the overnight session opens, it is expected to move on.
  assert.equal(tapeState(FRI_2000, at("2026-09-06T21:00:00-04:00")).state, "stale",
    "Sunday overnight is open - a Friday tape is now behind");
});

test("a weekend tape from the week BEFORE is STALE", () => {
  const v = tapeState(at("2026-08-28T20:00:00-04:00"), SAT_1328);
  assert.equal(v.state, "stale");
});

test("the 30-minute slack is the boundary inside a session", () => {
  const now = at("2026-09-02T11:00:00-04:00");
  assert.equal(tapeState(now - 30 * 60 * 1000, now).state, "live", "exactly 30m is not stale");
  assert.equal(tapeState(now - 31 * 60 * 1000, now).state, "stale");
});

test("an unreadable tape stamp yields null rather than a guess", () => {
  assert.equal(tapeState(NaN, SAT_1328), null);
  assert.equal(tapeState(undefined, SAT_1328), null);
});

// A tape stamped slightly AHEAD (clock skew between worker and browser) must
// not read as stale - behindMs is floored at zero.
test("a tape stamped slightly in the future is not stale", () => {
  const now = at("2026-09-02T11:00:00-04:00");
  const v = tapeState(now + 60 * 1000, now);
  assert.equal(v.state, "live");
  assert.equal(v.behindMs, 0);
});
