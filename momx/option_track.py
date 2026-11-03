"""Real OPTION results for the scanner's rules - tracked live (2026-09-27).

Ganesh trades short-dated OTM calls/puts, but every back-test scored the
STOCK (there is no option-price history in this app). "Yes please" to: save
the price of one real contract at each signal and follow it through the day,
so the scorecard can say what the OPTION did.

WHAT IS TRACKED - the first time today each rule fires on a ticker, 09:30-15:30
ET, on the board it fired on (bull = calls, bear = puts):
  go          A/A+ with a completed GO (m5.gapGo.goAt today) and the right side
              in control on 30m (adx 30m +DI/-DI) - momxFilters.gapAndGo
  goRvolMacd  GO + the worker's own confirmation (m5.goConfirmation: MACD and
              30m side) + RVOL cyan (bull) / magenta (bear) on 5m/15m/30m -
              momxFilters.goRvolMacd (the screen filter, commit 6d817bb)
  mxaplus     row.momoxAPlus stamped (bull only - there is no bear MX A+)
  daily2      row.strategy.daily2 on today's list
  turn        row.marketTurn stamped (bull only)

THE CONTRACT - no guessing, the same choice every time: the nearest expiry
that is NOT today (days_to_expiration >= 1, as momx/strategy_g.option_plan),
and the out-of-the-money strike NEAREST the price (first strike above spot
for a call, below for a put) with a live price. Price = mid of bid/ask (else
mark/last) - a real fill is usually a little worse; said so on the scorecard.

FOLLOWING IT - the contract is re-priced every REPRICE_SECONDS until 15:55 ET
from the same /api/oi-finder-chain the ticker card uses (:3002 with
X-AGX-Background, one chain at a time, spaced, paused while Schwab's
transport is blocked - the momx/optionable.py discipline). Stored per ET day:
entry, best, worst, last (at/after 15:50 = the close), all with times.

Artifacts: <root>/momx_option_track/<day>.json (bull root = artifacts/, bear =
artifacts/bear/ like every bear book). Never raises into a build. No thread
at import.
"""
from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo
from momx.bot_levels import LevelEvaluator, LEVEL_RULES

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_option_track"
CHAIN_URL = os.environ.get("AGX_MOMX_OPTIONABLE_URL", "http://127.0.0.1:3002/api/oi-finder-chain?symbol=")
#: Entry AT THE SIGNAL (2026-10-08, his ask "take trade start 9:30 if you see in rules"):
#: api_server.option_contract_quotes picks the contract from the chain it already holds,
#: re-anchored on the live stock price, and prices it from a live Schwab quote. Measured
#: 10-08 before this: entries waited a median 40-75 min behind the one-chain-per-8s queue.
QUOTE_URL = os.environ.get("AGX_MOMX_QUOTE_URL", "http://127.0.0.1:3002/api/option-contract-quotes?q=")
FAST_BATCH = 60
QUOTE_CLOSE_RETRY_SECONDS = 60
OPTION_QUOTES_URL = os.environ.get("AGX_MOMX_OPTION_QUOTES_URL", "http://127.0.0.1:3002/api/option-quotes?symbols=")
#: Writes are batched (2026-10-08, from the TOS-parity session's py-spy): the day
#: file (2.7 MB) and carry.json (1.4 MB, 5,177 entries) were re-encoded with
#: indent=1 after EVERY priced symbol - 55 ms per carry write alone, inside the
#: scanner process, and at 15:50 every open symbol is priced. Now at most one
#: write per SAVE_EVERY_SECONDS, compact JSON, flushed when the queue is idle,
#: before a day change and at the end of each board pass.
#: 10 -> 30 s (2026-10-16): the bull + bear day files and carry.json are now
#: ~8 MB of JSON together, and re-encoding them every 10 s was ~9% of the
#: worker main process's GIL (py-spy --gil), slowing the Watchlist build.
SAVE_EVERY_SECONDS = 30.0
STATUS_URL = os.environ.get("AGX_MOMX_OPTIONABLE_STATUS_URL", "http://127.0.0.1:3002/api/schwab/status")
HEADERS = {"X-AGX-Background": "1"}
FROM_MIN, UNTIL_MIN = 9 * 60 + 30, 15 * 60 + 30     # new signals
CLOSE_MIN, STOP_MIN = 15 * 60 + 50, 15 * 60 + 58    # "the close" mark window, last re-price
REPRICE_SECONDS = 600
#: HIS EXIT (2026-09-28, "exit ... below VWAP/21 EMA exit the trade profit or
#: loss", "all trade weekly hold max profit also, or 0DTE trade close"): each
#: trade is scored four ways -
#:   exitPrice  the weekly at the first COMPLETED 5m close below the scanner's
#:              VWAP or 5m EMA21 after entry (bull; above for bear), else 15:50;
#:   zExit      the same signal on a 0DTE contract (when the chain has today's
#:              expiry), closed at that exit or 15:50;
#:   holdBest   the weekly's highest mark until it expires (carried across days
#:              in CARRY_FILE, re-priced every CARRY_REPRICE_SECONDS);
#:   close      the 15:50 mark (unchanged).
#: The exit is read when the board rebuilds (15-35 s), so a mark comes from the
#: next re-price after the signal - a few seconds to a minute late.
CARRY_FILE = "carry.json"
CARRY_REPRICE_SECONDS = 1800
#: Day files kept (his ask 2026-09-28: "save the data 90 days", then "do 180
#: days also"); older ones are deleted once a day when the book rolls over.
KEEP_DAYS = 180
FETCH_GAP = 8.0
WARM_POLLS, WARM_POLL_SECONDS = 8, 4.0
#: FRESH CHAINS ONLY (2026-10-02). /api/oi-finder-chain answers with a disk copy
#: while it refreshes (diskCached / stale, scannedAt hours old - 10,150 s seen
#: that evening). The bot priced 523 of 8,694 trades (Sep 28 - Oct 2) from such
#: copies: an old spot picked the strike and an old mid became the entry, e.g.
#: APLD 24.5C "bought" at $0.25 with the stock at $25.90 (worth >= $1.40). Those
#: fills made most of the recorded profit. A chain now counts only if Schwab read
#: it within CHAIN_MAX_AGE_SECONDS and its spot agrees with the scanner's live
#: price within SPOT_MISMATCH; otherwise the trade stays pending and is retried.
CHAIN_MAX_AGE_SECONDS = 180
JEV_PICK_FRESH_SECONDS = 15 * 60
JEV_PICK_CUTOFF = 0.30        # = momx.jev_trade.JEV_CUTOFF (the scanner's "✓ JEV" tag)
#: The bot's "Jev pick" RULE buys from a lower score (his ask 2026-10-03: "Bot Jev score >= 10
#: trade - want to see how it performs and improve Jev"). Each trade keeps its score (jevP), so
#: the Bot page's Jev >=10 / 20 / 30% buttons split the results by cut-off.
JEV_RULE_CUTOFF = 0.10
SPOT_MISMATCH = 0.015
RULES = ("go", "goRvolMacd", "mxaplus", "daily2", "turn", "star1", "c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath")
#: INFO rules = every other tag the Setup column shows (Ganesh 2026-09-28:
#: "take all the trades what you see in the setup, want to see who is winning
#: everyday"). NOT proven - on stock prices the arrows did no better than
#: random (Sep 1-25); the others were never tested as trades. Each takes the
#: first INFO_MAX_PER_DAY a day (bull board): every trade is a chain fetch
#: every 10 min, one chain per FETCH_GAP, and chain fetches are what saturated
#: the CPU before - 6 x 15 caps the day at ~90 fetches per re-price round.
INFO_RULES = ("c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath",
              "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom", "momStrong", "momFlip", "jev",
              "explosive", "steady")
#: "dayLine" (2026-09-29, his AMD / GOOGL / NVDA / AAPL charts: "enter above
#: 21 EMA calls or below 21 EMA puts with volume increasing or momentum"):
#: m5.dayLine.signal on one of the board's 30 biggest stocks by average dollar
#: volume (DAY_LINE_MEGA). Back-test Sep 1-28: calls +0.63-0.75%/trade vs
#: +0.13-0.30 random (small n); puts no better than random. TEST.
DAY_LINE_MEGA = 30
#: The only ETFs the daily-line rules take (his ask 2026-09-29: "add SPY/QQQ,
#: no skip this ETF"). Sep 1-28 on the same rule: SPY calls 4 trades +0.35%
#: avg, puts -0.16%; QQQ calls 7 +0.34%, puts 3 -0.50% (stock moves, tiny n).
DAY_LINE_ETFS = frozenset({"SPY", "QQQ"})
#: "openPath" (2026-09-28, his ABVX rule: "premarket CALL2H + C4H and EMA +
#: VWAP + 5 mins Ichi and High OI and no levels to target 0.5"): 09:30-10:30,
#: the ZS lines (above EMA9/21/50 + VWAP + cloud), no level within +$0.50
#: (pivots, pDH, pmH - not today's own high), and a live 2H/4H chart arrow
#: today. High OI is NOT a gate (it passes ~95% of names); the walls are saved
#: with every trade. Not back-tested yet (run scheduled 2026-09-28 16:12).
#: Sector rules (2026-09-28, "ZS and CRWD, those are sector trades, I don't see
#: in bot"): a stock listed among its sector's leaders (up + above VWAP) while
#: the sector shows 🔥 - sectorHot = one of the day's two latched HOT sectors,
#: sectorMove = a "moving now" sector (Cybersecurity 09-28). Back-test Sep 1-25
#: (hot_arrow_bt): up + above-VWAP members of a hot sector +0.59%/trade, 34%
#: hit +2% first; of a moving-now sector +0.15%. Tracked for real options.
#: Per-rule daily caps where 15 is too few (2026-09-28, ABVX: "dont miss this
#: kind of trade"): 44 C2H/C4H and ~40 CALL arrows fired on the bull board that
#: day. The re-price loop is one chain per FETCH_GAP, so more trades only make a
#: round slower (~10-15 min), never more frequent.
#: 2026-09-29 ("don't miss any trades today"): on 09-28 five rules hit their
#: cap (C2H 40, CALL2H 40, ADX / SKIT / sector-moving 15) and later signals
#: were never taken. Caps are now a flood guard only (150/rule/day); new
#: trades are priced FIRST (_urgent queue), so a long day slows re-pricing of
#: open trades, never the entry price.
INFO_CAP: dict[str, int] = {"jev": 1000}   # Jev pick at 10%+ (2026-10-03): room for the whole day
#: "zs" (2026-09-28, "Yes add it to the bot as test"): his ZS entry -
#: momentum._zs_rule on the last completed 5m bar (above EMA9/21/50 + chart
#: VWAP + Ichimoku cloud, nothing from dH / pmH / pDH / P / R1 / R2 within 2%
#: above). Back-test Sep 1-25: no edge (~0%/trade); tracked for real options.
INFO_MAX_PER_DAY = 1000
#: 2026-10-08 ("trades should take if you see the signal 9:30-3:30, no guess work"):
#: at 150 a day, 3-5 rules hit the cap EVERY day Oct 1-8 (MOM, RVOL, SKIT, MOM
#: strong, FLIP...) and every later signal was dropped. New trades are now priced
#: from one batched quote (QUOTE_URL), so the cap is a flood guard only.
SKIT_BREAK_TFS = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
SKIT_BULL_BG = ("cyan", "green", "lime")
SKIT_BEAR_BG = ("magenta", "red", "light_red")
SKIT_FRESH_SECONDS = 60 * 60
RVOL_TAG_TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "D")


def _m(v: Any) -> Mapping:
    return v if isinstance(v, Mapping) else {}


def _num(v: Any) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _et(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ET)


def _in_control_30(row: Mapping, bear: bool) -> bool:
    a30 = _m(_m(row.get("adx")).get("30m"))
    plus, minus = _num(a30.get("plus")), _num(a30.get("minus"))
    if plus is None or minus is None:
        return False
    return minus > plus if bear else plus > minus


