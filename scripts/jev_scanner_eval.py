"""Daily check: did the scanner tickers Jev scored TAKE beat the ones it skipped? (his ask
2026-10-03: "let it be there, want to see how scanner and Jev AI perform").

Reads the day's Jev scanner scores (artifacts/momx_jev_scanner/<day>.jsonl, bull; and
artifacts/bear/... for puts), keeps scores made 09:30-15:00 ET, one per ticker per 30
minutes, and measures the STOCK from the first 5m candle after the score:
  good   = reached +2% within 60 min before falling 1% (puts: -2% before +1%)
  mirror = the opposite move first (catches "jumpy" picks that are not directional)
  net 60 min and to the 15:55 close, in the trade's direction.
Groups: Jev >= 30% (take), 20-30%, under 20% (skip). Appends one line per day to
artifacts/jev_scanner_eval/history.jsonl and prints the day plus running totals.
Alpaca 5m bars only (never Schwab). Read-only toward the app.

    python scripts/jev_scanner_eval.py [YYYY-MM-DD]      (default: today ET)
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
ET = ZoneInfo("America/New_York")
OUT = ROOT / "artifacts" / "jev_scanner_eval"
SOURCES = (("bull", ROOT / "artifacts" / "momx_jev_scanner"), ("bear", ROOT / "artifacts" / "bear" / "momx_jev_scanner"))
GROUPS = (("take >=30%", 0.30, 9.0), ("20-30%", 0.20, 0.30), ("skip <20%", -1.0, 0.20))


def scores(folder: Path, day: str) -> list[dict]:
    f = folder / f"{day}.jsonl"
    if not f.exists():
        return []
    out, last = [], {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            t = datetime.fromisoformat(r["at"]).astimezone(ET)
        except (ValueError, KeyError):
            continue
        m = t.hour * 60 + t.minute
        if not 570 <= m <= 900 or t.date().isoformat() != day:
            continue
        prev = last.get(r["symbol"])
        if prev and (t - prev).total_seconds() < 1800:
            continue
        last[r["symbol"]] = t
        out.append({**r, "t": t})
    return out


def bars_for(symbols: list[str], day: str) -> dict:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from momx import feed
    out = {}
    for i in range(0, len(symbols), 50):
        res = feed.fetch_5m(symbols[i:i + 50], days=3, schwab_volume=False, use_cache=False)
        for s, df in res.bars.items():
            d = df.reset_index()
            cols = {str(c).lower(): c for c in d.columns}
            tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
            rows = []
            for r in d.to_dict("records"):
                tv = r[tcol]
                tv = tv.timestamp() if hasattr(tv, "timestamp") else float(tv)
                if tv > 1e12:
                    tv /= 1000
                t = datetime.fromtimestamp(tv, ET)
                if t.date().isoformat() == day and 570 <= t.hour * 60 + t.minute < 960:
                    rows.append((t, float(r[cols["open"]]), float(r[cols["high"]]), float(r[cols["low"]]), float(r[cols["close"]])))
            out[s] = sorted(rows)
    return out


def outcome(bars: list, t: datetime, up: bool) -> dict | None:
    start = t.replace(second=0, microsecond=0) + timedelta(minutes=(5 - t.minute % 5) % 5)
    idx = next((i for i, b in enumerate(bars) if b[0] >= start), None)
    if idx is None:
        return None
    e = bars[idx][1]
    w = [b for b in bars[idx:] if b[0] < bars[idx][0] + timedelta(minutes=60)]
    sign = 1 if up else -1

    def first(target_hi, target_lo, want_hi):
        for b in w:
            hi, lo = b[2] >= target_hi, b[3] <= target_lo
            if want_hi:
                if lo:
                    return False
                if hi:
                    return True
            else:
                if hi:
                    return False
                if lo:
                    return True
        return False
    good = first(e * 1.02, e * 0.99, True) if up else first(e * 1.01, e * 0.98, False)
    mirror = first(e * 1.01, e * 0.98, False) if up else first(e * 1.02, e * 0.99, True)
    close = next((b[4] for b in reversed(bars) if b[0].hour * 60 + b[0].minute <= 955), bars[-1][4])
    return {"good": good, "mirror": mirror, "r60": sign * (w[-1][4] / e - 1) * 100, "rclose": sign * (close / e - 1) * 100}


def summary(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return {"n": len(rows), "good": round(100 * st.mean(r["good"] for r in rows), 1),
            "mirror": round(100 * st.mean(r["mirror"] for r in rows), 1),
            "r60": round(st.mean(r["r60"] for r in rows), 3), "up60": round(100 * st.mean(r["r60"] > 0 for r in rows), 1),
            "rclose": round(st.mean(r["rclose"] for r in rows), 3)}


def main(day: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    day_rec = {"day": day}
    print(f"JEV SCANNER CHECK {day} (stock moves; good = +2% before -1% in 60 min, mirror = the opposite first)")
    for side, folder in SOURCES:
        sc = scores(folder, day)
        if not sc:
            print(f"  {side.upper()}: no Jev scanner scores recorded 09:30-15:00")
            continue
        bars = bars_for(sorted({s["symbol"] for s in sc}), day)
        rows = []
        for s in sc:
            o = outcome(bars.get(s["symbol"]) or [], s["t"], side == "bull")
            if o:
                rows.append({**o, "p": s["p"]})
        day_rec[side] = {}
        for name, lo, hi in GROUPS:
            g = summary([r for r in rows if lo <= r["p"] < hi])
            day_rec[side][name] = g
            if g:
                print(f"  {side.upper():4s} {name:10s} n={g['n']:4d} | good {g['good']:5.1f}% mirror {g['mirror']:5.1f}% | "
                      f"net 60m {g['r60']:+.2f}% (up {g['up60']:.0f}%) | to close {g['rclose']:+.2f}%")
    hist = OUT / "history.jsonl"
    lines = [ln for ln in (hist.read_text(encoding="utf-8").splitlines() if hist.exists() else []) if f'"day": "{day}"' not in ln]
    lines.append(json.dumps(day_rec))
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    days = [json.loads(ln) for ln in lines if ln.strip()]
    print(f"RUNNING ({len(days)} day(s), weighted by trades):")
    for side in ("bull", "bear"):
        for name, _, _ in GROUPS:
            pts = [d[side][name] for d in days if d.get(side) and d[side].get(name)]
            n = sum(p["n"] for p in pts)
            if not n:
                continue
            w = lambda k: sum(p[k] * p["n"] for p in pts) / n  # noqa: E731
            print(f"  {side.upper():4s} {name:10s} n={n:5d} | good {w('good'):5.1f}% mirror {w('mirror'):5.1f}% | net 60m {w('r60'):+.2f}% | to close {w('rclose'):+.2f}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat())
