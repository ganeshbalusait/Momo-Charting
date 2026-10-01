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
import os
import queue
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_option_track"
CHAIN_URL = os.environ.get("AGX_MOMX_OPTIONABLE_URL", "http://127.0.0.1:3002/api/oi-finder-chain?symbol=")
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
RULES = ("go", "goRvolMacd", "mxaplus", "daily2", "turn", "star1", "c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath")
#: INFO rules = every other tag the Setup column shows (Ganesh 2026-09-28:
#: "take all the trades what you see in the setup, want to see who is winning
#: everyday"). NOT proven - on stock prices the arrows did no better than
#: random (Sep 1-25); the others were never tested as trades. Each takes the
#: first INFO_MAX_PER_DAY a day (bull board): every trade is a chain fetch
#: every 10 min, one chain per FETCH_GAP, and chain fetches are what saturated
#: the CPU before - 6 x 15 caps the day at ~90 fetches per re-price round.
INFO_RULES = ("c2h", "call2h", "adx", "skit", "rvol", "sqz", "zs", "zs2", "sectorHot", "sectorMove", "openPath",
              "solo", "hotLead", "dayLine", "dayReclaim", "dayLine100", "mom")
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
INFO_CAP: dict[str, int] = {}
#: "zs" (2026-09-28, "Yes add it to the bot as test"): his ZS entry -
#: momentum._zs_rule on the last completed 5m bar (above EMA9/21/50 + chart
#: VWAP + Ichimoku cloud, nothing from dH / pmH / pDH / P / R1 / R2 within 2%
#: above). Back-test Sep 1-25: no edge (~0%/trade); tracked for real options.
INFO_MAX_PER_DAY = 150
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


def fired(row: Mapping, bear: bool, now: datetime) -> list[str]:
    """Which RULES this row shows right now (mirrors momxFilters.js)."""
    out: list[str] = []
    if row.get("etf"):  # ETFs never fire a rule (momx/etf_list.py, 2026-09-27 "use only stocks")
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
    if row.get("etf") or _m(row.get("grade")).get("letter") not in ("A+", "A"):
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
        if not isinstance(sym, str) or row.get("etf"):
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
        if not isinstance(row, Mapping) or row.get("etf"):
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


def _price(r: Mapping) -> float:
    bid, ask = _num(r.get("bid")) or 0.0, _num(r.get("ask")) or 0.0
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 4)
    return _num(r.get("mark")) or _num(r.get("last")) or 0.0


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
            "delta": _num(best.get("delta")), "spot": round(spot, 4), "entry": _price(best)}


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
            "eEntry": _price(best), "eLast": _price(best), "eBest": _price(best), "eStatus": "open"}


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


def contract_mark(chain: Any, symbol: str) -> float | None:
    for r in _m(chain).get("selectedExpiryChainRows") or []:
        if isinstance(r, Mapping) and r.get("symbol") == symbol:
            p = _price(r)
            return p if p > 0 else None
    return None


def _http_json(url: str, timeout: float = 40.0) -> Any:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_fetch(symbol: str) -> Any:
    return _http_json(CHAIN_URL + symbol)


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


