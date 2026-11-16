// Charts & OI: zoom, pan, jump to latest, reset, price lock, crosshair and
// zoom memory (2026-11-04 coverage). The candles are canvas pixels, so the
// visible time window is read from the TradingView-style scrollbar thumb
// ("Chart time window"), and state from button aria/disabled and the OHLC strip.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways } from "./harness.mjs";
import { card, thumbWidth, waitForThumbChange, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const control = (page, label) => card(page).locator(`button[aria-label="${label}"]`);
const ZOOM_IN = "Zoom in";
const ZOOM_OUT = "Zoom out";
const PAN_BACK = "Pan the chart back in time to older candles";
const PAN_FORWARD = "Pan the chart forward in time to newer candles";
const LATEST = "Return to the newest live candle while preserving the selected horizontal zoom";
const RESET = "Reset both chart axes to the default candle-focused view";
const PRICE_LOCK = "Toggle price lock (follow the newest bar at every close)";

async function ready(page) {
  await waitForTape(page, { symbol: "AAPL" });
  await card(page).locator('.oi-chart-scrollbar[aria-label="AAPL chart time scroll"]').waitFor({ timeout: 30_000 });
  return thumbWidth(page);
}

e2eTest("zoom in narrows the time window, zoom out widens it, Reset returns the default", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    await control(page, ZOOM_IN).click();
    const zoomedIn = await waitForThumbChange(page, start, { direction: -1 });
    assert.ok(zoomedIn < start - 1, `zoom in: ${start} -> ${zoomedIn}`);
    await control(page, RESET).click();
    const reset = await waitForThumbChange(page, zoomedIn, { direction: 1 });
    assert.ok(Math.abs(reset - start) <= 3, `reset: ${start} -> ${reset}`);
    await control(page, ZOOM_OUT).click();
    const zoomedOut = await waitForThumbChange(page, reset, { direction: 1 });
    assert.ok(zoomedOut > reset + 1, `zoom out: ${reset} -> ${zoomedOut}`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("pan back enables pan forward; Return to latest disables it again and keeps the zoom", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await ready(page);
    await control(page, ZOOM_IN).click();
    const zoomed = await waitForThumbChange(page, await thumbWidth(page), { direction: -1, timeout: 3_000 });
    assert.equal(await control(page, PAN_FORWARD).isDisabled(), true, "at the live edge there is nothing newer to pan to");
    await control(page, PAN_BACK).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:enabled`).waitFor({ timeout: 10_000 });
    await control(page, LATEST).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:disabled`).waitFor({ timeout: 10_000 });
    assert.ok(Math.abs((await thumbWidth(page)) - zoomed) <= 3, "Return to latest changed the zoom");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("price lock stays on for the panel after a reload", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await ready(page);
    const lock = control(page, PRICE_LOCK);
    assert.equal(await lock.getAttribute("aria-pressed"), "false");
    await lock.click();
    await card(page).locator(`button[aria-label="${PRICE_LOCK}"][aria-pressed="true"]`).waitFor({ timeout: 5_000 });
    await page.waitForFunction(() => {
      try { return JSON.parse(localStorage.getItem("oiFinderChartWorkspace") || "{}").panels?.[0]?.priceLock === true; } catch { return false; }
    }, null, { timeout: 10_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await ready(page);
    assert.equal(await control(page, PRICE_LOCK).getAttribute("aria-pressed"), "true");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("hovering the chart shows the crosshair time; leaving it goes back to Live", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await ready(page);
    const strip = card(page).locator(".oi-finder-chart-ohlc small").first();
    assert.match(await strip.innerText(), /^Live \d{4}-\d{2}-\d{2} \d{2}:\d{2} ET/);
    const canvas = card(page).locator(".oi-finder-chart-canvas");
    const box = await canvas.boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
    await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.4);
    await card(page).locator(".oi-finder-chart-ohlc small.is-crosshair-time").waitFor({ timeout: 10_000 });
    assert.match(await strip.innerText(), /^Crosshair \d{4}-\d{2}-\d{2} \d{2}:\d{2} ET/);
    await page.mouse.move(2, 2);
    await page.waitForFunction(() => !document.querySelector(".oi-finder-chart-ohlc small.is-crosshair-time"), null, { timeout: 10_000 });
    assert.match(await strip.innerText(), /^Live /);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("the mouse wheel zooms time on the chart and never scrolls the page", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    const top = (await card(page).boundingBox()).y;
    const scroll = await page.evaluate(() => [window.scrollY, document.scrollingElement?.scrollTop || 0]);
    const box = await card(page).locator(".oi-finder-chart-canvas").boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    for (let notch = 0; notch < 3; notch += 1) await page.mouse.wheel(0, -240);
    const after = await waitForThumbChange(page, start);
    assert.ok(Math.abs(after - start) > 1, "the wheel did not zoom the time axis");
    assert.deepEqual(await page.evaluate(() => [window.scrollY, document.scrollingElement?.scrollTop || 0]), scroll);
    assert.ok(Math.abs((await card(page).boundingBox()).y - top) <= 1, "the chart card moved: the wheel scrolled its container");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("a zoom on 5m comes back after visiting 15m in the same session", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    await control(page, ZOOM_IN).click();
    let zoomed = await waitForThumbChange(page, start, { direction: -1 });
    await control(page, ZOOM_IN).click();
    zoomed = await waitForThumbChange(page, zoomed, { direction: -1 });
    const frames = card(page).locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button');
    await frames.filter({ hasText: /^15m$/ }).click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
    await frames.filter({ hasText: /^5m$/ }).click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    await page.waitForTimeout(800);
    const restored = await thumbWidth(page);
    assert.ok(Math.abs(restored - zoomed) <= 3, `5m zoom not restored: ${zoomed} -> ${restored} (default ${start})`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

// ---- Zoom memory edges (2026-11-14) -----------------------------------------
// One candle slot is about 4 px of thumb here, so these compare within 1.5 px:
// "about one candle wider" has to fail.
const frameButton = (page, label) => card(page)
  .locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button')
  .filter({ hasText: new RegExp(`^${label}$`) });

async function visit15mAndComeBack(page) {
  await frameButton(page, "15m").click();
  await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
  await frameButton(page, "5m").click();
  await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
  await page.waitForTimeout(800);
}

e2eTest("Return to latest keeps its candles and its blank room through 5m -> 15m -> 5m, three times over", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    await control(page, ZOOM_IN).click();
    let zoomed = await waitForThumbChange(page, start, { direction: -1 });
    await control(page, ZOOM_IN).click();
    zoomed = await waitForThumbChange(page, zoomed, { direction: -1 });
    await control(page, PAN_BACK).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:enabled`).waitFor({ timeout: 10_000 });
    await control(page, LATEST).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:disabled`).waitFor({ timeout: 10_000 });
    await page.waitForTimeout(600);
    const parked = await thumbWidth(page);
    assert.ok(Math.abs(parked - zoomed) <= 3, `Return to latest changed the zoom: ${zoomed} -> ${parked}`);
    // The view used to come back without the blank room ahead of the newest
    // candle (34 slots -> 28), and a second and third visit stepped it in
    // again (-> 22 -> 16).
    for (let trip = 1; trip <= 3; trip += 1) {
      await visit15mAndComeBack(page);
      const restored = await thumbWidth(page);
      assert.ok(Math.abs(restored - parked) <= 1.5, `round trip ${trip}: ${parked} -> ${restored} (default ${start})`);
      assert.equal(await control(page, PAN_FORWARD).isDisabled(), true, `round trip ${trip}: the newest candle left the screen`);
      // Park it again, as the trader would: on a view that is already parked
      // this moves nothing and remembers the restored view for the next visit.
      await control(page, LATEST).click();
      await page.waitForTimeout(600);
      const reparked = await thumbWidth(page);
      assert.ok(Math.abs(reparked - parked) <= 1.5, `round trip ${trip}, parked again: ${parked} -> ${reparked}`);
    }
    await control(page, RESET).click();
    const reset = await waitForThumbChange(page, parked, { direction: 1 });
    assert.ok(Math.abs(reset - start) <= 3, `Reset after the round trips: ${start} -> ${reset}`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("a mouse-wheel zoom on 5m comes back exactly, not a candle wider, on every visit", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    const box = await card(page).locator(".oi-finder-chart-canvas").boundingBox();
    const hover = () => page.mouse.move(box.x + box.width * 0.42, box.y + box.height * 0.4);
    await hover();
    for (let notch = 0; notch < 3; notch += 1) await page.mouse.wheel(0, -240);
    await waitForThumbChange(page, start);
    await page.waitForTimeout(900);
    const zoomed = await thumbWidth(page);
    assert.ok(Math.abs(zoomed - start) > 4, `the wheel did not zoom: ${start} -> ${zoomed}`);
    for (let trip = 1; trip <= 3; trip += 1) {
      await visit15mAndComeBack(page);
      const restored = await thumbWidth(page);
      assert.ok(Math.abs(restored - zoomed) <= 1.5, `round trip ${trip}: ${zoomed} -> ${restored} (default ${start})`);
      // A sideways nudge far smaller than a pixel: the chart is the trader's
      // again, so the restored view is saved afresh for the next visit.
      await hover();
      await page.mouse.wheel(0.02, 0);
      await page.waitForTimeout(700);
      const nudged = await thumbWidth(page);
      assert.ok(Math.abs(nudged - zoomed) <= 1.5, `round trip ${trip}, saved again: ${zoomed} -> ${nudged}`);
    }
    await control(page, RESET).click();
    await page.waitForTimeout(800);
    const reset = await thumbWidth(page);
    assert.ok(Math.abs(reset - start) <= 3, `Reset after the round trips: ${start} -> ${reset}`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("a zoom followed at once by a timeframe switch is still the zoom that comes back", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const start = await ready(page);
    // No wait between the two clicks: the zoom has to be remembered from the
    // range it set, not from what the chart reports a frame later.
    await control(page, ZOOM_IN).click();
    await frameButton(page, "15m").click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
    await frameButton(page, "5m").click();
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    await page.waitForTimeout(800);
    const restored = await thumbWidth(page);
    assert.ok(restored < start - 4, `the zoom was not remembered: ${start} -> ${restored}`);
    await control(page, RESET).click();
    const reset = await waitForThumbChange(page, restored, { direction: 1 });
    assert.ok(Math.abs(reset - start) <= 3, `reset: ${start} -> ${reset}`);
    await control(page, ZOOM_IN).click();
    const oneZoom = await waitForThumbChange(page, reset, { direction: -1 });
    assert.ok(Math.abs(restored - oneZoom) <= 1.5, `one zoom-in is ${oneZoom}, but ${restored} came back`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("on a phone, Return to latest keeps its blank room through 5m -> 15m -> 5m", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await ready(page);
    // A phone has no zoom buttons: the pan arrows and Return to latest are the
    // toolbar, and the timeframe is a native select.
    const select = card(page).locator('select.oi-finder-timeframe-select[aria-label="Chart timeframe"]');
    await select.waitFor({ state: "visible", timeout: 30_000 });
    await control(page, PAN_BACK).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:enabled`).waitFor({ timeout: 10_000 });
    await control(page, LATEST).click();
    await card(page).locator(`button[aria-label="${PAN_FORWARD}"]:disabled`).waitFor({ timeout: 10_000 });
    await page.waitForTimeout(600);
    const parked = await thumbWidth(page);
    for (let trip = 1; trip <= 2; trip += 1) {
      await select.selectOption("15m");
      await waitForTape(page, { symbol: "AAPL", timeframe: "15m" });
      await select.selectOption("5m");
      await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
      await page.waitForTimeout(800);
      const restored = await thumbWidth(page);
      assert.ok(Math.abs(restored - parked) <= 1.5, `round trip ${trip}: ${parked} -> ${restored}`);
      assert.equal(await control(page, PAN_FORWARD).isDisabled(), true, `round trip ${trip}: the newest candle left the screen`);
      await control(page, LATEST).click();
      await page.waitForTimeout(600);
    }
    assert.equal(await overflowsSideways(page), false, "the chart page scrolls sideways on a phone");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });
