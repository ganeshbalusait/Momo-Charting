// "Why is this here?" in words a 10-year-old or someone who has never traded can follow (Ganesh 2026-11-20: "I need
// justification why ... chose that stocks. Build like this 10 kid can understand ... For leap oi scanner and long te
// and agx flow"). Same pattern as the Long Term page's "In simple words": a one-line verdict, tick / cross lines,
// "What this means", and "not advice". No guesswork: every line says what the data shows, never that a stock will
// go up or down.
//
// Each check is {mark, text}: mark "yes" (✓, a reason it is on the list), "no" (✗, a reason to be careful) or
// "info" (•, a plain fact).

const whole = (value) => Math.round(Number(value) || 0).toLocaleString("en-US");
const has = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));

function money(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)} billion`;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)} million`;
  if (n >= 1000) return `$${whole(n)}`;
  return `$${n.toFixed(2)}`;
}

function dayText(iso) {
  const key = String(iso || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return "the end date";
  const d = new Date(`${key}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** "about 1 in 5" from a delta (its size), or null. */
export function chanceText(delta) {
  if (!has(delta)) return null;
  const d = Math.abs(Number(delta));
  if (d <= 0) return null;
  if (d >= 0.9) return "almost certain, as things stand today";
  if (d >= 0.45) return `about ${Math.round(d * 10)} in 10`;
  return `about 1 in ${Math.max(2, Math.round(1 / d))}`;
}

const sideWord = (row) => (row?.side === "PUT" ? "Put" : "Call");
const betLine = (row) => (row?.side === "PUT"
  ? "A Put is a bet that pays if the stock goes DOWN below the strike price before the end date."
  : "A Call is a bet that pays if the stock goes UP above the strike price before the end date.");
const NOT_ADVICE = "This is information, not advice. Nobody knows which way a stock will go. Practice with paper trading first.";

/** LEAP OI Scanner row. ctx: {rank (0-based place on its list), price (current stock price, optional)}. */
export function leapWhy(row, { rank = null, price = null } = {}) {
  if (!row) return null;
  const strike = Number(row.strike);
  const kind = row.side === "PUT" ? "put" : "call";
  const verdict = rank === 0
    ? `More people hold this ${kind} than any other one on this list.`
    : Number.isInteger(rank) ? `Number ${rank + 1} on this list for how many people hold it.`
      : `One of the long bets the most people hold on ${row.ticker}.`;
  const checks = [];
  checks.push({ mark: "yes", text: `${whole(row.oi)} contracts are open. That is how many of these bets people are holding right now (each one covers 100 shares).` });
  if (has(row.oiChange) && Number(row.oiChange) !== 0) {
    const n = Number(row.oiChange);
    const since = row.oiChangeSince ? ` since ${dayText(row.oiChangeSince)}` : " since the day before";
    checks.push(n > 0
      ? { mark: "yes", text: `${whole(n)} new contracts were opened${since}: more people joined this bet.` }
      : { mark: "no", text: `${whole(-n)} contracts were closed${since}: some people left this bet.` });
  }
  const chance = chanceText(row.delta);
  if (chance) checks.push({ mark: "info", text: `Delta ${Math.abs(Number(row.delta)).toFixed(2)}: the chance it ends up worth something is ${chance}.` });
  const mid = has(row.bid) && has(row.ask) && Number(row.ask) > 0 ? (Number(row.bid) + Number(row.ask)) / 2 : null;
  const now = has(price) ? Number(price) : has(row.spot) ? Number(row.spot) : null;
  if (mid !== null && Number.isFinite(strike) && strike > 0) {
    const breakeven = row.side === "PUT" ? strike - mid : strike + mid;
    const move = now ? Math.round((breakeven / now - 1) * 1000) / 10 : null;
    const where = row.side === "PUT" ? "below" : "above";
    const moveText = move === null ? "" : ` (${move > 0 ? "up" : "down"} ${Math.abs(move).toFixed(1)}% from ${now.toFixed(2)} today)`;
    checks.push({ mark: "info", text: `To make money by ${dayText(row.expiry)}, the stock has to be ${where} ${breakeven.toFixed(2)}${moveText}. One contract costs about $${whole(mid * 100)}.` });
  }
  if (row.entry) checks.push({ mark: "yes", text: "ENTRY: our option chain marks this as the usual starting choice for that date." });
  const flow = row.flow;
  if (flow && Number(flow.volume) > 0) {
    const word = flow.fill === "ask" ? "the last trade looked like a buyer (it paid the asking price)"
      : flow.fill === "bid" ? "the last trade looked like a seller (it took the bid price)" : "we can't tell if buyers or sellers led";
    checks.push({ mark: "info", text: `Today ${whole(flow.volume)} traded, about ${money(flow.premium)}; ${word}.` });
  } else {
    checks.push({ mark: "info", text: "No trades on this contract seen today yet." });
  }
  return {
    verdict,
    checks,
    meaning: `${betLine(row)} Many people holding it shows where they are betting, not who will be right. Our test of 15,896 cases found big open interest guessed the direction no better than a coin flip.`,
    notAdvice: NOT_ADVICE,
  };
}

/** AGX Flow row (one contract that traded today). */
export function flowWhy(row) {
  if (!row) return null;
  const volume = Number(row.volume) || 0;
  const oi = Number(row.oi) || 0;
  const unusual = volume >= 500 && volume > oi;
  const verdict = `About ${money(row.premium)} was spent on this ${sideWord(row)} today${unusual ? ", far more than usual" : ""}.`;
  const checks = [];
  checks.push({ mark: "yes", text: `${money(row.premium)} spent today: ${whole(volume)} contracts, each covering 100 shares.` });
  if (oi > 0) {
    const times = volume / oi;
    checks.push(unusual
      ? { mark: "yes", text: `${whole(volume)} traded today, but only ${whole(oi)} were open before: ${times >= 10 ? Math.round(times) : times.toFixed(1)} times more. That is unusual.` }
      : { mark: "no", text: `${whole(volume)} traded today and ${whole(oi)} were already open, so this is not unusual.` });
  } else if (volume > 0) {
    checks.push({ mark: "yes", text: `${whole(volume)} traded today and none were open before: all new.` });
  }
  checks.push(row.fill === "ask" ? { mark: "info", text: "The last trade paid the asking price, so a buyer was probably eager (Bought). This is a guess." }
    : row.fill === "bid" ? { mark: "info", text: "The last trade took the bid price, so a seller was probably eager (Sold). This is a guess." }
      : { mark: "info", text: "The last trade was between the bid and the ask, so we can't tell if a buyer or a seller led." });
  if (has(row.newVolume) && Number(row.newVolume) > 0) {
    checks.push({ mark: "yes", text: `${whole(row.newVolume)} more traded in the last few minutes.` });
  }
  const days = Number(row.days);
  if (Number.isFinite(days)) {
    checks.push(days <= 7
      ? { mark: "no", text: `It ends on ${dayText(row.expiry)}, in ${days} day${days === 1 ? "" : "s"}. Short bets can lose their value fast.` }
      : { mark: "info", text: `It ends on ${dayText(row.expiry)}, in ${days} days.` });
  }
  if (has(row.delta)) {
    const d = Math.abs(Number(row.delta));
    checks.push(d >= 0.9
      ? { mark: "info", text: `Delta ${d.toFixed(2)}: this option already moves almost like the stock itself, so it may be insurance or a way to hold the stock, not a bet on a big move.` }
      : { mark: "info", text: `Delta ${d.toFixed(2)}: the chance it ends up worth something is ${chanceText(d)}.` });
  }
  return {
    verdict,
    checks,
    meaning: `${betLine(row)} Big money on one side does not prove the stock will move that way: it can be insurance (a hedge) for shares someone already owns, or one half of a bigger trade.`,
    notAdvice: NOT_ADVICE,
  };
}

export const MARKS = { yes: "✓", no: "✗", info: "•" };
