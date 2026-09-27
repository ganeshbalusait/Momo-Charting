// The MomX FILTERS panel: a second gate that runs on top of the scan result.
//
// SHAPE IS DELIBERATELY THE TOS SCAN'S SHAPE (the trader's instruction,
// 2026-09-05: "same as TOS scan"). A TOS scan is a top-level block that says
// ANY of / ALL of, holding groups that each say ANY of / ALL of. So does this.
//
// WHY A SECOND GATE EXISTS AT ALL. The Bull Momo scan is a NET, not a filter:
// its ANY block holds 35 conditions (8 timeframes x 3 crosses, 4 squeeze rows,
// 7 RVOL rows) and one is enough. Measured on the archived 2026-09-04 session
// at 13:00 ET: 208 matches, of which 95 (46%) tripped exactly ONE of the 35,
// and MACD alone accounted for 189 of the firings against RVOL's 15. So the
// board is mostly "a MACD crossed on some timeframe", which is why the trader
// re-reads RVOL / SQZ / Skittles by eye. This module is that re-read.
//
// THE TOP LEVEL IS HIS CHOICE: ANY of (the default) or ALL of. It was not
// always. On 2026-09-05 he chose ANY-only - "Only ANY OF THE FOLLOWING should
// be in filter" - and the block had no top-level dropdown at all. On
// 2026-09-22 he asked for one: "In filter, i want to add "All of the
// following", "any of the following". example. when i select "Rvol with any
// timeframes " and "Skittles with any timeframes" , i choose "all of the
// follwoing" then i see the scanner list only Rvol and skittles matches?".
// So the config now carries a top-level `mode`, always read through
// topMode(). ANY stays the default, so no board moved the day ALL arrived.
//
// IN BOTH MODES A SWITCHED-OFF GROUP IS LEFT OUT, never counted as a failure.
// That is his example exactly: SQZ off, RVOL + SKITTLES on, ALL = the tickers
// with an RVOL hit AND a Skittles cross, whatever their squeeze reads. The
// groups keep their OWN ANY/ALL headers for their timeframes, independent of
// the top one.
//
// WHAT ALL DOES TO THE COUNT. Measured 13:00 ET with all three groups on,
// and shown to him before his 2026-09-05 call (he chose ANY then). Kept
// because it is still the number to know before picking ALL:
//
//     day         matches   ANY of the 3 groups   ALL of the 3 groups
//     2026-09-02      292                   240                     8
//     2026-09-03      294                   235                    21
//     2026-09-04      208                   148                    11
//
// ANY barely narrows because "no squeeze" is the NORMAL state - the SQZ group
// alone passes 125 of 208. The lever that narrows an ANY block is unticking
// groups (RVOL alone passes 15 of 208), which the `on` switch on each group
// gives him. Under ALL it runs the other way: every group he switches on is
// one more thing a ticker must have, which is how all three on leaves 8-21.
//
// EVERYTHING RUNS IN THE BROWSER. The board payload already carries all 358
// rows (`rows` + `rest`) with every RVOL/SQZ/Skittles cell attached - verified
// 308 of 308 rest rows on the live board - so filtering costs no request, no
// worker time and no extra CPU on the scanner. See momx/board.py::_contract_row
// for the payload contract; a cell this module reads must exist there.

// Bump this to force every saved config back to the defaults. coerceFilters
// MERGES a stored document over the defaults, so changing a default alone
// cannot reach anyone who has already opened the panel - their saved value
// wins, which is the whole point of persisting it. v2: SQZ went ALL -> ANY
// (2026-09-05) and he was already carrying a saved "all" on his phone.
// v3: Skittles `role` became `on` + `rankToTop` (below) - a saved v2 document
// would keep the mode that made a ticked group not filter.
// The top-level `mode` (2026-09-22, main 695d638) arrived WITHOUT a bump, on
// purpose (see DEFAULT_MOMX_FILTERS).
// v4 (2026-09-22): Setup / Momentum / Pattern gates for the scanner grade.
// NOT a reset - coerceFilters MIGRATES a v3 document (every field kept,
// including the top-level `mode`; the three gates start at "any"), because
// nothing in v3 changed meaning. A future bump that migrates must likewise
// carry `mode` across like any other field.
export const MOMX_FILTER_VERSION = 4;

// The scanner-grade gates. Values are the only ones the panel offers; any
// other stored value is coerced back to "any".
// v2 / v3 / daily2 (2026-09-23) are STRATEGIES, not letter gates: picking one
// makes it the whole filter (see strategyOf). Added values need no version
// bump - an older document simply never holds them.
export const FILTER_SETUP_VALUES = Object.freeze(["any", "aAndUp", "aPlus", "v2", "v3", "daily2", "g", "chart", "opt", "go", "goRvolMacd", "best", "newsMomo", "bestHot", "solo", "hotLead"]);
export const FILTER_STRATEGY_VALUES = Object.freeze(["v2", "v3", "daily2", "g", "chart", "opt", "go", "goRvolMacd", "best", "newsMomo", "bestHot", "solo", "hotLead"]);
// 2026-09-24 (his ask "use only the best one"): the dropdown offers only
// "best" = GO or OPT. The older strategies stay valid values (a saved config
// keeps working, the scorecard keeps judging them) but are not offered.
// 2026-09-25 10:15 ET, after his losing morning: "revert those changes same
// as yesterday - only new features like GO, OPT..., everything on the
// scanner, the stars" (columns News / Event / frozen stay). ONE switch turns
// off everything added from 2026-09-24 12:47 on: the ⭐ ranks, GO, OPT, SOLO,
// 🔥 hot-sector leaders, the NEWS setup tag, the ⚠ warnings, the Best setups
// family of filters and the 🔥 / 🔸 sector marks. The worker still computes
// them (the scorecard keeps scoring them); set true to bring them back.
// Back ON 2026-09-25 11:15 ET, his ask: "add those back features like before
// 9:30 was working in filters".
export let MOMX_NEW_SETUPS = true;
/** Tests (and a future Settings switch) turn the set back on; ESM live binding. */
export function setMomxNewSetups(on) {
  MOMX_NEW_SETUPS = Boolean(on);
}
export const NEW_SETUP_VALUES = Object.freeze(["opt", "go", "goRvolMacd", "best", "newsMomo", "bestHot", "solo", "hotLead"]);
export const LEGACY_STRATEGY_VALUES = Object.freeze(["v2", "v3", "daily2", "g", "chart", "opt", "go"]);
export const STRATEGY_LABELS = Object.freeze({
  goRvolMacd: "GO + short RVOL + MACD",
  v2: "V2", v3: "V3", daily2: "Daily 2", g: "G", chart: "Chart CALL2H / CALL4H", opt: "Options setup", go: "Gap and go", best: "Best setups", newsMomo: "News + momentum", bestHot: "Best setups in hot sectors", solo: "SOLO big-money stocks", hotLead: "Hot sector leaders",
});

// The META / MRNA picture (2026-09-24, his "we want to catch MRNA, META
// trades"): at 09:30-09:40 both winners had a 2h AND 4h Skittles cross block,
// buyers in control on 30m (+DI above -DI) and 5m momentum Extended; the two
// losers (PLTR, INTC - also A+) had none of it. Back-test 09-01..22 on 647
// A/A+ signals: this picture ran +4% (what a 0.15-delta weekly needs) 31% of
// the time vs 15% for the PLTR/INTC picture. Judged LIVE on the row.
const SKIT_CROSS_BULL = new Set(["cyan", "green", "lime", "dark_green"]);
// The BEAR mirror (spec 2026-09-24): the four bearish paints of the same cell.
const SKIT_CROSS_BEAR = new Set(["magenta", "red", "light_red", "plum"]);

/** A row served on the BEAR board (row.direction === "bear"). Every rule
 * below reads this: the worker already put the bear scan/grade/momentum under
 * the usual names, so only the colour sets and the comparisons flip here. */
export function isBearRow(row) {
  return Boolean(row && row.direction === "bear");
}

export function optionsSetup(row) {
  if (!row || !row.grade || !["A+", "A"].includes(row.grade.letter)) return false;
  const bear = isBearRow(row);
  const set = bear ? SKIT_CROSS_BEAR : SKIT_CROSS_BULL;
  const sk = row.skittles || {};
  const block = (tf) => set.has(sk[tf] && sk[tf].bg);
  const a30 = row.adx && row.adx["30m"];
  const plus = a30 && a30.plus !== null && a30.plus !== undefined ? Number(a30.plus) : NaN;
  const minus = a30 && a30.minus !== null && a30.minus !== undefined ? Number(a30.minus) : NaN;
  const inControl = bear ? minus > plus : plus > minus;
  return block("2h") && block("4h") && Number.isFinite(plus) && Number.isFinite(minus) && inControl
    && Boolean(row.m5 && row.m5.state === "extended");
}

