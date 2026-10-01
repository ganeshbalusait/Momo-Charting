"""MomX scanner grade - Draft 2 baseline (experimental).

Spec: docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md.
"A+" means the defined conditions align. It is not a recommendation and does
not guarantee profit. The rule is frozen as back-tested; change it only as a
new draft compared against this one on recorded data.

Colour meanings are momx/columns.py's, not ours:
  SQZ bg cyan  = medium squeeze released, momentum rising (lights 2 bars)
  SQZ bg orange= medium squeeze ON, or a high-compression squeeze just fired
  SKIT fg cyan / dark_green = EMA9 > EMA20 (dark green: and FastD >= 90)
  SKIT bg cyan/green/lime/dark_green = a bullish cross on this bar
  RVOL bg cyan (z>=3) / green (z>=2) with the bar closing in its upper half
  H/L bg green = close above the midpoint of the last 8 two-hour bars
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

SQZ_TFS = ("4h", "D", "Wk")
PUSH_RVOL_TFS = ("5m", "15m", "30m", "1h", "2h", "4h")
SKIT_TFS = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
NEWS_MAX_HOURS = 12
A_PLUS_SKIT = 7
A_SKIT = 6
B_SKIT = 5
LETTER_RANK = {"A+": 3, "A": 2, "B": 1, None: 0}

RVOL_BULL_BG = frozenset({"cyan", "green"})
SKIT_TREND_FG = frozenset({"cyan", "dark_green"})
#: Bullish CROSS backgrounds. Every one of these is a cross CONFIRMED by the
#: EMA state - read columns.skittles_cell: "cyan" is exu1 (EMA9 crossed above
#: EMA20), "green" is macd21 AND exu, "lime" is exu2 AND exu. All three require
#: EMA9 > EMA20 on that bar.
#:
#: "dark_green" was in this set until 2026-09-22 and is the one that is NOT.
#: It is reached only by the bare `elif macd21:` branch, AFTER "green" has
#: already claimed the with-trend case - so bg dark_green means exactly "MACD
#: crossed up while EMA9 is NOT above EMA20", a cross against the prevailing
#: EMA state. The cell's own FOREGROUND says so: that same condition paints
#: fg "downtick". Counting it bullish let a timeframe score for the grade
#: while its EMA state was down, which was the only channel by which a
#: downtrending ticker could reach A+ at all (20 of one day's 78 A+ events
#: held at least one such cell; 13 of that day's 18 "downtrend A+").
#:
#: Measured over the 23-file archive, 204,075 snapshots: A+ 19,073 -> 16,758
#: (-12.1%), A -20.5%, B -22.8%, 5.25% of all snapshots change letter. This
#: TIGHTENS the gate; it does not re-rank anything.
SKIT_CROSS_BG = frozenset({"cyan", "green", "lime"})
TF_LABEL = {"Wk": "W", "M": "Mo"}

# --- BEAR (spec 2026-09-24): the same rule on the bearish paints -------------
#: RVOL bg magenta (z>=3) / red (z>=2) with the bar closing in its LOWER half.
RVOL_BEAR_BG = frozenset({"magenta", "red"})
#: SKIT fg magenta = EMA9 < EMA20; plum = that and FastD <= 10 (mirror of dark_green).
SKIT_BEAR_TREND_FG = frozenset({"magenta", "plum"})
#: Bearish CROSS backgrounds that REQUIRE EMA9 < EMA20 on the bar: magenta
#: (9x20 down), red (MACD down with exd), light_red (4x8 down with exd). bg
#: "plum" is the bare `elif macd21d:` branch - MACD crossed down while EMA9 is
#: NOT below EMA20 - and is excluded for the same reason bg dark_green was
#: dropped from the bull set on 2026-09-22.
SKIT_BEAR_CROSS_BG = frozenset({"magenta", "red", "light_red"})
SQZ_FIRED_BG = {"bull": "cyan", "bear": "magenta"}
HL_BG = {"bull": "green", "bear": "red"}


def is_bear(direction) -> bool:
    return str(direction or "").strip().lower() == "bear"


def sets_for(direction="bull") -> dict:
    """The colour sets one direction grades on. Read by grade_log too."""
    if is_bear(direction):
        return {"rvol_bg": RVOL_BEAR_BG, "skit_fg": SKIT_BEAR_TREND_FG, "skit_bg": SKIT_BEAR_CROSS_BG,
                "sqz_fired": SQZ_FIRED_BG["bear"], "hl_bg": HL_BG["bear"], "direction": "bear"}
    return {"rvol_bg": RVOL_BULL_BG, "skit_fg": SKIT_TREND_FG, "skit_bg": SKIT_CROSS_BG,
            "sqz_fired": SQZ_FIRED_BG["bull"], "hl_bg": HL_BG["bull"], "direction": "bull"}


def _cell(row: Mapping, section: str, tf: str) -> Mapping:
    part = row.get(section)
    cell = part.get(tf) if isinstance(part, Mapping) else None
    return cell if isinstance(cell, Mapping) else {}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _news_age_hours(row: Mapping, now: datetime) -> float | None:
    news = row.get("news")
    if not isinstance(news, Mapping) or not news.get("headline") or not news.get("at"):
        return None
    try:
        published = _aware(datetime.fromisoformat(str(news["at"]).replace("Z", "+00:00")))
    except ValueError:
        return None
    return (_aware(now) - published).total_seconds() / 3600.0


def _label(tf: str) -> str:
    return TF_LABEL.get(tf, tf)


def grade_row(row: Any, now: datetime, direction: str = "bull") -> dict | None:
    """The Draft 2 grade; ``direction="bear"`` reads the bearish paints instead
    (spec 2026-09-24). For bear, ``aboveMid`` means BELOW the 16h midpoint and
    ``skitBullish`` lists the bearish timeframes - the check names are kept so
    every reader of the dict works for both directions."""
    if not isinstance(row, Mapping):
        return None
    S = sets_for(direction)
    bear = S["direction"] == "bear"

    fired = [tf for tf in SQZ_TFS if _cell(row, "sqz", tf).get("bg") == S["sqz_fired"]]
    coiling = [tf for tf in SQZ_TFS if _cell(row, "sqz", tf).get("bg") == "orange"]
    sqz_ok = bool(fired) or not coiling

    push_rvol = [tf for tf in PUSH_RVOL_TFS if _cell(row, "rvol", tf).get("bg") in S["rvol_bg"]]
    age = _news_age_hours(row, now)
    push_news = age is not None and 0 <= age <= NEWS_MAX_HOURS
    push = bool(push_rvol) or push_news

    bullish, trend, cross = [], 0, 0
    for tf in SKIT_TFS:
        cell = _cell(row, "skittles", tf)
        is_trend = cell.get("fg") in S["skit_fg"]
        is_cross = cell.get("bg") in S["skit_bg"]
        trend += is_trend
        cross += is_cross
        if is_trend or is_cross:
            bullish.append(tf)
    skit = len(bullish)

    hl = row.get("highLow") if isinstance(row.get("highLow"), Mapping) else {}
    above_mid = hl.get("bg") == S["hl_bg"]
    try:
        hl_degree = float(hl.get("value")) if hl.get("value") is not None else None
    except (TypeError, ValueError):
        hl_degree = None

    letter = None
    if sqz_ok and push:
        if skit >= A_PLUS_SKIT and above_mid:
            letter = "A+"
        elif skit >= A_SKIT:
            letter = "A"
        elif skit >= B_SKIT:
            letter = "B"

    reasons = []
    if fired:
        reasons.append("SQZ fired " + "+".join(_label(tf) for tf in fired))
    elif coiling:
        reasons.append("SQZ coiling " + "+".join(_label(tf) for tf in coiling))
    else:
        reasons.append("no squeeze ON")
    for tf in push_rvol:
        value = _cell(row, "rvol", tf).get("value")
        reasons.append(f"RVOL {tf} {value}")
    if push_news:
        reasons.append(f"news {age:.1f}h ago")
    reasons.append(f"SKIT {skit}/8")
    if bear:
        reasons.append("below 16h midpoint" if above_mid else "above 16h midpoint")
    else:
        reasons.append("above 16h midpoint" if above_mid else "below 16h midpoint")
    pct = row.get("pctChange")
    if isinstance(pct, (int, float)):
        reasons.append(f"already {pct:+.1f}% today")

    return {
        "letter": letter,
        "direction": S["direction"],
        "checks": {
            "sqzOK": sqz_ok,
            "sqzFired": fired,
            "sqzCoiling": coiling,
            "push": push,
            "pushRvol": push_rvol,
            "pushNews": push_news,
            "newsAgeHours": None if age is None else round(age, 2),
            "skit": skit,
            "skitTrend": trend,
            "skitCross": cross,
            "skitBullish": bullish,
            "aboveMid": above_mid,
            "hlDegree": hl_degree,
        },
        "reasons": reasons,
        "pctChange": pct if isinstance(pct, (int, float)) else None,
    }


def safe_grade_row(row: Any, now: datetime, direction: str = "bull") -> dict | None:
    """grade_row that can never raise - a grading bug costs a grade, not a row."""
    try:
        return grade_row(row, now, direction)
    except Exception:  # noqa: BLE001
        return None
