// Charts & OI -> drawing tools, end to end (2026-11-04 coverage pass, feature
// charts-drawings). Drawings live in this browser only (oiFinderChartDrawingsV1),
// per ticker + timeframe, so nothing here reaches the server. A horizontal line is
// one click, which keeps the spec independent of drag/magnet timing; the click lands
// in the middle of the price pane of the live AAPL chart. Every spec ends back on
// the crosshair tool so the chart is not left in drawing mode.
import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors, readStorage } from "./charts-studies-helpers.mjs";

const STORE = "oiFinderChartDrawingsV1";

function tools(card) {
  const bar = card.locator('aside.oi-chart-drawing-toolbar[aria-label="Chart drawing tools"]');
  return {
    bar,
    button: (label) => bar.locator(`button[aria-label="${label}"]`),
  };
}

async function showTools(card) {
  const { bar, button } = tools(card);
  await bar.waitFor({ state: "visible", timeout: 30_000 });
  if (await bar.evaluate((element) => element.classList.contains("is-collapsed"))) {
    await bar.locator('button.oi-chart-drawing-toolbar-toggle[aria-label="Show drawing tools"]').click();
  }
  await button("Horizontal line").waitFor({ state: "visible", timeout: 10_000 });
  return tools(card);
}

async function clickPricePane(page, card) {
  const layer = card.locator('svg.oi-chart-drawing-layer[aria-label="Chart drawings"]');
  const box = await layer.boundingBox();
  assert.ok(box && box.width > 50 && box.height > 50, `drawing layer is ${JSON.stringify(box)}`);
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.4);
}

const drawings = (card) => card.locator("svg.oi-chart-drawing-layer .oi-chart-drawing");
const savedFor = async (page, scope) => (JSON.parse((await readStorage(page, STORE)) || "{}")[scope] || []);

async function drawHorizontalLine(app, card) {
  const { button } = await showTools(card);
  await button("Horizontal line").click();
  assert.equal(await button("Horizontal line").getAttribute("aria-pressed"), "true");
  await clickPricePane(app.page, card);
  await drawings(card).first().waitFor({ state: "attached", timeout: 15_000 });
  return button;
}

e2eTest("a horizontal line is drawn, undone, redone, hidden, shown and deleted", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    const { button } = await showTools(card);
    assert.equal(await button("Delete selected drawing").isDisabled(), true, "Delete must wait for a selection");
    assert.equal(await button("Lock selected drawing").isDisabled(), true, "Lock must wait for a selection");
    assert.equal(await button("Undo drawing").isDisabled(), true);

    await drawHorizontalLine(app, card);
    assert.equal(await drawings(card).count(), 1);
    const saved = await savedFor(app.page, "AAPL:5m");
    assert.deepEqual(saved.map(({ type }) => type), ["horizontal"]);
    // The new line is selected, so Lock and Delete come alive.
    assert.equal(await button("Delete selected drawing").isEnabled(), true);
    assert.equal(await button("Lock selected drawing").isEnabled(), true);

    await button("Delete selected drawing").click();
    await drawings(card).first().waitFor({ state: "detached", timeout: 10_000 });
    assert.deepEqual(await savedFor(app.page, "AAPL:5m"), []);
    assert.equal(await button("Delete selected drawing").isDisabled(), true, "nothing is selected after a delete");

    await button("Undo drawing").click();                       // undo the delete
    await drawings(card).first().waitFor({ state: "attached", timeout: 10_000 });
    assert.equal((await savedFor(app.page, "AAPL:5m"))[0].id, saved[0].id);
    await button("Undo drawing").click();                       // undo the line itself
    await drawings(card).first().waitFor({ state: "detached", timeout: 10_000 });
    assert.deepEqual(await savedFor(app.page, "AAPL:5m"), []);
    await button("Redo drawing").click();                       // and back
    await drawings(card).first().waitFor({ state: "attached", timeout: 10_000 });
    assert.equal((await savedFor(app.page, "AAPL:5m")).length, 1);

    await button("Hide drawings").click();
    await card.locator("svg.oi-chart-drawing-layer.is-hidden").waitFor({ timeout: 10_000 });
    assert.equal(await drawings(card).count(), 0);
    assert.equal((await savedFor(app.page, "AAPL:5m")).length, 1, "hiding must not delete");
    await button("Show drawings").click();
    await drawings(card).first().waitFor({ state: "attached", timeout: 10_000 });
    assert.equal(await card.locator("svg.oi-chart-drawing-layer.is-hidden").count(), 0);

    await button("Crosshair and chart pan").click();
    assert.equal(await button("Crosshair and chart pan").getAttribute("aria-pressed"), "true");
    assert.equal(app.writes.filter((write) => /drawing/i.test(write.path)).length, 0, "drawings must never reach the server");
    noPageErrors(app);
  });
});

e2eTest("a drawing belongs to its ticker and timeframe and survives a reload", async () => {
  await withApp(chartAppOptions(), async (app) => {
    let card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    await drawHorizontalLine(app, card);
    await tools(card).button("Crosshair and chart pan").click();

    await card.locator(".oi-finder-timeframes button", { hasText: /^15m$/ }).click();
    card = await chartCard(app.page, "AAPL", { timeframe: "15m" });
    await app.page.waitForTimeout(1_500);
    assert.equal(await drawings(card).count(), 0, "the 5m line leaked onto the 15m chart");

    await card.locator(".oi-finder-timeframes button", { hasText: /^5m$/ }).click();
    card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    await drawings(card).first().waitFor({ state: "attached", timeout: 15_000 });

    await app.page.reload({ waitUntil: "domcontentloaded" });
    card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    await drawings(card).first().waitFor({ state: "attached", timeout: 30_000 });
    assert.equal((await savedFor(app.page, "AAPL:5m")).length, 1);
    assert.deepEqual(await savedFor(app.page, "AAPL:15m"), []);

    // A second ticker starts clean.
    await app.page.locator('nav.oi-chart-quick-tickers[aria-label="Quick-access OI Finder tickers"] button', { hasText: /^SPY/ }).first().click();
    const spy = await chartCard(app.page, "SPY");
    await app.page.waitForTimeout(1_500);
    assert.equal(await drawings(spy).count(), 0, "an AAPL drawing showed on SPY");
    noPageErrors(app);
  });
});

e2eTest("on a phone the Draw button shows and hides the drawing tools", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const draw = card.locator("button.oi-finder-mobile-draw-toggle");
    await draw.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await draw.getAttribute("aria-pressed"), "false");
    await draw.click();
    assert.equal(await draw.getAttribute("aria-pressed"), "true");
    await card.locator("aside.oi-chart-drawing-toolbar:not(.is-collapsed)").waitFor({ timeout: 10_000 });
    await draw.click();
    assert.equal(await draw.getAttribute("aria-pressed"), "false");
    await card.locator("aside.oi-chart-drawing-toolbar.is-collapsed").waitFor({ state: "attached", timeout: 10_000 });
    noPageErrors(app);
  });
});
