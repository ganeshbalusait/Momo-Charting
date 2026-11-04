// MomX board option Spread column (2026-11-04 coverage), end to end in the
// real app on the live board (read-only). The spread classes come from
// GET /api/momx-scanner/option-spreads, which the spec answers from a map it
// fills AFTER reading which tickers are on the board, then asks the panel to
// re-poll - so every class is checked against a known ticker whatever the
// board holds today.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { sleep } from "./options-fixtures.mjs";

const VIEW = '[data-testid="momx-scanner-view"]';
const ROW = `${VIEW} tr.momx-row`;
const CLASSES = ["Avoid", "Wide", "OK", "Tight"];
const PCT = { Avoid: 21.4, Wide: 11.2, OK: 5.1, Tight: 1.8 };

function spreadService() {
  const spreads = {};
  return {
    spreads,
    fakeReads: { "GET /api/momx-scanner/option-spreads": () => ({ json: { spreads: { ...spreads }, inWindow: true, window: "10:00-15:30 ET", held: Object.keys(spreads).length } }) },
  };
}

async function boardRows(page, minimum = 1) {
  await page.waitForFunction(([selector, wanted]) => document.querySelectorAll(selector).length >= wanted, [ROW, minimum], { timeout: 120_000 });
  return page.locator(ROW).evaluateAll((rows) => rows.map((row) => /^[A-Z][A-Z0-9.\-]{0,9}/.exec(
    (row.querySelector('td[data-col="symbol"]')?.innerText || "").trim(),
  )?.[0]).filter(Boolean));
}

const repoll = (page) => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
const spreadOf = (page, symbol) => page.locator(ROW, { has: page.locator(`td[data-col="symbol"]`, { hasText: new RegExp(`^${symbol}\\b`) }) })
  .locator("td.momx-spreadcol").first();

e2eTest("every board row has a Spread cell reading Tight / OK / Wide / Avoid or -", async () => {
  await withApp({ view: "MomX Scanner" }, async (app) => {
    await boardRows(app.page);
    assert.equal((await app.page.locator(`${VIEW} .momx-head-row th[data-col="spread"]`).innerText()).trim(), "Spread");
    const texts = await app.page.locator(`${ROW} td.momx-spreadcol`).allInnerTexts();
    assert.ok(texts.length > 0);
    for (const text of texts) assert.ok([...CLASSES, "-"].includes(text.trim()), `unexpected Spread cell "${text}"`);
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner/option-spreads"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("each class lands on its ticker, sorting puts Avoid above Wide above OK above Tight", async () => {
  const service = spreadService();
  await withApp({ view: "MomX Scanner", fakeReads: service.fakeReads }, async (app) => {
    const symbols = (await boardRows(app.page, 4)).slice(0, 4);
    symbols.forEach((symbol, index) => {
      const cls = CLASSES[(index + 1) % 4];                                  // not already in sorted order
      service.spreads[symbol] = { cls, medPct: PCT[cls], medUsd: 0.12, contracts: 20, chainTs: Date.now() / 1000, reads: 3 };
    });
    await repoll(app.page);
    for (const symbol of symbols) {
      await app.page.waitForFunction(([selector, sym, cls]) => [...document.querySelectorAll(selector)].some((row) => (
        (row.querySelector('td[data-col="symbol"]')?.innerText || "").trim().startsWith(sym)
        && row.querySelector("td.momx-spreadcol")?.textContent.trim() === cls
      )), [ROW, symbol, service.spreads[symbol].cls], { timeout: 15_000 });
      const cell = spreadOf(app.page, symbol);
      assert.match(await cell.getAttribute("class"), new RegExp(`\\bis-${service.spreads[symbol].cls.toLowerCase()}\\b`));
    }
    const header = app.page.locator(`${VIEW} .momx-head-row th[data-col="spread"]`);
    for (let click = 0; click < 3 && (await header.getAttribute("aria-sort")) !== "descending"; click += 1) {
      await header.click();
      await sleep(300);
    }
    assert.equal(await header.getAttribute("aria-sort"), "descending");
    const order = (await boardRows(app.page, 4)).filter((symbol) => symbols.includes(symbol)).map((symbol) => service.spreads[symbol].cls);
    assert.deepEqual(order, CLASSES, "widest spread first on a descending sort");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping a Spread cell opens its explanation and does not open the ticker", async () => {
  const service = spreadService();
  await withApp({ view: "MomX Scanner", fakeReads: service.fakeReads }, async (app) => {
    const [symbol] = await boardRows(app.page);
    service.spreads[symbol] = { cls: "Avoid", medPct: 21.4, medUsd: 0.45, atmRule: true, contracts: 18, chainTs: Date.now() / 1000, reads: 3,
      note: "Mid orders rarely fill (16% in paper trading) and crossing the spread is costly - skip." };
    await repoll(app.page);
    const cell = spreadOf(app.page, symbol);
    await app.page.waitForFunction(([selector, sym]) => [...document.querySelectorAll(selector)].some((row) => (
      (row.querySelector('td[data-col="symbol"]')?.innerText || "").trim().startsWith(sym)
      && row.querySelector("td.momx-spreadcol")?.textContent.trim() === "Avoid"
    )), [ROW, symbol], { timeout: 15_000 });
    await cell.click();
    const dialog = app.page.locator('.momx-tag-explain[role="dialog"]');
    await dialog.waitFor({ state: "visible", timeout: 5_000 });
    assert.equal(await dialog.locator(".momx-tag-explain-head strong").innerText(), `Spread · ${symbol}`);
    const detail = await dialog.locator(".momx-tag-explain-detail").innerText();
    assert.match(detail, /^Spread: Avoid/);
    assert.match(detail, /Avoid because an at-the-money option under \$3 is \$0\.30\+ wide\./);
    assert.equal(await app.page.locator(".command-context h1").innerText(), "MomX Scanner", "the tap must not navigate away");
    await dialog.locator('button[aria-label="Close"]').click();
    await app.page.waitForFunction(() => !document.querySelector(".momx-tag-explain"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the board scrolls inside its own box with the Spread column, never the page", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE }, async (app) => {
    await boardRows(app.page);
    assert.ok(await app.page.locator(`${ROW} td.momx-spreadcol`).count() > 0);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
