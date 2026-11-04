// Coverage addition (2026-11-04): the grouped header row (RVOL / SQZ / SKIT /
// CHART bands) must span EXACTLY as many cells as the header row under it,
// for every column layout the trader can make - or the band labels slide one
// cell out of line with the numbers under them. Run over the REAL column list
// (lifted from MomxScannerPanel.jsx, where it lives inside the component
// file), with and without the Time column, for every one-step move and every
// hidden column.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  LOCKED_KEYS,
  applyLayout,
  columnGroupSpans,
  defaultLayout,
  moveKey,
  pinnedRun,
  readStoredLayout,
  toggleHidden,
} from "./momxColumnLayout.js";
import { historyColumns, liveColumns } from "./momxHistory.js";

// CRLF-safe: a Python edit can flip the panel file to CRLF (see the memory note
// "frontend source-lifting tests need LF").
const source = readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8").replace(/\r\n/g, "\n");
const block = source.slice(source.indexOf("const MOMX_COLUMNS = ["), source.indexOf("\n];", source.indexOf("const MOMX_COLUMNS = [")));
const COLUMNS = [...block.matchAll(/^\s*\{ key: "([^"]+)",[^\n]*$/gm)].map(([line, key]) => {
  const group = /group: "([^"]+)"/.exec(line);
  return group ? { key, group: group[1] } : { key };
});
const KEYS = COLUMNS.map((column) => column.key);

function checkSpans(columns, label) {
  const spans = columnGroupSpans(columns, pinnedRun(columns));
  const total = spans.reduce((sum, span) => sum + span.span, 0);
  assert.equal(total, columns.length, `${label}: group row spans ${total} cells over ${columns.length} headers`);
  assert.equal(new Set(spans.map((span) => span.key)).size, spans.length, `${label}: duplicate span keys`);
  // Walking the spans must land every header under its own band.
  let at = 0;
  for (const span of spans) {
    for (let i = 0; i < span.span; i += 1, at += 1) {
      assert.equal(columns[at].group || null, span.label, `${label}: ${columns[at].key} sits under the ${span.label} band`);
    }
  }
}

test("the real board has its RVOL, SQZ and SKIT bands", () => {
  assert.ok(COLUMNS.length >= 30, `lifted ${COLUMNS.length} columns from MomxScannerPanel.jsx`);
  for (const band of ["RVOL", "SQZ", "SKIT", "CHART"]) {
    assert.ok(COLUMNS.some((column) => column.group === band), band + " band present");
  }
  assert.ok(KEYS.includes("symbol") && KEYS.includes("setup"));
});

test("default layout: the bands line up on Live, Live + Time and History", () => {
  const layout = defaultLayout(KEYS);
  checkSpans(applyLayout(COLUMNS, layout), "live");
  checkSpans(applyLayout(liveColumns(COLUMNS), layout), "live+time");
  checkSpans(applyLayout(historyColumns(COLUMNS), layout), "history");
});

test("every one-step move of every column keeps the bands in line", () => {
  for (const key of KEYS) {
    for (const delta of [-1, 1]) {
      const layout = moveKey(defaultLayout(KEYS), key, delta);
      checkSpans(applyLayout(liveColumns(COLUMNS), layout), `move ${key} ${delta}`);
      checkSpans(applyLayout(historyColumns(COLUMNS), layout), `history move ${key} ${delta}`);
    }
  }
});

test("hiding any one column (and then a whole band) keeps the bands in line", () => {
  for (const key of KEYS) {
    if (LOCKED_KEYS.has(key)) continue;
    const layout = toggleHidden(defaultLayout(KEYS), key);
    const applied = applyLayout(liveColumns(COLUMNS), layout);
    assert.ok(!applied.some((column) => column.key === key), `${key} is hidden`);
    checkSpans(applied, `hide ${key}`);
  }
  let layout = defaultLayout(KEYS);
  for (const column of COLUMNS) if (column.group === "RVOL") layout = toggleHidden(layout, column.key);
  checkSpans(applyLayout(liveColumns(COLUMNS), layout), "no RVOL band");
});

test("Symbol cannot be hidden, so a row always keeps its name", () => {
  const layout = toggleHidden(defaultLayout(KEYS), "symbol");
  assert.ok(applyLayout(COLUMNS, layout).some((column) => column.key === "symbol"));
});

test("a junk or blocked saved layout opens on the default order", () => {
  const previous = globalThis.window;
  try {
    for (const getItem of [() => "{broken", () => JSON.stringify({ version: 99, order: ["nope"] }), () => { throw new Error("blocked"); }]) {
      globalThis.window = { localStorage: { getItem } };
      const layout = readStoredLayout(KEYS);
      assert.deepEqual(applyLayout(COLUMNS, layout).map((column) => column.key), KEYS);
    }
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});
