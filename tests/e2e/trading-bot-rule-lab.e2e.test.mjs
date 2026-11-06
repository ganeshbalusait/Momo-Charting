// Bot page -> "Every Setup rule · AI scorecard" (2026-11-05). Reads are canned;
// the section makes no writes.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const SECTION = "[data-testid='bot-rule-lab']";
const split = (wt, wr, wa) => ({ with: { trades: wt, winRate: wr, avgReturn: wa }, without: { trades: 40, winRate: 30, avgReturn: -12 } });
const LAB = {
  trials: 2, error: "",
  scorecard: {
    generatedAt: "2026-11-05T18:00:00-05:00",
    checks: { wallRoom3: { bull: "Room to run: nearest big call wall 3%+ above", bear: "Room to run: nearest big put wall 3%+ below" } },
    rules: [
      { side: "bull", rule: "mom", trades: 320, days: 15, winRate: 41.5, avgReturn: -3.2, lastDay: { day: "2026-11-05", trades: 22, winRate: 55, avgReturn: 2.4 },
        checks: { wallRoom3: split(90, 63, 7.5) }, aiTesting: { check: "wallRoom3", checkMeaning: "Room to run: nearest big call wall 3%+ above", startDay: "2026-11-05", pickedBy: "AI rule-lab agent" } },
      { side: "bull", rule: "chartSqz", trades: 4, days: 1, winRate: 50, avgReturn: 1, checks: {} },
      { side: "bear", rule: "sqz", trades: 70, days: 10, winRate: 35, avgReturn: -8, checks: {}, aiImproved: { checkMeaning: "Below VWAP" } },
    ],
  },
  experiments: [{ state: "running" }, { state: "improved" }],
};

function canned() {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
    "GET /api/momx-scanner/bot-truth": { json: { available: false, accounts: {} } },
    "GET /api/momx-scanner/rule-lab": { json: LAB },
  };
}

e2eTest("the BOT page lists every Setup rule with its record, best chart check and the AI's test", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    const section = app.page.locator(SECTION);
    await section.waitFor({ timeout: 60_000 });
    await section.locator("summary", { hasNotText: "Loading" }).waitFor({ timeout: 20_000 });
    assert.match(await section.locator("summary").innerText(), /1 testing · 1 improved · 0 rejected · 2 tried in all/);
    await section.locator("summary").click();
    const rows = section.locator("tbody tr");
    await rows.first().waitFor({ timeout: 10_000 });
    assert.equal(await rows.count(), 2, "bull side only");
    const first = await rows.first().innerText();
    assert.match(first, /320\s+42%\s+-3\.2%/);
    assert.match(first, /Room to run: nearest big call wall 3%\+ above: 63% · \+7\.5% \(90\)/);
    assert.match(first, /Testing since 2026-11-05/);
    assert.match(await rows.nth(1).innerText(), /Chart squeeze/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the scorecard scrolls inside its box, not the page", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned() }, async (app) => {
    const section = app.page.locator(SECTION);
    await section.waitFor({ timeout: 60_000 });
    await section.locator("summary").scrollIntoViewIfNeeded();
    await section.locator("summary").click();
    await section.locator("tbody tr").first().waitFor({ timeout: 10_000 });
    const box = await section.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `section at ${JSON.stringify(box)}`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
