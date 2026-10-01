import assert from "node:assert/strict";
import test from "node:test";

import {
  OI_CHART_WORKSPACE_STORAGE_KEY,
  applyWorkspaceNavigationIntent,
  deleteChartGrid,
  listChartGrids,
  loadChartGrid,
  loadChartWorkspace,
  normalizeChartWorkspace,
  readLegacyGeometryNumber,
  saveChartGridAs,
  mergeChartGridStores,
  pendingDefaultChartGrid,
  markChartGridApplied,
  readChartGridMeta,
  readChartGridStoreFrom,
  chartGridStamp,
  OI_CHART_GRIDS_STORAGE_KEY,
  saveChartWorkspace,
  updateSharedWorkspaceSymbol,
  updateWorkspacePanelTimeframe,
} from "./chartWorkspaceState.js";

const layoutIds = [
  "single",
  "two-columns",
  "two-rows",
  "three-columns",
  "three-grid",
  "quad",
  "four-columns",
];
const timeframes = ["3m", "5m", "15m", "1h", "4h", "D", "W"];
const options = {
  fallbackLayoutId: "single",
  fallbackSymbol: "AAPL",
  maxPanels: 6,
  defaultTimeframes: ["5m", "15m", "1h", "4h", "D", "W"],
  validLayoutIds: layoutIds,
  validTimeframes: timeframes,
};

function memoryStorage(initialValue = null) {
  const values = new Map();
  if (initialValue !== null) values.set(OI_CHART_WORKSPACE_STORAGE_KEY, initialValue);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  };
}

test("normalizes malformed workspaces and clamps panel fields", () => {
  const malformed = normalizeChartWorkspace({
    version: 99,
    layoutId: "not-a-layout",
    isMaximized: "yes",
    companionVisible: "no",
    syncSymbols: null,
    activePanel: 200,
    widePanel: -20,
    panels: [
      { symbol: " a$a pl? ", linkGroup: 50, timeframe: "not-a-timeframe" },
      null,
      { symbol: "", linkGroup: -8, timeframe: "4H" },
    ],
    geometry: [],
  }, options);

  assert.equal(malformed.version, 2);
  assert.equal(malformed.layoutId, "single");
  assert.equal(malformed.isMaximized, false);
  assert.equal(malformed.companionVisible, true);
  assert.equal(malformed.syncSymbols, true);
  assert.equal(malformed.activePanel, 5);
  assert.equal(malformed.widePanel, 0);
  assert.equal(malformed.panels.length, 6);
  assert.deepEqual(malformed.panels[0], { symbol: "AAPL", linkGroup: 9, timeframe: "5m", priceLock: false });
  assert.deepEqual(malformed.panels[1], { symbol: "AAPL", linkGroup: 2, timeframe: "15m", priceLock: false });
  assert.deepEqual(malformed.panels[2], { symbol: "AAPL", linkGroup: 1, timeframe: "4h", priceLock: false });
  assert.deepEqual(malformed.geometry, {});

  for (const candidate of [null, [], "bad", 42]) {
    assert.doesNotThrow(() => normalizeChartWorkspace(candidate, options));
    assert.equal(normalizeChartWorkspace(candidate, options).panels.length, 6);
  }
});

test("falls back safely for malformed or unavailable storage", () => {
  for (const raw of ["{", "[]", "null", "42", "\"bad\""]) {
    const loaded = loadChartWorkspace(memoryStorage(raw), options);
    assert.equal(loaded.version, 2);
    assert.equal(loaded.layoutId, "single");
    assert.equal(loaded.panels.length, 6);
  }

  const throwingReader = { getItem: () => { throw new Error("storage disabled"); } };
  assert.doesNotThrow(() => loadChartWorkspace(throwingReader, options));
  assert.equal(loadChartWorkspace(throwingReader, options).panels[0].timeframe, "5m");

  const throwingWriter = { setItem: () => { throw new Error("quota exceeded"); } };
  assert.equal(saveChartWorkspace(throwingWriter, normalizeChartWorkspace(null, options)), false);
  assert.equal(saveChartWorkspace(null, normalizeChartWorkspace(null, options)), false);
});

