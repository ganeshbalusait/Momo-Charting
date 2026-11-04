// Charts & OI -> TOS study overlays, end to end (2026-11-04 coverage pass,
// feature charts-tos-study-overlays). Turning any study on must never break or
// freeze the chart (the study ladder exists because one did - "Page
// Unresponsive"). The overlays are canvas-drawn, so what the DOM can prove is:
// no render-error card, no page errors, a render cost the card reports in
// data-render-ms-last, a responsive page, and every settings panel opening.
// Visual parity with his TOS scripts still needs a TOS screenshot comparison;
// that limit is reported with the suite. Live AAPL 5m; profile saves are faked.
import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, closeIndicators, noPageErrors, openIndicators, setStudy, studyRow } from "./charts-studies-helpers.mjs";

const OVERLAYS = [
  "EMA 9", "EMA 21", "EMA 50", "SMA 200", "VWAP", "EMA Clouds", "Relative Volume Candles", "StochasX",
  "CloudMAX MTF EMA 1/5/15", "AutoFib — Single TF", "Ichimoku", "MTF ADX Clouds (Lower)", "PivotPoints",
  "WoodiesPivots", "PersonsPivots", "MTF MA Levels", "Session Time Lines", "Previous OHLC Levels",
  "Extended Sessions", "TOS candle colors",
];
const RENDER_BUDGET_MS = 2_500;

async function allStudyLabels(card) {
  await openIndicators(card);
  return card.locator("section.oi-finder-indicator-row > div:not(.oi-finder-indicator-settings) > label b").allInnerTexts();
}

async function assertChartHealthy(app, card, context) {
  assert.equal(await card.locator("section.oi-chart-render-error").count(), 0, `${context}: the chart crashed into its error card`);
  assert.deepEqual(app.pageErrors, [], `${context}: page errors\n${app.pageErrors.join("\n")}`);
  const last = Number(await card.getAttribute("data-render-ms-last") || 0);
  assert.ok(last < RENDER_BUDGET_MS, `${context}: one chart render took ${last} ms`);
}

async function responsiveWithin(app, card, budgetMs) {
  const zoom = card.locator('button.oi-finder-chart-control[aria-label="Zoom in"]');
  const started = Date.now();
  await zoom.click({ timeout: budgetMs });
  await app.page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve())));
  return Date.now() - started;
}

e2eTest("each TOS overlay can be switched on and off on its own without breaking the chart", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    const labels = await allStudyLabels(card);
    const cloudBands = labels.filter((label) => label.endsWith("Cloud Bands"));
    assert.ok(cloudBands.length >= 1, "the Cloud Bands studies are missing from the list");
    for (const label of OVERLAYS) assert.ok(labels.includes(label), `"${label}" is missing from the Indicators list`);
    for (const label of [...OVERLAYS, ...cloudBands]) {
      const box = studyRow(card, label).locator('input[type="checkbox"]').first();
      const before = await box.isChecked();
      await setStudy(card, label, !before);
      await app.page.waitForTimeout(1_200);
      await assertChartHealthy(app, card, `${label} ${before ? "off" : "on"}`);
      await setStudy(card, label, before);
      await app.page.waitForTimeout(400);
      await assertChartHealthy(app, card, `${label} restored`);
    }
    noPageErrors(app);
  });
}, { timeout: 420_000 });

e2eTest("with every overlay on, the chart stays responsive", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL", { timeframe: "5m" });
    const labels = await allStudyLabels(card);
    for (const label of [...OVERLAYS, ...labels.filter((name) => name.endsWith("Cloud Bands"))]) await setStudy(card, label, true);
    await closeIndicators(card);
    await card.locator(".oi-finder-adx-study-title").waitFor({ state: "visible", timeout: 60_000 });
    // Let the study ladder climb, then probe the main thread a few times.
    await app.page.waitForTimeout(10_000);
    const samples = [];
    for (let probe = 0; probe < 4; probe += 1) {
      samples.push(await responsiveWithin(app, card, 5_000));
      await app.page.waitForTimeout(2_000);
    }
    assert.ok(Math.max(...samples) < 1_500, `a Zoom-in click took ${Math.max(...samples)} ms with every overlay on (${samples.join(", ")})`);
    await assertChartHealthy(app, card, "all overlays on");
    assert.ok(Number(await card.getAttribute("data-tape-length")) > 0);
  });
}, { timeout: 300_000 });

e2eTest("every study's settings panel opens and closes without errors", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const labels = await allStudyLabels(card);
    assert.ok(labels.length >= 30, `only ${labels.length} studies listed`);
    for (const label of labels) {
      const row = studyRow(card, label);
      const toggle = row.locator(`button[aria-label="${label} settings"]`);
      await toggle.click();
      await row.locator(".oi-finder-indicator-settings").waitFor({ state: "visible", timeout: 5_000 });
      assert.match(await row.getAttribute("class"), /\bis-open\b/, label);
      await toggle.click();
      await row.locator(".oi-finder-indicator-settings").waitFor({ state: "detached", timeout: 5_000 });
    }
    noPageErrors(app);
  });
}, { timeout: 240_000 });

e2eTest("on a phone the Indicators sheet opens inside the screen and a study toggles", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const popover = await openIndicators(card);
    const box = await popover.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `Indicators sheet at ${JSON.stringify(box)} is cut off`);
    const box9 = studyRow(card, "EMA 9").locator('input[type="checkbox"]').first();
    const before = await box9.isChecked();
    await setStudy(card, "EMA 9", !before);
    await app.page.waitForTimeout(1_000);
    await assertChartHealthy(app, card, "EMA 9 on phone");
    await setStudy(card, "EMA 9", before);
    noPageErrors(app);
  });
});
