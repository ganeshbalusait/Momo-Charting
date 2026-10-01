// AGX preflight health checklist - pure helpers for the admin-only Settings panel.
//
// WHY THIS MODULE EXISTS AT ALL
// -----------------------------
// Overnight every real fault was invisible from the app's own badges: a status
// string said premarket data was missing while all nine symbols HAD it, a chart
// served 151-minute-old candles with no flag, a credential cache remembered
// "failed" forever. The lesson encoded here is:
//
//   * every check MEASURES AN OUTCOME and reports a NUMBER
//   * a check that could not run says SO - it is UNKNOWN, never green
//   * "no result yet today" is never rendered as a pass
//
// So this file deliberately has no "ok boolean" anywhere. `measure` (the
// number) is a first-class field, and `unknown` is a distinct tone that ranks
// ABOVE warn in the headline, because "I did not look" is worse news than a
// degradation we already understand.
//
// Everything here is a pure function over plain data so it runs under
// `node --test` with no DOM and no fetch. App.jsx owns fetching and rendering.

export const PREFLIGHT_ENDPOINT = "/api/preflight";
export const PREFLIGHT_RUN_ENDPOINT = "/api/preflight/run";
export const PREFLIGHT_STRIP_DAYS = 30;
// Ceiling on POST /api/preflight/run. A live run is ~14s (14 checks, ~12 chart
// fetches, several broker round-trips, all under one server-side lock), so this
// is generous - it exists only so a stalled backend cannot leave the button
// reading "Running checks..." forever with no way out but a page reload.
export const PREFLIGHT_RUN_TIMEOUT_MS = 180000;

// ---------------------------------------------------------------------------
// status vocabulary
// ---------------------------------------------------------------------------
//
// The backend is written by another session; rather than couple the UI to one
// exact spelling we map a generous set of aliases onto four tones. Anything we
// do NOT recognise becomes "unknown" - never "pass". Guessing green is the bug
// class this whole feature exists to kill.

const STATUS_ALIASES = new Map([
  ["pass", "pass"], ["passed", "pass"], ["ok", "pass"], ["good", "pass"],
  ["green", "pass"], ["healthy", "pass"], ["up", "pass"], ["true", "pass"],
  ["success", "pass"],

  ["warn", "warn"], ["warning", "warn"], ["amber", "warn"], ["yellow", "warn"],
  ["degraded", "warn"], ["expected", "warn"], ["expected_degradation", "warn"],
  ["partial", "warn"], ["stale", "warn"],

  ["fail", "fail"], ["failed", "fail"], ["fails", "fail"], ["error", "fail"],
  ["red", "fail"], ["critical", "fail"], ["down", "fail"], ["broken", "fail"],
  ["false", "fail"],

  ["unknown", "unknown"], ["skip", "unknown"], ["skipped", "unknown"],
  ["not_run", "unknown"], ["notrun", "unknown"], ["pending", "unknown"],
  ["grey", "unknown"], ["gray", "unknown"], ["n/a", "unknown"],
  ["na", "unknown"], ["none", "unknown"], ["no_data", "unknown"],
]);

/** Map any backend spelling to one of pass | warn | fail | unknown. */
export function normalizeStatus(value) {
  if (value === true) return "pass";
  if (value === false) return "fail";
  if (value == null) return "unknown";
  const key = String(value).trim().toLowerCase().replace(/[\s-]+/g, "_");
  return STATUS_ALIASES.get(key) || STATUS_ALIASES.get(key.replace(/_/g, "")) || "unknown";
}

/** CSS class for a tone. One place, so the strip and the rows can never drift. */
export function statusToneClass(value) {
  return `preflight-tone-${normalizeStatus(value)}`;
}

/** Short badge text shown on each row. */
export function statusLabel(value) {
  const tone = normalizeStatus(value);
  if (tone === "pass") return "PASS";
  if (tone === "warn") return "WARN";
  if (tone === "fail") return "FAIL";
  return "UNKNOWN";
}

