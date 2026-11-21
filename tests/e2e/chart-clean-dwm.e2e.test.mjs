// Clean Daily / Weekly / Monthly charts (2026-11-21, Ganesh: "I want to see
// only charts M/W/D with tickers and fib and ema and patterns if I want I will
// enable it and I will save the chart layout" -> "Clean single chart").
// No saved profiles (charts-core.helpers answers the profile read with none),
// so the D/W/M chart starts clean and a minute chart keeps its own studies.

import assert from "node:assert/strict";

import { PHONE, e2eTest } from "./harness.mjs";
import { card, openIndicators, profilePosts, studyCheckbox, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const KEPT = ["EMA 9", "EMA 21", "Big swing Fib", "Chart pattern (breakout)"];
const OFF = ["Ichimoku", "VWAP", "OI Levels / High OI"];

// The studies list is a <details>: a click on its summary opens AND closes it.
async function closeIndicators(page) {
  const menu = card(page).locator("details.oi-finder-indicators-menu");
  if (await menu.evaluate((node) => node.open)) await menu.locator("summary").click();
}

async function checked(popover, labels) {
  const out = {};
  for (const label of labels) out[label] = await studyCheckbox(popover, label).isChecked();
  return out;
}

e2eTest("a Monthly chart starts with only EMA, Big swing Fib and the pattern on; ticking a study and saving keeps it", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["M"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "M" });
    const popover = await openIndicators(page);
    assert.deepEqual(await checked(popover, KEPT), Object.fromEntries(KEPT.map((label) => [label, true])));
    assert.deepEqual(await checked(popover, OFF), Object.fromEntries(OFF.map((label) => [label, false])));
    // He turns one back on: the M profile is saved with it and the clean marker.
    const earlier = profilePosts(app).length;
    await studyCheckbox(popover, "Ichimoku").click();
    await page.waitForTimeout(1_500);
    const posts = profilePosts(app).slice(earlier);
    assert.equal(posts.at(-1)?.key, "M");
    assert.equal(posts.at(-1)?.profile?.indicatorSettings?.ichimoku, true);
    assert.equal(posts.at(-1)?.profile?.indicatorSettings?.cleanDwmVersion, "clean-dwm-v1");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a 5-minute chart keeps its studies, and gets them back after visiting the Daily chart", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "5m" });
    let popover = await openIndicators(page);
    const before = await checked(popover, ["Ichimoku", "VWAP"]);
    assert.deepEqual(before, { Ichimoku: true, VWAP: true }, "defaults on a minute chart");
    await closeIndicators(page);
    await card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button', { hasText: /^D$/ }).click();
    await waitForTape(page, { symbol: "NVDA", timeframe: "D" });
    popover = await openIndicators(page);
    assert.deepEqual(await checked(popover, ["Ichimoku", "VWAP", "EMA 21"]), { Ichimoku: false, VWAP: false, "EMA 21": true });
    await closeIndicators(page);
    await card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button', { hasText: /^5m$/ }).click();
    await waitForTape(page, { symbol: "NVDA", timeframe: "5m" });
    popover = await openIndicators(page);
    assert.deepEqual(await checked(popover, ["Ichimoku", "VWAP"]), before, "the clean Daily set did not leak into 5m");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("on a phone the Weekly chart starts clean too", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"], timeframes: ["W"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "W" });
    const popover = await openIndicators(page);
    assert.equal(await studyCheckbox(popover, "Chart pattern (breakout)").isChecked(), true);
    assert.equal(await studyCheckbox(popover, "Ichimoku").isChecked(), false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
