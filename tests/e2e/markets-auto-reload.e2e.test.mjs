// New-version watch end to end in the real app (2026-11-04 coverage): the
// update bar in session, the tap that lands on Release Notes, the reload when
// hidden, the silent reload out of session, and the two "learned nothing"
// cases. Only the page's fetch of '/' is routed (document loads stay real);
// visibility is driven through document.visibilityState and the clock is
// pinned with Playwright's page.clock so "in session" never depends on when
// the spec runs.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";

const NEW_BUILD = "/assets/index-E2ENEWBUILD.js";
const BAR = "#agx-update-bar";
const WEDNESDAY_10AM_ET = new Date("2026-11-04T15:00:00Z");   // in session
const SATURDAY_NOON_ET = new Date("2026-11-07T17:00:00Z");     // out of session

async function serveRoot(app, html) {
  await app.context.route((url) => url.pathname === "/", (route) => (route.request().resourceType() === "document"
    ? route.fallback()
    : route.fulfill({ status: 200, contentType: "text/html", body: html })));
}
const builtPage = (src) => `<!doctype html><html><head><script type="module" crossorigin src="${src}"></script></head><body><div id="root"></div></body></html>`;

async function setVisibility(page, state) {
  await page.evaluate((value) => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => value });
    Object.defineProperty(document, "hidden", { configurable: true, get: () => value === "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
}

async function settled(app) {
  await app.page.locator('.command-context h1', { hasText: "Settings" }).waitFor({ timeout: 60_000 });
  await app.page.evaluate(() => { window.__e2eSameDocument = true; });
}
const sameDocument = (page) => page.evaluate(() => window.__e2eSameDocument === true);

e2eTest("in session, a new build while he watches shows the bar and does NOT reload", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    await app.page.clock.setFixedTime(WEDNESDAY_10AM_ET);
    await serveRoot(app, builtPage(NEW_BUILD));
    await setVisibility(app.page, "hidden");
    await setVisibility(app.page, "visible");
    const bar = app.page.locator(BAR);
    await bar.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await bar.innerText()).trim(), "New version available - tap to update");
    await app.page.waitForTimeout(2_000);
    assert.equal(await sameDocument(app.page), true, "the page reloaded in front of him");
    assert.equal(await app.page.locator(BAR).count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping the bar reloads onto Release Notes, once", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    await app.page.clock.setFixedTime(WEDNESDAY_10AM_ET);
    await serveRoot(app, builtPage(NEW_BUILD));
    await setVisibility(app.page, "hidden");
    await setVisibility(app.page, "visible");
    await app.page.locator(BAR).waitFor({ state: "visible", timeout: 15_000 });
    await Promise.all([app.page.waitForEvent("load"), app.page.locator(BAR).click()]);
    await app.page.locator('[data-testid="release-notes-view"] h1.release-notes-title').waitFor({ timeout: 60_000 });
    assert.equal(await app.page.evaluate(() => sessionStorage.getItem("agx:open-release-notes")), null, "the hand-off is consumed");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await app.page.locator(".command-context h1").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(1_000);
    assert.equal(await app.page.locator('[data-testid="release-notes-view"]').count(), 0, "a later reload went to Release Notes again");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("in session, an offered update applies itself when the app is hidden", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    await app.page.clock.setFixedTime(WEDNESDAY_10AM_ET);
    await serveRoot(app, builtPage(NEW_BUILD));
    await setVisibility(app.page, "hidden");
    await setVisibility(app.page, "visible");
    await app.page.locator(BAR).waitFor({ state: "visible", timeout: 15_000 });
    await Promise.all([app.page.waitForEvent("load", { timeout: 15_000 }), setVisibility(app.page, "hidden").catch(() => {})]);
    assert.equal(await sameDocument(app.page), false);
    assert.equal(await app.page.evaluate(() => sessionStorage.getItem("agx:open-release-notes")), null,
      "a hidden reload must not hijack the next view");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("out of session, coming back after a minute reloads silently and stays on his page", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    await app.page.clock.setFixedTime(SATURDAY_NOON_ET);
    await serveRoot(app, builtPage(NEW_BUILD));
    await setVisibility(app.page, "hidden");
    await app.page.clock.setFixedTime(new Date(SATURDAY_NOON_ET.getTime() + 61_000));
    await Promise.all([app.page.waitForEvent("load", { timeout: 15_000 }), setVisibility(app.page, "visible").catch(() => {})]);
    await app.page.locator(".command-context h1", { hasText: "Settings" }).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator('[data-testid="release-notes-view"]').count(), 0, "a silent reload must not switch the page");
    assert.equal(await app.page.evaluate(() => localStorage.getItem("agx:reloaded-for")), NEW_BUILD);
    assert.equal(await app.page.locator(BAR).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("resuming onto the same build does nothing", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    const current = await app.page.evaluate(() => document.querySelector('script[type="module"][src]')?.getAttribute("src"));
    assert.ok(current, "the running page has no module entry script");
    await app.page.clock.setFixedTime(SATURDAY_NOON_ET);
    await serveRoot(app, builtPage(current));
    await setVisibility(app.page, "hidden");
    await app.page.clock.setFixedTime(new Date(SATURDAY_NOON_ET.getTime() + 5 * 60_000));
    await setVisibility(app.page, "visible");
    await app.page.waitForTimeout(3_000);
    assert.equal(await sameDocument(app.page), true);
    assert.equal(await app.page.locator(BAR).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("'/' answering a login page (no module script) never reloads or offers anything", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await settled(app);
    await app.page.clock.setFixedTime(SATURDAY_NOON_ET);
    await serveRoot(app, "<!doctype html><title>Sign in - Cloudflare Access</title><script src=\"/cdn-cgi/access/login.js\"></script><p>Get a login code</p>");
    for (let round = 0; round < 3; round += 1) {
      await setVisibility(app.page, "hidden");
      await app.page.clock.setFixedTime(new Date(SATURDAY_NOON_ET.getTime() + (round + 1) * 120_000));
      await setVisibility(app.page, "visible");
    }
    await app.page.waitForTimeout(3_000);
    assert.equal(await sameDocument(app.page), true, "a login page was read as a new build");
    assert.equal(await app.page.locator(BAR).count(), 0);
    assert.equal(await app.page.evaluate(() => localStorage.getItem("agx:reloaded-for")), null);
    assert.deepEqual(app.pageErrors, []);
  });
});
