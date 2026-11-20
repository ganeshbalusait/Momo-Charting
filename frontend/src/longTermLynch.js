// Peter view on the Long Term page (2026-11-19, Ganesh: "look only quality
// stocks, add one more next to MOS like Peter then show me his star sheet and
// tickers"). Peter Lynch's growth-at-a-reasonable-price screen, worked out from
// the rows the page already has (no extra request). Pure functions, no React.
//
// Who gets a sheet: quality companies only (the page's HIGH quality mark:
// return on capital 20%+), with a live price, a profit, at least 8 years of
// filings and earnings that grew. Everything else is left off the list.
//
// The five stars:
//   growth  earnings grew 10-50% a year (above 50% Lynch did not trust to last)
//   sales   sales grew 5%+ a year, so the earnings growth is not only cost cuts
//   debt    debt is at most 0.8x equity
//   cash    free cash flow is growing
//   price   PEG is 1 or less. PEG = P/E / (growth % + dividend yield %), Lynch's
//           own formula: the dividend counts as part of the return.
// Inventory turning over 10%+ slower than a year ago (it grew faster than
// sales) is a warning line on the card; it does not change the stars.
// A bank is not judged on debt or cash flow; those two are left out for it.

//
// Cyclicals (same day, "add a cyclical warning label"): the server marks a row
// with its cyclical industry (chips, oil & gas, metals & mining, autos...). A
// cyclical's P/E is lowest when its earnings are at the top of the cycle, so it
// is never called CHEAP: it reads CYCLICAL and its price star is left out.

export const LYNCH_TILES = ["CHEAP", "FAIR", "PRICEY", "CYCLICAL"];
// What the tag reads as on screen. Peter called P/E = growth the FAIR price, so a score between
// 1 and 2 is ABOVE his fair price (the key stays FAIR in the code and the tests).
export const LYNCH_VERDICT_LABEL = { CHEAP: "CHEAP", FAIR: "ABOVE FAIR", PRICEY: "PRICEY", CYCLICAL: "CYCLICAL" };
export const LYNCH_TILES_NOTE = "These four describe the price only. CHEAP: at or under Peter's fair price for the growth (score 1 or less). ABOVE FAIR: over it, up to twice (1 to 2). PRICEY: more than twice, the level he called very negative. CYCLICAL: a good-years-and-bad-years business (chips, oil, metals, cars, chemicals, airlines), never called cheap. A CHEAP company can still fail another check, so read \"Would Peter buy it?\" on the card. Tap a tile to show only those; tap again for all.";
// Said on the page so the screen's own choices are not passed off as his.
export const LYNCH_OWN_CHOICES = "This page's own choices, not Peter's: the 10% to 50% growth band, counting growth above 50% as 50, and the limits of the sales, borrowing and cash checks. His favourite companies grew 20% to 25% a year and he was wary of faster growth. He wrote rules of thumb, never one formula: the fair price in dollars is his \"fairly priced\" test (price-to-profit equal to growth) worked out for today's numbers.";
export const LYNCH_RULES = Object.freeze({
  growthMin: 10, growthMax: 50, salesMin: 5, debtMax: 0.8, pegCheap: 1, pegFair: 2, stalwartFrom: 10, fastFrom: 20, inventorySlow: 0.1,
});

// Back-test run 2026-11-19 (artifacts/backtest_peter_lynch.py -> peter_lynch_backtest.json):
// 98 of today's companies, 452 yearly picks from 2010, entered 90 days after each fiscal year
// end, PEG without the dividend. Shown on the page so the tag is not read as a buy signal.
export const LYNCH_BACKTEST_NOTE = "Back-test 2010–2026 (98 of today's companies, 452 yearly picks): the CHEAP tag did NOT beat the quality group as a whole. Up after 1 year: CHEAP 72% vs all quality 74% in 2010–18, and 59% vs 66% in 2019–26; after 3 years 82% vs 85%, and 73% vs 82%. Quality itself did the work. Read CHEAP as the price you pay, not a buy signal. Limits: today's survivors only, yearly data. Not advice.";

