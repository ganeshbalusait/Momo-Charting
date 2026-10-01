// MomX scanner history - pure helpers for the 30-day archive view.
//
// Spec: docs/superpowers/specs/2026-08-31-momx-scanner-history-design.md
//
// Everything in this file is a pure function over plain data so it runs under
// `node --test` with no DOM and no fetch. The panel (MomxScannerPanel.jsx)
// owns the fetching and rendering; this module owns the decisions:
//
// - historyColumns:    the history table's columns ARE the live board's
//                      COLUMNS array with one Time column spliced in after
//                      Symbol. Column parity is structural, not a copy.
// - historyRequest:    which of the three API query forms a (date, search)
//                      pair resolves to - typing a ticker switches to the
//                      cross-day symbol form, clearing it returns to the day.
// - resolveDayNav:     prev/next day resolution for the ‹ date › control,
//                      with the arrows DISABLED (not hidden) at the ends.
// - flattenHistoryRows: one table row per snapshot (the duplicates the trader
//                      asked for), each carrying its ET time label, its
//                      changed-cell set, and whether it should render the
//                      sparkline/quote cells (arrivals only).
// - groupRowsByDay:    the search view's day-header grouping, newest first.
// - sortHistoryRows:   header-click sorting for the history table. History
//                      rows WRAP a board row, so this reaches into entry.row
//                      for the board columns and owns Time/Symbol itself; the
//                      comparison itself is momxCells.sortRows, reused, so the
//                      two tables can never sort differently.

import { sortRows } from "./momxCells.js";

export const MOMX_HISTORY_ENDPOINT = "/api/momx-scanner/history";

// Mirrors the backend's RETENTION_DAYS; the API response carries the live
// value in `retentionDays` and the UI prefers that when present.
export const MOMX_HISTORY_RETENTION_DAYS = 30;

// The Time column spliced into the live COLUMNS array. `kind: "time"` is not
// a live-board kind, so the live row renderer can never accidentally receive
// it; only the history row renderer knows it.
export const MOMX_HISTORY_TIME_COLUMN = Object.freeze({
  key: "time",
  label: "Time",
  kind: "time",
  sortable: true,
});

// The LIVE board's Time column - when that ticker ENTERED the scan
// (row.matchedSince). Asked for on 2026-09-01: "Mag7 & watchlist - add one
// more column time same history, if I want I will keep or I will hide it".
//
// It is defined HERE, beside the history one, on purpose: the two are a PAIR
// and the trap is only visible when you can see both at once. Its key is
// deliberately DISTINCT ("matchedSince", not "time") and it is spliced into a
// LIVE-ONLY array (liveColumns below), so historyColumns(MOMX_COLUMNS) can
// never see it and the history table can never grow a second Time header. The
// already-has-one guard in insertTimeColumn is belt-and-braces on top of that.
export const MOMX_LIVE_TIME_COLUMN = Object.freeze({
  key: "matchedSince",
  label: "Time",
  kind: "matchedSince",
  sortable: true,
});

// True for EITHER table's Time column, whichever array it arrived in.
function isTimeColumn(column) {
  if (!column || typeof column !== "object") return false;
  return (
    column.key === MOMX_HISTORY_TIME_COLUMN.key ||
    column.key === MOMX_LIVE_TIME_COLUMN.key ||
    column.kind === "time" ||
    column.kind === "matchedSince"
  );
}

// Splice ONE Time column in directly after Symbol (or after the first key of
// `afterKeys` the array actually has). If the incoming array
// ALREADY carries a time column, that column is REPLACED in place rather than
// added to: a table may have exactly one Time header, ever. That is what stops
// the live board's Time column - should anyone ever move it into the shared
// MOMX_COLUMNS array - from giving the history table two.
export function insertTimeColumn(columns, timeColumn, afterKeys = ["symbol"]) {
  const live = Array.isArray(columns)
    ? columns.filter((column) => column && typeof column === "object")
    : [];
  const time = timeColumn && typeof timeColumn === "object" ? timeColumn : MOMX_HISTORY_TIME_COLUMN;
  if (live.some(isTimeColumn)) return live.map((column) => (isTimeColumn(column) ? time : column));
  const keys = new Set(live.map((column) => column.key));
  const anchor = (Array.isArray(afterKeys) ? afterKeys : []).find((key) => keys.has(key));
  const out = [];
  let inserted = false;
  for (const column of live) {
    out.push(column);
    if (!inserted && anchor !== undefined && column.key === anchor) {
      out.push(time);
      inserted = true;
    }
  }
  // A columns array with no symbol column is malformed, but the Time column
  // must still exist somewhere or the whole feature is pointless.
  if (!inserted) out.unshift(time);
  return out;
}

