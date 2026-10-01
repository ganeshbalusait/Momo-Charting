"""End-to-end health checklist for the AGX stack: every check MEASURES a number.

WHY THIS MODULE EXISTS
----------------------
Overnight on 2026-08-31 every real fault was INVISIBLE from the app's own
badges, because the badges were FLAGS:

* The chart payload said "Premarket 04:00-07:00 is unavailable - check the
  Tradier token" while all nine scanner symbols HAD the bars (AAPL 148,
  TSLA 167, NVDA 166). The string was written on the fetch path and never
  cleared, so it outlived the condition it described.
* A chart served candles 151 MINUTES old while the broker had 40-second-old
  bars. Nothing said so. It was found by COMPARING served data to the broker.
* A saved grid held a typo'd ticker (GOOGLE) that burned a chart rebuild every
  ~30s for days.
* A credential cache stored "failed" permanently and silently disabled a
  working fallback.

THE RULE THAT FALLS OUT, and the one design constraint of this file: every
check MEASURES AN OUTCOME and reports a NUMBER. A check that can only say "OK"
is a flag, and flags are exactly what failed here. A check that could not run
says ``unknown`` - it never reports green for "I did not look". ``unknown`` is
a first-class status and is NEVER collapsed into pass anywhere in this module.

Stdlib only; the clock, the archive directory and every outside-world call are
injected through :class:`Context`, so tests never touch the real archive, the
real clock, or the live brokers.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

RETENTION_DAYS = 30
DEFAULT_HISTORY_DIR = Path("artifacts") / "preflight"
DEFAULT_ARTIFACTS_DIR = Path("artifacts")

#: A check that has needed the same automatic fix this many days running is
#: MASKING a defect rather than curing one, so it is escalated to warn even
#: when the heal worked. Self-healing that repeats daily is a chronic fault
#: with a quiet cover story.
CHRONIC_HEAL_DAYS = 3

#: ``unknown`` ranks ABOVE pass and BELOW warn on purpose: "I could not look"
#: is worse news than a measured green, and less actionable than a measured
#: warn. Nothing in this module ever maps unknown onto pass.
STATUS_ORDER = {"pass": 0, "unknown": 1, "warn": 2, "fail": 3}


def worst_status(*statuses: str) -> str:
    """The worst of the given statuses; ``unknown`` when nothing is gradeable."""
    seen = [s for s in statuses if s in STATUS_ORDER]
    if not seen:
        return "unknown"
    return max(seen, key=lambda status: STATUS_ORDER[status])


def _result(
    check_id: str,
    label: str,
    status: str,
    measured: str,
    detail: str,
    *,
    healable: bool = False,
    critical: bool = False,
) -> dict:
    """Build one checklist row. An unrecognised status degrades to unknown."""
    if status not in STATUS_ORDER:
        status = "unknown"
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "measured": str(measured),
        "detail": str(detail),
        "healable": bool(healable),
        "critical": bool(critical),
    }


def _is_market_hours(now_et: datetime) -> bool:
    """Weekday 09:30-16:00 ET. Holidays are NOT modelled - on a holiday this
    answers True and the only consequence is that the run is more conservative
    about healing, which is the harmless direction to be wrong in."""
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


#: How long BEFORE the open a DISRUPTIVE fix stops being allowed to start.
#: The 09:30 bell is the wrong line to draw: a MomX rebuild kicked off at 09:29
#: takes 8 of 12 cores and is still eating them at 09:31, which is exactly the
#: harm the market-hours rule was written to prevent.
HEAL_PREOPEN_BUFFER_MINUTES = 15


def _is_heal_deferred(now_et: datetime) -> bool:
    """True when a NOT-market-hours-safe fix must be REPORTED, not run.

    Deliberately a separate function from _is_market_hours: the summary's
    ``marketHours`` field must keep saying what the clock actually says, while
    the heal gate closes HEAL_PREOPEN_BUFFER_MINUTES early.
    """
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30 - HEAL_PREOPEN_BUFFER_MINUTES) <= minutes < (16 * 60)


# ----------------------------------------------------------------------
# Outside-world seams
#
# Every network / disk / broker call this module makes goes through one of
# these functions, and every one is overridable on the Context. That is what
# lets tests drive all fourteen checks with no server, no broker and no clock.
# ----------------------------------------------------------------------


def http_get(url: str, timeout: float = 20.0, headers: dict | None = None) -> dict:
    """GET a URL. Returns {ok, status, seconds, body, json, error}. Never raises.

    Never raising is the point: a probe that explodes takes the whole run down,
    and a run that does not finish tells the trader nothing at 09:25.
    """
    started = time.monotonic()
    request = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:  # it answered, just not with 200
        body = b""
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            pass
        return {
            "ok": False,
            "status": int(exc.code),
            "seconds": time.monotonic() - started,
            "body": body.decode("utf-8", errors="replace")[:2000],
            "json": None,
            "error": "HTTP %d" % int(exc.code),
        }
    except Exception as exc:  # noqa: BLE001 - refused / DNS / timeout
        return {
            "ok": False,
            "status": 0,
            "seconds": time.monotonic() - started,
            "body": "",
            "json": None,
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    elapsed = time.monotonic() - started
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return {
        "ok": 200 <= status < 400,
        "status": status,
        "seconds": elapsed,
        "body": "" if parsed is not None else text[:2000],
        "json": parsed,
        "error": "" if 200 <= status < 400 else "HTTP %d" % status,
    }


def http_post_json(url: str, payload: dict, timeout: float = 20.0) -> dict:
    body = json.dumps(payload or {}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "ok": True,
                "status": int(getattr(response, "status", 0) or 0),
                "seconds": time.monotonic() - started,
                "body": response.read().decode("utf-8", errors="replace")[:2000],
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "seconds": time.monotonic() - started,
            "body": "",
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def schwab_quotes(symbols) -> dict:
    """Live quotes straight from Schwab. {} when the round-trip did not happen."""
    wanted = [str(s).strip().upper() for s in symbols if str(s or "").strip()]
    if not wanted:
        return {}
    try:
        from data.schwab_client import SchwabClient  # local import: pulls pandas
    except Exception:  # noqa: BLE001
        return {}
    try:
        quotes = SchwabClient().get_quotes(wanted)
    except Exception:  # noqa: BLE001
        return {}
    return quotes if isinstance(quotes, dict) else {}


def schwab_newest_bar_epoch(symbol: str) -> float | None:
    """Epoch seconds of the BROKER's newest 1-minute bar, or None.

    This is the independent yardstick chart_freshness needs. Comparing the
    served tape against the WALL CLOCK cannot work - it screams every night and
    every weekend for no reason. Comparing it against the broker's own newest
    bar is self-normalising: when the market is shut both sides sit on the same
    last bar and the measured lag is zero.
    """
    try:
        from data.schwab_client import SchwabClient  # local import: pulls pandas
    except Exception:  # noqa: BLE001
        return None
    try:
        client = SchwabClient()
        frame = client.get_chart_bars(str(symbol).upper(), timeframe="1Min", days_back=1)
        if frame is None or getattr(frame, "empty", True):
            return None
        return float(frame["timestamp"].max().timestamp())
    except Exception:  # noqa: BLE001
        return None


def tradier_probe() -> dict:
    """One read-only Tradier quote. {ok, status, message, configured}."""
    try:
        from config import settings

        token = str(getattr(settings.tradier, "access_token", "") or "").strip()
        base = str(getattr(settings.tradier, "base_url", "") or "").rstrip("/")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "message": "config unreadable: %s" % exc,
                "configured": False}
    if not token or not base:
        return {"ok": False, "status": 0, "message": "no access token configured",
                "configured": False}
    outcome = http_get(
        base + "/markets/quotes?symbols=AAPL",
        timeout=12.0,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    return {
        "ok": bool(outcome["ok"]),
        "status": int(outcome["status"]),
        "message": str(outcome.get("body") or outcome.get("error") or "")[:300],
        "configured": True,
    }


def alpaca_probe(key: str, secret: str) -> dict:
    """Does this Alpaca pair actually authenticate? {ok, status, message}."""
    if not str(key or "").strip() or not str(secret or "").strip():
        return {"ok": False, "status": 0, "message": "not set", "present": False}
    outcome = http_get(
        "https://data.alpaca.markets/v2/stocks/AAPL/bars/latest?feed=iex",
        timeout=12.0,
        headers={"APCA-API-KEY-ID": str(key), "APCA-API-SECRET-KEY": str(secret)},
    )
    return {
        "ok": bool(outcome["ok"]),
        "status": int(outcome["status"]),
        "message": str(outcome.get("error") or "")[:200],
        "present": True,
    }


def alpaca_env_credentials() -> tuple[str, str]:
    return (
        str(os.environ.get("ALPACA_KEY_ID") or "").strip(),
        str(os.environ.get("ALPACA_SECRET_KEY") or "").strip(),
    )


def alpaca_vault_credentials() -> tuple[str, str]:
    """The key the app ACTUALLY uses: the owner's Settings-page Alpaca key.

    Mirrors DashboardState._resolve_owner_alpaca_client (first active account,
    admins first) on purpose - a preflight that probed a DIFFERENT credential
    than the app uses would be measuring the wrong thing and calling it green.
    """
    try:
        import sqlite3

        from auth_service import AuthService
        from config import DATABASE_PATH

        connection = sqlite3.connect(str(DATABASE_PATH), timeout=10.0)
        try:
            row = connection.execute(
                "SELECT id FROM app_users WHERE is_active = 1 "
                "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return "", ""
        stored = AuthService().get_provider_credentials(str(row[0]), "alpaca_market_data")
        return (
            str(stored.get("key_id") or "").strip(),
            str(stored.get("secret_key") or "").strip(),
        )
    except Exception:  # noqa: BLE001
        return "", ""


#: Google Drive folder names, copied from scripts/backup_agx.py. Resolved
#: READ-ONLY here on purpose: that script MKDIRS its destination, and a health
#: check must never create the very directory whose absence is the finding.
_DRIVE_FOLDERS = ("My Drive", "Google Drive", "GoogleDrive")
LOCAL_ONLY_BACKUP_DIR = Path("C:/AGX-Backups-LOCAL-ONLY/AGX-Backups")


def backup_directory() -> Path | None:
    home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    candidates = [home / name for name in _DRIVE_FOLDERS]
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.append(Path(letter + ":/") / "My Drive")
    for base in candidates:
        try:
            target = base / "AGX-Backups"
            if target.exists():
                return target
        except OSError:
            continue
    try:
        if LOCAL_ONLY_BACKUP_DIR.exists():
            return LOCAL_ONLY_BACKUP_DIR
    except OSError:
        pass
    return None


def running_api_server_state():
    """``api_server.STATE`` if this process IS the api_server, else None.

    ``sys.modules.get`` and never ``import api_server``: importing that module
    boots every scheduler and opens the 1GB live journal (see
    tests/conftest.py). A health check must be free to run from anywhere.
    """
    module = sys.modules.get("api_server")
    return getattr(module, "STATE", None) if module is not None else None


def premarket_symbols() -> tuple:
    """The nine names the premarket scanner watches, read from its own list."""
    try:
        from premarket_scanner import PREMARKET_SCAN_SYMBOLS

        return tuple(PREMARKET_SCAN_SYMBOLS)
    except Exception:  # noqa: BLE001
        return ("AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX")


# ----------------------------------------------------------------------
# Context
# ----------------------------------------------------------------------


@dataclass
class Context:
    """Everything a check is allowed to touch. Fully overridable in tests."""

    now_et: datetime
    api_base: str = "http://127.0.0.1:3002"
    momx_base: str = "http://127.0.0.1:3010"
    gateway_base: str = "http://127.0.0.1:3001"
    dev_frontend: str = "http://127.0.0.1:5173"
    preview_frontend: str = "http://127.0.0.1:4173"
    artifacts_dir: Path = DEFAULT_ARTIFACTS_DIR
    history_dir: Path = DEFAULT_HISTORY_DIR
    #: Kept to three: this check must not become the load it is measuring.
    freshness_symbols: tuple = ()
    chart_open_symbols: tuple = ()
    latency_samples: int = 8
    momx_board: str = "Mag7"
    http_get: Callable = http_get
    http_post_json: Callable = http_post_json
    schwab_quotes: Callable = schwab_quotes
    schwab_newest_bar_epoch: Callable = schwab_newest_bar_epoch
    tradier_probe: Callable = tradier_probe
    alpaca_probe: Callable = alpaca_probe
    alpaca_env_credentials: Callable = alpaca_env_credentials
    alpaca_vault_credentials: Callable = alpaca_vault_credentials
    backup_directory: Callable = backup_directory
    api_server_state: Callable = running_api_server_state
    premarket_symbols: Callable = premarket_symbols
    #: Per-run scratch: shared latency samples, the chart payloads the three
    #: chart checks would otherwise fetch twice, and the symbols a heal needs.
    scratch: dict = field(default_factory=dict)
    _started: float = field(default_factory=time.monotonic, repr=False)

    # -- small shared helpers -------------------------------------------

    def now(self) -> datetime:
        """``now_et`` advanced by however long this run has been going.

        An age must be measured against the moment of the PROBE, not the moment
        the run started. A full run takes ~15-30s, and on the first live run
        that made the pre-warmer's last cycle read "-2s ago" - a nonsense
        number, which quietly erodes trust in every other number on the page.
        Still injectable: with a frozen ``now_et`` the elapsed part is
        microseconds, so tests stay exact.
        """
        return self.now_et + timedelta(seconds=max(0.0, time.monotonic() - self._started))

    def chart_url(self, symbol: str, **params) -> str:
        query = {"symbol": str(symbol).upper(), "initial": "1"}
        query.update({key: str(value) for key, value in params.items()})
        return self.api_base + "/api/oi-finder-chart?" + urllib.parse.urlencode(query)

    def fetch_chart(self, symbol: str, *, fresh: bool = False) -> dict:
        """The chart payload as the BROWSER would receive it, timed.

        Cached per run so chart_freshness / premarket_window / chart_open_speed
        cost one fetch per symbol between them, not three. ``fresh=True``
        bypasses the cache - chart_open_speed must measure a real request.
        """
        cache = self.scratch.setdefault("charts", {})
        key = str(symbol).upper()
        if not fresh and key in cache:
            return cache[key]
        outcome = self.http_get(self.chart_url(key), timeout=90.0)
        cache[key] = outcome
        return outcome

    def board_symbols(self) -> list:
        """Panel symbols from the trader's SAVED grids, most recent layout first.

        chart_open_speed and chart_freshness pick from here so they measure the
        click-a-row case on the board he is actually looking at, not a symbol
        chosen by this file.
        """
        document = self.load_chart_grids()
        ordered: list = []
        layouts = []
        for email, grids in (document.get("users") or {}).items():
            if not isinstance(grids, dict):
                continue
            for name, grid in grids.items():
                if isinstance(grid, dict):
                    layouts.append((str(grid.get("savedAt") or ""), email, name, grid))
        layouts.sort(key=lambda item: item[0], reverse=True)
        for _saved, _email, _name, grid in layouts:
            workspace = grid.get("workspace") if isinstance(grid.get("workspace"), dict) else {}
            for panel in (workspace.get("panels") or []):
                if not isinstance(panel, dict):
                    continue
                symbol = str(panel.get("symbol") or "").strip().upper()
                if symbol and symbol not in ordered:
                    ordered.append(symbol)
        return ordered

    def broker_known(self, symbols) -> tuple:
        """(the subset the broker recognises, whether it could be resolved).

        One batched quote call per run, cached. Exists because a typo'd ticker
        on the board (GOOGLE) made the chart-OPEN-SPEED row fail on the live
        run: two different findings landing on the wrong one of them. The
        typo belongs to invalid_grid_symbols and nowhere else, so the chart
        checks filter it out here.

        When the broker does not answer at all, this returns everything as
        "known" and resolved=False. Silently DROPPING a symbol because a quote
        call failed would quietly shrink what gets measured, which is the
        failure mode this whole module is against.
        """
        wanted = [str(s).strip().upper() for s in symbols if str(s or "").strip()]
        cache = self.scratch.setdefault("broker_known", {})
        missing = [s for s in wanted if s not in cache]
        if missing:
            quotes = self.schwab_quotes(missing) or {}
            if not quotes:
                return set(wanted), False
            for symbol in missing:
                cache[symbol] = symbol in quotes
        return {s for s in wanted if cache.get(s, True)}, True

    def load_chart_grids(self) -> dict:
        path = Path(self.artifacts_dir) / "chart_grids.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return document if isinstance(document, dict) else {}


def _age_seconds(now_et: datetime, stamp) -> float | None:
    """Seconds between an ISO stamp (any offset, or naive-as-ET) and now."""
    text = str(stamp or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return (now_et - parsed).total_seconds()


def _minutes(seconds: float) -> str:
    return "%.1f min" % (float(seconds) / 60.0)


def _bar_times(payload) -> list:
    bars = (payload or {}).get("bars") if isinstance(payload, dict) else None
    if not isinstance(bars, list):
        return []
    times = []
    for bar in bars:
        if isinstance(bar, dict) and bar.get("time") is not None:
            try:
                times.append(float(bar["time"]))
            except (TypeError, ValueError):
                continue
    return times


# ----------------------------------------------------------------------
# The checks. Each one MEASURES; none of them reads a status flag.
# ----------------------------------------------------------------------

#: Below this a served chart is treated as keeping up with the broker.
CHART_LAG_WARN_MINUTES = 3.0
CHART_LAG_FAIL_MINUTES = 10.0
#: The 04:00-07:00 window is 180 minutes; a symbol with fewer than this many
#: bars had a real hole even though it was not empty.
PREMARKET_MIN_BARS_WARN = 30
#: Set BY THE TRADER: "more than 15 seconds is a fail".
CHART_OPEN_FAIL_SECONDS = 15.0
CHART_OPEN_WARN_SECONDS = 5.0
LATENCY_MEDIAN_WARN_SECONDS = 0.5
LATENCY_MEDIAN_FAIL_SECONDS = 2.0
#: A max this far above a healthy median is the CPU-saturation signature that
#: made charts crawl all week (measured 2026-09-01 08:31: median 0.06s, max
#: 7.73s). A median-only check called that green, which is why 14 exists.
LATENCY_MAX_WARN_SECONDS = 2.0
LATENCY_MAX_FAIL_SECONDS = 5.0
PREWARM_CYCLE_FAIL_SECONDS = 600.0
MOMX_BOARD_WARN_SECONDS = 300.0
MOMX_BOARD_FAIL_SECONDS = 900.0
MOMX_TAPE_WARN_SECONDS = 45 * 60.0
BACKUP_WARN_HOURS = 30.0
BACKUP_FAIL_HOURS = 60.0


def check_processes_endpoints(ctx: Context) -> dict:
    """1. Which of the five listening services actually answered, and how fast."""
    targets = [
        ("api_server :3002", ctx.api_base + "/api/health", True),
        ("momx :3010", ctx.momx_base + "/healthz", True),
        ("gateway :3001", ctx.gateway_base + "/api/auth/status", True),
        ("vite :5173", ctx.dev_frontend + "/", False),
        ("preview :4173", ctx.preview_frontend + "/", False),
    ]
    answered, missing, required_missing, frontends_missing = [], [], [], []
    for name, url, required in targets:
        outcome = ctx.http_get(url, timeout=15.0)
        if outcome.get("ok"):
            answered.append("%s %.2fs" % (name, float(outcome.get("seconds") or 0.0)))
        else:
            missing.append("%s (%s)" % (name, outcome.get("error") or "no answer"))
            (required_missing if required else frontends_missing).append(name)
    measured = "%d/%d answered - %s" % (len(answered), len(targets), ", ".join(answered) or "none")
    if missing:
        measured += "; NO ANSWER: " + ", ".join(missing)
    if required_missing:
        status = "fail"
        detail = (
            "These are down: " + ", ".join(required_missing) + ". Nothing here restarts a "
            "process automatically. Run Start-ScheduledTask 'AgenticAI-Trading-24x7' "
            "(backend + gateway) and check the momx worker; then re-run this checklist."
        )
    elif len(frontends_missing) >= 2:
        status = "fail"
        detail = (
            "Both web front ends are down, so the app cannot be opened in a browser at "
            "all. Start the dev server (npm run dev in frontend) or the preview server."
        )
    elif frontends_missing:
        status = "warn"
        detail = (
            ", ".join(frontends_missing) + " is not answering. The app still opens on the "
            "other address, so this is not urgent - but :5173 is the one you normally use."
        )
    else:
        status = "pass"
        detail = "All five services answered."
    return _result("processes_endpoints", "Processes and endpoints", status, measured,
                   detail, healable=False, critical=True)


#: Chart probes per run. Three fresh chart opens is already several seconds of
#: real backend work; this check must not become the load it measures.
CHART_SAMPLE_SIZE = 3


def _board_sample(ctx: Context, board: list, known: set) -> tuple:
    """(symbols to probe, a sentence stating the SCOPE that was sampled).

    The cap is a cost decision, but an UNDISCLOSED sample is the same defect
    this module exists to kill: "worst 0.0 min behind the broker" reads as a
    statement about the board when it is a statement about three of twelve.
    So the scope is printed as part of the number, and the window ROTATES by
    day-of-year - the off-hot-set tape freeze was per-symbol (SMCI and MSTR
    frozen while every other chart ticked), and a sample pinned to the first
    three names would have read PASS straight through it every morning.
    """
    candidates = [s for s in board if s in known] or list(board)
    total = len(candidates)
    if total <= CHART_SAMPLE_SIZE:
        return list(candidates), ""
    slots = (total + CHART_SAMPLE_SIZE - 1) // CHART_SAMPLE_SIZE
    slot = ctx.now_et.timetuple().tm_yday % slots
    start = slot * CHART_SAMPLE_SIZE
    picked = candidates[start:start + CHART_SAMPLE_SIZE]
    if len(picked) < CHART_SAMPLE_SIZE:
        picked = picked + candidates[: CHART_SAMPLE_SIZE - len(picked)]
    note = ("sampled %d of %d board symbols (group %d of %d, rotating daily so the whole "
            "board is covered every %d run-days)" % (len(picked), total, slot + 1, slots, slots))
    return picked, note


def check_chart_freshness(ctx: Context) -> dict:
    """2. Served newest bar vs the BROKER's newest bar, in minutes."""
    board = list(ctx.freshness_symbols) or ctx.board_symbols() or ["AAPL", "NVDA", "TSLA"]
    known, _resolved = ctx.broker_known(board)
    # Same reason as chart_open_speed: a typo'd ticker has no candles anywhere,
    # and reporting it here would bury a real staleness number under a finding
    # that belongs to the saved-layouts row.
    symbols, scope = _board_sample(ctx, board, known)
    if not symbols:
        return _result(
            "chart_freshness", "Chart candles vs the broker", "unknown",
            "no chart symbol was available to probe",
            "Nothing was measured, so candle freshness is unknown - not a pass.",
            healable=True, critical=True)
    parts, lags, stale = [], [], []
    blank, no_answer, broker_silent = [], [], []
    for symbol in symbols:
        outcome = ctx.fetch_chart(symbol)
        payload = outcome.get("json")
        if not outcome.get("ok") or not isinstance(payload, dict):
            no_answer.append("%s (%s)" % (symbol, outcome.get("error") or "no answer"))
            continue
        served = _bar_times(payload)
        if not served:
            blank.append(symbol)
            continue
        broker = ctx.schwab_newest_bar_epoch(symbol)
        if broker is None:
            broker_silent.append(symbol)
            continue
        # Clamp at zero: the served tape is streamer-fed and is routinely a few
        # seconds AHEAD of the broker's price-history endpoint. Negative lag is
        # not a finding.
        lag = max(0.0, (float(broker) - max(served)) / 60.0)
        lags.append(lag)
        parts.append("%s %.1f min" % (symbol, lag))
        if lag >= CHART_LAG_WARN_MINUTES:
            stale.append(symbol)
    # A blank chart needs the same re-pull a stale one does, so the healer gets
    # both - measured-stale first, then blank.
    ctx.scratch["stale_chart_symbols"] = stale + [s for s in blank if s not in stale]

    bits = []
    if lags:
        bits.append("worst %.1f min behind the broker - %s" % (max(lags), ", ".join(parts)))
    if blank:
        bits.append("%s served NO candles at all" % ", ".join(blank))
    if no_answer:
        bits.append("the chart request failed for " + "; ".join(no_answer))
    if broker_silent:
        bits.append("%s could not be compared - the broker stayed silent"
                    % ", ".join(broker_silent))
    measured = "; ".join(bits) if bits else "nothing could be measured"
    if scope:
        measured += "; " + scope

    lag_status, detail = "pass", ""
    if lags:
        worst = max(lags)
        if worst >= CHART_LAG_FAIL_MINUTES:
            lag_status = "fail"
            detail = ("The chart is showing candles %.0f minutes older than the broker's "
                      "own. Re-open the affected chart; if it stays behind, the tape "
                      "needs a refresh from the broker. " % worst)
        elif worst >= CHART_LAG_WARN_MINUTES:
            lag_status = "warn"
            detail = ("Candles are %.1f minutes behind the broker. Not wrong yet, but "
                      "drifting. " % worst)
    # The symbols that could NOT be compared are GRADED, not just narrated. A
    # chart that serves nothing is a strictly worse fault than one a few minutes
    # behind, and a symbol the broker would not answer for is UNKNOWN, never a
    # pass. Appending them to the measured string and then grading only `worst`
    # is how this row read PASS while its own text named a blank chart.
    status = worst_status(
        lag_status,
        "fail" if (blank or no_answer) else "pass",
        "unknown" if broker_silent else "pass",
    )
    if blank:
        detail += ("%s opened with no candles at all - that chart is BLANK on screen, "
                   "which is worse than a stale one. " % ", ".join(blank))
    if no_answer:
        detail += ("The chart request itself failed for %s - see the processes row. "
                   % "; ".join(no_answer))
    if broker_silent:
        detail += ("%s could not be compared because the broker stayed silent, so their "
                   "freshness is UNKNOWN rather than good - check the Schwab row. "
                   % ", ".join(broker_silent))
    if not detail:
        detail = "Charts are level with the broker."
    return _result("chart_freshness", "Chart candles vs the broker", status, measured,
                   detail.strip(), healable=True, critical=True)