def prune(directory: Path, today: str) -> None:
    """Delete day files older than KEEP_DAYS calendar days. Never raises."""
    try:
        from datetime import date, timedelta
        cutoff = (date.fromisoformat(today) - timedelta(days=KEEP_DAYS)).isoformat()
        for f in _day_files(directory):
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
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.directory = Path(directory)
        self.bear = str(direction).lower() == "bear"
        self._fetch = fetch or _default_fetch
        self._healthy = healthy or _default_healthy
        self._background = background
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._day = ""
        self._trades: list[dict] = []          # one per (rule, symbol)
        self._opt_seen: dict[str, dict[str, float]] = {}   # board -> symbol -> first OPT (epoch s), for ⭐1
        self._carry: list[dict] | None = None   # weeklies held past their day for holdBest (CARRY_FILE)
        self._skipped: list[dict] = []          # signals NOT taken because momentum was fading
        self._queue: queue.Queue = queue.Queue()
        self._urgent: queue.Queue = queue.Queue()   # symbols with a NEW (pending) trade - priced first
        self._queued: set[str] = set()
        self._urgent_set: set[str] = set()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ store
    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._trades, self._opt_seen, self._skipped = day, [], {}, []
        prune(self.directory, day)
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            self._trades = [t for t in doc.get("trades") or [] if isinstance(t, dict)]
            self._opt_seen = {b: dict(v) for b, v in (doc.get("optSeen") or {}).items() if isinstance(v, dict)}
            self._skipped = [x for x in doc.get("skippedFading") or [] if isinstance(x, dict)]
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"date": self._day, "direction": "bear" if self.bear else "bull",
                                       "trades": self._trades, "optSeen": self._opt_seen,
                                       "skippedFading": self._skipped}, indent=1),
                           encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

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
                                     "momentum": _m(row.get("m5")).get("state"), "sector": name})
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

    def _save_carry(self) -> None:
        path = self._carry_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({"open": self._carry or []}, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _finish_carry(self, c: dict) -> None:
        """Write the final holdBest back into the trade's own day file."""
        path = self._path(c["day"])
        if c["day"] == self._day:
            for t in self._trades:
                if t.get("rule") == c["rule"] and t.get("symbol") == c["symbol"]:
                    t.update(holdBest=c["holdBest"], holdBestAt=c.get("holdBestAt"), holdDone=True)
            self._save()
            return
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            for t in doc.get("trades") or []:
                if t.get("rule") == c["rule"] and t.get("symbol") == c["symbol"]:
                    t.update(holdBest=c["holdBest"], holdBestAt=c.get("holdBestAt"), holdDone=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def trades(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._trades]

    #: What the BOT panel shows per virtual trade (2026-09-28, "we can use
    #: virtual bot?"): no OI walls, no internal timestamps - ~200 bytes a trade.
    SUMMARY_KEYS = ("rule", "symbol", "board", "at", "stockPrice", "momentum", "status", "contract", "strike", "expiry",
                    "entry", "entryAt", "last", "lastAt", "best", "worst", "close",
                    "exitAt", "exitReason", "exitPrice", "holdBest", "holdDone",
                    "zContract", "zStrike", "zEntry", "zLast", "zBest", "zExit", "zStatus",
                    "eContract", "eStrike", "eDelta", "eEntry", "eLast", "eBest", "eExit", "eStatus")

    def summary(self) -> list[dict]:
        with self._lock:
            return [{k: t.get(k) for k in self.SUMMARY_KEYS if t.get(k) is not None} for t in self._trades]

    # ------------------------------------------------------------------ apply
    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            minute = local.hour * 60 + local.minute
            if local.weekday() >= 5:
                return
            rows = [r for s in ("rows", "rest") for r in (_m(payload).get(s) or []) if isinstance(r, dict)]
            mega = mega_caps(rows)
            need: set[str] = set()
            with self._lock:
                self._load(local.date().isoformat())
                have = {(t["rule"], t["symbol"]) for t in self._trades}
                changed = False
                if FROM_MIN <= minute <= UNTIL_MIN:
                    for row in rows:
                        sym = row.get("symbol")
                        if not isinstance(sym, str):
                            continue
                        rules = fired(row, self.bear, local)
                        if (sym in mega or sym in DAY_LINE_ETFS) and day_line_now(row, local):
                            rules.append("dayLine")
                        if day_line_now(row, local, "reclaim"):
                            rules.append("dayReclaim")
                        # his $100 + $0.50-room version, any stock (2026-09-29)
                        if day_line_now(row, local, "signal100"):
                            rules.append("dayLine100")
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
                                continue
                            have.add((rule, sym))
                            self._trades.append({"rule": rule, "symbol": sym, "board": board,
                                                 "at": local.replace(microsecond=0).isoformat(),
                                                 "stockPrice": _num(row.get("last")), "status": "pending",
                                                 # 5m momentum state AT ENTRY (his ask 2026-09-28:
                                                 # "which momentum state to trade" - measured, not guessed)
                                                 "momentum": _m(row.get("m5")).get("state")})
                            changed = True
                            need.add(sym)
                    # Star #1 on both boards (bear since 2026-09-28).
                    if True:
                        seen = self._opt_seen.setdefault(str(board), {})
                        if 9 * 60 + 30 <= minute < OPT_CUTOFF_MIN:
                            for row in rows:
                                sym = row.get("symbol")
                                if (isinstance(sym, str) and sym not in seen and not row.get("etf")
                                        and options_setup(row, self.bear)):
                                    seen[sym] = local.timestamp()
                                    changed = True
                        star = star_one(rows, seen, local.date(), self.bear)
                        if star and ("star1", star) not in have:
                            row = next(r for r in rows if r.get("symbol") == star)
                            have.add(("star1", star))
                            self._trades.append({"rule": "star1", "symbol": star, "board": board,
                                                 "at": local.replace(microsecond=0).isoformat(),
                                                 "stockPrice": _num(row.get("last")), "status": "pending",
                                                 "momentum": _m(row.get("m5")).get("state")})
                            changed = True
                            need.add(star)
                if self.bear and FROM_MIN <= minute <= UNTIL_MIN:
                    self._bear_sector_trades(str(board), rows, local, have, need)
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
                for row in rows:
                    got = fires.get(row.get("symbol"))
                    if got:
                        row["dayLines"] = dict(got)
                    else:
                        row.pop("dayLines", None)   # a reused row must not keep yesterday's
            if need:
                self.request(sorted(need))
        except Exception:  # noqa: BLE001 - option tracking never costs a scan
            return

    # --------------------------------------------------------------- checker
    def request(self, symbols: list[str]) -> None:
        with self._lock:
            fresh = [s for s in symbols if s not in self._queued]
            pending = {t["symbol"] for t in self._trades if t.get("status") == "pending"}
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
            if isinstance(rows, list) and rows and not _m(chain).get("frontExpiryOnly") and not _m(chain).get("mobileFast"):
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
                if chain is None:
                    return
                changed = False
                for t in self._trades:
                    if t["symbol"] != sym:
                        continue
                    if t.get("status") == "pending":
                        # His checklist (2026-09-27): "break of major level with
                        # High OI", "retest OI level below", "within expected
                        # move?" - no chain history exists to test these, so the
                        # walls and EM seen AT the signal are saved from today.
                        t.update(levels_at_signal(chain))
                        pick = pick_contract(chain, self.bear)
                        if not pick:
                            t["status"], t["why"] = "no_contract", "no OTM contract with a price after today"
                        else:
                            t.update(pick)
                            t.update(status="open", entryAt=stamp, best=pick["entry"], bestAt=stamp,
                                     worst=pick["entry"], worstAt=stamp, last=pick["entry"], lastAt=stamp,
                                     pricedAtTs=now)
                            e = pick_entry_strike(chain, self.bear, pick["expiry"])
                            t.update(e if e else {"eStatus": "none"})
                            z = pick_contract(chain, self.bear, zero_dte=True)
                            if z:
                                t.update(zContract=z["contract"], zStrike=z["strike"], zEntry=z["entry"],
                                         zLast=z["entry"], zBest=z["entry"], zStatus="open")
                            else:
                                t["zStatus"] = "none"
                        changed = True
                    elif t.get("status") == "open":
                        mark = contract_mark(chain, t.get("contract"))
                        if mark is None:
                            continue
                        t.update(last=mark, lastAt=stamp, pricedAtTs=now)
                        if mark > float(t.get("best") or 0):
                            t.update(best=mark, bestAt=stamp)
                        if mark < float(t.get("worst") or mark):
                            t.update(worst=mark, worstAt=stamp)
                        if t.get("exitAt") and t.get("exitPrice") is None:
                            t.update(exitPrice=mark, exitPriceAt=stamp)
                        if t.get("eStatus") == "open":
                            em = contract_mark(chain, t.get("eContract"))
                            if em is not None:
                                t["eLast"] = em
                                if em > float(t.get("eBest") or 0):
                                    t["eBest"] = em
                                if t.get("exitAt") or minute >= CLOSE_MIN:
                                    t.update(eExit=em, eStatus="closed")
                        if t.get("zStatus") == "open":
                            zm = contract_mark(chain, t.get("zContract"))
                            if zm is not None:
                                t["zLast"] = zm
                                if zm > float(t.get("zBest") or 0):
                                    t["zBest"] = zm
                                if t.get("exitAt") or minute >= CLOSE_MIN:
                                    t.update(zExit=zm, zStatus="closed")
                        if minute >= CLOSE_MIN:
                            t.update(close=mark, closeAt=stamp, status="closed")
                            if t.get("exitPrice") is None:
                                t.update(exitPrice=mark, exitPriceAt=stamp, exitReason="15:50 close")
                            t.update(holdBest=t.get("best"), holdBestAt=t.get("bestAt"))
                            if str(t.get("expiry") or "") > self._day:
                                carry = self._carry_list()
                                if not any(c["day"] == self._day and c["rule"] == t["rule"] and c["symbol"] == sym
                                           for c in carry):
                                    carry.append({"day": self._day, "rule": t["rule"], "symbol": sym,
                                                  "contract": t.get("contract"), "expiry": t.get("expiry"),
                                                  "entry": t.get("entry"), "holdBest": t.get("best"),
                                                  "holdBestAt": t.get("bestAt"), "pricedAtTs": now})
                                    self._save_carry()
                            else:
                                t["holdDone"] = True
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
