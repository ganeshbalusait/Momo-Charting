// USER MANAGEMENT (admin) end to end (coverage 2026-11-04, group "shell").
// The user list and the signed-in admin are fixtures, so the rows and their
// buttons are deterministic, and every create / disable / delete POST is
// answered by the harness - no account is ever changed. One spec reads the
// live list to prove the real admin's own row carries no buttons.

import assert from "node:assert/strict";

import { BASE_URL, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const STATUS = "GET /api/auth/status";
const USERS = "GET /api/admin/users";
const MESSAGE = ".admin-users-body .schwab-settings-message";
const ADMIN = {
  id: "e2e-admin", email: "e2e.admin@example.com", displayName: "E2E Admin", role: "admin", isAdmin: true,
  isActive: true, hasPassword: true, mustChangePassword: false,
};
const MEMBER = { id: "e2e-member", email: "member@example.com", displayName: "Member Trader", role: "user", isAdmin: false,
  isActive: true, hasPassword: false, mustChangePassword: false, lastLoginAt: "2026-11-03T14:00:00+00:00" };
const fixtures = (over = {}) => ({
  [STATUS]: { json: { bootstrapRequired: false, bootstrapRequiresToken: false, user: ADMIN, accessStatus: "", accessEmail: "" } },
  [USERS]: { json: { users: [ADMIN, MEMBER] } },
  ...over,
});

async function userCard(page) {
  await page.locator(".admin-users-card").waitFor({ state: "visible", timeout: 60_000 });
  await page.locator(".admin-user-list > div").first().waitFor({ state: "visible", timeout: 30_000 });
}

const row = (page, email) => page.locator(".admin-user-list > div").filter({ hasText: email });

async function message(page, text) {
  const line = page.locator(MESSAGE).filter({ hasText: text });
  await line.waitFor({ state: "visible", timeout: 15_000 });
  return (await line.innerText()).trim();
}

e2eTest("Create user is disabled until an email is typed", async () => {
  await withApp({ view: "Settings", fakeReads: fixtures() }, async (app) => {
    await userCard(app.page);
    const create = app.page.locator(".admin-user-create-grid button");
    assert.equal(await create.isDisabled(), true);
    await app.page.locator('.admin-user-create-grid input[type="email"]').fill("new@example.com");
    assert.equal(await create.isEnabled(), true);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("creating a user shows a password-free invite naming the address", async () => {
  await withApp({
    view: "Settings",
    fakeReads: fixtures(),
    fakeWrites: { "POST /api/admin/users": { json: { user: { id: "e2e-new", email: "new.person@example.com", displayName: "New Person" }, emailed: false } } },
  }, async (app) => {
    await userCard(app.page);
    await app.page.locator(".admin-user-create-grid label", { hasText: "Name" }).locator("input").fill("New Person");
    await app.page.locator('.admin-user-create-grid input[type="email"]').fill("new.person@example.com");
    await app.page.locator(".admin-user-create-grid button").click();
    const invite = app.page.locator(".admin-invite-text");
    await invite.waitFor({ state: "visible", timeout: 15_000 });
    const text = await invite.innerText();
    assert.match(text, /new\.person@example\.com/);
    assert.match(text, /Hi New Person,/);
    assert.doesNotMatch(text, /temporary password|password:/i, "an invite never carries a password");
    assert.match(await message(app.page, "Created"), /Add this address to Cloudflare Access/);
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/admin/users").body),
      { displayName: "New Person", email: "new.person@example.com", role: "user" });
    assert.equal(await app.page.locator('.admin-user-create-grid input[type="email"]').inputValue(), "", "the form is cleared");
  });
});

e2eTest("Copy invite copies the text and says Copied", async () => {
  await withApp({
    view: "Settings",
    fakeReads: fixtures(),
    fakeWrites: { "POST /api/admin/users": { json: { user: { id: "e2e-new", email: "copy.me@example.com", displayName: "" } } } },
  }, async (app) => {
    await app.context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: BASE_URL });
    await userCard(app.page);
    await app.page.locator('.admin-user-create-grid input[type="email"]').fill("copy.me@example.com");
    await app.page.locator(".admin-user-create-grid button").click();
    const copy = app.page.locator(".admin-invite-copy");
    await copy.waitFor({ state: "visible", timeout: 15_000 });
    await copy.click();
    await app.page.locator(".admin-invite-copy", { hasText: "Copied" }).waitFor({ timeout: 10_000 });
    const clipboard = await app.page.evaluate(() => navigator.clipboard.readText());
    assert.match(clipboard, /copy\.me@example\.com/);
  });
});

e2eTest("Delete asks twice: Cancel keeps the row, Yes sends the id", async () => {
  await withApp({
    view: "Settings",
    fakeReads: fixtures(),
    fakeWrites: { "POST /api/admin/users/delete": { json: { deleted: true } } },
  }, async (app) => {
    await userCard(app.page);
    const member = row(app.page, MEMBER.email);
    await member.locator(".admin-user-actions .is-danger", { hasText: "Delete" }).click();
    const confirm = member.locator('.admin-user-delete-confirm[aria-label="Confirm delete"]');
    await confirm.waitFor({ state: "visible", timeout: 10_000 });
    assert.match(await confirm.innerText(), /Delete for good\?[\s\S]*Yes, delete[\s\S]*Cancel/);
    await confirm.locator("button", { hasText: "Cancel" }).click();
    assert.equal(await member.locator(".admin-user-delete-confirm").count(), 0);
    assert.equal(app.writes.filter((w) => w.path === "/api/admin/users/delete").length, 0, "Cancel sends nothing");
    await member.locator(".admin-user-actions .is-danger", { hasText: "Delete" }).click();
    await member.locator(".admin-user-delete-confirm .is-danger", { hasText: "Yes, delete" }).click();
    assert.match(await message(app.page, "Deleted"), /^Deleted member@example\.com\. Remove that address from Cloudflare Access too/);
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/admin/users/delete").body), { userId: MEMBER.id });
  });
});

e2eTest("Disable switches the account off and says its sessions ended", async () => {
  await withApp({
    view: "Settings",
    fakeReads: fixtures(),
    fakeWrites: { "POST /api/admin/users/set-active": { json: { user: { ...MEMBER, isActive: false } } } },
  }, async (app) => {
    await userCard(app.page);
    await row(app.page, MEMBER.email).locator(".admin-user-actions button", { hasText: "Disable" }).click();
    assert.equal(await message(app.page, "switched off"), "member@example.com is switched off. Any open sessions were ended.");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/admin/users/set-active").body),
      { userId: MEMBER.id, isActive: false });
  });
});

e2eTest("the signed-in admin's own row has no Disable or Delete (live list)", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    await userCard(app.page);
    const me = await (await app.page.request.get(`${BASE_URL}/api/auth/status`)).json();
    assert.ok(me.user?.isAdmin, "the live auto-login account is expected to be an admin");
    const own = row(app.page, me.user.email);
    assert.equal(await own.count(), 1, "the admin's own row is listed");
    assert.equal(await own.locator(".admin-user-actions button").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a non-admin never sees the card and the list is never requested", async () => {
  const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, user: MEMBER, accessStatus: "", accessEmail: "" } };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: member } }, async (app) => {
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(".personal-api-keys-card").waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await app.page.locator(".admin-users-card").count(), 0);
    assert.equal(app.reads.filter((r) => r.path === "/api/admin/users").length, 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("user rows fit a phone", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: fixtures() }, async (app) => {
    await userCard(app.page);
    for (const item of await app.page.locator(".admin-user-list > div").all()) {
      await item.scrollIntoViewIfNeeded();
      const box = await item.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `row at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
  });
});