const SCORED = new Set(["BUY", "WAIT", "AVOID"]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
// A dollar figure as shown ($377, $6.30) and as a number (377, 6.3): whole dollars from $10 up.
const priced = (v) => (v >= 10 ? Math.round(v) : Math.round(v * 100) / 100);
const money = (v) => (v >= 10 ? `$${Math.round(v).toLocaleString("en-US")}` : `$${v.toFixed(2)}`);
// A buy LIMIT is rounded down, never up (a limit rounded up is past the real one): whole dollars
// from $100, cents below. Today's price is shown to the cent, as in the card's header.
const limited = (v) => (v >= 100 ? Math.floor(v + 1e-9) : Math.floor(v * 100 + 1e-9) / 100);
const limit = (v) => (v >= 100 ? `$${limited(v).toLocaleString("en-US")}` : `$${limited(v).toFixed(2)}`);
const cents = (v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const pct = (v) => (isNum(v) ? Math.round(v * 1000) / 10 : null);
// A number next to a check: whole, unless rounding would put it on the other side of one of the
// check's limits (9.96 must not print "10" beside a failed "10 or more"): then one decimal.
// Shared by the card and the simple-words box so both always print the same figure.
export function nearLimit(v, ...limits) {
  const r = Math.round(v);
  const flips = limits.some((limit) => (v < limit) !== (r < limit) || (v > limit) !== (r > limit));
  return flips ? Math.abs(v).toFixed(1) : String(Math.abs(r));
}
const beside = (v, ...limits) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${nearLimit(v, ...limits)}%`;

// Groups (same day, "add mag7 tickers and watchlist tickers separate also"):
// Quality = every quality company (the list as first built); Mag7 = every Mag7
// company, quality or not, so none of them is missing (a non-quality one is
// marked, one that cannot be scored says why); Watchlist = the quality
// companies on his Watchlist. The server sends who is on each list.
// Best (2026-11-20, "Confused here which one buy?" then "Peter one don't mixed with Warren") =
// Peter's own top marks: every judged star earned and the price CHEAP. Nothing in the Peter view
// reads Warren's (Rule #1) verdict. The view opens on Best when it has a company.
export const LYNCH_SCOPES = ["Best", "Quality", "Mag7", "Watchlist"];
export const LYNCH_SCOPE_LABEL = { Best: "Peter would buy", Quality: "Quality", Mag7: "Mag7", Watchlist: "Watchlist" };

export function defaultLynchScope(rows) {
  return lynchRows(rows, { scope: "Best" }).length ? "Best" : "Quality";
}

// Why a company has no star sheet (null when it can have one). Quality is not
// judged here: it is a gate of the Quality and Watchlist groups only.
export function lynchMissing(row) {
  if (!row) return "there is no information on it";
  if (!SCORED.has(row.signal)) {
    if (row.signal === "ETF") return "this is a fund (a basket of many companies), not one company";
    if (row.signal === "NO_PRICE") return "there is no fresh price for it right now";
    if (row.signal === "STALE") return "its company reports are out of date";
    if (row.signal === "SHORT_HISTORY") return "it has too few years of company reports";
    return "its company reports could not be read";
  }
  if (!isNum(row.pe) || row.pe <= 0) return "it made no profit, so its price cannot be compared with its profit";
  const growth = pct(row.epsGrowth);
  if (growth == null) return "its profit growth cannot be measured: it lost money at the start of the ten years, or a year is missing";
  if (growth <= 0) return "its profit did not grow over the ten years";
  return null;
}

// "Would Peter buy it?" (2026-11-20, Ganesh: "if Peter was there did he buy it or not? Simple
// question ... Think like you are Peter"). One answer from his own checks, never Warren's:
//   YES      every judged check passes, the price is at or under his fair price for the growth
//            (score 1 or less), and no unsold goods are piling up
//   WAIT     the business passes, the price does not
//   NO       a business check fails (growth, sales, borrowing, cash) or unsold goods are piling up
//   CAREFUL  a cyclical: he bought those when the bad years were ending, never because the price
//            looked low (Beating the Street ch. 15; Worth, Sept 1993)
// This is the page's reading of his published rules of thumb, not a forecast. He never published
// a single yes / no formula.
const BUY_NO = {
  sales: "Its sales are barely growing.",
  debt: "It owes too much.",
  cash: "Its spare cash is shrinking.",
};
const lower = (s) => `${s.charAt(0).toLowerCase()}${s.slice(1)}`;

function wouldBuy({ checks, verdict, fair, growth, cyclical, note, inventoryWarning, lowQuality, foreign, priced: hasPrice }) {
  const failed = checks.find((c) => c.key !== "price" && c.passed === false);
  const failedWhy = !failed ? null
    : failed.key === "growth"
      ? (growth > LYNCH_RULES.growthMax ? "Its profit grew faster than this page's 50% limit; Peter was wary of growth that fast." : "Its profit grows too slowly.")
      : BUY_NO[failed.key];
  // Only quality companies are scored (his "look only quality stocks"). The Mag7 group also shows
  // the others, and for those the page gives no buy answer and no price (audit 11-20: AMZN), also
  // when the company is a cyclical (review 11-20).
  if (lowQuality) {
    return {
      answer: "OFFLIST", price: null, failedWhy,
      why: `${note ? `${note} ` : ""}This page only scores quality companies: ones that earn $20 or more a year on every $100 put in. This one earns less, so it is off the quality list and the checks below are for reading only.`,
    };
  }
  if (cyclical) {
    // the explanation of the word comes first, right under the answer; a failed check or his own
    // sell sign is a NO for a cyclical as for anyone (review 11-20)
    if (failedWhy) return { answer: "NO", why: `${note} It also fails a check: ${lower(failedWhy)}`, price: null, failedWhy };
    if (inventoryWarning) {
      return { answer: "NO", why: `${note} Unsold goods are piling up, which Peter read as a sign to sell this kind of business, not to buy.`, price: null, failedWhy: null };
    }
    return { answer: "CAREFUL", why: note, price: null, failedWhy: null };
  }
  if (failedWhy) return { answer: "NO", why: failedWhy, price: null, failedWhy };
  if (inventoryWarning) return { answer: "NO", why: "Unsold goods are piling up faster than it sells.", price: null, failedWhy: null };
  // no YES without a price the page could judge (review 11-20: a HKD row or no fresh price)
  if (foreign) return { answer: "CANNOT", why: `A good business, but its profit is reported in ${foreign}, so the page cannot judge its price in dollars.`, price: null, failedWhy: null };
  if (!hasPrice) return { answer: "CANNOT", why: "A good business, but there is no fresh price right now to judge.", price: null, failedWhy: null };
  if (verdict === "CHEAP") return { answer: "YES", why: "Every check passes and the price is at or under his fair price for its growth.", price: null, failedWhy: null };
  const base = "A good business, but the price is above his fair price for its growth.";
  return { answer: "WAIT", why: `${base} His fair price works out to ${limit(fair)}.`, price: limited(fair), failedWhy: null };
}

// "Which price would Peter buy at, and where is the exit?" (2026-11-20, Ganesh: "I need Peter
// which prize he will buy and I don't see exit also"). On every card. Checked the same day against
// what he wrote (One Up on Wall Street ch. 7, 13, 16, 17; his Worth columns of 1993 and 1996) and
// against an edge-case audit of this code:
//   Buy price  He wrote that a fairly priced company's P/E equals its earnings growth rate, that
//              half the growth rate is very positive and twice is very negative. So the most he
//              would pay is his FAIR price: the price where P/E = growth % + dividend %. The
//              dividend is dollars a share, so its % changes with the price: the fair price is
//              the root of P^2 - E*g*P - 100*E*D = 0 (E profit a share, D dividend a share).
//              He published the yardstick, never a dollar formula: the dollars are this page's
//              arithmetic at this year's profit. A limit is rounded DOWN, never up.
//              None for a company that fails a check, none for one off the quality list, none for
//              a cyclical (he gave a timing rule for those; a low P/E on a cyclical is his
//              warning), and none when the profit is not reported in dollars.
//   Exit       He had no set selling price as a general rule. A fast grower: kept while earnings
//              and expansion go on; twice his fair price is his "very negative" level (a
//              yardstick, not an order to sell). A steady grower (his "stalwart"): he generally
//              bought them for a 30% to 50% gain on the price he paid, so the dollars are counted
//              from today's price when he would buy today. When he would not buy today the card
//              says so first, and says when today's price is already past the level.
//              A cyclical: toward the end of the cycle; inventories building is his sell signal,
//              and the page says when it cannot check that.
const CYCLICAL_ONE = {
  chips: "a chip maker",
  "oil & gas": "an oil and gas company",
  "metals & mining": "a mining or metals company",
  autos: "a car maker",
  "heavy machinery": "a heavy machinery maker",
  "airlines & cruise lines": "an airline or cruise line",
  homebuilders: "a homebuilder",
  chemicals: "a chemical maker",
  tires: "a tire maker",
};

// "Nvda what is cyclical?": two sentences, one vocabulary (good years / bad years), no forecast.
// The mark is the page's, from the company's industry code; his own test was the earnings line.
function cyclicalNote(cyclical) {
  if (!cyclical) return null;
  const one = CYCLICAL_ONE[cyclical] || "a company like this";
  return `Cyclical means good years and bad years take turns: ${one} earns a lot in a good year and far less in a bad one. In a good year its profit is so big that the price looks low next to it, so here a low price is not a reason to buy.`;
}

function cyclicalKind(cyclical) {
  if (!cyclical) return null;
  const one = (CYCLICAL_ONE[cyclical] || cyclical).replace(/^an? /, "");
  return `${one.charAt(0).toUpperCase()}${one.slice(1)}`;
}

const KEEP_FAST = "He had no set selling price. He kept a fast grower while its profit kept growing, and thought about selling when the growth slowed.";
const SELL_STEADY = "He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid.";
const NOT_OWNED = "He would not own it at today's price.";

// "Fair price" is HIS word; the dollars are the page's sum (review 11-20: never "the most he would
// pay" or "his buy price", and say once on the line that he never gave a dollar price).
const SUM_NOTE = " He never gave a dollar price; this is the page's sum of his rule at this year's profit.";

function peterPlan({ buy, fair, price, grower, cyclical, inventoryWarning, inventoryKnown, bank, foreign, verdict, negative }) {
  const none = { buyPrice: null, goodPrice: null, exitLow: null, exitHigh: null, exitFar: null };
  if (buy.answer === "OFFLIST") return { ...none, buyText: "None worked out: it is not on the quality list.", exitText: null };
  if (cyclical) {
    const exitText = inventoryKnown
      ? `He had no set selling price. He sold when the good years looked close to ending; unsold goods piling up was one of his signs.${inventoryWarning ? " That sign shows in its last yearly report." : ""}`
      : "He had no set selling price. He sold when the good years looked close to ending, for example when the price of what it sells, or its orders, start to fall. This page cannot check that for you.";
    return {
      ...none,
      buyText: `No set price. He bought this kind of business when its bad years were ending and its profit was low, not because the price looked low.${buy.failedWhy ? ` It also fails a check: ${lower(buy.failedWhy)}` : ""}`,
      exitText: buy.answer === "NO" && buy.failedWhy ? null : exitText,
    };
  }
  if (buy.answer === "NO") {
    return { ...none, buyText: `None. He would not buy it while it fails a check: ${lower(buy.why.replace(/\.$/, ""))}.`, exitText: null };
  }
  const fast = grower === "Fast grower";
  if (foreign) {
    return { ...none, buyText: `No dollar price worked out: its profit is reported in ${foreign}, so its growth is not measured in dollars.`, exitText: fast ? KEEP_FAST : SELL_STEADY };
  }
  if (fair == null) {
    return { ...none, buyText: "There is no fresh price right now, so no fair price is worked out.", exitText: fast ? KEEP_FAST : SELL_STEADY };
  }
  // A limit is rounded down; if that would put it under today's price on a YES (fair price within
  // $1 above today), it is shown to the cent so the line never says less than today (review 11-20).
  const mostRaw = limited(fair);
  const most = buy.answer === "YES" && mostRaw < price ? Math.floor(fair * 100 + 1e-9) / 100 : mostRaw;
  const show = (v) => (v >= 100 && !Number.isInteger(v) ? cents(v) : limit(v));
  const half = limited(most / 2);
  // where the score reaches 2: twice the fair price without a dividend, a little less with one
  const far = dividendLevel(most, fair, negative);
  const farWords = `${money(far)}, where the price is twice his fair level for this growth,`;
  const fewer = bank ? " Debt and cash are not judged for a finance company, so this rests on fewer checks." : "";
  const prices = { ...none, buyPrice: most, goodPrice: half };
  if (buy.answer === "YES") {
    const wide = price < half;
    const buyText = `Today's ${cents(price)} passes his price test. His fair price works out to ${show(most)}; ${limit(half)} or less he called a very good price.${SUM_NOTE}${wide ? " Today is under even the very good price. A gap this wide can mean this year's profit had a one-off jump, so check that first." : ""}${fewer}`;
    if (fast) {
      return {
        ...prices, buyText, exitFar: wide ? null : Math.round(far),
        exitText: wide ? KEEP_FAST : `${KEEP_FAST} A price near ${farWords} he called very negative. That is a warning level at this year's profit, not a target.`,
      };
    }
    const low = price * 1.3;
    const high = price * 1.5;
    return {
      ...prices, buyText, exitLow: priced(low), exitHigh: priced(high),
      exitText: `${SELL_STEADY} From today's ${cents(price)} that is about ${money(low)} to ${money(high)}.${high < most ? ` His price test would still pass there (up to ${show(most)}), so he might hold longer.` : ""}`,
    };
  }
  // WAIT: he would not buy today
  const times = price / most;
  const buyText = `His fair price works out to ${limit(most)}; ${limit(half)} or less he called a very good price.${SUM_NOTE} Today's ${cents(price)} is above it${verdict === "PRICEY" ? `: ${times.toFixed(1)} times his fair price` : ""}.${fewer}`;
  if (fast) {
    return {
      ...prices, buyText, exitFar: Math.round(far),
      exitText: `${NOT_OWNED} A price near ${farWords} he called very negative${price >= far ? `; today's ${cents(price)} is already past that` : ""}.`,
    };
  }
  if (verdict === "PRICEY") return { ...prices, buyText, exitText: NOT_OWNED };
  const low = most * 1.3;
  const high = most * 1.5;
  return {
    ...prices, buyText, exitLow: priced(low), exitHigh: priced(high),
    exitText: `${NOT_OWNED} Had he bought at his fair price of ${limit(most)}, a 30% to 50% rise is about ${money(low)} to ${money(high)}${price > high ? `; today's ${cents(price)} is already past that` : ""}.`,
  };
}

