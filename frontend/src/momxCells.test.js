import assert from "node:assert/strict";

// Every other *.test.js here runs under `node --test src/*.test.js`, but the
// MomoX board task specifies `npx vitest run src/momxCells.test.js`. Picking the
// runner at import time keeps this file green under BOTH: vitest sets
// process.env.VITEST, and the import is dynamic so `node --test` never has to
// resolve the "vitest" specifier. Assertions stay on node:assert, which both
// runners support unchanged.
const { test } = process.env.VITEST ? await import("vitest") : await import("node:test");

import {
  MOMX_BOARD_CACHE_MAX_ROWS,
  MOMX_BOARD_CACHE_PREFIX,
  MOMX_DEFAULT_SORT,
  MOMX_INDUSTRY_PALETTE,
  MOMX_MATCHED_NEW_MS,
  MOMX_MOMENTUM_FADE_MS,
  MOMX_MOMENTUM_FRESH_MS,
  MOMX_MOMENTUM_MAX_CHIPS,
  MOMX_NEWS_FRESH_MS,
  THINKSCRIPT_COLORS,
  ageOf,
  badgeOf,
  boardCacheEntry,
  capMomentumEvents,
  cellStyle,
  decideBoardView,
  fireLabel,
  firesOf,
  filterByIndustries,
  formatPctChange,
  formatRvol,
  formatSkittles,
  industryColor,
  industryCounts,
  matchedSinceLabel,
  momentumAgeBucket,
  momentumChipLabel,
  momentumEventTime,
  momxBoardCacheKey,
  newMatchSymbols,
  cellIsLiveSpike,
  rvolSaturationNote,
  cellAgeClock,
  hasPaintedBackground,
  cellAgeLabel,
  cellAgeIsFresh,
  newsOf,
  newsTimeLabel,
  quoteTrendBars,
  readBoardCache,
  sortRows,
  liveHighLowCell,
  livePctChange,
  sparklinePath,
  writeBoardCache,
  newsBadgeTitle,
} from "./momxCells.js";

// The payload contract fixes this list. The backend emits these lowercase
// thinkScript names and never hex, so a missing name here is a cell that
// renders with no colour at all.
const CONTRACT_COLOR_NAMES = [
  "cyan",
  "magenta",
  "green",
  "red",
  "light_red",
  "plum",
  "lime",
  "dark_green",
  "dark_red",
  "violet",
  "downtick",
  "orange",
  "white",
  "black",
  "gray",
];

