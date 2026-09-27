from __future__ import annotations

"""A user's own Alpaca key, and the prices it fetches.

The MY DATA PROVIDER KEYS card has always written an Alpaca key into the
user's encrypted vault row and reported it configured. Nothing ever read it:
both credential lookups in api_server select "first active admin", so every
user's data came off the owner's key while their own sat unused. This module
is what makes that card tell the truth.

Mirrors user_schwab on purpose - same cache key shape, same never-shared
invariant - so the two read the same way and a reader who understands one
understands the other.
"""

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# The vault slot the settings card already writes, with the field names it
# already uses.
ALPACA_PROVIDER = "alpaca_market_data"


class UserAlpacaClients:
    """One Alpaca data client per user, built from their encrypted vault row.

    Cached because the live quote path polls about once a second per open
    chart. Keyed by user id and never shared: a client built from one user's
    key serving another would spend the wrong person's quota while the UI
    claimed otherwise, which is invisible from outside.
    """

    def __init__(self, credentials_reader, client_factory):
        self._read_credentials = credentials_reader
        self._build_client = client_factory
        self._clients: dict[str, object] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _user_id(user) -> str:
        if isinstance(user, dict):
            return str(user.get("id") or "").strip()
        return str(user or "").strip()

    def for_user(self, user):
        """This user's own Alpaca client, or None if they have no key."""
        user_id = self._user_id(user)
        if not user_id:
            return None
        with self._lock:
            cached = self._clients.get(user_id)
            if cached is not None:
                return cached
            try:
                credentials = self._read_credentials(user_id, ALPACA_PROVIDER)
            except Exception:
                # A vault read failing must degrade to "no personal client",
                # not to a 500 on every chart poll.
                return None
            key = str((credentials or {}).get("key_id") or "").strip()
            secret = str((credentials or {}).get("secret_key") or "").strip()
            if not key or not secret:
                return None
            try:
                client = self._build_client(key, secret)
            except Exception:
                return None
            self._clients[user_id] = client
            return client

    def invalidate(self, user_id) -> None:
        """Drop the cached client so the next request picks up a new key."""
        target = self._user_id(user_id)
        if not target:
            return
        with self._lock:
            self._clients.pop(target, None)


def previous_closes(client, symbols) -> dict:
    """Prior REGULAR-SESSION close per symbol, for computing a day move.

    last_prices() deliberately returns only a last price - it runs on the 1s
    chart poll and must stay as small as possible. The quick-ticker rails need a
    PERCENTAGE though, and a percentage needs a prior close, so this is the
    separate, once-a-day half. The caller caches it; a prior close does not
    change during a session.

    Asks for DAILY BARS over an explicit range and takes the newest session
    strictly before today, rather than the snapshot's ``previous_daily_bar``.
    That field was the first implementation and it is WRONG during extended
    hours - it resolves a different session boundary. Measured 2026-08-27 08:20
    ET, overnight session, against Schwab's close_price for the same instant:

        sym    snapshot   daily bars   Schwab close
        AAPL    309.895       313.45         313.45
        NVDA     212.96       209.66         209.66
        TSLA     350.24       345.82         345.82
        NFLX      82.25        81.46          81.46

    Daily bars agree with Schwab exactly on every symbol; the snapshot agrees on
    none. The consequence was not cosmetic: AAPL rendered +0.16% GREEN on a
    stock that was -1.08% red. A wrong-direction badge on a trading surface is
    worse than no badge, which is why this resolves the session explicitly
    instead of trusting a provider's idea of "previous".

    Never raises, for the same reason last_prices does not: a provider outage
    must show as a missing badge, not a broken price feed.
    """
    wanted = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    if client is None or not wanted:
        return {}

    # "Today" in MARKET time. The machine runs on US/Central, so using its local
    # date would misclassify the current session for part of every day.
    try:
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.utcnow().date()

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        # 12 days covers a long weekend plus a holiday and still returns a
        # handful of rows per symbol.
        response = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=wanted,
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=12),
            end=datetime.now(),
        ))
    except ImportError:
        # Tests inject a fake client; alpaca-py is only needed for the real one.
        try:
            response = client.get_stock_bars(wanted)
        except Exception:
            return {}
    except Exception:
        return {}

    data = getattr(response, "data", response) or {}
    closes = {}
    for symbol in wanted:
        rows = data.get(symbol) or []
        best_date = None
        best_close = None
        for bar in rows:
            stamp = getattr(bar, "timestamp", None)
            session = getattr(stamp, "date", lambda: None)()
            # Strictly BEFORE today: during regular hours a partial bar for the
            # current session already exists, and using it would compare a
            # stock against itself.
            if session is None or session >= today:
                continue
            if best_date is None or session > best_date:
                best_date, best_close = session, getattr(bar, "close", None)
        try:
            value = float(best_close)
        except (TypeError, ValueError):
            continue
        # Zero is not a close. Letting it through would divide a day move by
        # zero, or paint an infinite percentage.
        if value <= 0:
            continue
        closes[symbol] = value
    return closes


def last_prices(client, symbols) -> dict:
    """Latest trade per symbol, in the shape the quote path already speaks.

    Returns {symbol: {"last_price": float}}, matching the Schwab client's
    get_quotes output so the caller does not care which provider answered.
    Never raises: this runs on the live price poll, and a provider outage must
    show as no price rather than as a broken endpoint.
    """
    wanted = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    if client is None or not wanted:
        return {}
    try:
        from alpaca.data.requests import StockLatestTradeRequest

        response = client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=wanted)
        )
    except ImportError:
        # Tests inject a fake client; alpaca-py is only needed for the real one.
        try:
            response = client.get_stock_latest_trade(wanted)
        except Exception:
            return {}
    except Exception:
        return {}

    rows = {}
    for symbol in wanted:
        trade = (response or {}).get(symbol)
        price = getattr(trade, "price", None)
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        # Zero is not a price. Passing it through paints a flat line at zero
        # on the chart instead of showing that there is no data.
        if value <= 0:
            continue
        rows[symbol] = {"last_price": value}
    return rows
