// Release Notes unread dot end to end in the real app (2026-11-04 coverage).
// The dot is the only sign that a silent auto-reload delivered a new version,
// so it must show for an unread release, clear the moment the page is opened,
// stay cleared, and never show (or crash) when the notes cannot be fetched.

import assert from "node:assert/strict";
import test from "node:test";

import { newestAt, parseReleases } from "../../frontend/src/releaseNotes.js";
import { BASE_URL, PHONE, e2eTest, marketHoursBlock, withApp } from "./harness.mjs";

const DOT = '[data-testid="release-notes-unread"]';
const NAV = "button.nav-item.nav-release-notes";
const SEEN = "agx:release-notes-seen";
const OLD_MARK = "2026-01-01T09:00:00-05:00";

async function notesFetched(page) {
  await page.waitForFunction(() => performance.getEntriesByType("resource").some((entry) => entry.name.includes("/release-notes.json")), null, { timeout: 60_000 });
  await page.waitForTimeout(750);   // let React apply the count
}

async function newest(page) {
  const raw = await page.evaluate(() => fetch(`/release-notes.json?t=${Date.now()}`, { cache: "no-store" }).then((r) => r.json()));
  return newestAt(parseReleases(raw));
}

e2eTest("an older read marker shows the dot on the Release Notes item", async () => {
  await withApp({ view: "Settings", storage: { [SEEN]: OLD_MARK } }, async (app) => {
    await app.page.locator(`${NAV} ${DOT}`).waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await app.page.locator(`${NAV} i.nav-unread-dot`).count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("opening Release Notes clears the dot at once, stores the newest stamp, and it stays cleared", async () => {
  await withApp({ view: "Settings", storage: { [SEEN]: OLD_MARK } }, async (app) => {
    await app.page.locator(`${NAV} ${DOT}`).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(NAV).click();
    await app.page.waitForFunction((selector) => !document.querySelector(selector), DOT, { timeout: 2_000 });
    await app.page.locator('[data-testid="release-notes-view"] section.release-entry').first().waitFor({ timeout: 30_000 });
    const stamp = await newest(app.page);
    await app.page.waitForFunction(([key, value]) => localStorage.getItem(key) === value, [SEEN, stamp]);

    // A second window of the same browser (no re-seeding) on another page.
    await app.page.evaluate(() => localStorage.setItem("agenticActiveView", "Settings"));
    const again = await app.context.newPage();
    const errors = [];
    again.on("pageerror", (error) => errors.push(String(error)));
    await again.goto(new URL("/", BASE_URL).href, { waitUntil: "domcontentloaded" });
    await again.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
    await notesFetched(again);
    assert.equal(await again.locator(DOT).count(), 0, "the dot came back after reading the notes");
    assert.deepEqual(errors, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a marker at the newest release shows no dot; a brand-new browser shows one", async () => {
  let stamp = null;
  await withApp({ view: "Settings" }, async (app) => {
    await app.page.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
    await notesFetched(app.page);
    assert.equal(await app.page.evaluate((key) => localStorage.getItem(key), SEEN), null);
    assert.equal(await app.page.locator(`${NAV} ${DOT}`).count(), 1, "nothing read yet: the dot shows");
    stamp = await newest(app.page);
  });
  await withApp({ view: "Settings", storage: { [SEEN]: stamp } }, async (app) => {
    await app.page.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
    await notesFetched(app.page);
    assert.equal(await app.page.locator(DOT).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("notes that cannot be fetched show no dot and break nothing", async () => {
  await withApp({ view: "Settings", storage: { [SEEN]: OLD_MARK } }, async (app) => {
    await app.page.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
    await app.context.route(/\/release-notes\.json/, (route) => route.fulfill({ status: 500, contentType: "text/plain", body: "boom" }));
    const blocked = await app.context.newPage();     // boots with the route in place, marker kept
    const errors = [];
    blocked.on("pageerror", (error) => errors.push(String(error)));
    const asked = blocked.waitForRequest(/\/release-notes\.json/, { timeout: 60_000 });
    await blocked.goto(new URL("/", BASE_URL).href, { waitUntil: "domcontentloaded" });
    await blocked.locator(NAV).waitFor({ state: "visible", timeout: 60_000 });
    await asked;
    await blocked.waitForTimeout(1_000);
    assert.equal(await blocked.locator(DOT).count(), 0);
    assert.deepEqual(errors, []);
  });
});

// Expected to fail today (2026-11-04): the dot lives only in the desktop
// sidebar, which is display:none at <=760px, and neither the bottom bar nor the
// More sheet carries one - so on the phone a silent update leaves no trace.
test("phone: an unread release is signalled somewhere on screen", {
  skip: marketHoursBlock() || false,
  todo: "the unread dot exists only in the desktop sidebar; the phone bottom bar and More sheet show nothing",
  timeout: 180_000,
}, async () => {
  await withApp({ view: "Settings", viewport: PHONE, storage: { [SEEN]: OLD_MARK } }, async (app) => {
    await app.page.locator('[data-testid="mobile-primary-more"]').waitFor({ state: "visible", timeout: 60_000 });
    await notesFetched(app.page);
    const visibleDots = await app.page.locator(`${DOT}, [data-testid="mobile-primary-more"] .nav-unread-dot`).evaluateAll(
      (nodes) => nodes.filter((node) => node.getBoundingClientRect().width > 0).length,
    );
    assert.ok(visibleDots > 0, "no unread signal is visible at 375px");
  });
});
