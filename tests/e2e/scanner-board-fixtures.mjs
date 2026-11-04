// Shared fixtures for the MomX Scanner end-to-end specs (scanner-board-*).
// Not a spec itself (no .e2e.test.mjs suffix).
//
// A deterministic board for SPY / AAPL / NVDA, answered through the harness's
// fakeReads, so a spec can assert exact counts, orders and tags instead of
// whatever the live market shows at the time it runs. The shape follows the
// worker's payload contract (momx/board.py): every RVOL / SQZ / SKIT cell is
// {value, bg, fg}; the browser paints the colours by name.
//
// With the default FILTERS (RVOL 1h/2h/4h >= 2.5 bullish, OR SQZ 2h/4h fired
// or empty), among the two scan matches only NVDA passes:
//   NVDA  scan match, +3.1%, grade A+, RVOL 1h/2h/4h 3.2 cyan, SQZ orange, MX A+ two hours ago
//   AAPL  scan match, +1.2%, grade A,  RVOL ~1.1 (dark green), SQZ orange
//   SPY   not a match, -0.4%, no grade, quiet RVOL, SQZ empty
// Under ALL, SQZ passes nobody, so nothing passes.

export const STAMP = new Date(Date.now() - 90_000).toISOString();
const HOURS_AGO = (hours) => new Date(Date.now() - hours * 3_600_000).toISOString();

const cell = (value, bg = "black", fg = "white") => ({ value, bg, fg });
const RVOL_TFS = ["5m", "15m", "30m", "1h", "2h", "4h", "D"];
const SQZ_TFS = ["2h", "4h", "D", "Wk"];
const SKIT_TFS = ["2h", "4h", "D", "2D", "3D", "4D", "Wk", "M"];

function frames(timeframes, make) {
  return Object.fromEntries(timeframes.map((tf) => [tf, make(tf)]));
}

function row({ symbol, pct, pass, rvol, sqz, skit, letter, extra = {}, direction = "bull" }) {
  return {
    symbol,
    industry: symbol === "SPY" ? "Index ETF" : "Technology",
    pctChange: pct,
    last: symbol === "SPY" ? 580.25 : symbol === "AAPL" ? 230.1 : 182.4,
    direction,
    scanPass: pass,
    scanReasons: pass ? ["rvol:1h", "sqz:4h"] : [],
    matchedSince: pass ? HOURS_AGO(3) : null,
    rvol: frames(RVOL_TFS, rvol),
    sqz: frames(SQZ_TFS, sqz),
    skittles: frames(SKIT_TFS, skit),
    highLow: { value: pct > 0 ? 0.7 : -0.3, bg: pct > 0 ? "green" : "red" },
    color: { bg: "white" },
    quoteTrend: [],
    sparkline: [],
    grade: letter ? { letter, checks: { skit: 6, skitBullish: ["D", "Wk"], pushRvol: ["1h"] }, reasons: ["fixture"] } : null,
    m5: { state: "building", chart: "above", trigger: 100.5 },
    ...extra,
  };
}

export function bullRows() {
  return [
    row({
      symbol: "NVDA", pct: 3.1, pass: true, letter: "A+",
      rvol: (tf) => (["1h", "2h", "4h"].includes(tf) ? cell(3.2, "cyan", "black") : cell(0.4)),
      sqz: () => cell(1, "orange", "black"),
      skit: (tf) => (tf === "D" ? cell(1, "cyan", "cyan") : cell(1, "black", "cyan")),
      extra: { momoxAPlus: { at: HOURS_AGO(2), weeklies: true, sqz: ["4h"] } },
    }),
    row({
      symbol: "AAPL", pct: 1.2, pass: true, letter: "A",
      rvol: () => cell(1.1, "black", "dark_green"),
      sqz: () => cell(1, "orange", "black"),
      skit: () => cell(1, "black", "cyan"),
    }),
    row({
      symbol: "SPY", pct: -0.4, pass: false, letter: null,
      rvol: () => cell(0.3),
      sqz: () => cell(0, "black", "black"),
      skit: () => cell(1, "black", "magenta"),
    }),
  ];
}

