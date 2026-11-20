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
//   price   PEG (P/E / growth %) is 1 or less
// A bank is not judged on debt or cash flow; those two are left out for it.

export const LYNCH_TILES = ["CHEAP", "FAIR", "PRICEY"];
export const LYNCH_RULES = Object.freeze({
  growthMin: 10, growthMax: 50, salesMin: 5, debtMax: 0.8, pegCheap: 1, pegFair: 2, stalwartFrom: 10, fastFrom: 20,
});

const SCORED = new Set(["BUY", "WAIT", "AVOID"]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const pct = (v) => (isNum(v) ? Math.round(v * 1000) / 10 : null);
const whole = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v))}%`;

export function lynchSheet(row) {
  if (!row || row.quality !== "HIGH" || row.young || !SCORED.has(row.signal)) return null;
  const growth = pct(row.epsGrowth);
  if (!isNum(row.pe) || row.pe <= 0 || growth == null || growth <= 0) return null;
  const R = LYNCH_RULES;
  const bank = !!row.financial;
  const peg = Math.round((row.pe / Math.min(growth, R.growthMax)) * 100) / 100;
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
    { key: "price", label: "Price for growth", text: `PEG ${peg.toFixed(2)}`, passed: peg <= R.pegCheap },
  ];
  return {
    peg,
    growthPct: growth,
    type: growth < R.stalwartFrom ? "Slow grower" : growth < R.fastFrom ? "Stalwart" : "Fast grower",
    verdict: peg <= R.pegCheap ? "CHEAP" : peg <= R.pegFair ? "FAIR" : "PRICEY",
    stars: checks.filter((c) => c.passed === true).length,
    of: checks.filter((c) => c.passed !== null).length,
    checks,
  };
}

// [{ row, sheet }] for the quality rows: most stars first, then the lowest PEG.
export function lynchRows(rows, { tile = null, query = "" } = {}) {
  const wanted = String(query || "").trim().toLowerCase();
  const list = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    const sheet = lynchSheet(row);
    if (!sheet) continue;
    if (tile && sheet.verdict !== tile) continue;
    if (wanted && !`${row.symbol || ""} ${row.name || ""}`.toLowerCase().includes(wanted)) continue;
    list.push({ row, sheet });
  }
  return list.sort((a, b) => b.sheet.stars - a.sheet.stars || a.sheet.peg - b.sheet.peg
    || String(a.row.symbol).localeCompare(String(b.row.symbol)));
}

export function lynchCounts(list) {
  const counts = { CHEAP: 0, FAIR: 0, PRICEY: 0 };
  for (const item of Array.isArray(list) ? list : []) counts[item.sheet.verdict] += 1;
  return counts;
}
