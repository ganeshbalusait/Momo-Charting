// Moved out of MomxScannerPanel.jsx (2026-10-03) so `node --test` can import it - the
// panel re-exports it. Behaviour unchanged.
import { tapeState } from "./marketSession.js";

// "Data as of ..." note - now a THREE-way verdict, not an age threshold.
//
// It used to say "newest bar over 30 minutes old = stale, paint it amber",
// which is true every evening and all weekend. On Saturday 2026-09-05 the
// trader read the resulting amber "DATA AS OF FRI 8:00 PM ET" chip and asked
// "it's scanner is not running?" - it was: verified live, the worker rebuilt
// and wrote its board cache at 13:29 ET while the payload reported generatedAt
// Sat 13:28 ET, tapeAsOf Fri 20:00 ET and zero errors. Friday 20:00 ET is
// simply the last bar of the week.
//
// A warning that fires on the normal case is not a warning. marketSession.js
// answers the question that actually matters - has the market had a CHANCE to
// produce a newer bar - so the chip can be quiet when the market is shut and
// still shout when the tape has genuinely stalled, including the case a plain
// age test misses (a tape stuck since Monday is only ~26h old on Tuesday
// night, yet a whole session behind).

export function tapeAsOfNote(iso, nowMs) {
  // ALWAYS returns a label when the stamp is readable - {text, tone, stale}.
  // It used to return null while the tape was fresh, so the note vanished the
  // moment things were healthy and the trader could not tell WHEN the data ran
  // ("how i know when the data ran?", 2026-08-31). UPDATED already says when
  // the SCAN built; this says how old the BARS are, which is the number that
  // decides whether a signal is actionable.
  //
  // `stale` is kept as a boolean because callers style on it, and it now means
  // "there is something wrong" rather than "the market is shut".
  if (!iso) return null;
  const stamp = new Date(iso);
  if (Number.isNaN(stamp.getTime())) return null;
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  const verdict = tapeState(stamp.getTime(), now);
  if (!verdict) return null;

  const clockOf = (options) => {
    try {
      return stamp.toLocaleString("en-US", { timeZone: "America/New_York", ...options });
    } catch {
      return null;
    }
  };
  const dayClock = clockOf({
    weekday: "short", hour: "numeric", minute: "2-digit", hour12: true,
  });

  if (verdict.state === "stale") {
    return {
      tone: "stale",
      stale: true,
      text: dayClock ? `Tape stale - last bar ${dayClock} ET` : "Tape stale",
    };
  }
  if (verdict.state === "closed") {
    // NEUTRAL. The market being shut is not a fault, and the header already
    // carries a MARKET CLOSED pill - this only adds which bar we stopped at.
    return {
      tone: "closed",
      stale: false,
      text: dayClock ? `Last bar ${dayClock} ET` : "Last bar of the session",
    };
  }
  const minutes = Math.floor(Math.max(0, verdict.ageMs) / 60000);
  const clock = clockOf({ hour: "2-digit", minute: "2-digit", hour12: false });
  return {
    tone: "live",
    stale: false,
    // "Data 11:05 ET (3m)" - the timestamp AND the age, because a stamp alone
    // still makes you do arithmetic against the wall clock.
    text: clock ? `Data ${clock} ET (${minutes}m)` : "Data is current",
  };
}
