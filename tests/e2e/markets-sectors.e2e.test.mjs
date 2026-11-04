// Sectors page (Robinhood/TOS heatmap) end to end in the real app
// (2026-11-04 coverage). Quotes are answered by a fixture so colours, order and
// the feed-down case are deterministic; after 16:00 ET the live quotes would be
// the day's closes anyway. Only the tile-tap test opens a chart (NVDA).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Sectors";
const PAGE = '[data-testid="sectors-view"]';
// Headline % per sector ETF: a fixed, distinct order for the High/Low sort.
const ETF_MOVE = { XLK: 1.5, XLF: -0.4, XLV: 0.2, XLY: 2.1, XLC: -1.1, XLI: 0.7, XLP: -2.3, XLE: 1.0, XLU: -0.1, XLRE: 0.4, XLB: -0.8 };
const BY_ETF = { XLK: "Technology", XLF: "Financials", XLV: "Healthcare", XLY: "Consumer Cyclical", XLC: "Communication", XLI: "Industrials", XLP: "Consumer Defensive", XLE: "Energy", XLU: "Utilities", XLRE: "Real Estate", XLB: "Materials" };
const HIGH_ORDER = Object.entries(ETF_MOVE).sort((a, b) => b[1] - a[1]).map(([etf]) => BY_ETF[etf]);

const quotes = ({ allNull = false } = {}) => (read) => {
  const symbols = (new URLSearchParams(read.search).get("symbols") || "").split(",").filter(Boolean);
  return {
    json: {
      rows: symbols.map((symbol, index) => ({
        symbol,
        lastPrice: allNull ? null : 50 + index,
        change: allNull ? null : 0.5,
        changePct: allNull ? null : (ETF_MOVE[symbol] ?? ((index % 7) - 3) * 0.6),
        volume: null,
        name: `${symbol} CORP`,
        industry: null,
        marketCap: null,
      })),
    },
  };
};

const cardTitles = (page) => page.locator(`${PAGE} article.sector-heatmap-card button.sector-heatmap-card-title b`).allInnerTexts();

async function openSectors(app) {
  const panel = app.page.locator(`${PAGE} section.sector-heatmap-page.sector-heatmap-panel[aria-label="Sector panel"]`);
  await panel.waitFor({ state: "visible", timeout: 60_000 });
  await app.page.locator(`${PAGE} button.sector-heatmap-tile.is-up`).first().waitFor({ timeout: 30_000 });
  return panel;
}

