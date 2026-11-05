import test from "node:test";
import assert from "node:assert/strict";
import { botReport, botTradeResults, botContractLabel, formatBotPct, formatBotDollars, botAiBook } from "./momxBot.js";

test("four results per trade: his exit, 0DTE, hold-to-max, day close", () => {
  const r = botTradeResults({ entry: 2, last: 2.5, exitPrice: 3, zEntry: 1, zExit: 0.5, best: 4, close: 2.5 });
  assert.equal(r.exit, 50);
  assert.equal(r.exitDollars, 100);
  assert.equal(r.zero, -50);
  assert.equal(r.max, 100);
  assert.equal(r.close, 25);
  // no exit yet -> the latest mark; holdBest wins over the day's best
  const open = botTradeResults({ entry: 2, last: 1, best: 3, holdBest: 5 });
  assert.equal(open.exit, -50);
  assert.equal(open.max, 150);
  assert.equal(botTradeResults({ entry: null, status: "pending" }).exit, null);
});

test("report: newest first, leaderboard by average exit, totals", () => {
  const rep = botReport([
    { day: "2026-09-28", trades: [
      { rule: "go", symbol: "KOD", at: "2026-09-28T09:40:20-04:00", entry: 5.9, exitPrice: 11.8 },
      { rule: "daily2", symbol: "LITE", at: "2026-09-28T09:36:00-04:00", entry: 34.1, exitPrice: 17.05 },
      { rule: "c2h", symbol: "ZS", at: "2026-09-28T11:05:00-04:00", entry: 1.0, exitPrice: 1.5 },
      { rule: "turn", symbol: "LFCR", at: "2026-09-28T10:10:00-04:00", status: "pending" },
    ] },
  ]);
  assert.deepEqual(rep.rows.map((r) => r.symbol), ["ZS", "LFCR", "KOD", "LITE"]);
  assert.deepEqual(rep.leaderboard.map((r) => r.label), ["GO", "C2H / C4H", "Daily 2", "Market turn"]);
  assert.equal(rep.leaderboard[1].test, true);
  assert.equal(rep.total.trades, 4);
  assert.equal(rep.total.priced, 3);
  assert.equal(rep.total.wins, 2);
  assert.equal(Math.round(rep.total.winRate), 67);
  assert.equal(formatBotPct(rep.leaderboard[0].avgExit), "+100%");
  assert.equal(formatBotDollars(-1705), "-$1,705");
});

test("contract labels", () => {
  assert.equal(botContractLabel({ strike: 75, contract: "KOD   261016C00075000", expiry: "2026-10-16" }), "75C 10/16");
  assert.equal(botContractLabel({ zStrike: 200, zContract: "ZS    260928C00200000" }, true), "200C 0DTE");
  assert.equal(botContractLabel({ zStatus: "none" }, true), "no 0DTE");
  assert.equal(botContractLabel({ status: "pending" }), "pending");
});

test("the chain's ENTRY-strike leg is scored beside the first OTM strike", async () => {
  const { botEntryStrikeLabel } = await import("./momxBot.js");
  const r = botTradeResults({ entry: 2, last: 3, eEntry: 0.75, eLast: 1.5, eStrike: 103, eContract: "ABVX  261002C00103000" });
  assert.equal(r.entryStrike, 100);
  assert.equal(botEntryStrikeLabel({ eStrike: 103, eContract: "ABVX  261002C00103000" }), "103C");
  assert.equal(botEntryStrikeLabel({ eStatus: "none" }), "none");
  const rep = botReport([{ day: "2026-09-28", trades: [{ rule: "go", symbol: "A", entry: 1, exitPrice: 1.2, eEntry: 0.5, eExit: 1 }] }]);
  assert.equal(Math.round(rep.leaderboard[0].avgEntryStrike), 100);
});

