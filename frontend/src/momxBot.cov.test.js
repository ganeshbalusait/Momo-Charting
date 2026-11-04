// Bot page maths that the screen switches lean on (2026-11-04 coverage pass).
// setBotExit / setBotContract are module globals the Bot page sets on every
// render; the 9-way "Contract x exit" matrix must not change with them, the
// book filters must keep exactly their own trades, and the Trades KPI's day
// count is the number of days in the answer.
import test from "node:test";
import assert from "node:assert/strict";

import {
  BOT_AI_CUTOFF, botAiBook, botExitMatrix, botNewsBook, botReport, botTradeResults, setBotContract, setBotExit,
} from "./momxBot.js";

const MRNA = {
  rule: "skit", symbol: "MRNA", at: "2026-10-09T09:30:16-04:00", entry: 8.475, entryBid: 8.3, entryAsk: 8.65,
  exitPrice: 11.3, exitBid: 11.1, exitAsk: 11.5, best: 13.8, holdBest: 27.175, close: 27.175, closeBid: 26.35, closeAsk: 28.0,
  zEntry: 2.38, zEntryBid: 2.3, zEntryAsk: 2.46, zExit: 5.35, zBest: 24.6, zClose: 24.6, zCloseBid: 23.6, zCloseAsk: 25.6,
  eEntry: 1.5, eExit: 2.0, eLast: 2.0, status: "closed", exitAt: "2026-10-09T10:15:00-04:00",
};
const LOSER = { ...MRNA, rule: "go", symbol: "X", exitPrice: 4, exitBid: 3.9, exitAsk: 4.1, zClose: 1, zCloseBid: 0.9, zCloseAsk: 1.1 };

test("the page's exit / contract switches never change the 9-way matrix", () => {
  const rows = [MRNA, LOSER];
  const before = botExitMatrix(rows, "bull", "mid");
  const headline = botTradeResults(MRNA).exit;
  try {
    setBotExit("best");
    setBotContract("zero");
    assert.notEqual(botTradeResults(MRNA).exit, headline, "the switches did take effect on the headline");
    assert.deepEqual(botExitMatrix(rows, "bull", "mid"), before);
    setBotExit("close");
    setBotContract("entry");
    assert.deepEqual(botExitMatrix(rows, "bull", "mid"), before);
  } finally {
    setBotExit("exit");
    setBotContract("main");
  }
  assert.equal(botTradeResults(MRNA).exit, headline, "defaults restored");
});

test("AI pick keeps 55% and up only; missing or unreadable scores never count", () => {
  const day = { day: "2026-10-28", trades: [
    { symbol: "EDGE", aiP: BOT_AI_CUTOFF }, { symbol: "UNDER", aiP: 0.5499 }, { symbol: "HIGH", aiP: 0.9 },
    { symbol: "NONE" }, { symbol: "NULL", aiP: null }, { symbol: "TEXT", aiP: "high" }, null,
  ] };
  const kept = botAiBook([day])[0].trades.map((t) => t.symbol);
  assert.deepEqual(kept, ["EDGE", "HIGH"]);
  assert.equal(BOT_AI_CUTOFF, 0.55);
});

test("News trade keeps only the bot's news rule, for calls and for puts alike", () => {
  const bull = { day: "2026-10-28", trades: [{ rule: "news", symbol: "N1" }, { rule: "jev", symbol: "J1" }, { rule: "go", symbol: "G1" }] };
  const bear = { day: "2026-10-28", trades: [{ rule: "news", symbol: "P1" }, { rule: "daily2", symbol: "D1" }] };
  assert.deepEqual(botNewsBook([bull])[0].trades.map((t) => t.symbol), ["N1"]);
  assert.deepEqual(botNewsBook([bear])[0].trades.map((t) => t.symbol), ["P1"]);
  assert.deepEqual(botNewsBook(null), []);
  assert.deepEqual(botNewsBook([{ day: "2026-10-27" }])[0].trades, []);
});

test("the Trades KPI counts the days in the answer, so a 1W answer never reads as Today's", () => {
  const today = [{ day: "2026-10-28", trades: [MRNA] }];
  const week = [
    { day: "2026-10-28", trades: [MRNA] }, { day: "2026-10-27", trades: [LOSER] }, { day: "2026-10-24", trades: [] },
  ];
  assert.equal(botReport(today).days.length, 1);
  assert.equal(botReport(week).days.length, 3);
  assert.equal(botReport(week).total.trades, 2);
});
