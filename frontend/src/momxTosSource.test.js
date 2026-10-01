// TOS checkbox (2026-09-30): the MomX toolbar's switch between TOS (Schwab
// only) and Alpaca scanner data. Source-level checks in the momxHistory.test.js
// style - the panel is too large to mount under node:test.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const panelSource = readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");

test("the TOS checkbox is gone - TOS data is permanent (2026-10-01)", () => {
  assert.ok(!panelSource.includes("<span>TOS</span>"), "no TOS toggle on the toolbar");
  assert.ok(!panelSource.includes('data-testid="momx-tos-source"'));
  assert.match(panelSource, /"TOS loading "/);                       // the progress note stays
});

test("TOS checkbox POSTs the source to /api/momx-scanner/data-source", () => {
  assert.match(panelSource, /const MOMX_DATA_SOURCE_ENDPOINT = "\/api\/momx-scanner\/data-source";/);
  const start = panelSource.indexOf("const onDataSourceChange = useCallback(");
  assert.ok(start > 0);
  const body = panelSource.slice(start, start + 3000);
  assert.match(body, /fetch\(MOMX_DATA_SOURCE_ENDPOINT, \{/);
  assert.match(body, /method: "POST"/);
  assert.match(body, /credentials: "include"/);
  assert.match(body, /JSON\.stringify\(\{ source: next \}\)/);
  assert.match(body, /const next = checked \? "tos" : "alpaca";/);
  assert.match(body, /Only an admin can change the data source\./);
  // Success re-runs the Refresh button's rebuild-and-wait.
  assert.match(body, /forceRebuild\(\);/);
  // Failure reverts: the optimistic value is cleared in finally, and
  // serverSource is only written on success.
  assert.match(body, /finally \{\s*if \(mountedRef\.current\) setSourcePending\(null\);/);
});

test("after a switch only an already-in-flight poll is ignored, not a time window", () => {
  // dataSource is stamped at serve time, so a time-based hold (5 min) could
  // only pin the box to a wrong value after another admin switched it back.
  assert.doesNotMatch(panelSource, /MOMX_DATA_SOURCE_HOLD_MS|hold\.until/);
  assert.match(panelSource, /sourceHoldRef\.current = \{ value: applied, since: Date\.now\(\) \};/);
  assert.match(panelSource, /reported !== hold\.value && boardRequestedAtRef\.current < hold\.since\) return;/);
  // The stamp is the request's START, set right before the board it produced.
  assert.match(panelSource, /const requestedAt = Date\.now\(\);\s*const response = await fetch\(url, \{ cache: "no-store" \}\);/);
  assert.match(panelSource, /boardRequestedAtRef\.current = requestedAt;\s*setBoard\(payload\);/);
});

test("tooltip is the trader's plain-language wording", () => {
  assert.match(
    panelSource,
    /"TOS: scan only on your TOS \(Schwab\) data - matches the TOS scanner\. The full list refreshes every few minutes because Schwab limits requests; the top rows stay live\. Untick for Alpaca data: the full list refreshes about every minute, but volume and prices can differ from TOS\."/,
  );
});

test("TOS loading note shows loaded/total only while symbols are pending", () => {
  assert.match(panelSource, /"TOS loading " \+ \(Number\(tosStatus\.loaded\) \|\| 0\) \+ "\/" \+ Number\(tosStatus\.total\)/);
  assert.match(panelSource, /Number\(tosStatus\.pending\) > 0/);
  assert.match(panelSource, /<span className="momx-since" data-testid="momx-tos-loading">\{tosLoadingNote\}<\/span>/);
});

test("rate-paced 'tos: queued' symbols are not reported as missing data", () => {
  assert.match(panelSource, /!String\(liveBoard\.errors\[symbol\]\)\.includes\("tos: queued"\)/);
});
