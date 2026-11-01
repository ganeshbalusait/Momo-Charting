from datetime import datetime
from momx.freshness import snapshot


def test_source_scan_and_serve_times_are_distinct():
    now = datetime.fromisoformat("2026-10-29T14:00:00+00:00")
    p = {"generatedAt": "2026-10-29T13:59:00+00:00", "scanCompletedAt": "2026-10-29T13:59:45+00:00",
         "rows": [{"marketDataAt": 1793281500}], "rest": [{"marketDataAt": 1793280900}, {}]}
    f = snapshot(p, "tos", now)
    assert f["scanCompletedAt"] != f["scanStartedAt"] != f["servedAt"]
    assert f["oldestDataAt"] < f["newestDataAt"]
    assert f["rowsWithTime"] == 2 and f["unknownTimeRows"] == 1
    assert f["providerDelayMinutes"] is None and "not confirmed" in f["providerNote"]
    assert f["barIntervalSeconds"] == 300 and f["barTimestampRole"] == "start"


def test_volume_midpoint_is_not_mistaken_for_source_candle_time():
    f = snapshot({"rows": [{"rvol": {"5m": {"barAt": 1793280900}}}]}, "tos")
    assert f["rowsWithTime"] == 0 and f["unknownTimeRows"] == 1
    assert f["newestDataAt"] is None


def test_alpaca_history_delay_is_labelled_for_the_actual_session():
    assert snapshot({}, "alpaca", datetime.fromisoformat("2026-10-29T14:00:00+00:00"))["providerDelayMinutes"] == 20
    assert snapshot({}, "alpaca", datetime.fromisoformat("2026-10-30T04:00:00+00:00"))["providerDelayMinutes"] == 16


def test_tos_overnight_discloses_delayed_gap_fill_without_claiming_all_tos_is_delayed():
    f = snapshot({}, "tos", datetime.fromisoformat("2026-10-30T06:00:00+00:00"))
    assert f["providerDelayMinutes"] is None
    assert "BOATS" in f["providerNote"] and "16-minute" in f["providerNote"]
    assert "takes precedence" in f["providerNote"]
