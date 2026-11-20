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
        dividend = r.get("dividendYield")
        dividend_pct = round(dividend * 1000) / 10 if isinstance(dividend, (int, float)) and dividend > 0 else 0
        out[r["symbol"]] = round(r["pe"] / (min(round(growth * 1000) / 10, 50) + dividend_pct), 2)
    return out


def test_rows_carry_the_dividend_yield_and_the_inventory_change(monkeypatch, store_dir):
    """2026-11-19 "fix all issue": Lynch's PEG adds the dividend to the growth, and inventory growing
    faster than sales is his warning sign. Both come from the Finnhub fixture through the real route."""
    table = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS, "description": s}
             for s in SYMBOLS if fixture_price(s)}
    rows = {r["symbol"]: r for r in _served(monkeypatch, store_dir, table)["rows"]}
    assert rows["AAPL"]["dividendYield"] == pytest.approx(0.003105)        # Finnhub 0.3105 (%)
    assert rows["MCD"]["dividendYield"] > rows["AAPL"]["dividendYield"] > 0
    # GOOGL (internet, SIC 7370), JPM (bank) and MCD (restaurants, 5812 is a shop) : only makers and shops
    assert rows["GOOGL"]["inventoryTurnChange"] is None and rows["JPM"]["inventoryTurnChange"] is None
    # 2026-11-20 audit: Apple holds about two weeks of sales (it turns its inventory over 24+ times
    # a year), so a swing in that ratio is not goods piling up and no figure is served. Home Depot
    # keeps months of goods on the shelf, so it gets one.
    assert rows["AAPL"]["inventoryTurnChange"] is None
    assert isinstance(rows["HD"]["inventoryTurnChange"], float) and -1 < rows["HD"]["inventoryTurnChange"] < 5
    assert rows["SPY"]["dividendYield"] is None, "an ETF row carries neither"


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
    by_symbol = {r["symbol"]: r for r in body["rows"]}
    for symbol, peg, stars, of, verdict in sheet:
        priced = "CHEAP" if peg <= 1 else "FAIR" if peg <= 2 else "PRICEY"
        assert verdict == ("CYCLICAL" if by_symbol[symbol].get("cyclical") else priced), symbol
    assert all(by_symbol[symbol]["quality"] == "HIGH" for symbol, *_ in sheet), "quality companies only"


def test_rows_carry_the_cyclical_industry_and_a_cyclical_is_never_called_cheap(monkeypatch, store_dir):
    """2026-11-19 "add a cyclical warning label": the route marks each row from the company's SIC code
    (NVDA and TSM file under semiconductors, ASML under chip-making equipment), and the Peter view
    shows a marked company as CYCLICAL whatever its PEG."""
    table = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS, "description": s}
             for s in SYMBOLS if fixture_price(s)}
    body = _served(monkeypatch, store_dir, table)
    marks = {r["symbol"]: r["cyclical"] for r in body["rows"]}
    assert (marks["NVDA"], marks["TSM"], marks["ASML"]) == ("chips", "chips", "chips")
    assert (marks["AAPL"], marks["GOOGL"], marks["MCD"], marks["JPM"]) == (None, None, None, None)
    sheet = {symbol: verdict for symbol, _, _, _, verdict in _sheet(body)}
    cyclical = {s for s in sheet if marks[s]}
    assert cyclical, "at least one cyclical fixture company is on Peter's list"
    assert all(sheet[s] == "CYCLICAL" for s in cyclical)
    assert all(sheet[s] != "CYCLICAL" for s in sheet if not marks[s])


MAG7_NODE = """
const { lynchRows } = await import(process.argv[1]);
let body = ""; for await (const chunk of process.stdin) body += chunk;
const page = JSON.parse(body);
const pick = (scope) => lynchRows(page.rows, { scope, lists: page.meta.lists }).map(({ row, sheet, reason }) => [row.symbol, sheet ? sheet.lowQuality : null, reason || null]);
process.stdout.write(JSON.stringify({ Mag7: pick("Mag7"), Watchlist: pick("Watchlist") }));
"""


def test_the_route_says_who_is_on_mag7_and_the_watchlist_and_the_groups_follow(monkeypatch, store_dir):
    """2026-11-19 "add mag7 tickers and watchlist tickers separate also": meta.lists comes from the
    MomX lists file (faked here), in list order, canonical, without repeats. The Mag7 group shows
    every member company, quality or not; the Watchlist group keeps to quality companies."""
    from long_term import symbols as lt_symbols
    import momx.board as board

    monkeypatch.setattr(board, "load_universes", lambda path=None: {
        "Mag7": ["AAPL", "nvda", "GOOGL", "SPY", "AAPL", "HOOD"], "Watchlist": ["MCD", "JPM", "AAPL"], "Movers": ["TSM"]})
    assert lt_symbols.named_lists() == {"Mag7": ["AAPL", "NVDA", "GOOGL", "SPY", "HOOD"], "Watchlist": ["MCD", "JPM", "AAPL"]}
    table = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS, "description": s}
             for s in SYMBOLS if fixture_price(s)}
    body = _served(monkeypatch, store_dir, table)
    assert body["meta"]["lists"] == {"Mag7": ["AAPL", "NVDA", "GOOGL", "SPY", "HOOD"], "Watchlist": ["MCD", "JPM", "AAPL"]}
    view = subprocess.run(["node", "--input-type=module", "-e", MAG7_NODE, MODULE], input=json.dumps(body),
                          capture_output=True, text=True, timeout=60, check=False)
    assert view.returncode == 0, view.stderr
    groups = json.loads(view.stdout)
    rows = {r["symbol"]: r for r in body["rows"]}
    mag7 = {symbol: (low, reason) for symbol, low, reason in groups["Mag7"]}
    assert set(mag7) == {"AAPL", "NVDA", "GOOGL", "HOOD"}, "every Mag7 company, the ETF left out"
    for symbol, (low, reason) in mag7.items():
        if reason is None:
            assert low == (rows[symbol]["quality"] != "HIGH"), symbol
        else:
            assert low is None and isinstance(reason, str) and reason
    watch = [symbol for symbol, _, _ in groups["Watchlist"]]
    assert set(watch) <= {"MCD", "JPM", "AAPL"} and all(rows[s]["quality"] == "HIGH" for s in watch)


