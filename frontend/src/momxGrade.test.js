import assert from "node:assert/strict";

// Every other *.test.js here runs under `node --test src/*.test.js`, but the
// MomoX board task specifies `npx vitest run src/momxCells.test.js`. Picking the
// runner at import time keeps this file green under BOTH: vitest sets
// process.env.VITEST, and the import is dynamic so `node --test` never has to
// resolve the "vitest" specifier. Assertions stay on node:assert, which both
// runners support unchanged.
const { test } = process.env.VITEST ? await import("vitest") : await import("node:test");

import {
  MOMENTUM_LABEL,
  PATTERN_ICON,
  GRADE_DISCLAIMER,
  setupText,
  setupParts,
  setupTone,
  momentumTone,
  setupSortValue,
  freshText,
  gradeAgeText,
  setFreshClockForTests,
  freshSortValue,
  timelineDetail,
  trackRecordLine,
  gradeAtTime,
  gradeUnderwaterNow,
  gradeWithinCandle,
  isBullishCallLabel,
  chartLabel,
  hlDegreeText,
  FRESH_ICON_ORDER,
  adxText,
  sessionLabel,
} from "./momxGrade.js";

// --- constants ---

test("MOMENTUM_LABEL has the five draft states", () => {
  assert.deepEqual(MOMENTUM_LABEL, {
    building: "Building ↑",
    holding: "Holding",
    fading: "Fading ↓",
    extended: "Extended",
    quiet: "Quiet",
  });
});

test("PATTERN_ICON maps explosive/steady", () => {
  assert.deepEqual(PATTERN_ICON, { explosive: "💥", steady: "📈" });
});

test("GRADE_DISCLAIMER is the exact binding text", () => {
  assert.equal(
    GRADE_DISCLAIMER,
    "A+ means the defined conditions align. It is not a recommendation and does not guarantee profit."
  );
});

// --- setupText ---

test("setupText: brief's base case - letter + building, no pattern", () => {
  assert.equal(setupText({ grade: { letter: "A+" }, m5: { state: "building" } }), "A+ · Building ↑");
});

test("setupText: letter + explosive pattern + building", () => {
  assert.equal(
    setupText({ grade: { letter: "A+" }, m5: { state: "building", pattern: "explosive" } }),
    "A+ 💥 · Building ↑"
  );
});

test("setupText: letter + steady pattern + holding", () => {
  assert.equal(
    setupText({ grade: { letter: "A" }, m5: { state: "holding", pattern: "steady" } }),
    "A 📈 · Holding"
  );
});

test("setupText: no letter but a steady pattern + building -> dash placeholder", () => {
  assert.equal(
    setupText({ grade: null, m5: { state: "building", pattern: "steady" } }),
    "– 📈 · Building ↑"
  );
});

test("setupText: letter + fading, no pattern", () => {
  assert.equal(setupText({ grade: { letter: "A" }, m5: { state: "fading" } }), "A · Fading ↓");
});

test("setupText: provisional appends (prov.)", () => {
  assert.equal(
    setupText({ grade: { letter: "A+" }, m5: { state: "building", provisional: true } }),
    "A+ · Building ↑ (prov.)"
  );
});

test("setupText: no letter, no pattern, quiet momentum -> empty string", () => {
  assert.equal(setupText({ grade: null, m5: { state: "quiet" } }), "");
});

test("setupText: no letter, no pattern, missing m5 -> empty string", () => {
  assert.equal(setupText({ grade: null }), "");
});

test("setupText: letter only, no m5 at all -> just the letter", () => {
  assert.equal(setupText({ grade: { letter: "B" } }), "B");
});

test("setupText: letter + quiet state -> 'A · Quiet'", () => {
  assert.equal(setupText({ grade: { letter: "A" }, m5: { state: "quiet" } }), "A · Quiet");
});

test("setupText: tolerates null/undefined/garbage row", () => {
  assert.equal(setupText(null), "");
  assert.equal(setupText(undefined), "");
  assert.equal(setupText(42), "");
  assert.equal(setupText("garbage"), "");
  assert.equal(setupText({}), "");
});

// --- setupTone / momentumTone ---

test("setupTone maps letters", () => {
  assert.equal(setupTone({ grade: { letter: "A+" } }), "aplus");
  assert.equal(setupTone({ grade: { letter: "A" } }), "a");
  assert.equal(setupTone({ grade: { letter: "B" } }), "b");
  assert.equal(setupTone({ grade: null }), "none");
  assert.equal(setupTone({}), "none");
  assert.equal(setupTone(null), "none");
});

test("momentumTone: building=up, fading=down, everything else flat", () => {
  assert.equal(momentumTone({ m5: { state: "building" } }), "up");
  assert.equal(momentumTone({ m5: { state: "fading" } }), "down");
  assert.equal(momentumTone({ m5: { state: "holding" } }), "flat");
  assert.equal(momentumTone({ m5: { state: "extended" } }), "flat");
  assert.equal(momentumTone({ m5: { state: "quiet" } }), "flat");
  assert.equal(momentumTone({}), "flat");
  assert.equal(momentumTone(null), "flat");
});

// --- setupSortValue ---

