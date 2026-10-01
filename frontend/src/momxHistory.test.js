// MomX scanner history helpers - the JS test list pinned by
// docs/superpowers/specs/2026-08-31-momx-scanner-history-design.md section 5:
//
// - day-nav prev/next resolution, including at both ends of the archive
// - search switches to cross-day mode and back
// - changed-cell highlighting maps `changed` entries to the right columns
// - arrival rows render sparkline/quote cells, change rows blank them
// - column parity: the history table's columns are derived from the same
//   COLUMNS array the live board uses
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  MOMX_HISTORY_ENDPOINT,
  annotateChanges,
  MOMX_HISTORY_TIME_COLUMN,
  changedColumnKeys,
  dayLabel,
  etDayIso,
  flattenHistoryRows,
  groupRowsByDay,
  historyColumns,
  historyRequest,
  liveColumns,
  MOMX_LIVE_TIME_COLUMN,
  resolveDayNav,
  snapshotTimeLabel,
} from "./momxHistory.js";

// ---- column parity (structural, against the panel's REAL source) ----------
//
// node --test cannot import MomxScannerPanel.jsx (JSX loader), so the live
// MOMX_COLUMNS array is lifted OUT of the panel source and evaluated - the
// same source-lifting pattern premarketGapWindow.test.js uses on App.jsx.
// This is the test the spec demands: history columns are DERIVED from the
// live board's array, so the two tables cannot drift apart.

const panelSource = readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");

function liftLiveColumns() {
  const match = /const MOMX_COLUMNS = \[[\s\S]*?\n\];/.exec(panelSource);
  assert.ok(match, "could not lift MOMX_COLUMNS out of MomxScannerPanel.jsx");
  // The array literal references two formatters; stubs are enough because the
  // parity assertions read keys/labels/kinds, never formatted values.
  const factory = new Function(
    "formatRvol",
    "formatSkittles",
    match[0] + "\nreturn MOMX_COLUMNS;",
  );
  return factory(
    () => "",
    () => "",
  );
}

test("column parity: history columns are the live COLUMNS plus Time after Symbol", () => {
  const live = liftLiveColumns();
  assert.ok(live.length > 10, "lifted a suspiciously small column set");
  const history = historyColumns(live);

  // Exactly one column was added, and it is the Time column.
  assert.equal(history.length, live.length + 1);
  const extras = history.filter((column) => !live.includes(column));
  assert.equal(extras.length, 1);
  assert.equal(extras[0], MOMX_HISTORY_TIME_COLUMN);
  assert.equal(extras[0].label, "Time");

  // Time sits DIRECTLY after Symbol - the spec's placement.
  const symbolIndex = history.findIndex((column) => column.key === "symbol");
  assert.ok(symbolIndex >= 0, "history lost the symbol column");
  assert.equal(history[symbolIndex + 1].key, "time");

  // Every live column survives, by IDENTITY and in the live board's order -
  // not a copy that could be edited independently.
  const historyWithoutTime = history.filter((column) => column.key !== "time");
  assert.equal(historyWithoutTime.length, live.length);
  live.forEach((column, index) => {
    assert.equal(historyWithoutTime[index], column, "column order drifted at " + column.key);
  });

  // Every changed-cell name the backend can emit maps onto a real column key.
  const keys = new Set(history.map((column) => column.key));
  const mapped = changedColumnKeys([
    "rvol.5m",
    "rvol.1h",
    "sqz.Wk",
    "skittles.2D",
    "highLow",
    "color",
    "badge.on",
    "news.headline",
    "scanReasons",
  ]);
  for (const key of mapped) {
    assert.ok(keys.has(key), "changed-cell map points at a column that does not exist: " + key);
  }
});

test("live columns: default order is Symbol | Time | Setup | Fresh", () => {
  const live = liftLiveColumns();
  const keys = liveColumns(live).map((column) => column.key);
  const at = keys.indexOf("symbol");
  // 2026-09-22 "frozen column symbol + setup + time": Time moved in front of
  // Fresh so the three pinned columns sit together.
  assert.deepEqual(keys.slice(at, at + 4), ["symbol", "matchedSince", "setup", "fresh"]);
  // Exactly one Time column, and it is the live one.
  assert.equal(keys.filter((key) => key === "matchedSince" || key === "time").length, 1);
  // History keeps Time directly after Symbol.
  const history = historyColumns(live).map((column) => column.key);
  assert.equal(history[history.indexOf("symbol") + 1], "time");
});

