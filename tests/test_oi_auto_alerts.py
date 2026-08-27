from datetime import date

from oi_auto_alerts import (
    apply_completed_five_minute_close,
    build_oi_ladder,
    decorate_alert_row,
    next_monthly_opex,
    normalize_symbols,
)


def _level(side, strike, oi, delta, *, expiry="2026-08-21", volume=100):
    return {
        "side": side,
        "strike": strike,
        "open_interest": oi,
        "volume": volume,
        "delta": delta,
        "expiry": expiry,
        "days_to_expiration": 4,
    }


def _tsla_ladder():
    rows = [
        _level("CALL", 345, 6541, 0.43),
        _level("CALL", 345, 1200, 0.39, expiry="2026-08-14", volume=900),
        _level("CALL", 350, 17753, 0.34),
        _level("CALL", 355, 3784, 0.24),
        _level("CALL", 357.5, 3239, 0.10),
        _level("CALL", 360, 12844, 0.08),
        _level("PUT", 340, 5910, -0.45),
        _level("PUT", 330, 7094, -0.31),
        _level("PUT", 320, 7708, -0.20),
        _level("PUT", 310, 5959, -0.13),
        _level("PUT", 300, 8943, -0.08),
    ]
    return build_oi_ladder(rows, 341.63, as_of=date(2026, 8, 17))


def test_builds_directional_tsla_ladder_with_dominant_expiry_and_oi_exception():
    ladder = _tsla_ladder()

    assert ladder["monthlyExpiry"] == "2026-09-18"
    assert [level["strike"] for level in ladder["callLevels"]] == [345.0, 350.0, 360.0]
    assert [level["strike"] for level in ladder["putLevels"]] == [340.0, 330.0, 320.0, 310.0, 300.0]
    assert ladder["callLevels"][0]["openInterest"] == 6541
    assert ladder["callLevels"][-1]["openInterest"] == 12844


def test_exact_touch_does_not_confirm_but_close_above_advances_to_next_call_target():
    row = {"symbol": "TSLA", **_tsla_ladder()}

    touched, touched_events = apply_completed_five_minute_close(
        row, close=345, bar_ended_at="2026-08-17T09:35:00-04:00"
    )
    assert touched_events == []
    assert decorate_alert_row(touched)["activeCall"]["strike"] == 345

    crossed, events = apply_completed_five_minute_close(
        touched, close=345.01, bar_ended_at="2026-08-17T09:40:00-04:00"
    )
    assert [level["strike"] for level in events[0]["crossedLevels"]] == [345]
    assert events[0]["nextTarget"]["strike"] == 350
    assert decorate_alert_row(crossed)["activeCall"]["strike"] == 350


def test_gap_close_confirms_every_passed_call_level_and_selects_next_target():
    row = {"symbol": "TSLA", **_tsla_ladder()}
    updated, events = apply_completed_five_minute_close(
        row, close=356, bar_ended_at="2026-08-17T10:00:00-04:00"
    )

    assert [level["strike"] for level in events[0]["crossedLevels"]] == [345, 350]
    assert events[0]["confirmedLevel"]["strike"] == 350
    assert events[0]["nextTarget"]["strike"] == 360
    assert decorate_alert_row(updated)["activeCall"]["strike"] == 360


def test_put_close_must_be_strictly_below_and_duplicate_bar_is_ignored():
    row = {"symbol": "TSLA", **_tsla_ladder()}
    touched, events = apply_completed_five_minute_close(
        row, close=340, bar_ended_at="2026-08-17T10:05:00-04:00"
    )
    assert events == []

    crossed, events = apply_completed_five_minute_close(
        touched, close=329.5, bar_ended_at="2026-08-17T10:10:00-04:00"
    )
    put_event = next(event for event in events if event["side"] == "PUT")
    assert [level["strike"] for level in put_event["crossedLevels"]] == [340, 330]
    assert put_event["nextTarget"]["strike"] == 320

    duplicate, duplicate_events = apply_completed_five_minute_close(
        crossed, close=310, bar_ended_at="2026-08-17T10:10:00-04:00"
    )
    assert duplicate_events == []
    assert duplicate == crossed


def test_monthly_opex_and_manual_symbol_normalization():
    assert next_monthly_opex(date(2026, 8, 10)).isoformat() == "2026-08-21"
    assert next_monthly_opex(date(2026, 8, 22)).isoformat() == "2026-09-18"
    assert normalize_symbols([" tsla ", "TSLA", "brk.b", "bad ticker", "NVDA"]) == [
        "TSLA",
        "BRK.B",
        "NVDA",
    ]


# --- MomoX parity ---------------------------------------------------------
# Transcribed from the MomoX MSFT High OI panel (2026-08-20 session), the same
# fixture the frontend list is pinned to.

