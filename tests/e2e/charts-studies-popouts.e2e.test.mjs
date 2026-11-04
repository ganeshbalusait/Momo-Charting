// Detached chart window (/?popout=chart), end to end (2026-11-04 coverage pass,
// feature charts-popouts). The in-app buttons no longer open these windows, but a
// saved link still renders one: one standalone live chart with its own header,
// option chain toggle and live badge, and it must never disturb the main window's
// remembered page or apply a saved grid. Desktop only - a pop-out is a desktop
// window. The MAG7 pop-out opens seven live tickers, past this suite's two-ticker
// limit, so it is not exercised here.
import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { GRIDS_READ, chartCard, noPageErrors, readStorage } from "./charts-studies-helpers.mjs";

const POPOUT = '[data-testid="chart-popout-view"]';

e2eTest("a chart link opens NVDA 15m alone with its header, chain and live badge", async () => {
  await withApp({ path: "/?popout=chart&symbol=NVDA&timeframe=15m&link=3" }, async (app) => {
    const view = app.page.locator(`${POPOUT}.charts-oi-popout-page.is-chart`);
    await view.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await app.page.locator('[data-testid="chart-popout-root"]').count(), 1);
    assert.equal((await view.locator(".charts-oi-popout-header b").first().innerText()).trim(), "NVDA · 15m");
    await chartCard(app.page, "NVDA", { timeframe: "15m" });
    assert.equal(await app.page.locator("section.oi-finder-chart-card[data-chart-symbol]").count(), 1, "a detached chart is ONE chart");
    assert.equal(await app.page.locator('[data-testid="primary-navigation"]').count(), 0, "no app chrome in a pop-out");
    const badge = view.locator(".charts-oi-popout-live");
    assert.match((await badge.innerText()).trim(), /^(LOADING|STREAMING|LIVE DATA)$/);
    assert.match(await app.page.title(), /^NVDA Live Chart/);
    noPageErrors(app);
  });
});

e2eTest("a link with no ticker and an unknown timeframe falls back to AAPL 5m", async () => {
  await withApp({ path: "/?popout=chart&timeframe=7m&link=42" }, async (app) => {
    const view = app.page.locator(POPOUT);
    await view.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal((await view.locator(".charts-oi-popout-header b").first().innerText()).trim(), "AAPL · 5m");
    await chartCard(app.page, "AAPL", { timeframe: "5m" });
    noPageErrors(app);
  });
});

e2eTest("the Option chain button opens and hides the chain beside the chart", async () => {
  await withApp({ path: "/?popout=chart&symbol=AAPL&timeframe=5m" }, async (app) => {
    const layout = app.page.locator(`${POPOUT} .charts-oi-popout-chart-layout`);
    await layout.waitFor({ state: "visible", timeout: 60_000 });
    assert.match(await layout.getAttribute("class"), /\bhas-chain\b/, "a pop-out opens with its chain");
    const toggle = app.page.locator(`${POPOUT} .charts-oi-popout-header button`, { hasText: /Hide options|Option chain/ });
    assert.equal((await toggle.innerText()).trim(), "Hide options");
    await toggle.click();
    await app.page.locator(`${POPOUT} .charts-oi-popout-chart-layout.is-chain-hidden`).waitFor({ timeout: 10_000 });
    assert.equal((await toggle.innerText()).trim(), "Option chain");
    await toggle.click();
    await app.page.locator(`${POPOUT} .charts-oi-popout-chart-layout.has-chain`).waitFor({ timeout: 10_000 });
    await app.page.locator(`${POPOUT} section.charts-oi-tos-chain.is-popout-chain`).waitFor({ state: "visible", timeout: 30_000 });
    noPageErrors(app);
  });
});

e2eTest("a pop-out never changes the main window's page and never applies a saved grid", async () => {
  const savedAt = "2026-11-03T15:00:00.000Z";
  const remote = {
    Quad: { savedAt, workspace: { version: 2, layoutId: "quad", panels: [{ symbol: "AAPL", timeframe: "5m" }] }, indicators: null },
    $meta: { defaultGrid: { name: "Quad", savedAt }, deleted: {} },
  };
  await withApp({
    path: "/?popout=chart&symbol=AAPL&timeframe=5m",
    storage: { agenticActiveView: "Settings" },
    fakeReads: { [GRIDS_READ]: { json: { grids: remote } } },
  }, async (app) => {
    let navigations = 0;
    app.page.on("framenavigated", (frame) => { if (frame === app.page.mainFrame()) navigations += 1; });
    await app.page.locator(POPOUT).waitFor({ state: "visible", timeout: 60_000 });
    await chartCard(app.page, "AAPL", { timeframe: "5m" });
    await app.page.waitForTimeout(6_000);
    assert.equal(navigations, 0, "the pop-out reloaded itself (a grid was applied)");
    assert.equal(await readStorage(app.page, "agenticActiveView"), "Settings", "the main window's remembered page changed");
    assert.equal(await readStorage(app.page, "oiFinderAppliedChartGrid"), null, "the pop-out consumed the main window's grid stamp");
    assert.equal(await app.page.locator("section.oi-chart-multilayout.layout-quad").count(), 0);
    assert.equal(app.writes.filter((write) => write.path === "/api/chart-grids").length, 0);
    noPageErrors(app);
  });
});
