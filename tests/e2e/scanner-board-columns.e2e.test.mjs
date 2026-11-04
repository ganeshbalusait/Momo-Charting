// MomX Scanner columns, end to end (2026-11-04 coverage): the Columns dialog
// moves / hides / resets columns on BOTH the Live and History tables, the
// layout survives a reload, a header can be dragged or right-clicked, and the
// Time switch never knocks the RVOL / SQZ / SKIT band row out of line.
// Client-only: the board is the deterministic fixture; History reads live.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady, liveHeaderKeys } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const DIALOG = "[role='dialog'][aria-label='Scanner columns']";
const LIVE = "table.momx-table:not(.momx-history-table):not(.momx-find-table)";

async function openColumns(page) {
  await page.locator("[data-testid='momx-columns-open']").click();
  const dialog = page.locator(DIALOG);
  await dialog.waitFor({ timeout: 10_000 });
  return dialog;
}

async function bandsLineUp(page, table) {
  return page.locator(table).evaluate((node) => {
    const spans = [...node.querySelectorAll("thead tr.momx-group-row th")].reduce((sum, th) => sum + th.colSpan, 0);
    const heads = node.querySelectorAll("thead tr.momx-head-row th").length;
    return { spans, heads };
  });
}

e2eTest("moving % Chg right in the Columns dialog moves it on Live and on History", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const before = await liveHeaderKeys(app.page);
    const at = before.indexOf("pctChange");
    assert.ok(at > 0 && before[at + 1] === "price", "default: % Chg then Price " + before.join(","));
    const dialog = await openColumns(app.page);
    await dialog.locator("li[data-col='pctChange'] button[aria-label='Move % Chg right']").click();
    await dialog.getByRole("button", { name: "Close" }).click();
    await dialog.waitFor({ state: "detached", timeout: 5_000 });
    const after = await liveHeaderKeys(app.page);
    assert.equal(after.indexOf("pctChange"), at + 1);
    assert.equal(after[at], "price");
    assert.deepEqual(await bandsLineUp(app.page, LIVE).then(({ spans, heads }) => spans === heads), true);
    // The same layout drives the History table.
    await app.page.locator(".momx-seg button[role='tab']", { hasText: "History" }).click();
    const history = "table.momx-history-table";
    await app.page.locator(history).waitFor({ timeout: 30_000 });
    const historyKeys = await liveHeaderKeys(app.page, history);
    assert.ok(historyKeys.indexOf("price") < historyKeys.indexOf("pctChange"), "History follows: " + historyKeys.join(","));
    const lined = await bandsLineUp(app.page, history);
    assert.equal(lined.spans, lined.heads);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a hidden column stays hidden after a reload, and Reset brings the default layout back", async () => {
  // The layout key is deliberately NOT seeded: the reload must read what the page saved.
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const original = await liveHeaderKeys(app.page);
    assert.ok(original.includes("news"));
    let dialog = await openColumns(app.page);
    await dialog.locator("input[aria-label='Hide News']").uncheck();
    await dialog.locator("li[data-col='price'] button[aria-label='Move Price left']").click();
    await app.page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(await app.page.locator(`${LIVE} th[data-col='news']`).count(), 0);
    await app.page.waitForFunction(() => JSON.parse(window.localStorage.getItem("momx.scanner.columnLayout") || "{}").hidden?.includes("news"));
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    assert.equal(await app.page.locator(`${LIVE} th[data-col='news']`).count(), 0, "still hidden after a reload");
    assert.notDeepEqual(await liveHeaderKeys(app.page), original);
    dialog = await openColumns(app.page);
    assert.equal(await dialog.locator("input[aria-label='Show News']").isChecked(), false);
    await dialog.getByRole("button", { name: "Reset to default" }).click();
    await dialog.getByRole("button", { name: "Close" }).click();
    assert.deepEqual(await liveHeaderKeys(app.page), original);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("dragging the Price header onto % Chg puts Price before it", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const price = app.page.locator(`${LIVE} th.momx-th.is-col-draggable[data-col='price']`);
    const pct = app.page.locator(`${LIVE} th.momx-th.is-col-draggable[data-col='pctChange']`);
    await price.dragTo(pct, { targetPosition: { x: 3, y: 6 } });
    await app.page.waitForFunction((sel) => {
      const keys = [...document.querySelectorAll(sel + " thead tr.momx-head-row th[data-col]")].map((th) => th.getAttribute("data-col"));
      return keys.indexOf("price") >= 0 && keys.indexOf("price") < keys.indexOf("pctChange");
    }, LIVE, { timeout: 5_000 });
    // A drag is not a click: the board did not re-sort by Price.
    assert.equal(await price.getAttribute("aria-sort"), "none");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("right-clicking a header opens its menu, and Escape closes it", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator(`${LIVE} th[data-col='news']`).click({ button: "right" });
    const menu = app.page.locator("[data-testid='momx-colmenu'][role='menu']");
    await menu.waitFor({ timeout: 5_000 });
    assert.equal(await menu.getAttribute("aria-label"), "Column News");
    assert.deepEqual((await menu.locator(".momx-colmenu-item[role='menuitem']").allInnerTexts()).map((t) => t.trim()),
      ["Hide column", "◀ Move left", "Move right ▶", "Show all columns"]);
    await app.page.keyboard.press("Escape");
    await menu.waitFor({ state: "detached", timeout: 5_000 });
    // Hide from the menu works the same as the dialog.
    await app.page.locator(`${LIVE} th[data-col='news']`).click({ button: "right" });
    await menu.getByRole("menuitem", { name: "Hide column" }).click();
    await app.page.locator(`${LIVE} th[data-col='news']`).waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Time switch adds and removes the Time column and the band row stays in line", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const time = app.page.locator("label.momx-toggle", { hasText: /^Time$/ }).locator("input");
    const timeHeader = app.page.locator(`${LIVE} th[data-col='matchedSince']`);
    if (!(await time.isChecked())) await time.check();
    await timeHeader.waitFor({ timeout: 5_000 });
    assert.ok(await app.page.locator(`${LIVE} td.momx-col-time .momx-history-time`).count() >= 1, "a matched row shows when it entered the scan");
    let lined = await bandsLineUp(app.page, LIVE);
    assert.equal(lined.spans, lined.heads);
    await time.uncheck();
    await timeHeader.waitFor({ state: "detached", timeout: 5_000 });
    lined = await bandsLineUp(app.page, LIVE);
    assert.equal(lined.spans, lined.heads);
    // Every body row has exactly as many cells as the header.
    const cells = await app.page.locator(`${LIVE} tbody tr.momx-row`).evaluateAll((rows) => rows.map((row) => row.children.length));
    assert.ok(cells.every((count) => count === lined.heads), `row cells ${cells} vs ${lined.heads} headers`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Columns dialog fits the screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator("[data-testid='momx-columns-open']").scrollIntoViewIfNeeded();
    const dialog = await openColumns(app.page);
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `dialog at ${JSON.stringify(box)}`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
