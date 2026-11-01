"""Read-only provenance for scanner replies; no trading or feed decisions."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math


def epoch(value):
    try:
        if isinstance(value, (int, float)):
            return float(value) if math.isfinite(value) and value > 0 else None
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def snapshot(payload, source, now=None):
    now = now or datetime.now(timezone.utc)
    stamps = []
    unknown = 0
    for row in list(payload.get("rows") or []) + list(payload.get("rest") or []):
        stamp = epoch(row.get("marketDataAt"))
        if stamp is None:
            unknown += 1
        else:
            stamps.append(stamp)
    local = now.astimezone(ZoneInfo("America/New_York"))
    overnight = local.hour >= 20 or local.hour < 4
    delay = (16 if overnight else 20) if source == "alpaca" else None
    return {
        "servedAt": now.isoformat(),
        "scanStartedAt": payload.get("scanStartedAt") or payload.get("generatedAt"),
        "scanCompletedAt": payload.get("scanCompletedAt") or payload.get("generatedAt"),
        "newestDataAt": datetime.fromtimestamp(max(stamps), timezone.utc).isoformat() if stamps else payload.get("tapeAsOf"),
        "oldestDataAt": datetime.fromtimestamp(min(stamps), timezone.utc).isoformat() if stamps else None,
        "rowsWithTime": len(stamps), "unknownTimeRows": unknown,
        "barIntervalSeconds": 300, "barTimestampRole": "start",
        "providerDelayMinutes": delay,
        "providerNote": ("Alpaca BOATS history is requested with a 16-minute delay." if overnight else
                         "Alpaca SIP history is requested with a 20-minute delay; the IEX tail has partial-market volume.")
                        if source == "alpaca" else
                        "TOS history refreshes share a rate limit; individual tickers can be older than the newest ticker. A fixed TOS entitlement delay is not confirmed."
                        + (" Overnight gaps may use Alpaca BOATS history requested with a 16-minute delay; an available newer Schwab/stream bar takes precedence." if overnight else ""),
    }
