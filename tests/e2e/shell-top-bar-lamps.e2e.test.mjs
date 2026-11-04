// The header's TOS MARKET / TOS TRADING / ALPACA lamps and login countdown,
// end to end (coverage 2026-11-04, group "shell"). The Schwab status (and the
// signed-in user where it matters) is a fixture, so each lamp state is
// deterministic. Opened on Release Notes, a light page: the header is the same
// on every page.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const STATUS = "GET /api/schwab/status";
const AUTH = "GET /api/auth/status";
const LAMPS = ".topbar.trading-header .market-strip-right .connection-state";
const HOUR = 3600 * 1000;

const profile = (over = {}) => ({
  configured: true, credentialsConfigured: true, hasRefreshToken: true, accepted: true, healthy: true,
  refreshTokenExpiresAt: new Date(Date.now() + 5.5 * 24 * HOUR).toISOString(), ...over,
});
const schwab = (marketData = {}, trading = {}, top = {}) => () => ({
  json: { marketData: profile(marketData), trading: profile(trading), alpacaBarStream: { connected: true, source: "e2e", subscribedSymbols: 3 }, ...top },
});
const asUser = (over = {}) => ({
  json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
    user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false,
      isActive: true, hasPassword: true, mustChangePassword: false, ...over } },
});

function lamp(page, label) {
  return page.locator(LAMPS).filter({ hasText: label });
}

async function header(page) {
  await page.locator(LAMPS).first().waitFor({ state: "visible", timeout: 60_000 });
}

e2eTest("a healthy Schwab key lights TOS MARKET green", async () => {
  await withApp({ view: "Release Notes", fakeReads: { [STATUS]: schwab() } }, async (app) => {
    await header(app.page);
    const market = lamp(app.page, "TOS MARKET");
    await app.page.locator(`${LAMPS}.is-live`, { hasText: "TOS MARKET" }).waitFor({ timeout: 15_000 });
    assert.match(await market.getAttribute("title"), /^Your Schwab Market Data key: charts, option chains, OI\. Verified working\./);
    assert.equal((await market.locator(".login-countdown").innerText()).trim(), "5d");
    assert.doesNotMatch(await market.getAttribute("class"), /is-expiring/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("under two days left the countdown turns amber and reads 1d 6h", async () => {
  const soon = () => new Date(Date.now() + 30.5 * HOUR).toISOString();
  const status = () => schwab({ refreshTokenExpiresAt: soon() })();
  await withApp({ view: "Release Notes", fakeReads: { [STATUS]: status } }, async (app) => {
    await header(app.page);
    const market = lamp(app.page, "TOS MARKET");
    await app.page.locator(`${LAMPS}.is-expiring`, { hasText: "TOS MARKET" }).waitFor({ timeout: 15_000 });
    assert.equal((await market.locator(".login-countdown").innerText()).trim(), "1d 6h");
  });
});

e2eTest("an expired login reads EXPIRED", async () => {
  await withApp({ view: "Release Notes", fakeReads: { [STATUS]: schwab({}, { refreshTokenExpiresAt: "2026-01-01T00:00:00Z", healthy: false }) } }, async (app) => {
    await header(app.page);
    const trading = lamp(app.page, "TOS TRADING");
    await app.page.locator(`${LAMPS} .login-countdown`, { hasText: "EXPIRED" }).waitFor({ timeout: 15_000 });
    assert.match(await trading.getAttribute("class"), /is-down/);
    assert.match(await trading.getAttribute("class"), /is-expiring/);
  });
});

e2eTest("a key Schwab refuses is red and its tooltip says so", async () => {
  await withApp({
    view: "Release Notes",
    fakeReads: { [STATUS]: schwab({ healthy: false, accepted: false, acceptedError: "invalid_client" }) },
  }, async (app) => {
    await header(app.page);
    await app.page.locator(`${LAMPS}.is-down`, { hasText: "TOS MARKET" }).waitFor({ timeout: 15_000 });
    assert.equal(await lamp(app.page, "TOS MARKET").getAttribute("title"),
      "Schwab is REFUSING this key: invalid_client. Re-enter the app key and secret in Settings.");
  });
});

e2eTest("ACCT KEY and the shared ALPACA stream lamp are admin-only", async () => {
  await withApp({ view: "Release Notes", fakeReads: { [STATUS]: schwab(), [AUTH]: asUser() } }, async (app) => {
    await header(app.page);
    assert.equal(await lamp(app.page, "ACCT KEY").count(), 0);
    const alpaca = lamp(app.page, "ALPACA");
    assert.equal(await alpaca.count(), 1);
    assert.doesNotMatch(await alpaca.getAttribute("title"), /failover feed/, "a member sees their own key, not the house stream");
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ view: "Release Notes", fakeReads: { [STATUS]: schwab() } }, async (app) => {
    await header(app.page);
    assert.match(await lamp(app.page, "ALPACA").getAttribute("title"), /^Alpaca key: failover feed \(e2e · 3 tickers\)\. Connected\./);
  });
});

test("a member's own saved Alpaca key lights ALPACA on every page, not only after visiting Settings",
  { skip: marketHoursBlock() || false, todo: "BUG: apiKeySummary loads only on Settings, so a member with a saved Alpaca key sees ALPACA red on Charts/Release Notes", timeout: 180_000 },
  async () => {
    const keys = { json: { alpaca: { configured: true, keyIdMasked: "PKE2...ABCDEF" }, tradier: { configured: false, tokenMasked: "" } } };
    await withApp({ view: "Release Notes", fakeReads: { [STATUS]: schwab(), [AUTH]: asUser(), "GET /api/user/api-keys": keys } }, async (app) => {
      await header(app.page);
      await app.page.locator(`${LAMPS}.is-live`, { hasText: "ALPACA" }).waitFor({ timeout: 15_000 });
    });
  });

e2eTest("the header strip never pushes a phone sideways", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE, fakeReads: { [STATUS]: schwab() } }, async (app) => {
    await app.page.locator(".topbar.trading-header .market-strip").waitFor({ state: "attached", timeout: 60_000 });
    await app.page.locator(".release-notes").waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
