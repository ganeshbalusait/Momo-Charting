import assert from "node:assert/strict";
import test from "node:test";

import { cleanLink } from "./appLinks.js";
import {
  SIMULATED_NOTE, chartLink, cleanTicker, contractLabel, dayLabel, failureText, ladder, money, openGain, latestTradeFacts, rowMoney, rowPercent, runLine, signedMoney, sortRuns, tickersDashboard,
  statusLabel, tickerProblem, timeEt, tradeRows, winsLabel,
} from "./tickerTrader.js";

const OPEN = {
  id: "NVDA-CALL-1", day: "2026-11-13", ticker: "NVDA", side: "CALL", strike: 165, expiry: "2026-11-14", qty: 3, held: 2,
  entry: 1.45, entrySpot: 158.4, stopStock: 158.4, stopKind: "break-even", t1: 161, t2: 164.6, t3: 167, bid: 2.0,
  boughtAt: "2026-11-13T10:05:00-05:00",
  targets: [{ label: "T1", stock: 161, reason: "half the expected move" }, { label: "T2", stock: 164.6, reason: "full expected move" },
    { label: "T3", stock: 167, reason: "OI level past the move" }],
  hits: [{ target: "T1", at: "2026-11-13T10:31:00-05:00", stock: 161.02, sold: 1, price: 2.1 }],
};

test("a typed ticker is cleaned and checked before it is sent", () => {
  assert.equal(cleanTicker(" $nvda "), "NVDA");
  assert.equal(tickerProblem("brk.b"), "");
  assert.equal(tickerProblem(""), "Type a ticker first.");
  assert.match(tickerProblem("NV1DA"), /letters only/);
  assert.match(tickerProblem("TOOLONGX"), /up to 6/);
});

test("money, times and days read the way the tiles show them", () => {
  assert.equal(money(10186.4), "$10,186");
  assert.equal(money(null), "–");
  assert.equal(signedMoney(186), "+$186");
  assert.equal(signedMoney(-142.2), "−$142");
  assert.equal(signedMoney(0), "$0");
  assert.equal(signedMoney(null), "–");
  assert.equal(timeEt("2026-11-13T10:05:00-05:00"), "10:05 ET");
  assert.equal(timeEt("2026-11-13T09:30:07-05:00"), "9:30 ET");
  assert.equal(timeEt(""), "");
  assert.equal(dayLabel("2026-11-13"), "Nov 13");
  assert.equal(contractLabel(OPEN), "NVDA 165 Call · Nov 14");
});

test("the ladder runs T3, T2, T1, Entry, Stop and ticks what was hit", () => {
  const rows = ladder(OPEN);
  assert.deepEqual(rows.map((r) => r.key), ["T3", "T2", "T1", "Entry", "Stop"]);
  assert.deepEqual(rows[2], { key: "T1", kind: "target", hit: true, label: "T1 ✓", value: "161.00", note: "hit 10:31 ET · sold 1" });
  assert.equal(rows[0].hit, false);
  assert.equal(rows[0].note, "OI level past the move");
  assert.equal(rows[3].value, "158.40 · ask $1.45");
  assert.equal(rows[3].note, "3 contracts");
  // 2026-11-24: the Stop line also names the option's own stop price (bought at $1.45 -> sold at a bid of $0.72 or less).
  assert.deepEqual([rows[4].value, rows[4].note], ["158.40", "moved to break-even · or option bid $0.72"]);
  assert.equal(ladder({ ...OPEN, stopKind: "stop", hits: [] })[4].note, "or the option's bid at $0.72 (down 50%)");
  assert.equal(ladder({ ...OPEN, stopKind: "T1" })[4].note, "moved to T1 · or option bid $0.72");
  assert.equal(ladder({ ...OPEN, stopKind: "stop", entry: null })[4].note, "option down 50%");
  assert.equal(ladder({ ...OPEN, qty: 1, hits: [{ target: "T1", at: "2026-11-13T10:31:00-05:00", sold: 0 }] })[2].note, "hit 10:31 ET · none sold");
  assert.deepEqual(ladder(null), []);
});

test("the open gain needs a real bid", () => {
  assert.equal(openGain(OPEN), "+38%");
  assert.equal(openGain({ ...OPEN, bid: 1.0 }), "−31%");
  assert.equal(openGain({ ...OPEN, bid: null }), "");
  assert.equal(openGain({ ...OPEN, bid: undefined }), "");
});

