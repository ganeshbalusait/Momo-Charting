// A cached option chain must never wear the green LIVE lamp.
//
// `live` is written by api_server.py:4864 as bool(call_rows or put_rows) - it
// means "this build parsed at least one row", nothing about freshness. The disk
// cache will replay a payload up to five days old and keep that live:true.
// Measured 2026-08-27: an AVGO chain 2 days 21 hours old, from a provider that
// was 401-refused at the time, rendered "FEED LIVE" over a caption of
// "11:56:00 PM ET" - time only, so Monday night read as tonight.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

const isChainFeedFresh = new Function([
  lift(/function isChainFeedFresh\([\s\S]*?\n}/, "isChainFeedFresh"),
  "return isChainFeedFresh;",
].join("\n\n"))();

test("a genuinely live chain is fresh", () => {
  assert.equal(isChainFeedFresh({ live: true, stale: false }), true);
});

test("THE REGRESSION: a stale disk chain is not fresh, however many rows it has", () => {
  // The exact measured shape: live true, stale true, served from disk.
  assert.equal(
    isChainFeedFresh({ live: true, stale: true, cached: true, diskCached: true }),
    false,
  );
});

test("a chain with no rows is not fresh", () => {
  assert.equal(isChainFeedFresh({ live: false, stale: false }), false);
});

test("missing or absent payloads never claim freshness", () => {
  assert.equal(isChainFeedFresh(null), false);
  assert.equal(isChainFeedFresh(undefined), false);
  assert.equal(isChainFeedFresh({}), false);
});

test("freshness is never inferred from row count alone", () => {
  // Guards the specific wrong fix: reading callRows/putRows instead of stale.
  assert.equal(isChainFeedFresh({ live: true, stale: true, callRows: 900 }), false);
});
