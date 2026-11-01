import test from "node:test";
import assert from "node:assert/strict";

import { candleTimeFor, gradeStepMarkers } from "./gradeMarkers.js";

const s = (iso) => Date.parse(iso) / 1000;
const e = (hhmm, letter, day = "2026-09-23") => ({ t: `${day}T${hhmm}:00-04:00`, letter });

test("FSLY 2026-09-23: A+ in the morning, A at noon, A+ again at 13:19", () => {
  // The real tape: A+ 09:58, gone 10:04, A 12:03 (steps up from nothing), a
  // one-snapshot gap at 12:38, A+ 13:19.
  const tape = [e("09:53", null), e("09:58", "A+"), e("10:04", null), e("12:03", "A"), e("12:08", "A"),
    e("12:38", null), e("12:44", "A"), e("13:12", null), e("13:19", "A+"), e("13:24", "A+")];
  assert.deepEqual(gradeStepMarkers(tape).map((m) => [new Date(m.time * 1000).toISOString().slice(11, 16), m.letter]), [
    ["13:58", "A+"], // 09:58 ET
    ["16:03", "A"],  // 12:03 ET - the 12:44 flicker back to A is within the hour, not repeated
    ["17:19", "A+"], // 13:19 ET - more than an hour after the morning A+
  ]);
});

test("TSLA 2026-09-23: the 09:47 A shows even though no arrow fired there", () => {
  const tape = [e("09:41", null), e("09:47", "A"), e("09:53", null), e("09:58", "A")];
  assert.deepEqual(gradeStepMarkers(tape).map((m) => m.letter), ["A"]);
  assert.equal(gradeStepMarkers(tape)[0].time, s("2026-09-23T09:47:00-04:00"));
});

test("a new day starts fresh; B never marks; A -> A+ is a step up", () => {
  const tape = [e("15:50", "A"), e("09:31", "A", "2026-09-24"), e("09:40", "B", "2026-09-24"), e("09:45", "A+", "2026-09-24")];
  assert.deepEqual(gradeStepMarkers(tape).map((m) => m.letter), ["A", "A", "A+"]);
  assert.deepEqual(gradeStepMarkers(null), []);
  assert.deepEqual(gradeStepMarkers([{ t: "junk", letter: "A" }, null]), []);
});

test("candleTimeFor picks the candle the snapshot falls in", () => {
  const bars = [100, 400, 700];
  assert.equal(candleTimeFor(450, bars), 400);
  assert.equal(candleTimeFor(700, bars), 700);
  assert.equal(candleTimeFor(50, bars), null);
});

test("the per-hour ET day cache matches toLocaleDateString, across the DST change", () => {
  const fmt = (sec) => new Date(sec * 1000).toLocaleDateString("en-US", { timeZone: "America/New_York" });
  // 2026-11-01 02:00 EDT -> 01:00 EST; walk 3 days in 7-minute steps.
  const entries = [];
  for (let sec = s("2026-10-31T00:00:00Z"); sec < s("2026-11-03T00:00:00Z"); sec += 420) {
    entries.push({ _sec: sec, letter: (sec / 420) % 3 === 0 ? "A" : "B" });
  }
  // Reference: the step-up rule with the original per-entry day computation.
  const reference = [];
  const lastShown = {};
  let previousRank = 0;
  let previousDay = null;
  for (const entry of entries) {
    const day = fmt(entry._sec);
    if (day !== previousDay) { previousRank = 0; previousDay = day; }
    const rank = entry.letter === "A" ? 1 : 0;
    if (rank > previousRank && rank > 0) {
      const last = lastShown[entry.letter];
      if (last === undefined || entry._sec - last >= 3600) {
        reference.push({ time: entry._sec, letter: entry.letter });
        lastShown[entry.letter] = entry._sec;
      }
    }
    previousRank = rank;
  }
  assert.deepEqual(gradeStepMarkers(entries), reference);
});