test("THINKSCRIPT_COLORS covers exactly the payload contract palette", () => {
  assert.deepEqual(Object.keys(THINKSCRIPT_COLORS).sort(), [...CONTRACT_COLOR_NAMES].sort());
  for (const name of CONTRACT_COLOR_NAMES) {
    assert.match(THINKSCRIPT_COLORS[name], /^#[0-9a-f]{6}$/, `${name} must be a 6-digit lowercase hex`);
  }
});

test("THINKSCRIPT_COLORS keeps the skittle ladder visually separable", () => {
  // The skittles ladder distinguishes lime (4x8 cross), green (MACD cross in
  // trend) and dark_green (MACD cross alone). If any two collide the trader
  // cannot tell those states apart on the board.
  const greens = [THINKSCRIPT_COLORS.lime, THINKSCRIPT_COLORS.green, THINKSCRIPT_COLORS.dark_green];
  assert.equal(new Set(greens).size, 3);
  const reds = [
    THINKSCRIPT_COLORS.red,
    THINKSCRIPT_COLORS.light_red,
    THINKSCRIPT_COLORS.dark_red,
    THINKSCRIPT_COLORS.downtick,
  ];
  assert.equal(new Set(reds).size, 4);
});

test("cellStyle maps a fully coloured cell", () => {
  assert.deepEqual(cellStyle({ value: 3.2, bg: "cyan", fg: "black" }), {
    backgroundColor: THINKSCRIPT_COLORS.cyan,
    color: THINKSCRIPT_COLORS.black,
  });
});

test("cellStyle treats black as a real colour, not as absent", () => {
  // Color.BLACK is the resting state of every thinkScript column: it must
  // paint the cell black, not fall through to the row background.
  assert.equal(cellStyle({ value: 0.4, bg: "black", fg: "dark_green" }).backgroundColor, "#000000");
});

test("cellStyle survives null, undefined and half-filled cells", () => {
  const blank = { backgroundColor: "transparent", color: "inherit" };
  assert.deepEqual(cellStyle(null), blank);
  assert.deepEqual(cellStyle(undefined), blank);
  assert.deepEqual(cellStyle({}), blank);
  assert.deepEqual(cellStyle({ value: 1, bg: null, fg: null }), blank);
  assert.deepEqual(cellStyle({ value: 1, bg: "orange", fg: null }), {
    backgroundColor: THINKSCRIPT_COLORS.orange,
    color: "inherit",
  });
  assert.deepEqual(cellStyle({ value: 1, bg: null, fg: "magenta" }), {
    backgroundColor: "transparent",
    color: THINKSCRIPT_COLORS.magenta,
  });
});

test("cellStyle falls back instead of throwing on an unknown colour name", () => {
  assert.deepEqual(cellStyle({ value: 1, bg: "chartreuse", fg: "puce" }), {
    backgroundColor: "transparent",
    color: "inherit",
  });
  assert.doesNotThrow(() => cellStyle({ bg: 42, fg: {} }));
  assert.deepEqual(cellStyle("cyan"), { backgroundColor: "transparent", color: "inherit" });
});

test("cellStyle normalises case and stray whitespace", () => {
  assert.equal(cellStyle({ bg: " CYAN " }).backgroundColor, THINKSCRIPT_COLORS.cyan);
  assert.equal(cellStyle({ fg: "Dark_Green" }).color, THINKSCRIPT_COLORS.dark_green);
});

test("formatRvol renders one decimal", () => {
  assert.equal(formatRvol(1.8), "1.8");
  assert.equal(formatRvol(2), "2.0");
  assert.equal(formatRvol(-1.24), "-1.2");
  assert.equal(formatRvol("3.25"), "3.3");
});

test("formatRvol blanks missing values and never prints negative zero", () => {
  assert.equal(formatRvol(null), "");
  assert.equal(formatRvol(undefined), "");
  assert.equal(formatRvol(Number.NaN), "");
  assert.equal(formatRvol(""), "");
  assert.equal(formatRvol("n/a"), "");
  assert.equal(formatRvol(-0.04), "0.0");
});

test("formatSkittles rounds the StochasticFast value to an integer", () => {
  assert.equal(formatSkittles(68.4), "68");
  assert.equal(formatSkittles(68.5), "69");
  assert.equal(formatSkittles(0), "0");
  assert.equal(formatSkittles(100), "100");
  assert.equal(formatSkittles("9.6"), "10");
});

test("formatSkittles blanks missing values", () => {
  assert.equal(formatSkittles(null), "");
  assert.equal(formatSkittles(undefined), "");
  assert.equal(formatSkittles(Number.NaN), "");
  assert.equal(formatSkittles("-"), "");
});

test("formatPctChange prints a signed two-decimal percentage", () => {
  assert.equal(formatPctChange(-1.59), "-1.59%");
  assert.equal(formatPctChange(1.59), "+1.59%");
  assert.equal(formatPctChange(12), "+12.00%");
  assert.equal(formatPctChange("-0.5"), "-0.50%");
});

test("formatPctChange leaves flat unsigned and blanks missing values", () => {
  assert.equal(formatPctChange(0), "0.00%");
  assert.equal(formatPctChange(-0.001), "0.00%");
  assert.equal(formatPctChange(0.004), "0.00%");
  assert.equal(formatPctChange(null), "");
  assert.equal(formatPctChange(undefined), "");
  assert.equal(formatPctChange(Number.NaN), "");
});

test("sparklinePath draws through the values scaled to the box", () => {
  const d = sparklinePath([1, 2, 3], 100, 20);
  // Low value pins to the bottom, high value to the top, x spreads edge to edge.
  assert.equal(d, "M 0,20 L 50,10 L 100,0");
});

test("sparklinePath needs at least two points", () => {
  assert.equal(sparklinePath([], 100, 20), "");
  assert.equal(sparklinePath([5], 100, 20), "");
  assert.equal(sparklinePath(null, 100, 20), "");
  assert.equal(sparklinePath(undefined, 100, 20), "");
  assert.equal(sparklinePath("not an array", 100, 20), "");
  assert.equal(sparklinePath([5, null], 100, 20), "");
});

test("sparklinePath ignores non-numeric points", () => {
  // A gap is dropped, not plotted as zero, and the remaining points respace
  // across the full width.
  assert.equal(sparklinePath([1, null, 3], 100, 20), "M 0,20 L 100,0");
  // A numeric string is a value, not a gap: it stays in the path.
  assert.equal(sparklinePath([1, Number.NaN, "2", 3], 100, 20), "M 0,20 L 50,10 L 100,0");
  assert.equal(sparklinePath([1, 2, 2.5, 3], 100, 20), "M 0,20 L 33.33,10 L 66.67,5 L 100,0");
});

test("sparklinePath draws a flat centre line when every value is identical", () => {
  // max === min would divide by zero; a dead-flat tape must still render.
  assert.equal(sparklinePath([7, 7, 7], 100, 20), "M 0,10 L 50,10 L 100,10");
  assert.equal(sparklinePath([0, 0], 100, 20), "M 0,10 L 100,10");
});

test("sparklinePath refuses a degenerate box instead of emitting NaN", () => {
  assert.equal(sparklinePath([1, 2, 3], 0, 20), "");
  assert.equal(sparklinePath([1, 2, 3], 100, 0), "");
  assert.equal(sparklinePath([1, 2, 3], Number.NaN, 20), "");
});

test("quoteTrendBars ignores price entirely - geometry is DIRECTION only", () => {
  // The regression this pins (3dd0455). The column used to scale each bar
  // to its close within the window, so on closes [100,99,98,97,98] the
  // single UP tick drew SHORTER than two of the downs - the eye read a
  // magnitude his script never encodes. His script has exactly three
  // heights, so price must not reach the geometry at all.
  const priced = quoteTrendBars(
    [
      { value: -1, price: 100 },
      { value: 1, price: 110 },
      { value: 1, price: 105 },
    ],
    30,
    20,
  );
  const unpriced = quoteTrendBars(
    [{ value: -1 }, { value: 1 }, { value: 1 }],
    30,
    20,
  );
  assert.equal(priced.length, 3);
  // Same directions, wildly different prices, identical bars.
  for (let i = 0; i < priced.length; i += 1) {
    assert.equal(priced[i].y, unpriced[i].y);
    assert.equal(priced[i].h, unpriced[i].h);
  }
  // Both UP ticks are the same height whatever their price, and neither is
  // shorter than the DOWN tick.
  assert.equal(priced[1].h, priced[2].h);
  assert.ok(priced[1].h >= priced[0].h, "an up tick is never shorter than a down tick");
});

test("quoteTrendBars draws a flat tick on the midline, not a zero-height bar", () => {
  // value 0 is the script's third state (close == close[1]). It must still
  // be visible - a bar of height 0 would read as missing data.
  const bars = quoteTrendBars([{ value: 0 }, { value: 0, price: 50 }], 20, 20);
  assert.equal(bars.length, 2);
  for (const bar of bars) {
    assert.ok(Number.isFinite(bar.h) && bar.h > 0);
    // Centred on the midline rather than anchored to an edge.
    assert.equal(bar.y + bar.h / 2, 10);
  }
  // A flat tick is the SHORTEST of the three states.
  const up = quoteTrendBars([{ value: 1 }], 20, 20)[0];
  assert.ok(bars[0].h < up.h);
});

test("quoteTrendBars falls back to direction ticks when price is absent", () => {
  const cells = [{ value: 1 }, { value: -1 }, { value: 0 }];
  const bars = quoteTrendBars(cells, 30, 20);
  assert.equal(bars[0].y, 0);            // up bar from the top
  assert.equal(bars[1].y, 10);           // down bar from the midline
});

test("quoteTrendBars hands the cell back so colour comes from cellStyle only", () => {
  const cell = { value: 1, price: 42, bg: "dark_green", fg: "green" };
  const [bar] = quoteTrendBars([cell, { value: 1, price: 40 }], 10, 20);
  assert.equal(bar.cell, cell);
});

test("quoteTrendBars returns an empty array for empty input", () => {
  assert.deepEqual(quoteTrendBars([], 30, 20), []);
  assert.deepEqual(quoteTrendBars(null, 30, 20), []);
});

test("quoteTrendBars never emits NaN on a zero-sized box", () => {
  const bars = quoteTrendBars([{ value: 1 }, { value: -1 }], 0, 0);
  assert.equal(bars.length, 2);
  for (const bar of bars) {
    for (const key of ["x", "y", "w", "h"]) {
      assert.ok(Number.isFinite(bar[key]), `${key} must be finite, got ${bar[key]}`);
    }
  }
});

const ROWS = [
  { symbol: "NVDA", pctChange: 2.41 },
  { symbol: "AAPL", pctChange: -1.59 },
  { symbol: "MSFT", pctChange: null },
  { symbol: "TSLA", pctChange: 7.02 },
];

test("sortRows defaults to the TOS board order: pctChange descending", () => {
  assert.deepEqual(sortRows(ROWS).map((row) => row.symbol), ["TSLA", "NVDA", "AAPL", "MSFT"]);
  assert.deepEqual(MOMX_DEFAULT_SORT, { key: "pctChange", direction: "desc" });
});

test("sortRows sorts pctChange ascending on request", () => {
  assert.deepEqual(sortRows(ROWS, "pctChange", "asc").map((row) => row.symbol), ["AAPL", "NVDA", "TSLA", "MSFT"]);
});

test("sortRows puts null sort keys last in BOTH directions", () => {
  const rows = [
    { symbol: "A", pctChange: null },
    { symbol: "B", pctChange: 1 },
    { symbol: "C", pctChange: undefined },
    { symbol: "D", pctChange: Number.NaN },
    { symbol: "E", pctChange: -4 },
  ];
  assert.deepEqual(sortRows(rows, "pctChange", "desc").map((row) => row.symbol), ["B", "E", "A", "C", "D"]);
  assert.deepEqual(sortRows(rows, "pctChange", "asc").map((row) => row.symbol), ["E", "B", "A", "C", "D"]);
});

test("sortRows sorts by symbol", () => {
  assert.deepEqual(sortRows(ROWS, "symbol", "asc").map((row) => row.symbol), ["AAPL", "MSFT", "NVDA", "TSLA"]);
  assert.deepEqual(sortRows(ROWS, "symbol", "desc").map((row) => row.symbol), ["TSLA", "NVDA", "MSFT", "AAPL"]);
  const missing = [{ symbol: "ZM" }, { symbol: null }, { symbol: "AA" }];
  assert.deepEqual(sortRows(missing, "symbol", "asc").map((row) => row.symbol), ["AA", "ZM", null]);
});

test("sortRows returns a new array and never mutates the input", () => {
  const input = ROWS.map((row) => ({ ...row }));
  const snapshot = input.map((row) => row.symbol);
  const sorted = sortRows(input, "pctChange", "desc");
  assert.notEqual(sorted, input);
  assert.deepEqual(input.map((row) => row.symbol), snapshot);
  // Rows themselves are passed through by reference, not cloned.
  assert.equal(sorted[0], input.find((row) => row.symbol === "TSLA"));
});

test("sortRows breaks ties on symbol so the board order is stable", () => {
  const rows = [
    { symbol: "NVDA", pctChange: 1 },
    { symbol: "AMD", pctChange: 1 },
    { symbol: "INTC", pctChange: 1 },
  ];
  assert.deepEqual(sortRows(rows, "pctChange", "desc").map((row) => row.symbol), ["AMD", "INTC", "NVDA"]);
});

test("sortRows tolerates junk input", () => {
  assert.deepEqual(sortRows(null), []);
  assert.deepEqual(sortRows(undefined), []);
  assert.deepEqual(sortRows("rows"), []);
  assert.deepEqual(sortRows([{ symbol: "A", pctChange: 1 }], "unknownKey").map((row) => row.symbol), ["A"]);
});

// ---------------------------------------------------------------------------
// Industry chips
// ---------------------------------------------------------------------------

test("MOMX_INDUSTRY_PALETTE is a set of distinct 6-digit hex colours", () => {
  assert.ok(MOMX_INDUSTRY_PALETTE.length >= 8);
  for (const color of MOMX_INDUSTRY_PALETTE) {
    assert.match(color, /^#[0-9a-f]{6}$/);
  }
  assert.equal(new Set(MOMX_INDUSTRY_PALETTE).size, MOMX_INDUSTRY_PALETTE.length);
});

test("industry colours never leak into the thinkScript palette", () => {
  // A chip colour appearing on a value cell would look like a study output.
  const thinkscript = new Set(Object.values(THINKSCRIPT_COLORS));
  for (const color of MOMX_INDUSTRY_PALETTE) {
    assert.equal(thinkscript.has(color), false, `${color} collides with a thinkScript colour`);
  }
});

test("industryColor is deterministic and case/whitespace insensitive", () => {
  const first = industryColor("Semiconductors");
  assert.equal(industryColor("Semiconductors"), first);
  assert.equal(industryColor("  semiconductors  "), first);
  assert.equal(industryColor("SEMICONDUCTORS"), first);
  assert.ok(MOMX_INDUSTRY_PALETTE.includes(first));
});

test("industryColor spreads real industry names across the palette", () => {
  // A hash that bunches every name onto one or two entries would make the chip
  // row a single colour, which is the whole point of having a palette.
  const names = [
    "Semiconductors",
    "Software",
    "Apparel",
    "Banks",
    "Biotech",
    "Autos",
    "Energy",
    "Retail",
    "Media",
    "Airlines",
    "Insurance",
    "Utilities",
  ];
  const used = new Set(names.map(industryColor));
  assert.ok(used.size >= 5, `expected several distinct chip colours, got ${used.size}`);
});

test("industryColor never throws on junk and always returns a palette entry", () => {
  for (const junk of [null, undefined, 42, {}, [], "", "   "]) {
    const color = industryColor(junk);
    assert.ok(MOMX_INDUSTRY_PALETTE.includes(color));
  }
});

test("industryCounts counts each industry present, biggest first", () => {
  const rows = [
    { symbol: "NVDA", industry: "Semis" },
    { symbol: "AMD", industry: "Semis" },
    { symbol: "MSFT", industry: "Software" },
    { symbol: "AVGO", industry: "Semis" },
    { symbol: "NKE", industry: "Apparel" },
    { symbol: "ORCL", industry: "Software" },
  ];
  assert.deepEqual(
    industryCounts(rows).map((chip) => [chip.name, chip.count]),
    [["Semis", 3], ["Software", 2], ["Apparel", 1]],
  );
});

test("industryCounts breaks count ties alphabetically so chips do not reshuffle", () => {
  const rows = [
    { symbol: "A", industry: "Zinc" },
    { symbol: "B", industry: "Apparel" },
    { symbol: "C", industry: "Media" },
  ];
  assert.deepEqual(industryCounts(rows).map((chip) => chip.name), ["Apparel", "Media", "Zinc"]);
});

test("industryCounts carries the chip colour and skips unlabelled rows", () => {
  const rows = [
    { symbol: "NVDA", industry: "Semis" },
    { symbol: "XYZ", industry: "" },
    { symbol: "QRS", industry: "   " },
    { symbol: "ABC" },
    { symbol: "DEF", industry: null },
    null,
    "not a row",
  ];
  const chips = industryCounts(rows);
  assert.deepEqual(chips.map((chip) => chip.name), ["Semis"]);
  assert.equal(chips[0].color, industryColor("Semis"));
});

test("industryCounts tolerates junk input", () => {
  assert.deepEqual(industryCounts(null), []);
  assert.deepEqual(industryCounts(undefined), []);
  assert.deepEqual(industryCounts("rows"), []);
  assert.deepEqual(industryCounts([]), []);
});

test("filterByIndustries keeps every row when nothing is selected", () => {
  const rows = [{ symbol: "NVDA", industry: "Semis" }, { symbol: "MSFT", industry: "Software" }];
  assert.equal(filterByIndustries(rows, new Set()), rows);
  assert.equal(filterByIndustries(rows, []), rows);
  assert.equal(filterByIndustries(rows, null), rows);
});

test("filterByIndustries keeps rows in any selected industry", () => {
  const rows = [
    { symbol: "NVDA", industry: "Semis" },
    { symbol: "MSFT", industry: "Software" },
    { symbol: "NKE", industry: "Apparel" },
    { symbol: "XYZ", industry: "" },
  ];
  assert.deepEqual(
    filterByIndustries(rows, new Set(["Semis", "Apparel"])).map((row) => row.symbol),
    ["NVDA", "NKE"],
  );
  assert.deepEqual(filterByIndustries(rows, ["Software"]).map((row) => row.symbol), ["MSFT"]);
});

test("filterByIndustries tolerates junk rows", () => {
  assert.deepEqual(filterByIndustries(null, ["Semis"]), []);
  assert.deepEqual(filterByIndustries([null, "x", { symbol: "A" }], ["Semis"]), []);
});

test("badgeOf returns nothing until the backend ships a live badge", () => {
  // row.badge is being added by another agent RIGHT NOW: absent must be silent.
  assert.equal(badgeOf({ symbol: "NVDA" }), null);
  assert.equal(badgeOf({ symbol: "NVDA", badge: null }), null);
  assert.equal(badgeOf({ symbol: "NVDA", badge: {} }), null);
  assert.equal(badgeOf({ symbol: "NVDA", badge: { on: false, tooltip: "x" } }), null);
  assert.equal(badgeOf({ symbol: "NVDA", badge: "on" }), null);
  assert.equal(badgeOf(null), null);
  assert.equal(badgeOf("NVDA"), null);
});

test("badgeOf exposes the backend tooltip, with a readable fallback", () => {
  assert.deepEqual(badgeOf({ badge: { on: true, tooltip: "4x8 cross up on 2h" } }), {
    tooltip: "4x8 cross up on 2h",
  });
  assert.deepEqual(badgeOf({ badge: { on: true } }), { tooltip: "Momentum badge" });
  assert.deepEqual(badgeOf({ badge: { on: 1, tooltip: 5 } }), { tooltip: "Momentum badge" });
});

// ---------------------------------------------------------------------------
// Momentum strip helpers
// ---------------------------------------------------------------------------

const NOW = Date.parse("2026-08-28T14:30:00Z");
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

test("momentumEventTime reads ISO strings, epoch ms and epoch seconds", () => {
  assert.equal(momentumEventTime({ at: "2026-08-28T14:30:00Z" }), NOW);
  assert.equal(momentumEventTime({ at: NOW }), NOW);
  assert.equal(momentumEventTime({ at: Math.floor(NOW / 1000) }), Math.floor(NOW / 1000) * 1000);
  assert.equal(momentumEventTime({ at: "not a date" }), null);
  assert.equal(momentumEventTime({ at: null }), null);
  assert.equal(momentumEventTime({}), null);
  assert.equal(momentumEventTime(null), null);
});

test("momentumChipLabel formats a new_match as NEW symbol pct", () => {
  const label = momentumChipLabel({
    type: "new_match",
    symbol: "crwd",
    pctChange: 4.2,
    scanReasons: ["4x8 cross up", "RVOL 2h > 1.5"],
    at: iso(0),
  });
  assert.equal(label.kind, "new_match");
  assert.equal(label.tag, "NEW");
  assert.equal(label.symbol, "CRWD");
  assert.equal(label.detail, "+4.20%");
  assert.equal(label.text, "NEW CRWD +4.20%");
  assert.equal(label.title, "4x8 cross up  RVOL 2h > 1.5");
});

test("momentumChipLabel survives a new_match with no pctChange and no reasons", () => {
  const label = momentumChipLabel({ type: "new_match", symbol: "MPC", at: iso(0) });
  assert.equal(label.detail, "");
  assert.equal(label.text, "NEW MPC");
  assert.equal(label.title, "Newly passed the scan");
});

test("momentumChipLabel formats an rvol_spike measure-first", () => {
  const label = momentumChipLabel({
    type: "rvol_spike",
    symbol: "MPC",
    timeframe: "5m",
    value: 3.1,
    at: iso(0),
  });
  assert.equal(label.kind, "rvol_spike");
  assert.equal(label.tag, "RVOL 5m");
  assert.equal(label.detail, "3.1");
  assert.equal(label.text, "RVOL 5m 3.1 MPC");
});

test("momentumChipLabel rvol_spike tolerates a missing timeframe and value", () => {
  const label = momentumChipLabel({ type: "rvol_spike", symbol: "NVDA", at: iso(0) });
  assert.equal(label.tag, "RVOL");
  assert.equal(label.detail, "");
  assert.equal(label.text, "RVOL NVDA");
});

test("momentumChipLabel formats a lost_match", () => {
  const label = momentumChipLabel({ type: "lost_match", symbol: "XYZ", at: iso(0) });
  assert.equal(label.kind, "lost_match");
  assert.equal(label.text, "LOST XYZ");
  assert.equal(label.detail, "");
});

test("momentumChipLabel drops malformed events silently", () => {
  assert.equal(momentumChipLabel(null), null);
  assert.equal(momentumChipLabel("new_match"), null);
  assert.equal(momentumChipLabel({ type: "meteor_strike", symbol: "NVDA" }), null);
  assert.equal(momentumChipLabel({ type: "new_match" }), null);
  assert.equal(momentumChipLabel({ type: "new_match", symbol: "   " }), null);
  assert.equal(momentumChipLabel({ type: "new_match", symbol: 7 }), null);
});

test("momentumAgeBucket buckets by age with fade at 15 minutes", () => {
  const fresh = { type: "new_match", symbol: "A", at: iso(30 * 1000) };
  const recent = { type: "new_match", symbol: "A", at: iso(5 * 60 * 1000) };
  const faded = { type: "new_match", symbol: "A", at: iso(MOMX_MOMENTUM_FADE_MS) };
  assert.equal(momentumAgeBucket(fresh, NOW), "fresh");
  assert.equal(momentumAgeBucket(recent, NOW), "recent");
  assert.equal(momentumAgeBucket(faded, NOW), "faded");
  // The fresh window boundary itself still counts as fresh.
  assert.equal(momentumAgeBucket({ type: "new_match", symbol: "A", at: iso(MOMX_MOMENTUM_FRESH_MS) }, NOW), "fresh");
});

test("momentumAgeBucket never flashes what it cannot date, but tolerates clock skew", () => {
  assert.equal(momentumAgeBucket({ type: "new_match", symbol: "A" }, NOW), "faded");
  assert.equal(momentumAgeBucket({ type: "new_match", symbol: "A", at: "garbage" }, NOW), "faded");
  // A slightly-future stamp is backend/browser clock skew, not old news.
  assert.equal(momentumAgeBucket({ type: "new_match", symbol: "A", at: iso(-20 * 1000) }, NOW), "fresh");
});

test("capMomentumEvents sorts newest first and caps with an overflow count", () => {
  const events = [];
  for (let index = 0; index < 16; index += 1) {
    events.push({ type: "new_match", symbol: "S" + index, at: iso(index * 60 * 1000) });
  }
  // Feed them oldest-first to prove the sort, not the input order, wins.
  const { visible, overflow } = capMomentumEvents([...events].reverse());
  assert.equal(visible.length, MOMX_MOMENTUM_MAX_CHIPS);
  assert.equal(overflow, 16 - MOMX_MOMENTUM_MAX_CHIPS);
  assert.equal(visible[0].symbol, "S0");
  assert.equal(visible[1].symbol, "S1");
});

test("capMomentumEvents dedupes exact repeats and drops malformed events", () => {
  const spike = { type: "rvol_spike", symbol: "MPC", timeframe: "5m", value: 3.1, at: iso(1000) };
  const { visible, overflow } = capMomentumEvents([
    spike,
    { ...spike },
    null,
    { type: "wat", symbol: "NVDA", at: iso(0) },
    { type: "new_match", at: iso(0) },
    { type: "lost_match", symbol: "XYZ", at: iso(2000) },
  ]);
  assert.deepEqual(
    visible.map((event) => event.type + ":" + event.symbol),
    ["rvol_spike:MPC", "lost_match:XYZ"],
  );
  assert.equal(overflow, 0);
});

test("capMomentumEvents puts undated events last and survives junk input", () => {
  const { visible } = capMomentumEvents([
    { type: "new_match", symbol: "NODATE" },
    { type: "new_match", symbol: "DATED", at: iso(60 * 1000) },
  ]);
  assert.deepEqual(visible.map((event) => event.symbol), ["DATED", "NODATE"]);
  assert.deepEqual(capMomentumEvents(null), { visible: [], overflow: 0 });
  assert.deepEqual(capMomentumEvents("events"), { visible: [], overflow: 0 });
});

test("capMomentumEvents honours a custom limit and rejects a junk one", () => {
  const events = [
    { type: "new_match", symbol: "A", at: iso(0) },
    { type: "new_match", symbol: "B", at: iso(1000) },
    { type: "new_match", symbol: "C", at: iso(2000) },
  ];
  const capped = capMomentumEvents(events, 2);
  assert.deepEqual(capped.visible.map((event) => event.symbol), ["A", "B"]);
  assert.equal(capped.overflow, 1);
  assert.equal(capMomentumEvents(events, 0).visible.length, 3);
  assert.equal(capMomentumEvents(events, NaN).visible.length, 3);
});

test("newMatchSymbols keeps only fresh new_match symbols, uppercased", () => {
  const events = [
    { type: "new_match", symbol: "crwd", at: iso(30 * 1000) },
    { type: "new_match", symbol: "OLD", at: iso(MOMX_MOMENTUM_FRESH_MS + 1000) },
    { type: "rvol_spike", symbol: "MPC", at: iso(0) },
    { type: "lost_match", symbol: "XYZ", at: iso(0) },
    { type: "new_match", symbol: "NODATE" },
    { type: "new_match", symbol: "SKEW", at: iso(-10 * 1000) },
    null,
  ];
  const fresh = newMatchSymbols(events, NOW);
  assert.deepEqual([...fresh].sort(), ["CRWD", "SKEW"]);
});

test("newMatchSymbols survives junk input", () => {
  assert.equal(newMatchSymbols(null, NOW).size, 0);
  assert.equal(newMatchSymbols("events", NOW).size, 0);
  assert.equal(newMatchSymbols([], NOW).size, 0);
});

// ---------------------------------------------------------------------------
// Board persistence + the show-cache-vs-building decision
// ---------------------------------------------------------------------------

// A minimal localStorage double. `failWrites` simulates a full quota (setItem
// throws QuotaExceededError); `failAccess` simulates the private-mode case where
// even reading a value throws. Only getItem/setItem are needed by the module.
function makeStorage(options = {}) {
  const map = new Map();
  return {
    map,
    getItem(key) {
      if (options.failAccess) throw new Error("SecurityError: storage disabled");
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      if (options.failWrites) throw new Error("QuotaExceededError");
      map.set(key, String(value));
    },
  };
}

const BOARD = {
  generatedAt: "2026-08-28T13:31:00Z",
  universeCount: 355,
  warming: false,
  rows: [
    { symbol: "NVDA", scanPass: true, pctChange: 2.4 },
    { symbol: "TSLA", scanPass: false, pctChange: -1.1 },
  ],
};

test("momxBoardCacheKey namespaces per list and folds empty names to a default", () => {
  assert.equal(momxBoardCacheKey("Mag7"), MOMX_BOARD_CACHE_PREFIX + "Mag7");
  assert.equal(momxBoardCacheKey("  Keepers  "), MOMX_BOARD_CACHE_PREFIX + "Keepers");
  assert.equal(momxBoardCacheKey(null), MOMX_BOARD_CACHE_PREFIX + "__default__");
  assert.equal(momxBoardCacheKey(""), MOMX_BOARD_CACHE_PREFIX + "__default__");
  assert.equal(momxBoardCacheKey(42), MOMX_BOARD_CACHE_PREFIX + "__default__");
});

test("boardCacheEntry slims a board and refuses an empty one", () => {
  const entry = boardCacheEntry(BOARD);
  assert.deepEqual(entry.rows, BOARD.rows);
  assert.equal(entry.generatedAt, BOARD.generatedAt);
  assert.equal(entry.universeCount, 355);
  // No rows -> nothing worth caching (and must never overwrite good rows).
  assert.equal(boardCacheEntry({ rows: [], warming: true }), null);
  assert.equal(boardCacheEntry({ warming: true }), null);
  assert.equal(boardCacheEntry(null), null);
  assert.equal(boardCacheEntry("nope"), null);
});

test("boardCacheEntry derives a universe count when the field is absent", () => {
  assert.equal(boardCacheEntry({ rows: [{ symbol: "A" }, { symbol: "B" }] }).universeCount, 2);
  assert.equal(
    boardCacheEntry({ rows: [{ symbol: "A" }], universe: ["A", "B", "C"] }).universeCount,
    3,
  );
});

test("boardCacheEntry caps the stored row count to guard the quota", () => {
  const rows = [];
  for (let index = 0; index < MOMX_BOARD_CACHE_MAX_ROWS + 50; index += 1) {
    rows.push({ symbol: "S" + index });
  }
  const entry = boardCacheEntry({ rows });
  assert.equal(entry.rows.length, MOMX_BOARD_CACHE_MAX_ROWS);
});

test("board cache round-trips through storage, per list", () => {
  const storage = makeStorage();
  assert.equal(writeBoardCache("Keepers", BOARD, storage), true);
  const back = readBoardCache("Keepers", storage);
  assert.deepEqual(back.rows, BOARD.rows);
  assert.equal(back.generatedAt, BOARD.generatedAt);
  assert.equal(back.universeCount, 355);
  // A different list is a different bucket, not this one.
  assert.equal(readBoardCache("Mag7", storage), null);
});

// THE ACTUAL RELOAD HOP. Every other round-trip test here goes
// boardCacheEntry -> decideBoardView and skips readBoardCache, which is why a
// read side that silently dropped `rest` and `tapeAsOf` stayed green while the
// reloaded board fell back to 50 rows (2026-09-02).
test("a reload keeps the rest rows and the tape stamp, not just the top rows", () => {
  const storage = makeStorage();
  const board = {
    ...BOARD,
    tapeAsOf: "2026-08-28T13:15:00Z",
    rest: [
      { symbol: "AMD", scanPass: false, pctChange: 0.4, rvol: { "5m": 1.2 } },
      { symbol: "INTC", scanPass: false, pctChange: -0.2 },
      { symbol: "", scanPass: false },
      null,
    ],
  };
  assert.equal(writeBoardCache("Keepers", board, storage), true);
  const back = readBoardCache("Keepers", storage);
  assert.equal(back.tapeAsOf, "2026-08-28T13:15:00Z");
  assert.deepEqual(back.rest.map((row) => row.symbol), ["AMD", "INTC"]);
  // ...and the view built from that cache shows them, so an unticked
  // "Scan matches only" is not back to the top rows alone after a reload.
  const view = decideBoardView({ liveBoard: { rows: [], warming: true }, cache: back, warming: true });
  assert.equal(view.rows.length, 2);
  assert.equal(view.rest.length, 2);
  assert.equal(view.tapeAsOf, "2026-08-28T13:15:00Z");
});

test("writeBoardCache never stores an empty board, so warming cannot wipe rows", () => {
  const storage = makeStorage();
  writeBoardCache("Keepers", BOARD, storage);
  // A later warming/empty answer must be a no-op, leaving the good rows intact.
  assert.equal(writeBoardCache("Keepers", { rows: [], warming: true }, storage), false);
  assert.deepEqual(readBoardCache("Keepers", storage).rows, BOARD.rows);
});

test("writeBoardCache degrades silently when the quota is exhausted", () => {
  const storage = makeStorage({ failWrites: true });
  assert.doesNotThrow(() => {
    assert.equal(writeBoardCache("Keepers", BOARD, storage), false);
  });
});

test("readBoardCache degrades to no-cache on unparseable or wrong-shaped blobs", () => {
  const storage = makeStorage();
  storage.map.set(momxBoardCacheKey("Keepers"), "{not json");
  assert.equal(readBoardCache("Keepers", storage), null);
  storage.map.set(momxBoardCacheKey("Keepers"), JSON.stringify({ rows: "nope" }));
  assert.equal(readBoardCache("Keepers", storage), null);
  storage.map.set(momxBoardCacheKey("Keepers"), JSON.stringify({ rows: [] }));
  assert.equal(readBoardCache("Keepers", storage), null);
  storage.map.set(momxBoardCacheKey("Keepers"), JSON.stringify(42));
  assert.equal(readBoardCache("Keepers", storage), null);
});

test("readBoardCache degrades to no-cache when storage access itself throws", () => {
  const storage = makeStorage({ failAccess: true });
  assert.doesNotThrow(() => {
    assert.equal(readBoardCache("Keepers", storage), null);
  });
});

test("read/write degrade to null/false when there is no storage at all", () => {
  // No window in this runtime and no double passed: must not throw.
  assert.equal(readBoardCache("Keepers"), null);
  assert.equal(writeBoardCache("Keepers", BOARD), false);
  assert.equal(readBoardCache("Keepers", {}), null);
});

test("decideBoardView shows the live board when the fetch returned rows", () => {
  const cache = boardCacheEntry(BOARD);
  const live = {
    generatedAt: "2026-08-28T14:00:00Z",
    universeCount: 355,
    rows: [{ symbol: "AMD", scanPass: true }],
  };
  const view = decideBoardView({ liveBoard: live, cache, warming: false });
  assert.equal(view.source, "live");
  assert.equal(view.rows, live.rows);
  assert.equal(view.generatedAt, "2026-08-28T14:00:00Z");
  assert.equal(view.universeCount, 355);
  assert.equal(view.updating, false);
});

test("decideBoardView keeps live rows but flags updating while still warming", () => {
  const live = { generatedAt: "t", universeCount: 355, warming: true, rows: [{ symbol: "AMD" }] };
  const view = decideBoardView({ liveBoard: live, cache: null, warming: true });
  assert.equal(view.source, "live");
  assert.equal(view.updating, true);
});

test("decideBoardView shows the CACHE, not Building, when the server is warming", () => {
  // The exact worker-restart case: server answers warming with no rows, but a
  // good board is cached -> the trader keeps seeing tickers, flagged updating.
  const cache = boardCacheEntry(BOARD);
  const warmingLive = { warming: true, universeCount: 355, rows: [] };
  const view = decideBoardView({ liveBoard: warmingLive, cache, warming: true });
  assert.equal(view.source, "cache");
  assert.deepEqual(view.rows, BOARD.rows);
  assert.equal(view.generatedAt, BOARD.generatedAt);
  assert.equal(view.universeCount, 355);
  assert.equal(view.updating, true);
});

test("decideBoardView falls back to cache even when there is no live board yet", () => {
  // First paint on a fresh mount / page reload: fetch not resolved, cache read
  // from localStorage -> rows appear within the frame, marked updating.
  const view = decideBoardView({ liveBoard: null, cache: boardCacheEntry(BOARD) });
  assert.equal(view.source, "cache");
  assert.deepEqual(view.rows, BOARD.rows);
  assert.equal(view.updating, true);
});

test("decideBoardView is empty ONLY when nothing exists anywhere", () => {
  const view = decideBoardView({
    liveBoard: { warming: true, universeCount: 355, rows: [] },
    cache: null,
    warming: true,
  });
  assert.equal(view.source, "empty");
  assert.deepEqual(view.rows, []);
  assert.equal(view.updating, true);
  // Genuine first-ever run: keep the warming board's universe figure visible.
  assert.equal(view.universeCount, 355);
});

test("decideBoardView tolerates being called with nothing", () => {
  const view = decideBoardView();
  assert.equal(view.source, "empty");
  assert.deepEqual(view.rows, []);
  assert.equal(view.updating, false);
  assert.equal(view.universeCount, 0);
  assert.equal(view.generatedAt, null);
});

test("cellStyle keeps a readable script colour but rescues an invisible one", () => {
  // Readable script pairings are preserved (the fg carries signal meaning).
  assert.equal(cellStyle({ bg: "plum", fg: "black" }).color, "#000000");
  assert.equal(cellStyle({ bg: "cyan", fg: "black" }).color, "#000000");
  // Skittles paints violet on plum (contrast 1.12) - invisible. Rescued to the
  // colour that ACTUALLY contrasts on light plum: black (10:1), not white (2:1).
  assert.equal(cellStyle({ bg: "plum", fg: "violet" }).color, "#000000");
  assert.equal(cellStyle({ bg: "magenta", fg: "violet" }).color, "#000000");
  // NOT rescued (0b2d901). His words: "I DONT SEE WHITE RVOL" - and TOS has
  // none. The bottom rungs of his RVOL ladder are dark ON PURPOSE, so the eye
  // lands only on the timeframes that are moving. dark_green on black is
  // contrast 2.82 and dark_red 2.10; both sit above the 1.6 floor and keep
  // the script's own ink. Rescuing them to white undid the whole device.
  assert.equal(cellStyle({ bg: "black", fg: "dark_green" }).color, THINKSCRIPT_COLORS.dark_green);
  assert.equal(cellStyle({ bg: "black", fg: "dark_red" }).color, THINKSCRIPT_COLORS.dark_red);
  assert.equal(cellStyle({ bg: "dark_green", fg: "black" }).color, "#000000");
  // A transparent background leaves the text untouched.
  assert.equal(cellStyle({ bg: null, fg: "green" }).color, THINKSCRIPT_COLORS.green);
});

// Three tones, three meanings. The weekend case used to assert stale===true,
// which is what put an amber "DATA AS OF FRI 8:00 PM ET" chip on a healthy
// Saturday board and had the trader asking whether the scanner had died
// (2026-09-05). A shut market is not a fault; a tape that has fallen behind
// the market IS, and both must still be distinguishable at a glance.
test("tapeAsOfNote separates a closed market from a stalled tape", async () => {
  const { tapeAsOfNote } = await import("./MomxScannerPanel.jsx");

  // Sunday 03:00 ET holding Friday's 19:55 close - completely normal.
  const sunday = Date.parse("2026-08-30T07:00:00Z");
  const closed = tapeAsOfNote("2026-08-28T23:55:00+00:00", sunday);
  assert.equal(closed.tone, "closed");
  assert.equal(closed.stale, false, "a shut market must not read as a fault");
  assert.ok(closed.text.startsWith("Last bar Fri"), closed.text);

  // Wednesday 11:00 ET, tape three minutes old - the quiet in-session note,
  // which still reports the age so he can always see when the data ran.
  const midSession = Date.parse("2026-09-02T15:00:00Z");
  const live = tapeAsOfNote(new Date(midSession - 3 * 60 * 1000).toISOString(), midSession);
  assert.equal(live.tone, "live");
  assert.equal(live.stale, false);
  assert.ok(/^Data \d{2}:\d{2} ET \(3m\)$/.test(live.text), live.text);

  // Same session, an hour behind. THIS is what the loud chip exists for.
  const stalled = tapeAsOfNote(new Date(midSession - 60 * 60 * 1000).toISOString(), midSession);
  assert.equal(stalled.tone, "stale");
  assert.equal(stalled.stale, true);
  assert.ok(stalled.text.startsWith("Tape stale - last bar"), stalled.text);

  // A tape a whole session behind is stale even though the market is shut.
  const tueNight = Date.parse("2026-09-02T02:00:00Z"); // Tue 22:00 ET
  const behind = tapeAsOfNote("2026-08-31T20:00:00-04:00", tueNight);
  assert.equal(behind.tone, "stale", "a session-old tape is stale at any hour");

  // Unknown -> no note rather than a lie
  assert.equal(tapeAsOfNote(null, sunday), null);
  assert.equal(tapeAsOfNote("garbage", sunday), null);
});

test("tapeAsOf survives the view-builder and the localStorage cache round trip", async () => {
  // The market-closed note reads boardView.tapeAsOf. The API carried it but
  // decideBoardView's field whitelist dropped it, so the note NEVER rendered
  // (found live on a Sunday, 2026-08-30). Pin every hop.
  const { decideBoardView, boardCacheEntry } = await import("./momxCells.js");
  const board = { rows: [{ symbol: "BUD" }], generatedAt: "2026-08-30T17:00:00Z", tapeAsOf: "2026-08-28T23:55:00+00:00" };
  assert.equal(decideBoardView({ liveBoard: board }).tapeAsOf, board.tapeAsOf);
  const entry = boardCacheEntry(board);
  assert.equal(entry.tapeAsOf, board.tapeAsOf);
  assert.equal(decideBoardView({ cache: entry }).tapeAsOf, board.tapeAsOf);
});

// ---------------------------------------------------------------------------
// matchedSinceLabel: the row's "entered the matched set" stamp
// ---------------------------------------------------------------------------

test("matchedSinceLabel: a fresh stamp is NEW, an older same-day stamp is a bare ET time", () => {
  // Monday 2026-08-31 10:00 ET (EDT, UTC-4).
  const now = Date.parse("2026-08-31T14:00:00Z");

  // Entered 5 minutes ago -> NEW (and the label is still the entry time).
  const fresh = matchedSinceLabel("2026-08-31T13:55:00Z", now);
  assert.equal(fresh.isNew, true);
  assert.equal(fresh.label, "9:55");

  // Entered 20 minutes ago -> not NEW; label is the ET clock time with NO
  // AM/PM (dense board, trading-day times are unambiguous).
  const older = matchedSinceLabel("2026-08-31T13:40:00Z", now);
  assert.equal(older.isNew, false);
  assert.equal(older.label, "9:40");

  // Exactly at the window edge -> no longer NEW (strict <).
  const edge = matchedSinceLabel(new Date(now - MOMX_MATCHED_NEW_MS).toISOString(), now);
  assert.equal(edge.isNew, false);
});

test("matchedSinceLabel: a previous ET day becomes a short weekday", () => {
  const now = Date.parse("2026-08-31T14:00:00Z"); // Monday 10:00 ET
  // Friday's stamp (BUD/DHI keeping their Friday entries across the weekend).
  const friday = matchedSinceLabel("2026-08-28T19:47:00Z", now);
  assert.equal(friday.isNew, false);
  assert.equal(friday.label, "Fri");
  // The day is compared in ET, not UTC: 02:00Z Monday is still Sunday evening
  // in New York, so it must NOT render as a same-day clock time.
  const sundayEvening = matchedSinceLabel("2026-08-31T02:00:00Z", now);
  assert.equal(sundayEvening.label, "Sun");
});

test("matchedSinceLabel: absent/garbage stamps yield null, future stamps clamp to NEW", () => {
  const now = Date.parse("2026-08-31T14:00:00Z");
  // Old cached boards have no matchedSince at all - the row renders as before.
  assert.equal(matchedSinceLabel(null, now), null);
  assert.equal(matchedSinceLabel(undefined, now), null);
  assert.equal(matchedSinceLabel("", now), null);
  assert.equal(matchedSinceLabel("garbage", now), null);
  assert.equal(matchedSinceLabel(12345, now), null);
  // Backend clock a few minutes ahead of the browser: the entry is still NEW,
  // never negative-aged into "old".
  const future = matchedSinceLabel(new Date(now + 5 * 60 * 1000).toISOString(), now);
  assert.equal(future.isNew, true);
});

// ---------------------------------------------------------------------------
// News badge: ageOf + newsOf
// ---------------------------------------------------------------------------
//
// newsOf IS the badge decision: MomxScannerPanel renders the newspaper icon
// exactly when newsOf(row) !== null, so these tests pin the render rule
// without needing JSX.

const NEWS_NOW = Date.parse("2026-08-30T15:00:00Z");
const newsAt = (msAgo) => new Date(NEWS_NOW - msAgo).toISOString();

test("ageOf: under a minute reads as just now", () => {
  assert.equal(ageOf(newsAt(30 * 1000), NEWS_NOW), "just now");
});

test("ageOf: minutes under an hour", () => {
  assert.equal(ageOf(newsAt(5 * 60 * 1000), NEWS_NOW), "5m ago");
  assert.equal(ageOf(newsAt(59 * 60 * 1000), NEWS_NOW), "59m ago");
});

test("ageOf: hours under a day, floored", () => {
  assert.equal(ageOf(newsAt(60 * 60 * 1000), NEWS_NOW), "1h ago");
  assert.equal(ageOf(newsAt(3 * 60 * 60 * 1000 + 20 * 60 * 1000), NEWS_NOW), "3h ago");
  assert.equal(ageOf(newsAt(23 * 60 * 60 * 1000 + 59 * 60 * 1000), NEWS_NOW), "23h ago");
});

test("ageOf: a day and beyond reads in days", () => {
  assert.equal(ageOf(newsAt(26 * 60 * 60 * 1000), NEWS_NOW), "1d ago");
  assert.equal(ageOf(newsAt(50 * 60 * 60 * 1000), NEWS_NOW), "2d ago");
});

test("ageOf: unreadable input renders empty, never NaN", () => {
  assert.equal(ageOf(undefined, NEWS_NOW), "");
  assert.equal(ageOf("", NEWS_NOW), "");
  assert.equal(ageOf("not-a-date", NEWS_NOW), "");
});

test("ageOf: clock-skew future timestamps clamp to just now", () => {
  assert.equal(ageOf(newsAt(-90 * 1000), NEWS_NOW), "just now");
});

test("newsOf: rows without news get no badge", () => {
  // The board today: most rows carry no news field at all, and a failed
  // best-effort fetch ships exactly this shape. All must stay badge-free.
  assert.equal(newsOf({ symbol: "NVDA" }, NEWS_NOW), null);
  assert.equal(newsOf({ symbol: "NVDA", news: null }, NEWS_NOW), null);
  assert.equal(newsOf({ symbol: "NVDA", news: "AI up" }, NEWS_NOW), null);
  assert.equal(newsOf(null, NEWS_NOW), null);
  assert.equal(newsOf({ symbol: "NVDA", news: { headline: "  " } }, NEWS_NOW), null);
});

test("newsOf: a fresh headline gets the badge with a display-ready age", () => {
  const row = {
    symbol: "NVDA",
    news: {
      headline: " Nvidia guides above the street ",
      source: "Reuters",
      url: "https://example.com/nvda",
      publishedAt: newsAt(3 * 60 * 60 * 1000),
    },
  };
  const news = newsOf(row, NEWS_NOW);
  assert.equal(news.headline, "Nvidia guides above the street");
  assert.equal(news.age, "3h ago");
  assert.equal(news.source, "Reuters");
  assert.equal(news.url, "https://example.com/nvda");
});

test("newsOf: enforces the 24h window when the timestamp is readable", () => {
  const stale = {
    news: { headline: "old story", publishedAt: newsAt(MOMX_NEWS_FRESH_MS + 60 * 1000) },
  };
  assert.equal(newsOf(stale, NEWS_NOW), null);
  const edge = {
    news: { headline: "still fresh", publishedAt: newsAt(MOMX_NEWS_FRESH_MS - 60 * 1000) },
  };
  assert.notEqual(newsOf(edge, NEWS_NOW), null);
});

test("newsOf: trusts the worker's window when the timestamp is missing", () => {
  const news = newsOf({ news: { headline: "untimed but server-filtered" } }, NEWS_NOW);
  assert.notEqual(news, null);
  assert.equal(news.age, "");
});

test("newsOf: accepts `at` as the time field and drops non-http urls", () => {
  const row = {
    news: {
      headline: "wire story",
      at: newsAt(10 * 60 * 1000),
      url: "javascript:alert(1)",
    },
  };
  const news = newsOf(row, NEWS_NOW);
  assert.equal(news.age, "10m ago");
  assert.equal(news.url, null);
});

// ---------------------------------------------------------------------------
// the NEWS view's Time column (2026-09-02: "I don't know when the news came")
// ---------------------------------------------------------------------------

test("newsOf: carries the headline instant so the NEWS view can print and sort on it", () => {
  const at = newsAt(3 * 60 * 60 * 1000);
  const news = newsOf({ news: { headline: "x", publishedAt: at } }, NEWS_NOW);
  assert.equal(news.atMs, Date.parse(at));
  const untimed = newsOf({ news: { headline: "x" } }, NEWS_NOW);
  assert.equal(untimed.atMs, null, "no readable stamp -> badge still shows, but there is no time to print");
});

test("newsTimeLabel: same ET day prints the 24h clock and a compact age", () => {
  // NEWS_NOW is 11:00 ET; three hours earlier is 08:00 ET the same day.
  const news = newsOf({ news: { headline: "x", publishedAt: newsAt(3 * 60 * 60 * 1000) } }, NEWS_NOW);
  assert.deepEqual(newsTimeLabel(news, NEWS_NOW), { label: "08:00", age: "3h" });
});

test("newsTimeLabel: a headline from the previous ET day carries the weekday", () => {
  // 20 hours before 11:00 ET Sunday is 15:00 ET Saturday - still inside the
  // 24h window, so it MUST say which day, or "15:00" reads as this afternoon.
  const news = newsOf({ news: { headline: "x", publishedAt: newsAt(20 * 60 * 60 * 1000) } }, NEWS_NOW);
  assert.deepEqual(newsTimeLabel(news, NEWS_NOW), { label: "Sat 15:00", age: "20h" });
});

test("newsTimeLabel: nothing to print without a stamp, and never throws on junk", () => {
  assert.equal(newsTimeLabel(newsOf({ news: { headline: "x" } }, NEWS_NOW), NEWS_NOW), null);
  assert.equal(newsTimeLabel(null, NEWS_NOW), null);
  assert.equal(newsTimeLabel({ atMs: "soon" }, NEWS_NOW), null);
  assert.equal(newsTimeLabel(newsOf({ news: { headline: "x", at: newsAt(20 * 1000) } }, NEWS_NOW), NEWS_NOW).age, "now");
});

// ---------------------------------------------------------------------------
// the rest of the list (2026-09-02)
// ---------------------------------------------------------------------------

test("decideBoardView carries the rest of the list, live and cached", () => {
  const rest = [{ symbol: "ZETA", industry: "Software", pctChange: 1.7, scanPass: false }];
  const live = decideBoardView({ liveBoard: { rows: [{ symbol: "AAPL" }], rest }, warming: false });
  assert.deepEqual(live.rest, rest);

  const cached = decideBoardView({ liveBoard: null, cache: { rows: [{ symbol: "AAPL" }], rest } });
  assert.deepEqual(cached.rest, rest);

  // absent or malformed -> [] and never a crash; a bare {} is dropped
  assert.deepEqual(decideBoardView({ liveBoard: { rows: [{ symbol: "AAPL" }] } }).rest, []);
  assert.deepEqual(decideBoardView({ liveBoard: { rows: [{ symbol: "AAPL" }], rest: [{}, null, "x"] } }).rest, []);
  assert.deepEqual(decideBoardView({}).rest, []);
});

test("the board cache keeps the rest so a reload does not go back to 50 rows", () => {
  const rest = [{ symbol: "ZETA", pctChange: 1.7, scanPass: false }];
  const entry = boardCacheEntry({ rows: [{ symbol: "AAPL" }], generatedAt: "x", rest });
  assert.deepEqual(entry.rest, rest);
});

// ---------------------------------------------------------------------------
// Fires - what the lightning bolt means (2026-09-02)
//
// It used to mean "has weekly options". He asked for it to mean what it means
// on the card he works from: the signals that fired. The data was already
// there in scanReasons; these pin the translation into his vocabulary.
// ---------------------------------------------------------------------------

test("fireLabel speaks his vocabulary, not the wire's", () => {
  assert.equal(fireLabel("ema9x20:4h"), "9x20 4h");
  assert.equal(fireLabel("ema4x8:2D"), "4x8 2D");
  assert.equal(fireLabel("macd:Mo"), "MACD Mo");
  assert.equal(fireLabel("sqzfired:D"), "SQZ D");
});

test("an unknown study still shows, rather than vanishing", () => {
  // A study added on the server must not disappear from the board because
  // this map was not updated in the same commit.
  assert.equal(fireLabel("supertrend:1h"), "SUPERTREND 1h");
  assert.equal(fireLabel("newthing"), "NEWTHING");
});

test("firesOf reads scanReasons in order and de-duplicates", () => {
  const row = { scanReasons: ["macd:4h", "ema9x20:4h", "macd:4h", "sqzfired:D"] };
  assert.deepEqual(firesOf(row), ["MACD 4h", "9x20 4h", "SQZ D"]);
});

test("firesOf is total", () => {
  assert.deepEqual(firesOf(null), []);
  assert.deepEqual(firesOf({}), []);
  assert.deepEqual(firesOf({ scanReasons: "macd:4h" }), []);
  assert.deepEqual(firesOf({ scanReasons: [null, "", "macd:4h"] }), ["MACD 4h"]);
});
// ---------------------------------------------------------------------------
// cellAgeLabel - WHEN the bar behind an RVOL reading opened
//
// FDX's 2h read a cyan 3.1 that had arrived at 16:55 and was still 3.1 at
// 18:44. HPE's 4h read a cyan 3.1 that was the morning's volume while every
// fast column was negative. The number cannot say which; the bar's open time
// can.
// ---------------------------------------------------------------------------

// 2026-09-03 15:00:00 -04:00 -- FDX's 2h bucket that afternoon.
const FDX_2H_BAR = 1788462000;

test("cellAgeLabel says how long ago the volume arrived, not what time it was", () => {
  // He read a clock time twice and was confused both times - once as morning
  // vs afternoon, once as a possible duration ("16 hrs?"). The question was
  // always "is this new", so the label answers that directly.
  const at = (mins) => (FDX_2H_BAR + mins * 60) * 1000;
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(12)), "12m");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(45)), "45m");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(60)), "1h");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(90)), "1h30");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(180)), "3h");
});

