// Sign-in, sign-out and owner setup, end to end in the real app (coverage
// 2026-11-04, group "shell"). The login status is a fixture (fakeReads), and
// every sign-in/out POST is answered by the harness, so no session is created
// or ended on the live server.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const STATUS = "GET /api/auth/status";
const LOGIN = "POST /api/auth/login";
const user = (over = {}) => ({
  id: "e2e-user-1", email: "e2e.trader@example.com", displayName: "E2E Trader", role: "admin", isAdmin: true,
  isActive: true, hasPassword: true, mustChangePassword: false, ...over,
});
const status = (over = {}) => ({
  json: { bootstrapRequired: false, bootstrapRequiresToken: false, user: null, accessStatus: "", accessEmail: "", ...over },
});

async function loginScreen(page) {
  await page.locator(".auth-page .auth-card").waitFor({ state: "visible", timeout: 60_000 });
}

async function signIn(page, email = "e2e.trader@example.com", password = "Not-Real-Pass-1") {
  await page.locator('.auth-form input[name="email"]').fill(email);
  await page.locator('.auth-form input[name="password"]').fill(password);
  await page.locator('.auth-form button[type="submit"]').click();
}

e2eTest("a signed-out browser sees the Welcome back form and no workspace", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status() } }, async (app) => {
    await loginScreen(app.page);
    assert.equal((await app.page.locator(".auth-card h1").innerText()).trim(), "Welcome back");
    assert.equal(await app.page.locator('.auth-form input[name="email"]').count(), 1);
    assert.equal(await app.page.locator('.auth-form input[name="password"]').count(), 1);
    assert.equal(await app.page.locator('.auth-form input[name="displayName"]').count(), 0);
    assert.equal(await app.page.locator("aside.sidebar").count(), 0, "no workspace behind the login screen");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused sign-in shows the server's reason and the button comes back", async () => {
  const reason = "Invalid email or password.";
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status() }, fakeWrites: { [LOGIN]: { status: 400, json: { error: reason } } } }, async (app) => {
    await loginScreen(app.page);
    await signIn(app.page);
    const error = app.page.locator(".auth-form .auth-error");
    await error.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await error.innerText()).trim(), reason);
    const button = app.page.locator('.auth-form button[type="submit"]');
    await app.page.locator('.auth-form button[type="submit"]:enabled').waitFor({ timeout: 10_000 });
    assert.equal((await button.innerText()).trim(), "Sign in");
    const write = app.writes.find((w) => w.path === "/api/auth/login");
    assert.deepEqual(JSON.parse(write.body), { email: "e2e.trader@example.com", password: "Not-Real-Pass-1" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a new device is told it is waiting for admin approval", async () => {
  const pending = { status: 403, json: { devicePending: true, error: "This device is waiting for admin approval. Approve it from an already signed-in device, then sign in again." } };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status() }, fakeWrites: { [LOGIN]: pending } }, async (app) => {
    await loginScreen(app.page);
    await signIn(app.page);
    const error = app.page.locator(".auth-form .auth-error");
    await error.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await error.innerText(), /waiting for admin approval/);
    assert.equal(await app.page.locator('[data-testid="app-shell"]').count(), 0);
  });
});

e2eTest("a good sign-in opens the workspace", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status() }, fakeWrites: { [LOGIN]: { json: { user: user() } } } }, async (app) => {
    await loginScreen(app.page);
    await signIn(app.page);
    await app.page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 30_000 });
    assert.match(await app.page.locator(".account-settings-card .table-toolbar").innerText(), /e2e\.trader@example\.com/);
    assert.equal(await app.page.locator(".auth-page").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Cloudflare let in an address with no account: the notice names it", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status({ accessStatus: "no_account", accessEmail: "x@example.com" }) } }, async (app) => {
    await loginScreen(app.page);
    const notice = app.page.locator('.auth-access-notice[role="alert"]');
    await notice.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await notice.innerText()).trim(), "No AGX account for x@example.com. Ask your administrator to add you in Settings.");
  });
});

e2eTest("a switched-off account is told so by name", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status({ accessStatus: "disabled", accessEmail: "off@example.com" }) } }, async (app) => {
    await loginScreen(app.page);
    assert.match(await app.page.locator('.auth-access-notice[role="alert"]').innerText(), /off@example\.com has been switched off/);
  });
});

e2eTest("an empty database asks for the owner account and the setup token", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: status({ bootstrapRequired: true, bootstrapRequiresToken: true }) },
    fakeWrites: { "POST /api/auth/bootstrap": { json: { user: user() } } },
  }, async (app) => {
    await loginScreen(app.page);
    assert.equal((await app.page.locator(".auth-card h1").innerText()).trim(), "Create owner account");
    await app.page.locator('.auth-form input[name="displayName"]').fill("Owner");
    await app.page.locator('.auth-form input[name="setupToken"]').fill("setup-token-e2e");
    assert.equal((await app.page.locator('.auth-form button[type="submit"]').innerText()).trim(), "Create admin account");
    await signIn(app.page, "owner@example.com", "Owner-Pass-123");
    await app.page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
    const write = app.writes.find((w) => w.path === "/api/auth/bootstrap");
    assert.deepEqual(JSON.parse(write.body), { email: "owner@example.com", password: "Owner-Pass-123", displayName: "Owner", setupToken: "setup-token-e2e" });
    assert.equal(app.writes.filter((w) => w.path === "/api/auth/login").length, 0);
  });
});

e2eTest("Sign out returns to the login screen after exactly one logout call", async () => {
  await withApp({ view: "Settings", fakeWrites: { "POST /api/auth/logout": { json: { ok: true } } } }, async (app) => {
    const signOut = app.page.locator('.signed-in-user button[aria-label="Sign out"]');
    await signOut.waitFor({ state: "visible", timeout: 60_000 });
    await signOut.click();
    await loginScreen(app.page);
    assert.equal(app.writes.filter((w) => w.path === "/api/auth/logout").length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a backend restart during boot never dumps the trader on the login page", async () => {
  let calls = 0;
  const flaky = () => {
    calls += 1;
    return calls <= 2 ? { status: 500, json: { error: "restarting" } } : status({ user: user() });
  };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: flaky } }, async (app) => {
    await app.page.locator('[data-testid="app-shell"]').waitFor({ state: "visible", timeout: 60_000 });
    assert.ok(calls >= 3, `status was asked ${calls} times`);
    assert.equal(await app.page.locator(".auth-form").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the login card fits a phone", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [STATUS]: status() } }, async (app) => {
    await loginScreen(app.page);
    const box = await app.page.locator(".auth-card").boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `card at ${JSON.stringify(box)} is cut off`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
