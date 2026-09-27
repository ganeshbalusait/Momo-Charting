"""The MomX scanner as its OWN process. Not a workaround - the architecture.

Why this is a separate process, measured 2026-08-28 07:20 ET:

    same 30-minute fetch, same machine, same network
      standalone process : 60 symbols in 10.0s
      inside api_server   : still running after 5h19m

api_server is CPU-saturated by the chart engine (study-extend, chart warmers,
chain builders - visible all through api_server.out.log). It had burned 19,480
CPU-seconds in 5h19m wall. A 357-symbol board build sharing that GIL crawls,
and worse, it steals cycles from the charts the trader is actually looking at.
This repo has a documented history of exactly this failure (the OI-finder
saturation, the six-chart study starvation).

So the scanner owns a process. It cannot starve the chart engine and the chart
engine cannot starve it. api_server proxies the momx routes here and NEVER
builds a board itself - see the note on _momx_proxy in api_server.

Supervised by scripts/scanner_watchdog.ps1 alongside api_server and the gateway.
Run standalone with:  .venv/Scripts/python.exe momx_worker.py
"""

from __future__ import annotations

import gzip
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import atexit
import signal
import time

from momx import board, fastlane, grade_log, history, momo_alert, movers, query, service

PORT = 3010

#: Momentum event ledger, keyed by list name. Lives here rather than in
#: api_server so the diff runs in the process that owns the board snapshots.
_LEDGERS: dict[str, dict] = {}

#: Momo Alert state, PER USER (2026-08-30: each account tunes its own
#: timeframes/thresholds/sound/phone topic; one user change never touches
#: another). Board baselines (prev/stamps) are shared - there is one market -
#: but every account gets its own cooldown ledger and alert list, evaluated
#: against ITS config. A background thread evaluates every ~10s so a user
#: phone push fires from the worker even with every browser closed; the
#: request handlers only READ.
import threading as _threading

_MOMO = {
    "stamps": {},  # list name -> last diffed generatedAt (shared)
    "prev": {},    # list name -> last completed payload (shared)
    "users": {},   # email -> {"cooldown": {sym: epoch}, "alerts": [...]}
}
_MOMO_LOCK = _threading.Lock()
_MOMO_ALERT_CAP = 20
_MOMO_EVAL_SECONDS = 10.0


def _momo_user_state(email: str) -> dict:
    held = _MOMO["users"].get(email)
    if held is None:
        held = {"cooldown": {}, "alerts": []}
        _MOMO["users"][email] = held
    return held


def _momo_evaluate() -> None:
    """Diff any new builds against EVERY account config, push per account.

    Both directions of each list (spec 2026-09-24): the bear board is keyed
    "<list> BEAR" in the ledgers and in the alert's ``list`` text, so a bear
    RVOL spike reads "MOMO NVDA ... (Mag7 BEAR)" on the phone."""
    for name in ("Mag7", "Watchlist"):
      for direction in ("bull", "bear"):
        key = name if direction == "bull" else f"{name} BEAR"
        try:
            current = service.snapshot(name, direction)
        except Exception:  # noqa: BLE001 - one bad list must not kill alerts
            continue
        if current.get("warming") or current.get("fromDisk"):
            continue  # not a completed live build; keep the old baseline
        current = {**current, "list": key}
        stamp = current.get("generatedAt")
        per_user_fresh = []
        with _MOMO_LOCK:
            if not stamp or stamp == _MOMO["stamps"].get(key):
                continue
            previous = _MOMO["prev"].get(key)
            _MOMO["prev"][key] = current
            _MOMO["stamps"][key] = stamp
            for email, config in momo_alert.all_user_configs().items():
                state = _momo_user_state(email)
                fresh = momo_alert.detect(current, previous, config, state["cooldown"])
                if fresh:
                    state["alerts"] = (fresh[::-1] + state["alerts"])[:_MOMO_ALERT_CAP]
                    per_user_fresh.append((config, fresh))
        # Pushes OUTSIDE the lock: network calls must never block readers.
        # The push window gates ONLY the phone push (trader, 2026-08-31:
        # "ntfy alert momo scanner from 9:15am to 3:30pm est"); the in-app
        # alerts were already recorded above regardless.
        for config, fresh in per_user_fresh:
            if not momo_alert.push_window_allows(config):
                continue
            momo_alert.push_ntfy(fresh, config.get("ntfyTopic") or "")