def _stamp_today(value: Any, today) -> bool:
    """An ISO string or epoch-seconds stamp that falls on today (ET)."""
    n = _num(value)
    if n is not None and n > 1e9:
        return _et(n).date() == today
    try:
        return datetime.fromisoformat(str(value)).astimezone(ET).date() == today
    except (TypeError, ValueError):
        return False


#: ETFs the bot DOES trade on every rule (Ganesh 2026-10-11: "Trade spy/qqq in
#: etf in bot"). Every other ETF and the cash indexes (SPX, NDX, ... - "don't
#: trade SPX bot") stay out, per 2026-09-27 "use only stocks".
BOT_TRADED_ETFS = frozenset({"SPY", "QQQ"})


def _etf_excluded(row: Mapping) -> bool:
    """True when the row is an ETF/index the bot must not trade."""
    return bool(row.get("etf")) and str(row.get("symbol") or "").upper() not in BOT_TRADED_ETFS


def mega_caps(rows: list) -> set:
    """The board's DAY_LINE_MEGA biggest non-ETF stocks by m5.dayLine.dollarVol."""
    ranked = sorted(((_num(_m(_m(r.get("m5")).get("dayLine")).get("dollarVol")) or 0, r.get("symbol"))
                     for r in rows if isinstance(r, Mapping) and not r.get("etf")), reverse=True)
    return {s for v, s in ranked[:DAY_LINE_MEGA] if v > 0 and isinstance(s, str)}


def day_line_now(row: Mapping, now: datetime, key: str = "signal") -> bool:
    """m5.dayLine[key] on a bar from today (the read itself is 09:35-11:00 only).
    key "reclaim" = the reverse case (ZS 09-28), any stock - "dayReclaim"."""
    if row.get("etf") and row.get("symbol") not in DAY_LINE_ETFS:
        return False
    d = _m(_m(row.get("m5")).get("dayLine"))
    bar = _num(d.get("barAt"))
    return d.get(key) is True and bar is not None and _et(bar).date() == now.astimezone(ET).date()


def live_today(row: Mapping, now: datetime) -> bool:
    """The ticker has a 5m candle from TODAY (ET). 2026-10-08: replaying the day's
    board as if it were the next morning, Market turn / MX A+ / Daily 2 / ADX /
    sector / hot-leader rules still fired on the stale rows - on a market holiday
    or a stale feed the bot would have "bought" yesterday's signals. No candle
    today = no new trade."""
    today = now.astimezone(ET).date()
    for at in (_num(_m(_m(row.get("m5")).get("sqzMom")).get("barAt")),
               _num(_m(_m(row.get("rvol")).get("5m")).get("barAt"))):
        if at and at > 0 and _et(at).date() == today:
            return True
    return False


def fired(row: Mapping, bear: bool, now: datetime) -> list[str]:
    """Which RULES this row shows right now (mirrors momxFilters.js)."""
    out: list[str] = []
    if _etf_excluded(row):  # ETFs never fire a rule (momx/etf_list.py, 2026-09-27 "use only stocks")
        return out
    today = now.astimezone(ET).date()
    letter = _m(row.get("grade")).get("letter")
    m5 = _m(row.get("m5"))
    go_at = _num(_m(m5.get("gapGo")).get("goAt"))
    go = (letter in ("A+", "A") and go_at is not None and go_at > 0 and _et(go_at).date() == today
          and (go_at + 300) <= now.timestamp() and _in_control_30(row, bear))
    if go:
        out.append("go")
    c = _m(m5.get("goConfirmation"))
    bar_at = _num(c.get("barAt"))
    if (letter in ("A+", "A") and go_at and go_at > 0 and bar_at is not None and _et(bar_at).date() == today
            and c.get("macdDown" if bear else "macdUp") is True and c.get("sellers30" if bear else "buyers30") is True
            and any(_m(_m(row.get("rvol")).get(tf)).get("bg") == ("magenta" if bear else "cyan") for tf in ("5m", "15m", "30m"))):
        out.append("goRvolMacd")
    # On the bear board these stamps come from the BEAR MX A+ / market-turn
    # books (2026-09-28) - the bot takes them on both boards.
    if _m(row.get("momoxAPlus")).get("at"):
        out.append("mxaplus")
    if isinstance(_m(row.get("strategy")).get("daily2"), Mapping):
        out.append("daily2")
    if _m(row.get("marketTurn")).get("at"):
        out.append("turn")
    # The Setup column's information tags, on BOTH boards (bear 2026-09-28,
    # "for bear why I see only 3 rules"): bear = P2H/P4H and PUT2H/PUT4H
    # arrows, -DI ADX, magenta/red Skittles and RVOL, a fired-down squeeze.
    if _arrow_today(row, today, ("P2H", "P4H") if bear else ("C2H", "C4H")):
        out.append("c2h")
    if _arrow_today(row, today, ("PUT2H", "PUT4H") if bear else ("CALL2H", "CALL4H")):
        out.append("call2h")
    if _adx_cyan(row, bear):
        out.append("adx")
    if _skit_fresh(row, now, bear):
        out.append("skit")
    if _rvol_painted(row, today, bear):
        out.append("rvol")
    if _sqz_fired_today(row, today):
        out.append("sqz")
    # MOM (his ask 2026-09-30 "in BOT I don't see MOM, add it"): the Setup
    # column's MOM tag - the chart's Squeeze Momentum line CYAN (bull: above 0
    # and rising) / MAGENTA (bear: below 0 and falling) on today's last 5m bar.
    # Back-test (with ADX + RVOL added) = random; this plain version untested.
    sm = _m(m5.get("sqzMom"))
    sm_bar = _num(sm.get("barAt"))
    if sm.get("color") == ("magenta" if bear else "cyan") and sm_bar and _et(sm_bar).date() == today:
        out.append("mom")
        # MOM strong (his ask 2026-10-03, "Strong call setup ... for scanner
        # setup and BOT"): the same momentum colour AND the ADX background
        # cyan (bear: magenta) on 5m and 30m - momxFilters.adxBackgroundAgrees.
        if _adx_background_agrees(row, bear):
            out.append("momStrong")
    # MOM FLIP (his ask 2026-10-03, APLD 10:50) - momxFilters.momFlipTag: the
    # 5m ADX background flipped to our side on the last CLOSED candle (today)
    # while the momentum dots move our way on that same candle.
    a5 = _m(_m(row.get("adx")).get("5m"))
    flip_at = _num(a5.get("closedCrossAt"))
    sm_val, sm_prev = _num(sm.get("value")), _num(sm.get("prev"))
    if (a5.get("closedCross") == ("bear" if bear else "bull") and flip_at and _et(flip_at).date() == today
            and sm_val is not None and sm_prev is not None and (sm_val < sm_prev if bear else sm_val > sm_prev)
            and (not sm_bar or int(sm_bar) == int(flip_at))):
        out.append("momFlip")
    # 💥 Explosive / 📈 Steady (his ask 2026-10-10, "yes" to tracking them):
    # the grade's 5m pattern (momx/momentum.py _pattern) the Setup column shows
    # beside the letter - on both boards (bear = the mirror). Never tested.
    pattern = m5.get("pattern")
    if pattern in ("explosive", "steady"):
        out.append(pattern)
    # ZS / ZS2 / ABVX on both boards (bear = below every line, clear path DOWN,
    # 2026-09-28). The bear SECTOR rules are board-level - OptionTrackBook.apply.
    if _zs_now(row, now):
        out.append("zs")
    if _zs_now(row, now, session_highs_block=False):
        out.append("zs2")
    if _open_path(row, now, bear):
        out.append("openPath")
    if not bear:
        # SOLO and 🔥#1/#2 hot-sector leaders (2026-09-29, "why I don't see SOLO
        # rules in BOT?"): the worker's own stamps, bull board only (bear_view
        # drops them - no bear version exists yet).
        solo_at = _m(row.get("solo")).get("at")
        if solo_at and _stamp_today(solo_at, today):
            out.append("solo")
        if _m(row.get("hotLeader")).get("rank") is not None:
            out.append("hotLead")
        rot = _m(row.get("sectorRotation"))
        leaders = {_m(x).get("symbol") for x in rot.get("leaders") or []}
        if row.get("symbol") in leaders:
            if rot.get("hot") is True:
                out.append("sectorHot")
            elif rot.get("late") is True:
                out.append("sectorMove")
    return out


# --------------------------------------------------------------------- ⭐1
# momxFilters.rankBestSetups / bestSetupRanks (2026-09-28, "Add ⭐1 to the bot
# too"): the board's GO / OPT rows ranked 1) GO in a 🔥 hot or moving-now
# sector, 2) other GO, 3) OPT first seen 09:30-10:00; inside each: fewer ⚠
# warnings, then a bullish AI news read, then the earlier signal. Each symbol
# that becomes ⭐1 on a board gets ONE trade (bull board).
SKIT_CROSS_BULL = ("cyan", "green", "lime", "dark_green")
SKIT_CROSS_BEAR = ("magenta", "red", "light_red", "plum")
NEWS_WORDS = ("EARNINGS", "UPGRADE", "DOWNGRADE", "CONTRACT", "PRODUCT", "FDA", "DEAL", "OFFERING", "LEGAL", "MACRO")
OPT_CUTOFF_MIN = 10 * 60


def gap_and_go_at(row: Mapping, today, bear: bool = False) -> float | None:
    """momxFilters.gapAndGo: the GO candle's start (epoch s) or None. On the
    bear board the worker's gapGo is the gap-DOWN version; 30m SELLERS rule."""
    if _etf_excluded(row) or _m(row.get("grade")).get("letter") not in ("A+", "A"):
        return None
    t = _num(_m(_m(row.get("m5")).get("gapGo")).get("goAt"))
    if not t or t <= 0 or not _in_control_30(row, bear) or _et(t).date() != today:
        return None
    return t


def options_setup(row: Mapping, bear: bool = False) -> bool:
    """momxFilters.optionsSetup: A+/A, 2h and 4h Skittles cross (bear: the
    bearish crosses), the right side in control on 30m, Extended."""
    if _m(row.get("grade")).get("letter") not in ("A+", "A"):
        return False
    sk = _m(row.get("skittles"))
    cross = SKIT_CROSS_BEAR if bear else SKIT_CROSS_BULL
    return (_m(sk.get("2h")).get("bg") in cross and _m(sk.get("4h")).get("bg") in cross
            and _in_control_30(row, bear) and _m(row.get("m5")).get("state") == "extended")


def setup_warnings(row: Mapping, bear: bool = False) -> int:
    """How many momxFilters.setupWarnings a row carries (the 30m-sellers
    warning is bull only, as in momxFilters)."""
    n = 0
    c = _m(row.get("catalyst"))
    if c.get("direction") == "bearish" and c.get("category") in NEWS_WORDS:
        n += 1
    pct = _num(row.get("pctChange"))
    if pct is not None and pct >= 8:
        n += 1
    m5 = _m(row.get("m5"))
    if m5.get("state") == "fading":
        n += 1
    last, vwap = _num(row.get("last")), _num(m5.get("vwap"))
    if last is not None and vwap is not None and last < vwap:
        n += 1
    a30 = _m(_m(row.get("adx")).get("30m"))
    plus, minus = _num(a30.get("plus")), _num(a30.get("minus"))
    if not bear and plus is not None and minus is not None and minus > plus:
        n += 1
    return n


def star_one(rows: list, opt_seen: Mapping, today, bear: bool = False) -> str | None:
    """The board's ⭐1 symbol right now (rows in board order), or None."""
    ranked = []
    for index, row in enumerate(rows):
        sym = row.get("symbol")
        if not isinstance(sym, str) or _etf_excluded(row):
            continue
        go = gap_and_go_at(row, today, bear)
        opt = None if go else opt_seen.get(sym)
        if go is None and opt is None:
            continue
        s = _m(row.get("sectorRotation"))
        tier = (0 if (s.get("hot") or s.get("late")) else 1) if go is not None else 2
        c = _m(row.get("catalyst"))
        news_up = 0 if c.get("direction") == "bullish" and c.get("confidence") in ("high", "medium") else 1
        at = go if go is not None else float(opt)
        ranked.append(((tier, setup_warnings(row, bear), news_up, at, index), sym))
    return min(ranked)[1] if ranked else None


