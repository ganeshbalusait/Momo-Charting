// MomX Scanner -> the live Price and Spread columns, end to end.
// Price ticks from the live stream (read-only); the Spread classes are a
// fixture for AAPL (Tight) and NVDA (Avoid) so the cells and the tap-to-explain
// box can be checked exactly. Every other row must print "-", never junk.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = {
  "momx.scanner.activeList": "Mag7",
  "momx.scanner.direction": "bull",
  "momx.scanner.activeView": "live",
  "momx-auto-cards-on": "0",
};
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";

function spreads() {
  const chainTs = Math.floor(Date.now() / 1000) - 600;
  return {
    json: {
      inWindow: true,
      window: "10:00-15:30 ET",
      spreads: {
        AAPL: { cls: "Tight", medPct: 1.2, medUsd: 0.03, atmRule: false, contracts: 20, reads: 2, chainTs,
          expiries: { weekly: "2026-11-06", monthly: "2026-11-20" }, note: "Mid orders usually fill (72% filled at the mid in paper trading)." },
        NVDA: { cls: "Avoid", medPct: 17.5, medUsd: 0.41, atmRule: true, contracts: 18, reads: 3, chainTs },
      },
    },
  };
}

async function wholeBoard(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(1).waitFor({ timeout: 120_000 });
}

const rowOf = (page, symbol) => page.locator(ROWS, { has: page.locator(".momx-symbol", { hasText: new RegExp(`^${symbol}$`) }) }).first();

e2eTest("every Price and Spread cell shows a value or '-', and the fixture classes land on their rows", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/option-spreads": spreads } }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    await rowOf(page, "AAPL").waitFor({ timeout: 30_000 });
    await rowOf(page, "NVDA").waitFor({ timeout: 30_000 });

    const prices = (await page.locator(`${ROWS} td.momx-pricecol`).allInnerTexts()).map((t) => t.trim());
    assert.ok(prices.length >= 2);
    for (const text of prices) assert.match(text, /^(\d+\.\d{2,4})?$/, `price cell "${text}"`);
    assert.ok(prices.some((text) => text !== ""), "at least one row shows a price");

    const aapl = rowOf(page, "AAPL").locator("td.momx-spreadcol");
    await page.waitForFunction(() => [...document.querySelectorAll("td.momx-spreadcol")].some((td) => td.textContent.trim() === "Tight"), null, { timeout: 15_000 });
    assert.equal((await aapl.innerText()).trim(), "Tight");
    assert.match(await aapl.getAttribute("class"), /\bis-tight\b/);
    const nvda = rowOf(page, "NVDA").locator("td.momx-spreadcol");
    assert.equal((await nvda.innerText()).trim(), "Avoid");
    assert.match(await nvda.getAttribute("class"), /\bis-avoid\b/);
    const texts = (await page.locator(`${ROWS} td.momx-spreadcol`).allInnerTexts()).map((t) => t.trim());
    for (const text of texts) assert.ok(["Tight", "OK", "Wide", "Avoid", "-"].includes(text), `spread cell "${text}"`);
    assert.equal(texts.filter((t) => t !== "-").length, 2, "only the two fixture tickers carry a class");
    for (const text of [...prices, ...texts]) assert.doesNotMatch(text, /NaN|undefined|null/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping a Spread cell explains the class in plain words", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/option-spreads": spreads } }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const cell = rowOf(page, "NVDA").locator("td.momx-spreadcol");
    await page.waitForFunction(() => [...document.querySelectorAll("td.momx-spreadcol")].some((td) => td.textContent.trim() === "Avoid"), null, { timeout: 30_000 });
    await cell.click();
    const box = page.locator(".momx-tag-explain[role='dialog'][aria-label='What Spread · NVDA means']");
    await box.waitFor({ state: "visible", timeout: 10_000 });
    const text = await box.innerText();
    assert.match(text, /Spread: Avoid/);
    assert.match(text, /Median spread 17\.5% of the mid \(\$0\.41\)/);
    assert.match(text, /at-the-money option under \$3 is \$0\.30\+ wide/);
    await box.locator("button[aria-label='Close']").click();
    await box.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone a tapped Spread cell explains itself on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER, fakeReads: { "GET /api/momx-scanner/option-spreads": spreads } }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const cell = rowOf(page, "AAPL").locator("td.momx-spreadcol");
    await page.waitForFunction(() => [...document.querySelectorAll("td.momx-spreadcol")].some((td) => td.textContent.trim() === "Tight"), null, { timeout: 30_000 });
    await cell.scrollIntoViewIfNeeded();
    await cell.tap();
    const box = page.locator(".momx-tag-explain-box");
    await box.waitFor({ state: "visible", timeout: 10_000 });
    const rect = await box.boundingBox();
    assert.ok(rect && rect.x >= 0 && rect.x + rect.width <= PHONE.width, `explain box at ${JSON.stringify(rect)} is cut off`);
    assert.match(await box.innerText(), /Spread: Tight/);
    assert.deepEqual(app.pageErrors, []);
  });
});
