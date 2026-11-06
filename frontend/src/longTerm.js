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
  const checklist = arr(d.checks).map((c) => {
    if (c.passed === null || c.passed === undefined) {
      return `n/a ${c.label}${c.detail ? ` — ${c.detail}` : ""}`;
    }
    const body = [c.label, c.value, c.threshold].filter((x) => x !== undefined && x !== null && x !== "").join(" ");
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
