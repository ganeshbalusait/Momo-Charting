import json
import random

import numpy as np

from momx import unsupervised
from momx.paper_learning import LearningTeam, research_task, scoreboard, batch_win_rate, ROTATION, specialist_analysis


def _days(n_days=30, per_day=8, seed=3):
    """Strong-market days win, weak-market days lose; a few odd entries always lose."""
    rng = random.Random(seed)
    done = {}
    for d in range(n_days):
        day = f"2026-09-{d + 1:02d}"
        strong = d % 2 == 0
        items = []
        for i in range(per_day):
            odd = i == 0
            t = {"m_spyPct": (1.0 if strong else -1.0) + rng.gauss(0, .1), "m_breadth": (.7 if strong else .3) + rng.gauss(0, .02),
                 "spread": 5.0 if odd else rng.gauss(.05, .01), "delta": rng.gauss(.5, .05), "dte": rng.gauss(7, .5),
                 "win": 0 if odd else int(strong if rng.random() < .85 else not strong)}
            items.append(("bull", t))
        done[day] = items
    return done


feat = lambda t, side: t
lab = lambda t: t["win"]


def test_regimes_are_learned_without_labels_and_hold_on_later_days():
    report = unsupervised.train_report(_days(), feat, lab, ("spread", "delta", "dte"), ("m_spyPct", "m_breadth"))
    reg = report["regimes"]
    assert reg["state"] == "ready" and reg["persistence"]["held"]
    assert reg["persistence"]["testSpread"] >= 50
    assert reg["testFrom"] > "2026-09-15"          # graded on later days only


def test_autoencoder_flags_rare_entries_on_held_out_days():
    ano = unsupervised.train_report(_days(), feat, lab, ("spread", "delta", "dte"), ())["anomalies"]
    assert ano["state"] == "ready" and ano["unusualTrades"] >= 5
    assert ano["unusualWinRate"] < ano["normalWinRate"]
    assert ano["drivers"][0] == "spread"


def test_too_little_history_reports_collecting_not_a_result():
    report = unsupervised.train_report({"2026-09-01": [("bull", {"m_spyPct": 1, "win": 1})]}, feat, lab, ("delta",), ("m_spyPct",))
    assert report["regimes"]["state"] == "collecting" and report["anomalies"]["state"] == "collecting"


def test_report_is_json_safe_and_brief_is_small():
    report = unsupervised.train_report(_days(), feat, lab, ("spread", "delta", "dte"), ("m_spyPct", "m_breadth"))
    json.dumps(report, allow_nan=False)
    assert "centroids" not in json.dumps(unsupervised.brief(report))


def test_kmeans_separates_two_obvious_groups():
    data = np.vstack([np.zeros((20, 2)), np.ones((20, 2)) * 10])
    labels = unsupervised.assign(data, unsupervised.kmeans(data, 2))
    assert len(set(labels[:20])) == 1 and len(set(labels[20:])) == 1 and labels[0] != labels[-1]


def test_research_routes_to_regime_or_anomaly_only_on_a_real_gap():
    assert research_task({"regimes": {"persistence": {"held": True, "testSpread": 25, "bestRegime": 0, "worstRegime": 1}}})["role"] == "regime"
    assert research_task({"regimes": {"persistence": {"held": True, "testSpread": 3}}}) is None
    assert research_task({"anomalies": {"state": "ready", "unusualTrades": 20, "unusualWinRate": 20.0, "normalWinRate": 55.0}})["role"] == "anomaly"
    assert research_task({"anomalies": {"state": "ready", "unusualTrades": 5, "unusualWinRate": 20.0, "normalWinRate": 55.0}}) is None


def test_specialist_calls_are_graded_by_the_next_batch_only():
    win, loss = {"pnlPerContract": 10}, {"pnlPerContract": -10}
    jobs = [
        {"state": "complete", "completedAt": 1, "specialistTask": {"role": "risk"}, "specialist": {"forecast": "higher"}, "evidence": [loss, loss]},
        {"state": "complete", "completedAt": 2, "specialistTask": {"role": "risk"}, "specialist": {"forecast": "lower"}, "evidence": [win, win]},
        {"state": "complete", "completedAt": 3, "specialistTask": {"role": "exit"}, "specialist": {"forecast": "higher"}, "evidence": [win, loss]},
    ]
    board = scoreboard(jobs)
    assert board["risk"] == {"calls": 2, "graded": 2, "correct": 2, "unclear": 0, "pending": 0, "hitRate": 100.0}
    assert board["exit"]["pending"] == 1 and board["exit"]["hitRate"] is None
    assert batch_win_rate([win, loss, {"realizedPnl": 5}]) == 66.7


def test_missing_forecast_is_unclear_not_a_failed_review():
    text = json.dumps({"summary": "s", "hypothesis": "h", "validation": "v"})
    assert specialist_analysis(text)["forecast"] == "unclear"
    assert specialist_analysis(json.dumps({**json.loads(text), "forecast": "higher"}))["forecast"] == "higher"


def test_rotation_gives_lifecycle_specialists_turns_and_prompts_carry_research(tmp_path):
    from test_momx_paper_learning import Agents, Book, closed
    scanner_inputs = {"highestLevel": 1}
    (tmp_path / "unsupervised.json").write_text(json.dumps({"regimes": {"state": "ready", "testWinRate": 50.0}, "anomalies": {"state": "collecting"}}))
    agents = Agents()
    team = LearningTeam({"bull": Book([closed()])}, tmp_path / "paper" / "learning", agents)
    team.research = lambda: {"regimes": {"state": "ready"}}
    team.discover()
    job_name = next(iter(team.jobs))
    team.jobs[job_name]["evidence"][0]["scannerInputsAtSignal"] = scanner_inputs
    assert team.run_once()
    job = team.jobs[job_name]
    assert job["specialistTask"]["role"] == ROTATION[0]
    assert job["specialist"]["forecast"] == "unclear"
    assert "Unsupervised research" in agents.prompts[0]
    assert "scoreboard" in team.status()
