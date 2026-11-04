// The ticker-tagged news store as the trader sees it (2026-11-04 coverage):
// MomX Scanner -> NEWS shows the stored headlines and one health line that
// says how many sources answered and whether the live push feeds are up.
// Pressing NEWS asks for a refresh; that POST is answered by the harness, so
// no news site is scraped. One spec uses a fixture for exact wording, one
// reads the live store and checks only the structure.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";

const VIEW = "MomX Scanner";
const BOX = '[data-testid="momx-newsfeed"]';
const HEALTH = '[data-testid="momx-newsfeed-health"]';
const REFRESH_ACK = { "POST /api/news-feed/refresh": { json: { started: false, reason: "e2e", running: false } } };
const ago = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
const item = (symbol, headline, minutes) => ({
  symbol, headline, url: `https://example.invalid/store/${symbol}/${minutes}`, source: "Benzinga", via: "Alpaca live",
  publishedAt: ago(minutes), storedAt: ago(minutes), sentiment: "Positive", score: 2, tags: "Earnings", summary: "", relatedSymbols: [symbol],
});
const LATEST = {
  symbols: ["NVDA", "AAPL"],
  feed: [item("NVDA", "Nvidia raises its data-center outlook", 4), item("AAPL", "Apple sets its event date", 90)],
  latest: {},
  count: 2,
  refreshing: false,
  newsFeedMeta: {
    sources: [
      { name: "yahoo_rss", label: "Yahoo Finance RSS", status: "ok" },
      { name: "finviz", label: "Finviz", status: "blocked" },
    ],
  },
  live: {
    alpaca: { status: "live", received: 4, stored: 4 },
    wires: { status: "live", received: 1, stored: 1 },
    edgar: { status: "reconnecting", received: 0, stored: 0, error: "HTTPError: 403" },
  },
};

async function openNews(app) {
  const toggle = app.page.locator('[data-testid="momx-news-toggle"]');
  await toggle.waitFor({ state: "visible", timeout: 90_000 });
  await toggle.click();
  assert.equal(await toggle.getAttribute("aria-pressed"), "true");
  await app.page.locator(BOX).waitFor({ state: "visible", timeout: 30_000 });
}

e2eTest("MomX NEWS: the health line names the sources that answered and the live feeds that are down", async () => {
  await withApp({ view: VIEW, fakeWrites: REFRESH_ACK, fakeReads: { "GET /api/news-feed/latest": { json: LATEST } } }, async (app) => {
    await openNews(app);
    await app.page.waitForFunction((selector) => /sources OK/.test(document.querySelector(selector)?.textContent || ""), HEALTH, { timeout: 30_000 });
    assert.equal((await app.page.locator(HEALTH).innerText()).trim(),
      "2 headlines · 1/2 sources OK · Unavailable: Finviz · live feeds 2/3 (SEC down)");
    assert.match(await app.page.locator(HEALTH).getAttribute("class"), /\bis-down\b/);
    assert.equal(app.writes.filter((write) => write.path === "/api/news-feed/refresh").length >= 1, true, "pressing NEWS asks for fresh headlines");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("MomX NEWS against the live store: a well-formed health line, and links open in a new tab", async () => {
  await withApp({ view: VIEW, fakeWrites: REFRESH_ACK }, async (app) => {
    await openNews(app);
    await app.page.waitForFunction((selector) => /headlines? · /.test(document.querySelector(selector)?.textContent || ""), HEALTH, { timeout: 30_000 });
    const health = (await app.page.locator(HEALTH).innerText()).trim();
    assert.match(health, /^\d+ headlines? · (refreshing the sources now|sources not refreshed yet|last refresh failed: .+|\d+\/\d+ sources OK)/);
    assert.ok(!/undefined|NaN/.test(health), health);
    for (const link of await app.page.locator(`${BOX} .momx-newsfeed-item a`).all()) {
      assert.equal(await link.getAttribute("target"), "_blank");
      assert.match(await link.getAttribute("rel"), /noreferrer/);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
