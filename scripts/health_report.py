"""Ask every market-data feed whether it is ACCEPTED, and say so in English.

Written 2026-08-27 after a day in which four separate failures were all silent:
a Schwab credential the broker was refusing while the UI showed a green lamp, a
Tradier token returning 401 behind an empty premarket band, a date parser 400x
too slow behind "charts are loading slow", and 130 poisoned caches behind the
same complaint. Every one was found by a trader looking at a chart and sensing
something was wrong, hours later.

This is deliberately READ-ONLY. It probes, it reports, it changes nothing and
restarts nothing. An agent that edits production on a schedule is how a
premarket restart takes the charts out at the opening bell - which is exactly
what happened today. Detection is the part worth automating; repair is not.

Run:  .venv\\Scripts\\python.exe scripts\\health_report.py
Exit: 0 all good, 1 something needs a human.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
API = "http://127.0.0.1:3002"
# Liquid names across sectors: if these are stale, everything is.
SAMPLE = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "MSFT", "AMD", "META"]

problems: list[str] = []
notes: list[str] = []


def say(line: str = "") -> None:
    print(line, flush=True)


def probe_schwab() -> None:
    """Does Schwab ACCEPT each credential - not merely, is one stored."""
    try:
        from data.schwab_client import SchwabClient
    except Exception as exc:
        problems.append("cannot import the Schwab client: %s" % exc)
        return
    for profile, label in (("", "market data"), ("trading", "trading")):
        try:
            client = SchwabClient(profile) if profile else SchwabClient()
            if not getattr(client, "configured", False):
                problems.append("Schwab %s app: no credential saved." % label)
                say("  Schwab %-12s NOT CONFIGURED" % label)
                continue
            quotes = client.get_quotes(["SPY"])
            if quotes.get("SPY"):
                say("  Schwab %-12s OK" % label)
            else:
                problems.append(
                    "Schwab %s app: the key is saved but returned no quote." % label)
                say("  Schwab %-12s ACCEPTED BUT EMPTY" % label)
        except Exception as exc:
            detail = str(exc)
            hint = ("the app key or secret is wrong - re-paste it in Settings"
                    if "invalid_client" in detail or "Unauthorized" in detail
                    else detail[:90])
            problems.append("Schwab %s app is REFUSED: %s" % (label, hint))
            say("  Schwab %-12s REFUSED  (%s)" % (label, hint))


def probe_tradier() -> None:
    """Tradier is the ONLY source for today's 04:00-07:00 bars."""
    try:
        import os

        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        token = os.getenv("TRADIER_ACCESS_TOKEN") or ""
        base = (os.getenv("TRADIER_BASE_URL") or "").rstrip("/")
        if not token or not base:
            problems.append("Tradier: no token configured.")
            say("  Tradier      NOT CONFIGURED")
            return
        today = datetime.datetime.now(ET).date().isoformat()
        url = ("%s/markets/timesales?symbol=SPY&interval=5min"
               "&start=%s%%2004:00&end=%s%%2005:00" % (base, today, today))
        request = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read(1)
        say("  Tradier      OK")
    except Exception as exc:
        detail = str(exc)
        hint = ("the access token is not approved - renew it in Settings"
                if "401" in detail or "not approved" in detail.lower() else detail[:90])
        problems.append("Tradier is REFUSED: %s" % hint)
        notes.append("Premarket 04:00-07:00 will be blank: Tradier is the only "
                     "feed that carries it (Schwab starts the current day at 07:00).")
        say("  Tradier      REFUSED  (%s)" % hint)


def probe_alpaca() -> None:
    """The owner's saved Alpaca key - overnight 20:00-04:00 bars come from it."""
    try:
        from auth_service import AuthService
        from api_server import DATABASE_PATH

        service = AuthService()
        connection = sqlite3.connect(str(DATABASE_PATH), timeout=10.0)
        row = connection.execute(
            "SELECT id FROM app_users WHERE is_active = 1 "
            "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1"
        ).fetchone()
        connection.close()
        creds = service.get_provider_credentials(row[0], "alpaca_market_data") or {}
        key = str(creds.get("keyId") or creds.get("key_id") or "")
        secret = str(creds.get("secretKey") or creds.get("secret_key") or "")
        if not key or not secret:
            problems.append("Alpaca: no key saved in Settings.")
            say("  Alpaca       NOT CONFIGURED")
            return
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = StockHistoricalDataClient(key, secret)
        end = datetime.datetime.now()
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=["SPY"], timeframe=TimeFrame.Day,
            start=end - datetime.timedelta(days=6), end=end)).data
        if bars.get("SPY"):
            say("  Alpaca       OK")
        else:
            problems.append("Alpaca: the key works but returned no bars.")
            say("  Alpaca       ACCEPTED BUT EMPTY")
    except Exception as exc:
        problems.append("Alpaca is REFUSED: %s" % str(exc)[:90])
        say("  Alpaca       REFUSED  (%s)" % str(exc)[:70])


def probe_freshness() -> None:
    """Do the liquid names actually serve current candles?"""
    now = datetime.datetime.now(ET)
    stale = []
    for symbol in SAMPLE:
        try:
            with urllib.request.urlopen(
                "%s/api/oi-finder-chart?symbol=%s" % (API, symbol), timeout=120
            ) as response:
                payload = json.load(response)
        except Exception as exc:
            stale.append("%s (unreachable: %s)" % (symbol, str(exc)[:40]))
            continue
        bars = payload.get("bars") or []
        if not bars:
            stale.append("%s (no candles)" % symbol)
            continue
        newest = datetime.datetime.fromtimestamp(bars[-1]["time"], ET)
        lag = (now - newest).total_seconds() / 60.0
        say("  %-6s last candle %s  (%.0f min behind)" % (
            symbol, newest.strftime("%H:%M"), lag))
        # 45 minutes is loose on purpose: a chart nobody is watching refreshes
        # lazily by design, and flagging that would cry wolf every night.
        if lag > 45 and _market_open(now):
            stale.append("%s is %.0f minutes behind" % (symbol, lag))
    if stale:
        problems.append("Stale candles: " + "; ".join(stale))


def _market_open(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    minute = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minute < 16 * 60


def write_status(now: datetime.datetime) -> None:
    """Publish the verdict where the running app can read it.

    A log file is not a fix: nobody opens one. This lands in artifacts/ so
    api_server can serve it and the header can turn red on its own, which is
    the whole point - the failure has to come to the trader, not wait to be
    looked for.
    """
    try:
        target = ROOT / "artifacts" / "feed_health.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "checkedAt": now.isoformat(),
            "healthy": not problems,
            "problems": problems,
            "notes": notes,
        }
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(target)
    except Exception:
        # A monitor that crashes the thing it monitors is worse than no monitor.
        pass


def main() -> int:
    now = datetime.datetime.now(ET)
    say("AGX health check - %s ET" % now.strftime("%Y-%m-%d %H:%M"))
    say()
    say("FEEDS (does the provider ACCEPT the credential?)")
    probe_schwab()
    probe_tradier()
    probe_alpaca()
    say()
    say("CANDLE FRESHNESS")
    probe_freshness()
    say()
    write_status(now)
    if problems:
        say("NEEDS ATTENTION:")
        for item in problems:
            say("  - %s" % item)
        for item in notes:
            say("  note: %s" % item)
        return 1
    say("All feeds accepted and candles are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
