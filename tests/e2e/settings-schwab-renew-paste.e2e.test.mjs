// Settings -> Schwab: renewing by pasting from app.agxtrade.com on any
// computer (2026-11-04). One Renew both click, then Market Data and Accounts &
// Trading are each finished by pasting into their own box, with an "Open ...
// login" button for step 2; and the full app keys are shown. The status reads
// are a fixture and every start / token POST is answered by the harness, so no
// Schwab login is started and no token is written. Pop-ups go to about:blank.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const STATUS = "GET /api/schwab/status";
const START = "POST /api/schwab/oauth/start-both";
const TOKEN = "POST /api/schwab/token";
const MESSAGE = ".schwab-settings-card .schwab-settings-message";
const OPEN_NEXT = ".schwab-manual-renew-open";
const MARKET_KEY = "wvo0-E2E-FULL-MARKET-DATA-KEY-kOjMc5";
const TRADING_KEY = "iERd-E2E-FULL-TRADING-KEY-FvNaS3";

const profile = (clientId) => ({
  configured: true, credentialsConfigured: true, clientId, clientIdMasked: `${clientId.slice(0, 4)}...${clientId.slice(-6)}`,
  hasRefreshToken: true, refreshTokenValid: true, refreshTokenRemainingSeconds: 3 * 86400,
  refreshTokenExpiresAt: "2026-11-06T04:08:16Z", redirectUri: "https://127.0.0.1",
});
const status = { json: { ...profile(MARKET_KEY), marketData: profile(MARKET_KEY), trading: profile(TRADING_KEY), callbackCertTrusted: true } };
const manualStart = { json: {
  authorizationUrl: "about:blank#agx-e2e-paste-md",
  authorizationUrls: { market_data: "about:blank#agx-e2e-paste-md", trading: "about:blank#agx-e2e-paste-tr" },
  profiles: ["market_data", "trading"], callbackListening: false, manualCallbackRequired: true,
} };
const MD_PASTE = "https://127.0.0.1/?code=E2E-MD&session=s&state=MD-1";
const TR_PASTE = "https://127.0.0.1/?code=E2E-TR&session=s&state=TR-1";

async function card(page) {
  await page.locator(".schwab-renew-button:enabled").waitFor({ timeout: 60_000 });
}

async function message(page, text) {
  const line = page.locator(MESSAGE).filter({ hasText: text }).first();
  await line.waitFor({ state: "visible", timeout: 15_000 });
  return (await line.innerText()).trim();
}

async function paste(page, index, url) {
  const box = page.locator(".schwab-oauth-callback").nth(index);
  await box.locator("input").fill(url);
  await box.locator("button").click();
}

e2eTest("from another computer: Renew both, paste Market Data, open step 2, paste Trading, done", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status }, fakeWrites: { [START]: manualStart, [TOKEN]: { json: { status: "connected", hasAccessToken: true, hasRefreshToken: true } } } }, async (app) => {
    await card(app.page);
    const first = app.context.waitForEvent("page");
    await app.page.locator(".schwab-renew-button").click();
    await (await first).waitForURL(/agx-e2e-paste-md/, { timeout: 15_000 });
    assert.match(await message(app.page, "Step 1 of 2"), /cannot finish by itself.*Market Data callback URL box/s);
    assert.equal((await app.page.locator(OPEN_NEXT).innerText()).trim(), "Open Market Data login");

    await paste(app.page, 0, MD_PASTE);
    assert.match(await message(app.page, "Step 2 of 2"), /^✓ Market Data renewed\. Step 2 of 2: click Open Accounts & Trading login/);
    const open = app.page.locator(OPEN_NEXT);
    assert.equal((await open.innerText()).trim(), "Open Accounts & Trading login");
    const second = app.context.waitForEvent("page");
    await open.click();
    await (await second).waitForURL(/agx-e2e-paste-tr/, { timeout: 15_000 });

    await paste(app.page, 1, TR_PASTE);
    assert.equal(await message(app.page, "Both Schwab logins renewed"), "✓ Both Schwab logins renewed.");
    assert.equal(await app.page.locator(OPEN_NEXT).count(), 0, "no step left to open");
    const tokens = app.writes.filter((w) => w.path === "/api/schwab/token").map((w) => JSON.parse(w.body));
    assert.deepEqual(tokens, [
      { profile: "market_data", authorizationResponseUrl: MD_PASTE },
      { profile: "trading", authorizationResponseUrl: TR_PASTE },
    ]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a paste in the wrong box shows the server's plain reason and step 1 stays open", async () => {
  const reason = "This address is from the Market Data sign-in. Paste it into the Market Data callback URL box instead.";
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status }, fakeWrites: { [START]: manualStart, [TOKEN]: { status: 400, json: { error: reason } } } }, async (app) => {
    await card(app.page);
    const popup = app.context.waitForEvent("page");
    await app.page.locator(".schwab-renew-button").click();
    await (await popup).waitForURL(/agx-e2e-paste-md/, { timeout: 15_000 });
    await paste(app.page, 1, MD_PASTE);
    assert.equal(await message(app.page, "Paste it into the Market Data"), reason);
    assert.equal((await app.page.locator(OPEN_NEXT).innerText()).trim(), "Open Market Data login");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the full app keys are shown so they can be checked against Schwab", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: status } }, async (app) => {
    await card(app.page);
    const keys = await app.page.locator(".schwab-app-key").allInnerTexts();
    assert.deepEqual(keys.map((key) => key.trim()), [MARKET_KEY, TRADING_KEY]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the full keys and the step-2 button fit on screen", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: { [STATUS]: status }, fakeWrites: { [START]: manualStart, [TOKEN]: { json: { status: "connected" } } } }, async (app) => {
    await card(app.page);
    const popup = app.context.waitForEvent("page");
    await app.page.locator(".schwab-renew-button").click();
    await popup;
    await paste(app.page, 0, MD_PASTE);
    await message(app.page, "Step 2 of 2");
    for (const selector of [".schwab-app-key", OPEN_NEXT]) {
      const element = app.page.locator(selector).first();
      await element.scrollIntoViewIfNeeded();
      const box = await element.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
