"""Bot accounts (2026-11-26): starting money, a reset and past runs for the four
accounts on the Bot page.

    paper3 / paper5   the Alpaca PAPER accounts (momx/paper_mirror.py day files)
    research          the virtual bot: every scanner signal scored at the mid (momx/option_track.py)
    ticker            the simulated Ticker trader (momx/ticker_trader.py)

Each account has RUNS. A run = {n, money, perTrade, startedAt, endedAt, capped}. Until he
resets an account it has an implicit run 1 (nothing is stored for it). A reset ends the
current run - its numbers are saved as they stand at that moment, so later code changes
cannot alter a past run - and starts run n+1 with the new money, `capped` true.

BOOKKEEPING ONLY. Nothing here places, cancels or changes an order and nothing here calls a
broker: it reads the saved day files and keeps one small JSON file of runs. The one thing the
live paper bot reads from here is `paper_cap` (its per-trade money and the run's free money),
and only for a run a reset created.

What the numbers mean:
    value      the run's money + totalPnl
    totalPnl   closed profit or loss since the run started + openPnl
    openPnl    what the open trades have already sold for a profit or loss, plus the rest at
               its last price (a trade with no price yet adds nothing)
    invested   what the contracts still held cost
    free       the run's money + closed profit or loss + what open trades already banked
               - invested - buys sent and still waiting to fill
    dayPnl     closed profit or loss of today (ET) + the openPnl of trades OPENED today
               (a trade carried from an earlier day adds only when it closes)
"""
from __future__ import annotations

import heapq
from array import array
import json
import os
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple
from zoneinfo import ZoneInfo

from momx import bot_desk

ET = ZoneInfo("America/New_York")
ORDER = ("paper3", "paper5", "research", "ticker")
LABELS = {"paper3": "Paper 3", "paper5": "Paper 5", "research": "Research", "ticker": "Ticker"}
KINDS = {"paper3": "paper", "paper5": "paper", "research": "virtual", "ticker": "simulated"}
NOTES = {
    "paper3": "An Alpaca paper account: real paper orders, counted only when they fill.",
    "paper5": "An Alpaca paper account: real paper orders, counted only when they fill.",
    "research": "The virtual bot: every scanner signal priced at the middle of the bid and ask, no order is sent.",
    "ticker": "The Ticker trader: simulated trades on the tickers you type, no order is sent.",
}
START_MONEY = 10_000.0
MIN_MONEY = 500.0
MAX_MONEY = 1_000_000.0
MIN_PER_TRADE = 50.0
#: Per-trade money a reset uses when none is sent and the run before it had none.
DEFAULT_PER_TRADE = 300.0
RUNS_FILE = "runs.json"
ORDERS_MAX = 50
#: Open trades listed per account (Research can hold hundreds at once); `positionsTotal` is the full count.
POSITIONS_MAX = 100
LOG_MAX = 200
#: The longest one read of the Research history may take. The files are about 13 MB a day;
#: each is read once and kept, so a first open after a restart may need two or three reads.
RESEARCH_BUDGET_SECONDS = 4.0
#: How long a read waits for another one that is going on before it answers "still reading".
RESEARCH_WAIT_SECONDS = 2.0
#: The newest saved days kept trade by trade in the first run (its latest orders and open trades).
RESEARCH_FULL_DAYS = 3
#: Today's Research file is rewritten all day: a copy this fresh is reused.
RESEARCH_REUSE_SECONDS = 30.0
STILL_READING = "Still reading the Research trade history. Open this again in a few seconds."
PAPER_STATUS = {"buying": "Waiting to fill", "open": "Open", "selling": "Selling", "exit_due": "Selling",
                "closed": "Closed", "missed": "Not filled"}


