# Long Term value screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Long Term" page in the left panel that scores every ticker he follows with the Rule #1 / Buffett checklist from SEC filings and Finnhub, and says BUY / WAIT / AVOID plus a LEAPS flag, with every number dated and explained.

**Architecture:** A new Python package `long_term/` fetches SEC XBRL company facts and Finnhub basic financials nightly on a paced daemon thread inside api_server, keeps a trimmed per-ticker store under `artifacts/long_term/`, and computes the checklist, sticker price, Buffett value and signals in pure functions. Three `/api/long-term` routes serve the in-memory rows merged with live Schwab prices. The React panel `LongTermPanel.jsx` renders tiles, a sortable table (cards on the phone) and a "why" drawer; pure screen logic lives in `longTerm.js`.

**Tech Stack:** Python 3.12 stdlib (urllib, json, threading, zoneinfo, dataclasses), pytest, the existing `tests/api_harness.py`; React 18 + lucide icons, node:test, Playwright via `tests/e2e/harness.mjs`.

**Spec:** `docs/superpowers/specs/2026-11-06-long-term-value-screen-design.md`

## Global Constraints

- Build in the live repo `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`; Python is `./.venv/Scripts/python.exe`; frontend tests run from `frontend/` with `node --test`.
- Commit with explicit pathspecs only. `api_server.py` and `frontend/src/index.css` carry another session's uncommitted edits: stage only your hunks (`git diff -- <file> > patch`, trim to your hunks, `git apply --cached patch`), never `git add` the whole file.
- No network from tests: every fetch goes through an injectable `get` callable; `tests/live_guard.py` refuses anything else.
- Env flag `AGX_LONG_TERM_REFRESH` ("1" default in the serving process; `tests/conftest.py` sets "0") gates the refresher thread. Store dir env override `AGX_LONG_TERM_DIR`.
- SEC requests: User-Agent `AGX Trading App (admin@agxtrade.com)` (env `SEC_USER_AGENT`), `Accept-Encoding: gzip`, ≤ 5 req/s, back-off on 403/429, abort after 3 consecutive 403s. Finnhub ≤ 30 req/min.
- Thresholds (verbatim from the spec): ROIC now ≥ 15%; consistency ≥ 10% in ≥ 80% of window years; growth ≥ 10%/yr; debt ≤ 3 × FCF; BUY/LEAPS need ≥ 8 fiscal years, 5–7 years cap at WAIT; EPS TTM ≥ 0.8 × last FY EPS; g floored 0 capped 15%; future P/E = min(2g%, median historical P/E), floor 5, cap 40; discount 1.15^10; Buffett discount 10%, terminal 3%, growth min(FCF growth, 15%) floored 0; MOS default 50%, option 30%; LEAPS band price ≤ 0.8 × sticker and ROIC now ≥ 20%; price guard: > 0, finite, quote not older than the last session, within 25% of the reference price; cross-check tolerances: ROIC same side of 15% or within max(5 pts, 25% rel.), EPS within max(25%, $0.10), EPS growth within 10 pts or both ≥ 10%, price sanity 15%, listing factor drift 20%; stale: annual end > 15 months, US 10-Q filer latest end > 7 months, fetchedAt > 4 days; 0-debt accepted only if interest absent or < 1% of operating income AND Finnhub D/E < 0.05.
- Signals vocabulary (server and screen): `BUY`, `WAIT`, `AVOID`, `NO_PRICE`, `ETF`, `NO_FILINGS`, `SHORT_HISTORY`, `STALE`, `CHECK_DATA`. `leaps` is a separate boolean. `financial` is a separate boolean.
- Every shipped change needs a release-notes entry at the top of `frontend/public/release-notes.json` stamped in ET and a row in `tests/FEATURE_TESTS.md`.
- Never run full test suites 09:30–16:00 ET; end-to-end specs skip themselves then.

## Review Focus

