"""Integration: the Sectors page's one quote request, through the real
/api/watchlist-quotes route over HTTP (2026-11-04 coverage).

The page asks for every card's leaders plus the 11 SPDR ETFs in ONE request
every 8 s (?profile=1&symbols=<SECTOR_SYMBOLS>). The symbol list is read from
the frontend's own sectorHeatmap.js, so a change there is tested here too.
Schwab is a fake; the industry/market-cap cache is a scratch file and its
background Finnhub fill is stubbed (it would otherwise call Finnhub and
rewrite the live cache).
"""
from __future__ import annotations

import json
import subprocess
import time as real_time
from pathlib import Path
from urllib.parse import quote

import pytest

from api_harness import api_server, running_api
from momx import industry_lookup

REPO = Path(__file__).resolve().parents[1]


def _sector_symbols() -> list[str]:
    module = (REPO / "frontend" / "src" / "sectorHeatmap.js").as_uri()
    out = subprocess.run(
        ["node", "--input-type=module", "-e",
         f"import({json.dumps(module)}).then((m) => console.log(JSON.stringify(m.SECTOR_SYMBOLS)))"],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(out.stdout)


SECTOR_SYMBOLS = _sector_symbols()


class FakeSchwab:
    """Stands in for data.schwab_client.SchwabClient; counts get_quotes calls."""

    calls: list[list[str]] = []
    answer = "priced"     # "priced" | "null" | "raise"

    def get_quotes(self, symbols):
        FakeSchwab.calls.append(list(symbols))
        if FakeSchwab.answer == "raise":
            raise RuntimeError("Schwab 401")
        if FakeSchwab.answer == "null":
            return {}
        return {
            symbol: {"last_price": 100.0 + index, "change": 1.25, "change_pct": 0.5 + index / 100,
                     "volume": 1000 + index, "description": f"{symbol} CORP"}
            for index, symbol in enumerate(symbols)
        }


class ShiftedClock:
    """api_server.time with a movable monotonic clock (everything else is real)."""

    def __init__(self):
        self.offset = 0.0

    def monotonic(self):
        return real_time.monotonic() + self.offset

    def __getattr__(self, name):
        return getattr(real_time, name)


@pytest.fixture
def sectors(monkeypatch, tmp_path):
    FakeSchwab.calls = []
    FakeSchwab.answer = "priced"
    cache_file = tmp_path / "momx_industry_cache.json"
    cache_file.write_text(json.dumps({
        "NVDA": {"industry": "Semiconductors", "marketCap": 4.4e12, "marketCapCurrency": "USD", "marketCapAt": "2026-11-04T12:00:00Z"},
        # Stored before the currency check (2026-11-04): may be in KRW/TWD, so it is never shown.
        "TSM": {"industry": "Semiconductors", "marketCap": 3.5e13, "marketCapAt": "2026-11-04T12:00:00Z"},
        "XLK": {"industry": None, "marketCap": None, "marketCapAt": "2026-11-04T12:00:00Z"},
    }), encoding="utf-8")
    fills, finnhub = [], []
    clock = ShiftedClock()
    monkeypatch.setattr(api_server, "SchwabClient", FakeSchwab)
    monkeypatch.setattr(api_server, "time", clock)
    monkeypatch.setattr(api_server.STATE, "_watchlist_quote_cache", {}, raising=False)
    monkeypatch.setattr(industry_lookup, "cache_path", lambda: cache_file)
    monkeypatch.setattr(industry_lookup, "schedule_market_cap_fill", lambda symbols, **kw: fills.append(list(symbols)) or True)
    monkeypatch.setattr(industry_lookup, "fetch_industry", lambda *a, **kw: finnhub.append(a) or None)
    industry_lookup.clear_memory_cache()
    yield {"clock": clock, "fills": fills, "finnhub": finnhub}
    industry_lookup.clear_memory_cache()


def _url(symbols, profile=True):
    return f"/api/watchlist-quotes?{'profile=1&' if profile else ''}symbols={quote(','.join(symbols))}"


def test_the_grid_request_answers_one_row_per_symbol_in_order_with_profiles(sectors):
    assert len(SECTOR_SYMBOLS) > 150 and len(set(SECTOR_SYMBOLS)) == len(SECTOR_SYMBOLS)
    with running_api() as api:
        status, body = api.get(_url(SECTOR_SYMBOLS))
    assert status == 200, body
    rows = body["rows"]
    assert [row["symbol"] for row in rows] == SECTOR_SYMBOLS
    first = rows[0]
    assert first["lastPrice"] == 100.0 and first["change"] == 1.25 and first["changePct"] == 0.5
    assert first["name"] == f"{SECTOR_SYMBOLS[0]} CORP"
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["NVDA"]["marketCap"] == 4.4e12, "market cap comes from the industry cache"
    assert by_symbol["TSM"]["marketCap"] is None, "a cap stored without its currency must not size a tile"
    assert all("industry" in row and "marketCap" in row for row in rows)
    assert len(FakeSchwab.calls) == 1, "one upstream call for the whole grid"
    assert sectors["finnhub"] == [], "the 8-second poll never waits on Finnhub"
    assert sectors["fills"] == [SECTOR_SYMBOLS], "missing caps are filled on a background pass, scheduled once"


def test_a_second_poll_inside_five_seconds_is_served_from_the_shared_cache(sectors):
    with running_api() as api:
        api.get(_url(SECTOR_SYMBOLS))
        sectors["clock"].offset = 4.0
        status, body = api.get(_url(SECTOR_SYMBOLS))
        assert status == 200 and len(body["rows"]) == len(SECTOR_SYMBOLS)
        assert len(FakeSchwab.calls) == 1
        sectors["clock"].offset = 5.5
        api.get(_url(SECTOR_SYMBOLS))
    assert len(FakeSchwab.calls) == 2, "after 5 s the next poll asks Schwab again"


def test_an_all_null_answer_still_returns_every_row_and_is_not_cached(sectors):
    FakeSchwab.answer = "null"
    with running_api() as api:
        status, body = api.get(_url(["NVDA", "AAPL", "XLK"]))
        assert status == 200, body
        assert [row["symbol"] for row in body["rows"]] == ["NVDA", "AAPL", "XLK"]
        assert all(row["lastPrice"] is None and row["changePct"] is None for row in body["rows"])
        FakeSchwab.answer = "priced"
        status, body = api.get(_url(["NVDA", "AAPL", "XLK"]))
    assert len(FakeSchwab.calls) == 2, "a feed-down answer must not pin an empty grid for 5 s"
    assert body["rows"][0]["lastPrice"] == 100.0


def test_schwab_raising_is_a_200_with_null_prices_never_a_500(sectors):
    FakeSchwab.answer = "raise"
    with running_api() as api:
        status, body = api.get(_url(["NVDA", "XLK"]))
    assert status == 200, body
    assert [(row["symbol"], row["lastPrice"]) for row in body["rows"]] == [("NVDA", None), ("XLK", None)]


def test_without_profile_rows_carry_no_profile_and_the_cache_is_not_mutated(sectors):
    with running_api() as api:
        status, plain = api.get(_url(["NVDA", "AAPL"], profile=False))
        assert status == 200
        assert all("industry" not in row and "marketCap" not in row for row in plain["rows"])
        status, profiled = api.get(_url(["NVDA", "AAPL"]))        # same symbol set -> same cache slot
        assert status == 200 and profiled["rows"][0]["marketCap"] == 4.4e12
        status, plain_again = api.get(_url(["NVDA", "AAPL"], profile=False))
    assert len(FakeSchwab.calls) == 1
    assert all("marketCap" not in row for row in plain_again["rows"]), "profile=1 leaked into the shared 5 s cache"
    assert sectors["fills"] == [["NVDA", "AAPL"]], "only the profile=1 call schedules a cap fill"


def test_an_open_sector_page_asks_for_its_own_names_separately(sectors):
    """The page's second request ('more' names) is its own cache slot."""
    with running_api() as api:
        api.get(_url(SECTOR_SYMBOLS))
        status, body = api.get(_url(["ORCL", "CRM", "IBM"]))
    assert status == 200 and [row["symbol"] for row in body["rows"]] == ["ORCL", "CRM", "IBM"]
    assert [len(call) for call in FakeSchwab.calls] == [len(SECTOR_SYMBOLS), 3]