// OPT only counts when it FIRST shows before 10:00 ET (2026-09-24 test,
// Sep 1-24: GO + OPT-before-10:00 = 108 trades, 55% win, +0.52%/trade vs
// all-day OPT 38% / +0.18%; every OPT after 10:30 this week lost or went flat).
// The first time is remembered for the day, so a 09:40 OPT stays listed at
// 11:00 after its momentum cools. Kept in memory + this browser's storage;
// a page first opened after 10:00 cannot know an earlier OPT (limitation).
const OPT_LATCH_KEY = "momx-opt-latch-v1";
const OPT_LATCH_KEY_BEAR = "momx-opt-latch-bear-v1";
const OPT_CUTOFF_MIN = 10 * 60;
// One latch per direction: a bear OPT at 09:40 must not be remembered as a
// bull one (and the other way round) when he flips the switch.
const optLatches = { bull: null, bear: null };
function etParts(ms) {
  const f = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  const o = Object.fromEntries(f.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  return { day: o.year + "-" + o.month + "-" + o.day, min: (Number(o.hour) % 24) * 60 + Number(o.minute) };
}
function loadOptLatch(day, direction = "bull") {
  const held = optLatches[direction];
  if (held && held.day === day) return held;
  const key = direction === "bear" ? OPT_LATCH_KEY_BEAR : OPT_LATCH_KEY;
  let saved = null;
  try {
    saved = JSON.parse(window.localStorage.getItem(key) || "null");
  } catch {
    saved = null;
  }
  optLatches[direction] = saved && saved.day === day && saved.seen && typeof saved.seen === "object" ? saved : { day, seen: {} };
  return optLatches[direction];
}
function saveOptLatch(direction = "bull") {
  try {
    window.localStorage.setItem(direction === "bear" ? OPT_LATCH_KEY_BEAR : OPT_LATCH_KEY, JSON.stringify(optLatches[direction]));
  } catch {
    // private window / no storage: the in-memory latch still works this session
  }
}
/** Test hook. */
export function resetOptLatch() {
  optLatches.bull = null;
  optLatches.bear = null;
  try {
    window.localStorage.removeItem(OPT_LATCH_KEY);
    window.localStorage.removeItem(OPT_LATCH_KEY_BEAR);
  } catch {
    // no storage
  }
}
/** {clock, at(ms)} when this row's OPT first showed today before 10:00 ET, else null. */
export function earlyOpt(row, nowMs = Date.now()) {
  const sym = row && row.symbol;
  if (!sym) return null;
  const direction = isBearRow(row) ? "bear" : "bull";
  const now = etParts(nowMs);
  const latch = loadOptLatch(now.day, direction);
  if (!(sym in latch.seen) && now.min >= 9 * 60 + 30 && now.min < OPT_CUTOFF_MIN && optionsSetup(row)) {
    latch.seen[sym] = nowMs;
    saveOptLatch(direction);
  }
  const at = latch.seen[sym];
  return at ? { at, clock: etClock(new Date(at).toISOString()) } : null;
}

// The AI news reader's verdict (momx/news_catalyst.py -> row.catalyst): WHY
// an A+/A ticker moves. Shown only when the news really explains it - an
// UNKNOWN read is "no news explains this", which is not worth a tag.
const NEWS_WORDS = {
  EARNINGS: "Earnings", UPGRADE: "Upgrade", DOWNGRADE: "Downgrade", CONTRACT: "Contract",
  PRODUCT: "Product", FDA: "FDA", DEAL: "Deal", OFFERING: "Offering", LEGAL: "Legal", MACRO: "Macro",
};
export function newsCatalyst(row) {
  const c = row && row.catalyst;
  if (!c || typeof c !== "object" || !NEWS_WORDS[c.category]) return null;
  const direction = ["bullish", "bearish"].includes(c.direction) ? c.direction : "neutral";
  const arrow = direction === "bullish" ? " ↑" : direction === "bearish" ? " ↓" : "";
  const who = c.provider === "claude" ? "Claude" : c.provider === "openai" ? "Gemini" : "AI";
  const clock = c.at ? etClock(c.at) : "";
  return {
    key: "news-" + direction,
    text: "NEWS " + NEWS_WORDS[c.category] + arrow,
    title: (c.summary || "") + (c.headline ? " Headline: “" + c.headline + "”." : "") +
      " " + direction.charAt(0).toUpperCase() + direction.slice(1) + " news, " + (c.confidence || "low") +
      " confidence. Read by " + who + (clock ? " at " + clock + " ET" : "") + ". AI can be wrong: open the headline before trading on it.",
  };
}

/**
 * His wrong-pick patterns (2026-09-24/25 trades and the Thursday replay), on a
 * row that carries a setup. Short labels for a ⚠ tag; [] when clean.
 */
export function setupWarnings(row) {
  if (!row || typeof row !== "object") return [];
  const out = [];
  const c = row.catalyst;
  if (c && c.direction === "bearish" && NEWS_WORDS[c.category]) out.push("news ↓");
  const pct = row.pctChange === null || row.pctChange === undefined ? NaN : Number(row.pctChange);
  if (Number.isFinite(pct) && pct >= 8) out.push("up " + Math.round(pct) + "%");
  const m5 = row.m5 && typeof row.m5 === "object" ? row.m5 : null;
  if (m5 && m5.state === "fading") out.push("fading");
  const last = row.last === null || row.last === undefined ? NaN : Number(row.last);
  const vwap = m5 && m5.vwap !== null && m5.vwap !== undefined ? Number(m5.vwap) : NaN;
  if (Number.isFinite(last) && Number.isFinite(vwap) && last < vwap) out.push("below VWAP");
  // 30-minute SELLERS in control (-DI above +DI), bull rows only (2026-09-25,
  // "build both"): over 20 sessions these entries won 14-18% and averaged
  // -0.16% to -0.29% - the clearest "skip" filter the back-test found.
  const adx30 = !isBearRow(row) && row.adx && typeof row.adx === "object" ? row.adx["30m"] : null;
  if (adx30 && typeof adx30 === "object") {
    const plus = adx30.plus === null || adx30.plus === undefined ? NaN : Number(adx30.plus);
    const minus = adx30.minus === null || adx30.minus === undefined ? NaN : Number(adx30.minus);
    if (Number.isFinite(plus) && Number.isFinite(minus) && minus > plus) out.push("30m sellers");
  }
  return out;
}

/**
 * Best setups, RANKED by his tested order (2026-09-25, "rank them"):
 *   1) GO inside a 🔥 hot or 🔸 moving-now sector, 2) other GO, 3) OPT;
 *   inside each: 2+ warnings sink to the end, then a bullish NEWS read first,
 *   then the earlier signal. The ORDER is the ranking - nothing is hidden.
 */
export function rankBestSetups(rows, nowMs = Date.now()) {
  const list = Array.isArray(rows) ? rows.slice() : [];
  const rank = (row, index) => {
    const go = gapAndGo(row, nowMs);
    const opt = go ? null : earlyOpt(row, nowMs);
    const s = row && row.sectorRotation;
    const inFlow = Boolean(s && (s.hot || s.late));
    const tier = go ? (inFlow ? 0 : 1) : opt ? 2 : 3;
    const warn = setupWarnings(row).length;
    const c = row && row.catalyst;
    const newsUp = c && c.direction === "bullish" && ["high", "medium"].includes(c.confidence) ? 0 : 1;
    const at = go ? go.at * 1000 : opt ? opt.at : Infinity;
    // Fewer ⚠ first inside a tier (a clean GO above a GO that is up 9%).
    return { row, index, key: [tier, warn, newsUp, at] };
  };
  return list
    .map(rank)
    .sort((a, b) => {
      for (let i = 0; i < a.key.length; i += 1) {
        if (a.key[i] !== b.key[i]) return a.key[i] < b.key[i] ? -1 : 1;
      }
      return a.index - b.index;
    })
    .map((entry) => entry.row);
}

/** symbol -> 1-based rank among the board's GO / OPT rows (rankBestSetups). */
export function bestSetupRanks(rows, nowMs = Date.now()) {
  if (!MOMX_NEW_SETUPS) return new Map();
  const setups = (Array.isArray(rows) ? rows : []).filter(
    (row) => row && (gapAndGo(row, nowMs) || earlyOpt(row, nowMs)),
  );
  const out = new Map();
  rankBestSetups(setups, nowMs).forEach((row, index) => {
    if (row && row.symbol && !out.has(row.symbol)) out.set(row.symbol, index + 1);
  });
  return out;
}

/** The row's industry is a HOT sector today (momx/sectors.py). */
export function inHotSector(row) {
  return Boolean(row && row.sectorRotation && row.sectorRotation.hot);
}

/** His three steps in order: positive news (AI, medium/high confidence) ->
 * 5m momentum Building or Extended -> letter A+ or A. */
export function newsMomentum(row) {
  const c = row && row.catalyst;
  const wanted = isBearRow(row) ? "bearish" : "bullish";
  if (!c || c.direction !== wanted || !["high", "medium"].includes(c.confidence) || !NEWS_WORDS[c.category]) return false;
  if (!(row.m5 && ["building", "extended"].includes(row.m5.state))) return false;
  return Boolean(row.grade && ["A+", "A"].includes(row.grade.letter));
}

function buyers30(row) {
  const a30 = row && row.adx && row.adx["30m"];
  const plus = a30 && a30.plus !== null && a30.plus !== undefined ? Number(a30.plus) : NaN;
  const minus = a30 && a30.minus !== null && a30.minus !== undefined ? Number(a30.minus) : NaN;
  return Number.isFinite(plus) && Number.isFinite(minus) && plus > minus;
}

/** Sellers in control on 30m (-DI above +DI) - the bear GO's check. */
function sellers30(row) {
  const a30 = row && row.adx && row.adx["30m"];
  const plus = a30 && a30.plus !== null && a30.plus !== undefined ? Number(a30.plus) : NaN;
  const minus = a30 && a30.minus !== null && a30.minus !== undefined ? Number(a30.minus) : NaN;
  return Number.isFinite(plus) && Number.isFinite(minus) && minus > plus;
}

/** Whoever the row's direction needs in control on 30m. */
function inControl30(row) {
  return isBearRow(row) ? sellers30(row) : buyers30(row);
}

// Gap and go (META 2026-09-21: gapped +2.2%, cleared VWAP, the open and the
// 09:30 candle's high on the 09:40 candle, ran +10%). The scanner marks the
// FIRST 5m candle 09:35-10:25 that did it (row.m5.gapGo.goAt, momx momentum
// _gap_go); here: A+/A letter and 30m buyers in control on the live row.
// Back-test 09-01..23, tickers graded before the entry: 61 trades, 44% ran +4%.
export function gapAndGo(row, nowMs = Date.now()) {
  if (!row || !row.grade || !["A+", "A"].includes(row.grade.letter)) return null;
  const g = row.m5 && row.m5.gapGo;
  const t = g && Number(g.goAt);
  // The worker's bear gapGo is gap DOWN and go (below VWAP, the open and the
  // 09:30 LOW); here only the 30m side check flips.
  if (!Number.isFinite(t) || t <= 0 || !inControl30(row)) return null;
  const day = (ms) => new Date(ms).toLocaleDateString("en-US", { timeZone: "America/New_York" });
  if (day(t * 1000) !== day(nowMs)) return null;
  // The clock is the candle's CLOSE - the moment it could be seen.
  return { clock: etClock(new Date((t + 300) * 1000).toISOString()), gap: Number(g.gap), at: t };
}

/** Bull-only research combination. Re-evaluated on each snapshot, not latched. */
export function goRvolMacd(row, nowMs = Date.now()) {
  if (!row || isBearRow(row) || !["A", "A+"].includes(row.grade?.letter)) return null;
  const c = row.m5?.goConfirmation;
  const goAt = row.m5?.gapGo?.goAt;
  if (!c || c.macdUp !== true || c.buyers30 !== true
      || !Number.isFinite(c.barAt) || !Number.isFinite(goAt) || goAt <= 0) return null;
  const closeMs = (c.barAt + 300) * 1000;
  // Same freshness window as the replay; no forming/future/previous-day bars.
  if (closeMs > nowMs || nowMs - closeMs > 300000 || (goAt + 300) * 1000 > nowMs) return null;
  const now = etParts(nowMs);
  if (now.min < 570 || now.min > 930 || etParts(goAt * 1000).day !== now.day
      || etParts(closeMs).day !== now.day) return null;
  if (!["5m", "15m", "30m"].some((tf) => row.rvol?.[tf]?.bg === "cyan")) return null;
  return { clock: etClock(new Date(closeMs).toISOString()), atMs: closeMs };
}
// The rules as the worker applies them (momx/strategy.py) - said once, used by
// the footer sentence and the panel hint.
// Judged ONCE, at the ticker's first A or A+ signal of the day, then kept for
// the day - the way the back-test measured them (momx/strategy.py).
export const STRATEGY_RULES = Object.freeze({
  goRvolMacd: "Bull only, 09:30-15:30 ET: A/A+ with a completed GO breakout, buyers in control on completed 30m candles, a cyan RVOL background on any of 5m/15m/30m, and completed 5m MACD (12,26,9) above its signal with histogram increasing. Checked each refresh; disappears when confirmation no longer passes",
  v2: "at its first A+ or A signal today, from 9:35 AM ET: up less than 10%, momentum Extended, RVOL push. Stays on the list for the day",
  v3: "V2 + a squeeze fired (4h / D / Wk) at that signal",
  daily2: "V2 + RVOL push on 2 or more timeframes at that signal, numbered #1, #2, #3 in the order they qualified today",
  // Strategy G = his own method (momx/strategy_g.py), judged all day.
  g: "your rules: RVOL cyan and rising on 30m-D; SQZ 2h/4h fired or none; Skittles 2h cyan cross today after 8 AM + 4h/D cross within 24h; " +
    "5m above EMA20 and VWAP or a C5/CALL5 arrow; a strong call-OI wall as target with the ENTRY call's ROI of 150% or more. " +
    "Exit at the target, at -50% on the option, or at the close",
  // The chart's own arrows (momx/chart_signals.py runs the chart's calculator).
  chart: "the chart printed a CALL2H (last 2 hours) or CALL4H (last 4 hours) arrow - the same arrows as your chart; the time is the chart candle, ET",
  hotLead: "The two strongest stocks (up and above VWAP) of a 🔥 hot sector, fixed when the sector lit (09:35-11:00 ET). CAUTION: in the careful all-day back-test the single #1 stock lost about half the time - the hot sector's GROUP of rising stocks is the reliable signal (45% win, 25% loss), not one name. Best used together with GO / OPT",
  solo: "Big money in ONE stock (09:45-11:00 ET): volume 5-20x its normal for that time of day, up 1%+, above VWAP and the 09:30 close, $5+, while its sector is not moving. Sep 1-23: 57% ran +4%, but about half gave it back by the close - a fast mover, take profit into strength",
  bestHot: "Best setups (GO, or OPT before 10:00) whose industry is a 🔥 HOT sector today - up to 2 sectors, picked between 09:35 and 11:00 ET by one score (how much of it is up and above VWAP + how fast its 3-day lead over SPY is growing). Back-test Sep 1-23: GO in a hot sector won 4 of 6 - a small sample; plain Best setups is the tested everyday choice",
  newsMomo: "His order (2026-09-24): the AI reads the news first and finds it POSITIVE (medium or high confidence), then 5m momentum is Building or Extended, then the letter is A+ or A. Not back-tested yet - the AI reads started 2026-09-25",
  best: "GO (gap and go, listed first) or OPT (turning up now) when OPT first shows before 10:00 ET. Sep 1-24: 55% win, +0.52% per trade on the stock",
  go: "A+ or A that gapped up 2% or more and, between 09:35 and 10:30, closed a 5m candle above VWAP, the open and the first 5-minute high - with buyers in control on 30m. The META Sept 21 picture",
  opt: "Before 10:00 ET only. A+ or A with a fresh Skittles cross on BOTH 2h and 4h, buyers in control on 30m (+DI above -DI) and 5m momentum Extended - the META / MRNA picture of Sept 24. Checked live, all day",
});

// The BEAR wording of the same rules (spec 2026-09-24). Only the rules that
// have a direction are re-said; sector / SOLO / news keep the bull text.
export const STRATEGY_RULES_BEAR = Object.freeze({
  v2: "at its first A+ or A signal today, from 9:35 AM ET: down less than 10%, momentum Extended (down), RVOL selling push. Stays on the list for the day",
  v3: "V2 + a squeeze fired DOWN (4h / D / Wk) at that signal",
  daily2: "V2 + RVOL selling push on 2 or more timeframes at that signal, numbered #1, #2, #3 in the order they qualified today",
  g: "your rules on the sell side: RVOL magenta and rising on 30m-D; SQZ 2h/4h fired down or none; Skittles 2h magenta cross today after 8 AM + 4h/D cross within 24h; " +
    "5m below EMA20 and VWAP or a P5/PUT5 arrow; a strong put-OI wall as target with the ENTRY put's ROI of 150% or more. " +
    "Exit at the target, at -50% on the option, or at the close",
  chart: "the chart printed a PUT2H (last 2 hours) or PUT4H (last 4 hours) arrow - the same arrows as your chart; the time is the chart candle, ET",
  best: "GO (gap down and go, listed first) or OPT (turning down now) when OPT first shows before 10:00 ET. The bull rules mirrored - not back-tested yet; the bear track record starts today",
  go: "A+ or A (bear) that gapped down 2% or more and, between 09:35 and 10:30, closed a 5m candle below VWAP, the open and the first 5-minute low - with sellers in control on 30m",
  opt: "Before 10:00 ET only. A+ or A (bear) with a fresh Skittles cross DOWN on BOTH 2h and 4h, sellers in control on 30m (-DI above +DI) and 5m momentum Extended down. Checked live, all day",
});

/** The rules in the wording of one direction ("bull" | "bear"). */
export function strategyRules(direction) {
  return direction === "bear" ? { ...STRATEGY_RULES, ...STRATEGY_RULES_BEAR } : STRATEGY_RULES;
}

/** "13:00" (ET) from an ISO time, or "". */
function etClock(iso) {
  const t = Date.parse(iso || "");
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}

/**
 * Today's chart arrows on the row, one per label (CALL2H / CALL4H / C2H / C4H),
 * earliest first: [{ label, clock, at, seenAt }].
 */
//: How long an arrow stays on the scanner: one candle of its own timeframe
//: (his ask 2026-09-24: WRBY "CALL2H 09:15" should be gone at 11:15).
const ARROW_LIFE_MS = { "2H": 2 * 3600 * 1000, "4H": 4 * 3600 * 1000 };

export function chartArrows(row, nowMs = Date.now()) {
  const list = row && Array.isArray(row.chartSignals) ? row.chartSignals : [];
  const byLabel = new Map();
  for (const s of list) {
    if (!s || !s.label) continue;
    const life = ARROW_LIFE_MS[String(s.timeframe || String(s.label).slice(-2)).toUpperCase()] || ARROW_LIFE_MS["2H"];
    const at = Date.parse(s.at || "");
    if (!Number.isFinite(at) || nowMs >= at + life) continue; // expired
    if (nowMs < at) continue; // not yet (clock skew)
    // One per label PER FAMILY: the chart draws the 4x8 (yellow) and the 9x20
    // (cyan) crosses as separate arrows, and folding them into one tag hid
    // the 9x20 ones (his catch, 2026-09-24).
    const key = s.label + "|" + (s.family || "");
    const held = byLabel.get(key);
    if (!held || String(s.at) < String(held.at)) byLabel.set(key, s);
  }
  const familyRank = (f) => (f === "4x8" ? 0 : 1);
  return [...byLabel.values()]
    .sort((a, b) => String(a.at).localeCompare(String(b.at)) || familyRank(a.family) - familyRank(b.family)
      || String(a.label).localeCompare(String(b.label)))
    .map((s) => ({ label: s.label, family: s.family || "", clock: etClock(s.at), at: s.at, seenAt: s.seenAt,
      // C2H / C4H (P2H / P4H): the chart's short arrow - a cross against the
      // higher timeframe's trend. The worker ships them only 09:30-16:00 ET.
      compact: Boolean(s.compact) || !/^(CALL|PUT)/.test(String(s.label)) }));
}

/**
 * The first 2h / 4h squeeze FIRE seen today in regular hours, per timeframe,
 * from the grade's own timeline ("SQZ 2h released" - the cell turning to its
 * fired state). 2026-09-25 PYPL: SQZ 2h fired 10:07, C4H 10:10, then +3.3%.
 */
export function sqzFires(row, nowMs = Date.now()) {
  const timeline = row && row.gradeFresh && Array.isArray(row.gradeFresh.timeline) ? row.gradeFresh.timeline : [];
  const today = etParts(nowMs).day;
  const first = new Map();
  for (const e of timeline) {
    const m = /^SQZ (2h|4h) released$/.exec(String((e && e.what) || ""));
    const at = Date.parse((e && e.at) || "");
    if (!m || !Number.isFinite(at) || at > nowMs) continue;
    const p = etParts(at);
    if (p.day !== today || p.min < 9 * 60 + 30 || p.min >= 16 * 60) continue;
    if (!first.has(m[1]) || at < first.get(m[1])) first.set(m[1], at);
  }
  return ["2h", "4h"].filter((tf) => first.has(tf)).map((tf) => ({
    tf, at: first.get(tf), clock: etClock(new Date(first.get(tf)).toISOString()),
  }));
}
export const FILTER_MOMENTUM_VALUES = Object.freeze(["any", "building"]);
export const FILTER_PATTERN_VALUES = Object.freeze(["any", "explosive", "steady", "either"]);

//: Timeframe order per group, as the board's own columns order them.
export const FILTER_RVOL_TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "D"];
export const FILTER_SQZ_TIMEFRAMES = ["2h", "4h", "D", "Wk"];
export const FILTER_SKITTLES_TIMEFRAMES = ["2h", "4h", "D", "2D", "3D", "4D", "Wk", "M"];

