// The virtual BOT page (2026-09-28: "create one left side panel below MomX
// Scanner ... which rule is good, which tickers took, PNL"). Admin only - the
// worker refuses /api/momx-scanner/bot to anyone else. Reads the option
// tracker's days (momx/option_track.py): at every GO+ / GO / ⭐1 / MX A+ /
// Daily 2 / market-turn signal (and, as a test, the first 15 a day of each
// other Setup tag) it "bought" the nearest weekly OTM option at the mid.
// NO order is ever sent - paper numbers from real quotes.
//
// Every trade is scored four ways (his exits, 2026-09-28):
//   exit   the weekly at the first 5m close below VWAP / EMA21, else 15:50
//   zero   the same signal on a 0DTE contract, closed at that exit / 15:50
//   max    the weekly's best price until it expires ("hold max profit")
//   close  the weekly at 15:50

export const BOT_RULE_LABELS = Object.freeze({
  goRvolMacd: "GO+ (GO + short RVOL + MACD)",
  go: "GO",
  star1: "⭐1",
  mxaplus: "MX A+",
  daily2: "Daily 2",
  turn: "Market turn",
  c2h: "C2H / C4H",
  call2h: "CALL2H / 4H",
  adx: "ADX cyan",
  skit: "SKIT",
  rvol: "RVOL",
  sqz: "SQZ fire",
  zs: "ZS rule",
  zs2: "ZS (no day-high block)",
  sectorHot: "🔥 Sector (hot)",
  sectorMove: "🔥 Sector (moving)",
  openPath: "ABVX rule",
  solo: "SOLO big money",
  hotLead: "🔥#1/#2 sector leader",
  dayLine: "Daily line (above yday high)",
  dayReclaim: "Gap down, took back yday low",
  dayLine100: "Daily line $100 (calls)",
  mom: "MOM↑ (momentum line cyan)",
});
// Plain-language meaning of each rule (his ask 2026-09-28: "for each rule,
// give me explanation ... so i know what is missing"). "Needs" = every
// condition; the back-test line is Sep 1-25 on stock prices.
export const BOT_RULE_HELP = Object.freeze({
  goRvolMacd: { what: "GO, plus fresh buying volume and momentum turning up.", needs: "Everything GO needs, AND a cyan RVOL on 5m, 15m or 30m, AND 5-minute MACD above its signal line and rising, on completed candles.", tested: "Beat random picks in the back-test (small sample)." },
  go: { what: "Gap and go: the stock gapped up and broke out in the first hour.", needs: "Grade A+ or A; gap up 2% or more; above VWAP, the open and the first 5-minute high by 10:30; 30-minute buyers in control (+DI above -DI).", tested: "The best rule in the back-test (about 64% win on option names)." },
  star1: { what: "The #1 of today's best setups on that board.", needs: "Ranking: GO in a hot or moving sector first, then other GO, then OPT (first seen before 10:00). Fewer warnings (fading, below VWAP, up 8%+, 30m sellers, bad news) rank higher.", tested: "Beat random picks in the back-test." },
  mxaplus: { what: "The MomoX-style A+: news + volume + squeeze + trend.", needs: "News in the last 24h; RVOL cyan (3 or more) on ANY of 15m/30m/1h/2h/4h/D - e.g. 15m at 5 counts even when 30m is 1; a squeeze firing on any of 30m/1h/2h/4h/D/W; Skittles cyan or green on ANY of D-Mo; not an ETF. (Call-wall check removed 2026-09-29.)", tested: "The old version (all Skittles, call wall) had the highest +4% run rate in the back-test. This 2026-09-29 version is not back-tested yet." },
  daily2: { what: "The first two stocks each day that pass Strategy V2 with volume on 2+ timeframes.", needs: "A+ grade after 09:35, momentum Extended, not up 10%+, RVOL on at least two timeframes. Only the first two a day.", tested: "Good in the back-test, lost live on several days." },
  turn: { what: "Market turn: SPY recovers and these are the strongest names at that moment.", needs: "SPY back above its VWAP after a morning dip; the stock is one of the 5 strongest then: above VWAP, up 1%+, 30-minute buyers in control (ADX 20+).", tested: "Small edge in a 20-day test (44% hit +2% first)." },
  c2h: { what: "The chart's short arrow: the 2H or 4H trend just crossed up, but the bigger trend has not turned yet.", needs: "A C2H or C4H arrow today (4x8 or 9x20), taken the first time the scanner sees it - even if the chart later repaints it away.", tested: "TEST - arrows alone did no better than random." },
  call2h: { what: "The chart's full arrow: the 2H or 4H crossed up AND the bigger trend agrees.", needs: "A CALL2H or CALL4H arrow today. Note: the 'bigger trend agrees' part is only known when the higher candle closes, so some CALL arrows appear hours after the candle they are drawn on.", tested: "TEST - arrows alone did no better than random." },
  adx: { what: "Strong trend right now on the 5-minute chart.", needs: "5m ADX above 25 with +DI above 25 (the white line above yellow on a cyan background), on a graded row.", tested: "TEST - in the back-test the move had usually already run." },
  skit: { what: "A Skittles block (2h to Monthly) just turned cyan/green.", needs: "A with-trend cross first seen in the last hour that the block still shows.", tested: "TEST - never back-tested on its own." },
  rvol: { what: "Heavy buying volume right now.", needs: "An RVOL cell painted cyan or green on 5m-D on today's bar.", tested: "TEST - RVOL already built on 30m-4h came late in the back-test." },
  sqz: { what: "A 2h or 4h squeeze just fired (released).", needs: "The squeeze cell changed to fired during market hours today.", tested: "TEST - never back-tested on its own." },
  zs: { what: "Your ZS entry: above every line with a clear path.", needs: "5m close above EMA 9, 21, 50, VWAP and the Ichimoku cloud, AND nothing within 2% above: today's high, premarket high, yesterday's high, pivot P/R1/R2. (9eD/21eD, weekly MAs and the High-OI wall are not in the live check.)", tested: "TEST - back-test about 0% a trade." },
  sectorHot: { what: "A leader of one of the day's two HOT sectors.", needs: "The stock is up and above VWAP and listed among its sector's leaders while the sector is one of the two latched HOT sectors (lit 09:35-11:00: most members up and above VWAP, 3-day strength rising).", tested: "TEST - back-test: up + above-VWAP members of a hot sector +0.59%/trade, 34% hit +2% first (best sector result)." },
  sectorMove: { what: "A leader of a sector that is 'moving now' (also shown with 🔥).", needs: "The stock is up and above VWAP and listed among its sector's leaders while the sector is moving now (60%+ of members up and above VWAP, median up 0.5%+) - e.g. Cybersecurity on 09-28: PANW, ZS, CRWD, NET.", tested: "TEST - back-test: +0.15%/trade (weaker than HOT sectors)." },
  openPath: { what: "Your ABVX rule: at the open, above EMA 9/21, VWAP and the cloud, a clear $0.50 above, and a chart arrow.", needs: "09:30-10:30; 5m close above EMA 9 and EMA 21, VWAP and the Ichimoku cloud (the 50 EMA no longer needed - your update 09-30); no level within +$0.50 (pivots P/R1/R2, yesterday's high, premarket high - today's own high does not count); a CALL2H / C2H / C4H / CALL4H arrow on the chart today that has not vanished. High OI is not a filter (it passes ~95% of names).", tested: "TEST - back-test scheduled after the close on 09-28." },
  solo: { what: "SOLO: big money going into ONE stock while its sector is not moving.", needs: "09:45-11:00; volume so far 5x-20x normal for the time of day, up 1%+, above VWAP and the 09:30 candle, price $5+, its sector NOT moving with it.", tested: "TEST as an option trade - on stock prices (Sep 1-23) 57% ran +4% at some point, but only about half were still up at the close: a fast mover." },
  hotLead: { what: "🔥#1 / #2: the top two leaders of a hot sector.", needs: "The scanner's 🔥#1 or 🔥#2 tag - the strongest stocks of the day's hot sector.", tested: "TEST - hot-sector members did +0.59%/trade on stock prices (Sep 1-25); never tested as options." },
  dayLine: { what: "Your daily-line trade: the stock opens past yesterday's high (calls) or low (puts) and the 5m candle closes on that side of the 21 EMA with rising volume or momentum.", needs: "One of the board's 30 biggest stocks (by dollar volume), or SPY / QQQ; 09:35-11:00; open above yesterday's high (puts: below yesterday's low); 5m close above the 21 EMA (puts: below); volume up (more than the last bar and 1.5x the last 30 min) OR MACD rising above its signal (puts: falling below).", tested: "TEST - back-test Sep 1-28 (biggest stocks): calls +0.63-0.75% a trade vs +0.13-0.30% random (few trades, not proven); puts no better than random." },
  dayReclaim: { what: "The reverse (ZS 09-28): opened past yesterday's line the wrong way, and the first 5m candle closed straight back across it.", needs: "Calls: open below yesterday's low, the 09:30 candle closes back above it. Puts: open above yesterday's high, the 09:30 candle closes back below it. Any stock, plus SPY / QQQ (no other ETFs).", tested: "TEST - back-test Sep 1-28 flips: calls -0.11% vs -0.14% random (Sep 1-16), +0.55% vs +0.17% (Sep 17-28)." },
  dayLine100: { what: "Your daily line with your filters: $100+ stock, opened past yesterday's high (calls) or low (puts), $0.50 of room to the next daily line.", needs: "Open $100 or more; open above yesterday's high (puts: below yesterday's low); no daily line (the last 3 days' open / high / low / close) within $0.50 of the price in the trade's direction; 5m close above the 21 EMA (puts: below) with volume rising or MACD momentum; 09:35-11:00. Any stock, plus SPY / QQQ.", tested: "TEST - back-test Sep 1-28: CALLS held to the close +0.36% / +0.58% a trade vs +0.10% / +0.25% for random $100+ stocks - beat random in both halves (one up month). Your 21 EMA exit made about half that. PUTS: no edge." },
  mom: { what: "MOM↑: the chart's Squeeze Momentum line is cyan - buying getting stronger (bear MOM↓: magenta, selling getting stronger).", needs: "On the last 5-minute candle the Squeeze Momentum line (shared_SqueezeChain24, length 20) is above 0 and rising (bear: below 0 and falling). Same as the MOM tag in the Setup column. 09:30-15:30, first time per stock per day.", tested: "TEST - this plain version is not back-tested. With ADX above 20 and RVOL added, this week did the same as random stocks (84 trades, 29% win)." },
  zs2: { what: "Your ZS entry, but today's own high does not block it.", needs: "Same lines as ZS; the clear path ignores today's high and the premarket high (ABVX 09-28 was blocked only by its own day high).", tested: "TEST - back-test -0.22% overall, +0.09% on Watchlist names." },
});
// The same rules on the BEAR board (puts) read the bearish mirror (2026-09-28,
// "same as bull rules need bear bot"), so a few names flip.
export const BOT_RULE_LABELS_BEAR = Object.freeze({
  ...BOT_RULE_LABELS,
  c2h: "P2H / P4H",
  call2h: "PUT2H / 4H",
  adx: "ADX magenta",
  zs2: "ZS (no day-low)",
  sectorHot: "🔥 Sector falling (early)",
  sectorMove: "🔥 Sector falling",
  openPath: "ABVX rule (down)",
  dayLine: "Daily line (below yday low)",
  dayReclaim: "Gap up, lost yday high",
  dayLine100: "Daily line $100 (puts)",
  mom: "MOM↓ (momentum line magenta)",
});
export function botRuleLabel(rule, side = "bull") {
  const map = side === "bear" ? BOT_RULE_LABELS_BEAR : BOT_RULE_LABELS;
  return map[rule] || rule;
}
export const BOT_TEST_RULES = new Set(["c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath", "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom"]);
export const BOT_RULE_ORDER = ["goRvolMacd", "go", "star1", "mxaplus", "daily2", "turn", "c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath", "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom"];

