import json
from types import SimpleNamespace

import pytest

from momx.paper_learning import LearningTeam, analysis, evidence, trade_first
from test_momx_paper_mirror import FakeClient, Book, NOW, _trade
from momx.paper_mirror import PaperMirror


def closed(**over):
    return {"symbol": "SPY", "side": "bull", "rule": "go", "at": "2026-10-30T10:00:00-04:00",
            "status": "closed", "entry": 1.1, "exitPrice": .8, **over}


class Agents:
    def __init__(self):
        self.prompts = []
        self.on = True
    def enabled(self):
        return self.on
    def request(self, prompt, parse):
        self.prompts.append(prompt)
        return {**parse(json.dumps({"summary": "Loss observed, insufficient evidence", "hypothesis": "Compare spread groups", "validation": "Use unseen chronological days and fees"})), "provider": "ollama"}


def test_level_agents_honor_actual_local_primary_without_gateway_wait(tmp_path):
    from momx.paper_ai import Reviewer
    calls = []
    def transport(url, body, headers=None, timeout=0):
        calls.append(url)
        assert url == 'http://127.0.0.1:11434/api/chat'
        return {'message': {'content': json.dumps({
            'summary': 'Recorded outcome only', 'hypothesis': 'Test a frozen proposal',
            'validation': 'Require later chronological outcomes and costs'})}}
    reviewer = Reviewer(tmp_path, transport=transport)
    reviewer.directory.mkdir()
    (reviewer.directory / 'config.json').write_text(
        '{"enabled":true,"mode":"paper","provider":"ollama"}')
    row = closed(rule='level1', levelSignal={'highestLevel': 1, 'setupCount': 3})
    team = LearningTeam({'bull': Book([row])}, tmp_path / 'learning', reviewer)
    team.discover()
    assert team.run_once()
    assert len(calls) >= 3
    assert team.status()['reviewedTrades'] == 1
    assert not team.status()['entryBlocking']


def test_trade_first_never_requires_score_ai_availability_or_approval(tmp_path):
    client = FakeClient()
    m = PaperMirror({"bull": Book([_trade(jevP=None)])}, tmp_path, client_factory=lambda: client,
                    off=lambda: False, account="paper5", jev_cut=None, ai_policy=trade_first)
    m.tick(NOW)
    assert len(client.calls) == 1 and client.calls[0][3] == 1.2
    assert m.status()["rows"][0]["aiDecision"]["mode"] == "post_trade"


def test_shared_virtual_production_policy_is_nonblocking():
    from momx import service
    assert service._OPTION_TRACK._ai_policy is trade_first
    assert service._BEAR_OPTION_TRACK._ai_policy is trade_first
    assert trade_first("bull", {"aiP": .1}, 0)["approved"]


def test_missing_exits_and_open_positions_are_not_retrospective_outcomes():
    assert evidence("bull", closed(exitPrice=None)) is None
    assert evidence("bull", closed(status="open")) is None
    assert evidence("bull", closed(badQuote=True)) is None
    assert evidence("bull", closed(exitPrice=float("nan"))) is None
    assert evidence("bull", closed(exitPrice=0))["pnlPerContract"] == -110


def test_agents_exchange_evidence_and_persist_without_changing_trades_or_models(tmp_path):
    row = closed(secret="never transmit", best=999)
    agents = Agents()
    team = LearningTeam({"bull": Book([row])}, tmp_path, agents, clock=lambda: 1000)
    team.discover()
    team.discover()
    assert team.status()["pendingTrades"] == 1
    assert list(team.status()["tradeStates"].values()) == ["pending"]
    assert not agents.prompts  # discovery cannot call providers
    assert team.run_once()
    assert len(agents.prompts) == 4 and "reviewAgent" in agents.prompts[-1]
    assert team.status()['latest']['specialistTask']['role'] == 'model_quality'
    assert "scannerAgent" in agents.prompts[1] and "scannerAgent" in agents.prompts[2]
    assert "never transmit" not in "".join(agents.prompts) and "999" not in "".join(agents.prompts)
    assert team.status()["reviewedTrades"] == 1 and not team.status()["entryBlocking"]
    assert list(team.status()["tradeStates"].values()) == ["complete"]
    assert row["exitPrice"] == .8 and not (tmp_path / "model.json").exists()
    restarted = LearningTeam({"bull": Book([row])}, tmp_path, agents)
    restarted.discover()
    assert not restarted.run_once() and len(agents.prompts) == 4


