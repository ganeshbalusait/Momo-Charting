"""Integration (2026-11-19): the Long Term page's Peter view.

"Add one more next to MOS like Peter then show me his star sheet and tickers", quality stocks only.
The star sheet is worked out in the browser (frontend/src/longTermLynch.js) from the rows the real
GET /api/long-term route serves, so this drives the real route (fixture EDGAR / Finnhub, fake Schwab
prices, as tests/test_long_term_api.py) and hands the answer to the real frontend module under node.
The expected list is worked out here in Python, on its own, from the same served rows.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from api_harness import running_api
from long_term import service as lt_service
from test_long_term_api import Quotes
from test_long_term_service import NOW_MS, SYMBOLS, fixture_price, make_svc

MODULE = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "longTermLynch.js").as_uri()
NODE = """
const { lynchRows } = await import(process.argv[1]);
let body = ""; for await (const chunk of process.stdin) body += chunk;
const list = lynchRows(JSON.parse(body).rows);
process.stdout.write(JSON.stringify(list.map(({ row, sheet }) => [row.symbol, sheet.peg, sheet.stars, sheet.of, sheet.verdict])));
"""
SCORED = {"BUY", "WAIT", "AVOID"}


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("long_term_lynch_store")
    make_svc(directory).refresh(force=True)
    return directory


def _served(monkeypatch, store_dir, table):
    s = make_svc(store_dir, quotes=Quotes(table))
    monkeypatch.setattr(lt_service, "_INSTANCE", s)
    try:
        with running_api() as api:
            status, body = api.get("/api/long-term?mos=30")
    finally:
        s.stop_refresher()
    assert status == 200, body
    return body


def _sheet(body):
    view = subprocess.run(["node", "--input-type=module", "-e", NODE, MODULE], input=json.dumps(body),
                          capture_output=True, text=True, timeout=60, check=False)
    assert view.returncode == 0, view.stderr
    return json.loads(view.stdout)


def _expected(rows):
    """symbol -> PEG for the rows Peter's list may show: quality, priced, profitable, growing."""
    out = {}
    for r in rows:
        growth = r.get("epsGrowth")
        if r.get("quality") != "HIGH" or r.get("young") or r.get("signal") not in SCORED:
            continue
        if not r.get("pe") or r["pe"] <= 0 or growth is None or growth <= 0:
            continue
        out[r["symbol"]] = round(r["pe"] / min(round(growth * 1000) / 10, 50), 2)
    return out


def test_peter_list_is_the_quality_rows_of_the_real_route_with_their_peg(monkeypatch, store_dir):
    table = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS, "description": s}
             for s in SYMBOLS if fixture_price(s)}
    body = _served(monkeypatch, store_dir, table)
    expected = _expected(body["rows"])
    assert expected, "the fixtures hold at least one quality company with a price"
    sheet = _sheet(body)
    assert {symbol: peg for symbol, peg, *_ in sheet} == pytest.approx(expected)
    assert [(-stars, peg) for _, peg, stars, _, _ in sheet] == sorted((-stars, peg) for _, peg, stars, _, _ in sheet), \
        "most stars first, then the lowest PEG"
    for symbol, peg, stars, of, verdict in sheet:
        assert 0 <= stars <= of <= 5
        assert verdict == ("CHEAP" if peg <= 1 else "FAIR" if peg <= 2 else "PRICEY"), symbol
    by_symbol = {r["symbol"]: r for r in body["rows"]}
    assert all(by_symbol[symbol]["quality"] == "HIGH" for symbol, *_ in sheet), "quality companies only"


def test_without_prices_nobody_is_on_peters_list(monkeypatch, store_dir):
    body = _served(monkeypatch, store_dir, {})
    assert body["rows"] and _sheet(body) == []
