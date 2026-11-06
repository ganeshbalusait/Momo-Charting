// Long Term page logic: Buffett / Rule #1 value screen.
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  MOS_OPTIONS, PREFS_KEY, readPrefs, writePrefs, TILES, tileCounts, priceGuard, guardRow,
  filterRows, DEFAULT_SORT, sortRows, SIGNAL_LABEL, signalTone, formatMoney, formatPct,
  formatCap, formatGrowth, buyZoneBar, columns, cardFields, drawerSections,
  reasonText, big5Dots, formatEtStamp, acceptsPayload, sortValue, etDate, previousWeekday, formatEps,
  formatPayback,
} from "./longTerm.js";

// quoteTimeMs defaults to "just now": the guard also rejects quotes older than the previous weekday (ET).
const row = (o) => ({ symbol: "X", name: "X Corp", price: 100, quoteTimeMs: Date.now(), refPrice: null, signal: "WAIT", leaps: false, buyZonePct: null, ...o });
const AAPL = row({ symbol: "AAPL", name: "Apple Inc", signal: "BUY", leaps: false, buyZonePct: 0.1, buyBelow: 120.5, roic: 0.28, big5: 5, big5Na: 0, reason: "ok" });
const MSFT = row({ symbol: "MSFT", name: "Microsoft", signal: "BUY", leaps: true, buyZonePct: 0.05 });
const KO = row({ symbol: "KO", name: "Coca-Cola", signal: "WAIT", buyZonePct: -0.2 });
const NVDA = row({ symbol: "NVDA", name: "Nvidia", signal: "AVOID", buyZonePct: -0.9 });
const SPY = row({ symbol: "SPY", name: "S&P ETF", signal: "ETF" });

describe("prefs", () => {
  const mem = (init = {}) => {
    const d = { ...init };
    return { getItem: (k) => (k in d ? d[k] : null), setItem: (k, v) => { d[k] = v; } };
  };
  test("constants", () => {
    assert.deepEqual(MOS_OPTIONS, [50, 30]);
    assert.equal(PREFS_KEY, "longTermPrefs");
  });
  test("defaults", () => {
    assert.deepEqual(readPrefs(mem()), { mos: 50, sort: { key: "default", dir: "desc" }, tile: null });
  });
  test("round trip", () => {
    const s = mem();
    writePrefs(s, { mos: 30, sort: { key: "pe", dir: "asc" }, tile: "BUY" });
    assert.deepEqual(readPrefs(s), { mos: 30, sort: { key: "pe", dir: "asc" }, tile: "BUY" });
  });
  test("invalid mos and junk fall back", () => {
    assert.equal(readPrefs(mem({ longTermPrefs: JSON.stringify({ mos: 40 }) })).mos, 50);
    assert.equal(readPrefs(mem({ longTermPrefs: "{bad" })).mos, 50);
  });
  test("throwing storage never throws", () => {
    const bad = { getItem() { throw new Error("x"); }, setItem() { throw new Error("x"); } };
    assert.equal(readPrefs(bad).mos, 50);
    assert.doesNotThrow(() => writePrefs(bad, { mos: 30 }));
    assert.equal(readPrefs(null).mos, 50);
  });
});

describe("tiles and guard", () => {
  test("tileCounts", () => {
    assert.deepEqual(TILES, ["BUY", "LEAPS", "WAIT", "AVOID", "NODATA"]);
    assert.deepEqual(tileCounts([AAPL, MSFT, KO, NVDA, SPY]), { BUY: 2, LEAPS: 1, WAIT: 1, AVOID: 1, NODATA: 1 });
  });
  test("priceGuard", () => {
    assert.equal(priceGuard(row({})), true);
    assert.equal(priceGuard(row({ price: null })), false);
    assert.equal(priceGuard(row({ price: 0 })), false);
    assert.equal(priceGuard(row({ quoteTimeMs: null })), false);
    assert.equal(priceGuard(row({ quoteTimeMs: 0 })), false);
    assert.equal(priceGuard(row({ refPrice: 100, price: 125 })), true);
    assert.equal(priceGuard(row({ refPrice: 100, price: 126 })), false);
    assert.equal(priceGuard(row({ refPrice: 100, price: 70 })), false);
    assert.equal(priceGuard(row({ refPrice: undefined })), true);
  });
  test("guardRow", () => {
    const g = guardRow(row({ signal: "BUY", leaps: true, price: null }));
    assert.equal(g.signal, "NO_PRICE");
    assert.equal(g.leaps, false);
    assert.equal(guardRow(row({ signal: "ETF", price: null })).signal, "ETF");
    const ok = row({ signal: "BUY", leaps: true });
    assert.equal(guardRow(ok).signal, "BUY");
    assert.equal(ok.signal, "BUY");
  });
  test("guardRow clears leaps on any failing row, keeps non-priced signals, tolerates null", () => {
    const s = guardRow(row({ signal: "STALE", leaps: true, price: null }));
    assert.equal(s.signal, "STALE");
    assert.equal(s.leaps, false);
    assert.equal(tileCounts([s]).LEAPS, 0);
    assert.equal(guardRow(null), null);
  });
});

