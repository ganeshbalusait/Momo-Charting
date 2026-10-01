import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_HISTORY_QUERY,
  HISTORY_QUERY_SECTIONS,
  buildCondition,
  describeHistoryQuery,
  groupResultsByDay,
  historyQueryParams,
  historyQueryProblem,
  selectedStates,
  summariseResults,
} from "./momxHistoryQuery.js";
import {
  FILTER_SKITTLES_TIMEFRAMES,
  RVOL_BULLISH_BG,
  RVOL_BULLISH_FG,
  SKITTLES_STATE_BG,
  SQZ_STATE_BG,
} from "./momxFilters.js";

const clone = (value) => JSON.parse(JSON.stringify(value));
const base = (patch = {}) => ({ ...clone(DEFAULT_HISTORY_QUERY), ...patch });

// --------------------------------------------------------------------------
// the condition - the ONLY thing the server is told
// --------------------------------------------------------------------------

test("an RVOL query sends the threshold and the bullish colour lists", () => {
  const condition = buildCondition(base({ section: "rvol", timeframe: "1h", min: 3 }));
  assert.deepEqual(condition, {
    section: "rvol",
    timeframe: "1h",
    min: 3,
    bg: [...RVOL_BULLISH_BG],
    fg: [...RVOL_BULLISH_FG],
  });
});

// The colour lists must be THE board's lists, not a second copy typed out
// here - that duplication is the failure this whole design avoids.
test("the bullish lists are momxFilters' own, not a local copy", () => {
  const condition = buildCondition(base());
  assert.deepEqual(condition.bg, ["cyan", "green"]);
  assert.deepEqual(condition.fg, ["cyan", "green", "dark_green"]);
  assert.deepEqual(condition.bg, [...RVOL_BULLISH_BG]);
});

test("unticking 'buyers winning' drops the colour constraint entirely", () => {
  const condition = buildCondition(base({ bullishOnly: false }));
  assert.deepEqual(condition.bg, []);
  assert.deepEqual(condition.fg, []);
  assert.equal(condition.min, 3);
});

test("a Skittles query sends BACKGROUNDS only", () => {
  const condition = buildCondition(
    base({ section: "skittles", timeframe: "2h" }),
  );
  assert.equal(condition.section, "skittles");
  assert.equal(condition.min, null);
  assert.deepEqual(condition.bg, ["cyan", "green", "lime", "dark_green"]);
  // Sending fg would quietly widen it: a cyan Skittles NUMBER means "9 already
  // above 20", a state rather than the cross.
  assert.deepEqual(condition.fg, []);
});

test("Skittles colour names resolve through the board's own state table", () => {
  const state = base({ section: "skittles", timeframe: "4h" });
  state.states.skittles = ["magenta", "plum"];
  const condition = buildCondition(state);
  assert.deepEqual(condition.bg, [SKITTLES_STATE_BG.magenta, SKITTLES_STATE_BG.plum]);
});

// SQZ's "blank" state is painted BLACK - the name in the panel and the colour
// on the cell are different strings, and the server must get the colour.
test("the SQZ blank state resolves to its background colour, not its label", () => {
  const state = base({ section: "sqz", timeframe: "2h" });
  state.states.sqz = ["cyan", "blank"];
  const condition = buildCondition(state);
  assert.deepEqual(condition.bg, ["cyan", "black"]);
  assert.equal(SQZ_STATE_BG.blank, "black");
});

test("an unknown colour name is dropped rather than sent", () => {
  const state = base({ section: "skittles", timeframe: "2h" });
  state.states.skittles = ["cyan", "chartreuse"];
  assert.deepEqual(selectedStates(state), ["cyan"]);
  assert.deepEqual(buildCondition(state).bg, ["cyan"]);
});

// --------------------------------------------------------------------------
// refusing to run a query that would answer the wrong question
// --------------------------------------------------------------------------

test("a valid default query has no problem", () => {
  assert.equal(historyQueryProblem(base()), "");
});

test("a colour section with nothing ticked refuses, and says why", () => {
  const state = base({ section: "skittles", timeframe: "2h" });
  state.states.skittles = [];
  const problem = historyQueryProblem(state);
  assert.match(problem, /tick at least one/i);
  assert.equal(buildCondition(state), null);
  assert.equal(historyQueryParams(state, { list: "Watchlist" }), null);
});

// Number("") and Number(null) are both 0, so a blank box would become
// "RVOL >= 0" - every snapshot in the archive, dressed as a search result.
test("a blank or non-numeric threshold refuses rather than becoming zero", () => {
  for (const min of ["", "   ", null, undefined, "abc", NaN]) {
    assert.match(historyQueryProblem(base({ min })), /number/i, String(min));
    assert.equal(buildCondition(base({ min })), null, String(min));
  }
  // ...but a real zero, typed deliberately, is a valid threshold.
  assert.equal(historyQueryProblem(base({ min: 0 })), "");
  assert.equal(buildCondition(base({ min: 0 })).min, 0);
});

test("a timeframe the column does not have refuses", () => {
  // 5m is an RVOL timeframe but not a Skittles one.
  const state = base({ section: "skittles", timeframe: "5m" });
  assert.equal(FILTER_SKITTLES_TIMEFRAMES.includes("5m"), false);
  assert.match(historyQueryProblem(state), /timeframe/i);
});

test("an unknown section refuses", () => {
  assert.match(historyQueryProblem(base({ section: "quote" })), /column/i);
});

