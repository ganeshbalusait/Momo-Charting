// The MomX toolbar's Momo Alert settings: the phone channel (ntfy topic),
// Generate and Send test, end to end (coverage 2026-11-04, group "shell").
// The saved config is a fixture, every config save is echoed back by the
// harness and the test push is answered by it - nothing reaches ntfy.sh and
// the trader's real topic is never changed.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const CONFIG_READ = "GET /api/momx-scanner/momo-config";
const CONFIG_SAVE = "POST /api/momx-scanner/momo-config";
const TEST_PUSH = "POST /api/momo-alert/test-push";
const DIALOG = '.momo-settings[role="dialog"][aria-label="Momo Alert settings"]';

const config = (ntfyTopic = "") => ({
  json: {
    timeframes: { "5m": { armed: true, threshold: 3 }, "15m": { armed: false, threshold: 3 }, "30m": { armed: false, threshold: 3 },
      "1h": { armed: false, threshold: 3 }, "2h": { armed: false, threshold: 3 } },
    cooldownMinutes: 15, sound: true, ntfyTopic, pushWindow: { start: 555, end: 930 }, marketTurnPush: false,
  },
});
const echo = (write) => ({ json: JSON.parse(write.body || "{}") });

async function openSettings(page) {
  const gear = page.locator('button[aria-label="Momo Alert settings"]');
  await gear.waitFor({ state: "visible", timeout: 60_000 });
  await gear.click();
  await page.locator(DIALOG).waitFor({ state: "visible", timeout: 15_000 });
  const topic = page.locator(`${DIALOG} .momo-settings-push-row input`);
  await topic.waitFor({ state: "visible", timeout: 15_000 });
  return {
    topic,
    generate: page.locator(`${DIALOG} .momo-settings-push-row button`, { hasText: "Generate" }),
    sendTest: page.locator(`${DIALOG} .momo-settings-push-row button`, { hasText: /Send test|Sending/ }),
  };
}

e2eTest("the gear opens the Momo Alert settings dialog", async () => {
  await withApp({ view: "MomX Scanner", fakeReads: { [CONFIG_READ]: config("agx-momo-0123456789ab") } }, async (app) => {
    const { topic } = await openSettings(app.page);
    assert.equal(await topic.inputValue(), "agx-momo-0123456789ab");
    assert.match(await app.page.locator(`${DIALOG} .momo-settings-hint`).innerText(), /subscribe to this exact name/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Generate makes an unguessable agx-momo- name and saves it", async () => {
  await withApp({ view: "MomX Scanner", fakeReads: { [CONFIG_READ]: config("") }, fakeWrites: { [CONFIG_SAVE]: echo } }, async (app) => {
    const { topic, generate, sendTest } = await openSettings(app.page);
    assert.equal(await sendTest.isDisabled(), true, "nothing to test without a topic");
    await generate.click();
    await app.page.waitForFunction((selector) => /^agx-momo-[0-9a-f]{12}$/.test(document.querySelector(selector)?.value || ""),
      `${DIALOG} .momo-settings-push-row input`, { timeout: 10_000 });
    const name = await topic.inputValue();
    const saved = app.writes.filter((w) => w.path === "/api/momx-scanner/momo-config").map((w) => JSON.parse(w.body).ntfyTopic);
    assert.ok(saved.includes(name), "the generated name was saved");
    assert.equal(await sendTest.isEnabled(), true);
  });
});

e2eTest("Send test saves first, then reports success", async () => {
  await withApp({
    view: "MomX Scanner",
    fakeReads: { [CONFIG_READ]: config("agx-momo-0123456789ab") },
    fakeWrites: { [CONFIG_SAVE]: echo, [TEST_PUSH]: { json: { ok: true, topic: "agx-momo-0123456789ab" } } },
  }, async (app) => {
    const { sendTest } = await openSettings(app.page);
    await sendTest.click();
    const result = app.page.locator(`${DIALOG} .momo-test-result.is-ok`);
    await result.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await result.innerText(), /^Sent - check your phone\./);
    const order = app.writes.map((w) => w.path).filter((p) => p === "/api/momx-scanner/momo-config" || p === "/api/momo-alert/test-push");
    assert.deepEqual(order.slice(-2), ["/api/momx-scanner/momo-config", "/api/momo-alert/test-push"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a failed test shows the server's verdict word for word", async () => {
  const verdict = "No ntfy topic saved for you.";
  await withApp({
    view: "MomX Scanner",
    fakeReads: { [CONFIG_READ]: config("agx-momo-0123456789ab") },
    fakeWrites: { [CONFIG_SAVE]: echo, [TEST_PUSH]: { json: { ok: false, error: verdict } } },
  }, async (app) => {
    const { sendTest } = await openSettings(app.page);
    await sendTest.click();
    const result = app.page.locator(`${DIALOG} .momo-test-result.is-fail`);
    await result.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal((await result.innerText()).trim(), verdict);
  });
});

e2eTest("clearing the topic disables Send test", async () => {
  await withApp({ view: "MomX Scanner", fakeReads: { [CONFIG_READ]: config("agx-momo-0123456789ab") }, fakeWrites: { [CONFIG_SAVE]: echo } }, async (app) => {
    const { topic, sendTest } = await openSettings(app.page);
    assert.equal(await sendTest.isEnabled(), true);
    await topic.fill("");
    await app.page.locator(`${DIALOG} .momo-settings-push-row button:disabled`, { hasText: "Send test" }).waitFor({ timeout: 10_000 });
  });
});

e2eTest("on a phone the dialog fits the screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, fakeReads: { [CONFIG_READ]: config("agx-momo-0123456789ab") } }, async (app) => {
    await openSettings(app.page);
    const box = await app.page.locator(DIALOG).boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `dialog at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});