def check_premarket_window(ctx: Context) -> dict:
    """3. Do all nine scanner names have TODAY's 04:00-07:00 ET bars?"""
    now = ctx.now_et
    if now.weekday() >= 5:
        return _result(
            "premarket_window", "Premarket 04:00-07:00 bars", "unknown",
            "not measured - %s is a weekend" % now.strftime("%A"),
            "There is no premarket session on a weekend, so there is nothing to measure. "
            "This is not a pass and not a failure.", healable=True)
    if now.hour < 7:
        return _result(
            "premarket_window", "Premarket 04:00-07:00 bars", "unknown",
            "not measured - it is %s ET and the window is not over yet" % now.strftime("%H:%M"),
            "Before 07:00 ET a missing bar means nothing: the window is still filling. "
            "Re-run after 07:00 ET for a real answer.", healable=True)
    symbols = list(ctx.premarket_symbols())
    day = now.date()
    counts, empty, thin, unknowns = {}, [], [], []
    for symbol in symbols:
        outcome = ctx.fetch_chart(symbol)
        payload = outcome.get("json")
        if not isinstance(payload, dict):
            unknowns.append(symbol)
            continue
        total = 0
        for epoch in _bar_times(payload):
            moment = datetime.fromtimestamp(epoch, EASTERN)
            if moment.date() == day and 4 <= moment.hour < 7:
                total += 1
        counts[symbol] = total
        if total == 0:
            empty.append(symbol)
        elif total < PREMARKET_MIN_BARS_WARN:
            thin.append("%s %d" % (symbol, total))
    ctx.scratch["premarket_missing_symbols"] = empty
    if not counts:
        return _result(
            "premarket_window", "Premarket 04:00-07:00 bars", "unknown",
            "no symbol answered (%d asked)" % len(symbols),
            "The backend did not return candles for any scanner symbol, so premarket "
            "coverage is unknown - not green.", healable=True)
    thinnest = min(counts.items(), key=lambda item: item[1])
    measured = "%d/%d symbols have 04:00-07:00 bars; thinnest %s %d, richest %d" % (
        len(counts) - len(empty), len(symbols), thinnest[0], thinnest[1],
        max(counts.values()),
    )
    if unknowns:
        measured += "; no answer for " + ", ".join(unknowns)
    if empty:
        status, detail = "fail", (
            "No early-morning candles at all for " + ", ".join(empty) + ". The premarket "
            "scanner cannot see those names this morning. The automatic fix asks the "
            "server to re-pull them from the backup feed.")
    elif thin:
        status, detail = "warn", (
            "Thin coverage (" + ", ".join(thin) + " bars out of a possible 180). There are "
            "gaps in the early session for those names.")
    elif unknowns:
        status, detail = "warn", (
            "Some symbols could not be read: " + ", ".join(unknowns))
    else:
        status, detail = "pass", "Every scanner symbol has early-morning candles."
    return _result("premarket_window", "Premarket 04:00-07:00 bars", status, measured,
                   detail, healable=True)