def _zs_now(row: Mapping, now: datetime, session_highs_block: bool = True) -> bool:
    """zs: every level blocks. zs2 (2026-09-28, ABVX blocked only by its own
    day high 96.78 -> dH): today's high and the premarket high do NOT block -
    only prior-day high and the pivots do. Back-test "B2core": no edge either
    (-0.22% all, +0.09% Watchlist) - both tracked for real options."""
    zs = _m(_m(_m(row.get("m5")).get("pillars")).get("zs"))
    bar = _num(zs.get("barAt"))
    blockers = [b for b in zs.get("blockers") or [] if session_highs_block or b not in ("dH", "pmH", "dL", "pmL")]
    if not (zs.get("above") is True and not blockers and bar):
        return False
    closed = _et(bar + 300)
    minute = closed.hour * 60 + closed.minute
    return closed.date() == now.astimezone(ET).date() and 9 * 60 + 35 <= minute <= 15 * 60 + 30


#: ABVX lines (his update 2026-09-30: "EMA 9/21 not 9/21/50 - it takes too much
#: time in 5 mins"): the 5m close above EMA 9, EMA 21, VWAP and the Ichimoku
#: cloud - the 50 EMA no longer has to be cleared (the ZS rule still uses it).
ABVX_LINES = ("ema9", "ema21", "vwap", "cloud")


#: Bot rules stamped on the row for the Setup column (row["dayLines"][key] =
#: the day's first fire): the daily-line rules (2026-09-29) and, since
#: 2026-09-30 ("tomorrow want to see ZS and ABVX also"), ZS and ABVX.
STAMP_RULES = {"dayLine": "line", "dayReclaim": "reclaim", "dayLine100": "line100",
               "zs": "zs", "openPath": "abvx"}


def _abvx_lines_ok(zs: Mapping, bear: bool = False) -> bool:
    close = _num(zs.get("close"))
    vals = [_num(zs.get(k)) for k in ABVX_LINES]
    if close is None or any(v is None for v in vals):
        return False
    return all((close < v) if bear else (close > v) for v in vals)


def _open_path(row: Mapping, now: datetime, bear: bool = False) -> bool:
    minute = now.astimezone(ET).hour * 60 + now.astimezone(ET).minute
    if not (9 * 60 + 30 <= minute <= 10 * 60 + 30):
        return False
    zs = _m(_m(_m(row.get("m5")).get("pillars")).get("zs"))
    bar = _num(zs.get("barAt"))
    if not (_abvx_lines_ok(zs, bear) and zs.get("clear050") is True and bar and _et(bar).date() == now.astimezone(ET).date()):
        return False
    return _arrow_today(row, now.astimezone(ET).date(),
                        ("PUT2H", "PUT4H", "P2H", "P4H") if bear else ("CALL2H", "CALL4H", "C2H", "C4H"))


# Bear sectors (2026-09-28, mirrors momxCells.sectorsForDirection on the bear
# board): FALLING = >= 60% of a sector's priced members (4+) down on the day
# AND below their 5m VWAP, median move <= -0.5%; top 3 by that share. Leaders
# = the 5 weakest of those members. "sectorHot" (bear) = the first 2 falling
# sectors seen 09:35-11:00, latched for the day; "sectorMove" = falling now.
BEAR_SECTOR_BREADTH, BEAR_SECTOR_MEDIAN, BEAR_SECTOR_MAX = 0.6, -0.5, 3


