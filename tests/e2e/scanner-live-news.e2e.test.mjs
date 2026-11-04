// MomX Scanner -> NEWS, NEWS ▲, the News popover with the AI "why", and the
// Headlines box, end to end on the live Mag7 board.
//
// The stored headlines are a fixture (AAPL: a story Jev reads bullish; NVDA: a
// story with no Jev verdict), so counts and filters can be checked exactly.
// The refresh POST is answered by the harness - no news site is scraped - and
// every /api/ai/ read is faked: an AI call costs money.

import test from "node:test";
import assert from "node:assert/strict";

import { PHONE, e2eTest, marketHoursBlock, withApp } from "./harness.mjs";

const SCANNER = {
  "momx.scanner.activeList": "Mag7",
  "momx.scanner.direction": "bull",
  "momx.scanner.activeView": "live",
  "momx-auto-cards-on": "0",
};
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";
const NEWS = "[data-testid='momx-news-toggle']";
const TONE = "[data-testid='momx-news-tone-toggle']";
const REFRESH = "POST /api/news-feed/refresh";

function storedNews() {
  const hourAgo = new Date(Date.now() - 60 * 60 * 1000).toISOString();
  const item = (symbol, headline, jev) => ({
    symbol, headline, publishedAt: hourAgo, source: "Reuters", via: "Yahoo Finance",
    url: `https://example.com/${symbol}`, sentiment: "Strong", score: 3, summary: `${symbol} teaser`,
    relatedSymbols: [symbol], ...(jev ? { jev } : {}),
  });
  const aapl = item("AAPL", "Apple beats on iPhone sales", { label: "bullish", tone: "up", text: "Bullish" });
  const nvda = item("NVDA", "Nvidia schedules its investor day", null);
  return {
    json: {
      symbols: ["AAPL", "NVDA"], latest: { AAPL: aapl, NVDA: nvda }, feed: [aapl, nvda], count: 2, lookbackDays: 7,
      refreshing: false, refreshError: null, storeError: null,
      newsFeedMeta: { symbolsScanned: 7, headlinesRefreshed: 2, headlinesStored: 2, sources: [{ name: "yahoo_rss", label: "Yahoo Finance RSS", status: "ok", items: 2 }] },
    },
  };
}

const ANSWER = {
  available: true, symbol: "AAPL", changePct: 1.2, headlinesConsidered: 1, summary: "Apple beat on iPhone sales.",
  category: "EARNINGS", confidence: "high", direction: "bullish", headline: { title: "Apple beats on iPhone sales" },
  reason: "Explained from recent headlines.", provider: "fake",
};
// Exactly what agents/ai_endpoints answers while the model call is still running.
const PLACEHOLDER = {
  available: false, pending: true, provider: null, generatedAt: new Date().toISOString(),
  reason: "The AI is writing this now. It appears here on its own in a few seconds -- no need to do anything.",
};

async function boardWithNews(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(1).waitFor({ timeout: 120_000 });
  await page.locator("td.momx-newscol.is-openable[aria-label='Open news for AAPL']").waitFor({ timeout: 30_000 });
}

const symbolsOnScreen = async (page) =>
  (await page.locator(`${ROWS} .momx-symbol`).allInnerTexts()).map((text) => text.trim()).filter(Boolean);