test("preserves each exact one, two, three, and four-chart layout id", () => {
  for (const layoutId of layoutIds) {
    const normalized = normalizeChartWorkspace({ layoutId }, options);
    assert.equal(normalized.layoutId, layoutId);
  }
});

test("keeps hidden panel timeframes when a smaller layout is active", () => {
  const workspace = normalizeChartWorkspace({
    layoutId: "single",
    panels: [
      { timeframe: "5m" },
      { timeframe: "15m" },
      { timeframe: "1h" },
      { timeframe: "4h" },
      { timeframe: "D" },
      { timeframe: "W" },
    ],
  }, options);

  assert.deepEqual(workspace.panels.map(({ timeframe }) => timeframe), ["5m", "15m", "1h", "4h", "D", "W"]);
  assert.equal(normalizeChartWorkspace({ ...workspace, layoutId: "quad" }, options).panels[3].timeframe, "4h");
});

test("updates the shared visible symbol without changing any timeframe", () => {
  const workspace = normalizeChartWorkspace({
    layoutId: "quad",
    panels: [
      { symbol: "AAPL", timeframe: "5m" },
      { symbol: "AAPL", timeframe: "15m" },
      { symbol: "AAPL", timeframe: "1h" },
      { symbol: "AAPL", timeframe: "4h" },
      { symbol: "NVDA", timeframe: "D" },
      { symbol: "TSLA", timeframe: "W" },
    ],
  }, options);
  const beforeTimeframes = workspace.panels.map(({ timeframe }) => timeframe);
  const updated = updateSharedWorkspaceSymbol(workspace, " ms$ft ", 4);

  assert.deepEqual(updated.panels.slice(0, 4).map(({ symbol }) => symbol), ["MSFT", "MSFT", "MSFT", "MSFT"]);
  assert.deepEqual(updated.panels.slice(4).map(({ symbol }) => symbol), ["NVDA", "TSLA"]);
  assert.deepEqual(updated.panels.map(({ timeframe }) => timeframe), beforeTimeframes);
  assert.deepEqual(workspace.panels.slice(0, 4).map(({ symbol }) => symbol), ["AAPL", "AAPL", "AAPL", "AAPL"]);
});

test("updates only the active symbol when symbol synchronization is disabled", () => {
  const workspace = normalizeChartWorkspace({
    syncSymbols: false,
    activePanel: 2,
    panels: Array.from({ length: 6 }, () => ({ symbol: "AAPL" })),
  }, options);
  const updated = updateSharedWorkspaceSymbol(workspace, "NVDA", 4);

  assert.deepEqual(updated.panels.map(({ symbol }) => symbol), ["AAPL", "AAPL", "NVDA", "AAPL", "AAPL", "AAPL"]);
});

test("updates one panel timeframe using the canonical valid key", () => {
  const workspace = normalizeChartWorkspace(null, options);
  const updated = updateWorkspacePanelTimeframe(workspace, 3, "4H", timeframes);

  assert.equal(updated.panels[3].timeframe, "4h");
  assert.equal(updated.panels[0].timeframe, "5m");
  assert.equal(updateWorkspacePanelTimeframe(updated, 3, "invalid", timeframes), updated);
  assert.equal(updateWorkspacePanelTimeframe(updated, 99, "D", timeframes), updated);
});