//: The top level's two settings, in the order the dropdown lists them.
export const FILTER_TOP_MODES = Object.freeze(["any", "all"]);

// The SQZ cell's background is the whole state; these are the five the column
// can paint (momx/columns.py::squeeze_cell, in its own order). Keyed by the
// name shown in the panel so the checkbox labels and the test are the same
// vocabulary the trader used ("fires or empty").
export const SQZ_STATE_BG = {
  cyan: "cyan",       // squeeze FIRED, momentum rising
  blank: "black",     // no squeeze at all - the cell reads "-"
  magenta: "magenta", // squeeze fired, momentum NOT rising
  orange: "orange",   // still squeezed
  white: "white",     // high-compression squeeze
};

// Every colour the Skittles column can paint, and what fired to cause it
// (momx/columns.py::skittles_cell, in the script's own order). ONLY the
// BACKGROUND is read - cyan TEXT means "9 already above 20", which is a state,
// not an event.
//
// The filter tested `bg === "cyan"` until 2026-09-05 - one of FOUR bullish
// events - so a MACD cross could not be filtered on at all. He caught it:
// "Skittles you didn't add MACD?". Measured on that night's 8 matches: 0 cyan
// cells, but 4 dark_green, 2 green and 1 lime - so the group could only ever
// return nothing.
export const SKITTLES_STATE_BG = {
  cyan: "cyan",             // EMA(9) crossed ABOVE EMA(20)
  green: "green",           // MACD crossed up, with 9 above 20
  lime: "lime",             // EMA(4) crossed above EMA(8), with 9 above 20
  dark_green: "dark_green", // MACD crossed up
  magenta: "magenta",       // EMA(9) crossed BELOW EMA(20)
  red: "red",               // MACD crossed down, with 9 below 20
  light_red: "light_red",   // EMA(4) crossed below EMA(8), with 9 below 20
  plum: "plum",             // MACD crossed down
};

// BEAR mirror of the colour groups (2026-09-26, "filter should work on bull
// and bear - no guess work"). He keeps ONE filter setting; on a BEAR row each
// ticked colour means its down-side twin, exactly as RVOL "bullish only"
// already flips to "bearish only". The pairs are the app's own bear rules,
// not new ones: momx/grade.py SQZ_FIRED_BG {"bull": "cyan", "bear": "magenta"}
// and SKIT_CROSS_BG / SKIT_BEAR_CROSS_BG (cyan/green/lime <-> magenta/red/
// light_red; dark_green <-> plum, the two counter-trend MACD crosses). Before
// this, SQZ / Skittles on the BEAR board picked BULLISH fires and crosses.
export const SQZ_BEAR_MIRROR = Object.freeze({ cyan: "magenta", magenta: "cyan" });
export const SKITTLES_BEAR_MIRROR = Object.freeze({
  cyan: "magenta", magenta: "cyan",
  green: "red", red: "green",
  lime: "light_red", light_red: "lime",
  dark_green: "plum", plum: "dark_green",
});
/** The state name a ticked box stands for on this board direction. */
export function mirroredState(section, name, bear) {
  if (!bear) return name;
  const map = section === "sqz" ? SQZ_BEAR_MIRROR : section === "skittles" ? SKITTLES_BEAR_MIRROR : {};
  return map[name] || name;
}

