import assert from "node:assert/strict";

// Same runner preamble as momxCells.test.js: green under `node --test` AND
// vitest. vitest sets process.env.VITEST; the import is dynamic so node never
// resolves the "vitest" specifier.
const { test } = process.env.VITEST ? await import("vitest") : await import("node:test");

import {
  LAYOUT_STORAGE_KEY,
  LAYOUT_VERSION,
  LOCKED_KEYS,
  applyLayout,
  columnGroupSpans,
  defaultLayout,
  effectiveSort,
  nextSortState,
  NO_SORT,
  moveKey,
  moveKeyTo,
  normalizeLayout,
  pinnedRun,
  readStoredLayout,
  showAll,
  toggleHidden,
  visibleNeighbor,
  writeStoredLayout,
} from "./momxColumnLayout.js";

const KEYS = ["industry", "symbol", "setup", "fresh", "pctChange", "rvol.2h", "highLow", "rvol.5m"];

const col = (key, group) => (group ? { key, group } : { key });
const COLUMNS = [
  col("industry"),
  col("symbol"),
  col("setup"),
  col("fresh"),
  col("pctChange"),
  col("rvol.2h", "RVOL"),
  col("highLow"),
  col("rvol.5m", "RVOL"),
];

const keysOf = (columns) => columns.map((column) => column.key);

// ---- constants -------------------------------------------------------------

test("constants: storage key, version, Symbol locked", () => {
  assert.equal(LAYOUT_STORAGE_KEY, "momx.scanner.columnLayout");
  assert.equal(LAYOUT_VERSION, 1);
  assert.ok(LOCKED_KEYS.has("symbol"));
});

test("defaultLayout is the default order with nothing hidden", () => {
  const layout = defaultLayout(KEYS);
  assert.deepEqual(layout, { version: 1, order: KEYS, hidden: [] });
  assert.notEqual(layout.order, KEYS, "must be a copy, not the caller's array");
});

// ---- moving ----------------------------------------------------------------

test("moveKey moves one step right and left", () => {
  const start = defaultLayout(KEYS);
  const right = moveKey(start, "setup", 1);
  assert.deepEqual(right.order.slice(1, 4), ["symbol", "fresh", "setup"]);
  const back = moveKey(right, "setup", -1);
  assert.deepEqual(back.order, KEYS);
  // Pure: the input is untouched.
  assert.deepEqual(start.order, KEYS);
});

test("moveKey at the edges is a no-op (same object)", () => {
  const start = defaultLayout(KEYS);
  assert.equal(moveKey(start, "industry", -1), start);
  assert.equal(moveKey(start, "rvol.5m", 1), start);
  assert.equal(moveKey(start, "not-a-column", 1), start);
});

test("moveKey can walk a column all the way to the far right", () => {
  let layout = defaultLayout(KEYS);
  for (let i = 0; i < 20; i += 1) layout = moveKey(layout, "setup", 1);
  assert.equal(layout.order[layout.order.length - 1], "setup");
  assert.equal(layout.order.length, KEYS.length);
});

// ---- hide / show -----------------------------------------------------------

test("toggleHidden hides then shows a column", () => {
  const hidden = toggleHidden(defaultLayout(KEYS), "rvol.5m");
  assert.deepEqual(hidden.hidden, ["rvol.5m"]);
  assert.equal(keysOf(applyLayout(COLUMNS, hidden)).includes("rvol.5m"), false);
  const shown = toggleHidden(hidden, "rvol.5m");
  assert.deepEqual(shown.hidden, []);
});

test("symbol cannot be hidden - not by toggle, not from storage", () => {
  const start = defaultLayout(KEYS);
  assert.equal(toggleHidden(start, "symbol"), start);
  const normalized = normalizeLayout({ version: 1, order: KEYS, hidden: ["symbol", "setup"] }, KEYS);
  assert.deepEqual(normalized.hidden, ["setup"]);
});

// ---- normalizing a stored layout -------------------------------------------

test("an unknown stored key is dropped from order and hidden", () => {
  const stored = { version: 1, order: ["gone", ...KEYS], hidden: ["gone", "fresh"] };
  const normalized = normalizeLayout(stored, KEYS);
  assert.deepEqual(normalized.order, KEYS);
  assert.deepEqual(normalized.hidden, ["fresh"]);
});

