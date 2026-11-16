// Charts & OI: switching tickers (2026-11-04 coverage). Typing a ticker, the
// quick-ticker rail, linked vs independent panels, a fast back-and-forth that
// must never leave the previous ticker's candles on screen (data-chart-owner),
// and the identity strip above the chart. Tickers: AAPL and NVDA only.

import assert from "node:assert/strict";

import { e2eTest } from "./harness.mjs";
import { CARD, card, cardData, waitForTape, withCharts, workspaceStorage } from "./charts-core.helpers.mjs";

const tickerInput = (page, index = 0) => card(page, index).locator('input[aria-label="Chart ticker"]');

async function typeTicker(page, symbol, index = 0) {
  const input = tickerInput(page, index);
  await input.fill(symbol);
  await input.press("Enter");
}

e2eTest("typing NVDA and Enter moves the panel, its tape and its OHLC strip to NVDA", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await typeTicker(page, "nvda");
    await waitForTape(page, { symbol: "NVDA" });
    assert.match(await card(page).locator(".oi-finder-chart-ohlc b").innerText(), /^NVDA · 5m$/);
    assert.equal(await tickerInput(page).inputValue(), "NVDA");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("an unknown typed ticker offers 'Search option chain' instead of loading it", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const input = tickerInput(page);
    await input.click();
    await input.fill("ZZQX");
    const listbox = card(page).locator('.oi-finder-chart-symbol-dropdown[role="listbox"][aria-label="Chart ticker suggestions"]');
    await listbox.waitFor({ state: "visible", timeout: 15_000 });
    await listbox.locator("button", { hasText: "Search option chain" }).waitFor({ timeout: 15_000 });
    assert.match(await listbox.locator("button", { hasText: "Search option chain" }).innerText(), /ZZQX/);
    // Clear it before the page goes: leaving the field with ZZQX typed would
    // open that (non-existent) ticker.
    await input.fill("");
    assert.equal((await cardData(page)).chartSymbol, "AAPL");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("a quick-ticker click switches the active panel and marks that ticker current", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const rail = page.locator('nav.oi-chart-quick-tickers[aria-label="Quick-access OI Finder tickers"]');
    await rail.waitFor({ state: "visible", timeout: 30_000 });
    // 2026-11-11: the rail has no "FAV TICKERS" label any more - it hid USO.
    assert.equal(await rail.getByText("FAV TICKERS", { exact: true }).count(), 0);
    assert.equal(await rail.locator("button", { hasText: /^AAPL/ }).getAttribute("aria-current"), "true");
    await rail.locator("button", { hasText: /^NVDA/ }).click();
    await waitForTape(page, { symbol: "NVDA" });
    assert.equal(await rail.locator("button", { hasText: /^NVDA/ }).getAttribute("aria-current"), "true");
    assert.equal(await rail.locator("button", { hasText: /^AAPL/ }).getAttribute("aria-current"), null);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

// Explicit TOS colour tags outrank the Link toggle (App.jsx panesShareOneLinkGroup,
// f3d4c8ff 2026-09-27): panes tagged with different colours stay independent
// even with Link on. The shared seed tags pane n with colour n+1, so this spec
// tags both visible panes with the same colour - the case where the toggle
// "keeps its original meaning".
function oneColourWorkspace(options) {
  const seeded = workspaceStorage(options);
  const workspace = JSON.parse(seeded.oiFinderChartWorkspace);
  workspace.panels = workspace.panels.map((panel) => ({ ...panel, linkGroup: 1 }));
  return { ...seeded, oiFinderChartWorkspace: JSON.stringify(workspace) };
}

e2eTest("with Link ticker on both panels follow; with it off only the active panel changes", async () => {
  const layout = { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"], syncSymbols: true };
  await withCharts({ workspace: layout, storage: oneColourWorkspace(layout) }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 0, symbol: "AAPL" });
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    const linkToggle = page.locator('button.oi-chart-workspace-toggle[aria-label="Ticker linked across charts"]');
    await linkToggle.waitFor({ timeout: 15_000 });
    assert.equal(await linkToggle.getAttribute("aria-pressed"), "true");
    await typeTicker(page, "NVDA", 0);
    await waitForTape(page, { index: 0, symbol: "NVDA" });
    await waitForTape(page, { index: 1, symbol: "NVDA" });

    await linkToggle.click();
    const unlinked = page.locator('button.oi-chart-workspace-toggle[aria-label="Link ticker across charts"]');
    await unlinked.waitFor({ timeout: 15_000 });
    assert.equal(await unlinked.getAttribute("aria-pressed"), "false");
    await typeTicker(page, "AAPL", 0);
    await waitForTape(page, { index: 0, symbol: "AAPL" });
    await page.waitForTimeout(3_000);
    assert.equal((await cardData(page, 1)).chartSymbol, "NVDA", "an unlinked panel must keep its own ticker");
    assert.equal((await cardData(page, 1)).chartOwner, "NVDA");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("AAPL -> NVDA -> AAPL inside two seconds ends on AAPL's own candles", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await typeTicker(page, "NVDA");
    // The panel must actually BE on NVDA before the trader goes back. Under
    // load the NVDA switch can take seconds to reach the page; typing AAPL
    // before it did let the wait below be answered by the ORIGINAL AAPL chart,
    // and the hold loop then ran through the real NVDA -> AAPL reload. Only
    // the ticker has to arrive, not NVDA's candles: going back while they are
    // still loading is the point of this test.
    await page.waitForFunction((selector) => document.querySelector(selector)?.dataset.chartSymbol === "NVDA",
      CARD, { timeout: 60_000 });
    await page.waitForTimeout(400);
    await typeTicker(page, "AAPL");
    await waitForTape(page, { symbol: "AAPL" });
    // Hold for a few seconds: a late NVDA response must never be adopted.
    for (let check = 0; check < 6; check += 1) {
      const now = await cardData(page);
      assert.equal(now.chartSymbol, "AAPL");
      assert.equal(now.chartOwner, "AAPL", `NVDA bars on the AAPL panel: ${JSON.stringify(now)}`);
      assert.ok(Number(now.tapeLength) > 0);
      await page.waitForTimeout(500);
    }
    assert.match(await card(page).locator(".oi-finder-chart-ohlc b").innerText(), /^AAPL · 5m$/);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("the identity strip names the ticker, and a failing strip read never breaks the chart", async () => {
  const strip = {
    "GET /api/ticker-strip": (read) => (read.search.includes("symbol=AAPL")
      ? { json: { symbol: "AAPL", name: "APPLE INC (E2E)", lastPrice: 123.45, change: 1.2, changePct: 0.98, closePrice: 122.25 } }
      : { status: 500, json: { error: "strip down (end-to-end test)" } }),
  };
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeReads: strip }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const identity = card(page).locator('[data-testid="ticker-identity-strip"]');
    await page.waitForFunction((selector) => /APPLE INC \(E2E\)/.test(document.querySelector(selector)?.textContent || ""),
      '[data-testid="ticker-identity-strip"]', { timeout: 30_000 });
    assert.match(await identity.locator(".ticker-identity-name").innerText(), /APPLE INC \(E2E\)/);

    await typeTicker(page, "NVDA");                 // its strip read answers 500
    await waitForTape(page, { symbol: "NVDA" });
    await identity.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await identity.locator(".ticker-identity-name").innerText(), /NVDA/, "falls back to the symbol, never the old name");
    assert.doesNotMatch(await identity.innerText(), /APPLE/);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