test("cellAgeLabel is empty when an age would be meaningless", () => {
  const now = (FDX_2H_BAR + 600) * 1000;
  assert.equal(cellAgeLabel({}, now), "");
  assert.equal(cellAgeLabel({ barAt: 0 }, now), "");
  assert.equal(cellAgeLabel({ barAt: "soon" }, now), "");
  assert.equal(cellAgeLabel(null, now), "");
  // a stamp in the future is clock skew, not a negative age
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR + 600 }, FDX_2H_BAR * 1000), "");
  // multi-day ages are NOT empty - the Skittles block runs out to 4D/Wk/M
});

test("cellAgeLabel counts in days once past 24 hours", () => {
  // The Skittles block runs to 4D, Wk and M, and he asked for those by name.
  // A 24-hour cap used to live here and would have blanked exactly those.
  const at = (h) => (FDX_2H_BAR + h * 3600) * 1000;
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(24)), "1d");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(44)), "1d20");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(92)), "3d20");
  assert.equal(cellAgeLabel({ barAt: FDX_2H_BAR }, at(23)), "23h");
});

test("hasPaintedBackground reads the script's own \"this matters\"", () => {
  // Both ladders end in BLACK, which is the script saying nothing here.
  for (const bg of ["cyan", "green", "magenta", "red", "plum", "dark_green"]) {
    assert.equal(hasPaintedBackground({ bg }), true, bg);
  }
  assert.equal(hasPaintedBackground({ bg: "black" }), false);
  assert.equal(hasPaintedBackground({ bg: " BLACK " }), false);
  assert.equal(hasPaintedBackground({ bg: null }), false);
  assert.equal(hasPaintedBackground({}), false);
  assert.equal(hasPaintedBackground(null), false);
});

