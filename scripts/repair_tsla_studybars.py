"""One-shot repair: TSLA's deep study tape was 1-min-flooded (576k rows).

The 2026-08-24 04:00 storm build wrote TSLA's studyBars at 1-minute spacing
(575,971 rows vs AMZN's 13,343), which made every TSLA serve re-gzip a
~576k-row array (13-23s serves) and the browser chart ship megabytes.
This aggregates the whole tape to proper 30-minute buckets using the same
parity function the scanner uses (premarket_scanner.aggregate_chart_bars),
backs up the bloated file, and writes the repaired payload for the next
boot to hydrate. Run from the repo root with the venv python.
"""
import gzip
import json
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")
from premarket_scanner import aggregate_chart_bars  # noqa: E402

ET = ZoneInfo("America/New_York")
PATH = "artifacts/oi_chart_cache/TSLA.json.gz"
BACKUP = "artifacts/_quarantine/TSLA.pre-aggregation.json.gz"


def main() -> None:
    raw = open(PATH, "rb").read()
    payload = json.loads(gzip.decompress(raw))
    study = payload.get("studyBars") or []
    print("before:", len(study))
    if len(study) < 50_000:
        print("tape already sane; refusing to touch it")
        return
    open(BACKUP, "wb").write(raw)
    rebuilt = aggregate_chart_bars(study, 30)
    first = datetime.fromtimestamp(int(rebuilt[0]["time"]), tz=timezone.utc).astimezone(ET)
    last = datetime.fromtimestamp(int(rebuilt[-1]["time"]), tz=timezone.utc).astimezone(ET)
    print("after:", len(rebuilt), "| range:", first.strftime("%Y-%m-%d"), "->", last.strftime("%a %H:%M"))
    payload["studyBars"] = rebuilt
    open(PATH, "wb").write(gzip.compress(json.dumps(payload).encode(), 1))
    print("written (AMZN reference: 13343 rows)")


if __name__ == "__main__":
    main()
