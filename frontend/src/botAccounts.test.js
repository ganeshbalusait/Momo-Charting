// The Bot's paper accounts page (2026-11-27): the screen logic on its own.
// Run: node --test src/botAccounts.test.js
import test from "node:test";
import assert from "node:assert/strict";

import {
  ACCOUNT_ORDER, ACCOUNT_TABS, MONEY_PRESETS, accountCalendar, accountHead, accountKpis, accountMoney, accountOrder, accountValue,
  accountWrite, checkMoney, endingRun, orderRow, parseMoney, pickAccount, pickTab, positionRow, resetNotes, runMonths, runRows,
  runsSummary, shortDay, signedPct, timeEt, wholeDollars,
} from "./botAccounts.js";

const TILES = { netPnl: 412, closed: 14, wins: 8, losses: 6, winPct: 57.14, profitFactor: 1.62, greenDays: 3, redDays: 2, dayWinPct: 60, avgWin: 134, avgLoss: -111 };
const DAYS = [{ day: "2026-11-19", pnl: 210, trades: 3 }, { day: "2026-11-20", pnl: -96, trades: 2 }, { day: "2026-11-23", pnl: 188, trades: 4 },
  { day: "2026-11-24", pnl: -126, trades: 2 }, { day: "2026-11-25", pnl: 236, trades: 3 }];
const RESEARCH = {
  key: "research", label: "Research", kind: "virtual", note: "Every scanner signal, scored at the mid price.",
  run: { n: 2, money: 3000, perTrade: 300, startedAt: "2026-11-19T09:30:00-05:00", capped: true },
  value: 3474, free: 2932, invested: 542, dayPnl: -184, openPnl: 62, totalPnl: 474,
  positions: [{ symbol: "NVDA", contract: "150C · Dec 4", qty: 3, entry: 0.62, mark: 0.71, pnl: 27, pct: 14.5, at: "2026-11-25T10:42:00-05:00" }],
  orders: [{ symbol: "AMD", contract: "165C · Dec 4", qty: 1, status: "filled", price: 0.48, at: "2026-11-25T14:05:00-05:00" }],
  journal: { tiles: TILES, days: DAYS }, skippedNoMoney: 2, resets: 1, canTradeTicker: false, error: null,
  pastRuns: [{ n: 1, money: 2000, perTrade: 300, startedAt: "2026-11-13T09:30:00-05:00", endedAt: "2026-11-19T09:30:00-05:00", endValue: 409, pnl: -1591,
    pnlPct: -79.55, trades: 8, tiles: { netPnl: -1591, closed: 8, wins: 0, losses: 8 }, days: [{ day: "2026-11-13", pnl: -1300, trades: 4 }] }],
};

test("money is shown to the cent for a value and in whole dollars for a limit; nothing is invented", () => {
  assert.equal(accountValue(4412), "$4,412.00");
  assert.equal(accountValue(-12.5), "−$12.50");
  assert.equal(accountValue(null), "–");
  assert.equal(accountValue(undefined), "–");
  assert.equal(wholeDollars(5000), "$5,000");
  assert.equal(wholeDollars(null), "–");
  assert.equal(signedPct(13.71), "+13.7%");
  assert.equal(signedPct(-79.55), "−79.5%".replace("79.5", (79.55).toFixed(1)));
  assert.equal(signedPct(0), "0.0%");
  assert.equal(signedPct(null), "–");
});

test("a day and a time are read as written in the server's ET stamp, never shifted by the browser", () => {
  assert.equal(shortDay("2026-11-19T09:30:00-05:00"), "Nov 19");
  assert.equal(shortDay("2026-01-02"), "Jan 2");
  assert.equal(shortDay("2026-11-30T23:59:00-05:00"), "Nov 30");      // would be Dec 1 in UTC
  assert.equal(shortDay(""), "");
  assert.equal(shortDay(null), "");
  assert.equal(timeEt("2026-11-25T14:05:00-05:00"), "2:05 PM");
  assert.equal(timeEt("2026-11-25T00:07:00-05:00"), "12:07 AM");
  assert.equal(timeEt("2026-11-25T12:00:00-05:00"), "12:00 PM");
  assert.equal(timeEt("2026-11-25"), "");
});