test("a run says what it is doing", () => {
  const waiting = { id: "SPY-PUT", ticker: "SPY", side: "PUT", status: "waiting", note: "Market closed: buys at the 9:30 open", tradeCount: 0, wins: 0 };
  assert.equal(statusLabel(waiting), "Waiting to buy");
  assert.equal(runLine(waiting), "Market closed: buys at the 9:30 open");
  assert.equal(statusLabel({ side: "CALL", open: OPEN }), "In a trade · CALL");
  assert.equal(runLine({ status: "idle", tradeCount: 4, wins: 2 }), "4 trades · 2 wins");
  assert.equal(runLine({ status: "idle", tradeCount: 1, wins: 1 }), "1 trade · 1 win");
  assert.equal(runLine({ status: "idle", tradeCount: 0, note: "Not bought: one contract costs $1,200, more than the $1,000 cash left in the account." }),
    "Not bought: one contract costs $1,200, more than the $1,000 cash left in the account.");
  assert.equal(winsLabel({ tradeCount: 3, wins: 2 }), "2 of 3");
  assert.equal(winsLabel({ tradeCount: 0, wins: 0 }), "–");
  assert.equal(statusLabel({ status: "stopped" }), "Stopped");
});

test("the ticker list shows the open trade's profit at the bid next to the ticker (2026-11-18)", () => {
  // OPEN: 2 held, bought at $1.45, bid $2.00 -> +$110 open, +38%; $30 already banked from T1.
  const run = { ticker: "NVDA", side: "CALL", open: OPEN, note: "In a trade: holding 2 of 3", net: 30, openPl: 110, liveNet: 140 };
  assert.equal(rowMoney(run), 140);
  assert.equal(signedMoney(rowMoney(run)), "+$140");
  assert.equal(runLine(run), "In a trade: holding 2 of 3 · open +$110 (+38%)");
  // No bid seen yet: no open figure, the list keeps the banked money.
  assert.equal(runLine({ ...run, openPl: null, liveNet: 30 }), "In a trade: holding 2 of 3");
  assert.equal(rowMoney({ ...run, openPl: null, liveNet: 30 }), 30);
  // A server not yet restarted (no openPl / liveNet): worked out from the open trade's bid, entry and held.
  const older = { ticker: "NVDA", side: "CALL", open: OPEN, note: "In a trade: holding 2 of 3", net: 30 };
  assert.equal(rowMoney(older), 140);
  assert.equal(runLine(older), "In a trade: holding 2 of 3 · open +$110 (+38%)");
  assert.equal(rowMoney({ ...older, open: { ...OPEN, bid: null } }), 30);
  assert.equal(rowMoney({ net: -12 }), -12);
  // The % under the money: +$140 on the $435 the 3 contracts cost (cost missing -> entry x 100 x qty) = +32%.
  assert.equal(rowPercent(run), "+32%");
  // A closed trade: -$142 on $360 spent.
  assert.equal(rowPercent({ net: -142, trades: [{ cost: 360, pl: -142 }] }), "−39%");
  assert.equal(rowPercent({ net: 0, trades: [] }), "");                    // never bought: no %
  assert.equal(runLine({ status: "idle", tradeCount: 1, wins: 1, openPl: 50 }), "1 trade · 1 win");
});

test("Your Tickers: newest trade first, with its contract, expiry, date and time (2026-11-18)", () => {
  const spy = { id: "SPY-PUT", ticker: "SPY", side: "PUT", open: null, trades: [
    { strike: 590, side: "PUT", expiry: "2026-11-14", day: "2026-11-13", boughtAt: "2026-11-13T09:31:00-05:00", closedAt: "2026-11-13T09:52:00-05:00" }] };
  const avgo = { id: "AVGO-CALL", ticker: "AVGO", side: "CALL", trades: [],
    open: { strike: 410, side: "CALL", expiry: "2026-11-21", boughtAt: "2026-11-18T10:05:00-05:00" } };
  const lly = { id: "LLY-CALL", ticker: "LLY", side: "CALL", open: null, trades: [], noteAt: "2026-11-18T10:30:00-05:00" };
  assert.deepEqual(sortRuns([spy, lly, avgo]).map((r) => r.ticker), ["AVGO", "SPY", "LLY"]);   // never bought goes last
  assert.deepEqual(latestTradeFacts(avgo), { contract: "410 Call · exp Nov 21", when: "Bought Nov 18, 10:05 ET" });
  assert.deepEqual(latestTradeFacts(spy), { contract: "590 Put · exp Nov 14", when: "Nov 13, 9:31 → 9:52 ET" });
  assert.equal(latestTradeFacts(lly), null);
  // A later close beats an earlier buy; a trade held overnight names its close day.
  const old = { ...spy, trades: [{ ...spy.trades[0], closedAt: "2026-11-18T10:40:00-05:00" }] };
  assert.equal(sortRuns([avgo, old])[0].ticker, "SPY");
  assert.equal(latestTradeFacts(old).when, "Nov 13, 9:31 → 10:40 ET (Nov 18)");
});

