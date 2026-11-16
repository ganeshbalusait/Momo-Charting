// My Trading screen logic (owner only, 2026-11-11). Run: node --test src/myTrading.test.js
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  MY_TABLES, TOO_FEW_LINE, basisLine, emptyLine, hasTrades, holdLine, isTooFew, mtClock, mtCost, mtFactor, mtMinutes, mtMoney, mtFraction, myEvents,
  myExecution, myHabits, mySchwab, myTable, myTiles, myTradingUrl, myVersus, myVersusBasis, openPositions, periodLabel, readPeriod,
  refreshReply, tradeContract,
} from "./myTrading.js";

const stats = (trades, over = {}) => ({ trades, wins: Math.ceil(trades / 2), losses: Math.floor(trades / 2), winRate: 0.5,
  avgWin: 120, avgLoss: -80, expectancy: 20, profitFactor: 1.5, pnl: trades * 20, ...over });

const doc = () => ({
  days: 30, period: { days: 30, from: "2026-10-13", to: "2026-11-11" },
  schwab: { lastReadAt: "2026-11-10T16:31:04-05:00", stoppedDay: null, nextManualAt: "2026-11-10T17:31:04-05:00" },
  summary: stats(24, { pnl: -350, expectancy: -14.6, medianHoldMinutes: 42 }),
  breakdowns: {
    setup: [{ key: "L3", label: "BULL Level 3", ...stats(12, { pnl: 400 }) }, { key: "none", label: "not on the board", ...stats(4, { pnl: -900 }) }],
    timeOfDay: [{ label: "09:30-10:00", ...stats(8) }, { label: "10-11", ...stats(16) }],
    callPut: { Calls: stats(20), Puts: stats(4, { pnl: -500 }) },
  },
  habits: [
    { key: "revenge", count: 2, cost: -310, trades: [{ id: "t1", underlying: "NVDA", expiry: "2026-11-14", strike: 190, putCall: "CALL", entryAt: "2026-11-05T10:12:00-05:00", pnl: -210 },
      { id: "t2", underlying: "TSLA", expiry: "2026-11-14", strike: 450, putCall: "PUT", entryAt: "2026-11-06T11:02:00-05:00", pnl: -100 }] },
    { key: "overtrading", count: 0, cost: 0, trades: [] },
    { key: "heldPast50", count: 1, cost: -640, approx: true, trades: [{ id: "t3", underlying: "AMD", strike: 250, putCall: "C", pnl: -640 }] },
  ],
  execution: { trades: 9, spreadCost: 184.5, avgSpreadDollars: 20.5, avgSpreadPct: 0.042, vsMid: -184.5, approxCount: 3, unpriced: 15 },
  paperBots: [{ account: "paper3", label: "Paper 3", ...stats(30, { pnl: 1200 }) }, { account: "paper5", label: "Paper 5", ...stats(6) }],
  events: { items: [{ at: "2026-11-13T08:30:00-05:00", title: "CPI", impact: "high" }, { date: "2026-11-12", symbol: "NVDA", kind: "earnings" }], note: "" },
  openPositions: [{ underlying: "SPY", expiry: "2026-11-12", strike: 600, putCall: "CALL", quantity: 2, price: 1.25, entryAt: "2026-11-10T15:40:00-05:00" }],
});

test("period: only 7, 30 or 90 days; the url carries it", () => {
  assert.equal(readPeriod("90"), 90);
  assert.equal(readPeriod("45"), 30);
  assert.equal(readPeriod(null), 30);
  assert.equal(myTradingUrl(7), "/api/my-insights?days=7");
  assert.equal(myTradingUrl("junk"), "/api/my-insights?days=30");
  assert.equal(periodLabel(doc()), "last 30 days (10-13 to 11-11)");
});

