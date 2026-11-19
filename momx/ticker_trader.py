"""Ticker trader (2026-11-13): he types a ticker, picks Call or Put, and the app
trades it by the chart's plan in ONE SIMULATED account shared by every ticker.

One account (2026-11-16, his "why each trade 2k ?" and "2k account reset but it will
reduce amount so I know how my account size in real loss or profit"): every ticker
spends from the same cash. A buy takes its cost out of the cash, a sale puts the
proceeds back, so the account reads like a real one. Only the starting size is
stored; cash = start + every trade's profit or loss - what the open trades cost.
Reset starts the whole account fresh at a size he picks.

His words (2026-11-12): "I will type the ticker and you trade start with 10k
fresh account entry and target and exit you have to pick it from charts" and
"I have to choose tickers and call or puts, default call".

SIMULATED ONLY. Nothing in this module can place an order: it has no broker
client and imports none. It only READS quotes through the same read path the
paper bots use (one batched call per tick for every run) and the option chain
the app last saved. Buys are scored at the option's ASK, sells at its BID (a real
$0.00 bid counts), only from a quote whose OWN time (quoteAt) is no older than
QUOTE_SECONDS and not before today's 9:30 open; otherwise it waits and says so.
The quote call runs outside the lock, so the page and the Stop button never wait on it.

The numbers are the chart's own (momx/chart_plan.py, the tested port of the
chart's Project ROI card): the contract the chart would pick for that side, the
stop where the option is down 50%, and T1 / T2 / T3.

    size     3 contracts, fewer when 3 would cost more than 10% of the account;
             when 10% is too small for even one, ONE contract is still bought as
             long as the account can pay for it (2026-11-16: LLY at $440 a contract
             was skipped on his $2,000 account, "Bot is not taking trade properly")
    stop     the stock trades through the stop -> everything is sold
    targets  one contract at each (2 contracts: T1 and T2; 1 contract: T2);
             the stop moves to break-even after T1 and to T1 after T2
    close    everything left is sold at 15:50 ET
    expiry   a contract past its expiry with no quote is closed at $0.00
             ("Expired worthless"), or at its intrinsic value when in the money
    re-entry never by itself: "Trade again" starts the next one
"""
from __future__ import annotations

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

from momx import chart_plan, ticker_journal

ET = ZoneInfo("America/New_York")
START_BALANCE = 10_000.0
#: The account sizes he may pick (2026-11-13: "10k, 5k, 2k or 100K", "What ever I want").
MIN_SIZE = 500.0
MAX_SIZE = 1_000_000.0
CONTRACTS = 3
#: The most one trade may cost, as a share of the account's balance.
MAX_TRADE_SHARE = 0.10
MAX_RUNS = 10
#: A quote whose OWN time (quoteAt, the exchange's) is older than this is never used for a
#: fill, the stop or a target. 60 s, not 15: outside the busiest names an option's quote
#: changes less often than every 15 s.
QUOTE_SECONDS = 60.0
#: The quote REPLY itself (its asOf) must be this recent.
REPLY_SECONDS = 15.0
#: The quote call's own timeout: it runs outside the lock, and a slow server costs one tick.
FETCH_SECONDS = 5.0
NO_TIMES_NOTE = "Waiting for quote times (restart needed)"
TICK_SECONDS = 10.0
OPEN_MINUTE = 9 * 60 + 30
CLOSE_OUT_MINUTE = 15 * 60 + 50
END_MINUTE = 16 * 60
STATE_FILE = "state.json"
TICKER_RE = re.compile(r"^[A-Z.]{1,6}$")
CLOSED_NOTE = "Market closed: buys at the 9:30 open"


class TraderError(Exception):
    """A request this trader refuses, with the HTTP status to answer it with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _pos(value: Any) -> float | None:
    out = _num(value)
    return out if out is not None and out > 0 else None


def _open_epoch(now: datetime) -> float:
    return now.replace(hour=OPEN_MINUTE // 60, minute=OPEN_MINUTE % 60, second=0, microsecond=0).timestamp()


def fresh(quote: Any, now: datetime) -> bool:
    """This quote is today's and live: it carries its own time (quoteAt, epoch
    seconds), that time is not before today's 9:30 ET open and it is no older than
    QUOTE_SECONDS. A reply stamped "now" around yesterday's closing quote is not fresh."""
    if not isinstance(quote, Mapping):
        return False
    at = _num(quote.get("quoteAt"))
    if at is None or at <= 0:
        return False
    return at >= _open_epoch(now) and -120 <= now.timestamp() - at <= QUOTE_SECONDS


