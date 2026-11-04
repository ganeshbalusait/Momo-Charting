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

e2eTest("the Chart and Chain tabs show one section at a time, with no sideways scroll", async () => {
  await withApp(phone(), async (app) => {
    await chartCard(app.page, "AAPL");
    const tabs = app.page.locator(TABS);
    await tabs.waitFor({ state: "visible", timeout: 30_000 });
    const chartTab = tabs.locator("button", { hasText: "Chart" });
    const chainTab = tabs.locator("button", { hasText: "Chain" });
    assert.match(await chartTab.getAttribute("class"), /\bis-active\b/);
    assert.match(await app.page.locator(SHELL).getAttribute("class"), /\bis-chart-tab\b/);
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").isVisible(), true);
    assert.equal(await overflowsSideways(app.page), false, "the chart tab scrolls sideways");

    await chainTab.click();
    await app.page.locator(`${SHELL}.is-options-tab`).waitFor({ timeout: 10_000 });
    assert.match(await chainTab.getAttribute("class"), /\bis-active\b/);
    await app.page.locator(".charts-oi-page-full .charts-oi-tos-chain").first().waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").isVisible(), false, "the chart is still showing on the Chain tab");
    assert.equal(await overflowsSideways(app.page), false, "the chain tab scrolls sideways");

    await chartTab.click();
    await app.page.locator(`${SHELL}.is-chart-tab`).waitFor({ timeout: 10_000 });
    await app.page.locator(".charts-oi-page-full .charts-oi-full-chart").waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(await app.page.locator(".charts-oi-page-full .charts-oi-tos-chain").first().isVisible().catch(() => false), false,
      "the chain is still showing on the Chart tab");
    // Back on the chart, the same AAPL tape is still there (hidden, never unmounted).
    await chartCard(app.page, "AAPL");
    assert.equal(await tabs.isVisible(), true);
    noPageErrors(app);
  });
});

e2eTest("the in-card quick tickers switch the ticker and the timeframe select switches the timeframe", async () => {
  await withApp(phone(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const quick = card.locator('.oi-finder-chart-quick-row[aria-label="Quick tickers"]');
    await quick.waitFor({ state: "visible", timeout: 30_000 });
    assert.match(await quick.locator("button", { hasText: /^AAPL$/ }).getAttribute("class"), /\bis-active\b/);
    await quick.locator("button", { hasText: /^SPY$/ }).click();
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
