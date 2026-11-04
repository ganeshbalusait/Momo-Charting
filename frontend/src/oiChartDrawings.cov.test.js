// Unit (2026-11-04 coverage pass, group charts-studies, feature charts-drawings):
// the per-ticker/per-timeframe drawing store (oiFinderChartDrawingsV1). Drawings live
// in this browser only, so a full or blocked phone storage must cost the save, never
// the chart (a throw inside commitChartDrawings would take the chart panel down).
import test, { afterEach, beforeEach } from "node:test";
import assert from "node:assert/strict";

import {
  OI_CHART_DRAWINGS_STORAGE_KEY,
  chartDrawingScopeKey,
  loadChartDrawings,
  saveChartDrawings,
} from "./oiChartDrawings.js";

class MemoryStorage {
  constructor({ quota = Infinity, blocked = false } = {}) {
    this.items = new Map();
    this.quota = quota;
    this.blocked = blocked;
  }

  getItem(name) {
    if (this.blocked) throw new Error("SecurityError: storage is blocked");
    return this.items.has(name) ? this.items.get(name) : null;
  }

  setItem(name, value) {
    if (this.blocked) throw new Error("SecurityError: storage is blocked");
    if (String(value).length > this.quota) {
      const error = new Error("The quota has been exceeded.");
      error.name = "QuotaExceededError";
      throw error;
    }
    this.items.set(name, String(value));
  }
}

const trend = (id, price = 100) => ({
  id, type: "trend", color: "#ff0000", locked: false, createdAt: 1,
  points: [{ time: 1_700_000_000, price }, { time: 1_700_000_300, price: price + 2 }],
});

let previousWindow;
beforeEach(() => {
  previousWindow = globalThis.window;
});
afterEach(() => {
  if (previousWindow === undefined) delete globalThis.window;
  else globalThis.window = previousWindow;
});

test("a save round-trips per ticker and timeframe and leaves the other scopes alone", () => {
  globalThis.window = { localStorage: new MemoryStorage() };
  const aapl5 = chartDrawingScopeKey("aapl", "5m");
  const aapl15 = chartDrawingScopeKey("AAPL", "15m");
  const nvda5 = chartDrawingScopeKey("NVDA", "5m");
  assert.equal(saveChartDrawings(aapl5, [trend("a")]), true);
  assert.equal(saveChartDrawings(nvda5, [trend("n", 180)]), true);
  assert.deepEqual(loadChartDrawings(aapl5).map(({ id }) => id), ["a"]);
  assert.deepEqual(loadChartDrawings(aapl15), [], "a 5m drawing must not show on the 15m chart");
  assert.deepEqual(loadChartDrawings(nvda5).map(({ id }) => id), ["n"]);
  assert.equal(saveChartDrawings(aapl5, []), true);
  assert.deepEqual(loadChartDrawings(aapl5), []);
  assert.deepEqual(loadChartDrawings(nvda5).map(({ id }) => id), ["n"], "clearing AAPL kept NVDA's drawings");
});

test("a full storage reports a failed save instead of throwing, and keeps what was saved", () => {
  const storage = new MemoryStorage();
  globalThis.window = { localStorage: storage };
  const scope = chartDrawingScopeKey("SPY", "5m");
  assert.equal(saveChartDrawings(scope, [trend("kept")]), true);
  storage.quota = storage.getItem(OI_CHART_DRAWINGS_STORAGE_KEY).length; // nothing bigger fits
  let result;
  assert.doesNotThrow(() => { result = saveChartDrawings(scope, [trend("kept"), trend("new")]); });
  assert.equal(result, false);
  assert.deepEqual(loadChartDrawings(scope).map(({ id }) => id), ["kept"]);
});

test("blocked storage (private mode) reads as no drawings and saves report failure", () => {
  globalThis.window = { localStorage: new MemoryStorage({ blocked: true }) };
  const scope = chartDrawingScopeKey("AAPL", "5m");
  assert.deepEqual(loadChartDrawings(scope), []);
  assert.equal(saveChartDrawings(scope, [trend("x")]), false);
});

test("a corrupt or non-object store reads as empty; a non-object one is replaced on save", () => {
  const storage = new MemoryStorage();
  storage.setItem(OI_CHART_DRAWINGS_STORAGE_KEY, "{not json");
  globalThis.window = { localStorage: storage };
  const scope = chartDrawingScopeKey("AAPL", "5m");
  assert.deepEqual(loadChartDrawings(scope), []);
  storage.setItem(OI_CHART_DRAWINGS_STORAGE_KEY, JSON.stringify([1, 2]));
  assert.equal(saveChartDrawings(scope, [trend("fresh")]), true, "a non-object store is replaced");
  assert.deepEqual(loadChartDrawings(scope).map(({ id }) => id), ["fresh"]);
});

// KNOWN BUG (found 2026-11-04): saveChartDrawings JSON.parses the existing store
// inside its try, so once the stored value is unreadable (a truncated write, a
// browser crash mid-save) EVERY later save returns false: the trader keeps
// drawing, the lines show, and all of them are gone on the next reload - on every
// ticker - until site data is cleared. loadChartDrawings already treats a corrupt
// store as empty; the save should start a fresh store the same way.
test("a save after a corrupt store starts a clean store instead of failing forever", {
  todo: "saveChartDrawings fails every save while the stored JSON is corrupt (drawings silently lost on reload)",
}, () => {
  const storage = new MemoryStorage();
  storage.setItem(OI_CHART_DRAWINGS_STORAGE_KEY, "{not json");
  globalThis.window = { localStorage: storage };
  const scope = chartDrawingScopeKey("AAPL", "5m");
  assert.equal(saveChartDrawings(scope, [trend("fresh")]), true);
  assert.deepEqual(loadChartDrawings(scope).map(({ id }) => id), ["fresh"]);
});

test("malformed drawings in the store are dropped on load, valid ones kept", () => {
  const storage = new MemoryStorage();
  const scope = chartDrawingScopeKey("NVDA", "1h");
  storage.setItem(OI_CHART_DRAWINGS_STORAGE_KEY, JSON.stringify({
    [scope]: [trend("good"), { id: "bad", type: "trend", points: [{ time: "x", price: 1 }] }, { id: "odd", type: "ellipse", points: [] }],
  }));
  globalThis.window = { localStorage: storage };
  assert.deepEqual(loadChartDrawings(scope).map(({ id }) => id), ["good"]);
});
