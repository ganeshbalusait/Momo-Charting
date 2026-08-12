from __future__ import annotations

from alpaca_stream import AlpacaBarStream


def test_debounced_restart_stays_in_standby_when_primary_recovers() -> None:
    stream = AlpacaBarStream(
        credentials_provider=lambda: ("key", "secret"),
        publish=lambda *_args: None,
        primary_connected=lambda: True,
        restart_debounce_seconds=0.5,
    )
    with stream._lock:
        stream._desired = {"NVDA"}

    stream._restart()

    status = stream.status()
    assert status["standby"] is True
    assert status["subscribedSymbols"] == 0
    assert stream._thread is None


def _install_fake_alpaca(monkeypatch, raise_on_run: BaseException) -> None:
    """Swap in a websocket that only raises, so no socket is ever opened."""
    import sys
    import types

    class FakeStream:
        def __init__(self, **_kwargs) -> None:
            pass

        def subscribe_bars(self, *_args) -> None:
            pass

        subscribe_quotes = subscribe_trades = subscribe_bars

        def stop(self) -> None:
            pass

        def run(self):
            raise raise_on_run

    class DataFeed(str):
        IEX = "iex"

        def __new__(cls, value):
            if value not in ("iex", "sip"):
                raise ValueError(value)
            return str.__new__(cls, value)

    live = types.ModuleType("alpaca.data.live")
    live.StockDataStream = FakeStream
    enums = types.ModuleType("alpaca.data.enums")
    enums.DataFeed = DataFeed
    for name, module in (
        ("alpaca", types.ModuleType("alpaca")),
        ("alpaca.data", types.ModuleType("alpaca.data")),
        ("alpaca.data.live", live),
        ("alpaca.data.enums", enums),
    ):
        monkeypatch.setitem(sys.modules, name, module)


def _run_capturing_escapes(stream: AlpacaBarStream) -> list[BaseException]:
    import threading

    escaped: list[BaseException] = []

    def target() -> None:
        try:
            stream._run(["SPY"])
        except BaseException as exc:  # noqa: BLE001 - containment is the test
            escaped.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=15)
    assert not thread.is_alive(), "stream thread hung"
    return escaped


def _failover_stream() -> AlpacaBarStream:
    stream = AlpacaBarStream(
        credentials_provider=lambda: ("key", "secret"),
        publish=lambda *_args: None,
        primary_connected=lambda: False,
    )
    stream._supervisor_started = True  # never spawn the supervisor in a test
    return stream


def test_connection_limit_error_cannot_escape_the_stream_thread(monkeypatch) -> None:
    """2026-08-10: this ValueError exited the whole api_server process."""
    stream = _failover_stream()
    _install_fake_alpaca(monkeypatch, ValueError("connection limit exceeded"))

    assert _run_capturing_escapes(stream) == []
    assert "connection limit exceeded" in stream.status()["lastError"]


def test_system_exit_from_asyncio_teardown_cannot_escape(monkeypatch) -> None:
    stream = _failover_stream()
    _install_fake_alpaca(monkeypatch, SystemExit(1))

    assert _run_capturing_escapes(stream) == []
    assert stream.status()["lastError"] == "SystemExit: 1"


def test_connection_limit_suppresses_reconnects_until_it_decays(monkeypatch) -> None:
    """Alpaca's per-account limit only decays as leaked sockets time out;
    retrying inside that window keeps it pinned."""
    import time

    stream = _failover_stream()
    _install_fake_alpaca(monkeypatch, ValueError("connection limit exceeded"))
    _run_capturing_escapes(stream)

    assert stream._connection_limit_until - time.monotonic() > 60

    stream._desired = set()
    stream.set_symbols(["SPY", "QQQ"])
    assert stream._restart_timer is None, "debounced reconnect scheduled anyway"

    # The supervisor calls _restart() directly every 15s - same guard applies.
    with stream._lock:
        stream._desired = {"SPY"}
        stream._thread = None
    stream._restart()
    assert stream._thread is None, "supervisor reconnected into the exhausted limit"


def test_ordinary_stream_error_still_reconnects(monkeypatch) -> None:
    stream = _failover_stream()
    _install_fake_alpaca(monkeypatch, RuntimeError("socket closed"))
    _run_capturing_escapes(stream)

    assert stream._connection_limit_until == 0.0
    stream.set_symbols(["SPY"])
    assert stream._restart_timer is not None
    stream._restart_timer.cancel()


