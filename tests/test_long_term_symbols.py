"""Long Term: symbol forms and the screening universe. No network."""
from long_term.symbols import (
    SEED_TICKERS,
    canonical,
    to_edgar,
    to_finnhub,
    to_schwab,
    universe,
)


def test_canonical_forms():
    assert canonical(" brk.b ") == "BRK-B" and canonical("BRK/B") == "BRK-B" and canonical(None) == ""
    assert to_schwab("BRK-B") == "BRK/B" and to_finnhub("BRK-B") == "BRK.B"
    assert to_edgar("BRK.B") == "BRK-B"
    assert canonical("") == "" and canonical("   ") == ""


def test_seed_list_is_the_sheet():
    assert len(SEED_TICKERS) == 239 and "AAOI" in SEED_TICKERS and len(set(SEED_TICKERS)) == 239
    assert "GOOG" in SEED_TICKERS and "GOOGL" in SEED_TICKERS


def test_universe_unions_every_list(tmp_path):
    (tmp_path / "watchlist.txt").write_text("AAPL\nbrk.b\n# comment\n")
    (tmp_path / "momx_universe.json").write_text(
        '{"schemaVersion":1,"lists":{"Watchlist":["MSFT"],"Mag7":["NVDA","AAPL"]}}'
    )
    rows = universe(watchlist_path=tmp_path / "watchlist.txt", universe_path=tmp_path / "momx_universe.json")
    assert {"AAPL", "BRK-B", "MSFT", "NVDA", "AAOI"} <= set(rows) and rows == sorted(set(rows))
    assert "COMMENT" not in rows and "#" not in rows


def test_universe_missing_files_still_returns_seed(tmp_path):
    rows = universe(watchlist_path=tmp_path / "nope.txt", universe_path=tmp_path / "nope.json")
    assert set(SEED_TICKERS) <= set(rows)
