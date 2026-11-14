"""Grading the paper AI gate against real Bot outcomes (scripts/grade_paper_ai_gate.py)."""
import gzip
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import grade_paper_ai_gate as g  # noqa: E402
from momx import ai_filter as ai  # noqa: E402


def trade(symbol, at, entry, exit_price, rule="MX A+"):
    return {"rule": rule, "symbol": symbol, "at": at, "entry": entry, "exitPrice": exit_price, "status": "closed"}


def review(side, t, approved, provider="ollama"):
    return {"key": ai.trade_key(side, t), "approved": approved, "provider": provider, "probability": 0.6}


@pytest.fixture
def live_dirs(tmp_path, monkeypatch):
    bull, bear, filt = tmp_path / "bull", tmp_path / "bear", tmp_path / "filter"
    for d in (bull, bear, filt / "paper", filt / "dataset"):
        d.mkdir(parents=True)
    monkeypatch.setattr(ai, "_roots", lambda: {"bull": bull, "bear": bear})
    monkeypatch.setattr(ai, "_dir", lambda: filt)
    return bull, bear, filt


def write_day(root, day, trades):
    (root / f"{day}.json").write_text(json.dumps({"trades": trades}), encoding="utf-8")


def test_wins_and_returns_follow_the_ai_filter_label():
    assert g.outcome(trade("A", "t", 1.0, 1.5)) == (1, pytest.approx(0.5))
    assert g.outcome(trade("A", "t", 2.0, 1.0)) == (0, pytest.approx(-0.5))
    assert g.outcome(trade("A", "t", 1.0, 1.0))[0] == 0          # flat is not a win
    assert g.outcome({"entry": 1.0, "exitPrice": None, "last": 3.0}) is None   # unpriced: never guessed
    assert g.outcome({**trade("A", "t", 1.0, 2.0), "badQuote": True}) is None
    assert g.outcome({"_win": 1, "entry": 1.0, "exitPrice": 1.2}) == (1, pytest.approx(0.2))  # dataset row


def test_last_review_of_a_key_counts_and_torn_lines_are_skipped(tmp_path):
    t = trade("TSLA", "2026-11-03T09:40:00-05:00", 1.0, 2.0)
    first, last = review("bull", t, False), review("bull", t, True)
    (tmp_path / "2026-11-03.jsonl").write_text(json.dumps(first) + "\n" + json.dumps(last) + "\n{torn",
                                               encoding="utf-8")
    (tmp_path / "2026-11-01.jsonl").write_text(json.dumps(review("bull", trade("X", "y", 1, 1), True)),
                                               encoding="utf-8")
    got = g.load_reviews(tmp_path, since="2026-11-02")
    assert list(got) == [ai.trade_key("bull", t)] and got[ai.trade_key("bull", t)]["approved"] is True


def test_groups_split_approved_skipped_and_unanswered():
    ts = [trade(f"S{i}", f"2026-11-03T10:{i:02d}:00-05:00", 1.0, 2.0 if i % 2 == 0 else 0.5) for i in range(8)]
    reviews = {r["key"]: r for r in (
        review("bull", ts[0], True), review("bull", ts[1], True),            # 1 win 1 loss
        review("bull", ts[2], False), review("bull", ts[3], False, "openclaw"),
        review("bull", ts[4], False, "unavailable"),
        {"key": "bull|gone|ZZZ|old", "approved": True, "provider": "ollama"})}
    report = g.grade({"2026-11-03": [("bull", t) for t in ts] + [("bull", ts[0])]}, reviews)
    groups = report["groups"]
    assert groups["all"]["trades"] == 8                      # duplicate signal counted once
    assert groups["reviewed"]["trades"] == 5
    assert (groups["approved"]["trades"], groups["approved"]["winPct"]) == (2, 50.0)
    assert (groups["skipped"]["trades"], groups["skipped"]["winPct"]) == (2, 50.0)
    assert groups["noAnswer"]["trades"] == 1
    assert set(report["byProvider"]) == {"ollama", "openclaw"}
    assert report["reviewsWithoutTrade"] == 1
    assert "Too few trades" in g.verdict_text(report)


def test_a_real_gap_is_called_real_and_luck_is_not():
    big = g.summarize([(1, 1.0)] * 70 + [(0, -1.0)] * 30)
    low = g.summarize([(1, 1.0)] * 40 + [(0, -1.0)] * 60)
    assert g.difference(big, low)["real"] is True
    same = g.summarize([(1, 1.0)] * 52 + [(0, -1.0)] * 48)
    assert g.difference(same, g.summarize([(1, 1.0)] * 50 + [(0, -1.0)] * 50))["real"] is False
    s = g.summarize([(1, 0.5)] * 3 + [(0, -0.5)])
    assert s["winPct"] == 75.0 and s["winLow"] < 75 < s["winHigh"] and s["avgReturnPct"] == 25.0


def test_whole_script_reads_bot_files_and_dataset_and_writes_nothing(live_dirs, capsys):
    bull, bear, filt = live_dirs
    win, loss = trade("NVDA", "2026-11-04T09:45:00-05:00", 1.0, 1.4), trade("AMD", "2026-11-04T09:50:00-05:00", 1.0, 0.7)
    put = trade("SPY", "2026-11-04T10:00:00-05:00", 2.0, 3.0)
    write_day(bull, "2026-11-04", [win, loss])
    write_day(bear, "2026-11-04", [put])
    old = {"side": "bull", "rule": "MX A+", "symbol": "META", "at": "2026-09-01T09:40:00-04:00",
           "features": {}, "win": 1, "ret": 0.3}
    with gzip.open(filt / "dataset" / "2026-09-01.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(old) + "\n")
    (filt / "paper" / "2026-11-04.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (review("bull", win, True), review("bull", loss, False),
                                          review("bear", put, True))), encoding="utf-8")
    before = sorted(p for p in filt.rglob("*"))

    assert g.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["groups"]["all"]["trades"] == 4             # 3 Bot trades + 1 permanent-dataset trade
    assert report["groups"]["approved"] == {**report["groups"]["approved"], "trades": 2, "winPct": 100.0}
    assert report["groups"]["skipped"]["winPct"] == 0.0
    assert g.main(["--since", "2026-11-01"]) == 0
    text = capsys.readouterr().out
    assert "Every Bot trade (baseline)" in text and "Verdict:" in text and "since 2026-11-01" in text
    assert sorted(p for p in filt.rglob("*")) == before        # read-only
