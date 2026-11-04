// News Feed page (Market News Intelligence table) end to end in the real app
// (2026-11-04 coverage). The page's rows come from the dashboard poll, which is
// answered with a fixture so freshness, ratings and universes are exact; the
// 'Refresh View' POST is answered by the harness (no news site is scraped).

import assert from "node:assert/strict";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";
import test from "node:test";

const VIEW = "News Feed";
const TABLE = '[data-table-id="news-feed"]';
const MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"];
const WATCHLIST = ["AAPL", "NVDA", "AMD", "PLTR"];
const ago = (hours) => new Date(Date.now() - hours * 3_600_000).toISOString();
const row = (symbol, headline, hours, score, sentiment, tags, n) => ({
  id: `${symbol}-${n}`, symbol, headline, published_at: hours === null ? null : ago(hours), score, sentiment, tags,
  source: "Benzinga", url: `https://example.invalid/news/${n}`,
});
const ROWS = [
  row("AAPL", "Apple upgraded to Buy at Example Securities", 0.3, 3, "Strong", "Upgrade, Analyst", 1),
  row("NVDA", "Nvidia guides above the street", 3, 2, "Positive", "Earnings", 2),
  row("AMD", "AMD launches a new data-center chip", 30, 1, "Neutral", "Product", 3),
  row("PLTR", "Palantir faces a shareholder suit", 100, 0, "Negative", "Lawsuit", 4),
  row("TSLA", "Tesla deliveries beat estimates", 2, 3, "Strong", "Delivery", 5),
  row("SPY", "Index funds see record inflows", 1.5, 1, "Neutral", "Flows", 6),
  row("AAPL", "Apple undated research note", null, 1, "Neutral", "Research", 7),
];
const CANNED = row("NVDA", "Canned refresh headline from the harness", 0.1, 3, "Strong", "Upgrade", 99);

function newsApp({ extraRows = [] } = {}) {
  const state = { refreshed: false };
  const dashboard = () => ({
    json: {
      catalysts: [...(state.refreshed ? [CANNED] : []), ...ROWS, ...extraRows],
      catalystIndex: [],
      watchlist: WATCHLIST,
      mag7OptionWatchlist: MAG7,
    },
  });
  return {
    state,
    fakeReads: { "GET /api/dashboard": dashboard, "GET /api/news-feed": dashboard },
  };
}

const metric = (page, label) => page.locator('section.news-metric-strip[aria-label="News summary"] article', { hasText: label }).locator("b").innerText();
const symbols = (page) => page.locator(`${TABLE} tbody tr span.symbol-pill`).allInnerTexts();
const select = (page, label) => page.locator(".news-filter-row label", { hasText: label }).locator("select");

async function ready(page) {
  await page.locator(`${TABLE} tbody tr`).first().waitFor({ timeout: 60_000 });
}

