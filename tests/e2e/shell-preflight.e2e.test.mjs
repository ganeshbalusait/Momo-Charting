// The APP HEALTH CHECKLIST card (admin only) end to end (coverage 2026-11-04,
// group "shell"). The checklist read is a fixture where the state matters and
// the Run check now POST is always answered by the harness - no broker probe,
// no heal, no push ever runs from a spec.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const READ = "GET /api/preflight";
const RUN = "POST /api/preflight/run";
const DAY = 24 * 3600 * 1000;
const etDay = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date(ms));

const run = (ms, checks) => ({ at: new Date(ms).toISOString(), available: true, passed: 1, warned: 0, failed: 1, unknown: 0, checks });
const CHECKS = [
  { id: "schwab", label: "Schwab market data", status: "pass", measured: "2 tokens, 5 days left" },
  { id: "chart", label: "SPY chart freshness", status: "fail", measured: "151 min behind", detail: "Restart the chart warmer.", critical: true },
];
const envelope = (result, historyDays = 5) => {
  const now = Date.now();
  const history = Array.from({ length: historyDays }, (_, i) => run(now - (i + 1) * DAY, CHECKS));
  return { available: true, unavailableReason: "", date: etDay(now), result, ranToday: Boolean(result), history, historyDays,
    historyError: "", schedule: { at: "08:45 ET", weekdaysOnly: true, windowMinutes: 30, threadStarted: true } };
};
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

async function card(page) {
  await page.locator(".preflight-card .preflight-headline").waitFor({ state: "visible", timeout: 60_000 });
}

e2eTest("an admin sees the checklist first on Settings, with a result or an honest empty notice (live)", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await card(app.page);
    const firstCard = app.page.locator(".settings-scroll-layout .paper-trading-stack > section.data-card").nth(1);
    assert.match(await firstCard.getAttribute("class"), /preflight-card/, "the checklist sits right under MY ACCOUNT");
    await app.page.locator(".preflight-card .preflight-check, .preflight-card .preflight-notice").first().waitFor({ timeout: 30_000 });
    assert.equal(await app.page.locator(".preflight-run-button").count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Run check now shows progress, then the fresh rows worst first", async () => {
  let ran = false;
  let release;
  const read = () => ({ json: envelope(ran ? run(Date.now(), CHECKS) : null) });
  const post = () => new Promise((resolve) => { release = () => { ran = true; resolve({ json: { ...envelope(run(Date.now(), CHECKS)), ran: run(Date.now(), CHECKS) } }); }; });
  await withApp({ view: "Settings", fakeReads: { [READ]: read }, fakeWrites: { [RUN]: post } }, async (app) => {
    await card(app.page);
    await app.page.locator(".preflight-notice.is-empty", { hasText: "No result yet today" }).waitFor({ timeout: 15_000 });
    const button = app.page.locator(".preflight-run-button");
    await button.click();
    await app.page.locator(".preflight-run-button", { hasText: "Running checks..." }).waitFor({ timeout: 10_000 });
    assert.equal(await button.isDisabled(), true);
    release();
    await app.page.locator(".preflight-run-button:enabled", { hasText: "Run check now" }).waitFor({ timeout: 15_000 });
    const rows = app.page.locator(".preflight-checks .preflight-check");
    await rows.first().waitFor({ timeout: 15_000 });
    assert.match(await rows.nth(0).innerText(), /SPY chart freshness[\s\S]*CRITICAL[\s\S]*151 min behind/);
    assert.match(await rows.nth(1).innerText(), /Schwab market data/);
    assert.equal(app.writes.filter((w) => w.path === "/api/preflight/run").length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a failed run shows the server's reason", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [READ]: { json: envelope(run(Date.now(), CHECKS)) } },
    fakeWrites: { [RUN]: { status: 500, json: { error: "preflight lock busy" } } },
  }, async (app) => {
    await card(app.page);
    await app.page.locator(".preflight-run-button").click();
    const notice = app.page.locator(".preflight-notice.is-error");
    await notice.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await notice.innerText()).trim(), "preflight lock busy");
    assert.equal((await app.page.locator(".preflight-headline b").innerText()).trim(), "Checklist unavailable");
  });
});

test("after a failed run the previous rows are not left standing under the error",
  { skip: marketHoursBlock() || false, todo: "a failed Run check now keeps the earlier rows on screen beneath 'Checklist unavailable' (the GET path clears them; the POST path does not)", timeout: 180_000 },
  async () => {
    await withApp({
      view: "Settings",
      fakeReads: { [READ]: { json: envelope(run(Date.now(), CHECKS)) } },
      fakeWrites: { [RUN]: { status: 500, json: { error: "preflight lock busy" } } },
    }, async (app) => {
      await card(app.page);
      await app.page.locator(".preflight-checks .preflight-check").first().waitFor({ timeout: 15_000 });
      await app.page.locator(".preflight-run-button").click();
      await app.page.locator(".preflight-notice.is-error").waitFor({ timeout: 15_000 });
      assert.equal(await app.page.locator(".preflight-checks .preflight-check").count(), 0);
    });
  });

e2eTest("the 30-day strip: tapping a day shows that day's result", async () => {
  await withApp({ view: "Settings", fakeReads: { [READ]: { json: envelope(run(Date.now(), CHECKS), 6) } } }, async (app) => {
    await card(app.page);
    const days = app.page.locator(".preflight-strip .preflight-day");
    await days.first().waitFor({ timeout: 15_000 });
    assert.equal(await days.count(), 30);
    const square = days.nth(28);
    const title = await square.getAttribute("aria-label");
    await square.click();
    assert.match(await square.getAttribute("class"), /is-selected/);
    assert.equal((await app.page.locator(".preflight-day-detail").innerText()).trim(), title);
  });
});

e2eTest("a result two days old carries the stale warning", async () => {
  await withApp({ view: "Settings", fakeReads: { [READ]: { json: envelope(run(Date.now() - 2 * DAY, CHECKS)) } } }, async (app) => {
    await card(app.page);
    const stale = app.page.locator(".preflight-notice.is-stale");
    await stale.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await stale.innerText(), /Run it again before you trade on it\./);
  });
});

e2eTest("a non-admin's browser never even asks for the checklist", async () => {
  await withApp({ view: "Settings", fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await app.page.locator(".personal-api-keys-card").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await app.page.locator(".preflight-card").count(), 0);
    assert.equal(app.reads.filter((r) => r.path.startsWith("/api/preflight")).length, 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the day strip fits", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [READ]: { json: envelope(run(Date.now(), CHECKS)) } } }, async (app) => {
    await card(app.page);
    const strip = app.page.locator(".preflight-strip");
    await strip.scrollIntoViewIfNeeded();
    const box = await strip.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `strip at ${JSON.stringify(box)} is cut off`);
    assert.equal(await overflowsSideways(app.page), false);
  });
});
