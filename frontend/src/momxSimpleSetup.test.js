// The simple Setup column (2026-09-28, "lot of rules its confusing"): only
// GO+, GO, ⭐1, ⚠, ADX, the chart arrows, ⚡, MX A+, Skittles breaks, SQZ fires
// and the RVOL timeframes stay.
import test from "node:test";
import assert from "node:assert/strict";
import { strategyTags, skittlesBreaks, rvolBuilding, setMomxSimpleSetup } from "./momxFilters.js";

const AT = Date.parse("2026-09-28T10:30:00-04:00");
const barAt = (hm) => Date.parse("2026-09-28T" + hm + ":00-04:00") / 1000;

test("RVOL tag lists the painted, live timeframes on the board's side", () => {
  const row = {
    symbol: "NVDA",
    rvol: {
      "5m": { bg: "black", fg: "cyan", barAt: barAt("10:25") },
      "30m": { bg: "cyan", barAt: barAt("10:00") },
      "1h": { bg: "green", barAt: barAt("10:00") },
      "2h": { bg: "magenta", barAt: barAt("10:00") },
      D: { bg: "cyan", barAt: Date.parse("2026-09-25T09:30:00-04:00") / 1000 },   // prior session: dimmed, left out
    },
  };
  assert.equal(rvolBuilding(row, AT).text, "RVOL↑ 30m 1h");
  const bear = { ...row, direction: "bear" };
  assert.equal(rvolBuilding(bear, AT).text, "RVOL↓ 2h");
  assert.equal(rvolBuilding({ symbol: "X", rvol: { "5m": { bg: "black" } } }, AT), null);
});

test("Skittles tag: only crosses SEEN in the last hour that still show", () => {
  const ev = (hm, what) => ({ at: "2026-09-28T" + hm + ":00-04:00", what });
  const row = {
    symbol: "M",
    skittles: { "2h": { bg: "cyan" }, "4h": { bg: "green" }, D: { bg: "lime" }, "2D": { bg: "cyan" }, "3D": { bg: "cyan" }, M: { bg: "green" } },
    gradeFresh: { timeline: [
      ev("10:05", "SKIT 2h bg cyan"),       // fresh
      ev("10:20", "SKIT D bg lime"),        // fresh, newest
      ev("09:10", "SKIT 4h bg green"),      // 80 min ago: not fresh
      ev("10:15", "SKIT 3D bg dark_green"), // counter-trend paint: not a break
    ] },
  };
  const tags = skittlesBreaks(row, AT);
  // 2D / 3D / Mo painted for days: left out. One pill per cross time, newest
  // first, and only the newest carries the flash clock.
  assert.deepEqual(tags.map((x) => x.text), ["SKIT↑ D 10:20", "SKIT↑ 2h 10:05"]);
  assert.equal(tags[0].atMs, Date.parse("2026-09-28T10:20:00-04:00"));
  assert.equal(tags[1].atMs, undefined);
  const both = strategyTags(row, Date.parse("2026-09-28T10:22:00-04:00")).filter((x) => x.key === "skit");
  assert.deepEqual(both.map((x) => Boolean(x.fresh)), [true, false], "only the latest blinks");
  const gone = { ...row, skittles: { ...row.skittles, "2h": { bg: "black" }, D: { bg: "black" } } };
  assert.deepEqual(skittlesBreaks(gone, AT), [], "the block no longer shows it");
  assert.deepEqual(skittlesBreaks({ symbol: "M", skittles: { "2D": { bg: "cyan" } } }, AT), [], "painted, never seen crossing");
  // BB 2026-09-28: 4D crossed 09:47, blinked off, back at 11:01 - not fresh (times shifted to the test clock).
  const blink = { symbol: "BB", skittles: { "4D": { bg: "green" } }, gradeFresh: { timeline: [
    { at: "2026-09-28T09:10:00-04:00", what: "SKIT 4D bg green" }, { at: "2026-09-28T10:25:00-04:00", what: "SKIT 4D bg green" }] } };
  assert.deepEqual(skittlesBreaks(blink, AT), []);
});

test("simple mode keeps his list and drops the rest", () => {
  setMomxSimpleSetup(true);
  const row = {
    symbol: "META",
    grade: { letter: "A+" },
    momoxAPlus: { at: "2026-09-28T09:35:00-04:00", price: 700 },
    rvol: { "30m": { bg: "cyan", barAt: barAt("10:00") } },
    skittles: { "2h": { bg: "cyan" } },
    gradeFresh: { timeline: [{ at: "2026-09-28T10:10:00-04:00", what: "SKIT 2h bg cyan" }] },
    marketTurn: { rank: 1, at: "2026-09-28T10:05:00-04:00", price: 700 },
  };
  const keys = strategyTags(row, AT).map((t) => t.key);
  assert.equal(keys[0], "bolt");
  assert.ok(keys.includes("mxaplus") && keys.includes("rvol") && keys.includes("skit"), keys.join(","));
  assert.ok(!keys.includes("turn"), "market turn is not on his list");
  setMomxSimpleSetup(false);
  assert.ok(strategyTags(row, AT).some((t) => t.key === "turn"), "full mode still shows it");
  setMomxSimpleSetup(true);
});

