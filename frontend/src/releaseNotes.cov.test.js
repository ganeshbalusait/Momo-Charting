// Unit (2026-11-04 coverage): the SHIPPED release-notes file obeys the rules
// the Release Notes page and its unread dot depend on. The page reads
// frontend/public/release-notes.json as-is, so a malformed entry there is a
// broken page for the trader even when every function is right.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { newestAt, parseReleases, unreadCount, versionLabel } from "./releaseNotes.js";

const RAW = JSON.parse(readFileSync(new URL("../public/release-notes.json", import.meta.url), "utf8"));
const ENTRIES = RAW.releases;
const easternOffset = (at) => {
  const name = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", timeZoneName: "shortOffset" })
    .formatToParts(new Date(at)).find((part) => part.type === "timeZoneName").value;
  return name === "GMT-4" ? "-04:00" : "-05:00";
};

test("every shipped entry parses, so the page lists all of them", () => {
  assert.ok(Array.isArray(ENTRIES) && ENTRIES.length > 0);
  assert.equal(parseReleases(RAW).length, ENTRIES.length, "an entry was dropped by the parser");
});

test("every entry is stamped in ET with the offset that was in force that day", () => {
  const wrong = ENTRIES.filter((entry) => typeof entry.at !== "string" || !entry.at.endsWith(easternOffset(entry.at)));
  assert.deepEqual(wrong.map((entry) => entry.at), []);
});

test("every entry has a heading and at least one plain item", () => {
  const empty = ENTRIES.filter((entry) => !String(entry.heading || "").trim()
    || !Array.isArray(entry.items) || !entry.items.some((item) => typeof item === "string" && item.trim()));
  assert.deepEqual(empty.map((entry) => entry.at), []);
});

test("the version label of the newest entry is year.month.day.N", () => {
  const releases = parseReleases(RAW);
  assert.match(versionLabel(releases[0], releases), /^\d{4}\.\d{1,2}\.\d{1,2}\.\d+$/);
});

test("the unread dot: the newest stamp clears it, the second-newest leaves exactly one", () => {
  const releases = parseReleases(RAW);
  assert.equal(unreadCount(releases, newestAt(releases)), 0);
  const secondNewest = releases.find((release) => release.ms !== null && release.ms < releases[0].ms);
  const newerThanSecond = releases.filter((release) => release.ms !== null && release.ms > secondNewest.ms).length;
  assert.equal(unreadCount(releases, secondNewest.at), newerThanSecond);
  assert.ok(newerThanSecond >= 1);
  assert.equal(unreadCount(releases, "garbage"), releases.filter((release) => release.ms !== null).length,
    "a corrupt marker counts every dated entry as unread");
});

// Known data problem (2026-11-04): the gate (scripts/check_release_notes.py)
// treats releases[0] as "this deploy's entry", and versionLabel numbers the
// Nth deploy of a day by stamp, so the file must be newest-first with unique
// stamps. The page itself re-sorts, which is why nobody noticed.
test("the file is already newest first", { todo: "18 entries sit below an OLDER one (e.g. 2026-10-30T13:51 under 13:42)" }, () => {
  const outOfOrder = ENTRIES.filter((entry, index) => index > 0 && new Date(entry.at) > new Date(ENTRIES[index - 1].at));
  assert.deepEqual(outOfOrder.map((entry) => entry.at), []);
});

test("no two entries share a stamp", { todo: "7 pairs share an exact stamp (e.g. 2026-11-04T01:06:58-05:00 twice)" }, () => {
  const seen = new Set();
  const duplicates = ENTRIES.filter((entry) => (seen.has(entry.at) ? true : (seen.add(entry.at), false)));
  assert.deepEqual(duplicates.map((entry) => entry.at), []);
});
