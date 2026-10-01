// "PREMARKET FEED DOWN" was shown for a window that had data in it.
//
// Measured 2026-08-28 09:40 ET on AAPL: the server's own note read "Premarket
// 04:00-07:00 is running on the Alpaca SIP backup (about 15 minutes behind)",
// and the chart carried candles across that window - while the badge above it
// said the feed was DOWN. The backend distinguishes "on backup" from "nothing
// at all"; the badge flattened both into the same alarm.
//
// Which way it errs matters in opposite directions: calling a working backup
// "down" teaches him to ignore the badge, and calling a real hole "backup"
// lets him trade off candles that are not there.
import test from "node:test";
import assert from "node:assert/strict";

import { premarketGapBadge } from "./premarketGap.js";

test("no gap at all shows nothing", () => {
  assert.equal(premarketGapBadge("", ""), null);
  assert.equal(premarketGapBadge(undefined, undefined), null);
});

test("running on the backup says BACKUP, not DOWN", () => {
  const badge = premarketGapBadge("backup", "on the Alpaca SIP backup, ~15 min behind");
  assert.match(badge.text, /BACKUP/);
  assert.doesNotMatch(badge.text, /DOWN/, "there ARE candles in this window");
  assert.equal(badge.tone, "warn");
});

test("a genuinely empty window still says DOWN", () => {
  const badge = premarketGapBadge("missing", "Tradier refused and the backup returned nothing");
  assert.match(badge.text, /DOWN/);
  assert.equal(badge.tone, "down");
});

test("both keep the window in the label, because that is the actionable part", () => {
  for (const state of ["backup", "missing"]) {
    assert.match(premarketGapBadge(state, "x").text, /04:00/);
  }
});

test("the server's own sentence is carried through as the tooltip", () => {
  // It names the cause and the fix ("renew its access token in Settings").
  // Rewording it here would mean two places to keep true.
  const note = "Tradier, the primary, refused the request - renew its access token in Settings.";
  assert.equal(premarketGapBadge("backup", note).title, note);
});

test("an older server that sends a note but no state still warns", () => {
  // Deploys are not atomic. Unknown state with a note present must not go
  // silent - falling back to the loud label keeps today's behaviour rather
  // than inventing reassurance we have not verified.
  const badge = premarketGapBadge("", "something is wrong with premarket");
  assert.match(badge.text, /DOWN/);
  assert.equal(badge.tone, "down");
});

test("an unrecognised state with no note stays silent", () => {
  assert.equal(premarketGapBadge("wobble", ""), null);
});
