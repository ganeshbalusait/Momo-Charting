// Learn auto-opens once for a brand-new browser (2026-11-04 coverage), end to
// end in the real app. The harness normally seeds agxLearnSeen with the current
// LEARN_CONTENT_VERSION; these specs override it to play a new browser. The
// saved default chart grid is marked applied (as the harness does) wherever
// the app could otherwise open a multi-chart grid; at most one chart opens.

import assert from "node:assert/strict";

import { OI_CHART_APPLIED_GRID_STORAGE_KEY, chartGridStamp, readChartGridMeta } from "../../frontend/src/chartWorkspaceState.js";
import { LEARN_CONTENT_VERSION } from "../../frontend/src/learnFirstVisit.js";
import { BASE_URL, PHONE, e2eTest, withApp } from "./harness.mjs";

const LEARN = '[data-testid="learn-view"]';

async function appliedGrid() {
  try {
    const response = await fetch(`${BASE_URL}/api/chart-grids`);
    const { defaultGrid } = readChartGridMeta((await response.json())?.grids);
    return defaultGrid ? { [OI_CHART_APPLIED_GRID_STORAGE_KEY]: chartGridStamp(defaultGrid.name, defaultGrid.savedAt) } : {};
  } catch {
    return {};
  }
}
const stored = (page, key) => page.evaluate((name) => localStorage.getItem(name), key);

e2eTest("a brand-new browser lands on Learn, stays there past the chart-grid steer, and burns the version", async () => {
  await withApp({ storage: { agxLearnSeen: "" } }, async (app) => {
    await app.page.locator(LEARN).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.waitForTimeout(10_000);
    assert.equal(await app.page.locator(LEARN).count(), 1, "the async grid steer took the new user away from Learn");
    assert.equal((await app.page.locator(".command-context h1").innerText()).trim(), "Learn + Setup");
    assert.equal(await stored(app.page, "agxLearnSeen"), String(LEARN_CONTENT_VERSION));
    assert.equal(await stored(app.page, "agenticActiveView"), "Learn + Setup");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("once seen, Learn never opens by itself again", async () => {
  const grid = await appliedGrid();
  await withApp({ storage: { agxLearnSeen: "", ...grid } }, async (app) => {
    await app.page.locator(LEARN).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator('button.nav-item[title="Settings"]').click();
    await app.page.locator(".command-context h1", { hasText: "Settings" }).waitFor();
    // A second window of the same browser: nothing re-seeded, storage as the app left it.
    const again = await app.context.newPage();
    const errors = [];
    again.on("pageerror", (error) => errors.push(String(error)));
    await again.goto(new URL("/", BASE_URL).href, { waitUntil: "domcontentloaded" });
    await again.locator(".command-context h1", { hasText: "Settings" }).waitFor({ timeout: 60_000 });
    await again.waitForTimeout(5_000);
    assert.equal(await again.locator(LEARN).count(), 0);
    assert.equal(await stored(again, "agxLearnSeen"), String(LEARN_CONTENT_VERSION));
    assert.deepEqual(errors, []);
  });
});

e2eTest("an up-to-date browser with no remembered page opens on Charts & OI, not Learn", async () => {
  await withApp({ storage: await appliedGrid() }, async (app) => {
    await app.page.locator(".command-context h1", { hasText: "Charts & OI" }).waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(3_000);
    assert.equal(await app.page.locator(LEARN).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("an older seen version mid-workflow stays on its remembered page", async () => {
  await withApp({ view: "Settings", storage: { agxLearnSeen: "1" } }, async (app) => {
    await app.page.locator(".command-context h1", { hasText: "Settings" }).waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(5_000);
    assert.equal(await app.page.locator(LEARN).count(), 0, "a remembered page must not be yanked to Learn");
    assert.equal(await stored(app.page, "agxLearnSeen"), "1", "the version is only burned when Learn is shown");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a pop-out chart window never becomes Learn and never writes the remembered page", async () => {
  await withApp({ path: "/?popout=chart&symbol=AAPL&timeframe=5m", storage: { agxLearnSeen: "" } }, async (app) => {
    await app.page.locator("#root").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(8_000);
    assert.equal(await app.page.locator(LEARN).count(), 0);
    assert.equal(await stored(app.page, "agenticActiveView"), null);
    assert.equal(await stored(app.page, "agxLearnSeen"), "", "a pop-out must not burn the first visit");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: a new browser opens Learn and the bottom bar still navigates away", async () => {
  await withApp({ viewport: PHONE, storage: { agxLearnSeen: "" } }, async (app) => {
    await app.page.locator(LEARN).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator('[data-testid="mobile-primary-premarket-scanner"]').click();
    await app.page.locator('[data-testid="premarket-scanner-view"]').waitFor({ timeout: 30_000 });
    assert.equal(await app.page.locator(LEARN).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