function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function pct(from, to) {
  const a = num(from);
  const b = num(to);
  return a === null || a <= 0 || b === null ? null : ((b - a) / a) * 100;
}

/** The four results of one trade, in % of the option's entry price. */
export function botTradeResults(t) {
  const entry = num(t && t.entry);
  const exitPrice = num(t && t.exitPrice) ?? num(t && t.last);
  return {
    exit: pct(entry, exitPrice),
    exitDollars: entry === null || exitPrice === null ? null : (exitPrice - entry) * 100,
    zero: pct(t && t.zEntry, num(t && t.zExit) ?? num(t && t.zLast)),
    // The chain's ENTRY strike (delta just under 0.20), same expiry and exit.
    entryStrike: pct(t && t.eEntry, num(t && t.eExit) ?? num(t && t.eLast)),
    max: pct(entry, num(t && t.holdBest) ?? num(t && t.best)),
    close: pct(entry, num(t && t.close) ?? num(t && t.last)),
  };
}

/** The exit-rule P/L %. */
export function botTradePnl(t) {
  return botTradeResults(t).exit;
}

function avg(values) {
  const v = values.filter((x) => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

function stats(rows) {
  const priced = rows.filter((r) => r.results.exit !== null);
  const wins = priced.filter((r) => r.results.exit > 0).length;
  return {
    trades: rows.length,
    priced: priced.length,
    wins,
    losses: priced.filter((r) => r.results.exit < 0).length,
    winRate: priced.length ? (wins / priced.length) * 100 : null,
    avgExit: avg(priced.map((r) => r.results.exit)),
    avgZero: avg(rows.map((r) => r.results.zero)),
    zeroCount: rows.filter((r) => r.results.zero !== null).length,
    avgEntryStrike: avg(rows.map((r) => r.results.entryStrike)),
    entryStrikeCount: rows.filter((r) => r.results.entryStrike !== null).length,
    avgMax: avg(rows.map((r) => r.results.max)),
    avgClose: avg(rows.map((r) => r.results.close)),
    dollars: priced.reduce((sum, r) => sum + (r.results.exitDollars || 0), 0),
    // ROI (his ask 2026-09-28): profit / what the contracts cost, 1 each.
    invested: priced.reduce((sum, r) => sum + (num(r.entry) || 0) * 100, 0),
    roi: (() => {
      const cost = priced.reduce((sum, r) => sum + (num(r.entry) || 0) * 100, 0);
      return cost > 0 ? (priced.reduce((sum, r) => sum + (r.results.exitDollars || 0), 0) / cost) * 100 : null;
    })(),
    // More columns (his ask 2026-09-30 "add more column"): size of the average
    // win and loss, the best and worst trade, how many days it made money,
    // trades a day, and profit factor ($ won / $ lost).
    ...extraStats(priced),
  };
}

function extraStats(priced) {
  const winsR = priced.filter((r) => r.results.exit > 0);
  const lossR = priced.filter((r) => r.results.exit < 0);
  const byDay = new Map();
  for (const r of priced) byDay.set(r.day, (byDay.get(r.day) || 0) + (r.results.exitDollars || 0));
  const won = winsR.reduce((s, r) => s + (r.results.exitDollars || 0), 0);
  const lost = -lossR.reduce((s, r) => s + (r.results.exitDollars || 0), 0);
  const pcts = priced.map((r) => r.results.exit);
  return {
    avgWin: avg(winsR.map((r) => r.results.exit)),
    avgLoss: avg(lossR.map((r) => r.results.exit)),
    best: pcts.length ? Math.max(...pcts) : null,
    worst: pcts.length ? Math.min(...pcts) : null,
    daysTraded: byDay.size,
    daysUp: [...byDay.values()].filter((v) => v > 0).length,
    perDay: byDay.size ? priced.length / byDay.size : null,
    profitFactor: lost > 0 ? won / lost : (won > 0 ? Infinity : null),
  };
}

/**
 * days: [{day, trades}] from the endpoint. Returns the trade rows (newest
 * first), the rule leaderboard (best average exit first; rules with no priced
 * trade last) and the totals.
 */
export function botReport(days, { side = "bull" } = {}) {
  const rows = [];
  for (const d of Array.isArray(days) ? days : []) {
    for (const t of Array.isArray(d && d.trades) ? d.trades : []) {
      if (!t || !t.symbol || !t.rule) continue;
      rows.push({ ...t, day: d.day, side, label: botRuleLabel(t.rule, side), test: BOT_TEST_RULES.has(t.rule), results: botTradeResults(t) });
    }
  }
  rows.sort((a, b) => String(b.at || b.day).localeCompare(String(a.at || a.day)));
  const leaderboard = BOT_RULE_ORDER.map((rule) => ({
    rule, label: botRuleLabel(rule, side), test: BOT_TEST_RULES.has(rule), ...stats(rows.filter((r) => r.rule === rule)),
  }))
    .filter((r) => r.trades > 0)
    .sort((a, b) => (b.avgExit ?? -Infinity) - (a.avgExit ?? -Infinity));
  return { rows, leaderboard, total: stats(rows), days: (Array.isArray(days) ? days : []).map((d) => d.day) };
}

export function formatBotPct(value, digits = 0) {
  if (value === null || value === undefined) return "-";
  return (value >= 0 ? "+" : "") + value.toFixed(digits) + "%";
}

export function formatBotDollars(value) {
  if (value === null || value === undefined) return "-";
  const abs = Math.abs(value);
  return (value >= 0 ? "+$" : "-$") + (abs >= 1000 ? abs.toLocaleString("en-US", { maximumFractionDigits: 0 }) : abs.toFixed(0));
}

/** "75C 10/16" from a trade (P for puts); zero = the 0DTE leg. */
export function botContractLabel(t, zero = false) {
  const strike = num(zero ? t.zStrike : t.strike);
  const contract = String((zero ? t.zContract : t.contract) || "");
  if (strike === null) return zero ? (t.zStatus === "none" ? "no 0DTE" : "-") : t.status === "pending" ? "pending" : "-";
  const side = /\d{6}([CP])\d/.exec(contract);
  const exp = zero ? "0DTE" : String(t.expiry || "").slice(5).replace("-", "/");
  // (the ENTRY-strike leg is labelled by botEntryStrikeLabel)
  return strike + (side ? side[1] : "") + (exp ? " " + exp : "");
}

/** "103C" - the chain's ENTRY strike leg of a trade. */
export function botEntryStrikeLabel(t) {
  const strike = num(t && t.eStrike);
  if (strike === null) return t && t.eStatus === "none" ? "none" : "-";
  const side = /\d{6}([CP])\d/.exec(String(t.eContract || ""));
  return strike + (side ? side[1] : "");
}

// Periods for the "Winners by period" table (his ask 2026-09-28: "daily / 2D /
// 3D / 4D / weekly, 1 week / 2 week / 3 week ... over all - who is winning").
// Counted in TRADING days that have bot data: 1W = the last 5 of them.
export const BOT_PERIODS = Object.freeze([
  { key: "1D", days: 1 }, { key: "2D", days: 2 }, { key: "3D", days: 3 }, { key: "4D", days: 4 },
  { key: "1W", days: 5 }, { key: "2W", days: 10 }, { key: "3W", days: 15 }, { key: "1M", days: 20 },
  { key: "All", days: Infinity },
]);

function newestFirst(days) {
  return (Array.isArray(days) ? days : []).filter((d) => d && d.day).slice().sort((a, b) => String(b.day).localeCompare(String(a.day)));
}

/** rule rows x period columns; each cell = the rule's stats over that period. */
export function botPeriodMatrix(days, periods = BOT_PERIODS, side = "bull") {
  const sorted = newestFirst(days);
  const cols = periods.filter((p, i) => i === 0 || p.days === Infinity || p.days <= Math.max(sorted.length, 1) || periods[i - 1].days < sorted.length)
    .map((p) => ({ ...p, report: botReport(sorted.slice(0, p.days === Infinity ? sorted.length : p.days), { side }), dayCount: Math.min(sorted.length, p.days) }));
  const rules = BOT_RULE_ORDER.filter((rule) => cols.some((c) => c.report.leaderboard.some((r) => r.rule === rule)));
  const best = cols.map((c) => {
    const ranked = c.report.leaderboard.filter((r) => r.priced > 0 && r.avgExit !== null);
    return ranked.length ? ranked[0].rule : null;
  });
  return {
    periods: cols.map((c, i) => ({ key: c.key, days: c.dayCount, best: best[i], total: c.report.total })),
    rows: rules.map((rule) => ({
      rule, label: botRuleLabel(rule, side), test: BOT_TEST_RULES.has(rule),
      cells: cols.map((c) => c.report.leaderboard.find((r) => r.rule === rule) || null),
    })),
  };
}

/** One line per trading day, newest first: totals + best and worst rule. */
export function botDailyLog(days, side = "bull") {
  return newestFirst(days).map((d) => {
    const rep = botReport([d], { side });
    const ranked = rep.leaderboard.filter((r) => r.priced > 0 && r.avgExit !== null);
    return { day: d.day, ...rep.total, best: ranked[0] || null, worst: ranked.length > 1 ? ranked[ranked.length - 1] : null };
  });
}

// "Momentum at entry" (his ask 2026-09-28: "which momentum state to trade") -
// every bot trade carries the 5m state it was taken in (momx/momentum.py).
export const BOT_MOMENTUM_STATES = Object.freeze(["building", "holding", "extended", "fading", "quiet"]);

/** rule rows x momentum-state columns over report rows (botReport().rows). */
export function botMomentumMatrix(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const cell = (subset) => (subset.length ? stats(subset) : null);
  const rules = BOT_RULE_ORDER.filter((rule) => list.some((r) => r.rule === rule));
  const known = (r) => BOT_MOMENTUM_STATES.includes(r.momentum);
  return {
    states: BOT_MOMENTUM_STATES,
    rows: rules.map((rule) => ({
      rule, label: BOT_RULE_LABELS[rule], test: BOT_TEST_RULES.has(rule),
      cells: BOT_MOMENTUM_STATES.map((s) => cell(list.filter((r) => r.rule === rule && r.momentum === s))),
      unknown: list.filter((r) => r.rule === rule && !known(r)).length,
    })),
    totals: BOT_MOMENTUM_STATES.map((s) => cell(list.filter((r) => r.momentum === s))),
    unknown: list.filter((r) => !known(r)).length,
  };
}

// Column sorting for the Bot tables (his ask 2026-09-28: "all the column make
// it sort high/low/no sort"). One click = high first, two = low first, three =
// back to the table's own order. Blanks always sink to the bottom.
export function nextBotSort(sort, key) {
  if (!sort || sort.key !== key) return { key, dir: "desc" };
  if (sort.dir === "desc") return { key, dir: "asc" };
  return { key: null, dir: null };
}

export function sortBotRows(list, sort, getters) {
  const rows = Array.isArray(list) ? list : [];
  const get = sort && sort.key && getters ? getters[sort.key] : null;
  if (!get) return rows;
  const sign = sort.dir === "asc" ? 1 : -1;
  const blank = (v) => v === null || v === undefined || v === "" || (typeof v === "number" && !Number.isFinite(v));
  return rows
    .map((row, index) => ({ row, index, value: get(row) }))
    .sort((a, b) => {
      const ba = blank(a.value);
      const bb = blank(b.value);
      if (ba || bb) return ba === bb ? a.index - b.index : ba ? 1 : -1;
      if (a.value < b.value) return -sign;
      if (a.value > b.value) return sign;
      return a.index - b.index;
    })
    .map((entry) => entry.row);
}
