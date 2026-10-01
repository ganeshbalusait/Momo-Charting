"""Phone alert a day before a Schwab login expires, and once when it has.

Schwab refresh tokens die exactly 7 days after login. On 2026-09-03 the
market-data one expired at 19:51 ET with nobody watching Settings; the charts
went dark overnight and the recovery took until 01:00. The header lamps now
carry a countdown, but a countdown is only seen while the app is open. This
is the part that reaches the phone.

Pure: no clock, no disk, no network. api_server owns the loop, the ledger
file and the push. ``due`` decides; everything else is plumbing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

#: Alert when this much (or less) of the login remains.
WARN_SECONDS = 24 * 3600

LABELS = {
    "market_data": "TOS MARKET (Market Data - charts, quotes, chains)",
    "trading": "TOS TRADING (Accounts & Trading - live ticks)",
}


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ledger_key(profile: str, saved_at: str, kind: str) -> str:
    """One alert of each kind per LOGIN, not per day: keyed on the login stamp
    so a re-authentication arms a fresh warning and a restart never repeats
    one already sent."""
    return f"{profile}:{saved_at}:{kind}"


def due(statuses: dict, ledger: dict, now: datetime | None = None) -> list[dict]:
    """Which alerts should go out right now.

    ``statuses`` maps profile -> SchwabClient.connection_status() (only
    ``refreshTokenExpiresAt`` and ``tokenSavedAt`` are read). ``ledger`` holds
    the keys already sent. Returns entries carrying the ledger key to record
    AFTER a successful push, plus the phone title/body.
    """
    now = now or datetime.now(timezone.utc)
    out = []
    for profile, status in (statuses or {}).items():
        if not isinstance(status, dict):
            continue
        expires = _parse(status.get("refreshTokenExpiresAt"))
        saved = str(status.get("tokenSavedAt") or "")
        if expires is None or not saved:
            continue
        label = LABELS.get(profile, profile)
        when = expires.astimezone(ET).strftime("%a %b %d, %I:%M %p ET").replace(" 0", " ")
        remaining = (expires - now).total_seconds()
        if remaining <= 0:
            key = ledger_key(profile, saved, "expired")
            if key not in ledger:
                out.append({
                    "key": key,
                    "title": "Schwab login EXPIRED",
                    "body": f"{label} login expired {when}. Its data is down until you re-authenticate in AGX Settings.",
                })
            continue
        if remaining <= WARN_SECONDS:
            key = ledger_key(profile, saved, "day")
            if key not in ledger:
                hours = max(1, int(remaining // 3600))
                out.append({
                    "key": key,
                    "title": f"Schwab login expires in {hours}h",
                    "body": f"{label} login expires {when}. Re-authenticate in AGX Settings before then or its data goes dark.",
                })
    return out