class AccountsError(Exception):
    """A refusal in plain words, with its HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- small helpers
def _f(value: Any) -> float | None:
    return float(value) if bot_desk._num(value) else None


def _ts(stamp: Any) -> float | None:
    """Epoch seconds of an ISO stamp; one without an offset is read as ET."""
    try:
        at = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return (at if at.tzinfo else at.replace(tzinfo=ET)).timestamp()


def _et_day(stamp: Any) -> str:
    """The ET date an ISO stamp falls on ("" when it is not a time)."""
    try:
        at = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return ""
    return (at.astimezone(ET) if at.tzinfo else at).date().isoformat()


def _iso(now: datetime) -> str:
    return now.astimezone(ET).replace(microsecond=0).isoformat()


def _day_start(day: str) -> str:
    """Midnight ET of a YYYY-MM-DD day, as an ISO stamp with its ET offset."""
    return datetime.combine(date.fromisoformat(day), datetime.min.time(), ET).isoformat()


def _mid(bid: Any, ask: Any) -> float | None:
    b, a = _f(bid), _f(ask)
    return round((b + a) / 2, 4) if b is not None and a is not None and b >= 0 and a > 0 and a >= b else None


def contract_text(occ: Any, strike: Any = None, side: Any = None, expiry: Any = None) -> str:
    """A contract in a few words: "150C · Dec 4". From the option symbol
    (AAPL  261204C00150000) when it reads as one, else from the parts given."""
    text = str(occ or "").strip()
    tail = text[-15:]
    if len(text) >= 16 and tail[:6].isdigit() and tail[6] in "CP" and tail[7:].isdigit():
        try:
            when = date(2000 + int(tail[:2]), int(tail[2:4]), int(tail[4:6]))
            return f"{int(tail[7:]) / 1000:g}{tail[6]} · {when:%b} {when.day}"
        except ValueError:
            pass
    letter = {"CALL": "C", "PUT": "P", "bull": "C", "bear": "P"}.get(str(side or ""), "")
    out = f"{float(strike):g}{letter}" if bot_desk._num(strike) else ""
    try:
        when = date.fromisoformat(str(expiry)[:10])
        out = f"{out} · {when:%b} {when.day}".strip(" ·")
    except ValueError:
        pass
    return out or text


def journal(closed_trades: Iterable[Mapping]) -> dict:
    """The journal of a run from its closed trades [{day: "YYYY-MM-DD" (ET), pl: dollars}]:
    {"tiles": {...}, "days": [{day, pnl, trades}]} with the days in date order.

    A break-even trade (pl == 0) is neither a win nor a loss. profitFactor = dollars won /
    dollars lost; None unless there is at least one win AND one loss (never infinity).
    Money is rounded to cents."""
    pls, by_day = [], {}
    for t in closed_trades:
        pl = _f(t.get("pl")) if isinstance(t, Mapping) else None
        if pl is None:
            continue
        pls.append(pl)
        d = by_day.setdefault(str(t.get("day") or "")[:10], [0.0, 0])
        d[0] += pl
        d[1] += 1
    wins, losses = [p for p in pls if p > 0], [p for p in pls if p < 0]
    won, lost = sum(wins), -sum(losses)
    days = [{"day": day, "pnl": round(by_day[day][0], 2), "trades": by_day[day][1]} for day in sorted(by_day)]
    green, red = sum(1 for d in days if d["pnl"] > 0), sum(1 for d in days if d["pnl"] < 0)
    tiles = {
        "netPnl": round(sum(pls), 2), "closed": len(pls), "wins": len(wins), "losses": len(losses),
        "winPct": round(len(wins) / len(pls) * 100, 1) if pls else None,
        "profitFactor": round(won / lost, 2) if wins and losses and lost > 0 else None,
        "greenDays": green, "redDays": red,
        "dayWinPct": round(green / len(days) * 100, 1) if days else None,
        "avgWin": round(won / len(wins), 2) if wins else None,
        "avgLoss": round(-lost / len(losses), 2) if losses else None,
    }
    return {"tiles": tiles, "days": days}


# ---------------------------------------------------------------- Research (the virtual bot)
class VTrade(NamedTuple):
    """One virtual trade of momx/option_track.py, on the contract the Research page follows
    (the 0.20-delta one, the e-prefixed fields), sold by the bot's own exit, at the mid.

    UNIT: `entry`, `exit` and `mark` are OPTION PRICES in dollars per share, exactly as the
    virtual bot records them (it records no dollar P/L and no contract count). One contract is
    100 shares, so one contract costs entry x 100 dollars and makes (exit - entry) x 100."""
    side: str
    rule: str
    symbol: str
    contract: str
    day: str
    entry_at: str
    entry_ts: float
    entry: float
    exit_at: str | None
    exit_ts: float | None
    exit: float | None
    mark: float | None


def virtual_trade(t: Mapping, side: str, day: str, today: str) -> VTrade | None:
    """The Research result of one saved virtual trade, or None when it has no price (the same
    rules as the Research page, frontend/src/momxBot.js botLegResult for contract "entry",
    exit "exit", fills "mid"): a bad quote counts nowhere; the entry and the exit are the mid
    of the recorded bid / ask, else the recorded price; a sold price with a $0.00 bid is $0.00.
    A trade of an earlier day that never got an exit price has no result."""
    if not isinstance(t, Mapping) or t.get("badQuote") or not t.get("symbol") or not t.get("rule"):
        return None
    if _f(t.get("eEntryBid")) is not None and _f(t.get("eEntryAsk")) is not None:
        entry = _mid(t.get("eEntryBid"), t.get("eEntryAsk"))
    else:
        entry = _f(t.get("eEntry"))
    if entry is None or entry <= 0:
        return None
    entry_at = str(t.get("entryAt") or t.get("at") or "")
    entry_ts = _ts(entry_at)
    if entry_ts is None:
        return None
    exited = _f(t.get("eExit")) is not None
    over = t.get("status") == "closed" or t.get("exitPrice") is not None or bool(t.get("exitAt"))
    if not exited and (over or day < today):
        return None
    pre = "eExit" if exited else "eLast"
    bid, ask = _f(t.get(pre + "Bid")), _f(t.get(pre + "Ask"))
    price = _f(t.get("eExit" if exited else "eLast"))
    if bid is not None and ask is not None:
        price = _mid(bid, ask)
    if exited and bid == 0:
        price = 0.0
    if exited and price is None:
        return None
    exit_at = str(t.get("eExitPriceAt") or t.get("exitPriceAt") or t.get("exitAt") or t.get("closeAt") or "") if exited else None
    exit_ts = (_ts(exit_at) or entry_ts) if exited else None
    return VTrade(side, str(t.get("rule")), str(t.get("symbol")), str(t.get("eContract") or ""), day, entry_at, entry_ts,
                  entry, exit_at or (entry_at if exited else None), exit_ts, price if exited else None,
                  None if exited else price)


class ResearchReader:
    """The saved virtual trades. A day file is about 13 MB, so each is read once and only what
    is needed is kept (past days never change):

      * a day the run needs trade by trade (the newest RESEARCH_FULL_DAYS files, and every day
        since a reset) keeps its VTrades;
      * an older day of the first, uncapped run keeps only each closed trade's one-contract
        profit or loss (that run counts every trade at one contract, so the list is enough);
      * a day from before a reset is not read at all.
    """

    def __init__(self, dirs: Callable[[], list], clock: Callable[[], float] = time.monotonic,
                 budget: float = RESEARCH_BUDGET_SECONDS) -> None:
        self._dirs, self._clock, self._budget = dirs, clock, budget
        self._lock = threading.Lock()
        self._files: dict[str, tuple] = {}      # path -> (version, read at, array of closed P/L, [VTrade] | None)

    def _listing(self) -> list[tuple[str, Path]]:
        from momx import option_track
        return [(side, path) for side, base in self._dirs() for path in option_track._day_files(Path(base))]

    def first_day(self) -> str | None:
        """The first saved trade day (file names only: nothing is read)."""
        days = [path.stem for _, path in self._listing()]
        return min(days) if days else None

    def read(self, today: str, run: Mapping, wait: float = RESEARCH_WAIT_SECONDS) -> tuple | None:
        """(trades, old, signature) for one run: its VTrades, and for the uncapped run's older
        days [(day, array of one-contract P/L)]. None = not all read yet: another read is
        going on, or the time allowed ran out (every call reads at least one file, so the
        next ones get there)."""
        if not self._lock.acquire(timeout=max(0.0, wait)):
            return None
        try:
            listing = sorted(self._listing(), key=lambda x: x[1].stem, reverse=True)       # newest first
            days = sorted({path.stem for _, path in listing}, reverse=True)
            capped = bool(run.get("capped"))
            full_from = _et_day(run.get("startedAt")) if capped else (days[:RESEARCH_FULL_DAYS] or [today])[-1]
            started, trades, old, complete, signature, parsed = self._clock(), [], [], True, [], 0
            for side, path in listing:
                day = path.stem
                want_full = day >= full_from
                if capped and not want_full:
                    continue                                   # before the reset: not this run's
                try:
                    info = path.stat()
                except OSError:
                    continue
                version, hit = (info.st_mtime_ns, info.st_size), self._files.get(str(path))
                stale = hit is not None and hit[0] != version and self._clock() - hit[1] >= RESEARCH_REUSE_SECONDS
                if hit is None or stale or (want_full and hit[3] is None):
                    if parsed and self._clock() - started > self._budget:
                        complete = False
                        continue
                    parsed += 1
                    try:
                        doc = json.loads(path.read_bytes().decode("utf-8"))
                    except (OSError, ValueError, UnicodeDecodeError):
                        continue
                    found = doc.get("trades") if isinstance(doc, dict) else None
                    made = [v for v in (virtual_trade(t, side, day, today) for t in found or []) if v]
                    pls = array("d", [(v.exit - v.entry) * 100 for v in made if v.exit is not None])
                    hit = (version, self._clock(), pls, made if want_full else None)
                    self._files[str(path)] = hit
                signature.append((str(path), hit[0], want_full))
                if want_full:
                    trades.extend(hit[3])
                else:
                    old.append((day, hit[2]))
            return (trades, old, (today, tuple(signature))) if complete else None
        finally:
            self._lock.release()

    def warm(self, today: Callable[[], str], run: Callable[[], Mapping], pause: float = 0.25, sleep=time.sleep) -> None:
        """Read the history one file at a time with a pause between files, so the first open of
        the page after a restart does not wait for it (momx_worker starts this on a thread)."""
        slow = ResearchReader(self._dirs, self._clock, budget=-1.0)
        slow._lock, slow._files = self._lock, self._files
        for _ in range(2000):
            if slow.read(today(), run(), wait=30.0) is not None:
                return
            sleep(pause)


def _per_trade_at(run: Mapping, ts: float) -> float:
    """The run's per-trade money at a moment (a change applies from when it was made)."""
    amount = float(run.get("perTrade") or 0.0)
    log = [x for x in run.get("perTradeLog") or [] if isinstance(x, Mapping)]
    if log:
        amount = float(log[0].get("perTrade") or amount)
        for x in log:
            if (_ts(x.get("at")) or 0.0) <= ts:
                amount = float(x.get("perTrade") or amount)
    return amount


