"""Shadow AI filter (momx/ai_filter.py): entry-time features, labels, model, walk-forward."""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np

from momx import ai_filter as ai


def _t(i, win, rule="zs2", jev=0.2):
    return {"rule": rule, "symbol": f"S{i}", "at": "2026-10-15T10:05:00-04:00", "entryAt": "2026-10-15T10:05:00-04:00",
            "entry": 1.0, "entryBid": 0.95, "entryAsk": 1.05, "exitPrice": 1.5 if win else 0.6, "status": "closed",
            "strike": 101.0, "spot": 100.0, "delta": 0.3, "expectedMove": 2.0, "roi2em": 1.2, "riskOk": True,
            "expiry": "2026-10-16", "jevP": jev, "momentum": "extended", "board": "Watchlist",
            "callWalls": [{"strike": 105.0}], "best": 9.9, "close": 0.1}


def test_label_and_no_future_fields_in_features():
    assert ai.label(_t(0, True)) == 1 and ai.label(_t(0, False)) == 0
    assert ai.label({"entry": 1.0, "status": "open"}) is None and ai.label({**_t(0, True), "badQuote": True}) is None
    f = ai.features(_t(0, True), "bull")
    assert f["hour"] == "h1" and abs(f["spreadPct"] - 0.1) < 1e-9 and abs(f["moneyness"] - 0.01) < 1e-9
    assert "best" not in f and "close" not in f and "exitPrice" not in f


def test_entry_prediction_uses_scanner_snapshot_without_history_or_network(monkeypatch):
    from types import SimpleNamespace
    captured = []
    def predict(f):
        captured.append(f)
        return .63
    scorer = SimpleNamespace(model=SimpleNamespace(predict=predict), champion="lr")
    monkeypatch.setattr(ai, "_SCORER", scorer)
    trade = {**_t(0, True), "setup": {"rvol5m": 3.1, "sqzOn": 2, "skitAvg": 70, "grade": 3}}
    stamp = datetime(2026, 10, 15, 10, 5, tzinfo=ai.ET).timestamp()
    forecast = ai.predict_at_entry("bull", trade, stamp)
    assert forecast["probability"] == .63 and forecast["phase"] == "before_entry"
    assert captured[0]["s_skitAvg"] == 70 and captured[0]["s_sqzOn"] == 2 and captured[0]["s_rvol5m"] == 3.1
    assert not {"exitPrice", "close", "best"}.intersection(captured[0])
    assert ai.predict_at_entry("bull", {**trade, "exitPrice": 999}, stamp) == forecast


def test_invalid_or_missing_entry_model_never_blocks_trade(monkeypatch):
    from types import SimpleNamespace
    from momx.paper_learning import trade_first
    monkeypatch.setattr(ai, "_SCORER", None)
    assert trade_first("bull", _t(0, False), 1)["approved"]
    monkeypatch.setattr(ai, "_SCORER", SimpleNamespace(model=SimpleNamespace(predict=lambda f: float("nan")), champion="nn"))
    result = trade_first("bull", _t(0, False), 1)
    assert result["approved"] and not result["prediction"]["available"]


def test_scoring_loop_preserves_prediction_made_before_entry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    stamp = datetime(2026, 10, 15, 10, 5, tzinfo=ai.ET)
    forecast = {"probability": .63, "phase": "before_entry", "family": "lr", "scoredAt": stamp.isoformat()}
    trade = {**_t(0, True), "aiDecision": {"prediction": forecast}}
    scorer = ai.Scorer({"bull": SimpleNamespace(trades=lambda: [trade])}, tmp_path)
    (tmp_path / "model.json").write_text('{}')
    scorer.model = SimpleNamespace(predict=lambda f: .99)
    monkeypatch.setattr(scorer, "_load_model", lambda: None)
    def unexpected(*args):
        raise AssertionError("Must use the original prediction, not reconstruct features")
    monkeypatch.setattr(scorer, "_record_for", unexpected)
    scorer.tick(stamp)
    key = ai.trade_key("bull", trade)
    assert scorer.scores[key] == .63 and scorer.score_meta[key]["phase"] == "before_entry"


