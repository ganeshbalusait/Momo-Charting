// Long Term page glossary: the "i" next to each term (Ganesh 11-07: "I need
// each name 'I' for information - what is MOS, ROIC, EPS"). Plain language for
// a trader, not an accountant: what the number is, and what the page wants it
// to be. Pure data + matching; no React, so it is unit-tested on its own.

export const GLOSSARY = {
  mos: {
    term: "MOS (margin of safety)",
    text: "How far below the fair value the price must be before the page says BUY. 50% MOS: BUY only at half the fair value or less. 30% MOS: at 70% of it. A bigger margin means fewer BUYs and more room to be wrong.",
  },
  tiles: {
    term: "The tiles",
    text: "BUY: a Rule #1 business at or under its Safety Buy price. LEAPS: strong enough to own through long-dated calls (price at most 80% of the sticker, ROIC 20%+, 8+ years, earnings not falling). LEAP SETUP: a BUY or WAIT business whose price touched its Monthly 50 EMA this month while the monthly trend is up, the one entry the back-test found that beats buying on any day. WAIT: a good business, price still too high; the row shows the price to buy at. AVOID: fails a test, the reason says which. NO DATA: an ETF, no SEC filings, too little history, or a stale filing. Tap a tile to show only those rows; tap it again to show all.",
  },
  roic_now: {
    term: "ROIC (return on invested capital)",
    text: "Profit ÷ (the owners' money + debt). How many cents the business earns each year on every dollar put into it. Rule #1 wants 15% or more; under 10% means the business struggles to earn its keep.",
  },
  roe_now: {
    term: "ROE (return on equity)",
    text: "Profit ÷ the owners' money. Used instead of ROIC for banks and insurers, whose debt is their raw material. 15% or more wanted.",
  },
  roic_consistency: {
    term: "ROIC consistency",
    text: "In how many of the last 10 years ROIC was at least 10%. Rule #1 wants 80% of the years, so one good year is not enough.",
  },
  sales_growth: {
    term: "Sales growth",
    text: "How fast revenue (everything the company sold) grew per year, over up to 10 years. 10% a year or more wanted.",
  },
  eps_growth: {
    term: "EPS (earnings per share) growth",
    text: "EPS = the year's profit ÷ the number of shares. This is how fast it grew per year, over up to 10 years, adjusted for stock splits. 10% a year or more wanted.",
  },
  equity_growth: {
    term: "Equity growth",
    text: "Equity = what the company owns minus what it owes (book value). Money handed back through buybacks and dividends is added back so a generous company is not punished. 10% a year or more wanted.",
  },
  fcf_growth: {
    term: "FCF (free cash flow) growth",
    text: "FCF = cash the business brings in minus what it spends on equipment and buildings: the cash the owners could actually take out. 10% a year or more wanted.",
  },
  debt: {
    term: "Debt payable",
    text: "All debt ÷ one year of free cash flow. 0.6× means the company could pay off all its debt with about 7 months of cash. Rule #1 wants 3× or less, or no debt.",
  },
  earning: {
    term: "Earning money now",
    text: "EPS over the last 12 months (TTM = trailing twelve months) and free cash flow must both be above zero. A company losing money is AVOID.",
  },
  history: {
    term: "History and freshness",
    text: "Complete fiscal years of SEC filings behind the numbers. 3 are needed to judge at all; BUY or LEAPS need 8. Stale means the latest filing is too old to trust.",
  },
  eps_not_falling: {
    term: "Earnings not falling",
    text: "EPS over the last 12 months compared with the last full year. Under 0.8× means profit is dropping fast, so no BUY.",
  },
  sources: {
    term: "Sources agree",
    text: "The company's SEC filings (EDGAR) and Finnhub are two independent sources. When they disagree on ROIC or EPS by too much, the row cannot be BUY until they agree.",
  },
  sticker: {
    term: "Sticker price (step 1: EPS grown 10 years)",
    text: "Start from EPS now (or 1.5× the 3-year average if lower), and grow it for 10 years at the growth rate g (the lower of EPS and equity growth, capped at 15%).",
  },
  pe: {
    term: "P/E (price ÷ earnings)",
    text: "How many years of today's profit you pay for one share. The future P/E is the lower of the company's own historical P/E and 2 × g. Future EPS × future P/E = the price in 10 years.",
  },
  discount: {
    term: "Discounted to today → sticker",
    text: "Money in 10 years is worth less today. Dividing the 10-year price by 1.15 for each year gives the sticker: buy at the sticker and you earn about 15% a year if the business keeps growing.",
  },
  buffett: {
    term: "Buffett value",
    text: "Today's value of the owner earnings (free cash flow minus the stock handed to staff) for 10 years, discounted 10% a year, plus a small value for the years after.",
  },
  fair: {
    term: "Fair value",
    text: "The lower, safer of the sticker price and the Buffett value.",
  },
  safety: {
    term: "Safety Buy",
    text: "Fair value minus the margin of safety: the BUY price. At 50% MOS a $538 fair value gives a $269 Safety Buy.",
  },
  leaps_flag: {
    term: "LEAPS flag",
    text: "LEAPS are long-dated calls. The flag shows when the price is at most 80% of the sticker on a business with ROIC 20%+, 8+ years of history and earnings not falling.",
  },
  payback: {
    term: "Payback time",
    text: "How many years of growing owner earnings it takes to add up to the company's market value. Phil Town buys at 8 years or less.",
  },
  entry: {
    term: "ENTRY-style contract and delta",
    text: "An out-of-the-money call with delta just under 0.20 and the most open interest, 3 to 12 months out, the way the Options chain marks ENTRY. Delta ≈ the chance the call ends in the money, and how much it moves per $1 in the stock: 0.15 = about a 15% chance and 15¢ per $1. Cheap, but the stock must rise a lot before it pays (see breakeven).",
  },
  quote: {
    term: "Ask, bid, mid",
    text: "Bid = the most a buyer pays right now; ask = the least a seller takes; mid = halfway. One contract is 100 shares, so a $7.00 price costs $700.",
  },
  breakeven: {
    term: "Breakeven",
    text: "The stock price at expiry where the call has paid back what it cost: strike + price paid. +79% means the stock must rise 79% by expiry just to get your money back.",
  },
  intrinsic: {
    term: "In the money and time value",
    text: "In the money (intrinsic value) = what the call is worth if used today: stock price minus strike, or $0. Time value = the rest of the price; it melts to zero by expiry.",
  },
  oi: {
    term: "Open interest (OI) and volume",
    text: "Open interest = contracts still open at this strike; volume = contracts traded today. Low numbers mean few buyers and sellers: prices jump and you may not get out at a fair price.",
  },
  thin: {
    term: "Thin market",
    text: "Fewer than 50 open contracts. Use a limit order near the mid, never a market order, and expect a wide gap when you sell.",
  },
  wide: {
    term: "Wide spread",
    text: "The ask is far above the mid, so you lose that gap the moment you buy. Use a limit order near the mid.",
  },
  deep: {
    term: "Deep call (stock substitute)",
    text: "An in-the-money call with delta about 0.75. It moves almost like the stock for much less money and needs only a small rise to break even: Rule #1's way to own the stock through options.",
  },
  years: {
    term: "Ten years",
    text: "Revenue = sales. Net inc = profit after everything. EPS = profit per share. Equity = what the company owns minus what it owes. FCF = free cash flow. ROIC = profit ÷ (owners' money + debt). All from the company's SEC filings.",
  },
  setup: {
    term: "LEAP setup",
    text: "Three checks at once: (1) a daily low this month reached the Monthly 50 EMA (the 50-month average of monthly closes, completed months only); (2) uptrend: last month closed above that EMA and the EMA is rising; (3) the Long Term page rates the business BUY or WAIT. All three = a LEAP setup, and the page looks up a contract for it even without the LEAPS flag.",
  },
  setup_touch: {
    term: "Monthly 50 EMA touch",
    text: "The 50 EMA of the monthly closes of completed months (thinkorswim's EMA, settled after 100 months). Touched = some daily low this month was at or below it: the pullback to long-term support the back-test buys. Not touched: the line says how far the level is below this month's low.",
  },
  setup_uptrend: {
    term: "Uptrend (monthly)",
    text: "Last month's close is above the Monthly 50 EMA, and the EMA is rising (higher than a month earlier). A touch in a falling trend is a breakdown, not a pullback, so it is not a setup.",
  },
  setup_verdict: {
    term: "Long Term check",
    text: "The business must be worth owning: the Long Term page rates it BUY or WAIT after the price check. AVOID, No price, No data or a stale filing never make a setup. The LEAPS flag is not needed; it is shown beside it.",
  },
  crosschecks: {
    term: "Cross-checks",
    text: "Each line compares the SEC filings with Finnhub for the same number. ✓ = they agree; ✗ = they differ, and the row is judged on the worse one.",
  },
};

