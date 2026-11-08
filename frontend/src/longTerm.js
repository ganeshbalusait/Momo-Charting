// Long Term page logic: Buffett / Rule #1 value screen. Pure functions only,
// no React and no DOM; storage is passed in so it can be faked in tests.

export const MOS_OPTIONS = [50, 30];
export const PREFS_KEY = "longTermPrefs";
export const DEFAULT_SORT = { key: "default", dir: "desc" };
// SETUP (2026-11-08): the LEAP setup rows (Monthly 50 EMA touch in an uptrend on a BUY or WAIT business).
export const TILES = ["BUY", "LEAPS", "SETUP", "WAIT", "AVOID", "NODATA"];

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
    // a tile is a filter for this visit only: the next visit starts with every row (Ganesh 11-07)
  } catch {
    return { mos: 50, sort: { ...DEFAULT_SORT }, tile: null };
  }
  return prefs;
}

export function writePrefs(storage, prefs) {
  try {
    const { tile, ...kept } = prefs || {}; // eslint-disable-line no-unused-vars
    storage.setItem(PREFS_KEY, JSON.stringify(kept));
  } catch {
    /* storage may be full, blocked or absent; the page works without it */
  }
}

// ---- tiles, guard, filter --------------------------------------------------

export function tileCounts(rows) {
  const counts = { BUY: 0, LEAPS: 0, SETUP: 0, WAIT: 0, AVOID: 0, NODATA: 0 };
  for (const r of rows || []) {
    if (r.signal === "BUY" || r.signal === "WAIT" || r.signal === "AVOID") counts[r.signal] += 1;
    else counts.NODATA += 1;
    if (r.leaps) counts.LEAPS += 1;
    if (isSetup(r)) counts.SETUP += 1;
  }
  return counts;
}