#: The auto-managed list of the day's biggest gainers. Named like any other
#: list so it appears in the picker, the history archive and the alerts with
#: no special-casing anywhere downstream.
MOVERS_LIST_NAME = "Movers"

#: Every 5 minutes. The screener is one small HTTP call, but each MEMBERSHIP
#: change costs a board build, so this is slow enough that the list settles.
_MOVERS_REFRESH_SECONDS = 300.0

#: Regular trading hours only, in ET minutes-of-day. The screener reports the
#: regular session; premarket movers are the Premarket scanner's job, and a
#: list that silently held yesterday's names overnight would be worse than an
#: empty one.
_MOVERS_OPEN_MINUTE = 9 * 60 + 25
_MOVERS_CLOSE_MINUTE = 16 * 60 + 5


def _movers_credentials() -> tuple[str, str]:
    """The PAPER5 profile: measured 2026-09-03, the only key of the four that
    the screener accepts (the rest 401 on market data too - they are dead)."""
    import os

    return (
        os.getenv("ALPACA_PROFILE_PAPER5_KEY_ID", "") or "",
        os.getenv("ALPACA_PROFILE_PAPER5_SECRET_KEY", "") or "",
    )


def _movers_window_open(now_et) -> bool:
    if now_et.weekday() >= 5:
        return False
    minute = now_et.hour * 60 + now_et.minute
    return _MOVERS_OPEN_MINUTE <= minute <= _MOVERS_CLOSE_MINUTE


def _movers_refresh() -> dict:
    """One pass: fetch, filter, and write ONLY if the membership changed."""
    key, secret = _movers_credentials()
    gainers = movers.fetch_gainers(key, secret)
    if not gainers:
        # A dead key, a rate limit or a screener outage. Leave the list alone;
        # emptying a board he is watching is far worse than a stale one.
        return {"ok": False, "reason": "no gainers returned", "wrote": False}
    # Oversample, then cut to the cap only after the no-options names are gone
    # (2026-09-26: 101 of 162 movers the back-test traded had no options; with
    # the scanner now options-only they would take slots and never show).
    picked = movers.select_movers(gainers, limit=movers.MAX_SYMBOLS * 3)
    if picked:
        # Second pass, against the asset NAMES: 2x/3x single-stock wrappers
        # look like ordinary tickers (MSTX, HOOG, SNOU) and filled 20 of the
        # first 26 slots with geared copies of the same few underlyings.
        picked = movers.drop_leveraged(picked, movers.fetch_asset_names(picked, key, secret))
    picked = service.drop_no_options(picked)[: movers.MAX_SYMBOLS]
    if not picked:
        return {"ok": True, "reason": "nothing cleared the filters", "wrote": False}
    try:
        current = board.load_universe(MOVERS_LIST_NAME)
    except Exception:  # noqa: BLE001 - the list does not exist yet
        current = []
    if not movers.membership_changed(current, picked):
        return {"ok": True, "reason": "unchanged", "wrote": False, "count": len(picked)}
    board.save_universe(picked, MOVERS_LIST_NAME, create=True)
    service.refresh_now(MOVERS_LIST_NAME)
    return {"ok": True, "wrote": True, "count": len(picked), "symbols": picked[:10]}


def _movers_loop() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    while True:
        try:
            if _movers_window_open(datetime.now(eastern)):
                _movers_refresh()
        except Exception:  # noqa: BLE001 - this loop must never take the worker down
            pass
        time.sleep(_MOVERS_REFRESH_SECONDS)


