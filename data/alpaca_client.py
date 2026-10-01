from __future__ import annotations

import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

import pandas as pd
from alpaca.common.enums import Sort
from alpaca.data.enums import DataFeed
from alpaca.data.live.stock import StockDataStream
from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    AssetClass,
    AssetStatus,
    ContractType,
    OrderClass,
    OrderSide,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest,
    GetAssetsRequest,
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from alpaca.trading.stream import TradingStream
from zoneinfo import ZoneInfo

from config import EASTERN_TZ, settings

LOGGER = logging.getLogger(__name__)


TIMEFRAME_MAP = {
    "1Min": TimeFrame.Minute,
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "30Min": TimeFrame(30, TimeFrameUnit.Minute),
    "1Day": TimeFrame.Day,
}
OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")


@dataclass(slots=True)
class MarketClock:
    is_open: bool
    timestamp: datetime | None
    next_open: datetime | None
    next_close: datetime | None


@dataclass(slots=True)
class StreamStatus:
    enabled: bool
    marketDataConnected: bool
    tradeUpdatesConnected: bool
    subscribedSymbols: int
    lastBarAt: datetime | None
    lastQuoteAt: datetime | None
    lastTradeUpdateAt: datetime | None
    startedAt: datetime | None
    lastError: str


class AlpacaClient:
    def __init__(self, mode: str | None = None, profile_id: str | None = None) -> None:
        self._tz = ZoneInfo(EASTERN_TZ)
        self.mode = (mode or settings.execution_mode).lower()
        self.profile_id = profile_id or settings.default_account_profile
        self.credentials = settings.credentials_for_profile(self.profile_id, self.mode)
        self._data_client = StockHistoricalDataClient(
            api_key=self.credentials.key,
            secret_key=self.credentials.secret,
        )
        self._trading_client = TradingClient(
            api_key=self.credentials.key,
            secret_key=self.credentials.secret,
            paper=self.credentials.paper,
        )
        # Market-clock resilience: if this client's own key is unauthorized the
        # clock lookup falls back to any other configured key and caches it here.
        self._clock_client: TradingClient | None = None
        self._clock_source: str = self.profile_id
        self._clock_fallback_warned = False
        self._clock_all_failed_warned = False
        # Credential health: reflects the last authenticated call against this
        # account's OWN key (None = not yet observed).  Surfaced to the dashboard
        # so a dead/unauthorized key shows up explicitly instead of as a silent $0.
        self._auth_ok: bool | None = None
        self._auth_unauthorized = False
        self._auth_error = ""
        self._auth_checked_at: datetime | None = None
        self._stream_lock = threading.Lock()
        self._stream_started_at: datetime | None = None
        self._stream_last_bar_at: datetime | None = None
        self._stream_last_quote_at: datetime | None = None
        self._stream_last_trade_update_at: datetime | None = None
        self._stream_error = ""
        self._market_stream: StockDataStream | None = None
        self._trade_stream: TradingStream | None = None
        self._market_stream_thread: threading.Thread | None = None
        self._trade_stream_thread: threading.Thread | None = None
        self._market_stream_started = False
        self._trade_stream_started = False
        self._stream_subscribed_symbols: set[str] = set()
        self._stream_bars: dict[str, pd.DataFrame] = defaultdict(pd.DataFrame)
        self._stream_quotes: dict[str, dict] = {}
        self._stream_trade_updates: list[dict] = []

    @property
    def configured(self) -> bool:
        return bool(self.credentials.key and self.credentials.secret)

    @property
    def is_paper(self) -> bool:
        return self.credentials.paper

    def ensure_streaming(self, symbols: Iterable[str]) -> None:
        if not settings.alpaca_streaming_enabled or not self.configured:
            return

        symbol_list = [str(symbol).upper() for symbol in symbols if str(symbol).strip()]
        if not symbol_list:
            return

        with self._stream_lock:
            new_symbols = [symbol for symbol in symbol_list if symbol not in self._stream_subscribed_symbols]
            if self._market_stream is None:
                feed = DataFeed.SIP if settings.alpaca_data_feed.lower() == "sip" else DataFeed.IEX
                self._market_stream = StockDataStream(
                    api_key=self.credentials.key,
                    secret_key=self.credentials.secret,
                    feed=feed,
                )
                self._market_stream.subscribe_bars(self._on_live_bar, *symbol_list)
                self._market_stream.subscribe_updated_bars(self._on_live_bar, *symbol_list)
                self._market_stream.subscribe_quotes(self._on_live_quote, *symbol_list)
                self._stream_subscribed_symbols.update(symbol_list)
                self._start_market_stream_locked()
            elif new_symbols:
                self._market_stream.subscribe_bars(self._on_live_bar, *new_symbols)
                self._market_stream.subscribe_updated_bars(self._on_live_bar, *new_symbols)
                self._market_stream.subscribe_quotes(self._on_live_quote, *new_symbols)
                self._stream_subscribed_symbols.update(new_symbols)

            if settings.alpaca_trade_updates_enabled and self._trade_stream is None:
                self._trade_stream = TradingStream(
                    api_key=self.credentials.key,
                    secret_key=self.credentials.secret,
                    paper=self.credentials.paper,
                )
                self._trade_stream.subscribe_trade_updates(self._on_trade_update)
                self._start_trade_stream_locked()

    def get_stream_status(self) -> StreamStatus:
        with self._stream_lock:
            return StreamStatus(
                enabled=settings.alpaca_streaming_enabled,
                marketDataConnected=bool(self._market_stream_started),
                tradeUpdatesConnected=bool(self._trade_stream_started),
                subscribedSymbols=len(self._stream_subscribed_symbols),
                lastBarAt=self._stream_last_bar_at,
                lastQuoteAt=self._stream_last_quote_at,
                lastTradeUpdateAt=self._stream_last_trade_update_at,
                startedAt=self._stream_started_at,
                lastError=self._stream_error,
            )

    def get_live_bars(self, symbol: str) -> pd.DataFrame:
        with self._stream_lock:
            frame = self._stream_bars.get(str(symbol).upper())
            if frame is None or frame.empty:
                return pd.DataFrame()
            return frame.copy()

    @staticmethod
    def _read_clock(client: TradingClient) -> MarketClock | None:
        """Return the market clock from ``client``, or ``None`` if the call fails."""
        try:
            clock = client.get_clock()
        except Exception:  # noqa: BLE001 - broker/network/auth failure
            return None
        return MarketClock(
            is_open=clock.is_open,
            timestamp=clock.timestamp,
            next_open=clock.next_open,
            next_close=clock.next_close,
        )

    def get_clock(self) -> MarketClock:
        """Fetch the (account-agnostic) US market clock, resilient to a dead key.

        The regular-session banner and ``canAutoTrade`` gating derive from this,
        so a single unauthorized key must not silently report the market as
        closed.  Order: this client's own key, then a previously-discovered
        fallback, then any other configured Alpaca key.  Only when *every* key
        fails do we report closed - and we log it loudly instead of hiding it.
        """
        errors: list[str] = []

        # 1) This client's own (active-profile) credentials — the normal path.
        primary = self._read_clock(self._trading_client)
        if primary is not None:
            if self._clock_client is not None:
                LOGGER.info(
                    "Alpaca clock for profile '%s' recovered; dropping fallback key.",
                    self.profile_id,
                )
                self._clock_client = None
                self._clock_source = self.profile_id
                self._clock_fallback_warned = False
                self._clock_all_failed_warned = False
            return primary
        errors.append(f"{self.profile_id}: primary key unavailable")

        # 2) A previously-discovered working fallback key, if any.
        if self._clock_client is not None:
            cached = self._read_clock(self._clock_client)
            if cached is not None:
                return cached
            self._clock_client = None  # cached fallback went stale; rediscover

        # 3) Any other configured Alpaca key (allow-list independent).
        for cred in settings.clock_credential_candidates(self.mode):
            if cred.key == self.credentials.key and cred.secret == self.credentials.secret:
                continue  # already tried as the primary above
            try:
                client = TradingClient(api_key=cred.key, secret_key=cred.secret, paper=cred.paper)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{cred.profile_id}: {type(exc).__name__}")
                continue
            clock = self._read_clock(client)
            if clock is None:
                errors.append(f"{cred.profile_id}: clock unavailable")
                continue
            self._clock_client = client
            self._clock_source = cred.profile_id
            if not self._clock_fallback_warned:
                LOGGER.warning(
                    "Alpaca clock for active profile '%s' is unauthorized/unreachable; "
                    "serving market clock from fallback key '%s'. Refresh the '%s' key.",
                    self.profile_id,
                    cred.profile_id,
                    self.profile_id,
                )
                self._clock_fallback_warned = True
            return clock

        if not self._clock_all_failed_warned:
            LOGGER.error(
                "Alpaca market clock unavailable from every configured key (%s); "
                "reporting market closed. Refresh Alpaca API credentials.",
                "; ".join(errors) or "none configured",
            )
            self._clock_all_failed_warned = True
        return MarketClock(
            is_open=False,
            timestamp=None,
            next_open=None,
            next_close=None,
        )

    @staticmethod
    def _is_unauthorized(exc: Exception) -> bool:
        """True if an Alpaca error looks like an auth failure (dead/revoked key)."""
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status in (401, 403):
            return True
        text = str(exc).lower()
        return "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text

    def _record_auth(self, ok: bool, exc: Exception | None) -> None:
        self._auth_ok = ok
        self._auth_checked_at = datetime.now(tz=self._tz)
        if ok:
            self._auth_unauthorized = False
            self._auth_error = ""
        else:
            self._auth_unauthorized = bool(exc is not None and self._is_unauthorized(exc))
            self._auth_error = f"{type(exc).__name__}" if exc is not None else "unknown error"

    def credential_health(self, probe: bool = False) -> dict:
        """Report this account key's authorization health for the dashboard.

        Reads the cached result of the last authenticated call (``get_account``
        runs every dashboard refresh).  ``authorized`` is ``None`` until the key
        has actually been exercised; pass ``probe=True`` to force one check.
        """
        if probe and self._auth_ok is None:
            try:
                self.get_account()
            except Exception:  # noqa: BLE001 - state captured in _record_auth
                pass
        return {
            "profileId": self.profile_id,
            "label": self.credentials.label,
            "paper": self.credentials.paper,
            "configured": self.configured,
            "authorized": self._auth_ok,
            "unauthorized": self._auth_unauthorized,
            "error": self._auth_error,
            "checkedAt": self._auth_checked_at.isoformat() if self._auth_checked_at else None,
        }

    def get_account(self):
        try:
            account = self._trading_client.get_account()
        except Exception as exc:  # noqa: BLE001 - record auth health, then re-raise
            self._record_auth(False, exc)
            raise
        self._record_auth(True, None)
        return account

    def get_positions(self):
        return self._trading_client.get_all_positions()

    def get_open_orders(self, symbols: list[str] | None = None):
        request = GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            nested=True,
            symbols=symbols,
        )
        return self._trading_client.get_orders(filter=request)

    def get_orders(
        self,
        status: QueryOrderStatus = QueryOrderStatus.OPEN,
        symbols: list[str] | None = None,
        after: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 500,
    ):
        request = GetOrdersRequest(
            status=status,
            nested=True,
            symbols=symbols,
            after=after,
            until=until,
            limit=limit,
            direction=Sort.DESC,
        )
        return self._trading_client.get_orders(filter=request)

    def get_order_by_client_id(self, client_order_id: str):
        return self._trading_client.get_order_by_client_id(client_order_id)

    def cancel_order_by_id(self, order_id: str):
        return self._trading_client.cancel_order_by_id(str(order_id))

    def normalize_option_symbol(self, symbol: str) -> str:
        return str(symbol or "").strip().upper().replace(" ", "")

    def is_option_symbol(self, symbol: str) -> bool:
        return bool(OPTION_SYMBOL_PATTERN.match(self.normalize_option_symbol(symbol)))

    def _is_option_position(self, position) -> bool:
        asset_class = getattr(position, "asset_class", None)
        if asset_class == AssetClass.US_OPTION:
            return True
        return str(asset_class or "").strip().lower() in {"assetclass.us_option", "us_option"}

    def get_option_positions(self):
        return [
            position
            for position in self._trading_client.get_all_positions()
            if self._is_option_position(position) or self.is_option_symbol(str(getattr(position, "symbol", "")))
        ]

    def get_option_orders(
        self,
        status: QueryOrderStatus = QueryOrderStatus.OPEN,
        symbols: list[str] | None = None,
        after: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = 500,
    ):
        normalized_symbols = [self.normalize_option_symbol(symbol) for symbol in symbols or [] if str(symbol).strip()]
        orders = self.get_orders(
            status=status,
            symbols=normalized_symbols or None,
            after=after,
            until=until,
            limit=limit,
        )
        if normalized_symbols:
            normalized_set = set(normalized_symbols)
            return [
                order
                for order in orders
                if self.normalize_option_symbol(str(getattr(order, "symbol", ""))) in normalized_set
            ]
        return [
            order
            for order in orders
            if self.is_option_symbol(str(getattr(order, "symbol", "")))
        ]

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date_gte: date | str | None = None,
        expiration_date_lte: date | str | None = None,
        contract_type: ContractType = ContractType.CALL,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 200,
    ):
        request = GetOptionContractsRequest(
            underlying_symbols=[str(underlying_symbol or "").strip().upper()],
            expiration_date_gte=expiration_date_gte,
            expiration_date_lte=expiration_date_lte,
            type=contract_type,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=limit,
        )
        return self._trading_client.get_option_contracts(request)

    def _option_qty(self, qty: float) -> int:
        return max(int(round(float(qty or 0))), 1)

    def _position_intent(self, value: str | PositionIntent) -> PositionIntent:
        if isinstance(value, PositionIntent):
            return value
        normalized = str(value or "").strip().lower()
        mapping = {
            "buy_to_open": PositionIntent.BUY_TO_OPEN,
            "buy_to_close": PositionIntent.BUY_TO_CLOSE,
            "sell_to_open": PositionIntent.SELL_TO_OPEN,
            "sell_to_close": PositionIntent.SELL_TO_CLOSE,
        }
        return mapping.get(normalized, PositionIntent.BUY_TO_OPEN)

    def _order_side_for_intent(self, intent: PositionIntent) -> OrderSide:
        if intent in {PositionIntent.BUY_TO_OPEN, PositionIntent.BUY_TO_CLOSE}:
            return OrderSide.BUY
        return OrderSide.SELL

    def submit_option_limit_order(
        self,
        symbol: str,
        qty: float,
        limit_price: float,
        client_order_id: str,
        position_intent: str | PositionIntent = PositionIntent.BUY_TO_OPEN,
    ):
        intent = self._position_intent(position_intent)
        order_request = LimitOrderRequest(
            symbol=self.normalize_option_symbol(symbol),
            qty=self._option_qty(qty),
            side=self._order_side_for_intent(intent),
            time_in_force=TimeInForce.DAY,
            limit_price=round(float(limit_price), 2),
            client_order_id=client_order_id,
            position_intent=intent,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def submit_option_market_order(
        self,
        symbol: str,
        qty: float,
        client_order_id: str,
        position_intent: str | PositionIntent = PositionIntent.BUY_TO_OPEN,
    ):
        intent = self._position_intent(position_intent)
        order_request = MarketOrderRequest(
            symbol=self.normalize_option_symbol(symbol),
            qty=self._option_qty(qty),
            side=self._order_side_for_intent(intent),
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
            position_intent=intent,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def close_option_position(
        self,
        symbol: str,
        qty: float | None = None,
        percentage: float | None = None,
    ):
        close_request = ClosePositionRequest(
            qty=str(self._option_qty(qty)) if qty is not None else None,
            percentage=str(float(percentage)) if percentage is not None else None,
        )
        return self._trading_client.close_position(
            self.normalize_option_symbol(symbol),
            close_options=close_request,
        )

    def submit_bracket_order(
        self,
        symbol: str,
        qty: float,
        stop_price: float,
        target_price: float,
        client_order_id: str,
    ):
        quantity = round(qty, 4 if settings.trading.allow_fractional_shares else 0)
        order_request = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(target_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            client_order_id=client_order_id,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def submit_market_entry_order(
        self,
        symbol: str,
        qty: float,
        client_order_id: str,
    ):
        quantity = self._normalized_stock_order_quantity(qty)
        order_request = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def submit_extended_hours_limit_order(
        self,
        symbol: str,
        qty: float,
        limit_price: float,
        side: str,
        client_order_id: str,
    ):
        quantity = self._normalized_stock_order_quantity(
            qty,
            preserve_fractional=str(side or "").strip().lower() == "sell",
        )
        order_request = LimitOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            extended_hours=True,
            client_order_id=client_order_id,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def submit_market_exit_order(
        self,
        symbol: str,
        qty: float,
        client_order_id: str,
    ):
        # Liquidation must preserve legacy fractional positions even when new
        # fractional entries are disabled. Rounding a sub-share position to
        # zero causes Alpaca to reject every manager cycle with qty > 0.
        quantity = self._normalized_stock_order_quantity(qty, preserve_fractional=True)
        order_request = MarketOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        return self._trading_client.submit_order(order_data=order_request)

    def _normalized_stock_order_quantity(self, qty: float, preserve_fractional: bool = False) -> float:
        try:
            raw_quantity = abs(float(qty))
        except (TypeError, ValueError):
            raw_quantity = 0.0
        precision = 4 if preserve_fractional or settings.trading.allow_fractional_shares else 0
        quantity = round(raw_quantity, precision)
        if quantity <= 0:
            raise ValueError("Stock order quantity must be greater than zero.")
        return quantity

    def list_active_symbols(self, limit: int = 100) -> list[str]:
        request = GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
        assets = self._trading_client.get_all_assets(request)
        return [asset.symbol for asset in assets if asset.tradable][:limit]

    def get_stock_bars(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime | None = None,
    ) -> dict[str, pd.DataFrame]:
        symbol_list = list(symbols)
        request = StockBarsRequest(
            symbol_or_symbols=symbol_list,
            timeframe=TIMEFRAME_MAP[timeframe],
            start=start,
            end=end,
            adjustment="raw",
            feed=settings.alpaca_data_feed,
        )
        try:
            bars = self._data_client.get_stock_bars(request).df
        except Exception:
            return {}
        if bars.empty:
            return {}

        frames: dict[str, pd.DataFrame] = {}
        if isinstance(bars.index, pd.MultiIndex):
            for symbol in bars.index.get_level_values(0).unique():
                frame = bars.xs(symbol).reset_index()
                frames[symbol] = self._normalize_frame(frame)
        else:
            symbol = symbol_list[0]
            frames[symbol] = self._normalize_frame(bars.reset_index())
        return frames

    def get_stock_bars_range(
        self,
        symbols: Iterable[str],
        timeframe: str,
        start: datetime,
        end: datetime,
        chunk_days: int = 30,
    ) -> dict[str, pd.DataFrame]:
        symbol_list = list(symbols)
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
            frame = pd.concat(parts, ignore_index=True).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            merged[symbol] = frame
        return merged

    def get_intraday_bars(
        self,
        symbols: Iterable[str],
        timeframe: str = "1Min",
        days_back: int = 1,
    ) -> dict[str, pd.DataFrame]:
        now = datetime.now(tz=self._tz)
        effective_days_back = max(days_back, self._default_intraday_lookback_days(now))
        start = (now - timedelta(days=effective_days_back)).replace(hour=4, minute=0, second=0, microsecond=0)
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
        frames = self.get_intraday_bars(["SPY"], timeframe=timeframe)
        return frames.get("SPY", pd.DataFrame())

    def get_chart_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        days_back: int = 2,
    ) -> pd.DataFrame:
        self.ensure_streaming([symbol])
        frames = self.get_intraday_bars([symbol], timeframe=timeframe, days_back=days_back)
        historical = frames.get(symbol.upper(), pd.DataFrame())
        live = self.get_live_bars(symbol)
        if historical.empty:
            return live
        if live.empty:
            return historical
        combined = pd.concat([historical, live], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return combined

    def _start_market_stream_locked(self) -> None:
        if self._market_stream is None or self._market_stream_started:
            return
        self._market_stream_thread = threading.Thread(target=self._run_market_stream, daemon=True)
        self._market_stream_thread.start()
        self._market_stream_started = True
        self._stream_started_at = datetime.now(tz=self._tz)

    def _start_trade_stream_locked(self) -> None:
        if self._trade_stream is None or self._trade_stream_started:
            return
        self._trade_stream_thread = threading.Thread(target=self._run_trade_stream, daemon=True)
        self._trade_stream_thread.start()
        self._trade_stream_started = True
        if self._stream_started_at is None:
            self._stream_started_at = datetime.now(tz=self._tz)

    def _run_market_stream(self) -> None:
        try:
            if self._market_stream is not None:
                self._market_stream.run()
        except Exception as exc:
            with self._stream_lock:
                self._stream_error = f"market_stream: {exc}"
                self._market_stream_started = False

    def _run_trade_stream(self) -> None:
        try:
            if self._trade_stream is not None:
                self._trade_stream.run()
        except Exception as exc:
            with self._stream_lock:
                self._stream_error = f"trade_stream: {exc}"
                self._trade_stream_started = False

    async def _on_live_bar(self, bar) -> None:
        symbol = str(getattr(bar, "symbol", "")).upper()
        timestamp = pd.to_datetime(getattr(bar, "timestamp", None), utc=True)
        if not symbol or pd.isna(timestamp):
            return
        row = pd.DataFrame(
            [
                {
                    "timestamp": timestamp.tz_convert(self._tz),
                    "open": float(getattr(bar, "open", 0.0)),
                    "high": float(getattr(bar, "high", 0.0)),
                    "low": float(getattr(bar, "low", 0.0)),
                    "close": float(getattr(bar, "close", 0.0)),
                    "volume": float(getattr(bar, "volume", 0.0)),
                    "trade_count": float(getattr(bar, "trade_count", 0.0) or 0.0),
                    "session_vwap": float(getattr(bar, "vwap", 0.0) or 0.0),
                }
            ]
        )
        with self._stream_lock:
            existing = self._stream_bars.get(symbol)
            combined = pd.concat([existing, row], ignore_index=True) if existing is not None and not existing.empty else row
            combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").tail(600).reset_index(drop=True)
            self._stream_bars[symbol] = combined
            self._stream_last_bar_at = datetime.now(tz=self._tz)

    async def _on_live_quote(self, quote) -> None:
        symbol = str(getattr(quote, "symbol", "")).upper()
        if not symbol:
            return
        with self._stream_lock:
            self._stream_quotes[symbol] = {
                "bid_price": float(getattr(quote, "bid_price", 0.0) or 0.0),
                "ask_price": float(getattr(quote, "ask_price", 0.0) or 0.0),
                "bid_size": float(getattr(quote, "bid_size", 0.0) or 0.0),
                "ask_size": float(getattr(quote, "ask_size", 0.0) or 0.0),
                "timestamp": getattr(quote, "timestamp", None),
            }
            self._stream_last_quote_at = datetime.now(tz=self._tz)

    async def _on_trade_update(self, update) -> None:
        event = getattr(update, "event", None)
        order = getattr(update, "order", None)
        with self._stream_lock:
            self._stream_trade_updates.append(
                {
                    "event": str(event) if event is not None else None,
                    "symbol": str(getattr(order, "symbol", "")) if order is not None else None,
                    "client_order_id": str(getattr(order, "client_order_id", "")) if order is not None else None,
                    "status": str(getattr(order, "status", "")) if order is not None else None,
                    "at": datetime.now(tz=self._tz).isoformat(),
                }
            )
            self._stream_trade_updates = self._stream_trade_updates[-100:]
            self._stream_last_trade_update_at = datetime.now(tz=self._tz)

    def _normalize_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.rename(
            columns={
                "timestamp": "timestamp",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "trade_count": "trade_count",
                "vwap": "session_vwap",
            }
        ).copy()
        normalized["timestamp"] = pd.to_datetime(normalized["timestamp"], utc=True).dt.tz_convert(self._tz)
        normalized = normalized.sort_values("timestamp").reset_index(drop=True)
        return normalized

    def _default_intraday_lookback_days(self, now: datetime) -> int:
        weekday = now.weekday()
        if weekday == 5:
            return 3
        if weekday == 6:
            return 4
        if weekday == 0 and now.hour < 9:
            return 3
        return 1