def check_schwab_token(ctx: Context) -> dict:
    """4. A real Schwab quote round-trips (this is what silently dies weekly)."""
    wanted = ("AAPL", "SPY")
    quotes = ctx.schwab_quotes(wanted) or {}
    got = [symbol for symbol in wanted if isinstance(quotes.get(symbol), dict)]
    if not got:
        return _result(
            "schwab_token", "Schwab connection", "fail",
            "0/%d quotes round-tripped" % len(wanted),
            "The Schwab connection is not returning prices. Its login expires about once "
            "a week and charts quietly stop ticking when it does. Re-connect Schwab from "
            "Settings; nothing here can write that credential for you.",
            healable=False, critical=True)
    sample = quotes.get(got[0]) or {}
    price = sample.get("last_price") or sample.get("lastPrice")
    measured = "%d/%d quotes round-tripped - %s %s" % (
        len(got), len(wanted), got[0], price if price is not None else "no price field")
    if len(got) < len(wanted):
        return _result(
            "schwab_token", "Schwab connection", "warn", measured,
            "Schwab answered but not for every symbol asked. Worth a second look if "
            "charts feel patchy.", healable=False, critical=True)
    return _result("schwab_token", "Schwab connection", "pass", measured,
                   "Schwab is returning live prices.", healable=False, critical=True)


