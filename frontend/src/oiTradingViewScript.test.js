import test from "node:test";
import assert from "node:assert/strict";
import {
  buildTradingViewOiScript,
  buildTradingViewOiSnapshot,
  normalizeTradingViewSymbols,
} from "./oiTradingViewScript.js";

test("normalizes an unlimited comma, space, or newline separated ticker list", () => {
  assert.deepEqual(
    normalizeTradingViewSymbols("spy, QQQ\nPLTR  now;SPY invalid$"),
    ["SPY", "QQQ", "PLTR", "NOW"],
  );
});

test("attributes each strike to its dominant windowed expiry by default", () => {
  const snapshot = buildTradingViewOiSnapshot({
    symbol: "GOOGL",
    scannedAt: "2026-07-29T20:00:00Z",
    currentAtm: { expiry: "2026-07-29", call: { strike: 335 } },
    tosScriptLevels: [
      {
        expiry: "2026-07-31",
        daysToExpiration: 2,
        atmStrike: 335,
        callLevels: [
          { strike: 400, openInterest: 12_245, volume: 100 },
          { strike: 335, openInterest: 1_000, volume: 50 },
        ],
        putLevels: [],
      },
      {
        expiry: "2026-07-29",
        daysToExpiration: 0,
        atmStrike: 335,
        callLevels: [{ strike: 335, openInterest: 2_835, volume: 33_973 }],
        putLevels: [{ strike: 330, openInterest: 7_997, volume: 907 }],
      },
    ],
  });

  assert.equal(snapshot.frontExpiry, "2026-07-29");
  assert.equal(snapshot.lastExpiry, "2026-07-31");
  assert.equal(snapshot.expiryCount, 2);
  assert.equal(snapshot.expiryRange, "07-29→07-31");
  assert.equal(snapshot.maxDaysToExpiration, 2);
  // 335 calls exist on both expiries (2,835 on 07-29 vs 1,000 on 07-31); the
  // level keeps the dominant expiry's own OI instead of a cross-expiry sum.
  const dominant = snapshot.callLevels.find((level) => level.strike === 335);
  assert.equal(dominant.openInterest, 2_835);
  assert.equal(dominant.volume, 33_973);
  assert.deepEqual(dominant.expiries, ["2026-07-29"]);
  assert.equal(snapshot.callLevels.some((level) => level.strike === 400), true);
});

test("expiry scope pins the chart front expiry without combining strikes across expiries", () => {
  const snapshot = buildTradingViewOiSnapshot({
    symbol: "GOOGL",
    scannedAt: "2026-07-29T20:00:00Z",
    currentAtm: { expiry: "2026-07-29", call: { strike: 335 } },
    tosScriptLevels: [
      {
        expiry: "2026-07-31",
        daysToExpiration: 2,
        atmStrike: 335,
        callLevels: [{ strike: 400, openInterest: 12_245, volume: 100 }],
        putLevels: [],
      },
      {
        expiry: "2026-07-29",
        daysToExpiration: 0,
        atmStrike: 335,
        callLevels: [
          { strike: 335, openInterest: 2_835, volume: 33_973 },
          { strike: 345, openInterest: 2_358, volume: 11_170 },
          { strike: 340, openInterest: 2_005, volume: 33_326 },
          { strike: 330, openInterest: 1_995, volume: 3_305 },
          { strike: 342.5, openInterest: 1_620, volume: 18_016 },
        ],
        putLevels: [{ strike: 330, openInterest: 7_997, volume: 907 }],
      },
    ],
  }, { scope: "expiry" });

  assert.equal(snapshot.frontExpiry, "2026-07-29");
  assert.equal(snapshot.lastExpiry, "2026-07-29");
  assert.equal(snapshot.expiryCount, 1);
  assert.equal(snapshot.atmStrike, 335);
  assert.equal(snapshot.callLevels.find((level) => level.strike === 342.5).strength, "moderate");
  assert.equal(snapshot.callLevels.find((level) => level.strike === 342.5).color, "#84cc16");
  assert.deepEqual(snapshot.callLevels.find((level) => level.strike === 342.5).expiries, ["2026-07-29"]);
  assert.equal(snapshot.callLevels.some((level) => level.strike === 400), false);
});

