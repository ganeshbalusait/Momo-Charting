// Coverage additions (2026-11-04) for momxFilters.js:
//  * the remembered FILTERS config survives junk, old shapes and blocked
//    storage (readStoredFilters / writeStoredFilters), and
//  * freshAlert - the 10-minute window that flashes the ticker and pops a
//    card - only for the setups he trades from since 2026-09-28 (GO+, GO,
//    MX A+), never for SOLO / OPT / TURN / hot leader any more.
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_MOMX_FILTERS,
  FRESH_TAG_MS,
  MOMX_FILTERS_STORAGE_KEY,
  MOMX_FILTER_VERSION,
  freshAlert,
  readStoredFilters,
  strategyTags,
  writeStoredFilters,
} from "./momxFilters.js";

const clone = (value) => JSON.parse(JSON.stringify(value));

function withStorage(storage, fn) {
  const previous = globalThis.window;
  globalThis.window = { localStorage: storage };
  try {
    return fn();
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
}

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
  };
}

const throwing = {
  getItem() { throw new Error("SecurityError: storage blocked"); },
  setItem() { throw new Error("QuotaExceededError"); },
};

test("a fresh browser, garbage JSON or blocked storage all open on the default filters", () => {
  for (const storage of [memoryStorage(), memoryStorage({ [MOMX_FILTERS_STORAGE_KEY]: "{not json" }),
    memoryStorage({ [MOMX_FILTERS_STORAGE_KEY]: "42" }), memoryStorage({ [MOMX_FILTERS_STORAGE_KEY]: "null" }), throwing]) {
    assert.deepEqual(withStorage(storage, readStoredFilters), clone(DEFAULT_MOMX_FILTERS));
  }
});

test("his tuning round-trips through storage exactly", () => {
  const storage = memoryStorage();
  const tuned = clone(DEFAULT_MOMX_FILTERS);
  tuned.mode = "all";
  tuned.rvol.timeframes["1h"].min = 3.5;
  tuned.sqz.timeframes.D = true;
  tuned.skittles.on = true;
  withStorage(storage, () => writeStoredFilters(tuned));
  const back = withStorage(storage, readStoredFilters);
  assert.equal(back.mode, "all");
  assert.equal(back.rvol.timeframes["1h"].min, 3.5);
  assert.equal(back.sqz.timeframes.D, true);
  assert.equal(back.skittles.on, true);
  assert.equal(back.version, MOMX_FILTER_VERSION);
});

test("a v3 document from before the grade gates migrates without losing his settings", () => {
  const v3 = { version: 3, mode: "all", rvol: { on: true, timeframes: { "2h": { on: false, min: 4 } } } };
  const storage = memoryStorage({ [MOMX_FILTERS_STORAGE_KEY]: JSON.stringify(v3) });
  const back = withStorage(storage, readStoredFilters);
  assert.equal(back.version, MOMX_FILTER_VERSION);
  assert.equal(back.mode, "all");
  assert.deepEqual(back.rvol.timeframes["2h"], { on: false, min: 4 });
  // Timeframes the old document never knew come from the defaults, not blank.
  assert.deepEqual(back.rvol.timeframes["4h"], DEFAULT_MOMX_FILTERS.rvol.timeframes["4h"]);
  assert.equal(back.setup, "any");
  assert.deepEqual(back.setupRules, []);
});

test("a stored ALL spelled wrong widens to ANY, and unknown setup rules are dropped", () => {
  const saved = { ...clone(DEFAULT_MOMX_FILTERS), mode: "ALL", setupRules: ["not-a-rule", 7] };
  const storage = memoryStorage({ [MOMX_FILTERS_STORAGE_KEY]: JSON.stringify(saved) });
  const back = withStorage(storage, readStoredFilters);
  assert.equal(back.mode, "any");
  assert.deepEqual(back.setupRules, []);
});

test("a storage that refuses writes never throws into the board", () => {
  assert.doesNotThrow(() => withStorage(throwing, () => writeStoredFilters(clone(DEFAULT_MOMX_FILTERS))));
});

// ---------------------------------------------------------------- freshAlert

const NOW = Date.parse("2026-11-03T15:00:00Z");     // 10:00 ET
const minutesAgo = (minutes) => new Date(NOW - minutes * 60_000).toISOString();
const mxRow = (at, extra = {}) => ({ symbol: "NVDA", pctChange: 2, momoxAPlus: { at, weeklies: true, sqz: ["4h"] }, ...extra });

test("an MX A+ under 10 minutes old flashes the ticker, with the time it fired", () => {
  const hit = freshAlert(mxRow(minutesAgo(3)), NOW);
  assert.ok(hit, "a 3-minute-old MX A+ is a fresh alert");
  assert.equal(hit.key, "mxaplus");
  assert.equal(hit.atMs, NOW - 3 * 60_000);
  assert.match(hit.text, /^MX A\+ \d{1,2}:\d{2}/);
  // The Setup cell's bolt flashes with it.
  const [bolt] = strategyTags(mxRow(minutesAgo(3)), NOW);
  assert.deepEqual([bolt.key, bolt.fresh], ["bolt", true]);
});

test("at 10 minutes the alert is over but the bolt stays, no longer flashing", () => {
  assert.equal(FRESH_TAG_MS, 10 * 60_000);
  assert.equal(freshAlert(mxRow(minutesAgo(10)), NOW), null);
  assert.equal(freshAlert(mxRow(minutesAgo(9.9)), NOW).key, "mxaplus");
  const [bolt] = strategyTags(mxRow(minutesAgo(45)), NOW);
  assert.deepEqual([bolt.key, bolt.fresh], ["bolt", false]);
});

test("a stamp from the future (clock skew) does not flash", () => {
  assert.equal(freshAlert(mxRow(new Date(NOW + 60_000).toISOString()), NOW), null);
});

test("SOLO, a market turn and a hot leader no longer pop a card, however fresh", () => {
  const recent = minutesAgo(2);
  const row = {
    symbol: "AAPL", pctChange: 3,
    solo: { ratio: 12.5, at: recent },
    marketTurn: { rank: 1, at: recent },
    hotLeader: { rank: 1, at: recent, sector: "Semis" },
  };
  assert.equal(freshAlert(row, NOW), null);
});

test("junk rows never alert and never throw", () => {
  for (const row of [null, undefined, {}, { symbol: "X", momoxAPlus: {} }, { symbol: "X", momoxAPlus: { at: "not a time" } }]) {
    assert.equal(freshAlert(row, NOW), null);
  }
});