def _stock_fresh(quote: Any, now: datetime) -> bool:
    """The stock's quote passes the same check when it carries a time; one the
    server could put no time on is taken as it is (the reply itself is recent)."""
    if not isinstance(quote, Mapping):
        return False
    return fresh(quote, now) if quote.get("quoteAt") is not None else True


def _live_ask(quote: Any, now: datetime) -> float | None:
    return _pos(quote.get("ask")) if fresh(quote, now) else None


def _live_bid(quote: Any, now: datetime) -> float | None:
    """The bid to sell at: any number from 0.00 up, on a fresh quote with a live ask.
    None = no usable quote. Test the answer with "is None": $0.00 is a real bid."""
    if not fresh(quote, now) or not _pos(quote.get("ask")):
        return None
    bid = _num(quote.get("bid"))
    return bid if bid is not None and bid >= 0 else None


def _waiting(quote: Any, now: datetime, what: str) -> str:
    """Why there is no usable quote on `what`, in his words."""
    if isinstance(quote, Mapping) and not fresh(quote, now):
        at = _num(quote.get("quoteAt"))
        if at is None or at < _open_epoch(now):
            return f"Waiting for today's first quote on {what}"
    return f"Waiting for a fresh quote on {what} (no quote under {QUOTE_SECONDS:.0f} s old)"


def expired(trade: Mapping, now: datetime) -> bool:
    """The contract's expiry day has passed, or it is that day after 16:00 ET."""
    day = str(trade.get("expiry") or "")[:10]
    if len(day) != 10:
        return False
    today = now.date().isoformat()
    return today > day or (today == day and now.hour * 60 + now.minute >= END_MINUTE)


def _stock_price(quote: Any) -> float | None:
    """The stock's price from one live quote: the last trade, else the bid/ask middle."""
    if not isinstance(quote, Mapping):
        return None
    last = _pos(quote.get("last"))
    if last:
        return last
    bid, ask = _pos(quote.get("bid")), _pos(quote.get("ask"))
    return (bid + ask) / 2 if bid and ask else None


def buying_open(now: datetime) -> bool:
    """Options are trading and there is still time before the 15:50 close."""
    minute = now.hour * 60 + now.minute
    return now.weekday() < 5 and OPEN_MINUTE <= minute < CLOSE_OUT_MINUTE


