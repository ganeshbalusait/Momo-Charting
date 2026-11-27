"""Alpaca PAPER mirror of the virtual Bot (2026-10-16, his "Do A and B" -> "B" -> "Go";
2026-10-17: paper3 = All trades, paper5 = Jev BOT >= 10%).

Reads the Bot's virtual trades (momx/option_track.py) and places PAPER orders
for the Jev BOT subset on one Alpaca paper account, so the Bot page can show
real paper fills next to the mid-price estimates. It never changes the Bot.

Safety:
* PAPER ONLY - refuses to run unless the account's credentials say paper.
* Off switch: env AGX_MOMX_PAPER_MIRROR=0 or the file artifacts/paper_mirror_off.
* Since 2026-11-13 (his decision 2026-11-12, "use that entry the position and
  target 1,2,3 ... and stop loss"): the chart's Project ROI plan (momx/chart_plan.py,
  see EXIT_PLAN). Buy: limit at the ASK, in the buy windows (from 9:30 ET; his
  2026-11-13 "Remove 10 am rule on bot also if you see the setup then trade it").
  A Level signal with no chart plan is never bought. Stop: the STOCK
  price where the option is down 50% (the chart's formula), sold the tick the
  stock trades through it. Targets: T1 / T2 / T3 from the chart, one lot each.
  Orders opened before the switch keep the older plan (PARTIAL_AT) until closed.
* A buy not filled in BUY_TTL_SECONDS is cancelled and re-sent at the ask.
  Target, Bot-exit and 15:52 sells are midpoint limits; STOP sells are limits at
  the bid. No market orders; unfilled exits stay visible and may stay open past
  FINAL_EXIT.
* Caps: MAX_OPEN positions, MAX_ENTRIES_PER_DAY, one open position per
  ticker; no new entries after 15:30.
* Controls (2026-11-09, momx/paper_control.py): a per-account Pause and a
  daily loss stop both hold NEW buys only; exits keep running. A buy the
  broker has no record of is closed as missed instead of holding a slot.
"""

from __future__ import annotations

import hashlib
import copy
import json
import math
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from momx import chart_check, chart_plan, fill_chance, paper_control

ET = ZoneInfo("America/New_York")
#: His split (2026-10-17): paper3 trades the Bot's "All trades" book (bull
#: and bear), paper5 the "Jev BOT" book (Jev >= 10%). account -> (label, jev cut
#: or None = every trade). Each account has its own caps and day file.
#: 2026-11-09 (his "use only 3 strategies trades and 0.2 delta from tomorrow"):
#: both accounts trade ONLY the three Level strategies, on the 0.20-delta
#: contract. AI never blocks a buy (his "Don't block any trade using AI").
#: 2026-11-10 (his "Use Agentic AI ... plug and play ... see which strategy works
#: good then we will use same for real money"): the two accounts trade the same
#: signals and differ ONLY in which waiting signal gets a free slot - paper3 the
#: newest (rules bot), paper5 the one an AI ranks best (momx/paper_picker.py).
ACCOUNTS = {"paper3": ("Rules bot - Level 1 / 2 / 3 at 0.20 delta, newest signal first", None),
            "paper5": ("AI bot - Level 1 / 2 / 3 at 0.20 delta, AI picks which signal gets the slot", None)}
#: An AI ranking older than this is not used: the rules bot's order applies.
RANK_FRESH_SECONDS = 120.0
#: paper3's rules - HIS pick (2026-10-17, "For paper trade 3, bull/bear take
#: rule from the screenshot 1-11,13,14,15" of the Bot leaderboard): the same
#: rules for BULL. Not 12 (MOM↑) and nothing below 15.
_PAPER3 = frozenset({"star1", "zs2", "openPath", "sectorMove", "call2h", "news", "explosive", "skit", "zs",
                     "dayLine100", "momStrong", "rvol", "c2h"})
#: Bear side - his pick from the BEAR leaderboard, rows 1-17 (2026-10-17).
_PAPER3_BEAR = frozenset({"openPath", "call2h", "dayReclaim", "skit", "go", "sectorMove", "mxaplus", "momStrong",
                          "turn", "rvol", "goRvolMacd", "mom", "sqz", "star1", "news", "explosive"})
PICKED_RULES = {"bull": _PAPER3, "bear": _PAPER3_BEAR}
#: paper5 bull - his pick from the Jev BOT (>= 10%) BULL leaderboard, 50+ trades,
#: rows 1-10 (2026-10-17).
PAPER5_RULES = {"bull": frozenset({"rvol", "call2h", "momStrong", "openPath", "mom", "zs2", "sqz",
                                   "adx", "skit"}),
                #: Bear - his pick from the Jev BOT (>= 10%) BEAR leaderboard, 50+ trades, rows 1-13.
                "bear": frozenset({"openPath", "call2h", "zs", "skit", "adx", "rvol", "zs2", "sectorMove",
                                   "mom", "explosive", "go", "momStrong"})}
#: The three Level strategies (momx/bot_levels.py). The levels stack: a Level 3
#: signal is also a Level 2 and a Level 1 signal, and on the real 11-06 / 11-09
#: files NO signal was Level 1 only. So one signal = ONE paper order, stored
#: under its highest level, with row["levels"] naming every level it qualified
#: for. The Scorecard and the loss study count that one trade under each of
#: those levels (Level 1 = every Level signal; Level 3 = only the strongest);
#: the account's own totals count it once.
LEVEL_RULES = ("level1", "level2", "level3")
_LEVELS = frozenset(LEVEL_RULES)
LEVEL_ONLY = {"bull": _LEVELS, "bear": _LEVELS}
ACCOUNT_RULES = {"paper3": LEVEL_ONLY, "paper5": LEVEL_ONLY}
#: Which of the Bot's recorded contracts the paper accounts buy: "entry" = the
#: OTM contract just under 0.20 delta on next week's expiry (option_track's
#: eContract); "main" = the nearest OTM strike (the old behaviour).
PAPER_LEG = "entry"
_LEG_FIELDS = {"contract": "eContract", "lastBid": "eLastBid", "lastAsk": "eLastAsk", "exitBid": "eExitBid",
               "exitAsk": "eExitAsk", "entry": "eEntry", "strike": "eStrike", "delta": "eDelta"}


def leg_get(t: Mapping, field: str, leg: str = "main") -> Any:
    """One field of the chosen contract on a Bot trade (the 0.20-delta leg's
    fields carry an "e" prefix)."""
    return t.get(_LEG_FIELDS[field]) if leg == "entry" else t.get(field)
ACCOUNT = "paper3"
JEV_CUT = 0.10
FRESH_SECONDS = 600.0
QUOTE_FRESH_SECONDS = 120.0
#: His sizing (2026-10-17: "Limit max $200-$300", "Use my plan", "Min 3 cons",
#: "Less than $300 then 3 cons, greater than $400 to $800 take only one cons"):
#: 2026-11-10 (his rule, "took 3 cons, 2 cons exit at 100% ... 1 cons move to
#: BE"): EVERY buy up to MAX_PRICE is at least MIN_MULTI = 3 contracts, so the
#: sell-2 / keep-1 split always works; more when they fit in BUDGET (0.25 -> 12,
#: 0.50 -> 6, 1.00 -> 3, 5.00 -> 3, 8.00 -> 3); over $8.00 -> skip. (Before this,
#: $4.01-$8.00 bought 1 contract, which could never be split.)
BUDGET = 300.0
MIN_MULTI = 3
MAX_PRICE = 8.00
#: His exit plan: at +100% sell 20% (when holding 2+) and move the stop to
#: break-even; at +200% the stop goes to +100%, +300% -> +200%, ... The Bot's
#: own exit (or 15:52) still sells whatever is left.
#: 2026-11-10, how he trades by hand: "took 3 cons, 2 cons exit at 100% or high
#: oi level. 1 cons move to BE. Went 200% move 1 cons to 100%" and "Exit -50% or
#: below 5 mins candle 21 ema". So two thirds come off at +100% OR when the
#: stock reaches the high-OI wall that was ahead of it at the signal (his
#: target); the rest gets a break-even stop and the +100% ladder; a position
#: down 50% is sold. (Before this: 20% off at +100%, no downside stop.)
PARTIAL_AT = 1.00
PARTIAL_SHARE = 2 / 3
PARTIAL_WHY = "+100%: sell 2 of 3"
TARGET_WHY = "High OI level reached: sell 2 of 3"
STOP_LOSS = 0.50
STOP_LOSS_WHY = "stop loss -50%"
#: The -50% must show on this many ticks in a row (20 s) before it sells, so
#: one bad quote on a thin option cannot stop a position out.
STOP_LOSS_TICKS = 2
#: The stock price the Bot last saw must be this fresh to count as "reached the level".
STOCK_FRESH_SECONDS = 180.0
#: An option this cheap, or with nobody bidding, cannot be sold back at the mid:
#: 42 of the 176 Level contracts on 2026-11-09 had a $0.00 bid. Not bought.
MIN_PRICE = 0.05
#: No limit on open positions in practice (his "No limit for open", 2026-11-10; it was 8).
#: Every Level signal that passes the price / bid checks gets an order. The number left is a runaway guard.
MAX_OPEN = 500
#: No daily count limit in practice (his "Trade as much as you can", 2026-11-10; it was 25).
#: The number left is only a runaway guard.
MAX_ENTRIES_PER_DAY = 500
BUY_TTL_SECONDS = 120.0
SELL_TTL_SECONDS = 60.0
TICK_SECONDS = 10.0
FINAL_EXIT = (15, 52)
#: When NEW paper buys may go in (2026-10-17, his "first 90 minutes ... and 2nd
#: half (1:00pm est)" -> "Yes do it"): the 11:00-13:00 midday chop is skipped.
#: 2026-11-12: the chart-plan build first started the buys at 10:00. 2026-11-13, his
#: decision the next morning ("Remove 10 am rule on bot also if you see the setup then
#: trade it"; "Start trading 9:30 if you see the setup or wait 5 mins"): back to
#: 9:30-11:00 and 13:00-15:30 ET, with the top-of-board rule as before (no 5-minute
#: hold 9:30-9:45, the hold from 9:45: OPEN_NO_HOLD_UNTIL). Exits run all day.
#: 2026-11-25, his words: "Bot will start at 9:35 to 11:30 between take the trades and 1 pm to
#: 3:30 pm est take the trade": the morning window is now 9:35-11:30 (was 9:30-11:00).
ENTRY_WINDOWS = ((9, 35, 11, 30), (13, 0, 15, 30))