test("cellAgeClock keeps the exact time for the tooltip, in 24-hour", () => {
  // 24-hour on purpose: "16:00" cannot be misread as 4am, and it matches the
  // Time column on the same row.
  assert.equal(cellAgeClock({ barAt: FDX_2H_BAR }), "15:00");
  assert.equal(cellAgeClock({}), "");
  assert.equal(cellAgeClock(null), "");
});

test("cellAgeIsFresh answers 'is this the latest volume' for him", () => {
  const now = (FDX_2H_BAR + 3 * 3600) * 1000;                 // 3h after the bar
  // FDX's real case: volume landed at 4pm, read at 7pm -> NOT fresh.
  assert.equal(cellAgeIsFresh({ barAt: FDX_2H_BAR }, now), false);
  // Same cell read five minutes after the volume arrived -> fresh.
  assert.equal(cellAgeIsFresh({ barAt: FDX_2H_BAR }, (FDX_2H_BAR + 300) * 1000), true);
});

test("the freshness window is the app's existing definition of new", () => {
  const base = FDX_2H_BAR * 1000;
  const justInside = base + MOMX_MATCHED_NEW_MS - 1000;
  const justOutside = base + MOMX_MATCHED_NEW_MS + 1000;
  assert.equal(cellAgeIsFresh({ barAt: FDX_2H_BAR }, justInside), true);
  assert.equal(cellAgeIsFresh({ barAt: FDX_2H_BAR }, justOutside), false);
});

