// The Technical analysis card: verdict pills, bars and where each event marker sits on the mini chart.
import assert from "node:assert/strict";
import test from "node:test";

import { barDay, chartFrame, cleanBars, level, placeMarkers, technicalCard, termVerdict } from "./miniEventChart.js";

const day = (d, o, h, l, c, v = 1000) => ({ time: d, open: o, high: h, low: l, close: c, volume: v });
const bars = [day("2026-11-03", 10, 12, 9, 11), day("2026-11-04", 11, 13, 10, 12), day("2026-11-05", 12, 14, 11, 13), day("2026-11-06", 13, 15, 12, 14)];

test("bullish sits below the bar's low, bearish and neutral above its high, earnings on the bottom edge", () => {
  const m = placeMarkers(bars, [
    { time: "2026-11-04", kind: "bullish", name: "MACD cross up" },
    { time: "2026-11-05", kind: "bearish", name: "RSI overbought" },
    { time: "2026-11-05", kind: "neutral", name: "Inside day" },
    { time: "2026-11-06", kind: "earnings", name: "Earnings" },
  ]);
  assert.deepEqual(m.map((x) => [x.barTime, x.shape, x.side, x.price, x.stack]), [
    ["2026-11-04", "dot", "below", 10, 0],
    ["2026-11-05", "square", "above", 14, 0],
    ["2026-11-05", "diamond", "above", 14, 1],
    ["2026-11-06", "earnings", "bottom", null, 0],
  ]);
  assert.equal(m[0].color, "#22c55e");
  assert.equal(m[1].color, "#ef4444");
  assert.equal(m[0].name, "MACD cross up");
});

test("an event snaps to the last bar on or before it; weekly bars hold their whole week", () => {
  const weekly = [day("2026-10-19", 1, 2, 0.5, 1.5), day("2026-10-26", 1.5, 2, 1, 1.8), day("2026-11-02", 1.8, 3, 1.7, 2.9)];
  const m = placeMarkers(weekly, [{ time: "2026-10-29T14:30:00-04:00", kind: "bull" }, { time: "2026-11-06", kind: "bear" }]);
  assert.deepEqual(m.map((x) => x.barTime), ["2026-10-26", "2026-11-02"]);
  assert.equal(m[0].name, "Bullish", "no name: the kind's title");
  assert.equal(m[0].when, "2026-10-29T14:30:00-04:00");
});

test("events before the first bar, past the last bar's period, or of an unknown kind are left out", () => {
  const m = placeMarkers(bars, [
    { time: "2026-10-01", kind: "bullish" },
    { time: "2026-11-09", kind: "bearish" },
    { time: "2026-11-05", kind: "rumour" },
    { time: "", kind: "bullish" },
    { kind: "bullish" },
  ]);
  assert.deepEqual(m, []);
  assert.deepEqual(placeMarkers([], [{ time: "2026-11-05", kind: "bullish" }]), []);
});

test("bars are cleaned: oldest first, one per day, bad prices dropped, epoch seconds read as a day", () => {
  const out = cleanBars([day("2026-11-05", 1, 2, 0.5, 1), day("2026-11-04", 1, 2, 0.5, 1), day("2026-11-05", 1, 3, 0.5, 2), day("2026-11-06", 1, 0.5, 2, 1), { time: 1794009600, o: 1, h: 2, l: 1, c: 2, v: 5 }]);
  assert.deepEqual(out.map((b) => b.time), ["2026-11-04", "2026-11-05", "2026-11-07"]);
  assert.equal(out[1].high, 3, "the later copy of a day wins");
  assert.equal(barDay(1793836800), "2026-11-05");
  assert.equal(barDay(1793836800000), "2026-11-05");
  assert.equal(barDay("junk"), "");
});

test("support and resistance never draw a VWAP line", () => {
  assert.deepEqual(level({ price: 12.5 }, "Support"), { price: 12.5, label: "Support" });
  assert.deepEqual(level(14, "Resistance"), { price: 14, label: "Resistance" });
  assert.equal(level({ price: 13, label: "VWAP" }, "Support"), null);
  assert.equal(level({ price: -1 }, "Support"), null);
  const f = chartFrame({ bars, support: { price: 11, label: "vwap" }, resistance: { price: 15 } });
  assert.equal(f.support, null);
  assert.equal(f.resistance.label, "Resistance");
});

test("verdict pills: red down when bearish, green up when bullish, grey when neutral; strength 0-4", () => {
  assert.deepEqual(termVerdict({ verdict: "Bearish", strength: 3, reason: "Price under the 50-day average" }), { verdict: "bearish", tone: "down", word: "Bearish signal", strength: 3, reason: "Price under the 50-day average" });
  assert.equal(termVerdict({ verdict: "bullish", strength: 9 }).strength, 4);
  assert.equal(termVerdict({ verdict: "neutral" }).tone, "flat");
  assert.equal(termVerdict({ verdict: "bearish", reason: "below VWAP" }).reason, "", "no VWAP reasons");
  assert.equal(termVerdict({ verdict: "maybe" }), null);
});

test("the card hides when it has nothing; terms may sit at the top or under `terms`", () => {
  assert.equal(technicalCard(null), null);
  assert.equal(technicalCard({ asOf: "x" }), null);
  const card = technicalCard({ terms: { short: { verdict: "bearish", strength: 2 }, long: { verdict: "bullish", strength: 4 } }, charts: { daily: { bars, events: [{ time: "2026-11-05", kind: "bullish" }] } }, asOf: "as of 15:00 ET" });
  assert.deepEqual(card.pills.map((p) => p.key), ["short", "long"]);
  assert.equal(card.daily.markers.length, 1);
  assert.equal(card.weekly, null);
  assert.equal(technicalCard({ short: { verdict: "neutral" } }).pills.length, 1);
});
