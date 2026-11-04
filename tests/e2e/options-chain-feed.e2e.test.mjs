// The option chain feed as the trader drives it (2026-11-04 coverage): the
// top-bar ticker search loads a ticker's chain, and a ticker nobody can quote
// ends in a plain error instead of an endless spinner. Runs on Quick Options
// (no chart on that page, so an unknown ticker never asks the live server
// for candles); the chain reads are the options fixture.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { CHAIN_READS, NO_CHAIN_ERROR, sleep } from "./options-fixtures.mjs";

const SEARCH = 'input[aria-label="Ticker from watchlist"]';
const TITLE = '[data-testid="mobile-quick-options"] .mobile-quick-options-title b';
const SURFACE = '[data-testid="mobile-full-option-chain"]';

async function search(page, typed) {
  const box = page.locator(SEARCH);
  await box.waitFor({ state: "visible", timeout: 60_000 });
  await box.fill(typed);
  await box.press("Enter");
}

const chainReadsFor = (app, symbol) => app.reads.filter((read) => read.path === "/api/oi-finder-chain"
  && new URLSearchParams(read.search).get("symbol") === symbol);

e2eTest("the top search loads the typed ticker's chain (typed lower-case)", async () => {
  await withApp({ view: "Quick Options", fakeReads: CHAIN_READS }, async (app) => {
    await app.page.locator(TITLE).waitFor({ state: "visible", timeout: 60_000 });
    for (const [typed, symbol] of [["nvda", "NVDA"], ["spy", "SPY"]]) {
      await search(app.page, typed);
      await app.page.locator(TITLE, { hasText: `${symbol} LIVE CHAIN` }).waitFor({ timeout: 30_000 });
      await app.page.locator(`${SURFACE} > header`, { hasText: "Spot $100" }).waitFor({ timeout: 30_000 });
      assert.ok(chainReadsFor(app, symbol).length >= 1, `no chain read for ${symbol}`);
      assert.equal(await app.page.locator('.oi-finder-header-atm-price strong').innerText(), "$100.00");
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("an unknown ticker ends in the plain error on both tabs, never an endless spinner", async () => {
  await withApp({ view: "Quick Options", fakeReads: CHAIN_READS }, async (app) => {
    await app.page.locator(TITLE).waitFor({ state: "visible", timeout: 60_000 });
    await search(app.page, "zzzzq");
    await app.page.locator(TITLE, { hasText: "ZZZZQ LIVE CHAIN" }).waitFor({ timeout: 30_000 });
    await app.page.locator(`${SURFACE} .high-oi-panel .charts-oi-empty`, { hasText: NO_CHAIN_ERROR }).waitFor({ timeout: 30_000 });
    await app.page.locator(`${SURFACE} .charts-oi-chain-view-tabs [role="tab"]`, { hasText: /^Chain$/ }).click();
    const state = app.page.locator(`${SURFACE} .charts-oi-chain-state.is-error[role="alert"]`);
    await state.waitFor({ timeout: 15_000 });
    const text = await state.innerText();
    assert.match(text, /Option chain unavailable/);
    assert.ok(text.includes(NO_CHAIN_ERROR), text);
    await sleep(3_000);
    assert.equal(await app.page.getByText("Loading live option chain").count(), 0, "still spinning on a dead ticker");
    assert.ok(chainReadsFor(app, "ZZZZQ").length >= 1);
    assert.deepEqual(app.pageErrors, []);
  });
});
