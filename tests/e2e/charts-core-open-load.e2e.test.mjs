// Charts & OI: opening a chart (2026-11-04 coverage). First paint from the slim
// payload, the deep tape pulled once when a timeframe needs it, cheap since=
// reconciles after that, and a readable message - never a blank card - when
// the chart server fails. Live data: structure is asserted, never prices.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways } from "./harness.mjs";
import { CARD, card, cardData, chartReads, waitForTape, withCharts } from "./charts-core.helpers.mjs";

async function assertPainted(app, symbol) {
  const { page } = app;
  await waitForTape(page, { symbol });
  const panel = card(page);
  await panel.locator(".oi-finder-candle-chart .oi-finder-chart-canvas canvas").first().waitFor({ state: "attached", timeout: 30_000 });
  await panel.locator('.oi-finder-chart-live-dot[data-live="1"]').waitFor({ state: "attached", timeout: 30_000 });
  const data = await cardData(page);
  assert.equal(data.chartSymbol, symbol);
  assert.equal(data.chartOwner, symbol, "the tape on screen belongs to the ticker asked for");
  assert.ok(Number(data.tapeFirst) > 0 && Number(data.tapeLast) >= Number(data.tapeFirst), JSON.stringify(data));
  assert.equal(await page.locator("section.oi-chart-render-error").count(), 0, "chart render boundary tripped");
  assert.equal(await panel.locator(".oi-finder-chart-empty").count(), 0);
  await panel.locator(".oi-finder-chart-ohlc b").waitFor({ timeout: 30_000 });
  assert.match(await panel.locator(".oi-finder-chart-ohlc b").innerText(), new RegExp(`^${symbol} · 5m$`));
}

e2eTest("a single AAPL chart paints candles, its canvas and the live dot", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    await app.page.locator('[data-testid="charts-and-oi-view"]').first().waitFor({ timeout: 60_000 });
    await assertPainted(app, "AAPL");
    // The first chart request is the slim first paint, never the whole tape.
    const first = chartReads(app, "AAPL").find((query) => !query.historyStatus && !query.prefetch);
    assert.ok(first, "no chart request was made");
    assert.equal(first.initial, "true");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the chart paints and nothing scrolls sideways", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["AAPL"] } }, async (app) => {
    await waitForTape(app.page, { symbol: "AAPL" });
    await card(app.page).locator('.oi-finder-chart-live-dot[data-live="1"]').waitFor({ state: "attached", timeout: 30_000 });
    assert.equal(await app.page.locator("section.oi-chart-render-error").count(), 0);
    assert.equal(await overflowsSideways(app.page), false, "the chart page scrolls sideways on a phone");
    const box = await card(app.page).boundingBox();
    assert.ok(box && box.x >= -1 && box.x + box.width <= PHONE.width + 1, `card at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the deep tape is pulled once for D, then the 30s reconcile asks only for the tail", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    // AAPL is a quick ticker: its deep history waits until a view needs it.
    assert.equal((await cardData(page)).historyOnDemand, "true");
    await card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button', { hasText: /^D$/ }).click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "D" });
    const deadline = Date.now() + 90_000;
    while (!chartReads(app, "AAPL").some((query) => Number(query.since) > 0) && Date.now() < deadline) {
      await page.waitForTimeout(1_000);
    }
    const reads = chartReads(app, "AAPL");
    const deep = reads.filter((query) => query.deep === "true");
    assert.equal(deep.length, 1, `deep pulls: ${JSON.stringify(reads)}`);
    const deepAt = reads.findIndex((query) => query.deep === "true");
    assert.ok(reads.slice(deepAt + 1).some((query) => Number(query.since) > 0), "no since= reconcile after the deep pull");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a failing chart server shows a readable message in the card, then recovers by itself", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const failing = /\/api\/oi-finder-chart\?(?=.*symbol=NVDA)/;
    await page.route(failing, (route) => route.fulfill({
      status: 502, contentType: "application/json", body: JSON.stringify({ error: "Bad gateway (end-to-end test)" }),
    }));
    const input = card(page).locator('input[aria-label="Chart ticker"]');
    await input.fill("NVDA");
    await input.press("Enter");
    const empty = card(page).locator(".oi-finder-chart-empty");
    await page.waitForFunction((selector) => {
      const node = document.querySelector(`${selector} .oi-finder-chart-empty`);
      return node && /API is unavailable right now \(server error 502\)\. Retrying automatically\./.test(node.textContent || "");
    }, CARD, { timeout: 60_000 });
    assert.ok((await empty.innerText()).trim().length > 10, "the card must explain itself, not go blank");
    assert.equal(await page.locator("section.oi-chart-render-error").count(), 0);
    await page.unroute(failing);
    await waitForTape(page, { symbol: "NVDA", timeout: 120_000 });
    assert.equal(await empty.count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
