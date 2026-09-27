// Strike reach: how far a strike sits from the price, measured in the
// options market's own expected move for that expiry.
//
// Why this exists (2026-09-22): six far-out weekly calls lost - SHOP 160C Thu
// with SHOP at $146.43, GOOGL 370C Wed with GOOGL at $361.17. Measured with
// LIVE prices each strike needed 1.3-1.7x the expected move by expiry. This
// puts that number on the ticker card BEFORE the trade.
//
// Pure functions only - no fetching, no React - so the arithmetic is tested
// on its own. "Expected move" is whatever the chain payload's
// expiryExpectedMoves says (ATM-straddle based); nothing here re-derives it.

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_WORDS = {
  sun: 0, sunday: 0,
  mon: 1, monday: 1,
  tue: 2, tues: 2, tuesday: 2,
  wed: 3, weds: 3, wednesday: 3,
  thu: 4, thur: 4, thurs: 4, thursday: 4,
  fri: 5, friday: 5,
  sat: 6, saturday: 6,
};

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function isoParts(iso) {
  const match = ISO_DATE.exec(String(iso || "").slice(0, 10));
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  // Weekday from the calendar date itself (UTC), never from the browser's
  // time zone - "2026-09-25" is a Friday wherever the laptop is.
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  if (!Number.isFinite(weekday)) return null;
  return { year, month, day, weekday };
}

/** "2026-09-25" -> "Fri 9/25"; "" for anything that is not a date. */
export function expiryLabel(iso) {
  const parts = isoParts(iso);
  return parts ? WEEKDAYS[parts.weekday] + " " + parts.month + "/" + parts.day : "";
}

function positive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : null;
}

/**
 * Listed expiries on/after today, sorted, de-duplicated.
 *
 * `nowEtMinutes` (minutes since ET midnight, 0-1439) is optional. When given
 * and it is 16:00 ET or later, today's OWN expiry is dropped: the market is
 * closed and that contract has already expired, so it must not still be
 * offered as a row or as the default for a bare "160C" typed after the
 * close on an expiry day. Omit it and today's expiry is kept (used by
 * callers that only have a date, not a time).
 */
export function upcomingExpiries(expiries, todayIso, nowEtMinutes) {
  const today = String(todayIso || "").slice(0, 10);
  const closedToday =
    Number.isFinite(nowEtMinutes) && nowEtMinutes >= 16 * 60;
  const seen = new Set();
  for (const raw of Array.isArray(expiries) ? expiries : []) {
    const iso = String(raw || "").slice(0, 10);
    if (!isoParts(iso)) continue;
    if (today && iso < today) continue;
    if (today && closedToday && iso === today) continue;
    seen.add(iso);
  }
  return [...seen].sort();
}

/**
 * The next `count` expiries with their 1x/2x reach levels.
 * expectedMoves: { "YYYY-MM-DD": dollars }. Expiries whose move is missing,
 * zero, negative or NaN are skipped rather than shown as "±$0.00".
 */
export function reachRows(expectedMoves, spot, todayIso, count = 3, nowEtMinutes) {
  const price = positive(spot);
  if (!price || !expectedMoves || typeof expectedMoves !== "object") return [];
  const rows = [];
  for (const expiry of upcomingExpiries(Object.keys(expectedMoves), todayIso, nowEtMinutes)) {
    const em = positive(expectedMoves[expiry]);
    if (!em) continue;
    rows.push({
      expiry,
      label: expiryLabel(expiry),
      em,
      emPct: (em / price) * 100,
      call1x: price + em,
      call2x: price + 2 * em,
      put1x: price - em,
      put2x: price - 2 * em,
    });
    if (rows.length >= count) break;
  }
  return rows;
}

function resolveExpiryToken(token, expiries) {
  const word = WEEKDAY_WORDS[token];
  if (word !== undefined) {
    return expiries.find((iso) => isoParts(iso)?.weekday === word) || undefined;
  }
  const iso = ISO_DATE.exec(token);
  if (iso) return expiries.includes(token) ? token : undefined;
  const md = /^(\d{1,2})\/(\d{1,2})(?:\/(\d{2}|\d{4}))?$/.exec(token);
  if (md) {
    const month = Number(md[1]);
    const day = Number(md[2]);
    const yearToken = md[3];
    return (
      expiries.find((value) => {
        const parts = isoParts(value);
        if (!parts || parts.month !== month || parts.day !== day) return false;
        if (yearToken === undefined) return true;
        // "9/25/27" must not match a 2026 expiry just because the month and
        // day line up - a typed year is only a match when it IS that year.
        const typedYear = yearToken.length === 2 ? 2000 + Number(yearToken) : Number(yearToken);
        return typedYear === parts.year;
      }) || undefined
    );
  }
  return null; // not an expiry-looking token at all
}

