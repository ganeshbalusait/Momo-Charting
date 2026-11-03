"""scripts/option_spread_scan.py - the offline option bid/ask spread scanner.

Every test builds a tiny fake repo under tmp_path (chain cache, scanner board,
universe file, watchlist), so nothing here reads the live artifacts/ or calls
a broker. Dates are relative to the real clock so the fixture never expires.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "option_spread_scan.py"
_SPEC = importlib.util.spec_from_file_location("option_spread_scan", SCRIPT)
oss = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(oss)

NOW = time.time()
TODAY = datetime.fromtimestamp(NOW, oss.ET).date()
SPOT = 100.0


def _row(side, expiry, strike, bid, ask, delta, oi=500, vol=50):
    occ = f"TEST  {expiry:%y%m%d}{side[0]}{int(strike * 1000):08d}"
    return {"symbol": occ, "side": side, "expiry": expiry.isoformat(),
            "days_to_expiration": 99,  # stale on purpose: the scanner must recompute it
            "strike": strike, "bid": bid, "ask": ask, "mark": None if ask is None else (bid + ask) / 2,
            "delta": delta, "volume": vol, "open_interest": oi}


def _rows():
    past, d0, d1, d8 = (TODAY + timedelta(days=n) for n in (-1, 0, 1, 8))
    return [
        _row("CALL", past, 101, 1.00, 1.01, 0.45),   # expired: must be ignored however tight
        _row("CALL", d0, 101, 0.50, 0.52, 0.40),     # 0DTE call
        _row("PUT", d0, 99, 0.40, 0.44, 0.40),       # 0DTE put
        _row("CALL", d1, 100, 2.00, 2.10, 0.50),     # strike == spot: not OTM
        _row("CALL", d1, 101, 1.00, 1.10, 0.45, oi=1234, vol=567),  # MAIN call: 0.10/1.05 = 9.52% TIGHT
        _row("CALL", d1, 104, 0.00, 0.00, 0.25),     # no market: skipped by the Bot
        _row("CALL", d1, 105, 0.20, 0.30, 0.19),     # D20 call: 40% WIDE
        _row("CALL", d1, 110, 0.00, 0.05, 0.05),     # no bid
        _row("PUT", d1, 99, 0.95, 1.10, 0.45),       # MAIN put: 0.15/1.025 = 14.6% OK
        _row("PUT", d1, 95, 0.18, 0.22, 0.21),       # delta 0.21 is not under 0.20
        _row("PUT", d1, 94, 0.10, 0.14, 0.15),       # D20 put
        _row("CALL", d8, 101, 3.00, 3.05, 0.55),     # later expiry: not the nearest
        {"side": "CALL", "expiry": "garbage", "strike": "x"},  # junk rows must not crash anything
        "not a row",
        {"symbol": "TEST  JUNK", "side": "CALL", "expiry": (TODAY + timedelta(days=1)).isoformat(),
         "strike": "abc", "bid": "n/a", "ask": None},
    ]


def _chain(symbol="TEST", rows=None, scanned_ago=300.0, schema=1, spot=SPOT):
    scanned = datetime.fromtimestamp(NOW - scanned_ago, oss.ET).isoformat()
    return {"symbol": symbol, "live": True, "optionQuoteSchemaVersion": schema, "scannedAt": scanned,
            "underlyingPrice": spot, "selectedExpiryChainRows": _rows() if rows is None else rows}


def _write_chain(root: Path, symbol: str, payload=None, raw: bytes | None = None, mtime: float | None = None):
    d = root / "artifacts" / "oi_chain_cache"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{symbol}.json.gz"
    if raw is not None:
        path.write_bytes(raw)
    else:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload if payload is not None else _chain(symbol), fh)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture(autouse=True)
def _no_real_etf_list(monkeypatch):
    # The real ETF list knows "TEST" as a fund; the fixtures model a stock.
    monkeypatch.setattr(oss, "_is_etf", lambda sym: False)


@pytest.fixture()
def repo(tmp_path):
    _write_chain(tmp_path, "TEST")
    (tmp_path / "artifacts" / "momx_board_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _scan(root, symbols=("TEST",), **kw):
    kw.setdefault("now_ts", NOW)
    return oss.scan(list(symbols), root=root, **kw)


def _by(result, side, pick="MAIN"):
    return [r for r in result["rows"] if r["side"] == side and r["pick"] == pick]


# --------------------------------------------------------------------------- selection

def test_main_pick_is_the_bots_contract_nearest_expiry_first_otm(repo):
    res = _scan(repo)
    call, put = _by(res, "CALL")[0], _by(res, "PUT")[0]
    d1 = (TODAY + timedelta(days=1)).isoformat()
    assert (call["strike"], call["expiry"], call["dte"]) == (101, d1, 1)   # not the expired 101C, not 100C ATM
    assert (put["strike"], put["expiry"], put["dte"]) == (99, d1, 1)
    assert (call["openInterest"], call["volume"]) == (1234, 567)
    assert res["skipped"] == []


def test_d20_and_0dte_picks(repo):
    res = _scan(repo, picks=("main", "d20", "0dte"))
    assert _by(res, "CALL", "D20")[0]["strike"] == 105
    assert _by(res, "PUT", "D20")[0]["strike"] == 94      # 95P has delta 0.21, not under 0.20
    z = _by(res, "CALL", "0DTE")[0]
    assert (z["strike"], z["dte"]) == (101, 0)


def test_builtin_copy_matches_option_track(repo):
    """The fallback copy must pick exactly what the Bot's live rules pick."""
    live_pc, live_pe, note = oss.load_picker()
    assert note is None, f"momx.option_track did not import: {note}"
    chain = _chain()
    view = {**chain, "selectedExpiryChainRows": oss.fresh_rows(chain["selectedExpiryChainRows"], TODAY)}
    for spot in (SPOT, 99.5, 101.2, 94.0):
        v = {**view, "underlyingPrice": spot}
        for bear in (False, True):
            for zero in (False, True):
                assert oss.pick_contract_copy(v, bear, zero_dte=zero) == live_pc(v, bear, zero_dte=zero)
            main = live_pc(v, bear)
            if main:
                live_e = live_pe(v, bear, main["expiry"])
                copy_e = oss.pick_entry_strike_copy(v, bear, main["expiry"])
                if live_e is None:
                    assert copy_e is None
                else:
                    keys = ("eContract", "eStrike", "eDelta", "eEntry", "eEntryBid", "eEntryAsk")
                    assert {k: copy_e[k] for k in keys} == {k: live_e[k] for k in keys}


