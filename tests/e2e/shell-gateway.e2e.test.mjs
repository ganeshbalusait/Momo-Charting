// The gateway in the real stack (Vite :5173 -> gateway :3001 -> api_server
// :3002), coverage 2026-11-04, group "shell": loading a whole page never
// produces a server error, and the gateway's own health probe reports both
// processes. Read-only.

import assert from "node:assert/strict";

import { BASE_URL, PHONE, e2eTest, withApp } from "./harness.mjs";

e2eTest("loading Settings through the gateway produces no 5xx from any API", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    const failures = [];
    app.page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/") && response.request().method() === "GET" && response.status() >= 500) {
        failures.push(`${response.status()} ${url.pathname}`);
      }
    });
    await app.page.reload({ waitUntil: "domcontentloaded", timeout: 60_000 });
    await app.page.locator(".account-settings-card").waitFor({ state: "visible", timeout: 60_000 });
    await app.page.locator(".schwab-settings-card .schwab-app-status").first().waitFor({ state: "visible", timeout: 30_000 });
    await app.page.locator(".personal-api-keys-card .personal-provider-card header span").first().waitFor({ timeout: 30_000 });
    await app.page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
    assert.deepEqual(failures, [], "the gateway or api_server answered a page read with a server error");
    assert.ok(app.reads.some((read) => read.path === "/api/schwab/status"), "the page really read through the stack");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the gateway health probe reports the gateway and the pipeline", async () => {
  await withApp({ view: "Settings", viewport: PHONE }, async (app) => {
    const response = await app.page.request.get(`${BASE_URL}/api/gateway-health`);
    assert.equal(response.status(), 200);
    const body = await response.json();
    assert.equal(body.gateway, "ok");
    assert.equal(body.pipeline, "ok", "api_server behind the gateway is down");
    assert.equal(typeof body.serves.otherProxy, "number");
    assert.equal(typeof body.sse.sseClients, "number");
  });
});