def entry_open(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return any(h1 * 60 + m1 <= m < h2 * 60 + m2 for h1, m1, h2, m2 in ENTRY_WINDOWS)


def has_plan(t: Mapping, leg: str = "main") -> bool:
    """True when the Bot gave this signal a chart plan for the contract the paper account
    buys (option_track.plan_open: the stock stop and T1 / T2 / T3). ONE rule for "may this
    Level signal be bought" and "does it get a chart plan": a paper order never goes out
    without one (PaperMirror._entries). The same test as PaperMirror._plan_attach."""
    plan = t.get("plan") if isinstance(t, Mapping) else None
    if not isinstance(plan, Mapping) or _num(plan.get("strike")) is None:
        return False
    strike = _num(leg_get(t, "strike", leg))
    return strike is None or float(plan["strike"]) == strike


#: THE CHART PLAN (his decision 2026-11-12: "BOT we can do the entry and exit and
#: profit target, we have in chart 'entry, exit and target' use that entry the
#: position and target 1,2,3 ... and stop loss"; "Do the paper trades and all
#: trades also changes"). Every order sent by this code carries
#: row["exitPlan"] == EXIT_PLAN and is managed by PaperMirror._manage_plan:
#:   entry    limit at the option's ASK when the order goes out;
#:   stop     the STOCK price where the option is down 50% from the fill
#:            (momx/chart_plan.py, the chart's projectRoiStopStock), checked on
#:            the live stock price every tick and sold in full the first tick
#:            the stock trades through it;
#:   backstop the option itself down 50% on a quote under STOP_QUOTE_SECONDS
#:            old: sold in full on the first such tick;
#:   targets  T1 (half the expected move), T2 (the full move), T3 (the chart's
#:            open-interest strike past the move): one lot at each
#:            (chart_plan.split_contracts). At T1 the stop moves to break-even
#:            (the stock price at entry), at T2 to T1's stock price;
#:   also     the Bot's exit (5m close past the 21 EMA) and the 15:52 close.
#: A row WITHOUT the marker was opened under the older plan (+100% / high-OI
#: level / stop ladder) and keeps it until it is closed.
EXIT_PLAN = "chart"
#: The file in each account's folder that says the running paper bot trades EXIT_PLAN
#: (PaperMirror._plan_mark; read by momx/bot_desk.plan_running).
PLAN_MARK_FILE = "plan.json"
#: The option's own -50% only counts on a quote this fresh.
STOP_QUOTE_SECONDS = 15.0
#: A live stock quote older than this is not used for the stop or the targets.
PLAN_STOCK_SECONDS = 30.0
#: The Bot exit's reasons in his words (the chart-plan rows' sell reason).
BOT_EXIT_WORDS = {"below EMA21": "5-minute candle closed below the 21 EMA",
                  "above EMA21": "5-minute candle closed above the 21 EMA",
                  "15:50 close": "15:50 close"}
BACKSTOP_WHY = "Stop: option down 50% (backstop)"
#: A sale the broker REJECTS is not sent again straight away (2026-11-13 review: a
#: rejected sale was re-sent as a new order every 10-second tick, about 2,000 a day
#: per position, with no note). It is tried again once per SELL_TTL_SECONDS; after
#: this many refusals in a row, once per SELL_REJECT_SLOW_SECONDS. The row stays
#: "exit_due" with the note "The broker refused this sale ..." (shown on the Paper
#: page and in the Scorecard's All trades). A fill or a normal cancel clears the count.
SELL_REJECTS_MAX = 5
SELL_REJECT_SLOW_SECONDS = 1800.0
SELL_REFUSED_NOTE = "The broker refused this sale"
#: A SALE the broker says it has no record of ("order not found"). If the broker ANSWERED when
#: it was sent (a 403 / 422 / 429 / 5xx: Alpaca refuses most orders this way, so no order ever
#: exists) it is a refusal at once and takes the back-off above. If nothing came back (a network
#: timeout) the order may still appear, so it is looked up by its id for this long first, then
#: sent again under a new id. Before 2026-11-13 such a sale stayed "selling" for good: no stop,
#: no targets, no 15:52 close (CLS sat that way from 11-10 12:24, 89 tries).
SELL_LOST_SECONDS = 15.0
SELL_LOST_NOTE = "The broker has no record of this sale: sending it again"
LIVE = ("buying", "open", "selling", "exit_due")

# Order rules v2 (2026-11-04/05). The BOT truth check found paper filled only
# 1 in 3 mid orders and missed the BOT's winners: orders went out ~4 min late
# at a stale mid, sat 2 min, were cancelled and never re-priced. His rule
# (2026-11-05): "dont think about spready just take the trade, if setup is
# good" / "dont miss any trade" - replaces his 2026-10-16 "use only mid order".
# So: first try at the live mid; unfilled after BUY_TTL_SECONDS, re-sent at the
# live ASK (a marketable limit) so it fills. No spread filter on entries.
EXEC_VERSION = 2
#: An unfilled mid buy is re-sent at the live ask up to this many times.
BUY_REPRICES = 2
#: How often the paper account's real balance is read from Alpaca (his "scoreboard should show
#: alpaca buying power", 2026-11-10: the Scorecard said $10,000 while Paper 3 held under $1,000).
ACCOUNT_SECONDS = 60.0
#: An account read older than this is "unknown": the buying-power check then
#: never blocks a buy (a failed or stale read must not stop the bot).
BROKER_FRESH_SECONDS = 3 * ACCOUNT_SECONDS
#: A buy the broker says it has no record of ("order not found") this long
#: after it was sent never reached the broker: it becomes "missed" instead of
#: holding one of the MAX_OPEN slots for days (five did on 2026-11-04/05).
ORPHAN_SECONDS = 300.0
#: The day's P/L must be past the daily loss stop on this many ticks in a row
#: before the stop latches, so one bad quote cannot end the day's buying.
STOP_TICKS = 3
#: Signals the chart check turned down today, kept in the day file so the
#: Scorecard can say why no order went out (his rule 2026-11-10). Bounded: the
#: oldest is dropped past this many.
CHART_SKIPS_MAX = 600


def spread_share(bid: Any, ask: Any) -> float | None:
    """(ask - bid) / mid, or None for an unusable quote."""
    b, a = _num(bid), _num(ask)
    if b is None or a is None or a <= 0 or b < 0 or a < b:
        return None
    return (a - b) / ((a + b) / 2)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def _mid(bid: Any, ask: Any) -> float | None:
    b, a = _num(bid), _num(ask)
    if b is None or a is None or a <= 0 or b < 0 or a < b:
        return None
    return round((a + b) / 2, 2)


def _ask(bid: Any, ask: Any) -> float | None:
    """The ask of a usable quote, to the cent: what a buy at the chart plan's entry pays."""
    if _mid(bid, ask) is None:
        return None
    return round(float(ask), 2)


def trade_key(side: str, t: Mapping) -> str:
    return f"{side}|{t.get('rule')}|{t.get('symbol')}|{t.get('at')}"


def eligible(t: Mapping, now_ts: float, jev_cut: float | None = JEV_CUT, leg: str = "main") -> bool:
    """A Bot trade the mirror may still buy: open, fresh, with the wanted
    contract on it, and (when jev_cut is set - the Jev BOT book) Jev's score
    at or above the cut. The Level strategies are flagged virtualOnly by the
    Bot; since 2026-11-09 they are exactly what the paper accounts trade."""
    rule = str(t.get("rule") or "").lower()
    if rule == "jev" or (t.get("virtualOnly") and rule not in _LEVELS) or not still_open(t, leg):
        return False
    if jev_cut is not None:
        jev = _num(t.get("jevP"))
        if jev is None or jev < jev_cut:
            return False
    opened = _ts(t.get("entryAt") or t.get("at"))
    return opened is not None and 0 <= now_ts - opened <= FRESH_SECONDS


def _newest_first(kv) -> tuple:
    """Sort key for the rules order: by signal time, then the higher Level (use with reverse=True)."""
    rule = str(kv[1][1].get("rule") or "")
    return (str(kv[1][1].get("at") or ""), LEVEL_RULES.index(rule) if rule in _LEVELS else -1)


def still_open(t: Mapping, leg: str = "main") -> bool:
    """The Bot still holds this trade on a good quote and the wanted contract
    exists. No age test: an order already sent stays worth filling for as long
    as the Bot itself is in the trade."""
    if t.get("badQuote") or t.get("status") not in (None, "open"):
        return False
    return t.get("exitPrice") is None and bool(leg_get(t, "contract", leg))


def sellable(bid: Any, ask: Any) -> bool:
    """A quote worth buying on: somebody is bidding and the mid is at least MIN_PRICE."""
    mid = _mid(bid, ask)
    return mid is not None and mid >= MIN_PRICE and (_num(bid) or 0) > 0


def entry_limit(t: Mapping, now_ts: float, leg: str = "main") -> float | None:
    """The mid to buy at, from the Bot's own live quote; None when stale."""
    q = bot_quote(t, now_ts, leg)
    return _mid(*q) if q else None


def bot_quote(t: Mapping, now_ts: float, leg: str = "main") -> tuple[Any, Any] | None:
    """The Bot's own last (bid, ask) for the chosen contract while fresh; None
    when stale or missing. Both contracts are re-quoted in the same pass, so
    lastAt times them both."""
    seen = _ts(t.get("lastAt"))
    if seen is None or not 0 <= now_ts - seen <= QUOTE_FRESH_SECONDS:
        return None
    bid, ask = leg_get(t, "lastBid", leg), leg_get(t, "lastAsk", leg)
    if _mid(bid, ask) is None:
        return None
    return bid, ask


def _stock_price(q: Any) -> float | None:
    """The stock's price from one live quote: the last trade, else the bid/ask middle."""
    if not isinstance(q, Mapping):
        return None
    last = _num(q.get("last"))
    if last is not None and last > 0:
        return last
    return _mid(q.get("bid"), q.get("ask"))


def size_for(price: float | None, budget: float | None = None) -> int | None:
    """Contracts to buy at this option price, per his plan; None = skip.
    `budget`: the per-trade money of a run he started with a reset (momx/bot_accounts.py);
    None = BUDGET, as always."""
    if price is None or price <= 0 or price > MAX_PRICE:
        return None
    return max(MIN_MULTI, int((BUDGET if budget is None else budget) // (price * 100)))


#: top_verdict's answers: buy may go on / not yet (checked again on later ticks while the
#: signal is fresh) / no (a rules skip for the day).
TOP_OK, TOP_WAIT, TOP_FAIL = "ok", "wait", "fail"
#: HIS RULE (2026-11-10, "Start trading 9:30", "Don't start 9:45"): from 9:30 to 9:45 ET a Level
#: signal needs only its ticker in the top N on the current scan; the 5-minute hold applies from
#: 9:45 on. (hour, minute) ET.
OPEN_NO_HOLD_UNTIL = (9, 45)


def top_verdict(t: Mapping, top: Mapping | None, now: datetime | None,
                warming: bool = False) -> tuple[str, str | None]:
    """His top-of-board rule (2026-11-10, "see scanner top 5 same ticker is repeating top 5
    then try to trade", "Mag7 top 3", "use H/L column top"), in ONE place: may this Level signal
    be bought at `now`, with its ticker standing at `top` on its board ({board, n, rank,
    heldSeconds, hl} - option_track.OptionTrackBook.top_now at buy time, or the record's own
    "topHL" stamp with the record's time; None = unknown)?

    - Other rules and boards outside option_track.TOP_N: TOP_OK.
    - 9:30-9:45 ET (OPEN_NO_HOLD_UNTIL): rank <= n on the current scan is enough.
    - From 9:45: rank <= n AND held for TOP_HOLD_SECONDS; a shorter hold is TOP_WAIT.
    - Rank over n, or not a scan match: TOP_FAIL. A scan match with no H/L on the scan
      ("hlMissing", a candle gap): rank unknown, TOP_WAIT (retried, never final).
    - Unknown: never blocks before 9:45; from 9:45 it is TOP_WAIT while `warming` (the book has
      just started and has not seen the board yet), else TOP_OK (missing data never blocks).
    Returns (state, why-not)."""
    if t.get("rule") not in _LEVELS:
        return TOP_OK, None
    from momx.option_track import TOP_HOLD_SECONDS, TOP_N
    stamp = t.get("topHL") if isinstance(t.get("topHL"), Mapping) else {}
    board = str((top or {}).get("board") or t.get("board") or stamp.get("board") or "")
    n = TOP_N.get(board)
    if not n:
        return TOP_OK, None
    local = (now or datetime.now(ET)).astimezone(ET)
    hold = (local.hour, local.minute) >= OPEN_NO_HOLD_UNTIL
    hold_min = TOP_HOLD_SECONDS / 60
    where = "the Watchlist" if board == "Watchlist" else f"the {board} board"
    need = (f"needs top {n} for {hold_min:g} min from 9:45" if hold
            else f"needs top {n}; no {hold_min:g}-min hold before 9:45")
    sym = t.get("symbol")
    if not isinstance(top, Mapping):
        if hold and warming:
            return TOP_WAIT, (f"{sym}: the bot just started and has not seen {where} scan yet "
                              f"(waits up to {hold_min:g} min; {need})")
        return TOP_OK, None
    rank, held = top.get("rank"), _num(top.get("heldSeconds")) or 0.0
    if not isinstance(rank, int) or isinstance(rank, bool):
        if top.get("hlMissing"):
            # A scan match with no numeric H/L on that scan (a candle gap): its rank is unknown,
            # not bad - not in the top (its run broke), but checked again on the next scan.
            return TOP_WAIT, f"{sym} had no H/L on the last scan of {where} (checked again on the next scan; {need})"
        return TOP_FAIL, f"{sym} was not ranked on {where} by H/L ({need})"
    if rank > n:
        return TOP_FAIL, f"{sym} was #{rank} on {where} by H/L ({need})"
    if hold and held < TOP_HOLD_SECONDS:
        so_far = f"{int(held // 60)} min" if held >= 60 else f"{int(held)} s"
        return TOP_WAIT, f"{sym} was #{rank} on {where} by H/L for only {so_far} of the {hold_min:g} ({need})"
    return TOP_OK, None


def stamp_verdict(t: Mapping) -> tuple[str, str | None]:
    """top_verdict on the record's own "topHL" stamp at the record's time (history: where the
    ticker stood when the signal fired). No stamp (older records) passes."""
    stamp = t.get("topHL")
    if not isinstance(stamp, Mapping):
        return TOP_OK, None
    at = _ts(t.get("at"))
    return top_verdict(t, stamp, datetime.fromtimestamp(at, ET) if at is not None else None)


def _live_level_tickers(rows) -> set:
    """(side, symbol) of today's Level orders that are still LIVE (being bought, held or sold)."""
    return {(r.get("side"), r.get("symbol")) for r in rows
            if r.get("rule") in _LEVELS and not r.get("carried") and r.get("status") in LIVE}


def _levels_for(side: str, t: Mapping, seen_today, top) -> list | None:
    """Every level one Level order counts for: all levels up to the signal's highest one - or,
    for an upgrade while the ticker's earlier Level order is still live (seen_today), just its
    own level. None when it is not a Level signal or the highest level is unknown."""
    if t.get("rule") not in _LEVELS or not isinstance(top, int) or not 1 <= top <= len(LEVEL_RULES):
        return None
    return [t.get("rule")] if (side, t.get("symbol")) in seen_today else list(LEVEL_RULES[:top])


def oi_target(side: str, t: Mapping) -> float | None:
    """His target: the nearest high open-interest wall AHEAD of the stock at
    the signal - a call wall above for calls, a put wall below for puts."""
    spot = _num(t.get("spot")) or _num(t.get("stockPrice"))
    if not spot:
        return None
    bear = side == "bear"
    ahead = []
    for w in (t.get("putWalls" if bear else "callWalls") or []):
        strike = _num(w.get("strike")) if isinstance(w, Mapping) else None
        if strike and (strike < spot if bear else strike > spot):
            ahead.append(strike)
    return (max(ahead) if bear else min(ahead)) if ahead else None


def partial_qty(qty: int, held: int) -> int:
    """How many to take off at the first target: two thirds of what was bought
    (3 -> 2, 5 -> 3, 12 -> 8), always leaving at least one to run."""
    return max(0, min(max(1, round(qty * PARTIAL_SHARE)), held - 1))


def stop_for(buy: float, peak_gain: float) -> float | None:
    """His ladder: peak +100% -> stop at break-even, +200% -> +100%, +300% -> +200% ..."""
    if peak_gain < 1.0:
        return None
    return round(buy * int(peak_gain + 1e-9), 2)


def _client_for(account: str):
    from data.alpaca_client import AlpacaClient

    return AlpacaClient(mode="paper", profile_id=account)


def _off() -> bool:
    if os.environ.get("AGX_MOMX_PAPER_MIRROR", "1").strip() == "0":
        return True
    try:
        from config import ARTIFACTS_DIR
        return (Path(ARTIFACTS_DIR) / "paper_mirror_off").exists()
    except Exception:  # noqa: BLE001
        return False


class PaperMirror:
    def __init__(self, books: Mapping[str, Any], directory: Path,
                 client_factory: Callable[[], Any] | None = None,
                 off: Callable[[], bool] = _off, account: str = ACCOUNT,
                 book: str = "All trades", jev_cut: float | None = JEV_CUT,
                 rules: Mapping[str, frozenset] | None = None,
                 quotes_fetch: Callable[[list], Any] | None = None,
                 ai_policy: Callable | None = None, leg: str = "main",
                 run_cap: Callable[[str, Path, str], Any] | None = None) -> None:
        self.books = dict(books)
        #: momx/bot_accounts.paper_cap: None unless he reset this account with starting money;
        #: then the run's per-trade money and its free money (_entries). Never touches an exit.
        self._run_cap = run_cap
        #: Which recorded contract this account buys ("main" or "entry" = 0.20 delta).
        self.leg = leg
        self.directory = Path(directory)
        self.account, self.book, self.jev_cut, self.rules = account, book, jev_cut, rules
        self._client_factory = client_factory or (lambda: _client_for(account))
        self._off = off
        if quotes_fetch is None and client_factory is None:
            from momx.option_track import _default_quotes_fetch
            quotes_fetch = _default_quotes_fetch
        self._quotes_fetch = quotes_fetch
        self._ai_policy = ai_policy
        self._ai_status = {"enabled": False, "reason": "AI paper filter not started"}
        self._client = None
        self._lock = threading.Lock()
        self._day = ""
        self._rows: dict[str, dict] = {}
        self._error = ""
        self._expiry_retry = {}
        self._refused = ""
        #: Why new buys are held right now ("" = buying allowed).
        self._hold_buys = ""
        #: The AI's latest order for the signals waiting for a slot (momx/paper_picker.py),
        #: {"at", "order": [keys], "why": {key: text}, "source", "of"}; None = rules order.
        self._ai_rank: dict | None = None
        self.picker = None
        #: {"at", "pnl", "limit"} once today's daily loss stop has latched.
        self._daily_stop: dict | None = None
        self._stop_ticks = 0
        #: The paper account at Alpaca: {"at", "cash", "optionsBuyingPower", "buyingPower", "equity", "lastEquity"}.
        self._broker: dict | None = None
        self._broker_ts = 0.0
        #: Today's signals the chart check turned down: {key: {symbol, side, rule, at, why, checkedAt}}.
        self._chart_skips: dict[str, dict] = {}
        #: Today's signals not bought because the paper account lacked the buying power, same shape.
        self._fund_skips: dict[str, dict] = {}
        #: Today's Level signals not bought because the ticker was not in its board's top N by
        #: H/L for 5 min (his top-of-board rule, top_verdict), same shape.
        self._top_skips: dict[str, dict] = {}
        #: In memory only: for each fund skip, how many of today's orders were closed or missed
        #: when it was last checked (an order closing since frees money: check again).
        self._fund_seen: dict[str, int] = {}

    def _chart_verdict(self, side: str, t: Mapping) -> tuple[str, str | None]:
        """His step 3 (his rule 2026-11-10): the 5m chart check on the signal's
        frozen chart. Level signals only; anything else is "not checked".
        Never raises: a broken snapshot passes as "not checked"."""
        if t.get("rule") not in _LEVELS:
            return chart_check.NOT_CHECKED, None
        try:
            from momx.chart_learning import snapshot as chart_snapshot
            return chart_check.verdict(side, chart_snapshot(t))
        except Exception:  # noqa: BLE001 - never block a buy on a bad chart record
            return chart_check.NOT_CHECKED, None

    def _top_check(self, side: str, t: Mapping, now: datetime) -> tuple[str, str | None]:
        """His top-of-board rule at BUY time (top_verdict): on the ticker's CURRENT run from the
        Bot book this mirror already holds (OptionTrackBook.top_now), not the record's frozen stamp.
        The record's "topHL" stamp is used only when the book cannot say: a book without top_now
        (test doubles), or a board still unknown once the book's warm-up is over."""
        if t.get("rule") not in _LEVELS:
            return TOP_OK, None
        book = self.books.get(side)
        top_now = getattr(book, "top_now", None)
        if not callable(top_now):
            return stamp_verdict(t)
        from momx.option_track import TOP_HOLD_SECONDS, TOP_N
        stamp = t.get("topHL") if isinstance(t.get("topHL"), Mapping) else {}
        board = str(t.get("board") or stamp.get("board") or "")
        if board not in TOP_N:
            return TOP_OK, None
        now_ts = now.timestamp()
        try:
            current = top_now(board, t.get("symbol"), now_ts)
        except Exception:  # noqa: BLE001 - an unreadable run is an unknown run
            current = None
        if isinstance(current, Mapping):
            return top_verdict(t, current, now)
        started = _num(getattr(book, "started_at", None))
        if started is not None and now_ts - started < TOP_HOLD_SECONDS:
            return top_verdict(t, None, now, warming=True)
        return stamp_verdict(t)

    def _top_skip(self, key: str, side: str, t: Mapping, why: str, stamp: str, final: bool) -> bool:
        """Remember (or refresh) the one top-of-board skip of this signal (side, ticker, time).
        final = a rules skip for the day (rank); otherwise the signal is checked again on later
        ticks and only the reason is kept current (latest rank / hold), so "Not ordered" stays
        truthful. True when something changed (save)."""
        signal = (side, t.get("symbol"), t.get("at"))
        k = next((k for k, v in self._top_skips.items()
                  if isinstance(v, dict) and (v.get("side"), v.get("symbol"), v.get("at")) == signal), key)
        old = self._top_skips.get(k)
        if isinstance(old, dict) and old.get("why") == why and bool(old.get("final")) == final:
            return False
        self._remember_skip(self._top_skips, k, side, t, why, stamp, refresh=True)
        entry = self._top_skips.get(k)
        if isinstance(entry, dict):
            entry.pop("passed", None)         # held (again) by the top check: the latest reason stands
            if final:
                entry["final"] = True
            else:
                entry.pop("final", None)
        return True

    def _top_final(self) -> set:
        """Signals (side, ticker, time) the top-of-board check turned down for the day."""
        return {(v.get("side"), v.get("symbol"), v.get("at")) for v in self._top_skips.values()
                if isinstance(v, dict) and v.get("final")}

    def _top_pass(self, key: str, side: str, t: Mapping, stamp: str) -> bool:
        """The signal passes the top-of-board check now: its entry is kept but marked passed
        ({"passed": time}, no "why"), so no stale reason is left behind AND the Scorecard knows the
        check passed at buy time (it must not fall back to the record's frozen stamp). A signal
        with no entry gets one only when its own stamp would read as a top-of-board hold.
        _drop_skips_for still removes it once an order goes out. True when something changed (save)."""
        signal = (side, t.get("symbol"), t.get("at"))
        found = [k for k, v in self._top_skips.items()
                 if isinstance(v, dict) and (v.get("side"), v.get("symbol"), v.get("at")) == signal]
        if not found:
            if stamp_verdict(t)[0] == TOP_OK:
                return False
            self._remember_skip(self._top_skips, key, side, t, "", stamp, refresh=True)
            found = [key]
        changed = False
        for k in found:
            entry = self._top_skips.get(k)
            if not isinstance(entry, dict) or (entry.get("passed") and "why" not in entry):
                continue
            entry.pop("why", None)
            entry.pop("final", None)
            entry["passed"] = stamp
            changed = True
        return changed

    def _top_skip_count(self) -> int:
        """Signals the top-of-board check is holding or turned down (passed entries excluded)."""
        return sum(1 for v in self._top_skips.values() if isinstance(v, dict) and not v.get("passed"))

    def _skip_for_chart(self, key: str, side: str, t: Mapping, why: str, stamp: str) -> bool:
        """Remember a chart-check skip for the day. True when it is new (save)."""
        return self._remember_skip(self._chart_skips, key, side, t, why, stamp)

    @staticmethod
    def _remember_skip(store: dict, key: str, side: str, t: Mapping, why: str, stamp: str,
                       refresh: bool = False) -> bool:
        """Remember why a signal got no order today (bounded). True when it is new (save); a
        known one only has its reason (and with refresh, its checkedAt) refreshed in memory
        (saved with the next save)."""
        if key in store:
            store[key]["why"] = why
            if refresh:
                store[key]["checkedAt"] = stamp
            return False
        store[key] = {"symbol": t.get("symbol"), "side": side, "rule": t.get("rule"),
                      "at": t.get("at"), "why": why, "checkedAt": stamp}
        while len(store) > CHART_SKIPS_MAX:
            store.pop(next(iter(store)))
        return True

    def _free_buying_power(self, now_ts: float) -> float | None:
        """The paper account's options buying power still free for a new buy: the last Alpaca
        read, less every LIVE order sent at or after that read - still waiting, or already filled
        since (a buy sent before the read is already inside the broker's figure). A filled one
        counts at its fill (filled contracts x fill price), else at its limit. Closed and missed
        orders are not counted. None = unknown or stale: never blocks."""
        b = self._broker
        if not isinstance(b, dict):
            return None
        power, read_ts = _num(b.get("optionsBuyingPower")), _ts(b.get("at"))
        if power is None or read_ts is None or not -60 <= now_ts - read_ts <= BROKER_FRESH_SECONDS:
            return None
        committed = 0.0
        for r in self._rows.values():
            if r.get("status") not in LIVE or (_ts(r.get("buyAt")) or 0) < read_ts:
                continue
            qty, limit = _num(r.get("qty")), _num(r.get("buyLimit"))
            got, fill = _num(r.get("buyFilledQty")), _num(r.get("buyFill"))
            if r.get("status") != "buying" and got and fill:
                committed += got * fill * 100
            elif qty and limit:
                committed += qty * limit * 100
        return power - committed

    def _fund_hold(self, key: str, now_ts: float, done: int) -> bool:
        """True when this signal's buying-power skip still stands without a fresh quote: it was
        checked after the latest (fresh) account read and no order has closed or missed since.
        Saves the synchronous live-quote call on every 10 s tick for a signal that cannot be paid for."""
        skip = self._fund_skips.get(key)
        if not isinstance(skip, dict) or self._fund_seen.get(key) != done:
            return False
        if self._free_buying_power(now_ts) is None:
            return False                      # unknown or stale buying power never blocks
        read_ts, checked = _ts((self._broker or {}).get("at")), _ts(skip.get("checkedAt"))
        return read_ts is not None and checked is not None and checked >= read_ts

    def _skip_for_funds(self, key: str, side: str, t: Mapping, why: str, stamp: str, done: int) -> bool:
        """Remember a buying-power skip (checkedAt refreshed on every re-check). True when new (save)."""
        self._fund_seen[key] = done
        return self._remember_skip(self._fund_skips, key, side, t, why, stamp, refresh=True)

    def _drop_skips_for(self, signal: tuple) -> bool:
        """An order went out for this (side, symbol, at) signal: no skip reason may remain for it
        at any other level. True when something was dropped (save)."""
        dropped = False
        for store in (self._fund_skips, self._chart_skips, self._top_skips):
            for k in [k for k, v in store.items()
                      if isinstance(v, dict) and (v.get("side"), v.get("symbol"), v.get("at")) == signal]:
                store.pop(k, None)
                self._fund_seen.pop(k, None)
                dropped = True
        return dropped

    def _read_account(self, client, now_ts: float, stamp: str) -> bool:
        """Read the paper account's real money from Alpaca, at most once a minute. A failed read
        keeps the last good one (marked with the error); it never stops the trading loop."""
        if now_ts - self._broker_ts < ACCOUNT_SECONDS:
            return False
        self._broker_ts = now_ts
        try:
            a = client.get_account()
        except Exception as exc:  # noqa: BLE001
            if self._broker:
                self._broker["error"] = f"{type(exc).__name__}: {exc}"[:160]
            return False

        def money(name):
            v = getattr(a, name, None) if not isinstance(a, dict) else a.get(name)
            return _num(v)
        self._broker = {"at": stamp, "cash": money("cash"), "optionsBuyingPower": money("options_buying_power"),
                        "buyingPower": money("buying_power"), "equity": money("equity"), "lastEquity": money("last_equity")}
        return True

    def _client_ok(self):
        if self._client is None and not self._refused:
            try:
                client = self._client_factory()
            except Exception as exc:  # noqa: BLE001
                self._error = f"client: {type(exc).__name__}: {exc}"[:200]
                return None
            creds = getattr(client, "credentials", None)
            if not getattr(creds, "paper", False):
                self._refused = f"{self.account} is not a paper account - mirror refused to run"
                return None
            self._client = client
        return self._client

    def _path(self, day: str) -> Path:
        return self.directory / f"{day}.json"

    def _load(self, day: str) -> None:
        self._day, self._rows = day, {}
        self._daily_stop, self._stop_ticks = None, 0
        self._broker, self._broker_ts = None, 0.0
        self._chart_skips, self._fund_skips, self._fund_seen = {}, {}, {}
        self._top_skips = {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("topSkips"), dict):
                self._top_skips = doc["topSkips"]        # and the top-of-board ones
            if isinstance(doc, dict) and isinstance(doc.get("chartSkips"), dict):
                self._chart_skips = doc["chartSkips"]    # a restart keeps the day's chart-check reasons
            if isinstance(doc, dict) and isinstance(doc.get("fundSkips"), dict):
                self._fund_skips = doc["fundSkips"]      # and the buying-power ones
            if isinstance(doc, dict) and isinstance(doc.get("rows"), dict):
                self._rows = doc["rows"]
            if isinstance(doc, dict) and isinstance(doc.get("dailyStop"), dict):
                self._daily_stop = doc["dailyStop"]      # a restart must not re-open a stopped day
            if isinstance(doc, dict) and isinstance(doc.get("broker"), dict):
                self._broker = doc["broker"]             # shown until the next read
        except (OSError, ValueError):
            pass
        # Midpoint exits need not fill before the bell. Retain the latest known
        # state of every old order across both rollover and process restart.
        seen = set(self._rows)
        for path in sorted(self.directory.glob("20*.json"), reverse=True):
            if path.stem >= day:
                continue
            try:
                previous = json.loads(path.read_text(encoding="utf-8")).get("rows", {})
            except (OSError, ValueError):
                continue
            for key, row in previous.items():
                if key in seen:
                    continue
                seen.add(key)
                if row.get("status") in LIVE:
                    self._rows[key] = {**row, "carried": True}

    def _plan_mark(self, stamp: str) -> None:
        """Say on disk which exit plan THIS running bot follows: <account>/plan.json =
        {"plan": "chart", "since": first time this code ran}. The Scorecard is built in
        another process (api_server, momx/bot_desk.py) and the Bot page is live in the
        browser before either server restarts, so the page shows the chart plan's text and
        evaluation only once this file says the paper bot really trades it."""
        try:
            path = self.directory / PLAN_MARK_FILE
            try:
                seen = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                seen = None
            if isinstance(seen, dict) and seen.get("plan") == EXIT_PLAN and seen.get("since"):
                return
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"plan": EXIT_PLAN, "since": stamp, "account": self.account}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass

    def _save(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path(self._day).with_suffix(".tmp")
            doc = {"day": self._day, "account": self.account, "rows": self._rows}
            if self._daily_stop:
                doc["dailyStop"] = self._daily_stop
            if self._broker:
                doc["broker"] = self._broker
            if self._chart_skips:
                doc["chartSkips"] = self._chart_skips
            if self._fund_skips:
                doc["fundSkips"] = self._fund_skips
            if self._top_skips:
                doc["topSkips"] = self._top_skips
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, self._path(self._day))
        except OSError:
            pass

    def _live_quote(self, contract: str, now_ts: float, cache: dict | None = None,
                    stock: str | None = None) -> tuple[Any, Any] | None:
        """A fresh broker (bid, ask) for one contract right now, or None.
        The Bot re-prices one ticker every 8+ s, so its own quote can be
        minutes old; the order uses the live quote whenever it can.
        With `stock` (and a cache), the stock's live price rides in the same
        call and is left in cache[("stock", symbol)] for the chart plan."""
        if not self._quotes_fetch or not contract:
            return None
        if cache is not None and contract in cache:
            return cache[contract]
        found = None
        try:
            doc = self._quotes_fetch([contract] + ([stock] if stock else [])) or {}
            seen = _num(doc.get("asOf"))
            if seen is not None and -5 <= now_ts - seen <= QUOTE_FRESH_SECONDS:
                q = (doc.get("quotes") or {}).get(contract) or {}
                if _mid(q.get("bid"), q.get("ask")) is not None:
                    found = (q.get("bid"), q.get("ask"))
                if stock and cache is not None and now_ts - seen <= PLAN_STOCK_SECONDS:
                    cache[("stock", stock)] = _stock_price((doc.get("quotes") or {}).get(stock))
        except Exception:  # noqa: BLE001 - fall back to the Bot's own quote
            found = None
        if cache is not None:
            cache[contract] = found
        return found

    def _reprice_buy(self, client, key: str, row: dict, bot: Mapping, now_ts: float, stamp: str) -> bool:
        """Re-send a cancelled, unfilled buy at the live ASK so it fills (his
        "dont miss any trade", 2026-11-05). False = give up (missed)."""
        tries = int(row.get("buyTries") or 0)
        if row.get("exec") != EXEC_VERSION or tries >= BUY_REPRICES:
            return False
        t = (bot.get(key) or ("", {}))[1]
        leg = row.get("leg") or "main"
        # Only the Bot leaving stops the re-send. The signal's age does not:
        # the order is already out, and on 2026-11-09 the 10-minute age test
        # here turned 18 late-sent buys into misses with no try at the ask.
        if t and not still_open(t, leg):
            return False
        charted = row.get("exitPlan") == EXIT_PLAN
        cache: dict = {}
        q = (self._live_quote(row.get("contract"), now_ts, cache, stock=row.get("symbol") if charted else None)
             or (bot_quote(t, now_ts, leg) if t else None))
        limit = round(float(q[1]), 2) if q and sellable(*q) else None
        if limit is None or limit <= 0:
            row["note"] = ("No fresh quote to re-send at the ask; not filled at the ask" if row.get("exitPlan") == EXIT_PLAN
                           else "No fresh quote to re-send at the ask; not filled at the mid")
            return False
        cid = self._cid(key, "b", tries + 1)
        row.setdefault("buyHistory", []).append({"limit": row.get("buyLimit"), "at": row.get("buyAt"), "id": row.get("buyId")})
        row.update(buyId=cid, buyLimit=limit, buyAt=stamp, buyTries=tries + 1, note=f"Re-sent at the ask ${limit:.2f} (try {tries + 1})")
        row.pop("submitError", None)
        if charted:
            # The plan starts from the stock price when THIS order went out, not the first
            # one's: an ask buy goes unfilled mainly when the price is running away, so two
            # to six minutes later the stock has moved. Kept at the first order's price, a
            # re-sent buy "hit T1" on its fill tick and sold a lot under what it had just paid
            # (2026-11-13 review). _plan_stop re-bases the stop and T1/T2/T3 from entryStock.
            row["buyHistory"][-1].update(entryStock=row.get("entryStock"), entryBid=row.get("entryBid"),
                                         entryAsk=row.get("entryAsk"))
            stock_now = cache.get(("stock", row.get("symbol")))
            if stock_now is None and t:
                seen_at = _ts(t.get("stockLastAt"))
                if seen_at is not None and 0 <= now_ts - seen_at <= STOCK_FRESH_SECONDS:
                    stock_now = _num(t.get("stockLast"))
            if stock_now is not None:
                row["entryStock"] = stock_now
            row.update(entryBid=_num(q[0]), entryAsk=_num(q[1]), entryMid=_mid(q[0], q[1]))
        try:
            client.submit_option_limit_order(row["contract"], int(row.get("qty") or 1), limit, cid, "buy_to_open")
        except Exception as exc:  # noqa: BLE001 - outcome unknown: reconcile by client id
            row["note"] = f"Awaiting broker reconciliation: {exc}"[:200]
            if paper_control.broker_answered(exc):
                row["submitError"] = str(exc)[:300]  # what the broker answered: kept, the note is overwritten
        return True

    def _cid(self, key: str, leg: str, attempt: int = 0) -> str:
        return f"mxp-{leg}{attempt}-{hashlib.sha1((self.account + key).encode()).hexdigest()[:16]}"

    @staticmethod
    def _state(client, cid: str) -> tuple[str, float | None, Any, int]:
        order = client.get_order_by_client_id(cid)
        status = str(getattr(order, "status", "") or "").split(".")[-1].lower()
        filled = int(_num(getattr(order, "filled_qty", None)) or 0)
        return status, _num(getattr(order, "filled_avg_price", None)), getattr(order, "id", None), filled

    def _cancel(self, client, cid: str) -> tuple[str, float | None, int]:
        status, price, oid, filled = self._state(client, cid)
        if status not in ("filled", "canceled", "expired", "rejected") and oid:
            try:
                client.cancel_order_by_id(oid)
            except Exception:  # noqa: BLE001 - it may have filled meanwhile
                pass
            status, price, _, filled = self._state(client, cid)
        return status, price, filled

    # ------------------------------------------------------------------ tick
    def tick(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(ET)).astimezone(ET)
        if self._off():
            return
        minutes = now.hour * 60 + now.minute
        trading_now = now.weekday() < 5 and 9 * 60 + 30 <= minutes <= 16 * 60
        day, now_ts = now.date().isoformat(), now.timestamp()
        stamp = now.replace(microsecond=0).isoformat()
        with self._lock:
            if self._day != day:
                self._load(day)
                self._plan_mark(stamp)
            if not trading_now and not any(self._expired_day(r, now) for r in self._rows.values()):
                return
            client = self._client_ok()
            if client is None:
                return
            bot: dict[str, tuple[str, dict]] = {}
            for side, book in self.books.items():
                try:
                    for t in book.trades():
                        bot[trade_key(side, t)] = (side, t)
                except Exception:  # noqa: BLE001
                    continue
            changed = self._read_account(client, now_ts, stamp)
            try:
                changed |= self._reconcile_expired(client, now)
                if trading_now:
                    changed |= self._check_buy_hold(day, stamp)
                    changed |= self._follow(client, now_ts, stamp, bot)
                    changed |= self._manage(client, bot, now, now_ts, stamp)
                    changed |= self._entries(client, bot, now, now_ts, stamp)
                self._error = ""
            except Exception as exc:  # noqa: BLE001 - never kill the loop
                self._error = f"{type(exc).__name__}: {exc}"[:200]
            if changed:
                self._save()

    def set_ai_rank(self, rank: dict | None) -> None:
        """Called from the picker's own thread. A plain attribute swap: no lock
        needed, and the order loop never waits for the AI."""
        self._ai_rank = dict(rank) if rank else None

    def _waiting(self, bot: Mapping, now_ts: float) -> list[tuple[str, str, dict]]:
        """The signals that could take a free slot right now, in the rules bot's
        order (newest first, highest Level first), one row per signal and per
        ticker. No broker call: the Bot's own quote decides "sellable" here."""
        held = {r.get("symbol") for r in self._rows.values() if r.get("status") in LIVE}
        taken = {(r.get("side"), r.get("symbol"), r.get("at")) for r in self._rows.values() if r.get("rule") in _LEVELS}
        top_final, now = self._top_final(), datetime.fromtimestamp(now_ts, ET)
        out, tickers = [], set()
        for key, (side, t) in sorted(bot.items(), key=_newest_first, reverse=True):
            allowed = None if self.rules is None else self.rules.get(side)
            if allowed is not None and t.get("rule") not in allowed:
                continue
            signal = (side, t.get("symbol"), t.get("at"))
            if (t.get("rule") in _LEVELS and signal in taken) or key in self._rows or t.get("symbol") in held:
                continue
            if t.get("symbol") in tickers or not eligible(t, now_ts, self.jev_cut, self.leg):
                continue
            if t.get("rule") in _LEVELS and not has_plan(t, self.leg):
                continue                      # no chart plan: the order loop will not buy it
            if (t.get("rule") in _LEVELS and signal in top_final) or self._top_check(side, t, now)[0] != TOP_OK:
                continue                      # not (yet) in its board's top N by H/L: not bought now
            if self._chart_verdict(side, t)[0] == chart_check.FAILED:
                continue                      # the order loop will not buy it: not one for the AI to rank
            q = bot_quote(t, now_ts, self.leg)
            if not q or not sellable(*q) or not size_for(_ask(*q)):
                continue
            tickers.add(t.get("symbol"))
            out.append((key, side, dict(t)))
        return out

    def candidates(self, now: datetime | None = None) -> list[tuple[str, str, dict]] | None:
        """For the AI picker: the signals waiting for this account's NEXT free
        slot. Returned while the slots are full too - a freed slot is refilled
        in the same tick it frees, so the ranking has to be ready beforehand.
        [] = nothing to choose (off, outside the buy windows, held, day's buys
        used up). None = the order loop is busy; ask again, this is not "empty"."""
        now = (now or datetime.now(ET)).astimezone(ET)
        if self._off() or now.weekday() >= 5 or not entry_open(now) or self._hold_buys:
            return []
        if not self._lock.acquire(timeout=1):
            return None
        try:
            if self._day != now.date().isoformat():
                return []                     # the order loop loads the day first
            entries = sum(1 for r in self._rows.values() if r.get("buyId") and not r.get("carried"))
            if entries >= MAX_ENTRIES_PER_DAY:
                return []
            bot: dict[str, tuple[str, dict]] = {}
            for side, book in self.books.items():
                try:
                    for t in book.trades():
                        bot[trade_key(side, t)] = (side, t)
                except Exception:  # noqa: BLE001
                    continue
            return self._waiting(bot, now.timestamp())
        finally:
            self._lock.release()

    def _check_buy_hold(self, day: str, stamp: str) -> bool:
        """Decide whether NEW buys are held this tick (pause, or the daily loss
        stop). Exits are never held. True when the stop latched just now."""
        latched = False
        if not paper_control.DAILY_STOP_ON:
            # The stop is switched off. One that tripped earlier today stays on file as a record
            # (it explains the signals missed while it held), stamped with when it was lifted.
            if self._daily_stop and not self._daily_stop.get("liftedAt"):
                self._daily_stop["liftedAt"] = stamp
                latched = True                # save the stamp
            self._stop_ticks = 0
        elif self._daily_stop is None:
            pnl = paper_control.day_pnl(self._rows.values(), day)
            # A held position with no price at all is "unknown", not "fine":
            # it must not clear the count toward the stop (a dead 0/0 quote
            # or one failed quote read would otherwise keep the bot buying).
            unknown = any(r.get("status") in paper_control.HOLDING and (_num(r.get("held")) or 0) > 0
                          and not paper_control.carried(r, day)
                          and _num(r.get("markMid")) is None and _num(r.get("stopMark")) is None
                          for r in self._rows.values())
            if pnl <= -paper_control.DAILY_LOSS_STOP:
                self._stop_ticks += 1
            elif not unknown:
                self._stop_ticks = 0
            if self._stop_ticks >= STOP_TICKS:
                self._daily_stop = {"at": stamp, "pnl": pnl, "limit": paper_control.DAILY_LOSS_STOP}
                latched = True
        if paper_control.paused(self.directory.parent, self.account):
            self._hold_buys = "paused"
        elif self._daily_stop and paper_control.DAILY_STOP_ON:
            self._hold_buys = "daily loss stop"
        else:
            self._hold_buys = ""
        return latched

    def remove_stuck(self, key: str, who: str = "", now: datetime | None = None) -> dict:
        """His "Remove" button for a stuck buy. The broker is asked first: the
        row is closed as missed only when the broker confirms nothing filled.
        A filled order is a real position and is never removed."""
        now = (now or datetime.now(ET)).astimezone(ET)
        stamp = now.replace(microsecond=0).isoformat()

        def refuse(text: str) -> dict:
            return {"ok": False, "error": text}

        if not self._lock.acquire(timeout=12):
            return refuse("The paper bot is busy with the broker. Try again in a moment.")
        try:
            if self._day != now.date().isoformat():
                self._load(now.date().isoformat())
            row = self._rows.get(key)
            if row is None:
                return refuse("That order is not in today's list any more.")
            if row.get("status") != "buying":
                return refuse(f"That order is not waiting to buy any more (it is now: {row.get('status')}).")
            if now.timestamp() - (_ts(row.get("buyAt")) or now.timestamp()) < paper_control.STUCK_SECONDS:
                return refuse("That order is still being worked. It can be removed once it is 10 minutes old.")
            client = self._client_ok()
            if client is None:
                return refuse(self._refused or self._error or "The paper account is not reachable right now.")
            ids = [row.get("buyId")] + [h.get("id") for h in row.get("buyHistory") or []]
            for cid in [c for c in ids if c]:
                try:
                    status, _, filled = self._cancel(client, cid)
                except Exception as exc:  # noqa: BLE001
                    if paper_control.not_found(exc):
                        continue                  # the broker never had it: nothing to cancel
                    return refuse(f"Could not check this order with the broker, so nothing was removed: {exc}"[:240])
                if filled > 0:
                    return refuse(f"The broker says {filled} contract(s) of this order filled. "
                                  "It is a real position, not a stuck order, so it was not removed.")
                if status not in ("canceled", "expired", "rejected"):
                    # A cancel that failed or is still pending: the order could
                    # yet fill with nothing tracking it.
                    return refuse("The broker still shows this order as working, so it was not removed. "
                                  "Try again in a moment.")
            row.update(status="missed", removedAt=stamp, removedBy=str(who or ""),
                       note="Removed by you: stuck buy, nothing filled at the broker")
            self._save()
            return {"ok": True, "account": self.account, "key": key, "symbol": row.get("symbol")}
        finally:
            self._lock.release()

    @staticmethod
    def _expired_day(row, now):
        if row.get("status") not in ("open", "exit_due") or not row.get("held"):
            return None
        match = re.fullmatch(r"[A-Z0-9.]+(\d{6})[CP]\d{8}", (row.get("contract") or "").replace(" ", ""))
        if match:
            try:
                day = datetime.strptime(match[1], "%y%m%d").date()
                return day.isoformat() if day < now.date() else None
            except ValueError:
                pass
        return None

    def _reconcile_expired(self, client, now):
        changed = False
        by_day = {}
        for row in self._rows.values():
            day = self._expired_day(row, now)
            if day:
                by_day.setdefault(day, []).append(row)
        for day, rows in by_day.items():
            if now.timestamp() < self._expiry_retry.get(day, 0):
                continue
            self._expiry_retry[day] = now.timestamp()+300
            try:
                activities = client.get_option_expirations(day)
            except Exception:
                activities = []
            for row in rows:
                matches = [a for a in activities if a.get("activity_type") == "OPEXP" and a.get("status") == "executed"
                           and (a.get("symbol") or "").replace(" ", "") == (row.get("contract") or "").replace(" ", "") and a.get("date") == day
                           and a.get("id") and _num(a.get("net_amount")) == 0
                           and _num(a.get("qty")) == -int(row["held"])]
                same_contract = [r for r in self._rows.values() if (r.get("contract") or "").replace(" ", "") == (row.get("contract") or "").replace(" ", "") and r.get("status") in LIVE]
                if len(matches) != 1 or len(same_contract) != 1:
                    row.update(note="Expired contract: awaiting matching broker expiration evidence; P/L remains unpriced", markMid=None, markAt=None)
                    changed = True
                    continue
                activity = matches[0]
                stamp = datetime.fromisoformat(day+"T16:00:00").replace(tzinfo=ET).isoformat()
                row.setdefault("sells", []).append({"qty":int(row["held"]), "price":0.0,
                    "why":"Broker-confirmed worthless expiration", "at":stamp, "kind":"expiration", "activityId":activity["id"]})
                row.update(held=0, markMid=None, markAt=None, closeTimeBasis="Expiration date; time is session close",
                           note="Expired worthless per broker activity; no midpoint sell fill")
                self._close_out(row, stamp)
                changed = True
        return changed

    @staticmethod
    def _close_out(row: dict, stamp: str) -> None:
        buy = _num(row.get("buyFill"))
        fills = row.get("sells") or []
        complete = buy is not None and buy > 0 and fills and all(_num(f.get("price")) is not None and _num(f.get("price")) >= 0 and (_num(f.get("qty")) or 0) > 0 for f in fills)
        if complete and row.get("buyFilledQty") is not None:
            complete = math.isclose(sum(float(f["qty"]) for f in fills), _num(row["buyFilledQty"]) or 0, abs_tol=1e-8)
        pl = sum((float(f["price"]) - buy) * float(f["qty"]) * 100 for f in fills) if complete else None
        row.update(status="closed", closedAt=stamp, pl=round(pl, 2) if pl is not None else None)

    def _follow(self, client, now_ts: float, stamp: str, bot: Mapping | None = None) -> bool:
        changed = False
        for key, row in self._rows.items():
            try:
                changed |= self._follow_row(client, row, now_ts, stamp, key=key, bot=bot)
            except Exception as exc:
                orphan = (row.get("status") == "buying" and paper_control.not_found(exc)
                          and now_ts - (_ts(row.get("buyAt")) or now_ts) > ORPHAN_SECONDS)
                if orphan and row.get("submitError"):
                    # The broker answered when the buy was sent: it refused it. Say why.
                    row.update(status="missed",
                               note=("The broker refused this buy: " + paper_control.refusal_reason(row["submitError"]))[:200])
                elif orphan:
                    row.update(status="missed", note="The broker has no record of this buy: it never reached the broker")
                elif self._sale_lost(row, exc, now_ts):
                    pass  # back to "exit_due": the manage pass sends it again
                else:
                    row["note"] = f"Awaiting broker reconciliation: {exc}"[:200]
                changed = True
        return changed

    def _follow_row(self, client, row: dict, now_ts: float, stamp: str, key: str | None = None,
                    bot: Mapping | None = None, pull: bool = False) -> bool:
        """pull=True: cancel the working SALE now instead of waiting SELL_TTL_SECONDS (a
        chart-plan stop is due while a target lot is still at the broker); anything it
        filled meanwhile is booked exactly as usual."""
        changed = False
        if row.get("status") == "buying":
            status, price, _, filled = self._state(client, row["buyId"])
            late = now_ts - (_ts(row.get("buyAt")) or now_ts) > BUY_TTL_SECONDS
            if status != "filled" and late:
                status, price, filled = self._cancel(client, row["buyId"])
            if status == "filled" or (filled > 0 and status in ("canceled", "expired", "rejected")):
                got = filled or int(row.get("qty") or 1)
                row.update(status="open", buyFill=price, buyFillAt=stamp, buyFilledQty=got, held=got, peak=0.0, sells=[])
                if row.get("exitPlan") == EXIT_PLAN:
                    # His split: one lot at each target, from what really filled.
                    row["planSplit"] = chart_plan.split_contracts(got)
                    self._plan_stop(row)
                # Broker time is distinct from the time our poll observed a fill.
                try:
                    filled_at = getattr(client.get_order_by_client_id(row["buyId"]), "filled_at", None)
                    if filled_at:
                        row["buyFilledAt"] = str(filled_at)
                except Exception:
                    pass
                changed = True
            elif (status in ("canceled", "expired") and key and not self._hold_buys
                  and self._reprice_buy(client, key, row, bot or {}, now_ts, stamp)):
                changed = True
            elif status in ("canceled", "expired", "rejected"):
                note = row.get("note") if row.get("exec") == EXEC_VERSION and row.get("note") else None
                first = "ask" if row.get("exitPlan") == EXIT_PLAN else "mid"
                if self._hold_buys and status != "rejected":
                    note = f"Not filled at the {first}; not re-sent at the ask ({self._hold_buys})"
                row.update(status="missed", note=note or f"buy {status or 'unfilled'} (not filled at the {first})")
                changed = True
        elif row.get("status") == "selling":
            status, price, _, filled = self._state(client, row["sellId"])
            late = pull or now_ts - (_ts(row.get("sellAt")) or now_ts) > SELL_TTL_SECONDS
            if status != "filled" and late:
                status, price, filled = self._cancel(client, row["sellId"])
            if filled > 0 and status in ("filled", "canceled", "expired", "rejected"):
                row.pop("sellRejects", None)
                row.pop("sellRejectAt", None)
                row.setdefault("sells", []).append(
                    {"qty": filled, "price": price, "why": row.get("sellWhy"), "at": stamp})
                row["held"] = int(row.get("held") or 0) - filled
                if str(row.get("sellWhy") or "").startswith(("+100%", "High OI level")):
                    row["partialDone"] = True
                want = int(row.get("sellQty") or filled) - filled
                if row["held"] <= 0:
                    self._close_out(row, stamp)
                elif want > 0:
                    row.update(status="exit_due", sellQty=want)       # the rest of that leg, re-priced
                else:
                    row.update(status="open", sellTries=0)
                    row.pop("sellUrgent", None)
                changed = True
            elif status == "rejected":
                # The broker refused the sale outright. Not re-sent every tick: see SELL_REJECTS_MAX.
                self._sale_refused(row, now_ts)
                changed = True
            elif status in ("canceled", "expired"):
                row["status"] = "exit_due"                              # re-priced on the manage pass
                row.pop("sellRejects", None)
                row.pop("sellRejectAt", None)
                changed = True
        return changed

    @staticmethod
    def _sale_refused(row: dict, now_ts: float) -> None:
        """Book one refusal of the working sale: back to "exit_due", counted and timed so
        _sale_held spaces the next tries (see SELL_REJECTS_MAX), with the broker's reason."""
        count = int(row.get("sellRejects") or 0) + 1
        said = paper_control.refusal_reason(row["sellError"]) if row.get("sellError") else ""
        more = ("; trying again every %d minutes" % (SELL_REJECT_SLOW_SECONDS // 60)
                if count >= SELL_REJECTS_MAX else "")
        times = "" if count == 1 else f" ({count} times in a row{more})"
        row.update(status="exit_due", sellRejects=count, sellRejectAt=now_ts,
                   note=(SELL_REFUSED_NOTE + (": " + said if said else "") + times)[:200])

    @classmethod
    def _sale_lost(cls, row: dict, exc: BaseException, now_ts: float) -> bool:
        """A working SALE the broker has no record of (see SELL_LOST_SECONDS). True when the
        row went back to "exit_due" so it is sent again (under a new id: the try counter has
        already moved on); False = not this case, or still inside the wait for a sale whose
        submit got no answer. What is sold, why and how (sellQty / sellWhy / sellUrgent) is kept."""
        if row.get("status") != "selling" or not paper_control.not_found(exc):
            return False
        if row.get("sellError"):
            cls._sale_refused(row, now_ts)        # the broker answered at submit: a refusal
            return True
        if now_ts - (_ts(row.get("sellAt")) or now_ts) < SELL_LOST_SECONDS:
            return False                          # no answer at submit: it may still appear
        row.update(status="exit_due", note=SELL_LOST_NOTE)
        return True

    @staticmethod
    def _sale_held(row: Mapping, now_ts: float) -> bool:
        """True while a sale the broker refused must not be sent again yet (see SELL_REJECTS_MAX)."""
        count = int(row.get("sellRejects") or 0)
        last = _num(row.get("sellRejectAt"))
        if count <= 0 or last is None:
            return False
        wait = SELL_REJECT_SLOW_SECONDS if count >= SELL_REJECTS_MAX else SELL_TTL_SECONDS
        return 0 <= now_ts - last < wait

    def _sell(self, client, key: str, row: dict, qty: int, why: str, bid: Any, ask: Any, stamp: str,
              urgent: bool | None = None) -> None:
        """A limit sell at the mid. urgent=True (a chart-plan STOP): a limit at the BID, so it
        fills instead of sitting at the mid while the option keeps falling (several 2026-11-10..12
        "-50%" stops closed at -78% to -93%). None = the same as this row's last sell (a re-price)."""
        tries = int(row.get("sellTries") or 0)
        if urgent is None:
            urgent = bool(row.get("sellUrgent"))
        if urgent:
            row["sellUrgent"] = True
        else:
            row.pop("sellUrgent", None)
        limit = _mid(bid, ask)
        if urgent and limit is not None and (_num(bid) or 0) > 0:
            limit = round(float(bid), 2)
        if limit is None or limit <= 0:
            row.update(status="exit_due", sellQty=qty, sellWhy=why,
                       note="Waiting for a valid fresh bid/ask midpoint; no market fallback")
            return
        cid = self._cid(key, "s%d-" % len(row.get("sells") or []), tries)
        try:
            client.submit_option_limit_order(row["contract"], qty, limit, cid, "sell_to_close")
            row.update(sellId=cid, sellLimit=limit, sellAt=stamp, sellQty=qty, sellWhy=why, status="selling", note="")
            row.pop("sellError", None)
        except Exception as exc:  # noqa: BLE001
            if paper_control.broker_answered(exc):
                row["sellError"] = str(exc)[:300]      # what the broker answered, for the note
            # A timeout can occur AFTER broker acceptance. Reconcile this exact
            # client id before any new attempt; do not create another order.
            row.update(sellId=cid, sellLimit=limit, sellAt=stamp, sellQty=qty,
                       sellWhy=why, status="selling", note=f"Awaiting broker reconciliation: {exc}"[:200])
        row["sellTries"] = tries + 1

    def _manage(self, client, bot, now: datetime, now_ts: float, stamp: str) -> bool:
        changed = False
        final = (now.hour, now.minute) >= FINAL_EXIT
        live_quotes = {}
        #: symbol -> the stock's live price this tick, for the chart-plan rows' stop and targets.
        live_stocks: dict = {}
        if self._quotes_fetch:
            contracts = sorted({r["contract"] for r in self._rows.values()
                                if r.get("status") in ("open", "selling", "exit_due") and r.get("contract")})
            # The stocks of the chart-plan positions ride in the same batched call (one Schwab
            # /quotes request), so the stop is checked on a live stock price every tick instead
            # of waiting for the scanner's next board rebuild (15 s to several minutes).
            stocks = sorted({str(r["symbol"]) for r in self._rows.values()
                             if r.get("status") in ("open", "selling", "exit_due") and r.get("contract")
                             and r.get("exitPlan") == EXIT_PLAN and r.get("symbol") and int(r.get("held") or 0) > 0})
            if contracts:
                try:
                    doc = self._quotes_fetch(contracts + stocks) or {}
                    seen = _num(doc.get("asOf"))
                    if seen is not None and -5 <= now_ts - seen <= QUOTE_FRESH_SECONDS:
                        live_quotes = doc.get("quotes") or {}
                        if now_ts - seen <= PLAN_STOCK_SECONDS:
                            for sym in stocks:
                                px = _stock_price(live_quotes.get(sym))
                                if px is not None:
                                    live_stocks[sym] = px
                except Exception:
                    pass  # Keep the position and retry; never fall back to market.
        for key, row in self._rows.items():
            if row.get("status") not in ("open", "selling", "exit_due"):
                continue
            held = int(row.get("held") or 0)
            if held <= 0:
                continue
            if self._expired_day(row, now):
                continue  # Only broker expiration evidence can settle an expired contract.
            t = bot.get(key, ("", {}))[1]
            leg = row.get("leg") or "main"
            bid, ask = leg_get(t, "lastBid", leg), leg_get(t, "lastAsk", leg)
            seen = _ts(t.get("lastAt"))
            fresh = seen is not None and 0 <= now_ts - seen <= QUOTE_FRESH_SECONDS
            if not fresh:
                bid, ask = None, None
            if fresh and leg_get(t, "exitBid", leg) is not None:
                bid, ask = leg_get(t, "exitBid", leg), leg_get(t, "exitAsk", leg)
            if self._quotes_fetch:
                q = live_quotes.get(row.get("contract")) or {}
                bid, ask = q.get("bid"), q.get("ask")
                seen = _num(doc.get("asOf")) if live_quotes else None
            mark = _mid(bid, ask)
            # Display valuation only; stale/missing quotes never become zero P/L.
            row.update(markMid=mark, markAt=datetime.fromtimestamp(seen, ET).isoformat() if mark is not None and seen is not None else None)
            if mark is not None:
                row["stopMark"] = mark        # last good price, for the daily loss stop only
            changed = True
            age = now_ts - seen if mark is not None and seen is not None else None
            if row.get("status") == "selling":
                # Pending orders remain exclusively broker-reconciled - with ONE exception: a
                # chart-plan position whose working sale is a target lot (or the 21-EMA / 15:52
                # sale) at the mid. Its stop is still checked every tick; when the stock trades
                # through it (or the option's own -50% shows) the working sale is cancelled and
                # everything left is sold at the bid in this same tick. Before 2026-11-13 the
                # stop waited for the 60 s sale to time out first.
                if row.get("exitPlan") != EXIT_PLAN or row.get("sellUrgent") or row.get("carried"):
                    continue
                self._plan_attach(row, t)
                self._plan_stop(row)
                stock = self._plan_stock(row, t, live_stocks.get(row.get("symbol")), now_ts, stamp)
                if not self._plan_due(row, stock, mark, age, stamp):
                    continue
                try:
                    self._follow_row(client, row, now_ts, stamp, key=key, pull=True)
                except Exception as exc:  # noqa: BLE001
                    # The broker has no record of the working sale (refused when it was sent,
                    # or lost on the way): nothing to cancel, so the stop goes out now.
                    # Anything else = the broker did not answer: try again next tick.
                    if not self._sale_lost(row, exc, now_ts):
                        continue
                held = int(row.get("held") or 0)
                if row.get("status") not in ("open", "exit_due") or held <= 0:
                    continue  # it filled in full meanwhile, or the cancel has not gone through yet
            if row.get("exitPlan") == EXIT_PLAN:
                # The chart plan (his decision 2026-11-12). Rows without the marker were
                # opened under the older plan below and keep it until they are closed.
                self._manage_plan(client, key, row, t, held, bid, ask, mark, age,
                                  live_stocks.get(row.get("symbol")), final, now_ts, stamp)
                continue
            if row.get("status") == "exit_due":                      # re-price the same leg
                if self._sale_held(row, now_ts):
                    continue                                          # the broker refused it: not every tick
                self._sell(client, key, row, min(held, int(row.get("sellQty") or held)),
                           row.get("sellWhy") or "exit", bid, ask, stamp)
                changed = True
                continue
            buy = _num(row.get("buyFill"))
            mark = _mid(bid, ask)
            if buy and mark is not None:
                gain = mark / buy - 1
                if gain > float(row.get("peak") or 0):
                    row["peak"] = round(gain, 4)
                    changed = True
                stop = stop_for(buy, float(row.get("peak") or 0))
                if stop is not None and stop != row.get("stop"):
                    row["stop"] = stop
                    changed = True
            bot_out = t.get("exitPrice") is not None or t.get("status") == "closed"
            why = None
            if row.get("carried"):
                why = "Prior-session midpoint exit still unfilled"
            elif final:
                why = "15:52 close"
            elif bot_out:
                why = "Bot exit: " + str(t.get("exitReason") or "")
            elif mark is not None and row.get("stop") is not None and mark <= float(row["stop"]):
                level = round((float(row["stop"]) / buy - 1) * 100) if buy else 0
                why = "stop at break-even" if level <= 0 else f"stop at +{level}%"
            elif buy and mark is not None and mark <= buy * (1 - STOP_LOSS):
                # His "-50%" exit. Counted over consecutive ticks; the count is
                # cleared below as soon as the option is back above the line.
                row["lossTicks"] = int(row.get("lossTicks") or 0) + 1
                changed = True
                if row["lossTicks"] >= STOP_LOSS_TICKS:
                    why = STOP_LOSS_WHY
            if buy and mark is not None and mark > buy * (1 - STOP_LOSS) and row.get("lossTicks"):
                row["lossTicks"] = 0
                changed = True
            # His first target: the stock reaches the high-OI wall that was ahead of it.
            stock, target = _num(t.get("stockLast")), _num(row.get("targetStrike"))
            stock_at = _ts(t.get("stockLastAt"))
            at_target = (stock is not None and target is not None and stock_at is not None
                         and 0 <= now_ts - stock_at <= STOCK_FRESH_SECONDS
                         and (stock <= target if row.get("side") == "bear" else stock >= target))
            if why:
                row["sellTries"] = 0
                self._sell(client, key, row, held, why, bid, ask, stamp)
                changed = True
            elif (not row.get("partialDone") and held >= 2 and buy and mark is not None
                  and (mark / buy - 1 >= PARTIAL_AT or at_target)):
                row["sellTries"] = 0
                first = PARTIAL_WHY if mark / buy - 1 >= PARTIAL_AT else TARGET_WHY
                self._sell(client, key, row, partial_qty(int(row.get("qty") or held), held), first, bid, ask, stamp)
                # "1 cons move to BE": the runner can no longer turn into a loss,
                # whichever of the two targets came first.
                if row.get("stop") is None or float(row["stop"]) < buy:
                    row["stop"] = round(buy, 2)
                changed = True
        return changed

    # ------------------------------------------------------- the chart plan
    #: What an order keeps of the Bot trade's chart plan (momx/option_track.py works it out
    #: once per signal from the option chain, with the chart's own formulas).
    PLAN_KEYS = ("side", "expiry", "strike", "entrySpot", "expectedMove", "delta", "gamma", "t1", "t2", "t3",
                 "t3FromOi", "chartExpiry", "chartStrike", "chainAt", "walls")

    def _plan_attach(self, row: dict, t: Mapping) -> bool:
        """Copy the signal's chart plan onto the order once the Bot has it (it may arrive a few
        seconds after the order: the Bot reads the chain in the background). Only a plan for
        the SAME contract counts. True when it was attached now."""
        if isinstance(row.get("plan"), dict):
            return False
        plan = t.get("plan") if isinstance(t, Mapping) else None
        if not isinstance(plan, Mapping) or _num(plan.get("strike")) is None:
            return False
        if _num(row.get("strike")) is not None and float(plan["strike"]) != float(row["strike"]):
            return False
        row["plan"] = {k: plan.get(k) for k in self.PLAN_KEYS}
        return True

    @staticmethod
    def _plan_stop(row: dict) -> bool:
        """The stop and the targets, once the order has both its fill and its plan. The Bot
        worked the plan out at the SIGNAL; the order goes out a little later (median about
        2 minutes, up to 10), so everything is re-based on ONE stock price - the stock
        price when the order went out - exactly as the chart does when a fill is typed
        (chart_plan.rebase = buildProjectRoi from that stock price):
          stop      the STOCK price where the option is down 50% from the FILL;
          T1/T2/T3  half the expected move, the full move, and the chart's open-interest
                    strike past the move, all from that same stock price.
        Before 2026-11-13 the targets stayed at the signal's stock price while the
        break-even stop used the order's: a stock already past T1 at the order was bought
        at the ask, "hit T1" on the first tick and was stopped at break-even a cent later.
        Set once; target hits move the stop later. No plan, or no way down to half -> no
        stock stop (the option's own -50% backstop still stands). True when it was set now."""
        plan, fill = row.get("plan"), _num(row.get("buyFill"))
        if not isinstance(plan, Mapping) or not fill or row.get("stopSet"):
            return False
        spot = _num(row.get("entryStock")) or _num(plan.get("entrySpot"))
        try:
            based = chart_plan.rebase(plan, fill, spot)
        except Exception:  # noqa: BLE001 - a plan that cannot be re-based keeps its signal-time targets
            based = None
        if based:
            kept = {k: v for k, v in plan.items() if k != "walls"}
            kept.setdefault("signalSpot", plan.get("entrySpot"))
            kept.setdefault("signalTargets", [plan.get("t1"), plan.get("t2"), plan.get("t3")])
            kept.update(entrySpot=based["entrySpot"], t1=based["t1"], t2=based["t2"], t3=based["t3"],
                        t3FromOi=based["t3FromOi"])
            row["plan"] = kept
            premium, stop = based["stopPremium"], based["stopStock"]
        else:
            premium, stop = chart_plan.restop(plan, fill, spot)
        row.update(stopSet=True, stopPremium=premium, stopStock=stop, stopStock0=stop, stopKind="stop", entryStock=spot)
        return True

    @staticmethod
    def _plan_stock(row: dict, t: Mapping, live_stock: float | None, now_ts: float, stamp: str) -> float | None:
        """The stock's live price for a chart-plan row: this tick's quote, else the Bot's last
        board price while fresh. Kept on the row for the Paper page."""
        stock = live_stock
        if stock is None:
            seen = _ts(t.get("stockLastAt"))
            if seen is not None and 0 <= now_ts - seen <= STOCK_FRESH_SECONDS:
                stock = _num(t.get("stockLast"))
        if stock is not None:
            row.update(stockLast=stock, stockAt=stamp)
        return stock

    @staticmethod
    def _plan_urgent(row: Mapping, stock: float | None, mark: float | None,
                     quote_age: float | None) -> tuple[str | None, float | None]:
        """(reason, stop price) when a chart-plan position must be sold in full NOW: the stock
        traded through the stop, or the option itself is down 50% on a fresh quote. Else (None, None)."""
        stop, buy = _num(row.get("stopStock")), _num(row.get("buyFill"))
        if stock is not None and stop is not None and (stock >= stop if row.get("side") == "bear" else stock <= stop):
            lead = {"break-even": "Stop at break-even", "T1": "Stop at T1"}.get(row.get("stopKind"), "Stop")
            return f"{lead}: stock traded through {stop:.2f}", stop
        if (buy and mark is not None and mark <= buy * (1 - STOP_LOSS)
                and quote_age is not None and -5 <= quote_age <= STOP_QUOTE_SECONDS):
            # The backstop: the option itself is down 50% on a fresh quote. First tick, no wait.
            return BACKSTOP_WHY, None
        return None, None

    def _plan_due(self, row: dict, stock: float | None, mark: float | None, quote_age: float | None,
                  stamp: str) -> str | None:
        """_plan_urgent, LATCHED on the row (2026-11-13 review). Once the stock has traded
        through the stop (or the option's own -50% backstop has shown) the stop is DUE and
        stays due: row["stopDue"] = {why, at, stock, stop}. Before this, a stop seen while a
        target lot was still at the broker (its cancel pending for a tick or two) was
        forgotten when the stock bounced a few cents back: the lot was re-priced at the mid
        and the rest held with no stop. Now everything is sold as soon as the working order
        is out of the way, wherever the stock is by then. Returns the reason, or None."""
        due = row.get("stopDue")
        if isinstance(due, dict) and due.get("why"):
            return str(due["why"])
        why, stop = self._plan_urgent(row, stock, mark, quote_age)
        if why:
            row["stopDue"] = {"why": why, "at": stamp, "stock": stock, "stop": stop}
        return why

    def _manage_plan(self, client, key: str, row: dict, t: Mapping, held: int, bid: Any, ask: Any,
                     mark: float | None, quote_age: float | None, live_stock: float | None, final: bool,
                     now_ts: float, stamp: str) -> None:
        """One tick of one chart-plan position (status open or exit_due). See EXIT_PLAN."""
        self._plan_attach(row, t)
        self._plan_stop(row)
        buy = _num(row.get("buyFill"))
        bear = row.get("side") == "bear"
        stock = self._plan_stock(row, t, live_stock, now_ts, stamp)
        if buy and mark is not None and mark / buy - 1 > float(row.get("peak") or 0):
            row["peak"] = round(mark / buy - 1, 4)
        why, urgent = None, False
        stop_why = self._plan_due(row, stock, mark, quote_age, stamp)
        if row.get("carried"):
            why = "Prior-session exit still unfilled"
        elif stop_why:
            why, urgent = stop_why, True
        elif final:
            why = "15:52 close"
        elif t.get("exitPrice") is not None or t.get("status") == "closed":
            said = str(t.get("exitReason") or "")
            why = BOT_EXIT_WORDS.get(said) or ("Bot exit: " + said)
        pending = row.get("status") == "exit_due"
        if why and (not pending or int(row.get("sellQty") or 0) < held or (urgent and not row.get("sellUrgent"))):
            # Everything goes, at once. A target lot still being worked is replaced by this.
            # The try counter is NOT reset here: the order id is built from (sales booked,
            # tries), and the broker refuses an id it has seen. A reset made the stop re-use
            # the id of the cancelled, unfilled sale of this leg, so the stop was refused and
            # reached the broker one tick later for every earlier re-price (2026-11-13 review:
            # 30 mid re-prices -> the stop went out 310 s late). The counter only goes back to
            # 0 when a sale FILLS (_follow_row), which also changes the id's first part.
            self._sell(client, key, row, held, why, bid, ask, stamp, urgent=urgent)
            return
        if pending:                                   # re-price the same leg, the same way
            if self._sale_held(row, now_ts):
                return                                # the broker refused it: not every tick
            self._sell(client, key, row, min(held, int(row.get("sellQty") or held)),
                       row.get("sellWhy") or "exit", bid, ask, stamp)
            return
        # Targets on the stock price: T1, T2, T3, one lot each.
        plan = row.get("plan") if isinstance(row.get("plan"), Mapping) else {}
        levels = [_num(plan.get(k)) for k in ("t1", "t2", "t3")]
        done = len(row.get("hits") or [])
        if stock is None or done >= 3:
            return
        reached = done
        while reached < 3 and levels[reached] is not None and (stock <= levels[reached] if bear else stock >= levels[reached]):
            reached += 1
        if reached == done:
            return
        split = row.get("planSplit") if isinstance(row.get("planSplit"), list) and len(row.get("planSplit")) == 3 \
            else chart_plan.split_contracts(row.get("buyFilledQty") or row.get("qty"))
        qty = min(held, sum(int(n) for n in split[done:reached]))
        last = reached >= 3 or sum(int(n) for n in split[reached:]) <= 0 or qty >= held
        if last:
            qty = held
        for i in range(done, reached):
            row.setdefault("hits", []).append({"target": f"T{i + 1}", "at": stamp, "stock": stock, "level": levels[i]})
        name = f"T{reached}"
        if last:
            reason = f"{name} hit: sold the last {qty}"
        else:
            # "1 cons move to BE": after T1 the rest cannot turn into a loss on the stock;
            # after T2 the stop follows up to T1.
            entry_stock = _num(row.get("entryStock"))
            if reached == 1 and entry_stock:
                row.update(stopStock=round(entry_stock, 2), stopKind="break-even")
            elif reached == 2 and levels[0] is not None:
                row.update(stopStock=levels[0], stopKind="T1")
            moved = "stop to break-even" if reached == 1 else "stop to T1"
            reason = f"{name} hit: sold {qty}, {moved}" if qty > 0 else f"{name} hit: {moved}"
        if qty > 0:
            self._sell(client, key, row, qty, reason, bid, ask, stamp, urgent=False)
        else:
            row["note"] = reason

    def _entries(self, client, bot, now: datetime, now_ts: float, stamp: str) -> bool:
        if not entry_open(now):
            return False
        if self._hold_buys:
            return False                      # paused, or the daily loss stop: exits only
        live = sum(1 for r in self._rows.values() if r.get("status") in LIVE)
        entries = sum(1 for r in self._rows.values() if r.get("buyId") and not r.get("carried"))
        held = {r.get("symbol") for r in self._rows.values() if r.get("status") in LIVE}
        changed = False
        quote_cache: dict = {}
        # NEWEST signal first. There are far more Level signals than slots (176
        # on 2026-11-09 against 8), and oldest-first handed every freed slot to
        # a signal about 10 minutes old, whose mid order then missed. At the
        # same moment the HIGHEST Level row goes first.
        queue = sorted(bot.items(), key=_newest_first, reverse=True)
        # The AI bot: signals the AI has ranked go first, in its order; anything
        # it has not seen yet keeps the rules order behind them. A missing or
        # stale ranking changes nothing - the rules order is the fallback.
        rank = self._ai_rank
        if rank and 0 <= now_ts - float(rank.get("at") or 0) <= RANK_FRESH_SECONDS:
            place = {k: i for i, k in enumerate(rank.get("order") or [])}
            ranked_at = float(rank.get("at") or 0)

            def position(kv):
                if kv[0] in place:
                    return (1, place[kv[0]])
                # Not ranked. A signal NEWER than the ranking goes ahead of the
                # ranked ones (the rules bot would take it first too, and the AI
                # has simply not seen it yet); an older one waits behind them.
                return (0, 0) if (_ts(kv[1][1].get("at")) or 0) > ranked_at else (2, 0)

            queue.sort(key=position)          # stable: ties keep the rules order
        else:
            rank, place = None, {}
        # One Level signal (same ticker, same moment) is one order, whatever
        # became of it: a missed Level 3 buy is not tried again as Level 2.
        taken = {(r.get("side"), r.get("symbol"), r.get("at")) for r in self._rows.values() if r.get("rule") in _LEVELS}
        # Tickers whose earlier Level order today is still LIVE (being bought,
        # held or sold). The Bot records each level once per ticker per day, so
        # a ticker that starts as Level 2 and is upgraded minutes later gets a
        # lone Level 3 row with a new time (25 of 152 tickers on 2026-11-09).
        # An upgrade while that earlier order is still live counts for its own
        # level only - the ticker's Level 1 / Level 2 trade is the earlier
        # order. Once the earlier order has CLOSED (or never filled), a new
        # Level signal is a fresh entry (option_track's re-entry) and counts
        # for every level it stands at, like any first signal.
        seen_today = _live_level_tickers(self._rows.values())
        # Orders closed or missed so far (a change frees buying power: fund skips are re-checked).
        done = sum(1 for r in self._rows.values() if r.get("status") not in LIVE)
        # Level signals the chart check already turned down today (one signal = one verdict).
        chart_failed = {(v.get("side"), v.get("symbol"), v.get("at")) for v in self._chart_skips.values()
                        if isinstance(v, dict)}
        # Signals the top-of-board check turned down for the day (a rank fail; a short hold is retried).
        top_failed = self._top_final()
        # A run he started with a reset (momx/bot_accounts.py): its per-trade money replaces
        # BUDGET and a buy must fit in the run's free money. None (no reset, or the run could
        # not be read) = every line below does exactly what it did before.
        cap = None
        if self._run_cap is not None:
            try:
                cap = self._run_cap(self.account, self.directory, now.date().isoformat())
            except Exception:  # noqa: BLE001 - bookkeeping must never stop the paper bot
                cap = None
        for key, (side, t) in queue:
            if live >= MAX_OPEN or entries >= MAX_ENTRIES_PER_DAY:
                break
            allowed = None if self.rules is None else self.rules.get(side)
            if allowed is not None and t.get("rule") not in allowed:
                continue
            signal = (side, t.get("symbol"), t.get("at"))
            if t.get("rule") in _LEVELS and signal in taken:
                continue
            if key in self._rows or t.get("symbol") in held or not eligible(t, now_ts, self.jev_cut, self.leg):
                continue
            # ONE clock for "bought" and "has a chart plan" (2026-11-13 review): the Bot decides
            # whether a Level signal gets a plan (option_track.plan_open, from the later of its
            # signal and entry times), and a signal without one is never bought - so no paper
            # order can go out with no stock stop and no T1 / T2 / T3. Checked again on later
            # ticks while the signal is fresh (the plan is written when the trade opens).
            if t.get("rule") in _LEVELS and not has_plan(t, self.leg):
                continue
            # His top-of-board rule (2026-11-10, "Mag7 top 3", "use H/L column top"): a Level
            # signal from Mag7 / the Watchlist needs its ticker in the board's top N by H/L on the
            # CURRENT scan, and from 9:45 on every scan for the last 5 minutes (top_verdict, on
            # the Bot book's live run). Free - no quote, no chart - so it runs first. Out of the
            # top N is a rules skip for the day; a hold still building (or a book still warming
            # up after a restart) is checked again on later ticks while the signal is fresh.
            if t.get("rule") in _LEVELS and signal in top_failed:
                continue
            top_state, top_why = self._top_check(side, t, now)
            if top_state != TOP_OK:
                final = top_state == TOP_FAIL
                if final:
                    top_failed.add(signal)    # one signal (side, ticker, time) is one skip
                changed |= self._top_skip(key, side, t, top_why or "not top of board", stamp, final)
                continue
            if t.get("rule") in _LEVELS and self._top_pass(key, side, t, stamp):
                changed = True                # passes now: marked passed, no stale reason left behind
            # His step 3, the chart check (his rule 2026-11-10): calls need the
            # 5m price above the 21 EMA and puts below it, with at least
            # chart_check.ROOM_PCT of room before the next pivot / previous-day /
            # premarket level. A fail is a rules skip (no order), remembered for
            # the day so the Scorecard can say why. The chart is frozen on the
            # signal, so one signal is judged once. AI plays no part here.
            if t.get("rule") in _LEVELS and signal in chart_failed:
                continue
            chart_said, chart_why = self._chart_verdict(side, t)
            if chart_said == chart_check.FAILED:
                chart_failed.add(signal)
                changed |= self._skip_for_chart(key, side, t, chart_why or "chart check failed", stamp)
                continue
            if self._chart_skips.pop(key, None):  # passes now: no stale reason left behind
                changed = True
            # Buying power, cheap checks first (each live quote is a synchronous broker call
            # under the mirror lock): a signal already found unaffordable after the latest
            # account read, with no order closed since, is not quoted again; nor is one whose
            # Bot quote alone (MIN_MULTI contracts at its mid) is more than is free.
            if self._fund_hold(key, now_ts, done):
                continue
            free = self._free_buying_power(now_ts)
            bot_q = bot_quote(t, now_ts, self.leg) if free is not None else None
            bot_mid = _ask(*bot_q) if bot_q and sellable(*bot_q) else None   # the buy goes out at the ask
            if bot_mid is not None and bot_mid <= MAX_PRICE and bot_mid * MIN_MULTI * 100 > free:
                need = bot_mid * MIN_MULTI * 100
                why = f"not enough buying power (${need:,.0f} needed, ${max(free, 0):,.0f} free)"
                changed |= self._skip_for_funds(key, side, t, why, stamp, done)
                continue
            contract = leg_get(t, "contract", self.leg)
            # A live broker quote first (the Bot's own can be minutes old), else
            # the Bot's quote while it is fresh (v2, 2026-11-04).
            live_q = self._live_quote(contract, now_ts, quote_cache, stock=t.get("symbol"))
            q = live_q or bot_quote(t, now_ts, self.leg)
            if not q:
                continue
            bid, ask = q
            if not sellable(bid, ask):
                continue                      # $0 bid or a penny option: it could not be sold back
            # The chart plan's entry (his decision 2026-11-12): a limit at the ASK, not the mid.
            limit = _ask(bid, ask)
            qty = size_for(limit) if cap is None else size_for(limit, cap.per_trade)
            if not qty:
                continue
            # Buying power (2026-11-10): every buy is 3+ contracts up to $8.00
            # (up to $2,400) and the paper account can hold far less. A buy the
            # account cannot pay for is a rules skip with the reason saved for
            # the Scorecard; the contract count is never cut. Unknown or stale
            # buying power never blocks. Re-checked after the next account read
            # or once an order closes, while the signal is still eligible.
            needed = qty * limit * 100
            if free is not None and needed > free:
                why = f"not enough buying power (${needed:,.0f} needed, ${max(free, 0):,.0f} free)"
                changed |= self._skip_for_funds(key, side, t, why, stamp, done)
                continue
            if cap is not None:
                # The run's own books: its money + closed profit or loss - what is held or on order.
                run_free = cap.free(self._rows)
                if needed > run_free:
                    why = f"no free money in this run (${needed:,.0f} needed, ${max(run_free, 0):,.0f} free)"
                    changed |= self._skip_for_funds(key, side, t, why, stamp, done)
                    continue
            if self._fund_skips.pop(key, None):   # paid for now: no stale reason left behind
                self._fund_seen.pop(key, None)
                changed = True
            # Research must see the same fresh quote and time as this order,
            # not the earlier virtual signal entry or a future broker fill.
            policy_trade = {**t, "contract": contract, "strike": leg_get(t, "strike", self.leg),
                            "delta": leg_get(t, "delta", self.leg), "entry": limit, "entryBid": bid,
                            "entryAsk": ask, "entryAt": stamp}
            # AI is asked for its opinion and the answer is kept on the order
            # for the learning team - but it can never stop the buy (his
            # "Don't block any trade using AI", 2026-11-09). No answer, an
            # error or a "no" all end the same way: the trade is taken.
            ai_decision = None
            if self._ai_policy is not None:
                try:
                    ai_decision = self._ai_policy(side, policy_trade, now_ts)
                except Exception:
                    ai_decision = None
                if not isinstance(ai_decision, dict):
                    ai_decision = {"enabled": True, "approved": True, "mode": "post_trade",
                                   "reason": "AI gave no usable answer; the trade is taken anyway"}
                self._ai_status = ai_decision
            cid = self._cid(key, "b")
            row = {"side": side, "rule": t.get("rule"), "symbol": t.get("symbol"), "at": t.get("at"),
                   "contract": contract, "qty": qty, "jevP": t.get("jevP"), "botEntry": leg_get(t, "entry", self.leg),
                   "buyLimit": limit, "buyId": cid, "buyAt": stamp, "status": "buying",
                   "exec": EXEC_VERSION, "buyTries": 0, "spreadAtOrder": spread_share(bid, ask),
                   # Robinhood-style fill likelihood (momx/fill_chance.py): shown, never acted on.
                   "fillChance": fill_chance.stamp(bid, ask),
                   "quoteVia": "live" if live_q else "bot", "leg": self.leg,
                   "delta": leg_get(t, "delta", self.leg), "strike": leg_get(t, "strike", self.leg),
                   # The chart plan: bought at the ask; the quote and the stock price at the order.
                   "exitPlan": EXIT_PLAN, "entryBid": _num(bid), "entryAsk": _num(ask), "entryMid": _mid(bid, ask)}
            stock_now = quote_cache.get(("stock", t.get("symbol")))
            if stock_now is None:
                seen_at = _ts(t.get("stockLastAt"))
                if seen_at is not None and 0 <= now_ts - seen_at <= STOCK_FRESH_SECONDS:
                    stock_now = _num(t.get("stockLast"))
            if stock_now is None and isinstance(t.get("plan"), Mapping):
                stock_now = _num(t["plan"].get("entrySpot"))
            row["entryStock"] = stock_now
            self._plan_attach(row, t)
            if self.picker is not None:
                # How this order was chosen, for the Scorecard's rules-against-AI comparison.
                row["aiPick"] = ({"rank": place[key] + 1, "of": rank.get("of"), "why": (rank.get("why") or {}).get(key, ""),
                                  "source": rank.get("source")} if rank and key in place else None)
            if isinstance(t.get("levelSignal"), dict):
                top = t["levelSignal"].get("highestLevel")
                row["levelAtSignal"] = top
                levels = _levels_for(side, t, seen_today, top)
                if levels is not None:
                    row["levels"] = levels
            if t.get("rule") in _LEVELS:
                taken.add(signal)
                seen_today.add((side, t.get("symbol")))
            from momx.paper_experiments import inputs
            row["entryInputs"] = inputs(policy_trade)
            from momx.chart_learning import snapshot as chart_snapshot
            chart_inputs = chart_snapshot(t)
            if chart_inputs:
                row['chartContext'] = chart_inputs
            # "passed", or "not checked" (no usable chart, or not a Level signal): missing data never blocks.
            row["chartCheck"] = chart_said
            from momx.scanner_learning import snapshot as scanner_snapshot
            captured = scanner_snapshot(t, side)
            if captured:
                row['scannerInputs'] = captured
            if ai_decision:
                row["aiDecision"] = ai_decision
                if ai_decision.get("approved") is False:
                    row["aiWouldSkip"] = True     # its advice, recorded; the buy still goes out
            try:
                client.submit_option_limit_order(contract, qty, limit, cid, "buy_to_open")
                live += 1
                held.add(t.get("symbol"))
            except Exception as exc:  # noqa: BLE001 - submission outcome may be unknown
                row.update(status="buying", note=f"Awaiting broker reconciliation: {exc}"[:200])
                if paper_control.broker_answered(exc):
                    row["submitError"] = str(exc)[:300]   # the broker's answer; a network error is not a refusal
                live += 1
                held.add(t.get("symbol"))
            self._rows[key] = row
            # One bought signal is not also "skipped" at its other levels.
            self._drop_skips_for(signal)
            entries += 1
            changed = True
        return changed

    # ------------------------------------------------------------------ read
    def status(self) -> dict:
        # Broker calls run under the mutation lock. A read must never wait for
        # network reconciliation: the atomic day files are a saved fallback.
        acquired = self._lock.acquire(blocking=False)
        source, stamp, day = "memory", datetime.now(ET).isoformat(), self._day
        try:
            if acquired:
                rows = copy.deepcopy(list(self._rows.values()))
            else:
                source, rows, stamp = "unavailable", [], None
                for path in sorted(self.directory.glob("20*.json"), reverse=True):
                    try:
                        saved = json.loads(path.read_text(encoding="utf-8"))
                        if not isinstance(saved.get("rows"), dict):
                            continue
                        rows = list(saved["rows"].values())
                        day = saved.get("day") or path.stem
                        stamp = datetime.fromtimestamp(path.stat().st_mtime, ET).isoformat()
                        source = "saved"
                        break
                    except (OSError, ValueError, AttributeError):
                        continue
        finally:
            if acquired:
                self._lock.release()
        rows.sort(key=lambda r: str(r.get("buyAt") or ""), reverse=True)
        closed = [r for r in rows if r.get("status") == "closed" and r.get("pl") is not None]
        return {
                "account": self.account, "day": day, "enabled": not self._off() and not self._refused,
                "reconciling": not acquired, "snapshotSource": source, "snapshotAt": stamp,
                "refused": self._refused, "error": self._error,
                "holdBuys": self._hold_buys, "dailyStop": dict(self._daily_stop) if self._daily_stop else None,
                "broker": dict(self._broker) if self._broker else None,
                # How many of today's signals the chart check turned down (reasons: day file "chartSkips").
                "chartSkipped": len(self._chart_skips),
                # How many were not bought for want of buying power (reasons: day file "fundSkips").
                "fundSkipped": len(self._fund_skips),
                # How many were not in their board's top N by H/L for 5 min (reasons: day file "topSkips").
                "topSkipped": self._top_skip_count(),
                "picker": ({**self.picker.status(), "rankedAt": (self._ai_rank or {}).get("at"),
                            "source": (self._ai_rank or {}).get("source")} if self.picker is not None else {"on": False}),
                "ai": dict(self._ai_status),
                "rules": {"book": self.book,
                          "picked": {side: sorted(r) for side, r in (self.rules or {}).items()},
                          "contract": "0.20 delta (OTM, next week's expiry)" if self.leg == "entry" else "nearest OTM strike, next week's expiry",
                          "ai": "AI never blocks a buy; its opinion is recorded on the order",
                          "orders": f"buy: limit at the live ask; unfilled after {BUY_TTL_SECONDS:.0f} s, re-sent at the ask "
                                    f"(up to {BUY_REPRICES} times); target and close sells: limit at the mid; "
                                    "stop sells: limit at the bid",
                          "entry": "Setup 3+ with a confirm - Level 1: one of RVOL / SQZ / Skittles, Level 2: two, "
                                   "Level 3: all three; the ticker in its board's top by H/L (Mag7 top 3, "
                                   "Watchlist top 7; calls highest H/L, puts lowest) on every scan for the last "
                                   "5 min at buy time (a hold still "
                                   "building is checked again while the signal is fresh); then the 5m chart check: "
                                   "price above the 21 EMA for calls "
                                   f"(below for puts) and at least {chart_check.ROOM_PCT:g}% of room before the next "
                                   "pivot / previous-day / premarket level; no chart data = not checked, never blocked",
                          "sizing": f"up to ${MAX_PRICE:.2f}: at least {MIN_MULTI} contracts (more when they fit in "
                                    f"${BUDGET:.0f}), so one lot can come off at each target; over ${MAX_PRICE:.2f}: skip",
                          "entryTimes": "new buys 9:35-11:30 AM and 1:00-3:30 PM ET (midday skipped; a signal from the "
                                        "10 minutes before 1:00 is bought at 1:00; a Level signal with no chart plan "
                                        "is not bought)",
                          # Which exit plan this running bot follows (the Bot page shows the
                          # chart-plan text only when the server says so).
                          "plan": EXIT_PLAN,
                          "exitPlan": "the chart's Entry / Target / Stop plan. Stop: the stock price where the option is down 50% "
                                      "from the fill; all sold the moment the stock trades through it (backstop: the option "
                                      f"itself down 50% on a quote under {STOP_QUOTE_SECONDS:.0f} s old); the stop is checked every tick, also "
                                      "while a target sale is still working. Targets on the stock, measured from the stock "
                                      "price when the order went out: "
                                      "T1 = half the expected move: sell 1 of 3 and stop to break-even (the stock price at entry); "
                                      "T2 = the full expected move: sell 1 and stop to T1; T3 = the biggest open-interest strike "
                                      "past the move (1.5x the move when there is none): sell the last. 2 contracts: T1 and T2; "
                                      "1 contract: T2. Also the Bot's exit (a 5-minute candle closing past the 21 EMA) and the "
                                      "15:52 close; fills not guaranteed",
                          "planSince": "orders from before the chart plan keep their older exit plan until closed",
                          "maxOpen": MAX_OPEN, "maxEntriesPerDay": MAX_ENTRIES_PER_DAY},
                "totals": {"entries": sum(1 for r in rows if r.get("buyId")),
                           "filled": sum(1 for r in rows if r.get("buyFill") is not None),
                           "missed": sum(1 for r in rows if r.get("status") == "missed"),
                           "open": sum(1 for r in rows if r.get("status") in ("open", "selling", "exit_due")),
                           "closed": len(closed), "wins": sum(1 for r in closed if r["pl"] > 0),
                           "pl": round(sum(r["pl"] for r in closed), 2)},
                "rows": rows,
            }


def _history(self: "PaperMirror", days: int = 1) -> list[dict]:
    """The last `days` saved day files, newest first: [{day, rows}] (Bot page tabs)."""
    out = []
    try:
        files = sorted(self.directory.glob("20*.json"), reverse=True)[:max(1, int(days))]
    except OSError:
        files = []
    for f in files:
        if f.stem == self._day:
            if self._lock.acquire(blocking=False):
                try:
                    out.append({"day": self._day, "rows": copy.deepcopy(list(self._rows.values()))})
                finally:
                    self._lock.release()
                continue
            # The broker is busy. Read the atomically saved file below instead
            # of blocking history behind order/expiration reconciliation.
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            out.append({"day": doc.get("day") or f.stem, "rows": list((doc.get("rows") or {}).values())})
        except (OSError, ValueError):
            continue
    # Carried positions appear in more than one snapshot. Show their newest
    # broker state once, under the original entry day, without rewriting files.
    seen = set()
    grouped = {}
    for day_doc in out:
        for row in day_doc["rows"]:
            key = row.get("buyId") or (row.get("side"), row.get("rule"), row.get("symbol"), row.get("at"))
            if key in seen:
                continue
            seen.add(key)
            original_day = str(row.get("buyAt") or day_doc["day"])[:10]
            grouped.setdefault(original_day, []).append(row)
    return [{"day": day, "rows": grouped[day]} for day in sorted(grouped, reverse=True)]


PaperMirror.history = _history


_MIRRORS: list[PaperMirror] = []


def _run_cap(account: str, folder: Path, day: str) -> Any:
    """The account's run, when he started one with a reset (momx/bot_accounts.py); else None."""
    from momx import bot_accounts
    return bot_accounts.paper_cap(account, folder, day)


def start(books: Mapping[str, Any], directory: Path) -> list[PaperMirror]:
    """Start one mirror per ACCOUNTS entry, one loop thread (momx_worker). Idempotent."""
    if not _MIRRORS:
        from momx.paper_learning import trade_first
        from momx.paper_experiments import paper3_policy, paper5_policy
        for account, (label, cut) in ACCOUNTS.items():
            _MIRRORS.append(PaperMirror(books, Path(directory) / account, account=account, book=label, jev_cut=cut,
                                        rules=ACCOUNT_RULES.get(account), leg=PAPER_LEG,
                                        ai_policy=paper5_policy if account == "paper5" else paper3_policy,
                                        run_cap=_run_cap))
            _MIRRORS[-1]._ai_status = trade_first()

        def loop() -> None:
            while True:
                for mirror in _MIRRORS:
                    try:
                        mirror.tick()
                    except Exception:  # noqa: BLE001
                        pass
                time.sleep(TICK_SECONDS)

        threading.Thread(target=loop, name="momx-paper-mirror", daemon=True).start()
    return _MIRRORS


def current() -> list[PaperMirror]:
    return list(_MIRRORS)