test("the account shown is the remembered one when the server lists it, else the first listed", () => {
  const doc = { accounts: { paper3: {}, research: {} }, order: ["paper3", "paper5", "research", "ticker"] };
  assert.deepEqual(accountOrder(doc), ["paper3", "research"]);          // an account the server did not send is not shown
  assert.equal(pickAccount(doc, "research"), "research");
  assert.equal(pickAccount(doc, "paper5"), "paper3");
  assert.equal(pickAccount(doc, null), "paper3");
  assert.equal(pickAccount(null, "paper3"), "");
  assert.deepEqual(accountOrder({ accounts: { ticker: {}, paper5: {} } }), ["paper5", "ticker"]);   // our order when the server sends none
  assert.deepEqual(ACCOUNT_ORDER, ["paper3", "paper5", "research", "ticker"]);
  assert.deepEqual(ACCOUNT_TABS.map(([, label]) => label), ["Positions", "Orders", "Journal", "Past runs"]);
  assert.equal(pickTab("journal"), "journal");
  assert.equal(pickTab("nonsense"), "positions");
  assert.equal(pickTab(null), "positions");
});

test("typed money: 5k, 5,000, $3000 and 2.5k are money; words, signs and blanks are not", () => {
  assert.equal(parseMoney("5k"), 5000);
  assert.equal(parseMoney("5,000"), 5000);
  assert.equal(parseMoney(" $3000 "), 3000);
  assert.equal(parseMoney("2.5K"), 2500);
  assert.equal(parseMoney("29"), 29);
  for (const junk of ["", " ", "abc", "-500", "5kk", "1e5", "5 k x", null, undefined]) assert.equal(parseMoney(junk), null, String(junk));
  assert.deepEqual(MONEY_PRESETS, [2000, 3000, 5000, 10000]);
});

test("the reset is refused on the screen for money the server would refuse, with the reason in words", () => {
  assert.deepEqual(checkMoney("5k", "300"), { ok: true, money: 5000, perTrade: 300, error: "" });
  assert.match(checkMoney("29", "300").error, /at least \$500/);        // his "5k, 29, 3k": 29 is not an account
  assert.match(checkMoney("2000000", "300").error, /at most \$1,000,000/);
  assert.match(checkMoney("abc", "300").error, /as a number/);
  assert.match(checkMoney("5000", "").error, /money per trade as a number/);
  assert.match(checkMoney("5000", "10").error, /at least \$50/);
  assert.match(checkMoney("500", "600").error, /cannot be more than the starting money/);
  assert.equal(checkMoney("500", "500").ok, true);
});

test("the top card: value, what the run started with, and day / open / total P&L with their colours", () => {
  const head = accountHead(RESEARCH);
  assert.equal(head.value, "$3,474.00");
  assert.equal(head.started, "Started with $3,000 on Nov 19 · run 2");
  assert.deepEqual(head.pnls, [{ label: "Day P&L", value: "−$184", tone: "is-down" }, { label: "Open P&L", value: "+$62", tone: "is-up" },
    { label: "Total P&L", value: "+$474", tone: "is-up" }]);
  // A missing number is a dash with no colour, never a zero.
  const empty = accountHead({ label: "Paper 5", run: {} });
  assert.equal(empty.value, "–");
  assert.equal(empty.started, "");
  assert.deepEqual(empty.pnls.map((p) => [p.value, p.tone]), [["–", ""], ["–", ""], ["–", ""]]);
  assert.equal(accountHead(null).value, "–");
});

test("the money card: free money and the open share for a run with a limit; plain words for a run without one", () => {
  const m = accountMoney(RESEARCH);
  assert.equal(m.perTrade, "$300");
  assert.equal(m.free, "$2,932");
  assert.equal(m.capped, true);
  assert.ok(Math.abs(m.share - 542 / 3474) < 1e-9);
  assert.equal(m.barLabel, "$542 of $3,474 is in open trades");
  assert.match(m.note, /stops buying when the free money runs out/);
  assert.match(m.note, /2 trades were left out/);
  assert.match(accountMoney({ ...RESEARCH, skippedNoMoney: 1 }).note, /1 trade was left out/);
  assert.doesNotMatch(accountMoney({ ...RESEARCH, skippedNoMoney: 0 }).note, /left out/);
  const open = accountMoney({ run: { n: 1, money: 10000, perTrade: 300, capped: false }, value: 8409, invested: 0, free: 8409 });
  assert.equal(open.capped, false);
  assert.equal(open.canChange, false);
  assert.equal(m.canChange, true);
  // Research before a reset has no money per trade (one contract a signal); the Ticker trader sizes its own trades.
  assert.equal(accountMoney({ run: { n: 1, money: 10000, perTrade: null, capped: false } }).perTrade, "1 contract");
  assert.equal(accountMoney({ kind: "simulated", run: { n: 1, money: 10000, perTrade: 1000, capped: true } }).canChange, false);
  assert.match(open.note, /No money limit yet/);
  // A value of zero or a negative free amount never divides by zero or shows a negative.
  const broke = accountMoney({ run: { capped: true, perTrade: 300 }, value: 0, invested: 50, free: -20 });
  assert.equal(broke.share, 0);
  assert.equal(broke.free, "$0");
});

