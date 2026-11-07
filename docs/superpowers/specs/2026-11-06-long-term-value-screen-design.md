# Long Term value screen (Buffett / Rule #1) — design

Date: 2026-11-06 (revised after a three-reviewer check the same night).
Requested by Ganesh: "build like this in our app" from his Google Sheet
`Buffet-Calc_Stock_BigFive`, tab `LONG-TERM-WARREN`, with "build what you
think and correct everything", "no manual input, data should be correct",
"don't miss any tickers from our watchlist", "left side panel 'Long
Term'", "make sure UI looks good". His use of it: **"if it says BUY I will
buy; if it says LEAPS I will buy."** That sentence sets the bar: a BUY or
LEAPS flag must never come from bad, stale, split-corrupted or
half-missing data, and a missing price must never read as a bargain.

## Intent

A page in the app that answers, for every ticker he follows, the Rule #1 /
Buffett questions the sheet tried to answer: is this a wonderful business
(ten years of high return on capital; growth in sales, earnings, equity and
free cash flow; debt it can pay off), what is it worth, and is today's
price far enough below that worth to buy. The sheet's method is kept where
it is right (the checklist, his thresholds, AVOID / WAIT / BUY, the LEAPS
flag) and corrected where it is wrong:

| Sheet | Problem | This design |
|---|---|---|
| Net income, equity, debt, cash typed in from Yahoo | thousands-vs-dollars mistakes (Apple net income $8.29M); most rows AVOID for a data reason | pulled from SEC filings (XBRL company facts), ten years, nightly, no typing |
| Fair value = EPS × (8.5 + 2×10) × 4.4/4.5 | Graham formula with 10% growth for every company | Rule #1 sticker price on each company's own growth and its own historical P/E, plus a Buffett owner-earnings value |
| Growth Type `IF(F>=20)` on a fraction | always LOW | fixed, renamed **Quality** (HIGH when ROIC ≥ 20%) |
| LEAPS guard `J > -50` on a fraction | never blocks | fixed: price within a fixed band under the sticker, earnings not falling |
| 5-year growth | a two-year hot streak looks like quality | up to 10 fiscal years, 3-year averaged endpoints, consistency counted year by year |
| Single source | nothing to catch a bad number | two independent sources plus a price sanity check; disagreement blocks BUY/LEAPS |

## Goals

- One nav entry **"Long Term"** in the left panel (desktop) and the phone
  "More" sheet; works at desktop and phone width.
- Every ticker in his universe: the app watchlist file (371 today), every
  MomX named list (Watchlist, Mag7, EMA lists, Movers…), and the 239
  distinct tickers from the sheet as a seed list in the repo. Union of all,
  deduplicated, symbols normalised (BRK.B / BRK-B / BRK/B are one ticker).
  Verified 2026-11-06: 385 tickers, 35 ETFs, 5 with no SEC record (EA, WBD,
  BITF, PMH, APPU), 345 scoreable.
- Zero manual data entry. Everything is fetched, computed, dated and
  explained.
- Signals he can act on: **BUY**, **WAIT**, **AVOID**, and a **LEAPS**
  flag, each with a one-line reason; every WAIT row shows **the price it
  becomes a BUY at and the price the LEAPS flag turns on at**, because on
  most days a Rule #1 screen has few or no BUYs and the page must still be
  useful.
- Fail safe: anything unknown, stale, inconsistent or priceless is never
  BUY or LEAPS.
- Looks like it belongs in the app (dark terminal theme, tabular
  numerals), dense but readable; phone gets cards, not a squeezed table.

## Non-goals (this round)

- The sheet's LEAP CHECKERS tab (grading one option contract: delta,
  extrinsic %, expiry). Next round; the drawer says so.
- Analyst estimates, moat scoring, management quality. Not computable from
  filings; the "why" drawer says so.
- A trend gate on the LEAPS flag (price above the weekly 50 EMA). Noted
  as a possible next step; not in this round.
- Alerts, portfolio tracking, AI commentary, fundamentals charts.

## Anti-goals

- A BUY or LEAPS that comes from a scale error, a stock split, a stale
  filing, a missing year, an unresolved debt tag, a bank scored like a
  factory, a null price, or one source alone.
- A page that is empty or "loading" for minutes. Rows are precomputed
  nightly and served from memory; only the price is live.