test("explicit navigation overrides only the saved active panel symbol and timeframe", () => {
  const workspace = normalizeChartWorkspace({
    layoutId: "quad",
    syncSymbols: true,
    activePanel: 2,
    panels: [
      { symbol: "AAPL", timeframe: "5m" },
      { symbol: "MSFT", timeframe: "15m" },
      { symbol: "NVDA", timeframe: "1h" },
      { symbol: "TSLA", timeframe: "D" },
    ],
  }, options);
  const updated = applyWorkspaceNavigationIntent(workspace, {
    symbol: " am$zn ",
    timeframe: "4H",
  }, options);

  assert.deepEqual(updated.panels.slice(0, 4).map(({ symbol, timeframe }) => (
    `${symbol}:${timeframe}`
  )), ["AAPL:5m", "MSFT:15m", "AMZN:4h", "TSLA:D"]);
  assert.equal(updated.layoutId, "quad");
  assert.equal(updated.syncSymbols, true);
  assert.equal(applyWorkspaceNavigationIntent(updated, { symbol: "", timeframe: "4h" }, options), updated);
  assert.equal(applyWorkspaceNavigationIntent(updated, { symbol: "AMZN", timeframe: "bad" }, options), updated);
});

test("round-trips the complete normalized workspace", () => {
  const storage = memoryStorage();
  const workspace = normalizeChartWorkspace({
    layoutId: "four-columns",
    isMaximized: true,
    companionVisible: false,
    syncSymbols: false,
    activePanel: 3,
    widePanel: 2,
    panels: [
      { symbol: "AAPL", linkGroup: 1, timeframe: "5m" },
      { symbol: "MSFT", linkGroup: 2, timeframe: "15m" },
      { symbol: "NVDA", linkGroup: 3, timeframe: "1h" },
      { symbol: "TSLA", linkGroup: 4, timeframe: "4h" },
      { symbol: "META", linkGroup: 5, timeframe: "D" },
      { symbol: "AMZN", linkGroup: 6, timeframe: "W" },
    ],
    geometry: {
      twoPanelSplit: 62,
      chartHeight: 720,
      companionWidth: 480,
      columnWidthProfiles: { "four-columns": [20, 30, 25, 25] },
    },
  }, options);

  assert.equal(saveChartWorkspace(storage, workspace), true);
  assert.deepEqual(loadChartWorkspace(storage, options), workspace);
});

test("legacy geometry numbers fall back instead of clamping a missing key to the minimum", () => {
  const storage = memoryStorage();

  // Number(null) === 0 is finite, so a naive isFinite() check silently clamps an
  // absent key up to the minimum. That seeded twoPanelSplit=25 on first visit and
  // rendered the two-chart layout 25/75 instead of 50/50.
  assert.equal(readLegacyGeometryNumber(storage, "oiFinderTwoPanelSplit", 25, 75, 50), 50);
  assert.equal(readLegacyGeometryNumber(storage, "oiFinderWidePanel", 0, 9, null), null);
  assert.equal(readLegacyGeometryNumber(storage, "oiFinderWorkspaceChartHeight", 240, 1200, null), null);
  assert.equal(readLegacyGeometryNumber(storage, "oiFinderBigScreenCompanionWidth", 340, 1200, null), null);

  storage.setItem("blank", "   ");
  storage.setItem("junk", "not-a-number");
  assert.equal(readLegacyGeometryNumber(storage, "blank", 25, 75, 50), 50);
  assert.equal(readLegacyGeometryNumber(storage, "junk", 25, 75, 50), 50);

  // Real stored values still clamp into range.
  storage.setItem("oiFinderTwoPanelSplit", "62");
  storage.setItem("low", "5");
  storage.setItem("high", "900");
  storage.setItem("edge", "0");
  assert.equal(readLegacyGeometryNumber(storage, "oiFinderTwoPanelSplit", 25, 75, 50), 62);
  assert.equal(readLegacyGeometryNumber(storage, "low", 25, 75, 50), 25);
  assert.equal(readLegacyGeometryNumber(storage, "high", 25, 75, 50), 75);
  assert.equal(readLegacyGeometryNumber(storage, "edge", 0, 9, null), 0);
  assert.equal(readLegacyGeometryNumber(null, "oiFinderTwoPanelSplit", 25, 75, 50), 50);
});

