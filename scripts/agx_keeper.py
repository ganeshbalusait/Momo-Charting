"""Keep every ticker's chart tape warm, and keep saying whether the feeds work.

Ganesh, 2026-08-27: "I want simple any tickers loading ultra fast chart and
option in market hours" and "run AI all the time to fix my charts... 24/7".

WHAT THIS DOES
    - Finds tapes that have fallen behind the market and touches them, paced,
      so a ticker is already current when he opens it instead of serving a
      stale copy and healing over the next few minutes. That gap - fast to
      respond, but 80 minutes short of the close - is what "still I see candle
      gaps" actually was.
    - Probes every provider on a slower cycle and publishes the verdict to
      artifacts/feed_health.json, so a refused credential is visible within
      minutes rather than after a day.

WHAT THIS DELIBERATELY DOES NOT DO
    - It never restarts anything. A premarket restart is what dropped his open
      charts at the bell on the 27th.
    - It never edits code or configuration.
    - It contains no model call. The work here is deterministic - survey,
      compare, fetch, report - and putting an LLM in a loop that runs every
      minute would add cost and nondeterminism to something with exactly one
      right answer. Diagnosis of something NEW is where a model earns its keep,
      and that is a human asking for it, not a cron.

It is a caretaker, not a surgeon.

Run:      .venv\\Scripts\\python.exe scripts\\agx_keeper.py
Stop:     Ctrl-C, or kill the process. Nothing it does is stateful.
"""
from __future__ import annotations

import datetime
import gzip
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
API = "http://127.0.0.1:3002"
# Must match request_context.BACKGROUND_HEADER in the server.
BACKGROUND_HEADER = "X-AGX-Background"
CACHE = ROOT / "artifacts" / "oi_chart_cache"
LOG = ROOT / "artifacts" / "agx_keeper.log"

# A tape more than this far behind the newest bar ANY symbol has is worth
# refreshing. Judged against other symbols rather than the clock so a holiday,
# a halt or a quiet overnight does not make every ticker look broken at once.
LAG_MINUTES = 20.0
# Pace between touches. Each one costs the server a ~2.4s build at most, and
# the point of this process is to be invisible - a keeper that starves the app
# it maintains has made things worse.
TOUCH_SECONDS = 1.5
# Never touch more than this in one cycle, so a cold start cannot become a
# thundering herd.
MAX_PER_CYCLE = 10
CYCLE_SECONDS = 300.0
HEALTH_EVERY_CYCLES = 3
# A tape whose newest bar did not move after we touched it is telling us the
# touch cannot help: the symbol simply has not traded since. That is the normal
# state of most of the list after 16:00 and all weekend - on the evening of the
# 27th, 326 of 399 tapes read as "lagging" while exactly one was genuinely
# stale, because lag is judged against the handful of names still printing
# after hours. Without a back-off the keeper would rebuild those 326 every
# cycle, forever, for a result that cannot change. Each fruitless touch doubles
# the rest, so a thin name costs one build and then goes quiet.
MAX_COOLDOWN_CYCLES = 24


def log(message: str) -> None:
    stamp = datetime.datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    line = "%s  %s" % (stamp, message)
    print(line, flush=True)
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def market_phase(now: datetime.datetime) -> str:
    if now.weekday() >= 5:
        return "weekend"
    minute = now.hour * 60 + now.minute
    if 9 * 60 + 30 <= minute < 16 * 60:
        return "regular"
    if 4 * 60 <= minute < 9 * 60 + 30 or 16 * 60 <= minute < 20 * 60:
        return "extended"
    return "closed"


def starts_new_session(now: datetime.datetime, cleared: "datetime.date | None") -> bool:
    """Is this the first cycle of a trading day whose rest we have not cleared?

    From 04:00 so premarket is warm, through 20:00. The post-close stretch does
    not re-clear because the day was already cleared that morning, so the
    evening still costs nothing.
    """
    if now.weekday() >= 5:
        return False
    if not (4 * 60 <= now.hour * 60 + now.minute < 20 * 60):
        return False
    return cleared != now.date()


