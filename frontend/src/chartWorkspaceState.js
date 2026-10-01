export const OI_CHART_WORKSPACE_STORAGE_KEY = "oiFinderChartWorkspace";

const WORKSPACE_VERSION = 2;

// Geometry values a pre-v2 build could seed by mistake. Its legacy reader tested
// Number(localStorage.getItem(key)) with isFinite, but Number(null) is 0 -- and 0
// is finite -- so a key that had never been written clamped up to its own minimum
// instead of using the intended fallback. twoPanelSplit is the visible one: every
// fresh profile started at 25, so "2 side by side" rendered 25/75, not 50/50.
const LEGACY_SEEDED_GEOMETRY = Object.freeze([
  // twoPanelSplit is restored to an explicit 50 rather than dropped: App.jsx
  // restores it with isFinite(Number(value)), and Number(null) would read as 0.
  Object.freeze({ key: "twoPanelSplit", seeded: 25, restore: 50 }),
  Object.freeze({ key: "chartHeight", seeded: 240, restore: undefined }),
  Object.freeze({ key: "companionWidth", seeded: 340, restore: undefined }),
]);
const DEFAULT_LAYOUT_IDS = Object.freeze([
  "single",
  "two-columns",
  "two-rows",
  "three-columns",
  "three-grid",
  "three-rows",
  "quad",
  "four-columns",
  "four-rows",
  "six-grid",
  "six-columns",
  "seven-columns",
  "eight-columns",
  "mag7",
  "eight-grid",
  "nine-grid",
  "ten-grid",
]);
const DEFAULT_TIMEFRAMES = Object.freeze([
  "5m", "15m", "1h", "4h", "3m", "10m", "30m", "2h", "D", "W",
]);

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function normalizeStringList(value, fallback = []) {
  const source = Array.isArray(value) ? value : fallback;
  return [...new Set(source
    .map((item) => String(item ?? "").trim())
    .filter(Boolean))];
}

function finiteInteger(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) : fallback;
}

function clampInteger(value, minimum, maximum, fallback = minimum) {
  return Math.min(maximum, Math.max(minimum, finiteInteger(value, fallback)));
}

