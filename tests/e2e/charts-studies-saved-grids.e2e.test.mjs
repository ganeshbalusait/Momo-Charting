// Charts & OI -> "Save layout" and the Grids menu, end to end (2026-11-04
// coverage pass, feature charts-saved-grids). Every POST /api/chart-grids is
// answered by the harness (fakeWrites) or refused by it, so the trader's real
// saved grids are never touched; the GET is faked too, so a spec sees either an
// empty store or the default grid it plants - never his live layout.
import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { GRIDS_READ, chartAppOptions, chartCard, noPageErrors, readStorage } from "./charts-studies-helpers.mjs";

const GRIDS_SAVE = "POST /api/chart-grids";
const SAVE_BUTTON = 'button.oi-chart-workspace-toggle.oi-chart-workspace-save[aria-label="Save the complete chart workspace and open it on every device"]';
const MENU_BUTTON = 'button.oi-chart-workspace-toggle[aria-label="Open the saved grids menu"]';
const grids = (app) => app.writes.filter((write) => write.method === "POST" && write.path === "/api/chart-grids");
const sentStore = (write) => JSON.parse(write.body).grids;

// A default grid "saved on another device": two AAPL charts side by side (5m + 15m).
const SAVED_AT = "2026-11-03T15:00:00.000Z";
const REMOTE_STORE = {
  "Two up": {
    savedAt: SAVED_AT,
    workspace: {
      version: 2, layoutId: "two-columns", isMaximized: false, companionVisible: true, syncSymbols: true, activePanel: 0, widePanel: null,
      panels: [{ symbol: "AAPL", timeframe: "5m", linkGroup: 1 }, { symbol: "AAPL", timeframe: "15m", linkGroup: 1 }],
      geometry: {},
    },
    indicators: null,
  },
  $meta: { defaultGrid: { name: "Two up", savedAt: SAVED_AT }, deleted: {} },
};

e2eTest("Save layout pushes the workspace as the default grid and says it opens on every device", async () => {
  await withApp(chartAppOptions({ fakeWrites: { [GRIDS_SAVE]: { json: { ok: true, count: 1 } } } }), async (app) => {
    await chartCard(app.page, "AAPL");
    await app.page.locator(SAVE_BUTTON).first().click();
    const status = app.page.locator('span.oi-chart-workspace-sync[role="status"][data-grid-sync="saved"]').first();
    await status.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await status.innerText()).trim(), "Saved — opens on every device");
    assert.equal(grids(app).length, 1, "one press, one push");
    const store = sentStore(grids(app)[0]);
    assert.equal(store.$meta.defaultGrid.name, "My layout");
    assert.equal(store["My layout"].workspace.layoutId, "single");
    assert.equal(store["My layout"].workspace.panels[0].symbol, "AAPL");
    assert.equal(await readStorage(app.page, "oiFinderAppliedChartGrid"), `My layout@${store.$meta.defaultGrid.savedAt}`,
      "the saving device must stamp itself so it does not re-apply its own save");
    noPageErrors(app);
  });
});

e2eTest("a failed server save stays on screen as 'saved here only'", async () => {
  // No fakeWrites: the harness answers the push with a 503.
  await withApp(chartAppOptions(), async (app) => {
    await chartCard(app.page, "AAPL");
    await app.page.locator(SAVE_BUTTON).first().click();
    const status = app.page.locator('span.oi-chart-workspace-sync[data-grid-sync="failed"]').first();
    await status.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await status.innerText()).trim(), "Server save failed — saved here only");
    assert.match(await status.getAttribute("title"), /HTTP 503/);
    await app.page.waitForTimeout(9_000); // a green "saved" clears after 8 s; a failure must not
    assert.equal(await status.isVisible(), true, "the failure notice cleared itself");
    const local = JSON.parse(await readStorage(app.page, "oiFinderChartGrids"));
    assert.ok(local["My layout"], "the layout is still saved in this browser");
    noPageErrors(app);
  });
});