def test_spot_reanchors_on_a_newer_scanner_price(repo):
    board = {"generatedAt": datetime.fromtimestamp(NOW - 10, oss.ET).isoformat(), "universe": ["TEST"],
             "rows": [{"symbol": "TEST", "last": 104.5}], "rest": []}
    (repo / "artifacts" / "momx_board_cache" / "Watchlist.json").write_text(json.dumps(board), encoding="utf-8")
    call = _by(_scan(repo), "CALL")[0]
    assert (call["strike"], call["spot"], call["spotSource"]) == (105, 104.5, "scanner")


def test_spot_ignores_an_older_scanner_price(repo):
    board = {"generatedAt": datetime.fromtimestamp(NOW - 3600, oss.ET).isoformat(),
             "rows": [{"symbol": "TEST", "last": 104.5}]}
    (repo / "artifacts" / "momx_board_cache" / "Watchlist.json").write_text(json.dumps(board), encoding="utf-8")
    call = _by(_scan(repo), "CALL")[0]
    assert (call["strike"], call["spotSource"]) == (101, "chain")


def test_max_dte_zero_leaves_only_today(repo):
    res = _scan(repo, max_dte=0, picks=("main", "0dte"))
    assert [r["pick"] for r in res["rows"]] == ["0DTE", "0DTE"]
    reasons = dict(res["skipped"])
    assert reasons["TEST call"].startswith("no expiry 1+ days out")


# --------------------------------------------------------------------------- spread math + verdict

