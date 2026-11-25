// Unit: the Bot home page's words and numbers (botHome.js). 2026-11-25.
// The fixtures are cut from the real GET /api/momx-scanner/bot-desk answer of that night:
// Paper 3 out of money at Alpaca, 67 closed trades, −$5,124.
import assert from "node:assert/strict";
import test from "node:test";

import {
  HOME_MORE, HOME_SECTIONS, HOME_TRADE_FILTERS,
  homeCalendar, homeCallsPuts, homeChecks, homeClosedTrades, homeDay, homeDayGroups, homeDayHead, homeExitWord, homeFewNote, homeFreshness,
  homeJournalTiles, homeLessons, homeLevel, homeMissedRows, homeMonths, homeNewestDay, homeOpenRows, homePct, homePeriodRows, homePlainNote,
  homeResult, homeShortDay, homeSide, homeSpark, homeStamp, homeState, homeTiles, homeTime, homeTone, homeTopLesson, homeTrades, homeVerdict,
  homeWeekday, readHomeAccount, readHomeFilter, readHomeSection,
} from "./botHome.js";

const LIMITS = { accountSize: 10000, profitTarget: 1000, dailyLossLimit: 500, maxDrawdown: 1000, minDays: 20, minTrades: 100,
  minProfitFactor: 1.3, minFillPct: 50, luckTrades: 3 };
const PLAN = { entryAsk: 0.87, entryStock: 21.38, stop: 26.13, stopNow: 26.13, stopKind: "stop", t1: 19.44, t2: 17.51, t3: 17.5, hits: [], ready: true };

const closed = (symbol, side, day, boughtAt, closedAt, pnl, roiPct, exit, extra = {}) => ({
  symbol, side, rule: "level2", levels: ["level1", "level2"], day, boughtAt, closedAt, qty: 3, buy: 0.87, pnl, roiPct, exit,
  contract: `${symbol}   261127P00019000`, aiPicked: false, plan: PLAN, ...extra,
});

const TRADES = [
  closed("KSS", "bear", "2026-11-23", "2026-11-23T09:49:05-05:00", "2026-11-23T14:22:00-05:00", -120, -46, "5-minute candle closed above the 21 EMA"),
  closed("DECK", "bull", "2026-11-23", "2026-11-23T10:07:26-05:00", "2026-11-23T10:15:23-05:00", -240, -80, "Stop: option down 50% (backstop)", { rule: "level1", levels: ["level1"], qty: 4, buy: 0.75 }),
  closed("MCD", "bull", "2026-11-23", "2026-11-20T13:53:32-05:00", "2026-11-23T09:54:34-05:00", -219, -52, "Stop: option down 50% (backstop)"),
  closed("NVDA", "bull", "2026-11-19", "2026-11-19T09:40:00-05:00", "2026-11-19T15:50:00-05:00", 696, 112, "15:50 close",
    { plan: { ...PLAN, hits: [{ target: "T1", at: "2026-11-19T10:00:16-05:00" }] } }),
  closed("FLAT", "bull", "2026-11-19", "2026-11-19T09:41:00-05:00", "2026-11-19T11:00:00-05:00", 0, 0, "Stop: stock traded through 54.10"),
];

const day = (key, trades, pnl, calls, puts) => ({ key, trades, wins: pnl > 0 ? 1 : 0, losses: trades, winRatePct: pnl > 0 ? 50 : 0, pnl, roiPct: -10, calls, puts, best: null });

const PAPER3 = {
  orders: { sent: 70, filled: 68, missed: 2, skipped: 0, holding: 1, stuck: 0, fillRatePct: 97.1 },
  stats: { n: 67, wins: 14, losses: 50, flat: 3, winRatePct: 20.9, netPnl: -5124, roiPct: -20, cost: 25616, avgWin: 315.21, avgLoss: -190.74,
    profitFactor: 0.46, expectancy: -76.48, bestTrade: 1300, worstTrade: -1005, pnlWithoutBest: -8164 },
  days: [{ day: "2026-11-13", closed: 18, pnl: -2116, cum: -2116 }, { day: "2026-11-19", closed: 9, pnl: 696, cum: -1420 }, { day: "2026-11-23", closed: 4, pnl: -3704, cum: -5124 }],
  daySummary: { tradingDays: 7, greenDays: 2, redDays: 5, maxDrawdown: 5124, dailyLimitBreaks: 5 },
  journal: {
    periods: {
      day: [day("2026-11-23", 3, -579, -459, -120), day("2026-11-19", 2, 696, 696, 0), day("2026-10-30", 1, -50, -50, 0)],
      week: [day("2026-11-23", 3, -579, -459, -120), day("2026-11-16", 2, 696, 696, 0)],
      month: [day("2026-11", 67, -5124, -4740, -384), day("2026-10", 1, -50, -50, 0)],
    },
    trades: TRADES, tradeCount: 67,
  },
  tradeLog: { counts: { open: 1, closed: 67, missed: 1, waiting: 1, skipped: 0, signal: 0 }, total: 70, rows: [
    { key: "bear|level2|COP|x", status: "open", symbol: "COP", side: "bear", rule: "level2", levels: ["level1", "level2"], at: "2026-11-23T15:20:45-05:00",
      boughtAt: "2026-11-23T15:24:20-05:00", qty: 3, buy: 0.76, limit: 0.76, pnl: -51, roiPct: -22.4, live: true, markAt: "2026-11-24T16:01:00.235592-05:00", note: "holding", plan: PLAN },
    { key: "bull|level1|WAIT|x", status: "waiting", symbol: "WAIT", side: "bull", rule: "level1", at: "2026-11-23T15:21:00-05:00", qty: 2, buy: null, limit: 1.1, pnl: null, roiPct: null, note: "", plan: null },
    { key: "c", status: "closed", symbol: "KSS", side: "bear", at: "2026-11-23T09:49:05-05:00", pnl: -120 },
    { key: "bear|level2|M|x", status: "missed", symbol: "M", side: "bear", rule: "level2", levels: ["level1", "level2"], at: "2026-11-23T09:47:04-05:00", qty: 15, buy: null, limit: 0.19,
      pnl: null, roiPct: null, note: "The broker refused this buy: not enough buying power in the paper account", plan: { ...PLAN, stop: null } },
  ] },
  objectives: [
    { key: "profit", target: 1000, actual: -5124, pass: false }, { key: "dailyLoss", target: -500, actual: -2116, pass: false, breaks: 5 },
    { key: "drawdown", target: 1000, actual: 5124, pass: false }, { key: "days", target: 20, actual: 7, pass: false },
    { key: "trades", target: 100, actual: 67, pass: false }, { key: "profitFactor", target: 1.3, actual: 0.46, pass: false },
    { key: "luck", target: 0, actual: -8164, pass: false, trades: 3 }, { key: "fill", target: 50, actual: 97.1, pass: true },
  ],
  verdict: { ready: false, passed: 1, total: 8 },
  lastActivity: "2026-11-23T15:24:20-05:00", balance: 4876,
  mode: { key: "rules", name: "Rules bot" },
  broker: { at: "2026-11-25T01:19:29-05:00", cash: 9.85, optionsBuyingPower: 9.85, buyingPower: 9.85, equity: 27.85, lastEquity: 159.85, error: null },
  control: { paused: false, pausedAt: null, dailyStop: { on: false, limit: 500, hit: false, hitAt: null, todayPnl: 0 } },
};

