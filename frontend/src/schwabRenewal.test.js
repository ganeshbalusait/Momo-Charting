import test from "node:test";
import assert from "node:assert/strict";

import { renewalBaseline, renewalProgress } from "./schwabRenewal.js";

const before = {
  marketData: { refreshTokenExpiresAt: "2026-11-06T04:08:16Z", tokenSavedAt: "2026-10-30T04:08:16Z" },
  trading: { refreshTokenExpiresAt: "2026-11-06T04:09:15Z", tokenSavedAt: "2026-10-30T04:09:15Z" },
};
const renewal = {
  profiles: ["market_data", "trading"],
  baseline: renewalBaseline(before, ["market_data", "trading"]),
  startedAt: "2026-11-04T22:00:00Z",
};

test("walks step 1, step 2 and done as each 7-day expiry moves", () => {
  assert.match(renewalProgress(renewal, before).text, /^Step 1 of 2/);
  const marketDone = { ...before, marketData: { refreshTokenExpiresAt: "2026-11-11T22:01:00Z" } };
  const half = renewalProgress(renewal, marketDone);
  assert.equal(half.finished, false);
  assert.match(half.text, /Market Data renewed\. Step 2 of 2: approve Accounts & Trading/);
  const all = renewalProgress(renewal, { ...marketDone, trading: { refreshTokenExpiresAt: "2026-11-11T22:02:00Z" } });
  assert.deepEqual([all.finished, all.failed], [true, false]);
  assert.match(all.text, /Both Schwab logins renewed/);
});

test("an access-token refresh alone is not a renewal", () => {
  const refreshed = { ...before, marketData: { ...before.marketData, accessTokenExpiresAt: "2026-11-04T23:00:00Z" } };
  assert.match(renewalProgress(renewal, refreshed).text, /^Step 1 of 2/);
});

test("a failed login from this attempt is reported; one from an older attempt is not", () => {
  const failedNow = { ...before, marketData: { ...before.marketData, renewalResult: { ok: false, at: "2026-11-04T22:01:00+00:00", error: "access_denied" } } };
  const now = renewalProgress(renewal, failedNow);
  assert.deepEqual([now.finished, now.failed], [true, true]);
  assert.equal(now.text, "The Schwab Market Data login did not finish: access_denied. Click Renew both to try again.");
  const old = { ...before, marketData: { ...before.marketData, renewalResult: { ok: false, at: "2026-11-03T10:00:00+00:00", error: "x" } } };
  assert.equal(renewalProgress(renewal, old).failed, false);
});

test("the callback's own success counts before the expiry stamp is re-read", () => {
  const fresh = { marketData: { refreshTokenExpiresAt: before.marketData.refreshTokenExpiresAt,
    renewalResult: { ok: true, at: "2026-11-04T22:01:00+00:00" } }, trading: before.trading };
  assert.match(renewalProgress(renewal, fresh).text, /Market Data renewed\. Step 2 of 2/);
});

test("only Market Data configured: a single step", () => {
  const one = { profiles: ["market_data"], baseline: renewalBaseline(before, ["market_data"]), startedAt: renewal.startedAt };
  assert.match(renewalProgress(one, before).text, /^Log in to Schwab in the new window and approve Market Data/);
  const failed = { marketData: { ...before.marketData, renewalResult: { ok: false, at: "2026-11-04T22:01:00Z", error: "invalid_grant." } } };
  assert.match(renewalProgress(one, failed).text, /invalid_grant\. Click Renew Market Data to try again\.$/);
  assert.match(renewalProgress(one, { marketData: { refreshTokenExpiresAt: "2026-11-11T22:01:00Z" } }).text, /Schwab Market Data renewed/);
});