const SIDE_WORD = /^(c|calls?|p|puts?)$/;

/**
 * "160C Thu" / "160c" / "95P 9/25" / "370 C Wed" / "160" / "SHOP 160C Fri" /
 * "Fri 160C" -> {strike, side, expiry}. Side defaults to C; expiry defaults
 * to the nearest of `preferredExpiries` (falling back to `expiries`) - pass
 * them sorted, soonest first. Any word we do not understand gives null -
 * better "cannot read that" than a wrong answer. A date/weekday that reads
 * fine but is not a listed expiry gives expiry: null plus `unmatchedExpiry`
 * (the token) - never a silent swap to a different expiry.
 *
 * A single LEADING token that is not the strike is tolerated: a weekday
 * ("Fri 160C", matching the trailing form's own rules) or a bare 1-5 letter
 * word ("SHOP 160C Fri") read as a pasted-in ticker and dropped - a trader
 * copying a contract off another screen should not get "can't read that".
 * A trailing unknown word is still unreadable; only the front gets this.
 */
export function parseStrikeQuery(text, expiries, preferredExpiries) {
  if (typeof text !== "string") return null;
  const list = (Array.isArray(expiries) ? expiries : [])
    .map((value) => String(value || "").slice(0, 10))
    .filter((value) => isoParts(value));
  const preferredList = (Array.isArray(preferredExpiries) ? preferredExpiries : list)
    .map((value) => String(value || "").slice(0, 10))
    .filter((value) => isoParts(value));
  const cleaned = text
    .trim()
    .toLowerCase()
    .replace(/,/g, " ")
    .replace(/\$/g, "")
    // "160C" -> "160 c", "95p" -> "95 p", wherever it lands in the string -
    // a leading symbol or weekday can now come before the strike.
    .replace(/(\d+(?:\.\d+)?)(calls?|puts?|c|p)\b/g, "$1 $2");
  if (!cleaned) return null;
  let tokens = cleaned.split(/\s+/).filter(Boolean);
  if (!tokens.length) return null;

  let expiry = null;
  let unmatched = null;

  if (tokens.length > 1 && !/^\d/.test(tokens[0])) {
    const head = tokens[0];
    if (WEEKDAY_WORDS[head] !== undefined) {
      const resolved = resolveExpiryToken(head, list);
      if (resolved === undefined) unmatched = head;
      else expiry = resolved;
      tokens = tokens.slice(1);
    } else if (/^[a-z]{1,5}$/.test(head) && !SIDE_WORD.test(head)) {
      tokens = tokens.slice(1); // a pasted ticker symbol - ignore it
    } else {
      return null;
    }
  }

  const strike = positive(/^\d+(?:\.\d+)?$/.test(tokens[0]) ? tokens[0] : NaN);
  if (!strike) return null;

  let side = null;
  for (const token of tokens.slice(1)) {
    if (/^(c|calls?)$/.test(token)) {
      if (side && side !== "C") return null;
      side = "C";
      continue;
    }
    if (/^(p|puts?)$/.test(token)) {
      if (side && side !== "P") return null;
      side = "P";
      continue;
    }
    const resolved = resolveExpiryToken(token, list);
    if (resolved === null || expiry || unmatched) return null; // unknown word, or two dates
    if (resolved === undefined) {
      // Reads as a date but this symbol lists no such expiry ("Thu" on a
      // Friday-weekly name). Kept distinct so the card can say so instead of
      // silently answering for a different expiry.
      unmatched = token;
      continue;
    }
    expiry = resolved;
  }
  if (unmatched) return { strike, side: side || "C", expiry: null, unmatchedExpiry: unmatched };
  return { strike, side: side || "C", expiry: expiry || preferredList[0] || list[0] || null };
}

/**
 * How far the price must travel to reach the strike, in expected moves.
 * need > 0 = move still needed toward the strike; need <= 0 = already ITM.
 */
