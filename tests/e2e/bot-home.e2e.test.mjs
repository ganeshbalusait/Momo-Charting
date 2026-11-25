// Bot page -> the new home page (2026-11-25, frontend/src/BotHome.jsx): is the bot buying, what it
// made on real paper fills, then Journal / Trades / Learned / Ready, and four "More" rows that lead to
// the older pages. Every read is canned (one Scorecard read, GET /api/momx-scanner/bot-desk); the page
// sends nothing, and each spec asserts there were no writes.
//
// How the spec reaches the page (the mount must match):
//   * left-rail view "Bot" (agenticActiveView), with localStorage "bot.view" = "home"
//   * the page is [data-testid='bh-root'] inside [data-testid='bot-view']
//   * a "More" row replaces it with the older page plus the six Bot tabs ([data-testid='bh-oldnav']) that return

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const DESK_READ = "GET /api/momx-scanner/bot-desk";
const TRUTH_READ = "GET /api/momx-scanner/bot-truth";
const TICKER_READ = "GET /api/momx-scanner/ticker-trader";
const HOME = { "bot.view": "home" };                 // the harness seeds Research for the older Bot specs
const SHOTS = process.env.BOT_HOME_SHOTS || "";      // a folder: save a picture of each screen there
const ROOT = "[data-testid='bot-view'] [data-testid='bh-root']";
const tid = (name) => `[data-testid='${name}']`;

const LIMITS = { accountSize: 10000, profitTarget: 1000, dailyLossLimit: 500, maxDrawdown: 1000, minDays: 20, minTrades: 100,
  minProfitFactor: 1.3, minFillPct: 50, luckTrades: 3 };
const PLAN = { entryAsk: 0.87, entryStock: 21.38, stop: 26.13, stopNow: 26.13, stopKind: "stop", t1: 19.44, t2: 17.51, t3: 17.5, hits: [], ready: true };
const closed = (symbol, side, day, boughtAt, closedAt, pnl, roiPct, exit, extra = {}) => ({
  symbol, side, rule: "level2", levels: ["level1", "level2"], day, boughtAt, closedAt, qty: 3, buy: 0.87, pnl, roiPct, exit, aiPicked: false, plan: PLAN, ...extra });
const period = (key, trades, pnl, calls, puts, winRatePct = 0) => ({ key, trades, wins: 0, losses: trades, winRatePct, pnl, roiPct: -10, calls, puts, best: null });
const check = (key, target, actual, pass, extra = {}) => ({ key, target, actual, pass, ...extra });
const BUYING = { paused: false, pausedAt: null, dailyStop: { on: false, limit: 500, hit: false, hitAt: null, todayPnl: 0 } };

