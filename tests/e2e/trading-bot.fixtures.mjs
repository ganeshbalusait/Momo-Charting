// Canned Bot-page answers for the trading-bot-*.e2e specs (2026-11-04).
// Shapes follow the real routes: GET /api/momx-scanner/bot?days=N
// (api_server, OptionTrackBook.SUMMARY_KEYS per trade), /paper-mirror,
// /paper-learning and /ai-report (momx_worker). Deterministic, so the specs
// can assert counts and labels instead of whatever the live bot holds today.

const ENTRY = { entry: 1.0, entryBid: 0.95, entryAsk: 1.05, zEntry: 0.5, zEntryBid: 0.45, zEntryAsk: 0.55, eEntry: 0.3, eEntryBid: 0.28, eEntryAsk: 0.32 };

function trade(day, n, { rule = "go", symbol = "NVDA", outcome = "win", ...extra } = {}) {
  const hh = String(9 + Math.floor((35 + n) / 60)).padStart(2, "0");
  const mm = String((35 + n) % 60).padStart(2, "0");
  const at = `${day}T${hh}:${mm}:00-04:00`;
  const base = {
    rule, symbol, at, entryAt: at, board: "bull", status: "closed", strike: 200, expiry: "2026-10-31",
    contract: `${symbol}261031C00200000`, zContract: `${symbol}${day.slice(2).replaceAll("-", "")}C00200000`, zStrike: 200,
    eContract: `${symbol}261031C00205000`, eStrike: 205, momentum: "extended", spot: 195, expectedMove: 4, riskOk: outcome !== "loss",
    ...ENTRY, best: 1.6, holdBest: 1.8, holdDone: true,
  };
  if (outcome === "open") {
    return { ...base, status: "open", riskOk: undefined, last: 1.2, lastBid: 1.15, lastAsk: 1.25, lastAt: at, ...extra };
  }
  const exit = outcome === "win" ? 1.4 : 0.6;
  return {
    ...base, exitPrice: exit, exitBid: exit - 0.05, exitAsk: exit + 0.05, exitAt: `${day}T15:10:00-04:00`,
    exitReason: "5m close below VWAP", close: exit + 0.1, closeBid: exit + 0.05, closeAsk: exit + 0.15,
    zExit: outcome === "win" ? 0.9 : 0.2, zExitBid: outcome === "win" ? 0.85 : 0.15, zExitAsk: outcome === "win" ? 0.95 : 0.25,
    zClose: 0.7, zCloseBid: 0.65, zCloseAsk: 0.75, eExit: 0.5, eExitBid: 0.48, eExitAsk: 0.52, ...extra,
  };
}

const AI_AT_ENTRY = { prediction: { phase: "before_entry", probability: 0.61, family: "logistic", scoredAt: "2026-10-28T09:40:00-04:00", target: "win at strategy exit" } };

export const TODAY = "2026-10-28";

// Today: 4 bull trades (3 rules; NVDA scored 70% by the shadow AI), 2 bear trades.
export function todayBull(day = TODAY) {
  return [
    trade(day, 0, { rule: "go", symbol: "NVDA", outcome: "win", aiDecision: AI_AT_ENTRY, aiP: 0.7 }),
    trade(day, 5, { rule: "daily2", symbol: "AAPL", outcome: "loss" }),
    trade(day, 9, { rule: "news", symbol: "SPY", outcome: "open", jevP: 0.8 }),
    trade(day, 12, { rule: "go", symbol: "AAPL", outcome: "win" }),
  ];
}

function todayBear(day = TODAY) {
  return [
    trade(day, 3, { rule: "news", symbol: "SPY", outcome: "win", board: "bear" }),
    trade(day, 7, { rule: "go", symbol: "AAPL", outcome: "loss", board: "bear" }),
  ];
}

const WEEK_DAYS = ["2026-10-28", "2026-10-27", "2026-10-24"];

function many(day, count, offset) {
  const symbols = ["NVDA", "AAPL", "SPY"];
  return Array.from({ length: count }, (_, i) => trade(day, (offset + i) % 300, {
    rule: i % 3 === 0 ? "daily2" : "go", symbol: symbols[i % 3], outcome: i % 4 === 0 ? "loss" : "win",
  }));
}

/** The bot answer for ?days=N: 1 -> today; 2..5 -> 3 days; 6+ -> 5 days and 105 bull trades. */
export function botAnswer(search) {
  const days = Number(new URLSearchParams(search || "").get("days")) || 5;
  if (days <= 1) return { bull: [{ day: TODAY, trades: todayBull() }], bear: [{ day: TODAY, trades: todayBear() }], keepDays: 180 };
  if (days <= 5) {
    return {
      bull: [
        { day: WEEK_DAYS[0], trades: todayBull() },
        { day: WEEK_DAYS[1], trades: [trade(WEEK_DAYS[1], 1, { symbol: "SPY", outcome: "win" }), trade(WEEK_DAYS[1], 2, { rule: "daily2", symbol: "NVDA", outcome: "loss" })] },
        { day: WEEK_DAYS[2], trades: [trade(WEEK_DAYS[2], 1, { rule: "go", symbol: "AAPL", outcome: "win" })] },
      ],
      bear: [{ day: WEEK_DAYS[0], trades: todayBear() }],
      keepDays: 180,
    };
  }
  const month = ["2026-10-28", "2026-10-27", "2026-10-24", "2026-10-23", "2026-10-22"];
  return {
    bull: month.map((day, i) => ({ day, trades: many(day, 21, i * 7) })),
    bear: [{ day: month[0], trades: todayBear() }],
    keepDays: 180,
  };
}

