// Loopback auto-login vs a tunnel visitor, end to end through the real stack
// (Vite :5173 -> gateway :3001 -> api_server :3002), coverage 2026-11-04,
// group "shell". The trader's own browser opens signed in with no password; a
// browser that LOOKS like it came through Cloudflare (a relay header) must get
// the login page, whatever else it claims. Read-only: no sign-in is attempted.

import assert from "node:assert/strict";
import test from "node:test";

import { BASE_URL, e2eTest, marketHoursBlock, withApp } from "./harness.mjs";

const TUNNEL = { "Cf-Connecting-Ip": "203.0.113.9" };
// /api/auth/status caches a signed-in answer for 8 s keyed only by cookie and
// Access email (see test_cov_shell_loopback_api.py, a known bug). A tunnel-
// looking page opened inside that window would be answered from the trader's
// cache, so these specs open their first page on a static file (no app boot,
// no status call) and wait the window out where they must read the status.
const STATIC = "/release-notes.json";
const STATUS_CACHE_MS = 9_000;

async function ownerEmail(app) {
  const response = await app.page.request.get(`${BASE_URL}/api/auth/status`);
  assert.equal(response.status(), 200);
  const body = await response.json();
  assert.ok(body.user?.email, "this PC is expected to auto-sign-in (LOCAL_AUTO_LOGIN_EMAIL)");
  return body.user.email;
}

async function openAsTunnelVisitor(app, headers) {
  await app.context.setExtraHTTPHeaders(headers);
  const page = await app.context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  await page.goto(BASE_URL + "/", { waitUntil: "domcontentloaded", timeout: 60_000 });
  return { page, errors };
}

e2eTest("the trader's own browser opens signed in, with no password", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    const card = app.page.locator(".account-settings-card");
    await card.waitFor({ state: "visible", timeout: 60_000 });
    const email = await ownerEmail(app);
    assert.match(await card.locator(".table-toolbar").innerText(), new RegExp(email.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
    assert.equal(await app.page.locator(".auth-page").count(), 0);
    assert.equal(await app.page.locator(".signed-in-user").count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a browser that came through the tunnel gets the login page", async () => {
  await withApp({ path: STATIC }, async (app) => {
    // Earlier specs (and the trader's own boot) may have just filled the 8 s status cache.
    await new Promise((resolve) => setTimeout(resolve, STATUS_CACHE_MS));
    const { page, errors } = await openAsTunnelVisitor(app, TUNNEL);
    await page.locator(".auth-page .auth-form").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await page.locator('[data-testid="app-shell"]').count(), 0);
    assert.deepEqual(errors, []);
  });
});

e2eTest("a tunnel visitor cannot claim to be local or name the admin's email", async () => {
  await withApp({ path: STATIC }, async (app) => {
    const admin = await ownerEmail(app);
    await new Promise((resolve) => setTimeout(resolve, STATUS_CACHE_MS));   // let the 8 s status cache expire
    const { page, errors } = await openAsTunnelVisitor(app, {
      ...TUNNEL, "X-AGX-Request-Origin": "local", "X-AGX-Access-Email": admin,
    });
    await page.locator(".auth-page .auth-form").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await page.locator('[data-testid="app-shell"]').count(), 0);
    assert.deepEqual(errors, []);
  });
});

test("a tunnel visitor arriving right after the trader's own page load still gets the login page",
  { skip: marketHoursBlock() || false, todo: "BUG: /api/auth/status serves its 8 s cache to a cookie-less tunnel visitor, who then sees the owner's workspace", timeout: 180_000 },
  async () => {
    await withApp({ view: "Settings" }, async (app) => {
      await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 60_000 });
      const { page } = await openAsTunnelVisitor(app, TUNNEL);
      await page.locator(".auth-page, [data-testid=\"app-shell\"]").first().waitFor({ state: "visible", timeout: 60_000 });
      assert.equal(await page.locator('[data-testid="app-shell"]').count(), 0, "the visitor was shown the owner's workspace");
    });
  });
