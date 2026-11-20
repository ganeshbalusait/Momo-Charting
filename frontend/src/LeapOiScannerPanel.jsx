import { useCallback, useEffect, useMemo, useState } from "react";

import "./leapOi.css";
import "./simpleWhy.css";
import {
  DELTAS, RANGES, SIDES, awayText, barWidth, boardTickers, contractLabel, deltaText, expiryLabel, formatOi,
  BYTK_SORTS, boardTotals, cleanTicker, costText, dominance, filterByTicker, leftOutText, midText, searchNote, sortByTicker, spreadText, priceText, progressText, readAtText, scannerUrl,
  addRecent, contractFacts, moveText, oiChangeText, oiChangeTone, readRecent, saveRecent, sinceText,
} from "./leapOi.js";
import { LIVE_PRICE_POLL_MS, awayFrom, changeTone, quotesFrom, stockPrice, stockText, tickersOnScreen } from "./leapLivePrice.js";
import { flowCell } from "./leapFlow.js";
import BeginnerGuide from "./BeginnerGuide.jsx";
import { MARKS, leapWhy } from "./simpleWhy.js";
import TickerStory from "./TickerStory.jsx";

// Today's trades on this contract, from AGX Flow's latest read: count, dollars, and Bought / Sold / Can't tell, so the
// trader does not have to open AGX Flow to know (2026-11-20).
function TodayFlow({ row }) {
  const cell = flowCell(row);
  return (
    <span className={`leapoi-flow ${cell.tone}`} title={cell.title} data-testid="leap-oi-flow">
      {cell.text}
      {cell.word ? <b className="leapoi-flow-word">{cell.word}</b> : null}
      {cell.unusual ? <span className="leapoi-flow-unusual">Unusual</span> : null}
    </span>
  );
}

// The stock's current price (live quote once a minute, else the price at the chain read), shared by every card.
function StockPrice({ row, quotes }) {
  const stock = stockPrice(row, quotes);
  return (
    <span className={`leapoi-stock ${changeTone(stock)}`} data-testid="leap-oi-stock"
      title={stock.live ? "Current stock price and today's change" : "Stock price when the option chain was read"}>
      {stockText(stock)}
    </span>
  );
}

function awayNow(row, quotes) {
  const away = awayFrom(row, stockPrice(row, quotes));
  return awayText(away === null ? row.away : away);
}

// Search any ticker (2026-11-17 "Add search ticker also"): type a ticker, the page shows only that one, even when it is
// not on Mag7 or the watchlist. "Back to board" returns. Shared with AGX Flow. The last 5 searches sit under the box
// as one-tap buttons (2026-11-17 "All six"), kept in this browser only and shared by both pages.
export function TickerSearch({ active, onSearch, onClear }) {
  const [text, setText] = useState(active || "");
  const [bad, setBad] = useState(false);
  const [recent, setRecent] = useState(readRecent);
  useEffect(() => { setText(active || ""); }, [active]);
  const go = (ticker) => {
    const next = addRecent(recent, ticker);
    setRecent(next);
    saveRecent(next);
    setBad(false);
    setText(ticker);
    onSearch(ticker);
  };
  const submit = (event) => {
    event.preventDefault();
    const ticker = cleanTicker(text);
    if (!ticker) { setBad(true); return; }
    go(ticker);
  };
  return (
    <form className="tk-search" role="search" aria-label="Search any ticker" onSubmit={submit} data-testid="ticker-search">
      <input type="search" value={text} placeholder="Search any ticker" aria-label="Search any ticker" maxLength={8}
        autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="search"
        onChange={(event) => { setText(event.target.value); setBad(false); }} />
      <button type="submit">Search</button>
      {active ? (
        <button type="button" className="tk-search-back" onClick={() => { setText(""); setBad(false); onClear(); }}>✕ Back to board</button>
      ) : null}
      {bad ? <small className="tk-search-bad" role="alert">Type a ticker, like NVDA.</small> : null}
      {recent.length ? (
        <div className="tk-search-recent" role="group" aria-label="Recent searches">
          <small>Recent</small>
          {recent.map((ticker) => (
            <button key={ticker} type="button" aria-pressed={ticker === active} onClick={() => go(ticker)}>{ticker}</button>
          ))}
        </div>
      ) : null}
    </form>
  );
}

