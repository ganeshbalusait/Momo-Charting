// Shared setup for the charts-core end-to-end specs (2026-11-04 coverage).
// Not a spec itself (no .e2e.test.mjs suffix), so the suite glob skips it.
//
// Every chart spec opens "Charts & OI" on a SEEDED workspace so the layout,
// tickers and timeframes are the spec's, not the trader's saved grid:
//  * GET /api/chart-grids is answered with no saved grids, so a fresh test
//    browser is never steered into (and reloaded onto) his default grid;
//  * GET /api/chart-indicator-profiles is answered with no server profiles,
//    so the indicator set is the app's defaults, not his saved one;
//  * oiFinderChartWorkspace holds the layout, panels and timeframes.
// Tickers stay within SPY / AAPL / NVDA, at most two per spec.

import { withApp } from "./harness.mjs";

export const CHARTS_VIEW = "Charts & OI";
export const CARD = "section.oi-finder-chart-card[data-tape-length]";
export const NO_SAVED_GRIDS = { "GET /api/chart-grids": { json: { grids: {} } } };
export const NO_SERVER_PROFILES = { "GET /api/chart-indicator-profiles": { json: { profiles: {} } } };
// Every indicator click saves the profile for all tickers; answer it here so
// nothing reaches the trader's real profile file.
export const PROFILE_SAVE_OK = { "POST /api/chart-indicator-profiles": { json: { ok: true } } };

export function workspaceStorage({
  layoutId = "single", symbols = ["AAPL"], timeframes = ["5m"], syncSymbols = true, activePanel = 0, priceLocks = [],
} = {}) {
  const panels = Array.from({ length: 10 }, (_, index) => ({
    symbol: symbols[index % symbols.length],
    linkGroup: (index % 9) + 1,
    timeframe: timeframes[index % timeframes.length],
    priceLock: priceLocks[index] === true,
  }));
  const workspace = {
    version: 2, layoutId, isMaximized: false, companionVisible: true, syncSymbols,
    activePanel, widePanel: null, panels, geometry: {},
  };
  return { oiFinderChartWorkspace: JSON.stringify(workspace), oiFinderChartLayout: layoutId };
}

// withApp on Charts & OI with a seeded workspace and deterministic reads.
export function withCharts({ workspace = {}, storage = {}, fakeReads = {}, fakeWrites = {}, ...options } = {}, fn) {
  return withApp({
    ...options,
    view: CHARTS_VIEW,
    storage: { ...workspaceStorage(workspace), ...storage },
    fakeReads: { ...NO_SAVED_GRIDS, ...NO_SERVER_PROFILES, ...fakeReads },
    fakeWrites: { ...PROFILE_SAVE_OK, ...fakeWrites },
  }, fn);
}

// The n-th real chart card (progressive placeholders carry no tape attrs).
export function card(page, index = 0) {
  return page.locator(CARD).nth(index);
}

// Wait until panel `index` owns a non-empty tape (for `symbol`, if given).
export async function waitForTape(page, { index = 0, symbol = "", timeframe = "", timeout = 90_000 } = {}) {
  await page.waitForFunction(({ selector, index: at, symbol: want, timeframe: frame }) => {
    const node = document.querySelectorAll(selector)[at];
    if (!node || !(Number(node.dataset.tapeLength) > 0)) return false;
    if (want && (node.dataset.chartSymbol !== want || node.dataset.chartOwner !== want)) return false;
    if (frame && node.dataset.chartTimeframe !== frame) return false;
    return true;
  }, { selector: CARD, index, symbol, timeframe }, { timeout });
}

export async function cardData(page, index = 0) {
  return page.locator(CARD).nth(index).evaluate((node) => ({ ...node.dataset, className: node.className }));
}

// Chart GETs the page made, parsed, optionally for one ticker.
export function chartReads(app, symbol = "") {
  return app.reads
    .filter((read) => read.path === "/api/oi-finder-chart")
    .map((read) => Object.fromEntries(new URLSearchParams(read.search)))
    .filter((query) => !symbol || String(query.symbol || "").toUpperCase() === symbol);
}

// Width of the time scrollbar thumb of panel `index` (the visible time window).
export async function thumbWidth(page, index = 0) {
  const thumb = page.locator(CARD).nth(index).locator(".oi-chart-scrollbar-thumb");
  await page.waitForFunction((element) => element && element.style.visibility !== "hidden" && element.getBoundingClientRect().width > 0,
    await thumb.elementHandle(), { timeout: 30_000 });
  return (await thumb.boundingBox()).width;
}

export async function waitForThumbChange(page, before, { index = 0, direction = 0, timeout = 10_000 } = {}) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const now = await thumbWidth(page, index);
    const moved = direction < 0 ? now < before - 1 : direction > 0 ? now > before + 1 : Math.abs(now - before) > 1;
    if (moved || Date.now() > deadline) return now;
    await page.waitForTimeout(150);
  }
}

export async function openIndicators(page, index = 0) {
  const panel = page.locator(CARD).nth(index);
  await panel.locator('details.oi-finder-indicators-menu > summary[aria-label="Add or remove chart indicators"]').click();
  const popover = panel.locator(".oi-finder-indicators-popover");
  await popover.waitFor({ state: "visible", timeout: 15_000 });
  return popover;
}

const exactly = (text) => new RegExp(`^${text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`);

// One study row, found by its exact visible label ("EMA 9" must not match
// "MTF EMA 9×20 Signal Clouds").
export function studyRow(popover, label) {
  return popover.locator("section.oi-finder-indicator-row")
    .filter({ has: popover.page().locator("label b", { hasText: exactly(label) }) })
    .first();
}

// The on/off checkbox of one study row.
export function studyCheckbox(popover, label) {
  return studyRow(popover, label).locator("label > input[type=\"checkbox\"]").first();
}

export function profilePosts(app) {
  return app.writes
    .filter((write) => write.method === "POST" && write.path === "/api/chart-indicator-profiles")
    .map((write) => JSON.parse(write.body || "{}"));
}