// What the server sends for an account that has not traded (momx/bot_desk.py with no orders).
const EMPTY = {
  orders: { sent: 0, filled: 0, missed: 0, skipped: 0, holding: 0, stuck: 0, fillRatePct: null },
  stats: { n: 0, wins: 0, losses: 0, flat: 0, winRatePct: null, netPnl: 0, roiPct: null, cost: 0, avgWin: null, avgLoss: null, profitFactor: null,
    expectancy: null, bestTrade: null, worstTrade: null, pnlWithoutBest: null },
  days: [], daySummary: { tradingDays: 0, greenDays: 0, redDays: 0, bestDay: null, worstDay: null, maxDrawdown: 0, dailyLimitBreaks: 0 },
  journal: { periods: { day: [], week: [], month: [] }, trades: [], tradeCount: 0 },
  tradeLog: { rows: [], counts: { open: 0, closed: 0, missed: 0, waiting: 0, skipped: 0, signal: 0 }, total: 0 },
  objectives: [
    { key: "profit", target: 1000, actual: 0, pass: false }, { key: "dailyLoss", target: -500, actual: null, pass: true, breaks: 0 },
    { key: "drawdown", target: 1000, actual: 0, pass: true }, { key: "days", target: 20, actual: 0, pass: false },
    { key: "trades", target: 100, actual: 0, pass: false }, { key: "profitFactor", target: 1.3, actual: null, pass: false },
    { key: "luck", target: 0, actual: null, pass: false, trades: 3 }, { key: "fill", target: 50, actual: null, pass: false },
  ],
  verdict: { ready: false, passed: 2, total: 8 }, lastActivity: null, balance: 10000, broker: null,
  control: { paused: false, pausedAt: null, dailyStop: { on: false, limit: 500, hit: false, hitAt: null, todayPnl: 0 } },
};

const LESSONS = { updatedAt: "2026-11-24T17:02:27-05:00", startDay: "2026-11-13", minLosses: 10, note: "Suggestions only.", lessons: [
  { side: "bear", rule: "level3", losses: 6, wins: 6, netPnl: -756, status: "collecting", source: "none", lesson: "Not enough losing trades yet (6 of 10).", proposedChange: "", howToTest: "" },
  { side: "bull", rule: "level2", losses: 25, wins: 3, netPnl: -3581, status: "numbers", source: "numbers only", lesson: "18 of the 25 losing trades were in the group 'first 30 minutes' (1 winning trades were).", proposedChange: "" },
  { side: "bull", rule: "level1", losses: 31, wins: 4, netPnl: -4740, status: "lesson", source: "openai", lesson: "Most losing trades were filled 10%+ above the signal price.",
    proposedChange: "Skip the trade when the order fills 10%+ above the signal price.", howToTest: "Track 20 paper trades." },
] };

const DOC = { available: true, days: 90, asOf: "2026-11-25T01:19:47-05:00", limits: LIMITS, smallSample: 20, startDay: "2026-11-13", paused: false,
  lessons: LESSONS, accounts: { paper3: PAPER3, paper5: EMPTY } };

test("dates and times are read as the server wrote them, in plain words", () => {
  assert.equal(homeDay("2026-11-23T15:24:20-05:00"), "Mon, Nov 23");
  assert.equal(homeDay("2026-11-13"), "Fri, Nov 13");
  assert.equal(homeShortDay("2026-11-13"), "Nov 13");
  assert.equal(homeWeekday("2026-11-20T13:53:32-05:00"), "Fri");
  assert.equal(homeTime("2026-11-20T13:53:32-05:00"), "1:53 PM");
  assert.equal(homeTime("2026-11-23T09:49:05-05:00"), "9:49 AM");
  assert.equal(homeTime("2026-11-23T00:05:00-05:00"), "12:05 AM");
  assert.equal(homeTime("2026-11-23T12:00:00-05:00"), "12:00 PM");
  assert.equal(homeStamp("2026-11-25T01:19:47-05:00"), "Nov 25, 1:19 AM");
  for (const bad of [null, undefined, "", "soon", 5, "2026-13-40"]) {
    assert.equal(homeDay(bad), "");
    assert.equal(homeShortDay(bad), "");
    assert.equal(homeTime(bad), "");
    assert.equal(homeStamp(bad), "");
  }
});