// Paper 3, cut from the real answer of 2026-11-25: 67 closed trades, −$5,124, and $9.85 left at Alpaca.
const PAPER3 = {
  mode: { key: "rules", name: "Rules bot" },
  orders: { sent: 70, filled: 68, missed: 2, skipped: 0, holding: 1, stuck: 0, fillRatePct: 97.1, stuckSymbols: [], stuckRows: [], holdingOvernight: 0 },
  stats: { n: 67, wins: 14, losses: 50, flat: 3, winRatePct: 20.9, netPnl: -5124, roiPct: -20, cost: 25616, avgWin: 315.21, avgLoss: -190.74,
    profitFactor: 0.46, expectancy: -76.48, bestTrade: 1300, worstTrade: -1005, pnlWithoutBest: -8164 },
  days: [{ day: "2026-11-13", sent: 21, filled: 20, closed: 18, pnl: -2116, cum: -2116 }, { day: "2026-11-19", sent: 5, filled: 5, closed: 9, pnl: 696, cum: -4545 },
    { day: "2026-11-23", sent: 4, filled: 4, closed: 3, pnl: -579, cum: -5124 }],
  daySummary: { tradingDays: 7, greenDays: 2, redDays: 5, bestDay: { day: "2026-11-19", pnl: 696 }, worstDay: { day: "2026-11-13", pnl: -2116 }, maxDrawdown: 5124, dailyLimitBreaks: 5 },
  byRule: [], strategies: { rows: [], best: null, need: 20 },
  journal: {
    periods: {
      day: [period("2026-11-23", 3, -579, -459, -120), period("2026-11-19", 2, 696, 696, 0, 50), period("2026-10-30", 1, -50, -50, 0)],
      week: [period("2026-11-23", 3, -579, -459, -120), period("2026-11-16", 2, 696, 696, 0, 50), period("2026-10-26", 1, -50, -50, 0)],
      month: [period("2026-11", 67, -5124, -4740, -384, 20.9), period("2026-10", 1, -50, -50, 0)],
    },
    trades: [
      closed("KSS", "bear", "2026-11-23", "2026-11-23T09:49:05-05:00", "2026-11-23T14:22:00-05:00", -120, -46, "5-minute candle closed above the 21 EMA"),
      closed("DECK", "bull", "2026-11-23", "2026-11-23T10:07:26-05:00", "2026-11-23T10:15:23-05:00", -240, -80, "Stop: option down 50% (backstop)", { rule: "level1", levels: ["level1"], qty: 4, buy: 0.75 }),
      closed("MCD", "bull", "2026-11-23", "2026-11-20T13:53:32-05:00", "2026-11-23T09:54:34-05:00", -219, -52, "Stop: option down 50% (backstop)"),
      closed("NVDA", "bull", "2026-11-19", "2026-11-19T09:40:00-05:00", "2026-11-19T15:50:00-05:00", 746, 112, "15:50 close", { plan: { ...PLAN, hits: [{ target: "T1", at: "2026-11-19T10:00:16-05:00" }] } }),
      closed("AMD", "bull", "2026-11-19", "2026-11-19T09:41:00-05:00", "2026-11-19T11:00:00-05:00", -50, -19, "Stop: stock traded through 54.10"),
      closed("OLD", "bull", "2026-10-30", "2026-10-30T09:41:00-04:00", "2026-10-30T11:00:00-04:00", -50, -19, "15:50 close"),
    ],
    tradeCount: 67,
  },
  tradeLog: { counts: { open: 1, closed: 67, missed: 1, waiting: 0, skipped: 0, signal: 0 }, total: 69, rows: [
    { key: "bear|level2|COP|2026-11-23T15:20:45-05:00", status: "open", symbol: "COP", side: "bear", rule: "level2", levels: ["level1", "level2"], at: "2026-11-23T15:20:45-05:00",
      boughtAt: "2026-11-23T15:24:20-05:00", closedAt: null, qty: 3, buy: 0.76, limit: 0.76, pnl: -51, roiPct: -22.4, live: true, markAt: "2026-11-24T16:01:00-05:00", note: "holding", scored: true, plan: PLAN },
    { key: "bear|level2|M|2026-11-23T09:47:04-05:00", status: "missed", symbol: "M", side: "bear", rule: "level2", levels: ["level1", "level2"], at: "2026-11-23T09:47:04-05:00",
      boughtAt: null, closedAt: null, qty: 15, buy: null, limit: 0.19, pnl: null, roiPct: null, live: false, markAt: null,
      note: "The broker refused this buy: not enough buying power in the paper account", scored: true, plan: { ...PLAN, stop: null } },
  ] },
  objectives: [
    check("profit", 1000, -5124, false), check("dailyLoss", -500, -2116, false, { breaks: 5 }), check("drawdown", 1000, 5124, false),
    check("days", 20, 7, false), check("trades", 100, 67, false), check("profitFactor", 1.3, 0.46, false),
    check("luck", 0, -8164, false, { trades: 3 }), check("fill", 50, 97.1, true),
  ],
  verdict: { ready: false, passed: 1, total: 8 },
  from: "2026-11-13", through: "2026-11-23", lastActivity: "2026-11-23T15:24:20-05:00", earlier: [], balance: 4876,
  broker: { at: "2026-11-25T01:19:29-05:00", cash: 9.85, optionsBuyingPower: 9.85, buyingPower: 9.85, equity: 27.85, lastEquity: 159.85, error: null },
  control: BUYING,
};