def test_wedged_auth_loop_is_torn_down_instead_of_spinning(monkeypatch) -> None:
    """alpaca-py retries auth failures internally with ZERO delay.

    Its _run_forever catches ValueError('connection limit exceeded'), logs it,
    and hits `finally: await asyncio.sleep(0)`, so it re-auths as fast as the
    network allows and stream.run() never returns. Our except clause can never
    see it. Something has to stop it from outside, or it burns CPU and keeps
    Alpaca's connection count pinned so the limit never decays.
    """
    import sys
    import threading
    import time
    import types

    import alpaca_stream as module

    monkeypatch.setattr(module, "CONNECT_TIMEOUT_SECONDS", 2.0)

    stopped = threading.Event()

    class WedgedStream:
        """Never authenticates; only stop() ends it - like the real loop."""

        _running = False

        def __init__(self, **_kwargs) -> None:
            pass

        def subscribe_bars(self, *_args) -> None:
            pass

        subscribe_quotes = subscribe_trades = subscribe_bars

        def stop(self) -> None:
            stopped.set()

        def run(self):
            stopped.wait(timeout=15)

    class DataFeed(str):
        IEX = "iex"

        def __new__(cls, value):
            if value not in ("iex", "sip"):
                raise ValueError(value)
            return str.__new__(cls, value)

    live = types.ModuleType("alpaca.data.live")
    live.StockDataStream = WedgedStream
    enums = types.ModuleType("alpaca.data.enums")
    enums.DataFeed = DataFeed
    for name, mod in (
        ("alpaca", types.ModuleType("alpaca")),
        ("alpaca.data", types.ModuleType("alpaca.data")),
        ("alpaca.data.live", live),
        ("alpaca.data.enums", enums),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    stream = _failover_stream()
    assert _run_capturing_escapes(stream) == []

    assert stopped.is_set(), "a socket that never authenticates must be stopped"
    assert stream.status()["connected"] is False, "must not report a phantom connection"
    assert stream._connection_limit_until > time.monotonic(), "backoff must be armed"


def test_connected_is_only_reported_once_the_socket_authenticates(monkeypatch) -> None:
    """The old code assumed connected after 5s, so health reported
    connected:true while auth was failing in a loop."""
    import sys
    import types

    import alpaca_stream as module

    monkeypatch.setattr(module, "CONNECT_TIMEOUT_SECONDS", 5.0)

    class AuthenticatingStream:
        def __init__(self, **_kwargs) -> None:
            self._running = False

        def subscribe_bars(self, *_args) -> None:
            pass

        subscribe_quotes = subscribe_trades = subscribe_bars

        def stop(self) -> None:
            self._running = False

        def run(self):
            import time as _t
            self._running = True          # auth succeeds
            _t.sleep(2.5)

    class DataFeed(str):
        IEX = "iex"

        def __new__(cls, value):
            return str.__new__(cls, value)

    live = types.ModuleType("alpaca.data.live")
    live.StockDataStream = AuthenticatingStream
    enums = types.ModuleType("alpaca.data.enums")
    enums.DataFeed = DataFeed
    for name, mod in (
        ("alpaca", types.ModuleType("alpaca")),
        ("alpaca.data", types.ModuleType("alpaca.data")),
        ("alpaca.data.live", live),
        ("alpaca.data.enums", enums),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    stream = _failover_stream()
    assert _run_capturing_escapes(stream) == []
    assert stream._connection_limit_until == 0.0, "a healthy socket must not arm the backoff"


def test_status_surfaces_the_reason_alpaca_refused(monkeypatch) -> None:
    """A bare timeout hides the cause; the cause is what makes it fixable."""
    import logging
    import sys
    import threading
    import types

    import alpaca_stream as module

    monkeypatch.setattr(module, "CONNECT_TIMEOUT_SECONDS", 2.0)
    stopped = threading.Event()

    class WedgedStream:
        _running = False

        def __init__(self, **_kwargs) -> None:
            pass

        def subscribe_bars(self, *_args) -> None:
            pass

        subscribe_quotes = subscribe_trades = subscribe_bars

        def stop(self) -> None:
            stopped.set()

        def run(self):
            # alpaca-py only ever LOGS this; it never raises it to us.
            logging.getLogger("alpaca").error(
                "error during websocket communication: connection limit exceeded"
            )
            stopped.wait(timeout=10)

    class DataFeed(str):
        IEX = "iex"

        def __new__(cls, value):
            return str.__new__(cls, value)

    live = types.ModuleType("alpaca.data.live")
    live.StockDataStream = WedgedStream
    enums = types.ModuleType("alpaca.data.enums")
    enums.DataFeed = DataFeed
    for name, mod in (
        ("alpaca", types.ModuleType("alpaca")),
        ("alpaca.data", types.ModuleType("alpaca.data")),
        ("alpaca.data.live", live),
        ("alpaca.data.enums", enums),
    ):
        monkeypatch.setitem(sys.modules, name, mod)

    stream = _failover_stream()
    _run_capturing_escapes(stream)

    last_error = stream.status()["lastError"]
    assert "connection limit exceeded" in last_error, last_error
    # And a connection-limit refusal must still arm the long backoff.
    import time
    assert stream._connection_limit_until > time.monotonic()
