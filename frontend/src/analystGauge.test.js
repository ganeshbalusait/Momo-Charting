// The Analysts card's gauge, target bar and month mix.
import assert from "node:assert/strict";
import test from "node:test";

import { analystCard, analystGauge, arcPath, arcPoint, consensusWord, monthMix, signedPct, targetBar, wholePercents } from "./analystGauge.js";

const counts = { strongBuy: 20, buy: 25, hold: 10, sell: 4, strongSell: 1 };   // 60 analysts

test("the half circle is split by each rating's share, left (Strong Sell) to right (Strong Buy)", () => {
  const g = analystGauge(counts);
  assert.equal(g.total, 60);
  assert.deepEqual(g.segments.map((s) => s.label), ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]);
  assert.equal(g.segments[0].from, 180);
  assert.ok(Math.abs(g.segments[4].to) < 1e-9, "the last segment ends at the right end");
  for (let i = 1; i < 5; i += 1) assert.equal(g.segments[i].from, g.segments[i - 1].to, "segments touch");
  assert.ok(Math.abs((g.segments[3].from - g.segments[3].to) - 75) < 1e-9, "Buy 25 of 60 = 75 degrees");
  assert.deepEqual(g.segments.map((s) => s.pct), [2, 7, 17, 41, 33], "equal remainders: the left-most ratings take the spare points");
  assert.equal(g.segments.reduce((a, s) => a + s.pct, 0), 100);
});

test("the consensus word is the average rating, rounded; the server's own word wins when it is one of the five", () => {
  assert.equal(consensusWord(counts), "Buy");   // (1+8+30+100+100)/60 = 3.98
  assert.equal(consensusWord({ strongBuy: 9, buy: 1 }), "Strong Buy");
  assert.equal(consensusWord({ hold: 3 }), "Hold");
  assert.equal(consensusWord({ sell: 2, strongSell: 2 }), "Sell");   // 1.5 rounds to 2
  assert.equal(consensusWord({}), "");
  assert.equal(analystGauge({ ...counts, consensus: "strong buy" }).consensus, "Strong Buy");
  assert.equal(analystGauge({ ...counts, consensus: "Outperform" }).consensus, "Buy", "an unknown word falls back to the counts");
});

test("the needle sits at the average rating: all Strong Buy is the right end, all Hold the top", () => {
  assert.equal(analystGauge({ strongBuy: 4 }).needle, 0);
  assert.equal(analystGauge({ hold: 4 }).needle, 90);
  assert.equal(analystGauge({ strongSell: 4 }).needle, 180);
});

test("no analysts, or junk counts, hide the gauge", () => {
  assert.equal(analystGauge({}), null);
  assert.equal(analystGauge({ buy: -3, hold: NaN, sell: "7" }), null);
  assert.equal(analystGauge(null), null);
});

test("whole percents always add up to 100 and never give an empty rating a point", () => {
  assert.deepEqual(wholePercents([1, 1, 1]), [34, 33, 33]);
  assert.deepEqual(wholePercents([0, 0, 0]), [0, 0, 0]);
  assert.deepEqual(wholePercents([1, 0, 2]), [33, 0, 67]);
});

test("arc points and paths: 180 degrees is the left end, 90 the top", () => {
  const [lx, ly] = arcPoint(100, 100, 80, 180);
  assert.ok(Math.abs(lx - 20) < 1e-9 && Math.abs(ly - 100) < 1e-9);
  const [tx, ty] = arcPoint(100, 100, 80, 90);
  assert.ok(Math.abs(tx - 100) < 1e-9 && Math.abs(ty - 20) < 1e-9);
  assert.match(arcPath(100, 100, 80, 14, 180, 90), /^M20\.00 100\.00 A80 80 0 0 1 100\.00 20\.00 L100\.00 34\.00 A66 66 0 0 0 34\.00 100\.00 Z$/);
  assert.equal(arcPath(100, 100, 80, 14, 90, 90), "", "an empty rating draws nothing");
});

