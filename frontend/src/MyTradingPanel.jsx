// My Trading (2026-11-11, owner only, "Yes build it"): the owner's REAL
// Schwab option trades, read once a day after the close, as plain numbers.
// Data: GET /api/my-insights?days=7|30|90 (momx/my_insights.py), which only
// reads the saved copy. The one button that writes is "Refresh now"
// (POST /api/my-insights/refresh), which asks the server for one read-only
// Schwab read, at most once an hour. Nothing here can place or change an order.
import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import "./myTrading.css";
import {
  MY_TABLES, MY_TRADING_PERIODS, MY_TRADING_PERIOD_KEY, MY_TRADING_REFRESH_URL, TOO_FEW_LINE,
  basisLine, emptyLine, hasTrades, myEvents, myExecution, myHabits, mySchwab, myTable, myTiles, myTradingUrl, myVersus, myVersusBasis,
  openPositions, periodLabel, readPeriod, refreshReply,
} from "./myTrading.js";

function readStoredPeriod() {
  try { return readPeriod(window.localStorage.getItem(MY_TRADING_PERIOD_KEY)); } catch { return 30; }
}

function Tile({ tile }) {
  return (
    <div className={"mt-tile" + (tile.dim ? " is-dim" : "")} data-testid={`my-trading-tile-${tile.key}`}>
      <span className="mt-tile-label">{tile.label}</span>
      <span className={"mt-tile-value " + tile.tone}>{tile.value}</span>
      {tile.sub ? <span className="mt-tile-sub">{tile.sub}</span> : null}
    </div>
  );
}