test("live columns: Time falls back to after Symbol when there is no Setup column", () => {
  const withFresh = liveColumns([{ key: "symbol" }, { key: "fresh" }, { key: "pctChange" }]);
  assert.deepEqual(withFresh.map((column) => column.key), ["symbol", "matchedSince", "fresh", "pctChange"]);
  const columns = liveColumns([{ key: "symbol" }, { key: "pctChange" }]);
  assert.deepEqual(columns.map((column) => column.key), ["symbol", "matchedSince", "pctChange"]);
  assert.equal(columns[1], MOMX_LIVE_TIME_COLUMN);
});

test("historyColumns tolerates a malformed columns array", () => {
  // No symbol column: Time still exists (leading), nothing throws.
  const columns = historyColumns([{ key: "pctChange" }]);
  assert.equal(columns[0].key, "time");
  assert.equal(historyColumns(null).length, 1);
});

// ---- day-nav resolution ----------------------------------------------------

const DAYS = ["2026-08-31", "2026-08-28", "2026-08-27"]; // newest first, as the API sends

test("day nav resolves prev/next in the middle of the archive", () => {
  const nav = resolveDayNav(DAYS, "2026-08-28");
  assert.equal(nav.date, "2026-08-28");
  assert.equal(nav.olderDate, "2026-08-27");
  assert.equal(nav.newerDate, "2026-08-31");
  assert.equal(nav.canOlder, true);
  assert.equal(nav.canNewer, true);
  assert.equal(nav.label, "Fri 28 Aug 2026");
});

test("day nav disables the newer arrow on the newest day and the older arrow on the oldest", () => {
  const newest = resolveDayNav(DAYS, "2026-08-31");
  assert.equal(newest.canNewer, false);
  assert.equal(newest.newerDate, null);
  assert.equal(newest.canOlder, true);
  assert.equal(newest.olderDate, "2026-08-28");

  const oldest = resolveDayNav(DAYS, "2026-08-27");
  assert.equal(oldest.canOlder, false);
  assert.equal(oldest.olderDate, null);
  assert.equal(oldest.canNewer, true);
  assert.equal(oldest.newerDate, "2026-08-28");
});

test("day nav falls back to the newest day when the selection is unknown or null", () => {
  assert.equal(resolveDayNav(DAYS, null).date, "2026-08-31");
  assert.equal(resolveDayNav(DAYS, "2026-01-01").date, "2026-08-31");
  // An unsorted or duplicated days list is repaired, not trusted.
  const nav = resolveDayNav(["2026-08-27", "2026-08-31", "2026-08-31"], null);
  assert.deepEqual(nav.days, ["2026-08-31", "2026-08-27"]);
});

test("day nav with an empty archive disables everything and names no day", () => {
  const nav = resolveDayNav([], null);
  assert.equal(nav.date, null);
  assert.equal(nav.canOlder, false);
  assert.equal(nav.canNewer, false);
  assert.equal(nav.label, "");
  // Garbage in the days list is dropped, never rendered as a day.
  assert.equal(resolveDayNav(["not-a-date", 42, null], null).date, null);
});

test("dayLabel formats the ISO date without timezone drift", () => {
  // Built from UTC parts by hand: a local-zone formatter west of Greenwich
  // would print Sun 30 for this Monday.
  assert.equal(dayLabel("2026-08-31"), "Mon 31 Aug 2026");
  assert.equal(dayLabel("2026-01-02"), "Fri 2 Jan 2026");
  assert.equal(dayLabel("garbage"), "");
});

test("etDayIso names the ET day, not the UTC or local one", () => {
  // 03:30 UTC on the 31st is 23:30 ET on the 30th: still the previous
  // trading day's archive file.
  assert.equal(etDayIso(new Date("2026-08-31T03:30:00Z")), "2026-08-30");
  // Midday UTC is the same ET day, winter (EST) and summer (EDT) alike.
  assert.equal(etDayIso(new Date("2026-08-31T18:00:00Z")), "2026-08-31");
  assert.equal(etDayIso(new Date("2026-01-15T18:00:00Z")), "2026-01-15");
  // No argument: today's ET day, ISO-shaped (the archive's filename shape).
  assert.match(etDayIso(), /^\d{4}-\d{2}-\d{2}$/);
  // Garbage never throws; it answers "" and the panel treats the view as
  // live (refetches), the safe direction.
  assert.equal(etDayIso(new Date("garbage")), "");
});

