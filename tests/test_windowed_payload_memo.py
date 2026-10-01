from __future__ import annotations

from api_server import DashboardState


class _Stub(DashboardState):
    def __init__(self):
        pass


def _entry():
    newest = 1_786_377_600
    return {
        "cached_at": 0.0,
        "payload": {
            "bars": [{"time": newest, "close": 1.0}],
            "ganeshHigherTimeframeSignals": {
                "schemaVersion": 1,
                "signals": [
                    {"time": newest - 86_400, "kind": "old"},
                    {"time": newest, "kind": "visible"},
                ],
            },
        },
    }


def test_windows_signals_to_the_visible_bar_range():
    state = _Stub()
    entry = _entry()
    result = state._windowed_payload_for_cache_entry(entry)
    signals = result["ganeshHigherTimeframeSignals"]["signals"]
    assert [row["kind"] for row in signals] == ["visible"]


def test_second_call_reuses_the_stored_result():
    state = _Stub()
    entry = _entry()
    first = state._windowed_payload_for_cache_entry(entry)
    second = state._windowed_payload_for_cache_entry(entry)
    assert first is second, "the windowed payload must be computed once per cache entry"
    assert entry["windowed_payload"] is first


def test_a_replaced_payload_recomputes():
    state = _Stub()
    entry = _entry()
    state._windowed_payload_for_cache_entry(entry)
    # Simulate a refresh landing: new payload object, stale memo cleared.
    entry["payload"] = _entry()["payload"]
    entry.pop("windowed_payload", None)
    again = state._windowed_payload_for_cache_entry(entry)
    assert again is entry["windowed_payload"]
