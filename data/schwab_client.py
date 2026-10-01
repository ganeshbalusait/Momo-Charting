from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd
from schwab import auth as schwab_auth

from config import ARTIFACTS_DIR, EASTERN_TZ, settings
from data.schwab_rate_limit import GATE, SchwabUnavailable, looks_like_edge_block


def _retry_after_seconds(response) -> float:
    """Schwab's own back-off instruction, when it sends one.

    Their documentation says to wait 60 seconds after a 429; the header is
    honoured when present so a longer instruction is never shortened.
    """
    try:
        return max(float(response.headers.get("retry-after") or 0.0), 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def schwab_profile_settings(profile: str):
    """Settings for a named Schwab profile.

    "market_data" (default) is the primary app from settings; "trading" is the
    optional Accounts & Trading app with its own key pair and token file, used
    for the live tick stream.
    """
    if str(profile or "").strip().lower() == "trading":
        from dataclasses import replace
        return replace(
            settings.schwab,
            client_id=os.getenv("SCHWAB_TRADING_CLIENT_ID", ""),
            client_secret=os.getenv("SCHWAB_TRADING_CLIENT_SECRET", ""),
            token_path=os.getenv(
                "SCHWAB_TRADING_TOKEN_PATH",
                str(ARTIFACTS_DIR / "schwab_trading_token.json"),
            ),
        )
    return settings.schwab


SCHWAB_AUTH_BASE = "https://api.schwabapi.com/v1/oauth"
SCHWAB_MARKET_DATA_BASE = "https://api.schwabapi.com/marketdata/v1"
SCHWAB_REFRESH_TOKEN_LIFETIME_DAYS = 7


TIMEFRAME_PARAMS = {
    "1Min": {"frequencyType": "minute", "frequency": 1},
    "5Min": {"frequencyType": "minute", "frequency": 5},
    "30Min": {"frequencyType": "minute", "frequency": 30},
    "1Day": {"periodType": "year", "frequencyType": "daily", "frequency": 1},
}


class SchwabClient:
    """Read-only Schwab Trader API market data adapter.

    This intentionally matches the subset of AlpacaClient used by the scanner
    and backtester, while Alpaca remains the broker/execution client.
    """

    _auth_lock = threading.RLock()
    #: Pending OAuth handshakes, keyed by the TOKEN FILE the flow will write.
    #:
    #: This was a single shared slot until 2026-09-04, which meant one
    #: authorization at a time for the whole process: starting the Accounts &
    #: Trading login silently destroyed a Market Data login the trader was
    #: half way through (and vice versa), and the paste-back then failed with
    #: schwab-py's raw "Expecting value: line 1 column 1 (char 0)" because the
    #: PKCE verifier no longer matched the code. Two users authenticating at
    #: once collided the same way.
    #:
    #: token_path is the right key because it is exactly what the flow is
    #: allowed to write: it already differs per profile (schwab_token.json vs
    #: schwab_trading_token.json) and per user (user_token_path), so two flows
    #: share a slot only when they would write the same file, which is the one
    #: case where clobbering is correct.
    _pending_auth_contexts: dict[str, object] = {}

    def __init__(self, config=None) -> None:
        self._tz = ZoneInfo(EASTERN_TZ)
        # An explicit config selects an alternate Schwab profile (the optional
        # Accounts & Trading app) with its own keys and token file; a string
        # names a profile ("trading"); the default remains the market-data
        # profile from settings.
        if isinstance(config, str):
            config = schwab_profile_settings(config)
        self.config = config or settings.schwab
        self._token_path = Path(self.config.token_path)
        self._client = None
        self._token: dict = {}
        self._token_expires_at: datetime | None = None
        self._refresh_token_expires_at: datetime | None = None
        self._token_saved_at: datetime | None = None
        self._token_load_error = ""
        self._token_file_mtime_ns: int | None = None
        self._load_cached_token()

    @property
    def configured(self) -> bool:
        return bool(self.config.client_id and self.config.client_secret and self._token.get("refresh_token"))

    def library_client(self):
        """schwab-py client for this profile (required by StreamClient)."""
        self._refuse_if_refresh_token_dead()
        if self._client is None:
            self._client = schwab_auth.client_from_token_file(
                str(self._token_path),
                api_key=self.config.client_id,
                app_secret=self.config.client_secret,
            )
        return self._client

    def connection_status(self) -> dict:
        # AWARE, because the token stamps below are aware. Both sides of the
        # subtraction have to agree or Python raises; and an aware stamp is
        # what makes the ISO string self-describing on the wire (see below).
        now = datetime.now(timezone.utc)
        access_remaining = (
            max(int((self._token_expires_at - now).total_seconds()), 0)
            if self._token_expires_at
            else None
        )
        refresh_remaining = (
            max(int((self._refresh_token_expires_at - now).total_seconds()), 0)
            if self._refresh_token_expires_at
            else None
        )
        client_id = str(self.config.client_id or "")
        masked_client_id = (
            f"{client_id[:4]}...{client_id[-6:]}"
            if len(client_id) > 10
            else "configured" if client_id else ""
        )
        return {
            "configured": self.configured,
            "credentialsConfigured": bool(self.config.client_id and self.config.client_secret),
            "hasClientId": bool(self.config.client_id),
            "hasClientSecret": bool(self.config.client_secret),
            "clientIdMasked": masked_client_id,
            "hasAccessToken": bool(self._token.get("access_token")),
            "hasRefreshToken": bool(self._token.get("refresh_token")),
            "accessTokenValid": bool(self._token.get("access_token") and (access_remaining is None or access_remaining > 0)),
            "refreshTokenValid": bool(self._token.get("refresh_token") and (refresh_remaining is None or refresh_remaining > 0)),
            "accessTokenRemainingSeconds": access_remaining,
            "refreshTokenRemainingSeconds": refresh_remaining,
            "tokenFileExists": self._token_path.exists(),
            "tokenPath": str(self._token_path),
            "redirectUri": self.config.redirect_uri,
            "includeExtendedHours": self.config.include_extended_hours,
            "tokenExpiresAt": self._token_expires_at.isoformat() if self._token_expires_at else None,
            "refreshTokenExpiresAt": self._refresh_token_expires_at.isoformat() if self._refresh_token_expires_at else None,
            "tokenSavedAt": self._token_saved_at.isoformat() if self._token_saved_at else None,
            "tokenManager": "schwab-py",
            "tokenLoadError": self._token_load_error,
        }

    def test_connection(self) -> dict:
        params = urlencode({"symbols": "SPY", "fields": "quote,reference"})
        payload = self._get_json(f"{SCHWAB_MARKET_DATA_BASE}/quotes?{params}")
        quote = payload.get("SPY") if isinstance(payload, dict) else None
        if not isinstance(quote, dict):
            raise RuntimeError("Schwab connected but did not return the SPY verification quote.")
        return {
            "connected": True,
            "symbol": "SPY",
            "verifiedAt": datetime.now().isoformat(),
        }

    @property
    def _auth_key(self) -> str:
        """Which pending handshake is mine - see _pending_auth_contexts."""
        return str(Path(self.config.token_path).resolve())

    def begin_authorization(self) -> str:
        if not self.config.client_id or not self.config.client_secret:
            raise RuntimeError("Schwab app key and secret are required before authentication.")
        with self._auth_lock:
            context = schwab_auth.get_auth_context(
                self.config.client_id,
                self.config.redirect_uri,
            )
            type(self)._pending_auth_contexts[self._auth_key] = context
            return context.authorization_url

    def exchange_authorization_response(self, received_url: str) -> dict:
        """Exchange a loopback OAuth callback through schwab-py.

        The library owns PKCE/state validation, token exchange, and later
        refreshes. This app never processes a raw authorization code itself.
        """
        with self._auth_lock:
            context = type(self)._pending_auth_contexts.get(self._auth_key)
            if context is None:
                raise RuntimeError("No Schwab authorization is waiting. Start a fresh authentication first.")
            client = schwab_auth.client_from_received_url(
                self.config.client_id,
                self.config.client_secret,
                context,
                received_url,
                self._write_library_token,
                enforce_enums=False,
            )
            # Consumed: an authorization code is single-use, so leaving the
            # context behind would only let a stale retry fail confusingly.
            type(self)._pending_auth_contexts.pop(self._auth_key, None)

        self._client = client
        self._load_cached_token()
        return {
            "connected": True,
            "hasAccessToken": bool(self._token.get("access_token")),
            "hasRefreshToken": bool(self._token.get("refresh_token")),
        }

    def get_stock_bars(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        if timeframe not in TIMEFRAME_PARAMS:
            return {}
        if not self.configured:
            return {}

        frames: dict[str, pd.DataFrame] = {}
        for symbol in [str(item).strip().upper() for item in symbols if str(item).strip()]:
            try:
                frame = self._get_price_history(symbol, timeframe, start, end)
            except Exception:
                frame = pd.DataFrame()
            if not frame.empty:
                frames[symbol] = frame
        return frames

    def get_quotes(self, symbols: Iterable[str], chunk_size: int = 100) -> dict[str, dict]:
        if not self.configured:
            return {}

        symbol_list = [str(item).strip().upper() for item in symbols if str(item).strip()]
        quotes: dict[str, dict] = {}
        for start in range(0, len(symbol_list), max(int(chunk_size), 1)):
            batch = symbol_list[start:start + max(int(chunk_size), 1)]
            if not batch:
                continue
            params = urlencode({"symbols": ",".join(batch), "fields": "quote,reference,regular"})
            try:
                payload = self._get_json(f"{SCHWAB_MARKET_DATA_BASE}/quotes?{params}")
            except SchwabUnavailable:
                # Schwab is refusing us outright - an edge block, a 429 cooldown,
                # or our own ceiling. Later batches cannot do better, and sending
                # them anyway is precisely what turns a rate limit into an IP
                # ban, so stop rather than walking the rest of the list.
                #
                # Still returns {} rather than raising: six call sites above rely
                # on that contract, and the outcome is already recorded at the
                # gate, so returning quietly loses no information.
                break
            except Exception:
                # Anything else is a per-batch problem (one bad symbol, a blip);
                # the remaining batches are still worth trying. The gate has
                # recorded it either way.
                continue
            if not isinstance(payload, dict):
                continue
            for raw_symbol, raw_quote in payload.items():
                symbol = str(raw_symbol).strip().upper()
                if not symbol or not isinstance(raw_quote, dict):
                    continue
                quote = raw_quote.get("quote") if isinstance(raw_quote.get("quote"), dict) else {}
                regular = raw_quote.get("regular") if isinstance(raw_quote.get("regular"), dict) else {}
                reference = raw_quote.get("reference") if isinstance(raw_quote.get("reference"), dict) else {}
                last_price = self._first_float(
                    quote,
                    regular,
                    "lastPrice",
                    "mark",
                    "regularMarketLastPrice",
                    "closePrice",
                    "bidPrice",
                    "askPrice",
                )
                change_pct = self._first_float(
                    quote,
                    regular,
                    "netPercentChange",
                    "regularMarketPercentChange",
                    "markPercentChange",
                )
                change = self._first_float(
                    quote,
                    regular,
                    "netChange",
                    "regularMarketNetChange",
                    "markChange",
                )
                volume = self._first_float(quote, regular, "totalVolume", "regularMarketVolume")
                quotes[symbol] = {
                    "symbol": symbol,
                    "last_price": last_price,
                    "change": change,
                    "change_pct": change_pct,
                    "volume": volume,
                    # Previous close and description are needed by consumers
                    # that recompute day-change from a live tick (the ticker
                    # strip froze its change/% between polls without this).
                    "close_price": self._first_float(
                        quote,
                        regular,
                        "closePrice",
                        "regularMarketPreviousClose",
                        "previousClose",
                    ),
                    "description": str(reference.get("description") or "").strip(),
                    "asset_type": str(reference.get("assetType") or raw_quote.get("assetMainType") or "").upper(),
                }
        return quotes

    def get_stock_bars_range(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        chunk_days: int = 30,
    ) -> dict[str, pd.DataFrame]:
        symbol_list = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
        combined: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in symbol_list}
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            chunk_frames = self.get_stock_bars(symbol_list, timeframe, cursor, chunk_end)
            for symbol in symbol_list:
                frame = chunk_frames.get(symbol)
                if frame is not None and not frame.empty:
                    combined[symbol].append(frame)
            cursor = chunk_end

        merged: dict[str, pd.DataFrame] = {}
        for symbol, parts in combined.items():
            if not parts:
                continue
            merged[symbol] = (
                pd.concat(parts, ignore_index=True)
                .drop_duplicates(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        return merged

    def get_intraday_bars(
        self,
        symbols: Iterable[str],
        timeframe: str = "1Min",
        days_back: int = 1,
    ) -> dict[str, pd.DataFrame]:
        now = datetime.now(tz=self._tz)
        start = (now - timedelta(days=max(days_back, self._default_intraday_lookback_days(now)))).replace(
            hour=4,
            minute=0,
            second=0,
            microsecond=0,
        )
        return self.get_stock_bars(symbols=symbols, timeframe=timeframe, start=start, end=now)

    def get_daily_bars(
        self,
        symbols: Iterable[str],
        lookback_days: int = 60,
    ) -> dict[str, pd.DataFrame]:
        now = datetime.now(tz=self._tz)
        start = now - timedelta(days=lookback_days * 2)
        return self.get_stock_bars(symbols=symbols, timeframe="1Day", start=start, end=now)

    def get_spy_context(self, timeframe: str = "1Min") -> pd.DataFrame:
        return self.get_intraday_bars(["SPY"], timeframe=timeframe).get("SPY", pd.DataFrame())

    def get_chart_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        days_back: int = 2,
    ) -> pd.DataFrame:
        return self.get_intraday_bars([symbol], timeframe=timeframe, days_back=days_back).get(symbol.upper(), pd.DataFrame())

    def get_option_chain(
        self,
        symbol: str,
        contract_type: str = "CALL",
        strike_count: int = 20,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> dict:
        if not self.configured:
            return {}
        params = {
            "symbol": str(symbol).strip().upper(),
            "contractType": contract_type.upper(),
            "strategy": "SINGLE",
            "strikeCount": max(int(strike_count), 1),
            "includeQuotes": "TRUE",
            "range": "ALL",
        }
        if from_date:
            params["fromDate"] = from_date.date().isoformat()
        if to_date:
            params["toDate"] = to_date.date().isoformat()
        return self._get_json(f"{SCHWAB_MARKET_DATA_BASE}/chains?{urlencode(params)}")

    def ensure_streaming(self, symbols: Iterable[str]) -> None:
        return None

    def _get_price_history(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime | None,
    ) -> pd.DataFrame:
        params = {
            "symbol": symbol,
            "startDate": self._epoch_ms(start),
            "endDate": self._epoch_ms(end or datetime.now(tz=self._tz)),
            "needExtendedHoursData": str(self.config.include_extended_hours).lower(),
            "needPreviousClose": "true",
            **TIMEFRAME_PARAMS[timeframe],
        }
        payload = self._get_json(f"{SCHWAB_MARKET_DATA_BASE}/pricehistory?{urlencode(params)}")
        candles = payload.get("candles") if isinstance(payload, dict) else None
        if not candles:
            return pd.DataFrame()
        return self._normalize_candles(candles)

    def _refuse_if_refresh_token_dead(self) -> None:
        """Raise BEFORE any network call once the refresh token has expired.

        schwab-py/authlib refresh on every request; with a dead refresh token
        each call becomes a rejected POST to Schwab's token endpoint. On
        2026-09-03 that ran all night from the MomX worker (357 symbols x 2
        timeframes, 12 threads, every build - measured 39 connections per
        20 s) and by morning Akamai answered every request from this address
        with Access Denied, including the OAuth exchange needed to recover.
        A known-dead token must cost nothing on the wire.

        The token FILE is re-read when it changes, so a client held in memory
        (the worker caches one per process) heals the moment the trader
        re-authenticates instead of needing a restart.
        """
        try:
            mtime_ns = self._token_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        if mtime_ns != self._token_file_mtime_ns:
            self._load_cached_token()
            self._client = None
        expires_at = self._refresh_token_expires_at
        if expires_at is not None and datetime.now(timezone.utc) >= expires_at:
            raise RuntimeError(
                "Schwab refresh token expired at %s; no request was sent. "
                "Re-authenticate in Settings." % expires_at.isoformat(timespec="seconds")
            )

    def _get_json(self, url: str) -> dict:
        """The single choke point for every Schwab market-data REST call.

        Quotes, chains and price history all pass through here, which is why
        the rate gate and the outcome recording live at this one spot rather
        than being repeated (and forgotten) at each call site.

        The recording is deliberately done HERE and not in the callers: every
        caller above this swallows failures to keep its own contract - notably
        get_quotes' `except Exception: continue` - so a signal raised upward
        gets eaten. On 2026-09-04 that is exactly what happened: Schwab
        refused every request for 70 minutes and not one line reached any log.
        Recorded at the gate, the outcome survives whatever the caller does
        with the exception.
        """
        self._refuse_if_refresh_token_dead()
        client = self._library_client()
        # Raises SchwabUnavailable rather than sending, when we are inside a
        # cooldown or over the rate ceiling. Nothing goes on the wire.
        GATE.acquire()
        try:
            response = client.session.get(
                url,
                headers={"Accept": "application/json"},
                timeout=self.config.timeout_seconds,
            )
        except Exception as exc:
            GATE.note_failure("network", "%s: %s" % (type(exc).__name__, exc))
            raise
        status = int(getattr(response, "status_code", 0) or 0)
        # Only look at the body for the statuses that can carry a refusal page;
        # reading .text on a good payload would cost a copy of every chain.
        if status in (401, 403, 429):
            body = str(getattr(response, "text", "") or "")
            if looks_like_edge_block(status, body):
                GATE.note_edge_block()
                raise SchwabUnavailable(
                    "Schwab's edge refused this address (HTTP %d Access Denied). "
                    "This is not a login problem - an unauthenticated request gets "
                    "the same page, and the OAuth endpoint is behind the same block."
                    % status,
                    kind="blocked",
                )
            if status == 429:
                GATE.note_rate_limited(_retry_after_seconds(response))
                raise SchwabUnavailable(
                    "Schwab returned 429 Too Many Requests; backing off before the "
                    "next call.",
                    kind="rate_limit",
                    retry_after=_retry_after_seconds(response),
                )
            GATE.note_failure("http_%d" % status, body[:300])
        response.raise_for_status()
        GATE.note_success()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _library_client(self):
        if self._client is not None:
            return self._client
        if not self.config.client_id or not self.config.client_secret:
            raise RuntimeError("Schwab app key and secret are required.")
        if not self._token_path.exists():
            raise RuntimeError("No schwab-py token is available. Re-authenticate in Settings.")
        try:
            self._client = schwab_auth.client_from_token_file(
                str(self._token_path),
                self.config.client_id,
                self.config.client_secret,
                enforce_enums=False,
            )
        except Exception as exc:
            raise RuntimeError("The local Schwab token is invalid or expired. Re-authenticate in Settings.") from exc
        return self._client

    def _first_float(self, *sources_and_keys) -> float | None:
        sources = [item for item in sources_and_keys if isinstance(item, dict)]
        keys = [item for item in sources_and_keys if isinstance(item, str)]
        for source in sources:
            for key in keys:
                value = source.get(key)
                if value is None or value == "":
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    def _write_library_token(self, token: dict, *args, **kwargs) -> None:
        """Persist the schwab-py token envelope atomically; never log it."""
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._token_path.with_suffix(f"{self._token_path.suffix}.tmp")
        temporary_path.write_text(json.dumps(token), encoding="utf-8")
        temporary_path.replace(self._token_path)

    def _load_cached_token(self) -> None:
        self._token = {}
        self._token_load_error = ""
        self._token_expires_at = None
        self._refresh_token_expires_at = None
        self._token_saved_at = None
        try:
            self._token_file_mtime_ns = self._token_path.stat().st_mtime_ns
        except OSError:
            self._token_file_mtime_ns = None
        if not self._token_path.exists():
            return
        try:
            payload = json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._token_load_error = "The local token file could not be read."
            return
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, dict) or "creation_timestamp" not in payload:
            self._token_load_error = "A legacy token file was found. Re-authenticate to create a schwab-py token."
            return
        self._token = token
        try:
            created_at = float(payload.get("creation_timestamp"))
            # tz=utc, NOT naive local. These datetimes are serialised straight
            # to the settings page as .isoformat(); naive they carried no
            # offset, so the browser read a machine-local (CT) wall time as
            # UTC and drew every Schwab deadline FIVE HOURS EARLY. The trader
            # was told to re-authenticate by 2:51 PM when the refresh token
            # actually died at 7:51 PM (2026-09-03).
            self._token_saved_at = datetime.fromtimestamp(created_at, tz=timezone.utc)
            self._refresh_token_expires_at = self._token_saved_at + timedelta(days=SCHWAB_REFRESH_TOKEN_LIFETIME_DAYS)
        except (TypeError, ValueError, OSError):
            self._token_saved_at = None
            self._refresh_token_expires_at = None
        expires_at = token.get("expires_at")
        if expires_at not in (None, ""):
            try:
                self._token_expires_at = datetime.fromtimestamp(float(expires_at), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                self._token_expires_at = None

    def _normalize_candles(self, candles: list[dict]) -> pd.DataFrame:
        frame = pd.DataFrame(candles)
        if frame.empty:
            return frame
        frame = frame.rename(columns={"datetime": "timestamp"}).copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True).dt.tz_convert(self._tz)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
        frame["trade_count"] = 0.0
        frame["session_vwap"] = 0.0
        frame = frame[
            ["timestamp", "open", "high", "low", "close", "volume", "trade_count", "session_vwap"]
        ].sort_values("timestamp")
        # SCHWAB SENDS EVERY CANDLE TWICE. Measured 2026-09-04 on the raw
        # response, before any parsing of ours: AAPL 5Min over four hours came
        # back with 310 candles carrying 155 distinct datetimes, each bar
        # repeated with identical OHLC and volume. Nothing here ever removed
        # them, so the duplicates reached every consumer of a Schwab frame.
        #
        # Harmless in the one place it would have been most damaging - the MomX
        # volume map keys a dict by epoch (momx/feed.py:770), so a repeat
        # overwrites rather than accumulates and RVOL is unaffected - but a
        # chart series fed two bars with the same timestamp is exactly the
        # input that makes Lightweight Charts throw, and any consumer that
        # sums or counts rows was silently working on double.
        #
        # keep="first" is safe precisely because the pairs are identical; this
        # cannot change a value, only remove a repeat.
        return frame.drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)

    def _epoch_ms(self, value: datetime) -> int:
        timestamp = value if value.tzinfo is not None else value.replace(tzinfo=self._tz)
        return int(timestamp.timestamp() * 1000)

    def _default_intraday_lookback_days(self, now: datetime) -> int:
        if now.weekday() == 5:
            return 3
        if now.weekday() == 6:
            return 4
        if now.weekday() == 0 and now.hour < 9:
            return 3
        return 1
