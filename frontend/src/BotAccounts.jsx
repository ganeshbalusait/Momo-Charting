// The Bot's paper accounts page (2026-11-27): his "See Webull paper trade page mobile I want same how reset button
// pnl ... 1, paper trader 3/5 2 I set money for app trade automatically 3. Trade a ticker also", "Use this journal
// for all" (the Scorecard's tiles and month calendar) and "when I reset new money old journal save with money".
// One screen, four accounts. Every number comes from GET /api/momx-scanner/bot-accounts; the two writes are the
// reset and the money per trade. Paper money only: nothing here places an order.
import { useEffect, useMemo, useState } from "react";
import { Gauge } from "./BotDesk.jsx";
import { deskMonthKey } from "./botDesk.js";
import {
  ACCOUNTS_MONEY_URL, ACCOUNTS_RESET_URL, ACCOUNTS_URL, ACCOUNT_KEY, ACCOUNT_TABS, ACCOUNT_TAB_KEY, MONEY_PRESETS,
  accountCalendar, accountHead, accountKpis, accountMoney, accountOrder, accountWrite, checkMoney, endingRun, orderRow, pickAccount,
  pickTab, positionRow, resetNotes, runMonths, runRows, runsSummary, wholeDollars,
} from "./botAccounts.js";

const REFRESH_MS = 30_000;
const stored = (key) => { try { return window.localStorage.getItem(key); } catch { return null; } };
const keep = (key, value) => { try { window.localStorage.setItem(key, value); } catch { /* private window */ } };