// The history table's columns: the SAME array the live board renders from,
// with Time directly after Symbol. Anything else (adding, dropping or
// reordering a live column) flows into history automatically - that is the
// column-parity requirement, and momxHistory.test.js asserts it against the
// panel's real MOMX_COLUMNS source.
export function historyColumns(columns) {
  return insertTimeColumn(columns, MOMX_HISTORY_TIME_COLUMN);
}

// The LIVE board's columns: the same array with the matchedSince Time column
// after Setup (Symbol | Setup | Time | Fresh - 2026-09-22 "frozen column
// symbol + setup + time": the three frozen columns sit together), or after
// Symbol when there is no Setup column. The panel renders this one,
// and drops the Time column out of it when the trader hides the column.
export function liveColumns(columns) {
  // Time right after Symbol (2026-09-25: "Industry / Symbol / Time / Setup").
  return insertTimeColumn(columns, MOMX_LIVE_TIME_COLUMN, ["symbol"]);
}

// Which API query a (date, symbol-search) pair maps to. A non-empty search
// term WINS: the view switches to that ticker's snapshots across all days.
// Clearing the search returns to the selected day - the date is preserved in
// the day-mode request, so "back" really is back.
/** The board cache key for one list on one board: bull keys are the bare list
 * name (unchanged), the bear board's are suffixed so the two can never be
 * served for each other (spec 2026-09-24). */
export function boardCacheKey(list, direction) {
  if (list === null || list === undefined) return list;
  return direction === "bear" ? list + "|bear" : list;
}

export function historyRequest(list, options) {
  const opts = options || {};
  const symbol = typeof opts.symbol === "string" ? opts.symbol.trim().toUpperCase() : "";
  const date = typeof opts.date === "string" && opts.date ? opts.date : null;
  const direction = opts.direction === "bear" ? "bear" : "bull";
  const params = new URLSearchParams();
  params.set("list", typeof list === "string" ? list : "");
  // The BEAR archive lives under its own root on the worker (?dir=bear).
  if (direction === "bear") params.set("dir", "bear");
  let mode = "day";
  if (symbol !== "") {
    params.set("symbol", symbol);
    mode = "symbol";
    // A CHOSEN day narrows the ticker search to that day (2026-09-05: "search
    // like time, or date or search or all three"). No chosen day is still
    // every day - the panel shows that as "All days" in the day picker.
    if (date) params.set("date", date);
  } else if (date) {
    params.set("date", date);
  }
  // Rows back from the NEWEST, so page 1 is always what just happened. Sent
  // only when non-zero so the common request keeps its old URL, and with it
  // its cache entry.
  const offset = Number.isFinite(opts.offset) ? Math.max(0, Math.trunc(opts.offset)) : 0;
  if (offset > 0) params.set("offset", String(offset));
  return {
    mode,
    offset,
    url: MOMX_HISTORY_ENDPOINT + "?" + params.toString(),
    // Cache key for the panel's fetched-once-per-view map. The offset is part
    // of the identity: without it, page 2 would be served page 1 from cache.
    key:
      (direction === "bear" ? "bear|" : "") +
      (mode === "symbol"
        ? list + "|sym:" + symbol + (date ? "@" + date : "")
        : list + "|" + (date || "latest")) +
      (offset > 0 ? "|+" + offset : ""),
  };
}

const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

