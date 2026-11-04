// "Scan MAG7 OI" on the dashboard overview (2026-11-04 coverage).
//
// KNOWN GAP, kept as a todo test: the button lives in the "Dashboard" view
// (App.jsx, data-testid="dashboard-overview"), but "Dashboard" is not in the
// left rail (optionNavItems) and the remembered-page restore only accepts
// rail pages, so no click and no saved page can open it - the trader cannot
// reach this button. If the view comes back (or the button moves), this test
// becomes the real end-to-end check: one POST /api/oi-scan {universe:"mag7"},
// answered by the harness, and the progress status while it runs.

import assert from "node:assert/strict";
import test from "node:test";

import { marketHoursBlock, withApp } from "./harness.mjs";
import { sleep } from "./options-fixtures.mjs";

const SCAN = "POST /api/oi-scan";

test("Scan MAG7 OI posts one MAG7 scan and shows its progress", {
  todo: "the Dashboard view that holds this button cannot be opened from the app (not in the rail, not restorable)",
  skip: marketHoursBlock() || false,
  timeout: 180_000,
}, async () => {
  const reply = async () => {
    await sleep(1_500);
    return { json: { dashboard: { oiScannerAuto: { symbols: ["META", "AMD", "AAPL"], message: "PRIORITY MAG7 OI Scanner started for 3 underlyings." } },
      scanLabel: "MAG7 OI Scanner", symbolCount: 3, started: true } };
  };
  await withApp({ view: "Dashboard", fakeWrites: { [SCAN]: reply } }, async (app) => {
    const overview = app.page.locator('[data-testid="dashboard-overview"]');
    await overview.waitFor({ state: "visible", timeout: 30_000 });
    const button = overview.locator("button", { hasText: "Scan MAG7 OI" });
    await button.click();
    await app.page.locator('.dashboard-control-progress[role="status"]').waitFor({ timeout: 5_000 });
    await overview.locator("button", { hasText: "Scanning MAG7..." }).waitFor({ timeout: 1_400 });
    const scans = app.writes.filter((write) => `${write.method} ${write.path}` === SCAN);
    assert.deepEqual(scans.map((write) => JSON.parse(write.body)), [{ universe: "mag7" }]);
    assert.deepEqual(app.pageErrors, []);
  });
});
