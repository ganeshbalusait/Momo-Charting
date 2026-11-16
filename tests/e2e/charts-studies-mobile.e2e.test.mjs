// Charts & OI on the phone (375x812, touch), end to end (2026-11-04 coverage
// pass, feature charts-mobile): the Chart/Chain section tabs, no sideways
// scrolling, the in-card quick tickers and native timeframe select, and the
// small first paint (no deep-history pull on its own). Chromium's phone emulation
// cannot reproduce iPhone Safari quirks; that limit is reported with the suite.
import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors } from "./charts-studies-helpers.mjs";

const TABS = 'nav.mobile-terminal-action-bar[data-testid="mobile-chart-section-tabs"][aria-label="Chart sections"]';
const SHELL = '[data-testid="app-shell"]';
const phone = (extra = {}) => chartAppOptions({ viewport: PHONE, ...extra });

// Phone navigation is the fixed bottom bar (f3d4c8ff, 2026-09-27): at <=760px
// index.css hides the older in-page Chart/Chain strip with display:none
// !important ("The fixed five-item menu below owns phone navigation"), and the
// bar's Options button always opens the full Quick Options workspace
// (openMobileOptions in App.jsx). So on a phone the chart page shows the chart
// alone, Options swaps it for Quick Options, and Chart brings the chart back.
const BAR = 'nav.mobile-chart-bottom-nav[aria-label="Mobile primary navigation"]';

e2eTest("on a phone the bottom bar's Chart and Options show one section at a time, with no sideways scroll", async () => {
  await withApp(phone(), async (app) => {
    await chartCard(app.page, "AAPL");
    const bar = app.page.locator(BAR);
    await bar.waitFor({ state: "visible", timeout: 30_000 });
    const chartTab = bar.locator("button", { hasText: "Chart" });
    const optionsTab = bar.locator('[data-testid="mobile-primary-options"]');
    assert.match(await chartTab.getAttribute("class"), /\bis-active\b/);
    assert.equal(await chartTab.getAttribute("aria-current"), "page");
    assert.match(await app.page.locator(SHELL).getAttribute("class"), /\bis-chart-tab\b/);
    // The older strip is still rendered (wider screens use it) but never shown on a phone.
    await app.page.locator(TABS).waitFor({ state: "attached", timeout: 10_000 });
    assert.equal(await app.page.locator(TABS).isVisible(), false, "the old Chart/Chain strip doubles the bottom bar");
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").isVisible(), true);
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-tos-chain").first().isVisible().catch(() => false), false,
      "the chain is showing on the Chart tab");
    assert.equal(await overflowsSideways(app.page), false, "the chart tab scrolls sideways");

    await optionsTab.click();
    await app.page.locator('[data-testid="quick-options-view"]').waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await optionsTab.getAttribute("aria-current"), "page");
    assert.doesNotMatch(await app.page.locator(SHELL).getAttribute("class"), /\bis-chart-tab\b/);
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").isVisible().catch(() => false), false,
      "the chart is still showing on the Options tab");
    assert.equal(await overflowsSideways(app.page), false, "the Options tab scrolls sideways");

    await chartTab.click();
    await app.page.locator(`${SHELL}.is-chart-tab`).waitFor({ timeout: 15_000 });
    await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await chartTab.getAttribute("aria-current"), "page");
    // Back on the chart, the AAPL tape is there again.
    await chartCard(app.page, "AAPL");
    assert.equal(await overflowsSideways(app.page), false);
    noPageErrors(app);
  });
});

e2eTest("the in-card quick tickers switch the ticker and the timeframe select switches the timeframe", async () => {
  await withApp(phone(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const quick = card.locator('.oi-finder-chart-quick-row[aria-label="Quick tickers"]');
    await quick.waitFor({ state: "visible", timeout: 30_000 });
    assert.match(await quick.locator("button", { hasText: /^AAPL(?![A-Z])/ }).getAttribute("class"), /\bis-active\b/);
    await quick.locator("button", { hasText: /^SPY(?![A-Z])/ }).click();
    const spy = await chartCard(app.page, "SPY");
    const select = spy.locator('select.oi-finder-timeframe-select[aria-label="Chart timeframe"]');
    await select.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await select.inputValue(), "5m");
    await select.selectOption("15m");
    await chartCard(app.page, "SPY", { timeframe: "15m" });
    assert.equal(await overflowsSideways(app.page), false);
    noPageErrors(app);
  });
});

e2eTest("the in-card quick tickers show each ticker's day move, with no FAV TICKERS label", async () => {
  // 2026-11-11: the phone row was symbol-only. Quotes are faked so the numbers
  // are known; a ticker with no reported move must show no badge, not 0.00%.
  const rows = [
    { symbol: "SPY", changePercent: 0.97 },
    { symbol: "NFLX", changePercent: -0.34 },
    { symbol: "USO", changePercent: null },
  ];
  await withApp(phone({ fakeReads: { "GET /api/live-chart-quotes": { json: { rows } } } }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const quick = card.locator('.oi-finder-chart-quick-row[aria-label="Quick tickers"]');
    await quick.waitFor({ state: "visible", timeout: 30_000 });
    const spy = quick.locator("button", { hasText: /^SPY(?![A-Z])/ });
    await spy.locator("em.ticker-day-move.is-up", { hasText: "+0.97%" }).waitFor({ timeout: 30_000 });
    assert.equal((await quick.locator("button", { hasText: /^NFLX/ }).locator("em.ticker-day-move.is-down").innerText()).trim(), "-0.34%");
    assert.equal(await quick.locator("button", { hasText: /^USO/ }).locator("em.ticker-day-move").count(), 0);
    assert.equal(await app.page.getByText("FAV TICKERS", { exact: true }).count(), 0);
    assert.equal(await overflowsSideways(app.page), false);
    noPageErrors(app);
  });
});

e2eTest("the phone's first paint does not pull the deep history by itself", async () => {
  await withApp(phone(), async (app) => {
    await chartCard(app.page, "AAPL");
    await app.page.waitForTimeout(20_000);
    const chartReads = app.reads.filter((read) => read.path === "/api/oi-finder-chart");
    assert.ok(chartReads.some((read) => /initial=true/.test(read.search)), "the first paint is the slim initial=true read");
    const deep = chartReads.filter((read) => /deep=true/.test(read.search));
    assert.deepEqual(deep, [], "the phone pulled the multi-MB deep tape without the trader panning back");
    noPageErrors(app);
  });
});

e2eTest("the chart controls fit the phone screen", async () => {
  await withApp(phone(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    for (const selector of [
      'select.oi-finder-timeframe-select[aria-label="Chart timeframe"]',
      "button.oi-finder-mobile-draw-toggle",
      'button.oi-finder-chart-alert[aria-label="Create a price alert for AAPL"]',
      'details.oi-finder-indicators-menu > summary[aria-label="Add or remove chart indicators"]',
    ]) {
      const control = card.locator(selector).first();
      await control.waitFor({ state: "visible", timeout: 30_000 });
      const box = await control.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    noPageErrors(app);
  });
});
