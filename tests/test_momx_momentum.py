from datetime import datetime, timezone

from momx import momentum

T0 = int(datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc).timestamp())  # 09:30 ET


def bars(closes, *, highs=None, lows=None, vols=None, start=T0):
    out = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c + 0.2
        l = lows[i] if lows else c - 0.2
        out.append({"time": start + 300 * i, "open": c, "high": h, "low": l, "close": c,
                    "volume": (vols[i] if vols else 1000)})
    return out


FLAT = [100.0] * 8
DONE = T0 + 300 * 40          # "now" long after the bars: all completed
RVOL_UP = {"5m": {"bg": "cyan"}}


def test_session_vwap_starts_at_0930_and_weights_by_volume():
    # One premarket bar (09:25) must not count; then 100 x 1000 and 110 x 3000.
    pre = bars([50.0], start=T0 - 300)
    b = pre + bars([100.0, 110.0], vols=[1000, 3000])
    s = momentum.summarize(b + bars(FLAT, start=T0 + 600, vols=[0] * 8), None, now_epoch=DONE)
    # typical price = close here (high/low are +-0.2 around it) -> (100*1000 + 110*3000)/4000
    assert abs(s["vwap"] - 107.5) < 1e-9


def test_cross_up_at_is_the_last_completed_9_20_cross_today():
    # 30 falling bars put EMA9 under EMA20, then a sharp rise crosses it up.
    down = [100.0 - i * 0.5 for i in range(30)]
    up = [down[-1] + i * 2.0 for i in range(1, 8)]
    b = bars(down + up)
    s = momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600)
    assert s["crossUpAt"] is not None
    assert b[30]["time"] <= s["crossUpAt"] <= b[-1]["time"]
    assert momentum.summarize(bars(down), None, now_epoch=DONE)["crossUpAt"] is None


def test_trigger_is_max_high_of_last_six_completed():
    s = momentum.summarize(bars(FLAT), None, now_epoch=DONE)
    assert s["trigger"] == 100.2
    assert s["chart"] == "below" and s["state"] == "quiet"


def test_breakout_close_near_high_with_rvol_is_building():
    b = bars(FLAT + [101.0], highs=[100.2] * 8 + [101.05], lows=[99.8] * 8 + [100.1])
    s = momentum.summarize(b, RVOL_UP, now_epoch=DONE)
    assert s["chart"] == "breakout_confirmed"
    assert s["state"] == "building" and s["provisional"] is False


def test_breakout_without_rvol_is_holding_not_building():
    b = bars(FLAT + [101.0], highs=[100.2] * 8 + [101.05], lows=[99.8] * 8 + [100.1])
    assert momentum.summarize(b, None, now_epoch=DONE)["state"] == "holding"


def test_close_back_below_trigger_fails_and_fades():
    b = bars(FLAT + [101.0, 100.0])
    s = momentum.summarize(b, RVOL_UP, now_epoch=DONE)
    assert s["chart"] == "failed" and s["state"] == "fading"


def test_developing_bar_above_trigger_is_provisional():
    b = bars(FLAT + [101.0])
    now = b[-1]["time"] + 60          # last bar still forming
    s = momentum.summarize(b, RVOL_UP, now_epoch=now)
    assert s["chart"] == "breakout_provisional"
    assert s["state"] == "building" and s["provisional"] is True


def test_far_above_ema_is_extended():
    closes = [100.0] * 30 + [100.0 + 2 * i for i in range(1, 6)]
    s = momentum.summarize(bars(closes), RVOL_UP, now_epoch=T0 + 300 * 60)
    assert s["state"] == "extended"


def test_explosive_pattern_needs_big_volume_bar_and_breakout():
    # NOTE: bumped from 30 flat bars to 60 (+ now_epoch pushed out to match).
    # rvol_zscore (momx.indicators) returns 0.0 during warm-up (< RVOL_LENGTH=50
    # bars of history), so with only 30 bars before the spike the spike's
    # z-score would be forced to 0.0 and could never clear EXPLOSIVE_Z=3.0.
    # 60 flat bars gives the 50-bar window a full, real stdev by the time the
    # spike lands. See task-4 report for the arithmetic (z ~= 7.0 here).
    vols = [1000] * 60 + [9000]
    closes = [100.0] * 60 + [101.0]
    b = bars(closes, highs=[100.2] * 60 + [101.05], lows=[99.8] * 60 + [100.1], vols=vols)
    assert momentum.summarize(b, RVOL_UP, now_epoch=T0 + 300 * 100)["pattern"] == "explosive"


def test_steady_pattern_is_rising_15m_highs_and_lows():
    closes = [100.0 + 0.05 * i for i in range(30)]          # slow grind, every 15m candle higher
    s = momentum.summarize(bars(closes), None, now_epoch=T0 + 300 * 60)
    assert s["pattern"] == "steady"


def test_flat_tape_has_no_pattern():
    assert momentum.summarize(bars([100.0] * 30), None, now_epoch=T0 + 300 * 60)["pattern"] is None


def test_too_few_bars_returns_none():
    assert momentum.summarize(bars([100.0] * 3), None, now_epoch=DONE) is None


PREV_DAY = T0 - 86400 * 3  # Friday 09:30 ET (T0 is Monday 2026-09-21)


def test_gap_go_meta_2026_09_21_shape():
    # Friday closes 100; Monday 09:25 premarket 102.2 (+2.2% gap); 09:30 bar high
    # 104; 09:35 back under the 09:30 high; 09:40 clears it -> goAt = 09:40.
    prev = bars([100.0] * 8, start=PREV_DAY)
    pre = bars([102.2], start=T0 - 300)
    day = bars([103.5, 103.8, 104.5, 105.0, 105.2, 105.4, 105.6, 105.8], highs=[104.0] + [None] * 7)
    for b in day[1:]:
        b["high"] = b["close"] + 0.2
    g = momentum.summarize(prev + pre + day, None, now_epoch=DONE)["gapGo"]
    assert g["gap"] == 2.2 and g["open"] == 102.2 and g["orHigh"] == 104.0
    assert g["goAt"] == T0 + 600