test("winners by period and the day-by-day log", async () => {
  const { botPeriodMatrix, botDailyLog } = await import("./momxBot.js");
  const day = (d, goExit, d2Exit) => ({ day: d, trades: [
    { rule: "go", symbol: "A", at: d + "T09:40:00-04:00", entry: 1, exitPrice: goExit },
    { rule: "daily2", symbol: "B", at: d + "T09:36:00-04:00", entry: 1, exitPrice: d2Exit },
  ] });
  const days = [day("2026-09-28", 2, 0.5), day("2026-09-25", 0.5, 2), day("2026-09-24", 1.5, 1.2)];
  const m = botPeriodMatrix(days);
  const keys = m.periods.map((p) => p.key);
  assert.deepEqual(keys.slice(0, 3), ["1D", "2D", "3D"]);
  assert.equal(m.periods[0].best, "go");                   // today GO +100%, Daily 2 -50%
  const go = m.rows.find((r) => r.rule === "go");
  assert.equal(go.cells[0].wins, 1);
  assert.equal(go.cells[2].trades, 3);                     // 3D covers all three days
  const log = botDailyLog(days);
  assert.deepEqual(log.map((l) => l.day), ["2026-09-28", "2026-09-25", "2026-09-24"]);
  assert.equal(log[1].best.rule, "daily2");                // 09-25: Daily 2 +100% beat GO -50%
});

test("momentum at entry: rule x state", async () => {
  const { botMomentumMatrix } = await import("./momxBot.js");
  const rep = botReport([{ day: "2026-09-29", trades: [
    { rule: "go", symbol: "A", entry: 1, exitPrice: 2, momentum: "extended" },
    { rule: "go", symbol: "B", entry: 1, exitPrice: 0.5, momentum: "fading" },
    { rule: "turn", symbol: "C", entry: 1, exitPrice: 1.5, momentum: "extended" },
    { rule: "turn", symbol: "D", entry: 1, exitPrice: 1.2 },
  ] }]);
  const m = botMomentumMatrix(rep.rows);
  const go = m.rows.find((r) => r.rule === "go");
  const ext = m.states.indexOf("extended");
  assert.equal(go.cells[ext].wins, 1);
  assert.equal(go.cells[m.states.indexOf("fading")].losses, 1);
  assert.equal(m.totals[ext].trades, 2);
  assert.equal(m.unknown, 1);                    // trades from before the field existed
});

test("ROI = profit / contract cost", () => {
  const rep = botReport([{ day: "2026-09-28", trades: [
    { rule: "go", symbol: "A", entry: 5.9, exitPrice: 11.8 },     // +$590 on $590
    { rule: "go", symbol: "B", entry: 2.8, exitPrice: 1.4 },      // -$140 on $280
  ] }]);
  const go = rep.leaderboard[0];
  assert.equal(Math.round(go.invested), 870);
  assert.equal(Math.round(go.roi), 52);                           // 450 / 870
  assert.equal(Math.round(rep.total.roi), 52);
});

test("sort cycles high -> low -> none; blanks sink", async () => {
  const { nextBotSort, sortBotRows } = await import("./momxBot.js");
  let s = nextBotSort(null, "roi");
  assert.deepEqual(s, { key: "roi", dir: "desc" });
  const rows = [{ s: "A", roi: 5 }, { s: "B", roi: null }, { s: "C", roi: 50 }, { s: "D", roi: -10 }];
  const get = { roi: (r) => r.roi };
  assert.deepEqual(sortBotRows(rows, s, get).map((r) => r.s), ["C", "A", "D", "B"]);
  s = nextBotSort(s, "roi");
  assert.deepEqual(sortBotRows(rows, s, get).map((r) => r.s), ["D", "A", "C", "B"]);
  s = nextBotSort(s, "roi");
  assert.equal(s.key, null);
  assert.deepEqual(sortBotRows(rows, s, get).map((r) => r.s), ["A", "B", "C", "D"]);
  assert.deepEqual(nextBotSort({ key: "roi", dir: "asc" }, "wins"), { key: "wins", dir: "desc" });
});

test("bear board labels", async () => {
  const { botReport: br } = await import("./momxBot.js");
  const rep = br([{ day: "2026-09-29", trades: [{ rule: "c2h", symbol: "X", entry: 1, exitPrice: 2 }] }], { side: "bear" });
  assert.equal(rep.leaderboard[0].label, "P2H / P4H");
  assert.equal(rep.rows[0].label, "P2H / P4H");
});