e2eTest("Sectors draws 11 cards of top stocks with the ETF move as each headline", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    const panel = await openSectors(app);
    assert.equal(await app.page.locator(`${PAGE} article.sector-heatmap-card`).count(), 11);
    assert.equal((await panel.locator("header small").first().innerText()).trim(), "11 sectors · top stocks");
    const tech = app.page.locator(`${PAGE} article.sector-heatmap-card`).filter({ hasText: "Technology" });
    assert.equal((await tech.locator("span.sector-heatmap-card-move").innerText()).trim(), "▲ 1.50%");
    assert.match(await tech.locator("span.sector-heatmap-card-move").getAttribute("class"), /is-up/);
    assert.equal(await tech.locator("button.sector-heatmap-tile").count(), 16);
    assert.match(await tech.locator('button.sector-heatmap-tile[aria-label^="NVDA "]').getAttribute("aria-label"), /^NVDA [+-]?\d+\.\d\d% in Technology$/);
    const staples = app.page.locator(`${PAGE} article.sector-heatmap-card`).filter({ hasText: "Consumer Defensive" });
    assert.equal((await staples.locator("span.sector-heatmap-card-move").innerText()).trim(), "▼ 2.30%");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("High sorts the best sector first and the choice survives a reload", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    await openSectors(app);
    const sort = app.page.locator(`${PAGE} [role="group"][aria-label="Sort sectors"]`);
    await sort.locator("button", { hasText: "High" }).click();
    assert.equal(await sort.locator("button", { hasText: "High" }).getAttribute("aria-pressed"), "true");
    assert.deepEqual(await cardTitles(app.page), HIGH_ORDER);
    await sort.locator("button", { hasText: "Low" }).click();
    assert.deepEqual(await cardTitles(app.page), [...HIGH_ORDER].reverse());
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await openSectors(app);
    assert.equal(await app.page.locator(`${PAGE} [aria-label="Sort sectors"] button`, { hasText: "Low" }).getAttribute("aria-pressed"), "true");
    assert.deepEqual(await cardTitles(app.page), [...HIGH_ORDER].reverse());
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the TOS style keeps every tile inside its card", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    const panel = await openSectors(app);
    await app.page.locator(`${PAGE} [role="group"][aria-label="Heatmap style"] button`, { hasText: "TOS" }).click();
    assert.match(await panel.getAttribute("class"), /\bis-style-tos\b/);
    await app.page.waitForTimeout(300);
    const outside = await app.page.evaluate((root) => {
      const bad = [];
      document.querySelectorAll(`${root} article.sector-heatmap-card`).forEach((card) => {
        const box = card.getBoundingClientRect();
        card.querySelectorAll("button.sector-heatmap-tile").forEach((tile) => {
          const t = tile.getBoundingClientRect();
          if (t.left < box.left - 1 || t.right > box.right + 1 || t.top < box.top - 1 || t.bottom > box.bottom + 1) bad.push(tile.getAttribute("aria-label"));
        });
      });
      return bad;
    }, PAGE);
    assert.deepEqual(outside, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a sector's own page: chips narrow the list, Map shows tiles, Back returns to the grid", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    const panel = await openSectors(app);
    await app.page.locator(`${PAGE} button.sector-heatmap-card-title`, { hasText: "Technology" }).click();
    const page = app.page.locator(`${PAGE} .sector-page`);
    await page.waitFor({ timeout: 15_000 });
    assert.match(await panel.getAttribute("class"), /\bis-sector-open\b/);
    assert.equal((await page.locator(".sector-page-head h2").innerText()).trim(), "Technology");
    assert.match(await page.locator(".sector-page-about").innerText(), /Chipmakers/);
    const itemCount = async () => Number((await page.locator(".sector-page-listhead small").innerText()).match(/(\d+) items/)[1]);
    await app.page.waitForFunction(() => Number((document.querySelector(".sector-page-listhead small")?.textContent || "").match(/\d+/)?.[0]) > 16);
    const all = await itemCount();
    assert.ok(all >= 36, `only ${all} names on the Technology page`);
    assert.equal(await page.locator("ul.sector-page-rows li button").count(), all);
    const chips = page.locator('[role="group"][aria-label="Technology sub-groups"]');
    await chips.locator("button", { hasText: "Semiconductors" }).click();
    assert.equal(await chips.locator("button", { hasText: "Semiconductors" }).getAttribute("aria-pressed"), "true");
    const semis = await itemCount();
    assert.ok(semis > 0 && semis < all, `Semiconductors shows ${semis} of ${all}`);
    await page.locator('[role="group"][aria-label="List or heatmap"] button', { hasText: "Map" }).click();
    await app.page.waitForFunction((wanted) => document.querySelectorAll(".sector-page .sector-heatmap-tiles button.sector-heatmap-tile").length === wanted, semis, { timeout: 10_000 });
    await app.page.locator(PAGE).evaluate((el) => el.scrollTo({ top: 400 }));
    await page.locator('button.sector-page-back[aria-label="Back to all sectors"]').click();
    await app.page.locator(`${PAGE} article.sector-heatmap-card`).nth(10).waitFor();
    assert.equal(await app.page.locator(`${PAGE} article.sector-heatmap-card`).count(), 11);
    await app.page.waitForFunction((root) => document.querySelector(root)?.scrollTop === 0, PAGE, { timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping a tile opens that ticker's chart", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    await openSectors(app);
    await app.page.locator(`${PAGE} button.sector-heatmap-tile[aria-label^="NVDA "]`).first().click();
    await app.page.waitForFunction(() => document.querySelector(".command-context h1")?.textContent === "Charts & OI", null, { timeout: 30_000 });
    await app.page.waitForFunction(() => document.querySelector('input[aria-label="Ticker from watchlist"]')?.value === "NVDA", null, { timeout: 30_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("feed down: an all-null answer still draws every card in grey", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes({ allNull: true }) } }, async (app) => {
    await app.page.locator(`${PAGE} article.sector-heatmap-card`).nth(10).waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(1_000);
    assert.equal(await app.page.locator(`${PAGE} article.sector-heatmap-card`).count(), 11);
    const tiles = app.page.locator(`${PAGE} button.sector-heatmap-tile`);
    assert.equal(await tiles.count(), 11 * 16, "no tile may vanish when the feed is down");
    assert.equal(await app.page.locator(`${PAGE} button.sector-heatmap-tile.is-none`).count(), 11 * 16);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("polling: the grid asks every ~8 s, a sector page adds its own names, leaving stops both", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    await openSectors(app);
    // Only the Sectors panel asks for the sector ETFs (grid) or ORCL (Technology page);
    // other pages' quote polls are not counted.
    const quoteReads = () => app.reads.filter((read) => read.path === "/api/watchlist-quotes"
      && /(^|,)(XLK|ORCL)(,|$)/.test(new URLSearchParams(read.search).get("symbols") || ""));
    const start = quoteReads().length;
    await app.page.waitForTimeout(17_000);
    const grid = quoteReads().length - start;
    assert.ok(grid >= 2 && grid <= 3, `${grid} grid polls in 17 s`);
    await app.page.locator(`${PAGE} button.sector-heatmap-card-title`, { hasText: "Technology" }).click();
    await app.page.waitForTimeout(9_000);
    assert.ok(quoteReads().some((read) => /(^|,)ORCL(,|$)/.test(new URLSearchParams(read.search).get("symbols") || "")), "the open page never quoted its extra names");
    await app.page.locator('button.nav-item[title="Settings"]').click();
    await app.page.waitForTimeout(1_000);
    const left = quoteReads().length;
    await app.page.waitForTimeout(10_000);
    assert.equal(quoteReads().length, left, "Sectors kept polling after the page was left");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: Sectors from the bottom bar fits the width and scrolls to Materials", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { "GET /api/watchlist-quotes": quotes() } }, async (app) => {
    const tab = app.page.locator('[data-testid="mobile-primary-sectors"]');
    await tab.waitFor({ state: "visible", timeout: 60_000 });
    await tab.click();
    await openSectors(app);
    assert.equal(await overflowsSideways(app.page), false);
    const materials = app.page.locator(`${PAGE} article.sector-heatmap-card`).filter({ hasText: "Materials" });
    const inView = async () => {
      const box = await materials.boundingBox();
      return Boolean(box && box.y < PHONE.height && box.y + 40 > 0);
    };
    await app.page.mouse.move(PHONE.width / 2, PHONE.height / 2);
    for (let step = 0; step < 40 && !(await inView()); step += 1) {
      await app.page.mouse.wheel(0, 600);
      await app.page.waitForTimeout(100);
    }
    assert.ok(await inView(), "a real scroll never reached the Materials card");
    assert.deepEqual(app.pageErrors, []);
  });
});
