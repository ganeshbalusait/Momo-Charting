// Settings > DEVICE ACCESS end to end (coverage 2026-11-04, group "shell").
// The device list is a fixture (the live list rarely has a pending row) and
// every approve / reject / revoke POST is answered by the harness, so no real
// device is ever approved or signed out. Results print in the USER MANAGEMENT
// card, as on the live page.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const DEVICES = "GET /api/admin/devices";
const MESSAGE = ".admin-users-body .schwab-settings-message";
const PENDING = 'section[aria-labelledby="pending-device-access-title"] .admin-device-row';
const APPROVED = 'section[aria-labelledby="approved-device-access-title"] .admin-device-row';

const listing = {
  pendingRequests: [{
    id: "dev-pending-1", userId: "u-2", userEmail: "member@example.com", userDisplayName: "Member Trader",
    label: "Safari on iPhone", ipAddress: "203.0.113.9", status: "pending", requestedAt: "2026-11-04T14:00:00+00:00",
  }],
  approvedDevices: [
    { id: "dev-mine", userId: "u-1", userEmail: "owner@example.com", userDisplayName: "Owner", label: "Chrome on Windows",
      ipAddress: "127.0.0.1", status: "approved", approvedAt: "2026-10-01T12:00:00+00:00", lastSeenAt: "2026-11-04T14:30:00+00:00", isCurrentDevice: true },
    { id: "dev-old-phone", userId: "u-2", userEmail: "member@example.com", userDisplayName: "Member Trader", label: "Chrome on Android",
      ipAddress: "198.51.100.4", status: "approved", approvedAt: "2026-09-01T12:00:00+00:00", lastSeenAt: "2026-10-20T09:00:00+00:00", isCurrentDevice: false },
  ],
};

async function deviceCard(page) {
  await page.locator(".admin-device-access-card").waitFor({ state: "visible", timeout: 60_000 });
  await page.locator(PENDING).first().waitFor({ state: "visible", timeout: 30_000 });
}

async function message(page, text) {
  const line = page.locator(MESSAGE).filter({ hasText: text });
  await line.waitFor({ state: "visible", timeout: 15_000 });
  return (await line.innerText()).trim();
}

e2eTest("a pending device shows who and what, and Approve lets it in", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [DEVICES]: { json: listing } },
    fakeWrites: { "POST /api/admin/device-requests": { json: { deviceId: "dev-pending-1", status: "approved" } } },
  }, async (app) => {
    await deviceCard(app.page);
    const row = app.page.locator(PENDING).first();
    assert.match(await row.locator(".admin-device-user").innerText(), /Member Trader[\s\S]*member@example\.com/);
    assert.match(await row.locator(".admin-device-details").innerText(), /Safari on iPhone[\s\S]*203\.0\.113\.9/);
    await row.locator(".admin-device-actions button", { hasText: "Approve" }).click();
    assert.equal(await message(app.page, "Device approved"), "Device approved. The user can sign in now.");
    const write = app.writes.find((w) => w.path === "/api/admin/device-requests");
    assert.deepEqual(JSON.parse(write.body), { deviceId: "dev-pending-1", action: "approve" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Reject sends a reject decision and says so", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [DEVICES]: { json: listing } },
    fakeWrites: { "POST /api/admin/device-requests": { json: { deviceId: "dev-pending-1", status: "rejected" } } },
  }, async (app) => {
    await deviceCard(app.page);
    await app.page.locator(`${PENDING} .admin-device-actions .is-danger`).first().click();
    assert.equal(await message(app.page, "rejected"), "Device request rejected.");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/admin/device-requests").body),
      { deviceId: "dev-pending-1", action: "reject" });
  });
});

e2eTest("the current device cannot be revoked; another one can", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [DEVICES]: { json: listing } },
    fakeWrites: { "POST /api/admin/devices/revoke": { json: { deviceId: "dev-old-phone", status: "revoked" } } },
  }, async (app) => {
    await deviceCard(app.page);
    const mine = app.page.locator(APPROVED).filter({ hasText: "Chrome on Windows" });
    assert.equal((await mine.locator(".admin-device-actions em").innerText()).trim(), "Current device");
    assert.equal(await mine.locator(".admin-device-actions button").count(), 0, "no Revoke on the device in use");
    const other = app.page.locator(APPROVED).filter({ hasText: "Chrome on Android" });
    await other.locator(".admin-device-actions .is-danger", { hasText: "Revoke" }).click();
    assert.equal(await message(app.page, "revoked"), "Device access revoked and its active sessions were signed out.");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/admin/devices/revoke").body), { deviceId: "dev-old-phone" });
  });
});

e2eTest("a refused decision shows the server's reason", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [DEVICES]: { json: listing } },
    fakeWrites: { "POST /api/admin/device-requests": { status: 400, json: { error: "This device request is no longer pending." } } },
  }, async (app) => {
    await deviceCard(app.page);
    await app.page.locator(`${PENDING} .admin-device-actions button`, { hasText: "Approve" }).click();
    assert.equal(await message(app.page, "no longer pending"), "This device request is no longer pending.");
  });
});

e2eTest("with nothing waiting, both lists say so", async () => {
  await withApp({ view: "Settings", fakeReads: { [DEVICES]: { json: { pendingRequests: [], approvedDevices: [] } } } }, async (app) => {
    await app.page.locator(".admin-device-access-card").waitFor({ state: "visible", timeout: 60_000 });
    const empty = app.page.locator(".admin-device-access-card .admin-device-empty");
    await empty.first().waitFor({ state: "visible", timeout: 30_000 });
    assert.deepEqual((await empty.allInnerTexts()).map((t) => t.trim()),
      ["No devices are waiting for approval.", "No approved devices yet."]);
    assert.match(await app.page.locator(".admin-device-access-card .table-toolbar").innerText(), /0 pending approval/);
  });
});

e2eTest("device rows fit a phone", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [DEVICES]: { json: listing } } }, async (app) => {
    await deviceCard(app.page);
    for (const row of await app.page.locator(".admin-device-row").all()) {
      await row.scrollIntoViewIfNeeded();
      const box = await row.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `row at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