test("leaderboard extra columns: avg win/loss, best/worst, days up, per day, profit factor", async () => {
  const { botReport } = await import("./momxBot.js");
  const days = [
    { day: "2026-09-29", trades: [{ rule: "go", symbol: "A", entry: 1, exitPrice: 2 }, { rule: "go", symbol: "B", entry: 1, exitPrice: 0.5 }] },
    { day: "2026-09-30", trades: [{ rule: "go", symbol: "C", entry: 2, exitPrice: 1 }] },
  ];
  const r = botReport(days).leaderboard.find((x) => x.rule === "go");
  assert.equal(r.avgWin, 100); assert.equal(r.avgLoss, -50); assert.equal(r.best, 100); assert.equal(r.worst, -50);
  assert.equal(r.daysTraded, 2); assert.equal(r.daysUp, 1); assert.equal(r.perDay, 1.5);
  assert.equal(Math.round(r.profitFactor * 100) / 100, 0.67);   // won $100, lost $150
});

test("✓ Jev check splits each rule's answered trades at the cutoff; unanswered trades are left out", async () => {
  const { botJevCompare, botJevApproved } = await import("./momxBot.js");
  const tr = (rule, sym, jevP, exitPrice) => ({ rule, symbol: sym, entry: 1, exitPrice, jevP, at: "2026-10-05T10:00:00-04:00" });
  const rep = botReport([{ day: "2026-10-05", trades: [
    tr("go", "A", 0.45, 1.5), tr("go", "B", 0.1, 0.5), tr("go", "C", undefined, 2), tr("mom", "D", 0.31, 0.8),
  ] }]);
  const cmp = botJevCompare(rep.rows);
  assert.equal(cmp.asked, 3);
  assert.equal(cmp.notAsked, 1);
  const go = cmp.rows.find((r) => r.rule === "go");
  assert.deepEqual([go.all.trades, go.jev.trades, go.jev.wins, go.rest.trades, go.rest.losses], [2, 1, 1, 1, 1]);
  assert.deepEqual([cmp.total.jev.trades, cmp.total.jev.wins, cmp.total.jev.losses], [2, 1, 1]);
  assert.equal(botJevApproved({ jevP: 0.3 }), true);
  assert.equal(botJevApproved({ jevP: 0.29 }), false);
  assert.equal(botJevApproved({}), null);
});

test("Jev BOT keeps only the trades Jev would take, day by day", async () => {
  const { botJevBook } = await import("./momxBot.js");
  const days = [{ day: "2026-10-05", trades: [{ rule: "go", symbol: "A", jevP: 0.4 }, { rule: "go", symbol: "B", jevP: 0.1 }, { rule: "go", symbol: "C" }] }];
  assert.deepEqual(botJevBook(days)[0].trades.map((t) => t.symbol), ["A"]);
  assert.equal(botJevBook(days)[0].day, "2026-10-05");
  assert.deepEqual(botJevBook(null), []);
});

test("a bad-quote trade counts nowhere", () => {
  const rep = botReport([{ day: "2026-10-02", trades: [
    { rule: "mom", symbol: "APLD", entry: 0.25, exitPrice: 1.63, badQuote: true },
    { rule: "mom", symbol: "TSLA", entry: 3.8, exitPrice: 6.47 },
  ] }]);
  assert.equal(rep.total.priced, 1);
  assert.equal(rep.total.wins, 1);
  assert.equal(Math.round(rep.total.dollars), 267);
});

