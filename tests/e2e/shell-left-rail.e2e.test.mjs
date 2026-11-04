// The desktop left rail, page memory and header shortcuts end to end
// (coverage 2026-11-04, group "shell"). Read-only navigation. A "reload" here
// is a second tab in the same browser: the harness re-seeds its storage on
// every load of the FIRST tab, so only a fresh tab shows what the app itself
// remembered. Charts & OI, Quick Options, Bot and Mag7 are left out of the
// page sweep on purpose: they start chart/chain/bot builds across many tickers.

import assert from "node:assert/strict";
import test from "node:test";

import { BASE_URL, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const RAIL = 'aside.sidebar[data-testid="primary-navigation"]';
const H1 = ".command-context h1";
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

// [rail title (what the trader reads), page key (what the header shows)]
const PAGES = [
  ["Alerts", "Auto Alert"], ["Premarket", "Premarket Scanner"], ["MomX Scanner", "MomX Scanner"],
  ["Option Paper Trading", "Option Paper Trading"], ["Stock Paper Trading", "Paper Trading"], ["Learn + Setup", "Learn + Setup"],
  ["Watchlist", "Watchlist"], ["Sectors", "Sectors"], ["News Feed", "News Feed"], ["Earnings Calendar", "Earnings Calendar"],
  ["ROI Calc", "ROI Calc"], ["OI Level Scripts", "OI Level Script TOS"], ["Release Notes", "Release Notes"], ["Settings", "Settings"],
];

const navItem = (page, title) => page.locator(`${RAIL} .nav-item[title="${title}"]`);

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const exactly = (text) => new RegExp(`^${escapeRegExp(text)}$`);

async function heading(page, expected) {
  await page.locator(H1, { hasText: exactly(expected) }).waitFor({ timeout: 30_000 });
  assert.equal((await page.locator(H1).innerText()).trim(), expected);
}

async function secondTab(app) {
  const page = await app.context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(BASE_URL + "/", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
  return { page, errors };
}

e2eTest("every rail button opens its page and moves the highlight", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await heading(app.page, "Release Notes");
    for (const [title, key] of PAGES) {
      const item = navItem(app.page, title);
      await item.click();
      await heading(app.page, key);
      assert.equal(await item.getAttribute("aria-current"), "page", `${title} is not highlighted`);
      assert.match(await item.getAttribute("class"), /nav-active/);
      assert.equal(await app.page.locator(`${RAIL} .nav-item[aria-current="page"]`).count(), 1);
    }
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("collapsing the rail survives a reload, and expanding brings the labels back", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    const toggle = app.page.locator(`${RAIL} .sidebar-toggle`);
    await toggle.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await toggle.getAttribute("aria-label"), "Collapse navigation");
    await toggle.click();
    await app.page.locator(".app-shell.sidebar-collapsed").waitFor({ timeout: 10_000 });
    assert.equal(await toggle.getAttribute("aria-label"), "Expand navigation");
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    const { page, errors } = await secondTab(app);
    assert.equal(await page.locator(".app-shell.sidebar-collapsed").count(), 1, "the collapsed rail was not remembered");
    await page.locator(`${RAIL} button[aria-label="Expand navigation"]`).click();
    await page.locator(".app-shell:not(.sidebar-collapsed)").waitFor({ timeout: 10_000 });
    assert.equal(await page.locator(`${RAIL} .nav-item[title="Settings"] .nav-label`).isVisible(), true);
    assert.deepEqual(errors, []);
  });
});

e2eTest("the last page opened is where a new tab starts", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await heading(app.page, "Release Notes");
    await navItem(app.page, "Watchlist").click();
    await heading(app.page, "Watchlist");
    const { page, errors } = await secondTab(app);
    await heading(page, "Watchlist");
    assert.equal(await page.locator(`${RAIL} .nav-item[aria-current="page"]`).getAttribute("title"), "Watchlist");
    assert.deepEqual(errors, []);
  });
});

e2eTest("a non-admin's rail has no Bot or paper-trading pages", async () => {
  await withApp({ view: "Release Notes", fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await heading(app.page, "Release Notes");
    for (const hidden of ["Bot", "Option Paper Trading", "Stock Paper Trading"]) {
      assert.equal(await navItem(app.page, hidden).count(), 0, `${hidden} is visible to a non-admin`);
    }
    assert.ok(await navItem(app.page, "Settings").count());
    assert.equal(await app.page.locator(".signed-in-user b").innerText(), "User");
  });
});

e2eTest("the header gear, avatar and help button go where they say", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await heading(app.page, "Release Notes");
    await app.page.locator('.topbar button[aria-label="Settings"]').click();
    await heading(app.page, "Settings");
    await navItem(app.page, "Release Notes").click();
    await heading(app.page, "Release Notes");
    await app.page.locator(".signed-in-user .avatar").click();
    await heading(app.page, "Settings");
    await app.page.locator('button[aria-label="Learn how to use AGX"]').click();
    await heading(app.page, "Learn + Setup");
    assert.deepEqual(app.pageErrors, []);
  });
});

test("a non-admin whose browser remembers Bot does not land on a hidden page",
  { skip: marketHoursBlock() || false, todo: "BUG: the remembered-page check accepts any rail label and ignores adminOnly, so a non-admin opens 'Bot' (empty, no rail item lit)", timeout: 180_000 },
  async () => {
    await withApp({ view: "Bot", fakeReads: { "GET /api/auth/status": member, "GET /api/momx-scanner/bot": { json: {} } } }, async (app) => {
      await app.page.locator(H1).waitFor({ timeout: 60_000 });
      assert.notEqual((await app.page.locator(H1).innerText()).trim(), "Bot");
      assert.equal(await app.page.locator(`${RAIL} .nav-item[aria-current="page"]`).count(), 1, "no rail item is lit");
    });
  });

e2eTest("at 1024 px with the rail collapsed every page is reachable and nothing scrolls sideways", async () => {
  await withApp({ view: "Release Notes", viewport: { width: 1024, height: 768 }, storage: { agenticSidebarCollapsed: "true" } }, async (app) => {
    await app.page.locator(".app-shell.sidebar-collapsed").waitFor({ timeout: 60_000 });
    for (const [title, key] of [["Settings", "Settings"], ["Watchlist", "Watchlist"], ["News Feed", "News Feed"], ["Release Notes", "Release Notes"]]) {
      await navItem(app.page, title).click();
      await heading(app.page, key);
      assert.equal(await overflowsSideways(app.page), false, `${key} scrolls sideways at 1024 px`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
