"""Optional independent Hermes research, isolated from entry and exit execution."""
import json
import os
from pathlib import Path
import subprocess

from momx import ai_filter


class HermesReviewer:
    def __init__(self, config_path=None, runner=subprocess.run):
        self.config_path = Path(config_path or ai_filter._dir() / "paper" / "hermes.json")
        self.runner = runner

    def enabled(self):
        try:
            return json.loads(self.config_path.read_text()).get("enabled") is True
        except (OSError, ValueError, AttributeError):
            return False

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
                                 encoding="utf-8", timeout=180, check=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            envelope = _envelope(result.stdout)
            from momx.paper_ai import _structured_text
            return {**parse(_structured_text(envelope["response"])), "provider": "hermes", "model": "ollama/qwen3.5:4b", "available": True}
        except Exception as exc:
            return {"provider": "hermes", "available": False, "reason": type(exc).__name__ + ": independent review unavailable"}


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