def check_alpaca_credentials(ctx: Context) -> dict:
    """5. WHICH Alpaca key authenticates - the .env one or the Settings one."""
    env_key, env_secret = ctx.alpaca_env_credentials()
    vault_key, vault_secret = ctx.alpaca_vault_credentials()
    env = ctx.alpaca_probe(env_key, env_secret)
    vault = ctx.alpaca_probe(vault_key, vault_secret)

    def describe(name: str, key: str, probe: dict) -> str:
        if not key:
            return "%s: not set" % name
        head = key[:6] + "..."
        if probe.get("ok"):
            return "%s %s authenticates (HTTP %d)" % (name, head, int(probe.get("status") or 200))
        return "%s %s rejected (%s)" % (name, head, probe.get("error") or probe.get("message")
                                        or ("HTTP %d" % int(probe.get("status") or 0)))

    measured = "; ".join([
        describe("Settings key", vault_key, vault),
        describe(".env key", env_key, env),
    ])
    # MEASURE the in-process credential cache too. The incident this row exists
    # for was a WORKING key with the backend remembering a permanent failure for
    # it; probing the key over HTTP alone reports green in the middle of exactly
    # that outage, and the healer written to clear it could never fire.
    poisoned_seconds = None
    state = ctx.api_server_state()
    if state is not None:
        if getattr(state, "_owner_alpaca_client_cache", None) is False:
            stamp = float(getattr(state, "_owner_alpaca_client_cache_at", 0.0) or 0.0)
            poisoned_seconds = max(0.0, time.monotonic() - stamp)
            measured += ("; the backend is holding a remembered FAILURE for this key, set "
                         "%s ago" % _minutes(poisoned_seconds))
        else:
            measured += "; the backend holds no remembered failure"
    reachable = any(int(p.get("status") or 0) > 0 for p in (env, vault))
    if not reachable and not (vault.get("ok") or env.get("ok")):
        if not env_key and not vault_key:
            return _result(
                "alpaca_credentials", "Alpaca market-data key", "fail", measured,
                "No Alpaca key is stored anywhere. The 04:00-07:00 premarket backfill "
                "runs on Alpaca, so that window will be empty. Save a key under Settings "
                "-> API credentials.", healable=True, critical=True)
        return _result(
            "alpaca_credentials", "Alpaca market-data key", "unknown", measured,
            "Alpaca could not be reached at all, so which key works is unknown - not "
            "green. Check this machine's internet connection.",
            healable=True, critical=True)
    if vault.get("ok") and poisoned_seconds is not None:
        status = "warn"
        detail = ("The saved Alpaca key AUTHENTICATES, but the backend is still "
                  "remembering a failure for it (%s old) and is not using it. That is the "
                  "exact silent outage that switched the premarket fallback off while a "
                  "working key sat in Settings. The automatic fix clears that memory."
                  % _minutes(poisoned_seconds))
    elif vault.get("ok"):
        status = "pass"
        detail = ("The key the app actually uses (the one saved in Settings) works. The "
                  ".env key is dead and is not used - that is expected.")
    elif env.get("ok"):
        status = "warn"
        detail = ("Only the .env key works; the Settings key - which is the one the charts "
                  "and the premarket backfill read - does not. Re-save the working key "
                  "under Settings -> API credentials.")
    else:
        status = "fail"
        detail = ("No Alpaca key authenticates. The 04:00-07:00 premarket window and the "
                  "overnight candles both run on Alpaca and will be empty. Save a working "
                  "key under Settings -> API credentials.")
    return _result("alpaca_credentials", "Alpaca market-data key", status, measured,
                   detail, healable=True, critical=True)


#: Tradier's account is unfunded and the API was revoked. This is a KNOWN,
#: ACCEPTED state, not a daily emergency - a checklist that cries wolf every
#: morning is a checklist nobody reads. Capped at warn by construction.
_TRADIER_REVOKED_MARKERS = (
    "access_token_not_approved", "keymanagement", "not approved", "unfunded",
)


def check_tradier(ctx: Context) -> dict:
    """6. Probe Tradier - but a revoked/unfunded account is WARN, never FAIL."""
    probe = ctx.tradier_probe() or {}
    status_code = int(probe.get("status") or 0)
    message = str(probe.get("message") or "")
    if probe.get("ok"):
        return _result(
            "tradier", "Tradier (premarket primary)", "pass",
            "quote round-tripped (HTTP %d)" % (status_code or 200),
            "Tradier is answering again. The premarket window can use it as primary.")
    if not probe.get("configured"):
        return _result(
            "tradier", "Tradier (premarket primary)", "warn",
            "no token configured",
            "Expected. Tradier has no token here and the Alpaca backup covers the "
            "04:00-07:00 window. Nothing to do.")
    lowered = message.lower()
    revoked = status_code in (401, 403) and any(m in lowered for m in _TRADIER_REVOKED_MARKERS)
    if revoked or status_code in (401, 403):
        return _result(
            "tradier", "Tradier (premarket primary)", "warn",
            "HTTP %d, account access revoked" % status_code,
            "EXPECTED - the Alpaca fallback covers this window. Tradier revoked API "
            "access because the account is unfunded. Nothing is broken; ignore this row "
            "unless you decide to fund Tradier again.")
    return _result(
        "tradier", "Tradier (premarket primary)", "warn",
        "HTTP %s - %s" % (status_code or "no answer", message[:120] or "no detail"),
        "Tradier is not answering, which is expected here. The Alpaca fallback covers "
        "the 04:00-07:00 window, so this never counts as a failure.")


def check_momx_scanner(ctx: Context) -> dict:
    """7. MomX board age, tape age, and how many tickers actually have a price."""
    url = ctx.momx_base + "/api/momx-scanner?" + urllib.parse.urlencode({"list": ctx.momx_board})
    outcome = ctx.http_get(url, timeout=30.0)
    payload = outcome.get("json")
    if not isinstance(payload, dict):
        return _result(
            "momx_scanner", "MomX scanner board", "unknown",
            "the worker did not return a board (%s)" % (outcome.get("error") or "no JSON"),
            "The scanner worker on :3010 did not answer, so its freshness is unknown - "
            "not green. See the processes row above.", healable=True)
    measured_at = ctx.now()
    board_age = _age_seconds(measured_at, payload.get("generatedAt"))
    tape_age = _age_seconds(measured_at, payload.get("tapeAsOf"))
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    priced = [r for r in rows if isinstance(r, dict) and isinstance(r.get("last"), (int, float))]
    universe = int(payload.get("universeCount") or len(rows) or 0)
    pending = max(0, universe - len(priced))
    errors = payload.get("errors") if isinstance(payload.get("errors"), dict) else {}
    measured = "board %s old, tape %s old, %d/%d tickers priced" % (
        _minutes(board_age) if board_age is not None else "age unknown",
        _minutes(tape_age) if tape_age is not None else "age unknown",
        len(priced), universe or len(rows),
    )
    if errors:
        measured += ", %d symbol errors" % len(errors)
    ctx.scratch["momx_board_age"] = board_age
    if board_age is None:
        return _result(
            "momx_scanner", "MomX scanner board", "unknown", measured,
            "The board carries no build time, so its age cannot be measured.", healable=True)
    if board_age >= MOMX_BOARD_FAIL_SECONDS or not priced:
        status, detail = "fail", (
            "The scanner board is %s old and %d of %d tickers have no price - it has "
            "stopped rebuilding. Anything it shows now is history."
            % (_minutes(board_age), pending, universe or len(rows)))
    elif board_age >= MOMX_BOARD_WARN_SECONDS or pending:
        status, detail = "warn", (
            "The board is running behind (%s old, %d ticker(s) still without a price)."
            % (_minutes(board_age), pending))
    elif (tape_age is not None and tape_age >= MOMX_TAPE_WARN_SECONDS
          and _is_market_hours(ctx.now_et)):
        # Tape age is only graded during market hours. Outside them it grows
        # without bound and grading it would fail the checklist every night.
        status, detail = "warn", (
            "The board is rebuilding, but the price tape underneath it is %s old during "
            "market hours." % _minutes(tape_age))
    else:
        status, detail = "pass", "The scanner is rebuilding on schedule with every ticker priced."
    return _result("momx_scanner", "MomX scanner board", status, measured, detail, healable=True)


