// Ticker trader journal and AI review (2026-11-13): the screen's pure helpers (tickerTrader.js) and the
// Scorecard pieces it reuses (botDesk.js) on a journal document shaped like momx/ticker_journal.py's.
import assert from "node:assert/strict";
import test from "node:test";

import { cleanLink } from "./appLinks.js";
import { deskCalendar, deskCurve, deskDailyBars, deskKpis } from "./botDesk.js";
import {
  REVIEW_LABEL, breakdownTables, equityDays, holdLabel, improveItems, journalStats, journalTrades, journalUrl, noteProblem,
  shouldAskReview, tradeDetail,
} from "./tickerTrader.js";

const STOPPED = {
  id: "NVDA-CALL-1-1", runId: "NVDA-CALL", day: "2026-11-13", ticker: "NVDA", side: "CALL", strike: 104, expiry: "2026-11-20", qty: 3,
  entry: 1.1, entrySpot: 100, stopStart: 98.4, stopStock: 98.4, t1: 102, t2: 104, t3: 106, pl: -180, plPct: -54.5,
  boughtAt: "2026-11-13T10:00:20-05:00", closedAt: "2026-11-13T10:00:30-05:00", outcome: "stopped", holdMinutes: 0.2,
  hits: [], targets: [{ label: "T1", stock: 102, reason: "half the expected move" }],
  events: [{ at: "2026-11-13T10:00:30-05:00", text: "Stop: stock traded through 98.40, sold 3 at $0.50." }], note: "", noteAt: null,
};
const WON = {
  ...STOPPED, id: "NVDA-CALL-2-2", pl: 470, plPct: 142.4, outcome: "T3", holdMinutes: 75, closedAt: "2026-11-13T11:16:00-05:00",
  hits: [{ target: "T1", at: "2026-11-13T10:01:00-05:00", sold: 1, price: 1.8 }, { target: "T2", at: "2026-11-13T10:40:00-05:00", sold: 1, price: 2.6 },
    { target: "T3", at: "2026-11-13T11:16:00-05:00", sold: 1, price: 3.6 }],
  events: [{ at: "2026-11-13T10:01:00-05:00", text: "T1 hit: sold 1 at $1.80, stop to break-even." }], note: "Waited for the pullback.",
};
const DOC = {
  scope: "NVDA-CALL", label: "NVDA Call", asOf: "2026-11-13T11:20:00-05:00", startBalance: 10000, balance: 10290, openTrades: 0,
  stats: { n: 2, wins: 1, losses: 1, winRatePct: 50, netPnl: 290, avgWin: 470, avgLoss: -180, profitFactor: 2.61, expectancy: 145, bestTrade: 470, worstTrade: -180 },
  daySummary: { tradingDays: 1, greenDays: 1, redDays: 0 },
  days: [{ day: "2026-11-13", sent: 2, filled: 2, closed: 2, pnl: 290, cum: 290 }],
  journal: { periods: { day: [{ key: "2026-11-13", trades: 2, wins: 1, losses: 1, winRatePct: 50, pnl: 290 }], week: [], month: [] } },
  equity: [{ id: STOPPED.id, at: STOPPED.closedAt, pl: -180, balance: 9820 }, { id: WON.id, at: WON.closedAt, pl: 470, balance: 10290 }],
  bestId: WON.id, worstId: STOPPED.id,
  breakdowns: {
    side: [{ key: "CALL", label: "Calls", trades: 2, wins: 1, winRatePct: 50, pnl: 290 }, { key: "PUT", label: "Puts", trades: 0, wins: 0, winRatePct: null, pnl: 0 }],
    outcome: [{ key: "stopped", label: "Stopped before T1", trades: 1, wins: 0, winRatePct: 0, pnl: -180 }],
  },
  improve: [
    { key: "fastStop", title: "Stopped within 5 minutes", text: "The stop was hit within 5 minutes of the buy on 1 of 2 trades (50%).", trades: 2, enough: false, judge: "Too few trades to judge" },
    { key: "t1First", title: "T1 before the stop", text: "T1 was reached before the stop on 6 of 11 trades (54.5%).", trades: 11, enough: true, judge: "" },
  ],
  trades: [WON, STOPPED], review: null, reviewStatus: null,
};

test("the journal reads its own route, one run or all", () => {
  assert.equal(journalUrl("NVDA-CALL"), "/api/momx-scanner/ticker-trader/journal?id=NVDA-CALL");
  assert.equal(journalUrl(""), "/api/momx-scanner/ticker-trader/journal?id=all");
  assert.equal(REVIEW_LABEL, "AI review, can be wrong. Only you see this.");
});

