import json
from datetime import datetime
from pathlib import Path

import pytest

from momx import grade

FIX = json.loads((Path(__file__).parent / "fixtures" / "momx_grade_rows.json").read_text(encoding="utf-8"))


def _g(label):
    item = FIX[label]
    return grade.grade_row(item["row"], datetime.fromisoformat(item["at"]))


def test_meta_0933_is_a_plus():
    g = _g("META_0933")
    assert g["letter"] == "A+"
    assert g["checks"]["skit"] == 8
    assert set(g["checks"]["sqzFired"]) >= {"Wk"}
    assert "5m" in g["checks"]["pushRvol"]


def test_qcom_morning_climbs_from_nothing_to_a():
    """QCOM is the fixture that caught the dark_green paint leak.

    At 09:42 its 4h cell reads 23 with bg ``dark_green`` / fg ``downtick`` -
    columns.skittles_cell paints that exact pair when MACD crossed UP while
    EMA9 is still BELOW EMA20. Until 2026-09-22 the grade counted that
    background as a bullish cross, which bought QCOM a fifth timeframe and
    made it a B. Four genuinely bullish timeframes is below B, so it must
    grade nothing at all.
    """
    early = _g("QCOM_0942")
    assert early["letter"] is None
    assert early["checks"]["skit"] == 4
    assert "4h" not in early["checks"]["skitBullish"]
    late = _g("QCOM_1041")
    # 6 of 8 by 10:41 - the two intraday timeframes are still not bullish, so
    # this is an A. It used to read A+ on the strength of the same bad cell.
    assert late["letter"] == "A"
    assert late["checks"]["pushNews"] is True        # the 05:05 headline
    assert late["checks"]["pushRvol"] == []            # no volume push at 10:41


def test_dark_green_macd_cross_against_the_ema_is_not_bullish():
    """The paint rule itself, guarded directly.

    Every bullish CROSS background requires EMA9 > EMA20 on that bar (cyan =
    exu1, green = macd21 and exu, lime = exu2 and exu). dark_green is the bare
    ``elif macd21`` branch reached only when that is false. If it ever returns
    to SKIT_CROSS_BG, a downtrending timeframe scores for the grade again.
    """
    assert "dark_green" not in grade.SKIT_CROSS_BG
    assert {"cyan", "green", "lime"} == set(grade.SKIT_CROSS_BG)
    # fg dark_green stays bullish: columns.skittles_cell paints it only when
    # EMA9 IS above EMA20 and the reading is >= 90. Same word, opposite state.
    assert "dark_green" in grade.SKIT_TREND_FG

    row = {
        "skittles": {tf: {"value": 50, "bg": "black", "fg": "magenta"} for tf in grade.SKIT_TFS},
        "highLow": {"bg": "green", "fg": "black", "value": 0.5},
    }
    row["skittles"]["4h"] = {"value": 23, "bg": "dark_green", "fg": "downtick"}
    checks = grade.grade_row(row, datetime(2026, 9, 22, 10, 0))["checks"]
    assert checks["skit"] == 0
    assert checks["skitBullish"] == []


def test_riot_coiling_weekly_squeeze_blocks_grade():
    g = _g("RIOT_0902")
    assert g["letter"] is None
    assert "Wk" in g["checks"]["sqzCoiling"]


def test_overnight_xovr_has_alignment_but_no_push():
    g = _g("XOVR_0001")
    assert g["letter"] is None
    assert g["checks"]["skit"] == 8
    assert g["checks"]["push"] is False


def test_dark_green_skit_fg_counts_bullish():
    row = {"skittles": {"2D": {"value": 94, "bg": "black", "fg": "dark_green"}}}
    g = grade.grade_row(row, datetime(2026, 9, 21, 9, 33))
    assert g["checks"]["skitBullish"] == ["2D"]
    assert g["checks"]["skitTrend"] == 1 and g["checks"]["skitCross"] == 0


def test_missing_cells_never_pass():
    g = grade.grade_row({}, datetime(2026, 9, 21, 9, 33))
    assert g["letter"] is None
    c = g["checks"]
    assert c["push"] is False and c["skit"] == 0 and c["aboveMid"] is False
    assert c["sqzOK"] is True  # no squeeze ON anywhere = OK, per Draft 2


def test_news_age_limit_is_12_hours():
    row = {"news": {"headline": "x", "at": "2026-09-21T09:00:00+00:00"}}
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T20:59:00+00:00"))["checks"]["pushNews"] is True
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T21:01:00+00:00"))["checks"]["pushNews"] is False


def test_future_dated_news_does_not_count():
    row = {"news": {"headline": "x", "at": "2026-09-21T12:00:00+00:00"}}
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T11:00:00+00:00"))["checks"]["pushNews"] is False


def test_not_a_mapping_returns_none():
    assert grade.grade_row(None, datetime(2026, 9, 21)) is None


# ----------------------------------------------------------------------
# BEAR grade - Draft 2 mirrored on the bearish paints (spec 2026-09-24)
# ----------------------------------------------------------------------

from momx_mirror import mirror_row  # noqa: E402


def test_every_fixture_grades_the_same_letter_on_its_bear_mirror():
    for label, item in FIX.items():
        at = datetime.fromisoformat(item["at"])
        bull = grade.grade_row(item["row"], at)
        bear = grade.grade_row(mirror_row(item["row"]), at, direction="bear")
        assert bear["letter"] == bull["letter"], label
        assert bear["checks"]["skit"] == bull["checks"]["skit"], label
        assert bear["checks"]["aboveMid"] == bull["checks"]["aboveMid"], label
        assert bear["checks"]["pushRvol"] == bull["checks"]["pushRvol"], label
        assert bear["checks"]["sqzFired"] == bull["checks"]["sqzFired"], label


def test_bear_grade_ignores_bull_colours_and_reads_bg_plum_as_no_cross():
    item = FIX["META_0933"]
    at = datetime.fromisoformat(item["at"])
    assert grade.grade_row(item["row"], at, direction="bear")["letter"] is None
    r = mirror_row(item["row"])
    r["skittles"] = dict(r["skittles"], **{"4h": {"value": 20, "bg": "plum", "fg": "violet"}})
    g = grade.grade_row(r, at, direction="bear")
    assert "4h" not in g["checks"]["skitBullish"]
    assert g["checks"]["skit"] == 7


def test_bear_reasons_say_below_midpoint_and_carry_direction():
    item = FIX["META_0933"]
    at = datetime.fromisoformat(item["at"])
    g = grade.grade_row(mirror_row(item["row"]), at, direction="bear")
    assert "below 16h midpoint" in g["reasons"]
    assert g["direction"] == "bear"
    assert grade.grade_row(item["row"], at)["direction"] == "bull"
    assert grade.safe_grade_row(item["row"], at, "bear")["direction"] == "bear"
