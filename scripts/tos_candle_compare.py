"""Compare TOS's own candles with the MomX scanner's candles, candle by candle.

WHY: the scanner's rules match his TOS scan row for row, yet the results
differ, because the CANDLES differ (2026-09-29: Schwab's API served a DOCU
16:50 single-print candle his TOS chart did not draw; LASR 2h RVOL read 3.4
here vs 3.1 in TOS). Tests prove the code follows its rules; only a
candle-level diff against TOS itself can prove the inputs match.

HOW:
1. In TOS, add the CandleCheck study (TOS_STUDY below) to a chart of the
   symbol at the timeframe to check, extended hours ON. It prints the last 10
   candles: start time (ET), close, volume, and TOS's own RVOL z-score.
2. Copy the label text (or type it from a screenshot) into a text file, one
   candle per line, e.g.  ``0: 16:50 C 66.98 V 18999 R 1.2``
3. Run:
       .venv/Scripts/python.exe scripts/tos_candle_compare.py DOCU 5m tos.txt
   Timeframes: 5m 15m 30m 1h 2h 4h D. Without a TOS file it just prints our
   candles, so both sides can be captured at the same minute.

Every line is labelled MATCH / DIFF / ONLY-OURS / ONLY-TOS. Nothing here
changes the scanner; it is a measuring tool.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momx import board, feed  # noqa: E402
from momx.indicators import rvol_zscore  # noqa: E402

ET = ZoneInfo("America/New_York")
TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h", "D")
LAST_N = 10
RVOL_LENGTH = 50
#: Close must agree to the cent; volume within this percent counts as MATCH.
VOLUME_TOLERANCE_PCT = 1.0

TOS_STUDY = """# CandleCheck - last 10 candles: time(ET) close volume RVOL, for the MomX compare tool
declare upper;
def m = SecondsFromTime(0) / 60;
def hh = Floor(m / 60);
def mm = m - hh * 60;
def rv = (volume - Average(volume, 50)) / StDev(volume, 50);
AddLabel(yes, "0: " + hh + ":" + mm + " C " + AsPrice(close) + " V " + volume + " R " + Round(rv, 1), Color.WHITE);
AddLabel(yes, "1: " + hh[1] + ":" + mm[1] + " C " + AsPrice(close[1]) + " V " + volume[1] + " R " + Round(rv[1], 1), Color.WHITE);
AddLabel(yes, "2: " + hh[2] + ":" + mm[2] + " C " + AsPrice(close[2]) + " V " + volume[2] + " R " + Round(rv[2], 1), Color.WHITE);
AddLabel(yes, "3: " + hh[3] + ":" + mm[3] + " C " + AsPrice(close[3]) + " V " + volume[3] + " R " + Round(rv[3], 1), Color.WHITE);
AddLabel(yes, "4: " + hh[4] + ":" + mm[4] + " C " + AsPrice(close[4]) + " V " + volume[4] + " R " + Round(rv[4], 1), Color.WHITE);
AddLabel(yes, "5: " + hh[5] + ":" + mm[5] + " C " + AsPrice(close[5]) + " V " + volume[5] + " R " + Round(rv[5], 1), Color.WHITE);
AddLabel(yes, "6: " + hh[6] + ":" + mm[6] + " C " + AsPrice(close[6]) + " V " + volume[6] + " R " + Round(rv[6], 1), Color.WHITE);
AddLabel(yes, "7: " + hh[7] + ":" + mm[7] + " C " + AsPrice(close[7]) + " V " + volume[7] + " R " + Round(rv[7], 1), Color.WHITE);
AddLabel(yes, "8: " + hh[8] + ":" + mm[8] + " C " + AsPrice(close[8]) + " V " + volume[8] + " R " + Round(rv[8], 1), Color.WHITE);
AddLabel(yes, "9: " + hh[9] + ":" + mm[9] + " C " + AsPrice(close[9]) + " V " + volume[9] + " R " + Round(rv[9], 1), Color.WHITE);
"""

_LINE = re.compile(
    r"(?P<hh>\d{1,2})(?:\.0+)?\s*:\s*(?P<mm>\d{1,2})(?:\.0+)?\s+C\s+\$?(?P<close>[\d,]*\.?\d+)"
    r"\s+V\s+(?P<vol>[\d,]*\.?\d+)(?:\s+R\s+(?P<rv>-?[\d.]+|NaN))?",
    re.IGNORECASE,
)


def parse_tos(text: str) -> dict[str, dict]:
    """``{"HH:MM": {close, volume, rvol}}`` from CandleCheck label text."""
    out: dict[str, dict] = {}
    for match in _LINE.finditer(text):
        key = f"{int(match['hh']):02d}:{int(match['mm']):02d}"
        rv = match["rv"]
        out[key] = {
            "close": float(match["close"].replace(",", "")),
            "volume": float(match["vol"].replace(",", "")),
            "rvol": None if rv in (None, "NaN") else float(rv),
        }
    return out


def scanner_candles(symbol: str, timeframe: str) -> list[dict]:
    """The exact tape the scanner evaluates: same fetch, same bucketing."""
    five = feed.fetch_5m([symbol])
    thirty = feed.fetch_30m([symbol])
    daily = feed.fetch_daily([symbol])
    tapes = board.build_tapes(five.bars.get(symbol), thirty.bars.get(symbol), daily.bars.get(symbol))
    bars = tapes.get(timeframe) or []
    z = rvol_zscore([b["volume"] for b in bars], RVOL_LENGTH)
    for bar, value in zip(bars, z):
        bar["rvol"] = value
    return bars


def _label(bar: dict, timeframe: str) -> str:
    stamp = datetime.fromtimestamp(int(bar["time"]), ET)
    return stamp.strftime("%Y-%m-%d") if timeframe == "D" else stamp.strftime("%H:%M")


def compare(symbol: str, timeframe: str, tos: dict[str, dict] | None) -> int:
    bars = scanner_candles(symbol, timeframe)
    if not bars:
        print(f"{symbol} {timeframe}: the scanner has no candles")
        return 1
    ours = {_label(b, timeframe): b for b in bars[-LAST_N:]}
    keys = sorted(set(ours) | set(tos or {}))
    print(f"{symbol} {timeframe}  (ET, newest last; RVOL = 50-bar z on each side)")
    print(f"{'time':>10} | {'ours close':>10} {'ours vol':>11} {'ours R':>6} | "
          f"{'TOS close':>10} {'TOS vol':>11} {'TOS R':>6} | verdict")
    mismatches = 0
    for key in keys:
        a = ours.get(key)
        b = (tos or {}).get(key)
        left = (f"{a['close']:>10.2f} {a['volume']:>11,.0f} {a['rvol']:>6.1f}" if a
                else f"{'-':>10} {'-':>11} {'-':>6}")
        right = (f"{b['close']:>10.2f} {b['volume']:>11,.0f} "
                 f"{('-' if b['rvol'] is None else format(b['rvol'], '.1f')):>6}" if b
                 else f"{'-':>10} {'-':>11} {'-':>6}")
        if tos is None:
            verdict = ""
        elif a and not b:
            verdict = "ONLY-OURS"
        elif b and not a:
            verdict = "ONLY-TOS"
        else:
            same_close = abs(a["close"] - b["close"]) < 0.005
            vol_gap = abs(a["volume"] - b["volume"]) / max(b["volume"], 1.0) * 100
            verdict = "MATCH" if same_close and vol_gap <= VOLUME_TOLERANCE_PCT else (
                f"DIFF close {a['close'] - b['close']:+.2f} vol {vol_gap:.1f}%")
        if verdict and verdict != "MATCH":
            mismatches += 1
        print(f"{key:>10} | {left} | {right} | {verdict}")
    if tos is not None:
        print(f"\n{mismatches} of {len(keys)} candles differ.")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--study":
        print(TOS_STUDY)
        return 0
    if len(argv) < 3 or argv[2] not in TIMEFRAMES:
        print(__doc__)
        print("usage: tos_candle_compare.py SYMBOL {5m|15m|30m|1h|2h|4h|D} [TOS_LABELS.txt]")
        print("       tos_candle_compare.py --study   (prints the CandleCheck thinkScript)")
        return 2
    symbol = argv[1].upper()
    tos = parse_tos(Path(argv[3]).read_text(encoding="utf-8")) if len(argv) > 3 else None
    return compare(symbol, argv[2], tos)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
