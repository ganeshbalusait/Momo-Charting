from types import SimpleNamespace

import pytest

from data.alpaca_client import AlpacaClient


def test_expiration_query_paginates_and_deduplicates():
    calls = []
    first = [{"id":str(i), "date":"2026-10-23", "activity_type":"OPEXP"} for i in range(100)]
    def get(path, params):
        calls.append((path, dict(params)))
        return first if len(calls) == 1 else [{"id":"99"},{"id":"100","date":"2026-10-23","activity_type":"OPEXP"},{"id":"101","date":"2026-10-24","activity_type":"OPEXP"}]
    client = AlpacaClient.__new__(AlpacaClient)
    client._trading_client = SimpleNamespace(get=get)
    result = client.get_option_expirations("2026-10-23")
    assert len(result) == 101
    assert calls[0][1]["activity_types"] == "OPEXP"
    assert calls[0][1]["after"] == "2026-10-22T00:00:00Z" and "date" not in calls[0][1]
    assert calls[1][1]["page_token"] == "99"


def test_repeated_expiration_page_fails_without_partial_evidence():
    client = AlpacaClient.__new__(AlpacaClient)
    client._trading_client = SimpleNamespace(get=lambda *args:[{"id":str(i)} for i in range(100)])
    with pytest.raises(ValueError, match="pagination"):
        client.get_option_expirations("2026-10-23")
