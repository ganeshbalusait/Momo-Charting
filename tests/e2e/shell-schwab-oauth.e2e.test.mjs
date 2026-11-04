// Schwab logins from Settings end to end (coverage 2026-11-04, group "shell"):
// Renew both walking to done, the single Authenticate buttons, the pasted
// callback, and the first-time certificate note. Complements
// settings-schwab-renew.e2e.test.mjs. The status reads are a fixture sequence
// and every start / token POST is answered by the harness, so no Schwab login
// is ever started and no token is written. Pop-ups go to about:blank.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";

const STATUS = "GET /api/schwab/status";
const MESSAGE = ".schwab-settings-card .schwab-settings-message";

const profile = (expires, over = {}) => ({
  configured: true, credentialsConfigured: true, clientIdMasked: "abcd...123456", hasRefreshToken: true,
  refreshTokenValid: true, refreshTokenRemainingSeconds: 3 * 86400, refreshTokenExpiresAt: expires,
  tokenSavedAt: "2026-10-30T04:08:16Z", redirectUri: "https://127.0.0.1", ...over,
});
const schwab = (market, trading, top = {}) => ({ json: { ...market, marketData: market, trading, callbackCertTrusted: true, ...top } });
const OLD_M = "2026-11-06T04:08:16Z";
const OLD_T = "2026-11-06T04:09:15Z";
const NEW_M = "2026-11-13T15:01:00Z";
const NEW_T = "2026-11-13T15:02:00Z";

async function card(page) {
  await page.locator(".schwab-renew-button:enabled").waitFor({ timeout: 60_000 });
}

async function message(page, text, timeout = 15_000) {
  const line = page.locator(MESSAGE).filter({ hasText: text });
  await line.first().waitFor({ state: "visible", timeout });
  return (await line.first().innerText()).trim();
}

e2eTest("Renew both walks step 1, step 2 and done as each login lands", async () => {
  let phase = 0;   // 0: before; then each status read after the start advances one login
  const status = () => {
    if (phase === 0) return schwab(profile(OLD_M), profile(OLD_T));
    phase += 1;
    if (phase <= 3) return schwab(profile(OLD_M), profile(OLD_T));                // still waiting
    if (phase <= 5) return schwab(profile(NEW_M), profile(OLD_T));                // market data renewed
    return schwab(profile(NEW_M), profile(NEW_T));                                // both renewed
  };
  const start = () => {
    phase = 1;
    return { json: { authorizationUrl: "about:blank#agx-e2e-renew", profiles: ["market_data", "trading"], callbackListening: true, manualCallbackRequired: false } };
  };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status }, fakeWrites: { "POST /api/schwab/oauth/start-both": start } }, async (app) => {
    await card(app.page);
    assert.equal((await app.page.locator(".schwab-renew-button").innerText()).trim(), "Renew both");
    const seen = new Set();
    const watch = setInterval(async () => {
      const text = await app.page.locator(MESSAGE).first().innerText().catch(() => "");
      if (text) seen.add(text.trim());
    }, 250);
    try {
      const popup = app.context.waitForEvent("page");
      await app.page.locator(".schwab-renew-button").click();
      await (await popup).waitForURL(/agx-e2e-renew/, { timeout: 15_000 });
      const done = await message(app.page, "Both Schwab logins renewed", 60_000);
      assert.match(done, /Next renewal by /);
    } finally {
      clearInterval(watch);
    }
    const all = [...seen];
    assert.ok(all.some((t) => /^Step 1 of 2/.test(t)), `never showed step 1: ${JSON.stringify(all)}`);
    assert.ok(all.some((t) => /Step 2 of 2/.test(t)), `never showed step 2: ${JSON.stringify(all)}`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("with only Market Data set up the button says Renew Market Data", async () => {
  const none = { credentialsConfigured: false, configured: false, hasRefreshToken: false, refreshTokenExpiresAt: null };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab(profile(OLD_M), none) } }, async (app) => {
    await card(app.page);
    assert.equal((await app.page.locator(".schwab-renew-button").innerText()).trim(), "Renew Market Data");
    const trading = app.page.locator(".schwab-settings-actions button", { hasText: "Authenticate Optional Trading" });
    assert.equal(await trading.isDisabled(), true);
  });
});