def check_chart_prewarm(ctx: Context) -> dict:
    """8. Prewarm counters must RECONCILE - warm+behind+cold+skipped = candidates."""
    outcome = ctx.http_get(ctx.api_base + "/api/health", timeout=20.0)
    payload = outcome.get("json")
    prewarm = payload.get("chartPrewarm") if isinstance(payload, dict) else None
    if not isinstance(prewarm, dict) or not prewarm:
        return _result(
            "chart_prewarm", "Chart pre-warmer", "unknown",
            "the backend reported no pre-warm figures",
            "The backend did not report on the chart pre-warmer, so whether charts are "
            "being kept warm is unknown - not green.")
    candidates = int(prewarm.get("candidates") or 0)
    warm = int(prewarm.get("warm") or 0)
    behind = int(prewarm.get("behind") or 0)
    cold = int(prewarm.get("cold") or 0)
    skipped = int(prewarm.get("skipped") or 0)
    total = warm + behind + cold + skipped
    cycle_age = _age_seconds(ctx.now(), prewarm.get("lastCycleAt"))
    if cycle_age is not None:
        # Sub-second negatives are clock skew between this probe and the
        # backend stamping its cycle, not a fault. Anything larger is left
        # visible on purpose - a cycle stamped in the FUTURE is worth seeing.
        cycle_age = cycle_age if cycle_age < -2.0 else max(0.0, cycle_age)
    measured = "%d candidates = %d warm + %d behind + %d cold + %d skipped (%s); last cycle %s" % (
        candidates, warm, behind, cold, skipped,
        "reconciles" if total == candidates else "DOES NOT reconcile, %d unaccounted" % (candidates - total),
        ("%.0fs ago" % cycle_age) if cycle_age is not None else "time unknown",
    )
    if cycle_age is None:
        return _result("chart_prewarm", "Chart pre-warmer", "unknown", measured,
                       "The pre-warmer never stamped a cycle time, so it cannot be told "
                       "apart from stopped.")
    if cycle_age >= PREWARM_CYCLE_FAIL_SECONDS or not prewarm.get("enabled", True):
        status, detail = "fail", (
            "The chart pre-warmer has not completed a cycle for %s. Charts will open "
            "cold and slow. This needs a backend restart, which nothing here will do "
            "automatically." % _minutes(cycle_age))
    elif candidates == 0:
        status, detail = "warn", (
            "The pre-warmer is running but has NOTHING queued, so no chart is being kept "
            "warm and every chart you open will be built from cold. 0 candidates "
            "reconciles with 0 warm, which is why this used to read as a pass.")
    elif total != candidates:
        status, detail = "warn", (
            "The pre-warmer's own numbers do not add up (%d listed, %d accounted for). "
            "Something is being dropped between the queue and the workers."
            % (candidates, total))
    elif candidates and behind * 2 > candidates:
        status, detail = "warn", (
            "More than half the charts (%d of %d) are behind. Opening one of those will "
            "feel slow." % (behind, candidates))
    else:
        status, detail = "pass", "The pre-warmer is cycling and most charts are warm."
    return _result("chart_prewarm", "Chart pre-warmer", status, measured, detail)


def check_scanner_history(ctx: Context) -> dict:
    """9. Today's archives exist AND are still being written to."""
    now = ctx.now_et
    day = now.date().isoformat()
    if now.weekday() >= 5:
        return _result(
            "scanner_history", "Today's scanner archive", "unknown",
            "not measured - %s is a weekend" % now.strftime("%A"),
            "No scan runs at a weekend, so an empty archive is correct, not a fault.")
    if now.hour < 6:
        return _result(
            "scanner_history", "Today's scanner archive", "unknown",
            "not measured - it is %s ET, before the scanner starts" % now.strftime("%H:%M"),
            "The scanner starts at 06:00 ET. Re-run after that for a real answer.")
    # Write AGE is reported but deliberately NOT graded. These archives only
    # grow when something MATCHES, so a Mag7 file untouched for two hours means
    # "nothing fired", not "the writer died" (measured live 2026-09-01: Mag7
    # 113.7 min, Watchlist 1.3 min, both healthy). Grading the age would fail
    # the checklist on every quiet morning, which is how a checklist gets
    # ignored. What IS graded is existence: a missing day file is a real loss.
    parts, problems = [], []
    premarket = Path(ctx.artifacts_dir) / "premarket_scanner_history" / (day + ".json")
    try:
        stored = json.loads(premarket.read_text(encoding="utf-8"))
        rows = stored.get("rows") if isinstance(stored, dict) else None
        age = max(0.0, time.time() - premarket.stat().st_mtime)
        parts.append("premarket %d symbols, written %s ago" % (
            len(rows) if isinstance(rows, dict) else 0, _minutes(age)))
    except (OSError, ValueError):
        problems.append("premarket archive for %s is missing" % day)
    momx_root = Path(ctx.artifacts_dir) / "momx_history"
    boards_seen = 0
    try:
        boards = sorted(p for p in momx_root.iterdir() if p.is_dir())
    except OSError:
        boards = []
    for board in boards:
        path = board / (day + ".json")
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            symbols = stored.get("symbols") if isinstance(stored, dict) else None
            if not isinstance(symbols, dict):
                symbols = stored.get("rows") if isinstance(stored, dict) else {}
            age = max(0.0, time.time() - path.stat().st_mtime)
            parts.append("momx %s %d symbols, written %s ago" % (
                board.name, len(symbols) if isinstance(symbols, dict) else 0, _minutes(age)))
            boards_seen += 1
        except (OSError, ValueError):
            problems.append("momx %s archive for %s is missing" % (board.name, day))
    if boards_seen == 0:
        # boards_seen was computed and never used: with no momx_history tree at
        # all, `boards` is empty, no problem is recorded, and the row passed on
        # the premarket archive alone while saying nothing about momx.
        problems.append("no momx board archive was found at all under %s (expected the "
                        "Mag7 and Watchlist boards)" % momx_root)
    measured = "; ".join(parts) if parts else "no archive file found for " + day
    if problems:
        measured += "; " + "; ".join(problems)
    if not parts:
        return _result(
            "scanner_history", "Today's scanner archive", "fail", measured,
            "Nothing has been archived today, so tonight there will be no record of what "
            "the scanners showed. Nothing here writes that archive for you - it has to "
            "come from the running scanners.")
    if problems:
        return _result("scanner_history", "Today's scanner archive", "warn", measured,
                       "Part of today's archive is missing: " + "; ".join(problems))
    return _result("scanner_history", "Today's scanner archive", "pass", measured,
                   "Today's history is being written.")


def _latency_samples(ctx: Context) -> list:
    """Sample /api/auth/status once per run; checks 10 and 14 share the numbers."""
    cached = ctx.scratch.get("latency_samples")
    if isinstance(cached, list):
        return cached
    samples = []
    for _ in range(max(int(ctx.latency_samples or 0), 1)):
        outcome = ctx.http_get(ctx.api_base + "/api/auth/status", timeout=30.0)
        if outcome.get("ok"):
            samples.append(float(outcome.get("seconds") or 0.0))
    ctx.scratch["latency_samples"] = samples
    return samples


def check_response_latency(ctx: Context) -> dict:
    """10. Median time to answer the smallest endpoint in the app."""
    samples = _latency_samples(ctx)
    if not samples:
        return _result(
            "response_latency", "Backend response time (typical)", "unknown",
            "no sample answered out of %d attempts" % ctx.latency_samples,
            "The backend did not answer any of the timing probes, so its speed is "
            "unknown - not green. See the processes row.", critical=True)
    median = statistics.median(samples)
    measured = "median %.2fs over %d samples of /api/auth/status" % (median, len(samples))
    if median >= LATENCY_MEDIAN_FAIL_SECONDS:
        status, detail = "fail", (
            "The backend takes %.1fs to answer a request that does no work. The whole app "
            "will feel slow, charts most of all. Usually something is eating the CPU."
            % median)
    elif median >= LATENCY_MEDIAN_WARN_SECONDS:
        status, detail = "warn", (
            "The backend is answering in %.2fs where it normally takes under a tenth of a "
            "second. It is under load." % median)
    else:
        status, detail = "pass", "The backend is answering instantly."
    return _result("response_latency", "Backend response time (typical)", status,
                   measured, detail, critical=True)


def check_latency_spikes(ctx: Context) -> dict:
    """14. The WORST sample, not the typical one.

    Deliberately separate from check 10. On 2026-09-01 08:31 the median was
    0.06s and the max was 7.73s - something blocked for eight seconds. A
    median-only check called that green, and eight-second blocks are exactly
    the CPU saturation that made charts crawl all week.
    """
    samples = _latency_samples(ctx)
    if not samples:
        return _result(
            "latency_spikes", "Backend stalls (worst case)", "unknown",
            "no sample answered out of %d attempts" % ctx.latency_samples,
            "No timing sample came back, so stalls cannot be ruled out - this is not a "
            "pass.", critical=True)
    worst = max(samples)
    median = statistics.median(samples)
    measured = "max %.2fs (median %.2fs) over %d samples" % (worst, median, len(samples))
    if worst >= LATENCY_MAX_FAIL_SECONDS:
        status, detail = "fail", (
            "One request in %d took %.1f seconds even though the typical one took %.2fs. "
            "Something on this machine blocks for seconds at a time - that is what makes "
            "a chart take forever to appear even when everything looks fine."
            % (len(samples), worst, median))
    elif worst >= LATENCY_MAX_WARN_SECONDS:
        status, detail = "warn", (
            "Worst request %.1fs against a typical %.2fs. Occasional stalls are starting."
            % (worst, median))
    else:
        status, detail = "pass", "No stalls: the slowest probe was %.2fs." % worst
    return _result("latency_spikes", "Backend stalls (worst case)", status, measured,
                   detail, critical=True)