// Today's date in ET as "YYYY-MM-DD" (en-CA formats exactly that). The
// archive's day files are keyed by ET day; the panel uses this to tell an
// IMMUTABLE payload (a past day, cacheable forever) from one still growing
// while the scan runs (today / newest / search) -- a trader opening history
// at 15:00 must not silently be shown the 09:40 fetch of today.
export function etDayIso(when) {
  const stamp = when instanceof Date ? when : new Date();
  if (Number.isNaN(stamp.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(stamp);
  } catch {
    return "";
  }
}
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-08-31" -> "Mon 31 Aug 2026". Built by hand from UTC parts, NOT via
// toLocaleDateString: an ISO date fed to `new Date` parses as UTC midnight,
// and a local-zone formatter west of Greenwich would print the previous day.
export function dayLabel(dateStr) {
  if (typeof dateStr !== "string" || !DAY_RE.test(dateStr)) return "";
  const [y, m, d] = dateStr.split("-").map(Number);
  if (m < 1 || m > 12 || d < 1 || d > 31) return "";
  const stamp = new Date(Date.UTC(y, m - 1, d));
  return WEEKDAYS[stamp.getUTCDay()] + " " + d + " " + MONTHS[m - 1] + " " + y;
}

// Day-nav resolution. `days` is the API's newest-first list (re-sorted here
// defensively); `selected` is the date the trader is on, or null for newest.
// ‹ goes to the OLDER day, › to the NEWER; at either end the arrow's target
// is null and its can-flag false, which the UI renders as a disabled button.
export function resolveDayNav(days, selected) {
  const cleaned = Array.isArray(days) ? days.filter((d) => typeof d === "string" && DAY_RE.test(d)) : [];
  const sorted = [...new Set(cleaned)].sort().reverse();
  if (sorted.length === 0) {
    return {
      days: sorted,
      date: null,
      label: "",
      canOlder: false,
      canNewer: false,
      olderDate: null,
      newerDate: null,
    };
  }
  const date = sorted.includes(selected) ? selected : sorted[0];
  const index = sorted.indexOf(date);
  return {
    days: sorted,
    date,
    label: dayLabel(date),
    canOlder: index < sorted.length - 1,
    canNewer: index > 0,
    olderDate: index < sorted.length - 1 ? sorted[index + 1] : null,
    newerDate: index > 0 ? sorted[index - 1] : null,
  };
}

// The snapshot's clock time in ET - the "so I know when it came to the
// scanner" column. 24h on purpose: the archive spans premarket, where
// "8:01" vs "08:01" is the difference between a.m. certainty and a guess.
export function snapshotTimeLabel(iso) {
  if (typeof iso !== "string" || iso === "") return "";
  const stamp = new Date(iso);
  if (Number.isNaN(stamp.getTime())) return "";
  try {
    return stamp.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
  } catch {
    return "";
  }
}

// Maps one snapshot's `changed` list onto the COLUMN KEYS whose cells get the
// highlight. The backend names trigger fields (spec section "What counts as a
// change"); three of them have no column of their own and light up the Symbol
// cell, where the thing they change (badge, news icon, row tooltip) lives.
export function changedColumnKeys(changed) {
  const keys = new Set();
  if (!Array.isArray(changed)) return keys;
  for (const name of changed) {
    if (typeof name !== "string" || name === "") continue;
    if (name.startsWith("rvol.") || name.startsWith("sqz.") || name.startsWith("skittles.")) {
      keys.add(name);
    } else if (name === "highLow" || name === "color") {
      keys.add(name);
    } else if (name === "badge.on" || name === "badge" || name === "news.headline" || name === "news" || name === "scanReasons") {
      keys.add("symbol");
    }
    // Unknown names are ignored rather than guessed at: a future trigger
    // field simply doesn't highlight until this map learns its column.
  }
  return keys;
}

// One table row per snapshot - the duplicate entries the trader explicitly
// asked for - normalised for rendering. Sorted ascending by time so a day
// reads as the timeline he experienced. Change rows blank the sparkline and
// quote-trend cells (showChart false): the backend strips those fields from
// change snapshots, and the blank cells are what make arrivals visually
// distinct in the table.
export function flattenHistoryRows(rows) {
  if (!Array.isArray(rows)) return [];
  const entries = [];
  rows.forEach((raw, index) => {
    if (!raw || typeof raw !== "object") return;
    const row = raw.row && typeof raw.row === "object" ? raw.row : null;
    const symbol =
      typeof raw.symbol === "string" && raw.symbol !== ""
        ? raw.symbol
        : row && typeof row.symbol === "string"
          ? row.symbol
          : "";
    if (symbol === "" || !row) return;
    const at = typeof raw.at === "string" ? raw.at : "";
    const atMs = at ? Date.parse(at) : NaN;
    const isArrival = raw.isArrival === undefined ? !Array.isArray(raw.changed) || raw.changed.length === 0 : Boolean(raw.isArrival);
    const changed = Array.isArray(raw.changed) ? raw.changed : [];
    entries.push({
      key: symbol + "|" + (raw.date || "") + "|" + at + "|" + index,
      symbol,
      at,
      atMs: Number.isFinite(atMs) ? atMs : 0,
      date: typeof raw.date === "string" && DAY_RE.test(raw.date) ? raw.date : null,
      timeLabel: snapshotTimeLabel(at),
      isArrival,
      changed,
      changedKeys: changedColumnKeys(changed),
      hits: Number.isFinite(Number(raw.hits)) ? Number(raw.hits) : null,
      truncated: Boolean(raw.truncated),
      capped: false, // set below, on the LAST snapshot of a capped ticker
      peakRvol: Number.isFinite(Number(raw.peakRvol)) ? Number(raw.peakRvol) : null,
      showChart: isArrival,
      row,
    });
  });
  entries.sort((a, b) => (a.atMs === b.atMs ? (a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0) : a.atMs - b.atMs));
  // "capped" is shown once, on the ticker's LAST recorded row of the day -
  // the point after which the archive went silent, not every row before it.
  const lastIndexByKey = new Map();
  entries.forEach((entry, index) => {
    lastIndexByKey.set(entry.symbol + "|" + (entry.date || ""), index);
  });
  entries.forEach((entry, index) => {
    if (entry.truncated && lastIndexByKey.get(entry.symbol + "|" + (entry.date || "")) === index) {
      entry.capped = true;
    }
  });
  return entries;
}

// Search view: that ticker's snapshots across every archived day, grouped
// under day headers, newest day first; within a day, oldest first (the
// timeline again). Entries without a date land in a trailing "" group rather
// than vanishing.
export function groupRowsByDay(entries) {
  const flat = Array.isArray(entries) ? entries : [];
  const byDate = new Map();
  for (const entry of flat) {
    const date = entry && typeof entry === "object" ? entry.date || "" : "";
    if (!byDate.has(date)) byDate.set(date, []);
    byDate.get(date).push(entry);
  }
  const dates = [...byDate.keys()].sort().reverse();
  const withDate = dates.filter((d) => d !== "");
  const groups = withDate.map((date) => ({
    date,
    label: dayLabel(date),
    rows: byDate.get(date),
  }));
  if (byDate.has("")) groups.push({ date: null, label: "Unknown day", rows: byDate.get("") });
  return groups;
}

// ---------------------------------------------------------------------------
// Sorting the history table
// ---------------------------------------------------------------------------
//
// TIME ASCENDING is the default and it must stay that way: the day is a
// timeline of arrivals, and that timeline IS the page. Clicking Time toggles
// it; clicking any other header sorts by that column.
export const MOMX_HISTORY_DEFAULT_SORT = Object.freeze({ key: "time", direction: "asc" });

// One history row is a SNAPSHOT ENTRY that WRAPS a board row
// ({symbol, at, atMs, row, changed, ...}), so a board column's value lives one
// level down, in entry.row. Two columns are the entry's own and have no board
// value at all:
//   time   -> the snapshot instant (entry.atMs)
//   symbol -> the entry's symbol (present even when the archived row is thin)
// Everything else is delegated to `resolveValue(row, key)` - the panel passes
// its columnSortValue, the SAME function the live board sorts on, so a cell
// column sorts on the cell's VALUE here exactly as it does there.
function historySortValue(entry, key, resolveValue) {
  if (key === "time") return Number.isFinite(entry.atMs) ? entry.atMs : null;
  if (key === "symbol") {
    return typeof entry.symbol === "string" && entry.symbol !== "" ? entry.symbol : null;
  }
  const row = entry.row && typeof entry.row === "object" ? entry.row : null;
  if (!row) return null;
  if (typeof resolveValue === "function") {
    const value = resolveValue(row, key);
    return value === undefined ? null : value;
  }
  const raw = row[key];
  return raw === undefined ? null : raw;
}

// The comparison itself is momxCells.sortRows - NOT a second comparator that
// would drift away from the live board's. Each entry is projected onto a
// throwaway index object carrying its sort value plus the symbol sortRows
// tie-breaks on, exactly the way the live board projects its rows, and the
// entries are read back off the sorted index. Missing values sort LAST in both
// directions because that is sortRows' rule, not a new one invented here.
export function sortHistoryRows(entries, sort, resolveValue) {
  const list = Array.isArray(entries)
    ? entries.filter((entry) => entry && typeof entry === "object")
    : [];
  // NO SORT (third header click): the timeline order as it arrived.
  if (sort && sort.direction === "none") return list;
  const key =
    sort && typeof sort.key === "string" && sort.key ? sort.key : MOMX_HISTORY_DEFAULT_SORT.key;
  const direction = sort && String(sort.direction).toLowerCase() === "desc" ? "desc" : "asc";
  if (list.length < 2) return list;
  // Ties inside sortRows resolve on symbol; the incoming entries are already
  // time-ascending (flattenHistoryRows) and Array.prototype.sort is stable, so
  // rows that tie keep their timeline order underneath whatever is sorted.
  const index = list.map((entry) => ({
    symbol: typeof entry.symbol === "string" ? entry.symbol : null,
    [key]: historySortValue(entry, key, resolveValue),
    entry,
  }));
  return sortRows(index, key, direction).map((item) => item.entry);
}

// SEARCH view: one ticker across many days. The day grouping IS the structure
// of that page, so sorting happens INSIDE each day, never across them - a
// "sort by RVOL" that interleaved Tuesday and Friday would destroy the only
// thing the search view is for.
export function sortHistoryGroups(groups, sort, resolveValue) {
  if (!Array.isArray(groups)) return [];
  return groups.map((group) => ({
    ...group,
    rows: sortHistoryRows(group && group.rows, sort, resolveValue),
  }));
}

// Which columns never show a difference, and why:
//   time    - every row differs by definition; a delta there is noise
//   highLow - a continuous float that moves on every tick. It stopped being a
//             trigger on 2026-09-01 for that reason, so showing its drift
//             would reintroduce exactly the noise that removal took out.
// The trader named both: "other than time and high/low any column change
// anything show the difference between 2".
const NO_DIFFERENCE_COLUMNS = new Set(["time", "highLow"]);

// Read a column's raw value out of a stored snapshot row. Deliberately mirrors
// cellFor/columnSortValue in MomxScannerPanel.jsx: section+tf for the grouped
// cells (rvol/sqz/skittles), a bare field otherwise. Keyed off the CHANGED
// name (e.g. "rvol.5m") because that is what the recorder writes.
function snapshotValue(row, changedKey) {
  if (!row || typeof row !== "object" || typeof changedKey !== "string") return null;
  const dot = changedKey.indexOf(".");
  const cell = dot === -1
    ? row[changedKey]
    : ((row[changedKey.slice(0, dot)] || {})[changedKey.slice(dot + 1)]);
  if (cell === undefined || cell === null) return null;
  if (typeof cell === "object") return cell.value === undefined ? null : cell.value;
  return cell;
}

/**
 * Annotate each snapshot with what its changed cells moved FROM.
 *
 * A ringed cell says a number moved; it does not say what it moved from, which
 * is the part worth reading. The previous value is already on screen - each
 * ticker's snapshots are consecutive - so this needs no stored history and
 * works retroactively on everything already archived.
 *
 * Comparison is PER SYMBOL: the day is a timeline of many tickers interleaved,
 * so the row physically above is usually a different stock. Comparing against
 * it would invent differences that never happened.
 *
 * Returns NEW entry objects; the input is never mutated (the caller's array is
 * React state).
 */
export function annotateChanges(entries) {
  if (!Array.isArray(entries)) return [];
  const previousBySymbol = new Map();
  return entries.map((entry) => {
    if (!entry || typeof entry !== "object") return entry;
    const symbol = String(entry.symbol || "");
    const previousRow = previousBySymbol.get(symbol) || null;
    previousBySymbol.set(symbol, entry.row || null);

    const changes = {};
    for (const key of Array.isArray(entry.changed) ? entry.changed : []) {
      if (typeof key !== "string" || NO_DIFFERENCE_COLUMNS.has(key)) continue;
      const to = snapshotValue(entry.row, key);
      const from = previousRow ? snapshotValue(previousRow, key) : null;
      const bothNumeric = typeof from === "number" && Number.isFinite(from)
        && typeof to === "number" && Number.isFinite(to);
      const delta = bothNumeric ? to - from : null;
      changes[key] = {
        from,
        to,
        delta,
        // "up"/"down" only when a delta is real. A squeeze going "-" -> "*2"
        // has a direction in the trader's head but not in arithmetic, and
        // colouring it green would be a claim the data does not make.
        direction: delta === null || delta === 0 ? "" : (delta > 0 ? "up" : "down"),
      };
    }
    return { ...entry, changes };
  });
}

/** "+2.7" / "-7" / "0.8 -> 3.5" - what the cell shows under its new value. */
export function differenceLabel(change) {
  if (!change || typeof change !== "object") return "";
  if (change.delta !== null && change.delta !== undefined && Number.isFinite(change.delta)) {
    if (change.delta === 0) return "";
    const size = Math.abs(change.delta);
    // Match the board's own precision: RVOL prints one decimal, Skittles are
    // whole numbers. Printing 2.7000000000000002 would look like a bug.
    const text = Number.isInteger(change.delta) ? String(size) : size.toFixed(1);
    return `${change.delta > 0 ? "+" : "\u2212"}${text}`;
  }
  const from = change.from === null || change.from === undefined || change.from === "" ? "\u2014" : String(change.from);
  const to = change.to === null || change.to === undefined || change.to === "" ? "\u2014" : String(change.to);
  if (from === to) return "";
  return `${from} \u2192 ${to}`;
}