def session_open(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return now.weekday() < 5 and OPEN_MINUTE <= minute < END_MINUTE


def clean_ticker(value: Any) -> str:
    name = str(value or "").strip().upper()
    if not TICKER_RE.match(name):
        raise TraderError(400, "Type a ticker: letters only, up to 6 (for example NVDA).")
    return name


def clean_side(value: Any) -> str:
    name = str(value if value is not None else "CALL").strip().upper()
    if name not in ("CALL", "PUT"):
        raise TraderError(400, "Pick Call or Put.")
    return name


def clean_size(value: Any, default: float = START_BALANCE) -> float:
    """The account size he picked, in whole dollars: $500 to $1,000,000. None = default."""
    if value is None:
        return float(default)
    amount = None if isinstance(value, str) and not value.strip() else _num(value)
    if amount is None:
        raise TraderError(400, "Account size must be a number of dollars (for example 10000).")
    amount = float(round(amount))
    if not MIN_SIZE <= amount <= MAX_SIZE:
        raise TraderError(400, f"Account size must be between ${MIN_SIZE:,.0f} and ${MAX_SIZE:,.0f}.")
    return amount


def size_for(balance: float, ask: float, cash: float | None = None) -> int:
    """3 contracts, fewer when 3 would cost more than 10% of the account (`balance`) or more
    than the `cash` left in it. When 10% does not cover even one, one contract is bought if
    the cash can pay for it; 0 = it cannot."""
    if not ask or ask <= 0:
        return 0
    cost = ask * 100
    cash = balance if cash is None else cash
    fits = max(0, min(CONTRACTS, int((balance * MAX_TRADE_SHARE + 1e-9) // cost), int((cash + 1e-9) // cost)))
    return fits or (1 if cash + 1e-9 >= cost else 0)


def _held_cost(trade: Any) -> float:
    """What the contracts still held in this open trade cost."""
    if not isinstance(trade, Mapping):
        return 0.0
    return (_num(trade.get("entry")) or 0.0) * 100 * int(_num(trade.get("held")) or 0)


def chart_pick(chain: Any, side: str, spot: float, today: str) -> dict | None:
    """The contract the chart's Project ROI card picks for this side: the chain's
    own expiry while it still trades (else the nearest), the 0.20-delta OTM strike."""
    if not isinstance(chain, Mapping):
        return None
    rows = chain.get("selectedExpiryChainRows")
    live = chart_plan.expiries(rows, today)
    if not live:
        return None
    atm = chain.get("currentAtm") if isinstance(chain.get("currentAtm"), Mapping) else {}
    wanted = str(atm.get("expiry") or "")[:10]
    expiry = wanted if any(e["expiry"] == wanted for e in live) else live[0]["expiry"]
    picked = chart_plan.chain_entry(rows, spot, side, expiry)
    row = chart_plan.find_row(rows, side, expiry, picked["strike"]) if picked else None
    if not row or not row.get("symbol"):
        return None
    return {"contract": str(row["symbol"]), "strike": picked["strike"], "expiry": expiry}


def _default_chain(symbol: str) -> Any:
    from momx.option_track import _default_plan_chain
    return _default_plan_chain(symbol)


def _default_quotes(symbols: list) -> Any:
    # The paper bots' own read path, with this trader's short timeout. Contracts and
    # tickers are letters, digits, dots and spaces: only the space needs escaping.
    from momx.option_track import OPTION_QUOTES_URL, _http_json
    return _http_json(OPTION_QUOTES_URL + ",".join(str(s) for s in symbols).replace(" ", "%20"), timeout=FETCH_SECONDS)


class TickerTrader:
    def __init__(self, directory: Path, quotes_fetch: Callable[[list], Any] | None = None,
                 chain_fetch: Callable[[str], Any] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.directory = Path(directory)
        self._quotes_fetch = quotes_fetch or _default_quotes
        self._chain_fetch = chain_fetch or _default_chain
        self._clock = clock or (lambda: datetime.now(ET))
        self._lock = threading.RLock()
        self._runs: dict[str, dict] = {}
        self._account: dict = {"startBalance": START_BALANCE}
        self._load()
        # The journal's notes (per trade) and today's AI reviews (per run, and "all"): momx/ticker_journal.py.
        self._notes: dict = ticker_journal.read_json(self.directory / ticker_journal.NOTES_FILE, {})
        self._reviews: dict = ticker_journal.read_json(self.directory / ticker_journal.REVIEWS_FILE, {})
        self._reviewing: set[str] = set()

    # ---- file -----------------------------------------------------------
    def _load(self) -> None:
        try:
            doc = json.loads((self.directory / STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        runs = doc.get("runs") if isinstance(doc, Mapping) else None
        if isinstance(runs, Mapping):
            self._runs = {str(k): v for k, v in runs.items() if isinstance(v, dict)}
        account = doc.get("account") if isinstance(doc, Mapping) else None
        if isinstance(account, Mapping) and _pos(account.get("startBalance")):
            self._account = {**account, "startBalance": float(_pos(account["startBalance"]))}
        elif self._runs:
            # A file from before the one account: each run had its own. The newest run's size is the account's.
            newest = max(self._runs.values(), key=lambda r: str(r.get("createdAt") or ""))
            self._account = {"startBalance": float(_pos(newest.get("startBalance")) or START_BALANCE)}
        for run in self._runs.values():
            run.pop("balance", None)
            run.pop("startBalance", None)

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / STATE_FILE
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"version": 2, "account": self._account, "runs": self._runs}, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    # ---- the one account ------------------------------------------------
    def _start_size(self) -> float:
        return float(_pos(self._account.get("startBalance")) or START_BALANCE)

    def _realized(self) -> float:
        total = 0.0
        for run in self._runs.values():
            total += sum(_num(t.get("pl")) or 0.0 for t in run.get("trades") or [] if isinstance(t, Mapping))
            if isinstance(run.get("open"), Mapping):
                total += _num(run["open"].get("pl")) or 0.0
        return total

    def _invested(self) -> float:
        return sum(_held_cost(run.get("open")) for run in self._runs.values())

    def _equity(self) -> float:
        """The account with every open trade counted at what it cost: the start plus all profit and loss so far."""
        return round(self._start_size() + self._realized(), 2)

    def _cash(self) -> float:
        return round(self._equity() - self._invested(), 2)

    def account(self) -> dict:
        """What the screen shows: the cash left, what the open trades cost, and the account's value
        with each open trade at its last bid (at its cost until a bid has been seen)."""
        with self._lock:
            start, cash, worth, count = self._start_size(), self._cash(), 0.0, 0
            for run in self._runs.values():
                trade = run.get("open")
                if not isinstance(trade, Mapping):
                    continue
                count += 1
                bid = _num(trade.get("bid"))
                price = bid if bid is not None and bid >= 0 else (_num(trade.get("entry")) or 0.0)
                worth += price * 100 * int(_num(trade.get("held")) or 0)
            value = round(cash + worth, 2)
            return {"startBalance": start, "cash": cash, "invested": round(self._invested(), 2), "openValue": round(worth, 2),
                    "value": value, "net": round(value - start, 2), "realized": round(self._realized(), 2), "openTrades": count,
                    "resetAt": self._account.get("resetAt")}

    # ---- helpers --------------------------------------------------------
    def _chain(self, ticker: str) -> Any:
        try:
            chain = self._chain_fetch(ticker)
        except Exception:  # noqa: BLE001 - no saved chain
            return None
        return chain if isinstance(chain, Mapping) and chain.get("selectedExpiryChainRows") else None

    @staticmethod
    def _note(run: dict, text: str, stamp: str) -> bool:
        if run.get("note") == text:
            return False
        run["note"], run["noteAt"] = text, stamp
        return True

    def _run(self, run_id: Any) -> dict:
        run = self._runs.get(str(run_id or ""))
        if run is None:
            raise TraderError(404, "That ticker is not in the Ticker trader.")
        return run

    # ---- his buttons ----------------------------------------------------
    def start(self, ticker: Any, side: Any = "CALL", size: Any = None) -> dict:
        name, which, amount = clean_ticker(ticker), clean_side(side), clean_size(size)
        if self._chain(name) is None:
            raise TraderError(400, f"No saved option chain for {name}. Open its chart once, then try again.")
        with self._lock:
            now = self._clock()
            stamp = now.replace(microsecond=0).isoformat()
            run_id = f"{name}-{which}"
            run = self._runs.get(run_id)
            if run is None:
                if len(self._runs) >= MAX_RUNS:
                    raise TraderError(409, f"The Ticker trader runs at most {MAX_RUNS} tickers at once.")
                if not self._runs and size is not None:
                    self._account = {"startBalance": amount, "resetAt": stamp}      # the very first ticker may name the size
                run = {"id": run_id, "ticker": name, "side": which, "status": "idle",
                       "createdAt": stamp, "open": None, "trades": [], "pick": None}
                self._runs[run_id] = run
            if not run.get("open") and run.get("status") != "waiting":
                self._arm(run, now, stamp)
            self._save()
            return self._public(run)

    def _arm(self, run: dict, now: datetime, stamp: str) -> None:
        run.update(status="waiting", pick=None, stopping=False, armedAt=stamp)
        self._note(run, "Buying on the next price update" if buying_open(now) else CLOSED_NOTE, stamp)

    def again(self, run_id: Any) -> dict:
        with self._lock:
            run = self._run(run_id)
            if run.get("open"):
                raise TraderError(409, "A trade is still open on this ticker.")
            now = self._clock()
            if run.get("status") != "waiting":
                self._arm(run, now, now.replace(microsecond=0).isoformat())
                self._save()
            return self._public(run)

    def stop(self, run_id: Any) -> dict:
        with self._lock:
            run = self._run(run_id)
            stamp = self._clock().replace(microsecond=0).isoformat()
            if run.get("open"):
                run["stopping"] = True
                self._note(run, "Stopping: selling at the next fresh bid", stamp)
            else:
                run.update(status="stopped", pick=None, stopping=False)
                self._note(run, "Stopped. Tap Trade again to start.", stamp)
            self._save()
            # No quote call here: the loop's next tick sells at the fresh bid.
            return self._public(run)

    def reset(self, run_id: Any = None, size: Any = None) -> dict | None:
        """Start the ONE account fresh at `size` (its own starting size when none is given): every ticker's
        open trade and history are cleared. `run_id` only says which run's screen asked (it is answered back)."""
        with self._lock:
            asked = self._run(run_id) if run_id not in (None, "", ticker_journal.ALL) else None
            amount = clean_size(size, self._start_size())
            stamp = self._clock().replace(microsecond=0).isoformat()
            gone = [str(t.get("id")) for r in self._runs.values() for t in r.get("trades") or [] if isinstance(t, Mapping)]
            if any(k in self._notes for k in gone):
                # The history is cleared, so are its notes.
                for k in gone:
                    self._notes.pop(k, None)
                ticker_journal.write_json(self.directory / ticker_journal.NOTES_FILE, self._notes)
            # Today's AI review spoke about the cleared history: drop it for this run and for "all",
            # so the next review is written from the fresh account (2026-11-13 review).
            # Marked "tried", not removed, so a reset cannot buy a second AI call the same day.
            entries = self._today_reviews(self._clock().date().isoformat())
            if entries:
                for k in list(entries):
                    entries[k] = {"status": "tried", "at": stamp, "why": "reset"}
                ticker_journal.write_json(self.directory / ticker_journal.REVIEWS_FILE, self._reviews)
            self._account = {"startBalance": amount, "resetAt": stamp}
            for run in self._runs.values():
                run.update(status="idle", open=None, trades=[], pick=None, stopping=False)
                self._note(run, f"Reset: fresh ${amount:,.0f}. Tap Trade again to start.", stamp)
            self._save()
            return self._public(asked) if asked is not None else None

    # ---- read -----------------------------------------------------------
    def _public(self, run: Mapping) -> dict:
        """One run for the screen. `balance` is the shared account's cash, `startBalance` its starting
        size, `net` this ticker's own profit or loss so far."""
        out = json.loads(json.dumps(run))
        account = self.account()
        out["startBalance"], out["balance"], out["account"] = account["startBalance"], account["cash"], account
        trades = out.get("trades") or []
        held = out.get("open") if isinstance(out.get("open"), Mapping) else {}
        out["net"] = round(sum(_num(t.get("pl")) or 0.0 for t in trades) + (_num(held.get("pl")) or 0.0), 2)
        # The open trade's contracts still held, at their last bid (2026-11-18 "Bot trade show pnl next to tickers").
        # None until a bid has been seen; the account card counts them the same way (account()).
        bid, entry, left = _num(held.get("bid")), _num(held.get("entry")), int(_num(held.get("held")) or 0)
        out["openPl"] = round((bid - entry) * 100 * left, 2) if held and bid is not None and bid >= 0 and entry and left > 0 else None
        out["liveNet"] = round(out["net"] + (out["openPl"] or 0.0), 2)
        out["tradeCount"] = len(trades)
        out["wins"] = sum(1 for t in trades if (t.get("pl") or 0) > 0)
        return out

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            return {"runs": [self._public(r) for r in self._runs.values()], "maxRuns": MAX_RUNS, "account": self.account(),
                    "startBalance": START_BALANCE, "minSize": MIN_SIZE, "maxSize": MAX_SIZE, "contracts": CONTRACTS, "maxTradeShare": MAX_TRADE_SHARE,
                    "quoteSeconds": QUOTE_SECONDS,
                    "marketOpen": session_open(now), "asOf": now.replace(microsecond=0).isoformat(),
                    "simulated": True}

    # ---- journal, notes and the AI review (momx/ticker_journal.py) --------
    def _today_reviews(self, day: str) -> dict:
        """Today's reviews only: a new ET day starts with none (each run may have one again)."""
        if self._reviews.get("day") != day or not isinstance(self._reviews.get("entries"), dict):
            self._reviews = {"day": day, "entries": {}}
        return self._reviews["entries"]

    def journal(self, scope: Any = ticker_journal.ALL) -> dict:
        with self._lock:
            now = self._clock()
            runs = json.loads(json.dumps(list(self._runs.values())))
            notes = json.loads(json.dumps(self._notes))
            reviews = dict(self._today_reviews(now.date().isoformat()))
            start = self._start_size()
        try:
            return ticker_journal.build(runs, scope, notes, reviews, now, account_start=start)
        except KeyError:
            raise TraderError(404, "That ticker is not in the Ticker trader.") from None

    def save_note(self, trade_id: Any, text: Any) -> dict:
        """His note on one closed trade: plain text, at most ticker_journal.NOTE_MAX characters. Empty clears it."""
        key = str(trade_id or "").strip()
        try:
            clean = ticker_journal.clean_note(text)
        except ValueError as exc:
            raise TraderError(400, str(exc)) from None
        with self._lock:
            known = {str(t.get("id")) for r in self._runs.values() for t in r.get("trades") or [] if isinstance(t, Mapping)}
            if not key or key not in known:
                raise TraderError(404, "That trade is not in the Ticker trader journal.")
            stamp = self._clock().replace(microsecond=0).isoformat()
            if clean:
                self._notes[key] = {"text": clean, "at": stamp}
            else:
                self._notes.pop(key, None)
            ticker_journal.write_json(self.directory / ticker_journal.NOTES_FILE, self._notes)
            return {"id": key, "text": clean, "at": stamp if clean else None}

    def review(self, scope: Any = ticker_journal.ALL, ask: Callable | None = None, known: frozenset = frozenset()) -> dict:
        """Today's AI review for one run (or all runs). The AI is asked at most once per run per ET day: a cached
        paragraph, or a recorded attempt that failed, answers every later ask. Skipped (not recorded) when there is
        nothing to review or the AI is off, resting or over the day's allowance. `ask` = a test's stand-in."""
        with self._lock:
            now = self._clock()
            runs = json.loads(json.dumps(list(self._runs.values())))
            start = self._start_size()
            try:
                key, _ = ticker_journal.scope_runs(runs, scope)
            except KeyError:
                raise TraderError(404, "That ticker is not in the Ticker trader.") from None
            held = self._today_reviews(now.date().isoformat()).get(key)
            if held:
                return {"review": ticker_journal.public_review(held), "status": held.get("status"), "cached": True}
            if key in self._reviewing:
                return {"review": None, "status": "writing", "cached": False}
            self._reviewing.add(key)
        try:
            doc = ticker_journal.build(runs, key, {}, {}, now, account_start=start)
            why = ticker_journal.skip_reason(doc, real=ask is None)
            if why:
                return {"review": None, "status": "skipped", "why": why, "cached": False}
            try:
                entry = ticker_journal.write_review(doc, ask, known, now)
            except Exception:  # noqa: BLE001 - the AI was asked: still one try today
                entry = {"status": "tried", "at": now.replace(microsecond=0).isoformat()}
            with self._lock:
                self._today_reviews(now.date().isoformat())[key] = entry
                ticker_journal.write_json(self.directory / ticker_journal.REVIEWS_FILE, self._reviews)
            return {"review": ticker_journal.public_review(entry), "status": entry["status"], "cached": False}
        finally:
            with self._lock:
                self._reviewing.discard(key)

    # ---- the loop -------------------------------------------------------
    @staticmethod
    def _token(run: Mapping) -> tuple:
        """What a tick planned for this run: it is applied only if this still matches."""
        trade = run.get("open")
        return ("open", trade.get("id")) if trade else ("waiting", run.get("armedAt"))

    def tick(self, now: datetime | None = None) -> None:
        # 1. Under the lock: what to ask for. 2. Lock released: the quote call (short
        # timeout), so the page's read and his buttons never wait on it. 3. Under the
        # lock again: apply to each run found again by id, unless it changed meanwhile.
        with self._lock:
            now = now or self._clock()
            stamp = now.replace(microsecond=0).isoformat()
            changed = False
            want: list[str] = []
            plan: list[tuple] = []
            for run in self._runs.values():
                trade = run.get("open")
                if trade:
                    if session_open(now) or expired(trade, now):
                        want += [trade["contract"], run["ticker"]]
                        plan.append((run["id"], self._token(run)))
                    else:
                        changed |= self._note(run, "Market closed: this trade is sold at the first fresh bid after the open", stamp)
                elif run.get("status") == "waiting":
                    if buying_open(now):
                        want.append(run["ticker"])
                        if run.get("pick"):
                            want.append(run["pick"]["contract"])
                        plan.append((run["id"], self._token(run)))
                    else:
                        changed |= self._note(run, CLOSED_NOTE, stamp)
            if changed:
                self._save()
        if not plan:
            return
        quotes: Mapping = {}
        timed = True
        try:
            doc = self._quotes_fetch(list(dict.fromkeys(want))) or {}
            seen = _num(doc.get("asOf"))
            if seen is not None and -5 <= now.timestamp() - seen <= REPLY_SECONDS and isinstance(doc.get("quotes"), Mapping):
                quotes = doc["quotes"]
        except Exception:  # noqa: BLE001 - no quote this tick: every run waits
            quotes = {}
        rows = [q for q in quotes.values() if isinstance(q, Mapping)]
        if rows and not any("quoteAt" in q for q in rows):
            # A server from before quote times: nothing says how old these prices are.
            quotes, timed = {}, False
        with self._lock:
            changed = False
            for run_id, token in plan:
                run = self._runs.get(run_id)
                if run is None or self._token(run) != token or (not run.get("open") and run.get("status") != "waiting"):
                    continue        # reset, stopped, removed or already handled while the quotes were fetched
                try:
                    if not timed and not (run.get("open") and expired(run["open"], now)):
                        changed |= self._note(run, NO_TIMES_NOTE, stamp)
                    elif run.get("open"):
                        changed |= self._manage(run, quotes, now, stamp)
                    else:
                        changed |= self._enter(run, quotes, now, stamp)
                except Exception as exc:  # noqa: BLE001 - one run never stops the others
                    changed |= self._note(run, f"Waiting: {type(exc).__name__}", stamp)
            if changed:
                self._save()

    def _idle(self, run: dict, why: str, stamp: str) -> bool:
        run.update(status="idle", pick=None)
        self._note(run, why, stamp)
        return True

    def _enter(self, run: dict, quotes: Mapping, now: datetime, stamp: str) -> bool:
        ticker, side = run["ticker"], run["side"]
        spot = _stock_price(quotes.get(ticker)) if _stock_fresh(quotes.get(ticker), now) else None
        if not spot:
            return self._note(run, _waiting(quotes.get(ticker), now, ticker), stamp)
        chain = self._chain(ticker)
        if chain is None:
            return self._idle(run, f"Not bought: no saved option chain for {ticker}.", stamp)
        day = now.date().isoformat()
        pick = chart_pick(chain, side, spot, day)
        if pick is None:
            return self._idle(run, f"Not bought: the chart has no 0.20-delta {side.title()} for {ticker} in the saved option chain.", stamp)
        if not run.get("pick") or run["pick"].get("contract") != pick["contract"]:
            run["pick"] = pick
            self._note(run, f"Picked the {pick['strike']:g} {side.title()} ({pick['expiry']}): waiting for its live ask", stamp)
            return True
        quote = quotes.get(pick["contract"])
        ask = _live_ask(quote, now)
        if not ask:
            return self._note(run, _waiting(quote, now, f"the {pick['strike']:g} {side.title()}"), stamp)
        plan = chart_plan.from_chain(chain, bear=side == "PUT", expiry=pick["expiry"], strike=pick["strike"],
                                     entry=ask, spot=spot, today=day)
        levels = [(plan or {}).get(k) for k in ("stopStock", "t1", "t2", "t3")]
        if not plan or any(not _pos(v) for v in levels):
            return self._idle(run, "Not bought: the chart has no stop or targets for this contract (the saved option chain has no expected move).", stamp)
        balance, cash = self._equity(), self._cash()
        qty = size_for(balance, ask, cash)
        if qty < 1:
            return self._idle(run, f"Not bought: one contract costs ${ask * 100:,.0f}, more than the ${cash:,.0f} cash left in the account.", stamp)
        by_share = min(CONTRACTS, int((balance * MAX_TRADE_SHARE + 1e-9) // (ask * 100)))
        number = len(run.get("trades") or []) + 1
        run["open"] = {
            "id": f"{run['id']}-{number}-{int(now.timestamp())}", "day": day, "ticker": ticker, "side": side,
            "contract": pick["contract"], "strike": pick["strike"], "expiry": pick["expiry"],
            "qty": qty, "held": qty, "split": chart_plan.split_contracts(qty),
            "entry": round(ask, 2), "entrySpot": plan["entrySpot"], "cost": round(ask * 100 * qty, 2), "boughtAt": stamp,
            "stopStart": plan["stopStock"], "stopStock": plan["stopStock"], "stopKind": "stop",
            "t1": plan["t1"], "t2": plan["t2"], "t3": plan["t3"],
            "targets": [{"label": t["label"], "stock": t["stock"], "reason": t["reason"]} for t in plan["targets"]],
            "expectedMove": plan.get("expectedMove"), "chainAt": plan.get("chainAt"),
            "hits": [], "sells": [], "events": [], "status": "open", "pl": 0.0,
            "why": f"You picked {side.title()}. Bought {qty} at the ask ${ask:.2f} with {ticker} at {plan['entrySpot']:.2f}."
                   + ("" if qty == CONTRACTS else
                      f" 1 not {CONTRACTS}: one contract costs more than 10% of the account, so the bot bought just one."
                      if by_share < 1 else
                      f" {qty} not {CONTRACTS}: that is all the cash left in the account buys."
                      if qty < by_share else
                      f" {qty} not {CONTRACTS}: 10% of the account is the most for one trade."),
        }
        run.update(status="open", pick=None, stock=round(spot, 2), stockAt=stamp)
        self._note(run, f"In a trade: {qty} x {pick['strike']:g} {side.title()}", stamp)
        return True

    def _sell(self, run: dict, trade: dict, qty: int, bid: float, text: str, stamp: str) -> None:
        gain = round((bid - trade["entry"]) * 100 * qty, 2)
        trade["held"] -= qty
        trade["pl"] = round(trade["pl"] + gain, 2)
        trade["sells"].append({"at": stamp, "qty": qty, "price": round(bid, 2), "pl": gain})
        trade["events"].append({"at": stamp, "text": text})

    def _close(self, run: dict, trade: dict, stamp: str) -> None:
        trade.update(status="closed", closedAt=stamp, held=0,
                     reason=" ".join(e["text"] for e in trade["events"]),
                     plPct=round(trade["pl"] / trade["cost"] * 100, 1) if trade.get("cost") else None,
                     balanceAfter=self._equity())
        run.setdefault("trades", []).append(trade)
        stopped = bool(run.get("stopping"))
        run.update(open=None, status="stopped" if stopped else "idle", stopping=False)
        self._note(run, ("Stopped. " if stopped else "Trade closed. ") + "Tap Trade again for the next one.", stamp)

    def _expire(self, run: dict, trade: dict, quotes: Mapping, stamp: str) -> None:
        """The contract is past its expiry and has no quote: it can never be sold, so
        the rest is closed here. Worth its intrinsic value when in the money (from the
        stock's price now, else the last one seen), otherwise $0.00."""
        held = int(trade["held"])
        stock = _stock_price(quotes.get(run["ticker"])) or _pos(run.get("stock"))
        strike = _num(trade.get("strike"))
        value = 0.0
        if stock and strike is not None:
            value = round(max(0.0, (strike - stock) if trade["side"] == "PUT" else (stock - strike)), 2)
        if value > 0:
            text = (f"Expired in the money: no quote, closed {held} at its intrinsic value ${value:.2f} "
                    f"({run['ticker']} at {stock:.2f}, strike {strike:g}).")
        elif stock:
            text = f"Expired worthless: closed {held} at $0.00."
        else:
            text = f"Expired worthless: closed {held} at $0.00 (no {run['ticker']} price to check)."
        self._sell(run, trade, held, value, text, stamp)
        self._close(run, trade, stamp)

    def _manage(self, run: dict, quotes: Mapping, now: datetime, stamp: str) -> bool:
        trade, ticker = run["open"], run["ticker"]
        quote = quotes.get(trade["contract"])
        # None = no usable quote. $0.00 is a real bid (a worthless option still sells).
        bid = _live_bid(quote, now)
        if bid is None and expired(trade, now):
            self._expire(run, trade, quotes, stamp)
            return True
        stock = _stock_price(quotes.get(ticker)) if _stock_fresh(quotes.get(ticker), now) else None
        changed = False
        if stock:
            run.update(stock=round(stock, 2), stockAt=stamp)
            changed = True
        if bid is not None:
            trade.update(bid=round(bid, 2), bidAt=stamp)
        held = int(trade["held"])
        what = f"the {_num(trade.get('strike')) or 0:g} {str(trade['side']).title()}"
        minute = now.hour * 60 + now.minute
        # Sold in full whatever the stock does: his Stop button, the 15:50 close, a trade left from another day.
        full = ("Stopped by you" if run.get("stopping") else
                "15:50 close" if minute >= CLOSE_OUT_MINUTE else
                "Left open from an earlier day" if trade.get("day") != now.date().isoformat() else "")
        if full:
            if bid is None:
                return self._note(run, f"{full}: {_waiting(quote, now, what)}", stamp) or changed
            self._sell(run, trade, held, bid, f"{full}: sold {held} at the bid ${bid:.2f}.", stamp)
            self._close(run, trade, stamp)
            return True
        if not stock:
            return self._note(run, _waiting(quotes.get(ticker), now, ticker), stamp) or changed
        call = trade["side"] != "PUT"
        stop = float(trade["stopStock"])
        if (stock <= stop) if call else (stock >= stop):
            if bid is None:
                return self._note(run, f"Stop reached at {stop:.2f}: {_waiting(quote, now, what)}", stamp) or changed
            kind = {"break-even": "Break-even stop", "T1": "Stop at T1"}.get(trade.get("stopKind"), "Stop")
            self._sell(run, trade, held, bid, f"{kind}: stock traded through {stop:.2f}, sold {held} at ${bid:.2f}.", stamp)
            self._close(run, trade, stamp)
            return True
        while len(trade["hits"]) < 3 and trade["held"] > 0:
            index = len(trade["hits"])
            label = f"T{index + 1}"
            level = float(trade[label.lower()])
            if not ((stock >= level) if call else (stock <= level)):
                break
            lot = min(int(trade["split"][index]), int(trade["held"]))
            if lot > 0 and bid is None:
                return self._note(run, f"{label} reached at {level:.2f}: {_waiting(quote, now, what)}", stamp) or changed
            move = ""
            if index == 0:
                trade.update(stopStock=trade["entrySpot"], stopKind="break-even")
                move = ", stop to break-even"
            elif index == 1:
                trade.update(stopStock=trade["t1"], stopKind="T1")
                move = ", stop to T1"
            trade["hits"].append({"target": label, "at": stamp, "stock": round(stock, 2), "sold": lot,
                                  "price": round(bid, 2) if lot > 0 else None})
            if lot > 0:
                self._sell(run, trade, lot, bid, f"{label} hit: sold {lot} at ${bid:.2f}{move}.", stamp)
            else:
                trade["events"].append({"at": stamp, "text": f"{label} hit: none sold{move}."})
            changed = True
            if trade["held"] <= 0:
                self._close(run, trade, stamp)
                return True
        changed |= self._note(run, f"In a trade: holding {trade['held']} of {trade['qty']}", stamp)
        return changed


_TRADERS: list[TickerTrader] = []


def start(directory: Path) -> TickerTrader:
    """Start the one trader and its loop thread (momx_worker). Idempotent."""
    if not _TRADERS:
        trader = TickerTrader(Path(directory))
        _TRADERS.append(trader)

        def loop() -> None:
            while True:
                try:
                    trader.tick()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(TICK_SECONDS)

        threading.Thread(target=loop, name="momx-ticker-trader", daemon=True).start()
    return _TRADERS[0]


def current() -> TickerTrader | None:
    return _TRADERS[0] if _TRADERS else None
