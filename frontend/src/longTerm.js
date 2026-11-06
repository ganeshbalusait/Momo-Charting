// Long Term page logic: Buffett / Rule #1 value screen. Pure functions only,
// no React and no DOM; storage is passed in so it can be faked in tests.

export const MOS_OPTIONS = [50, 30];
export const PREFS_KEY = "longTermPrefs";
export const DEFAULT_SORT = { key: "default", dir: "desc" };
export const TILES = ["BUY", "LEAPS", "WAIT", "AVOID", "NODATA"];

export const SIGNAL_LABEL = {
  BUY: "BUY",
  WAIT: "WAIT",
  AVOID: "AVOID",
  NO_PRICE: "No price",
  ETF: "ETF",
  NO_FILINGS: "No filings",
  SHORT_HISTORY: "Short history",
  STALE: "Stale filing",
  CHECK_DATA: "Check data",
};

const PRICED = new Set(["BUY", "WAIT", "AVOID"]);
const DASH = "—";
const MINUS = "−";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

// ---- prefs ----------------------------------------------------------------

export function readPrefs(storage) {
  const prefs = { mos: 50, sort: { ...DEFAULT_SORT }, tile: null };
  try {
    const raw = storage && storage.getItem(PREFS_KEY);
    if (!raw) return prefs;
    const saved = JSON.parse(raw);
    if (!saved || typeof saved !== "object") return prefs;
    if (MOS_OPTIONS.includes(saved.mos)) prefs.mos = saved.mos;
    if (saved.sort && typeof saved.sort.key === "string" && saved.sort.key) {
      prefs.sort = { key: saved.sort.key, dir: saved.sort.dir === "asc" ? "asc" : "desc" };
    }
    if (TILES.includes(saved.tile)) prefs.tile = saved.tile;
  } catch {
    return { mos: 50, sort: { ...DEFAULT_SORT }, tile: null };
  }
  return prefs;
}

export function writePrefs(storage, prefs) {
  try {
    storage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage may be full, blocked or absent; the page works without it */
  }
}

// ---- tiles, guard, filter --------------------------------------------------

export function tileCounts(rows) {
  const counts = { BUY: 0, LEAPS: 0, WAIT: 0, AVOID: 0, NODATA: 0 };
  for (const r of rows || []) {
    if (r.signal === "BUY" || r.signal === "WAIT" || r.signal === "AVOID") counts[r.signal] += 1;
    else counts.NODATA += 1;
    if (r.leaps) counts.LEAPS += 1;
  }
  return counts;
}

const DAY_MS = 86400000;
const ET_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
});

// "YYYY-MM-DD" of an epoch-ms instant on the New York calendar.
export function etDate(ms) {
  const parts = {};
  for (const p of ET_DATE.formatToParts(new Date(ms))) parts[p.type] = p.value;
  return `${parts.year}-${parts.month}-${parts.day}`;
}

// The weekday before an ET date (Friday for a Saturday, Sunday or Monday).
// Holidays count as weekdays, as on the server: that errs on the safe side.
export function previousWeekday(isoDate) {
  const [y, m, d] = isoDate.split("-").map(Number);
  let t = Date.UTC(y, m - 1, d) - DAY_MS;
  while ([0, 6].includes(new Date(t).getUTCDay())) t -= DAY_MS;
  return new Date(t).toISOString().slice(0, 10);
}

// The server's price guard, run again in the browser: a positive price, a
// quote from the previous weekday (ET) or later and not from the future, and
// within 25% of the nightly reference. `now` is injectable for tests.
export function priceGuard(row, now = Date.now()) {
  if (!row) return false;
  if (!isNum(row.price) || row.price <= 0) return false;
  if (!isNum(row.quoteTimeMs) || row.quoteTimeMs <= 0) return false;
  if (!isNum(now)) return false;
  if (row.quoteTimeMs > now + DAY_MS) return false;
  if (etDate(row.quoteTimeMs) < previousWeekday(etDate(now))) return false;
  if (isNum(row.refPrice) && row.refPrice > 0) {
    if (Math.abs(row.price - row.refPrice) / row.refPrice > 0.25) return false;
  }
  return true;
}

// A row whose price fails the guard (or every row, with `force`, when the
// feed itself is stale) can show no BUY, no LEAPS and no Buy Zone.
export function guardRow(row, { now = Date.now(), force = false } = {}) {
  if (!row || (!force && priceGuard(row, now))) return row;
  return { ...row, signal: PRICED.has(row.signal) ? "NO_PRICE" : row.signal, leaps: false, buyZonePct: null };
}

