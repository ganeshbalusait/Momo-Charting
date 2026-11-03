#!/usr/bin/env python
"""Option bid/ask SPREAD scanner - offline, from the app's saved option chains.

For every ticker on a scanner list it picks the option the Bot would buy
(momx.option_track's own rules: the nearest expiry 1+ days out, the first OTM
strike that has a price - a call for bull, a put for bear) and shows how wide
its bid/ask spread is, using the chains the app already saved to
artifacts/oi_chain_cache/<SYMBOL>.json.gz.

It only reads files. It never calls Schwab, Alpaca, Tradier or the running
app, so it is safe to run while the market is open.

    python scripts\\option_spread_scan.py                       # active scanner list, calls + puts
    python scripts\\option_spread_scan.py --list Watchlist --side call --top 40
    python scripts\\option_spread_scan.py --symbols AAPL,TSLA,ACB --picks all
    python scripts\\option_spread_scan.py --csv spreads.csv

WHY THE VERDICT CUT-OFFS ARE 10% / 15% (evidence run 2026-11-03):
71,170 virtual Bot trades from Oct 9 to Nov 2 recorded their entry bid/ask. The
Bot's scorecard buys and sells at the MID, so it never charges the spread. A
walk-forward test chose the cut-off on Oct 9-26 only and checked it on the
unseen days Oct 27 - Nov 2. The training days picked 12.5% (calls) and 15%
(puts), and a fixed 10% cut-off did about the same. Skipping spreads above the
cut-off raised the average trade by about 14 points at a half-spread fill and
about 24 points buying at the ask and selling at the bid. It was better on 5 of
5 unseen days. It is NOT proven at mid fills: there it cost 1.3 points. The
trades it kept were still about break-even, so TIGHT means "cheap to trade",
not "a winner".
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")

#: Same rules as api_server._load_oi_finder_chain_disk_payload, so a chain the
#: app would refuse is refused here too.
CHAIN_SCHEMA = 1                          # OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION
CHAIN_MAX_DISK_AGE_SECONDS = 5 * 24 * 3600  # OI_FINDER_CHAIN_DISK_CACHE_MAX_AGE_SECONDS
SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")  # _oi_finder_chain_disk_path

#: momx.board.MEGA7_SEED. Copied, not imported: momx.board pulls in the broker
#: data modules, which this script must never load.
MAG7_SEED = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO")

#: The saved chains hold expiries 0-31 days out (maxDaysToExpiration 31). Within
#: that range the Bot always takes the nearest expiry 1+ days out.
DEFAULT_MAX_DTE = 31
DEFAULT_STALE_MINUTES = 60
ENTRY_DELTA = 0.20  # momx.option_track.ENTRY_DELTA (used only by the built-in copy)

#: Verdict cut-offs on spread as a fraction of the mid. See the module docstring
#: for the out-of-sample test behind them.
TIGHT_MAX = 0.10
OK_MAX = 0.15

VERDICT_LINE = (
    "VERDICT (spread as % of mid): TIGHT <=10% | OK 10-15% | WIDE >15% = skip. Tested on unseen days "
    "(Oct 27-Nov 2): skipping wide spreads raised the average trade 14-24 points when you pay any of the "
    "spread (better on 5 of 5 days). Tighter is always cheaper - 10%/15% are practical lines, not a proven "
    "best cut-off. No effect if you always fill at the mid. TIGHT = cheap to trade, not a buy signal."
)
LEGEND_LINE = (
    "SPRD% = (ask-bid)/mid = what the option must gain at the mid just to break even if you buy at the ask "
    "and sell at the bid.  COST$ = that round trip in dollars for ONE contract (spread x 100).  OI/VOL '-' = "
    "not in the chain.  AGEm = minutes since the app last read this chain from Schwab."
)
PICK_LEGEND = (
    "PICK: MAIN = the Bot's contract (nearest expiry 1+ days out, first OTM strike); D20 = its 0.20-delta "
    "strike on the same expiry; 0DTE = the first OTM strike expiring today."
)
PICKS = ("main", "d20", "0dte")
PICK_LABEL = {"main": "MAIN", "d20": "D20", "0dte": "0DTE"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --------------------------------------------------------------------------- small helpers

def _m(v: Any) -> Mapping:
    return v if isinstance(v, Mapping) else {}


def _num(v: Any) -> float | None:
    """momx.option_track._num: a float, None for junk and NaN."""
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _ts(raw: Any) -> float | None:
    """Epoch seconds from an ISO time string, None if unusable."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.timestamp()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing or broken file is just "not there"
        return None


