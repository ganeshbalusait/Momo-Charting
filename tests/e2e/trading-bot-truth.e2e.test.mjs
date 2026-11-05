// Bot page -> Truth check card (2026-11-04): the BOT's virtual mid trades next to
// what the real Alpaca paper account got. Reads are canned; nothing is written.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const TRUTH_READ = "GET /api/momx-scanner/bot-truth";
const CARD = "section.bot-truth[aria-label='Truth check']";

export const TRUTH = {
  available: true, days: 30, from: "2026-10-19", through: "2026-11-04",
  accounts: {
    paper3: {
      orders: 319, filled: 105, missed: 212, open: 2, unmatched: 0, fillRatePct: 33.1,
      matched: { n: 105, botPnl: -3469, botRoi: -6.3, paperPnl: -5709, paperRoi: -10.7 },
      missedBotPnl: 1075.5,
      biggestMissed: [{ day: "2026-11-04", symbol: "AAPL", side: "bull", rule: "rvol", botEntry: 3.4, limit: 3.15, botPct: 231.6 }],
      byRule: [{ side: "bull", rule: "call2h", orders: 47, missed: 30, fillRatePct: 36.2, botRoi: 16.7, paperRoi: 16.0 }],
    },
  },
};

function canned(truth) {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
    [TRUTH_READ]: typeof truth === "function" ? truth : { json: truth },
  };
}

e2eTest("the Truth check card shows fill rate, BOT vs paper ROI and the biggest missed win", async () => {
  await withApp({ view: "Bot", fakeReads: canned(TRUTH) }, async (app) => {
    const card = app.page.locator(CARD);
    await card.locator(".bot-truth-headline").waitFor({ timeout: 60_000 });
    assert.equal((await card.locator(".bot-truth-headline").innerText()).trim(),
      "Your mid orders filled 105 of 317 (33%). On the 105 trades that filled and closed, the BOT says −6.3%, your paper account got −10.7%.");
    const values = (await card.locator(".bot-kpi-value").allInnerTexts()).map((s) => s.trim());
    assert.deepEqual(values, ["33%", "−6.3%", "−10.7%", "BOT looks 4.4 pts better than real"]);
    assert.match(await card.locator(".bot-truth-missed").innerText(), /AAPL 11-04 .* \+231\.6% \(limit \$3\.15 not filled\)/);
    await card.locator("summary", { hasText: "By rule" }).click();
    assert.equal(await card.locator("table.bot-table tbody tr").count(), 1);
    assert.ok(app.reads.some((r) => r.path === "/api/momx-scanner/bot-truth" && r.search === "?days=30"));
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("no paper orders yet and a failing read both say so instead of breaking the page", async () => {
  await withApp({ view: "Bot", fakeReads: canned({ available: false, accounts: {} }) }, async (app) => {
    await app.page.locator(CARD, { hasText: "No paper orders yet." }).waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Bot", fakeReads: canned(() => ({ status: 500, json: { error: "x" } })) }, async (app) => {
    await app.page.locator(CARD, { hasText: "Truth check unavailable right now." }).waitFor({ timeout: 60_000 });
    await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 30_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the Truth check fits with no sideways scroll", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned(TRUTH) }, async (app) => {
    const card = app.page.locator(CARD);
    await card.locator(".bot-truth-headline").waitFor({ timeout: 60_000 });
    await card.locator("summary", { hasText: "By rule" }).click();
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("after the new order rules start, the card shows their own fill rate", async () => {
  const truth = structuredClone(TRUTH);
  truth.accounts.paper3.newRules = { orders: 4, filled: 3, missed: 1, open: 0, matched: { n: 2, botRoi: 12, paperRoi: 8.5 } };
  await withApp({ view: "Bot", fakeReads: canned(truth) }, async (app) => {
    const line = app.page.locator(`${CARD} .bot-truth-new`);
    await line.waitFor({ timeout: 60_000 });
    assert.equal((await line.innerText()).trim(),
      "Since the new order rules: filled 3 of 4 (75%) · BOT +12.0% vs paper +8.5% on 2 closed.");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Bot", fakeReads: canned(TRUTH) }, async (app) => {
    await app.page.locator(`${CARD} .bot-truth-headline`).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(`${CARD} .bot-truth-new`).count(), 0);
  });
});