// "+1,400 since Nov 17" next to open interest (LEAP OI Scanner only; AGX Flow rows have no prior-day copy).
function OiChange({ row, short = false }) {
  const text = oiChangeText(row?.oiChange);
  if (!text) return null;
  return (
    <span className={`leapoi-chg ${oiChangeTone(row.oiChange)}`} title={`Open interest change ${sinceText(row.oiChangeSince)}`}>
      {short ? text : `${text} ${sinceText(row.oiChangeSince)}`}
    </span>
  );
}

function Fact({ label, children }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}

// Tap a contract (2026-11-17 "All six"): every fact the page has about it in one card, with Chart and Options page.
// The Options page holds contracts up to 31 days out, so a LEAP opens there on its ticker, not on this contract.
// Facts only: break-even is simple arithmetic at expiry, not a forecast or a pick.
// volumeLabel: AGX Flow reads every few minutes ("Volume today"); LEAP copies are up to 12 hours old (review 11-17).
// "In simple words" (2026-11-20): why this contract is on the list, for a 10-year-old or a first-time trader.
export function SimpleWhy({ why }) {
  if (!why) return null;
  return (
    <section className="simple-why" data-testid="simple-why" aria-label="In simple words">
      <h4>In simple words</h4>
      <p className="simple-why-verdict">{why.verdict}</p>
      <ul>
        {why.checks.map((check) => (
          <li key={check.text} className={`is-${check.mark}`}><span aria-hidden="true">{MARKS[check.mark]}</span> {check.text}</li>
        ))}
      </ul>
      <p className="simple-why-meaning"><b>What this means: </b>{why.meaning}</p>
      <p className="simple-why-advice">{why.notAdvice}</p>
    </section>
  );
}