def _norm_list_name(name: str) -> str:
    return " ".join(str(name or "").replace("_", " ").replace("-", " ").lower().split())


def _clean_symbols(items: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items if isinstance(items, (list, tuple)) else []:
        sym = str(item or "").strip().upper()
        if sym and sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


# --------------------------------------------------------------------------- the Bot's contract rules

def _price_copy(r: Mapping) -> float:
    bid, ask = _num(r.get("bid")), _num(r.get("ask"))
    if (bid is not None and ask is not None and math.isfinite(bid) and math.isfinite(ask)
            and bid >= 0 and ask > 0 and ask >= bid):
        return round((bid + ask) / 2, 4)
    return 0.0


def pick_contract_copy(chain: Any, bear: bool, zero_dte: bool = False) -> dict | None:
    """Built-in copy of momx.option_track.pick_contract, used ONLY if that
    import fails. tests/test_option_spread_scan.py checks the two agree."""
    spot = _num(_m(chain).get("underlyingPrice"))
    rows = _m(chain).get("selectedExpiryChainRows")
    if not spot or spot <= 0 or not isinstance(rows, list):
        return None
    side = "PUT" if bear else "CALL"
    legs = [r for r in rows if isinstance(r, Mapping) and r.get("side") == side and r.get("symbol")
            and _num(r.get("strike")) and r.get("expiry")
            and (((_num(r.get("days_to_expiration")) or 0) == 0) if zero_dte
                 else ((_num(r.get("days_to_expiration")) or 0) >= 1))
            and ((_num(r["strike"]) < spot) if bear else (_num(r["strike"]) > spot)) and _price_copy(r) > 0]
    if not legs:
        return None
    expiry = min(str(r["expiry"])[:10] for r in legs)
    legs = [r for r in legs if str(r["expiry"])[:10] == expiry]
    best = min(legs, key=lambda r: abs(_num(r["strike"]) - spot))
    return {"contract": best["symbol"], "strike": _num(best["strike"]), "expiry": expiry,
            "delta": _num(best.get("delta")), "spot": round(spot, 4), "entry": _price_copy(best),
            "entryBid": _num(best.get("bid")), "entryAsk": _num(best.get("ask"))}


def pick_entry_strike_copy(chain: Any, bear: bool, expiry: str) -> dict | None:
    """Built-in copy of momx.option_track.pick_entry_strike (0.20-delta strike)."""
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
        if strike is None or not r.get("symbol") or _price_copy(r) <= 0 or not (0 < delta < ENTRY_DELTA):
            continue
        if (strike < spot) if bear else (strike > spot):
            legs.append((abs(ENTRY_DELTA - delta), abs(strike - spot), r))
    if not legs:
        return None
    best = min(legs, key=lambda x: (x[0], x[1]))[2]
    return {"eContract": best["symbol"], "eStrike": _num(best["strike"]), "eDelta": _num(best.get("delta")),
            "eEntry": _price_copy(best), "eEntryBid": _num(best.get("bid")), "eEntryAsk": _num(best.get("ask"))}


def load_picker() -> tuple[Callable, Callable, str | None]:
    """The Bot's live rules from momx.option_track (stdlib-only, no network, no
    thread at import). Falls back to the built-in copy, with the reason."""
    try:
        if str(REPO) not in sys.path:
            sys.path.append(str(REPO))
        from momx import option_track as ot

        return ot.pick_contract, ot.pick_entry_strike, None
    except Exception as exc:  # noqa: BLE001
        return pick_contract_copy, pick_entry_strike_copy, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- spread math + verdict

def spread_stats(bid: Any, ask: Any) -> dict | None:
    """Spread of one quote. None when the quote has no usable market
    (no ask, a negative bid, or a crossed bid > ask)."""
    bid, ask = _num(bid), _num(ask)
    if bid is None or ask is None or not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if bid < 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    spread = ask - bid
    half = spread / 2
    return {
        "bid": bid, "ask": ask, "mid": mid, "spread": spread,
        "spreadPct": spread / mid,
        # Half the spread paid buying (ask vs mid) plus half paid selling (mid vs
        # bid), with the exit spread taken as today's (in the Bot's history the
        # exit spread matched the entry spread): one full spread per contract.
        "roundTripPct": (half + half) / mid,
        "costPerContract": spread * 100,
    }


def verdict(spread_pct: float | None) -> str:
    if spread_pct is None:
        return "?"
    pct = round(spread_pct, 6)  # 0.95/1.05 must read as exactly 10%, not 10.0000001%
    if pct <= TIGHT_MAX:
        return "TIGHT"
    if pct <= OK_MAX:
        return "OK"
    return "WIDE"


# --------------------------------------------------------------------------- files: chains, lists, spots

def fresh_rows(rows: Any, today: date, max_dte: int | None = None) -> list[dict]:
    """api_server.option_contract_quotes: drop expired expiries and recompute
    days_to_expiration from TODAY (the stored value is as of the scan day)."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, Mapping):
            continue
        try:
            expiry = date.fromisoformat(str(r.get("expiry") or "")[:10])
        except ValueError:
            continue
        dte = (expiry - today).days
        if dte < 0 or (max_dte is not None and dte > max_dte):
            continue
        out.append({**r, "days_to_expiration": dte})
    return out


def load_chain(chain_dir: Path, symbol: str, now_ts: float) -> tuple[dict | None, str | None]:
    """(chain, None) or (None, why it was skipped). Never raises."""
    if not SYMBOL_RE.fullmatch(symbol):
        return None, "not a symbol the chain cache can hold"
    path = chain_dir / f"{symbol}.json.gz"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None, "no saved chain"
    age = now_ts - mtime
    if age > CHAIN_MAX_DISK_AGE_SECONDS:
        return None, "chain over 5 days old (the app ignores it too)"
    try:
        # One decompress + one parse of the bytes: ~25% faster than a text-mode gzip stream.
        payload = json.loads(gzip.decompress(path.read_bytes()))
    except Exception as exc:  # noqa: BLE001 - truncated gzip, bad JSON, half-written file
        return None, f"chain file unreadable ({type(exc).__name__})"
    if not isinstance(payload, dict) or not payload.get("symbol"):
        return None, "chain file has no symbol"
    if payload.get("optionQuoteSchemaVersion") != CHAIN_SCHEMA:
        return None, f"old chain format (schema {payload.get('optionQuoteSchemaVersion')!r})"
    rows = payload.get("selectedExpiryChainRows")
    if not isinstance(rows, list) or not rows:
        return None, "chain has no contracts"
    scanned = _ts(payload.get("scannedAt"))
    payload["_scannedTs"] = scanned if scanned is not None else mtime
    return payload, None


def _count(value: Any) -> int | None:
    """A whole-number count (OI, volume), or None when missing or not a finite number."""
    number = _num(value)
    if number is None or not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _is_etf(symbol: str) -> bool:
    """An ETF/index the Bot never trades (momx.option_track._etf_excluded: only
    BOT_TRADED_ETFS - SPY, QQQ - are allowed among ETFs and indexes)."""
    try:
        from momx import etf_list
        try:
            from momx.option_track import BOT_TRADED_ETFS as allowed
        except Exception:  # noqa: BLE001
            allowed = frozenset({"SPY", "QQQ"})
        return bool(etf_list.is_etf(symbol)) and str(symbol).upper() not in allowed
    except Exception:  # noqa: BLE001 - no ETF list: no note
        return False


def board_spots(board_dir: Path, now_ts: float) -> dict[str, tuple[float, float]]:
    """{symbol: (last price, board time)} from the scanner's saved boards
    (newest board wins). The Bot re-anchors its strike on the live stock price;
    this is the freshest price on disk."""
    spots: dict[str, tuple[float, float]] = {}
    try:
        files = [p for p in board_dir.glob("*.json")
                 if not p.name.startswith(".") and not p.name.endswith(".bear.json")]
    except OSError:
        return spots
    for path in files:
        try:
            if now_ts - path.stat().st_mtime > 24 * 3600:
                continue
        except OSError:
            continue
        doc = _read_json(path)
        if not isinstance(doc, dict):
            continue
        made = _ts(doc.get("generatedAt"))
        if made is None:
            continue
        rows_in = [r for key in ("rows", "rest") if isinstance(doc.get(key), list) for r in doc[key]]
        for row in rows_in:
            if not isinstance(row, Mapping):
                continue
            sym = str(row.get("symbol") or "").upper()
            last = _num(row.get("last"))
            if sym and last and last > 0 and math.isfinite(last) and (sym not in spots or made > spots[sym][1]):
                spots[sym] = (last, made)
    return spots


def known_lists(root: Path) -> list[str]:
    names: list[str] = []
    universe = _read_json(root / "artifacts" / "momx_universe.json")
    for name in _m(_m(universe).get("lists")):
        names.append(str(name))
    try:
        for p in sorted((root / "artifacts" / "momx_board_cache").glob("*.json")):
            if p.name.startswith(".") or p.name.endswith(".bear.json"):
                continue
            names.append(p.stem.replace("_", " "))
    except OSError:
        pass
    names += ["Watchlist", "Mag7"]
    out, seen = [], set()
    for n in names:
        if _norm_list_name(n) not in seen:
            seen.add(_norm_list_name(n))
            out.append(n)
    return out


def resolve_symbols(root: Path, list_name: str | None = None, symbols: str | None = None
                    ) -> tuple[str, list[str], str]:
    """(label, symbols, where they came from). Empty symbols = unknown list.

    Default list = the scanner's active list (artifacts/momx_universe.json),
    else Watchlist. A list is read from momx_universe.json, else the scanner's
    saved board (artifacts/momx_board_cache/<List_Name>.json), else
    watchlist.txt (Watchlist) / the Mag7 seed (Mag7)."""
    if symbols:
        return "your symbols", _clean_symbols(str(symbols).replace(" ", ",").split(",")), "--symbols"
    universe = _read_json(root / "artifacts" / "momx_universe.json")
    lists = _m(_m(universe).get("lists"))
    name = list_name or str(_m(universe).get("active") or "") or "Watchlist"
    key = _norm_list_name(name)
    for listed, syms in lists.items():
        if _norm_list_name(listed) == key and _clean_symbols(syms):
            return str(listed), _clean_symbols(syms), "momx_universe.json"
    board_dir = root / "artifacts" / "momx_board_cache"
    try:
        boards = [p for p in board_dir.glob("*.json")
                  if not p.name.startswith(".") and not p.name.endswith(".bear.json")]
    except OSError:
        boards = []
    for path in boards:
        if _norm_list_name(path.stem) == key:
            doc = _m(_read_json(path))
            syms = _clean_symbols(doc.get("universe"))
            if not syms:
                syms = _clean_symbols([_m(r).get("symbol") for r in list(doc.get("rows") or [])
                                       + list(doc.get("rest") or [])])
            if syms:
                return str(doc.get("list") or path.stem.replace("_", " ")), syms, "scanner board cache"
    if key == "watchlist":
        try:
            text = (root / "watchlist.txt").read_text(encoding="utf-8")
        except OSError:
            text = ""
        syms = _clean_symbols(text.replace(",", " ").split())
        if syms:
            return "Watchlist", syms, "watchlist.txt"
    if key == "mag7":
        return "Mag7", list(MAG7_SEED), "Mag7 seed"
    return name, [], "unknown list"


# --------------------------------------------------------------------------- the scan

def _call(fn: Callable, *args, **kwargs) -> dict | None:
    try:
        out = fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 - one odd chain must not stop the scan
        return None
    return out if isinstance(out, dict) else None


def scan(symbols: list[str], sides: tuple[bool, ...] = (False, True), picks: tuple[str, ...] = ("main",),
         max_dte: int | None = DEFAULT_MAX_DTE, root: Path = REPO, now_ts: float | None = None,
         picker: tuple[Callable, Callable] | None = None) -> dict:
    """Pick and measure the contracts. sides: False = bull/call, True = bear/put."""
    now_ts = time.time() if now_ts is None else now_ts
    today = datetime.fromtimestamp(now_ts, ET).date()
    pick_contract, pick_entry_strike = picker or load_picker()[:2]
    chain_dir = root / "artifacts" / "oi_chain_cache"
    spots = board_spots(root / "artifacts" / "momx_board_cache", now_ts)
    rows: list[dict] = []
    skipped: list[tuple[str, str]] = []
    ages: dict[str, float] = {}
    loaded = 0
    for sym in symbols:
        chain, why = load_chain(chain_dir, sym, now_ts)
        if chain is None:
            skipped.append((sym, why or "unusable chain"))
            continue
        loaded += 1
        scanned_ts = chain["_scannedTs"]
        age_min = max(0.0, (now_ts - scanned_ts) / 60)
        ages[sym] = age_min
        legs = fresh_rows(chain.get("selectedExpiryChainRows"), today, max_dte)
        if not legs:
            skipped.append((sym, "no unexpired expiry in the chain" + (
                f" within {max_dte} days" if max_dte is not None else "")))
            continue
        spot = _num(chain.get("underlyingPrice"))
        spot_src = "chain"
        board = spots.get(sym)
        if board and (not spot or spot <= 0 or board[1] > scanned_ts):
            spot, spot_src = board[0], "scanner"  # the Bot re-anchors on the live price
        if not spot or spot <= 0:
            skipped.append((sym, "no stock price"))
            continue
        view = {**chain, "underlyingPrice": spot, "selectedExpiryChainRows": legs}
        by_contract = {str(r.get("symbol")): r for r in legs if r.get("symbol")}
        has_next_day = any(r["days_to_expiration"] >= 1 for r in legs)
        for bear in sides:
            side = "PUT" if bear else "CALL"
            who = f"{sym} {side.lower()}"
            main = _call(pick_contract, view, bear)
            chosen: list[tuple[str, str]] = []
            for kind in picks:
                label = PICK_LABEL[kind]
                if kind == "main":
                    contract = (main or {}).get("contract")
                    why = ("no expiry 1+ days out" + (f" within {max_dte} days" if max_dte is not None else "")
                           if not has_next_day else f"no OTM {side.lower()} with a bid/ask")
                elif kind == "d20":
                    e = _call(pick_entry_strike, view, bear, main["expiry"]) if main else None
                    contract = (e or {}).get("eContract")
                    why = "no main contract" if not main else "no strike under 0.20 delta with a bid/ask"
                else:
                    z = _call(pick_contract, view, bear, zero_dte=True)
                    contract = (z or {}).get("contract")
                    why = "no OTM contract expiring today"
                if contract and str(contract) in by_contract:
                    chosen.append((label, str(contract)))
                else:
                    skipped.append((who if kind == "main" else f"{who} {label}", why))
            for label, contract in chosen:
                leg = by_contract[contract]
                stats = spread_stats(leg.get("bid"), leg.get("ask"))
                if stats is None:  # cannot happen for a Bot pick (it needs a price); guard anyway
                    skipped.append((f"{who} {label}", "contract has no usable bid/ask"))
                    continue
                notes = []
                if stats["bid"] == 0:
                    notes.append("no bid")
                if _is_etf(sym):
                    notes.append("ETF/index - Bot skips")
                rows.append({
                    "ticker": sym, "side": side, "pick": label, "contract": contract,
                    "expiry": str(leg.get("expiry"))[:10], "dte": leg["days_to_expiration"],
                    "strike": _num(leg.get("strike")), "spot": spot, "spotSource": spot_src,
                    "delta": _num(leg.get("delta")),
                    "openInterest": _count(leg.get("open_interest")),
                    "volume": _count(leg.get("volume")),
                    "chainScannedAt": chain.get("scannedAt"), "ageMin": age_min,
                    **stats, "verdict": verdict(stats["spreadPct"]), "notes": notes,
                })
    rows.sort(key=lambda r: (r["spreadPct"], -(r["volume"] or 0), r["ticker"], r["side"], r["pick"]))
    return {"rows": rows, "skipped": skipped, "ages": ages, "loaded": loaded, "symbols": len(symbols)}


# --------------------------------------------------------------------------- output

def _px(v: float | None, places: int = 2) -> str:
    return "-" if v is None else f"{v:.{places}f}"


def _strike(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _expiry(raw: str) -> str:
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        return raw
    return f"{MONTHS[d.month - 1]} {d.day:02d}"


def _age(minutes: float) -> str:
    return f"{minutes / 60:.1f}h" if minutes < 48 * 60 else f"{minutes / 1440:.1f}d"


def _median(values: list[float]) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _names(items: list[str], limit: int = 25) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" ... +{len(items) - limit} more" if len(items) > limit else "")


def render(result: dict, *, label: str, side_text: str, picks: tuple[str, ...], stale_min: float,
           top: int, now_ts: float, elapsed: float, picker_note: str | None = None) -> list[str]:
    rows = result["rows"]
    stale = sorted(((s, a) for s, a in result["ages"].items() if a > stale_min), key=lambda x: -x[1])
    stale_set = {s for s, _ in stale}
    now_et = datetime.fromtimestamp(now_ts, ET)
    show_pick = tuple(picks) != ("main",)
    lines = [VERDICT_LINE, LEGEND_LINE]
    if show_pick:
        lines.append(PICK_LEGEND)
    lines.append(
        f"Spread scan {now_et:%Y-%m-%d %H:%M} ET | {label} ({result['symbols']} tickers) | {side_text} | "
        + ("contract: the Bot's pick (nearest expiry 1+ days out, first OTM strike) | " if not show_pick else "")
        + f"prices: the app's saved Schwab chains, not live quotes | {result['loaded']} chains read, "
        f"{len(stale)} older than {stale_min:g} min")
    if picker_note:
        lines.append(f"NOTE: momx.option_track did not import ({picker_note}); used the built-in copy of its rules.")
    lines.append("")

    cols = [("TICKER", 6, "<"), ("SIDE", 4, "<")] + ([("PICK", 4, "<")] if show_pick else []) + [
        ("EXPIRY", 6, "<"), ("DTE", 3, ">"), ("STRIKE", 7, ">"), ("BID", 6, ">"), ("ASK", 6, ">"),
        ("MID", 7, ">"), ("SPRD$", 5, ">"), ("SPRD%", 6, ">"), ("COST$", 6, ">"), ("OI", 6, ">"),
        ("VOL", 7, ">"), ("AGEm", 5, ">"), ("VERDICT", 7, "<"), ("NOTE", 0, "<")]

    def line(values: list[str]) -> str:
        parts = [f"{v:{align}{width}}" if width else v for v, (_, width, align) in zip(values, cols)]
        return " ".join(parts).rstrip()

    shown = rows[:top] if top and top > 0 else rows
    if shown:
        lines.append(line([c[0] for c in cols]))
        for r in shown:
            notes = list(r["notes"])
            if r["ticker"] in stale_set:
                notes.append(f"STALE {_age(r['ageMin'])}")
            values = [r["ticker"], r["side"]] + ([r["pick"]] if show_pick else []) + [
                _expiry(r["expiry"]), str(r["dte"]), _strike(r["strike"]), _px(r["bid"]), _px(r["ask"]),
                _px(r["mid"], 3), _px(r["spread"]), f"{r['spreadPct'] * 100:.1f}", f"{r['costPerContract']:.0f}",
                "-" if r["openInterest"] is None else str(r["openInterest"]),
                "-" if r["volume"] is None else str(r["volume"]), str(int(round(r["ageMin"]))), r["verdict"],
                ", ".join(notes)]
            lines.append(line(values))
    else:
        lines.append("No contracts to show (see SKIPPED below).")
    lines.append("")

    if rows:
        counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("TIGHT", "OK", "WIDE")}
        no_bid = sum(1 for r in rows if r["bid"] == 0)
        med = _median([r["spreadPct"] for r in rows])
        per_side = []
        for side in ("CALL", "PUT"):
            m = _median([r["spreadPct"] for r in rows if r["side"] == side])
            if m is not None:
                per_side.append(f"{side.lower()}s {m * 100:.1f}%")
        summary = (f"{len(rows)} contracts: TIGHT {counts['TIGHT']}, OK {counts['OK']}, WIDE {counts['WIDE']}"
                   f" ({no_bid} with no bid) | median spread {med * 100:.1f}%"
                   + (f" ({', '.join(per_side)})" if len(per_side) > 1 else ""))
        if len(shown) < len(rows):
            summary += f" | showing the tightest {len(shown)} (--top {top})"
        lines.append(summary)
    if stale:
        lines.append(f"STALE CHAINS: {len(stale)} older than {stale_min:g} min - their bid/ask may have moved, "
                     f"trust those rows less: " + _names([f"{s} {_age(a)}" for s, a in stale]))
    if result["skipped"]:
        by_reason: dict[str, list[str]] = {}
        for who, why in result["skipped"]:
            by_reason.setdefault(why, []).append(who)
        lines.append(f"SKIPPED {len(result['skipped'])}:")
        for why, who in sorted(by_reason.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {why} ({len(who)}): {_names(who)}")
    lines.append(f"Done in {elapsed:.1f}s - files only, no broker or app calls.")
    return [_ascii(x) for x in lines]


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = ["ticker", "side", "pick", "contract", "expiry", "dte", "strike", "spot", "spot_source", "delta",
              "bid", "ask", "mid", "spread", "spread_pct", "cost_per_contract", "open_interest", "volume",
              "chain_scanned_at", "chain_age_min", "verdict", "note"]
    with open(path, "w", newline="", encoding="ascii", errors="replace") as fh:
        writer = csv.writer(fh)
        writer.writerow(fields)
        for r in rows:
            writer.writerow([r["ticker"], r["side"], r["pick"], r["contract"], r["expiry"], r["dte"], r["strike"],
                             r["spot"], r["spotSource"], r["delta"], r["bid"], r["ask"], round(r["mid"], 4),
                             round(r["spread"], 4), round(r["spreadPct"] * 100, 2),
                             round(r["costPerContract"], 2), r["openInterest"], r["volume"],
                             r["chainScannedAt"], round(r["ageMin"], 1), r["verdict"], "; ".join(r["notes"])])


# --------------------------------------------------------------------------- command line

def _parse_picks(raw: str) -> tuple[str, ...]:
    items = [p.strip().lower() for p in str(raw or "").split(",") if p.strip()]
    if not items or "all" in items:
        return PICKS if items else ("main",)
    bad = [p for p in items if p not in PICKS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown pick {', '.join(bad)} (use main, d20, 0dte or all)")
    return tuple(p for p in PICKS if p in items)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="option_spread_scan.py",
        description="Bid/ask spread of the option the Bot would buy, per ticker, from the app's saved chains "
                    "(files only - no broker calls).")
    p.add_argument("--list", dest="list_name", metavar="NAME",
                   help="scanner list (default: the scanner's active list, else Watchlist)")
    p.add_argument("--symbols", metavar="A,B,C", help="these tickers instead of a list")
    p.add_argument("--side", choices=("call", "put", "both"), default="both",
                   help="call = bull, put = bear (default both)")
    p.add_argument("--picks", type=_parse_picks, default=("main",), metavar="main,d20,0dte|all",
                   help="which contracts: main (the Bot's buy, default), d20 (0.20 delta), 0dte, or all")
    p.add_argument("--max-dte", type=int, default=DEFAULT_MAX_DTE, metavar="N",
                   help=f"only expiries at most N days out (default {DEFAULT_MAX_DTE} = everything the saved "
                        "chains hold; like the Bot, the nearest expiry 1+ days out is taken)")
    p.add_argument("--top", type=int, default=0, metavar="N", help="show only the N tightest (default all)")
    p.add_argument("--csv", metavar="PATH", help="also write every row to this CSV file")
    p.add_argument("--stale-min", type=float, default=DEFAULT_STALE_MINUTES, metavar="M",
                   help=f"warn when a chain is older than M minutes (default {DEFAULT_STALE_MINUTES})")
    return p


def main(argv: list[str] | None = None, root: Path | None = None, now_ts: float | None = None) -> int:
    started = time.perf_counter()
    root = Path(root) if root else REPO
    args = build_parser().parse_args(argv)
    label, symbols, _source = resolve_symbols(root, args.list_name, args.symbols)
    if not symbols:
        print(_ascii(f"No tickers found for list '{label}'. Lists on disk: {', '.join(known_lists(root))}"))
        return 2
    sides = {"call": (False,), "put": (True,), "both": (False, True)}[args.side]
    side_text = {"call": "calls (bull)", "put": "puts (bear)", "both": "calls + puts"}[args.side]
    pick_contract, pick_entry_strike, picker_note = load_picker()
    now_ts = time.time() if now_ts is None else now_ts
    result = scan(symbols, sides, args.picks, args.max_dte, root, now_ts, (pick_contract, pick_entry_strike))
    lines = render(result, label=label, side_text=side_text, picks=args.picks, stale_min=args.stale_min,
                   top=args.top, now_ts=now_ts, elapsed=time.perf_counter() - started, picker_note=picker_note)
    code = 0
    if args.csv:
        try:
            write_csv(Path(args.csv), result["rows"])
            lines.insert(-1, _ascii(f"CSV: {len(result['rows'])} rows written to {args.csv}"))
        except OSError as exc:
            lines.insert(-1, _ascii(f"CSV NOT written ({args.csv}): {exc}"))
            code = 1
    sys.stdout.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.stdout.flush()
    except BrokenPipeError:  # e.g. piped into `more` / `head` and closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        exit_code = 0
    sys.exit(exit_code)