function matchesTile(row, tile) {
  if (!tile) return true;
  if (tile === "LEAPS") return !!row.leaps;
  if (tile === "NODATA") return !PRICED.has(row.signal);
  return row.signal === tile;
}

export function filterRows(rows, { tile, query } = {}) {
  const q = String(query || "").trim().toLowerCase();
  return (rows || []).filter((r) => {
    if (!matchesTile(r, tile)) return false;
    if (!q) return true;
    return String(r.symbol || "").toLowerCase().startsWith(q)
      || String(r.name || "").toLowerCase().includes(q);
  });
}

// ---- sorting ----------------------------------------------------------------

const bySymbol = (a, b) => String(a.symbol || "").localeCompare(String(b.symbol || ""));

function defaultCompare(a, b) {
  const nodata = (r) => (PRICED.has(r.signal) ? 0 : 1);
  if (nodata(a) !== nodata(b)) return nodata(a) - nodata(b);
  const notBuy = (r) => (r.signal === "BUY" ? 0 : 1);
  if (notBuy(a) !== notBuy(b)) return notBuy(a) - notBuy(b);
  const notLeaps = (r) => (r.leaps ? 0 : 1);
  if (notLeaps(a) !== notLeaps(b)) return notLeaps(a) - notLeaps(b);
  const za = isNum(a.buyZonePct), zb = isNum(b.buyZonePct);
  if (za !== zb) return za ? -1 : 1;
  if (za && a.buyZonePct !== b.buyZonePct) return b.buyZonePct - a.buyZonePct;
  return bySymbol(a, b);
}

// What a column sorts by: the ROIC column shows ROE for financial rows, so it
// sorts by ROE there too.
export function sortValue(row, key) {
  if (key === "roic" && row && row.financial) return row.roe;
  return row ? row[key] : undefined;
}

export function sortRows(rows, sort = DEFAULT_SORT) {
  const list = [...(rows || [])];
  const key = sort && sort.key ? sort.key : "default";
  const sign = sort && sort.dir === "asc" ? 1 : -1;
  if (key === "default") {
    list.sort(defaultCompare); // fixed order, dir is ignored
    return list;
  }
  list.sort((a, b) => {
    const va = sortValue(a, key), vb = sortValue(b, key);
    const aNull = va === null || va === undefined || va === "" || (typeof va === "number" && !Number.isFinite(va));
    const bNull = vb === null || vb === undefined || vb === "" || (typeof vb === "number" && !Number.isFinite(vb));
    if (aNull || bNull) {
      if (aNull && bNull) return bySymbol(a, b);
      return aNull ? 1 : -1; // nulls last in either direction
    }
    let cmp;
    if (typeof va === "string" || typeof vb === "string") cmp = String(va).localeCompare(String(vb));
    else cmp = va - vb;
    if (cmp === 0) return bySymbol(a, b);
    return cmp * sign;
  });
  return list;
}

export function signalTone(signal) {
  if (signal === "BUY") return "buy";
  if (signal === "WAIT") return "wait";
  if (signal === "AVOID") return "avoid";
  return "muted";
}

// ---- formats ----------------------------------------------------------------

export function formatMoney(v) {
  if (!isNum(v)) return DASH;
  return `${v < 0 ? MINUS : ""}$${Math.abs(v).toFixed(2)}`;
}

export function formatPct(v, digits = 0) {
  if (!isNum(v)) return DASH;
  const s = (Math.abs(v) * 100).toFixed(digits);
  return `${v < 0 && Number(s) !== 0 ? MINUS : ""}${s}%`;
}

