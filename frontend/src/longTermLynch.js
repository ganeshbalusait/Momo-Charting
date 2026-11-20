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
export const LYNCH_RULES = Object.freeze({
  growthMin: 10, growthMax: 50, salesMin: 5, debtMax: 0.8, pegCheap: 1, pegFair: 2, stalwartFrom: 10, fastFrom: 20, inventorySlow: 0.1,
});

// Back-test run 2026-11-19 (artifacts/backtest_peter_lynch.py -> peter_lynch_backtest.json):
// 98 of today's companies, 452 yearly picks from 2010, entered 90 days after each fiscal year
// end, PEG without the dividend. Shown on the page so the tag is not read as a buy signal.
export const LYNCH_BACKTEST_NOTE = "Back-test 2010–2026 (98 of today's companies, 452 yearly picks): the CHEAP tag did NOT beat the quality group as a whole. Up after 1 year: CHEAP 72% vs all quality 74% in 2010–18, and 59% vs 66% in 2019–26; after 3 years 82% vs 85%, and 73% vs 82%. Quality itself did the work. Read CHEAP as the price you pay, not a buy signal. Limits: today's survivors only, yearly data. Not advice.";

const SCORED = new Set(["BUY", "WAIT", "AVOID"]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const pct = (v) => (isNum(v) ? Math.round(v * 1000) / 10 : null);
const whole = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v))}%`;

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
  if (!row) return "no data";
  if (!SCORED.has(row.signal)) {
    if (row.signal === "ETF") return "an ETF, not a company";
    if (row.signal === "NO_PRICE") return "no live price";
    if (row.signal === "STALE") return "filings are stale";
    if (row.signal === "SHORT_HISTORY") return "not enough years of filings";
    return "no usable filings";
  }
  if (!isNum(row.pe) || row.pe <= 0) return "no profit, so no P/E";
  const growth = pct(row.epsGrowth);
  if (growth == null) return "no earnings growth figure";
  if (growth <= 0) return "earnings did not grow";
  return null;
}

// "Would Peter buy it?" (2026-11-20, Ganesh: "if Peter was there did he buy it or not? Simple
// question ... Think like you are Peter"). One answer from his own checks, never Warren's:
//   YES      every judged check passes, the price is low for the growth (PEG 1 or less), and no
//            stock is piling up
//   WAIT     the business passes, the price does not; his price is where the P/E equals the
//            growth (PEG 1), so today's price / PEG
//   NO       a business check fails (growth, sales, debt, cash) or unsold goods are piling up
//   CAREFUL  a cyclical: he bought those on the turn of the cycle, never because the P/E was low
// This is the page's reading of his published checklist, not a forecast.
const BUY_NO = {
  sales: "Its sales are barely growing.",
  debt: "It owes too much.",
  cash: "Its spare cash is shrinking.",
};

function wouldBuy({ row, checks, verdict, peg, growth, cyclical, inventoryWarning }) {
  if (cyclical) {
    return { answer: "CAREFUL", why: `This kind of business (${cyclical}) has good years and bad years, so a low price can be the top of a good year.`, price: null };
  }
  const failed = checks.find((c) => c.key !== "price" && c.passed === false);
  if (failed) {
    const why = failed.key === "growth"
      ? (growth > LYNCH_RULES.growthMax ? "Its profit growth is too fast to last." : "Its profit grows too slowly.")
      : BUY_NO[failed.key];
    return { answer: "NO", why, price: null };
  }
  if (inventoryWarning) return { answer: "NO", why: "Unsold goods are piling up faster than it sells.", price: null };
  if (verdict === "CHEAP") return { answer: "YES", why: "Every check passes and the price is low for its growth.", price: null };
  const base = "A good business, but the price is too high for its growth.";
  if (!isNum(row.price) || row.price <= 0) return { answer: "WAIT", why: base, price: null };
  const at = row.price / peg;
  const price = at >= 10 ? Math.round(at) : Math.round(at * 100) / 100;
  return { answer: "WAIT", why: `${base} Peter's price: $${price.toLocaleString("en-US")} or less.`, price };
}

export function lynchSheet(row, { anyQuality = false } = {}) {
  if (!row || lynchMissing(row)) return null;
  if (!anyQuality && (row.quality !== "HIGH" || row.young)) return null;
  const growth = pct(row.epsGrowth);
  const R = LYNCH_RULES;
  const bank = !!row.financial;
  const cyclical = typeof row.cyclical === "string" && row.cyclical.trim() ? row.cyclical.trim() : null;
  // Lynch's own formula adds the dividend yield to the growth rate.
  const dividendPct = isNum(row.dividendYield) && row.dividendYield > 0 ? Math.round(row.dividendYield * 1000) / 10 : 0;
  const peg = Math.round((row.pe / (Math.min(growth, R.growthMax) + dividendPct)) * 100) / 100;
  const pegText = `PEG ${peg.toFixed(2)}${dividendPct > 0 ? ` with the ${dividendPct.toFixed(1)}% dividend` : ""}`;
  // His warning sign: inventory growing faster than sales (the turnover fell 10%+ in a year).
  const turn = isNum(row.inventoryTurnChange) ? row.inventoryTurnChange : null;
  const inventoryWarning = turn != null && turn <= -R.inventorySlow
    ? `Inventory is turning over ${Math.round(-turn * 100)}% slower than a year ago: it is growing faster than sales.`
    : null;
  const sales = pct(row.salesGrowth);
  const cash = pct(row.fcfGrowth);
  const debt = isNum(row.debtToEquity) ? row.debtToEquity : null;
  const checks = [
    {
      key: "growth", label: "Earnings growth",
      text: growth > R.growthMax ? `${whole(growth)}/yr, too fast to last` : `${whole(growth)}/yr`,
      passed: growth >= R.growthMin && growth <= R.growthMax,
    },
    { key: "sales", label: "Sales growth", text: sales == null ? "no figure" : `${whole(sales)}/yr`, passed: sales != null && sales >= R.salesMin },
    bank
      ? { key: "debt", label: "Debt", text: "bank, not judged", passed: null }
      : { key: "debt", label: "Debt", text: debt == null ? "no equity figure" : `${debt.toFixed(2)}× equity`, passed: debt != null && debt <= R.debtMax },
    bank
      ? { key: "cash", label: "Cash flow", text: "bank, not judged", passed: null }
      : { key: "cash", label: "Cash flow", text: cash == null ? "no figure" : `${whole(cash)}/yr`, passed: cash != null && cash > 0 },
    cyclical
      ? { key: "price", label: "Price for growth", text: `PEG ${peg.toFixed(2)}, not judged`, passed: null }
      : { key: "price", label: "Price for growth", text: pegText, passed: peg <= R.pegCheap },
  ];
  const grower = growth < R.stalwartFrom ? "Slow grower" : growth < R.fastFrom ? "Stalwart" : "Fast grower";
  const verdict = cyclical ? "CYCLICAL" : peg <= R.pegCheap ? "CHEAP" : peg <= R.pegFair ? "FAIR" : "PRICEY";
  return {
    peg,
    grower,
    buy: wouldBuy({ row, checks, verdict, peg, growth, cyclical, inventoryWarning }),
    growthPct: growth,
    cyclical,
    dividendPct,
    inventoryWarning,
    lowQuality: row.quality !== "HIGH",
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