e2eTest("News Feed opens with the USD panel, the command band, the metric strip and the table", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: VIEW, fakeReads }, async (app) => {
    await ready(app.page);
    for (const selector of ['section.forex-us-news-panel[aria-label="USD economic news today"]', ".news-command-band .news-command-title", 'section.news-metric-strip[aria-label="News summary"]']) {
      assert.equal(await app.page.locator(`section.news-workspace ${selector}`).isVisible(), true, `${selector} is missing`);
    }
    assert.match(await app.page.locator("section.news-feed-card .table-toolbar").innerText(), /WATCHLIST NEWS FEED · INFO ONLY/);
    assert.deepEqual((await symbols(app.page)).sort(), ["AAPL", "AMD", "NVDA", "PLTR"], "watchlist, last 7 days, undated hidden");
    assert.equal(await metric(app.page, "Visible headlines"), "4");
    assert.equal(await metric(app.page, "Strong catalysts"), "1");
    assert.equal(await metric(app.page, "Universe size"), "4");
    const aaplRow = app.page.locator(`${TABLE} tbody tr`, { hasText: "Apple upgraded" });
    assert.equal(await aaplRow.locator("span.news-freshness").getAttribute("class"), "news-freshness news-freshness-breaking");
    assert.equal(await aaplRow.locator("span.news-rating").getAttribute("aria-label"), "3 of 3 catalyst rating");
    assert.equal((await aaplRow.locator("span.news-type-badge").innerText()).trim(), "Upgrade");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the MAG7 tab shows only MAG7 tickers", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: VIEW, fakeReads }, async (app) => {
    await ready(app.page);
    const tabs = app.page.locator('[role="group"][aria-label="News universe"]');
    await tabs.locator("button", { hasText: "MAG7" }).click();
    assert.match(await tabs.locator("button", { hasText: "MAG7" }).getAttribute("class"), /is-active/);
    assert.match(await app.page.locator("section.news-feed-card .table-toolbar").innerText(), /MAG7 NEWS FEED · INFO ONLY/);
    const shown = await symbols(app.page);
    assert.deepEqual(shown.sort(), ["AAPL", "NVDA", "TSLA"]);
    assert.ok(shown.every((symbol) => MAG7.includes(symbol)));
    await tabs.locator("button", { hasText: "All Stored" }).click();
    assert.ok((await symbols(app.page)).includes("SPY"), "All Stored also shows names outside both lists");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("filters: last hour, 3/3 rating, then Reset back to 7 days", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: VIEW, fakeReads }, async (app) => {
    await ready(app.page);
    await select(app.page, "Freshness").selectOption("breaking");
    assert.equal(await metric(app.page, "Visible headlines"), await metric(app.page, "Breaking hour"));
    assert.deepEqual(await symbols(app.page), ["AAPL"]);
    await select(app.page, "Freshness").selectOption("week");
    await select(app.page, "Rating").selectOption("3");
    const ratings = await app.page.locator(`${TABLE} tbody tr span.news-rating`).evaluateAll((spans) => spans.map((span) => span.className));
    assert.ok(ratings.length > 0 && ratings.every((name) => name === "news-rating news-rating-3"), JSON.stringify(ratings));
    await select(app.page, "Freshness").selectOption("all");
    await app.page.locator(".news-filter-row button", { hasText: "Reset" }).click();
    assert.equal(await select(app.page, "Freshness").inputValue(), "week");
    assert.equal(await select(app.page, "Rating").inputValue(), "all");
    assert.equal(await app.page.locator('input[aria-label="News search"]').inputValue(), "");
    assert.equal(await metric(app.page, "Visible headlines"), "4");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the topbar search filters the table and says when nothing matches", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: VIEW, fakeReads }, async (app) => {
    await ready(app.page);
    const search = app.page.locator('input[aria-label="News search"]');
    await search.fill("nvda");
    await search.press("Enter");
    await app.page.waitForFunction((table) => document.querySelectorAll(`${table} tbody tr`).length === 1, TABLE);
    assert.deepEqual(await symbols(app.page), ["NVDA"]);
    await search.fill("guidance-that-does-not-exist");
    await search.press("Enter");
    await app.page.locator(`${TABLE} .no-setups`).waitFor();
    assert.equal((await app.page.locator(`${TABLE} .no-setups`).innerText()).trim(), "No news matches this search and filters.");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Refresh View sends one request for the universe and shows the new headline", async () => {
  const fixture = newsApp();
  const reply = async () => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    fixture.state.refreshed = true;
    return { json: { catalysts: [CANNED, ...ROWS], catalystIndex: [], actionMessage: "News scan completed.", newsFeedMeta: {} } };
  };
  await withApp({ view: VIEW, fakeReads: fixture.fakeReads, fakeWrites: { "POST /api/news-feed": reply } }, async (app) => {
    await ready(app.page);
    const button = app.page.locator("button.news-refresh-button");
    assert.equal((await button.innerText()).trim(), "Refresh View");
    await button.click();
    await app.page.waitForFunction(() => document.querySelector("button.news-refresh-button")?.textContent.trim() === "Refreshing");
    assert.equal(await app.page.locator("button.news-refresh-button svg.is-spinning").count(), 1);
    await app.page.locator(`${TABLE} tbody tr`, { hasText: CANNED.headline }).waitFor({ timeout: 15_000 });
    const writes = app.writes.filter((write) => write.path === "/api/news-feed");
    assert.equal(writes.length, 1);
    const body = JSON.parse(writes[0].body);
    assert.deepEqual(body.symbols, WATCHLIST);
    assert.ok(body.symbols.length <= 40);
    await app.page.waitForFunction(() => document.querySelector("button.news-refresh-button")?.textContent.trim() === "Refresh View");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused refresh shows the reason and the button comes back", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: VIEW, fakeReads, fakeWrites: { "POST /api/news-feed": { status: 503, json: { error: "News sources are busy - try again in a minute." } } } }, async (app) => {
    await ready(app.page);
    await app.page.locator("button.news-refresh-button").click();
    await app.page.locator(".live-error", { hasText: "News sources are busy - try again in a minute." }).waitFor({ timeout: 10_000 });
    await app.page.locator("button.news-refresh-button:enabled").waitFor();
    assert.equal((await app.page.locator("button.news-refresh-button").innerText()).trim(), "Refresh View");
    assert.deepEqual(app.pageErrors, []);
  });
});