def test_gap_go_needs_a_two_percent_gap():
    prev = bars([100.0] * 8, start=PREV_DAY)
    g = momentum.summarize(prev + bars([101.0], start=T0 - 300) + bars([101.5 + i * 0.3 for i in range(8)]), None, now_epoch=DONE)["gapGo"]
    assert g["gap"] == 1.0 and g["goAt"] is None


def test_gap_go_none_without_a_prior_session():
    assert momentum.summarize(bars([100.0 + i for i in range(10)]), None, now_epoch=DONE)["gapGo"] is None


def test_time_of_day_volume_compares_the_same_time_on_prior_sessions():
    # Three prior sessions of 1,000 shares per 5m bar; today 5,000 per bar.
    prior = []
    for back in (4, 3, 2):  # Thu, Fri... any three earlier weekdays; only the date matters
        prior += bars([100.0] * 6, start=T0 - 86400 * back, vols=[1000] * 6)
    today = bars([101.0, 101.5, 102.0], vols=[5000] * 3)
    t = momentum.summarize(prior + today, None, now_epoch=DONE)["todVol"]
    assert t["ratio"] == 5.0 and t["sessions"] == 3 and t["open"] == 101.0


def test_time_of_day_volume_needs_three_prior_sessions():
    prior = bars([100.0] * 6, start=T0 - 86400 * 3) + bars([100.0] * 6, start=T0 - 86400 * 2)
    assert momentum.summarize(prior + bars([101.0] * 3), None, now_epoch=DONE)["todVol"] is None


# ----------------------------------------------------------------------
# BEAR momentum - the bull rules mirrored (spec 2026-09-24)
# ----------------------------------------------------------------------

from momx_mirror import mirror_bars, mirror_price  # noqa: E402

RVOL_DOWN = {"5m": {"bg": "magenta"}}


def test_bear_summary_is_the_mirror_of_the_bull_summary():
    up = [100.0 + i * 0.3 for i in range(40)]
    b = bars(up, vols=[1000] * 30 + [9000] * 10)
    now = b[-1]["time"] + 600
    bull = momentum.summarize(b, RVOL_UP, now_epoch=now)
    bear = momentum.summarize(mirror_bars(b), RVOL_DOWN, now_epoch=now, direction="bear")
    assert bull["direction"] == "bull" and bear["direction"] == "bear"
    assert bear["state"] == bull["state"] and bear["chart"] == bull["chart"]
    assert bear["pattern"] == bull["pattern"] and bear["breakoutAt"] == bull["breakoutAt"]
    assert abs(bear["trigger"] - mirror_price(bull["trigger"])) < 1e-6
    assert abs(bear["ema20"] - mirror_price(bull["ema20"])) < 1e-6
    assert abs(bear["atr14"] - bull["atr14"]) < 1e-9
    # The bull reading of a falling tape is not a breakout.
    assert momentum.summarize(mirror_bars(b), RVOL_UP, now_epoch=now)["chart"] == "below"


def test_bear_chart_state_with_no_breakdown_is_above():
    b = bars(FLAT)
    assert momentum.summarize(b, None, now_epoch=DONE, direction="bear")["chart"] == "above"
    assert momentum.summarize(b, None, now_epoch=DONE)["chart"] == "below"


def test_cross_down_at_is_reported_in_both_directions():
    up = [100.0 + i * 0.5 for i in range(30)]
    down = [up[-1] - i * 2.0 for i in range(1, 8)]
    b = bars(up + down)
    now = b[-1]["time"] + 600
    bull = momentum.summarize(b, None, now_epoch=now)
    bear = momentum.summarize(b, None, now_epoch=now, direction="bear")
    assert bull["crossDownAt"] is not None and bull["crossDownAt"] == bear["crossDownAt"]
    assert b[30]["time"] <= bear["crossDownAt"] <= b[-1]["time"]
    # The rise crossed UP first (bar 1 - the documented warm-up artefact of a
    # first-value-seeded EMA), the drop crossed DOWN later: both are reported.
    assert bull["crossUpAt"] == bear["crossUpAt"]
    assert bull["crossUpAt"] is None or bull["crossUpAt"] < bear["crossDownAt"]


def test_bear_extended_is_two_atr_below_ema20():
    drop = [100.0] * 25 + [100.0 - i * 1.5 for i in range(1, 8)]
    b = bars(drop)
    now = b[-1]["time"] + 600
    assert momentum.summarize(b, None, now_epoch=now, direction="bear")["state"] == "extended"
    assert momentum.summarize(b, None, now_epoch=now)["state"] != "extended"


def test_gap_down_and_go_marks_the_first_candle_below_vwap_open_and_or_low():
    prev = bars([100.0] * 78, start=T0 - 86400)                     # yesterday's session, closes 100
    pre = bars([97.0], start=T0 - 300)                              # 09:25: gapped -3%
    session = bars([97.5, 96.0, 95.0], vols=[1000, 2000, 2000])     # 09:35 closes below VWAP, open, 09:30 low
    b = prev + pre + session
    now = b[-1]["time"] + 600
    g = momentum.summarize(b, None, now_epoch=now, direction="bear")["gapGo"]
    assert g["gap"] < -2 and g["goAt"] == session[1]["time"]
    assert g["orLow"] == 97.3 and g["orHigh"] == 97.7
    assert momentum.summarize(b, None, now_epoch=now)["gapGo"]["goAt"] is None
