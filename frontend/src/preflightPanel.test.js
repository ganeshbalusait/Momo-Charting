// AGX preflight checklist helpers.
//
// The test list is written against the failures that caused the feature to
// exist, not against the code:
//
//  - an unrecognised / missing status must NEVER become a pass (the app told
//    the trader premarket data was missing while nine symbols had it)
//  - "no result yet today" must not render like a green run
//  - the measured NUMBER survives normalisation, and its absence is visible
//  - the 30-day strip is newest-right, gap-filled, and a gap is grey not green
//  - a 404 from a backend that has not shipped the endpoint says exactly that
//  - a non-admin gets visible:false, so App.jsx has a single place to decide
//    "do not even fetch"
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  PREFLIGHT_ENDPOINT,
  PREFLIGHT_RUN_ENDPOINT,
  PREFLIGHT_RUN_TIMEOUT_MS,
  PREFLIGHT_STRIP_DAYS,
  buildPreflightView,
  dayStripLabel,
  extractHistory,
  formatRunAge,
  healSentence,
  healWorked,
  isRunStale,
  normalizeCheck,
  normalizeStatus,
  orderChecks,
  orderDayStrip,
  preflightErrorMessage,
  statusLabel,
  statusToneClass,
  summarizeDayStrip,
  summarizeRun,
  unwrapRun,
} from "./preflightPanel.js";

const NOW = Date.parse("2026-09-01T13:00:00Z");

function check(name, status, extra = {}) {
  return { id: name.toLowerCase().replace(/\W+/g, "_"), name, status, ...extra };
}

function run(checks, extra = {}) {
  return {
    generatedAt: "2026-09-01T12:40:00Z",
    tradingDay: "2026-09-01",
    checks,
    ...extra,
  };
}

// ---- status vocabulary ----------------------------------------------------

test("known spellings map onto the four tones", () => {
  for (const value of ["pass", "PASS", "ok", "green", "healthy", "success", true]) {
    assert.equal(normalizeStatus(value), "pass", `${String(value)} should be pass`);
  }
  for (const value of ["warn", "warning", "degraded", "expected degradation", "stale"]) {
    assert.equal(normalizeStatus(value), "warn", `${String(value)} should be warn`);
  }
  for (const value of ["fail", "FAILED", "error", "critical", "down", false]) {
    assert.equal(normalizeStatus(value), "fail", `${String(value)} should be fail`);
  }
  for (const value of ["skipped", "not_run", "not run", "pending", "n/a"]) {
    assert.equal(normalizeStatus(value), "unknown", `${String(value)} should be unknown`);
  }
});

test("an unrecognised or missing status is UNKNOWN, never a pass", () => {
  // This is the whole point of the feature. Guessing green is the bug class.
  assert.equal(normalizeStatus(undefined), "unknown");
  assert.equal(normalizeStatus(null), "unknown");
  assert.equal(normalizeStatus(""), "unknown");
  assert.equal(normalizeStatus("mostly fine"), "unknown");
  assert.equal(normalizeStatus({}), "unknown");
});

test("tone class and label come from one place", () => {
  assert.equal(statusToneClass("ok"), "preflight-tone-pass");
  assert.equal(statusToneClass("nonsense"), "preflight-tone-unknown");
  assert.equal(statusLabel("degraded"), "WARN");
  assert.equal(statusLabel(undefined), "UNKNOWN");
});

// ---- one check ------------------------------------------------------------

test("the measured number survives, whatever the backend called the field", () => {
  assert.equal(
    normalizeCheck({ name: "Premarket", status: "pass", measure: "9/9 symbols, oldest 41 min" }).measure,
    "9/9 symbols, oldest 41 min",
  );
  assert.equal(normalizeCheck({ name: "X", status: "pass", metric: "151 min stale" }).measure, "151 min stale");
  assert.equal(normalizeCheck({ name: "X", status: "pass", value: 42 }).measure, "42");
});

test("a check with no number says so instead of looking tidy", () => {
  const passing = normalizeCheck({ name: "Charts", status: "pass" });
  assert.equal(passing.hasMeasure, false);
  assert.equal(passing.measure, "no measurement reported");

  const skipped = normalizeCheck({ name: "Charts", status: "skipped" });
  assert.equal(skipped.measure, "check did not run");
});

