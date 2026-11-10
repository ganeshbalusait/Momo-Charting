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

const CCJ_KEY = "bear|call2h|CCJ|2026-11-04T09:30:03-05:00";
const META_KEY = "bull|call2h|META|2026-11-05T09:30:08-05:00";
const NOT_FOUND = 'Awaiting broker reconciliation: {"code":40410000,"message":"order not found for mxp-b0-d9"}';
const CONTROL_WRITE = "POST /api/momx-scanner/paper-control";
const CONTROLS = "section[aria-label='Paper bot controls']";
const STUCK = "ul[aria-label='Stuck orders'] li";
const BUYING = { paused: false, pausedAt: null, dailyStop: { limit: 500, hit: false, hitAt: null, todayPnl: 219 } };

const PAPER3 = {
  control: BUYING,
  orders: { sent: 375, filled: 132, missed: 238, skipped: 135, holding: 3, stuck: 2, fillRatePct: 35.7, stuckSymbols: ["CCJ", "META"], holdingOvernight: 1,
    stuckRows: [
      { key: CCJ_KEY, symbol: "CCJ", side: "bear", rule: "call2h", contract: "CCJ   261106P00072000", buyAt: "2026-11-04T09:33:19-05:00", note: NOT_FOUND },
      { key: META_KEY, symbol: "META", side: "bull", rule: "call2h", contract: "META  261106C00632500", buyAt: "2026-11-05T09:30:39-05:00", note: NOT_FOUND },
    ] },
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

// ---- safety controls (2026-11-09): Pause / Resume, the daily loss stop, Remove a stuck order
e2eTest("Pause sends one request and the strip turns to PAUSED with a Resume button", async () => {
  let doc = DESK_DOC;
  const paused = { ...DESK_DOC, accounts: { ...DESK_DOC.accounts,
    paper3: { ...PAPER3, control: { ...BUYING, paused: true, pausedAt: "2026-11-09T18:31:00-05:00" } } } };
  const fakeWrites = { [CONTROL_WRITE]: () => { doc = paused; return { json: { ok: true, account: "paper3", paused: true, at: "2026-11-09T18:31:00-05:00" } }; } };
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(() => ({ json: doc })), fakeWrites }, async (app) => {
    const strip = app.page.locator(`${DESK} ${CONTROLS}`);
    await strip.waitFor({ timeout: 60_000 });
    assert.equal((await strip.locator("[data-testid='desk-buying']").textContent()).trim(), "New buys are ON");
    assert.equal((await strip.locator("[data-testid='desk-stop']").textContent()).trim(), "Daily loss stop $500 · today +$219");
    await strip.locator("button", { hasText: "Pause new buys" }).click();
    await strip.locator("button", { hasText: "Resume buying" }).waitFor({ timeout: 15_000 });
    assert.equal((await strip.locator("[data-testid='desk-buying']").textContent()).trim(), "New buys PAUSED at 11-09 18:31 ET");
    assert.match(await app.page.locator("[data-testid='desk-notice']").textContent(), /^Paused\. No new buys; positions already held still sell on their plan\.$/);
    assert.equal(app.writes.length, 1);
    assert.equal(app.writes[0].path, "/api/momx-scanner/paper-control");
    assert.deepEqual(JSON.parse(app.writes[0].body), { action: "pause", account: "paper3" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a stuck order: Cancel sends nothing, Remove sends its key and the row goes away", async () => {
  let doc = DESK_DOC;
  const freed = { ...DESK_DOC, accounts: { ...DESK_DOC.accounts, paper3: { ...PAPER3,
    orders: { ...PAPER3.orders, stuck: 1, stuckSymbols: ["META"], stuckRows: [PAPER3.orders.stuckRows[1]] } } } };
  const fakeWrites = { [CONTROL_WRITE]: () => { doc = freed; return { json: { ok: true, account: "paper3", key: CCJ_KEY, symbol: "CCJ" } }; } };
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(() => ({ json: doc })), fakeWrites }, async (app) => {
    const rows = app.page.locator(`${DESK} ${STUCK}`);
    await rows.first().waitFor({ timeout: 60_000 });
    assert.deepEqual(await texts(rows.locator("span")), [
      "CCJ $72 put · expires 11-06 · sent 11-04 09:33 ET · the broker has no record of it",
      "META $632.5 call · expires 11-06 · sent 11-05 09:30 ET · the broker has no record of it"]);
    const asked = [];
    app.page.once("dialog", (d) => { asked.push(d.message()); d.dismiss(); });
    await rows.nth(0).locator("button", { hasText: "Remove" }).click();
    assert.match(asked[0], /^Remove the stuck CCJ order\?/);
    assert.deepEqual(app.writes, []);                                  // he said no: nothing is sent
    assert.equal(await rows.count(), 2);

    app.page.once("dialog", (d) => d.accept());
    await rows.nth(0).locator("button", { hasText: "Remove" }).click();
    await app.page.locator("[data-testid='desk-notice']", { hasText: "Removed the stuck CCJ order. Its slot is free." }).waitFor({ timeout: 15_000 });
    assert.deepEqual(JSON.parse(app.writes[0].body), { action: "remove", account: "paper3", key: CCJ_KEY });
    assert.equal(app.writes.length, 1);
    await app.page.waitForFunction((sel) => document.querySelectorAll(sel).length === 1, `${DESK} ${STUCK}`, { timeout: 15_000 });
    assert.deepEqual(await texts(app.page.locator(`${DESK} .desk-alerts li`).first()), ["1 stuck order is holding a slot (META)"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused Remove shows the broker's reason and keeps the row; a tripped daily stop says so", async () => {
  const tripped = { ...DESK_DOC, accounts: { ...DESK_DOC.accounts, paper3: { ...PAPER3,
    control: { paused: false, pausedAt: null, dailyStop: { limit: 500, hit: true, hitAt: "2026-11-09T10:42:00-05:00", todayPnl: -560 } } } } };
  const refusal = "The broker says 3 contract(s) of this order filled. It is a real position, not a stuck order, so it was not removed.";
  const fakeWrites = { [CONTROL_WRITE]: { status: 409, json: { ok: false, error: refusal } } };
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(tripped), fakeWrites }, async (app) => {
    const rows = app.page.locator(`${DESK} ${STUCK}`);
    await rows.first().waitFor({ timeout: 60_000 });
    const stop = app.page.locator(`${DESK} [data-testid='desk-stop']`);
    assert.equal((await stop.textContent()).trim(), "Daily loss stop HIT at 11-09 10:42 ET · no new buys until tomorrow");
    assert.match(await stop.getAttribute("class"), /is-bad/);
    // The buying pill must not say ON while the stop holds buys.
    assert.equal((await app.page.locator(`${DESK} [data-testid='desk-buying']`).textContent()).trim(), "New buys HELD by the daily loss stop");
    app.page.once("dialog", (d) => d.accept());
    await rows.nth(1).locator("button", { hasText: "Remove" }).click();
    const notice = app.page.locator("[data-testid='desk-notice']");
    await notice.waitFor({ timeout: 15_000 });
    assert.equal((await notice.textContent()).trim(), refusal);
    assert.match(await notice.getAttribute("class"), /is-bad/);
    assert.equal(await rows.count(), 2);
    assert.deepEqual(JSON.parse(app.writes[0].body), { action: "remove", account: "paper3", key: META_KEY });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the controls and the stuck orders fit, and Remove can be tapped", async () => {
  const fakeWrites = { [CONTROL_WRITE]: { json: { ok: true, account: "paper3", key: CCJ_KEY, symbol: "CCJ" } } };
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned(DESK_DOC), fakeWrites }, async (app) => {
    const rows = app.page.locator(`${DESK} ${STUCK}`);
    await rows.first().waitFor({ timeout: 60_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [app.page.locator(`${DESK} ${CONTROLS}`), rows.nth(0), rows.nth(0).locator("button")]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "a control is cut off on a phone");
    }
    app.page.once("dialog", (d) => d.accept());
    await rows.nth(0).locator("button", { hasText: "Remove" }).tap();
    await app.page.locator("[data-testid='desk-notice']", { hasText: "Removed the stuck CCJ order." }).waitFor({ timeout: 15_000 });
    assert.deepEqual(JSON.parse(app.writes[0].body), { action: "remove", account: "paper3", key: CCJ_KEY });
    await shot(app, "desk-controls-phone");
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- fresh evaluation and AI loss lessons (2026-11-09)
const EMPTY = {
  control: BUYING, balance: 10000,
  orders: { sent: 0, filled: 0, missed: 0, skipped: 0, holding: 0, stuck: 0, fillRatePct: null, stuckSymbols: [], stuckRows: [], holdingOvernight: 0 },
  stats: { n: 0, wins: 0, losses: 0, flat: 0, winRatePct: null, netPnl: 0, roiPct: null, cost: 0, avgWin: null, avgLoss: null, profitFactor: null,
    expectancy: null, bestTrade: null, worstTrade: null, pnlWithoutBest: null },
  days: [], daySummary: { tradingDays: 0, greenDays: 0, redDays: 0, bestDay: null, worstDay: null, maxDrawdown: 0, dailyLimitBreaks: 0 },
  byRule: [], objectives: PAPER3.objectives.map((o) => ({ ...o, actual: null, pass: false })),
  verdict: { ready: false, passed: 0, total: 8 }, from: null, through: null, lastActivity: null,
};
const STUDY = { updatedAt: "2026-11-10T17:30:00-05:00", startDay: "2026-11-10", minLosses: 10,
  note: "Suggestions only. Nothing here blocks, changes or places a trade.", lessons: [
    { side: "bull", rule: "level1", losses: 12, wins: 4, netPnl: -340, status: "lesson", source: "openai", evidenceCount: 9,
      basis: "9 of 12 losing trades and 1 of 4 winning trades were 'first 30 minutes'",
      lesson: "Most losing trades were bought in the first 30 minutes on a wide spread.", proposedChange: "Try waiting until 10:00 before buying.",
      howToTest: "Compare the next 30 trades before and after 10:00." },
    { side: "bear", rule: "level3", losses: 2, wins: 1, netPnl: 15, status: "collecting", source: "none",
      lesson: "Not enough losing trades yet (2 of 10).", proposedChange: "", howToTest: "" }] };
const FRESH = { ...DESK_DOC, asOf: "2026-11-10T17:45:00-05:00", startDay: "2026-11-10", strategy: "Level 1 / 2 / 3 on the 0.20-delta contract",
  lessons: STUDY, accounts: { paper3: { ...PAPER3, balance: 9660, from: "2026-11-10", through: "2026-11-10" }, paper5: EMPTY } };
const LESSONS = "section[aria-label='AI lessons from losing trades']";

e2eTest("fresh evaluation: the start line with the balance, the AI lessons, and an account that has not traded yet", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(FRESH) }, async (app) => {
    const desk = app.page.locator(DESK);
    await desk.locator(VERDICT).waitFor({ timeout: 60_000 });
    assert.equal((await desk.locator("[data-testid='desk-evaluation']").textContent()).trim(),
      "Evaluation started 11-10 · trades only Level 1 / 2 / 3 on the 0.20-delta contract · $10,000 account · balance now $9,660");
    const card = desk.locator(LESSONS);
    assert.equal((await card.locator("[data-testid='desk-lessons-note']").textContent()).trim(),
      "Studied 11-10 17:30 ET · each signal counted once across both accounts · Suggestions only. Nothing here blocks, changes or places a trade.");
    const rows = card.locator(".desk-lessons li");
    assert.equal(await rows.count(), 2);
    assert.match(await rows.nth(0).textContent(), /Level 1.*calls.*AI lesson.*12 losing · 4 winning · −\$340/s);
    assert.match(await rows.nth(0).textContent(), /Most losing trades were bought in the first 30 minutes/);
    assert.match(await rows.nth(0).textContent(), /Checked against the counts: 9 of 12 losing trades and 1 of 4 winning trades were 'first 30 minutes'\./);
    assert.match(await rows.nth(0).textContent(), /To try: Try waiting until 10:00 before buying\./);
    assert.match(await desk.locator("section[aria-label='BOT claim against real result'] h4").textContent(), /since 11-10$/);
    assert.match(await rows.nth(0).textContent(), /How to test it: Compare the next 30 trades/);
    assert.match(await rows.nth(1).textContent(), /Level 3.*puts.*collecting.*Not enough losing trades yet \(2 of 10\)\./s);
    assert.equal(await rows.nth(1).locator(".desk-lesson-more").count(), 0);
    // The BOT's claim is read for the evaluation's own days (1 day old here), after the Scorecard read.
    assert.ok(app.reads.some((r) => r.path === "/api/momx-scanner/bot-truth" && r.search === "?days=1"));
    assert.ok(!app.reads.some((r) => r.path === "/api/momx-scanner/bot-truth" && r.search === "?days=30"));

    await desk.locator("[role='group'][aria-label='Paper account'] button", { hasText: "Paper 5" }).click();
    await desk.locator(`${VERDICT}.is-empty`).waitFor({ timeout: 15_000 });
    assert.equal((await desk.locator(`${VERDICT} h3`).textContent()).trim(), "No paper trades yet");
    assert.match(await desk.locator("[data-testid='desk-evaluation']").textContent(), /balance now \$10,000$/);
    assert.match(await desk.locator(".desk-coverage").nth(1).textContent(), /Paper 5 · no paper orders yet in this evaluation · judged as a \$10,000 evaluation account/);
    assert.equal(await desk.locator(".desk-check.is-pass").count(), 0);
    await desk.locator(`${CONTROLS} button`, { hasText: "Pause new buys" }).waitFor();      // it can still be paused
    assert.equal(await overflowsSideways(app.page), false);
    await shot(app, "desk-fresh-desktop");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("before the first study the lessons card says when it runs; phone width fits", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned({ ...FRESH, lessons: null }) }, async (app) => {
    const card = app.page.locator(`${DESK} ${LESSONS}`);
    await card.waitFor({ timeout: 60_000 });
    assert.match(await card.locator("[data-testid='desk-lessons-note']").textContent(), /^The first study runs after the next market close \(5:00 PM ET\)\./);
    assert.equal(await card.locator(".desk-lessons li").count(), 0);
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [card, app.page.locator(`${DESK} [data-testid='desk-evaluation']`)]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "cut off on a phone");
    }
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned(FRESH) }, async (app) => {
    const rows = app.page.locator(`${DESK} ${LESSONS} .desk-lessons li`);
    await rows.first().waitFor({ timeout: 60_000 });
    assert.equal(await overflowsSideways(app.page), false);
    const box = await rows.first().boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "a lesson is cut off on a phone");
    await shot(app, "desk-fresh-phone");
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- the three strategies side by side (2026-11-10: "Scoreboard I don't see 3 strategy? How I know which one is good")
const sideOf = (closed, pnl, winRatePct) => ({ closed, wins: 0, winRatePct, pnl, roiPct: null });
const strategy = (rule, closed, pnl, roiPct, extra = {}) => ({ rule, sent: closed + 4, filled: closed, fillRatePct: 80, closed, wins: 0,
  winRatePct: closed ? 54.2 : null, pnl, roiPct, perTrade: closed ? pnl / closed : null, smallSample: closed < 20,
  calls: sideOf(Math.ceil(closed * 0.75), pnl + 200, 61.1), puts: sideOf(Math.floor(closed * 0.25), -200, 33.3), sameAs: null, ...extra });
const STRATEGIES = { need: 20, best: "level2", allLosing: false, mostClosed: { rule: "level1", closed: 40 }, rows: [
  strategy("level1", 40, 900, 7.5), strategy("level2", 24, 1000, 13.9), strategy("level3", 24, 1000, 13.9, { sameAs: "level2" })] };
const NO_TRADES = { need: 20, best: null, allLosing: false, mostClosed: { rule: "level1", closed: 0 }, rows: ["level1", "level2", "level3"].map((rule) => ({
  rule, sent: 0, filled: 0, fillRatePct: null, closed: 0, wins: 0, winRatePct: null, pnl: 0, roiPct: null, perTrade: null, smallSample: true,
  calls: sideOf(0, 0, null), puts: sideOf(0, 0, null), sameAs: null })) };
const WITH_STRATEGIES = { ...FRESH, accounts: { paper3: { ...FRESH.accounts.paper3, strategies: STRATEGIES }, paper5: { ...EMPTY, strategies: NO_TRADES } } };
const STRATEGY_CARD = "section[aria-label='The three strategies']";

e2eTest("the three strategies are shown side by side with the best one named; an account with no trades lists all three too", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(WITH_STRATEGIES) }, async (app) => {
    const card = app.page.locator(`${DESK} ${STRATEGY_CARD}`);
    await card.waitFor({ timeout: 60_000 });
    assert.equal((await card.locator("[data-testid='desk-strategy-answer']").textContent()).trim(),
      "Best so far: Level 2 (+13.9% return, +$1,000 on 24 closed trades).");
    assert.deepEqual(await texts(card.locator("thead th")), ["Strategy", "Closed", "Win", "Real P/L", "Return", "Per trade", "Calls", "Puts", "Orders filled"]);
    const rows = card.locator("tbody tr");
    assert.equal(await rows.count(), 3);
    assert.deepEqual(await texts(rows.locator("td:first-child b")), ["Level 1", "Level 2", "Level 3"]);
    assert.match(await rows.nth(1).getAttribute("class"), /is-best/);
    assert.match(await rows.nth(1).textContent(), /best so far/);
    assert.match(await rows.nth(2).textContent(), /same trades as Level 2/);
    assert.deepEqual(await texts(rows.nth(0).locator("td").filter({ hasNot: app.page.locator("b") })),
      ["40", "54%", "+$900", "+7.5%", "+$23", "+$1,100 · 61% of 30", "−$200 · 33% of 10", "40 of 44"]);
    // The card sits above the account curve, so it is seen without hunting.
    const cardBox = await card.boundingBox();
    const curveBox = await app.page.locator(`${DESK} section[aria-label='Account curve']`).boundingBox();
    assert.ok(cardBox && curveBox && cardBox.y < curveBox.y);

    await app.page.locator(`${DESK} [role='group'][aria-label='Paper account'] button`, { hasText: "Paper 5" }).click();
    await app.page.locator(`${DESK} ${VERDICT}.is-empty`).waitFor({ timeout: 15_000 });
    assert.match(await card.locator("[data-testid='desk-strategy-answer']").textContent(), /^No closed trades yet\. The first Level orders go out in the 9:30 AM ET buy window\.$/);
    assert.deepEqual(await texts(card.locator("tbody tr td:first-child b")), ["Level 1", "Level 2", "Level 3"]);
    assert.equal(await overflowsSideways(app.page), false);
    await shot(app, "desk-strategies-desktop");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the strategies card fits and its table scrolls inside the card", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned(WITH_STRATEGIES) }, async (app) => {
    const card = app.page.locator(`${DESK} ${STRATEGY_CARD}`);
    await card.waitFor({ timeout: 60_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [card, card.locator("[data-testid='desk-strategy-answer']")]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "cut off on a phone");
    }
    assert.equal(await card.locator("tbody tr").count(), 3);
    await shot(app, "desk-strategies-phone");
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- rules bot against AI bot (2026-11-10: "see which strategy works good then we will use same for real money")
const botRow = (account, name, closed, pnl, roiPct, balance) => ({ account, mode: name === "AI bot" ? "ai" : "rules", name,
  how: name === "AI bot" ? "an AI ranks the waiting Level signals and the best one gets a free slot" : "the newest Level signal gets a free slot",
  sent: closed + 3, closed, winRatePct: closed ? 45.5 : null, pnl, roiPct, perTrade: closed ? pnl / closed : null, balance });
const VERSUS = { need: 20, ready: true, leader: "paper5", allLosing: false,
  rows: [botRow("paper3", "Rules bot", 25, 120, 1.6, 10120), botRow("paper5", "AI bot", 22, 640, 9.7, 10640)] };
const TWO_BOTS = { ...WITH_STRATEGIES, versus: VERSUS, accounts: {
  paper3: { ...WITH_STRATEGIES.accounts.paper3, mode: { key: "rules", name: "Rules bot", how: "the newest Level signal gets a free slot" } },
  paper5: { ...WITH_STRATEGIES.accounts.paper5, mode: { key: "ai", name: "AI bot", how: "an AI ranks the waiting Level signals" },
    aiPicks: { picked: { sent: 9, closed: 6, wins: 4, winRatePct: 66.7, pnl: 210, roiPct: 9 }, fallback: { sent: 2, closed: 0, wins: 0, winRatePct: null, pnl: 0, roiPct: null } } } } };
const VERSUS_CARD = "section[aria-label='Rules bot against AI bot']";

e2eTest("Rules bot against AI bot: both listed, the one ahead is named, and the AI bot says how many orders the AI chose", async () => {
  await withApp({ view: "Bot", storage: SCORECARD, fakeReads: canned(TWO_BOTS) }, async (app) => {
    const card = app.page.locator(`${DESK} ${VERSUS_CARD}`);
    await card.waitFor({ timeout: 60_000 });
    assert.equal((await card.locator("[data-testid='desk-versus-answer']").textContent()).trim(),
      "AI bot is ahead (+9.7% return, +$640 on 22 closed trades). That is the one to take to real money, once its account passes the checks.");
    const rows = card.locator("tbody tr");
    assert.deepEqual(await texts(rows.locator("td:first-child b")), ["Rules bot", "AI bot"]);
    assert.match(await rows.nth(1).getAttribute("class"), /is-best/);
    assert.match(await rows.nth(1).textContent(), /Paper 5.*ahead/s);
    assert.deepEqual(await texts(rows.nth(0).locator("td").filter({ hasNot: app.page.locator("b") })), ["25", "46%", "+$120", "+1.6%", "+$5", "$10,120"]);
    assert.equal(await card.locator("[data-testid='desk-ai-picks']").count(), 0);          // Paper 3 (rules bot) is selected: no AI line
    assert.deepEqual(await texts(app.page.locator(`${DESK} [role='group'][aria-label='Paper account'] button`)), ["Paper 3 · Rules bot", "Paper 5 · AI bot"]);
    // The comparison sits above the three strategies and the checks follow below it.
    const a = await card.boundingBox();
    const b = await app.page.locator(`${DESK} ${STRATEGY_CARD}`).boundingBox();
    assert.ok(a && b && a.y < b.y);

    await app.page.locator(`${DESK} [role='group'][aria-label='Paper account'] button`, { hasText: "Paper 5" }).click();
    await card.locator("[data-testid='desk-ai-picks']").waitFor({ timeout: 15_000 });
    assert.equal((await card.locator("[data-testid='desk-ai-picks']").textContent()).trim(),
      "Orders chosen by the AI: 9 picked by the AI (+$210 on 6 closed) · 2 on the rules order because the AI had no answer in time.");
    assert.equal(await overflowsSideways(app.page), false);
    await shot(app, "desk-versus-desktop");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the bot comparison fits", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: SCORECARD, fakeReads: canned(TWO_BOTS) }, async (app) => {
    const card = app.page.locator(`${DESK} ${VERSUS_CARD}`);
    await card.waitFor({ timeout: 60_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [card, card.locator("[data-testid='desk-versus-answer']"), app.page.locator(`${DESK} [role='group'][aria-label='Paper account']`)]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "cut off on a phone");
    }
    await shot(app, "desk-versus-phone");
    assert.deepEqual(app.pageErrors, []);
  });
});