test("the journal tiles are the Scorecard's five, from the run's own numbers", () => {
  const kpis = accountKpis(TILES);
  assert.deepEqual(kpis.map((k) => k.label), ["Net P&L", "Trade win %", "Profit factor", "Day win %", "Avg win / loss"]);
  assert.deepEqual(kpis.map((k) => k.value), ["+$412", "57%", "1.62", "60%", "1.21"]);
  assert.equal(kpis[0].sub, "14 closed trades");
  assert.equal(kpis[1].sub, "8 won · 6 lost");
  assert.equal(kpis[3].sub, "3 green · 2 red");
  // A run with no closed trade shows $0 and dashes, not numbers.
  assert.deepEqual(accountKpis(null).map((k) => k.value), ["$0", "–", "–", "–", "–"]);
  assert.deepEqual(accountKpis({ closed: 0 }).map((k) => k.value), ["$0", "–", "–", "–", "–"]);
});

test("the journal calendar is the Scorecard's month grid from the run's days; other runs' days are not in it", () => {
  const cal = accountCalendar(DAYS, "2026-11", "2026-11-25");
  assert.equal(cal.label, "November 2026");
  assert.equal(cal.pnl, "+$412");
  assert.equal(cal.days, 5);
  const cells = cal.weeks.flatMap((w) => w.cells).filter((c) => c.inMonth);
  const day = (key) => cells.find((c) => c.key === key);
  assert.deepEqual([day("2026-11-19").pnl, day("2026-11-19").trades, day("2026-11-19").tone], ["+$210", 3, "is-up"]);
  assert.deepEqual([day("2026-11-20").pnl, day("2026-11-20").tone], ["−$96", "is-down"]);
  assert.equal(day("2026-11-13").pnl, "");                              // the run before traded that day; this run did not
  assert.equal(day("2026-11-25").isToday, true);
  assert.equal(accountCalendar(null, "2026-11").days, 0);
  assert.equal(accountCalendar(DAYS, "nonsense"), null);
});

test("the calendar's arrows stop at the run's first and last month", () => {
  assert.deepEqual(runMonths(RESEARCH.run, DAYS, "2026-11"), { first: "2026-11", last: "2026-11" });
  assert.deepEqual(runMonths({ startedAt: "2026-10-28T09:30:00-04:00" }, [{ day: "2026-10-29" }, { day: "2026-12-02" }], "2026-12"), { first: "2026-10", last: "2026-12" });
  // A past run stops at the month it ended, not at today's.
  assert.deepEqual(runMonths(RESEARCH.pastRuns[0], RESEARCH.pastRuns[0].days, "2027-02"), { first: "2026-11", last: "2026-11" });
  assert.deepEqual(runMonths(null, null, "2026-11"), { first: "2026-11", last: "2026-11" });
});

test("positions and orders read as plain rows; a missing price is a dash", () => {
  assert.deepEqual(positionRow(RESEARCH.positions[0], 0), { key: "NVDA|150C · Dec 4|2026-11-25T10:42:00-05:00", symbol: "NVDA", contract: "150C · Dec 4",
    detail: "3 contracts · bought 0.62 · now 0.71", pnl: "+$27", pct: "+14.5%", tone: "is-up" });
  const blind = positionRow({ symbol: "TSLA", contract: "360C · Dec 4", qty: 1, entry: 0.9, mark: null, pnl: null, pct: null }, 4);
  assert.equal(blind.detail, "1 contract · bought 0.90 · now –");
  assert.deepEqual([blind.pnl, blind.pct, blind.tone], ["–", "", ""]);
  assert.equal(orderRow(RESEARCH.orders[0], 0).detail, "1 contract · 0.48 · Nov 25 2:05 PM");
  assert.equal(orderRow(RESEARCH.orders[0], 0).status, "filled");
  assert.equal(orderRow({ symbol: "AMD", contract: "x", qty: 3, status: "not filled", price: null }, 1).detail, "3 contracts");
});