test("the plain-language action is kept for non-pass rows and dropped for passes", () => {
  assert.equal(
    normalizeCheck({ name: "Schwab", status: "fail", action: "Re-authenticate Schwab in Settings." }).action,
    "Re-authenticate Schwab in Settings.",
  );
  assert.equal(normalizeCheck({ name: "Schwab", status: "pass", action: "Nothing to do." }).action, "");
});

test("auto-repair is surfaced as a fact, not inferred", () => {
  assert.equal(normalizeCheck({ name: "A", status: "pass", autoFixed: true }).autoFixed, true);
  assert.equal(normalizeCheck({ name: "A", status: "pass" }).autoFixed, false);
});

test("checks sort worst-first using preflight.py's own STATUS_ORDER", () => {
  // pass 0 < unknown 1 < warn 2 < fail 3, read backwards. Matching the backend
  // matters: a list that showed unknowns above warns would contradict the
  // verdict worst_status() computed for the same run.
  const ordered = orderChecks([
    check("Alpha", "pass"),
    check("Bravo", "warn"),
    check("Charlie", "fail"),
    check("Delta", "skipped"),
    check("Echo", "pass"),
  ]);
  assert.deepEqual(ordered.map((row) => row.name), ["Charlie", "Bravo", "Delta", "Alpha", "Echo"]);
});

// ---- the headline ---------------------------------------------------------

test("all passing reads as ready with the count", () => {
  const summary = summarizeRun(run([check("A", "pass"), check("B", "pass")]));
  assert.equal(summary.tone, "pass");
  assert.equal(summary.headline, "AGX ready - 2 of 2");
});

test("failures lead the headline and keep the denominator", () => {
  const summary = summarizeRun(run([check("A", "fail"), check("B", "fail"), check("C", "pass")]));
  assert.equal(summary.tone, "fail");
  assert.equal(summary.headline, "2 checks failing - 1 of 3 passing");
  assert.equal(summary.countsLabel, "1 pass · 2 fail");
});

test("one failure is singular", () => {
  assert.equal(summarizeRun(run([check("A", "fail")])).headline, "1 check failing - 0 of 1 passing");
});

test("checks that could not run get their own headline when nothing is worse", () => {
  const summary = summarizeRun(run([check("A", "skipped"), check("B", "pass"), check("C", "pass")]));
  assert.equal(summary.tone, "unknown");
  assert.equal(summary.headline, "1 check could not run - 2 of 3 passing");
});

test("an unknown is NEVER swallowed by a warn or a fail headline", () => {
  // The tone follows worst_status (warn beats unknown), but the sentence must
  // still say the unknown out loud - a check nobody looked at hiding inside a
  // "1 known degradation" headline is the exact bug shape this panel replaces.
  const warned = summarizeRun(run([check("A", "warn"), check("B", "skipped"), check("C", "pass")]));
  assert.equal(warned.tone, "warn");
  assert.equal(warned.headline, "AGX ready - 1 of 3, 1 known degradation, 1 could not run");

  const failed = summarizeRun(run([check("A", "fail"), check("B", "skipped")]));
  assert.equal(failed.tone, "fail");
  assert.equal(failed.headline, "1 check failing - 0 of 2 passing, 1 could not run");
});

test("an expected degradation (Tradier) still reads as ready, not as a failure", () => {
  // Tradier is dead on purpose. A checklist that cries wolf every morning is
  // ignored, which is how the app got here.
  const summary = summarizeRun(run([
    check("Tradier premarket", "warn", { measure: "401 revoked, Alpaca SIP covering 04:00-07:00" }),
    check("Alpaca SIP backfill", "pass", { measure: "9/9 symbols" }),
  ]));
  assert.equal(summary.tone, "warn");
  assert.equal(summary.headline, "AGX ready - 1 of 2, 1 known degradation");
  assert.equal(summary.counts.fail, 0);
});

test("an empty or missing run is 'no result yet' - never a pass", () => {
  for (const payload of [null, undefined, {}, { checks: [] }]) {
    const summary = summarizeRun(payload);
    assert.equal(summary.tone, "empty");
    assert.equal(summary.total, 0);
    assert.equal(summary.headline, "No result yet today");
    assert.notEqual(summary.toneClass, "preflight-tone-pass");
  }
});

test("the run timestamp is read whatever it is called", () => {
  assert.equal(summarizeRun({ checks: [check("A", "pass")], ranAt: "2026-09-01T11:00:00Z" }).ranAt, "2026-09-01T11:00:00Z");
  assert.equal(summarizeRun({ results: [check("A", "pass")], timestamp: "2026-09-01T11:00:00Z" }).ranAt, "2026-09-01T11:00:00Z");
});

