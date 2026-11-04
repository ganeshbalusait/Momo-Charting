// Settings -> Schwab -> "Renew both" (2026-11-04), end to end in the real app.
// The start call is answered by the harness, so no Schwab login is started;
// the status reads before and after it are the live server's.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const START = "POST /api/schwab/oauth/start-both";
const RENEW = ".schwab-renew-button";
const MESSAGE = ".schwab-settings-message";

const startReply = (profiles, extra = {}) => ({
  json: {
    authorizationUrl: "about:blank#agx-e2e-schwab",
    profiles,
    callbackListening: true,
    manualCallbackRequired: false,
    tokenManager: "schwab-py",
    ...extra,
  },
});

async function renewButton(page) {
  const button = page.locator(RENEW).first();
  await button.waitFor({ state: "visible", timeout: 60_000 });
  // Disabled until the status read says the keys are saved.
  await page.locator(`${RENEW}:enabled`).first().waitFor({ timeout: 30_000 });
  const label = (await button.innerText()).trim();
  assert.match(label, /^Renew (both|Market Data)$/);
  return { button, profiles: label === "Renew both" ? ["market_data", "trading"] : ["market_data"] };
}

e2eTest("Renew both opens the Schwab window and walks the steps", async () => {
  let profiles = ["market_data", "trading"];
  await withApp({ view: "Settings", fakeWrites: { [START]: () => startReply(profiles) } }, async (app) => {
    const found = await renewButton(app.page);
    profiles = found.profiles;
    const popup = app.context.waitForEvent("page");
    await found.button.click();
    const login = await popup;
    await login.waitForURL(/agx-e2e-schwab/, { timeout: 15_000 });
    const message = app.page.locator(MESSAGE);
    await message.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await message.innerText(), profiles.length > 1 ? /^Step 1 of 2: log in to Schwab/ : /^Log in to Schwab in the new window/);
    assert.deepEqual(app.writes.filter((write) => write.path === "/api/schwab/oauth/start-both").length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("from a browser that is not on the PC, Renew both says to paste instead", async () => {
  await withApp({ view: "Settings", fakeWrites: { [START]: startReply(["market_data", "trading"], { callbackListening: false, manualCallbackRequired: true }) } }, async (app) => {
    const { button } = await renewButton(app.page);
    await button.click();
    const message = app.page.locator(MESSAGE);
    await message.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await message.innerText(), /cannot finish by itself/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused start shows the server's reason", async () => {
  const reason = "Save the Schwab app key and secret first.";
  await withApp({ view: "Settings", fakeWrites: { [START]: { status: 400, json: { error: reason } } } }, async (app) => {
    const { button } = await renewButton(app.page);
    await button.click();
    const message = app.page.locator(MESSAGE);
    await message.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await message.innerText()).trim(), reason);
    assert.equal(await button.isEnabled(), true, "the button comes back for another try");
  });
});

e2eTest("on a phone the renew button fits on screen", async () => {
  await withApp({ view: "Settings", viewport: PHONE }, async (app) => {
    const { button } = await renewButton(app.page);
    await button.scrollIntoViewIfNeeded();
    const box = await button.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `button at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});
