// The live ATM strip and feed status (2026-11-04 coverage), end to end in the
// real app on the fixture chain (options-fixtures.mjs: spot $100, nearest
// expiry 2 DTE, ATM 100, expected move $8.20, today +$1.25 / +1.27%).

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { CHAIN_READS, chainReply, chartStorage, sleep } from "./options-fixtures.mjs";

const STRIP = '.oi-finder-header-atm[aria-label="Current ATM summary"]';
const BANNER = '.oi-finder-live-atm[aria-label="Live price and at-the-money strike"]';

// The Current ATM strip renders inside the top command bar, which is
// display:none on every page since 5e47c8cf (2026-08-10, index.css
// ".charts-workstation .command-bar"). It still follows the chain feed, so its
// numbers are read as attached text; the visible ATM read-out is the C/P Levels
// banner below.
e2eTest("the top-bar Current ATM strip carries price, today's move, expected move and both ATM sides", async () => {
  await withApp({ view: "Quick Options", fakeReads: CHAIN_READS }, async (app) => {
    const strip = app.page.locator(STRIP);
    await strip.locator(".oi-finder-header-atm-price strong", { hasText: "$100.00" }).waitFor({ state: "attached", timeout: 60_000 });
    const text = async (selector) => (await strip.locator(selector).textContent()).trim();
    assert.match(await text(".oi-finder-header-atm-title span"), / · 2 DTE$/);
    const moves = await text(".oi-finder-header-atm-moves");
    assert.match(moves, /1\.25/);
    assert.match(moves, /1\.27%/);
    assert.match(moves, /±\$8\.20/);
    assert.match(moves, /Range \$91\.80 — \$108\.20/);
    assert.equal(await text(".oi-finder-header-atm-side.is-call span"), "100");
    assert.equal(await text(".oi-finder-header-atm-side.is-put span"), "100");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("C/P Levels shows the LIVE ticker, ATM STRIKE and EXP RANGE banner", async () => {
  // The research board also polls its own live heatmap (/api/oi-finder): a fixture too.
  const fakeReads = { ...CHAIN_READS, "GET /api/oi-finder": (read) => chainReply(read, { researchSection: "heatmap" }) };
  await withApp({ view: "Quick Options", fakeReads }, async (app) => {
    const title = app.page.locator('[data-testid="mobile-quick-options"] .mobile-quick-options-title b');
    await title.waitFor({ state: "visible", timeout: 60_000 });
    const symbol = (await title.innerText()).replace(/ live chain$/i, "").trim();
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
    // The lamp sits in the page heading, display:none on the charts workstation
    // by design since 5e47c8cf (2026-08-10, index.css ".charts-workstation
    // .charts-oi-heading"). It still tracks the feed, so its text is read attached.
    const lamp = app.page.locator('.charts-oi-heading .mag7-wall-live');
    await lamp.waitFor({ state: "attached", timeout: 90_000 });
    await app.page.waitForFunction((selector) => /^REST FALLBACK/.test(document.querySelector(selector)?.textContent || ""),
      '.charts-oi-heading .mag7-wall-live', { timeout: 60_000 });
    for (let check = 0; check < 6; check += 1) {
      assert.doesNotMatch(await lamp.textContent(), /STREAMING/);
      await sleep(500);
    }
    assert.match(await lamp.textContent(), /Snapshot /, "it says when the REST snapshot was taken");
    assert.deepEqual(app.pageErrors, []);
  });
});
