from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import threading
import tempfile
import time
from typing import Callable, Iterable

from schwab.streaming import StreamClient

from data.schwab_client import SchwabClient


def event_stream_cursor(last_event_id: object, latest_sequence: object) -> int:
    try:
        latest = max(int(latest_sequence or 0), 0)
    except (TypeError, ValueError):
        latest = 0
    if last_event_id in (None, ""):
        return latest
    try:
        return min(max(int(last_event_id), 0), latest)
    except (TypeError, ValueError):
        return latest


# Schwab accepts large symbol lists but rejects oversized single requests;
# 100 per call matches the quote-endpoint chunking used elsewhere.
_EQUITY_SUBSCRIBE_CHUNK = 100
#: Schwab's per-connection equity limit. Measured 2026-09-30 11:38 ET: 345
#: requested -> "You've reached the maximum number of symbols allowed.
#: (CHART_EQUITY=300, DISCARDED=45)", connected=False, and every live quote
#: froze (the fastlane served 35-minute-old prices while reporting serving).
#: Chunking bounds each REQUEST; this bounds the TOTAL.
_MAX_EQUITY_SYMBOLS = 300


class SchwabMarketStream:
    """One reconnecting Schwab stream shared by all browser clients."""

    def __init__(
        self,
        client_factory: Callable[[], SchwabClient] | None = None,
        max_option_symbols: int = 300,
        *,
        chart_history_path: str | Path | None = None,
        chart_history_max_bars: int = 12_000,
        chart_history_flush_seconds: float = 30.0,
    ) -> None:
        self._client_factory = client_factory or (lambda: SchwabClient("trading"))
        self._max_option_symbols = max(1, int(max_option_symbols))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._desired_equities: set[str] = set()
        self._pinned_equities: set[str] = set()
        self._equity_leases: dict[str, int] = {}
        self._option_equity = ""
        self._desired_options: dict[str, None] = {}
        self._option_underlyings: dict[str, str] = {}
        self._events: deque[dict] = deque(maxlen=6000)
        self._sequence = 0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stop = threading.Event()
        self._connected = False
        self._last_error = ""
        self._last_event_at: str | None = None
        self._last_event_by_type: dict[str, dict] = {}
        #: Newest quote per equity symbol, for snapshot readers (the MomX
        #: fastlane polls this instead of tailing the event deque). Written
        #: under _condition in _publish; includes Alpaca-failover quotes too,
        #: since those publish through the same pipe with their own source tag.
        self._latest_equity: dict[str, dict] = {}
        # Newest CHART_EQUITY bar per symbol - the fallback price when the
        # LEVELONE quote has gone quiet (see latest_equities).
        self._latest_chart: dict[str, dict] = {}
        self._chart_history_path = Path(chart_history_path) if chart_history_path else None
        self._chart_history_max_bars = max(100, int(chart_history_max_bars))
        self._chart_history_flush_seconds = max(0.0, float(chart_history_flush_seconds))
        self._chart_history_writer: threading.Thread | None = None
        self._chart_history_last_flush = 0.0
        self._chart_history: dict[str, OrderedDict[int, dict]] = {}
        self._load_chart_history()

    @staticmethod
    def _number(value):
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _millis(value) -> int | None:
        try:
            parsed = int(float(value))
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None

    def _publish(self, event_type: str, symbol: str, data: dict) -> None:
        received_at = datetime.now(timezone.utc).isoformat()
        with self._condition:
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "type": event_type,
                "symbol": str(symbol or "").strip().upper(),
                "data": data,
                "receivedAt": received_at,
            }
            if event_type == "option":
                event["underlying"] = self._option_underlyings.get(event["symbol"], "")
            self._events.append(event)
            self._last_event_at = received_at
            market_time_millis = None
            if event_type in {"equity", "option"}:
                market_time_millis = self._millis(data.get("tradeTime") or data.get("quoteTime"))
            elif event_type == "chart":
                chart_time = self._number(data.get("time"))
                market_time_millis = round(chart_time * 1000) if chart_time and chart_time > 0 else None
            self._last_event_by_type[event_type] = {
                "symbol": event["symbol"],
                "receivedAt": received_at,
                "marketTimeMillis": market_time_millis,
                "sequence": self._sequence,
            }
            if event_type == "equity" and event["symbol"]:
                # LEVELONE sends only the fields that CHANGED. Replacing the
                # held quote with a bid-only update wiped LAST_PRICE (measured
                # 2026-09-30: CNQ/CVX/DDOG/GGLL/ZETA held last=None), so merge
                # and remember when the last price itself was refreshed.
                fresh = {key: value for key, value in data.items() if value is not None}
                merged = {**self._latest_equity.get(event["symbol"], {}), **fresh, "receivedAt": received_at}
                if fresh.get("last") is not None:
                    merged["lastAt"] = received_at
                    merged["lastSequence"] = self._sequence
                self._latest_equity[event["symbol"]] = merged
            elif event_type == "chart" and event["symbol"]:
                close = self._number(data.get("close"))
                if close is not None and close > 0:
                    self._latest_chart[event["symbol"]] = {
                        "close": close, "time": data.get("time"), "receivedAt": received_at,
                        "sequence": self._sequence,
                    }
            self._condition.notify_all()

    def _handle_equity(self, message: dict) -> None:
        for item in message.get("content") or []:
            symbol = str(item.get("key") or item.get("SYMBOL") or "").strip().upper()
            if symbol:
                self._publish("equity", symbol, {
                    # Explicit source so consumers can pick a single writer by
                    # PROVIDER rather than by timing. Schwab is the
                    # consolidated tape; alpaca_stream tags "alpaca:<feed>"
                    # for its IEX slice, which sits cents away and must not
                    # co-write the active chart line.
                    "source": "schwab",
                    "bid": self._number(item.get("BID_PRICE")),
                    "ask": self._number(item.get("ASK_PRICE")),
                    "last": self._number(item.get("LAST_PRICE")),
                    "mark": self._number(item.get("MARK")),
                    "totalVolume": self._number(item.get("TOTAL_VOLUME")),
                    "quoteTime": self._millis(item.get("QUOTE_TIME_MILLIS")),
                    "tradeTime": self._millis(item.get("TRADE_TIME_MILLIS")),
                })

    def _handle_chart(self, message: dict) -> None:
        for item in message.get("content") or []:
            symbol = str(item.get("key") or item.get("SYMBOL") or "").strip().upper()
            chart_time = self._millis(item.get("CHART_TIME_MILLIS"))
            if symbol and chart_time:
                bar = {
                    "time": chart_time // 1000,
                    "open": self._number(item.get("OPEN_PRICE")),
                    "high": self._number(item.get("HIGH_PRICE")),
                    "low": self._number(item.get("LOW_PRICE")),
                    "close": self._number(item.get("CLOSE_PRICE")),
                    "volume": self._number(item.get("VOLUME")),
                }
                self._record_chart_bar(symbol, bar)
                self._publish("chart", symbol, bar)

    def _load_chart_history(self) -> None:
        path = self._chart_history_path
        if path is None:
            return
        try:
            with gzip.open(path, "rt", encoding="utf-8") as source:
                payload = json.load(source)
            raw_symbols = payload.get("symbols") if isinstance(payload, dict) else None
            if not isinstance(raw_symbols, dict):
                return
            for raw_symbol, raw_bars in raw_symbols.items():
                symbol = str(raw_symbol or "").strip().upper()
                if not symbol or not isinstance(raw_bars, list):
                    continue
                ordered: OrderedDict[int, dict] = OrderedDict()
                for raw_bar in raw_bars[-self._chart_history_max_bars:]:
                    if not isinstance(raw_bar, dict):
                        continue
                    try:
                        timestamp = int(raw_bar.get("time") or 0)
                    except (TypeError, ValueError):
                        continue
                    if timestamp <= 0 or self._number(raw_bar.get("close")) is None:
                        continue
                    ordered[timestamp] = {
                        "time": timestamp,
                        "open": self._number(raw_bar.get("open")),
                        "high": self._number(raw_bar.get("high")),
                        "low": self._number(raw_bar.get("low")),
                        "close": self._number(raw_bar.get("close")),
                        "volume": self._number(raw_bar.get("volume")),
                    }
                if ordered:
                    self._chart_history[symbol] = ordered
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # Broker REST history remains available when the optional stream
            # tape is absent, stale, or interrupted during a previous write.
            return

    def _record_chart_bar(self, symbol: str, bar: dict) -> None:
        timestamp = int(bar.get("time") or 0)
        close = self._number(bar.get("close"))
        if timestamp <= 0 or close is None:
            return
        with self._lock:
            history = self._chart_history.setdefault(symbol, OrderedDict())
            previous = history.get(timestamp) or {}
            high_values = [
                value
                for value in (self._number(previous.get("high")), self._number(bar.get("high")))
                if value is not None
            ]
            low_values = [
                value
                for value in (self._number(previous.get("low")), self._number(bar.get("low")))
                if value is not None
            ]
            history[timestamp] = {
                "time": timestamp,
                "open": self._number(previous.get("open"))
                if self._number(previous.get("open")) is not None
                else self._number(bar.get("open")),
                "high": max(high_values) if high_values else close,
                "low": min(low_values) if low_values else close,
                "close": close,
                # CHART_EQUITY updates replace the current minute's volume;
                # summing packets would double count the same candle.
                "volume": self._number(bar.get("volume")) or 0.0,
            }
            history.move_to_end(timestamp)
            while len(history) > self._chart_history_max_bars:
                history.popitem(last=False)
        self._flush_chart_history_if_due()

    def chart_history(self, symbol: str) -> list[dict]:
        target = str(symbol or "").strip().upper()
        with self._lock:
            history = self._chart_history.get(target) or {}
            return [dict(bar) for bar in history.values()]

    def chart_history_since(self, since: float, symbols=None) -> dict[str, list]:
        """Minutes with time >= ``since`` as compact [t, o, h, l, c, v] rows,
        per symbol (all held symbols when ``symbols`` is None). Walks each
        history from its NEWEST end and stops at ``since``, so a poller asking
        for the last few minutes costs a few rows, not 12,000 per symbol."""
        cutoff = float(since or 0)
        out: dict[str, list] = {}
        with self._lock:
            keys = (list(self._chart_history) if symbols is None
                    else [str(s or "").strip().upper() for s in symbols])
            for key in keys:
                history = self._chart_history.get(key)
                if not history:
                    continue
                rows = []
                for bar in reversed(history.values()):
                    t = bar.get("time")
                    if t is None or t < cutoff:
                        break
                    rows.append([t, bar.get("open"), bar.get("high"), bar.get("low"),
                                 bar.get("close"), bar.get("volume")])
                if rows:
                    rows.reverse()
                    out[key] = rows
        return out

    def _flush_chart_history_if_due(self, *, force: bool = False) -> None:
        path = self._chart_history_path
        if path is None:
            return
        now = time.monotonic()
        if not force and now - self._chart_history_last_flush < self._chart_history_flush_seconds:
            return
        writer = self._chart_history_writer
        if not force and writer is not None and writer.is_alive():
            return  # the previous save is still running; never stack them
        with self._lock:
            # Shallow copy: _record_chart_bar REPLACES each bar dict rather
            # than mutating it, so the writer can share them. Deep-copying
            # 12,000 bars x ~210 symbols here was itself seconds of GIL time.
            snapshot = {
                symbol: list(history.values())
                for symbol, history in self._chart_history.items()
                if history
            }
            self._chart_history_last_flush = now
        if force:
            # A background save started earlier holds an OLDER snapshot; if it
            # finished after this one it would overwrite the newer file (seen
            # as a 1-in-5 flake of the overnight-persistence test). Let it
            # finish first, then write the newest state.
            if writer is not None and writer.is_alive():
                writer.join(timeout=60)
            self._write_chart_history(path, snapshot)
            return
        # OFF the stream's event loop (2026-09-30). This ran inline inside
        # _handle_chart: py-spy caught the schwab-market-stream thread in
        # gzip/json.dump of the ~10 MB history on two dumps 8 s apart while
        # every quote froze, connected=True and no error logged - the loop
        # could not read the socket until the save finished, every 30 s.
        writer = threading.Thread(
            target=self._write_chart_history,
            args=(path, snapshot),
            name="schwab-chart-history-writer",
            daemon=True,
        )
        self._chart_history_writer = writer
        writer.start()

    def _write_chart_history(self, path, snapshot: dict) -> None:
        temporary_path: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{path.stem}-",
                suffix=".tmp",
                dir=path.parent,
            )
            os.close(descriptor)
            with gzip.open(temporary_path, "wt", encoding="utf-8", compresslevel=3) as target:
                json.dump({"version": 1, "symbols": snapshot}, target, separators=(",", ":"))
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
            temporary_path = None
        except (OSError, TypeError, ValueError):
            return
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def _handle_option(self, message: dict) -> None:
        for item in message.get("content") or []:
            symbol = str(item.get("key") or item.get("SYMBOL") or "").strip().upper()
            if symbol:
                self._publish("option", symbol, {
                    "bid": self._number(item.get("BID_PRICE")),
                    "ask": self._number(item.get("ASK_PRICE")),
                    "last": self._number(item.get("LAST_PRICE")),
                    "mark": self._number(item.get("MARK")),
                    "totalVolume": self._number(item.get("TOTAL_VOLUME")),
                    "openInterest": self._number(item.get("OPEN_INTEREST")),
                    "delta": self._number(item.get("DELTA")),
                    "gamma": self._number(item.get("GAMMA")),
                    "theta": self._number(item.get("THETA")),
                    "vega": self._number(item.get("VEGA")),
                    "underlyingPrice": self._number(item.get("UNDERLYING_PRICE")),
                    "quoteTime": self._millis(item.get("QUOTE_TIME_MILLIS")),
                    "tradeTime": self._millis(item.get("TRADE_TIME_MILLIS")),
                })

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._thread_main, name="schwab-market-stream", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)
        with self._condition:
            self._condition.notify_all()
        self._flush_chart_history_if_due(force=True)

    def watch(self, equity_symbol: str, option_symbols: Iterable[str] = (), replace_options: bool = False) -> None:
        equity = str(equity_symbol or "").strip().upper()
        changed = False
        with self._lock:
            if replace_options:
                previous_option_equity = self._option_equity
                self._option_equity = equity
                if equity and equity not in self._desired_equities:
                    self._desired_equities.add(equity)
                    changed = True
                if (
                    previous_option_equity
                    and previous_option_equity != equity
                    and previous_option_equity not in self._pinned_equities
                    and self._equity_leases.get(previous_option_equity, 0) <= 0
                    and previous_option_equity in self._desired_equities
                ):
                    self._desired_equities.remove(previous_option_equity)
                    changed = True
            elif equity:
                self._pinned_equities.add(equity)
                if equity not in self._desired_equities:
                    self._desired_equities.add(equity)
                    changed = True
            normalized_options = [
                str(raw_symbol or "").strip().upper()
                for raw_symbol in option_symbols
                if str(raw_symbol or "").strip()
            ][:self._max_option_symbols]
            if replace_options:
                next_options = dict.fromkeys(normalized_options)
                changed = changed or set(next_options) != set(self._desired_options)
                self._desired_options = next_options
                self._option_underlyings = {option: equity for option in normalized_options}
            else:
                for option_symbol in normalized_options:
                    if option_symbol in self._desired_options or len(self._desired_options) >= self._max_option_symbols:
                        continue
                    self._desired_options[option_symbol] = None
                    self._option_underlyings[option_symbol] = equity
                    changed = True
        self.start()
        if changed and self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)

    def acquire_equities(self, equity_symbols: Iterable[str]) -> tuple[str, ...]:
        symbols = tuple(dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in equity_symbols
            if str(symbol or "").strip()
        ))
        if not symbols:
            return ()
        changed = False
        with self._lock:
            for symbol in symbols:
                self._equity_leases[symbol] = self._equity_leases.get(symbol, 0) + 1
                if symbol not in self._desired_equities:
                    self._desired_equities.add(symbol)
                    changed = True
        self.start()
        if changed and self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)
        return symbols

    def release_equities(self, equity_symbols: Iterable[str]) -> None:
        changed = False
        with self._lock:
            for raw_symbol in equity_symbols:
                symbol = str(raw_symbol or "").strip().upper()
                leases = self._equity_leases.get(symbol, 0)
                if leases > 1:
                    self._equity_leases[symbol] = leases - 1
                    continue
                self._equity_leases.pop(symbol, None)
                if (
                    symbol
                    and symbol not in self._pinned_equities
                    and symbol != self._option_equity
                    and symbol in self._desired_equities
                ):
                    self._desired_equities.remove(symbol)
                    changed = True
        if changed and self._loop and self._wake:
            self._loop.call_soon_threadsafe(self._wake.set)

    def wait_for_events(
        self,
        after_sequence: int,
        symbols: str | Iterable[str],
        timeout: float = 15.0,
    ) -> tuple[int, list[dict]]:
        raw_symbols = [symbols] if isinstance(symbols, str) else symbols
        targets = {
            str(symbol or "").strip().upper()
            for symbol in (raw_symbols or [])
            if str(symbol or "").strip()
        }
        deadline = time.monotonic() + max(float(timeout), 0.0)
        cursor = max(int(after_sequence or 0), 0)

        def matches(event: dict) -> bool:
            return bool(
                event["type"] == "status"
                or not targets
                or event["symbol"] in targets
                or (event["type"] == "option" and event.get("underlying") in targets)
            )

        with self._condition:
            while True:
                remaining = max(0.0, deadline - time.monotonic())
                signaled = self._condition.wait_for(
                    lambda: self._sequence > cursor or self._stop.is_set(),
                    timeout=remaining,
                )
                latest = self._sequence
                events = []
                # Cursors normally trail by one or two packets. Scan backward
                # only through that new tail instead of filtering the complete
                # 6,000-event buffer for every subscriber wake-up.
                for event in reversed(self._events):
                    if int(event["sequence"]) <= cursor:
                        break
                    if matches(event):
                        events.append(event)
                events.reverse()
                cursor = latest
                if (
                    events
                    or self._stop.is_set()
                    or not signaled
                    or time.monotonic() >= deadline
                ):
                    return cursor, events

    def latest_equities(self, symbols: Iterable[str] | None = None) -> dict[str, dict]:
        """Newest quote per symbol (copies), or every cached symbol when None.

        A snapshot read for pollers: unlike wait_for_events it never blocks and
        never replays history - just "what is the freshest quote you hold".
        Unknown symbols are simply absent from the result.
        """
        with self._condition:
            keys = (set(self._latest_equity) | set(self._latest_chart)) if symbols is None else [
                str(raw or "").strip().upper() for raw in symbols
            ]
            out: dict[str, dict] = {}
            for key in keys:
                quote = self._quote_with_chart_fallback(key)
                if quote is not None:
                    out[key] = quote
            return out

    def _quote_with_chart_fallback(self, key: str) -> dict | None:
        """The held quote, with ``last`` taken from the newest CHART_EQUITY bar
        when that bar arrived after the quote's last price (or it has none).

        2026-09-30 12:25 ET: LEVELONE delivered only AAPL for 40 minutes while
        CHART_EQUITY bars kept arriving for every subscribed symbol, so the
        scanner's live layer held 40-minute-old prices. A minute-bar close is
        at most ~1 minute old; ``lastSource`` says which one was used.
        Caller holds ``self._condition``."""
        held = self._latest_equity.get(key)
        chart = self._latest_chart.get(key)
        if held is None and chart is None:
            return None
        quote = dict(held or {"source": "schwab"})
        # Order by stream sequence, not wall-clock text: two messages in the
        # same clock tick would otherwise tie.
        if chart and (quote.get("last") is None or chart["sequence"] > int(quote.get("lastSequence") or 0)):
            quote["last"] = chart["close"]
            quote["lastAt"] = chart["receivedAt"]
            quote["lastSource"] = "chart"
            quote.setdefault("receivedAt", chart["receivedAt"])
        return quote

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._connected,
                "equitySymbols": len(self._desired_equities),
                "optionSymbols": len(self._desired_options),
                "lastEventAt": self._last_event_at,
                "lastEvents": {
                    event_type: dict(event)
                    for event_type, event in self._last_event_by_type.items()
                },
                "lastError": self._last_error,
            }

    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _apply_subscriptions(self, stream: StreamClient, equities: set[str], options: set[str]) -> None:
        with self._lock:
            desired_equities = set(self._desired_equities)
            desired_options = set(self._desired_options)
        new_equities = sorted(desired_equities - equities)
        removed_equities = sorted(equities - desired_equities)
        room = _MAX_EQUITY_SYMBOLS - (len(equities) - len(removed_equities))
        if len(new_equities) > room:
            # Over Schwab's limit: stream what fits, charts first (pinned and
            # the option chain's underlying), and say so in status - a quiet
            # partial feed must never again look like a healthy full one.
            with self._lock:
                first = set(self._pinned_equities)
                if self._option_equity:
                    first.add(self._option_equity)
            ranked = sorted(new_equities, key=lambda symbol: (symbol not in first, symbol))
            dropped = len(ranked) - max(0, room)
            new_equities = sorted(ranked[:max(0, room)])
            self._last_error = (
                f"equity cap: {dropped} symbol(s) not streamed "
                f"(Schwab limit {_MAX_EQUITY_SYMBOLS})"
            )
        new_options = sorted(desired_options - options)
        removed_options = sorted(options - desired_options)
        if removed_equities:
            await stream.level_one_equity_unsubs(removed_equities)
            await stream.chart_equity_unsubs(removed_equities)
            equities.difference_update(removed_equities)
        if removed_options:
            await stream.level_one_option_unsubs(removed_options)
            options.difference_update(removed_options)
        if new_equities:
            # Chunked, never one giant request. A 397-symbol watchlist sent
            # every symbol in a single subs call; when Schwab rejects an
            # oversized request the exception tears down handle_message(),
            # the whole stream reconnects, re-sends the same oversized
            # request, and loops forever — so the forming candle stopped
            # ticking and only advanced on the 1/min REST reconcile.
            # Options were already capped at 300; equities had no bound.
            first_batch = not equities
            for index in range(0, len(new_equities), _EQUITY_SUBSCRIBE_CHUNK):
                batch = new_equities[index:index + _EQUITY_SUBSCRIBE_CHUNK]
                if first_batch and index == 0:
                    await stream.level_one_equity_subs(batch)
                    await stream.chart_equity_subs(batch)
                else:
                    await stream.level_one_equity_add(batch)
                    await stream.chart_equity_add(batch)
                equities.update(batch)
        if new_options:
            if options:
                await stream.level_one_option_add(new_options)
            else:
                await stream.level_one_option_subs(new_options)
            options.update(new_options)

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        retry_delay = 2.0
        while not self._stop.is_set():
            stream = None
            message_task: asyncio.Task | None = None
            try:
                adapter = self._client_factory()
                if not adapter.configured:
                    raise RuntimeError("Schwab streaming is not configured.")
                stream = StreamClient(adapter.library_client(), enforce_enums=False)
                stream.add_level_one_equity_handler(self._handle_equity)
                stream.add_chart_equity_handler(self._handle_chart)
                stream.add_level_one_option_handler(self._handle_option)
                await stream.login()
                with self._lock:
                    self._connected = True
                    self._last_error = ""
                retry_delay = 2.0
                self._publish("status", "", {"connected": True})
                equities: set[str] = set()
                options: set[str] = set()
                await self._apply_subscriptions(stream, equities, options)
                while not self._stop.is_set():
                    if message_task is None:
                        message_task = asyncio.create_task(stream.handle_message())
                    wake_task = asyncio.create_task(self._wake.wait())
                    done, pending = await asyncio.wait({message_task, wake_task}, return_when=asyncio.FIRST_COMPLETED)
                    if wake_task in done:
                        self._wake.clear()
                        await self._apply_subscriptions(stream, equities, options)
                    if wake_task in pending:
                        wake_task.cancel()
                    if message_task in done:
                        message_task.result()
                        message_task = None
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = str(exc)
                self._publish("status", "", {"connected": False, "error": str(exc)})
                if not self._stop.is_set():
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=retry_delay)
                        self._wake.clear()
                    except asyncio.TimeoutError:
                        pass
                    retry_delay = min(retry_delay * 2.0, 30.0)
            finally:
                with self._lock:
                    self._connected = False
                if message_task is not None and not message_task.done():
                    message_task.cancel()
                    try:
                        await message_task
                    except (asyncio.CancelledError, Exception):
                        pass
                if stream is not None:
                    try:
                        await stream.logout()
                    except Exception:
                        pass
        self._loop = None
        self._wake = None