test("2×EM check: saved verdict first, else computed from spot / EM / strike / entry; the bot still takes every trade", async () => {
  const { botRiskOk, botRiskRoi, botRiskCompare } = await import("./momxBot.js");
  assert.equal(botRiskOk({ riskOk: false, roi2em: 5 }), false);                       // the server's verdict wins
  // call: 100 + 2 x 2 = 104; 102C at 0.80 worth 2.00 = +150%
  assert.equal(botRiskRoi({ spot: 100, expectedMove: 2, strike: 102, entry: 0.8 }), 1.5);
  assert.equal(botRiskOk({ spot: 100, expectedMove: 2, strike: 102, entry: 1.2 }), false);
  // put on the bear board: 50 - 2 = 48; 49P at 0.40 worth 1.00 = +150%
  assert.equal(botRiskOk({ board: "bear", spot: 50, expectedMove: 1, strike: 49, entry: 0.4 }), true);
  assert.equal(botRiskOk({ spot: 100, strike: 102, entry: 0.8 }), null);                // no EM = unknown
  const res = (exit) => ({ closed: true, exit, exitDollars: exit, zero: null, entryStrike: null, max: null, close: null });
  const cmp = botRiskCompare([
    { rule: "go", entry: 1, riskOk: true, results: res(50) },
    { rule: "go", entry: 1, riskOk: false, results: res(-40) },
    { rule: "go", entry: 1, results: res(10) },
  ]);
  assert.equal(cmp.checked, 2);
  assert.equal(cmp.notChecked, 1);
  assert.equal(cmp.total.jev.wins, 1);
  assert.equal(cmp.total.rest.losses, 1);
});

test("News trade is the bot's news rule - the scanner's NEWS ▲ / NEWS ▼ list", async () => {
  const { botNewsBook, botRuleLabel } = await import("./momxBot.js");
  const days = [{ day: "2026-10-08", trades: [
    { rule: "news", symbol: "A" }, { rule: "go", symbol: "B", newsJev: "bullish" }, { rule: "news", symbol: "C" },
  ] }];
  assert.deepEqual(botNewsBook(days)[0].trades.map((t) => t.symbol), ["A", "C"]);
  assert.equal(botNewsBook(days)[0].day, "2026-10-08");
  assert.deepEqual(botNewsBook(null), []);
  assert.match(botRuleLabel("news", "bull"), /News ▲/);
  assert.match(botRuleLabel("news", "bear"), /News ▼/);
});

test("Daily line before 10:00 is a leaderboard view, never double-counted", async () => {
  const { botReport } = await import("./momxBot.js");
  const days = [{ day: "2026-10-08", trades: [
    { rule: "dayLine", symbol: "A", at: "2026-10-08T09:40:00-04:00", entry: 1, exitPrice: 2 },
    { rule: "dayLine", symbol: "B", at: "2026-10-08T10:20:00-04:00", entry: 1, exitPrice: 0.5 },
  ] }];
  const r = botReport(days, { side: "bear" });
  const early = r.leaderboard.find((x) => x.rule === "dayLineEarly");
  assert.equal(early.trades, 1);
  assert.equal(early.wins, 1);
  assert.equal(early.label, "Daily line before 10:00 (below yday low)");
  assert.equal(r.total.trades, 2);                       // totals unchanged
});

test("Real fills: bought at the ask, sold at the bid; trades without bid/ask are left out", async () => {
  const { botReport, setBotFills } = await import("./momxBot.js");
  const days = [{ day: "2026-10-09", trades: [
    { rule: "go", symbol: "A", entry: 1.0, entryBid: 0.95, entryAsk: 1.05, exitPrice: 1.5, exitBid: 1.45, exitAsk: 1.55 },
    { rule: "go", symbol: "B", entry: 1.0, exitPrice: 2.0 },                       // old trade: no bid/ask
    { rule: "go", symbol: "C", entry: 2.0, entryBid: 1.9, entryAsk: 2.1, last: 2.0, lastBid: 1.9, lastAsk: 2.1 },
  ] }];
  setBotFills("real");
  try {
    const t = botReport(days).total;
    assert.equal(t.priced, 1);
    assert.equal(t.open, 1);
    assert.equal(Math.round(t.dollars), 40);
    assert.equal(Math.round(t.invested), 105);                                      // the asks paid
  } finally {
    setBotFills("mid");
  }
  assert.equal(botReport(days).total.priced, 3);
});

