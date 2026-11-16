// Bot page -> the chart's Entry / Target / Stop plan (2026-11-12, his "we have in chart
// 'entry, exit and target' use that entry the position and target 1,2,3 ... and stop loss").
// What the trader sees for each trade on the three screens: the Scorecard's All trades and
// journal, the Paper 3 tab, and the Research trades table; the new evaluation and the earlier
// period; and the rules text in his words. Reads are canned; nothing is written, and no spec
// can reach a route that places or cancels an order.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRROR_READ, TEAM, TODAY, mirrorDoc } from "./trading-bot.fixtures.mjs";

const DESK_READ = "GET /api/momx-scanner/bot-desk";
const TRUTH_READ = "GET /api/momx-scanner/bot-truth";
const DESK = "[data-testid='bot-desk']";
const ALL_TRADES = "section[aria-label='All trades']";
const SCORECARD = { "bot.view": "desk" };
const DAY = "2026-11-13";
const at = (clock) => `${DAY}T${clock}-05:00`;

const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());
const check = (key, target, actual, pass, extra = {}) => ({ key, target, actual, pass, ...extra });

// One trade at each stage of the plan, as momx/bot_desk.py serves it.
const PLAN = { entryAsk: 1.25, entryStock: 100, stop: 97.26, stopNow: 97.26, stopKind: "stop", t1: 102, t2: 104, t3: 108, hits: [], ready: true };
const order = (key, symbol, clock, extra) => ({ key, symbol, side: "bull", rule: "level2", levels: ["level1", "level2"], at: at(clock), qty: 3,
  buy: 1.25, scored: true, live: false, ...extra });
const TRADE_LOG = { total: 5, counts: { open: 2, closed: 1, missed: 0, waiting: 0, skipped: 0, signal: 2 }, rows: [
  order("k1", "AAPL", "10:31:00", { status: "open", pnl: 15, roiPct: 4, live: true, note: "holding", plan: PLAN }),
  order("k2", "MSFT", "10:12:00", { status: "open", pnl: 80, roiPct: 21.3, live: true, note: "part sold: T1 hit: sold 1, stop to break-even",
    plan: { ...PLAN, stopNow: 100, stopKind: "break-even", hits: [{ target: "T1", at: at("10:20:00") }] } }),
  order("k3", "NVDA", "10:05:00", { status: "closed", pnl: -195, roiPct: -52, note: "Stop: stock traded through 97.26", plan: PLAN }),
  { key: "k4", status: "signal", symbol: "AMD", side: "bull", rule: "level3", levels: ["level3"], at: at("10:02:00"), scored: false,
    note: "Not ordered: AMD was #9 on the Watchlist by H/L (needs top 7 for 5 min from 9:45) · 0.20-delta option $2.00, now +10%",
    plan: { ...PLAN, entryAsk: 2.1, entryStock: 250, stop: 247.5, stopNow: 250, stopKind: "break-even", t1: 253, t2: 256, t3: 260,
      hits: [{ target: "T1", at: at("10:30:00") }], status: "closed", pct: 12.5, reason: "Stop at break-even: stock traded through 250.00" } },
  // (Was a "Not ordered: before 10:00" signal; the 10:00 rule was removed 2026-11-13.)
  { key: "k5", status: "signal", symbol: "TSLA", side: "bear", rule: "level3", levels: ["level3"], at: at("09:41:00"), scored: false,
    note: "Not ordered: no chart plan (the option chain was not available at the signal); the bot does not buy without a stop and targets · 0.20-delta option $3.10, now −35%", plan: null }] };

const JOURNAL = { periods: { day: [], week: [], month: [] }, tradeCount: 1, trades: [
  { symbol: "NVDA", side: "bull", rule: "level2", levels: ["level1", "level2"], day: DAY, boughtAt: at("10:05:10"), closedAt: at("10:31:00"),
    qty: 3, buy: 1.25, pnl: -195, roiPct: -52, exit: "Stop: stock traded through 97.26", plan: PLAN }] };