test("setupSortValue: A+ Building > A+ Fading > A Building > none", () => {
  const apBuilding = setupSortValue({ grade: { letter: "A+" }, m5: { state: "building" } });
  const apFading = setupSortValue({ grade: { letter: "A+" }, m5: { state: "fading" } });
  const aBuilding = setupSortValue({ grade: { letter: "A" }, m5: { state: "building" } });
  const none = setupSortValue({ grade: null });
  assert.ok(apBuilding > apFading, "A+ Building should outrank A+ Fading");
  assert.ok(apFading > aBuilding, "A+ Fading should outrank A Building");
  assert.ok(aBuilding > none, "A Building should outrank no grade");
  assert.equal(none, -1);
});

test("setupSortValue: no letter + pattern sorts between lettered rows and nothing", () => {
  const patternBuilding = setupSortValue({ grade: null, m5: { pattern: "steady", state: "building" } });
  const patternFading = setupSortValue({ grade: null, m5: { pattern: "steady", state: "fading" } });
  const noPattern = setupSortValue({ grade: null, m5: { state: "quiet" } });
  const lettered = setupSortValue({ grade: { letter: "B" }, m5: { state: "quiet" } });
  assert.ok(patternBuilding > patternFading, "no letter + building pattern should rank higher than fading");
  assert.ok(patternFading > noPattern, "no letter + pattern should rank above no letter + no pattern");
  assert.ok(lettered > patternBuilding, "lettered B should rank above no letter + building pattern");
  assert.equal(noPattern, -1);
});

test("setupSortValue: momentum-only rows sit between pattern-only rows and blanks", () => {
  const blank = setupSortValue({ grade: null, m5: { state: "quiet" } });
  const patternFading = setupSortValue({ grade: null, m5: { pattern: "steady", state: "fading" } });
  const patternQuiet = setupSortValue({ grade: null, m5: { pattern: "explosive", state: "quiet" } });
  for (const state of ["building", "holding", "fading", "extended"]) {
    const momentumOnly = setupSortValue({ grade: null, m5: { state } });
    assert.equal(momentumOnly, 0, state + " with no letter and no pattern sorts at 0");
    assert.ok(momentumOnly > blank, state + " should outrank a blank row");
    assert.ok(patternFading > momentumOnly, "pattern-only should outrank momentum-only");
    assert.ok(patternQuiet > momentumOnly, "a quiet pattern row should still outrank momentum-only");
  }
  assert.equal(blank, -1);
});

test("setupSortValue tolerates garbage", () => {
  assert.equal(setupSortValue(null), -1);
  assert.equal(setupSortValue(undefined), -1);
  assert.equal(setupSortValue({}), -1);
});

// --- freshText ---