test("a future stamp is clock skew, not freshness", () => {
  const now = FDX_2H_BAR * 1000;
  assert.equal(cellAgeIsFresh({ barAt: FDX_2H_BAR + 600 }, now), false);
});

test("cellAgeIsFresh is total", () => {
  const now = FDX_2H_BAR * 1000;
  assert.equal(cellAgeIsFresh(null, now), false);
  assert.equal(cellAgeIsFresh("nope", now), false);
  assert.equal(cellAgeIsFresh({}, now), false);
  assert.equal(cellAgeIsFresh({ barAt: 0 }, now), false);
  assert.equal(cellAgeIsFresh({ barAt: "soon" }, now), false);
});
// ---------------------------------------------------------------------------
// cellIsLiveSpike - painted by his script AND the volume just arrived
//
// His ladder gives a cell a background only at relVol >= 2:
//     bullish  >=3 CYAN     >=2 GREEN   else BLACK
//     bearish  >=3 MAGENTA  >=2 RED     else BLACK
// so "has a background" already IS the threshold. This reads the colour the
// script assigned rather than re-deriving the number.
// ---------------------------------------------------------------------------

const JUST_NOW = (FDX_2H_BAR + 60) * 1000;
const LATER = (FDX_2H_BAR + 3 * 3600) * 1000;