def survey() -> tuple[float, dict[str, float]]:
    """Newest bar per cached symbol, and the market-wide high-water mark."""
    newest = 0.0
    tapes: dict[str, float] = {}
    for path in CACHE.glob("*.json.gz"):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                bars = json.load(handle).get("bars") or []
        except Exception:
            continue
        if not bars:
            continue
        try:
            stamp = float(bars[-1].get("time") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        tapes[path.stem.replace(".json", "")] = stamp
        newest = max(newest, stamp)
    return newest, tapes


def touch(symbol: str) -> bool:
    try:
        # Declare the chore. Without this header the server cannot tell these
        # touches from the trader's own browser, because they are the same
        # request to the same endpoint - so each one reset the 45s "a human is
        # watching" pause and claimed interactive priority in ChartBuildLane
        # against the chart actually on screen. At ~100 touches a cycle the
        # pause was never not held, and the warmer it exists to gate never ran.
        # historyStatus, not the full payload. Both trigger the same server-side
        # warm refresh (api_server.py: the status handler calls
        # _start_oi_finder_chart_refresh when a tape is missing or stale), but
        # this returns ~95 bytes instead of serialising the entire 5-6 MB
        # research payload. 2026-08-28: the full-payload touch was serialising
        # ~120 x 5 MB per cycle and reading ONE byte of each - a recursive walk
        # of ~300k nodes plus json.dumps plus gzip, all under the GIL, which
        # starved every trader request (auth/status measured at 13.5s, charts
        # blanked on tab switch). The keeper already only reads one byte, so it
        # never wanted the body; it wanted the side effect, which the cheap
        # endpoint delivers.
        request = urllib.request.Request(
            "%s/api/oi-finder-chart?symbol=%s&historyStatus=true" % (API, symbol),
            headers={BACKGROUND_HEADER: "1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            response.read(1)
        return True
    except Exception:
        return False


def run_health_check() -> None:
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "health_report.py")],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT))
        status = ROOT / "artifacts" / "feed_health.json"
        payload = json.loads(status.read_text(encoding="utf-8")) if status.exists() else {}
        if payload.get("healthy"):
            log("feeds OK")
        else:
            for problem in payload.get("problems", []):
                log("FEED PROBLEM: %s" % problem)
        if result.returncode not in (0, 1):
            log("health check exited %s" % result.returncode)
    except Exception as exc:
        log("health check failed to run: %s" % str(exc)[:120])


def already_running() -> bool:
    """True when another keeper owns the lock, so this copy should just exit.

    The scheduler re-launches every 10 minutes as its restart mechanism: if the
    keeper is alive the new copy exits immediately, and if it died the new copy
    takes over. Without this guard that same mechanism would stack a fresh
    keeper every 10 minutes until the machine had dozens of them touching the
    same endpoints - a self-inflicted thundering herd, which is exactly the
    failure this process exists to avoid.
    """
    lock = ROOT / "artifacts" / "agx_keeper.lock"
    try:
        if lock.exists():
            pid = int((lock.read_text(encoding="utf-8") or "0").strip() or 0)
            if pid > 0:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
                     "Where-Object { $_.CommandLine -like '*agx_keeper*' } | "
                     "Measure-Object).Count" % pid],
                    capture_output=True, text=True, timeout=45)
                if (result.stdout or "").strip().startswith("1"):
                    return True
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(__import__("os").getpid()), encoding="utf-8")
        return False
    except Exception:
        # A lock we cannot read must not stop the keeper from running at all.
        return False


PAUSE_MARKER = ROOT / "artifacts" / "keeper_paused"


