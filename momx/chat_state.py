"""What the Ask chat may read from the running app's memory (2026-11-11). Read only.

The chat must never start a Schwab call, a chart build, a refresh or a phone push, so it does not get
the app's STATE object to call methods on. These few functions peek at values already in memory and
return None when there is nothing there; none of them can start any work. The route hands over the
two objects with bind(); until then (a test, the scanner worker) everything reads as empty.
"""
from __future__ import annotations

import sys
from typing import Any

_BOUND: dict[str, Any] = {"state": None, "stream": None}


def bind(state: Any = None, stream: Any = None) -> None:
    """Called by the /api/bot-chat route with the app's own objects."""
    _BOUND.update(state=state, stream=stream)


def unbind() -> None:
    _BOUND.update(state=None, stream=None)


def _app(name: str) -> Any:
    key = {"STATE": "state", "MARKET_STREAM": "stream"}[name]
    if _BOUND[key] is not None:
        return _BOUND[key]
    for module in ("api_server", "__main__"):
        found = getattr(sys.modules.get(module), name, None)
        if found is not None:
            return found
    return None


def state() -> Any:
    """The app's STATE for a plain attribute peek (getattr on a cache). Never call its methods from the chat."""
    return _app("STATE")


def stream() -> Any:
    return _app("MARKET_STREAM")


def latest_equity(symbol: str) -> dict | None:
    """The newest streamed quote this process already holds for a symbol."""
    live = stream()
    if live is None:
        return None
    try:
        return (live.latest_equities([symbol]) or {}).get(str(symbol).upper())
    except Exception:  # noqa: BLE001 - an empty answer, never an error in the chat
        return None


def earnings_cache() -> Any:
    """The earnings calendar cache as it stands, at any age: (saved_at, watchlist, payload), or None after a restart."""
    return getattr(state(), "earnings_calendar_cache", None)


def chain_cache(symbol: str) -> Any:
    """The option chain held in memory for a symbol, or None. Does not fetch."""
    held = getattr(state(), "oi_finder_chain_cache", None)
    if not isinstance(held, dict):
        return None
    try:
        return held.get(str(symbol).upper())
    except Exception:  # noqa: BLE001
        return None


def news_latest(symbols: list[str]) -> dict | None:
    """Stored ticker-tagged headlines (database only, never scrapes), or None when the store is not up."""
    opener = getattr(state(), "_momx_news", None)     # builds the store object if needed; only the store's latest() reads, and it never scrapes
    if opener is None:
        return None
    try:
        return opener().latest(symbols)
    except Exception:  # noqa: BLE001
        return None
