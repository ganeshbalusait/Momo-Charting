// Momo Alert, end to end: the settings dialog (arm a timeframe, threshold on
// blur, Send test verdicts) and the app-wide MOMO banner.
// Every save and the phone test are answered by the harness: no account's
// settings change and nothing is pushed to ntfy. The settings and alert reads
// are fixtures so each screen has a known answer.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = { "momx.scanner.activeList": "Mag7", "momx.scanner.direction": "bull", "momx.scanner.activeView": "live", "momx-auto-cards-on": "0" };
const OPEN = "button.momx-icon-btn[aria-label='Momo Alert settings']";
const DIALOG = ".momo-settings[role='dialog'][aria-label='Momo Alert settings']";
const SAVE = "POST /api/momx-scanner/momo-config";
const TEST = "POST /api/momo-alert/test-push";

const CONFIG = {
  timeframes: { "5m": { armed: true, threshold: 3 }, "15m": { armed: false, threshold: 3 }, "30m": { armed: false, threshold: 3 },
    "1h": { armed: false, threshold: 3 }, "2h": { armed: false, threshold: 3 } },
  cooldownMinutes: 15, sound: true, ntfyTopic: "agx-momo-e2e0000001", pushWindow: { start: 555, end: 930 }, marketTurnPush: false,
};
const echo = (write) => ({ json: JSON.parse(write.body) });
const saves = (app) => app.writes.filter((write) => write.path === "/api/momx-scanner/momo-config").map((write) => JSON.parse(write.body));

async function openSettings(page) {
  await page.locator(OPEN).waitFor({ timeout: 90_000 });
  await page.locator(OPEN).click();
  const dialog = page.locator(DIALOG);
  await dialog.locator("label.momo-settings-row").first().waitFor({ timeout: 15_000 });
  return dialog;
}

const row = (dialog, name) => dialog.locator("label.momo-settings-row", { has: dialog.page().locator(".momo-settings-tf", { hasText: new RegExp(`^${name}$`) }) });

e2eTest("the settings list every armable timeframe and Sound; ticking 15m saves once with it armed", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/momo-config": { json: CONFIG } },
    fakeWrites: { [SAVE]: echo } }, async (app) => {
    const dialog = await openSettings(app.page);
    assert.deepEqual((await dialog.locator(".momo-settings-tf").allInnerTexts()).map((t) => t.trim()), ["5m", "15m", "30m", "1h", "2h", "Sound"]);
    assert.match(await dialog.locator(".momo-settings-note").first().innerText(), /One alert per ticker per 15 min/);
    await row(dialog, "15m").locator("input[type='checkbox']").check();
    await app.page.waitForTimeout(500);
    const sent = saves(app);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].timeframes["15m"].armed, true);
    assert.equal(sent[0].timeframes["5m"].armed, true);              // the rest of the config rides along unchanged
    assert.equal(sent[0].ntfyTopic, CONFIG.ntfyTopic);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a threshold is saved once, when the box loses focus, not per keystroke", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/momo-config": { json: CONFIG } },
    fakeWrites: { [SAVE]: echo } }, async (app) => {
    const dialog = await openSettings(app.page);
    const box = row(dialog, "5m").locator("input[type='number']");
    await box.fill("");
    await box.pressSequentially("4.5");
    assert.equal(saves(app).length, 0, "typing alone saves nothing");
    await box.blur();
    await app.page.waitForTimeout(500);
    const sent = saves(app);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].timeframes["5m"].threshold, 4.5);
    // Out of range is clamped before it is sent.
    await box.fill("80");
    await box.press("Enter");
    await app.page.waitForTimeout(500);
    assert.equal(saves(app).at(-1).timeframes["5m"].threshold, 50);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Send test reports the server's verdict - sent, or the reason it failed", async () => {
  let verdict = { ok: true, topic: CONFIG.ntfyTopic };
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/momo-config": { json: CONFIG } },
    fakeWrites: { [SAVE]: echo, [TEST]: () => ({ json: verdict }) } }, async (app) => {
    const dialog = await openSettings(app.page);
    const send = dialog.locator(".momo-settings-push button", { hasText: "Send test" });
    await send.click();
    const ok = dialog.locator(".momo-test-result.is-ok");
    await ok.waitFor({ timeout: 10_000 });
    assert.match(await ok.innerText(), /^Sent - check your phone/);
    assert.equal(app.writes.filter((write) => write.path === "/api/momo-alert/test-push").length, 1);

    verdict = { ok: false, topic: CONFIG.ntfyTopic, error: "The push did not go through (OSError). Check the server's internet connection and try again." };
    await send.click();
    const fail = dialog.locator(".momo-test-result.is-fail");
    await fail.waitFor({ timeout: 10_000 });
    assert.equal((await fail.innerText()).trim(), verdict.error);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("with no phone topic saved, Send test is disabled until Generate makes one", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-scanner/momo-config": { json: { ...CONFIG, ntfyTopic: "" } } },
    fakeWrites: { [SAVE]: echo } }, async (app) => {
    const dialog = await openSettings(app.page);
    const send = dialog.locator(".momo-settings-push button", { hasText: "Send test" });
    assert.equal(await send.isDisabled(), true);
    assert.equal(await dialog.locator(".momo-settings-push input[placeholder='off']").inputValue(), "");
    await dialog.locator(".momo-settings-push button", { hasText: "Generate" }).click();
    await app.page.waitForFunction(() => /^agx-momo-[0-9a-f]{12}$/.test(document.querySelector(".momo-settings-push input")?.value || ""), null, { timeout: 5_000 });
    assert.equal(await send.isEnabled(), true);
    assert.match(saves(app).at(-1).ntfyTopic, /^agx-momo-[0-9a-f]{12}$/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a new alert shows the MOMO banner on another page, and Dismiss removes it", async () => {
  let polls = 0;
  const alert = { type: "momo_alert", symbol: "NVDA", timeframe: "5m", value: 3.24, threshold: 3, pctChange: 1.5, list: "Mag7", at: new Date().toISOString() };
  await withApp({ view: "Premarket Scanner", fakeReads: {
    // First poll after load is absorbed silently (alerts from before the tab opened); the next one is news.
    "GET /api/momx-scanner/momo-alerts": () => { polls += 1; return { json: { alerts: polls === 1 ? [] : [alert], sound: false } }; },
  } }, async (app) => {
    const { page } = app;
    await page.locator("[data-testid='premarket-scanner-view']").waitFor({ timeout: 90_000 });
    const banner = page.locator(".momo-alert-banner[role='alert']");
    await banner.waitFor({ state: "visible", timeout: 40_000 });
    assert.equal((await banner.locator(".momo-alert-title").innerText()).trim(), "MOMO");
    assert.match((await banner.locator(".momo-alert-item").innerText()).replace(/\s+/g, " "), /NVDA\s*RVOL 5m 3\.2x · \+1\.50%/);
    await banner.locator("button.momo-alert-dismiss[aria-label='Dismiss']").click();
    await banner.waitFor({ state: "detached", timeout: 5_000 });
    // The same alert on the next poll is not shown again.
    await page.waitForTimeout(16_000);
    assert.equal(await banner.count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the settings dialog is on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER, fakeReads: { "GET /api/momx-scanner/momo-config": { json: CONFIG } } }, async (app) => {
    const dialog = await openSettings(app.page);
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= PHONE.width && box.y < PHONE.height, `settings at ${JSON.stringify(box)}`);
    await dialog.locator(".momo-settings-head button[aria-label='Close']").tap();
    await dialog.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