export function ContractSheet({ row, onClose, onOpenChart, onOpenOptions, volumeLabel = "Volume today", quotes = null, why = null }) {
  useEffect(() => {
    if (!row) return undefined;
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [row, onClose]);
  if (!row) return null;
  const facts = contractFacts(row);
  const tone = row.side === "PUT" ? "is-put" : "is-call";
  const has = (key) => row[key] !== null && row[key] !== undefined && row[key] !== "";
  const days = Number(row.days);
  return (
    <div className="ck-sheet-back" onClick={onClose} data-testid="contract-sheet-back">
      <div className="ck-sheet" role="dialog" aria-modal="true" aria-label={`${row.ticker} ${contractLabel(row)}`}
        onClick={(event) => event.stopPropagation()} data-testid="contract-sheet">
        <div className="ck-sheet-head">
          <b className="leapoi-tk">{row.ticker}</b>
          <b className={tone}>{contractLabel(row)}</b>
          {row.entry ? <span className="leapoi-tag">ENTRY</span> : null}
          <button type="button" className="ck-sheet-close" aria-label="Close" onClick={onClose}>✕</button>
        </div>
        <p className="ck-sheet-sub">{expiryLabel(row.expiry)}{Number.isFinite(days) ? ` · ${days} days out` : ""}{quotes ? null : has("spot") ? ` · stock ${priceText(row.spot)}` : ""}</p>
        <SimpleWhy why={why} />
        <dl className="ck-sheet-grid">
          {quotes ? <Fact label="Stock price"><StockPrice row={row} quotes={quotes} /></Fact> : null}
          {quotes ? <Fact label="From price">{awayNow(row, quotes)}</Fact> : null}
          {quotes ? <Fact label="Today's trades"><TodayFlow row={row} /></Fact> : null}
          <Fact label="Bid">{priceText(row.bid)}</Fact>
          <Fact label="Mid"><b>{priceText(facts.mid)}</b></Fact>
          <Fact label="Ask">{priceText(row.ask)}</Fact>
          <Fact label="1 contract at mid">{costText(row)}</Fact>
          <Fact label="Bid-ask spread">{spreadText(row)}</Fact>
          <Fact label="Delta">{deltaText(row.delta)}</Fact>
          <Fact label="Open interest"><b>{formatOi(row.oi)}</b> <OiChange row={row} short /></Fact>
          {has("volume") ? <Fact label={volumeLabel}>{formatOi(row.volume)}</Fact> : null}
          {has("premium") ? <Fact label="Premium today">${formatOi(Math.round(row.premium))}</Fact> : null}
          {has("iv") ? <Fact label="Implied volatility">{Number(row.iv).toFixed(1)}%</Fact> : null}
          <Fact label="Break-even at expiry">{priceText(facts.breakeven)}</Fact>
          <Fact label="Stock move to break-even">{moveText(facts.move)}</Fact>
        </dl>
        <p className="ck-sheet-note">
          Break-even = strike {row.side === "PUT" ? "minus" : "plus"} the mid, at expiry, before fees. Prices are as of the last read. Facts only, not a pick.
          {Number.isFinite(days) && days > 31 ? " The Options page shows contracts up to 31 days out, so it opens on this ticker, not on this contract." : ""}
        </p>
        <div className="ck-sheet-actions">
          <button type="button" onClick={() => { onClose(); onOpenChart?.(row.ticker); }}>Chart ›</button>
          {onOpenOptions ? <button type="button" onClick={() => { onClose(); onOpenOptions(row.ticker); }}>Options page ›</button> : null}
        </div>
      </div>
    </div>
  );
}

// A row, card or headline that opens the facts card on tap or Enter; its own buttons (Chart) keep their job.
function pickProps(row, onPick) {
  if (!onPick) return {};
  return {
    role: "button",
    tabIndex: 0,
    "aria-label": `${row.ticker} ${contractLabel(row)} details`,
    onClick: (event) => { if (!event.target.closest("button")) onPick(row); },
    onKeyDown: (event) => { if (event.key === "Enter" && event.target === event.currentTarget) onPick(row); },
  };
}
export { pickProps };

function Pick({ label, row, onPick }) {
  if (!row) return <div><dt>{label}</dt><dd className="leapoi-dim">—</dd></div>;
  const tone = row.side === "PUT" ? "is-put" : "is-call";
  return (
    <div className={onPick ? "leapoi-pick" : undefined} {...pickProps(row, onPick)}>
      <dt>{label}</dt>
      <dd><b className={tone}>{contractLabel(row)}</b> {expiryLabel(row.expiry)} · OI <b>{formatOi(row.oi)}</b> <OiChange row={row} short /> · Δ {deltaText(row.delta)} · Mid {midText(row)}</dd>
    </div>
  );
}

// Calls vs puts (2026-11-17 "who is dominating call or put?"): one bar, green = calls, red = puts, and who leads.
// Shared with AGX Flow. Facts only.
export function Dominance({ calls, puts, format = formatOi, title, compact = false }) {
  const d = dominance(calls, puts);
  const tone = d.leader === "calls" ? "is-call" : d.leader === "puts" ? "is-put" : "is-even";
  return (
    <div className={`oi-dom${compact ? " is-compact" : ""}`} data-testid="dominance" data-leader={d.leader}>
      <div className="oi-dom-head">
        {title ? <small className="oi-dom-title">{title}</small> : null}
        <b className={tone}>{d.text}</b>
        {compact ? null : <small>Calls {format(calls)} · Puts {format(puts)}</small>}
      </div>
      <div className="oi-dom-bar" aria-label={`Calls ${d.callPct}%, puts ${d.putPct}%`}>
        <span className="is-call" style={{ width: `${d.callPct}%` }} />
        <span className="is-put" style={{ width: `${d.putPct}%` }} />
      </div>
    </div>
  );
}

// One grey line, tap to see the names: what the board leaves out (ETFs, stocks under $10).
export function LeftOut({ excluded, minPrice, className }) {
  const text = leftOutText(excluded, minPrice);
  if (!text) return null;
  return (
    <details className={className} data-testid="board-left-out">
      <summary>{text}</summary>
      {(excluded || []).map((x) => x.ticker).join(", ")}
    </details>
  );
}

// By ticker (2026-11-17, "I see only mag7 tickets what about other tickets from watchlist"): one line per ticker, so
// the small names are not buried under Mag7's huge open interest in the ranked lists.
function ByTicker({ list, onOpenChart, onPick, quotes }) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState("az");
  const { shown, total } = filterByTicker(sortByTicker(list, sort), query);
  return (
    <>
      <h3>By ticker</h3>
      <p className="leapoi-sub">All {total} tickers on the board: each one's biggest call and put open interest, and its biggest ENTRY call and put.</p>
      <input className="leapoi-search" type="search" placeholder="Filter this list" value={query} aria-label="Filter this list"
        onChange={(event) => setQuery(event.target.value)} />
      <Chips label="Order" value={sort} onChange={setSort} items={BYTK_SORTS} />
      <div className="leapoi-bytk">
        {shown.map((line) => (
          <div className="leapoi-card leapoi-bytk-card" key={line.ticker} data-testid="leap-oi-ticker">
            <div className="leapoi-card-head">
              <b className="leapoi-tk">{line.ticker}</b>
              <small><StockPrice row={line} quotes={quotes} /></small>
              <small className="leapoi-dim">Calls {formatOi(line.callOi)} · Puts {formatOi(line.putOi)}</small>
              <button type="button" onClick={() => onOpenChart?.(line.ticker)}>Chart ›</button>
            </div>
            <Dominance calls={line.callOi} puts={line.putOi} compact />
            <dl className="leapoi-card-grid leapoi-bytk-grid">
              <Pick label="Most call OI" row={line.call} onPick={onPick} />
              <Pick label="Most put OI" row={line.put} onPick={onPick} />
              <Pick label="ENTRY call" row={line.entryCall} onPick={onPick} />
              <Pick label="ENTRY put" row={line.entryPut} onPick={onPick} />
            </dl>
          </div>
        ))}
      </div>
      {!shown.length ? <p className="leapoi-empty">{query ? "No ticker on the board starts with that." : "Nothing read yet."}</p> : null}
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
      board: p.board === "watchlist" || p.board === "both" ? "watchlist" : "mag7",   // an old saved "both" opens as Watchlist
      range: RANGES.some((r) => r.id === p.range) ? p.range : "leap",
      delta: DELTAS.some((d) => d.id === p.delta) ? p.delta : "min",
      side: SIDES.some((s) => s.id === p.side) ? p.side : "both",
    };
  } catch {
    return { board: "mag7", range: "leap", delta: "min", side: "both" };
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

function Hero({ title, row, onOpenChart, onPick, quotes }) {
  if (!row) return null;
  const tone = row.side === "PUT" ? "is-put" : "is-call";
  return (
    <div className="leapoi-hero" {...pickProps(row, onPick)}>
      <small>{title}</small>
      <b><span className="leapoi-tk">{row.ticker}</span> <span className={tone}>{contractLabel(row)}</span>{row.entry ? <span className="leapoi-tag">ENTRY</span> : null}</b>
      <span>Stock <StockPrice row={row} quotes={quotes} /> · strike {awayNow(row, quotes)} from price</span>
      <span>Today's trades: <TodayFlow row={row} /></span>
      <span>{expiryLabel(row.expiry)} · {row.days}d · OI <b>{formatOi(row.oi)}</b> <OiChange row={row} /> · Δ {deltaText(row.delta)}</span>
      <span>Bid {priceText(row.bid)} · <b>Mid {midText(row)}</b> · Ask {priceText(row.ask)} · 1 contract {costText(row)}</span>
      <button type="button" onClick={() => onOpenChart?.(row.ticker)}>Chart ›</button>
    </div>
  );
}

function Rows({ rows, onOpenChart, onPick, testId, quotes }) {
  const top = rows.length ? Math.max(...rows.map((r) => r.oi || 0)) : 0;
  if (!rows.length) return <p className="leapoi-empty">Nothing in this range yet.</p>;
  return (
    <>
      <table className="leapoi-table">
        <thead>
          <tr><th>#</th><th>Ticker</th><th>Contract</th><th>Expiry</th><th className="num">Open interest</th><th className="num">Delta</th><th className="num">Bid / Ask</th><th className="num">Mid</th><th className="num" title="Mid price x 100 shares">1 contract</th><th className="num">From price</th><th aria-label="Chart" /></tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const tone = row.side === "PUT" ? "is-put" : "is-call";
            return (
              <tr key={`${row.ticker}-${row.side}-${row.expiry}-${row.strike}`} data-testid={testId} className="leapoi-tap" {...pickProps({ ...row, rank: index }, onPick)}>
                <td className="leapoi-rank">{index + 1}</td>
                <td><b className="leapoi-tk">{row.ticker}</b><small><StockPrice row={row} quotes={quotes} /></small></td>
                <td><b className={tone}>{contractLabel(row)}</b>{row.entry ? <span className="leapoi-tag">ENTRY</span> : null}</td>
                <td>{expiryLabel(row.expiry)} <small>{row.days}d</small></td>
                <td className="num"><b>{formatOi(row.oi)}</b><OiChange row={row} short /><i className={`leapoi-bar ${tone}`} style={{ width: `${barWidth(row.oi, top)}%` }} /><small><TodayFlow row={row} /></small></td>
                <td className="num">{deltaText(row.delta)}</td>
                <td className="num">{priceText(row.bid)} / {priceText(row.ask)}</td>
                <td className="num"><b>{midText(row)}</b></td>
                <td className="num">{costText(row)}</td>
                <td className="num leapoi-dim">{awayNow(row, quotes)}</td>
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
            <div className="leapoi-card leapoi-tap" key={`${row.ticker}-${row.side}-${row.expiry}-${row.strike}`} data-testid={`${testId}-card`} {...pickProps({ ...row, rank: index }, onPick)}>
              <div className="leapoi-card-head">
                <span className="leapoi-rank">{index + 1}</span>
                <b className="leapoi-tk">{row.ticker}</b>
                <b className={tone}>{contractLabel(row)}</b>
                {row.entry ? <span className="leapoi-tag">ENTRY</span> : null}
                <button type="button" onClick={() => onOpenChart?.(row.ticker)}>Chart ›</button>
              </div>
              <dl className="leapoi-card-grid">
                <div><dt>Stock price</dt><dd><StockPrice row={row} quotes={quotes} /></dd></div>
                <div className="leapoi-flow-fact"><dt>Today's trades (bought or sold)</dt><dd><TodayFlow row={row} /></dd></div>
                <div><dt>Expiry</dt><dd>{expiryLabel(row.expiry)} · {row.days}d</dd></div>
                <div><dt>OI</dt><dd><b>{formatOi(row.oi)}</b> <OiChange row={row} short /></dd></div>
                <div><dt>Delta</dt><dd>{deltaText(row.delta)}</dd></div>
                <div><dt>Bid / Ask</dt><dd>{priceText(row.bid)} / {priceText(row.ask)}</dd></div>
                <div><dt>Mid</dt><dd><b>{midText(row)}</b></dd></div>
                <div><dt>1 contract at mid</dt><dd>{costText(row)}</dd></div>
                <div><dt>Bid-ask spread</dt><dd>{spreadText(row)}</dd></div>
                <div><dt>From price</dt><dd>{awayNow(row, quotes)}</dd></div>
              </dl>
              <p className="simple-why-line" data-testid="why-line"><b>Why it's here:</b> {leapWhy(row, { rank: index }).verdict} <span>Tap for the simple explanation.</span></p>
              <i className={`leapoi-bar ${tone}`} style={{ width: `${barWidth(row.oi, top)}%` }} />
            </div>
          );
        })}
      </div>
    </>
  );
}