test("the Your Tickers dashboard: today, win rate, trades, best and worst (2026-11-18)", () => {
  const runs = [
    { ticker: "SPY", trades: [{ pl: 5, closedAt: "2026-11-16T13:11:00-05:00" }], net: 5, open: null },
    { ticker: "AVGO", trades: [{ pl: -183, closedAt: "2026-11-18T11:39:00-05:00" }], net: -183, open: null },
    { ticker: "NVDA", trades: [], net: 30, open: { ...OPEN, pl: 30 }, note: "In a trade" },    // +$110 at the bid, $30 sold
    { ticker: "LLY", trades: [], net: 0, open: null },
  ];
  const d = tickersDashboard(runs, "2026-11-18T18:15:00-05:00");
  assert.deepEqual(d.today, { text: "−$43", tone: "is-down", note: "+$140 in 1 open" });     // -183 today + 140 open
  assert.deepEqual(d.winRate, { text: "50%", note: "1 of 2 won" });
  assert.deepEqual(d.trades, { text: "3", note: "2 closed · 1 open" });
  assert.deepEqual(d.best, { text: "NVDA +$140", tone: "is-up", note: "worst AVGO −$183" });  // LLY never bought: left out
  const none = tickersDashboard([{ ticker: "LLY", trades: [], open: null, net: 0 }], "2026-11-18T10:00:00-05:00");
  assert.equal(none.winRate.text, "–");
  assert.equal(none.best.text, "–");
});

test("closed trades are listed newest first with their reason", () => {
  const rows = tradeRows({ trades: [
    { id: "a", day: "2026-11-12", strike: 170, side: "CALL", pl: -142, reason: "Stop: stock traded through 166.80, sold 3 at $0.70.",
      boughtAt: "2026-11-12T10:00:00-05:00", closedAt: "2026-11-12T10:20:00-05:00", balanceAfter: 9858 },
    { id: "b", day: "2026-11-13", strike: 157, side: "PUT", pl: 96, reason: "T1 hit: sold 1 at $2.00, stop to break-even.",
      boughtAt: "2026-11-13T09:31:00-05:00", closedAt: "2026-11-13T11:02:00-05:00", balanceAfter: 9954 },
  ] });
  assert.deepEqual(rows.map((r) => r.key), ["b", "a"]);
  assert.deepEqual([rows[0].title, rows[0].plText, rows[0].tone, rows[0].when, rows[0].balanceAfter],
    ["Nov 13 · 157 Put", "+$96", "is-up", "9:31 ET to 11:02 ET", "$9,954"]);
  assert.equal(rows[1].tone, "is-down");
  assert.match(rows[1].reason, /^Stop: stock traded through 166\.80/);
  assert.deepEqual(tradeRows(null), []);
});

test("the chart button is a link the app will follow", () => {
  assert.deepEqual(cleanLink(chartLink("NVDA")), { label: "NVDA chart", view: "Charts & OI", ticker: "NVDA" });
});

test("failures are said in plain words, and the note says it is simulated", () => {
  assert.equal(failureText(400, { error: "No saved option chain for ZZZZ. Open its chart once, then try again." }),
    "No saved option chain for ZZZZ. Open its chart once, then try again.");
  assert.equal(failureText(403, { error: "admin only" }), "Only the owner can use the Ticker trader.");
  assert.equal(failureText(404, null), "The Ticker trader starts with the next app restart.");
  assert.equal(failureText(503, {}), "The Ticker trader starts with the next app restart.");
  assert.equal(failureText(0, null), "The Ticker trader did not answer. It will try again.");
  assert.equal(SIMULATED_NOTE, "Simulated inside the app. No order goes to any broker.");
});

// Account size (2026-11-13): quick buttons and any amount, $500 to $1,000,000.
import {
  DEFAULT_SIZE, SIZE_CHOICES, SIZE_KEY, parseSize, saveSize, savedSize, sizeChip, sizeProblem,
} from "./tickerTrader.js";