const EARLIER = [{ startDay: "2026-11-10", endDay: "2026-11-12",
  strategy: "Level 1 / 2 / 3 after the 5m chart check, bought at the mid from 9:30, sold 2 of 3 at +100% or the high-OI level, option stop at -50%",
  why: "Ended 11-12: both paper accounts lost money in three days, most of it on 9:30-10:00 buys.",
  orders: 96, trades: 61, wins: 17, losses: 44, winRatePct: 27.9, netPnl: -4621, worstTrade: -612, bestTrade: 418, stillOpen: 1, afterSwitch: 2,
  rows: [{ symbol: "DG", side: "bull", closedAt: "2026-11-12T10:02:00-05:00", qty: 3, buy: 2.2, pnl: -612, roiPct: -92.7, exit: "stop loss -50%", plan: null }] }];

const ACCOUNT = {
  control: { paused: false, pausedAt: null, dailyStop: { on: false, limit: 500, hit: false, hitAt: null, todayPnl: -195 } },
  orders: { sent: 3, filled: 3, missed: 0, skipped: 0, holding: 2, stuck: 0, fillRatePct: 100, stuckSymbols: [], stuckRows: [], holdingOvernight: 0 },
  stats: { n: 1, wins: 0, losses: 1, flat: 0, winRatePct: 0, netPnl: -195, roiPct: -52, cost: 375, avgWin: null, avgLoss: -195,
    profitFactor: 0, expectancy: -195, bestTrade: -195, worstTrade: -195, pnlWithoutBest: 0 },
  days: [{ day: DAY, sent: 3, filled: 3, closed: 1, pnl: -195, cum: -195 }],
  daySummary: { tradingDays: 1, greenDays: 0, redDays: 1, bestDay: { day: DAY, pnl: -195 }, worstDay: { day: DAY, pnl: -195 }, maxDrawdown: 195, dailyLimitBreaks: 0 },
  byRule: [],
  objectives: [check("profit", 1000, -195, false), check("dailyLoss", -500, -195, true, { breaks: 0 }), check("drawdown", 1000, 195, true),
    check("days", 20, 1, false), check("trades", 100, 1, false), check("profitFactor", 1.3, 0, false), check("luck", 0, 0, false, { trades: 3 }),
    check("fill", 50, 100, true)],
  verdict: { ready: false, passed: 3, total: 8 }, from: DAY, through: DAY, lastActivity: at("10:31:00"),
  balance: 9805, journal: JOURNAL, tradeLog: TRADE_LOG, earlier: EARLIER,
};

// plan: "chart" = the server says the running paper bots trade the chart plan (momx/bot_desk.py).
const DESK_DOC = { available: true, days: 90, asOf: at("12:30:00"), smallSample: 20, paused: false, startDay: DAY,
  plan: "chart", planSince: at("08:05:00"),
  oldStrategy: "Level 1 / 2 / 3 after the 5m chart check, bought at the mid from 9:30, sold 2 of 3 at +100% or the high-OI level, option stop at -50%",
  strategy: "Level 1 / 2 / 3 (Setup 3+ with RVOL, SQZ or Skittles) after the 5m chart check, bought at the ask from 9:30 on the chart's Entry / Target / Stop plan (stock stop, T1 / T2 / T3), 3+ contracts on the 0.20-delta contract",
  limits: { accountSize: 10000, profitTarget: 1000, dailyLossLimit: 500, maxDrawdown: 1000, minDays: 20, minTrades: 100, minProfitFactor: 1.3, minFillPct: 50, luckTrades: 3 },
  accounts: { paper3: ACCOUNT } };

