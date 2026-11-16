// Ticker trader (2026-11-13, his "I will type the ticker and you trade start with 10k fresh
// account entry and target and exit you have to pick it from charts"): BOT workspace > Research.
// He types a ticker and picks Call or Put; the server (momx/ticker_trader.py) trades it by the
// chart's plan in a simulated $10,000 account. Nothing here or there sends an order to a broker.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bot } from "lucide-react";
import { openAppLink } from "./appLinks.js";
import { DailyBars, Gauge } from "./BotDesk.jsx";
import { deskCalendar, deskCurve, deskKpis, deskMonthKey } from "./botDesk.js";
import {
  ALL_SCOPE, DEFAULT_SIZE, JOURNAL_POLL_MS, NOTE_MAX, NOTE_URL, REVIEW_LABEL, REVIEW_URL, SIMULATED_NOTE, SIZE_CHOICES, TICKER_CHIPS,
  TICKER_TRADER_POLL_MS, TICKER_TRADER_URL, breakdownTables, chartLink, cleanTicker, contractLabel, equityDays, failureText, improveItems,
  journalStats, journalTrades, journalUrl, ladder, money, noteProblem, openGain, parseSize, runLine, saveSize, savedSize, shouldAskReview,
  sideLabel, signedMoney, sizeChip, sizeProblem, statusLabel, tickerProblem, timeEt, tone, tradeDetail, tradeRows, winsLabel,
} from "./tickerTrader.js";
import "./tickerTrader.css";

const RULES = [
  ["Direction", "You choose: Call or Put. Call is the default."],
  ["Entry", "The option's ask, the same contract as the chart's Entry line."],
  ["Stop", "The stock price where the option is down 50%."],
  ["Targets", "T1, T2, T3 from the chart. One contract sold at each. Stop to break-even after T1, to T1 after T2."],
  ["Size", "3 contracts, at most 10% of the account in one trade."],
  ["Hours", "Buys the moment you tap Trade while the market is open. Everything is closed at 3:50 PM ET."],
];

function localStore() {
  try { return window.localStorage; } catch { return null; }
}

/** Account size: quick buttons $2K/$5K/$10K/$100K and a box for any amount. */
function SizePicker({ text, onText }) {
  const picked = parseSize(text);
  return (
    <div className="tt-size" data-testid="tt-size">
      <span className="tt-dim">Account size</span>
      <div className="tt-size-row" role="group" aria-label="Account size">
        {SIZE_CHOICES.map((n) => (
          <button key={n} type="button" aria-pressed={picked === n} className={picked === n ? "is-on" : ""}
            data-testid={`tt-size-${n}`} onClick={() => onText(String(n))}>{sizeChip(n)}</button>
        ))}
        <input type="text" inputMode="numeric" autoComplete="off" spellCheck="false" aria-label="Account size in dollars"
          data-testid="tt-size-input" maxLength={12} value={text} onChange={(e) => onText(e.target.value)} />
      </div>
    </div>
  );
}

async function readJson(res) {
  try { return await res.json(); } catch { return null; }
}

