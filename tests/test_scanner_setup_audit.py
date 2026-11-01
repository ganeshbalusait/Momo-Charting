from datetime import datetime, timezone

from scripts.audit_scanner_setups import audit_rows
from momx.grade import grade_row


def test_audit_distinguishes_actual_mismatch_duplicates_and_bad_hl():
    at = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
    row = {"symbol": "AAPL", "highLow": {"bg": "dark_green", "value": 0.8}}
    row["grade"] = grade_row(row, at)
    assert audit_rows({"rows": [row]}, at)["gradeDifferences"] == []
    wrong = {**row, "grade": {**row["grade"], "checks": {"aboveMid": False}}}
    report = audit_rows({"rows": [row, wrong], "rest": [{"symbol": "SPY", "highLow": {"value": 5}}]}, at)
    assert report["duplicates"] == ["AAPL"]
    assert report["gradeDifferences"][0]["symbol"] == "AAPL"
    assert report["invalidHighLow"][0]["symbol"] == "SPY"


def test_audit_bear_grades_uses_bear_paints():
    at = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
    row = {"symbol": "TSLA", "highLow": {"bg": "dark_red", "value": -0.8}}
    row["grade"] = grade_row(row, at, "bear")
    assert audit_rows({"rows": [row]}, at, "bear")["gradeDifferences"] == []
    assert audit_rows({"rows": [row]}, at, "bull")["gradeDifferences"]