test("a new default key missing from a stored layout appears at its default spot", () => {
  // Saved before "fresh" existed, with setup moved to the far right.
  const stored = {
    version: 1,
    order: ["industry", "symbol", "pctChange", "rvol.2h", "highLow", "rvol.5m", "setup"],
    hidden: [],
  };
  const normalized = normalizeLayout(stored, KEYS);
  // "fresh" follows its default predecessor "setup", wherever setup now lives.
  assert.deepEqual(normalized.order, [
    "industry", "symbol", "pctChange", "rvol.2h", "highLow", "rvol.5m", "setup", "fresh",
  ]);
  // A new FIRST column with no predecessor goes to the front.
  const noIndustry = normalizeLayout({ version: 1, order: KEYS.slice(1), hidden: [] }, KEYS);
  assert.deepEqual(noIndustry.order, KEYS);
});

test("normalizeLayout falls back to default on garbage or another version", () => {
  assert.deepEqual(normalizeLayout(null, KEYS), defaultLayout(KEYS));
  assert.deepEqual(normalizeLayout("nope", KEYS), defaultLayout(KEYS));
  assert.deepEqual(normalizeLayout({ version: 99, order: ["symbol"], hidden: [] }, KEYS), defaultLayout(KEYS));
  // Duplicates and non-strings are dropped.
  const messy = normalizeLayout({ version: 1, order: ["symbol", "symbol", 7, ...KEYS], hidden: [null] }, KEYS);
  assert.deepEqual(messy.order, ["symbol", ...KEYS.filter((key) => key !== "symbol")]);
  assert.deepEqual(messy.hidden, []);
});

// ---- applying --------------------------------------------------------------

test("applyLayout reorders and hides by key, keeping column identity", () => {
  let layout = moveKey(defaultLayout(KEYS), "industry", 1); // symbol, industry, ...
  layout = toggleHidden(layout, "fresh");
  const applied = applyLayout(COLUMNS, layout);
  assert.deepEqual(keysOf(applied), ["symbol", "industry", "setup", "pctChange", "rvol.2h", "highLow", "rvol.5m"]);
  assert.equal(applied[0], COLUMNS[1]);
});

test("applyLayout keeps an unlisted time column right after its input predecessor", () => {
  const time = { key: "time" };
  const history = [COLUMNS[0], COLUMNS[1], time, ...COLUMNS.slice(2)];
  let layout = defaultLayout(KEYS);
  // Move symbol one step right: Time rides along after it.
  layout = moveKey(layout, "symbol", 1);
  const applied = keysOf(applyLayout(history, layout));
  assert.deepEqual(applied.slice(0, 5), ["industry", "setup", "symbol", "time", "fresh"]);

  // The live board's matchedSince column after "fresh", with setup hidden.
  const matchedSince = { key: "matchedSince" };
  const live = [...COLUMNS.slice(0, 4), matchedSince, ...COLUMNS.slice(4)];
  const liveApplied = keysOf(applyLayout(live, toggleHidden(defaultLayout(KEYS), "setup")));
  assert.deepEqual(liveApplied.slice(0, 5), ["industry", "symbol", "fresh", "matchedSince", "pctChange"]);

  // It stays even when its predecessor is hidden.
  const freshHidden = keysOf(applyLayout(live, toggleHidden(defaultLayout(KEYS), "fresh")));
  assert.deepEqual(freshHidden.slice(0, 4), ["industry", "symbol", "setup", "matchedSince"]);
});

test("applyLayout: an unlisted first column stays first; a null layout changes nothing", () => {
  const lead = { key: "lead" };
  const applied = keysOf(applyLayout([lead, ...COLUMNS], moveKey(defaultLayout(KEYS), "industry", 1)));
  assert.equal(applied[0], "lead");
  assert.deepEqual(keysOf(applyLayout(COLUMNS, null)), KEYS);
});

