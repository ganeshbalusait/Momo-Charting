// The public health probe through the whole stack, and the header it feeds
// (coverage 2026-11-04, group "shell"). Live data: structure, not values.

import assert from "node:assert/strict";

import { BASE_URL, e2eTest, withApp } from "./harness.mjs";

e2eTest("/api/health answers through Vite and the gateway, and the header renders", async () => {
  await withApp({ view: "Release Notes" }, async (app) => {
    const response = await app.page.request.get(`${BASE_URL}/api/health`);
    assert.equal(response.status(), 200);
    const body = await response.json();
    assert.equal(typeof body.ok, "boolean");
    for (const section of ["runtime", "marketStream", "alpacaStream", "threads", "chartPrewarm"]) {
      assert.ok(section in body, `health has no ${section}`);
    }
    assert.ok(body.threads.total > 0);
    assert.doesNotMatch(JSON.stringify(body).toLowerCase(), /password|client_secret|secret_key/);
    const pill = app.page.locator(".topbar.trading-header .market-open-pill");
    await pill.waitFor({ state: "visible", timeout: 60_000 });
    assert.match((await pill.innerText()).trim(), /^MARKET \S+/);
    assert.deepEqual(app.pageErrors, []);
  });
});
