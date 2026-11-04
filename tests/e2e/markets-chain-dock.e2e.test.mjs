// Charts & OI -> option-chain column '+' menu -> Sector and News dock panels
// (2026-11-04 coverage), end to end in the real app.
//
// One chart only (AAPL, the default), switched once to NVDA by a dock tile.
// The chain column is seeded open, and the saved default chart grid is marked
// as applied (the way the harness does for other pages) so a multi-chart grid
// is never opened. The automatic news refresh POST is answered by the harness.

import assert from "node:assert/strict";

import { OI_CHART_APPLIED_GRID_STORAGE_KEY, chartGridStamp, readChartGridMeta } from "../../frontend/src/chartWorkspaceState.js";
import { BASE_URL, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const CHAIN = "#charts-oi-option-chain";
const ADD = `${CHAIN} summary[aria-label="Add a panel to the option-chain column"]`;
const MENU = `${CHAIN} .charts-oi-chain-add-menu`;
const SECTOR = `${CHAIN} section.charts-oi-chain-dock-panel.sector-heatmap-panel[aria-label="Sector panel"]`;
const NEWS = `${CHAIN} section.charts-oi-chain-dock-panel[aria-label="News panel"]`;
const REFRESH_ACK = { "POST /api/news-feed/refresh": { json: { started: false, reason: "e2e", running: false } } };

async function chartStorage(extra = {}) {
  const storage = { chartsOiChainOpen: "true", ...extra };
  try {
    const response = await fetch(`${BASE_URL}/api/chart-grids`);
    const { defaultGrid } = readChartGridMeta((await response.json())?.grids);
    if (defaultGrid) storage[OI_CHART_APPLIED_GRID_STORAGE_KEY] = chartGridStamp(defaultGrid.name, defaultGrid.savedAt);
  } catch {
    // No grid endpoint answer: nothing to mark.
  }
  return storage;
}

async function openMenu(page) {
  await page.locator(ADD).waitFor({ state: "visible", timeout: 90_000 });
  await page.locator(ADD).click();
  await page.locator(MENU).waitFor({ state: "visible" });
  return page.locator(MENU);
}

const storedPanels = (page) => page.evaluate(() => JSON.parse(localStorage.getItem("chartsOiChainDockPanels") || "[]"));

e2eTest("the + menu adds the Sector panel, it survives a reload, and Remove takes it away for good", async () => {
  await withApp({ view: "Charts & OI", storage: await chartStorage(), fakeWrites: REFRESH_ACK }, async (app) => {
    const menu = await openMenu(app.page);
    const options = menu.locator("button", { hasText: "Options" });
    assert.equal(await options.isDisabled(), true, "the chain itself cannot be removed");
    assert.equal(await options.getAttribute("aria-pressed"), "true");
    assert.equal((await options.locator("small").innerText()).trim(), "1/1");
    assert.equal((await menu.locator("button", { hasText: "Sector" }).locator("small").innerText()).trim(), "0/1");
    await menu.locator("button", { hasText: "Sector" }).click();
    await app.page.locator(SECTOR).waitFor({ state: "visible", timeout: 30_000 });
    assert.deepEqual(await storedPanels(app.page), ["sector"]);
    const reopened = await openMenu(app.page);
    assert.equal(await reopened.locator("button", { hasText: "Sector" }).getAttribute("aria-pressed"), "true");
    await app.page.keyboard.press("Escape");
    await app.page.locator(ADD).click();          // close the menu again

    await app.page.reload({ waitUntil: "domcontentloaded" });
    await app.page.locator(SECTOR).waitFor({ state: "visible", timeout: 90_000 });

    await app.page.locator(`${SECTOR} button[aria-label="Remove sector panel"]`).click();
    await app.page.waitForFunction((selector) => !document.querySelector(selector), SECTOR);
    assert.deepEqual(await storedPanels(app.page), []);
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await app.page.locator(ADD).waitFor({ state: "visible", timeout: 90_000 });
    await app.page.waitForTimeout(1_000);
    assert.equal(await app.page.locator(SECTOR).count(), 0, "a removed panel came back after reload");
    assert.deepEqual(app.pageErrors, []);
  });
});

// The dock's news is dashboard.catalysts + catalystIndex, also refreshed by the
// chart's automatic GET /api/news-feed; both are answered with the same rows.
// The chart opens on whatever ticker this browser's profile starts on, so every
// quick-rail ticker gets 10 stories; NVDA (the switch target) gets 2.
const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
const story = (symbol, n, minutes) => ({
  id: `${symbol}-${n}`, symbol, headline: `${symbol} story ${n}`, url: `https://example.invalid/${symbol}/${n}`,
  published_at: minutesAgo(minutes), source: "Benzinga", score: 2, sentiment: "Positive", tags: "Earnings",
});
const RAIL = ["SPY", "QQQ", "SLV", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO"];
const STORIES = Object.fromEntries(RAIL.map((symbol) => [
  symbol, Array.from({ length: symbol === "NVDA" ? 2 : 10 }, (_, n) => story(symbol, n + 1, (n + 1) * 15)),
]));
// The index repeats each ticker's two newest stories (they must be deduped).
const NEWS_PAYLOAD = {
  catalysts: RAIL.flatMap((symbol) => STORIES[symbol].slice(0, 6)),
  catalystIndex: RAIL.flatMap((symbol) => [...STORIES[symbol].slice(0, 2), ...STORIES[symbol].slice(6)]),
};
const expected = (symbol) => STORIES[symbol].slice(0, 8).map((row) => row.headline);
const waitForArticles = (page, count) => page.waitForFunction(
  ([selector, wanted]) => document.querySelectorAll(`${selector} .charts-oi-chain-dock-news article`).length === wanted,
  [NEWS, count], { timeout: 30_000 },
);

e2eTest("News dock: only the chart ticker's newest 8 headlines, and a ticker switch follows", async () => {
  const storage = await chartStorage({ chartsOiChainDockPanels: JSON.stringify(["news", "sector"]) });
  await withApp({
    view: "Charts & OI",
    storage,
    fakeWrites: REFRESH_ACK,
    fakeReads: {
      "GET /api/dashboard": { json: NEWS_PAYLOAD },
      "GET /api/news-feed": { json: { ...NEWS_PAYLOAD, actionMessage: "", newsFeedMeta: {} } },
    },
  }, async (app) => {
    const news = app.page.locator(NEWS);
    await news.waitFor({ state: "visible", timeout: 90_000 });
    const first = (await news.locator("header small").innerText()).trim();
    assert.ok(STORIES[first], `the chart opened on ${first}, which has no fixture stories`);
    const target = first === "NVDA" ? "AAPL" : "NVDA";
    await waitForArticles(app.page, Math.min(8, STORIES[first].length));
    assert.deepEqual(await news.locator(".charts-oi-chain-dock-news article a").allInnerTexts(), expected(first),
      "only this ticker, newest first, deduped, capped at 8");
    for (const link of await news.locator(".charts-oi-chain-dock-news article a").all()) {
      assert.equal(await link.getAttribute("target"), "_blank");
      assert.equal(await link.getAttribute("rel"), "noreferrer");
    }

    await app.page.locator(`${SECTOR} button.sector-heatmap-tile[aria-label^="${target} "]`).first().click();
    await app.page.waitForFunction(([selector, symbol]) => document.querySelector(`${selector} header small`)?.textContent === symbol, [NEWS, target], { timeout: 30_000 });
    await waitForArticles(app.page, Math.min(8, STORIES[target].length));
    assert.deepEqual(await news.locator(".charts-oi-chain-dock-news article a").allInnerTexts(), expected(target));
    assert.equal(await app.page.locator(".command-context h1").innerText(), "Charts & OI", "a dock tile keeps him on the chart page");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("with live data the News dock shows only stored headlines or says there are none", async () => {
  const storage = await chartStorage({ chartsOiChainDockPanels: JSON.stringify(["news"]) });
  await withApp({ view: "Charts & OI", storage, fakeWrites: REFRESH_ACK }, async (app) => {
    const news = app.page.locator(NEWS);
    await news.waitFor({ state: "visible", timeout: 90_000 });
    const ticker = (await news.locator("header small").innerText()).trim();
    assert.match(ticker, /^[A-Z.]{1,6}$/);
    await app.page.waitForTimeout(5_000);
    const articles = await news.locator(".charts-oi-chain-dock-news article").count();
    assert.ok(articles <= 8, `${articles} headlines in the dock`);
    if (articles === 0) {
      assert.equal((await news.locator(".charts-oi-chain-dock-empty").innerText()).trim(), `No stored headlines for ${ticker} yet.`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("docked panels never push the chain off screen, at full and reduced width", async () => {
  const storage = await chartStorage({ chartsOiChainDockPanels: JSON.stringify(["news", "sector"]) });
  await withApp({ view: "Charts & OI", storage, fakeWrites: REFRESH_ACK }, async (app) => {
    await app.page.locator(SECTOR).waitFor({ state: "visible", timeout: 90_000 });
    for (const width of [1440, 1100]) {
      await app.page.setViewportSize({ width, height: 900 });
      await app.page.waitForTimeout(500);
      const tabs = app.page.locator(`${CHAIN} .charts-oi-chain-view-tabs`).first();
      await tabs.scrollIntoViewIfNeeded();
      const box = await tabs.boundingBox();
      assert.ok(box && box.width > 0 && box.x >= 0 && box.x + box.width <= width + 1, `chain tabs at ${JSON.stringify(box)} (width ${width})`);
      assert.equal(await overflowsSideways(app.page), false, `sideways scroll at ${width}px`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
