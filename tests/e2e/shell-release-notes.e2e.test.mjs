// The Release Notes page and the rail's unread dot, end to end (coverage
// 2026-11-04, group "shell"). The notes are the live file; the "new build"
// and the load failure are simulated by answering the page's own fetches
// (never a navigation). A "reload" is a second tab, because the harness
// re-seeds storage on every load of the first one.

import assert from "node:assert/strict";

import { newestAt, parseReleases } from "../../frontend/src/releaseNotes.js";
import { BASE_URL, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const RAIL = 'aside.sidebar[data-testid="primary-navigation"]';
const DOT = '[data-testid="release-notes-unread"]';
const SEEN = "agx:release-notes-seen";

async function liveNotes() {
  const response = await fetch(`${BASE_URL}/release-notes.json?t=${Date.now()}`, { cache: "no-store" });
  assert.equal(response.status, 200);
  return parseReleases(await response.json());
}

async function page(app) {
  await app.page.locator(".release-notes .release-entry").first().waitFor({ state: "visible", timeout: 60_000 });
}

async function reopen(app) {
  // Leave the page and come back so the panel mounts (and fetches) again.
  await app.page.locator(`${RAIL} .nav-item[title="Settings"]`).click();
  await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 30_000 });
  await app.page.locator(`${RAIL} .nav-release-notes`).click();
}

e2eTest("the newest deploy is on top, every stamp in ET", async () => {
  const releases = await liveNotes();
  await withApp({ view: "Release Notes" }, async (app) => {
    await page(app);
    assert.equal((await app.page.locator(".release-notes-title").innerText()).trim(), "Release Notes");
    assert.equal((await app.page.locator(".release-entry-heading").first().innerText()).trim(), releases[0].heading);
    const stamps = await app.page.locator(".release-entry-stamp").allInnerTexts();
    assert.ok(stamps.length > 0 && stamps.every((s) => /ET$/.test(s.trim())), `stamps: ${JSON.stringify(stamps.slice(0, 3))}`);
    assert.match((await app.page.locator(".release-notes-version").innerText()).trim(), /^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$/);
    assert.ok(await app.page.locator(".release-entry-items li").count() > 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the same build says it is installed; a newer build offers the update", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await page(app);
    await app.page.locator(".release-notes-status-installed").waitFor({ state: "visible", timeout: 15_000 });
    await app.context.route(`${BASE_URL}/`, (route) => {
      const request = route.request();
      if (request.isNavigationRequest() || (request.headers().accept || "").includes("text/html")) return route.continue();
      return route.fulfill({ status: 200, contentType: "text/html",
        body: '<!doctype html><script type="module" crossorigin src="/assets/index-E2ENEWBUILD.js"></script>' });
    });
    await reopen(app);
    const update = app.page.locator(".release-notes-status-update");
    await update.waitFor({ state: "visible", timeout: 20_000 });
    assert.match((await update.innerText()).trim(), /^Update available . tap to install$/);
  });
});

e2eTest("opening the page clears the unread dot and remembers the newest entry", async () => {
  const releases = await liveNotes();
  await withApp({ view: "Settings", storage: { [SEEN]: "2020-01-01T00:00:00Z" } }, async (app) => {
    await app.page.locator(`${RAIL} ${DOT}`).waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(`${RAIL} .nav-release-notes`).click();
    await page(app);
    await app.page.locator(`${RAIL} ${DOT}`).waitFor({ state: "detached", timeout: 10_000 });
    assert.equal(await app.page.evaluate((key) => window.localStorage.getItem(key), SEEN), newestAt(releases));
    // Leave the notes page first: a tab that reopens ON Release Notes clears the dot by itself.
    await app.page.locator(`${RAIL} .nav-item[title="Settings"]`).click();
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 30_000 });
    const second = await app.context.newPage();
    await second.goto(BASE_URL + "/", { waitUntil: "domcontentloaded", timeout: 60_000 });
    await second.locator(`${RAIL} .nav-release-notes`).waitFor({ state: "visible", timeout: 60_000 });
    await second.waitForTimeout(2_000);   // the dot appears only after the notes fetch answers
    assert.equal(await second.locator(`${RAIL} ${DOT}`).count(), 0, "the dot came back in a new tab");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("if the notes cannot load, the page says so instead of crashing", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 60_000 });
    await app.context.route(/\/release-notes\.json/, (route) => route.fulfill({ status: 500, contentType: "text/plain", body: "boom" }));
    await app.page.locator(`${RAIL} .nav-release-notes`).click();
    const error = app.page.locator(".release-notes-note-error");
    await error.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await error.innerText(), /Could not load the release notes \(HTTP 500\)/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the notes page never scrolls sideways", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE }, async (app) => {
    await page(app);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
