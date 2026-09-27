import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_MOMX_FILTERS,
  optionsSetup,
  gapAndGo,
  newsCatalyst,
  newsMomentum,
  inHotSector,
  setupWarnings,
  bestSetupRanks,
  earlyOpt,
  resetOptLatch,
  LEGACY_STRATEGY_VALUES,
  FILTER_RVOL_TIMEFRAMES,
  FILTER_SKITTLES_TIMEFRAMES,
  FILTER_SQZ_TIMEFRAMES,
  FILTER_TOP_MODES,
  MOMX_FILTER_VERSION,
  coerceFilters,
  coerceThreshold,
  countPassing,
  describeFilters,
  describeMembership,
  filterMembership,
  filterRows,
  filteringGroups,
  gatesActive,
  groupPassCounts,
  chartArrows,
  sqzFires,
  gPlanText,
  isStarred,
  orderByDaily2,
  pushStarredToTop,
  strategyOf,
  strategyTags,
  rowPasses,
  rvolGroupResult,
  rvolIsBullish,
  skittlesGroupResult,
  SKITTLES_STATE_BG,
  sqzGroupResult,
  topMode,
  whyNothingPasses,
  setMomxNewSetups,
} from "./momxFilters.js";

// These tests pin the 2026-09-24/25 setups, switched off in the app on
// 2026-09-25 (MOMX_NEW_SETUPS); run them with the set on.
setMomxNewSetups(true);

// --------------------------------------------------------------------------
// helpers: rows shaped exactly like the board payload (momx/board.py)
// --------------------------------------------------------------------------

const clone = (value) => JSON.parse(JSON.stringify(value));

function cfg(patch = {}) {
  const base = clone(DEFAULT_MOMX_FILTERS);
  return { ...base, ...patch };
}

function cell(value, bg, fg = "black") {
  return { value, bg, fg };
}

function row(overrides = {}) {
  return {
    symbol: "TEST",
    rvol: {},
    sqz: {},
    skittles: {},
    ...overrides,
  };
}

// A row that passes nothing: every cell black, no squeeze reading at all.
function deadRow() {
  const rvol = {};
  for (const tf of FILTER_RVOL_TIMEFRAMES) rvol[tf] = cell(0.1, "black", "black");
  const sqz = {};
  for (const tf of FILTER_SQZ_TIMEFRAMES) sqz[tf] = cell("-", "orange");
  const skittles = {};
  for (const tf of FILTER_SKITTLES_TIMEFRAMES) skittles[tf] = cell(50, "black");
  return row({ rvol, sqz, skittles });
}

// A config with EXACTLY the named groups switched on and the top level set
// to `mode`. Everything else is the default, so a test only has to state what
// it is about.
function topCfg(mode, ...on) {
  const config = cfg({ mode });
  for (const name of ["rvol", "sqz", "skittles"]) {
    config[name] = { ...config[name], on: on.includes(name) };
  }
  return config;
}

// Dead rows that pass exactly ONE group under the defaults.
function rvolHit(symbol = "RVOL") {
  const r = deadRow();
  r.symbol = symbol;
  r.rvol["2h"] = cell(3.1, "cyan"); // 2h is ticked at 2.5 by default
  return r;
}

function sqzHit(symbol = "SQZ") {
  const r = deadRow();
  r.symbol = symbol;
  r.sqz["2h"] = cell("-", "black"); // no squeeze on 2h; 4h stays squeezed
  return r;
}

function skittlesHit(symbol = "SKIT") {
  const r = deadRow();
  r.symbol = symbol;
  r.skittles["3D"] = cell(70, "dark_green"); // a MACD cross-up on 3D
  return r;
}

// --------------------------------------------------------------------------
// RVOL group
// --------------------------------------------------------------------------

test("rvol: a ticked timeframe at or above its own threshold passes", () => {
  const r = deadRow();
  r.rvol["2h"] = cell(2.5, "green");
  assert.equal(rvolGroupResult(r, cfg()), true);
});

test("rvol: just under the threshold fails", () => {
  const r = deadRow();
  r.rvol["2h"] = cell(2.4, "green");
  assert.equal(rvolGroupResult(r, cfg()), false);
});

test("rvol: ANY means one ticked timeframe is enough", () => {
  const r = deadRow();
  r.rvol["4h"] = cell(3.1, "cyan");
  // 1h and 2h are black and far below 2.5; 4h alone carries it.
  assert.equal(rvolGroupResult(r, cfg()), true);
});

test("rvol: ALL means every ticked timeframe must clear its bar", () => {
  const r = deadRow();
  r.rvol["1h"] = cell(3.0, "cyan");
  r.rvol["2h"] = cell(3.0, "cyan");
  r.rvol["4h"] = cell(1.0, "black");
  const config = cfg();
  config.rvol = { ...config.rvol, mode: "all" };
  assert.equal(rvolGroupResult(r, config), false);
  r.rvol["4h"] = cell(2.5, "green");
  assert.equal(rvolGroupResult(r, config), true);
});

test("rvol: an unticked timeframe is ignored however big it reads", () => {
  const r = deadRow();
  r.rvol["5m"] = cell(7.0, "cyan"); // 5m is unticked by default
  assert.equal(rvolGroupResult(r, cfg()), false);
});

test("rvol: bullishOnly rejects a magenta reading at the same number", () => {
  const r = deadRow();
  r.rvol["2h"] = cell(3.5, "magenta");
  assert.equal(rvolGroupResult(r, cfg()), false);
  const loose = cfg();
  loose.rvol = { ...loose.rvol, bullishOnly: false };
  assert.equal(rvolGroupResult(r, loose), true);
});

test("rvol: each timeframe uses ITS OWN threshold, not a shared one", () => {
  const r = deadRow();
  const config = cfg();
  config.rvol = clone(config.rvol);
  config.rvol.timeframes["1h"] = { on: true, min: 5.0 };
  config.rvol.timeframes["2h"] = { on: false, min: 2.5 };
  config.rvol.timeframes["4h"] = { on: false, min: 2.5 };
  r.rvol["1h"] = cell(4.0, "cyan");
  assert.equal(rvolGroupResult(r, config), false, "4.0 must not clear a 5.0 bar");
  r.rvol["1h"] = cell(5.0, "cyan");
  assert.equal(rvolGroupResult(r, config), true);
});

test("rvol: a group with nothing ticked does not take part", () => {
  const config = cfg();
  config.rvol = clone(config.rvol);
  for (const tf of FILTER_RVOL_TIMEFRAMES) config.rvol.timeframes[tf].on = false;
  assert.equal(rvolGroupResult(deadRow(), config), null);
});

test("rvol: a switched-off group does not take part", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  assert.equal(rvolGroupResult(deadRow(), config), null);
});

test("rvol: a missing cell fails rather than throwing", () => {
  const r = row({ rvol: {}, sqz: {}, skittles: {} });
  assert.equal(rvolGroupResult(r, cfg()), false);
});

// The colour ladder is the ONLY record of which side won the bar. Below 0.5
// the script paints black on black and the side is genuinely unknown; the
// filter must not silently treat unknown as bearish.
test("rvolIsBullish reads background first, then the text ladder", () => {
  assert.equal(rvolIsBullish(cell(3.5, "cyan")), true);
  assert.equal(rvolIsBullish(cell(2.5, "green")), true);
  assert.equal(rvolIsBullish(cell(3.5, "magenta")), false);
  assert.equal(rvolIsBullish(cell(2.5, "red")), false);
  assert.equal(rvolIsBullish(cell(1.7, "black", "cyan")), true);
  assert.equal(rvolIsBullish(cell(1.7, "black", "magenta")), false);
  assert.equal(rvolIsBullish(cell(0.2, "black", "black")), null);
});

test("rvol: an unknown-side cell is not failed by bullishOnly", () => {
  const r = deadRow();
  const config = cfg();
  config.rvol = clone(config.rvol);
  config.rvol.timeframes["2h"] = { on: true, min: 0.1 };
  config.rvol.timeframes["1h"].on = false;
  config.rvol.timeframes["4h"].on = false;
  r.rvol["2h"] = cell(0.2, "black", "black");
  assert.equal(rvolGroupResult(r, config), true);
});

// A COLD cell is not a reading of zero. When a study has not warmed up the
// board ships the cell with every field null - `{value: null, bg: null, fg:
// null}` - and 2,662 of those sit in the archived Watchlist history, 1,780 of
// them on 4h, which is ticked by default. `Number(null)` is 0 and 0 clears a
// threshold of 0, so a blank 4h cell used to PASS and put a ticker with no
// data on the board. The colours cannot save it either: null bg and null fg
// make rvolIsBullish return null, which bullishOnly deliberately lets through.
// Same trap as the repo's other Number(null) false zeroes.
test("rvol: a cold cell with no reading fails even a zero threshold", () => {
  const r = deadRow();
  const config = cfg();
  config.rvol = clone(config.rvol);
  config.rvol.timeframes["2h"] = { on: true, min: 0 };
  config.rvol.timeframes["1h"].on = false;
  config.rvol.timeframes["4h"].on = false;
  r.rvol["2h"] = { value: null, bg: null, fg: null };
  assert.equal(rvolGroupResult(r, config), false);
});

// A real zero IS a reading and must still clear a zero bar - the fix has to
// reject blankness, not small numbers.
test("rvol: a genuine 0.0 reading still clears a zero threshold", () => {
  const r = deadRow();
  const config = cfg();
  config.rvol = clone(config.rvol);
  config.rvol.timeframes["2h"] = { on: true, min: 0 };
  config.rvol.timeframes["1h"].on = false;
  config.rvol.timeframes["4h"].on = false;
  r.rvol["2h"] = cell(0, "black", "black");
  assert.equal(rvolGroupResult(r, config), true);
});

// Every shape of "no number here". `undefined` is a cell the payload shipped
// without a value; "" and " " are what a text field yields when it is empty.
test("rvol: undefined and empty-string readings are blank, not zero", () => {
  const config = cfg();
  config.rvol = clone(config.rvol);
  config.rvol.timeframes["2h"] = { on: true, min: 0 };
  config.rvol.timeframes["1h"].on = false;
  config.rvol.timeframes["4h"].on = false;
  for (const blank of [undefined, "", " "]) {
    const r = deadRow();
    r.rvol["2h"] = cell(blank, "cyan");
    assert.equal(rvolGroupResult(r, config), false, `blank ${JSON.stringify(blank)}`);
  }
  // A numeric STRING is a reading and must keep working.
  const r = deadRow();
  r.rvol["2h"] = cell("2.5", "cyan");
  assert.equal(rvolGroupResult(r, config), true);
});

// The board-level proof, because the group verdict is only half of it.
test("rvol: a cold ticker does not reach the board at a zero threshold", () => {
  const config = topCfg("any", "rvol");
  config.rvol = clone(config.rvol);
  for (const tf of FILTER_RVOL_TIMEFRAMES) config.rvol.timeframes[tf].on = false;
  config.rvol.timeframes["4h"] = { on: true, min: 0 };
  const cold = deadRow();
  cold.symbol = "COLD";
  cold.rvol["4h"] = { value: null, bg: null, fg: null };
  const warm = deadRow();
  warm.symbol = "WARM";
  warm.rvol["4h"] = cell(1.2, "cyan");
  assert.deepEqual(
    filterRows([cold, warm], config).map((each) => each.symbol),
    ["WARM"],
  );
});

// --------------------------------------------------------------------------
// the threshold box
// --------------------------------------------------------------------------

// The panel's own half of the same bug. `<input type="number">` reports ""
// while the box is empty - which it is for the keystroke between clearing 2.5
// and typing 1.5 - and `Number("")` is 0, so the old handler SAVED a threshold
// of 0 as soon as the box went blank. That is the value that let the cold
// cells above onto the board. Keeping the previous minimum makes a half-typed
// edit a no-op.
test("coerceThreshold: an emptied box keeps the previous minimum", () => {
  assert.equal(coerceThreshold("", 2.5), 2.5);
  assert.equal(coerceThreshold(" ", 2.5), 2.5);
  assert.equal(coerceThreshold(null, 2.5), 2.5);
  assert.equal(coerceThreshold(undefined, 2.5), 2.5);
  assert.equal(coerceThreshold("abc", 2.5), 2.5);
});

test("coerceThreshold: a typed number replaces the previous minimum", () => {
  assert.equal(coerceThreshold("1.5", 2.5), 1.5);
  assert.equal(coerceThreshold(4, 2.5), 4);
});

// A deliberate 0 must stick. "Any reading at all" is a thing he can ask for -
// it is blankness that is not a reading.
test("coerceThreshold: a deliberate zero is kept, not treated as empty", () => {
  assert.equal(coerceThreshold("0", 2.5), 0);
  assert.equal(coerceThreshold("0.0", 2.5), 0);
});

