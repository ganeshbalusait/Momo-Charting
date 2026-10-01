import { Fragment, createContext, memo, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { TOS_LINK_GROUPS, sendTickerToCharts, tosLinkGroup } from "./tosLinkGroups.js";

import MomxTickerCard from "./MomxTickerCard.jsx";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  ChevronDown,
  Columns3,
  ExternalLink,
  Newspaper,
  RefreshCw,
  Search,
  Settings,
  SlidersHorizontal,
  Star,
  Zap,
} from "lucide-react";

import { etParts, isTradingDay, tapeState } from "./marketSession.js";
import {
  boardSymbols,
  feedAge,
  groupFeedBySymbol,
  mergeStoredNews,
  sentimentTone,
  sourceHealthLine,
  MOMX_NEWS_FRESH_MS as NEWSFEED_FRESH_MS,
} from "./momxNewsFeed.js";

import {
  formatJumpInput,
  jumpOffset,
  jumpTargetInEntries,
  pageStartClock,
  parseJumpTime,
  resolveJump,
  sessionLine,
  sessionStart,
} from "./momxHistoryJump.js";

import {
  DEFAULT_HISTORY_QUERY,
  HISTORY_QUERY_SECTIONS,
  HISTORY_QUERY_SECTION_BY_KEY,
  buildCondition,
  describeHistoryQuery,
  groupResultsByDay,
  historyQueryParams,
  historyQueryProblem,
  selectedStates,
  summariseResults,
} from "./momxHistoryQuery.js";

import {
  DEFAULT_MOMX_FILTERS,
  FILTER_RVOL_TIMEFRAMES,
  FILTER_SKITTLES_TIMEFRAMES,
  FILTER_SQZ_TIMEFRAMES,
  SKITTLES_STATE_BG,
  SQZ_STATE_BG,
  mirroredState,
  coerceThreshold,
  countPassing,
  describeFilters,
  describeMembership,
  filterRows,
  filteringGroups,
  gatesActive,
  groupPassCounts,
  isStarred,
  orderByDaily2,
  pushStarredToTop,
  readStoredFilters,
  STRATEGY_LABELS,
  strategyRules,
  strategyOf,
  strategyTags,
  bestSetupRanks,
  MOMX_NEW_SETUPS,
  freshAlert,
  topMode,
  whyNothingPasses,
  writeStoredFilters,
} from "./momxFilters.js";

import MomxGradeWhy from "./MomxGradeWhy.jsx";
import MomxColumnManager from "./MomxColumnManager.jsx";
import MomxColumnHeaderMenu, { useColumnHeaderControls, usePinnedColumns } from "./MomxColumnHeaderMenu.jsx";
import {
  applyLayout,
  columnGroupSpans,
  defaultLayout,
  effectiveSort,
  nextSortState,
  readStoredLayout,
  writeStoredLayout,
} from "./momxColumnLayout.js";
import {
  chartLabel,
  freshSortValue,
  freshText,
  gradeAgeText,
  momentumTone,
  setupSortValue,
  setupText,
  setupParts,
  setupTone,
} from "./momxGrade.js";

import {
  MOMX_DEFAULT_SORT,
  THINKSCRIPT_COLORS,
  firesOf,
  boardCacheEntry,
  capMomentumEvents,
  cellStyle,
  hlBlockStyle,
  isMutedCell,
  hasPaintedBackground,
  cellAgeClock,
  cellAgeLabel,
  cellIsLiveSpike,
  rvolSaturationNote,
  rvolNotLiveReason,
  cellAgeIsFresh,
  sectorRollup,
  sectorsForDirection,
  SECTOR_MOVE_PCT,
  decideBoardView,
  filterByIndustries,
  formatPctChange,
  formatRvol,
  formatSkittles,
  industryColor,
  industryCounts,
  matchedSinceLabel,
  momentumAgeBucket,
  momentumChipLabel,
  momentumEventTime,
  newMatchSymbols,
  newsOf,
  newsTimeLabel,
  quoteTrendBars,
  readBoardCache,
  sortRows,
  liveHighLowCell,
  livePctChange,
  sparklinePath,
  writeBoardCache,
  newsBadgeTitle,
  newsColumnCell,
  earningsMapFrom,
  eventColumnCell,
  optionsGateNote,
  typedOptionsSentence,
} from "./momxCells.js";
import {
  MOMX_HISTORY_DEFAULT_SORT,
  MOMX_HISTORY_RETENTION_DAYS,
  annotateChanges,
  dayLabel,
  differenceLabel,
  etDayIso,
  flattenHistoryRows,
  groupRowsByDay,
  historyColumns,
  historyRequest,
  boardCacheKey,
  liveColumns,
  resolveDayNav,
  snapshotTimeLabel,
  sortHistoryGroups,
  sortHistoryRows,
} from "./momxHistory.js";

// MomX Scanner board - the trader's MomoX/MomoScan window, replayed.
//
// This panel lives in its own file on purpose: App.jsx is ~33k lines and every
// panel added to it costs the whole app a re-parse. Nothing here derives a
// colour for a VALUE CELL: the backend resolves every thinkScript
// AssignBackgroundColor / AssignValueColor rule and ships { value, bg, fg },
// and momxCells.js turns a colour NAME into CSS. If a cell is the wrong colour
// the bug is in the thinkScript port, not in this file. (The industry chip
// palette is the one deliberate exception - see momxCells.js - and it never
// touches a value cell.)
//
// POLLING: 15s, and not one millisecond faster. On 2026-08-22 a 5s poll on a
// sibling scanner was measured as the single largest CPU consumer in the whole
// backend - it inflated a 408-byte /api/auth/status from an 11ms median to a
// 5.7s p90. 15s against THIS endpoint is different in kind, not just degree:
// the MomX board is served by momx_worker.py on :3010 - its own IDLE-priority
// process, not api_server - and a GET returns the warmer's cached ~100KB
// snapshot without triggering any rebuild, so a faster poll costs the backend
// nothing. The interval is also held off entirely while the tab is hidden, and
// an in-flight request is never stacked on by the next tick (do NOT remove
// that drop/queue logic, and do NOT go below 15s).
const MOMX_POLL_MS = 15000;

//: A pop-out board he is NOT looking at - anything behind the front window -
//: refreshes on this instead (his call, 2026-09-02). Still refreshing, just
//: not four boards a quarter-minute. The front window and the page itself
//: stay on MOMX_POLL_MS, and raising a window restores it immediately.
const MOMX_BACKGROUND_POLL_MS = 60000;

const MOMX_ENDPOINT = "/api/momx-scanner";
const MOMX_LISTS_ENDPOINT = "/api/momx-scanner/lists";
const MOMX_UNIVERSE_ENDPOINT = "/api/momx-scanner/universe";
const MOMX_UNIVERSE_RESET_ENDPOINT = "/api/momx-scanner/universe/reset";
const MOMX_UNIVERSE_ADD_ENDPOINT = "/api/momx-scanner/universe/add";
const MOMX_LIST_CREATE_ENDPOINT = "/api/momx-scanner/lists/create";
const MOMX_LIST_RENAME_ENDPOINT = "/api/momx-scanner/lists/rename";
const MOMX_LIST_DELETE_ENDPOINT = "/api/momx-scanner/lists/delete";
// Force-rebuild for the refresh/SCAN buttons: marks the list due on the
// worker's warmer instead of re-reading the cached board. Older workers do not
// have this route; a 404 (or network error) falls back to a plain cached-board
// reload so the buttons never become a no-op on version skew.
const MOMX_REBUILD_ENDPOINT = "/api/momx-scanner/rebuild";
// TOS checkbox (2026-09-30): which data the scanner reads - "tos" (Schwab
// price history only, rate-paced) or "alpaca" (SIP/BOATS/IEX tapes). The
// switch is GLOBAL on the worker (every viewer), so api_server lets only an
// admin POST it.
const MOMX_DATA_SOURCE_ENDPOINT = "/api/momx-scanner/data-source";
const MOMX_TOS_TOOLTIP =
  "TOS: scan only on your TOS (Schwab) data - matches the TOS scanner. The full list refreshes every few minutes because Schwab limits requests; the top rows stay live. Untick for Alpaca data: the full list refreshes about every minute, but volume and prices can differ from TOS.";
// NEWS store (2026-09-26): api_server keeps headlines scraped from feeds where
// the PUBLISHER tagged the ticker (Yahoo, Alpaca/Benzinga, Benzinga, Finviz,
// Nasdaq, SEC EDGAR). `latest` is a DB read for the board's tickers; `refresh`
// starts ONE background scrape and returns at once, so the tab polls `latest`
// until `refreshing` clears. Neither touches the scanner worker.
const MOMX_NEWS_LATEST_ENDPOINT = "/api/news-feed/latest";
const MOMX_NEWS_REFRESH_ENDPOINT = "/api/news-feed/refresh";
const MOMX_NEWS_REFRESH_POLL_MS = 3000;
const MOMX_NEWS_REFRESH_WAIT_MS = 150000;
const MOMX_NEWS_STORE_POLL_MS = 5 * 60 * 1000;
// Every ticker costs one request per source, so a refresh reads the FIRST N
// tickers in board order (ranked). Mirrors the server cap
// (NEWS_MOMX_REFRESH_MAX_SYMBOLS) so the list's "reading N tickers" is honest.
const MOMX_NEWS_REFRESH_MAX_SYMBOLS = 60;
// FASTLANE: real-time quotes for the MATCHED rows only, from the Schwab
// consolidated stream the charts already run (the "TOS api key" - trader,
// 2026-08-30). The broad scan stays on bars; this is the live layer that makes
// a match's price tick second-by-second, which for options momentum is the
// part that matters. 2.5s while the stream is serving; a lazy 30s check when
// it is not (weekend/overnight), so a closed market costs almost nothing.
const MOMX_LIVE_QUOTES_ENDPOINT = "/api/momx-live-quotes";
// The LIVE ⚡ (api_server _live_bolt_loop + momx/live_bolt.py): advanced every
// second on the Schwab stream; polled every second, with ?full=1 (the H/L ⚡
// momentum of every name, for the sort) every tenth poll.
const MOMX_LIVE_BOLTS_ENDPOINT = "/api/momx-live-bolts";
const BOLT_VIEW_KEY = "momx-bolt-view";
const HL_SORT_KEY = "momx-hl-bolt-sort";
const MOMX_LIVE_QUOTES_MS = 2500;
const MOMX_LIVE_QUOTES_IDLE_MS = 30000;
const EMPTY_OBJECT = Object.freeze({});
// After a forced rebuild, the board is re-fetched every 3s until its
// generatedAt changes; a watchlist build takes ~31-39s, so give up (quietly -
// spinner stops, rows stay) after 45s and let the normal poll pick it up.
const MOMX_REBUILD_POLL_MS = 3000;
const MOMX_REBUILD_WAIT_MS = 45000;

// Momentum event feed: "what just changed", so a NEW match is visible within
// seconds instead of at the next board rebuild. May simply not exist yet on a
// deployment - every failure is silent and the strip just does not render.
const MOMX_MOMENTUM_ENDPOINT = "/api/momx-scanner/momentum";
// /momentum returns a few KB of events and triggers no board rebuild, so 15s
// is safe here for the same reason it is now safe on MOMX_POLL_MS above
// (isolated IDLE worker, cached payload, nothing rebuilt on a GET). The
// 2026-08-22 incident - a 5s poll took /api/auth/status from an 11ms median to
// a 5.7s p90 - is why NEITHER interval may ever go below 15s.
const MOMX_MOMENTUM_POLL_MS = 15000;

// Scanner grade track record (the line under the letter in the "why" panel).
// Rebuilt by the worker once a night after 16:15 ET, so 30 minutes is plenty;
// a failure keeps the last good copy (or null -> "track record unavailable").
const MOMX_GRADE_RECORD_ENDPOINT = "/api/momx-scanner/grade-record";
const MOMX_GRADE_RECORD_POLL_MS = 30 * 60 * 1000;

// Which watchlist tab the trader had open, remembered across reloads. Reads and
// writes are wrapped because localStorage throws outright in some privacy
// modes, and a storage exception must not take the board down with it.
const MOMX_LIST_STORAGE_KEY = "momx.scanner.activeList";
// BULL or BEAR (spec 2026-09-24), remembered per device like the tab.
const MOMX_DIRECTION_STORAGE_KEY = "momx.scanner.direction";
// Whether that tab was the LIVE board or its HISTORY twin, same wrapping
// rules: together the two keys re-seat all four tabs across a reload.
const MOMX_VIEW_STORAGE_KEY = "momx.scanner.activeView";

// Quote Trend mini-histogram box, in CSS px.
const QUOTE_TREND_WIDTH = 58;
const QUOTE_TREND_HEIGHT = 13;
// The 1D sparkline. It now owns a column of its own under the CHART header; it
// used to be drawn faintly behind the % figure, where it was unreadable.
const SPARKLINE_WIDTH = 52;
const SPARKLINE_HEIGHT = 15;

// A stable empty array: returning a fresh [] from a memo on every poll would
// hand React a new identity each tick, which is the documented trigger for this
// app's "Page Unresponsive" bug class.
const EMPTY_ROWS = Object.freeze([]);

// The board the trader last saw, per list, surviving a tab-switch remount.
// React resets component state to its initializer on unmount, so `board` came
// back null and the panel showed "Building this watchlist from live bars..."
// on every return - blank for however long the rebuild took, which during the
// 2026-08-28 keeper storm was minutes. This Map lives outside the component, so
// a returning panel paints its last rows instantly and refreshes behind them.
// Keyed by list name. It is the HOT layer only; the durable copy lives in
// localStorage (readBoardCache/writeBoardCache), which is what lets a full page
// RELOAD or a worker restart still show the last rows instead of blanking - the
// trader's actual complaint on 2026-08-28 ("anything refresh... just show the
// tickers, do not make it blank").
const momxBoardCache = new Map();

// Read a list's last board: the hot in-memory copy first, then the durable
// localStorage mirror (which survives a reload / worker restart). A hit from
// disk is promoted into the hot Map so the next read is free.
function readCachedBoard(list) {
  const hot = momxBoardCache.get(list);
  if (hot) return hot;
  const stored = readBoardCache(list);
  if (stored) momxBoardCache.set(list, stored);
  return stored || null;
}

function hasCachedBoard(list) {
  if (momxBoardCache.has(list)) return true;
  return readBoardCache(list) !== null;
}

// Store a board in BOTH layers, but ONLY when it actually has rows. A warming,
// stale, or empty payload must never overwrite good tickers already cached -
// that is the governing rule (stale rows always beat a blank board), and it is
// why a worker restart's empty answer can no longer wipe what the trader sees.
function writeCachedBoard(list, payload) {
  if (!boardCacheEntry(payload)) return;
  momxBoardCache.set(list, payload);
  writeBoardCache(list, payload);
}
// Same reasoning for "no industry filter": a fresh Set per render would
// invalidate the visibleRows memo on every render, at 355 rows.
const EMPTY_SELECTION = new Set();
// One shared empty Set for "nothing is starred", so the memo below returns an
// identical reference between builds and never re-renders the whole board.
const EMPTY_STARS = new Set();

// Column order is the trader's window, left to right. The RVOL band is
// deliberately SPLIT by the High/Low column and the SKIT band by Quote Trend -
// that is how the TOS watchlist is laid out, so the group header emits two RVOL
// spans and two SKIT spans rather than reordering the columns to look tidier.
// Labels are the SHORT forms his window prints ("% Chg", "H/L", "5", "W",
// "Mo"); the keys stay on the payload contract's names.
const MOMX_COLUMNS = [
  { key: "industry", label: "Industry", kind: "industry", sortable: true },
  { key: "symbol", label: "Symbol", kind: "symbol", sortable: true },
  // Scanner grade (experimental, 2026-09-22): grade + momentum + pattern, and
  // what changed in the last 15 minutes. Display helpers live in momxGrade.js.
  // "Setup", not "A/A+ Setup": the header text set the column's width once the
  // momentum words left the cell (2026-09-24, his "why we need this much space").
  { key: "setup", label: "Setup", kind: "setup", sortable: true },
  { key: "fresh", label: "Fresh", kind: "fresh", sortable: true },
  { key: "pctChange", label: "% Chg", kind: "pct", sortable: true },
  // News + Event, as MomoX prints them (2026-09-25): the headline's age with a
  // ✦ when the AI reader judged it, and the next earnings date.
  { key: "news", label: "News", kind: "news", sortable: true },
  { key: "event", label: "Event", kind: "event", sortable: true },

  // CHART / 1D: the day's shape, as its own column.
  { key: "sparkline", label: "1D", group: "CHART", kind: "chart", sortable: false },

  { key: "rvol.2h", label: "2h", group: "RVOL", kind: "cell", section: "rvol", tf: "2h", format: formatRvol, sortable: true },
  { key: "rvol.4h", label: "4h", group: "RVOL", kind: "cell", section: "rvol", tf: "4h", format: formatRvol, sortable: true },
  { key: "rvol.D", label: "D", group: "RVOL", kind: "cell", section: "rvol", tf: "D", format: formatRvol, sortable: true },

  // TOS prints no digits here -- "High/Low Graph" is a drawn block. kind:"bar"
  // renders the 0-100 range position as a fill; the number stays on hover.
  { key: "highLow", label: "H/L", kind: "bar", field: "highLow", sortable: true },

  { key: "rvol.5m", label: "5", group: "RVOL", kind: "cell", section: "rvol", tf: "5m", format: formatRvol, sortable: true },
  { key: "rvol.15m", label: "15", group: "RVOL", kind: "cell", section: "rvol", tf: "15m", format: formatRvol, sortable: true },
  { key: "rvol.30m", label: "30", group: "RVOL", kind: "cell", section: "rvol", tf: "30m", format: formatRvol, sortable: true },
  { key: "rvol.1h", label: "1h", group: "RVOL", kind: "cell", section: "rvol", tf: "1h", format: formatRvol, sortable: true },

  // COLOR is a separator, not data: a hairline solid column that breaks the
  // RVOL block away from the SQZ block. It carries no text by design.
  { key: "color", label: "COLOR", kind: "color", field: "color", sortable: false },

  { key: "sqz.2h", label: "2h", group: "SQZ", kind: "cell", section: "sqz", tf: "2h", sortable: true },
  { key: "sqz.4h", label: "4h", group: "SQZ", kind: "cell", section: "sqz", tf: "4h", sortable: true },
  { key: "sqz.D", label: "D", group: "SQZ", kind: "cell", section: "sqz", tf: "D", sortable: true },
  { key: "sqz.Wk", label: "W", group: "SQZ", kind: "cell", section: "sqz", tf: "Wk", sortable: true },

  { key: "skittles.2h", label: "2h", group: "SKIT", kind: "cell", section: "skittles", tf: "2h", format: formatSkittles, sortable: true },
  { key: "skittles.4h", label: "4h", group: "SKIT", kind: "cell", section: "skittles", tf: "4h", format: formatSkittles, sortable: true },

  { key: "quoteTrend", label: "Quote", kind: "quoteTrend", sortable: true },

  { key: "skittles.D", label: "D", group: "SKIT", kind: "cell", section: "skittles", tf: "D", format: formatSkittles, sortable: true },
  { key: "skittles.2D", label: "2D", group: "SKIT", kind: "cell", section: "skittles", tf: "2D", format: formatSkittles, sortable: true },
  { key: "skittles.3D", label: "3D", group: "SKIT", kind: "cell", section: "skittles", tf: "3D", format: formatSkittles, sortable: true },
  { key: "skittles.4D", label: "4D", group: "SKIT", kind: "cell", section: "skittles", tf: "4D", format: formatSkittles, sortable: true },
  { key: "skittles.Wk", label: "W", group: "SKIT", kind: "cell", section: "skittles", tf: "Wk", format: formatSkittles, sortable: true },
  { key: "skittles.M", label: "Mo", group: "SKIT", kind: "cell", section: "skittles", tf: "M", format: formatSkittles, sortable: true },
];

// The row's sector-rotation stamp, or null while the new sector marks are
// switched off (MOMX_NEW_SETUPS in momxFilters.js).
function rot(row) {
  return (MOMX_NEW_SETUPS && row && row.sectorRotation) || null;
}

// 2026-09-25 (his ask "make it HOT"): a sector the worker marks "moving now"
// (late) shows exactly like a hot one - one name, one flame. The data keeps
// the two apart (the 🔥#1 / #2 leader tag is still the hot rule only).
function isHotRow(row) {
  const r = rot(row);
  return Boolean(r && (r.hot || r.late));
}

// "Add HOT symbol in ticker also" (2026-09-25): a 🔥 before the symbol of the
// stocks listed in their HOT sector's box (its top 5 by % today) - 1-based
// rank, or 0 when the row is not one of them.
function hotLeaderRank(row) {
  const r = rot(row);
  if (!r || !(r.hot || r.late) || !Array.isArray(r.leaders)) return 0;
  const at = r.leaders.findIndex((leader) => leader && leader.symbol === (row && row.symbol));
  return at >= 0 ? at + 1 : 0;
}

// Which RVOL timeframe earned a sector leader its "▲vol" (momx/sectors.py
// unusual_volume: 15m / 30m / 1h RVOL >= 1.0) - the highest one, "1h" first.
function volTimeframe(row) {
  const cells = row && row.rvol ? row.rvol : null;
  if (!cells) return "";
  for (const tf of ["1h", "30m", "15m"]) {
    const value = cells[tf] && cells[tf].value;
    if (value !== null && value !== undefined && Number(value) >= 1.0) return tf;
  }
  return "";
}

const AUTO_CARDS_PREF_KEY = "momx-auto-cards-on";
const AUTO_CARDS_SEEN_KEY = "momx-auto-cards-seen-v1";

// SYMBOL -> next earnings ({date, daysUntil, timingCode, timing}) from the
// app's earnings calendar (/api/earnings-calendar). Module-level so the sort
// and the History rows can read it; the panel refreshes it and passes each
// live row its own entry as a prop, so the row memo still holds.
let EARNINGS_BY_SYMBOL = new Map();

// The News / Event cells, shared by the live board and the History table.
// onOpen (live board only): clicking the News cell opens the same popover as
// the newspaper icon - asked for 2026-09-27, the cell is the bigger target on
// the phone. The History table passes nothing and stays a plain cell.
function newsEventCell(column, row, earnings, className, nowMs, onOpen) {
  if (column.kind === "news") {
    const cell = newsColumnCell(row, nowMs);
    const openable = Boolean(cell && onOpen);
    return (
      <td
        key={column.key}
        data-col={column.key}
        className={className + " momx-newscol" + (cell && cell.fresh ? " is-fresh" : "") + (cell && cell.stored ? " is-stored" : "")
          // Positive flashes green, negative flashes red (text + sparkle, no
          // fill) - asked for 2026-09-27. Neutral gets no class: he wants
          // only the two colours. The hover leads with the word
          // (cell.title), so desktop reads it; the phone gets the same word
          // in the tap popover.
          + (cell && (cell.tone === "up" || cell.tone === "down") ? " is-tone-" + cell.tone : "")
          + (openable ? " is-openable" : "")}
        title={cell ? cell.title : undefined}
        role={openable ? "button" : undefined}
        tabIndex={openable ? 0 : undefined}
        aria-label={openable ? "Open news for " + row.symbol : undefined}
        onClick={openable ? (event) => { event.stopPropagation(); onOpen(); } : undefined}
        onKeyDown={openable ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); event.stopPropagation(); onOpen(); } } : undefined}
      >
        {cell ? cell.text : ""}
        {/* MomoX3-style sparkle on every row with a headline; colour = the AI
            verdict when judged, else the scraper's keyword sentiment. */}
        {cell ? <span className={"momx-newscol-ai is-" + cell.tone + (cell.aiJudged ? " is-judged" : "")} aria-hidden="true">✦</span> : null}
      </td>
    );
  }
  const cell = eventColumnCell(earnings);
  return (
    <td key={column.key} data-col={column.key} className={className + " momx-eventcol" + (cell && cell.soon ? " is-soon" : "")} title={cell ? cell.title : undefined}>
      {cell ? cell.text : ""}
      {cell && cell.icon ? <span className="momx-eventcol-icon">{cell.icon}</span> : null}
    </td>
  );
}

const MOMX_COLUMN_BY_KEY = new Map(MOMX_COLUMNS.map((column) => [column.key, column]));

// The keys the Columns manager orders and hides (momxColumnLayout.js). The
// Time columns are NOT in here: they have their own switch, and applyLayout
// keeps them right after the column they follow. Group header spans are no
// longer precomputed - they are derived per render from the APPLIED column
// list (columnGroupSpans), so any order the trader picks still spans right.
const MOMX_COLUMN_KEYS = MOMX_COLUMNS.map((column) => column.key);

// The HISTORY table's columns are the SAME array with one Time column spliced
// in directly after Symbol (momxHistory.js). Column parity with the live board
// is structural, not a promise - momxHistory.test.js asserts it against this
// file's MOMX_COLUMNS source.
const MOMX_HISTORY_COLUMNS = historyColumns(MOMX_COLUMNS);
// The LIVE board with its own Time column (matchedSince - when the ticker
// entered the scan), placed after Fresh. Both variants are precomputed; the
// trader's column layout is applied on top at render (applyLayout), and the
// group spans are derived from that SAME applied array the body renders,
// because a colSpan computed from a different column list is how a grouped
// header silently slips one cell out of alignment.
const MOMX_LIVE_COLUMNS = liveColumns(MOMX_COLUMNS);
// Sort lookup for the live board. Built from the LIVE list on purpose:
// MOMX_COLUMN_BY_KEY above has no Time column, so a click on the live Time
// header used to resolve to no column at all and sort nothing (every value
// null, rows left in symbol order) - found 2026-09-02 while giving the NEWS
// view a newest-first order.
const MOMX_LIVE_COLUMN_BY_KEY = new Map(MOMX_LIVE_COLUMNS.map((column) => [column.key, column]));
// The board's own default sort - High/Low descending (2026-08-28) - kept as a
// stable object so it can double as effectiveSort's fallback (below) without
// creating a fresh reference on every render. Distinct from momxCells'
// MOMX_DEFAULT_SORT, which is only sortRows' generic %chg fallback.
const MOMX_LIVE_DEFAULT_SORT = Object.freeze({ key: "highLow", direction: "desc" });
// The BEAR board opens the other way up: stocks nearest their N-bar LOW on
// top (his screenshot 2026-09-25 01:34 ET, "H/L should be like that").
const MOMX_BEAR_DEFAULT_SORT = Object.freeze({ key: "highLow", direction: "asc" });
// The NEWS view's own default - newest headline first.
const MOMX_NEWS_DEFAULT_SORT = Object.freeze({ key: "matchedSince", direction: "desc" });
// Show/hide, remembered. "if I want I will keep or I will hide it" - default
// ON, because he asked for the column.
const MOMX_TIME_COLUMN_STORAGE_KEY = "momx.scanner.showTime";
function readStoredShowTime() {
  try {
    return window.localStorage.getItem(MOMX_TIME_COLUMN_STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}
function writeStoredShowTime(on) {
  try {
    window.localStorage.setItem(MOMX_TIME_COLUMN_STORAGE_KEY, on ? "1" : "0");
  } catch {
    // Private mode. Losing the preference is survivable; throwing is not.
  }
}

// The industry chips collapse behind a button. Default CLOSED: the row cost
// two lines above the table and he filters by industry occasionally, not
// constantly. Remembered, for the same reason the Time column is.
const MOMX_INDUSTRY_PANEL_STORAGE_KEY = "momx.scanner.showIndustries";
function readStoredShowIndustries() {
  try {
    return window.localStorage.getItem(MOMX_INDUSTRY_PANEL_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}
function writeStoredShowIndustries(on) {
  try {
    window.localStorage.setItem(MOMX_INDUSTRY_PANEL_STORAGE_KEY, on ? "1" : "0");
  } catch {
    // Private mode. Losing the preference is survivable; throwing is not.
  }
}

// The sector cards (HOT / leaders) collapse behind their own arrow, 2026-09-26
// ("use v arrow to hide / extend"). Default OPEN - they are the 09:35-11:00
// context he reads; remembered like the industry chips.
const MOMX_SECTOR_PANEL_STORAGE_KEY = "momx.scanner.showSectors";
function readStoredShowSectors() {
  try {
    return window.localStorage.getItem(MOMX_SECTOR_PANEL_STORAGE_KEY) !== "0";
  } catch {
    return true;
  }
}
function writeStoredShowSectors(on) {
  try {
    window.localStorage.setItem(MOMX_SECTOR_PANEL_STORAGE_KEY, on ? "1" : "0");
  } catch {
    // Private mode: the cards simply open by default next time.
  }
}

// TOS-style colour link (2026-09-26, "like TOS - when I click the scanner
// ticker it takes me to the charting page"). The scanner carries one of the
// charts' 9 link colours; clicking a ticker opens Charts & OI with it in every
// chart of that colour (App.jsx listens - tosLinkGroups.sendTickerToCharts).
// Default Red = the first chart's colour. The main board and each detached /
// in-app window remember their own colour, as TOS gadgets do.
const MOMX_LINK_STORAGE_KEY = "momx.scanner.linkGroup";
function linkStorageKey(embedded, list) {
  return embedded && list ? MOMX_LINK_STORAGE_KEY + "." + list : MOMX_LINK_STORAGE_KEY;
}
function readStoredLinkGroup(key) {
  try {
    return tosLinkGroup(window.localStorage.getItem(key), 1).value;
  } catch {
    return 1;
  }
}
function writeStoredLinkGroup(key, group) {
  try {
    window.localStorage.setItem(key, String(group));
  } catch {
    // Private mode: Red again next time.
  }
}

// { openChart(symbol), group } for the rows. One context instead of a prop on
// every memoised row: openChart is ref-stable, so switching colour re-renders
// the ticker buttons only through the context, never the whole table per poll.
const MomxChartLinkContext = createContext(null);

// The main board's open in-app windows/cards, held for the page session so a
// trip to the charts (TOS-link click) does not close them. Memory only.
const SESSION_POPOUTS = { list: [] };

function MomxSymbolLink({ symbol, className }) {
  const link = useContext(MomxChartLinkContext);
  const text = symbol || "";
  if (!link || !text) return <span className={className}>{text}</span>;
  return (
    <button
      type="button"
      className={className + " momx-symbol-link"}
      style={{ "--momx-link-color": link.group.color }}
      onClick={(event) => {
        event.stopPropagation();
        link.openChart(text);
      }}
      title={"Open " + text + " on the " + link.group.name + " chart(s)"}
      data-testid="momx-symbol-link"
    >
      {text}
    </button>
  );
}

// Geometry of the pop-up scanner window: where he put it, how big he made
// it. Kept out of the component so the clamping is testable on its own and
// so a corrupt stored value can only ever degrade to "open it centred".
const MOMX_POPOUT_RECT_KEY = "momx.scanner.popoutRect";   // + "." + list name
const MOMX_POPOUT_MIN_W = 420;
const MOMX_POPOUT_MIN_H = 260;

function defaultPopoutRect(index = 0) {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(1500, Math.round(vw * 0.78)));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(1000, Math.round(vh * 0.72)));
  // Cascade, the way every window manager does it: a second window opened at
  // the same coordinates as the first is indistinguishable from no second
  // window at all. Wraps after five so it cannot walk off the screen.
  const step = 28 * (index % 5);
  return {
    x: Math.round((vw - w) / 2) + step,
    y: Math.round((vh - h) / 2) + step,
    w,
    h,
  };
}

/** Keep the window on screen and above the minimum size. A window dragged
 *  off the edge is a window he cannot get back without clearing storage. */
function clampPopoutRect(rect) {
  if (!rect) return defaultPopoutRect();
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(Number(rect.w) || 0, vw));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(Number(rect.h) || 0, vh));
  return {
    w,
    h,
    x: Math.max(0, Math.min(Number(rect.x) || 0, vw - w)),
    y: Math.max(0, Math.min(Number(rect.y) || 0, vh - h)),
  };
}

/** Geometry is remembered PER LIST, so the Mag7 window reopens where the
 *  Mag7 window was - not where the last window of any list happened to be. */
function popoutRectKey(list) {
  return MOMX_POPOUT_RECT_KEY + "." + String(list || "default");
}

//: Detached (real) windows are remembered separately from the in-app ones.
//: Screen coordinates, not viewport coordinates - sharing one key would have
//: each stomp the other with a number measured from a different origin.
const MOMX_DETACHED_RECT_KEY = "momx.scanner.detachedRect";

function readDetachedRect(list) {
  try {
    const raw = JSON.parse(
      window.localStorage.getItem(MOMX_DETACHED_RECT_KEY + "." + String(list || "default")) || "null",
    );
    if (!raw || ["x", "y", "w", "h"].some((k) => !Number.isFinite(Number(raw[k])))) return null;
    return raw;
  } catch {
    return null;
  }
}

function readPopoutRect(list) {
  try {
    const raw = JSON.parse(window.localStorage.getItem(popoutRectKey(list)) || "null");
    if (!raw || ["x", "y", "w", "h"].some((k) => !Number.isFinite(Number(raw[k])))) return null;
    return clampPopoutRect(raw);
  } catch {
    return null;   // absent, private mode, or a shape from an older build
  }
}

function writePopoutRect(list, rect) {
  try {
    if (rect) window.localStorage.setItem(popoutRectKey(list), JSON.stringify(rect));
  } catch {
    // Private mode. He loses the remembered position, not the window.
  }
}

// The tab names, remembered. They change about as often as he edits a
// watchlist, so painting the last known set immediately is right almost always
// -- and an EMPTY tab bar is wrong immediately. See loadLists for why this
// exists: a failed or warming /lists response used to delete the tabs.
const MOMX_LISTS_CACHE_KEY = "momx.scanner.lists";

function readCachedLists() {
  try {
    const raw = window.localStorage.getItem(MOMX_LISTS_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) return null;
    const items = parsed.filter(
      (item) => item && typeof item.name === "string" && item.name,
    );
    return items.length > 0 ? items : null;
  } catch {
    return null;   // absent, private mode, or a shape from an older build
  }
}

function writeCachedLists(items) {
  try {
    if (!Array.isArray(items) || items.length === 0) return;
    window.localStorage.setItem(
      MOMX_LISTS_CACHE_KEY,
      // Names and counts only. Anything richer would go stale silently and be
      // believed, which is the failure this cache is meant to avoid.
      JSON.stringify(items.map((item) => ({ name: item.name, count: item.count }))),
    );
  } catch {
    // Private mode. Losing the cache costs one round trip, not correctness.
  }
}

function cellFor(row, column) {
  if (!row || typeof row !== "object") return null;
  if (column.section) {
    const section = row[column.section];
    return section && typeof section === "object" ? section[column.tf] || null : null;
  }
  if (column.field) return row[column.field] || null;
  return null;
}

