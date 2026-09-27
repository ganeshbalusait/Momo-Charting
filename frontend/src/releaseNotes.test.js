// Release notes exist to answer one question after the fact: "something changed
// and my app started behaving differently - what shipped, and when?" Four
// deploys landed on 2026-08-31 alone, so a note that says only "31 Aug" cannot
// tell him whether a fix was in the build he was running at 3pm. The timestamp
// is the whole point, which is why it is what these tests are hardest on.
import test from "node:test";
import assert from "node:assert/strict";

import {
  parseReleases,
  versionLabel,
  formatStamp,
  newestAt,
  unreadCount,
  readSeen,
  writeSeen,
} from "./releaseNotes.js";

const doc = (releases) => ({ note: "x", releases });

const AUG31_4PM = "2026-08-31T16:00:00-04:00";
const AUG31_6PM = "2026-08-31T18:00:00-04:00";
const AUG31_2PM = "2026-08-31T14:22:00-04:00";
const AUG30_9AM = "2026-08-30T09:00:00-04:00";

const entry = (at, heading = "h") => ({ at, heading, subheading: "s", items: ["a", "b"] });

test("sorts newest first regardless of the order in the file", () => {
  const out = parseReleases(doc([entry(AUG30_9AM), entry(AUG31_6PM), entry(AUG31_4PM)]));
  assert.deepEqual(out.map((r) => r.at), [AUG31_6PM, AUG31_4PM, AUG30_9AM]);
});

test("survives a missing, empty or garbage document rather than throwing", () => {
  for (const bad of [null, undefined, {}, { releases: null }, { releases: "nope" }, 7, "x"]) {
    assert.deepEqual(parseReleases(bad), [], `failed on ${JSON.stringify(bad)}`);
  }
});

test("an entry with no usable timestamp is KEPT and sorted last, not silently dropped", () => {
  // Dropping it would hide shipped work from the only page that records it.
  const out = parseReleases(doc([entry(AUG31_4PM), entry("not-a-date", "undated"), entry(AUG30_9AM)]));
  assert.equal(out.length, 3);
  assert.equal(out[out.length - 1].heading, "undated");
  assert.equal(out[out.length - 1].at, null);
});

test("drops entries that carry no content at all", () => {
  const out = parseReleases(doc([entry(AUG31_4PM), { at: AUG31_6PM }, null, "x"]));
  assert.deepEqual(out.map((r) => r.heading), ["h"]);
});

test("items are always an array, whatever the file says", () => {
  const out = parseReleases(doc([{ at: AUG31_4PM, heading: "h", items: "not a list" }]));
  assert.deepEqual(out[0].items, []);
});

// --- version label ------------------------------------------------------

test("version is year.month.day.Nth-deploy-of-that-day, counted oldest-first", () => {
  const rel = parseReleases(doc([entry(AUG31_2PM), entry(AUG31_4PM), entry(AUG31_6PM), entry(AUG30_9AM)]));
  // newest (6PM) is the third deploy of 31 Aug
  assert.equal(versionLabel(rel[0], rel), "2026.8.31.3");
  assert.equal(versionLabel(rel[1], rel), "2026.8.31.2");
  assert.equal(versionLabel(rel[2], rel), "2026.8.31.1");
  assert.equal(versionLabel(rel[3], rel), "2026.8.30.1");
});

test("the ordinal counts the ET day, not UTC - a 9pm ET deploy is not tomorrow", () => {
  // 21:00 ET on the 31st is 01:00 UTC on 1 Sep. Using UTC would call this
  // September's first deploy and renumber the day underneath him.
  const rel = parseReleases(doc([entry("2026-08-31T09:00:00-04:00"), entry("2026-08-31T21:00:00-04:00")]));
  assert.equal(versionLabel(rel[0], rel), "2026.8.31.2");
});

test("an undated entry has no version rather than a wrong one", () => {
  const rel = parseReleases(doc([entry("garbage")]));
  assert.equal(versionLabel(rel[0], rel), null);
});

// --- stamp --------------------------------------------------------------

test("the stamp carries weekday, date, year AND time in ET", () => {
  const s = formatStamp(AUG31_6PM);
  for (const part of ["Mon", "31", "Aug", "2026", "6:00", "PM", "ET"]) {
    assert.ok(s.includes(part), `"${s}" is missing ${part}`);
  }
});

test("the stamp is ET even when the entry is written in another offset", () => {
  // Same instant as 6:00 PM ET, expressed as UTC.
  assert.ok(formatStamp("2026-08-31T22:00:00Z").includes("6:00"));
});

test("an unusable stamp renders as nothing, never as Invalid Date", () => {
  for (const bad of [null, undefined, "", "nope", 5]) assert.equal(formatStamp(bad), "");
});

// --- unread -------------------------------------------------------------

test("everything is unread until something has been read", () => {
  const rel = parseReleases(doc([entry(AUG31_6PM), entry(AUG31_4PM)]));
  assert.equal(unreadCount(rel, null), 2);
});

test("counts only entries newer than the last one read", () => {
  const rel = parseReleases(doc([entry(AUG31_6PM), entry(AUG31_4PM), entry(AUG30_9AM)]));
  assert.equal(unreadCount(rel, AUG31_4PM), 1);
  assert.equal(unreadCount(rel, AUG31_6PM), 0);
});

test("a read marker from the future does not go negative or wrap", () => {
  const rel = parseReleases(doc([entry(AUG31_6PM)]));
  assert.equal(unreadCount(rel, "2027-01-01T00:00:00-05:00"), 0);
});

test("a corrupt read marker is treated as having read nothing, not everything", () => {
  // Erring the other way would silently hide every future release note.
  const rel = parseReleases(doc([entry(AUG31_6PM), entry(AUG31_4PM)]));
  assert.equal(unreadCount(rel, "corrupt"), 2);
});

test("undated entries never count as unread - they can never be marked read", () => {
  // They sort last and carry no instant, so counting them would pin the dot on
  // forever with nothing the trader could do about it.
  const rel = parseReleases(doc([entry(AUG31_6PM), entry("garbage")]));
  assert.equal(unreadCount(rel, AUG31_6PM), 0);
});

test("newestAt is the marker to store when the page is opened", () => {
  const rel = parseReleases(doc([entry(AUG31_4PM), entry(AUG31_6PM)]));
  assert.equal(newestAt(rel), AUG31_6PM);
  assert.equal(newestAt([]), null);
  assert.equal(newestAt(parseReleases(doc([entry("garbage")]))), null);
});

// --- the read marker ----------------------------------------------------

test("the read marker round-trips, and hostile storage never throws", () => {
  const map = new Map();
  const store = {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
  };
  assert.equal(readSeen(store), null);
  writeSeen(store, AUG31_6PM);
  assert.equal(readSeen(store), AUG31_6PM);

  const hostile = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); } };
  assert.equal(readSeen(hostile), null);
  assert.doesNotThrow(() => writeSeen(hostile, AUG31_6PM));
  assert.equal(readSeen(null), null);
  assert.doesNotThrow(() => writeSeen(null, AUG31_6PM));
});

test("a null or empty marker is never written - it would read as 'nothing seen' forever", () => {
  const map = new Map();
  const store = { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => map.set(k, v) };
  writeSeen(store, null);
  writeSeen(store, "");
  assert.equal(map.size, 0);
});