test("group spans follow the applied order", () => {
  const columns = [col("rvol.2h", "RVOL"), col("highLow"), col("rvol.5m", "RVOL")];
  const keys = keysOf(columns);
  // Default: split RVOL band.
  assert.deepEqual(
    columnGroupSpans(applyLayout(columns, defaultLayout(keys))).map((span) => [span.label, span.span]),
    [["RVOL", 1], [null, 1], ["RVOL", 1]],
  );
  // highLow moved to the end: one RVOL span of 2, then H/L.
  const moved = moveKey(defaultLayout(keys), "highLow", 1);
  const spans = columnGroupSpans(applyLayout(columns, moved));
  assert.deepEqual(spans.map((span) => [span.label, span.span]), [["RVOL", 2], [null, 1]]);
  assert.equal(new Set(spans.map((span) => span.key)).size, spans.length, "span keys stay unique");
});

// ---- effective sort ---------------------------------------------------------

const SORT_COLUMNS = [
  { key: "symbol", sortable: true },
  { key: "setup", sortable: true },
  { key: "fresh", sortable: true },
  { key: "sparkline", sortable: false },
  { key: "highLow", sortable: true },
];
const HIGH_LOW_DEFAULT = { key: "highLow", direction: "desc" };

test("effectiveSort: visible key is returned unchanged", () => {
  const sort = { key: "setup", direction: "asc" };
  assert.equal(effectiveSort(sort, SORT_COLUMNS, HIGH_LOW_DEFAULT), sort);
});

test("effectiveSort: hidden key falls back to the default sort's key", () => {
  const hiddenKey = { key: "rvol.5m", direction: "desc" };
  assert.deepEqual(effectiveSort(hiddenKey, SORT_COLUMNS, HIGH_LOW_DEFAULT), HIGH_LOW_DEFAULT);
});

test("effectiveSort: default also hidden falls back to the first sortable visible column, default's direction", () => {
  const hiddenKey = { key: "rvol.5m", direction: "asc" };
  const columnsWithoutHighLow = SORT_COLUMNS.filter((column) => column.key !== "highLow");
  assert.deepEqual(
    effectiveSort(hiddenKey, columnsWithoutHighLow, HIGH_LOW_DEFAULT),
    { key: "symbol", direction: "desc" },
  );
});

test("effectiveSort: not-sortable-but-visible key does not count as visible+sortable for the fallback", () => {
  // sparkline is visible but not sortable, and is not the default sort's key,
  // so the fallback must skip past it to the next sortable column.
  const columns = [{ key: "sparkline", sortable: false }, { key: "fresh", sortable: true }];
  assert.deepEqual(
    effectiveSort({ key: "rvol.5m" }, columns, { key: "highLow", direction: "asc" }),
    { key: "fresh", direction: "asc" },
  );
});

test("effectiveSort: garbage sort falls back to the default", () => {
  assert.deepEqual(effectiveSort(null, SORT_COLUMNS, HIGH_LOW_DEFAULT), HIGH_LOW_DEFAULT);
  assert.deepEqual(effectiveSort({}, SORT_COLUMNS, HIGH_LOW_DEFAULT), HIGH_LOW_DEFAULT);
  assert.deepEqual(effectiveSort({ key: 42 }, SORT_COLUMNS, HIGH_LOW_DEFAULT), HIGH_LOW_DEFAULT);
});

// ---- storage ---------------------------------------------------------------

function withStorage(storage, fn) {
  const had = Object.prototype.hasOwnProperty.call(globalThis, "window");
  const previous = globalThis.window;
  globalThis.window = storage === undefined ? {} : { localStorage: storage };
  try {
    return fn();
  } finally {
    if (had) globalThis.window = previous;
    else delete globalThis.window;
  }
}

function memoryStorage() {
  const map = new Map();
  return {
    map,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
  };
}

test("stored layout round-trips", () => {
  const storage = memoryStorage();
  withStorage(storage, () => {
    const layout = toggleHidden(moveKey(defaultLayout(KEYS), "setup", 3), "rvol.5m");
    writeStoredLayout(layout);
    assert.ok(storage.map.has(LAYOUT_STORAGE_KEY));
    assert.deepEqual(readStoredLayout(KEYS), layout);
  });
});

