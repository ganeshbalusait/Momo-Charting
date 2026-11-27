// Unit (2026-11-26, "I have to choose call or puts or both, can we do automatic based on trend" -> "Build it now"):
// Auto picks the ROI side from today's open. Above the open = Call, below = Put. It was the one trend rule that
// pointed the right way in every sample of the Mag7 back-test (a small lean, not a sure thing).
import assert from "node:assert/strict";
import test from "node:test";

import { AUTO_SIDE_BAND, roiAutoNote, roiAutoSide, roiNextView, roiSessionOpen, roiViewOf } from "./chartRoiAutoSide.js";

const et = (text) => Math.floor(new Date(`${text}-05:00`).getTime() / 1000); // November: ET = UTC-5
const NOON = new Date("2026-11-25T12:00:00-05:00");
const bar = (text, open) => ({ time: et(text), open, high: open + 1, low: open - 1, close: open });

test("above today's open is a call, below is a put", () => {
  assert.equal(roiAutoSide({ spot: 101, open: 100, current: "PUT" }), "CALL");
  assert.equal(roiAutoSide({ spot: 99, open: 100, current: "CALL" }), "PUT");
});

test("right at the open the side does not flip back and forth", () => {
  assert.equal(AUTO_SIDE_BAND, 0.0005);
  assert.equal(roiAutoSide({ spot: 100.04, open: 100, current: "PUT" }), "PUT", "inside the band: stay");
  assert.equal(roiAutoSide({ spot: 99.96, open: 100, current: "CALL" }), "CALL", "inside the band: stay");
  assert.equal(roiAutoSide({ spot: 100.05, open: 100, current: "PUT" }), "CALL", "at the band edge it switches");
  assert.equal(roiAutoSide({ spot: 99.95, open: 100, current: "CALL" }), "PUT");
});

test("with no open or no price Auto decides nothing", () => {
  assert.equal(roiAutoSide({ spot: 101, open: 0, current: "PUT" }), null);
  assert.equal(roiAutoSide({ spot: 0, open: 100, current: "PUT" }), null);
  assert.equal(roiAutoSide({ spot: Number.NaN, open: 100 }), null);
});

test("today's open is the 9:30 candle's open on an intraday chart", () => {
  const chartBars = [bar("2026-11-24T15:55:00", 170), bar("2026-11-25T08:00:00", 171.2), bar("2026-11-25T09:30:00", 172.5), bar("2026-11-25T09:35:00", 173)];
  assert.equal(roiSessionOpen({ chartBars, now: NOON }), 172.5);
});

test("on a daily chart today's open comes from today's daily candle", () => {
  const dailyBars = [{ date: "2026-11-24", open: 169, high: 171, low: 168, close: 170 }, { date: "2026-11-25", open: 172.4, high: 174, low: 172, close: 173 }];
  assert.equal(roiSessionOpen({ dailyBars, chartBars: [], now: NOON }), 172.4);
  assert.equal(roiSessionOpen({ dailyBars: [{ time: et("2026-11-25T00:00:00"), open: 172.4 }], now: NOON }), 172.4, "a daily bar with a time, no date");
});

test("the intraday 9:30 candle wins over the daily candle for the same day", () => {
  const dailyBars = [{ date: "2026-11-25", open: 171.9 }];
  assert.equal(roiSessionOpen({ dailyBars, chartBars: [bar("2026-11-25T09:30:00", 172.5)], now: NOON }), 172.5);
});

test("no open before 9:30, on a day with no session, or from yesterday's candles", () => {
  const chartBars = [bar("2026-11-25T09:30:00", 172.5)];
  assert.equal(roiSessionOpen({ chartBars: [bar("2026-11-25T08:00:00", 171)], dailyBars: [{ date: "2026-11-25", open: 171 }], now: new Date("2026-11-25T09:10:00-05:00") }), 0, "premarket");
  assert.equal(roiSessionOpen({ chartBars, dailyBars: [{ date: "2026-11-25", open: 172.4 }], now: new Date("2026-11-26T12:00:00-05:00") }), 0, "holiday: yesterday's open is not today's");
  assert.equal(roiSessionOpen({ chartBars: [bar("2026-11-25T12:00:00", 174)], now: NOON }), 0, "a 4-hour candle starting at noon is not the open");
  assert.equal(roiSessionOpen({}), 0);
});

test("the note says the side and why, in plain words", () => {
  assert.equal(roiAutoNote({ ticker: "AAPL", spot: 173.2, open: 172.5, side: "CALL" }), "Auto: Call · AAPL 173.20 is above today's open 172.50");
  assert.equal(roiAutoNote({ ticker: "AAPL", spot: 171, open: 172.5, side: "PUT" }), "Auto: Put · AAPL 171.00 is below today's open 172.50");
  assert.equal(roiAutoNote({ ticker: "AAPL", spot: 172.52, open: 172.5, side: "PUT" }), "Auto: Put · AAPL 172.52 is right at today's open 172.50, side kept");
  assert.equal(roiAutoNote({ ticker: "AAPL", spot: 171, open: 0, side: "PUT" }), "Auto: waiting for today's open (9:30 AM ET) · Put kept");
});

test("which toolbar button is lit, and the phone button's order", () => {
  assert.equal(roiViewOf(null), "");
  assert.equal(roiViewOf({ side: "PUT" }), "PUT");
  assert.equal(roiViewOf({ side: "CALL", both: true }), "BOTH");
  assert.equal(roiViewOf({ side: "PUT", auto: true }), "AUTO");
  assert.deepEqual(["CALL", "PUT", "BOTH", "AUTO"].map(roiNextView), ["PUT", "BOTH", "AUTO", "CALL"]);
});