test("auto-fix count is summarised for the headline area", () => {
  const summary = summarizeRun(run([check("A", "pass", { autoFixed: true }), check("B", "pass")]));
  assert.equal(summary.autoFixed, 1);
  assert.equal(summary.autoFixedLabel, "1 problem fixed automatically");
});

// ---- the 30-day strip -----------------------------------------------------

test("the strip is exactly N days, oldest first so newest sits on the right", () => {
  const strip = orderDayStrip([{ date: "2026-09-01", status: "pass" }], { today: "2026-09-01", days: 30 });
  assert.equal(strip.length, 30);
  assert.equal(strip[0].date, "2026-08-03");
  assert.equal(strip[29].date, "2026-09-01");
  assert.equal(strip[29].status, "pass");
});

test("days with no archived run are grey and say 'no check ran'", () => {
  const strip = orderDayStrip([{ date: "2026-09-01", status: "pass" }], { today: "2026-09-01", days: 3 });
  assert.deepEqual(strip.map((day) => day.status), ["none", "none", "pass"]);
  assert.equal(strip[0].missing, true);
  assert.equal(strip[0].toneClass, "preflight-tone-none");
  assert.match(strip[0].title, /no check ran/);
  // A gap must not borrow the pass colour.
  assert.notEqual(strip[0].toneClass, "preflight-tone-pass");
});

test("each day carries its date and a summary with numbers for hover/tap", () => {
  const strip = orderDayStrip(
    [{ date: "2026-08-31", status: "fail", passed: 12, total: 14, summary: "Schwab token expired" }],
    { today: "2026-08-31", days: 1 },
  );
  assert.equal(strip[0].label, "Aug 31");
  assert.equal(strip[0].passed, 12);
  assert.equal(strip[0].total, 14);
  assert.equal(strip[0].title, "Aug 31 - FAIL · 12 of 14 passing · Schwab token expired");
});

test("with no explicit today the strip anchors on the newest archived day", () => {
  const strip = orderDayStrip(
    [{ date: "2026-08-20", status: "pass" }, { date: "2026-08-25", status: "fail" }],
    { days: 6 },
  );
  assert.equal(strip[strip.length - 1].date, "2026-08-25");
  assert.equal(strip[strip.length - 1].status, "fail");
});

test("no history at all draws nothing rather than 30 fake days", () => {
  assert.deepEqual(orderDayStrip([], {}), []);
  assert.deepEqual(orderDayStrip(null, {}), []);
});

test("unparseable day rows are dropped, not guessed", () => {
  const strip = orderDayStrip([{ date: "not-a-date", status: "pass" }], { today: "2026-09-01", days: 2 });
  assert.deepEqual(strip.map((day) => day.missing), [true, true]);
});

test("the strip itself reports a number", () => {
  const strip = orderDayStrip(
    [{ date: "2026-09-01", status: "pass" }, { date: "2026-08-31", status: "fail" }],
    { today: "2026-09-01", days: 5 },
  );
  assert.equal(summarizeDayStrip(strip), "2 of the last 5 days ran, 1 failing");
  assert.equal(summarizeDayStrip([]), "No archived days yet");
});

test("day labels are UTC-stable", () => {
  assert.equal(dayStripLabel("2026-01-01"), "Jan 1");
  assert.equal(dayStripLabel("2026-12-31"), "Dec 31");
  assert.equal(dayStripLabel(""), "");
});

// ---- freshness ------------------------------------------------------------

test("run age is a number, and an unknown time says so", () => {
  assert.equal(formatRunAge("2026-09-01T12:40:00Z", NOW), "20 min ago");
  assert.equal(formatRunAge("2026-09-01T12:59:45Z", NOW), "just now");
  assert.equal(formatRunAge("2026-09-01T09:30:00Z", NOW), "3 h 30 min ago");
  assert.equal(formatRunAge("2026-08-30T13:00:00Z", NOW), "2 days ago");
  assert.equal(formatRunAge("", NOW), "run time unknown");
  assert.equal(formatRunAge("garbage", NOW), "run time unknown");
});

test("a run with no readable timestamp counts as stale", () => {
  assert.equal(isRunStale("2026-09-01T12:40:00Z", NOW), false);
  assert.equal(isRunStale("2026-08-29T12:40:00Z", NOW), true);
  assert.equal(isRunStale(undefined, NOW), true);
});

