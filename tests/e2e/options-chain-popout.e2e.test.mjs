// Detached option-chain and chart windows (?popout=chain / ?popout=chart,
// 2026-11-04 coverage). The app no longer opens these itself, but the URLs
// still render for anyone holding the link. The chain read is the options
// fixture (spot $100); the chart pop-out loads live AAPL candles.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { CHAIN_READS } from "./options-fixtures.mjs";

const CHAIN_POPOUT = '[data-testid="chain-popout-view"]';
const POPOUT_CHAIN = ".charts-oi-tos-chain.is-popout-chain";

e2eTest("the chain pop-out renders SPY's chain on its own, titled for the window", async () => {
  await withApp({ path: "/?popout=chain&symbol=spy", fakeReads: CHAIN_READS }, async (app) => {
    await app.page.locator('[data-testid="chain-popout-root"]').waitFor({ timeout: 60_000 });
    const view = app.page.locator(CHAIN_POPOUT);
    await view.locator(`${POPOUT_CHAIN} > header`, { hasText: "Spot $100" }).waitFor({ timeout: 60_000 });
    assert.equal(await view.locator(`${POPOUT_CHAIN} > header b`).innerText(), "SPY LIVE OPTION CHAIN");
    assert.equal(await view.locator(".charts-oi-popout-header span").first().innerText(), "DETACHED LIVE OPTION CHAIN");
    assert.equal(await app.page.title(), "SPY Option Chain · Agentic Trading");
    assert.equal(await app.page.locator('[data-testid="primary-navigation"]').count(), 0, "no nav rail in a detached window");
    assert.match(await view.locator(".charts-oi-popout-live").innerText(), /LIVE DATA/);
    // Pop-outs always read the compact chain feed.
    assert.ok(app.reads.some((read) => read.path === "/api/oi-finder-chain"));
    assert.ok(!app.reads.some((read) => read.path === "/api/oi-finder"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("both pop-out tabs render: High OI board and the expiry chain", async () => {
  await withApp({ path: "/?popout=chain&symbol=SPY", fakeReads: CHAIN_READS }, async (app) => {
    const chain = app.page.locator(`${CHAIN_POPOUT} ${POPOUT_CHAIN}`);
    await chain.locator(".high-oi-panel tr.high-oi-em.is-spot").waitFor({ timeout: 60_000 });
    await chain.locator('.charts-oi-chain-view-tabs [role="tab"]', { hasText: /^Chain$/ }).click();
    await chain.locator(".charts-oi-expiry-accordion .charts-oi-expiry-item.is-open tr.is-atm").waitFor({ timeout: 30_000 });
    assert.equal(await chain.locator(".high-oi-panel").count(), 0);
    await chain.locator('.charts-oi-chain-view-tabs [role="tab"]', { hasText: "High OI" }).click();
    await chain.locator(".high-oi-panel").waitFor({ timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the detached chart's Option chain toggle hides and shows its side chain", async () => {
  await withApp({ path: "/?popout=chart&symbol=AAPL&timeframe=5m", fakeReads: CHAIN_READS }, async (app) => {
    const view = app.page.locator('[data-testid="chart-popout-view"]');
    await view.waitFor({ timeout: 60_000 });
    assert.equal(await view.locator(".charts-oi-popout-header b").innerText(), "AAPL · 5m");
    assert.equal(await app.page.title(), "AAPL Live Chart · Agentic Trading");
    await view.locator(`${POPOUT_CHAIN} > header`, { hasText: "AAPL LIVE OPTION CHAIN" }).waitFor({ timeout: 60_000 });
    assert.equal(await view.locator('.charts-oi-resize-handle[aria-label="Resize detached chart and option chain"]').count(), 1);
    await view.locator(".charts-oi-popout-header button", { hasText: "Hide options" }).click();
    await app.page.waitForFunction((selector) => !document.querySelector(selector), `[data-testid="chart-popout-view"] ${POPOUT_CHAIN}`);
    await view.locator(".charts-oi-popout-header button", { hasText: "Option chain" }).click();
    await view.locator(POPOUT_CHAIN).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("an unknown pop-out mode falls back to the normal app", async () => {
  await withApp({ path: "/?popout=bogus&symbol=SPY", fakeReads: CHAIN_READS }, async (app) => {
    await app.page.locator('[data-testid="app-shell"]').waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator('main.trading-popout-root').count(), 0);
    assert.equal(await app.page.locator('[data-testid="primary-navigation"]').count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});
