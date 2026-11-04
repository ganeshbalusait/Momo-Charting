// Charts & OI: big screen and single-chart full screen (2026-11-04 coverage).
// Both only re-lay the screen: they must not re-download the chart, must not
// move the time window the trader was reading (the "shaking" refits in
// memory), and the option-chain companion must hide and come back.

import assert from "node:assert/strict";

import { DESKTOP, e2eTest } from "./harness.mjs";
import { card, cardData, chartReads, thumbWidth, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const MULTI = 'section.oi-chart-multilayout[aria-label="TOS-style multi-chart layout"]';
const fullDownloads = (app) => chartReads(app, "AAPL").filter((query) => query.initial === "true" || query.deep === "true").length;

async function canvasBox(page, index = 0) {
  return card(page, index).locator(".oi-finder-chart-canvas").boundingBox();
}

e2eTest("big screen fills the app, its option chain hides and returns, and Exit restores the layout", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    const before = await canvasBox(page);
    await page.locator('button.oi-chart-workspace-toggle[aria-label="Open the big screen workspace"]').click();
    const workspace = page.locator(`${MULTI}.is-workspace-maximized`);
    await workspace.waitFor({ timeout: 10_000 });
    const box = await workspace.boundingBox();
    assert.ok(box.width >= DESKTOP.width * 0.95 && box.height >= DESKTOP.height * 0.85, `big screen is only ${JSON.stringify(box)}`);
    await page.waitForTimeout(500);
    const big = await canvasBox(page);
    assert.ok(big.width * big.height > before.width * before.height, "the chart did not grow on the big screen");

    const companion = page.locator('aside.oi-chart-workspace-companion[aria-label="Synchronized live option chain"]');
    await companion.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal(await page.locator(`${MULTI}.has-big-screen-companion`).count(), 1);
    await companion.getByRole("button", { name: "Hide option chain", exact: true }).click();
    await companion.waitFor({ state: "detached", timeout: 10_000 });
    await page.locator('button.oi-chart-workspace-toggle[aria-label="Show the option chain panel"]').click();
    await companion.waitFor({ state: "visible", timeout: 10_000 });

    await page.locator('button.oi-chart-workspace-toggle[aria-label="Exit the big screen workspace"]').click();
    await page.waitForFunction((selector) => !document.querySelector(selector), `${MULTI}.is-workspace-maximized`, { timeout: 10_000 });
    await page.waitForTimeout(500);
    const after = await canvasBox(page);
    assert.ok(Math.abs(after.width - before.width) <= 4 && Math.abs(after.height - before.height) <= 4,
      `layout not restored: ${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
    assert.equal(await page.locator(`${MULTI}.layout-two-columns`).count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("maximize opens the chart with its option chain and exits without refetching or moving the time window", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    await page.waitForTimeout(2_000);
    const downloads = fullDownloads(app);
    const before = await cardData(page, 0);
    const windowBefore = await thumbWidth(page, 0);

    await card(page, 0).getByRole("button", { name: "Maximize AAPL", exact: true }).click();
    const maximized = page.locator("section.oi-finder-chart-card.is-maximized.has-maximized-option-chain");
    await maximized.waitFor({ timeout: 10_000 });
    const chain = maximized.locator('aside.oi-finder-chart-maximized-chain[aria-label="Synchronized live option chain"]');
    await chain.waitFor({ state: "visible", timeout: 15_000 });
    await chain.getByRole("button", { name: "Hide option chain", exact: true }).click();
    await chain.waitFor({ state: "detached", timeout: 10_000 });
    await page.locator("section.oi-finder-chart-card.is-maximized").getByRole("button", { name: "Show option chain", exact: true }).click();
    await chain.waitFor({ state: "visible", timeout: 10_000 });

    await page.locator("section.oi-finder-chart-card.is-maximized").getByRole("button", { name: "Exit AAPL full-screen view", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector("section.oi-finder-chart-card.is-maximized"), null, { timeout: 10_000 });
    await page.waitForTimeout(800);

    const after = await cardData(page, 0);
    assert.equal(fullDownloads(app), downloads, "maximize/exit re-downloaded the chart");
    assert.equal(after.chartOwner, "AAPL");
    assert.ok(Number(after.tapeLength) >= Number(before.tapeLength), "the tape shrank across maximize");
    assert.ok(Math.abs((await thumbWidth(page, 0)) - windowBefore) <= 2, "the visible time window moved (refit)");
    assert.equal(await page.locator(`${MULTI}.layout-two-columns.charts-2`).count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("'Fill the screen with AAPL' maximizes the panel with its chain beside it", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 0, symbol: "AAPL" });
    await card(page, 0).getByRole("button", { name: "Fill the screen with AAPL", exact: true }).click();
    const maximized = page.locator("section.oi-finder-chart-card.is-maximized.has-maximized-option-chain");
    await maximized.waitFor({ timeout: 10_000 });
    const box = await maximized.boundingBox();
    assert.ok(box.width >= DESKTOP.width * 0.6, `full screen is only ${JSON.stringify(box)}`);
    await maximized.getByRole("button", { name: "Exit AAPL full-screen view", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector("section.oi-finder-chart-card.is-maximized"), null, { timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });
