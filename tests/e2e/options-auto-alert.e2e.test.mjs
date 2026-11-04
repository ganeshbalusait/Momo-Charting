// Auto Alert page and app-wide alert delivery (2026-11-04 coverage), end to
// end in the real app. The alert service is a fixture: GET /api/oi-auto-alerts
// answers from an in-test state, and every POST (symbols, settings, rearm) is
// answered by the harness from the same state - nothing reaches the live
// alert list or starts a ladder build. Card prices are faked too.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { expiryIn, sleep } from "./options-fixtures.mjs";

const MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"];
const DRAWER = '[data-testid="oi-auto-alert-drawer"]';
const today = () => expiryIn(0);

function confirmEvent(id, symbol = "NVDA", side = "CALL", strike = 150) {
  return {
    id, kind: "confirm", symbol, side, level: { strike }, price: strike + 0.6,
    at: `${today()}T10:40:00-05:00`, sessionDate: today(),
    message: `${symbol} ${side} OI ${strike} CONFIRMED · 5m close ${strike + 0.6}`,
  };
}

function nvdaRow({ callFired = false } = {}) {
  const level = (strike, openInterest) => ({ strike, openInterest, importance: 5 });
  return {
    symbol: "NVDA", status: "armed", sourceGroup: "manual", spot: 148.2, armedToday: true,
    callLevels: [level(150, 90_000), level(155, 60_000)], putLevels: [level(145, 70_000), level(140, 40_000)],
    activeCall: level(150, 90_000), nextCall: level(155, 60_000), activePut: level(145, 70_000), nextPut: level(140, 40_000),
    callState: callFired ? "fired" : "armed", putState: "armed", rearmAvailable: callFired,
    confirmedCallStrikes: [], confirmedPutStrikes: [], levelsUpdatedAt: `${today()}T09:15:20-05:00`,
  };
}

// One mutable alert service per test: reads and writes see the same state.
function alertService({ manual = [], includeMag7 = true, enabled = true, rows = [], events = [], regularHours = false } = {}) {
  const state = { manual: [...manual], includeMag7, enabled, rows, events, polls: 0 };
  const payload = () => {
    const watched = [...(state.includeMag7 ? MAG7 : []), ...state.manual.filter((s) => !MAG7.includes(s) || !state.includeMag7)];
    const rowSymbols = state.rows.map((row) => row.symbol);
    return {
      enabled: state.enabled, includeMag7: state.includeMag7, mag7Symbols: MAG7, manualSymbols: [...state.manual], maxManualSymbols: 30,
      status: state.enabled ? "Armed" : "Paused",
      message: state.enabled ? "Waiting for the 9:15 AM ET OI-ladder build." : "Automatic OI alerts are paused.",
      refreshing: false, lastError: "", pendingSymbols: watched.filter((symbol) => !rowSymbols.includes(symbol)),
      rows: state.rows, events: typeof state.events === "function" ? state.events(state.polls) : state.events,
      sessionAlerts: { days: [] }, serverTime: `${today()}T11:00:00-05:00`,
      session: { isTradingDay: true, regularHours },
    };
  };
  return {
    state,
    fakeReads: {
      "GET /api/oi-auto-alerts": () => { state.polls += 1; return { json: payload() }; },
      "GET /api/watchlist-quotes": { json: { rows: [] } },
    },
    fakeWrites: {
      "POST /api/oi-auto-alerts/symbols": (write) => {
        const body = JSON.parse(write.body || "{}");
        if (body.add && !state.manual.includes(body.add)) state.manual.push(body.add);
        if (body.remove) state.manual = state.manual.filter((symbol) => symbol !== body.remove);
        return { json: payload() };
      },
      "POST /api/oi-auto-alerts/settings": (write) => {
        const body = JSON.parse(write.body || "{}");
        if ("enabled" in body) state.enabled = body.enabled;
        if ("includeMag7" in body) state.includeMag7 = body.includeMag7;
        return { json: payload() };
      },
      "POST /api/oi-auto-alerts/rearm": () => {
        state.rows = state.rows.map((row) => ({ ...row, callState: "armed", rearmAvailable: false }));
        return { json: payload() };
      },
      "POST /api/oi-auto-alerts/refresh": () => ({ json: { ...payload(), refreshRequested: true } }),
    },
  };
}

const posts = (app, path) => app.writes.filter((write) => write.path === path).map((write) => JSON.parse(write.body || "{}"));

async function drawerReady(page) {
  await page.locator(`${DRAWER} .auto-alert-drawer-status`).waitFor({ state: "visible", timeout: 60_000 });
  await page.locator(`${DRAWER} .auto-alert-drawer-message`, { hasText: /OI-ladder build|paused/ }).waitFor({ timeout: 30_000 });
}

e2eTest("the page shows the status pill, the MAG7 cards being built, or the empty state", async () => {
  const service = alertService();
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    assert.equal(await app.page.locator(`${DRAWER} .auto-alert-drawer-status`).innerText(), "Armed");
    assert.equal(await app.page.locator(`${DRAWER} .auto-alert-card.is-pending`).count(), 7);
    assert.equal(await app.page.locator(`${DRAWER} .auto-alert-drawer-empty`).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
  const empty = alertService({ includeMag7: false });
  await withApp({ view: "Auto Alert", fakeReads: empty.fakeReads, fakeWrites: empty.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    assert.equal(await app.page.locator(`${DRAWER} .auto-alert-drawer-empty`).innerText(),
      "No tickers yet — turn on MAG7 or add a ticker above.");
  });
});

e2eTest("Add posts the ticker once and shows its chip; the chip's x posts the removal", async () => {
  const service = alertService();
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    const input = app.page.locator(`${DRAWER} input[aria-label="Add a ticker to the automatic OI alerts"]`);
    await input.fill("spy");
    await app.page.locator(`${DRAWER} .auto-alert-drawer-add button[type="submit"]`).click();
    await app.page.locator(`${DRAWER} .auto-alert-drawer-chips span`, { hasText: "SPY" }).waitFor({ timeout: 10_000 });
    assert.deepEqual(posts(app, "/api/oi-auto-alerts/symbols"), [{ add: "SPY" }]);
    assert.equal(await input.inputValue(), "", "the box clears after a successful add");
    await app.page.locator(`${DRAWER} button[aria-label="Remove SPY from auto alerts"]`).click();
    await app.page.waitForFunction((selector) => !document.querySelector(`${selector} .auto-alert-drawer-chips`), DRAWER);
    assert.deepEqual(posts(app, "/api/oi-auto-alerts/symbols"), [{ add: "SPY" }, { remove: "SPY" }]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a malformed ticker shows the inline error and sends nothing", async () => {
  const service = alertService();
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    await app.page.locator(`${DRAWER} input[aria-label="Add a ticker to the automatic OI alerts"]`).fill("1BAD");
    await app.page.locator(`${DRAWER} .auto-alert-drawer-add button[type="submit"]`).click();
    await app.page.locator(`${DRAWER} .auto-alert-drawer-error[role="alert"]`,
      { hasText: "Enter a valid ticker (letters, digits, . or -), e.g. AMD." }).waitFor({ timeout: 5_000 });
    assert.deepEqual(app.writes, []);
  });
});

e2eTest("the Alerts on and MAG7 switches post the flipped setting", async () => {
  const service = alertService();
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    const alertsOn = app.page.locator(`${DRAWER} .auto-alert-drawer-controls label`, { hasText: "Alerts on" }).locator('input[type="checkbox"]');
    await alertsOn.click();
    await app.page.locator(`${DRAWER} .auto-alert-drawer-status`, { hasText: "Paused" }).waitFor({ timeout: 10_000 });
    assert.equal(await alertsOn.isChecked(), false);
    const mag7 = app.page.locator(`${DRAWER} .auto-alert-drawer-controls label`, { hasText: "MAG7" }).locator('input[type="checkbox"]');
    await mag7.click();
    await app.page.waitForFunction((selector) => !document.querySelector(`${selector} .auto-alert-card.is-pending`), DRAWER);
    assert.deepEqual(posts(app, "/api/oi-auto-alerts/settings"), [{ enabled: false }, { includeMag7: false }]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a new confirmation on another page plays the toast and counts on the Auto Alert bell", async () => {
  // First poll: nothing new (history is shown, not replayed). Later polls: one confirm.
  const service = alertService({ regularHours: true, events: (polls) => (polls > 1 ? [confirmEvent("e2e-confirm-1")] : []) });
  await withApp({ view: "Watchlist", fakeReads: { ...service.fakeReads, "GET /api/dashboard": { json: { watchlist: ["NVDA"] } } }, fakeWrites: service.fakeWrites }, async (app) => {
    const toast = app.page.locator(".auto-alert-toast");
    await toast.waitFor({ state: "visible", timeout: 40_000 });
    assert.match(await toast.innerText(), /NVDA CALL OI 150 CONFIRMED/);
    assert.equal(await app.page.locator('[data-testid="nav-auto-alert"] .nav-auto-alert-badge').innerText(), "1");
    await app.page.locator('[data-testid="nav-auto-alert"]').click();
    await app.page.locator('[data-testid="auto-alert-view"]').waitFor({ timeout: 30_000 });
    await app.page.waitForFunction(() => !document.querySelector('[data-testid="nav-auto-alert"] .nav-auto-alert-badge'));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a Latest alerts row opens that ticker on Charts & OI", async () => {
  const service = alertService({ events: [confirmEvent("e2e-latest-1")] });
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    const row = app.page.locator(`${DRAWER} .auto-alert-latest-list button.is-confirm`);
    await row.waitFor({ timeout: 15_000 });
    assert.match(await row.innerText(), /NVDA/);
    await row.click();
    await app.page.locator('[data-testid="charts-and-oi-view"]').waitFor({ timeout: 60_000 });
    await app.page.locator(".charts-oi-heading h2", { hasText: "NVDA" }).waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Re-arm on a card whose CALL alert went posts the re-arm for that ticker", async () => {
  const service = alertService({ includeMag7: false, manual: ["NVDA"], rows: [nvdaRow({ callFired: true })] });
  await withApp({ view: "Auto Alert", fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    const card = app.page.locator(`${DRAWER} .auto-alert-card`, { has: app.page.locator(".auto-alert-symbol", { hasText: "NVDA" }) });
    await card.locator(".auto-alert-card-rearm", { hasText: "CALL alert used today" }).waitFor({ timeout: 15_000 });
    await card.locator(".auto-alert-card-rearm button", { hasText: "Re-arm" }).click();
    await app.page.waitForFunction((selector) => !document.querySelector(`${selector} .auto-alert-card-rearm`), DRAWER);
    assert.deepEqual(posts(app, "/api/oi-auto-alerts/rearm"), [{ symbol: "NVDA" }]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Auto Alert page fits", async () => {
  const service = alertService({ includeMag7: false, manual: ["NVDA"], rows: [nvdaRow()] });
  await withApp({ view: "Auto Alert", viewport: PHONE, fakeReads: service.fakeReads, fakeWrites: service.fakeWrites }, async (app) => {
    await drawerReady(app.page);
    await app.page.locator(`${DRAWER} .auto-alert-card`).first().waitFor({ timeout: 15_000 });
    await sleep(500);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