test("every colour his ladder paints counts, not just cyan", () => {
  for (const bg of ["cyan", "green", "magenta", "red"]) {
    assert.equal(cellIsLiveSpike({ value: 3.4, bg, barAt: FDX_2H_BAR }, JUST_NOW), true, bg);
  }
});

test("a black cell never blinks - black IS the script saying nothing here", () => {
  assert.equal(cellIsLiveSpike({ value: 1.9, bg: "black", barAt: FDX_2H_BAR }, JUST_NOW), false);
  assert.equal(cellIsLiveSpike({ value: 1.9, bg: null, barAt: FDX_2H_BAR }, JUST_NOW), false);
  assert.equal(cellIsLiveSpike({ value: 1.9, barAt: FDX_2H_BAR }, JUST_NOW), false);
});

test("painted but OLD does not blink - the FDX case", () => {
  // FDX's cyan 2h 3.1: real, and the volume landed three hours earlier.
  assert.equal(cellIsLiveSpike({ value: 3.1, bg: "cyan", barAt: FDX_2H_BAR }, LATER), false);
});

test("casing and padding from the wire do not break it", () => {
  assert.equal(cellIsLiveSpike({ value: 3.4, bg: " CYAN ", barAt: FDX_2H_BAR }, JUST_NOW), true);
  assert.equal(cellIsLiveSpike({ value: 1.0, bg: " BLACK ", barAt: FDX_2H_BAR }, JUST_NOW), false);
});