// --------------------------------------------------------------------------
// SQZ group
// --------------------------------------------------------------------------

test("sqz: fired-cyan and blank both count as a pass by default", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("2", "cyan");
  r.sqz["4h"] = cell("-", "black");
  assert.equal(sqzGroupResult(r, cfg()), true);
});

// The DEFAULT is ANY, matching the TOS scan's SqzFired row (2026-09-05).
test("sqz: ANY (the default) means one clean timeframe is enough", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("7", "orange");
  assert.equal(sqzGroupResult(r, cfg()), true);
});

test("sqz: ALL means one squeezed timeframe sinks the row", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("7", "orange");
  const config = cfg();
  config.sqz = { ...config.sqz, mode: "all" };
  assert.equal(sqzGroupResult(r, config), false);
});

// The OTHER timeframe is squeezed here on purpose: under the ANY default a
// clean 4h would carry the row and the magenta box would prove nothing.
test("sqz: magenta (fired, momentum down) is not a pass by default", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("3", "magenta");
  r.sqz["4h"] = cell("7", "orange");
  assert.equal(sqzGroupResult(r, cfg()), false);
  const config = cfg();
  config.sqz = clone(config.sqz);
  config.sqz.pass.magenta = true;
  assert.equal(sqzGroupResult(r, config), true);
});

// A cold study has no background at all. "Unknown" is not "no squeeze": if it
// passed, every symbol would sail through the group after a worker restart.
test("sqz: a cell with no reading never passes", () => {
  const r = deadRow();
  r.sqz["2h"] = { value: null, bg: null, fg: null };
  r.sqz["4h"] = cell("7", "orange"); // squeezed, so only the cold 2h could pass
  assert.equal(sqzGroupResult(r, cfg()), false);
});

test("sqz: with no state ticked nothing can pass", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("-", "black");
  const config = cfg();
  config.sqz = clone(config.sqz);
  for (const key of Object.keys(config.sqz.pass)) config.sqz.pass[key] = false;
  assert.equal(sqzGroupResult(r, config), false);
});

// --------------------------------------------------------------------------
// Skittles group
// --------------------------------------------------------------------------

// The group is OFF by default now, so these switch it on: they are testing
// the group's own logic, not whether it is in use.
const skittlesOn = () => {
  const config = cfg();
  config.skittles = { ...clone(config.skittles), on: true };
  return config;
};

test("skittles: a cyan BLOCK on any ticked timeframe fires the group", () => {
  const r = deadRow();
  r.skittles["3D"] = cell(88, "cyan");
  assert.equal(skittlesGroupResult(r, skittlesOn()), true);
});

// Cyan TEXT means "9 already above 20" - common, and not the cross event.
test("skittles: cyan TEXT on a black block does not fire the group", () => {
  const r = deadRow();
  r.skittles["3D"] = cell(88, "black", "cyan");
  assert.equal(skittlesGroupResult(r, skittlesOn()), false);
});

test("skittles: an unticked timeframe is ignored", () => {
  const r = deadRow();
  r.skittles.M = cell(88, "cyan");
  const config = skittlesOn();
  config.skittles.timeframes.M = false;
  assert.equal(skittlesGroupResult(r, config), false);
});

// A switched-off group takes no part - it is not a failure, it is absent.
test("skittles: a switched-off group does not take part", () => {
  const r = deadRow();
  r.skittles["3D"] = cell(88, "cyan");
  assert.equal(skittlesGroupResult(r, cfg()), null);
});

// --------------------------------------------------------------------------
// the row verdict - top level ANY (the default); ALL has its own block below
// --------------------------------------------------------------------------

test("row: ANY - one group passing is enough", () => {
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("-", "black"); // SQZ passes; RVOL does not
  assert.equal(rvolGroupResult(r, cfg()), false);
  assert.equal(rowPasses(r, cfg()), true);
});

test("row: ANY - every participating group failing rejects the row", () => {
  assert.equal(rowPasses(deadRow(), cfg()), false);
});

// Skittles defaults to "push to top": it must colour the star without ever
// removing a row. As a FILTER it empties the board at the open (measured 0-1
// tickers at 09:45 ET on 2026-09-02/03/04), which is why this is the default.
// Skittles is OFF by default, so it stars without deciding the verdict - the
// v2 behaviour, now spelled with two independent switches instead of a mode
// that silently overrode the group's own checkbox.
test("row: a switched-off Skittles still stars but never decides the verdict", () => {
  const r = deadRow();
  r.skittles.D = cell(91, "cyan");
  assert.equal(rowPasses(r, cfg()), false);
  assert.equal(isStarred(r, cfg()), true);
});

// The whole point of the 2026-09-05 rework: ticking the box filters, full stop.
test("row: ticking SKITTLES makes it filter, like every other group", () => {
  const r = deadRow();
  r.skittles.D = cell(91, "cyan");
  const config = cfg();
  config.skittles = { ...config.skittles, on: true };
  assert.equal(rowPasses(r, config), true);
  assert.deepEqual([...filteringGroups(config)], ["rvol", "sqz", "skittles"]);
});

test("the star switch is independent of whether the group filters", () => {
  const r = deadRow();
  r.skittles.D = cell(91, "cyan");
  const on = cfg();
  on.skittles = { ...on.skittles, on: true, rankToTop: true };
  assert.equal(isStarred(r, on), true, "filtering AND starring");
  const noStar = cfg();
  noStar.skittles = { ...noStar.skittles, on: true, rankToTop: false };
  assert.equal(isStarred(r, noStar), false, "filtering, not starring");
  assert.equal(rowPasses(r, noStar), true);
});

// An empty filter must never blank the board - that failure mode looks
// exactly like a dead backend.
test("row: with every group off, everything passes", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  config.sqz = { ...config.sqz, on: false };
  config.skittles = { ...config.skittles, on: false };
  assert.equal(rowPasses(deadRow(), config), true);
});

test("filterRows / countPassing agree and leave input order alone", () => {
  const good = deadRow();
  good.symbol = "GOOD";
  good.rvol["1h"] = cell(3.0, "cyan");
  const bad = deadRow();
  bad.symbol = "BAD";
  const rows = [bad, good];
  assert.deepEqual(filterRows(rows, cfg()).map((r) => r.symbol), ["GOOD"]);
  assert.equal(countPassing(rows, cfg()), 1);
  assert.deepEqual(rows.map((r) => r.symbol), ["BAD", "GOOD"], "input untouched");
});

// --------------------------------------------------------------------------
// the top level: ANY (the default) or ALL - his 2026-09-22 request
// --------------------------------------------------------------------------

// ANY is where every existing board already is, and a value the code cannot
// read must land there too: a damaged saved document may WIDEN his board,
// never silently empty it.
test("the top level starts as ANY, and anything it cannot read is ANY", () => {
  assert.deepEqual(FILTER_TOP_MODES, ["any", "all"]);
  assert.equal(Object.isFrozen(FILTER_TOP_MODES), true);
  assert.equal(DEFAULT_MOMX_FILTERS.mode, "any");
  assert.equal(topMode(cfg()), "any");
  assert.equal(topMode(cfg({ mode: "all" })), "all");
  assert.equal(topMode(null), "any");
  assert.equal(topMode(undefined), "any");
  assert.equal(topMode({}), "any");
  assert.equal(topMode({ mode: "ALL" }), "any");
});

test("ALL: an RVOL hit is not enough when SKITTLES does not pass", () => {
  const r = rvolHit();
  const all = topCfg("all", "rvol", "skittles");
  assert.equal(rvolGroupResult(r, all), true);
  assert.equal(skittlesGroupResult(r, all), false);
  assert.equal(rowPasses(r, all), false);
  // the same row and the same groups under ANY: one group is enough
  assert.equal(rowPasses(r, topCfg("any", "rvol", "skittles")), true);
});

test("ALL: a row passing RVOL AND SKITTLES passes", () => {
  const r = rvolHit("BOTH");
  r.skittles["3D"] = cell(70, "dark_green");
  assert.equal(rowPasses(r, topCfg("all", "rvol", "skittles")), true);
});

// His example, word for word: "when i select "Rvol with any timeframes " and
// "Skittles with any timeframes" , i choose "all of the follwoing" then i see
// the scanner list only Rvol and skittles matches?" - SQZ switched OFF. The
// switched-off group is LEFT OUT: it must not block a row it would fail.
test("his example: RVOL (any tf) + SKITTLES (any tf), SQZ off, ALL = both, and only both", () => {
  const config = topCfg("all", "rvol", "skittles");
  config.rvol = { ...config.rvol, mode: "any" };
  config.skittles = { ...config.skittles, mode: "any" };

  const both = rvolHit("BOTH");
  both.skittles["3D"] = cell(70, "dark_green");
  // Every SQZ cell on this row is "still squeezed" (orange): switched ON, the
  // group would fail it. Switched OFF it takes no part.
  assert.equal(sqzGroupResult(both, { ...config, sqz: { ...config.sqz, on: true } }), false);
  assert.equal(sqzGroupResult(both, config), null);
  assert.equal(rowPasses(both, config), true);

  assert.equal(rowPasses(rvolHit(), config), false, "RVOL alone is not enough");
  assert.equal(rowPasses(skittlesHit(), config), false, "SKITTLES alone is not enough");

  const rows = [rvolHit(), both, skittlesHit(), deadRow()];
  assert.deepEqual(filterRows(rows, config).map((r) => r.symbol), ["BOTH"]);
  assert.deepEqual(
    filterRows(rows, { ...config, mode: "any" }).map((r) => r.symbol),
    ["RVOL", "BOTH", "SKIT"],
  );
});

test("ALL with exactly one group on is that group alone", () => {
  const every = deadRow();
  every.symbol = "EVERY";
  every.rvol["2h"] = cell(3.1, "cyan");
  every.sqz["2h"] = cell("-", "black");
  every.skittles["3D"] = cell(70, "dark_green");
  const rows = [deadRow(), rvolHit(), sqzHit(), skittlesHit(), every];
  const alone = { rvol: rvolGroupResult, sqz: sqzGroupResult, skittles: skittlesGroupResult };
  for (const name of ["rvol", "sqz", "skittles"]) {
    const all = topCfg("all", name);
    for (const r of rows) {
      const expected = alone[name](r, all) === true;
      assert.equal(rowPasses(r, all), expected, name + " / " + r.symbol);
      assert.equal(rowPasses(r, topCfg("any", name)), expected, name + " / " + r.symbol + " (ANY)");
    }
  }
});

// An empty filter must never blank the board - under ALL exactly as under
// ANY. A group switched ON with no timeframe ticked is left out the same way.
test("ALL with every group off still passes every row and says NOTHING IS FILTERING", () => {
  const rows = [deadRow(), rvolHit(), skittlesHit()];
  const config = topCfg("all");
  assert.equal(filterRows(rows, config).length, rows.length);
  assert.match(describeFilters(config), /NOTHING IS FILTERING/);
  assert.doesNotMatch(describeFilters(config), /^ALL of: /);

  const noFrames = topCfg("all", "rvol");
  noFrames.rvol = clone(noFrames.rvol);
  for (const tf of FILTER_RVOL_TIMEFRAMES) noFrames.rvol.timeframes[tf].on = false;
  assert.equal(filterRows(rows, noFrames).length, rows.length);
  assert.match(describeFilters(noFrames), /NOTHING IS FILTERING/);
});

// SQZ on, timeframes ticked, but no "counts as a pass" state: the group
// passes nobody, so under ALL it empties the board. The sentence used to
// leave SQZ out altogether - with SQZ the only group it even said "NOTHING IS
// FILTERING - every ticker passes" over a board that showed none.
test("ALL with SQZ on but no state ticked passes nothing, and the sentence names it", () => {
  const config = topCfg("all", "rvol", "sqz");
  config.sqz = clone(config.sqz);
  for (const key of Object.keys(config.sqz.pass)) config.sqz.pass[key] = false;
  const r = rvolHit();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("2", "cyan");
  assert.equal(filterRows([r, deadRow(), sqzHit()], config).length, 0);
  assert.match(
    describeFilters(config),
    /^ALL of: RVOL any of 1h\/2h\/4h ≥ 2\.5 AND SQZ \(no state ticked\)/,
  );
  // the same group, named the same way, under ANY and on its own
  assert.match(describeFilters({ ...config, mode: "any" }), / OR SQZ \(no state ticked\)/);
  const alone = { ...config, rvol: { ...config.rvol, on: false } };
  assert.match(describeFilters(alone), /^ALL of: SQZ \(no state ticked\)/);
  assert.match(describeFilters({ ...alone, mode: "any" }), /^ANY of: SQZ \(no state ticked\)/);
});