def test_model_learns_a_real_signal_and_round_trips(tmp_path):
    rows, ys = [], []
    for i in range(400):
        win = i % 2 == 0
        rows.append(ai.features(_t(i, win, rule="zs2" if win else "rvol"), "bull"))
        ys.append(int(win))
    m = ai.Model().fit(rows, ys)
    good = m.predict(ai.features(_t(1, True, rule="zs2"), "bull"))
    bad = m.predict(ai.features(_t(1, False, rule="rvol"), "bull"))
    assert good > 0.6 > 0.4 > bad
    back = ai.Model.from_json(json.loads(json.dumps(m.to_json())))
    assert abs(back.predict(rows[0]) - m.predict(rows[0])) < 1e-6


def test_walk_forward_never_grades_a_training_day():
    days = {f"2026-10-{d:02d}": [("bull", _t(i, i % 2 == 0)) for i in range(40)] for d in range(1, 9)}
    r = ai.walk_forward(days)
    assert r["testDays"] == len(days) - ai.MIN_TRAIN_DAYS
    assert [p["day"] for p in r["perDay"]] == sorted(days)[ai.MIN_TRAIN_DAYS:]
    assert set(r["bySide"]) == {"bull", "bear"} and r["bySide"]["bull"]["all"]["trades"] == r["all"]["trades"]


