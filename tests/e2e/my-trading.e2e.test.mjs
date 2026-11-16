// My Trading (owner only, 2026-11-11) end to end. Every read of the page's
// data is answered with a fixture (fakeReads), so no spec reaches Schwab or
// the saved trade copy. The one write, "Refresh now", is answered by
// fakeWrites and only recorded. Desktop: rail entry, tiles, too-few marking,
// habits with their trades, events strip, the refresh request, wheel scroll.
// Phone: More sheet entry, no sideways scroll. A regular user: no rail item,
// no sheet item, a remembered "My Trading" shows nothing and reads nothing.
// The server-side 403/401 for regular/signed-out users is proven by the
// integration tests (a loopback browser is always the owner to the server).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "My Trading";
const RAIL = 'aside.sidebar[data-testid="primary-navigation"]';
const MORE = '[data-testid="mobile-primary-more"]';
const SHEET = '.mobile-more-sheet[role="menu"][aria-label="More destinations"]';
const PAGE = '[data-testid="my-trading"]';
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

const stats = (trades, over = {}) => ({ trades, wins: Math.ceil(trades / 2), losses: Math.floor(trades / 2), winRate: 0.5,
  avgWin: 120, avgLoss: -80, expectancy: 20, profitFactor: 1.5, pnl: trades * 20, ...over });

const fixture = (days = 30) => ({
  days, period: { days, from: "2026-10-13", to: "2026-11-11" },
  schwab: { lastReadAt: "2026-11-10T16:31:04-05:00", stoppedDay: null, nextManualAt: "2020-01-01T00:00:00-05:00" },
  summary: stats(24, { pnl: -350, expectancy: -14.6, medianHoldMinutes: 42 }),
  breakdowns: {
    setup: [{ key: "L3", label: "BULL Level 3", ...stats(12, { pnl: 400 }) }, { key: "none", label: "not on the board", ...stats(4, { pnl: -900 }) }],
    timeOfDay: [{ label: "09:30-10:00", ...stats(8) }, { label: "10-11", ...stats(16) }],
    dte: [{ label: "0", ...stats(14) }, { label: "1-2", ...stats(10) }],
    callPut: [{ label: "Calls", ...stats(20) }, { label: "Puts", ...stats(4, { pnl: -500 }) }],
    ticker: [{ label: "NVDA", ...stats(11) }, { label: "TSLA", ...stats(13, { pnl: -700 }) }],
  },
  habits: [
    { key: "revenge", count: 2, cost: -310, trades: [
      { id: "t1", underlying: "NVDA", expiry: "2026-11-14", strike: 190, putCall: "CALL", entryAt: "2026-11-05T10:12:00-05:00", pnl: -210 },
      { id: "t2", underlying: "TSLA", expiry: "2026-11-14", strike: 450, putCall: "PUT", entryAt: "2026-11-06T11:02:00-05:00", pnl: -100 }] },
    { key: "overtrading", count: 0, cost: 0, trades: [] },
  ],
  execution: { trades: 9, spreadCost: 184.5, avgSpreadDollars: 20.5, avgSpreadPct: 0.042, vsMid: -184.5, approxCount: 3, unpriced: 15 },
  paperBots: [{ account: "paper3", label: "Paper 3", ...stats(30, { pnl: 1200 }) }],
  events: { items: [{ at: "2026-11-13T08:30:00-05:00", title: "CPI", impact: "high" }], note: "" },
  openPositions: [],
});

const insights = (read) => ({ json: fixture(Number(new URLSearchParams(read.search).get("days") || 30)) });

