import json
import threading
from types import SimpleNamespace

import pytest

from momx import ai_filter, paper_ai
from momx.paper_ai import Reviewer, decision


def reviewer(tmp_path, transport):
    config = tmp_path / "gateway.json"
    config.write_text(json.dumps({"gateway": {"port": 18789, "auth": {"token": "test-only"}}}))
    r = Reviewer(tmp_path, transport=transport, clock=lambda: 1000, gateway_config=config)
    r.directory.mkdir()
    (r.directory / "config.json").write_text('{"enabled":true,"mode":"paper"}')
    return r


def test_primary_and_limit_fallback(tmp_path):
    calls = []
    def transport(url, body, headers=None, timeout=0):
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("simulated provider limit")
        assert body["think"] is False
        return {"message": {"content": '{"action":"approve","reason":"consistent setup"}'}}
    r = reviewer(tmp_path, transport)
    result = r.infer({"probability": .7})
    assert result["approved"] and result["provider"] == "ollama" and result["fallback"]
    assert len(calls) == 2
    r.transport = lambda *a, **k: {"choices": [{"message": {"content": '{"action":"skip","reason":"weak setup"}'}}]}
    result = r.infer({"probability": .7})
    assert not result["approved"] and result["provider"] == "openclaw" and not result["fallback"]


def test_native_review_requests_supported_structured_output(tmp_path):
    from momx.paper_learning import analysis
    def transport(url, body, headers=None, timeout=0):
        assert body['response_format']['type']=='json_schema'
        assert body['response_format']['json_schema']['schema']==analysis.schema
        return {'choices':[{'message':{'content':json.dumps(dict(summary='s',hypothesis='h',validation='v'))}}]}
    result=reviewer(tmp_path,transport).request('synthetic check',analysis)
    assert result['provider']=='openclaw' and not result['fallback']


def test_local_primary_bypasses_gateway_and_preserves_role_config(tmp_path):
    calls = []
    def transport(url, body, headers=None, timeout=0):
        calls.append(url)
        assert url == 'http://127.0.0.1:11434/api/chat'
        assert headers is None and body['think'] is False
        return {'message': {'content': '{"action":"skip","reason":"insufficient evidence"}'}}
    r = reviewer(tmp_path, transport)
    (r.directory / 'config.json').write_text('{"enabled":true,"mode":"paper","provider":"ollama"}')
    result = r.for_agent('bot-critic').infer({})
    assert result['provider'] == 'ollama' and result['fallback'] is False
    assert 'primaryError' not in result and len(calls) == 1
    assert r.primary_provider() == 'ollama'


def test_level_review_can_try_openclaw_first_without_changing_other_local_reviews(tmp_path):
    calls=[]
    def transport(url, body, headers=None, timeout=0):
        calls.append(url)
        if len(calls)==1:raise TimeoutError('gateway unavailable')
        return {'message':{'content':'{"action":"skip","reason":"research only"}'}}
    r=reviewer(tmp_path,transport)
    (r.directory/'config.json').write_text('{"provider":"ollama"}')
    out=r.request('synthetic level review',decision,prefer_gateway=True)
    assert out['provider']=='ollama' and out['fallback'] is True
    assert calls==['http://127.0.0.1:18789/v1/chat/completions','http://127.0.0.1:11434/api/chat']
    assert r.primary_provider()=='ollama'


def test_local_primary_failure_is_reported_without_cloud_request(tmp_path):
    calls = []
    def transport(url, *args, **kwargs):
        calls.append(url)
        raise TimeoutError('private diagnostics')
    r = reviewer(tmp_path, transport)
    (r.directory / 'config.json').write_text('{"provider":"ollama"}')
    result = r.infer({})
    assert len(calls) == 1 and result['provider'] == 'unavailable'
    assert result['errors'] == ['TimeoutError']
    assert 'private diagnostics' not in json.dumps(result)


@pytest.mark.parametrize("text", ['{"action":"buy"}', '{"action":"approve"}', 'approve', '[]'])
def test_invalid_decisions_never_approve(text):
    with pytest.raises((ValueError, TypeError)):
        decision(text)


def test_both_providers_fail_closed_without_leaking_errors(tmp_path):
    def failed(*a, **k):
        raise RuntimeError("secret material")
    result = reviewer(tmp_path, failed).infer({"probability": .6})
    assert not result["approved"] and result["provider"] == "unavailable"
    assert "secret material" not in json.dumps(result)
    assert "entry skipped" not in result["reason"]


