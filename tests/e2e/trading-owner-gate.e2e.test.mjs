// Owner-only trading pages, end to end (2026-11-04 coverage pass).
// The owner sees Bot / Option Paper Trading / Stock Paper Trading in the rail
// and in the phone More sheet; a signed-in user who is not an admin sees none
// of them and must never land on them from a remembered page. The non-admin
// is simulated by answering the boot-time /api/auth/status read with a
// user-role account (localhost otherwise auto-signs in as the owner). Nothing
// is written: every write is stopped by the harness.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, withApp } from "./harness.mjs";

const TRADING_PAGES = ["Bot", "Option Paper Trading", "Stock Paper Trading"];
const RAIL = "[data-testid='primary-navigation']";
const NON_ADMIN = {
  "GET /api/auth/status": {
    json: {
      bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
      user: { id: "e2e-viewer", email: "viewer@example.com", displayName: "E2E Viewer", role: "user", isAdmin: false, isActive: true, mustChangePassword: false },
    },
  },
};

async function railLabels(page) {
  await page.locator(`${RAIL} button.nav-item`).first().waitFor({ timeout: 60_000 });
  return (await page.locator(`${RAIL} button.nav-item .nav-label`).allInnerTexts()).map((label) => label.trim());
}

e2eTest("the owner's rail lists the three trading pages and opens the Bot page", async () => {
  await withApp({ view: "MomX Scanner" }, async (app) => {
    const labels = await railLabels(app.page);
    for (const page of TRADING_PAGES) assert.ok(labels.includes(page), `${page} missing from the rail: ${labels.join(", ")}`);
    await app.page.locator(`${RAIL} button.nav-item`, { hasText: /^Bot$/ }).click();
    await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
    assert.equal((await app.page.locator(`${RAIL} button.nav-item.nav-active .nav-label`).innerText()).trim(), "Bot");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the owner reaches the three trading pages from the More sheet", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE }, async (app) => {
    const more = app.page.locator("[data-testid='mobile-primary-more']");
    await more.waitFor({ state: "visible", timeout: 60_000 });
    await more.click();
    const sheet = app.page.locator(".mobile-more-sheet[role='menu']");
    await sheet.waitFor({ state: "visible", timeout: 15_000 });
    const items = (await sheet.locator("[role='menuitem'] span").allInnerTexts()).map((label) => label.trim());
    for (const page of TRADING_PAGES) assert.ok(items.includes(page), `${page} missing from More: ${items.join(", ")}`);
    await sheet.locator("[role='menuitem']", { hasText: "Stock Paper Trading" }).click();
    await app.page.locator("section.paper-trading-layout").waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a non-admin's rail has none of the trading pages", async () => {
  await withApp({ view: "MomX Scanner", fakeReads: NON_ADMIN }, async (app) => {
    const labels = await railLabels(app.page);
    assert.ok(labels.includes("MomX Scanner"), `the rail did not load: ${labels.join(", ")}`);
    for (const page of TRADING_PAGES) assert.ok(!labels.includes(page), `${page} shown to a non-admin`);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a non-admin with the Bot page remembered never sees the Bot workspace", async () => {
  await withApp({ view: "Bot", fakeReads: NON_ADMIN }, async (app) => {
    await railLabels(app.page);
    await app.page.locator("[data-testid='app-shell']").waitFor({ timeout: 30_000 });
    assert.equal(await app.page.locator("[data-testid='bot-view']").count(), 0);
    assert.equal(app.reads.filter((read) => read.path === "/api/momx-scanner/bot").length, 0, "the Bot data was requested for a non-admin");
  });
});

// FAILS TODAY: the remembered page is checked against every nav item, not the
// ones this user may see, and the stock / option paper pages render without an
// admin check (App.jsx activeView === "Paper Trading" / "Option Paper Trading").
for (const remembered of ["Paper Trading", "Option Paper Trading"]) {
  test(`a non-admin with '${remembered}' remembered never sees that trading page`, {
    skip: marketHoursBlock() || false,
    todo: "App.jsx restores any stored nav label and renders the paper trading pages with no isAdmin check",
    timeout: 180_000,
  }, async () => {
    await withApp({ view: remembered, fakeReads: NON_ADMIN }, async (app) => {
      await railLabels(app.page);
      await app.page.waitForTimeout(3_000);   // give a wrong restore time to paint
      assert.equal(await app.page.locator("section.paper-trading-layout").count(), 0, `${remembered} rendered for a non-admin`);
    });
  });
}
