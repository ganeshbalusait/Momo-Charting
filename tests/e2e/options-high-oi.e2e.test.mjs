// The High OI board - the chain column's default tab (2026-11-04 coverage),
// end to end in the real app on the fixture chain from options-fixtures.mjs:
// spot $100, call walls 110 / 105 / 108, put walls 90 / 95 / 92, a 1-day
// expected move of $8.20 (so +EM 108.20 / -EM 91.80).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, chartStorage, expiryIn } from "./options-fixtures.mjs";

const CHAIN = "#charts-oi-option-chain";
const BOARD = `${CHAIN} .high-oi-panel[aria-label="Highest open interest walls"]`;
const TOP = `${BOARD} input[aria-label="Walls shown per side"]`;
const PICKER = `${BOARD} .charts-oi-walls-picker[aria-label="Chart OI walls per side"]`;

const options = async (extra = {}) => ({ view: "Charts & OI", storage: await chartStorage(), fakeReads: CHAIN_READS, ...extra });

async function boardReady(page) {
  await page.locator(`${BOARD} tr.high-oi-em.is-spot`).waitFor({ state: "visible", timeout: 90_000 });
}

// The board, top to bottom: a wall's strike, or a separator's label.
const boardSequence = (page) => page.locator(`${BOARD} tbody tr`).evaluateAll((rows) => rows.map((row) => (
  row.classList.contains("high-oi-em")
    ? row.textContent.trim().split(" ")[0]
    : `${row.classList.contains("is-put") ? "P" : "C"}${row.querySelector(".high-oi-strike")?.textContent.trim()}`
)));

async function showTop(page, count) {
  await page.locator(TOP).fill(String(count));
  await page.waitForFunction(([selector, wanted]) => document.querySelectorAll(`${selector} tr.high-oi-row`).length === wanted * 2, [BOARD, count]);
}

e2eTest("High OI is the default tab: call walls above Last, put walls below, the biggest wall per side flagged", async () => {
  await withApp(await options(), async (app) => {
    await boardReady(app.page);
    assert.equal(await app.page.locator(`${CHAIN} .charts-oi-chain-view-tabs [role="tab"][aria-selected="true"]`).innerText(), "High OI");
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-em.is-spot`).innerText()).trim(), "Last 100.00");
    const sequence = await boardSequence(app.page);
    const last = sequence.indexOf("Last");
    assert.ok(sequence.slice(0, last).every((item) => !item.startsWith("P")), `a put above Last: ${sequence}`);
    assert.ok(sequence.slice(last + 1).every((item) => !item.startsWith("C")), `a call below Last: ${sequence}`);
    assert.equal(await app.page.locator(`${BOARD} tr.high-oi-row.is-call`).count(), 8, "top 8 per side by default");
    assert.equal(await app.page.locator(`${BOARD} tr.high-oi-row.is-put`).count(), 8);
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-row.is-call.is-high-oi .high-oi-strike`).innerText()).trim(), "110");
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-row.is-put.is-high-oi .high-oi-strike`).innerText()).trim(), "90");
    const stats = await app.page.locator(`${BOARD} .high-oi-stats`).innerText();
    assert.match(stats, /ExMo\s*±\$8\.20/);
    assert.match(stats, /\+EM\s*108\.20/);
    assert.match(stats, /-EM\s*91\.80/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Top 3 keeps three walls a side with +EM / Last / -EM in strike order and the nearest walls toned", async () => {
  await withApp(await options(), async (app) => {
    await boardReady(app.page);
    await showTop(app.page, 3);
    assert.deepEqual(await boardSequence(app.page), ["C110", "+EM", "C108", "C105", "Last", "P95", "P92", "-EM", "P90"]);
    // The contested call wall glows red, the active put support green - like the chart walls.
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-row.is-tone-red .high-oi-strike`).allInnerTexts()).join(), "105");
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-row.is-tone-green .high-oi-strike`).allInnerTexts()).join(), "95");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the scope button flips 'to monthly' and 'front expiry'; front shows only the nearest expiry", async () => {
  await withApp(await options(), async (app) => {
    await boardReady(app.page);
    const scope = app.page.locator(`${BOARD} .high-oi-controls button`, { hasText: /^(to monthly|front expiry)$/ });
    assert.equal(await scope.innerText(), "to monthly");
    const short = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
    const exp = async () => new Set(await app.page.locator(`${BOARD} td.high-oi-exp`).allInnerTexts());
    // To monthly: the 110 call wall lives in the last (monthly) expiry.
    assert.equal((await app.page.locator(`${BOARD} tr.high-oi-row.is-call.is-high-oi .high-oi-exp`).innerText()), short(expiryIn(16)));
    assert.ok((await exp()).size >= 2);
    await scope.click();
    await app.page.locator(`${BOARD} .high-oi-controls button`, { hasText: "front expiry" }).waitFor();
    await app.page.waitForFunction(([selector, label]) => [...document.querySelectorAll(`${selector} td.high-oi-exp`)]
      .every((cell) => cell.textContent.trim() === label), [BOARD, short(expiryIn(2))], { timeout: 10_000 });
    assert.deepEqual([...(await exp())], [short(expiryIn(2))]);
    await app.page.locator(`${BOARD} .high-oi-controls button`, { hasText: "front expiry" }).click();
    await app.page.locator(`${BOARD} .high-oi-controls button`, { hasText: "to monthly" }).waitFor();
  });
});

e2eTest("the chart-walls picker and the tab choice survive a reload", async () => {
  await withApp(await options(), async (app) => {
    await boardReady(app.page);
    await app.page.locator(`${PICKER} button`, { hasText: /^5$/ }).click();
    assert.equal(await app.page.locator(`${PICKER} button`, { hasText: /^5$/ }).getAttribute("aria-pressed"), "true");
    await app.page.locator(`${CHAIN} .charts-oi-chain-view-tabs [role="tab"]`, { hasText: /^Chain$/ }).click();
    await app.page.reload({ waitUntil: "domcontentloaded" });
    const chainTab = app.page.locator(`${CHAIN} .charts-oi-chain-view-tabs [role="tab"]`, { hasText: /^Chain$/ });
    await chainTab.waitFor({ timeout: 90_000 });
    assert.equal(await chainTab.getAttribute("aria-selected"), "true", "the chosen tab is remembered");
    await app.page.locator(`${CHAIN} .charts-oi-chain-view-tabs [role="tab"]`, { hasText: "High OI" }).click();
    await boardReady(app.page);
    assert.equal(await app.page.locator(`${PICKER} button`, { hasText: /^5$/ }).getAttribute("aria-pressed"), "true");
    assert.equal(await app.page.evaluate(() => localStorage.getItem("oiChartWallsPerSide")), "5");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the High OI board fits the screen", async () => {
  await withApp({ view: "Quick Options", viewport: PHONE, fakeReads: CHAIN_READS }, async (app) => {
    const board = app.page.locator('[data-testid="mobile-full-option-chain"] .high-oi-panel');
    await board.locator("tr.high-oi-em.is-spot").waitFor({ state: "visible", timeout: 90_000 });
    const box = await board.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `board at ${JSON.stringify(box)}`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
