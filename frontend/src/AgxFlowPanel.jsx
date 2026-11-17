import { useEffect, useMemo, useState } from "react";

import "./agxFlow.css";
import {
  MAG7_BOARD,
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
  parseTickers,
  readingProgress,
  rowKey,
  splitPct,
} from "./agxFlow";

// AGX Flow (2026-11-17): a free, Unusual-Whales-style options flow board from
// Schwab's option chain. Facts only; nothing here places an order.

const PREFS_KEY = "agxFlowPrefs";
const SIDE_LABELS = { both: "Both", calls: "Calls", puts: "Puts" };
const PHONE_QUERY = "(max-width: 699px)";

function readPrefs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    return {
      board: parsed?.board === "custom" ? "custom" : "mag7",
      custom: typeof parsed?.custom === "string" ? parsed.custom : "",
      side: SIDE_FILTERS.includes(parsed?.side) ? parsed.side : "both",
      minPremium: MIN_PREMIUMS.includes(parsed?.minPremium) ? parsed.minPremium : MIN_PREMIUMS[0],
    };
  } catch {
    return { board: "mag7", custom: "", side: "both", minPremium: MIN_PREMIUMS[0] };
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
              <small>{t.pending ? "reading…" : t.error ? "no data" : `${t.unusualCount} unusual`}</small>
            </div>
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

function FlowTable({ rows, onOpenChart }) {
  return (
    <div className="agx-flow-table-wrap">
      <table className="agx-flow-table">
        <thead>
          <tr>
            <th>Ticker</th><th>Contract</th><th>Expiry</th><th className="num">Volume</th><th className="num">OI</th>
            <th className="num">Vol/OI</th><th className="num">Premium</th><th>Fill</th><th className="num">Delta</th>
            <th className="num" title="Volume since the previous read">New</th><th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} data-testid="agx-flow-row" className={row.side === "PUT" ? "is-put" : "is-call"}>
              <td><b>{row.ticker}</b></td>
              <td className="agx-flow-contract">{contractLabel(row)}</td>
              <td>{expiryLabel(row.expiry, row.days)}</td>
              <td className="num">{formatCount(row.volume)}</td>
              <td className="num">{formatCount(row.oi)}</td>
              <td className="num">{formatVolOi(row.volOi)}</td>
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

function FlowCards({ rows, onOpenChart }) {
  return (
    <ul className="agx-flow-cards">
      {rows.map((row) => (
        <li key={rowKey(row)} data-testid="agx-flow-row" className={`agx-flow-card ${row.side === "PUT" ? "is-put" : "is-call"}`}>
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
            {row.newVolume > 0 ? <span>+{formatCount(row.newVolume)} new</span> : null}
            <FillTag fill={row.fill} />
          </div>
          <button type="button" className="agx-flow-chart" onClick={() => onOpenChart?.(row.ticker)} aria-label={`Open ${row.ticker} chart`}>Chart ›</button>
        </li>
      ))}
    </ul>
  );
}

export default function AgxFlowPanel({ onOpenChart }) {
  const [prefs, setPrefs] = useState(readPrefs);
  const [draft, setDraft] = useState(prefs.custom);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const phone = usePhone();

  const updatePrefs = (patch) => setPrefs((current) => {
    const next = { ...current, ...patch };
    try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* storage can be disabled */ }
    return next;
  });

  const symbols = useMemo(() => {
    const custom = parseTickers(prefs.custom);
    return prefs.board === "custom" && custom.length ? custom : MAG7_BOARD;
  }, [prefs.board, prefs.custom]);
  const symbolKey = symbols.join(",");
  const progress = readingProgress(data?.tickers);
  const reading = progress.pending > 0;

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    const load = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      setLoading(true);
      try {
        const response = await fetch(flowQuery(symbolKey.split(",")));
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
  }, [symbolKey]);

  // While Schwab is still being read, look again sooner than the minute poll.
  useEffect(() => {
    if (!reading) return undefined;
    const quick = setTimeout(async () => {
      try {
        const response = await fetch(flowQuery(symbolKey.split(",")));
        if (response.ok) setData(await response.json());
      } catch { /* the minute poll will try again */ }
    }, 5000);
    return () => clearTimeout(quick);
  }, [reading, data, symbolKey]);

  const rows = filterRows(data?.rows, { side: prefs.side, minPremium: prefs.minPremium });
  const tickers = Array.isArray(data?.tickers) ? data.tickers : [];

  return (
    <section className="agx-flow-view" data-testid="agx-flow-view" aria-label="AGX Flow">
      <header className="agx-flow-head">
        <div className="agx-flow-title">
          <h2>AGX Flow</h2>
          <small>Unusual option volume · Schwab chain · facts only</small>
        </div>
        <div className="agx-flow-controls">
          <span className="agx-flow-switch" role="group" aria-label="Board">
            <button type="button" aria-pressed={prefs.board === "mag7"} onClick={() => updatePrefs({ board: "mag7" })}>Mag7 + SPY/QQQ</button>
            <button type="button" aria-pressed={prefs.board === "custom"} onClick={() => updatePrefs({ board: "custom" })}>My tickers</button>
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Side">
            {SIDE_FILTERS.map((side) => (
              <button key={side} type="button" aria-pressed={prefs.side === side} onClick={() => updatePrefs({ side })}>{SIDE_LABELS[side]}</button>
            ))}
          </span>
          <span className="agx-flow-switch" role="group" aria-label="Minimum premium">
            {MIN_PREMIUMS.map((value) => (
              <button key={value} type="button" aria-pressed={prefs.minPremium === value} onClick={() => updatePrefs({ minPremium: value })}>{formatPremium(value)}</button>
            ))}
          </span>
        </div>
        {prefs.board === "custom" ? (
          <form
            className="agx-flow-tickers"
            onSubmit={(event) => { event.preventDefault(); updatePrefs({ custom: parseTickers(draft).join(", ") }); }}
          >
            <input
              type="text"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="AAPL, NVDA, AMD (up to 20)"
              aria-label="Tickers for AGX Flow"
              autoCapitalize="characters"
              spellCheck={false}
            />
            <button type="submit">Show</button>
          </form>
        ) : null}
      </header>

      {reading ? <p className="agx-flow-status">Reading Schwab… ({progress.done} of {progress.total})</p> : null}
      {!data && loading ? <p className="agx-flow-status">Loading…</p> : null}
      {error ? <p className="agx-flow-status is-error">{error}</p> : null}

      {tickers.length ? <TickerStrip tickers={tickers} /> : null}

      {data && !rows.length ? (
        <p className="agx-flow-empty">
          {reading ? "Waiting for Schwab's chains…" : `No unusual contracts at ${formatPremium(prefs.minPremium)}+ premium right now.`}
        </p>
      ) : null}
      {rows.length ? (phone ? <FlowCards rows={rows} onOpenChart={onOpenChart} /> : <FlowTable rows={rows} onOpenChart={onOpenChart} />) : null}

      <p className="agx-flow-note">{data?.note || "Facts only, not a direction signal."} Unusual = volume of at least 500 and above open interest. Paper only; nothing here places an order.</p>
    </section>
  );
}
