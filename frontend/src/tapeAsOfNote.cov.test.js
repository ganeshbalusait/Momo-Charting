// Unit (2026-11-04 coverage pass, group charts-studies, feature charts-tape-freshness):
// the "Data as of" verdict - live / closed / stale - at the session edges the
// earlier cases in momxCells.test.js do not reach: the overnight (BOATS) window,
// Friday night, Sunday's 20:00 reopen and the premarket open. The clock is
// always passed in; nothing here reads the wall clock.
import test from "node:test";
import assert from "node:assert/strict";

import { tapeAsOfNote } from "./tapeAsOfNote.js";

// 2026-09-01 is a Tuesday; ET is UTC-4 in September.
const et = (day, hour, minute = 0) => Date.parse(`2026-09-${String(day).padStart(2, "0")}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00-04:00`);
const iso = (ms) => new Date(ms).toISOString();

test("overnight: a BOATS bar 30 minutes old is fine, 50 minutes behind is stale", () => {
  const now = et(1, 23, 0); // Tue 23:00 ET, overnight tape running
  const fine = tapeAsOfNote(iso(now - 30 * 60_000), now);
  assert.equal(fine.stale, false, fine.text);
  assert.equal(fine.tone, "closed", "outside 04:00-20:00 the chip is neutral, not 'live'");
  const dead = tapeAsOfNote(iso(now - 50 * 60_000), now);
  assert.equal(dead.tone, "stale", "45 min overnight slack, then it must shout");
});

test("Tuesday 23:55 holding Tuesday's 20:00 bar is a dead overnight feed, not a closed market", () => {
  const note = tapeAsOfNote(iso(et(1, 20, 0)), et(1, 23, 55));
  assert.equal(note.tone, "stale");
  assert.match(note.text, /^Tape stale - last bar Tue 8:00 PM ET$/);
});

test("Friday night and all Saturday, Friday's 20:00 bar is the last bar of the week", () => {
  const fridayClose = iso(et(4, 19, 59));
  for (const now of [et(4, 22, 0), et(5, 13, 30), et(6, 19, 59)]) {
    const note = tapeAsOfNote(fridayClose, now);
    assert.equal(note.tone, "closed", new Date(now).toISOString());
    assert.equal(note.stale, false);
    assert.match(note.text, /^Last bar Fri 7:59 PM ET$/);
  }
});

test("Sunday 21:00: the overnight session reopened, so Friday's bar is now stale", () => {
  const note = tapeAsOfNote(iso(et(4, 19, 59)), et(6, 21, 0));
  assert.equal(note.tone, "stale");
});

test("premarket: inside 04:00-20:00 a current bar reads live with its age", () => {
  const now = et(2, 4, 10); // Wed 04:10 ET
  const note = tapeAsOfNote(iso(now - 4 * 60_000), now);
  assert.equal(note.tone, "live");
  assert.equal(note.text, "Data 04:06 ET (4m)");
});

test("a regular-hours tape 31 minutes behind is stale; 29 is still live", () => {
  const now = et(2, 11, 0);
  assert.equal(tapeAsOfNote(iso(now - 29 * 60_000), now).tone, "live");
  assert.equal(tapeAsOfNote(iso(now - 31 * 60_000), now).tone, "stale");
});

test("a stamp a few seconds in the future reads as 0m, never a negative age", () => {
  const now = et(2, 11, 0);
  const note = tapeAsOfNote(iso(now + 20_000), now);
  assert.equal(note.tone, "live");
  assert.match(note.text, /\(0m\)$/);
});

test("unreadable stamps give no note rather than a guess", () => {
  const now = et(2, 11, 0);
  for (const value of [undefined, "", "not a date", null]) assert.equal(tapeAsOfNote(value, now), null);
});
