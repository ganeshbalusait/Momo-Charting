import assert from "node:assert/strict";
import test from "node:test";

import { applyWorkspaceLinkedNavigationIntent, normalizeChartWorkspace } from "./chartWorkspaceState.js";

// TOS-style colour link from the MomX scanner (2026-09-26).
const panels = [
  { symbol: "AAPL", timeframe: "5m", linkGroup: 1 },
  { symbol: "MSFT", timeframe: "15m", linkGroup: 2 },
  { symbol: "NVDA", timeframe: "1h", linkGroup: 1 },
  { symbol: "TSLA", timeframe: "4h", linkGroup: 3 },
  { symbol: "AMD", timeframe: "D", linkGroup: 1 },     // hidden in a 4-up layout
];
const state = { activePanel: 1, syncSymbols: false, panels };

test("every panel of the colour gets the ticker, each keeps its own timeframe", () => {
  const { state: next, changed, matched } = applyWorkspaceLinkedNavigationIntent(state, { symbol: " aal ", linkGroup: 1 }, { visibleCount: 4 });
  assert.equal(matched, true);
  assert.deepEqual(changed, [0, 2, 4]);                       // hidden panel 4 too
  assert.deepEqual(next.panels.map((p) => `${p.symbol}:${p.timeframe}:${p.linkGroup}`),
    ["AAL:5m:1", "MSFT:15m:2", "AAL:1h:1", "TSLA:4h:3", "AAL:D:1"]);
  assert.equal(next.activePanel, 0);                           // first VISIBLE panel of the colour
  assert.equal(next.syncSymbols, false);
});

test("other colours are untouched", () => {
  const { state: next } = applyWorkspaceLinkedNavigationIntent(state, { symbol: "AAL", linkGroup: 3 }, { visibleCount: 4 });
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAPL", "MSFT", "NVDA", "AAL", "AMD"]);
  assert.equal(next.activePanel, 3);
});

test("no visible chart in that colour: the active chart and its colour peers take it", () => {
  const { state: next, matched } = applyWorkspaceLinkedNavigationIntent(state, { symbol: "AAL", linkGroup: 8 }, { visibleCount: 4 });
  assert.equal(matched, false);
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAPL", "AAL", "NVDA", "TSLA", "AMD"]);
  assert.equal(next.activePanel, 1);
  assert.deepEqual(next.panels.map((p) => p.linkGroup), [1, 2, 1, 3, 1]);   // no colour re-assigned
});

test("colour only on a HIDDEN panel counts as no visible match", () => {
  const onlyHidden = { activePanel: 0, panels: [{ symbol: "A", linkGroup: 2 }, { symbol: "B", linkGroup: 5 }] };
  const { state: next, matched } = applyWorkspaceLinkedNavigationIntent(onlyHidden, { symbol: "AAL", linkGroup: 5 }, { visibleCount: 1 });
  assert.equal(matched, false);
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAL", "B"]);
});

test("synced workspace fallback fills every visible panel", () => {
  const { state: next } = applyWorkspaceLinkedNavigationIntent(state, { symbol: "AAL", linkGroup: 9 }, { visibleCount: 4, syncWholeWorkspace: true });
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAL", "AAL", "AAL", "AAL", "AMD"]);
});

test("MAG7-style one colour per tile replaces exactly that tile", () => {
  const mag7 = { activePanel: 0, panels: ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"].map((symbol, i) => ({ symbol, linkGroup: i + 1 })) };
  const { state: next } = applyWorkspaceLinkedNavigationIntent(mag7, { symbol: "AAL", linkGroup: 6 }, { visibleCount: 7 });
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "AAL", "TSLA"]);
  assert.equal(next.activePanel, 5);
});

test("nothing to change returns the same state object; bad input is a no-op", () => {
  const same = { activePanel: 0, panels: [{ symbol: "AAL", linkGroup: 1 }] };
  assert.equal(applyWorkspaceLinkedNavigationIntent(same, { symbol: "AAL", linkGroup: 1 }).state, same);
  for (const bad of [{ symbol: "", linkGroup: 1 }, { symbol: "AAL", linkGroup: 0 }, { symbol: "AAL", linkGroup: 10 }, { symbol: "AAL" }, null]) {
    assert.equal(applyWorkspaceLinkedNavigationIntent(state, bad).state, state);
  }
});

test("the intent never leaks into the saved workspace shape", () => {
  const { state: next } = applyWorkspaceLinkedNavigationIntent(state, { symbol: "AAL", linkGroup: 1, id: "x", source: "momx" }, { visibleCount: 4 });
  const saved = normalizeChartWorkspace(next, { fallbackLayoutId: "single", fallbackSymbol: "AAPL", maxPanels: 6 });
  for (const panel of saved.panels) {
    assert.deepEqual(Object.keys(panel).filter((k) => !["symbol", "linkGroup", "timeframe", "priceLock"].includes(k)), []);
  }
});

test("with a wide/featured panel, the on-screen first chart of the colour becomes active", () => {
  const wide = { activePanel: 1, panels: [
    { symbol: "A", linkGroup: 1 }, { symbol: "B", linkGroup: 2 }, { symbol: "C", linkGroup: 1 }, { symbol: "D", linkGroup: 3 },
  ] };
  const { state: next } = applyWorkspaceLinkedNavigationIntent(wide, { symbol: "AAL", linkGroup: 1 }, { visibleCount: 4, order: [2, 0, 1, 3] });
  assert.equal(next.activePanel, 2);
  assert.deepEqual(next.panels.map((p) => p.symbol), ["AAL", "B", "AAL", "D"]);
});