e2eTest("NEWS shows exactly the counted rows, opens the Headlines box and asks for one capped refresh", async () => {
  await withApp({
    view: "MomX Scanner", storage: SCANNER,
    fakeReads: { "GET /api/news-feed/latest": storedNews },
    fakeWrites: { [REFRESH]: (write) => ({ json: { started: true, running: true, symbols: JSON.parse(write.body).symbols } }) },
  }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    const board = await symbolsOnScreen(page);
    const counted = Number((await page.locator(`${NEWS} .momx-news-count`).innerText()).trim());
    assert.ok(counted >= 2, `NEWS counted ${counted}`);
    await page.locator(NEWS).click();
    await page.locator(`${NEWS}[aria-pressed='true']`).waitFor({ timeout: 5_000 });
    const shown = await symbolsOnScreen(page);
    assert.equal(shown.length, counted, "the count before pressing is the rows after");
    assert.ok(shown.includes("AAPL") && shown.includes("NVDA"));
    const box = page.locator("[data-testid='momx-newsfeed'][aria-label='News headlines']");
    await box.waitFor({ state: "visible", timeout: 10_000 });
    assert.match(await box.locator("[data-testid='momx-newsfeed-health']").innerText(), /\S/);
    const refreshes = app.writes.filter((write) => write.path === "/api/news-feed/refresh");
    assert.equal(refreshes.length, 1);
    const asked = JSON.parse(refreshes[0].body).symbols;
    assert.ok(asked.length > 0 && asked.length <= 60, `refresh asked for ${asked.length} tickers`);
    assert.ok(asked.every((symbol) => board.includes(symbol)), "only board tickers are scraped");

    await page.locator(NEWS).click();                         // off again: the whole board back
    await page.locator(`${NEWS}[aria-pressed='false']`).waitFor({ timeout: 5_000 });
    assert.equal((await symbolsOnScreen(page)).length, board.length);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("NEWS ▲ keeps only rows whose news Jev reads bullish", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/news-feed/latest": storedNews } }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    assert.match(await page.locator(TONE).innerText(), /NEWS ▲/);
    const counted = Number((await page.locator(`${TONE} .momx-news-count`).innerText()).trim());
    await page.locator(TONE).click();
    await page.locator(`${TONE}[aria-pressed='true']`).waitFor({ timeout: 5_000 });
    const shown = await symbolsOnScreen(page);
    assert.equal(shown.length, counted);
    assert.ok(shown.includes("AAPL") && !shown.includes("NVDA"), `NEWS ▲ showed ${shown}`);
    for (const arrow of await page.locator(`${ROWS} .momx-newscol-jev`).allInnerTexts()) assert.equal(arrow.trim(), "▲");
    assert.equal(await page.locator(`${ROWS} .momx-newscol-jev`).count(), shown.length);
    assert.equal(app.writes.length, 0, "NEWS ▲ is a filter, not a scrape");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a News cell opens the headline and the AI's answer; the overlay closes it", async () => {
  await withApp({
    view: "MomX Scanner", storage: SCANNER,
    fakeReads: { "GET /api/news-feed/latest": storedNews, "GET /api/ai/catalyst": { json: ANSWER } },
  }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    await page.locator("td.momx-newscol.is-openable[aria-label='Open news for AAPL']").click();
    const pop = page.locator(".momx-news-pop[role='dialog'][aria-label='News for AAPL']");
    await pop.waitFor({ state: "visible", timeout: 5_000 });
    assert.equal((await pop.locator(".momx-news-pop-symbol").innerText()).trim(), "AAPL");
    assert.match(await pop.innerText(), /Apple beats on iPhone sales/);
    await pop.locator("button.momx-news-why").click();
    await pop.locator(".momx-news-ai b", { hasText: "Apple beat on iPhone sales." }).waitFor({ timeout: 10_000 });
    assert.match(await pop.locator(".momx-news-ai small").innerText(), /EARNINGS · confidence high · 1 headline read/);
    const asked = app.reads.filter((read) => read.path === "/api/ai/catalyst");
    assert.equal(asked.length, 1);
    assert.equal(new URLSearchParams(asked[0].search).get("symbol"), "AAPL");
    await page.locator(".momx-news-overlay").click({ position: { x: 4, y: 4 } });
    await pop.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

test("the AI's 'writing it now' placeholder is waited out until the answer arrives", {
  skip: marketHoursBlock() || false,
  todo: "BUG: the server placeholder says available:false, so askWhy stops at once and shows 'No answer.' (see tests/test_cov_scanner-live_news_api.py xfail)",
  timeout: 180_000,
}, async () => {
  let asks = 0;
  await withApp({
    view: "MomX Scanner", storage: SCANNER,
    fakeReads: {
      "GET /api/news-feed/latest": storedNews,
      "GET /api/ai/catalyst": () => { asks += 1; return { json: asks === 1 ? PLACEHOLDER : ANSWER }; },
    },
  }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    await page.locator("td.momx-newscol.is-openable[aria-label='Open news for AAPL']").click();
    const pop = page.locator(".momx-news-pop[role='dialog'][aria-label='News for AAPL']");
    await pop.locator("button.momx-news-why").click();
    await pop.locator(".momx-news-ai b", { hasText: "Apple beat on iPhone sales." }).waitFor({ timeout: 15_000 });
    assert.ok(asks >= 2);
  });
});

e2eTest("the Headlines search narrows to a ticker and says so when nothing matches", async () => {
  await withApp({
    view: "MomX Scanner", storage: SCANNER,
    fakeReads: { "GET /api/news-feed/latest": storedNews },
    fakeWrites: { [REFRESH]: { json: { started: true, running: true, symbols: [] } } },
  }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    await page.locator(NEWS).click();
    const box = page.locator("[data-testid='momx-newsfeed']");
    await box.locator(".momx-newsfeed-group").first().waitFor({ timeout: 10_000 });
    assert.ok(await box.locator(".momx-newsfeed-group").count() >= 2);
    const search = box.locator("[data-testid='momx-newsfeed-search'][aria-label='Search the headlines by ticker or word']");
    await search.fill("nvda");
    assert.deepEqual((await box.locator(".momx-newsfeed-symbol").allInnerTexts()).map((t) => t.replace(/\d+$/, "").trim()), ["NVDA"]);
    await search.fill("iphone");
    assert.deepEqual((await box.locator(".momx-newsfeed-symbol").allInnerTexts()).map((t) => t.replace(/\d+$/, "").trim()), ["AAPL"]);
    await search.fill("zzqqxx");
    assert.match(await box.locator(".momx-newsfeed-empty").innerText(), /No headline for "zzqqxx" on this board/);
    await box.locator("button[aria-label='Clear the search']").click();
    assert.ok(await box.locator(".momx-newsfeed-group").count() >= 2);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the News popover fits on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER, fakeReads: { "GET /api/news-feed/latest": storedNews } }, async (app) => {
    const { page } = app;
    await boardWithNews(page);
    const cell = page.locator("td.momx-newscol.is-openable[aria-label='Open news for NVDA']");
    await cell.scrollIntoViewIfNeeded();
    await cell.tap();
    const pop = page.locator(".momx-news-pop[aria-label='News for NVDA']");
    await pop.waitFor({ state: "visible", timeout: 5_000 });
    const box = await pop.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `popover at ${JSON.stringify(box)} is cut off`);
    await pop.locator("button[aria-label='Close']").tap();
    await pop.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