test("heals v1 workspaces seeded with the clamp-minimum geometry", () => {
  const poisoned = loadChartWorkspace(memoryStorage(JSON.stringify({
    version: 1,
    layoutId: "two-columns",
    widePanel: 0,
    geometry: {
      twoPanelSplit: 25,
      chartHeight: 240,
      companionWidth: 340,
      columnWidthProfiles: {},
    },
  })), options);

  assert.equal(poisoned.geometry.twoPanelSplit, 50);
  assert.equal(poisoned.geometry.chartHeight, undefined);
  assert.equal(poisoned.geometry.companionWidth, undefined);
  assert.equal(poisoned.widePanel, null);
  // twoPanelSplit must be an explicit 50, never null: Number(null) is 0, which
  // App.jsx's isFinite() restore would accept as a real split.
  assert.equal(Object.hasOwn(poisoned.geometry, "twoPanelSplit"), true);
});

test("keeps deliberately customized v1 geometry and never re-migrates", () => {
  const customized = loadChartWorkspace(memoryStorage(JSON.stringify({
    version: 1,
    layoutId: "two-columns",
    widePanel: 1,
    geometry: {
      twoPanelSplit: 62,
      chartHeight: 720,
      companionWidth: 480,
      columnWidthProfiles: { "four-columns": [20, 30, 25, 25] },
    },
  })), options);

  assert.equal(customized.geometry.twoPanelSplit, 62);
  assert.equal(customized.geometry.chartHeight, 720);
  assert.equal(customized.geometry.companionWidth, 480);
  assert.equal(customized.widePanel, 1);
  assert.deepEqual(customized.geometry.columnWidthProfiles, { "four-columns": [20, 30, 25, 25] });

  // Already-migrated workspaces keep a legitimate 25 split and a wide panel 0.
  const alreadyMigrated = loadChartWorkspace(memoryStorage(JSON.stringify({
    version: 2,
    layoutId: "two-columns",
    widePanel: 0,
    geometry: { twoPanelSplit: 25, chartHeight: 240, companionWidth: 340 },
  })), options);

  assert.equal(alreadyMigrated.geometry.twoPanelSplit, 25);
  assert.equal(alreadyMigrated.geometry.chartHeight, 240);
  assert.equal(alreadyMigrated.geometry.companionWidth, 340);
  assert.equal(alreadyMigrated.widePanel, 0);
});

test("keeps detached workspaces in a separate storage slot", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  };
  const detachedKey = `${OI_CHART_WORKSPACE_STORAGE_KEY}:mag7`;
  const workspace = normalizeChartWorkspace({ layoutId: "mag7" }, {
    ...options,
    validLayoutIds: [...layoutIds, "mag7"],
  });

  assert.equal(saveChartWorkspace(storage, workspace, detachedKey), true);
  assert.equal(loadChartWorkspace(storage, {
    ...options,
    storageKey: detachedKey,
    validLayoutIds: [...layoutIds, "mag7"],
  }).layoutId, "mag7");
  assert.equal(storage.getItem(OI_CHART_WORKSPACE_STORAGE_KEY), null);
});

test("saves, lists, loads, and deletes named grids", () => {
  const storage = memoryStorage();
  const workspace = normalizeChartWorkspace({
    layoutId: "quad",
    panels: [{ symbol: "NVDA", timeframe: "15m", linkGroup: 3 }],
  }, options);
  const indicators = { ema9: true, clouds: false };

  assert.ok(saveChartGridAs(storage, "  mag7  ", { workspace, indicators }));
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["mag7"]);

  const loaded = loadChartGrid(storage, "mag7", options);
  assert.equal(loaded.workspace.layoutId, "quad");
  assert.equal(loaded.workspace.panels[0].symbol, "NVDA");
  assert.deepEqual(loaded.indicators, indicators);

  assert.ok(saveChartGridAs(storage, "main", { workspace, indicators: null }));
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["mag7", "main"]);

  assert.equal(deleteChartGrid(storage, "mag7"), true);
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["main"]);
  assert.equal(loadChartGrid(storage, "mag7", options), null);
});