def check_invalid_grid_symbols(ctx: Context) -> dict:
    """11. Panel symbols in the saved grids the broker does not know (the GOOGLE typo)."""
    document = ctx.load_chart_grids()
    users = document.get("users") if isinstance(document, dict) else None
    if not isinstance(users, dict):
        return _result(
            "invalid_grid_symbols", "Saved layouts point at real tickers", "unknown",
            "no saved layouts could be read from chart_grids.json",
            "The saved layouts file could not be read, so bad tickers in it cannot be "
            "ruled out. This is not a pass.")
    placements: dict = {}
    layouts = 0
    for email, grids in users.items():
        if not isinstance(grids, dict):
            continue
        for name, grid in grids.items():
            if not isinstance(grid, dict):
                continue
            layouts += 1
            workspace = grid.get("workspace") if isinstance(grid.get("workspace"), dict) else {}
            for index, panel in enumerate(workspace.get("panels") or []):
                if not isinstance(panel, dict):
                    continue
                symbol = str(panel.get("symbol") or "").strip().upper()
                if symbol:
                    placements.setdefault(symbol, []).append("%s panel %d" % (name, index + 1))
    if not placements:
        if layouts:
            # A layout count above zero with a placement count of zero is a
            # contradiction. This check hard-codes workspace.panels[].symbol and
            # App.jsx has moved that shape before; the day it moves again the
            # typo detector must go UNKNOWN, not permanently green.
            return _result(
                "invalid_grid_symbols", "Saved layouts point at real tickers", "unknown",
                "%d saved layouts contained no readable panel symbols" % layouts,
                "The saved-layout format this check reads (workspace.panels[].symbol) "
                "found nothing, so a typo'd ticker cannot be ruled out. Not a pass - the "
                "check needs updating to the new layout shape.")
        return _result(
            "invalid_grid_symbols", "Saved layouts point at real tickers", "pass",
            "0 symbols across 0 saved layouts",
            "There are no saved chart layouts, so there is nothing to be wrong.")
    known, resolved = ctx.broker_known(sorted(placements))
    if not resolved:
        return _result(
            "invalid_grid_symbols", "Saved layouts point at real tickers", "unknown",
            "%d symbols across %d layouts, but the broker did not answer" % (
                len(placements), layouts),
            "The broker could not confirm any of the saved tickers, so a typo cannot be "
            "ruled out. Not a pass.")
    unknown = sorted(symbol for symbol in placements if symbol not in known)
    measured = "%d symbols across %d saved layouts, %d the broker does not know" % (
        len(placements), layouts, len(unknown))
    if unknown:
        measured += ": " + ", ".join(
            "%s (%s)" % (symbol, placements[symbol][0]) for symbol in unknown[:5])
    if not unknown:
        return _result("invalid_grid_symbols", "Saved layouts point at real tickers",
                       "pass", measured, "Every saved chart panel points at a real ticker.")
    # FAIL, not warn: this is a defect with a measured ongoing cost (16 wasted
    # rebuilds in 21 minutes, for days) and a one-click human fix. Graded warn
    # it would be reported forever and acted on never.
    return _result(
        "invalid_grid_symbols", "Saved layouts point at real tickers", "fail", measured,
        "These tickers do not exist at the broker, so the app rebuilds them over and over "
        "and never succeeds - it wasted a rebuild every 30 seconds for days once. Open "
        "the layout named above, fix the ticker in that panel, and save. Nothing here "
        "edits your saved layouts.", healable=False)


def check_backups(ctx: Context) -> dict:
    """12. The nightly off-machine backup actually produced a file recently."""
    directory = ctx.backup_directory()
    if directory is None:
        return _result(
            "backups", "Nightly backup", "fail",
            "no backup folder exists on this machine",
            "There is no backup folder at all, so the trade journal and the API keys "
            "exist in exactly one place. Check that Google Drive for Desktop is running, "
            "then run the AGX-Nightly-Backup task once.")
    try:
        files = [p for p in Path(directory).iterdir() if p.is_file()]
    except OSError as exc:
        return _result(
            "backups", "Nightly backup", "unknown",
            "backup folder %s could not be listed (%s)" % (directory, exc),
            "The backup folder could not be read, so whether backups are happening is "
            "unknown - not green.")
    if not files:
        return _result(
            "backups", "Nightly backup", "fail",
            "0 files in %s" % directory,
            "The backup folder is empty. Run the AGX-Nightly-Backup scheduled task and "
            "confirm it produces a trades / secrets / code set.")
    newest = max(files, key=lambda p: p.stat().st_mtime)
    hours = max(0.0, (time.time() - newest.stat().st_mtime) / 3600.0)
    local_only = str(directory).replace("\\", "/").upper().find("LOCAL-ONLY") >= 0
    measured = "%d files in %s, newest %s %.1f h old" % (
        len(files), directory, newest.name, hours)
    if hours >= BACKUP_FAIL_HOURS:
        return _result("backups", "Nightly backup", "fail", measured,
                       "The last backup is %.0f hours old - the nightly job is not "
                       "running. Check the AGX-Nightly-Backup scheduled task." % hours)
    if local_only:
        return _result("backups", "Nightly backup", "warn", measured,
                       "Backups are being written to a folder on THIS disk, not to Google "
                       "Drive. That protects against nothing if the laptop dies. Check "
                       "that Google Drive for Desktop is signed in.")
    if hours >= BACKUP_WARN_HOURS:
        return _result("backups", "Nightly backup", "warn", measured,
                       "Last backup is %.0f hours old; the job runs at 01:30 so it should "
                       "never be older than about a day." % hours)
    return _result("backups", "Nightly backup", "pass", measured,
                   "Last night's backup landed in Google Drive.")


def check_chart_open_speed(ctx: Context) -> dict:
    """13. THE ONE HE FEELS: actually open charts from his board and time them.

    chart_freshness catches WRONG candles and response_latency catches a
    saturated box, but neither measures how long a chart takes to APPEAR -
    which is the complaint raised all week ("any tickers charts loading very
    slow"). Threshold set by the trader: more than 15 seconds is a fail.
    Symbols come from the LIVE board so this measures the click-a-row case, and
    it is capped at three so the check does not become the load it measures.
    """
    board = list(ctx.chart_open_symbols) or (ctx.board_symbols() or ["AAPL", "NVDA", "TSLA"])
    known, _resolved = ctx.broker_known(board)
    # A ticker the broker does not know opens empty no matter how fast the
    # machine is; that is the saved-layouts row's finding, not this one.
    skipped = [symbol for symbol in board if symbol not in known]
    symbols, scope = _board_sample(ctx, board, known)
    timings, empty, failed = [], [], []
    for symbol in symbols:
        outcome = ctx.fetch_chart(symbol, fresh=True)
        seconds = float(outcome.get("seconds") or 0.0)
        payload = outcome.get("json")
        if not outcome.get("ok") or not isinstance(payload, dict):
            failed.append("%s (%s)" % (symbol, outcome.get("error") or "no answer"))
            continue
        bars = len(_bar_times(payload))
        timings.append((symbol, seconds, bars))
        if bars == 0:
            empty.append(symbol)
    if not timings:
        if failed:
            # Every chart request was REFUSED. That is a measured outcome, not
            # an absence of one, so it is a fail rather than an unknown.
            return _result(
                "chart_open_speed", "Time to open a chart", "fail",
                "no chart opened: " + "; ".join(failed),
                "Not one chart would open, so nothing on the board will render. See the "
                "processes row above.", critical=True)
        return _result(
            "chart_open_speed", "Time to open a chart", "unknown",
            "no chart opened: no symbols on the board",
            "No chart could be opened at all, so open speed is unknown - not a pass. See "
            "the processes row.", critical=True)
    worst = max(seconds for _s, seconds, _b in timings)
    measured = ", ".join("%s %.1fs" % (symbol, seconds) for symbol, seconds, _b in timings)
    measured += " (worst %.1fs)" % worst
    if empty:
        measured += "; 0 candles for " + ", ".join(empty)
    if failed:
        measured += "; no answer for " + "; ".join(failed)
    if skipped:
        measured += "; skipped " + ", ".join(skipped) + " (the broker does not know that "
        measured += "ticker - see the saved-layouts row)"
    if scope:
        measured += "; " + scope
    if empty:
        return _result(
            "chart_open_speed", "Time to open a chart", "fail", measured,
            "A chart answered but came back with no candles for " + ", ".join(empty) +
            " - it opens to an empty screen. Check the broker connection rows above.",
            critical=True)
    if len(failed) >= len(timings):
        # More charts refused than opened. Calling that a warn because the
        # survivor was fast reports the winner and hides the casualties.
        return _result(
            "chart_open_speed", "Time to open a chart", "fail", measured,
            "%d of %d charts would not open at all (%s). The one that did was fast, but "
            "most of the board is not rendering." % (
                len(failed), len(failed) + len(timings), "; ".join(failed)),
            critical=True)
    if worst >= CHART_OPEN_FAIL_SECONDS:
        status, detail = "fail", (
            "A chart took %.0f seconds to appear. Your limit is 15. Something is "
            "saturating this machine - check the stalls row below." % worst)
    elif worst >= CHART_OPEN_WARN_SECONDS:
        status, detail = "warn", (
            "Charts are taking up to %.0f seconds to appear. Still usable, but it is "
            "drifting towards the slow behaviour from last week." % worst)
    elif failed:
        status, detail = "warn", (
            "The charts that opened were fast, but these did not answer: " + "; ".join(failed))
    else:
        status, detail = "pass", "Charts appear in under a second."
    return _result("chart_open_speed", "Time to open a chart", status, measured, detail,
                   critical=True)


@dataclass(frozen=True)
class CheckSpec:
    id: str
    label: str
    run: Callable


