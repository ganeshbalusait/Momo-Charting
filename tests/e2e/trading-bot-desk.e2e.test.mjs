// Bot page -> Scorecard (2026-11-09): the paper accounts judged like a prop-firm
// evaluation on real paper fills. It is the view the Bot page opens on; the older
// virtual-BOT page sits behind "Research". Reads are canned; nothing is written.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

// The BOT's claim on the same filled trades (bot-truth), for the comparison card.
const TRUTH = { available: true, days: 30, accounts: {
  paper3: { orders: 319, filled: 105, missed: 212, matched: { n: 105, botPnl: -3469, botRoi: -6.3, paperPnl: -5709, paperRoi: -10.7 } } } };

const DESK_READ = "GET /api/momx-scanner/bot-desk";
const TRUTH_READ = "GET /api/momx-scanner/bot-truth";
const DESK = "[data-testid='bot-desk']";
const VERDICT = "[data-testid='desk-verdict']";
const SCORECARD = { "bot.view": "desk" };          // the harness seeds Research for the older Bot specs
const SHOTS = process.env.DESK_SHOTS || "";        // a folder: save a picture of each width there

const LIMITS = { accountSize: 10000, profitTarget: 1000, dailyLossLimit: 500, maxDrawdown: 1000, minDays: 20, minTrades: 100,
  minProfitFactor: 1.3, minFillPct: 50, luckTrades: 3 };

const check = (key, target, actual, pass, extra = {}) => ({ key, target, actual, pass, ...extra });

const PAPER3 = {
  orders: { sent: 375, filled: 132, missed: 238, skipped: 135, holding: 3, stuck: 2, fillRatePct: 35.7, stuckSymbols: ["CCJ", "META"], holdingOvernight: 1 },
  stats: { n: 129, wins: 32, losses: 95, flat: 2, winRatePct: 24.8, netPnl: -6817, roiPct: -10.1, cost: 67191, avgWin: 300.41, avgLoss: -172.95,
    profitFactor: 0.59, expectancy: -52.84, bestTrade: 1008, worstTrade: -807, pnlWithoutBest: -9664 },
  days: [
    { day: "2026-11-04", sent: 25, filled: 7, closed: 7, pnl: 420, cum: 420 },
    { day: "2026-11-05", sent: 25, filled: 12, closed: 12, pnl: -1354, cum: -934 },
    { day: "2026-11-06", sent: 19, filled: 11, closed: 8, pnl: -770, cum: -1704 },
    { day: "2026-11-09", sent: 12, filled: 4, closed: 4, pnl: 219, cum: -1485 },
  ],
  daySummary: { tradingDays: 16, greenDays: 3, redDays: 13, bestDay: { day: "2026-10-22", pnl: 957 }, worstDay: { day: "2026-11-05", pnl: -1354 },
    maxDrawdown: 7281, dailyLimitBreaks: 9 },
  byRule: [
    { side: "bull", rule: "call2h", sent: 54, filled: 20, fillRatePct: 37.7, closed: 20, wins: 8, winRatePct: 40, pnl: 1350, roiPct: 12.3, smallSample: false },
    { side: "bear", rule: "skit", sent: 9, filled: 5, fillRatePct: 55.6, closed: 5, wins: 3, winRatePct: 60, pnl: 474, roiPct: 18.2, smallSample: true },
    { side: "bull", rule: "rvol", sent: 44, filled: 21, fillRatePct: 51.2, closed: 21, wins: 4, winRatePct: 19, pnl: -2738, roiPct: -23.9, smallSample: false },
  ],
  objectives: [
    check("profit", 1000, -6817, false), check("dailyLoss", -500, -1354, false, { breaks: 9 }), check("drawdown", 1000, 7281, false),
    check("days", 20, 16, false), check("trades", 100, 129, true), check("profitFactor", 1.3, 0.59, false),
    check("luck", 0, -9664, false, { trades: 3 }), check("fill", 50, 35.7, false),
  ],
  verdict: { ready: false, passed: 1, total: 8 },
  from: "2026-10-19", through: "2026-11-09", lastActivity: "2026-11-09T15:05:52-05:00",
};

const PAPER5 = {
  ...PAPER3,
  orders: { sent: 150, filled: 62, missed: 87, skipped: 184, holding: 0, stuck: 0, fillRatePct: 41.6, stuckSymbols: [], holdingOvernight: 0 },
  stats: { ...PAPER3.stats, n: 59, wins: 12, losses: 46, netPnl: 458, roiPct: 1.2 },
  days: [{ day: "2026-11-09", sent: 25, filled: 11, closed: 10, pnl: 825, cum: 458 }],
  objectives: PAPER3.objectives.map((o) => ({ ...o, pass: true })),
  verdict: { ready: true, passed: 8, total: 8 },
  from: "2026-11-02",
};

const DESK_DOC = { available: true, days: 90, asOf: "2026-11-09T18:30:00-05:00", limits: LIMITS, smallSample: 20, paused: false,
  accounts: { paper3: PAPER3, paper5: PAPER5 } };

function canned(desk, truth = TRUTH) {
  return {
    [DESK_READ]: typeof desk === "function" ? desk : { json: desk },
    [TRUTH_READ]: { json: truth },
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
  };
}

const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());