SIMPLE_MODULE = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "longTermSimple.js").as_uri()
SIMPLE_NODE = """
const { simpleWhy } = await import(process.argv[1]);
let body = ""; for await (const chunk of process.stdin) body += chunk;
const out = {};
for (const row of JSON.parse(body).rows) out[row.symbol] = simpleWhy(row);
process.stdout.write(JSON.stringify(out));
"""
JARGON = ("ROIC", "ROE", "EPS", "PEG", "FCF", "MOS", "P/E", "margin of safety", "sticker", "TTM")


def test_every_served_row_gets_a_plain_words_explanation_that_matches_its_verdict(monkeypatch, store_dir):
    """2026-11-20 "justification why Warren or Peter chose that stocks ... a 10 kid can understand":
    the "In simple words" box is built from the rows the real route serves. Warren's first sentence
    must agree with the row's signal, the numbers must be the row's own, and no line may use a
    trading word."""
    table = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS, "description": s}
             for s in SYMBOLS if fixture_price(s)}
    body = _served(monkeypatch, store_dir, table)
    view = subprocess.run(["node", "--input-type=module", "-e", SIMPLE_NODE, SIMPLE_MODULE], input=json.dumps(body),
                          capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert view.returncode == 0, view.stderr
    simple = json.loads(view.stdout)
    # 2026-11-20 "if Peter was there did he buy it or not? ... Think like you are Peter and Warren"
    opening = {"BUY": "Would Warren buy it? Yes.", "WAIT": "Would Warren buy it? Not yet.",
               "AVOID": "Would Warren buy it? No.", "ETF": "This is a fund"}
    seen = set()
    for r in body["rows"]:
        why = simple[r["symbol"]]
        warren = why["warren"]
        if r["signal"] in opening:
            assert warren["headline"].startswith(opening[r["signal"]]), r["symbol"]
            seen.add(r["signal"])
        said = " ".join([warren["headline"], *(line["text"] for line in warren["lines"]), warren["todo"] or ""])
        if why["peter"]:
            said += " " + " ".join([why["peter"]["headline"], *(line["text"] for line in why["peter"]["lines"]),
                                    why["peter"]["todo"] or "", why["peter"]["sell"] or ""])
        # the AVOID line quotes the rule's own reason, which may name a ratio: every other line is plain
        plain = " ".join(part for part in said.split("The rule it failed:")[:1])
        assert not any(word in plain for word in JARGON), (r["symbol"], plain)
        assert all(line["mark"] in ("yes", "no", "info") for line in warren["lines"])
    assert {"AVOID", "ETF"} <= seen and seen & {"BUY", "WAIT"}, seen
    aapl, row = simple["AAPL"], next(r for r in body["rows"] if r["symbol"] == "AAPL")
    assert f"it earns ${round(row['roic'] * 100)} a year" in aapl["warren"]["lines"][0]["text"]
    answers = ("Would Peter buy it? Yes.", "Would Peter buy it? Not at this price.", "Would Peter buy it? No.",
               "Would Peter buy it? Only with care.", "Would Peter buy it? He cannot tell:",
               "Would Peter buy it? This page gives no buy answer.", "Would Peter buy it? He cannot tell.")
    for symbol, why in simple.items():
        if why["peter"]:
            assert why["peter"]["headline"].startswith(answers), symbol
            # a sell line only where he would own it, and never a fixed profit target as a rule
            if why["peter"]["headline"].startswith(("Would Peter buy it? No.", "Would Peter buy it? He cannot tell:",
                                                    "Would Peter buy it? This page gives no buy answer.", "Would Peter buy it? He cannot tell.")):
                assert why["peter"]["sell"] is None, symbol
            else:
                assert why["peter"]["sell"].startswith("When would he sell?"), symbol
    assert aapl["warren"]["sell"] is None or aapl["warren"]["sell"].startswith("When would he sell? This rule sells when")


def test_a_broken_lists_file_gives_no_groups_and_the_page_still_answers(monkeypatch, store_dir):
    import momx.board as board

    def boom(path=None):
        raise ValueError("unreadable lists file")

    monkeypatch.setattr(board, "load_universes", boom)
    body = _served(monkeypatch, store_dir, {})
    assert body["rows"] and body["meta"]["lists"] == {}


def test_without_prices_nobody_is_on_peters_list(monkeypatch, store_dir):
    body = _served(monkeypatch, store_dir, {})
    assert body["rows"] and _sheet(body) == []