e2eTest("a named grid can be saved, is listed, and can be deleted (each change pushed)", async () => {
  await withApp(chartAppOptions({ fakeWrites: { [GRIDS_SAVE]: { json: { ok: true, count: 1 } } } }), async (app) => {
    await chartCard(app.page, "AAPL");
    await app.page.locator(MENU_BUTTON).first().click();
    const menu = app.page.locator('[role="menu"][aria-label="Saved grids"]');
    await menu.waitFor({ state: "visible", timeout: 10_000 });
    assert.equal(await app.page.locator(MENU_BUTTON).first().getAttribute("aria-expanded"), "true");
    const submit = menu.locator('button[type="submit"]', { hasText: "Save grid" });
    assert.equal(await submit.isDisabled(), true, "Save grid needs a name");
    await menu.locator('input[aria-label="Name for this saved grid"]').fill("E2E grid");
    await submit.click();
    const load = menu.locator('button[aria-label="Load the saved grid E2E grid"]');
    await load.waitFor({ state: "visible", timeout: 10_000 });
    await menu.locator('[role="status"][data-grid-sync="saved"]').waitFor({ timeout: 15_000 });
    assert.equal(grids(app).length, 1);
    assert.ok(sentStore(grids(app)[0])["E2E grid"], "the named grid was not in the push");

    await menu.locator('button[aria-label="Delete the saved grid E2E grid"]').click();
    await load.waitFor({ state: "detached", timeout: 10_000 });
    for (let waited = 0; grids(app).length < 2 && waited < 10_000; waited += 250) await app.page.waitForTimeout(250);
    assert.equal(grids(app).length, 2, "the delete was not pushed");
    const after = sentStore(grids(app)[1]);
    assert.equal(after["E2E grid"], undefined);
    assert.ok(after.$meta.deleted["E2E grid"], "the delete must leave a tombstone so another device cannot resurrect it");
    noPageErrors(app);
  });
});

e2eTest("a default grid saved on another device opens here exactly once", async () => {
  await withApp(chartAppOptions({ fakeReads: { [GRIDS_READ]: { json: { grids: REMOTE_STORE } } } }), async (app) => {
    let navigations = 0;
    app.page.on("framenavigated", (frame) => { if (frame === app.page.mainFrame()) navigations += 1; });
    await app.page.locator("section.oi-chart-multilayout.layout-two-columns").waitFor({ state: "visible", timeout: 90_000 });
    assert.equal(await readStorage(app.page, "oiFinderAppliedChartGrid"), `Two up@${SAVED_AT}`);
    await chartCard(app.page, "AAPL", { timeframe: "5m" });
    await chartCard(app.page, "AAPL", { timeframe: "15m" });
    const settled = navigations;
    await app.page.waitForTimeout(8_000);
    assert.equal(navigations, settled, "the grid re-applied itself in a reload loop");
    assert.ok(navigations <= 1, `expected one reload into the grid, saw ${navigations}`);
    assert.equal(grids(app).length, 0, "applying a server grid must not push anything back");
    noPageErrors(app);
  });
});

// The steer on its own. The harness re-seeds its start page ("Settings") on every
// page load, so the grid's own apply-and-reload would bounce this test browser back
// to Settings - something a real browser never does. The planted default therefore
// names a grid with no saved workspace: the steer (which reads only the default
// marker) fires, the workspace finds nothing to apply, and no reload follows.
const STEER_ONLY_STORE = {
  "Two up": { savedAt: SAVED_AT, indicators: null },
  $meta: { defaultGrid: { name: "Two up", savedAt: SAVED_AT }, deleted: {} },
};

e2eTest("opened on Settings with an unapplied default grid, the app moves itself to Charts & OI", async () => {
  await withApp(chartAppOptions({ view: "Settings", fakeReads: { [GRIDS_READ]: { json: { grids: STEER_ONLY_STORE } } } }), async (app) => {
    await app.page.locator('[data-testid="charts-and-oi-view"]').waitFor({ state: "visible", timeout: 60_000 });
    await chartCard(app.page, "AAPL");
    await app.page.waitForTimeout(3_000);
    assert.equal(await app.page.locator('[data-testid="charts-and-oi-view"]').isVisible(), true, "the app left Charts & OI again");
    noPageErrors(app);
  });
});

e2eTest("a page the trader is already on is not taken over when the default grid is already applied", async () => {
  // Same Settings start, but this browser has applied the planted default.
  await withApp(chartAppOptions({
    view: "Settings",
    storage: { oiFinderAppliedChartGrid: `Two up@${SAVED_AT}` },
    fakeReads: { [GRIDS_READ]: { json: { grids: STEER_ONLY_STORE } } },
  }), async (app) => {
    await app.page.locator('[data-testid="primary-navigation"]').waitFor({ state: "visible", timeout: 60_000 });
    for (let waited = 0; !app.reads.some((read) => read.path === "/api/chart-grids") && waited < 30_000; waited += 500) await app.page.waitForTimeout(500);
    await app.page.waitForTimeout(3_000);
    assert.equal(await app.page.locator('[data-testid="charts-and-oi-view"]').count(), 0, "an already-applied grid still steered the app away from Settings");
    noPageErrors(app);
  });
});