test("describeFilters: ALL reads as an AND sentence, ANY is unchanged", () => {
  const all = topCfg("all", "rvol", "sqz", "skittles");
  const text = describeFilters(all);
  assert.equal(
    text,
    "ALL of: RVOL any of 1h/2h/4h ≥ 2.5 AND SQZ any of 2h/4h is cyan or blank" +
      " AND a bullish Skittles cross on any of 2h/4h/D/2D/3D/4D/Wk/M (starred)",
  );
  assert.match(text, /^ALL of: /);
  assert.doesNotMatch(text, / OR /);
  const any = describeFilters({ ...all, mode: "any" });
  assert.equal(
    any,
    "ANY of: RVOL any of 1h/2h/4h ≥ 2.5 OR SQZ any of 2h/4h is cyan or blank" +
      " OR a bullish Skittles cross on any of 2h/4h/D/2D/3D/4D/Wk/M (starred)",
  );
  assert.doesNotMatch(any, / AND /);
});

// --------------------------------------------------------------------------
// property check: the top level against the three group verdicts
// --------------------------------------------------------------------------

// A small LCG, so the "random" board is identical on every run and every
// machine - a failure here must reproduce, never flake.
function lcg(seed) {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

// Rows shaped like the board payload, with every colour each column can
// paint, some missing cells and some cold (no background) cells.
function generatedRows(count, seed) {
  const next = lcg(seed);
  const pick = (list) => list[Math.floor(next() * list.length)];
  const rows = [];
  for (let i = 0; i < count; i += 1) {
    const rvol = {};
    for (const tf of FILTER_RVOL_TIMEFRAMES) {
      if (next() < 0.08) continue;
      rvol[tf] = cell(
        pick([0.2, 0.9, 1.6, 2.4, 2.5, 2.9, 3.0, 4.4, 7.0, null]),
        pick(["black", "black", "cyan", "green", "magenta", "red"]),
        pick(["black", "cyan", "green", "dark_green", "magenta", "red", "dark_red"]),
      );
    }
    const sqz = {};
    for (const tf of FILTER_SQZ_TIMEFRAMES) {
      if (next() < 0.08) continue;
      sqz[tf] = cell("-", pick(["cyan", "black", "black", "magenta", "orange", "orange", "white", null]));
    }
    const skittles = {};
    for (const tf of FILTER_SKITTLES_TIMEFRAMES) {
      if (next() < 0.08) continue;
      skittles[tf] = cell(50, pick([
        "black", "black", "black", "black", "black", "black", "black", "black",
        "cyan", "green", "lime", "dark_green", "magenta", "red", "light_red", "plum", null,
      ]));
    }
    rows.push(row({ symbol: "R" + String(i).padStart(3, "0"), rvol, sqz, skittles }));
  }
  return rows;
}

// Two and three groups on, with group-level ANY and ALL mixed in.
function propertyConfigs() {
  const configs = [topCfg("any", "rvol", "skittles"), topCfg("any", "rvol", "sqz")];

  const sqzSkittles = topCfg("any", "sqz", "skittles");
  sqzSkittles.sqz = { ...sqzSkittles.sqz, mode: "all" };
  sqzSkittles.skittles = clone(sqzSkittles.skittles);
  for (const tf of FILTER_SKITTLES_TIMEFRAMES) {
    sqzSkittles.skittles.timeframes[tf] = ["2h", "D", "Wk"].includes(tf);
  }
  configs.push(sqzSkittles);

  configs.push(topCfg("any", "rvol", "sqz", "skittles"));

  const mixed = topCfg("any", "rvol", "sqz", "skittles");
  mixed.rvol = clone(mixed.rvol);
  mixed.rvol.mode = "all";
  mixed.rvol.timeframes["4h"].on = false; // ALL of 1h/2h
  mixed.skittles = clone(mixed.skittles);
  mixed.skittles.mode = "all";
  for (const tf of FILTER_SKITTLES_TIMEFRAMES) mixed.skittles.timeframes[tf] = ["4h", "D"].includes(tf);
  configs.push(mixed);

  const loose = topCfg("any", "rvol", "sqz", "skittles");
  loose.rvol = clone(loose.rvol);
  loose.rvol.bullishOnly = false;
  loose.rvol.timeframes["5m"] = { on: true, min: 4.0 };
  configs.push(loose);

  return configs;
}

// The expectation, built ONLY from the three group verdicts: a null group is
// not taking part and is skipped; no group taking part means the row passes.
function expectedVerdict(r, config, mode) {
  const results = [
    rvolGroupResult(r, config),
    sqzGroupResult(r, config),
    skittlesGroupResult(r, config),
  ].filter((result) => result !== null);
  if (results.length === 0) return true;
  return mode === "all"
    ? results.every((result) => result === true)
    : results.some((result) => result === true);
}

test("property: over 360 seeded rows ALL = every taking-part group, ANY = at least one", () => {
  const rows = generatedRows(360, 20260922);
  const before = rows.map((r) => r.symbol);
  let separated = false;
  for (const [index, base] of propertyConfigs().entries()) {
    const counts = {};
    for (const mode of FILTER_TOP_MODES) {
      const config = { ...base, mode };
      const label = "config " + index + " " + mode;
      const expected = rows.filter((r) => expectedVerdict(r, config, mode));
      const got = filterRows(rows, config);
      assert.deepEqual(got.map((r) => r.symbol), expected.map((r) => r.symbol), label);
      assert.ok(got.every((r, i) => r === expected[i]), label + ": the same row objects, in order");
      assert.equal(countPassing(rows, config), expected.length, label);
      // "takes part" means one thing everywhere: a null verdict is exactly a
      // group filteringGroups() leaves out
      const active = filteringGroups(config);
      for (const r of rows) {
        assert.equal(rvolGroupResult(r, config) !== null, active.has("rvol"), label);
        assert.equal(sqzGroupResult(r, config) !== null, active.has("sqz"), label);
        assert.equal(skittlesGroupResult(r, config) !== null, active.has("skittles"), label);
      }
      counts[mode] = expected.length;
    }
    assert.ok(counts.any >= counts.all, "ALL can only narrow ANY (config " + index + ")");
    if (counts.all > 0 && counts.all < counts.any && counts.any < rows.length) separated = true;
  }
  // Guard against a vacuous pass: the generated board must actually tell
  // ANY and ALL apart somewhere, with rows on both sides of each.
  assert.ok(separated, "no config separated ANY from ALL - the generator proves nothing");
  assert.deepEqual(rows.map((r) => r.symbol), before, "input order untouched");
});

test("groupPassCounts.passing follows the top level; the group numbers do not", () => {
  const rows = generatedRows(360, 20260922);
  for (const base of propertyConfigs()) {
    const any = groupPassCounts(rows, { ...base, mode: "any" });
    const all = groupPassCounts(rows, { ...base, mode: "all" });
    assert.equal(any.passing, rows.filter((r) => expectedVerdict(r, base, "any")).length);
    assert.equal(all.passing, rows.filter((r) => expectedVerdict(r, base, "all")).length);
    assert.ok(any.passing >= all.passing);
    assert.deepEqual({ ...any, passing: 0 }, { ...all, passing: 0 });
  }
});

// --------------------------------------------------------------------------
// why the filter left 0 tickers
// --------------------------------------------------------------------------

test("whyNothingPasses: ALL with RVOL passing nobody names RVOL", () => {
  const config = topCfg("all", "rvol", "skittles");
  const counts = groupPassCounts([skittlesHit("A"), skittlesHit("B"), deadRow()], config);
  assert.deepEqual([counts.rvol, counts.skittles, counts.passing], [0, 2, 0]);
  assert.equal(
    whyNothingPasses(config, counts),
    "RVOL passes no ticker right now - and ALL needs every switched-on group to pass.",
  );
});

test("whyNothingPasses: every group at zero is named, in RVOL/SQZ/SKITTLES order", () => {
  const config = topCfg("all", "rvol", "sqz", "skittles");
  const two = groupPassCounts([sqzHit("A"), sqzHit("B"), deadRow()], config);
  assert.deepEqual([two.rvol, two.sqz, two.skittles, two.passing], [0, 2, 0, 0]);
  assert.equal(
    whyNothingPasses(config, two),
    "RVOL and SKITTLES pass no ticker right now - and ALL needs every switched-on group to pass.",
  );
  const three = groupPassCounts([deadRow()], config);
  assert.equal(
    whyNothingPasses(config, three),
    "RVOL, SQZ and SKITTLES pass no ticker right now - and ALL needs every switched-on group to pass.",
  );
});

test("whyNothingPasses: ALL where every group passes someone, but never the same row", () => {
  const config = topCfg("all", "rvol", "skittles");
  const counts = groupPassCounts([rvolHit(), skittlesHit(), deadRow()], config);
  assert.deepEqual([counts.rvol, counts.skittles, counts.passing], [1, 1, 0]);
  assert.equal(
    whyNothingPasses(config, counts),
    "Each switched-on group passes some tickers, but no ticker passes RVOL and SKITTLES" +
      " at the same time right now.",
  );
});

test("whyNothingPasses: ANY names only the groups that are switched on", () => {
  const three = topCfg("any", "rvol", "sqz", "skittles");
  const counts = groupPassCounts([deadRow(), deadRow()], three);
  assert.equal(counts.passing, 0);
  assert.equal(
    whyNothingPasses(three, counts),
    "None of the switched-on groups (RVOL, SQZ and SKITTLES) passes a ticker right now.",
  );
  const two = topCfg("any", "rvol", "skittles");
  assert.equal(
    whyNothingPasses(two, groupPassCounts([deadRow()], two)),
    "None of the switched-on groups (RVOL and SKITTLES) passes a ticker right now.",
  );
});

test("whyNothingPasses says nothing when there is nothing to explain", () => {
  const config = topCfg("all", "rvol", "skittles");
  const both = rvolHit("BOTH");
  both.skittles["3D"] = cell(70, "dark_green");
  // something passes
  assert.equal(whyNothingPasses(config, groupPassCounts([both, deadRow()], config)), null);
  // an empty board: 0 of 0 is not the filter's doing
  assert.equal(whyNothingPasses(config, groupPassCounts([], config)), null);
  // the panel is closed, so no counts were taken
  assert.equal(whyNothingPasses(config, null), null);
  assert.equal(whyNothingPasses(null, groupPassCounts([deadRow()], config)), null);
  // nothing is filtering: the loud "Nothing is filtering" warning owns that
  assert.equal(
    whyNothingPasses(topCfg("all"), { rvol: 0, sqz: 0, skittles: 0, total: 5, passing: 0 }),
    null,
  );
});

// --------------------------------------------------------------------------
// push to top
// --------------------------------------------------------------------------

test("pushStarredToTop is a stable partition, not a re-sort", () => {
  const mk = (symbol, starred) => {
    const r = deadRow();
    r.symbol = symbol;
    if (starred) r.skittles["2h"] = cell(90, "cyan");
    return r;
  };
  const rows = [mk("A", false), mk("B", true), mk("C", false), mk("D", true)];
  assert.deepEqual(
    pushStarredToTop(rows, cfg()).map((r) => r.symbol),
    ["B", "D", "A", "C"],
  );
});

test("pushStarredToTop does nothing when the star switch is off", () => {
  const config = cfg();
  config.skittles = { ...config.skittles, rankToTop: false };
  const r = deadRow();
  r.skittles["2h"] = cell(90, "cyan");
  const rows = [deadRow(), r];
  assert.deepEqual(pushStarredToTop(rows, config).length, 2);
  assert.equal(pushStarredToTop(rows, config)[0], rows[0]);
});

// --------------------------------------------------------------------------
// persistence
// --------------------------------------------------------------------------

test("coerceFilters returns the defaults for junk, null and an old version", () => {
  for (const input of [null, undefined, 7, "x", { version: 0 }]) {
    assert.deepEqual(coerceFilters(input), clone(DEFAULT_MOMX_FILTERS));
  }
});

// A config saved before a timeframe existed must still get that timeframe,
// otherwise the panel renders an undefined row and the filter reads NaN.
test("coerceFilters fills in a timeframe the saved config never knew about", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.version = MOMX_FILTER_VERSION;
  delete saved.rvol.timeframes["30m"];
  delete saved.skittles.timeframes["4D"];
  const out = coerceFilters(saved);
  assert.deepEqual(out.rvol.timeframes["30m"], DEFAULT_MOMX_FILTERS.rvol.timeframes["30m"]);
  assert.equal(out.skittles.timeframes["4D"], true);
});