def main() -> int:
    if PAUSE_MARKER.exists():
        print("keeper paused (artifacts/keeper_paused present); exiting", flush=True)
        return 0
    if already_running():
        print("another keeper is already running; exiting", flush=True)
        return 0
    log("keeper started (lag>%.0fm, %.1fs pacing, cycle %.0fs)" % (
        LAG_MINUTES, TOUCH_SECONDS, CYCLE_SECONDS))
    cycle = 0
    # symbol -> newest-bar stamp at the moment we touched it last cycle
    touched_at: dict[str, float] = {}
    # symbol -> consecutive touches that moved nothing / cycles left to rest
    misses: dict[str, int] = {}
    cooldown: dict[str, int] = {}
    # The trading date whose rest history we have already cleared.
    session_cleared: datetime.date | None = None
    while True:
        cycle += 1
        now = datetime.datetime.now(ET)
        phase = market_phase(now)
        try:
            # Rest earned overnight must not carry into the session. "Did not
            # move while the market was shut" says nothing about a symbol -
            # NOTHING moves after 20:00 - whereas "did not move during the
            # session" is the symbol telling us it is thin. Same observation,
            # opposite meaning, and only the second should earn rest. Without
            # this, everything drifts to the 24-cycle cap overnight and a
            # symbol could wait two hours past 09:30 for its first touch, which
            # is precisely the window this process exists to protect. Cleared
            # once at the start of each trading day, from 04:00 so premarket is
            # warm too; the post-close 16:00-20:00 stretch does not re-clear,
            # so the evening still costs nothing.
            if starts_new_session(now, session_cleared):
                if cooldown or misses:
                    log("session open: cleared rest for %d symbols "
                        "(overnight quiet says nothing about a live market)"
                        % len(set(cooldown) | set(misses)))
                cooldown.clear()
                misses.clear()
                touched_at.clear()
                session_cleared = now.date()

            if cycle == 1 or cycle % HEALTH_EVERY_CYCLES == 0:
                run_health_check()

            newest, tapes = survey()
            if not tapes:
                log("no cached tapes yet")
            else:
                # Grade last cycle's touches before choosing this cycle's.
                for symbol, before in touched_at.items():
                    if tapes.get(symbol, 0.0) > before:
                        misses.pop(symbol, None)
                        cooldown.pop(symbol, None)
                    else:
                        streak = misses.get(symbol, 0) + 1
                        misses[symbol] = streak
                        cooldown[symbol] = min(2 ** streak, MAX_COOLDOWN_CYCLES)
                touched_at.clear()

                lagging = [
                    sym for sym, stamp in tapes.items()
                    if (newest - stamp) / 60.0 > LAG_MINUTES
                ]
                lagging.sort(key=lambda sym: tapes[sym])

                # A symbol that came current on its own - because Ganesh opened
                # it, or because it simply traded again - has answered the only
                # question the rest was asking. Forget its history, or a name
                # that recovers and then genuinely breaks would sit out up to
                # two hours before we looked at it.
                current = set(tapes) - set(lagging)
                for symbol in current:
                    misses.pop(symbol, None)
                    cooldown.pop(symbol, None)

                ready = []
                resting = 0
                for symbol in lagging:
                    left = cooldown.get(symbol, 0)
                    if left > 0:
                        cooldown[symbol] = left - 1
                        resting += 1
                        continue
                    ready.append(symbol)

                batch = ready[:MAX_PER_CYCLE]
                live_before = len(tapes) - len(lagging)
                if not batch:
                    log("%s: %d/%d current, %d lagging but resting" % (
                        phase, live_before, len(tapes), resting))
                else:
                    ok = 0
                    for symbol in batch:
                        touched_at[symbol] = tapes.get(symbol, 0.0)
                        ok += touch(symbol)
                        time.sleep(TOUCH_SECONDS)
                    log("%s: %d/%d current, touched %d of %d lagging "
                        "(%d ok, %d resting)" % (
                            phase, live_before, len(tapes), len(batch),
                            len(lagging), ok, resting))
        except Exception as exc:  # never let one bad cycle end the keeper
            log("cycle error: %s" % str(exc)[:160])

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("keeper stopped")
