"""Keep a "Movers" list populated with the day's gainers, in the worker.

2026-09-03: CHPT ran +72% on 19x volume and the board never saw it until he
typed the ticker in at 12:56, three and a half hours after MomoX3 had it. Not
a scan failure - CHPT was not in his 358-name watchlist, and a watchlist
scanner cannot find what it is not watching.

The refresher writes ONE list through board.save_universe and asks the warmer
to rebuild just that list. Deliberately NOT service.set_universe, which also
calls board.set_active_list, wipes the list's payload and drops its
matched-since map: on a 5-minute cadence that would yank whichever tab he was
reading over to Movers, and reset every "NEW" flag on the board each cycle.

Three properties that keep it from becoming a nuisance:

* it writes only when the SET of names changes. The feed re-ranks every few
  seconds and a re-rank is not new information; rebuilding on one would cost a
  build every cycle for nothing.
* an empty or failed fetch leaves the list exactly as it is. A dead key or a
  rate limit must never empty a board he is watching.
* it never touches the active list, so the tab he is on is his.

Credentials: the PAPER5 profile. Measured 2026-09-03 - the default key and
the PAPER3/PAPER4 profiles all return 401 on the screener AND on plain market
data, so they are simply dead; PAPER5 answers both.
"""
import io

p = "momx_worker.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = "from momx import board, fastlane, history, momo_alert, service"
NEW = "from momx import board, fastlane, history, momo_alert, movers, service"
assert s.count(OLD) == 1, "import anchor"
s = s.replace(OLD, NEW)

OLD = '''def _momo_loop() -> None:'''
NEW = '''#: The auto-managed list of the day's biggest gainers. Named like any other
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
    picked = movers.select_movers(gainers)
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


def _momo_loop() -> None:'''
assert s.count(OLD) == 1, "loop anchor"
s = s.replace(OLD, NEW)

OLD = '''    _threading.Thread(target=_momo_loop, name="momo-eval", daemon=True).start()'''
NEW = '''    _threading.Thread(target=_momo_loop, name="momo-eval", daemon=True).start()
    _threading.Thread(target=_movers_loop, name="movers-refresh", daemon=True).start()'''
assert s.count(OLD) == 1, "thread anchor"
s = s.replace(OLD, NEW)

# A manual trigger, so the list can be filled without waiting out the timer.
OLD = '''            if path == "/api/momx-scanner/rebuild":'''
NEW = '''            if path == "/api/momx-scanner/movers/refresh":
                self._json(HTTPStatus.OK, _movers_refresh())
                return
            if path == "/api/momx-scanner/rebuild":'''
assert s.count(OLD) == 1, "route anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("momx_worker.py: Movers list refresher + manual trigger")
