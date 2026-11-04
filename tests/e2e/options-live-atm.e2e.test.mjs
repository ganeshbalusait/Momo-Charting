// The live ATM strip and feed status (2026-11-04 coverage), end to end in the
// real app on the fixture chain (options-fixtures.mjs: spot $100, nearest
// expiry 2 DTE, ATM 100, expected move $8.20, today +$1.25 / +1.27%).

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { CHAIN_READS, chainReply, chartStorage, sleep } from "./options-fixtures.mjs";

const STRIP = '.oi-finder-header-atm[aria-label="Current ATM summary"]';
const BANNER = '.oi-finder-live-atm[aria-label="Live price and at-the-money strike"]';

e2eTest("the top-bar Current ATM strip shows price, today's move, expected move and both ATM sides", async () => {
  await withApp({ view: "Quick Options", fakeReads: CHAIN_READS }, async (app) => {
    const strip = app.page.locator(STRIP);
    await strip.locator(".oi-finder-header-atm-price strong", { hasText: "$100.00" }).waitFor({ timeout: 60_000 });
    assert.match(await strip.locator(".oi-finder-header-atm-title span").innerText(), / · 2 DTE$/);
    const moves = await strip.locator(".oi-finder-header-atm-moves").innerText();
    assert.match(moves, /1\.25/);
    assert.match(moves, /1\.27%/);
    assert.match(moves, /±\$8\.20/);
    assert.match(moves, /Range \$91\.80 — \$108\.20/);
    assert.equal(await strip.locator(".oi-finder-header-atm-side.is-call span").innerText(), "100");
    assert.equal(await strip.locator(".oi-finder-header-atm-side.is-put span").innerText(), "100");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("C/P Levels shows the LIVE ticker, ATM STRIKE and EXP RANGE banner", async () => {
  // The research board also polls its own live heatmap (/api/oi-finder): a fixture too.
  const fakeReads = { ...CHAIN_READS, "GET /api/oi-finder": (read) => chainReply(read, { researchSection: "heatmap" }) };
  await withApp({ view: "Quick Options", fakeReads }, async (app) => {
    const title = app.page.locator('[data-testid="mobile-quick-options"] .mobile-quick-options-title b');
    await title.waitFor({ state: "visible", timeout: 60_000 });
    const symbol = (await title.innerText()).replace(" LIVE CHAIN", "").trim();
    await app.page.locator('[data-testid="mobile-options-section-levels"]').click();
    const banner = app.page.locator(BANNER);
    await banner.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await banner.locator(".oi-finder-live-atm-cell.is-price small").innerText(), `LIVE ${symbol}`);
    assert.equal(await banner.locator(".oi-finder-live-atm-cell.is-price strong").innerText(), "$100.00");
    assert.equal(await banner.locator(".oi-finder-live-atm-cell.is-atm small").innerText(), "ATM STRIKE");
    assert.equal(await banner.locator(".oi-finder-live-atm-cell.is-atm strong").innerText(), "100");
    assert.equal(await banner.locator(".oi-finder-live-atm-cell.is-range small").innerText(), "EXP RANGE (±$8.20)");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("with the live stream down the chain header says REST FALLBACK, never STREAMING", async () => {
  // CHAIN_READS answers /api/live-market-stream with 503: the SSE pump is down.
  await withApp({ view: "Charts & OI", storage: await chartStorage(), fakeReads: CHAIN_READS }, async (app) => {
    const lamp = app.page.locator('.charts-oi-heading .mag7-wall-live');
    await lamp.waitFor({ state: "visible", timeout: 90_000 });
    await app.page.waitForFunction((selector) => /^REST FALLBACK/.test(document.querySelector(selector)?.textContent || ""),
      '.charts-oi-heading .mag7-wall-live', { timeout: 60_000 });
    for (let check = 0; check < 6; check += 1) {
      assert.doesNotMatch(await lamp.innerText(), /STREAMING/);
      await sleep(500);
    }
    assert.match(await lamp.innerText(), /Snapshot /, "it says when the REST snapshot was taken");
    assert.deepEqual(app.pageErrors, []);
  });
});
