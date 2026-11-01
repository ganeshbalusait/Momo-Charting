import test from "node:test";
import assert from "node:assert/strict";
import { fivePillars, trendPillar, squeezePillar, structurePillar, targetPillar, signalPillar, estimatedRoi } from "./momxPillars.js";

const NOW = Date.parse("2026-09-25T10:15:00-04:00");
const sec = (iso) => Math.floor(Date.parse(iso) / 1000);

const PYPL = {
  symbol: "PYPL",
  last: 53.4,
  m5: {
    crossUpAt: sec("2026-09-25T10:05:00-04:00"),
    pillars: {
      ribbon30m: { trend: "bull" },
      ribbon5m: { trend: "bull" },
      macdCrossUpAt: sec("2026-09-25T10:00:00-04:00"),
      stoch: { k: 70, d: 60 },
      prevHigh: 52.67,
      premarketHigh: 52.8,
    },
  },
  sqz: { "2h": { bg: "cyan" }, "4h": { bg: "orange" } },
};
// The BOT's contract: nearest OTM strike on the first expiry after today, at the mid.
const CHAIN_ROWS = [
  { side: "CALL", expiry: "2026-09-26", days_to_expiration: 1, strike: 54, bid: 0.9, ask: 1.1, delta: 0.42 },
  { side: "CALL", expiry: "2026-09-26", days_to_expiration: 1, strike: 56, bid: 0.3, ask: 0.4, delta: 0.18 },
  { side: "CALL", expiry: "2026-09-25", days_to_expiration: 0, strike: 54, bid: 0.2, ask: 0.3, delta: 0.3 },   // today - skipped
  { side: "PUT", expiry: "2026-09-26", days_to_expiration: 1, strike: 50, bid: 0.8, ask: 1.0, delta: -0.35 },
  { side: "PUT", expiry: "2026-09-26", days_to_expiration: 1, strike: 52, bid: 1.4, ask: 1.6, delta: -0.45 },
];
const CHAIN = { underlyingPrice: 53.4, todayChange: 1.03, expiries: ["2026-09-25", "2026-09-26"],
  expiryExpectedMoves: { "2026-09-25": 1.9 }, currentAtm: { expectedMove: 1.9 }, selectedExpiryChainRows: CHAIN_ROWS };
const WALLS = { calls: [{ strike: 55, openInterest: 47000 }, { strike: 54, openInterest: 12000 }] };

test("PYPL at 10:15 on 2026-09-25: all five pillars pass -> READY", () => {
  const out = fivePillars(PYPL, { chain: CHAIN, walls: WALLS, nowMs: NOW });
  assert.deepEqual(out.rows.map((r) => r.pass), [true, true, true, true, true]);
  assert.equal(out.ready, true);
  assert.match(out.rows[3].text, /54C wall/);
  assert.match(out.rows[4].text, /9x20 cross up 5m/);
});

test("trend: bear or mixed ribbon fails, missing data waits", () => {
  assert.equal(trendPillar({ m5: { pillars: { ribbon30m: { trend: "bear" } } } }).pass, false);
  assert.equal(trendPillar({ m5: { pillars: { ribbon30m: { trend: "mixed" } } } }).pass, false);
  assert.equal(trendPillar({}).pass, null);
});

test("squeeze: on or fired up passes; fired down or none fails", () => {
  assert.equal(squeezePillar({ sqz: { "2h": { bg: "white" } } }).pass, true);
  assert.equal(squeezePillar({ sqz: { "2h": { bg: "magenta" }, "4h": { bg: "black" } } }).pass, false);
  assert.equal(squeezePillar({}).pass, null);
});

test("structure: his rule - the BOT's option must return >= +100% at 2x the expected move", () => {
  // 53.4 + 2 x 1.9 = 57.20; 54C at the 1.00 mid is worth 3.20 there = +220%
  const ok = structurePillar({ last: 53.4 }, CHAIN);
  assert.equal(ok.pass, true);
  assert.match(ok.text, /2×EM top \$57\.20 · Δ0\.42 54C \$1\.00 → \+220% at 2×EM/);
  // the same strike at a 2.00 mid is worth 3.20 = +60%: under the rule
  const dear = { ...CHAIN, selectedExpiryChainRows: [{ ...CHAIN_ROWS[0], bid: 1.9, ask: 2.1 }] };
  const out = structurePillar({ last: 53.4 }, dear);
  assert.equal(out.pass, false);
  assert.match(out.text, /\+60% at 2×EM - under your \+100% rule/);
  assert.equal(structurePillar({ last: 53 }, null).pass, null);
});

test("target: nearest level above must be at least 1% away", () => {
  assert.equal(targetPillar({ last: 54.8, m5: { pillars: {} } }, WALLS).pass, false); // 55C only +0.4%
  assert.equal(targetPillar({ last: 40, m5: { pillars: {} } }, null).text.includes("psych level $45.00"), true);
});

test("signal: nothing in the last 30 minutes fails; overbought alone is not a buy", () => {
  const stale = { m5: { crossUpAt: sec("2026-09-25T09:00:00-04:00"), pillars: { stoch: { k: 90, d: 85 } } } };
  const out = signalPillar(stale, NOW);
  assert.equal(out.pass, false);
  assert.match(out.text, /overbought/);
});


