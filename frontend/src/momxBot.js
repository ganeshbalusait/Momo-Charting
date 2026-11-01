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
  level1: "Level 1 · 3+ Setup types",
  level2: "Level 2 · 3+ Setup types",
  level3: "Level 3 · 3+ Setup types",
  goRvolMacd: "GO+ (GO + short RVOL + MACD)",
  jev: "Jev pick",
  news: "News ▲ (scan match + Jev bullish news)",
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
  dayLineEarly: "Daily line before 10:00 (above yday high)",
  dayReclaim: "Gap down, took back yday low",
  dayLine100: "Daily line $100 (calls)",
  mom: "MOM↑ (momentum line cyan)",
  momStrong: "MOM↑ strong (momentum + ADX cyan 5m/30m)",
  momFlip: "FLIP↑ (5m ADX flips cyan, momentum rising)",
  explosive: "💥 Explosive (volume burst)",
  steady: "📈 Steady (higher highs)",
});
// Plain-language meaning of each rule (his ask 2026-09-28: "for each rule,
// give me explanation ... so i know what is missing"). "Needs" = every
// condition; the back-test line is Sep 1-25 on stock prices.
export const BOT_RULE_HELP = Object.freeze({
  level1: { what: "Rising-RVOL virtual strategy; MOM optional.", needs: "At least 3 cyan/green RVOL timeframes from 5m through Daily strictly increasing versus their prior valid publication, plus at least 3 of the 23 distinct Setup types. Existing call eligibility and 09:30-15:30 ET entry window apply.", tested: "Experimental virtual strategy. No calibrated win probability; no historical results backfilled. AI agents review completed trades afterward." },
  level2: { what: "Level 1 with SQZ confirmation; MOM optional.", needs: "Level 1 plus at least one cyan SQZ cell OR all SQZ cells blank, AND an active Setup SQZ-release tag. At least 3 Setup types total.", tested: "Experimental virtual strategy, recorded separately from Level 1. Levels overlap; not independent executions." },
  level3: { what: "Level 2 with Skittles confirmation; MOM optional.", needs: "Level 2 plus at least one cyan/green Skittles background box from 2h through Monthly. At least 3 Setup types total.", tested: "Experimental virtual strategy, recorded separately from Levels 1/2. Higher confirmation has not been proven more profitable." },
  news: { what: "News ▲: the scanner's NEWS ▲ list (BEAR: NEWS ▼).", needs: "The ticker is a scan match AND the headline in its News cell is one TypeSafe Jev reads as bullish (BEAR board: bearish) - the same tickers the scanner shows under NEWS ▲ with Scan matches only ticked. No ETFs. 09:30-15:30.", tested: "TEST - starts 2026-10-08. Back-look Oct 6-7 on stock prices: bullish-news stocks moved about the same as other news." },
  jev: { what: "Jev pick: TypeSafe Jev scored this scanner ticker as a TAKE.", needs: "The ticker is on the scanner (a match); Jev read its whole scanner picture - RVOL, Skittles, squeeze, momentum, VWAP, sector, SPY, news - and gave a take chance of 30% or more in the last 15 minutes. Re-scored when the picture changes (at most every 10 minutes). 09:30-15:30.", tested: "TEST - starts 2026-10-05; not back-tested (scanner scores did not exist before)." },
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
  dayLineEarly: { what: "The Daily line trades whose signal came before 10:00 AM ET (09:35-09:59) - a view, not extra trades.", needs: "Everything Daily line needs, signal before 10:00. These are the same trades as Daily line; the totals count them once.", tested: "Watched from 2026-10-08: Sep 28 - Oct 8, puts 9:30-10 won 70% (44 trades, avg +44%) - found by searching many slices, so it must hold on new days before it means anything." },
  dayLine: { what: "Your daily-line trade: the stock opens past yesterday's high (calls) or low (puts) and the 5m candle closes on that side of the 21 EMA with rising volume or momentum.", needs: "One of the board's 30 biggest stocks (by dollar volume), or SPY / QQQ; 09:35-11:00; open above yesterday's high (puts: below yesterday's low); 5m close above the 21 EMA (puts: below); volume up (more than the last bar and 1.5x the last 30 min) OR MACD rising above its signal (puts: falling below).", tested: "TEST - back-test Sep 1-28 (biggest stocks): calls +0.63-0.75% a trade vs +0.13-0.30% random (few trades, not proven); puts no better than random." },
  dayReclaim: { what: "The reverse (ZS 09-28): opened past yesterday's line the wrong way, and the first 5m candle closed straight back across it.", needs: "Calls: open below yesterday's low, the 09:30 candle closes back above it. Puts: open above yesterday's high, the 09:30 candle closes back below it. Any stock, plus SPY / QQQ (no other ETFs).", tested: "TEST - back-test Sep 1-28 flips: calls -0.11% vs -0.14% random (Sep 1-16), +0.55% vs +0.17% (Sep 17-28)." },
  dayLine100: { what: "Your daily line with your filters: $100+ stock, opened past yesterday's high (calls) or low (puts), $0.50 of room to the next daily line.", needs: "Open $100 or more; open above yesterday's high (puts: below yesterday's low); no daily line (the last 3 days' open / high / low / close) within $0.50 of the price in the trade's direction; 5m close above the 21 EMA (puts: below) with volume rising or MACD momentum; 09:35-11:00. Any stock, plus SPY / QQQ.", tested: "TEST - back-test Sep 1-28: CALLS held to the close +0.36% / +0.58% a trade vs +0.10% / +0.25% for random $100+ stocks - beat random in both halves (one up month). Your 21 EMA exit made about half that. PUTS: no edge." },
  mom: { what: "MOM↑: the chart's Squeeze Momentum line is cyan - buying getting stronger (bear MOM↓: magenta, selling getting stronger).", needs: "On the last 5-minute candle the Squeeze Momentum line (shared_SqueezeChain24, length 20) is above 0 and rising (bear: below 0 and falling). Same as the MOM tag in the Setup column. 09:30-15:30, first time per stock per day.", tested: "TEST - this plain version is not back-tested. With ADX above 20 and RVOL added, this week did the same as random stocks (84 trades, 29% win)." },
  momStrong: { what: "MOM strong: the strong call setup - momentum dots cyan and rising AND the ADX background filled cyan on 5m and 30m (bear: magenta / magenta).", needs: "On the last 5-minute candle the Squeeze Momentum line (shared_SqueezeChain24) is cyan - above 0 and rising (bear: magenta, below 0 and falling); AND on BOTH 5m and 30m ADX DI+ is above 25 and above DI- (bear: DI- above 25 and above DI+) - buyers pushing harder and the trend strong across timeframes. Same as the solid cyan MOM tag in the Setup column. 09:30-15:30, first time per stock per day.", tested: "TEST - starts 2026-10-05; not back-tested. Momentum + 5m ADX alone did the same as random stocks (Sep 1-28); the 30m agreement is new." },
  explosive: { what: "💥 Explosive: a sudden burst of buying on the 5-minute chart - the pattern beside the grade letter.", needs: "One of the last 6 five-minute candles traded 3 or more times its usual volume swing (RVOL z-score 3.0+) and closed in its upper half, while the 5m breakout is confirmed or holding. Puts: the same burst closing in the lower half while the breakdown holds. 09:30-15:30, first time per stock per day.", tested: "TEST - starts 2026-10-12. Never back-tested on its own." },
  steady: { what: "📈 Steady: a slow, clean climb - the pattern beside the grade letter.", needs: "At least 4 of the last 6 fifteen-minute candles made a higher high AND a higher low than the one before, and the price is above the 5m 20 EMA. Puts: lower highs and lower lows, below the 20 EMA. 09:30-15:30, first time per stock per day.", tested: "TEST - starts 2026-10-12. Never back-tested on its own." },
  momFlip: { what: "MOM FLIP: the 5m ADX background flips to your side while the momentum dots turn your way - earlier than MOM.", needs: "On the last CLOSED 5-minute candle the ADX buyers' line (DI+) crosses above the sellers' line (DI-) - background magenta -> cyan - and the Squeeze Momentum dots are rising on that candle (cyan or dark blue). Puts: DI- crosses above DI+ (cyan -> magenta) and the dots are falling (purple or magenta). Does not wait for the dots to cross 0. 09:30-15:30, first time per stock per day.", tested: "TEST - starts 2026-10-05. Back-test Sep 1-28 (all optionable stocks, 5m): the flip alone, with the dots turning, or with the dots coloured did the SAME as random stocks at the same moment, calls and puts, in both halves of the month. Fires on ~350 stocks a day." },
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
  dayLineEarly: "Daily line before 10:00 (below yday low)",
  dayReclaim: "Gap up, lost yday high",
  dayLine100: "Daily line $100 (puts)",
  mom: "MOM↓ (momentum line magenta)",
  momStrong: "MOM↓ strong (momentum + ADX magenta 5m/30m)",
  momFlip: "FLIP↓ (5m ADX flips magenta, momentum falling)",
  explosive: "💥 Explosive (selling burst)",
  steady: "📈 Steady (lower lows)",
  news: "News ▼ (scan match + Jev bearish news)",
});
export function botRuleLabel(rule, side = "bull") {
  const map = side === "bear" ? BOT_RULE_LABELS_BEAR : BOT_RULE_LABELS;
  return map[rule] || rule;
}
export const BOT_TEST_RULES = new Set(["level1", "level2", "level3", "news", "jev", "c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath", "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom", "momStrong", "momFlip", "explosive", "steady"]);
export const BOT_RULE_ORDER = ["level1", "level2", "level3", "news", "jev", "goRvolMacd", "go", "star1", "mxaplus", "daily2", "turn", "c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath", "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom", "momStrong", "momFlip", "explosive", "steady"];

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
// Fills (2026-10-09, his "do it" on realistic fills): "mid" = the bot's mid prices
// (as before); "real" = bought at the ASK, sold at the BID - what a market order
// actually pays on a short-dated option. Bid / ask are recorded from 2026-10-09;
// a trade without them has no real P/L and is left out of the "real" numbers.
export let BOT_FILLS = "mid";
export function setBotFills(v) {
  BOT_FILLS = v === "real" || v === "pro" ? v : "mid";
}
// "Pro" fill (his ask 2026-10-09): a limit order worked from the mid - bought at
// mid + 1/4 of the spread, sold at mid - 1/4 of the spread. Between Mid (best case)
// and Real (market orders both ways).
const PRO_SPREAD_SHARE = 0.25;
function proFill(bid, ask, buying) {
  if (bid === null || ask === null || ask <= 0 || bid < 0 || ask < bid) return null;
  const mid = (bid + ask) / 2;
  const px = buying ? mid + PRO_SPREAD_SHARE * (ask - bid) : mid - PRO_SPREAD_SHARE * (ask - bid);
  return Math.round(px * 10000) / 10000;
}

