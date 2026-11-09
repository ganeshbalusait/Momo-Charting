// Scanner render cost (2026-11-09, "frozen black"): the panel is memo'd so the
// app shell's ticks skip it, identical live quotes keep the held map, and the
// ET day/clock helpers reuse one formatter (answers must not change).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dayLineAt } from './momxFilters.js';

const panel = readFileSync(new URL('./MomxScannerPanel.jsx', import.meta.url), 'utf8');

test('the scanner panel is exported memo-wrapped so app-shell ticks skip it', () => {
  assert.match(panel, /^export default memo\(MomxScannerPanel\);$/m);
  assert.doesNotMatch(panel, /^export default function MomxScannerPanel/m);
});

test('identical live quotes keep the held map instead of re-rendering the board', () => {
  assert.match(panel, /setLiveQuotesBySymbol\(\(held\) => \(held === nextQuotes\s*\|\| JSON\.stringify\(held\) === JSON\.stringify\(nextQuotes\) \? held : nextQuotes\)\)/);
});

test('dayLineAt still judges the ET day, across UTC midnight and ET midnight', () => {
  const row = (at) => ({ dayLines: { reclaim: at } });
  // 20:30 ET Nov 9 is 01:30 UTC Nov 10 - still the same ET day as 09:45 ET.
  const evening = Date.parse('2026-11-09T20:30:00-05:00');
  assert.equal(dayLineAt(row('2026-11-09T09:45:00-05:00'), 'reclaim', evening), Date.parse('2026-11-09T09:45:00-05:00'));
  // 23:59 ET vs 00:01 ET the next day: different ET days.
  assert.equal(dayLineAt(row('2026-11-09T23:59:00-05:00'), 'reclaim', Date.parse('2026-11-10T00:01:00-05:00')), null);
  // Repeated calls in the same minute answer the same (cached) way.
  for (let i = 0; i < 3; i++) assert.equal(dayLineAt(row('2026-11-09T09:45:00-05:00'), 'reclaim', evening + i * 1000), Date.parse('2026-11-09T09:45:00-05:00'));
  assert.equal(dayLineAt(row('bad'), 'reclaim', evening), null);
});
