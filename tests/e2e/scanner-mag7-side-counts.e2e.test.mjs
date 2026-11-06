// Mag7 counts inside the BULL / BEAR switch (2026-11-05, "Bull arrow number"):
// "BULL ↑ 2 ↓1" = 2 Mag7 tickers pass the bull scan, 1 passes the bear scan.
// Flipping to BEAR reads "BEAR ↓ 1 ↑2". Other lists show no counts. Reads are
// canned (bull board: NVDA + AAPL pass; bear board: SPY passes); no writes.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const TOGGLE = "[data-testid='momx-direction-toggle']";
const SHOWN = "[data-testid='momx-side-count-shown']";
const OTHER = "[data-testid='momx-side-count-other']";

async function countsRead(page, shown, other) {
  await page.waitForFunction(([s, o, ns, no]) =>
    document.querySelector(s)?.textContent.trim() === ns && document.querySelector(o)?.textContent.trim() === no,
  [SHOWN, OTHER, shown, other], { timeout: 20_000 });
}

e2eTest("Mag7 reads BULL ↑ 2 ↓1, and after the flip BEAR ↓ 1 ↑2", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await countsRead(app.page, "2", "↓1");
    assert.match(await app.page.locator(OTHER).getAttribute("class"), /\bis-bear\b/);
    assert.match(await app.page.locator(TOGGLE).getAttribute("title"), /2 pass BULL, 1 pass BEAR/);
    assert.ok(app.reads.some((r) => r.path === "/api/momx-scanner" && /dir=bear/.test(r.search)), "the other side is read");
    await app.page.locator(TOGGLE).click();
    await app.page.waitForFunction((sel) => document.querySelector(sel)?.getAttribute("aria-pressed") === "true", TOGGLE);
    await countsRead(app.page, "1", "↑2");
    assert.match(await app.page.locator(OTHER).getAttribute("class"), /\bis-bull\b/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Watchlist switch shows no counts", async () => {
  await withApp({ view: VIEW, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist" }, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.waitForTimeout(1500);
    assert.equal(await app.page.locator(SHOWN).count(), 0);
    assert.equal((await app.page.locator(TOGGLE).innerText()).trim(), "BULL");
  });
});

e2eTest("on a phone the switch with its counts stays on screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await countsRead(app.page, "2", "↓1");
    const box = await app.page.locator(TOGGLE).boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `switch at ${JSON.stringify(box)}`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
