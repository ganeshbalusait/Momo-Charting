import { useEffect, useMemo, useState } from "react";

import "./leapOi.css";
import {
  DELTAS, RANGES, SIDES, awayText, barWidth, boardTickers, contractLabel, deltaText, expiryLabel, formatOi,
  filterByTicker, priceText, progressText, readAtText, scannerUrl,
} from "./leapOi.js";

function Pick({ label, row }) {
  if (!row) return <div><dt>{label}</dt><dd className="leapoi-dim">—</dd></div>;
  const tone = row.side === "PUT" ? "is-put" : "is-call";
  return (
    <div>
      <dt>{label}</dt>
      <dd><b className={tone}>{contractLabel(row)}</b> {expiryLabel(row.expiry)} · OI <b>{formatOi(row.oi)}</b> · Δ {deltaText(row.delta)}</dd>
    </div>
  );
}

// By ticker (2026-11-17, "I see only mag7 tickets what about other tickets from watchlist"): one line per ticker, so
// the small names are not buried under Mag7's huge open interest in the ranked lists.
function ByTicker({ list, onOpenChart }) {
  const [query, setQuery] = useState("");
  const [showAll, setShowAll] = useState(false);
  const { shown, total } = filterByTicker(list, query, showAll);
  return (
    <>
      <h3>By ticker</h3>
      <p className="leapoi-sub">Every ticker on the board: its biggest call and put open interest, and its biggest ENTRY call and put.</p>
      <input className="leapoi-search" type="search" placeholder="Find a ticker" value={query} aria-label="Find a ticker"
        onChange={(event) => setQuery(event.target.value)} />
      <div className="leapoi-bytk">
        {shown.map((line) => (
          <div className="leapoi-card leapoi-bytk-card" key={line.ticker} data-testid="leap-oi-ticker">
            <div className="leapoi-card-head">
              <b className="leapoi-tk">{line.ticker}</b>
              <small className="leapoi-dim">{priceText(line.spot)}</small>
              <small className="leapoi-dim">Calls {formatOi(line.callOi)} · Puts {formatOi(line.putOi)}</small>
              <button type="button" onClick={() => onOpenChart?.(line.ticker)}>Chart ›</button>
            </div>
            <dl className="leapoi-card-grid leapoi-bytk-grid">
              <Pick label="Most call OI" row={line.call} />
              <Pick label="Most put OI" row={line.put} />
              <Pick label="ENTRY call" row={line.entryCall} />
              <Pick label="ENTRY put" row={line.entryPut} />
            </dl>
          </div>
        ))}
      </div>
      {!shown.length ? <p className="leapoi-empty">{query ? "No ticker on the board starts with that." : "Nothing read yet."}</p> : null}
      {total > shown.length ? (
        <button type="button" className="leapoi-more" onClick={() => setShowAll(true)}>Show all {total} tickers</button>
      ) : null}
    </>
  );
}

// LEAP OI Scanner (2026-11-17): strikes 1-12 months out with the most open interest across Mag7 + the watchlist,
// with delta and the option chain's ENTRY tag. Facts only.
const PREFS_KEY = "leapOiScannerPrefs";
const POLL_READY_MS = 5 * 60 * 1000;   // open interest changes once a day; prices in the copy are "as of" its read
const POLL_PENDING_MS = 6000;          // while Schwab is still being read in the background

function readPrefs() {
  try {
    const p = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    return {
      board: p.board === "mag7" ? "mag7" : "both",
      range: RANGES.some((r) => r.id === p.range) ? p.range : "leap",
      delta: DELTAS.some((d) => d.id === p.delta) ? p.delta : "min",
      side: SIDES.some((s) => s.id === p.side) ? p.side : "both",
    };
  } catch {
    return { board: "both", range: "leap", delta: "min", side: "both" };
  }
}

function Chips({ label, items, value, onChange }) {
  return (
    <div className="leapoi-chip-group" role="group" aria-label={label}>
      <small>{label}</small>
      {items.map((item) => (
        <button key={item.id} type="button" className={value === item.id ? "is-on" : ""} aria-pressed={value === item.id}
          onClick={() => onChange(item.id)}>{item.label}</button>
      ))}
    </div>
  );
}

