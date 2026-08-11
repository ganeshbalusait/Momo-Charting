from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loop_engineering import BilevelLoopRunner, LoopConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the safe bilevel experiment loop.")
    parser.add_argument("--config", default="loop_config.example.json")
    parser.add_argument("--reset", action="store_true", help="Discard this run's shadow workspace and state.")
    parser.add_argument("--baseline-only", action="store_true", help="Run only the immutable verifier baseline.")
    args = parser.parse_args()

    config_path = (REPO_ROOT / args.config).resolve()
    config = LoopConfig.from_json(config_path)
    state = BilevelLoopRunner(REPO_ROOT, config).run(
        reset=args.reset,
        baseline_only=args.baseline_only,
    )
    print(json.dumps(state, indent=2, sort_keys=True))
    return 0 if state.get("status") not in {"blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