def test_spread_math():
    s = oss.spread_stats(1.00, 1.10)
    assert s["mid"] == pytest.approx(1.05)
    assert s["spread"] == pytest.approx(0.10)
    assert s["spreadPct"] == pytest.approx(0.10 / 1.05)
    assert s["roundTripPct"] == pytest.approx(0.10 / 1.05)  # half in + half out
    z = oss.spread_stats(0, 0.10)                            # no bid
    assert (z["mid"], z["spreadPct"]) == (pytest.approx(0.05), pytest.approx(2.0))
    for bid, ask in ((1.2, 1.0), (None, 1.0), (1.0, 0), (-0.1, 1.0), ("x", 1.0), (float("nan"), 1.0)):
        assert oss.spread_stats(bid, ask) is None


def test_verdict_cutoffs():
    assert oss.verdict(oss.spread_stats(0.95, 1.05)["spreadPct"]) == "TIGHT"  # exactly 10% after float noise
    assert oss.verdict(0.0) == "TIGHT"
    assert oss.verdict(0.1001) == "OK"
    assert oss.verdict(0.15) == "OK"
    assert oss.verdict(0.1501) == "WIDE"
    assert oss.verdict(2.0) == "WIDE"
    assert oss.verdict(None) == "?"


def test_rows_carry_verdicts_and_sort_tightest_first(repo):
    res = _scan(repo, picks=("main", "d20"))
    got = [(r["side"], r["pick"], r["verdict"]) for r in res["rows"]]
    assert got[0] == ("CALL", "MAIN", "TIGHT") and ("PUT", "MAIN", "OK") in got
    pcts = [r["spreadPct"] for r in res["rows"]]
    assert pcts == sorted(pcts)


# --------------------------------------------------------------------------- missing / odd data

def test_missing_and_odd_chains_are_skipped_with_a_reason(repo):
    _write_chain(repo, "BADGZ", raw=b"not gzip at all")
    _write_chain(repo, "TRUNC", raw=gzip.compress(b'{"symbol": "TRUNC"')[:-6])
    _write_chain(repo, "OLDFMT", _chain("OLDFMT", schema=None))
    _write_chain(repo, "NOROWS", _chain("NOROWS", rows=[]))
    _write_chain(repo, "OLD", _chain("OLD"), mtime=NOW - 6 * 86400)
    expired = [_row("CALL", TODAY - timedelta(days=3), 101, 1.0, 1.1, 0.4)]
    _write_chain(repo, "EXPIRED", _chain("EXPIRED", rows=expired))
    _write_chain(repo, "NOSPOT", _chain("NOSPOT", spot=None))
    no_otm = [_row("CALL", TODAY + timedelta(days=1), 90, 10.0, 10.2, 0.9)]
    _write_chain(repo, "NOOTM", _chain("NOOTM", rows=no_otm))
    _write_chain(repo, "LIST", raw=gzip.compress(b"[1, 2, 3]"))
    syms = ["MISSING", "BADGZ", "TRUNC", "OLDFMT", "NOROWS", "OLD", "EXPIRED", "NOSPOT", "NOOTM", "LIST",
            "BAD/SYM", "TEST"]
    res = _scan(repo, symbols=syms, sides=(False,))
    why = dict(res["skipped"])
    assert why["MISSING"] == "no saved chain"
    assert why["BADGZ"].startswith("chain file unreadable")
    assert why["TRUNC"].startswith("chain file unreadable")
    assert why["OLDFMT"].startswith("old chain format")
    assert why["NOROWS"] == "chain has no contracts"
    assert why["OLD"].startswith("chain over 5 days old")
    assert why["EXPIRED"].startswith("no unexpired expiry")
    assert why["NOSPOT"] == "no stock price"
    assert why["NOOTM call"] == "no OTM call with a bid/ask"
    assert why["LIST"] == "chain file has no symbol"
    assert why["BAD/SYM"].startswith("not a symbol")
    assert [r["ticker"] for r in res["rows"]] == ["TEST"]