test("Pro fills: buy at mid + 1/4 spread, sell at mid - 1/4 spread", async () => {
  const { botReport, setBotFills } = await import("./momxBot.js");
  const days = [{ day: "2026-10-09", trades: [
    { rule: "go", symbol: "A", entry: 1.0, entryBid: 0.9, entryAsk: 1.1, exitPrice: 1.5, exitBid: 1.4, exitAsk: 1.6 },
    { rule: "go", symbol: "B", entry: 1.0, exitPrice: 2.0 },
  ] }];
  setBotFills("pro");
  try {
    const t = botReport(days).total;
    assert.equal(t.priced, 1);
    // buy 1.0 + 0.05 = 1.05, sell 1.5 - 0.05 = 1.45 -> +$40
    assert.equal(Math.round(t.dollars), 40);
    assert.equal(Math.round(t.invested), 105);
  } finally {
    setBotFills("mid");
  }
});

// MRNA 10-09 shape: next week 8.475 -> VWAP exit 11.3; same-day 2.38 -> close 24.60.
const MRNA = {
  rule: "skit", at: "2026-10-09T09:30:16-04:00", entry: 8.475, entryBid: 8.3, entryAsk: 8.65,
  exitPrice: 11.3, exitBid: 11.1, exitAsk: 11.5, best: 13.8, holdBest: 27.175, close: 27.175, closeBid: 26.35, closeAsk: 28.0,
  zEntry: 2.38, zEntryBid: 2.3, zEntryAsk: 2.46, zExit: 5.35, zBest: 24.6, zClose: 24.6, zCloseBid: 23.6, zCloseAsk: 25.6,
  eEntry: 1.5, eExit: 2.0, eLast: 2.0,
};

test("exit x contract: each leg sold its own way, at each fill", async () => {
  const { botLegResult } = await import("./momxBot.js");
  assert.equal(Math.round(botLegResult(MRNA, "main", "exit", "mid").pct), 33);
  assert.equal(Math.round(botLegResult(MRNA, "zero", "close", "mid").pct), 934);
  assert.equal(Math.round(botLegResult(MRNA, "zero", "close", "real").pct), Math.round((23.6 / 2.46 - 1) * 100));
  assert.equal(Math.round(botLegResult(MRNA, "zero", "best", "mid").pct), 934);
  // Best has no bid / ask: no Real / Pro number, never a made-up one.
  assert.equal(botLegResult(MRNA, "zero", "best", "real").pct, null);
  // A finished day with no recorded close is unknown - its frozen "last" is not the close.
  assert.equal(botLegResult(MRNA, "entry", "close", "mid").pct, null);
  // Missing entry bid / ask -> no Real fill.
  assert.equal(botLegResult(MRNA, "entry", "exit", "real").pct, null);
  assert.equal(botLegResult({ ...MRNA, badQuote: true }, "main", "exit", "mid").pct, null);
});

test("the switches move the headline numbers; defaults match the old bot", async () => {
  const { botTradeResults, setBotExit, setBotContract } = await import("./momxBot.js");
  try {
    assert.equal(Math.round(botTradeResults(MRNA).exit), 33);
    setBotContract("zero");
    setBotExit("close");
    const r = botTradeResults(MRNA);
    assert.equal(Math.round(r.exit), 934);
    assert.equal(r.cost, 2.38);
    setBotExit("nonsense");
    assert.equal(Math.round(botTradeResults(MRNA).exit), Math.round((5.35 / 2.38 - 1) * 100));
  } finally {
    setBotExit("exit");
    setBotContract("main");
  }
});

test("contract x exit matrix: 9 cells per rule plus an all-rules row", async () => {
  const { botExitMatrix } = await import("./momxBot.js");
  const m = botExitMatrix([MRNA, { ...MRNA, symbol: "X", exitPrice: 4, exitBid: 3.9, exitAsk: 4.1, zClose: 1, zCloseBid: 0.9, zCloseAsk: 1.1 }], "bull", "mid");
  assert.equal(m.combos.length, 9);
  assert.equal(m.rows.length, 1);
  const cell = (key) => m.rows[0].cells[m.combos.findIndex((c) => c.key === key)];
  assert.deepEqual([cell("main:exit").wins, cell("main:exit").losses], [1, 1]);
  assert.deepEqual([cell("zero:close").wins, cell("zero:close").losses], [1, 1]);
  assert.equal(cell("entry:close").priced, 0);
  assert.equal(m.total[0].priced, 2);
});

