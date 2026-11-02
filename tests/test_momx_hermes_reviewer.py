import json
from types import SimpleNamespace
import subprocess
from momx.hermes_reviewer import HermesReviewer
from momx.paper_learning import analysis


def test_hermes_is_explicitly_enabled_and_uses_bounded_stdin_transport(tmp_path):
    config = tmp_path / "hermes.json"
    r = HermesReviewer(config)
    assert not r.enabled()
    config.write_text(json.dumps({"enabled": True, "python": "python.exe", "root": str(tmp_path), "home": str(tmp_path)}))
    def run(args, **kwargs):
        assert isinstance(args, list) and "shell" not in kwargs
        assert kwargs["timeout"] == 180 and json.loads(kwargs["input"])["prompt"] == "research"
        return SimpleNamespace(stdout=json.dumps({"response": '{"summary":"ok","hypothesis":"test","validation":"unseen data"}'}))
    r.runner = run
    assert r.enabled() and r.request("research", analysis)["available"]


def test_hermes_timeout_does_not_leak_process_output(tmp_path):
    config = tmp_path / "hermes.json"
    config.write_text(json.dumps({"enabled": True, "python": "python.exe", "root": str(tmp_path), "home": str(tmp_path)}))
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret command", 180, output="private data")
    result = HermesReviewer(config, run).request("research", analysis)
    assert not result["available"] and "private" not in json.dumps(result) and "secret" not in json.dumps(result)


def test_hermes_bridge_preserves_request_when_framework_restarts_python(tmp_path):
    import os
    import subprocess
    import sys
    import json
    from pathlib import Path
    fake=tmp_path/'run_agent.py'
    fake.write_text("""import os,subprocess,sys,json
if not os.environ.get('AGX_TEST_HERMES_RESTARTED'):
    env={**os.environ,'AGX_TEST_HERMES_RESTARTED':'1'}
    result=subprocess.run([sys.executable,sys.argv[0]],env=env,stdin=sys.stdin)
    raise SystemExit(result.returncode)
class AIAgent:
    def __init__(self,**kwargs): self.tools=[]
    def chat(self,prompt): return json.dumps({'summary':prompt,'hypothesis':'test','validation':'test'})
""",encoding='utf-8')
    bridge=Path(__file__).resolve().parents[1]/'scripts/hermes_bot_review.py'
    result=subprocess.run([sys.executable,str(bridge)],input=json.dumps({'prompt':'request survives restart'}),
        env={**os.environ,'PYTHONPATH':str(tmp_path)},capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    response=json.loads(json.loads(result.stdout)['response'])
    assert response['summary']=='request survives restart'


def test_native_timeout_runner_terminates_process_and_keeps_output_private():
    import sys,time
    from momx.hermes_reviewer import run_bounded
    start=time.monotonic()
    try:
        run_bounded([sys.executable,'-c','import time; print("private",flush=True); time.sleep(30)'],
            input='',capture_output=True,text=True,encoding='utf-8',timeout=.1,check=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired as exc:
        assert exc.output is None and 'private' not in str(exc)
    else:
        raise AssertionError('A stalled Hermes process must time out')
    assert time.monotonic()-start < 15


def test_hermes_accepts_complete_json_fence_and_records_actual_availability(tmp_path):
    config=tmp_path/'hermes.json'
    config.write_text(json.dumps({'enabled':True,'python':'python','root':str(tmp_path),'home':str(tmp_path)}))
    def run(*args,**kwargs):
        return SimpleNamespace(stdout=json.dumps({'response':'```json\n{"summary":"native","hypothesis":"test","validation":"future trades"}\n```'}))
    reviewer=HermesReviewer(config,run)
    assert reviewer.request('test',analysis)['available'] is True
    assert reviewer.status()['available'] is True


def test_hermes_accepts_log_lines_and_a_json_fence_but_still_validates(tmp_path):
    config = tmp_path / "hermes.json"
    config.write_text(json.dumps({"enabled": True, "python": "python.exe", "root": str(tmp_path), "home": str(tmp_path)}))
    good = '```json\n{"summary":"ok","hypothesis":"h","validation":"v"}\n```'
    out = "Hermes starting...\n" + json.dumps({"response": good}) + "\n"
    result = HermesReviewer(config, lambda *a, **k: SimpleNamespace(stdout=out)).request("research", analysis)
    assert result["available"] and result["summary"] == "ok"
    prose = "Here you go:\n" + good
    result = HermesReviewer(config, lambda *a, **k: SimpleNamespace(stdout=json.dumps({"response": prose}))).request("research", analysis)
    assert not result["available"]