test("the Scorecard's tiles, bars and calendar draw the journal unchanged", () => {
  const tiles = Object.fromEntries(deskKpis(DOC).map((k) => [k.key, k.value]));
  assert.deepEqual(tiles, { net: "+$290", win: "50%", factor: "2.61", dayWin: "100%", avg: "2.61" });
  assert.equal(deskDailyBars(DOC.days).bars.length, 1);
  const cal = deskCalendar(DOC, "2026-11", "2026-11-13");
  const cell = cal.weeks.flatMap((w) => w.cells).find((c) => c.key === "2026-11-13");
  assert.equal(cell.pnl, "+$290");
  assert.equal(cell.trades, 2);
  assert.equal(cal.pnl, "+$290");
});

test("average win and loss, expectancy, best and worst trade; dashes before any trade", () => {
  assert.deepEqual(journalStats(DOC).map((x) => [x.key, x.value]),
    [["avgWin", "+$470"], ["avgLoss", "−$180"], ["expectancy", "+$145"], ["best", "+$470"], ["worst", "−$180"]]);
  assert.ok(journalStats({ stats: { n: 0 } }).every((x) => x.value === "–"));
});

test("the equity curve starts at the account's balance and follows each trade", () => {
  const days = equityDays(DOC);
  assert.deepEqual(days.map((d) => [d.cum, d.balance]), [[-180, 9820], [290, 10290]]);
  const curve = deskCurve(days, {});
  assert.equal(curve.points.length, 2);
  assert.equal(curve.last.cum, 290);
  assert.ok(curve.points[0].y > curve.zeroY && curve.points[1].y < curve.zeroY);   // below, then above the start line
});

test("breakdowns show trades, win rate and net; an empty bucket shows dashes", () => {
  const tables = breakdownTables(DOC);
  assert.deepEqual(tables.map((t) => t.title), ["Call vs Put", "Which target was reached"]);
  assert.deepEqual(tables[0].rows, [
    { key: "CALL", label: "Calls", trades: 2, win: "50%", net: "+$290", tone: "is-up" },
    { key: "PUT", label: "Puts", trades: 0, win: "–", net: "–", tone: "" },
  ]);
});

test("what to improve carries each line's trade count and says too few under ten", () => {
  assert.deepEqual(improveItems(DOC).map((x) => [x.key, x.count, x.judge]),
    [["fastStop", "2 trades", "Too few trades to judge"], ["t1First", "11 trades", ""]]);
});

test("the trade list marks best, worst and notes; the card shows the plan, the hits and a chart link for that day", () => {
  const rows = journalTrades(DOC);
  assert.deepEqual(rows.map((r) => [r.title, r.plText, r.best, r.worst, r.hasNote, r.outcome]), [
    ["Nov 13 · NVDA 104 Call", "+$470", true, false, true, "Reached T3"],
    ["Nov 13 · NVDA 104 Call", "−$180", false, true, false, "Stopped before T1"],
  ]);
  const card = tradeDetail(WON);
  assert.deepEqual(card.levels.map((l) => l.label), ["Entry", "Stop", "T1 ✓", "T2 ✓", "T3 ✓"]);
  assert.equal(card.levels[2].note, "hit 10:01 ET · sold 1 at $1.80");
  assert.equal(card.levels[0].value, "100.00 · ask $1.10");
  assert.equal(card.hold, "1 h 15 min");
  assert.equal(card.pct, "+142.4%");
  assert.equal(card.note, "Waited for the pullback.");
  assert.deepEqual(cleanLink(card.link), { label: "NVDA chart", view: "Charts & OI", ticker: "NVDA", timeframe: "5m", day: "2026-11-13" });
  const stopped = tradeDetail(STOPPED);
  assert.equal(stopped.levels[2].note, "half the expected move");
  assert.equal(stopped.levels[3].note, "not reached");
  assert.equal(holdLabel(0.2), "under 1 min");
  assert.equal(holdLabel(null), "–");
});

test("a note is plain text up to 2,000 characters", () => {
  assert.equal(noteProblem("x".repeat(2000)), "");
  assert.equal(noteProblem("x".repeat(2001)), "A note can be at most 2,000 characters.");
  assert.equal(noteProblem(null), "A note is plain text.");
});

test("the AI review is asked for once per opening, only with trades and nothing for today yet", () => {
  assert.equal(shouldAskReview(DOC, false), true);
  assert.equal(shouldAskReview(DOC, true), false);
  assert.equal(shouldAskReview({ ...DOC, review: { text: "x" } }, false), false);
  assert.equal(shouldAskReview({ ...DOC, reviewStatus: "tried" }, false), false);
  assert.equal(shouldAskReview({ ...DOC, stats: { n: 0 } }, false), false);
  assert.equal(shouldAskReview(null, false), false);
});