// Exit and contract switches (his "Yes" 2026-10-10, after MRNA 10-09: the bot's
// VWAP exit on next week's contract made +33%, the same-day contract held to the
// close made +934%). The headline result of every trade - win / loss, P/L, ROI -
// is the chosen CONTRACT sold the chosen WAY:
//   exit:  "exit"  = his exit rule (VWAP / stop, as before)
//          "close" = held to the 15:50 close
//          "best"  = sold at the day's best price (hindsight - the ceiling)
//   contract: "main" = next week's contract (as before), "zero" = same-day (0DTE),
//             "entry" = the 0.20-delta strike of the main expiry.
export const BOT_EXITS = ["exit", "close", "best"];
export const BOT_CONTRACTS = ["main", "zero", "entry"];
export let BOT_EXIT = "exit";
export let BOT_CONTRACT = "main";
export function setBotExit(v) {
  BOT_EXIT = BOT_EXITS.includes(v) ? v : "exit";
}
export function setBotContract(v) {
  BOT_CONTRACT = BOT_CONTRACTS.includes(v) ? v : "main";
}

// Today's ET date, worked out at most once a minute (the matrix asks for it per trade).
const ET_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });
let etDayCache = { minute: -1, day: "" };
function etToday() {
  const minute = Math.floor(Date.now() / 60000);
  if (etDayCache.minute !== minute) etDayCache = { minute, day: ET_DAY.format(new Date()) };
  return etDayCache.day;
}
function isTodayTrade(t) {
  return String((t && t.at) || "").slice(0, 10) === etToday();
}

