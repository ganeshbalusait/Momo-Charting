// Confirm L1/L2/L3 (his rules 2026-11-05), end to end. A level needs two scanner
// publications with RVOL rising, so the board read answers an earlier scan first
// and later ones with RVOL higher each time (the panel re-reads every 15 s):
//   NVDA  RVOL up on 1h/2h/4h, 2h SQZ blank, Daily Skittles box, 3+ Setup types -> L3
//   AAPL  RVOL up the same way, every 2h-D SQZ orange, no fire              -> L1
// Then the Setup ▾ list's "Confirm L3" / "Confirm L1+" choices filter the board.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady, boardFor, rowSymbols } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const FIRST = new Date(Date.now() - 60_000).toISOString();
const LATER = () => new Date(Date.now() - 5_000).toISOString();

// RVOL must rise on EVERY scan: an unchanged value correctly drops the level.
function confirmBoard(call) {
  const board = boardFor("Mag7", "bull");
  const at = call ? LATER() : FIRST;
  board.generatedAt = board.scanCompletedAt = at;
  const value = 2.1 + call * 0.4;
  for (const row of board.rows) {
    if (row.symbol === "SPY") continue;
    row.marketDataAt = at;
    for (const tf of ["1h", "2h", "4h"]) row.rvol[tf] = { value, bg: "cyan", fg: "black" };
    // 3+ Setup types: RVOL, Star 1 and Explosive.
    row.bestRank = row.symbol === "NVDA" ? 1 : 2;
    row.m5 = { ...row.m5, pattern: "explosive" };
  }
  const nvda = board.rows.find((row) => row.symbol === "NVDA");
  nvda.sqz["2h"] = { value: "-", bg: "black", fg: "black" };
  const aapl = board.rows.find((row) => row.symbol === "AAPL");
  aapl.bestRank = 1;
  return board;
}

function reads() {
  let calls = 0;
  return { ...boardReads(), "GET /api/momx-scanner": () => ({ json: confirmBoard(calls++) }) };
}

const levelOf = (page, symbol) => page.locator(`tr.momx-row:has([data-testid='momx-symbol-link']:text-is('${symbol}')) [data-testid='momx-confirmation-level']`);

async function waitForLevels(page) {
  await levelOf(page, "NVDA").waitFor({ timeout: 45_000 });
  await levelOf(page, "AAPL").waitFor({ timeout: 10_000 });
}

async function pickRule(page, name) {
  const header = page.locator("button.momx-setup-header-button[aria-label='Choose Setup column rules']");
  await header.click();
  const picker = page.locator("[role='dialog'][aria-label='Setup column rules']");
  await picker.waitFor({ timeout: 10_000 });
  await picker.locator(".momx-setup-rule-grid label", { hasText: name }).locator("input[type='checkbox']").check();
  return picker;
}

e2eTest("Setup column shows Confirm L3 / L1 by his 2h-D SQZ rule, and the Setup ▾ Confirm choices filter the board", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads() }, async (app) => {
    await boardReady(app.page);
    await waitForLevels(app.page);
    assert.equal((await levelOf(app.page, "NVDA").innerText()).trim(), "Confirm L3");
    assert.equal((await levelOf(app.page, "AAPL").innerText()).trim(), "Confirm L1");
    let picker = await pickRule(app.page, "Confirm L3");
    await picker.getByRole("button", { name: "Done" }).click();
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 1);
    assert.deepEqual(await rowSymbols(app.page), ["NVDA"]);
    // Swap to Confirm L1+: both L1 and L3 tickers show.
    picker = await pickRule(app.page, "Confirm L3");
    await picker.locator(".momx-setup-rule-grid label", { hasText: "Confirm L3" }).locator("input[type='checkbox']").uncheck();
    await picker.locator(".momx-setup-rule-grid label", { hasText: "Confirm L1+" }).locator("input[type='checkbox']").check();
    await picker.getByRole("button", { name: "Done" }).click();
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 2);
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Setup ▾ list shows the three Confirm choices on screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: reads() }, async (app) => {
    await boardReady(app.page);
    const header = app.page.locator("button.momx-setup-header-button[aria-label='Choose Setup column rules']");
    await header.scrollIntoViewIfNeeded();
    await header.click();
    const picker = app.page.locator("[role='dialog'][aria-label='Setup column rules']");
    await picker.waitFor({ timeout: 10_000 });
    assert.match(await picker.locator("header").innerText(), /of 27 selected/);
    for (const name of ["Confirm L1+", "Confirm L2+", "Confirm L3"]) {
      const label = picker.locator(".momx-setup-rule-grid label", { hasText: name });
      await label.scrollIntoViewIfNeeded();
      const box = await label.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `${name} at ${JSON.stringify(box)}`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