describe("filter and sort", () => {
  const rows = [KO, NVDA, AAPL, SPY, MSFT];
  test("filterRows tiles", () => {
    assert.deepEqual(filterRows(rows, { tile: "BUY" }).map((r) => r.symbol), ["AAPL", "MSFT"]);
    assert.deepEqual(filterRows(rows, { tile: "LEAPS" }).map((r) => r.symbol), ["MSFT"]);
    assert.deepEqual(filterRows(rows, { tile: "NODATA" }).map((r) => r.symbol), ["SPY"]);
    assert.equal(filterRows(rows, {}).length, 5);
  });
  test("filterRows query: symbol prefix or name substring, case-insensitive", () => {
    assert.deepEqual(filterRows(rows, { query: "ms" }).map((r) => r.symbol), ["MSFT"]);
    assert.deepEqual(filterRows(rows, { query: "COLA" }).map((r) => r.symbol), ["KO"]);
    assert.deepEqual(filterRows(rows, { query: "SF" }).map((r) => r.symbol), []);
  });
  test("default sort", () => {
    assert.deepEqual(DEFAULT_SORT, { key: "default", dir: "desc" });
    assert.deepEqual(sortRows(rows, DEFAULT_SORT).map((r) => r.symbol), ["MSFT", "AAPL", "KO", "NVDA", "SPY"]);
  });
  test("default sort ignores dir", () => {
    const want = sortRows(rows, DEFAULT_SORT).map((r) => r.symbol);
    assert.deepEqual(sortRows(rows, { key: "default", dir: "asc" }).map((r) => r.symbol), want);
  });
  test("column sort nulls last both ways", () => {
    const a = row({ symbol: "A", pe: 10 }), b = row({ symbol: "B", pe: null }), c = row({ symbol: "C", pe: 30 });
    assert.deepEqual(sortRows([b, c, a], { key: "pe", dir: "asc" }).map((r) => r.symbol), ["A", "C", "B"]);
    assert.deepEqual(sortRows([b, a, c], { key: "pe", dir: "desc" }).map((r) => r.symbol), ["C", "A", "B"]);
  });
  test("string sort and no mutation", () => {
    const input = [KO, AAPL];
    assert.deepEqual(sortRows(input, { key: "symbol", dir: "asc" }).map((r) => r.symbol), ["AAPL", "KO"]);
    assert.deepEqual(sortRows(input, { key: "name", dir: "desc" }).map((r) => r.symbol), ["KO", "AAPL"]);
    assert.equal(input[0], KO);
  });
});

describe("signals and formats", () => {
  test("labels and tones", () => {
    assert.equal(SIGNAL_LABEL.NO_PRICE, "No price");
    assert.equal(SIGNAL_LABEL.STALE, "Stale filing");
    assert.equal(signalTone("BUY"), "buy");
    assert.equal(signalTone("WAIT"), "wait");
    assert.equal(signalTone("AVOID"), "avoid");
    assert.equal(signalTone("ETF"), "muted");
    assert.equal(signalTone(undefined), "muted");
  });
  test("formats", () => {
    assert.equal(formatMoney(344.17), "$344.17");
    assert.equal(formatMoney(null), "—");
    assert.equal(formatPct(0.1234), "12%");
    assert.equal(formatPct(0.1234, 1), "12.3%");
    assert.equal(formatPct(null), "—");
    assert.equal(formatCap(5.0e12), "5.00T");
    assert.equal(formatCap(1.014e10), "10.1B");
    assert.equal(formatCap(6.406e8), "641M");
    assert.equal(formatCap(null), "—");
    assert.equal(formatGrowth(0.12), "+12%/yr");
    assert.equal(formatGrowth(-0.05), "−5%/yr");
    assert.equal(formatGrowth(null), "—");
  });
  test("buyZoneBar", () => {
    assert.deepEqual(buyZoneBar(0.2), { width: 20, tone: "buy" });
    assert.deepEqual(buyZoneBar(-0.5), { width: 50, tone: "wait" });
    assert.deepEqual(buyZoneBar(-3), { width: 100, tone: "wait" });
    assert.deepEqual(buyZoneBar(null), { width: 0, tone: "muted" });
  });
});