// Worst-first ordering. This is preflight.py's STATUS_ORDER read backwards
// (pass 0, unknown 1, warn 2, fail 3) - deliberately the SAME ranking, so the
// row order on screen can never contradict the verdict the backend computed
// with worst_status(). "unknown" sits above pass because "I could not look" is
// worse news than a measured pass, and below warn because it is less
// actionable than a measured degradation. The headline still NAMES the
// unknowns (see summarizeRun) so they can never hide behind a warn.
const TONE_RANK = { fail: 0, warn: 1, unknown: 2, pass: 3 };

// ---------------------------------------------------------------------------
// normalising one run
// ---------------------------------------------------------------------------

function firstString(source, keys) {
  for (const key of keys) {
    const value = source?.[key];
    if (typeof value === "string" && value.trim()) return value.trim();
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return "";
}

function firstArray(source, keys) {
  for (const key of keys) {
    if (Array.isArray(source?.[key])) return source[key];
  }
  return null;
}

/**
 * The ET day a run or archive entry belongs to.
 *
 * preflight.py attributes a result to the day it was MEASURED, read from its
 * own `at` stamp, not from the clock at read time - so we do the same here
 * rather than falling back to "today". An archived Friday result must never
 * slide onto Monday's square just because that is when it is being looked at.
 */
function dayOf(source) {
  const explicit = firstString(source, ["tradingDay", "date", "day", "sessionDate"]);
  const stamped = explicit || firstString(source, ["at", "generatedAt", "ranAt", "timestamp"]);
  const match = /^\d{4}-\d{2}-\d{2}/.exec(stamped);
  return match ? match[0] : "";
}

/**
 * A run's status when the backend did not stamp one on the envelope.
 *
 * preflight.py archives counters (passed/warned/failed/unknown) rather than a
 * word, so derive the word with the SAME precedence worst_status() uses. If
 * there are no counters at all we return "unknown", never "pass" - a day we
 * cannot read is not a day that passed.
 */
function statusFromCounters(source) {
  const failed = Number(source?.failed ?? source?.fail ?? source?.failCount);
  const warned = Number(source?.warned ?? source?.warn ?? source?.warnCount);
  const unknown = Number(source?.unknown ?? source?.unknownCount);
  const passed = Number(source?.passed ?? source?.pass ?? source?.passCount);
  const any = [failed, warned, unknown, passed].some((value) => Number.isFinite(value));
  if (!any) return "unknown";
  if (Number.isFinite(failed) && failed > 0) return "fail";
  if (Number.isFinite(warned) && warned > 0) return "warn";
  if (Number.isFinite(unknown) && unknown > 0) return "unknown";
  if (Number.isFinite(passed) && passed > 0) return "pass";
  return "unknown";
}

function counterTotals(source) {
  const parts = ["passed", "warned", "failed", "unknown"].map((key) => {
    const value = Number(source?.[key]);
    return Number.isFinite(value) ? value : null;
  });
  if (parts.every((value) => value == null)) return { passed: null, total: null };
  const total = parts.reduce((sum, value) => sum + (value || 0), 0);
  return { passed: parts[0] == null ? null : parts[0], total };
}

/**
 * One checklist row. `measure` is the whole point of the feature: a green tick
 * with no number is what failed the trader. When the backend gives us no
 * number we say "no measurement reported" rather than leaving it blank, so the
 * gap is visible instead of looking tidy.
 */
export function normalizeCheck(raw, index = 0) {
  const source = raw && typeof raw === "object" ? raw : {};
  const status = normalizeStatus(
    source.status ?? source.state ?? source.result ?? source.verdict ?? source.outcome,
  );
  const name = firstString(source, ["name", "label", "title", "check", "id", "key"]) || `Check ${index + 1}`;
  const id = firstString(source, ["id", "key", "slug", "name", "label"]) || `check-${index}`;
  const measure = firstString(source, ["measure", "measured", "metric", "value", "number", "reading", "summary"]);
  const detail = firstString(source, ["detail", "details", "message", "note", "description"]);
  // preflight.py's _result() has no dedicated action field - it puts the
  // "what happened / what to do" sentence in `detail`. So a non-passing check
  // promotes its detail to the action line rather than leaving the trader with
  // a red row and no instruction, and we then do not print it twice.
  const explicitAction = firstString(source, ["action", "fix", "remedy", "nextStep", "next_step", "advice", "howToFix"]);
  const action = status === "pass" ? "" : explicitAction || detail;
  const duration = Number(source.durationMs ?? source.duration_ms ?? source.elapsedMs);

  return {
    id,
    name,
    status,
    tone: status,
    toneClass: statusToneClass(status),
    label: statusLabel(status),
    // Never blank. A row with nothing to show says so out loud.
    measure: measure || (status === "unknown" ? "check did not run" : "no measurement reported"),
    hasMeasure: Boolean(measure),
    detail: detail === action ? "" : detail,
    // The action line is only useful when something needs doing.
    action,
    critical: Boolean(source.critical),
    // `healed` is the name preflight.py actually stamps on a repaired check
    // (run_preflight sets after.healed = true). Without it the per-row badge
    // could never light up, so there was no way to tell WHICH row the auto-fix
    // banner was talking about.
    autoFixed: Boolean(
      source.autoFixed ?? source.auto_fixed ?? source.healed ?? source.repaired ?? source.fixed,
    ),
    // A repair that RAN. Distinct from autoFixed, which means it worked.
    autoFixAttempted: Boolean(
      source.healAttempted ?? source.heal_attempted ?? source.autoFixAttempted,
    ),
    autoFixNote: firstString(source, [
      "autoFixNote", "auto_fix_note", "repairNote", "fixNote", "healAction", "heal_action",
    ]),
    expected: Boolean(source.expected ?? source.expectedDegradation ?? source.expected_degradation),
    durationMs: Number.isFinite(duration) ? duration : null,
  };
}

/**
 * One readable line for an auto-repair.
 *
 * preflight.py reports `healed` as a list of OBJECTS
 * ({id,label,action,at,statusBefore,statusAfter,daysRunning}). The panel used
 * to String() them, so the single line that explains what the system silently
 * did to itself rendered as "[object Object]" - on exactly the mornings
 * something was repaired, i.e. the mornings it matters.
 */
export function healSentence(entry) {
  if (!entry || typeof entry !== "object") return String(entry ?? "");
  const label = firstString(entry, ["label", "name", "id"]) || "check";
  const action = firstString(entry, ["action", "description", "did"]) || "a fix was applied";
  const before = statusLabel(entry.statusBefore ?? entry.status_before);
  const after = statusLabel(entry.statusAfter ?? entry.status_after);
  const days = Number(entry.daysRunning ?? entry.days_running);
  let line = `${label}: ${action} (${before} -> ${after}`;
  if (Number.isFinite(days) && days > 1) line += `, ${days} days running`;
  return `${line})`;
}

/**
 * Did the repair actually repair anything?
 *
 * preflight.py appends to `healed` whenever a fix RAN, whatever the outcome -
 * heal_momx_scanner can structurally never report a post-heal pass, because it
 * queues an async rebuild and the re-check reads the board milliseconds later.
 * Counting those as fixes printed "1 problem fixed automatically" directly
 * above a row still reading FAIL. A re-check that could not run (unknown) is
 * not a fix either.
 */
export function healWorked(entry) {
  if (!entry || typeof entry !== "object") return true; // a plain sentence: reported as done
  const before = normalizeStatus(entry.statusBefore ?? entry.status_before);
  const after = normalizeStatus(entry.statusAfter ?? entry.status_after);
  if (after === "unknown") return false;
  return TONE_RANK[after] > TONE_RANK[before];
}

/** Worst-first, stable within a tone so the list does not jitter between polls. */
export function orderChecks(checks) {
  const rows = (Array.isArray(checks) ? checks : []).map((row, index) => normalizeCheck(row, index));
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const rank = TONE_RANK[a.row.tone] - TONE_RANK[b.row.tone];
      return rank !== 0 ? rank : a.index - b.index;
    })
    .map((entry) => entry.row);
}

