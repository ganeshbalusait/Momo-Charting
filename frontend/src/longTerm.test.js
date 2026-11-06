// Long Term page logic: Buffett / Rule #1 value screen.
import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  MOS_OPTIONS, PREFS_KEY, readPrefs, writePrefs, TILES, tileCounts, priceGuard, guardRow,
  filterRows, DEFAULT_SORT, sortRows, SIGNAL_LABEL, signalTone, formatMoney, formatPct,
  formatCap, formatGrowth, buyZoneBar, columns, cardFields, drawerSections,
  reasonText, big5Dots, formatEtStamp, acceptsPayload, sortValue, etDate, previousWeekday, formatEps,
  formatPayback, formatExpiry, leapLine, leapSection, leapOrder,
  leapTransient, leapRetryDue, LEAP_RETRY_MS, LEAP_RETRY_MAX_MS, LEAP_TRANSIENT_REASONS,
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
  const contract = {
    symbol: "AAPL  280115C00200000", expiry: "2028-01-15", dte: 434, strike: 200, bid: 61.9, ask: 62.4, mid: 62.15,
    delta: 0.75, openInterest: 1234, volume: 56, breakeven: 262.4, breakevenPct: 0.0933, intrinsic: 40,
    extrinsic: 22.4, cost: 6240, wideSpread: false,
  };
  const payload = (over = {}) => ({
    symbol: "AAPL", name: "Apple Inc", leaps: true, price: 240, leapsBelow: 300, contract, reason: "",
    expiries: ["2027-11-19", "2028-01-15"], fetchedAt: "2026-11-06T14:00:00Z", refreshedAt: "2026-11-06T03:12:00-05:00", ...over,
  });

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

  test("leapLine: one short line for the contract", () => {
    assert.equal(leapLine(payload()), "Jan 2028 $200 C · ask $62.40 · BE $262 (+9%)");
    assert.equal(leapLine(payload({ contract: { ...contract, strike: 202.5, breakevenPct: -0.012, breakeven: 237.1 } })),
      "Jan 2028 $202.50 C · ask $62.40 · BE $237 (−1%)");
  });

  test("leapLine: no contract says why, null payload is empty, missing numbers print the dash", () => {
    assert.equal(leapLine(payload({ contract: null, reason: "no in-the-money call with a live quote" })),
      "No contract: no in-the-money call with a live quote");
    assert.equal(leapLine(payload({ contract: null, reason: "" })), "No contract: —");
    assert.equal(leapLine(payload({ leaps: false, contract: null, reason: "LEAPS not flagged" })), "No contract: LEAPS not flagged");
    assert.equal(leapLine(null), "");
    assert.equal(leapLine(undefined), "");
    assert.equal(leapLine("junk"), "");
    const partial = leapLine(payload({ contract: { expiry: "2028-01-15" } }));
    assert.equal(partial, "Jan 2028 — C · ask — · BE — (—)");
    assert.ok(!/NaN|undefined|null/.test(partial));
    const bad = leapLine(payload({ contract: { strike: "200", ask: NaN, breakeven: Infinity, breakevenPct: "x" } }));
    assert.equal(bad, "— — C · ask — · BE — (—)");
  });

  test("leapSection: the drawer lines", () => {
    assert.deepEqual(leapSection(payload()), [
      "Jan 15, 2028 (434 days) · $200 call · delta 0.75",
      "Ask $62.40 · bid $61.90 · mid $62.15 · $6,240 per contract",
      "Breakeven $262.40 · +9% from $240",
      "In the money $40.00 · time value $22.40",
      "Open interest 1,234 · volume 56",
    ]);
  });

  test("leapSection: a wide spread adds a warning line", () => {
    const wide = payload({ contract: { ...contract, bid: 55, ask: 62.4, mid: 58.7, wideSpread: true } });
    const lines = leapSection(wide);
    assert.equal(lines.length, 6);
    assert.equal(lines[5], "⚠ wide spread (ask 6.3% over mid)");
    assert.equal(leapSection(payload({ contract: { ...contract, wideSpread: true, mid: null } }))[5], "⚠ wide spread");
  });

  test("thin market: the line and the drawer warn, the contract stays", () => {
    const thin = payload({ contract: { ...contract, openInterest: 1, thin: true } });
    assert.ok(leapLine(thin).endsWith(" ⚠ thin"), leapLine(thin));
    assert.ok(!leapLine(payload()).includes("thin"));
    const lines = leapSection(thin);
    assert.equal(lines[lines.length - 1], "⚠ thin market: open interest 1 — use a limit order near the mid, and expect a wide exit");
    assert.equal(lines.length, 6);
    assert.ok(!leapSection(payload()).some((l) => l.includes("thin")));
  });

  test("leapSection: no contract, null payload and missing numbers never leak", () => {
    assert.deepEqual(leapSection(payload({ contract: null, reason: "no expiry 12-25 months out" })), ["No contract: no expiry 12-25 months out"]);
    assert.deepEqual(leapSection(payload({ contract: null })), ["No contract: —"]);
    assert.deepEqual(leapSection(null), ["No contract: —"]);
    assert.deepEqual(leapSection({}), ["No contract: —"]);
    const lines = leapSection(payload({ price: null, contract: { expiry: "2028-01-15", strike: 200 } }));
    assert.deepEqual(lines, [
      "Jan 15, 2028 (— days) · $200 call · delta —",
      "Ask — · bid — · mid — · — per contract",
      "Breakeven — · — from —",
      "In the money — · time value —",
      "Open interest — · volume —",
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
    assert.deepEqual([...LEAP_TRANSIENT_REASONS].sort(), ["chain unavailable", "no chain"]);
    assert.equal(leapTransient(payload({ contract: null, reason: "chain unavailable" })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: "no chain" })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: " chain unavailable " })), true);
    assert.equal(leapTransient(payload({ contract: null, reason: "no in-the-money call with a live quote" })), false);
    assert.equal(leapTransient(payload({ contract: null, reason: "no expiry 12-25 months out" })), false);
    assert.equal(leapTransient(payload({ contract: null, reason: "LEAPS not flagged" })), false);
    assert.equal(leapTransient(payload({ contract: null, reason: "" })), false);
    assert.equal(leapTransient(payload({ reason: "chain unavailable" })), false, "a contract is never transient");
    assert.equal(leapTransient(null), false);
    assert.equal(leapTransient("junk"), false);
    assert.equal(leapTransient({}), false);
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
