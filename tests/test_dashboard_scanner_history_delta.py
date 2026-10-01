"""The 5s dashboard poll must stop re-shipping unchanged scanner history.

Measured 2026-08-10: /api/dashboard was 1.24 MB and polled every 5 seconds.
scannerHistory was 993 KB of it (80%), and its raw_json field alone was
862 KB - 70% of the whole payload - for rows whose newest scan_date was 18
days old. That is ~18 MB/minute of JSON.parse plus a fresh array identity
every poll, which invalidates every downstream consumer.

The client sends the version it already holds; when it still matches, the
server omits the two history arrays. mergeDashboardPayload's
`{...current, ...payload}` then keeps the cached arrays AND their original
references, so consumers do not recompute either.
"""
from __future__ import annotations

from api_server import (
    browser_scanner_history_records,
    dashboard_payload_for_client,
    scanner_history_version,
)


def _rows() -> list[dict]:
    return [
        {"scan_date": "2026-07-14", "symbol": "MSFT", "scanned_at": "2026-07-14T09:53:57", "raw_json": "{}"},
        {"scan_date": "2026-07-22", "symbol": "NVDA", "scanned_at": "2026-07-22T10:01:02", "raw_json": "{}"},
    ]


def _days() -> list[dict]:
    return [{"scan_date": "2026-07-14"}, {"scan_date": "2026-07-22"}]


def _payload() -> dict:
    return {
        "scannerHistory": _rows(),
        "scannerHistoryDays": _days(),
        "scannerHistoryVersion": scanner_history_version(_rows(), _days()),
        "watchlist": [{"symbol": "SPY"}],
    }


def test_version_is_stable_for_identical_history() -> None:
    assert scanner_history_version(_rows(), _days()) == scanner_history_version(_rows(), _days())


def test_version_changes_when_a_scan_row_is_appended() -> None:
    appended = _rows() + [
        {"scan_date": "2026-08-10", "symbol": "PLTR", "scanned_at": "2026-08-10T09:31:00", "raw_json": "{}"},
    ]
    assert scanner_history_version(appended, _days()) != scanner_history_version(_rows(), _days())


def test_version_changes_when_retention_trims_old_rows() -> None:
    trimmed = _rows()[1:]
    assert scanner_history_version(trimmed, _days()) != scanner_history_version(_rows(), _days())


def test_version_changes_when_the_day_index_changes() -> None:
    assert scanner_history_version(_rows(), _days()[1:]) != scanner_history_version(_rows(), _days())


def test_matching_version_omits_both_history_arrays() -> None:
    payload = _payload()
    held = payload["scannerHistoryVersion"]

    trimmed = dashboard_payload_for_client(payload, held)

    assert "scannerHistory" not in trimmed
    assert "scannerHistoryDays" not in trimmed
    # The version must survive so the client can keep matching on it.
    assert trimmed["scannerHistoryVersion"] == held
    # Everything else is untouched.
    assert trimmed["watchlist"] == [{"symbol": "SPY"}]
    # And the caller's payload (the shared dashboard cache!) is not mutated.
    assert "scannerHistory" in payload


def test_stale_version_still_ships_the_full_history() -> None:
    payload = _payload()

    trimmed = dashboard_payload_for_client(payload, "some-older-version")

    assert trimmed["scannerHistory"] == _rows()
    assert trimmed["scannerHistoryDays"] == _days()


def test_client_without_a_version_gets_the_full_history() -> None:
    payload = _payload()

    for empty in ("", None):
        trimmed = dashboard_payload_for_client(payload, empty)
        assert trimmed["scannerHistory"] == _rows(), f"empty version {empty!r} must not trim"


def test_trimming_is_a_no_op_when_the_payload_has_no_history() -> None:
    payload = {"watchlist": []}

    assert dashboard_payload_for_client(payload, "anything") == payload


def test_compact_poll_omits_all_large_historical_sections() -> None:
    payload = {
        **_payload(),
        "catalysts": [{"headline": "news"}],
        "catalystIndex": [{"symbol": "SPY"}],
        "optionTradeHistory": [{"option_symbol": "SPY260810C00500000"}],
        "marketStatus": {"status": "OPEN"},
    }

    compact = dashboard_payload_for_client(payload, "", compact=True)

    for key in (
        "scannerHistory",
        "scannerHistoryDays",
        "catalysts",
        "catalystIndex",
        "optionTradeHistory",
    ):
        assert key not in compact
    assert compact["scannerHistoryVersion"] == payload["scannerHistoryVersion"]
    assert compact["marketStatus"] == {"status": "OPEN"}
    # The shared cache handed into the helper must never be mutated.
    assert "catalysts" in payload


def test_browser_history_uses_an_object_instead_of_double_encoded_json() -> None:
    records = browser_scanner_history_records([
        {"symbol": "NVDA", "raw_json": '{"underlying":"NVDA","delta":0.42}'},
    ])

    assert records == [{
        "symbol": "NVDA",
        "raw": {"underlying": "NVDA", "delta": 0.42},
    }]
    assert "raw_json" not in records[0]
