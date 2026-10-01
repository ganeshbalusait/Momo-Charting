"""Record WHERE the premarket scanner's minutes go, during a live session.

The question this exists to answer, measured 2026-09-01: AAPL's 2H cross became
true at 09:25 and its row reached the board at 09:39:47 -- ~14 minutes. META was
worse: a 09:00 bucket signal that first appeared at 10:55. Both are ours, and
neither can be diagnosed after the fact because the code path that feeds the
scanner only runs 00:00-10:00 ET on weekdays.

So this samples the live endpoint through a real premarket and writes one JSON
line per symbol per poll. Nothing is inferred here: the payload already carries
tapeAgeSeconds / tapeNewestAt per row, so the log records observations and the
analysis happens afterwards against a file that cannot change under it.

Deliberately a READER. It polls the same endpoint a browser polls and writes to
artifacts/; it never touches a cache, never forces a refresh, and cannot make
the thing it is measuring slower. The 30s cadence is well under the endpoint's
own 4s response cache, so it adds no build load either.

Run:  .venv/Scripts/python.exe scripts/premarket_latency_log.py
It exits on its own at the end of the window.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ENDPOINT = os.environ.get(
    "AGX_PREMARKET_URL", "http://127.0.0.1:3002/api/premarket-scanner"
)
OUT = Path("artifacts") / ("premarket_latency_%s.jsonl" % datetime.now(ET).strftime("%Y%m%d"))

#: Start before the 06:00 window so the first sample predates any signal, and
#: run past the open so a row that arrives late is still captured.
START_MINUTE = 5 * 60 + 45          # 05:45 ET
END_MINUTE = 10 * 60 + 5            # 10:05 ET
POLL_SECONDS = 30.0


def now_et() -> datetime:
    return datetime.now(ET)


def minute_of_day(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def fetch() -> dict | None:
    try:
        with urllib.request.urlopen(ENDPOINT, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - a failed poll is a data point
        return {"_error": "%s: %s" % (type(exc).__name__, exc)}


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print("logging to %s" % OUT)

    # Wait for the window rather than assuming the caller timed it.
    while minute_of_day(now_et()) < START_MINUTE:
        if now_et().weekday() >= 5:
            print("weekend - nothing to measure")
            return 0
        time.sleep(30)

    samples = 0
    while minute_of_day(now_et()) < END_MINUTE:
        moment = now_et()
        payload = fetch() or {}
        rows = payload.get("rows") or []
        record = {
            "at": moment.isoformat(),
            "error": payload.get("_error"),
            "status": payload.get("status"),
            "matchCount": payload.get("matchCount"),
            "ready": payload.get("readySymbols"),
            "pending": payload.get("pendingSymbols"),
            "stale": payload.get("staleSymbols"),
            "lagging": payload.get("laggingSymbols"),
            "rows": [
                {
                    "symbol": row.get("symbol"),
                    # The two that answer "how late was it?"
                    "firstSeenAt": row.get("firstSeenAt"),
                    "latestSignalAt": row.get("latestSignalAt"),
                    # The two that answer "why?"
                    "tapeNewestAt": row.get("tapeNewestAt"),
                    "tapeAgeSeconds": row.get("tapeAgeSeconds"),
                    "tapeCoversWindow": row.get("tapeCoversWindow"),
                    "signals48": row.get("signals48"),
                    "signals920": row.get("signals920"),
                }
                for row in rows
            ],
        }
        with OUT.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        samples += 1
        if samples % 20 == 0:
            print("  %s  %d samples, %d rows" % (moment.strftime("%H:%M:%S"), samples, len(rows)))
        time.sleep(POLL_SECONDS)

    print("done: %d samples -> %s" % (samples, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