// Paper 5: money to spend, one order that never filled, no closed trade.
const PAPER5 = {
  mode: { key: "ai", name: "AI bot" },
  orders: { sent: 1, filled: 0, missed: 1, skipped: 0, holding: 0, stuck: 0, fillRatePct: 0, stuckSymbols: [], stuckRows: [], holdingOvernight: 0 },
  stats: { n: 0, wins: 0, losses: 0, flat: 0, winRatePct: null, netPnl: 0, roiPct: null, cost: 0, avgWin: null, avgLoss: null, profitFactor: null,
    expectancy: null, bestTrade: null, worstTrade: null, pnlWithoutBest: null },
  days: [], daySummary: { tradingDays: 0, greenDays: 0, redDays: 0, bestDay: null, worstDay: null, maxDrawdown: 0, dailyLimitBreaks: 0 },
  byRule: [], strategies: { rows: [], best: null, need: 20 },
  journal: { periods: { day: [], week: [], month: [] }, trades: [], tradeCount: 0 },
  tradeLog: { counts: { open: 0, closed: 0, missed: 1, waiting: 0, skipped: 0, signal: 0 }, total: 1, rows: [
    { key: "bull|level2|AAPL|x", status: "missed", symbol: "AAPL", side: "bull", rule: "level2", levels: ["level1", "level2"], at: "2026-11-24T09:47:04-05:00",
      qty: 3, buy: null, limit: 1.1, pnl: null, roiPct: null, live: false, note: "the order did not fill", plan: null }] },
  objectives: [
    check("profit", 1000, null, false), check("dailyLoss", -500, null, false, { breaks: 0 }), check("drawdown", 1000, null, false),
    check("days", 20, 0, false), check("trades", 100, 0, false), check("profitFactor", 1.3, null, false),
    check("luck", 0, null, false, { trades: 3 }), check("fill", 50, 0, false),
  ],
  verdict: { ready: false, passed: 0, total: 8 },
  from: "2026-11-24", through: "2026-11-24", lastActivity: null, earlier: [], balance: 10000,
  broker: { at: "2026-11-24T16:00:11-05:00", cash: 9000, optionsBuyingPower: 9000, buyingPower: 9000, equity: 9000, lastEquity: 9000, error: null },
  control: BUYING, aiPicks: { picked: {}, fallback: {} },
};

const LESSONS = { updatedAt: "2026-11-24T17:02:27-05:00", startDay: "2026-11-13", minLosses: 10, note: "Suggestions only. Nothing here blocks, changes or places a trade.", lessons: [
  { side: "bull", rule: "level1", losses: 31, wins: 4, netPnl: -4740, status: "lesson", source: "openai", lesson: "Most losing trades were filled 10%+ above the signal price.",
    proposedChange: "Skip the trade when the order fills 10%+ above the signal price.", howToTest: "Track 20 paper trades." },
  { side: "bull", rule: "level2", losses: 25, wins: 3, netPnl: -3581, status: "numbers", source: "numbers only", lesson: "18 of the 25 losing trades were in the group 'first 30 minutes'.", proposedChange: "", howToTest: "" },
  { side: "bear", rule: "level3", losses: 6, wins: 6, netPnl: -756, status: "collecting", source: "none", lesson: "Not enough losing trades yet (6 of 10).", proposedChange: "", howToTest: "" },
] };

const DESK_DOC = { available: true, days: 90, asOf: "2026-11-25T01:19:47-05:00", limits: LIMITS, smallSample: 20, startDay: "2026-11-13", plan: "chart",
  planSince: "2026-11-13T07:33:26-05:00", strategy: "Level 1 / 2 / 3", oldStrategy: "", versus: null, lessons: LESSONS, paused: false,
  accounts: { paper3: PAPER3, paper5: PAPER5 } };

const TICKER_DOC = { runs: [], account: { startBalance: 10000, cash: 10000, invested: 0, openValue: 0, value: 10000, net: 0, realized: 0, openTrades: 0 },
  maxRuns: 10, startBalance: 10000, marketOpen: false, asOf: "2026-11-25T01:19:47-05:00", simulated: true };

/** Every read the Bot view can make, answered here: the home page's one read, and the older pages behind "More". */
function canned(desk = { json: DESK_DOC }) {
  return {
    [DESK_READ]: desk,
    [TRUTH_READ]: { json: { available: true, days: 13, accounts: {} } },
    [TICKER_READ]: { json: TICKER_DOC },
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
  };
}

const text = async (locator) => (await locator.innerText()).replace(/\s+/g, " ").trim();
const texts = async (locator) => (await locator.allInnerTexts()).map((s) => s.replace(/\s+/g, " ").trim());

