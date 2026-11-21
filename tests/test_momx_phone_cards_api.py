"""Integration (2026-11-20, MomX phone cards): a board built by the REAL scan
code, served by the real scanner worker over HTTP, is read by the phone card
code (frontend/src/momxPhoneCard.js) in node.

The phone card reads row.skittles[2h..M], row.rvol[5m..D], row.sqz[2h..Wk],
pctChange and industry. If the Python side renamed a timeframe key, the card
would show blank pills and dark dots instead of failing - this is the
tripwire. Same stub feed and quiet build as the FILTERS contract test.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from worker_harness import running_worker

REPO = Path(__file__).resolve().parents[1]
CARD_JS = (REPO / "frontend" / "src" / "momxPhoneCard.js").as_uri()

_spec = importlib.util.spec_from_file_location(
    "filters_contract", Path(__file__).with_name("test_cov_scanner-board_filters_contract_api.py"),
)
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)
StubFeed = _contract.StubFeed
NODE = _contract.NODE
quiet_build = _contract.quiet_build  # pytest fixture, re-exported for this module

NODE_CHECK = r"""
import { readFileSync } from "node:fs";
const mod = await import(process.argv[1]);
const served = JSON.parse(readFileSync(process.argv[2], "utf8"));
const rows = [...(served.rows || []), ...(served.rest || [])];
const now = Date.parse(served.generatedAt) || Date.now();
const missing = [];
for (const row of rows) {
  for (const [tf] of mod.PHONE_SKITTLES_TFS) {
    const cell = row.skittles && row.skittles[tf];
    if (!cell || typeof cell !== "object" || !("value" in cell)) missing.push(`${row.symbol} skittles.${tf}`);
  }
  for (const group of mod.PHONE_DOT_GROUPS) {
    for (const tf of group.tfs) {
      const cell = row[group.section] && row[group.section][tf];
      if (!cell || typeof cell !== "object" || !("bg" in cell)) missing.push(`${row.symbol} ${group.section}.${tf}`);
    }
  }
}
const cards = rows.map((row) => mod.phoneRowSummary(row, now));
process.stdout.write(JSON.stringify({
  missing,
  cards: cards.map((card) => ({
    symbol: card.symbol,
    pctText: card.pctText,
    tone: card.tone,
    skitTexts: card.skittles.map((cell) => cell.text),
    painted: card.skittles.filter((cell) => cell.painted).length,
    dots: card.dots.map((group) => group.length),
    litDots: card.dots.flat().filter((dot) => dot.color).length,
  })),
}));
"""


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_served_board_gives_every_phone_card_its_skittles_and_dots(tmp_path, monkeypatch, quiet_build):
    built = _contract.board.build_board(["UPPP", "FLAT", "DOWN"], limit=None, feed_module=StubFeed())
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", built)
        status, served = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, served
    path = tmp_path / "served.json"
    path.write_text(json.dumps(served), encoding="utf-8")
    run = subprocess.run([NODE, "--input-type=module", "-e", NODE_CHECK, CARD_JS, str(path)],
                         capture_output=True, text=True, timeout=60, cwd=str(REPO / "frontend"))
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout)
    assert seen["missing"] == [], "fields the phone card reads are missing from the served rows"
    cards = {card["symbol"]: card for card in seen["cards"]}
    assert sorted(cards) == ["DOWN", "FLAT", "UPPP"]
    for card in cards.values():
        assert card["dots"] == [4, 3, 4]
        assert len(card["skitTexts"]) == 8
        # The browser could read the Python numbers: at least the daily
        # Skittles prints for every stub ticker (200 daily bars each).
        assert card["skitTexts"][2] != "", card
        assert card["pctText"].endswith("%"), card
    assert cards["UPPP"]["tone"] == "up"
    assert cards["DOWN"]["tone"] == "down"