test("coerceFilters keeps the trader's edits", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.rvol.timeframes["1h"].min = 4.5;
  saved.rvol.timeframes.D.on = true;
  saved.sqz.mode = "any";
  saved.skittles.on = true;
  const out = coerceFilters(saved);
  assert.equal(out.rvol.timeframes["1h"].min, 4.5);
  assert.equal(out.rvol.timeframes.D.on, true);
  assert.equal(out.sqz.mode, "any");
  assert.equal(out.skittles.on, true);
});

// "Scan matches only" lives on the board, not in this config - one control,
// one value. A copy here could disagree with the tick beside it.
test("the filter config carries no matchesOnly of its own", () => {
  assert.equal("matchesOnly" in DEFAULT_MOMX_FILTERS, false);
  assert.equal("matchesOnly" in coerceFilters(null), false);
});

// Until 2026-09-22 this test asserted the OPPOSITE: the panel had no top-level
// control ("Only ANY OF THE FOLLOWING should be in filter", 2026-09-05), so a
// stray saved `mode` was DROPPED. He then asked for the dropdown, so a saved
// "all" is his choice - and it must survive a reload, or his ALL would quietly
// turn back into ANY overnight.
test("a saved top-level ALL survives a reload and makes the block ALL-of", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.mode = "all";
  const out = coerceFilters(JSON.parse(JSON.stringify(saved)));
  assert.equal(out.mode, "all");
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black");
  r.sqz["4h"] = cell("-", "black");
  // SQZ passes; RVOL (switched on by default) does not
  assert.equal(rowPasses(r, out), false, "under ALL, SQZ alone no longer carries the row");
  assert.equal(rowPasses(r, { ...out, mode: "any" }), true, "under ANY it still does");
});

test("coerceFilters keeps a saved ALL and reads anything else as ANY, edits intact", () => {
  const edited = (mode) => {
    const saved = clone(DEFAULT_MOMX_FILTERS);
    saved.mode = mode;
    saved.rvol.timeframes["1h"].min = 4.5;
    saved.sqz.on = false;
    saved.skittles.on = true;
    return saved;
  };
  const cases = [
    ["all", "all"], ["any", "any"],
    ["ALL", "any"], ["and", "any"], [7, "any"], [null, "any"], [undefined, "any"],
  ];
  for (const [mode, expected] of cases) {
    const out = coerceFilters(edited(mode));
    const label = String(mode) + " -> " + expected;
    assert.equal(out.mode, expected, label);
    assert.equal(out.rvol.timeframes["1h"].min, 4.5, label);
    assert.equal(out.sqz.on, false, label);
    assert.equal(out.skittles.on, true, label);
  }
});

// HIS REAL UPGRADE CASE. Every document saved before 2026-09-22 has no
// top-level `mode` at all, and his carries his own tuning. It must come back
// as ANY - which is what it always meant - with every other setting exactly
// as he left it: no version bump, no reset, his board unchanged until he
// picks ALL himself.
test("his saved settings from before the dropdown come back as ANY, untouched", () => {
  const saved = {
    // What his phone actually carries: a v3 document (the v4 scanner-grade
    // bump MIGRATES it - see the v4 tests below).
    version: 3,
    rvol: {
      on: true,
      mode: "any",
      bullishOnly: true,
      timeframes: {
        "5m": { on: false, min: 5 },
        "15m": { on: true, min: 6 },
        "30m": { on: true, min: 2 },
        "1h": { on: true, min: 1 },
        "2h": { on: true, min: 1 },
        "4h": { on: true, min: 1 },
        D: { on: true, min: 1 },
      },
    },
    sqz: {
      on: false,
      mode: "any",
      timeframes: { "2h": true, "4h": true, D: false, Wk: false },
      pass: { cyan: true, blank: true, magenta: false, orange: false, white: false },
    },
    skittles: {
      on: true,
      mode: "any",
      rankToTop: true,
      pass: {
        cyan: true, green: true, lime: true, dark_green: true,
        magenta: false, red: false, light_red: false, plum: false,
      },
      timeframes: {
        "2h": true, "4h": true, D: true, "2D": true,
        "3D": true, "4D": true, Wk: true, M: true,
      },
    },
  };
  assert.equal("mode" in saved, false);
  // Through JSON, exactly as it sits in localStorage.
  const out = coerceFilters(JSON.parse(JSON.stringify(saved)));
  assert.equal(out.mode, "any");
  assert.equal(out.version, MOMX_FILTER_VERSION);
  const kept = JSON.parse(JSON.stringify(out));
  delete kept.mode;
  // The v4 migration adds the three scanner-grade gates, all "any".
  assert.deepEqual([kept.setup, kept.momentum, kept.pattern], ["any", "any", "any"]);
  delete kept.setup;
  delete kept.momentum;
  delete kept.pattern;
  assert.deepEqual(kept, { ...saved, version: MOMX_FILTER_VERSION },
    "every other setting exactly as he saved it");
  // and his board reads as it did: under ANY one passing group is enough
  assert.equal(rowPasses(skittlesHit(), out), true);
});

test("describeFilters reads as an ANY sentence and never crashes", () => {
  const text = describeFilters(cfg());
  assert.match(text, /^ANY of: /);
  assert.match(text, /RVOL any of 1h\/2h\/4h/);
  assert.match(text, /SQZ any of 2h\/4h/);
  assert.equal(describeFilters(null), "");
});

// --------------------------------------------------------------------------
// which groups actually decide anything (his 2026-09-05 report)
// --------------------------------------------------------------------------

// He switched RVOL and SQZ off, switched SKITTLES on and ticked D - and every
// ticker still showed. Skittles was on "push to top", which only re-orders, so
// nothing was filtering. The sentence still claimed it as a condition.
test("with every group switched off, nothing filters and all rows pass", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  config.sqz = { ...config.sqz, on: false };
  assert.deepEqual([...filteringGroups(config)], []);
  assert.equal(rowPasses(deadRow(), config), true);
});

test("the sentence says NOTHING IS FILTERING rather than naming a dead rule", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  config.sqz = { ...config.sqz, on: false };
  const text = describeFilters(config);
  assert.match(text, /NOTHING IS FILTERING/);
  assert.doesNotMatch(text, /^ANY of: /);
  // and it still says what the ranking is doing, because that IS switched on
  assert.match(text, /pushed to top/);
});

test("ticking Skittles alone makes it the only filtering group", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  config.sqz = { ...config.sqz, on: false };
  config.skittles = { ...config.skittles, on: true };
  assert.deepEqual([...filteringGroups(config)], ["skittles"]);
  // Filtering AND starring is ONE clause - the phrase must not appear twice.
  assert.match(describeFilters(config), /^ANY of: a bullish Skittles cross on any of .*\(starred\)$/);
  assert.doesNotMatch(describeFilters(config), /pushed to top/);
});

// "any of D" reads like a bug to a human; one timeframe has no "any".
test("one ticked Skittles timeframe drops the pointless \"any of\"", () => {
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  config.sqz = { ...config.sqz, on: false };
  config.skittles = clone(config.skittles);
  config.skittles.on = true;
  config.skittles.rankToTop = false;
  for (const tf of FILTER_SKITTLES_TIMEFRAMES) config.skittles.timeframes[tf] = tf === "D";
  assert.match(describeFilters(config), /^ANY of: a bullish Skittles cross on D$/);
});

// --------------------------------------------------------------------------
// Skittles paints: MACD was unreachable until 2026-09-05
// --------------------------------------------------------------------------

// "Skittles you didn't add MACD?" - the column paints FOUR bullish events and
// only cyan (the EMA 9/20 cross) could be filtered on. Measured on that
// night's 8 matches: 0 cyan cells, but 4 dark_green, 2 green, 1 lime.
test("skittles: a MACD cross-up (dark_green) passes by default", () => {
  const r = deadRow();
  r.skittles.D = cell(70, "dark_green");
  assert.equal(skittlesGroupResult(r, skittlesOn()), true);
});

test("skittles: MACD-up-with-9-above-20 (green) and EMA4x8 (lime) pass too", () => {
  for (const bg of ["green", "lime", "cyan"]) {
    const r = deadRow();
    r.skittles.D = cell(70, bg);
    assert.equal(skittlesGroupResult(r, skittlesOn()), true, bg + " must pass");
  }
});

test("skittles: the bearish paints do NOT pass by default", () => {
  for (const bg of ["magenta", "red", "light_red", "plum"]) {
    const r = deadRow();
    r.skittles.D = cell(30, bg);
    assert.equal(skittlesGroupResult(r, skittlesOn()), false, bg + " must not pass");
  }
});

test("skittles: unticking a paint removes it", () => {
  const r = deadRow();
  r.skittles.D = cell(70, "dark_green");
  const config = skittlesOn();
  config.skittles.pass = { ...config.skittles.pass, dark_green: false };
  assert.equal(skittlesGroupResult(r, config), false);
});

test("skittles: with no paint ticked nothing can pass", () => {
  const r = deadRow();
  r.skittles.D = cell(70, "cyan");
  const config = skittlesOn();
  config.skittles.pass = {};
  assert.equal(skittlesGroupResult(r, config), false);
});

// Every paint the column can emit must be tickable, or it is unreachable -
// which is exactly the bug this block exists for.
test("SKITTLES_STATE_BG covers every non-black background the column paints", () => {
  const painted = [
    "magenta", "red", "light_red", "plum",
    "cyan", "green", "lime", "dark_green",
  ];
  assert.deepEqual(Object.keys(SKITTLES_STATE_BG).sort(), painted.sort());
});

// A switched-off group must still report what switching it ON would give -
// 0 beside an off group reads as "this finds nothing", the opposite of true.
test("groupPassCounts scores every group as if switched on", () => {
  const hot = deadRow();
  hot.rvol["2h"] = cell(3.1, "cyan");
  const config = cfg();
  config.rvol = { ...config.rvol, on: false };
  assert.equal(groupPassCounts([hot, deadRow()], config).rvol, 1);
});

test("a group with every timeframe unticked is not a filtering group", () => {
  const config = cfg();
  config.sqz = clone(config.sqz);
  for (const tf of FILTER_SQZ_TIMEFRAMES) config.sqz.timeframes[tf] = false;
  assert.equal(filteringGroups(config).has("sqz"), false);
});

test("groupPassCounts reports each group on its own plus the whole filter", () => {
  const hot = deadRow();
  hot.symbol = "HOT";
  hot.rvol["2h"] = cell(3.1, "cyan");
  const clean = deadRow();
  clean.symbol = "CLEAN";
  clean.sqz["2h"] = cell("-", "black");
  const star = deadRow();
  star.symbol = "STAR";
  star.skittles.D = cell(91, "cyan");
  const counts = groupPassCounts([hot, clean, star, deadRow()], cfg());
  assert.equal(counts.total, 4);
  assert.equal(counts.rvol, 1);
  assert.equal(counts.sqz, 1);
  assert.equal(counts.skittles, 1);
  // Skittles is switched OFF by default, so STAR does not pass the filter.
  assert.equal(counts.passing, 2);
});

// --------------------------------------------------------------------------
// v4: Setup / Momentum / Pattern gates (scanner grade, 2026-09-22)
// --------------------------------------------------------------------------
//
// These are REQUIREMENTS on top of the ANY-of groups, not another ANY member:
// "A+ only" means a B row is gone whatever its RVOL reads. A row with no grade
// (or no m5 block) never passes an active gate - a missing cell never passes.

// Every group off, so only the gate under test decides.
function gateCfg(patch = {}) {
  const base = cfg();
  base.rvol.on = false;
  base.sqz.on = false;
  base.skittles.on = false;
  return { ...base, ...patch };
}

function gradedRow(letter, state = null, pattern = null) {
  return row({
    grade: letter ? { letter } : null,
    m5: state || pattern ? { state, pattern } : null,
  });
}

test("v4 defaults: setup, momentum and pattern are all 'any'", () => {
  assert.equal(MOMX_FILTER_VERSION, 4);
  assert.equal(DEFAULT_MOMX_FILTERS.setup, "any");
  assert.equal(DEFAULT_MOMX_FILTERS.momentum, "any");
  assert.equal(DEFAULT_MOMX_FILTERS.pattern, "any");
});

test("setup 'aPlus' keeps only A+ rows", () => {
  const config = gateCfg({ setup: "aPlus" });
  assert.equal(rowPasses(gradedRow("A+"), config), true);
  assert.equal(rowPasses(gradedRow("A"), config), false);
  assert.equal(rowPasses(gradedRow("B"), config), false);
  assert.equal(rowPasses(gradedRow(null), config), false);
  assert.equal(rowPasses(row(), config), false, "a row with no grade field never passes");
});