/** The equity curve from the starting balance, one point per closed trade (botDesk.deskCurve geometry). */
function EquityCurve({ doc }) {
  const days = useMemo(() => equityDays(doc), [doc]);
  const c = useMemo(() => deskCurve(days, {}), [days]);
  if (!c.points.length) return <p className="desk-note">The curve starts with the first closed trade.</p>;
  const start = Number(doc.startBalance) || 0;
  return (
    <svg className="desk-curve-svg" viewBox={`0 0 ${c.width} ${c.height}`} role="img" preserveAspectRatio="none" data-testid="tj-equity"
      aria-label={`Balance after each trade, from ${money(start)} to ${money(start + c.last.cum)}`}>
      <line className="desk-line-zero" x1="0" x2={c.width} y1={c.zeroY} y2={c.zeroY} />
      {c.points.length > 1 ? <polyline className={"desk-line-pnl " + tone(c.last.cum)} points={c.line} /> : null}
      {c.points.map((p) => (
        <circle key={p.day} className={"desk-dot " + tone(p.pnl)} cx={p.x} cy={p.y} r="3.5">
          <title>{`Trade ${signedMoney(p.pnl)}, balance ${money(start + p.cum)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function MonthCalendar({ doc }) {
  const thisMonth = deskMonthKey(doc.asOf);
  const [month, setMonth] = useState("");
  const cal = deskCalendar(doc, month || thisMonth, String(doc.asOf || "").slice(0, 10));
  if (!cal) return null;
  return (
    <section className="desk-card tj-card" aria-label="Calendar" data-testid="tj-calendar">
      <div className="desk-card-head">
        <h4>{cal.label}</h4>
        <span className="desk-cal-nav">
          <button type="button" className="desk-btn" onClick={() => setMonth(cal.prev)} aria-label="Previous month">‹</button>
          <button type="button" className="desk-btn" onClick={() => setMonth("")} disabled={cal.key === thisMonth}>This month</button>
          <button type="button" className="desk-btn" onClick={() => setMonth(cal.next)} aria-label="Next month" disabled={cal.key >= thisMonth}>›</button>
        </span>
        <span className="desk-cal-total">Month: <b className={cal.tone}>{cal.pnl}</b> · {cal.days} {cal.days === 1 ? "day" : "days"} traded</span>
      </div>
      <div className="desk-cal" role="table" aria-label={`Daily results for ${cal.label}`}>
        <div className="desk-cal-row is-head" role="row">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Week"].map((name) => <span key={name} role="columnheader">{name}</span>)}
        </div>
        {cal.weeks.map((week) => (
          <div key={week.key} className="desk-cal-row" role="row">
            {week.cells.map((c) => (
              <span key={c.key} role="cell" title={c.title} data-day={c.inMonth ? c.key : undefined}
                className={"desk-cal-cell" + (c.inMonth ? "" : " is-out") + (c.tone ? " " + c.tone : "") + (c.isToday ? " is-today" : "")}>
                {c.inMonth ? <span className="desk-cal-num">{c.num}</span> : null}
                {c.pnl ? <b className="desk-cal-pnl">{c.pnl}</b> : null}
                {c.trades ? <span className="desk-cal-trades">{c.trades} {c.trades === 1 ? "trade" : "trades"}</span> : null}
              </span>
            ))}
            <span role="cell" className={"desk-cal-cell is-week" + (week.tone ? " " + week.tone : "")}>
              <b className="desk-cal-pnl">{week.pnl}</b>
              {week.days ? <span className="desk-cal-trades">{week.days} {week.days === 1 ? "day" : "days"}</span> : null}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

/** One trade opened from the journal: its plan, what was hit and when, the chart button and his note. */
function TradeCard({ trade, onSaved }) {
  const d = tradeDetail(trade);
  const [text, setText] = useState(d.note);
  const [state, setState] = useState({ busy: false, error: "", saved: false });
  useEffect(() => { setText(d.note); setState({ busy: false, error: "", saved: false }); }, [d.id]);    // another trade
  const save = async () => {
    const problem = noteProblem(text);
    if (problem) { setState({ busy: false, error: problem, saved: false }); return; }
    setState({ busy: true, error: "", saved: false });
    try {
      const res = await fetch(NOTE_URL, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: d.id, text }), signal: AbortSignal.timeout(15000) });
      const out = await readJson(res);
      if (!res.ok || !out?.ok) { setState({ busy: false, error: failureText(res.status, out), saved: false }); return; }
      setText(out.note.text);
      setState({ busy: false, error: "", saved: true });
      onSaved(d.id, out.note);
    } catch {
      setState({ busy: false, error: failureText(0, null), saved: false });
    }
  };
  return (
    <div className="tt-card tj-detail" data-testid="tj-detail">
      <div className="tt-card-head"><b className="tt-contract">{d.title}</b><b className={d.tone}>{d.plText}{d.pct ? ` · ${d.pct}` : ""}</b></div>
      <span className="tt-why">{d.outcome} · held {d.hold}</span>
      <div className="tt-ladder" role="list" aria-label="The plan it was bought on">
        {d.levels.map((row) => (
          <div key={row.key} role="listitem" data-testid={`tj-level-${row.key}`}
            className={`tt-level is-${row.key === "Entry" ? "entry" : row.key === "Stop" ? "stop" : "target"}` + (row.hit ? " is-hit" : "")}>
            <b>{row.label}</b><span>{row.value}</span><small>{row.note}</small>
          </div>
        ))}
      </div>
      {d.events.map((e) => <span key={e.key} className="tt-why">{e.at} · {e.text}</span>)}
      <button type="button" className="tt-chart" data-testid="tj-chart" onClick={() => openAppLink(d.link)}>
        Open {trade.ticker} chart{d.link.day ? ` (${d.link.day.slice(5)})` : ""} ›
      </button>
      <label className="tt-dim" htmlFor={`tj-note-${d.id}`}>Your note (only you see this)</label>
      <textarea id={`tj-note-${d.id}`} className="tj-note" data-testid="tj-note" rows={3} maxLength={NOTE_MAX} value={text}
        onChange={(e) => { setText(e.target.value); setState((s) => ({ ...s, saved: false, error: "" })); }}
        placeholder="What you saw, what you would do differently…" />
      <div className="tj-note-bar">
        <small className="tt-dim">{text.length.toLocaleString("en-US")} / {NOTE_MAX.toLocaleString("en-US")}</small>
        {state.saved ? <small className="tt-dim" role="status" data-testid="tj-note-saved">Saved</small> : null}
        <button type="button" className="tt-primary" disabled={state.busy || text === d.note} data-testid="tj-note-save" onClick={save}>
          {state.busy ? "Saving…" : "Save note"}
        </button>
      </div>
      {state.error ? <p className="bot-error" role="alert" data-testid="tj-note-error">{state.error}</p> : null}
    </div>
  );
}

/** The journal for one run or every run: tiles, curve, daily bars, calendar, AI review, what to improve,
 * breakdowns and every trade. Reads the journal every minute while on screen; the AI review is asked for at
 * most once each time the journal opens (the server answers from its cache after the first that day). */
function Journal({ scope, runs, onScope, onBack }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const [review, setReview] = useState({ text: null, writing: false });
  const [openTrade, setOpenTrade] = useState(null);
  const asked = useRef(new Set());
  const alive = useRef(true);
  // The read this opening already sent: a re-run of the opening effect for the same journal within a second
  // (React's development double mount) uses it instead of asking the server twice.
  const opened = useRef({ scope: null, at: 0 });

  const load = useCallback(async () => {
    try {
      const res = await fetch(journalUrl(scope), { credentials: "include", cache: "no-store", signal: AbortSignal.timeout(15000) });
      const body = await readJson(res);
      if (!alive.current) return;
      if (res.ok && body && body.stats) { setDoc(body); setError(""); } else setError(failureText(res.status, body));
    } catch {
      if (alive.current) setError(failureText(0, null));
    }
  }, [scope]);

  useEffect(() => {
    alive.current = true;
    const now = Date.now();
    const again = opened.current.scope === scope && now - opened.current.at < 1000;
    if (!again) {
      opened.current = { scope, at: now };
      setDoc(null);
      setOpenTrade(null);
      setReview({ text: null, writing: false });
      load();
    }
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, JOURNAL_POLL_MS);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [load]);

  // Today's AI review: one POST per opening at most, only when the server has none and no try recorded today.
  useEffect(() => {
    if (!doc || doc.scope !== (scope || ALL_SCOPE) || !shouldAskReview(doc, asked.current.has(doc.scope))) return;
    asked.current.add(doc.scope);
    setReview({ text: null, writing: true });
    fetch(REVIEW_URL, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: doc.scope }), signal: AbortSignal.timeout(30000) })
      .then((res) => readJson(res).then((out) => (res.ok && out?.ok ? out : null)))
      .catch(() => null)
      .then((out) => { if (alive.current) setReview({ text: out?.review || null, writing: false }); });
  }, [doc, scope]);

  const shown = review.text || doc?.review || null;
  const kpis = doc ? deskKpis(doc) : [];
  const trades = doc ? journalTrades(doc) : [];
  const picked = openTrade && doc ? (doc.trades || []).find((t) => t.id === openTrade) : null;
  const onSaved = (id, note) => setDoc((d) => (d ? { ...d, trades: d.trades.map((t) => (t.id === id ? { ...t, note: note.text, noteAt: note.at } : t)) } : d));

  return (
    <section className="tt-run tj desk" data-testid="tj" aria-label="Journal">
      <div className="tt-run-bar">
        <button type="button" className="tt-back" onClick={onBack}>‹ Ticker trader</button>
      </div>
      <div className="tj-scopes" role="group" aria-label="Which journal">
        {[{ id: ALL_SCOPE, label: "All tickers" }, ...runs.map((r) => ({ id: r.id, label: `${r.ticker} ${sideLabel(r.side)}` }))].map((s) => (
          <button key={s.id} type="button" aria-pressed={(scope || ALL_SCOPE) === s.id} className={(scope || ALL_SCOPE) === s.id ? "is-on" : ""}
            onClick={() => onScope(s.id)}>{s.label}</button>
        ))}
      </div>
      <div className="tt-run-head">
        <div>
          <h3>Journal · {doc?.label || (scope === ALL_SCOPE ? "All tickers" : scope)}</h3>
          <span className="tt-dim">{doc ? `Simulated trades · ${money(doc.startBalance)} start · ${money(doc.balance)} now` : "Loading…"}</span>
        </div>
      </div>
      {error ? <p className="bot-error" role="alert" data-testid="tj-error">{error}</p> : null}
      {doc ? (
        <>
          <section className="desk-tiles" aria-label="Key numbers">
            {kpis.map((k) => (
              <div key={k.key} className="desk-tile" data-tile={k.key}>
                <span className="desk-tile-label">{k.label}</span>
                <span className="desk-tile-row">
                  <span className={"desk-tile-value " + k.tone}>{k.value}</span>
                  {k.share == null ? null : <Gauge share={k.share} />}
                </span>
                <span className="desk-tile-sub">{k.sub}</span>
              </div>
            ))}
          </section>
          <div className="tj-stats" aria-label="More numbers">
            {journalStats(doc).map((x) => (
              <div key={x.key} className="tt-tile" data-testid={`tj-stat-${x.key}`}><span>{x.label.toUpperCase()}</span><b className={x.tone}>{x.value}</b>
                {x.sub ? <small className="tt-dim">{x.sub}</small> : null}</div>
            ))}
          </div>

          {review.writing ? <p className="tt-dim" role="status" data-testid="tj-review-writing">Writing today's AI review…</p> : null}
          {shown ? (
            <div className="tt-card tj-review" data-testid="tj-review">
              <div className="tt-card-head"><h4>AI REVIEW</h4><span className="tt-dim">{shown.asOf || ""}</span></div>
              <p className="tj-review-text">{shown.text}</p>
              <small className="tj-review-label">{REVIEW_LABEL}</small>
            </div>
          ) : null}

          <div className="tt-card" data-testid="tj-improve">
            <div className="tt-card-head"><h4>WHAT TO IMPROVE</h4><span className="tt-dim">from the numbers, no AI</span></div>
            {!improveItems(doc).length ? <span className="tt-why">No closed trades yet. This fills in as trades close.</span> : null}
            {improveItems(doc).map((x) => (
              <div key={x.key} className="tj-improve" data-testid="tj-improve-item">
                <b>{x.title}</b>
                <span className="tt-why">{x.text}</span>
                <small className="tt-dim">{x.count}{x.judge ? <span className="tj-judge"> · {x.judge}</span> : null}</small>
              </div>
            ))}
          </div>

          <div className="desk-grid">
            <section className="desk-card tj-card" aria-label="Equity curve">
              <div className="desk-card-head"><h4>Equity curve</h4><span className={"desk-big " + tone(doc.stats?.netPnl)}>{money(doc.balance)}</span></div>
              <EquityCurve doc={doc} />
              <p className="desk-legend"><span className="is-zero">Start {money(doc.startBalance)}</span></p>
            </section>
            <section className="desk-card tj-card" aria-label="Net daily P&L">
              <div className="desk-card-head"><h4>Net daily P&amp;L</h4><span className="desk-note">each bar is one day</span></div>
              {(doc.days || []).some((d) => d.closed) ? <DailyBars days={doc.days} /> : <p className="desk-note">The bars start with the first closed trade.</p>}
            </section>
          </div>
          <MonthCalendar doc={doc} />

          <div className="tj-breakdowns">
            {breakdownTables(doc).map((table) => (
              <div key={table.key} className="tt-card" data-testid={`tj-breakdown-${table.key}`}>
                <div className="tt-card-head"><h4>{table.title.toUpperCase()}</h4></div>
                <div className="tj-table" role="table" aria-label={table.title}>
                  <div className="tj-tr is-head" role="row"><span role="columnheader" /><span role="columnheader">Trades</span>
                    <span role="columnheader">Win</span><span role="columnheader">Net</span></div>
                  {table.rows.map((r) => (
                    <div key={r.key} className={"tj-tr" + (r.trades ? "" : " is-empty")} role="row">
                      <span role="cell">{r.label}</span><span role="cell">{r.trades}</span><span role="cell">{r.win}</span>
                      <b role="cell" className={r.tone}>{r.net}</b>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="tt-card">
            <div className="tt-card-head"><h4>TRADES</h4><span className="tt-dim">{trades.length || "none yet"}{doc.openTrades ? ` · ${doc.openTrades} open` : ""}</span></div>
            {trades.map((row) => (
              <div key={row.key}>
                <button type="button" className={"tt-row tj-trade" + (openTrade === row.id ? " is-open" : "")} data-testid="tj-trade"
                  aria-expanded={openTrade === row.id} onClick={() => setOpenTrade(openTrade === row.id ? null : row.id)}>
                  <span className="tt-row-main"><b>{row.title}{row.best ? " · best" : row.worst ? " · worst" : ""}</b>
                    <small>{row.when} · {row.outcome}{row.hasNote ? " · note" : ""}</small></span>
                  <span className={"tt-row-money " + row.tone}>{row.plText}</span>
                </button>
                {openTrade === row.id && picked ? <TradeCard trade={picked} onSaved={onSaved} /> : null}
              </div>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

function RunView({ run, busy, onBack, onAct, onJournal }) {
  // Reset opens the size picker, filled with this run's starting size.
  const [resetText, setResetText] = useState(null);
  const [resetError, setResetError] = useState("");
  const startSize = Number(run.startBalance) || DEFAULT_SIZE;
  const resetSize = resetText === null ? null : parseSize(resetText);
  const doReset = async () => {
    const problem = sizeProblem(resetText);
    if (problem) { setResetError(problem); return; }
    const done = await onAct("reset", { id: run.id, size: resetSize });
    if (done) { setResetText(null); setResetError(""); saveSize(localStore(), resetSize); }
  };
  const trade = run.open;
  const rows = tradeRows(run);
  const gain = openGain(trade);
  const canAgain = !trade && run.status !== "waiting";
  return (
    <section className="tt-run" data-testid="tt-run" aria-label={`${run.ticker} ${sideLabel(run.side)}`}>
      <div className="tt-run-bar">
        <button type="button" className="tt-back" onClick={onBack}>‹ Ticker trader</button>
        <div className="tt-run-actions">
          <button type="button" className="tt-ghost" disabled={Boolean(busy) || run.status === "stopped" || (!trade && run.status !== "waiting")}
            onClick={() => onAct("stop", { id: run.id })} title="Sell now at the bid and stop this ticker">Stop</button>
          <button type="button" className="tt-ghost" disabled={Boolean(busy) || resetText !== null} data-testid="tt-reset"
            onClick={() => { setResetText(String(startSize)); setResetError(""); }}
            title="Start fresh at an account size you pick, with an empty history">Reset</button>
        </div>
      </div>
      {resetText !== null ? (
        <div className="tt-card" data-testid="tt-reset-panel">
          <div className="tt-card-head"><h4>RESET</h4></div>
          <span className="tt-why">Closes any open trade, clears the history and starts fresh at this size.</span>
          <SizePicker text={resetText} onText={(t) => { setResetText(t); setResetError(""); }} />
          {resetError ? <p className="bot-error" role="alert" data-testid="tt-reset-error">{resetError}</p> : null}
          <div className="tt-entry">
            <button type="button" className="tt-primary" disabled={Boolean(busy)} data-testid="tt-reset-go" onClick={doReset}>
              {resetSize !== null && !sizeProblem(resetText) ? `Reset to ${money(resetSize)}` : "Reset"}
            </button>
            <button type="button" className="tt-ghost" data-testid="tt-reset-cancel"
              onClick={() => { setResetText(null); setResetError(""); }}>Cancel</button>
          </div>
        </div>
      ) : null}
      <div className="tt-run-head">
        <div>
          <h3>{run.ticker}</h3>
          <span className="tt-dim">{run.stock ? `${Number(run.stock).toFixed(2)} · as of ${timeEt(run.stockAt)}` : `${sideLabel(run.side)} · simulated`}</span>
        </div>
        <span className={"tt-pill" + (trade ? " is-live" : "")} data-testid="tt-status">{statusLabel(run)}</span>
      </div>
      <div className="tt-tiles">
        <div className="tt-tile"><span>BALANCE</span><b data-testid="tt-balance">{money(run.balance)}</b><small className="tt-dim" data-testid="tt-from">from {money(startSize)}</small></div>
        <div className="tt-tile"><span>NET</span><b className={tone(run.net)}>{signedMoney(run.net)}</b></div>
        <div className="tt-tile"><span>WINS</span><b>{winsLabel(run)}</b></div>
      </div>
      {run.note ? <p className="tt-note" role="status" data-testid="tt-note">{run.note}{run.noteAt ? ` · ${timeEt(run.noteAt)}` : ""}</p> : null}
      {trade ? (
        <div className="tt-card" data-testid="tt-open">
          <div className="tt-card-head"><h4>OPEN TRADE</h4><span className="tt-dim">bought {timeEt(trade.boughtAt)}</span></div>
          <div className="tt-card-head">
            <b className="tt-contract">{contractLabel(trade)}</b>
            {gain ? <b className={gain.startsWith("+") ? "is-up" : gain.startsWith("−") ? "is-down" : ""}>{gain}</b> : null}
          </div>
          <span className="tt-why">Why: {trade.why}</span>
          <span className="tt-why">Holding {trade.held} of {trade.qty}{trade.bid ? ` · bid $${Number(trade.bid).toFixed(2)} at ${timeEt(trade.bidAt)}` : ""}</span>
          <div className="tt-ladder" role="list" aria-label="Chart plan">
            {ladder(trade).map((row) => (
              <div key={row.key} role="listitem" data-testid={`tt-level-${row.key}`}
                className={`tt-level is-${row.kind}` + (row.hit ? " is-hit" : "")}>
                <b>{row.label}</b><span>{row.value}</span><small>{row.note}</small>
              </div>
            ))}
          </div>
          {(trade.events || []).map((event, i) => <span key={i} className="tt-why">{timeEt(event.at)} · {event.text}</span>)}
        </div>
      ) : (
        <div className="tt-card">
          <div className="tt-card-head"><h4>NO TRADE OPEN</h4></div>
          <span className="tt-why">{run.status === "waiting" ? "The bot buys as soon as it has a fresh price." : "The bot does not buy again by itself."}</span>
          {canAgain ? <button type="button" className="tt-primary" disabled={Boolean(busy)} data-testid="tt-again"
            onClick={() => onAct("again", { id: run.id })}>Trade again</button> : null}
        </div>
      )}
      <button type="button" className="tt-chart" data-testid="tt-chart" onClick={() => openAppLink(chartLink(run.ticker))}>
        Open {run.ticker} chart ›
      </button>
      <button type="button" className="tt-chart" data-testid="tt-journal" onClick={() => onJournal(run.id)}>
        Journal and AI review for {run.ticker} ›
      </button>
      <div className="tt-card">
        <div className="tt-card-head"><h4>TRADES</h4><span className="tt-dim">{rows.length || "none yet"}</span></div>
        {rows.map((row) => (
          <div key={row.key} className={"tt-trade " + row.tone} data-testid="tt-trade">
            <div className="tt-card-head"><b>{row.title}</b><b className={row.tone}>{row.plText}</b></div>
            <span className="tt-why">{row.reason}</span>
            <span className="tt-dim">{row.when} · balance after {row.balanceAfter}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

export default function TickerTrader({ viewSwitch, onAllTrades, initialTicker = "", initialSide = "CALL" }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");
  // Filled in from the Research page's "Trade a ticker" box; Trade is still the trader's own tap.
  const [side, setSide] = useState(initialSide === "PUT" ? "PUT" : "CALL");
  const [ticker, setTicker] = useState(String(initialTicker || "").toUpperCase());
  const [busy, setBusy] = useState("");
  const [openId, setOpenId] = useState(null);
  const [journal, setJournal] = useState(null);         // null = not open; "all" or a run id
  const [sizeText, setSizeText] = useState(() => String(savedSize(localStore())));
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const res = await fetch(TICKER_TRADER_URL, { credentials: "include", cache: "no-store", signal: AbortSignal.timeout(15000) });
      const body = await readJson(res);
      if (!alive.current) return;
      if (res.ok && body && Array.isArray(body.runs)) { setDoc(body); setError(""); } else setError(failureText(res.status, body));
    } catch {
      if (alive.current) setError(failureText(0, null));
    }
  }, []);

  // Read every 10 s only while this panel is on screen (mounted and the page visible).
  useEffect(() => {
    alive.current = true;
    load();
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") load(); }, TICKER_TRADER_POLL_MS);
    return () => { alive.current = false; window.clearInterval(timer); };
  }, [load]);

  const act = useCallback(async (action, body) => {
    setBusy(action);
    setFormError("");
    try {
      const res = await fetch(`${TICKER_TRADER_URL}/${action}`, { method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
      const out = await readJson(res);
      if (!alive.current) return null;
      if (!res.ok || !out?.ok) { setFormError(failureText(res.status, out)); return null; }
      await load();
      return out.run;
    } catch {
      if (alive.current) setFormError(failureText(0, null));
      return null;
    } finally {
      if (alive.current) setBusy("");
    }
  }, [load]);

  const trade = async (raw) => {
    const problem = tickerProblem(raw);
    if (problem) { setFormError(problem); return; }
    const badSize = sizeProblem(sizeText);
    if (badSize) { setFormError(badSize); return; }
    const size = parseSize(sizeText);
    const run = await act("start", { ticker: cleanTicker(raw), side, size });
    if (run) { setTicker(""); setOpenId(run.id); saveSize(localStore(), size); }
  };

  const runs = doc?.runs || [];
  const shown = openId ? runs.find((r) => r.id === openId) : null;

  return (
    <div className="bot-page tt" data-testid="ticker-trader">
      <header className="bot-head">
        <div className="bot-title">
          <Bot size={18} aria-hidden="true" />
          <h2>BOT workspace</h2>
          <span className="bot-badge">SIMULATED</span>
          <span className="bot-badge is-admin">Admin only</span>
        </div>
        <div className="bot-controls">
          {viewSwitch}
          <div className="bot-seg" role="group" aria-label="Which bot">
            <button type="button" onClick={onAllTrades} title="Every trade the rules took">All trades</button>
            <button type="button" className="is-on" aria-pressed="true">Ticker trader</button>
          </div>
        </div>
      </header>
      {error ? <p className="bot-error" role="alert">{error}</p> : null}
      {formError ? <p className="bot-error" role="alert" data-testid="tt-error">{formError}</p> : null}
      {journal ? <Journal scope={journal} runs={runs} onScope={setJournal} onBack={() => setJournal(null)} />
        : shown ? <RunView run={shown} busy={busy} onBack={() => setOpenId(null)} onAct={act} onJournal={setJournal} /> : (
        <>
          <form className="tt-card" onSubmit={(e) => { e.preventDefault(); trade(ticker); }}>
            <label htmlFor="tt-ticker" className="tt-dim">Type a ticker and pick Call or Put. The bot trades it from the chart in a fresh simulated account.</label>
            <div className="tt-side" role="group" aria-label="Call or Put">
              {["CALL", "PUT"].map((s) => (
                <button key={s} type="button" aria-pressed={side === s} className={(side === s ? "is-on " : "") + (s === "PUT" ? "is-put" : "is-call")}
                  onClick={() => setSide(s)}>{sideLabel(s)}</button>
              ))}
            </div>
            <SizePicker text={sizeText} onText={(t) => { setSizeText(t); setFormError(""); }} />
            <div className="tt-entry">
              <input id="tt-ticker" type="text" inputMode="text" autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck="false"
                maxLength={7} placeholder="NVDA" value={ticker} onChange={(e) => { setTicker(e.target.value.toUpperCase()); setFormError(""); }} />
              <button type="submit" className="tt-primary" disabled={Boolean(busy)} data-testid="tt-trade">{busy === "start" ? "Starting…" : "Trade"}</button>
            </div>
            <div className="tt-chips" aria-label="Quick tickers">
              {TICKER_CHIPS.map((chip) => <button key={chip} type="button" onClick={() => { setTicker(chip); setFormError(""); }}>{chip}</button>)}
            </div>
          </form>
          <div className="tt-card">
            <div className="tt-card-head"><h4>YOUR TICKERS</h4><span className="tt-dim">{runs.length ? `${runs.length} of ${doc?.maxRuns || 10}` : ""}</span></div>
            {doc && !runs.length ? <span className="tt-why">None yet. Type a ticker above and tap Trade.</span> : null}
            {!doc && !error ? <span className="tt-why" role="status">Loading…</span> : null}
            {runs.map((run) => (
              <button key={run.id} type="button" className="tt-row" data-testid="tt-row" onClick={() => setOpenId(run.id)}>
                <span className="tt-row-main"><b>{run.ticker} · {sideLabel(run.side)}</b><small>{runLine(run)}</small></span>
                <span className={"tt-row-money " + tone(run.net)}>{money(run.balance)}</span>
              </button>
            ))}
            {runs.length ? (
              <button type="button" className="tt-chart" data-testid="tt-journal-all" onClick={() => setJournal(ALL_SCOPE)}>
                Journal and AI review, all tickers ›
              </button>
            ) : null}
          </div>
          <div className="tt-card">
            <div className="tt-card-head"><h4>WHAT THE BOT DOES</h4></div>
            <dl className="tt-rules">
              {RULES.map(([name, text]) => <div key={name}><dt>{name}</dt><dd>{text}</dd></div>)}
            </dl>
          </div>
        </>
      )}
      <p className="tt-foot" data-testid="tt-simulated">{SIMULATED_NOTE} Buys at the ask, sells at the bid, from live quotes; no real money.</p>
    </div>
  );
}
