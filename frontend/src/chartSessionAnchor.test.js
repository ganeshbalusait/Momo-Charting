import test from "node:test";
import assert from "node:assert/strict";

import { lineStyleDashArray, momoxLevelAnchorTime, trimPointsToAnchor } from "./chartSessionAnchor.js";

const dateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const timeFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

// 2026-08-05 and 2026-08-06 are EDT (UTC-4), so 04:00 ET === 08:00 UTC.
const et = (day, hour, minute = 0) => Math.floor(Date.UTC(2026, 7, day, hour + 4, minute) / 1000);
const bar = (time) => ({ time, high: 1, low: 1 });

const OPTIONS = { dateFormatter, timeFormatter, timeframeMinutes: 5 };

test("anchors to the 4:00 AM ET bar of the latest session", () => {
  const bars = [et(5, 10), et(6, 3, 30), et(6, 4), et(6, 9, 30), et(6, 15)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, OPTIONS), et(6, 4));
});

test("ignores an earlier session's 4:00 AM bar", () => {
  // The 08-05 premarket bar would match the clock test; only 08-06 may win.
  const bars = [et(5, 4), et(5, 10), et(6, 4), et(6, 11)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, OPTIONS), et(6, 4));
});

test("takes the first bar at or after 4:00, not a later premarket bar", () => {
  const bars = [et(6, 4, 5), et(6, 5), et(6, 9, 30)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, OPTIONS), et(6, 4, 5));
});

test("falls back to the session's first bar when premarket is missing", () => {
  // A ticker with no extended-hours data still has to anchor somewhere.
  const bars = [et(5, 15), et(6, 9, 30), et(6, 10)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, OPTIONS), et(6, 9, 30));
});

test("anchors to the latest LOADED session, so scrolled-back history still works", () => {
  const bars = [et(4, 4), et(4, 10), et(5, 4), et(5, 12)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, OPTIONS), et(5, 4));
});

test("keeps daily and higher timeframes full-width", () => {
  const bars = [et(5, 9, 30), et(6, 9, 30)].map(bar);
  for (const timeframeMinutes of [1440, 10080, 43200]) {
    assert.equal(momoxLevelAnchorTime(bars, { ...OPTIONS, timeframeMinutes }), null);
  }
  // 4h is the timeframe in the reference screenshot and must still anchor.
  assert.notEqual(momoxLevelAnchorTime([et(6, 4), et(6, 12)].map(bar), { ...OPTIONS, timeframeMinutes: 240 }), null);
});

test("returns null instead of throwing on empty or unusable input", () => {
  assert.equal(momoxLevelAnchorTime([], OPTIONS), null);
  assert.equal(momoxLevelAnchorTime(null, OPTIONS), null);
  assert.equal(momoxLevelAnchorTime([bar(et(6, 4))], { timeframeMinutes: 5 }), null);
});

test("honours a custom anchor clock", () => {
  const bars = [et(6, 4), et(6, 9, 30), et(6, 11)].map(bar);
  assert.equal(momoxLevelAnchorTime(bars, { ...OPTIONS, anchorClock: 930 }), et(6, 9, 30));
});

test("trims a per-bar study series to the session anchor", () => {
  const points = [et(5, 10), et(6, 3), et(6, 4), et(6, 11)].map((time) => ({ time, value: 1 }));
  assert.deepEqual(
    trimPointsToAnchor(points, et(6, 4)).map((point) => point.time),
    [et(6, 4), et(6, 11)],
  );
});

test("keeps whitespace gap points that fall inside the session", () => {
  // Persons ranges use valueless points to break the line; they must survive
  // trimming or the series reconnects across the gap.
  const points = [{ time: et(6, 4), value: 1 }, { time: et(6, 5) }, { time: et(6, 6), value: 2 }];
  assert.equal(trimPointsToAnchor(points, et(6, 4)).length, 3);
});

test("returns the series untouched rather than emptying it", () => {
  const points = [et(5, 10), et(5, 11)].map((time) => ({ time, value: 1 }));
  // Anchor past every point: full-width is wrong, but vanishing is worse.
  assert.equal(trimPointsToAnchor(points, et(6, 4)).length, 2);
  // No anchor (D/W/M) leaves the full history in place.
  assert.equal(trimPointsToAnchor(points, null).length, 2);
  assert.deepEqual(trimPointsToAnchor([], et(6, 4)), []);
  assert.deepEqual(trimPointsToAnchor(null, et(6, 4)), []);
});

test("maps every lightweight-charts line style to its dash pattern", () => {
  assert.deepEqual(lineStyleDashArray(0), []);
  assert.deepEqual(lineStyleDashArray(2), [4, 3]);
  assert.deepEqual(lineStyleDashArray(3), [8, 4]);
  // Unknown/undefined styles fall back to solid rather than vanishing.
  assert.deepEqual(lineStyleDashArray(undefined), []);
  assert.deepEqual(lineStyleDashArray(99), []);
});