// A LEAP setup row: the server's setupPass (the Monthly 50 EMA touched this month
// in an uptrend), on a row the page still rates BUY or WAIT after its own price
// guard, and never from a stale monthly state. The LEAPS flag is not needed.
export function isSetup(row) {
  if (!row || row.setupPass !== true) return false;
  if (row.signal !== "BUY" && row.signal !== "WAIT") return false;
  return !(row.setup && typeof row.setup === "object" && row.setup.stale === true);
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
// feed itself is stale) can show no BUY, no LEAPS, no LEAP setup and no Buy Zone.
export function guardRow(row, { now = Date.now(), force = false } = {}) {
  if (!row || (!force && priceGuard(row, now))) return row;
  return { ...row, signal: PRICED.has(row.signal) ? "NO_PRICE" : row.signal, leaps: false, setupPass: false, buyZonePct: null };
}

function matchesTile(row, tile) {
  if (!tile) return true;
  if (tile === "LEAPS") return !!row.leaps;
  if (tile === "SETUP") return isSetup(row);
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
    setup: isSetup(row),
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
// leapsBelow, contract | null, deep | null, reason, expiries, fetchedAt,
// refreshedAt}. v2 (2026-11-06): `contract` is the ENTRY-style pick (the
// out-of-the-money call just under 0.20 delta with the most open interest, like
// the chain page's ENTRY badge); `deep` is the Rule #1 stock substitute (in the
// money near 0.75 delta) on the same expiry. The server picks both; these
// helpers only put them into words and never let a missing number through as
// NaN or undefined.

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

const pickOf = (payload, key) => {
  if (!payload || typeof payload !== "object") return null;
  const c = payload[key];
  return c && typeof c === "object" ? c : null;
};
const contractOf = (payload) => pickOf(payload, "contract"); // the ENTRY-style pick
const deepOf = (payload) => pickOf(payload, "deep"); // the stock-substitute pick
const reasonOf = (payload) => {
  const reason = payload && typeof payload === "object" && typeof payload.reason === "string" && payload.reason.trim();
  return reason || DASH;
};
const noContract = (payload) => `No contract: ${reasonOf(payload)}`;
const noEntry = (payload) => `No ENTRY contract: ${reasonOf(payload)}`;
const deltaText = (v) => (isNum(v) ? v.toFixed(2) : DASH);

export const DEEP_HEADING = "Deep call (stock substitute):";
export const DEEP_NONE = "Deep call: none";

// The deep pick on one compact line: "$200 call · delta 0.75 · ask $78.20 ·
// breakeven $278 (+16%) · OI 20,672", with " ⚠ thin" / " ⚠ wide spread" when flagged.
function deepLine(d) {
  let line = `${dollars(d.strike)} call · delta ${deltaText(d.delta)} · ask ${formatMoney(d.ask)} · breakeven ${wholeDollars(d.breakeven)} (${signedPct(d.breakevenPct)}) · OI ${count(d.openInterest)}`;
  if (d.thin === true) line += " ⚠ thin";
  if (d.wideSpread === true) line += " ⚠ wide spread";
  return line;
}

// One short line for the table cell / phone card:
// "Jan 2028 $450 C · ask $8.90 · BE $459 (+91%)" (the ENTRY pick); with no ENTRY
// pick but a deep one, "No ENTRY contract: <reason> · deep Jan 2028 $200 C ask $78.20";
// "No contract: <reason>" when there is neither; "" for no payload.
export function leapLine(payload) {
  if (!payload || typeof payload !== "object") return "";
  const c = contractOf(payload);
  if (!c) {
    const d = deepOf(payload);
    if (!d) return noContract(payload);
    return `${noEntry(payload)} · deep ${formatExpiry(d.expiry)} ${dollars(d.strike)} C ask ${formatMoney(d.ask)}`;
  }
  const line = `${formatExpiry(c.expiry)} ${dollars(c.strike)} C · ask ${formatMoney(c.ask)} · BE ${wholeDollars(c.breakeven)} (${signedPct(c.breakevenPct)})`;
  return c.thin === true ? `${line} ⚠ thin` : line;
}

// The lines of the drawer's "LEAP to buy" section: the ENTRY block (headline,
// quote, breakeven, time value, open interest, warnings), then the deep call
// on one line under its heading. With no ENTRY pick the block is the one
// "No ENTRY contract" line; with neither pick, "No contract: <reason>".
export function leapSection(payload) {
  const c = contractOf(payload);
  const d = deepOf(payload);
  if (!c && !d) return [noContract(payload)];
  const lines = [];
  if (c) {
    lines.push(
      `ENTRY-style · ${formatExpiry(c.expiry, { long: true })} (${isNum(c.dte) ? Math.round(c.dte) : DASH} days) · ${dollars(c.strike)} call · delta ${deltaText(c.delta)} · OI ${count(c.openInterest)}`,
      `Ask ${formatMoney(c.ask)} · bid ${formatMoney(c.bid)} · mid ${formatMoney(c.mid)} · ${wholeDollars(c.cost)} per contract`,
      `Breakeven ${formatMoney(c.breakeven)} · ${signedPct(c.breakevenPct)} from ${dollars(payload.price)}`,
      `In the money ${formatMoney(c.intrinsic)} · time value ${formatMoney(c.extrinsic)}`,
      `Open interest ${count(c.openInterest)} · volume ${count(c.volume)}`,
    );
    if (c.wideSpread === true) {
      const over = isNum(c.ask) && isNum(c.mid) && c.mid > 0 ? (c.ask - c.mid) / c.mid : null;
      lines.push(isNum(over) ? `⚠ wide spread (ask ${formatPct(over, 1)} over mid)` : "⚠ wide spread");
    }
    if (c.thin === true) {
      lines.push(`⚠ thin market: open interest ${count(c.openInterest)} — use a limit order near the mid, and expect a wide exit`);
    }
  } else lines.push(noEntry(payload));
  if (d) lines.push(DEEP_HEADING, deepLine(d));
  else lines.push(DEEP_NONE);
  return lines;
}

// Which rows get a leap fetch after a rows payload lands: the LEAP setup rows
// first (rare, and the trade he back-tested), then the LEAPS-flagged rows, each
// in the page's default order, capped so a long list never floods the chain.
export function leapOrder(rows, cap = LEAP_FETCH_CAP) {
  const list = (rows || []).filter(Boolean);
  const setups = list.filter((r) => isSetup(r));
  const flagged = list.filter((r) => r.leaps === true && !isSetup(r));
  return [...sortRows(setups, DEFAULT_SORT), ...sortRows(flagged, DEFAULT_SORT)].slice(0, cap).map((r) => r.symbol);
}

// Whether a row gets a contract at all ("LEAP to buy", the contract line).
export function wantsLeap(row) {
  return !!row && (row.leaps === true || isSetup(row));
}

// ---- LEAP setup (2026-11-08) -----------------------------------------------------
// row.setup (and detail.setup) from the server: {level, prevLevel, lastClose,
// lastMonth "YYYY-MM", uptrend, closeAbove, emaRising, month "YYYY-MM", monthLow,
// touched, touchDate "YYYY-MM-DD", pass, asOf, reason, stale?}. The rule is his
// back-test's (artifacts/scan_monthly_ema_touch.py): the 50 EMA of completed monthly
// closes, touched by a daily low this month, with last month's close above a
// rising EMA. These helpers only put it into words.

export const SETUP_NOTE = "Back-test 2005-2026 (model option prices): buying on this touch won 60% over 6 months vs 54% on any day. In-the-money calls won most (20% ITM: 64%); far out-of-the-money calls paid only when sold at double. No daily-close stop at the 50 EMA: it cut wins to about 45%. Not advice.";
const SETUP_SIGNALS = new Set(["BUY", "WAIT"]);

// "Nov" from "2026-11"; null for anything else.
function monthName(period) {
  const m = typeof period === "string" ? /^(\d{4})-(\d{2})/.exec(period.trim()) : null;
  const i = m ? Number(m[2]) - 1 : -1;
  return i >= 0 && i < 12 ? MONTHS[i] : null;
}

// "Nov 5" from "2026-11-05" (or an ISO time); null for anything else.
function dayName(iso) {
  const long = formatExpiry(typeof iso === "string" ? iso.slice(0, 10) : iso, { long: true });
  return long === DASH ? null : long.replace(/, \d{4}$/, "");
}

// The lines of the drawer's "LEAP setup" section. `source` is a row (its setup
// and, unless `signal` is given, its signal) or a setup dict (detail.setup) with
// the row's signal. [] when there is no setup state.
export function setupLines(source, signal) {
  if (!source || typeof source !== "object") return [];
  // a row carries a symbol / signal; a setup dict never does
  const fromRow = ["setup", "setupPass", "symbol", "signal"].some((k) => Object.prototype.hasOwnProperty.call(source, k));
  const s = fromRow ? source.setup : source;
  const sig = signal !== undefined ? signal : fromRow ? source.signal : undefined;
  if (!s || typeof s !== "object") return [];
  const lines = [];
  const level = s.level;
  const month = monthName(s.month);
  const monthLow = month ? `${month} low` : "Low";

  if (!isNum(level)) {
    const why = typeof s.reason === "string" && s.reason.trim() ? s.reason.trim() : "no monthly data yet";
    lines.push(`n/a Monthly 50 EMA: ${why}`);
  } else {
    const lvl = formatMoney(level);
    const touched = s.touched === true || (s.touched === undefined && isNum(s.monthLow) && s.monthLow <= level);
    if (touched) {
      const day = dayName(s.touchDate);
      lines.push(`✓ Monthly 50 EMA touched: ${monthLow} ${formatMoney(s.monthLow)} ≤ level ${lvl}${day ? ` (first touch ${day})` : ""}`);
    } else if (isNum(s.monthLow)) {
      const gap = level > 0 ? ` (${formatPct((s.monthLow - level) / level, 1)} below the low)` : "";
      lines.push(`✗ Monthly 50 EMA not touched this month: ${monthLow} ${formatMoney(s.monthLow)}, level ${lvl}${gap}`);
    } else {
      const why = typeof s.reason === "string" && s.reason.trim() ? s.reason.trim() : "no trading day this month yet";
      lines.push(`✗ Monthly 50 EMA not touched this month: ${why}, level ${lvl}`);
    }

    const closeAbove = typeof s.closeAbove === "boolean" ? s.closeAbove : isNum(s.lastClose) && s.lastClose > level;
    const rising = typeof s.emaRising === "boolean" ? s.emaRising : isNum(s.prevLevel) && level > s.prevLevel;
    const up = typeof s.uptrend === "boolean" ? s.uptrend : closeAbove && rising;
    const last = monthName(s.lastMonth) || "Last month";
    let where = "at";
    if (isNum(s.lastClose)) where = s.lastClose > level ? "above" : s.lastClose < level ? "below" : "at";
    const trend = isNum(s.prevLevel) ? `, which is ${rising ? "rising" : "falling"} (was ${formatMoney(s.prevLevel)})` : "";
    lines.push(`${up ? "✓" : "✗"} Uptrend: ${last} closed ${formatMoney(s.lastClose)} ${where} the 50 EMA ${lvl}${trend}`);
  }

  const ok = SETUP_SIGNALS.has(sig);
  const label = SIGNAL_LABEL[sig] || (typeof sig === "string" && sig) || DASH;
  lines.push(`${ok ? "✓" : "✗"} Long Term: ${label} (BUY or WAIT needed)`);

  // Stale only because a new month began (the server's staleReason "stale: state is for
  // 2026-10, not this month"): the checks above are last month's; "4 days" would be false.
  const rolled = typeof s.staleReason === "string" ? /state is for (\d{4}-\d{2})/.exec(s.staleReason) : null;
  if (s.stale === true && rolled) {
    const i = Number(rolled[1].slice(5, 7)) - 1;
    const full = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
      "October", "November", "December"][i] || rolled[1];
    lines.push(`⚠ These checks are for ${full}, not this month, so not a setup`);
  } else if (s.stale === true) {
    const day = dayName(s.asOf);
    lines.push(day
      ? `⚠ Monthly bars stale: last read ${day}, more than 4 days ago, so not a setup`
      : "⚠ Monthly bars stale: not read in the last 4 days, so not a setup");
  }
  return lines;
}

// A 200 answer with no contract for one of these reasons is a passing state of
// the chain feed, not a verdict on the row, so the page asks again: "chain
// unavailable" / "no chain" (Schwab blocked, dead token, client not configured;
// the server does not cache it) and LEAP_NO_DELTA_REASON (the chain came back,
// but Schwab hides every greek outside market hours, so there is no delta to
// pick an ENTRY by; the server's 5-minute chain copy lapses before the retry).
export const LEAP_NO_DELTA_REASON = "no deltas from the chain feed (hidden outside market hours)";
// "LEAPS not flagged" can only reach the page for a row it saw flagged (it never
// asks for other rows): a quote blip on the server dropped the flag for one
// request, so it is retried like a feed miss, never shown as a verdict.
export const LEAP_NOT_FLAGGED_REASON = "LEAPS not flagged";
export const LEAP_TRANSIENT_REASONS = new Set(["chain unavailable", "no chain", LEAP_NO_DELTA_REASON, LEAP_NOT_FLAGGED_REASON]);
export const LEAP_RETRY_MS = 60_000; // first retry; doubles per repeat, capped
export const LEAP_RETRY_MAX_MS = 10 * 60_000;

export function leapTransient(payload) {
  if (!payload || typeof payload !== "object" || contractOf(payload)) return false;
  return typeof payload.reason === "string" && LEAP_TRANSIENT_REASONS.has(payload.reason.trim());
}

// The sentence under a transient answer (the cell's Retry tooltip, the drawer):
// what the feed is doing, and that the page keeps asking on its own.
export function leapTransientNote(payload) {
  const reason = payload && typeof payload === "object" && typeof payload.reason === "string" ? payload.reason.trim() : "";
  if (reason === LEAP_NO_DELTA_REASON) {
    return "The chain feed is not sending deltas (Schwab hides them outside market hours); the page retries on its own.";
  }
  if (reason === LEAP_NOT_FLAGGED_REASON) {
    return "The quote blinked and the row lost its LEAPS flag for one answer; the page retries on its own.";
  }
  return "The chain feed did not answer; the page retries on its own.";
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
