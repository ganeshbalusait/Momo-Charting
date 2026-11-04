// Quick Options - the phone-first fast option chain (2026-11-04 coverage),
// end to end in the real app. Chain reads are the options fixture (spot
// $100); research reads (/api/oi-finder) and the News POST are answered by
// the spec, so nothing here builds a live research section or scrapes news.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, chainReply, sleep } from "./options-fixtures.mjs";

const BOARD = '[data-testid="mobile-quick-options"]';
const TITLE = `${BOARD} .mobile-quick-options-title b`;
const RAIL = `${BOARD} nav[aria-label="Fast option tickers"]`;
const FAST_INPUT = `${BOARD} input[aria-label="Fast option ticker"]`;
const RESEARCH = { "GET /api/oi-finder": (read) => chainReply(read, { researchSection: new URLSearchParams(read.search).get("section") || "" }) };

const heading = async (page) => (await page.locator(TITLE).innerText()).replace(" LIVE CHAIN", "").trim();

async function ready(page) {
  await page.locator(TITLE).waitFor({ state: "visible", timeout: 60_000 });
  await page.locator('[data-testid="mobile-full-option-chain"] > header', { hasText: "Spot $100" }).waitFor({ timeout: 60_000 });
}

e2eTest("on a phone Quick Options opens on the live chain with the active rail ticker marked", async () => {
  await withApp({ view: "Quick Options", viewport: PHONE, fakeReads: { ...CHAIN_READS, ...RESEARCH } }, async (app) => {
    await ready(app.page);
    assert.equal(await app.page.locator(`${BOARD} .mobile-quick-options-title span`).innerText(), "FAST OPTIONS");
    const symbol = await heading(app.page);
    assert.match(await app.page.locator(`${BOARD} .mobile-quick-options-title small`).innerText(), / · 2 DTE$/);
    const active = app.page.locator(`${RAIL} button[aria-current="true"]`);
    assert.equal(await active.count(), 1);
    assert.equal(await active.innerText(), symbol);
    assert.equal(await app.page.locator('[data-testid="mobile-options-section-chain"]').getAttribute("aria-current"), "page");
    assert.equal(await overflowsSideways(app.page), false, "the phone page scrolls sideways");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a rail tap switches the chain; a typed ticker is upper-cased; a malformed one is ignored", async () => {
  await withApp({ view: "Quick Options", viewport: PHONE, fakeReads: { ...CHAIN_READS, ...RESEARCH } }, async (app) => {
    await ready(app.page);
    const target = (await heading(app.page)) === "SPY" ? "NVDA" : "SPY";
    await app.page.locator(`${RAIL} button`, { hasText: new RegExp(`^${target}$`) }).click();
    await app.page.locator(TITLE, { hasText: `${target} LIVE CHAIN` }).waitFor({ timeout: 30_000 });
    assert.equal(await app.page.locator(`${RAIL} button[aria-current="true"]`).innerText(), target);

    const typed = target === "SPY" ? "nvda" : "spy";
    await app.page.locator(FAST_INPUT).fill(typed);
    assert.equal(await app.page.locator(FAST_INPUT).inputValue(), typed.toUpperCase());
    await app.page.locator(FAST_INPUT).press("Enter");
    await app.page.locator(TITLE, { hasText: `${typed.toUpperCase()} LIVE CHAIN` }).waitFor({ timeout: 30_000 });

    const before = app.reads.length;
    await app.page.locator(FAST_INPUT).fill("1bad");
    await app.page.locator(`${BOARD} .mobile-quick-options-search button`, { hasText: "Go" }).click();
    await sleep(1_500);
    assert.equal(await heading(app.page), typed.toUpperCase(), "a malformed ticker must not switch the chain");
    assert.ok(!app.reads.slice(before).some((read) => /symbol=1BAD/i.test(read.search)), "a malformed ticker was requested");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Heatmap loads on its own with a loading state and a plain error, and Chain still works", async () => {
  const slowFailure = { "GET /api/oi-finder": async () => { await sleep(1_500); return { status: 400, json: { error: "Heatmap unavailable." } }; } };
  await withApp({ view: "Quick Options", viewport: PHONE, fakeReads: { ...CHAIN_READS, ...slowFailure } }, async (app) => {
    await ready(app.page);
    assert.equal(app.reads.filter((read) => read.path === "/api/oi-finder").length, 0, "research loads only when tapped");
    await app.page.locator('[data-testid="mobile-options-section-heatmap"]').click();
    await app.page.locator(`${BOARD} .mobile-options-research-loading[role="status"]`, { hasText: "Loading Heatmap" }).waitFor({ timeout: 5_000 });
    const failure = app.page.locator(`${BOARD} .mobile-quick-options-error[role="alert"]`);
    await failure.waitFor({ timeout: 15_000 });
    assert.equal(await failure.innerText(), "Unable to load heatmap research.");
    const research = app.reads.filter((read) => read.path === "/api/oi-finder");
    assert.ok(research.some((read) => new URLSearchParams(read.search).get("section") === "heatmap"));
    await app.page.locator('[data-testid="mobile-options-section-chain"]').click();
    await ready(app.page);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("News sends one POST for the ticker and shows the RELATED NEWS card", async () => {
  let symbol = "";
  const news = (write) => {
    symbol = JSON.parse(write.body || "{}").symbols?.[0] || "";
    return { json: { catalysts: [{ id: "e2e-1", symbol, headline: `${symbol} fixture headline`, url: "https://example.invalid/e2e",
      published_at: new Date().toISOString(), source: "Benzinga", sentiment: "Positive" }], catalystIndex: [] } };
  };
  await withApp({
    view: "Quick Options", viewport: PHONE, fakeReads: { ...CHAIN_READS, ...RESEARCH },
    fakeWrites: { "POST /api/news-feed": news, "POST /api/news-feed/refresh": { json: { started: false } } },
  }, async (app) => {
    await ready(app.page);
    const current = await heading(app.page);
    await app.page.locator('[data-testid="mobile-options-section-news"]').click();
    const card = app.page.locator(`${BOARD} .oi-finder-related-news`);
    await card.locator(".oi-finder-related-news-title b", { hasText: "RELATED NEWS" }).waitFor({ timeout: 30_000 });
    await card.locator("a", { hasText: `${current} fixture headline` }).waitFor({ timeout: 15_000 });
    const posts = app.writes.filter((write) => write.method === "POST" && write.path === "/api/news-feed");
    assert.equal(posts.length, 1);
    assert.deepEqual(JSON.parse(posts[0].body), { symbols: [current] });
    assert.equal(symbol, current);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the phone's bottom Options tab opens Quick Options", async () => {
  await withApp({ view: "Watchlist", viewport: PHONE, fakeReads: { ...CHAIN_READS, ...RESEARCH } }, async (app) => {
    const tab = app.page.locator('[data-testid="mobile-primary-options"]');
    await tab.waitFor({ state: "visible", timeout: 60_000 });
    await tab.click();
    await app.page.locator('[data-testid="quick-options-view"]').waitFor({ timeout: 30_000 });
    await ready(app.page);
    assert.equal(await tab.getAttribute("aria-current"), "page");
    assert.deepEqual(app.pageErrors, []);
  });
});