test("storage throwing or missing falls back to the default layout", () => {
  const throwing = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("QuotaExceeded"); },
  };
  withStorage(throwing, () => {
    assert.deepEqual(readStoredLayout(KEYS), defaultLayout(KEYS));
    assert.doesNotThrow(() => writeStoredLayout(defaultLayout(KEYS)));
  });
  withStorage(undefined, () => {
    assert.deepEqual(readStoredLayout(KEYS), defaultLayout(KEYS));
    assert.doesNotThrow(() => writeStoredLayout(defaultLayout(KEYS)));
  });
  const corrupt = memoryStorage();
  corrupt.setItem(LAYOUT_STORAGE_KEY, "{not json");
  withStorage(corrupt, () => {
    assert.deepEqual(readStoredLayout(KEYS), defaultLayout(KEYS));
  });
});

// ---- drag-and-drop move / right-click "Show all" ---------------------------

test("moveKeyTo drops a column before or after the target header", () => {
  const start = defaultLayout(KEYS);
  const before = moveKeyTo(start, "rvol.5m", "symbol", "before");
  assert.deepEqual(before.order, ["industry", "rvol.5m", "symbol", "setup", "fresh", "pctChange", "rvol.2h", "highLow"]);
  const after = moveKeyTo(start, "industry", "fresh", "after");
  assert.deepEqual(after.order, ["symbol", "setup", "fresh", "industry", "pctChange", "rvol.2h", "highLow", "rvol.5m"]);
  // Leftward drop after a target, rightward drop before one.
  assert.deepEqual(moveKeyTo(start, "highLow", "symbol", "after").order.slice(0, 3), ["industry", "symbol", "highLow"]);
  assert.deepEqual(moveKeyTo(start, "symbol", "rvol.5m", "before").order.slice(-2), ["symbol", "rvol.5m"]);
  // Symbol moves like any other column.
  assert.equal(moveKeyTo(start, "symbol", "rvol.5m", "after").order.at(-1), "symbol");
  // Pure.
  assert.deepEqual(start.order, KEYS);
  assert.equal(before.version, start.version);
});

test("moveKeyTo keeps hidden columns and their hidden state", () => {
  const start = toggleHidden(defaultLayout(KEYS), "fresh");
  const moved = moveKeyTo(start, "fresh", "industry", "before");
  assert.equal(moved.order[0], "fresh");
  assert.deepEqual(moved.hidden, ["fresh"]);
});

test("moveKeyTo returns the SAME object when nothing changes", () => {
  const start = defaultLayout(KEYS);
  assert.equal(moveKeyTo(start, "setup", "setup", "before"), start, "dropped on itself");
  assert.equal(moveKeyTo(start, "setup", "setup", "after"), start);
  assert.equal(moveKeyTo(start, "setup", "symbol", "after"), start, "already right after the target");
  assert.equal(moveKeyTo(start, "symbol", "setup", "before"), start, "already right before the target");
  assert.equal(moveKeyTo(start, "nope", "setup", "before"), start, "unknown dragged key");
  assert.equal(moveKeyTo(start, "setup", "time", "before"), start, "target outside the layout (Time)");
  assert.equal(moveKeyTo(start, "setup", "fresh", "sideways"), start, "bad place");
  assert.equal(moveKeyTo(null, "setup", "fresh", "before"), null);
});

test("showAll unhides every column and keeps the order", () => {
  let layout = moveKeyTo(defaultLayout(KEYS), "highLow", "industry", "before");
  layout = toggleHidden(toggleHidden(layout, "setup"), "rvol.2h");
  const shown = showAll(layout);
  assert.deepEqual(shown.hidden, []);
  assert.deepEqual(shown.order, layout.order);
  assert.deepEqual(layout.hidden, ["setup", "rvol.2h"], "input untouched");
  const plain = defaultLayout(KEYS);
  assert.equal(showAll(plain), plain, "nothing hidden -> same object");
  assert.equal(showAll(null), null);
});

