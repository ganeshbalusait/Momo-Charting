// Charts & OI: the Indicators panel and the per-timeframe profile shared by
// every ticker and device (2026-11-04 coverage). Every study click saves the
// timeframe's profile; the POST is answered by the harness (fakeWrites) or
// refused (the harness's 503) so the trader's real profile is never touched.
// The server copy the app reads at boot is a fixture (no saved profiles,
// unless a test serves a newer one).

import assert from "node:assert/strict";

import { PHONE, e2eTest } from "./harness.mjs";
import {
  card, openIndicators, profilePosts, studyCheckbox, studyRow, waitForTape, withCharts,
} from "./charts-core.helpers.mjs";

const SAVE_REFUSED = { "POST /api/chart-indicator-profiles": { status: 503, json: { error: "Blocked by the end-to-end harness: writes are not sent." } } };

async function settle(page, ms = 600) {
  await page.waitForTimeout(ms);
}

e2eTest("ticking a study saves the 5m profile for every ticker in one POST", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    const vwap = studyCheckbox(popover, "VWAP");
    const was = await vwap.isChecked();
    const earlier = profilePosts(app).length;
    await vwap.click();
    await settle(page);
    assert.equal(await vwap.isChecked(), !was);
    const posts = profilePosts(app).slice(earlier);
    assert.equal(posts.length, 1, JSON.stringify(posts.map((post) => post.key)));
    assert.equal(posts[0].key, "5m");
    assert.equal(posts[0].profile.indicatorSettings.vwap, !was);
    assert.equal(typeof posts[0].profile.indicatorOptions, "object");
    assert.match(posts[0].profile.savedAt, /^\d{4}-\d{2}-\d{2}T/);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("'Save 5m for all tickers' says saved when the server takes it, and 'saved on this device only' when it refuses", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    await popover.getByRole("button", { name: "Save 5m for all tickers", exact: true }).click();
    const status = card(page).locator('span.oi-finder-visible-save-status[role="status"]');
    await status.waitFor({ timeout: 10_000 });
    await settle(page, 1_000);
    assert.equal((await status.innerText()).trim(), "saved");
    assert.equal(profilePosts(app).at(-1).key, "5m");
  });
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites: SAVE_REFUSED }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    await popover.getByRole("button", { name: "Save 5m for all tickers", exact: true }).click();
    await page.waitForFunction(() => /saved on this device only/.test(
      document.querySelector('span.oi-finder-visible-save-status[role="status"]')?.textContent || "",
    ), null, { timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("a study switched on AAPL stays switched when the panel moves to NVDA", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    let popover = await openIndicators(page);
    const was = await studyCheckbox(popover, "VWAP").isChecked();
    await studyCheckbox(popover, "VWAP").click();
    await settle(page);
    await popover.getByRole("button", { name: "Close indicators editor" }).click();
    const input = card(page).locator('input[aria-label="Chart ticker"]');
    await input.fill("NVDA");
    await input.press("Enter");
    await waitForTape(page, { symbol: "NVDA" });
    popover = await openIndicators(page);
    assert.equal(await studyCheckbox(popover, "VWAP").isChecked(), !was, "the 5m profile did not follow the ticker");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("in a 2-chart layout on the same timeframe, a click in chart 1 updates chart 2", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    const first = await openIndicators(page, 0);
    const was = await studyCheckbox(first, "VWAP").isChecked();
    await studyCheckbox(first, "VWAP").click();
    await settle(page);
    await first.getByRole("button", { name: "Close indicators editor" }).click();
    const second = await openIndicators(page, 1);
    assert.equal(await studyCheckbox(second, "VWAP").isChecked(), !was);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a newer server copy of the 5m profile (another device's save) replaces this device's", async () => {
  let served = { profiles: {} };
  const reads = { "GET /api/chart-indicator-profiles": () => ({ json: served }) };
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeReads: reads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    let popover = await openIndicators(page);
    const was = await studyCheckbox(popover, "VWAP").isChecked();
    const current = await page.evaluate(() => ({
      indicatorSettings: JSON.parse(localStorage.getItem("oiFinderChartIndicators") || "{}"),
      indicatorOptions: JSON.parse(localStorage.getItem("oiFinderChartIndicatorOptions") || "{}"),
    }));
    assert.ok(Object.keys(current.indicatorSettings).length > 5, "no indicator settings in this browser yet");
    served = { profiles: { "5m": {
      indicatorSettings: { ...current.indicatorSettings, vwap: !was },
      indicatorOptions: current.indicatorOptions,
      savedAt: "2099-01-01T00:00:00.000Z",
    } } };
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL" });
    popover = await openIndicators(page);
    await page.waitForFunction((want) => {
      const rows = [...document.querySelectorAll(".oi-finder-indicators-popover section.oi-finder-indicator-row")];
      const row = rows.find((node) => node.querySelector("label b")?.textContent === "VWAP");
      return row?.querySelector("label > input[type=checkbox]")?.checked === want;
    }, !was, { timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("'Reset 5m' puts a changed study back to its default and saves the reset", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    const defaultState = await studyCheckbox(popover, "VWAP").isChecked();
    await studyCheckbox(popover, "VWAP").click();
    await settle(page);
    assert.equal(await studyCheckbox(popover, "VWAP").isChecked(), !defaultState);
    await popover.getByRole("button", { name: "Reset 5m", exact: true }).click();
    await settle(page);
    assert.equal(await studyCheckbox(popover, "VWAP").isChecked(), defaultState);
    const last = profilePosts(app).at(-1);
    assert.equal(last.key, "5m");
    assert.equal(last.profile.indicatorSettings.vwap, defaultState);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("an EMA 9 length of 13 survives a reload", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    let popover = await openIndicators(page);
    await popover.getByRole("button", { name: "EMA 9 settings", exact: true }).click();
    const length = studyRow(popover, "EMA 9").locator(".oi-finder-indicator-settings label", { hasText: "Length" }).locator('input[type="number"]');
    await length.fill("13");
    await settle(page);
    assert.equal(profilePosts(app).at(-1).profile.indicatorOptions.ema9Period, 13);
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL" });
    popover = await openIndicators(page);
    await popover.getByRole("button", { name: "EMA 9 settings", exact: true }).click();
    assert.equal(await studyRow(popover, "EMA 9").locator(".oi-finder-indicator-settings label", { hasText: "Length" }).locator('input[type="number"]').inputValue(), "13");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("on a phone the indicators popover fits on screen", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    const box = await popover.boundingBox();
    assert.ok(box && box.x >= -1 && box.x + box.width <= PHONE.width + 1, `popover at ${JSON.stringify(box)} is cut off`);
    await popover.getByRole("button", { name: "Close indicators editor" }).click();
    await popover.waitFor({ state: "detached", timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });
