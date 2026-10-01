import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  FALLBACK_PAGE_SIZE,
  SESSION_BOUNDS,
  jumpOffset,
  jumpTarget,
  pageStartClock,
  formatJumpInput,
  jumpTargetInEntries,
  parseJumpTime,
  resolveJump,
  sessionLine,
  sessionStart,
} from "./momxHistoryJump.js";

// --------------------------------------------------------------------------
// parsing what he actually types
// --------------------------------------------------------------------------

test("every shape of a time he might type", () => {
  assert.equal(parseJumpTime("945"), "09:45");
  assert.equal(parseJumpTime("9:45"), "09:45");
  assert.equal(parseJumpTime("0945"), "09:45");
  assert.equal(parseJumpTime("09:45"), "09:45");
  assert.equal(parseJumpTime(" 09:45 "), "09:45");
  assert.equal(parseJumpTime("1530"), "15:30");
  assert.equal(parseJumpTime("15:30"), "15:30");
});

// A bare hour is a real thing to type when scanning a day.
test("a bare hour becomes the top of that hour", () => {
  assert.equal(parseJumpTime("9"), "09:00");
  assert.equal(parseJumpTime("13"), "13:00");
  assert.equal(parseJumpTime("9:"), "09:00");
});

test("nonsense returns null rather than jumping somewhere he did not ask for", () => {
  for (const input of ["", "   ", "abc", "25:00", "9:75", "99999", null, undefined]) {
    assert.equal(parseJumpTime(input), null, String(input));
  }
});

test("midnight and the last minute both parse", () => {
  assert.equal(parseJumpTime("0"), "00:00");
  assert.equal(parseJumpTime("0000"), "00:00");
  assert.equal(parseJumpTime("2359"), "23:59");
});

// --------------------------------------------------------------------------
// finding the moment
// --------------------------------------------------------------------------

const INDEX = [
  ["00:00", 0, 12],
  ["09:44", 100, 3],
  ["09:46", 140, 5],
  ["13:02", 900, 4],
  ["16:01", 1500, 2],
];

test("an exact time lands on itself", () => {
  assert.deepEqual(jumpTarget(INDEX, "13:02"), { clock: "13:02", position: 900, rows: 4 });
});

// The archive has quiet stretches. Landing on the NEXT recorded moment keeps
// them visible; reporting "nothing found" for 09:45 on a day whose next entry
// is 09:46 would be a lie about the data.
test("a time the archive skipped lands on the next one recorded", () => {
  const hit = jumpTarget(INDEX, "09:45");
  assert.equal(hit.clock, "09:46");
  assert.equal(hit.position, 140);
});

test("a time past the last entry lands on the last one rather than refusing", () => {
  const hit = jumpTarget(INDEX, "23:50");
  assert.equal(hit.clock, "16:01");
  assert.equal(hit.past, true);
});

test("an empty or missing index yields null", () => {
  assert.equal(jumpTarget([], "09:45"), null);
  assert.equal(jumpTarget(undefined, "09:45"), null);
  assert.equal(jumpTarget(INDEX, null), null);
});

// --------------------------------------------------------------------------
// the offset - the arithmetic that has to invert the server exactly
// --------------------------------------------------------------------------

// Verified against the live archive: 2026-09-04 holds 11,770 rows at a page
// size of 400, and "09:45" sits at row 798. Offset 10572 makes the page open
// at 09:45 and run to 10:04, with the pager reading "showing 799-1198".
test("the offset reproduces the measured live jump", () => {
  assert.equal(jumpOffset(798, 11770, 400), 10572);
});

test("a position inside the last page floors at zero rather than going negative", () => {
  assert.equal(jumpOffset(11700, 11770, 400), 0);
  assert.equal(jumpOffset(11769, 11770, 400), 0);
});

// A hardcoded 400 would silently mis-jump the day the server's page size moved.
test("the page size comes from the payload, with a fallback", () => {
  assert.equal(jumpOffset(0, 1000, 100), 900);
  assert.equal(jumpOffset(0, 1000, undefined), 1000 - FALLBACK_PAGE_SIZE);
  assert.equal(FALLBACK_PAGE_SIZE, 400);
});

test("junk inputs yield offset zero rather than NaN", () => {
  assert.equal(jumpOffset("x", 1000, 400), 0);
  assert.equal(jumpOffset(10, "x", 400), 0);
});

// --------------------------------------------------------------------------
// one keystroke, end to end
// --------------------------------------------------------------------------

const PAYLOAD = { timeIndex: INDEX, totalRows: 1600, pageSize: 400 };

test("resolveJump returns where to go and what it actually hit", () => {
  const out = resolveJump("945", PAYLOAD);
  assert.equal(out.clock, "09:45");
  assert.equal(out.landedOn, "09:46", "the archive had no 09:45");
  assert.equal(out.moved, true, "so the box repaints to the time he got");
  assert.equal(out.offset, jumpOffset(140, 1600, 400));
  assert.equal(out.error, undefined);
});

test("resolveJump reports an unreadable time instead of guessing", () => {
  assert.match(resolveJump("banana", PAYLOAD).error, /09:45/);
  assert.match(resolveJump("", PAYLOAD).error, /09:45/);
});

test("resolveJump reports an empty day instead of jumping to zero", () => {
  assert.match(resolveJump("945", { timeIndex: [], totalRows: 0 }).error, /No times/);
});

test("an exact hit is not flagged as moved", () => {
  assert.equal(resolveJump("13:02", PAYLOAD).moved, false);
});

// --------------------------------------------------------------------------
// the box's resting value - it is never empty
// --------------------------------------------------------------------------

// Taken as the MINIMUM stamp on the page rather than rows[0], so a client-side
// sort cannot make the box disagree with the table under it.
test("the box shows the page's earliest time whatever order the rows are in", () => {
  const rows = [
    { at: "2026-09-04T10:04:00-04:00" },
    { at: "2026-09-04T09:45:00-04:00" },
    { at: "2026-09-04T09:58:00-04:00" },
  ];
  assert.equal(pageStartClock(rows), "09:45");
  assert.equal(pageStartClock([...rows].reverse()), "09:45");
});

test("the box copes with an empty or malformed page", () => {
  assert.equal(pageStartClock([]), null);
  assert.equal(pageStartClock(null), null);
  assert.equal(pageStartClock([{}, { at: 42 }]), null);
});

// --------------------------------------------------------------------------
// the session line
// --------------------------------------------------------------------------

test("the session line drops empty windows and keeps the day's order", () => {
  const line = sessionLine([
    ["Overnight", 0], ["Pre", 607], ["Open", 479], ["Morning", 3344],
    ["Midday", 4322], ["Power", 1525], ["After", 1473],
  ]);
  assert.deepEqual(line.map((entry) => entry.name),
    ["Pre", "Open", "Morning", "Midday", "Power", "After"]);
  assert.equal(line[0].rows, 607);
});

test("clicking a session name finds the first row inside its window", () => {
  // 09:44 is already inside Open (09:30-10:00), so it wins over 09:46 - the
  // window is the trading session, not the round hour.
  assert.equal(sessionStart(INDEX, null, "Open"), 100, "09:44 is in 09:30-10:00");
  assert.equal(sessionStart(INDEX, null, "Midday"), 900, "13:02");
  assert.equal(sessionStart(INDEX, null, "Overnight"), 0, "00:00");
  assert.equal(sessionStart(INDEX, null, "Nonsense"), null);
});

test("a session with no rows on the day yields null rather than jumping anywhere", () => {
  assert.equal(sessionStart([["13:02", 900, 4]], null, "Open"), null);
});

