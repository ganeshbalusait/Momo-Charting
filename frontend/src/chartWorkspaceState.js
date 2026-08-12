export const OI_CHART_WORKSPACE_STORAGE_KEY = "oiFinderChartWorkspace";

const WORKSPACE_VERSION = 1;
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

  return {
    version: WORKSPACE_VERSION,
    layoutId,
    isMaximized: source.isMaximized === true,
    companionVisible: source.companionVisible !== false,
    syncSymbols: source.syncSymbols !== false,
    activePanel: clampInteger(source.activePanel, 0, resolved.maxPanels - 1, 0),
    widePanel: source.widePanel == null || !Number.isFinite(Number(source.widePanel))
      ? null
      : clampInteger(source.widePanel, 0, resolved.maxPanels - 1, 0),
    panels,
    geometry,
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

const MAX_SAVED_GRIDS = 24;
const MAX_GRID_NAME_LENGTH = 40;

export function normalizeChartGridName(name) {
  return String(name ?? "").trim().slice(0, MAX_GRID_NAME_LENGTH);
}

function readChartGrids(storage) {
  try {
    const raw = storage?.getItem?.(OI_CHART_GRIDS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function listChartGrids(storage) {
  return Object.entries(readChartGrids(storage))
    .filter(([name, grid]) => normalizeChartGridName(name) && isRecord(grid))
    .map(([name, grid]) => ({ name, savedAt: String(grid.savedAt || "") }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function saveChartGridAs(storage, name, snapshot) {
  const gridName = normalizeChartGridName(name);
  if (!storage || typeof storage.setItem !== "function" || !gridName || !isRecord(snapshot)) {
    return false;
  }
  try {
    const grids = readChartGrids(storage);
    grids[gridName] = {
      savedAt: new Date().toISOString(),
      workspace: serializableClone(snapshot.workspace, null),
      indicators: serializableClone(snapshot.indicators, null),
    };
    // Same-name saves overwrite; beyond the cap the oldest grid is dropped so
    // the store cannot grow without bound.
    const names = Object.keys(grids);
    if (names.length > MAX_SAVED_GRIDS) {
      names
        .sort((a, b) => String(grids[a].savedAt || "").localeCompare(String(grids[b].savedAt || "")))
        .slice(0, names.length - MAX_SAVED_GRIDS)
        .forEach((stale) => { delete grids[stale]; });
    }
    storage.setItem(OI_CHART_GRIDS_STORAGE_KEY, JSON.stringify(grids));
    return true;
  } catch {
    return false;
  }
}

export function loadChartGrid(storage, name, options = {}) {
  const grid = readChartGrids(storage)[normalizeChartGridName(name)];
  if (!isRecord(grid) || !isRecord(grid.workspace)) return null;
  return {
    workspace: normalizeChartWorkspace(grid.workspace, options),
    indicators: isRecord(grid.indicators) ? grid.indicators : null,
  };
}

export function deleteChartGrid(storage, name) {
  const gridName = normalizeChartGridName(name);
  if (!storage || typeof storage.setItem !== "function" || !gridName) return false;
  try {
    const grids = readChartGrids(storage);
    if (!(gridName in grids)) return false;
    delete grids[gridName];
    storage.setItem(OI_CHART_GRIDS_STORAGE_KEY, JSON.stringify(grids));
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
