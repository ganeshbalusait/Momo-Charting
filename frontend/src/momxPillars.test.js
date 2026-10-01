import test from "node:test";
import assert from "node:assert/strict";
import { fivePillars, trendPillar, squeezePillar, structurePillar, targetPillar, signalPillar } from "./momxPillars.js";

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
const CHAIN = { underlyingPrice: 53.4, todayChange: 1.03, expiries: ["2026-09-25"], expiryExpectedMoves: { "2026-09-25": 1.9 } };
const WALLS = { calls: [{ strike: 55, openInterest: 47000 }, { strike: 54, openInterest: 12000 }] };

test("PYPL at 10:15 on 2026-09-25: all five pillars pass -> READY", () => {
  const out = fivePillars(PYPL, { chain: CHAIN, walls: WALLS, nowMs: NOW });
  assert.deepEqual(out.rows.map((r) => r.pass), [true, true, true, true, true]);
  assert.equal(out.ready, true);
  assert.match(out.rows[3].text, /54C wall/);
  assert.match(out.rows[4].text, /9x20 cross 5m/);
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

test("structure: room left in the expected move; used up fails", () => {
  assert.equal(structurePillar({ last: 54.4 }, CHAIN).pass, false); // top = 52.37 + 1.9 = 54.27
  assert.equal(structurePillar({ last: 53 }, null).pass, null);
});

test("target: nearest level above must be at least 1% away", () => {
  assert.equal(targetPillar({ last: 54.8, m5: { pillars: {} } }, WALLS).pass, false); // 55C only +0.4%
  assert.equal(targetPillar({ last: 40, m5: { pillars: {} } }, null).text.includes("psych level $41.00"), true);
});

test("signal: nothing in the last 30 minutes fails; overbought alone is not a buy", () => {
  const stale = { m5: { crossUpAt: sec("2026-09-25T09:00:00-04:00"), pillars: { stoch: { k: 90, d: 85 } } } };
  const out = signalPillar(stale, NOW);
  assert.equal(out.pass, false);
  assert.match(out.text, /overbought/);
});