test("freshText: SKIT + RVOL icons render latest timeline hits with ET time, then age", () => {
  const row = {
    gradeFresh: {
      icons: ["SKIT", "RVOL"],
      ageMinutes: 2,
      timeline: [
        { at: "2026-09-22T13:30:00Z", what: "SKIT 4h bg green" }, // earlier SKIT hit
        { at: "2026-09-22T13:32:00Z", what: "SKIT 4h bg green" }, // latest SKIT hit -> 09:32 ET
        { at: "2026-09-22T13:33:00Z", what: "RVOL 5m 7.0" }, // -> 09:33 ET
      ],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T13:35:00Z")); // 2 min after the newest item
  assert.equal(freshText(row), "SKIT 4h bg green 09:32 · RVOL 5m 7.0 09:33 · 2m");
  setFreshClockForTests(null);
});

// MDB, 2026-09-22 13:51 ET: the Fresh cell read "SKIT 4D bg green 13:47 · 243m"
// four minutes after that colour changed, because the age printed was the age
// of the A+ GRADE (first reached 09:43), not of the change. The trader read it
// as a four-hour-old change. Fresh now times the newest change; the grade's age
// moved to the Setup hover.
test("freshText: the age is time since the NEWEST change, not the grade's age", () => {
  const row = {
    grade: { letter: "A+" },
    gradeFresh: {
      icons: ["SKIT"],
      ageMinutes: 243, // A+ since 09:43
      firstToday: { "A+": { at: "2026-09-22T09:43:43-04:00", price: 416.05 } },
      timeline: [
        { at: "2026-09-22T11:36:10-04:00", what: "SKIT D bg cyan" },
        { at: "2026-09-22T13:47:17-04:00", what: "SKIT 4D bg green" },
      ],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T13:51:42-04:00"));
  assert.equal(freshText(row), "SKIT 4D bg green 13:47 · 4m");
  assert.equal(gradeAgeText(row), "A+ since 09:43 ET (243m)");
  setFreshClockForTests(null);
});

test("gradeAgeText: no letter, or no first-today entry -> empty", () => {
  assert.equal(gradeAgeText({ grade: { letter: null }, gradeFresh: { firstToday: {} } }), "");
  assert.equal(gradeAgeText({ grade: { letter: "A" }, gradeFresh: { firstToday: {} } }), "");
  assert.equal(gradeAgeText(null), "");
  assert.equal(gradeAgeText({}), "");
});

test("freshText: news icon matches the 'news' timeline prefix case-insensitively", () => {
  const row = {
    gradeFresh: {
      icons: ["NEWS"],
      ageMinutes: null,
      timeline: [{ at: "2026-09-22T13:35:00Z", what: "news" }],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T13:36:00Z")); // 1 min later
  assert.equal(freshText(row), "news 09:35 · 1m");
  setFreshClockForTests(null);
});

test("freshText: no timeline items -> empty, even when the grade has an age", () => {
  assert.equal(freshText({ gradeFresh: { icons: [], ageMinutes: 18, timeline: [] } }), "");
});

test("freshText: nothing present -> empty string", () => {
  assert.equal(freshText({ gradeFresh: { icons: [], ageMinutes: null, timeline: [] } }), "");
});

test("freshText: gradeFresh null/missing -> empty string", () => {
  assert.equal(freshText({ gradeFresh: null }), "");
  assert.equal(freshText({}), "");
  assert.equal(freshText(null), "");
});

test("freshText: known UTC instant formats correctly in America/New_York (EDT, UTC-4)", () => {
  const row = {
    gradeFresh: {
      icons: ["SQZ"],
      ageMinutes: null,
      timeline: [{ at: "2026-09-22T13:32:00Z", what: "SQZ 4h released" }],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T13:39:00Z")); // 7 min later
  assert.equal(freshText(row), "SQZ 4h released 09:32 · 7m");
  setFreshClockForTests(null);
});

// --- freshSortValue ---

test("freshSortValue: newest change first, -Infinity when there is none", () => {
  const older = { gradeFresh: { ageMinutes: 5, timeline: [{ at: "2026-09-22T13:30:00Z", what: "news" }] } };
  const newer = { gradeFresh: { ageMinutes: 240, timeline: [{ at: "2026-09-22T13:47:00Z", what: "SKIT 4D bg green" }] } };
  // The grade's age no longer decides the order: the row whose colour changed
  // most recently sorts first, even if its letter is much older (MDB case).
  assert.ok(freshSortValue(newer) > freshSortValue(older));
  assert.equal(freshSortValue({ gradeFresh: { ageMinutes: 5, timeline: [] } }), -Infinity);
  assert.equal(freshSortValue({ gradeFresh: { timeline: [{ at: "nonsense", what: "x" }] } }), -Infinity);
  assert.equal(freshSortValue({ gradeFresh: null }), -Infinity);
  assert.equal(freshSortValue({}), -Infinity);
  assert.equal(freshSortValue(null), -Infinity);
});

// --- trackRecordLine ---

test("trackRecordLine: back-test source with full stats", () => {
  const record = {
    source: "back-test from History archive (13 days)",
    letters: { "A+": { count: 325, pctHigherClose: 44.0, avgToClose: 0.1 } },
  };
  assert.equal(
    trackRecordLine(record, "A+"),
    "A+ · 325 signals · 44% closed higher · avg +0.10% (back-test from History archive (13 days))"
  );
});

test("trackRecordLine: recorded source says (recorded)", () => {
  const record = {
    source: "recorded",
    letters: { "A": { count: 12, pctHigherClose: 50.0, avgToClose: -0.22 } },
  };
  assert.equal(trackRecordLine(record, "A"), "A · 12 signals · 50% closed higher · avg -0.22% (recorded)");
});

test("trackRecordLine: count 0 says no signals yet", () => {
  const record = { source: "recorded", letters: { "B": { count: 0 } } };
  assert.equal(trackRecordLine(record, "B"), "B · no signals yet");
});

test("trackRecordLine: missing record -> unavailable", () => {
  assert.equal(trackRecordLine(null, "A+"), "track record unavailable");
  assert.equal(trackRecordLine(undefined, "A+"), "track record unavailable");
});

test("trackRecordLine: missing letter -> unavailable", () => {
  const record = { source: "recorded", letters: { "A+": { count: 5, pctHigherClose: 60, avgToClose: 0.2 } } };
  assert.equal(trackRecordLine(record, null), "track record unavailable");
  assert.equal(trackRecordLine(record, "C"), "track record unavailable");
});

test("trackRecordLine: null values inside stats render as dash", () => {
  const record = { source: "recorded", letters: { "A": { count: 4, pctHigherClose: null, avgToClose: null } } };
  assert.equal(trackRecordLine(record, "A"), "A · 4 signals · – closed higher · avg – (recorded)");
});

// --- gradeAtTime ---

const TAPE = [
  { t: "2026-09-22T13:20:00Z", letter: "B" },
  { t: "2026-09-22T13:35:00Z", letter: "A+" }, // 09:35 ET
  { t: "2026-09-22T13:40:00Z", letter: "A" },
];

test("gradeAtTime: picks the 09:35 entry for a 09:37 label", () => {
  const epoch = Date.parse("2026-09-22T13:37:00Z") / 1000;
  assert.equal(gradeAtTime(TAPE, epoch).letter, "A+");
});

test("gradeAtTime: null for a label 6 minutes after the last entry", () => {
  const epoch = Date.parse("2026-09-22T13:46:00Z") / 1000; // 6 min after 13:40 entry
  assert.equal(gradeAtTime(TAPE, epoch), null);
});

test("gradeAtTime: null for a label before the first entry", () => {
  const epoch = Date.parse("2026-09-22T13:10:00Z") / 1000;
  assert.equal(gradeAtTime(TAPE, epoch), null);
});

test("gradeAtTime tolerates garbage", () => {
  assert.equal(gradeAtTime(null, 100), null);
  assert.equal(gradeAtTime(TAPE, "not a number"), null);
  assert.equal(gradeAtTime([null, {}, { t: "not a date" }], 100), null);
});

// --- gradeWithinCandle ---

// 15m candle opening 09:35 ET = 13:35 UTC on 2026-09-22 (EDT, UTC-4).
const CANDLE_OPEN_SEC = Date.parse("2026-09-22T13:35:00Z") / 1000; // 09:35 ET
const CANDLE_SECONDS_15M = 15 * 60;

function etSec(hh, mm) {
  return Date.parse(`2026-09-22T${String(hh + 4).padStart(2, "0")}:${String(mm).padStart(2, "0")}:00Z`) / 1000;
}

test("gradeWithinCandle: entry inside the candle window (09:44) matches", () => {
  const tape = [{ t: "2026-09-22T13:44:00Z", letter: "A+" }]; // 09:44 ET
  const now = etSec(10, 0);
  const result = gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now);
  assert.equal(result.letter, "A+");
});

// The scanner samples every ~8 min (median, measured), so most 5m candles
// hold no sample of their own. A grade recorded shortly BEFORE the candle is
// the grade that was live when the signal fired, and is carried forward.
test("gradeWithinCandle: entry 1 min before candle open (09:34) is carried forward", () => {
  const tape = [{ t: "2026-09-22T13:34:00Z", letter: "A+" }]; // 09:34 ET
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now).letter, "A+");
});

test("gradeWithinCandle: entry exactly at the carry-back edge (09:20) still matches", () => {
  const tape = [{ t: "2026-09-22T13:20:00Z", letter: "A" }]; // 09:20 ET = open - 15m
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now).letter, "A");
});

test("gradeWithinCandle: entry older than the carry-back (09:19) does not match", () => {
  const tape = [{ t: "2026-09-22T13:19:00Z", letter: "A+" }]; // 09:19 ET = open - 16m
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now), null);
});

test("gradeWithinCandle: a sample INSIDE the candle beats an older carried-back one", () => {
  const tape = [
    { t: "2026-09-22T13:30:00Z", letter: "B" },  // 09:30 ET, before the open
    { t: "2026-09-22T13:40:00Z", letter: "A+" }, // 09:40 ET, inside the candle
  ];
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now).letter, "A+");
});

