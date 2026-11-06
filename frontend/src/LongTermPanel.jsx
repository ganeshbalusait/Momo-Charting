// Long Term page: the Buffett / Rule #1 value screen. All logic lives in
// longTerm.js; this file only fetches, holds state and draws.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, RefreshCw, X } from "lucide-react";

import {
  MOS_OPTIONS,
  TILES,
  SIGNAL_LABEL,
  DEFAULT_SORT,
  readPrefs,
  writePrefs,
  tileCounts,
  guardRow,
  filterRows,
  sortRows,
  signalTone,
  formatMoney,
  formatPct,
  formatCap,
  formatGrowth,
  formatEps,
  formatPayback,
  buyZoneBar,
  columns,
  cardFields,
  drawerSections,
  reasonText,
  big5Dots,
  formatEtStamp,
  acceptsPayload,
  leapLine,
  leapSection,
  leapOrder,
  leapTransient,
  leapRetryDue,
  LEAP_FETCH_CAP,
} from "./longTerm";

const POLL_MS = 30000;
// No accepted payload for 3 poll intervals: the prices on screen are stale,
// so rows are shown downgraded (no BUY, no LEAPS) until the feed returns.
const FEED_STALE_MS = 3 * POLL_MS;
const CLOCK_MS = 15000;
const PHONE_QUERY = "(max-width: 760px)";
const DASH = "—";
const TILE_LABEL = { BUY: "BUY", LEAPS: "LEAPS", WAIT: "WAIT", AVOID: "AVOID", NODATA: "No data" };
const TILE_TONE = { BUY: "buy", LEAPS: "leaps", WAIT: "wait", AVOID: "avoid", NODATA: "muted" };
const TILE_TITLE = {
  BUY: "Price at or under the safety price, Rule #1 business",
  LEAPS: "Price under the LEAPS level: long-dated calls worth a look",
  WAIT: "Good business, price still above the buy level",
  AVOID: "Not a Rule #1 business",
  NODATA: "No price, ETF, no filings, short history, stale or check data",
};
const GROWTH_KEYS = new Set(["salesGrowth", "epsGrowth", "fcfGrowth"]);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);

function safeStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function useIsPhone() {
  const [phone, setPhone] = useState(() => {
    try {
      return window.matchMedia(PHONE_QUERY).matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let mq;
    try {
      mq = window.matchMedia(PHONE_QUERY);
    } catch {
      return undefined;
    }
    const onChange = () => setPhone(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return phone;
}

function Pill({ tone, children, title }) {
  return <span className={`long-term-pill is-${tone}`} title={title}>{children}</span>;
}

function Big5({ big5, big5Na }) {
  const dots = big5Dots(big5, big5Na);
  const title = isNum(big5) ? `Big 5: ${big5} of 5 pass${big5Na ? `, ${big5Na} n/a` : ""}` : "Big 5: n/a";
  return (
    <span className="long-term-big5" title={title} aria-label={title}>
      {dots.map((d, i) => <i key={i} className={`is-${d}`} />)}
    </span>
  );
}

function BuyZone({ pct }) {
  const bar = buyZoneBar(pct);
  return (
    <span className="long-term-zone" title="How far the price is under (+) or over (−) the buy level">
      <span className="long-term-zone-track"><span className={`long-term-zone-fill is-${bar.tone}`} style={{ width: `${bar.width}%` }} /></span>
      <span className="long-term-zone-text">{isNum(pct) ? `${pct > 0 ? "+" : ""}${formatPct(pct)}` : DASH}</span>
    </span>
  );
}

// The contract line under the LEAPS pill (table cell and phone card). The
// Retry control is a span, not a button: the phone card is itself a button.
// `queued`: the page is about to read this row on its own (inside the cap or
// its drawer is open); otherwise a row with no entry offers a "Find contract"
// link instead of saying "finding…" for a read that will never come.
function LeapLine({ symbol, entry, onRetry, queued = true }) {
  const act = (e) => {
    e.stopPropagation();
    e.preventDefault();
    onRetry?.();
  };
  const link = (label, title) => (
    <span
      role="button"
      tabIndex={0}
      className="long-term-link"
      title={title}
      onClick={act}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") act(e);
      }}
    >
      {label}
    </span>
  );
  let text;
  if (!entry && !queued) {
    text = link("Find contract", `Only the first ${LEAP_FETCH_CAP} LEAPS rows are looked up on their own; click to look this one up`);
  } else if (!entry || entry.loading) text = "finding…";
  else if (entry.error) {
    text = (
      <>
        contract not available{" "}
        {link("Retry")}
      </>
    );
  } else if (entry.transient) {
    text = (
      <>
        {leapLine(entry.payload)}{" "}
        {link("Retry", "The chain feed did not answer; the page retries on its own")}
      </>
    );
  } else text = leapLine(entry.payload);
  return <small className="long-term-leap" data-testid={`long-term-leap-${symbol}`}>{text}</small>;
}

function cellContent(key, row, leap = {}) {
  const local = row.currency && row.currency !== "USD";
  switch (key) {
    case "symbol":
      return (
        <span className="long-term-sym">
          <b>{row.symbol}</b>
          {row.young ? <em className="long-term-tag" title={`${row.years ?? "?"} fiscal years: capped at WAIT`}>young</em> : null}
        </span>
      );
    case "name":
      return <span className="long-term-name" title={row.name || ""}>{row.name || DASH}</span>;
    case "price":
    case "buyBelow":
    case "leapsBelow":
    case "sticker":
    case "buffett":
      return formatMoney(row[key]);
    case "signal":
      return <Pill tone={signalTone(row.signal)}>{SIGNAL_LABEL[row.signal] || row.signal || DASH}</Pill>;
    case "leaps":
      return row.leaps ? (
        <>
          <Pill tone="leaps">LEAPS</Pill>
          <LeapLine symbol={row.symbol} entry={leap.entry} onRetry={leap.onRetry} queued={leap.queued} />
        </>
      ) : <span className="long-term-dim">{DASH}</span>;
    case "buyZonePct":
      return <BuyZone pct={row.buyZonePct} />;
    case "payback":
      return formatPayback(row.payback);
    case "roic":
      return row.financial ? (
        <span title="Financial: ROE replaces ROIC">{formatPct(row.roe)} <em className="long-term-tag">FIN</em></span>
      ) : formatPct(row.roic);
    case "big5":
      return <Big5 big5={row.big5} big5Na={row.big5Na} />;
    case "quality":
      return row.quality ? <span className={row.quality === "HIGH" ? "long-term-good" : "long-term-dim"}>{row.quality}</span> : DASH;
    case "pe":
      return isNum(row.pe) ? row.pe.toFixed(1) : DASH;
    case "debtToEquity":
      return isNum(row.debtToEquity) ? row.debtToEquity.toFixed(2) : DASH;
    case "marketCap":
      return isNum(row.marketCap) ? `$${formatCap(row.marketCap)}` : DASH;
    case "filedEnd":
      return <span title={isNum(row.years) ? `${row.years} fiscal years` : ""}>{row.filedEnd || DASH}</span>;
    default:
      if (GROWTH_KEYS.has(key)) {
        return (
          <span>
            {formatGrowth(row[key])}
            {local && isNum(row[key]) ? <small className="long-term-dim"> (local ccy)</small> : null}
          </span>
        );
      }
      return row[key] ?? DASH;
  }
}

function markClass(line) {
  if (line.startsWith("✓")) return "is-ok";
  if (line.startsWith("✗")) return "is-no";
  if (line.startsWith("n/a")) return "is-na";
  if (line.startsWith("⚠")) return "is-warn";
  return "";
}

function LineList({ lines, marks = false }) {
  if (!lines.length) return <p className="long-term-dim">{DASH}</p>;
  return (
    <ul className="long-term-lines">
      {lines.map((line, i) => <li key={i} className={marks ? markClass(line) : ""}>{line}</li>)}
    </ul>
  );
}

function YearsTable({ detail }) {
  const years = Array.isArray(detail?.years) ? detail.years : [];
  if (!years.length) return <p className="long-term-dim">{DASH}</p>;
  const big = (v) => (isNum(v) ? formatCap(v) : DASH);
  return (
    <div className="long-term-years-wrap">
      <table className="long-term-years">
        <thead>
          <tr><th>FY</th><th>Revenue</th><th>Net inc</th><th>EPS</th><th>Equity</th><th>FCF</th><th>ROIC</th></tr>
        </thead>
        <tbody>
          {years.map((y) => (
            <tr key={y.end}>
              <td>{String(y.end || "").slice(0, 4)}</td>
              <td>{big(y.revenue)}</td>
              <td>{big(y.netIncome)}</td>
              <td>{formatEps(y.eps, detail?.unit)}</td>
              <td>{big(y.equity)}</td>
              <td>{big(y.fcf)}</td>
              <td>{formatPct(y.roic)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// "LEAP to buy": only for a LEAPS-flagged row; independent of the filings
// detail so a slow EDGAR read never hides the contract (and the other way round).
function LeapSection({ row, entry, onRetry }) {
  let body;
  if (!entry || entry.loading) body = <p className="long-term-dim">finding…</p>;
  else if (entry.error) {
    body = (
      <p className="long-term-dim">
        contract not available{" "}
        <button type="button" className="long-term-link" onClick={onRetry}>Retry</button>
      </p>
    );
  } else if (entry.transient) {
    body = (
      <>
        <LineList lines={leapSection(entry.payload)} marks />
        <p className="long-term-dim">
          The chain feed did not answer; the page retries on its own.{" "}
          <button type="button" className="long-term-link" onClick={onRetry}>Retry now</button>
        </p>
      </>
    );
  } else body = <LineList lines={leapSection(entry.payload)} marks />;
  return (
    <section className="long-term-drawer-leap" data-testid="long-term-drawer-leap">
      <h4>LEAP to buy</h4>
      {body}
      <p className="long-term-note">
        Shown because the price is under the LEAPS level {formatMoney(row.leapsBelow)}. The page picks the contract; the trade is yours.
      </p>
    </section>
  );
}

function Drawer({ row, entry, leap, onChart, onClose, onRetry, onRetryLeap, width }) {
  const detail = entry?.detail;
  const sections = useMemo(() => (detail ? drawerSections(detail) : null), [detail]);
  const byTitle = (t) => sections?.find((s) => s.title === t)?.lines || [];
  return (
    <div className="long-term-drawer" data-testid="long-term-drawer" style={width ? { width } : undefined}>
      <header>
        <b>Why {row.symbol}</b>
        <span className="long-term-dim long-term-drawer-name">{row.name}</span>
        <span className="long-term-drawer-reason">{reasonText(row)}</span>
        <button type="button" className="long-term-btn is-accent" onClick={() => onChart(row.symbol)}>
          <BarChart3 size={13} /> Chart
        </button>
        <button type="button" className="long-term-icon-btn" onClick={onClose} aria-label="Close">
          <X size={14} />
        </button>
      </header>
      {row.leaps ? <LeapSection row={row} entry={leap} onRetry={onRetryLeap} /> : null}
      {!entry || entry.loading ? (
        <p className="long-term-dim">Loading the filings…</p>
      ) : entry.error ? (
        <p className="long-term-dim">
          Details are not available yet.{" "}
          <button type="button" className="long-term-link" onClick={onRetry}>Retry</button>
        </p>
      ) : (
        <div className="long-term-drawer-grid">
          <section>
            <h4>Checklist</h4>
            <LineList lines={byTitle("Checklist")} marks />
          </section>
          <section>
            <h4>Valuation</h4>
            <LineList lines={byTitle("Valuation")} />
          </section>
          <section>
            <h4>Ten years{detail?.unit ? <small> · {detail.unit}</small> : null}</h4>
            <YearsTable detail={detail} />
          </section>
          <section>
            <h4>Cross-checks</h4>
            <LineList lines={byTitle("Cross-checks")} marks />
            <h4>Sources</h4>
            <LineList lines={byTitle("Sources")} />
          </section>
        </div>
      )}
    </div>
  );
}

function Card({ row, open, onToggle, leap, onRetryLeap, leapQueued }) {
  const f = cardFields(row);
  return (
    <button
      type="button"
      className={`long-term-card is-${f.tone}${open ? " is-open" : ""}`}
      data-testid={`long-term-card-${row.symbol}`}
      aria-expanded={open}
      onClick={onToggle}
    >
      <span className="long-term-card-top">
        <b>{f.symbol}</b>
        <span className="long-term-card-name">{f.name}</span>
        <span className="long-term-card-price">{f.price}</span>
      </span>
      <span className="long-term-card-mid">
        <Pill tone={f.tone}>{f.signalLabel}</Pill>
        {f.leaps ? <Pill tone="leaps">LEAPS</Pill> : null}
        <BuyZone pct={row.buyZonePct} />
      </span>
      {f.leaps ? <LeapLine symbol={row.symbol} entry={leap} onRetry={onRetryLeap} queued={leapQueued} /> : null}
      <span className="long-term-card-bottom">
        <span>BUY ≤ <b>{f.buyBelow}</b></span>
        <span>{row.financial ? "ROE" : "ROIC"} <b>{row.financial ? formatPct(row.roe) : f.roic}</b></span>
        <Big5 big5={f.big5} big5Na={f.big5Na} />
      </span>
      <span className="long-term-card-reason">{reasonText(row)}</span>
    </button>
  );
}

export default function LongTermPanel({ isAdmin = false, onChart = () => {} }) {
  const [prefs, setPrefs] = useState(() => readPrefs(safeStorage()));
  const [data, setData] = useState(null); // { rows, meta }
  const [failed, setFailed] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [query, setQuery] = useState("");
  const [openSym, setOpenSym] = useState(null);
  const [details, setDetails] = useState({});
  // LEAP to buy, keyed by symbol: { epoch, loading } | { epoch, payload } | { epoch, error }
  // | { epoch, payload, transient: true, at, tries } (the chain feed did not answer; retried).
  // An entry counts only for the MOS and filings refresh it was fetched under.
  const [leaps, setLeaps] = useState({});
  const [refreshMsg, setRefreshMsg] = useState("");
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [wrapWidth, setWrapWidth] = useState(0);
  const [acceptedAt, setAcceptedAt] = useState(0); // Date.now() of the last accepted rows payload
  const [clock, setClock] = useState(() => Date.now());
  const wrapRef = useRef(null);
  const phone = useIsPhone();
  const mos = prefs.mos;

  const updatePrefs = useCallback((patch) => {
    setPrefs((current) => ({ ...current, ...patch }));
  }, []);

  // Save prefs after they change (not inside the state updater, which React
  // may run twice). The first render only read them, so it is skipped.
  const prefsLoaded = useRef(false);
  useEffect(() => {
    if (!prefsLoaded.current) {
      prefsLoaded.current = true;
      return;
    }
    const storage = safeStorage();
    if (storage) writePrefs(storage, prefs);
  }, [prefs]);

  // Rows: on open, on MOS change, every 30 s while the page is visible.
  // A poll is skipped while a request is still out (a slow backend still gets
  // to answer); only unmount, a MOS change or Retry abort it. Rows are kept
  // with the MOS they were computed for and only shown under that MOS.
  useEffect(() => {
    let alive = true;
    let controller = null;
    let inFlight = false;
    setFailed(false);
    const load = async () => {
      if (inFlight) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      const ctrl = new AbortController();
      controller = ctrl;
      inFlight = true;
      try {
        const response = await fetch(`/api/long-term?mos=${mos}`, { signal: ctrl.signal });
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
        if (!alive || ctrl.signal.aborted) return;
        if (!response.ok || !acceptsPayload(payload, mos)) {
          setFailed(true);
          return;
        }
        setData({ rows: payload.rows, meta: payload.meta, mos });
        setAcceptedAt(Date.now());
        setClock(Date.now());
        setFailed(false);
      } catch {
        if (alive && !ctrl.signal.aborted) setFailed(true);
      } finally {
        inFlight = false;
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      controller?.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [mos, reloadTick]);

  // A slow clock re-runs the price guard (quote age) and the stale-feed check.
  useEffect(() => {
    const timer = setInterval(() => setClock(Date.now()), CLOCK_MS);
    return () => clearInterval(timer);
  }, []);

  // The drawer must fit the visible table width, not the scrolled table.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const measure = () => setWrapWidth(Math.floor(el.clientWidth));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [phone, data]);

  const loadDetail = useCallback((symbol) => {
    const key = `${symbol}:${mos}`;
    setDetails((current) => ({ ...current, [key]: { loading: true } }));
    fetch(`/api/long-term/${encodeURIComponent(symbol)}?mos=${mos}`)
      .then(async (response) => {
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
        const ok = response.ok && payload && typeof payload === "object" && !payload.error;
        setDetails((current) => ({ ...current, [key]: ok ? { detail: payload } : { error: true } }));
      })
      .catch(() => setDetails((current) => ({ ...current, [key]: { error: true } })));
  }, [mos]);

  const toggleRow = useCallback((symbol) => {
    setOpenSym((current) => (current === symbol ? null : symbol));
  }, []);

  useEffect(() => {
    if (!openSym) return;
    // Fetch once per symbol and MOS; a failed fetch waits for the Retry link.
    if (!details[`${openSym}:${mos}`]) loadDetail(openSym);
  }, [openSym, mos, details, loadDetail]);

  // Rows for another MOS are never shown: after a switch the page shows the
  // loading state (or "not available" if the fetch fails) until a match lands.
  const current = data && data.mos === mos ? data : null;
  const refreshedAt = current?.meta?.refreshedAt ?? null;
  // A finished refresh changes the filings: drop the cached Why drawers.
  useEffect(() => {
    setDetails({});
  }, [refreshedAt]);

  const feedStale = !!current && acceptedAt > 0 && clock - acceptedAt > FEED_STALE_MS;
  const guarded = useMemo(
    () => (current?.rows || []).map((r) => guardRow(r, { now: clock, force: feedStale })),
    [current, clock, feedStale],
  );
  const counts = useMemo(() => tileCounts(guarded), [guarded]);

  // LEAP to buy: one fetch per flagged symbol per MOS and filings refresh.
  // A late answer from an earlier epoch is dropped, never shown.
  const leapEpoch = `${mos}|${refreshedAt ?? ""}`;
  const leapEntry = useCallback((symbol) => {
    const e = leaps[symbol];
    return e && e.epoch === leapEpoch ? e : undefined;
  }, [leaps, leapEpoch]);
  const loadLeap = useCallback((symbol) => {
    const epoch = leapEpoch;
    const put = (entry) => setLeaps((cur) => {
      const was = cur[symbol];
      if (was && was.epoch !== epoch && entry.loading !== true) return cur; // the page moved on
      // Transient answers in a row (for the retry backoff); a real answer resets the count.
      const tries = was && was.epoch === epoch && isNum(was.tries) ? was.tries : 0;
      let next;
      if (entry.loading) next = { epoch, loading: true, tries };
      else if (entry.payload && leapTransient(entry.payload)) {
        next = { epoch, payload: entry.payload, transient: true, at: Date.now(), tries: tries + 1 };
      } else next = { epoch, ...entry };
      return { ...cur, [symbol]: next };
    });
    put({ loading: true });
    fetch(`/api/long-term/${encodeURIComponent(symbol)}/leap?mos=${mos}`)
      .then(async (response) => {
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }
        const ok = response.ok && payload && typeof payload === "object" && !payload.error;
        put(ok ? { payload } : { error: true });
      })
      .catch(() => put({ error: true }));
  }, [mos, leapEpoch]);
  // The rows the page reads on its own: the flagged rows in default order, capped.
  // Rows that are not flagged never cause a chain read; flagged rows past the
  // cap offer a "Find contract" link and are read when their drawer opens.
  const leapAuto = useMemo(() => new Set(leapOrder(guarded)), [guarded]);
  // A row needs a read when it has no entry for this epoch, or its last answer
  // was transient ("chain unavailable" / "no chain") and the backoff has passed.
  const leapDue = useCallback((symbol) => {
    const e = leapEntry(symbol);
    return !e || leapRetryDue(e, clock);
  }, [leapEntry, clock]);
  // After a rows payload is accepted, and on every clock tick for the retries.
  useEffect(() => {
    if (!current) return;
    const wanted = new Set(leapAuto);
    // A flagged row looked up by hand ("Find contract" or its drawer) whose
    // answer was transient is retried on its own too, as the Retry tooltip says.
    for (const r of guarded) {
      if (r.leaps === true && !wanted.has(r.symbol)) {
        const e = leapEntry(r.symbol);
        if (e && e.transient) wanted.add(r.symbol);
      }
    }
    for (const symbol of wanted) {
      if (leapDue(symbol)) loadLeap(symbol);
    }
  }, [current, guarded, leapAuto, leapEntry, leapDue, loadLeap]);
  // Opening the drawer of a flagged row past the cap fetches its contract too.
  useEffect(() => {
    if (!openSym) return;
    const row = guarded.find((r) => r.symbol === openSym);
    if (row && row.leaps === true && leapDue(openSym)) loadLeap(openSym);
  }, [openSym, guarded, leapDue, loadLeap]);
  const visible = useMemo(
    () => sortRows(filterRows(guarded, { tile: prefs.tile, query }), prefs.sort),
    [guarded, prefs.tile, prefs.sort, query],
  );
  const cols = useMemo(() => columns("desktop"), []);

  const meta = current?.meta || {};
  const refreshing = meta.refreshing && typeof meta.refreshing === "object" ? meta.refreshing : null;
  const etfs = Array.isArray(meta.etfs) ? meta.etfs : [];
  const noFilings = Array.isArray(meta.noFilings) ? meta.noFilings : [];

  const onSort = (key) => {
    const cur = prefs.sort || DEFAULT_SORT;
    let next;
    if (cur.key !== key) next = { key, dir: key === "symbol" || key === "name" ? "asc" : "desc" };
    else if (cur.dir === (key === "symbol" || key === "name" ? "asc" : "desc")) next = { key, dir: cur.dir === "asc" ? "desc" : "asc" };
    else next = { ...DEFAULT_SORT }; // third click: back to the default order
    updatePrefs({ sort: next });
  };

  const onRefresh = async () => {
    setRefreshBusy(true);
    setRefreshMsg("");
    try {
      const response = await fetch("/api/long-term/refresh", { method: "POST" });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (response.status === 403) setRefreshMsg("Admins only");
      else if (payload?.started) setRefreshMsg("Refresh started");
      else if (payload?.deferredUntil) setRefreshMsg(`Refresh deferred to ${payload.deferredUntil}`);
      else if (payload?.running) setRefreshMsg("A refresh is already running");
      else if (payload?.error) setRefreshMsg(String(payload.error));
      else setRefreshMsg(response.ok ? "Done" : "Refresh is not available yet");
      setReloadTick((t) => t + 1);
    } catch {
      setRefreshMsg("Refresh is not available yet");
    } finally {
      setRefreshBusy(false);
    }
  };

  const sortKey = prefs.sort?.key || "default";
  const sortDir = prefs.sort?.dir || "desc";
  const hasRows = guarded.length > 0;

  let body;
  if (!current && !failed) {
    body = <div className="long-term-empty">Loading Long Term…</div>;
  } else if (!hasRows && refreshing) {
    body = (
      <div className="long-term-empty">
        <p>
          Pulling ten years of filings for {refreshing.total ?? meta.universe ?? "your"} companies, about 12 minutes. Rows fill in as they arrive.
        </p>
        <p className="long-term-progress">
          {refreshing.done ?? 0} / {refreshing.total ?? "?"}
          <span className="long-term-zone-track">
            <span
              className="long-term-zone-fill is-leaps"
              style={{ width: `${refreshing.total ? Math.min(100, Math.round(((refreshing.done || 0) / refreshing.total) * 100)) : 0}%` }}
            />
          </span>
        </p>
      </div>
    );
  } else if (!hasRows) {
    body = (
      <div className="long-term-empty">
        <p>Long Term data is not available yet</p>
        <button type="button" className="long-term-btn" onClick={() => setReloadTick((t) => t + 1)}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  } else if (!visible.length) {
    body = <div className="long-term-empty">No tickers match{query ? ` “${query}”` : ""}.</div>;
  } else if (phone) {
    body = (
      <div className="long-term-cards">
        {visible.map((row) => (
          <div key={row.symbol} className="long-term-card-slot">
            <Card
              row={row}
              open={openSym === row.symbol}
              onToggle={() => toggleRow(row.symbol)}
              leap={leapEntry(row.symbol)}
              onRetryLeap={() => loadLeap(row.symbol)}
              leapQueued={leapAuto.has(row.symbol) || openSym === row.symbol}
            />
            {openSym === row.symbol ? (
              <Drawer
                row={row}
                entry={details[`${row.symbol}:${mos}`]}
                leap={leapEntry(row.symbol)}
                onChart={onChart}
                onClose={() => setOpenSym(null)}
                onRetry={() => loadDetail(row.symbol)}
                onRetryLeap={() => loadLeap(row.symbol)}
              />
            ) : null}
          </div>
        ))}
      </div>
    );
  } else {
    body = (
      <div className="long-term-table-wrap" ref={wrapRef}>
        <table className="long-term-table" data-testid="long-term-table">
          <thead>
            <tr>
              {cols.map((c) => {
                const active = sortKey === c.key;
                return (
                  <th
                    key={c.key}
                    className={`is-${c.align}${active ? " is-sorted" : ""}`}
                    aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    <button type="button" onClick={() => onSort(c.key)}>
                      {c.label}
                      {active ? <span className="long-term-sort-mark">{sortDir === "asc" ? "▲" : "▼"}</span> : null}
                    </button>
                  </th>
                );
              }).reduce((acc, th, i) => {
                acc.push(th);
                if (cols[i].key === "signal") acc.push(<th key="reason" className="is-left is-plain">Reason</th>);
                return acc;
              }, [])}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const open = openSym === row.symbol;
              const tone = signalTone(row.signal);
              const cells = [];
              const leap = {
                entry: leapEntry(row.symbol),
                onRetry: () => loadLeap(row.symbol),
                queued: leapAuto.has(row.symbol) || open,
              };
              for (const c of cols) {
                cells.push(<td key={c.key} className={`is-${c.align} is-col-${c.key}`}>{cellContent(c.key, row, leap)}</td>);
                if (c.key === "signal") {
                  cells.push(
                    <td key="reason" className="is-left is-col-reason">
                      <span className={`long-term-reason is-${tone}`} title={reasonText(row)}>{reasonText(row)}</span>
                    </td>,
                  );
                }
              }
              return [
                <tr
                  key={row.symbol}
                  className={`long-term-row is-${tone}${open ? " is-open" : ""}`}
                  data-testid={`long-term-row-${row.symbol}`}
                  aria-expanded={open}
                  tabIndex={0}
                  onClick={() => toggleRow(row.symbol)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleRow(row.symbol);
                    }
                  }}
                >
                  {cells}
                </tr>,
                open ? (
                  <tr key={`${row.symbol}-why`} className="long-term-drawer-row">
                    <td colSpan={cols.length + 1}>
                      <Drawer
                        row={row}
                        entry={details[`${row.symbol}:${mos}`]}
                        leap={leap.entry}
                        onChart={onChart}
                        onClose={() => setOpenSym(null)}
                        onRetry={() => loadDetail(row.symbol)}
                        onRetryLeap={leap.onRetry}
                        width={wrapWidth || undefined}
                      />
                    </td>
                  </tr>
                ) : null,
              ];
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="long-term-panel" data-testid="long-term-panel">
      <header className="long-term-head">
        <div className="long-term-title">
          <h2>Long Term</h2>
          <p className="long-term-sub">
            Rule #1 / Buffett screen · {meta.scored ?? DASH} scored of {meta.universe ?? DASH} · filings refreshed {formatEtStamp(meta.refreshedAt)}
            {refreshing ? <span className="long-term-live"> · refreshing {refreshing.done ?? 0}/{refreshing.total ?? "?"}…</span> : null}
            {meta.deferredUntil ? <span className="long-term-warn"> · Refresh deferred to {meta.deferredUntil}</span> : null}
            {failed ? <span className="long-term-warn"> · update failed, retrying</span> : null}
            {feedStale ? <span className="long-term-warn" data-testid="long-term-stale"> · prices stale, reconnecting…</span> : null}
          </p>
          <p className="long-term-note">Rule #1 buys are rare; WAIT rows show the price to buy at</p>
        </div>
        <div className="long-term-controls">
          <div className="long-term-switch" role="group" aria-label="Margin of safety">
            {MOS_OPTIONS.map((m) => (
              <button
                key={m}
                type="button"
                aria-pressed={mos === m}
                data-testid={`long-term-mos-${m}`}
                title={`${m}% margin of safety`}
                onClick={() => {
                  if (mos !== m) {
                    setOpenSym(null);
                    updatePrefs({ mos: m });
                  }
                }}
              >
                {m}% MOS
              </button>
            ))}
          </div>
          <input
            type="search"
            className="long-term-search"
            data-testid="long-term-search"
            placeholder="Ticker or name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search tickers"
          />
          {isAdmin ? (
            <>
              <button
                type="button"
                className="long-term-btn"
                data-testid="long-term-refresh"
                disabled={refreshBusy}
                onClick={onRefresh}
                title="Re-read every company's filings (deferred during market hours)"
              >
                <RefreshCw size={13} className={refreshBusy ? "is-spinning" : ""} /> Refresh
              </button>
              {refreshMsg ? <span className="long-term-refresh-msg">{refreshMsg}</span> : null}
            </>
          ) : null}
        </div>
      </header>

      <div className="long-term-tiles" role="group" aria-label="Filter by signal">
        {TILES.map((t) => {
          const count = counts[t] || 0;
          const active = prefs.tile === t;
          const glow = (t === "BUY" || t === "LEAPS") && count > 0;
          return (
            <button
              key={t}
              type="button"
              className={`long-term-tile is-${TILE_TONE[t]}${active ? " is-active" : ""}${glow ? " is-glow" : ""}`}
              data-testid={`long-term-tile-${t}`}
              aria-pressed={active}
              title={TILE_TITLE[t]}
              onClick={() => updatePrefs({ tile: active ? null : t })}
            >
              <span>{TILE_LABEL[t]}</span>
              <b>{count}</b>
            </button>
          );
        })}
      </div>

      {body}

      <footer className="long-term-footer" data-testid="long-term-footer">
        <p><span>ETFs, no company filings:</span> {etfs.length ? etfs.join(", ") : DASH}</p>
        <p><span>No SEC filings:</span> {noFilings.length ? noFilings.join(", ") : DASH}</p>
      </footer>
    </div>
  );
}
