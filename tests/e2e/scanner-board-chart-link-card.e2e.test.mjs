// MomX Scanner -> charts link and the ticker card, end to end (2026-11-04
// coverage). A ticker click opens Charts & OI on it (also from the detached
// scanner window, over the BroadcastChannel); hovering only warms the chart;
// the link colour is remembered; the lightning badge opens ONE draggable card
// that Escape closes; Pop-up cards is remembered; and on a phone a card fills
// the screen with its close button reachable.
//
// One ticker (NVDA) reaches the live chart route, only in the link tests.
// The card's day chart and option chain are fixtures, so no chain read starts.

import assert from "node:assert/strict";

import { BASE_URL, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const CHARTS_NAV = "button.nav-item[title='Charts & OI'][aria-current='page']";
const link = (page, symbol) => page.locator("tr.momx-row [data-testid='momx-symbol-link']", { hasText: new RegExp("^" + symbol + "$") });

function dayBars() {
  // 10:00-10:15 ET on 2026-11-03 (15:00 UTC): the card plots by ET minute of day.
  const start = Date.UTC(2026, 10, 3, 15, 0) / 1000;
  return { symbol: "NVDA", day: "2026-11-03",
    bars: [[start, 180.1], [start + 300, 181.4], [start + 600, 180.9], [start + 900, 182.4]], low: 180.1, high: 182.4 };
}

const cardReads = () => ({
  ...boardReads(),
  "GET /api/momx-scanner/daybars": { json: dayBars() },
  "GET /api/oi-finder": { json: { symbol: "NVDA", callRows: [], putRows: [], errors: [] } },
});

const chartReadsFor = (app, symbol, from = 0) => app.reads.slice(from)
  .filter((read) => read.path === "/api/oi-finder-chart" && new URLSearchParams(read.search).get("symbol") === symbol);

e2eTest("clicking a ticker opens Charts & OI on that ticker", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const nvda = link(app.page, "NVDA");
    assert.equal(await nvda.getAttribute("title"), "Open NVDA on the Red chart(s)");
    const from = app.reads.length;
    await nvda.click();
    await app.page.locator(CHARTS_NAV).waitFor({ timeout: 30_000 });
    await app.page.locator("[data-testid='charts-and-oi-view']").first().waitFor({ timeout: 30_000 });
    assert.equal(await app.page.locator("[data-testid='momx-scanner-view']").count(), 0, "left the scanner");
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    assert.ok(chartReadsFor(app, "NVDA", from).length >= 1, "the chart asked for NVDA");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("hovering a ticker sends one warm message, only prefetches, and stays on the scanner", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.evaluate(() => {
      window.__agxLinks = [];
      window.addEventListener("agx:chart-link", (event) => window.__agxLinks.push(event.detail));
    });
    const from = app.reads.length;
    await link(app.page, "AAPL").hover();
    await app.page.waitForTimeout(1_200);
    const messages = await app.page.evaluate(() => window.__agxLinks);
    assert.deepEqual(messages.map((m) => [m.symbol, m.intent, m.linkGroup]), [["AAPL", "warm", 1]]);
    // The warm may be served from the chart cache; any chart read it made is a prefetch.
    for (const read of chartReadsFor(app, "AAPL", from)) {
      assert.equal(new URLSearchParams(read.search).get("prefetch"), "true", read.search);
    }
    assert.equal(await app.page.locator("[data-testid='momx-scanner-view']").count(), 1, "still on MomX");
    assert.equal(await app.page.locator(CHARTS_NAV).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the link colour is remembered and the ticker button names it", async () => {
  // The link key is NOT seeded: the reload must read what the page saved.
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const picker = app.page.locator("[data-testid='momx-link-picker'][aria-label='Chart link colour for this scanner']");
    assert.equal(await picker.inputValue(), "1");
    await picker.selectOption("2");
    await app.page.waitForFunction(() => document.querySelector("tr.momx-row [data-testid='momx-symbol-link']")?.title.endsWith("on the Yellow chart(s)"));
    await app.page.waitForFunction(() => window.localStorage.getItem("momx.scanner.linkGroup") === "2");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    assert.equal(await app.page.locator("[data-testid='momx-link-picker']").inputValue(), "2");
    assert.equal(await link(app.page, "NVDA").getAttribute("title"), "Open NVDA on the Yellow chart(s)");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a ticker clicked in the detached scanner window opens the chart in the main window", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const detached = await app.context.newPage();
    const detachedErrors = [];
    detached.on("pageerror", (error) => detachedErrors.push(String(error).slice(0, 300)));
    await detached.goto(new URL("/?popout=momx&list=Mag7", BASE_URL).href, { waitUntil: "domcontentloaded", timeout: 60_000 });
    await detached.locator("[data-testid='momx-popout-root'] tr.momx-row").first().waitFor({ timeout: 60_000 });
    await link(detached, "NVDA").click();
    await app.page.locator(CHARTS_NAV).waitFor({ timeout: 30_000 });
    // The detached window itself never navigates.
    assert.equal(await detached.locator("[data-testid='momx-popout-root']").count(), 1);
    assert.deepEqual(detachedErrors, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the lightning badge opens one card per ticker; dragging moves it and Escape closes it", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: cardReads() }, async (app) => {
    await boardReady(app.page);
    const badge = app.page.locator("button.momx-badge.is-fires[aria-label='Open the NVDA card']");
    await badge.click();
    const card = app.page.locator(".momx-popout-window[role='dialog']");
    await card.waitFor({ timeout: 10_000 });
    assert.equal((await card.locator(".momx-popout-titlebar b").innerText()).trim(), "NVDA");
    await card.locator(".momx-ticker-card").first().waitFor({ timeout: 10_000 });
    await card.locator(".momx-daychart").waitFor({ timeout: 10_000 });
    await badge.click();                                 // again: raised, not duplicated
    assert.equal(await card.count(), 1);
    const before = await card.boundingBox();
    const bar = await card.locator(".momx-popout-titlebar b").boundingBox();
    await app.page.mouse.move(bar.x + 5, bar.y + 5);
    await app.page.mouse.down();
    await app.page.mouse.move(bar.x - 115, bar.y + 45, { steps: 6 });
    await app.page.mouse.up();
    const after = await card.boundingBox();
    assert.ok(Math.abs(after.x - (before.x - 120)) <= 2 && Math.abs(after.y - (before.y + 40)) <= 2,
      `card moved from ${JSON.stringify(before)} to ${JSON.stringify(after)}`);
    await app.page.keyboard.press("Escape");
    await card.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Pop-up cards switch is remembered after a reload", async () => {
  // The switch's key is NOT seeded here (BOARD_STORAGE turns it off).
  await withApp({ view: VIEW, storage: { "momx.scanner.activeList": "Mag7" }, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const toggle = app.page.locator("label.momx-toggle", { hasText: "Pop-up cards" }).locator("input");
    assert.equal(await toggle.isChecked(), true, "on by default");
    await toggle.uncheck();
    await app.page.waitForFunction(() => window.localStorage.getItem("momx-auto-cards-on") === "0");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    assert.equal(await app.page.locator("label.momx-toggle", { hasText: "Pop-up cards" }).locator("input").isChecked(), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone a card fills the screen and its close button is reachable", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: cardReads() }, async (app) => {
    await boardReady(app.page);
    const badge = app.page.locator("button.momx-badge.is-fires[aria-label='Open the NVDA card']");
    await badge.scrollIntoViewIfNeeded();
    await badge.tap();
    const card = app.page.locator(".momx-popout-window[role='dialog']");
    await card.waitFor({ timeout: 10_000 });
    const box = await card.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `card at ${JSON.stringify(box)}`);
    const close = card.locator(".momx-popout-close");
    const closeBox = await close.boundingBox();
    assert.ok(closeBox && closeBox.x + closeBox.width <= PHONE.width && closeBox.y >= 0, `close at ${JSON.stringify(closeBox)}`);
    assert.equal(await overflowsSideways(app.page), false);
    await close.tap();
    await card.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
