// MomX scanner on a phone as two-line cards (2026-11-20, his sample scanner:
// "Build it image looks good"). Reads are canned (scanner-board-fixtures);
// no writes. Desktop keeps the table.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const CARD = "[data-testid='momx-phone-board'] [data-testid='momx-phone-card']";
const SWITCH = "[data-testid='momx-phone-view-switch']";
const CARDS_STORAGE = { ...BOARD_STORAGE, "momx.scanner.phoneView": "cards" };

// "Don't remove column": the phone opens on the full table; Cards is a choice.
e2eTest("phone: the full table with every column is the default; Cards switches and is remembered", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await page.locator("table.momx-table tr.momx-row").first().waitFor({ timeout: 30_000 });
    assert.equal(await page.locator("[data-testid='momx-phone-card']").count(), 0);
    const heads = await page.locator("table.momx-table:not(.momx-other-side-table) th.momx-th").allInnerTexts();
    for (const name of ["Industry", "Symbol", "Setup", "Fresh", "% Chg", "Price", "Spread", "News", "Event", "H/L", "Mo"]) {
      assert.ok(heads.some((text) => text.includes(name)), `column ${name} on the phone table: ${heads.join("|")}`);
    }
    // A "Cards" tick box in the Scan-matches row (no row of its own).
    const toggle = page.locator(SWITCH).locator("input[type='checkbox']");
    assert.equal(await toggle.isChecked(), false, "Table is the default");
    assert.equal(await page.locator(`.momx-toolbar-stats ${SWITCH}`).count(), 1);
    await toggle.check();
    await page.locator(CARD).first().waitFor({ timeout: 15_000 });
    assert.equal(await page.evaluate(() => localStorage.getItem("momx.scanner.phoneView")), "cards");
    await toggle.uncheck();
    await page.locator("table.momx-table tr.momx-row").first().waitFor({ timeout: 15_000 });
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: the board is two-line cards with the eight Skittles timeframes, nothing cut off", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: CARDS_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await page.locator(CARD).first().waitFor({ timeout: 30_000 });
    assert.equal(await page.locator("table.momx-table:not(.momx-history-table)").count(), 0, "no wide table on a phone");
    const head = (await page.locator("[data-testid='momx-phone-board'] .momx-phone-head").first().innerText()).split(/\s+/);
    assert.deepEqual(head, ["Industry", "Symbol", "H/L", "2h", "4h", "D", "2D", "3D", "4D", "W", "Mo"]);
    const first = page.locator(CARD).first();
    assert.equal(await first.locator(".momx-phone-skit").count(), 8);
    assert.equal(await first.locator(".momx-phone-dot-group").count(), 3);
    assert.match(await first.locator(".momx-phone-pct").innerText(), /^[+-]?\d+\.\d\d%$|^$/);
    const symbol = await first.getAttribute("data-symbol");
    assert.ok(symbol, "card names its ticker");
    // Every card fits inside the screen.
    const outside = await page.evaluate((sel) => [...document.querySelectorAll(sel)]
      .map((node) => node.getBoundingClientRect())
      .filter((box) => box.left < -1 || box.right > window.innerWidth + 1).length, CARD);
    assert.equal(outside, 0);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: the Mag7 bear side is cards under the bull cards", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: CARDS_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    const other = page.locator("[data-testid='momx-other-side'] [data-testid='momx-phone-board-other']");
    await other.waitFor({ state: "visible", timeout: 20_000 });
    await page.waitForFunction(() => document.querySelectorAll("[data-testid='momx-phone-board-other'] [data-testid='momx-phone-card']").length === 1, null, { timeout: 20_000 });
    assert.equal(await other.locator("[data-testid='momx-phone-card']").first().getAttribute("data-symbol"), "SPY");
    const bull = await page.locator("[data-testid='momx-phone-board']").boundingBox();
    const bear = await other.boundingBox();
    assert.ok(bear.y > bull.y + bull.height - 1, "bear cards are below the bull cards");
    // Same width as the bull cards: the Mo column was cut off on the right.
    assert.ok(Math.abs(bear.width - bull.width) <= 1 && bear.x + bear.width <= PHONE.width + 1, JSON.stringify({ bull, bear }));
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: the ticker on a card is the same chart link as on the desktop table", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: CARDS_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    const first = page.locator(CARD).first();
    await first.waitFor({ timeout: 30_000 });
    const symbol = await first.getAttribute("data-symbol");
    const link = first.locator("[data-testid='momx-symbol-link']");
    assert.equal((await link.innerText()).trim(), symbol);
    assert.deepEqual(app.pageErrors, []);
  });
});

// "Column try to update ... see the sample ... don't remove or change columns"
// (2026-11-20, phone and desktop): the Table keeps every column; only the
// Skittles numbers become value-coloured boxes.
for (const viewport of [null, PHONE]) {
  e2eTest(`${viewport ? "phone" : "desktop"} table: every column kept, Skittles numbers are value-coloured boxes`, async () => {
    await withApp({ view: VIEW, ...(viewport ? { viewport } : {}), storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
      const { page } = app;
      await boardReady(page);
      await page.locator("table.momx-table:not(.momx-other-side-table) tr.momx-row").first().waitFor({ timeout: 30_000 });
      const heads = await page.locator("table.momx-table:not(.momx-other-side-table) th.momx-th").evaluateAll((cells) => cells.map((cell) => cell.dataset.col));
      for (const col of ["industry", "symbol", "setup", "fresh", "pctChange", "price", "spread", "news", "event", "rvol.2h", "highLow", "sqz.2h", "skittles.2h", "quoteTrend", "skittles.D", "skittles.M"]) {
        assert.ok(heads.includes(col), `column ${col} kept: ${heads.join(",")}`);
      }
      const boxes = page.locator("table.momx-table:not(.momx-other-side-table) td[data-col^='skittles.'] .momx-skit-box");
      assert.ok(await boxes.count() > 0, "Skittles numbers are boxes");
      const kinds = await boxes.evaluateAll((spans) => spans.map((span) => span.className));
      assert.ok(kinds.every((name) => /\bis-(fill|outline|text)\b/.test(name)), kinds.join(" | "));
      assert.equal(await page.locator("td[data-col^='rvol.'] .momx-skit-box").count(), 0, "RVOL keeps the TOS pills");
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("desktop keeps the full table, no cards", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await page.locator("table.momx-table tr.momx-row").first().waitFor({ timeout: 30_000 });
    assert.equal(await page.locator("[data-testid='momx-phone-card']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