// What a header click sorts on. Cell columns sort on the cell's VALUE, never on
// its colour, so "sort by 4h squeeze" orders by the number TOS prints.
function columnSortValue(row, column, newsMode) {
  if (!row || typeof row !== "object" || !column) return null;
  // The two Time columns sort on the INSTANT, never on the printed label -
  // "04:20" vs "11:05" compares wrongly as text either side of noon.
  if (column.kind === "matchedSince") {
    // While NEWS is pressed the column shows the headline's time, so it must
    // sort on that instant too - or "newest first" would order by scan entry.
    if (newsMode) {
      const news = newsOf(row);
      return news ? news.atMs : null;
    }
    const stamp = Date.parse(row.matchedSince || "");
    return Number.isNaN(stamp) ? null : stamp;
  }
  // News: newest headline first on a descending sort. Event: soonest earnings
  // first on a descending sort (the value is -daysUntil).
  if (column.kind === "news") {
    const news = newsOf(row);
    return news ? news.atMs : null;
  }
  if (column.kind === "event") {
    const e = EARNINGS_BY_SYMBOL.get(row.symbol);
    return e ? -e.daysUntil : null;
  }
  if (column.kind === "cell") {
    const cell = cellFor(row, column);
    if (!cell || typeof cell !== "object") return null;
    return cell.value === undefined ? null : cell.value;
  }
  if (column.kind === "bar") {
    const cell = row[column.field];
    if (!cell || typeof cell !== "object") return null;
    return cell.value === undefined ? null : cell.value;
  }
  if (column.kind === "quoteTrend") {
    if (!Array.isArray(row.quoteTrend) || row.quoteTrend.length === 0) return null;
    return row.quoteTrend.reduce((total, cell) => {
      const value = Number(cell && typeof cell === "object" ? cell.value : cell);
      return Number.isFinite(value) ? total + value : total;
    }, 0);
  }
  // Setup: A+ first, then Building ahead of Holding/Fading/Quiet (the rank
  // setupSortValue returns is an integer), ties by % change - folded in as a
  // fraction under 0.1 so it can never cross an integer rank. Nothing to show
  // = null, which sorts last in both directions like any blank cell.
  if (column.kind === "setup") {
    const rank = setupSortValue(row);
    if (!(rank >= 0)) return null;
    const pct = Number(row.pctChange);
    const tie = Number.isFinite(pct) ? Math.max(-99, Math.min(99, pct)) / 1000 : 0;
    return rank + tie;
  }
  // Fresh: newest first on a descending sort (the value is -ageMinutes).
  if (column.kind === "fresh") {
    const value = freshSortValue(row);
    return Number.isFinite(value) ? value : null;
  }
  const raw = row[column.key];
  return raw === undefined ? null : raw;
}

// The script's colour, drawn as a rounded pill around the number rather than
// as a full-bleed block filling the table cell. Returns plain text when the
// script gave the cell no background, so only genuinely coloured cells get a
// shape and the dense grid does not turn into a wall of boxes.
function cellBody(cell, format) {
  const text = cellText(cell, format);
  if (text === "" || text === null || text === undefined) return text;
  const style = cellStyle(cell);
  const painted =
    style.backgroundColor && style.backgroundColor !== "transparent";
  if (!painted) return text;
  return (
    <span className="momx-pill" style={style}>
      {text}
    </span>
  );
}

function cellText(cell, format) {
  if (!cell || typeof cell !== "object") return "";
  // TOS hides a weak reading by painting the text the same black as its
  // background -- his RVOL ladder ends "else Color.BLACK" on a black bg for
  // anything at or below 0.5. Those cells are meant to read as EMPTY so the
  // eye lands only on the timeframes that are moving; he compared CVS side by
  // side and asked why ours printed a number in every column.
  //
  // Rendering nothing (rather than invisible text) also keeps the value out of
  // selection, copy and screen readers, which is what "empty" should mean.
  if (isMutedCell(cell)) return "";
  if (typeof format === "function") return format(cell.value);
  if (cell.value === null || cell.value === undefined) return "";
  return String(cell.value);
}

// Trading days are read in market time, so the "last updated" stamp is ET even
// when the trader is travelling.
// "Data as of ..." note - now a THREE-way verdict, not an age threshold.
//
// It used to say "newest bar over 30 minutes old = stale, paint it amber",
// which is true every evening and all weekend. On Saturday 2026-09-05 the
// trader read the resulting amber "DATA AS OF FRI 8:00 PM ET" chip and asked
// "it's scanner is not running?" - it was: verified live, the worker rebuilt
// and wrote its board cache at 13:29 ET while the payload reported generatedAt
// Sat 13:28 ET, tapeAsOf Fri 20:00 ET and zero errors. Friday 20:00 ET is
// simply the last bar of the week.
//
// A warning that fires on the normal case is not a warning. marketSession.js
// answers the question that actually matters - has the market had a CHANCE to
// produce a newer bar - so the chip can be quiet when the market is shut and
// still shout when the tape has genuinely stalled, including the case a plain
// age test misses (a tape stuck since Monday is only ~26h old on Tuesday
// night, yet a whole session behind).

export function tapeAsOfNote(iso, nowMs) {
  // ALWAYS returns a label when the stamp is readable - {text, tone, stale}.
  // It used to return null while the tape was fresh, so the note vanished the
  // moment things were healthy and the trader could not tell WHEN the data ran
  // ("how i know when the data ran?", 2026-08-31). UPDATED already says when
  // the SCAN built; this says how old the BARS are, which is the number that
  // decides whether a signal is actionable.
  //
  // `stale` is kept as a boolean because callers style on it, and it now means
  // "there is something wrong" rather than "the market is shut".
  if (!iso) return null;
  const stamp = new Date(iso);
  if (Number.isNaN(stamp.getTime())) return null;
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  const verdict = tapeState(stamp.getTime(), now);
  if (!verdict) return null;

  const clockOf = (options) => {
    try {
      return stamp.toLocaleString("en-US", { timeZone: "America/New_York", ...options });
    } catch {
      return null;
    }
  };
  const dayClock = clockOf({
    weekday: "short", hour: "numeric", minute: "2-digit", hour12: true,
  });

  if (verdict.state === "stale") {
    return {
      tone: "stale",
      stale: true,
      text: dayClock ? `Tape stale - last bar ${dayClock} ET` : "Tape stale",
    };
  }
  if (verdict.state === "closed") {
    // NEUTRAL. The market being shut is not a fault, and the header already
    // carries a MARKET CLOSED pill - this only adds which bar we stopped at.
    return {
      tone: "closed",
      stale: false,
      text: dayClock ? `Last bar ${dayClock} ET` : "Last bar of the session",
    };
  }
  const minutes = Math.floor(Math.max(0, verdict.ageMs) / 60000);
  const clock = clockOf({ hour: "2-digit", minute: "2-digit", hour12: false });
  return {
    tone: "live",
    stale: false,
    // "Data 11:05 ET (3m)" - the timestamp AND the age, because a stamp alone
    // still makes you do arithmetic against the wall clock.
    text: clock ? `Data ${clock} ET (${minutes}m)` : "Data is current",
  };
}

function formatUpdatedAt(iso) {
  if (!iso) return "never";
  const stamp = new Date(iso);
  if (Number.isNaN(stamp.getTime())) return "never";
  try {
    const clock = stamp.toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
    return clock + " ET";
  } catch {
    return stamp.toLocaleTimeString();
  }
}

function readStoredList() {
  try {
    const stored = window.localStorage.getItem(MOMX_LIST_STORAGE_KEY);
    return typeof stored === "string" && stored ? stored : null;
  } catch {
    return null;
  }
}

function writeStoredList(name) {
  try {
    if (typeof name === "string" && name) window.localStorage.setItem(MOMX_LIST_STORAGE_KEY, name);
  } catch {
    /* private browsing: the tab choice simply does not persist */
  }
}

function readStoredDirection() {
  try {
    return window.localStorage.getItem(MOMX_DIRECTION_STORAGE_KEY) === "bear" ? "bear" : "bull";
  } catch {
    return "bull";
  }
}

function writeStoredDirection(direction) {
  try {
    window.localStorage.setItem(MOMX_DIRECTION_STORAGE_KEY, direction === "bear" ? "bear" : "bull");
  } catch {
    /* private browsing: the switch simply does not persist */
  }
}

