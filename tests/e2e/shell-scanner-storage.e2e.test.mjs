// The SCANNER STORAGE card (admin only) end to end (coverage 2026-11-04, group
// "shell"). The save is answered by the harness, so the live retention never
// changes.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";

const SAVE = "POST /api/scanner-storage-settings";
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

const storageCard = (page) => page.locator("section.data-card", { has: page.locator(".table-toolbar span", { hasText: "SCANNER STORAGE" }) });

e2eTest("an admin's save sends the number of days and the table shows it", async () => {
  await withApp({ view: "Settings", fakeWrites: { [SAVE]: { json: {} } } }, async (app) => {
    const card = storageCard(app.page);
    await card.waitFor({ state: "visible", timeout: 60_000 });
    const input = card.locator('.risk-config-grid input[type="number"]');
    await input.fill("21");
    await card.locator(".config-save-button").click();
    await card.locator(".config-save-button:enabled", { hasText: "Save Scanner Storage" }).waitFor({ timeout: 15_000 });
    const write = app.writes.find((w) => w.path === "/api/scanner-storage-settings");
    assert.deepEqual(JSON.parse(write.body), { historyRetentionDays: 21 });
    assert.equal((await card.locator(".risk-rules-table > div").first().locator("span").innerText()).trim(), "21");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused save shows the reason on the page", async () => {
  await withApp({ view: "Settings", fakeWrites: { [SAVE]: { status: 400, json: { error: "historyRetentionDays must be a number." } } } }, async (app) => {
    const card = storageCard(app.page);
    await card.waitFor({ state: "visible", timeout: 60_000 });
    await card.locator(".config-save-button").click();
    await app.page.locator(".workspace .live-error", { hasText: "historyRetentionDays must be a number." }).waitFor({ timeout: 15_000 });
    assert.equal(app.writes.filter((w) => w.path === "/api/scanner-storage-settings").length, 1);
  });
});

e2eTest("a non-admin never sees the card", async () => {
  await withApp({ view: "Settings", fakeReads: { "GET /api/auth/status": member } }, async (app) => {
    await app.page.locator(".personal-api-keys-card").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await storageCard(app.page).count(), 0);
    assert.equal(await app.page.locator(".config-save-button").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