#: Order matters for cost, not for correctness: chart_open_speed runs FIRST so
#: it measures a genuinely fresh request, and the two chart checks after it
#: reuse the payloads it already paid for.
CHECKS: tuple = (
    CheckSpec("processes_endpoints", "Processes and endpoints", check_processes_endpoints),
    CheckSpec("chart_open_speed", "Time to open a chart", check_chart_open_speed),
    CheckSpec("chart_freshness", "Chart candles vs the broker", check_chart_freshness),
    CheckSpec("premarket_window", "Premarket 04:00-07:00 bars", check_premarket_window),
    CheckSpec("schwab_token", "Schwab connection", check_schwab_token),
    CheckSpec("alpaca_credentials", "Alpaca market-data key", check_alpaca_credentials),
    CheckSpec("tradier", "Tradier (premarket primary)", check_tradier),
    CheckSpec("momx_scanner", "MomX scanner board", check_momx_scanner),
    CheckSpec("chart_prewarm", "Chart pre-warmer", check_chart_prewarm),
    CheckSpec("scanner_history", "Today's scanner archive", check_scanner_history),
    CheckSpec("response_latency", "Backend response time (typical)", check_response_latency),
    CheckSpec("latency_spikes", "Backend stalls (worst case)", check_latency_spikes),
    CheckSpec("invalid_grid_symbols", "Saved layouts point at real tickers",
              check_invalid_grid_symbols),
    CheckSpec("backups", "Nightly backup", check_backups),
)


# ----------------------------------------------------------------------
# AUTO-HEAL
#
# TWO TIERS, and the boundary is not negotiable.
#
# SAFE to run automatically - every one of these is idempotent, touches no
# credential, deletes nothing, restarts nothing, and is byte-for-byte a request
# the app already makes on its own:
#   * re-touch a stale chart tape        (the browser's own refresh request)
#   * force a premarket backfill retry   (same request, different symbols)
#   * re-resolve a poisoned credential cache (clears a remembered FAILURE)
#   * ask the MomX worker to rebuild     (its own routine work)
#
# NEVER automatic, no exceptions: restarting any process, writing any
# credential, deleting any data, or editing the trader's saved grids. Those
# report the exact action for a human instead - see each check's `detail`.
#
# Market-hours rule: a heal that could disturb the trader is DEFERRED while the
# market is open and reported instead. Judged per action below.
# ----------------------------------------------------------------------


class HealUnavailable(RuntimeError):
    """The automatic fix could not run here (and must not be faked as done)."""


@dataclass(frozen=True)
class HealAction:
    check_id: str
    description: str
    #: False = defer while the market is open, because running it could make the
    #: app worse for the trader at exactly the wrong moment.
    market_hours_safe: bool
    run: Callable


def heal_chart_freshness(ctx: Context) -> str:
    """Ask the server to re-pull the stale symbols from the broker.

    Market-hours safe: this is the identical request the browser sends when a
    chart is re-opened. Three extra requests, no restart, no state destroyed.
    """
    symbols = list(ctx.scratch.get("stale_chart_symbols") or []) or list(
        ctx.scratch.get("charts", {}).keys())[:3]
    if not symbols:
        raise HealUnavailable("no symbol was measured as stale, so there is nothing to refresh")
    done = []
    for symbol in symbols[:3]:
        outcome = ctx.http_get(ctx.chart_url(symbol, refresh="1"), timeout=90.0)
        if outcome.get("ok"):
            done.append(symbol)
    ctx.scratch.pop("charts", None)  # force the re-run to measure the NEW tape
    if not done:
        raise HealUnavailable("the refresh request did not succeed for any symbol")
    return "asked the server to re-pull " + ", ".join(done) + " from the broker"


def heal_premarket_window(ctx: Context) -> str:
    """Force the 04:00-07:00 backfill to retry for the symbols that had no bars.

    Market-hours safe: same refresh request as above, at most nine of them, and
    the backfill it triggers is the one the server already runs on its own.
    """
    symbols = list(ctx.scratch.get("premarket_missing_symbols") or [])
    if not symbols:
        raise HealUnavailable("no symbol was measured as empty, so there is nothing to backfill")
    # Capped like its sibling. Uncapped this was nine serial requests at a 90s
    # timeout - up to 13.5 minutes holding the run lock, and it fires exactly
    # when the backend is already sick and those timeouts are most likely.
    attempt, deferred = symbols[:3], symbols[3:]
    done = []
    for symbol in attempt:
        outcome = ctx.http_get(ctx.chart_url(symbol, refresh="1"), timeout=90.0)
        if outcome.get("ok"):
            done.append(symbol)
    ctx.scratch.pop("charts", None)
    if not done:
        raise HealUnavailable("the backfill retry did not succeed for any symbol")
    sentence = "forced a premarket backfill retry for " + ", ".join(done)
    if deferred:
        sentence += (" (capped at 3 per run; %s were not retried this time)"
                     % ", ".join(deferred))
    return sentence


def heal_alpaca_credentials(ctx: Context) -> str:
    """Clear the remembered FAILURE in the owner-Alpaca client cache.

    This is the exact bug from the incident list: the cache stored "failed"
    permanently and kept a WORKING key switched off. Clearing it makes the next
    call re-resolve. Market-hours safe: it drops one cached negative, writes no
    credential, and cannot lose data. Only possible in-process - from outside,
    it reports the manual action instead of pretending.
    """
    state = ctx.api_server_state()
    if state is None:
        raise HealUnavailable(
            "this fix only works from inside the backend process; from here, open "
            "Settings -> API credentials and re-save the Alpaca key")
    cached = getattr(state, "_owner_alpaca_client_cache", None)
    if cached is not False:
        raise HealUnavailable("the credential cache holds no remembered failure to clear")
    state._owner_alpaca_client_cache = None
    state._owner_alpaca_client_cache_at = 0.0
    return "cleared a remembered credential failure so the saved Alpaca key is retried"


def heal_momx_scanner(ctx: Context) -> str:
    """Queue a MomX board rebuild.

    NOT market-hours safe, and this is a judgement call worth stating: a MomX
    build has historically taken 8 of 12 cores, and CPU pressure during the
    session is precisely what made charts crawl. A stale board is annoying; a
    forced extra build at 09:31 is worse than the fault. While the market is
    open this is reported, not run.
    """
    outcome = ctx.http_post_json(
        ctx.momx_base + "/api/momx-scanner/rebuild", {"list": ctx.momx_board}, timeout=30.0)
    if not outcome.get("ok"):
        raise HealUnavailable("the worker refused the rebuild request (%s)"
                              % (outcome.get("error") or "no answer"))
    return "queued a rebuild of the %s board" % ctx.momx_board


HEALERS: tuple = (
    HealAction("chart_freshness", "re-pull the stale chart from the broker", True,
               heal_chart_freshness),
    HealAction("premarket_window", "force the premarket backfill to retry", True,
               heal_premarket_window),
    HealAction("alpaca_credentials", "clear the remembered credential failure", True,
               heal_alpaca_credentials),
    HealAction("momx_scanner", "queue a scanner rebuild", False, heal_momx_scanner),
)


# ----------------------------------------------------------------------
# History: one file per ET day, atomic write, 30-day prune.
# Shape copied from premarket_scanner_history.py, in service since 2026-08-23.
# ----------------------------------------------------------------------


#: A day file is named exactly YYYY-MM-DD.json. Enforced on every path that
#: DELETES, because `directory` is a public argument of run_preflight and a bare
#: stem comparison happily unlinked "1-important.json" and "2025-backup.json".
_DAY_FILE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _day_file(directory: Path, day: str) -> Path:
    return Path(directory) / (day + ".json")


