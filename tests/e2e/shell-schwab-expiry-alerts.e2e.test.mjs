// Schwab login expiry alerts: the phone push itself has no screen (see
// tests/test_cov_shell_schwab_expiry_alerts_api.py for the push end to end);
// what the trader sees is the Settings promise that the alerts are on, beside
// the expiry badge (coverage 2026-11-04, group "shell"). Read-only, live status.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

for (const [label, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`Settings says renewal alerts are on, next to the expiry badges (${label})`, async () => {
    await withApp({ view: "Settings", ...(viewport ? { viewport } : {}) }, async (app) => {
      const policy = app.page.locator(".schwab-settings-card .schwab-renewal-alert-policy");
      await policy.waitFor({ state: "attached", timeout: 60_000 });
      await policy.scrollIntoViewIfNeeded();
      assert.equal((await policy.locator("strong").innerText()).trim(), "Automatic renewal alerts are on");
      assert.match(await policy.locator("small").innerText(), /alert at 3 days, 1 day, and expiration/);
      await app.page.locator(".schwab-settings-card .schwab-app-status .schwab-expiry-badge").first().waitFor({ timeout: 30_000 });
      assert.equal(await app.page.locator(".schwab-settings-card .schwab-app-status .schwab-expiry-badge").count(), 2);
      if (viewport) {
        const box = await policy.boundingBox();
        assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `the note at ${JSON.stringify(box)} is cut off`);
      }
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
