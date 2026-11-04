// Unit (2026-11-04 coverage pass, group charts-studies, feature charts-squeeze).
//
// 1. The JS side of the scanner/chart parity fixture. tests/test_premarket_scanner.py
//    checks the PYTHON scanner against tests/fixtures/squeeze_release_expected.json,
//    which scripts/generate_squeeze_fixture.mjs writes from this module. If someone
//    changes squeezeRelease.js and does not regenerate the fixture, the Python test
//    would blame the scanner; this test fails on the chart side instead, naming the
//    real cause.
// 2. Why the first paint carries 2,400 five-minute bars: a 2h fire needs 20+ closed
//    2h buckets behind it. A tail that long reproduces the full tape's recent fires
//    exactly; the old ~1.5-day first paint could not fire at all (MRNA 09:00, 2026-10-09).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { SQUEEZE_LENGTH, squeezeReleaseEvents } from "./squeezeRelease.js";

const fixture = (name) => JSON.parse(readFileSync(new URL(`../../tests/fixtures/${name}`, import.meta.url), "utf8"));
const bars = fixture("squeeze_release_bars.json");
const expected = fixture("squeeze_release_expected.json");
const key = (event) => `${event.minutes}|${event.bucketTime}|${event.closeTime}|${event.tone}`;

test("squeezeRelease.js still produces the committed parity fixture event for event", () => {
  assert.deepEqual(Object.keys(expected).sort(), ["120", "1440", "240", "60"]);
  for (const minutes of [60, 120, 240, 1440]) {
    assert.deepEqual(
      squeezeReleaseEvents(bars, minutes),
      expected[String(minutes)],
      `${minutes}m fires changed - if the chart change is intended run node scripts/generate_squeeze_fixture.mjs, then re-run tests/test_premarket_scanner.py`,
    );
  }
  assert.ok(Object.values(expected).every((events) => events.length > 0), "the fixture must exercise every timeframe");
});

test("every fixture fire is a closed bucket with a bull or bear tone", () => {
  const lastTime = bars.at(-1).time;
  for (const [minutes, events] of Object.entries(expected)) {
    for (const event of events) {
      assert.equal(event.minutes, Number(minutes));
      assert.equal(event.closeTime - event.bucketTime, Number(minutes) * 60);
      assert.ok(event.closeTime <= lastTime, `a forming ${minutes}m bucket fired at ${event.bucketTime}`);
      assert.ok(["bull", "bear"].includes(event.tone));
    }
  }
});

test("a first-paint-sized tail reproduces the full tape's recent 2h fires; a 1.5-day tail cannot fire", () => {
  // The fixture is 30-minute bars: 400 of them cover the same hours as 2,400 5m bars.
  const firstPaint = bars.slice(-400);
  const tailEvents = new Set(squeezeReleaseEvents(firstPaint, 120).map(key));
  const settled = expected["120"].filter((event) => event.bucketTime >= firstPaint[0].time + (SQUEEZE_LENGTH + 2) * 7200);
  assert.ok(settled.length >= 1, "fixture should have a recent 2h fire to check");
  for (const event of settled) assert.ok(tailEvents.has(key(event)), `first paint misses the ${new Date(event.bucketTime * 1000).toISOString()} 2h fire`);

  const oldFirstPaint = bars.slice(-30); // ~1.5 sessions
  assert.deepEqual(squeezeReleaseEvents(oldFirstPaint, 120), [], "fewer than 20 2h buckets must never fire");
});

test("includeForming can add the forming bucket's fire but never changes the closed ones", () => {
  for (const minutes of [60, 120, 240]) {
    const closed = squeezeReleaseEvents(bars, minutes).map(key);
    const live = squeezeReleaseEvents(bars, minutes, { includeForming: true }).map(key);
    assert.deepEqual(live.slice(0, closed.length), closed, `${minutes}m`);
    assert.ok(live.length - closed.length <= 1, `${minutes}m: only the forming bucket may be added`);
  }
});