function pluralChecks(count) {
  return count === 1 ? "check" : "checks";
}

/**
 * The headline. Counts are always present because "2 checks failing" without a
 * denominator is just another flag.
 */
export function summarizeRun(run) {
  const checks = orderChecks(firstArray(run, ["checks", "results", "items", "rows"]));
  const counts = { pass: 0, warn: 0, fail: 0, unknown: 0 };
  for (const check of checks) counts[check.tone] += 1;
  const total = checks.length;

  const ranAt = firstString(run, ["generatedAt", "ranAt", "completedAt", "finishedAt", "timestamp", "at"]);
  const day = dayOf(run);
  const unavailableReason = run && run.available === false
    ? firstString(run, ["unavailableReason", "unavailable_reason", "reason"]) || "the checklist did not run"
    : "";
  // preflight.py reports auto-repairs as a `healed` list of OBJECTS, and also
  // flags the repaired check itself. Count both, never double-count a check -
  // and count only the repairs that actually changed the outcome.
  const healedRaw = (firstArray(run, ["healed", "repairs", "autoFixes"]) || []).filter(Boolean);
  const healed = healedRaw.map(healSentence);
  const healedWorked = healedRaw.filter(healWorked);
  const healedFailed = healedRaw.filter((entry) => !healWorked(entry));
  const elapsed = Number(run?.elapsedMs);

  if (!run || typeof run !== "object" || total === 0) {
    return {
      checks,
      counts,
      total: 0,
      tone: "empty",
      toneClass: "preflight-tone-empty",
      headline: "No result yet today",
      // Explicitly NOT phrased as reassurance. Empty is not a pass.
      detail: "Nothing has been measured yet. Run the check to see where AGX stands.",
      countsLabel: "0 checks",
      unavailableReason,
      ranAt,
      day,
    };
  }

  // Tone follows the backend's worst_status precedence (fail > warn > unknown
  // > pass). The wording does NOT collapse to the tone: when there are both
  // degradations and unmeasured checks, both numbers appear, because an
  // unknown hidden inside a "warn" headline is exactly the flag-shaped bug
  // this panel replaces.
  let tone = "pass";
  let headline = `AGX ready - ${counts.pass} of ${total}`;
  if (counts.fail > 0) {
    tone = "fail";
    headline = `${counts.fail} ${pluralChecks(counts.fail)} failing - ${counts.pass} of ${total} passing`;
    if (counts.unknown > 0) headline += `, ${counts.unknown} could not run`;
  } else if (counts.warn > 0) {
    tone = "warn";
    headline = `AGX ready - ${counts.pass} of ${total}, ${counts.warn} known ${counts.warn === 1 ? "degradation" : "degradations"}`;
    if (counts.unknown > 0) headline += `, ${counts.unknown} could not run`;
  } else if (counts.unknown > 0) {
    tone = "unknown";
    headline = `${counts.unknown} ${pluralChecks(counts.unknown)} could not run - ${counts.pass} of ${total} passing`;
  }

  const parts = [`${counts.pass} pass`];
  if (counts.warn) parts.push(`${counts.warn} warn`);
  if (counts.fail) parts.push(`${counts.fail} fail`);
  if (counts.unknown) parts.push(`${counts.unknown} unknown`);
  if (Number.isFinite(elapsed) && elapsed > 0) parts.push(`${elapsed} ms`);

  const flaggedFixes = checks.filter((check) => check.autoFixed).length;
  // When the run reports a healed LIST, that list is the count - it carries the
  // before/after statuses, so it can tell a repair from an attempt. max() over
  // the per-row flags let a failed attempt inflate the number back up.
  const autoFixed = healedRaw.length ? healedWorked.length : flaggedFixes;
  const autoFixAttemptedLabel = healedFailed.length
    ? `${healedFailed.length} automatic ${healedFailed.length === 1 ? "fix" : "fixes"} attempted and did NOT work: ${healedFailed.map(healSentence).join("; ")}`
    : "";

  return {
    checks,
    counts,
    total,
    tone,
    toneClass: `preflight-tone-${tone}`,
    headline,
    detail: checks.filter((check) => check.tone !== "pass").map((check) => check.name).join(", "),
    countsLabel: parts.join(" · "),
    autoFixed,
    healed,
    autoFixAttempted: healedFailed.length,
    autoFixAttemptedLabel,
    autoFixedLabel: autoFixed
      ? `${autoFixed} ${autoFixed === 1 ? "problem" : "problems"} fixed automatically${healedWorked.length ? `: ${healedWorked.map(healSentence).join("; ")}` : ""}`
      : "",
    unavailableReason,
    elapsedMs: Number.isFinite(elapsed) ? elapsed : null,
    ranAt,
    day,
  };
}