def _momo_loop() -> None:
    while True:
        time.sleep(_MOMO_EVAL_SECONDS)
        try:
            _momo_evaluate()
        except Exception:  # noqa: BLE001 - the evaluator must never die
            pass


def _momo_alerts(email: str) -> dict:
    """This account recent alerts. Evaluation is the loop job; a poll only
    piggybacks a catch-up call so a just-started worker answers fresh."""
    try:
        _momo_evaluate()
    except Exception:  # noqa: BLE001
        pass
    config = momo_alert.load_user_config(email)
    with _MOMO_LOCK:
        state = _momo_user_state(email)
        return {
            "alerts": list(state["alerts"]),
            "sound": bool(config.get("sound", True)),
        }


def _ledger_key(name: str, direction: str) -> str:
    return f"{name}|bear" if direction == "bear" else name


def _direction(query: dict) -> str:
    """``?dir=bear`` selects the BEAR board (spec 2026-09-24); anything else is bull."""
    try:
        return "bear" if str(query.get("dir", [""])[0]).strip().lower() == "bear" else "bull"
    except (AttributeError, IndexError, TypeError):
        return "bull"


def _momentum(list_name: str | None, direction: str = "bull") -> dict:
    """New-match and RVOL-crossing events since the previous completed build.

    Re-diffs ONLY when generatedAt moves (once per build), so the panel's 15s
    poll costs microseconds no matter how often it is called. The bear board
    keeps its own ledger under ``<list>|bear``.
    """
    direction = "bear" if direction == "bear" else "bull"
    current = service.snapshot(list_name, direction)
    name = str(current.get("list") or "")
    key = _ledger_key(name, direction)
    held = _LEDGERS.get(key) or {"stamp": None, "prev": None, "events": []}
    stamp = current.get("generatedAt")
    if stamp and stamp != held["stamp"] and not current.get("warming"):
        fresh = fastlane.diff_matches(held["prev"], current)
        fresh += fastlane.rvol_spikes(current, previous=held["prev"])
        held["events"] = fastlane.merge_events(held["events"], fresh, cap=50)
        held["prev"] = current
        held["stamp"] = stamp
        _LEDGERS[key] = held
    # On EVERY read, not just on a new build: right after a paste the
    # snapshot is a warming placeholder carrying the new universe, and the
    # chips for the tickers he just removed must go now, not 30s later.
    pruned = fastlane.prune_events(held["events"], current)
    if len(pruned) != len(held["events"]):
        held["events"] = pruned
        _LEDGERS[key] = held
    return {"events": held["events"], "list": name, "generatedAt": stamp, "direction": direction}


def _clear_momentum(list_name: str | None) -> dict:
    """Empty the strip for one list. The diff baseline is kept, so the next
    build reports only what changes after the clear, not a replay."""
    current = service.snapshot(list_name)
    name = str(current.get("list") or "")
    for key in (name, _ledger_key(name, "bear")):   # both directions of the list
        held = _LEDGERS.get(key)
        if held is not None:
            held["events"] = []
            _LEDGERS[key] = held
    return {"events": [], "list": name, "generatedAt": current.get("generatedAt"), "cleared": True}