1. A quote that is null, 0, stale (yesterday's) or 30% away from the nightly reference price must produce `NO_PRICE`, never BUY, on both server and screen — Task 6 (`test_price_guard_*`), Task 9 (`priceGuard` tests), Task 8 (integration: faked quotes).
2. A company whose EPS facts straddle a stock split (GOOGL 20:1) must not read as shrinking, and a reverse split must not read as a grower — Task 2 (`test_split_factor_googl`, `test_reverse_split_fixture`).
3. A debt concept that resolves to nothing must not pass the debt check as "no debt" — Task 5 (`test_zero_debt_requires_proof`).
4. A bank (JPM, SIC 6021) must be scored with ROE and never get a Buffett value or FCF-based AVOID — Task 5 (`test_financial_rule_set_jpm`), Task 6 (`test_financial_signal_uses_sticker_only`).
5. The sticker must not move when today's price moves, and the truth table must contain a BUY and a LEAPS case — Task 6 (`test_sticker_is_price_invariant`, `test_signal_truth_table`).

---

### Task 1: Fixtures, symbol normalisation and the universe

**Files:**
- Create: `long_term/__init__.py` (empty docstring module)
- Create: `long_term/symbols.py`
- Create: `tests/fixtures/long_term/companyfacts_<SYM>.json` for AAPL, ASML, TSM, HOOD, MCD, HD, JPM, GOOGL, NVDA, CRWV (trimmed), `tests/fixtures/long_term/finnhub_<SYM>.json` for the same, `tests/fixtures/long_term/submissions_<SYM>.json` (only `name`, `sic`, `sicDescription`, `fiscalYearEnd`, `tickers`), `tests/fixtures/long_term/company_tickers.json` (the 10 entries plus BRK-B)
- Create: `scripts/long_term_fixtures.py` (the trimmer; reads raw files from a folder given on the command line, keeps only the tags in `long_term.edgar.ALL_TAGS` plus `dei`, forms 10-K/10-K/A/10-KT/10-Q/20-F/20-F/A/40-F, facts with `end` ≥ 2012-01-01)
- Test: `tests/test_long_term_symbols.py`

**Interfaces:**
- Produces: `canonical(symbol: Any) -> str` (uppercase, strip, `.`→`-`, `/`→`-`, "" for junk); `to_schwab(symbol) -> str` (`-`→`/`); `to_finnhub(symbol) -> str` (`-`→`.`); `to_edgar(symbol) -> str` (`-` stays); `SEED_TICKERS: tuple[str, ...]` (the sheet's 239 distinct tickers, comment with sheet id `1YmDZqMJUSSSQxJC-BkOYBHtY22ALUAmctO8q3XNcLJc` and date 2026-11-06); `universe(*, watchlist_path: Path | None = None, universe_path: Path | None = None) -> list[str]` = sorted canonical union of `config._load_watchlist()`-style file read, every list in `momx.board.load_universes(path=universe_path)`, and `SEED_TICKERS`.
- Raw fixture sources are already downloaded at `C:\Users\ganes\AppData\Local\Temp\claude\c68cdb9c3751baba\08154687-1551-492b-9122-a0ed2d0f8301\scratchpad\edgar\<SYM>.json`; Finnhub and submissions must be fetched once with the trimmer (network allowed for the script, not for tests).

- [ ] **Step 1: Write the failing tests**

```python
from long_term.symbols import canonical, to_finnhub, to_schwab, universe, SEED_TICKERS

def test_canonical_forms():
    assert canonical(" brk.b ") == "BRK-B" and canonical("BRK/B") == "BRK-B" and canonical(None) == ""
    assert to_schwab("BRK-B") == "BRK/B" and to_finnhub("BRK-B") == "BRK.B"

def test_seed_list_is_the_sheet():
    assert len(SEED_TICKERS) == 239 and "AAOI" in SEED_TICKERS and len(set(SEED_TICKERS)) == 239

def test_universe_unions_every_list(tmp_path):
    (tmp_path / "watchlist.txt").write_text("AAPL\nbrk.b\n# comment\n")
    (tmp_path / "momx_universe.json").write_text('{"schemaVersion":1,"lists":{"Watchlist":["MSFT"],"Mag7":["NVDA","AAPL"]}}')
    rows = universe(watchlist_path=tmp_path / "watchlist.txt", universe_path=tmp_path / "momx_universe.json")
    assert {"AAPL", "BRK-B", "MSFT", "NVDA", "AAOI"} <= set(rows) and rows == sorted(set(rows))
```

- [ ] **Step 2: Run them to see them fail** — `./.venv/Scripts/python.exe -m pytest tests/test_long_term_symbols.py -q -p no:cacheprovider` → ImportError.
- [ ] **Step 3: Implement `long_term/symbols.py`** with the Interfaces above. Read the watchlist file the way `config._load_watchlist` does (one symbol per line, `#` comments, env `WATCHLIST_FILE` default `<repo>/watchlist.txt`).
- [ ] **Step 4: Build the fixtures**: write `scripts/long_term_fixtures.py`, run it against the scratch folder; check sizes (each companyfacts fixture < 600 KB). The tag list `ALL_TAGS` is defined in Task 2 — for this task hard-code the same list in the script and leave a comment to import it once Task 2 lands.
- [ ] **Step 5: Run tests to verify they pass.**
- [ ] **Step 6: Commit** — `git commit -m "Long Term: symbols, universe union and test fixtures" -- long_term/__init__.py long_term/symbols.py tests/test_long_term_symbols.py tests/fixtures/long_term scripts/long_term_fixtures.py`

---

### Task 2: EDGAR fact selection, tag resolution, units and splits

**Files:**
- Create: `long_term/edgar.py`
- Test: `tests/test_long_term_edgar.py`

**Interfaces:**
- Produces:
  - `CONCEPTS: dict[str, list[str | tuple[str, ...]]]` for `revenue, net_income, eps, equity, debt, cash, cfo, capex, sbc, buybacks, dividends, interest, op_income, assets, shares` (a tuple entry is a sum recipe); `ALL_TAGS: frozenset[str]`; `ANNUAL_FORMS = {"10-K","10-K/A","10-KT","20-F","20-F/A","40-F"}`.
  - `@dataclass Fact(end: str, start: str | None, val: float, unit: str, tag: str, form: str, accn: str, filed: str)`
  - `annual_facts(facts_by_unit: dict, *, forms=ANNUAL_FORMS) -> dict[str, dict[str, Fact]]` unit → period-end → latest-filed Fact, duration 350–380 days (instants: no start).
  - `pick_unit(per_unit: dict[str, dict]) -> str | None` most annual years; USD only if ≥ local coverage.
  - `resolve_concept(gaap: dict, ifrs: dict, concept: str) -> tuple[dict[str, float], str, str, bool] | None` → (end → value, unit, primary tag, tag_break) using the per-company rule (most coverage primary; fill only when overlap agrees within 2%; revenue takes the larger of Revenues / RevenueFromContract… per year; recipes sum per year only when every part is present for that year, missing parts treated as 0 only for ShortTermBorrowings/CommercialPaper).
  - `infer_split_factors(eps_rows: list[Fact]) -> dict[str, float]` period-end → cumulative factor to multiply older values by (clean ratios {1.5, 2, 3, 4, 5, 7, 10, 20, 50} and their inverses, else ignore).
  - `apply_split(series: dict[str, float], factors: dict[str, float]) -> dict[str, float]`.
  - `@dataclass Snapshot(symbol, cik, name, sic, currency, annual: dict[str, dict[str, float]], units: dict[str, str], tags: dict[str, str], tag_breaks: list[str], ttm: dict[str, float], ttm_period_end: str | None, ttm_is_fy: bool, latest_annual_end: str | None, latest_period_end: str | None, fetched_at: str, split_factor: float, notes: list[str])`
  - `build_snapshot(symbol: str, cik: int, companyfacts: dict, *, sic: int | None, name: str, fetched_at: str) -> Snapshot` (TTM fields filled by Task 3's `ttm_flows`; this task leaves `ttm={}`).

- [ ] **Step 1: Write the failing tests** (fixtures from Task 1):

```python
def test_annual_rows_use_duration_not_fy(aapl):   # FY2025 10-K carries FY2024/2023 comparatives
    rows = annual_facts(aapl["facts"]["us-gaap"]["NetIncomeLoss"]["units"])["USD"]
    assert rows["2026-09-26"].val == 132175000000 and rows["2025-09-27"].form == "10-K"
    assert all(350 <= days(r) <= 380 for r in rows.values())

def test_revenue_stitches_asc606_rename(aapl):     # SalesRevenueNet to 2017, RevenueFromContract… from 2018
    series, unit, tag, broke = resolve_concept(gaap(aapl), {}, "revenue")
    assert unit == "USD" and len(series) >= 10 and not broke

def test_one_unit_per_series_tsm(tsm):             # TWD covers 8 years, USD 8 years too -> USD allowed only if >= local
    series, unit, *_ = resolve_concept({}, ifrs(tsm), "net_income")
    assert unit in ("TWD", "USD") and len({unit}) == 1

def test_debt_recipe_does_not_double_count(aapl):  # LongTermDebtNoncurrent + Current + CommercialPaper, not + LongTermDebt
    series, unit, tag, _ = resolve_concept(gaap(aapl), {}, "debt")
    assert abs(series["2026-09-26"] - (68208000000 + 9906000000 + cp("2026-09-26"))) < 1e6

def test_split_factor_googl(googl):               # FY2020 EPS 58.61 (2021 10-K) vs 2.93 (2023 10-K) -> 20
    factors = infer_split_factors(eps_facts(googl))
    assert factors["2020-12-31"] == pytest.approx(1/20) and factors["2019-12-31"] == pytest.approx(1/20)

def test_reverse_split_fixture():                 # synthetic: 1.25 (old) vs 10.0 (restated) -> factor 8
    facts = [Fact("2021-12-31","2021-01-01",1.25,"USD/shares","EarningsPerShareDiluted","10-K","a1","2022-02-01"),
             Fact("2021-12-31","2021-01-01",10.0,"USD/shares","EarningsPerShareDiluted","10-K","a2","2023-02-01")]
    assert infer_split_factors(facts)["2021-12-31"] == pytest.approx(8)

def test_snapshot_googl_eps_is_split_adjusted(googl):
    snap = build_snapshot("GOOGL", 1652044, googl, sic=7370, name="Alphabet", fetched_at="2026-11-06T03:00:00-05:00")
    assert snap.annual["2018-12-31"]["eps"] == pytest.approx(43.7 / 20, rel=1e-3)
    assert snap.latest_annual_end == "2025-12-31" and snap.currency == "USD"
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `long_term/edgar.py`** per the Interfaces and the spec's "Fact selection rules". Keep `CONCEPTS` as the single tag table (us-gaap and ifrs-full candidates side by side).
- [ ] **Step 4: Run tests to verify they pass.** Also run the resolution over all ten fixtures in a parametrised smoke test asserting each resolves `net_income` and `equity` with ≥ 5 years.
- [ ] **Step 5: Commit** — `git commit -m "Long Term: EDGAR fact selection, tag table, units and split inference" -- long_term/edgar.py tests/test_long_term_edgar.py`

---

### Task 3: EDGAR TTM, stamps, SIC and the paced client

**Files:**
- Modify: `long_term/edgar.py`
- Test: `tests/test_long_term_edgar.py` (append)

**Interfaces:**
- Produces:
  - `ttm_flows(facts_by_unit: dict, unit: str, latest_annual_end: str) -> tuple[float | None, str | None, bool]` → (value, period_end, is_fy) per the spec's YTD rule (same accession for prior YTD; ±7 days; 80–290 day durations).
  - `latest_quarterly_shares(facts: dict, unit="shares") -> float | None` (duration fact from the latest 10-Q/10-K, 80–100 days, else the annual).
  - `build_snapshot(...)` now fills `ttm` for revenue, net_income, cfo, capex, sbc, interest, op_income (flows) and debt, cash, equity, assets (latest instant of any form), plus `shares`, `ttm_period_end`, `ttm_is_fy`, `latest_period_end`.
  - `class EdgarClient(get: Callable[[str, dict], bytes] | None = None, *, user_agent: str | None = None, pace_per_sec: float = 5.0, sleep=time.sleep)` with `cik_map() -> dict[str, int]` (keys canonical with `-`), `company_facts(cik: int) -> dict`, `submission(cik: int) -> dict` (returns only name/sic/fiscalYearEnd/tickers), gzip handling, back-off on 403/429 (1, 2, 4, 8 s), raising `EdgarBlocked` after 3 consecutive 403s. `get` default uses urllib with the User-Agent.
  - `is_financial_sic(sic: int | None) -> bool` for 6000–6499, 6200–6299, 6300–6399.

- [ ] **Step 1: Write the failing tests**

```python
def test_ttm_from_fy_plus_ytd_minus_prior_ytd(aapl):   # AAPL FY ends Sep; latest 10-Q is Q1 (Dec) -> value, end, is_fy False
    value, end, is_fy = ttm_flows(aapl["facts"]["us-gaap"]["NetIncomeLoss"]["units"], "USD", "2026-09-26")
    assert end >= "2026-09-26" and isinstance(value, float)

def test_ttm_falls_back_to_fy_for_20f(asml):
    value, end, is_fy = ttm_flows(asml["facts"]["us-gaap"]["NetIncomeLoss"]["units"], "EUR", "2025-12-31")
    assert is_fy and end == "2025-12-31" and value == 9609400000

def test_prior_ytd_must_come_from_same_accession():  # synthetic facts: prior YTD from another accession is ignored -> FY fallback
    ...

def test_is_financial_sic():
    assert is_financial_sic(6021) and is_financial_sic(6211) and is_financial_sic(6331) and not is_financial_sic(7389)

def test_client_paces_and_backs_off():
    calls, clock = [], [0.0]
    def get(url, headers): calls.append(url); return b'{"ok":1}' if len(calls) != 2 else (_ for _ in ()).throw(HTTPError(url, 403, "", {}, None))
    client = EdgarClient(get, sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    client.company_facts(320193); client.company_facts(937966)
    assert clock[0] >= 1.0 and "User-Agent" not in calls  # back-off slept; headers carried the UA (assert via a captured headers list)

def test_client_aborts_after_three_403s(): ...
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the TTM functions, the client and `is_financial_sic`.
- [ ] **Step 4: Run the whole file; all pass.**
- [ ] **Step 5: Commit** — `-- long_term/edgar.py tests/test_long_term_edgar.py`

---

### Task 4: Finnhub extract

**Files:**
- Create: `long_term/finnhub.py`
- Test: `tests/test_long_term_finnhub.py`

**Interfaces:**
- Produces: `@dataclass FinnhubExtract(symbol: str, eps_annual: dict[str, float], pe_annual: dict[str, float], roic_annual: dict[str, float], eps_ttm: float | None, eps_incl_ttm: float | None, pe_ttm: float | None, roi_ttm: float | None, roe_ttm: float | None, market_cap_usd: float | None, debt_to_equity: float | None, fetched_at: str)`; `fetch_metrics(symbol: str, api_key: str, *, get=None, fetched_at: str) -> FinnhubExtract | None` (None when the key is empty, the call fails, or `metric` is empty); `class FinnhubPacer(per_minute=30, sleep=time.sleep)` used by the service; `marketCapitalization` is in millions → store dollars; `pe` nulls dropped; series keyed by `period` (YYYY-MM-DD).

- [ ] **Step 1: Write the failing tests** using `tests/fixtures/long_term/finnhub_NVDA.json`, `finnhub_JPM.json` (no eps series), `finnhub_CRWV.json` (null pe):

```python
def test_extract_nvda(nvda_fh):
    x = fetch_metrics("NVDA", "k", get=lambda url, t: nvda_fh, fetched_at="2026-11-06T03:00:00-05:00")
    assert x.eps_annual["2015-01-25"] == pytest.approx(0.028) and x.market_cap_usd > 1e12 and x.eps_incl_ttm == x.eps_ttm

def test_bank_without_eps_series(jpm_fh): assert fetch_metrics("JPM", "k", get=lambda u, t: jpm_fh, fetched_at="x").eps_annual == {}
def test_null_pe_dropped(crwv_fh): assert None not in fetch_metrics("CRWV", "k", get=lambda u, t: crwv_fh, fetched_at="x").pe_annual.values()
def test_no_key_returns_none(): assert fetch_metrics("AAPL", "", get=lambda u, t: {}, fetched_at="x") is None
def test_symbol_uses_dots(): captured = []; fetch_metrics("BRK-B", "k", get=lambda u, t: captured.append(u) or {"metric": {}}, fetched_at="x"); assert "symbol=BRK.B" in captured[0]
```

- [ ] **Step 2: Run to verify they fail.** · **Step 3: Implement.** · **Step 4: Run; pass.**
- [ ] **Step 5: Commit** — `-- long_term/finnhub.py tests/test_long_term_finnhub.py`

---

### Task 5: Metrics, growth windows and the checklist

**Files:**
- Create: `long_term/metrics.py`
- Test: `tests/test_long_term_metrics.py`

**Interfaces:**
- Consumes: `Snapshot` (Task 2/3), `FinnhubExtract` (Task 4), `is_financial_sic`.
- Produces:
  - `@dataclass Check(key: str, label: str, value: float | None, threshold: str, passed: bool | None, detail: str)` (`passed=None` = n/a).
  - `@dataclass Metrics(symbol, financial: bool, years: int, window: list[str], currency: str, roic_now: float | None, roic_series: dict[str, float], roe_now: float | None, net_income_ttm, fcf_ttm, owner_earnings_ttm, eps_ttm_edgar: float | None, eps_last_fy: float | None, eps_3yr_avg: float | None, growth: dict[str, float | None]  # sales, eps, equity, fcf, growth_source: dict[str, str], debt, cash, equity, assets, pe_hist_median: float | None, market_cap_usd, listing_factor: float | None, checks: dict[str, Check], big5: int, big5_na: int, rule1_business: bool, status: str | None, status_reason: str, young: bool)`.
  - `growth_window(annual: dict[str, dict[str, float]]) -> list[str]` (≤ 10 ends, start rule, gap rule).
  - `cagr(values: dict[str, float], window: list[str]) -> float | None` (3-year averaged endpoints, elapsed days, None when an endpoint ≤ 0 or < 5 rows).
  - `roic_series(annual) -> dict[str, float]` (net_income ÷ (equity + debt), guard ≤ 0 or < 10% of assets).
  - `adjusted_equity(annual, window) -> dict[str, float]` (equity + cumulative buybacks + dividends from the window start).
  - `listing_factor(price: float | None, fh: FinnhubExtract | None) -> float | None` (price ÷ (eps_ttm × pe_ttm); 1.0 when within 15%).
  - `compute_metrics(snapshot: Snapshot | None, fh: FinnhubExtract | None, *, today: date, is_etf: bool, price: float | None) -> Metrics`.
  - Check keys: `roic_now, roic_consistency, sales_growth, eps_growth, equity_growth, fcf_growth, debt, earning, history, eps_not_falling, sources`.

- [ ] **Step 1: Write the failing tests** (build snapshots from fixtures via `build_snapshot`; Finnhub via `fetch_metrics`):

```python
def test_cagr_uses_three_year_endpoints_and_elapsed_days():
    vals = {f"{y}-12-31": v for y, v in zip(range(2016, 2026), [10, 100, 10, 12, 14, 16, 18, 20, 22, 24])}
    assert cagr(vals, sorted(vals)) == pytest.approx(((22+24+20)/3 / ((10+100+10)/3)) ** (365.25/ (365.25*7)) - 1, rel=1e-6)
def test_cagr_undefined_on_negative_endpoint(): ...
def test_growth_window_skips_spac_years():  # revenue 50k, 60k, then 10M... -> window starts at the 10M year
def test_growth_window_refuses_gap_over_400_days(): ...
def test_roic_guard_negative_denominator():  # equity -5, debt 3 -> None
def test_roic_town_definition_mcd(mcd_snapshot): assert 0.15 < compute_metrics(mcd_snapshot, mcd_fh, today=TODAY, is_etf=False, price=300).roic_now < 0.35
def test_equity_growth_na_for_buybacks_hd(hd_snapshot, hd_fh): assert compute_metrics(...).checks["equity_growth"].passed in (None, True)
def test_zero_debt_requires_proof():  # snapshot with debt series empty, interest 5% of op income -> status CHECK_DATA "debt unresolved"
def test_zero_debt_accepted_with_proof():  # interest absent and fh.debt_to_equity 0.01 -> debt check passes
def test_financial_rule_set_jpm(jpm_snapshot, jpm_fh):
    m = compute_metrics(jpm_snapshot, jpm_fh, today=TODAY, is_etf=False, price=250)
    assert m.financial and m.roe_now and m.checks["fcf_growth"].passed is None and m.checks["debt"].passed is None and m.growth["eps"] is not None  # EDGAR fallback (no Finnhub eps series)
def test_eps_growth_prefers_finnhub_series(googl_snapshot, googl_fh): assert compute_metrics(...).growth_source["eps"] == "finnhub"
def test_sources_disagree_on_roic_sets_check_data(aapl_snapshot, aapl_fh):  # patch fh.roi_ttm = 5 -> status CHECK_DATA
def test_price_sanity_catches_scale_error(aapl_snapshot, aapl_fh):   # price 100 vs eps*pe 344 -> CHECK_DATA "price sanity"
def test_listing_factor_tsm(tsm_snapshot, tsm_fh): assert 0.1 < listing_factor(350.0, tsm_fh) < 0.2 and listing_factor(1322.0, asml_fh) == 1.0
def test_stale_by_annual_end(): ...   # annual end 16 months old -> STALE
def test_short_history_crwv(crwv_snapshot, crwv_fh): assert compute_metrics(...).status == "SHORT_HISTORY"
def test_young_eight_year_rule(hood_snapshot, hood_fh): assert compute_metrics(...).young is True   # 5 years
def test_etf_status(): assert compute_metrics(None, None, today=TODAY, is_etf=True, price=1).status == "ETF"
def test_no_filings_status(): assert compute_metrics(None, None, today=TODAY, is_etf=False, price=1).status == "NO_FILINGS"
def test_big5_counts_and_rule1_business(aapl_snapshot, aapl_fh): m = compute_metrics(...); assert 0 <= m.big5 <= 5 and isinstance(m.rule1_business, bool)
```

- [ ] **Step 2: Run to verify they fail.** · **Step 3: Implement `long_term/metrics.py`.** Status precedence: ETF → NO_FILINGS → STALE → SHORT_HISTORY → CHECK_DATA → None. `young = 5 <= years < 8`.
- [ ] **Step 4: Run; all pass.** Add a parametrised smoke test over all ten fixtures: `compute_metrics` never raises and `status in {None, "SHORT_HISTORY", "CHECK_DATA", "STALE"}`.
- [ ] **Step 5: Commit** — `-- long_term/metrics.py tests/test_long_term_metrics.py`

---

### Task 6: Valuation and signals

**Files:**
- Create: `long_term/valuation.py`
- Test: `tests/test_long_term_valuation.py`

**Interfaces:**
- Consumes: `Metrics`, `FinnhubExtract`.
- Produces:
  - `@dataclass Valuation(sticker: float | None, buffett: float | None, fair_value: float | None, g: float | None, g_source: str, future_pe: float | None, pe_source: str, sticker_eps: float | None, payback_years: float | None, explain: list[str])`.
  - `sticker_price(sticker_eps: float, g: float, pe_hist_median: float | None) -> tuple[float, float]` → (sticker, future_pe).
  - `buffett_value(owner_earnings_per_share: float, g_fcf: float, *, discount=0.10, terminal=0.03, years=10) -> float`.
  - `payback_years(owner_earnings_total: float, g: float, market_cap: float) -> float | None` (cap 50).
  - `valuation(m: Metrics, fh: FinnhubExtract | None) -> Valuation` (sticker EPS = min(eps_ttm_usd, 1.5 × eps_3yr_avg_usd) with `eps_ttm_usd = fh.eps_ttm × listing_factor`; Buffett per share = eps_ttm_usd × owner_earnings_ttm ÷ net_income_ttm, requires NI > 0; none for financial).
  - `price_guard(price: float | None, quote_time_ms: float | None, ref_price: float | None, *, now: datetime) -> tuple[bool, str]` (last-session rule: quote date ≥ previous trading day in ET).
  - `signal(m: Metrics, v: Valuation, *, price: float | None, price_ok: bool, mos: int) -> dict` with keys `signal, leaps, reason, buy_below, leaps_below, buy_zone_pct, safety_buy, quality`.

- [ ] **Step 1: Write the failing tests**

```python
def test_sticker_matches_hand_math():   # eps 8.98, g 0.12, median pe 30 -> future pe 24 -> 8.98*1.12**10*24/1.15**10
    s, pe = sticker_price(8.98, 0.12, 30.0); assert pe == 24 and s == pytest.approx(8.98 * 1.12**10 * 24 / 1.15**10, rel=1e-9)
def test_future_pe_floor_and_cap(): assert sticker_price(1, 0.0, 3)[1] == 5 and sticker_price(1, 0.15, 90)[1] == 30 and sticker_price(1, 0.15, None)[1] == 30
def test_sticker_is_price_invariant(aapl_metrics):   # valuation() with two different prices -> same sticker
def test_buffett_value_hand_math(): assert buffett_value(10.0, 0.10) == pytest.approx(sum(10*1.1**t/1.1**t for t in range(1, 11)) + (10*1.1**10*1.03/(0.10-0.03))/1.1**10)
def test_payback_years(): assert payback_years(100.0, 0.0, 800.0) == 8
def test_financial_signal_uses_sticker_only(jpm_metrics): v = valuation(jpm_metrics, jpm_fh); assert v.buffett is None and v.fair_value == v.sticker
def test_price_guard_null_zero_stale_far():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ET)
    assert price_guard(None, None, 100, now=now)[0] is False and price_guard(0, ms(now), 100, now=now)[0] is False
    assert price_guard(100, ms(now - timedelta(days=3)), 100, now=now)[0] is False and price_guard(140, ms(now), 100, now=now)[0] is False
    assert price_guard(100, ms(now - timedelta(hours=20)), 102, now=now)[0] is True   # last session
def test_signal_truth_table():   # synthetic Metrics (all checks pass, 10 years, roic 0.25) + Valuation(sticker 100, buffett 120)
    assert signal(m, v, price=40, price_ok=True, mos=50)["signal"] == "BUY"
    assert signal(m, v, price=70, price_ok=True, mos=50) == {..., "signal": "WAIT", "leaps": True, "buy_below": 50.0, "leaps_below": 80.0}
    assert signal(m, v, price=90, price_ok=True, mos=50)["leaps"] is False
    assert signal(m, v, price=40, price_ok=False, mos=50)["signal"] == "NO_PRICE"
    assert signal(young_m, v, price=40, price_ok=True, mos=50)["signal"] == "WAIT"     # 6 years
    assert signal(falling_m, v, price=40, price_ok=True, mos=50)["signal"] == "WAIT"   # eps_not_falling fails
    assert signal(avoid_m, v, price=40, price_ok=True, mos=50)["signal"] == "AVOID"    # roic_now fails
    assert signal(m, v, price=60, price_ok=True, mos=30)["signal"] == "BUY"             # 30% MOS: safety 70
def test_leaps_band_ignores_mos(): assert signal(m, v, price=75, price_ok=True, mos=30)["leaps"] == signal(m, v, price=75, price_ok=True, mos=50)["leaps"]
def test_safety_buy_is_lower_of_sticker_and_buffett(): ...
```

- [ ] **Step 2: Run to verify they fail.** · **Step 3: Implement `long_term/valuation.py`.** Reasons are short trading-language strings ("ROIC 9% < 15%", "losing money", "debt 5× FCF", "Check data: debt unresolved", "Short history (4 years)", "young: 6 years, BUY needs 8", "earnings falling").
- [ ] **Step 4: Run; all pass.** · **Step 5: Commit** — `-- long_term/valuation.py tests/test_long_term_valuation.py`

---

### Task 7: Store, refresher and payloads

**Files:**
- Create: `long_term/service.py`
- Test: `tests/test_long_term_service.py`

**Interfaces:**
- Consumes: everything above plus `momx.etf_list.is_etf`, `long_term.symbols.universe`.
- Produces:
  - `class Store(directory: Path)` with `load()`, `save()` (temp + `os.replace`, 5 retries on PermissionError, keeps `.prev.json`), `records: dict[str, dict]` (per ticker: `snapshot` as dict, `finnhub` as dict, `sic`, `ref_price`, `listing_factor`, `fetched_at`, `last_seen`), `summary: dict` (`rows` by symbol, `refreshed_at`).
  - `class LongTermService(*, directory: Path | None = None, edgar: EdgarClient | None = None, finnhub_get=None, finnhub_key: str | None = None, quotes_fn: Callable[[list[str]], dict] | None = None, now_fn=None, sleep=time.sleep)`:
    - `universe() -> list[str]`
    - `refresh(symbols: list[str] | None = None, *, force=False) -> dict` synchronous pass; skips tickers fetched < 3 days ago unless force; one EDGAR fetch per CIK; writes the store every 10 tickers; updates `progress`.
    - `progress -> dict | None` (`{"done", "total"}` while running).
    - `rows(mos: int, quotes: dict[str, dict]) -> list[dict]` and `page_payload(mos: int, quotes: dict) -> dict` (row and meta shapes exactly as in the spec's API section, camelCase keys).
    - `detail_payload(symbol: str, mos: int, quote: dict | None) -> dict | None` (checklist with values, ten-year table with unit, `explain` lines, cross-checks, sources, stamps, listing factor).
    - `request_refresh(*, now: datetime) -> dict` → `{"started": True}` or `{"deferredUntil": "16:05 ET"}` during 09:30–16:00 ET weekdays, `{"running": True}` if a pass is running.
    - `start_refresher() -> None` idempotent daemon thread: loop → if due (store empty or `refreshed_at` older than 24 h, or local time within 03:00–03:30 ET and not refreshed today) and not market hours → `refresh()`; sleep 60 s.
    - `drop_departed(now)` keeps records 30 days after `last_seen`.
  - `service_instance() -> LongTermService` module singleton (directory `artifacts/long_term` or env `AGX_LONG_TERM_DIR`; Finnhub key from env `FINNHUB_API_KEY`; quotes via `SchwabClient().get_quotes` with `to_schwab` symbols, chunked, cached 10 s inside the service).

- [ ] **Step 1: Write the failing tests** (fake `EdgarClient(get=fixture_getter)`, fake finnhub getter, fake quotes, `tmp_path` directory, frozen `now_fn`):

```python
def test_refresh_builds_rows_for_ten_fixtures(svc): r = svc.refresh(force=True); assert r["scored"] >= 8 and (svc.directory / "summary.json").exists()
def test_refresh_fetches_each_cik_once(svc_with_goog_and_googl): ...; assert edgar_calls.count(1652044) == 1
def test_refresh_skips_fresh_tickers(svc): svc.refresh(force=True); calls_before = len(edgar_calls); svc.refresh(); assert len(edgar_calls) == calls_before
def test_store_write_is_atomic_and_retries(tmp_path, monkeypatch): ...  # os.replace raising PermissionError twice then succeeding
def test_page_payload_shape_and_live_merge(svc):
    svc.refresh(force=True); page = svc.page_payload(50, {"AAPL": {"last_price": 344.17, "quote_time_ms": NOW_MS}})
    row = next(r for r in page["rows"] if r["symbol"] == "AAPL")
    assert set(row) >= {"symbol","name","price","signal","leaps","reason","buyBelow","leapsBelow","buyZonePct","safetyBuy","sticker","buffett","payback","roic","big5","quality","pe","debtToEquity","salesGrowth","epsGrowth","fcfGrowth","marketCap","filedEnd","years","financial","young"}
    assert page["meta"]["mos"] == 50 and "SPY" in page["meta"]["etfs"]
def test_missing_quote_is_no_price_never_buy(svc): page = svc.page_payload(50, {}); assert all(r["signal"] != "BUY" and r["leaps"] is False for r in page["rows"])
def test_detail_payload_has_explain_and_table(svc): d = svc.detail_payload("AAPL", 50, None); assert len(d["years"]) >= 10 and any("sticker" in line.lower() for line in d["explain"])
def test_request_refresh_deferred_in_market_hours(svc): assert "deferredUntil" in svc.request_refresh(now=ET_TIME(2026, 11, 6, 10, 0))
def test_refresher_runs_nightly_window_only(svc): ...  # now 03:05 ET Tue -> due; 10:00 ET -> not due; 17:00 ET with store 25 h old -> due
def test_departed_ticker_kept_30_days(svc): ...
```

- [ ] **Step 2: Run to verify they fail.** · **Step 3: Implement `long_term/service.py`.** · **Step 4: Run; all pass.**
- [ ] **Step 5: Commit** — `-- long_term/service.py tests/test_long_term_service.py`

---

### Task 8: API routes, lazy start and integration tests

**Files:**
- Modify: `api_server.py` — in `do_GET` next to the `/api/watchlist-quotes` branch (~line 23572): `GET /api/long-term` and `GET /api/long-term/<SYM>`; in `do_POST` near `/api/watchlist/add` (~line 24851): `POST /api/long-term/refresh`; in `main()` after `_news_auto_start()`: start the refresher when `AGX_LONG_TERM_REFRESH != "0"`.
- Modify: `tests/conftest.py` — add `_os.environ["AGX_LONG_TERM_REFRESH"] = "0"` and `_os.environ["AGX_LONG_TERM_DIR"] = str(_SCRATCH_DIR / "long_term")`.
- Test: `tests/test_long_term_api.py`

**Interfaces:**
- Consumes: `long_term.service.service_instance()`; `self._require_session_user()`, `self._require_admin_user()`, `self._send_json(HTTPStatus.OK, payload)`.
- Produces: the three routes. GET rows: `mos` query param (50 default, 30 allowed, else 400). Prices: `service.quotes_for(symbols)` (the service's cached Schwab call). Detail 404 `{"error": "unknown ticker"}` for a symbol outside the store. Refresh: admin only; returns `request_refresh(now=...)`.

- [ ] **Step 1: Write the failing integration tests**

```python
from api_harness import api_server, running_api, seed_user, RELAYED
from long_term import service as lt_service

@pytest.fixture
def svc(monkeypatch, tmp_path): s = LongTermService(directory=tmp_path, edgar=EdgarClient(get=fixture_getter), finnhub_get=fixture_fh, quotes_fn=lambda syms: {"AAPL": {"last_price": 344.17, "quote_time_ms": NOW_MS}}, now_fn=lambda: ET_NOW); s.refresh(force=True); monkeypatch.setattr(lt_service, "_INSTANCE", s); return s

def test_rows_round_trip(svc):
    with running_api() as api: status, body = api.get("/api/long-term?mos=50")
    assert status == 200 and body["meta"]["mos"] == 50 and any(r["symbol"] == "AAPL" and r["price"] == 344.17 for r in body["rows"])
def test_anonymous_tunnel_visitor_is_401(svc):
    with running_api() as api: status, _ = api.get("/api/long-term", headers=RELAYED); assert status == 401
def test_bad_mos_is_400(svc): ...
def test_detail_and_unknown(svc): ... assert api.get("/api/long-term/AAPL")[0] == 200 and api.get("/api/long-term/ZZZZ")[0] == 404
def test_null_quote_never_buy(svc_no_quotes): ... assert all(r["signal"] != "BUY" for r in body["rows"])
def test_refresh_needs_admin(svc):  # non-admin user -> 403; admin during market hours -> deferredUntil
```

- [ ] **Step 2: Run to verify they fail.** · **Step 3: Add the routes** (announce the `api_server.py` write in the cross-session channel first; keep the diff to the three branches plus the `main()` start line; import `long_term.service` lazily inside the branches so a broken package cannot take the server down).
- [ ] **Step 4: Run the file; then the harness guard tests `tests/test_cov_*guards*.py` if present; all pass.**
- [ ] **Step 5: Commit** — stage only your hunks of `api_server.py` (`git diff -- api_server.py > /tmp/lt.patch`, keep your hunks, `git apply --cached`), then `git commit -m "Long Term: /api/long-term routes and refresher start" -- tests/test_long_term_api.py tests/conftest.py` (the staged api_server hunks ride along).

---

### Task 9: Screen logic module `longTerm.js`

**Files:**
- Create: `frontend/src/longTerm.js`
- Test: `frontend/src/longTerm.test.js`

**Interfaces:**
- Produces (all pure, no DOM):
  - `MOS_OPTIONS = [50, 30]`, `PREFS_KEY = "longTermPrefs"`, `readPrefs(storage) -> {mos, sort: {key, dir}, tile}`, `writePrefs(storage, prefs)`.
  - `TILES = ["BUY", "LEAPS", "WAIT", "AVOID", "NODATA"]`, `tileCounts(rows) -> {BUY, LEAPS, WAIT, AVOID, NODATA}` (NODATA = every status other than BUY/WAIT/AVOID; LEAPS counts rows with `leaps`).
  - `priceGuard(row) -> boolean` (price > 0 finite; `quoteTimeMs` present; within 25% of `refPrice` when both present) and `guardRow(row) -> row` (signal → "NO_PRICE", leaps false when the guard fails).
  - `filterRows(rows, {tile, query}) -> rows` (query matches symbol prefix or name substring, case-insensitive).
  - `DEFAULT_SORT = {key: "default", dir: "desc"}`, `sortRows(rows, sort)` (default: BUY, then leaps, then buyZonePct desc, NODATA last; any column key sorts numerically with nulls last).
  - `SIGNAL_LABEL = {BUY: "BUY", WAIT: "WAIT", AVOID: "AVOID", NO_PRICE: "No price", ETF: "ETF", NO_FILINGS: "No filings", SHORT_HISTORY: "Short history", STALE: "Stale filing", CHECK_DATA: "Check data"}`, `signalTone(signal) -> "buy"|"wait"|"avoid"|"muted"`.
  - `formatMoney(v)`, `formatPct(v, digits=0)` (fractions in, "12%" out, "—" for null), `formatCap(v)` ("5.00T", "10.1B", "640M"), `formatGrowth(v)` ("+12%/yr"), `buyZoneBar(pct) -> {width: 0..100, tone}` (clamped at −100%).
  - `columns(viewport) -> [{key, label, align, hideOnPhone}]`; `cardFields(row) -> {...}` for the phone card.
  - `drawerSections(detail) -> [{title, lines}]` (checklist lines "✓ ROIC now 28% ≥ 15%", "✗ …", "n/a …").

- [ ] **Step 1: Write the failing tests** (node:test) covering each export with two or three rows of fixture data, including `priceGuard` on null/0/stale/far prices and `sortRows` default order; **Step 2: run `cd frontend && node --test src/longTerm.test.js` → fails**; **Step 3: implement**; **Step 4: pass**.
- [ ] **Step 5: Commit** — `-- frontend/src/longTerm.js frontend/src/longTerm.test.js`

---

### Task 10: The panel, styles, nav and view wiring

**Files:**
- Create: `frontend/src/LongTermPanel.jsx`
- Modify: `frontend/src/App.jsx` — import `LongTermPanel` and the `Landmark` icon from lucide-react; add `{ label: "Long Term", icon: Landmark }` to `optionNavItems` directly after the `Sectors` entry (~line 1182); add `if (view === "Long Term") return "Rule #1 / Buffett screen: ten years of filings, sticker price, safety price, BUY / WAIT / AVOID and a LEAPS flag for every ticker you follow";` to `viewDescription`; add the view block after the Sectors block (~line 31298):

```jsx
{activeView === "Long Term" && (
  <section className="long-term-view" data-testid="long-term-view">
    <LongTermPanel
      isAdmin={Boolean(authUser?.isAdmin)}
      onChart={(symbol) => { setMobileChartSection("chart"); openChartSignalChart(symbol, "D", "long-term"); }}
    />
  </section>
)}
```
- Modify: `frontend/src/learnContent.js` `LEARN_ELSEWHERE` — add after Sectors: `{ goTo: "Long Term", goToLabel: "Long Term", oneLiner: "The Buffett / Rule #1 screen: ten years of filings per ticker, a sticker price, a safety price, and BUY / WAIT / AVOID with a LEAPS flag." }`.
- Modify: `frontend/src/index.css` — append a `.long-term-*` block (view scroll container like `.sectors-view`; header; tiles; table with sticky header, tabular numerals, pills `.long-term-pill.is-buy/.is-wait/.is-avoid/.is-muted/.is-leaps`, Big 5 dots, buy-zone bar; drawer; `@media (max-width: 760px)` cards; no horizontal page scroll).
- Test: the existing `frontend/src/learnContent.test.js` (nav coverage assertion) and a JSX syntax check via the live app's esbuild.

**Interfaces:**
- Consumes: `longTerm.js` exports; `GET /api/long-term?mos=`, `GET /api/long-term/<SYM>`, `POST /api/long-term/refresh`.
- Produces: `<LongTermPanel isAdmin onChart />` with `data-testid`s: `long-term-panel`, `long-term-tile-<BUY|LEAPS|WAIT|AVOID|NODATA>`, `long-term-mos-<50|30>`, `long-term-search`, `long-term-refresh`, `long-term-table`, `long-term-row-<SYM>`, `long-term-card-<SYM>`, `long-term-drawer`, `long-term-footer`. Polls the rows every 30 s; MOS and tile remembered via `readPrefs/writePrefs`; header shows `meta.refreshedAt` in ET, the "Rule #1 buys are rare; WAIT rows show the price to buy at" line, and progress while `meta.refreshing`; empty state copy from the spec.

- [ ] **Step 1: Run the learn-content test to see it fail** (`node --test src/learnContent.test.js` once the nav item exists without the LEARN entry) — then add the entry.
- [ ] **Step 2: Build `LongTermPanel.jsx`** (page variant only; tiles → table/cards → drawer; `fetch` with no custom headers like `SectorHeatmapPanel`).
- [ ] **Step 3: Wire App.jsx, learnContent.js, index.css** (re-read regions right before each edit; another session edits these files).
- [ ] **Step 4: Verify**: `cd frontend && node --test src/longTerm.test.js src/learnContent.test.js src/mobileOverflowNavigation.test.js`; esbuild JSX transform of `App.jsx` and `LongTermPanel.jsx` succeeds; `npx vite build` succeeds (the watcher also rebuilds).
- [ ] **Step 5: Commit** — `-- frontend/src/LongTermPanel.jsx frontend/src/App.jsx frontend/src/learnContent.js` plus your `index.css` hunks staged via `git apply --cached`.

---

### Task 11: End-to-end spec, feature index, release note

**Files:**
- Create: `tests/e2e/long-term.e2e.test.mjs`
- Modify: `tests/FEATURE_TESTS.md` (append the row), `frontend/public/release-notes.json` (new top entry)

**Interfaces:**
- Consumes: `withApp({ view: "Long Term", fakeReads: {"GET /api/long-term": {json: FIXTURE_PAGE}, "GET /api/long-term/AAPL": {json: FIXTURE_DETAIL}} })`, `PHONE`, the `data-testid`s from Task 10. `FIXTURE_PAGE` = three rows (one BUY with leaps, one WAIT, one AVOID) plus an ETF in `meta.etfs`.

- [ ] **Step 1: Write the spec**: desktop — tiles show counts 1/1/1/1, clicking `long-term-tile-BUY` leaves one row, search "AA" filters, clicking the row opens `long-term-drawer` containing "Sticker", MOS 30 button re-fetches with `mos=30` (assert in `app.reads`), `app.pageErrors` empty; phone — `long-term-card-AAPL` visible, no sideways overflow, tap opens the drawer.
- [ ] **Step 2: Run** `node --test --test-concurrency=1 tests/e2e/long-term.e2e.test.mjs` (outside market hours) → passes against the live app on :5173 (the frontend from Task 10 must be served; the backend routes from Task 8 are not needed because reads are faked).
- [ ] **Step 3: Append to `tests/FEATURE_TESTS.md`**: `| Long Term: Rule #1 / Buffett screen (SEC filings + Finnhub, sticker price, Safety Buy, BUY/WAIT/AVOID + LEAPS) | tests/test_long_term_symbols.py, tests/test_long_term_edgar.py, tests/test_long_term_finnhub.py, tests/test_long_term_metrics.py, tests/test_long_term_valuation.py, tests/test_long_term_service.py, frontend/src/longTerm.test.js | tests/test_long_term_api.py | tests/e2e/long-term.e2e.test.mjs |`
- [ ] **Step 4: Release note** at the top of `frontend/public/release-notes.json`, ET timestamp, heading "New: Long Term page (Buffett / Rule #1 screen)" with items in trader language: what the page shows, where the numbers come from (SEC filings, nightly), BUY / WAIT / AVOID and LEAPS meaning, "BUY ≤ / LEAPS ≤" levels on WAIT rows, that buys are rare by design, the 50% / 30% safety switch, and that ETFs and a few delisted names are listed as not scored.
- [ ] **Step 5: Commit** — `-- tests/e2e/long-term.e2e.test.mjs tests/FEATURE_TESTS.md` plus the release-notes hunk via `git apply --cached` (other sessions add entries too).

---

### Task 12: Deploy, first pass and smoke test (operations, done by the orchestrator)

- [ ] Announce in the cross-session channel, then restart the backend outside market hours (kill the api_server parent PID; the watchdog respawns; confirm exactly one `api_server.py` process and a fresh CreationDate).
- [ ] Hit `GET /api/long-term` on :3001 as the local user; the first pass starts (store empty); watch `meta.refreshing` until done (~12 min); then audit `artifacts/long_term/summary.json`: count by signal, list every `CHECK_DATA` reason and every `debt unresolved`, compare AAPL / GOOGL / MSFT / NVDA / JPM / TSM / MCD rows against Finnhub and common sense; fix tag lists if a systematic miss shows (new unit test per fix).
- [ ] Browser smoke test on :5173 at desktop and 375 px: nav item, tiles, sort, filter, drawer, MOS switch, Chart button; no console errors.
- [ ] Run the full `tests/test_long_term_*.py`, `frontend/src/*.test.js` and the e2e spec after the close; report which layers ran.