test("a missing number is a dash and never a zero (Number(null) is 0 in JS)", () => {
  assert.equal(homePct(null), "–");
  assert.equal(homePct(undefined), "–");
  assert.equal(homePct("12"), "–");
  assert.equal(homePct(-46), "−46%");
  assert.equal(homePct(112.4), "+112%");
  assert.equal(homePct(0), "0%");
  assert.equal(homePct(-0.3), "0%");
  assert.equal(homeTone(null), "");
  assert.equal(homeTone(0), "");
  assert.equal(homeTone(5), "is-up");
  assert.equal(homeTone(-5), "is-down");
});

test("side, level and the saved section, account and filter", () => {
  assert.equal(homeSide("bull"), "Call");
  assert.equal(homeSide("bear"), "Put");
  assert.equal(homeLevel("level2", ["level1", "level2"]), "Level 2");
  assert.equal(homeLevel("level1"), "Level 1");
  assert.equal(homeLevel("level3", []), "Level 3");
  assert.equal(homeLevel("rvol"), "rvol");
  assert.equal(homeLevel(null), "");
  assert.deepEqual(HOME_SECTIONS.map(([key, label]) => `${key}:${label}`), ["home:Home", "journal:Journal", "trades:Trades", "learned:Learned", "ready:Ready"]);
  assert.equal(readHomeSection("trades"), "trades");
  assert.equal(readHomeSection("desk"), "home");
  assert.equal(readHomeSection(null), "home");
  assert.equal(readHomeAccount("paper5", DOC), "paper5");
  assert.equal(readHomeAccount("paper9", DOC), "paper3");
  assert.equal(readHomeAccount("paper5", { accounts: { paper3: PAPER3 } }), "paper3");
  assert.equal(readHomeAccount(null, null), "paper3");
  assert.equal(readHomeAccount("paper5", null), "paper5");
  assert.equal(readHomeFilter("missed"), "missed");
  assert.equal(readHomeFilter("closed"), "all");
});

test("the exit sentence becomes a few plain words for the row", () => {
  assert.equal(homeExitWord("Stop: option down 50% (backstop)"), "sold at the −50% stop");
  assert.equal(homeExitWord("5-minute candle closed above the 21 EMA"), "trend turned");
  assert.equal(homeExitWord("5-minute candle closed below the 21 EMA"), "trend turned");
  assert.equal(homeExitWord("15:50 close"), "sold at the close");
  assert.equal(homeExitWord("Stop: stock traded through 54.10"), "sold at the stop");
  assert.equal(homeExitWord("Sold 1 at T2"), "target reached");
  assert.equal(homeExitWord("Broker-confirmed worthless expiration"), "expired worthless");
  assert.equal(homeExitWord("Sold by hand"), "sold by hand");
  assert.equal(homeExitWord(""), "sold");
  assert.equal(homeExitWord(null), "sold");
});

test("a saved note never shows a broker reply, a program name or a status code", () => {
  assert.equal(homePlainNote("The broker refused this buy: not enough buying power in the paper account"), "The broker refused this buy: not enough buying power in the paper account");
  assert.equal(homePlainNote('Awaiting broker reconciliation: {"code":40410000,"message":"order not found"}'), "Awaiting broker reconciliation");
  assert.equal(homePlainNote("ValueError: experiment proposal failed", "The order was not filled."), "The order was not filled.");
  assert.equal(homePlainNote("OpenAI HTTP 429: quota", "x"), "x");
  assert.equal(homePlainNote("Traceback (most recent call last)", "x"), "x");
  assert.equal(homePlainNote(null, "x"), "x");
  assert.equal(homePlainNote("Stop: stock traded through 412.50"), "Stop: stock traded through 412.50");
});

test("state line: cannot buy when the Alpaca paper account is under the Scorecard's $300 line", () => {
  const s = homeState(DOC, PAPER3);
  assert.equal(s.kind, "cannot");
  assert.equal(s.title, "Cannot buy: the Alpaca paper account has $10 left");
  assert.match(s.line, /Reset or top up the paper account at Alpaca/);
  assert.equal(s.lastOrder, "Last order Mon, Nov 23");
  assert.equal(s.read, "Alpaca balance read Nov 25, 1:19 AM ET.");
  // Out of money AND paused: still "cannot buy", and the pause is named.
  const both = homeState({ ...DOC, paused: true }, PAPER3);
  assert.equal(both.kind, "cannot");
  assert.match(both.line, /Buying is also paused\.$/);
  // The cash figure is used when the options buying power is missing; $299.99 is still short, $300 is not.
  assert.equal(homeState(DOC, { ...PAPER3, broker: { at: "2026-11-25T01:00:00-05:00", cash: 299.99, optionsBuyingPower: null } }).kind, "cannot");
  assert.equal(homeState(DOC, { ...PAPER3, broker: { at: "2026-11-25T01:00:00-05:00", cash: 300, optionsBuyingPower: 300 } }).kind, "on");
});

