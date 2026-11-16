// "Scan MAG7 OI" (2026-11-04 coverage; reachable since 2026-11-14).
//
// The button used to live only in the "Dashboard" view (App.jsx,
// data-testid="dashboard-overview"), which is not in the left rail and cannot
// be restored, so the trader could not reach it. It is now on the Mag7
// Watchlist page (view key "Mag7 Scanner", data-testid="mag7-oi-scan"), using
// the same request and busy state as the Dashboard strip
// (frontend/src/automationControls.js). The check: one POST /api/oi-scan
// {universe:"mag7"}, answered by the harness, and the progress status while it
// runs - at desktop and at phone width.

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { sleep } from "./options-fixtures.mjs";

const SCAN = "POST /api/oi-scan";
const VIEW = "Mag7 Scanner";
const BAR = '[data-testid="mag7-oi-scan"]';

const reply = async () => {
  await sleep(1_500);
  return { json: { dashboard: { oiScannerAuto: { symbols: ["META", "AMD", "AAPL"], message: "PRIORITY MAG7 OI Scanner started for 3 underlyings." } },
    scanLabel: "MAG7 OI Scanner", symbolCount: 3, started: true } };
};

async function scanOnce(viewport) {
  await withApp({ view: VIEW, viewport, fakeWrites: { [SCAN]: reply } }, async (app) => {
    const bar = app.page.locator(BAR);
    await bar.waitFor({ state: "visible", timeout: 60_000 });
    const button = bar.locator("button", { hasText: "Scan MAG7 OI" });
    await button.scrollIntoViewIfNeeded();
    const box = await button.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= viewport.width + 1, `the button at ${JSON.stringify(box)} is cut off`);
    assert.equal(await overflowsSideways(app.page), false, "the Mag7 page scrolls sideways");
    assert.deepEqual(app.writes, [], "nothing is sent before the click");
    await button.click();
    await bar.locator('.dashboard-control-progress[role="status"]').waitFor({ timeout: 5_000 });
    await bar.locator("button", { hasText: "Scanning MAG7..." }).waitFor({ timeout: 1_400 });
    assert.equal(await bar.locator("button").isDisabled(), true, "no second scan while one is starting");
    // the reply lands: the button is ready again and the progress line is gone
    await bar.locator("button:enabled", { hasText: "Scan MAG7 OI" }).waitFor({ timeout: 10_000 });
    assert.equal(await bar.locator('.dashboard-control-progress[role="status"]').count(), 0);
    const scans = app.writes.filter((write) => `${write.method} ${write.path}` === SCAN);
    assert.deepEqual(scans.map((write) => JSON.parse(write.body)), [{ universe: "mag7" }]);
    assert.equal(app.writes.length, 1, `only the scan is sent: ${JSON.stringify(app.writes.map((w) => w.path))}`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
}

e2eTest("Scan MAG7 OI posts one MAG7 scan and shows its progress", async () => {
  await scanOnce(DESKTOP);
});

e2eTest("on a phone Scan MAG7 OI fits the screen and posts one MAG7 scan", async () => {
  await scanOnce(PHONE);
});