def test_advisory_fallback_uses_required_analysis_schema(tmp_path):
    from momx.paper_learning import analysis
    def transport(url, body, headers=None, timeout=0):
        if "18789" in url: raise TimeoutError()
        assert body["format"]["required"] == ["summary", "hypothesis", "validation"]
        assert body["think"] is False and timeout == paper_ai.AGENT_TIMEOUT_SECONDS
        assert body["options"]["num_thread"] == paper_ai.OLLAMA_THREADS and body["keep_alive"] == "30m"
        return {"message":{"content":'{"summary":"mixed outcomes","hypothesis":"test spreads","validation":"future paired fills"}'}}
    result = reviewer(tmp_path,transport).request("Research only",analysis)
    assert result["provider"] == "ollama" and result["validation"]


def test_native_agent_routing_preserves_provider_fallback(tmp_path):
    from momx.paper_learning import analysis
    routed = []
    def transport(url, body, headers=None, timeout=0):
        routed.append(body["model"])
        return {"choices": [{"message": {"content": '{"summary":"ok","hypothesis":"test","validation":"future data"}'}}]}
    base = reviewer(tmp_path, transport)
    for role in ("scanner", "reviewer", "critic", "planner"):
        result = base.for_agent("bot-"+role).request("research", analysis)
        assert result["agent"] == "bot-"+role and result["provider"] == "openclaw"
    assert routed == ["openclaw/bot-"+role for role in ("scanner", "reviewer", "critic", "planner")]


def test_specialists_are_bounded_toolless_and_do_not_overwrite_existing_agents(tmp_path):
    from momx.paper_ai import ensure_specialist
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({"agents": {"entries": {"main": {"name": "Existing"}}}, "unrelated": 123}))
    ensure_specialist(path, "bot-specialist-execution")
    first = path.read_text()
    ensure_specialist(path, "bot-specialist-execution")
    ensure_specialist(path, "bot-specialist-any-arbitrary-agent")
    assert path.read_text() == first
    doc = json.loads(first)
    assert doc["unrelated"] == 123 and doc["agents"]["entries"]["main"]["name"] == "Existing"
    assert doc["agents"]["entries"]["bot-specialist-execution"]["tools"] == {"deny": ["*"]}