test("review fixes: no frozen side-contract 'best', no past-day 'close' from a stale last", async () => {
  const { botLegResult, botTradeResults } = await import("./momxBot.js");
  const old = { ...MRNA, zClose: undefined, zCloseBid: undefined, zCloseAsk: undefined, zBest: 5.35, close: undefined };
  assert.equal(botLegResult(old, "zero", "best", "mid").pct, null);
  // main "best" is the DAY's best, not the multi-day holdBest
  assert.equal(Math.round(botLegResult(MRNA, "main", "best", "mid").pct), Math.round((13.8 / 8.475 - 1) * 100));
  assert.equal(botTradeResults({ ...old, last: 9 }).close, null);
});

test("real realized totals distinguish win, loss, flat, open and missing quotes", async () => {
 const {setBotFills,setBotExit,setBotContract}=await import('./momxBot.js');
 setBotFills('real');setBotExit('exit');setBotContract('main');
 try {
 const base={entry:1,entryAsk:1,entryBid:0.9,exitPrice:1};
 const trades=[{...base,symbol:'WIN',exitBid:1.5},{...base,symbol:'LOSS',exitBid:0.5},{...base,symbol:'FLAT',exitBid:1},{...base,symbol:'MISSING'},{entry:1,entryAsk:1,last:2,lastBid:2,symbol:'OPEN'}].map(t=>({...t,rule:'go'}));
 const {total,rows}=botReport([{day:'2026-10-15',trades}]);
 assert.equal(total.wins,1);assert.equal(total.losses,1);assert.equal(total.breakeven,1);
 assert.equal(total.open,1);assert.equal(total.unpriced,1);assert.equal(total.priced,3);
 assert.equal(total.dollars,0);assert.equal(total.invested,300);
 assert.equal(rows.find(r=>r.symbol==='LOSS').results.sell,0.5);
 assert.equal(rows.find(r=>r.symbol==='LOSS').results.cost,1);
 } finally {setBotFills('mid');}
});


test("AI pick keeps only trades the AI scored 55%+", () => {
  const days = [{ day: "2026-10-19", trades: [{ symbol: "A", aiP: 0.61 }, { symbol: "B", aiP: 0.5 }, { symbol: "C" }] }];
  assert.deepEqual(botAiBook(days)[0].trades.map((t) => t.symbol), ["A"]);
});


test("0.20 delta losses: a zero-bid exit is unsellable ($0) and never a frozen missing exit", async () => {
  const { botLegResult, setBotContract } = await import("./momxBot.js");
  const loss = {symbol: "AAPL", rule: "go", status: "closed", exitPrice: 2,
    eEntry: 1, eEntryBid: 0.9, eEntryAsk: 1.1, eExit: 1,
    eExitBid: 0, eExitAsk: 0.02};
  // Was -99 (scored at the $0.01 mid) until the 2026-11-04 truth check: a $0 bid
  // has no buyer, so the exit is worth $0.
  assert.equal(botLegResult(loss, "entry").pct, -100);
  assert.equal(botLegResult({...loss, eExit: 0, eExitBid: null, eExitAsk: null}, "entry").pct, -100);
  assert.equal(botLegResult({status: "closed", eEntry: 1, eLast: 1}, "entry").pct, null);
  try {
    setBotContract("entry");
    const report = botReport([{day: "2026-10-30", trades: [loss]}]);
    assert.equal(report.total.losses, 1);
    assert.equal(report.total.wins, 0);
    assert.equal(report.total.dollars, -100);
    assert.equal(report.total.roi, -100);
  } finally { setBotContract("main"); }
});


test("identical fractional midpoint prices are break-even, not floating-point wins", async () => {
  const { botLegResult } = await import("./momxBot.js");
  const r = botLegResult({entry: 1.525, entryBid: 1.5, entryAsk: 1.55, exitPrice: 1.525});
  assert.equal(r.pct, 0);
  assert.equal(r.dollars, 0);
});