// ---------------------------------------------------------------------------
// the daily strip
// ---------------------------------------------------------------------------

const DAY_MS = 24 * 60 * 60 * 1000;

/** "2026-09-01" -> epoch ms at UTC midnight. Null for anything unparseable. */
function isoDayToMs(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || "").trim());
  if (!match) return null;
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isFinite(ms) ? ms : null;
}

function msToIsoDay(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

/** "2026-09-01T09:05:00-04:00" -> "09:05". Blank when there is no time in it. */
function timeOfDay(stamp) {
  const match = /T(\d{2}:\d{2})/.exec(String(stamp || ""));
  return match ? match[1] : "";
}

/** "2026-09-01" -> "Sep 1". Calendar arithmetic stays in UTC to avoid drift. */
export function dayStripLabel(isoDay) {
  const ms = isoDayToMs(isoDay);
  if (ms == null) return String(isoDay || "");
  const date = new Date(ms);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][date.getUTCMonth()];
  return `${month} ${date.getUTCDate()}`;
}

/**
 * The last N calendar days, OLDEST FIRST so the newest square lands on the
 * right the way the trader asked. Days with no archived run are "none" (grey)
 * and say so - a missing day must not read as a pass, and must not read as a
 * failure either.
 */
export function orderDayStrip(history, options = {}) {
  const days = Number(options.days) > 0 ? Math.floor(Number(options.days)) : PREFLIGHT_STRIP_DAYS;
  const rows = Array.isArray(history) ? history : [];

  const byDay = new Map();
  for (const raw of rows) {
    const source = raw && typeof raw === "object" ? raw : {};
    // Archived entries are whole run dicts: their day comes from their own
    // `at` stamp, not from a separate date field and never from the clock.
    const ms = isoDayToMs(dayOf(source));
    if (ms == null) continue;
    const iso = msToIsoDay(ms);
    const existing = byDay.get(iso);
    // Two runs on one day (the 08:45 scheduled one plus a manual re-run): the
    // LATER stamp wins, which is what the trader last saw.
    if (!existing || String(source.at || "") >= String(existing.at || "")) byDay.set(iso, source);
  }

  // Anchor: explicit "today", else the newest archived day, else nothing to draw.
  let anchor = isoDayToMs(options.today);
  if (anchor == null) {
    for (const key of byDay.keys()) {
      const ms = isoDayToMs(key);
      if (ms != null && (anchor == null || ms > anchor)) anchor = ms;
    }
  }
  if (anchor == null) return [];

  const strip = [];
  for (let offset = days - 1; offset >= 0; offset -= 1) {
    const ms = anchor - offset * DAY_MS;
    const iso = msToIsoDay(ms);
    const source = byDay.get(iso);
    if (!source) {
      strip.push({
        date: iso,
        label: dayStripLabel(iso),
        status: "none",
        toneClass: "preflight-tone-none",
        missing: true,
        title: `${dayStripLabel(iso)} - no check ran`,
        summary: "No check ran",
      });
      continue;
    }
    const stamped = source.status ?? source.state ?? source.verdict ?? source.overall;
    // No stamped word (preflight.py archives counters) -> derive it with the
    // same precedence worst_status() uses. Unreadable -> unknown, not pass.
    const latestStatus = stamped == null ? statusFromCounters(source) : normalizeStatus(stamped);
    // The SQUARE shows the day's WORST, not its last run. preflight.record_run
    // writes `worst`/`worstAt` for exactly this reason ("an intermittent
    // failure at 09:05 must not be erased by a clean re-run at 16:00"), and
    // `overall` on the archived document is the latest run - so reading
    // `overall` threw the day's memory away and rendered a bad morning green.
    const worstStamped = source.worst ?? source.worstStatus ?? source.worst_status;
    const status = worstStamped == null ? latestStatus : normalizeStatus(worstStamped);
    const worstAt = firstString(source, ["worstAt", "worst_at"]);
    const dayGotBetter = TONE_RANK[status] < TONE_RANK[latestStatus];
    // passed and total are taken as a PAIR from one source. Mixing them - a
    // counter numerator over a checks-array denominator - is how you end up
    // rendering "12 of 3 passing", which is worse than showing nothing.
    const totals = counterTotals(source);
    const declaredTotal = Number(source.total ?? source.totalChecks);
    const declaredPassed = Number(source.passed ?? source.pass ?? source.passCount);
    let passed = null;
    let total = null;
    if (Number.isFinite(declaredPassed) && Number.isFinite(declaredTotal)) {
      passed = declaredPassed;
      total = declaredTotal;
    } else if (totals.total != null) {
      passed = totals.passed;
      total = totals.total;
    } else if (Number.isFinite(declaredPassed) && Array.isArray(source.checks)) {
      passed = declaredPassed;
      total = source.checks.length;
    }
    const counted = Number.isFinite(passed) && Number.isFinite(total) ? `${passed} of ${total} passing` : "";
    const summaryText = firstString(source, ["summary", "headline", "detail", "message"]);
    // When the day ENDED better than it was, both facts are printed. Showing
    // only the worst would hide that it was fixed; showing only the last run
    // is the bug this replaces.
    const summary = dayGotBetter
      ? [
          `worst ${statusLabel(status)}${worstAt ? ` at ${timeOfDay(worstAt)}` : ""}`,
          `last run ${statusLabel(latestStatus)}${counted ? ` ${counted}` : ""}`,
        ].filter(Boolean).join(" · ")
      : [statusLabel(status), counted, summaryText].filter(Boolean).join(" · ");
    strip.push({
      date: iso,
      label: dayStripLabel(iso),
      status,
      latestStatus,
      worstAt,
      recovered: dayGotBetter,
      toneClass: statusToneClass(status),
      missing: false,
      passed: Number.isFinite(passed) ? passed : null,
      total: Number.isFinite(total) ? total : null,
      title: `${dayStripLabel(iso)} - ${summary}`,
      summary,
    });
  }
  return strip;
}