// ---- search: cross-day mode and back ---------------------------------------

test("typing a ticker switches the request to the symbol form", () => {
  // No chosen day: across ALL days.
  const req = historyRequest("Watchlist", { date: null, symbol: "dg " });
  assert.equal(req.mode, "symbol");
  assert.equal(req.url, MOMX_HISTORY_ENDPOINT + "?list=Watchlist&symbol=DG");
  assert.ok(!req.url.includes("date="));
  assert.equal(req.key, "Watchlist|sym:DG");
  // A chosen day rides along and gets its own cache slot (2026-09-05).
  const day = historyRequest("Watchlist", { date: "2026-08-28", symbol: "dg " });
  assert.equal(day.mode, "symbol");
  assert.equal(day.url, MOMX_HISTORY_ENDPOINT + "?list=Watchlist&symbol=DG&date=2026-08-28");
  assert.equal(day.key, "Watchlist|sym:DG@2026-08-28");
});

test("clearing the search returns to the previously selected day", () => {
  const req = historyRequest("Watchlist", { date: "2026-08-28", symbol: "" });
  assert.equal(req.mode, "day");
  assert.equal(req.url, MOMX_HISTORY_ENDPOINT + "?list=Watchlist&date=2026-08-28");
  assert.equal(req.key, "Watchlist|2026-08-28");
  // No date selected -> the newest-day form, with no date param at all.
  const latest = historyRequest("Mag7", {});
  assert.equal(latest.url, MOMX_HISTORY_ENDPOINT + "?list=Mag7");
  assert.equal(latest.key, "Mag7|latest");
});

test("the symbol search groups snapshots under day headers, newest day first", () => {
  const entries = flattenHistoryRows([
    { symbol: "DG", date: "2026-08-27", at: "2026-08-27T10:00:00-04:00", isArrival: true, changed: [], row: { symbol: "DG" } },
    { symbol: "DG", date: "2026-08-31", at: "2026-08-31T09:40:00-04:00", isArrival: true, changed: [], row: { symbol: "DG" } },
    { symbol: "DG", date: "2026-08-31", at: "2026-08-31T11:05:00-04:00", isArrival: false, changed: ["rvol.1h"], row: { symbol: "DG" } },
  ]);
  const groups = groupRowsByDay(entries);
  assert.deepEqual(groups.map((group) => group.date), ["2026-08-31", "2026-08-27"]);
  assert.equal(groups[0].label, "Mon 31 Aug 2026");
  // Within a day the timeline runs oldest -> newest, as the day was lived.
  assert.deepEqual(groups[0].rows.map((entry) => entry.timeLabel), ["09:40", "11:05"]);
  assert.equal(groups[1].rows.length, 1);
});

// ---- changed-cell highlighting ---------------------------------------------

test("changed entries map onto exactly the right column keys", () => {
  const keys = changedColumnKeys(["rvol.1h", "skittles.2h", "highLow", "color"]);
  assert.deepEqual([...keys].sort(), ["color", "highLow", "rvol.1h", "skittles.2h"]);
  // Badge, news and scanReasons live in the Symbol cell.
  assert.deepEqual([...changedColumnKeys(["badge.on"])], ["symbol"]);
  assert.deepEqual([...changedColumnKeys(["news.headline"])], ["symbol"]);
  assert.deepEqual([...changedColumnKeys(["scanReasons"])], ["symbol"]);
  // Unknown names are ignored, never guessed onto a column.
  assert.equal(changedColumnKeys(["mystery.field", "", 7]).size, 0);
  assert.equal(changedColumnKeys(null).size, 0);
});

// ---- snapshot flattening: arrivals vs change rows ---------------------------

const ARRIVAL = {
  symbol: "SNOW",
  at: "2026-08-31T08:01:12-04:00",
  isArrival: true,
  changed: [],
  hits: 118,
  truncated: false,
  peakRvol: 1.1,
  row: { symbol: "SNOW", pctChange: 2.4, sparkline: [1, 2, 3], quoteTrend: [{ value: 1 }] },
};
const CHANGE = {
  symbol: "SNOW",
  at: "2026-08-31T09:14:40-04:00",
  isArrival: false,
  changed: ["rvol.1h", "skittles.2h"],
  hits: 118,
  truncated: false,
  peakRvol: 2.8,
  row: { symbol: "SNOW", pctChange: 3.1 },
};

