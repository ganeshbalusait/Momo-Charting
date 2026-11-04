// Release Notes page end to end in the real app (2026-11-04 coverage): every
// shipped entry listed with its ET stamp, the version line, the install status,
// the "Update available" path and a missing notes file. Read-only: the notes
// file and '/' are static, and the two failure cases are routed in the browser.

import assert from "node:assert/strict";

import { parseReleases } from "../../frontend/src/releaseNotes.js";
import { BASE_URL, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW_SEL = '[data-testid="release-notes-view"]';
const NAV = "button.nav-item.nav-release-notes";

async function servedReleases(page) {
  const raw = await page.evaluate(() => fetch(`/release-notes.json?t=${Date.now()}`, { cache: "no-store" }).then((r) => r.json()));
  return parseReleases(raw);
}

async function openNotesFromSettings(app) {
  await app.page.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
  await app.page.locator(NAV).click();
  await app.page.locator(`${VIEW_SEL} h1.release-notes-title`).waitFor({ timeout: 15_000 });
}

e2eTest("every shipped release is listed newest first, stamped in ET, under a version line", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await app.page.locator(`${VIEW_SEL} section.release-entry`).first().waitFor({ timeout: 60_000 });
    const releases = await servedReleases(app.page);
    assert.equal(await app.page.locator(`${VIEW_SEL} section.release-entry`).count(), releases.length);
    assert.equal((await app.page.locator(`${VIEW_SEL} h2.release-entry-heading`).first().innerText()).trim(), releases[0].heading);
    const stamps = await app.page.locator(`${VIEW_SEL} .release-entry-stamp`).allInnerTexts();
    assert.ok(stamps.length > 0 && stamps.every((stamp) => stamp.trim().endsWith(" ET")), "a stamp is not in ET");
    assert.match((await app.page.locator(`${VIEW_SEL} .release-notes-version`).innerText()).trim(), /^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$/);
    assert.ok(await app.page.locator(`${VIEW_SEL} ul.release-entry-items li`).count() >= releases.length);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the install line settles on a real answer, never a stuck Checking", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    const status = app.page.locator(`${VIEW_SEL} .release-notes-status-installed`);
    await status.waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(3_000);
    const text = (await status.innerText()).trim();
    const devServer = new URL(BASE_URL).port === "5173";
    assert.equal(text, devServer ? "Latest release" : text);
    assert.match(text, /^(Latest release|Currently Installed)$/);
    assert.equal(await app.page.locator(`${VIEW_SEL} button.release-notes-status-update`).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a newer build on the server offers 'Update available' and the tap reloads onto Release Notes", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    // Only the page's fetch of '/' sees a different build; navigations are real.
    await app.context.route((url) => url.pathname === "/", (route) => (route.request().resourceType() === "document"
      ? route.fallback()
      : route.fulfill({ status: 200, contentType: "text/html", body: '<!doctype html><script type="module" crossorigin src="/assets/index-E2ENEWBUILD.js"></script>' })));
    await openNotesFromSettings(app);
    const update = app.page.locator(`${VIEW_SEL} button.release-notes-status-update`);
    await update.waitFor({ timeout: 15_000 });
    assert.equal((await update.innerText()).trim(), "Update available – tap to install");
    await Promise.all([app.page.waitForEvent("load"), update.click()]);
    await app.page.locator(`${VIEW_SEL} h1.release-notes-title`).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.evaluate(() => sessionStorage.getItem("agx:open-release-notes")), null, "the hand-off mark is used once");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a missing notes file says so instead of a blank page", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await app.context.route(/\/release-notes\.json/, (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "missing" }));
    await openNotesFromSettings(app);
    const error = app.page.locator(`${VIEW_SEL} p.release-notes-note-error`);
    await error.waitFor({ timeout: 15_000 });
    assert.match(await error.innerText(), /^Could not load the release notes \(HTTP 404\)\./);
    assert.equal(await app.page.locator(`${VIEW_SEL} section.release-entry`).count(), 0);
    assert.equal((await app.page.locator(`${VIEW_SEL} .release-notes-version`).innerText()).trim(), "—");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: Release Notes from the More sheet wraps to the width and scrolls to the oldest entry", async () => {
  await withApp({ view: "Settings", viewport: PHONE }, async (app) => {
    const more = app.page.locator('[data-testid="mobile-primary-more"]');
    await more.waitFor({ state: "visible", timeout: 60_000 });
    await more.click();
    await app.page.locator('.mobile-more-sheet[aria-label="More destinations"] [role="menuitem"]', { hasText: "Release Notes" }).click();
    await app.page.locator(`${VIEW_SEL} section.release-entry`).first().waitFor({ timeout: 30_000 });
    assert.equal(await overflowsSideways(app.page), false);
    const last = app.page.locator(`${VIEW_SEL} section.release-entry`).last();
    await app.page.mouse.move(PHONE.width / 2, PHONE.height / 2);
    const visible = async () => {
      const box = await last.boundingBox();
      return Boolean(box && box.y < PHONE.height && box.y + box.height > 0);
    };
    for (let step = 0; step < 300 && !(await visible()); step += 1) await app.page.mouse.wheel(0, 3_000);
    assert.ok(await visible(), "a real scroll never reached the oldest release");
    assert.deepEqual(app.pageErrors, []);
  });
});