def test_review_is_async_deduplicated_excludes_outcomes_and_expires(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def transport(url, body, *a, **k):
        calls.append(body)
        entered.set()
        assert release.wait(3)
        return {"choices": [{"message": {"content": '{"action":"approve","reason":"consistent"}'}}]}
    r = reviewer(tmp_path, transport)
    t = {"symbol": "SPY", "rule": "go", "at": "2026-10-30T10:00:00-04:00", "entry": 1, "best": 777, "exitPrice": 888}
    key = ai_filter.trade_key("bull", t)
    monkeypatch.setattr(ai_filter, "_SCORER", SimpleNamespace(scores={key: .7}))
    assert not r.review("bull", t, 1000)["approved"]
    assert entered.wait(2)
    assert not r.review("bull", t, 1001)["approved"]
    release.set()
    r.jobs.join()
    assert len(calls) == 1 and "777" not in json.dumps(calls) and "888" not in json.dumps(calls)
    assert r.review("bull", t, 1002)["approved"]
    assert not r.review("bull", {**t, "contract": "different-contract"}, 1003)["approved"]
    assert not r.review("bull", t, 1301)["approved"]
    assert list(r.directory.glob("*.jsonl"))


def test_low_missing_and_nonfinite_score_cannot_queue(tmp_path, monkeypatch):
    r = reviewer(tmp_path, lambda *a, **k: pytest.fail("must not request model"))
    t = {"symbol": "SPY", "rule": "go", "at": "2026-10-30T10:00:00-04:00"}
    for p in (None, .54, float("nan"), float("inf"), True, 1.1):
        monkeypatch.setattr(ai_filter, "_SCORER", SimpleNamespace(scores={ai_filter.trade_key("bull", t): p}))
        assert not r.review("bull", t, 1000)["approved"]
    assert r.worker is None


def test_closed_missing_exit_is_not_a_training_outcome():
    assert ai_filter.label({"entry": 1, "status": "closed", "last": 2}) is None
    assert ai_filter.label({"entry": 1, "status": "closed", "exitPrice": 0}) == 0


def test_disabled_or_missing_configuration_pauses_new_entries(tmp_path):
    r = Reviewer(tmp_path)
    assert r.review("bull", {}, 1000)["approved"] is False
    r.directory.mkdir()
    (r.directory / "config.json").write_text('{"enabled":true,"mode":"live"}')
    assert r.review("bull", {}, 1000)["approved"] is False


def test_scorer_can_score_pending_candidate_without_fabricating_entry(tmp_path, monkeypatch):
    trade = {"symbol": "SPY", "rule": "news", "at": "2026-10-30T10:00:00-04:00", "status": "pending", "aiCandidate": {"entry": 1.2}}
    scorer = ai_filter.Scorer({"bull": SimpleNamespace(trades=lambda: [trade])}, tmp_path)
    scorer.model = SimpleNamespace(predict=lambda _: .7)
    monkeypatch.setattr(scorer, "_load_model", lambda: None)
    monkeypatch.setattr(scorer, "_retrain", lambda _: None)
    monkeypatch.setattr(scorer, "_record_for", lambda *args: {})
    scorer.tick(ai_filter.datetime(2026, 10, 30, 10, 0, tzinfo=ai_filter.ET))
    assert scorer.scores[ai_filter.trade_key("bull", trade)] == .7
    assert "entry" not in trade


def test_gateway_has_bounded_local_model_window_before_fallback(tmp_path):
    calls=[]
    def transport(url, body, headers=None, timeout=0):
        calls.append((url,timeout))
        if '18789' in url: raise TimeoutError()
        return {'message':{'content':'{"action":"skip","reason":"local fallback"}'}}
    config=tmp_path/'openclaw.json'
    config.write_text('{"gateway":{"port":18789,"auth":{"token":"test"}}}')
    from momx.paper_ai import Reviewer,decision
    r=Reviewer(tmp_path,transport=transport,gateway_config=config).request('test',decision)
    assert calls[0][1]==180 and r['provider']=='ollama' and r['fallback']


def test_model_quality_specialist_inherits_bot_local_model_and_has_no_tools(tmp_path):
    from momx.paper_ai import ensure_specialist
    path=tmp_path/'openclaw.json'
    model={'primary':'ollama/qwen3.5:4b','fallbacks':[]}
    path.write_text(json.dumps({'agents':{'entries':{'bot-reviewer':{'model':model,'thinkingDefault':'off'}}}}))
    ensure_specialist(path,'bot-specialist-model_quality')
    entry=json.loads(path.read_text())['agents']['entries']['bot-specialist-model_quality']
    assert entry['model']==model and entry['tools']=={'deny':['*']} and entry['skills']==[]
    assert entry['thinkingDefault']=='off'


def test_dedicated_bot_profile_is_used_by_every_role_and_explicit_override_wins(tmp_path):
    (tmp_path/'paper').mkdir()
    profile=tmp_path/'dedicated-openclaw.json'
    (tmp_path/'paper'/'config.json').write_text(json.dumps({'openclawConfigPath':str(profile)}))
    r=Reviewer(tmp_path)
    assert r.gateway_config==profile
    assert r.for_agent('bot-critic').gateway_config==profile
    override=tmp_path/'override.json'
    assert Reviewer(tmp_path,gateway_config=override).gateway_config==override


def test_native_timeout_respects_configured_cpu_agent_budget_and_remains_bounded(tmp_path):
    from momx.paper_ai import Reviewer, decision
    calls=[]
    def transport(url, body, headers=None, timeout=0):
        calls.append(timeout)
        return {'choices':[{'message':{'content':'{"action":"skip","reason":"synthetic"}'}}]}
    profile=tmp_path/'openclaw.json'
    for budget,expected in ((600,630),(99999,630),(1,180)):
        profile.write_text(json.dumps({'gateway':{'port':18790,'auth':{'token':'test'}},
                                       'agents':{'defaults':{'timeoutSeconds':budget}}}))
        result=Reviewer(tmp_path,transport=transport,gateway_config=profile).request('test',decision)
        assert result['provider']=='openclaw' and not result['fallback']
        assert calls[-1]==expected


def test_native_complete_json_fence_does_not_cause_false_fallback(tmp_path):
    from momx.paper_learning import analysis
    calls=[]
    def transport(url,*args,**kwargs):
        calls.append(url)
        return {'choices':[{'message':{'content':'```json\n{"summary":"s","hypothesis":"h","validation":"v"}\n```'}}]}
    out=reviewer(tmp_path,transport).request('research',analysis)
    assert out['provider']=='openclaw' and out['fallback'] is False
    assert len(calls)==1 and out['summary']=='s'


@pytest.mark.parametrize('content', ['Prose\n```json\n{"action":"approve","reason":"x"}\n```', '```json\n{"action":"approve"}\n```', '```json\n{}\n```\n```json\n{}\n```'])
def test_json_fence_does_not_bypass_validation_or_accept_prose(content):
    from momx.paper_ai import _structured_text
    with pytest.raises((ValueError,TypeError)):
        decision(_structured_text(content))


def test_default_gateway_is_the_bot_profile_the_start_script_launches(tmp_path):
    from momx.paper_ai import default_gateway_config
    assert default_gateway_config(tmp_path) == tmp_path / '.openclaw' / 'openclaw.json'
    bot = tmp_path / '.openclaw-agx-bot' / 'openclaw.json'
    bot.parent.mkdir()
    bot.write_text('{}')
    assert default_gateway_config(tmp_path) == bot