test("numbers: a missing number is a dash, never a zero", () => {
  assert.equal(mtMoney(null), "–");
  assert.equal(mtMoney(undefined), "–");
  assert.equal(mtMoney(1350.4), "+$1,350");
  assert.equal(mtMoney(-420), "−$420");
  assert.equal(mtMoney(0), "$0");
  assert.equal(mtCost(-84.2), "$84");
  // The server documents win rate and spread % as fractions: always x100, never guessed from the size.
  assert.equal(mtFraction(0.5), "50%");
  assert.equal(mtFraction(0.62), "62%");
  assert.equal(mtFraction(1.5), "150%");
  assert.equal(mtFraction(1), "100%");
  assert.equal(mtFraction(null), "–");
  assert.equal(mtFactor(1.456), "1.46");
  assert.equal(mtFactor("inf"), "no losses");
  assert.equal(mtClock("2026-11-10T15:05:52-05:00"), "11-10 15:05 ET");
  assert.equal(mtClock("bad"), "");
  assert.equal(mtMinutes(42), "42 min");
  assert.equal(mtMinutes(90), "1.5 h");
});

test("fewer than 10 trades says too few trades to judge", () => {
  assert.equal(isTooFew({ trades: 9 }), true);
  assert.equal(isTooFew({ trades: 10 }), false);
  assert.equal(basisLine({ trades: 3 }, "last 7 days"), `3 trades, last 7 days · ${TOO_FEW_LINE}`);
  assert.equal(basisLine({ trades: 1 }), `1 trade · ${TOO_FEW_LINE}`);
  assert.equal(basisLine({ trades: 12 }, "last 30 days"), "12 trades, last 30 days");
  const tiles = myTiles({ summary: stats(4) });
  const win = tiles.find((t) => t.key === "winRate");
  assert.equal(win.dim, true);
  assert.equal(win.sub, TOO_FEW_LINE);
  assert.equal(tiles.find((t) => t.key === "pnl").dim, false, "the dollar total is a fact, never greyed");
});

test("tiles: P/L, win rate, expectancy, profit factor, trades", () => {
  const tiles = myTiles(doc());
  assert.deepEqual(tiles.map((t) => t.key), ["pnl", "winRate", "expectancy", "profitFactor", "trades"]);
  assert.equal(tiles[0].value, "−$350");
  assert.equal(tiles[0].tone, "is-down");
  assert.equal(tiles[1].value, "50%");
  assert.equal(tiles[2].value, "−$15");
  assert.equal(tiles[3].value, "1.50");
  assert.equal(tiles[4].value, "24");
  assert.equal(tiles[4].sub, "typical hold 42 min");
});

test("tables: arrays and objects both read; small rows stay in and are marked", () => {
  const setup = myTable(doc(), "setup");
  assert.deepEqual(setup.map((r) => r.label), ["not on the board", "BULL Level 3"], "biggest dollar swing first");
  assert.equal(setup[0].few, true);
  assert.equal(setup[0].note, TOO_FEW_LINE);
  assert.equal(setup[1].few, false);
  const time = myTable(doc(), "timeOfDay");
  assert.deepEqual(time.map((r) => r.label), ["09:30-10:00", "10-11"], "time buckets keep the server's order");
  const cp = myTable(doc(), "callPut");
  assert.deepEqual(cp.map((r) => r.label), ["Calls", "Puts"]);
  assert.equal(cp[1].pnl, "−$500");
  assert.deepEqual(myTable(doc(), "delta"), [], "no delta data = no table");
  assert.ok(MY_TABLES.some((t) => t.key === "dte") && MY_TABLES.some((t) => t.key === "weekday") && MY_TABLES.some((t) => t.key === "ticker"));
});

test("habits: costliest first, each with its trades; a habit that did not happen says so", () => {
  const h = myHabits(doc());
  assert.deepEqual(h.map((x) => x.key), ["heldPast50", "revenge", "overtrading"]);
  assert.equal(h[0].cost, "$640");
  assert.equal(h[0].approx, true);
  assert.equal(h[1].line, "2 times, $310 lost on those trades");
  assert.equal(h[1].trades[0].contract, "NVDA 11-14 190C");
  assert.equal(h[1].trades[1].contract, "TSLA 11-14 450P");
  assert.equal(h[1].trades[0].when, "11-05 10:12 ET");
  assert.equal(h[2].empty, true);
  assert.equal(h[2].line, "Did not happen in this period.");
  const asObject = myHabits({ habits: { revenge: { count: 1, cost: -50, trades: [] } } });
  assert.equal(asObject[0].title, "New trade soon after a loss");
});