def test_cli_prints_ascii_table_header_and_stale_warning(repo, capsys):
    _write_chain(repo, "OLDQ", _chain("OLDQ", scanned_ago=3 * 3600))
    csv_path = repo / "out.csv"
    code = oss.main(["--symbols", "TEST,OLDQ,MISSING", "--csv", str(csv_path)], root=repo, now_ts=NOW)
    out = capsys.readouterr().out
    assert code == 0
    out.encode("ascii")  # Windows console safe
    lines = out.splitlines()
    assert lines[0].startswith("VERDICT (spread as % of mid): TIGHT <=10% | OK 10-15% | WIDE >15%")
    assert "No effect if you always fill at the mid" in lines[0]
    assert any(l.startswith("TICKER SIDE EXPIRY") for l in lines)
    assert any(l.startswith("STALE CHAINS: 1 older than 60 min") and "OLDQ 3.0h" in l for l in lines)
    assert any("no saved chain (1): MISSING" in l for l in lines)
    assert any(l.startswith("OLDQ") and "STALE 3.0h" in l for l in lines)
    table = [l for l in lines if l.startswith(("TEST", "OLDQ"))]
    assert len(table) == 4 and "TIGHT" in table[0].split()
    with open(csv_path, encoding="ascii") as fh:
        assert len(fh.read().strip().splitlines()) == 1 + 4


def test_cli_top_and_side(repo, capsys):
    assert oss.main(["--symbols", "TEST", "--side", "put", "--top", "1"], root=repo, now_ts=NOW) == 0
    out = capsys.readouterr().out
    rows = [l for l in out.splitlines() if l.startswith("TEST")]
    assert len(rows) == 1 and rows[0].split()[1] == "PUT" and rows[0].split()[-1] == "OK"


# --------------------------------------------------------------------------- which tickers

def test_default_list_is_the_active_scanner_list(repo):
    (repo / "artifacts" / "momx_universe.json").write_text(json.dumps(
        {"schemaVersion": 2, "active": "Liquid Movers", "lists": {"Liquid Movers": ["test", "AAA", "TEST"]}}),
        encoding="utf-8")
    assert oss.resolve_symbols(repo) == ("Liquid Movers", ["TEST", "AAA"], "momx_universe.json")
    assert oss.resolve_symbols(repo, "liquid_movers")[1] == ["TEST", "AAA"]
    assert oss.resolve_symbols(repo, symbols="aapl, tsla")[1] == ["AAPL", "TSLA"]


def test_list_falls_back_to_board_then_watchlist_then_mag7(repo, capsys):
    (repo / "watchlist.txt").write_text("AAPL, MSFT\nTEST\n", encoding="utf-8")
    assert oss.resolve_symbols(repo) == ("Watchlist", ["AAPL", "MSFT", "TEST"], "watchlist.txt")
    board = {"list": "Watchlist", "universe": ["NVDA", "TEST"], "rows": [], "rest": []}
    (repo / "artifacts" / "momx_board_cache" / "Watchlist.json").write_text(json.dumps(board), encoding="utf-8")
    assert oss.resolve_symbols(repo, "Watchlist")[1] == ["NVDA", "TEST"]
    assert oss.resolve_symbols(repo, "Mag7")[1] == list(oss.MAG7_SEED)
    assert oss.main(["--list", "No Such List"], root=repo, now_ts=NOW) == 2
    assert "No tickers found for list 'No Such List'" in capsys.readouterr().out


# --------------------------------------------------------------------------- review fixes (2026-11-03)

def test_cost_per_contract_is_the_spread_in_dollars():
    s = oss.spread_stats(1.00, 1.10) if hasattr(oss, "spread_stats") else None
    if s is None:
        pytest.skip("spread helper renamed")
    assert s["costPerContract"] == pytest.approx(10.0)


def test_missing_or_infinite_counts_never_crash_and_show_as_unknown(repo):
    rows = _rows()
    for r in rows:
        if isinstance(r, dict):
            r["open_interest"], r["volume"] = float("inf"), None
    _write_chain(repo, "TEST", _chain(rows=rows))
    res = _scan(repo)
    call = _by(res, "CALL")[0]
    assert call["openInterest"] is None and call["volume"] is None


def test_a_malformed_board_does_not_crash_the_spot_lookup(tmp_path):
    (tmp_path / "board.json").write_text(json.dumps({"generatedAt": datetime.now(oss.ET).isoformat(), "rows": {"x": 1}}))
    assert oss.board_spots(tmp_path, time.time()) == {}


def test_etfs_the_bot_skips_are_labelled(repo, monkeypatch):
    monkeypatch.setattr(oss, "_is_etf", lambda sym: sym == "TEST")
    res = _scan(repo)
    assert all("ETF/index - Bot skips" in r["notes"] for r in res["rows"])
