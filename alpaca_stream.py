"""Alpaca fallback publisher for the shared live market-event stream.

Feeds the same event queue the Schwab streamer publishes to, so the browser's
single /api/live-market-stream subscription receives packets from whichever
source is alive. While the Schwab (TOS) stream is connected, this publisher
stays quiet to avoid duplicate ticks; whenever Schwab is down (expired token,
no trading profile) Alpaca carries the live candles and prices instead.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable


# How long a socket may sit un-authenticated before we tear it down. Long
# enough for a slow handshake, short enough that a wedged retry loop cannot
# hold the account's only connection slot for a whole session.
CONNECT_TIMEOUT_SECONDS = 45.0


class AlpacaBarStream:
    """One reconnecting Alpaca websocket shared by all browser clients."""

    def __init__(
        self,
        credentials_provider: Callable[[], tuple[str, str] | None],
        publish: Callable[[str, str, dict], None],
        primary_connected: Callable[[], bool],
        feed: str = "iex",
        restart_debounce_seconds: float = 8.0,
    ) -> None:
        self._credentials_provider = credentials_provider
        self._publish = publish
        self._primary_connected = primary_connected
        self._feed = str(feed or "iex").lower()
        self._restart_debounce_seconds = max(0.5, float(restart_debounce_seconds))
        self._lock = threading.RLock()
        self._desired: set[str] = set()
        self._running_symbols: set[str] = set()
        self._stream = None
        self._thread: threading.Thread | None = None
        self._restart_timer: threading.Timer | None = None
        self._connected = False
        self._started_at = 0.0
        self._last_error = ""
        self._stopping = False
        self._standby = False
        self._supervisor_started = False
        # Set when Alpaca reports its per-account connection limit exhausted.
        # The count only decays as leaked sockets time out server-side, so
        # retrying before this passes just keeps the limit pinned.
        self._connection_limit_until = 0.0
        # Last ERROR alpaca-py logged; it never raises these to us.
        self._last_provider_error = ""
        self._log_hook_installed = False

    # ------------------------------------------------------------------ state
    def status(self) -> dict:
        with self._lock:
            standby = (
                not self._connected
                and not self._last_error
                and bool(self._desired)
                and self._standby
            )
            return {
                "connected": self._connected,
                "standby": standby,
                "source": f"alpaca:{self._feed}" if not standby else "standby (primary feed serving)",
                "subscribedSymbols": len(self._running_symbols),
                "lastError": self._last_error,
            }

    def set_symbols(self, symbols: Iterable[str]) -> None:
        """Adjust the live subscription set; restarts the socket debounced."""
        desired = {
            str(symbol or "").strip().upper()
            for symbol in symbols
            if str(symbol or "").strip()
        }
        with self._lock:
            self._ensure_supervisor_locked()
            if desired == self._desired:
                return
            self._desired = desired
            # STANDBY: while the primary (consolidated) feed is serving, do
            # not hold Alpaca's single account websocket at all — it exists
            # purely as failover, and connecting it while restarts from the
            # same day still hold account-side slots produced endless
            # "connection limit exceeded" retry churn. The supervisor thread
            # connects on demand when the primary goes unhealthy.
            primary_serving = False
            try:
                primary_serving = bool(self._primary_connected())
            except Exception:
                primary_serving = False
            if primary_serving:
                self._standby = True
                return
            self._standby = False
            # Do not dial into an exhausted account connection limit; the
            # count decays only as Alpaca times out the leaked sockets, and
            # each attempt during that window refreshes the exhaustion.
            if time.monotonic() < float(getattr(self, "_connection_limit_until", 0.0)):
                return
            # Alpaca allows ONE data websocket per account, and a reconnect
            # that overlaps the old socket fails with "connection limit
            # exceeded" until the server releases the slot. Only a symbol we
            # are NOT already subscribed to justifies a bounce; removals keep
            # the existing socket and simply stream extra symbols nobody
            # displays.
            if (
                desired <= self._running_symbols
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return
            if self._restart_timer is not None:
                self._restart_timer.cancel()
            # alpaca-py's stream cannot re-subscribe reliably mid-run from
            # another thread, so new symbols bounce the socket. Debounce so a
            # burst of chart mounts costs one reconnect, not five.
            self._restart_timer = threading.Timer(
                self._restart_debounce_seconds, self._restart,
            )
            self._restart_timer.daemon = True
            self._restart_timer.start()

    def _ensure_supervisor_locked(self) -> None:
        if self._supervisor_started:
            return
        self._supervisor_started = True
        threading.Thread(
            target=self._supervise,
            name="alpaca-stream-supervisor",
            daemon=True,
        ).start()

    def _supervise(self) -> None:
        """Failover manager: hold the socket only while the primary is down."""
        while True:
            time.sleep(15)
            with self._lock:
                desired = set(self._desired)
                running = self._thread is not None and self._thread.is_alive()
            primary_serving = False
            try:
                primary_serving = bool(self._primary_connected())
            except Exception:
                primary_serving = False
            if primary_serving and running:
                # Return to standby: free the account's only socket slot and
                # stop any retry churn while the consolidated feed serves.
                with self._lock:
                    self._standby = True
                    self._stop_locked()
                    self._running_symbols = set()
                    self._last_error = ""
            elif not primary_serving and desired and not running:
                with self._lock:
                    self._standby = False
                self._restart()

    # ------------------------------------------------------------- lifecycle
    def _restart(self) -> None:
        with self._lock:
            self._restart_timer = None
            # Choke point for the backoff: set_symbols is not the only caller
            # (the supervisor dials directly every 15s while the primary is
            # down), and each attempt against an exhausted account limit
            # refreshes the exhaustion instead of clearing it.
            if time.monotonic() < self._connection_limit_until:
                return
            old_thread = self._thread
            self._stop_locked()
        # Join OUTSIDE the lock (the run thread's cleanup takes it), then give
        # Alpaca's edge a moment to release the account's single socket slot -
        # reconnecting instantly is what produced "connection limit exceeded".
        if old_thread is not None and old_thread.is_alive():
            old_thread.join(timeout=10.0)
        if old_thread is not None:
            time.sleep(3.0)
        # The debounce window is long enough for Schwab to finish logging in.
        # Re-check it here: set_symbols may have scheduled this restart while
        # Schwab was still connecting, but starting Alpaca afterward would
        # consume the account's only socket and can create an endless
        # "connection limit exceeded" retry loop beside a healthy primary.
        primary_serving = False
        try:
            primary_serving = bool(self._primary_connected())
        except Exception:
            primary_serving = False
        with self._lock:
            desired = set(self._desired)
            if not desired:
                self._running_symbols = set()
                return
            if primary_serving:
                self._standby = True
                self._running_symbols = set()
                self._last_error = ""
                return
            self._running_symbols = set(desired)
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run,
                args=(sorted(desired),),
                name="alpaca-bar-stream",
                daemon=True,
            )
            self._thread.start()

    def _stop_locked(self) -> None:
        self._stopping = True
        stream = self._stream
        self._stream = None
        self._connected = False
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass

    def _capture_alpaca_errors(self) -> None:
        """Record why alpaca-py's socket failed, since it never tells us.

        alpaca-py swallows auth failures inside its own retry loop and only
        log.exception()s them, so "connection limit exceeded" / "auth failed"
        never reach any of our code. Without this, a dead failover feed shows
        up in Settings as a bare timeout with no cause, which is exactly the
        state that hid a whole day of failed Alpaca auth behind a
        connected:true flag.
        """
        if getattr(self, "_log_hook_installed", False):
            return
        self._log_hook_installed = True
        stream_self = self

        class _ErrorCapture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                try:
                    message = str(record.getMessage() or "").strip()
                except Exception:
                    return
                if not message:
                    return
                with stream_self._lock:
                    stream_self._last_provider_error = message[:300]

        handler = _ErrorCapture(level=logging.ERROR)
        logging.getLogger("alpaca").addHandler(handler)

    def _run(self, symbols: list[str]) -> None:
        try:
            self._capture_alpaca_errors()
            credentials = self._credentials_provider()
            if not credentials:
                with self._lock:
                    self._last_error = "Add an Alpaca API key in Settings to enable the live bar stream."
                return
            key, secret = credentials
            from alpaca.data.enums import DataFeed
            from alpaca.data.live import StockDataStream
            try:
                feed = DataFeed(self._feed)
            except ValueError:
                feed = DataFeed.IEX
            stream = StockDataStream(api_key=key, secret_key=secret, feed=feed)

            async def on_bar(bar) -> None:
                # Minute bars are NOT gated on Schwab health: the watchlist
                # minis live on Alpaca bars by design (dual-feed: Schwab owns
                # the active chart price line, Alpaca owns background candles).
                # Only 'equity' packets stay single-writer gated.
                try:
                    self._publish("chart", str(bar.symbol), {
                        "time": int(bar.timestamp.timestamp()),
                        "open": float(bar.open),
                        "high": float(bar.high),
                        "low": float(bar.low),
                        "close": float(bar.close),
                        "volume": float(bar.volume or 0),
                        "source": f"alpaca:{self._feed}",
                    })
                except Exception:
                    pass

            latest_quotes: dict[str, tuple[float, float]] = {}
            # SPY alone streams dozens of quotes per second; publishing each
            # one made every subscribed panel re-render ~65 times/sec and the
            # charts visibly janky. Coalesce per symbol and flush at 4/sec —
            # faster than the eye needs, 15x fewer renders.
            pending_equity: dict[str, dict] = {}
            pending_lock = threading.Lock()

            def flush_pending_equity() -> None:
                while True:
                    time.sleep(0.25)
                    with self._lock:
                        if self._stopping or self._stream is not stream:
                            return
                    with pending_lock:
                        items = list(pending_equity.items())
                        pending_equity.clear()
                    for symbol_key, data in items:
                        try:
                            # Consumers discriminate by source: the active
                            # chart takes alpaca:* equity only as stale-REST
                            # failover, consolidated (schwab) always.
                            data["source"] = f"alpaca:{self._feed}"
                            self._publish("equity", symbol_key, data)
                        except Exception:
                            pass

            async def on_quote(quote) -> None:
                try:
                    bid = float(quote.bid_price) if quote.bid_price else 0.0
                    ask = float(quote.ask_price) if quote.ask_price else 0.0
                    if bid > 0 and ask > 0 and ask >= bid:
                        latest_quotes[str(quote.symbol)] = (bid, ask)
                    if self._primary_connected():
                        return
                    with pending_lock:
                        entry = pending_equity.setdefault(str(quote.symbol), {})
                        entry["bid"] = bid or None
                        entry["ask"] = ask or None
                except Exception:
                    pass

            async def on_trade(trade) -> None:
                if self._primary_connected():
                    return
                try:
                    price = float(trade.price)
                    # IEX raw prints include odd lots and stale away-market
                    # executions that consolidated feeds suppress. One such
                    # print (75.68 against a 77.42 market) yanked the live
                    # price line and painted a fake $1.7 wick. Only publish a
                    # last price within 1% of the current quote midpoint.
                    quote = latest_quotes.get(str(trade.symbol))
                    if quote:
                        mid = (quote[0] + quote[1]) / 2
                        if mid > 0 and abs(price - mid) / mid > 0.01:
                            return
                    with pending_lock:
                        entry = pending_equity.setdefault(str(trade.symbol), {})
                        entry["last"] = price
                        entry["tradeTime"] = int(trade.timestamp.timestamp() * 1000)
                except Exception:
                    pass

            stream.subscribe_bars(on_bar, *symbols)
            stream.subscribe_quotes(on_quote, *symbols)
            stream.subscribe_trades(on_trade, *symbols)
            with self._lock:
                if self._stopping:
                    return
                self._stream = stream
                self._started_at = time.monotonic()
                self._last_error = ""
            threading.Thread(
                target=flush_pending_equity,
                name="alpaca-equity-flush",
                daemon=True,
            ).start()

            def confirm_connected() -> None:
                """Watch for a REAL authenticated socket, and give up if none.

                A blind "assume connected after 5s" timer used to live here,
                and it lied: on 2026-08-10 health reported connected:true while
                alpaca-py was failing auth in a tight loop.

                It also has to police that loop. alpaca-py's _run_forever
                catches ValueError('connection limit exceeded'), logs it, and
                hits `finally: await asyncio.sleep(0)` - zero delay - so it
                retries auth as fast as the network allows, forever. The
                exception never reaches our handler and stream.run() never
                returns, so nothing here could ever see it. It burns CPU, spams
                tracebacks, and keeps Alpaca's connection count pinned so the
                limit can never decay. The only way out is to stop it from
                outside once it is clearly not going to connect.
                """
                deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    with self._lock:
                        if self._stopping or self._stream is not stream:
                            return
                    # alpaca-py flips _running only after auth + subscribe.
                    if getattr(stream, "_running", False):
                        with self._lock:
                            if self._stream is stream and not self._stopping:
                                self._connected = True
                        return
                    time.sleep(1.0)
                with self._lock:
                    if self._stopping or self._stream is not stream:
                        return
                    self._connected = False
                    reason = self._last_provider_error
                    self._last_error = (
                        f"Alpaca websocket never authenticated ({CONNECT_TIMEOUT_SECONDS:.0f}s): "
                        f"{reason}" if reason else
                        f"Alpaca websocket never authenticated ({CONNECT_TIMEOUT_SECONDS:.0f}s); backing off."
                    )
                    if "connection limit" in reason.lower():
                        self._connection_limit_until = time.monotonic() + 300.0
                    self._connection_limit_until = time.monotonic() + 300.0
                # Outside the lock: stop() hands a coroutine to the stream's own
                # loop, which ends _run_forever and releases the account slot.
                try:
                    stream.stop()
                except Exception:
                    pass

            confirm = threading.Thread(
                target=confirm_connected,
                name="alpaca-connect-confirm",
                daemon=True,
            )
            confirm.start()
            stream.run()
        except BaseException as exc:
            # BaseException, not Exception: alpaca-py's run() wraps
            # asyncio.run(), which can surface SystemExit/CancelledError from
            # the loop's teardown. On 2026-08-10 a ValueError('connection
            # limit exceeded') from _auth escaped this handler and the whole
            # api_server process exited mid-session. Nothing this stream can
            # hit may ever kill the backend: the failover feed going down
            # must degrade to REST, not take trading down with it.
            detail = str(exc).strip()
            # SystemExit(1) stringifies to a bare "1", which reads as nonsense
            # in the Settings status line — name the class for anything that
            # is not a plain Exception with a message.
            message = (
                detail
                if detail and isinstance(exc, Exception)
                else f"{exc.__class__.__name__}: {detail}".rstrip(": ")
            )
            with self._lock:
                if not self._stopping:
                    self._last_error = message
                # Alpaca allows ONE data websocket per account and counts
                # leaked connections until they time out server-side. While
                # the limit is exhausted, reconnecting fast keeps it
                # exhausted — so back off hard and let it decay.
                if "connection limit" in message.lower():
                    self._connection_limit_until = time.monotonic() + 300.0
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                # A genuine interpreter shutdown still stops this thread, but
                # it stops HERE rather than propagating out of the thread.
                return
        finally:
            with self._lock:
                if self._stream is not None and self._thread is threading.current_thread():
                    self._stream = None
                self._connected = False