const LEG_PREFIX = { main: "", zero: "z", entry: "e" };
function legField(t, contract, name) {
  const pre = LEG_PREFIX[contract] ?? "";
  if (!pre) return num(t && t[name]);
  // main's exit price is "exitPrice"; the side legs use "zExit" / "eExit".
  const key = pre + (name === "exitPrice" ? "Exit" : name.charAt(0).toUpperCase() + name.slice(1));
  return num(t && t[key]);
}

function recordedMid(t, contract, price, bid, ask) {
  const b = legField(t, contract, bid), a = legField(t, contract, ask);
  if (b !== null && a !== null) return b >= 0 && a > 0 && a >= b ? Math.round((b + a) * 5000) / 10000 : null;
  // Older archives predate bid/ask capture. Keep their recorded value; never
  // manufacture a missing exit from a stale last quote.
  return legField(t, contract, price);
}

/** One contract sold one way, at one fill. {pct, dollars, cost} - all null when
 * the trade has no price for it (no guessing: a missing bid / ask is no result). */
export function botLegResult(t, contract = BOT_CONTRACT, exit = BOT_EXIT, fills = BOT_FILLS) {
  const none = { pct: null, dollars: null, cost: null };
  if (!t || t.badQuote) return none;
  const f = (name) => legField(t, contract, name);
  const entry = fills === "mid" ? recordedMid(t, contract, "entry", "entryBid", "entryAsk") : f("entry");
  if (entry === null || entry <= 0) return none;
  const exited = f("exitPrice") !== null;
  let price;
  let bid = null;
  let ask = null;
  if (exit === "best") {
    // The day's best has no bid / ask behind it: a Mid-only number. The DAY's best
    // for all three (main's multi-day "holdBest" would not compare like for like).
    // Before 2026-10-12 the side contracts stopped being followed at the exit, so a
    // finished day without their recorded close has no true day's best.
    if (fills !== "mid") return none;
    if (contract !== "main" && f("close") === null && !isTodayTrade(t)) return none;
    // Entry itself is an observed midpoint and belongs to the day's path.
    // Legacy best marks can predate corrected bid/ask entry capture.
    const best = f("best");
    price = best === null ? null : Math.max(entry, best);
  } else if (exit === "close") {
    const close = f("close");
    // No recorded close on a finished day = unknown (the old trackers froze the
    // side contracts at the exit, so their "last" is NOT the close). Only today's
    // still-open trades fall back to the live price.
    if (close === null && !isTodayTrade(t)) return none;
    price = close ?? f("last");
    bid = close !== null ? f("closeBid") : f("lastBid");
    ask = close !== null ? f("closeAsk") : f("lastAsk");
  } else {
    if (!exited && (t.status === "closed" || t.exitPrice != null || t.exitAt)) return none;
    price = exited ? f("exitPrice") : f("last");
    bid = exited ? f("exitBid") : f("lastBid");
    ask = exited ? f("exitAsk") : f("lastAsk");
  }
  if (fills === "mid" && exit !== "best" && bid !== null && ask !== null) {
    price = bid >= 0 && ask > 0 && ask >= bid ? Math.round((bid + ask) * 5000) / 10000 : null;
  }
  if (fills === "real" || fills === "pro") {
    const inBid = f("entryBid");
    const inAsk = f("entryAsk");
    const buy = fills === "real" ? (inAsk !== null && inAsk > 0 ? inAsk : null) : proFill(inBid, inAsk, true);
    const sell = fills === "real" ? (bid !== null && bid >= 0 ? bid : null) : proFill(bid, ask, false);
    if (buy === null || buy <= 0 || sell === null) return none;
    return { pct: pct(buy, sell), dollars: (sell - buy) * 100, cost: buy, sell };
  }
  if (price === null) return none;
  return { pct: pct(entry, price), dollars: (price - entry) * 100, cost: entry, sell: price };
}

