"""The last hour's true high and low, for the ticker card.

2026-09-02: he wants a per-ticker card like the one in his screenshot, whose
right column reads "1-hr high 89.47 / 1-hr low 87.85".

Computed from the finest intraday tape's HIGH and LOW columns, not from
closes. Closes were the tempting shortcut - `quoteTrend` already carries 16 of
them - but the highest CLOSE of the last hour is not the hour's high, and on a
card sitting next to the last price a number that is quietly a few cents short
is worse than no number: he would read it as the level that was actually
touched.

The window is counted in BARS of whichever tape is finest, not in wall-clock
minutes, for the same reason `_finest_intraday_key` exists: a halt or a gap
makes timestamp arithmetic lie, whereas "the last 12 five-minute bars" is
exactly what the chart in front of him shows.
"""
import io

p = "momx/columns.py"
s = io.open(p, encoding="utf-8", newline="").read()

# --- how many bars make an hour, per tape ---------------------------------
OLD = "QUOTE_TREND_POINTS = 16"
NEW = '''QUOTE_TREND_POINTS = 16

#: Bars that make up one hour, per intraday tape. Counted in BARS rather than
#: minutes because a halt or a gap makes timestamp arithmetic lie, while "the
#: last 12 five-minute bars" is exactly what his chart shows.
HOUR_BARS_BY_TAPE = {"5m": 12, "15m": 4, "30m": 2, "1h": 1, "2h": 1, "4h": 1}'''
assert s.count(OLD) == 1, "points anchor"
s = s.replace(OLD, NEW)

# --- the helper ------------------------------------------------------------
OLD = '''def _finest_intraday(tapes: Any) -> list[Mapping[str, Any]]:'''
NEW = '''def hour_high_low(tapes: Any) -> dict:
    """The last hour's true high and low off the finest intraday tape.

    From the bars' HIGH and LOW, never from closes: the highest close of the
    last hour is not the hour's high, and a card printing that next to the
    last price would be quietly wrong about a level he can see was touched.

    Total: no tape, an unknown resolution, or bars without usable numbers all
    give ``{"high": None, "low": None}`` rather than raising or inventing.
    """
    key = _finest_intraday_key(tapes)
    rows = _finest_intraday(tapes)
    if not key or not rows:
        return {"high": None, "low": None}
    window = rows[-HOUR_BARS_BY_TAPE.get(key, 1):]
    highs: list[float] = []
    lows: list[float] = []
    for bar in window:
        if not isinstance(bar, Mapping):
            continue
        for field, sink in (("high", highs), ("low", lows)):
            try:
                value = float(bar.get(field))
            except (TypeError, ValueError):
                continue
            if value == value:            # not NaN
                sink.append(value)
    return {
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
    }


def _finest_intraday(tapes: Any) -> list[Mapping[str, Any]]:'''
assert s.count(OLD) == 1, "helper anchor"
s = s.replace(OLD, NEW)

# --- on the row ------------------------------------------------------------
OLD = '''        "quoteTrend": quote_trend(intraday),
        "sparkline": sparkline(intraday),
    }'''
NEW = '''        "quoteTrend": quote_trend(intraday),
        "sparkline": sparkline(intraday),
        # For the ticker card's right-hand column. Cheap: the tape is already
        # resolved above and this is one pass over at most twelve bars.
        "hourHighLow": hour_high_low(tapes),
    }'''
assert s.count(OLD) == 1, "row anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("momx/columns.py: hourHighLow on every row")