test("arrival rows show the chart cells, change rows blank them", () => {
  const [arrival, change] = flattenHistoryRows([ARRIVAL, CHANGE]);
  assert.equal(arrival.isArrival, true);
  assert.equal(arrival.showChart, true, "the arrival renders sparkline/quote cells");
  assert.equal(change.isArrival, false);
  assert.equal(change.showChart, false, "a change row blanks sparkline/quote cells");
  assert.equal(change.changedKeys.has("rvol.1h"), true);
  assert.equal(change.changedKeys.has("skittles.2h"), true);
  assert.equal(arrival.changedKeys.size, 0);
});

test("snapshots keep one row each, ordered by time, with ET time labels", () => {
  // Fed out of order on purpose; the view must read as the day's timeline.
  const entries = flattenHistoryRows([CHANGE, ARRIVAL]);
  assert.equal(entries.length, 2, "one table row per snapshot - duplicates are the feature");
  assert.deepEqual(entries.map((entry) => entry.timeLabel), ["08:01", "09:14"]);
  assert.equal(entries[0].symbol, "SNOW");
});

test("the capped tag lands on the ticker's LAST row only", () => {
  const entries = flattenHistoryRows([
    { ...ARRIVAL, truncated: true },
    { ...CHANGE, truncated: true },
    { symbol: "DG", at: "2026-08-31T10:02:00-04:00", isArrival: true, changed: [], truncated: false, row: { symbol: "DG" } },
  ]);
  assert.deepEqual(entries.map((entry) => entry.capped), [false, true, false]);
});

test("malformed snapshot entries are dropped, not rendered blank", () => {
  const entries = flattenHistoryRows([
    null,
    { at: "2026-08-31T08:00:00-04:00" }, // no symbol, no row
    { symbol: "X", at: "2026-08-31T08:00:00-04:00" }, // no row object
    ARRIVAL,
  ]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].symbol, "SNOW");
  assert.deepEqual(flattenHistoryRows(null), []);
});

test("snapshotTimeLabel is ET and refuses garbage quietly", () => {
  assert.equal(snapshotTimeLabel("2026-08-31T08:01:12-04:00"), "08:01");
  // The same instant expressed in UTC still prints the ET clock.
  assert.equal(snapshotTimeLabel("2026-08-31T12:01:12Z"), "08:01");
  assert.equal(snapshotTimeLabel("garbage"), "");
  assert.equal(snapshotTimeLabel(null), "");
});

// --- what changed, not just THAT it changed -------------------------------
//
// Trader, 2026-09-01: "other than time and high/low any column change anything
// show the difference between 2". A ringed cell says a number moved; it does
// not say what it moved FROM, which is the thing worth reading.

const cellRow = (over = {}) => ({
  symbol: "SHEL",
  rvol: { "5m": { value: 0.8 }, "15m": { value: 1.0 } },
  skittles: { "2h": { value: 74 } },
  sqz: { D: { value: "-" } },
  highLow: { value: 0.4 },
  ...over,
});

const snap = (at, changed, row) => ({ symbol: "SHEL", at, atMs: Date.parse(at), changed, row });

test("a numeric change reports from, to and a signed delta", () => {
  const rows = [
    snap("2026-09-01T04:20:00-04:00", [], cellRow()),
    snap("2026-09-01T04:22:00-04:00", ["rvol.5m"],
      cellRow({ rvol: { "5m": { value: 3.5 }, "15m": { value: 1.0 } } })),
  ];
  const out = annotateChanges(rows);
  assert.deepEqual(out[0].changes, {}, "an arrival has nothing to compare against");
  const d = out[1].changes["rvol.5m"];
  assert.equal(d.from, 0.8);
  assert.equal(d.to, 3.5);
  assert.ok(Math.abs(d.delta - 2.7) < 1e-9);
  assert.equal(d.direction, "up");
});

test("a fall is negative and marked down", () => {
  const rows = [
    snap("2026-09-01T04:20:00-04:00", [], cellRow({ skittles: { "2h": { value: 81 } } })),
    snap("2026-09-01T04:22:00-04:00", ["skittles.2h"], cellRow({ skittles: { "2h": { value: 74 } } })),
  ];
  const d = annotateChanges(rows)[1].changes["skittles.2h"];
  assert.equal(d.from, 81); assert.equal(d.to, 74);
  assert.equal(d.delta, -7); assert.equal(d.direction, "down");
});