test("state line: buying is paused (the account's Pause, the master switch, or today's loss stop)", () => {
  const rich = { ...PAPER3, broker: { ...PAPER3.broker, cash: 9000, optionsBuyingPower: 9000 } };
  const paused = homeState(DOC, { ...rich, control: { paused: true, pausedAt: "2026-11-24T10:00:00-05:00", dailyStop: {} } });
  assert.equal(paused.kind, "paused");
  assert.equal(paused.title, "Buying is paused");
  assert.match(paused.line, /until you resume it/);
  const off = homeState({ ...DOC, paused: true }, rich);
  assert.equal(off.kind, "paused");
  assert.match(off.line, /switched off/);
  const held = homeState(DOC, { ...rich, control: { paused: false, dailyStop: { on: true, hit: true } } });
  assert.equal(held.kind, "paused");
  assert.match(held.line, /loss limit was hit/);
});

test("state line: buying is on, with what there is to spend", () => {
  const s = homeState(DOC, { ...PAPER3, broker: { at: "2026-11-25T01:19:29-05:00", cash: 9000, optionsBuyingPower: 8450.4 } });
  assert.equal(s.kind, "on");
  assert.equal(s.title, "Buying is on");
  assert.equal(s.line, "The Alpaca paper account has $8,450 to spend.");
  // No broker read yet: never "cannot buy $0" from a missing number.
  const unread = homeState(DOC, EMPTY);
  assert.equal(unread.kind, "on");
  assert.equal(unread.line, "The Alpaca balance has not been read yet.");
  assert.equal(unread.lastOrder, "No order placed yet");
  assert.equal(unread.read, "");
  assert.equal(homeState(DOC, { ...PAPER3, broker: { at: "", cash: 0 } }).kind, "on");
  assert.equal(homeState(DOC, { ...PAPER3, broker: { at: "2026-11-25T01:00:00-05:00", cash: null, optionsBuyingPower: null } }).kind, "on");
  assert.equal(homeState(null, null).kind, "none");
});

test("one money story: the real result, the start size, the trade count and the start day", () => {
  const r = homeResult(DOC, PAPER3);
  assert.equal(r.value, "−$5,124");
  assert.equal(r.tone, "is-down");
  assert.equal(r.line, "from a $10,000 start · 67 closed trades · since Nov 13");
  assert.equal(r.days, "2 winning days of 7");
  assert.equal(r.few, "");
  assert.equal(r.empty, false);
  // The notional balance ($4,876) is not part of the story.
  assert.ok(!JSON.stringify(r).includes("4,876"));
  const few = homeResult(DOC, { ...PAPER3, stats: { ...PAPER3.stats, n: 8 } });
  assert.equal(few.few, "8 closed trades: too few to judge (needs 20)");
  assert.equal(homeFewNote(1, DOC), "1 closed trade: too few to judge (needs 20)");
  assert.equal(homeFewNote(20, DOC), "");
  assert.equal(homeFewNote(0, DOC), "");
  assert.equal(homeFewNote(null, DOC), "");
  assert.equal(homeFewNote(5, { smallSample: 3 }), "");
});

test("an account with no closed trades says so instead of showing numbers", () => {
  const r = homeResult(DOC, EMPTY);
  assert.deepEqual([r.empty, r.value, r.tone, r.days, r.few], [true, "$0", "", "", ""]);
  assert.equal(r.line, "from a $10,000 start · 0 closed trades · since Nov 13");
  assert.equal(homeSpark(EMPTY.days), null);
  assert.deepEqual(homeTiles(EMPTY).map((t) => t.value), ["–", "–", "0"]);
  assert.deepEqual(homeJournalTiles(DOC, EMPTY).map((t) => t.value), ["$0", "–", "–", "–", "–", "–"]);
  assert.deepEqual(homeMonths(EMPTY), []);
  assert.equal(homeNewestDay(EMPTY), "");
  assert.equal(homeCallsPuts(EMPTY), null);
  assert.deepEqual(homeClosedTrades(EMPTY), []);
  const t = homeTrades(EMPTY, "all");
  assert.deepEqual(t.filters.map((f) => f.count), [0, 0, 0, 0, 0]);
  assert.deepEqual(t.empty, { title: "No trades yet", line: "Each trade shows here once the paper account buys it." });
  assert.equal(homeVerdict(EMPTY).title, "No paper trades yet");
  // Nothing at all from the server: every function still answers.
  for (const acct of [null, undefined, {}]) {
    assert.equal(homeResult(null, acct).value, "$0");
    assert.deepEqual(homeClosedTrades(acct), []);
    assert.deepEqual(homeOpenRows(acct), []);
    assert.deepEqual(homeMissedRows(acct), []);
    assert.deepEqual(homeChecks(null, acct), []);
    assert.equal(homeTrades(acct).empty.title, "No trades yet");
    assert.equal(homeCalendar(acct, "2026-11").cells.filter((c) => c && c.has).length, 0);
    assert.deepEqual(homePeriodRows(acct, "week"), []);
  }
});

test("the account line starts at $0 and ends on the result", () => {
  const s = homeSpark(PAPER3.days);
  assert.equal(s.line.split(" ").length, 4);
  assert.equal(s.line.split(" ")[0], `0,${s.zeroY}`);
  assert.equal(s.last, -5124);
  assert.equal(s.tone, "is-down");
  assert.equal(s.label, "Result by day, from $0 to −$5,124");
  assert.equal(s.zeroY, 5);                                    // $0 is the highest point: the top of the frame
  // Days without a number are left out, and one flat day does not divide by zero.
  assert.equal(homeSpark([{ day: "a", cum: null }, { day: "b", cum: 0 }]).line, "0,32 320,32");
  assert.equal(homeSpark(null), null);
});

test("Home tiles say how many trades they rest on", () => {
  const tiles = homeTiles(PAPER3);
  assert.deepEqual(tiles.map((t) => [t.label, t.value, t.sub]), [
    ["Won", "14 of 67", "21% of closed trades"],
    ["Per trade, on average", "−$76", "on 67 trades"],
    ["Open now", "2", "trades"],
  ]);
  assert.equal(tiles[1].tone, "is-down");
});

