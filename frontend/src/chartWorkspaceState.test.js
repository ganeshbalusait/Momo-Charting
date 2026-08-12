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
  saveChartGridAs,
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

  assert.equal(malformed.version, 1);
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
    assert.equal(loaded.version, 1);
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

  assert.equal(saveChartGridAs(storage, "  mag7  ", { workspace, indicators }), true);
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["mag7"]);

  const loaded = loadChartGrid(storage, "mag7", options);
  assert.equal(loaded.workspace.layoutId, "quad");
  assert.equal(loaded.workspace.panels[0].symbol, "NVDA");
  assert.deepEqual(loaded.indicators, indicators);

  assert.equal(saveChartGridAs(storage, "main", { workspace, indicators: null }), true);
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["mag7", "main"]);

  assert.equal(deleteChartGrid(storage, "mag7"), true);
  assert.deepEqual(listChartGrids(storage).map((grid) => grid.name), ["main"]);
  assert.equal(loadChartGrid(storage, "mag7", options), null);
});

test("same-name grid saves overwrite instead of duplicating", () => {
  const storage = memoryStorage();
  const first = normalizeChartWorkspace({ layoutId: "single" }, options);
  const second = normalizeChartWorkspace({ layoutId: "quad" }, options);

  assert.equal(saveChartGridAs(storage, "main", { workspace: first, indicators: null }), true);
  assert.equal(saveChartGridAs(storage, "main", { workspace: second, indicators: null }), true);
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
  assert.equal(saveChartGridAs(storage, "fresh", { workspace, indicators: null }), true);
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