// THE TWO COPIES MUST AGREE. The windows live in momx/history.py and are
// mirrored here; this reads the python source rather than trusting the mirror,
// because a silent drift would put the "Open" button somewhere that is not the
// open - the exact failure the preset-button design was rejected for.
test("the session windows match the server's, read from the python source", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const source = fs.readFileSync(
    path.resolve(here, "..", "..", "momx", "history.py"), "utf8",
  );
  // Stop at the tuple's own terminator, not at the first ")" - that one closes
  // the FIRST entry and would leave the block one line long and this
  // comparison vacuously true.
  const start = source.indexOf("SESSION_WINDOWS = (");
  const block = source.slice(start, source.indexOf("\n)", start));
  const found = {};
  for (const match of block.matchAll(/\("(\w+)",\s*([^,]+),\s*([^)]+)\)/g)) {
    const evaluate = (expression) =>
      expression.split("+").reduce((total, term) => {
        const value = term.trim().split("*").reduce((product, piece) => product * Number(piece.trim()), 1);
        return total + value;
      }, 0);
    found[match[1]] = [evaluate(match[2]), evaluate(match[3])];
  }
  assert.deepEqual(found, SESSION_BOUNDS);
});


// ---- keypad formatting --------------------------------------------------------

test("digits typed on a keypad gain their colon: 0930 reads 09:30", () => {
  assert.equal(formatJumpInput("0"), "0");
  assert.equal(formatJumpInput("09"), "09");
  assert.equal(formatJumpInput("093"), "09:3");
  assert.equal(formatJumpInput("0930"), "09:30");
  // Three digits with an impossible hour split the other way.
  assert.equal(formatJumpInput("930"), "9:30");
  // A pasted or already-formatted time is left alone; junk is dropped.
  assert.equal(formatJumpInput("09:30"), "09:30");
  assert.equal(formatJumpInput("9:45"), "9:45");
  assert.equal(formatJumpInput("09:301"), "09:30");
  assert.equal(formatJumpInput("ab"), "");
  assert.equal(formatJumpInput(""), "");
  // Whatever it shows, the parser reads it.
  for (const typed of ["0930", "930", "09:30", "093"]) {
    assert.notEqual(parseJumpTime(formatJumpInput(typed)), null, typed);
  }
  assert.equal(parseJumpTime(formatJumpInput("0930")), "09:30");
  assert.equal(parseJumpTime(formatJumpInput("930")), "09:30");
});

// ---- jumping inside a ticker search ------------------------------------------

const DG = [
  { date: "2026-09-04", timeLabel: "09:31" },
  { date: "2026-09-04", timeLabel: "10:15" },
  { date: "2026-09-04", timeLabel: "14:02" },
  { date: "2026-09-03", timeLabel: "09:45" },
  { date: "2026-09-03", timeLabel: "11:00" },
];

test("ticker + time lands on the newest day's entry at or after the time", () => {
  assert.deepEqual(jumpTargetInEntries(DG, "09:45"), { clock: "10:15", day: "2026-09-04", past: false });
  assert.deepEqual(jumpTargetInEntries(DG, "09:31"), { clock: "09:31", day: "2026-09-04", past: false });
});

test("ticker + day + time pins the search to that day", () => {
  assert.deepEqual(jumpTargetInEntries(DG, "09:45", "2026-09-03"), { clock: "09:45", day: "2026-09-03", past: false });
  assert.deepEqual(jumpTargetInEntries(DG, "10:00", "2026-09-03"), { clock: "11:00", day: "2026-09-03", past: false });
});

test("a time after the last entry lands on the last one and says so", () => {
  assert.deepEqual(jumpTargetInEntries(DG, "15:00"), { clock: "14:02", day: "2026-09-04", past: true });
});

test("a day with nothing loaded, or no entries at all, is null not a crash", () => {
  assert.equal(jumpTargetInEntries(DG, "09:45", "2026-09-01"), null);
  assert.equal(jumpTargetInEntries([], "09:45"), null);
  assert.equal(jumpTargetInEntries(null, "09:45"), null);
  assert.equal(jumpTargetInEntries(DG, ""), null);
});