function useAccounts(refresh) {
  const [state, setState] = useState({ doc: null, failed: false, loading: true });
  useEffect(() => {
    let alive = true;
    const load = () => fetch(ACCOUNTS_URL, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((doc) => { if (alive) setState({ doc, failed: false, loading: false }); })
      // A failed refresh keeps the last good numbers and says so.
      .catch(() => { if (alive) setState((s) => ({ doc: s.doc, failed: true, loading: false })); });
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [refresh]);
  return state;
}

// The Scorecard's journal: the five tiles and the month calendar, for one run.
function Journal({ run, tiles, days, today }) {
  const thisMonth = deskMonthKey(today);
  const span = runMonths(run, days, thisMonth);
  const [month, setMonth] = useState("");
  const key = month && month >= span.first && month <= span.last ? month : span.last;
  const calendar = useMemo(() => accountCalendar(days, key, today), [days, key, today]);
  const kpis = accountKpis(tiles);
  return (
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
      {calendar ? (
        <section className="desk-card" aria-label="Calendar">
          <div className="desk-card-head">
            <h4>{calendar.label}</h4>
            <span className="desk-cal-nav">
              <button type="button" className="desk-btn" onClick={() => setMonth(calendar.prev)} aria-label="Previous month" disabled={calendar.key <= span.first}>‹</button>
              <button type="button" className="desk-btn" onClick={() => setMonth(calendar.next)} aria-label="Next month" disabled={calendar.key >= span.last}>›</button>
            </span>
            <span className="desk-cal-total" data-testid="ba-cal-total">Month: <b className={calendar.tone}>{calendar.pnl}</b> · {calendar.days} {calendar.days === 1 ? "day" : "days"} traded</span>
          </div>
          <div className="desk-cal" role="table" aria-label={`Daily results for ${calendar.label}`}>
            <div className="desk-cal-row is-head" role="row">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Week"].map((name) => <span key={name} role="columnheader">{name}</span>)}
            </div>
            {calendar.weeks.map((week) => (
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
      ) : null}
    </>
  );
}

// Reset: what is ending, the new money, and one button that says what it will do. The confirm is the button itself.
function ResetSheet({ acct, onClose, onDone }) {
  const run = acct.run || {};
  const [money, setMoney] = useState(String(Math.round(run.money || 5000)));
  const [perTrade, setPerTrade] = useState(String(Math.round(run.perTrade || 300)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const check = checkMoney(money, perTrade);
  const submit = async (event) => {
    event.preventDefault();
    if (!check.ok || busy) return;
    setBusy(true); setError("");
    const result = await accountWrite(ACCOUNTS_RESET_URL, { account: acct.key, money: check.money, perTrade: check.perTrade });
    setBusy(false);
    if (result.ok) onDone(); else setError(result.error);
  };
  return (
    <form className="ba-sheet" data-testid="ba-reset-sheet" aria-label={`Reset ${acct.label}`} onSubmit={submit}>
      <div className="ba-sheet-head">
        <h3>Reset {acct.label}</h3>
        <button type="button" className="ba-x" aria-label="Close" onClick={onClose}>×</button>
      </div>
      <div className="ba-card">
        <span className="ba-dim ba-strong">This run ends and is saved</span>
        <dl className="ba-facts">
          {endingRun(acct).map(([label, value, tone]) => (<div key={label}><dt>{label}</dt><dd className={tone}>{value}</dd></div>))}
        </dl>
      </div>
      <div className="ba-card">
        <label className="ba-label" htmlFor="ba-money">New starting money</label>
        <div className="ba-input is-big"><span aria-hidden="true">$</span>
          <input id="ba-money" inputMode="decimal" autoComplete="off" value={money} onChange={(e) => setMoney(e.target.value)} />
        </div>
        <div className="ba-presets" role="group" aria-label="Starting money">
          {MONEY_PRESETS.map((v) => (
            <button type="button" key={v} aria-pressed={check.ok && check.money === v} onClick={() => setMoney(String(v))}>{"$" + v / 1000 + "k"}</button>
          ))}
        </div>
        <label className="ba-label" htmlFor="ba-per">Money per automatic trade, up to</label>
        <div className="ba-input"><span aria-hidden="true">$</span>
          <input id="ba-per" inputMode="decimal" autoComplete="off" value={perTrade} onChange={(e) => setPerTrade(e.target.value)} />
        </div>
      </div>
      <div className="ba-card ba-notes">
        <b>What Reset does</b>
        {resetNotes(acct, check.ok ? check.money : null).map((line) => <p key={line}>{line}</p>)}
      </div>
      {check.ok ? null : <p className="ba-error" role="status">{check.error}</p>}
      {error ? <p className="ba-error" role="alert">{error}</p> : null}
      <button type="submit" className="ba-btn is-main" data-testid="ba-reset-go" disabled={!check.ok || busy}>
        {busy ? "Resetting…" : check.ok ? `Reset and start run ${(run.n || 1) + 1} with ${wholeDollars(check.money)}` : "Reset"}
      </button>
      <button type="button" className="ba-btn" onClick={onClose}>Cancel</button>
    </form>
  );
}

// Money per automatic trade, changed without a reset.
function MoneyForm({ acct, onClose, onDone }) {
  const run = acct.run || {};
  const [perTrade, setPerTrade] = useState(String(Math.round(run.perTrade || 300)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const check = checkMoney(String(run.money || 0), perTrade);
  const submit = async (event) => {
    event.preventDefault();
    if (!check.ok || busy) return;
    setBusy(true); setError("");
    const result = await accountWrite(ACCOUNTS_MONEY_URL, { account: acct.key, perTrade: check.perTrade });
    setBusy(false);
    if (result.ok) onDone(); else setError(result.error);
  };
  return (
    <form className="ba-money-form" data-testid="ba-money-form" aria-label="Money per automatic trade" onSubmit={submit}>
      <label className="ba-label" htmlFor="ba-per-only">Money per automatic trade, up to</label>
      <div className="ba-input"><span aria-hidden="true">$</span>
        <input id="ba-per-only" inputMode="decimal" autoComplete="off" value={perTrade} onChange={(e) => setPerTrade(e.target.value)} />
      </div>
      {check.ok ? null : <p className="ba-error" role="status">{check.error}</p>}
      {error ? <p className="ba-error" role="alert">{error}</p> : null}
      <div className="ba-two">
        <button type="button" className="ba-btn" onClick={onClose}>Cancel</button>
        <button type="submit" className="ba-btn is-main" disabled={!check.ok || busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}

export default function BotAccounts({ onTradeTicker }) {
  const [refresh, setRefresh] = useState(0);
  const { doc, failed, loading } = useAccounts(refresh);
  const [picked, setPicked] = useState(() => stored(ACCOUNT_KEY));
  const [tab, setTab] = useState(() => pickTab(stored(ACCOUNT_TAB_KEY)));
  const [resetting, setResetting] = useState(false);
  const [editMoney, setEditMoney] = useState(false);
  const [openRun, setOpenRun] = useState(null);       // a past run's number, when its journal is open
  const order = accountOrder(doc);
  const key = pickAccount(doc, picked);
  const acct = doc && doc.accounts ? doc.accounts[key] : null;
  const today = String((doc && doc.asOf) || "").slice(0, 10);
  const choose = (next) => { setPicked(next); keep(ACCOUNT_KEY, next); setResetting(false); setEditMoney(false); setOpenRun(null); };
  const showTab = (next) => { setTab(next); keep(ACCOUNT_TAB_KEY, next); setOpenRun(null); };
  const done = () => { setResetting(false); setEditMoney(false); setRefresh((n) => n + 1); };

  if (!acct) {
    return (
      <div className="bh ba desk" data-testid="bot-accounts">
        <p className="ba-dim" role="status">{loading ? "Loading the paper accounts…" : "The paper accounts could not be read. The app server did not answer; it is tried again every 30 seconds."}</p>
      </div>
    );
  }
  const head = accountHead(acct);
  const money = accountMoney(acct);
  const runs = runRows(acct);
  const summary = runsSummary(acct);
  const past = openRun === null ? null : (acct.pastRuns || []).find((r) => r.n === openRun) || null;

  return (
    <div className="bh ba desk" data-testid="bot-accounts">
      <nav className="bh-seg ba-accounts" role="group" aria-label="Account">
        {order.map((k) => (
          <button type="button" key={k} data-testid={"ba-account-" + k} aria-pressed={k === key} onClick={() => choose(k)}>{doc.accounts[k].label}</button>
        ))}
      </nav>
      {failed ? <p className="ba-error" role="status">The last refresh failed. These are the last numbers read.</p> : null}
      {acct.error ? <p className="ba-error" role="status">{acct.error}</p> : null}

      {resetting ? <ResetSheet acct={acct} onClose={() => setResetting(false)} onDone={done} /> : (<>
        <section className="ba-card" aria-label="Account value" data-testid="ba-value">
          <div className="ba-row">
            <span className="ba-dim">{head.label} · account value</span>
            <span className="ba-pill">PAPER</span>
          </div>
          <b className="ba-big" data-testid="ba-value-number">{head.value}</b>
          {head.started ? <span className="ba-dim" data-testid="ba-started">{head.started}</span> : null}
          <div className="ba-three">
            {head.pnls.map((p) => (
              <div key={p.label} className="ba-well"><span className="ba-dim">{p.label}</span><b className={p.tone}>{p.value}</b></div>
            ))}
          </div>
          <div className="ba-two">
            <button type="button" className="ba-btn" data-testid="ba-reset" onClick={() => setResetting(true)}>Reset</button>
            <button type="button" className="ba-btn is-main" data-testid="ba-trade-ticker" onClick={onTradeTicker}>Trade a ticker</button>
          </div>
          {head.note ? <span className="ba-dim">{head.note}</span> : null}
        </section>

        <section className="ba-card" aria-label="Money for automatic trades" data-testid="ba-money">
          <div className="ba-row">
            <b>Money for automatic trades</b>
            {money.canChange && !editMoney ?<button type="button" className="ba-btn is-small" data-testid="ba-money-change" onClick={() => setEditMoney(true)}>Change</button> : null}
          </div>
          {editMoney ? <MoneyForm acct={acct} onClose={() => setEditMoney(false)} onDone={done} /> : (<>
            <div className="ba-two">
              <div><span className="ba-dim">Per trade, up to</span><b className="ba-mid">{money.perTrade}</b></div>
              <div><span className="ba-dim">Free to trade now</span><b className="ba-mid">{money.capped ? money.free : "no limit"}</b></div>
            </div>
            {money.capped ? <div className="ba-bar" role="img" aria-label={money.barLabel}><span style={{ width: Math.round(money.share * 100) + "%" }} /></div> : null}
            <span className="ba-dim">{money.note}</span>
          </>)}
        </section>

        <nav className="ba-tabs" role="tablist" aria-label="Account lists">
          {ACCOUNT_TABS.map(([k, label]) => (
            <button type="button" key={k} role="tab" data-testid={"ba-tab-" + k} aria-selected={tab === k} onClick={() => showTab(k)}>{label}</button>
          ))}
        </nav>

        {tab === "positions" ? (
          <section className="ba-list" aria-label="Positions">
            {(acct.positions || []).length ? acct.positions.map(positionRow).map((p) => (
              <div key={p.key} className="ba-item">
                <div><b>{p.symbol}</b> <span className="ba-dim">{p.contract}</span><span className="ba-dim ba-block">{p.detail}</span></div>
                <div className="ba-right"><b className={p.tone}>{p.pnl}</b><span className="ba-dim ba-block">{p.pct}</span></div>
              </div>
            )) : <p className="ba-dim">No open trades in this run.</p>}
          </section>
        ) : null}

        {tab === "orders" ? (
          <section className="ba-list" aria-label="Orders">
            {(acct.orders || []).length ? acct.orders.map(orderRow).map((o) => (
              <div key={o.key} className="ba-item">
                <div><b>{o.symbol}</b> <span className="ba-dim">{o.contract}</span><span className="ba-dim ba-block">{o.detail}</span></div>
                <div className="ba-right"><span className="ba-pill">{o.status}</span></div>
              </div>
            )) : <p className="ba-dim">No orders in this run yet.</p>}
          </section>
        ) : null}

        {tab === "journal" ? (
          <section className="ba-journal" aria-label="Journal" data-testid="ba-journal">
            <span className="ba-dim">{head.label} · this run only{head.started ? " · " + head.started.toLowerCase() : ""}</span>
            <Journal key={key + "|now"} run={acct.run} tiles={acct.journal && acct.journal.tiles} days={acct.journal && acct.journal.days} today={today} />
          </section>
        ) : null}

        {tab === "runs" ? (
          <section className="ba-list" aria-label="Past runs" data-testid="ba-runs">
            <div className="ba-card ba-three">
              <div><span className="ba-dim">Runs</span><b className="ba-mid">{summary.runs}</b></div>
              <div><span className="ba-dim">Money put in</span><b className="ba-mid">{summary.money}</b></div>
              <div><span className="ba-dim">All runs P&amp;L</span><b className={"ba-mid " + summary.tone}>{summary.pnl}</b></div>
            </div>
            <span className="ba-dim" data-testid="ba-resets">{head.label} · {summary.resetsText}</span>
            {past ? (
              <div className="ba-journal" data-testid="ba-past-journal">
                <div className="ba-row">
                  <b>Run {past.n} · started with {wholeDollars(past.money)}</b>
                  <button type="button" className="ba-btn is-small" onClick={() => setOpenRun(null)}>Back to runs</button>
                </div>
                <Journal key={key + "|" + past.n} run={past} tiles={past.tiles} days={past.days} today={today} />
              </div>
            ) : runs.map((r) => (
              <button type="button" key={r.key} className={"ba-run" + (r.live ? " is-live" : "")} data-testid={"ba-run-" + r.n}
                onClick={() => (r.live ? showTab("journal") : setOpenRun(r.n))}>
                <span className="ba-row"><b>{r.title}</b><span className={"ba-pill" + (r.live ? " is-on" : "")}>{r.live ? "NOW" : "SAVED"}</span></span>
                <span className="ba-dim">{r.dates}</span>
                <span className="ba-row"><b className={"ba-mid " + r.tone}>{r.pnl}</b><span className="ba-dim">{r.end}</span></span>
                <span className="ba-link">Open this run&apos;s journal ›</span>
              </button>
            ))}
          </section>
        ) : null}

        <p className="ba-dim">Paper money only. Nothing on this page places a real order.</p>
      </>)}
    </div>
  );
}