export function formatCap(v) {
  if (!isNum(v)) return DASH;
  const sign = v < 0 ? MINUS : "";
  const a = Math.abs(v);
  if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) {
    const b = a / 1e9;
    return `${sign}${b >= 100 ? b.toFixed(0) : b.toFixed(1).replace(/\.0$/, "")}B`;
  }
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(0)}M`;
  return `${sign}${a.toFixed(0)}`;
}

// EPS in its filing currency: "$6.08" for USD, "TWD 45.00" otherwise.
export function formatEps(v, unit) {
  if (!isNum(v)) return DASH;
  if (!unit || unit === "USD") return formatMoney(v);
  return `${v < 0 ? MINUS : ""}${unit} ${Math.abs(v).toFixed(2)}`;
}

// Payback years; the server caps the count at 50.
export function formatPayback(v) {
  if (!isNum(v)) return DASH;
  return v >= 50 ? "50+y" : `${v.toFixed(1)}y`;
}

export function formatGrowth(v) {
  if (!isNum(v)) return DASH;
  const s = (Math.abs(v) * 100).toFixed(0);
  if (v < 0 && Number(s) !== 0) return `${MINUS}${s}%/yr`;
  return `+${s}%/yr`;
}

export function buyZoneBar(pct) {
  if (!isNum(pct)) return { width: 0, tone: "muted" };
  const width = Math.min(100, Math.round(Math.abs(pct) * 100));
  return { width, tone: pct >= 0 ? "buy" : "wait" };
}

// ---- columns / cards ---------------------------------------------------------

const COLUMNS = [
  ["symbol", "Ticker", "left", false],
  ["name", "Name", "left", false],
  ["price", "Price", "right", false],
  ["signal", "Signal", "left", false],
  ["leaps", "LEAPS", "center", false],
  ["buyZonePct", "Buy Zone", "left", false],
  ["buyBelow", "BUY ≤", "right", true],
  ["leapsBelow", "LEAPS ≤", "right", true],
  ["sticker", "Sticker", "right", true],
  ["buffett", "Buffett", "right", true],
  ["payback", "Payback", "right", true],
  ["roic", "ROIC", "right", true],
  ["big5", "Big 5", "right", true],
  ["quality", "Quality", "right", true],
  ["pe", "P/E", "right", true],
  ["debtToEquity", "D/E", "right", true],
  ["salesGrowth", "Sales 10y", "right", true],
  ["epsGrowth", "EPS 10y", "right", true],
  ["fcfGrowth", "FCF 10y", "right", true],
  ["marketCap", "Mkt Cap", "right", true],
  ["filedEnd", "Filed", "right", true],
].map(([key, label, align, hideOnPhone]) => ({ key, label, align, hideOnPhone }));

export function columns(viewport = "desktop") {
  const cols = COLUMNS.map((c) => ({ ...c }));
  return viewport === "phone" ? cols.filter((c) => !c.hideOnPhone) : cols;
}

export function cardFields(row) {
  return {
    symbol: row.symbol,
    name: row.name,
    price: formatMoney(row.price),
    signalLabel: SIGNAL_LABEL[row.signal] || row.signal || DASH,
    tone: signalTone(row.signal),
    leaps: !!row.leaps,
    buyZone: buyZoneBar(row.buyZonePct),
    buyBelow: formatMoney(row.buyBelow),
    roic: formatPct(row.roic),
    big5: row.big5,
    big5Na: row.big5Na,
    reason: row.reason,
  };
}

// ---- detail drawer ------------------------------------------------------------

export function drawerSections(detail) {
  const d = detail || {};
  const unit = d.unit && d.unit !== "USD" ? `${d.unit} ` : "";
  const money = (v) => (isNum(v) ? `${unit}${formatCap(v)}` : DASH);

  const arr = (v) => (Array.isArray(v) ? v : []);
  const join = (parts, sep) => parts.filter((x) => x !== undefined && x !== null && x !== "").join(sep);
  // The server sends each check's value as a bare number (0.4406 for ROIC, 10 for years);
  // show it the way the threshold reads. A value that already is text passes through.
  const checkValue = (c) => {
    const v = c.value;
    if (!isNum(v)) return v === undefined || v === null || v === "" ? null : v;
    switch (c.key) {
      case "roic_now":
      case "roic_consistency":
        return formatPct(v);
      case "sales_growth":
      case "eps_growth":
      case "equity_growth":
      case "fcf_growth":
        return `${formatPct(v)}/yr`;
      case "debt":
        return `${v.toFixed(1)}× FCF`;
      case "earning":
        return `EPS ${formatEps(v, d.unit)}`;
      case "history":
        return `${Math.round(v)} years`;
      case "eps_not_falling":
        return `${v.toFixed(2)}× last FY`;
      default:
        return String(v);
    }
  };
  const checklist = arr(d.checks).map((c) => {
    if (c.passed === null || c.passed === undefined) {
      return `n/a ${c.label}${c.detail ? ` — ${c.detail}` : ""}`;
    }
    const body = [c.label, checkValue(c), c.threshold].filter((x) => x !== undefined && x !== null && x !== "").join(" ");
    return `${c.passed ? "✓" : "✗"} ${body}`;
  });

  const years = arr(d.years).map((y) => {
    const year = String(y.end || "").slice(0, 4);
    const eps = formatEps(y.eps, d.unit);
    const bare = (v) => (isNum(v) ? formatCap(v) : DASH);
    return `${year}: sales ${money(y.revenue)} · net ${bare(y.netIncome)} · EPS ${eps} · equity ${bare(y.equity)} · FCF ${bare(y.fcf)} · ROIC ${formatPct(y.roic)}`;
  });

  return [
    { title: "Checklist", lines: checklist },
    { title: "Valuation", lines: [...arr(d.explain)] },
    { title: "Ten years", lines: years },
    {
      title: "Cross-checks",
      lines: arr(d.crossChecks).map((c) => join([`${c.ok ? "✓" : "✗"} ${c.label ?? ""}`.trim(), c.detail], " — ")),
    },
    { title: "Sources", lines: arr(d.sources).map((s) => join([s.label, s.value], ": ")) },
  ];
}

// ---- page helpers --------------------------------------------------------------

// A rows payload is shown only for the MOS it was computed with, so a late
// answer for the old MOS never appears under the new label.
export function acceptsPayload(payload, mos) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.rows)) return false;
  const meta = payload.meta;
  if (!meta || typeof meta !== "object" || meta.mos === null || meta.mos === undefined || meta.mos === "") return false;
  return Number(meta.mos) === mos;
}

// The reason cell: a WAIT row priced above its safety buy shows the levels to
// buy at; a WAIT row held back for another reason (young, earnings falling or
// unknown) says so, plus the BUY level only when it has one; AVOID rows say
// why, and rows without a usable price or filings show their label (a guarded
// row keeps no stale BUY reason).
export const WAIT_PRICE_REASON = "price above safety buy";

export function reasonText(row) {
  if (!row) return DASH;
  if (row.signal === "WAIT") {
    if (!row.reason || row.reason === WAIT_PRICE_REASON) {
      const leaps = isNum(row.leapsBelow) ? ` · LEAPS ≤ ${formatMoney(row.leapsBelow)}` : "";
      return `BUY ≤ ${formatMoney(row.buyBelow)}${leaps}`;
    }
    return isNum(row.buyBelow) ? `${row.reason} · BUY ≤ ${formatMoney(row.buyBelow)}` : row.reason;
  }
  if (row.signal === "BUY" || row.signal === "AVOID") return row.reason || SIGNAL_LABEL[row.signal];
  const label = SIGNAL_LABEL[row.signal] || row.signal || DASH;
  if (row.signal === "NO_PRICE" || !row.reason) return label;
  const r = String(row.reason);
  // the server's reason may already carry the label ("Check data: …", "Stale: …")
  if (r.toLowerCase().startsWith(String(label).toLowerCase()) || (row.signal === "STALE" && r.startsWith("Stale"))) return r;
  return `${label}: ${r}`;
}

// Five Big 5 dots: passes first, then fails, then n/a (grey). Unknown → all n/a.
export function big5Dots(big5, big5Na) {
  if (!isNum(big5)) return ["na", "na", "na", "na", "na"];
  const pass = Math.max(0, Math.min(5, Math.round(big5)));
  const na = isNum(big5Na) ? Math.max(0, Math.min(5 - pass, Math.round(big5Na))) : 0;
  return Array.from({ length: 5 }, (_, i) => (i < pass ? "pass" : i < pass + na ? "na" : "fail"));
}

// "Nov 6, 03:12 AM ET" from an ISO string or epoch (s or ms).
export function formatEtStamp(value) {
  if (value === null || value === undefined || value === "") return DASH;
  const d = new Date(typeof value === "number" && value < 1e12 ? value * 1000 : value);
  if (Number.isNaN(d.getTime())) return DASH;
  const text = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: true,
  }).format(d);
  return `${text} ET`;
}

// ---- LEAP to buy ----------------------------------------------------------------
// The leap payload (GET /api/long-term/<SYM>/leap): {symbol, name, leaps, price,
// leapsBelow, contract | null, reason, expiries, fetchedAt, refreshedAt}. The
// server picks the contract; these helpers only put it into words and never
// let a missing number through as NaN or undefined.

export const LEAP_FETCH_CAP = 12;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "Jan 2028", or "Jan 15, 2028" with `long`; DASH for anything that is not a date.
export function formatExpiry(iso, { long = false } = {}) {
  if (typeof iso !== "string") return DASH;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso.trim());
  if (!m) return DASH;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const check = new Date(Date.UTC(y, mo - 1, d));
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d) return DASH;
  return long ? `${MONTHS[mo - 1]} ${d}, ${y}` : `${MONTHS[mo - 1]} ${y}`;
}

const thousands = (s) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

// "$200" for a whole number, "$202.50" otherwise, with thousands separators.
function dollars(v) {
  if (!isNum(v)) return DASH;
  const sign = v < 0 ? MINUS : "";
  const a = Math.abs(v);
  if (Number.isInteger(a)) return `${sign}$${thousands(String(a))}`;
  const [whole, cents] = a.toFixed(2).split(".");
  return `${sign}$${thousands(whole)}.${cents}`;
}

// "$262" (whole dollars) for the short table line.
const wholeDollars = (v) => (isNum(v) ? `${v < 0 ? MINUS : ""}$${thousands(String(Math.round(Math.abs(v))))}` : DASH);
const signedPct = (v) => (isNum(v) ? `${v > 0 ? "+" : ""}${formatPct(v)}` : DASH);
const count = (v) => (isNum(v) ? thousands(String(Math.round(v))) : DASH);

const contractOf = (payload) => {
  if (!payload || typeof payload !== "object") return null;
  const c = payload.contract;
  return c && typeof c === "object" ? c : null;
};
const noContract = (payload) => {
  const reason = payload && typeof payload === "object" && typeof payload.reason === "string" && payload.reason.trim();
  return `No contract: ${reason || DASH}`;
};

// One short line for the table cell / phone card:
// "Jan 2028 $200 C · ask $62.40 · BE $262 (+9%)"; "No contract: <reason>"; "" for no payload.
export function leapLine(payload) {
  if (!payload || typeof payload !== "object") return "";
  const c = contractOf(payload);
  if (!c) return noContract(payload);
  const line = `${formatExpiry(c.expiry)} ${dollars(c.strike)} C · ask ${formatMoney(c.ask)} · BE ${wholeDollars(c.breakeven)} (${signedPct(c.breakevenPct)})`;
  return c.thin === true ? `${line} ⚠ thin` : line;
}

// The lines of the drawer's "LEAP to buy" section.
export function leapSection(payload) {
  const c = contractOf(payload);
  if (!c) return [noContract(payload)];
  const lines = [
    `${formatExpiry(c.expiry, { long: true })} (${isNum(c.dte) ? Math.round(c.dte) : DASH} days) · ${dollars(c.strike)} call · delta ${isNum(c.delta) ? c.delta.toFixed(2) : DASH}`,
    `Ask ${formatMoney(c.ask)} · bid ${formatMoney(c.bid)} · mid ${formatMoney(c.mid)} · ${wholeDollars(c.cost)} per contract`,
    `Breakeven ${formatMoney(c.breakeven)} · ${signedPct(c.breakevenPct)} from ${dollars(payload.price)}`,
    `In the money ${formatMoney(c.intrinsic)} · time value ${formatMoney(c.extrinsic)}`,
    `Open interest ${count(c.openInterest)} · volume ${count(c.volume)}`,
  ];
  if (c.wideSpread === true) {
    const over = isNum(c.ask) && isNum(c.mid) && c.mid > 0 ? (c.ask - c.mid) / c.mid : null;
    lines.push(isNum(over) ? `⚠ wide spread (ask ${formatPct(over, 1)} over mid)` : "⚠ wide spread");
  }
  if (c.thin === true) {
    lines.push(`⚠ thin market: open interest ${count(c.openInterest)} — use a limit order near the mid, and expect a wide exit`);
  }
  return lines;
}

// Which rows get a leap fetch after a rows payload lands: the LEAPS-flagged
// rows in the page's default order, capped so a long list never floods the chain.
export function leapOrder(rows, cap = LEAP_FETCH_CAP) {
  const flagged = (rows || []).filter((r) => r && r.leaps === true);
  return sortRows(flagged, DEFAULT_SORT).slice(0, cap).map((r) => r.symbol);
}

// A 200 answer with no contract for one of these reasons is a passing state of
// the chain feed (Schwab blocked, dead token, client not configured), not a
// verdict on the row: the server does not cache it, so the page asks again.
export const LEAP_TRANSIENT_REASONS = new Set(["chain unavailable", "no chain"]);
export const LEAP_RETRY_MS = 60_000; // first retry; doubles per repeat, capped
export const LEAP_RETRY_MAX_MS = 10 * 60_000;

export function leapTransient(payload) {
  if (!payload || typeof payload !== "object" || contractOf(payload)) return false;
  return typeof payload.reason === "string" && LEAP_TRANSIENT_REASONS.has(payload.reason.trim());
}

// When a transient entry ({transient: true, at, tries}) is due for another read.
// tries counts the transient answers in a row: 1 min, 2, 4, 8, then every 10.
export function leapRetryDue(entry, now = Date.now()) {
  if (!entry || entry.transient !== true || entry.loading) return false;
  if (!isNum(entry.at) || !isNum(now)) return true;
  const tries = Math.max(1, Math.floor(isNum(entry.tries) ? entry.tries : 1));
  const wait = Math.min(LEAP_RETRY_MS * 2 ** (tries - 1), LEAP_RETRY_MAX_MS);
  return now - entry.at >= wait;
}