def test_failed_critic_retries_without_repeating_reviewer_then_reports_failure(tmp_path):
    agents = Agents()
    original = agents.request
    def request(prompt, parse):
        return {"provider": "unavailable"} if "independent critic" in prompt else original(prompt, parse)
    agents.request = request
    now = [1000]
    team = LearningTeam({"bull": Book([closed()])}, tmp_path, agents, clock=lambda: now[0])
    team.discover()
    team.run_once()
    assert not team.run_once()  # retry is bounded, not a hot loop
    for _ in range(2):
        now[0] += 61
        team.run_once()
    assert len(agents.prompts) == 3
    assert team.status()["failedTrades"] == 1 and team.status()["reviewedTrades"] == 0


def test_real_paper_fills_are_distinct_and_carried_duplicates_are_ignored(tmp_path):
    row = {"status": "closed", "buyId": "id1", "buyFill": 1.1, "pl": -30, "sells": [{"qty": 1, "price": .8, "why": "strategy"}]}
    mirror = SimpleNamespace(account="paper3", history=lambda _: [{"rows": [row]}, {"rows": [row]}])
    team = LearningTeam({}, tmp_path, Agents(), mirrors=[mirror])
    team.discover()
    assert team.status()["pendingTrades"] == 1
    job = next(iter(team.jobs.values()))
    assert job["evidence"][0]["realizedPnl"] == -30


def test_specialist_is_created_for_a_recorded_need_and_findings_reach_critic(tmp_path):
    agents = Agents()
    book = Book([closed()])
    book.capture_status = lambda: {"day": "2026-10-30", "notEntered": 2}
    team = LearningTeam({"bull": book}, tmp_path, agents)
    team.discover()
    assert team.run_once()
    job = next(iter(team.jobs.values()))
    assert job["specialistTask"]["role"] == "execution"
    assert job["specialist"]["summary"]
    assert len(agents.prompts) == 4 and "specialistAgent" in agents.prompts[-1]


def test_hermes_failure_is_visible_and_does_not_block_openclaw_discussion(tmp_path):
    hermes = SimpleNamespace(enabled=lambda: True, request=lambda *args: {"provider": "hermes", "available": False, "reason": "TimeoutError"})
    team = LearningTeam({"bull": Book([closed()])}, tmp_path, Agents(), hermes=hermes)
    team.discover()
    assert team.run_once()
    assert team.status()["reviewedTrades"] == 1
    assert team.status()["latest"]["hermes"]["reason"] == "TimeoutError"


@pytest.mark.parametrize("text", ['[]', '{}', '{"summary":"ok","hypothesis":"x"}'])
def test_invalid_agent_messages_cannot_be_completed_research(text):
    with pytest.raises(ValueError):
        analysis(text)


def test_isolated_learning_directory_does_not_enable_production_hermes(tmp_path,monkeypatch):
    import json
    from momx import ai_filter
    from momx.paper_learning import LearningTeam
    production=tmp_path/'production'
    (production/'paper').mkdir(parents=True)
    (production/'paper/hermes.json').write_text(json.dumps({'enabled':True}))
    monkeypatch.setattr(ai_filter,'_dir',lambda:production)
    isolated=tmp_path/'isolated/learning'
    team=LearningTeam({},directory=isolated)
    assert team.hermes.config_path==isolated.parent/'hermes.json'
    assert team.hermes.enabled() is False
    assert json.loads((production/'paper/hermes.json').read_text())['enabled'] is True