MSFT_ROWS = [
    _level("CALL", 530, 16600, 0.03, volume=613),
    _level("CALL", 525, 30700, 0.14, expiry="2026-09-18", volume=1131),
    _level("CALL", 520, 11500, 0.04, volume=4693),
    _level("CALL", 510, 17600, 0.23, expiry="2026-09-18", volume=499),
    _level("CALL", 500, 48300, 0.09, volume=155),
    _level("CALL", 495, 3900, 0.31, expiry="2026-09-18", volume=88),
    _level("CALL", 490, 8400, 0.24, volume=156),
    _level("CALL", 485, 3700, 0.45, volume=572),
    # Deep ITM calls holding more OI than any wall above: never a level.
    _level("CALL", 400, 90000, 0.97, volume=12),
    _level("PUT", 480, 8500, -0.42, volume=240),
    _level("PUT", 475, 5200, -0.28, volume=2896),
    _level("PUT", 470, 6000, -0.24, expiry="2026-09-18", volume=1978),
    _level("PUT", 465, 4700, -0.12, volume=1258),
    _level("PUT", 460, 13300, -0.09, volume=245),
    _level("PUT", 455, 3300, -0.06, volume=649),
    _level("PUT", 450, 11800, -0.04, volume=2088),
    _level("PUT", 440, 6900, -0.09, expiry="2026-09-18", volume=410),
    # Deep ITM puts above the anchor: never a level.
    _level("PUT", 560, 75000, -0.98, volume=5),
]


def _msft_ladder(**kwargs):
    return build_oi_ladder(MSFT_ROWS, 484.42, as_of=date(2026, 8, 20), **kwargs)


def test_msft_ladder_scores_importance_like_the_momox_panel():
    ladder = _msft_ladder(min_importance=1)

    assert [(level["strike"], level["imp"]) for level in ladder["callLevels"]] == [
        (485.0, 1),
        (490.0, 2),
        (495.0, 1),
        (500.0, 5),
        (510.0, 4),
        (520.0, 3),
        (525.0, 5),
        (530.0, 3),
    ]
    assert [(level["strike"], level["imp"]) for level in ladder["putLevels"]] == [
        (480.0, 5),
        (475.0, 4),
        (470.0, 4),
        (465.0, 4),
        (460.0, 5),
        (455.0, 3),
        (450.0, 5),
        (440.0, 5),
    ]


def test_msft_ladder_alerts_only_on_imp_three_and_above():
    ladder = _msft_ladder()

    # The faint Imp 1-2 walls (485, 490, 495) are dropped, so no alert can
    # fire at a strike sitting right on top of spot.
    assert [level["strike"] for level in ladder["callLevels"]] == [500.0, 510.0, 520.0, 525.0, 530.0]
    assert [level["strike"] for level in ladder["putLevels"]] == [
        480.0,
        475.0,
        470.0,
        465.0,
        460.0,
        455.0,
        450.0,
        440.0,
    ]


def test_itm_strikes_never_become_levels_however_large():
    ladder = _msft_ladder(min_importance=1)

    assert all(level["strike"] > 484.42 for level in ladder["callLevels"])
    assert all(level["strike"] < 484.42 for level in ladder["putLevels"])
    assert 400.0 not in [level["strike"] for level in ladder["callLevels"]]
    assert 560.0 not in [level["strike"] for level in ladder["putLevels"]]


def test_anchor_price_pins_the_split_when_price_runs_intraday():
    # Price ran to 496.50 but the session anchor stays at BMO, so 485 and 490
    # remain call levels instead of flipping into put support mid-session.
    ladder = build_oi_ladder(
        MSFT_ROWS,
        496.50,
        as_of=date(2026, 8, 20),
        anchor_price=484.42,
        min_importance=1,
    )

    assert ladder["anchor"] == 484.42
    assert 485.0 in [level["strike"] for level in ladder["callLevels"]]
    assert 485.0 not in [level["strike"] for level in ladder["putLevels"]]

    unanchored = build_oi_ladder(MSFT_ROWS, 496.50, as_of=date(2026, 8, 20), min_importance=1)
    assert unanchored["anchor"] == 496.5
    assert 485.0 not in [level["strike"] for level in unanchored["callLevels"]]


def test_monthly_window_rolls_forward_inside_opex_week():
    # On 2026-08-20 MomoX still lists the 2026-09-18 cycle; stopping at the
    # 2026-08-21 OPEX one day out would have hidden every one of those walls.
    assert next_monthly_opex(date(2026, 8, 20)).isoformat() == "2026-09-18"
    assert next_monthly_opex(date(2026, 8, 3)).isoformat() == "2026-08-21"
    assert 525.0 in [level["strike"] for level in _msft_ladder()["callLevels"]]
