// MomX Scanner lists, end to end (2026-11-04 coverage): the WATCHLISTS
// picker (built-ins, create, two-press delete, switch + reload), the Tickers
// box (Enter = Add, Set and Restore need a second press), the 21/50 EMA touch
// timeframe dropdown, and the Movers list. Every write is answered by the
// harness (fakeWrites) - nothing reaches the trader's real lists file.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import {
  BOARD_STORAGE, boardReads, boardReady, emaList, listsFixture, moversList, writesTo,
} from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const PICKER = ".momx-picker[role='dialog'][aria-label='Watchlists']";

async function openPicker(page) {
  await page.locator(".momx-listbtn[aria-haspopup='true']").click();
  const picker = page.locator(PICKER);
  await picker.waitFor({ timeout: 10_000 });
  return picker;
}

const item = (picker, name) => picker.locator(".momx-picker-list li").filter({ has: picker.page().locator(".momx-picker-name", { hasText: new RegExp("^" + name + "$") }) });

e2eTest("the picker marks Mag7 and Watchlist built-in, with no Rename or Delete on them", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    assert.deepEqual(await picker.locator(".momx-picker-name").allInnerTexts(), ["Mag7", "Watchlist", "Swing"]);
    for (const name of ["Mag7", "Watchlist"]) {
      const li = item(picker, name);
      assert.equal((await li.locator(".momx-picker-builtin").innerText()).trim(), "built-in");
      assert.equal(await li.getByRole("button", { name: "Rename" }).count(), 0);
      assert.equal(await li.getByRole("button", { name: /^Delete/ }).count(), 0);
    }
    const swing = item(picker, "Swing");
    assert.equal(await swing.locator(".momx-picker-builtin").count(), 0);
    assert.equal(await swing.getByRole("button", { name: "Rename" }).count(), 1);
    assert.equal(await swing.getByRole("button", { name: "Delete", exact: true }).count(), 1);
    assert.match(await picker.locator("li.is-active .momx-picker-name").innerText(), /^Mag7$/);
    await app.page.keyboard.press("Escape");
    await picker.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Add list sends one create with the typed name and says what happened", async () => {
  const fakeWrites = { "POST /api/momx-scanner/lists/create": { json: { ok: true, list: "Momentum", created: true, universe: [], universeCount: 0 } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    const add = picker.getByRole("button", { name: "Add list" });
    assert.equal(await add.isDisabled(), true, "nothing typed, nothing to add");
    await picker.locator(".momx-picker-new input[aria-label='New list name']").fill("Momentum");
    await add.click();
    await app.page.locator(".momx-banner.is-notice", { hasText: "Created Momentum." }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/momx-scanner/lists/create");
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { name: "Momentum" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused create shows the worker's reason inside the picker", async () => {
  const reason = "You already have 6 lists, which is the limit. Each list makes the others refresh more slowly. Delete one first.";
  const fakeWrites = { "POST /api/momx-scanner/lists/create": { json: { ok: false, error: reason } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    await picker.locator("input[aria-label='New list name']").fill("Seventh");
    await picker.getByRole("button", { name: "Add list" }).click();
    await picker.locator(".momx-picker-error", { hasText: reason }).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Delete needs two presses: the first only arms it, the second sends one delete", async () => {
  const fakeWrites = { "POST /api/momx-scanner/lists/delete": { json: { ok: true, deleted: "Swing", active: "Mag7", names: ["Mag7", "Watchlist"] } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    const swing = item(picker, "Swing");
    await swing.getByRole("button", { name: "Delete", exact: true }).click();
    const armed = swing.locator("button.momx-btn.is-armed");
    await armed.waitFor({ timeout: 5_000 });
    assert.equal((await armed.innerText()).trim(), "Delete?");
    assert.equal(writesTo(app, "/api/momx-scanner/lists/delete").length, 0, "the first press sends nothing");
    await armed.click();
    await app.page.locator(".momx-banner.is-notice", { hasText: "Deleted Swing." }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/momx-scanner/lists/delete");
    assert.equal(sent.length, 1);
    assert.equal(JSON.parse(sent[0].body).list, "Swing");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("picking Watchlist switches the board and is remembered after a reload", async () => {
  // The active list is NOT seeded here: the reload must find what the page saved.
  await withApp({ view: VIEW, storage: { "momx-auto-cards-on": "0" }, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    await item(picker, "Watchlist").locator(".momx-picker-pick").click();
    await picker.waitFor({ state: "detached", timeout: 5_000 });
    await app.page.waitForFunction(() => document.querySelector(".momx-listbtn-name")?.textContent.trim() === "Watchlist");
    await app.page.waitForFunction(() => window.localStorage.getItem("momx.scanner.activeList") === "Watchlist");
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner" && /list=Watchlist/.test(read.search)));
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    await app.page.waitForFunction(() => document.querySelector(".momx-listbtn-name")?.textContent.trim() === "Watchlist", null, { timeout: 30_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the picker opens fully on screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    const box = await picker.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `picker at ${JSON.stringify(box)} is cut off`);
    assert.ok(box.y >= 0 && box.y < PHONE.height);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- the Tickers box ---------------------------------------------------------

const form = (page) => page.locator("form.momx-universe-form");
const button = (page, name) => form(page).getByRole("button", { name, exact: true });

e2eTest("Add and Set are off with an empty box; Restore stays available", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    assert.equal(await button(app.page, "Add").isDisabled(), true);
    assert.equal(await button(app.page, "Set").isDisabled(), true);
    assert.equal(await button(app.page, "Restore").isEnabled(), true);
    await app.page.locator("#momx-universe-input").fill("   ");
    assert.equal(await button(app.page, "Add").isDisabled(), true, "spaces are still empty");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Enter in the Tickers box APPENDS: one add with the text and the list on screen", async () => {
  const fakeWrites = { "POST /api/momx-scanner/universe/add": { json: {
    ok: true, list: "Mag7", added: ["AVGO", "AAPL"], already: [], universeBefore: 10, universeCount: 12, materialised: false, noop: false,
  } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    const input = app.page.locator("#momx-universe-input");
    await input.fill("avgo aapl");
    await input.press("Enter");
    await app.page.locator(".momx-banner.is-notice", { hasText: "Added AVGO, AAPL. 10 to 12 tickers." }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/momx-scanner/universe/add");
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { text: "avgo aapl", list: "Mag7" });
    assert.equal(writesTo(app, "/api/momx-scanner/universe").length, 0, "Enter never REPLACES the list");
    assert.equal(await input.inputValue(), "", "the box clears after a good add");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Set on a 10-ticker list with one ticker typed arms first ('Replace?') and sends nothing", async () => {
  const fakeWrites = { "POST /api/momx-scanner/universe": { json: { ok: true, list: "Mag7", universe: ["AAPL"], universeCount: 1 } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    await app.page.locator(".momx-tab-count", { hasText: "10" }).first().waitFor({ timeout: 10_000 });
    await app.page.locator("#momx-universe-input").fill("AAPL");
    await button(app.page, "Set").click();
    const armed = form(app.page).locator("button.momx-btn.is-armed");
    await armed.waitFor({ timeout: 5_000 });
    assert.equal((await armed.innerText()).trim(), "Replace?");
    assert.match(await app.page.locator(".momx-banner.is-notice").innerText(), /This REPLACES the Mag7 list - it does not add to it\. 10 tickers would become 1\./);
    assert.equal(writesTo(app, "/api/momx-scanner/universe").length, 0, "the first press sends nothing");
    // Changing the text disarms it: an old arm never fires on a new paste.
    await app.page.locator("#momx-universe-input").fill("AAPL NVDA");
    await armed.waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(writesTo(app, "/api/momx-scanner/universe").length, 0);
    // Arm again and confirm: exactly one replace.
    await button(app.page, "Set").click();
    await form(app.page).locator("button.momx-btn.is-armed").click();
    await app.page.locator(".momx-banner.is-notice", { hasText: "Saved 1 tickers." }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/momx-scanner/universe");
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { text: "AAPL NVDA", list: "Mag7" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Restore arms first ('Restore?') and only the second press restores the list", async () => {
  const fakeWrites = { "POST /api/momx-scanner/universe/reset": { json: { ok: true, list: "Mag7", universe: [], universeCount: 10, restored: true } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    await button(app.page, "Restore").click();
    const armed = form(app.page).locator("button.momx-btn.is-armed");
    await armed.waitFor({ timeout: 5_000 });
    assert.equal((await armed.innerText()).trim(), "Restore?");
    assert.equal(writesTo(app, "/api/momx-scanner/universe/reset").length, 0, "the first press sends nothing");
    await armed.click();
    await app.page.locator(".momx-banner.is-notice", { hasText: "Restored 10 tickers." }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/momx-scanner/universe/reset");
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { list: "Mag7" });
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- 21 / 50 EMA touch lists and Movers --------------------------------------

e2eTest("the EMA chart dropdown shows only on an EMA touch list, and Weekly switches to that list", async () => {
  let switched = false;
  const lists = () => listsFixture({ extra: [emaList(switched ? "Weekly" : "Monthly")] });
  const fakeWrites = { "POST /api/momx-scanner/ema-timeframe": () => {
    switched = true;
    return { json: { ok: true, list: "21 EMA Touch Weekly", previous: "21 EMA Touch Monthly", active: "21 EMA Touch Weekly" } };
  } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads({ lists }), fakeWrites }, async (app) => {
    await boardReady(app.page);
    assert.equal(await app.page.locator("[data-testid='momx-ema-tf']").count(), 0, "no dropdown on Mag7");
    const picker = await openPicker(app.page);
    await item(picker, "21 EMA Touch Monthly").locator(".momx-picker-pick").click();
    const dropdown = app.page.locator("[data-testid='momx-ema-tf']");
    await dropdown.waitFor({ timeout: 10_000 });
    assert.equal(await dropdown.inputValue(), "Monthly");
    assert.deepEqual(await dropdown.locator("option").allInnerTexts(), ["Monthly", "Weekly", "Daily"]);
    await dropdown.selectOption("Weekly");
    await app.page.waitForFunction(() => document.querySelector(".momx-listbtn-name")?.textContent.trim() === "21 EMA Touch Weekly", null, { timeout: 15_000 });
    const sent = writesTo(app, "/api/momx-scanner/ema-timeframe");
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { length: 21, timeframe: "Weekly" });
    assert.equal(await app.page.locator(".momx-ema-tf-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Movers list opens like any other list", async () => {
  const lists = () => listsFixture({ extra: [moversList()] });
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads({ lists }) }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    await item(picker, "Movers").locator(".momx-picker-pick").click();
    await app.page.waitForFunction(() => document.querySelector(".momx-listbtn-name")?.textContent.trim() === "Movers");
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row, td.momx-empty").length > 0);
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner" && /list=Movers/.test(read.search)));
    assert.equal(await app.page.locator(".momx-banner.is-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