test("HIGH/LOW and TIME are excluded - he named both", () => {
  const rows = [
    snap("2026-09-01T04:20:00-04:00", [], cellRow()),
    snap("2026-09-01T04:22:00-04:00", ["highLow", "rvol.5m"],
      cellRow({ highLow: { value: 0.9 }, rvol: { "5m": { value: 3.5 }, "15m": { value: 1.0 } } })),
  ];
  const changes = annotateChanges(rows)[1].changes;
  assert.ok(!("highLow" in changes), "highLow must never render a difference");
  assert.ok(!("time" in changes));
  assert.ok("rvol.5m" in changes, "the real change still reports");
});

test("a non-numeric change shows from -> to instead of a delta", () => {
  const rows = [
    snap("2026-09-01T04:20:00-04:00", [], cellRow()),
    snap("2026-09-01T04:22:00-04:00", ["sqz.D"], cellRow({ sqz: { D: { value: "*2" } } })),
  ];
  const d = annotateChanges(rows)[1].changes["sqz.D"];
  assert.equal(d.from, "-"); assert.equal(d.to, "*2");
  assert.equal(d.delta, null, "a delta between '-' and '*2' would be a lie");
});

test("comparison is PER SYMBOL - two tickers interleaved never compare to each other", () => {
  const other = { symbol: "CNQ", rvol: { "5m": { value: 9.9 } } };
  const rows = [
    { symbol: "SHEL", at: "2026-09-01T04:20:00-04:00", atMs: 1, changed: [], row: cellRow() },
    { symbol: "CNQ", at: "2026-09-01T04:21:00-04:00", atMs: 2, changed: [], row: other },
    { symbol: "SHEL", at: "2026-09-01T04:22:00-04:00", atMs: 3, changed: ["rvol.5m"],
      row: cellRow({ rvol: { "5m": { value: 3.5 } } }) },
  ];
  const d = annotateChanges(rows)[2].changes["rvol.5m"];
  assert.equal(d.from, 0.8, "must compare against SHEL's own previous row, not CNQ's");
});

test("a first-ever change with no usable previous value reports to without inventing from", () => {
  const rows = [
    snap("2026-09-01T04:20:00-04:00", [], { symbol: "SHEL" }),
    snap("2026-09-01T04:22:00-04:00", ["rvol.5m"], cellRow({ rvol: { "5m": { value: 3.5 } } })),
  ];
  const d = annotateChanges(rows)[1].changes["rvol.5m"];
  assert.equal(d.from, null);
  assert.equal(d.to, 3.5);
  assert.equal(d.delta, null);
});

test("garbage in never throws", () => {
  for (const bad of [null, undefined, "x", 7, [null], [{}]]) {
    assert.doesNotThrow(() => annotateChanges(bad));
  }
  assert.deepEqual(annotateChanges(null), []);
});

test("annotating does not mutate the entries it was given", () => {
  const rows = [snap("2026-09-01T04:20:00-04:00", [], cellRow())];
  const before = JSON.stringify(rows);
  annotateChanges(rows);
  assert.equal(JSON.stringify(rows), before);
});

// --------------------------------------------------------------------------
// BEAR direction (spec 2026-09-24): every history request and cache key says
// which board it is for, so bull rows can never be shown under a BEAR header.
// --------------------------------------------------------------------------

import { boardCacheKey } from "./momxHistory.js";

test("historyRequest carries the direction in the URL and the cache key", () => {
  const bear = historyRequest("Mag7", { direction: "bear" });
  assert.match(bear.url, /dir=bear/);
  assert.equal(bear.key.startsWith("bear|Mag7"), true);
  const bull = historyRequest("Mag7", {});
  assert.equal(bull.url.includes("dir="), false);
  assert.equal(bull.key.startsWith("Mag7"), true);
  const sym = historyRequest("Mag7", { direction: "bear", symbol: "nvda", date: "2026-09-25" });
  assert.match(sym.url, /dir=bear/);
  assert.equal(sym.key, "bear|Mag7|sym:NVDA@2026-09-25");
});

test("boardCacheKey keeps bull keys unchanged and suffixes bear", () => {
  assert.equal(boardCacheKey("Mag7", "bull"), "Mag7");
  assert.equal(boardCacheKey("Mag7", undefined), "Mag7");
  assert.equal(boardCacheKey("Mag7", "bear"), "Mag7|bear");
  assert.equal(boardCacheKey(null, "bear"), null);
});