export default function LeapOiScannerPanel({ onOpenChart, onOpenOptions }) {
  const [prefs, setPrefs] = useState(readPrefs);
  const [watchlist, setWatchlist] = useState(null);   // null until /api/watchlist answers (or fails)
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState(null);         // the contract whose facts card is open

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

  // A searched ticker replaces the board until "Back to board" (not saved: the page always reopens on a board).
  const [search, setSearch] = useState("");
  const startSearch = (ticker) => { if (ticker !== search) { setPayload(null); setError(""); } setSearch(ticker); };
  const endSearch = () => { if (search) { setPayload(null); setError(""); } setSearch(""); };
  const tickers = useMemo(() => (search ? [search] : boardTickers(prefs.board, watchlist)), [search, prefs.board, watchlist]);
  // Wait for the watchlist before the first read, so Schwab is not asked for Mag7 alone and then again.
  const ready = (Boolean(search) || prefs.board === "mag7" || watchlist !== null) && tickers.length > 0;
  const emptyWatchlist = !search && prefs.board !== "mag7" && watchlist !== null && tickers.length === 0;
  const note = search ? searchNote(payload, search, "LEAP OI Scanner") : "";
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

  // Current stock prices for every ticker on screen, once a minute (the chain copy's price can be 12 hours old).
  const [quotes, setQuotes] = useState({});
  const quoteKey = useMemo(() => tickersOnScreen(payload).join(","), [payload]);
  useEffect(() => {
    if (!quoteKey) return undefined;
    let alive = true;
    let timer = null;
    const load = async () => {
      try {
        const response = await fetch(`/api/watchlist-quotes?symbols=${encodeURIComponent(quoteKey)}`);
        const next = response.ok ? quotesFrom(await response.json().catch(() => null)) : {};
        if (alive && Object.keys(next).length) setQuotes(next);
      } catch { /* keep the last prices; the card falls back to the price at read */ }
      if (alive) timer = window.setTimeout(load, LIVE_PRICE_POLL_MS);
    };
    load();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [quoteKey]);

  const progress = progressText(payload);
  const closePick = useCallback(() => setPicked(null), []);
  const anyChange = (payload?.rows || []).some((r) => r.oiChange !== null && r.oiChange !== undefined);
  const readAt = readAtText(payload);
  const errors = (payload?.tickers || []).filter((t) => t.state === "error");

  return (
    <div className="leapoi" data-testid="leap-oi-view">
      <header className="leapoi-head">
        <h2>LEAP OI Scanner</h2>
        <p>Strikes with the most open interest, {RANGES.find((r) => r.id === prefs.range)?.label.replace("LEAP ", "")} months out · {search ? `${search} only` : prefs.board === "mag7" ? "Mag7" : "Watchlist"}{readAt ? ` · ${readAt}` : ""}</p>
      </header>
      <BeginnerGuide page="leapOi" />
      <TickerSearch active={search} onSearch={startSearch} onClear={endSearch} />
      <div className="leapoi-filters">
        <Chips label="Board" value={search ? "" : prefs.board} onChange={(v) => { endSearch(); setPref("board", v); }}
          items={[{ id: "mag7", label: "Mag7" }, { id: "watchlist", label: "Watchlist" }]} />
        <Chips label="Months out" value={prefs.range} onChange={(v) => setPref("range", v)} items={RANGES} />
        <Chips label="Delta" value={prefs.delta} onChange={(v) => setPref("delta", v)} items={DELTAS} />
        <Chips label="Side" value={prefs.side} onChange={(v) => setPref("side", v)} items={SIDES} />
      </div>
      {progress ? <p className="leapoi-progress" role="status">{progress}</p> : null}
      {error ? <p className="leapoi-error" role="alert">{error}</p> : null}
      {search ? <TickerStory ticker={search} /> : null}
      {note ? <p className="leapoi-empty" data-testid="search-note">{note}</p> : null}
      {emptyWatchlist ? <p className="leapoi-empty">Your watchlist has no tickers besides Mag7.</p> : null}
      {loading && !payload && !emptyWatchlist ? <p className="leapoi-progress">Loading…</p> : null}
      {payload ? (
        <>
          <div className="leapoi-heros">
            <Hero title="Biggest call open interest" row={payload.biggestCall} onOpenChart={onOpenChart} onPick={setPicked} quotes={quotes} />
            <Hero title="Biggest put open interest" row={payload.biggestPut} onOpenChart={onOpenChart} onPick={setPicked} quotes={quotes} />
          </div>
          {payload.side === "both" ? (
            <Dominance title="Calls vs puts on this board" {...boardTotals(payload.byTicker)} />
          ) : null}
          <h3>ENTRY contracts with the most open interest</h3>
          <p className="leapoi-sub">Each expiry's ENTRY, picked exactly like the option chain: out of the money, delta just under 0.20.</p>
          <Rows rows={(payload.entries || []).slice(0, 15)} onOpenChart={onOpenChart} onPick={setPicked} testId="leap-oi-entry-row" quotes={quotes} />
          <h3>All strikes with the most open interest</h3>
          <p className="leapoi-sub">Biggest open interest at the delta you picked. ENTRY tag when that strike is the chain's ENTRY. Tap a contract for its details.</p>
          <p className="leapoi-sub" data-testid="oi-change-note">{anyChange
            ? "Green or red next to open interest: the change since the previous day's read (new contracts opened or closed)."
            : "Open interest change since the previous day shows once a second day has been read."}</p>
          <Rows rows={payload.rows || []} onOpenChart={onOpenChart} onPick={setPicked} testId="leap-oi-row" quotes={quotes} />
          <ByTicker list={payload.byTicker || []} onOpenChart={onOpenChart} onPick={setPicked} quotes={quotes} />
          {errors.length ? <p className="leapoi-sub">Not read: {errors.map((t) => t.ticker).join(", ")} ({errors[0].error})</p> : null}
          <LeftOut excluded={payload.excluded} minPrice={payload.minPrice} className="leapoi-sub" />
          <p className="leapoi-note">{payload.note}</p>
        </>
      ) : null}
      <ContractSheet row={picked} onClose={closePick} onOpenChart={onOpenChart} onOpenOptions={onOpenOptions} quotes={quotes}
        why={picked ? leapWhy(picked, { rank: Number.isInteger(picked.rank) ? picked.rank : null, price: stockPrice(picked, quotes).price }) : null}
        volumeLabel={`Volume at last read${readAt ? ` (${readAt.replace(/^Read /, "")})` : ""}`} />
    </div>
  );
}
