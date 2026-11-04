"""Integration: scripts/option_spread_scan.py run as a real command in its own
Python process, the way the trader runs it.

The script finds its data from its own location (REPO = the folder above
scripts/), so it is copied into a scratch repo with a scratch chain cache:
the run can never read the live artifacts/. The real momx package is put on
its path so the Bot's own contract rules are used, the ETF list points at a
missing scratch file and its download is switched off.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from test_option_spread_scan import _write_chain

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "option_spread_scan.py"


def scratch_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / "option_spread_scan.py")
    _write_chain(root, "TEST")
    (root / "artifacts" / "momx_board_cache").mkdir(parents=True, exist_ok=True)
    return root


def run(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO), "AGX_MOMX_ETF_PATH": str(tmp_path / "no_etf_list.json"),
           "AGX_MOMX_ETF_REFRESH": "0", "PYTHONIOENCODING": "ascii"}
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "option_spread_scan.py"), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )


def test_the_command_prints_the_table_and_writes_the_csv(tmp_path):
    root = scratch_repo(tmp_path)
    out = tmp_path / "spreads.csv"
    done = run(root, tmp_path, "--symbols", "TEST", "--csv", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    lines = done.stdout.splitlines()
    assert lines[0].startswith("VERDICT (spread as % of mid): TIGHT <=10% | OK 10-15% | WIDE >15%")
    assert any(line.startswith("TICKER SIDE EXPIRY") for line in lines)
    rows = [line.split() for line in lines if line.startswith("TEST ")]
    assert [(row[1], row[-1]) for row in rows] == [("CALL", "TIGHT"), ("PUT", "OK")]
    assert not any("did not import" in line for line in lines), "the Bot's own rules were used"
    assert lines[-1].startswith("Done in") and lines[-1].endswith("files only, no broker or app calls.")
    assert len(out.read_text(encoding="ascii").strip().splitlines()) == 1 + 2


def test_an_unknown_list_exits_2_and_names_the_lists_on_disk(tmp_path):
    root = scratch_repo(tmp_path)
    done = run(root, tmp_path, "--list", "Nope")
    assert done.returncode == 2, done.stdout + done.stderr
    assert done.stdout.startswith("No tickers found for list 'Nope'. Lists on disk: Watchlist, Mag7")