test("gradeWithinCandle: carryBackSec 0 restores strict inside-the-candle matching", () => {
  const tape = [{ t: "2026-09-22T13:34:00Z", letter: "A+" }]; // 09:34 ET
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now, 0), null);
});

test("gradeWithinCandle: entry after candle close (09:51) does not match", () => {
  const tape = [{ t: "2026-09-22T13:51:00Z", letter: "A+" }]; // 09:51 ET, close is 09:50
  const now = etSec(10, 0);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now), null);
});

test("gradeWithinCandle: two entries in window - the later one (09:48) wins", () => {
  const tape = [
    { t: "2026-09-22T13:36:00Z", letter: "B" }, // 09:36 ET
    { t: "2026-09-22T13:48:00Z", letter: "A+" }, // 09:48 ET
  ];
  const now = etSec(10, 0);
  const result = gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now);
  assert.equal(result.letter, "A+");
});

test("gradeWithinCandle: entry at 09:48 does not match when now is only 09:40 (not yet reached)", () => {
  const tape = [{ t: "2026-09-22T13:48:00Z", letter: "A+" }]; // 09:48 ET
  const now = etSec(9, 40); // 09:40 ET
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now), null);
});

test("gradeWithinCandle: daily 1440-min window catches a 12:00 entry", () => {
  const tape = [{ t: "2026-09-22T16:00:00Z", letter: "A" }]; // 12:00 ET
  const now = etSec(13, 0); // 13:00 ET, well within the day
  const result = gradeWithinCandle(tape, CANDLE_OPEN_SEC, 1440 * 60, now);
  assert.equal(result.letter, "A");
});

test("gradeWithinCandle: uses pre-parsed _sec when present, ignoring t", () => {
  const tape = [{ t: "not a date", _sec: CANDLE_OPEN_SEC + 60, letter: "A+" }];
  const now = etSec(10, 0);
  const result = gradeWithinCandle(tape, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M, now);
  assert.equal(result.letter, "A+");
});

test("gradeWithinCandle: bad inputs return null", () => {
  const tape = [{ t: "2026-09-22T13:44:00Z", letter: "A+" }];
  assert.equal(gradeWithinCandle(null, CANDLE_OPEN_SEC, CANDLE_SECONDS_15M), null);
  assert.equal(gradeWithinCandle(tape, "not a number", CANDLE_SECONDS_15M), null);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, 0), null);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, -900), null);
  assert.equal(gradeWithinCandle(tape, CANDLE_OPEN_SEC, NaN), null);
  assert.equal(gradeWithinCandle(tape, NaN, CANDLE_SECONDS_15M), null);
});

// --- isBullishCallLabel ---

test("isBullishCallLabel: CALL-prefixed and C-digit labels are bullish", () => {
  assert.equal(isBullishCallLabel("CALL5"), true);
  assert.equal(isBullishCallLabel("c5"), true);
});