def record_run(summary: dict, now_et: datetime, directory: Path | str = DEFAULT_HISTORY_DIR) -> dict:
    """Fold one run into today's file. Keeps the LATEST run and the WORST status.

    Keeping the worst is the whole point: an intermittent failure at 09:05 must
    not be erased by a clean re-run at 16:00, or the checklist would quietly
    forget the only morning that mattered.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = now_et.date().isoformat()
    path = _day_file(directory, day)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    checks = summary.get("checks") or []
    run_status = {}
    for check in checks:
        check_id = str(check.get("id") or "")
        if check_id:
            run_status[check_id] = str(check.get("status") or "")
    # Fold the PRE-heal status back in. The heal loop overwrites summary["checks"]
    # with the post-heal result, so without this a fault that was automatically
    # fixed at 08:45 archives as a fully green day and survives nowhere except
    # healed[].statusBefore - which is exactly the "quietly forget the only
    # morning that mattered" this function was written to prevent.
    for entry in summary.get("healed") or []:
        check_id = str(entry.get("id") or "")
        before = str(entry.get("statusBefore") or "")
        if check_id and before:
            run_status[check_id] = worst_status(run_status.get(check_id, ""), before)
    run_worst = worst_status(*run_status.values()) if run_status else "unknown"
    previous_worst = str(stored.get("worst") or "")
    combined_worst = worst_status(previous_worst, run_worst) if previous_worst else run_worst
    check_worst = stored.get("checkWorst") if isinstance(stored.get("checkWorst"), dict) else {}
    for check_id, status in run_status.items():
        check_worst[check_id] = worst_status(str(check_worst.get(check_id) or ""), status)
    healed_ids = list(stored.get("healedIds") or [])
    for entry in summary.get("healed") or []:
        check_id = str(entry.get("id") or "")
        if check_id and check_id not in healed_ids:
            healed_ids.append(check_id)
    # The day file IS the latest summary, flattened, with the day's memory laid
    # on top. Flattened rather than nested under "latest" because the reader
    # (the admin endpoint) attributes an archived day by reading `at` off the
    # entry itself; a nested shape made every archived day read as "no date",
    # so the strip would have shown thirty blank days and called it fine.
    document = dict(summary)
    document.update({
        "date": day,
        "runs": int(stored.get("runs") or 0) + 1,
        "worst": combined_worst,
        "worstAt": stored.get("worstAt") if combined_worst == previous_worst else summary.get("at"),
        "checkWorst": check_worst,
        "healedIds": healed_ids,
    })
    temporary = path.with_name("." + path.name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps(document), encoding="utf-8")
    os.replace(temporary, path)
    _prune(directory, now_et)
    return document


def _prune(directory: Path, now_et: datetime) -> None:
    """Drop day files older than RETENTION_DAYS (name-sorted ISO dates)."""
    directory = Path(directory)
    cutoff = (now_et.date() - timedelta(days=RETENTION_DAYS)).isoformat()
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return
    for path in files:
        if not _DAY_FILE_NAME.match(path.stem):
            continue
        if path.stem < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
    # A crash between the tmp write and os.replace leaves an orphan the *.json
    # glob never matches; sweep any .tmp older than a day.
    try:
        for orphan in directory.glob(".*.tmp"):
            if (time.time() - orphan.stat().st_mtime) > 86400:
                orphan.unlink()
    except OSError:
        pass


def load_preflight_history(days: int = RETENTION_DAYS,
                           directory: Path | str = DEFAULT_HISTORY_DIR) -> list:
    """Newest-first day records for the UI strip."""
    directory = Path(directory)
    try:
        files = sorted(
            (p for p in directory.glob("*.json") if _DAY_FILE_NAME.match(p.stem)),
            reverse=True,
        )[: max(int(days), 0)]
    except OSError:
        return []
    records = []
    for path in files:
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(stored, dict):
            stored.setdefault("date", path.stem)
            records.append(stored)
    return records


def healed_streak(check_id: str, now_et: datetime,
                  directory: Path | str = DEFAULT_HISTORY_DIR) -> int:
    """How many PREVIOUS run-days in a row this check needed the same fix.

    Counts consecutive day FILES, not calendar days: a weekend has no file and
    must not reset the streak of a fault that shows up every trading morning.
    """
    today = now_et.date().isoformat()
    streak = 0
    for record in load_preflight_history(RETENTION_DAYS, directory):
        if str(record.get("date")) >= today:
            continue
        if check_id in (record.get("healedIds") or []):
            streak += 1
        else:
            break
    return streak


# ----------------------------------------------------------------------
# Runner
# ----------------------------------------------------------------------


def _run_one(spec: CheckSpec, ctx: Context) -> dict:
    """Run a check; ANY failure inside it degrades to unknown, never to pass.

    A broken check must not take the run down and must not be mistaken for a
    healthy one - both of those are the exact failure this feature exists for.
    """
    try:
        outcome = spec.run(ctx)
    except Exception as exc:  # noqa: BLE001
        return _result(
            spec.id, spec.label, "unknown",
            "the check itself failed to run (%s)" % type(exc).__name__,
            "This item could not be checked, so its state is unknown - treat it as "
            "unchecked, not as healthy. Technical detail: %s: %s"
            % (type(exc).__name__, exc))
    if not isinstance(outcome, dict) or not outcome.get("id"):
        return _result(
            spec.id, spec.label, "unknown", "the check returned nothing usable",
            "This item could not be checked, so its state is unknown - not healthy.")
    # The id is taken from the SPEC, never from the returned dict: the runner
    # looks checks up by id to re-run them after a heal, and a check that
    # renamed itself would make that lookup explode.
    normalised = _result(
        spec.id,
        str(outcome.get("label") or spec.label),
        str(outcome.get("status") or "unknown"),
        outcome.get("measured") or "",
        outcome.get("detail") or "",
        healable=bool(outcome.get("healable")),
        critical=bool(outcome.get("critical")),
    )
    return normalised


def run_preflight(
    now_et: datetime | None = None,
    *,
    ctx: Context | None = None,
    directory: Path | str | None = None,
    checks: tuple | None = None,
    healers: tuple | None = None,
    heal: bool = True,
    record: bool = True,
) -> dict:
    """Run every check, heal what is safe to heal, and archive the result.

    Returns {at, passed, warned, failed, unknown, checks: [...], healed: [...]}.
    """
    history_dir = Path(directory) if directory is not None else (
        ctx.history_dir if ctx is not None else DEFAULT_HISTORY_DIR)
    if ctx is None:
        ctx = Context(now_et=now_et or datetime.now(EASTERN), history_dir=history_dir)
    elif now_et is not None:
        ctx.now_et = now_et
    ctx.history_dir = history_dir
    specs = tuple(checks) if checks is not None else CHECKS
    actions = {a.check_id: a for a in (tuple(healers) if healers is not None else HEALERS)}

    results = [_run_one(spec, ctx) for spec in specs]
    by_id = {spec.id: spec for spec in specs}
    healed: list = []
    market_open = _is_market_hours(ctx.now_et)
    heal_deferred = _is_heal_deferred(ctx.now_et)

    for index, outcome in enumerate(results):
        if not heal:
            break
        if outcome["status"] not in {"warn", "fail"} or not outcome["healable"]:
            continue
        action = actions.get(outcome["id"])
        if action is None:
            continue
        if heal_deferred and not action.market_hours_safe:
            # Rule (b): do not disturb the trader mid-session to fix something
            # that is merely stale. Report it instead, and say why.
            outcome["detail"] += (
                " | The automatic fix (%s) was NOT run: it is inside the window that "
                "opens %d minutes before the bell and closes at 16:00, and it would slow "
                "the app down while you are trading. It will run outside that window, or "
                "you can trigger it yourself."
                % (action.description, HEAL_PREOPEN_BUFFER_MINUTES))
            continue
        try:
            did = action.run(ctx)
        except Exception as exc:  # noqa: BLE001 - HealUnavailable and anything else
            outcome["detail"] += (" | The automatic fix could not run: %s." % exc)
            continue
        # Rule (a): RE-RUN the check and report the REAL outcome. Never report
        # green because a fix was attempted - that is precisely the bug class
        # this whole feature exists to prevent.
        after = _run_one(by_id[outcome["id"]], ctx)
        if after["status"] == "unknown" and outcome["status"] in {"warn", "fail"}:
            # A re-check that CRASHED must not launder a fail into an unknown
            # and thereby LOWER the run's overall severity. Healing may only
            # improve the reported state by actually having fixed something.
            after["detail"] = (
                "The re-check after the automatic fix could not run, so the earlier %s "
                "stands. " % outcome["status"]) + after["detail"]
            after["status"] = outcome["status"]
        after["detail"] = ("Automatic fix applied (%s). " % did) + after["detail"]
        # `healed` means REPAIRED, not "a repair was attempted". Stamping it on
        # every attempt made the panel count a fix that changed nothing as a
        # problem solved - the same lie one level down from the healed list.
        # heal_momx_scanner can NEVER improve its own re-check (it queues an
        # async rebuild and the board is re-read milliseconds later), so on a
        # stale-board morning the row read FAIL under "1 problem fixed".
        after["healed"] = STATUS_ORDER[after["status"]] < STATUS_ORDER[outcome["status"]]
        after["healAttempted"] = True
        after["healAction"] = did
        entry = {
            "id": after["id"],
            "label": after["label"],
            "action": did,
            "at": ctx.now_et.isoformat(),
            "statusBefore": outcome["status"],
            "statusAfter": after["status"],
        }
        # Rule (c): a fix that keeps being needed is MASKING a defect. Escalate
        # even when it worked, or the auto-fixer hides a chronic fault forever.
        streak = healed_streak(after["id"], ctx.now_et, history_dir) + 1
        entry["daysRunning"] = streak
        if streak >= CHRONIC_HEAL_DAYS and after["status"] == "pass":
            after["status"] = "warn"
            after["detail"] = (
                "This has needed the same automatic fix %d days running, so the fix is "
                "covering up a real problem rather than curing it. " % streak
            ) + after["detail"]
        results[index] = after
        healed.append(entry)

    counts = {"pass": 0, "warn": 0, "fail": 0, "unknown": 0}
    for outcome in results:
        counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1
    summary = {
        "at": ctx.now_et.isoformat(),
        "date": ctx.now_et.date().isoformat(),
        "marketHours": market_open,
        "passed": counts["pass"],
        "warned": counts["warn"],
        "failed": counts["fail"],
        "unknown": counts["unknown"],
        "overall": worst_status(*[o["status"] for o in results]) if results else "unknown",
        "checks": results,
        "healed": healed,
    }
    if record:
        try:
            stored = record_run(summary, ctx.now_et, history_dir)
            summary["runsToday"] = stored.get("runs")
            summary["worstToday"] = stored.get("worst")
        except Exception as exc:  # noqa: BLE001 - archiving must never kill a run
            summary["recordError"] = "%s: %s" % (type(exc).__name__, exc)
    return summary


_BADGE = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "unknown": "UNKNOWN"}


def format_report(summary: dict) -> str:
    """The checklist as a human reads it - one line of NUMBERS per row."""
    lines = []
    header = "AGX PREFLIGHT  %s   pass %d   warn %d   fail %d   unknown %d   overall %s" % (
        str(summary.get("at") or "")[:19].replace("T", " "),
        int(summary.get("passed") or 0), int(summary.get("warned") or 0),
        int(summary.get("failed") or 0), int(summary.get("unknown") or 0),
        _BADGE.get(str(summary.get("overall")), "?"),
    )
    lines.append(header)
    lines.append("-" * len(header))
    for check in summary.get("checks") or []:
        lines.append("%-8s %-34s %s" % (
            _BADGE.get(check.get("status"), "?"), check.get("label"), check.get("measured")))
        if check.get("status") != "pass":
            lines.append(" " * 9 + "-> " + str(check.get("detail")))
    healed = summary.get("healed") or []
    if healed:
        lines.append("")
        lines.append("Automatic fixes applied this run:")
        for entry in healed:
            lines.append("  - %s: %s (%s -> %s, %d day(s) running)" % (
                entry.get("label"), entry.get("action"), entry.get("statusBefore"),
                entry.get("statusAfter"), int(entry.get("daysRunning") or 1)))
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    summary = run_preflight(
        heal="--no-heal" not in argv,
        record="--no-record" not in argv,
    )
    if "--json" in argv:
        print(json.dumps(summary, indent=2))
    else:
        print(format_report(summary))
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
