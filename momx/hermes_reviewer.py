"""Optional independent Hermes research, isolated from entry and exit execution."""
import json
import os
from pathlib import Path
import subprocess
import time

from momx import ai_filter

#: Measured 2026-11-02 on the laptop: a good Hermes run took 134 s, 128 s of it
#: Hermes importing its own framework (the answer itself ~6 s); a cold model
#: load adds ~50 s. 180 s reported nearly every run "unavailable". Research
#: only - never on the entry/exit path - so a longer wait costs nothing.
HERMES_TIMEOUT_SECONDS = 420


def run_bounded(*args, **kwargs):
    """Kill the Hermes re-exec descendants too; inherited pipes cannot extend the deadline."""
    timeout = kwargs.pop('timeout')
    check = kwargs.pop('check', False)
    value = kwargs.pop('input', None)
    kwargs.pop('capture_output', None)
    with subprocess.Popen(*args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as child:
        try:
            out, err = child.communicate(value, timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                        capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
            finally:
                if child.poll() is None:
                    child.kill()
            # Do not wait forever for a descendant retaining stdout/stderr.
            try:
                child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
            raise subprocess.TimeoutExpired('Hermes research', timeout) from None
        if check and child.returncode:
            raise subprocess.CalledProcessError(child.returncode, 'Hermes research')
        return subprocess.CompletedProcess(args[0], child.returncode, out, err)


class HermesReviewer:
    def __init__(self, config_path=None, runner=run_bounded):
        self.config_path = Path(config_path or ai_filter._dir() / "paper" / "hermes.json")
        self.runner = runner

    def enabled(self):
        try:
            return json.loads(self.config_path.read_text()).get("enabled") is True
        except (OSError, ValueError, AttributeError):
            return False

    def status(self):
        try:
            return json.loads(self.config_path.with_name('hermes-status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {"available": None, "reason": "Not checked in this runtime"}

    def _record(self, result):
        try:
            ai_filter._write_atomic(self.config_path.with_name('hermes-status.json'), json.dumps({
                k:result[k] for k in ('provider','available','reason') if k in result} | {'checkedAt':time.time()}))
        except OSError:
            pass
        return result

    def request(self, prompt, parse):
        try:
            config = json.loads(self.config_path.read_text())
            if config.get("enabled") is not True:
                return {"provider": "hermes", "available": False, "reason": "Hermes research disabled"}
            bridge = Path(__file__).resolve().parents[1] / "scripts" / "hermes_bot_review.py"
            env = {**os.environ, **config.get("environment", {}), "HERMES_HOME": config["home"],
                   "HERMES_API_TIMEOUT": "150", "PYTHONIOENCODING": "utf-8"}
            result = self.runner([config["python"], str(bridge)], cwd=config["root"], env=env,
                                 input=json.dumps({"prompt": prompt}), capture_output=True, text=True,
                                 encoding="utf-8", timeout=HERMES_TIMEOUT_SECONDS, check=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            envelope = _envelope(result.stdout)
            from momx.paper_ai import _structured_text
            return self._record({**parse(_structured_text(envelope["response"])), "provider": "hermes", "model": "ollama/qwen3.5:4b", "available": True})
        except Exception as exc:
            return self._record({"provider": "hermes", "available": False, "reason": type(exc).__name__ + ": independent review unavailable"})


def _envelope(stdout):
    """The bridge's JSON envelope: its LAST stdout line. Hermes start-up or
    shutdown can print other lines, which made json.loads fail on a good reply."""
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            value = json.loads(line)
            if isinstance(value, dict) and "response" in value:
                return value
    raise ValueError("Hermes bridge returned no JSON envelope")