def _int_or_zero(value) -> int:
    """A query-string integer, or 0. Never raises on a hand-typed URL."""
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return  # a 15s poll would fill the log with noise

    def _momo_user(self) -> str:
        """The account this request acts for. api_server forwards the session
        email in X-AGX-User; a bare :3010 request (local tooling) falls back
        to the shared default profile."""
        return str(
            self.headers.get("X-AGX-User") or momo_alert.DEFAULT_USER
        ).strip().lower() or momo_alert.DEFAULT_USER

    #: Below this, gzip costs more CPU than the bytes it saves.
    GZIP_MIN_BYTES = 8192

    def _json(self, status: HTTPStatus, payload: object) -> None:
        """JSON, gzipped when the client asked and it is worth it.

        The scanner history made this necessary: ONE day of the Watchlist
        board is 423 snapshots and 846 KB uncompressed by 09:00, growing all
        session, and every snapshot repeats the same cell/colour keys. The
        trader saw "Loading the Watchlist history..." and empty panels on the
        desktop while that downloaded and parsed - over the Cloudflare tunnel
        on a phone it is far worse. The payload is ~10x compressible because
        it is so repetitive, which makes this the cheapest possible fix.

        Compression is skipped for small bodies and whenever the client did
        not offer gzip, so nothing else on this worker changes behaviour.
        """
        body = json.dumps(payload).encode("utf-8")
        encoding = None
        if len(body) >= self.GZIP_MIN_BYTES and "gzip" in (
            self.headers.get("Accept-Encoding") or ""
        ).lower():
            try:
                # Level 5: measured on this payload as ~95% of the size
                # reduction of level 9 for a fraction of the CPU, and this
                # runs on the box that also builds the boards.
                body = gzip.compress(body, compresslevel=5)
                encoding = "gzip"
            except Exception:
                encoding = None  # never fail a response over compression
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        wanted = str(query.get("list", [""])[0]).strip() or None
        # ?dir=bear on any board route serves the BEAR side (spec 2026-09-24).
        direction = _direction(query)
        try:
            if path == "/api/momx-scanner":
                self._json(HTTPStatus.OK, service.snapshot(wanted, direction))
                return
            if path == "/api/momx-scanner/lists":
                self._json(HTTPStatus.OK, service.lists())
                return
            if path == "/api/momx-scanner/live-seeds":
                # The LIVE ⚡ seeds (momx/live_bolt.py) for api_server's
                # per-second layer. Loopback consumer only.
                self._json(HTTPStatus.OK, {"list": wanted, "seeds": service.live_seeds(wanted)})
                return
            if path == "/api/momx-scanner/status":
                self._json(HTTPStatus.OK, service.status())
                return
            if path == "/api/momx-scanner/momentum":
                self._json(HTTPStatus.OK, _momentum(wanted, direction))
                return
            if path == "/api/momx-scanner/momo-alerts":
                self._json(HTTPStatus.OK, _momo_alerts(self._momo_user()))
                return
            if path == "/api/momx-scanner/momo-config":
                self._json(
                    HTTPStatus.OK, momo_alert.load_user_config(self._momo_user())
                )
                return
            if path == "/api/momx-scanner/history":
                # 30-day scan-match archive. Absent day / unknown symbol /
                # empty archive all answer with an EMPTY payload, never an
                # error -- a quiet day is a fact worth seeing. api_server
                # already proxies every /api/momx-scanner* path here, so no
                # api_server change exists for this route.
                # NOT `query` - that name is the momx.query MODULE, imported
                # at the top of this file. Python makes a name assigned
                # anywhere in a function local to the WHOLE function, so this
                # one line turned every `query.query(...)` in the
                # history-query branch below into an unbound local and every
                # search into a 500. The restart script then reported "still
                # answers 404", which was a wrong diagnosis on top of a real
                # bug. Found by an audit, confirmed by symtable and by
                # execution against the real archive.
                history_args = parse_qs(parsed.query)
                self._json(
                    HTTPStatus.OK,
                    history.history_response(
                        wanted or board.DEFAULT_LIST_NAME,
                        date=str(history_args.get("date", [""])[0]).strip() or None,
                        symbol=str(history_args.get("symbol", [""])[0]).strip() or None,
                        directory=service.history_dir(direction),
                        # Rows back from the newest. Junk becomes 0 rather than
                        # an error: a bad offset should show page one, not a
                        # 500 on a tab he only opened to look at history.
                        offset=_int_or_zero(history_args.get("offset", ["0"])[0]),
                    ),
                )
                return
            if path == "/api/momx-scanner/history-query":
                # "Which tickers ever matched this condition" over the 30-day
                # archive. The BROWSER sends the resolved condition - the
                # timeframe, the threshold, the exact colours that count -
                # derived from the same momxFilters.js that paints the live
                # board, so this side never learns what "cyan" means and the
                # two cannot drift apart. See momx/query.py.
                #
                # It cannot be done in the browser: a day file is 8.8-25.5 MB
                # and /history only ever ships the newest 400 of ~11,770
                # snapshots, so a client-side filter would search 3% of the day
                # and present the answer as if it covered all of it.
                query_args = parse_qs(parsed.query)

                def _one(name: str) -> str:
                    return str(query_args.get(name, [""])[0]).strip()

                date = _one("date")
                try:
                    self._json(
                        HTTPStatus.OK,
                        query.query(
                            wanted or board.DEFAULT_LIST_NAME,
                            {
                                "section": _one("section"),
                                "timeframe": _one("timeframe"),
                                "min": _one("min") or None,
                                "bg": _one("bg"),
                                "fg": _one("fg"),
                            },
                            directory=service.history_dir(direction),
                            # No date = every day this board has. One date pins
                            # it, which is what the "this day" scope sends.
                            dates=[date] if date else None,
                            limit=_int_or_zero(query_args.get("limit", ["0"])[0])
                            or query.MAX_RESULTS,
                        ),
                    )
                except query.ConditionError as exc:
                    # 400, not an empty result: "nothing matched" and "I could
                    # not understand you" must never look alike in a search.
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            if path == "/api/momx-scanner/grade-tape":
                # One symbol's 5-minute grade samples for the chart's circles.
                # NOT `query` as the local name - see the /history note above.
                tape_args = parse_qs(parsed.query)
                symbol = str(tape_args.get("symbol", [""])[0]).strip().upper()
                if not symbol:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required"})
                    return
                days = _int_or_zero(tape_args.get("days", ["5"])[0]) or 5
                self._json(
                    HTTPStatus.OK,
                    grade_log.tape_response(
                        service.grade_dir(direction), symbol, days=max(1, min(10, days))
                    ),
                )
                return
            if path == "/api/momx-scanner/grade-record":
                # Track record beside the letter: recorded, else the back-test,
                # else {"source": None} ("track record unavailable").
                self._json(HTTPStatus.OK, grade_log.record_response(service.grade_dir(direction), direction))
                return
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"ok": True})
                return
        except board.UnknownListError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"no route {path}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if path == "/api/momx-scanner/momo-config":
                self._json(
                    HTTPStatus.OK,
                    momo_alert.save_user_config(self._momo_user(), body),
                )
                return
            if path == "/api/momx-scanner/universe":
                self._json(
                    HTTPStatus.OK,
                    service.set_universe(body.get("text"), body.get("list")),
                )
                return
            if path == "/api/momx-scanner/universe/add":
                # APPEND, not replace. The one the trader reaches for daily.
                self._json(
                    HTTPStatus.OK,
                    service.add_universe(body.get("text"), body.get("list")),
                )
                return
            if path == "/api/momx-scanner/universe/remove":
                self._json(
                    HTTPStatus.OK,
                    service.remove_universe(body.get("text"), body.get("list")),
                )
                return
            if path == "/api/momx-scanner/lists/create":
                self._json(
                    HTTPStatus.OK,
                    service.create_list(body.get("name"), body.get("text")),
                )
                return
            if path == "/api/momx-scanner/lists/rename":
                self._json(
                    HTTPStatus.OK,
                    service.rename_list(body.get("list"), body.get("name")),
                )
                return
            if path == "/api/momx-scanner/lists/delete":
                self._json(HTTPStatus.OK, service.delete_list(body.get("list")))
                return
            if path == "/api/momx-scanner/lists/active":
                self._json(HTTPStatus.OK, service.set_active(body.get("list")))
                return
            if path == "/api/momx-scanner/universe/reset":
                # The undo for the Tickers box. Restores ONE list to its
                # default ticker set; api_server proxies it by path prefix,
                # so no route is needed there.
                self._json(
                    HTTPStatus.OK,
                    service.reset_universe(body.get("list")),
                )
                return
            if path == "/api/momx-scanner/momentum/clear":
                self._json(HTTPStatus.OK, _clear_momentum(body.get("list")))
                return
            if path == "/api/momx-scanner/movers/refresh":
                self._json(HTTPStatus.OK, _movers_refresh())
                return
            if path == "/api/momx-scanner/rebuild":
                # Queues the build on the warmer thread and returns at once;
                # the current generatedAt lets the client detect the new one.
                self._json(HTTPStatus.OK, service.request_rebuild(body.get("list")))
                return
        except board.UnknownListError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": f"no route {path}"})


