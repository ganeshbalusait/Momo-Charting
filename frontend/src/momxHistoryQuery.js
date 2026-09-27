// "Which tickers ever hit this?" - the History tab's search over the 30-day
// archive.
//
// His ask, verbatim (2026-09-05): "1h Rvol i will get result which tickers
// have rvol of 1hr". So the answer is a TICKER LIST. Measured on his own
// archive, "Skittles 2h cyan or magenta" on 2026-09-04 is 455 snapshots but
// only 30 tickers - the raw snapshot list is the same name every two minutes.
//
// THIS MODULE BUILDS THE CONDITION; THE SERVER APPLIES IT.
//
// The search cannot run in the browser: a day file is 8.8-25.5 MB and the
// history endpoint only ever ships the newest 400 of a day's ~11,770
// snapshots. Filtering what is already here would search 3% of the day and
// present the result as though it covered all of it.
//
// So the colours travel in the request. momx/query.py never learns what
// "cyan" means, what counts as bullish, or what a good RVOL is - it applies a
// condition built HERE, from the same constants that paint the live board.
// That is the whole defence against the failure this repo keeps hitting: two
// copies of one rule drifting apart. A search result cannot disagree with the
// board when both read the same lists.

import {
  FILTER_RVOL_TIMEFRAMES,
  FILTER_SKITTLES_TIMEFRAMES,
  FILTER_SQZ_TIMEFRAMES,
  RVOL_BEARISH_BG,
  RVOL_BEARISH_FG,
  RVOL_BULLISH_BG,
  RVOL_BULLISH_FG,
  SKITTLES_STATE_BG,
  SQZ_STATE_BG,
} from "./momxFilters.js";

/**
 * The three searchable columns. `kind` decides which control the panel draws:
 * RVOL is a number with a direction, SQZ and Skittles are a set of colours.
 */
export const HISTORY_QUERY_SECTIONS = [
  {
    key: "rvol",
    label: "RVOL",
    kind: "threshold",
    timeframes: FILTER_RVOL_TIMEFRAMES,
    defaultTimeframe: "1h",
  },
  {
    key: "sqz",
    label: "SQZ",
    kind: "colour",
    timeframes: FILTER_SQZ_TIMEFRAMES,
    states: SQZ_STATE_BG,
    defaultTimeframe: "2h",
    defaultStates: ["cyan"],
  },
  {
    key: "skittles",
    label: "SKITTLES",
    kind: "colour",
    timeframes: FILTER_SKITTLES_TIMEFRAMES,
    states: SKITTLES_STATE_BG,
    defaultTimeframe: "2h",
    // The four bullish crosses, same default as the live FILTERS panel.
    defaultStates: ["cyan", "green", "lime", "dark_green"],
  },
];

export const HISTORY_QUERY_SECTION_BY_KEY = new Map(
  HISTORY_QUERY_SECTIONS.map((section) => [section.key, section]),
);

/** Search this day only, or every day the archive holds. */
export const HISTORY_QUERY_SCOPES = ["day", "all"];

export const DEFAULT_HISTORY_QUERY = Object.freeze({
  section: "rvol",
  timeframe: "1h",
  // 3.0, not the board's 2.5: a search is looking for the standout moments of
  // a whole month, where the live board is narrowing a single snapshot.
  min: 3,
  bullishOnly: true,
  // Per-section colour ticks, so switching section and back does not lose them.
  states: Object.freeze({
    sqz: Object.freeze(["cyan"]),
    skittles: Object.freeze(["cyan", "green", "lime", "dark_green"]),
  }),
  scope: "day",
});

function sectionOf(state) {
  return HISTORY_QUERY_SECTION_BY_KEY.get((state && state.section) || "") || null;
}

/** The colour names ticked for whichever section is selected. */
export function selectedStates(state) {
  const section = sectionOf(state);
  if (!section || section.kind !== "colour") return [];
  const picked = ((state && state.states) || {})[section.key];
  const known = Object.keys(section.states);
  return (Array.isArray(picked) ? picked : []).filter((name) => known.includes(name));
}

/**
 * Why this query cannot run yet, or "" when it can.
 *
 * Returned as a SENTENCE rather than a boolean so the panel can say what is
 * missing instead of just greying the button out - a disabled control with no
 * reason is the shape of thing he has rejected all night.
 */
export function historyQueryProblem(state) {
  const section = sectionOf(state);
  if (!section) return "Pick a column to search.";
  if (!section.timeframes.includes(state.timeframe)) {
    return `Pick a timeframe for ${section.label}.`;
  }
  if (section.kind === "threshold") {
    // NOT `Number.isFinite(Number(state.min))`. Number("") and Number(null)
    // are both 0, so an empty box would silently become "RVOL >= 0" - a query
    // matching every snapshot in the archive, presented as a search result.
    // This project has already shipped that exact bug twice (see the
    // Number(null) note in App.jsx).
    const raw = typeof state.min === "number" ? state.min : String(state.min ?? "").trim();
    if (raw === "") return "Enter a number to search above.";
    if (!Number.isFinite(Number(raw))) return "Enter a number to search above.";
    return "";
  }
  if (selectedStates(state).length === 0) {
    return `Tick at least one ${section.label} colour.`;
  }
  return "";
}

