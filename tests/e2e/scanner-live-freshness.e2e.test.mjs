// MomX Scanner -> what the toolbar says about its own data, end to end:
// the Updated time, the freshness chip's tone, and the "TOS loading n/N" note.
// The board is the live Mag7 board. For the loading note the live board is
// fetched as-is and only its tosStatus is replaced, so the note can be checked
// both while tapes are loading and once every tape is held.

import assert from "node:assert/strict";

import { BASE_URL, PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = { "momx.scanner.activeList": "Mag7", "momx.scanner.direction": "bull", "momx.scanner.activeView": "live", "momx-auto-cards-on": "0" };
const CHIP = "[data-testid='momx-data-freshness']";
const LOADING = "[data-testid='momx-tos-loading']";
const TONES = ["momx-tape-note", "momx-tape-closed", "momx-since"];

// The live board, with only its TOS load count replaced.
const liveBoardWith = (tosStatus) => async (read) => {
  const response = await fetch(`${BASE_URL}${read.path}${read.search}`);
  const board = await response.json();
  return { status: response.status, json: { ...board, dataSource: "tos", tosStatus } };
};

async function toolbar(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const updated = page.locator(".momx-stat", { has: page.locator(".momx-stat-label", { hasText: /^Updated$/ }) });
  await updated.waitFor({ timeout: 30_000 });
  return updated;
}

e2eTest("Updated shows when the scan ran and the freshness chip carries one of its three tones", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    const updated = await toolbar(page);
    const chip = page.locator(CHIP);
    await chip.waitFor({ timeout: 60_000 });
    const value = (await updated.locator(".momx-stat-value").innerText()).trim();
    assert.match(value, /\d/, `Updated reads "${value}"`);
    assert.doesNotMatch(value, /never|NaN|undefined|Invalid/i);
    const tone = (await chip.getAttribute("class")).split(/\s+/).find((name) => TONES.includes(name));
    assert.ok(tone, `chip class ${await chip.getAttribute("class")}`);
    assert.notEqual(tone, "momx-tape-note", `the tape is STALE: "${await chip.innerText()}" - worth chasing, the scanner is behind the market`);
    assert.match(await chip.innerText(), /5m bar \d{2}:\d{2}–\d{2}:\d{2} ET|Data \d{2}:\d{2} ET/);
    if (tone === "momx-tape-closed") assert.match(await chip.innerText(), /market closed/);
    assert.match(await chip.getAttribute("title"), /Scan started: .* Completed: /);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("'TOS loading n/N' shows only while tapes are still loading", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER,
    fakeReads: { "GET /api/momx-scanner": liveBoardWith({ source: "tos", total: 7, loaded: 5, pending: 2, oldestSeconds: 40 }) } }, async (app) => {
    const { page } = app;
    await toolbar(page);
    const note = page.locator(LOADING);
    await note.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal((await note.innerText()).trim(), "TOS loading 5/7");
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "MomX Scanner", storage: SCANNER,
    fakeReads: { "GET /api/momx-scanner": liveBoardWith({ source: "tos", total: 7, loaded: 7, pending: 0, oldestSeconds: 40 }) } }, async (app) => {
    const { page } = app;
    await toolbar(page);
    await page.locator(".momx-table-scroll tr.momx-row, .momx-empty").first().waitFor({ timeout: 60_000 });
    await page.waitForTimeout(2_000);
    assert.equal(await page.locator(LOADING).count(), 0, "every tape held: no loading note");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Updated stat and freshness chip are on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER }, async (app) => {
    const { page } = app;
    const updated = await toolbar(page);
    await updated.scrollIntoViewIfNeeded();
    const box = await updated.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `Updated at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});