describe("columns and cards", () => {
  test("desktop columns", () => {
    const cols = columns("desktop");
    assert.deepEqual(cols.map((c) => c.key), ["symbol", "name", "price", "signal", "leaps", "buyZonePct", "buyBelow", "leapsBelow", "sticker", "buffett", "payback", "roic", "big5", "quality", "pe", "debtToEquity", "salesGrowth", "epsGrowth", "fcfGrowth", "marketCap", "filedEnd"]);
    assert.equal(cols[5].label, "Buy Zone");
    assert.equal(cols[6].label, "BUY ≤");
    assert.equal(cols[16].label, "Sales 10y");
    assert.equal(cols.filter((c) => !c.hideOnPhone).length, 6);
  });
  test("phone columns", () => {
    assert.deepEqual(columns("phone").map((c) => c.key), ["symbol", "name", "price", "signal", "leaps", "buyZonePct"]);
  });
  test("cardFields", () => {
    const c = cardFields(AAPL);
    assert.equal(c.symbol, "AAPL");
    assert.equal(c.price, "$100.00");
    assert.equal(c.signalLabel, "BUY");
    assert.equal(c.tone, "buy");
    assert.equal(c.leaps, false);
    assert.deepEqual(c.buyZone, { width: 10, tone: "buy" });
    assert.equal(c.buyBelow, "$120.50");
    assert.equal(c.roic, "28%");
    assert.equal(c.big5, 5);
    assert.equal(c.big5Na, 0);
    assert.equal(c.reason, "ok");
  });
});

describe("drawerSections", () => {
  const detail = {
    symbol: "AAPL", unit: "USD",
    checks: [
      { key: "roic", label: "ROIC now", value: "28%", threshold: "≥ 15%", passed: true, detail: "" },
      { key: "debt", label: "Debt", value: "5.0× FCF", threshold: "> 3×", passed: false, detail: "" },
      { key: "eq", label: "Equity growth", value: "", threshold: "", passed: null, detail: "buybacks" },
    ],
    years: [{ end: "2025-09-27", revenue: 391e9, netIncome: 94e9, eps: 6.08, equity: 57e9, fcf: 108e9, roic: 0.68 }],
    explain: ["Sticker $200", "Buy below $100"],
    crossChecks: [{ label: "Shares", ok: true, detail: "match" }, { label: "Cash", ok: false, detail: "off" }],
    sources: [{ label: "SEC", value: "10-K" }],
  };
  test("sections", () => {
    const s = drawerSections(detail);
    assert.deepEqual(s.map((x) => x.title), ["Checklist", "Valuation", "Ten years", "Cross-checks", "Sources"]);
    assert.deepEqual(s[0].lines, ["✓ ROIC now 28% ≥ 15%", "✗ Debt 5.0× FCF > 3×", "n/a Equity growth — buybacks"]);
    assert.deepEqual(s[1].lines, ["Sticker $200", "Buy below $100"]);
    assert.deepEqual(s[2].lines, ["2025: sales 391B · net 94B · EPS $6.08 · equity 57B · FCF 108B · ROIC 68%"]);
    assert.deepEqual(s[3].lines, ["✓ Shares — match", "✗ Cash — off"]);
    assert.deepEqual(s[4].lines, ["SEC: 10-K"]);
  });
  test("missing parts never print undefined", () => {
    const s = drawerSections({ checks: "nope", crossChecks: [{ label: "A", ok: true }], sources: [{ label: "S" }, { value: "v" }] });
    assert.deepEqual(s[0].lines, []);
    assert.deepEqual(s[3].lines, ["✓ A"]);
    assert.deepEqual(s[4].lines, ["S", "v"]);
    assert.ok(!JSON.stringify(s).includes("undefined"));
  });
  test("non-USD unit and empty detail", () => {
    const s = drawerSections({ ...detail, unit: "TWD", years: [{ end: "2025-12-31", revenue: 2.89e12, netIncome: 1.2e12, eps: 45, equity: 3e12, fcf: 1e12, roic: 0.3 }] });
    assert.ok(s[2].lines[0].startsWith("2025: sales TWD 2.89T"));
    assert.deepEqual(drawerSections(null).map((x) => x.lines.length), [0, 0, 0, 0, 0]);
  });
});

