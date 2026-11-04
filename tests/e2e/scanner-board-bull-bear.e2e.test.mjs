// MomX Scanner BULL / BEAR switch, end to end (2026-11-04 coverage): one
// click flips the board to BEAR - every board read gains &dir=bear, the BEAR
// rows replace the bull ones, NEWS ▲ becomes NEWS ▼, the bull-only bolt view
// hides - and the choice survives a reload on this device.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { boardReads, boardReady, rowSymbols } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const TOGGLE = "[data-testid='momx-direction-toggle']";
// The direction key is deliberately NOT seeded: the reload must read what the page saved.
const STORAGE = { "momx.scanner.activeList": "Mag7", "momx-auto-cards-on": "0" };

e2eTest("BULL -> BEAR: the next board read asks for the bear board and the bear rows show", async () => {
  await withApp({ view: VIEW, storage: STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const toggle = app.page.locator(TOGGLE);
    assert.equal((await toggle.innerText()).trim(), "BULL");
    assert.equal(await toggle.getAttribute("aria-pressed"), "false");
    assert.match(await app.page.locator("[data-testid='momx-news-tone-toggle']").innerText(), /^NEWS ▲/);
    assert.equal(await app.page.locator("[data-testid='momx-bolt-view']").count(), 1);
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    const before = app.reads.length;

    await toggle.click();
    await app.page.waitForFunction((sel) => document.querySelector(sel)?.getAttribute("aria-pressed") === "true", TOGGLE);
    assert.equal((await toggle.innerText()).trim(), "BEAR");
    assert.match(await toggle.getAttribute("class"), /\bis-bear\b/);
    await app.page.waitForFunction(() => {
      const symbols = [...document.querySelectorAll("tr.momx-row [data-testid='momx-symbol-link']")].map((b) => b.textContent.trim());
      return symbols.length === 1 && symbols[0] === "SPY";
    }, null, { timeout: 15_000 });
    // (A poll already in flight at the click may still be a bull read; the
    // newest one, the one that painted the bear rows, carries dir=bear.)
    const boardReadsAfter = app.reads.slice(before).filter((read) => read.path === "/api/momx-scanner");
    assert.ok(boardReadsAfter.some((read) => /[?&]dir=bear\b/.test(read.search)), JSON.stringify(boardReadsAfter));
    assert.match(boardReadsAfter.at(-1).search, /[?&]dir=bear\b/);
    assert.ok(app.reads.slice(before).some((read) => read.path === "/api/momx-scanner/grade-record" && /dir=bear/.test(read.search)));
    assert.match(await app.page.locator("[data-testid='momx-news-tone-toggle']").innerText(), /^NEWS ▼/);
    assert.equal(await app.page.locator("[data-testid='momx-bolt-view']").count(), 0, "the bolt view is bull-only");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("BEAR is remembered after a reload, and flipping back reads the bull board again", async () => {
  await withApp({ view: VIEW, storage: STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator(TOGGLE).click();
    await app.page.waitForFunction(() => window.localStorage.getItem("momx.scanner.direction") === "bear");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await boardReady(app.page);
    const toggle = app.page.locator(TOGGLE);
    assert.equal((await toggle.innerText()).trim(), "BEAR");
    assert.equal(await toggle.getAttribute("aria-pressed"), "true");
    const before = app.reads.length;
    await toggle.click();
    await app.page.waitForFunction((sel) => document.querySelector(sel)?.textContent.trim().startsWith("BULL"), TOGGLE);
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 2, null, { timeout: 15_000 });
    const after = app.reads.slice(before).filter((read) => read.path === "/api/momx-scanner");
    assert.ok(after.length >= 1 && !/dir=bear/.test(after.at(-1).search), JSON.stringify(after));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the BULL/BEAR button is on screen and flipping it keeps the layout", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const toggle = app.page.locator(TOGGLE);
    await toggle.scrollIntoViewIfNeeded();
    const box = await toggle.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `toggle at ${JSON.stringify(box)}`);
    await toggle.tap();
    await app.page.waitForFunction((sel) => document.querySelector(sel)?.getAttribute("aria-pressed") === "true", TOGGLE);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