def _deprioritise() -> None:
    """Run below normal priority so the chart engine always wins a core.

    The scanner is background work; the charts are what the trader is looking
    at. On 2026-08-28 the pool took 8 of 12 cores through every build and the
    app went sluggish at the open. Lower priority means the OS hands momx only
    the cycles nothing else wants - the build gets slower under load and
    invisible when the machine is busy, which is exactly the right trade.
    Child pool workers inherit the class, so this covers them too.
    """
    try:
        import ctypes

        # IDLE, not BELOW_NORMAL. api_server AND the gateway already run at
        # BELOW_NORMAL, so a BELOW_NORMAL scanner was merely their EQUAL and its
        # pool children (spawned Normal) actually OUT-ranked them - on
        # 2026-08-28 the first /api/auth/status behind a build took 23s and the
        # app hung at "Loading secure workspace". IDLE guarantees the scanner
        # only ever uses cycles the app does not want, so it can never starve
        # the thing serving the trader. Pool children inherit this class.
        IDLE_PRIORITY_CLASS = 0x00000040
        kernel32 = ctypes.windll.kernel32
        # ctypes defaults every return/arg to 32-bit int. GetCurrentProcess()
        # returns the 64-bit pseudo-handle (0xFFFFFFFFFFFFFFFF); truncated to
        # 0xFFFFFFFF it is an INVALID handle, so SetPriorityClass silently
        # returned 0 (failure, not an exception) and this printed "IDLE" while
        # the process actually stayed NORMAL - the exact starvation the IDLE
        # design prevents (verify agent, 2026-08-28). Declare the types so the
        # real handle survives, and CHECK the return value.
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        ok = kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), IDLE_PRIORITY_CLASS)
        if ok:
            print("priority: IDLE (never contends with api_server/gateway)", flush=True)
        else:
            err = ctypes.get_last_error()
            print(f"priority: FAILED to set IDLE (GetLastError={err})", flush=True)
    except Exception as exc:  # noqa: BLE001 - non-Windows or restricted
        print(f"priority: unchanged ({exc})", flush=True)


def _shutdown_pool_and_exit(signum=None, frame=None):
    """Take the process pool down with us.

    ProcessPoolExecutor children do NOT die when the parent is killed. Every
    restart of this worker on 2026-08-28 stranded 8 pool processes at NORMAL
    priority with no parent - they kept burning CPU and outranked both
    api_server and this worker (which are BelowNormal), and the trader reported
    the app was very slow at the open. Eight of them were found alive at 08:34.

    atexit covers a clean exit; SIGTERM/SIGINT cover the usual kills. A hard
    taskkill /F still cannot be caught, so the watchdog's port check remains
    the backstop - but the common cases are covered here.
    """
    try:
        board.shutdown_pool()
    except Exception:  # noqa: BLE001
        pass
    if signum is not None:
        raise SystemExit(0)


def main() -> int:
    _deprioritise()
    atexit.register(_shutdown_pool_and_exit)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _shutdown_pool_and_exit)
        except Exception:  # noqa: BLE001 - not all signals exist on Windows
            pass
    service.start_warmer()
    _threading.Thread(target=_momo_loop, name="momo-eval", daemon=True).start()
    _threading.Thread(target=_movers_loop, name="movers-refresh", daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"momx worker on http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