- Anything that slows the chart or scanner. No CPU-heavy work in the
  request path; the nightly pass is paced and refuses to run during
  market hours.
- Missing tickers. A ticker that cannot be scored is still listed with
  the reason.

## Data sources (my call)

1. **SEC EDGAR XBRL company facts** (primary for dollar items; free, no
   key): `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`; ticker →
   CIK from `www.sec.gov/files/company_tickers.json`; SIC code (bank /
   insurer / broker detection) from `data.sec.gov/submissions/CIK….json`,
   cached per CIK. Verified for AAPL, ASML (20-F, EUR), TSM (IFRS, TWD +
   USD), HOOD, MCD, HD, JPM, GOOGL, NVDA, CRWV. User-Agent
   `AGX Trading App (admin@agxtrade.com)` (env `SEC_USER_AGENT`), gzip,
   ≤ 5 requests/s, exponential back-off on 403/429, abort the pass after
   three consecutive 403s. One fetch per CIK (GOOG/GOOGL share one).
2. **Finnhub basic financials** (`/stock/metric?metric=all`, free tier,
   key already in `.env`): `series.annual.eps` (split-adjusted, decades),
   `series.annual.pe` (historical P/E), `series.annual.roic`, `roiTTM`,
   `roeTTM`, `epsTTM` / `epsInclExtraItemsTTM`, `peTTM`,
   `marketCapitalization`, `totalDebt/totalEquityAnnual`. Verified
   2026-11-06 on AAPL, ASML, HOOD, TSM, NVDA, GOOGL, JPM, MCD, CRWV. Known
   gaps: banks have no `eps` series (JPM), negative-equity names have no
   `roic` series after equity turns negative (MCD), young names have few
   years, `pe` values can be null. Paced at 30/min with back-off (the key
   is shared with the sector lookup and earnings calendar). An empty
   answer is "Finnhub: no data" (never BUY), not an error.
3. **Schwab quotes** (already in the app): live last price and company
   name, chunked and cached like `/api/watchlist-quotes`. The nightly pass
   also stores a reference price per ticker.
4. ETF detection: `momx/etf_list.py` (`is_etf`). ETFs get a footer line and
   no signal.

No FMP, no Alpha Vantage, no Yahoo.

## Fact selection rules (EDGAR)

- An **annual** row is a fact with form in {10-K, 10-K/A, 10-KT, 20-F,
  20-F/A, 40-F} and 350 ≤ (end − start) ≤ 380 days (instants: end only).
  Never select by `fy`/`fp`/`frame`. Key by period end; when the same
  period appears in several filings take the latest `filed` (tie-break by
  accession). Label years by period end.
- **One unit per concept per company**: the unit (USD, EUR, TWD…) that
  covers the most annual years; never mix units in a series; the unit is
  stored and shown ("in TWD millions").
