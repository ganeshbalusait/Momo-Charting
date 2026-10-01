"""Re-runnable back-test of the Draft 2 scanner grade over the MomX History archive.

    .venv/Scripts/python.exe scripts/momx_grade_backtest.py [Watchlist|Mag7] \
        [--history-dir PATH]

Uses momx/grade.py itself, so the app and this report can never disagree.
Limits (printed with the result): the archive only holds tickers while they
matched the scan, snapshots cap at 120/ticker/day, and the outcome is to the
close only - not a profitability measure.

``--direction bear`` (spec 2026-09-24) grades the BEAR rule over the bear
History root (artifacts/bear/momx_history/<board>) and scores a DROP as the
win, writing artifacts/bear/momx_grade_record_backtest.json.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from momx import grade  # noqa: E402

MIN_SNAPSHOTS_FOR_CLOSE = 500
SESSION = ("09:30", "15:30")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("rows", {})


def _prev_closes(rows: dict) -> dict:
    out = {}
    for symbol, rec in rows.items():
        for snap in rec.get("snapshots", []):
            r = snap.get("row", {})
            last, pct = r.get("last"), r.get("pctChange")
            if last and pct is not None:
                out[symbol] = last / (1 + pct / 100.0)
                break
    return out


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "pctHigherClose": round(100.0 * sum(v > 0 for v in values) / len(values), 1),
        "avgToClose": round(statistics.mean(values), 3),
        "medianToClose": round(statistics.median(values), 3),
        "up3": sum(v >= 3 for v in values),
        "down3": sum(v <= -3 for v in values),
    }


def _is_bear(direction) -> bool:
    return str(direction or "").strip().lower() == "bear"


def default_history_dir(board: str, direction: str = "bull") -> Path:
    base = ROOT / "artifacts" / "bear" if _is_bear(direction) else ROOT / "artifacts"
    return base / "momx_history" / board


def record_target(direction: str = "bull") -> Path:
    base = ROOT / "artifacts" / "bear" if _is_bear(direction) else ROOT / "artifacts"
    return base / "momx_grade_record_backtest.json"


def run(history_dir: Path, days: list[str] | None = None, direction: str = "bull") -> dict:
    bear = _is_bear(direction)
    sign = -1.0 if bear else 1.0
    files = sorted(history_dir.glob("*.json"))
    all_stems = [p.stem for p in files]
    names = list(all_stems)
    if days:
        names = [d for d in names if d in days]
    events = []
    for day in names:
        rows = _load(history_dir / f"{day}.json")
        # A day is only a real trading session if its own file has enough
        # snapshots - the same "real session" bar used below to find the
        # next close. This excludes weekends/holidays (e.g. thin files with
        # a stray event) without needing a holiday calendar.
        if sum(len(r.get("snapshots", [])) for r in rows.values()) < MIN_SNAPSHOTS_FOR_CLOSE:
            continue
        closes = None
        for later in all_stems[all_stems.index(day) + 1:]:
            nrows = _load(history_dir / f"{later}.json")
            if sum(len(r.get("snapshots", [])) for r in nrows.values()) >= MIN_SNAPSHOTS_FOR_CLOSE:
                closes = _prev_closes(nrows)
                break
        if closes is None:
            continue
        for symbol, rec in rows.items():
            seen = set()
            for snap in rec.get("snapshots", []):
                try:
                    at = snap["at"]
                    ts = datetime.fromisoformat(at)
                except (ValueError, TypeError, KeyError):
                    continue
                hhmm = at[11:16]
                if not (SESSION[0] <= hhmm <= SESSION[1]):
                    continue
                g = grade.safe_grade_row(snap.get("row"), ts, "bear" if bear else "bull")
                letter = g and g["letter"]
                price = (snap.get("row") or {}).get("last")
                if not letter or letter in seen or not price or symbol not in closes:
                    continue
                seen.add(letter)
                close = closes[symbol]
                events.append({"day": day, "time": hhmm, "symbol": symbol, "letter": letter,
                               "price": price, "close": close,
                               # In the trade's favour: a drop is the bear win.
                               "toClose": round(sign * (close / price - 1) * 100, 3)})
    letters = {L: _stats([e["toClose"] for e in events if e["letter"] == L]) for L in ("A+", "A", "B")}
    return {"days": sorted({e["day"] for e in events}), "letters": letters, "events": events,
            "direction": "bear" if bear else "bull"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", nargs="?", default="Watchlist")
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=None,
        help='Directory of daily History JSON files (default: ROOT/"artifacts"/"momx_history"/<board>)',
    )
    parser.add_argument("--direction", choices=("bull", "bear"), default="bull",
                        help="bear = the BEAR grade over the bear History root, drops scored as wins")
    args = parser.parse_args()

    history_dir = args.history_dir if args.history_dir is not None else default_history_dir(args.board, args.direction)
    out = run(history_dir, direction=args.direction)
    out["source"] = f"back-test from History archive ({len(out['days'])} days, {args.board}, {args.direction})"
    target = record_target(args.direction)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, target)
    for letter, s in out["letters"].items():
        print(letter, s)
    print("limits: scan-matched tickers only; 120 snapshots/ticker/day cap; to-close only")


if __name__ == "__main__":
    main()