// ---- transport ------------------------------------------------------------

test("the run is found inline or inside any of the usual envelopes", () => {
  const inline = run([check("A", "pass")]);
  assert.equal(unwrapRun(inline), inline);
  assert.equal(unwrapRun({ result: inline }), inline);
  assert.equal(unwrapRun({ latest: inline }), inline);
  assert.equal(unwrapRun(null), null);
});

test("history is found next to the run or inside the envelope", () => {
  assert.equal(extractHistory({ history: [1] }).length, 1);
  assert.equal(extractHistory({ days: [1, 2] }).length, 2);
  assert.equal(extractHistory({ result: { history: [1, 2, 3] } }).length, 3);
  assert.deepEqual(extractHistory(null), []);
});

test("a 404 says the backend half is missing, and never implies health", () => {
  const message = preflightErrorMessage(404);
  assert.match(message, /not deployed/);
  assert.match(message, /Nothing has been measured/);
});

test("403 explains the admin gate; other codes still carry the number", () => {
  assert.match(preflightErrorMessage(403), /admin only/);
  assert.match(preflightErrorMessage(500), /HTTP 500/);
  assert.equal(preflightErrorMessage(500, "boom"), "boom");
  assert.match(preflightErrorMessage(0), /unreachable/);
});

test("endpoints are constants so App.jsx and the tests cannot drift", () => {
  assert.equal(PREFLIGHT_ENDPOINT, "/api/preflight");
  assert.equal(PREFLIGHT_RUN_ENDPOINT, "/api/preflight/run");
  assert.equal(PREFLIGHT_STRIP_DAYS, 30);
});

// ---- the view model -------------------------------------------------------

test("a non-admin gets nothing to render - one place decides 'do not fetch'", () => {
  assert.deepEqual(buildPreflightView({ isAdmin: false, payload: run([check("A", "pass")]) }), { visible: false });
  assert.deepEqual(buildPreflightView({}), { visible: false });
});

test("the admin view exposes a phase so there is no forever-spinner", () => {
  assert.equal(buildPreflightView({ isAdmin: true, loading: true }).phase, "loading");
  assert.equal(buildPreflightView({ isAdmin: true, error: "boom" }).phase, "error");
  assert.equal(buildPreflightView({ isAdmin: true, payload: {} }).phase, "empty");
  assert.equal(buildPreflightView({ isAdmin: true, payload: run([check("A", "pass")]) }).phase, "ready");
});

test("a refresh over an existing result keeps showing the result, not a spinner", () => {
  const view = buildPreflightView({ isAdmin: true, loading: true, payload: run([check("A", "pass")]) });
  assert.equal(view.phase, "ready");
  assert.equal(view.loading, true);
});

test("the admin view carries the headline, rows, strip and age together", () => {
  const view = buildPreflightView({
    isAdmin: true,
    nowMs: NOW,
    payload: {
      ...run([
        check("Premarket coverage", "pass", { measure: "9/9 symbols, oldest 41 min" }),
        check("Schwab token", "fail", { measure: "expired 2 h ago", action: "Re-authenticate Schwab in Settings." }),
      ]),
      history: [{ date: "2026-09-01", status: "fail", passed: 1, total: 2 }],
    },
  });
  assert.equal(view.visible, true);
  assert.equal(view.summary.headline, "1 check failing - 1 of 2 passing");
  assert.equal(view.checks[0].name, "Schwab token");
  assert.equal(view.checks[0].action, "Re-authenticate Schwab in Settings.");
  assert.equal(view.checks[1].measure, "9/9 symbols, oldest 41 min");
  assert.equal(view.strip.length, 30);
  assert.equal(view.strip[29].date, "2026-09-01");
  assert.equal(view.ageLabel, "20 min ago");
  assert.equal(view.stale, false);
});

// ---- the REAL api_server / preflight.py contract --------------------------
//
// Pinned against the shapes those two files actually emit, so a rename on
// either side breaks a test instead of quietly blanking the admin page:
//   check    {id, label, status, measured, detail, healable, critical}
//   run      {at, available, unavailableReason, passed, warned, failed,
//             unknown, elapsedMs, checks, healed}
//   envelope {available, unavailableReason, date, result, ranToday, history,
//             historyDays, historyError, schedule{at, threadStarted, ...}}