// A bullish RVOL cell. The BACKGROUND only colours at >= 2, so a threshold
// under 2 has to read the FOREGROUND ladder to know the side (cyan 1.5-2,
// green 1-1.5, dark_green 0.5-1). Below 0.5 the script paints black text and
// the cell genuinely does not record which side won - `bullishOnly` therefore
// cannot judge a threshold under 0.5 and lets the cell through on value alone.
// At the trader's 2.5 this never arises; it is written down so a future low
// threshold does not look like a bug.
// EXPORTED because the history query has to send these exact lists to the
// server, which deliberately holds no colour knowledge of its own. One
// definition, used by the live board and by the archive search - so a search
// result can never disagree with the board that produced it.
export const RVOL_BULLISH_BG = Object.freeze(["cyan", "green"]);
export const RVOL_BULLISH_FG = Object.freeze(["cyan", "green", "dark_green"]);
export const RVOL_BEARISH_BG = Object.freeze(["magenta", "red"]);
export const RVOL_BEARISH_FG = Object.freeze(["magenta", "red", "dark_red"]);

const RVOL_BULLISH_BG_SET = new Set(RVOL_BULLISH_BG);
const RVOL_BULLISH_FG_SET = new Set(RVOL_BULLISH_FG);
const RVOL_BEARISH_BG_SET = new Set(RVOL_BEARISH_BG);
const RVOL_BEARISH_FG_SET = new Set(RVOL_BEARISH_FG);

export const DEFAULT_MOMX_FILTERS = Object.freeze({
  version: MOMX_FILTER_VERSION,
  // The top level: "any" or "all", his choice since 2026-09-22 (see header).
  // "any" is what every board was before the dropdown existed. Adding the key
  // needed NO version bump - a bump would wipe the rest of his tuning - because
  // coerceFilters reads a document that never had it as "any", which is what
  // that document always meant. It governs how the switched-on GROUPS combine.
  mode: "any",
  // Scanner-grade GATES (v4). Unlike the groups below these are requirements
  // ANDed AHEAD of the group logic, whatever the top-level ANY/ALL says:
  // "A+ only" removes a B row whatever its RVOL reads. "any" = the gate takes
  // no part.
  setup: "any",
  momentum: "any",
  pattern: "any",
  rvol: {
    on: true,
    mode: "any",
    bullishOnly: true,
    // Thresholds seeded from his Momo Alert panel (5m at 5, the faster rows at
    // 3) so the two panels do not disagree on what a spike is; the rows he
    // actually ticks are 1h/2h/4h at 2.5, settled 2026-09-05 after 3.0 was
    // measured to return 0-2 names for whole mornings.
    timeframes: {
      "5m": { on: false, min: 5.0 },
      "15m": { on: false, min: 3.0 },
      "30m": { on: false, min: 3.0 },
      "1h": { on: true, min: 2.5 },
      "2h": { on: true, min: 2.5 },
      "4h": { on: true, min: 2.5 },
      D: { on: false, min: 2.5 },
    },
  },
  sqz: {
    on: true,
    // ANY, to match the TOS scan: SqzFired is one of its ANY rows across
    // 2h/4h/D/Wk, so one timeframe firing counts (2026-09-05, his call).
    //
    // WORTH KNOWING, because it is NOT the same thing TOS is doing. There the
    // row is a positive EVENT - the squeeze fired. Here the group's pass set
    // also holds the do-nothing state ("no squeeze at all"), so ANY reads as
    // "2h OR 4h is not squeezed", which almost everything satisfies. Measured
    // 2026-09-04 13:00 ET on 208 matches: the group passes 125 under ALL and
    // 184 under ANY. He was shown that and chose ANY.
    mode: "any",
    timeframes: { "2h": true, "4h": true, D: false, Wk: false },
    // "fires or empty (no Squeeze)" - his words, 2026-09-04.
    pass: { cyan: true, blank: true, magenta: false, orange: false, white: false },
  },
  skittles: {
    // OFF, and this is the honest spelling of what the board already did.
    //
    // Until 2026-09-05 this group was `on: true` with `role: "rank"`, meaning
    // a TICKED box that filtered nothing - the radio silently overrode the
    // checkbox. He ticked SKITTLES, ticked 2h, and every ticker still showed.
    // Explaining that in a warning box was not a fix; the control was lying.
    //
    // Now `on` means the same thing in all three groups: this group takes part
    // in the filter, combined with the others by the top-level ANY or ALL.
    // Ranking moved to its own switch (`rankToTop`) which works whether or not
    // the group filters - so the original intent, "show me the RVOL/SQZ names
    // with the Skittles ones on top", is still one click away and is what the
    // defaults below do. Default behaviour is UNCHANGED from v2: the filter is
    // RVOL OR SQZ (top level ANY), and Skittles stars and lifts.
    on: false,
    mode: "any",
    // The star, and floating those rows to the top. Independent of `on`.
    rankToTop: true,
    // The four BULLISH paints. This is a bull board, so the bearish four are
    // present in the panel but unticked. Adding this key needs NO version bump:
    // coerceFilters spreads the defaults FIRST, so a saved v3 document that has
    // never heard of `pass` picks it up without losing his other settings.
    pass: {
      cyan: true, green: true, lime: true, dark_green: true,
      magenta: false, red: false, light_red: false, plum: false,
    },
    timeframes: {
      "2h": true, "4h": true, D: true, "2D": true,
      "3D": true, "4D": true, Wk: true, M: true,
    },
  },
});

// NOTE - "Scan matches only" is deliberately NOT a field here. The panel
// renders the board's EXISTING `matchesOnly` state, so the toolbar tick and
// the panel tick are one control with one value. A second copy in this config
// would be a switch that can disagree with the one beside it, which is the
// exact failure this codebase keeps hitting (see the "complete for the
// narrower thing" pattern). It also stays unpersisted, as it already was.

// ---------------------------------------------------------------------------
// cell access
// ---------------------------------------------------------------------------

function cellOf(row, section, timeframe) {
  if (!row || typeof row !== "object") return null;
  const group = row[section];
  if (!group || typeof group !== "object") return null;
  const cell = group[timeframe];
  return cell && typeof cell === "object" ? cell : null;
}

