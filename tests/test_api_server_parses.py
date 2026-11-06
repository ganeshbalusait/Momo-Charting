"""Unit: the backend file loads, both on disk and as COMMITTED.

2026-11-06: commit f5fb1744 (BOT Truth check) saved an api_server.py whose
/bot-truth route was pasted into the middle of the /bot route, and its helper
into the middle of _momx_bot_version. The copy on disk was fine, so the live
server and every test ran - but the committed copy raised IndentationError, so
any checkout / stash / fresh worktree would have produced a backend that
cannot start. These tests check the disk copy AND the HEAD copy.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FILES = ["api_server.py", "momx_worker.py"]


@pytest.mark.parametrize("name", FILES)
def test_the_file_on_disk_parses(name):
    path = ROOT / name
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("name", FILES)
def test_the_committed_copy_parses(name):
    try:
        out = subprocess.run(["git", "show", f"HEAD:{name}"], cwd=ROOT, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    if out.returncode != 0:
        pytest.skip(f"{name} is not in HEAD")
    ast.parse(out.stdout.decode("utf-8"), filename=f"HEAD:{name}")


def test_each_bot_route_is_one_whole_block():
    """The two routes the bad commit interleaved: each `if` is followed by its own body."""
    source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    seen = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            right = node.test.comparators[0] if node.test.comparators else None
            if isinstance(right, ast.Constant) and right.value in ("/api/momx-scanner/bot", "/api/momx-scanner/bot-truth"):
                seen.setdefault(right.value, []).append(ast.get_source_segment(source, node))
    bot = "\n".join(seen.get("/api/momx-scanner/bot", []))
    truth = "\n".join(seen.get("/api/momx-scanner/bot-truth", []))
    assert "_momx_bot_truth(" in truth and "_momx_bot_truth(" not in bot
    assert "_session_user()" in bot


def test_the_truth_helper_is_a_module_function_not_inside_another():
    tree = ast.parse((ROOT / "api_server.py").read_text(encoding="utf-8"))
    top = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert {"_momx_bot_truth", "_momx_bot_version", "_momx_bot_payload"} <= top