test("the Home banner and the teaser", () => {
  assert.deepEqual(homeVerdict(PAPER3), { tone: "notready", title: "Not ready for real money", line: "1 of 8 checks passed" });
  assert.equal(homeVerdict({ verdict: { ready: true, passed: 8, total: 8 } }).title, "Ready for real money");
  assert.equal(homeVerdict(null).tone, "empty");
  assert.equal(homeTopLesson(DOC), "Most losing trades were filled 10%+ above the signal price.");
  assert.equal(homeTopLesson({ lessons: { lessons: [LESSONS.lessons[0]] } }), null);     // still collecting: nothing to tease
  assert.equal(homeTopLesson({ lessons: null }), null);
  assert.deepEqual(HOME_MORE.map((m) => `${m.name}|${m.title}`), ["ticker|Trade one ticker", "scorecard|Full scorecard",
    "research|Research: every signal, simulated", "ask|Ask the bot"]);
});

test("a closed trade as a row and as its detail", () => {
  const [kss, deck, mcd, nvda] = homeClosedTrades(PAPER3);
  assert.equal(kss.title, "KSS · Put");
  assert.equal(kss.sub, "9:49 AM · trend turned");
  assert.deepEqual([kss.value, kss.roi, kss.tone, kss.day], ["−$120", "−46%", "is-down", "2026-11-23"]);
  assert.deepEqual(kss.detail, [
    ["Signal", "Level 2"], ["Bought", "3 at $0.87 · 9:49 AM"], ["Sold", "2:22 PM"], ["Stock when bought", "21.38"],
    ["Stop (stock price)", "26.13"], ["Targets", "19.44 · 17.51 · 17.50"], ["Targets reached", "none"],
  ]);
  assert.equal(kss.sentence, "5-minute candle closed above the 21 EMA");
  assert.equal(kss.stopNote, "");
  assert.equal(deck.detail[0][1], "Level 1");
  assert.equal(deck.detail[1][1], "4 at $0.75 · 10:07 AM");
  // Bought on an earlier day than it closed: the row and the detail name the day.
  assert.equal(mcd.sub, "bought Fri 1:53 PM · sold at the −50% stop");
  assert.equal(mcd.detail[1][1], "3 at $0.87 · Fri 1:53 PM");
  assert.equal(mcd.detail[2][1], "Mon 9:54 AM");
  assert.equal(nvda.detail[6][1], "1 of 3");
  assert.equal(nvda.sub, "9:40 AM · sold at the close");
  assert.equal(new Set(homeClosedTrades(PAPER3).map((t) => t.id)).size, 5);
});

test("a −50% stop that sold worse than −55% is said plainly; a stop that held is not flagged", () => {
  const [, deck, mcd] = homeClosedTrades(PAPER3);
  assert.equal(deck.stopNote, "The stop was set at −50% but the sale filled at −80%.");
  assert.equal(mcd.stopNote, "");                                               // −52%: inside the allowance
  const edge = (roiPct, exit = "Stop: option down 50% (backstop)") => homeClosedTrades({ journal: { trades: [closed("X", "bull", "2026-11-23", null, null, -1, roiPct, exit)] } })[0].stopNote;
  assert.equal(edge(-55), "");
  assert.equal(edge(-55.1), "The stop was set at −50% but the sale filled at −55%.");
  assert.equal(edge(null), "");                                                 // a missing return is not "0%" and not a miss
  assert.equal(edge(-90, "15:50 close"), "");
});

test("a trade with no plan, no times and no numbers still renders as dashes", () => {
  const [t] = homeClosedTrades({ journal: { trades: [{ symbol: "OLD", side: "bull", day: "2026-11-13", pnl: null, roiPct: null, plan: null }, null, "x"] } });
  assert.equal(t.title, "OLD · Call");
  assert.deepEqual([t.value, t.roi, t.tone, t.sub], ["–", "–", "", "sold"]);
  assert.deepEqual(t.detail.map(([, v]) => v), ["–", "–", "–", "–", "–", "–", "–"]);
});

test("open trades come from the trade log, and an estimate is called an estimate", () => {
  const [cop, wait] = homeOpenRows(PAPER3);
  assert.equal(cop.title, "COP · Put");
  assert.equal(cop.sub, "Nov 23, 3:24 PM · holding");
  assert.deepEqual([cop.value, cop.roi], ["−$51", "−22% so far"]);
  assert.equal(cop.detail[1][0], "Bought");
  assert.equal(cop.detail[1][1], "3 at $0.76 · 3:24 PM");
  assert.equal(cop.sentence, "Not sold yet. The result is an estimate from the price at Nov 24, 4:01 PM.");
  assert.equal(wait.sub, "Nov 23, 3:21 PM · waiting for a fill");
  assert.deepEqual([wait.value, wait.roi], ["–", ""]);
  assert.deepEqual(wait.detail[1], ["Order", "2 at $1.10 · 3:21 PM"]);
  const old = homeOpenRows({ tradeLog: { rows: [{ status: "open", symbol: "ZZ", side: "bull", at: "2026-11-12T10:00:00-05:00", pnl: null, note: "from before the fresh start, not scored · holding" }] } })[0];
  assert.equal(old.sentence, "From before the fresh start, not scored · holding. Not sold yet.");
});

test("orders that were not bought carry the saved reason", () => {
  const [m] = homeMissedRows(PAPER3);
  assert.equal(m.title, "M · Put");
  assert.equal(m.value, "Not bought");
  assert.equal(m.sub, "Nov 23, 9:47 AM · not bought");
  assert.deepEqual(m.detail[1], ["Order", "15 at $0.19 · 9:47 AM"]);
  assert.equal(m.sentence, "The broker refused this buy: not enough buying power in the paper account");
  const raw = homeMissedRows({ tradeLog: { rows: [{ status: "missed", symbol: "Q", note: 'APIError: {"code":403}' }] } })[0];
  assert.equal(raw.sentence, "The order was sent and never filled.");
});