def research_books(trades: Iterable[VTrade], run: Mapping, old: Iterable[tuple] = ()) -> dict:
    """The virtual trades of one run as an account. `old`: the uncapped run's older days as
    [(day, one-contract P/L of each closed trade)] (ResearchReader.read).

    UNCAPPED (run 1): every priced trade counts, ONE contract each - the Research page's own
    numbers. CAPPED (after a reset): walked in entry-time order. Contracts =
    max(1, int(perTrade // (entry x 100))); a trade whose ONE contract costs more than perTrade
    is left out (`skippedTooBig`); a trade is taken only when its cost fits in the free cash at
    its entry time (the run's money + profit or loss of trades already sold - cost of trades
    still open at that moment), else it is counted in `skippedNoMoney`. The virtual bot records
    per-share option prices (see VTrade), so a taken trade's dollars are
    (exit - entry) x 100 x contracts."""
    start = _ts(run.get("startedAt")) or 0.0
    capped = bool(run.get("capped"))
    mine = sorted((t for t in trades if t.entry_ts >= start), key=lambda t: t.entry_ts)
    cash, selling = float(run.get("money") or 0.0), []
    closed, positions, orders, no_money, too_big = [], [], [], 0, 0
    if not capped:
        closed.extend({"day": day, "pl": round(pl, 2)} for day, pls in old for pl in pls)
    for t in mine:
        while selling and selling[0][0] <= t.entry_ts:
            cash += heapq.heappop(selling)[1]
        one = t.entry * 100
        qty = 1
        text = contract_text(t.contract, side=t.side)
        if capped:
            per_trade = _per_trade_at(run, t.entry_ts)
            if one > per_trade:
                too_big += 1
                orders.append((t.entry_ts, {"symbol": t.symbol, "contract": text, "qty": 0, "status": "Skipped: costs more than the per-trade money",
                                            "price": round(t.entry, 2), "at": t.entry_at}))
                continue
            qty = max(1, int(per_trade // one))
            if qty * one > cash + 1e-9:
                no_money += 1
                orders.append((t.entry_ts, {"symbol": t.symbol, "contract": text, "qty": 0, "status": "Skipped: no free money",
                                            "price": round(t.entry, 2), "at": t.entry_at}))
                continue
        cost = qty * one
        cash -= cost
        if t.exit is not None:
            pl = round((t.exit - t.entry) * 100 * qty, 2)
            heapq.heappush(selling, (t.exit_ts, cost + pl))
            closed.append({"day": _et_day(t.exit_at) or t.day, "pl": pl})
            orders.append((t.exit_ts, {"symbol": t.symbol, "contract": text, "qty": qty, "status": "Closed",
                                       "price": round(t.entry, 2), "at": t.entry_at}))
        else:
            unreal = round((t.mark - t.entry) * 100 * qty, 2) if t.mark is not None else None
            positions.append({"symbol": t.symbol, "contract": text, "qty": qty, "entry": round(t.entry, 2),
                              "mark": round(t.mark, 2) if t.mark is not None else None, "pnl": unreal,
                              "pct": round(unreal / cost * 100, 1) if unreal is not None and cost else None,
                              "at": t.entry_at, "_cost": cost, "_banked": 0.0, "_unreal": unreal or 0.0})
            orders.append((t.entry_ts, {"symbol": t.symbol, "contract": text, "qty": qty, "status": "Open",
                                        "price": round(t.entry, 2), "at": t.entry_at}))
    orders.sort(key=lambda x: x[0], reverse=True)
    positions.sort(key=lambda p: str(p["at"]), reverse=True)
    return {"closed": closed, "positions": positions, "orders": [o for _, o in orders[:ORDERS_MAX]],
            "skippedNoMoney": no_money, "skippedTooBig": too_big, "reserved": 0.0}


# ---------------------------------------------------------------- Paper 3 / Paper 5
def _paper_row_money(r: Mapping) -> tuple[float, float, float | None, int] | None:
    """A filled, still held paper order (bot_desk's own notion: a buy fill and a HOLDING
    status): (cost of what is still held, profit or loss already banked by part-sales, the
    rest at its last mid or None when it has no price, contracts held). None = not held."""
    fill = _f(r.get("buyFill"))
    if fill is None or r.get("status") not in bot_desk.HOLDING:
        return None
    qty = bot_desk._filled_qty(r)
    sells = [x for x in (r.get("sells") or []) if isinstance(x, Mapping)]
    priced = [x for x in sells if bot_desk._num(x.get("qty")) and bot_desk._num(x.get("price"))]
    gone = sum(x["qty"] for x in priced)
    # The bot's own count of what is still held wins over the arithmetic (as on the Scorecard).
    held = r["held"] if bot_desk._num(r.get("held")) and r["held"] >= 0 else max(qty - gone, 0)
    banked = sum((x["price"] - fill) * 100 * x["qty"] for x in priced)
    mark = _f(r.get("markMid"))
    unreal = (mark - fill) * 100 * held if mark is not None and len(priced) == len(sells) else None
    return fill * 100 * held, banked, unreal, int(held)


def _paper_waiting(r: Mapping) -> float:
    """What a buy that is sent and not filled yet would cost at its limit (0 when it is not one)."""
    if r.get("status") != "buying" or _f(r.get("buyFill")) is not None:
        return 0.0
    qty, limit = _f(r.get("qty")), _f(r.get("buyLimit"))
    return qty * limit * 100 if qty and limit else 0.0


def _closed_in_run(r: Mapping, start: float) -> bool:
    if not bot_desk._closed(r):
        return False
    at = _ts(r.get("closedAt") or r.get("at"))
    return at is not None and at >= start


def paper_books(rows: Iterable[Mapping], run: Mapping) -> dict:
    """One paper account's saved order rows as the run's account.

    Closed trades: rows closed (bot_desk._closed: status closed, a buy fill, a numeric pl)
    with `closedAt` inside the run. Open trades: rows filled and still held.
    The implicit run 1 counts what the Scorecard counts: orders signalled since its start day
    that were opened under the plan being judged (bot_desk.old_plan_after_switch)."""
    start = _ts(run.get("startedAt")) or 0.0
    rows = [r for r in rows if isinstance(r, Mapping) and r.get("status") != "skipped"]
    if not run.get("capped"):
        first = str(run.get("startedAt") or "")[:10]
        rows = [r for r in rows if bot_desk._day(r.get("at")) >= first and not bot_desk.old_plan_after_switch(r)]
    closed, positions, orders, reserved = [], [], [], 0.0
    for r in rows:
        text = contract_text(r.get("contract"), r.get("strike"), r.get("side"))
        held = _paper_row_money(r)
        last = str(r.get("closedAt") or r.get("buyFillAt") or r.get("buyAt") or r.get("at") or "")
        in_run = (_ts(last) or 0.0) >= start
        if _closed_in_run(r, start):
            closed.append({"day": _et_day(r.get("closedAt")) or bot_desk._day(r.get("at")), "pl": float(r["pl"])})
            in_run = True
        elif held is not None:
            cost, banked, unreal, left = held
            fill = float(r["buyFill"])
            pnl = round(banked + unreal, 2) if unreal is not None else None
            whole = fill * 100 * bot_desk._filled_qty(r)
            positions.append({"symbol": r.get("symbol"), "contract": text, "qty": left, "entry": round(fill, 2),
                              "mark": _f(r.get("markMid")) if unreal is not None else None, "pnl": pnl,
                              "pct": round(pnl / whole * 100, 1) if pnl is not None and whole else None,
                              "at": r.get("buyFillAt") or r.get("buyAt") or r.get("at"),
                              "_cost": cost, "_banked": banked, "_unreal": unreal or 0.0})
            in_run = True
        else:
            waiting = _paper_waiting(r)
            reserved += waiting
            in_run = in_run or waiting > 0
        if in_run:
            price = _f(r.get("buyFill"))
            orders.append((last, {"symbol": r.get("symbol"), "contract": text,
                                  "qty": bot_desk._filled_qty(r) if price is not None else _f(r.get("qty")),
                                  "status": PAPER_STATUS.get(str(r.get("status")), str(r.get("status") or "Unknown")),
                                  "price": price if price is not None else _f(r.get("buyLimit")), "at": last or None}))
    orders.sort(key=lambda x: x[0], reverse=True)
    positions.sort(key=lambda p: str(p["at"] or ""), reverse=True)
    return {"closed": closed, "positions": positions, "orders": [o for _, o in orders[:ORDERS_MAX]],
            "skippedNoMoney": 0, "reserved": round(reserved, 2)}


def _saved_rows(folder: Path) -> list[dict]:
    """Every saved order of one paper account, its newest saved state (bot_desk.load_rows)."""
    return bot_desk.load_rows(Path(folder), "0000-00-00", "9999-99-99")


class RunCap:
    """What the live paper bot reads for a run a reset created (momx/paper_mirror.py
    PaperMirror._entries): the money one buy may use, and the run's free money."""

    def __init__(self, per_trade: float, base: float, start: float) -> None:
        self.per_trade, self._base, self._start = float(per_trade), float(base), float(start)

    def free(self, rows: Mapping[str, Mapping]) -> float:
        """The run's money + closed profit or loss + what open trades banked - what the held
        contracts cost - buys sent and still waiting. `rows`: the paper bot's own {key: order}
        of today (with the orders it carried in); earlier days are already in the base."""
        free = self._base
        for r in rows.values():
            if not isinstance(r, Mapping):
                continue
            if _closed_in_run(r, self._start):
                free += float(r["pl"])
                continue
            held = _paper_row_money(r)
            if held is not None:
                free += held[1] - held[0]
            else:
                free -= _paper_waiting(r)
        return free


# ---------------------------------------------------------------- Ticker
def ticker_books(snapshot: Mapping) -> dict:
    """The Ticker trader's own account (TickerTrader.snapshot()) in the same shape."""
    closed, positions, orders = [], [], []
    for run in snapshot.get("runs") or []:
        if not isinstance(run, Mapping):
            continue
        for t in run.get("trades") or []:
            if not isinstance(t, Mapping) or _f(t.get("pl")) is None:
                continue
            text = contract_text(t.get("contract"), t.get("strike"), t.get("side"), t.get("expiry"))
            closed.append({"day": _et_day(t.get("closedAt")) or str(t.get("day") or ""), "pl": float(t["pl"])})
            orders.append((str(t.get("closedAt") or ""), {"symbol": t.get("ticker"), "contract": text, "qty": _f(t.get("qty")),
                                                         "status": "Closed", "price": _f(t.get("entry")), "at": t.get("boughtAt")}))
        t = run.get("open")
        if isinstance(t, Mapping):
            text = contract_text(t.get("contract"), t.get("strike"), t.get("side"), t.get("expiry"))
            entry, held, bid = _f(t.get("entry")) or 0.0, int(_f(t.get("held")) or 0), _f(t.get("bid"))
            banked = _f(t.get("pl")) or 0.0
            # At its last bid; at what it cost until a bid has been seen (TickerTrader.account()).
            unreal = (bid - entry) * 100 * held if bid is not None and bid >= 0 else None
            pnl = round(banked + unreal, 2) if unreal is not None else None
            whole = _f(t.get("cost")) or 0.0
            positions.append({"symbol": t.get("ticker"), "contract": text, "qty": held, "entry": round(entry, 2),
                              "mark": bid if unreal is not None else None, "pnl": pnl,
                              "pct": round(pnl / whole * 100, 1) if pnl is not None and whole else None,
                              "at": t.get("boughtAt"), "_cost": entry * 100 * held, "_banked": banked, "_unreal": unreal or 0.0})
            orders.append((str(t.get("boughtAt") or ""), {"symbol": t.get("ticker"), "contract": text, "qty": _f(t.get("qty")),
                                                         "status": "Open", "price": round(entry, 2), "at": t.get("boughtAt")}))
    orders.sort(key=lambda x: x[0], reverse=True)
    positions.sort(key=lambda p: str(p["at"] or ""), reverse=True)
    return {"closed": closed, "positions": positions, "orders": [o for _, o in orders[:ORDERS_MAX]],
            "skippedNoMoney": 0, "reserved": 0.0}


# ---------------------------------------------------------------- the accounts
def account_body(key: str, run: Mapping, books: Mapping | None, past: list, today: str, error: str | None = None) -> dict:
    """One account of the GET answer, from its run and its books (None = could not be read)."""
    books = books or {"closed": [], "positions": [], "orders": [], "skippedNoMoney": 0, "reserved": 0.0}
    money = float(run.get("money") or 0.0)
    made = books.get("_journal") or journal(books["closed"])
    closed_pnl = made["tiles"]["netPnl"]
    positions = books["positions"]
    invested = sum(p["_cost"] for p in positions)
    banked = sum(p["_banked"] for p in positions)
    open_pnl = round(banked + sum(p["_unreal"] for p in positions), 2)
    total = round(closed_pnl + open_pnl, 2)
    day_pnl = sum(t["pl"] for t in books["closed"] if t["day"] == today) + \
        sum(p["_banked"] + p["_unreal"] for p in positions if _et_day(p["at"]) == today)
    body = {
        "key": key, "label": LABELS[key], "kind": KINDS[key], "note": NOTES[key],
        "run": {k: run.get(k) for k in ("n", "money", "perTrade", "startedAt", "capped")},
        "value": round(money + total, 2),
        "free": round(money + closed_pnl + banked - invested - float(books.get("reserved") or 0.0), 2),
        "invested": round(invested, 2), "dayPnl": round(day_pnl, 2), "openPnl": open_pnl, "totalPnl": total,
        "positions": [{k: v for k, v in p.items() if not k.startswith("_")} for p in positions[:POSITIONS_MAX]],
        "positionsTotal": len(positions),
        "orders": books["orders"], "journal": made,
        "skippedNoMoney": int(books.get("skippedNoMoney") or 0),
        "pastRuns": list(reversed(past)), "resets": len(past),
        "canTradeTicker": key == "ticker", "error": error,
    }
    for extra in ("skippedTooBig", "reserved"):
        if books.get(extra):
            body[extra] = books[extra]
    return body


class BotAccounts:
    def __init__(self, directory: Path, paper_folder: Callable[[str], Path | None],
                 research_dirs: Callable[[], list], ticker: Callable[[], Any],
                 clock: Callable[[], datetime] | None = None) -> None:
        self.directory = Path(directory)
        self._paper_folder, self._ticker = paper_folder, ticker
        self._research = ResearchReader(research_dirs)
        self._clock = clock or (lambda: datetime.now(ET))
        self._lock = threading.Lock()        # the saved runs: held briefly (the paper bot reads through it)
        self._writing = threading.Lock()     # one reset / change at a time
        self._doc: dict = {"version": 1, "accounts": {}, "log": []}
        self._past_cache: dict = {}
        self._research_memo: tuple | None = None
        self._load()

    # ---- file -----------------------------------------------------------
    def _load(self) -> None:
        try:
            doc = json.loads((self.directory / RUNS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(doc, dict) and isinstance(doc.get("accounts"), dict):
            self._doc = {"version": 1, "log": doc.get("log") if isinstance(doc.get("log"), list) else [],
                         "accounts": {k: v for k, v in doc["accounts"].items() if k in ORDER and isinstance(v, dict)}}

    def _save(self, doc: dict) -> None:
        """Temp file, then replace: a crash mid-write never leaves half a file."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / RUNS_FILE
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    # ---- runs -----------------------------------------------------------
    def _stored(self, account: str) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._doc["accounts"].get(account) or {}))

    def _past(self, account: str) -> list:
        return [p for p in self._stored(account).get("past") or [] if isinstance(p, dict)]

    def _run(self, account: str, first_day: str | None = None, ticker: Mapping | None = None) -> dict:
        """The account's current run: the stored one, else the implicit run 1."""
        stored = self._stored(account)
        n = len([p for p in stored.get("past") or [] if isinstance(p, dict)]) + 1
        if account == "ticker":
            # Whatever the Ticker trader itself reports: its starting size, and the time of its last reset.
            from momx import ticker_trader
            acct = (ticker or {}).get("account") or {}
            money = _f(acct.get("startBalance")) or ticker_trader.START_BALANCE
            made = sorted(str(r.get("createdAt")) for r in (ticker or {}).get("runs") or [] if isinstance(r, Mapping) and r.get("createdAt"))
            started = acct.get("resetAt") or (made[0] if made else None) or (ticker or {}).get("asOf")
            # Its own sizing: up to 10% of the account a trade, and never more than the cash left.
            return {"n": n, "money": money, "perTrade": round(money * ticker_trader.MAX_TRADE_SHARE, 2),
                    "startedAt": started, "capped": True}
        run = stored.get("run")
        if isinstance(run, dict) and run.get("startedAt"):
            return run
        if account == "research":
            # Every signal, one contract each: there is no per-trade money until a reset sets one.
            day = first_day or self._clock().astimezone(ET).date().isoformat()
            return {"n": 1, "money": START_MONEY, "perTrade": None, "startedAt": _day_start(day), "capped": False}
        from momx import paper_mirror
        return {"n": 1, "money": START_MONEY, "perTrade": float(paper_mirror.BUDGET),
                "startedAt": _day_start(bot_desk.EVALUATION_START), "capped": False}

    def _research_run(self) -> dict:
        return self._run("research", first_day=self._research.first_day())

    def warm_research(self) -> None:
        """Read the Research history ahead of the first open (a thread momx_worker starts)."""
        self._research.warm(lambda: self._clock().astimezone(ET).date().isoformat(), self._research_run)

    # ---- read -----------------------------------------------------------
    def _account(self, account: str, now: datetime) -> dict:
        """One account's body. Raises when its trades cannot be read."""
        today = now.astimezone(ET).date().isoformat()
        past = self._past(account)
        if account == "ticker":
            trader = self._ticker()
            if trader is None:
                raise AccountsError(503, "The Ticker trader is not running yet.")
            snap = trader.snapshot()
            return account_body(account, self._run(account, ticker=snap), ticker_books(snap), past, today)
        if account == "research":
            run = self._research_run()
            got = self._research.read(today, run)
            if got is None:
                return account_body(account, run, None, past, today, STILL_READING)
            trades, old, signature = got
            memo_key = (signature, json.dumps(run, sort_keys=True))
            memo = self._research_memo
            if memo is None or memo[0] != memo_key:
                made = research_books(trades, run, old)
                made["_journal"] = journal(made["closed"])       # 100,000+ trades: worked out once per read
                memo = (memo_key, made)
                self._research_memo = memo
            return account_body(account, run, memo[1], past, today)
        run = self._run(account)
        folder = self._paper_folder(account)
        rows = _saved_rows(folder) if folder is not None and Path(folder).is_dir() else []
        return account_body(account, run, paper_books(rows, run), past, today)

    def account(self, account: str, now: datetime | None = None) -> dict:
        """One account; a failure to read it is its `error` sentence, never an exception."""
        now = now or self._clock()
        try:
            return self._account(account, now)
        except AccountsError as exc:
            why = exc.message
        except Exception as exc:  # noqa: BLE001 - one account failing must not fail the others
            why = f"This account could not be read ({type(exc).__name__})."
        today = now.astimezone(ET).date().isoformat()
        try:
            run = self._research_run() if account == "research" else self._run(account)
        except Exception:  # noqa: BLE001
            run = {"n": len(self._past(account)) + 1, "money": START_MONEY, "perTrade": None, "startedAt": None, "capped": False}
        return account_body(account, run, None, self._past(account), today, why)

    def snapshot(self, now: datetime | None = None) -> dict:
        now = now or self._clock()
        return {"ok": True, "asOf": _iso(now), "accounts": {key: self.account(key, now) for key in ORDER},
                "order": list(ORDER), "limits": {"minMoney": MIN_MONEY, "maxMoney": MAX_MONEY, "minPerTrade": MIN_PER_TRADE}}

    # ---- his buttons ----------------------------------------------------
    @staticmethod
    def _known(account: Any) -> str:
        if not isinstance(account, str) or account not in ORDER:
            raise AccountsError(400, "Pick one of the four accounts: Paper 3, Paper 5, Research or Ticker.")
        return account

    @staticmethod
    def _dollars(value: Any, what: str) -> float:
        amount = None
        if isinstance(value, str) and value.strip():
            try:
                amount = float(value.replace(",", "").replace("$", ""))
            except ValueError:
                amount = None
        elif bot_desk._num(value):
            amount = float(value)
        if amount is None or amount != amount or amount in (float("inf"), float("-inf")):
            raise AccountsError(400, f"{what} must be a number of dollars (for example 10000).")
        return round(amount, 2)

    def _money(self, value: Any) -> float:
        money = self._dollars(value, "Starting money")
        if not MIN_MONEY <= money <= MAX_MONEY:
            raise AccountsError(400, f"Starting money must be between ${MIN_MONEY:,.0f} and ${MAX_MONEY:,.0f}.")
        return money

    def _per_trade(self, value: Any, money: float) -> float:
        per_trade = self._dollars(value, "Money per trade")
        if not MIN_PER_TRADE <= per_trade <= money:
            raise AccountsError(400, f"Money per trade must be between ${MIN_PER_TRADE:,.0f} and the account's ${money:,.0f}.")
        return per_trade

    def _commit(self, change: Callable[[dict], None], entry: dict) -> dict:
        """Apply a change to a copy of the saved runs, save it, then make it the live copy.
        Returns the copy from before (for an undo). Nothing is kept in memory that was not saved."""
        with self._lock:
            before = json.loads(json.dumps(self._doc))
        doc = json.loads(json.dumps(before))
        change(doc)
        doc["log"] = (doc.get("log") or [])[-(LOG_MAX - 1):] + [entry]
        try:
            self._save(doc)
        except OSError as exc:
            raise AccountsError(409, f"Could not save the change, so nothing was changed: {exc}") from exc
        with self._lock:
            self._doc = doc
        self._past_cache.clear()
        self._research_memo = None
        return before

    def reset(self, account: Any, money: Any, per_trade: Any = None, who: str = "", now: datetime | None = None) -> dict:
        """End the account's current run (its numbers saved as they stand now) and start the next
        one with `money`. Bookkeeping only: no order is sent, changed or cancelled; a paper
        position still open stays open at the broker and counts in the new run when it closes."""
        key = self._known(account)
        amount = self._money(money)
        now = now or self._clock()
        stamp = _iso(now)
        with self._writing:
            current = self._account(key, now)
            if current.get("error"):
                raise AccountsError(409, f"Nothing was reset: {current['error']}")
            run = current["run"]
            if key == "ticker":
                from momx import ticker_trader
                share = round(amount * ticker_trader.MAX_TRADE_SHARE, 2)
            elif per_trade is None:
                share = min(float(run.get("perTrade") or DEFAULT_PER_TRADE), amount)
            else:
                share = self._per_trade(per_trade, amount)
            tiles = current["journal"]["tiles"]
            ended = {"n": run["n"], "money": run["money"], "perTrade": run.get("perTrade"), "startedAt": run.get("startedAt"),
                     "endedAt": stamp, "capped": bool(run.get("capped")), "endValue": current["value"], "pnl": current["totalPnl"],
                     "pnlPct": round(current["totalPnl"] / run["money"] * 100, 2) if run.get("money") else None,
                     "trades": tiles["closed"], "tiles": tiles, "days": current["journal"]["days"],
                     "openAtEnd": current["positionsTotal"], "skippedNoMoney": current["skippedNoMoney"]}
            fresh = {"n": run["n"] + 1, "money": amount, "perTrade": share, "startedAt": stamp, "endedAt": None,
                     "capped": True, "perTradeLog": [{"at": stamp, "perTrade": share}]}

            def change(doc: dict) -> None:
                slot = doc["accounts"].setdefault(key, {})
                slot["past"] = [p for p in slot.get("past") or [] if isinstance(p, dict)] + [ended]
                slot["run"] = fresh

            before = self._commit(change, {"at": stamp, "who": str(who or "")[:120], "action": "reset", "account": key,
                                           "money": amount, "perTrade": share, "endedRun": run["n"]})
            if key == "ticker":
                # Its own reset, with the new size: clears its simulated trades. Undone here if it refuses.
                trader = self._ticker()
                try:
                    if trader is None:
                        raise AccountsError(503, "The Ticker trader is not running yet.")
                    trader.reset(None, amount)
                except Exception as exc:  # noqa: BLE001
                    try:
                        self._save(before)
                    except OSError:
                        pass
                    with self._lock:
                        self._doc = before
                    message = getattr(exc, "message", None) or f"The Ticker trader could not be reset ({type(exc).__name__})."
                    raise AccountsError(int(getattr(exc, "status", 409) or 409), f"Nothing was reset: {message}") from exc
            return self.account(key, now)

    def set_per_trade(self, account: Any, per_trade: Any, who: str = "", now: datetime | None = None) -> dict:
        """Change the money one trade may use, from now on, in the current run."""
        key = self._known(account)
        now = now or self._clock()
        stamp = _iso(now)
        with self._writing:
            if key == "ticker":
                raise AccountsError(409, "The Ticker account sizes each trade itself: up to 10% of the account, 3 contracts at most. "
                                         "Only its starting money can be changed, with a reset.")
            run = self._run(key)
            if not run.get("capped"):
                raise AccountsError(409, f"{LABELS[key]} has not been reset yet, so it has no per-trade money to change. "
                                         "Reset it with a starting amount first.")
            share = self._per_trade(per_trade, float(run["money"]))

            def change(doc: dict) -> None:
                kept = doc["accounts"][key]["run"]
                kept["perTrade"] = share
                kept["perTradeLog"] = [x for x in kept.get("perTradeLog") or [] if isinstance(x, dict)] + [{"at": stamp, "perTrade": share}]

            self._commit(change, {"at": stamp, "who": str(who or "")[:120], "action": "perTrade", "account": key, "perTrade": share})
            return self.account(key, now)

    # ---- the live paper bot's one read ----------------------------------
    def paper_cap(self, account: str, folder: Path, day: str) -> RunCap | None:
        """None unless a reset created this paper account's current run. Else its per-trade
        money and a base for its free money: the run's money + the closed profit or loss saved
        in the day files BEFORE `day` (read once a day per run; today's orders are the paper
        bot's own, counted by RunCap.free). An order closed on an earlier day
        is only in that day's file and one still live is only in the bot's own rows, so
        nothing is counted twice."""
        if account not in ("paper3", "paper5"):
            return None
        with self._lock:
            run = (self._doc["accounts"].get(account) or {}).get("run")
            run = dict(run) if isinstance(run, dict) and run.get("capped") and run.get("startedAt") else None
        if run is None:
            return None
        start = _ts(run["startedAt"]) or 0.0
        key = (account, run.get("n"), run["startedAt"], day)
        past = self._past_cache.get(key)
        if past is None:
            past = sum(float(r["pl"]) for r in _keyed_rows(Path(folder), day).values() if _closed_in_run(r, start))
            self._past_cache = {key: past}
        return RunCap(float(run.get("perTrade") or 0.0), float(run.get("money") or 0.0) + past, start)


def _keyed_rows(folder: Path, before: str) -> dict[str, dict]:
    """{order key: its newest saved state} from the day files older than `before`."""
    rows: dict[str, dict] = {}
    for path in sorted(Path(folder).glob("20*.json"), reverse=True):
        if path.stem >= before:
            continue
        doc = bot_desk._read(path)
        found = doc.get("rows") if isinstance(doc, dict) else None
        for key, r in (found.items() if isinstance(found, dict) else ()):
            if key not in rows and isinstance(r, dict):
                rows[key] = r
    return rows


_BOOKS: list[BotAccounts] = []


def start(directory: Path, warm_after: float | None = 90.0) -> BotAccounts:
    """The one set of accounts of the scanner worker (momx_worker). Idempotent. Its one thread
    reads the Research history ahead of the first open (`warm_after` seconds after the start,
    one file at a time; None = no thread) and then ends."""
    if not _BOOKS:
        def paper_folder(account: str) -> Path:
            from momx import paper_mirror
            for mirror in paper_mirror.current():
                if mirror.account == account:
                    return mirror.directory
            from config import ARTIFACTS_DIR
            return Path(ARTIFACTS_DIR) / "momx_paper_mirror" / account

        def research_dirs() -> list:
            from momx import service
            return [("bull", service.grade_dir()), ("bear", service.grade_dir("bear"))]

        def ticker() -> Any:
            from momx import ticker_trader
            return ticker_trader.current()

        book = BotAccounts(Path(directory), paper_folder, research_dirs, ticker)
        _BOOKS.append(book)
        if warm_after is not None:
            def warm() -> None:
                try:
                    time.sleep(warm_after)
                    book.warm_research()
                except Exception:  # noqa: BLE001 - the page reads it itself when this could not
                    pass

            threading.Thread(target=warm, name="momx-bot-accounts-warm", daemon=True).start()
    return _BOOKS[0]


def current() -> BotAccounts | None:
    return _BOOKS[0] if _BOOKS else None


def paper_cap(account: str, folder: Path, day: str) -> RunCap | None:
    """For momx/paper_mirror.py: None (the paper bot trades exactly as before) unless the
    accounts are running here AND a reset created the account's current run."""
    book = current()
    return book.paper_cap(account, folder, day) if book is not None else None