function GroupTable({ doc, table, period }) {
  const rows = myTable(doc, table.key);
  if (!rows.length) return null;
  const total = rows.reduce((sum, r) => sum + r.trades, 0);
  return (
    <section className="mt-card" data-testid={`my-trading-table-${table.key}`}>
      <div className="mt-card-head">
        <h3>{table.title}</h3>
        <span className="mt-basis">{basisLine({ trades: total }, period)}</span>
      </div>
      <div className="mt-table-wrap">
        <table className="mt-table">
          <thead>
            <tr><th className="is-left">{table.head}</th><th>Trades</th><th>Win rate</th><th>Per trade</th><th>Profit factor</th><th>P/L</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key || r.label} className={r.few ? "is-few" : ""} title={r.few ? TOO_FEW_LINE : undefined}>
                <td className="is-left">{r.label}{r.few ? <span className="mt-few"> · {TOO_FEW_LINE}</span> : null}</td>
                <td>{r.trades}</td>
                <td>{r.winRate}</td>
                <td className={r.expectancyTone}>{r.expectancy}</td>
                <td>{r.profitFactor}</td>
                <td className={r.pnlTone}>{r.pnl}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function HabitCard({ habit }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={"mt-habit" + (habit.empty ? " is-empty" : "")} data-testid={`my-trading-habit-${habit.key}`}>
      <div className="mt-habit-head">
        <h4>{habit.title}</h4>
        <span className={"mt-habit-cost" + (habit.costValue ? " is-down" : "")}>{habit.costValue ? habit.cost : ""}</span>
      </div>
      <p className="mt-note">{habit.what}</p>
      <p className="mt-habit-line">{habit.line}{habit.approx ? " (approximate)" : ""}</p>
      {habit.note ? <p className="mt-note">{habit.note}</p> : null}
      {habit.trades.length ? (
        <>
          <button type="button" className="mt-link" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
            {open ? "Hide the trades" : `Show the ${habit.trades.length} ${habit.trades.length === 1 ? "trade" : "trades"}`}
          </button>
          {open ? (
            <ul className="mt-habit-trades">
              {habit.trades.map((t) => (
                <li key={t.id}>
                  <span className="mt-when">{t.when}</span>
                  <span className="mt-contract">{t.contract}</span>
                  <span className={t.tone}>{t.pnl}</span>
                  {t.note ? <span className="mt-note">{t.note}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </article>
  );
}

export default function MyTradingPanel() {
  const [days, setDays] = useState(readStoredPeriod);
  const [state, setState] = useState({ doc: null, loading: true, error: "" });
  const [reloadKey, setReloadKey] = useState(0);
  const [refresh, setRefresh] = useState({ busy: false, reply: null });

  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fetch(myTradingUrl(days), { credentials: "include", cache: "no-store" })
      .then(async (r) => {
        const body = await r.json().catch(() => ({}));
        if (!alive) return;
        if (r.status === 401) setState({ doc: null, loading: false, error: "Sign in to see this page." });
        else if (r.status === 403) setState({ doc: null, loading: false, error: "This page is for the owner only." });
        else if (!r.ok) setState((s) => ({ doc: s.doc, loading: false, error: body.error || "Could not load My Trading." }));
        else setState({ doc: body, loading: false, error: "" });
      })
      .catch(() => { if (alive) setState((s) => ({ doc: s.doc, loading: false, error: "Could not reach the server." })); });
    return () => { alive = false; };
  }, [days, reloadKey]);

  const pick = (n) => {
    setDays(n);
    try { window.localStorage.setItem(MY_TRADING_PERIOD_KEY, String(n)); } catch { /* private window */ }
  };

  const doc = state.doc;
  const schwab = mySchwab(doc);

  // While a read is running the saved copy is looked at again every 15 s (this
  // page's own GET, never Schwab), so the numbers appear when the read ends.
  useEffect(() => {
    if (!schwab.reading) return undefined;
    const id = window.setTimeout(() => setReloadKey((k) => k + 1), 15000);
    return () => window.clearTimeout(id);
  }, [schwab.reading, reloadKey]);

  const askRefresh = useCallback(() => {
    setRefresh({ busy: true, reply: null });
    fetch(MY_TRADING_REFRESH_URL, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(async (r) => ({ status: r.status, body: await r.json().catch(() => ({})) }))
      .then(({ status, body }) => {
        setRefresh({ busy: false, reply: refreshReply(status, body) });
        if (status === 200 || status === 202) window.setTimeout(() => setReloadKey((k) => k + 1), 4000);
      })
      .catch(() => setRefresh({ busy: false, reply: { tone: "bad", text: "Could not reach the server." } }));
  }, []);

  const period = periodLabel(doc || { days });
  const habits = myHabits(doc);
  const execution = myExecution(doc);
  const versus = myVersus(doc);
  const versusBasis = myVersusBasis(doc);
  const events = myEvents(doc);
  const open = openPositions(doc);

  return (
    <div className="mt" data-testid="my-trading">
      <header className="mt-head">
        <div className="mt-title">
          <h2>My Trading</h2>
          <p className="mt-note">Your real Schwab option trades, read-only. {period}.</p>
        </div>
        <div className="mt-periods" role="group" aria-label="Period">
          {MY_TRADING_PERIODS.map((n) => (
            <button key={n} type="button" aria-pressed={days === n} onClick={() => pick(n)}>{n} days</button>
          ))}
        </div>
      </header>

      <section className={"mt-source is-" + schwab.tone} data-testid="my-trading-schwab">
        <div>
          <strong>{schwab.title}</strong>
          <p className="mt-note">{schwab.line}</p>
        </div>
        <div className="mt-source-tools">
          <button type="button" className="mt-btn" disabled={!schwab.canRefresh || refresh.busy} onClick={askRefresh}>
            <RefreshCw size={14} aria-hidden="true" /> {refresh.busy ? "Asking…" : "Refresh now"}
          </button>
          {refresh.reply ? <span className={"mt-reply is-" + refresh.reply.tone} role="status">{refresh.reply.text}</span>
            : schwab.refreshNote ? <span className="mt-reply">{schwab.refreshNote}</span> : null}
        </div>
      </section>

      {state.error ? <p className="mt-note is-bad" role="alert">{state.error}</p> : null}
      {state.loading && !doc ? <p className="mt-note">Loading…</p> : null}

      {events.items.length || events.note ? (
        <section className="mt-events" data-testid="my-trading-events" aria-label="Upcoming market events">
          <span className="mt-events-label">Next 7 days</span>
          {events.items.length ? events.items.map((e) => (
            <span key={e.key} className={"mt-event" + (e.today ? " is-today" : "") + (e.high ? " is-high" : "")}>
              <b>{e.day}{e.time ? ` ${e.time}` : ""}</b> {e.title}
            </span>
          )) : <span className="mt-note">{events.note}</span>}
        </section>
      ) : null}

      {doc && !hasTrades(doc) ? (
        <p className="mt-note" data-testid="my-trading-empty">{emptyLine(doc)}</p>
      ) : null}

      {doc && hasTrades(doc) ? (
        <>
          <section className="mt-tiles" data-testid="my-trading-tiles">
            {myTiles(doc).map((t) => <Tile key={t.key} tile={t} />)}
          </section>

          <section className="mt-section" data-testid="my-trading-habits">
            <h3>Habits that cost money</h3>
            {habits.length ? (
              <div className="mt-habits">{habits.map((h) => <HabitCard key={h.key} habit={h} />)}</div>
            ) : <p className="mt-note">No habit checks in this period.</p>}
          </section>

          {execution ? (
            <section className="mt-card" data-testid="my-trading-execution">
              <div className="mt-card-head"><h3>Fills vs the middle price</h3></div>
              {execution.measured ? (
                <div className="mt-exec">
                  <Tile tile={{ key: "spread", label: execution.spreadLabel, value: execution.spreadCost, tone: "", sub: "" }} />
                  <Tile tile={{ key: "spreadAvg", label: execution.avgLabel, value: execution.avgPerFill, tone: "", sub: execution.avgPctLine }} />
                  <Tile tile={{ key: "vsMid", label: "Fills vs mid", value: execution.vsMid, tone: "", sub: "" }} />
                </div>
              ) : null}
              <p className="mt-note">{execution.line}{execution.unpriced ? ` ${execution.unpriced} fills had no saved price.` : ""}</p>
              {execution.note ? <p className="mt-note">{execution.note}</p> : null}
            </section>
          ) : null}

          {versus.length ? (
            <section className="mt-card" data-testid="my-trading-versus">
              <div className="mt-card-head"><h3>You vs the paper bots, same days</h3><span className="mt-basis">{versusBasis}</span></div>
              <div className="mt-table-wrap">
                <table className="mt-table">
                  <thead><tr><th className="is-left">Account</th><th>Trades</th><th>Win rate</th><th>Per trade</th><th>P/L</th></tr></thead>
                  <tbody>
                    {versus.map((v) => (
                      <tr key={v.label} className={(v.own ? "is-own " : "") + (v.few ? "is-few" : "")}>
                        <td className="is-left">{v.label}{v.few ? <span className="mt-few"> · {TOO_FEW_LINE}</span> : null}</td>
                        <td>{v.trades}</td><td>{v.winRate}</td><td>{v.expectancy}</td><td className={v.tone}>{v.pnl}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          {MY_TABLES.map((t) => <GroupTable key={t.key} doc={doc} table={t} period={period} />)}

          {open.length ? (
            <section className="mt-card" data-testid="my-trading-open">
              <div className="mt-card-head"><h3>Still open</h3><span className="mt-basis">not counted above</span></div>
              <ul className="mt-open">
                {open.map((p) => <li key={p.key}><span className="mt-contract">{p.contract}</span> <span>{p.qty} @ {p.cost}</span> <span className="mt-when">{p.since}</span></li>)}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