async function shot(app, name) {
  if (SHOTS) await app.page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function openHome(app) {
  const root = app.page.locator(ROOT);
  await root.locator(tid("bh-state")).waitFor({ timeout: 60_000 });
  return root;
}

async function go(root, name) {
  await root.locator(tid(`bh-tab-${name}`)).click();
  await root.locator(tid(`bh-section-${name}`)).waitFor({ timeout: 15_000 });
  assert.equal(await root.locator(tid(`bh-tab-${name}`)).getAttribute("aria-pressed"), "true");
}

/** Every button on the page is big enough for a thumb, and no text is under 12 px. */
async function touchAudit(root) {
  return root.evaluate((el) => {
    const out = [];
    for (const node of el.querySelectorAll("*")) {
      const box = node.getBoundingClientRect();
      if (!box.width || !box.height) continue;
      if (node.tagName === "BUTTON" && (box.height < 44 || box.width < 44)) out.push(`small button: ${node.textContent.trim().slice(0, 24)} ${Math.round(box.width)}x${Math.round(box.height)}`);
      const own = [...node.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (own && parseFloat(getComputedStyle(node).fontSize) < 12) out.push(`small text: ${node.textContent.trim().slice(0, 24)}`);
      if (box.right > window.innerWidth + 1 || box.left < -1) out.push(`cut off: ${node.textContent.trim().slice(0, 24)}`);
    }
    return out;
  });
}

async function wholeFlow(app, { phone }) {
  const root = await openHome(app);
  const tag = phone ? "phone" : "desktop";

  // ---- Home: the state comes first, in words, before any number.
  const state = root.locator(tid("bh-state"));
  assert.equal(await state.getAttribute("data-state"), "cannot");
  assert.equal(await text(state.locator("b")), "Cannot buy: the Alpaca paper account has $10 left");
  assert.match(await text(state), /Reset or top up the paper account at Alpaca/);
  assert.match(await text(state), /Last order Mon, Nov 23/);
  const result = root.locator(tid("bh-result"));
  assert.ok((await state.boundingBox()).y < (await result.boundingBox()).y, "the state line sits above the result");
  assert.equal(await root.locator(`${tid("bh-section-home")} > *`).first().getAttribute("data-testid"), "bh-state");
  // One money story: the real result, never the notional "$4,876 balance".
  assert.equal(await text(result.locator(".bh-big")), "−$5,124");
  assert.match(await text(result), /from a \$10,000 start · 67 closed trades · since Nov 13/);
  assert.equal(await result.locator("svg.bh-spark polyline").count(), 1);
  assert.ok(!(await text(root)).includes("4,876"));
  assert.equal(await text(root.locator(tid("bh-fresh"))), "Read Nov 25, 1:19 AM ET");
  assert.deepEqual(await texts(root.locator(".bh-tiles .bh-tile b")), ["14 of 67", "−$76", "1"]);
  assert.equal(await text(root.locator(`${tid("bh-verdict")} b`)), "Not ready for real money");
  assert.deepEqual(await texts(root.locator("[role='group'][aria-label='Which bot'] button")), ["Paper 3 · Rules bot", "Paper 5 · AI bot"]);
  assert.deepEqual(await texts(root.locator("[role='group'][aria-label='Bot sections'] button")), ["Home", "Journal", "Trades", "Learned", "Ready", "More"]);
  assert.equal(await root.locator(`${tid("bh-section-home")} ${tid("bh-trade-row")}`).count(), 4);
  assert.deepEqual(await touchAudit(root), []);
  assert.equal(await overflowsSideways(app.page), false);
  await shot(app, `${tag}-home`);

  // ---- Journal: six tiles, the month calendar, tap a day for its trades.
  await go(root, "journal");
  assert.deepEqual(await texts(root.locator(".bh-tiles.is-six .bh-tile-label")),
    ["Result", "Won", "Average win", "Average loss", "Made per $1 lost", "Biggest drop"]);
  assert.equal(await text(root.locator(tid("bh-cal-month"))), "November 2026");
  const days = root.locator(tid("bh-cal-day"));
  assert.deepEqual(await days.evaluateAll((els) => els.map((el) => el.dataset.day)), ["2026-11-19", "2026-11-23"]);
  assert.equal(await text(root.locator(tid("bh-day-head"))), "Mon, Nov 23 · −$579 · 3 trades");          // the newest day is open
  assert.equal(await root.locator(`${tid("bh-section-journal")} ${tid("bh-trade-row")}`).count(), 3);
  await days.first().click();
  assert.equal(await days.first().getAttribute("aria-pressed"), "true");
  assert.equal(await text(root.locator(tid("bh-day-head"))), "Thu, Nov 19 · +$696 · 2 trades");
  assert.deepEqual(await texts(root.locator(`${tid("bh-section-journal")} ${tid("bh-trade-row")} .bh-row-l .bh-row-t`)), ["NVDA · Call", "AMD · Call"]);
  // A row opens its detail in place.
  const nvda = root.locator(tid("bh-trade-row")).first();
  await nvda.click();
  assert.equal(await nvda.getAttribute("aria-expanded"), "true");
  const detail = root.locator(tid("bh-trade-detail"));
  assert.equal(await detail.count(), 1);
  assert.match(await text(detail), /Signal Level 2 Bought 3 at \$0\.87 · 9:40 AM Sold 3:50 PM Stock when bought 21\.38 Stop \(stock price\) 26\.13 Targets 19\.44 · 17\.51 · 17\.50 Targets reached 1 of 3/);
  assert.match(await text(detail), /15:50 close$/);
  // Previous month, among the months that have trades; then there is nowhere further back.
  await root.getByRole("button", { name: "Previous month" }).click();
  assert.equal(await text(root.locator(tid("bh-cal-month"))), "October 2026");
  assert.equal(await text(root.locator(tid("bh-day-head"))), "Fri, Oct 30 · −$50 · 1 trade");
  assert.equal(await root.getByRole("button", { name: "Previous month" }).isDisabled(), true);
  await root.getByRole("button", { name: "Next month" }).click();
  assert.equal(await text(root.locator(tid("bh-cal-month"))), "November 2026");
  // Day / Week / Month.
  await root.locator("[role='group'][aria-label='Period'] button", { hasText: "Month" }).click();
  assert.match(await text(root.locator(tid("bh-periods"))), /^Month Trades Won Result November 2026 67 21% −\$5,124 October 2026 1 0% −\$50$/);
  assert.equal(await text(root.locator(tid("bh-calls-puts"))), "Calls against puts Calls −$4,790 Puts −$384");
  assert.deepEqual(await touchAudit(root), []);
  assert.equal(await overflowsSideways(app.page), false);
  await shot(app, `${tag}-journal`);

  // ---- Trades: counted filters, rows grouped by day, the detail and the plain stop note.
  await go(root, "trades");
  assert.deepEqual(await texts(root.locator("[role='group'][aria-label='Filter trades'] button")), ["All 68", "Open 1", "Won 1", "Lost 5", "Not bought 1"]);
  assert.deepEqual(await root.locator(tid("bh-day-group")).evaluateAll((els) => els.map((el) => el.dataset.day)), ["2026-11-23", "2026-11-19", "2026-10-30"]);
  assert.equal(await root.locator("table").count(), 0);                                                   // rows, never a wide table
  await root.locator(tid("bh-filter-lost")).click();
  assert.equal(await root.locator(tid("bh-filter-lost")).getAttribute("aria-pressed"), "true");
  assert.equal(await root.locator(tid("bh-trade-row")).count(), 5);
  const mcd = root.locator(tid("bh-trade-row"), { hasText: "MCD" });
  assert.match(await text(mcd), /bought Fri 1:53 PM · sold at the −50% stop/);
  const deck = root.locator(tid("bh-trade-row"), { hasText: "DECK" });
  await deck.click();
  assert.equal(await deck.getAttribute("aria-expanded"), "true");
  assert.equal(await text(root.locator(tid("bh-stop-note"))), "The stop was set at −50% but the sale filled at −80%.");
  assert.match(await text(root.locator(tid("bh-trade-detail"))), /Signal Level 1 Bought 4 at \$0\.75 · 10:07 AM/);
  await deck.click();                                                                                     // and closes again
  assert.equal(await root.locator(tid("bh-trade-detail")).count(), 0);
  await root.locator(tid("bh-filter-open")).click();
  assert.match(await text(root.locator(tid("bh-trade-row"))), /^COP · Put Nov 23, 3:24 PM · holding −\$51 −22% so far$/);
  await root.locator(tid("bh-filter-missed")).click();
  const missed = root.locator(tid("bh-trade-row"));
  assert.match(await text(missed), /^M · Put Nov 23, 9:47 AM · not bought Not bought$/);
  await missed.click();
  assert.match(await text(root.locator(tid("bh-trade-detail"))), /The broker refused this buy: not enough buying power in the paper account$/);
  assert.deepEqual(await touchAudit(root), []);
  assert.equal(await overflowsSideways(app.page), false);
  await shot(app, `${tag}-trades`);

  // ---- Learned: findings with evidence, the suggestion, and the true status.
  await go(root, "learned");
  assert.match(await text(root.locator(tid("bh-learned-head"))), /The review last ran Tue, Nov 24, 5:02 PM ET\..*Today nothing here changes what the bot buys\./);
  const lessons = root.locator(tid("bh-lesson"));
  assert.equal(await lessons.count(), 3);
  assert.match(await text(lessons.first()), /^Calls · Level 1 · 31 lost, 4 won · −\$4,740 Most losing trades.*Suggested change: Skip the trade.*Suggestion only · not applied$/);
  assert.deepEqual(await texts(root.locator(tid("bh-lesson-status"))), ["Suggestion only · not applied", "Suggestion only · not applied", "Still collecting trades"]);
  assert.ok(!/openai|numbers only|Error/i.test(await text(root)));
  assert.equal(await overflowsSideways(app.page), false);

  // ---- Ready: the verdict and eight plain pass / fail lines.
  await go(root, "ready");
  assert.match(await text(root.locator(tid("bh-ready-verdict"))), /^Not ready for real money 1 of 8 checks passed\. Every check must pass on real paper fills first\.$/);
  const checks = root.locator(tid("bh-check"));
  assert.equal(await checks.count(), 8);
  assert.deepEqual(await checks.evaluateAll((els) => els.filter((el) => el.dataset.pass === "yes").map((el) => el.dataset.check)), ["fill"]);
  assert.equal(await text(root.locator(`${tid("bh-check")}[data-check='dailyLoss'] .bh-check-text`)), "No day loses more than $500 worst day −$2,116 · broken on 5 days");
  assert.equal(await text(root.locator(`${tid("bh-check")}[data-check='profitFactor'] .bh-check-text`)), "Makes $1.30 for each $1 lost makes $0.46");
  assert.equal(await overflowsSideways(app.page), false);
  await shot(app, `${tag}-ready`);

  // ---- The last screen is remembered on this device.
  await app.page.reload({ waitUntil: "domcontentloaded" });
  await app.page.locator(`${ROOT} ${tid("bh-section-ready")}`).waitFor({ timeout: 60_000 });

  // ---- The other bot: money to spend, no closed trade. A designed empty state, no zeros dressed as results.
  await root.locator(tid("bh-account-paper5")).click();
  assert.equal(await checks.count(), 8);
  assert.equal(await text(root.locator(`${tid("bh-check")}[data-check='profit'] .bh-check-text`)), "Account is up $1,000 no result yet");
  await go(root, "home");
  assert.equal(await root.locator(tid("bh-state")).getAttribute("data-state"), "on");
  assert.match(await text(root.locator(tid("bh-state"))), /^Buying is on The Alpaca paper account has \$9,000 to spend\. No order placed yet/);
  assert.match(await text(root.locator(tid("bh-result"))), /\$0 from a \$10,000 start · 0 closed trades · since Nov 13 No trade has closed yet/);
  assert.equal(await text(root.locator(`${tid("bh-section-home")} ${tid("bh-empty")} h3`)), "No closed trades yet");
  await go(root, "journal");
  assert.equal(await text(root.locator(`${tid("bh-empty")} h3`)), "No closed trades yet");
  assert.equal(await root.locator(tid("bh-cal-day")).count(), 0);
  await go(root, "trades");
  assert.deepEqual(await texts(root.locator("[role='group'][aria-label='Filter trades'] button")), ["All 0", "Open 0", "Won 0", "Lost 0", "Not bought 1"]);
  assert.equal(await text(root.locator(`${tid("bh-empty")} h3`)), "No trades yet");
  assert.equal(await overflowsSideways(app.page), false);
  await shot(app, `${tag}-empty`);
  await root.locator(tid("bh-account-paper3")).click();
  await go(root, "home");
  return root;
}

/** A "More" row leaves the home page for an older page; "Back to Bot" returns to it. */
async function moreAndBack(app, name, landed) {
  const page = app.page;
  await page.locator(`${ROOT} ${tid(`bh-more-${name}`)}`).click();
  // 2026-11-25: the older pages carry the six Bot tabs (More lit) instead of the lone back button.
  const nav = page.locator(`[data-testid='bot-view'] ${tid("bh-oldnav")}`);
  const back = nav.locator(tid("bh-go-home"));
  await back.waitFor({ timeout: 30_000 });
  assert.deepEqual(await texts(nav.locator("button")), ["Home", "Journal", "Trades", "Learned", "Ready", "More"]);
  assert.deepEqual(await texts(nav.locator("button[aria-pressed='true']")), ["More"]);
  const small = await nav.locator("button").evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().width < 44 || el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent));
  assert.deepEqual(small, []);
  assert.equal(await page.locator(ROOT).count(), 0, `"${name}" replaces the home page`);
  if (landed) await page.locator(landed).first().waitFor({ timeout: 60_000 });
  const box = await back.boundingBox();
  assert.ok(box.height >= 44, "Back to Bot is big enough for a thumb");
  await back.click();
  await page.locator(`${ROOT} ${tid("bh-section-home")} ${tid("bh-state")}`).waitFor({ timeout: 30_000 });
  assert.equal(await page.locator(`[data-testid='bot-view'] ${tid("bh-oldnav")}`).count(), 0);
}

