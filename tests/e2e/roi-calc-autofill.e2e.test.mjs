// ROI Calc auto-fill (2026-11-11), end to end in the real app at desktop and
// phone width.
//
// The chain read is answered with a fixed chain (spot 200, strikes 190-215,
// expected move 5.00) so every number on the page is known. The Gamma ROI POST
// is answered with no results, so the page uses its own Delta + Gamma estimate.
// Checks: every box fills itself, a typed number sticks and "↺ Live" puts the
// live one back, a ladder tap re-prices the entry, switching to puts re-picks,
// and on the phone the page really scrolls (wheel over the content) without
// sideways overflow.

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const SPOT = 200.4;
const MOVE = 5;
const day = new Date(Date.now() + 2 * 86_400_000).toISOString().slice(0, 10);
const STRIKES = [190, 195, 200, 205, 210, 215];
const price = (side, strike) => {
  const intrinsic = side === "CALL" ? Math.max(SPOT - strike, 0) : Math.max(strike - SPOT, 0);
  const extrinsic = Math.max(0.1, 2.2 - Math.abs(SPOT - strike) * 0.2);
  return Math.round((intrinsic + extrinsic) * 100) / 100;
};
const row = (side, strike) => {
  const mark = price(side, strike);
  return {
    side, strike, expiry: day, days_to_expiration: 2, mark, bid: mark - 0.05, ask: Math.round((mark + 0.05) * 100) / 100,
    last: mark, delta: side === "CALL" ? 0.45 : -0.45, gamma: 0.04, open_interest: strike === 210 || strike === 190 ? 9000 : 500,
    volume: 100, expected_move: MOVE,
  };
};
const chain = (symbol) => ({
  live: true, symbol, scannedAt: new Date().toISOString(), underlyingPrice: SPOT, todayChange: 1, todayChangePercent: 0.5,
  expiryExpectedMoves: { [day]: MOVE }, currentAtm: { expiry: day, daysToExpiration: 2, expectedMove: MOVE },
  selectedExpiryChainRows: STRIKES.flatMap((strike) => [row("CALL", strike), row("PUT", strike)]),
  callRows: [], putRows: [], tosScriptLevels: [], errors: [],
});
const FAKE = {
  fakeReads: { "GET /api/oi-finder-chain": (read) => ({ json: chain(new URLSearchParams(read.search).get("symbol") || "AAPL") }) },
  fakeWrites: { "POST /api/option-roi-estimate": { json: { results: [] } } },
};

const VIEW = '[data-testid="roi-calc-view"]';
const input = (page, label) => page.locator(`${VIEW} input[aria-label="${label}"]`);

async function waitFilled(page) {
  await page.locator(VIEW).waitFor({ state: "visible", timeout: 90_000 });
  await page.waitForFunction((selector) => {
    const field = document.querySelector(`${selector} input[aria-label="ITM option premium"]`);
    return field && field.value !== "";
  }, VIEW, { timeout: 60_000 });
}

for (const [label, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`ROI Calc fills every box from the live chain and keeps a typed number (${label})`, async () => {
    await withApp({ view: "ROI Calc", viewport, ...FAKE }, async (app) => {
      const { page } = app;
      await waitFilled(page);
      // 195 call is the first ITM strike that is not the ATM (200).
      assert.equal(await input(page, "ITM option premium").inputValue(), price("CALL", 195).toFixed(2));
      assert.equal(await input(page, "Expected move").inputValue(), "5.00");
      assert.equal(await input(page, "Entry option strike").inputValue(), "205");
      assert.equal(await input(page, "Entry option premium").inputValue(), row("CALL", 205).ask.toFixed(2));
      assert.equal(await input(page, "Target 1 stock price").inputValue(), "202.90");
      assert.equal(await input(page, "Target 2 stock price").inputValue(), "205.40");
      assert.equal(await input(page, "Target 3 stock price").inputValue(), "210.00");
      const ladder = page.locator(`${VIEW} .roi-option-ladder-row`);
      assert.deepEqual((await ladder.locator("b").allInnerTexts()).map((text) => text.trim()), ["200C", "205C", "210C", "215C"]);
      assert.match(await page.locator(`${VIEW} .roi-option-projection`).innerText(), /if \w+ tags \$205\.40/);

      // A typed number sticks; "↺ Live" puts the live one back.
      await input(page, "Expected move").fill("2.5");
      const live = page.locator(`${VIEW} .roi-calc-inputs .roi-live-reset`);
      await live.waitFor({ state: "visible" });
      assert.equal(await input(page, "Expected move").inputValue(), "2.5");
      await live.click();
      assert.equal(await input(page, "Expected move").inputValue(), "5.00");

      // A ladder tap picks the strike and re-prices the entry.
      await ladder.filter({ hasText: "210C" }).click();
      assert.equal(await input(page, "Entry option strike").inputValue(), "210");
      assert.equal(await input(page, "Entry option premium").inputValue(), row("CALL", 210).ask.toFixed(2));

      // Puts re-pick the first OTM put and mirror the targets.
      await page.locator(`${VIEW} .roi-option-side button`, { hasText: "Put" }).click();
      assert.equal(await input(page, "Entry option strike").inputValue(), "195");
      assert.equal(await input(page, "Target 2 stock price").inputValue(), "195.40");

      assert.equal(await overflowsSideways(page), false, "the page scrolls sideways");
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("ROI Calc scrolls on the phone (real wheel input over the content)", async () => {
  await withApp({ view: "ROI Calc", viewport: PHONE, ...FAKE }, async (app) => {
    const { page } = app;
    await waitFilled(page);
    const box = await page.locator(`${VIEW} .roi-calc-hero`).boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.wheel(0, 1500);
    await page.waitForFunction((selector) => document.querySelector(selector).scrollTop > 300, VIEW, { timeout: 10_000 });
    // The live-chain calculator at the bottom is reachable.
    await page.locator(`${VIEW} .roi-option-target-manual`).scrollIntoViewIfNeeded();
    assert.ok(await page.locator(`${VIEW} .roi-option-ladder-row`).first().isVisible());
  });
});