test("setup 'aAndUp' keeps A+ and A", () => {
  const config = gateCfg({ setup: "aAndUp" });
  assert.equal(rowPasses(gradedRow("A+"), config), true);
  assert.equal(rowPasses(gradedRow("A"), config), true);
  assert.equal(rowPasses(gradedRow("B"), config), false);
  assert.equal(rowPasses(gradedRow(null), config), false);
});

test("momentum 'building' keeps only m5.state === building", () => {
  const config = gateCfg({ momentum: "building" });
  assert.equal(rowPasses(gradedRow("A+", "building"), config), true);
  assert.equal(rowPasses(gradedRow(null, "building"), config), true);
  assert.equal(rowPasses(gradedRow("A+", "holding"), config), false);
  assert.equal(rowPasses(gradedRow("A+", "fading"), config), false);
  assert.equal(rowPasses(gradedRow("A+"), config), false, "no m5 block never passes");
});

test("pattern gate: explosive / steady / either", () => {
  const exp = gradedRow(null, "building", "explosive");
  const std = gradedRow(null, "holding", "steady");
  const none = gradedRow("A+", "building");
  assert.equal(rowPasses(exp, gateCfg({ pattern: "explosive" })), true);
  assert.equal(rowPasses(std, gateCfg({ pattern: "explosive" })), false);
  assert.equal(rowPasses(std, gateCfg({ pattern: "steady" })), true);
  assert.equal(rowPasses(exp, gateCfg({ pattern: "steady" })), false);
  assert.equal(rowPasses(exp, gateCfg({ pattern: "either" })), true);
  assert.equal(rowPasses(std, gateCfg({ pattern: "either" })), true);
  assert.equal(rowPasses(none, gateCfg({ pattern: "either" })), false);
});

test("gates AND with the ANY-of groups: a group pass cannot rescue a failed gate", () => {
  const config = cfg({ setup: "aPlus" });
  const r = deadRow();
  r.sqz["2h"] = cell("-", "black"); // SQZ group passes on its own
  r.grade = { letter: "A" };
  assert.equal(rowPasses(r, config), false);
  r.grade = { letter: "A+" };
  assert.equal(rowPasses(r, config), true);
  const dead = deadRow();
  dead.grade = { letter: "A+" };
  assert.equal(rowPasses(dead, config), false, "the groups still have to pass too");
});

test("gates 'any' change nothing about existing behaviour", () => {
  const r = deadRow();
  assert.equal(rowPasses(r, cfg()), false);
  assert.equal(rowPasses(row(), gateCfg()), true, "no groups, no gates: everything passes");
});

test("coerceFilters migrates a v3 document to v4 keeping every field", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.version = 3;
  delete saved.setup;
  delete saved.momentum;
  delete saved.pattern;
  saved.rvol.timeframes["1h"].min = 4.5;
  saved.sqz.mode = "all";
  saved.skittles.on = true;
  saved.skittles.pass.cyan = false;
  const out = coerceFilters(saved);
  assert.equal(out.version, 4);
  assert.equal(out.setup, "any");
  assert.equal(out.momentum, "any");
  assert.equal(out.pattern, "any");
  assert.equal(out.rvol.timeframes["1h"].min, 4.5);
  assert.equal(out.sqz.mode, "all");
  assert.equal(out.skittles.on, true);
  assert.equal(out.skittles.pass.cyan, false);
});

test("coerceFilters keeps saved gates and rejects unknown gate values", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.setup = "aPlus";
  saved.momentum = "building";
  saved.pattern = "either";
  let out = coerceFilters(saved);
  assert.equal(out.setup, "aPlus");
  assert.equal(out.momentum, "building");
  assert.equal(out.pattern, "either");
  saved.setup = "Z";
  saved.momentum = 5;
  saved.pattern = null;
  out = coerceFilters(saved);
  assert.equal(out.setup, "any");
  assert.equal(out.momentum, "any");
  assert.equal(out.pattern, "any");
});

test("coerceFilters still resets a pre-v3 document", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.version = 2;
  saved.rvol.timeframes["1h"].min = 4.5;
  assert.deepEqual(coerceFilters(saved), clone(DEFAULT_MOMX_FILTERS));
});

test("gates count as filtering: no 'nothing is filtering' sentence when one is set", () => {
  const text = describeFilters(gateCfg({ setup: "aPlus", momentum: "building" }));
  assert.doesNotMatch(text, /NOTHING IS FILTERING/);
  assert.ok(text.startsWith("Only Setup A+ AND Momentum Building. "), text);
  assert.match(text, /pushed to top$/, "the ranking clause still follows");
  assert.equal(gatesActive(gateCfg({ pattern: "steady" })), true);
  assert.equal(gatesActive(gateCfg()), false);
});

// --------------------------------------------------------------------------
// merge of main 695d638 (top-level ANY/ALL) with the v4 scanner-grade gates
// --------------------------------------------------------------------------

test("a v3 document WITH a top-level ALL migrates to v4 keeping ALL and its groups", () => {
  const saved = clone(DEFAULT_MOMX_FILTERS);
  saved.version = 3;
  delete saved.setup;
  delete saved.momentum;
  delete saved.pattern;
  saved.mode = "all";
  saved.rvol.timeframes["1h"].min = 4.5;
  saved.sqz.on = false;
  saved.skittles.on = true;
  saved.skittles.mode = "all";
  const out = coerceFilters(JSON.parse(JSON.stringify(saved)));
  assert.equal(out.version, 4);
  assert.equal(out.mode, "all", "the top-level ALL is carried across the v4 bump");
  assert.equal(topMode(out), "all");
  assert.equal(out.rvol.on, true);
  assert.equal(out.rvol.timeframes["1h"].min, 4.5);
  assert.equal(out.sqz.on, false);
  assert.equal(out.skittles.on, true);
  assert.equal(out.skittles.mode, "all");
  assert.deepEqual([out.setup, out.momentum, out.pattern], ["any", "any", "any"]);
  assert.deepEqual([...filteringGroups(out)].sort(), ["rvol", "skittles"]);
});

test("ALL + a setup gate: the gate is ANDed first, the groups combine with ALL", () => {
  const config = topCfg("all", "rvol", "skittles");
  config.setup = "aPlus";
  const both = rvolHit("BOTH");
  both.skittles["3D"] = cell(70, "dark_green");
  const rvolOnly = rvolHit("RONLY");
  // A+ and both groups pass -> passes
  both.grade = { letter: "A+" };
  assert.equal(rowPasses(both, config), true);
  // A (gate fails) -> rejected even though both groups pass
  both.grade = { letter: "A" };
  assert.equal(rowPasses(both, config), false);
  // A+ but only one group -> rejected by ALL; the same row passes under ANY
  rvolOnly.grade = { letter: "A+" };
  assert.equal(rowPasses(rvolOnly, config), false);
  assert.equal(rowPasses(rvolOnly, { ...config, mode: "any" }), true);
  // no grade at all never passes an active gate, in either mode
  const ungraded = rvolHit("NOGRADE");
  ungraded.skittles["3D"] = cell(70, "dark_green");
  assert.equal(rowPasses(ungraded, config), false);
  assert.equal(rowPasses(ungraded, { ...config, mode: "any" }), false);
  // the sentence leads with the gate and joins the groups with AND
  const text = describeFilters(config);
  assert.ok(text.startsWith("Only Setup A+, and ALL of: "), text);
  assert.match(text, / AND /);
});

test("a garbage top-level mode reads as ANY, through coerceFilters and topMode", () => {
  for (const bad of ["ALL", "every", 1, true, null, {}, []]) {
    const saved = clone(DEFAULT_MOMX_FILTERS);
    saved.mode = bad;
    saved.setup = "aAndUp";
    const out = coerceFilters(JSON.parse(JSON.stringify(saved)));
    assert.equal(out.mode, "any", JSON.stringify(bad));
    assert.equal(out.setup, "aAndUp", "the gates survive a bad mode");
    assert.equal(topMode({ mode: bad }), "any");
  }
  const v3 = clone(DEFAULT_MOMX_FILTERS);
  v3.version = 3;
  v3.mode = "Any of the following";
  assert.equal(coerceFilters(v3).mode, "any");
});

test("whyNothingPasses names the scanner-grade filters when they are what empties the board", () => {
  const gatesOnly = gateCfg({ setup: "aPlus" });
  assert.equal(
    whyNothingPasses(gatesOnly, groupPassCounts([gradedRow("B")], gatesOnly)),
    "No ticker passes the Setup / Momentum / Pattern filters right now.",
  );
  const all = topCfg("all", "rvol", "skittles");
  all.setup = "aPlus";
  const both = rvolHit("BOTH");
  both.skittles["3D"] = cell(70, "dark_green");
  both.grade = { letter: "B" };
  assert.match(whyNothingPasses(all, groupPassCounts([both], all)), /together with the Setup/);
  const any = { ...all, mode: "any" };
  assert.match(whyNothingPasses(any, groupPassCounts([both], any)), /also passes the Setup/);
});

// --------------------------------------------------------------------------
// what is IN the filter (2026-09-22, his phone)
// --------------------------------------------------------------------------

// His phone at 07:26 CT: ALL on top, RVOL's OWN box unticked with 1h/2h/4h
// still ticked inside it, SQZ unticked, SKITTLES on with cyan/green. The board
// showed 15 tickers with no RVOL and he called ALL "wrong" - it was right,
// the panel just let a switched-off group look switched on. The line under
// the top dropdown now says which groups the filter is really using.
function hisPhone() {
  const config = cfg({ mode: "all" });
  config.rvol = { ...config.rvol, on: false, timeframes: {
    ...config.rvol.timeframes,
    "1h": { on: true, min: 1 }, "2h": { on: true, min: 1 }, "4h": { on: true, min: 1 },
  } };
  config.sqz = { ...config.sqz, on: false, mode: "all" };
  config.skittles = { ...config.skittles, on: true, pass: {
    ...config.skittles.pass, lime: false, dark_green: false,
  } };
  return config;
}

test("membership: his phone reads SKITTLES in, RVOL and SQZ left out", () => {
  const config = hisPhone();
  assert.deepEqual(filterMembership(config), {
    inFilter: ["SKITTLES"], off: ["RVOL", "SQZ"], noTimeframe: [], gates: [],
  });
  assert.equal(describeMembership(config), "In the filter: SKITTLES. Left out (off): RVOL and SQZ.");
});

test("membership: every group on is one clause, with no 'left out'", () => {
  const config = topCfg("all", "rvol", "sqz", "skittles");
  assert.equal(describeMembership(config), "In the filter: RVOL, SQZ and SKITTLES.");
});

test("membership: a group that is on with no timeframe ticked is left out, and says why", () => {
  const config = topCfg("any", "rvol", "sqz");
  config.sqz = { ...config.sqz, timeframes: { "2h": false, "4h": false, D: false, Wk: false } };
  assert.deepEqual(filterMembership(config).noTimeframe, ["SQZ"]);
  assert.equal(
    describeMembership(config),
    "In the filter: RVOL. Left out (off): SKITTLES. Left out (no timeframe ticked): SQZ.",
  );
});

test("membership: the scanner-grade filters are named with the groups", () => {
  const config = hisPhone();
  config.setup = "aPlus";
  assert.equal(
    describeMembership(config),
    "In the filter: SKITTLES and Setup A+. Left out (off): RVOL and SQZ.",
  );
  const gatesOnly = gateCfg({ momentum: "building" });
  assert.equal(
    describeMembership(gatesOnly),
    "In the filter: Momentum Building. Left out (off): RVOL, SQZ and SKITTLES.",
  );
});

test("membership: nothing in the filter reads as null (the loud warning covers it)", () => {
  assert.equal(describeMembership(topCfg("all")), null);
  assert.equal(describeMembership(null), null);
});