// The Paper 3 tab: two orders on the chart plan and one from before it, as the paper bot saves them.
function paperRow(n, symbol, extra) {
  return { buyId: `paper3-p${n}`, buyAt: `${TODAY}T10:0${n}:00-04:00`, buyFilledAt: `${TODAY}T10:0${n}:02-04:00`, at: `${TODAY}T10:0${n}:00-04:00`,
    side: "bull", rule: "level2", symbol, contract: `${symbol}261106C00104000`, qty: 3, botEntry: 1.2, buyLimit: 1.25, buyFill: 1.25, status: "open",
    held: 3, pl: null, sells: [], ...extra };
}
const PAPER_ROWS = [
  paperRow(1, "NVDA", { exitPlan: "chart", entryAsk: 1.25, entryBid: 1.15, entryStock: 100, stopStock0: 97.26, stopStock: 100, stopKind: "break-even",
    held: 2, plan: { t1: 102, t2: 104, t3: 108, delta: 0.2, gamma: 0.03 }, hits: [{ target: "T1", at: `${TODAY}T10:20:00-04:00`, stock: 102.1 }],
    sells: [{ qty: 1, price: 1.75, why: "T1 hit: sold 1, stop to break-even", at: `${TODAY}T10:20:05-04:00` }] }),
  paperRow(2, "AAPL", { exitPlan: "chart", entryAsk: 1.25, entryStock: 250, stopStock0: 247.5, stopStock: 247.5, stopKind: "stop",
    plan: { t1: 253, t2: 256, t3: 260 }, hits: [] }),
  paperRow(3, "SPY", { stop: 0.8 }),                                   // an order from before the chart plan
];
const PAPER_RULES = { book: "Rules bot - Level 1 / 2 / 3 at 0.20 delta, newest signal first", picked: { bull: ["level1", "level2", "level3"], bear: ["level1", "level2", "level3"] },
  orders: "buy: limit at the live ask; unfilled after 120 s, re-sent at the ask (up to 2 times); target and close sells: limit at the mid; stop sells: limit at the bid",
  sizing: "up to $8.00: at least 3 contracts",
  plan: "chart",
  entryTimes: "new buys 9:30-11:00 AM and 1:00-3:30 PM ET (midday skipped; a signal from the 10 minutes before 1:00 is bought at 1:00; a Level signal with no chart plan is not bought)",
  exitPlan: "the chart's Entry / Target / Stop plan. Stop: the stock price where the option is down 50% from the fill; T1 = half the expected move: sell 1 of 3 and stop to break-even" };
const PAPER3_DOC = { ...mirrorDoc("paper3"), rules: PAPER_RULES, rows: PAPER_ROWS, history: [{ day: TODAY, rows: PAPER_ROWS }] };

// The Research table: one Level trade followed on the plan, one whose signal fired at midday.
const LEVEL = { rule: "level2", board: "bull", status: "closed", strike: 101, expiry: "2026-10-31", contract: "NVDA261031C00101000",
  eContract: "NVDA261031C00104000", eStrike: 104, eDelta: 0.19, eStatus: "closed", entry: 3, entryBid: 2.9, entryAsk: 3.1, eEntry: 1.2, eEntryBid: 1.15,
  eEntryAsk: 1.25, eExit: 1.6, eExitBid: 1.55, eExitAsk: 1.65, eClose: 1.5, eBest: 2.5, exitPrice: 3.4, exitBid: 3.3, exitAsk: 3.5,
  exitReason: "below EMA21", close: 3.3, best: 4, holdBest: 4, holdDone: true, spot: 100, expectedMove: 4,
  levelSignal: { version: 1, highestLevel: 2, strategyLevel: 2, setupCount: 3, setupTypes: [{ key: "tagRvol", text: "RVOL" }] }, virtualOnly: true };
const PLANNED = { ...LEVEL, symbol: "NVDA", at: `${TODAY}T10:05:00-04:00`, entryAt: `${TODAY}T10:05:02-04:00`, exitAt: `${TODAY}T10:40:00-04:00`,
  plan: { entry: 1.25, entrySpot: 100, stopStock: 97.26, t1: 102, t2: 104, t3: 108, delta: 0.19, gamma: 0.03 },
  planState: { status: "closed", qty: 3, held: 0, stopStock: 102, stopKind: "T1", pct: 56, reason: "Stop at T1: stock traded through 102.00",
    hits: [{ target: "T1", at: `${TODAY}T10:10:00-04:00` }, { target: "T2", at: `${TODAY}T10:15:00-04:00` }], sells: [] } };
const EARLY = { ...LEVEL, symbol: "AAPL", at: `${TODAY}T11:41:00-04:00`, entryAt: `${TODAY}T11:41:02-04:00`, exitAt: `${TODAY}T12:00:00-04:00`,
  planNote: "Not ordered: outside the buy windows (9:30-11:00, 1:00-3:30)" };