// With no dividend the very-negative level is exactly twice the fair price shown (so the reader's
// own sum agrees); with one, the root where the score reaches 2 (it is a little under twice).
function dividendLevel(most, fair, negative) {
  if (negative == null || Math.abs(negative - 2 * fair) < 1e-6 * Math.max(1, fair)) return most * 2;
  return negative;
}

// The card of a company with no star sheet (2026-11-20, "Why I don't see data for tsla"): it
// still answers the question, says why in a full sentence, and says what you pay per $1 of
// yearly profit when there is a profit. null when the company can have a sheet.
export function lynchNoSheet(row) {
  const reason = lynchMissing(row);
  if (!reason) return null;
  const pe = row && isNum(row.pe) && row.pe > 0 ? row.pe : null;
  const per = pe == null ? null : pe >= 100 ? `$${Math.round(pe).toLocaleString("en-US")}` : `$${pe.toFixed(2)}`;
  return {
    answer: "CANNOT TELL",
    why: `${reason.charAt(0).toUpperCase()}${reason.slice(1)}.`,
    pays: per ? `You pay ${per} for every $1 it earns in a year.` : null,
  };
}

export function lynchSheet(row, { anyQuality = false } = {}) {
  if (!row || lynchMissing(row)) return null;
  if (!anyQuality && (row.quality !== "HIGH" || row.young)) return null;
  const growth = pct(row.epsGrowth);
  const R = LYNCH_RULES;
  const bank = !!row.financial;
  const cyclical = typeof row.cyclical === "string" && row.cyclical.trim() ? row.cyclical.trim() : null;
  // Lynch's own formula adds the dividend yield to the growth rate.
  let dividendPct = isNum(row.dividendYield) && row.dividendYield > 0 ? Math.round(row.dividendYield * 1000) / 10 : 0;
  // a payout bigger than the whole year's profit is a one-off, not a yearly return: left out
  if (dividendPct * row.pe > 100) dividendPct = 0;
  const counted = Math.min(growth, R.growthMax);
  const rawPeg = row.pe / (counted + dividendPct);
  let peg = Math.round(rawPeg * 100) / 100;
  // the shown score never sits on the wrong side of a limit it missed (1.004 must not read 1.00)
  if (rawPeg > R.pegCheap && peg <= R.pegCheap) peg = R.pegCheap + 0.01;
  if (rawPeg > R.pegFair && peg <= R.pegFair) peg = R.pegFair + 0.01;
  const pegShown = peg.toFixed(2);
  // His warning sign: inventory growing faster than sales (the turnover fell 10%+ in a year).
  const turn = isNum(row.inventoryTurnChange) ? row.inventoryTurnChange : null;
  const inventoryWarning = turn != null && turn <= -R.inventorySlow
    ? `Unsold goods are piling up: it sells through its goods ${Math.round(-turn * 100)}% slower than a year ago.`
    : null;
  const sales = pct(row.salesGrowth);
  const cash = pct(row.fcfGrowth);
  const debt = isNum(row.debtToEquity) ? row.debtToEquity : null;
  const SKIP = "finance company: this page skips this check";
  const MISSING = "number missing";
  // Plain labels and values (2026-11-20 audit: no PEG, P/E, "x equity" or "/yr" on the card).
  const checks = [
    {
      key: "growth", label: "Profit growth",
      text: growth > R.growthMax ? `${beside(growth, R.growthMax)} a year, over this page's 50% limit` : `${beside(growth, R.growthMin, R.stalwartFrom, R.fastFrom, R.growthMax)} a year`,
      passed: growth >= R.growthMin && growth <= R.growthMax,
    },
    { key: "sales", label: "Sales growth", text: sales == null ? MISSING : `${beside(sales, R.salesMin)} a year`, passed: sales != null && sales >= R.salesMin },
    bank
      ? { key: "debt", label: "Borrowed", text: SKIP, passed: null }
      : { key: "debt", label: "Borrowed", text: debt == null ? MISSING : `$${nearLimit(debt * 100, R.debtMax * 100)} per $100 of owners' money`, passed: debt != null && debt <= R.debtMax },
    bank
      ? { key: "cash", label: "Cash left over", text: SKIP, passed: null }
      : { key: "cash", label: "Cash left over", text: cash == null ? MISSING : `${beside(cash, 0)} a year`, passed: cash != null && cash > 0 },
    cyclical
      ? { key: "price", label: "Price for growth", text: `${pegShown}, not used for this kind of business`, passed: null }
      : {
        key: "price", label: "Price for growth",
        text: `${pegShown}${dividendPct > 0 ? ` with its ${dividendPct.toFixed(1)}% yearly payout` : ""} (1 or less: at or under his fair price)`,
        passed: peg <= R.pegCheap,
      },
  ];
  // His book's word for the middle group is "stalwart"; the page says "steady grower" everywhere.
  const grower = growth < R.stalwartFrom ? "Slow grower" : growth < R.fastFrom ? "Steady grower" : "Fast grower";
  const verdict = cyclical ? "CYCLICAL" : peg <= R.pegCheap ? "CHEAP" : peg <= R.pegFair ? "FAIR" : "PRICEY";
  // His fair price: where P/E = growth % + dividend %. E and D are dollars a share, so the answer
  // does not depend on today's price (the yield does): the root of P^2 - E*g*P - 100*E*D = 0.
  const price = isNum(row.price) && row.price > 0 ? row.price : null;
  const foreign = typeof row.currency === "string" && row.currency && row.currency !== "USD" ? row.currency : null;
  let fair = null;
  if (price != null && !foreign) {
    const profit = price / row.pe;
    const payout = (price * dividendPct) / 100;
    const reach = profit * counted;
    fair = (reach + Math.sqrt(reach * reach + 400 * profit * payout)) / 2;
  }
  const note = cyclicalNote(cyclical);
  const lowQuality = row.quality !== "HIGH";
  const buy = wouldBuy({ checks, verdict, fair, growth, cyclical, note, inventoryWarning, lowQuality, foreign, priced: price != null });
  return {
    peg,
    grower,
    buy,
    plan: peterPlan({
      buy, fair, price, grower, cyclical, inventoryWarning, inventoryKnown: turn != null, bank, foreign, verdict,
      // where the score reaches 2, the level he called very negative: P^2 - 2*E*g*P - 200*E*D = 0
      negative: fair == null ? null : (() => {
        const profit = price / row.pe;
        const reach = 2 * profit * counted;
        return (reach + Math.sqrt(reach * reach + 800 * profit * ((price * dividendPct) / 100))) / 2;
      })(),
    }),
    cyclicalNote: note,
    kind: cyclicalKind(cyclical),
    growthPct: growth,
    counted,
    cyclical,
    dividendPct,
    inventoryWarning,
    lowQuality,
    type: cyclical ? `Cyclical · ${cyclical}` : grower,
    verdict,
    stars: checks.filter((c) => c.passed === true).length,
    of: checks.filter((c) => c.passed !== null).length,
    checks,
  };
}