e2eTest("the owner opens My Trading from the rail and reads the numbers", async () => {
  await withApp({
    view: "Release Notes",
    fakeReads: { "GET /api/my-insights": insights },
    fakeWrites: { "POST /api/my-insights/refresh": { status: 202, json: { ok: true, message: "Reading from Schwab." } } },
  }, async (app) => {
    const { page } = app;
    const item = page.locator(`${RAIL} .nav-item[title="${VIEW}"]`);
    await item.waitFor({ state: "visible", timeout: 60_000 });
    await item.click();
    await page.locator(PAGE).waitFor({ timeout: 30_000 });
    await page.locator('[data-testid="my-trading-tiles"]').waitFor({ timeout: 30_000 });
    assert.equal((await page.locator('[data-testid="my-trading-tile-pnl"] .mt-tile-value').innerText()).trim(), "−$350");
    assert.equal((await page.locator('[data-testid="my-trading-tile-trades"] .mt-tile-value').innerText()).trim(), "24");
    assert.match(await page.locator('[data-testid="my-trading-schwab"]').innerText(), /Last read from Schwab at 11-10 16:31 ET/);
    // The small row is kept and marked, not hidden.
    assert.match(await page.locator('[data-testid="my-trading-table-setup"]').innerText(), /not on the board · too few trades to judge/);
    // Habits: costliest card first, its trades behind a button.
    const revenge = page.locator('[data-testid="my-trading-habit-revenge"]');
    assert.match(await revenge.innerText(), /2 times, \$310 lost/);
    await revenge.getByRole("button", { name: /Show the 2 trades/ }).click();
    assert.match(await revenge.innerText(), /NVDA 11-14 190C/);
    assert.match(await page.locator('[data-testid="my-trading-habit-overtrading"]').innerText(), /Did not happen in this period/);
    assert.match(await page.locator('[data-testid="my-trading-events"]').innerText(), /CPI/);
    assert.match(await page.locator('[data-testid="my-trading-execution"]').innerText(), /approximate/);
    assert.match(await page.locator('[data-testid="my-trading-versus"]').innerText(), /You \(real\)[\s\S]*Paper 3/);
    assert.doesNotMatch(await page.locator(PAGE).innerText(), /you should/i);

    // Period picker asks the server for that period.
    await page.locator(`${PAGE} .mt-periods button`, { hasText: "90 days" }).click();
    await page.waitForFunction(() => document.querySelector('[data-testid="my-trading"] .mt-periods button[aria-pressed="true"]')?.textContent === "90 days");
    assert.ok(app.reads.some((r) => r.path === "/api/my-insights" && r.search === "?days=90"));

    // The one write: Refresh now asks for one read and nothing else is sent.
    await page.locator(`${PAGE} .mt-btn`, { hasText: "Refresh now" }).click();
    await page.locator(`${PAGE} .mt-reply.is-ok`).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.writes.map((w) => `${w.method} ${w.path}`).filter((w) => w.includes("my-insights")), ["POST /api/my-insights/refresh"]);
    assert.equal(app.writes.filter((w) => /order|trader\/v1/i.test(w.path)).length, 0);

    // The view owns its scroller: a real wheel over the content moves it.
    const box = await page.locator(PAGE).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + 200);
    await page.mouse.wheel(0, 1200);
    await page.waitForFunction(() => document.querySelector('[data-testid="my-trading-view"]').scrollTop > 0, null, { timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a stopped Schwab read shows sign-in needed and no refresh", async () => {
  const stopped = (read) => ({ json: { ...fixture(30), schwab: { lastReadAt: "2026-11-10T16:31:04-05:00", stoppedDay: "2026-11-11", reason: "401" } } });
  await withApp({ view: VIEW, fakeReads: { "GET /api/my-insights": stopped } }, async (app) => {
    const box = app.page.locator('[data-testid="my-trading-schwab"]');
    await box.locator("strong", { hasText: "Schwab sign-in needed" }).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(`${PAGE} .mt-btn`, { hasText: "Refresh now" }).isDisabled(), true);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone My Trading is in the More sheet and fits the screen", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE, fakeReads: { "GET /api/my-insights": insights } }, async (app) => {
    const { page } = app;
    await page.locator(MORE).waitFor({ state: "visible", timeout: 60_000 });
    await page.locator(MORE).tap();
    await page.locator(SHEET).waitFor({ state: "visible", timeout: 10_000 });
    await page.locator(`${SHEET} [role="menuitem"]`, { hasText: VIEW }).tap();
    await page.locator('[data-testid="my-trading-tiles"]').waitFor({ timeout: 30_000 });
    assert.equal(await overflowsSideways(page), false, "the page scrolls sideways on a phone");
    const view = await page.locator('[data-testid="my-trading-view"]').boundingBox();
    const tiles = await page.locator('[data-testid="my-trading-tiles"]').boundingBox();
    assert.ok(tiles.x >= view.x - 1 && tiles.x + tiles.width <= view.x + view.width + 1, "tiles are cut off at the side");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a regular user has no My Trading entry and a remembered one shows and reads nothing", async () => {
  await withApp({ view: "Release Notes", fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await app.page.locator(`${RAIL} .nav-item[title="Settings"]`).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(`${RAIL} .nav-item[title="${VIEW}"]`).count(), 0);
  });
  await withApp({ view: "Release Notes", viewport: PHONE, fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await app.page.locator(MORE).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(MORE).tap();
    await app.page.locator(SHEET).waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(await app.page.locator(`${SHEET} [role="menuitem"]`, { hasText: VIEW }).count(), 0);
  });
  await withApp({ view: VIEW, fakeReads: { "GET /api/auth/status": member, "GET /api/my-insights": { status: 403, json: { error: "owner only" } } } }, async (app) => {
    await app.page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
    await app.page.waitForTimeout(1500); // give a wrongly-mounted panel time to ask for its data
    assert.equal(await app.page.locator('[data-testid="my-trading-view"]').count(), 0);
    assert.equal(app.reads.filter((r) => r.path.startsWith("/api/my-insights")).length, 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a read that found no trades says so, and fills better than mid read as saved", async () => {
  // Real result 11-12: Schwab answered for …1867 with no option trades. The page must not ask the owner to wait for a first read.
  const empty = () => ({ json: { ...fixture(90), available: false, summary: stats(0), breakdowns: {}, habits: [], execution: null, paperBots: [],
    schwab: { state: "ok", lastReadAt: "2026-11-12T01:09:00-05:00", accounts: ["…1867"], firstDay: null, throughDay: "2026-11-12", nextManualAt: "2020-01-01T00:00:00-05:00" } } });
  await withApp({ view: VIEW, fakeReads: { "GET /api/my-insights": empty } }, async (app) => {
    const line = app.page.locator('[data-testid="my-trading-empty"]');
    await line.waitFor({ timeout: 60_000 });
    assert.equal((await line.innerText()).trim(), "Read from Schwab at 11-12 01:09 ET: no option trades found in the last 90 days on account …1867.");
    assert.doesNotMatch(await app.page.locator(PAGE).innerText(), /first read happens/);
    assert.deepEqual(app.pageErrors, []);
  });
  const saved = () => ({ json: { ...fixture(30), execution: { trades: 4, spreadCost: -84, vsMid: 84, avgSpreadDollars: -21, avgSpreadPct: -0.03, approxCount: 0, unpriced: 0 } } });
  await withApp({ view: VIEW, fakeReads: { "GET /api/my-insights": saved } }, async (app) => {
    const card = app.page.locator('[data-testid="my-trading-execution"]');
    await card.waitFor({ timeout: 60_000 });
    const text = await card.innerText();
    assert.match(text, /Saved vs mid in total[\s\S]*\$84/);
    assert.match(text, /Saved per fill on average[\s\S]*\$21/);
    assert.match(text, /3% of the price below mid/);
    assert.doesNotMatch(text, /Spread paid|-3%/);
    assert.deepEqual(app.pageErrors, []);
  });
});