test("contract label", () => {
  assert.equal(tradeContract({ underlying: "SPY", expiry: "2026-11-12", strike: 600, putCall: "CALL" }), "SPY 11-12 600C");
  assert.equal(tradeContract({ optionSymbol: "SPY   261112C00600000" }), "SPY   261112C00600000");
});

test("execution: spread cost and the approximate count", () => {
  const e = myExecution(doc());
  assert.equal(e.spreadCost, "$185");
  assert.equal(e.avgPct, "4%");
  assert.match(e.line, /9 fills/);
  assert.match(e.line, /3 of them used a chain saved more than 5 minutes away \(approximate\)/);
  assert.equal(e.unpriced, 15);
  assert.match(myExecution({ execution: { trades: 0 } }).line, /cannot be measured/);
  assert.equal(myExecution({}), null);
  assert.equal(e.measured, true);
  assert.equal(e.spreadLabel, "Spread paid in total");
  assert.equal(e.avgLabel, "Paid per fill on average");
  assert.equal(e.avgPctLine, "4% of the price");
});

test("execution: fills better than mid read as saved, never as money paid", () => {
  const e = myExecution({ execution: { trades: 4, spreadCost: -84, vsMid: 84, avgSpreadDollars: -21, avgSpreadPct: -0.03 } });
  assert.equal(e.saved, true);
  assert.equal(e.spreadLabel, "Saved vs mid in total");
  assert.equal(e.spreadCost, "$84");
  assert.equal(e.avgLabel, "Saved per fill on average");
  assert.equal(e.avgPerFill, "$21");
  assert.equal(e.avgPctLine, "3% of the price below mid");
  assert.equal(e.vsMid, "+$84");
  assert.doesNotMatch(JSON.stringify(e), /-3%|Spread paid|Paid per fill/);
});

test("execution: nothing priced shows no $0 as if measured, and the note line once", () => {
  const line = "No saved option prices near these fills, so the spread cannot be measured.";
  const e = myExecution({ execution: { trades: 0, spreadCost: 0, vsMid: 0, unpriced: 8, note: line } });
  assert.equal(e.measured, false);
  assert.equal(e.spreadCost, "–");
  assert.equal(e.avgPerFill, "–");
  assert.equal(e.vsMid, "–");
  assert.doesNotMatch(JSON.stringify(e), /\$0/);
  assert.equal(e.note, "", "the server note repeats the line, so it is not printed twice");
  assert.equal(e.unpriced, 8);
  // A different server note still shows.
  assert.equal(myExecution({ execution: { trades: 2, spreadCost: 10, note: "Chains saved from 11-10 only." } }).note, "Chains saved from 11-10 only.");
});

test("execution: a 1.5 spread fraction is 150%, not 2%", () => {
  const e = myExecution({ execution: { trades: 2, spreadCost: 30, avgSpreadDollars: 15, avgSpreadPct: 1.5, vsMid: -30 } });
  assert.equal(e.avgPct, "150%");
  assert.equal(e.avgPctLine, "150% of the price");
});

test("empty page after a read that found no trades says so", () => {
  const read = { state: "ok", lastReadAt: "2026-11-12T01:09:00-05:00", accounts: ["…1867"], firstDay: null, throughDay: "2026-11-12" };
  assert.equal(emptyLine({ available: false, schwab: read }),
    "Read from Schwab at 11-12 01:09 ET: no option trades found in the last 90 days on account …1867.");
  assert.match(emptyLine({ available: false, schwab: { ...read, accounts: ["…1867", "…4410"] } }), /on accounts …1867, …4410\.$/);
  assert.match(emptyLine({ available: false, schwab: { ...read, accounts: [] } }), /in the last 90 days\.$/);
  assert.match(emptyLine({ available: false, schwab: { state: "never" } }), /^No trades read from Schwab yet/);
  assert.match(emptyLine({ available: false, schwab: { state: "signin_needed", stopped: true, signInNeeded: true } }), /^Schwab sign-in needed/);
  assert.doesNotMatch(emptyLine({ available: false, schwab: { state: "stopped", stopped: true, signInNeeded: false } }), /first read happens/);
});