const SERVER_RUN = {
  at: "2026-09-01T08:45:12.500000-04:00",
  available: true,
  passed: 1,
  warned: 1,
  failed: 1,
  unknown: 0,
  elapsedMs: 4180,
  healed: ["restarted the premarket warmer"],
  checks: [
    {
      id: "premarket-coverage",
      label: "Premarket scanner coverage",
      status: "pass",
      measured: "9/9 symbols, oldest 41 min",
      detail: "AAPL 148 bars, TSLA 167, NVDA 166",
      healable: false,
      critical: true,
    },
    {
      id: "tradier",
      label: "Tradier premarket feed",
      status: "warn",
      measured: "401 access_token_not_approved",
      detail: "Expected: the account is unfunded. Alpaca SIP covers 04:00-07:00.",
    },
    {
      id: "schwab-token",
      label: "Schwab refresh token",
      status: "fail",
      measured: "expired 2 h ago",
      detail: "Re-authenticate Schwab from Settings before the open.",
      critical: true,
    },
  ],
};

const SERVER_ENVELOPE = {
  available: true,
  unavailableReason: "",
  date: "2026-09-01",
  result: SERVER_RUN,
  ranToday: true,
  history: [SERVER_RUN, { at: "2026-08-31T08:45:00-04:00", passed: 14, warned: 0, failed: 0, unknown: 0 }],
  historyDays: 2,
  historyError: "",
  schedule: { at: "08:45 ET", weekdaysOnly: true, threadStarted: true },
};

test("the server's own check shape (label/measured) maps straight through", () => {
  const view = buildPreflightView({ isAdmin: true, payload: SERVER_ENVELOPE, nowMs: Date.parse("2026-09-01T13:00:00Z") });
  assert.equal(view.phase, "ready");
  assert.equal(view.summary.headline, "1 check failing - 1 of 3 passing");
  assert.equal(view.checks[0].name, "Schwab refresh token");
  assert.equal(view.checks[0].measure, "expired 2 h ago");
  // preflight.py has no `action` field - a failing check must still tell the
  // trader what to do, so its detail is promoted rather than dropped.
  assert.equal(view.checks[0].action, "Re-authenticate Schwab from Settings before the open.");
  assert.equal(view.checks[0].detail, "", "the promoted sentence must not print twice");
  assert.equal(view.checks[2].name, "Premarket scanner coverage");
  assert.equal(view.checks[2].measure, "9/9 symbols, oldest 41 min");
});

test("elapsed time and the healed list become numbers on screen", () => {
  const summary = summarizeRun(SERVER_RUN);
  assert.match(summary.countsLabel, /4180 ms/);
  assert.equal(summary.autoFixed, 1);
  assert.match(summary.autoFixedLabel, /restarted the premarket warmer/);
});

test("a run is dated by its own `at` stamp, not by the reader's clock", () => {
  assert.equal(summarizeRun(SERVER_RUN).day, "2026-09-01");
  const strip = orderDayStrip(SERVER_ENVELOPE.history, { today: "2026-09-01", days: 3 });
  assert.equal(strip[2].date, "2026-09-01");
  assert.equal(strip[1].date, "2026-08-31");
});

test("archived days with counters but no status word are graded, not guessed", () => {
  const strip = orderDayStrip(SERVER_ENVELOPE.history, { today: "2026-09-01", days: 2 });
  assert.equal(strip[1].status, "fail", "1 failed -> fail");
  assert.equal(strip[1].title, "Sep 1 - FAIL · 1 of 3 passing");
  assert.equal(strip[0].status, "pass", "14/14 -> pass");
  assert.equal(strip[0].title, "Aug 31 - PASS · 14 of 14 passing");
});

test("passed and total come from ONE source, so '12 of 3 passing' is impossible", () => {
  const strip = orderDayStrip(
    // Deliberately inconsistent: counters say 14 checks, the array holds 3.
    [{ at: "2026-09-01T08:45:00-04:00", passed: 12, warned: 1, failed: 1, unknown: 0, checks: [1, 2, 3] }],
    { today: "2026-09-01", days: 1 },
  );
  assert.equal(strip[0].title, "Sep 1 - FAIL · 12 of 14 passing");
});

test("a day with no readable counters is unknown, never pass", () => {
  const strip = orderDayStrip([{ at: "2026-09-01T08:45:00-04:00" }], { today: "2026-09-01", days: 1 });
  assert.equal(strip[0].status, "unknown");
});

