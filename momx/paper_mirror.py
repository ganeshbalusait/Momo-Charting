"""Alpaca PAPER mirror of the virtual Bot (2026-10-16, his "Do A and B" -> "B" -> "Go";
2026-10-17: paper3 = All trades, paper5 = Jev BOT >= 10%).

Reads the Bot's virtual trades (momx/option_track.py) and places PAPER orders
for the Jev BOT subset on one Alpaca paper account, so the Bot page can show
real paper fills next to the mid-price estimates. It never changes the Bot.

Safety:
* PAPER ONLY - refuses to run unless the account's credentials say paper.
* Off switch: env AGX_MOMX_PAPER_MIRROR=0 or the file artifacts/paper_mirror_off.
* Limit at the mid (his "use only mid order"), sized by price (see BUDGET). A
  buy not filled in BUY_TTL_SECONDS is cancelled. Exits follow his plan (see
  PARTIAL_AT) and the Bot's exit. All retries remain midpoint limits;
  unfilled exits remain visible and may stay open past FINAL_EXIT.
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

from momx import paper_control

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
#: up to $4.00 an option -> at least MIN_MULTI contracts, more when they fit in
#: BUDGET (0.25 -> 12, 0.50 -> 6, 1.00 -> 3); over $4.00 up to $8.00 -> 1; over
#: $8.00 -> skip.
BUDGET = 300.0
MIN_MULTI = 3
MULTI_UP_TO = 4.00
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
MAX_OPEN = 8
MAX_ENTRIES_PER_DAY = 25
BUY_TTL_SECONDS = 120.0
SELL_TTL_SECONDS = 60.0
TICK_SECONDS = 10.0
FINAL_EXIT = (15, 52)
#: When NEW paper buys may go in (2026-10-17, his "first 90 minutes ... and 2nd
#: half (1:00pm est)" -> "Yes do it"): 9:30-11:00 and 13:00-15:30 ET; the
#: 11:00-13:00 midday chop is skipped. Exits run all day.
ENTRY_WINDOWS = ((9, 30, 11, 0), (13, 0, 15, 30))


def entry_open(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return any(h1 * 60 + m1 <= m < h2 * 60 + m2 for h1, m1, h2, m2 in ENTRY_WINDOWS)
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
#: A buy the broker says it has no record of ("order not found") this long
#: after it was sent never reached the broker: it becomes "missed" instead of
#: holding one of the MAX_OPEN slots for days (five did on 2026-11-04/05).
ORPHAN_SECONDS = 300.0
#: The day's P/L must be past the daily loss stop on this many ticks in a row
#: before the stop latches, so one bad quote cannot end the day's buying.
STOP_TICKS = 3


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


def size_for(price: float | None) -> int | None:
    """Contracts to buy at this option price, per his plan; None = skip."""
    if price is None or price <= 0 or price > MAX_PRICE:
        return None
    if price <= MULTI_UP_TO:
        return max(MIN_MULTI, int(BUDGET // (price * 100)))
    return 1


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
                 ai_policy: Callable | None = None, leg: str = "main") -> None:
        self.books = dict(books)
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
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("rows"), dict):
                self._rows = doc["rows"]
            if isinstance(doc, dict) and isinstance(doc.get("dailyStop"), dict):
                self._daily_stop = doc["dailyStop"]      # a restart must not re-open a stopped day
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

    def _save(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self._path(self._day).with_suffix(".tmp")
            doc = {"day": self._day, "account": self.account, "rows": self._rows}
            if self._daily_stop:
                doc["dailyStop"] = self._daily_stop
            tmp.write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, self._path(self._day))
        except OSError:
            pass

    def _live_quote(self, contract: str, now_ts: float, cache: dict | None = None) -> tuple[Any, Any] | None:
        """A fresh broker (bid, ask) for one contract right now, or None.
        The Bot re-prices one ticker every 8+ s, so its own quote can be
        minutes old; the order uses the live quote whenever it can."""
        if not self._quotes_fetch or not contract:
            return None
        if cache is not None and contract in cache:
            return cache[contract]
        found = None
        try:
            doc = self._quotes_fetch([contract]) or {}
            seen = _num(doc.get("asOf"))
            if seen is not None and -5 <= now_ts - seen <= QUOTE_FRESH_SECONDS:
                q = (doc.get("quotes") or {}).get(contract) or {}
                if _mid(q.get("bid"), q.get("ask")) is not None:
                    found = (q.get("bid"), q.get("ask"))
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
        q = self._live_quote(row.get("contract"), now_ts) or (bot_quote(t, now_ts, leg) if t else None)
        limit = round(float(q[1]), 2) if q and sellable(*q) else None
        if limit is None or limit <= 0:
            row["note"] = "No fresh quote to re-send at the ask; not filled at the mid"
            return False
        cid = self._cid(key, "b", tries + 1)
        row.setdefault("buyHistory", []).append({"limit": row.get("buyLimit"), "at": row.get("buyAt"), "id": row.get("buyId")})
        row.update(buyId=cid, buyLimit=limit, buyAt=stamp, buyTries=tries + 1, note=f"Re-sent at the ask ${limit:.2f} (try {tries + 1})")
        try:
            client.submit_option_limit_order(row["contract"], int(row.get("qty") or 1), limit, cid, "buy_to_open")
        except Exception as exc:  # noqa: BLE001 - outcome unknown: reconcile by client id
            row["note"] = f"Awaiting broker reconciliation: {exc}"[:200]
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
            changed = False
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
            q = bot_quote(t, now_ts, self.leg)
            if not q or not sellable(*q) or not size_for(_mid(*q)):
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
        if self._daily_stop is None:
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
        elif self._daily_stop:
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
                if orphan:
                    row.update(status="missed", note="The broker has no record of this buy: it never reached the broker")
                else:
                    row["note"] = f"Awaiting broker reconciliation: {exc}"[:200]
                changed = True
        return changed

    def _follow_row(self, client, row: dict, now_ts: float, stamp: str, key: str | None = None,
                    bot: Mapping | None = None) -> bool:
        changed = False
        if row.get("status") == "buying":
            status, price, _, filled = self._state(client, row["buyId"])
            late = now_ts - (_ts(row.get("buyAt")) or now_ts) > BUY_TTL_SECONDS
            if status != "filled" and late:
                status, price, filled = self._cancel(client, row["buyId"])
            if status == "filled" or (filled > 0 and status in ("canceled", "expired", "rejected")):
                got = filled or int(row.get("qty") or 1)
                row.update(status="open", buyFill=price, buyFillAt=stamp, buyFilledQty=got, held=got, peak=0.0, sells=[])
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
                if self._hold_buys and status != "rejected":
                    note = f"Not filled at the mid; not re-sent at the ask ({self._hold_buys})"
                row.update(status="missed", note=note or f"buy {status or 'unfilled'} (not filled at the mid)")
                changed = True
        elif row.get("status") == "selling":
            status, price, _, filled = self._state(client, row["sellId"])
            late = now_ts - (_ts(row.get("sellAt")) or now_ts) > SELL_TTL_SECONDS
            if status != "filled" and late:
                status, price, filled = self._cancel(client, row["sellId"])
            if filled > 0 and status in ("filled", "canceled", "expired", "rejected"):
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
                changed = True
            elif status in ("canceled", "expired", "rejected"):
                row["status"] = "exit_due"                              # re-priced on the manage pass
                changed = True
        return changed

    def _sell(self, client, key: str, row: dict, qty: int, why: str, bid: Any, ask: Any, stamp: str) -> None:
        tries = int(row.get("sellTries") or 0)
        limit = _mid(bid, ask)
        if limit is None or limit <= 0:
            row.update(status="exit_due", sellQty=qty, sellWhy=why,
                       note="Waiting for a valid fresh bid/ask midpoint; no market fallback")
            return
        cid = self._cid(key, "s%d-" % len(row.get("sells") or []), tries)
        try:
            client.submit_option_limit_order(row["contract"], qty, limit, cid, "sell_to_close")
            row.update(sellId=cid, sellLimit=limit, sellAt=stamp, sellQty=qty, sellWhy=why, status="selling", note="")
        except Exception as exc:  # noqa: BLE001
            # A timeout can occur AFTER broker acceptance. Reconcile this exact
            # client id before any new attempt; do not create another order.
            row.update(sellId=cid, sellLimit=limit, sellAt=stamp, sellQty=qty,
                       sellWhy=why, status="selling", note=f"Awaiting broker reconciliation: {exc}"[:200])
        row["sellTries"] = tries + 1

    def _manage(self, client, bot, now: datetime, now_ts: float, stamp: str) -> bool:
        changed = False
        final = (now.hour, now.minute) >= FINAL_EXIT
        live_quotes = {}
        if self._quotes_fetch:
            contracts = sorted({r["contract"] for r in self._rows.values()
                                if r.get("status") in ("open", "selling", "exit_due") and r.get("contract")})
            if contracts:
                try:
                    doc = self._quotes_fetch(contracts) or {}
                    seen = _num(doc.get("asOf"))
                    if seen is not None and -5 <= now_ts - seen <= QUOTE_FRESH_SECONDS:
                        live_quotes = doc.get("quotes") or {}
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
            if row.get("status") == "selling":
                continue  # Pending orders remain exclusively broker-reconciled.
            if row.get("status") == "exit_due":                      # re-price the same leg
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
        # Tickers that already had a Level order today. The Bot records each
        # level once per ticker per day, so a ticker that starts as Level 2 and
        # is upgraded minutes later gets a lone Level 3 row with a new time (25
        # of 152 tickers on 2026-11-09). That upgrade is a new signal and may
        # be traded, but it counts for its own level only - the ticker's
        # Level 1 / Level 2 trade is the earlier order.
        seen_today = {(r.get("side"), r.get("symbol")) for r in self._rows.values()
                      if r.get("rule") in _LEVELS and not r.get("carried")}
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
            contract = leg_get(t, "contract", self.leg)
            # A live broker quote first (the Bot's own can be minutes old), else
            # the Bot's quote while it is fresh (v2, 2026-11-04).
            live_q = self._live_quote(contract, now_ts, quote_cache)
            q = live_q or bot_quote(t, now_ts, self.leg)
            if not q:
                continue
            bid, ask = q
            if not sellable(bid, ask):
                continue                      # $0 bid or a penny option: it could not be sold back
            limit = _mid(bid, ask)
            qty = size_for(limit)
            if not qty:
                continue
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
                   "quoteVia": "live" if live_q else "bot", "leg": self.leg,
                   "delta": leg_get(t, "delta", self.leg), "strike": leg_get(t, "strike", self.leg)}
            if self.picker is not None:
                # How this order was chosen, for the Scorecard's rules-against-AI comparison.
                row["aiPick"] = ({"rank": place[key] + 1, "of": rank.get("of"), "why": (rank.get("why") or {}).get(key, ""),
                                  "source": rank.get("source")} if rank and key in place else None)
            row["targetStrike"] = oi_target(side, t)      # his target: the high-OI wall ahead of the stock
            if isinstance(t.get("levelSignal"), dict):
                top = t["levelSignal"].get("highestLevel")
                row["levelAtSignal"] = top
                if t.get("rule") in _LEVELS and isinstance(top, int) and 1 <= top <= len(LEVEL_RULES):
                    # Every level this one order counts for - or, for a later
                    # upgrade on a ticker already ordered today, just its own.
                    row["levels"] = [t.get("rule")] if (side, t.get("symbol")) in seen_today else list(LEVEL_RULES[:top])
            if t.get("rule") in _LEVELS:
                taken.add(signal)
                seen_today.add((side, t.get("symbol")))
            from momx.paper_experiments import inputs
            row["entryInputs"] = inputs(policy_trade)
            from momx.chart_learning import snapshot as chart_snapshot
            chart_inputs = chart_snapshot(t)
            if chart_inputs:
                row['chartContext'] = chart_inputs
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
                live += 1
                held.add(t.get("symbol"))
            self._rows[key] = row
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
                "picker": ({**self.picker.status(), "rankedAt": (self._ai_rank or {}).get("at"),
                            "source": (self._ai_rank or {}).get("source")} if self.picker is not None else {"on": False}),
                "ai": dict(self._ai_status),
                "rules": {"book": self.book,
                          "picked": {side: sorted(r) for side, r in (self.rules or {}).items()},
                          "contract": "0.20 delta (OTM, next week's expiry)" if self.leg == "entry" else "nearest OTM strike, next week's expiry",
                          "ai": "AI never blocks a buy; its opinion is recorded on the order",
                          "orders": f"buy: limit at the live mid; unfilled after {BUY_TTL_SECONDS:.0f} s, re-sent at the ask "
                                    f"(up to {BUY_REPRICES} times); sell: limit at mid",
                          "sizing": f"up to ${MULTI_UP_TO:.2f}: {MIN_MULTI}+ contracts (as many as fit in ${BUDGET:.0f}); "
                                    f"over ${MULTI_UP_TO:.2f} to ${MAX_PRICE:.2f}: 1 contract; over ${MAX_PRICE:.2f}: skip",
                          "entryTimes": "new buys 9:30-11:00 AM and 1:00-3:30 PM ET (midday skipped)",
                          "exitPlan": "at +100% or when the stock reaches the high-OI level ahead: sell 2 of 3 and stop to break-even; "
                                      "+200%: stop to +100%; +300%: stop to +200% ...; -50%: sell all; else the Bot's exit "
                                      "(5m close below VWAP / 21 EMA); midpoint exit attempts from 15:52; fills not guaranteed",
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


def start(books: Mapping[str, Any], directory: Path) -> list[PaperMirror]:
    """Start one mirror per ACCOUNTS entry, one loop thread (momx_worker). Idempotent."""
    if not _MIRRORS:
        from momx.paper_learning import trade_first
        from momx.paper_experiments import paper3_policy, paper5_policy
        for account, (label, cut) in ACCOUNTS.items():
            _MIRRORS.append(PaperMirror(books, Path(directory) / account, account=account, book=label, jev_cut=cut,
                                        rules=ACCOUNT_RULES.get(account), leg=PAPER_LEG,
                                        ai_policy=paper5_policy if account == "paper5" else paper3_policy))
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