const BOT_DOC = { bull: [{ day: TODAY, trades: [PLANNED, EARLY] }], bear: [{ day: TODAY, trades: [] }], keepDays: 180 };

function canned(extra = {}) {
  return {
    [DESK_READ]: { json: DESK_DOC },
    [TRUTH_READ]: { json: { available: false, days: 1, accounts: {} } },
    [BOT_READ]: { json: BOT_DOC },
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: { mirrors: [PAPER3_DOC, mirrorDoc("paper5")] } },
    ...extra,
  };
}

e2eTest("Scorecard: every trade shows Entry (ask), Stop, T1, T2, T3, which targets were hit, and why it ended", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned() }, async (app) => {
    const card = app.page.locator(`${DESK} ${ALL_TRADES}`);
    await card.waitFor({ timeout: 60_000 });
    assert.deepEqual(await texts(card.locator("thead th")), ["Signal", "Ticker", "Side", "Strategy", "Status", "Contracts", "Bought at",
      "Entry (ask)", "Stop (stock)", "T1", "T2", "T3", "Real P/L", "Return", "What happened"]);
    const rows = card.locator("tbody tr");
    assert.equal(await rows.count(), 5);
    // Holding: the plan as it was set at the fill.
    assert.deepEqual(await texts(rows.nth(0).locator("td")), ["11-13 10:31 ET", "AAPL", "Call", "Level 2", "Open", "3", "$1.25",
      "$1.25", "97.26", "102.00", "104.00", "108.00", "+$15 now", "+4%", "holding"]);
    // T1 reached: a check mark with the time, and the stop moved to break-even.
    assert.deepEqual(await texts(rows.nth(1).locator("td")), ["11-13 10:12 ET", "MSFT", "Call", "Level 2", "Open", "3", "$1.25",
      "$1.25", "97.26 → 100.00 (break-even)", "102.00 ✓ 10:20", "104.00", "108.00", "+$80 now", "+21%", "part sold: T1 hit: sold 1, stop to break-even"]);
    // Stopped: the reason names the stock price.
    assert.deepEqual(await texts(rows.nth(2).locator("td")), ["11-13 10:05 ET", "NVDA", "Call", "Level 2", "Closed", "3", "$1.25",
      "$1.25", "97.26", "102.00", "104.00", "108.00", "−$195", "−52%", "Stop: stock traded through 97.26"]);
    // A signal the bot did not order still shows the plan it would have had, and how that went.
    const amd = await texts(rows.nth(3).locator("td"));
    assert.deepEqual(amd.slice(7, 12), ["$2.10", "247.50 → 250.00 (break-even)", "253.00 ✓ 10:30", "256.00", "260.00"]);
    assert.match(amd[14], /^Not ordered: AMD was #9 .* · on the plan \+13% \(Stop at break-even: stock traded through 250\.00\)$/);
    // A signal the bot could work out no plan for is listed, with the reason, and has no plan.
    const tsla = await texts(rows.nth(4).locator("td"));
    assert.deepEqual([tsla[0], tsla[1], tsla[2], tsla[4]], ["11-13 09:41 ET", "TSLA", "Put", "Not ordered"]);
    assert.deepEqual(tsla.slice(7, 12), ["–", "–", "–", "–", "–"]);
    assert.match(tsla[14], /^Not ordered: no chart plan \(the option chain was not available at the signal\); the bot does not buy without a stop and targets · /);
    assert.match(await card.textContent(), /Entry \(ask\), Stop, T1, T2 and T3 are the chart's plan for that trade/);

    // The new evaluation is named, and the earlier period keeps its own totals in its own card.
    assert.match(await app.page.locator(DESK).textContent(), /Evaluation started 11-13 · trades only Level 1 \/ 2 \/ 3 .*bought at the ask from 9:30 on the chart's Entry \/ Target \/ Stop plan/);
    const earlier = app.page.locator(`${DESK} [data-testid='desk-earlier']`);
    assert.equal((await earlier.locator("h4").textContent()).trim(), "Earlier period: 11-10 to 11-12");
    assert.equal((await earlier.locator("[data-testid='desk-earlier-summary']").textContent()).trim(),
      "61 closed trades · 17 wins (28%) · net −$4,621 · worst trade −$612 · best trade +$418 · 1 still open on the old exit plan · includes 2 older-plan orders sent after 11-12, before the bot was restarted");
    assert.match(await earlier.textContent(), /Not part of the evaluation above\. It traded: Level 1 \/ 2 \/ 3 .*bought at the mid from 9:30/);
    await earlier.locator("details summary").click();
    assert.deepEqual(await texts(earlier.locator("tbody tr").first().locator("td")), ["11-12 10:02 ET", "DG", "Call", "3", "$2.20", "−$612", "−93%", "stop loss -50%"]);

    // The journal's closed trades carry the stop and the targets too.
    const journal = app.page.locator(`${DESK} details.desk-journal-trades`).filter({ hasText: "Every closed trade" });
    await journal.locator("summary").click();
    assert.deepEqual(await texts(journal.locator("thead th")), ["Closed", "Ticker", "Side", "Strategy", "Contracts", "Bought at", "Stop (stock)", "T1", "T2", "T3",
      "Real P/L", "Return", "How it ended"]);
    assert.deepEqual(await texts(journal.locator("tbody tr").first().locator("td")), ["11-13 10:31 ET", "NVDA", "Call", "Level 2", "3", "$1.25", "97.26", "102.00",
      "104.00", "108.00", "−$195", "−52%", "Stop: stock traded through 97.26"]);

    // The rules, in his words.
    await app.page.locator(`${DESK} details.desk-how summary`).click();
    const rulesBox = app.page.locator(`${DESK} [data-testid='desk-plan-rules']`);
    assert.equal(await rulesBox.getAttribute("data-plan"), "chart");
    const rules = (await rulesBox.textContent()).replace(/\s+/g, " ");
    assert.ok(!rules.includes("10:00"), "the 10:00 rule is gone");
    assert.ok(!rules.includes("older plan"), "the server says the chart plan runs");
    for (const words of ["How the bot trades since 11-13 (the chart's Entry / Target / Stop plan):", "Buys go in from 9:30 to 11:00 AM and from 1:00 to 3:30 PM ET",
      "limit order at the option's ask", "stock trades through it, everything is sold",
      "T1 is half the expected move", "sell 1 at T1 and move the stop to break-even", "sell 1 at T2 and move the stop to T1", "sell the last at T3"]) {
      assert.ok(rules.includes(words), words);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

// The page is live in the browser before the servers restart. A payload from a server that does
// not say the chart plan runs (no "plan" field = the old api_server; "old" = the paper bots have
// not restarted) must show the OLD plan's text and say the new evaluation has not started.
for (const [label, patch, viewport] of [["an old server's payload (no plan field)", { plan: undefined, planSince: undefined }, undefined],
  ["plan: old, at phone width", { plan: "old", planSince: null }, PHONE]]) {
  e2eTest(`Scorecard before the restart: ${label} shows the older plan's text, not the chart plan's`, async () => {
    const doc = { ...DESK_DOC, ...patch };
    await withApp({ view: "Bot", viewport, storage: SCORECARD, fakeReads: canned({ [DESK_READ]: { json: doc } }) }, async (app) => {
      const line = app.page.locator(`${DESK} [data-testid='desk-evaluation']`);
      await line.waitFor({ timeout: 60_000 });
      const said = (await line.textContent()).trim();
      assert.match(said, /^The chart-plan evaluation has not started: the paper bots still trade the older plan \(Level 1 \/ 2 \/ 3 .*bought at the mid from 9:30.*\) until the servers are restarted\. (?:Orders sent until then are counted in the earlier period below|The numbers on this page are the older plan's) · \$10,000 account · balance now \$9,805$/);
      // An old server (no plan field) has not split the orders, so the page must not point at an "earlier period below".
      assert.equal(/earlier period below/.test(said), patch.plan === "old");
      assert.ok(!said.includes("Evaluation started"));
      await app.page.locator(`${DESK} details.desk-how summary`).click();
      const rulesBox = app.page.locator(`${DESK} [data-testid='desk-plan-rules']`);
      assert.equal(await rulesBox.getAttribute("data-plan"), "old");
      const rules = (await rulesBox.textContent()).replace(/\s+/g, " ");
      for (const words of ["How the bot trades right now (the older plan is still running):", "limit order at the middle of the bid and ask",
        "At +100% on the option", "2 of 3 contracts are sold", "built but not running yet. It starts when the servers are restarted."]) {
        assert.ok(rules.includes(words), words);
      }
      assert.ok(!rules.includes("T1 is half the expected move") && !rules.includes("since 11-13"));
      assert.equal(await overflowsSideways(app.page), false);
      const box = await line.boundingBox();
      if (viewport) assert.ok(box && box.x >= 0 && box.x + box.width <= viewport.width + 1, "cut off on a phone");
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("phone width: the plan columns scroll inside the All trades card and nothing is cut off", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned() }, async (app) => {
    const card = app.page.locator(`${DESK} ${ALL_TRADES}`);
    await card.waitFor({ timeout: 60_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [card, card.locator(".desk-trades-scroll"), app.page.locator(`${DESK} [data-testid='desk-earlier']`)]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "cut off on a phone");
    }
    // The table is wider than the phone and scrolls inside its own box: the T3 column can be reached.
    const scroll = card.locator(".desk-trades-scroll");
    const wide = await scroll.evaluate((el) => el.scrollWidth > el.clientWidth);
    assert.equal(wide, true);
    // (T3 is not the last column - Real P/L, Return and What happened follow - so swipe the box
    // sideways until the T3 header is in view, as a thumb would.)
    const t3Head = card.locator("thead th", { hasText: /^T3$/ });
    await scroll.evaluate((el) => {
      const th = [...el.querySelectorAll("thead th")].find((h) => h.textContent.trim() === "T3");
      const box = el.getBoundingClientRect();
      const cell = th.getBoundingClientRect();
      el.scrollLeft += cell.right - box.right;
    });
    const t3 = await t3Head.boundingBox();
    const frame = await scroll.boundingBox();
    assert.ok(t3 && frame && t3.x >= frame.x - 1 && t3.x + t3.width <= frame.x + frame.width + 1 && t3.x + t3.width <= PHONE.width + 1, "T3 cannot be reached");
    // ... and on to the last column, the reason the trade ended.
    await scroll.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    const last = await card.locator("thead th").last().boundingBox();
    assert.ok(last && last.x + last.width <= PHONE.width + 1, "the last column cannot be reached");
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

const PAPER_SELECT = "select.bot-paper-select[aria-label='Alpaca paper account']";

async function paperCells(page, header) {
  return page.evaluate((title) => {
    const table = [...document.querySelectorAll(".bot-scroll table.bot-table")].find((t) => [...t.querySelectorAll("thead th")].some((th) => th.textContent === "Buy fill"));
    const index = [...table.querySelectorAll("thead th")].findIndex((th) => th.textContent === title);
    return [...table.querySelectorAll("tbody tr")].map((tr) => tr.children[index]?.textContent.trim());
  }, header);
}

e2eTest("Paper 3: each order shows its stock stop and T1 / T2 / T3, and the account rules describe the plan", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
    await app.page.locator(PAPER_SELECT).waitFor({ timeout: 30_000 });
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
    assert.deepEqual(await paperCells(app.page, "Ticker"), ["SPY", "AAPL", "NVDA"]);
    // The order from before the chart plan keeps its option-price stop and has no targets.
    assert.deepEqual(await paperCells(app.page, "Stop"), ["$0.80", "247.50", "97.26 → 100.00 (break-even)"]);
    assert.deepEqual(await paperCells(app.page, "T1"), ["–", "253.00", "102.00 ✓ 10:20"]);
    assert.deepEqual(await paperCells(app.page, "T2"), ["–", "256.00", "104.00"]);
    assert.deepEqual(await paperCells(app.page, "T3"), ["–", "260.00", "108.00"]);
    assert.deepEqual(await paperCells(app.page, "Buy fill"), ["$1.25", "$1.25", "$1.25"]);
    await app.page.locator("details summary", { hasText: "Account rules and entry policy" }).click();
    const rules = (await app.page.locator("details p.bot-jevbook-note", { hasText: "Alpaca PAPER · paper3" }).textContent()).replace(/\s+/g, " ");
    // Was "new buys 10:00-11:00 AM ... (no buys 9:30-10:00" (the 10:00 rule, removed 2026-11-13).
    assert.match(rules, /When: new buys 9:30-11:00 AM and 1:00-3:30 PM ET \(midday skipped/);
    assert.ok(!rules.includes("10:00"));
    assert.match(rules, /Exit: the chart's Entry \/ Target \/ Stop plan\./);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Research trades: a Level trade shows its chart plan in one line, and a midday signal says Not ordered: outside the buy windows", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "trades" } }, async (app) => {
    await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
    await app.page.locator(".bot-card .bot-card-head h3", { hasText: /^Trades/ }).waitFor({ timeout: 30_000 });
    const plans = app.page.locator(".bot-card [data-testid='bot-trade-plan']");
    await plans.first().waitFor({ timeout: 30_000 });
    const byTicker = await app.page.evaluate(() => Object.fromEntries([...document.querySelectorAll(".bot-card table.bot-table tbody tr")].map((tr) => [
      tr.querySelector("td:nth-child(3) b")?.textContent, tr.querySelector("[data-testid='bot-trade-plan']")?.textContent.trim()])));
    assert.equal(byTicker.NVDA,
      "Entry $1.25 · Stop 97.26 → 102.00 (T1) · T1 102.00 ✓ 10:10 · T2 104.00 ✓ 10:15 · T3 108.00 · on the plan +56% (Stop at T1: stock traded through 102.00)");
    assert.equal(byTicker.AAPL, "Not ordered: outside the buy windows (9:30-11:00, 1:00-3:30)");
    assert.ok((await texts(app.page.locator(".bot-card table.bot-table thead th"))).some((h) => h.startsWith("Chart plan")));
    assert.match(await app.page.locator("p.bot-foot").textContent(), /Chart plan \(Level trades recorded while the chart plan is running; a dash = recorded before that\) = three 0\.20-delta contracts bought at the ask/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the Paper 3 orders with the plan columns do not push the page sideways", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned() }, async (app) => {
    await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
    await app.page.locator(PAPER_SELECT).waitFor({ timeout: 30_000 });
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
    assert.deepEqual(await paperCells(app.page, "T1"), ["–", "253.00", "102.00 ✓ 10:20"]);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- 2026-11-13 review fixes: an order bought minutes after its signal shows the plan from the
// stock price at the ORDER (entry, stop and T1-T3 from one price), and a sale the paper broker
// keeps refusing says so instead of a silent "holding".
const REFUSED = "The broker refused this sale (5 times in a row; trying again every 30 minutes)";
const REBASED_PLAN = { entryAsk: 1.25, entryStock: 101, stop: 98.31, stopNow: 101, stopKind: "break-even", t1: 103, t2: 105, t3: 108,
  hits: [{ target: "T1", at: at("13:05:05") }], ready: true };
const LATE_LOG = { total: 2, counts: { open: 2, closed: 0, missed: 0, waiting: 0, skipped: 0, signal: 0 }, rows: [
  order("r1", "AMD", "12:56:22", { status: "open", pnl: 50, roiPct: 13.3, live: true, boughtAt: at("13:00:05"),
    note: "part sold: T1 hit: sold 1, stop to break-even", plan: REBASED_PLAN }),
  order("r2", "NVDA", "10:05:00", { status: "open", pnl: -195, roiPct: -52, live: true, note: REFUSED, plan: PLAN })] };
const LATE_DESK = { ...DESK_DOC, accounts: { paper3: { ...ACCOUNT, tradeLog: LATE_LOG } } };
const LATE_PAPER_ROWS = [
  paperRow(1, "NVDA", { exitPlan: "chart", entryAsk: 1.25, entryStock: 100, stopStock0: 97.26, stopStock: 97.26, stopKind: "stop", status: "exit_due",
    note: REFUSED, sellRejects: 5, sellWhy: "Stop: stock traded through 97.26", plan: { t1: 102, t2: 104, t3: 108 }, hits: [] }),
  paperRow(2, "AMD", { exitPlan: "chart", entryAsk: 1.25, entryStock: 101, stopStock0: 98.31, stopStock: 101, stopKind: "break-even", held: 2,
    plan: { t1: 103, t2: 105, t3: 108, entrySpot: 101, signalSpot: 100, signalTargets: [102, 104, 108] },
    hits: [{ target: "T1", at: `${TODAY}T13:05:05-04:00`, stock: 103.1, level: 103 }],
    sells: [{ qty: 1, price: 1.75, why: "T1 hit: sold 1, stop to break-even", at: `${TODAY}T13:05:05-04:00` }] }),
];
const LATE_PAPER3 = { ...mirrorDoc("paper3"), rules: PAPER_RULES, rows: LATE_PAPER_ROWS, history: [{ day: TODAY, rows: LATE_PAPER_ROWS }] };
const lateReads = () => canned({ [DESK_READ]: { json: LATE_DESK }, [MIRROR_READ]: { json: { mirrors: [LATE_PAPER3, mirrorDoc("paper5")] } } });

async function refusedCell(page) {
  return page.evaluate(() => {
    const cell = document.querySelector(".bot-scroll table.bot-table tbody td[title^='The broker refused this sale']");
    return cell ? { text: cell.textContent.trim(), title: cell.getAttribute("title"), ticker: cell.parentElement.textContent.includes("NVDA") } : null;
  });
}

for (const [name, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`${name}: an order bought after its signal shows the plan from the order's stock price, and a refused sale says so`, async () => {
    await withApp({ view: "Bot", storage: SCORECARD, fakeReads: lateReads(), ...(viewport ? { viewport } : {}) }, async (app) => {
      const card = app.page.locator(`${DESK} ${ALL_TRADES}`);
      await card.waitFor({ timeout: 60_000 });
      const rows = card.locator("tbody tr");
      assert.equal(await rows.count(), 2);
      // Signal 12:56, bought at 13:00 with the stock at 101: stop, T1, T2 and T3 start from 101.
      const amd = await texts(rows.nth(0).locator("td"));
      assert.deepEqual([amd[0], amd[1], amd[4]], ["11-13 12:56 ET", "AMD", "Open"]);
      assert.deepEqual(amd.slice(7, 12), ["$1.25", "98.31 → 101.00 (break-even)", "103.00 ✓ 13:05", "105.00", "108.00"]);
      assert.equal(amd[14], "part sold: T1 hit: sold 1, stop to break-even");
      // The paper broker keeps refusing the stop's sale: the row says it in plain words.
      const nvda = await texts(rows.nth(1).locator("td"));
      assert.deepEqual([nvda[1], nvda[4], nvda[14]], ["NVDA", "Open", REFUSED]);
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
    // The Paper 3 tab: the same order with its re-based targets, and the refusal on the stuck row.
    await withApp({ view: "Bot", fakeReads: lateReads(), ...(viewport ? { viewport } : {}) }, async (app) => {
      await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
      await app.page.locator(PAPER_SELECT).waitFor({ timeout: 30_000 });
      await app.page.locator(PAPER_SELECT).selectOption("paper3");
      await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
      assert.deepEqual(await paperCells(app.page, "Ticker"), ["AMD", "NVDA"]);
      assert.deepEqual(await paperCells(app.page, "Stop"), ["98.31 → 101.00 (break-even)", "97.26"]);
      assert.deepEqual(await paperCells(app.page, "T1"), ["103.00 ✓ 13:05", "102.00"]);
      assert.deepEqual(await paperCells(app.page, "T2"), ["105.00", "104.00"]);
      assert.deepEqual(await paperCells(app.page, "T3"), ["108.00", "108.00"]);
      assert.deepEqual(await refusedCell(app.page), { text: "exit_due", title: REFUSED, ticker: true });
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