async function shot(app, name) {
  if (SHOTS) await app.page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

e2eTest("the Bot page opens on the Scorecard: verdict, the eight checks and the real numbers", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(DESK_DOC) }, async (app) => {
    const desk = app.page.locator(DESK);
    await desk.locator(VERDICT).waitFor({ timeout: 60_000 });
    assert.equal((await desk.locator(`${VERDICT} h3`).textContent()).trim(), "Not ready for real money");
    assert.equal((await desk.locator(`${VERDICT} .desk-verdict-main p`).textContent()).trim(),
      "Passed 1 of 8 checks on real paper fills. Every check must pass before real money.");
    assert.match(await desk.locator(".desk-asof").textContent(), /Real paper fills · as of 11-09 18:30 ET/);

    assert.equal(await desk.locator(".desk-check").count(), 8);
    assert.deepEqual(await texts(desk.locator(".desk-check.is-pass .desk-check-label")), ["Filled trades closed"]);
    const profit = desk.locator(".desk-check[data-check='profit']");
    assert.equal((await profit.locator(".desk-check-actual").textContent()).trim(), "−$6,817");
    assert.equal((await profit.locator(".desk-check-target").textContent()).trim(), "Needs +$1,000");
    assert.equal((await desk.locator(".desk-check[data-check='dailyLoss'] .desk-check-target").textContent()).trim(),
      "Worst day · limit $500 · broken on 9 days");

    assert.deepEqual(await texts(desk.locator(".desk-alerts li")),
      ["2 stuck orders are holding a slot (CCJ, META)", "1 position from an earlier day still not sold"]);
    assert.match(await desk.locator(".desk-coverage").textContent(), /Paper 3 · 2026-10-19 to 2026-11-09 · 375 orders sent · 132 filled · 238 never filled/);
    assert.equal(await desk.locator("svg.desk-curve-svg circle").count(), 4);

    const rules = desk.locator("section[aria-label='Rules on real fills'] tbody tr");
    assert.equal(await rules.count(), 3);
    assert.match(await rules.nth(0).textContent(), /\+\$1,350/);
    assert.match(await rules.nth(1).textContent(), /too few/);
    const days = desk.locator("section[aria-label='Day by day'] tbody tr");
    assert.match(await days.nth(0).textContent(), /^2026-11-09/);                 // newest day first
    assert.match(await days.nth(2).textContent(), /2026-11-05.*over daily limit/);

    const claim = desk.locator("section[aria-label='BOT claim against real result']");
    assert.deepEqual(await texts(claim.locator(".desk-stat-value")), ["−6.3%", "−10.7%"]);

    assert.ok(app.reads.some((r) => r.path === "/api/momx-scanner/bot-desk" && r.search === "?days=90"));
    assert.ok(!app.reads.some((r) => r.path === "/api/momx-scanner/bot"), "the Scorecard must not download the virtual BOT trades");
    assert.equal(await overflowsSideways(app.page), false);
    await shot(app, "desk-desktop");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("switching to the other paper account and to Research and back", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(DESK_DOC) }, async (app) => {
    const desk = app.page.locator(DESK);
    await desk.locator(VERDICT).waitFor({ timeout: 60_000 });
    await desk.locator("[role='group'][aria-label='Paper account'] button", { hasText: "Paper 5" }).click();
    await desk.locator(`${VERDICT}.is-ready`).waitFor({ timeout: 15_000 });
    assert.equal((await desk.locator(`${VERDICT} h3`).textContent()).trim(), "Passed the paper evaluation");
    assert.equal(await desk.locator(".desk-check.is-pass").count(), 8);
    assert.equal(await desk.locator(".desk-alerts").count(), 0);

    const page = app.page.locator("[role='group'][aria-label='Page']");
    await page.locator("button", { hasText: "Research" }).click();
    await app.page.locator("section.bot-kpis[aria-label='Totals']").waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(DESK).count(), 0);
    await app.page.locator("[role='group'][aria-label='Which bot']").waitFor();
    await page.locator("button", { hasText: "Scorecard" }).click();
    await app.page.locator(`${DESK} ${VERDICT}`).waitFor({ timeout: 15_000 });
    assert.equal(await app.page.locator("section.bot-kpis[aria-label='Totals']").count(), 0);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("no paper orders yet, a paused bot and a failed read all say so plainly", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned({ ...DESK_DOC, available: false, paused: true, accounts: {} }, { available: false, accounts: {} }) }, async (app) => {
    const desk = app.page.locator(DESK);
    await desk.locator(`${VERDICT}.is-empty`).waitFor({ timeout: 60_000 });
    assert.equal((await desk.locator(`${VERDICT} h3`).textContent()).trim(), "No paper trades yet");
    assert.deepEqual(await texts(desk.locator(".desk-alerts li")), ["Paper bot is switched OFF"]);
    assert.equal(await desk.locator(".desk-check").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(() => ({ status: 500, json: { error: "x" } })) }, async (app) => {
    await app.page.locator(`${DESK} [role='alert']`, { hasText: "The scorecard could not load." }).waitFor({ timeout: 60_000 });
    await app.page.locator(`${DESK} button`, { hasText: "Try again" }).waitFor();
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the Scorecard fits with no sideways scroll", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned(DESK_DOC) }, async (app) => {
    const desk = app.page.locator(DESK);
    await desk.locator(VERDICT).waitFor({ timeout: 60_000 });
    assert.equal(await desk.locator(".desk-check").count(), 8);
    assert.equal(await overflowsSideways(app.page), false);
    const box = await desk.locator(VERDICT).boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "the verdict is cut off on a phone");
    await shot(app, "desk-phone");
    assert.deepEqual(app.pageErrors, []);
  });
});
