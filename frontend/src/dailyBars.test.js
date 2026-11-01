import test from "node:test";
import assert from "node:assert/strict";
import { dailyBarTradingDay, dedupeDailyBars } from "./dailyBars.js";

// ET wall-clock -> epoch seconds (EDT, UTC-4).
const et = (iso) => Date.parse(`${iso}-04:00`) / 1000;

test("trading day = own ET date; only a weekend evening stub drops", () => {
  assert.equal(dailyBarTradingDay(et("2026-10-02T01:00:00")), "2026-10-02");
  assert.equal(dailyBarTradingDay(et("2026-10-02T00:06:00")), "2026-10-02");
  assert.equal(dailyBarTradingDay(et("2026-09-23T20:15:00")), "2026-09-23");
  // A Friday's full candle stamped 20:00 keeps its Friday (SPY 10-02).
  assert.equal(dailyBarTradingDay(et("2026-10-02T20:00:00")), "2026-10-02");
  // Sunday 20:00 = Monday's overnight start (CRDO row stamped "2026-09-27").
  assert.equal(dailyBarTradingDay(et("2026-09-27T20:00:00")), null);
});

test("an evening overnight stub loses to the real day on volume (SPY 10-01 20:00)", () => {
  const out = dedupeDailyBars([
    { time: et("2026-10-01T01:00:00"), close: 763.99, volume: 47708058 },
    { time: et("2026-10-01T20:00:00"), close: 766.0, volume: 33943 },
    { time: et("2026-10-02T20:00:00"), close: 770.0, volume: 50000000 },
  ]);
  assert.deepEqual(out.map((bar) => [bar.date, bar.close]), [["2026-10-01", 763.99], ["2026-10-02", 770.0]]);
});

test("one candle per day, the fullest copy wins (CRDO 2026-10-02 served twice)", () => {
  const rows = [
    { time: et("2026-10-01T00:00:00"), date: "2026-10-01", close: 209.65, volume: 7217937 },
    { time: et("2026-10-01T00:07:00"), date: "2026-10-01", close: 210.0, volume: 6900000 },
    { time: et("2026-10-02T00:00:00"), date: "2026-10-02", close: 218.64, volume: 7537915 },
    { time: et("2026-10-02T00:06:00"), date: "2026-10-02", close: 218.6028, volume: 7532828 },
  ];
  const out = dedupeDailyBars(rows);
  assert.deepEqual(out.map((bar) => [bar.date, bar.close]), [["2026-10-01", 209.65], ["2026-10-02", 218.64]]);
});

test("a Sunday-evening stub is dropped", () => {
  const out = dedupeDailyBars([
    { time: et("2026-09-25T01:00:00"), date: "2026-09-25", volume: 11753078 },
    { time: et("2026-09-27T20:00:00"), date: "2026-09-27", volume: 900000 },
  ]);
  assert.deepEqual(out.map((bar) => bar.date), ["2026-09-25"]);
});

test("clean input comes back as the same array", () => {
  const rows = [
    { time: et("2026-10-01T01:00:00"), volume: 1 },
    { time: et("2026-10-02T01:00:00"), volume: 1 },
  ];
  assert.equal(dedupeDailyBars(rows), rows);
});