function readStoredView() {
  try {
    return window.localStorage.getItem(MOMX_VIEW_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function writeStoredView(view) {
  try {
    window.localStorage.setItem(MOMX_VIEW_STORAGE_KEY, view);
  } catch {
    /* private browsing: the view choice simply does not persist */
  }
}

function QuoteTrendCell({ cells }) {
  const bars = quoteTrendBars(cells, QUOTE_TREND_WIDTH, QUOTE_TREND_HEIGHT);
  if (bars.length === 0) return null;
  return (
    <svg
      className="momx-quote-trend"
      width={QUOTE_TREND_WIDTH}
      height={QUOTE_TREND_HEIGHT}
      viewBox={"0 0 " + QUOTE_TREND_WIDTH + " " + QUOTE_TREND_HEIGHT}
      aria-hidden="true"
      focusable="false"
    >
      {bars.map((bar, index) => {
        const style = cellStyle(bar.cell);
        // The bar takes the cell's background; if the backend left it unset the
        // foreground is the next authority, and only then a neutral grey. No
        // colour is invented from the value here.
        let fill = style.backgroundColor;
        if (fill === "transparent") fill = style.color === "inherit" ? "#5a5c68" : style.color;
        return <rect key={index} x={bar.x} y={bar.y} width={bar.w} height={bar.h} fill={fill} />;
      })}
    </svg>
  );
}

// The momentum strip: one horizontal row of event chips, newest on the left.
// Memoised so the 15s momentum poll re-renders THESE ~12 chips and nothing
// else - the 358-row table must never repaint on a strip tick (that is this
// app's documented "Page Unresponsive" bug class). Chip colours are UI chrome
// chosen in CSS, NOT thinkScript colours - never cite them as parity.
// The list picker: switch, create, rename, delete. Replaces the tab bar, which
// grew two buttons per list.
//
// PORTALED to <body> for the same reason the Momo settings sheet is: the
// panel's pinned toolbar and the table's sticky headers each form a stacking
// context that paints in DOM order, so an in-panel dropdown gets sliced by the
// header rows.
//
// Every destructive action is TWO TAPS, never window.confirm() - his phone
// in-app browser (WKWebView with no dialog delegate) resolves confirm() to
// false without drawing it, which is what made Set and Restore look dead on
// 2026-09-01.
function MomxListPicker({
  lists,
  activeList,
  maxLists,
  busy,
  error,
  anchorRef,
  onPick,
  onCreate,
  onRename,
  onDelete,
  onClose,
}) {
  const [newName, setNewName] = useState("");
  const [renaming, setRenaming] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [deleteArmed, setDeleteArmed] = useState("");
  // A DROPDOWN, not a centred modal (trader ask, 2026-09-02): it hangs off the
  // list button so the board stays readable behind it. It still PORTALS (see
  // the comment above - the sticky toolbar and header rows would slice an
  // in-panel menu) which means it cannot be positioned by CSS alone; these are
  // the button's live viewport coordinates. Null until measured, so the menu
  // is never painted at 0,0 for a frame.
  const [box, setBox] = useState(null);

  useLayoutEffect(() => {
    const place = () => {
      const anchor = anchorRef && anchorRef.current;
      if (!anchor || typeof anchor.getBoundingClientRect !== "function") {
        // No anchor to hang from (shouldn't happen) - centre it rather than
        // pin the menu to a corner where it looks broken.
        setBox({ centered: true });
        return;
      }
      const rect = anchor.getBoundingClientRect();
      const viewportW = window.innerWidth || 0;
      const viewportH = window.innerHeight || 0;
      const width = Math.min(380, Math.max(240, viewportW - 16));
      // Clamp INSIDE the viewport: on a phone the button sits near the left
      // edge, on a wide screen a right-hand list button would otherwise push
      // the menu off-screen.
      const left = Math.max(8, Math.min(rect.left, viewportW - width - 8));
      const below = viewportH - rect.bottom - 12;
      const above = rect.top - 12;
      // Flip above the button when there is more room there (a phone in
      // landscape, or the toolbar sitting low on a short window).
      const flip = below < 220 && above > below;
      setBox({
        left,
        width,
        top: flip ? undefined : rect.bottom + 6,
        bottom: flip ? viewportH - rect.top + 6 : undefined,
        maxHeight: Math.max(160, (flip ? above : below) - 6),
      });
    };
    place();
    // Track the button instead of vanishing: the toolbar is sticky, but the
    // page can still scroll or rotate under an open menu. `true` catches the
    // scroll on the .momx-scanner-view scroller, which does not bubble.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorRef]);

  // Escape closes the menu. The app-wide Escape handler deliberately ignores
  // key presses while .momx-picker-overlay is up (it would close a popout
  // window instead), so without this the menu had no keyboard exit at all.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const full = lists.length >= maxLists;
  const style =
    box && !box.centered
      ? { left: box.left, width: box.width, top: box.top, bottom: box.bottom, maxHeight: box.maxHeight }
      : undefined;

  return createPortal(
    <div className="momx-picker-overlay" onClick={onClose}>
      <div
        className={"momx-picker" + (box && !box.centered ? " is-anchored" : "")}
        role="dialog"
        aria-label="Watchlists"
        style={style}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-picker-head">
          <span>WATCHLISTS</span>
          <button type="button" onClick={onClose} aria-label="Close">
            x
          </button>
        </div>

        {error ? <p className="momx-picker-error">{error}</p> : null}

        <ul className="momx-picker-list">
          {lists.map((item) => {
            const isRenaming = renaming === item.name;
            const isArmed = deleteArmed === item.name;
            return (
              <li key={item.name} className={item.name === activeList ? "is-active" : ""}>
                {isRenaming ? (
                  <form
                    className="momx-picker-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      onRename(item.name, renameValue);
                      setRenaming("");
                    }}
                  >
                    <input
                      className="momx-find-input"
                      value={renameValue}
                      autoFocus
                      spellCheck={false}
                      maxLength={32}
                      onChange={(event) => setRenameValue(event.target.value)}
                      aria-label={"New name for " + item.name}
                    />
                    <button type="submit" className="momx-btn" disabled={busy}>
                      Save
                    </button>
                    <button type="button" className="momx-btn" onClick={() => setRenaming("")}>
                      Cancel
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="momx-picker-pick"
                      onClick={() => onPick(item.name)}
                    >
                      <span className="momx-picker-name">{item.name}</span>
                      {Number.isFinite(item.count) ? (
                        <span className="momx-tab-count">{item.count}</span>
                      ) : null}
                      {item.warming ? <span className="momx-tab-state">building</span> : null}
                    </button>
                    {/* Built-in lists cannot be renamed or deleted. The server
                        refuses both, but showing buttons that always fail is
                        its own bug - the flag comes from the server so the UI
                        never guesses from the name. */}
                    {item.builtIn ? (
                      <span className="momx-picker-builtin" title="Built-in list">
                        built-in
                      </span>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="momx-btn"
                          disabled={busy}
                          onClick={() => {
                            setRenaming(item.name);
                            setRenameValue(item.name);
                            setDeleteArmed("");
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          className={isArmed ? "momx-btn is-armed" : "momx-btn"}
                          disabled={busy}
                          onClick={() => {
                            if (!isArmed) {
                              setDeleteArmed(item.name);
                              return;
                            }
                            setDeleteArmed("");
                            onDelete(item.name);
                          }}
                          title={
                            isArmed
                              ? "Press again to delete " + item.name + " for good"
                              : "Delete " + item.name
                          }
                        >
                          {isArmed ? "Delete?" : "Delete"}
                        </button>
                      </>
                    )}
                  </>
                )}
              </li>
            );
          })}
        </ul>

        <form
          className="momx-picker-new"
          onSubmit={(event) => {
            event.preventDefault();
            if (!newName.trim() || full) return;
            onCreate(newName);
            setNewName("");
          }}
        >
          <input
            className="momx-find-input"
            value={newName}
            placeholder={full ? "List limit reached" : "New list name"}
            spellCheck={false}
            maxLength={32}
            disabled={busy || full}
            onChange={(event) => setNewName(event.target.value)}
            aria-label="New list name"
          />
          <button type="submit" className="momx-btn" disabled={busy || full || !newName.trim()}>
            Add list
          </button>
        </form>
        <p className="momx-picker-note">
          {full
            ? "You have the maximum of " +
              maxLists +
              " lists. Delete one to add another."
            : "A new list starts empty - switch to it, then use Add to put tickers in. More lists means each one refreshes less often."}
        </p>
      </div>
    </div>,
    document.body,
  );
}

const MomentumStrip = memo(function MomentumStrip({ events, overflow, nowMs, focusSymbol, onPick, onClear }) {
  if (!Array.isArray(events) || events.length === 0) return null;
  return (
    <div className="momx-momentum-strip" role="list" aria-label="Momentum events, newest first">
      {events.map((event) => {
        const label = momentumChipLabel(event);
        if (!label) return null;
        const bucket = momentumAgeBucket(event, nowMs);
        const active = focusSymbol === label.symbol;
        const classes = [
          "momx-mo-chip",
          "is-" + label.kind.replace("_", "-"),
          "is-" + bucket,
        ];
        if (active) classes.push("is-focus");
        const at = momentumEventTime(event);
        // The spike reads measure-first ("RVOL 5m 3.1 MPC"); the other kinds
        // read name-first ("NEW CRWD +4.2%", "LOST XYZ").
        const measureFirst = label.kind === "rvol_spike";
        return (
          <button
            key={label.kind + "-" + label.symbol + "-" + (at === null ? label.text : at)}
            type="button"
            role="listitem"
            className={classes.join(" ")}
            title={
              label.title +
              ". " +
              (active
                ? "Click again to show the whole board."
                : "Click to show only " + label.symbol + " in the table.")
            }
            aria-pressed={active}
            onClick={() => onPick(label.symbol)}
          >
            <span className="momx-mo-tag">{label.tag}</span>
            {measureFirst && label.detail !== "" ? (
              <span className="momx-mo-detail">{label.detail}</span>
            ) : null}
            <span className="momx-mo-symbol">{label.symbol}</span>
            {!measureFirst && label.detail !== "" ? (
              <span className="momx-mo-detail">{label.detail}</span>
            ) : null}
          </button>
        );
      })}
      {overflow > 0 ? <span className="momx-mo-more">+{overflow} more</span> : null}
      {typeof onClear === "function" ? (
        <button
          type="button"
          className="momx-chip-clear momx-mo-clear"
          title="Clear every momentum chip for this list. New events keep arriving."
          onClick={onClear}
        >
          clear
        </button>
      ) : null}
    </div>
  );
});

// One <tr>, memoised on the row object. With 355 symbols on screen a sort click
// or a chip toggle would otherwise re-render ~9,000 cells; because a poll
// replaces row objects only when the payload actually changes, memo turns those
// interactions into a reorder of untouched DOM subtrees. Everything this row
// needs is either on the row, a module constant, or a parent callback whose
// identity NEVER changes (onNewsToggle is useCallback([]), so the news popover
// opening or closing re-renders zero rows). The ONE exception is `isNew`, a
// boolean saying "this symbol arrived as a new_match in the last ~2 minutes":
// the parent derives it per row, so the Set the momentum poll rebuilds every
// 15s never reaches this component - only rows whose flag actually FLIPS
// re-render, and a strip tick touches zero rows.
// Momo Alert settings: per-timeframe arm + threshold, sound. The worker owns
// the config (persisted to disk); this popover is a thin editor - every
// change POSTs immediately, no save button to forget.
const MOMO_CONFIG_ENDPOINT = "/api/momx-scanner/momo-config";
const MOMO_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h"];

function MomoSettings({ onClose }) {
  const [config, setConfig] = useState(null);
  const [error, setError] = useState("");
  // null | "sending" | the server's verdict {ok, topic?, error?}. The test
  // push is the only part of setup that can PROVE the phone is wired up - a
  // typo'd topic otherwise fails silently forever - so the verdict renders
  // verbatim, success or failure.
  const [testPush, setTestPush] = useState(null);
  // Threshold edits draft locally and commit on blur/Enter. Pushing per
  // keystroke let the worker's validated echo fight the keystroke: deleting
  // "3" -> "" -> Number("") = 0 -> server rejects, echoes 3 back -> the input
  // snaps back mid-edit ("3 is not deleting"). Raw strings live here until
  // the user commits.
  const [drafts, setDrafts] = useState({});

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(MOMO_CONFIG_ENDPOINT, { credentials: "include" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const data = await res.json();
        if (!cancelled) setConfig(data);
      } catch {
        if (!cancelled) setError("Momo Alert needs the updated scanner worker.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const push = async (next) => {
    setConfig(next); // optimistic; the worker echoes the validated config back
    try {
      const res = await fetch(MOMO_CONFIG_ENDPOINT, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(next),
      });
      if (res.ok) setConfig(await res.json());
    } catch {
      // keep the optimistic state; next open re-reads the truth
    }
  };

  // Commit a threshold draft: mirror the server rule (finite, clamped 1..50)
  // client-side so what you see is what saves; anything unparseable just
  // reverts the display to the saved value with no POST.
  const commitThreshold = (name) => {
    const draft = drafts[name];
    if (draft === undefined) return;
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    const parsed = Number(draft);
    if (draft.trim() === "" || !Number.isFinite(parsed)) return;
    const clamped = Math.min(50, Math.max(1, parsed));
    setFrame(name, { threshold: clamped });
  };

  const setFrame = (name, patch) => {
    if (!config) return;
    push({
      ...config,
      timeframes: {
        ...config.timeframes,
        [name]: { ...config.timeframes[name], ...patch },
      },
    });
  };

  const cooldown = config ? Math.round(config.cooldownMinutes) : 15;
  // PORTALED to <body>: inside the panel, the pinned toolbar and the table's
  // sticky headers each form stacking contexts that paint in DOM order, so an
  // in-panel popover gets sliced by the header rows (seen on the phone,
  // 2026-08-30). A body-level fixed overlay escapes every panel context.
  return createPortal(
    <div className="momo-settings-overlay" onClick={onClose}>
      <div
        className="momo-settings"
        role="dialog"
        aria-label="Momo Alert settings"
        onClick={(event) => event.stopPropagation()}
      >
      <div className="momo-settings-head">
        <span>MOMO ALERT</span>
        <button type="button" onClick={onClose} aria-label="Close">x</button>
      </div>
      {error ? <p className="momo-settings-error">{error}</p> : null}
      {!config && !error ? <p className="momo-settings-note">Loading...</p> : null}
      {config ? (
        <>
          <p className="momo-settings-note">
            Fires when an armed timeframe RVOL crosses its bar while the scan
            passes. One alert per ticker per {cooldown} min.
          </p>
          {MOMO_TIMEFRAMES.map((name) => {
            const frame = (config.timeframes || {})[name] || {};
            return (
              <label key={name} className="momo-settings-row">
                <input
                  type="checkbox"
                  checked={Boolean(frame.armed)}
                  onChange={(event) => setFrame(name, { armed: event.target.checked })}
                />
                <span className="momo-settings-tf">{name}</span>
                <span className="momo-settings-x">RVOL</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min="1"
                  max="50"
                  step="0.5"
                  value={
                    drafts[name] !== undefined
                      ? drafts[name]
                      : String(frame.threshold != null ? frame.threshold : 3)
                  }
                  onChange={(event) =>
                    setDrafts((prev) => ({ ...prev, [name]: event.target.value }))
                  }
                  onBlur={() => commitThreshold(name)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                  }}
                />
                <span className="momo-settings-x">x</span>
              </label>
            );
          })}
          <label className="momo-settings-row">
            <input
              type="checkbox"
              checked={Boolean(config.sound)}
              onChange={(event) => push({ ...config, sound: event.target.checked })}
            />
            <span className="momo-settings-tf">Sound</span>
          </label>
          <div className="momo-settings-push">
            <span className="momo-settings-x">Phone push (ntfy topic)</span>
            <div className="momo-settings-push-row">
              <input
                type="text"
                placeholder="off"
                value={config.ntfyTopic || ""}
                onChange={(event) => push({ ...config, ntfyTopic: event.target.value })}
              />
              {/* One tap makes an unguessable channel name, so no user has to
                  invent one (the name IS the password). Clearing the field
                  still turns push off; nothing auto-refills it. */}
              <button
                type="button"
                onClick={() => {
                  const bytes = new Uint8Array(6);
                  window.crypto.getRandomValues(bytes);
                  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
                  push({ ...config, ntfyTopic: "agx-momo-" + hex });
                }}
              >
                Generate
              </button>
              <button
                type="button"
                disabled={!String(config.ntfyTopic || "").trim() || testPush === "sending"}
                onClick={async () => {
                  setTestPush("sending");
                  // The server tests the SAVED topic. The field saves per
                  // keystroke and on Generate, but those saves are async - a
                  // user pressing Generate then Send test in one breath must
                  // not be told "press Generate first". Re-save, then test.
                  try { await push(config); } catch { /* verdict will say */ }
                  try {
                    const res = await fetch("/api/momo-alert/test-push", {
                      method: "POST",
                      credentials: "include",
                    });
                    setTestPush(await res.json());
                  } catch {
                    setTestPush({ ok: false, error: "Could not reach the server." });
                  }
                }}
              >
                {testPush === "sending" ? "Sending…" : "Send test"}
              </button>
            </div>
            <span className="momo-settings-hint">
              Install the free ntfy app, subscribe to this exact name, then press
              Send test - your phone should buzz.
            </span>
            {testPush && testPush !== "sending" ? (
              <span className={"momo-test-result " + (testPush.ok ? "is-ok" : "is-fail")}>
                {testPush.ok
                  ? "Sent - check your phone. If nothing arrived, the name in the ntfy app does not match exactly."
                  : String(testPush.error || "The test did not go through.")}
              </span>
            ) : null}
          </div>
        </>
      ) : null}
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------------------
// FILTERS - the trader's second gate, laid out like a thinkorswim scan
// ---------------------------------------------------------------------------
// "Same as TOS scan" (2026-09-05): one block holding groups that each carry
// their own ANY-of / ALL-of header. The block's own top line was a FIXED "ANY
// of the following" label until 2026-09-22 - he had asked for exactly that
// ("Only ANY OF THE FOLLOWING should be in filter"). On 2026-09-22 he asked
// for the choice: "when i select "Rvol with any timeframes " and "Skittles
// with any timeframes" , i choose "all of the follwoing" then i see the
// scanner list only Rvol and skittles matches?". So the top line is now a
// dropdown - ANY (the default, what every board already was) or ALL - and a
// group he switches off is left out under both. When the result is 0 tickers
// the panel names the group that passed nobody (whyNothingPasses), because
// under ALL one quiet group empties the board by design.
//
// The live count sits in the panel head and moves as boxes are ticked. That is
// the whole point of the panel: every question that was previously answered by
// asking him ("any of 1h/2h/4h or all three?", "2.5 or 3.0?") is now a box he
// sets while watching what it does to the ticker count.
//
// PORTALED to <body> for the same reason MomoSettings is - the pinned toolbar
// and the sticky table headers each form a stacking context that would slice
// an in-panel dialog (seen on the phone, 2026-08-30).
function MomxFiltersPanel({ config, count, total, counts, onChange, onReset, onClose, direction = "bull" }) {
  // The BULL / BEAR switch lives on the board; this popover only reads it.
  // Before it was passed in, opening FILTERS threw "direction is not defined"
  // and took the whole workspace down (2026-09-25).
  const bear = direction === "bear";
  const setGroup = (name, patch) =>
    onChange({ ...config, [name]: { ...config[name], ...patch } });

  // Which groups actually decide anything. A group can be switched ON and
  // still not filter (Skittles on "push to top"), and until 2026-09-05 nothing
  // said so - he switched RVOL and SQZ off, ticked Skittles D, and got every
  // ticker with a footer claiming Skittles was the rule.
  const active = filteringGroups(config);
  // The scanner-grade gates filter on their own, so "nothing is filtering" is
  // only true when no group AND no gate takes part.
  const nothingFilters = active.size === 0 && !gatesActive(config);
  const setGate = (name, value) => onChange({ ...config, [name]: value });
  const gateSelect = (name, label, options) => (
    <label className="momx-filters-row momx-filters-gate">
      <span className="momx-filters-tf">{label}</span>
      <select
        className="momx-filters-mode"
        value={config[name] || "any"}
        onChange={(event) => setGate(name, event.target.value)}
        aria-label={label}
      >
        {options.map((entry) =>
          // { group, options } renders a labelled section (the Setup list was
          // one long list and confusing - his words, 2026-09-25).
          entry && entry.group ? (
            <optgroup key={entry.group} label={entry.group}>
              {entry.options.map(([value, text]) => (
                <option key={value} value={value}>{text}</option>
              ))}
            </optgroup>
          ) : (
            <option key={entry[0]} value={entry[0]}>{entry[1]}</option>
          ))}
      </select>
    </label>
  );
  // The top-level ANY/ALL, and - when the filter leaves 0 tickers - which
  // group passed nobody. `counts` may be null (the board only takes it while
  // this panel is open); whyNothingPasses reads that as nothing to explain.
  const mode = topMode(config);
  const whyEmpty = whyNothingPasses(config, counts);
  // What the filter is really using, said under the top dropdown - and a
  // group whose own box is unticked is greyed with OFF beside its name. On
  // 2026-09-22 his phone had RVOL's box unticked with 1h/2h/4h still ticked
  // inside it; it looked ON, ALL returned 15 tickers without RVOL, and he
  // reported ALL as wrong. The filter was right; the panel was misleading.
  const membership = describeMembership(config);
  // A strategy picked in A/A+ Setup is the whole filter: the groups stay
  // editable (his tuning is kept for when he goes back) but are greyed out.
  const strategy = strategyOf(config);
  const groupOff = (name) => !(config[name] && config[name].on);
  const groupClass = (name) =>
    "momx-filters-group" + (groupOff(name) || strategy ? " is-off" : "");
  const offTag = (name) => (groupOff(name) ? (
    <span
      className="momx-filters-offtag"
      data-testid={"momx-filters-off-" + name}
      title={"Switched off - " + name.toUpperCase() + " is not part of the filter. Tick its box to use it."}
    >
      OFF
    </span>
  ) : null);

  // "N pass" beside each group header. Shown whether or not the group is
  // filtering, so the number tells him what switching it on WOULD do - the
  // answer to "why am I still seeing everything" is Skittles reading 0 while
  // the board shows 8.
  const groupCount = (name) => (
    <span
      className={"momx-filters-gcount" + (active.has(name) ? " is-live" : "")}
      title={
        active.has(name)
          ? (counts ? counts[name] : 0) + " of " + (counts ? counts.total : 0) + " tickers pass this group"
          : (counts ? counts[name] : 0) + " of " + (counts ? counts.total : 0)
            + " would pass - this group is not filtering right now"
      }
    >
      {counts ? counts[name] : 0}
    </span>
  );

  const setRvolFrame = (timeframe, patch) =>
    setGroup("rvol", {
      timeframes: {
        ...config.rvol.timeframes,
        [timeframe]: { ...config.rvol.timeframes[timeframe], ...patch },
      },
    });

  // A group header's ANY/ALL. Rendered as a <select> rather than two radios
  // because that is what the TOS scan editor uses and he reads this panel
  // against that one.
  const modeSelect = (name, value) => (
    <select
      className="momx-filters-mode"
      value={value}
      onChange={(event) => setGroup(name, { mode: event.target.value })}
      aria-label={name.toUpperCase() + " match mode"}
    >
      <option value="any">ANY of the following</option>
      <option value="all">ALL of the following</option>
    </select>
  );

  return createPortal(
    <div className="momx-filters-overlay" onClick={onClose}>
      <div
        className="momx-filters"
        role="dialog"
        aria-label="Scanner filters"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-filters-head">
          <span>FILTERS</span>
          <span className="momx-filters-count">
            {count} of {total} tickers
          </span>
          <button type="button" onClick={onClose} aria-label="Close">x</button>
        </div>

        {/* THE ONLY SCROLLER IN THIS DIALOG. The head and foot are siblings
            outside it, so there is no position:sticky ANYWHERE inside a
            scrolling box - that combination, inside a position:fixed overlay,
            is what wedged the panel on his iPhone (2026-09-05: scrolled to the
            bottom, would not come back up). It also means the count and Reset
            are always on screen instead of scrolling away. */}
        <div className="momx-filters-body">
        {/* The block's own ANY/ALL, where the TOS scan editor puts it: on
            top, above the groups, each of which keeps its own. His request,
            2026-09-22 - RVOL on, SKITTLES on, "all of the follwoing", and the
            list shows only the tickers with both. The same <select> as the
            group headers, so the two levels read as one vocabulary. Saved
            with the rest of the config; the toolbar FILTERS button stays the
            one on/off switch. */}
        <div className="momx-filters-top">
          <select
            className="momx-filters-mode momx-filters-topmode"
            value={mode}
            onChange={(event) => onChange({ ...config, mode: event.target.value })}
            aria-label="Match ANY or ALL of the switched-on groups"
            data-testid="momx-filters-topmode"
          >
            <option value="any">ANY of the following</option>
            <option value="all">ALL of the following</option>
          </select>
        </div>
        {membership ? (
          <p className="momx-filters-members" data-testid="momx-filters-members">
            {membership}
          </p>
        ) : null}
        <p className="momx-filters-note">
          Runs on top of the scan results.{" "}
          {strategy ? (
            <>
              Strategy <strong>{STRATEGY_LABELS[strategy]}</strong> (in A/A+ Setup below) decides on
              its own - this dropdown and the groups are not used until you set A/A+ Setup back.
            </>
          ) : mode === "all" ? (
            <>
              A ticker must pass <strong>every</strong> switched-on group below. A
              group you switch off is left out.
            </>
          ) : (
            <>
              A ticker needs to pass just <strong>one</strong> switched-on group below.
            </>
          )}{" "}
          {strategy ? null : "Each group's own dropdown picks ANY or ALL of its timeframes."}
        </p>
        {/* "0 of 358 tickers" on its own reads like a broken scanner. Under
            ALL it is the expected result of one quiet group, so say which
            group it is - the answer is already in the per-group counts. */}
        {whyEmpty ? (
          <p className="momx-filters-warn" data-testid="momx-filters-why-empty">
            <strong>0 tickers.</strong> {whyEmpty}
          </p>
        ) : null}

        {/* The state he hit on 2026-09-05: Skittles on "push to top" and
            nothing else switched on, so the filter passed every row while the
            panel looked configured. Said in the plainest words available, with
            the fix named. */}
        {nothingFilters ? (
          <p className="momx-filters-warn is-loud">
            <strong>Nothing is filtering - every ticker passes.</strong>{" "}
            Switch on RVOL, SQZ or SKITTLES below, and tick at least one
            timeframe inside it.
          </p>
        ) : null}

        {/* ---------------------------------------------- SCANNER GRADE
            Requirements, not ANY-of members: "A+ only" removes a B row
            whatever its RVOL reads. A row with no grade never passes. */}
        <section className="momx-filters-group">
          <header className="momx-filters-grouphead">
            <span className="momx-filters-on">
              <span>SCANNER GRADE (experimental)</span>
            </span>
          </header>
          <div className="momx-filters-gates">
            {gateSelect("setup", "A/A+ Setup", [
              // Grouped 2026-09-25 ("so many dropdown, so much confusion"):
              // the everyday choice alone on top, then the rest by purpose.
              ...(MOMX_NEW_SETUPS ? [{ group: "⭐ Use every day", options: [["best", "⭐ Best setups (GO, or OPT before 10:00)"]] }] : []),
              { group: "MomoX", options: [["mxaplus", "MomoX A+"]] },
              // The bot's other rules (2026-09-28), each on the board's own side.
              { group: "More rules (tests)", options: [
                ["turn", bear ? "Market turn (SPY turns down)" : "Market turn (SPY turns up)"],
                ["sector", bear ? "🔥 Falling-sector leaders" : "🔥 Sector leaders (hot / moving)"],
                ["zs", bear ? "ZS rule (below every line, clear 2% down)" : "ZS rule (above every line, clear 2%)"],
                ["chartShort", bear ? "Short arrows P2H / P4H" : "Short arrows C2H / C4H"],
                ["dayLine", bear ? "Daily line: opened below yday low" : "Daily line: opened above yday high"],
                ["dayLine100", bear ? "Daily line $100+, $0.50 room (puts)" : "Daily line $100+, $0.50 room (calls)"],
                ["dayReclaim", bear ? "Gap up, lost yday high" : "Gap down, took back yday low"],
              ] },
              { group: "Letters only", options: [["any", "Any"], ["aAndUp", "A and up"], ["aPlus", "A+ only"]] },
              { group: MOMX_NEW_SETUPS ? "Extra ideas (still being tested)" : "Chart", options: [
                ...(MOMX_NEW_SETUPS ? [
                  ["goRvolMacd", "GO + short RVOL + MACD"],
                  ["bestHot", "Best setups in 🔥 hot sectors"],
                  ["hotLead", "🔥 Hot sector leaders (#1 / #2)"],
                  ["solo", "SOLO big-money stocks"],
                  ["newsMomo", "News + momentum (AI news, then A/A+)"],
                ] : []),
                // His own chart read, kept by his ask 2026-09-24.
                ["chart", "Chart CALL2H / CALL4H (live)"],
              ] },
              // Never stopped running (his ask 2026-09-25 to bring them back).
              { group: "My older strategies", options: [
                ["v2", "Strategy V2"],
                ["v3", "Strategy V3"],
                ["daily2", "Strategy Daily 2"],
                ["g", "Strategy G (my rules)"],
                ...(MOMX_NEW_SETUPS ? [["go", "GO only"], ["opt", "OPT only"]] : []),
              ] },
            ])}
            {gateSelect("momentum", "Momentum", [
              ["any", "Any"],
              ["building", "Building only"],
            ])}
            {gateSelect("pattern", "Pattern", [
              ["any", "Any"],
              ["explosive", "💥 Explosive"],
              ["steady", "📈 Steady"],
              ["either", "Either"],
            ])}
          </div>
          {strategy ? (
            <p className="momx-filters-hint is-strategy" data-testid="momx-filters-strategy-rules">
              <strong>Strategy {STRATEGY_LABELS[strategy]}:</strong> {strategyRules(direction)[strategy]}.
              {" "}This is the whole filter - Momentum, Pattern and the groups below are
              left out. Test only (not proven): sell at +2%, cut at -1.5%.
            </p>
          ) : (
            <p className="momx-filters-hint">
              These must ALSO be true, under ANY and under ALL - they narrow the groups
              below, never widen them.
            </p>
          )}
        </section>

        {/* ---------------------------------------------- RVOL */}
        <section className={groupClass("rvol")} data-testid="momx-filters-group-rvol">
          <header className="momx-filters-grouphead">
            <label className="momx-filters-on">
              <input
                type="checkbox"
                checked={Boolean(config.rvol.on)}
                onChange={(event) => setGroup("rvol", { on: event.target.checked })}
              />
              <span>RVOL</span>
            </label>
            {groupCount("rvol")}
            {offTag("rvol")}
            {modeSelect("rvol", config.rvol.mode)}
          </header>

          <div className="momx-filters-frames">
            {FILTER_RVOL_TIMEFRAMES.map((timeframe) => {
              const frame = config.rvol.timeframes[timeframe] || {};
              return (
                <label key={timeframe} className="momx-filters-row">
                  <input
                    type="checkbox"
                    checked={Boolean(frame.on)}
                    onChange={(event) => setRvolFrame(timeframe, { on: event.target.checked })}
                  />
                  <span className="momx-filters-tf">{timeframe}</span>
                  <span className="momx-filters-op">&ge;</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    min="0"
                    max="7"
                    step="0.1"
                    value={String(frame.min)}
                    onChange={(event) =>
                      setRvolFrame(timeframe, {
                        min: coerceThreshold(event.target.value, frame.min),
                      })
                    }
                  />
                </label>
              );
            })}
          </div>

          <label className="momx-filters-check">
            <input
              type="checkbox"
              checked={Boolean(config.rvol.bullishOnly)}
              onChange={(event) => setGroup("rvol", { bullishOnly: event.target.checked })}
            />
            {/* The colour already carries the direction: at the same reading the
                column paints cyan/green when the bar closed in its upper half
                and magenta/red when it did not (momx/columns.py::rvol_cell,
                "isBullish = buying >= selling"). Under 2.0 there is NO block -
                the side lives only in the number's own colour - and the old
                label ("green or cyan block") never said so. 2026-09-22: he set
                D >= 0.8, saw CVS read 0.8 and asked why it did not pass. Its
                0.8 was dark red. */}
            <span>
              {bear ? "Bearish only" : "Bullish only"}{" "}
              <em>
                {bear
                  ? "- red or magenta only: the block, or under 2.0 the number's own colour. Green or cyan never counts, whatever the number."
                  : "- green or cyan only: the block, or under 2.0 the number's own colour. Red or magenta never counts, whatever the number."}
              </em>
            </span>
          </label>
          <p className="momx-filters-hint">
            The number is the column's own reading: <strong>cyan is 3.0 and up</strong>,{" "}
            <strong>green is 2.0 to 3.0</strong>, and it tops out at 7.0. Under 2.0
            there is no block: a green or cyan number means the bar closed in the
            top half of its range, a red or magenta one in the bottom half.
          </p>
        </section>

        {/* ---------------------------------------------- SQZ */}
        <section className={groupClass("sqz")} data-testid="momx-filters-group-sqz">
          <header className="momx-filters-grouphead">
            <label className="momx-filters-on">
              <input
                type="checkbox"
                checked={Boolean(config.sqz.on)}
                onChange={(event) => setGroup("sqz", { on: event.target.checked })}
              />
              <span>SQZ</span>
            </label>
            {groupCount("sqz")}
            {offTag("sqz")}
            {modeSelect("sqz", config.sqz.mode)}
          </header>

          <div className="momx-filters-chips">
            {FILTER_SQZ_TIMEFRAMES.map((timeframe) => (
              <label key={timeframe} className="momx-filters-chip">
                <input
                  type="checkbox"
                  checked={Boolean(config.sqz.timeframes[timeframe])}
                  onChange={(event) =>
                    setGroup("sqz", {
                      timeframes: { ...config.sqz.timeframes, [timeframe]: event.target.checked },
                    })
                  }
                />
                <span>{timeframe}</span>
              </label>
            ))}
          </div>

          <p className="momx-filters-sub">Counts as a pass:</p>
          <div className="momx-filters-states">
            {SQZ_STATE_LABELS.map(([name, label]) => (
              <label key={name} className="momx-filters-check">
                <input
                  type="checkbox"
                  checked={Boolean(config.sqz.pass[name])}
                  onChange={(event) =>
                    setGroup("sqz", {
                      pass: { ...config.sqz.pass, [name]: event.target.checked },
                    })
                  }
                />
                {/* BEAR: the box stands for its down-side twin (momxFilters
                    mirroredState) - show THAT colour and wording. */}
                <span
                  className="momx-filters-swatch"
                  style={{ "--momx-swatch": SQZ_SWATCH[mirroredState("sqz", name, bear)] }}
                  aria-hidden="true"
                />
                <span>{bear ? SQZ_STATE_TEXT[mirroredState("sqz", name, true)] || label : label}</span>
              </label>
            ))}
          </div>
        </section>

        {/* ---------------------------------------------- SKITTLES */}
        <section className={groupClass("skittles")} data-testid="momx-filters-group-skittles">
          <header className="momx-filters-grouphead">
            <label className="momx-filters-on">
              <input
                type="checkbox"
                checked={Boolean(config.skittles.on)}
                onChange={(event) => setGroup("skittles", { on: event.target.checked })}
              />
              <span>SKITTLES</span>
            </label>
            {groupCount("skittles")}
            {offTag("skittles")}
            {modeSelect("skittles", config.skittles.mode)}
          </header>

          <div className="momx-filters-chips">
            {FILTER_SKITTLES_TIMEFRAMES.map((timeframe) => (
              <label key={timeframe} className="momx-filters-chip">
                <input
                  type="checkbox"
                  checked={Boolean(config.skittles.timeframes[timeframe])}
                  onChange={(event) =>
                    setGroup("skittles", {
                      timeframes: {
                        ...config.skittles.timeframes,
                        [timeframe]: event.target.checked,
                      },
                    })
                  }
                />
                <span>{timeframe}</span>
              </label>
            ))}
          </div>

          <p className="momx-filters-sub">Counts as a pass:</p>
          <div className="momx-filters-states">
            {SKITTLES_STATE_LABELS.map(([name, label]) => (
              <label key={name} className="momx-filters-check">
                <input
                  type="checkbox"
                  checked={Boolean(config.skittles.pass[name])}
                  onChange={(event) =>
                    setGroup("skittles", {
                      pass: { ...config.skittles.pass, [name]: event.target.checked },
                    })
                  }
                />
                <span
                  className="momx-filters-swatch"
                  style={{ "--momx-swatch": SKITTLES_SWATCH[mirroredState("skittles", name, bear)] }}
                  aria-hidden="true"
                />
                <span>{bear ? SKITTLES_STATE_TEXT[mirroredState("skittles", name, true)] || label : label}</span>
              </label>
            ))}
          </div>
          <p className="momx-filters-hint">
            These are the block colours, not the number's colour. Every one is a
            cross that fired on this bar or the one before.
          </p>

          {/* An INDEPENDENT switch, not a mode. It used to be a radio pair
              ("push to top" / "must have") where choosing the first made the
              ticked SKITTLES box filter nothing - a control that overrode the
              checkbox above it. Now the checkbox means the same thing in all
              three groups, and this only decides the star and the ordering,
              which is why it keeps working with the group switched off - and
              why it stays bright (momx-filters-keep) when an OFF group's other
              controls are greyed. */}
          <label className="momx-filters-check momx-filters-keep">
            <input
              type="checkbox"
              checked={Boolean(config.skittles.rankToTop)}
              onChange={(event) => setGroup("skittles", { rankToTop: event.target.checked })}
            />
            <span>
              Star these and push them to the top{" "}
              <em>- works whether or not SKITTLES is filtering</em>
            </span>
          </label>
          {/* Measured 2026-09-02/03/04: on its own this group leaves 0 or 1
              ticker at 9:45 ET every one of those mornings, and on the live
              board 2026-09-05 it was 0 of 8. The header count says so live;
              this says WHY, right where he has just ticked it. It used to say
              "a cyan Skittles" and that the group "only adds tickers" - wrong
              twice over: since 2026-09-05 the group reads whichever crosses
              are ticked (by default the four bullish ones, cyan only one of
              them), and under ALL (2026-09-22) a filtering Skittles group at
              0 EMPTIES the board rather than adding anything. */}
          {config.skittles.on && counts && counts.skittles === 0 ? (
            <p className="momx-filters-warn">
              No ticker has one of the ticked Skittles crosses on the ticked
              timeframes right now. Each cross is a one-bar event, so this is
              often nobody.
            </p>
          ) : null}
        </section>
        </div>

        <footer className="momx-filters-foot">
          <span className="momx-filters-sentence">{describeFilters(config, direction)}</span>
          <button type="button" className="momx-btn" onClick={onReset}>
            Reset
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

// Panel wording for the five squeeze states. DERIVED from SQZ_STATE_BG rather
// than written out again: that constant is what the filter actually tests, so
// a state added there but forgotten here would be a box he can never tick.
// An unnamed state falls back to its own key instead of vanishing.
const SQZ_STATE_TEXT = {
  cyan: "cyan - fired, rising",
  blank: 'blank "-" - no squeeze',
  magenta: "magenta - fired, not rising",
  orange: "orange - still squeezed",
  white: "white - high compression",
};

// Skittles paints. Written as WHAT FIRED, not as a colour name: "green" tells
// him nothing, "MACD crossed up" is the thing he is choosing. Derived from
// SKITTLES_STATE_BG for the same reason the SQZ list is - a paint the filter
// tests but the panel never renders is a condition he cannot reach, which is
// exactly how MACD went missing until 2026-09-05.
// SHORT ON PURPOSE. The first cut spelled each one out ("green - MACD crossed
// up, 9 above 20") and every label wrapped to THREE lines in the two-column
// grid, making this block ~370px tall on a phone - which is how the Skittles
// colours ended up below the fold and he reported MACD as missing when it was
// on screen. The word "crossed" lives once, in the hint line underneath.
const SKITTLES_STATE_TEXT = {
  cyan: "cyan - EMA 9/20 up",
  green: "green - MACD up, 9 over 20",
  lime: "lime - EMA 4/8 up",
  dark_green: "dark green - MACD up",
  magenta: "magenta - EMA 9/20 down",
  red: "red - MACD down, 9 under 20",
  light_red: "light red - EMA 4/8 down",
  plum: "plum - MACD down",
};

const SKITTLES_STATE_LABELS = Object.keys(SKITTLES_STATE_BG).map((name) => [
  name,
  SKITTLES_STATE_TEXT[name] || name,
]);

const SKITTLES_SWATCH = Object.fromEntries(
  Object.entries(SKITTLES_STATE_BG).map(([name, bg]) => [
    name,
    THINKSCRIPT_COLORS[bg] || "transparent",
  ]),
);

const SQZ_STATE_LABELS = Object.keys(SQZ_STATE_BG).map((name) => [
  name,
  SQZ_STATE_TEXT[name] || name,
]);

// The swatch beside each box is the cell's own colour, so the panel and the
// board are the same vocabulary. "blank" is the absence of paint, so it shows
// as an empty outline rather than a colour.
const SQZ_SWATCH = Object.fromEntries(
  Object.entries(SQZ_STATE_BG).map(([name, bg]) => [
    name,
    bg === "black" ? "transparent" : THINKSCRIPT_COLORS[bg] || "transparent",
  ]),
);

// The NEWS list (2026-09-26): headlines from the ticker-tagged scraper for the
// tickers on the board, grouped by ticker (newest ticker first, newest story
// first), each with publisher, aggregator, age and teaser, under one line that
// says where they came from and which feeds did not answer. Shown while NEWS
// is pressed, above the table, in its own scroller so a long list never pushes
// the board off a phone. Pure rendering; the data comes from the panel.
function MomxNewsFeedBox({ payload, busy, symbols, onRefresh }) {
  const [open, setOpen] = useState(true);
  const nowMs = Date.now();
  const groups = useMemo(() => groupFeedBySymbol(payload ? payload.feed : [], symbols), [payload, symbols]);
  const refreshing = Boolean(busy || (payload && payload.refreshing));
  // `busy` is known before the store has been polled again, so the line says
  // "refreshing" the moment the button does, not a poll later.
  const health = sourceHealthLine(refreshing ? { ...(payload || {}), refreshing: true } : payload);
  const unavailable = /Unavailable:|failed/.test(health);
  return (
    <section className="momx-newsfeed" aria-label="News headlines" data-testid="momx-newsfeed">
      <div className="momx-newsfeed-head">
        <button
          type="button"
          className={"momx-industry-toggle" + (open ? " is-open" : "")}
          aria-expanded={open}
          onClick={() => setOpen((on) => !on)}
          title={open ? "Hide the headlines" : "Show the headlines"}
        >
          <ChevronDown size={13} aria-hidden="true" className="momx-industry-caret" />
          <span>Headlines</span>
          <span className="momx-industry-total">{groups.length}</span>
        </button>
        <span className={"momx-newsfeed-health" + (unavailable ? " is-down" : "")} data-testid="momx-newsfeed-health" title={health}>
          {health}
        </span>
        <button
          type="button"
          className="momx-newsfeed-refresh"
          onClick={onRefresh}
          disabled={refreshing}
          title="Read every source again for the tickers on the board"
          data-testid="momx-newsfeed-refresh"
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {!open ? null : groups.length === 0 ? (
        <p className="momx-newsfeed-empty">
          {refreshing
            ? "Reading the sources for " + Math.min(symbols.length, MOMX_NEWS_REFRESH_MAX_SYMBOLS) + " tickers…"
            : "No stored headline for these tickers yet. Press Refresh."}
        </p>
      ) : (
        <div className="momx-newsfeed-list">
          {groups.map((group) => (
            <div key={group.symbol} className="momx-newsfeed-group">
              <div className="momx-newsfeed-symbol">
                {group.symbol}
                <span>{group.items.length}</span>
              </div>
              <ul>
                {group.items.map((item) => {
                  const tone = sentimentTone(item.sentiment);
                  const fresh = item.atMs !== null && nowMs - item.atMs <= NEWSFEED_FRESH_MS;
                  const whence = [
                    item.source,
                    item.via && item.via !== item.source ? "via " + item.via : "",
                    item.atMs !== null ? feedAge(item.publishedAt, nowMs) : "",
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={item.url || item.headline} className="momx-newsfeed-item">
                      <span className={"momx-newsfeed-pill" + (fresh ? " is-fresh" : "")}>{fresh ? "Fresh" : "Stored"}</span>
                      {tone ? <span className={"momx-newsfeed-sent is-" + tone}>{item.sentiment}</span> : null}
                      {item.url ? (
                        <a href={item.url} target="_blank" rel="noopener noreferrer">{item.headline}</a>
                      ) : (
                        <span>{item.headline}</span>
                      )}
                      {whence ? <small>{whence}</small> : null}
                      {item.summary ? <small className="momx-newsfeed-summary">{item.summary}</small> : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// News popover, one per board, rendered by the PARENT (not the row): the open
// symbol lives in parent state, so opening/closing it re-renders zero MomxRows,
// and the rows receive only the stable onNewsToggle callback. PORTALED to
// <body> for the same reason MomoSettings is - the pinned toolbar and sticky
// table headers form stacking contexts that slice any in-panel popover.
// Fixed centered-bottom everywhere: thumb-reachable on the phone, deliberately
// not anchored to the row (anchoring across sticky scrollers is the
// over-engineering the design note forbids).
function MomxNewsPopover({ symbol, news, verdict, onClose }) {
  // "Why is it moving?" - ON DEMAND, never per row. This costs a model call and
  // a 358-row board must never fire 358 of them, so it is a button the trader
  // presses on a headline he already opened.
  //
  // The endpoint has existed and worked since 2026-08-27 and was referenced
  // ZERO times in the frontend. Its own rule does the hard part: when the
  // headlines do not explain the move it answers "No news explains this move."
  // instead of inventing a reason - which is the failure in the competitor's
  // app, where a generic line about GPU compute capacity sits under a ticker up
  // 5.83% and explains nothing.
  const [aiState, setAiState] = useState("idle");
  const [aiRead, setAiRead] = useState(null);
  useEffect(() => {
    // A different symbol is a different question.
    setAiState("idle");
    setAiRead(null);
  }, [symbol]);
  const askWhy = async (attempt = 0) => {
    if (attempt === 0 && aiState === "loading") return;
    setAiState("loading");
    try {
      const response = await fetch(
        "/api/ai/catalyst?symbol=" + encodeURIComponent(symbol || ""),
      );
      const payload = await response.json();
      const answered = payload && typeof payload === "object";
      // THE FIRST CALL USUALLY HAS NO ANSWER YET. The endpoint hands back a
      // placeholder ("The AI is writing this now...") with summary null while a
      // daemon thread does the model call, then serves the real answer from its
      // cache moments later. Treating that as the answer would print "No
      // answer." over a question that is still being worked on - measured on
      // AAPL 2026-09-04. So: keep waiting, and only give up after a bounded
      // number of tries so a stuck build cannot spin forever.
      const pending = answered && payload.summary == null && payload.available !== false;
      if (pending && attempt < 6) {
        setAiRead(payload);
        window.setTimeout(() => askWhy(attempt + 1), 2500);
        return;
      }
      setAiRead(answered ? payload : null);
      setAiState("done");
    } catch {
      // A failed explanation must never be mistaken for "no news explains it".
      setAiRead(null);
      setAiState("error");
    }
  };
  if (!news) return null;
  return createPortal(
    <div className="momx-news-overlay" onClick={onClose}>
      <div
        className="momx-news-pop"
        role="dialog"
        aria-label={"News for " + symbol}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-news-pop-head">
          <span className="momx-news-pop-symbol">{symbol}</span>
          <button type="button" onClick={onClose} aria-label="Close">x</button>
        </div>
        {/* The plain word first (2026-09-27): on the phone there is no hover,
            so this is where "Positive" / "Negative" has to be said. */}
        {verdict && verdict.toneWord ? (
          <p className={"momx-news-pop-verdict is-" + verdict.tone}>
            {verdict.toneWord + " news"}
            <small>{" (" + verdict.readBy + ")"}</small>
          </p>
        ) : null}
        <p className="momx-news-pop-headline">{news.headline}</p>
        {/* From the ticker-tagged store: the publisher's teaser, and whether
            the story is inside the 24h window or an older stored one. */}
        {news.summary ? <p className="momx-news-pop-summary">{news.summary}</p> : null}
        <div className="momx-news-pop-meta">
          {news.stored ? (
            <span className={"momx-newsfeed-pill" + (news.fresh ? " is-fresh" : "")}>{news.fresh ? "Fresh" : "Stored"}</span>
          ) : null}
          {news.sentiment ? (
            <span className={"momx-newsfeed-sent is-" + (sentimentTone(news.sentiment) || "flat")}>{news.sentiment}</span>
          ) : null}
          {news.age ? <span>{news.age}</span> : null}
          {news.source ? <span>{news.source}</span> : null}
          {news.via && news.via !== news.source ? <span>{"via " + news.via}</span> : null}
          {news.url ? (
            <a href={news.url} target="_blank" rel="noopener noreferrer">
              Read
            </a>
          ) : null}
          <button
            type="button"
            className="momx-news-why"
            onClick={askWhy}
            disabled={aiState === "loading"}
          >
            {aiState === "loading" ? "Reading..." : "Why is it moving?"}
          </button>
        </div>
        {news.scope === "market-wide" ? (
          // Said here as well as in the badge tooltip, because this panel is
          // where he reads the story and decides what it means.
          <p className="momx-news-pop-scope">
            Market-wide story
            {news.namedCount > 1 ? " naming " + news.namedCount + " tickers" : ""}
            {" \u2014 it mentions " + symbol + ", it is not about it."}
          </p>
        ) : null}
        {aiState === "loading" && aiRead && aiRead.summary == null && aiRead.reason ? (
          <div className="momx-news-ai"><small>{aiRead.reason}</small></div>
        ) : null}
        {aiState === "done" && aiRead ? (
          <div className="momx-news-ai">
            <b>{aiRead.summary || "No answer."}</b>
            <small>
              {/* Always shown, because "no news explains this" and "we had no
                  news to read" are different answers and the trader must be
                  able to tell them apart. */}
              {aiRead.available === false
                ? aiRead.reason || "AI is not configured."
                : (aiRead.category || "") + " · confidence " + (aiRead.confidence || "?")
                  + " · " + (aiRead.headlinesConsidered || 0) + " headline"
                  + ((aiRead.headlinesConsidered || 0) === 1 ? "" : "s") + " read"}
            </small>
          </div>
        ) : null}
        {aiState === "error" ? (
          <div className="momx-news-ai">
            <small>Could not reach the AI just now - this is not an answer about the news.</small>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

// The Setup / Fresh cells, shared by the live row and the History row so the
// two tables cannot drift. Returns null for any other column kind. `base` is
// the row's own cell class (History adds is-changed). A row with no grade and
// no pattern renders an empty cell - never a guess.
// H/L drawn the MomoX way (his screenshot, 2026-09-25): one solid block at the
// left of the cell whose WIDTH is the size of HighLowDegree (-1..+1) and
// whose colour is his script's ladder - pale mint when strong (> +0.5, his
// DARK_GREEN), green when mildly above the middle, red / dark red below it,
// gray at exactly the middle. The number stays on hover. Shared by the main
// rows and the rest rows so the two can never draw it differently.
function hlBlock(cell) {
  const block = hlBlockStyle(cell);
  if (!block) return null;
  return (
    <span className="momx-hl-block" data-hl={cell.bg}>
      <span className="momx-hl-block-fill" style={{ backgroundColor: block.color, width: block.widthPct + "%" }} />
    </span>
  );
}

function gradeCell(column, row, onOpenGrade, base = "momx-cell") {
  if (column.kind === "setup") {
    const text = setupText(row);
    const parts = setupParts(text);
    const m5 = row && row.m5 && typeof row.m5 === "object" ? row.m5 : null;
    // The grade's own age belongs here, not in the Fresh column: that column
    // reports how long ago the newest COLOUR CHANGE was (2026-09-22).
    const since = gradeAgeText(row);
    const chartPart = m5
      ? "5m: " + chartLabel(m5.chart, row && row.direction === "bear" ? "bear" : "bull") + " · trigger " +
        (typeof m5.trigger === "number" && Number.isFinite(m5.trigger) ? "$" + m5.trigger.toFixed(2) : "–")
      : "";
    const hover = [since, chartPart].filter(Boolean).join(" · ") || undefined;
    const tags = strategyTags(row);
    // The momentum WORD (Fading / Holding / Extended / Building) is no longer
    // printed here (his ask, 2026-09-24): it describes only the last few 5m
    // candles, and "A+ · Fading" read as a downtrend - FSLY showed "A ·
    // Fading" at 13:00 on 2026-09-23, the moment its chart fired CALL2H and
    // CALL4H. The cell is the letter + his strategy / chart tags; the 5m state
    // stays in the why panel and still drives the V2 rules.
    const hasLetter = Boolean(parts.letter) && parts.letter.replace(/[^A-Z+]/g, "") !== "";
    return (
      <td
        key={column.key}
        data-col={column.key}
        className={`${base} momx-setup is-${setupTone(row)} mom-${momentumTone(row)}`}
        title={hover}
      >
        {/* MomoX's ⚡ goes FIRST, before the letter, so it never sits beside
            the grade's Explosive pattern icon (💥 since 2026-09-25). */}
        {tags.filter((tag) => tag.key === "bolt").map((tag) => (
          <span
            key={tag.key}
            className={"momx-strategy-tag is-" + tag.key + (tag.fresh ? " is-fresh" : "") + (tag.gone ? " is-gone" : "")}
            title={tag.title}
            data-testid={"momx-strategy-tag-" + tag.key}
          >
            {tag.text}
          </span>
        ))}
        {hasLetter ? (
          <button
            type="button"
            className="momx-setup-button"
            onClick={(event) => {
              // The row itself may be clickable (card / snapshot) - this
              // click is for the why panel only.
              event.stopPropagation();
              if (onOpenGrade) onOpenGrade(row);
            }}
            title={(hover ? hover + " - " : "") + "click to see why"}
          >
            <span className="momx-setup-letter">{parts.letter}</span>
          </button>
        ) : null}
        {/* V2 / V3 / "D2 #n" - inside this cell, not a new column (his ask,
            2026-09-23). The worker decides them (momx/strategy.py). */}
        {tags.filter((tag) => tag.key !== "bolt").map((tag) => (
          <span
            key={tag.key}
            className={"momx-strategy-tag is-" + tag.key + (tag.fresh ? " is-fresh" : "") + (tag.gone ? " is-gone" : "")}
            title={tag.title}
            data-testid={"momx-strategy-tag-" + tag.key}
          >
            {tag.text}
          </span>
        ))}
      </td>
    );
  }
  if (column.kind === "fresh") {
    return (
      <td key={column.key} data-col={column.key} className={base + " momx-fresh"}>
        {freshText(row)}
      </td>
    );
  }
  return null;
}

const MomxRow = memo(function MomxRow({ row, isNew, showNewTag, sinceLabel, liveLast, onNewsToggle, onOpenCard, onOpenGrade, columns, newsTime, starred, earnings, bolt }) {
  const passed = Boolean(row && row.scanPass);
  // Use the same session change as the scanner, filters and sorting. A live
  // after-hours last is not the regular-session close used by this percentage;
  // inferring its denominator from a quote-trend cell changed LASR -1.37% to
  // +0.23%. The next scanner refresh updates this authoritative value.
  const pct = row?.pctChange;
  const up = Number.isFinite(pct) ? pct >= 0 : null;
  const fires = firesOf(row);
  // MomoX-style: the ticker itself flashes while a fresh setup (GO / OPT /
  // SOLO / TURN / 🔥 leader) is under 10 minutes old.
  const alertFresh = Boolean(freshAlert(row));
  // null on any malformed/stale/absent payload - newsOf is the single gate,
  // tested in momxCells.test.js, so a bad news fetch can never mark a row.
  const news = newsOf(row);
  const reasons =
    row && Array.isArray(row.scanReasons) && row.scanReasons.length > 0
      ? row.scanReasons.join("  ")
      : undefined;

  // LEFT ACCENT BAR on scan passes, tinted by the day's direction.
  // PRODUCT DECISION, NOT PARITY: thinkorswim draws no such bar and no
  // thinkScript in this project emits one. It exists because passes have to be
  // findable at a glance in a 355-row board. Do not cite it as MomoX-derived.
  const classes = ["momx-row"];
  if (passed) classes.push(up === false ? "is-pass-down" : "is-pass-up");
  else classes.push("is-muted");
  // NEW-MATCH FLASH: the same event that put a chip on the momentum strip
  // briefly highlights this row, so the strip and the board tell one story.
  // (The NEW text badge in the symbol cell is separate: since 2026-08-31 it is
  // driven by the worker's matchedSince STAMP - see showNewTag below - because
  // the event feed only exists while a tab polls and dies on restarts.)
  if (isNew) classes.push("is-new-match");

  return (
    <tr className={classes.join(" ")} title={reasons}>
      {(columns || MOMX_COLUMNS).map((column) => {
        if (column.kind === "setup" || column.kind === "fresh") {
          return gradeCell(column, row, onOpenGrade);
        }
        if (column.kind === "news" || column.kind === "event") {
          return newsEventCell(column, row, earnings, "momx-cell", undefined,
            column.kind === "news" && onNewsToggle ? () => onNewsToggle(row.symbol) : null);
        }
        if (column.kind === "matchedSince") {
          // NEWS pressed: this column carries WHEN THE HEADLINE CAME - most
          // rows in that view never matched the scan, so the scan stamp alone
          // would leave it blank (his 2026-09-02 screenshot). A row that DID
          // match keeps its alert time on top and the headline time rides
          // under it, exactly like the History tab, because he asked twice
          // "how do I know what time the ticker came" with NEWS pressed.
          if (newsTime) {
            const when = news ? newsTimeLabel(news) : null;
            const alertAt = snapshotTimeLabel(row && row.matchedSince);
            return (
              <td
                key={column.key}
                data-col={column.key}
                className="momx-cell momx-col-time momx-col-newstime"
                title={news ? news.headline : undefined}
              >
                {alertAt ? (
                  <span className="momx-history-time" title={"Alert: entered the scan at " + alertAt + " ET"}>
                    {alertAt}
                  </span>
                ) : null}
                {when && alertAt ? (
                  <span className="momx-history-newstime">
                    <Newspaper size={9} aria-hidden="true" />
                    {when.label}
                    <span className="momx-news-age">{when.age}</span>
                  </span>
                ) : when ? (
                  <>
                    <span className="momx-history-time">{when.label}</span>
                    <span className="momx-news-age">{when.age}</span>
                  </>
                ) : null}
              </td>
            );
          }
          return (
            <td key={column.key} data-col={column.key} className="momx-cell momx-col-time">
              <span className="momx-history-time">{snapshotTimeLabel(row && row.matchedSince)}</span>
            </td>
          );
        }
        if (column.kind === "industry") {
          const name = (row && typeof row.industry === "string" ? row.industry : "").trim();
          return (
            <td key={column.key} data-col={column.key} className="momx-cell momx-col-industry">
              {name === "" ? null : (
                <span
                  className={"momx-industry-pill" + (isHotRow(row) ? " is-hot" : "")}
                  style={{ "--momx-pill": industryColor(name) }}
                  title={isHotRow(row) ? "HOT sector: money is flowing into " + name + " right now" : undefined}
                >
                  {isHotRow(row) ? "🔥 " : ""}{name}
                </span>
              )}
            </td>
          );
        }

        if (column.kind === "symbol") {
          // Cyan up, magenta down - the app-wide candle palette, so a symbol
          // reads the same way here as it does on a chart.
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className="momx-cell momx-col-symbol">
              {/* The Skittles star. In the symbol cell for the same reason the
                  news badge is: the board is width-locked and a whole column
                  for one mark is not affordable. Shown only while FILTERS is
                  pressed AND Skittles is set to "push to top" - as a must-have
                  filter every visible row would carry one, which says nothing. */}
              {starred ? (
                <span
                  className="momx-star"
                  title="Skittles cyan - EMA(9) crossed above EMA(20) on a ticked timeframe"
                  aria-label="Skittles cross"
                >
                  <Star size={10} aria-hidden="true" />
                </span>
              ) : null}
              {fires.length > 0 || (bolt && bolt.on) ? (
                <button
                  type="button"
                  // ScannerX3's ⚡: LIVE while the signal state holds - full
                  // colour when fresh, fading toward a quarter over 2 hours,
                  // gone the moment price loses the upper half of its hour.
                  className={"momx-badge is-fires" + (bolt && bolt.on ? " is-live-bolt" : "")}
                  style={bolt && bolt.on && Number.isFinite(Number(bolt.opacity)) ? { opacity: Number(bolt.opacity) } : undefined}
                  title={(bolt && bolt.on
                    ? "⚡ LIVE: " + (bolt.families || []).join(" · ") + " - fired " + String(bolt.firedAt || "").slice(11, 16) + " ET" + (bolt.now ? ", passing the gates now" : "") + ". "
                    : "") + (fires.length ? "Signals fired: " + fires.join(" · ") : "") + " - click for the full card"}
                  aria-label={"Open the " + (row.symbol || entry.symbol) + " card"}
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenCard(row.symbol || entry.symbol);
                  }}
                >
                  <Zap size={10} aria-hidden="true" />
                </button>
              ) : null}
              {news ? (
                // A BADGE IN THE SYMBOL CELL, not a column (trader decision
                // 2026-08-30): the board is width-locked at 355 rows x N cols.
                <button
                  type="button"
                  // A round-up that merely names this ticker is dimmed, so the
                  // eye can tell it from a story actually about the company
                  // without opening anything. Not hidden: an absent badge reads
                  // as "no news at all", which is its own wrong impression.
                  className={
                    "momx-news-badge"
                    + (news.scope === "market-wide" ? " momx-news-badge--market" : "")
                  }
                  title={newsBadgeTitle(news)}
                  aria-label={
                    (news.scope === "market-wide" ? "Market-wide news: " : "News: ")
                    + news.headline
                  }
                  onClick={(event) => {
                    event.stopPropagation();
                    if (onNewsToggle) onNewsToggle(row.symbol);
                  }}
                >
                  <Newspaper size={11} aria-hidden="true" />
                </button>
              ) : null}
              {hotLeaderRank(row) ? (
                <span
                  className="momx-hot-symbol"
                  title={"#" + hotLeaderRank(row) + " stock of the HOT " + (row.industry || "") + " sector right now (top 5 by % today). Where money is going - not a tested buy signal on its own."}
                >
                  🔥
                </span>
              ) : null}
              <MomxSymbolLink className={"momx-symbol" + tone + (alertFresh ? " is-alert-fresh" : "")} symbol={(row && row.symbol) || ""} />
              {showNewTag ? (
                <span className="momx-new-tag" title="Entered the matched set in the last 15 minutes">
                  NEW
                </span>
              ) : sinceLabel ? (
                // WHEN this symbol entered the matched set: same ET day shows
                // the clock time ("9:47"), an older entry the weekday ("Fri").
                <span className="momx-since" title={"In the matched set since " + sinceLabel + " ET"}>
                  {sinceLabel}
                </span>
              ) : null}
            </td>
          );
        }

        if (column.kind === "pct") {
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className={"momx-cell momx-pct" + tone}>
              {formatPctChange(pct)}
            </td>
          );
        }

        if (column.kind === "chart") {
          const path = sparklinePath(row && row.sparkline, SPARKLINE_WIDTH, SPARKLINE_HEIGHT);
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className={"momx-cell momx-col-chart" + tone}>
              {path ? (
                <svg
                  className="momx-spark"
                  width={SPARKLINE_WIDTH}
                  height={SPARKLINE_HEIGHT}
                  viewBox={"0 0 " + SPARKLINE_WIDTH + " " + SPARKLINE_HEIGHT}
                  aria-hidden="true"
                  focusable="false"
                >
                  <path d={path} fill="none" stroke="currentColor" strokeWidth="1" />
                </svg>
              ) : null}
            </td>
          );
        }

        if (column.kind === "color") {
          // The separator uses the backend cell when it sends one and falls
          // back to solid white, which is this column's whole job on the
          // trader's board.
          const style = cellStyle(row && row.color);
          const background =
            style.backgroundColor === "transparent" ? "#ffffff" : style.backgroundColor;
          return (
            <td
              key={column.key}
              data-col={column.key}
              className="momx-cell momx-col-color"
              style={{ backgroundColor: background }}
            />
          );
        }

        if (column.kind === "bar") {
          // High/Low GRAPH: a bipolar bar showing the MAGNITUDE of HighLowDegree
          // (the trader's script value, -1 at the N-bar low .. +1 at the high),
          // like the TOS graph column - NOT a solid block, which ignored the
          // magnitude (a close just above mid looked identical to one at the
          // high). Positive fills right from centre in green, negative fills
          // left in red; length is |degree|. The backend owns the colour; the
          // exact degree stays on hover.
          const cell = row && row[column.field];
          const raw = cell && typeof cell === "object" ? Number(cell.value) : NaN;
          return (
            <td
              key={column.key}
              data-col={column.key}
              className="momx-cell momx-col-bar"
              title={Number.isFinite(raw) ? raw.toFixed(2) : undefined}
            >
              {hlBlock(cell)}
            </td>
          );
        }

        if (column.kind === "quoteTrend") {
          return (
            <td key={column.key} data-col={column.key} className="momx-cell momx-col-quote-trend">
              <QuoteTrendCell cells={row && row.quoteTrend} />
            </td>
          );
        }

        const cell = cellFor(row, column);
        // A cell his script painted whose reason arrived in the last 15
        // minutes - for RVOL the volume, for Skittles the bar the cross fired
        // in. Measured on his own board: ~5 RVOL cells at a time, and for
        // Skittles 3 when the 2h rolls (11:00, 15:00) or 7 when 2h and 4h roll
        // together (13:00).
        //
        // The daily family (D..M) can only ever be fresh at midnight, when
        // every symbol's bar rolls at once - 73 cells on a 50-row board. That
        // is accurate rather than wrong (they really did all just roll), and
        // it is 00:00-00:15 ET, so it is left uniform rather than special-cased.
        // Preserve the study colours, including old and closing-auction cells.
        // Freshness controls the pulse and tooltip, not the study output.
        const notLive = column.section === "rvol" && hasPaintedBackground(cell) ? rvolNotLiveReason(cell) : "";
        const liveSpike =
          (column.section === "rvol" || column.section === "skittles")
          && !notLive
          && cellIsLiveSpike(cell);
        return (
          <td
            key={column.key}
            data-col={column.key}
            className={"momx-cell" + (liveSpike ? " momx-cell--live" : "")}
            data-rvol-notlive={notLive || undefined}
            // Only saturated RVOL cells get a tooltip; everywhere else the
            // number already says everything and a title would be noise.
            title={column.section === "rvol"
              ? (notLive === "auction"
                ? "Volume timestamp is in the closing-auction window (3:50-4:10 PM ET); study colours are preserved."
                : notLive === "old"
                  ? "From an earlier session; study colours are preserved."
                  : rvolSaturationNote(cell) || undefined)
              : undefined}
            // Foreground only: the BACKGROUND rides on the pill inside, which
            // is what lets it have rounded corners at all.
            style={{ color: cellStyle(cell).color }}
          >
            {cellBody(cell, column.format)}
            {/* WHEN the volume behind this reading arrived. Only on RVOL
                cells that are already visible - his ladder blacks out weak
                readings on purpose, and annotating those would un-blank all
                seven columns on every row.

                D IS included. It was excluded while barAt meant "bar open",
                which on a daily bar is always midnight; now that barAt is the
                volume midpoint, D answers "when did half of today's volume
                trade" - CHPT's +74% day reads 11:20. He spotted the gap. */}
            {(() => {
              // Which cells get dated, and what the date MEANS, differ by
              // column - so this decides both here rather than pretending one
              // rule fits both.
              //
              //   RVOL      an accumulation, so barAt is the volume-weighted
              //             midpoint: "the volume arrived then". Shown on any
              //             visible cell.
              //   SKITTLES  no volume; the colour is a cross that fired inside
              //             the CURRENT bar, so barAt is the bar's own start:
              //             "the cross is at most this old". Shown only where a
              //             cross actually fired - his ladder leaves the rest
              //             black, and dating those adds ~2,400 labels that say
              //             nothing.
              const isRvol = column.section === "rvol";
              const isSkittles = column.section === "skittles";
              if (!isRvol && !isSkittles) return null;
              if (isRvol ? isMutedCell(cell) : !hasPaintedBackground(cell)) return null;
              const age = cellAgeLabel(cell);
              if (!age) return null;
              const fresh = cellAgeIsFresh(cell);
              const clock = cellAgeClock(cell);
              const title = isRvol
                ? "Half this " + column.label + " reading’s volume had arrived " + age
                  + " ago, at " + clock + " ET"
                  + (fresh ? " — inside the last 15 minutes, so this is live" : "")
                : "This " + column.label + " bar opened " + age + " ago, at " + clock
                  + " ET — the cross behind the colour fired somewhere inside it, so it is at most that old"
                  + (fresh ? ", and the bar is under 15 minutes old" : "");
              return (
                <span
                  className={"momx-cell-age" + (fresh ? " momx-cell-age--fresh" : "")}
                  title={title}
                >
                  {age}
                </span>
              );
            })()}
          </td>
        );
      })}
    </tr>
  );
});

// ---------------------------------------------------------------------------
// History - the 30-day archive of what the scan matched.
// Spec: docs/superpowers/specs/2026-08-31-momx-scanner-history-design.md
// ---------------------------------------------------------------------------

// One archived snapshot = ONE table row (the duplicate entries the trader
// asked for), rendered over the SAME column array as the live board plus the
// Time column. Cells named in the snapshot's `changed` list are highlighted;
// arrival rows carry the sparkline/quote cells and change rows blank them,
// which is what makes arrivals visually distinct in the table.
const MomxHistoryRow = memo(function MomxHistoryRow({ entry, newsTime, onOpenSnapshot, onOpenGrade, columns = MOMX_HISTORY_COLUMNS }) {
  const row = entry.row && typeof entry.row === "object" ? entry.row : EMPTY_OBJECT;
  const pct = Number(row.pctChange);
  const up = Number.isFinite(pct) ? pct >= 0 : null;
  const fires = firesOf(row);
  // News freshness is judged AS OF THE SNAPSHOT, not as of now: an archived
  // row shows what he saw at 09:14, and by tonight every headline would fail
  // a Date.now() freshness gate and the icon would silently vanish.
  const news = newsOf(row, entry.atMs || undefined);
  const changed = entry.changedKeys;
  const classes = ["momx-row", "momx-history-row"];
  classes.push(up === false ? "is-pass-down" : "is-pass-up");
  if (entry.isArrival) classes.push("is-arrival");
  const reasons =
    Array.isArray(row.scanReasons) && row.scanReasons.length > 0
      ? row.scanReasons.join("  ")
      : undefined;
  return (
    // data-at is what "Jump to" scrolls to. entry.timeLabel is the same HH:MM
    // the Time column renders, so the marker and what he reads are one value.
    <tr
      className={classes.join(" ")}
      title={reasons}
      data-at={entry.timeLabel}
      data-day={entry.date || undefined}
    >
      {columns.map((column) => {
        const base = "momx-cell" + (changed && changed.has(column.key) ? " is-changed" : "");
        if (column.kind === "setup" || column.kind === "fresh") {
          return gradeCell(column, row, onOpenGrade, base);
        }
        if (column.kind === "news" || column.kind === "event") {
          // News age as of the snapshot, like the badge; Event = today's calendar.
          return newsEventCell(column, row, EARNINGS_BY_SYMBOL.get(row.symbol), base, entry.atMs || undefined);
        }
        if (column.kind === "time") {
          // NEWS pressed: the headline's own time rides under the snapshot
          // time, judged as of the snapshot like the icon is. The snapshot
          // time stays on top - in an archive "when it came to the scanner"
          // is still the row's identity.
          const when = newsTime && news ? newsTimeLabel(news, entry.atMs || undefined) : null;
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-col-time"}>
              <span className="momx-history-time">{entry.timeLabel}</span>
              {when ? (
                <span className="momx-history-newstime" title={news.headline}>
                  <Newspaper size={9} aria-hidden="true" />
                  {when.label}
                </span>
              ) : null}
              {entry.isArrival ? (
                <span className="momx-new-tag" title="First time the scan matched this ticker today">
                  NEW
                </span>
              ) : null}
              {entry.capped ? (
                <span
                  className="momx-capped-tag"
                  title="This ticker hit its daily snapshot cap here - later changes were not recorded"
                >
                  capped
                </span>
              ) : null}
            </td>
          );
        }
        if (column.kind === "industry") {
          const name = (typeof row.industry === "string" ? row.industry : "").trim();
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-col-industry"}>
              {name === "" ? null : (
                <span
                  className={"momx-industry-pill" + (isHotRow(row) ? " is-hot" : "")}
                  style={{ "--momx-pill": industryColor(name) }}
                  title={isHotRow(row) ? "HOT sector: money is flowing into " + name + " right now" : undefined}
                >
                  {isHotRow(row) ? "🔥 " : ""}{name}
                </span>
              )}
            </td>
          );
        }
        if (column.kind === "symbol") {
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-col-symbol"}>
              {fires.length > 0 ? (
                <button
                  type="button"
                  className="momx-badge is-fires"
                  title={"Signals fired: " + fires.join(" · ") + " - click for this moment's card"}
                  aria-label={"Open the " + (row.symbol || entry.symbol) + " card for this snapshot"}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (onOpenSnapshot) onOpenSnapshot(entry);
                  }}
                >
                  <Zap size={10} aria-hidden="true" />
                </button>
              ) : null}
              {news ? (
                <span className="momx-news-badge" title={news.headline}>
                  <Newspaper size={11} aria-hidden="true" />
                </span>
              ) : null}
              {hotLeaderRank(row) ? (
                <span
                  className="momx-hot-symbol"
                  title={"#" + hotLeaderRank(row) + " stock of the HOT " + (row.industry || "") + " sector right now (top 5 by % today). Where money is going - not a tested buy signal on its own."}
                >
                  🔥
                </span>
              ) : null}
              <MomxSymbolLink className={"momx-symbol" + tone} symbol={row.symbol || entry.symbol} />
            </td>
          );
        }
        if (column.kind === "pct") {
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-pct" + tone}>
              {formatPctChange(pct)}
            </td>
          );
        }
        if (column.kind === "chart") {
          const path = entry.showChart
            ? sparklinePath(row.sparkline, SPARKLINE_WIDTH, SPARKLINE_HEIGHT)
            : null;
          const tone = up === null ? "" : up ? " is-up" : " is-down";
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-col-chart" + tone}>
              {path ? (
                <svg
                  className="momx-spark"
                  width={SPARKLINE_WIDTH}
                  height={SPARKLINE_HEIGHT}
                  viewBox={"0 0 " + SPARKLINE_WIDTH + " " + SPARKLINE_HEIGHT}
                  aria-hidden="true"
                  focusable="false"
                >
                  <path d={path} fill="none" stroke="currentColor" strokeWidth="1" />
                </svg>
              ) : null}
            </td>
          );
        }
        if (column.kind === "color") {
          const style = cellStyle(row.color);
          const background =
            style.backgroundColor === "transparent" ? "#ffffff" : style.backgroundColor;
          return (
            <td
              key={column.key}
              data-col={column.key}
              className={base + " momx-col-color"}
              style={{ backgroundColor: background }}
            />
          );
        }
        if (column.kind === "bar") {
          const cell = row[column.field];
          const raw = cell && typeof cell === "object" ? Number(cell.value) : NaN;
          return (
            <td
              key={column.key}
              data-col={column.key}
              className={base + " momx-col-bar"}
              title={Number.isFinite(raw) ? raw.toFixed(2) : undefined}
            >
              {hlBlock(cell)}
            </td>
          );
        }
        if (column.kind === "quoteTrend") {
          return (
            <td key={column.key} data-col={column.key} className={base + " momx-col-quote-trend"}>
              {entry.showChart ? <QuoteTrendCell cells={row.quoteTrend} /> : null}
            </td>
          );
        }
        const cell = cellFor(row, column);
        // What it moved FROM, not just that it moved. The ring says a number
        // changed; this says by how much - which is the part worth reading
        // when scanning a ticker's day (trader, 2026-09-01). Time and H/L are
        // excluded upstream in annotateChanges: one differs by definition, the
        // other is a per-tick float.
        const difference = differenceLabel((entry.changes || {})[column.key]);
        return (
          <td
            key={column.key}
            data-col={column.key}
            className={base}
            style={{ color: cellStyle(cell).color }}
          >
            {cellBody(cell, column.format)}
            {difference ? (
              <span
                className={"momx-diff is-" + (((entry.changes || {})[column.key] || {}).direction || "flat")}
                title={`Previously ${((entry.changes || {})[column.key] || {}).from ?? "—"}`}
              >{difference}</span>
            ) : null}
          </td>
        );
      })}
    </tr>
  );
});

// The history tab's whole body: day nav + search + the dense table (the same
// horizontally-scrolling table the live board uses, so phones get the same
// content). Fetching lives in the PARENT - one fetch per (list, day) or
// (list, search) view with a manual refresh and NO polling, because history
// is an archive, not a feed; this component renders whatever it is handed.
// ---------------------------------------------------------------------------
// FIND - "which tickers ever hit this" over the 30-day archive
// ---------------------------------------------------------------------------
// His ask (2026-09-05): "1h Rvol i will get result which tickers have rvol of
// 1hr". The answer is a TICKER LIST, so this renders its own view rather than
// squeezing into the snapshot table - a different question deserves a
// different answer shape, and it keeps the day pager, the NEWS toggle and the
// "showing 1-44 of 44" arithmetic meaning exactly what they mean today.
//
// The condition is built in momxHistoryQuery.js from the SAME colour tables
// the live board paints with, and the server applies it without ever learning
// what "cyan" means. See momx/query.py.
function MomxHistoryQueryDialog({ state, onChange, onClose, onRun, busy, error, direction = "bull" }) {
  const section = HISTORY_QUERY_SECTION_BY_KEY.get(state.section);
  const problem = historyQueryProblem(state);
  const picked = selectedStates(state);

  const setStates = (names) =>
    onChange({ ...state, states: { ...state.states, [section.key]: names } });

  return createPortal(
    <div className="momx-filters-overlay" onClick={onClose}>
      <div
        className="momx-filters"
        role="dialog"
        aria-label="Search the archive"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-filters-head">
          <span>FIND</span>
          <button type="button" onClick={onClose} aria-label="Close">x</button>
        </div>

        <div className="momx-filters-body">
          <p className="momx-filters-note">
            Which tickers ever hit this? Searches the whole archive on the
            server - not just the page on screen.
          </p>

          <section className="momx-filters-group">
            <header className="momx-filters-grouphead">
              <span className="momx-filters-on"><span>COLUMN</span></span>
              <select
                className="momx-filters-mode"
                value={state.section}
                aria-label="Column to search"
                onChange={(event) => {
                  const next = HISTORY_QUERY_SECTION_BY_KEY.get(event.target.value);
                  // Carry the timeframe over ONLY if the new column has it -
                  // Skittles has no 5m, and a stale timeframe would leave the
                  // dialog refusing to search the moment it switched.
                  onChange({
                    ...state,
                    section: next.key,
                    timeframe: next.timeframes.includes(state.timeframe)
                      ? state.timeframe
                      : next.defaultTimeframe,
                  });
                }}
              >
                {HISTORY_QUERY_SECTIONS.map((item) => (
                  <option key={item.key} value={item.key}>{item.label}</option>
                ))}
              </select>
            </header>

            <div className="momx-filters-chips">
              {section.timeframes.map((timeframe) => (
                <label key={timeframe} className="momx-filters-chip">
                  <input
                    type="radio"
                    name="momx-find-timeframe"
                    checked={state.timeframe === timeframe}
                    onChange={() => onChange({ ...state, timeframe })}
                  />
                  <span>{timeframe}</span>
                </label>
              ))}
            </div>

            {section.kind === "threshold" ? (
              <div className="momx-filters-frames" style={{ marginTop: 8 }}>
                <label className="momx-filters-row">
                  <span className="momx-filters-tf">at least</span>
                  <span className="momx-filters-op">&ge;</span>
                  <input
                    type="number"
                    inputMode="decimal"
                    step="0.1"
                    value={String(state.min ?? "")}
                    aria-label="Minimum reading"
                    onChange={(event) => onChange({ ...state, min: event.target.value })}
                  />
                </label>
                <label className="momx-filters-check">
                  <input
                    type="checkbox"
                    checked={Boolean(state.bullishOnly)}
                    onChange={(event) =>
                      onChange({ ...state, bullishOnly: event.target.checked })
                    }
                  />
                  <span>
                    {direction === "bear"
                      ? <>Sellers winning <em>- red or magenta block</em></>
                      : <>Buyers winning <em>- green or cyan block</em></>}
                  </span>
                </label>
              </div>
            ) : (
              <>
                <p className="momx-filters-sub">Counts as a hit:</p>
                <div className="momx-filters-states">
                  {Object.keys(section.states).map((name) => (
                    <label key={name} className="momx-filters-check">
                      <input
                        type="checkbox"
                        checked={picked.includes(name)}
                        onChange={(event) =>
                          setStates(
                            event.target.checked
                              ? [...picked, name]
                              : picked.filter((item) => item !== name),
                          )
                        }
                      />
                      <span
                        className="momx-filters-swatch"
                        style={{ "--momx-swatch": (section.key === "sqz" ? SQZ_SWATCH : SKITTLES_SWATCH)[name] }}
                        aria-hidden="true"
                      />
                      <span>
                        {(section.key === "sqz" ? SQZ_STATE_TEXT : SKITTLES_STATE_TEXT)[name] || name}
                      </span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </section>

          <section className="momx-filters-group">
            <header className="momx-filters-grouphead">
              <span className="momx-filters-on"><span>WHERE</span></span>
            </header>
            <div className="momx-filters-role">
              {[["day", "this day"], ["all", "every archived day"]].map(([value, label]) => (
                <label key={value} className="momx-filters-check">
                  <input
                    type="radio"
                    name="momx-find-scope"
                    checked={state.scope === value}
                    onChange={() => onChange({ ...state, scope: value })}
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            <p className="momx-filters-hint">
              Every archived day reads the whole 30-day archive on the server.
              The first run of a query takes a second or two; repeats are instant.
            </p>
          </section>
        </div>

        {/* THE ERROR BELONGS IN HERE. It used to render in the History
            section behind this modal, so a failed search looked like a button
            that did nothing - which is exactly how it was reported
            ("I searched with rvol 1hr and I don't see any result"). A failure
            has to appear where the person is looking. */}
        {error ? (
          <p className="momx-filters-warn is-loud" role="alert">
            <strong>The search did not run.</strong> {error}
          </p>
        ) : null}

        <footer className="momx-filters-foot">
          <span className="momx-filters-sentence">{describeHistoryQuery(state)}</span>
          <button
            type="button"
            className="momx-btn is-primary"
            disabled={Boolean(problem) || busy}
            onClick={onRun}
          >
            {busy ? "Searching" : "Search"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function MomxHistoryQueryResults({ payload, state, onClose, onOpenSymbol }) {
  const groups = groupResultsByDay(payload);
  const section = HISTORY_QUERY_SECTION_BY_KEY.get(payload?.condition?.section || state.section);
  const showsValue = section && section.kind === "threshold";
  return (
    <Fragment>
      <div className="momx-history-bar">
        <button type="button" className="momx-btn" onClick={onClose}>
          {"\u2039"} Back to history
        </button>
        <span className="momx-find-headline">{summariseResults(payload)}</span>
        <span className="momx-find-sentence">{describeHistoryQuery(state)}</span>
      </div>
      <div className="momx-table-scroll">
        <table className="momx-table momx-find-table">
          <thead>
            <tr className="momx-head-row">
              <th scope="col" className="momx-th">Day</th>
              <th scope="col" className="momx-th">Symbol</th>
              <th scope="col" className="momx-th">Industry</th>
              <th scope="col" className="momx-th">First</th>
              <th scope="col" className="momx-th">Last</th>
              <th scope="col" className="momx-th">Hits</th>
              {showsValue ? <th scope="col" className="momx-th">Peak</th> : null}
              <th scope="col" className="momx-th">% Chg</th>
            </tr>
          </thead>
          <tbody>
            {groups.length === 0 ? (
              <tr>
                <td className="momx-empty" colSpan={showsValue ? 8 : 7}>
                  <span className="momx-empty-inner">
                    {summariseResults(payload) || "Nothing matched."}
                  </span>
                </td>
              </tr>
            ) : (
              groups.flatMap((group) =>
                group.rows.map((row, index) => (
                  <tr key={group.date + ":" + row.symbol} className="momx-row">
                    <td className="momx-cell">{index === 0 ? group.date : ""}</td>
                    <td className="momx-cell momx-col-symbol">
                      <button
                        type="button"
                        className="momx-find-symbol"
                        onClick={() => onOpenSymbol(row.symbol)}
                        title={"Open the " + row.symbol + " card"}
                      >
                        {row.symbol}
                      </button>
                    </td>
                    <td className="momx-cell">
                      {row.industry ? (
                        <span
                          className="momx-industry-pill"
                          style={{ "--momx-pill": industryColor(row.industry) }}
                        >
                          {row.industry}
                        </span>
                      ) : null}
                    </td>
                    <td className="momx-cell">{row.first}</td>
                    <td className="momx-cell">{row.last}</td>
                    <td className="momx-cell">{row.hits}</td>
                    {showsValue ? (
                      <td className="momx-cell">
                        {row.peak === null || row.peak === undefined ? "" : row.peak}
                      </td>
                    ) : null}
                    <td className="momx-cell">{formatPctChange(row.pctChange)}</td>
                  </tr>
                )),
              )
            )}
          </tbody>
        </table>
      </div>
    </Fragment>
  );
}

// memo: the panel re-renders on every live poll and alert tick; without this
// each one rebuilt the whole 400-row archive table (every prop below is
// stable between those renders, so the memo actually holds).
const MomxHistorySection = memo(function MomxHistorySection({ onOpenSnapshot, onOpenCard, onOpenGrade, list, payload, loading, error, date, query, search, onDate, onSearch, onRefresh, onOffset, findState, findResult, findBusy, findError, onOpenFind, onCloseFind, columnLayout, onColumnLayoutChange }) {
  // The Live board's column layout, applied to the History columns: one
  // layout for both tables. Memoised so the memo'd rows keep one `columns`
  // reference until the layout itself changes.
  const historyColumnList = useMemo(() => applyLayout(MOMX_HISTORY_COLUMNS, columnLayout), [columnLayout]);
  // Frozen Symbol | Time | Setup block (usePinnedColumns): offsets are CSS
  // variables on the table, so the memo'd rows never re-render for them.
  const historyPin = usePinnedColumns(historyColumnList);
  const historyColumnGroups = useMemo(
    () => columnGroupSpans(historyColumnList, historyPin.pinned),
    [historyColumnList, historyPin.pinned],
  );
  // Same drag / right-click header controls as the Live board, same layout.
  const historyHeader = useColumnHeaderControls(columnLayout, onColumnLayoutChange);
  const days = payload && Array.isArray(payload.days) ? payload.days : EMPTY_ROWS;
  const today = etDayIso();
  const nav = useMemo(
    () => resolveDayNav(days, (payload && payload.date) || date || null),
    [days, payload, date],
  );
  // Sort state lives here, beside the table it drives. Default: time
  // ascending - the day is a timeline of arrivals and that is the whole point
  // of the page; any other column is one click away.
  const [rawHistorySort, setHistorySort] = useState(MOMX_HISTORY_DEFAULT_SORT);
  const onHistorySort = (column) => {
    if (!column || !column.sortable) return;
    // Same three-state cycle as the live board, ending in NO SORT (timeline order).
    setHistorySort((current) => nextSortState(current, column, column.key === "time" ? "asc" : "desc"));
  };
  // Same hidden-sort-column fix as the live board: the saved sort key can
  // point at a column just hidden from the History layout. ONE effective sort
  // drives both the row order and the header arrow.
  const historySort = useMemo(
    () => effectiveSort(rawHistorySort, historyColumnList, MOMX_HISTORY_DEFAULT_SORT),
    [rawHistorySort, historyColumnList],
  );
  // ANNOTATE BEFORE SORTING. The difference between two snapshots is a fact
  // about the timeline; computing it after a re-sort would compare a row
  // against whatever now happens to sit above it and invent changes that
  // never happened.
  const fullTimeline = useMemo(
    () => annotateChanges(flattenHistoryRows(payload ? payload.rows : null)),
    [payload],
  );
  // NEWS on the archive (asked 2026-09-02: "Add history also news so I can
  // filter with news also and tickers also"). Same gate as the live board's
  // button, judged AS OF EACH SNAPSHOT (entry.atMs) exactly like the row's
  // icon, so the filter keeps precisely the rows that show the icon. Applied
  // AFTER annotateChanges - the highlights are facts about the full timeline
  // and must not be recomputed against a thinned one - and BEFORE sorting and
  // day-grouping, so it composes with the ticker search (server-side) and with
  // whatever column he sorts by. It narrows the PAGE he is on; Newer/Older
  // still walk the whole archive.
  const [newsOnly, setNewsOnly] = useState(false);
  const newsTimeline = useMemo(
    () => fullTimeline.filter((entry) => newsOf(entry.row, entry.atMs || undefined) !== null),
    [fullTimeline],
  );
  const newsCount = newsTimeline.length;
  const timeline = newsOnly ? newsTimeline : fullTimeline;
  const searching = query !== "";
  const entries = useMemo(
    () => (searching ? timeline : sortHistoryRows(timeline, historySort, columnSortValue)),
    [searching, timeline, historySort],
  );
  const dayGroups = useMemo(
    // Sorting stays WITHIN each day group: days themselves remain newest-first
    // so a ticker's history still reads as a sequence of days.
    () => (searching ? sortHistoryGroups(groupRowsByDay(timeline), historySort, columnSortValue) : null),
    [searching, timeline, historySort],
  );
  const tickerCount = useMemo(() => {
    const seen = new Set();
    entries.forEach((entry) => seen.add(entry.symbol));
    return seen.size;
  }, [entries]);
  const retention =
    payload && Number.isFinite(payload.retentionDays)
      ? payload.retentionDays
      : MOMX_HISTORY_RETENTION_DAYS;

  let empty = "";
  if (loading && entries.length === 0) {
    empty = "Loading the " + (list || "") + " history...";
  } else if (error && entries.length === 0) {
    empty = "No history to show - see the message above.";
  } else if (newsOnly && fullTimeline.length > 0) {
    empty = "No entry on this page had news at the time. Press NEWS again for every entry, or page with Newer / Older.";
  } else if (searching && entries.length === 0) {
    empty = date
      ? query + " did not match " + (list || "this list") + " on " + (dayLabel(date) || date) +
        ". Pick \"All days\" to search the whole archive."
      : query + " has not matched " + (list || "this list") + " in the last " + retention + " days.";
  } else if (days.length === 0) {
    empty = "History starts saving the first time a ticker matches.";
  } else {
    // A day with no matches is an archived fact worth seeing, not a gap.
    empty = "No scan matches were recorded on this day.";
  }

  const pager = (place) => {
    const total = Number(payload && payload.totalRows) || 0;
    // The PAGE's size, not the filtered count: with NEWS pressed the page is
    // still rows 1-400 of the archive, it just shows fewer of them.
    const shown = fullTimeline.length;
    const offset = Number(payload && payload.offset) || 0;
    // Shown on BOTH history tabs even when everything fits on one page. It was
    // hidden in that case as anti-noise, and the result was worse: Mag7 (387
    // rows) had no pager while Watchlist (4800) did, and he reasonably read the
    // difference as the feature being missing rather than unnecessary. The
    // count line is worth having on its own -- "showing 1-387 of 387" answers
    // "is that everything?", which is the question the cap created.
    if (total === 0) return null;
    const first = total - offset - shown + 1;
    const last = total - offset;
    return (
      <div className={"momx-history-pager is-" + place}>
        <button
          type="button"
          className="momx-btn"
          disabled={loading || offset <= 0}
          onClick={() => onOffset(Math.max(0, offset - shown))}
        >
          {"\u2039 Newer"}
        </button>
        <span className="momx-history-pager-label">
          {shown > 0
            ? "showing " + first + "\u2013" + last + " of " + total +
              (newsOnly ? " \u00b7 " + entries.length + " with news" : "")
            : "no rows on this page of " + total}
        </span>
        <button
          type="button"
          className="momx-btn"
          disabled={loading || !(payload && payload.hasMore)}
          onClick={() => onOffset(offset + shown)}
        >
          {"Older \u203a"}
        </button>
      </div>
    );
  };

  // ---- Jump to a time -----------------------------------------------------
  // It MOVES THE PAGE; it does not filter. A day holds ~11,770 snapshots and a
  // page is 400, so a filter would either search the 3% already here and report
  // it as the day, or force every counter on this bar to be redefined at once.
  // Choosing an offset is what the pager already does, so "44 entries",
  // "showing 1-44 of 44" and Newer/Older all stay correct untouched.
  const [jumpText, setJumpText] = useState("");
  const [jumpFocus, setJumpFocus] = useState(false);
  const [jumpError, setJumpError] = useState("");
  // The minute a jump is trying to reveal. Paging alone was not enough: on a
  // day small enough to fit one page the offset floors to 0 and nothing moves,
  // which is exactly how "0930 does nothing" was reported.
  const [jumpMark, setJumpMark] = useState(null);
  // At rest the box shows the time the page on screen starts at, as a grey
  // HINT (placeholder), never as a value. It used to sit in the box as text
  // with maxLength=5: on an iPhone the tap placed a caret instead of selecting
  // it, the box was then full, and every digit typed was silently dropped -
  // that is the whole of "09:30 is not taking" (measured on WebKit,
  // 2026-09-05). An empty box cannot fail that way on any engine. Read as the
  // MINIMUM stamp on the page rather than rows[0], so a column sort cannot
  // make the hint disagree with the table under it.
  const pageStart = pageStartClock(payload && payload.rows);
  const jumpValue = jumpFocus ? jumpText : "";
  // Time works on a day (server timeIndex) AND inside a ticker search (the
  // loaded entries, which carry their day) - "search like time, or date or
  // search or all three of them" (2026-09-05).
  const canJump =
    (Array.isArray(payload && payload.timeIndex) && payload.timeIndex.length > 0) ||
    (searching && fullTimeline.length > 0);

  const commitJump = () => {
    const typed = jumpText.trim();
    if (!typed) return;
    setJumpMark(null);
    if (searching) {
      const clock = parseJumpTime(typed);
      if (!clock) {
        setJumpError("Enter a time like 09:45");
        return;
      }
      // Search the rows he can SEE. If NEWS is hiding the one he wants, say
      // so - the old code scrolled nowhere and said nothing.
      const hit = jumpTargetInEntries(entries, clock, (payload && payload.date) || null);
      if (!hit) {
        const hidden = newsOnly && jumpTargetInEntries(fullTimeline, clock, (payload && payload.date) || null);
        setJumpError(
          hidden
            ? "No entry with news at " + clock + " - press NEWS to see every entry"
            : "No " + query + " entry at or after " + clock + " on this page",
        );
        return;
      }
      setJumpError("");
      setJumpText(hit.clock);
      setJumpMark({ clock: hit.clock, day: hit.day });
      return;
    }
    const out = resolveJump(typed, payload);
    if (out.error) {
      setJumpError(out.error);
      return;
    }
    setJumpError("");
    // Repaint to the time he ACTUALLY got - the archive has quiet stretches and
    // landing on the next recorded moment must be visible, not silent.
    setJumpText(out.landedOn);
    onOffset(out.offset);
    setJumpMark({ clock: out.landedOn, day: null });
  };

  // Reveal the row once it is on screen. Runs after every render while a mark
  // is pending, because the rows may arrive a fetch later when the page really
  // did move. block:"center" and inline:"nearest" on purpose - this view owns
  // BOTH axes, and a default scrollIntoView would also yank the wide table
  // sideways out from under him.
  useEffect(() => {
    if (!jumpMark) return undefined;
    const target = document.querySelector(
      '.momx-history-table tbody tr[data-at="' + jumpMark.clock + '"]' +
        (jumpMark.day ? '[data-day="' + jumpMark.day + '"]' : ""),
    );
    if (!target) {
      // The page may still be on its way. If the row is on the page but NEWS
      // is hiding it, say that now; otherwise wait for the fetch, but not
      // forever - a mark that never lands must not fire on a Refresh an hour
      // later (measured: a missed jump under NEWS later scrolled 0 -> 578).
      if (!loading && newsOnly && fullTimeline.some((entry) => entry.timeLabel === jumpMark.clock)) {
        setJumpError(jumpMark.clock + " has no entry with news - press NEWS to see it");
        setJumpMark(null);
        return undefined;
      }
      const giveUp = window.setTimeout(() => setJumpMark(null), 6000);
      return () => window.clearTimeout(giveUp);
    }
    target.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
    target.classList.add("is-jump-target");
    const timer = window.setTimeout(() => {
      target.classList.remove("is-jump-target");
      setJumpMark(null);
    }, 2600);
    // Remove the class on the way out too: a second jump inside 2.6s used to
    // leave the first row cyan for good, because React never owned the class.
    return () => {
      window.clearTimeout(timer);
      target.classList.remove("is-jump-target");
    };
  }, [jumpMark, payload, loading, newsOnly, fullTimeline]);

  // A new day or a new ticker is a new question: what he typed for the old
  // one (and any complaint about it) must not carry over into the new box.
  useEffect(() => {
    setJumpText("");
    setJumpError("");
  }, [date, query]);

  const sessions = sessionLine(payload && payload.sessions);

  // A FIND result answers a different question - "which tickers ever hit this",
  // over the whole archive - so it takes the whole view rather than living
  // inside a table whose every counter (totalRows, "showing 1-44 of 44",
  // Newer/Older) assumes an unfiltered contiguous slice of ONE day.
  if (findResult) {
    return (
      <MomxHistoryQueryResults
        payload={findResult}
        state={findState}
        onClose={onCloseFind}
        // onOpenCard, NOT onOpenSnapshot. The results table calls this with a
        // bare symbol string; onOpenSnapshot wants a snapshot object and
        // returns silently without one, so every ticker click was a no-op.
        // The snapshot table keeps onOpenSnapshot - it really does have rows.
        onOpenSymbol={onOpenCard}
      />
    );
  }

  return (
    // A FRAGMENT, not a wrapper div, and that is the fix for "history cannot
    // scroll left or right" (trader, 2026-08-31 22:00). The panel's layout
    // contract (index.css, `.momx-scanner-panel > :not(.momx-table-scroll)`)
    // makes every direct child except the table wrapper sticky and capped to
    // the view's width - that is what keeps toolbars on screen while the ONE
    // scroller (.momx-scanner-view) pans the full-width table. A wrapper div
    // here was a direct child that was not .momx-table-scroll, so the ENTIRE
    // history block - table included - was width-capped and pinned: nothing
    // below it could scroll. Flattening puts the bar, the error line, the
    // table wrapper and the footnote directly under the panel, where the
    // existing contract handles each of them exactly like the live board's.
    <Fragment>
      {findError ? (
        <p className="momx-banner is-error" role="alert">
          <AlertTriangle size={13} aria-hidden="true" />
          <span>{findError}</span>
        </p>
      ) : null}
      <div className="momx-history-bar">
        <div className="scanner-day-nav">
          <button
            type="button"
            className="scanner-day-navbtn"
            disabled={searching || !nav.canOlder}
            onClick={() => onDate(nav.olderDate)}
            aria-label="Older day"
          >
            {"\u2039"}
          </button>
          {/* A real picker, not a label that only LOOKED tappable. One tap
              opens the phone's wheel over the days that actually exist in the
              archive (the day arrows step one day per tap and reload each
              time - Tuesday was five taps away). While a ticker is typed an
              "All days" choice appears; picking a day narrows the ticker to it. */}
          {nav.days.length > 0 ? (
            <select
              className="scanner-day-label scanner-day-select"
              value={searching && !date ? "" : nav.date || ""}
              onChange={(event) => onDate(event.target.value || null)}
              aria-label={searching ? "Day to search " + query + " on" : "Archived day"}
              data-testid="momx-history-day"
            >
              {searching ? <option value="">All days</option> : null}
              {nav.days.map((day) => (
                <option key={day} value={day}>
                  {dayLabel(day) + (day === today ? " \u00b7 today" : "")}
                </option>
              ))}
            </select>
          ) : (
            <span className="scanner-day-label">No days archived yet</span>
          )}
          <button
            type="button"
            className="scanner-day-navbtn"
            disabled={searching || !nav.canNewer}
            onClick={() => onDate(nav.newerDate)}
            aria-label="Newer day"
          >
            {"\u203a"}
          </button>
          {/* The DAY's count, not the page's - it printed "400 entries" beside
              a pager reading "of 11770". The ticker figure is per page, and
              says so. */}
          <span className="scanner-day-count">
            {(payload && Number.isFinite(payload.totalRows) && payload.totalRows > 0
              ? payload.totalRows
              : entries.length) +
              (searching
                ? " entries"
                : " entries \u00b7 " + tickerCount + (tickerCount === 1 ? " ticker" : " tickers") + " on this page")}
          </span>
        </div>
        {canJump ? (
          <label className="momx-history-jump">
            <span>Time</span>
            <input
              type="text"
              inputMode="numeric"
              spellCheck={false}
              autoComplete="off"
              className={jumpError ? "is-bad" : ""}
              value={jumpValue}
              placeholder={pageStart || "09:45"}
              data-testid="momx-history-jump"
              aria-label={searching ? "Jump " + query + " to a time (ET)" : "Jump to a time on this day (ET)"}
              // Empty on focus, on purpose - see jumpValue above.
              onFocus={() => { setJumpFocus(true); setJumpText(""); }}
              // The iPhone number pad has no Enter key, so closing it (blur)
              // is the only exit every phone has - it COMMITS. commitJump
              // first, while jumpText is still the source of truth.
              onBlur={() => { commitJump(); setJumpFocus(false); }}
              // Digits only from the keypad; the colon is added as he types
              // (0930 -> 09:30), because the keypad has no colon key.
              onChange={(event) => { setJumpText(formatJumpInput(event.target.value)); setJumpError(""); }}
              onKeyDown={(event) => {
                if (event.key === "Enter") { event.preventDefault(); commitJump(); }
              }}
            />
            <button
              type="button"
              className="momx-btn momx-history-jump-go"
              // Keep focus in the box so blur does not also fire and commit
              // twice; the click is the commit.
              onPointerDown={(event) => event.preventDefault()}
              onClick={commitJump}
              data-testid="momx-history-jump-go"
              aria-label="Go to this time"
            >
              Go
            </button>
            {jumpError ? (
              // Visible TEXT. It used to live in a title tooltip, which iOS
              // never shows - the only signal was an amber border.
              <span className="momx-history-jump-error" role="alert">{jumpError}</span>
            ) : null}
          </label>
        ) : null}
        <input
          className="momx-history-search"
          type="text"
          value={search}
          spellCheck={false}
          autoComplete="off"
          placeholder={date ? "Ticker on this day" : "Ticker, all days"}
          aria-label="Search history for a ticker"
          onChange={(event) => onSearch(event.target.value)}
        />
        {search !== "" ? (
          <button type="button" className="momx-btn" onClick={() => onSearch("")}>
            Clear
          </button>
        ) : null}
        <button
          type="button"
          className={"momx-news-btn" + (newsOnly ? " is-active" : "")}
          onClick={() => setNewsOnly((on) => !on)}
          aria-pressed={newsOnly}
          data-testid="momx-history-news-toggle"
          title={
            newsOnly
              ? "Showing only the entries that had news at the time. Press again for every entry."
              : newsCount > 0
                ? "Show only the " + newsCount + " entries on this page that had news at the time"
                : "No entry on this page had news at the time"
          }
        >
          <Newspaper size={11} aria-hidden="true" />
          NEWS
          <span className="momx-news-count">{newsCount}</span>
        </button>
        {/* FIND asks a different question from everything else on this bar -
            "which tickers ever hit this", over the WHOLE archive rather than
            the 400-row page on screen - so it opens its own view instead of
            narrowing this one. That keeps the day pager, the NEWS count and
            the "showing 1-44 of 44" arithmetic meaning what they mean today. */}
        <button
          type="button"
          className="momx-find-btn"
          onClick={onOpenFind}
          data-testid="momx-history-find"
          title="Search the whole archive for tickers that hit a condition"
        >
          <Search size={11} aria-hidden="true" />
          FIND
        </button>
        <button
          type="button"
          className="momx-icon-btn"
          onClick={onRefresh}
          disabled={loading}
          title="Reload the archive"
          aria-label="Reload the archive"
        >
          <RefreshCw size={13} className={loading ? "is-spinning" : ""} aria-hidden="true" />
        </button>
      </div>

      {/* Read-only, and a DIRECT child of the fragment - the panel's layout
          contract makes every direct child except the table wrapper sticky and
          width-capped, and burying this inside the bar would wrap it at 640px
          behind six other controls. Each name jumps to the first row of its
          window; the counts are of the WHOLE day, never the page, so they
          cannot contradict the pager beneath them. */}
      {sessions.length > 0 ? (
        <div className="momx-history-sessions" data-testid="momx-history-sessions">
          {sessions.map((entry) => (
            <button
              key={entry.name}
              type="button"
              className="momx-session-link"
              title={"Jump to " + entry.name + " - " + entry.rows + " entries that day"}
              onClick={() => {
                const at = sessionStart(payload.timeIndex, payload.sessions, entry.name);
                if (at === null) return;
                onOffset(jumpOffset(at, payload.totalRows, payload.pageSize));
              }}
            >
              {entry.name} <b>{entry.rows}</b>
            </button>
          ))}
        </div>
      ) : null}

      {pager("top")}

      {error ? (
        <p className="momx-banner is-error" role="alert">
          <AlertTriangle size={13} aria-hidden="true" />
          <span>{error}</span>
        </p>
      ) : null}

      <div className="momx-table-scroll">
        <table ref={historyPin.tableRef} className="momx-table momx-history-table" {...historyPin.tableAttrs}>
          <thead>
            <tr className="momx-group-row">
              {historyColumnGroups.map((group) => {
                const pin = group.pinKey ? historyPin.headPin(group.pinKey) : null;
                const base = group.label ? "momx-group-label" : "momx-group-gap";
                return (
                  <th
                    key={group.key}
                    colSpan={group.span}
                    className={pin ? base + " " + pin.className : base}
                    style={pin ? pin.style : undefined}
                    scope={group.span > 1 ? "colgroup" : "col"}
                  >
                    {group.label || ""}
                  </th>
                );
              })}
            </tr>
            <tr className="momx-head-row">
              {historyColumnList.map((column) => {
                // Same affordance as the live board's header, deliberately:
                // two tables that look identical must behave identically.
                const active = historySort.key === column.key;
                const classes = ["momx-th"];
                if (column.sortable) classes.push("is-sortable");
                if (active) classes.push("is-sorted");
                if (column.kind === "color") classes.push("momx-col-color");
                if (column.kind === "symbol") classes.push("momx-col-symbol");
                const movable = Boolean(columnLayout && Array.isArray(columnLayout.order) && columnLayout.order.includes(column.key));
                if (movable) classes.push("is-col-draggable");
                const pin = historyPin.headPin(column.key);
                if (pin) classes.push(pin.className);
                return (
                  <th
                    key={column.key}
                    scope="col"
                    data-col={column.key}
                    className={classes.join(" ")}
                    style={pin ? pin.style : undefined}
                    draggable={movable}
                    {...historyHeader.headerHandlers}
                    aria-sort={active ? (historySort.direction === "asc" ? "ascending" : "descending") : "none"}
                    title={column.kind === "color" ? "COLOR" : undefined}
                    onClick={
                      column.sortable
                        ? () => {
                            if (historyHeader.justDragged()) return;
                            onHistorySort(column);
                          }
                        : undefined
                    }
                  >
                    {column.kind === "color" ? (
                      <span className="momx-visually-hidden">COLOR</span>
                    ) : (
                      <>
                        <span className="momx-th-label">{column.label}</span>
                        {active ? (
                          <span className="momx-sort-arrow">{historySort.direction === "asc" ? "▲" : "▼"}</span>
                        ) : null}
                      </>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr>
                <td className="momx-empty" colSpan={historyColumnList.length}>
                  <span className="momx-empty-inner">{empty}</span>
                </td>
              </tr>
            ) : searching ? (
              dayGroups.map((group) => (
                <Fragment key={group.date || "unknown-day"}>
                  <tr className="momx-history-dayrow">
                    <td colSpan={historyColumnList.length}>{group.label}</td>
                  </tr>
                  {group.rows.map((entry) => (
                    <MomxHistoryRow key={entry.key} entry={entry} newsTime={newsOnly} onOpenSnapshot={onOpenSnapshot} onOpenGrade={onOpenGrade} columns={historyColumnList} />
                  ))}
                </Fragment>
              ))
            ) : (
              entries.map((entry) => <MomxHistoryRow key={entry.key} entry={entry} newsTime={newsOnly} onOpenSnapshot={onOpenSnapshot} onOpenGrade={onOpenGrade} columns={historyColumnList} />)
            )}
          </tbody>
        </table>
      </div>
      <MomxColumnHeaderMenu
        menu={historyHeader.menu}
        columns={historyColumnList}
        layout={columnLayout}
        onChange={onColumnLayoutChange}
        onClose={historyHeader.closeMenu}
      />

      {pager("bottom")}

      <p className="momx-footnote">
        {"Every change the scan saw, one row per snapshot, kept " +
          retention +
          " days. NEW marks a ticker's first match of the day; highlighted cells are what changed. NEWS narrows the page you are on to the entries that had a headline at the time."}
      </p>
    </Fragment>
  );
});

export default function MomxScannerPanel({ initialList = null, embedded = false, background = false }) {
  // `embedded` = this board is inside a pop-out window. It owns no windows of
  // its own (that would recurse), shows no pop-out button, and - the one that
  // silently breaks the main page if forgotten - never writes the shared
  // active-list key, because a Mag7 window mounting would otherwise flip the
  // page behind it to Mag7.
  // `board` is the LIVE payload the last fetch returned; `boardList` records
  // which list it belongs to, so a fetch that is still in flight for the old
  // list cannot be mistaken for the newly selected one. The rows actually shown
  // are decided by decideBoardView (live board vs cached board) further down.
  const [board, setBoard] = useState(null);
  const [boardList, setBoardList] = useState(() => initialList || readStoredList());
  // Seeded from the cache so a cold mount paints the tab bar immediately
  // rather than after the /lists round trip -- which is also what made
  // switching back to this panel feel slow.
  const [lists, setLists] = useState(() => readCachedLists() || EMPTY_ROWS);
  const [activeList, setActiveList] = useState(() => initialList || readStoredList());
  // BULL / BEAR switch (spec 2026-09-24). Every board / momentum / history /
  // grade fetch below carries `dirQuery`, and every board cache key is
  // scoped by it, so the two boards never show under each other's label.
  const [direction, setDirection] = useState(readStoredDirection);
  const bear = direction === "bear";
  const dirQuery = bear ? "&dir=bear" : "";
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  // No full-screen "Loading" when a cached board (memory OR localStorage) can be
  // shown immediately; the fetch then refreshes behind an "updating" note.
  const [loading, setLoading] = useState(() => !hasCachedBoard(boardCacheKey(readStoredList(), readStoredDirection())));
  // True while a forced rebuild is waiting on the worker (POST /rebuild, then
  // poll until generatedAt changes). Kept apart from `loading` so the quiet 3s
  // wait-polls do not flash the board-wide loading state.
  const [rebuilding, setRebuilding] = useState(false);
  const [matchesOnly, setMatchesOnly] = useState(true);
  // TOS checkbox: the worker's data source as last reported by a board
  // payload. Kept apart from `board` because a list switch nulls the board,
  // and the source is global - the checkbox must not vanish on every switch.
  // null = this worker never reported one (older worker): no checkbox at all.
  // Seeded from the last value this browser saw (2026-10-01: during a worker
  // blip the panel got boards without dataSource and the box vanished from
  // his screen); a later board always overrides it.
  const [serverSource, setServerSourceState] = useState(() => {
    try {
      const saved = window.localStorage.getItem("momx-data-source");
      return saved === "tos" || saved === "alpaca" ? saved : null;
    } catch {
      return null;
    }
  });
  const setServerSource = useCallback((value) => {
    setServerSourceState(value);
    try {
      if (value === "tos" || value === "alpaca") window.localStorage.setItem("momx-data-source", value);
    } catch {
      // storage blocked: the box still follows the live boards
    }
  }, []);
  // The value being POSTed (optimistic), null when nothing is in flight.
  const [sourcePending, setSourcePending] = useState(null);
  const sourceHoldRef = useRef(null);
  // When the request behind the board on screen STARTED (see the hold below).
  const boardRequestedAtRef = useRef(0);
  const [boltView, setBoltViewState] = useState(() => {
    try {
      const held = window.localStorage.getItem(BOLT_VIEW_KEY);
      return held === "today" || held === "now" ? held : "all";
    } catch {
      return "all";
    }
  });
  const setBoltView = useCallback((value) => {
    setBoltViewState(value);
    try { window.localStorage.setItem(BOLT_VIEW_KEY, value); } catch { /* session only */ }
  }, []);
  const [hlSort, setHlSortState] = useState(() => {
    try { return window.localStorage.getItem(HL_SORT_KEY) === "1"; } catch { return false; }
  });
  const setHlSort = useCallback((on) => {
    setHlSortState(Boolean(on));
    try { window.localStorage.setItem(HL_SORT_KEY, on ? "1" : "0"); } catch { /* session only */ }
  }, []);
  // {symbol: {on, now, opacity, families, firedAt, hlMomentum}} - identities
  // reused per symbol when unchanged, so only rows whose ⚡ changed re-render.
  const [liveBolts, setLiveBolts] = useState(EMPTY_OBJECT);
  const [liveHl, setLiveHl] = useState(EMPTY_OBJECT);
  const boltCounts = useMemo(() => {
    let today = 0;
    let now = 0;
    // ⚡ TODAY = every bolt that fired today (2026-09-28: "on" dropped to 0 by
    // the evening, hiding the day's 15 bolts); ⚡ NOW = on and passing now.
    for (const item of Object.values(liveBolts)) {
      if (item && item.firedAt) today += 1;
      if (item && item.on && item.now) now += 1;
    }
    return { today, now };
  }, [liveBolts]);
  useEffect(() => {
    if (embedded || direction === "bear") return undefined;
    let cancelled = false;
    let tick = 0;
    let inFlight = false;
    const poll = async () => {
      if (cancelled || inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlight = true;
      const full = tick % 10 === 0;
      tick += 1;
      try {
        const res = await fetch(MOMX_LIVE_BOLTS_ENDPOINT + (full ? "?full=1" : ""), { credentials: "include", cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        const bolts = (data && data.bolts) || {};
        if (cancelled) return;
        setLiveBolts((held) => {
          const next = {};
          let changed = false;
          for (const [symbol, item] of Object.entries(bolts)) {
            if (!item || !item.firedAt) continue;
            const old = held[symbol];
            if (old && JSON.stringify(old) === JSON.stringify(item)) next[symbol] = old;
            else { next[symbol] = item; changed = true; }
          }
          if (!changed && Object.keys(next).length === Object.keys(held).length) return held;
          return next;
        });
        if (full) {
          const hl = {};
          for (const [symbol, item] of Object.entries(bolts)) {
            const v = Number(item && item.hlMomentum);
            if (Number.isFinite(v)) hl[symbol] = v;
          }
          setLiveHl(hl);
        }
      } catch {
        /* stream or server down: keep the last state */
      } finally {
        inFlight = false;
      }
    };
    poll();
    const timer = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [embedded, direction]);
  // NEWS narrows the board to rows with a fresh headline. Deliberately NOT
  // remembered across reloads: a filter he forgot he left on would make the
  // board look like the scan found nothing.
  // The live Time column, remembered across reloads.
  const [showTimeColumn, setShowTimeColumn] = useState(readStoredShowTime);
  // NEWS pressed or not. Not persisted: it is a "show me the catalysts right
  // now" look, not a way he wants the board to open tomorrow.
  const [newsOnly, setNewsOnly] = useState(false);
  // FILTERS: the trader's second gate (RVOL / SQZ / Skittles), ANY or ALL of
  // the following - his choice at the top of the panel since 2026-09-22. The
  // CONFIG (that choice included) is remembered - it is his tuning, and
  // re-ticking seven boxes every morning is exactly the guesswork this
  // replaces. Whether the filter is PRESSED is not, for the same reason NEWS
  // is not: a filter he forgot he left on makes the scan look broken.
  const [filters, setFilters] = useState(readStoredFilters);
  const [filtersOn, setFiltersOn] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // ---- back to top -------------------------------------------------------
  // He reported the board scrolled down and refusing to come back up on his
  // iPhone (2026-09-05). It could NOT be reproduced here: Chromium's iPhone
  // emulation recovers from synthesized touch swipes every time, panned
  // sideways or not; the 15s refresh does not drag the position back over a
  // full minute; the document itself never scrolls; and WebKit cannot
  // synthesize a native gesture at all, so Safari's engine could not be
  // driven. Five experiments, no repro.
  //
  // So this is NOT a root-cause fix and must not be recorded as one. It is an
  // escape: a programmatic scroll works even when the gesture path is wedged,
  // so he always has a way back to the toolbar. If the underlying wedge is
  // ever found, this button is still worth keeping - the board is 26 columns
  // wide and getting home is a real thing to want.
  const panelRef = useRef(null);
  const scrollerRef = useRef(null);
  const [scrolledDown, setScrolledDown] = useState(false);
  const scrolledDownRef = useRef(false);

  useEffect(() => {
    const node = panelRef.current;
    if (!node) return undefined;
    // Walk UP to the real scroller rather than querying by class: the popout
    // window renders a second element with the same class, and a document-wide
    // query would wire this button to the wrong board.
    let scroller = node.parentElement;
    while (scroller && scroller !== document.body) {
      const style = window.getComputedStyle(scroller);
      if (/(auto|scroll)/.test(style.overflowY)) break;
      scroller = scroller.parentElement;
    }
    if (!scroller || scroller === document.body) return undefined;
    scrollerRef.current = scroller;
    // Only touch React state when the answer flips. Calling the setter on
    // every scroll event - even with the same boolean - still ran this whole
    // component on each one (React's same-value bail-out only applies when
    // nothing else is queued), and that render was the long task behind
    // "scroll is very lagging" on the History table (measured 2026-09-05:
    // 4 panel renders + 3 full table rebuilds in one thumb swipe).
    const onScroll = () => {
      const down = scroller.scrollTop > 80;
      if (down === scrolledDownRef.current) return;
      scrolledDownRef.current = down;
      setScrolledDown(down);
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, []);

  const scrollToTop = useCallback(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    // Instant, not smooth: if a gesture is already wedged, an animated scroll
    // is the wrong thing to bet the rescue on.
    scroller.scrollTop = 0;
    setScrolledDown(false);
  }, []);
  useEffect(() => { writeStoredFilters(filters); }, [filters]);
  useEffect(() => { writeStoredShowTime(showTimeColumn); }, [showTimeColumn]);
  // Column order and show/hide (the Columns button), saved per device. One
  // layout for the Live board and the History table.
  const [columnLayout, setColumnLayout] = useState(() => readStoredLayout(MOMX_COLUMN_KEYS));
  const [columnsOpen, setColumnsOpen] = useState(false);
  useEffect(() => { writeStoredLayout(columnLayout); }, [columnLayout]);
  const [showIndustries, setShowIndustries] = useState(readStoredShowIndustries);
  useEffect(() => { writeStoredShowIndustries(showIndustries); }, [showIndustries]);
  const [showSectors, setShowSectors] = useState(readStoredShowSectors);
  useEffect(() => { writeStoredShowSectors(showSectors); }, [showSectors]);
  // TOS colour link to the charts (see MOMX_LINK_STORAGE_KEY). A window's
  // list is fixed (initialList), so its key never changes under it.
  const linkKey = linkStorageKey(embedded, initialList);
  const [linkGroupValue, setLinkGroupValue] = useState(() => readStoredLinkGroup(linkKey));
  useEffect(() => { writeStoredLinkGroup(linkKey, linkGroupValue); }, [linkKey, linkGroupValue]);
  const linkGroupRef = useRef(linkGroupValue);
  linkGroupRef.current = linkGroupValue;
  const openChartLinked = useCallback((symbol) => {
    sendTickerToCharts(symbol, linkGroupRef.current);
  }, []);
  const chartLink = useMemo(
    () => ({ openChart: openChartLinked, group: tosLinkGroup(linkGroupValue, 1) }),
    [openChartLinked, linkGroupValue],
  );
  // ONE decision, used by the group header, the column header AND the body -
  // three places that must agree on the column count or the grouped header
  // slips a cell out of alignment.
  // The Time column is forced on while NEWS is pressed, whatever the tickbox
  // says: in that view it carries the headline time, which is the whole point
  // of pressing NEWS. The tickbox itself is left alone, so unpressing NEWS
  // puts the board back exactly as he had it.
  // The Columns manager's order/hidden layout is applied on top; memoised so
  // the memo'd rows get ONE `columns` reference until the layout or the Time
  // switch changes (never a fresh array per poll).
  const liveColumnSource = showTimeColumn || newsOnly ? MOMX_LIVE_COLUMNS : MOMX_COLUMNS;
  const liveColumnList = useMemo(() => applyLayout(liveColumnSource, columnLayout), [liveColumnSource, columnLayout]);
  // Frozen Symbol | Setup | Time block (usePinnedColumns): offsets are CSS
  // variables on the table, so the memo'd rows never re-render for them.
  const livePin = usePinnedColumns(liveColumnList);
  const liveColumnGroups = useMemo(
    () => columnGroupSpans(liveColumnList, livePin.pinned),
    [liveColumnList, livePin.pinned],
  );
  // Drag a header to move it, right-click it to hide / move / show all -
  // the same layout the Columns dialog edits (MomxColumnHeaderMenu.jsx).
  const liveHeader = useColumnHeaderControls(columnLayout, setColumnLayout);
  // The board OPENS sorted by High/Low descending (stocks nearest their N-bar
  // high on top), per the trader (2026-08-28, "keep high/low sort by default").
  // MOMX_DEFAULT_SORT stays the generic sortRows fallback (%chg); this is only
  // the panel's initial view, and clicking any header still re-sorts.
  const [liveSort, setLiveSort] = useState(() => (readStoredDirection() === "bear" ? MOMX_BEAR_DEFAULT_SORT : MOMX_LIVE_DEFAULT_SORT));
  // Flipping BULL <-> BEAR re-seats the H/L sort for that side (lows first on
  // the bear board); a header click after that still wins until the next flip.
  useEffect(() => {
    setLiveSort(bear ? MOMX_BEAR_DEFAULT_SORT : MOMX_LIVE_DEFAULT_SORT);
  }, [bear]);
  // The NEWS view keeps its OWN order - newest headline first - so pressing
  // NEWS does not disturb whatever he sorted the live board by, and unpressing
  // it gives that order straight back. Header clicks write to whichever one is
  // showing.
  const [newsSort, setNewsSort] = useState(MOMX_NEWS_DEFAULT_SORT);
  const rawSort = newsOnly ? newsSort : liveSort;
  const setSort = newsOnly ? setNewsSort : setLiveSort;
  const sortDefault = newsOnly ? MOMX_NEWS_DEFAULT_SORT : (bear ? MOMX_BEAR_DEFAULT_SORT : MOMX_LIVE_DEFAULT_SORT);
  // The saved sort key can point at a column the trader just hid (the sort
  // state and the column layout are saved independently). ONE effective sort
  // drives BOTH the row order (visibleRows below) and the header arrow, so
  // they never disagree.
  const sort = useMemo(
    () => effectiveSort(rawSort, liveColumnList, sortDefault),
    [rawSort, liveColumnList, sortDefault],
  );
  const [selectedIndustries, setSelectedIndustries] = useState(EMPTY_SELECTION);
  const [universeText, setUniverseText] = useState("");
  const [universeBusy, setUniverseBusy] = useState(false);
  // Two-tap arming for the two destructive controls. See onSubmitUniverse for
  // why these are not window.confirm() calls.
  const [setArmed, setSetArmed] = useState(false);
  const [restoreArmed, setRestoreArmed] = useState(false);
  // Offered everywhere now that the pop-out is an in-app overlay instead of
  // an OS window. The old desktop-only gate existed because iOS/Android turn
  // window.open into a background tab; that objection does not survive a
  // pop-out that never leaves the page, and desktop and phone are meant to
  // look the same. Still hidden inside the detached ?popout= window, where a
  // button that re-opens the window you are already in is just confusing.
  const [canPopOut] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return !new URLSearchParams(window.location.search).get("popout");
    } catch {
      return true;
    }
  });
  // The open pop-out windows: [{ id, kind, list, symbol?, rect, z }]. A LIST,
  // not a boolean -
  // he runs two thinkorswim watchlist windows side by side on different lists
  // and wants the same here. Never populated for an embedded board.
  // Kept for the SESSION across unmounts (2026-09-26 review): a TOS-link
  // ticker click switches the app to Charts & OI, which unmounts this panel;
  // the windows he had open come back when he returns to the scanner.
  const [popouts, setPopouts] = useState(() => (embedded ? EMPTY_ROWS : SESSION_POPOUTS.list));
  useEffect(() => {
    if (!embedded) SESSION_POPOUTS.list = popouts;
  }, [embedded, popouts]);
  // MomoX-style pop-up cards (2026-09-25). On by default; remembered per
  // browser. Which tickers already popped today is remembered too, so a
  // reload never re-pops the morning's cards.
  const [autoCards, setAutoCardsState] = useState(() => {
    try {
      return window.localStorage.getItem(AUTO_CARDS_PREF_KEY) !== "0";
    } catch {
      return true;
    }
  });
  const setAutoCards = useCallback((on) => {
    setAutoCardsState(Boolean(on));
    try {
      window.localStorage.setItem(AUTO_CARDS_PREF_KEY, on ? "1" : "0");
    } catch {
      /* private window: the choice lasts this session */
    }
  }, []);
  const popoutDragRef = useRef(null);
  // Continue after any windows restored from SESSION_POPOUTS, so a new
  // window never reuses a restored window's id or sits under it.
  const popoutSeqRef = useRef(popouts.reduce((top, win) => {
    const n = Number(String(win.id || "").split("-").pop());
    return Number.isFinite(n) && n > top ? n : top;
  }, 0));
  const popoutTopRef = useRef(popouts.reduce((top, win) => (Number(win.z) > top ? Number(win.z) : top), 10));
  const [pickerOpen, setPickerOpen] = useState(false);
  // The list button the picker hangs off - see MomxListPicker.
  const listBtnRef = useRef(null);
  const [listBusy, setListBusy] = useState(false);
  const [listError, setListError] = useState("");
  const [maxLists, setMaxLists] = useState(6);
  const [momentumEvents, setMomentumEvents] = useState(EMPTY_ROWS);
  // Event column: the app's earnings calendar, refreshed every 30 minutes
  // (the server caches it; a failed fetch keeps the last map).
  const [earningsMap, setEarningsMap] = useState(EARNINGS_BY_SYMBOL);
  useEffect(() => {
    let alive = true;
    const load = () => {
      fetch("/api/earnings-calendar?days=45")
        .then((response) => (response.ok ? response.json() : null))
        .then((payload) => {
          if (!alive || !payload) return;
          const next = earningsMapFrom(payload);
          if (next.size === 0 && EARNINGS_BY_SYMBOL.size > 0) return;
          EARNINGS_BY_SYMBOL = next;
          setEarningsMap(next);
        })
        .catch(() => {});
    };
    load();
    const timer = setInterval(load, 30 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  // The clock the age buckets read. Bumped by the momentum poll (not by a
  // dedicated timer) so a chip's fade and a row's NEW flash expire even when
  // the payload itself stops changing.
  const [momentumNow, setMomentumNow] = useState(() => Date.now());
  // A strip chip was clicked: show only this symbol in the table.
  const [focusSymbol, setFocusSymbol] = useState(null);
  // Momo Alert settings popover (the toolbar gear).
  const [momoSettingsOpen, setMomoSettingsOpen] = useState(false);
  // The open news popover, keyed by SYMBOL in the parent so MomxRow's memo
  // never sees popover state. The toggle has an empty dep list on purpose -
  // a stable identity is what lets every row keep it without re-rendering.
  const [newsOpenSymbol, setNewsOpenSymbol] = useState(null);
  const onNewsToggle = useCallback((symbol) => {
    setNewsOpenSymbol((current) => (current === symbol ? null : symbol));
  }, []);
  const onNewsClose = useCallback(() => setNewsOpenSymbol(null), []);

  const mountedRef = useRef(true);
  // The board's tickers and the store refresh, as refs: forceRebuild (below)
  // fires the news refresh after a SCAN lands and must not re-create itself
  // every time the board changes.
  const newsSymbolsRef = useRef([]);
  const refreshNewsFeedRef = useRef(null);
  // Holds the CURRENT request's promise, not just a boolean, so an explicit
  // refresh can queue behind an in-flight poll instead of racing it, while a
  // poll tick that lands on a slow request is dropped outright.
  const inFlightRef = useRef(null);
  // generatedAt of the newest LIVE payload runFetch accepted. The rebuild wait
  // loop compares against this to know when the worker finished a fresh build.
  const liveGeneratedAtRef = useRef(null);
  // Monotonic token: a second refresh click invalidates the first click's wait
  // loop, so two loops never fight over the spinner.
  const rebuildTokenRef = useRef(0);
  const listsInFlightRef = useRef(false);
  const momentumInFlightRef = useRef(false);
  // True while anything on screen can still age (a chip fading, a NEW flash
  // expiring). Read to skip the clock bump when the strip is empty, so an
  // unwired endpoint costs zero re-renders.
  const momentumAliveRef = useRef(false);

  // ---- history (30-day archive) ------------------------------------------
  // History fetches ONLY while a history tab is showing, once per entry
  // into a (list, day) or (list, search) view, with a manual refresh button.
  // The archive adds ZERO polling load on top of the live board - it is an
  // archive, not a feed. PAST days are immutable, so their payloads are held
  // in a Map and flipping back to them costs nothing; views that can still
  // grow while the scan runs (today, newest day, a ticker search) refetch on
  // each entry instead of silently serving the morning's snapshot of today.
  const [historyView, setHistoryView] = useState(() => readStoredView() === "history");
  const [historyDate, setHistoryDate] = useState(null); // null = newest archived day
  // Rows back from the NEWEST snapshot; 0 is page one. Rows, not pages, so the
  // server never has to agree with this component about a page size.
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historySearch, setHistorySearch] = useState(""); // the raw input box
  const [historyQuery, setHistoryQuery] = useState(""); // debounced, committed
  const [historyPayload, setHistoryPayload] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  // ---- FIND: "which tickers ever hit this" over the 30-day archive --------
  // Deliberately NOT persisted. A saved search is a filter he forgot he left
  // on, and the History tab already has three of those; FIND opens clean and
  // its result is dismissed with one button.
  const [findOpen, setFindOpen] = useState(false);
  const [findState, setFindState] = useState(() =>
    JSON.parse(JSON.stringify(DEFAULT_HISTORY_QUERY)),
  );
  const [findResult, setFindResult] = useState(null);
  const [findBusy, setFindBusy] = useState(false);
  const [findError, setFindError] = useState("");

  const historyCacheRef = useRef(new Map());
  const historySeqRef = useRef(0);

  useEffect(() => {
    writeStoredView(historyView ? "history" : "live");
  }, [historyView]);

  // Debounce the search box into the committed query so each keystroke does
  // not fire its own fetch; clearing the box returns to the selected day.
  useEffect(() => {
    const trimmed = historySearch.trim().toUpperCase();
    const timer = window.setTimeout(() => setHistoryQuery(trimmed), 300);
    return () => window.clearTimeout(timer);
  }, [historySearch]);

  // A new day or a new search is a new list of rows, so go back to page one.
  // Without this, stepping to page 3 and then choosing a two-page day leaves an
  // empty table under a pager reading "no rows on this page of N" - honest and
  // useless.
  useEffect(() => {
    setHistoryOffset(0);
    // ...and drop any FIND result. The list picker and the Live/History tabs
    // sit ABOVE the view branch and stay clickable while results are up, so
    // without this the old board's tickers sit under the new board's name -
    // and the results bar never prints which board they came from.
    setFindResult(null);
    setFindError("");
  }, [historyDate, historyQuery, activeList]);

  const runFind = useCallback(async () => {
    const params = historyQueryParams(findState, {
      list: activeList,
      direction,
      // historyDate is null until a day arrow is pressed, and a missing date
      // means "every day" to the server - so "this day" silently searched the
      // whole archive while the footer still said "on this day". Measured:
      // 51 tickers on one day vs 137 across six. Fall back to the day the
      // payload on screen is actually showing, the same resolution the
      // history section itself uses.
      date:
        historyDate ||
        (historyPayload && historyPayload.date) ||
        // A ticker search across "All days" has no date on its payload; "this
        // day" then means the newest archived day, the one the day picker
        // would show, not silently the whole archive.
        (historyPayload && Array.isArray(historyPayload.days) && historyPayload.days[0]) ||
        null,
    });
    // buildCondition already refused, so the button was disabled - this is the
    // belt to that brace, never a silent no-op.
    if (!params) {
      setFindError(historyQueryProblem(findState));
      return;
    }
    setFindBusy(true);
    setFindError("");
    try {
      const response = await fetch(
        "/api/momx-scanner/history-query?" + params.toString(),
        { cache: "no-store" },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        // The server answers 400 for a condition it cannot apply. "Nothing
        // matched" and "I did not understand you" must never look the same in
        // a search, so the error surfaces instead of an empty table.
        // A 404 means one specific, fixable thing: the scanner worker is
        // running code from before this feature existed. Say that, rather
        // than "Not Found", which tells him nothing he can act on.
        if (response.status === 404) {
          throw new Error(
            "The scanner worker has not picked up this feature yet - it needs"
            + " restarting once. Everything else keeps working meanwhile.",
          );
        }
        throw new Error((payload && payload.error) || "The search could not run.");
      }
      // api_server answers ANY proxy failure - worker restarting, upstream
      // timeout - with HTTP 200 {"warming": true, rows: []}. Without this
      // guard that renders as "No archived days to search yet." while six
      // days and 78 MB sit on disk: a truthful-looking answer to a question
      // that was never asked. Exactly the failure this project keeps hitting.
      if (!payload || payload.warming || !Array.isArray(payload.results)) {
        throw new Error(
          "The scanner is still starting up - try the search again in a moment.",
        );
      }
      setFindResult(payload);
      setFindOpen(false);
    } catch (error) {
      setFindError(String((error && error.message) || error));
    } finally {
      setFindBusy(false);
    }
  }, [findState, activeList, historyDate, historyPayload]);

  const loadHistory = useCallback(
    async (force) => {
      if (!activeList) return;
      const request = historyRequest(activeList, {
        date: historyDate,
        symbol: historyQuery,
        offset: historyOffset,
        direction,
      });
      // Only a PAST day is immutable; today / newest / search keep growing.
      const immutable =
        request.mode === "day" && Boolean(historyDate) && historyDate !== etDayIso();
      if (!force && immutable) {
        const held = historyCacheRef.current.get(request.key);
        if (held) {
          setHistoryPayload(held);
          setHistoryError("");
          return;
        }
      }
      const seq = ++historySeqRef.current;
      setHistoryLoading(true);
      try {
        const response = await fetch(request.url, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(
            response.status === 404
              ? "The scanner worker does not have the history feature yet."
              : "History answered with an error (" + response.status + ").",
          );
        }
        const payload = await response.json();
        if (!mountedRef.current || seq !== historySeqRef.current) return;
        if (!payload || typeof payload !== "object") {
          throw new Error("History sent a reply this page could not read.");
        }
        historyCacheRef.current.set(request.key, payload);
        setHistoryPayload(payload);
        setHistoryError("");
      } catch (caught) {
        if (!mountedRef.current || seq !== historySeqRef.current) return;
        setHistoryError(
          caught && caught.message ? caught.message : "Could not load the scanner history.",
        );
      } finally {
        if (mountedRef.current && seq === historySeqRef.current) setHistoryLoading(false);
      }
    },
    [activeList, historyDate, historyQuery, historyOffset, direction],
  );

  useEffect(() => {
    if (!historyView) return;
    loadHistory(false);
  }, [historyView, loadHistory]);

  // Stable so the memoized History section is not handed a fresh function on
  // every panel render (which would defeat the memo).
  const onOpenFind = useCallback(() => { setFindError(""); setFindOpen(true); }, []);
  const onCloseFind = useCallback(() => { setFindResult(null); setFindError(""); }, []);

  const onHistoryRefresh = useCallback(() => {
    loadHistory(true);
  }, [loadHistory]);

  // ---- watchlist tabs ----------------------------------------------------
  // The lists endpoint is metadata only (name/count/builtAt/warming), so it is
  // cheap enough to poll beside the board; that is what stops a "building" tab
  // from reading as building forever after its first build lands. A failure
  // here is SILENT: if the endpoint is not deployed the panel falls back to the
  // server's own active list and shows no tabs at all, which is strictly better
  // than an error banner over a board that is working fine.
  const loadLists = useCallback(async () => {
    if (listsInFlightRef.current) return;
    listsInFlightRef.current = true;
    try {
      const response = await fetch(MOMX_LISTS_ENDPOINT, { cache: "no-store" });
      if (!response.ok) throw new Error("lists unavailable");
      const payload = await response.json();
      if (!mountedRef.current) return;
      const items = Array.isArray(payload && payload.lists)
        ? payload.lists.filter((item) => item && typeof item.name === "string" && item.name)
        : [];
      // An empty response means "the worker has not registered its lists yet",
      // not "there are no lists". Keeping what is on screen is the honest
      // reading and the one that does not strand him without tabs.
      if (items.length > 0) {
        setLists(items);
        writeCachedLists(items);
      }
      if (Number.isFinite(payload && payload.maxLists)) setMaxLists(payload.maxLists);
      setActiveList((current) => {
        const known = (name) => items.some((item) => item.name === name);
        if (current && known(current)) return current;
        const stored = readStoredList();
        if (stored && known(stored)) return stored;
        if (payload && typeof payload.active === "string" && known(payload.active)) {
          return payload.active;
        }
        return items.length > 0 ? items[0].name : null;
      });
    } catch {
      // KEEP the tabs. A worker restart, a request aborted while switching
      // panels, or a momentary network blip must not delete the only
      // navigation on the page. He hit this repeatedly on 2026-09-01 because
      // the scanner worker was restarted many times that day.
    } finally {
      listsInFlightRef.current = false;
    }
  }, []);

  useEffect(() => {
    // Never from inside a pop-out window: see `embedded` above.
    if (!embedded && activeList) writeStoredList(activeList);
  }, [activeList, embedded]);

  useEffect(() => {
    if (!embedded) writeStoredDirection(direction);
  }, [direction, embedded]);

  // Flipping the switch must never leave the other board on screen under the
  // new label: drop the live payload at once; the cached board for the new
  // direction (if any) shows while the fetch is in flight.
  useEffect(() => {
    setBoard(null);
  }, [direction]);

  const runFetch = useCallback(async (quiet, listName) => {
    if (!quiet && mountedRef.current) setLoading(true);
    try {
      const url = (listName ? MOMX_ENDPOINT + "?list=" + encodeURIComponent(listName) : MOMX_ENDPOINT + "?")
        + dirQuery;
      const requestedAt = Date.now();
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) {
        if (response.status === 400) {
          // The remembered tab is not a list the server knows. Drop it so the
          // next lists poll can re-seat the selection, instead of looping on
          // 400s forever against a name that no longer exists.
          if (mountedRef.current) setActiveList(null);
          throw new Error(
            "That watchlist is not on the server any more, so the default list is being shown.",
          );
        }
        throw new Error(
          response.status === 404
            ? "The MomX scanner service is not running yet, so there is nothing to show."
            : "The MomX scanner service answered with an error (" + response.status + ").",
        );
      }
      const payload = await response.json();
      if (!mountedRef.current) return;
      if (!payload || typeof payload !== "object") {
        throw new Error("The MomX scanner sent a reply this page could not read.");
      }
      const cacheKey = boardCacheKey(listName || readStoredList(), direction);
      // Cache only a board that HAS rows; a warming/empty answer must not wipe
      // the last good tickers (governing rule). The live payload is still kept
      // in `board` so the warming banner and per-symbol errors stay accurate.
      // The bear board is cached under its own key (spec 2026-09-24).
      writeCachedBoard(cacheKey, payload);
      boardRequestedAtRef.current = requestedAt;
      setBoard(payload);
      setBoardList(cacheKey);
      liveGeneratedAtRef.current = payload.generatedAt || null;
      setError("");
    } catch (caught) {
      if (!mountedRef.current) return;
      // Never blank the board on a failed refresh: the last good rows stay on
      // screen and the banner says why they may be stale.
      setError(
        caught && caught.message
          ? caught.message
          : "Could not reach the MomX scanner. The figures below may be out of date.",
      );
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [direction, dirQuery]);

  const load = useCallback(
    async (options) => {
      const quiet = Boolean(options && options.quiet);
      const queue = Boolean(options && options.queue);
      if (inFlightRef.current) {
        if (!queue) return;
        await inFlightRef.current.catch(() => {});
      }
      const request = runFetch(quiet, activeList);
      inFlightRef.current = request;
      try {
        await request;
      } finally {
        if (inFlightRef.current === request) inFlightRef.current = null;
      }
    },
    [runFetch, activeList],
  );

  // The refresh and SCAN buttons. They used to call load(), which only
  // re-downloads the worker's CACHED board - the scan itself never re-ran, so
  // the click looked dead. Now: POST /rebuild for the active list, remember
  // the generatedAt baseline, then re-fetch the board every 3s (via the same
  // load()/inFlight machinery as the poll) until generatedAt changes. On
  // timeout the spinner just stops - the last good rows stay on screen, no
  // banner. A worker without the /rebuild route falls back to the old load().
  const forceRebuild = useCallback(async () => {
    const token = ++rebuildTokenRef.current;
    setRebuilding(true);
    try {
      let baseline = liveGeneratedAtRef.current;
      let accepted = false;
      try {
        const body = activeList ? { list: activeList } : {};
        const response = await fetch(MOMX_REBUILD_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify(body),
        });
        if (response.ok) {
          accepted = true;
          const payload = await response.json().catch(() => null);
          // The worker answers with the generatedAt of the build it is about
          // to replace - the exact baseline "has it changed yet?" needs.
          if (payload && payload.generatedAt) baseline = payload.generatedAt;
        }
      } catch {
        // Network failure: treated like the missing route below.
      }
      if (!accepted) {
        await load({ quiet: false, queue: true });
        return;
      }
      const deadline = Date.now() + MOMX_REBUILD_WAIT_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, MOMX_REBUILD_POLL_MS));
        if (!mountedRef.current || rebuildTokenRef.current !== token) return;
        await load({ quiet: true, queue: true });
        if (!mountedRef.current || rebuildTokenRef.current !== token) return;
        const current = liveGeneratedAtRef.current;
        if (current && current !== baseline) {
          // The SCAN landed: refresh the news store for the scanned tickers
          // in the background. A tick later, so the refs see the new rows.
          // Never awaited - news can neither block nor alter the scan.
          window.setTimeout(() => {
            const refresh = refreshNewsFeedRef.current;
            if (mountedRef.current && refresh) refresh(newsSymbolsRef.current);
          }, 400);
          return;
        }
      }
      // Timed out quietly; the 15s poll will adopt the build whenever it lands.
    } finally {
      if (mountedRef.current && rebuildTokenRef.current === token) setRebuilding(false);
    }
  }, [activeList, load]);

  // Adopt the data source each board reports. The worker stamps dataSource at
  // SERVE time (momx/service.py _with_data_source), so the first board
  // REQUESTED after the switch's POST resolved already carries the new value.
  // The only stale answer is a poll that was already in flight: ignore a
  // board whose request started before the switch, adopt anything after -
  // including a later switch back by another admin or tab (a time-based hold
  // pinned the box to the wrong value for minutes in that case).
  useEffect(() => {
    const reported = board && board.dataSource;
    if (reported !== "tos" && reported !== "alpaca") return;
    const hold = sourceHoldRef.current;
    if (hold) {
      if (reported !== hold.value && boardRequestedAtRef.current < hold.since) return;
      sourceHoldRef.current = null;
    }
    setServerSource(reported);
  }, [board]);

  // The TOS checkbox. POST the new source (worker saves it and queues a
  // rebuild), then run the Refresh button's rebuild-and-wait so the new build
  // is picked up as soon as it lands. Any failure reverts the box.
  const onDataSourceChange = useCallback(
    async (checked) => {
      if (sourcePending) return;
      const next = checked ? "tos" : "alpaca";
      setSourcePending(next);
      setNotice("");
      try {
        let response;
        try {
          response = await fetch(MOMX_DATA_SOURCE_ENDPOINT, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "include",
            cache: "no-store",
            body: JSON.stringify({ source: next }),
          });
        } catch {
          throw new Error("Could not reach the MomX scanner. The data source was not changed.");
        }
        if (!response.ok) {
          throw new Error(
            response.status === 403
              ? "Only an admin can change the data source."
              : response.status === 404
                ? "This copy of the scanner service cannot switch data source yet."
                : "The data source was not changed (the service answered " + response.status + ").",
          );
        }
        const saved = await response.json().catch(() => null);
        const applied = saved && (saved.source === "tos" || saved.source === "alpaca") ? saved.source : next;
        if (!mountedRef.current) return;
        sourceHoldRef.current = { value: applied, since: Date.now() };
        setServerSource(applied);
        setError("");
        setNotice(
          applied === "tos"
            ? "Switched to TOS data. Rebuilding the board - the full list fills in over a few minutes."
            : "Switched to Alpaca data. Rebuilding the board.",
        );
        forceRebuild();
      } catch (caught) {
        if (!mountedRef.current) return;
        setError(caught && caught.message ? caught.message : "The data source was not changed.");
      } finally {
        if (mountedRef.current) setSourcePending(null);
      }
    },
    [forceRebuild, sourcePending],
  );

  useEffect(() => {
    mountedRef.current = true;
    loadLists();
    // queue:true matters on a TAB SWITCH, which re-runs this effect with a new
    // `load`: without it, a switch made while the board poll happened to be in
    // flight would be dropped by the in-flight guard and the trader would stare
    // at the other list's rows until the next tick.
    load({ quiet: false, queue: true });

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        loadLists();
        load({ quiet: true });
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      mountedRef.current = false;
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [load, loadLists]);

  // The repeating refresh, kept in its OWN effect so that changing its period
  // does not re-run the one above. That effect fires load({quiet:false}) on
  // entry, so folding `background` into it would flash a loading state every
  // time he clicked between windows - a raise changes the stacking order,
  // which changes `background`.
  useEffect(() => {
    const period = background ? MOMX_BACKGROUND_POLL_MS : MOMX_POLL_MS;
    const tick = () => {
      // A hidden tab still holds the interval but skips the work: a background
      // window must not keep the backend rebuilding the board.
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      loadLists();
      load({ quiet: true });
    };
    const timer = window.setInterval(tick, period);
    return () => window.clearInterval(timer);
  }, [load, loadLists, background]);

  // ---- momentum events ---------------------------------------------------
  // Every failure is SILENT by contract: the endpoint is not wired on every
  // deployment yet, so a 404, a network error or an unreadable payload all
  // resolve to "no events" and the strip simply does not render. No banner.
  // Scoped to the OPEN tab, like the board fetch. Without ?list= the worker
  // answered for its own active list, so the Watchlist tab showed the scan
  // list's chips (2026-09-01: ten LOST chips over a one-ticker watchlist).
  const loadMomentum = useCallback(async () => {
    if (momentumInFlightRef.current) return;
    momentumInFlightRef.current = true;
    let events = EMPTY_ROWS;
    try {
      const url = (activeList
        ? MOMX_MOMENTUM_ENDPOINT + "?list=" + encodeURIComponent(activeList)
        : MOMX_MOMENTUM_ENDPOINT + "?") + dirQuery;
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        const payload = await response.json();
        if (Array.isArray(payload && payload.events) && payload.events.length > 0) {
          events = payload.events;
        }
      }
    } catch {
      /* silent: an unwired endpoint is a normal state, not an error */
    } finally {
      momentumInFlightRef.current = false;
      if (mountedRef.current) {
        setMomentumEvents(events);
        // Advance the age clock only while something on screen can still age;
        // a permanently-empty feed then never re-renders the panel.
        if (events.length > 0 || momentumAliveRef.current) setMomentumNow(Date.now());
        momentumAliveRef.current = events.length > 0;
      }
    }
  }, [activeList, dirQuery]);

  // Clear is optimistic: the chips go now, and the worker forgets them so
  // the next 15s poll does not bring them back. Silent on failure, like
  // the poll itself.
  const clearMomentum = useCallback(async () => {
    setMomentumEvents(EMPTY_ROWS);
    setFocusSymbol(null);
    try {
      await fetch(MOMX_MOMENTUM_ENDPOINT + "/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(activeList ? { list: activeList } : {}),
      });
    } catch {
      /* the strip is already empty on screen; the poll will re-sync */
    }
  }, [activeList]);

  useEffect(() => {
    loadMomentum();
    const tick = () => {
      // Same discipline as the board poll: a hidden tab holds the interval but
      // does no work, and an in-flight request is never stacked on.
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      loadMomentum();
    };
    const timer = window.setInterval(tick, MOMX_MOMENTUM_POLL_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") loadMomentum();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [loadMomentum]);

  // Capped/deduped/newest-first once per payload; the "+N more" tail comes
  // with it. Depends only on the events array, so a clock bump does not
  // rebuild it.
  const momentumStrip = useMemo(() => capMomentumEvents(momentumEvents), [momentumEvents]);

  // Symbols whose new_match is under ~2 minutes old, for the table flash. The
  // Set is rebuilt each poll tick, but rows receive only their own BOOLEAN, so
  // the new identity stops at this memo instead of re-rendering 358 rows.
  const freshMatchSet = useMemo(
    () => newMatchSymbols(momentumEvents, momentumNow),
    [momentumEvents, momentumNow],
  );

  // One helper for all three list-management writes: they differ only in the
  // endpoint, the body and the sentence afterwards. Each POSTs, reads the
  // {ok:false} body the worker returns with a 200 status (checking response.ok
  // alone reported success for a save that never happened), then refreshes the
  // list metadata and the board.
  const listAction = useCallback(
    async (endpoint, body, describe) => {
      if (listBusy) return;
      setListBusy(true);
      setListError("");
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify(body),
        });
        if (!response.ok) {
          throw new Error(
            response.status === 404
              ? "This copy of the scanner service does not support named lists yet."
              : "That did not work (the service answered " + response.status + ").",
          );
        }
        const payload = await response.json().catch(() => null);
        if (payload && payload.ok === false) {
          throw new Error(payload.error || "That did not work. Nothing was changed.");
        }
        if (!mountedRef.current) return;
        if (payload && typeof payload.list === "string" && payload.list) {
          setActiveList(payload.list);
        } else if (body.deletedFallback) {
          setActiveList(payload && payload.active ? payload.active : null);
        }
        setNotice(describe(payload || {}));
        await loadLists();
        await load({ quiet: true, queue: true });
      } catch (caught) {
        if (!mountedRef.current) return;
        setListError(
          caught && caught.message ? caught.message : "That did not work. Nothing was changed.",
        );
      } finally {
        if (mountedRef.current) setListBusy(false);
      }
    },
    [listBusy, load, loadLists],
  );

  const onCreateList = useCallback(
    (name) =>
      listAction(
        MOMX_LIST_CREATE_ENDPOINT,
        { name },
        (payload) =>
          "Created " +
          (payload.list || name) +
          ". It is empty - use Add to put tickers in it.",
      ),
    [listAction],
  );

  const onRenameList = useCallback(
    (from, to) =>
      listAction(MOMX_LIST_RENAME_ENDPOINT, { list: from, name: to }, (payload) =>
        "Renamed " + (payload.renamedFrom || from) + " to " + (payload.list || to) + ".",
      ),
    [listAction],
  );

  const onDeleteList = useCallback(
    (name) =>
      listAction(
        MOMX_LIST_DELETE_ENDPOINT,
        { list: name, deletedFallback: true },
        () => "Deleted " + name + ".",
      ),
    [listAction],
  );

  // Blow the board up to fill the app, IN the app. This used to be
  // window.open(..., "popup=yes") onto ?popout=momx, which Chrome was
  // serving him as an ordinary tab - losing the app, and the installed PWA
  // with it (2026-09-02: "i want app popup not with chrome"). Nothing is
  // opened now; the same panel is portalled into a fixed overlay. See the
  // render for why a portal and not position:fixed.
  //
  // The ?popout=momx URL still renders standalone for anyone holding that
  // link, and the chart/mag7/chain pop-outs are untouched.
  const focusPopout = useCallback((id) => {
    popoutTopRef.current += 1;
    const z = popoutTopRef.current;
    setPopouts((windows) => windows.map((w) => (w.id === id ? { ...w, z } : w)));
  }, []);

  const closePopout = useCallback((id) => {
    setPopouts((windows) => windows.filter((w) => w.id !== id));
  }, []);

  // Open a window for the list currently on screen. A list that already has a
  // window gets that window raised instead of a duplicate - two windows on the
  // same list would show identical data and be impossible to tell apart.
  const onPopOut = useCallback(() => {
    const list = activeList || "";
    setPopouts((windows) => {
      const existing = windows.find((w) => w.list === list);
      if (existing) {
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      return [
        ...windows,
        {
          id: "popout-" + popoutSeqRef.current,
          kind: "board",
          list,
          // Where he last left this list's window, clamped to today's screen
          // so a rect remembered from a bigger monitor cannot strand it.
          rect: clampPopoutRect(readPopoutRect(list) || defaultPopoutRect(windows.length)),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Promote an in-app window to a REAL one, so it can go on the second
  // monitor or sit on top of thinkorswim. This is the only thing that can
  // leave the app: a div cannot be painted outside the window that owns it,
  // which is why installing AGX to the desktop did not help (2026-09-02).
  //
  // The window NAME is per list. The original code named every window
  // "agx-momx-scanner", and window.open re-targets an existing name instead
  // of making a new window - which is why only one ever appeared.
  const detachPopout = useCallback((win) => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("popout", "momx");
    url.searchParams.set("list", win.list || "");
    // Nothing chart-related belongs in a scanner window.
    ["symbol", "timeframe", "link"].forEach((key) => url.searchParams.delete(key));

    // The in-app rect is measured from the top-left of the PAGE; window.open
    // positions from the top-left of the SCREEN. Without the app window's own
    // offset the detached window lands somewhere unrelated - and on a second
    // monitor that can be off the visible desktop entirely.
    const saved = readDetachedRect(win.list);
    const chromeH = Math.max(0, window.outerHeight - window.innerHeight);
    const left = Math.round(saved ? saved.x : (window.screenX || 0) + win.rect.x);
    const top = Math.round(saved ? saved.y : (window.screenY || 0) + chromeH + win.rect.y);
    const width = Math.round(saved ? saved.w : win.rect.w);
    const height = Math.round(saved ? saved.h : win.rect.h);

    const opened = window.open(
      url.toString(),
      "agx-momx-" + String(win.list || "default").replace(/[^A-Za-z0-9]+/g, "-"),
      `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`,
    );
    if (!opened) {
      // Blocked. KEEP the in-app window - closing it here would leave him
      // with neither, which is strictly worse than what he had.
      setNotice(
        "Your browser blocked the detached window. Allow pop-ups for this site, then press Detach again.",
      );
      return;
    }
    opened.focus?.();
    // It lives out there now; two copies of the same list on screen would
    // just be confusing.
    closePopout(win.id);
  }, [closePopout]);

  // One symbol's card, in its own small window. Same window manager as the
  // board windows - a card is a KIND, not a second system - so it drags,
  // resizes, stacks and closes by exactly the same code.
  const onOpenCard = useCallback((symbol, alert = null) => {
    const wanted = String(symbol || "").trim().toUpperCase();
    if (!wanted) return;
    setPopouts((windows) => {
      const existing = windows.find((w) => w.kind === "card" && w.symbol === wanted);
      if (existing) {
        // Already open: raise it. A second identical card is just clutter.
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      const cards = windows.filter((w) => w.kind === "card").length;
      const base = defaultPopoutRect(cards);
      return [
        ...windows,
        {
          id: "card-" + popoutSeqRef.current,
          kind: "card",
          symbol: wanted,
          alert: alert && typeof alert === "object" ? alert : null,
          list: activeList || "",
          // A card is a fixed amount of information, so it gets a fixed,
          // smaller box rather than the board's near-full-screen default.
          rect: clampPopoutRect({ x: base.x, y: base.y, w: 820, h: 470 }),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Scanner grade "why" panel. The row is captured AT THE CLICK (a History row
  // is a frozen moment; a live row is what he clicked on). Stable callback so
  // the memoised MomxRow / MomxHistoryRow do not re-render for it.
  const [gradeRow, setGradeRow] = useState(null);
  const onOpenGrade = useCallback((row) => {
    if (row && typeof row === "object") setGradeRow(row);
  }, []);
  const onCloseGrade = useCallback(() => setGradeRow(null), []);
  const [gradeRecord, setGradeRecord] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(MOMX_GRADE_RECORD_ENDPOINT + (bear ? "?dir=bear" : ""), { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        if (!cancelled && payload && typeof payload === "object") setGradeRecord(payload);
      } catch {
        /* keep the last good record; the panel says "track record unavailable" */
      }
    };
    // The record is per direction: drop the old one so a bear letter never
    // shows the bull numbers while the bear record loads.
    setGradeRecord(null);
    load();
    const timer = window.setInterval(load, MOMX_GRADE_RECORD_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [bear]);

  // A card for one archived MOMENT, opened from the History view. Same window
  // kind as a live card; what differs is that its data is frozen and stamped,
  // so it can never be read as "now".
  const onOpenSnapshot = useCallback((entry) => {
    const snapshot = entry && typeof entry.row === "object" ? entry.row : null;
    const wanted = String((snapshot && snapshot.symbol) || (entry && entry.symbol) || "")
      .trim()
      .toUpperCase();
    if (!snapshot || !wanted) return;
    const at = entry.at || entry.lastSeenAt || "";
    const id = "snap-" + wanted + "-" + at;
    setPopouts((windows) => {
      const existing = windows.find((w) => w.id === id);
      if (existing) {
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutTopRef.current += 1;
      const cards = windows.filter((w) => w.kind === "card").length;
      const base = defaultPopoutRect(cards);
      return [
        ...windows,
        {
          id,
          kind: "card",
          symbol: wanted,
          list: activeList || "",
          snapshot,
          snapshotAt: at,
          // The row already parsed this once; re-parsing the string risks
          // disagreeing with the row it was opened from.
          snapshotAtMs: Number(entry.atMs) || Date.parse(at) || 0,
          // Open interest changes overnight, so today's walls WERE the walls
          // in force for a row stamped today, and were not for an older one.
          sameDay: String(at).slice(0, 10) === new Date().toISOString().slice(0, 10),
          rect: clampPopoutRect({ x: base.x, y: base.y, w: 820, h: 470 }),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Drag from the title bar, resize from the corner. Pointer capture rather
  // than window listeners so a fast drag that outruns the cursor cannot drop
  // the gesture, and so touch and pen work unchanged.
  const beginPopoutGesture = useCallback((event, mode, id) => {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.currentTarget;
    setPopouts((windows) => {
      const win = windows.find((w) => w.id === id);
      if (win) {
        popoutDragRef.current = {
          id, mode, startX: event.clientX, startY: event.clientY, rect: win.rect,
        };
      }
      return windows;
    });
    target.setPointerCapture?.(event.pointerId);
    focusPopout(id);
    event.preventDefault();
  }, [focusPopout]);

  const movePopoutGesture = useCallback((event) => {
    const drag = popoutDragRef.current;
    if (!drag || !event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = clampPopoutRect(
      drag.mode === "move"
        ? { ...drag.rect, x: drag.rect.x + dx, y: drag.rect.y + dy }
        : { ...drag.rect, w: drag.rect.w + dx, h: drag.rect.h + dy },
    );
    setPopouts((windows) => windows.map((w) => (w.id === drag.id ? { ...w, rect: next } : w)));
  }, []);

  const endPopoutGesture = useCallback((event) => {
    const drag = popoutDragRef.current;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    popoutDragRef.current = null;
    if (!drag) return;
    setPopouts((windows) => {
      const win = windows.find((w) => w.id === drag.id);
      if (win) writePopoutRect(win.list, win.rect);
      return windows;
    });
  }, []);

  // A DETACHED window records its own screen geometry, so re-detaching that
  // list puts it back where he left it - on the second monitor if that is
  // where it was. Written on hide rather than on every move: `resize` fires
  // continuously while dragging an OS window edge.
  useEffect(() => {
    if (!embedded || typeof window === "undefined") return undefined;
    const list = initialList;
    if (!list) return undefined;
    const remember = () => {
      try {
        window.localStorage.setItem(
          MOMX_DETACHED_RECT_KEY + "." + String(list),
          JSON.stringify({
            x: window.screenX, y: window.screenY, w: window.outerWidth, h: window.outerHeight,
          }),
        );
      } catch {
        /* private mode: it simply opens at the default place next time */
      }
    };
    window.addEventListener("pagehide", remember);
    return () => {
      remember();
      window.removeEventListener("pagehide", remember);
    };
  }, [embedded, initialList]);

  // Esc closes the FRONT window only. Closing the whole stack on one key
  // would throw away an arrangement he spent time building. Bound only while
  // something is open, so it can never swallow Esc from anything else, and
  // skipped while a popover that portals ABOVE the windows is up.
  useEffect(() => {
    if (popouts.length === 0) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (document.querySelector(".momx-news-overlay, .momo-settings-overlay, .momx-picker-overlay, .momx-grade-why-overlay, .momx-columns-overlay")) return;
      setPopouts((windows) => {
        if (windows.length === 0) return windows;
        const top = windows.reduce((a, b) => (b.z > a.z ? b : a));
        return windows.filter((w) => w.id !== top.id);
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popouts.length]);

  const onPickMomentum = useCallback((symbol) => {
    setFocusSymbol((current) => (current === symbol ? null : symbol));
  }, []);

  // The live payload counts only for the list currently selected: a fetch still
  // resolving for the previous list must not be read as this one's data.
  // boardList is the CACHE key of the payload in `board` (direction-scoped
  // since the BEAR switch), so compare against the same key or the bear
  // board would always fall through to its cached copy (review 2026-09-25).
  const liveBoard = boardList === boardCacheKey(activeList, direction) ? board : null;
  const activeTab = lists.find((item) => item.name === activeList) || null;
  const rawWarming = Boolean((liveBoard && liveBoard.warming) || (activeTab && activeTab.warming));

  // The durable last-good board for the active list. Recomputed when the list
  // changes or when a fresh fetch lands (which may have written a newer copy).
  const cachedBoard = useMemo(() => readCachedBoard(boardCacheKey(activeList, direction)), [activeList, direction, board]);

  // THE decision: show live rows when the fetch returned any; otherwise fall
  // back to the cached rows rather than the empty "Building..." state; only
  // truly empty when nothing exists anywhere. Also yields the stamp/count for
  // whatever is on screen and whether an "updating" note belongs on it.
  const boardView = useMemo(
    () => decideBoardView({ liveBoard, cache: cachedBoard, warming: rawWarming }),
    [liveBoard, cachedBoard, rawWarming],
  );
  const rows = boardView.rows.length > 0 ? boardView.rows : EMPTY_ROWS;
  // Everyone the 50-row cap cut. Since 2026-09-02 (second cut) the worker
  // builds these with every column, so they render exactly like the top 50;
  // they stay a separate list because `rows` is what the fastlane quote poll
  // reads, and it does not want 357 symbols.
  const restRows = boardView.rest;

  // ---- NEWS store: ticker-tagged headlines from api_server ------------------
  // `latest` (one headline per ticker) is folded into the rows, so newsOf -
  // the ONE gate for the badge, the count, the filter and the column - sees
  // the store's headline whenever it is newer than the worker's (or the worker
  // has none). The store never blocks the board: a failed read leaves the
  // worker's headlines exactly as they were.
  const [newsFeed, setNewsFeed] = useState(null);
  const [newsFeedBusy, setNewsFeedBusy] = useState(false);
  const newsLatest = newsFeed && newsFeed.latest && typeof newsFeed.latest === "object" ? newsFeed.latest : EMPTY_OBJECT;
  const allRows = useMemo(
    () => mergeStoredNews(restRows.length > 0 ? rows.concat(restRows) : rows, newsLatest),
    [rows, restRows, newsLatest],
  );
  const newsSymbols = useMemo(() => boardSymbols(allRows), [allRows]);
  newsSymbolsRef.current = newsSymbols;
  const newsSymbolsKey = newsSymbols.join(",");
  const loadNewsFeed = useCallback(async (symbols) => {
    if (!Array.isArray(symbols) || symbols.length === 0) return null;
    try {
      const response = await fetch(
        MOMX_NEWS_LATEST_ENDPOINT + "?symbols=" + encodeURIComponent(symbols.join(",")),
        { cache: "no-store" },
      );
      if (!response.ok) return null;
      const payload = await response.json();
      if (!payload || typeof payload !== "object") return null;
      if (mountedRef.current) setNewsFeed(payload);
      return payload;
    } catch {
      return null; // news can never break the board
    }
  }, []);
  // Ask api_server to read every source again for these tickers, then poll the
  // store until the scrape finishes (or a bounded wait runs out).
  const refreshNewsFeed = useCallback(async (symbols) => {
    if (!Array.isArray(symbols) || symbols.length === 0) return;
    setNewsFeedBusy(true);
    try {
      const response = await fetch(MOMX_NEWS_REFRESH_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ symbols: symbols.slice(0, MOMX_NEWS_REFRESH_MAX_SYMBOLS) }),
      });
      if (!response.ok) return;
      const deadline = Date.now() + MOMX_NEWS_REFRESH_WAIT_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, MOMX_NEWS_REFRESH_POLL_MS));
        if (!mountedRef.current) return;
        const current = newsSymbolsRef.current.length > 0 ? newsSymbolsRef.current : symbols;
        const payload = await loadNewsFeed(current);
        if (payload && !payload.refreshing) return;
      }
    } catch {
      // A failed refresh leaves the last stored headlines on screen.
    } finally {
      if (mountedRef.current) setNewsFeedBusy(false);
    }
  }, [loadNewsFeed]);
  refreshNewsFeedRef.current = refreshNewsFeed;
  // Read the store whenever the set of tickers on the board changes (debounced:
  // the rows arrive in pieces while a list warms), and every 5 minutes.
  useEffect(() => {
    if (!newsSymbolsKey) return undefined;
    const id = window.setTimeout(() => loadNewsFeed(newsSymbolsRef.current), 400);
    return () => window.clearTimeout(id);
  }, [newsSymbolsKey, loadNewsFeed]);
  useEffect(() => {
    const id = window.setInterval(() => {
      if (newsSymbolsRef.current.length > 0) loadNewsFeed(newsSymbolsRef.current);
    }, MOMX_NEWS_STORE_POLL_MS);
    return () => window.clearInterval(id);
  }, [loadNewsFeed]);

  // Metadata for the list currently selected, for the picker button's count
  // and building state. An empty object is fine: every read of it is guarded.
  const activeMeta = useMemo(
    () => lists.find((item) => item.name === activeList) || {},
    [lists, activeList],
  );

  // ---- fastlane: live quotes for the rows on screen -----------------------
  // {symbol: last}. Object identities are reused per symbol when the price is
  // unchanged, so MomxRow's memo only re-renders the rows that actually ticked.
  const [liveLastBySymbol, setLiveLastBySymbol] = useState(EMPTY_OBJECT);
  const liveQuotesInFlightRef = useRef(false);
  const liveQuotesServingRef = useRef(true);
  const liveSymbolsRef = useRef("");

  useEffect(() => {
    liveSymbolsRef.current = (rows || [])
      .map((row) => row && row.symbol)
      .filter(Boolean)
      .slice(0, 60)
      .join(",");
  }, [rows]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (cancelled || liveQuotesInFlightRef.current) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const symbols = liveSymbolsRef.current;
      if (!symbols) return;
      liveQuotesInFlightRef.current = true;
      try {
        const res = await fetch(
          `${MOMX_LIVE_QUOTES_ENDPOINT}?symbols=${encodeURIComponent(symbols)}`,
          { credentials: "include" },
        );
        if (!res.ok) return; // older api_server: fastlane simply stays dark
        const data = await res.json();
        liveQuotesServingRef.current = Boolean(data && data.serving);
        const quotes = (data && data.quotes) || {};
        setLiveLastBySymbol((held) => {
          let changed = false;
          const next = { ...held };
          for (const [symbol, quote] of Object.entries(quotes)) {
            const last = Number(quote && quote.last);
            if (Number.isFinite(last) && last > 0 && held[symbol] !== last) {
              next[symbol] = last;
              changed = true;
            }
          }
          return changed ? next : held;
        });
      } catch {
        // Network blip: keep showing build prices; next tick retries.
      } finally {
        liveQuotesInFlightRef.current = false;
      }
    };
    poll();
    // Two cadences, one interval: tick fast, but when the stream reported
    // not-serving (weekend, socket down) only actually poll on the slow beat.
    let beat = 0;
    const timer = window.setInterval(() => {
      beat += 1;
      const idleEvery = Math.max(1, Math.round(MOMX_LIVE_QUOTES_IDLE_MS / MOMX_LIVE_QUOTES_MS));
      if (!liveQuotesServingRef.current && beat % idleEvery !== 0) return;
      poll();
    }, MOMX_LIVE_QUOTES_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const showingCache = boardView.source === "cache";

  // Counted over the WHOLE list: on a busy day more than 50 symbols can pass
  // and the ones beyond the cap arrive as rest rows - still matches.
  const matchCount = useMemo(
    () => allRows.reduce((total, row) => (row && row.scanPass ? total + 1 : total), 0),
    [allRows],
  );

  // Three memos, in this order, so a chip click cannot move the chip counts
  // under the trader's cursor: the counts are taken BEFORE the industry filter.
  // "Scan matches only" OFF means the whole list (2026-09-02: he unticked it
  // on the 357-name Watchlist, saw 50 rows and asked where the rest were).
  const scanRows = useMemo(
    () => (matchesOnly ? allRows.filter((row) => row && row.scanPass) : allRows),
    [allRows, matchesOnly],
  );

  // Which rows carry a headline the popover would actually show. newsOf is
  // the ONE gate (fresh within 24h, non-blank headline), so the NEWS count on
  // the button, the filtered board and the newspaper icons in the rows can
  // never disagree. Read over ALL shipped rows - since 2026-09-02 the worker
  // attaches news to the rest rows too, and a row that shows the icon but is
  // missing from the NEWS view would be exactly the count/icon disagreement
  // this gate exists to prevent.
  //
  // Taken from ALL shipped rows, not the scan matches: NEWS is a catalyst
  // finder, and the momentum scan usually passes a handful of the 50 (6 on
  // the night this shipped, none of them with news). Filtering news THROUGH
  // "Scan matches only" would leave the NEWS view empty most of the time. The
  // matches stay obvious inside it - MomxRow still paints them is-pass-up.
  const newsRows = useMemo(() => allRows.filter((row) => newsOf(row) !== null), [allRows]);
  const newsCount = newsRows.length;

  // A picked STRATEGY (V2 / V3 / Daily 2 / G) reads the whole board, not just
  // the scan matches: its tickers are judged by their own rules, and a Daily 2
  // or G entry that later drops out of the scan must stay on the day's list.
  // Seen 2026-09-23: with "Scan matches only" ticked a G #1 was hidden.
  //
  // TWO sets, on purpose: `unfilteredRows` is what the board shows with the
  // FILTERS button OFF (scan matches as ticked); `filterSourceRows` is what the
  // filter - and every count ABOUT the filter - reads. Deriving the second from
  // the button state made the panel say "0 of 5 tickers - no ticker has
  // qualified for V2" while 9 had, whenever FILTERS was still off (smoke test
  // 2026-09-24): the count read the 5 scan matches, the board the whole list.
  const strategyPicked = Boolean(strategyOf(filters));
  const unfilteredRows = newsOnly ? newsRows : scanRows;
  const filterSourceRows = newsOnly ? newsRows : strategyPicked ? allRows : scanRows;

  // The FILTERS gate. Sits BEFORE the industry chips and the sector rollup on
  // purpose, exactly like the NEWS narrowing above: those two are summaries OF
  // the board, and a chip counting rows the table is not showing is the same
  // disagreement the news count/icon gate exists to prevent.
  const baseRows = useMemo(
    () => (filtersOn ? filterRows(filterSourceRows, filters) : unfilteredRows),
    [filterSourceRows, unfilteredRows, filtersOn, filters],
  );

  // Counted on the set the filter reads, so the button can say how many rows a
  // press would leave BEFORE he presses it - the same contract as the NEWS count.
  const filterCount = useMemo(
    () => countPassing(filterSourceRows, filters),
    [filterSourceRows, filters],
  );

  // Per-group counts for the panel. Only computed while the panel is OPEN -
  // it is four passes over up to 358 rows and nobody is reading it otherwise.
  const filterGroupCounts = useMemo(
    () => (filtersOpen ? groupPassCounts(filterSourceRows, filters) : null),
    [filtersOpen, filterSourceRows, filters],
  );

  // Which symbols carry the Skittles star. A SET of symbols rather than a flag
  // per row: MomxRow takes a boolean, so the memo still holds for every row
  // whose star did not change on this build.
  const starredSymbols = useMemo(() => {
    if (!filtersOn || !filters || !filters.skittles || !filters.skittles.rankToTop) {
      return EMPTY_STARS;
    }
    const out = new Set();
    for (const row of baseRows) if (row && isStarred(row, filters)) out.add(row.symbol);
    return out.size === 0 ? EMPTY_STARS : out;
  }, [baseRows, filtersOn, filters]);

  const chips = useMemo(() => industryCounts(baseRows), [baseRows]);
  // Derived from the rows already on screen, so it can never disagree with the
  // table beneath it and costs no extra request.
  // A HOT sector (money flowing in, momx/sectors.py) must show whatever the
  // filter leaves on screen - 2026-09-24 his FILTERS kept 4 rows and the hot
  // Quantum / Space chips vanished. Hot groups come from the WHOLE board and
  // lead; the usual participation chips still count the rows on screen.
  // Pop a card for each NEW fresh setup (GO / OPT / SOLO / TURN / 🔥 leader),
  // once per ticker per ET day, at most 3 per board update, wide screens only
  // (on a phone a floating window would cover the board).
  useEffect(() => {
    if (!autoCards || embedded || !MOMX_NEW_SETUPS || bear) return;
    if (typeof window === "undefined" || window.innerWidth < 900) return;
    const now = Date.now();
    const day = new Date(now).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    let seen;
    try {
      seen = JSON.parse(window.localStorage.getItem(AUTO_CARDS_SEEN_KEY) || "null");
    } catch {
      seen = null;
    }
    if (!seen || seen.day !== day || !Array.isArray(seen.symbols)) seen = { day, symbols: [] };
    let opened = 0;
    for (const row of allRows) {
      if (opened >= 3) break;
      const symbol = row && row.symbol;
      if (!symbol || seen.symbols.includes(symbol)) continue;
      const hit = freshAlert(row, now);
      if (!hit) continue;
      seen.symbols.push(symbol);
      opened += 1;
      const came = Number.isFinite(hit.atMs)
        ? new Date(hit.atMs).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false })
        : "";
      onOpenCard(symbol, { label: hit.text, cameAt: came });
    }
    if (opened) {
      try {
        window.localStorage.setItem(AUTO_CARDS_SEEN_KEY, JSON.stringify(seen));
      } catch {
        /* no storage: it can pop again after a reload, nothing worse */
      }
    }
  }, [allRows, autoCards, embedded, bear, onOpenCard]);

  const rowBySymbol = useMemo(() => new Map(allRows.filter(Boolean).map((row) => [row.symbol, row])), [allRows]);
  const sectors = useMemo(() => {
    const onScreen = sectorRollup(baseRows);
    // New sector marks off (MOMX_NEW_SETUPS): the plain participation chips,
    // as on 2026-09-24 morning.
    if (!MOMX_NEW_SETUPS) return onScreen.map((group) => ({ ...group, hot: false, late: false, leaders: [] }));
    const hot = sectorRollup(
      allRows.filter((row) => row && row.sectorRotation && (row.sectorRotation.hot || row.sectorRotation.late)),
      { minMembers: 1, limit: 8 },
    ).filter((group) => group.hot || group.late);
    const names = new Set(hot.map((group) => group.name));
    // Bull: a 🔥 sector that turned negative today is shown cooled; bear: 🔥 =
    // falling sectors (momxCells.sectorsForDirection, 2026-09-28).
    return sectorsForDirection(hot.concat(onScreen.filter((group) => !names.has(group.name))), allRows, direction);
  }, [baseRows, allRows, direction]);

  // Live H/L rows, reused per symbol while neither the built row nor its live
  // price changed, so MomxRow's memo only re-renders rows that actually ticked.
  const liveHlRowsRef = useRef(new Map());

  const visibleRows = useMemo(() => {
    // A clicked momentum chip WINS over every other filter: the trader asked
    // for one specific symbol, so it is pulled from the FULL row set - a
    // lost_match must still be findable with "Scan matches only" ticked.
    // The ⚡ views read the WHOLE board: a stock that fired a bolt is listed
    // even when it is no longer a scan match.
    const byIndustry = focusSymbol
      ? allRows.filter((row) => row && row.symbol === focusSymbol)
      : filterByIndustries(boltView === "all" ? baseRows : allRows, selectedIndustries);
    // ⚡ TODAY = the bolt is on (fired today, still in the upper half of its
    // hour); ⚡ NOW = on AND passing the gates this second. ALL = no filter.
    const filtered = focusSymbol || boltView === "all"
      ? byIndustry
      : byIndustry.filter((row) => {
        const b = row && liveBolts[row.symbol];
        return Boolean(b && (boltView === "today" ? b.firedAt : b.on && b.now));
      });
    if (filtered.length === 0) return EMPTY_ROWS;
    // H/L follows the LIVE price between board rebuilds, like his TOS column
    // (2h, EXT, length 8), which re-sorts as prices move. The build ships the
    // 8-bar hh/ll; the live last extends them and the same formula re-runs.
    const liveCache = liveHlRowsRef.current;
    // %Chg moves live too (premarket + regular hours only - see livePctChange).
    const et = etParts(Date.now());
    const etMinute = et.hour * 60 + et.minute;
    const pctSessionOk = isTradingDay(et.weekday) && etMinute >= 4 * 60 && etMinute < 16 * 60;
    const withLive = filtered.map((row) => {
      const last = row ? liveLastBySymbol[row.symbol] : undefined;
      if (!row || !Number.isFinite(last)) return row;
      const held = liveCache.get(row.symbol);
      if (held && held.base === row && held.last === last && held.pctOk === pctSessionOk) return held.out;
      const highLow = liveHighLowCell(row.highLow, last);
      const pctChange = livePctChange(row.prevClose, last, pctSessionOk);
      const out = highLow || pctChange != null
        ? { ...row, ...(highLow ? { highLow } : {}), ...(pctChange != null ? { pctChange } : {}) }
        : row;
      liveCache.set(row.symbol, { base: row, last, out, pctOk: pctSessionOk });
      return out;
    });
    const column = MOMX_LIVE_COLUMN_BY_KEY.get(sort.key);
    // sortRows only reads FLAT keys, so each row is projected onto a throwaway
    // index object carrying its sort value plus the symbol it tie-breaks on.
    const unsorted = sort.direction === "none";
    const index = unsorted ? [] : withLive.map((row) => ({
      symbol: row && row.symbol ? row.symbol : null,
      [sort.key]: columnSortValue(row, column, newsOnly),
      row,
    }));
    const hlValue = (row) => {
      const v = row ? (liveHl[row.symbol] ?? (liveBolts[row.symbol] && liveBolts[row.symbol].hlMomentum)) : undefined;
      return Number.isFinite(Number(v)) ? Number(v) : -1;
    };
    // H/L ⚡ sort (ScannerX3): who sits at the top of an hour that actually
    // went somewhere - position in the last hour x the hour's size vs usual.
    const ordered = hlSort
      ? [...withLive].sort((a, b) => hlValue(b) - hlValue(a))
      : unsorted
        ? withLive // NO SORT: the scanner's own order
        : sortRows(index, sort.key, sort.direction).map((entry) => entry.row);
    // Skittles "push to top" is applied LAST and is a stable partition, not a
    // re-sort: his column sort still decides the order inside each half, so
    // clicking a header does what it always did. Only while the filter is
    // pressed - re-ordering the board for someone who never opened FILTERS
    // would be a mystery.
    // Daily 2 picked: the list reads #1, #2, #3 top-down whatever column is
    // sorted - the order IS the strategy (a no-op for every other setting).
    const out = filtersOn ? orderByDaily2(pushStarredToTop(ordered, filters), filters) : ordered;
    // ⭐1, ⭐2 ... on the day's best setups, ranked across the WHOLE board by his
    // tested order, visible with or without FILTERS (his ask 2026-09-25: "I
    // don't change any filters, I just use the scanner"). Only ranked rows are
    // copied, so every other row keeps its identity for the row memo.
    const ranks = bestSetupRanks(allRows);
    return ranks.size ? out.map((row) => (row && ranks.has(row.symbol) ? { ...row, bestRank: ranks.get(row.symbol) } : row)) : out;
  }, [
    allRows, baseRows, selectedIndustries, sort.key, sort.direction, focusSymbol, boltView, liveBolts, liveHl, hlSort,
    newsOnly, filtersOn, filters, earningsMap, liveLastBySymbol,
  ]);

  // Clock for the matchedSince row decorations, read ONCE per render (not
  // state, not a ticker): the 15s board poll re-renders this component anyway,
  // which is cadence enough for a 15-minute NEW window to expire, and a plain
  // number keeps MomxRow's memo intact - rows re-render only when their own
  // stamp actually crosses the boundary and flips the derived primitives.
  const rowClockMs = Date.now();

  // The row whose news popover is open. Looked up from the VISIBLE set on
  // purpose: filter or sort the symbol off-screen and the popover simply stops
  // rendering - no effect, no cleanup, nothing to leak.
  const newsOpenRow = newsOpenSymbol
    ? visibleRows.find((row) => row && row.symbol === newsOpenSymbol) || null
    : null;

  const onSort = useCallback((column) => {
    if (!column.sortable) return;
    // desc -> asc -> NO SORT (names: asc -> desc -> no sort); momxColumnLayout.
    setSort((current) => nextSortState(current, column));
  }, [setSort]);

  const onToggleIndustry = useCallback((name) => {
    setSelectedIndustries((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next.size === 0 ? EMPTY_SELECTION : next;
    });
  }, []);

  const onChooseList = useCallback((name) => {
    setActiveList((current) => {
      if (current === name) return current;
      // A different list is a different row set: the old industry chips do not
      // apply to it and, left on, would silently hide every row. The same goes
      // for a momentum-chip focus taken on the previous list.
      setSelectedIndustries(EMPTY_SELECTION);
      setFocusSymbol(null);
      setNotice("");
      // Switching lists is the same rule as returning to one: if this list has
      // been seen (memory OR localStorage) its rows are shown at once - the
      // cachedBoard memo re-reads them for the new activeList and decideBoardView
      // paints them with an "updating" note - and only a list genuinely never
      // loaded blanks to the building state. Clearing the live board (and
      // re-pointing boardList) is what stops the PREVIOUS list's rows bleeding
      // under the new tab while its fetch is in flight.
      // A different list is also a different ARCHIVE: reset the history view
      // to its newest day with no search, and drop the old list's payload so
      // it cannot flash under the new tab while the fetch is in flight.
      setHistoryDate(null);
      setHistorySearch("");
      setHistoryQuery("");
      setHistoryPayload(null);
      setBoard(null);
      setBoardList(boardCacheKey(name, direction));
      setLoading(!hasCachedBoard(boardCacheKey(name, direction)));
      return name;
    });
  }, [direction]);

  // Every tab click lands here; `history` says whether it was a History tab.
  // The list choice reuses onChooseList (a same-list click is a no-op there,
  // so flipping Watchlist <-> Watchlist History never resets the filters).
  const onChooseTab = useCallback(
    (name, history) => {
      setHistoryView(history);
      onChooseList(name);
    },
    [onChooseList],
  );

  const onSubmitUniverse = useCallback(
    async (event) => {
      event.preventDefault();
      if (universeBusy) return;
      if (universeText.trim() === "") return;
      // This box REPLACES the list; it has never added to it. On 2026-09-01 a
      // single symbol went into it while Watchlist was active and 357 tickers
      // were gone in one keystroke, with no confirmation and nothing to undo.
      // A paste that shrinks a real list to a fraction of its size is far more
      // likely to be a mistake than an intention, so that one case - and only
      // that one - arms first. A normal paste of a similar-sized list is
      // untouched, so this cannot become a click-through habit.
      //
      // Two-tap, NOT window.confirm(): his phone in-app browser (WKWebView
      // with no dialog delegate) resolves confirm() to false without ever
      // drawing it, so a confirm here made Set look broken on the device he
      // actually uses. Same reasoning as the alert centre's "Clear all" and
      // the admin two-tap delete (App.jsx:25133).
      const pastedCount = (universeText.match(/[A-Za-z][A-Za-z.-]*/g) || []).length;
      const knownCount = (lists.find((item) => item.name === activeList) || {}).count || 0;
      if (knownCount >= 10 && pastedCount < knownCount / 2 && !setArmed) {
        setSetArmed(true);
        setError("");
        setNotice(
          "This REPLACES the " +
            (activeList || "current") +
            " list - it does not add to it. " +
            knownCount +
            " tickers would become " +
            pastedCount +
            ". Press Set again to confirm, or Restore to keep what you have.",
        );
        return;
      }
      setSetArmed(false);
      setUniverseBusy(true);
      setNotice("");
      try {
        const body = { text: universeText };
        // The list name travels with the paste. Without it the server applies a
        // Mag7-sized paste to whichever list it happens to consider active.
        if (activeList) body.list = activeList;
        const response = await fetch(MOMX_UNIVERSE_ENDPOINT, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          cache: "no-store",
          body: JSON.stringify(body),
        });
        if (!response.ok) {
          throw new Error(
            response.status === 404
              ? "The MomX scanner service is not running yet, so the ticker list could not be saved."
              : "The ticker list was not saved (the service answered " + response.status + ").",
          );
        }
        // The worker answers 200 even when it REFUSED the paste: a text it
        // could not find any ticker in comes back as {ok:false, error:...}
        // with an OK status. Checking only response.ok reported "Ticker list
        // saved." for a save that never happened, and cleared the box so the
        // text was gone too - which is exactly how a trader ends up retrying
        // with a shorter input until a bad one sticks.
        const saved = await response.json().catch(() => null);
        if (saved && saved.ok === false) {
          throw new Error(
            (saved.error || "That text had no usable tickers in it.") +
              " Nothing was changed.",
          );
        }
        if (!mountedRef.current) return;
        setError("");
        setNotice(
          (saved && typeof saved.universeCount === "number"
            ? "Saved " + saved.universeCount + " tickers. Rebuilding the board."
            : "Ticker list saved. Rebuilding the board.") + typedOptionsSentence(saved),
        );
        setUniverseText("");
        loadLists();
        await load({ quiet: true, queue: true });
      } catch (caught) {
        if (!mountedRef.current) return;
        setError(
          caught && caught.message
            ? caught.message
            : "Could not save the ticker list. Nothing was changed.",
        );
      } finally {
        if (mountedRef.current) setUniverseBusy(false);
      }
    },
    [activeList, lists, load, loadLists, setArmed, universeBusy, universeText],
  );

  // ADD, the safe half of the Tickers box. Until this existed the box could
  // only REPLACE, so "put OXY on my list" and "throw my list away" were the
  // same button - which is how 357 tickers became 1 on 2026-09-01. No arming
  // here on purpose: adding is not destructive, and a confirmation on the safe
  // action would train him to tap through the one on the dangerous action.
  const onAddUniverse = useCallback(async () => {
    if (universeBusy || universeText.trim() === "") return;
    setUniverseBusy(true);
    setSetArmed(false);
    setNotice("");
    try {
      const body = { text: universeText };
      if (activeList) body.list = activeList;
      const response = await fetch(MOMX_UNIVERSE_ADD_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? "This copy of the scanner service cannot add tickers yet."
            : "The tickers were not added (the service answered " + response.status + ").",
        );
      }
      const added = await response.json().catch(() => null);
      if (added && added.ok === false) {
        throw new Error((added.error || "Those tickers could not be added.") + " Nothing was changed.");
      }
      if (!mountedRef.current) return;
      setError("");
      const names = (added && added.added) || [];
      const already = (added && added.already) || [];
      let message;
      if (names.length === 0) {
        message =
          already.length > 0
            ? (already.length === 1 ? already[0] + " is" : already.join(", ") + " are") +
              " already on this list. Nothing changed." + typedOptionsSentence(added)
            : "Nothing was added.";
      } else {
        message =
          "Added " +
          names.join(", ") +
          ". " +
          (added.universeBefore || 0) +
          " to " +
          (added.universeCount || 0) +
          " tickers." +
          (already.length > 0 ? " " + already.join(", ") + " was already there." : "") +
          typedOptionsSentence(added);
        // The one consequence he cannot see and must be told: this list used to
        // follow My Watchlist and now holds its own copy.
        if (added.materialised) {
          message +=
            " This list no longer follows My Watchlist - press Restore to reconnect it.";
        }
      }
      setNotice(message);
      setUniverseText("");
      loadLists();
      await load({ quiet: true, queue: true });
    } catch (caught) {
      if (!mountedRef.current) return;
      setError(
        caught && caught.message ? caught.message : "Could not add those tickers.",
      );
    } finally {
      if (mountedRef.current) setUniverseBusy(false);
    }
  }, [activeList, load, loadLists, universeBusy, universeText]);

  // The undo for the box above. It does not paste 357 symbols back in - it
  // drops the saved override so the list follows My Watchlist again, which is
  // what keeps it tracking every future edit he makes there.
  //
  // Two-tap for the same reason as Set above: window.confirm() never draws in
  // his phone in-app browser and silently returns false, which made Restore
  // look dead on the one device this was built for.
  const onRestoreUniverse = useCallback(async () => {
    if (universeBusy) return;
    if (!restoreArmed) {
      setRestoreArmed(true);
      setError("");
      setNotice(
        "Press Restore again to put the " +
          (activeList || "current") +
          " list back to your full default ticker list. Anything pasted into this tab is discarded.",
      );
      return;
    }
    setRestoreArmed(false);
    setUniverseBusy(true);
    setNotice("");
    try {
      const response = await fetch(MOMX_UNIVERSE_RESET_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(activeList ? { list: activeList } : {}),
      });
      if (!response.ok) {
        throw new Error(
          response.status === 404
            ? "This copy of the scanner service is too old to restore lists, so nothing was changed."
            : "The list was not restored (the service answered " + response.status + ").",
        );
      }
      const restored = await response.json().catch(() => null);
      if (restored && restored.ok === false) {
        throw new Error((restored.error || "The list was not restored.") + " Nothing was changed.");
      }
      if (!mountedRef.current) return;
      setError("");
      setNotice(
        restored && typeof restored.universeCount === "number"
          ? "Restored " + restored.universeCount + " tickers. Rebuilding the board."
          : "List restored. Rebuilding the board.",
      );
      setUniverseText("");
      loadLists();
      await load({ quiet: true, queue: true });
    } catch (caught) {
      if (!mountedRef.current) return;
      setError(
        caught && caught.message
          ? caught.message
          : "Could not restore the list. Nothing was changed.",
      );
    } finally {
      if (mountedRef.current) setUniverseBusy(false);
    }
  }, [activeList, load, loadLists, restoreArmed, universeBusy]);

  // Both arms are per-attempt, so changing the text or the tab drops them: an
  // armed Set left over from a previous paste must never fire against a
  // different one.
  useEffect(() => {
    setSetArmed(false);
    setRestoreArmed(false);
  }, [activeList, universeText]);

  // The count reflects the data ON SCREEN (cached or live), so it never drops to
  // 0 while cached rows are visible.
  const universeCount = boardView.universeCount;

  // TOS checkbox state: the optimistic value while a POST is in flight, else
  // what the worker last reported. The loading note reads the LIVE board's
  // tosStatus (per list), and only while TOS is still filling symbols in.
  const shownSource = sourcePending || serverSource;
  const tosStatus = liveBoard && liveBoard.tosStatus;
  const tosLoadingNote =
    shownSource === "tos" &&
    !sourcePending &&
    tosStatus &&
    typeof tosStatus === "object" &&
    Number(tosStatus.pending) > 0 &&
    Number.isFinite(Number(tosStatus.total))
      ? "TOS loading " + (Number(tosStatus.loaded) || 0) + "/" + Number(tosStatus.total)
      : "";

  // Options only (momx/optionable.py): what the scanner held back and why.
  // Read from the LIVE board, like errorSymbols below - the cache whitelist in
  // decideBoardView would drop an unknown field.
  const optionsNote = optionsGateNote(liveBoard && liveBoard.optionsGate);

  // Per-symbol errors are a property of the LIVE fetch, not of cached rows.
  // TOS mode (2026-09-30): a symbol still waiting for its rate-paced Schwab
  // turn comes back as "tos: queued" - not a failure, and the "TOS loading
  // n/N" note already counts it, so it stays out of "No data came back".
  const errorSymbols =
    liveBoard && liveBoard.errors && typeof liveBoard.errors === "object"
      ? Object.keys(liveBoard.errors).filter(
          (symbol) => !String(liveBoard.errors[symbol]).includes("tos: queued"),
        )
      : EMPTY_ROWS;

  const warming = rawWarming;

  // A 355-symbol first build takes MINUTES. That is normal, and this empty
  // state has to read as "building", never as anything resembling a failure.
  let emptyMessage = "The MomX board is empty. Paste a ticker list above and press Set.";
  if (warming && rows.length === 0) {
    emptyMessage =
      "Building this watchlist from live bars. A short list takes about half a minute; a few hundred symbols take several minutes. Rows appear here on their own.";
  } else if (loading && rows.length === 0) {
    emptyMessage = "Loading the MomX board...";
  } else if (error && rows.length === 0) {
    emptyMessage = "No figures to show yet - see the message above.";
  } else if (focusSymbol && rows.length > 0) {
    emptyMessage =
      focusSymbol + " is not on this list right now. Press clear above to see the whole board.";
  } else if (selectedIndustries.size > 0 && baseRows.length > 0) {
    emptyMessage = "No row is in the industries you picked. Click a chip again to clear it.";
  } else if (newsOnly && rows.length > 0) {
    emptyMessage = "No ticker on the board has news from the last 24 hours. Press NEWS again to see the whole list.";
  // Named BEFORE the matchesOnly line: with FILTERS pressed the filter is the
  // narrower cause, and blaming "Scan matches only" would send him to the
  // wrong switch. It also says WHICH conditions are on, because a filter he
  // tuned yesterday is the hardest empty board to explain to himself.
  } else if (filtersOn && filterSourceRows.length > 0) {
    emptyMessage =
      "No ticker passes your filters right now. Press FILTERS again for the whole board, " +
      "or open the sliders to loosen them. Currently: " + describeFilters(filters, direction);
  } else if (matchesOnly && rows.length > 0) {
    emptyMessage = "No symbol passes the " + (bear ? "bear" : "bull") + " scan right now. Untick “Scan matches only” to see the whole list.";
  }

  const panel = (
    <section className="momx-scanner-panel" data-testid="momx-scanner-panel" ref={panelRef}>
      {/* ONE row, whatever the list count. The old bar rendered two tabs per
          list (live + History), so it grew 2N: fine at the two built-in lists,
          but at six lists it is twelve buttons wrapping to ~170px of a 375px
          phone before a single row of the board - and more if a name is long.
          A picker plus a Live/History toggle is a constant 26px for any N. */}
      {lists.length > 0 ? (
        <div className="momx-listbar">
          {/* TOS link square, top-left like a thinkorswim gadget: clicking a
              ticker opens it in every chart of this colour. */}
          <label
            className="tos-sync-picker momx-link-picker"
            style={{ "--tos-link-color": chartLink.group.color, "--tos-link-text": chartLink.group.text }}
            title={"Linked to the " + chartLink.group.name + " chart(s): click a ticker to open it there"}
          >
            <select
              value={linkGroupValue}
              onChange={(event) => setLinkGroupValue(tosLinkGroup(event.target.value, 1).value)}
              aria-label="Chart link colour for this scanner"
              data-testid="momx-link-picker"
            >
              {TOS_LINK_GROUPS.map((item) => (
                <option value={item.value} key={item.value}>{item.value} - {item.name}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            ref={listBtnRef}
            className={"momx-listbtn" + (pickerOpen ? " is-open" : "")}
            aria-haspopup="true"
            aria-expanded={pickerOpen}
            onClick={() => setPickerOpen((open) => !open)}
            title="Switch, add, rename or delete a list"
          >
            <span className="momx-listbtn-name">{activeList || "Lists"}</span>
            {Number.isFinite(activeMeta.count) ? (
              <span className="momx-tab-count">{activeMeta.count}</span>
            ) : null}
            {activeMeta.warming ? <span className="momx-tab-state">building</span> : null}
            <ChevronDown size={12} aria-hidden="true" />
          </button>

          <div className="momx-seg" role="tablist" aria-label="Live board or history">
            <button
              type="button"
              role="tab"
              aria-selected={!historyView}
              className={"momx-segbtn" + (!historyView ? " is-active" : "")}
              onClick={() => onChooseTab(activeList, false)}
            >
              Live
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={historyView}
              className={"momx-segbtn" + (historyView ? " is-active" : "")}
              onClick={() => onChooseTab(activeList, true)}
              title={"What the scan matched on " + (activeList || "this list") + ", day by day, kept 30 days"}
            >
              History
            </button>
          </div>

          {pickerOpen ? (
            <MomxListPicker
              lists={lists}
              activeList={activeList}
              maxLists={maxLists}
              busy={listBusy}
              error={listError}
              anchorRef={listBtnRef}
              onPick={(name) => {
                setPickerOpen(false);
                onChooseTab(name, historyView);
              }}
              onCreate={onCreateList}
              onRename={onRenameList}
              onDelete={onDeleteList}
              onClose={() => setPickerOpen(false)}
            />
          ) : null}
        </div>
      ) : null}

      {/* RENDERED ABOVE THE VIEW BRANCH, not inside it. The first cut put this
          beside the FILTERS dialog, which lives in the LIVE-board half - so
          pressing FIND on the History tab set the flag and drew nothing. It is
          a portal either way; what matters is that it is reachable from the
          view whose bar carries the button. */}
      {findOpen ? (
        <MomxHistoryQueryDialog
          state={findState}
          onChange={setFindState}
          onClose={() => { setFindOpen(false); setFindError(""); }}
          onRun={runFind}
          busy={findBusy}
          error={findError}
          direction={direction}
        />
      ) : null}

      {/* Scanner grade "why" panel - above the view branch for the same
          reason as FIND: Setup cells are clickable in Live AND History. */}
      {gradeRow ? (
        <MomxGradeWhy row={gradeRow} record={gradeRecord} onClose={onCloseGrade} />
      ) : null}

      {historyView ? (
        <MomxHistorySection
          onOpenSnapshot={onOpenSnapshot}
          onOpenCard={onOpenCard}
          onOpenGrade={onOpenGrade}
          list={activeList}
          payload={historyPayload}
          loading={historyLoading}
          error={historyError}
          date={historyDate}
          query={historyQuery}
          search={historySearch}
          onDate={setHistoryDate}
          onSearch={setHistorySearch}
          onOffset={setHistoryOffset}
          onRefresh={onHistoryRefresh}
          findState={findState}
          findResult={findResult}
          findBusy={findBusy}
          findError={findError}
          onOpenFind={onOpenFind}
          onCloseFind={onCloseFind}
          columnLayout={columnLayout}
          onColumnLayoutChange={setColumnLayout}
        />
      ) : (
        <>
      <div className="momx-toolbar">
        {/* Enter submits ADD, not Set. The safe action is the one a reflex
            reaches, which is the whole point of this change. */}
        <form
          className="momx-universe-form"
          onSubmit={(event) => {
            event.preventDefault();
            onAddUniverse();
          }}
        >
          <label className="momx-universe-label" htmlFor="momx-universe-input">
            Tickers:
          </label>
          <input
            id="momx-universe-input"
            className="momx-universe-input"
            type="text"
            value={universeText}
            spellCheck={false}
            autoComplete="off"
            placeholder={
              activeList
                ? "Type tickers, e.g. avgo aapl oxy - then Add"
                : "Type tickers, e.g. avgo aapl oxy"
            }
            onChange={(event) => setUniverseText(event.target.value)}
          />
          {/* Add comes FIRST and is the primary button. Set (replace) sits
              after it, deliberately quieter: the box used to offer only
              replace, so the safe action had to be invented and the dangerous
              one demoted. Enter submits the form, which is Add. */}
          <button
            type="submit"
            className="momx-btn is-primary"
            disabled={universeBusy || universeText.trim() === ""}
          >
            {universeBusy ? "Working" : "Add"}
          </button>
          <button
            type="button"
            className={setArmed ? "momx-btn is-armed" : "momx-btn"}
            disabled={universeBusy || universeText.trim() === ""}
            onClick={onSubmitUniverse}
            title={"REPLACE the whole " + (activeList || "current") + " list with what is typed"}
          >
            {universeBusy ? "Setting" : setArmed ? "Replace?" : "Set"}
          </button>
          {/* Sits next to Set on purpose: the moment he sees the list is wrong
              is the moment he is looking at this box, and the fix has to be
              reachable from there rather than from a settings page. */}
          <button
            type="button"
            className={restoreArmed ? "momx-btn is-armed" : "momx-btn"}
            onClick={onRestoreUniverse}
            disabled={universeBusy}
            title={
              "Put the " +
              (activeList || "current") +
              " list back to your full default ticker list"
            }
          >
            {universeBusy ? "Working" : restoreArmed ? "Restore?" : "Restore"}
          </button>
        </form>

        <button
          type="button"
          className="momx-btn is-primary"
          onClick={forceRebuild}
          disabled={loading || rebuilding}
          title={"Run the " + (bear ? "bear" : "bull") + " scan again now"}
        >
          {rebuilding ? "SCANNING" : "SCAN"}
        </button>

        {/* BULL / BEAR switch (spec 2026-09-24). The bear scan is the bull
            scan mirrored (crosses below, squeezes firing down, selling volume,
            biggest losers first); the worker builds both from one pass. */}
        <button
          type="button"
          className={"momx-bull" + (bear ? " is-bear" : "")}
          onClick={() => setDirection((d) => (d === "bear" ? "bull" : "bear"))}
          title={bear
            ? "BEAR scan: the bull scan mirrored for puts. Click for BULL."
            : "BULL scan (AlertX Bull Momo). Click for BEAR - the same scan mirrored for puts."}
          aria-pressed={bear}
          data-testid="momx-direction-toggle"
        >
          {bear ? "BEAR" : "BULL"}
          {bear ? <ArrowDown size={11} aria-hidden="true" /> : <ArrowUp size={11} aria-hidden="true" />}
        </button>

        {/* FILTERS: press to narrow, click the sliders to tune. Two controls in
            one button so the toolbar does not grow a second cell - the label
            toggles, the icon opens the panel. The count is shown BEFORE he
            presses, same contract as NEWS. */}
        <span className={"momx-filters-btn" + (filtersOn ? " is-active" : "")}>
          <button
            type="button"
            className="momx-filters-toggle"
            onClick={() => setFiltersOn((on) => !on)}
            aria-pressed={filtersOn}
            data-testid="momx-filters-toggle"
            title={
              filtersOn
                ? "Showing only tickers that pass your filters. Press again for the whole board.\n" +
                  describeFilters(filters, direction)
                : "Show only the " + filterCount + " tickers that pass your filters\n" +
                  describeFilters(filters, direction)
            }
          >
            {/* Measured on a 390px phone: this button wraps the toolbar to an
                extra line, growing the board's content by 32px (163 -> 195px
                of toolbar). Hiding the label was tried and did NOT win the
                line back - see index.css - so the word stays. */}
            <span className="momx-filters-label">FILTERS</span>
            <span className="momx-filters-badge">{filterCount}</span>
          </button>
          <button
            type="button"
            className="momx-filters-open"
            onClick={() => setFiltersOpen(true)}
            data-testid="momx-filters-open"
            title="Set the filter conditions"
            aria-label="Set the filter conditions"
          >
            <SlidersHorizontal size={12} aria-hidden="true" />
          </button>
        </span>
        {filtersOpen ? (
          <MomxFiltersPanel
            config={filters}
            count={filterCount}
            total={filterSourceRows.length}
            counts={filterGroupCounts}
            onChange={setFilters}
            onReset={() => setFilters(JSON.parse(JSON.stringify(DEFAULT_MOMX_FILTERS)))}
            onClose={() => setFiltersOpen(false)}
            direction={direction}
          />
        ) : null}

        {/* Columns: move left/right, show/hide, reset. Same size and weight
            as NEWS beside it; the layout also drives the History table. */}
        <button
          type="button"
          className={"momx-news-btn momx-columns-btn" + (columnsOpen ? " is-active" : "")}
          onClick={() => setColumnsOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={columnsOpen}
          data-testid="momx-columns-open"
          title="Move columns left or right, hide or show them"
        >
          <Columns3 size={11} aria-hidden="true" />
          Columns
        </button>
        {columnsOpen ? (
          <MomxColumnManager
            columns={MOMX_COLUMNS}
            layout={columnLayout}
            onChange={setColumnLayout}
            onReset={() => setColumnLayout(defaultLayout(MOMX_COLUMN_KEYS))}
            onClose={() => setColumnsOpen(false)}
          />
        ) : null}
        <MomxColumnHeaderMenu
          menu={liveHeader.menu}
          columns={liveColumnList}
          layout={columnLayout}
          onChange={setColumnLayout}
          onClose={liveHeader.closeMenu}
        />

        {/* NEWS is a FILTER, not a scan: it narrows the rows already on the
            board to the ones with a fresh headline (the same test that draws
            the newspaper icon), match or not. The count is shown BEFORE he
            presses it, so he knows whether the press is worth making. */}
        <button
          type="button"
          className={"momx-news-btn" + (newsOnly ? " is-active" : "")}
          onClick={() => {
            // NEWS starts from the WHOLE board. A momentum-chip focus would
            // otherwise win over it outright (visibleRows), and an industry
            // chip would leave the intersection - possibly empty, with the
            // chip itself gone from the row because chips are rebuilt from
            // the news rows. Found by the 2026-09-02 verification pass.
            setFocusSymbol(null);
            setSelectedIndustries(EMPTY_SELECTION);
            // Pressing NEWS on also asks the sources for fresh headlines for
            // the tickers on the board; the list below the toolbar fills in
            // from the store meanwhile. Pressing it off just restores the board.
            if (!newsOnly) refreshNewsFeed(newsSymbols);
            setNewsOnly((on) => !on);
          }}
          aria-pressed={newsOnly}
          data-testid="momx-news-toggle"
          title={
            newsOnly
              ? "Showing only tickers with news from the last 24 hours. Press again for the whole board."
              : newsCount > 0
                ? "Show only the " + newsCount + " scanned tickers with news from the last 24 hours (matches or not)"
                : "No scanned ticker has news from the last 24 hours right now"
          }
        >
          <Newspaper size={11} aria-hidden="true" />
          NEWS
          <span className="momx-news-count">{newsCount}</span>
        </button>

        {/* Hidden inside the detached ?popout= window - a button that re-opens
            the window you are already in is just confusing. */}
        {canPopOut && !embedded ? (
          <button
            type="button"
            className={
              "momx-icon-btn momx-popout-btn" +
              (popouts.some((w) => w.list === activeList) ? " is-active" : "")
            }
            onClick={onPopOut}
            title={
              popouts.some((w) => w.list === activeList)
                ? (activeList || "This list") + " is already popped out - click to bring its window to the front"
                : "Pop " + (activeList || "this list") +
                  " out into its own window. Switch list and press again for a second window."
            }
            aria-label={"Pop " + (activeList || "this list") + " out into its own window"}
            aria-pressed={popouts.some((w) => w.list === activeList)}
          >
            <ExternalLink size={13} aria-hidden="true" />
          </button>
        ) : null}

        <button
          type="button"
          className="momx-icon-btn"
          onClick={forceRebuild}
          disabled={loading || rebuilding}
          title="Refresh"
          aria-label="Refresh"
        >
          <RefreshCw size={13} className={loading || rebuilding ? "is-spinning" : ""} aria-hidden="true" />
        </button>

        <button
          type="button"
          className={"momx-icon-btn" + (momoSettingsOpen ? " is-active" : "")}
          onClick={() => setMomoSettingsOpen((open) => !open)}
          title="Momo Alert settings"
          aria-label="Momo Alert settings"
        >
          <Settings size={13} aria-hidden="true" />
        </button>
        {momoSettingsOpen ? (
          <MomoSettings onClose={() => setMomoSettingsOpen(false)} />
        ) : null}
        {newsOpenRow ? (
          <MomxNewsPopover
            symbol={newsOpenSymbol}
            news={newsOf(newsOpenRow)}
            verdict={newsColumnCell(newsOpenRow)}
            onClose={onNewsClose}
          />
        ) : null}

        <div className="momx-toolbar-stats">
          <label className="momx-toggle">
            <input
              type="checkbox"
              checked={matchesOnly}
              onChange={(event) => setMatchesOnly(event.target.checked)}
            />
            <span>Scan matches only</span>
          </label>
          {/* No TOS checkbox (2026-10-01, his order): the scanner runs on TOS data
              permanently; only the "TOS loading n/N" note below remains. */}
          {tosLoadingNote ? (
            <span className="momx-since" data-testid="momx-tos-loading">{tosLoadingNote}</span>
          ) : null}
          {embedded || bear ? null : (
            // ScannerX3's control, one button that CYCLES: ALL -> ⚡ NOW ->
            // ⚡ TODAY -> ALL (2026-09-25 "build flash same as momox3
            // today/now"). The count is how many names hold the bolt.
            <button
              type="button"
              className={"momx-bolt-view is-" + boltView}
              onClick={() => setBoltView(boltView === "all" ? "now" : boltView === "now" ? "today" : "all")}
              title={"Which rows show - click to cycle ALL → ⚡ NOW → ⚡ TODAY. "
                + "⚡ NOW: names passing the gates this second with a live bolt. "
                + "⚡ TODAY: every stock that fired a bolt today (the bolt runs all session, 09:25-16:05 ET). "
                + "Live every second on the Schwab stream."}
              data-testid="momx-bolt-view"
            >
              {boltView === "all" ? "ALL" : boltView === "now" ? "⚡ NOW" : "⚡ TODAY"}
              <span className="momx-bolt-count">
                {boltView === "now" ? boltCounts.now : boltCounts.today}
              </span>
            </button>
          )}
          {embedded || bear ? null : (
            <label className="momx-toggle" title="H/L ⚡ sort: who sits nearest the top of its last hour, weighted by how big that hour was against the name's usual hour. Refreshed every 10 seconds.">
              <input type="checkbox" checked={hlSort} onChange={(event) => setHlSort(event.target.checked)} />
              <span>H/L⚡ sort</span>
            </label>
          )}
          {embedded ? null : (
            <label className="momx-toggle" title="MomoX-style: when a GO / OPT / SOLO / TURN / 🔥 leader fires, its card pops up by itself (once per ticker per day, up to 3 at a time, wide screens only)">
              <input
                type="checkbox"
                checked={autoCards}
                onChange={(event) => setAutoCards(event.target.checked)}
              />
              <span>Pop-up cards</span>
            </label>
          )}
          <label className="momx-toggle" title="When each ticker entered the scan - the same Time column the History tabs show">
            <input
              type="checkbox"
              checked={showTimeColumn}
              onChange={(event) => setShowTimeColumn(event.target.checked)}
            />
            <span>Time</span>
          </label>
          <span className="momx-stat">
            <span className="momx-stat-label">Matches</span>
            <span className="momx-stat-value">{matchCount}</span>
          </span>
          <span className="momx-stat">
            <span className="momx-stat-label">Tickers</span>
            <span className="momx-stat-value">{universeCount}</span>
            {optionsNote ? (
              <span className="momx-options-gate" title={optionsNote.title} data-testid="momx-options-gate">
                {optionsNote.text}
              </span>
            ) : null}
          </span>
          <span className="momx-stat">
            <span className="momx-stat-label">Updated</span>
            {/* The stamp is for the data ON SCREEN: the cache's build time while
                cached rows are showing, the live time once fresh - never "never"
                with rows visible. A subtle "updating" note rides alongside while
                a rebuild is in flight. */}
            <span className="momx-stat-value">
              {formatUpdatedAt(boardView.generatedAt)}
              {(() => {
                const note = tapeAsOfNote(boardView.tapeAsOf);
                if (!note) return null;
                // Three tones, three meanings. The old code had two and used
                // the loud one for "the market is shut", which is how an amber
                // chip came to read as "the scanner is dead".
                const TONE_CLASS = {
                  stale: "momx-tape-note",
                  closed: "momx-tape-closed",
                  live: "momx-since",
                };
                const TONE_TITLE = {
                  stale:
                    "The tape has NOT kept up with the market - the newest bar is"
                    + " further behind than trading hours can explain. Matches cannot"
                    + " change until new bars arrive. This one is worth chasing.",
                  closed:
                    "The market is closed, so this is the last bar of the session -"
                    + " nothing is wrong. The scanner is still running and rebuilding;"
                    + " UPDATED above is when it last ran.",
                  live:
                    "How old the newest bar in this scan is. UPDATED above is when"
                    + " the scan itself ran.",
                };
                return (
                  <span
                    className={TONE_CLASS[note.tone] || "momx-since"}
                    title={TONE_TITLE[note.tone] || TONE_TITLE.live}
                  >
                    {note.text}
                  </span>
                );
              })()}
              {boardView.updating ? (
                <span
                  className="momx-updating"
                  title={
                    showingCache
                      ? "Showing the last saved board while a fresh one is built."
                      : "Showing the previous scan while the next complete scan is built."
                  }
                >
                  updating…
                </span>
              ) : null}
            </span>
          </span>
        </div>
      </div>

      <MomentumStrip
        events={momentumStrip.visible}
        overflow={momentumStrip.overflow}
        nowMs={momentumNow}
        focusSymbol={focusSymbol}
        onPick={onPickMomentum}
        onClear={clearMomentum}
      />

      {focusSymbol ? (
        <p className="momx-banner is-notice momx-focus-note">
          <span>
            {"Showing only " + focusSymbol + " (picked from the momentum strip)."}
          </span>
          <button type="button" className="momx-chip-clear" onClick={() => setFocusSymbol(null)}>
            clear
          </button>
        </p>
      ) : null}

      {chips.length > 0 ? (
        <div className="momx-industry-bar">
          <button
            type="button"
            className={"momx-industry-toggle" + (showIndustries ? " is-open" : "")}
            aria-expanded={showIndustries}
            onClick={() => setShowIndustries((on) => !on)}
            title={showIndustries ? "Hide the industry filter" : "Show the industry filter"}
          >
            <ChevronDown
              size={13}
              aria-hidden="true"
              className="momx-industry-caret"
            />
            <span>Industries</span>
            <span className="momx-industry-total">{chips.length}</span>
          </button>
          {/* The active filter stays visible while the panel is CLOSED. A board
              quietly showing one industry, with nothing on screen saying so, is
              a worse outcome than the two rows this change reclaims. */}
          {selectedIndustries.size > 0 ? (
            <span className="momx-industry-active">
              {[...selectedIndustries].join(", ")}
              <button
                type="button"
                className="momx-chip-clear"
                onClick={() => setSelectedIndustries(EMPTY_SELECTION)}
              >
                clear
              </button>
            </span>
          ) : null}
        </div>
      ) : null}

      {chips.length > 0 && showIndustries ? (
        <div className="momx-chip-row">
          {chips.map((chip) => {
            const active = selectedIndustries.has(chip.name);
            return (
              <button
                key={chip.name}
                type="button"
                className={"momx-chip" + (active ? " is-active" : "")}
                style={{ "--momx-chip": chip.color }}
                aria-pressed={active}
                onClick={() => onToggleIndustry(chip.name)}
                title={
                  active ? "Showing " + chip.name + ". Click again to clear it." : "Show only " + chip.name
                }
              >
                {chip.name}
                <sup className="momx-chip-count">{chip.count}</sup>
              </button>
            );
          })}
          {selectedIndustries.size > 0 ? (
            <button
              type="button"
              className="momx-chip-clear"
              onClick={() => setSelectedIndustries(EMPTY_SELECTION)}
            >
              clear
            </button>
          ) : null}
        </div>
      ) : null}

      {sectors.length > 0 ? (
        <div className="momx-industry-bar momx-sector-bar">
          <button
            type="button"
            className={"momx-industry-toggle" + (showSectors ? " is-open" : "")}
            aria-expanded={showSectors}
            onClick={() => setShowSectors((on) => !on)}
            title={showSectors ? "Hide the sector cards" : "Show the sector cards"}
            data-testid="momx-sector-toggle"
          >
            <ChevronDown size={13} aria-hidden="true" className="momx-industry-caret" />
            <span>Sectors</span>
            <span className="momx-industry-total">{sectors.length}</span>
          </button>
          {/* Closed: the names still show, so a HOT sector is never hidden
              without a trace. */}
          {!showSectors ? (
            <span className="momx-sector-summary">
              {sectors.map((sector) => (sector.hot || sector.late ? "🔥" : "") + sector.name).join(" · ")}
            </span>
          ) : null}
        </div>
      ) : null}

      {sectors.length > 0 && showSectors ? (
        <div className="momx-sectors" aria-label="Sector rotation">
          {sectors.map((sector) => {
            const active = selectedIndustries.has(sector.name);
            const clean = sector.up === sector.total;
            const avg = (sector.avg >= 0 ? "+" : "") + sector.avg.toFixed(1) + "%";
            return (
              <button
                key={sector.name}
                type="button"
                className={
                  "momx-sector" + (active ? " is-active" : "") + (clean ? " is-clean" : "") + (sector.hot || sector.late ? " is-hot" : "")
                }
                style={{ "--momx-sector": sector.color }}
                aria-pressed={active}
                onClick={() => onToggleIndustry(sector.name)}
                title={
                  (sector.hot
                    ? "HOT SECTOR (one of up to 2 a day, lit 09:35-11:00): " + sector.breadthUp + " of " + sector.breadthTotal +
                      " up and above VWAP, and its 3-day trend is strengthening. Context, not a trade by itself - look for a " +
                      "GO / OPT inside it, and do not chase the sector's top gainer. "
                    : sector.late
                      ? "HOT (moving now): " + sector.name + " is one of the two strongest other sectors right now - " +
                        sector.breadthUp + " of " + sector.breadthTotal + " up and above VWAP, median move " +
                        (sector.today === null ? "?" : (sector.today >= 0 ? "+" : "") + sector.today.toFixed(1)) +
                        "% today. Shown so you see where money is going; in the back-test, sectors that only started " +
                        "moving later did not win more than they lost as same-day trades - watch it, and for tomorrow. "
                      : "") +
                  (active
                    ? "Showing " + sector.name + ". Click again to clear it."
                    : "Show only " + sector.name)
                }
              >
                <span className="momx-sector-mark" aria-hidden="true">{sector.hot || sector.late ? "🔥" : "▲"}</span>
                <span className="momx-sector-name">{sector.name}</span>
                <span className="momx-sector-count">
                  {sector.bear
                    ? sector.breadthUp + " of " + sector.breadthTotal + " down & below VWAP"
                    : <>{sector.up} of {sector.total} up &ge;{SECTOR_MOVE_PCT}%</>}
                </span>
                <span className="momx-sector-avg">avg {avg}</span>
                {sector.cooled ? (
                  <span className="momx-sector-rs is-cooled">
                    was 🔥 · now {sector.today !== null ? (sector.today >= 0 ? "+" : "") + sector.today.toFixed(1) + "%" : "negative"} - cooled, no longer a bull sector today
                  </span>
                ) : null}
                {sector.hot || sector.late ? (
                  <span className="momx-sector-rs">
                    {sector.bear ? "FALLING" : "HOT"}
                    {sector.today !== null ? " · today " + (sector.today >= 0 ? "+" : "") + sector.today.toFixed(1) + "%" : ""}
                    {sector.ret3 !== null ? " · 3d " + (sector.ret3 >= 0 ? "+" : "") + sector.ret3.toFixed(1) + "%" : ""}
                    {sector.volUp !== null ? " · vol " + sector.volUp + " of " + sector.breadthTotal : ""}
                  </span>
                ) : null}
                {(sector.hot || sector.late) && sector.leaders.length > 0 ? (
                  <span className="momx-sector-leaders">
                    {sector.leaders.map((leader) => (
                      <span key={leader.symbol} className={"momx-sector-leader" + (leader.vol ? " has-vol" : "")}>
                        {leader.symbol} {(leader.pct >= 0 ? "+" : "") + Number(leader.pct).toFixed(1)}
                        {leader.vol ? " ▲vol" + (volTimeframe(rowBySymbol.get(leader.symbol)) ? " " + volTimeframe(rowBySymbol.get(leader.symbol)) : "") : ""}
                      </span>
                    ))}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}

      {error ? (
        <p className="momx-banner is-error" role="alert">
          <AlertTriangle size={13} aria-hidden="true" />
          <span>{error}</span>
        </p>
      ) : null}
      {!error && notice ? <p className="momx-banner is-notice">{notice}</p> : null}
      {warming && rows.length > 0 ? (
        <p className="momx-banner is-notice">
          <span>Showing the previous scan while a fresh scan is built. These matches are not the new scan results.</span>
        </p>
      ) : null}
      {errorSymbols.length > 0 ? (
        <p className="momx-banner is-warn">
          <span>
            {"No data came back for " +
              errorSymbols.slice(0, 8).join(", ") +
              (errorSymbols.length > 8 ? " and " + (errorSymbols.length - 8) + " more" : "") +
              "."}
          </span>
        </p>
      ) : null}

      {newsOnly ? (
        <MomxNewsFeedBox
          payload={newsFeed}
          busy={newsFeedBusy}
          symbols={newsSymbols}
          onRefresh={() => refreshNewsFeed(newsSymbols)}
        />
      ) : null}

      <div className="momx-table-scroll">
        <table ref={livePin.tableRef} className="momx-table" {...livePin.tableAttrs}>
          <thead>
            <tr className="momx-group-row">
              {liveColumnGroups.map((group) => {
                const pin = group.pinKey ? livePin.headPin(group.pinKey) : null;
                const base = group.label ? "momx-group-label" : "momx-group-gap";
                return (
                  <th
                    key={group.key}
                    colSpan={group.span}
                    className={pin ? base + " " + pin.className : base}
                    style={pin ? pin.style : undefined}
                    scope={group.span > 1 ? "colgroup" : "col"}
                  >
                    {group.label || ""}
                  </th>
                );
              })}
            </tr>
            <tr className="momx-head-row">
              {liveColumnList.map((column) => {
                const active = sort.key === column.key;
                const classes = ["momx-th"];
                if (column.sortable) classes.push("is-sortable");
                if (active) classes.push("is-sorted");
                if (column.kind === "color") classes.push("momx-col-color");
                if (column.kind === "symbol") classes.push("momx-col-symbol");
                const movable = columnLayout.order.includes(column.key);
                if (movable) classes.push("is-col-draggable");
                const pin = livePin.headPin(column.key);
                if (pin) classes.push(pin.className);
                return (
                  <th
                    key={column.key}
                    scope="col"
                    data-col={column.key}
                    className={classes.join(" ")}
                    style={pin ? pin.style : undefined}
                    draggable={movable}
                    {...liveHeader.headerHandlers}
                    aria-sort={active ? (sort.direction === "asc" ? "ascending" : "descending") : "none"}
                    title={
                      column.kind === "color"
                        ? "COLOR"
                        : column.kind === "matchedSince" && newsOnly
                          ? "When the headline was published (ET), newest first - a ticker that matched the scan shows its alert time above the headline time"
                          : undefined
                    }
                    onClick={
                      column.sortable
                        ? () => {
                            if (liveHeader.justDragged()) return;
                            onSort(column);
                          }
                        : undefined
                    }
                  >
                    {column.kind === "color" ? (
                      <span className="momx-visually-hidden">COLOR</span>
                    ) : (
                      <>
                        <span className="momx-th-label">
                          {column.kind === "matchedSince" && newsOnly ? "News" : column.label}
                        </span>
                        {active ? (
                          <span className="momx-sort-arrow">{sort.direction === "asc" ? "▲" : "▼"}</span>
                        ) : null}
                      </>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibleRows.length === 0 ? (
              <tr>
                <td className="momx-empty" colSpan={liveColumnList.length}>
                  <span className="momx-empty-inner">{emptyMessage}</span>
                </td>
              </tr>
            ) : (
              visibleRows.map((row, rowIndex) => {
                const flashNew = freshMatchSet.size > 0 && freshMatchSet.has(row && row.symbol);
                // The worker's matchedSince stamp (when this symbol ENTERED
                // the matched set). Read against the per-render clock above;
                // primitives only, so MomxRow's memo still holds between
                // polls. Absent on old cached boards -> matched is null and
                // the NEW tag falls back to the event feed, exactly as before
                // this stamp existed.
                const matched = matchedSinceLabel(row && row.matchedSince, rowClockMs);
                return (
                  <MomxRow
                    liveLast={liveLastBySymbol[row && row.symbol]}
                    key={(row && row.symbol) || "row-" + rowIndex}
                    row={row}
                    onNewsToggle={onNewsToggle}
                onOpenCard={onOpenCard}
                    onOpenGrade={onOpenGrade}
                    // A BOOLEAN, not the Set: memo then re-renders only the rows
                    // whose flag flips, never the whole board on a 15s tick.
                    // Drives ONLY the background flash; the NEW text badge is
                    // stamp-driven via showNewTag.
                    isNew={flashNew}
                    showNewTag={matched ? matched.isNew : flashNew}
                    sinceLabel={matched && !matched.isNew ? matched.label : null}
                    columns={liveColumnList}
                    newsTime={newsOnly}
                    starred={starredSymbols.has(row && row.symbol)}
                    earnings={earningsMap.get(row && row.symbol) || null}
                    bolt={liveBolts[row && row.symbol] || null}
                  />
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <p className="momx-footnote">
        Colours come straight from the thinkScript studies on the server. The scanner re-runs
        continuously; this view picks up the newest scan every 15 seconds while the tab is open.
        The small number under an RVOL value is how long ago that volume arrived &mdash; 12m is
        happening now, 3h is old news. It brightens under 15 minutes, and pulses when the cell
        is also coloured.
      </p>
        </>
      )}

      {/* PORTALED so the board's own scrolling cannot carry it away - the
          whole point is that it is reachable when scrolling is not
          cooperating. Only while actually scrolled down, so it is invisible
          in normal use. Outside the history/live branch: it used to sit in
          the live branch only, so the 400-row History page - where a way
          home matters most - never had one (2026-09-06). */}
      {scrolledDown
        ? createPortal(
            <button
              type="button"
              className="momx-to-top"
              onClick={scrollToTop}
              data-testid="momx-to-top"
              title="Back to the top of the board"
              aria-label="Back to the top of the board"
            >
              <ArrowUp size={13} aria-hidden="true" />
              TOP
            </button>,
            document.body,
          )
        : null}
    </section>
  );

  // The page's own board is ALWAYS returned. a3d1c3a returned the portal
  // INSTEAD, which moved the single board into the window and left the
  // scanner area of the page empty - the black rectangle he photographed.
  // Each window mounts its OWN board, which is also the only way two lists
  // can be on screen at once: one board cannot show two lists.
  // (Both returns wrap the board in the chart-link context - the tickers read
  // it to become TOS-linked buttons.)
  if (embedded || popouts.length === 0) {
    return <MomxChartLinkContext.Provider value={chartLink}>{panel}</MomxChartLinkContext.Provider>;
  }

  // PORTALS, not position:fixed on the panel. `.momx-scanner-view` carries
  // container-type:inline-size, which makes it the containing block for every
  // fixed descendant: measured 2026-09-02, a `fixed; inset:0` box inside this
  // panel came out 1828x1080 in a 1920x1080 window, and 341px wide on a 375px
  // phone. The same containment already broke fixed positioning once on the
  // chart tab, where the fix was to drop it - not an option here, because the
  // sticky toolbar's 100cqw width cap resolves against this very container.
  //
  // Deliberately NOT aria-modal and with no backdrop: these are windows, not
  // dialogs. The app behind them stays visible and usable - the whole point.
  //
  // The inner wrapper re-uses the `momx-scanner-view` class so every rule
  // scoped to it still applies, above all THE single scroll container that
  // owns both axes. The flex column is the trap documented on
  // `.trading-popout-root.is-momx`: that view is `flex: 1; min-height: 0` and
  // collapses to zero height inside a plain block, taking the board with it.
  // Highest z = the window he raised last. Read once per render rather than
  // per window, so N windows do not each scan the list.
  const frontZ = popouts.reduce((top, win) => (win.z > top ? win.z : top), -Infinity);

  return (
    <>
      <MomxChartLinkContext.Provider value={chartLink}>{panel}</MomxChartLinkContext.Provider>
      {popouts.map((win) =>
        createPortal(
          <div
            className="momx-popout-window"
            role="dialog"
            aria-label={(win.list || "MomX") + " scanner window"}
            style={{ left: win.rect.x, top: win.rect.y, width: win.rect.w, height: win.rect.h, zIndex: 3000 + win.z }}
            onPointerDownCapture={() => focusPopout(win.id)}
          >
            <div
              className="momx-popout-titlebar"
              onPointerDown={(event) => beginPopoutGesture(event, "move", win.id)}
              onPointerMove={movePopoutGesture}
              onPointerUp={endPopoutGesture}
              onPointerCancel={endPopoutGesture}
              onDoubleClick={() =>
                setPopouts((windows) =>
                  windows.map((w) =>
                    w.id === win.id ? { ...w, rect: clampPopoutRect(defaultPopoutRect(0)) } : w,
                  ),
                )
              }
              title="Drag to move. Double-click to re-centre."
            >
              <b>
                {win.kind === "card"
                  ? win.symbol + (win.snapshot ? " · snapshot" : "")
                  : win.list || "MomX Scanner"}
              </b>
              <span>drag to move · corner to resize</span>
              {win.kind === "card" ? null : (
              <button
                type="button"
                className="momx-popout-detach"
                onClick={() => detachPopout(win)}
                onPointerDown={(event) => event.stopPropagation()}
                title="Move this window out of AGX - its own window, for a second monitor or beside thinkorswim"
                aria-label={"Detach the " + (win.list || "scanner") + " window from AGX"}
              >
                <ExternalLink size={12} aria-hidden="true" />
                detach
              </button>
              )}
              <button
                type="button"
                className="momx-popout-close"
                onClick={() => closePopout(win.id)}
                onPointerDown={(event) => event.stopPropagation()}
                title="Close this window (Esc closes the front one)"
                aria-label={"Close the " + (win.list || "scanner") + " window"}
              >
                ×
              </button>
            </div>
            <div className="momx-scanner-view">
              {win.kind === "card" ? (
                // Looked up from the LIVE rows, not copied at open time, so
                // the card keeps refreshing with the board behind it.
                <MomxTickerCard
                  row={
                    win.snapshot
                      || rows.find((r) => String(r.symbol || "").toUpperCase() === win.symbol)
                      || null
                  }
                  stampLabel={
                    win.snapshot
                      ? formatUpdatedAt(win.snapshotAt) + " · from History"
                      : formatUpdatedAt(boardView.generatedAt)
                  }
                  // A snapshot's news freshness is judged as of the snapshot,
                  // the way the history ROW already judges it: by tonight
                  // every headline would fail a Date.now() gate and quietly
                  // disappear from a card about this morning.
                  nowMs={win.snapshot ? win.snapshotAtMs || momentumNow : momentumNow}
                  frozen={Boolean(win.snapshot)}
                  allowWalls={!win.snapshot || win.sameDay}
                  alert={win.alert || null}
                />
              ) : (
                <MomxScannerPanel
                initialList={win.list}
                embedded
                // The front window is the one he raised last. Everything
                // behind it is a board he is not reading.
                background={win.z !== frontZ}
              />
              )}
            </div>
            <div
              className="momx-popout-resize"
              role="separator"
              aria-label="Resize this window"
              onPointerDown={(event) => beginPopoutGesture(event, "resize", win.id)}
              onPointerMove={movePopoutGesture}
              onPointerUp={endPopoutGesture}
              onPointerCancel={endPopoutGesture}
              title="Drag to resize"
            />
          </div>,
          document.body,
          win.id,
        ),
      )}
    </>
  );
}
