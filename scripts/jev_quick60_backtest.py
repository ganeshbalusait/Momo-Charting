"""Back-test: can Jev spot the QUICK option trade - "+2% within 60 minutes before -1%"?

His ask 2026-10-03 (NBIS Fri: CALL4H at 09:28 $238 -> $248 in ~75 min, "people made quick
100%"; Jev scored it 0-11%). The production question asks "take or stand aside" in general;
this one names the exact quick-trade outcome, and is scored on exactly that outcome.

Sample: the MomX History snapshots (bull Watchlist board) 09:30-11:30 ET, one per ticker
per 30 minutes - the row exactly as the scanner had it at that moment (no hindsight).
Each sample gets ONE Jev call carrying the production questions (take %) AND the new one
(quick %). Outcome from Alpaca 5m bars (never Schwab), first bar after the snapshot:
  good   = +2% before -1% within 60 min      mirror = -2% before +1% (the "jumpy" control)
Cut-offs are chosen on the EARLIER half of the days and judged on the LATER half.

    python scripts/jev_quick60_backtest.py [first_day] [last_day] [max_samples]
Results: artifacts/jev_quick60/<run>.jsonl (one line per sample) + printed summary.
"""
from __future__ import annotations

import json
import os
import random
import statistics as st
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
ET = ZoneInfo("America/New_York")
OUT = ROOT / "artifacts" / "jev_quick60"
HIST = ROOT / "artifacts" / "momx_history" / "Watchlist"

QUICK = {
    "type": "choice",
    "instructions": (
        "A trader wants to buy a short-dated call option on this stock right now for a QUICK trade: "
        "in and out within the hour. Using only the facts above, will the stock rise at least 2% from "
        "its current price within the next 60 minutes, before it falls 1%?"),
    "criteria": {
        "yes": "Within the next 60 minutes the stock gains 2% or more before it drops 1% - the fast move "
               "keeps going and the call can double.",
        "no": "Within the next 60 minutes the stock stalls, chops, or drops 1% before it gains 2%.",
    },
}


def samples(first: str, last: str) -> list[dict]:
    out = []
    for f in sorted(HIST.glob("2026-*.json")):
        day = f.stem
        if not first <= day <= last:
            continue
        try:
            rows = json.loads(f.read_text(encoding="utf-8")).get("rows") or {}
        except (OSError, ValueError):
            continue
        for symbol, rec in rows.items():
            last_slot = None
            for snap in (rec or {}).get("snapshots") or []:
                at = snap.get("at") or snap.get("seenAt")
                row = snap.get("row") or snap
                try:
                    t = datetime.fromisoformat(at).astimezone(ET)
                except (TypeError, ValueError):
                    continue
                m = t.hour * 60 + t.minute
                if t.date().isoformat() != day or not 570 <= m <= 690 or row.get("etf"):
                    continue
                slot = m // 30
                if slot == last_slot:
                    continue
                last_slot = slot
                out.append({"day": day, "symbol": symbol, "at": at, "t": t, "row": row})
    return out