test("Trades: five counted filters, open on top, closed grouped by day newest first", () => {
  const all = homeTrades(PAPER3, "all");
  assert.deepEqual(HOME_TRADE_FILTERS.map(([, label]) => label), ["All", "Open", "Won", "Lost", "Not bought"]);
  assert.deepEqual(all.filters.map((f) => `${f.label} ${f.count}${f.on ? "*" : ""}`), ["All 69*", "Open 2", "Won 1", "Lost 3", "Not bought 1"]);
  assert.equal(all.open.length, 2);
  assert.deepEqual(all.groups.map((g) => [g.day, g.label, g.value, g.rows.length]), [["2026-11-23", "Mon, Nov 23", "−$579", 3], ["2026-11-19", "Thu, Nov 19", "+$696", 2]]);
  assert.equal(all.capped, "Showing the newest 5 of 67 closed trades.");
  assert.equal(all.empty, null);
  assert.deepEqual(all.missed, []);

  const won = homeTrades(PAPER3, "won");
  assert.deepEqual(won.groups.map((g) => g.rows.map((r) => r.title)), [["NVDA · Call"]]);
  assert.deepEqual([won.open.length, won.capped], [0, ""]);
  const lost = homeTrades(PAPER3, "lost");
  assert.equal(lost.groups[0].rows.length, 3);                                  // the flat trade is neither won nor lost
  assert.equal(homeTrades(PAPER3, "open").groups.length, 0);
  assert.equal(homeTrades(PAPER3, "open").open.length, 2);
  const missed = homeTrades(PAPER3, "missed");
  assert.deepEqual([missed.missed.length, missed.groups.length, missed.open.length], [1, 0, 0]);
  assert.equal(homeTrades(PAPER3, "nonsense").filter, "all");
  assert.deepEqual(homeTrades({ ...PAPER3, tradeLog: null }, "open").empty, { title: "No open trades", line: "The bot is holding nothing right now." });
  assert.equal(homeTrades({ ...PAPER3, tradeLog: null }, "missed").empty.title, "Every order was bought");
});

test("day grouping sums real numbers only and skips rows with no day", () => {
  const groups = homeDayGroups([{ day: "2026-11-02", pnl: 10 }, { day: "2026-11-03", pnl: null }, { day: "2026-11-02", pnl: -4 }, { day: "", pnl: 99 }, null]);
  assert.deepEqual(groups.map((g) => [g.day, g.pnl, g.value, g.tone, g.rows.length]), [["2026-11-03", 0, "$0", "", 1], ["2026-11-02", 6, "+$6", "is-up", 2]]);
  assert.deepEqual(homeDayGroups(null), []);
});

test("journal: six tiles in plain words", () => {
  const tiles = homeJournalTiles(DOC, PAPER3);
  assert.deepEqual(tiles.map((t) => [t.label, t.value, t.sub]), [
    ["Result", "−$5,124", "67 closed trades"],
    ["Won", "21%", "14 wins · 50 losses"],
    ["Average win", "+$315", "best +$1,300"],
    ["Average loss", "−$191", "worst −$1,005"],
    ["Made per $1 lost", "$0.46", "needs $1.30"],
    ["Biggest drop", "−$5,124", "from the highest point"],
  ]);
  assert.ok(!tiles.some((t) => /expectancy|profit factor|drawdown/i.test(t.label + t.sub)));
});

test("calendar: a month that starts mid-week leaves blanks before the first weekday", () => {
  // October 2026 starts on a Thursday: three blanks (Mon, Tue, Wed), then 1 and 2.
  const oct = homeCalendar(PAPER3, "2026-10", "2026-10-30");
  assert.deepEqual(oct.cells.slice(0, 5).map((c) => (c ? c.num : null)), [null, null, null, 1, 2]);
  assert.equal(oct.cells.filter(Boolean).length, 22);
  assert.equal(oct.label, "October 2026");
  assert.deepEqual([oct.prev, oct.next], ["", "2026-11"]);
  const day30 = oct.cells.find((c) => c && c.num === 30);
  assert.deepEqual([day30.has, day30.selected, day30.value, day30.tone, day30.label], [true, true, "−$50", "is-down", "Fri, Oct 30: −$50 on 1 trade"]);
  // November 2026 starts on a Sunday: Monday the 2nd is the first cell, no blanks, no weekend days.
  const nov = homeCalendar(PAPER3, "2026-11", "2026-11-19");
  assert.equal(nov.cells[0].num, 2);
  assert.equal(nov.cells.length, 21);
  assert.equal(nov.cells.length % 5, 1);
  assert.ok(nov.cells.every((c) => ![1, 7, 8, 14, 15, 21, 22, 28, 29].includes(c.num)));
  assert.deepEqual(nov.cells.filter((c) => c.has).map((c) => [c.key, c.value, c.selected]), [["2026-11-19", "+$696", true], ["2026-11-23", "−$579", false]]);
  assert.equal(nov.cells.find((c) => c.num === 3).label, "Tue, Nov 3");
  assert.deepEqual([nov.prev, nov.next], ["2026-10", ""]);
  // May 2026 starts on a Friday: four blanks.
  assert.deepEqual(homeCalendar(PAPER3, "2026-05").cells.slice(0, 5).map((c) => (c ? c.num : null)), [null, null, null, null, 1]);
  assert.equal(homeCalendar(PAPER3, "nonsense"), null);
  assert.equal(homeCalendar(PAPER3, "2026-13"), null);
  // A five-figure day is shortened so it fits a phone cell.
  const big = homeCalendar({ journal: { periods: { day: [day("2026-11-02", 2, -12345, 0, 0)] } } }, "2026-11");
  assert.equal(big.cells[0].value, "−$12k");
});