- **Tag resolution per company, not per year**: for each concept, the
  primary is the candidate covering the **latest annual year** (ties →
  most coverage; debt and capex → table order); another tag may fill
  missing years only if it agrees within 2% in every overlap year, else
  the year is left blank and the series is marked "tag break". Debt and
  capex fill missing years in table order **without** the 2% rule (noted,
  no tag break); capex refuses a tag more than 25% off in an overlap year.
  A debt recipe showing 0 in the latest year never outranks a later one
  showing a value there.
  Candidate lists (first draft; one module, one test per concept):
  - Revenue: Revenues, RevenueFromContractWithCustomerExcludingAssessedTax,
    …IncludingAssessedTax (take the largest when several exist in a year),
    SalesRevenueNet; banks:
    InterestAndDividendIncomeOperating + NoninterestIncome; IFRS Revenue.
  - Net income: NetIncomeLoss, ProfitLoss; IFRS
    ProfitLossAttributableToOwnersOfParent, ProfitLoss.
  - Equity: StockholdersEquity, …IncludingPortionAttributableToNoncontrollingInterest;
    IFRS EquityAttributableToOwnersOfParent, Equity.
  - Debt, ordered mutually exclusive recipes: (a) LongTermDebtNoncurrent +
    LongTermDebtCurrent + ShortTermBorrowings + CommercialPaper; (b)
    LongTermDebt + ShortTermBorrowings + CommercialPaper; (c) DebtCurrent +
    LongTermDebtNoncurrent; (d) LongTermDebtAndCapitalLeaseObligations;
    (e) IFRS Noncurrent/Current Borrowings, else Borrowings; then (audit
    2026-11-06) NotesPayable; ConvertibleLongTermNotesPayable +
    ConvertibleNotesPayableCurrent; DebtAndCapitalLeaseObligations;
    LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities;
    LoansPayable; OtherNotesPayableCurrent; LongTermDebtNoncurrent alone;
    ConvertibleDebtNoncurrent + ConvertibleDebtCurrent; ConvertibleNotesPayable;
    IFRS LongtermBorrowings + CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings
    (current parts optional where listed). Single-part partial recipes
    (NotesPayable, LoansPayable, OtherNotesPayableCurrent,
    LongTermDebtNoncurrent) carry a "partial recipe" note. Operating
    leases excluded (shown in the drawer).
  - Cash: CashAndCashEquivalentsAtCarryingValue,
    CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents; IFRS
    CashAndCashEquivalents.
  - Operating cash flow: NetCashProvidedByUsedInOperatingActivities,
    …ContinuingOperations; IFRS CashFlowsFromUsedInOperatingActivities.
  - Capex: PaymentsToAcquirePropertyPlantAndEquipment,
    PaymentsToAcquireProductiveAssets,
    PaymentsToAcquireOtherPropertyPlantAndEquipment; IFRS
    PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities.
  - Stock comp: ShareBasedCompensation. Buybacks:
    PaymentsForRepurchaseOfCommonStock. Dividends: PaymentsOfDividends,
    PaymentsOfDividendsCommonStock. Interest: InterestExpense,
    InterestExpenseNonoperating, InterestExpenseDebt. Operating income:
    OperatingIncomeLoss. Total assets: Assets.
  - Diluted shares: WeightedAverageNumberOfDilutedSharesOutstanding.
    Diluted EPS: EarningsPerShareDiluted; IFRS DilutedEarningsLossPerShare.
- **Splits**: EDGAR per-share facts are as reported in each filing. The
  split factor is inferred from the overlap: when one period end carries
  different EPS values in different filings, the ratio (latest ÷ earliest,
  rounded to a clean ratio 2, 3, 4, 5, 10, 20, 50, 1.5, or 1/2 … 1/10) is
  applied to every older, unrestated year of EPS and shares. Verified on
  GOOGL (FY2020: 58.61 then 2.93, 20:1). EPS growth itself comes from the
  Finnhub split-adjusted series when it exists; the EDGAR split-corrected
  series is the fallback (banks) and the cross-check.