test("a fresh Skittles bar counts as live, same rule as RVOL", () => {
  // Skittles has no volume - barAt is the BAR's start, so "fresh" means the
  // bar (and therefore the cross that coloured it) opened inside 15 minutes.
  const justOpened = (FDX_2H_BAR + 5 * 60) * 1000;
  assert.equal(cellIsLiveSpike({ value: 69, bg: "cyan", barAt: FDX_2H_BAR }, justOpened), true);
  // ...and an hour into the bar it is not.
  const later = (FDX_2H_BAR + 3600) * 1000;
  assert.equal(cellIsLiveSpike({ value: 69, bg: "cyan", barAt: FDX_2H_BAR }, later), false);
  // a black Skittles cell has no cross to be fresh about
  assert.equal(cellIsLiveSpike({ value: 69, bg: "black", barAt: FDX_2H_BAR }, justOpened), false);
});

test("cellIsLiveSpike is total", () => {
  assert.equal(cellIsLiveSpike(null, JUST_NOW), false);
  assert.equal(cellIsLiveSpike("nope", JUST_NOW), false);
  assert.equal(cellIsLiveSpike({}, JUST_NOW), false);
  assert.equal(cellIsLiveSpike({ bg: "cyan" }, JUST_NOW), false);        // no stamp
  assert.equal(cellIsLiveSpike({ bg: "cyan", barAt: 0 }, JUST_NOW), false);
});
test("rvolSaturationNote says what a capped score cannot", () => {
  const note = rvolSaturationNote({ value: 7.0, xAvg: 30.8 });
  assert.match(note, /31/);                 // rounded above 10x
  assert.match(note, /capped at 7\.0/);
});

