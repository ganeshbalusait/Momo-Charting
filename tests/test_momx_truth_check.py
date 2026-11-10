"""BOT truth check: virtual BOT trades vs real Alpaca paper fills (2026-11-04)."""
import json

from momx import truth_check as tc

AT = "2026-11-04T09:30:03-05:00"


def bot(rule="rvol", symbol="AAPL", at=AT, entry=1.0, exit_=1.5, **extra):
    return {"rule": rule, "symbol": symbol, "at": at, "entry": entry, "entryBid": entry - 0.05,
            "entryAsk": entry + 0.05, "exitPrice": exit_, "exitBid": exit_ - 0.05, "exitAsk": exit_ + 0.05,
            "status": "closed", **extra}


def paper(status, rule="rvol", symbol="AAPL", at=AT, side="bull", **extra):
    return {"side": side, "rule": rule, "symbol": symbol, "at": at, "qty": 3, "botEntry": 1.0,
            "buyLimit": 1.0, "status": status, **extra}


def test_exit_zero_bid_is_unsellable():
    assert tc.bot_exit_value({"exitPrice": 0.01, "exitBid": 0, "exitAsk": 0.01}) == 0.0


def test_exit_uses_mid():
    assert tc.bot_exit_value({"exitPrice": 1.0, "exitBid": 1.0, "exitAsk": 1.2}) == 1.1


def test_exit_falls_back_to_price_and_none():
    assert tc.bot_exit_value({"exitPrice": 0.7}) == 0.7
    assert tc.bot_exit_value({}) is None


def test_entry_uses_mid():
    assert tc.bot_entry_value({"entry": 9.0, "entryBid": 1.0, "entryAsk": 1.2}) == 1.1


def test_compare_counts_missed_filled_open():
    rows = [paper("closed", symbol="A", buyFill=1.0, pl=10.0), paper("missed", symbol="B"),
            paper("buying", symbol="C")]
    lookup = {("bull", "rvol", s, AT): bot(symbol=s) for s in "ABC"}
    acct = tc.compare({"paper3": rows}, lookup)["accounts"]["paper3"]
    assert (acct["orders"], acct["filled"], acct["missed"], acct["open"]) == (3, 1, 1, 1)
    assert acct["fillRatePct"] == 50.0


def test_compare_matched_pnl():
    rows = [paper("closed", buyFill=1.05, pl=90.0)]
    acct = tc.compare({"paper3": rows}, {("bull", "rvol", "AAPL", AT): bot()})["accounts"]["paper3"]
    assert acct["matched"] == {"n": 1, "botPnl": 150.0, "botRoi": 50.0, "paperPnl": 90.0, "paperRoi": 28.6}


def test_missed_bot_pnl_and_biggest():
    rows = [paper("missed", buyLimit=0.95)]
    acct = tc.compare({"paper3": rows}, {("bull", "rvol", "AAPL", AT): bot(exit_=3.0)})["accounts"]["paper3"]
    assert acct["missedBotPnl"] == 600.0
    assert acct["biggestMissed"][0]["symbol"] == "AAPL"
    assert acct["biggestMissed"][0]["botPct"] == 200.0
    assert acct["biggestMissed"][0]["limit"] == 0.95


def test_unmatched_and_bad_quote_excluded():
    rows = [paper("closed", symbol="X", buyFill=1.0, pl=5.0), paper("closed", symbol="Y", buyFill=1.0, pl=5.0)]
    lookup = {("bull", "rvol", "Y", AT): bot(symbol="Y", badQuote=True)}
    acct = tc.compare({"paper3": rows}, lookup)["accounts"]["paper3"]
    assert acct["unmatched"] == 2
    assert acct["matched"]["n"] == 0
    assert acct["filled"] == 2


def test_by_rule_rows():
    rows = [paper("closed", buyFill=1.0, pl=30.0), paper("missed", symbol="B")]
    lookup = {("bull", "rvol", "AAPL", AT): bot(), ("bull", "rvol", "B", AT): bot(symbol="B")}
    by = tc.compare({"paper3": rows}, lookup)["accounts"]["paper3"]["byRule"]
    assert by == [{"side": "bull", "rule": "rvol", "orders": 2, "missed": 1, "fillRatePct": 50.0,
                   "botRoi": 50.0, "paperRoi": 10.0}]


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_build_dedupes_carried_rows_latest_wins(tmp_path):
    key = "bull|rvol|AAPL|" + AT
    _write(tmp_path / "momx_paper_mirror/paper3/2026-11-04.json", {"rows": {key: paper("buying")}})
    _write(tmp_path / "momx_paper_mirror/paper3/2026-11-05.json",
           {"rows": {key: paper("closed", buyFill=1.0, pl=12.0, carried=True)}})
    _write(tmp_path / "momx_option_track/2026-11-04.json", {"trades": [bot()]})
    report = tc.build(tmp_path, days=30, today="2026-11-05")
    acct = report["accounts"]["paper3"]
    assert report["available"] is True
    assert (acct["orders"], acct["filled"], acct["open"]) == (1, 1, 0)
    assert acct["matched"]["paperPnl"] == 12.0


