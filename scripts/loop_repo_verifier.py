from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def run(command: list[str], cwd: Path, timeout: float) -> tuple[bool, float, str]:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    elapsed = time.perf_counter() - started
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    return completed.returncode == 0, elapsed, output[-2000:]


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable verifier for repository experiments.")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    checks = [
        [args.python, "-m", "py_compile", "scanner.py", "strategy.py", "backtester.py", "api_server.py"],
        [args.python, "-m", "unittest", "tests.test_scanner_live_volume", "tests.test_strategy_fast_entry"],
    ]
    if args.full:
        checks[-1] = [args.python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]

    total_seconds = 0.0
    details = []
    gate_pass = True
    for command in checks:
        passed, elapsed, output = run(command, root, args.timeout)
        total_seconds += elapsed
        details.append({"command": command, "passed": passed, "seconds": round(elapsed, 4), "output": output})
        if not passed:
            gate_pass = False
            break
    # Runtime is the optimization metric only after correctness gates pass.
    score = 1_000_000.0 / max(total_seconds, 0.001) if gate_pass else 0.0
    print(json.dumps({
        "score": round(score, 6),
        "gate_pass": gate_pass,
        "metrics": {"seconds": round(total_seconds, 4), "checks": details},
        "summary": "all correctness gates passed" if gate_pass else "a correctness gate failed",
    }))
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