test("isBullishCallLabel: real chart call labels (CALLD, CALLW, CALLM, C4H, C1H)", () => {
  assert.equal(isBullishCallLabel("CALLD"), true);
  assert.equal(isBullishCallLabel("CALLW"), true);
  assert.equal(isBullishCallLabel("CALLM"), true);
  assert.equal(isBullishCallLabel("C4H"), true);
  assert.equal(isBullishCallLabel("C1H"), true);
});

test("isBullishCallLabel: real chart put labels and non-directional labels", () => {
  assert.equal(isBullishCallLabel("P4H"), false);
  assert.equal(isBullishCallLabel("PUT2H"), false);
  assert.equal(isBullishCallLabel("MACD-M"), false);
});

test("isBullishCallLabel: direction=bullish overrides label", () => {
  assert.equal(isBullishCallLabel("MACD-M", "bullish"), true);
  assert.equal(isBullishCallLabel("whatever", "bullish"), true);
});

test("isBullishCallLabel: PUT/P-digit labels are not bullish", () => {
  assert.equal(isBullishCallLabel("P15"), false);
  assert.equal(isBullishCallLabel("PUT1H"), false);
});

test("isBullishCallLabel: direction=bullish always wins", () => {
  assert.equal(isBullishCallLabel("whatever", "bullish"), true);
});

test("isBullishCallLabel tolerates garbage", () => {
  assert.equal(isBullishCallLabel(null), false);
  assert.equal(isBullishCallLabel(undefined), false);
  assert.equal(isBullishCallLabel(42), false);
});

test("chartLabel: backend 5m chart states read as the spec's words", () => {
  assert.equal(chartLabel("below"), "Below trigger");
  assert.equal(chartLabel("breakout_provisional"), "Breakout – provisional");
  assert.equal(chartLabel("breakout_confirmed"), "Breakout confirmed");
  assert.equal(chartLabel("holding"), "Holding");
  assert.equal(chartLabel("failed"), "Failed");
  assert.equal(chartLabel("something_new"), "something_new", "unknown passes through");
  assert.equal(chartLabel(null), "–");
  assert.equal(chartLabel(undefined), "–");
  assert.equal(chartLabel(5), "–");
});

// --- H/L degree (checks.hlDegree runs -1..+1) ---

test("hlDegreeText shows two decimals, not a rounded integer", () => {
  assert.equal(hlDegreeText(0.79), "0.79");
  assert.equal(hlDegreeText(0.3), "0.30");
  assert.equal(hlDegreeText(-0.456), "-0.46");
  assert.equal(hlDegreeText(1), "1.00");
  assert.equal(hlDegreeText(0), "0.00");
  assert.equal(hlDegreeText(null), "–");
  assert.equal(hlDegreeText(undefined), "–");
  assert.equal(hlDegreeText(Number.NaN), "–");
  assert.equal(hlDegreeText("0.5"), "–", "a string is not a reading");
});

// --- setupParts (phone: the pinned Setup cell stacks letter over momentum) ---

test("setupParts: splits the letter from the momentum words", () => {
  assert.deepEqual(setupParts("A+ 💥 · Building ↑"), { letter: "A+ 💥", momentum: "Building ↑" });
});

test("setupParts: keeps the provisional mark with the momentum", () => {
  assert.deepEqual(setupParts("A · Fading ↓ (prov.)"), { letter: "A", momentum: "Fading ↓ (prov.)" });
});

test("setupParts: letter only -> no momentum", () => {
  assert.deepEqual(setupParts("B"), { letter: "B", momentum: "" });
});

test("setupParts: empty / missing text", () => {
  assert.deepEqual(setupParts(""), { letter: "", momentum: "" });
  assert.deepEqual(setupParts(null), { letter: "", momentum: "" });
});

// --- timelineDetail (the "why" panel: bar time vs seen time) ---
//
// The timeline's own stamp is when a SCAN OBSERVED the change; the bar behind
// it can be much older (a 5m bucket that started 13 minutes earlier, a 4h
// squeeze release from the morning, a 4D Skittles bar that opened yesterday).

const sec = (iso) => Date.parse(iso) / 1000;
const AT_1353 = "2026-09-22T13:53:12-04:00";

function atToday() {
  setFreshClockForTests(() => Date.parse("2026-09-22T14:00:00-04:00"));
}

