"""Bought or sold: an ESTIMATE of which side pushed each option contract's trades today (Ganesh 2026-11-19, "I want
OI, they are buyers or sellers").

The exchange does not publish who opened a contract, so this is never a fact. What the app can see, each time it
re-reads a ticker's option chain: the contract's volume so far today, its last trade price, and the bid and ask.

- The volume added since the previous read is sorted by where the last trade printed: near the ask is "bought" (a
  buyer paid up), near the bid is "sold" (a seller hit the bid), the middle third is left unsorted.
- Volume that traded before the app first read the chain today was never seen, and is never sorted.
- The next session's open interest against this session's says whether positions were opened or closed.

Limits, said on the screen too: every contract traded between two reads is counted at the ONE last price, a trade in
the middle cannot be sorted, and a spread trade can print one leg at the ask and the other at the bid.

Pure logic first (side_of, step, read); the small store below keeps one file per ticker under the artifacts folder
(the tests' scratch folder through AGX_MOMX_GRADE_DIR). Nothing here reaches the internet.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
from datetime import datetime, time
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
#: A trade in the top or bottom 30% of the bid-ask spread is sorted; the middle 40% is not.
EDGE = 0.30
#: A read is given only when at least this many contracts were sorted, and they are at least this share of the day's volume.
MIN_SORTED = 100
MIN_SHARE_OF_VOLUME = 0.25
#: "mostly bought" at 60% or more of the sorted contracts, "mostly sold" at 40% or less, "mixed" between.
MOSTLY = 0.60
SESSION_OPEN, SESSION_CLOSE = time(9, 30), time(16, 15)
NOTE = ("Bought/sold is the app's estimate from where trades printed between the bid and the ask while it was "
        "watching. It is not exchange data.")

_LOCK = threading.Lock()
_STATES: dict[str, dict] = {}


def side_of(last: object, bid: object, ask: object) -> str | None:
    """"bought", "sold" or "mid" for one last trade price against the bid and ask; None when the quote is unusable."""
    try:
        last, bid, ask = float(last), float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not (last > 0 and bid >= 0 and ask > bid):
        return None
    place = (last - bid) / (ask - bid)
    return "bought" if place >= 1 - EDGE else "sold" if place <= EDGE else "mid"


def contract_key(side: object, expiry: object, strike: object) -> str:
    return f"{'P' if str(side or '').upper().startswith('P') else 'C'}|{str(expiry or '')[:10]}|{float(strike or 0):g}"


def _whole(value: object) -> int:
    try:
        return max(int(round(float(value))), 0)
    except (TypeError, ValueError):
        return 0


def step(state: Mapping | None, rows: Iterable[Mapping], day: str) -> dict:
    """The state after one more read of the chain. `rows` carry side, expiry, strike, volume, open_interest, bid, ask, last."""
    old = state if isinstance(state, Mapping) else {}
    same_day = old.get("day") == day
    before = old.get("contracts") if isinstance(old.get("contracts"), Mapping) else {}
    contracts: dict[str, dict] = {key: dict(value) for key, value in before.items()} if same_day else {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        strike = row.get("strike", row.get("strike_price"))
        expiry = row.get("expiry", row.get("expiry_date"))
        try:
            if float(strike or 0) <= 0 or not expiry:
                continue
        except (TypeError, ValueError):
            continue
        key = contract_key(row.get("side"), expiry, strike)
        volume = _whole(row.get("volume", row.get("total_volume")))
        oi = _whole(row.get("open_interest", row.get("openInterest")))
        seen = contracts.get(key)
        if seen is None:
            prior = before.get(key) if not same_day else None
            # First read of this contract today: what already traded was not watched, so it is never sorted.
            contracts[key] = {"v": volume, "b": 0, "s": 0, "m": 0, "u": volume, "oi": oi,
                              "poi": _whole(prior.get("oi")) if isinstance(prior, Mapping) else None,
                              "pday": old.get("day") if isinstance(prior, Mapping) else None}
            continue
        added = volume - _whole(seen.get("v"))
        if added > 0:
            bucket = {"bought": "b", "sold": "s"}.get(side_of(row.get("last"), row.get("bid"), row.get("ask")) or "", "m")
            seen[bucket] = _whole(seen.get(bucket)) + added
            seen["v"] = volume
        if oi:
            seen["oi"] = oi
    return {"day": day, "contracts": contracts}


def read(entry: Mapping | None) -> dict | None:
    """What the screen shows for one contract, or None when there is nothing worth showing."""
    if not isinstance(entry, Mapping):
        return None
    bought, sold, volume = _whole(entry.get("b")), _whole(entry.get("s")), _whole(entry.get("v"))
    sorted_count = bought + sold
    enough = sorted_count >= MIN_SORTED and volume > 0 and sorted_count >= volume * MIN_SHARE_OF_VOLUME
    share = round(bought / sorted_count, 4) if enough else None
    label = None if share is None else "mostly bought" if share >= MOSTLY else "mostly sold" if share <= 1 - MOSTLY else "mixed"
    prior = entry.get("poi")
    oi_change = (_whole(entry.get("oi")) - _whole(prior)) if prior is not None and entry.get("oi") else None
    if label is None and oi_change is None:
        return None
    return {"bought": bought, "sold": sold, "mid": _whole(entry.get("m")), "unseen": _whole(entry.get("u")), "volume": volume,
            "share": share, "label": label, "oiChange": oi_change, "oiSince": entry.get("pday") if oi_change is not None else None}


def text(strike_label: str, est: Mapping | None) -> str | None:
    """One plain line for the chat: "150 call: mostly bought, 62% of 8,400 sorted contracts (13,500 traded). ..."."""
    if not isinstance(est, Mapping):
        return None
    parts = []
    if est.get("label"):
        share = float(est["share"])
        shown = share if est["label"] != "mostly sold" else 1 - share
        word = "bought" if est["label"] != "mostly sold" else "sold"
        parts.append(f"{est['label']}, {round(shown * 100)}% {word} of {est['bought'] + est['sold']:,} sorted contracts "
                     f"({est['volume']:,} traded)")
    if est.get("oiChange") is not None:
        change = int(est["oiChange"])
        parts.append(f"open interest {'up' if change > 0 else 'down' if change < 0 else 'unchanged'}"
                     f"{f' {abs(change):,}' if change else ''} since {est.get('oiSince')}")
    return f"{strike_label}: {'; '.join(parts)}." if parts else None


# ---------------------------------------------------------------- the store

def _root() -> Path:
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    if override:
        return Path(override)
    from config import ARTIFACTS_DIR
    return Path(ARTIFACTS_DIR)


def _path(symbol: str, root: Path | None) -> Path:
    return (root or _root()) / "oi_sides" / f"{symbol}.json.gz"


def load(symbol: str, root: Path | None = None) -> dict | None:
    """The saved state for one ticker, or None."""
    symbol = str(symbol or "").strip().upper()
    with _LOCK:
        hit = _STATES.get(f"{root or ''}|{symbol}")
    if hit is not None:
        return hit
    try:
        with gzip.open(_path(symbol, root), "rt", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    return state if isinstance(state, dict) and isinstance(state.get("contracts"), dict) else None


def in_session(now: datetime) -> bool:
    local = now.astimezone(ET)
    return local.weekday() < 5 and SESSION_OPEN <= local.time() <= SESSION_CLOSE


def annotate(symbol: str, rows: list[dict], now: datetime, root: Path | None = None) -> None:
    """Record one read of the chain (regular session only) and put each row's estimate on it as row["sideEst"].

    Outside the session nothing is recorded: Schwab repeats the last session's volume, and a weekend read must not
    wipe Friday's estimate. The saved estimate is still shown.
    """
    symbol = str(symbol or "").strip().upper()
    if not symbol or not rows:
        return
    state = load(symbol, root)
    if in_session(now):
        state = step(state, rows, now.astimezone(ET).date().isoformat())
        with _LOCK:
            _STATES[f"{root or ''}|{symbol}"] = state
        _save(symbol, state, root)
    contracts = (state or {}).get("contracts") or {}
    for row in rows:
        try:
            est = read(contracts.get(contract_key(row.get("side"), row.get("expiry"), row.get("strike"))))
        except (TypeError, ValueError):
            est = None
        if est:
            row["sideEst"] = {**est, "day": state.get("day")}


def _save(symbol: str, state: dict, root: Path | None) -> None:
    path = _path(symbol, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with gzip.open(scratch, "wt", encoding="utf-8") as handle:
            json.dump(state, handle, separators=(",", ":"))
        os.replace(scratch, path)
    finally:
        try:
            scratch.unlink()
        except OSError:
            pass


def reset() -> None:
    """Forget what is held in memory (tests)."""
    with _LOCK:
        _STATES.clear()