test("journal: months with trades, the newest day, the day heading", () => {
  assert.deepEqual(homeMonths(PAPER3), ["2026-10", "2026-11"]);
  assert.equal(homeNewestDay(PAPER3), "2026-11-23");
  assert.equal(homeNewestDay(PAPER3, "2026-10"), "2026-10-30");
  assert.equal(homeNewestDay(PAPER3, "2026-09"), "");
  assert.equal(homeDayHead(PAPER3, "2026-11-23"), "Mon, Nov 23 · −$579 · 3 trades");
  assert.equal(homeDayHead(PAPER3, "2026-10-30"), "Fri, Oct 30 · −$50 · 1 trade");
  assert.equal(homeDayHead(PAPER3, "2026-11-24"), "");
});

test("journal: the Day / Week / Month table and calls against puts", () => {
  assert.deepEqual(homePeriodRows(PAPER3, "day")[0], { key: "2026-11-23", label: "Mon, Nov 23", trades: 3, won: "0%", value: "−$579", tone: "is-down" });
  assert.deepEqual(homePeriodRows(PAPER3, "week").map((r) => r.label), ["Week of Nov 23", "Week of Nov 16"]);
  assert.deepEqual(homePeriodRows(PAPER3, "month").map((r) => [r.label, r.trades, r.value]), [["November 2026", 67, "−$5,124"], ["October 2026", 1, "−$50"]]);
  assert.equal(homePeriodRows(PAPER3, "year")[0].label, "Mon, Nov 23");
  assert.equal(homePeriodRows({ journal: { periods: { day: [{ key: "2026-11-23", trades: 0, winRatePct: null, pnl: null }] } } })[0].won, "–");
  assert.deepEqual(homeCallsPuts(PAPER3), [{ key: "calls", label: "Calls", value: "−$4,790", tone: "is-down" }, { key: "puts", label: "Puts", value: "−$384", tone: "is-down" }]);
});

test("Learned: each finding with its evidence, the suggestion and its true status", () => {
  const view = homeLessons(DOC);
  assert.equal(view.ready, true);
  assert.equal(view.ran, "The review last ran Tue, Nov 24, 5:02 PM ET.");
  assert.equal(view.applied, "Today nothing here changes what the bot buys.");
  const [collecting, numbers, lesson] = view.rows;
  assert.equal(lesson.evidence, "Calls · Level 1 · 31 lost, 4 won · −$4,740");
  assert.equal(lesson.sentence, "Most losing trades were filled 10%+ above the signal price.");
  assert.equal(lesson.change, "Skip the trade when the order fills 10%+ above the signal price.");
  assert.equal(lesson.status, "Suggestion only · not applied");
  assert.equal(numbers.evidence, "Calls · Level 2 · 25 lost, 3 won · −$3,581");
  assert.deepEqual([numbers.change, numbers.status], ["", "Suggestion only · not applied"]);
  assert.equal(collecting.evidence, "Puts · Level 3 · 6 lost, 6 won · −$756");
  assert.equal(collecting.sentence, "Not enough losing trades yet: 6 of the 10 the review needs.");
  assert.equal(collecting.status, "Still collecting trades");
  // No agent name and no error trace ever reaches the screen.
  assert.ok(!/openai|ollama|hermes|numbers only|Error/i.test(JSON.stringify(view)));
  const broken = homeLessons({ lessons: { updatedAt: "", lessons: [{ side: "bull", rule: "level1", lesson: "ValueError: Scanner specialist unavailable", status: "lesson" }, null] } });
  assert.equal(broken.rows.length, 1);
  assert.equal(broken.rows[0].sentence, "The review found no clear pattern.");
  assert.equal(broken.rows[0].evidence, "Calls · Level 1 · 0 lost, 0 won");
  assert.equal(broken.ran, "The review has run.");
});

test("Learned: no review yet, and a review with nothing in it", () => {
  const none = homeLessons({ lessons: null });
  assert.deepEqual([none.ready, none.title, none.rows.length], [false, "No review yet", 0]);
  assert.match(none.ran, /after the market closes/);
  assert.equal(homeLessons(null).ready, false);
  assert.equal(homeLessons({ lessons: { updatedAt: "2026-11-24T17:02:27-05:00", lessons: [] } }).empty, "No findings yet. The review needs at least 10 losing trades on a level.");
});

test("Ready: all eight checks as a plain line and the real figure", () => {
  assert.deepEqual(homeChecks(DOC, PAPER3).map((c) => [c.key, c.pass, c.title, c.figure]), [
    ["profit", false, "Account is up $1,000", "now −$5,124"],
    ["dailyLoss", false, "No day loses more than $500", "worst day −$2,116 · broken on 5 days"],
    ["drawdown", false, "Biggest drop stays under $1,000", "dropped $5,124 from the top"],
    ["days", false, "20 trading days", "7 so far"],
    ["trades", false, "100 closed trades", "67 so far"],
    ["profitFactor", false, "Makes $1.30 for each $1 lost", "makes $0.46"],
    ["luck", false, "Still up without the 3 best trades", "−$8,164 without them"],
    ["fill", true, "At least 50% of orders get filled", "97% filled"],
  ]);
  assert.deepEqual(homeChecks(DOC, PAPER3).map((c) => c.mark), ["Not passed", "Not passed", "Not passed", "Not passed", "Not passed", "Not passed", "Not passed", "Passed"]);
});