test("visibleNeighbor steps over hidden columns and columns outside the layout", () => {
  const layout = toggleHidden(defaultLayout(KEYS), "setup");
  // On screen: industry symbol [time] fresh pctChange ... (setup hidden, time not in layout)
  const onScreen = ["industry", "symbol", "time", "fresh", "pctChange", "rvol.2h", "highLow", "rvol.5m"].map((key) => ({ key }));
  assert.equal(visibleNeighbor(onScreen, layout, "fresh", -1), "symbol");
  assert.equal(visibleNeighbor(onScreen, layout, "symbol", 1), "fresh");
  assert.equal(visibleNeighbor(onScreen, layout, "industry", -1), null, "left edge");
  assert.equal(visibleNeighbor(onScreen, layout, "rvol.5m", 1), null, "right edge");
  assert.equal(visibleNeighbor(onScreen, layout, "time", 1), null, "Time is not movable");
  // Moving past the neighbour really moves it one visible step.
  const moved = moveKeyTo(layout, "fresh", visibleNeighbor(onScreen, layout, "fresh", -1), "before");
  assert.deepEqual(moved.order.slice(0, 3), ["industry", "fresh", "symbol"]);
});

// ---- pinned run (2026-09-25: "frozen only industry/symbol") ----------------

const LIVE_DEFAULT = ["industry", "symbol", "matchedSince", "setup", "fresh", "pctChange", "rvol.2h"].map((key) => ({ key }));
const HISTORY_DEFAULT = ["industry", "symbol", "time", "setup", "fresh", "pctChange"].map((key) => ({ key }));

test("pinnedRun: Live and History pin only Industry | Symbol; Time and Setup scroll", () => {
  assert.deepEqual(pinnedRun(LIVE_DEFAULT), ["industry", "symbol"]);
  assert.deepEqual(pinnedRun(HISTORY_DEFAULT), ["industry", "symbol"]);
});

test("pinnedRun: Industry leads only when it sits right before Symbol", () => {
  assert.deepEqual(pinnedRun(["symbol", "matchedSince", "industry"].map((key) => ({ key }))), ["symbol"]);
  assert.deepEqual(pinnedRun(["industry", "fresh", "symbol", "time"].map((key) => ({ key }))), ["symbol"]);
});

test("pinnedRun: no Symbol / junk input", () => {
  assert.deepEqual(pinnedRun([{ key: "setup" }, { key: "fresh" }]), []);
  assert.deepEqual(pinnedRun(null), []);
  assert.deepEqual(pinnedRun([null, { key: "symbol" }, { key: "setup" }]), ["symbol"]);
});

test("columnGroupSpans: pinned columns get their own spacer cell carrying pinKey", () => {
  const columns = [col("industry"), col("symbol"), col("setup"), col("fresh"), col("rvol.2h", "RVOL"), col("rvol.5m", "RVOL")];
  const spans = columnGroupSpans(columns, ["symbol", "setup"]);
  assert.deepEqual(
    spans.map((span) => [span.label, span.span, span.pinKey || null]),
    [[null, 1, null], [null, 1, "symbol"], [null, 1, "setup"], [null, 1, null], ["RVOL", 2, null]],
  );
  assert.equal(spans.reduce((sum, span) => sum + span.span, 0), columns.length);
  assert.equal(new Set(spans.map((span) => span.key)).size, spans.length, "span keys stay unique");
  // No pinned list -> unchanged behaviour.
  assert.deepEqual(columnGroupSpans(columns).map((span) => span.span), [4, 2]);
});

test("header clicks cycle high-to-low, low-to-high, then NO SORT", () => {
  const hl = { key: "highLow", kind: "bar" };
  const sym = { key: "symbol", kind: "symbol" };
  let s = nextSortState(null, hl);
  assert.deepEqual(s, { key: "highLow", direction: "desc" });
  s = nextSortState(s, hl);
  assert.deepEqual(s, { key: "highLow", direction: "asc" });
  s = nextSortState(s, hl);
  assert.equal(s, NO_SORT);
  assert.deepEqual(nextSortState(s, hl), { key: "highLow", direction: "desc" });
  // Names start A-Z; a different column always starts fresh.
  assert.deepEqual(nextSortState({ key: "highLow", direction: "asc" }, sym), { key: "symbol", direction: "asc" });
  assert.deepEqual(nextSortState({ key: "symbol", direction: "asc" }, sym), { key: "symbol", direction: "desc" });
  assert.equal(nextSortState({ key: "time", direction: "desc" }, { key: "time" }, "asc"), NO_SORT);
  // NO SORT survives the hidden-column fallback instead of snapping back.
  assert.equal(effectiveSort(NO_SORT, [hl], { key: "highLow", direction: "desc" }), NO_SORT);
});
