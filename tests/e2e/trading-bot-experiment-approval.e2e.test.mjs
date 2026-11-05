// Bot page -> Paper 5 rule statistics check and (sign-off mode) Approve / Reject
// (2026-11-05). Reads are canned; the one write is answered by the harness.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const STATUS = "[aria-label='Automated paper experiments']";
const BOX = "section.bot-experiment-approval[aria-label='Paper 5 rule waiting for approval']";
const DECIDE = "POST /api/momx-scanner/paper-experiment-decision";

const SPEC = { kind: "spread_ceiling", value: 0.1 };
const EVALUATION = { trades: 104, observedDays: 10, candidateNet: 1250, baselineNet: -310 };
const CHECK = { passed: true, trials: 7, lower: 12.6, reason: "Still better than baseline after counting all 7 rules tried" };

function team(experiments) {
  return { ...TEAM, experiments: { ...TEAM.experiments, ...experiments } };
}

function canned(teamDoc) {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: teamDoc },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
    "GET /api/momx-scanner/bot-truth": { json: { available: false, accounts: {} } },
  };
}

e2eTest("an automatically switched-on rule shows its statistics check and who switched it on", async () => {
  const doc = team({ activeId: "cand1", registeredActiveId: "cand1",
    latest: { id: "cand1", state: "active", reason: "Passed the forward test and the statistics check; Paper 5 uses this rule automatically",
      screen: { spec: SPEC }, statsCheck: CHECK, approvedBy: "automatic (statistics check)" } });
  await withApp({ view: "Bot", fakeReads: canned(doc) }, async (app) => {
    const line = app.page.locator(`${STATUS} .bot-experiment-check`);
    await line.waitFor({ timeout: 60_000 });
    assert.equal((await line.innerText()).trim(),
      "Statistics check: passed · Still better than baseline after counting all 7 rules tried · switched on by automatic (statistics check)");
    assert.equal(await app.page.locator(BOX).count(), 0);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("sign-off mode: a waiting rule can be approved with one confirmed click", async () => {
  const doc = team({ latest: { id: "cand1", state: "awaiting_approval", reason: "waiting", screen: { spec: SPEC }, statsCheck: CHECK },
    awaiting: { id: "cand1", screen: { spec: SPEC }, evaluation: EVALUATION, statsCheck: CHECK } });
  await withApp({ view: "Bot", fakeReads: canned(doc),
    fakeWrites: { [DECIDE]: { json: { ok: true, experiment: { id: "cand1", state: "active" } } } } }, async (app) => {
    const box = app.page.locator(BOX);
    await box.waitFor({ timeout: 60_000 });
    assert.match(await box.innerText(), /Only take entries with a spread no wider than 10% of the price/);
    assert.match(await box.innerText(), /104 trades over 10 days · with the rule \$1,250 vs −\$310 without it/);
    app.page.once("dialog", (dialog) => dialog.accept());
    await box.getByRole("button", { name: "Approve" }).click();
    await box.locator("[role='status']", { hasText: "Approved" }).waitFor({ timeout: 10_000 });
    assert.equal(app.writes.length, 1);
    assert.deepEqual(JSON.parse(app.writes[0].body), { id: "cand1", decision: "approve" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("cancelling the confirm sends nothing; phone width fits", async () => {
  const doc = team({ awaiting: { id: "cand1", screen: { spec: SPEC }, evaluation: EVALUATION, statsCheck: CHECK } });
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned(doc) }, async (app) => {
    const box = app.page.locator(BOX);
    await box.waitFor({ timeout: 60_000 });
    app.page.once("dialog", (dialog) => dialog.dismiss());
    await box.getByRole("button", { name: "Reject" }).click();
    assert.deepEqual(app.writes, []);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