def test_build_reads_bear_dir(tmp_path):
    key = "bear|rvol|AAPL|" + AT
    _write(tmp_path / "momx_paper_mirror/paper5/2026-11-04.json",
           {"rows": {key: paper("closed", side="bear", buyFill=1.0, pl=-20.0)}})
    _write(tmp_path / "bear/momx_option_track/2026-11-04.json", {"trades": [bot(exit_=0.8)]})
    acct = tc.build(tmp_path, days=30, today="2026-11-04")["accounts"]["paper5"]
    assert acct["matched"]["n"] == 1
    assert acct["matched"]["botPnl"] == -60.0


def test_build_skips_days_outside_window(tmp_path):
    _write(tmp_path / "momx_paper_mirror/paper3/2026-09-01.json",
           {"rows": {"bull|rvol|A|x": paper("missed", at="2026-09-01T09:30:00-04:00")}})
    report = tc.build(tmp_path, days=5, today="2026-11-04")
    assert report["accounts"]["paper3"]["orders"] == 0


def test_build_empty_is_unavailable(tmp_path):
    report = tc.build(tmp_path, days=30, today="2026-11-04")
    assert report["available"] is False
    assert report["accounts"] == {}


def test_build_survives_a_broken_file(tmp_path):
    bad = tmp_path / "momx_paper_mirror/paper3/2026-11-04.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    assert tc.build(tmp_path, days=30, today="2026-11-04")["available"] is False


def test_new_order_rules_are_reported_apart():
    rows = [paper("missed", symbol="OLD"),
            paper("closed", symbol="NEW", exec=2, buyFill=1.0, pl=30.0),
            paper("missed", symbol="NEW2", exec=2)]
    lookup = {("bull", "rvol", s, AT): bot(symbol=s) for s in ("OLD", "NEW", "NEW2")}
    acct = tc.compare({"paper3": rows}, lookup)["accounts"]["paper3"]
    assert (acct["orders"], acct["filled"], acct["missed"]) == (3, 1, 2)
    new = acct["newRules"]
    assert (new["orders"], new["filled"], new["missed"], new["fillRatePct"]) == (2, 1, 1, 50.0)
    assert new["matched"]["paperRoi"] == 10.0


def test_no_new_rules_section_before_any_v2_order():
    acct = tc.compare({"paper3": [paper("missed")]}, {})["accounts"]["paper3"]
    assert "newRules" not in acct


def test_a_020_delta_paper_fill_is_compared_with_the_bots_020_delta_contract():
    """2026-11-09: paper buys the 0.20-delta contract, so the BOT's claim must be for that same option."""
    from momx import truth_check as tc
    at = "2026-11-10T09:35:00-05:00"
    bot = {("bull", "level1", "AAPL", at): {"entry": 3.0, "entryBid": 2.9, "entryAsk": 3.1, "exitPrice": 6.0, "exitBid": 5.9,
                                            "exitAsk": 6.1, "eEntry": 0.5, "eEntryBid": 0.48, "eEntryAsk": 0.52,
                                            "eExit": 0.4, "eExitBid": 0.38, "eExitAsk": 0.42}}
    row = {"side": "bull", "rule": "level1", "symbol": "AAPL", "at": at, "qty": 5, "status": "closed", "buyFill": 0.52,
           "pl": -60.0, "leg": "entry"}
    m = tc._stats([row], bot)["matched"]
    assert m["n"] == 1 and m["botPnl"] == -50.0 and m["botRoi"] == -20.0        # (0.40 - 0.50) x 5 contracts
    old = tc._stats([{**row, "leg": None}], bot)["matched"]
    assert old["botPnl"] == 1500.0                                               # a main-contract row is unchanged


def test_the_research_by_rule_table_counts_a_level_order_under_every_level_like_the_scorecard():
    from momx import truth_check as tc
    at = "2026-11-10T09:35:00-05:00"
    bot = {("bull", "level3", "AAPL", at): {"eEntry": 0.5, "eEntryBid": 0.48, "eEntryAsk": 0.52, "eExit": 0.7, "eExitBid": 0.68, "eExitAsk": 0.72}}
    rows = [{"side": "bull", "rule": "level3", "symbol": "AAPL", "at": at, "qty": 5, "status": "closed", "buyFill": 0.52,
             "pl": 80.0, "leg": "entry", "levels": ["level1", "level2", "level3"]},
            {"side": "bull", "rule": "level2", "symbol": "MSFT", "at": at, "qty": 5, "status": "missed", "leg": "entry",
             "levels": ["level1", "level2"]}]
    out = tc._stats(rows, bot)
    assert (out["orders"], out["filled"], out["missed"], out["matched"]["n"]) == (2, 1, 1, 1)        # the account: each order once
    by = {r["rule"]: r for r in out["byRule"]}
    assert (by["level1"]["orders"], by["level1"]["missed"]) == (2, 1)
    assert (by["level2"]["orders"], by["level2"]["missed"]) == (2, 1)
    assert (by["level3"]["orders"], by["level3"]["missed"]) == (1, 0) and by["level3"]["fillRatePct"] == 100.0
