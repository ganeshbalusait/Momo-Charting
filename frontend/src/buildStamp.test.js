// A day-old OI ladder must never be able to look like this morning's.
//
// The Mag7 alert cards printed "Levels 9:15:00 AM ET" for walls built on
// Wed Aug 26. formatTimeLabel emits time with NO date, so a stale ladder and a
// fresh one rendered identically - and that is what hid two days of stale
// levels while the backend quietly armed them. The date is the fact that
// changes what the time means, so it has to be on screen.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

// Lifted and RUN, not asserted on as text - a comment claiming the date is
// shown is exactly the kind of evidence this bug taught us not to trust.
const formatBuildStamp = new Function([
  'const MARKET_TIMEZONE = "America/New_York";',
  "const parseApiDate = (value) => { const d = new Date(value); return Number.isNaN(d.getTime()) ? null : d; };",
  lift(/function formatTimeLabel\([\s\S]*?\n}/, "formatTimeLabel"),
  lift(/function formatBuildStamp\([\s\S]*?\n}/, "formatBuildStamp"),
  "return formatBuildStamp;",
].join("\n\n"))();

const todayAt = (hour, minute) => {
  // Build an instant that is `hour:minute` ET today, whatever the runner's zone.
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type).value;
  // -04:00 in summer; the assertions below only care about the calendar day.
  return `${get("year")}-${get("month")}-${get("day")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00-04:00`;
};

const daysAgo = (days, hour, minute) => {
  const then = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(then);
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00-04:00`;
};

test("a ladder built TODAY still shows time only, exactly as before", () => {
  const shown = formatBuildStamp(todayAt(9, 15));
  assert.match(shown, /ET$/);
  assert.ok(!/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b/.test(shown),
    `today's build should not be dated, got: ${shown}`);
});

test("THE REGRESSION: a ladder built on another day is dated", () => {
  // Computed relative to now rather than pinned to 2026-08-26, so this cannot
  // quietly start passing (or failing) on one particular calendar day - the
  // last thing this test should have is a hidden dependence on the date.
  const shown = formatBuildStamp(daysAgo(1, 9, 15));
  assert.match(shown, /(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/, `expected a weekday in: ${shown}`);
  assert.match(shown, /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/,
    `expected a month in: ${shown}`);
  assert.match(shown, /ET$/);
});

test("yesterday and today can never render identically", () => {
  // The actual defect, stated as an invariant: the SAME clock time on a
  // different day must not produce the same string. This is the assertion that
  // would have caught it two days ago.
  assert.notEqual(formatBuildStamp(daysAgo(1, 9, 15)), formatBuildStamp(todayAt(9, 15)));
});

test("an after-hours rebuild is dated once the session has rolled", () => {
  // AAPL was rebuilt at 20:11 ET after the close. Read the NEXT morning it is
  // stale, and must say so rather than showing a bare "8:11:32 PM ET".
  const shown = formatBuildStamp(daysAgo(1, 20, 11));
  assert.match(shown, /(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/, `expected a weekday in: ${shown}`);
});

test("empty and unparseable values degrade quietly", () => {
  assert.equal(formatBuildStamp(""), "--");
  assert.equal(formatBuildStamp(null), "--");
  assert.equal(formatBuildStamp("not a date"), "not a date");
});
