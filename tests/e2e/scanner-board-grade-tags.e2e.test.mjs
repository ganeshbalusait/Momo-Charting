// MomX Scanner grade letter and Setup tags, end to end (2026-11-04 coverage):
// clicking the A+ letter opens "Scanner grade A+ (experimental)" with the
// track-record line (Close and Escape both close it, and the row's card does
// not open), and tapping a Setup tag opens its "What ... means" box - the
// only way to read a tag on a phone, which has no hover.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const WHY = ".momx-grade-why[role='dialog'][aria-label='Scanner grade A+ (experimental)']";
const nvdaRow = (page) => page.locator("tr.momx-row").filter({ has: page.locator("[data-testid='momx-symbol-link']", { hasText: /^NVDA$/ }) });

e2eTest("the A+ letter opens its why panel with the recorded track record; Close and Escape close it", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const letter = nvdaRow(app.page).locator("td.momx-setup .momx-setup-button");
    assert.equal((await letter.locator(".momx-setup-letter").innerText()).trim(), "A+");
    await letter.click();
    const why = app.page.locator(WHY);
    await why.waitFor({ timeout: 10_000 });
    assert.match(await why.locator(".momx-filters-head").innerText(), /^NVDA · Scanner grade A\+ \(experimental\)/);
    // The fixture record: 12 recorded A+ signals (the record read may land just after the click).
    const record = why.locator(".momx-grade-why-record", { hasText: "12 signals" });
    await record.waitFor({ timeout: 10_000 });
    assert.match((await record.innerText()).trim(), /^A\+ · 12 signals · .* closed higher · avg .*\(recorded\)$/);
    assert.ok((await why.locator(".momx-grade-why-disclaimer").innerText()).trim().length > 0);
    assert.equal(await app.page.locator(".momx-popout-window").count(), 0, "the letter opens the why panel, not the card");
    await why.getByRole("button", { name: "Close" }).click();
    await why.waitFor({ state: "detached", timeout: 5_000 });
    await letter.click();
    await why.waitFor({ timeout: 5_000 });
    await app.page.keyboard.press("Escape");
    await why.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("without a track record the panel says so in words", async () => {
  const reads = { ...boardReads(), "GET /api/momx-scanner/grade-record": { json: { source: null, patternSource: null, direction: "bull" } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads }, async (app) => {
    await boardReady(app.page);
    await nvdaRow(app.page).locator(".momx-setup-button").click();
    const why = app.page.locator(WHY);
    await why.waitFor({ timeout: 10_000 });
    assert.equal((await why.locator(".momx-grade-why-record").innerText()).trim(), "track record unavailable");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping the MX A+ tag explains it, and a tap outside the box closes it", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const tag = nvdaRow(app.page).locator("[data-testid='momx-strategy-tag-mxaplus']");
    const text = (await tag.innerText()).trim();
    assert.match(text, /^MX A\+ \d{1,2}:\d{2} W$/);
    assert.equal(await nvdaRow(app.page).locator("[data-testid='momx-strategy-tag-bolt']").count(), 1, "the setup's bolt sits in front");
    await tag.click();
    const box = app.page.locator(`.momx-tag-explain[role='dialog'][aria-label='What ${text} means']`);
    await box.waitFor({ timeout: 5_000 });
    assert.equal((await box.locator(".momx-tag-explain-head strong").innerText()).trim(), text);
    assert.ok((await box.locator(".momx-tag-explain-box p").first().innerText()).trim().length > 20, "a plain-words rule");
    assert.equal(await app.page.locator(".momx-popout-window").count(), 0, "a tag tap never opens the card");
    await box.click({ position: { x: 4, y: 4 } });                 // the dim overlay, outside the box
    await box.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the tag box is fully on screen and its close button works", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const tag = nvdaRow(app.page).locator("[data-testid='momx-strategy-tag-mxaplus']");
    await tag.scrollIntoViewIfNeeded();
    await tag.tap();
    const box = app.page.locator(".momx-tag-explain[role='dialog'] .momx-tag-explain-box");
    await box.waitFor({ timeout: 5_000 });
    const rect = await box.boundingBox();
    assert.ok(rect && rect.x >= 0 && rect.x + rect.width <= PHONE.width, `box at ${JSON.stringify(rect)}`);
    assert.ok(rect.y >= 0 && rect.y + rect.height <= PHONE.height);
    assert.equal(await overflowsSideways(app.page), false);
    await app.page.locator(".momx-tag-explain-head button[aria-label='Close']").tap();
    await box.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