test("rvolSaturationNote keeps a decimal below 10x", () => {
  assert.match(rvolSaturationNote({ value: 7.0, xAvg: 8.4 }), /8\.4/);
});

test("rvolSaturationNote is empty for every ordinary cell", () => {
  assert.equal(rvolSaturationNote({ value: 3.1 }), "");
  assert.equal(rvolSaturationNote({ value: 3.1, xAvg: 0 }), "");
  assert.equal(rvolSaturationNote({ value: 3.1, xAvg: "lots" }), "");
  assert.equal(rvolSaturationNote(null), "");
  assert.equal(rvolSaturationNote("nope"), "");
});

// ---------------------------------------------------------------------------
// newsBadgeTitle: source, age and scope all existed in the payload and none of
// them reached the screen. A two-day-old round-up looked identical to a fresh
// story about the company.
// ---------------------------------------------------------------------------
test("the badge title leads with the source and the age", () => {
  const title = newsBadgeTitle({
    headline: "Nebius wins AI deal", source: "Yahoo Finance", age: "3h ago", scope: "specific",
  });
  assert.ok(title.startsWith("Yahoo Finance · 3h ago"), title);
  assert.ok(title.includes("Nebius wins AI deal"));
  // source and age on their own line, headline beneath it
  assert.equal(title.split("\n")[0], "Yahoo Finance · 3h ago");
});

test("a market-wide story says so, in words, with the ticker count", () => {
  const title = newsBadgeTitle({
    headline: "10 Information Technology Stocks Whale Activity",
    source: "benzinga", age: "1h ago", scope: "market-wide", namedCount: 10,
  });
  assert.ok(title.includes("Market-wide story naming 10 tickers"), title);
  assert.ok(title.includes("it is not about it"), title);
});

test("a specific story carries no market-wide warning", () => {
  const title = newsBadgeTitle({
    headline: "Ballard Power bought GeoPura", source: "Yahoo Finance", age: "2h ago", scope: "specific",
  });
  assert.ok(!title.includes("Market-wide"), title);
});

test("a payload with no source or age still shows the headline", () => {
  assert.equal(newsBadgeTitle({ headline: "Bare headline", scope: "specific" }), "Bare headline");
});

test("junk never throws", () => {
  assert.equal(newsBadgeTitle(null), "");
  assert.equal(newsBadgeTitle(undefined), "");
  assert.equal(newsBadgeTitle({}), "");
});

test("newsOf defaults an unstamped payload to specific, not market-wide", () => {
  // An older worker ships no scope. Every badge suddenly claiming to be a
  // round-up would be worse than the bug being fixed.
  const out = newsOf({ news: { headline: "Old worker payload", at: new Date().toISOString() } });
  assert.equal(out.scope, "specific");
  assert.equal(out.namedCount, 0);
});

test("newsOf carries the worker's market-wide stamp through", () => {
  const out = newsOf({
    news: {
      headline: "Sector round-up", at: new Date().toISOString(),
      scope: "market-wide", namedCount: 12,
    },
  });
  assert.equal(out.scope, "market-wide");
  assert.equal(out.namedCount, 12);
});

test("liveHighLowCell re-runs his H/L formula on the live price", () => {
  const built = { value: 0.2, bg: "green", fg: "black", hh: 110, ll: 100 };
  // mid 105: 109 -> (109-105)/(110-105) = 0.8, dark green.
  assert.deepEqual(liveHighLowCell(built, 109), { ...built, value: 0.8, bg: "dark_green", live: true });
  // A new high widens the window: hh 112, mid 106 -> exactly +1.
  assert.equal(liveHighLowCell(built, 112).value, 1);
  // A new low: ll 98, mid 104 -> (98-104)/(110-104) = -1, dark red.
  assert.deepEqual([liveHighLowCell(built, 98).value, liveHighLowCell(built, 98).bg], [-1, "dark_red"]);
  assert.equal(liveHighLowCell(built, 104).bg, "red");
});

test("liveHighLowCell keeps the built cell when inputs are missing", () => {
  assert.equal(liveHighLowCell({ value: 0.5, bg: "green" }, 100), null);   // old payload, no hh/ll
  assert.equal(liveHighLowCell({ value: 0.5, hh: 1, ll: 1 }, null), null);  // no live price
  assert.equal(liveHighLowCell(null, 100), null);
  assert.deepEqual(liveHighLowCell({ hh: 5, ll: 5 }, 5), { hh: 5, ll: 5, value: 0, bg: "gray", live: true });
});

test("livePctChange re-runs %Chg on the live price during the session only", () => {
  // ZETA 2026-09-30: base 29.21 -> live 31.70 = +8.52%.
  assert.equal(Math.round(livePctChange(29.21, 31.7, true) * 100) / 100, 8.52);
  assert.equal(livePctChange(29.21, 31.7, false), null);      // after 16:00: keep built
  assert.equal(livePctChange(null, 31.7, true), null);        // old payload, no base
  assert.equal(livePctChange(29.21, null, true), null);
});
