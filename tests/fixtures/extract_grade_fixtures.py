"""Extract the grade test fixtures from the real MomX History archive.

Run once from the repo root (worktree):
    "/c/GANESH/AgenticAI-Trading 7/AgenticAI-Trading 2/.venv/Scripts/python.exe" \
        tests/fixtures/extract_grade_fixtures.py

The History archive lives only in the main checkout (this worktree has no
artifacts/), so the source directory is a CLI argument defaulting to the main
checkout's path. The output is committed here so tests never read artifacts/.
"""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HIST = Path(
    "C:/GANESH/AgenticAI-Trading 7/AgenticAI-Trading 2/artifacts/momx_history/Watchlist"
)
WANT = [
    ("META_0933", "2026-09-21", "META", "09:33"),
    ("INTC_0933", "2026-09-21", "INTC", "09:33"),
    ("QCOM_0942", "2026-09-21", "QCOM", "09:42"),
    ("QCOM_1041", "2026-09-21", "QCOM", "10:41"),
    ("RIOT_0902", "2026-09-21", "RIOT", "09:02"),
    ("XOVR_0001", "2026-09-22", "XOVR", "00:01"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=DEFAULT_HIST,
        help="Directory holding the dated MomX History JSON files (read-only source).",
    )
    args = parser.parse_args()
    hist = args.history_dir

    out = {}
    cache = {}
    for label, day, symbol, hhmm in WANT:
        doc = cache.setdefault(day, json.loads((hist / f"{day}.json").read_text(encoding="utf-8")))
        snaps = doc["rows"][symbol]["snapshots"]
        snap = next(s for s in snaps if s["at"][11:16] == hhmm)
        out[label] = {"at": snap["at"], "row": snap["row"]}
    target = ROOT / "tests" / "fixtures" / "momx_grade_rows.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    print("wrote", target, "labels:", ", ".join(out))


if __name__ == "__main__":
    main()