export function botTradeResults(t) {
  // "Bad quote" (2026-10-02): the entry came from an old disk copy of the option
  // chain and was below what the option was already worth - not a real fill.
  // Counted nowhere: no win, no loss, no P/L, no ROI.
  if (t && t.badQuote) return { exit: null, exitDollars: null, zero: null, entryStrike: null, max: null, close: null };
  const entry = num(t && t.entry);
  const chosen = botLegResult(t);
  return {
    closed: legClosed(t, BOT_CONTRACT, BOT_EXIT),
    sell: chosen.sell ?? null,
    cost: chosen.cost ?? recordedMid(t, BOT_CONTRACT, "entry", "entryBid", "entryAsk"),
    exit: chosen.pct,
    exitDollars: chosen.dollars,
    zero: botLegResult(t, "zero", "exit").pct,
    // The chain's ENTRY strike (delta just under 0.20), same expiry and exit.
    entryStrike: botLegResult(t, "entry", "exit").pct,
    max: pct(entry, num(t && t.holdBest) ?? num(t && t.best)),
    close: pct(entry, num(t && t.close) ?? (isTodayTrade(t) ? num(t && t.last) : null)),
  };
}

function legClosed(t, contract, exit) {
  if (exit === "best") return true; // hypothetical result, labelled as hindsight
  if (exit === "close") return legField(t, contract, "close") !== null;
  return legField(t, contract, "exitPrice") !== null || t?.status === "closed" || Boolean(t?.exitAt);
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
  const priced = rows.filter((r) => r.results.exit !== null && (BOT_FILLS !== "real" || r.results.closed));
  const completed = priced.filter((r) => r.results.closed);
  const openPriced = priced.filter((r) => !r.results.closed);
  const wins = completed.filter((r) => r.results.exit > 0).length;
  return {
    trades: rows.length,
    priced: priced.length,
    wins,
    breakeven: completed.filter((r) => r.results.exit === 0).length,
    completed: completed.length,
    openPriced: openPriced.length,
    realizedDollars: completed.reduce((sum, r) => sum + r.results.exitDollars, 0),
    openDollars: openPriced.reduce((sum, r) => sum + r.results.exitDollars, 0),
    openWins: openPriced.filter((r) => r.results.exit > 0).length,
    openLosses: openPriced.filter((r) => r.results.exit < 0).length,
    open: rows.filter((r) => r.results.closed === false).length,
    unpriced: rows.filter((r) => r.results.exit === null).length,
    losses: completed.filter((r) => r.results.exit < 0).length,
    winRate: completed.length ? (wins / completed.length) * 100 : null,
    avgExit: avg(priced.map((r) => r.results.exit)),
    avgZero: avg(rows.map((r) => r.results.zero)),
    zeroCount: rows.filter((r) => r.results.zero !== null).length,
    avgEntryStrike: avg(rows.map((r) => r.results.entryStrike)),
    entryStrikeCount: rows.filter((r) => r.results.entryStrike !== null).length,
    avgMax: avg(rows.map((r) => r.results.max)),
    avgClose: avg(rows.map((r) => r.results.close)),
    dollars: priced.reduce((sum, r) => sum + (r.results.exitDollars || 0), 0),
    // ROI (his ask 2026-09-28): profit / what the contracts cost, 1 each.
    invested: priced.reduce((sum, r) => sum + ((r.results && r.results.cost) ?? num(r.entry) ?? 0) * 100, 0),
    roi: (() => {
      const cost = priced.reduce((sum, r) => sum + ((r.results && r.results.cost) ?? num(r.entry) ?? 0) * 100, 0);
      return cost > 0 ? (priced.reduce((sum, r) => sum + (r.results.exitDollars || 0), 0) / cost) * 100 : null;
    })(),
    // More columns (his ask 2026-09-30 "add more column"): size of the average
    // win and loss, the best and worst trade, how many days it made money,
    // trades a day, and profit factor ($ won / $ lost).
    ...extraStats(completed),
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
/** A Daily line trade whose signal came before 10:00 ET (the "Daily line before 10:00" view). */
export function isEarlyDayLine(t) {
  if (!t || t.rule !== "dayLine") return false;
  const clock = String(t.at || "").slice(11, 16);
  return clock !== "" && clock < "10:00";
}

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
    .filter((r) => r.trades > 0);
  // "Daily line before 10:00" (2026-10-08, his ask to watch the 70% slice): a VIEW of
  // the Daily line trades signalled before 10:00 ET - listed on the leaderboard,
  // never added to the totals (they are the same trades).
  const early = rows.filter(isEarlyDayLine);
  if (early.length) leaderboard.push({ rule: "dayLineEarly", label: botRuleLabel("dayLineEarly", side), test: true, view: true, ...stats(early) });
  leaderboard.sort((a, b) => (b.avgExit ?? -Infinity) - (a.avgExit ?? -Infinity));
  return { rows, leaderboard, total: stats(rows), days: (Array.isArray(days) ? days : []).map((d) => d.day) };
}

// Jev shadow (momx/jev_trade.py, 2026-10-02 TEST): every trade the bot buys is
// also read by TypeSafe Jev, which records P(take) as t.jevP. The bot still
// takes EVERY trade - this only compares, per rule, the trades Jev would have
// taken (jevP >= cutoff) with the ones it would have skipped. Cutoff 0.30 was
// fixed before the back-test (scripts/jev_bot_backtest.py). Trades with no jevP
// (before 2026-10-05, or opened right after a restart) are left out.
export const BOT_JEV_CUTOFF = 0.3;

export function botJevCompare(rows, cutoff = BOT_JEV_CUTOFF) {
  const list = Array.isArray(rows) ? rows : [];
  const asked = list.filter((r) => num(r.jevP) !== null);
  const split = (subset) => ({
    all: stats(subset),
    jev: stats(subset.filter((r) => num(r.jevP) >= cutoff)),
    rest: stats(subset.filter((r) => num(r.jevP) < cutoff)),
  });
  return {
    cutoff,
    asked: asked.length,
    notAsked: list.length - asked.length,
    total: split(asked),
    rows: BOT_RULE_ORDER.filter((rule) => asked.some((r) => r.rule === rule)).map((rule) => ({
      rule, label: botRuleLabel(rule, list.find((r) => r.rule === rule)?.side || "bull"), test: BOT_TEST_RULES.has(rule),
      ...split(asked.filter((r) => r.rule === rule)),
    })),
  };
}

/** "Jev BOT" (his ask 2026-10-02): the same days with only the trades Jev would take. */
export function botJevBook(days, cutoff = BOT_JEV_CUTOFF) {
  return (Array.isArray(days) ? days : []).map((d) => ({
    ...d, trades: (Array.isArray(d && d.trades) ? d.trades : []).filter((t) => botJevApproved(t, cutoff) === true),
  }));
}

/** "News trade": the bot's "news" rule trades (his ask 2026-10-08, "make sure bot
 * also take the same") - the scanner's NEWS ▲ (BULL) / NEWS ▼ (BEAR) list with Scan
 * matches only: a scan match whose News cell Jev reads bullish / bearish
 * (momx/trade_news.news_rule_symbols). Starts 2026-10-08. */
export function botNewsBook(days) {
  return (Array.isArray(days) ? days : []).map((d) => ({
    ...d, trades: (Array.isArray(d && d.trades) ? d.trades : []).filter((t) => t && t.rule === "news"),
  }));
}

/** "AI pick" (2026-10-17, his "Add some bot trade also with that"): only the
 * trades the shadow AI model scored 55%+ (t.aiP, from momx/ai_filter.py). */
export const BOT_AI_CUTOFF = 0.55;
export function botAiBook(days, cutoff = BOT_AI_CUTOFF) {
  return (Array.isArray(days) ? days : []).map((d) => ({
    ...d, trades: (Array.isArray(d && d.trades) ? d.trades : []).filter((t) => num(t && t.aiP) !== null && num(t.aiP) >= cutoff),
  }));
}

export function botJevApproved(t, cutoff = BOT_JEV_CUTOFF) {
  const p = num(t && t.jevP);
  return p === null ? null : p >= cutoff;
}

// 2x expected move check (his rule 2026-10-03: "fewer losses - exp move 2x and ROI at least
// 100"; then "don't skip it, just trade"). The bot still takes EVERY trade; each one is marked
// whether its option would be worth >= 2x its cost if the stock reached 2x the 1-day expected
// move from the price at entry (momx/option_track.py risk_check). Older trades are checked
// from their saved spot / expectedMove / strike / entry. null = not enough data.
export const BOT_RISK_EM_MULTIPLE = 2;
export const BOT_RISK_MIN_ROI = 1;

export function botRiskRoi(t) {
  if (!t) return null;
  const saved = num(t.roi2em);
  if (saved !== null) return saved;
  const spot = num(t.spot);
  const em = num(t.expectedMove);
  const strike = num(t.strike);
  const entry = num(t.entry);
  if (!(spot > 0) || !(em > 0) || strike === null || !(entry > 0)) return null;
  const bear = t.side === "bear" || t.board === "bear";
  const edge = bear ? spot - BOT_RISK_EM_MULTIPLE * em : spot + BOT_RISK_EM_MULTIPLE * em;
  const intrinsic = Math.max(0, bear ? strike - edge : edge - strike);
  return intrinsic / entry - 1;
}

export function botRiskOk(t) {
  if (t && typeof t.riskOk === "boolean") return t.riskOk;
  const roi = botRiskRoi(t);
  return roi === null ? null : roi >= BOT_RISK_MIN_ROI;
}

export function botRiskCompare(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const known = list.filter((r) => botRiskOk(r) !== null);
  const split = (subset) => ({
    all: stats(subset),
    jev: stats(subset.filter((r) => botRiskOk(r) === true)),
    rest: stats(subset.filter((r) => botRiskOk(r) === false)),
  });
  return {
    checked: known.length,
    notChecked: list.length - known.length,
    total: split(known),
    rows: BOT_RULE_ORDER.filter((rule) => known.some((r) => r.rule === rule)).map((rule) => ({
      rule, label: botRuleLabel(rule, list.find((r) => r.rule === rule)?.side || "bull"), test: BOT_TEST_RULES.has(rule),
      ...split(known.filter((r) => r.rule === rule)),
    })),
  };
}

export function formatBotPct(value, digits = 0) {
  if (value === null || value === undefined) return "-";
  if (value !== 0 && Math.abs(value) < 0.01) return (value > 0 ? "+" : "-") + "<0.01%";
  if (value !== 0 && Math.abs(value) < 1) digits = Math.max(digits, 2);
  return (value >= 0 ? "+" : "") + value.toFixed(digits) + "%";
}

export function formatBotDollars(value) {
  if (value === null || value === undefined) return "-";
  const abs = Math.round(Math.abs(value) * 100) / 100;
  return (value >= 0 ? "+$" : "-$") + abs.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(abs) ? 0 : 2, maximumFractionDigits: 2 });
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