test("Ready: missing figures read as words, never as $0 or 0%", () => {
  assert.deepEqual(homeChecks(DOC, EMPTY).map((c) => c.figure), [
    "now $0", "no trading day yet", "no drop so far", "0 so far", "0 so far", "not enough trades yet", "not enough trades yet", "no order sent yet",
  ]);
  const one = (o) => homeChecks(DOC, { objectives: [o] })[0];
  assert.equal(one({ key: "dailyLoss", target: -500, actual: 120, pass: true, breaks: 0 }).figure, "no losing day so far");
  assert.equal(one({ key: "dailyLoss", target: -500, actual: -700, pass: false, breaks: 1 }).figure, "worst day −$700 · broken on 1 day");
  assert.equal(one({ key: "luck", target: 0, actual: 40, pass: true }).title, "Still up without the 3 best trades");
  assert.equal(homeChecks({ limits: { luckTrades: 5 } }, { objectives: [{ key: "luck", target: 0, actual: 40, pass: true }] })[0].title, "Still up without the 5 best trades");
  assert.equal(one({ key: "profitFactor", target: 1.3, actual: 0, pass: false }).figure, "makes $0.00");
  // A check this page has no words for is left out, never shown as a program key.
  assert.deepEqual(homeChecks(DOC, { objectives: [{ key: "sharpe", target: 1, actual: 2, pass: true }, null, { key: "days", target: null, actual: 2 }] }), []);
});

test("the freshness line: loading, saved copy, failed refresh, fresh", () => {
  assert.deepEqual(homeFreshness({ doc: null, failed: false }), { tone: "", text: "Loading the bot's numbers…" });
  assert.equal(homeFreshness({ doc: null, failed: true }).text, "Could not load the bot's numbers. Trying again shortly.");
  assert.deepEqual(homeFreshness({ doc: DOC, failed: false, saved: true }), { tone: "warn", text: "Saved copy from Nov 25, 1:19 AM ET · updating…" });
  assert.deepEqual(homeFreshness({ doc: DOC, failed: true, saved: true }), { tone: "bad", text: "Could not refresh. Showing the last numbers. Read Nov 25, 1:19 AM ET." });
  assert.deepEqual(homeFreshness({ doc: DOC, failed: false, saved: false }), { tone: "", text: "Read Nov 25, 1:19 AM ET" });
  assert.equal(homeFreshness({ doc: { accounts: {} }, failed: false, saved: true }).text, "Saved copy from your last visit · updating…");
  assert.ok(!/\b\d{3}\b|Error|fetch/.test(homeFreshness({ doc: DOC, failed: true }).text.replace("1:19", "")));
});

// The mount (BotPanel.jsx, 2026-11-25): which page the Bot opens on, and where a "More" row goes.
test("the Bot opens on the home page; an older page remembered from before it existed does not hide it", async () => {
  const { botLanding, botMoreTarget, HOME_SEEN_KEY } = await import("./botHome.js");
  assert.equal(HOME_SEEN_KEY, "bot.homeSeen");
  // First visit after the home page shipped: home, whatever was remembered.
  for (const stored of ["desk", "research", "ask", "home", null, undefined, "", "nonsense"]) assert.equal(botLanding(stored, null), "home", String(stored));
  // Later visits: a page asked for by name is honoured; anything else is home.
  assert.equal(botLanding("desk", "1"), "desk");
  assert.equal(botLanding("research", "1"), "research");
  assert.equal(botLanding("ask", "1"), "ask");
  for (const stored of ["home", null, "", "nonsense"]) assert.equal(botLanding(stored, "1"), "home", String(stored));
  assert.equal(botLanding("desk", "0"), "home");
  // Every More row on the page has somewhere to go, and nothing else does.
  assert.deepEqual(HOME_MORE.map((row) => botMoreTarget(row.name ?? row.key ?? row[0])), [
    { view: "research", ticker: true }, { view: "desk", ticker: null }, { view: "research", ticker: false }, { view: "ask", ticker: null },
  ]);
  assert.equal(botMoreTarget("settings"), null);
});

test("the panel row: five pages, the one showing lit, and no More tab (2026-11-25)", async () => {
  const { BOT_PANELS, HOME_KEPT_SECTIONS, botMoreTarget, botPanelCurrent, readHomeSection } = await import("./botHome.js");
  assert.deepEqual(BOT_PANELS.map(([key, label]) => `${key}:${label}`), ["bot:Bot", "ticker:Ticker", "scorecard:Scorecard", "research:Research", "ask:Ask"]);
  assert.deepEqual(HOME_KEPT_SECTIONS, HOME_SECTIONS.map(([key]) => key));
  assert.equal(readHomeSection("more"), "home");
  assert.equal(botPanelCurrent("home", true), "bot");
  assert.equal(botPanelCurrent("desk", true), "scorecard");
  assert.equal(botPanelCurrent("ask", false), "ask");
  assert.equal(botPanelCurrent("research", true), "ticker");
  assert.equal(botPanelCurrent("research", false), "research");
  // Every panel but Bot opens through the same targets as the More rows.
  assert.deepEqual(BOT_PANELS.slice(1).map(([key]) => botMoreTarget(key).view), ["research", "desk", "research", "ask"]);
});

test("a tab tapped on an older page opens the home page on that section (2026-11-25)", async () => {
  const { homeStartSection } = await import("./botHome.js");
  assert.equal(homeStartSection("journal", "trades"), "journal");
  assert.equal(homeStartSection("more", "trades"), "trades");
  assert.equal(homeStartSection(null, "trades"), "trades");
  assert.equal(homeStartSection("nonsense", "more"), "home");
});