// Every on/off x ticked/unticked combination of the three groups: each group
// lands in exactly one bucket, "in" is exactly what filteringGroups() filters
// on, and the sentence names every group once. A line that disagreed with the
// filter would be the same lie as a switched-off group that looks on.
test("membership agrees with filteringGroups for all 64 group states", () => {
  const names = [["rvol", "RVOL"], ["sqz", "SQZ"], ["skittles", "SKITTLES"]];
  for (let bits = 0; bits < 64; bits += 1) {
    const config = cfg({ mode: bits & 1 ? "all" : "any" });
    names.forEach(([name], i) => {
      const on = Boolean(bits & (1 << (i * 2)));
      const ticked = Boolean(bits & (1 << (i * 2 + 1)));
      const group = { ...config[name], on };
      if (name === "rvol") {
        group.timeframes = Object.fromEntries(
          FILTER_RVOL_TIMEFRAMES.map((tf) => [tf, { on: ticked && tf === "1h", min: 1 }]),
        );
      } else {
        const frames = name === "sqz" ? FILTER_SQZ_TIMEFRAMES : FILTER_SKITTLES_TIMEFRAMES;
        group.timeframes = Object.fromEntries(frames.map((tf) => [tf, ticked && tf === "4h"]));
      }
      config[name] = group;
    });
    const active = filteringGroups(config);
    const m = filterMembership(config);
    for (const [name, label] of names) {
      const buckets = [m.inFilter, m.off, m.noTimeframe].filter((list) => list.includes(label));
      assert.equal(buckets.length, 1, `bits=${bits} ${label} in exactly one bucket`);
      assert.equal(m.inFilter.includes(label), active.has(name), `bits=${bits} ${label} in = filtering`);
      assert.equal(m.off.includes(label), !config[name].on, `bits=${bits} ${label} off = box unticked`);
    }
    const text = describeMembership(config);
    if (active.size === 0) {
      assert.equal(text, null, `bits=${bits}`);
    } else {
      for (const [, label] of names) {
        assert.equal(text.split(label).length - 1, 1, `bits=${bits} ${label} named once in: ${text}`);
      }
    }
  }
});

// --------------------------------------------------------------------------
// Strategy choices inside "A/A+ Setup" (2026-09-23): V2 / V3 / Daily 2
// --------------------------------------------------------------------------
//
// His ask: "instead of each column to check A/A+ i can trade only V2 or V3 or
// Daily2" - and "add in that column only". The worker decides the rules
// (momx/strategy.py, row.strategy); a strategy choice is the WHOLE filter:
// the groups and the other two gates are left out, so his ALL + Skittles
// tuning can never silently empty a strategy board.

function stratRow(symbol, strategy) {
  const r = row({ symbol, grade: { letter: "A+" }, m5: { state: "fading" } });
  r.strategy = strategy;
  return r;
}

test("strategy setups pass on row.strategy alone", () => {
  const v2 = stratRow("A", { v2: true, v3: false, daily2: null });
  const v3 = stratRow("B", { v2: true, v3: true, daily2: null });
  const d2 = stratRow("C", { v2: false, v3: false, daily2: { rank: 1, at: "x", price: 1 } });
  const none = stratRow("D", { v2: false, v3: false, daily2: null });
  const bare = stratRow("E", undefined);
  const pick = (setup) => filterRows([v2, v3, d2, none, bare], cfg({ setup })).map((r) => r.symbol);
  assert.deepEqual(pick("v2"), ["A", "B"]);
  assert.deepEqual(pick("v3"), ["B"]);
  assert.deepEqual(pick("daily2"), ["C"], "a latched Daily 2 stays even when V2 no longer passes");
});

test("a strategy ignores the groups and the Momentum / Pattern gates", () => {
  // His phone: ALL + Skittles on, which passes nobody here - and Momentum
  // 'building', which contradicts V2's 'extended'. The strategy still decides.
  const config = hisPhone();
  config.setup = "v2";
  config.momentum = "building";
  config.pattern = "explosive";
  const r = stratRow("ARM", { v2: true, v3: false, daily2: null });
  assert.equal(rowPasses(r, config), true);
  assert.deepEqual([...filteringGroups(config)], []);
  assert.equal(gatesActive(config), true, "so the 'nothing is filtering' warning stays away");
  assert.equal(strategyOf(config), "v2");
  assert.equal(strategyOf(cfg({ setup: "aPlus" })), null);
});

test("strategy values survive coerceFilters; junk still reads as any", () => {
  for (const setup of ["v2", "v3", "daily2"]) {
    assert.equal(coerceFilters({ ...clone(DEFAULT_MOMX_FILTERS), setup }).setup, setup);
  }
  assert.equal(coerceFilters({ ...clone(DEFAULT_MOMX_FILTERS), setup: "V2" }).setup, "any");
});

test("strategy sentences: footer, membership and the empty-board reason", () => {
  const config = hisPhone();
  config.setup = "daily2";
  assert.match(describeFilters(config), /^Strategy Daily 2: /);
  assert.doesNotMatch(describeFilters(config), /SKITTLES|Skittles/);
  assert.equal(
    describeMembership(config),
    "In the filter: Strategy Daily 2 only. Left out while a strategy is picked: RVOL, SQZ, SKITTLES, Momentum and Pattern.",
  );
  const empty = groupPassCounts([stratRow("X", { v2: false, v3: false, daily2: null })], config);
  assert.equal(
    whyNothingPasses(config, empty),
    "No ticker has qualified for Daily 2 today yet - the first can qualify from 9:35 AM ET.",
  );
  config.setup = "v3";
  assert.equal(
    whyNothingPasses(config, empty),
    "No ticker has qualified for Strategy V3 today yet - the first can qualify from 9:35 AM ET.",
  );
});

test("orderByDaily2 puts the Daily 2 list in #1, #2, #3 order only when Daily 2 is picked", () => {
  const a = stratRow("A", { daily2: { rank: 3 } });
  const b = stratRow("B", { daily2: { rank: 1 } });
  const c = stratRow("C", { daily2: { rank: 2 } });
  assert.deepEqual(orderByDaily2([a, b, c], cfg({ setup: "daily2" })).map((r) => r.symbol), ["B", "C", "A"]);
  assert.deepEqual(orderByDaily2([a, b, c], cfg({ setup: "v2" })).map((r) => r.symbol), ["A", "B", "C"]);
});

test("strategyTags: V3 over V2, plus the Daily 2 number; nothing for a bare row", () => {
  const tags = (r) => strategyTags(r, Date.now(), { legacy: true }).map((t) => t.text);
  assert.deepEqual(tags(stratRow("A", { v2: true, v3: false, daily2: null })), ["V2"]);
  assert.deepEqual(tags(stratRow("B", { v2: true, v3: true, daily2: { rank: 2 } })), ["V3", "D2 #2"]);
  assert.deepEqual(tags(stratRow("C", { v2: false, v3: false, daily2: { rank: 1 } })), ["D2 #1"]);
  assert.deepEqual(strategyTags(stratRow("D", undefined)), []);
  assert.deepEqual(strategyTags(null), []);
});

// Strategy G (2026-09-23): his own method, with the option plan beside the tag.
const PLTR_G = {
  rank: 1, target: 195, entryStrike: 200, entryPrice: 0.68, roi: 163.2, gammaRoi: 130,
  status: "open", lastPrice: 0.68, returnPct: null,
};

test("Strategy G filters on the G day list and orders it #1, #2", () => {
  const a = stratRow("A", { v2: false, v3: false, daily2: null, g: { ...PLTR_G, rank: 2 } });
  const b = stratRow("B", { v2: true, v3: false, daily2: null, g: null });
  const c = stratRow("C", { v2: false, v3: false, daily2: null, g: PLTR_G });
  const config = cfg({ setup: "g" });
  assert.deepEqual(orderByDaily2(filterRows([a, b, c], config), config).map((r) => r.symbol), ["C", "A"]);
  assert.equal(strategyOf(config), "g");
  assert.match(describeFilters(config), /^Strategy G: your rules/);
});

test("gPlanText reads like his chain: target, ENTRY call, ROI, then the real result", () => {
  assert.equal(gPlanText(PLTR_G), "→195 · 200C $0.68 · ROI 163% · now $0.68 (0%)");
  assert.equal(gPlanText({ ...PLTR_G, status: "target", returnPct: 157.4 }), "→195 · 200C $0.68 · ROI 163% · target hit +157%");
  assert.equal(gPlanText({ ...PLTR_G, status: "stop", returnPct: -54.4 }), "→195 · 200C $0.68 · ROI 163% · stopped -50% (-54%)");
  assert.equal(gPlanText({ ...PLTR_G, status: "close", returnPct: 17.6 }), "→195 · 200C $0.68 · ROI 163% · sold at close +18%");
  assert.equal(gPlanText({ ...PLTR_G, target: 392.5, entryStrike: 397.5 }).slice(0, 12), "→392.5 · 397");
  assert.equal(gPlanText(null), "");
});

test("strategyTags adds G #n and the plan", () => {
  const tags = strategyTags(stratRow("PLTR", { v2: false, v3: false, daily2: null, g: PLTR_G }), Date.now(), { legacy: true }).map((t) => t.text);
  assert.deepEqual(tags, ["G #1", "→195 · 200C $0.68 · ROI 163% · now $0.68 (0%)"]);
});

// The chart's CALL2H / CALL4H arrows on the row (2026-09-24, FSLY 09-23 13:00).
function chartRow(symbol, signals) {
  const r = row({ symbol, grade: { letter: "A" }, m5: { state: "fading" } });
  r.chartSignals = signals;
  return r;
}
const FSLY_ARROWS = [
  { label: "CALL2H", family: "4x8", timeframe: "2H", at: "2026-09-23T13:00:00-04:00", seenAt: "2026-09-23T13:04:10-04:00" },
  { label: "CALL4H", family: "4x8", timeframe: "4H", at: "2026-09-23T13:00:00-04:00", seenAt: "2026-09-23T13:04:10-04:00" },
  { label: "CALL2H", family: "9x20", timeframe: "2H", at: "2026-09-23T13:00:00-04:00", seenAt: "2026-09-23T13:04:10-04:00" },
];

const AT = (hhmm) => Date.parse(`2026-09-23T${hhmm}:00-04:00`);

test("chartArrows: one per label, earliest first, in ET", () => {
  assert.deepEqual(chartArrows(chartRow("FSLY", FSLY_ARROWS), AT("13:30")).map((a) => a.label + " " + a.family + " " + a.clock), ["CALL2H 4x8 13:00", "CALL4H 4x8 13:00", "CALL2H 9x20 13:00"], "both families, yellow 4/8 first");
  assert.deepEqual(chartArrows(chartRow("X", undefined), AT("13:30")), []);
});

test("an arrow lives one candle of its timeframe: CALL2H 2 hours, CALL4H 4 hours", () => {
  // WRBY 2026-09-24: "CALL2H 09:15" was still on the board at 13:18.
  const fsly = chartRow("FSLY", FSLY_ARROWS);
  const labels = (now) => chartArrows(fsly, AT(now)).map((a) => a.label);
  assert.deepEqual(labels("14:59"), ["CALL2H", "CALL4H", "CALL2H"]);
  assert.deepEqual(labels("15:00"), ["CALL4H"], "CALL2H gone after 2 hours");
  assert.deepEqual(labels("16:59"), ["CALL4H"]);
  assert.deepEqual(labels("17:00"), [], "CALL4H gone after 4 hours");
});

test("the Chart CALL2H/CALL4H choice filters on live arrows and orders by the earliest", () => {
  const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
  const fsly = chartRow("FSLY", [{ label: "CALL4H", family: "4x8", timeframe: "4H", at: ago(30) }]);
  const meta = chartRow("META", [{ label: "CALL2H", family: "4x8", timeframe: "2H", at: ago(90) }]);
  const stale = chartRow("WRBY", [{ label: "CALL2H", family: "4x8", timeframe: "2H", at: ago(245) }]);
  const none = chartRow("NONE", []);
  const config = cfg({ setup: "chart" });
  assert.deepEqual(orderByDaily2(filterRows([fsly, none, stale, meta], config), config).map((r) => r.symbol), ["META", "FSLY"]);
  assert.equal(strategyOf(config), "chart");
});