test("FILTERS: MomoX A+ shows only the rows with an MX A+ stamp (bear rows carry the BEAR book's)", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS, strategyOf } = await import("./momxFilters.js");
  const config = coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup: "mxaplus" });
  assert.equal(strategyOf(config), "mxaplus");
  const rows = [
    { symbol: "META", grade: { letter: "A+" }, momoxAPlus: { at: "2026-09-28T09:35:00-04:00" } },
    { symbol: "NVDA", grade: { letter: "A+" } },
    { symbol: "BEAR", direction: "bear", grade: { letter: "A+" }, momoxAPlus: { at: "2026-09-28T09:35:00-04:00" } },
  ];
  assert.deepEqual(filterRows(rows, config, AT).map((r) => r.symbol), ["META", "BEAR"]);
});

test("chart arrows: a redrawn CALL2H shows when it appeared; a repainted one is kept, struck", async () => {
  const { chartArrows } = await import("./momxFilters.js");
  const now = Date.parse("2026-09-28T13:10:00-04:00");
  const row = { symbol: "ABVX", chartSignals: [
    { label: "CALL2H", family: "4x8", timeframe: "2H", at: "2026-09-28T09:00:00-04:00", seenAt: "2026-09-28T13:04:45-04:00" },
    { label: "C4H", family: "4x8", timeframe: "4H", at: "2026-09-28T10:15:00-04:00", seenAt: "2026-09-28T10:20:00-04:00", goneAt: "2026-09-28T13:05:00-04:00" },
  ] };
  const arrows = chartArrows(row, now);
  const call = arrows.find((a) => a.label === "CALL2H");
  assert.equal(call.redrawn, true);
  const c4h = arrows.find((a) => a.label === "C4H");
  assert.ok(c4h && c4h.goneAt);
  setMomxSimpleSetup(true);
  const tags = strategyTags(row, now).filter((t) => t.key.startsWith("chart-"));
  assert.deepEqual(tags.map((t) => t.text).sort(), ["C4H 10:15 ✕13:05", "CALL2H 13:04↺"]);
  assert.equal(tags.find((t) => t.text.startsWith("C4H")).gone, true);
});

test("fading blocks no rule: MX A+ keeps its tag and bolt, with the warning", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS } = await import("./momxFilters.js");
  const at = Date.parse("2026-09-28T09:40:00-04:00");
  const fading = { symbol: "F", momoxAPlus: { at: "2026-09-28T09:35:00-04:00", price: 10 }, m5: { state: "fading" } };
  const tags = strategyTags(fading, at);
  assert.equal(tags[0].key, "bolt");
  assert.ok(tags.some((t) => t.key === "mxaplus"));
  assert.ok(tags.some((t) => t.key === "warn" && /fading/.test(t.text)));
  const config = coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup: "mxaplus" });
  assert.deepEqual(filterRows([fading], config, at).map((r) => r.symbol), ["F"]);
});

test("FILTERS: the bot's rules on both boards (MX A+ bear, turn, sector, ZS, short arrows)", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS } = await import("./momxFilters.js");
  const at = Date.parse("2026-09-29T10:30:00-04:00");
  const cfg = (setup) => coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup });
  const bearRow = (sym, extra = {}) => ({ symbol: sym, direction: "bear", industry: "Crypto", pctChange: -5, last: 9, m5: { vwap: 10 }, ...extra });
  const rows = [
    bearRow("RIOT", { momoxAPlus: { at: "2026-09-29T09:50:00-04:00" }, marketTurn: { rank: 1, at: "2026-09-29T10:10:00-04:00" } }),
    bearRow("CIFR"), bearRow("IREN"), bearRow("BTBT", { pctChange: -4 }),
    { symbol: "UP", direction: "bear", industry: "Pharma", pctChange: 1, last: 11, m5: { vwap: 10 } },
  ];
  assert.deepEqual(filterRows(rows, cfg("mxaplus"), at).map((r) => r.symbol), ["RIOT"]);
  assert.deepEqual(filterRows(rows, cfg("turn"), at).map((r) => r.symbol), ["RIOT"]);
  assert.deepEqual(filterRows(rows, cfg("sector"), at).map((r) => r.symbol).sort(), ["BTBT", "CIFR", "IREN", "RIOT"]);
  const bar = Date.parse("2026-09-29T10:20:00-04:00") / 1000;
  const zsRow = bearRow("Z", { m5: { vwap: 10, pillars: { zs: { above: true, clear2: true, barAt: bar } } } });
  assert.deepEqual(filterRows([zsRow, bearRow("N")], cfg("zs"), at).map((r) => r.symbol), ["Z"]);
  const arrow = bearRow("P", { chartSignals: [{ label: "P2H", family: "4x8", timeframe: "2H", at: "2026-09-29T10:00:00-04:00", seenAt: "2026-09-29T10:05:00-04:00" }] });
  assert.deepEqual(filterRows([arrow, bearRow("Q")], cfg("chartShort"), at).map((r) => r.symbol), ["P"]);
});