test("same-name grid saves overwrite instead of duplicating", () => {
  const storage = memoryStorage();
  const first = normalizeChartWorkspace({ layoutId: "single" }, options);
  const second = normalizeChartWorkspace({ layoutId: "quad" }, options);

  assert.ok(saveChartGridAs(storage, "main", { workspace: first, indicators: null }));
  assert.ok(saveChartGridAs(storage, "main", { workspace: second, indicators: null }));
  assert.equal(listChartGrids(storage).length, 1);
  assert.equal(loadChartGrid(storage, "main", options).workspace.layoutId, "quad");
});

test("rejects unusable grid saves and loads", () => {
  const storage = memoryStorage();
  const workspace = normalizeChartWorkspace(null, options);

  assert.equal(saveChartGridAs(storage, "   ", { workspace, indicators: null }), false);
  assert.equal(saveChartGridAs(storage, "x", null), false);
  assert.equal(loadChartGrid(storage, "missing", options), null);
  assert.equal(deleteChartGrid(storage, "missing"), false);

  storage.setItem("oiFinderChartGrids", "not json {");
  assert.equal(loadChartGrid(storage, "any", options), null);
  assert.deepEqual(listChartGrids(storage), []);
  assert.ok(saveChartGridAs(storage, "fresh", { workspace, indicators: null }));
  assert.equal(loadChartGrid(storage, "fresh", options).workspace.layoutId, "single");
});

test("normalizeChartWorkspace carries per-panel priceLock with a false default", () => {
  const normalized = normalizeChartWorkspace({
    panels: [
      { symbol: "TSLA", priceLock: true },
      { symbol: "AAPL", priceLock: "yes" },
      { symbol: "MSFT" },
    ],
  });
  assert.equal(normalized.panels[0].priceLock, true);
  assert.equal(normalized.panels[1].priceLock, false);
  assert.equal(normalized.panels[2].priceLock, false);
});

test("a saved grid becomes the default and stamps itself so the saving device does not re-apply it", () => {
  const storage = memoryStorage();
  const workspace = normalizeChartWorkspace({ layoutId: "quad", panels: [{ symbol: "NVDA" }] }, options);
  const stamp = saveChartGridAs(storage, "mag6", { workspace, indicators: null }, new Date("2026-08-24T15:00:00Z"));
  assert.equal(stamp, chartGridStamp("mag6", "2026-08-24T15:00:00.000Z"));
  const meta = readChartGridMeta(readChartGridStoreFrom(storage));
  assert.deepEqual(meta.defaultGrid, { name: "mag6", savedAt: "2026-08-24T15:00:00.000Z" });
  // The "$meta" entry never shows up as a grid.
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["mag6"]);
  assert.equal(loadChartGrid(storage, "$meta", options), null);

  // Another browser (no applied stamp) gets it; the saving browser does not.
  assert.equal(pendingDefaultChartGrid(storage, options)?.name, "mag6");
  markChartGridApplied(storage, stamp);
  assert.equal(pendingDefaultChartGrid(storage, options), null);

  // A newer save on any device makes it pending again.
  const later = saveChartGridAs(storage, "mag6", { workspace, indicators: null }, new Date("2026-08-24T16:00:00Z"));
  assert.notEqual(later, stamp);
  assert.equal(pendingDefaultChartGrid(storage, options)?.stamp, later);
});