test("strategyTags shows the chart arrows even with no strategy verdict", () => {
  assert.deepEqual(strategyTags(chartRow("FSLY", FSLY_ARROWS), AT("13:30")).map((t) => t.key), ["chart-CALL2H-4x8", "chart-CALL4H-4x8", "chart-CALL2H-9x20"]);
});
test("no '5m cross' tag any more (removed 2026-09-24 - too confusing on the live board)", () => {
  const noon = Date.parse("2026-09-23T12:00:00-04:00");
  const crwd = row({ symbol: "CRWD", m5: { state: "extended", crossUpAt: Date.parse("2026-09-23T10:50:00-04:00") / 1000 } });
  assert.deepEqual(strategyTags(crwd, noon), []);
});
test("adxCyan reads the chart's 5m ADX state: ADX > 25 and +DI > 25", () => {
  const r = (adx, plus, minus = 10) => row({ symbol: "CRWD", grade: { letter: "A+" }, adx: { "5m": { adx, plus, minus } } });
  const fresh = row({ symbol: "F", grade: { letter: "A" }, adx: { "5m": { adx: 26.1, prevAdx: 24.8, plus: 30, minus: 10 } } });
  const steady = row({ symbol: "S", grade: { letter: "A" }, adx: { "5m": { adx: 31, prevAdx: 29, plus: 30, minus: 10 } } });
  const noon = Date.parse("2026-09-23T12:00:00-04:00");
  assert.equal(strategyTags(fresh, noon)[0].fresh, true, "crossed up through 25 on the latest bar: flashes");
  assert.equal(strategyTags(steady, noon)[0].fresh, false, "already above: steady");
  const ungraded = row({ symbol: "X", adx: { "5m": { adx: 40, plus: 40, minus: 10 } } });
  assert.deepEqual(strategyTags(ungraded, Date.parse("2026-09-23T12:00:00-04:00")), [], "no letter: no ADX tag");
  assert.deepEqual(strategyTags(r(32.4, 30.1), Date.parse("2026-09-23T12:00:00-04:00")).map((t) => t.text), ["ADX 32 cyan"]);
  assert.deepEqual(strategyTags(r(32.4, 22), Date.parse("2026-09-23T12:00:00-04:00")), [], "+DI under 25: no cyan");
  assert.deepEqual(strategyTags(r(18, 30), Date.parse("2026-09-23T12:00:00-04:00")), [], "ADX under the yellow line");
  assert.deepEqual(strategyTags(r(null, 30), Date.parse("2026-09-23T12:00:00-04:00")), [], "a missing reading is not zero");
});
// The META / MRNA picture of 2026-09-24 vs PLTR / INTC (same A+ letter, losers).
const optRow = (over = {}) => row({
  symbol: "META",
  grade: { letter: "A+" },
  skittles: { "2h": { bg: "dark_green" }, "4h": { bg: "lime" } },
  adx: { "30m": { plus: 34, minus: 18 } },
  m5: { state: "extended" },
  ...over,
});
test("optionsSetup: META at 09:30 passes, PLTR at 09:30 does not", () => {
  assert.equal(optionsSetup(optRow()), true);
  const pltr = optRow({ symbol: "PLTR", skittles: { "2h": { bg: "black" }, "4h": { bg: "black" } }, adx: { "30m": { plus: 18.5, minus: 25.7 } }, m5: { state: "holding" } });
  assert.equal(optionsSetup(pltr), false);
});
test("optionsSetup needs every piece", () => {
  assert.equal(optionsSetup(optRow({ grade: { letter: "B" } })), false, "B letter");
  assert.equal(optionsSetup(optRow({ skittles: { "2h": { bg: "cyan" }, "4h": { bg: "black" } } })), false, "only 2h cross");
  assert.equal(optionsSetup(optRow({ adx: { "30m": { plus: 15, minus: 20 } } })), false, "30m sellers in control");
  assert.equal(optionsSetup(optRow({ adx: { "30m": { plus: null, minus: 20 } } })), false, "missing reading is not zero");
  assert.equal(optionsSetup(optRow({ m5: { state: "holding" } })), false, "not extended");
});
test("Options setup: tag first in the Setup cell, and the filter shows only those rows", () => {
  resetOptLatch();
  const at940 = Date.parse("2026-09-24T09:40:00-04:00");
  const tags = strategyTags(optRow(), at940);
  assert.equal(tags[0].key, "bolt"); // MomoX ⚡ first, then the OPT tag
  const tag = tags[1];
  assert.equal(tag.key, "opt");
  assert.equal(tag.text, "OPT 09:40");
  const config = cfg({ setup: "opt" });
  assert.equal(strategyOf(config), "opt");
  assert.deepEqual(filterRows([optRow(), optRow({ symbol: "PLTR", m5: { state: "holding" } })], config, at940).map((r) => r.symbol), ["META"]);
});
test("OPT counts only when it first shows before 10:00 ET, then stays for the day", () => {
  resetOptLatch();
  const at = (hm) => Date.parse("2026-09-24T" + hm + ":00-04:00");
  assert.equal(earlyOpt(optRow({ symbol: "LATE" }), at("10:15")), null, "first seen after 10:00: never");
  assert.equal(earlyOpt(optRow({ symbol: "EARLY" }), at("09:45")).clock, "09:45");
  const cooled = optRow({ symbol: "EARLY", m5: { state: "holding" } });
  assert.equal(earlyOpt(cooled, at("11:30")).clock, "09:45", "a 09:45 OPT is still listed at 11:30");
  assert.equal(earlyOpt(cooled, Date.parse("2026-09-25T09:45:00-04:00")), null, "next day starts clean");
  assert.equal(earlyOpt(optRow({ symbol: "PRE" }), at("09:10")), null, "premarket does not count");
});

// META 2026-09-21: gapped +2.2%, cleared everything on the 09:40 candle (closes 09:45).
const GO_AT = Date.parse("2026-09-21T09:40:00-04:00") / 1000;
const goRow = (over = {}) => row({
  symbol: "META",
  grade: { letter: "A+" },
  adx: { "30m": { plus: 30, minus: 20 } },
  m5: { state: "holding", gapGo: { gap: 2.2, open: 680.01, orHigh: 700, goAt: GO_AT } },
  ...over,
});
test("gapAndGo: META Sep 21 lights GO 09:45 (the candle's close)", () => {
  const at = Date.parse("2026-09-21T10:30:00-04:00");
  assert.equal(gapAndGo(goRow(), at).clock, "09:45");
  assert.deepEqual(strategyTags(goRow(), at).map((t) => t.text), ["⚡", "GO 09:45"]); // the MomoX bolt leads a fired setup
});
test("gapAndGo needs the letter, 30m buyers, and today", () => {
  const at = Date.parse("2026-09-21T10:30:00-04:00");
  assert.equal(gapAndGo(goRow({ grade: { letter: "B" } }), at), null);
  assert.equal(gapAndGo(goRow({ adx: { "30m": { plus: 15, minus: 20 } } }), at), null);
  assert.equal(gapAndGo(goRow({ adx: { "30m": { plus: null, minus: 20 } } }), at), null);
  assert.equal(gapAndGo(goRow({ m5: { gapGo: { gap: 1, goAt: null } } }), at), null);
  assert.equal(gapAndGo(goRow(), Date.parse("2026-09-22T10:30:00-04:00")), null, "yesterday's GO is gone");
});
test("the Gap and go filter", () => {
  const config = cfg({ setup: "go" });
  assert.equal(strategyOf(config), "go");
  const now = Date.now();
  const today = { gap: 3, goAt: Math.floor(now / 1000) - 60 };
  const rows = [goRow({ m5: { gapGo: today } }), goRow({ symbol: "PLTR", m5: { gapGo: { gap: 0.5, goAt: null } } })];
  assert.deepEqual(filterRows(rows, config).map((r) => r.symbol), ["META"]);
});

test("Best setups = GO or OPT; V2/G tags hidden unless asked for", () => {
  const config = cfg({ setup: "best" });
  assert.equal(strategyOf(config), "best");
  resetOptLatch();
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const go = goRow({ symbol: "META" });
  const opt = optRow({ symbol: "MRNA" });
  const none = optRow({ symbol: "PLTR", m5: { state: "holding" } });
  assert.deepEqual(filterRows([opt, none, go], config, now).map((r) => r.symbol), ["MRNA", "META"]);
  const g = stratRow("PLTR", { v2: true, v3: false, daily2: null, g: null });
  assert.deepEqual(strategyTags(g).map((t) => t.key), ["v2"], "V2 tag shown again (2026-09-25)");
  assert.deepEqual(strategyTags(g, Date.now(), { legacy: false }).map((t) => t.key), [], "and can be left out");
  assert.ok(LEGACY_STRATEGY_VALUES.includes("v2") && !LEGACY_STRATEGY_VALUES.includes("best"));
});

test("Best setups lists GO first, then OPT, each by time", () => {
  resetOptLatch();
  const config = cfg({ setup: "best" });
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const opt = optRow({ symbol: "MRNA" });
  const go = goRow({ symbol: "META" });
  const rows = filterRows([opt, go], config, now);
  assert.deepEqual(orderByDaily2(rows, config, now).map((r) => r.symbol), ["META", "MRNA"]);
});

test("NEWS tag: the AI news read, arrow by direction, nothing for UNKNOWN", () => {
  const at = "2026-09-24T09:41:00-04:00";
  const r = (catalyst) => row({ symbol: "XYZ", catalyst });
  const up = newsCatalyst(r({ category: "EARNINGS", direction: "bullish", confidence: "high", summary: "Beat.", provider: "claude", at }));
  assert.equal(up.text, "NEWS Earnings ↑");
  assert.equal(up.key, "news-bullish");
  assert.match(up.title, /Read by Claude at 09:41 ET/);
  const down = newsCatalyst(r({ category: "OFFERING", direction: "bearish", provider: "openai", at }));
  assert.equal(down.text, "NEWS Offering ↓");
  assert.match(down.title, /Read by Gemini/);
  assert.equal(newsCatalyst(r({ category: "MACRO", direction: "weird" })).key, "news-neutral");
  assert.equal(newsCatalyst(r({ category: "UNKNOWN", direction: "neutral" })), null);
  assert.equal(newsCatalyst(r(null)), null);
  assert.ok(strategyTags(r({ category: "FDA", direction: "bullish" }), Date.parse(at)).some((t) => t.key === "news-bullish"));
});

test("News + momentum: positive news alone is not enough - the stock must be pushing and graded", () => {
  const good = { category: "EARNINGS", direction: "bullish", confidence: "high" };
  const base = (over) => row({ symbol: "XYZ", catalyst: good, m5: { state: "building" }, grade: { letter: "A+" }, ...over });
  assert.equal(newsMomentum(base({})), true);
  assert.equal(newsMomentum(base({ m5: { state: "holding" } })), false, "news but no push: no trade");
  assert.equal(newsMomentum(base({ m5: { state: "fading" } })), false);
  assert.equal(newsMomentum(base({ grade: { letter: "B" } })), false, "no A/A+ yet");
  assert.equal(newsMomentum(base({ catalyst: { ...good, direction: "bearish" } })), false);
  assert.equal(newsMomentum(base({ catalyst: { ...good, confidence: "low" } })), false);
  assert.equal(newsMomentum(base({ catalyst: { ...good, category: "UNKNOWN" } })), false);
  const config = cfg({ setup: "newsMomo" });
  assert.deepEqual(filterRows([base({}), base({ symbol: "NOPUSH", m5: { state: "holding" } })], config).map((r) => r.symbol), ["XYZ"]);
});

test("Best setups in hot sectors: GO/OPT AND a hot sector", () => {
  resetOptLatch();
  const config = cfg({ setup: "bestHot" });
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const hot = { hot: true, rs3: 2.5, up: 8, total: 9 };
  const goHot = goRow({ symbol: "RKLB", sectorRotation: hot });
  const goCold = goRow({ symbol: "META", sectorRotation: { hot: false } });
  const hotNoSetup = row({ symbol: "LUNR", sectorRotation: hot });
  assert.deepEqual(filterRows([goCold, hotNoSetup, goHot], config, now).map((r) => r.symbol), ["RKLB"]);
  assert.equal(inHotSector(goHot), true);
  assert.equal(inHotSector(goCold), false);
});

test("SOLO: tag with the volume multiple, filter shows only SOLO, heaviest first", () => {
  const s = (symbol, ratio) => row({ symbol, solo: { at: "2026-09-24T09:50:00-04:00", ratio, price: 10 } });
  const tag = strategyTags(s("TSSI", 9.81), Date.parse("2026-09-24T10:00:00-04:00")).find((t) => t.key === "solo");
  assert.equal(tag.text, "SOLO 9.8× vol");
  assert.match(tag.title, /by 09:50 ET/);
  const config = cfg({ setup: "solo" });
  const rows = filterRows([s("A", 6), row({ symbol: "NONE" }), s("B", 12)], config);
  assert.deepEqual(orderByDaily2(rows, config).map((r) => r.symbol), ["B", "A"]);
});

test("Hot sector leaders: 🔥#1 tag and filter, #1 before #2", () => {
  const h = (symbol, rank) => row({ symbol, hotLeader: { rank, sector: "Quantum", at: "2026-09-24T09:50:00-04:00", pct: 3 } });
  const tag = strategyTags(h("IONQ", 1), Date.parse("2026-09-24T10:00:00-04:00")).find((t) => t.key === "hotlead");
  assert.equal(tag.text, "🔥#1 Quantum");
  assert.match(tag.title, /lit at 09:50 ET/);
  const config = cfg({ setup: "hotLead" });
  const rows = filterRows([h("QBTS", 2), row({ symbol: "X" }), h("IONQ", 1)], config);
  assert.deepEqual(orderByDaily2(rows, config).map((r) => r.symbol), ["IONQ", "QBTS"]);
});

