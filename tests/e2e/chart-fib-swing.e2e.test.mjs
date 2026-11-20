// Fib on Daily / Weekly / Monthly charts (2026-11-20, "Do both"):
//  * Big swing Fib - TradingView-style retracement of the lowest low to the
//    highest high of the last 252 days / 104 weeks / 60 months, 50%-61.8%
//    zone shaded, a "Fib 50% (price)" tag per level.
//  * AutoFib gains Weekly and Monthly timeframes.
// Read-only: the indicator profile POST is answered by charts-core.helpers.

import assert from "node:assert/strict";

import { PHONE, e2eTest } from "./harness.mjs";
import {
  card, openIndicators, profilePosts, studyCheckbox, studyRow, waitForTape, withCharts,
} from "./charts-core.helpers.mjs";

const BIG_SWING = "Big swing Fib (D/W/M)";
const AUTOFIB = "AutoFib — Single TF";

async function fibTags(page) {
  return page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
    .filter((node) => node.children.length === 0 && /^Fib \d+(\.\d)?% \(\d/.test(node.textContent.trim()))
    .map((node) => node.textContent.trim()));
}

async function waitForFibTags(page, atLeast = 3) {
  await page.waitForFunction((count) => [...document.querySelectorAll("section.oi-finder-chart-card *")]
    .filter((node) => node.children.length === 0 && /^Fib \d+(\.\d)?% \(\d/.test(node.textContent.trim()))
    .length >= count, atLeast, { timeout: 150_000 });
}

e2eTest("NVDA monthly chart shows the Big swing Fib tags; switching it off removes them and saves the M profile", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["M"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "M" });
    await waitForFibTags(page, 3);
    const tags = await fibTags(page);
    // NVDA monthly is a five-year RISE, so 100% sits at the low far below the
    // high (a short first-paint tape once drew 243 -> this month's low instead).
    const percent = (name) => Number(tags.find((tag) => tag.startsWith(`Fib ${name} (`))?.match(/\(([\d.]+)\)/)?.[1]);
    assert.ok(percent("100%") < percent("23.6%"), `the swing should be a rise: ${tags.join(" | ")}`);
    assert.ok(percent("100%") < 50, `the low should be the 2022 low: ${tags.join(" | ")}`);
    assert.ok(tags.some((tag) => tag.startsWith("Fib 61.8% (") || tag.startsWith("Fib 50% (")), tags.join(" | "));

    const popover = await openIndicators(page);
    const box = studyCheckbox(popover, BIG_SWING);
    assert.equal(await box.isChecked(), true, "Big swing Fib is on by default");
    const earlier = profilePosts(app).length;
    await box.click();
    await page.waitForFunction(() => ![...document.querySelectorAll("section.oi-finder-chart-card *")]
      .some((node) => node.children.length === 0 && /^Fib \d/.test(node.textContent.trim())), null, { timeout: 15_000 });
    const posts = profilePosts(app).slice(earlier);
    assert.equal(posts.at(-1)?.profile?.indicatorSettings?.fibSwing, false);
    assert.equal(posts.at(-1)?.key, "M");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("AutoFib settings list Weekly and Monthly, on by default; Big swing has its candle counts", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["W"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "W" });
    const popover = await openIndicators(page);
    await popover.getByRole("button", { name: `${AUTOFIB} settings`, exact: true }).click();
    const settings = studyRow(popover, AUTOFIB).locator(".oi-finder-indicator-settings");
    for (const label of ["Show Weekly", "Show Monthly"]) {
      const option = settings.locator("label", { hasText: label }).locator('input[type="checkbox"]');
      assert.equal(await option.isChecked(), true, label);
    }
    await popover.getByRole("button", { name: `${BIG_SWING} settings`, exact: true }).click();
    const swing = studyRow(popover, BIG_SWING).locator(".oi-finder-indicator-settings");
    assert.equal(await swing.locator("label", { hasText: "Weekly candles" }).locator("input").inputValue(), "104");
    assert.equal(await swing.locator("label", { hasText: "Monthly candles" }).locator("input").inputValue(), "60");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a 5-minute chart gets no Big swing Fib", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    await page.waitForTimeout(3_000);
    assert.deepEqual(await fibTags(page), []);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("phone: NVDA weekly chart shows Fib tags inside the chart, nothing cut off sideways", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"], timeframes: ["W"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "W" });
    await waitForFibTags(page, 2);
    const box = await card(page).boundingBox();
    const outside = await page.evaluate((cardBox) => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      .filter((node) => node.children.length === 0 && /^Fib \d/.test(node.textContent.trim()))
      .map((node) => node.getBoundingClientRect())
      .filter((rect) => rect.right > cardBox.x + cardBox.width + 1 || rect.left < cardBox.x - 1).length, box);
    assert.equal(outside, 0);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(sideways, false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
