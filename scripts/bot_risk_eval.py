"""Nightly: did the BOT trades that pass his 2x-expected-move rule lose less?

His rule (2026-10-03): "fewer losses - exp move 2x and ROI at least 100", then "don't skip
it, just trade - Jev will improve". The bot takes EVERY trade; momx/option_track.py
risk_check marks each one riskOk when its option would be worth >= 2x its cost if the
stock reached 2x the 1-day expected move from the entry price. This script compares the
two groups on what the option actually did - per trade AND per stock-day (one stock
firing several rules is one bet, not several) - and splits the days in half so a result
that only holds in one half is visible.

Reads artifacts/momx_option_track/<day>.json (calls) and artifacts/bear/... (puts).
Skips badQuote trades (stale chain copies). Read-only toward the app.

    python scripts/bot_risk_eval.py            # every saved day
    python scripts/bot_risk_eval.py 2026-10-05 # one day
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from momx.option_track import risk_check  # noqa: E402

SOURCES = (("bull", ROOT / "artifacts" / "momx_option_track"), ("bear", ROOT / "artifacts" / "bear" / "momx_option_track"))


def load(side: str, folder: Path, only: str | None) -> list[dict]:
    out = []
    for f in sorted(folder.glob("20*.json")):
        day = f.stem
        if only and day != only:
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for t in data.get("trades", []) if isinstance(data, dict) else data:
            entry, exit_ = t.get("entry"), t.get("exitPrice")
            if t.get("badQuote") or not entry or exit_ is None:
                continue
            ok = t.get("riskOk")
            if ok is None:
                ok = risk_check(t.get("spot"), t.get("expectedMove"), t.get("strike"), entry, side == "bear").get("riskOk")
            if ok is None:
                continue
            out.append({"day": day, "symbol": t.get("symbol"), "rule": t.get("rule"), "ok": ok,
                        "ret": float(exit_) / float(entry) - 1, "best": float(t.get("best") or entry) / float(entry) - 1})
    return out


def group(rows: list[dict]) -> str:
    if not rows:
        return "n=0"
    rets = [r["ret"] for r in rows]
    wins = sum(x > 0 for x in rets)
    return (f"n={len(rows):5d} win {100 * wins / len(rows):5.1f}% | avg {100 * st.mean(rets):+6.1f}% | "
            f"median {100 * st.median(rets):+6.1f}% | lost half+ {100 * sum(x <= -0.5 for x in rets) / len(rows):4.1f}%")


def per_stock_day(rows: list[dict]) -> list[dict]:
    seen: dict = {}
    for r in rows:                       # first trade per stock per day
        seen.setdefault((r["day"], r["symbol"]), r)
    return list(seen.values())


def main(only: str | None) -> None:
    print("BOT 2x-EXPECTED-MOVE CHECK (option % at the bot's exit; badQuote trades left out)")
    for side, folder in SOURCES:
        rows = load(side, folder, only)
        if not rows:
            print(f"  {side.upper()}: no priced trades")
            continue
        days = sorted({r["day"] for r in rows})
        print(f"  {side.upper()} ({'calls' if side == 'bull' else 'puts'}) {days[0]}..{days[-1]} ({len(days)} day(s))")
        for name, subset in (("every trade", rows), ("one per stock-day", per_stock_day(rows))):
            print(f"    {name}:")
            print(f"      PASS 2xEM >= +100%: {group([r for r in subset if r['ok']])}")
            print(f"      FAIL             : {group([r for r in subset if not r['ok']])}")
        if len(days) >= 2:
            half = days[len(days) // 2]
            for label, part in (("first half", [r for r in rows if r["day"] < half]), ("second half", [r for r in rows if r["day"] >= half])):
                sd = per_stock_day(part)
                print(f"    {label} (stock-days): PASS {group([r for r in sd if r['ok']])}")
                print(f"    {' ' * len(label)}              FAIL {group([r for r in sd if not r['ok']])}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