// --------------------------------------------------------------------------
// the request
// --------------------------------------------------------------------------

test("day scope pins the date; all scope sends none", () => {
  const day = historyQueryParams(base({ scope: "day" }), {
    list: "Watchlist",
    date: "2026-09-04",
  });
  assert.equal(day.get("date"), "2026-09-04");
  assert.equal(day.get("list"), "Watchlist");
  assert.equal(day.get("section"), "rvol");
  assert.equal(day.get("timeframe"), "1h");
  assert.equal(day.get("min"), "3");
  assert.equal(day.get("bg"), "cyan,green");
  assert.equal(day.get("fg"), "cyan,green,dark_green");

  const all = historyQueryParams(base({ scope: "all" }), {
    list: "Watchlist",
    date: "2026-09-04",
  });
  assert.equal(all.has("date"), false, "no date means every archived day");
});

test("a colour query sends no min at all", () => {
  const params = historyQueryParams(base({ section: "sqz", timeframe: "2h" }), {
    list: "Watchlist",
  });
  assert.equal(params.has("min"), false);
  assert.equal(params.get("bg"), "cyan");
});

// --------------------------------------------------------------------------
// what he reads
// --------------------------------------------------------------------------

test("the description reads as a sentence, in his vocabulary", () => {
  assert.equal(
    describeHistoryQuery(base({ scope: "day" })),
    "RVOL 1h ≥ 3, buyers winning — on this day",
  );
  assert.equal(
    describeHistoryQuery(base({ scope: "all", bullishOnly: false })),
    "RVOL 1h ≥ 3 — across every archived day",
  );
  const skittles = base({ section: "skittles", timeframe: "2h", scope: "all" });
  skittles.states.skittles = ["cyan", "green"];
  assert.equal(
    describeHistoryQuery(skittles),
    "SKITTLES 2h is cyan or green — across every archived day",
  );
});

test("the description becomes the reason when the query cannot run", () => {
  const state = base({ section: "sqz", timeframe: "2h" });
  state.states.sqz = [];
  assert.match(describeHistoryQuery(state), /tick at least one/i);
});

test("the summary counts tickers and days", () => {
  assert.equal(
    summariseResults({ tickers: 28, scannedDays: 1, truncated: false }),
    "28 tickers over 1 day",
  );
  assert.equal(
    summariseResults({ tickers: 1, scannedDays: 6, truncated: false }),
    "1 ticker over 6 days",
  );
});

// A truncated answer that reads like a complete one is the same lie as
// searching 3% of a day.
test("a truncated answer says so", () => {
  const text = summariseResults({
    tickers: 2000, scannedDays: 30, truncated: true,
    returnedResults: 2000, totalResults: 2431,
  });
  assert.match(text, /showing the top 2000 of 2431/);
});

test("an empty answer distinguishes 'nothing matched' from 'nothing to search'", () => {
  assert.match(summariseResults({ tickers: 0, scannedDays: 4 }), /Nothing matched in 4 days/);
  assert.match(summariseResults({ tickers: 0, scannedDays: 0 }), /No archived days/);
  assert.equal(summariseResults(null), "");
});

// --------------------------------------------------------------------------
// grouping
// --------------------------------------------------------------------------

test("results group by day, newest day first, strongest ticker first", () => {
  const grouped = groupResultsByDay({
    results: [
      { date: "2026-09-03", symbol: "OLD", peak: 9 },
      { date: "2026-09-04", symbol: "WEAK", peak: 3.2 },
      { date: "2026-09-04", symbol: "STRONG", peak: 5 },
    ],
  });
  assert.deepEqual(grouped.map((day) => day.date), ["2026-09-04", "2026-09-03"]);
  assert.deepEqual(grouped[0].rows.map((row) => row.symbol), ["STRONG", "WEAK"]);
});

test("a row with no peak sorts last rather than vanishing", () => {
  const grouped = groupResultsByDay({
    results: [
      { date: "2026-09-04", symbol: "NONUM", peak: null },
      { date: "2026-09-04", symbol: "HASNUM", peak: 1 },
    ],
  });
  assert.deepEqual(grouped[0].rows.map((row) => row.symbol), ["HASNUM", "NONUM"]);
});

test("grouping tolerates a junk payload", () => {
  assert.deepEqual(groupResultsByDay(null), []);
  assert.deepEqual(groupResultsByDay({ results: "nope" }), []);
  assert.deepEqual(groupResultsByDay({ results: [null, { symbol: "NODATE" }] }), []);
});

// --------------------------------------------------------------------------
// the section table itself
// --------------------------------------------------------------------------

// A section whose default timeframe is not in its own list would render a
// control that refuses to search the moment it opens.
test("every section's default timeframe is one it actually has", () => {
  for (const section of HISTORY_QUERY_SECTIONS) {
    assert.ok(
      section.timeframes.includes(section.defaultTimeframe),
      `${section.key} default ${section.defaultTimeframe}`,
    );
    if (section.kind === "colour") {
      for (const name of section.defaultStates) {
        assert.ok(name in section.states, `${section.key} default state ${name}`);
      }
    }
  }
});

test("historyQueryParams sends dir=bear only for the bear board", () => {
  const state = { section: "rvol", timeframe: "1h", min: "2", scope: "all" };
  const bear = historyQueryParams(state, { list: "Mag7", direction: "bear" });
  const bull = historyQueryParams(state, { list: "Mag7" });
  assert.ok(bear && bear.get("dir") === "bear");
  assert.ok(bull && bull.get("dir") === null);
});