test("the account size accepts digits, commas and a leading $, in whole dollars", () => {
  assert.equal(parseSize("$5,000"), 5000);
  assert.equal(parseSize("5000"), 5000);
  assert.equal(parseSize(" 100,000 "), 100000);
  assert.equal(parseSize("2500.6"), 2501);
  assert.equal(parseSize("100k"), null, "k is not a shorthand here");
  assert.equal(parseSize("10K"), null);
  assert.equal(parseSize("abc"), null);
  assert.equal(parseSize("-5000"), null);
  assert.equal(parseSize(""), null);
  assert.deepEqual(SIZE_CHOICES.map(sizeChip), ["$2K", "$5K", "$10K", "$100K"]);
  assert.equal(DEFAULT_SIZE, 10000);
});

test("an account size outside $500 to $1,000,000 is explained, not sent", () => {
  assert.equal(sizeProblem("$10,000"), "");
  assert.equal(sizeProblem("500"), "");
  assert.equal(sizeProblem("1,000,000"), "");
  assert.equal(sizeProblem("499"), "Account size must be at least $500.");
  assert.equal(sizeProblem("1000001"), "Account size can be at most $1,000,000.");
  assert.match(sizeProblem("100k"), /digits only/);
  assert.match(sizeProblem(""), /Type an account size/);
});

test("the size is remembered on the device, and a broken store falls back to $10,000", () => {
  const box = new Map();
  const store = { getItem: (k) => (box.has(k) ? box.get(k) : null), setItem: (k, v) => box.set(k, v) };
  assert.equal(savedSize(store), 10000);
  saveSize(store, 5000);
  assert.equal(box.get(SIZE_KEY), "5000");
  assert.equal(savedSize(store), 5000);
  box.set(SIZE_KEY, "99");
  assert.equal(savedSize(store), 10000, "an out-of-range memory is ignored");
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(savedSize(throwing), 10000);
  assert.doesNotThrow(() => saveSize(throwing, 2000));
  assert.equal(savedSize(null), 10000);
});

test("the one account's tiles: worth now, cash left, up or down (2026-11-16)", async () => {
  const { accountTiles } = await import("./tickerTrader.js");
  // His morning: $2,000, a $144 trade and a $440 trade open, worth $570 at the bid.
  assert.deepEqual(accountTiles({ startBalance: 2000, cash: 1416, invested: 584, openValue: 570, value: 1986, net: -14, realized: 0, openTrades: 2 }),
    { value: "$1,986", from: "from $2,000", cash: "$1,416", inTrades: "$584 in 2 open trades", net: "\u2212$14", tone: accountTiles({ startBalance: 1, cash: 1, value: 1, net: -14 }).tone, start: 2000 });
  assert.equal(accountTiles({ startBalance: 2000, cash: 1560, invested: 440, value: 2020, net: 20, openTrades: 1 }).inTrades, "$440 in 1 open trade");
  const flat = accountTiles({ startBalance: 2000, cash: 2000, invested: 0, value: 2000, net: 0, openTrades: 0 });
  assert.equal(flat.inTrades, "nothing in trades");
  assert.equal(flat.net, "$0");
  assert.equal(accountTiles(null), null);                       // an older server sends no account: no card
  assert.equal(accountTiles({ startBalance: 2000 }), null);
});

test("the option stop price is the highest cent that is down half or more (2026-11-24)", async () => {
  const { optionStopPrice } = await import("./tickerTrader.js");
  assert.equal(optionStopPrice({ entry: 0.7 }), 0.35);                 // his NVDA call: sold at a bid of $0.35 or less, not $0.26
  assert.equal(optionStopPrice({ entry: 0.66 }), 0.33);                // his AVGO call: not $0.05
  assert.equal(optionStopPrice({ entry: 0.65 }), 0.32);                // never a price that is down less than 50%
  assert.equal(optionStopPrice({ entry: 1.1 }), 0.55);
  assert.equal(optionStopPrice({ entry: 1.1, stopOption: 0.5 }), 0.5); // the server's own number wins
  assert.equal(optionStopPrice({ entry: 0.01, stopOption: 0 }), 0);
  assert.equal(optionStopPrice({}), null);
  assert.equal(optionStopPrice(null), null);
});