function normalizeSymbol(value, fallback = "AAPL") {
  const clean = (candidate) => String(candidate ?? "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9./-]/g, "");
  return clean(value) || clean(fallback) || "AAPL";
}

function canonicalTimeframe(value, validTimeframes) {
  const requested = String(value ?? "").trim();
  if (!requested) return "";
  return validTimeframes.find((timeframe) => timeframe === requested)
    || validTimeframes.find((timeframe) => timeframe.toLowerCase() === requested.toLowerCase())
    || "";
}

function serializableClone(value, fallback) {
  try {
    const serialized = JSON.stringify(value);
    if (serialized === undefined) return fallback;
    return JSON.parse(serialized);
  } catch {
    return fallback;
  }
}

/**
 * Read a legacy single-purpose localStorage number, clamped into range.
 *
 * Returns `fallback` when the key is absent, blank, or non-numeric. Callers rely
 * on that: clamping a missing key to `minimum` is what seeded every fresh profile
 * with a lopsided two-chart split.
 */
export function readLegacyGeometryNumber(storage, key, minimum, maximum, fallback = null) {
  let raw = null;
  try {
    raw = storage?.getItem?.(String(key));
  } catch {
    return fallback;
  }
  if (typeof raw !== "string" || !raw.trim()) return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? Math.min(maximum, Math.max(minimum, value)) : fallback;
}

function healLegacySeededGeometry(geometry) {
  const healed = { ...geometry };
  for (const { key, seeded, restore } of LEGACY_SEEDED_GEOMETRY) {
    if (!Object.hasOwn(healed, key) || Number(healed[key]) !== seeded) continue;
    if (restore === undefined) delete healed[key];
    else healed[key] = restore;
  }
  return healed;
}

function workspaceOptions(options = {}) {
  const maxPanels = clampInteger(options.maxPanels, 1, 100, DEFAULT_TIMEFRAMES.length);
  const defaultTimeframes = normalizeStringList(options.defaultTimeframes, DEFAULT_TIMEFRAMES);
  const validTimeframes = normalizeStringList(
    options.validTimeframes,
    defaultTimeframes.length ? defaultTimeframes : DEFAULT_TIMEFRAMES,
  );
  if (!validTimeframes.length) validTimeframes.push("5m");

  const validLayoutIds = normalizeStringList(options.validLayoutIds, DEFAULT_LAYOUT_IDS);
  if (!validLayoutIds.length) validLayoutIds.push("single");
  const requestedFallbackLayout = String(options.fallbackLayoutId ?? "single").trim();
  const fallbackLayoutId = validLayoutIds.includes(requestedFallbackLayout)
    ? requestedFallbackLayout
    : validLayoutIds[0];

  return {
    defaultTimeframes,
    fallbackLayoutId,
    fallbackSymbol: normalizeSymbol(options.fallbackSymbol, "AAPL"),
    maxPanels,
    validLayoutIds,
    validTimeframes,
  };
}

function defaultTimeframeAt(index, options) {
  const requested = options.defaultTimeframes[index]
    ?? options.defaultTimeframes[index % Math.max(options.defaultTimeframes.length, 1)];
  return canonicalTimeframe(requested, options.validTimeframes)
    || options.validTimeframes[0]
    || "5m";
}

export function normalizeChartWorkspace(candidate, options = {}) {
  const resolved = workspaceOptions(options);
  const source = isRecord(candidate) ? candidate : {};
  const sourcePanels = Array.isArray(source.panels) ? source.panels : [];
  const layoutId = resolved.validLayoutIds.includes(String(source.layoutId ?? "").trim())
    ? String(source.layoutId).trim()
    : resolved.fallbackLayoutId;

  const panels = Array.from({ length: resolved.maxPanels }, (_, index) => {
    const panel = isRecord(sourcePanels[index]) ? sourcePanels[index] : {};
    const timeframe = canonicalTimeframe(panel.timeframe, resolved.validTimeframes)
      || defaultTimeframeAt(index, resolved);
    return {
      symbol: normalizeSymbol(panel.symbol, resolved.fallbackSymbol),
      linkGroup: clampInteger(panel.linkGroup, 1, 9, (index % 9) + 1),
      timeframe,
      priceLock: panel.priceLock === true,
    };
  });

  const geometry = isRecord(source.geometry)
    ? serializableClone(source.geometry, {})
    : {};

  const widePanel = source.widePanel == null || !Number.isFinite(Number(source.widePanel))
    ? null
    : clampInteger(source.widePanel, 0, resolved.maxPanels - 1, 0);

  // Anything saved before v2 -- including the versionless legacy localStorage
  // path -- may carry geometry the old reader seeded at its clamp minimum. Reset
  // just those exact values once; deliberately customized geometry is untouched.
  const storedVersion = Number(source.version);
  const isPreV2 = !Number.isFinite(storedVersion) || storedVersion < WORKSPACE_VERSION;

  return {
    version: WORKSPACE_VERSION,
    layoutId,
    isMaximized: source.isMaximized === true,
    companionVisible: source.companionVisible !== false,
    syncSymbols: source.syncSymbols !== false,
    activePanel: clampInteger(source.activePanel, 0, resolved.maxPanels - 1, 0),
    // widePanel 0 was seeded the same way, and it drives the lopsided
    // grid-template-columns on the 3-across layout.
    widePanel: isPreV2 && widePanel === 0 ? null : widePanel,
    panels,
    geometry: isPreV2 ? healLegacySeededGeometry(geometry) : geometry,
  };
}

export function loadChartWorkspace(storage, options = {}) {
  try {
    const storageKey = String(options.storageKey || OI_CHART_WORKSPACE_STORAGE_KEY);
    const raw = storage?.getItem?.(storageKey);
    if (typeof raw !== "string" || !raw.trim()) return normalizeChartWorkspace(null, options);
    return normalizeChartWorkspace(JSON.parse(raw), options);
  } catch {
    return normalizeChartWorkspace(null, options);
  }
}

export function saveChartWorkspace(storage, state, storageKey = OI_CHART_WORKSPACE_STORAGE_KEY) {
  try {
    if (!storage || typeof storage.setItem !== "function" || !isRecord(state)) return false;
    storage.setItem(
      String(storageKey || OI_CHART_WORKSPACE_STORAGE_KEY),
      JSON.stringify({ ...state, version: WORKSPACE_VERSION }),
    );
    return true;
  } catch {
    return false;
  }
}

export const OI_CHART_GRIDS_STORAGE_KEY = "oiFinderChartGrids";
// The store's DEFAULT-grid save this browser has acknowledged, stored as
// "<name>@<savedAt>". Boot compares it with the store's current default: a
// different stamp means a save happened (here or on another device) that this
// browser has not acknowledged yet, so it applies that grid once. Saving
// stamps the saving device; loading ANY grid manually also acknowledges the
// current default (it is not "the grid on screen"), otherwise loading an
// older grid would bounce straight back to the last-saved one on reload.
export const OI_CHART_APPLIED_GRID_STORAGE_KEY = "oiFinderAppliedChartGrid";
// Reserved entry INSIDE the grid store. It rides along with the grids through
// the unchanged /api/chart-grids round-trip (the server persists the object
// exactly as sent), so "which grid is the default" and "which names were
// deleted" reach every device without touching the backend.
export const CHART_GRID_META_KEY = "$meta";

const MAX_SAVED_GRIDS = 24;
const MAX_GRID_NAME_LENGTH = 40;

export function normalizeChartGridName(name) {
  return String(name ?? "").trim().slice(0, MAX_GRID_NAME_LENGTH);
}

function isGridEntryName(name) {
  return Boolean(normalizeChartGridName(name)) && name !== CHART_GRID_META_KEY;
}

// The full store: named grids plus the reserved "$meta" entry.
function readChartGridStore(storage) {
  try {
    const raw = storage?.getItem?.(OI_CHART_GRIDS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function writeChartGridStore(storage, store) {
  storage.setItem(OI_CHART_GRIDS_STORAGE_KEY, JSON.stringify(store));
}

// Only the named grids (no meta), for callers that list/load/delete by name.
function readChartGrids(storage) {
  return Object.fromEntries(
    Object.entries(readChartGridStore(storage)).filter(([name]) => isGridEntryName(name)),
  );
}

export function readChartGridMeta(store) {
  const meta = isRecord(store) && isRecord(store[CHART_GRID_META_KEY]) ? store[CHART_GRID_META_KEY] : {};
  const defaultGrid = isRecord(meta.defaultGrid)
    && isGridEntryName(String(meta.defaultGrid.name || ""))
    && String(meta.defaultGrid.savedAt || "")
    ? { name: String(meta.defaultGrid.name), savedAt: String(meta.defaultGrid.savedAt) }
    : null;
  const deleted = {};
  if (isRecord(meta.deleted)) {
    Object.entries(meta.deleted).forEach(([name, deletedAt]) => {
      if (isGridEntryName(name) && typeof deletedAt === "string" && deletedAt) deleted[name] = deletedAt;
    });
  }
  return { defaultGrid, deleted };
}

function withChartGridMeta(store, meta) {
  const next = { ...store };
  const defaultGrid = meta.defaultGrid ? { name: meta.defaultGrid.name, savedAt: meta.defaultGrid.savedAt } : null;
  const deleted = { ...(meta.deleted || {}) };
  if (!defaultGrid && !Object.keys(deleted).length) {
    delete next[CHART_GRID_META_KEY];
    return next;
  }
  next[CHART_GRID_META_KEY] = { defaultGrid, deleted };
  return next;
}

export function chartGridStamp(name, savedAt) {
  return `${normalizeChartGridName(name)}@${String(savedAt || "")}`;
}

export function listChartGrids(storage) {
  return Object.entries(readChartGrids(storage))
    .filter(([, grid]) => isRecord(grid))
    .map(([name, grid]) => ({ name, savedAt: String(grid.savedAt || "") }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

// Returns the saved grid's stamp ("<name>@<savedAt>") or false. The saved grid
// becomes the store's default - the one every device opens next.
export function saveChartGridAs(storage, name, snapshot, now = new Date()) {
  const gridName = normalizeChartGridName(name);
  if (!storage || typeof storage.setItem !== "function" || !isGridEntryName(gridName) || !isRecord(snapshot)) {
    return false;
  }
  try {
    const store = readChartGridStore(storage);
    const meta = readChartGridMeta(store);
    const savedAt = now.toISOString();
    store[gridName] = {
      savedAt,
      workspace: serializableClone(snapshot.workspace, null),
      indicators: serializableClone(snapshot.indicators, null),
    };
    delete meta.deleted[gridName];
    meta.defaultGrid = { name: gridName, savedAt };
    // Same-name saves overwrite; beyond the cap the oldest grid is dropped so
    // the store cannot grow without bound.
    const names = Object.keys(store).filter(isGridEntryName);
    if (names.length > MAX_SAVED_GRIDS) {
      names
        .sort((a, b) => String(store[a].savedAt || "").localeCompare(String(store[b].savedAt || "")))
        .slice(0, names.length - MAX_SAVED_GRIDS)
        .forEach((stale) => { delete store[stale]; });
    }
    writeChartGridStore(storage, withChartGridMeta(store, meta));
    return chartGridStamp(gridName, savedAt);
  } catch {
    return false;
  }
}

export function loadChartGrid(storage, name, options = {}) {
  const gridName = normalizeChartGridName(name);
  if (!isGridEntryName(gridName)) return null;
  const grid = readChartGrids(storage)[gridName];
  if (!isRecord(grid) || !isRecord(grid.workspace)) return null;
  return {
    name: gridName,
    savedAt: String(grid.savedAt || ""),
    stamp: chartGridStamp(gridName, grid.savedAt),
    workspace: normalizeChartWorkspace(grid.workspace, options),
    indicators: isRecord(grid.indicators) ? grid.indicators : null,
  };
}

// A delete leaves a tombstone in the meta so another device that still holds
// the grid does not resurrect it at the next merge.
export function deleteChartGrid(storage, name, now = new Date()) {
  const gridName = normalizeChartGridName(name);
  if (!storage || typeof storage.setItem !== "function" || !isGridEntryName(gridName)) return false;
  try {
    const store = readChartGridStore(storage);
    if (!(gridName in store)) return false;
    const meta = readChartGridMeta(store);
    delete store[gridName];
    meta.deleted[gridName] = now.toISOString();
    if (meta.defaultGrid?.name === gridName) meta.defaultGrid = null;
    writeChartGridStore(storage, withChartGridMeta(store, meta));
    return true;
  } catch {
    return false;
  }
}

function newerIso(a, b) {
  return String(a || "").localeCompare(String(b || "")) >= 0 ? a : b;
}

// Two copies of the store (this browser's and the server's) become one:
// per name the newer save wins, a tombstone newer than a grid's save removes
// it, and the default grid is whichever side saved it last. Neither side is
// "authoritative" - the old rule (server replaces local whenever it is
// non-empty) silently wiped a grid saved on a device whose push had failed.
export function mergeChartGridStores(localStore, remoteStore) {
  const local = isRecord(localStore) ? localStore : {};
  const remote = isRecord(remoteStore) ? remoteStore : {};
  const localMeta = readChartGridMeta(local);
  const remoteMeta = readChartGridMeta(remote);
  const deleted = { ...localMeta.deleted };
  Object.entries(remoteMeta.deleted).forEach(([name, deletedAt]) => {
    deleted[name] = deleted[name] ? newerIso(deleted[name], deletedAt) : deletedAt;
  });
  const merged = {};
  const names = new Set([...Object.keys(local), ...Object.keys(remote)].filter(isGridEntryName));
  names.forEach((name) => {
    const candidates = [local[name], remote[name]].filter((grid) => isRecord(grid) && isRecord(grid.workspace));
    if (!candidates.length) return;
    const winner = candidates.length === 1
      ? candidates[0]
      : (String(candidates[0].savedAt || "").localeCompare(String(candidates[1].savedAt || "")) >= 0
        ? candidates[0]
        : candidates[1]);
    const tombstone = deleted[name];
    if (tombstone && String(tombstone).localeCompare(String(winner.savedAt || "")) > 0) return;
    delete deleted[name];
    merged[name] = winner;
  });
  let defaultGrid = null;
  [localMeta.defaultGrid, remoteMeta.defaultGrid].forEach((candidate) => {
    if (!candidate || !merged[candidate.name]) return;
    if (!defaultGrid || String(candidate.savedAt).localeCompare(defaultGrid.savedAt) > 0) defaultGrid = candidate;
  });
  const store = withChartGridMeta(merged, { defaultGrid, deleted });
  const serialized = JSON.stringify(store);
  return {
    store,
    changedFromLocal: serialized !== JSON.stringify(local),
    changedFromRemote: serialized !== JSON.stringify(remote),
  };
}

export function readChartGridStoreFrom(storage) {
  return readChartGridStore(storage);
}

export function writeChartGridStoreTo(storage, store) {
  if (!storage || typeof storage.setItem !== "function" || !isRecord(store)) return false;
  try {
    writeChartGridStore(storage, store);
    return true;
  } catch {
    return false;
  }
}

// The default grid this browser has not opened yet, or null. The saving device
// stamps itself at save time, so only OTHER devices (or a browser whose
// workspace predates the save) get it.
export function pendingDefaultChartGrid(storage, options = {}) {
  const store = readChartGridStore(storage);
  const { defaultGrid } = readChartGridMeta(store);
  if (!defaultGrid) return null;
  const grid = loadChartGrid(storage, defaultGrid.name, options);
  if (!grid) return null;
  let applied = "";
  try {
    applied = String(storage?.getItem?.(OI_CHART_APPLIED_GRID_STORAGE_KEY) || "");
  } catch {
    applied = "";
  }
  return applied === grid.stamp ? null : grid;
}

export function markChartGridApplied(storage, stamp) {
  try {
    storage?.setItem?.(OI_CHART_APPLIED_GRID_STORAGE_KEY, String(stamp || ""));
    return true;
  } catch {
    return false;
  }
}

export function updateSharedWorkspaceSymbol(state, symbol, visibleCount) {
  if (!isRecord(state) || !Array.isArray(state.panels) || !state.panels.length) return state;
  const activePanel = clampInteger(state.activePanel, 0, state.panels.length - 1, 0);
  const normalizedSymbol = normalizeSymbol(
    symbol,
    state.panels[activePanel]?.symbol || state.panels[0]?.symbol || "AAPL",
  );
  const panelCount = clampInteger(visibleCount, 1, state.panels.length, state.panels.length);
  const targetIndexes = state.syncSymbols === false
    ? new Set([activePanel])
    : new Set(Array.from({ length: panelCount }, (_, index) => index));

  return {
    ...state,
    panels: state.panels.map((panel, index) => (
      targetIndexes.has(index) ? { ...panel, symbol: normalizedSymbol } : panel
    )),
  };
}

export function updateWorkspacePanelTimeframe(state, panelIndex, timeframe, validTimeframes) {
  if (!isRecord(state) || !Array.isArray(state.panels)) return state;
  const index = Number(panelIndex);
  if (!Number.isInteger(index) || index < 0 || index >= state.panels.length) return state;
  const canonical = canonicalTimeframe(timeframe, normalizeStringList(validTimeframes));
  if (!canonical) return state;

  return {
    ...state,
    panels: state.panels.map((panel, currentIndex) => (
      currentIndex === index ? { ...panel, timeframe: canonical } : panel
    )),
  };
}

/**
 * TOS-style colour link (2026-09-26, "like TOS: when I click the scanner
 * ticker it takes me to the charting page"): put `intent.symbol` into EVERY
 * panel whose linkGroup is `intent.linkGroup` - hidden panels included, the
 * same group semantics as a manual ticker change - and make the first VISIBLE
 * one active so the option chain and its colour tag follow it.
 *
 * Symbol only: each panel keeps its own timeframe (a colour group is usually
 * one ticker across several timeframes, and panels auto-persist). Nothing
 * else changes - no linkGroup, layout, sync flag or geometry.
 *
 * No visible panel in that colour: fall back to what a manual pick in the
 * active panel does (the active panel plus its own colour peers, or every
 * visible panel when the workspace is synced) so the page he lands on always
 * shows the ticker. Returns { state, changed: [indexes], matched: boolean }.
 */
export function applyWorkspaceLinkedNavigationIntent(state, intent, options = {}) {
  const unchanged = { state, changed: [], matched: false };
  if (
    !isRecord(state)
    || !Array.isArray(state.panels)
    || !state.panels.length
    || !isRecord(intent)
  ) return unchanged;
  const group = Number(intent.linkGroup);
  const requestedSymbol = String(intent.symbol ?? "").trim();
  if (!requestedSymbol || !Number.isInteger(group) || group < 1 || group > 9) return unchanged;

  const panelCount = state.panels.length;
  const visibleCount = clampInteger(options.visibleCount, 1, panelCount, panelCount);
  const activePanel = clampInteger(state.activePanel, 0, panelCount - 1, 0);
  const symbol = normalizeSymbol(requestedSymbol, state.panels[activePanel]?.symbol || options.fallbackSymbol);
  const groupOf = (panel) => Number(panel?.linkGroup);

  let targets = state.panels
    .map((panel, index) => (groupOf(panel) === group ? index : -1))
    .filter((index) => index >= 0);
  // "First visible" in ON-SCREEN order: a wide/featured panel is drawn first
  // (options.order = App's visiblePanelIndexes), so it wins the focus.
  const screenOrder = Array.isArray(options.order) && options.order.length
    ? options.order.filter((index) => Number.isInteger(index) && index >= 0 && index < visibleCount)
    : Array.from({ length: visibleCount }, (_, index) => index);
  const visibleTargets = screenOrder.filter((index) => groupOf(state.panels[index]) === group);
  const matched = visibleTargets.length > 0;
  let nextActive = matched ? visibleTargets[0] : activePanel;
  if (!matched) {
    const activeGroup = groupOf(state.panels[activePanel]);
    targets = options.syncWholeWorkspace
      ? Array.from({ length: visibleCount }, (_, index) => index)
      : state.panels
        .map((panel, index) => (index === activePanel || groupOf(panel) === activeGroup ? index : -1))
        .filter((index) => index >= 0);
    nextActive = activePanel < visibleCount ? activePanel : 0;
  }

  const targetSet = new Set(targets);
  const changed = targets.filter((index) => state.panels[index]?.symbol !== symbol);
  if (!changed.length && nextActive === activePanel) return { state, changed: [], matched };
  return {
    state: {
      ...state,
      activePanel: nextActive,
      panels: state.panels.map((panel, index) => (
        targetSet.has(index) && panel.symbol !== symbol ? { ...panel, symbol } : panel
      )),
    },
    changed,
    matched,
  };
}

export function applyWorkspaceNavigationIntent(state, intent, options = {}) {
  if (
    !isRecord(state)
    || !Array.isArray(state.panels)
    || !state.panels.length
    || !isRecord(intent)
  ) return state;

  const requestedSymbol = String(intent.symbol ?? "").trim();
  const validTimeframes = normalizeStringList(options.validTimeframes, DEFAULT_TIMEFRAMES);
  const timeframe = canonicalTimeframe(intent.timeframe, validTimeframes);
  if (!requestedSymbol || !timeframe) return state;

  const activePanel = clampInteger(state.activePanel, 0, state.panels.length - 1, 0);
  const currentPanel = state.panels[activePanel] || {};
  const symbol = normalizeSymbol(requestedSymbol, currentPanel.symbol || options.fallbackSymbol);
  if (currentPanel.symbol === symbol && currentPanel.timeframe === timeframe) return state;

  return {
    ...state,
    panels: state.panels.map((panel, index) => (
      index === activePanel ? { ...panel, symbol, timeframe } : panel
    )),
  };
}