- **TTM** (trailing twelve months) for flow items = latest annual + YTD −
  prior YTD, where YTD is the fact from the latest 10-Q whose start is the
  day after the annual period end (±7 days) with 80–290 days duration, and
  prior YTD is the fact from the **same accession** one year earlier (±7
  days). If anything is missing, TTM = latest FY, stamped "TTM = FY". 20-F
  filers have no quarterly facts: TTM = FY. **EDGAR TTM EPS** = the diluted
  EPS tag's own TTM (latest FY + YTD − prior YTD, the same accession rule,
  split-scaled to today's share basis) when the tag's TTM ends on the net
  income TTM's period (±10 days); otherwise NI_TTM ÷ latest quarterly diluted
  shares. Why the tag first (ruled 2026-11-07, audit J1): buybacks and share
  raises move the diluted count inside the year, so NI_TTM ÷ the latest
  quarter's count mixes four quarters of income with one quarter's shares
  (DECK 7.83 vs the tag's 7.52 after buybacks; CLS 10.23 vs 10.60 after a
  raise); the tag also carries the parent share, preferred dividends and
  the if-converted method that NI ÷ shares misses (IBKR, MCHP, BYND). The
  two bases sat 0.1–4% apart on all ten audited tickers. When neither
  can be formed (diluted shares tagged per class only: HSY), NI_TTM ÷
  period-end CommonStockSharesOutstanding is used (noted "eps TTM derived
  from NI/period-end shares") as EDGAR's EPS for the cross-check and the
  sticker; that row is one source and is capped at WAIT ("not
  cross-checked: EPS TTM derived from NI/period-end shares").
- **Growth windows**: up to 10 fiscal years ending at the latest annual
  row, minimum 5 complete rows; start at the first year whose revenue is
  ≥ 5% of the latest year's and after any > 5× year-over-year jump
  (de-SPACs, shells); never span a gap > 400 days (row marked "gap in
  filings"). CAGR uses actual elapsed days and **3-year averaged
  endpoints** (mean of the first three and last three years) so a trough
  start or a peak end does not fake growth. Undefined when an endpoint
  ≤ 0.
- **Three date stamps** per ticker: latest annual period end, latest
  any-period end (the latest 10-K/10-Q/20-F/40-F period end across **every**
  resolved concept, not net income alone), fetchedAt. Stale = annual end older than 15 months, or
  (US 10-Q filer and latest period end older than 7 months), or fetchedAt
  older than 4 days.

## Numbers computed per ticker

- ROIC (Town's definition, same as Finnhub's) = net income ÷ (equity +
  total debt). n/a when the denominator ≤ 0 or < 10% of total assets.
  Displayed value capped at 100%+.
- FCF = operating cash flow − capex. Owner earnings = FCF − stock-based
  compensation.
- Growth rates (sales, EPS, equity, FCF) as above. Equity growth uses
  capital-returned-adjusted equity (equity + cumulative buybacks +
  cumulative dividends over the window) so buyback companies are not
  punished; if the start value is still ≤ 0 the check is "n/a (buybacks)".
  "Cumulative over the window" is accumulated from the **second** window
  year: the first year's equity is the start value as reported, and its
  own buybacks / dividends are not added (they happened before or while
  that balance formed). Pinned 2026-11-07 (audit J2) as the chosen reading;
  the inclusive reading is slightly more conservative (PYPL g 14.79% vs
  15.37%). A missing buyback or dividend figure counts as 0 and the drawer
  names the concept that is missing ("equity: dividends missing in …").
- Debt resolved to 0 is accepted only when interest expense is absent or
  < 1% of operating income AND Finnhub debt/equity is present with
  0 ≤ D/E < 0.3 (Finnhub's figure includes leases; a missing D/E proves
  nothing); otherwise the row is
  "Check data: debt unresolved". Only then do years without a debt figure
  count as 0 in the ROIC series.
- Debt-understated cross-check: when Finnhub D/E ≥ 2 × our (debt +
  operating leases) ÷ equity and the gap is > 0.2 (equity > 0, not
  financial), Sources agree fails: "debt understated? EDGAR D/E x vs
  Finnhub y" → Check data.
- CIK overrides (`long_term/symbols.py` `CIK_OVERRIDES`) win over the SEC
  ticker file (XOM → 34088; the file points at a shell with no facts).
- Financial companies (SIC 6000–6499 banks and savings, 6200–6299 brokers,
  6300–6399 insurers; V/MA are 7389 and score normally) use the
  **Financial rule set**: ROE replaces ROIC, FCF and debt checks are n/a,
  no Buffett value, sticker from EPS only. Row carries a FIN tag.

## The checklist

| # | Check | Threshold | Notes |
|---|---|---|---|
| 1 | ROIC now (TTM, else latest FY) | ≥ 15% | his gate; FIN: ROE ≥ 15% |
| 2 | ROIC consistency | ≥ 10% in at least 80% of the years in the window (8 of 10) | FIN: ROE |
| 3 | Sales growth | ≥ 10%/yr | |
| 4 | EPS growth | ≥ 10%/yr | Finnhub split-adjusted series, EDGAR fallback |
| 5 | Equity growth (capital-returned-adjusted) | ≥ 10%/yr | n/a (buybacks) is not a fail |
| 6 | FCF growth | ≥ 10%/yr | FIN: n/a |
| 7 | Debt payable | total debt ≤ 3 × FCF, or no debt (see the 0-debt rule) | FIN: n/a |
| 8 | Earning money now | EPS TTM > 0 and FCF TTM > 0 (FIN: EPS only) | |
| 9 | History and freshness | ≥ 5 complete years; not stale | BUY/LEAPS additionally need ≥ 8 years ("young" rows cap at WAIT) |
| 10 | Earnings not falling | EPS TTM ≥ 0.8 × last FY EPS | needed for BUY and LEAPS |
| 11 | Sources agree | see below | |

**Big 5 Score** = how many of checks 2–6 pass, shown as five dots (a grey
dot for n/a). The Rule #1 business test passes when at most one of the
five is a fail and check 2 is not the one that fails.

**Check 11, sources agree** (all must hold for BUY/LEAPS; a fail shows as
"Check data: <reason>"):
- ROIC: EDGAR TTM vs Finnhub roiTTM on the same side of the 15% gate, or
  within max(5 points, 25% relative). Applies to ADRs too.
- EPS (US filers, USD): EDGAR TTM EPS (as defined under TTM above) vs Finnhub
  epsInclExtraItemsTTM (else epsTTM), same period, within max(25%, $0.10).
  Finnhub period more than one quarter from EDGAR's → "periods differ".
- EPS growth: Finnhub series CAGR vs EDGAR split-corrected CAGR within 10
  points, or both ≥ 10%.
- Price sanity: Finnhub epsTTM × peTTM must be within 15% of the Schwab
  price (catches scale and stale-EPS errors). For an ADR this product is in
  the home currency per ordinary share; the ratio Schwab price ÷ (epsTTM ×
  peTTM) is then the **listing factor** (ADR ratio ÷ FX), stored nightly
  and used to convert Finnhub per-share figures to USD per listed share;
  a factor that moves > 20% between refreshes → "Check data: listing
  factor". A USD 10-K filer has no listing factor: it is pinned at 1.0,
  stored as 1.0 and never drift-checked (the raw ratio is only the
  price-sanity note; audit 2026-11-07, HSY 1.26 / CLS 0.82). Verified: TSM epsTTM 98.25 TWD × P/E 23.0 = 2,260 TWD; ASML
  26.96 × 49.0 = 1,322 ≈ the USD ADR price (factor 1).

## Valuation

Sticker price (Rule #1, Phil Town):
- growth g = the lower of EPS growth and equity growth (FIN: EPS and
  equity); when equity growth is n/a, the lower of EPS growth and FCF
  growth; floored at 0, capped at 15%.
- sticker EPS = min(EPS TTM, 1.5 × average of the last three fiscal
  years' EPS), in USD per listed share (Finnhub epsTTM × listing factor,
  cross-checked as above).
- future EPS = sticker EPS × (1+g)^10.
- future P/E = min(2 × g in %, median of the company's historical annual
  P/E over the window from Finnhub `series.annual.pe`, nulls dropped);
  floor 5, cap 40. **Never today's P/E** (a sticker anchored to today's
  price can never be below it). If no historical P/E series exists, 2 × g.
- sticker = future EPS × future P/E ÷ 1.15^10.
- Unit test invariant: the sticker must not change when today's price
  changes, and the signal truth table must contain at least one BUY and
  one LEAPS case.

Buffett value (owner earnings; not for FIN rows):
- owner earnings TTM = FCF − stock comp (local currency), converted to USD
  per listed share as EPS_usd × (owner earnings ÷ net income), which
  cancels currency and ADR ratio (requires NI > 0, else n/a).
- grow 10 years at min(FCF growth, 15%) floored at 0, discount at 10%/yr,
  terminal growth 3%. Shown as "Buffett value" per share.

**Fair value** = the lower of sticker and Buffett value (sticker alone for
FIN rows or when Buffett is n/a). **Safety Buy** = fair value × (1 − MOS).
MOS default **50%** (Town); a 30% option is on the page (the sheet's
header offered 0.7/0.5), remembered per browser. **Buy Zone %** = 1 −
price ÷ Safety Buy, clamped at −100% for the bar.

**Payback Time** (Town's third test, shown as a column, not a gate): years
of owner earnings growing at g needed to add up to the market cap; Town
buys at ≤ 8. Market cap from Finnhub marketCapitalization (USD).

## Signals

- **AVOID** when any of checks 1, 7, 8, 9 (the 5-year part) or 11 fails,
  the Rule #1 business test fails, or Safety Buy cannot be computed. The
  row says which, in words ("ROIC 9% < 15%", "losing money", "debt 5× FCF",
  "Check data: debt unresolved", "Short history (4 years)").
- **BUY** when not AVOID, ≥ 8 years, check 10 passes, and price ≤ Safety
  Buy.
- **WAIT** otherwise. The row shows "BUY ≤ $X · LEAPS ≤ $Y".
- **LEAPS** flag: not AVOID, ≥ 8 years, ROIC now ≥ 20%, check 10 passes,
  and price ≤ 0.8 × sticker (a fixed band that does not move with the MOS
  switch).
- **Quality**: HIGH when ROIC now ≥ 20%, else LOW.
- **No price** when the live price is missing, ≤ 0, not finite, older than
  the last session, or more than 25% away from the nightly reference
  price: the signal is "No price", never BUY/LEAPS, and the tiles exclude
  it. The same guard runs in the browser before any recompute.
- Rows without a score: ETF, No filings, Short history, Stale filing,
  Check data, Financial (scored with the FIN rule set, tagged).

## Refresh and storage

- Package `long_term/`: `symbols.py` (normalisation, universe union,
  seed list of the sheet's tickers with the sheet id and date),
  `edgar.py` (CIK map, SIC, facts fetch, fact selection, tag resolution,
  splits, annual/TTM series), `finnhub.py` (metrics, pacing),
  `metrics.py` (derived numbers and checks), `valuation.py` (sticker,
  Buffett, payback, signals, price guard), `service.py` (store, refresher
  thread, page and detail payloads).
- Store: `artifacts/long_term/fundamentals.json` holds per ticker only the
  resolved annual rows (≤ 10 years × ~16 concepts with unit, tag, form,
  accession, filed, period end), the TTM rows, the Finnhub extract, SIC,
  listing factor, reference price and the three stamps; ~1–2 MB total.
  `artifacts/long_term/summary.json` holds the computed rows. Written every
  10 tickers and at the end via temp file + `os.replace` with a 5-try retry
  on PermissionError, under one lock shared with the reader; previous file
  kept as `.prev.json` for one cycle. The request path serves the
  in-memory copy.
- Refresher: one daemon thread in api_server, started lazily from the
  first `/api/long-term` read or from `main()`, gated by env
  `AGX_LONG_TERM_REFRESH` (tests set it to 0; the harness imports
  api_server without starting it). Nightly at 03:00 ET; at startup if the
  store is empty or older than 24 h, but **never between 09:30 and 16:00
  ET** (a catch-up waits for the close). A ticker is re-fetched when its
  copy is older than 3 days or it is new. Budget ≈ 345 × (EDGAR facts +
  Finnhub) + new SICs, ~12 minutes at the paces above; JSON parsing is
  ~0.2–0.4 s per large filer and runs between sleeps. Progress is in the
  page header.
- Admin "Refresh now" (POST) queues a pass; during market hours it answers
  "deferred to 16:05 ET" and the button says so.
- A ticker that leaves the universe stays in the store 30 days.

## API

- `GET /api/long-term?mos=50|30` → `{ rows, meta: { universe, scored,
  etfs, noFilings, refreshedAt, refreshing: {done,total} | null,
  deferredUntil, mos } }`. Rows carry the precomputed numbers plus the live
  price merge (Buy Zone %, P/E = price ÷ EPS TTM, signal, LEAPS flag,
  "BUY ≤ / LEAPS ≤" levels). Any signed-in user.
- `GET /api/long-term/{SYMBOL}` → detail: checklist with values and
  pass/fail/n-a, the 10-year table with units, the valuation in words,
  the cross-check results, sources and dates, the listing factor.
- `POST /api/long-term/refresh` → admin only.

## The page

Desktop (`LongTermPanel.jsx`; pure logic in `longTerm.js`; styles
appended to `index.css` under `.long-term-*`):
- Header: "Long Term" · "Rule #1 / Buffett screen · 345 scored of 385 ·
  filings refreshed Nov 6, 03:12 AM ET" · a one-line note "Rule #1 buys
  are rare; WAIT rows show the price to buy at" · MOS switch [50% | 30%]
  · search box · (admin) Refresh.
- Five tiles: BUY · LEAPS · WAIT · AVOID · No data, each with its count;
  tapping a tile filters; BUY and LEAPS tiles glow when > 0.
- Table, sortable; default order BUY, then LEAPS, then by Buy Zone %
  descending. Columns: Ticker · Name · Price · Signal · LEAPS · Buy Zone %
  (bar) · BUY ≤ · LEAPS ≤ · Sticker · Buffett · Payback · ROIC · Big 5
  (dots) · Quality · P/E · D/E · Sales 10y · EPS 10y · FCF 10y · Mkt Cap ·
  Filed (period end). FIN rows show ROE in the ROIC column with a FIN tag;
  non-USD filers show "(local ccy)" on growth columns.
- Row click expands a "Why" drawer: the checklist with ✓ / ✗ / n/a and
  values; the 10-year mini table (Revenue, Net income, EPS, Equity, FCF,
  ROIC) with its unit; the valuation in words, generated from the real
  formula ("Sticker EPS $8.98 (TTM $8.98, 3-yr avg $7.5) growing 12%/yr
  for 10 years → $27.9; historical P/E 24 (2 × g = 24) → $669; discounted
  15%/yr → sticker $165. Buffett value $140. Fair value $140; 50% safety →
  Safety Buy $70. LEAPS flag below $132."); cross-checks; sources and
  dates; a "Chart" button that opens the ticker the way Sectors does.
- Footer: "ETFs, no company filings: …" · "No SEC filings: …".
- Phone (≤ 760 px): tiles become a scrollable chip row; rows become cards
  (ticker, name, price, signal + LEAPS pills, Buy Zone bar, BUY ≤ level,
  ROIC, Big 5 dots); tap expands the same drawer. No horizontal page
  scroll.
- First run: "Pulling ten years of filings for 345 companies, about 12
  minutes. Rows fill in as they arrive." with the progress count.

## Tests (three layers, same commit)

- Unit (pytest): fact selection and tag resolution on trimmed real
  fixtures (AAPL, ASML, TSM, HOOD, MCD, HD, JPM, GOOGL, NVDA, CRWV);
  split inference (GOOGL 20:1, NVDA 4:1 then 10:1, a reverse split
  fixture); TTM with YTD matching and the TTM=FY fallback; one-unit rule
  (TSM); growth windows (de-SPAC start, gap, 3-year endpoints, negative
  endpoint); ROIC guard; capital-returned equity; the 0-debt rule; FIN
  classification; cross-check tolerances; sticker (price-invariance
  property), Buffett, payback; the signal truth table (contains BUY and
  LEAPS cases; null/0/stale/far price → No price; young → WAIT); universe
  union and symbol normalisation; store atomic write and retry.
  Frontend (node --test): `longTerm.test.js` for sorting, tile filters,
  formatting, the price guard, MOS switch, phone card mapping, drawer
  text.
- Integration: `tests/test_long_term_api.py` with `running_api()`; EDGAR,
  Finnhub, SIC and Schwab faked by injected getters (fixtures above);
  asserts route shape, auth (anonymous 401, non-admin refresh 403), the
  live price merge, that a faked null/zero/stale quote yields zero BUY
  rows, that a faked source disagreement turns a BUY into Check data, and
  that the refresh is deferred during faked market hours.
- End to end: `tests/e2e/long-term.e2e.test.mjs` — click "Long Term" in
  the left panel, table renders, tile filter works, a row expands, search
  works, phone width shows cards with nothing cut off, no page errors.
  Reads answered with a fixture payload (fakeReads) so the spec does not
  depend on the nightly store.
- `tests/FEATURE_TESTS.md` row; release-notes entry at the top.

## Constraints and operations

- Build in the live repo (`AgenticAI-Trading 2`); commit with explicit
  paths only (other sessions have uncommitted work in the same tree; the
  "EMA scanner and swing trading" session was told what this session
  touches).
- One backend restart to load the routes, outside market hours, announced
  before and after. Full test suites only outside 09:30–16:00 ET.
- Never bulk-hit chart or chain endpoints from this feature.

## Calls I made that he may want to change later

- MOS default 50% (Town) with a 30% switch; the sheet used 30%.
- BUY/LEAPS need 8+ fiscal years; 5–7 years cap at WAIT ("young").
- Financials are scored on ROE and may reach BUY (Town's own practice),
  tagged FIN.
- Non-USD filers (TSM, ASML) are fully scored through the listing factor;
  if the factor cannot be derived the row is Check data.
- The LEAPS flag has no trend gate this round.
- The AVOID word is kept for everything that is not a Rule #1 business;
  the reason column says why, so a momentum name he trades short-term is
  not read as bearish.

## Left to the builder

- Final tag lists after the first nightly audit (log every ticker whose
  debt resolved to 0, every tag break, every cross-check miss).
- Column widths, exact pill colors (reuse existing app pill classes where
  they exist), sort icon.
- The exact wording of each reason string (keep them short and in
  trading terms).
