// The installable app in a real browser (coverage 2026-11-04, group "shell"):
// the network-only service worker registers and caches nothing, API calls
// still reach the server, the "New version available" bar appears only for a
// new build and lands on Release Notes, and offline never shows a stale copy.
// A "new build" is simulated by answering the app's own fetch of "/" with a
// page naming a different entry script; navigations are never rerouted.

import assert from "node:assert/strict";

import { BASE_URL, e2eTest, withApp } from "./harness.mjs";

const NEW_BUILD = '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-E2ENEWBUILD.js"></script></head><body></body></html>';

async function ready(page) {
  await page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
}

async function routeNewBuild(context) {
  // Only the app's own version check (a plain fetch of "/") is answered;
  // a navigation - even one the service worker forwards - is never touched.
  await context.route(`${BASE_URL}/`, (route) => {
    const request = route.request();
    const accept = request.headers().accept || "";
    if (request.isNavigationRequest() || request.resourceType() === "document" || accept.includes("text/html")) return route.continue();
    return route.fulfill({ status: 200, contentType: "text/html", body: NEW_BUILD });
  });
}

const visibleAgain = (page) => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));

e2eTest("the service worker registers at / and keeps no caches", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await ready(app.page);
    const worker = await app.page.evaluate(async () => {
      const registration = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 20_000))]);
      if (!registration) return null;
      return { scope: registration.scope, script: (registration.active || registration.installing || registration.waiting)?.scriptURL || "",
        caches: await caches.keys() };
    });
    assert.ok(worker, "no service worker registered within 20 s");
    assert.equal(new URL(worker.scope).pathname, "/");
    assert.equal(new URL(worker.script).pathname, "/sw.js");
    assert.deepEqual(worker.caches, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("with the worker in control, API reads still reach the server", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await ready(app.page);
    await app.page.evaluate(() => navigator.serviceWorker.ready);
    await app.page.waitForFunction(() => Boolean(navigator.serviceWorker.controller), null, { timeout: 20_000 });
    const body = await app.page.evaluate(async () => (await fetch("/api/health", { cache: "no-store" })).json());
    assert.equal(typeof body.ok, "boolean");
    assert.ok(body.threads && body.threads.total > 0, "the answer came from the API, not a worker error");
  });
});

e2eTest("the same build offers no update bar", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await ready(app.page);
    await visibleAgain(app.page);
    await app.page.waitForTimeout(3_000);   // the check is a fetch of "/": give it time to (not) answer
    assert.equal(await app.page.locator("#agx-update-bar").count(), 0);
  });
});

e2eTest("a new build offers the bar, and tapping it lands on Release Notes", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await ready(app.page);
    await routeNewBuild(app.context);
    await visibleAgain(app.page);
    const bar = app.page.locator("#agx-update-bar");
    await bar.waitFor({ state: "visible", timeout: 20_000 });
    assert.equal((await bar.innerText()).trim(), "New version available - tap to update");
    await Promise.all([app.page.waitForEvent("load", { timeout: 60_000 }), bar.click()]);
    await app.page.locator(".command-context h1", { hasText: "Release Notes" }).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.evaluate(() => window.sessionStorage.getItem("agx:open-release-notes")), null, "the hand-off mark is used once");
  });
});

e2eTest("offline, a reload fails instead of showing an old copy", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    await ready(app.page);
    await app.page.evaluate(() => navigator.serviceWorker.ready);
    await app.context.setOffline(true);
    try {
      let failed = false;
      try {
        await app.page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 });
      } catch {
        failed = true;
      }
      const shell = await app.page.locator('[data-testid="app-shell"]').count();
      assert.ok(failed || shell === 0, "an offline reload rendered the app from somewhere other than the network");
    } finally {
      await app.context.setOffline(false);
    }
  });
});
