import test from "node:test";
import assert from "node:assert/strict";
import { botReport, botTradeResults, botContractLabel, formatBotPct, formatBotDollars } from "./momxBot.js";

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
