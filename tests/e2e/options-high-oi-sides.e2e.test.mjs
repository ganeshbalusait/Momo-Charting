// High OI board: the "B/S" column (2026-11-19, "I want OI, they are buyers or sellers"). The app's ESTIMATE of whether
// a wall's trades were mostly bought or mostly sold, from where trades printed between the bid and the ask while
// the app was watching (server: momx/oi_sides.py, proven in tests/test_momx_oi_sides_api.py). Here the trader sees
// it on the fixture chain at desktop and phone width: the short cell, the tooltip with the counts, a dash where
// there is no estimate, and the note that says it is not exchange data.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, chainReply, chartStorage, optionChain, symbolOf } from "./options-fixtures.mjs";

const BOUGHT = { bought: 620, sold: 380, mid: 100, unseen: 400, volume: 1500, share: 0.62, label: "mostly bought", oiChange: 2500, oiSince: "2026-11-18", day: "2026-11-19" };
const SOLD = { bought: 100, sold: 400, mid: 0, unseen: 0, volume: 600, share: 0.2, label: "mostly sold", oiChange: null, oiSince: null, day: "2026-11-19" };
const OI_ONLY = { bought: 0, sold: 0, mid: 0, unseen: 900, volume: 900, share: null, label: null, oiChange: -12400, oiSince: "2026-11-18", day: "2026-11-19" };

// The fixture's walls: calls 110 / 105 / 108, puts 90 / 95 / 92. Estimates ride on every expiry of those strikes.
function withEstimates(read) {
  const chain = optionChain(symbolOf(read).toUpperCase());
  const rows = chain.selectedExpiryChainRows.map((row) => {
    if (row.side === "CALL" && row.strike === 110) return { ...row, sideEst: BOUGHT };
    if (row.side === "PUT" && row.strike === 90) return { ...row, sideEst: SOLD };
    if (row.side === "CALL" && row.strike === 105) return { ...row, sideEst: OI_ONLY };
    return row;
  });
  return chainReply(read, { selectedExpiryChainRows: rows });
}

const READS = { ...CHAIN_READS, "GET /api/oi-finder-chain": withEstimates };
const cellOf = (board, side, strike) => board.locator(`tr.high-oi-row.is-${side}`, { has: board.page().locator(".high-oi-strike", { hasText: new RegExp(`^${strike}$`) }) })
  .locator("[data-testid='high-oi-bs']");

async function check(app, board) {
  await board.locator("tr.high-oi-em.is-spot").waitFor({ state: "visible", timeout: 90_000 });
  assert.equal((await board.locator("thead th").last().innerText()).trim(), "B/S");
  const bought = cellOf(board, "call", 110);
  assert.equal((await bought.innerText()).trim(), "62% B");
  assert.match(await bought.getAttribute("class"), /is-bought/);
  assert.equal(await bought.getAttribute("title"),
    "mostly bought: 620 bought, 380 sold of 1,500 traded; open interest up 2,500 since 2026-11-18 (2026-11-19). Estimate, not exchange data.");
  const sold = cellOf(board, "put", 90);
  assert.equal((await sold.innerText()).trim(), "80% S");
  assert.match(await sold.getAttribute("class"), /is-sold/);
  assert.equal((await cellOf(board, "call", 105).innerText()).trim(), "OI -12k");
  const none = cellOf(board, "put", 95);
  assert.equal((await none.innerText()).trim(), "-");
  assert.match(await none.getAttribute("title"), /No estimate yet/);
  assert.match(await board.locator("[data-testid='high-oi-bs-note']").innerText(), /estimate.*not exchange data/i);
  // Every wall row has the cell, and the separators still span the whole table.
  assert.equal(await board.locator("tr.high-oi-row [data-testid='high-oi-bs']").count(), await board.locator("tr.high-oi-row").count());
  assert.equal(await board.locator("tr.high-oi-em td").first().getAttribute("colspan"), "9");
  assert.deepEqual(app.pageErrors, []);
}

e2eTest("High OI board shows the bought-or-sold estimate per wall, with the counts in the tooltip (desktop)", async () => {
  await withApp({ view: "Charts & OI", storage: await chartStorage(), fakeReads: READS }, async (app) => {
    await check(app, app.page.locator('#charts-oi-option-chain .high-oi-panel[aria-label="Highest open interest walls"]'));
  });
});

e2eTest("High OI board shows the bought-or-sold estimate on a phone and still fits the screen", async () => {
  await withApp({ view: "Quick Options", viewport: PHONE, fakeReads: READS }, async (app) => {
    const board = app.page.locator('[data-testid="mobile-full-option-chain"] .high-oi-panel');
    await check(app, board);
    const box = await board.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `board at ${JSON.stringify(box)}`);
    const cell = await cellOf(board, "call", 110).boundingBox();
    assert.ok(cell && cell.x + cell.width <= PHONE.width + 1, `B/S cell cut off at ${JSON.stringify(cell)}`);
    assert.equal(await overflowsSideways(app.page), false);
  });
});