/**
 * The condition the server applies. Colours are resolved HERE - the server is
 * handed lists, never rules.
 */
export function buildCondition(state, direction = "bull") {
  if (historyQueryProblem(state)) return null;
  const section = sectionOf(state);
  if (section.kind === "threshold") {
    return {
      section: section.key,
      timeframe: state.timeframe,
      min: Number(state.min),
      // Direction is a COLOUR question: at 2.0+ the background carries it, and
      // below 2.0 only the text colour does - so both lists go, exactly as
      // momxFilters' bullishOnly reads them.
      // On the BEAR archive the same box means SELLERS winning (2026-09-26),
      // exactly as the live FILTERS' "bullish only" flips on a bear row.
      bg: state.bullishOnly ? [...(direction === "bear" ? RVOL_BEARISH_BG : RVOL_BULLISH_BG)] : [],
      fg: state.bullishOnly ? [...(direction === "bear" ? RVOL_BEARISH_FG : RVOL_BULLISH_FG)] : [],
    };
  }
  return {
    section: section.key,
    timeframe: state.timeframe,
    min: null,
    // BACKGROUNDS only. A cyan Skittles NUMBER means "9 already above 20",
    // which is a state and not the cross - sending it as `fg` would quietly
    // widen every Skittles search.
    bg: selectedStates(state).map((name) => section.states[name]),
    fg: [],
  };
}

/** The query string for /api/momx-scanner/history-query. */
export function historyQueryParams(state, { list, date, direction } = {}) {
  const condition = buildCondition(state, direction);
  if (!condition) return null;
  const params = new URLSearchParams();
  if (list) params.set("list", list);
  // The bear archive (spec 2026-09-24).
  if (direction === "bear") params.set("dir", "bear");
  params.set("section", condition.section);
  params.set("timeframe", condition.timeframe);
  if (condition.min !== null && condition.min !== undefined) {
    params.set("min", String(condition.min));
  }
  if (condition.bg.length) params.set("bg", condition.bg.join(","));
  if (condition.fg.length) params.set("fg", condition.fg.join(","));
  // "day" scope pins the date; "all" sends none, which the server reads as
  // "every day this board has".
  if (state.scope === "day" && date) params.set("date", date);
  return params;
}

/** A sentence describing what will be searched, in his vocabulary. */
export function describeHistoryQuery(state) {
  const problem = historyQueryProblem(state);
  if (problem) return problem;
  const section = sectionOf(state);
  const where = state.scope === "all" ? "across every archived day" : "on this day";
  if (section.kind === "threshold") {
    const direction = state.bullishOnly ? ", buyers winning" : "";
    return `${section.label} ${state.timeframe} ≥ ${Number(state.min)}${direction} — ${where}`;
  }
  const names = selectedStates(state);
  return `${section.label} ${state.timeframe} is ${names.join(" or ")} — ${where}`;
}

/** The headline above the results table. */
export function summariseResults(payload) {
  if (!payload) return "";
  const tickers = Number(payload.tickers) || 0;
  const days = Number(payload.scannedDays) || 0;
  if (tickers === 0) {
    return days === 0
      ? "No archived days to search yet."
      : `Nothing matched in ${days} day${days === 1 ? "" : "s"}.`;
  }
  const head = `${tickers} ticker${tickers === 1 ? "" : "s"} over ${days} day${days === 1 ? "" : "s"}`;
  // NEVER a silent cap: a truncated answer that reads like a complete one is
  // the same lie as filtering 3% of a day.
  return payload.truncated
    ? `${head} — showing the top ${payload.returnedResults} of ${payload.totalResults}`
    : head;
}

/**
 * Results grouped by day, newest first, each day's tickers strongest first.
 *
 * The server already sorts this way; regrouping here means the panel renders
 * day headers without a second sort and without trusting the order blindly.
 */
export function groupResultsByDay(payload) {
  const rows = (payload && Array.isArray(payload.results) ? payload.results : []).slice();
  const byDay = new Map();
  for (const row of rows) {
    if (!row || !row.date) continue;
    if (!byDay.has(row.date)) byDay.set(row.date, []);
    byDay.get(row.date).push(row);
  }
  return [...byDay.keys()]
    .sort()
    .reverse()
    .map((date) => ({
      date,
      rows: byDay.get(date).slice().sort((a, b) => {
        const left = Number.isFinite(Number(a.peak)) ? Number(a.peak) : -Infinity;
        const right = Number.isFinite(Number(b.peak)) ? Number(b.peak) : -Infinity;
        if (left !== right) return right - left;
        return String(a.symbol || "").localeCompare(String(b.symbol || ""));
      }),
    }));
}
