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

// 2026-11-16 "for mag7 show bull and bear scanner below bull so I no need to
// switch": the bear matches sit in their own table under the bull board, and
// flipping to BEAR puts the bull matches underneath instead.
const OTHER_SIDE = "[data-testid='momx-other-side']";
async function otherSideSymbols(page) {
  return page.locator(OTHER_SIDE + " tbody tr").evaluateAll((rows) => rows.map((row) => (row.querySelector(".momx-symbol")?.textContent || row.textContent).trim().split(/\s+/)[0]));
}

e2eTest("Mag7 shows the BEAR matches in a table under the BULL board; flipped, BULL is underneath", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await countsRead(page, "2", "↓1");
    const under = page.locator(OTHER_SIDE);
    await under.waitFor({ state: "visible", timeout: 20_000 });
    assert.equal(await under.getAttribute("data-side"), "bear");
    assert.match(await under.locator(".momx-other-side-title").innerText(), /BEAR ↓\s*1/);
    await page.waitForFunction((sel) => document.querySelectorAll(sel + " tbody tr .momx-symbol").length === 1, OTHER_SIDE, { timeout: 20_000 });
    assert.deepEqual(await otherSideSymbols(page), ["SPY"]);
    // Under the board, not above it.
    const boardBox = await page.locator("table.momx-table").first().boundingBox();
    const underBox = await under.boundingBox();
    assert.ok(underBox.y > boardBox.y + boardBox.height - 1, "the bear table is below the bull board");
    await page.locator(TOGGLE).click();
    await page.waitForFunction((sel) => document.querySelector(sel)?.getAttribute("data-side") === "bull", OTHER_SIDE, { timeout: 20_000 });
    await page.waitForFunction((sel) => document.querySelectorAll(sel + " tbody tr .momx-symbol").length === 2, OTHER_SIDE, { timeout: 20_000 });
    assert.deepEqual((await otherSideSymbols(page)).sort(), ["AAPL", "NVDA"]);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the bear table fits under the board", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator(OTHER_SIDE).waitFor({ state: "visible", timeout: 20_000 });
    await app.page.waitForFunction((sel) => document.querySelectorAll(sel + " tbody tr .momx-symbol").length === 1, OTHER_SIDE, { timeout: 20_000 });
    assert.equal(await overflowsSideways(app.page), false);
    // 2026-11-16 "bear scanner little came right": the bear table starts at the
    // same left edge as the board and is not cut off on the right.
    const edges = await app.page.evaluate((sel) => {
      const board = document.querySelector("table.momx-table").getBoundingClientRect();
      const wrap = document.querySelector(sel + " .momx-table-scroll");
      const table = wrap.querySelector("table").getBoundingClientRect();
      return { boardLeft: board.left, tableLeft: table.left, clipped: table.width - wrap.getBoundingClientRect().width };
    }, OTHER_SIDE);
    assert.ok(Math.abs(edges.boardLeft - edges.tableLeft) <= 1, `left edges ${JSON.stringify(edges)}`);
    assert.ok(edges.clipped <= 1, `bear table cut off by ${edges.clipped}px`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Watchlist switch shows no counts", async () => {
  await withApp({ view: VIEW, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist" }, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.waitForTimeout(1500);
    assert.equal(await app.page.locator(SHOWN).count(), 0);
    assert.equal(await app.page.locator(OTHER_SIDE).count(), 0, "no second table on the Watchlist");
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
