// The "My Schwab Market & Optional Trading APIs" card end to end (coverage
// 2026-11-04, group "shell"). The Schwab status is a fixture and every save /
// test POST is answered by the harness: no key is written, Schwab is never
// called. The 10 s status poll keeps reading the same fixture.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const STATUS = "GET /api/schwab/status";
const SAVE = "POST /api/schwab/settings";
const TEST = "POST /api/schwab/test-connection";
const MESSAGE = ".schwab-settings-card .schwab-settings-message";

const profile = (over = {}) => ({
  configured: true, credentialsConfigured: true, clientIdMasked: "abcd...123456", hasRefreshToken: true,
  refreshTokenValid: true, accessTokenValid: true, refreshTokenRemainingSeconds: 5 * 86400,
  refreshTokenExpiresAt: "2026-11-09T15:00:00+00:00", redirectUri: "https://127.0.0.1", ...over,
});
const schwab = ({ marketData = {}, trading = {}, ...top } = {}) => ({
  json: { ...profile(marketData), marketData: profile(marketData), trading: profile(trading), callbackCertTrusted: true, alpacaBarStream: {}, ...top },
});

async function card(page) {
  await page.locator(".schwab-settings-card .schwab-app-status").first().waitFor({ state: "visible", timeout: 60_000 });
}

async function message(page, text) {
  const line = page.locator(MESSAGE).filter({ hasText: text });
  await line.waitFor({ state: "visible", timeout: 15_000 });
  return (await line.innerText()).trim();
}

e2eTest("saving keys clears the boxes, confirms, and sends all four fields", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab() }, fakeWrites: { [SAVE]: schwab({ saved: true }) } }, async (app) => {
    await card(app.page);
    const inputs = app.page.locator(".schwab-credential-grid input");
    await inputs.nth(0).fill("NEW-MARKET-KEY");
    await inputs.nth(1).fill("new-market-secret");
    await app.page.locator(".schwab-save-button").click();
    assert.equal(await message(app.page, "encrypted and saved"),
      "Your Market Data and optional Accounts & Trading credentials were encrypted and saved only for your account.");
    for (let i = 0; i < 4; i += 1) assert.equal(await inputs.nth(i).inputValue(), "");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/schwab/settings").body),
      { clientId: "NEW-MARKET-KEY", clientSecret: "new-market-secret", tradingClientId: "", tradingClientSecret: "" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused save shows the server's reason", async () => {
  const reason = "Schwab app key and secret are required.";
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab() }, fakeWrites: { [SAVE]: { status: 400, json: { error: reason } } } }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-save-button").click();
    assert.equal(await message(app.page, "required"), reason);
  });
});

e2eTest("Test Market Data reports success and the profile reads Connected", async () => {
  const verifiedAt = new Date().toISOString();
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: schwab() },
    fakeWrites: { [TEST]: { json: { symbol: "SPY", marketData: { connected: true, verifiedAt }, trading: {} } } },
  }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-test-actions button", { hasText: "Test Market Data" }).click();
    assert.equal(await message(app.page, "verified"), "SPY Market Data connection verified successfully.");
    const state = app.page.locator(".schwab-app-status").first().locator(".schwab-connection-state");
    assert.match(await state.innerText(), /^Connected · verified /);
    assert.match(await state.getAttribute("class"), /is-ok/);
    assert.equal(app.writes.find((w) => w.path === "/api/schwab/test-connection").search, "?profile=market_data");
  });
});

e2eTest("a failed test shows the reason and the profile reads Connection test failed", async () => {
  const reason = "Schwab rejected the request: 401 Unauthorized invalid_client";
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: schwab() },
    fakeWrites: { [TEST]: { status: 502, json: { error: reason, code: "schwab_connection_failed", marketData: { connected: false }, trading: {} } } },
  }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-test-actions button", { hasText: "Test Market Data" }).click();
    assert.equal(await message(app.page, "invalid_client"), reason);
    assert.equal((await app.page.locator(".schwab-app-status").first().locator(".schwab-connection-state").innerText()).trim(),
      "Connection test failed");
  });
});

e2eTest("without saved keys, Test and Renew are disabled", async () => {
  const none = { credentialsConfigured: false, configured: false, hasRefreshToken: false, clientIdMasked: "", refreshTokenExpiresAt: null };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab({ marketData: none, trading: none }) } }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-app-status b", { hasText: "not configured" }).first().waitFor({ timeout: 15_000 });
    await app.page.locator(".schwab-app-status .schwab-connection-state", { hasText: "App keys required" }).first().waitFor({ timeout: 15_000 });
    assert.ok(app.reads.some((r) => r.path === "/api/schwab/status"), "the card read the (fixture) status");
    for (const button of await app.page.locator(".schwab-test-actions button").all()) {
      assert.equal(await button.isDisabled(), true);
    }
    assert.equal(await app.page.locator(".schwab-renew-button").isDisabled(), true);
    const badge = app.page.locator(".schwab-app-status .schwab-expiry-badge").first();
    assert.equal((await badge.locator("strong").innerText()).trim(), "Required");
    assert.equal((await badge.textContent()).trim(), "OAuthRequiredauthenticate");
  });
});

e2eTest("both Test buttons lock while one test runs", async () => {
  let release;
  const slow = () => new Promise((resolve) => { release = () => resolve({ json: { symbol: "SPY", marketData: { connected: true }, trading: {} } }); });
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab() }, fakeWrites: { [TEST]: slow } }, async (app) => {
    await card(app.page);
    const buttons = app.page.locator(".schwab-test-actions button");
    await buttons.nth(0).click();
    await app.page.locator(".schwab-test-actions button", { hasText: "Testing Market Data..." }).waitFor({ timeout: 10_000 });
    assert.equal(await buttons.nth(0).isDisabled(), true);
    assert.equal(await buttons.nth(1).isDisabled(), true, "a second test cannot start while one runs");
    release();
    await app.page.locator(".schwab-test-actions button:enabled", { hasText: "Test Market Data" }).waitFor({ timeout: 10_000 });
  });
});

e2eTest("the expiry badge turns critical under a day and says Rejected for a refused key", async () => {
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: schwab({ marketData: { refreshTokenRemainingSeconds: 72000 }, trading: { refreshTokenValid: false, acceptedError: "invalid_client" } }) },
  }, async (app) => {
    await card(app.page);
    const badges = app.page.locator(".schwab-app-status .schwab-expiry-badge");
    await badges.nth(1).waitFor({ timeout: 15_000 });
    assert.match(await badges.nth(0).getAttribute("class"), /\bis-critical\b/);
    assert.equal((await badges.nth(0).locator("strong").innerText()).trim(), "0d 20h");
    assert.equal((await badges.nth(0).textContent()).trim(), "Expiry0d 20hrefresh left");
    assert.equal((await badges.nth(1).locator("strong").innerText()).trim(), "Rejected");
    assert.equal((await badges.nth(1).textContent()).trim(), "KeyRejectedcheck app secret");
    assert.equal(await badges.nth(1).getAttribute("title"), "Schwab rejected the app credentials: invalid_client");
  });
});

e2eTest("on a phone the key boxes and test buttons fit", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [STATUS]: schwab() } }, async (app) => {
    await card(app.page);
    for (const selector of [".schwab-credential-grid", ".schwab-test-actions"]) {
      const element = app.page.locator(selector).first();
      await element.scrollIntoViewIfNeeded();
      const box = await element.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