// "Which contract, which exit" scoreboard (Part 3, 2026-10-10): every rule's
// trades re-scored 9 ways - next week / same-day / 0.20-delta contract, each
// sold at his exit, at the 15:50 close, or at the day's best - at the chosen fills.
export const BOT_EXIT_LABELS = Object.freeze({ exit: "Strategy exit", close: "Hold to close", best: "Best price" });
export const BOT_CONTRACT_LABELS = Object.freeze({ main: "Next week", zero: "Same-day", entry: "0.20 delta" });
export const BOT_COMBOS = Object.freeze(
  ["main", "zero", "entry"].flatMap((contract) => ["exit", "close", "best"].map((exit) => ({ key: contract + ":" + exit, contract, exit }))),
);

function comboCell(rows, combo, fills) {
  let wins = 0;
  let losses = 0;
  let sum = 0;
  let dollars = 0;
  let cost = 0;
  let priced = 0;
  let completed = 0;
  for (const r of rows) {
    const res = botLegResult(r, combo.contract, combo.exit, fills);
    if (res.pct === null) continue;
    priced += 1;
    sum += res.pct;
    dollars += res.dollars;
    cost += res.cost * 100;
    if (legClosed(r, combo.contract, combo.exit)) {
      completed += 1;
      if (res.pct > 0) wins += 1;
      else if (res.pct < 0) losses += 1;
    }
  }
  return priced
    ? { priced, wins, losses, winRate: completed ? (wins / completed) * 100 : null, avgPct: sum / priced, dollars, roi: cost > 0 ? (dollars / cost) * 100 : null }
    : { priced: 0, wins: 0, losses: 0, winRate: null, avgPct: null, dollars: 0, roi: null };
}

/** rule rows x 9 (contract x exit) columns over report rows (botReport().rows). */
export function botExitMatrix(rows, side = "bull", fills = BOT_FILLS) {
  const list = (Array.isArray(rows) ? rows : []).filter((r) => r && !r.badQuote);
  const rules = BOT_RULE_ORDER.filter((rule) => list.some((r) => r.rule === rule));
  return {
    combos: BOT_COMBOS,
    rows: rules.map((rule) => {
      const mine = list.filter((r) => r.rule === rule);
      return { rule, label: botRuleLabel(rule, side), test: BOT_TEST_RULES.has(rule), trades: mine.length,
        cells: BOT_COMBOS.map((c) => comboCell(mine, c, fills)) };
    }),
    total: BOT_COMBOS.map((c) => comboCell(list, c, fills)),
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
