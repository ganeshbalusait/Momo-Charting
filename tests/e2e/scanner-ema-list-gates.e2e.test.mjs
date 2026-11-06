// The 21 / 50 EMA Touch lists run his "Momox_2NEW!" TOS ALL-gates (2026-11-05,
// "EMA lists only"): VOLUME 4h EXT >= 1.0% and CLOSE 1h EXT >= 0.5% vs 2 bars
// ago, where every other list keeps 0.5% / 0.3%. End to end against the LIVE
// scanner (reads only): the trader opens the EMA list from the picker; every
// ticker it shows as a scan match is also a Watchlist match, and every
// Watchlist match it drops carries the stricter-gate reason.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE } from "./scanner-board-fixtures.mjs";

const PICKER = ".momx-picker[role='dialog'][aria-label='Watchlists']";

async function openEmaList(page) {
  await page.locator(".momx-listbtn[aria-haspopup='true']").waitFor({ timeout: 60_000 });
  await page.locator(".momx-listbtn[aria-haspopup='true']").click();
  const picker = page.locator(PICKER);
  await picker.waitFor({ timeout: 15_000 });
  const pick = picker.locator(".momx-picker-pick").filter({ hasText: /^21 EMA Touch / }).first();
  const name = (await pick.locator(".momx-picker-name").textContent()).trim();
  await pick.click();
  await page.waitForFunction((want) => document.querySelector(".momx-listbtn-name")?.textContent.trim() === want,
    name, { timeout: 30_000 });
  return name;
}

async function board(page, list) {
  return page.evaluate(async (name) => {
    const res = await fetch(`/api/momx-scanner?list=${encodeURIComponent(name)}&matches=0`, { credentials: "include" });
    return { status: res.status, body: await res.json() };
  }, list);
}

const bySymbol = (body) => Object.fromEntries([...(body.rows || []), ...(body.rest || [])].map((r) => [r.symbol, r]));

for (const viewport of [undefined, PHONE]) {
  e2eTest(`EMA list matches pass the stricter 1.0% / 0.5% gates (${viewport ? "phone" : "desktop"})`, async () => {
    await withApp({ view: "MomX Scanner", storage: BOARD_STORAGE, viewport }, async (app) => {
      const name = await openEmaList(app.page);
      const ema = await board(app.page, name);
      const watch = await board(app.page, "Watchlist");
      assert.equal(ema.status, 200);
      assert.equal(watch.status, 200);
      const emaRows = bySymbol(ema.body);
      const watchRows = bySymbol(watch.body);
      assert.ok(Object.keys(emaRows).length > 0, `${name} board is empty`);
      for (const [symbol, row] of Object.entries(emaRows)) {
        const other = watchRows[symbol];
        if (!other || other.scanPass === undefined) continue;
        // Boards are built minutes apart; compare only where both read the same bars.
        if (row.marketDataAt && other.marketDataAt && row.marketDataAt !== other.marketDataAt) continue;
        if (row.scanPass) assert.ok(other.scanPass, `${symbol}: EMA-list match but not a Watchlist match`);
        if (other.scanPass && !row.scanPass) {
          assert.ok((row.scanReasons || []).some((r) => r.startsWith("blocked:pricechange:")),
            `${symbol}: dropped from ${name} without a Price_Change reason: ${row.scanReasons}`);
        }
      }
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 480_000 });   // the live app took ~150 s just to open on the loaded PC (2026-11-06)
}