export function strikeReach(input) {
  const { strike, side, spot, em } = input && typeof input === "object" ? input : {};
  const k = positive(strike);
  const price = positive(spot);
  const move = positive(em);
  const s = String(side || "").toUpperCase();
  if (!k || !price || !move || (s !== "C" && s !== "P")) return null;
  const need = s === "C" ? k - price : price - k;
  // Round once, band on the rounded value: 155 vs spot 147.60 with a 7.40
  // move is "exactly 1x" to a trader, but the raw float lands at
  // 1.0000000000000007 and would band as "stretch" - a false amber on a row
  // that reads as green. Whatever is displayed must be this same number.
  const multiple = Math.round((need / move) * 100) / 100;
  let band;
  if (need <= 0) band = "itm";
  else if (multiple <= 1) band = "inside";
  else if (multiple <= 2) band = "stretch";
  else band = "far";
  return { need, needPct: (need / price) * 100, multiple, band };
}

/**
 * The nearest high-OI wall each side of the price, measured in expected moves.
 *
 * Asked for 2026-09-22 off INOD: "High OI 75 ... that is the target". A wall
 * is where the open interest actually sits, so the question "is my strike
 * reachable" and the question "where is the crowd" belong on the same line.
 *
 * `walls` is the card's OWN High-OI model (buildHighOiContractList): its
 * `calls` are already the strikes above spot and its `puts` those at or below,
 * both sorted by strike descending. Nearest therefore means the LAST call and
 * the FIRST put - no re-ranking here, so the line can never disagree with the
 * walls drawn right above it.
 *
 * The multiple uses the wall's OWN expiry's expected move when the chain has
 * one. A monthly wall (INOD's 75 is Oct 16) measured against this Friday's
 * move would overstate how far it is, so when only another expiry has a move
 * the result says which one it borrowed (`emFromOtherExpiry` + `emLabel`)
 * rather than quietly mixing the two.
 */
export function wallReach(input) {
  const { walls, spot, expectedMoves, todayIso, nowEtMinutes } =
    input && typeof input === "object" ? input : {};
  const price = positive(spot);
  if (!price || !walls || typeof walls !== "object") return null;
  const moves = expectedMoves && typeof expectedMoves === "object" ? expectedMoves : {};
  const fallbackExpiry = upcomingExpiries(Object.keys(moves), todayIso, nowEtMinutes)[0] || null;

  const pick = (items, side) => {
    const list = (Array.isArray(items) ? items : []).filter((item) => positive(item?.strike));
    // The builder's own side rule: a call wall sits above spot, a put wall at
    // or below it (a strike being stood on is support).
    const near = side === "C"
      ? list.filter((item) => Number(item.strike) > price).sort((a, b) => a.strike - b.strike)
      : list.filter((item) => Number(item.strike) <= price).sort((a, b) => b.strike - a.strike);
    const wall = near[0];
    if (!wall) return null;
    const expiry = String(wall.expiry || "").slice(0, 10);
    const own = positive(moves[expiry]);
    const em = own || (fallbackExpiry ? positive(moves[fallbackExpiry]) : null);
    const emExpiry = own ? expiry : (em ? fallbackExpiry : null);
    const reach = em ? strikeReach({ strike: wall.strike, side, spot: price, em }) : null;
    return {
      side,
      strike: Number(wall.strike),
      openInterest: Math.max(0, Number(wall.openInterest) || 0),
      expiry,
      label: expiryLabel(expiry),
      em,
      emExpiry,
      emLabel: expiryLabel(emExpiry),
      emFromOtherExpiry: Boolean(em && !own),
      need: reach ? reach.need : null,
      needPct: reach ? reach.needPct : null,
      multiple: reach ? reach.multiple : null,
      band: reach ? reach.band : null,
    };
  };

  const call = pick(walls.calls, "C");
  const put = pick(walls.puts, "P");
  return call || put ? { call, put } : null;
}

/** Today's date in New York as "YYYY-MM-DD" - expiries are exchange dates.
 *
 *  Built from formatToParts' own year/month/day fields, not an "en-CA"
 *  formatted string - the locale's separator and field order are an
 *  implementation detail we do not want this date to depend on.
 */
export function todayInNewYork(nowMs = Date.now()) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date(nowMs));
    const get = (type) => parts.find((p) => p.type === type)?.value;
    const year = get("year");
    const month = get("month");
    const day = get("day");
    if (!year || !month || !day) throw new Error("missing date part");
    return year + "-" + month + "-" + day;
  } catch {
    return new Date(nowMs).toISOString().slice(0, 10);
  }
}

/** Minutes since ET midnight (0-1439), for the 16:00 expiry-day cutoff. */
export function etMinutesSinceMidnight(nowMs = Date.now()) {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(nowMs));
    const hour = Number(parts.find((p) => p.type === "hour")?.value);
    const minute = Number(parts.find((p) => p.type === "minute")?.value);
    if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null;
    return hour * 60 + minute;
  } catch {
    return null;
  }
}
