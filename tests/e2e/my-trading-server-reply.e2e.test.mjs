// My Trading (owner only, 2026-11-11): the REAL server reply on the real screen, end to end.
// The screen's own spec (my-trading.e2e.test.mjs) plays fixtures; this one lets GET /api/my-insights
// reach the running api_server, which only reads the saved once-a-day Schwab copy (it never calls
// Schwab), and checks the page shows the same numbers the route sent, at desktop and phone width.
// The refresh button is never pressed here: every write is blocked by the harness and must stay empty.
// A regular user (faked sign-in status) gets no rail entry and the page never asks for the data.
// Needs an api_server restarted with the My Trading routes; until then the route answers 404 and the
// spec says so instead of passing.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "My Trading";
const PAGE = '[data-testid="my-trading"]';
const RAIL = 'aside.sidebar[data-testid="primary-navigation"]';
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-2", email: "e2e.member2@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

async function serverReply(page) {
  return page.evaluate(async () => {
    const res = await fetch("/api/my-insights?days=30", { credentials: "include" });
    return { status: res.status, body: await res.json().catch(() => null) };
  });
}

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`the owner sees the server's own My Trading numbers at ${label}`, async () => {
    await withApp({ view: VIEW, viewport, storage: { "myTrading.days": "30" } }, async (app) => {
      const { page } = app;
      await page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
      const reply = await serverReply(page);
      assert.equal(reply.status, 200, `GET /api/my-insights answered ${reply.status}; restart api_server with the My Trading routes`);
      const doc = reply.body;
      for (const key of ["summary", "breakdowns", "habits", "execution", "paperBots", "events", "schwab", "period"]) {
        assert.ok(key in doc, `the reply has no ${key}`);
      }
      assert.equal(doc.days, 30);
      const hasTrades = Number(doc.summary?.trades) > 0 || (doc.openPositions || doc.open || []).length > 0;
      if (hasTrades) {
        await page.locator('[data-testid="my-trading-tiles"]').waitFor({ timeout: 60_000 });
        const trades = (await page.locator('[data-testid="my-trading-tile-trades"] .mt-tile-value').innerText()).trim();
        assert.equal(trades, String(doc.summary.trades));
      } else {
        // No Schwab trades saved yet (before the first once-a-day read): the page says so and shows no number tiles.
        await page.locator('[data-testid="my-trading-empty"]').waitFor({ timeout: 60_000 });
        assert.equal(await page.locator('[data-testid="my-trading-tiles"]').count(), 0);
      }
      // The Schwab line follows the server's state, in plain words.
      const source = await page.locator('[data-testid="my-trading-schwab"]').innerText();
      if (doc.schwab.stopped) assert.match(source, /Schwab sign-in needed/);
      else if (doc.schwab.lastReadAt) assert.match(source, /Last read from Schwab at/);
      else assert.match(source, /Not read from Schwab yet/);
      assert.doesNotMatch(await page.locator(PAGE).innerText(), /you should/i);
      if (viewport === PHONE) assert.equal(await overflowsSideways(page), false, "the page scrolls sideways on a phone");
      assert.deepEqual(app.writes.filter((w) => w.path.includes("my-insights") || /order|trader\/v1/i.test(w.path)), []);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("a regular user gets no My Trading entry and the page asks for nothing", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await app.page.locator(`${RAIL} .nav-item[title="Settings"]`).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(`${RAIL} .nav-item[title="${VIEW}"]`).count(), 0);
    await app.page.waitForTimeout(1500);
    assert.equal(app.reads.filter((r) => r.path.startsWith("/api/my-insights")).length, 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