def test_train_now_writes_model_and_report(tmp_path):
    bull = tmp_path / "bull"
    bull.mkdir()
    for d in range(1, 8):
        (bull / f"2026-10-{d:02d}.json").write_text(json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    rep = ai.train_now(tmp_path / "out", roots={"bull": bull}, today="2026-10-20")
    assert (tmp_path / "out" / "model.json").exists() and rep["trainDays"] == 7 and "auc" in rep


def test_permanent_dataset_is_written_and_trains_after_the_bot_file_is_gone(tmp_path):
    bull = tmp_path / "bull"
    bull.mkdir()
    for d in range(1, 8):
        (bull / f"2026-10-{d:02d}.json").write_text(json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    out = tmp_path / "out"
    ai.train_now(out, roots={"bull": bull}, today="2026-10-20")
    files = sorted((out / "dataset").glob("*.jsonl.gz"))
    assert len(files) == 7
    (bull / "2026-10-01.json").unlink()                     # the Bot pruned its oldest day
    days = ai.load_days({"bull": bull}, dataset=out / "dataset")
    assert "2026-10-01" in days and len(days["2026-10-01"]) == 30
    rows, ys, _ = ai._xy(days["2026-10-01"])
    assert len(rows) == 30 and set(ys) == {0, 1} and rows[0]["rule"] == "bull:zs2"
    rep = ai.train_now(out, roots={"bull": bull}, today="2026-10-20")
    assert rep["trainDays"] == 7 and "nn" in rep


def test_setup_column_tag_gets_the_best_score_per_ticker_and_side(monkeypatch):
    s = ai.Scorer({}, directory=None)
    at = "2026-10-15T10:05:00-04:00"
    now = datetime.fromisoformat("2026-10-15T11:00:00-04:00")
    s._day = "2026-10-15"
    s.scores = {f"bull|zs2|AAPL|{at}": 0.58, f"bull|rvol|AAPL|{at}": 0.62, f"bear|jev|AAPL|{at}": 0.7, f"bull|go|MSFT|{at}": 0.4}
    monkeypatch.setattr(ai, "_SCORER", s)
    payload = {"rows": [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "NVDA", "aiScore": {"p": 0.9}}], "rest": []}
    ai.mark_rows(payload, "bull", now)
    assert payload["rows"][0]["aiScore"] == {"p": 0.62, "rule": "rvol", "model": "saved local score", "symbol": "AAPL", "side": "bull", "at": at, "day": "2026-10-15"}
    assert payload["rows"][1]["aiScore"]["p"] == 0.4 and "aiScore" not in payload["rows"][2]
    bear = {"rows": [{"symbol": "AAPL"}]}
    ai.mark_rows(bear, "bear", now)
    assert bear["rows"][0]["aiScore"]["p"] == 0.7
    assert bear["rows"][0]["aiScore"]["side"] == "bear"


def test_ai_marks_reject_previous_day_and_corrupt_scores(monkeypatch, tmp_path):
    s = ai.Scorer({}, tmp_path)
    s._day = "2026-10-15"
    s.scores = {"bull|go|OLD|2026-10-14T15:00:00-04:00": 0.9,
                "bull|go|BAD|2026-10-15T10:00:00-04:00": 1.4,
                "bull|go|NAN|2026-10-15T10:00:00-04:00": float("nan"),
                "bull|go|aapl|2026-10-15T10:00:00-04:00": 0.6204,
                "bull|rvol|AAPL|2026-10-15T10:00:00-04:00": 0.6203}
    monkeypatch.setattr(ai, "_SCORER", s)
    now = datetime.fromisoformat("2026-10-15T11:00:00-04:00")
    scores = ai.best_scores("bull", now)
    assert set(scores) == {"AAPL"} and scores["AAPL"]["rule"] == "go"
    assert scores["AAPL"]["p"] == 0.6204
    assert ai.best_scores("bull", datetime.fromisoformat("2026-10-16T00:01:00-04:00")) == {}


def test_record_keeps_the_model_that_scored_the_trade_after_model_switch(monkeypatch, tmp_path):
    scorer = ai.Scorer({}, tmp_path)
    scorer._day, scorer.champion = "2026-10-15", "boosted"
    key = "bull|go|AAPL|2026-10-15T10:00:00-04:00"
    scorer.scores[key] = .62
    scorer.score_meta[key] = {"family": "lr", "scoredAt": "2026-10-15T10:01:00-04:00"}
    monkeypatch.setattr(ai, "_SCORER", scorer)
    mark = ai.best_scores("bull", datetime.fromisoformat("2026-10-15T11:00:00-04:00"))["AAPL"]
    assert mark["model"] == "logistic" and mark["scoredAt"].endswith("10:01:00-04:00")


def test_day_rollover_clears_scores_before_model_loading(monkeypatch, tmp_path):
    s = ai.Scorer({}, tmp_path)
    s._day, s.scores = "2026-10-14", {"yesterday": 0.9}
    monkeypatch.setattr(s, "_retrain", lambda *a: None)
    monkeypatch.setattr(s, "_load_model", lambda: None)
    s.tick(datetime.fromisoformat("2026-10-15T00:01:00-04:00"))
    assert s._day == "2026-10-15" and s.scores == {}


def test_neural_challenger_clips_outliers_and_preserves_legacy_models():
    x = np.array([[-1., 0.], [0., 1.], [1., 0.], [0., -1.]])
    net = ai.nn_fit(x, np.array([0., 1., 1., 0.]), epochs=5, hidden=(8,4,2), input_clip=6., activation="leaky_relu")
    extreme = np.array([[1e30, -1e30], [float("inf"), float("nan")]])
    values = net(extreme)
    assert np.isfinite(values).all() and ((values >= 0) & (values <= 1)).all()
    restored = ai.NeuralNet.from_json(net.to_json())
    assert np.allclose(values, restored(extreme), atol=1e-5)
    legacy = ai.NeuralNet.from_json({"W": [[[1.]]], "B": [[0.]]})
    assert legacy.input_clip is None and legacy.activation == "relu"


def test_live_score_index_is_reused_and_invalidated_when_a_trade_arrives(monkeypatch, tmp_path):
    now = datetime.now(ai.ET)
    s = ai.Scorer({}, tmp_path)
    s._day = now.date().isoformat()
    s.scores = {f"bull|go|AAPL|{now.isoformat()}": 0.6}
    monkeypatch.setattr(ai, "_SCORER", s)
    first = ai.best_scores("bull")
    assert ai.best_scores("bull") is first
    s.scores[f"bull|go|TSLA|{now.isoformat()}"] = 0.7
    assert set(ai.best_scores("bull")) == {"AAPL", "TSLA"}


def test_record_column_uses_only_earlier_days():
    days = {"2026-10-01": [("bull", _t(i, True)) for i in range(4)],
            "2026-10-02": [("bull", _t(i, False)) for i in range(4)],
            "2026-10-03": [("bull", _t(0, True))]}
    ai.add_records(days)
    assert days["2026-10-01"][0][1]["_recN"] == 0                       # nothing before day 1
    assert days["2026-10-02"][0][1]["_recWin"] == 1.0                   # day 1 only: all wins
    t3 = days["2026-10-03"][0][1]
    assert t3["_recN"] == 8 and t3["_recWin"] == 0.5                     # days 1-2, never day 3


def test_mistakes_are_weighted_up_and_reported():
    w = ai.learning_weights(["d1", "d2", "d2"], [1, 1, 0], [0.9, 0.2, 0.3], ["d1", "d2"])
    assert w[1] > w[2]                                   # the recent wrong call (said lose, won) counts more
    rep = ai.mistakes(("d2", [(("bull", _t(1, False)), 0.8), (("bull", _t(2, True)), 0.2)]))
    assert rep["badTakes"][0]["p"] == 0.8 and rep["missedWinners"][0]["p"] == 0.2 and rep["wrong"] == 2


def test_setup_snapshot_feeds_the_model():
    from momx import option_track as ot
    row = {"pctChange": 2.5, "rvol": {"5m": {"value": 3.1}}, "adx": {"5m": {"adx": 30, "plus": 25, "minus": 10}},
           "grade": {"letter": "A+"}, "skittles": {"2h": {"value": 80}, "D": {"value": 60}}, "scanReasons": ["a", "b"]}
    snap = ot.setup_snapshot(row)
    assert snap["rvol5m"] == 3.1 and snap["di5m"] == 15 and snap["grade"] == 3 and snap["skitAvg"] == 70
    f = ai.features({**_t(0, True), "setup": snap}, "bear")
    assert f["s_rvol5m"] == 3.1 and f["s_pct"] == -2.5 and f["s_di5m"] == -15     # read the bear way


def test_neural_net_promoted_after_5_winning_nights_and_demoted_after_3_losing():
    st = None
    for night in range(4):
        st = ai.decide_champion(st, 0.53, 0.55, f"n{night}")
    assert st["champion"] == "lr" and st["nnAhead"] == 4
    st = ai.decide_champion(st, 0.53, 0.55, "n5")
    assert st["champion"] == "nn" and st["log"][-1]["event"] == "neural net promoted"
    st = ai.decide_champion(st, 0.56, 0.55, "n6")
    st = ai.decide_champion(st, 0.56, 0.57, "n7")            # ahead again: the behind streak resets
    assert st["champion"] == "nn" and st["nnBehind"] == 0
    for night in range(3):
        st = ai.decide_champion(st, 0.56, 0.54, f"m{night}")
    assert st["champion"] == "lr" and st["log"][-1]["event"] == "neural net demoted"
    # a tiny lead (< MARGIN) never counts toward promotion
    assert ai.decide_champion(None, 0.53, 0.535, "x")["nnAhead"] == 0


def test_neural_model_saves_and_scores_like_the_live_model(tmp_path):
    rows = [ai.features(_t(i, i % 2 == 0, rule="zs2" if i % 2 == 0 else "rvol"), "bull") for i in range(300)]
    ys = [int(i % 2 == 0) for i in range(300)]
    enc = ai.Model().fit(rows, ys)
    net = ai.nn_fit(np.array([enc._vector(f) for f in rows], dtype=float), np.array(ys, dtype=float), epochs=15)
    m = ai.NeuralModel(enc, net)
    back = ai.NeuralModel.from_json(json.loads(json.dumps(m.to_json())))
    assert abs(back.predict(rows[0]) - m.predict(rows[0])) < 1e-4 and 0 <= m.predict(rows[1]) <= 1


def test_train_now_writes_the_challenger_and_the_champion(tmp_path):
    bull = tmp_path / "bull"
    bull.mkdir()
    for d in range(1, 8):
        (bull / f"2026-10-{d:02d}.json").write_text(json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    rep = ai.train_now(tmp_path / "out", roots={"bull": bull}, today="2026-10-20")
    assert (tmp_path / "out" / "nn.json").exists() and rep["champion"]["champion"] in ("lr", "nn")
    assert json.loads((tmp_path / "out" / "champion.json").read_text())["champion"] == rep["champion"]["champion"]


def test_market_picture_at_entry_from_spy_bars(tmp_path):
    from datetime import datetime
    et = ai.ET
    def ts(day, h, m):
        return int(datetime(2026, 10, day, h, m, tzinfo=et).timestamp())
    tape = ai.MarketTape(tmp_path)
    bars = [[ts(15, 15, 55), 99, 100, 99, 100.0, 1000]]                    # prior close 100
    bars += [[ts(16, 9, 30) + 300 * k, 100, 101, 99, 100 + k * 0.1, 1000] for k in range(12)]
    tape.bars = {"SPY": bars, "QQQ": bars}
    m = tape.at(datetime(2026, 10, 16, 10, 30, tzinfo=et).isoformat())
    assert round(m["spyPct"], 2) == 1.1                                     # 10:25 bar close 101.1 vs 100
    assert m["spy30"] > 0 and "spyVwap" in m
    f = ai.features({**_t(0, True), "_mkt": m, "market": {"breadth": 0.7}}, "bear")
    assert f["m_spyPct"] < 0 and abs(f["m_breadth"] - 0.3) < 1e-9          # read the bear way


def test_market_snapshot_from_the_board():
    from momx import option_track as ot
    rows = [{"symbol": "SPY", "pctChange": -0.5}, {"symbol": "QQQ", "pctChange": -0.8}, {"symbol": "A", "pctChange": 1.0}]
    snap = ot.market_snapshot(rows)
    assert snap["spyPct"] == -0.5 and snap["qqqPct"] == -0.8 and snap["breadth"] == round(1 / 3, 3)


def test_market_view_is_switched_by_the_data_not_by_hand():
    st = None
    for night in range(2):
        st = ai.decide_market(st, 0.53, 0.54, f"n{night}")
    assert st["useMarket"] is False and st["ahead"] == 2
    st = ai.decide_market(st, 0.53, 0.54, "n3")
    assert st["useMarket"] is True and st["log"][-1]["event"] == "market view switched ON"
    st = ai.decide_market(st, 0.55, 0.54, "n4")
    st = ai.decide_market(st, 0.55, 0.54, "n5")
    assert st["useMarket"] is False and st["log"][-1]["event"] == "market view switched OFF"
    assert ai.decide_market(None, 0.53, 0.533, "x")["ahead"] == 0          # under the margin: no credit


def test_model_remembers_its_feature_set():
    rows = [ai.features(_t(i, i % 2 == 0), "bull") for i in range(60)]
    m = ai.Model(ai.NUMERIC_MARKET).fit(rows, [i % 2 for i in range(60)])
    back = ai.Model.from_json(json.loads(json.dumps(m.to_json())))
    assert back.numeric == ai.NUMERIC_MARKET and len(back._vector(rows[0])) == len(m._vector(rows[0]))
    old = {k: v for k, v in m.to_json().items() if k != "numeric"}               # a model saved before today
    old["mean"] = {k: v for k, v in old["mean"].items() if not k.startswith("m_")}
    assert ai.Model.from_json(old).numeric == ai.NUMERIC


# ---------------------------------------------------------------- unattended running (2026-10-18)
def _full(i, win, day="2026-10-15", rule="zs2"):
    """A trade carrying every field the model reads, plus one it never reads."""
    return {**_t(i, win, rule=rule), "at": f"{day}T10:05:00-04:00", "entryAt": f"{day}T10:05:00-04:00",
            "stockPrice": 100.0, "putWalls": [{"strike": 97.0}], "jevFactors": {"regime": 0.4, "risk": -0.2},
            "setup": {"pct": 2.0, "rvol5m": 3.0, "grade": 3}, "market": {"breadth": 0.6, "spyPct": 0.3},
            "chainSnapshot": {"huge": list(range(50))}}


def test_vector_and_matrix_agree():
    rows = [ai.features(_full(i, i % 2 == 0, rule="zs2" if i % 3 else "rvol"), "bull") for i in range(80)]
    rows[3]["delta"] = None                                             # a missing value
    rows[4]["emPct"] = 1e9                                              # clipped at +5
    m = ai.Model(ai.NUMERIC_MARKET).fit(rows, [i % 2 for i in range(80)])
    assert np.allclose(m._matrix(rows), np.array([m._vector(f) for f in rows]))
    assert np.allclose(m.predict_many(rows), [m.predict(f) for f in rows])


def test_loaded_trades_keep_every_field_the_model_reads(tmp_path):
    bull = tmp_path / "bull"
    bull.mkdir()
    raw = [_full(i, i % 2 == 0) for i in range(5)]
    (bull / "2026-10-15.json").write_text(json.dumps({"trades": raw}))
    loaded = ai.load_days({"bull": bull})["2026-10-15"]
    assert "chainSnapshot" not in loaded[0][1]                           # dropped on load
    for (side, t), r in zip(loaded, raw):
        assert ai.features(t, side) == ai.features(r, "bull") and ai.label(t) == ai.label(r)


def test_compact_gives_the_same_rows_and_the_same_dataset(tmp_path):
    import gzip

    days = {"2026-10-15": [("bull", _full(i, i % 2 == 0)) for i in range(6)] + [("bull", {"entry": 1.0})]}
    rows, ys, _ = ai._xy(days["2026-10-15"])
    ai.save_dataset(days, tmp_path / "a")
    ai.compact(days)
    assert len(days["2026-10-15"]) == 6 and "callWalls" not in days["2026-10-15"][0][1]
    rows2, ys2, _ = ai._xy(days["2026-10-15"])
    assert ys2 == ys and rows2 == rows
    ai.save_dataset(days, tmp_path / "b")

    def read(d):
        with gzip.open(d / "dataset" / "2026-10-15.jsonl.gz", "rt", encoding="utf-8") as fh:
            return fh.read()

    assert read(tmp_path / "a") == read(tmp_path / "b")


def test_walk_forward_grades_only_the_newest_days_on_a_bounded_window(monkeypatch):
    monkeypatch.setattr(ai, "TRAIN_WINDOW_DAYS", 6)
    monkeypatch.setattr(ai, "WF_TEST_DAYS", 3)
    seen = []
    real = ai.fit_learning

    def spy(items, day_of, train_days, numeric=None):
        seen.append(list(train_days))
        return real(items, day_of, train_days, numeric)

    monkeypatch.setattr(ai, "fit_learning", spy)
    days = {f"2026-10-{d:02d}": [("bull", _t(i, i % 2 == 0)) for i in range(30)] for d in range(1, 21)}
    r = ai.walk_forward(days)
    names = sorted(days)
    assert [p["day"] for p in r["perDay"]] == names[-3:]
    assert seen == [names[k - 6:k] for k in range(17, 20)]               # 6 days each, never the test day


def test_train_now_loads_only_what_it_needs_and_never_calls_the_feed(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "TRAIN_WINDOW_DAYS", 4)
    monkeypatch.setattr(ai, "WF_TEST_DAYS", 2)
    monkeypatch.setattr(ai, "REC_DAYS", 2)
    calls = []
    monkeypatch.setattr(ai.MarketTape, "refresh", lambda self, *a, **k: calls.append(1))
    bull = tmp_path / "bull"
    bull.mkdir()
    for d in range(1, 13):
        (bull / f"2026-10-{d:02d}.json").write_text(json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    rep = ai.train_now(tmp_path / "out", roots={"bull": bull}, today="2026-10-20")
    assert rep["loadedDays"] == 8 and rep["trainDays"] == 4 and rep["testDays"] == 2 and calls == []
    ai.train_now(tmp_path / "out2", roots={"bull": bull}, today="2026-10-20", refresh_market=True)
    assert calls == [1]


def test_previous_close_is_the_prior_regular_session(tmp_path):
    from datetime import datetime
    et = ai.ET

    def bar(day, h, m, close):
        return [int(datetime(2026, 10, day, h, m, tzinfo=et).timestamp()), close, close, close, close, 1000]

    bars = [bar(16, 15, 55, 100.0), bar(16, 18, 0, 105.0),             # Friday close 100, after-hours 105
            bar(18, 21, 0, 106.0),                                     # Sunday-night overnight session
            bar(19, 4, 0, 107.0), bar(19, 9, 25, 108.0)]               # Monday pre-market
    bars += [bar(19, 9, 30 + 5 * k, 110.0) for k in range(6)]
    tape = ai.MarketTape(tmp_path)
    tape.bars = {"SPY": bars, "QQQ": bars}
    m = tape.at(datetime(2026, 10, 19, 10, 0, tzinfo=et).isoformat())
    assert round(m["spyPct"], 6) == 10.0                                 # vs Friday's 100, not 9:25's 108


def test_scorer_retrains_once_a_night_in_its_own_process(tmp_path, monkeypatch):
    from datetime import datetime
    et = ai.ET
    s = ai.Scorer({}, directory=tmp_path)
    runs = []

    def fake(upto):
        runs.append(upto)
        (tmp_path / "model.json").write_text(json.dumps(ai.Model().to_json()))

    monkeypatch.setattr(s, "_retrain", fake)
    s.tick(datetime(2026, 10, 19, 10, 0, tzinfo=et))                   # no model yet: train now
    s.tick(datetime(2026, 10, 19, 10, 1, tzinfo=et))
    assert runs == ["2026-10-19"]
    s.tick(datetime(2026, 10, 19, 16, 39, tzinfo=et))
    assert runs == ["2026-10-19"]                                        # not before 4:40 PM
    s.tick(datetime(2026, 10, 19, 16, 41, tzinfo=et))
    s.tick(datetime(2026, 10, 19, 17, 30, tzinfo=et))
    assert runs == ["2026-10-19", "2026-10-20"]                          # today's trades included, once
    (tmp_path / "report.json").write_text(json.dumps({"trainedAt": "2026-10-19T16:45:00-04:00"}))
    again = ai.Scorer({}, directory=tmp_path)                           # a worker restart that evening
    monkeypatch.setattr(again, "_retrain", fake)
    again.tick(datetime(2026, 10, 19, 18, 0, tzinfo=et))
    assert runs == ["2026-10-19", "2026-10-20"]
    again.tick(datetime(2026, 10, 24, 17, 0, tzinfo=et))                # Saturday: no retrain
    assert len(runs) == 2


def test_failed_first_training_is_retried_hourly_not_every_tick(tmp_path, monkeypatch):
    from datetime import datetime
    et = ai.ET
    s = ai.Scorer({}, directory=tmp_path)
    runs = []
    monkeypatch.setattr(s, "_retrain", lambda upto: runs.append(upto))  # never writes a model
    for minute in range(5):
        s.tick(datetime(2026, 10, 19, 10, minute, tzinfo=et))
    assert len(runs) == 1


def test_command_line_retrain_runs_as_its_own_program(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    grade = tmp_path / "grade"
    (grade / "momx_option_track").mkdir(parents=True)
    for d in range(1, 8):
        (grade / "momx_option_track" / f"2026-10-{d:02d}.json").write_text(
            json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    env = {**os.environ, "AGX_MOMX_GRADE_DIR": str(grade)}
    root = Path(ai.__file__).resolve().parent.parent
    res = subprocess.run([sys.executable, "-m", "momx.ai_filter", "--train", "--today", "2026-10-20",
                          "--dir", str(tmp_path / "out")], cwd=str(root), env=env, capture_output=True, text=True,
                         timeout=300)
    assert res.returncode == 0, res.stderr[-2000:]
    assert json.loads(res.stdout.strip().splitlines()[-1])["trainDays"] == 7
    assert (tmp_path / "out" / "model.json").exists() and (tmp_path / "out" / "report.json").exists()


def test_a_torn_dataset_file_never_stops_the_nightly_retrain(tmp_path):
    """An interrupted save used to leave half a .gz; reading it raised EOFError
    (not OSError) and every later retrain died on it."""
    import gzip

    bull = tmp_path / "bull"
    bull.mkdir()
    for d in range(1, 8):
        (bull / f"2026-10-{d:02d}.json").write_text(json.dumps({"trades": [_t(i, i % 3 == 0) for i in range(30)]}))
    out = tmp_path / "out"
    ai.train_now(out, roots={"bull": bull}, today="2026-10-20")
    assert not list((out / "dataset").glob("*.tmp")) and not list(out.glob(".*.tmp"))
    good = (out / "dataset" / "2026-10-02.jsonl.gz").read_bytes()
    (out / "dataset" / "2026-09-30.jsonl.gz").write_bytes(good[: len(good) // 2])     # torn
    with gzip.open(out / "dataset" / "2026-09-30.jsonl.gz", "rt") as fh:
        try:
            fh.read()
            torn = False
        except EOFError:
            torn = True
    assert torn
    days = ai.load_days({"bull": bull}, dataset=out / "dataset")
    assert "2026-09-30" not in days and "2026-10-02" in days
    rep = ai.train_now(out, roots={"bull": bull}, today="2026-10-20")
    assert "auc" in rep


def test_only_one_nightly_retrain_runs_at_a_time(tmp_path):
    """2026-11-02: four orphaned retrains fought at IDLE and none finished."""
    import subprocess
    import sys
    from momx.ai_filter import _single_retrain_lock
    held = _single_retrain_lock(tmp_path)
    assert held is not None
    probe = ("from pathlib import Path; from momx.ai_filter import _single_retrain_lock; "
             f"print(_single_retrain_lock(Path(r'{tmp_path}')) is None)")
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.strip() == "True"
    held.close()
    assert subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout.strip() == "False"