def falling_sectors(rows: list) -> dict[str, list[str]]:
    """sector -> its weakest leaders (bear read of the board's rows)."""
    groups: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, Mapping) or _etf_excluded(row):
            continue
        name = str(row.get("industry") or "").strip()
        pct = _num(row.get("pctChange"))
        if not name or pct is None or "ETF" in name:
            continue
        g = groups.setdefault(name, {"moves": [], "weak": []})
        g["moves"].append(pct)
        last, vwap = _num(row.get("last")), _num(_m(row.get("m5")).get("vwap"))
        if pct < 0 and last is not None and vwap is not None and last < vwap:
            g["weak"].append((pct, row.get("symbol")))
    ranked = []
    for name, g in groups.items():
        moves = sorted(g["moves"])
        n = len(moves)
        if n < 4:
            continue
        median = moves[n // 2] if n % 2 else (moves[n // 2 - 1] + moves[n // 2]) / 2
        share = len(g["weak"]) / n
        if share >= BEAR_SECTOR_BREADTH and median <= BEAR_SECTOR_MEDIAN:
            ranked.append((-share, median, name, [s for _, s in sorted(g["weak"])[:5]]))
    return {name: leaders for _, _, name, leaders in sorted(ranked)[:BEAR_SECTOR_MAX]}


def _timeline(row: Mapping) -> list:
    items = _m(row.get("gradeFresh")).get("timeline")
    return items if isinstance(items, list) else []


def _iso(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return at.astimezone(ET) if at.tzinfo else at.replace(tzinfo=ET)


def _adx_background_agrees(row: Mapping, bear: bool = False) -> bool:
    """momxFilters.adxBackgroundAgrees: on 5m AND 30m the with-trend DI is
    above 25 (the chart's cyan / magenta cloud) and above the other DI."""
    for tf in ("5m", "30m"):
        c = _m(_m(row.get("adx")).get(tf))
        mine, theirs = _num(c.get("minus" if bear else "plus")), _num(c.get("plus" if bear else "minus"))
        if mine is None or theirs is None or not (mine > 25 and mine > theirs):
            return False
    return True


def _adx_cyan(row: Mapping, bear: bool = False) -> bool:
    """momxFilters.adxCyan (graded rows only, as the tag): 5m ADX > 25 with
    +DI > 25 (bear: -DI > 25, the chart's magenta)."""
    if _m(row.get("grade")).get("letter") not in ("A+", "A", "B"):
        return False
    c = _m(_m(row.get("adx")).get("5m"))
    adx, di = _num(c.get("adx")), _num(c.get("minus" if bear else "plus"))
    return adx is not None and di is not None and adx > 25 and di > 25


def _skit_fresh(row: Mapping, now: datetime, bear: bool = False) -> bool:
    """momxFilters.skittlesBreaks: a with-trend Skittles cross FIRST seen in the
    last hour (grade-log timeline) that the block still shows."""
    colours = SKIT_BEAR_BG if bear else SKIT_BULL_BG
    cells = _m(row.get("skittles"))
    first: dict[str, float] = {}
    for item in _timeline(row):
        parts = str(_m(item).get("what") or "").split()
        if len(parts) != 4 or parts[0] != "SKIT" or parts[2] != "bg" or parts[3] not in colours:
            continue
        at = _iso(_m(item).get("at"))
        if at is None or at > now:
            continue
        first[parts[1]] = min(first.get(parts[1], at.timestamp()), at.timestamp())
    return any(tf in first and now.timestamp() - first[tf] <= SKIT_FRESH_SECONDS
               and _m(cells.get(tf)).get("bg") in colours for tf in SKIT_BREAK_TFS)


def _rvol_painted(row: Mapping, today, bear: bool = False) -> bool:
    """momxFilters.rvolBuilding: an RVOL cell painted cyan/green (bear:
    magenta/red) on today's bar."""
    colours = ("magenta", "red") if bear else ("cyan", "green")
    for tf in RVOL_TAG_TFS:
        c = _m(_m(row.get("rvol")).get(tf))
        bar = _num(c.get("barAt"))
        if c.get("bg") in colours and bar and _et(bar).date() == today:
            return True
    return False


SQZ_FIRE_EVENTS = tuple(f"SQZ {tf} released" for tf in ("2h", "4h", "D", "Wk"))


def _sqz_fired_today(row: Mapping, today) -> bool:
    """momxFilters.sqzFires: "SQZ <tf> released" seen today in the session, tf
    2h / 4h / D / Wk (D and Wk added 2026-09-30, his ask "not only 2h or 4h")."""
    for item in _timeline(row):
        what = str(_m(item).get("what") or "")
        at = _iso(_m(item).get("at"))
        if what in SQZ_FIRE_EVENTS and at and at.date() == today                 and 9 * 60 + 30 <= at.hour * 60 + at.minute < 16 * 60:
            return True
    return False


def _arrow_today(row: Mapping, today, labels: tuple) -> bool:
    for sig in row.get("chartSignals") or []:
        sig = _m(sig)
        if sig.get("label") not in labels or sig.get("goneAt"):
            continue   # an arrow the chart has since repainted away is never "bought"
        try:
            at = datetime.fromisoformat(str(sig.get("seenAt") or sig.get("at") or ""))
        except ValueError:
            continue
        if (at.astimezone(ET) if at.tzinfo else at.replace(tzinfo=ET)).date() == today:
            return True
    return False


def chain_age_seconds(chain: Any, now_ts: float) -> float | None:
    """Seconds since Schwab read this chain (its scannedAt); None if unknown."""
    raw = _m(chain).get("scannedAt")
    if not raw:
        return None
    try:
        return now_ts - datetime.fromisoformat(str(raw)).timestamp()
    except ValueError:
        return None


def chain_fresh(chain: Any, now_ts: float) -> bool:
    age = chain_age_seconds(chain, now_ts)
    if age is None:
        return not _m(chain).get("diskCached")
    return age <= CHAIN_MAX_AGE_SECONDS


def entry_impossible(entry: float, strike: float, live: float | None, bear: bool) -> bool:
    """An option bought for less than it is already in the money = a stale quote."""
    if not live or entry is None or strike is None:
        return False
    intrinsic = max(0.0, (strike - live) if bear else (live - strike))
    return entry < intrinsic * 0.9 - 0.02


def _price(r: Mapping) -> float:
    bid, ask = _num(r.get("bid")), _num(r.get("ask"))
    if (bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask)
            and bid >= 0 and ask > 0 and ask >= bid):
        return round((bid + ask) / 2, 4)
    return 0.0


def exit_signal(row: Mapping, entry_ts: float, bear: bool) -> str | None:
    """His exit: the last COMPLETED 5m candle that closed after the entry
    closed below VWAP or EMA21 (bull; above for bear). None = hold."""
    m5 = _m(row.get("m5"))
    last = _m(m5.get("lastCompleted"))
    start, close = _num(last.get("time")), _num(last.get("close"))
    if start is None or close is None or start + 300 <= entry_ts:
        return None
    vwap, e21 = _num(m5.get("vwap")), _num(m5.get("ema21"))
    if vwap is not None and (close > vwap if bear else close < vwap):
        return "above VWAP" if bear else "below VWAP"
    if e21 is not None and (close > e21 if bear else close < e21):
        return "above EMA21" if bear else "below EMA21"
    return None


def pick_contract(chain: Any, bear: bool, zero_dte: bool = False) -> dict | None:
    """Nearest non-today expiry (zero_dte: TODAY's expiry only), OTM strike
    nearest the price, with a price."""
    spot = _num(_m(chain).get("underlyingPrice"))
    rows = _m(chain).get("selectedExpiryChainRows")
    if not spot or spot <= 0 or not isinstance(rows, list):
        return None
    side = "PUT" if bear else "CALL"
    legs = [r for r in rows if isinstance(r, Mapping) and r.get("side") == side and r.get("symbol")
            and _num(r.get("strike")) and r.get("expiry")
            and (((_num(r.get("days_to_expiration")) or 0) == 0) if zero_dte
                 else ((_num(r.get("days_to_expiration")) or 0) >= 1))
            and ((_num(r["strike"]) < spot) if bear else (_num(r["strike"]) > spot)) and _price(r) > 0]
    if not legs:
        return None
    expiry = min(str(r["expiry"])[:10] for r in legs)
    legs = [r for r in legs if str(r["expiry"])[:10] == expiry]
    best = min(legs, key=lambda r: abs(_num(r["strike"]) - spot))
    return {"contract": best["symbol"], "strike": _num(best["strike"]), "expiry": expiry,
            "delta": _num(best.get("delta")), "spot": round(spot, 4), "entry": _price(best),
            "entryBid": _num(best.get("bid")), "entryAsk": _num(best.get("ask"))}


ENTRY_DELTA = 0.20
#: Rules a FADING 5m momentum blocks. Empty since 2026-09-28 (his call: "then
#: don't block MX A+ fading" - no rule blocks; the ⚠ fading warning still shows
#: and every trade records its momentum at entry for the Bot page).
FADING_BLOCKS: frozenset = frozenset()


def pick_entry_strike(chain: Any, bear: bool, expiry: str) -> dict | None:
    """The chain's ENTRY strike (App.jsx buildOptionRoiEstimates, his ask
    2026-09-28 "you should take that strike entry"): on ``expiry``, the OTM
    contract with 0 < |delta| < 0.20 whose |delta| is closest to 0.20, ties to
    the strike nearest spot. ABVX 2026-09-28: the 103C (delta 0.20) at $0.75."""
    spot = _num(_m(chain).get("underlyingPrice"))
    rows = _m(chain).get("selectedExpiryChainRows")
    if not spot or not isinstance(rows, list):
        return None
    side = "PUT" if bear else "CALL"
    legs = []
    for r in rows:
        if not isinstance(r, Mapping) or r.get("side") != side or str(r.get("expiry") or "")[:10] != expiry:
            continue
        strike, delta = _num(r.get("strike")), abs(_num(r.get("delta")) or 0.0)
        if strike is None or not r.get("symbol") or _price(r) <= 0 or not (0 < delta < ENTRY_DELTA):
            continue
        if (strike < spot) if bear else (strike > spot):
            legs.append((abs(ENTRY_DELTA - delta), abs(strike - spot), r))
    if not legs:
        return None
    best = min(legs, key=lambda x: (x[0], x[1]))[2]
    return {"eContract": best["symbol"], "eStrike": _num(best["strike"]), "eDelta": _num(best.get("delta")),
            "eEntry": _price(best), "eLast": _price(best), "eBest": _price(best), "eStatus": "open",
            "eEntryBid": _num(best.get("bid")), "eEntryAsk": _num(best.get("ask"))}


#: His risk gate (2026-10-03: "I want fewer losses - exp move 2x and ROI at least 100"):
#: a trade passes only if its option would be worth at least 2x what it cost (ROI >= +100%)
#: when the stock reaches 2x the 1-day expected move from the price at entry - the same
#: +/-EM2 band as his ticker card. Recorded on every trade; the Bot page's Risk gate
#: switch counts only the passing ones, so the skipped ones stay comparable.
RISK_EM_MULTIPLE = 2.0
RISK_MIN_ROI = 1.0


def risk_check(spot: Any, expected_move: Any, strike: Any, entry: Any, bear: bool) -> dict:
    """{em2Edge, roi2em, riskOk} - the option's value at the 2xEM edge vs its entry price.
    {} when any input is missing (riskOk unknown, never a silent pass)."""
    spot, em, strike, entry = _num(spot), _num(expected_move), _num(strike), _num(entry)
    if not spot or not em or em <= 0 or strike is None or not entry or entry <= 0:
        return {}
    edge = spot - RISK_EM_MULTIPLE * em if bear else spot + RISK_EM_MULTIPLE * em
    intrinsic = max(0.0, (strike - edge) if bear else (edge - strike))
    roi = intrinsic / entry - 1.0
    return {"em2Edge": round(edge, 4), "roi2em": round(roi, 4), "riskOk": roi >= RISK_MIN_ROI}


def levels_at_signal(chain: Any) -> dict:
    """The High-OI walls (top 3 a side, the ticker card's EM2 rule) and the
    1-day expected move from the chain at the signal. {} when unusable."""
    try:
        import oi_auto_alerts
        spot = _num(_m(chain).get("underlyingPrice"))
        rows = _m(chain).get("selectedExpiryChainRows") or []
        if not spot or not rows:
            return {}
        em = _num(_m(_m(chain).get("currentAtm")).get("expectedMove")) or 0.0
        walls = oi_auto_alerts.build_high_oi_walls(rows, spot, as_of=datetime.now(ET).date(), top_per_side=3,
                                                   expected_move=em)
        side = lambda key: [{"strike": w["strike"], "oi": w["openInterest"]} for w in walls.get(key) or []]
        return {"expectedMove": round(em, 4) if em else None, "callWalls": side("calls"), "putWalls": side("puts")}
    except Exception:  # noqa: BLE001 - levels are a bonus; never block the trade record
        return {}


def contract_quote(chain: Any, symbol: str) -> tuple[float | None, float | None]:
    """(bid, ask) of one contract in the chain - for the realistic fill (2026-10-09:
    buy at the ask, sell at the bid; the mid alone overstates option P/L)."""
    for r in _m(chain).get("selectedExpiryChainRows") or []:
        if isinstance(r, Mapping) and r.get("symbol") == symbol:
            bid, ask = _num(r.get("bid")), _num(r.get("ask"))
            return (bid if bid is not None and bid >= 0 else None), (ask if ask and ask > 0 else None)
    return None, None


def contract_mark(chain: Any, symbol: str) -> float | None:
    for r in _m(chain).get("selectedExpiryChainRows") or []:
        if isinstance(r, Mapping) and r.get("symbol") == symbol:
            p = _price(r)
            return p if p > 0 else None
    return None


def _default_quotes_fetch(contracts: list) -> Any:
    return _http_json(OPTION_QUOTES_URL + urllib.parse.quote(",".join(contracts), safe=",.-"), timeout=30.0)


def _default_quote_fetch(query: str) -> Any:
    return _http_json(QUOTE_URL + urllib.parse.quote(query, safe=":,.-"), timeout=30.0)


def _http_json(url: str, timeout: float = 40.0) -> Any:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


#: One FRESH chain serves both books (bull + bear) for SHARED_CHAIN_SECONDS (2026-10-03,
#: Jev pick at 10%+ adds entries; the Bear session flagged /api/oi-finder-chain load). Only
#: a chain that passes chain_fresh is kept, so the warm-up polling still sees a refresh.
SHARED_CHAIN_SECONDS = 60.0
_SHARED_CHAINS: dict[str, tuple[float, Any]] = {}
_SHARED_LOCK = threading.Lock()


def _default_fetch(symbol: str) -> Any:
    now = time.time()
    with _SHARED_LOCK:
        got = _SHARED_CHAINS.get(symbol)
        if got and now - got[0] <= SHARED_CHAIN_SECONDS and chain_fresh(got[1], now):
            return got[1]
    chain = _http_json(CHAIN_URL + symbol)
    if chain_fresh(chain, time.time()) and _m(chain).get("selectedExpiryChainRows"):
        with _SHARED_LOCK:
            _SHARED_CHAINS[symbol] = (time.time(), chain)
            if len(_SHARED_CHAINS) > 500:
                for key in sorted(_SHARED_CHAINS, key=lambda k: _SHARED_CHAINS[k][0])[:250]:
                    _SHARED_CHAINS.pop(key, None)
    return chain


def _default_healthy() -> bool:
    status = _m(_http_json(STATUS_URL, timeout=10.0))
    transport = _m(status.get("transport"))
    return not status.get("transportBlocked") and transport.get("coolingDown") is not True


def _day_files(directory: Path) -> list[Path]:
    folder = Path(directory) / DIRNAME
    try:
        return sorted(f for f in folder.glob("????-??-??.json") if f.is_file())
    except OSError:
        return []


#: Per-minute price path of every trade's main contract (2026-10-17, his "Fix all
#: issue" - the per-minute recording): one mid per minute from entry until the
#: 15:50 close, in its own day file so the Bot page's trade summary stays small.
#: Lets exits / trailing stops be tested walk-forward on real paths instead of
#: guessed from best / worst / close. Pruned on the same KEEP_DAYS clock.
PATHS_DIRNAME = "momx_option_paths"
#: The price-path file grows all day (one mid per minute per contract, a few MB
#: by the close) - rewritten at most this often, plus at the quote close and at
#: the day switch, instead of on every day-file save (which can be every second).
PATHS_SAVE_EVERY_SECONDS = 300.0


def setup_snapshot(row: Mapping, at=None) -> dict:
    """The scanner row's picture AT ENTRY - what the Setup column showed (2026-10-17,
    his "Setup column and record column"): RVOL, Skittles, squeeze, ADX, % change,
    grade, sector. Stored on the trade so the AI model learns from the whole
    setup, not just the rule name. Compact numbers only; never raises."""
    out: dict = {}
    try:
        from momx import chart_learning, scanner_learning
        signal_at = (at or datetime.now(ET)).replace(microsecond=0).isoformat()
        scanner = scanner_learning.snapshot({"at": signal_at, "scannerInputs": row.get("scannerInputs")}, "bull")
        if scanner:
            out["scannerInputs"] = scanner
        out["captureVersion"] = 1
        context = _m(row.get("m5")).get("chartContext")
        if isinstance(context, dict):
            candidate = {"at": (at or datetime.now(ET)).isoformat(), "symbol": row.get("symbol"),
                         "setup": {"chartContext": {**context, "symbol": row.get("symbol")}}}
            frozen = chart_learning.snapshot(candidate)
            if frozen:
                out["chartContext"] = frozen
        out["pct"] = _num(row.get("pctChange"))
        rv = _m(row.get("rvol"))
        for tf in ("5m", "15m", "30m", "1h"):
            out["rvol" + tf] = _num(_m(rv.get(tf)).get("value"))
        sk = [_num(_m(v).get("value")) for v in _m(row.get("skittles")).values()]
        sk = [v for v in sk if v is not None]
        out["skitAvg"] = round(sum(sk) / len(sk), 2) if sk else None
        on = 0
        for key in ("sqz", "sqzFast"):
            for v in _m(row.get(key)).values():
                if str(_m(v).get("value") or "-") not in ("-", "", "None"):
                    on += 1
        out["sqzOn"] = on
        adx = _m(row.get("adx"))
        for tf in ("5m", "30m"):
            a = _m(adx.get(tf))
            out["adx" + tf] = _num(a.get("adx"))
            plus, minus = _num(a.get("plus")), _num(a.get("minus"))
            out["di" + tf] = round(plus - minus, 2) if plus is not None and minus is not None else None
        out["hiLo"] = _num(_m(row.get("highLow")).get("value"))
        out["grade"] = {"A+": 3, "A": 2, "B": 1}.get(_m(row.get("grade")).get("letter"), 0)
        sr = _m(row.get("sectorRotation"))
        out["hot"] = 1 if sr.get("hot") else 0
        out["rs3"] = _num(sr.get("rs3"))
        out["reasons"] = len(row.get("scanReasons") or [])
    except Exception:  # noqa: BLE001 - a snapshot must never cost a trade
        pass
    out["capture"] = {"scanner": "captured" if out.get("scannerInputs") else "missing_or_invalid",
                      "chart": "captured" if out.get("chartContext") else "missing_or_stale",
                      "at": (at or datetime.now(ET)).replace(microsecond=0).isoformat()}
    return {k: v for k, v in out.items() if v is not None}


def market_snapshot(rows: Any) -> dict:
    """The whole board's mood AT ENTRY (2026-10-18, his "I want same as tsla" -
    the AI's missing "weather" camera): share of tickers up, average move, and
    SPY / QQQ % change. Never raises."""
    try:
        pcts = [_num(r.get("pctChange")) for r in rows or [] if isinstance(r, Mapping)]
        pcts = [v for v in pcts if v is not None]
        by = {r.get("symbol"): r for r in rows or [] if isinstance(r, Mapping)}
        return {k: v for k, v in {
            "breadth": round(sum(1 for v in pcts if v > 0) / len(pcts), 3) if pcts else None,
            "avgPct": round(sum(pcts) / len(pcts), 3) if pcts else None,
            "spyPct": _num(_m(by.get("SPY")).get("pctChange")),
            "qqqPct": _num(_m(by.get("QQQ")).get("pctChange")),
        }.items() if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def path_key(t: Mapping) -> str:
    return f"{t.get('rule')}|{t.get('symbol')}|{t.get('at')}"


def load_paths(directory: Path, day: str) -> dict[str, dict[str, float]]:
    """{trade key: {"HH:MM": mid}} for one day, {} when none."""
    try:
        doc = json.loads((Path(directory) / PATHS_DIRNAME / f"{day}.json").read_text(encoding="utf-8"))
        return {k: dict(v) for k, v in (doc.get("paths") or {}).items() if isinstance(v, dict)}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def prune(directory: Path, today: str) -> None:
    """Delete day files older than KEEP_DAYS calendar days. Never raises."""
    try:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(today) - timedelta(days=KEEP_DAYS)).isoformat()
        for f in _day_files(directory):
            if f.stem < cutoff:
                f.unlink(missing_ok=True)
        for f in (Path(directory) / PATHS_DIRNAME).glob("20*.json"):
            if f.stem < cutoff:
                f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def history(directory: Path, days: int = 5) -> list[dict]:
    """The last ``days`` day files, newest first: [{"day", "trades": [...]}] -
    the Bot page (admin only, momx_worker /api/momx-scanner/bot)."""
    out = []
    for f in reversed(_day_files(directory)[-max(1, min(int(days), KEEP_DAYS)):]):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        keys = OptionTrackBook.SUMMARY_KEYS
        out.append({"day": f.stem, "trades": [{k: t.get(k) for k in keys if t.get(k) is not None}
                                             for t in doc.get("trades") or [] if isinstance(t, dict)]})
    return out


class OptionTrackBook:
    def __init__(self, directory: Path, *, direction: str = "bull", fetch: Callable[[str], Any] | None = None,
                 healthy: Callable[[], bool] | None = None, background: bool = True,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 jev: Any = None, quote_fetch: Callable[[str], Any] | None = None,
                 quotes_fetch: Callable[[list], Any] | None = None, ai_policy: Callable | None = None) -> None:
        self.directory = Path(directory)
        self.bear = str(direction).lower() == "bear"
        self._fetch = fetch or _default_fetch
        # A test that injects its own chain fetch gets no live quote path unless it
        # passes quote_fetch too - a unit test must never reach the running app.
        self._quote_fetch = quote_fetch or (_default_quote_fetch if fetch is None else (lambda _q: None))
        self._quotes_fetch = quotes_fetch or (_default_quotes_fetch if fetch is None else (lambda _c: None))
        self._healthy = healthy or _default_healthy
        self._background = background
        self._clock = clock
        self._sleep = sleep
        self._ai_policy = ai_policy
        self._levels = LevelEvaluator()
        # Re-entrant: Jev's answer (_jev_ask's done) may arrive on the thread that holds it.
        self._lock = threading.RLock()
        self._day = ""
        self._trades: list[dict] = []          # one per (rule, symbol)
        self._paths: dict[str, dict[str, float]] = {}   # path_key -> {"HH:MM": mid} (PATHS_DIRNAME)
        self._paths_dirty = False
        self._paths_saved_at = float("-inf")
        self._opt_seen: dict[str, dict[str, float]] = {}   # board -> symbol -> first OPT (epoch s), for ⭐1
        self._carry: list[dict] | None = None   # weeklies held past their day for holdBest (CARRY_FILE)
        self._skipped: list[dict] = []          # signals NOT taken because momentum was fading
        self._capture_skips: dict[str, dict] = {}
        self._queue: queue.Queue = queue.Queue()
        self._fast_wanted: set[str] = set()
        self._fast_exit_next: dict[str, float] = {}
        self._quote_closed_day = ""
        self._quote_close_running = False
        self._quote_close_next = 0.0
        self._dirty_day = self._dirty_carry = False
        self._saved_day_at = self._saved_carry_at = 0.0
        self._fast_thread: threading.Thread | None = None
        self._urgent: queue.Queue = queue.Queue()   # symbols with a NEW (pending) trade - priced first
        self._finished: dict[str, list[dict]] = {}  # past day -> carries finished, not yet written
        self._queued: set[str] = set()
        self._urgent_set: set[str] = set()
        self._thread: threading.Thread | None = None
        # Jev shadow (momx/jev_trade.py, 2026-10-02 TEST): the scanner row + SPY row
        # seen when each trade's rule fired, asked about once the option is bought.
        # In memory only - a trade opened after a restart simply has no jevP.
        self._jev = jev
        self._jev_ctx: dict[tuple[str, str], tuple[dict, dict | None]] = {}
        self._spy_row: dict | None = None
        self._live_px: dict[str, float] = {}     # symbol -> the scanner's latest price (chain cross-check)

    # ------------------------------------------------------------------ Jev shadow
    def _jev_judge(self) -> Any:
        if self._jev is None:
            from momx.jev_trade import JevTradeJudge
            self._jev = JevTradeJudge()
        return self._jev

    def _jev_remember(self, rows: list, now_spy: dict | None) -> None:
        """Copy the row of every new pending trade (caller holds the lock)."""
        by_sym = {r.get("symbol"): r for r in rows if isinstance(r, Mapping)}
        for t in self._trades:
            key = (t.get("rule"), t.get("symbol"))
            if t.get("status") != "pending" or key in self._jev_ctx or t.get("symbol") not in by_sym:
                continue
            try:
                row = json.loads(json.dumps(by_sym[t["symbol"]], default=str))
                spy = json.loads(json.dumps(now_spy, default=str)) if now_spy else None
            except (TypeError, ValueError):
                continue
            self._jev_ctx[key] = (row, spy)

    def _jev_ask(self, t: dict) -> None:
        """The option was just bought: ask Jev in the background (caller holds the lock)."""
        ctx = self._jev_ctx.pop((t.get("rule"), t.get("symbol")), None)
        if ctx is None or t.get("jevP") is not None:
            return
        try:
            from momx.jev_trade import DESCRIBE_VERSION, describe
            side = "bear" if self.bear else "bull"
            state = describe(t, ctx[0], ctx[1], side, self._day)
            version = DESCRIBE_VERSION
            day = self._day

            def done(result: dict) -> None:
                with self._lock:
                    if self._day != day:
                        return
                    t.update({"jevP": round(float(result["jevP"]), 4), "jevAt": result.get("jevAt"), "jevV": version,
                              "jevModel": result.get("jevModel"),
                              "jevFactors": {k: result.get(k) for k in ("regime", "direction", "pressure",
                                                                         "quality", "risk", "news")}})
                    self._save()

            self._jev_judge().submit(state, side, done)
        except Exception:  # noqa: BLE001 - Jev never costs a trade
            return

    # ------------------------------------------------------------------ store
    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        if self._day and self._dirty_day:
            self._write_day()        # the old day's last changes, before switching
        if self._day:
            self._write_paths(force=True)
        self._day, self._trades, self._opt_seen, self._skipped = day, [], {}, []
        self._capture_skips = {}
        self._jev_ctx = {}
        self._paths = load_paths(self.directory, day)
        self._paths_dirty = False
        prune(self.directory, day)
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            self._trades = [t for t in doc.get("trades") or [] if isinstance(t, dict)]
            self._opt_seen = {b: dict(v) for b, v in (doc.get("optSeen") or {}).items() if isinstance(v, dict)}
            self._skipped = [x for x in doc.get("skippedFading") or [] if isinstance(x, dict)]
            self._capture_skips = {str(k): v for k, v in (doc.get("captureSkips") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self, force: bool = False) -> None:
        with self._lock:
            self._dirty_day = True
            if force or not self._background or self._clock() - self._saved_day_at >= SAVE_EVERY_SECONDS:
                self._write_day()

    def _note_path(self, t: Mapping, mark: Any, stamp: str) -> None:
        """Remember this minute's mid for the trade's main contract (caller holds the lock)."""
        v = _num(mark)
        if v is None or not stamp or len(stamp) < 16:
            return
        self._paths.setdefault(path_key(t), {})[stamp[11:16]] = round(v, 4)
        self._paths_dirty = True

    def _write_paths(self, force: bool = False) -> None:
        if not self._day or not self._paths or not self._paths_dirty:
            return
        if not force and self._clock() - self._paths_saved_at < PATHS_SAVE_EVERY_SECONDS:
            return
        self._paths_dirty, self._paths_saved_at = False, self._clock()
        path = self.directory / PATHS_DIRNAME / f"{self._day}.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"date": self._day, "direction": "bear" if self.bear else "bull",
                                       "paths": self._paths}, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _write_day(self) -> None:
        self._dirty_day, self._saved_day_at = False, self._clock()
        if not self._day:
            return
        path = self._path(self._day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"date": self._day, "direction": "bear" if self.bear else "bull",
                                       "trades": self._trades, "optSeen": self._opt_seen,
                                       "skippedFading": self._skipped, "captureSkips": self._capture_skips}, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
        self._write_paths()

    def _flush(self, due_only: bool = False) -> None:
        """Write whatever is pending (due_only: only if SAVE_EVERY_SECONDS passed)."""
        with self._lock:
            now = self._clock()
            if self._dirty_day and (not due_only or now - self._saved_day_at >= SAVE_EVERY_SECONDS):
                self._write_day()
            if self._dirty_carry and (not due_only or now - self._saved_carry_at >= SAVE_EVERY_SECONDS):
                self._write_carry()
            elif self._finished and not due_only:
                self._write_finished()

    def _bear_sector_trades(self, board: str, rows: list, local: datetime, have: set, need: set) -> None:
        """Bear sectorHot / sectorMove (caller holds the lock)."""
        falling = falling_sectors(rows)
        latched = self._opt_seen.setdefault("_bearHot:" + board, {})
        minute = local.hour * 60 + local.minute
        if 9 * 60 + 35 <= minute < 11 * 60:
            for name in falling:
                if len(latched) < 2 and name not in latched:
                    latched[name] = local.timestamp()
        by_sym = {r.get("symbol"): r for r in rows if isinstance(r, Mapping)}
        for name, leaders in falling.items():
            rule = "sectorHot" if name in latched else "sectorMove"
            for sym in leaders:
                row = by_sym.get(sym)
                if not row or (rule, sym) in have:
                    continue
                if sum(1 for k in have if k[0] == rule) >= INFO_CAP.get(rule, INFO_MAX_PER_DAY):
                    continue
                have.add((rule, sym))
                self._trades.append({"rule": rule, "symbol": sym, "board": board,
                                     "at": local.replace(microsecond=0).isoformat(),
                                     "stockPrice": _num(row.get("last")), "status": "pending",
                                     "momentum": _m(row.get("m5")).get("state"), "sector": name,
                                     "setup": setup_snapshot(row, local), "market": market_snapshot(rows)})
                need.add(sym)

    def _carry_path(self) -> Path:
        return self.directory / DIRNAME / CARRY_FILE

    def _carry_list(self) -> list[dict]:
        if self._carry is None:
            try:
                doc = json.loads(self._carry_path().read_text(encoding="utf-8"))
                self._carry = [c for c in doc.get("open") or [] if isinstance(c, dict)]
            except (OSError, ValueError, UnicodeDecodeError):
                self._carry = []
        return self._carry

    def _save_carry(self, force: bool = False) -> None:
        with self._lock:
            self._dirty_carry = True
            if force or not self._background or self._clock() - self._saved_carry_at >= SAVE_EVERY_SECONDS:
                self._write_carry()

    def _write_carry(self) -> None:
        self._dirty_carry, self._saved_carry_at = False, self._clock()
        self._write_finished()       # day files first: a carry never leaves before its holdDone lands
        path = self._carry_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"open": self._carry or []}, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _finish_carry(self, c: dict) -> None:
        """Write the final holdBest back into the trade's own day file."""
        if c["day"] == self._day:
            for t in self._trades:
                if t.get("rule") == c["rule"] and t.get("symbol") == c["symbol"]:
                    t.update(holdBest=c["holdBest"], holdBestAt=c.get("holdBestAt"), holdDone=True)
            self._save()
            return
        # Past day: queued, written by _write_finished with the next carry save.
        # One read+write of a 6 MB day file PER carry held the lock for hours on
        # Mon 2026-11-02 (8,884 Friday expiries) and froze every scanner board.
        with self._lock:
            self._finished.setdefault(c["day"], []).append(
                {"rule": c["rule"], "symbol": c["symbol"], "holdBest": c["holdBest"],
                 "holdBestAt": c.get("holdBestAt")})
            if not self._background:
                self._write_finished()

    def _write_finished(self) -> None:
        """Write queued holdDone updates: each past day file read and written once."""
        with self._lock:
            pending, self._finished = self._finished, {}
        for day, done in pending.items():
            by_key = {(d["rule"], d["symbol"]): d for d in done}
            path = self._path(day)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                for t in doc.get("trades") or []:
                    d = by_key.get((t.get("rule"), t.get("symbol")))
                    if d:
                        t.update(holdBest=d["holdBest"], holdBestAt=d.get("holdBestAt"), holdDone=True)
                tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, path)
            except (OSError, ValueError, UnicodeDecodeError):
                pass

    def trades(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._trades]

    #: What the BOT panel shows per virtual trade (2026-09-28, "we can use
    #: virtual bot?"): no OI walls, no internal timestamps - ~200 bytes a trade.
    SUMMARY_KEYS = ("rule", "symbol", "board", "at", "stockPrice", "momentum", "setup", "market", "status", "contract", "strike", "expiry",
                    "entry", "entryAt", "last", "lastAt", "best", "worst", "close", "aiDecision",
                    "exitAt", "exitReason", "exitPrice", "exitPriceAt", "closeAt", "holdBest", "holdDone",
                    "zContract", "zStrike", "zEntry", "zLast", "zBest", "zExit", "zStatus",
                    "eContract", "eStrike", "eDelta", "eEntry", "eLast", "eBest", "eExit", "eStatus",
                    "jevP", "jevV", "badQuote", "spot", "expectedMove", "em2Edge", "roi2em", "riskOk", "entryVia",
                    "entryBid", "entryAsk", "lastBid", "lastAsk", "exitBid", "exitAsk",
                    "closeBid", "closeAsk", "closeQ", "zClose", "zCloseBid", "zCloseAsk", "eClose", "eCloseBid",
                    "eCloseAsk", "zExitBid", "zExitAsk", "eExitBid", "eExitAsk", "zEntryBid", "zEntryAsk",
                    "eEntryBid", "eEntryAsk", "zLastBid", "zLastAsk", "eLastBid", "eLastAsk",
                    "levelSignal", "virtualOnly", "scannerInputs")

    def summary(self) -> list[dict]:
        with self._lock:
            return [{k: t.get(k) for k in self.SUMMARY_KEYS if t.get(k) is not None} for t in self._trades]

    def capture_status(self) -> dict:
        """Current saved session only; source views never count as separate trades."""
        with self._lock:
            have = {(t.get("rule"), t.get("symbol")) for t in self._trades}
            skipped = {(r.get("rule"), r.get("symbol")): {**r, "reason": "Fading momentum rule"} for r in self._skipped}
            skipped.update({(r.get("rule"), r.get("symbol")): r for r in self._capture_skips.values()})
            missing = [r for key, r in skipped.items() if key not in have]
            entered = sum((_num(t.get("entry")) or 0) > 0 for t in self._trades)
            pending = [t for t in self._trades if t.get("status") == "pending"]
            missing += [{"rule": t.get("rule"), "symbol": t.get("symbol"),
                         "reason": t.get("why") or _m(t.get("aiDecision")).get("reason") or t.get("status")}
                        for t in self._trades if t.get("status") in ("no_contract", "ai_skipped")]
            reasons = {}
            for r in missing:
                why = str(r["reason"])
                reasons[why] = reasons.get(why, 0)+1
            return {"day": self._day, "signals": len(self._trades)+len([k for k in skipped if k not in have]),
                    "entered": entered, "pending": len(pending), "notEntered": len(missing), "reasons": reasons,
                    "examples": missing[-10:], "levelStrategies": self._levels.status}

    def _expire_unpriced(self, local: datetime) -> bool:
        changed = False
        for t in self._trades:
            if t.get("status") != "pending":
                continue
            at = _iso(t.get("at"))
            if at and ((local-at).total_seconds() > 600 or local.hour*60+local.minute > UNTIL_MIN):
                t.update(status="no_contract", why="No usable option quote before the signal's 10-minute deadline or entry close")
                t.pop("aiCandidate", None)
                changed = True
        return changed

    # ------------------------------------------------------------------ apply
    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            minute = local.hour * 60 + local.minute
            if local.weekday() >= 5:
                return
            rows = [r for s in ("rows", "rest") for r in (_m(payload).get(s) or []) if isinstance(r, dict)]
            level_signals = self._levels.evaluate(board, _m(payload), local) if not self.bear else {}
            learning_contexts = getattr(self._levels, 'contexts', {}) if not self.bear else {}
            for row in rows:
                row.pop('scannerInputs', None)
                if row.get('symbol') in learning_contexts:
                    row['scannerInputs'] = learning_contexts[row['symbol']]
            mega = mega_caps(rows)
            spy_row = next((r for r in rows if r.get("symbol") == "SPY"), None)
            need: set[str] = set()
            with self._lock:
                self._load(local.date().isoformat())
                have = {(t["rule"], t["symbol"]) for t in self._trades}
                changed = self._expire_unpriced(local)
                if FROM_MIN <= minute <= UNTIL_MIN:
                    # "News" (2026-10-08): the scanner's NEWS ▲ / NEWS ▼ list with
                    # "Scan matches only" - momx/trade_news.news_rule_symbols.
                    from momx import trade_news
                    live = [r for r in rows if live_today(r, local)]
                    news_hits = trade_news.news_rule_symbols(live, self.bear, local)
                    for row in live:
                        sym = row.get("symbol")
                        if not isinstance(sym, str):
                            continue
                        rules = fired(row, self.bear, local)
                        level_signal = level_signals.get(sym) if not _etf_excluded(row) else None
                        if level_signal:
                            rules.extend(LEVEL_RULES[:level_signal["highestLevel"]])
                        if sym.upper() in news_hits:
                            rules.append("news")
                        if (sym in mega or sym in DAY_LINE_ETFS) and day_line_now(row, local):
                            rules.append("dayLine")
                        if day_line_now(row, local, "reclaim"):
                            rules.append("dayReclaim")
                        # his $100 + $0.50-room version, any stock (2026-09-29)
                        if day_line_now(row, local, "signal100"):
                            rules.append("dayLine100")
                        # Retired Jev picks remain historical; they create no new virtual entries.
                        for rule in rules:
                            if (rule, sym) in have:
                                continue
                            # HIS RULE, narrowed by the data (2026-09-28): momentum
                            # FADING = no MX A+ trade (back-test: fading MX A+
                            # -0.19% vs +0.54%; other rules no difference).
                            # Recorded (no option bought) so it can be checked.
                            if rule in FADING_BLOCKS and _m(row.get("m5")).get("state") == "fading":
                                if not any(x["rule"] == rule and x["symbol"] == sym for x in self._skipped):
                                    self._skipped.append({"rule": rule, "symbol": sym, "board": board,
                                                          "at": local.replace(microsecond=0).isoformat(),
                                                          "stockPrice": _num(row.get("last"))})
                                    changed = True
                                continue
                            if rule in INFO_RULES and sum(1 for k in have if k[0] == rule) >= INFO_CAP.get(rule, INFO_MAX_PER_DAY):
                                key = f"{rule}|{sym}"
                                if key not in self._capture_skips:
                                    self._capture_skips[key] = {"rule": rule, "symbol": sym, "at": local.isoformat(),
                                                                "reason": "Daily signal cap for this rule"}
                                    changed = True
                                continue
                            have.add((rule, sym))
                            self._trades.append({"rule": rule, "symbol": sym, "board": board,
                                                 "at": local.replace(microsecond=0).isoformat(),
                                                 "stockPrice": _num(row.get("last")), "status": "pending",
                                                 # 5m momentum state AT ENTRY (his ask 2026-09-28:
                                                 # "which momentum state to trade" - measured, not guessed)
                                                 "momentum": _m(row.get("m5")).get("state"),
                                                 "setup": setup_snapshot(row, local), "market": market_snapshot(rows)})
                            if row.get('scannerInputs'):
                                self._trades[-1]['scannerInputs'] = json.loads(json.dumps(row['scannerInputs']))
                            if rule in LEVEL_RULES:
                                self._trades[-1].update(levelSignal={**level_signal, "strategyLevel": LEVEL_RULES.index(rule)+1},
                                                        virtualOnly=True)
                            if rule == "jev":       # the scanner score IS this trade's Jev score
                                self._trades[-1].update(jevP=float(_m(row.get("jevRow"))["p"]), jevSource="scanner")
                            changed = True
                            need.add(sym)
                    # Star #1 on both boards (bear since 2026-09-28).
                    if True:
                        seen = self._opt_seen.setdefault(str(board), {})
                        if 9 * 60 + 30 <= minute < OPT_CUTOFF_MIN:
                            for row in live:
                                sym = row.get("symbol")
                                if (isinstance(sym, str) and sym not in seen and not _etf_excluded(row)
                                        and options_setup(row, self.bear)):
                                    seen[sym] = local.timestamp()
                                    changed = True
                        star = star_one(live, seen, local.date(), self.bear)
                        if star and ("star1", star) not in have:
                            row = next(r for r in live if r.get("symbol") == star)
                            have.add(("star1", star))
                            self._trades.append({"rule": "star1", "symbol": star, "board": board,
                                                 "at": local.replace(microsecond=0).isoformat(),
                                                 "stockPrice": _num(row.get("last")), "status": "pending",
                                                 "momentum": _m(row.get("m5")).get("state"),
                                                 "setup": setup_snapshot(row, local), "market": market_snapshot(rows)})
                            changed = True
                            need.add(star)
                if self.bear and FROM_MIN <= minute <= UNTIL_MIN:
                    self._bear_sector_trades(str(board), [r for r in rows if live_today(r, local)], local, have, need)
                    changed = True
                # HIS EXIT on the board's rows (open trades not yet exited)
                by_sym = {r.get("symbol"): r for r in rows if isinstance(r.get("symbol"), str)}
                for t in self._trades:
                    if t.get("status") != "open" or t.get("exitAt") or t["symbol"] not in by_sym:
                        continue
                    try:
                        entry_ts = datetime.fromisoformat(str(t.get("entryAt") or t.get("at"))).timestamp()
                    except ValueError:
                        continue
                    why = exit_signal(by_sym[t["symbol"]], entry_ts, self.bear)
                    if why:
                        t.update(exitAt=local.replace(microsecond=0).isoformat(), exitReason=why,
                                 exitStock=_num(_m(_m(by_sym[t["symbol"]].get("m5")).get("lastCompleted")).get("close")))
                        need.add(t["symbol"])
                        changed = True
                # weeklies carried from earlier days: re-price for holdBest
                if FROM_MIN <= minute <= STOP_MIN:
                    ts_now = self._clock()
                    for c in self._carry_list():
                        if ts_now - float(c.get("pricedAtTs") or 0) >= CARRY_REPRICE_SECONDS:
                            need.add(c["symbol"])
                # open trades due for a re-price (and pending ones still without a contract)
                if minute <= STOP_MIN:
                    ts = self._clock()
                    closing = minute >= CLOSE_MIN
                    for t in self._trades:
                        if t.get("status") == "pending" or (t.get("status") == "open" and (
                                closing or ts - float(t.get("pricedAtTs") or 0) >= REPRICE_SECONDS)):
                            need.add(t["symbol"])
                for r in rows:
                    px = _num(r.get("last"))
                    if isinstance(r.get("symbol"), str) and px:
                        self._live_px[r["symbol"]] = px
                if spy_row is not None:
                    self._spy_row = spy_row
                self._jev_remember(rows, self._spy_row)
                if changed:
                    self._save()
                # Setup-column stamp (2026-09-29, "I will see in setup column?"):
                # the day's first dayLine / dayReclaim fire on this board, from
                # the saved trades - so the tag survives the next bar and restarts.
                fires: dict = {}
                for t in self._trades:
                    if t.get("rule") in STAMP_RULES and t.get("board") == board:
                        key = STAMP_RULES[t["rule"]]
                        fires.setdefault(t["symbol"], {}).setdefault(key, t.get("at"))
                # "✓ JEV" (2026-10-02, "I need quality trades"): the first trade
                # on this board today that Jev would take (jevP >= cutoff, live only).
                jev_picks: dict = {}
                # "Jev score" column (2026-10-02): the highest take chance Jev gave
                # any of today's bot trades on this ticker, and how many it read.
                jev_best: dict = {}
                for t in self._trades:
                    if (t.get("board") != board or t.get("jevP") is None or t.get("jevSource") == "backtest"
                            or t.get("badQuote")):
                        continue
                    best = jev_best.get(t["symbol"])
                    if best is None or float(t["jevP"]) > float(best[0]["jevP"]):
                        jev_best[t["symbol"]] = (t, (best[1] if best else 0) + 1)
                    else:
                        jev_best[t["symbol"]] = (best[0], best[1] + 1)
                    if float(t["jevP"]) >= JEV_PICK_CUTOFF:
                        jev_picks.setdefault(t["symbol"], t)
                for sym, t in jev_picks.items():
                    fires.setdefault(sym, {}).setdefault("jev", t.get("entryAt") or t.get("at"))
                for row in rows:
                    got = fires.get(row.get("symbol"))
                    if got:
                        row["dayLines"] = dict(got)
                    else:
                        row.pop("dayLines", None)   # a reused row must not keep yesterday's
                    score = jev_best.get(row.get("symbol"))
                    if score:
                        row["jevScore"] = {"p": score[0]["jevP"], "rule": score[0].get("rule"), "n": score[1],
                                           "at": score[0].get("entryAt") or score[0].get("at")}
                    else:
                        row.pop("jevScore", None)
                    pick = jev_picks.get(row.get("symbol"))
                    if pick:
                        row["jevPick"] = {"rule": pick.get("rule"), "p": pick.get("jevP"), "at": pick.get("entryAt") or pick.get("at")}
                    else:
                        row.pop("jevPick", None)
            with self._lock:
                fresh = set()
                for t in self._trades:
                    if t.get("status") == "pending" and not t.get("fastTried"):
                        t["fastTried"] = True
                        fresh.add(t["symbol"])
                    elif (t.get("status") == "open" and t.get("exitAt")
                          and (t.get("exitPrice") is None or any(t.get(p + "Contract") and t.get(p + "Exit") is None for p in ("e", "z")))
                          and self._clock() >= self._fast_exit_next.get(t["symbol"], 0)):
                        fresh.add(t["symbol"])
            # One batch at 15:50, then a retry every QUOTE_CLOSE_RETRY_SECONDS for any
            # trade still without a quote close, until 16:28 (review 2026-10-10: a
            # failed first batch was never retried - the MRNA 10-09 hole again).
            if (CLOSE_MIN <= minute <= STOP_MIN + 30 and self._quote_closed_day != local.date().isoformat()
                    and not self._quote_close_running and self._clock() >= self._quote_close_next):
                self._quote_close_running = True
                self._quote_close_next = self._clock() + QUOTE_CLOSE_RETRY_SECONDS
                if self._background:
                    threading.Thread(target=self._quote_close, args=(local,), name="momx-option-close",
                                     daemon=True).start()
                else:
                    self._quote_close(local)
            if fresh:
                self._fast_open(sorted(fresh))
            if need:
                self.request(sorted(need))
            self._flush(due_only=True)
        except Exception:  # noqa: BLE001 - option tracking never costs a scan
            return

    # ------------------------------------------------------------- the close
    def _close_trade(self, t: dict, mark: float, bid, ask, stamp: str, now: float) -> None:
        """The 15:50 close of one open trade (caller holds the lock)."""
        sym = t["symbol"]
        t.update(close=mark, closeBid=bid, closeAsk=ask, closeAt=stamp, status="closed")
        if t.get("exitPrice") is None:
            t.update(exitAt=t.get("exitAt") or stamp, exitPrice=mark, exitPriceAt=stamp, exitReason="15:50 close", exitBid=bid, exitAsk=ask)
        t.update(holdBest=max(float(t.get("best") or 0), float(mark)), holdBestAt=t.get("bestAt"))
        if str(t.get("expiry") or "") > self._day:
            carry = self._carry_list()
            if not any(c["day"] == self._day and c["rule"] == t["rule"] and c["symbol"] == sym for c in carry):
                carry.append({"day": self._day, "rule": t["rule"], "symbol": sym,
                              "contract": t.get("contract"), "expiry": t.get("expiry"),
                              "entry": t.get("entry"), "holdBest": t.get("holdBest"),
                              "holdBestAt": t.get("bestAt"), "pricedAtTs": now})
                self._save_carry()
        else:
            t["holdDone"] = True

    def _quote_close(self, local: datetime) -> None:
        """At 15:50 (2026-10-10): close EVERY trade of the day from one batched live
        quote (OPTION_QUOTES_URL) - main, same-day and 0.20-delta contracts. On 10-09
        the chain queue never reached MRNA after 15:50, so no close was recorded and
        "held to the close" (+934% on its same-day 200C) was invisible. Trades the
        quote cannot price are retried each minute until 16:28 (and the chain queue
        still runs). A trade is done ("closeQ") once its main contract has a close."""
        try:
            self._quote_close_once(local)
        finally:
            self._quote_close_running = False

    def _quote_close_once(self, local: datetime) -> None:
        with self._lock:
            day = self._day
            todo = [t for t in self._trades
                    if t.get("contract") and t.get("status") in ("open", "closed") and (
                        t.get("closeQ") is None or any(t.get(p + "Contract") and t.get(p + "Close") is None
                                                      for p in ("e", "z")))]
            contracts = sorted({c for t in todo for c in (t.get("contract"), t.get("zContract"), t.get("eContract"))
                                if isinstance(c, str) and c})
        if not contracts:
            with self._lock:
                if self._day == day:
                    self._quote_closed_day = local.date().isoformat()
            return
        quotes: dict = {}
        for start in range(0, len(contracts), 150):
            try:
                got = self._quotes_fetch(contracts[start:start + 150])
            except Exception as exc:  # noqa: BLE001
                print(f"[momx-option-close] {'bear' if self.bear else 'bull'} quote batch failed: {exc!r}", flush=True)
                got = None
            quotes.update(_m(_m(got).get("quotes")))
        now = self._clock()
        stamp = local.replace(microsecond=0).isoformat()
        with self._lock:
            if self._day != day:
                return
            missing = 0
            for t in todo:
                for pre in ("e", "z"):
                    if t.get(pre + "Close") is not None:
                        continue
                    sq = _m(quotes.get(t.get(pre + "Contract")))
                    smid = _price(sq)
                    if not smid:
                        if t.get(pre + "Contract"):
                            missing += 1
                        continue
                    t.update({pre + "Close": smid, pre + "CloseBid": sq.get("bid"), pre + "CloseAsk": sq.get("ask"),
                              pre + "Last": smid, pre + "LastBid": sq.get("bid"), pre + "LastAsk": sq.get("ask")})
                    if smid > float(t.get(pre + "Best") or 0):
                        t[pre + "Best"] = smid
                    if t.get(pre + "Status") == "open":
                        t.update({pre + "Exit": smid, pre + "ExitBid": sq.get("bid"),
                                  pre + "ExitAsk": sq.get("ask"), pre + "Status": "closed"})
                q = _m(quotes.get(t["contract"]))
                mid = _price(q)
                if not mid:
                    if t.get("status") == "open" or t.get("close") is None:
                        missing += 1
                    continue
                if t.get("status") == "open":
                    t.update(last=mid, lastAt=stamp, lastBid=q.get("bid"), lastAsk=q.get("ask"), pricedAtTs=now)
                    self._note_path(t, mid, stamp)
                    if mid > float(t.get("best") or 0):
                        t.update(best=mid, bestAt=stamp)
                    if t.get("exitAt") and t.get("exitPrice") is None:
                        # The exit signal fired but was never priced: price it here and
                        # KEEP its reason (the chain path does the same).
                        t.update(exitPrice=mid, exitPriceAt=stamp, exitBid=q.get("bid"), exitAsk=q.get("ask"))
                    self._close_trade(t, mid, q.get("bid"), q.get("ask"), stamp, now)
                elif t.get("close") is None:
                    t.update(close=mid, closeBid=q.get("bid"), closeAsk=q.get("ask"), closeAt=stamp)
                t["closeQ"] = stamp
            if not missing:
                self._quote_closed_day = local.date().isoformat()
            elif quotes or todo:
                print(f"[momx-option-close] {'bear' if self.bear else 'bull'} {missing} trade(s) without a close quote - retrying",
                      flush=True)
            self._save(force=True)
            self._write_paths(force=True)       # the day's last minutes, not 5 minutes late

    # ------------------------------------------------------- entry at the signal
    def _fast_open(self, symbols: list[str]) -> None:
        """Price new trades NOW from a live quote (QUOTE_URL); whatever cannot be
        priced stays pending for the normal chain queue. Never blocks the scan."""
        with self._lock:
            self._fast_wanted.update(symbols)
            start = self._background and (self._fast_thread is None or not self._fast_thread.is_alive())
            if start:
                self._fast_thread = threading.Thread(target=self._fast_run, name="momx-option-fast", daemon=True)
        if not self._background:
            self._fast_run()
        elif start:
            self._fast_thread.start()

    def _fast_run(self) -> None:
        while True:
            with self._lock:
                batch = sorted(self._fast_wanted)[:FAST_BATCH]
                self._fast_wanted.difference_update(batch)
            if not batch:
                return
            try:
                self._fast_price(batch)
            except Exception:  # noqa: BLE001 - the normal queue still prices them
                pass

    def _approve_entry(self, trade: dict, candidate: dict, now: float) -> bool:
        """Stage a quote for scoring without recording an entry before AI approval."""
        if self._ai_policy is None:
            return True
        try:
            local = _et(now)
            if local.weekday() >= 5 or not FROM_MIN <= local.hour * 60 + local.minute <= UNTIL_MIN:
                trade.update(status="ai_skipped", aiDecision={"approved": False, "reason": "Entry window ended before AI approval"})
                trade.pop("aiCandidate", None)
                return False
            age = now - datetime.fromisoformat(trade["at"]).timestamp()
            if not 0 <= age <= 600:
                trade.update(status="ai_skipped", aiDecision={"approved": False, "reason": "Signal expired before AI approval"})
                trade.pop("aiCandidate", None)
                return False
            trade["aiCandidate"] = dict(candidate)
            result = self._ai_policy("bear" if self.bear else "bull", {**trade, **candidate}, now)
            trade["aiDecision"] = dict(result)
            if result.get("approved") is True and result.get("enabled") is True:
                trade.pop("aiCandidate", None)
                return True
            if result.get("provider"):
                trade["status"] = "ai_skipped"
                trade.pop("aiCandidate", None)
        except Exception:
            trade["aiDecision"] = {"approved": False, "reason": "AI review error; no entry"}
        return False

    def _fast_price(self, symbols: list[str]) -> None:
        self._fast_exits(symbols)
        with self._lock:
            day = self._day
            spots = {}
            for t in self._trades:
                if t["symbol"] in symbols and t.get("status") == "pending":
                    spots[t["symbol"]] = self._live_px.get(t["symbol"]) or _num(t.get("stockPrice"))
        items = [f"{sym}:{'P' if self.bear else 'C'}:{spot}" for sym, spot in spots.items() if spot]
        if not items:
            return
        reply = self._quote_fetch(",".join(items))
        now = self._clock()
        stamp = _et(now).replace(microsecond=0).isoformat()
        changed = False
        with self._lock:
            if self._day != day:
                return
            if self._expire_unpriced(_et(now)):
                self._save()
            for res in _m(reply).get("results") or []:
                res = _m(res)
                pick = _m(res.get("pick"))
                if not res.get("ok") or not pick.get("contract") or not _num(pick.get("entry")):
                    continue
                sym = res.get("symbol")
                live = self._live_px.get(sym)
                if entry_impossible(_num(pick["entry"]), _num(pick.get("strike")), live, self.bear):
                    continue
                for t in self._trades:
                    if t["symbol"] != sym or t.get("status") != "pending":
                        continue
                    candidate = {**_m(res.get("levels")), **dict(pick)}
                    candidate.update(risk_check(pick.get("spot"), candidate.get("expectedMove"), pick.get("strike"), pick["entry"], self.bear))
                    if not self._approve_entry(t, candidate, now):
                        changed = True
                        continue
                    t.update(candidate)
                    t.pop("why", None)
                    t.update(status="open", entryAt=stamp, best=pick["entry"], bestAt=stamp, worst=pick["entry"],
                             worstAt=stamp, last=pick["entry"], lastAt=stamp, pricedAtTs=now, entryVia="quote",
                             chainAt=res.get("chainAt"))
                    self._jev_ask(t)
                    e = res.get("e")
                    t.update(dict(e) if isinstance(e, Mapping) else {"eStatus": "none"})
                    z = _m(res.get("z"))
                    if z.get("contract") and _num(z.get("entry")):
                        t.update(zContract=z["contract"], zStrike=z.get("strike"), zEntry=z["entry"],
                                 zLast=z["entry"], zBest=z["entry"], zStatus="open",
                                 zEntryBid=z.get("entryBid"), zEntryAsk=z.get("entryAsk"))
                    else:
                        t["zStatus"] = "none"
                    changed = True
            if changed:
                self._save()

    def _fast_exits(self, symbols: list[str]) -> None:
        """Quote Strategy exits directly instead of waiting behind chain reprices.

        Runs on the existing background fast worker; missing quotes retry after
        30 seconds. Keep the actual response time, never backdate a late quote.
        """
        with self._lock:
            day = self._day
            todo = [t for t in self._trades if t.get("symbol") in symbols
                    and t.get("status") == "open" and t.get("exitAt")]
            contracts = sorted({t.get(p + "Contract" if p else "contract") for t in todo for p in ("", "e", "z")
                if t.get(p + "Exit" if p else "exitPrice") is None and t.get(p + "Contract" if p else "contract")})
            for t in todo:
                self._fast_exit_next[t["symbol"]] = self._clock() + 30
        for start in range(0, len(contracts), 150):
            try:
                quotes = _m(_m(self._quotes_fetch(contracts[start:start + 150])).get("quotes"))
            except Exception:
                continue  # The next scan retries; the chain queue remains available.
            stamp = _et(self._clock()).replace(microsecond=0).isoformat()
            with self._lock:
                if day != self._day:
                    return
                changed = False
                for t in todo:
                    if t.get("status") != "open":
                        continue
                    for p in ("", "e", "z"):
                        field = p + "Exit" if p else "exitPrice"
                        if t.get(field) is not None:
                            continue
                        q = _m(quotes.get(t.get(p + "Contract" if p else "contract")))
                        bid, ask = _num(q.get("bid")), _num(q.get("ask"))
                        if bid is None or ask is None or bid < 0 or ask < bid:
                            continue
                        mid = round((bid + ask) / 2, 4)
                        if p:
                            t.update({field: mid, p + "ExitBid": bid, p + "ExitAsk": ask,
                                      p + "ExitPriceAt": stamp, p + "Status": "closed"})
                        else:
                            t.update(exitPrice=mid, exitPriceAt=stamp, exitBid=bid, exitAsk=ask)
                        changed = True
                if changed:
                    self._save()

    # --------------------------------------------------------------- checker
    def request(self, symbols: list[str]) -> None:
        with self._lock:
            fresh = [s for s in symbols if s not in self._queued]
            pending = {t["symbol"] for t in self._trades if t.get("status") == "pending"
                       or (t.get("status") == "open" and t.get("exitAt") and t.get("exitPrice") is None)}
            for s in symbols:
                # a new trade jumps the re-price line (even if the symbol is
                # already waiting there - the later normal pop is skipped)
                if s in pending and s not in self._urgent_set:
                    self._urgent_set.add(s)
                    self._urgent.put(s)
            self._queued.update(fresh)
            for s in fresh:
                if s not in pending:
                    self._queue.put(s)
            start = self._background and bool(fresh) and (self._thread is None or not self._thread.is_alive())
            if start:
                self._thread = threading.Thread(target=self._run, name="momx-option-track", daemon=True)
        if not self._background:
            self.drain()
            return
        if start:
            self._thread.start()

    def _next(self, timeout: float | None) -> str | None:
        """The next symbol to price: new trades first, then re-prices."""
        try:
            sym = self._urgent.get_nowait()
            with self._lock:
                self._urgent_set.discard(sym)
            return sym
        except queue.Empty:
            pass
        while True:
            try:
                sym = self._queue.get_nowait() if timeout is None else self._queue.get(timeout=timeout)
            except queue.Empty:
                return None
            with self._lock:
                if sym in self._queued:      # not already priced via the urgent line
                    return sym

    def drain(self) -> None:
        while True:
            sym = self._next(None)
            if sym is None:
                return
            self._price_symbol(sym, waits=False)

    def _run(self) -> None:
        while True:
            sym = self._next(1.0)
            if sym is None:
                self._flush()          # queue idle: write what the last passes changed
                continue
            try:
                ok = bool(self._healthy())
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                with self._lock:
                    self._queued.discard(sym)
                self._sleep(60.0)
                continue
            self._price_symbol(sym, waits=True)
            self._sleep(FETCH_GAP)

    def _chain(self, sym: str, waits: bool) -> Any:
        for attempt in range(WARM_POLLS if waits else 1):
            try:
                chain = self._fetch(sym)
            except Exception:  # noqa: BLE001
                chain = None
            rows = _m(chain).get("selectedExpiryChainRows")
            if (isinstance(rows, list) and rows and not _m(chain).get("frontExpiryOnly") and not _m(chain).get("mobileFast")
                    and chain_fresh(chain, self._clock())):
                return chain
            if waits and attempt < WARM_POLLS - 1:
                self._sleep(WARM_POLL_SECONDS)
        return None

    def _price_symbol(self, sym: str, waits: bool) -> None:
        try:
            chain = self._chain(sym, waits)
            now = self._clock()
            local = _et(now)
            minute = local.hour * 60 + local.minute
            stamp = local.replace(microsecond=0).isoformat()
            with self._lock:
                self._queued.discard(sym)
                if self._expire_unpriced(local):
                    self._save()
                if chain is None:
                    return
                live = self._live_px.get(sym)
                spot = _num(_m(chain).get("underlyingPrice"))
                if live and spot and abs(spot / live - 1) > SPOT_MISMATCH:
                    return          # the chain's stock price is not today's: an old copy - retry later
                changed = False
                for t in self._trades:
                    if t["symbol"] != sym:
                        continue
                    if t.get("status") == "pending":
                        # His checklist (2026-09-27): "break of major level with
                        # High OI", "retest OI level below", "within expected
                        # move?" - no chain history exists to test these, so the
                        # walls and EM seen AT the signal are saved from today.
                        pick = pick_contract(chain, self.bear)
                        if pick and entry_impossible(pick["entry"], pick["strike"], live, self.bear):
                            continue        # quote not updated yet - stay pending, retried next pass
                        t.update(levels_at_signal(chain))
                        if not pick:
                            # An incomplete chain is transient: keep the signal in the
                            # priority retry queue until its original freshness deadline.
                            t["why"] = "Waiting for a usable OTM option quote; retrying within the signal deadline"
                        else:
                            candidate = dict(pick)
                            candidate.update(risk_check(pick["spot"], t.get("expectedMove"), pick["strike"], pick["entry"], self.bear))
                            if not self._approve_entry(t, candidate, now):
                                changed = True
                                continue
                            t.update(candidate)
                            t.pop("why", None)
                            t.update(status="open", entryAt=stamp, best=pick["entry"], bestAt=stamp,
                                     worst=pick["entry"], worstAt=stamp, last=pick["entry"], lastAt=stamp,
                                     pricedAtTs=now)
                            self._note_path(t, pick["entry"], stamp)
                            self._jev_ask(t)
                            e = pick_entry_strike(chain, self.bear, pick["expiry"])
                            t.update(e if e else {"eStatus": "none"})
                            z = pick_contract(chain, self.bear, zero_dte=True)
                            if z:
                                t.update(zContract=z["contract"], zStrike=z["strike"], zEntry=z["entry"],
                                         zLast=z["entry"], zBest=z["entry"], zStatus="open",
                                         zEntryBid=z.get("entryBid"), zEntryAsk=z.get("entryAsk"))
                            else:
                                t["zStatus"] = "none"
                        changed = True
                    elif t.get("status") == "open":
                        mark = contract_mark(chain, t.get("contract"))
                        if mark is None:
                            continue
                        t.update(last=mark, lastAt=stamp, pricedAtTs=now)
                        self._note_path(t, mark, stamp)
                        q_bid, q_ask = contract_quote(chain, t.get("contract"))
                        t.update(lastBid=q_bid, lastAsk=q_ask)
                        if mark > float(t.get("best") or 0):
                            t.update(best=mark, bestAt=stamp)
                        if mark < float(t.get("worst") or mark):
                            t.update(worst=mark, worstAt=stamp)
                        if t.get("exitAt") and t.get("exitPrice") is None:
                            t.update(exitPrice=mark, exitPriceAt=stamp, exitBid=q_bid, exitAsk=q_ask)
                        # The side contracts (0.20-delta "e", same-day "z") are followed
                        # for the WHOLE day (2026-10-10): exit price set once at the
                        # main exit, but best and the 15:50 close keep updating, so
                        # "Hold to close" / "Best" mean the same for all three.
                        for pre in ("e", "z"):
                            if not t.get(pre + "Contract") or t.get(pre + "Status") in (None, "none"):
                                continue
                            sm = contract_mark(chain, t.get(pre + "Contract"))
                            if sm is None:
                                continue
                            s_bid, s_ask = contract_quote(chain, t.get(pre + "Contract"))
                            t.update({pre + "Last": sm, pre + "LastBid": s_bid, pre + "LastAsk": s_ask})
                            if sm > float(t.get(pre + "Best") or 0):
                                t[pre + "Best"] = sm
                            if t.get(pre + "Status") == "open" and (t.get("exitAt") or minute >= CLOSE_MIN):
                                t.update({pre + "Exit": sm, pre + "ExitBid": s_bid, pre + "ExitAsk": s_ask,
                                          pre + "Status": "closed"})
                            if minute >= CLOSE_MIN:
                                t.update({pre + "Close": sm, pre + "CloseBid": s_bid, pre + "CloseAsk": s_ask})
                        if minute >= CLOSE_MIN:
                            self._close_trade(t, mark, q_bid, q_ask, stamp, now)
                        changed = True
                carry_changed = False
                for c in list(self._carry_list()):
                    if c["symbol"] != sym or c["day"] == self._day and minute < CLOSE_MIN:
                        continue
                    mark = contract_mark(chain, c.get("contract"))
                    c["pricedAtTs"] = now
                    if mark is not None and mark > float(c.get("holdBest") or 0):
                        c.update(holdBest=mark, holdBestAt=stamp)
                    today = local.date().isoformat()
                    if today > str(c.get("expiry")) or (today == str(c.get("expiry")) and minute >= CLOSE_MIN):
                        self._finish_carry(c)
                        self._carry.remove(c)
                    carry_changed = True
                if carry_changed:
                    self._save_carry()
                if changed:
                    self._save()
        except Exception:  # noqa: BLE001 - the checker never dies
            with self._lock:
                self._queued.discard(sym)