test("a closed trade's Stop line says which stop sold it and the real loss", async () => {
  const { closedStopNote, tradeDetail } = await import("./tickerTrader.js");
  assert.equal(closedStopNote({ entry: 0.7, stopOption: 0.35, stopFired: "option", stopLossPct: 51 }), "option stop sold it · option down 51%");
  assert.equal(closedStopNote({ entry: 0.7, stopOption: 0.35, stopFired: "stock", stopLossPct: 63 }), "stock stop sold it · option down 63%");
  assert.equal(closedStopNote({ entry: 1.1, stopOption: 0.55, stopFired: "stock", stopLossPct: -9 }), "stock stop sold it · option up 9%");
  assert.equal(closedStopNote({ entry: 1.1, stopOption: 0.55, stopFired: "stock", stopLossPct: null }), "stock stop sold it");
  assert.equal(closedStopNote({ entry: 1.1, stopOption: 0.55 }), "or the option's bid at $0.55 (down 50%)");     // no stop sold it
  assert.equal(closedStopNote({ entry: 0.7 }), "option down 50%");     // a trade from before the option stop keeps its old words
  const card = tradeDetail({ id: "NVDA-CALL-3", ticker: "NVDA", side: "CALL", strike: 104, expiry: "2026-11-20", qty: 3, entry: 1.1, entrySpot: 100,
    stopStart: 98.4, stopStock: 98.4, stopOption: 0.55, stopFired: "option", stopLossPct: 55, t1: 102, t2: 104, t3: 106, pl: -180, plPct: -54.5,
    boughtAt: "2026-11-13T10:00:20-05:00", closedAt: "2026-11-13T10:07:30-05:00", outcome: "stopped", hits: [], targets: [],
    events: [{ at: "2026-11-13T10:07:30-05:00", text: "Option stop: the option's bid $0.50 is down 55% from the $1.10 paid (the stop is 50%), sold 3 at $0.50." }] });
  assert.deepEqual(card.levels[1], { key: "Stop", label: "Stop", value: "98.40", note: "option stop sold it · option down 55%" });
  assert.equal(card.pct, "\u221254.5%");
  assert.equal(card.outcome, "Stopped before T1");
  assert.match(card.events[0].text, /^Option stop: the option's bid \$0\.50 is down 55% from the \$1\.10 paid/);
});

test("a trade saved before the option stop still says which stop sold it and the real loss (2026-11-25)", async () => {
  const { closedStopNote, stopSale, tradeDetail } = await import("./tickerTrader.js");
  // His real rows: no stopOption, no stopFired, no stopLossPct. Before this they read "option down 50%".
  const at = "2026-11-19T10:31:14-05:00";
  const nvda = { id: "NVDA-CALL-1-1795098725", ticker: "NVDA", side: "CALL", strike: 162.5, expiry: "2026-11-20", qty: 2, entry: 0.7, entrySpot: 155.74,
    stopStart: 153.02, stopStock: 153.02, t1: 158, t2: 160, t3: 162.5, pl: -88, plPct: -62.9, hits: [], targets: [], outcome: "stopped",
    boughtAt: "2026-11-19T09:32:05-05:00", closedAt: at, sells: [{ at, qty: 2, price: 0.26, pl: -88 }],
    events: [{ at, text: "Stop: stock traded through 153.02, sold 2 at $0.26." }] };
  const avgo = { ...nvda, ticker: "AVGO", entry: 0.66, qty: 3, pl: -183, plPct: -92.4, sells: [{ at, qty: 3, price: 0.05, pl: -183 }],
    events: [{ at, text: "Stop: stock traded through 348.80, sold 3 at $0.05." }] };
  assert.equal(closedStopNote(nvda), "stock stop sold it · option down 63%");
  assert.equal(closedStopNote(avgo), "stock stop sold it · option down 92%");
  assert.equal(tradeDetail(nvda).levels[1].note, "stock stop sold it · option down 63%");
  assert.equal(tradeDetail(nvda).pct, "−62.9%");
  // Worked out from whatever was saved: the price in the note, else (nothing sold at a target) the trade's own percent.
  assert.deepEqual(stopSale({ ...nvda, sells: [] }), { fired: "stock", lost: 63 });
  assert.deepEqual(stopSale({ ...nvda, sells: [], events: [{ at, text: "Stop: stock traded through 153.02." }] }), { fired: "stock", lost: 63 });
  // After T1 the trade's own percent has the T1 sale in it, so only the stop's own sale counts.
  const afterT1 = { ...nvda, plPct: 20, hits: [{ target: "T1" }], sells: [{ at: "x", qty: 1, price: 1.2 }, { at, qty: 1, price: 0.6 }],
    events: [{ at: "x", text: "T1 hit: sold 1 at $1.20, stop to break-even." }, { at, text: "Break-even stop: stock traded through 155.74, sold 1 at $0.60." }] };
  assert.equal(closedStopNote(afterT1), "stock stop sold it · option down 14%");
  assert.deepEqual(stopSale({ ...afterT1, sells: [], events: [{ at, text: "Stop at T1: stock traded through 158.00." }] }), { fired: "stock", lost: null });
  assert.equal(closedStopNote({ ...nvda, events: [{ at, text: "Option stop: the option's bid $0.34 is down 51% from the $0.70 paid (the stop is 50%), sold 2 at $0.34." }],
    sells: [{ at, qty: 2, price: 0.34 }] }), "option stop sold it · option down 51%");
  // No stop sold it: nothing is claimed, and the old words stay.
  assert.deepEqual(stopSale({ ...nvda, events: [{ at, text: "15:50 close: sold 2 at the bid $0.26." }] }), { fired: null, lost: null });
  assert.equal(closedStopNote({ ...nvda, events: [{ at, text: "15:50 close: sold 2 at the bid $0.26." }] }), "option down 50%");
  assert.deepEqual(stopSale(null), { fired: null, lost: null });
  // The server's own answer always wins.
  assert.deepEqual(stopSale({ ...nvda, stopFired: "stock", stopLossPct: 64 }), { fired: "stock", lost: 64 });
});

test("a run with no live price is marked 'price is old' on its row, the Today tile and the account (2026-11-25)", async () => {
  const { accountOldNote, oldPriceMark, openPlText } = await import("./tickerTrader.js");
  const dead = { id: "NVDA-CALL", ticker: "NVDA", side: "CALL", status: "open", quoteDead: true, openPl: 110, liveNet: 110, net: 0, open: OPEN, trades: [],
    note: "No live price on the 165 Call for 3 min: the option stop cannot be checked. Still holding 2 of 3." };
  const live = { ...dead, quoteDead: false, note: "In a trade: holding 2 of 3" };
  assert.equal(oldPriceMark(dead), "price is old");
  assert.equal(oldPriceMark(live), "");
  assert.equal(oldPriceMark({ ...dead, open: null }), "");              // nothing held: nothing to mark
  assert.equal(oldPriceMark(null), "");
  assert.equal(openPlText(dead), " · open +$110 (+38%), price is old");
  assert.equal(openPlText(live), " · open +$110 (+38%)");
  assert.equal(runLine(dead), "No live price on the 165 Call for 3 min: the option stop cannot be checked. Still holding 2 of 3. · open +$110 (+38%), price is old");
  assert.equal(runLine(live), "In a trade: holding 2 of 3 · open +$110 (+38%)");
  const asOf = "2026-11-13T10:42:05-05:00";
  assert.equal(tickersDashboard([dead], asOf).today.note, "+$110 in 1 open · price is old");
  assert.equal(tickersDashboard([live], asOf).today.note, "+$110 in 1 open");
  assert.equal(tickersDashboard([dead, { ...live, id: "AMD-CALL", ticker: "AMD" }], asOf).today.note, "+$220 in 2 open · 1 at an old price");
  assert.equal(accountOldNote({ openTrades: 1, oldPriceTrades: 1 }), "1 open trade's price is old");
  assert.equal(accountOldNote({ openTrades: 3, oldPriceTrades: 2 }), "2 open trades' prices are old");
  assert.equal(accountOldNote({ openTrades: 2, oldPriceTrades: 0 }), "");
  assert.equal(accountOldNote({ openTrades: 2 }), "");                  // a server not restarted yet sends no count
  assert.equal(accountOldNote(null), "");
});

test("an open trade with no live price is flagged; one with a live price is not", async () => {
  const { quoteWarning } = await import("./tickerTrader.js");
  assert.equal(quoteWarning({ open: OPEN, quoteDead: true }), "no live price since");
  assert.equal(quoteWarning({ open: OPEN, quoteDead: false }), "");
  assert.equal(quoteWarning({ open: OPEN }), "");                      // a server not restarted yet sends no flag
  assert.equal(quoteWarning({ open: null, quoteDead: true }), "");     // nothing held: nothing to warn about
  assert.equal(quoteWarning(null), "");
});
