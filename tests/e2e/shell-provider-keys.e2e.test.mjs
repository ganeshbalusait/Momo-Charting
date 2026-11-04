// MY DATA PROVIDER KEYS (Alpaca, Tradier) end to end (coverage 2026-11-04,
// group "shell"). The saved-key summary and the signed-in user are fixtures,
// and every save is answered by the harness - no key is ever stored.

import assert from "node:assert/strict";
import test from "node:test";

import { maskEmail } from "../../frontend/src/maskEmail.js";
import { PHONE, e2eTest, marketHoursBlock, withApp } from "./harness.mjs";

const KEYS = "GET /api/user/api-keys";
const SAVE = "POST /api/user/api-keys";
const MESSAGE = ".account-settings-body .schwab-settings-message";
const EMAIL = "e2e.trader@example.com";
const summary = (over = {}) => ({
  json: { alpaca: { configured: false, keyIdMasked: "" }, tradier: { configured: false, tokenMasked: "" },
    schwabMarketData: { configured: false }, schwabTrading: { configured: false }, ...over },
});
const signedIn = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: EMAIL, displayName: "E2E", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

const alpacaCard = (page) => page.locator(".personal-provider-card").filter({ hasText: "Alpaca Market Data" });
const tradierCard = (page) => page.locator(".personal-provider-card").filter({ hasText: "Tradier fallback data" });

async function cards(page) {
  await page.locator(".personal-api-keys-card .personal-provider-card").first().waitFor({ state: "visible", timeout: 60_000 });
  await alpacaCard(page).locator("header span", { hasText: "Not configured" }).waitFor({ timeout: 15_000 });
}

e2eTest("saving Alpaca keys shows the masked id, clears the boxes and confirms", async () => {
  const saved = summary({ alpaca: { configured: true, keyIdMasked: "PKE2...ABCDEF" } });
  await withApp({ view: "Settings", fakeReads: { [KEYS]: summary(), "GET /api/auth/status": signedIn }, fakeWrites: { [SAVE]: saved } }, async (app) => {
    await cards(app.page);
    const card = alpacaCard(app.page);
    await card.locator("label", { hasText: "API key ID" }).locator("input").fill("PKE2ETESTKEYABCDEF");
    await card.locator("label", { hasText: "API secret" }).locator("input").fill("e2e-alpaca-secret");
    await card.locator("button", { hasText: "Save Alpaca keys" }).click();
    await card.locator("header span", { hasText: "PKE2...ABCDEF" }).waitFor({ timeout: 15_000 });
    assert.equal((await app.page.locator(MESSAGE).innerText()).trim(), "Your API credentials were encrypted and saved.");
    for (const input of await card.locator("input").all()) assert.equal(await input.inputValue(), "");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/user/api-keys").body),
      { provider: "alpaca_market_data", keyId: "PKE2ETESTKEYABCDEF", secretKey: "e2e-alpaca-secret" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("saving a Tradier token sends only the token", async () => {
  const saved = summary({ tradier: { configured: true, tokenMasked: "trad...654321" } });
  await withApp({ view: "Settings", fakeReads: { [KEYS]: summary(), "GET /api/auth/status": signedIn }, fakeWrites: { [SAVE]: saved } }, async (app) => {
    await cards(app.page);
    const card = tradierCard(app.page);
    await card.locator("input").fill("tradier-e2e-token-654321");
    await card.locator("button", { hasText: "Save Tradier token" }).click();
    await card.locator("header span", { hasText: "trad...654321" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/user/api-keys").body),
      { provider: "tradier", accessToken: "tradier-e2e-token-654321" });
  });
});

e2eTest("a refused save shows the server's reason", async () => {
  const reason = "Unsupported API provider.";
  await withApp({ view: "Settings", fakeReads: { [KEYS]: summary(), "GET /api/auth/status": signedIn }, fakeWrites: { [SAVE]: { status: 400, json: { error: reason } } } }, async (app) => {
    await cards(app.page);
    await tradierCard(app.page).locator("button").click();
    const line = app.page.locator(MESSAGE).filter({ hasText: reason });
    await line.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await line.innerText()).trim(), reason);
  });
});

e2eTest("the card says whose keys these are without spelling out the email", async () => {
  await withApp({ view: "Settings", fakeReads: { [KEYS]: summary(), "GET /api/auth/status": signedIn } }, async (app) => {
    await cards(app.page);
    const toolbar = (await app.page.locator(".personal-api-keys-card .table-toolbar").innerText()).replace(/\s+/g, " ");
    assert.match(toolbar, new RegExp(`Private to ${maskEmail(EMAIL).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`));
    assert.doesNotMatch(toolbar, /e2e\.trader@/);
  });
});

test("on a phone the save confirmation is on screen where the trader pressed Save",
  { skip: marketHoursBlock() || false, todo: "the confirmation renders in the MY ACCOUNT card at the top of Settings, off-screen when Save is pressed at the provider card", timeout: 180_000 },
  async () => {
    const saved = summary({ alpaca: { configured: true, keyIdMasked: "PKE2...ABCDEF" } });
    await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [KEYS]: summary(), "GET /api/auth/status": signedIn }, fakeWrites: { [SAVE]: saved } }, async (app) => {
      await cards(app.page);
      const card = alpacaCard(app.page);
      await card.locator("button", { hasText: "Save Alpaca keys" }).scrollIntoViewIfNeeded();
      await card.locator("button", { hasText: "Save Alpaca keys" }).click();
      const line = app.page.locator(MESSAGE);
      await line.waitFor({ state: "attached", timeout: 15_000 });
      const box = await line.boundingBox();
      assert.ok(box && box.y >= 0 && box.y + box.height <= PHONE.height, `confirmation at ${JSON.stringify(box)} is off-screen`);
    });
  });