test("two runs on one day show the later one", () => {
  const strip = orderDayStrip(
    [
      { at: "2026-09-01T08:45:00-04:00", passed: 10, failed: 4 },
      { at: "2026-09-01T09:10:00-04:00", passed: 14, failed: 0 },
    ],
    { today: "2026-09-01", days: 1 },
  );
  assert.equal(strip[0].status, "pass");
  assert.equal(strip[0].passed, 14);
});

test("available:false is reported as 'nothing was measured', not as a pass", () => {
  const view = buildPreflightView({
    isAdmin: true,
    payload: {
      available: false,
      unavailableReason: "preflight.py is not importable",
      date: "2026-09-01",
      ranToday: false,
      result: {
        at: "2026-09-01T08:45:00-04:00",
        available: false,
        unavailableReason: "preflight.py is not importable",
        passed: 0, warned: 0, failed: 0, unknown: 1,
        checks: [{ id: "preflight-module", label: "Preflight checklist", status: "unknown", measured: "did not run", detail: "preflight.py is not importable" }],
        healed: [],
      },
    },
  });
  assert.equal(view.summary.tone, "unknown");
  assert.equal(view.summary.headline, "1 check could not run - 0 of 1 passing");
  assert.equal(view.summary.unavailableReason, "preflight.py is not importable");
  assert.equal(view.notices[0].key, "unavailable");
  assert.match(view.notices[0].text, /Nothing has been measured/);
});

test("a strip built on an unreadable archive says so instead of looking sparse", () => {
  const view = buildPreflightView({
    isAdmin: true,
    payload: { ...SERVER_ENVELOPE, history: [], historyDays: 0, historyError: "PermissionError on artifacts/preflight" },
  });
  const notice = view.notices.find((entry) => entry.key === "history-error");
  assert.ok(notice, "an unreadable archive must be announced");
  assert.match(notice.text, /PermissionError/);
});

test("a backend where the 08:45 job is not scheduled admits it", () => {
  const view = buildPreflightView({
    isAdmin: true,
    payload: { ...SERVER_ENVELOPE, schedule: { at: "08:45 ET", threadStarted: false } },
  });
  const notice = view.notices.find((entry) => entry.key === "scheduler-off");
  assert.ok(notice, "an unstarted scheduler must be announced");
  assert.match(notice.text, /NOT scheduled/);
  assert.equal(view.scheduleLabel, "Runs automatically at 08:45 ET on weekdays");
});

test("result:null with ranToday:false is the empty state, not a pass", () => {
  const view = buildPreflightView({
    isAdmin: true,
    payload: { available: true, date: "2026-09-01", result: null, ranToday: false, history: [], schedule: { at: "08:45 ET", threadStarted: true } },
  });
  assert.equal(view.phase, "empty");
  assert.equal(view.ranToday, false);
  assert.equal(view.summary.headline, "No result yet today");
  assert.notEqual(view.summary.toneClass, "preflight-tone-pass");
});

test("the strip anchors on the server's ET date, not the browser's clock", () => {
  const view = buildPreflightView({ isAdmin: true, payload: SERVER_ENVELOPE });
  assert.equal(view.strip.length, 30);
  assert.equal(view.strip[29].date, "2026-09-01");
});

// ---- structural: the panel in App.jsx actually uses these helpers ---------