test("merging the server copy never wipes a grid saved on this device", () => {
  const workspace = normalizeChartWorkspace({ layoutId: "quad", panels: [{ symbol: "AAPL" }] }, options);
  const local = memoryStorage();
  saveChartGridAs(local, "mac-grid", { workspace, indicators: null }, new Date("2026-08-24T15:00:00Z"));
  const remoteStore = {
    "pc-grid": { savedAt: "2026-08-24T14:00:00.000Z", workspace, indicators: null },
  };
  const merged = mergeChartGridStores(readChartGridStoreFrom(local), remoteStore);
  assert.deepEqual(Object.keys(merged.store).filter((name) => name !== "$meta").sort(), ["mac-grid", "pc-grid"]);
  assert.equal(readChartGridMeta(merged.store).defaultGrid.name, "mac-grid");
  assert.equal(merged.changedFromLocal, true);
  assert.equal(merged.changedFromRemote, true);

  // Same content both sides: nothing to write, nothing to push.
  const settled = mergeChartGridStores(merged.store, JSON.parse(JSON.stringify(merged.store)));
  assert.equal(settled.changedFromLocal, false);
  assert.equal(settled.changedFromRemote, false);

  // Same name on both sides: the newer save wins.
  const newer = { ...workspace, layoutId: "two-columns" };
  const conflict = mergeChartGridStores(
    { main: { savedAt: "2026-08-24T10:00:00.000Z", workspace, indicators: null } },
    { main: { savedAt: "2026-08-24T11:00:00.000Z", workspace: newer, indicators: null } },
  );
  assert.equal(conflict.store.main.workspace.layoutId, "two-columns");
  // Garbage on the server is ignored rather than merged in.
  assert.equal(Object.keys(mergeChartGridStores({}, { junk: "x", $meta: 5 }).store).length, 0);
});

test("a delete carries a tombstone so another device cannot resurrect the grid", () => {
  const workspace = normalizeChartWorkspace({ layoutId: "quad", panels: [{ symbol: "AAPL" }] }, options);
  const device = memoryStorage();
  saveChartGridAs(device, "old", { workspace, indicators: null }, new Date("2026-08-24T10:00:00Z"));
  const otherDeviceCopy = JSON.parse(device.getItem(OI_CHART_GRIDS_STORAGE_KEY));
  assert.equal(deleteChartGrid(device, "old", new Date("2026-08-24T12:00:00Z")), true);
  assert.equal(readChartGridMeta(readChartGridStoreFrom(device)).defaultGrid, null);

  const merged = mergeChartGridStores(otherDeviceCopy, readChartGridStoreFrom(device));
  assert.equal("old" in merged.store, false);
  assert.equal(readChartGridMeta(merged.store).deleted.old, "2026-08-24T12:00:00.000Z");
  assert.equal(pendingDefaultChartGrid(memoryStorage(), options), null);

  // Re-saving the same name after the delete clears the tombstone.
  const revived = memoryStorage();
  revived.setItem(OI_CHART_GRIDS_STORAGE_KEY, JSON.stringify(merged.store));
  saveChartGridAs(revived, "old", { workspace, indicators: null }, new Date("2026-08-24T13:00:00Z"));
  const again = mergeChartGridStores(readChartGridStoreFrom(revived), merged.store);
  assert.equal("old" in again.store, true);
  assert.equal(readChartGridMeta(again.store).deleted.old, undefined);
});

test("loading an older grid acknowledges the current default instead of bouncing back to it", () => {
  const storage = memoryStorage();
  const workspace = normalizeChartWorkspace({ layoutId: "quad", panels: [{ symbol: "AAPL" }] }, options);
  saveChartGridAs(storage, "swing", { workspace, indicators: null }, new Date("2026-08-24T14:00:00Z"));
  const scalpStamp = saveChartGridAs(storage, "scalp", { workspace, indicators: null }, new Date("2026-08-24T15:00:00Z"));
  markChartGridApplied(storage, scalpStamp);
  assert.equal(pendingDefaultChartGrid(storage, options), null);

  // The trader loads "swing" from the menu. What the app must stamp is the
  // DEFAULT's stamp (scalp), not swing's - then the reload sees nothing pending.
  const swing = loadChartGrid(storage, "swing", options);
  assert.equal(swing.name, "swing");
  const { defaultGrid } = readChartGridMeta(readChartGridStoreFrom(storage));
  markChartGridApplied(storage, chartGridStamp(defaultGrid.name, defaultGrid.savedAt));
  assert.equal(pendingDefaultChartGrid(storage, options), null);
  // Stamping the loaded grid instead (the old behavior) would have re-applied scalp.
  markChartGridApplied(storage, swing.stamp);
  assert.equal(pendingDefaultChartGrid(storage, options)?.name, "scalp");
});