test("hold-to-close remains open until a close is actually recorded", async () => {
  const { setBotExit } = await import("./momxBot.js");
  try {
    setBotExit("close");
    assert.equal(botTradeResults({entry: 1, last: 0.5}).closed, false);
    assert.equal(botTradeResults({entry: 1, close: 0.5}).closed, true);
  } finally { setBotExit("exit"); }
});


test("each exit separates completed outcomes from open midpoint P/L", async () => {
  const {setBotExit, setBotContract, setBotFills, botLegResult} = await import("./momxBot.js");
  const day = new Intl.DateTimeFormat("en-CA", {timeZone: "America/New_York"}).format(new Date());
  const base = {rule: "go", at: day + "T10:00:00-04:00", entry: 1, entryBid: .9, entryAsk: 1.1};
  const trades = [
    {...base, symbol: "WIN", exitPrice: 1.5, close: .8, best: 1.5},
    {...base, symbol: "LOSS", exitPrice: .5, close: 1.2, best: 1.2},
    {...base, symbol: "OPEN", last: .4, best: 1},
    {...base, symbol: "MISSING", exitAt: day + "T10:30:00-04:00", last: 3},
  ];
  setBotFills("mid"); setBotContract("main");
  try {
    setBotExit("exit");
    let r = botReport([{day, trades}]);
    assert.equal(r.total.wins, 1); assert.equal(r.total.losses, 1);
    assert.equal(r.total.completed, 2); assert.equal(r.total.openLosses, 1);
    assert.equal(r.total.openPriced, 1); assert.equal(r.total.unpriced, 1);
    assert.equal(r.total.completed + r.total.openPriced + r.total.unpriced, r.total.trades);
    assert.equal(r.total.realizedDollars, 0); assert.equal(r.total.openDollars, -60);
    assert.equal(r.total.dollars, -60); assert.equal(r.total.winRate, 50);
    assert.equal(r.rows.find(t=>t.symbol === "MISSING").results.exit, null);
    setBotExit("close"); r = botReport([{day, trades}]);
    assert.equal(r.total.wins, 1); assert.equal(r.total.losses, 1);
    assert.equal(r.total.completed, 2); assert.equal(r.total.openLosses, 1);
    assert.ok(Math.abs(r.total.dollars - 140) < 1e-8);
    setBotExit("best"); r = botReport([{day, trades}]);
    assert.equal(r.total.wins, 2); assert.equal(r.total.losses, 0);
    assert.ok(Math.abs(r.total.dollars - 70) < 1e-8);
    // Corrected entry itself is an observed price; a legacy peak cannot be lower.
    assert.equal(botLegResult({...base, eEntry: 1, eEntryBid: 1, eEntryAsk: 1.2, eBest: 1}, "entry", "best").pct, 0);
    // A missing peak remains unknown instead of being fabricated from entry.
    assert.equal(botLegResult(base, "main", "best").pct, null);
  } finally {setBotExit("exit"); setBotContract("main");}
});


test("fractional midpoint dollars and small losses remain visible", () => {
  assert.equal(formatBotDollars(-15.5), "-$15.50");
  assert.equal(formatBotDollars(-.5), "-$0.50");
  assert.equal(formatBotDollars(1234.50000000001), "+$1,234.50");
  assert.equal(formatBotPct(-.32), "-0.32%");
  assert.equal(formatBotPct(.001), "+<0.01%");
});

test("zero exit bid is unsellable: the BOT scores it at $0, not the mid", async () => {
  // 2026-11-04 truth check: 2,216 trades with a $0 exit bid were scored at a
  // price nobody would pay. At the mid a $0 / $0.02 exit looked like $0.01.
  const { botLegResult } = await import("./momxBot.js");
  const t = { entry: 0.5, entryBid: 0.45, entryAsk: 0.55, exitPrice: 0.01, exitBid: 0, exitAsk: 0.02,
    status: "closed", exitAt: "2026-10-20T10:00:00-04:00", at: "2026-10-20T09:40:00-04:00" };
  assert.equal(botLegResult(t, "main", "exit", "mid").pct, -100);
  assert.equal(botLegResult({ ...t, exitBid: 0.01, exitAsk: 0.03 }, "main", "exit", "mid").pct, -96);
});
