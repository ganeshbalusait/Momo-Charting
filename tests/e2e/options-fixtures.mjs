// Shared fixtures for the options group's end-to-end specs (2026-11-04).
//
// optionChain() is a deterministic /api/oi-finder-chain payload in the shape
// api_server sends (see tests/test_cov_options_chain_payload_api.py, which
// pins the server side of the same shape). Specs answer the chain read with
// it so row counts, ATM rows and strikes can be asserted exactly.
//
// CHAIN_READS also answers two live feeds the chain screens open:
//  * /api/live-market-stream with 503: a live SPY/AAPL tick would otherwise
//    move the fixture's spot price mid-assertion.
//  * /api/live-option-stream (the chain's contract lease): through the live
//    gateway this plain GET is treated as an event stream that never ends and
//    re-sent in a tight loop (tests/test_cov_options_stream_api.py, xfail) -
//    a spec must never trigger that on the trader's running app.

import { OI_CHART_APPLIED_GRID_STORAGE_KEY, chartGridStamp, readChartGridMeta } from "../../frontend/src/chartWorkspaceState.js";
import { BASE_URL } from "./harness.mjs";

export const SPOT = 100;
export const STRIKES = Array.from({ length: 41 }, (_, index) => 80 + index);   // 80..120
export const DTES = [2, 9, 16];
const CALL_WALLS = { 110: 25_000, 105: 18_000, 108: 9_000 };
const PUT_WALLS = { 90: 24_000, 95: 17_000, 92: 8_000 };
// The last (monthly) expiry holds the biggest 110 call wall, so "to monthly"
// shows a later expiry in the High OI board's Exp column and "front expiry" does not.
const LAST_EXPIRY_CALL_WALLS = { 110: 40_000 };

// YYYY-MM-DD in New York, `days` from today.
export function expiryIn(days, now = new Date()) {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(now);
  const [year, month, day] = today.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

export function occ(symbol, expiry, side, strike) {
  return `${symbol.padEnd(6, " ")}${expiry.slice(2, 4)}${expiry.slice(5, 7)}${expiry.slice(8, 10)}${side === "PUT" ? "P" : "C"}${String(Math.round(strike * 1000)).padStart(8, "0")}`;
}

const round2 = (value) => Math.round(value * 100) / 100;
export const markFor = (side, strike, dte, spot = SPOT) => {
  const moneyness = side === "CALL" ? strike - spot : spot - strike;
  return round2(Math.max(0.05, 4 - moneyness * 0.4 + dte * 0.05));
};
export const expectedMoveFor = (dte) => round2(markFor("CALL", SPOT, dte) + markFor("PUT", SPOT, dte));

export function optionChain(rawSymbol = "SPY", { spot = SPOT, dtes = DTES } = {}) {
  const symbol = String(rawSymbol || "SPY").trim().toUpperCase();
  const expiries = dtes.map((dte) => expiryIn(dte));
  const rows = [];
  const tosScriptLevels = [];
  expiries.forEach((expiry, index) => {
    const dte = dtes[index];
    const em = expectedMoveFor(dte);
    const isLast = index === dtes.length - 1;
    const callOi = (strike) => (isLast && LAST_EXPIRY_CALL_WALLS[strike]) || CALL_WALLS[strike] || 400 + strike;
    const putOi = (strike) => PUT_WALLS[strike] || 300 + strike;
    const topCalls = new Set(STRIKES.filter((strike) => strike > spot).sort((a, b) => callOi(b) - callOi(a)).slice(0, 5));
    const topPuts = new Set(STRIKES.filter((strike) => strike <= spot).sort((a, b) => putOi(b) - putOi(a)).slice(0, 5));
    for (const strike of STRIKES) {
      for (const side of ["CALL", "PUT"]) {
        const moneyness = side === "CALL" ? strike - spot : spot - strike;
        const mark = markFor(side, strike, dte, spot);
        rows.push({
          symbol: occ(symbol, expiry, side, strike), side, expiry, days_to_expiration: dte, strike,
          bid: round2(Math.max(0, mark - 0.05)), ask: round2(mark + 0.05), last: mark, mark,
          delta: round2(Math.min(0.98, Math.max(0.02, 0.5 - moneyness * 0.04))), gamma: 0.03, theta: -0.1, vega: 0.08,
          volume: 100 + strike, open_interest: side === "CALL" ? callOi(strike) : putOi(strike),
          is_atm: strike === spot, expected_move: em,
          is_high_open_interest: side === "CALL" ? topCalls.has(strike) : topPuts.has(strike),
          is_high_volume: false,
        });
      }
    }
    const levels = (side, oi, keep) => STRIKES.filter(keep).sort((a, b) => oi(b) - oi(a)).slice(0, 5)
      .map((strike) => ({ strike, openInterest: oi(strike), volume: 100 + strike, delta: 0.3 }));
    tosScriptLevels.push({
      expiry, daysToExpiration: dte,
      callLevels: levels("CALL", callOi, () => true),
      putLevels: levels("PUT", (s) => PUT_WALLS[s] || 300 + s, () => true),
    });
  });
  const front = expiries[0];
  return {
    symbol, live: true, stale: false, cached: false, warming: false, refreshing: false,
    optionQuoteSchemaVersion: 1, source: "Schwab/TOS option chain", sourceNote: "",
    scannedAt: new Date().toISOString(), refreshSeconds: 15, maxDaysToExpiration: 31,
    underlyingPrice: spot, todayChange: 1.25, todayChangePercent: 1.27, impliedVolatility: 28.5,
    expiries, expiryExpectedMoves: Object.fromEntries(expiries.map((expiry, index) => [expiry, expectedMoveFor(dtes[index])])),
    currentAtm: {
      expiry: front, daysToExpiration: dtes[0], expectedMove: expectedMoveFor(dtes[0]),
      call: { strike: spot, volume: 100 + spot, openInterest: 400 + spot },
      put: { strike: spot, volume: 100 + spot, openInterest: 300 + spot },
    },
    selectedExpiryChainRows: rows, tosScriptLevels, callRows: [], putRows: [],
    volumeMomentum: {}, dailyLiquidityHeatmap: {}, unusualOtmActivity: {}, unusualOtmDashboard: {},
    persistentActivity: {}, liveWallTrend: {}, summary: {}, errors: [],
  };
}

// The double-failure answer api_server gives for a ticker nobody can quote.
export const NO_CHAIN_ERROR = "Schwab/TOS failed (no option chain for ZZZZQ); the Tradier fallback also failed (no option chain for ZZZZQ).";

export const symbolOf = (read) => new URLSearchParams(read.search).get("symbol") || "SPY";

export function chainReply(read, overrides = {}) {
  const symbol = symbolOf(read).toUpperCase();
  if (symbol === "ZZZZQ") {
    return { json: { live: false, symbol, source: "Tradier option chain (Schwab/TOS fallback)", callRows: [], putRows: [],
      errors: [{ error: NO_CHAIN_ERROR }], scannedAt: new Date().toISOString() } };
  }
  return { json: { ...optionChain(symbol), ...overrides } };
}

export const CHAIN_READS = {
  "GET /api/oi-finder-chain": (read) => chainReply(read),
  "GET /api/live-market-stream": { status: 503, json: { error: "live stream stubbed by the options e2e fixture" } },
  "GET /api/live-option-stream": (read) => {
    const contracts = (new URLSearchParams(read.search).get("contracts") || "").split(",").filter(Boolean);
    return { json: { symbol: symbolOf(read).toUpperCase(), requested: contracts.length, subscribed: contracts.length, streamConnected: false, streamError: "" } };
  },
};

// Charts & OI: mark the saved default grid as applied (the harness only does
// this for other pages), so one chart opens instead of the saved multi-chart
// grid. Same approach as markets-chain-dock.e2e.test.mjs.
export async function chartStorage(extra = {}) {
  const storage = { ...extra };
  try {
    const response = await fetch(`${BASE_URL}/api/chart-grids`);
    const { defaultGrid } = readChartGridMeta((await response.json())?.grids);
    if (defaultGrid) storage[OI_CHART_APPLIED_GRID_STORAGE_KEY] = chartGridStamp(defaultGrid.name, defaultGrid.savedAt);
  } catch {
    // No grid endpoint answer: nothing to mark.
  }
  return storage;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