e2eTest("the Bot page opens on the new home: state first, five screens, one read, no writes", async () => {
  await withApp({ view: "Bot", storage: HOME, fakeReads: canned() }, async (app) => {
    const root = await wholeFlow(app, { phone: false });
    // On a wide screen the column stays narrow and the journal tiles sit three across.
    assert.ok((await root.boundingBox()).width <= 761);
    await go(root, "journal");
    const tiles = await root.locator(".bh-tiles.is-six .bh-tile").evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().top)));
    assert.equal(new Set(tiles).size, 2);
    await go(root, "home");
    // The home page reads the Scorecard once per load and nothing heavier.
    assert.ok(app.reads.some((r) => r.path === "/api/momx-scanner/bot-desk" && r.search === "?days=90"));
    assert.ok(!app.reads.some((r) => r.path === "/api/momx-scanner/bot"), "the home page must not download the virtual BOT trades");
    assert.ok(!app.reads.some((r) => r.path === "/api/momx-scanner/paper-learning"));
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the four More rows lead to the older pages and Back to Bot returns", async () => {
  await withApp({ view: "Bot", storage: HOME, fakeReads: canned() }, async (app) => {
    const root = await openHome(app);
    assert.deepEqual(await texts(root.locator(`${tid("bh-more")} .bh-row-l .bh-row-t`)),
      ["Trade one ticker", "Full scorecard", "Research: every signal, simulated", "Ask the bot"]);
    await moreAndBack(app, "scorecard", "[data-testid='bot-desk']");
    await moreAndBack(app, "ticker", "[data-testid='ticker-trader']");
    await moreAndBack(app, "research", "section.bot-kpis[aria-label='Totals']");
    await moreAndBack(app, "ask", "[data-testid='bot-chat']");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

// 2026-11-25, his "I see only this, where is research panel and they 2 panel?" (he was on Journal, where the
// More rows did not show). The More tab is on every screen and leads to the same four older pages.
for (const [label, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`${label}: from the Journal the More tab reaches Research and the Ticker trader, and no tab word is cut off`, async () => {
    await withApp({ view: "Bot", ...(viewport ? { viewport } : {}), storage: HOME, fakeReads: canned() }, async (app) => {
      const root = await openHome(app);
      await go(root, "journal");
      assert.equal(await root.locator(tid("bh-more")).count(), 0, "the Journal itself has no More rows");
      const cut = await root.locator("[aria-label='Bot sections'] button").evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent));
      assert.deepEqual(cut, []);
      await go(root, "more");
      assert.deepEqual(await texts(root.locator(`${tid("bh-more")} .bh-row-l .bh-row-t`)),
        ["Trade one ticker", "Full scorecard", "Research: every signal, simulated", "Ask the bot"]);
      await app.page.locator(`${ROOT} ${tid("bh-more-research")}`).click();
      await app.page.locator("section.bot-kpis[aria-label='Totals']").first().waitFor({ timeout: 60_000 });
      // The Trades tab on the Research page goes straight to Trades; Journal on the next page to Journal.
      await app.page.locator(`[data-testid='bot-view'] ${tid("bh-go-trades")}`).click();
      await app.page.locator(`${ROOT} ${tid("bh-section-trades")}`).waitFor({ timeout: 30_000 });
      await go(app.page.locator(ROOT), "more");
      await app.page.locator(`${ROOT} ${tid("bh-more-ticker")}`).click();
      await app.page.locator("[data-testid='ticker-trader']").first().waitFor({ timeout: 60_000 });
      assert.equal(await overflowsSideways(app.page), false);
      await app.page.locator(`[data-testid='bot-view'] ${tid("bh-go-journal")}`).click();
      await app.page.locator(`${ROOT} ${tid("bh-section-journal")}`).waitFor({ timeout: 30_000 });
      await app.page.reload();
      await app.page.locator(`${ROOT} ${tid("bh-section-journal")}`).waitFor({ timeout: 60_000 });
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("phone: the same flow fits 375 px with thumb-sized buttons, and More goes through", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: HOME, fakeReads: canned() }, async (app) => {
    const root = await wholeFlow(app, { phone: true });
    assert.ok((await root.boundingBox()).width <= 375);
    await moreAndBack(app, "scorecard", "[data-testid='bot-desk']");
    await moreAndBack(app, "ask", "[data-testid='bot-chat']");
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a failed refresh keeps the saved copy and says so in plain words", async () => {
  const saved = { "bot.deskSaved": JSON.stringify(DESK_DOC) };
  await withApp({ view: "Bot", viewport: PHONE, storage: { ...HOME, ...saved }, fakeReads: canned({ status: 503, json: { error: "RuntimeError: boom" } }) }, async (app) => {
    const root = await openHome(app);
    const fresh = root.locator(tid("bh-fresh"));
    await app.page.waitForFunction((sel) => /Could not refresh/.test(document.querySelector(sel)?.textContent || ""), `${ROOT} ${tid("bh-fresh")}`, { timeout: 30_000 });
    assert.equal(await text(fresh), "Could not refresh. Showing the last numbers. Read Nov 25, 1:19 AM ET.");
    assert.equal(await text(root.locator(`${tid("bh-result")} .bh-big`)), "−$5,124");
    assert.ok(!/503|RuntimeError|boom|fetch/i.test(await text(root)));
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the saved copy shows at once, marked as saved, until the server answers", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const newer = { ...DESK_DOC, asOf: "2026-11-25T09:31:00-05:00", accounts: { ...DESK_DOC.accounts, paper3: { ...PAPER3, stats: { ...PAPER3.stats, netPnl: -5000 } } } };
  const saved = { "bot.deskSaved": JSON.stringify(DESK_DOC) };
  await withApp({ view: "Bot", storage: { ...HOME, ...saved }, fakeReads: canned(async () => { await held; return { json: newer }; }) }, async (app) => {
    const root = await openHome(app);
    assert.equal(await text(root.locator(tid("bh-fresh"))), "Saved copy from Nov 25, 1:19 AM ET · updating…");
    assert.equal(await text(root.locator(`${tid("bh-result")} .bh-big`)), "−$5,124");
    release();
    await app.page.waitForFunction((sel) => /Read Nov 25, 9:31 AM ET/.test(document.querySelector(sel)?.textContent || ""), `${ROOT} ${tid("bh-fresh")}`, { timeout: 30_000 });
    assert.equal(await text(root.locator(`${tid("bh-result")} .bh-big`)), "−$5,000");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("no paper account at all: a plain empty page that still offers the More rows", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: HOME, fakeReads: canned({ json: { ...DESK_DOC, available: false, accounts: {}, lessons: null } }) }, async (app) => {
    const page = app.page;
    const root = page.locator(ROOT);
    await root.locator(tid("bh-empty")).waitFor({ timeout: 60_000 });
    await page.waitForFunction((sel) => /No paper trades yet/.test(document.querySelector(sel)?.textContent || ""), `${ROOT} ${tid("bh-empty")}`, { timeout: 30_000 });
    assert.equal(await root.locator(tid("bh-state")).count(), 0);
    assert.equal(await root.locator(`${tid("bh-more")} button`).count(), 4);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});