/** "12 of the last 30 days ran, 2 failing" - a number for the strip too. */
export function summarizeDayStrip(strip) {
  const days = Array.isArray(strip) ? strip : [];
  const ran = days.filter((day) => !day.missing);
  const failing = ran.filter((day) => day.status === "fail").length;
  const recovered = ran.filter((day) => day.recovered).length;
  if (!days.length) return "No archived days yet";
  const head = `${ran.length} of the last ${days.length} days ran`;
  const parts = [];
  if (failing) parts.push(`${failing} failing`);
  if (recovered) parts.push(`${recovered} recovered during the day`);
  return parts.length ? `${head}, ${parts.join(", ")}` : head;
}

// ---------------------------------------------------------------------------
// freshness
// ---------------------------------------------------------------------------

/**
 * How old the displayed run is, as a number. Deliberately blunt: a result from
 * yesterday must not look like this morning's.
 */
export function formatRunAge(value, nowMs) {
  const parsed = value ? Date.parse(value) : NaN;
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  if (!Number.isFinite(parsed)) return "run time unknown";
  const deltaMinutes = Math.floor((now - parsed) / 60000);
  if (deltaMinutes < 0) return "just now";
  if (deltaMinutes < 1) return "just now";
  if (deltaMinutes < 60) return `${deltaMinutes} min ago`;
  const hours = Math.floor(deltaMinutes / 60);
  if (hours < 24) return `${hours} h ${deltaMinutes % 60} min ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** True when the run on screen is old enough that it should not be trusted. */
export function isRunStale(value, nowMs, maxAgeMinutes = 24 * 60) {
  const parsed = value ? Date.parse(value) : NaN;
  if (!Number.isFinite(parsed)) return true;
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  return now - parsed > maxAgeMinutes * 60000;
}

// ---------------------------------------------------------------------------
// payload / transport
// ---------------------------------------------------------------------------

/**
 * Unwrap whichever envelope the backend chose. POST /run may answer with the
 * run inline or nested under result/run/latest.
 */
export function unwrapRun(payload) {
  if (!payload || typeof payload !== "object") return null;
  for (const key of ["result", "run", "latest", "today", "preflight", "data"]) {
    const nested = payload[key];
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      if (firstArray(nested, ["checks", "results", "items", "rows"])) return nested;
    }
  }
  return payload;
}

export function extractHistory(payload) {
  if (!payload || typeof payload !== "object") return [];
  return (
    firstArray(payload, ["history", "days", "archive", "daily"]) ||
    firstArray(payload.result || {}, ["history", "days", "archive", "daily"]) ||
    []
  );
}

/**
 * Turn a failed fetch into something a human can act on. "404" here means the
 * backend half is not deployed yet, and saying that is far more useful than a
 * generic error - and far safer than showing nothing, which reads as fine.
 */
export function preflightErrorMessage(status, message) {
  const code = Number(status);
  if (code === 404) {
    return "The health checklist API is not deployed on this backend yet (404). Nothing has been measured.";
  }
  if (code === 401 || code === 403) {
    return "This account is not allowed to read the health checklist (admin only).";
  }
  if (code === 503) {
    return "The health checklist service is not ready yet (503). Nothing has been measured.";
  }
  const text = typeof message === "string" ? message.trim() : "";
  if (text) return text;
  if (Number.isFinite(code) && code) return `Health checklist request failed (HTTP ${code}). Nothing has been measured.`;
  return "Health checklist is unreachable. Nothing has been measured.";
}

/**
 * The whole panel's view model in one call, so App.jsx renders and does not
 * decide. `visible` encodes the "admin only, and do not even fetch" rule.
 */
export function buildPreflightView(state = {}) {
  const { payload, error, loading, running, isAdmin, nowMs } = state;
  if (!isAdmin) return { visible: false };

  const envelope = payload && typeof payload === "object" ? payload : {};
  const run = unwrapRun(payload);
  const summary = summarizeRun(run);
  // The strip anchors on the server's own ET "today" when it sends one, so the
  // newest square is the day the BACKEND is judging - not the day the
  // browser's clock happens to be in.
  const strip = orderDayStrip(extractHistory(payload), {
    today: dayOf(envelope) || summary.day || undefined,
    days: PREFLIGHT_STRIP_DAYS,
  });

  // Envelope-level facts worth saying out loud, each one an OBSERVATION:
  //  - available:false          the checklist module could not even be loaded
  //  - historyError             the archive could not be read (strip is a lie)
  //  - schedule.threadStarted   the 08:45 job is not actually running here
  const notices = [];
  if (envelope.available === false) {
    notices.push({
      key: "unavailable",
      tone: "error",
      text: `The checklist could not run: ${envelope.unavailableReason || "reason not reported"}. Nothing has been measured.`,
    });
  }
  if (envelope.historyError) {
    notices.push({
      key: "history-error",
      tone: "error",
      text: `The 30-day archive could not be read (${envelope.historyError}), so the day strip below is incomplete.`,
    });
  }
  const schedule = envelope.schedule && typeof envelope.schedule === "object" ? envelope.schedule : null;
  if (schedule && schedule.threadStarted === false) {
    notices.push({
      key: "scheduler-off",
      tone: "error",
      text: `The daily ${schedule.at || "morning"} run is NOT scheduled in this backend process, so tomorrow will have no result unless you press "Run check now".`,
    });
  }

  const ranToday = envelope.ranToday === undefined ? summary.total > 0 : Boolean(envelope.ranToday);

  return {
    visible: true,
    loading: Boolean(loading),
    running: Boolean(running),
    error: error || "",
    summary,
    checks: summary.checks,
    strip,
    stripSummary: summarizeDayStrip(strip),
    notices,
    ranToday,
    scheduleLabel: schedule && schedule.at ? `Runs automatically at ${schedule.at} on weekdays` : "",
    ageLabel: formatRunAge(summary.ranAt, nowMs),
    stale: summary.total > 0 && isRunStale(summary.ranAt, nowMs),
    // Distinct states so the UI can never show a forever-spinner or a
    // reassuring blank: loading -> error -> empty -> ready.
    phase: loading && !summary.total ? "loading" : error ? "error" : summary.total ? "ready" : "empty",
  };
}
