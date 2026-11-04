// MY ACCOUNT password change and the temporary-password hold, end to end
// (coverage 2026-11-04, group "shell"). The signed-in user is a fixture (the
// live auto-login account may have no password at all), and the change and
// sign-out POSTs are answered by the harness - no password is ever changed.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";

const STATUS = "GET /api/auth/status";
const CHANGE = "POST /api/auth/change-password";
const GRID = ".account-password-grid";
const MESSAGE = ".account-settings-body .schwab-settings-message";

const user = (over = {}) => ({
  id: "e2e-user-1", email: "e2e.trader@example.com", displayName: "E2E Trader", role: "user", isAdmin: false,
  isActive: true, hasPassword: true, mustChangePassword: false, ...over,
});
const signedIn = (over) => ({ json: { bootstrapRequired: false, bootstrapRequiresToken: false, user: user(over), accessStatus: "", accessEmail: "" } });

function fields(page) {
  return {
    current: page.locator(`${GRID} input[autocomplete="current-password"]`),
    next: page.locator(`${GRID} input[autocomplete="new-password"]`).nth(0),
    confirm: page.locator(`${GRID} input[autocomplete="new-password"]`).nth(1),
    button: page.locator(`${GRID} button`),
  };
}

async function form(page) {
  await page.locator(GRID).waitFor({ state: "visible", timeout: 60_000 });
  return fields(page);
}

e2eTest("Change password stays disabled until all three boxes are filled", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: signedIn() } }, async (app) => {
    const f = await form(app.page);
    assert.equal(await f.button.isDisabled(), true);
    await f.current.fill("Old-Pass-123");
    await f.next.fill("New-Pass-456");
    assert.equal(await f.button.isDisabled(), true, "still missing the confirmation");
    await f.confirm.fill("New-Pass-456");
    assert.equal(await f.button.isEnabled(), true);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a mismatch is caught on screen and nothing is sent", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: signedIn() } }, async (app) => {
    const f = await form(app.page);
    await f.current.fill("Old-Pass-123");
    await f.next.fill("New-Pass-456");
    await f.confirm.fill("New-Pass-457");
    await f.button.click();
    const line = app.page.locator(MESSAGE);
    await line.waitFor({ state: "visible", timeout: 10_000 });
    assert.equal((await line.innerText()).trim(), "New password and confirmation do not match.");
    assert.equal(app.writes.filter((w) => w.path === "/api/auth/change-password").length, 0);
  });
});

e2eTest("a successful change signs out to the login screen", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: signedIn() },
    fakeWrites: { [CHANGE]: { json: { ok: true } }, "POST /api/auth/logout": { json: { ok: true } } },
  }, async (app) => {
    const f = await form(app.page);
    await f.current.fill("Old-Pass-123");
    await f.next.fill("New-Pass-456");
    await f.confirm.fill("New-Pass-456");
    await f.button.click();
    await app.page.locator(".auth-page .auth-form").waitFor({ state: "visible", timeout: 15_000 });
    const paths = app.writes.map((w) => w.path);
    assert.deepEqual(paths.filter((p) => p.startsWith("/api/auth/")), ["/api/auth/change-password", "/api/auth/logout"]);
    assert.deepEqual(JSON.parse(app.writes[paths.indexOf("/api/auth/change-password")].body),
      { currentPassword: "Old-Pass-123", newPassword: "New-Pass-456" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused change shows the reason and keeps what was typed", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: signedIn() },
    fakeWrites: { [CHANGE]: { status: 400, json: { error: "Current password is incorrect." } } },
  }, async (app) => {
    const f = await form(app.page);
    await f.current.fill("Wrong-Pass-123");
    await f.next.fill("New-Pass-456");
    await f.confirm.fill("New-Pass-456");
    await f.button.click();
    const line = app.page.locator(MESSAGE).filter({ hasText: "incorrect" });
    await line.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await line.innerText()).trim(), "Current password is incorrect.");
    assert.equal(await f.current.inputValue(), "Wrong-Pass-123");
    assert.equal(await f.confirm.inputValue(), "New-Pass-456");
    assert.equal(await app.page.locator(".auth-page").count(), 0, "a refusal must not sign out");
  });
});

e2eTest("a temporary password holds the user on Settings with only Settings in the rail", async () => {
  await withApp({ view: "Watchlist", fakeReads: { [STATUS]: signedIn({ mustChangePassword: true }) } }, async (app) => {
    await app.page.locator(".account-security-notice").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal((await app.page.locator(".account-security-notice").innerText()).trim(),
      "Change the temporary password before continuing.");
    const rail = app.page.locator('aside.sidebar[data-testid="primary-navigation"] .nav-item');
    assert.equal(await rail.count(), 1);
    assert.equal(await rail.first().getAttribute("title"), "Settings");
    assert.equal(await app.page.locator(".command-context h1").innerText(), "Settings", "the remembered page is overridden");
    assert.equal(await app.page.locator('button[aria-label="Learn how to use AGX"]').count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a Cloudflare-only account sees no password form", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: signedIn({ hasPassword: false }) } }, async (app) => {
    const body = app.page.locator(".account-settings-body");
    await body.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await app.page.locator(GRID).count(), 0);
    assert.match(await body.innerText(), /You sign in with a one-time code emailed by Cloudflare, so there is no password to change\./);
  });
});