test("you vs the paper bots: you first, small samples marked", () => {
  const v = myVersus(doc());
  assert.deepEqual(v.map((r) => r.label), ["You (real)", "Paper 3", "Paper 5"]);
  assert.equal(v[0].own, true);
  assert.equal(v[2].few, true);
  assert.deepEqual(myVersus({ summary: stats(3) }), [], "no bots = no table");
});

test("you vs the paper bots: bot rows use the same days the owner traded, not the whole period", () => {
  const sameDays = { trades: 3, wins: 3, losses: 0, winRate: 1, expectancy: 50, pnl: 150, period: "last 7 days, the days you traded" };
  const d = { summary: stats(4, { pnl: 80 }),
    paperBots: [{ account: "paper3", label: "Paper 3", ...stats(42, { winRate: 0.48, expectancy: -3.1, pnl: -130 }), sameDays }] };
  const [you, bot] = myVersus(d);
  assert.equal(you.trades, 4);
  assert.equal(bot.trades, 3);
  assert.equal(bot.winRate, "100%");
  assert.equal(bot.pnl, "+$150");
  assert.equal(bot.few, true, "3 same-day trades are too few, even though the whole period had 42");
  assert.doesNotMatch(JSON.stringify(bot), /42|48%|130/);
  assert.equal(myVersusBasis(d), "last 7 days, the days you traded");
  // An older doc without sameDays still shows something, and says it is the whole period.
  const old = { summary: stats(4), paperBots: [{ account: "paper3", label: "Paper 3", ...stats(42, { period: "last 7 days" }) }] };
  assert.equal(myVersus(old)[1].trades, 42);
  assert.match(myVersusBasis(old), /whole period/);
});

test("events strip: items, and the reason when no calendar is available", () => {
  const e = myEvents(doc(), new Date("2026-11-13T15:00:00Z"));
  assert.equal(e.items.length, 2);
  assert.equal(e.items[0].title, "CPI");
  assert.equal(e.items[0].high, true);
  assert.equal(e.items[0].today, true);
  assert.equal(e.items[1].title, "NVDA earnings");
  const none = myEvents({ events: { items: [], note: "No economic calendar on the current keys." } });
  assert.deepEqual(none.items, []);
  assert.equal(none.note, "No economic calendar on the current keys.");
  assert.match(myEvents({}).note, /No market events/);
});

test("Schwab state: last read, the once-an-hour refresh, and sign-in needed", () => {
  const ok = mySchwab(doc(), new Date("2026-11-10T22:00:00Z")); // 17:00 ET, 31 min before the next manual read
  assert.equal(ok.title, "Last read from Schwab at 11-10 16:31 ET");
  assert.equal(ok.canRefresh, false);
  assert.match(ok.refreshNote, /Refresh again in 32 min/);
  assert.match(ok.line, /cannot place, change or cancel an order/);
  const later = mySchwab(doc(), new Date("2026-11-10T23:00:00Z"));
  assert.equal(later.canRefresh, true);
  const stopped = mySchwab({ schwab: { lastReadAt: "2026-11-10T16:31:04-05:00", stoppedDay: "2026-11-11", reason: "401" } });
  assert.equal(stopped.title, "Schwab sign-in needed");
  assert.equal(stopped.tone, "bad");
  assert.equal(stopped.canRefresh, false, "a refused sign-in stops reads for the day; no retry button");
  assert.match(stopped.line, /no more reads today/);
  const never = mySchwab({});
  assert.equal(never.title, "Not read from Schwab yet");
  assert.equal(mySchwab({ schwab: { canRefresh: false } }).canRefresh, false);
});

