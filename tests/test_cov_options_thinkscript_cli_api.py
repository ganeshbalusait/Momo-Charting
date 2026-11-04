"""Integration: scripts/generate_oi_thinkscript.py run end to end through its
own main() - argument parsing, the Tradier-then-Schwab provider walk, the
level ranking and the file it writes. The providers are fake client classes
that answer Schwab-shaped chains; the study is written under tmp_path (the
live guard refuses artifacts/)."""
from __future__ import annotations

import pytest

from test_cov_options_chain_feed_api import schwab_chain
from test_cov_options_thinkscript_cli import gen


class Tradier:
    configured = True
    asked: list[str] = []

    def get_option_chain_range(self, symbol, **_kw):
        Tradier.asked.append(symbol)
        if symbol == "AAPL":
            raise RuntimeError("401 token not approved")
        if symbol == "ZZZZ":
            return {}
        return schwab_chain(symbol, dtes=(2, 9), spot=580.0 if symbol == "SPY" else 200.0)


class Schwab:
    configured = True
    asked: list[str] = []

    def __init__(self, *_args):
        pass

    def get_option_chain(self, symbol, **_kw):
        Schwab.asked.append(symbol)
        if symbol == "ZZZZ":
            return {}
        return schwab_chain(symbol, dtes=(2, 9))


@pytest.fixture
def providers(monkeypatch):
    import data.schwab_client as schwab_client
    import data.tradier_client as tradier_client

    Tradier.asked, Schwab.asked = [], []
    monkeypatch.setattr(tradier_client, "TradierClient", Tradier)
    monkeypatch.setattr(schwab_client, "SchwabClient", Schwab)


def run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["generate_oi_thinkscript.py", *argv])
    return gen.main()


def test_main_writes_one_study_for_every_ticker_that_answered(providers, monkeypatch, tmp_path, capsys):
    out = tmp_path / "studies" / "oi_levels.txt"
    code = run(monkeypatch, "--tickers", "spy,AAPL,ZZZZ", "--top", "6", "--out", str(out))
    printed = capsys.readouterr().out
    assert code == 0, printed
    script = out.read_text(encoding="utf-8")
    assert script.startswith("#Highest OI Levels + Momox OI Reference")
    assert "Tickers: SPY,AAPL |" in script                     # ZZZZ had no chain: left out
    assert 'def isSPY = GetSymbol() == "SPY";' in script and 'def isAAPL = GetSymbol() == "AAPL";' in script
    assert "if(isSPY){" in script and "}else if(isAAPL){" in script
    assert 'isSPY and highestBar,emU,"+EM"' in script and 'isAAPL and highestBar,emU,"+EM"' in script
    assert (Tradier.asked, Schwab.asked) == (["SPY", "AAPL", "ZZZZ"], ["AAPL", "ZZZZ"])
    assert "[ok]   SPY" in printed and "via tradier" in printed
    assert "[ok]   AAPL" in printed and "via schwab" in printed
    assert "[FAIL] ZZZZ" in printed and "schwab: empty chain" in printed
    assert "(2 tickers, 1 failed)" in printed


def test_main_writes_nothing_when_no_ticker_answers(providers, monkeypatch, tmp_path, capsys):
    out = tmp_path / "oi_levels.txt"
    code = run(monkeypatch, "--tickers", "ZZZZ", "--provider", "schwab", "--out", str(out))
    assert code == 1
    assert not out.exists()
    assert "No ticker succeeded; nothing to generate." in capsys.readouterr().err
