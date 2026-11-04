// Charts & OI -> price alerts from the chart, end to end (2026-11-04 coverage
// pass, feature charts-price-alert-from-chart). The chart bell and the toolbar
// bell open the Alert Center editor prefilled with the chart's ticker and
// "at or above"; alerts live in this browser only (oiFinderPriceAlerts), so a
// created alert never reaches the server. The alert price used is far above any
// real AAPL price, so it can never fire. The level lines on the chart are
// canvas-drawn and are not asserted here.
import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors, readStorage } from "./charts-studies-helpers.mjs";

const EDITOR = ".global-price-alert-popover";

async function editorFields(page) {
  const editor = page.locator(EDITOR).first();
  await editor.waitFor({ state: "visible", timeout: 15_000 });
  const form = editor.locator("form").first();
  return {
    editor,
    ticker: form.locator('input[placeholder="AAPL"]'),
    condition: form.locator("select").first(),
    price: form.locator('input[aria-label="Price alert value"]'),
    submit: form.locator('button[type="submit"]', { hasText: "Create alert" }),
  };
}

e2eTest("the chart bell opens the alert editor prefilled with the chart's ticker and 'above'", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await card.locator('button.oi-finder-chart-alert[aria-label="Create a price alert for AAPL"]').click();
    const fields = await editorFields(app.page);
    assert.equal(await fields.ticker.inputValue(), "AAPL");
    assert.equal(await fields.condition.inputValue(), "above");
    assert.equal(await fields.price.inputValue(), "", "the plain bell has no price to prefill");
    noPageErrors(app);
  });
});

e2eTest("the toolbar bell follows the active chart's ticker", async () => {
  await withApp(chartAppOptions(), async (app) => {
    await chartCard(app.page, "AAPL");
    await app.page.locator('nav.oi-chart-quick-tickers[aria-label="Quick-access OI Finder tickers"] button', { hasText: /^SPY/ }).first().click();
    await chartCard(app.page, "SPY");
    const bell = app.page.locator('button.oi-chart-workspace-toggle.oi-chart-workspace-alerts[aria-label="Open price alerts for SPY"]');
    await bell.waitFor({ state: "visible", timeout: 30_000 });
    await bell.click();
    const fields = await editorFields(app.page);
    assert.equal(await fields.ticker.inputValue(), "SPY");
    assert.equal(await fields.condition.inputValue(), "above");
    noPageErrors(app);
  });
});

e2eTest("creating an alert from the chart keeps it in this browser and lists it", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await card.locator('button.oi-finder-chart-alert[aria-label="Create a price alert for AAPL"]').click();
    const fields = await editorFields(app.page);
    await fields.submit.click();
    await fields.editor.getByText("Enter a ticker and valid alert price.").waitFor({ timeout: 5_000 });
    await fields.price.fill("99999");
    await fields.submit.click();
    const listed = fields.editor.locator(".oi-finder-price-alert-list article strong", { hasText: /AAPL · ≥ .*99,999/ });
    await listed.first().waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(await listed.count(), 1);
    // Auto Alert levels are mirrored into the same store; pick out the one made here.
    const saved = JSON.parse(await readStorage(app.page, "oiFinderPriceAlerts")).filter((alert) => Number(alert.price) === 99999);
    assert.equal(saved.length, 1);
    assert.deepEqual(
      { symbol: saved[0].symbol, price: saved[0].price, condition: saved[0].condition, status: saved[0].status, enabled: saved[0].enabled },
      { symbol: "AAPL", price: 99999, condition: "above", status: "active", enabled: true },
    );
    assert.equal(app.writes.filter((write) => /alert/i.test(write.path)).length, 0, "a chart price alert must not be sent to the server");
    noPageErrors(app);
  });
});

e2eTest("on a phone the chart bell still opens the editor, inside the screen", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const bell = card.locator('button.oi-finder-chart-alert[aria-label="Create a price alert for AAPL"]');
    await bell.waitFor({ state: "visible", timeout: 30_000 });
    await bell.click();
    const fields = await editorFields(app.page);
    assert.equal(await fields.ticker.inputValue(), "AAPL");
    const box = await fields.price.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `price field at ${JSON.stringify(box)} is cut off`);
    noPageErrors(app);
  });
});