def main(first: str, last: str, cap: int) -> None:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from momx.jev_trade import API_URL, MODEL, _default_post, describe, parse, questions  # noqa: F401
    from jev_scanner_eval import outcome

    pool = samples(first, last)
    random.Random(20261003).shuffle(pool)          # deterministic, spread over days and tickers
    pool = pool[:cap]
    print(f"{len(pool)} samples {first}..{last} (cap {cap})", flush=True)
    days = sorted({s["day"] for s in pool})
    bars = all_bars(sorted({s["symbol"] for s in pool}), days)
    key = (os.getenv("TYPESAFE_API_KEY") or "").strip()
    if not key:
        print("no TYPESAFE_API_KEY - stop")
        return
    qs = {**questions("bull"), "quick": QUICK}
    OUT.mkdir(parents=True, exist_ok=True)
    run = OUT / f"{first}_{last}.jsonl"

    def one(s: dict) -> dict | None:
        o = outcome(bars[s["day"]].get(s["symbol"]) or [], s["t"], True)
        if not o:
            return None
        trade = {"rule": "scanner", "symbol": s["symbol"], "at": s["at"], "stockPrice": s["row"].get("last")}
        try:
            state = describe(trade, s["row"], None, "bull", s["day"])
            resp = _default_post({"model": MODEL, "state": state, "questions": qs}, key)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}
        p = parse(resp)
        quick = ((resp.get("answers") or {}).get("quick") or {}).get("probabilities") or {}
        return {"day": s["day"], "symbol": s["symbol"], "at": s["at"], "take": p.get("jevP"), "quick": quick.get("yes"),
                "good": o["good"], "mirror": o["mirror"], "r60": o["r60"]}

    with ThreadPoolExecutor(max_workers=4) as ex:
        res = [r for r in ex.map(one, pool) if r]
    errors = [r for r in res if "error" in r]
    res = [r for r in res if "error" not in r and r["take"] is not None and r["quick"] is not None]
    run.write_text("\n".join(json.dumps(r) for r in res) + "\n", encoding="utf-8")
    print(f"scored {len(res)} ({len(errors)} errors{': ' + errors[0]['error'] if errors else ''}) -> {run}")
    report(res)


def all_bars(symbols: list[str], days: list[str]) -> dict:
    """{day: {symbol: [(t, o, h, l, c), ...]}} 09:30-16:00 ET from Alpaca 5m bars, fetched ONCE
    far enough back for the oldest day (jev_scanner_eval.bars_for only reaches 3 days)."""
    from momx import feed
    back = (datetime.now(ET).date() - datetime.fromisoformat(days[0]).date()).days + 3
    out = {d: {} for d in days}
    for i in range(0, len(symbols), 50):
        res = feed.fetch_5m(symbols[i:i + 50], days=back, schwab_volume=False, use_cache=False)
        for sym, df in res.bars.items():
            d = df.reset_index()
            cols = {str(c).lower(): c for c in d.columns}
            tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
            for r in d.to_dict("records"):
                tv = r[tcol]
                tv = tv.timestamp() if hasattr(tv, "timestamp") else float(tv)
                if tv > 1e12:
                    tv /= 1000
                t = datetime.fromtimestamp(tv, ET)
                day = t.date().isoformat()
                if day in out and 570 <= t.hour * 60 + t.minute < 960:
                    out[day].setdefault(sym, []).append((t, float(r[cols["open"]]), float(r[cols["high"]]),
                                                         float(r[cols["low"]]), float(r[cols["close"]])))
    for day in out:
        for sym in out[day]:
            out[day][sym].sort()
    return out


def rate(rows: list[dict]) -> str:
    if not rows:
        return "n=0"
    return (f"n={len(rows):4d} good {100 * st.mean(r['good'] for r in rows):5.1f}% "
            f"mirror {100 * st.mean(r['mirror'] for r in rows):5.1f}% net60 {st.mean(r['r60'] for r in rows):+.2f}%")


def report(res: list[dict]) -> None:
    days = sorted({r["day"] for r in res})
    if len(days) < 2:
        print("need 2+ days")
        return
    half = days[len(days) // 2]
    train = [r for r in res if r["day"] < half]
    test = [r for r in res if r["day"] >= half]
    print(f"EARLIER {days[0]}..{days[len(days) // 2 - 1]}  LATER {half}..{days[-1]}")
    print(f"  everything      EARLIER {rate(train)}")
    print(f"                  LATER   {rate(test)}")
    for field in ("take", "quick"):
        # cut-off = the top 20% of scores on the EARLIER days, then applied unchanged to LATER
        cut = sorted(r[field] for r in train)[int(0.8 * len(train))] if train else 1.0
        print(f"  Jev {field:5s} >= {cut:.2f} (top 20% of earlier days)")
        print(f"      EARLIER pick {rate([r for r in train if r[field] >= cut])} | rest {rate([r for r in train if r[field] < cut])}")
        print(f"      LATER   pick {rate([r for r in test if r[field] >= cut])} | rest {rate([r for r in test if r[field] < cut])}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "2026-09-22", a[1] if len(a) > 1 else "2026-10-02", int(a[2]) if len(a) > 2 else 400)
