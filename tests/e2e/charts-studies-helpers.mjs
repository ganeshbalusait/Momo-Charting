// Shared steps for the charts-studies end-to-end specs (2026-11-04 coverage pass).
// Not a spec itself (no .e2e.test.mjs suffix), so the e2e glob never runs it alone.
//
// Every chart spec opens a FRESH browser on Charts & OI with:
//  * GET /api/chart-grids answered empty - otherwise the trader's saved default
//    grid applies on boot (reload into his 6-chart layout) and the spec would test
//    his layout instead of the feature, on six live tickers;
//  * GET /api/chart-indicator-profiles answered empty - otherwise his saved
//    per-timeframe study profile re-ticks the boxes the spec just changed;
//  * POST /api/chart-indicator-profiles answered ok - every study toggle saves the
//    profile to the server; it must never overwrite his real one.
// So a fresh test browser shows the default single AAPL 5m chart.
import assert from "node:assert/strict";

export const CHART_VIEW = "Charts & OI";
export const PROFILE_SAVE = "POST /api/chart-indicator-profiles";
export const GRIDS_READ = "GET /api/chart-grids";

export function chartAppOptions({ fakeReads = {}, fakeWrites = {}, ...rest } = {}) {
  return {
    view: CHART_VIEW,
    ...rest,
    fakeReads: {
      [GRIDS_READ]: { json: { grids: {} } },
      "GET /api/chart-indicator-profiles": { json: { profiles: {} } },
      ...fakeReads,
    },
    fakeWrites: {
      [PROFILE_SAVE]: { json: { ok: true } },
      ...fakeWrites,
    },
  };
}

export const cardSelector = (symbol) => `section.oi-finder-chart-card[data-chart-symbol="${symbol}"]`;

// The first chart card for `symbol`, once its tape has candles on it.
export async function chartCard(page, symbol = "AAPL", { timeframe = null, timeout = 90_000 } = {}) {
  const selector = cardSelector(symbol) + (timeframe ? `[data-chart-timeframe="${timeframe}"]` : "");
  await page.locator(selector).first().waitFor({ state: "attached", timeout });
  await page.waitForFunction(
    (sel) => Number(document.querySelector(sel)?.dataset.tapeLength || 0) > 0,
    selector,
    { timeout },
  );
  return page.locator(selector).first();
}

// Open the card's "Indicators & studies" popover.
export async function openIndicators(card) {
  const popover = card.locator(".oi-finder-indicators-popover");
  if (await popover.isVisible().catch(() => false)) return popover;
  await card.locator('details.oi-finder-indicators-menu > summary[aria-label="Add or remove chart indicators"]').click();
  await popover.waitFor({ state: "visible", timeout: 15_000 });
  return popover;
}

export async function closeIndicators(card) {
  const close = card.locator('button.oi-finder-indicators-close[aria-label="Close indicators editor"]');
  if (await close.isVisible().catch(() => false)) await close.click();
  await card.locator(".oi-finder-indicators-popover").waitFor({ state: "detached", timeout: 10_000 });
}

// One study's row in the popover, by its exact bold label.
export function studyRow(card, label) {
  return card.locator("section.oi-finder-indicator-row").filter({ has: card.page().locator(`b:text-is("${label}")`) }).first();
}

// Tick or untick a study; returns the checkbox.
export async function setStudy(card, label, on) {
  await openIndicators(card);
  const box = studyRow(card, label).locator('input[type="checkbox"]').first();
  await box.waitFor({ state: "attached", timeout: 10_000 });
  if ((await box.isChecked()) !== on) await box.click();
  assert.equal(await box.isChecked(), on, `${label} did not switch ${on ? "on" : "off"}`);
  return box;
}

export async function readStorage(page, key) {
  return page.evaluate((name) => window.localStorage.getItem(name), key);
}

export function noPageErrors(app) {
  assert.deepEqual(app.pageErrors, [], `page errors:\n${app.pageErrors.join("\n")}`);
}