test("keeps all fifteen per-side chart tiers in the exported snapshot", () => {
  const calls = Array.from({ length: 18 }, (_, index) => ({
    strike: 100 + index,
    openInterest: 30_000 - index * 1_000,
    volume: 1_000 - index,
  }));
  const snapshot = buildTradingViewOiSnapshot({
    symbol: "AAPL",
    currentAtm: { expiry: "2026-07-31", call: { strike: 100 } },
    tosScriptLevels: [{
      expiry: "2026-07-31",
      daysToExpiration: 2,
      atmStrike: 100,
      callLevels: calls,
      putLevels: [],
    }],
  });

  assert.equal(snapshot.callLevels.length, 15);
  assert.deepEqual(snapshot.callLevels.map((level) => level.strike), calls.slice(0, 15).map((level) => level.strike));
});

test("generates one Pine v6 script that auto-detects every supplied ticker", () => {
  const googl = buildTradingViewOiSnapshot({
    symbol: "GOOGL",
    currentAtm: { expiry: "2026-07-29", call: { strike: 335 } },
    tosScriptLevels: [{
      expiry: "2026-07-29",
      daysToExpiration: 0,
      atmStrike: 335,
      callLevels: [
        { strike: 335, openInterest: 2_835, volume: 33_973 },
        { strike: 342.5, openInterest: 1_620, volume: 18_016 },
      ],
      putLevels: [{ strike: 330, openInterest: 7_997, volume: 907 }],
    }],
  });
  const pltr = buildTradingViewOiSnapshot({
    symbol: "PLTR",
    currentAtm: { expiry: "2026-07-31", call: { strike: 160 } },
    tosScriptLevels: [{
      expiry: "2026-07-31",
      daysToExpiration: 2,
      atmStrike: 160,
      callLevels: [{ strike: 165, openInterest: 8_000, volume: 4_000 }],
      putLevels: [{ strike: 155, openInterest: 6_000, volume: 3_000 }],
    }],
  });
  const script = buildTradingViewOiScript([googl, pltr]);

  assert.match(script, /^\/\/@version=6/);
  assert.match(script, /render_GOOGL_1\(\) =>/);
  assert.match(script, /render_PLTR_2\(\) =>/);
  assert.match(script, /if barstate\.islast and syminfo\.ticker == "GOOGL"\n    clearDrawings\(\)\n    render_GOOGL_1\(\)/);
  assert.match(script, /if barstate\.islast and syminfo\.ticker == "PLTR"\n    clearDrawings\(\)\n    render_PLTR_2\(\)/);
  assert.doesNotMatch(script, /^if barstate\.islast$/m);
  assert.match(script, /drawLevel\(showCalls, 342\.5, #84cc16, 2, 0, "C OI MODERATE 342\.5 · 1\.6K OI · 18K Vol · 07-29"/);
  assert.match(script, /drawLevel\(showATM, 335, #facc15/);
  assert.equal((script.match(/indicator\(/g) || []).length, 1);
});

test("keeps the Quick 13 out of one oversized barstate block", () => {
  const symbols = ["SPY", "QQQ", "SLV", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO"];
  const snapshots = symbols.map((symbol) => ({
    symbol,
    expiry: "2026-07-31",
    expiryRange: "07-31→08-28",
    maxDaysToExpiration: 29,
    atmStrike: 100,
    callLevels: [{
      strike: 105,
      openInterest: 10_000,
      volume: 5_000,
      sideShort: "C",
      strength: "strong",
      color: "#22c55e",
      lineWidth: 4,
      lineStyle: 0,
      expiries: ["2026-07-31"],
    }],
    putLevels: [{
      strike: 95,
      openInterest: 8_000,
      volume: 4_000,
      sideShort: "P",
      strength: "strong",
      color: "#ef4444",
      lineWidth: 4,
      lineStyle: 0,
      expiries: ["2026-07-31"],
    }],
  }));
  const script = buildTradingViewOiScript(snapshots);

  assert.equal((script.match(/^render_[A-Z0-9_]+_\d+\(\) =>$/gm) || []).length, 13);
  assert.equal((script.match(/^if barstate\.islast and syminfo\.ticker == /gm) || []).length, 13);
  assert.doesNotMatch(script, /^if barstate\.islast$/m);
});
