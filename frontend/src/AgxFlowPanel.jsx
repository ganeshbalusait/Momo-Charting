import { useCallback, useEffect, useMemo, useState } from "react";

import "./agxFlow.css";
import BeginnerGuide from "./BeginnerGuide.jsx";
import {
  MAG7_BOARD,
  EXPIRY_FILTERS,
  ORDERS,
  stripTickers,
  watchlistBoard,
  MIN_PREMIUMS,
  POLL_MS,
  SIDE_FILTERS,
  contractLabel,
  expiryLabel,
  fillLabel,
  filterRows,
  flowQuery,
  formatCount,
  formatDelta,
  formatPremium,
  formatVolOi,
  readingProgress,
  readingText,
  hasData,
  updatedText,
  rowKey,
  splitPct,
} from "./agxFlow";
import { ContractSheet, Dominance, LeftOut, TickerSearch, pickProps } from "./LeapOiScannerPanel.jsx";
import { dominance, midText, priceText, searchNote } from "./leapOi.js";

// AGX Flow (2026-11-17): a free, Unusual-Whales-style options flow board from
// Schwab's option chain. Facts only; nothing here places an order.

const PREFS_KEY = "agxFlowPrefs";
const SIDE_LABELS = { both: "Both", calls: "Calls", puts: "Puts" };
const PHONE_QUERY = "(max-width: 699px)";

function readPrefs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    return {
      // Two boards only (2026-11-17 "Only mag7 seperate and watchlist"); an old saved "My tickers" opens as Mag7.
      board: parsed?.board === "watchlist" ? "watchlist" : "mag7",
      side: SIDE_FILTERS.includes(parsed?.side) ? parsed.side : "both",
      minPremium: MIN_PREMIUMS.includes(parsed?.minPremium) ? parsed.minPremium : MIN_PREMIUMS[0],
      expiry: EXPIRY_FILTERS.some((e) => e.id === parsed?.expiry) ? parsed.expiry : "all",
      order: ORDERS.some((o) => o.id === parsed?.order) ? parsed.order : "premium",
    };
  } catch {
    return { board: "mag7", side: "both", minPremium: MIN_PREMIUMS[0], expiry: "all", order: "premium" };
  }
}