test("the price-target bar places low / average / high and today's price, with % from today", () => {
  const bar = targetBar({ low: 150, average: 200, high: 250 }, 180);
  assert.deepEqual(bar.today, { price: 180, pos: 30 });
  assert.deepEqual(bar.marks.map((m) => [m.key, m.pos, m.pct]), [["low", 0, -16.7], ["average", 50, 11.1], ["high", 100, 38.9]]);
  const above = targetBar({ low: 100, mean: 110, high: 120 }, 140);
  assert.equal(above.today.pos, 100, "today above the high sits at the right end");
  assert.equal(above.marks[1].key, "average");
  assert.equal(targetBar({ low: 100, high: 120 }, null), null);
  assert.equal(targetBar({ high: 120 }, 100), null);
  assert.equal(targetBar({ low: 130, high: 120 }, 100), null);
  assert.equal(signedPct(11.1), "+11.1%");
  assert.equal(signedPct(-16.7), "−16.7%");
});

test("the month mix is oldest first, at most six months, junk months left out", () => {
  const months = [
    { period: "2026-11-01", ...counts }, { period: "2026-10-01", strongBuy: 1, buy: 1 }, { period: "bad", buy: 3 }, { period: "2026-09-01" },
    ...["2026-08-01", "2026-07-01", "2026-06-01", "2026-05-01", "2026-04-01"].map((period) => ({ period, hold: 1 })),
  ];
  const mix = monthMix(months);
  assert.deepEqual(mix.map((m) => m.period), ["2026-05", "2026-06", "2026-07", "2026-08", "2026-10", "2026-11"]);
  assert.equal(mix[5].parts.reduce((a, p) => a + p.pct, 0), 100);
});

test("the card hides with no data and reads counts from the top or under `counts`", () => {
  assert.equal(analystCard(null), null);
  assert.equal(analystCard({ error: "no data" }), null);
  assert.equal(analystCard({ symbol: "AAPL" }), null);
  const card = analystCard({ symbol: "aapl", counts, consensus: "Buy", price: 180, target: { low: 150, average: 200, high: 250 }, updated: "2026-11-01T00:00:00", source: "Finnhub" });
  assert.equal(card.symbol, "AAPL");
  assert.equal(card.gauge.total, 60);
  assert.equal(card.updated, "2026-11-01");
  assert.equal(card.source, "Finnhub");
  assert.equal(card.target.marks.length, 3);
  assert.equal(analystCard({ ...counts, symbol: "X" }).target, null, "no price: the gauge shows, the target bar does not");
});

// 2026-11-11 review: the card looked for flat fields the route never sends, so it never showed. analystGauge.routeReply.json
// is momx/analyst.payload() itself (fetch() with faked Finnhub / FMP answers), the shape GET /api/long-term/analyst sends.
import { readFileSync } from "node:fs";
import { fromAnalystRoute } from "./analystGauge.js";

const routeReply = JSON.parse(readFileSync(new URL("./analystGauge.routeReply.json", import.meta.url), "utf8"));

test("the route's real reply shows the card: FMP grades, the FMP targets, Finnhub's months", () => {
  const card = analystCard(routeReply);
  assert.ok(card, "the card shows for the route's own shape");
  assert.equal(card.symbol, "NVDA");
  assert.equal(card.gauge.total, 68, "FMP grades 1+60+6+1+0, never added to Finnhub's count");
  assert.equal(card.gauge.consensus, "Buy");
  assert.equal(card.source, "FMP grades");
  assert.equal(card.updated, "2026-11-11");
  assert.deepEqual(card.months.map((m) => m.period), ["2026-10", "2026-11"]);
  assert.equal(card.target, null, "no price in the reply, so no target bar against today");
  const withPrice = analystCard({ ...routeReply, price: 200 });
  assert.deepEqual(withPrice.target.marks.map((m) => [m.key, m.price]), [["low", 140], ["average", 252.5], ["high", 360]]);
  assert.equal(withPrice.targetSource, "FMP");
  assert.deepEqual(analystCard(routeReply, 200).target, withPrice.target, "the page passes the price it shows, as the route sends none");
});

test("the route's reply without FMP grades uses Finnhub's newest month; with neither, no card", () => {
  const finnhubOnly = { ...routeReply, views: { ...routeReply.views, grades: null } };
  const card = analystCard(finnhubOnly);
  assert.equal(card.gauge.total, 68);
  assert.equal(card.source, "Finnhub, 2026-11");
  assert.equal(analystCard({ ...routeReply, views: { ...routeReply.views, grades: null, finnhub: null } }), null);
  assert.equal(fromAnalystRoute({ strongBuy: 1 }).strongBuy, 1, "an already-flat reply is left as it is");
});
