import json
from momx.learning_memory import summarize, context
from momx.paper_learning import LearningTeam
from test_momx_paper_learning import Agents


def rows(scope="virtual", n=8):
    return [{"key": f"bull|go|X|{i}" if scope == "virtual" else f"{scope}|{i}",
             "side": "bull", "rule": "go", "entryAt": f"2026-10-{i//2+20:02d}T10:00:00-04:00",
             "basis": "virtual strategy exit; midpoint estimate before fees", "entryMid": 1, "exitMid": .8,
             "entryFill": 1, "sells": [{"qty": 2, "price": .8}]} for i in range(n)]


def test_recurring_losses_need_separate_days_and_winning_counterexamples():
    data = rows()
    r = summarize(data)
    p = r["patterns"][0]
    assert p["trades"] == 8 and p["losses"] == 8 and p["net"] == -160
    data[0]["exitMid"] = 10
    assert not summarize(data)["patterns"]  # one winner outweighs losses in earlier period
    data = rows()
    for row in data:
        row["entryAt"] = data[0]["entryAt"]
    assert not summarize(data)["patterns"]  # many correlated signals on one day aren't recurrence


def test_views_and_carry_duplicates_do_not_double_count_or_mix_bases():
    data = rows() + rows("paper3") + rows("paper5")
    r = summarize(data + data)
    assert r["capturedTrades"] == 24
    assert len(r["patterns"]) == 3
    assert {p["scope"]: p["net"] for p in r["patterns"]} == {"virtual": -160.0, "paper3": -320.0, "paper5": -320.0}
    assert summarize(list(reversed(data)))["fingerprint"] == r["fingerprint"]


def test_invalid_missing_and_zero_expiry_prices():
    data = rows(n=10)
    data[0]["exitMid"] = None
    data[1]["exitMid"] = float("nan")
    data[2]["exitMid"] = 0  # confirmed zero is a loss, not missing
    r = summarize(data)
    assert r["excludedRows"] == 2 and r["capturedTrades"] == 8
    assert r["patterns"][0]["losses"] == 8
    paper = rows("paper3")
    paper[0]["filledQty"] = 3
    assert summarize(paper)["excludedRows"] == 1


def test_memory_persists_and_both_agents_receive_same_snapshot(tmp_path):
    agents = Agents()
    team = LearningTeam({}, tmp_path, agents, clock=lambda: 1000)
    team.save("batch-test.json", {"state": "pending", "attempts": 0, "createdAt": 1, "evidence": rows()})
    team.refresh_memory()
    before = json.loads((tmp_path / "memory.json").read_text())
    assert team.run_once()
    snapshot = context(before)
    assert all(json.dumps(snapshot) in p for p in agents.prompts)
    assert team.jobs["batch-test.json"]["memory"] == snapshot
    restarted = LearningTeam({}, tmp_path, agents)
    assert restarted.memory == before and not restarted.run_once()


def test_stale_history_rolls_out_of_research_window():
    data = rows()
    for day in range(1, 22):
        data.append({**data[0], "key": f"bull|other|{day}", "rule": "other",
                     "entryAt": f"2026-11-{day:02d}T10:00:00-05:00", "exitMid": 2})
    report = summarize(data)
    assert not report["patterns"] and report["scopes"]["virtual"]["days"] == 20
