"""Integration (Python -> browser contract): a board built by the REAL scan
and column code, served by the real scanner worker over HTTP, is fed to the
browser's own FILTERS and Setup-tag code (frontend/src/momxFilters.js) in
node.

FILTERS reads row.rvol[tf], row.sqz[tf] and row.skittles[tf] by name, and
the Setup cell reads row.grade / row.strategy / row.momoxAPlus / row.solo.
If the Python side renamed a timeframe key or a field, the browser would not
crash - it would silently fail every row and the trader would see an empty
board. This test is the tripwire for that.

The feed is a stub (no Alpaca/Schwab); news, earnings and the industry
lookup are switched off. The board build runs sequentially (3 symbols, a
stub feed) - no process pool.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

import pytest

from momx import ai_filter, board, industry_lookup
from worker_harness import running_worker

REPO = Path(__file__).resolve().parents[1]
FILTERS_JS = (REPO / "frontend" / "src" / "momxFilters.js").as_uri()
NODE = shutil.which("node")
Result = namedtuple("Result", "bars errors")
ANCHOR = int(datetime(2026, 10, 1, 13, 30, tzinfo=timezone.utc).timestamp())


def bars(count, minutes, *, drift, volume=10_000, spike_from=None):
    out = []
    price = 50.0
    for index in range(count):
        price = max(5.0, price + drift)
        surge = 12 if spike_from is not None and index >= count - spike_from else 1
        out.append({
            "timestamp": ANCHOR + index * minutes * 60,
            "open": price - drift / 2, "high": price + 0.4, "low": price - 0.4, "close": price,
            "volume": volume * surge + index,
        })
    return out


class StubFeed:
    def __init__(self):
        shapes = {"UPPP": dict(drift=0.05, spike_from=6), "FLAT": dict(drift=0.0), "DOWN": dict(drift=-0.04, spike_from=3)}
        self.five = {s: bars(400, 5, **k) for s, k in shapes.items()}
        self.thirty = {s: bars(400, 30, **k) for s, k in shapes.items()}
        self.daily = {s: bars(200, 1440, **k) for s, k in shapes.items()}

    def fetch_5m(self, symbols, **kwargs):
        return Result({s: self.five[s] for s in symbols if s in self.five}, {})

    def fetch_30m(self, symbols, **kwargs):
        return Result({s: self.thirty[s] for s in symbols if s in self.thirty}, {})

    def fetch_daily(self, symbols, **kwargs):
        return Result({s: self.daily[s] for s in symbols if s in self.daily}, {})


NODE_CHECK = r"""
import { readFileSync } from "node:fs";
// With -e, argv[1] is the first argument after the script.
const mod = await import(process.argv[1]);
const served = JSON.parse(readFileSync(process.argv[2], "utf8"));
const rows = [...(served.rows || []), ...(served.rest || [])];
const missing = [];
const sections = { rvol: mod.FILTER_RVOL_TIMEFRAMES, sqz: mod.FILTER_SQZ_TIMEFRAMES, skittles: mod.FILTER_SKITTLES_TIMEFRAMES };
for (const row of rows) {
  for (const [section, frames] of Object.entries(sections)) {
    for (const tf of frames) {
      const cell = row[section] && row[section][tf];
      if (!cell || typeof cell !== "object" || !("value" in cell) || !("bg" in cell)) missing.push(`${row.symbol} ${section}.${tf}`);
    }
  }
  if (!("grade" in row)) missing.push(`${row.symbol} grade`);
}
const config = mod.coerceFilters(null);
const allRvol = { ...config, rvol: { ...config.rvol, bullishOnly: false, timeframes: Object.fromEntries(mod.FILTER_RVOL_TIMEFRAMES.map((tf) => [tf, { on: true, min: 0 }])) }, sqz: { ...config.sqz, on: false } };
const now = Date.parse(served.generatedAt) || Date.now();
const out = {
  symbols: rows.map((r) => r.symbol),
  missing,
  passesDefault: rows.map((r) => mod.rowPasses(r, config, now)),
  passesAnyRvol: rows.map((r) => mod.rowPasses(r, allRvol, now)),
  filtered: mod.filterRows(rows, config, now).map((r) => r.symbol),
  tags: rows.map((r) => mod.strategyTags(r, now).map((t) => t.key)),
  fresh: rows.map((r) => mod.freshAlert(r, now)),
  stamp: (() => {
    const tagged = { ...rows[0], momoxAPlus: { at: new Date(now - 60_000).toISOString(), weeklies: true, sqz: ["4h"] } };
    return mod.strategyTags(tagged, now).map((t) => t.key);
  })(),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture
def quiet_build(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "_earnings_map", lambda symbols, now=None: {})
    monkeypatch.setattr(board, "_news_map", lambda symbols, now=None: {})
    monkeypatch.setenv(industry_lookup.CACHE_PATH_ENV, str(tmp_path / "industry.json"))
    monkeypatch.setattr(industry_lookup, "fetch_industry", lambda *a, **k: None)
    monkeypatch.setattr(ai_filter, "mark_rows", lambda payload, side, now=None: None)
    industry_lookup.clear_memory_cache()
    yield
    industry_lookup.clear_memory_cache()


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_a_served_board_has_every_field_the_browser_filters_read(tmp_path, monkeypatch, quiet_build):
    built = board.build_board(["UPPP", "FLAT", "DOWN"], limit=None, feed_module=StubFeed())
    assert not {k: v for k, v in built["errors"].items() if not k.startswith("_")}, built["errors"]
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", built)
        status, served = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, served
    path = tmp_path / "served.json"
    path.write_text(json.dumps(served), encoding="utf-8")
    run = subprocess.run([NODE, "--input-type=module", "-e", NODE_CHECK, FILTERS_JS, str(path)],
                         capture_output=True, text=True, timeout=60, cwd=str(REPO / "frontend"))
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout)
    assert sorted(seen["symbols"]) == ["DOWN", "FLAT", "UPPP"]
    assert seen["missing"] == [], "fields the FILTERS panel reads are missing from the served rows"
    # Every verdict is a real boolean, the board filter agrees with the row verdicts...
    assert all(isinstance(v, bool) for v in seen["passesDefault"] + seen["passesAnyRvol"])
    assert seen["filtered"] == [s for s, ok in zip(seen["symbols"], seen["passesDefault"]) if ok]
    # ...and a threshold of zero on every RVOL timeframe passes every row whose
    # cells carry a number - i.e. the browser can read the Python values.
    assert any(seen["passesAnyRvol"]), "no served RVOL value was readable by the browser"
    assert all(isinstance(t, list) for t in seen["tags"])
    assert all(hit is None or hit["key"] in ("go", "go-rvol-macd", "mxaplus") for hit in seen["fresh"])
    # The MomoX A+ stamp the worker writes ({at, weeklies, sqz}) becomes the
    # Setup cell's MX A+ tag behind the flashing bolt.
    assert seen["stamp"][0] == "bolt" and "mxaplus" in seen["stamp"]