function Hero({ title, row, onOpenChart }) {
  if (!row) return null;
  const tone = row.side === "PUT" ? "is-put" : "is-call";
  return (
    <div className="leapoi-hero">
      <small>{title}</small>
      <b><span className="leapoi-tk">{row.ticker}</span> <span className={tone}>{contractLabel(row)}</span>{row.entry ? <span className="leapoi-tag">ENTRY</span> : null}</b>
      <span>{expiryLabel(row.expiry)} · {row.days}d · OI <b>{formatOi(row.oi)}</b> · Δ {deltaText(row.delta)}</span>
      <button type="button" onClick={() => onOpenChart?.(row.ticker)}>Chart ›</button>
    </div>
  );
}

function Rows({ rows, onOpenChart, testId }) {
  const top = rows.length ? Math.max(...rows.map((r) => r.oi || 0)) : 0;
  if (!rows.length) return <p className="leapoi-empty">Nothing in this range yet.</p>;
  return (
    <>
      <table className="leapoi-table">
        <thead>
          <tr><th>#</th><th>Ticker</th><th>Contract</th><th>Expiry</th><th className="num">Open interest</th><th className="num">Delta</th><th className="num">Bid / Ask</th><th className="num">From price</th><th aria-label="Chart" /></tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const tone = row.side === "PUT" ? "is-put" : "is-call";
            return (
              <tr key={`${row.ticker}-${row.side}-${row.expiry}-${row.strike}`} data-testid={testId}>
                <td className="leapoi-rank">{index + 1}</td>
                <td><b className="leapoi-tk">{row.ticker}</b><small>{priceText(row.spot)}</small></td>
                <td><b className={tone}>{contractLabel(row)}</b>{row.entry ? <span className="leapoi-tag">ENTRY</span> : null}</td>
                <td>{expiryLabel(row.expiry)} <small>{row.days}d</small></td>
                <td className="num"><b>{formatOi(row.oi)}</b><i className={`leapoi-bar ${tone}`} style={{ width: `${barWidth(row.oi, top)}%` }} /></td>
                <td className="num">{deltaText(row.delta)}</td>
                <td className="num">{priceText(row.bid)} / {priceText(row.ask)}</td>
                <td className="num leapoi-dim">{awayText(row.away)}</td>
                <td><button type="button" onClick={() => onOpenChart?.(row.ticker)}>Chart ›</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="leapoi-cards">
        {rows.map((row, index) => {
          const tone = row.side === "PUT" ? "is-put" : "is-call";
          return (
            <div className="leapoi-card" key={`${row.ticker}-${row.side}-${row.expiry}-${row.strike}`} data-testid={`${testId}-card`}>
              <div className="leapoi-card-head">
                <span className="leapoi-rank">{index + 1}</span>
                <b className="leapoi-tk">{row.ticker}</b>
                <b className={tone}>{contractLabel(row)}</b>
                {row.entry ? <span className="leapoi-tag">ENTRY</span> : null}
                <button type="button" onClick={() => onOpenChart?.(row.ticker)}>Chart ›</button>
              </div>
              <dl className="leapoi-card-grid">
                <div><dt>Expiry</dt><dd>{expiryLabel(row.expiry)} · {row.days}d</dd></div>
                <div><dt>OI</dt><dd><b>{formatOi(row.oi)}</b></dd></div>
                <div><dt>Delta</dt><dd>{deltaText(row.delta)}</dd></div>
                <div><dt>Bid / Ask</dt><dd>{priceText(row.bid)} / {priceText(row.ask)}</dd></div>
                <div><dt>From price</dt><dd>{awayText(row.away)}</dd></div>
              </dl>
              <i className={`leapoi-bar ${tone}`} style={{ width: `${barWidth(row.oi, top)}%` }} />
            </div>
          );
        })}
      </div>
    </>
  );
}

export default function LeapOiScannerPanel({ onOpenChart }) {
  const [prefs, setPrefs] = useState(readPrefs);
  const [watchlist, setWatchlist] = useState(null);   // null until /api/watchlist answers (or fails)
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const setPref = (key, value) => {
    setPrefs((current) => {
      const next = { ...current, [key]: value };
      try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* private window */ }
      return next;
    });
  };

  useEffect(() => {
    let alive = true;
    fetch("/api/watchlist")
      .then((r) => (r.ok ? r.json() : { tickers: [] }))
      .then((data) => { if (alive) setWatchlist(Array.isArray(data?.tickers) ? data.tickers : []); })
      .catch(() => { if (alive) setWatchlist([]); });
    return () => { alive = false; };
  }, []);

  const tickers = useMemo(() => boardTickers(prefs.board, watchlist), [prefs.board, watchlist]);
  // Wait for the watchlist before the first read, so Schwab is not asked for Mag7 alone and then again.
  const ready = prefs.board === "mag7" || watchlist !== null;
  const url = useMemo(() => (ready ? scannerUrl({ tickers, range: prefs.range, delta: prefs.delta, side: prefs.side }) : ""),
    [ready, tickers, prefs.range, prefs.delta, prefs.side]);

  useEffect(() => {
    if (!url) return undefined;
    let alive = true;
    let timer = null;
    const load = async () => {
      try {
        const response = await fetch(url);
        const data = await response.json().catch(() => ({}));
        if (!alive) return;
        if (!response.ok) {
          setError(data?.error || `The scanner did not answer (${response.status}).`);
        } else {
          setError("");
          setPayload(data);
        }
        setLoading(false);
        const waiting = response.ok && Array.isArray(data?.pending) && data.pending.length > 0;
        timer = window.setTimeout(load, waiting ? POLL_PENDING_MS : POLL_READY_MS);
      } catch {
        if (!alive) return;
        setError("Could not reach the app server.");
        setLoading(false);
        timer = window.setTimeout(load, POLL_PENDING_MS * 5);
      }
    };
    setLoading(true);
    load();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [url]);

  const progress = progressText(payload);
  const readAt = readAtText(payload);
  const errors = (payload?.tickers || []).filter((t) => t.state === "error");

  return (
    <div className="leapoi" data-testid="leap-oi-view">
      <header className="leapoi-head">
        <h2>LEAP OI Scanner</h2>
        <p>Strikes with the most open interest, {RANGES.find((r) => r.id === prefs.range)?.label.replace("LEAP ", "")} months out · {prefs.board === "mag7" ? "Mag7" : "Mag7 + Watchlist"}{readAt ? ` · ${readAt}` : ""}</p>
      </header>
      <div className="leapoi-filters">
        <Chips label="Board" value={prefs.board} onChange={(v) => setPref("board", v)}
          items={[{ id: "both", label: "Mag7 + Watchlist" }, { id: "mag7", label: "Mag7" }]} />
        <Chips label="Months out" value={prefs.range} onChange={(v) => setPref("range", v)} items={RANGES} />
        <Chips label="Delta" value={prefs.delta} onChange={(v) => setPref("delta", v)} items={DELTAS} />
        <Chips label="Side" value={prefs.side} onChange={(v) => setPref("side", v)} items={SIDES} />
      </div>
      {progress ? <p className="leapoi-progress" role="status">{progress}</p> : null}
      {error ? <p className="leapoi-error" role="alert">{error}</p> : null}
      {loading && !payload ? <p className="leapoi-progress">Loading…</p> : null}
      {payload ? (
        <>
          <div className="leapoi-heros">
            <Hero title="Biggest call open interest" row={payload.biggestCall} onOpenChart={onOpenChart} />
            <Hero title="Biggest put open interest" row={payload.biggestPut} onOpenChart={onOpenChart} />
          </div>
          <h3>ENTRY contracts with the most open interest</h3>
          <p className="leapoi-sub">Each expiry's ENTRY, picked exactly like the option chain: out of the money, delta just under 0.20.</p>
          <Rows rows={(payload.entries || []).slice(0, 15)} onOpenChart={onOpenChart} testId="leap-oi-entry-row" />
          <h3>All strikes with the most open interest</h3>
          <p className="leapoi-sub">Biggest open interest at the delta you picked. ENTRY tag when that strike is the chain's ENTRY.</p>
          <Rows rows={payload.rows || []} onOpenChart={onOpenChart} testId="leap-oi-row" />
          <ByTicker list={payload.byTicker || []} onOpenChart={onOpenChart} />
          {errors.length ? <p className="leapoi-sub">Not read: {errors.map((t) => t.ticker).join(", ")} ({errors[0].error})</p> : null}
          <p className="leapoi-note">{payload.note}</p>
        </>
      ) : null}
    </div>
  );
}