// --------------------------------------------------------------------------
// BEAR rows (spec 2026-09-24): the row's direction flips the rules
// --------------------------------------------------------------------------

import { isBearRow, fiveMinuteCross, adxCyan, strategyRules, STRATEGY_RULES } from "./momxFilters.js";

test("bear OPT reads the bearish cross block, sellers on 30m and extended down", () => {
  const bear = { direction: "bear", grade: { letter: "A+" }, skittles: { "2h": { bg: "magenta" }, "4h": { bg: "red" } },
    adx: { "30m": { plus: 10, minus: 30 } }, m5: { state: "extended" } };
  assert.equal(isBearRow(bear), true);
  assert.equal(optionsSetup(bear), true);
  assert.equal(optionsSetup({ ...bear, direction: "bull" }), false);
  assert.equal(optionsSetup({ ...bear, adx: { "30m": { plus: 30, minus: 10 } } }), false);
  assert.equal(optionsSetup({ ...bear, skittles: { "2h": { bg: "cyan" }, "4h": { bg: "red" } } }), false);
});

test("bear GO wants sellers on 30m and reports the negative gap", () => {
  const t = Math.floor(Date.now() / 1000) - 600;
  const row = { direction: "bear", grade: { letter: "A" }, m5: { gapGo: { gap: -2.5, goAt: t } }, adx: { "30m": { plus: 5, minus: 20 } } };
  const go = gapAndGo(row);
  assert.ok(go && go.gap === -2.5);
  assert.equal(gapAndGo({ ...row, adx: { "30m": { plus: 20, minus: 5 } } }), null);
  assert.equal(gapAndGo({ ...row, direction: "bull" }), null);
});

test("bearish-only RVOL filter on a bear row keeps the selling cells", () => {
  const config = coerceFilters(DEFAULT_MOMX_FILTERS);          // 1h on at 2.5, bullishOnly true
  const rowWith = (bg, direction = "bear") => ({ direction, rvol: { "1h": { value: 3, bg } } });
  assert.equal(rvolGroupResult(rowWith("magenta"), config), true);
  assert.equal(rvolGroupResult(rowWith("cyan"), config), false);
  assert.equal(rvolGroupResult(rowWith("cyan", "bull"), config), true);
  assert.equal(rvolGroupResult(rowWith("magenta", "bull"), config), false);
});

test("bear tags: OPT latch is separate, gPlanText prints P, 5m cross reads crossDownAt, ADX reads -DI", () => {
  resetOptLatch();
  assert.ok(gPlanText({ target: 185, entryStrike: 180, entryPrice: 1.05, roi: 152, side: "P" }).includes("180P"));
  assert.ok(gPlanText({ target: 195, entryStrike: 200, entryPrice: 0.68, roi: 163 }).includes("200C"));
  const sec = Math.floor(Date.now() / 1000) - 60;
  assert.ok(fiveMinuteCross({ direction: "bear", m5: { crossDownAt: sec, crossUpAt: null } }));
  assert.equal(fiveMinuteCross({ direction: "bull", m5: { crossDownAt: sec, crossUpAt: null } }), null);
  const adx = adxCyan({ direction: "bear", adx: { "5m": { adx: 30, plus: 5, minus: 28, prevAdx: 24 } } });
  assert.deepEqual(adx, { adx: 30, plus: 28, fresh: true, side: "minus" });
  assert.equal(adxCyan({ direction: "bull", adx: { "5m": { adx: 30, plus: 5, minus: 28, prevAdx: 24 } } }), null);
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const bearOpt = { symbol: "XYZ", direction: "bear", grade: { letter: "A+" }, skittles: { "2h": { bg: "magenta" }, "4h": { bg: "red" } },
    adx: { "30m": { plus: 10, minus: 30 } }, m5: { state: "extended" } };
  assert.ok(earlyOpt(bearOpt, now));
  const tags = strategyTags(bearOpt, now).map((t) => t.key);
  assert.ok(tags.includes("opt"));
});

test("strategyRules(bear) flips the wording, bull is untouched", () => {
  assert.equal(strategyRules("bull"), STRATEGY_RULES);
  const bear = strategyRules("bear");
  assert.match(bear.go, /gapped down/i);
  assert.match(bear.opt, /cross down|below/i);
  assert.match(bear.g, /put/i);
  assert.match(bear.chart, /PUT2H/);
  assert.equal(bear.hotLead, STRATEGY_RULES.hotLead);
});

test("Best setups are RANKED: GO in a hot/moving sector, then GO, then OPT; 2+ warnings sink; ⚠ tag", () => {
  resetOptLatch();
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const goHot = goRow({ symbol: "HOTGO", sectorRotation: { hot: true } });
  const goPlain = goRow({ symbol: "GO1" });
  const goWarned = goRow({ symbol: "GOBAD", pctChange: 12, m5: { state: "fading", gapGo: { gap: 3, goAt: GO_AT } } });
  const opt = optRow({ symbol: "OPT1" });
  const config = cfg({ setup: "best" });
  const rows = filterRows([opt, goWarned, goPlain, goHot], config, now);
  assert.deepEqual(orderByDaily2(rows, config, now).map((r) => r.symbol), ["HOTGO", "GO1", "GOBAD", "OPT1"]);
  assert.deepEqual(setupWarnings(goWarned), ["up 12%", "fading"]);
  const warn = strategyTags(goWarned, now).find((t) => t.key === "warn");
  assert.equal(warn.text, "⚠ up 12% · fading");
  assert.equal(strategyTags(row({ symbol: "PLAIN", pctChange: 12 }), now).find((t) => t.key === "warn"), undefined, "no setup, no warning");
});

test("⭐ ranks: across the whole board, tagged on the row, no filter needed", () => {
  resetOptLatch();
  const now = Date.parse("2026-09-21T09:50:00-04:00");
  const ranks = bestSetupRanks([optRow({ symbol: "OPT1" }), goRow({ symbol: "GO1" }), row({ symbol: "NONE" })], now);
  assert.deepEqual([...ranks.entries()], [["GO1", 1], ["OPT1", 2]]);
  const tag = strategyTags(row({ symbol: "GO1", bestRank: 1 }), now).find((t) => t.key === "rank");
  assert.equal(tag.text, "⭐1");
});

test("⭐ ranks: a clean GO beats an earlier GO carrying one ⚠", () => {
  resetOptLatch();
  const now = Date.parse("2026-09-21T10:30:00-04:00");
  const early = goRow({ symbol: "HOT9", pctChange: 9.2 });
  const clean = goRow({ symbol: "CLEAN", pctChange: 3, m5: { state: "building", gapGo: { gap: 2.5, goAt: GO_AT + 600 } } });
  assert.deepEqual([...bestSetupRanks([early, clean], now).entries()], [["CLEAN", 1], ["HOT9", 2]]);
});

test("new setups switched off: Setup cell as on 2026-09-24 morning, saved Best setups reads Any", () => {
  resetOptLatch();
  setMomxNewSetups(false);
  try {
    const at = Date.parse("2026-09-21T10:30:00-04:00");
    const texts = strategyTags(goRow({ bestRank: 1, solo: { ratio: 7, at: "2026-09-21T09:50:00-04:00" } }), at).map((t) => t.text);
    assert.equal(texts.some((t) => /^(⭐|GO|OPT|SOLO|⚠)/.test(t)), false);
    assert.equal(strategyOf({ setup: "best" }), null);
    assert.equal(bestSetupRanks([goRow()], at).size, 0);
  } finally {
    setMomxNewSetups(true);
  }
});

test("PYPL 2026-09-25: SQZ 2h fire and the short C4H show in Setup; the Chart filter ignores C2H / C4H", () => {
  const now = Date.parse("2026-09-25T10:45:00-04:00");
  const row = {
    symbol: "PYPL",
    grade: { letter: "B" },
    gradeFresh: { timeline: [
      { at: "2026-09-25T08:10:00-04:00", what: "SQZ 4h released" },            // premarket: not shown
      { at: "2026-09-25T10:07:16-04:00", what: "SQZ 2h released" },
      { at: "2026-09-25T10:19:21-04:00", what: "SQZ 2h released" },            // repeat: first one wins
      { at: "2026-09-24T11:00:00-04:00", what: "SQZ 4h released" },            // yesterday
    ] },
    chartSignals: [{ label: "C4H", family: "9x20", timeframe: "4H", at: "2026-09-25T10:10:00-04:00", compact: true }],
  };
  assert.deepEqual(sqzFires(row, now).map((f) => f.tf + " " + f.clock), ["2h 10:07"]);
  const texts = strategyTags(row, now).map((t) => t.text);
  assert.ok(texts.includes("SQZ 2h 10:07"));
  assert.ok(texts.includes("C4H 10:10"));
  assert.equal(rowPasses(row, { ...DEFAULT_MOMX_FILTERS, setup: "chart" }, now), false);
  assert.equal(rowPasses({ ...row, chartSignals: [{ label: "CALL4H", family: "9x20", timeframe: "4H", at: "2026-09-25T10:10:00-04:00" }] }, { ...DEFAULT_MOMX_FILTERS, setup: "chart" }, now), true);
});

test("fresh tags flash for 10 minutes from when the scanner first saw them", () => {
  const row = {
    symbol: "QBTS",
    chartSignals: [{ label: "C4H", family: "9x20", timeframe: "4H", at: "2026-09-25T11:05:00-04:00", seenAt: "2026-09-25T11:13:00-04:00", compact: true }],
  };
  const tag = (now) => strategyTags(row, Date.parse(now)).find((t) => t.text === "C4H 11:05");
  assert.equal(tag("2026-09-25T11:15:00-04:00").fresh, true);   // 2 min after it was seen
  assert.equal(Boolean(tag("2026-09-25T11:24:00-04:00").fresh), false); // 11 min after
});

test("30m sellers warning and the market-turn tag", () => {
  const now = Date.parse("2026-09-25T11:12:00-04:00");
  const turnRow = { symbol: "BE", pctChange: 6, last: 288, m5: { vwap: 281 }, marketTurn: { rank: 2, at: "2026-09-25T11:08:00-04:00", price: 288 },
    adx: { "30m": { plus: 27, minus: 15, adx: 43 } } };
  const tags = strategyTags(turnRow, now);
  const turn = tags.find((t) => t.key === "turn");
  assert.equal(turn.text, "TURN #2 11:08");
  assert.equal(turn.fresh, true);
  assert.deepEqual(setupWarnings({ symbol: "ARM", pctChange: 3, adx: { "30m": { plus: 12.6, minus: 27.3, adx: 21 } } }), ["30m sellers"]);
  assert.deepEqual(setupWarnings({ symbol: "ARM", direction: "bear", pctChange: 3, adx: { "30m": { plus: 12.6, minus: 27.3, adx: 21 } } }), []);
});

test("MomoX bolt: leads the Setup cell when a setup fired today, flashes while fresh", () => {
  const row = { symbol: "M", marketTurn: { rank: 1, at: "2026-09-25T11:05:00-04:00", price: 22 } };
  const fresh = strategyTags(row, Date.parse("2026-09-25T11:08:00-04:00"));
  assert.equal(fresh[0].text, "⚡");
  assert.equal(fresh[0].fresh, true);
  const later = strategyTags(row, Date.parse("2026-09-25T13:00:00-04:00"));
  assert.equal(later[0].text, "⚡");
  assert.equal(Boolean(later[0].fresh), false);
  assert.equal(strategyTags({ symbol: "X" }, Date.now()).some((t) => t.key === "bolt"), false);
});

test("MomoX A+ tag: text, weeklies mark, fresh flash and the setup bolt", () => {
  const now = Date.parse("2026-09-21T09:40:00-04:00");
  const row = { symbol: "META", momoxAPlus: { at: "2026-09-21T09:35:00-04:00", price: 699.74, sqz: ["4h", "Wk"], weeklies: true } };
  const tags = strategyTags(row, now);
  const tag = tags.find((t) => t.key === "mxaplus");
  assert.equal(tag.text, "MX A+ 09:35 W");
  assert.equal(tag.fresh, true);
  assert.equal(tags[0].key, "bolt");
});

test("MomoX A+ tag names the High OI call wall in its hover text", () => {
  const now = Date.parse("2026-09-21T09:40:00-04:00");
  const row = { symbol: "META", momoxAPlus: { at: "2026-09-21T09:35:00-04:00", price: 749, sqz: ["Wk"], weeklies: true, oiStrike: 750, oi: 16446 } };
  const tag = strategyTags(row, now).find((t) => t.key === "mxaplus");
  assert.match(tag.title, /High OI call wall 750C \(16,446 OI\)/);
});