function usePhone() {
  const [phone, setPhone] = useState(() => typeof window !== "undefined" && window.matchMedia?.(PHONE_QUERY).matches);
  useEffect(() => {
    const media = window.matchMedia?.(PHONE_QUERY);
    if (!media) return undefined;
    const update = () => setPhone(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return phone;
}

function TickerStrip({ tickers }) {
  return (
    <div className="agx-flow-strip" aria-label="Premium by ticker">
      {tickers.map((t) => {
        const pct = splitPct(t.callPremium, t.putPremium);
        return (
          <div key={t.ticker} className={`agx-flow-chip${t.error ? " is-error" : ""}`} title={t.error || `${t.ticker}: calls ${formatPremium(t.callPremium)} · puts ${formatPremium(t.putPremium)}`}>
            <div className="agx-flow-chip-head">
              <b>{t.ticker}</b>
              <small>{t.pending && !hasData(t) ? "reading…" : t.error && !hasData(t) ? "no data" : `${t.unusualCount} unusual`}</small>
            </div>
            {hasData(t) ? <small className={`agx-flow-lead is-${dominance(t.callPremium, t.putPremium).leader}`}>{dominance(t.callPremium, t.putPremium).text}</small> : null}
            <div className="agx-flow-split" aria-label={`${t.ticker} calls ${pct}% of premium`}>
              <span className="is-call" style={{ width: `${pct}%` }} />
              <span className="is-put" style={{ width: `${100 - pct}%` }} />
            </div>
            <div className="agx-flow-chip-foot">
              <span className="is-call">C {formatPremium(t.callPremium)}</span>
              <span className="is-put">P {formatPremium(t.putPremium)}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FillTag({ fill }) {
  return <span className={`agx-flow-fill is-${fill || "mid"}`} title="A guess from where the last trade printed vs the bid/ask">{fillLabel(fill)}</span>;
}

function FlowTable({ rows, onOpenChart, onPick }) {
  return (
    <div className="agx-flow-table-wrap">
      <table className="agx-flow-table">
        <thead>
          <tr>
            <th>Ticker</th><th>Contract</th><th>Expiry</th><th className="num">Volume</th><th className="num">OI</th>
            <th className="num">Vol/OI</th><th className="num">Bid / Ask</th><th className="num">Mid</th><th className="num">Premium</th><th>Fill</th><th className="num">Delta</th>
            <th className="num" title="Volume since the previous read">New</th><th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} data-testid="agx-flow-row" className={`agx-flow-tap ${row.side === "PUT" ? "is-put" : "is-call"}`} {...pickProps(row, onPick)}>
              <td><b>{row.ticker}</b></td>
              <td className="agx-flow-contract">{contractLabel(row)}</td>
              <td>{expiryLabel(row.expiry, row.days)}</td>
              <td className="num">{formatCount(row.volume)}</td>
              <td className="num">{formatCount(row.oi)}</td>
              <td className="num">{formatVolOi(row.volOi)}</td>
              <td className="num">{priceText(row.bid)} / {priceText(row.ask)}</td>
              <td className="num"><b>{midText(row)}</b></td>
              <td className="num agx-flow-premium">{formatPremium(row.premium)}</td>
              <td><FillTag fill={row.fill} /></td>
              <td className="num">{formatDelta(row.delta)}</td>
              <td className="num">{row.newVolume > 0 ? `+${formatCount(row.newVolume)}` : "--"}</td>
              <td>
                <button type="button" className="agx-flow-chart" onClick={() => onOpenChart?.(row.ticker)} aria-label={`Open ${row.ticker} chart`}>Chart ›</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FlowCards({ rows, onOpenChart, onPick }) {
  return (
    <ul className="agx-flow-cards">
      {rows.map((row) => (
        <li key={rowKey(row)} data-testid="agx-flow-row" className={`agx-flow-card agx-flow-tap ${row.side === "PUT" ? "is-put" : "is-call"}`} {...pickProps(row, onPick)}>
          <div className="agx-flow-card-head">
            <b>{row.ticker}</b>
            <span className="agx-flow-contract">{contractLabel(row)}</span>
            <span className="agx-flow-premium">{formatPremium(row.premium)}</span>
          </div>
          <div className="agx-flow-card-body">
            <span>{expiryLabel(row.expiry, row.days)}</span>
            <span>Vol {formatCount(row.volume)}</span>
            <span>OI {formatCount(row.oi)}</span>
            <span>{formatVolOi(row.volOi)}</span>
            <span>Δ {formatDelta(row.delta)}</span>
            <span>Bid {priceText(row.bid)} · <b>Mid {midText(row)}</b> · Ask {priceText(row.ask)}</span>
            {row.newVolume > 0 ? <span>+{formatCount(row.newVolume)} new</span> : null}
            <FillTag fill={row.fill} />
          </div>
          <button type="button" className="agx-flow-chart" onClick={() => onOpenChart?.(row.ticker)} aria-label={`Open ${row.ticker} chart`}>Chart ›</button>
        </li>
      ))}
    </ul>
  );
}

// Phone alert (2026-11-17 "All six"): one push when a watchlist stock (not Mag7) gets an unusual contract worth $1M+,
// bundled, at most every 10 minutes, market hours, inside this account's phone window. Off until this account turns it
// on; it uses the phone channel set up for Momo Alerts. The switch saves for THIS account only.
function AlertSwitch() {
  const [state, setState] = useState(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  useEffect(() => {
    let alive = true;
    fetch("/api/agx-flow/alert")
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => { if (alive && body) setState(body); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  if (!state?.signedIn) return null;
  const toggle = async () => {
    setSaving(true);
    setProblem("");
    try {
      const response = await fetch("/api/agx-flow/alert", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: !state.on }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body?.error || `Not saved (${response.status}).`);
      setState((current) => ({ ...current, on: Boolean(body.on) }));
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "Not saved.");
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="agx-flow-alert" data-testid="agx-flow-alert">
      <span>
        <b>Phone alert</b> · a watchlist stock (not Mag7) gets an unusual contract worth {formatPremium(state.minPremium || 1_000_000)}+.
        One bundled buzz at most every 10 minutes, market hours, inside your Momo Alert phone window.
        {!state.hasPhone ? " Set up the phone channel in Momo Alert settings first." : ""}
      </span>
      <button type="button" role="switch" aria-checked={Boolean(state.on)} aria-label="Phone alert for $1M+ unusual"
        disabled={saving || (!state.hasPhone && !state.on)} onClick={toggle}>{state.on ? "On" : "Off"}</button>
      {problem ? <small className="is-error" role="alert">{problem}</small> : null}
    </div>
  );
}

export default function AgxFlowPanel({ onOpenChart, onOpenOptions }) {
  const [prefs, setPrefs] = useState(readPrefs);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const phone = usePhone();

  const updatePrefs = (patch) => setPrefs((current) => {
    const next = { ...current, ...patch };
    try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* storage can be disabled */ }
    return next;
  });

  // Watchlist board (2026-11-17, "what about other tickers from watchlist"): the whole watchlist without the Mag7 names (they have their own board).
  const [watchlist, setWatchlist] = useState(null);
  useEffect(() => {
    if (prefs.board !== "watchlist" || watchlist !== null) return undefined;
    let alive = true;
    fetch("/api/watchlist")
      .then((r) => (r.ok ? r.json() : { tickers: [] }))
      .then((body) => { if (alive) setWatchlist(Array.isArray(body?.tickers) ? body.tickers : []); })
      .catch(() => { if (alive) setWatchlist([]); });
    return () => { alive = false; };
  }, [prefs.board, watchlist]);
  // Search any ticker (2026-11-17 "Add search ticker also"): it replaces the board until "Back to board" (not saved).
  const [search, setSearch] = useState("");
  const symbols = useMemo(() => {
    if (search) return [search];
    if (prefs.board === "watchlist") return watchlist === null ? [] : watchlistBoard(watchlist);
    return MAG7_BOARD;
  }, [search, prefs.board, watchlist]);
  const pickBoard = (board) => { setSearch(""); setError(""); updatePrefs({ board }); };
  const symbolKey = symbols.join(",");
  const queryKey = `${symbolKey}|${prefs.expiry}|${prefs.order}`;
  const query = useCallback((list) => flowQuery(list, { expiry: prefs.expiry, order: prefs.order }), [prefs.expiry, prefs.order]);
  const [picked, setPicked] = useState(null);
  const closePick = useCallback(() => setPicked(null), []);
  const progress = readingProgress(data?.tickers);
  const reading = progress.pending > 0;                  // tickers with no numbers yet
  const busy = reading || progress.updating > 0;         // look again sooner while anything is being read
  const updated = updatedText(data?.tickers);

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      if (!symbolKey) return;                              // the watchlist has not answered yet
      setLoading(true);
      try {
        const response = await fetch(query(symbolKey.split(",")));
        const payload = await response.json().catch(() => ({}));
        if (cancelled) return;
        if (!response.ok) {
          setError(payload?.error || `AGX Flow could not load (${response.status}).`);
          return;
        }
        setError("");
        setData(payload);
      } catch {
        if (!cancelled) setError("AGX Flow could not reach the app server.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    setData(null);
    load();
    timer = setInterval(load, POLL_MS);
    const onVisible = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [queryKey]);   // eslint-disable-line react-hooks/exhaustive-deps -- queryKey covers symbols, expiry and order

  // While Schwab is still being read, look again sooner than the minute poll.
  useEffect(() => {
    if (!busy) return undefined;
    let cancelled = false;   // a late answer for an old search/expiry/order must never replace the new board (review 11-17)
    const quick = setTimeout(async () => {
      try {
        if (!symbolKey) return;
        const response = await fetch(query(symbolKey.split(",")));
        const body = response.ok ? await response.json() : null;
        if (!cancelled && body) setData(body);
      } catch { /* the minute poll will try again */ }
    }, 5000);
    return () => { cancelled = true; clearTimeout(quick); };
  }, [busy, data, symbolKey, query]);

  const rows = filterRows(data?.rows, { side: prefs.side, minPremium: prefs.minPremium });
  const tickers = Array.isArray(data?.tickers) ? data.tickers : [];
  const note = search ? searchNote(data, search, "AGX Flow") : "";

  return (
    <section className="agx-flow-view" data-testid="agx-flow-view" aria-label="AGX Flow">
      <header className="agx-flow-head">
        <div className="agx-flow-title">
          <h2>AGX Flow</h2>
          <small>Big option bets placed today, and whether they were bought or sold · facts only</small>
        </div>
        <BeginnerGuide page="agxFlow" />
        <div className="agx-flow-controls">
          <span className="agx-flow-switch" role="group" aria-label="Board">
            <button type="button" aria-pressed={!search && prefs.board === "mag7"} onClick={() => pickBoard("mag7")}>Mag7</button>
            <button type="button" aria-pressed={!search && prefs.board === "watchlist"} onClick={() => pickBoard("watchlist")}>Watchlist</button>
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Side">
            {SIDE_FILTERS.map((side) => (
              <button key={side} type="button" aria-pressed={prefs.side === side} onClick={() => updatePrefs({ side })}>{SIDE_LABELS[side]}</button>
            ))}
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Expiry">
            {EXPIRY_FILTERS.map((item) => (
              <button key={item.id} type="button" aria-pressed={prefs.expiry === item.id} onClick={() => updatePrefs({ expiry: item.id })}>{item.label}</button>
            ))}
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Order">
            {ORDERS.map((item) => (
              <button key={item.id} type="button" aria-pressed={prefs.order === item.id} onClick={() => updatePrefs({ order: item.id })}>{item.label}</button>
            ))}
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Minimum premium">
            {MIN_PREMIUMS.map((value) => (
              <button key={value} type="button" aria-pressed={prefs.minPremium === value} onClick={() => updatePrefs({ minPremium: value })}>{formatPremium(value)}</button>
            ))}
          </span>
        </div>
      </header>
      <TickerSearch active={search} onSearch={(ticker) => { setError(""); setSearch(ticker); }} onClear={() => { setError(""); setSearch(""); }} />
      {search ? <p className="agx-flow-updated" data-testid="search-showing">Showing {search} only</p> : null}

      {reading ?<p className="agx-flow-status">{readingText(progress)}</p> : null}
      {!reading && updated ? <p className="agx-flow-updated">{updated}{progress.updating ? " · refreshing" : ""}</p> : null}
      {!data && loading ? <p className="agx-flow-status">Loading…</p> : null}
      {error ? <p className="agx-flow-status is-error">{error}</p> : null}
      {note ? <p className="agx-flow-empty" data-testid="search-note">{note}</p> : null}

      {tickers.length ? (
        <Dominance
          title={`Calls vs puts today, option dollars${prefs.expiry !== "all" ? ` · ${EXPIRY_FILTERS.find((e) => e.id === prefs.expiry)?.label}` : ""}`}
          calls={tickers.reduce((sum, t) => sum + (Number(t.callPremium) || 0), 0)}
          puts={tickers.reduce((sum, t) => sum + (Number(t.putPremium) || 0), 0)}
          format={formatPremium}
        />
      ) : null}
      {tickers.length ? <TickerStrip tickers={stripTickers(tickers)} /> : null}

      {data && !rows.length && !note ? (
        <p className="agx-flow-empty">
          {reading ? "Waiting for Schwab's chains…"
            : prefs.order === "hot" && data?.marketOpen === false ? "Hot right now works while the market is open, 9:30 AM to 4:00 PM ET."
            : prefs.order === "hot" ? `Nothing unusual at ${formatPremium(prefs.minPremium)}+ has traded since the last read.`
            : `No unusual contracts at ${formatPremium(prefs.minPremium)}+ premium${prefs.expiry !== "all" ? " in this expiry window" : ""} right now.`}
        </p>
      ) : null}
      {prefs.order === "hot" ? <p className="agx-flow-updated" data-testid="hot-note">Hot right now: most option dollars added since the last read, a few minutes ago. Tap a contract for its details.</p> : null}
      {rows.length ? (phone ? <FlowCards rows={rows} onOpenChart={onOpenChart} onPick={setPicked} /> : <FlowTable rows={rows} onOpenChart={onOpenChart} onPick={setPicked} />) : null}

      <LeftOut excluded={data?.excluded} minPrice={data?.minPrice} className="agx-flow-updated" />
      <AlertSwitch />
      <p className="agx-flow-note">{data?.note || "Facts only, not a direction signal."} Unusual = volume of at least 500 and above open interest. Paper only; nothing here places an order.</p>
      <ContractSheet row={picked} onClose={closePick} onOpenChart={onOpenChart} onOpenOptions={onOpenOptions} />
    </section>
  );
}
