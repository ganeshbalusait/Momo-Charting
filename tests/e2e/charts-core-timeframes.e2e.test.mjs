// Charts & OI: the eleven timeframes (2026-11-04 coverage). Every button must
// land on its own timeframe with real candles, the 4H/D/W/M seeds must never
// strand the panel, the choice must survive a reload, and the phone uses the
// native select instead of the button row.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways } from "./harness.mjs";
import { card, cardData, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const TIMEFRAMES = ["3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "D", "W", "M"];
const buttonFor = (page, key) => card(page)
  .locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button', { hasText: new RegExp(`^${key}$`) });

e2eTest("every timeframe button draws its own candles, and 5m -> 4h -> M -> 5m never strands the panel", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    const labels = await card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button').allInnerTexts();
    assert.deepEqual(labels.map((label) => label.trim()), TIMEFRAMES);
    const lengths = {};
    for (const key of [...TIMEFRAMES.filter((key) => key !== "5m"), "4h", "M", "5m"]) {
      await buttonFor(page, key).click();
      await waitForTape(page, { symbol: "AAPL", timeframe: key, timeout: 90_000 });
      assert.match(await buttonFor(page, key).getAttribute("class"), /\bis-active\b/, key);
      assert.match(await card(page).locator(".oi-finder-chart-ohlc b").innerText(), new RegExp(`^AAPL · ${key}$`));
      lengths[key] = Number((await cardData(page)).tapeLength);
    }
    // Coarser views must not collapse to a stub (the 4H/D/W/M seed path).
    for (const key of ["4h", "D", "W", "M"]) assert.ok(lengths[key] >= 12, `${key} drew only ${lengths[key]} candles`);
    assert.equal(await page.locator("section.oi-chart-render-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 600_000 });

e2eTest("the panel's timeframe survives a reload", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    await buttonFor(page, "15m").click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
    await page.waitForFunction(() => {
      try {
        return JSON.parse(localStorage.getItem("oiFinderChartWorkspace") || "{}").panels?.[0]?.timeframe === "15m";
      } catch { return false; }
    }, null, { timeout: 15_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("on a phone the timeframe is a native select and the button row is hidden", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    const select = card(page).locator('select.oi-finder-timeframe-select[aria-label="Chart timeframe"]');
    await select.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"]').isVisible(), false);
    assert.deepEqual(
      (await select.locator("option").allInnerTexts()).map((label) => label.trim()),
      TIMEFRAMES,
    );
    await select.selectOption("15m");
    await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
    assert.equal(await select.inputValue(), "15m");
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
