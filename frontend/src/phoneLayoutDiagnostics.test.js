import assert from "node:assert/strict";
import { test } from "node:test";

import { isChainCollapsed, isDiagnosticsRequested } from "./phoneLayoutDiagnostics.js";

test("the readout is opt-in and only on an explicit diag=1", () => {
  assert.equal(isDiagnosticsRequested("?diag=1"), true);
  assert.equal(isDiagnosticsRequested("?symbol=AAPL&diag=1"), true);
  assert.equal(isDiagnosticsRequested("?diag=1&symbol=AAPL"), true);
  assert.equal(isDiagnosticsRequested(""), false);
  assert.equal(isDiagnosticsRequested("?diag=0"), false);
  // Must not fire on a lookalike parameter - traders load real query strings.
  assert.equal(isDiagnosticsRequested("?nodiag=1"), false);
  assert.equal(isDiagnosticsRequested("?diagnostics=1"), false);
});

test("the hash form works, because Access strips the query on login", () => {
  assert.equal(isDiagnosticsRequested("", "#diag=1"), true);
  assert.equal(isDiagnosticsRequested("", "#diag=0"), false);
  assert.equal(isDiagnosticsRequested("", "#nodiag=1"), false);
});

function fakeStorage(initial) {
  const map = new Map(initial ? Object.entries(initial) : []);
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    size: () => map.size,
  };
}

test("the flag is remembered, so a login redirect cannot lose it", () => {
  const store = fakeStorage();
  assert.equal(isDiagnosticsRequested("?diag=1", "", store), true);
  // Access bounces back to the bare path with no query at all.
  assert.equal(isDiagnosticsRequested("", "", store), true);
});

test("diag=0 turns it back off and forgets it", () => {
  const store = fakeStorage({ agxPhoneLayoutDiagnostics: "1" });
  assert.equal(isDiagnosticsRequested("?diag=0", "", store), false);
  assert.equal(isDiagnosticsRequested("", "", store), false);
});

test("blocked storage still honours the URL", () => {
  const hostile = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(isDiagnosticsRequested("?diag=1", "", hostile), true);
  assert.equal(isDiagnosticsRequested("", "", hostile), false);
});

test("dead space above the nav is the collapse signal", () => {
  // The reported phone symptom: ~316px of black between chart and nav.
  assert.equal(isChainCollapsed(316), true);
  assert.equal(isChainCollapsed(216), true);
});

test("a healthy chain's small band below the chart is not a collapse", () => {
  // 49px there is the OI level overlay, which is real content.
  assert.equal(isChainCollapsed(49), false);
  assert.equal(isChainCollapsed(5), false);
  assert.equal(isChainCollapsed(0), false);
});

test("an unmeasurable gap is never reported as a collapse", () => {
  assert.equal(isChainCollapsed(null), false);
  assert.equal(isChainCollapsed(undefined), false);
  assert.equal(isChainCollapsed("wide"), false);
});