// 2026-10-03: rebuilt to the MomoX definitions he pasted, puts included.
test("trend counts the ribbons and the higher-timeframe Skittles with the trade, and the Fib retracement", () => {
  const row = { last: 98, highLow: { hh: 100, ll: 90 }, m5: { pillars: { ribbon5m: { trend: "bull" }, ribbon30m: { trend: "bull" } } },
    skittles: { "2h": { bg: "cyan" }, "4h": { bg: "green" }, D: { bg: "magenta" }, Wk: { bg: "black", fg: "cyan" } } };
  const out = trendPillar(row, "bull");
  assert.equal(out.pass, true);
  assert.match(out.text, /5 of 6 with the call/);
  assert.match(out.text, /retraced 20% of today's range \(near Fib 23.6%\)/);
  assert.equal(trendPillar(row, "bear").pass, false);
});

test("squeeze: contraction (high / mid) or a fire WITH the trade; puts read magenta", () => {
  assert.match(squeezePillar({ sqzFast: { "30m": { bg: "cyan" } }, sqz: { D: { bg: "white" } } }, "bull").text,
    /contraction: D high squeeze · expansion: 30m fired ↑/);
  assert.equal(squeezePillar({ sqz: { "2h": { bg: "magenta" } } }, "bear").pass, true);
  assert.equal(squeezePillar({ sqz: { "2h": { bg: "magenta" } } }, "bull").pass, false);
});

test("structure: critical time, strength vs weakness, and the expected move on the put side", () => {
  const out = structurePillar({ last: 51, pctChange: -2.5 }, CHAIN, "bear", Date.parse("2026-09-25T15:20:00-04:00"));
  assert.match(out.text, /time: power hour · weakness -2.5% today \(with the put\)/);
  assert.equal(out.pass, true);                       // 51 - 3.8 = 47.20; 50P at 0.90 worth 2.80 = +211%
  assert.match(out.text, /2×EM bottom \$47\.20 · Δ0\.35 50P \$0\.90 → \+211% at 2×EM/);
  assert.equal(structurePillar({ last: 53, pctChange: 1 }, CHAIN, "bear").pass, false);   // strength = wrong side for a put
});

test("targets: 5% psych levels from yesterday's close and put walls below", () => {
  const up = targetPillar({ last: 103, prevClose: 100, m5: { pillars: {} } }, null, "bull");
  assert.match(up.text, /^psych level \$105\.00 \(\+1\.9%\) · then \+5% level \$105\.00/);   // $5 steps above $25
  const down = targetPillar({ last: 60, m5: { pillars: {}, dayLine: { prevLow: 58 } } }, { puts: [{ strike: 57 }] }, "bear");
  assert.equal(down.pass, true);
  assert.match(down.text, /^prior-day low \$58\.00 \(-3\.3%\) · then 57P wall/);
});

test("signals: put crosses, PUT arrows only, RVOL adds to the stack but is not a trigger alone", () => {
  const now = Date.parse("2026-09-25T10:15:00-04:00");
  const bear = { m5: { crossDownAt: sec("2026-09-25T10:05:00-04:00"), pillars: {} }, rvol: { "5m": { bg: "magenta" } } };
  const out = signalPillar(bear, now, "bear");
  assert.equal(out.pass, true);
  assert.match(out.text, /2 stacked: 9x20 cross down 5m · RVOL 5m/);
  assert.equal(signalPillar({ m5: { pillars: {} }, rvol: { "15m": { bg: "cyan" } } }, now, "bull").pass, false);
});

test("targets: pivot points, moving averages and today's Fib levels count as sell targets", () => {
  // prior day H 50 / L 46 / C 48 -> PP 48, R1 50, R2 52, S1 46, S2 44
  const row = { last: 49.2, prevClose: 48, m5: { pillars: { prevHigh: 50, zs: { prevLow: 46 }, ribbon5m: { ema21: 49.9 } } } };
  const bull = targetPillar(row, null, "bull");
  assert.match(bull.text, /^5m EMA 21 \$49\.90/);           // the nearest level above wins
  assert.equal(bull.pass, true);                              // +1.4% away
  assert.match(bull.text, /then (prior-day high|pivot R1) \$50\.00/);   // R1 = the prior high here
  const bear = targetPillar({ ...row, last: 47.5 }, null, "bear");
  assert.match(bear.text, /^pivot S1 \$46\.00 \(-3\.2%\) · then -5% level \$45\.60/);
  const fib = targetPillar({ last: 106, highLow: { hh: 120, ll: 100 } }, null, "bull");
  assert.match(fib.text, /^Fib 61\.8% \$107\.64 \(\+1\.5%\) · then (Fib 50\.0%|psych level) \$110\.00/);   // 120 - 20 * 0.618
});

test("estimatedRoi prices the BOT's contract: nearest OTM strike, first expiry after today, mid", () => {
  const r = estimatedRoi(CHAIN, "bull", 53.4, 57.2);
  assert.equal(r.text, "Δ0.42 54C $1.00 → +220% at 2×EM");
  assert.equal(estimatedRoi(CHAIN, "bear", 53.4, 49.6).text, "Δ0.45 52P $1.50 → +60% at 2×EM");
  assert.equal(estimatedRoi({ selectedExpiryChainRows: [] }, "bull", 53.4, 57.2), null);
});

test("signals: the 4x8 EMA cross (early trend) is a trigger", () => {
  const row = { m5: { pillars: { ema48CrossUpAt: sec("2026-09-25T10:10:00-04:00") } } };
  const out = signalPillar(row, NOW, "bull");
  assert.equal(out.pass, true);
  assert.match(out.text, /4x8 cross up 5m \(early trend\)/);
  assert.equal(signalPillar(row, NOW, "bear").pass, false);
});