// The BEAR board of the same build: only SPY is a (bear) match.
export function bearRows() {
  return [
    row({ symbol: "SPY", pct: -0.4, pass: true, letter: "B", direction: "bear",
      rvol: () => cell(2.8, "magenta", "black"), sqz: () => cell(1, "magenta", "black"), skit: () => cell(1, "black", "magenta") }),
    row({ symbol: "AAPL", pct: 1.2, pass: false, letter: null, direction: "bear",
      rvol: () => cell(0.3), sqz: () => cell(0, "black", "black"), skit: () => cell(1, "black", "cyan") }),
  ];
}

export function boardFor(list = "Mag7", direction = "bull") {
  const rows = direction === "bear" ? bearRows() : bullRows();
  return {
    generatedAt: STAMP,
    scanCompletedAt: STAMP,
    direction,
    list,
    universe: ["NVDA", "AAPL", "SPY"],
    universeCount: 3,
    rows,
    rest: [],
    errors: {},
    dataSource: "tos",
  };
}

const builtIn = (name, count) => ({ name, count, builtAt: STAMP, warming: false, builtIn: true });
const own = (name, count) => ({ name, count, builtAt: STAMP, warming: false, builtIn: false });

export function listsFixture({ extra = [], active = "Mag7" } = {}) {
  return { lists: [builtIn("Mag7", 10), builtIn("Watchlist", 3), own("Swing", 2), ...extra], active, maxLists: 6 };
}

export const emaList = (timeframe) => ({
  ...builtIn(`21 EMA Touch ${timeframe}`, 1), emaLength: 21, timeframe, timeframes: ["Monthly", "Weekly", "Daily"],
});
export const moversList = () => own("Movers", 2);

export const GRADE_RECORD = {
  source: "recorded", days: ["2026-10-30", "2026-11-02"], direction: "bull",
  letters: { "A+": { count: 12, pctHigherClose: 58.3, avgToClose: 0.42 }, A: { count: 0 } },
};

// Reads every board spec fakes: the board itself (bull or bear by ?dir=),
// the lists, and the per-second live layers that would otherwise repaint the
// fixture rows with the live market. Specs spread their own on top.
export function boardReads({ lists = () => listsFixture() } = {}) {
  return {
    "GET /api/momx-scanner": (read) => {
      const params = new URLSearchParams(read.search);
      return { json: boardFor(params.get("list") || "Mag7", params.get("dir") === "bear" ? "bear" : "bull") };
    },
    "GET /api/momx-scanner/lists": () => ({ json: lists() }),
    "GET /api/momx-scanner/momentum": { json: { events: [], list: "Mag7", generatedAt: STAMP } },
    "GET /api/momx-scanner/grade-record": { json: GRADE_RECORD },
    "GET /api/momx-scanner/live-rvol": { json: { cells: {} } },
    "GET /api/momx-live-quotes": { json: { quotes: {}, serving: false, leased: 0 } },
    "GET /api/momx-live-bolts": { json: { bolts: {} } },
  };
}

// Storage that starts every board spec the same way: Mag7, BULL, Live, and no
// pop-up cards (a fresh setup would otherwise float a card over the board).
export const BOARD_STORAGE = {
  "momx.scanner.activeList": "Mag7",
  "momx-auto-cards-on": "0",
};

export async function boardReady(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 60_000 });
  await page.locator("tr.momx-row, td.momx-empty").first().waitFor({ timeout: 60_000 });
}

export async function rowSymbols(page) {
  return page.locator("table.momx-table:not(.momx-history-table) tbody tr.momx-row td.momx-col-symbol [data-testid='momx-symbol-link']").allInnerTexts();
}

export async function liveHeaderKeys(page, tableSelector = "table.momx-table:not(.momx-history-table):not(.momx-find-table)") {
  return page.locator(`${tableSelector} thead tr.momx-head-row th[data-col]`).evaluateAll((ths) => ths.map((th) => th.getAttribute("data-col")));
}

export function writesTo(app, path) {
  return app.writes.filter((write) => write.path === path);
}