test("past runs: the run running now on top, then each reset with its money and its P&L", () => {
  const rows = runRows(RESEARCH);
  assert.deepEqual(rows.map((r) => [r.n, r.live]), [[2, true], [1, false]]);
  assert.equal(rows[0].title, "Run 2 · started with $3,000");
  assert.equal(rows[0].dates, "Nov 19 to today · 14 closed trades");
  assert.deepEqual([rows[0].pnl, rows[0].tone, rows[0].end], ["+$474", "is-up", "now $3,474"]);
  assert.equal(rows[1].title, "Run 1 · started with $2,000");
  assert.equal(rows[1].dates, "Nov 13 to Nov 19 · 8 closed trades");
  assert.deepEqual([rows[1].pnl, rows[1].tone, rows[1].end], ["−$1,591 · −79.5%".replace("79.5", (79.55).toFixed(1)), "is-down", "ended $409"]);
  assert.deepEqual(runsSummary(RESEARCH), { runs: 2, resets: 1, resetsText: "reset 1 time", money: "$5,000", pnl: "−$1,117", tone: "is-down" });
  // Never reset: one row, "reset 0 times".
  const fresh = { run: { n: 1, money: 10000, startedAt: "2026-11-13" }, value: 10000, totalPnl: 0, journal: { tiles: { closed: 1 } }, pastRuns: [] };
  assert.equal(runRows(fresh).length, 1);
  assert.equal(runRows(fresh)[0].dates, "Nov 13 to today · 1 closed trade");
  assert.equal(runsSummary(fresh).resetsText, "reset 0 times");
  assert.equal(runRows(null).length, 1);
});

test("the reset sheet says what is ending and what Reset does for that kind of account", () => {
  assert.deepEqual(endingRun(RESEARCH), [["Run", "2", ""], ["Started with", "$3,000", ""], ["Dates", "Nov 19 to today", ""], ["Closed trades", "14", ""],
    ["Ends at", "$3,474", ""], ["P&L", "+$474 · +15.8%", "is-up"]]);
  assert.equal(endingRun({ run: {} })[5][1], "–");
  const virtual = resetNotes(RESEARCH, 5000);
  assert.match(virtual[0], /start again from \$5,000/);
  assert.match(virtual[1], /Run 2 moves to Past runs.*Nothing is deleted/);
  assert.match(virtual[2], /only the signals this money can pay for/);
  assert.match(resetNotes({ kind: "paper", run: { n: 1 } }, 3000)[2], /Alpaca paper account itself is not changed/);
  assert.match(resetNotes({ kind: "simulated", run: { n: 4 } }, 3000)[2], /tickers are cleared/);
  assert.equal(resetNotes({ kind: "other" }, 3000).length, 2);
});

test("a write: the server's yes, the server's own refusal sentence, a bare error, and no answer at all", async () => {
  const calls = [];
  const answer = (status, body) => async (url, init) => { calls.push([url, init.method, init.body]); return { ok: status < 400, status, json: async () => body }; };
  assert.deepEqual(await accountWrite("/x", { account: "research", money: 5000 }, answer(200, { ok: true, account: { key: "research" } })),
    { ok: true, account: { key: "research" }, error: "" });
  assert.deepEqual(calls[0], ["/x", "POST", JSON.stringify({ account: "research", money: 5000 })]);
  assert.equal((await accountWrite("/x", {}, answer(400, { ok: false, error: "Starting money has to be at least $500." }))).error, "Starting money has to be at least $500.");
  assert.equal((await accountWrite("/x", {}, answer(502, null))).error, "The server said no (502). Nothing was changed.");
  // A 200 that does not say ok is not a success.
  assert.equal((await accountWrite("/x", {}, answer(200, { ok: false }))).ok, false);
  const down = await accountWrite("/x", {}, async () => { throw new Error("offline"); });
  assert.deepEqual(down, { ok: false, account: null, error: "The app server did not answer. Nothing was changed." });
});
