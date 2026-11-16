import assert from "node:assert/strict";
import test from "node:test";

import { cleanLink } from "./appLinks.js";
import {
  SIMULATED_NOTE, chartLink, cleanTicker, contractLabel, dayLabel, failureText, ladder, money, openGain, runLine, signedMoney,
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
  assert.deepEqual([rows[4].value, rows[4].note], ["158.40", "moved to break-even"]);
  assert.equal(ladder({ ...OPEN, stopKind: "stop", hits: [] })[4].note, "option down 50%");
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
  assert.equal(runLine({ status: "idle", tradeCount: 0, note: "Not bought: one contract costs $1,200, more than 10% of the $10,000 account." }),
    "Not bought: one contract costs $1,200, more than 10% of the $10,000 account.");
  assert.equal(winsLabel({ tradeCount: 3, wins: 2 }), "2 of 3");
  assert.equal(winsLabel({ tradeCount: 0, wins: 0 }), "–");
  assert.equal(statusLabel({ status: "stopped" }), "Stopped");
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
