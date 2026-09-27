// MomX scanner column manager - the pure half (2026-09-22: "make columns
// rearrange move left or right, hide and show option also").
//
// A layout is { version, order: key[], hidden: key[] } over the keys of the
// panel's MOMX_COLUMNS. ONE layout drives both the Live board and the History
// table: both tables derive their column arrays from MOMX_COLUMNS and add a
// Time column that is NOT in the layout (liveColumns / historyColumns in
// momxHistory.js); applyLayout keeps such unlisted columns right after the
// column that precedes them in the input.
//
// Everything here is pure except readStoredLayout / writeStoredLayout, which
// are wrapped so blocked storage (private mode, quota) falls back to the
// default layout instead of throwing.

export const LAYOUT_STORAGE_KEY = "momx.scanner.columnLayout";
export const LAYOUT_VERSION = 1;
// A row must keep its name: Symbol can move but never hide.
export const LOCKED_KEYS = new Set(["symbol"]);

function uniqueStrings(values, allowed) {
  const out = [];
  const seen = new Set();
  for (const value of Array.isArray(values) ? values : []) {
    if (typeof value !== "string" || seen.has(value) || (allowed && !allowed.has(value))) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function keysList(defaultKeys) {
  return uniqueStrings(defaultKeys, null);
}

export function defaultLayout(defaultKeys) {
  return { version: LAYOUT_VERSION, order: keysList(defaultKeys), hidden: [] };
}

// Repair a stored layout against today's column set: unknown keys are dropped,
// keys added since it was saved are inserted after their nearest default
// predecessor (so a new column lands where it would by default, relative to
// wherever the trader moved its neighbour), and locked keys are never hidden.
export function normalizeLayout(layout, defaultKeys) {
  const keys = keysList(defaultKeys);
  if (!layout || typeof layout !== "object" || layout.version !== LAYOUT_VERSION) {
    return defaultLayout(keys);
  }
  const known = new Set(keys);
  const order = uniqueStrings(layout.order, known);
  const present = new Set(order);
  keys.forEach((key, index) => {
    if (present.has(key)) return;
    let at = 0;
    for (let back = index - 1; back >= 0; back -= 1) {
      const position = order.indexOf(keys[back]);
      if (position >= 0) {
        at = position + 1;
        break;
      }
    }
    order.splice(at, 0, key);
    present.add(key);
  });
  const hidden = uniqueStrings(layout.hidden, known).filter((key) => !LOCKED_KEYS.has(key));
  return { version: LAYOUT_VERSION, order, hidden };
}

// Reorder `columns` by the layout and drop the hidden ones. Column objects keep
// their identity. A column whose key is not in `order` (the Time column, or a
// column the layout has never heard of) is placed right after the column that
// precedes it in the input - or first, if it is first in the input.
export function applyLayout(columns, layout) {
  const input = Array.isArray(columns) ? columns.filter((c) => c && typeof c === "object") : [];
  if (!layout || !Array.isArray(layout.order)) return input.slice();
  const byKey = new Map(input.map((column) => [column.key, column]));
  const listed = new Set(layout.order.filter((key) => byKey.has(key)));
  const placed = layout.order.filter((key) => byKey.has(key)).map((key) => byKey.get(key));
  input.forEach((column, index) => {
    if (listed.has(column.key)) return;
    const previous = index > 0 ? placed.indexOf(input[index - 1]) : -1;
    placed.splice(previous + 1, 0, column);
  });
  const hidden = new Set(Array.isArray(layout.hidden) ? layout.hidden : []);
  return placed.filter((column) => LOCKED_KEYS.has(column.key) || !hidden.has(column.key));
}

// One step left (delta -1) or right (+1). At an edge, or for an unknown key,
// the SAME layout object comes back, so a state setter does not re-render.
export function moveKey(layout, key, delta) {
  const order = layout && Array.isArray(layout.order) ? layout.order : [];
  const from = order.indexOf(key);
  const to = from + Math.sign(Number(delta) || 0);
  if (from < 0 || to === from || to < 0 || to >= order.length) return layout;
  const next = order.slice();
  next.splice(from, 1);
  next.splice(to, 0, key);
  return { ...layout, order: next };
}

// Drag-and-drop move (2026-09-22: "rearrange using mouse"): put `key` just
// before or just after `targetKey`. Dropped on itself, onto a column outside
// the layout (Time), or already in that spot -> the SAME layout object, so the
// state setter does not re-render the board for a drop that changed nothing.
export function moveKeyTo(layout, key, targetKey, place) {
  const order = layout && Array.isArray(layout.order) ? layout.order : null;
  if (!order || key === targetKey || (place !== "before" && place !== "after")) return layout;
  if (!order.includes(key) || !order.includes(targetKey)) return layout;
  const next = order.filter((k) => k !== key);
  const at = next.indexOf(targetKey) + (place === "after" ? 1 : 0);
  next.splice(at, 0, key);
  if (next.every((k, i) => k === order[i])) return layout;
  return { ...layout, order: next };
}

// Right-click "Move left" / "Move right": the next column the trader can SEE
// in that direction that the layout owns (hidden columns and the Time column
// are stepped over), or null at an edge / for a column outside the layout.
// moveKeyTo(layout, key, neighbour, delta < 0 ? "before" : "after") then moves
// it exactly one visible step, where moveKey would silently swap it with a
// hidden column and appear to do nothing.
export function visibleNeighbor(visibleColumns, layout, key, delta) {
  const owned = new Set(layout && Array.isArray(layout.order) ? layout.order : []);
  if (!owned.has(key)) return null;
  const keys = (Array.isArray(visibleColumns) ? visibleColumns : [])
    .map((column) => column && column.key)
    .filter((k) => owned.has(k));
  const at = keys.indexOf(key);
  if (at < 0) return null;
  const step = Math.sign(Number(delta) || 0);
  if (!step) return null;
  return keys[at + step] ?? null;
}

// Right-click "Show all columns": unhide everything, keep the order.
export function showAll(layout) {
  if (!layout || !Array.isArray(layout.hidden) || layout.hidden.length === 0) return layout;
  return { ...layout, hidden: [] };
}

export function toggleHidden(layout, key) {
  if (!layout || LOCKED_KEYS.has(key)) return layout;
  const hidden = Array.isArray(layout.hidden) ? layout.hidden : [];
  return hidden.includes(key)
    ? { ...layout, hidden: hidden.filter((k) => k !== key) }
    : { ...layout, hidden: [...hidden, key] };
}

// Contiguous runs of the same group label become one spanning <th>. Run it on
// the APPLIED column list - a colSpan computed from a different list than the
// body renders is how a grouped header silently slips out of alignment.
//
// `pinnedKeys` (pinnedRun's output): each pinned column gets a group cell of
// its own, carrying `pinKey`, so the panel can make that cell sticky too - a
// span shared with scrolling columns would slide away above a pinned header.
export function columnGroupSpans(columns, pinnedKeys) {
  const pinned = new Set(Array.isArray(pinnedKeys) ? pinnedKeys : []);
  const spans = [];
  (Array.isArray(columns) ? columns : []).forEach((column) => {
    const label = (column && column.group) || null;
    const pinKey = column && pinned.has(column.key) ? column.key : null;
    const previous = spans[spans.length - 1];
    if (!pinKey && previous && !previous.pinKey && previous.label === label) {
      previous.span += 1;
      return;
    }
    const span = { label, span: 1, key: (pinKey ? "pin-" + pinKey : label || "gap") + "-" + spans.length };
    if (pinKey) span.pinKey = pinKey;
    spans.push(span);
  });
  return spans;
}

// Frozen columns (2026-09-22: "frozen column symbol + setup + time"). Symbol
// and the run of visible columns straight after it that are Setup or a Time
// column (Live "matchedSince", History "time") stay on screen while the rest
// of the board scrolls sideways. Anything else - or a gap, because Setup was
// dragged away or hidden - ends the run, so what is pinned is always one
// contiguous block starting at Symbol and the sticky offsets simply add up.
//
// 2026-09-25 (his phone): "Industry, Symbol and Time frozen, not Setup; order
// Industry / Symbol / Time / Setup", then "frozen only industry/symbol". So
// Industry, when it sits right before Symbol, leads the run, and nothing
// after Symbol is frozen any more - Time and Setup scroll.
export const PIN_FOLLOWER_KEYS = new Set();
export const PIN_LEADER_KEYS = new Set(["industry"]);

export function pinnedRun(columns) {
  const keys = (Array.isArray(columns) ? columns : [])
    .filter((column) => column && typeof column === "object" && typeof column.key === "string")
    .map((column) => column.key);
  const at = keys.indexOf("symbol");
  if (at < 0) return [];
  const run = at > 0 && PIN_LEADER_KEYS.has(keys[at - 1]) ? [keys[at - 1], "symbol"] : ["symbol"];
  for (let i = at + 1; i < keys.length && PIN_FOLLOWER_KEYS.has(keys[i]); i += 1) {
    if (run.includes(keys[i])) break;
    run.push(keys[i]);
  }
  return run;
}

// The board can stay sorted by a column the trader just hid: the sort state
// and the layout are saved independently, so a header arrow can point at a
// column that no longer renders. Compute ONE effective sort from the two -
// used for both the row order and the header arrow, so they can never
// disagree - by falling back to `defaultSort`'s key (keeping ITS direction)
// when that column is visible, or otherwise the first sortable column left
// in `visibleColumns`' own order.
export function effectiveSort(sort, visibleColumns, defaultSort) {
  const columns = Array.isArray(visibleColumns) ? visibleColumns : [];
  const visible = new Set(
    columns.filter((column) => column && typeof column.key === "string").map((column) => column.key),
  );
  const fallback =
    defaultSort && typeof defaultSort === "object" && typeof defaultSort.key === "string"
      ? defaultSort
      : { key: null, direction: "desc" };
  if (sort && typeof sort === "object" && typeof sort.key === "string" && visible.has(sort.key)) {
    return sort;
  }
  if (typeof fallback.key === "string" && visible.has(fallback.key)) {
    return fallback;
  }
  const firstSortable = columns.find((column) => column && column.sortable && typeof column.key === "string");
  if (firstSortable) {
    return { key: firstSortable.key, direction: fallback.direction === "asc" ? "asc" : "desc" };
  }
  return fallback;
}

export function readStoredLayout(defaultKeys) {
  try {
    const raw = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return defaultLayout(defaultKeys);
    return normalizeLayout(JSON.parse(raw), defaultKeys);
  } catch {
    return defaultLayout(defaultKeys);
  }
}

export function writeStoredLayout(layout) {
  try {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    // Private mode or a full quota: losing the layout is survivable, throwing is not.
  }
}
