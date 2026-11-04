// ROI Calc page (2026-11-04 coverage), end to end in the real app.
// Quick rule: ITM premium / 2 (100% ROI) and / 3 (200% ROI) against the
// expected move. Option Target ROI: the options fixture chain (spot $100,
// nearest expiry 2 DTE, marks from options-fixtures.mjs markFor). The server
// estimate POST is left to the harness's block (503), which proves the page
// falls back to its own delta + gamma estimate.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, markFor } from "./options-fixtures.mjs";

const PAGE = '[data-testid="roi-calc-view"]';
const MANUAL = `${PAGE} .roi-option-target-manual[aria-label="Manual option target ROI calculator"]`;
const card = (page, label) => page.locator(`${PAGE} .roi-calc-target`, { hasText: label });
const options = (extra = {}) => ({ view: "ROI Calc", fakeReads: CHAIN_READS, ...extra });

async function chainReady(page) {
  await page.locator(`${MANUAL} .roi-option-live-price.is-live strong`, { hasText: "$100.00" }).waitFor({ timeout: 60_000 });
}

e2eTest("the quick rule's defaults: 100% ROI is tight by $0.04, 200% ROI passes at $1.03", async () => {
  await withApp(options(), async (app) => {
    await app.page.locator(PAGE).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(`${PAGE} input[aria-label="ITM option premium"]`).inputValue(), "3.09");
    assert.equal(await app.page.locator(`${PAGE} input[aria-label="Expected move"]`).inputValue(), "1.50");
    const hundred = card(app.page, "100% ROI");
    assert.match(await hundred.getAttribute("class"), /\bis-tight\b/);
    assert.equal(await hundred.locator("header b").innerText(), "TIGHT / NO PASS");
    assert.equal(await hundred.locator("strong").innerText(), "$1.55");
    assert.match(await hundred.innerText(), /Short of required move\s*\$0\.04/);   // 1.50 vs 1.545
    const twoHundred = card(app.page, "200% ROI");
    assert.match(await twoHundred.getAttribute("class"), /\bis-pass\b/);
    assert.equal(await twoHundred.locator("header b").innerText(), "PASSES");
    assert.equal(await twoHundred.locator("strong").innerText(), "$1.03");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a bigger expected move passes both, and a zero move passes neither", async () => {
  await withApp(options(), async (app) => {
    await app.page.locator(PAGE).waitFor({ timeout: 60_000 });
    const move = app.page.locator(`${PAGE} input[aria-label="Expected move"]`);
    await move.fill("2.00");
    await app.page.waitForFunction((selector) => document.querySelectorAll(`${selector} .roi-calc-target.is-pass`).length === 2, PAGE);
    await move.fill("0");
    await app.page.waitForFunction((selector) => document.querySelectorAll(`${selector} .roi-calc-target.is-tight`).length === 2, PAGE);
  });
});

e2eTest("current P&L uses the live Mark of the entered strike, per contract and in %", async () => {
  await withApp(options(), async (app) => {
    await chainReady(app.page);
    await app.page.locator(`${MANUAL} input[aria-label="Entry option strike"]`).fill("103");
    await app.page.locator(`${MANUAL} input[aria-label="Entry option premium"]`).fill("2.00");
    const mark = markFor("CALL", 103, 2);                                  // $2.90 on the fixture
    const pnl = app.page.locator(`${MANUAL} .roi-option-current-pnl`);
    await pnl.locator("strong", { hasText: "+$90.00" }).waitFor({ timeout: 10_000 });
    assert.match(await pnl.getAttribute("class"), /\bis-positive\b/);
    assert.equal(await pnl.locator("small").innerText(), `+${(((mark - 2) / 2) * 100).toFixed(1)}% · live Mark $${mark.toFixed(2)}`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a Call target between spot and strike gets chain ROI and Gamma ROI; a wrong-side target gets the hint", async () => {
  await withApp(options(), async (app) => {
    await chainReady(app.page);
    await app.page.locator(`${MANUAL} input[aria-label="Entry option strike"]`).fill("105");
    await app.page.locator(`${MANUAL} input[aria-label="Entry option premium"]`).fill("2.00");
    await app.page.locator(`${MANUAL} input[aria-label="Target 1 stock price"]`).fill("103");
    const first = app.page.locator(`${MANUAL} .roi-option-target-card`).first();
    await app.page.locator(`${MANUAL} .roi-option-target-card.is-ready`).first().waitFor({ timeout: 10_000 });
    // Chain ROI: the 103 call's live Mark against the 2.00 fill.
    const expected = Math.round(((markFor("CALL", 103, 2) - 2) / 2) * 100);
    assert.equal(await first.locator("header strong").innerText(), `${expected}% ROI`);
    // Gamma ROI renders even though the server estimate was blocked (local delta + gamma).
    await first.locator(".roi-option-gamma-result.is-ready strong", { hasText: /%$/ }).waitFor({ timeout: 10_000 });
    assert.ok(app.writes.some((write) => write.path === "/api/option-roi-estimate"), "the page asked the server first");
    await app.page.locator(`${MANUAL} input[aria-label="Target 1 stock price"]`).fill("95");
    await first.locator("small", { hasText: "For an OTM call exit, enter a stock target between the current price and the entry strike." }).waitFor({ timeout: 10_000 });
    assert.equal(await first.locator(".roi-option-gamma-result strong").innerText(), "--");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Put side switches the contracts and the direction rule", async () => {
  await withApp(options(), async (app) => {
    await chainReady(app.page);
    const put = app.page.locator(`${MANUAL} .roi-option-side[aria-label="Option side"] button`, { hasText: "Put" });
    await put.click();
    assert.match(await put.getAttribute("class"), /\bis-active\b.*\bis-put\b|\bis-put\b.*\bis-active\b/);
    await app.page.locator(`${MANUAL} input[aria-label="Entry option strike"]`).fill("95");
    await app.page.locator(`${MANUAL} input[aria-label="Entry option premium"]`).fill("1.00");
    await app.page.locator(`${MANUAL} input[aria-label="Target 1 stock price"]`).fill("97");
    const first = app.page.locator(`${MANUAL} .roi-option-target-card`).first();
    await app.page.locator(`${MANUAL} .roi-option-target-card.is-ready`).first().waitFor({ timeout: 10_000 });
    assert.match(await first.locator(".roi-option-fetched-mark small").innerText(), /^97 put · Δ /);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the calculator's inputs and cards fit", async () => {
  await withApp(options({ viewport: PHONE }), async (app) => {
    await chainReady(app.page);
    for (const selector of [`${PAGE} input[aria-label="ITM option premium"]`, `${MANUAL} input[aria-label="Target 1 stock price"]`]) {
      const box = await app.page.locator(selector).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} at ${JSON.stringify(box)}`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