test("timelineDetail: RVOL prints the bucket's bar time and the seen time", () => {
  atToday();
  const row = { rvol: { "5m": { value: 2.1, barAt: sec("2026-09-22T13:40:00-04:00") } } };
  assert.equal(
    timelineDetail(row, { at: AT_1353, what: "RVOL 5m 2.1" }),
    "RVOL 5m 2.1 · bar 13:40 · seen 13:53",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: a SKIT bar on an earlier ET date carries its date", () => {
  atToday();
  const row = { skittles: { "4D": { bg: "green", barAt: sec("2026-09-21T00:00:00-04:00") } } };
  assert.equal(
    timelineDetail(row, { at: "2026-09-22T13:47:00-04:00", what: "SKIT 4D bg green" }),
    "SKIT 4D bg green · bar 09-21 00:00 · seen 13:47",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: a midnight bar on today's date says so", () => {
  atToday();
  const row = { skittles: { "4D": { bg: "green", barAt: sec("2026-09-22T00:00:00-04:00") } } };
  assert.equal(
    timelineDetail(row, { at: "2026-09-22T13:47:00-04:00", what: "SKIT 4D bg green" }),
    "SKIT 4D bg green · bar 00:00 (today) · seen 13:47",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: SQZ names the release bar", () => {
  atToday();
  const row = { sqzRaw: { "4h": { lastReleaseAt: sec("2026-09-22T09:00:00-04:00") } } };
  assert.equal(
    timelineDetail(row, { at: "2026-09-22T13:12:00-04:00", what: "SQZ 4h released" }),
    "SQZ 4h released · release bar 09:00 · seen 13:12",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: a null lastReleaseAt prints the seen time only, never an invented bar", () => {
  atToday();
  const row = { sqzRaw: { "4h": { lastReleaseAt: null } } };
  assert.equal(
    timelineDetail(row, { at: "2026-09-22T13:12:00-04:00", what: "SQZ 4h released" }),
    "SQZ 4h released · seen 13:12",
  );
  assert.equal(
    timelineDetail({ sqzRaw: { "4h": null } }, { at: "2026-09-22T13:12:00-04:00", what: "SQZ 4h released" }),
    "SQZ 4h released · seen 13:12",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: news adds the publish time when the row carries one", () => {
  atToday();
  assert.equal(
    timelineDetail({}, { at: "2026-09-22T13:20:00-04:00", what: "news" }),
    "news · seen 13:20",
  );
  assert.equal(
    timelineDetail(
      { news: { headline: "Upgrade", at: "2026-09-22T17:05:00Z" } },
      { at: "2026-09-22T13:20:00-04:00", what: "news" },
    ),
    "news · seen 13:20 · published 13:05",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: a headline published on an earlier ET day carries its date", () => {
  atToday();
  assert.equal(
    timelineDetail(
      { news: { headline: "Late filing", at: "2026-09-21T22:30:00Z" } },
      { at: "2026-09-22T09:31:00-04:00", what: "news" },
    ),
    "news · seen 09:31 · published 09-21 18:30",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: a 5m breakout has no bar time -> seen only", () => {
  atToday();
  assert.equal(
    timelineDetail({ rvol: { "5m": { barAt: sec("2026-09-22T09:55:00-04:00") } } },
      { at: "2026-09-22T10:02:00-04:00", what: "5m breakout confirmed" }),
    "5m breakout confirmed · seen 10:02",
  );
  assert.equal(
    timelineDetail({}, { at: "2026-09-22T10:02:00-04:00", what: "WAT 4h something" }),
    "WAT 4h something · seen 10:02",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: garbage never throws and never invents a time", () => {
  atToday();
  assert.equal(timelineDetail(null, null), "");
  assert.equal(timelineDetail(undefined, undefined), "");
  assert.equal(timelineDetail({}, {}), "");
  assert.equal(timelineDetail({}, { what: 5, at: 5 }), "");
  assert.equal(timelineDetail([], { what: "   " }), "");
  assert.equal(timelineDetail("nope", { what: "RVOL 5m 2.1" }), "RVOL 5m 2.1");
  assert.equal(timelineDetail({ rvol: "nope" }, { what: "RVOL 5m 2.1", at: "nope" }), "RVOL 5m 2.1");
  assert.equal(
    timelineDetail({ rvol: { "5m": { barAt: "13:40" } } }, { what: "RVOL 5m 2.1", at: AT_1353 }),
    "RVOL 5m 2.1 · seen 13:53",
  );
  assert.equal(
    timelineDetail({ rvol: { "5m": { barAt: 0 } } }, { what: "RVOL 5m 2.1", at: AT_1353 }),
    "RVOL 5m 2.1 · seen 13:53",
  );
  assert.equal(timelineDetail({ news: { at: 12345 } }, { what: "news", at: AT_1353 }), "news · seen 13:53");
  setFreshClockForTests(null);
});

// --- ADX: a RECORDED FACT, never part of the letter -------------------------
//
// momx/grade_log.py appends "ADX 5m bull cross (ADX 35^)" to the timeline and
// puts "ADX" in gradeFresh.icons (ICON_ORDER, after SQZ). None of this may
// touch setupText / setupTone / setupSortValue - the letter is unchanged.

test("freshText: the ADX icon picks up an ADX timeline item", () => {
  const row = {
    gradeFresh: {
      icons: ["ADX"],
      ageMinutes: null,
      timeline: [{ at: "2026-09-22T17:45:00Z", what: "ADX 5m bull cross (ADX 35↑)" }],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T17:48:00Z"));
  assert.equal(freshText(row), "ADX 5m bull cross (ADX 35↑) 13:45 · 3m");
  setFreshClockForTests(null);
});

test("freshText: ADX sits after SQZ and takes the LATEST ADX item only", () => {
  const row = {
    gradeFresh: {
      icons: FRESH_ICON_ORDER.filter((icon) => icon === "SQZ" || icon === "ADX"),
      timeline: [
        { at: "2026-09-22T17:40:00Z", what: "ADX 30m bull cross (ADX 21)" },
        { at: "2026-09-22T17:41:00Z", what: "SQZ 4h released" },
        { at: "2026-09-22T17:45:00Z", what: "ADX 5m bull cross (ADX 35↑)" },
      ],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T17:46:00Z"));
  assert.equal(
    freshText(row),
    "SQZ 4h released 13:41 · ADX 5m bull cross (ADX 35↑) 13:45 · 1m",
  );
  setFreshClockForTests(null);
});

test("freshText: the ADX matcher does not swallow a differently-named kind", () => {
  const row = {
    gradeFresh: {
      icons: ["ADX"],
      timeline: [{ at: "2026-09-22T17:45:00Z", what: "ADXWIDE something" }],
    },
  };
  setFreshClockForTests(() => Date.parse("2026-09-22T17:46:00Z"));
  assert.equal(freshText(row), "1m"); // the item exists, but not as an ADX icon
  setFreshClockForTests(null);
});

test("freshText: FRESH_ICON_ORDER mirrors momx/grade_log.py ICON_ORDER", () => {
  assert.deepEqual(FRESH_ICON_ORDER, ["SKIT", "RVOL", "SQZ", "ADX", "NEWS"]);
});

test("timelineDetail: an ADX cross prints the seen time and no invented bar", () => {
  atToday();
  const row = { adx: { "5m": { plus: 48.6, minus: 13, adx: 35, rising: true, cross: "bull" } } };
  assert.equal(
    timelineDetail(row, { at: "2026-09-22T13:45:00-04:00", what: "ADX 5m bull cross (ADX 35)" }),
    "ADX 5m bull cross (ADX 35) · seen 13:45",
  );
  setFreshClockForTests(null);
});

test("timelineDetail: an ADX item on a row with no adx field still renders", () => {
  atToday();
  assert.equal(
    timelineDetail({}, { at: "2026-09-22T13:45:00-04:00", what: "ADX 30m bear cross (ADX 18)" }),
    "ADX 30m bear cross (ADX 18) · seen 13:45",
  );
  assert.equal(
    timelineDetail(null, { what: "ADX 5m bull cross (ADX 35↑)" }),
    "ADX 5m bull cross (ADX 35↑)",
  );
  setFreshClockForTests(null);
});

test("adxText: renders the chart's three numbers, arrow only while rising", () => {
  assert.equal(
    adxText({ plus: 48.6, minus: 13.0, adx: 34.6, rising: true }),
    "+DI 48.6 · −DI 13.0 · ADX 34.6 ↑",
  );
  assert.equal(
    adxText({ plus: 48.6, minus: 13.0, adx: 34.6, rising: false }),
    "+DI 48.6 · −DI 13.0 · ADX 34.6",
  );
});

test("adxText: a missing reading is a dash, never a confident 0.0", () => {
  // Number(null) === 0 - the guard has to be on the TYPE, not the value.
  assert.equal(adxText({ plus: null, minus: 13.0, adx: 34.6, rising: true }),
    "+DI – · −DI 13.0 · ADX 34.6 ↑");
  assert.equal(adxText({ plus: 0, minus: 0, adx: 0, rising: false }),
    "+DI 0.0 · −DI 0.0 · ADX 0.0");
});

test("adxText: nothing to show -> empty string, never throws", () => {
  assert.equal(adxText(null), "");
  assert.equal(adxText(undefined), "");
  assert.equal(adxText({}), "");
  assert.equal(adxText("nope"), "");
  assert.equal(adxText({ plus: null, minus: null, adx: null }), "");
});

test("sessionLabel: the chart's session windows, and nothing else", () => {
  assert.equal(sessionLabel("second30m"), "Second 30m (Smart)");
  assert.equal(sessionLabel("first30m"), "First 30m (Smart)");
  assert.equal(sessionLabel("powerHour"), "Power Hour");
  assert.equal(sessionLabel("premarket"), "Premarket");
  assert.equal(sessionLabel("nope"), null);
  assert.equal(sessionLabel(null), null);
  assert.equal(sessionLabel(undefined), null);
});

test("ADX never reaches the letter: setupText/Tone/Sort ignore row.adx", () => {
  const adx = { "5m": { plus: 48.6, minus: 13, adx: 35, cross: "bull", rising: true } };
  const plain = { grade: { letter: "B" }, m5: { state: "holding" } };
  const withAdx = { ...plain, adx };
  assert.equal(setupText(withAdx), setupText(plain));
  assert.equal(setupTone(withAdx), setupTone(plain));
  assert.equal(setupSortValue(withAdx), setupSortValue(plain));
});

// --- gradeUnderwaterNow ---
//
// GOOGL 2026-09-22: A+ stamped at 09:35 with price climbing, then gave the
// whole day back. The letter stays; the circle is struck through.
// A trigger-based rule was measured first and thrown away - it fired on 94.5%
// of today's 4,026 graded signals, which is no information at all.
const ENTRY = 358;
const CANDLE = Date.parse("2026-09-22T13:35:00Z") / 1000; // 09:35 ET

const bar = (hhmm, close) => ({
  time: Date.parse(`2026-09-22T${hhmm}:00Z`) / 1000,
  close,
});

test("gradeUnderwaterNow: the LATEST bar below the entry marks it underwater", () => {
  const bars = [bar("13:40", 361), bar("13:45", 363), bar("15:10", 352.4)];
  assert.equal(gradeUnderwaterNow(ENTRY, bars, CANDLE), bar("15:10", 0).time);
});

test("gradeUnderwaterNow: a dip that RECOVERS is not underwater", () => {
  // This is the whole point of using the latest bar, not the first breach.
  const bars = [bar("13:40", 350), bar("13:45", 352), bar("15:10", 361)];
  assert.equal(gradeUnderwaterNow(ENTRY, bars, CANDLE), null);
});

test("gradeUnderwaterNow: bars out of order still resolve to the latest one", () => {
  const bars = [bar("15:10", 352), bar("13:40", 361)];
  assert.equal(gradeUnderwaterNow(ENTRY, bars, CANDLE), bar("15:10", 0).time);
});

test("gradeUnderwaterNow: the signal's OWN candle and earlier bars are ignored", () => {
  const bars = [bar("13:30", 350), bar("13:35", 350)];
  assert.equal(gradeUnderwaterNow(ENTRY, bars, CANDLE), null);
});

test("gradeUnderwaterNow: exactly AT the entry is not below it", () => {
  assert.equal(gradeUnderwaterNow(ENTRY, [bar("15:10", 358)], CANDLE), null);
  assert.ok(gradeUnderwaterNow(ENTRY, [bar("15:10", 357.99)], CANDLE));
});

test("gradeUnderwaterNow: no entry price means no verdict, never a failure", () => {
  const bars = [bar("15:10", 1)];
  assert.equal(gradeUnderwaterNow(null, bars, CANDLE), null);
  assert.equal(gradeUnderwaterNow(undefined, bars, CANDLE), null);
  assert.equal(gradeUnderwaterNow(0, bars, CANDLE), null);
  assert.equal(gradeUnderwaterNow(-5, bars, CANDLE), null);
  assert.equal(gradeUnderwaterNow("abc", bars, CANDLE), null);
});

test("gradeUnderwaterNow: garbage bars never throw", () => {
  assert.equal(gradeUnderwaterNow(ENTRY, null, CANDLE), null);
  assert.equal(gradeUnderwaterNow(ENTRY, [], CANDLE), null);
  assert.equal(gradeUnderwaterNow(ENTRY, [null, {}, { time: "x" }], CANDLE), null);
  assert.equal(gradeUnderwaterNow(ENTRY, [bar("15:10", 352)], NaN), null);
});

// --------------------------------------------------------------------------
// BEAR rows (spec 2026-09-24)
// --------------------------------------------------------------------------

import { isBearishPutLabel } from "./momxGrade.js";

test("bear rows get the red tones and the flipped momentum words", () => {
  const row = { direction: "bear", grade: { letter: "A+" }, m5: { state: "building" } };
  assert.equal(setupTone(row), "aplus-bear");
  assert.equal(setupTone({ ...row, grade: { letter: "A" } }), "a-bear");
  assert.equal(setupTone({ ...row, grade: { letter: "B" } }), "b");
  assert.equal(momentumTone(row), "down");
  assert.equal(momentumTone({ ...row, m5: { state: "fading" } }), "up");
  assert.equal(setupText(row), "A+ · Building ↓");
  assert.equal(setupText({ ...row, m5: { state: "fading" } }), "A+ · Fading ↑");
  assert.equal(setupText({ ...row, direction: "bull" }), "A+ · Building ↑");
  assert.equal(chartLabel("breakout_confirmed", "bear"), "Breakdown confirmed");
  assert.equal(chartLabel("above", "bear"), "Above trigger");
  assert.equal(chartLabel("breakout_confirmed"), "Breakout confirmed");
});

test("bear track record says closed lower", () => {
  const record = { direction: "bear", source: "recorded", letters: { "A+": { count: 3, pctHigherClose: 66.7, avgToClose: 1.2 } } };
  assert.match(trackRecordLine(record, "A+"), /closed lower/);
  assert.match(trackRecordLine({ ...record, direction: "bull" }, "A+"), /closed higher/);
});

test("isBearishPutLabel", () => {
  assert.equal(isBearishPutLabel("PUT2H", "PUT"), true);
  assert.equal(isBearishPutLabel("P4H", "PUT"), true);
  assert.equal(isBearishPutLabel("anything", "PUT"), true);
  assert.equal(isBearishPutLabel("CALL2H", "CALL"), false);
  assert.equal(isBearishPutLabel("C2H", "CALL"), false);
});

test("gradeUnderwaterNow on a bear entry: underwater means price closed ABOVE the fire", () => {
  const bars = [bar("15:00", 100.0), bar("15:05", 101.0), bar("15:10", 103.0)];
  const candle = bar("15:00", 0).time;
  // Bull: 103 > 100 is fine. Bear: the short is underwater at 103.
  assert.equal(gradeUnderwaterNow(100.0, bars, candle), null);
  assert.equal(gradeUnderwaterNow(100.0, bars, candle, "bear"), bar("15:10", 0).time);
  const drop = [bar("15:00", 100.0), bar("15:05", 99.0), bar("15:10", 97.0)];
  assert.equal(gradeUnderwaterNow(100.0, drop, candle, "bear"), null);
  assert.equal(gradeUnderwaterNow(100.0, drop, candle), bar("15:10", 0).time);
});