test("the server's own shape: period label, hold medians, habit wording, a non-sign-in stop, nothing read yet", () => {
  assert.equal(periodLabel({ period: { days: 7, label: "last 7 days (11-05 to 11-11)" } }), "last 7 days (11-05 to 11-11)");
  assert.equal(holdLine({ summary: { trades: 3, medianHoldMinutes: 30 }, holdTime: { all: 30, wins: 20, losses: 90 } }),
    "typical hold 30 min · wins 20 min, losses 1.5 h");
  assert.equal(holdLine({ summary: { trades: 0, medianHoldMinutes: null }, holdTime: { all: null } }), "");
  const cut = myHabits({ habits: [{ key: "cutWinnersEarly", label: "Sold before +100%", what: "server words", count: 1, cost: 120, trades: [] }] })[0];
  assert.equal(cut.what, "server words");
  assert.equal(cut.line, "1 time, $120 less than the later +100% price");
  const stopped = mySchwab({ schwab: { state: "stopped", stopped: true, signInNeeded: false, stoppedDay: "2026-11-11", reason: "bad reply" } });
  assert.equal(stopped.title, "Schwab reading stopped for today");
  assert.equal(stopped.canRefresh, false);
  const signin = mySchwab({ schwab: { state: "signin_needed", stopped: true, signInNeeded: true, stoppedDay: "2026-11-11" } });
  assert.equal(signin.title, "Schwab sign-in needed");
  assert.equal(mySchwab({ schwab: { state: "reading", reading: true } }).reading, true);
  assert.equal(mySchwab({ schwab: { state: "reading", reading: true } }).canRefresh, false);
  assert.match(emptyLine({ available: false }), /^No trades read from Schwab yet/);
  assert.match(emptyLine({ available: true, period: { label: "last 7 days (11-05 to 11-11)" } }), /No closed trades in the last 7 days/);
  assert.equal(refreshReply(409, { message: "Already reading from Schwab." }).tone, "warn");
  assert.equal(refreshReply(409, { message: "Schwab sign-in needed." }).tone, "bad");
});

test("refresh replies", () => {
  assert.equal(refreshReply(202, {}).tone, "ok");
  assert.equal(refreshReply(429, {}).text, "Already read in the last hour.");
  assert.equal(refreshReply(403, {}).text, "Owner only.");
  assert.equal(refreshReply(500, { error: "boom" }).text, "boom");
});

test("open positions are listed apart and the page knows when it has trades", () => {
  const o = openPositions(doc());
  assert.equal(o[0].contract, "SPY 11-12 600C");
  assert.equal(o[0].cost, "$250");
  assert.equal(hasTrades(doc()), true);
  assert.equal(hasTrades({ summary: { trades: 0 } }), false);
  assert.equal(hasTrades(null), false);
});

test("no advice wording anywhere on the screen", () => {
  const words = JSON.stringify([myTiles(doc()), myHabits(doc()), myExecution(doc()), mySchwab(doc()), myEvents(doc()),
    mySchwab({ schwab: { stoppedDay: "2026-11-11" } }), myTable(doc(), "setup")]).toLowerCase();
  for (const phrase of ["you should", "should ", "we recommend", "consider ", "try to", "avoid "]) {
    assert.ok(!words.includes(phrase), `advice wording found: "${phrase}"`);
  }
  const source = readFileSync(new URL("./MyTradingPanel.jsx", import.meta.url), "utf8").toLowerCase();
  assert.ok(!source.includes("you should"));
});

test("the screen only reads: the one write is the refresh request, and no order route is named", () => {
  const source = readFileSync(new URL("./MyTradingPanel.jsx", import.meta.url), "utf8")
    + readFileSync(new URL("./myTrading.js", import.meta.url), "utf8");
  const posts = source.match(/method:\s*"(POST|PUT|PATCH|DELETE)"/g) || [];
  assert.equal(posts.length, 1);
  assert.match(source, /MY_TRADING_REFRESH_URL, \{ method: "POST"/);
  assert.doesNotMatch(source, /\/orders|place_?order|cancel_?order|replace_?order|\/trader\/v1/i);
});

test("the panel hides the spread tiles when nothing was measured and shows the same-days basis", () => {
  const source = readFileSync(new URL("./MyTradingPanel.jsx", import.meta.url), "utf8");
  assert.match(source, /\{execution\.measured \? \(\s*<div className="mt-exec">/);
  assert.match(source, /<h3>You vs the paper bots, same days<\/h3><span className="mt-basis">\{versusBasis\}<\/span>/);
  assert.match(source, /const versusBasis = myVersusBasis\(doc\);/);
});
