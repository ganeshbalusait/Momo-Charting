import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { hasUsableOiFinderChain, oiFinderInitialLoadPlan } from "./oiFinderRequestPolicy.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

// compactResponseReady is declared inside the Finder's mount effect, so it is
// lifted out and run for real rather than asserted on as source text. It is the
// gate that decides whether the enrich pass - the ONLY request carrying
// unusualOtmActivity - ever fires, and whether the poll loop stays pinned to
// the analytics-free compact endpoint.
const liftCompactResponseReady = () => new Function(
  "hasUsableOiFinderChain",
  [
    lift(/const compactResponseReady = \(result\) => Boolean\(\n[\s\S]*?\n {4}\);/, "compactResponseReady"),
    "return compactResponseReady;",
  ].join("\n\n"),
)(hasUsableOiFinderChain);

const compactResponseReady = liftCompactResponseReady();

const chainPayload = (extra = {}) => ({
  callRows: [{ strike: 350, expiry: "2026-08-28" }],
  putRows: [{ strike: 342.5, expiry: "2026-08-28" }],
  currentAtm: { expiry: "2026-08-28" },
  ...extra,
});

test("a usable chain is ready even while the server revalidates it", () => {
  // THE REGRESSION. `refreshing: true` only means the backend is revalidating
  // its 15s cache off-thread - the state of almost every cached response. When
  // this returned false, compactChainReady never flipped, so the enrich pass
  // never ran and the decision board sat on WAIT FOR DATA for the whole time a
  // ticker was open. Recovering needed a manual Refresh Chain, which takes the
  // shouldEnrichOiFinderFeed path where this was already fixed.
  assert.equal(compactResponseReady({ payload: chainPayload({ refreshing: true }) }), true);
});

test("a stale-but-usable cached chain is ready", () => {
  assert.equal(
    compactResponseReady({ payload: chainPayload({ cached: true, stale: true, refreshing: true }) }),
    true,
  );
});

test("a warming stub with no chain is not ready", () => {
  // The cold-start shape the server answers with while it builds. There is
  // nothing to enrich yet, and the poll should stay fast.
  assert.equal(
    compactResponseReady({ payload: { warming: true, refreshing: true, callRows: [], putRows: [] } }),
    false,
  );
});

test("an empty or missing payload is not ready", () => {
  assert.equal(compactResponseReady({ payload: null }), false);
  assert.equal(compactResponseReady(undefined), false);
  assert.equal(compactResponseReady({ payload: { callRows: [], putRows: [], currentAtm: {} } }), false);
});

test("compactResponseReady agrees with the manual-refresh enrich gate", () => {
  // The two paths decide the same thing and must not drift apart again: the
  // automatic mount/poll path used a stricter rule than Refresh Chain, which is
  // exactly why one worked and the other did not.
  const plan = oiFinderInitialLoadPlan("OI Finder");
  assert.equal(plan.enrichFinder, true);
  for (const payload of [
    chainPayload({ refreshing: true }),
    chainPayload(),
    { warming: true, refreshing: true, callRows: [], putRows: [] },
    { callRows: [], putRows: [], currentAtm: {} },
  ]) {
    assert.equal(
      compactResponseReady({ payload }),
      hasUsableOiFinderChain(payload),
      `enrich gates disagree for ${JSON.stringify(payload).slice(0, 60)}`,
    );
  }
});
