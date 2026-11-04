// MomX Scanner FILTERS, end to end (2026-11-04 coverage). Against the fixture
// board (scanner-board-fixtures.mjs) the default filters pass exactly one of
// the two scan matches (NVDA), and under ALL nobody passes because SQZ passes
// nobody. The badge counts BEFORE the press; the config is remembered, the
// press is not; Reset and the Setup ▾ rules behave as the trader expects.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady, rowSymbols } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const DIALOG = "[role='dialog'][aria-label='Scanner filters']";
const badge = (page) => page.locator("[data-testid='momx-filters-toggle'] .momx-filters-badge");
const state = (page) => page.locator("[data-testid='momx-filters-toggle'] .momx-filters-state");

async function openPanel(page) {
  await page.locator("[data-testid='momx-filters-open']").click();
  const dialog = page.locator(DIALOG);
  await dialog.waitFor({ timeout: 10_000 });
  return dialog;
}

e2eTest("the FILTERS badge says how many will show, and pressing it shows exactly those rows", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    assert.equal((await state(app.page).innerText()).trim(), "OFF");
    assert.equal((await badge(app.page).innerText()).trim(), "1");
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    await app.page.locator("[data-testid='momx-filters-toggle']").click();
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-state")?.textContent.trim() === "ON");
    assert.equal(await app.page.locator("[data-testid='momx-filters-toggle']").getAttribute("aria-pressed"), "true");
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 1);
    assert.deepEqual(await rowSymbols(app.page), ["NVDA"]);
    assert.equal(Number((await badge(app.page).innerText()).trim()), await app.page.locator("tr.momx-row").count());
    // Press again: the whole board is back.
    await app.page.locator("[data-testid='momx-filters-toggle']").click();
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 2);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("ALL with a group nobody passes says which group, and the board says why it is empty", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const dialog = await openPanel(app.page);
    assert.equal(await dialog.locator("[data-testid='momx-filters-why-empty']").count(), 0);
    await dialog.locator("[data-testid='momx-filters-topmode']").selectOption("all");
    const why = dialog.locator("[data-testid='momx-filters-why-empty']");
    await why.waitFor({ timeout: 5_000 });
    assert.equal((await why.innerText()).trim(), "0 tickers. SQZ passes no ticker right now - and ALL needs every switched-on group to pass.");
    assert.match(await dialog.locator(".momx-filters-count").innerText(), /^0 of 2 tickers$/);
    await dialog.getByRole("button", { name: "Close" }).click();
    await dialog.waitFor({ state: "detached", timeout: 5_000 });
    assert.equal((await badge(app.page).innerText()).trim(), "0");
    await app.page.locator("[data-testid='momx-filters-toggle']").click();
    const empty = app.page.locator("td.momx-empty .momx-empty-inner");
    await empty.waitFor({ timeout: 5_000 });
    assert.match(await empty.innerText(), /^No ticker passes your filters right now\. Press FILTERS again for the whole board/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the filter settings survive a reload but the FILTERS press does not", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const dialog = await openPanel(app.page);
    await dialog.locator("[data-testid='momx-filters-topmode']").selectOption("all");
    await dialog.getByRole("button", { name: "Close" }).click();
    await app.page.locator("[data-testid='momx-filters-toggle']").click();
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-state")?.textContent.trim() === "ON");
    await app.page.waitForFunction(() => JSON.parse(window.localStorage.getItem("momx.scanner.filters") || "{}").mode === "all");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    assert.equal((await state(app.page).innerText()).trim(), "OFF", "a filter left on must not make tomorrow's board look empty");
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    const again = await openPanel(app.page);
    assert.equal(await again.locator("[data-testid='momx-filters-topmode']").inputValue(), "all", "his ALL is kept");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Reset puts the default filters back and the badge back to its default count", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const dialog = await openPanel(app.page);
    await dialog.locator("[data-testid='momx-filters-topmode']").selectOption("all");
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-badge")?.textContent.trim() === "0");
    await dialog.locator(".momx-filters-foot").getByRole("button", { name: "Reset" }).click();
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-badge")?.textContent.trim() === "1");
    assert.equal(await dialog.locator("[data-testid='momx-filters-topmode']").inputValue(), "any");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("ticking A+ in Setup ▾ turns FILTERS on; pressing FILTERS clears the choice", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const header = app.page.locator("button.momx-setup-header-button[aria-label='Choose Setup column rules']");
    assert.equal((await header.innerText()).trim(), "Setup ▾");
    await header.click();
    const picker = app.page.locator("[role='dialog'][aria-label='Setup column rules']");
    await picker.waitFor({ timeout: 10_000 });
    // A+ is the first rule: only tickers showing the exact A+ letter.
    await picker.locator(".momx-setup-rule-grid label").first().locator("input[type='checkbox']").check();
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-state")?.textContent.trim() === "ON");
    assert.equal((await header.innerText()).trim(), "Setup ▾ 1");
    await picker.getByRole("button", { name: "Done" }).click();
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 1);
    assert.deepEqual(await rowSymbols(app.page), ["NVDA"]);
    await app.page.locator("[data-testid='momx-filters-toggle']").click();
    await app.page.waitForFunction(() => document.querySelector("[data-testid='momx-filters-toggle'] .momx-filters-state")?.textContent.trim() === "OFF");
    assert.equal((await header.innerText()).trim(), "Setup ▾");
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 2);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the filters dialog fits the screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator("[data-testid='momx-filters-open']").scrollIntoViewIfNeeded();
    const dialog = await openPanel(app.page);
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `dialog at ${JSON.stringify(box)}`);
    assert.ok(box.y >= 0 && box.y + box.height <= PHONE.height + 1, "dialog fits vertically (its body scrolls)");
    const reset = dialog.locator(".momx-filters-foot").getByRole("button", { name: "Reset" });
    const resetBox = await reset.boundingBox();
    assert.ok(resetBox && resetBox.y + resetBox.height <= PHONE.height, "Reset is reachable without scrolling the page");
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