e2eTest("Authenticate Market Data opens the Schwab window and says the callback is listening", async () => {
  const start = { json: { authorizationUrl: "about:blank#agx-e2e-auth", redirectUri: "https://127.0.0.1", callbackListening: true, manualCallbackRequired: false } };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T)) }, fakeWrites: { "POST /api/schwab/oauth/start": start } }, async (app) => {
    await card(app.page);
    const popup = app.context.waitForEvent("page");
    await app.page.locator(".schwab-settings-actions button", { hasText: "Authenticate Market Data" }).click();
    await (await popup).waitForURL(/agx-e2e-auth/, { timeout: 15_000 });
    assert.equal(await message(app.page, "Callback is listening"),
      "Callback is listening. Complete the Schwab Market Data login in the new window.");
    assert.equal(app.writes.find((w) => w.path === "/api/schwab/oauth/start").search, "?profile=market_data");
  });
});

e2eTest("from a browser off the PC, Authenticate says to paste the redirect", async () => {
  const start = { json: { authorizationUrl: "about:blank#agx-e2e-auth", callbackListening: false, manualCallbackRequired: true } };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T)) }, fakeWrites: { "POST /api/schwab/oauth/start": start } }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-settings-actions button", { hasText: "Authenticate Optional Trading" }).click();
    assert.equal(await message(app.page, "paste it into"),
      "Complete the Schwab Accounts & Trading login. When the redirect page cannot connect, copy its full address-bar URL and paste it into the Accounts & Trading callback field below.");
    assert.equal(app.writes.find((w) => w.path === "/api/schwab/oauth/start").search, "?profile=trading");
  });
});

e2eTest("pasting the redirect and pressing Complete finishes the Market Data login", async () => {
  const pasted = "https://127.0.0.1/?code=E2E-CODE&state=E2E-STATE";
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T)) },
    fakeWrites: { "POST /api/schwab/token": { json: { status: "connected", hasAccessToken: true, hasRefreshToken: true } } },
  }, async (app) => {
    await card(app.page);
    const box = app.page.locator(".schwab-oauth-callback").first();
    const complete = box.locator("button");
    assert.equal(await complete.isDisabled(), true, "nothing to complete yet");
    await box.locator("input").fill(pasted);
    assert.equal(await complete.isEnabled(), true);
    await complete.click();
    assert.equal(await message(app.page, "token created"), "Schwab Market Data token created. Run Test Market Data.");
    assert.equal(await box.locator("input").inputValue(), "");
    assert.deepEqual(JSON.parse(app.writes.find((w) => w.path === "/api/schwab/token").body),
      { profile: "market_data", authorizationResponseUrl: pasted });
  });
});

e2eTest("a refused paste shows the server's reason", async () => {
  const reason = "Save your Schwab app key and secret first.";
  await withApp({
    view: "Settings",
    fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T)) },
    fakeWrites: { "POST /api/schwab/token": { status: 400, json: { error: reason } } },
  }, async (app) => {
    await card(app.page);
    const box = app.page.locator(".schwab-oauth-callback").nth(1);
    await box.locator("input").fill("https://127.0.0.1/?code=x&state=y");
    await box.locator("button").click();
    assert.equal(await message(app.page, "first"), reason);
    assert.equal(JSON.parse(app.writes.find((w) => w.path === "/api/schwab/token").body).profile, "trading");
  });
});

e2eTest("signed out in the middle of a renewal, the wait stops and says why", async () => {
  let started = false;
  const status = () => (started ? { status: 401, json: { error: "Sign in to continue." } } : schwab(profile(OLD_M), profile(OLD_T)));
  const start = () => {
    started = true;
    return { json: { authorizationUrl: "about:blank#agx-e2e-renew", profiles: ["market_data", "trading"], callbackListening: true, manualCallbackRequired: false } };
  };
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status }, fakeWrites: { "POST /api/schwab/oauth/start-both": start } }, async (app) => {
    await card(app.page);
    await app.page.locator(".schwab-renew-button").click();
    assert.equal(await message(app.page, "signed out of AGX", 30_000),
      "You were signed out of AGX, so the renewal stopped waiting. Sign in and check the expiry above.");
  });
});

e2eTest("when the callback certificate is not trusted, the first-time note explains the warning", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T), { callbackCertTrusted: false }) } }, async (app) => {
    await card(app.page);
    const note = app.page.locator(".schwab-callback-note", { hasText: "Your connection is not private" });
    await note.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await note.innerText(), /Continue to 127\.0\.0\.1/);
  });
});

e2eTest("with a trusted certificate there is no warning note", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: schwab(profile(OLD_M), profile(OLD_T)) } }, async (app) => {
    await card(app.page);
    assert.equal(await app.page.locator(".schwab-callback-note", { hasText: "Your connection is not private" }).count(), 0);
  });
});