// [{ row, sheet }] most stars first, then the lowest PEG. scope "Mag7" also
// returns { row, sheet: null, reason } at the end for a member with no sheet.
export function lynchRows(rows, { tile = null, query = "", scope = null, lists = null } = {}) {
  const wanted = String(query || "").trim().toLowerCase();
  const best = scope === "Best";
  const listed = scope && scope !== "Quality" && !best;
  const members = listed ? new Set(Array.isArray(lists?.[scope]) ? lists[scope].map((s) => String(s).toUpperCase()) : []) : null;
  const everyMember = scope === "Mag7";
  const list = [];
  const bare = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (members && !members.has(String(row?.symbol || "").toUpperCase())) continue;
    if (wanted && !`${row.symbol || ""} ${row.name || ""}`.toLowerCase().includes(wanted)) continue;
    const sheet = lynchSheet(row, { anyQuality: everyMember });
    if (!sheet) {
      if (everyMember && !tile && row.signal !== "ETF") bare.push({ row, sheet: null, reason: lynchMissing(row) || "not scored" });
      continue;
    }
    if (best && sheet.buy.answer !== "YES") continue;
    if (tile && sheet.verdict !== tile) continue;
    list.push({ row, sheet });
  }
  list.sort((a, b) => b.sheet.stars - a.sheet.stars || a.sheet.peg - b.sheet.peg
    || String(a.row.symbol).localeCompare(String(b.row.symbol)));
  bare.sort((a, b) => String(a.row.symbol).localeCompare(String(b.row.symbol)));
  return [...list, ...bare];
}

export function lynchCounts(list) {
  const counts = { CHEAP: 0, FAIR: 0, PRICEY: 0, CYCLICAL: 0 };
  for (const item of Array.isArray(list) ? list : []) if (item.sheet) counts[item.sheet.verdict] += 1;
  return counts;
}
