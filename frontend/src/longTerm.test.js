// Long Term page logic: Buffett / Rule #1 value screen.
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  MOS_OPTIONS, PREFS_KEY, readPrefs, writePrefs, TILES, tileCounts, priceGuard, guardRow,
  filterRows, DEFAULT_SORT, sortRows, SIGNAL_LABEL, signalTone, formatMoney, formatPct,
  formatCap, formatGrowth, buyZoneBar, columns, cardFields, drawerSections,
  reasonText, big5Dots, formatEtStamp, acceptsPayload, sortValue, etDate, previousWeekday, formatEps,
  formatPayback, formatExpiry, leapLine, leapSection, leapOrder,
  leapTransient, leapTransientNote, leapRetryDue, LEAP_RETRY_MS, LEAP_RETRY_MAX_MS, LEAP_TRANSIENT_REASONS,
  LEAP_NO_DELTA_REASON, isSetup, setupLines, SETUP_NOTE,
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
  test("round trip keeps MOS and sort; a tile never carries over to the next visit", () => {
    // Ganesh 11-07: "I need to see all the data also if I don't select BUY": a tile left on
    // (NO DATA on his phone) hid every row on the next visit, so each visit starts with all rows
    const s = mem();
    writePrefs(s, { mos: 30, sort: { key: "pe", dir: "asc" }, tile: "BUY" });
    assert.deepEqual(readPrefs(s), { mos: 30, sort: { key: "pe", dir: "asc" }, tile: null });
    assert.equal(JSON.parse(s.getItem("longTermPrefs")).tile, undefined, "the tile is not stored");
    // an older stored tile (written before this change) is ignored too
    assert.equal(readPrefs(mem({ longTermPrefs: JSON.stringify({ mos: 50, tile: "NODATA" }) })).tile, null);
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
    // Re-pinned 2026-11-08 (LEAP setup): a sixth tile, SETUP, sits next to LEAPS and
    // counts the setupPass rows; none of these five rows is a setup, so SETUP is 0.
    assert.deepEqual(TILES, ["BUY", "LEAPS", "SETUP", "WAIT", "AVOID", "NODATA"]);
    assert.deepEqual(tileCounts([AAPL, MSFT, KO, NVDA, SPY]), { BUY: 2, LEAPS: 1, SETUP: 0, WAIT: 1, AVOID: 1, NODATA: 1 });
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
  test("checklist: the server's bare numbers read the way the threshold does", () => {
    const checks = [
      { key: "roic_now", label: "ROIC now", value: 0.44065335753176044, threshold: "≥ 15%", passed: true, detail: "" },
      { key: "roic_consistency", label: "ROIC consistency", value: 0.8, threshold: "≥ 10% in 80% of years", passed: true, detail: "" },
      { key: "sales_growth", label: "Sales growth", value: 0.16528715482540224, threshold: "≥ 10%/yr", passed: true, detail: "" },
      { key: "fcf_growth", label: "FCF growth", value: 0.0781, threshold: "≥ 10%/yr", passed: false, detail: "" },
      { key: "debt", label: "Debt payable", value: 0.44996223564954685, threshold: "debt ≤ 3× FCF, or no debt", passed: true, detail: "" },
      { key: "earning", label: "Earning money now", value: 17.9203, threshold: "EPS TTM > 0 and FCF TTM > 0", passed: true, detail: "" },
      { key: "history", label: "History and freshness", value: 10.0, threshold: "≥ 5 complete years, not stale", passed: true, detail: "" },
      { key: "eps_not_falling", label: "Earnings not falling", value: 1.0732068104372405, threshold: "EPS TTM ≥ 0.8 × last FY EPS", passed: true, detail: "" },
      { key: "sources", label: "Sources agree", value: null, threshold: "EDGAR and Finnhub agree", passed: null, detail: "ROIC 44% vs Finnhub 41% ok" },
    ];
    assert.deepEqual(drawerSections({ unit: "USD", checks }).map((x) => x.lines)[0], [
      "✓ ROIC now 44% ≥ 15%",
      "✓ ROIC consistency 80% ≥ 10% in 80% of years",
      "✓ Sales growth 17%/yr ≥ 10%/yr",
      "✗ FCF growth 8%/yr ≥ 10%/yr",
      "✓ Debt payable 0.4× FCF debt ≤ 3× FCF, or no debt",
      "✓ Earning money now EPS $17.92 EPS TTM > 0 and FCF TTM > 0",
      "✓ History and freshness 10 years ≥ 5 complete years, not stale",
      "✓ Earnings not falling 1.07× last FY EPS TTM ≥ 0.8 × last FY EPS",
      "n/a Sources agree — ROIC 44% vs Finnhub 41% ok",
    ]);
    // a foreign filer's EPS keeps its currency; an unknown numeric key prints as-is
    const s = drawerSections({ unit: "TWD", checks: [
      { key: "earning", label: "Earning money now", value: 45.25, threshold: "> 0", passed: true, detail: "" },
      { key: "other", label: "Other", value: 2.5, threshold: "?", passed: true, detail: "" },
    ] });
    assert.deepEqual(s[0].lines, ["✓ Earning money now EPS TWD 45.25 > 0", "✓ Other 2.5 ?"]);
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

describe("LEAP to buy", () => {
  // v2 (2026-11-06 15:20 ET): `contract` is the ENTRY-style pick (out of the money, delta
  // just under 0.20, highest open interest); `deep` is the Rule #1 stock substitute on the
  // same expiry. Numbers follow the Adobe check in the brief (price 240, $450 C / $200 C).
  const contract = {
    symbol: "ADBE  280115C00450000", expiry: "2028-01-15", dte: 434, strike: 450, bid: 8.7, ask: 8.9, mid: 8.8,
    delta: 0.16, openInterest: 1687, volume: 56, breakeven: 458.9, breakevenPct: 0.9121, intrinsic: 0,
    extrinsic: 8.9, cost: 890, wideSpread: false, thin: false, kind: "entry",
  };
  const deep = {
    symbol: "ADBE  280115C00200000", expiry: "2028-01-15", dte: 434, strike: 200, bid: 77.8, ask: 78.2, mid: 78,
    delta: 0.75, openInterest: 20672, volume: 312, breakeven: 278.2, breakevenPct: 0.1592, intrinsic: 40,
    extrinsic: 38.2, cost: 7820, wideSpread: false, thin: false, kind: "deep",
  };
  const NO_ENTRY = "no out-of-the-money call near 0.20 delta with a live quote";
  const NO_DELTA = "no deltas from the chain feed (hidden outside market hours)";
  const payload = (over = {}) => ({
    symbol: "ADBE", name: "Adobe Inc", leaps: true, price: 240, leapsBelow: 300, contract, deep, reason: "",
    expiries: ["2027-11-19", "2028-01-15"], fetchedAt: "2026-11-06T14:00:00Z", refreshedAt: "2026-11-06T03:12:00-05:00", ...over,
  });
  const DEEP_LINE = "$200 call · delta 0.75 · ask $78.20 · breakeven $278 (+16%) · OI 20,672";

  test("formatExpiry: short, long, invalid", () => {
    assert.equal(formatExpiry("2028-01-15"), "Jan 2028");
    assert.equal(formatExpiry("2028-01-15", { long: true }), "Jan 15, 2028");
    assert.equal(formatExpiry("2027-12-17T00:00:00"), "Dec 2027");
    assert.equal(formatExpiry("2028-13-01"), "—");
    assert.equal(formatExpiry("2028-02-30"), "—");
    assert.equal(formatExpiry("junk"), "—");
    assert.equal(formatExpiry(null), "—");
    assert.equal(formatExpiry(undefined, { long: true }), "—");
    assert.equal(formatExpiry(20280115), "—");
  });

  test("leapLine: one short line for the ENTRY contract (the deep pick stays out of it)", () => {
    assert.equal(leapLine(payload()), "Jan 2028 $450 C · ask $8.90 · BE $459 (+91%)");
    assert.equal(leapLine(payload({ contract: { ...contract, strike: 452.5, breakevenPct: -0.012, breakeven: 237.1 } })),
      "Jan 2028 $452.50 C · ask $8.90 · BE $237 (−1%)");
    assert.equal(leapLine(payload({ deep: null })), "Jan 2028 $450 C · ask $8.90 · BE $459 (+91%)", "deep never changes the ENTRY line");
  });

  test("leapLine: no ENTRY pick but a deep pick names both, shortly", () => {
    assert.equal(leapLine(payload({ contract: null, reason: NO_ENTRY })),
      `No ENTRY contract: ${NO_ENTRY} · deep Jan 2028 $200 C ask $78.20`);
    assert.equal(leapLine(payload({ contract: null, reason: "" })), "No ENTRY contract: — · deep Jan 2028 $200 C ask $78.20");
    const partial = leapLine(payload({ contract: null, reason: NO_ENTRY, deep: { strike: "200", ask: NaN } }));
    assert.equal(partial, `No ENTRY contract: ${NO_ENTRY} · deep — — C ask —`);
    assert.ok(!/NaN|undefined|null/.test(partial));
    assert.equal(leapLine(payload({ contract: null, reason: NO_ENTRY, deep: "junk" })), `No contract: ${NO_ENTRY}`, "a non-object deep is no deep");
  });

  test("leapLine: the after-hours no-delta answer shows the reason and the deep fallback, never a price for the ENTRY", () => {
    // Review 11-06 (finding 2): every greek is +/-999 after the close, so the server sends the
    // no-delta reason and a deep call picked by 0.80 x price with no delta.
    const line = leapLine(payload({ contract: null, reason: NO_DELTA, deep: { ...deep, delta: null } }));
    assert.equal(line, `No ENTRY contract: ${NO_DELTA} · deep Jan 2028 $200 C ask $78.20`);
    assert.ok(!/NaN|undefined|null/.test(line));
    assert.deepEqual(leapSection(payload({ contract: null, reason: NO_DELTA, deep: { ...deep, delta: null } })), [
      `No ENTRY contract: ${NO_DELTA}`,
      "Deep call (stock substitute):",
      "$200 call · delta — · ask $78.20 · breakeven $278 (+16%) · OI 20,672",
    ]);
  });

  test("leapLine: no contract at all says why, null payload is empty, missing numbers print the dash", () => {
    assert.equal(leapLine(payload({ contract: null, deep: null, reason: "no in-the-money call with a live quote" })),
      "No contract: no in-the-money call with a live quote");
    assert.equal(leapLine(payload({ contract: null, deep: null, reason: "" })), "No contract: —");
    assert.equal(leapLine(payload({ leaps: false, contract: null, deep: null, reason: "LEAPS not flagged" })), "No contract: LEAPS not flagged");
    assert.equal(leapLine(payload({ contract: null, reason: "chain unavailable", deep: undefined })), "No contract: chain unavailable", "v1 payload without a deep key");
    assert.equal(leapLine(null), "");
    assert.equal(leapLine(undefined), "");
    assert.equal(leapLine("junk"), "");
    const partial = leapLine(payload({ contract: { expiry: "2028-01-15" } }));
    assert.equal(partial, "Jan 2028 — C · ask — · BE — (—)");
    assert.ok(!/NaN|undefined|null/.test(partial));
    const bad = leapLine(payload({ contract: { strike: "200", ask: NaN, breakeven: Infinity, breakevenPct: "x" } }));
    assert.equal(bad, "— — C · ask — · BE — (—)");
  });

  test("leapSection: the ENTRY block, then the deep call on one line", () => {
    assert.deepEqual(leapSection(payload()), [
      "ENTRY-style · Jan 15, 2028 (434 days) · $450 call · delta 0.16 · OI 1,687",
      "Ask $8.90 · bid $8.70 · mid $8.80 · $890 per contract",
      "Breakeven $458.90 · +91% from $240",
      "In the money $0.00 · time value $8.90",
      "Open interest 1,687 · volume 56",
      "Deep call (stock substitute):",
      DEEP_LINE,
    ]);
  });

  test("leapSection: no deep pick says so on one line; no ENTRY pick keeps the deep block", () => {
    const none = leapSection(payload({ deep: null }));
    assert.equal(none.length, 6);
    assert.equal(none[5], "Deep call: none");
    assert.ok(!none.some((l) => l.includes("stock substitute")));
    assert.deepEqual(leapSection(payload({ deep: undefined })).slice(5), ["Deep call: none"], "v1 payload without a deep key");
    assert.deepEqual(leapSection(payload({ contract: null, reason: NO_ENTRY })), [
      `No ENTRY contract: ${NO_ENTRY}`,
      "Deep call (stock substitute):",
      DEEP_LINE,
    ]);
  });

  test("leapSection: a wide spread adds a warning line to the ENTRY block, and a flag to the deep line", () => {
    const wide = payload({ contract: { ...contract, bid: 7.9, ask: 8.9, mid: 8.4, wideSpread: true } });
    const lines = leapSection(wide);
    assert.equal(lines.length, 8);
    assert.equal(lines[5], "⚠ wide spread (ask 6.0% over mid)");
    assert.deepEqual(lines.slice(6), ["Deep call (stock substitute):", DEEP_LINE]);
    assert.equal(leapSection(payload({ contract: { ...contract, wideSpread: true, mid: null } }))[5], "⚠ wide spread");
    const deepWide = leapSection(payload({ deep: { ...deep, wideSpread: true } }));
    assert.equal(deepWide[6], `${DEEP_LINE} ⚠ wide spread`);
    const deepBoth = leapSection(payload({ deep: { ...deep, wideSpread: true, thin: true, openInterest: 3 } }));
    assert.equal(deepBoth[6], "$200 call · delta 0.75 · ask $78.20 · breakeven $278 (+16%) · OI 3 ⚠ thin ⚠ wide spread");
  });

  test("thin market: the line and the drawer warn, the contract stays", () => {
    const thin = payload({ contract: { ...contract, openInterest: 1, thin: true } });
    assert.ok(leapLine(thin).endsWith(" ⚠ thin"), leapLine(thin));
    assert.ok(!leapLine(payload()).includes("thin"));
    const lines = leapSection(thin);
    assert.equal(lines[5], "⚠ thin market: open interest 1 — use a limit order near the mid, and expect a wide exit");
    assert.equal(lines.length, 8);
    assert.deepEqual(lines.slice(6), ["Deep call (stock substitute):", DEEP_LINE]);
    assert.ok(!leapSection(payload()).some((l) => l.includes("thin")));
    const deepThin = leapSection(payload({ deep: { ...deep, openInterest: 12, thin: true } }));
    assert.equal(deepThin[6], "$200 call · delta 0.75 · ask $78.20 · breakeven $278 (+16%) · OI 12 ⚠ thin");
  });

  test("leapSection: no contract, null payload and missing numbers never leak", () => {
    assert.deepEqual(leapSection(payload({ contract: null, deep: null, reason: "no expiry 3-12 months out" })), ["No contract: no expiry 3-12 months out"]);
    assert.deepEqual(leapSection(payload({ contract: null, deep: null })), ["No contract: —"]);
    assert.deepEqual(leapSection(null), ["No contract: —"]);
    assert.deepEqual(leapSection({}), ["No contract: —"]);
    assert.deepEqual(leapSection({ contract: null, reason: "no chain", deep: "junk" }), ["No contract: no chain"]);
    const lines = leapSection(payload({ price: null, contract: { expiry: "2028-01-15", strike: 450 }, deep: { strike: 200 } }));
    assert.deepEqual(lines, [
      "ENTRY-style · Jan 15, 2028 (— days) · $450 call · delta — · OI —",
      "Ask — · bid — · mid — · — per contract",
      "Breakeven — · — from —",
      "In the money — · time value —",
      "Open interest — · volume —",
      "Deep call (stock substitute):",
      "$200 call · delta — · ask — · breakeven — (—) · OI —",
    ]);
    assert.ok(!/NaN|undefined|null/.test(lines.join("\n")));
  });

  test("leapOrder: flagged rows only, default order, capped at 12", () => {
    const flagged = (sym, extra = {}) => row({ symbol: sym, leaps: true, signal: "WAIT", buyZonePct: -0.1, ...extra });
    const many = Array.from({ length: 15 }, (_, i) => flagged(`T${String(i).padStart(2, "0")}`));
    const list = [KO, NVDA, flagged("ZZ", { signal: "BUY", buyZonePct: 0.2 }), ...many, AAPL, SPY, row({ symbol: "NO", leaps: "yes" })];
    const order = leapOrder(list);
    assert.equal(order.length, 12);
    assert.equal(order[0], "ZZ"); // BUY + LEAPS leads the default order
    assert.deepEqual(order.slice(1), many.slice(0, 11).map((r) => r.symbol));
    assert.ok(!order.includes("KO") && !order.includes("AAPL") && !order.includes("NO"));
    assert.deepEqual(leapOrder(null), []);
    assert.deepEqual(leapOrder([KO]), []);
  });

  test("leapTransient: only a chain-feed miss is retried, never a verdict on the row", () => {
    assert.equal(LEAP_NO_DELTA_REASON, NO_DELTA, "the backend's REASON_NO_DELTA, byte for byte");
    assert.deepEqual([...LEAP_TRANSIENT_REASONS].sort(), ["LEAPS not flagged", "chain unavailable", "no chain", NO_DELTA]);
    assert.equal(leapTransient(payload({ contract: null, reason: "chain unavailable" })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: "no chain" })), true);
    // Review 11-06 (finding 2): hidden greeks after the close are a feed state, retried; the deep
    // fallback riding along does not make the answer final.
    assert.equal(leapTransient(payload({ contract: null, reason: NO_DELTA })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: NO_DELTA, deep: { ...deep, delta: null } })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: NO_ENTRY })), false, "no call in the band is a verdict");
    assert.equal(leapTransient(payload({ contract: null, reason: " chain unavailable " })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: "no in-the-money call with a live quote" })), false);
    assert.equal(leapTransient(payload({ contract: null, reason: "no expiry 3-12 months out" })), false);
    // The page only asks for rows it saw flagged, so "not flagged" can only be a quote blip on the
    // server (review 11-06): retried, never shown as a verdict under a LEAPS pill.
    assert.equal(leapTransient(payload({ contract: null, reason: "LEAPS not flagged" })), true);
    assert.equal(leapTransientNote(payload({ contract: null, reason: "LEAPS not flagged" })),
      "The quote blinked and the row lost its LEAPS flag for one answer; the page retries on its own.");
    assert.equal(leapTransient(payload({ contract: null, reason: "" })), false);
    assert.equal(leapTransient(payload({ reason: "chain unavailable" })), false, "a contract is never transient");
    assert.equal(leapTransient(null), false);
    assert.equal(leapTransient("junk"), false);
    assert.equal(leapTransient({}), false);
  });

  test("leapTransientNote: names what the feed is doing and that the page retries", () => {
    const noDelta = leapTransientNote(payload({ contract: null, reason: NO_DELTA }));
    assert.equal(noDelta, "The chain feed is not sending deltas (Schwab hides them outside market hours); the page retries on its own.");
    assert.equal(leapTransientNote(payload({ contract: null, reason: ` ${NO_DELTA} ` })), noDelta, "trimmed");
    const silent = "The chain feed did not answer; the page retries on its own.";
    assert.equal(leapTransientNote(payload({ contract: null, reason: "chain unavailable" })), silent);
    assert.equal(leapTransientNote(payload({ contract: null, reason: "no chain" })), silent);
    assert.equal(leapTransientNote(null), silent);
    assert.equal(leapTransientNote({}), silent);
    assert.equal(leapTransientNote("junk"), silent);
  });

  test("leapRetryDue: 1 min, then doubling to 10 min; loading and settled entries never", () => {
    const at = 1_000_000;
    const t = (tries, elapsed) => leapRetryDue({ epoch: "50|x", transient: true, at, tries }, at + elapsed);
    assert.equal(LEAP_RETRY_MS, 60_000);
    assert.equal(LEAP_RETRY_MAX_MS, 600_000);
    assert.equal(t(1, 59_999), false);
    assert.equal(t(1, 60_000), true);
    assert.equal(t(2, 119_999), false);
    assert.equal(t(2, 120_000), true);
    assert.equal(t(3, 240_000), true);
    assert.equal(t(4, 479_999), false);
    assert.equal(t(5, 599_999), false);
    assert.equal(t(5, 600_000), true);
    assert.equal(t(9, 600_000), true, "capped at 10 minutes");
    assert.equal(t(0, 60_000), true, "a missing count means one try");
    assert.equal(t("x", 60_000), true);
    assert.equal(leapRetryDue({ epoch: "50|x", transient: true, at: "junk", tries: 1 }, at), true, "no stamp: ask again");
    assert.equal(leapRetryDue({ epoch: "50|x", transient: true, at, tries: 1, loading: true }, at + 999_999), false);
    assert.equal(leapRetryDue({ epoch: "50|x", payload: payload() }, at + 999_999), false);
    assert.equal(leapRetryDue({ epoch: "50|x", error: true }, at + 999_999), false);
    assert.equal(leapRetryDue(undefined, at), false);
    assert.equal(leapRetryDue(null, at), false);
  });
});