test("App.jsx renders the panel through this module and gates it on isAdmin", () => {
  const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(source, /from "\.\/preflightPanel"/, "App.jsx must import the helpers");
  assert.match(source, /buildPreflightView\(/, "App.jsx must build the view model here, not inline");
  assert.match(source, /preflightView\.visible/, "the section must be gated on the module's admin decision");
  // The fetch must not even be attempted for a non-admin.
  assert.match(
    source,
    /const loadPreflight = async \([\s\S]{0,400}?authUser\?\.isAdmin/,
    "loadPreflight must return early for non-admins",
  );
});

test("index.css defines a colour for every tone the strip and rows can emit", () => {
  const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");
  for (const tone of ["pass", "warn", "fail", "unknown", "none", "empty"]) {
    assert.match(css, new RegExp(`preflight-tone-${tone}\\b`), `missing style for preflight-tone-${tone}`);
  }
});


// ---------------------------------------------------------------------------
// REVIEW FIXES (2026-09-01). Every test below is a defect a reviewer drove in
// a real browser or under node against the payload preflight.py actually emits.
// ---------------------------------------------------------------------------

// preflight.py emits healed as OBJECTS, not sentences.
const HEALED_WORKED = {
  id: "chart_freshness",
  label: "Chart candles vs the broker",
  action: "asked the server to re-pull AAPL from the broker",
  at: "2026-09-01T08:45:00-04:00",
  statusBefore: "fail",
  statusAfter: "pass",
  daysRunning: 1,
};
const HEALED_DID_NOT_WORK = { ...HEALED_WORKED, id: "momx_scanner", label: "MomX scanner board", action: "queued a rebuild of the Mag7 board", statusAfter: "fail" };

test("a healed entry renders as a sentence, never as [object Object]", () => {
  const line = healSentence(HEALED_WORKED);
  assert.equal(
    line,
    "Chart candles vs the broker: asked the server to re-pull AAPL from the broker (FAIL -> PASS)",
  );
  assert.ok(!line.includes("[object Object]"));
  assert.ok(!line.includes("statusBefore"));
});

test("a chronic auto-fix says how many days it has been running", () => {
  assert.match(healSentence({ ...HEALED_WORKED, daysRunning: 4 }), /4 days running/);
  // one day is not worth saying
  assert.ok(!healSentence(HEALED_WORKED).includes("day"));
});

test("a plain-string healed entry still renders", () => {
  assert.equal(healSentence("restarted the warmer"), "restarted the warmer");
});

test("only a repair that IMPROVED the outcome counts as a fix", () => {
  assert.equal(healWorked(HEALED_WORKED), true);
  assert.equal(healWorked(HEALED_DID_NOT_WORK), false);
  // fail -> unknown is a re-check that could not run, not a repair
  assert.equal(healWorked({ ...HEALED_WORKED, statusAfter: "unknown" }), false);
  // fail -> warn is a genuine improvement
  assert.equal(healWorked({ ...HEALED_WORKED, statusAfter: "warn" }), true);
});

test("the headline never says a problem was fixed when the row is still failing", () => {
  const summary = summarizeRun(
    run([check("MomX scanner board", "fail", { measured: "board 41 min old" })], {
      healed: [HEALED_DID_NOT_WORK],
    }),
  );
  assert.equal(summary.autoFixed, 0);
  assert.equal(summary.autoFixedLabel, "");
  assert.match(summary.autoFixAttemptedLabel, /1 automatic fix attempted and did NOT work/);
  assert.match(summary.autoFixAttemptedLabel, /queued a rebuild of the Mag7 board/);
});

test("a repair that worked is named, with its action, in plain words", () => {
  const summary = summarizeRun(
    run([check("Chart candles vs the broker", "pass", { measured: "worst 0.0 min" })], {
      healed: [HEALED_WORKED],
    }),
  );
  assert.equal(summary.autoFixed, 1);
  assert.match(summary.autoFixedLabel, /1 problem fixed automatically/);
  assert.match(summary.autoFixedLabel, /re-pull AAPL from the broker/);
  assert.ok(!summary.autoFixedLabel.includes("[object Object]"));
  assert.equal(summary.autoFixAttemptedLabel, "");
});

test("the per-row FIXED badge reads the flag preflight.py actually writes", () => {
  // run_preflight stamps after.healed = true on the repaired check
  assert.equal(normalizeCheck({ id: "c", name: "C", status: "pass", healed: true }).autoFixed, true);
  assert.equal(normalizeCheck({ id: "c", name: "C", status: "pass" }).autoFixed, false);
});

// ---- the day strip shows the day's WORST, not its last run ---------------

const RECOVERED_DAY = {
  date: "2026-08-29",
  at: "2026-08-29T16:00:00-04:00",
  overall: "pass",
  passed: 14,
  warned: 0,
  failed: 0,
  unknown: 0,
  runs: 2,
  worst: "fail",
  worstAt: "2026-08-29T09:05:00-04:00",
  checkWorst: { chart_freshness: "fail" },
  healedIds: ["chart_freshness"],
};

test("a morning that failed and was fixed is NOT a green square", () => {
  const [day] = orderDayStrip([RECOVERED_DAY], { today: "2026-08-29", days: 1 });
  assert.equal(day.status, "fail", "the square must show the day's worst");
  assert.equal(day.latestStatus, "pass");
  assert.equal(day.recovered, true);
});

test("the recovered day says BOTH what happened and how it ended", () => {
  const [day] = orderDayStrip([RECOVERED_DAY], { today: "2026-08-29", days: 1 });
  assert.match(day.title, /worst FAIL at 09:05/);
  assert.match(day.title, /last run PASS 14 of 14/);
});

test("a day that never got worse than its last run reads normally", () => {
  const [day] = orderDayStrip(
    [{ date: "2026-08-28", at: "2026-08-28T08:45:00-04:00", overall: "pass", worst: "pass", passed: 14 }],
    { today: "2026-08-28", days: 1 },
  );
  assert.equal(day.status, "pass");
  assert.equal(day.recovered, false);
  assert.match(day.title, /PASS/);
  assert.ok(!day.title.includes("worst"));
});

test("a day whose worst is worse than its counters still counts as failing", () => {
  const strip = orderDayStrip([RECOVERED_DAY], { today: "2026-08-29", days: 1 });
  assert.match(summarizeDayStrip(strip), /1 failing/);
  assert.match(summarizeDayStrip(strip), /1 recovered during the day/);
});

test("an archive with no worst field still grades from what it has", () => {
  const [day] = orderDayStrip(
    [{ date: "2026-08-27", at: "2026-08-27T08:45:00-04:00", passed: 12, warned: 2, failed: 0, unknown: 0 }],
    { today: "2026-08-27", days: 1 },
  );
  assert.equal(day.status, "warn");
  assert.equal(day.recovered, false);
});

// ---- the run button is bounded -------------------------------------------

test("the run request has a timeout and App.jsx uses it", () => {
  assert.ok(PREFLIGHT_RUN_TIMEOUT_MS > 0 && PREFLIGHT_RUN_TIMEOUT_MS <= 300000);
  const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(source, /new AbortController\(\)/, "runPreflightNow must be abortable");
  assert.match(source, /PREFLIGHT_RUN_TIMEOUT_MS/, "the timeout must come from the shared constant");
  assert.match(source, /AbortError/, "an abort must be reported as a timeout, not as a dead run");
});

// ---- a failed refresh clears the stale rows ------------------------------

test("App.jsx drops the previous payload when a refresh fails", () => {
  const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  const loader = source.slice(source.indexOf("const loadPreflight = async"));
  const body = loader.slice(0, loader.indexOf("const runPreflightNow"));
  assert.match(
    body,
    /if \(!response\.ok\) \{[\s\S]{0,600}?setPreflightPayload\(null\)/,
    "a failed refresh must not leave the previous run's rows on screen",
  );
});

test("with no payload the view is the honest empty state, not a stale pass", () => {
  const view = buildPreflightView({
    payload: null,
    error: "Health checklist request failed (HTTP 500). Nothing has been measured.",
    isAdmin: true,
    nowMs: NOW,
  });
  assert.equal(view.phase, "error");
  assert.equal(view.checks.length, 0);
  assert.equal(view.summary.tone, "empty");
});

// ---- critical rows are marked --------------------------------------------

test("critical survives normalisation and App.jsx renders it on non-passing rows", () => {
  assert.equal(normalizeCheck({ id: "c", name: "C", status: "fail", critical: true }).critical, true);
  const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(source, /check\.critical/, "a failing critical row must not look like a cosmetic one");
  const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");
  assert.match(css, /\.preflight-critical\b/);
  assert.match(css, /\.preflight-autofix-failed\b/);
});


test("a failed repair cannot inflate the fixed count through the per-row flag", () => {
  // preflight.py used to stamp healed:true on every attempt. Even now, the
  // list is what carries before/after, so it is what the count comes from.
  const summary = summarizeRun(
    run(
      [
        check("Chart candles vs the broker", "pass", { measured: "0.0 min", healed: true }),
        check("MomX scanner board", "fail", { measured: "41 min old", healAttempted: true }),
      ],
      { healed: [HEALED_WORKED, HEALED_DID_NOT_WORK] },
    ),
  );
  assert.equal(summary.autoFixed, 1, "one of the two repairs actually repaired something");
  assert.match(summary.autoFixAttemptedLabel, /did NOT work/);
});

test("a row that a fix was tried on, and failed, says so on the row", () => {
  const row = normalizeCheck({
    id: "momx_scanner", name: "MomX", status: "fail",
    healed: false, healAttempted: true, healAction: "queued a rebuild",
  });
  assert.equal(row.autoFixed, false);
  assert.equal(row.autoFixAttempted, true);
  assert.equal(row.autoFixNote, "queued a rebuild");
  const source = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(source, /AUTOMATIC FIX DID NOT WORK/);
});