// The drawer's lines are text; the first words say which term a line is about.
// Order matters: the first match wins (e.g. "ROIC consistency" before "ROIC now").
const LINE_RULES = [
  [/^(?:[✓✗] |n\/a )?ROIC consistency\b/, "roic_consistency"],
  [/^(?:[✓✗] |n\/a )?ROE consistency\b/, "roic_consistency"],
  [/^(?:[✓✗] |n\/a )?ROIC now\b/, "roic_now"],
  [/^(?:[✓✗] |n\/a )?ROE now\b/, "roe_now"],
  [/^(?:[✓✗] |n\/a )?Sales growth\b/, "sales_growth"],
  [/^(?:[✓✗] |n\/a )?EPS growth\b/, "eps_growth"],
  [/^(?:[✓✗] |n\/a )?Equity growth\b/, "equity_growth"],
  [/^(?:[✓✗] |n\/a )?FCF growth\b/, "fcf_growth"],
  [/^(?:[✓✗] |n\/a )?Debt payable\b/, "debt"],
  [/^(?:[✓✗] |n\/a )?Earning money now\b/, "earning"],
  [/^(?:[✓✗] |n\/a )?History and freshness\b/, "history"],
  [/^(?:[✓✗] |n\/a )?Earnings not falling\b/, "eps_not_falling"],
  [/^(?:[✓✗] |n\/a )?Sources agree\b/, "sources"],
  [/^Sticker EPS\b/, "sticker"],
  [/^Historical P\/E\b/, "pe"],
  [/^Discounted\b/, "discount"],
  [/^Buffett value\b/, "buffett"],
  [/^Fair value\b/, "fair"],
  [/^Safety Buy\b/, "safety"],
  [/^LEAPS flag\b/, "leaps_flag"],
  [/^Payback\b/, "payback"],
  [/^ENTRY-style\b/, "entry"],
  [/^No ENTRY contract\b/, "entry"],
  [/^Ask(?= \$)/, "quote"],
  [/^Breakeven\b/, "breakeven"],
  [/^In the money\b/, "intrinsic"],
  [/^Open interest\b/, "oi"],
  [/^⚠ thin market\b/, "thin"],
  [/^⚠ wide spread\b/, "wide"],
  [/^Deep call\b/, "deep"],
  // LEAP setup section (2026-11-08)
  [/^(?:[✓✗] |n\/a )?Monthly 50 EMA\b/, "setup_touch"],
  [/^(?:[✓✗] |n\/a )?Uptrend\b/, "setup_uptrend"],
  [/^(?:[✓✗] |n\/a )?Long Term\b/, "setup_verdict"],
  [/^⚠ Monthly bars stale\b/, "setup"],
];

// Which term a line is about, and where its label ends, so the "i" sits right
// after the term ("ROIC now ⓘ 40% ≥ 15%"), not at the end of a long line
// (Ganesh 11-07: "next to ROIC, EPS like that"). `at` indexes the line as given.
export function glossaryMatch(line) {
  const raw = String(line ?? "");
  const lead = raw.length - raw.trimStart().length;
  const text = raw.trim();
  for (const [re, key] of LINE_RULES) {
    const m = re.exec(text);
    if (m) return { key, at: lead + m.index + m[0].length };
  }
  return null;
}

export function glossaryKey(line) {
  return glossaryMatch(line)?.key ?? null;
}

export function glossaryEntry(key) {
  return (key && GLOSSARY[key]) || null;
}