// A BLANK cell is not a reading of zero. `Number(null)` and `Number("")` are
// both 0, and 0 sails straight through Number.isFinite, so a cell the scanner
// shipped empty - a study that has not warmed up, which the board writes as
// `{value: null, bg: null, fg: null}` - used to read as 0.0 and clear any
// threshold of 0. There are 2,662 such RVOL cells in the archived Watchlist
// history, 1,780 of them on 4h, which is TICKED BY DEFAULT. Blank has to come
// back as null so the caller can fail it, exactly as the SQZ group already
// refuses a cell with no background: unknown is not a reading.
//
// (`undefined` was already safe - Number(undefined) is NaN - and a whitespace
// string is only reachable from a text box, but both are spelled out so the
// rule here is one stated line rather than a coincidence of three coercions.)
function numberOf(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

/**
 * The minimum a threshold box should hold after an edit. `<input
 * type="number">` reports "" whenever the box is empty - which it is for the
 * keystroke between clearing 2.5 and typing 1.5 - and `Number("")` is 0, so
 * the old inline handler saved a threshold of 0 the moment the box went blank
 * and every non-negative reading passed. Blank keeps the PREVIOUS minimum, so
 * a half-typed edit changes nothing; a deliberate 0 is a real number and
 * sticks.
 */
export function coerceThreshold(raw, previous) {
  const next = numberOf(raw);
  return next === null ? previous : next;
}

/** True when the RVOL cell's colours say buyers won that bar. */
export function rvolIsBullish(cell) {
  if (!cell) return false;
  if (RVOL_BULLISH_BG_SET.has(cell.bg)) return true;
  if (RVOL_BEARISH_BG_SET.has(cell.bg)) return false;
  if (RVOL_BULLISH_FG_SET.has(cell.fg)) return true;
  if (RVOL_BEARISH_FG_SET.has(cell.fg)) return false;
  // Black on black: |relVol| <= 0.5, side not recorded. See the note above.
  return null;
}

// ---------------------------------------------------------------------------
// groups
// ---------------------------------------------------------------------------

function combine(mode, results) {
  if (results.length === 0) return null; // nothing ticked -> not participating
  return mode === "all" ? results.every(Boolean) : results.some(Boolean);
}

/**
 * RVOL group. A ticked timeframe passes when its reading is at or above that
 * row's own threshold and (when `bullishOnly`) the cell is on the green/cyan
 * side. Returns null when the group is off or has no ticked timeframe, which
 * means "this group does not take part" rather than "this group failed" - an
 * empty ALL-of block would otherwise pass everything vacuously, and under the
 * top-level ALL a switched-off group scored as a failure would empty the
 * board.
 */
export function rvolGroupResult(row, config) {
  const group = (config && config.rvol) || {};
  if (!group.on) return null;
  const frames = group.timeframes || {};
  const results = [];
  for (const timeframe of FILTER_RVOL_TIMEFRAMES) {
    const frame = frames[timeframe];
    if (!frame || !frame.on) continue;
    const cell = cellOf(row, "rvol", timeframe);
    const value = cell ? numberOf(cell.value) : null;
    const threshold = numberOf(frame.min);
    if (value === null || threshold === null) {
      results.push(false);
      continue;
    }
    let ok = value >= threshold;
    if (ok && group.bullishOnly) {
      const bullish = rvolIsBullish(cell);
      // null = the cell cannot say which side won; do not fail it on that.
      // On a BEAR row the same box means "bearish only": sellers won the bar.
      if (isBearRow(row) ? bullish === true : bullish === false) ok = false;
    }
    results.push(ok);
  }
  return combine(group.mode, results);
}

/**
 * SQZ group. A ticked timeframe passes when its cell's background is one of
 * the ticked states. A cell with NO background (study not warmed up) never
 * passes: unknown is not the same as "no squeeze", and treating it as one
 * would quietly wave through every cold symbol after a restart.
 */
export function sqzGroupResult(row, config) {
  const group = (config && config.sqz) || {};
  if (!group.on) return null;
  const allowed = new Set();
  const pass = group.pass || {};
  const bear = isBearRow(row);
  for (const name of Object.keys(SQZ_STATE_BG)) {
    if (pass[name]) allowed.add(SQZ_STATE_BG[mirroredState("sqz", name, bear)]);
  }
  const frames = group.timeframes || {};
  const results = [];
  for (const timeframe of FILTER_SQZ_TIMEFRAMES) {
    if (!frames[timeframe]) continue;
    const cell = cellOf(row, "sqz", timeframe);
    results.push(Boolean(cell && cell.bg && allowed.has(cell.bg)));
  }
  if (allowed.size === 0) return results.length === 0 ? null : false;
  return combine(group.mode, results);
}

/**
 * Skittles group. A ticked timeframe passes when its BACKGROUND is one of the
 * ticked states - four bullish events by default, cyan being only one of them
 * (see SKITTLES_STATE_BG). Every paint is a ONE-candle window: the script's
 * "within 1 bars" means the cross fired on this bar or the one before.
 * Cyan TEXT is a different thing (9 already above 20, common and not an event)
 * and deliberately not read.
 */
export function skittlesGroupResult(row, config) {
  const group = (config && config.skittles) || {};
  if (!group.on) return null;
  const allowed = new Set();
  const pass = group.pass || {};
  const bear = isBearRow(row);
  for (const name of Object.keys(SKITTLES_STATE_BG)) {
    if (pass[name]) allowed.add(SKITTLES_STATE_BG[mirroredState("skittles", name, bear)]);
  }
  const frames = group.timeframes || {};
  const results = [];
  for (const timeframe of FILTER_SKITTLES_TIMEFRAMES) {
    if (!frames[timeframe]) continue;
    const cell = cellOf(row, "skittles", timeframe);
    results.push(Boolean(cell && cell.bg && allowed.has(cell.bg)));
  }
  if (allowed.size === 0) return results.length === 0 ? null : false;
  return combine(group.mode, results);
}

// ---------------------------------------------------------------------------
// the row verdict
// ---------------------------------------------------------------------------

/**
 * True when the Skittles group fires for this row, whatever its role. Drives
 * the star and the push-to-top ordering, so a row can be starred while
 * Skittles is taking no part in the filtering.
 */
export function isStarred(row, config) {
  if (!config || !config.skittles) return false;
  // Scored with the group forced ON: the star marks a Skittles cross whether
  // or not that group is being used to FILTER, which is the whole point of
  // separating the two switches. Ranking off means no star.
  if (!config.skittles.rankToTop) return false;
  const forced = { ...config, skittles: { ...config.skittles, on: true } };
  return skittlesGroupResult(row, forced) === true;
}

/**
 * The top level's setting: "all" only when the config says exactly "all",
 * otherwise "any" - missing, misspelt, "ALL", or no config at all.
 *
 * Deliberately lopsided. ANY is the wider of the two, so a damaged or foreign
 * saved value can only ever WIDEN his board, never silently empty it - an
 * empty board is the failure that looks exactly like a dead scanner.
 */
export function topMode(config) {
  return config && config.mode === "all" ? "all" : "any";
}

/**
 * Does this row survive the filter?
 *
 * The top level decides how the groups combine: ANY (the default) - one
 * passing group is enough; ALL - every participating group must pass (his
 * 2026-09-22 request). Only groups that TAKE PART are combined: a group that
 * is off or has no timeframe ticked is left out entirely in BOTH modes, so
 * under ALL a switched-off SQZ blocks nothing. With no group taking part every
 * row passes - an empty filter must never blank the board, under ALL as much
 * as under ANY.
 */
export function rowPasses(row, config, nowMs = Date.now()) {
  if (!row) return false;
  // A strategy is the WHOLE filter: its rules already include the letter,
  // momentum and RVOL, so the groups and other gates are left out (ANDing
  // them would let his ALL + Skittles tuning silently empty the board).
  if (strategyOf(config)) return strategyPasses(row, config, nowMs);
  if (!gatesPass(row, config)) return false;
  const results = [];
  const rvol = rvolGroupResult(row, config);
  if (rvol !== null) results.push(rvol);
  const sqz = sqzGroupResult(row, config);
  if (sqz !== null) results.push(sqz);
  const skittles = skittlesGroupResult(row, config);
  if (skittles !== null) results.push(skittles);
  if (results.length === 0) return true;
  return topMode(config) === "all" ? results.every(Boolean) : results.some(Boolean);
}

// ---------------------------------------------------------------------------
// scanner-grade gates (v4)
// ---------------------------------------------------------------------------

function gateValue(config, name, allowed) {
  const value = config && config[name];
  // A saved "Best setups" (or GO / OPT / ...) choice reads as Any while the
  // new setups are switched off, so it can never silently empty his list.
  if (name === "setup" && !MOMX_NEW_SETUPS && NEW_SETUP_VALUES.includes(value)) return "any";
  return allowed.includes(value) ? value : "any";
}

/** "v2" | "v3" | "daily2" when the Setup dropdown holds a strategy, else null. */
export function strategyOf(config) {
  const setup = gateValue(config, "setup", FILTER_SETUP_VALUES);
  return FILTER_STRATEGY_VALUES.includes(setup) ? setup : null;
}

/**
 * The worker's verdict (row.strategy, momx/strategy.py). A row without one
 * never passes. Daily 2 passes on the LATCH, so a ticker that qualified at
 * 09:40 stays on the list after its momentum turns.
 */
function strategyPasses(row, config, nowMs = Date.now()) {
  const which = strategyOf(config);
  // The Chart filter stays the confirmed CALL2H / CALL4H only (no C2H / C4H).
  if (which === "chart") return chartArrows(row, nowMs).some((a) => !a.compact);
  if (which === "opt") return earlyOpt(row, nowMs) !== null;
  if (which === "go") return gapAndGo(row, nowMs) !== null;
  if (which === "goRvolMacd") return goRvolMacd(row, nowMs) !== null;
  if (which === "best") return gapAndGo(row, nowMs) !== null || earlyOpt(row, nowMs) !== null;
  if (which === "newsMomo") return newsMomentum(row);
  if (which === "solo") return Boolean(row && row.solo);
  if (which === "hotLead") return Boolean(row && row.hotLeader);
  if (which === "bestHot") return inHotSector(row) && (gapAndGo(row, nowMs) !== null || earlyOpt(row, nowMs) !== null);
  const verdict = row && row.strategy && typeof row.strategy === "object" ? row.strategy : null;
  if (!which || !verdict) return false;
  if (which === "daily2" || which === "g") return Boolean(verdict[which] && typeof verdict[which] === "object");
  return verdict[which] === true;
}

/** The row's number (1, 2, ...) on today's Daily 2 (default) or G list, or null. */
export function daily2Rank(row, which = "daily2") {
  const d = row && row.strategy && row.strategy[which];
  return d && typeof d === "object" && Number.isFinite(d.rank) ? d.rank : null;
}

const money = (value) => (Number.isFinite(value) ? "$" + value.toFixed(2) : "?");
const strikeText = (value) => (Number.isFinite(value) ? String(Number(value.toFixed(2))) : "?");

/**
 * The G trade plan in his own words, for the tag beside "G #n":
 * "→195 · 200C $0.68 · ROI 163%" while open, then the real option result.
 */
export function gPlanText(g) {
  if (!g || typeof g !== "object") return "";
  const plan = "→" + strikeText(g.target) + " · " + strikeText(g.entryStrike) + (g.side === "P" ? "P " : "C ") + money(g.entryPrice) +
    " · ROI " + (Number.isFinite(g.roi) ? Math.round(g.roi) + "%" : "?");
  const ret = Number.isFinite(g.returnPct) ? (g.returnPct > 0 ? "+" : "") + Math.round(g.returnPct) + "%" : "";
  if (g.status === "target") return plan + " · target hit " + ret;
  if (g.status === "stop") return plan + " · stopped -50% (" + ret + ")";
  if (g.status === "close") return plan + " · sold at close " + ret;
  if (Number.isFinite(g.lastPrice) && Number.isFinite(g.entryPrice) && g.entryPrice > 0) {
    const now = Math.round((g.lastPrice / g.entryPrice - 1) * 100);
    return plan + " · now " + money(g.lastPrice) + " (" + (now > 0 ? "+" : "") + now + "%)";
  }
  return plan;
}

/**
 * The small tags the board's A/A+ Setup cell shows ("inside that column",
 * his ask - no new column): V3 when a squeeze fired, else V2; plus "D2 #n"
 * when the ticker is on today's Daily 2 list. [] for anything else.
 */
/**
 * The chart's 5-minute arrow (CloudMax 9x20 cross up = CALL5 / C5), from the
 * scanner's own 5m tape (row.m5.crossUpAt, epoch s). Today only, ET clock.
 * CRWD 2026-09-23: the green arrow before its +4% run was this one, not 2H/4H.
 */
export function fiveMinuteCross(row, nowMs = Date.now()) {
  // A bear row reads the 9x20 cross DOWN (the chart's PUT5 / P5).
  const t = row && row.m5 && Number(isBearRow(row) ? row.m5.crossDownAt : row.m5.crossUpAt);
  if (!Number.isFinite(t) || t <= 0) return null;
  const day = (ms) => new Date(ms).toLocaleDateString("en-US", { timeZone: "America/New_York" });
  if (day(t * 1000) !== day(nowMs)) return null;
  return { clock: etClock(new Date(t * 1000).toISOString()), at: t };
}

/**
 * The chart's 5m ADX pane, read the way the chart paints it (App.jsx
 * calculateMtfAdxLines + fills): white ADX line above the yellow 25 line AND
 * the cyan background (+DI above 25). From row.adx["5m"] (momx columns.adx_cell,
 * Wilder 10 - the chart's defaults). Null when not in that state.
 *
 * SHOWN, NOT A FILTER. Back-test 2026-09-01..22 (2026-09-24): V2 signals
 * already in this state averaged +0.25% per trade, V2 signals NOT yet in it
 * +1.01% - by the time ADX is strong the move has usually run.
 */
export function adxCyan(row) {
  const c = row && row.adx && row.adx["5m"];
  if (!c || typeof c !== "object") return null;
  // A bear row reads -DI (the chart's magenta fill); `plus` then carries the
  // -DI reading and `side` says which one it is.
  const side = isBearRow(row) ? "minus" : "plus";
  const adx = Number(c.adx);
  const di = Number(c[side]);
  if (c.adx === null || c[side] === null || c[side] === undefined || !Number.isFinite(adx) || !Number.isFinite(di)) return null;
  if (!(adx > 25 && di > 25)) return null;
  // FRESH = ADX crossed up through 25 on the latest 5m bar (the previous bar
  // was at or under the yellow line). The tag flashes while fresh - his ask
  // 2026-09-24 - and turns steady once the next bar keeps it above.
  const prev = c.prevAdx === null || c.prevAdx === undefined ? NaN : Number(c.prevAdx);
  const fresh = Number.isFinite(prev) && prev <= 25;
  return { adx: Math.round(adx), plus: Math.round(di), fresh, side };
}

export function strategyTags(row, nowMs = Date.now(), { legacy = true } = {}) {
  const verdict = row && row.strategy && typeof row.strategy === "object" ? row.strategy : null;
  const RULES = strategyRules(isBearRow(row) ? "bear" : "bull");
  // The "5m cross" tag was removed 2026-09-24 09:25 ET at his request ("too
  // confusing"): on the live board it sat on nearly every row, including
  // premarket crosses from 04:10. fiveMinuteCross() stays for the rules.
  const tags = [];
  // Everything from here to the ADX tag is the 2026-09-24/25 set (see
  // MOMX_NEW_SETUPS): the Setup cell reads as it did on 2026-09-24 morning.
  if (MOMX_NEW_SETUPS) newSetupTags(row, nowMs, RULES, tags);
  const marked = markFreshTags(legacyTags(row, nowMs, verdict, RULES, tags, legacy), nowMs);
  // MomoX's ⚡ (2026-09-25 "flash symbol i need it on setup same as MomoX"):
  // an orange bolt at the FRONT of the Setup cell when a setup fired today,
  // flashing while the newest one is under 10 minutes old.
  const setups = marked.filter((t) => ALERT_TAG_KEYS.has(t.key));
  if (!setups.length) return marked;
  const fresh = setups.some((t) => t.fresh);
  return [{
    key: "bolt",
    text: "⚡",
    fresh,
    title: "Fired today: " + setups.map((t) => t.text).join(" · ") + (fresh ? " (new in the last 10 minutes)" : ""),
  }, ...marked];
}

// "Setup column anything fresh make it flashing" (2026-09-25): a timed tag
// flashes for its first FRESH_TAG_MS - measured from when the scanner could
// first SEE it (a chart arrow's seenAt, a GO candle's close), not the candle
// it is drawn on. The ADX tag keeps its own fresh rule (a cross on the last bar).
export const FRESH_TAG_MS = 10 * 60 * 1000;

// MomoX-style alert (2026-09-25 "do the same for MomoX card ... ticker also
// have flash symbol"): the SETUP tags that pop a card and flash the ticker.
// Not the chart arrows / SQZ fires - those fire ~100 times a day.
export const ALERT_TAG_KEYS = new Set(["go", "opt", "solo", "turn", "hotlead", "mxaplus"]);
/** The first fresh setup tag on this row ({key, text, atMs, ...}), or null. */
export function freshAlert(row, nowMs = Date.now()) {
  if (!row) return null;
  return strategyTags(row, nowMs).find((t) => ALERT_TAG_KEYS.has(t.key) && t.fresh) || null;
}
/** Every tag's text for the card's ⚡ line, ADX and the long G plan left out. */
export function alertSignalTexts(row, nowMs = Date.now()) {
  if (!row) return [];
  return strategyTags(row, nowMs).filter((t) => !["adx", "gplan", "bolt"].includes(t.key)).map((t) => t.text);
}
function markFreshTags(tags, nowMs) {
  return tags.map((tag) => {
    const at = Number(tag.atMs);
    if (tag.fresh || !Number.isFinite(at)) return tag;
    const age = nowMs - at;
    return age >= 0 && age < FRESH_TAG_MS ? { ...tag, fresh: true } : tag;
  });
}

function newSetupTags(row, nowMs, RULES, tags) {
  const confirmedGo = goRvolMacd(row, nowMs);
  if (confirmedGo) tags.push({ key: "go-rvol-macd", text: "GO+ " + confirmedGo.clock,
    atMs: confirmedGo.atMs, title: "GO + short RVOL + MACD. Confirmation candle closed "
      + confirmedGo.clock + " ET. " + STRATEGY_RULES.goRvolMacd });
  const opt = earlyOpt(row, nowMs);
  const starRank = row && Number.isFinite(Number(row.bestRank)) ? Number(row.bestRank) : null;
  if (starRank) {
    tags.push({
      key: "rank",
      text: "⭐" + starRank,
      title: "#" + starRank + " of today's best setups by your tested order: GO inside a hot / moving-now sector, " +
        "then other GO, then OPT; cleaner names (no ⚠) and good news first. Ranked by your rules, not a recommendation.",
    });
  }
  if (opt) tags.push({
    key: "opt",
    atMs: opt.at,
    text: "OPT " + opt.clock,
    title: "Options setup (the META / MRNA picture), first seen " + opt.clock + " ET (only OPT before 10:00 counts): " + RULES.opt,
  });
  const go = gapAndGo(row, nowMs);
  if (go) {
    tags.push({
      key: "go",
      atMs: (go.at + 300) * 1000,
      text: "GO " + go.clock,
      title: (go.gap < 0
        ? "Gap down and go: gapped " + go.gap.toFixed(1) + "% and closed below VWAP, the open and the first 5-minute low on the candle that closed "
        : "Gap and go: gapped +" + go.gap.toFixed(1) + "% and cleared VWAP, the open and the first 5-minute high on the candle that closed ")
        + go.clock + " ET. " + RULES.go,
    });
  }
  const lead = row && row.hotLeader && Number.isFinite(Number(row.hotLeader.rank)) ? row.hotLeader : null;
  if (lead) {
    tags.push({
      key: "hotlead",
      atMs: Date.parse(lead.at || ""),
      text: "🔥#" + lead.rank + " " + (lead.sector || ""),
      title: "#" + lead.rank + " stock of today's hot sector (" + (lead.sector || "?") + ") when it lit at " +
        etClock(lead.at) + " ET. " + STRATEGY_RULES.hotLead,
    });
  }
  const soloHit = row && row.solo && Number.isFinite(Number(row.solo.ratio)) ? row.solo : null;
  if (soloHit) {
    tags.push({
      key: "solo",
      atMs: Date.parse(soloHit.at || ""),
      text: "SOLO " + Number(soloHit.ratio).toFixed(1) + "× vol",
      title: "Big money in this stock alone: " + Number(soloHit.ratio).toFixed(1) + "x its normal volume by " +
        etClock(soloHit.at) + " ET, while its sector was not moving. Fast mover - take profit into strength; " +
        "in the back-test about half gave the move back by the close. " + STRATEGY_RULES.solo,
    });
  }
  // MOMOX A+ (momx/momox_aplus.py, 2026-09-25): the competitor's A+ - a
  // catalyst, RVOL cyan, a squeeze fire (2h/4h/D/Wk) and Skittles D..M all
  // bullish, first time today. "W" = the stock has weekly options.
  const mxa = row && row.momoxAPlus && row.momoxAPlus.at ? row.momoxAPlus : null;
  if (mxa) {
    tags.push({
      key: "mxaplus",
      atMs: Date.parse(mxa.at),
      text: "MX A+ " + etClock(mxa.at) + (mxa.weeklies ? " W" : ""),
      title: "MomoX A+ at " + etClock(mxa.at) + " ET: news in the last 24h + RVOL cyan + squeeze fired (" +
        (mxa.sqz || []).join(", ") + ") + Skittles D, 2D, 3D, 4D, W and M all bullish" +
        (mxa.oiStrike ? " + High OI call wall " + mxa.oiStrike + "C (" + Number(mxa.oi || 0).toLocaleString() + " OI) above the price" : "") +
        (mxa.weeklies ? "; has weekly options" : "; no weekly options") +
        ". Back-test Sep 1-25: 39% hit +2% first, 35% hit -1.5% first, 37% ran +4% (held to close did better). Not proven.",
    });
  }
  // MARKET TURN (momx/market_turn.py, 2026-09-25): SPY back above VWAP after
  // the morning dip, and this name was one of the 5 strongest right then.
  const turn = row && row.marketTurn && Number.isFinite(Number(row.marketTurn.rank)) ? row.marketTurn : null;
  if (turn) {
    tags.push({
      key: "turn",
      atMs: Date.parse(turn.at || ""),
      text: "TURN #" + turn.rank + " " + etClock(turn.at),
      title: "Market turned up (SPY back above its VWAP after the morning dip) and this was #" + turn.rank +
        " of the 5 strongest names then: above VWAP, up 1%+, 30m buyers in control (ADX 20+). " +
        "20-day test: 44% hit +2% first, 41% hit -1.5% first, 20% ran +4% - a small edge, not proven.",
    });
  }
  const news = newsCatalyst(row);
  if (news) tags.push(news);
  // ⚠ on a SETUP only (GO / OPT / SOLO) - a warning on a row with no setup
  // is noise. His wrong-pick patterns; the Best setups order sinks 2+ of them.
  if (tags.some((t) => ["go", "opt", "solo", "turn", "mxaplus"].includes(t.key))) {
    const warnings = setupWarnings(row);
    if (warnings.length) {
      tags.push({
        key: "warn",
        text: "⚠ " + warnings.join(" · "),
        title: "Careful: " + warnings.join(", ") + ". These are the patterns behind wrong picks " +
          "(bad news, already up big, momentum fading, price back under VWAP). Two or more push the " +
          "name down the Best setups list.",
      });
    }
  }
}

function legacyTags(row, nowMs, verdict, RULES, tags, legacy) {
  // Only on graded rows: overnight most of the board sits in this state, and
  // a tag on every row is noise that pushes the column wide (2026-09-24).
  const graded = Boolean(row && row.grade && ["A+", "A", "B"].includes(row.grade.letter));
  const adx = graded ? adxCyan(row) : null;
  if (adx) {
    tags.push({
      key: "adx",
      text: "ADX " + adx.adx + (adx.side === "minus" ? " magenta" : " cyan"),
      fresh: adx.fresh,
      title: (adx.fresh ? "JUST CROSSED above 25 on the latest 5m bar. " : "") +
        "5m ADX " + adx.adx + " above 25 with " + (adx.side === "minus" ? "-DI " : "+DI ") + adx.plus + " above 25 - the chart's white line above the yellow " +
        "line on a " + (adx.side === "minus" ? "magenta" : "cyan") + " background. The trend is strong NOW; in the back-test, signals already in this state did worse " +
        "than signals that came before it (the move had usually run).",
    });
  }
  // SQZ fire (his ask 2026-09-25, with C2H / C4H): shown, not proven.
  tags.push(...sqzFires(row, nowMs).map((f) => ({
    key: "sqzfire-" + f.tf,
    atMs: f.at,
    text: "SQZ " + f.tf + " " + f.clock,
    title: "The " + f.tf + " squeeze fired (released) - first seen " + f.clock + " ET today. Not yet back-tested on its own.",
  })));
  tags.push(...chartArrows(row, nowMs).map((a) => ({
    key: "chart-" + a.label + "-" + (a.family === "9x20" ? "9x20" : "4x8"),
    atMs: Date.parse(a.seenAt || a.at || ""),
    text: a.label + " " + a.clock,
    title: "The chart's " + (a.family === "9x20" ? "cyan 9/20" : "yellow 4/8") + " " + a.label + " arrow, on the " + a.clock + " ET candle" +
      (a.seenAt ? " (the scanner saw it at " + etClock(a.seenAt) + " ET)" : "") +
      (a.compact ? ". Short arrow: the " + a.label.slice(-2) + " crossed while the bigger trend has not turned yet - shown only during market hours; not yet back-tested." : ""),
  })));
  // V2 / V3 / Daily 2 / G tags: shown again since 2026-09-25 (his ask); pass
  // { legacy: false } to leave them out.
  if (!verdict || !legacy) return tags;
  if (verdict.v3 === true) tags.push({ key: "v3", text: "V3", title: "On today's Strategy V3 list: " + RULES.v3 });
  else if (verdict.v2 === true) tags.push({ key: "v2", text: "V2", title: "On today's Strategy V2 list: " + RULES.v2 });
  const rank = daily2Rank(row);
  if (rank !== null) {
    tags.push({ key: "daily2", text: "D2 #" + rank, title: "Daily 2 #" + rank + " today: " + RULES.daily2 });
  }
  const gRank = daily2Rank(row, "g");
  if (gRank !== null) {
    const plan = gPlanText(verdict.g);
    tags.push({ key: "g", text: "G #" + gRank, title: "Strategy G #" + gRank + " today: " + plan });
    tags.push({ key: "gplan", text: plan, title: "Target wall, ENTRY " + (verdict.g && verdict.g.side === "P" ? "put" : "call") + " and ROI - then the real option result. " + RULES.g });
  }
  return tags;
}

/**
 * With Daily 2 picked, the list reads #1, #2, #3 top-down whatever column is
 * sorted - the order IS the strategy. Any other setting: input order, untouched.
 */
export function orderByDaily2(rows, config, nowMs = Date.now()) {
  const list = Array.isArray(rows) ? rows.slice() : [];
  const which = strategyOf(config);
  if (which === "chart") {
    // Earliest arrow first - the order they appeared on the chart today.
    const first = (row) => (chartArrows(row).filter((a) => !a.compact)[0] || {}).at || "~";
    return list
      .map((row, index) => ({ row, index, key: String(first(row)) }))
      .sort((a, b) => a.key.localeCompare(b.key) || a.index - b.index)
      .map((entry) => entry.row);
  }
  if (which === "hotLead") {
    return list
      .map((row, index) => ({ row, index, rank: Number(row && row.hotLeader && row.hotLeader.rank) || 9 }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .map((entry) => entry.row);
  }
  if (which === "solo") {
    // Heaviest volume first.
    return list
      .map((row, index) => ({ row, index, ratio: Number(row && row.solo && row.solo.ratio) || 0 }))
      .sort((a, b) => b.ratio - a.ratio || a.index - b.index)
      .map((entry) => entry.row);
  }
  if (which === "best" || which === "bestHot") {
    return rankBestSetups(list, nowMs);
  }
  if (which !== "daily2" && which !== "g") return list;
  return list
    .map((row, index) => ({ row, index, rank: daily2Rank(row, which) }))
    .sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || a.index - b.index)
    .map((entry) => entry.row);
}

/** True when at least one of Setup / Momentum / Pattern is set. */
export function gatesActive(config) {
  return (
    gateValue(config, "setup", FILTER_SETUP_VALUES) !== "any" ||
    gateValue(config, "momentum", FILTER_MOMENTUM_VALUES) !== "any" ||
    gateValue(config, "pattern", FILTER_PATTERN_VALUES) !== "any"
  );
}

/**
 * Setup / Momentum / Pattern, ANDed. A row with no grade or no m5 block never
 * passes an active gate - a missing cell never passes (plan constraint).
 */
export function gatesPass(row, config) {
  const letter = row && row.grade && typeof row.grade === "object" ? row.grade.letter : null;
  const m5 = row && row.m5 && typeof row.m5 === "object" ? row.m5 : null;
  const setup = gateValue(config, "setup", FILTER_SETUP_VALUES);
  if (setup === "aPlus" && letter !== "A+") return false;
  if (setup === "aAndUp" && letter !== "A+" && letter !== "A") return false;
  const momentum = gateValue(config, "momentum", FILTER_MOMENTUM_VALUES);
  if (momentum === "building" && !(m5 && m5.state === "building")) return false;
  const pattern = gateValue(config, "pattern", FILTER_PATTERN_VALUES);
  if (pattern !== "any") {
    const have = m5 ? m5.pattern : null;
    if (pattern === "either") {
      if (have !== "explosive" && have !== "steady") return false;
    } else if (have !== pattern) {
      return false;
    }
  }
  return true;
}

/**
 * Which groups actually take part in the filtering, as a Set of names.
 *
 * This is the answer to the question that cost him a round trip on
 * 2026-09-05: he switched RVOL and SQZ OFF, switched SKITTLES ON, ticked D -
 * and the board showed every ticker. Skittles was set to "push to top", which
 * only re-orders, so NO group was filtering and every row passed. The footer
 * sentence meanwhile read "ANY of: Skittles cyan on any of 1 timeframes",
 * naming a condition that was deciding nothing. A group can be switched on and
 * still not filter, and nothing said so - the exact shape of the
 * truthy-but-meaningless flag this project keeps getting bitten by.
 */
export function filteringGroups(config) {
  const active = new Set();
  if (!config) return active;
  if (strategyOf(config)) return active; // a strategy is the whole filter
  const on = (name) => Boolean(config[name] && config[name].on) && groupHasTickedFrame(config, name);
  if (on("rvol")) active.add("rvol");
  if (on("sqz")) active.add("sqz");
  // Same rule as the other two now: ticked with a ticked timeframe = filters.
  if (on("skittles")) active.add("skittles");
  return active;
}

function groupHasTickedFrame(config, name) {
  const group = (config || {})[name];
  if (!group) return false;
  const frames = group.timeframes || {};
  const keys = name === "rvol"
    ? FILTER_RVOL_TIMEFRAMES
    : name === "sqz" ? FILTER_SQZ_TIMEFRAMES : FILTER_SKITTLES_TIMEFRAMES;
  return keys.some((tf) => (name === "rvol" ? frames[tf] && frames[tf].on : frames[tf]));
}

/**
 * How many of `rows` EACH group would pass on its own, plus how many the whole
 * filter passes (`passing`, which follows the top-level ANY/ALL - the per-group
 * numbers do not). Rendered beside each group header, because "why am I still
 * seeing everything" is only answerable if he can see which group is doing the
 * work - Skittles reading 0 while the board shows 8 is the whole explanation.
 * Counted per group even when that group is not filtering, so the number tells
 * him what switching it on WOULD do.
 */
export function groupPassCounts(rows, config) {
  const list = Array.isArray(rows) ? rows : [];
  const out = { rvol: 0, sqz: 0, skittles: 0, total: list.length, passing: 0 };
  if (!config) return out;
  // Each group is scored AS IF SWITCHED ON. A group that is off would
  // otherwise score 0 - and 0 next to a switched-off group reads as "this
  // would find nothing", which is the opposite of what the number is for.
  const forceOn = (name) => ({ ...config, [name]: { ...config[name], on: true } });
  const asRvol = forceOn("rvol");
  const asSqz = forceOn("sqz");
  const asSkittles = forceOn("skittles");
  for (const row of list) {
    if (rvolGroupResult(row, asRvol) === true) out.rvol += 1;
    if (sqzGroupResult(row, asSqz) === true) out.sqz += 1;
    if (skittlesGroupResult(row, asSkittles) === true) out.skittles += 1;
    if (rowPasses(row, config)) out.passing += 1;
  }
  return out;
}

/** Rows that survive the filter. Input order is untouched. */
export function filterRows(rows, config, nowMs = Date.now()) {
  const list = Array.isArray(rows) ? rows : [];
  if (!config) return list.slice();
  return list.filter((row) => rowPasses(row, config, nowMs));
}

/** How many rows survive - for the count on the FILTERS button. */
export function countPassing(rows, config) {
  return filterRows(rows, config).length;
}

/**
 * Starred rows first, everything else in the order it arrived. Applied AFTER
 * the board's own sort so the trader's column sort still decides the order
 * inside each half. A stable partition, not a re-sort.
 */
export function pushStarredToTop(rows, config) {
  const list = Array.isArray(rows) ? rows : [];
  if (!config || !config.skittles || !config.skittles.rankToTop) return list.slice();
  const starred = [];
  const rest = [];
  for (const row of list) (isStarred(row, config) ? starred : rest).push(row);
  return starred.concat(rest);
}

// ---------------------------------------------------------------------------
// persistence
// ---------------------------------------------------------------------------

export const MOMX_FILTERS_STORAGE_KEY = "momx.scanner.filters";

function mergeFrames(defaults, saved) {
  const out = {};
  for (const [key, value] of Object.entries(defaults)) {
    const from = saved && typeof saved === "object" ? saved[key] : undefined;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      out[key] = { ...value, ...(from && typeof from === "object" ? from : {}) };
    } else {
      out[key] = from === undefined ? value : from;
    }
  }
  return out;
}

/**
 * A stored config merged onto the defaults, so a config written before a new
 * timeframe existed still gets that timeframe (missing key = the default, not
 * undefined). A version bump discards the old document outright.
 *
 * The top-level `mode` is kept only when it is one the dropdown offers. Every
 * document saved before 2026-09-22 has none, and reads as "any" - what it
 * always meant - with the rest of his settings untouched. (Until that date
 * this function DROPPED a saved `mode`, because the top level was fixed ANY.)
 */
export function coerceFilters(saved) {
  const base = DEFAULT_MOMX_FILTERS;
  // v3 -> v4 is a MIGRATION, not a reset: v4 only added the three gates, so a
  // v3 document keeps every setting and the gates start at "any".
  const migratable = saved && typeof saved === "object" && saved.version === 3;
  if (
    !saved ||
    typeof saved !== "object" ||
    (saved.version !== MOMX_FILTER_VERSION && !migratable)
  ) {
    return structuredCloneish(base);
  }
  return {
    version: MOMX_FILTER_VERSION,
    mode: FILTER_TOP_MODES.includes(saved.mode) ? saved.mode : base.mode,
    setup: gateValue(saved, "setup", FILTER_SETUP_VALUES),
    momentum: gateValue(saved, "momentum", FILTER_MOMENTUM_VALUES),
    pattern: gateValue(saved, "pattern", FILTER_PATTERN_VALUES),
    rvol: {
      ...base.rvol,
      ...(saved.rvol || {}),
      timeframes: mergeFrames(base.rvol.timeframes, (saved.rvol || {}).timeframes),
    },
    sqz: {
      ...base.sqz,
      ...(saved.sqz || {}),
      timeframes: mergeFrames(base.sqz.timeframes, (saved.sqz || {}).timeframes),
      pass: mergeFrames(base.sqz.pass, (saved.sqz || {}).pass),
    },
    skittles: {
      ...base.skittles,
      ...(saved.skittles || {}),
      timeframes: mergeFrames(base.skittles.timeframes, (saved.skittles || {}).timeframes),
      pass: mergeFrames(base.skittles.pass, (saved.skittles || {}).pass),
      // `role` was the v2 spelling and must not survive into a v3 document -
      // nothing reads it now, and a stray copy would mislead the next reader.
      role: undefined,
    },
  };
}

function structuredCloneish(value) {
  return JSON.parse(JSON.stringify(value));
}

export function readStoredFilters() {
  try {
    const raw = window.localStorage.getItem(MOMX_FILTERS_STORAGE_KEY);
    return coerceFilters(raw ? JSON.parse(raw) : null);
  } catch {
    return coerceFilters(null);
  }
}

export function writeStoredFilters(config) {
  try {
    window.localStorage.setItem(MOMX_FILTERS_STORAGE_KEY, JSON.stringify(config));
  } catch {
    /* private mode / quota - the filter still works for this session */
  }
}

/**
 * A human sentence for the button's tooltip and the panel footer.
 *
 * IT MUST ONLY NAME CONDITIONS THAT ACTUALLY DECIDE SOMETHING. The first cut
 * listed every switched-on group, so with Skittles set to "push to top" and
 * nothing else on it read "ANY of: Skittles cyan on any of 1 timeframes" while
 * the filter was passing every row - a sentence describing a rule that was not
 * being applied. Ranking is now stated separately, in the words "pushed to
 * top", and a filter that filters nothing says exactly that.
 */
export function describeFilters(config, direction = "bull") {
  if (!config) return "";
  const bear = direction === "bear";
  const which = strategyOf(config);
  if (which) return "Strategy " + STRATEGY_LABELS[which] + ": " + strategyRules(direction)[which];
  const active = filteringGroups(config);
  const parts = [];
  const rvol = config.rvol || {};
  if (active.has("rvol")) {
    const frames = FILTER_RVOL_TIMEFRAMES.filter((tf) => (rvol.timeframes || {})[tf]?.on);
    if (frames.length) {
      const mins = frames.map((tf) => rvol.timeframes[tf].min);
      const same = mins.every((m) => m === mins[0]);
      parts.push(
        "RVOL " + (rvol.mode === "all" ? "all" : "any") + " of " + frames.join("/") +
          (same ? " ≥ " + mins[0] : ""),
      );
    }
  }
  const sqz = config.sqz || {};
  if (active.has("sqz")) {
    const frames = FILTER_SQZ_TIMEFRAMES.filter((tf) => (sqz.timeframes || {})[tf]);
    const states = Object.keys(SQZ_STATE_BG).filter((name) => (sqz.pass || {})[name])
      .map((name) => mirroredState("sqz", name, bear));
    if (!states.length) {
      // Named, not skipped. With no state ticked the group passes NOBODY, so
      // under ALL it empties the board - and a sentence that left it out
      // (with SQZ the only group it read "NOTHING IS FILTERING - every ticker
      // passes") would hide the one condition doing it. Skittles' own phrase
      // already says "(no colour ticked)"; this is the same courtesy.
      parts.push("SQZ (no state ticked)");
    } else if (frames.length) {
      parts.push("SQZ " + (sqz.mode === "all" ? "all" : "any") + " of " + frames.join("/") +
        " is " + states.join(" or "));
    }
  }
  const skittles = config.skittles || {};
  const skittlesFrames = FILTER_SKITTLES_TIMEFRAMES
    .filter((tf) => (skittles.timeframes || {})[tf]);
  // Name the PAINTS, not just "cyan" - the group covers four bullish events
  // and calling all of them "cyan" is how the MACD gap went unnoticed.
  const skittlesStates = Object.keys(SKITTLES_STATE_BG)
    .filter((name) => (skittles.pass || {})[name])
    .map((name) => mirroredState("skittles", name, bear));
  const skittlesWhat = skittlesStates.length === 0
    ? "Skittles (no colour ticked)"
    : skittlesStates.length === 4
        && skittlesStates.every((n) => (bear ? ["magenta", "red", "light_red", "plum"] : ["cyan", "green", "lime", "dark_green"]).includes(n))
      ? (bear ? "a bearish Skittles cross" : "a bullish Skittles cross")
      : "Skittles " + skittlesStates.join("/");
  // "any of D" reads like a mistake; with one timeframe there is no "any".
  const skittlesPhrase = skittlesFrames.length === 1
    ? skittlesWhat + " on " + skittlesFrames[0]
    : skittlesWhat + " on any of " + skittlesFrames.join("/");
  const starring = Boolean(skittles.rankToTop) && skittlesFrames.length > 0;
  if (active.has("skittles") && skittlesFrames.length) {
    // Filtering AND starring is one clause, not two: repeating the same
    // phrase twice ("... on D. ... on D pushed to top") reads like a bug.
    parts.push(skittlesPhrase + (starring ? " (starred)" : ""));
  }

  // Ranking is NOT a condition and is never folded into the ANY/ALL list.
  // Stated on its own only when Skittles is NOT one of the conditions -
  // otherwise the clause above already carries it.
  const ranking = starring && !active.has("skittles")
    ? skittlesPhrase + " pushed to top"
    : "";

  // The gates are ANDed, so they are stated as their own "Only ..." clause.
  const gates = gateWords(config);
  const gateClause = gates.length ? "Only " + gates.join(" AND ") : "";

  if (!parts.length) {
    if (gateClause) {
      return ranking ? gateClause + ". " + ranking[0].toUpperCase() + ranking.slice(1) : gateClause;
    }
    const nothing = "NOTHING IS FILTERING - every ticker passes";
    return ranking ? nothing + "; " + ranking : nothing;
  }
  // The joining word is the top level's, spelled the way he reads a TOS scan:
  // ALL of: A AND B, ANY of: A OR B. The gates lead, because they are ANDed
  // ahead of the groups in both modes.
  const groupSentence = topMode(config) === "all"
    ? "ALL of: " + parts.join(" AND ")
    : "ANY of: " + parts.join(" OR ");
  const sentence = (gateClause ? gateClause + ", and " : "") + groupSentence;
  // The ranking clause follows a full stop, so it starts a sentence and must
  // not read "... is cyan or blank. a bullish Skittles cross ...".
  const led = ranking ? ranking[0].toUpperCase() + ranking.slice(1) : "";
  return led ? sentence + ". " + led : sentence;
}

// The active scanner-grade gates in words, in panel order. ONE spelling for
// the footer sentence and the "In the filter" line, so the two cannot drift.
function gateWords(config) {
  const words = [];
  const setup = gateValue(config, "setup", FILTER_SETUP_VALUES);
  if (setup === "aPlus") words.push("Setup A+");
  if (setup === "aAndUp") words.push("Setup A or A+");
  if (STRATEGY_LABELS[setup]) words.push("Strategy " + STRATEGY_LABELS[setup]);
  if (gateValue(config, "momentum", FILTER_MOMENTUM_VALUES) === "building") {
    words.push("Momentum Building");
  }
  const pattern = gateValue(config, "pattern", FILTER_PATTERN_VALUES);
  if (pattern === "explosive") words.push("Explosive pattern");
  if (pattern === "steady") words.push("Steady pattern");
  if (pattern === "either") words.push("Explosive or Steady pattern");
  return words;
}

// Panel names, in the order the panel stacks the groups.
const GROUP_LABELS = [["rvol", "RVOL"], ["sqz", "SQZ"], ["skittles", "SKITTLES"]];

// "A", "A and B", "A, B and C".
function joinNames(names) {
  if (names.length < 2) return names.join("");
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

/**
 * Why the filter leaves 0 tickers, in one sentence - or null when there is
 * nothing to explain. `counts` is what groupPassCounts() returns.
 *
 * Under ALL a board at 0 is the EXPECTED outcome of one quiet group, and "0 of
 * 358 tickers" alone reads like a broken scanner. The per-group numbers
 * already hold the answer (the group reading 0), so this says it in words
 * instead of leaving him to work it out from the badges.
 *
 * Null when: no counts (the panel is closed), an empty board (0 of 0 is not
 * the filter's doing), something passes, or no group is filtering - that last
 * case passes everything and has its own loud warning.
 */
export function whyNothingPasses(config, counts) {
  if (!config || !counts) return null;
  if (!(counts.total > 0) || counts.passing > 0) return null;
  const which = strategyOf(config);
  if (which === "daily2") {
    return "No ticker has qualified for Daily 2 today yet - the first can qualify from 9:35 AM ET.";
  }
  if (which === "g") {
    return "No ticker has qualified for Strategy G today yet - it checks all day from 9:30 AM to 3:30 PM ET.";
  }
  if (which === "chart") {
    return "No graded or scan-matched ticker has a live CALL2H (last 2 hours) or CALL4H (last 4 hours) arrow right now.";
  }
  if (which === "newsMomo") {
    return "No ticker has positive news AND building momentum AND an A+/A letter right now. The AI reads news from 06:30 ET.";
  }
  if (which === "hotLead") {
    return "No hot sector yet today - its two leading stocks are picked when a sector lights up between 09:45 and 11:00 ET.";
  }
  if (which === "solo") {
    return "No SOLO big-money stock yet today. SOLO is checked 09:45-11:00 ET (volume 5-20x normal, up and above VWAP, sector flat).";
  }
  if (which === "bestHot") {
    return "No GO or OPT ticker is in a hot sector right now. Hot sectors are marked from 09:45 ET; GO and OPT mostly appear 09:30-10:30.";
  }
  if (which === "best") {
    return "No ticker has GO or OPT right now. Both mostly appear between 09:30 and 10:30.";
  }
  if (which === "go") {
    return "No A+/A ticker has a gap and go today (gap 2%+, above VWAP, the open and the first 5-minute high by 10:30, 30m buyers).";
  }
  if (which === "goRvolMacd") {
    return "No bull A/A+ ticker currently passes GO + cyan 5m/15m/30m RVOL + rising 5m MACD with completed 30m buyers. Requires fresh completed candles, 09:30-15:30 ET; unavailable on BEAR.";
  }
  if (which === "opt") {
    return "No ticker shows the Options setup right now (A+/A + 2h and 4h Skittles cross + 30m buyers + Extended).";
  }
  if (which) {
    return "No ticker has qualified for Strategy " + STRATEGY_LABELS[which] +
      " today yet - the first can qualify from 9:35 AM ET.";
  }
  const active = filteringGroups(config);
  // The scanner-grade gates filter too (ANDed ahead of the groups), and the
  // per-group counts do not see them - so with a gate on, a group sentence
  // alone could blame the wrong thing.
  const gates = gatesActive(config);
  const gateNote = "the Setup / Momentum / Pattern filters";
  if (active.size === 0) {
    return gates ? "No ticker passes " + gateNote + " right now." : null;
  }
  const filtering = GROUP_LABELS.filter(([name]) => active.has(name));
  if (topMode(config) === "all") {
    const zero = filtering.filter(([name]) => counts[name] === 0).map(([, label]) => label);
    if (zero.length) {
      return joinNames(zero) + (zero.length > 1 ? " pass" : " passes") +
        " no ticker right now - and ALL needs every switched-on group to pass.";
    }
    if (gates) {
      return "No ticker passes " + joinNames(filtering.map(([, label]) => label)) +
        " together with " + gateNote + " right now.";
    }
    return "Each switched-on group passes some tickers, but no ticker passes " +
      joinNames(filtering.map(([, label]) => label)) + " at the same time right now.";
  }
  if (gates && filtering.some(([name]) => counts[name] > 0)) {
    return "No ticker that passes " + joinNames(filtering.map(([, label]) => label)) +
      " also passes " + gateNote + " right now.";
  }
  return "None of the switched-on groups (" +
    joinNames(filtering.map(([, label]) => label)) + ") passes a ticker right now.";
}

/**
 * Which groups the filter is really using, as panel labels: `inFilter` (the
 * ones filteringGroups() filters on), `off` (the group's own box unticked) and
 * `noTimeframe` (box ticked, no timeframe ticked) - every group in exactly one
 * of the three - plus the active scanner-grade gates.
 *
 * Why it exists (2026-09-22, his phone at 07:26 CT): ALL on top, RVOL's own
 * box unticked but 1h/2h/4h still ticked inside it. The ticked timeframes made
 * RVOL look ON, the board showed 15 tickers with no RVOL, and he reported ALL
 * as wrong. ALL was right - only SKITTLES was in the filter - but nothing on
 * screen said so outside the footer sentence.
 */
export function filterMembership(config) {
  const out = { inFilter: [], off: [], noTimeframe: [], gates: [] };
  if (!config) return out;
  const active = filteringGroups(config);
  for (const [name, label] of GROUP_LABELS) {
    const group = config[name] || {};
    if (active.has(name)) out.inFilter.push(label);
    else if (!group.on) out.off.push(label);
    else out.noTimeframe.push(label);
  }
  out.gates = gateWords(config);
  return out;
}

/**
 * The line under the top ANY/ALL, e.g. "In the filter: SKITTLES. Left out
 * (off): RVOL and SQZ." Null when nothing is in the filter: that state has its
 * own loud "Nothing is filtering" warning, and two lines saying it would be
 * noise.
 */
export function describeMembership(config) {
  const which = strategyOf(config);
  if (which === "goRvolMacd") return "In the filter: GO + short RVOL + MACD with its built-in RVOL and MACD checks. The separate RVOL, SQZ, SKITTLES, Momentum and Pattern controls below do not change this strategy.";
  if (which) {
    return "In the filter: Strategy " + STRATEGY_LABELS[which] + " only. Left out while a strategy is picked: " +
      "RVOL, SQZ, SKITTLES, Momentum and Pattern.";
  }
  const { inFilter, off, noTimeframe, gates } = filterMembership(config);
  const using = [...inFilter, ...gates];
  if (!using.length) return null;
  let text = "In the filter: " + joinNames(using) + ".";
  if (off.length) text += " Left out (off): " + joinNames(off) + ".";
  if (noTimeframe.length) text += " Left out (no timeframe ticked): " + joinNames(noTimeframe) + ".";
  return text;
}