// Suspected (2026-11-04, from the CSS): main.workspace is a 100dvh
// overflow:hidden flex column on every desktop page, and .news-workspace has
// no scroll rule of its own (Sectors, Learn and Settings each do), so rows
// below the first screen may be unreachable. Kept as a known failure until a
// run after the close shows otherwise.
const manyRows = Array.from({ length: 60 }, (_, n) => row("NVDA", `Nvidia headline number ${n + 1}`, 1 + n, 1, "Neutral", "Other", 100 + n));
test("desktop: a real mouse wheel reaches the last headline", {
  skip: marketHoursBlock() || false,
  todo: "suspected: .news-workspace has no overflow inside the overflow:hidden .workspace",
  timeout: 180_000,
}, async () => {
  const { fakeReads } = newsApp({ extraRows: manyRows });
  await withApp({ view: VIEW, fakeReads }, async (app) => {
    await ready(app.page);
    await select(app.page, "Freshness").selectOption("all");
    const last = app.page.locator(`${TABLE} tbody tr`).last();
    const table = await app.page.locator(TABLE).boundingBox();
    await app.page.mouse.move(table.x + table.width / 2, Math.min(table.y + 200, 800));
    for (let step = 0; step < 40; step += 1) await app.page.mouse.wheel(0, 800);
    await app.page.waitForTimeout(300);
    const box = await last.boundingBox();
    assert.ok(box && box.y + box.height <= 900 && box.y >= 0, `the last row is at ${JSON.stringify(box)} after scrolling`);
  });
});

e2eTest("phone: News Feed from the More sheet fits and its links open in a new tab", async () => {
  const { fakeReads } = newsApp();
  await withApp({ view: "Settings", viewport: PHONE, fakeReads }, async (app) => {
    const more = app.page.locator('[data-testid="mobile-primary-more"]');
    await more.waitFor({ state: "visible", timeout: 60_000 });
    await more.click();
    await app.page.locator('.mobile-more-sheet[aria-label="More destinations"] [role="menuitem"]', { hasText: "News Feed" }).click();
    await ready(app.page);
    assert.equal(await overflowsSideways(app.page), false);
    const links = app.page.locator(`${TABLE} a.news-headline-link`);
    assert.ok(await links.count() > 0);
    for (const link of await links.all()) {
      assert.equal(await link.getAttribute("target"), "_blank");
      assert.equal(await link.getAttribute("rel"), "noreferrer");
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