describe("page helpers", () => {
  test("reasonText: WAIT shows the buy levels", () => {
    assert.equal(reasonText(row({ signal: "WAIT", buyBelow: 70, leapsBelow: 132.5 })), "BUY ≤ $70.00 · LEAPS ≤ $132.50");
    assert.equal(reasonText(row({ signal: "WAIT" })), "BUY ≤ —");
  });
  test("reasonText: AVOID says why, no-data rows show the label", () => {
    assert.equal(reasonText(row({ signal: "AVOID", reason: "ROIC 4% < 10%" })), "ROIC 4% < 10%");
    assert.equal(reasonText(row({ signal: "AVOID" })), "AVOID");
    assert.equal(reasonText(row({ signal: "ETF" })), "ETF");
    assert.equal(reasonText(row({ signal: "STALE", reason: "last 10-K 2023" })), "Stale filing: last 10-K 2023");
    // a guarded row must not keep its old BUY reason
    assert.equal(reasonText(guardRow(row({ signal: "BUY", price: null, reason: "Below safety" }))), "No price");
  });
  test("big5Dots", () => {
    assert.deepEqual(big5Dots(5, 0), ["pass", "pass", "pass", "pass", "pass"]);
    assert.deepEqual(big5Dots(3, 1), ["pass", "pass", "pass", "na", "fail"]);
    assert.deepEqual(big5Dots(0, null), ["fail", "fail", "fail", "fail", "fail"]);
    assert.deepEqual(big5Dots(null, 0), ["na", "na", "na", "na", "na"]);
  });
  test("formatEtStamp", () => {
    assert.equal(formatEtStamp("2026-11-06T08:12:00Z"), "Nov 6, 03:12 AM ET");
    assert.equal(formatEtStamp(Date.parse("2026-11-06T08:12:00Z") / 1000), "Nov 6, 03:12 AM ET");
    assert.equal(formatEtStamp(null), "—");
    assert.equal(formatEtStamp("junk"), "—");
  });
});

describe("review fixes", () => {
  test("acceptsPayload only takes rows for the MOS in effect", () => {
    assert.equal(acceptsPayload({ rows: [], meta: { mos: 50 } }, 50), true);
    assert.equal(acceptsPayload({ rows: [], meta: { mos: "30" } }, 30), true);
    assert.equal(acceptsPayload({ rows: [], meta: { mos: 50 } }, 30), false);
    assert.equal(acceptsPayload({ rows: [], meta: {} }, 50), false);
    assert.equal(acceptsPayload({ rows: [] }, 50), false);
    assert.equal(acceptsPayload({ meta: { mos: 50 } }, 50), false);
    assert.equal(acceptsPayload(null, 50), false);
  });
  test("ROIC column sorts FIN rows by ROE", () => {
    const fin = row({ symbol: "JPM", financial: true, roic: null, roe: 0.3 });
    const a = row({ symbol: "A", roic: 0.2 });
    const b = row({ symbol: "B", roic: 0.1 });
    assert.equal(sortValue(fin, "roic"), 0.3);
    assert.equal(sortValue(a, "roic"), 0.2);
    assert.equal(sortValue(fin, "pe"), fin.pe);
    assert.deepEqual(sortRows([b, fin, a], { key: "roic", dir: "desc" }).map((r) => r.symbol), ["JPM", "A", "B"]);
    assert.deepEqual(sortRows([b, fin, a], { key: "roic", dir: "asc" }).map((r) => r.symbol), ["B", "A", "JPM"]);
  });
});

