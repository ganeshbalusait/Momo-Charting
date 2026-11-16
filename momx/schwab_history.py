"""The owner's REAL Schwab option trades, read-only (2026-11-11, his "Yes build it").

What this module may do, and nothing more:

* GET three Schwab Trader API paths on the "trading" profile, and only these:
      /trader/v1/accounts/accountNumbers
      /trader/v1/accounts/{hash}/transactions
      /trader/v1/accounts/{hash}/orders
  `check_request()` refuses any other method or path BEFORE anything is sent, and the
  transport below has no way to send anything but a GET. Nothing here can place, change
  or cancel an order, move money or change an account setting. A source test bans the
  order/post/put/delete calls from this file.
* Once a day after 16:30 ET on weekdays (the day is claimed BEFORE the request), plus at
  most one manual "refresh now" per hour. Never a retry loop: one pass, whatever happens.
* A sign-in failure (HTTP 401/403, a dead or invalid token, invalid_grant), an Akamai edge
  block or a 429 stops reading for the rest of the ET day, and the page shows
  "Schwab sign-in needed". The home IP was once blocked after a dead-token retry flood.
* Stores the fills under artifacts/insights/ (owner-only; no route serves the folder).
  The account number is never stored: only its last 4 digits. The account hash is held in
  memory for the one read and never written.

Fill shape (schwab_fills.json "fills" values):
    {"id", "activityId", "orderId", "account" ("…1234"), "at" (ET ISO), "day", "underlying",
     "optionSymbol", "assetType", "putCall" ("CALL"/"PUT"/None), "strike", "expiry" (YYYY-MM-DD),
     "dte" (calendar days from the fill day to expiry), "side" ("BUY"/"SELL"),
     "positionEffect" ("OPENING"/"CLOSING"/None), "quantity" (>0), "price", "fees",
     "multiplier" (100 options, 1 stock), "kind" ("trade" | "expired" | "exercised" | "assigned"),
     "valueKnown" (event rows only: False when an exercise/assignment's option value cannot be told)}

Schwab's reply shapes come from schwab-py 1.5.0 and Schwab's docs; they were not seen live
when this was written, so every field is read defensively.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASE_URL = "https://api.schwabapi.com"
#: The only requests this module can make. Each is a full-match on the path; the method must be GET.
ALLOWED_PATHS = (
    re.compile(r"/trader/v1/accounts/accountNumbers"),
    re.compile(r"/trader/v1/accounts/[A-Za-z0-9]{8,128}/transactions"),
    re.compile(r"/trader/v1/accounts/[A-Za-z0-9]{8,128}/orders"),
)
ALLOWED_PARAMS = {"types", "startDate", "endDate", "symbol", "fromEnteredTime", "toEnteredTime", "status", "maxResults"}
#: The daily read: weekdays from 16:30 ET (until midnight, so a PC woken late still reads once).
DAILY_AT_MINUTE = 16 * 60 + 30
MANUAL_EVERY_SECONDS = 3600
FIRST_RUN_DAYS = 90
#: Schwab: a transactions request may start at most 60 days back.
WINDOW_DAYS = 60
#: A hard ceiling on GETs in one ET day, whatever the reason (accountNumbers + 2 per account, 2 reads).
MAX_GETS_PER_DAY = 16
TIMEOUT_SECONDS = 20.0
SIGNIN_KINDS = ("signin", "blocked", "rate_limit")
FEE_KEYS = ("COMMISSION", "OPT_REG_FEE", "SEC_FEE", "TAF_FEE", "INDEX_OPTION_FEE", "CDSC_FEE", "OTHER")

_LOCK = threading.Lock()
_READING = {"on": False, "trigger": None, "startedAt": None}


class NotAllowed(RuntimeError):
    """A request this module must never make. Raised before anything is sent."""


class SchwabRefused(RuntimeError):
    """Schwab (or the token) said no. kind: signin | blocked | rate_limit | network | bad_reply | budget."""

    def __init__(self, message: str, kind: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind, self.status = kind, status


# ---------------------------------------------------------------- where things live
def root() -> Path:
    override = os.environ.get("AGX_INSIGHTS_DIR", "").strip()
    if override:
        return Path(override)
    from config import ARTIFACTS_DIR
    return Path(ARTIFACTS_DIR) / "insights"


def state_path() -> Path:
    return root() / "schwab_read.json"


def fills_path() -> Path:
    return root() / "schwab_fills.json"


#: Reads and writes of these small files take turns: on Windows a file being read cannot be replaced.
_FILES = threading.RLock()


def _read_json(path: Path, default):
    with _FILES:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
    return doc if isinstance(doc, type(default)) else default


def _write_json(path: Path, doc) -> None:
    text = json.dumps(doc, separators=(",", ":"), default=str)
    with _FILES:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(5):               # a local file only (a virus scanner may hold it a moment)
            try:
                tmp.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))


def load_state() -> dict:
    return _read_json(state_path(), {})


def save_state(state: dict) -> None:
    _write_json(state_path(), state)


def load_store() -> dict:
    doc = _read_json(fills_path(), {})
    doc.setdefault("fills", {})
    return doc


def save_store(doc: dict) -> None:
    _write_json(fills_path(), doc)


# ---------------------------------------------------------------- the allow-list
def check_request(method: str, path: str, params: dict | None = None) -> None:
    """Raise NotAllowed unless this is a GET of one of the three read paths with known parameters."""
    if str(method).upper() != "GET":
        raise NotAllowed(f"Only GET is allowed, not {method!r}.")
    if not isinstance(path, str) or not any(p.fullmatch(path) for p in ALLOWED_PATHS):
        raise NotAllowed(f"Path not on the read-only allow-list: {path!r}.")
    extra = set(params or {}) - ALLOWED_PARAMS
    if extra:
        raise NotAllowed(f"Parameters not on the allow-list: {sorted(extra)}.")


_ACCOUNT_IN_PATH = re.compile(r"/accounts/[A-Za-z0-9]{8,128}")
_QUERY = re.compile(r"\?[^\s'\"]*")


def _scrub(text) -> str:
    """Words safe to store or share: no account hash (it appears in a URL inside requests' error
    messages) and no query string (the read's dates). Everything stored or handed to the
    process-wide Schwab gate goes through this; GET /api/schwab/status is open to every user."""
    return _QUERY.sub("", _ACCOUNT_IN_PATH.sub("/accounts/<account>", str(text or "")))


def _what_failed(exc: BaseException) -> str:
    """The error's type only: a network error's message carries the full URL."""
    return type(exc).__name__


def _looks_like_signin(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(word in text for word in ("oauth", "invalid_grant", "invalid_token", "unauthorized", "refresh token",
                                         "re-authenticate", "token is invalid", "no schwab-py token", "app key and secret"))


def schwab_transport(client_factory: Callable[[], Any]) -> Callable[[str], Any]:
    """The real wire: a GET through the trading profile's signed session, with the app's
    process-wide Schwab gate. Returns fn(url) -> parsed JSON; raises SchwabRefused."""
    def get(url: str):
        from data.schwab_rate_limit import GATE, SchwabUnavailable, looks_like_edge_block
        try:
            client = client_factory()
            client._refuse_if_refresh_token_dead()      # raises before any network call on a dead token
            session = client._library_client().session
            GATE.acquire()
        except SchwabUnavailable as exc:
            raise SchwabRefused(_scrub(exc), "rate_limit" if getattr(exc, "kind", "") == "rate_limit" else "blocked") from exc
        except Exception as exc:  # noqa: BLE001 - every token problem is a sign-in problem here
            raise SchwabRefused(_scrub(f"{type(exc).__name__}: {exc}"), "signin") from exc
        try:
            response = session.get(url, headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            if _looks_like_signin(exc):
                GATE.note_failure("http_401", f"Schwab sign-in refused ({_what_failed(exc)}).")
                raise SchwabRefused(f"Schwab sign-in refused ({_what_failed(exc)}).", "signin") from exc
            GATE.note_failure("network", f"Network error reading Schwab history ({_what_failed(exc)}).")
            raise SchwabRefused(f"Network error ({_what_failed(exc)}).", "network") from exc
        status = int(getattr(response, "status_code", 0) or 0)
        if status in (401, 403, 429):
            body = str(getattr(response, "text", "") or "")
            if looks_like_edge_block(status, body):
                GATE.note_edge_block()
                raise SchwabRefused(f"Schwab's edge refused this address (HTTP {status}).", "blocked", status)
            if status == 429:
                GATE.note_rate_limited(0.0)
                raise SchwabRefused("Schwab returned 429 Too Many Requests.", "rate_limit", status)
            GATE.note_failure(f"http_{status}", _scrub(body)[:300])
            raise SchwabRefused(f"Schwab refused the sign-in (HTTP {status}).", "signin", status)
        if status >= 400 or status == 0:
            GATE.note_failure(f"http_{status}", f"Schwab answered HTTP {status} to a history read.")
            raise SchwabRefused(f"Schwab answered HTTP {status}.", "bad_reply", status)
        GATE.note_success()
        try:
            return response.json()
        except Exception as exc:  # noqa: BLE001
            raise SchwabRefused("Schwab's reply was not JSON.", "bad_reply", status) from exc
    return get


class Reader:
    """Counts and checks every request of one read. `transport(url)` returns parsed JSON or raises SchwabRefused."""

    def __init__(self, transport: Callable[[str], Any], state: dict, today: str) -> None:
        self.transport, self.state, self.today = transport, state, today
        self.calls = 0

    def get(self, path: str, params: dict | None = None):
        check_request("GET", path, params)
        budget = self.state.get("gets") if isinstance(self.state.get("gets"), dict) else {}
        used = int(budget.get("n") or 0) if budget.get("day") == self.today else 0
        if used >= MAX_GETS_PER_DAY:
            raise SchwabRefused("The day's Schwab read budget is used up.", "budget")
        self.state["gets"] = {"day": self.today, "n": used + 1}
        save_state(self.state)                      # counted before it is sent
        self.calls += 1
        url = BASE_URL + path + ("?" + urlencode(params) if params else "")
        return self.transport(url)


# ---------------------------------------------------------------- reading Schwab's reply
def _iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _when(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", text):
        text = text[:-2] + ":" + text[-2:]
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(ET)


_OCC = re.compile(r"^([A-Z.$]{1,6})\s*(\d{6})([CP])(\d{8})$")


def parse_option_symbol(symbol: str) -> dict:
    """{"underlying", "expiry", "putCall", "strike"} from "SPY   261110C00788000", or {}."""
    match = _OCC.match(str(symbol or "").strip().upper())
    if not match:
        return {}
    root_, ymd, cp, strike = match.groups()
    try:
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:6])).isoformat()
    except ValueError:
        return {}
    return {"underlying": root_, "expiry": expiry, "putCall": "CALL" if cp == "C" else "PUT", "strike": int(strike) / 1000}


def last4(number) -> str:
    digits = re.sub(r"\D", "", str(number or ""))
    return "…" + digits[-4:] if digits else "…????"


def _delivery_event(tx: dict, legs: list) -> tuple[str, float | None]:
    """("expired" | "exercised" | "assigned", the delivered stock's price per share or None) of one
    RECEIVE_AND_DELIVER row. Only a plain removal (no stock, no exercise/assignment words) is an expiry."""
    text = " ".join(str(tx.get(k) or "") for k in ("description", "subAccount", "activityType")).upper()
    for item, _, _ in legs:
        text += " " + str(item.get("description") or "").upper()
    stock_price = None
    has_stock = False
    for item, _, asset in legs:
        if asset == "OPTION":
            continue
        has_stock = True
        qty = abs(_num(item.get("amount")) or 0.0)
        price = _num(item.get("price"))
        cost = _num(item.get("cost"))
        if (price is None or price <= 0) and qty and cost:
            price = abs(cost) / qty
        if price and price > 0:
            stock_price = price
    option_amount = next((_num(i.get("amount")) or 0.0 for i, _, a in legs if a == "OPTION"), 0.0)
    if "ASSIGN" in text:
        return "assigned", stock_price
    if "EXERCIS" in text:
        return "exercised", stock_price
    if has_stock:                                  # a long option removed = exercised; a short one removed = assigned
        return ("exercised" if option_amount < 0 else "assigned"), stock_price
    return "expired", None


def parse_transactions(payload, account_label: str) -> list[dict]:
    """Fills from a /transactions reply. TRADE rows are fills. A RECEIVE_AND_DELIVER option row closes
    the position: a plain expiry at $0 ("expired"); an exercise or assignment ("exercised"/"assigned",
    told apart by the description or by a stock leg in the same row) at the option's intrinsic value
    when the delivered stock's price shows it, else with valueKnown False (left out of the numbers)."""
    out: list[dict] = []
    rows = payload if isinstance(payload, list) else (payload.get("transactions") if isinstance(payload, dict) else None)
    for tx in rows or []:
        if not isinstance(tx, dict):
            continue
        kind = str(tx.get("type") or "").upper()
        if kind not in ("TRADE", "RECEIVE_AND_DELIVER"):
            continue
        at = _when(tx.get("time") or tx.get("tradeDate"))
        if at is None:
            continue
        items = [i for i in tx.get("transferItems") or [] if isinstance(i, dict)]
        fees = 0.0
        legs = []
        for item in items:
            inst = item.get("instrument") if isinstance(item.get("instrument"), dict) else {}
            asset = str(inst.get("assetType") or "").upper()
            if item.get("feeType") or asset == "CURRENCY":
                fees += abs(_num(item.get("cost")) or 0.0)
                continue
            if asset in ("OPTION", "EQUITY", "COLLECTIVE_INVESTMENT", "ETF"):
                legs.append((item, inst, asset))
        event = _delivery_event(tx, legs) if kind == "RECEIVE_AND_DELIVER" else None
        for index, (item, inst, asset) in enumerate(legs):
            amount = _num(item.get("amount")) or 0.0
            cost = _num(item.get("cost"))
            if amount == 0 and not cost:
                continue
            if amount:
                side = "BUY" if amount > 0 else "SELL"
            else:
                side = "BUY" if (cost or 0) < 0 else "SELL"
            qty = abs(amount)
            symbol = str(inst.get("symbol") or "").strip()
            occ = parse_option_symbol(symbol) if asset == "OPTION" else {}
            expiry = occ.get("expiry") or (str(inst.get("expirationDate") or "")[:10] or None)
            put_call = occ.get("putCall") or (str(inst.get("putCall") or "").upper() or None)
            strike = occ.get("strike") if occ else _num(inst.get("strikePrice"))
            underlying = occ.get("underlying") or str(inst.get("underlyingSymbol") or symbol).strip().upper()
            multiplier = 100.0 if asset == "OPTION" else 1.0
            price = _num(item.get("price"))
            if price is None and qty and cost is not None:
                price = abs(cost) / qty / multiplier
            value_known = True
            if event is not None:
                if asset != "OPTION":
                    continue                       # the stock side of an exercise is not an option trade
                price, value_known = 0.0, True
                if event[0] != "expired":
                    stock = event[1]
                    if stock is not None and strike is not None and abs(stock - strike) > 0.005:
                        price = max(0.0, stock - strike) if put_call == "CALL" else max(0.0, strike - stock)
                    else:
                        value_known = False        # Schwab delivers the stock at the strike: the market value is not in the row
            if price is None or not qty:
                continue
            dte = None
            if expiry:
                try:
                    dte = (date.fromisoformat(expiry) - at.date()).days
                except ValueError:
                    dte = None
            activity = str(tx.get("activityId") or tx.get("transactionId") or f"{at.isoformat()}|{symbol}")
            out.append({
                "id": f"{activity}:{index}", "activityId": activity, "orderId": str(tx.get("orderId") or "") or None,
                "account": account_label, "at": at.isoformat(timespec="seconds"), "day": at.date().isoformat(),
                "underlying": underlying, "optionSymbol": symbol, "assetType": asset, "putCall": put_call,
                "strike": strike, "expiry": expiry, "dte": dte, "side": side,
                "positionEffect": str(item.get("positionEffect") or "").upper() or None,
                "quantity": qty, "price": round(price, 4), "fees": round(fees if index == 0 else 0.0, 2),
                "multiplier": multiplier, "kind": event[0] if event is not None else "trade",
                **({"valueKnown": value_known} if event is not None else {}),
            })
    return out


# ---------------------------------------------------------------- when a read may happen
def _today(now: datetime) -> str:
    return now.astimezone(ET).date().isoformat()


def stopped_today(state: dict, now: datetime) -> bool:
    return state.get("stoppedDay") == _today(now)


def daily_due(state: dict, now: datetime) -> bool:
    """A weekday at or after 16:30 ET, not claimed today, not stopped today."""
    now = now.astimezone(ET)
    return (now.weekday() < 5 and now.hour * 60 + now.minute >= DAILY_AT_MINUTE
            and state.get("lastReadDay") != _today(now) and not stopped_today(state, now))


def next_manual_at(state: dict) -> str | None:
    """When the next manual refresh is allowed (an hour after the last read attempt of any kind)."""
    stamps = [s for s in (state.get("lastManualAt"), state.get("lastAttemptAt")) if s]
    if not stamps:
        return None
    try:
        last = max(datetime.fromisoformat(s) for s in stamps)
    except ValueError:
        return None
    return (last + timedelta(seconds=MANUAL_EVERY_SECONDS)).isoformat(timespec="seconds")


def manual_refusal(state: dict, now: datetime) -> tuple[int, str] | None:
    """(HTTP status, plain words) when a manual refresh may not start now, else None."""
    if _READING["on"]:
        return 409, "Already reading from Schwab. The page updates when it is done."
    if stopped_today(state, now):
        return 409, "Schwab sign-in needed. Reading is stopped for today; renew the Schwab trading login in Settings."
    allowed = next_manual_at(state)
    if allowed and now.astimezone(ET) < datetime.fromisoformat(allowed):
        minutes = max(1, int((datetime.fromisoformat(allowed) - now).total_seconds() // 60) + 1)
        return 429, f"Already read in the last hour. Refresh again in about {minutes} min."
    return None


def status(now: datetime | None = None) -> dict:
    """What the page shows about the Schwab read."""
    now = now or datetime.now(ET)
    state = load_state()
    stopped = stopped_today(state, now)
    allowed = next_manual_at(state)
    if _READING["on"]:
        kind = "reading"
    elif stopped:
        kind = "signin_needed" if state.get("stoppedKind") == "signin" else "stopped"
    elif state.get("lastReadAt"):
        kind = "ok"
    else:
        kind = "never"
    return {
        "state": kind, "reading": _READING["on"], "lastReadAt": state.get("lastReadAt"), "lastReadDay": state.get("lastReadDay"),
        "lastAttemptAt": state.get("lastAttemptAt"), "stoppedDay": state.get("stoppedDay") if stopped else None,
        "stopped": stopped, "signInNeeded": stopped and state.get("stoppedKind") in SIGNIN_KINDS,
        "reason": state.get("stoppedReason") if stopped else None, "lastError": state.get("lastError"),
        "nextManualAt": allowed, "canRefresh": manual_refusal(state, now) is None,
        "accounts": list(state.get("accounts") or []), "firstDay": state.get("firstDay"), "throughDay": state.get("throughDay"),
        "note": "Read once a day after 16:30 ET, plus at most one manual refresh an hour. Read-only.",
    }


# ---------------------------------------------------------------- one read
def _windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Newest first, each at most WINDOW_DAYS long."""
    out, hi = [], end
    while hi > start:
        lo = max(start, hi - timedelta(days=WINDOW_DAYS))
        out.append((lo, hi))
        hi = lo
    return out


def fetch(reader: Reader, start: datetime, end: datetime) -> tuple[list[dict], list[str], list[str]]:
    """(fills, account labels, notes). Raises SchwabRefused on a sign-in / block / 429 / network failure."""
    accounts = reader.get("/trader/v1/accounts/accountNumbers")
    if not isinstance(accounts, list):
        raise SchwabRefused("Schwab's account list was not a list.", "bad_reply")
    fills, labels, notes = [], [], []
    for row in accounts:
        if not isinstance(row, dict) or not row.get("hashValue"):
            continue
        account_hash, label = str(row["hashValue"]), last4(row.get("accountNumber"))
        labels.append(label)
        for index, (lo, hi) in enumerate(_windows(start, end)):
            params = {"types": "TRADE,RECEIVE_AND_DELIVER", "startDate": _iso_z(lo), "endDate": _iso_z(hi)}
            try:
                payload = reader.get(f"/trader/v1/accounts/{account_hash}/transactions", params)
            except SchwabRefused as exc:
                if exc.kind == "bad_reply" and index > 0:
                    notes.append(f"Schwab gave no history older than {WINDOW_DAYS} days for {label}.")
                    break                           # an older window refused: keep what came, no retry
                raise
            fills.extend(parse_transactions(payload, label))
    return fills, labels, notes


def merge(store: dict, fills: list[dict]) -> int:
    """Add new fills by id; an existing fill is replaced by the newer copy. Returns how many were new."""
    known = store.setdefault("fills", {})
    new = 0
    for fill in fills:
        new += fill["id"] not in known          # a fill in two overlapping windows counts once
        known[fill["id"]] = fill
    return new


def _claim(trigger: str, now: datetime) -> dict | None:
    """Mark the read as started and write the claim BEFORE any request. None when it may not start.
    Caller holds _LOCK."""
    if _READING["on"]:
        return None
    state = load_state()
    if stopped_today(state, now):
        return None
    _READING.update(on=True, trigger=trigger, startedAt=now.isoformat(timespec="seconds"))
    state["lastAttemptAt"] = now.isoformat(timespec="seconds")
    if trigger == "daily":
        state["lastReadDay"] = _today(now)
    else:
        state["lastManualAt"] = now.isoformat(timespec="seconds")
    save_state(state)
    return state


def run_read(transport: Callable[[str], Any], trigger: str, now: datetime | None = None,
             after: Callable[[dict], None] | None = None, claimed: dict | None = None) -> dict:
    """One read: claim, fetch, store. Never retries. Returns the outcome {ok, kind?, newFills, calls}."""
    now = (now or datetime.now(ET)).astimezone(ET)
    today = _today(now)
    state = claimed
    if state is None:
        with _LOCK:
            state = _claim(trigger, now)
        if state is None:
            return {"ok": False, "kind": "busy" if _READING["on"] else "stopped"}
    reader = Reader(transport, state, today)
    try:
        store = load_store()
        last_at = _when(store.get("throughAt")) if store.get("throughAt") else None
        start = now - timedelta(days=FIRST_RUN_DAYS)
        if last_at is not None:
            start = max(start, last_at - timedelta(days=2))
        fills, labels, notes = fetch(reader, start, now)
        new = merge(store, fills)
        store["throughAt"] = now.isoformat(timespec="seconds")
        store["updatedAt"] = now.isoformat(timespec="seconds")
        save_store(store)
        state.update(lastReadAt=now.isoformat(timespec="seconds"), lastError=None, accounts=labels, notes=notes,
                     firstDay=min((f["day"] for f in store["fills"].values()), default=None), throughDay=today)
        save_state(state)
        if after is not None:
            try:
                after(store)
            except Exception as exc:  # noqa: BLE001 - the enrichment never undoes a good read
                state["lastError"] = f"Saved, but the extra details failed: {type(exc).__name__}"
                save_state(state)
        return {"ok": True, "newFills": new, "calls": reader.calls, "fills": len(store["fills"])}
    except NotAllowed:
        raise
    except SchwabRefused as exc:
        state["lastError"] = _scrub(f"{exc.kind}: {exc}")[:300]
        if exc.kind in SIGNIN_KINDS or exc.kind == "budget":
            state.update(stoppedDay=today, stoppedKind=exc.kind if exc.kind != "budget" else "stopped",
                         stoppedReason={"signin": "Schwab refused the sign-in", "blocked": "Schwab's edge blocked this address",
                                        "rate_limit": "Schwab said too many requests", "budget": "the day's read budget is used up"}[exc.kind])
        save_state(state)
        return {"ok": False, "kind": exc.kind, "calls": reader.calls}
    except Exception as exc:  # noqa: BLE001 - one bad read never takes the server down
        state["lastError"] = _scrub(f"{type(exc).__name__}: {exc}")[:300]
        save_state(state)
        return {"ok": False, "kind": "error", "calls": reader.calls}
    finally:
        _READING.update(on=False, trigger=None, startedAt=None)


def request_manual(transport: Callable[[str], Any], now: datetime | None = None,
                   after: Callable[[dict], None] | None = None) -> tuple[int, dict]:
    """The "refresh now" button: (HTTP status, reply). 202 = started on a quiet thread;
    429 = read in the last hour; 409 = already reading or stopped for today. The claim is
    written before this returns, so a double click cannot start two reads."""
    now = (now or datetime.now(ET)).astimezone(ET)
    with _LOCK:
        refused = manual_refusal(load_state(), now)
        if refused:
            code, words = refused
            return code, {"ok": False, "message": words, "schwab": status(now)}
        claimed = _claim("manual", now)
        if claimed is None:
            return 409, {"ok": False, "message": "Schwab sign-in needed. Reading is stopped for today.", "schwab": status(now)}
    threading.Thread(target=run_read, args=(transport, "manual"), kwargs={"after": after, "claimed": claimed, "now": now},
                     name="schwab-history-manual", daemon=True).start()
    return 202, {"ok": True, "message": "Reading from Schwab now. The page updates when it is done.", "schwab": status(now)}


def run_daily_if_due(transport: Callable[[str], Any], now: datetime | None = None,
                     after: Callable[[dict], None] | None = None) -> dict | None:
    """The scheduler's tick: one read when daily_due(), else None. Runs on the caller's thread."""
    now = (now or datetime.now(ET)).astimezone(ET)
    with _LOCK:
        if not daily_due(load_state(), now):
            return None
        claimed = _claim("daily", now)
    if claimed is None:
        return None
    return run_read(transport, "daily", now=now, after=after, claimed=claimed)