// ---- LEAP setup (2026-11-08) -------------------------------------------------
// Ganesh: "Build it 'leap setup'". A setup = the Monthly 50 EMA intramonth touch
// in an uptrend (his back-test's one edge) on a business the Long Term page rates
// BUY or WAIT. The server decides (row.setupPass); the page re-checks the signal
// after its own price guard and puts the three checks into words.
describe("LEAP setup", () => {
  // QCOM on Sun 11-08: monthly 50 level ~155.44, Nov low touched it, Oct closed above a rising EMA.
  const QSETUP = {
    level: 155.44, prevLevel: 153.9, lastClose: 165.2, lastMonth: "2026-10", uptrend: true, closeAbove: true,
    emaRising: true, month: "2026-11", monthLow: 152.1, touched: true, touchDate: "2026-11-05", pass: true,
    asOf: "2026-11-07", reason: null,
  };
  const QCOM = row({ symbol: "QCOM", name: "Qualcomm", signal: "WAIT", leaps: false, buyZonePct: -0.3, setup: QSETUP, setupPass: true });
  const setupRow = (o = {}, s = {}) => row({ symbol: "S", signal: "WAIT", setup: { ...QSETUP, ...s }, setupPass: true, ...o });

  test("isSetup: setupPass on a BUY or WAIT row only, never stale, never a guarded row", () => {
    assert.equal(isSetup(QCOM), true);
    assert.equal(isSetup(setupRow({ signal: "BUY" })), true);
    assert.equal(isSetup(setupRow({ signal: "AVOID" })), false, "AVOID is never a setup even if the server said so");
    assert.equal(isSetup(setupRow({ signal: "NO_PRICE" })), false);
    assert.equal(isSetup(setupRow({ signal: "ETF" })), false);
    assert.equal(isSetup(setupRow({ setupPass: false })), false);
    assert.equal(isSetup(setupRow({ setupPass: "true" })), false, "only a real true counts");
    assert.equal(isSetup(setupRow({}, { stale: true })), false, "a stale state is never a setup");
    assert.equal(isSetup(row({ setupPass: undefined })), false, "rows from before the change carry no field");
    assert.equal(isSetup(null), false);
    assert.equal(isSetup(guardRow({ ...QCOM, price: null })), false, "the price guard drops the setup with the signal");
    assert.equal(guardRow({ ...QCOM, price: null }).setupPass, false);
    assert.equal(guardRow(QCOM, { force: true }).setupPass, false, "a stale feed drops it too");
    assert.equal(guardRow(QCOM).setupPass, true);
  });

  test("tileCounts and the SETUP tile filter", () => {
    const list = [AAPL, MSFT, KO, NVDA, SPY, QCOM, setupRow({ symbol: "BAD", signal: "AVOID" })];
    const counts = tileCounts(list);
    assert.equal(counts.SETUP, 1);
    assert.equal(counts.WAIT, 2, "the setup row still counts as WAIT");
    assert.equal(counts.LEAPS, 1, "a setup is not a LEAPS flag");
    assert.deepEqual(filterRows(list, { tile: "SETUP" }).map((r) => r.symbol), ["QCOM"]);
    assert.deepEqual(filterRows(list, { tile: "SETUP", query: "qual" }).map((r) => r.symbol), ["QCOM"]);
    assert.deepEqual(filterRows(list, { tile: "SETUP", query: "AAPL" }), []);
    assert.deepEqual(filterRows(list, { tile: "LEAPS" }).map((r) => r.symbol), ["MSFT"]);
  });

  test("cardFields carries the setup flag for the SETUP pill", () => {
    assert.equal(cardFields(QCOM).setup, true);
    assert.equal(cardFields(KO).setup, false);
    assert.equal(cardFields(setupRow({ signal: "AVOID" })).setup, false);
  });

  test("leapOrder reads setup rows too, ahead of the LEAPS-only rows so the cap never drops them", () => {
    const flagged = (sym, extra = {}) => row({ symbol: sym, leaps: true, signal: "WAIT", buyZonePct: -0.1, ...extra });
    const many = Array.from({ length: 15 }, (_, i) => flagged(`T${String(i).padStart(2, "0")}`));
    const order = leapOrder([...many, KO, QCOM, setupRow({ symbol: "NOPE", signal: "AVOID" })]);
    assert.equal(order.length, 12);
    assert.equal(order[0], "QCOM");
    assert.deepEqual(order.slice(1), many.slice(0, 11).map((r) => r.symbol));
    assert.ok(!order.includes("NOPE") && !order.includes("KO"));
    assert.deepEqual(leapOrder([QCOM]), ["QCOM"], "a setup that is not LEAPS-flagged still gets its contract");
    assert.deepEqual(leapOrder([guardRow({ ...QCOM, price: null })]), [], "a guarded row is never read");
  });

  test("setupLines: QCOM-shaped pass", () => {
    const want = [
      "✓ Monthly 50 EMA touched: Nov low $152.10 ≤ level $155.44 (first touch Nov 5)",
      "✓ Uptrend: Oct closed $165.20 above the 50 EMA $155.44, which is rising (was $153.90)",
      "✓ Long Term: WAIT (BUY or WAIT needed)",
    ];
    assert.deepEqual(setupLines(QCOM), want, "a row: its setup and its signal");
    assert.deepEqual(setupLines(QSETUP, "WAIT"), want, "a detail.setup plus the row's signal");
    assert.equal(setupLines(QSETUP, "BUY")[2], "✓ Long Term: BUY (BUY or WAIT needed)");
  });

  test("setupLines: not touched this month says how far the level is under the low", () => {
    const lines = setupLines(setupRow({}, { touched: false, touchDate: null, monthLow: 158.2, pass: false }));
    assert.equal(lines[0], "✗ Monthly 50 EMA not touched this month: Nov low $158.20, level $155.44 (1.8% below the low)");
    assert.equal(lines.length, 3);
  });

  test("setupLines: no trading day yet this month", () => {
    const lines = setupLines(setupRow({}, { touched: false, touchDate: null, monthLow: null, pass: false, reason: "no trading day this month yet" }));
    assert.equal(lines[0], "✗ Monthly 50 EMA not touched this month: no trading day this month yet, level $155.44");
  });

  test("setupLines: close below the EMA, EMA falling", () => {
    const below = setupLines(setupRow({}, { lastClose: 150, closeAbove: false, uptrend: false, pass: false }));
    assert.equal(below[1], "✗ Uptrend: Oct closed $150.00 below the 50 EMA $155.44, which is rising (was $153.90)");
    const falling = setupLines(setupRow({}, { prevLevel: 156, emaRising: false, uptrend: false, pass: false }));
    assert.equal(falling[1], "✗ Uptrend: Oct closed $165.20 above the 50 EMA $155.44, which is falling (was $156.00)");
    // computed from the numbers when the booleans are missing
    const bare = setupLines(setupRow({}, { closeAbove: undefined, emaRising: undefined, uptrend: undefined }));
    assert.equal(bare[1], "✓ Uptrend: Oct closed $165.20 above the 50 EMA $155.44, which is rising (was $153.90)");
  });

  test("setupLines: the Long Term check fails for AVOID, No price and no-data rows", () => {
    assert.equal(setupLines(setupRow({ signal: "AVOID" }))[2], "✗ Long Term: AVOID (BUY or WAIT needed)");
    assert.equal(setupLines(setupRow({ signal: "NO_PRICE" }))[2], "✗ Long Term: No price (BUY or WAIT needed)");
    assert.equal(setupLines(QSETUP, "CHECK_DATA")[2], "✗ Long Term: Check data (BUY or WAIT needed)");
    assert.equal(setupLines(QSETUP)[2], "✗ Long Term: — (BUY or WAIT needed)", "no signal given: not passed");
  });

  test("setupLines: stale state and too little history give the reason", () => {
    const stale = setupLines(setupRow({ setupPass: false }, { stale: true, pass: false, asOf: "2026-11-02" }));
    assert.equal(stale[stale.length - 1], "⚠ Monthly bars stale: last read Nov 2, more than 4 days ago, so not a setup");
    assert.equal(stale.length, 4);
    const staleNoDate = setupLines(setupRow({}, { stale: true, asOf: null }));
    assert.equal(staleNoDate[3], "⚠ Monthly bars stale: not read in the last 4 days, so not a setup");
    // recheck 11-08: stale only because the month rolled over (bars read 3 days ago) - the
    // server's staleReason says so; "more than 4 days ago" would be false
    const rolled = setupLines(setupRow({ setupPass: false }, {
      stale: true, pass: false, month: "2026-10", asOf: "2026-10-30", staleReason: "stale: state is for 2026-10, not this month",
    }));
    assert.equal(rolled[rolled.length - 1], "⚠ These checks are for October, not this month, so not a setup");
    const young = setupLines(setupRow({ setupPass: false }, {
      level: null, prevLevel: null, lastClose: null, uptrend: null, closeAbove: null, emaRising: null, touched: null,
      touchDate: null, monthLow: null, pass: null, reason: "fewer than 100 months of history",
    }));
    assert.deepEqual(young, ["n/a Monthly 50 EMA: fewer than 100 months of history", "✓ Long Term: WAIT (BUY or WAIT needed)"]);
    assert.equal(setupLines(setupRow({}, { level: null, reason: null }))[0], "n/a Monthly 50 EMA: no monthly data yet");
  });

  test("setupLines: nothing for no setup, never NaN or undefined for junk", () => {
    assert.deepEqual(setupLines(row({})), []);
    assert.deepEqual(setupLines(null), []);
    assert.deepEqual(setupLines(undefined, "WAIT"), []);
    assert.deepEqual(setupLines("junk", "WAIT"), []);
    const junk = setupLines({ level: 155.44, month: "x", lastMonth: 7, touchDate: "nope", monthLow: "a", prevLevel: null, lastClose: undefined, touched: true }, "WAIT");
    assert.ok(junk.length >= 2);
    assert.ok(!/NaN|undefined|null/.test(junk.join("\n")), junk.join("\n"));
  });

  test("the back-test note is the plain sentence, ending Not advice", () => {
    assert.match(SETUP_NOTE, /^Back-test 2005-2026 \(model option prices\): buying on this touch won 60% over 6 months vs 54% on any day\./);
    assert.match(SETUP_NOTE, /20% ITM: 64%/);
    assert.match(SETUP_NOTE, /cut wins to about 45%\. Not advice\.$/);
  });
});