export const BOT_READ = "GET /api/momx-scanner/bot";
export const LEARNING_READ = "GET /api/momx-scanner/paper-learning";
export const AI_READ = "GET /api/momx-scanner/ai-report";
export const MIRROR_READ = "GET /api/momx-scanner/paper-mirror";

export const TEAM_NOT_STARTED = { enabled: false, mode: "post_trade", entryBlocking: false, error: "Learning team not started" };

export const TEAM = {
  enabled: true, mode: "post_trade", entryBlocking: false, primaryProvider: "ollama", backgroundResearchPaused: true,
  quietWindow: "06:30-17:00 ET weekdays", reviewedTrades: 12, pendingTrades: 1, failedTrades: 0,
  snapshotCoverage: { trades: 13, scanner: 10, chart: 9 }, tradeStates: {}, hermesEnabled: false, hermesStatus: null,
  scoreboard: { execution: { calls: 4, graded: 3, correct: 2, hitRate: 67, unclear: 1, pending: 0 } },
  research: {}, error: "", memory: null, memoryError: "", entryPatterns: null, capture: {},
  latest: {
    completedAt: 1761660000,
    scanner: { provider: "ollama", summary: "Two late entries after 15:00." },
    reviewer: { provider: "ollama", summary: "Losses cluster after 15:00.", hypothesis: "Skip entries after 15:00." },
    critic: { provider: "ollama", summary: "Sample is small.", hypothesis: "Test on unseen days.", validation: "Chronological held-out days with fees." },
  },
  experiments: {
    activeId: null, registeredActiveId: null, monitorHealthy: true, busy: false, error: "", experiments: [],
    latest: { id: "exp-1", state: "forward", reason: "Waiting for future paper fills",
              proposal: { reason: "Late entries lose", provider: "ollama" }, frozenAfterDay: "2026-10-27",
              screen: { version: 1, spec: { kind: "entry_after", value: 545 }, reason: "Exploratory only" } },
  },
};

export const AI_REPORT = {
  latest: {
    auc: 0.57, trainDays: 20, testDays: 5, trainTrades: 1200, trainedAt: "2026-10-27T16:45:00",
    bySide: { bull: { auc: 0.58, aiPick: { trades: 10, winRate: 60, roi: 12.5 }, all: { roi: 4.0 } }, bear: { auc: 0.52 } },
    nn: { auc: 0.55 }, aiPick: { roi: 12.5 }, all: { roi: 4.0 },
    perDay: [
      { day: "2026-10-24", aucToDate: 0.54, bullToDate: 0.55, bearToDate: 0.5 },
      { day: "2026-10-27", aucToDate: 0.57, bullToDate: 0.58, bearToDate: 0.52 },
    ],
  },
  history: [{ trainedAt: "2026-10-27T16:45:00", auc: 0.57, champion: "lr", trainDays: 20 }],
  paper: {},
};

function mirrorRow(account, n, side = "bull", status = "closed") {
  const symbol = account === "paper3" ? ["NVDA", "AAPL"][n % 2] : "SPY";
  return {
    buyId: `${account}-b${n}`, buyAt: `${TODAY}T10:0${n}:00-04:00`, buyFilledAt: `${TODAY}T10:0${n}:20-04:00`,
    side, rule: "go", symbol, contract: `${symbol}261031C00200000`, qty: 3, at: `${TODAY}T10:0${n}:00-04:00`,
    botEntry: 1.0, buyFill: 1.0, status, pl: status === "closed" ? 30 : null, stop: 0.8,
    sells: status === "closed" ? [{ qty: 3, price: 1.1, why: "strategy exit" }] : [],
  };
}

export function mirrorDoc(account) {
  const rows = [mirrorRow(account, 1), mirrorRow(account, 2, "bull", "open"), mirrorRow(account, 3, "bear")];
  return {
    account, day: TODAY, enabled: true, reconciling: false, snapshotSource: "memory", snapshotAt: `${TODAY}T11:00:00-04:00`,
    refused: "", error: "", ai: { enabled: true, reason: "Trade first" },
    rules: { book: account === "paper3" ? "AI review - your picked rules" : "AI review experiment", picked: { bull: ["go"], bear: ["go"] },
             orders: "limit at mid", sizing: "3+ contracts", entryTimes: "new buys 9:30-11:00 AM and 1:00-3:30 PM ET", exitPlan: "+100%: sell 20%" },
    totals: {}, rows, history: [{ day: TODAY, rows }],
  };
}

export const MIRRORS = { mirrors: [mirrorDoc("paper3"), mirrorDoc("paper5")] };