describe("final review fixes", () => {
  // Friday 2026-11-06 20:00 ET = 2026-11-07 01:00Z
  const FRI_EVE = Date.parse("2026-11-07T01:00:00Z");
  const at = (iso) => Date.parse(iso);
  test("etDate / previousWeekday use the New York calendar", () => {
    assert.equal(etDate(FRI_EVE), "2026-11-06");                    // already Saturday in UTC
    assert.equal(previousWeekday("2026-11-06"), "2026-11-05");      // Friday -> Thursday
    assert.equal(previousWeekday("2026-11-09"), "2026-11-06");      // Monday -> Friday
    assert.equal(previousWeekday("2026-11-08"), "2026-11-06");      // Sunday -> Friday
    assert.equal(previousWeekday("2026-11-07"), "2026-11-06");      // Saturday -> Friday
  });
  test("priceGuard rejects a quote older than the previous weekday (ET)", () => {
    const q = (iso, extra = {}) => row({ quoteTimeMs: at(iso), ...extra });
    assert.equal(priceGuard(q("2026-11-06T20:59:00Z"), FRI_EVE), true);            // today
    assert.equal(priceGuard(q("2026-11-05T20:59:00Z"), FRI_EVE), true);            // Thursday close
    assert.equal(priceGuard(q("2026-11-04T20:59:00Z"), FRI_EVE), false);           // Wednesday: stale
    assert.equal(priceGuard(q("2026-11-03T20:00:00Z"), FRI_EVE), false);           // 3 days old
    const monMorning = at("2026-11-09T14:00:00Z");
    assert.equal(priceGuard(q("2026-11-06T20:59:00Z"), monMorning), true);         // Monday accepts Friday
    assert.equal(priceGuard(q("2026-11-05T20:59:00Z"), monMorning), false);        // ...not Thursday
    const sat = at("2026-11-07T16:00:00Z");
    assert.equal(priceGuard(q("2026-11-06T20:59:00Z"), sat), true);                // Saturday accepts Friday
    assert.equal(priceGuard(q("2026-11-08T20:00:00Z"), FRI_EVE), false);           // future quote time
    // ET date, not UTC: 23:30 ET Wednesday is Thursday in UTC but still Wednesday in New York
    assert.equal(priceGuard(q("2026-11-05T04:30:00Z"), FRI_EVE), false);
    assert.equal(priceGuard(row({ price: null }), FRI_EVE), false);
  });
  test("guardRow nulls the Buy Zone when it downgrades and can force a downgrade", () => {
    const buy = row({ signal: "BUY", leaps: true, buyZonePct: 0.12, quoteTimeMs: at("2026-11-03T20:00:00Z") });
    const g = guardRow(buy, { now: FRI_EVE });
    assert.equal(g.signal, "NO_PRICE");
    assert.equal(g.leaps, false);
    assert.equal(g.buyZonePct, null);
    const fresh = row({ signal: "BUY", leaps: true, buyZonePct: 0.12, quoteTimeMs: FRI_EVE - 60000 });
    assert.equal(guardRow(fresh, { now: FRI_EVE }).signal, "BUY");
    const forced = guardRow(fresh, { now: FRI_EVE, force: true });                 // stale feed
    assert.deepEqual([forced.signal, forced.leaps, forced.buyZonePct], ["NO_PRICE", false, null]);
    assert.equal(guardRow(row({ signal: "ETF" }), { now: FRI_EVE, force: true }).signal, "ETF");
    assert.equal(tileCounts([forced]).BUY, 0);
  });
  test("reasonText: the three WAIT variants", () => {
    assert.equal(reasonText(row({ signal: "WAIT", reason: "price above safety buy", buyBelow: 70, leapsBelow: 132.5 })),
      "BUY ≤ $70.00 · LEAPS ≤ $132.50");
    assert.equal(reasonText(row({ signal: "WAIT", reason: "price above safety buy", buyBelow: 70, leapsBelow: null })),
      "BUY ≤ $70.00");
    assert.equal(reasonText(row({ signal: "WAIT", reason: "young: 6 years, BUY needs 8", buyBelow: null, leapsBelow: null })),
      "young: 6 years, BUY needs 8");
    assert.equal(reasonText(row({ signal: "WAIT", reason: "earnings falling", buyBelow: null })), "earnings falling");
    assert.equal(reasonText(row({ signal: "WAIT", reason: "earnings trend unknown", buyBelow: 55 })),
      "earnings trend unknown · BUY ≤ $55.00");
  });
  test("reasonText does not repeat a label the server already wrote", () => {
    assert.equal(reasonText(row({ signal: "STALE", reason: "Stale: last refresh 5 days ago" })), "Stale: last refresh 5 days ago");
    assert.equal(reasonText(row({ signal: "CHECK_DATA", reason: "Check data: listing factor moved 31%" })),
      "Check data: listing factor moved 31%");
    assert.equal(reasonText(row({ signal: "CHECK_DATA", reason: "computing" })), "Check data: computing");
  });
  test("EPS in its filing unit; payback 50+", () => {
    assert.equal(formatEps(6.08, "USD"), "$6.08");
    assert.equal(formatEps(45, "TWD"), "TWD 45.00");
    assert.equal(formatEps(-1.5, "EUR"), "−EUR 1.50");
    assert.equal(formatEps(null, "EUR"), "—");
    const s = drawerSections({ unit: "TWD", years: [{ end: "2025-12-31", revenue: 1e12, netIncome: 1e11, eps: 45, equity: 1e12, fcf: 1e11, roic: 0.3 }] });
    assert.ok(s[2].lines[0].includes("EPS TWD 45.00") && !s[2].lines[0].includes("$"));
    assert.equal(formatPayback(50), "50+y");
    assert.equal(formatPayback(12), "12.0y");
    assert.equal(formatPayback(null), "—");
  });
});
