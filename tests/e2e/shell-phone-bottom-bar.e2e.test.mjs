// The phone bottom bar and its More sheet end to end (coverage 2026-11-04,
// group "shell"). Read-only navigation at 375x812 with touch. The Chart button
// is left out on purpose: it opens the saved chart grid (many tickers).

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const BAR = 'nav.mobile-chart-bottom-nav[aria-label="Mobile primary navigation"]';
const MORE = '[data-testid="mobile-primary-more"]';
const SHEET = '.mobile-more-sheet[role="menu"][aria-label="More destinations"]';
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

async function bar(page) {
  await page.locator(BAR).waitFor({ state: "visible", timeout: 60_000 });
}

async function openSheet(page) {
  await page.locator(MORE).tap();
  await page.locator(SHEET).waitFor({ state: "visible", timeout: 10_000 });
  return (await page.locator(`${SHEET} .mobile-more-grid [role="menuitem"]`).allInnerTexts()).map((t) => t.trim());
}

const view = (page) => page.evaluate(() => window.localStorage.getItem("agenticActiveView"));

e2eTest("on a phone the bottom bar replaces the rail; on a desktop the rail is back", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await bar(app.page);
    assert.equal(await app.page.locator("aside.sidebar").isVisible(), false);
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Release Notes", viewport: DESKTOP }, async (app) => {
    await app.page.locator("aside.sidebar").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await app.page.locator(BAR).isVisible(), false);
  });
});

e2eTest("More lists every other page and none of the five bar pages", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await bar(app.page);
    const items = await openSheet(app.page);
    assert.equal(await app.page.locator(MORE).getAttribute("aria-expanded"), "true");
    for (const wanted of ["Settings", "Watchlist", "News Feed", "Alerts", "Release Notes", "Learn + Setup"]) {
      assert.ok(items.includes(wanted), `${wanted} missing from More: ${JSON.stringify(items)}`);
    }
    for (const onBar of ["Charts & OI", "Options", "Sectors", "Premarket", "MomX Scanner"]) {
      assert.ok(!items.includes(onBar), `${onBar} is on the bar and must not repeat in More`);
    }
    assert.equal((await app.page.locator(`${SHEET} header b`).innerText()).trim(), "Go to");
  });
});

e2eTest("a More destination opens, closes the sheet and lights More", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await bar(app.page);
    await openSheet(app.page);
    await app.page.locator(`${SHEET} [role="menuitem"]`, { hasText: "Settings" }).tap();
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await app.page.locator(SHEET).count(), 0);
    assert.match(await app.page.locator(MORE).getAttribute("class"), /is-active/);
    assert.equal(await view(app.page), "Settings");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Close and a tap on the backdrop each shut the sheet without navigating", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await bar(app.page);
    await openSheet(app.page);
    await app.page.locator(`${SHEET} button[aria-label="Close"]`).tap();
    assert.equal(await app.page.locator(SHEET).count(), 0);
    await openSheet(app.page);
    await app.page.locator(".mobile-more-backdrop").tap({ position: { x: 10, y: 10 } });
    assert.equal(await app.page.locator(SHEET).count(), 0);
    assert.equal(await view(app.page), "Release Notes");
    assert.equal(await app.page.locator(MORE).getAttribute("aria-expanded"), "false");
  });
});

e2eTest("Sectors, Premarket, MomX and Options open the page they name", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await bar(app.page);
    for (const [testid, key] of [["mobile-primary-sectors", "Sectors"], ["mobile-primary-premarket-scanner", "Premarket Scanner"],
      ["mobile-primary-momx-scanner", "MomX Scanner"], ["mobile-primary-options", "Quick Options"]]) {
      const button = app.page.locator(`[data-testid="${testid}"]`);
      await button.tap();
      await app.page.locator(`[data-testid="${testid}"][aria-current="page"]`).waitFor({ timeout: 15_000 });
      assert.match(await button.getAttribute("class"), /is-active/);
      assert.equal(await view(app.page), key);
      assert.equal(await overflowsSideways(app.page), false, `${key} scrolls sideways on a phone`);
      const box = await app.page.locator(BAR).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1 && box.y + box.height <= PHONE.height + 1,
        `the bar at ${JSON.stringify(box)} leaves the screen on ${key}`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a non-admin's More sheet has no Bot or paper-trading pages", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE, fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await bar(app.page);
    const items = await openSheet(app.page);
    for (const hidden of ["Bot", "Option Paper Trading", "Stock Paper Trading"]) {
      assert.ok(!items.includes(hidden), `${hidden} offered to a non-admin`);
    }
    assert.ok(items.includes("Settings"));
  });
});
