// BOT Scorecard (2026-11-09): the paper accounts judged like a prop-firm
// evaluation. His "i dont trust to turn into real money ... see how prop fund
// trading bot will be". One answer at the top (ready / not ready), the checks
// behind it, the real account curve, and what each rule earned on REAL fills.
// Data: GET /api/momx-scanner/bot-desk (momx/bot_desk.py). Read-only.
import { useEffect, useMemo, useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import { deskEarlier, deskPlanText, deskFillChance, deskBroker, deskFirstMonth, deskTradeLog, readTradeFilter, TRADE_FILTER_KEY, deskCalendar, deskDailyBars, deskKpis, deskMonthKey, DESK_SAVED_KEY, readSavedDesk, JOURNAL_MODES, JOURNAL_MODE_KEY, deskJournal, readJournalMode, DESK_ACCOUNT_KEY, DESK_CONTROL_URL, DESK_URL, deskAccountLabel, deskAiPicks, deskAlerts, deskClock, deskControl, deskCoverage, deskCurve, deskDollars, deskEvaluation, deskLessons, deskMoney, deskObjectives, deskPct, deskStrategies, deskStuckRows, deskTone, deskTruthDays, deskTruthLabel, deskVerdict, deskVersus } from "./botDesk.js";
import { truthAccountLabel, truthPct } from "./botTruth.js";
import { botRuleLabel } from "./momxBot.js";
import Fold from "./BotFold.jsx";

const REFRESH_MS = 60 * 1000;
const TRUTH_REFRESH_MS = 5 * 60 * 1000;

function useJson(url, everyMs, refreshKey = 0, savedKey = "") {
  // With a savedKey the answer from the last visit is shown at once (marked as saved)
  // and replaced when the server answers: the page never opens on a blank "Loading".
  const [state, setState] = useState(() => {
    let saved = null;
    if (savedKey) { try { saved = readSavedDesk(window.localStorage.getItem(savedKey)); } catch { saved = null; } }
    return { doc: saved, failed: false, loading: true, saved: !!saved };
  });
  useEffect(() => {
    if (!url) return undefined;          // not known yet (waits for the first Scorecard read)
    let alive = true;
    const load = () => fetch(url, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((doc) => {
        if (!alive) return;
        setState({ doc, failed: false, loading: false, saved: false });
        if (savedKey) { try { window.localStorage.setItem(savedKey, JSON.stringify(doc)); } catch { /* private window or full */ } }
      })
      // A failed refresh keeps the last good numbers but says so (failed stays true).
      .catch(() => { if (alive) setState((s) => ({ doc: s.doc, failed: true, loading: false, saved: s.saved })); });
    load();
    const id = window.setInterval(load, everyMs);
    return () => { alive = false; window.clearInterval(id); };
  }, [url, everyMs, refreshKey]);
  return state;
}

function Stat({ label, value, sub, tone = "" }) {
  return (
    <div className="desk-stat">
      <span className="desk-stat-label">{label}</span>
      <span className={"desk-stat-value " + tone}>{value}</span>
      {sub ? <span className="desk-stat-sub">{sub}</span> : null}
    </div>
  );
}

function Curve({ days, limits }) {
  const c = useMemo(() => deskCurve(days, limits), [days, limits]);
  if (!c.points.length) return <p className="desk-note">The curve starts with the first closed paper trade.</p>;
  return (
    <svg className="desk-curve-svg" viewBox={`0 0 ${c.width} ${c.height}`} role="img" preserveAspectRatio="none"
      aria-label={`Real paper profit and loss by day, now ${deskMoney(c.last.cum)}`}>
      <line className="desk-line-target" x1="0" x2={c.width} y1={c.targetY} y2={c.targetY} />
      <line className="desk-line-zero" x1="0" x2={c.width} y1={c.zeroY} y2={c.zeroY} />
      <line className="desk-line-floor" x1="0" x2={c.width} y1={c.floorY} y2={c.floorY} />
      {c.points.length > 1 ? <polyline className={"desk-line-pnl " + deskTone(c.last.cum)} points={c.line} /> : null}
      {c.points.map((p) => (
        <circle key={p.day} className={"desk-dot " + deskTone(p.pnl)} cx={p.x} cy={p.y} r="3.5">
          <title>{`${p.day}: day ${deskMoney(p.pnl)}, total ${deskMoney(p.cum)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

/** A half-circle gauge: the cyan part is the winning share, the magenta the rest. */
export function Gauge({ share }) {
  const length = 125.66;                                   // half the circumference of a 40-unit radius
  return (
    <svg className="desk-gauge" viewBox="0 0 100 56" aria-hidden="true">
      <path className="desk-gauge-rest" d="M10 50 A40 40 0 0 1 90 50" />
      {share > 0 ? <path className="desk-gauge-won" d="M10 50 A40 40 0 0 1 90 50" strokeDasharray={`${Math.round(share * length * 10) / 10} ${length}`} /> : null}
    </svg>
  );
}

// Exported for the Ticker trader journal (TickerTrader.jsx), which draws the same tiles and daily bars.
export function DailyBars({ days }) {
  const g = useMemo(() => deskDailyBars(days), [days]);
  if (!g.bars.length) return <p className="desk-note">The bars start with the first day a real paper trade closes.</p>;
  return (
    <svg className="desk-curve-svg desk-bars-svg" viewBox={`0 0 ${g.width} ${g.height}`} role="img" preserveAspectRatio="none"
      aria-label={`Real profit and loss for each of ${g.bars.length} days`}>
      <line className="desk-line-zero" x1="0" x2={g.width} y1={g.zeroY} y2={g.zeroY} />
      {g.bars.map((b) => (
        <rect key={b.day} className={"desk-bar-day " + b.tone} x={b.x} y={b.y} width={b.w} height={b.h} rx="2">
          <title>{`${b.day}: ${deskMoney(b.pnl)}`}</title>
        </rect>
      ))}
    </svg>
  );
}

export default function BotDesk() {
  const [refresh, setRefresh] = useState(0);
  const { doc, failed, loading, saved } = useJson(DESK_URL, REFRESH_MS, refresh, DESK_SAVED_KEY);
  // The BOT's claim is read for the same days the evaluation covers.
  const truthDays = deskTruthDays(doc);
  const truth = useJson(doc && truthDays ? `/api/momx-scanner/bot-truth?days=${truthDays}` : "", TRUTH_REFRESH_MS).doc;
  const names = Object.keys(doc?.accounts || {});
  const [picked, setPicked] = useState(() => {
    try { return window.localStorage.getItem(DESK_ACCOUNT_KEY) || ""; } catch { return ""; }
  });
  const account = names.includes(picked) ? picked : names[0] || "";
  const pick = (name) => {
    setPicked(name);
    setMonth("");                         // the other account opens on the current month
    try { window.localStorage.setItem(DESK_ACCOUNT_KEY, name); } catch { /* private window */ }
  };
  const acct = doc?.accounts?.[account] || null;
  const limits = doc?.limits || {};
  const verdict = deskVerdict(acct);
  const objectives = deskObjectives(acct, limits);
  const alerts = deskAlerts(doc, acct);
  const s = acct?.stats || {};
  const d = acct?.daySummary || {};
  const claim = truth?.accounts?.[account]?.matched;
  const control = deskControl(acct, doc);
  const evaluation = deskEvaluation(doc, acct);
  const lessons = deskLessons(doc, botRuleLabel);
  const strategies = deskStrategies(acct);
  const versus = deskVersus(doc, truthAccountLabel);
  const [journalMode, setJournalModeState] = useState(() => {
    try { return readJournalMode(window.localStorage.getItem(JOURNAL_MODE_KEY)); } catch { return "day"; }
  });
  const setJournalMode = (mode) => {
    setJournalModeState(mode);
    try { window.localStorage.setItem(JOURNAL_MODE_KEY, mode); } catch { /* private window */ }
  };
  const journal = deskJournal(acct, journalMode);
  // The journal dashboard: tiles, the month on the calendar (his choice, else the current month), latest trades.
  const kpis = deskKpis(acct);
  const thisMonth = deskMonthKey(doc?.asOf);
  const [month, setMonth] = useState("");
  const calendar = deskCalendar(acct, month || thisMonth, String(doc?.asOf || "").slice(0, 10)) || { weeks: [], label: "", key: "", pnl: "$0", tone: "", days: 0 };
  const recent = (deskJournal(acct, "day")?.trades || []).slice(0, 6);
  // "All trades": every signal and what became of it; the chosen filter is remembered on this device.
  const [tradeFilter, setTradeFilterState] = useState(() => {
    try { return readTradeFilter(window.localStorage.getItem(TRADE_FILTER_KEY)); } catch { return "all"; }
  });
  const setTradeFilter = (key) => {
    setTradeFilterState(key);
    try { window.localStorage.setItem(TRADE_FILTER_KEY, key); } catch { /* private window */ }
  };
  const tradeLog = deskTradeLog(acct, tradeFilter, String(doc?.asOf || "").slice(0, 10));
  const fillChance = deskFillChance(acct);
  const earlier = deskEarlier(acct);
  // The chart plan's text only when the SERVER says the paper bots run it (doc.plan === "chart");
  // this page is live in the browser before the servers restart.
  const planText = deskPlanText(doc);
  const broker = deskBroker(acct, doc?.limits?.accountSize, String(doc?.asOf || "").slice(0, 10));
  const aiPicks = deskAiPicks(acct);
  const stuck = deskStuckRows(acct);
  // Pause / Resume / Remove a stuck order (2026-11-09). The answer is shown as
  // it came back: a refusal says why, and nothing is assumed to have worked.
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const act = (body, done) => {
    const forAccount = body.account;      // the answer belongs to this account even if he switches meanwhile
    setBusy(true);
    setNotice(null);
    fetch(DESK_CONTROL_URL, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => r.json().catch(() => ({})).then((d) => ({ ok: r.ok && d?.ok === true, d })))
      .then(({ ok, d }) => {
        setNotice({ account: forAccount, bad: !ok, text: ok ? done : (d?.error || "That did not go through. Nothing was changed.") });
      })
      // No answer is not proof that nothing happened: say so, and re-read.
      .catch(() => setNotice({ account: forAccount, bad: true, text: "No answer from the scanner worker. Check the strip above before trying again." }))
      // Re-read after every answer: a refusal usually means the screen was out of date.
      .finally(() => { setBusy(false); setRefresh((n) => n + 1); });
  };
  const removeStuck = (row) => {
    if (!window.confirm(`Remove the stuck ${row.symbol} order? The broker is checked first. It then counts as never filled and its slot is free.`)) return;
    act({ action: "remove", account, key: row.key }, `Removed the stuck ${row.symbol} order. Its slot is free.`);
  };

  if (!doc) {
    return (
      <div className="desk" data-testid="bot-desk">
        {failed ? <p className="desk-note is-bad" role="alert">The scorecard could not load. <button type="button" className="desk-btn" onClick={() => setRefresh((n) => n + 1)}>Try again</button></p>
          : <p className="desk-note" role="status">Loading the scorecard…</p>}
      </div>
    );
  }

  return (
    <div className="desk" data-testid="bot-desk">
      <section className={"desk-verdict is-" + verdict.tone} data-testid="desk-verdict" aria-label="Real money verdict">
        <div className="desk-verdict-main">
          <span className="desk-stamp">{verdict.tone === "ready" ? "PASSED" : verdict.tone === "empty" ? "NOT STARTED" : "NOT READY"}</span>
          <h3>{verdict.title}</h3>
          <p>{verdict.line}</p>
        </div>
        <div className="desk-verdict-side">
          {names.length > 1 ? (
            <div className="desk-switch" role="group" aria-label="Paper account">
              {names.map((name) => (
                <button key={name} type="button" aria-pressed={name === account} onClick={() => pick(name)}>{deskAccountLabel(name, doc.accounts[name], truthAccountLabel(name))}</button>
              ))}
            </div>
          ) : null}
          <span className="desk-asof" role="status">
            {failed ? "Refresh failed · showing " : saved ? "Saved numbers from " : "Real paper fills · as of "}{deskClock(doc.asOf)}{saved && !failed ? " · updating…" : ""}
          </span>
          <button type="button" className="desk-btn" onClick={() => setRefresh((n) => n + 1)} disabled={loading} title="Refresh the scorecard">
            <RefreshCw size={13} aria-hidden="true" /> Refresh
          </button>
        </div>
      </section>

      {alerts.length ? (
        <ul className="desk-alerts" aria-label="Needs attention">
          {alerts.map((a) => <li key={a.text} className={"is-" + a.tone}>{a.text}</li>)}
        </ul>
      ) : null}

      {evaluation ? <p className="desk-coverage desk-evaluation" data-testid="desk-evaluation">{evaluation}</p> : null}

      {control ? (
        <section className="desk-controls" aria-label="Paper bot controls">
          <span className={"desk-pill is-" + control.tone} data-testid="desk-buying">{control.state}</span>
          <button type="button" className="desk-btn" disabled={busy}
            onClick={() => act({ action: control.action, account }, control.done)}>{control.button}</button>
          <span className={"desk-pill" + (control.stopHit ? " is-bad" : "")} data-testid="desk-stop">{control.stop}</span>
          {broker ? (
            <span className={"desk-pill is-" + broker.tone} data-testid="desk-broker"
              title={`Alpaca paper account · cash ${broker.cash} · read ${broker.asOf}`}>
              Alpaca buying power {broker.buyingPower} · account {broker.equity}
              {broker.change ? <> · <span className={broker.changeTone}>{broker.change} today</span></> : null}
            </span>
          ) : null}
          {broker && (broker.warning || broker.old || broker.error) ? (
            <span className="desk-note desk-broker-note" data-testid="desk-broker-note">
              {broker.warning}{broker.old ? ` Read ${broker.asOf}.` : ""}{broker.error ? ` ${broker.error}` : ""}
            </span>
          ) : null}
          <span className="desk-note">{acct?.control?.dailyStop?.on === false ? "Pause holds new buys only. There is no daily loss stop: the bot keeps taking Level signals all day." : "Pause and the daily loss stop hold new buys only."} Positions already held still sell on their plan.</span>
        </section>
      ) : null}

      {stuck.length ? (
        <ul className="desk-stuck" aria-label="Stuck orders">
          {stuck.map((row) => (
            <li key={row.key}>
              <span><b>{row.title}</b> · {row.detail}</span>
              <button type="button" className="desk-btn is-danger" disabled={busy} onClick={() => removeStuck(row)}
                aria-label={`Remove the stuck ${row.symbol} order`}>Remove</button>
            </li>
          ))}
        </ul>
      ) : null}

      {notice && notice.account === account ? <p className={"desk-note desk-notice" + (notice.bad ? " is-bad" : "")} role="status" data-testid="desk-notice">{notice.text}</p> : null}

      {!acct ? <p className="desk-note" role="status">No paper orders yet. The evaluation starts with the first real paper fill.</p> : (<>
        {/* 2026-11-26 (his "Refer deep-research see other bot pages and do it"): the reading order he asked for stays
            (All trades, numbers, charts, calendar, the two comparisons, the checks). The long lists are sections that
            open with one tap, so the numbers, the calendar and the checks are reached without a long scroll. */}
        <Fold id="trades" title="All trades" note="every signal and order, with its status">
        {/* All trades first (2026-11-10: he asked three times where it was - it sat below the calendar). */}
        {tradeLog ? (
          <section className="desk-card" aria-label="All trades">
            <div className="desk-card-head">
              <h4>All trades</h4>
              <div className="desk-switch" role="group" aria-label="Trade filter">
                {tradeLog.filters.map((f) => (
                  <button key={f.key} type="button" aria-pressed={f.on} onClick={() => setTradeFilter(f.key)}>{f.label}</button>
                ))}
              </div>
            </div>
            {fillChance ? (
              <p className="desk-note desk-fill-line" data-testid="desk-fill-chance">
                Fill likelihood, like Robinhood (from the spread when each buy is sent; it never changes the price. {planText.active ? "On the chart plan the bot buys at the ask, so nearly every order fills" : "The older plan, still running, tries the middle price first, then the ask"}) · {fillChance.parts.map((p) => p.text).join(" · ")}
              </p>
            ) : null}
            {tradeLog.rows.length ? (
              <div className="desk-scroll desk-trades-scroll">
                <table className="desk-table">
                  <thead><tr><th className="is-left">Signal</th><th className="is-left">Ticker</th><th className="is-left">Side</th><th className="is-left">Strategy</th><th className="is-left">Status</th><th>Contracts</th><th>Bought at</th><th title="The chart plan's entry: the option's ask when the order went out">Entry (ask)</th><th title="The stock price where the option is down 50% from the entry. An arrow shows where the stop stands after a target was reached.">Stop (stock)</th><th title="Half the expected move">T1</th><th title="The full expected move">T2</th><th title="The biggest open-interest strike past the move">T3</th><th>Real P/L</th><th>Return</th><th className="is-left">What happened</th></tr></thead>
                  <tbody>
                    {tradeLog.rows.map((t) => (
                      <tr key={t.key} data-status={t.status}>
                        <td className="is-left">{t.when}</td><td className="is-left"><b>{t.symbol}</b>{t.aiPicked ? <span className="desk-tag is-on">AI pick</span> : null}</td>
                        <td className="is-left">{t.side}</td><td className="is-left">{t.strategy}</td>
                        <td className="is-left"><span className={"desk-status is-" + t.status}>{t.statusLabel}</span>
                          {t.fill ? <span className={"desk-fill is-" + t.fillKey} title="Fill likelihood at the mid when the order was sent, from the quote's spread">{t.fill}</span> : null}</td>
                        <td>{t.qty}</td><td>{t.buy}</td><td>{t.plan.entry}</td><td>{t.plan.stop}</td><td>{t.plan.t1}</td><td>{t.plan.t2}</td><td>{t.plan.t3}</td><td className={t.tone}>{t.pnl}</td><td className={t.tone}>{t.roi}</td><td className="is-left">{t.note}{t.plan.result ? " · " + t.plan.result : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="desk-note" data-testid="desk-trades-empty">{tradeLog.empty}</p>}
            <p className="desk-note">
              {tradeLog.capped ? `Showing the ${tradeLog.capped}. ` : ""}Every paper order since the evaluation started, plus today's Bot signals this bot sent no order for ("Not ordered": the same list as Research, with the 0.20-delta option's move at the mid since the signal - nothing was bought), newest first. "now" (or a time) marks a trade still open: its result so far at the last mid price, an estimate that is not in the real totals.
              {tradeLog.full ? "" : " Open, not-filled and skipped orders appear after the next backend restart."} Entry (ask), Stop, T1, T2 and T3 are the chart&apos;s plan for that trade: the option&apos;s ask when the order went out, the stock price that stops it, and the three stock targets (✓ and a time = reached). A dash means the trade is from before the chart plan. The earlier period is listed below; older trades are under Research.
            </p>
          </section>
        ) : null}

        {/* The evaluations that ended (2026-11-12: the chart plan starts a clean record; the old plan's
            trades stay visible here and are never added to the numbers above). */}
        {earlier.map((p) => (
          <section className="desk-card" aria-label={p.title} key={p.key} data-testid="desk-earlier">
            <div className="desk-card-head"><h4>{p.title}</h4></div>
            <p className="desk-note">Not part of the evaluation above. It traded: {p.strategy}. {p.why}</p>
            <p className="desk-note" data-testid="desk-earlier-summary"><b className={p.tone}>{p.summary}</b></p>
            {p.trades.length ? (
              <details className="desk-journal-trades">
                <summary>Its closed trades ({p.trades.length})</summary>
                <div className="desk-scroll">
                  <table className="desk-table">
                    <thead><tr><th className="is-left">Closed</th><th className="is-left">Ticker</th><th className="is-left">Side</th><th>Contracts</th><th>Bought at</th><th>Real P/L</th><th>Return</th><th className="is-left">How it ended</th></tr></thead>
                    <tbody>
                      {p.trades.map((t) => (
                        <tr key={t.key}>
                          <td className="is-left">{t.when}</td><td className="is-left"><b>{t.symbol}</b></td><td className="is-left">{t.side}</td>
                          <td>{t.qty}</td><td>{t.buy}</td><td className={t.tone}>{t.pnl}</td><td className={t.tone}>{t.roi}</td><td className="is-left">{t.exit}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ) : null}
          </section>
        ))}

        </Fold>

        {/* Journal dashboard (2026-11-10, his "Scoreboard look like not a tradezella"): what a trading journal opens
            on - the key numbers, the two P&L charts, the month calendar and the latest trades. */}
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

        <Fold id="charts" title="Account curve and daily result" note="real profit and loss, day by day">
        <div className="desk-grid">
          <section className="desk-card" aria-label="Account curve">
            <div className="desk-card-head">
              <h4>Account curve · real profit and loss</h4>
              <span className={"desk-big " + deskTone(s.netPnl)}>{deskMoney(s.netPnl)}</span>
            </div>
            <Curve days={acct.days} limits={limits} />
            <p className="desk-legend">
              <span className="is-target">Target {deskMoney(limits.profitTarget)}</span>
              <span className="is-zero">Start $0</span>
              <span className="is-floor">Drawdown limit {deskMoney(-limits.maxDrawdown)}</span>
            </p>
          </section>

          <section className="desk-card" aria-label="Net daily P&L">
            <div className="desk-card-head"><h4>Net daily P&amp;L</h4><span className="desk-note">each bar is one day's real result</span></div>
            <DailyBars days={acct.days} />
          </section>
        </div>

        </Fold>

        <div className="desk-grid is-calendar">
          <section className="desk-card" aria-label="Calendar">
            <div className="desk-card-head">
              <h4>{calendar.label}</h4>
              <span className="desk-cal-nav">
                <button type="button" className="desk-btn" onClick={() => setMonth(calendar.prev)} aria-label="Previous month" disabled={calendar.key <= deskFirstMonth(acct, thisMonth)}>‹</button>
                <button type="button" className="desk-btn" onClick={() => setMonth("")} disabled={calendar.key === thisMonth}>This month</button>
                <button type="button" className="desk-btn" onClick={() => setMonth(calendar.next)} aria-label="Next month" disabled={calendar.key >= thisMonth}>›</button>
              </span>
              <span className="desk-cal-total" data-testid="desk-cal-total">Month: <b className={calendar.tone}>{calendar.pnl}</b> · {calendar.days} {calendar.days === 1 ? "day" : "days"} traded</span>
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

          <section className="desk-card" aria-label="Recent trades">
            <div className="desk-card-head"><h4>Recent trades</h4><span className="desk-note">real fills, newest first</span></div>
            {recent.length ? (
              <ul className="desk-recent">
                {recent.map((t) => (
                  <li key={t.key}>
                    <span className="desk-recent-main"><b>{t.symbol}</b> {t.side} · {t.strategy}{t.aiPicked ? <span className="desk-tag is-on">AI pick</span> : null}</span>
                    <span className={"desk-recent-pnl " + t.tone}>{t.pnl}</span>
                    <span className="desk-recent-sub">{t.when} · {t.exit}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="desk-note">No closed trades yet. They appear here as real paper trades close.</p>}
          </section>
        </div>

        {versus ? (
          <section className="desk-card" aria-label="Rules bot against AI bot">
            <div className="desk-card-head">
              <h4>Rules bot against AI bot · which way works</h4>
              <span className="desk-note">Same signals, same contract, same size and exits. Only the choice of which signal gets a free slot differs.</span>
            </div>
            <p className={"desk-answer" + (versus.tone ? " is-" + versus.tone : "")} data-testid="desk-versus-answer">{versus.answer}</p>
            <div className="desk-scroll">
              <table className="desk-table">
                <thead><tr><th className="is-left">Bot</th><th>Closed</th><th>Win</th><th>Real P/L</th><th>Return</th><th>Per trade</th><th>Balance</th></tr></thead>
                <tbody>
                  {versus.rows.map((r) => (
                    <tr key={r.key} className={r.leader ? "is-best" : ""} data-bot={r.key}>
                      <td className="is-left"><b>{r.name}</b><span className="desk-tag">{r.account}</span>{r.leader ? <span className="desk-tag is-on">ahead</span> : null}<span className="desk-strategy-needs">{r.how}</span></td>
                      <td>{r.closed}</td><td>{r.win}</td><td className={r.tone}>{r.pnl}</td><td className={r.tone}>{r.roi}</td><td className={r.tone}>{r.perTrade}</td><td>{r.balance}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {aiPicks ? <p className="desk-note" data-testid="desk-ai-picks">{aiPicks}</p> : null}
            <p className="desk-note">A bot is called ahead only when both have {doc.smallSample} closed real trades, the AI really chose at least half of the AI bot's trades, and the gap is bigger than one trade can move it.</p>
          </section>
        ) : null}

        {strategies ? (
          <section className="desk-card" aria-label="The three strategies">
            <div className="desk-card-head">
              <h4>The three strategies · which one is good</h4>
              <span className="desk-note">Real paper fills on the 0.20-delta contract{!planText.active ? " · the chart-plan evaluation has not started" : doc.startDay ? ` since ${doc.startDay.slice(5)}` : ""}.</span>
            </div>
            <p className={"desk-answer" + (strategies.tone ? " is-" + strategies.tone : "")} data-testid="desk-strategy-answer">{strategies.answer}</p>
            <div className="desk-scroll">
              <table className="desk-table">
                <thead><tr><th className="is-left">Strategy</th><th>Closed</th><th>Win</th><th>Real P/L</th><th>Return</th><th>Per trade</th><th>Calls</th><th>Puts</th><th>Orders filled</th></tr></thead>
                <tbody>
                  {strategies.rows.map((r) => (
                    <tr key={r.key} className={r.best ? "is-best" : ""} data-strategy={r.key}>
                      <td className="is-left"><b>{r.name}</b>{r.tag ? <span className={"desk-tag" + (r.best ? " is-on" : "")}>{r.tag}</span> : null}<span className="desk-strategy-needs">{r.needs}</span></td>
                      <td>{r.closed}</td><td>{r.win}</td>
                      <td className={r.pnlTone}>{r.pnl}</td><td className={r.pnlTone}>{r.roi}</td><td className={r.pnlTone}>{r.perTrade}</td>
                      <td className={r.callsTone}>{r.calls}</td><td className={r.putsTone}>{r.puts}</td><td>{r.filled}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="desk-note">The levels stack: a Level 3 signal is also a Level 2 and a Level 1 signal, so one trade can count in more than one row. Best = the highest return on the money used, among strategies with {doc.smallSample} or more closed trades that made money.</p>
          </section>
        ) : null}

        <Fold id="journal" title="Journal" note="profit and loss by day, week and month" when={!!journal}>
        {journal ? (
          <section className="desk-card" aria-label="Journal">
            <div className="desk-card-head">
              <h4>Journal · profit and loss by day, week and month</h4>
              <div className="desk-switch" role="group" aria-label="Journal period">
                {JOURNAL_MODES.map(([key, label]) => (
                  <button key={key} type="button" aria-pressed={journal.mode === key} onClick={() => setJournalMode(key)}>{label}</button>
                ))}
              </div>
            </div>
            <p className="desk-answer" data-testid="desk-journal-answer">{journal.answer}</p>
            {journal.rows.length ? (
              <div className="desk-scroll">
                <table className="desk-table">
                  <thead><tr><th className="is-left">{journal.mode === "day" ? "Day" : journal.mode === "week" ? "Week" : "Month"}</th><th>Trades</th><th>Win</th><th>Real P/L</th><th>Level 1</th><th>Level 2</th><th>Level 3</th><th>Calls</th><th>Puts</th><th>Best strategy</th></tr></thead>
                  <tbody>
                    {journal.rows.map((r) => (
                      <tr key={r.key} data-period={r.key}>
                        <td className="is-left">{r.label}</td><td>{r.trades}</td><td>{r.win}</td>
                        <td className={r.tone}><b>{r.pnl}</b></td>
                        <td className={r.tone1}>{r.level1}</td><td className={r.tone2}>{r.level2}</td><td className={r.tone3}>{r.level3}</td>
                        <td className={r.callsTone}>{r.calls}</td><td className={r.putsTone}>{r.puts}</td><td>{r.best}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {journal.trades.length ? (
              <details className="desk-journal-trades">
                <summary>Every closed trade{journal.tradeCount > journal.shown ? ` (newest ${journal.shown} of ${journal.tradeCount})` : ` (${journal.shown})`}</summary>
                <div className="desk-scroll">
                  <table className="desk-table">
                    <thead><tr><th className="is-left">Closed</th><th className="is-left">Ticker</th><th className="is-left">Side</th><th className="is-left">Strategy</th><th>Contracts</th><th>Bought at</th><th>Stop (stock)</th><th>T1</th><th>T2</th><th>T3</th><th>Real P/L</th><th>Return</th><th className="is-left">How it ended</th></tr></thead>
                    <tbody>
                      {journal.trades.map((t) => (
                        <tr key={t.key}>
                          <td className="is-left">{t.when}</td><td className="is-left"><b>{t.symbol}</b>{t.aiPicked ? <span className="desk-tag is-on">AI pick</span> : null}</td>
                          <td className="is-left">{t.side}</td><td className="is-left">{t.strategy}</td><td>{t.qty}</td><td>{t.buy}</td>
                          <td>{t.plan.stop}</td><td>{t.plan.t1}</td><td>{t.plan.t2}</td><td>{t.plan.t3}</td>
                          <td className={t.tone}>{t.pnl}</td><td className={t.tone}>{t.roi}</td><td className="is-left">{t.exit}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </details>
            ) : null}
            <p className="desk-note">Real paper fills only, on the day each trade closed. A trade counts under every Level it qualified for, so the three Level columns can add up to more than the total.</p>
          </section>
        ) : null}

        </Fold>

        <p className="desk-coverage">{truthAccountLabel(account)} · {deskCoverage(acct)} · judged as a {deskDollars(limits.accountSize)} evaluation account</p>

        <section className="desk-checks" aria-label="Evaluation checks">
          {objectives.map((o) => (
            <div key={o.key} className={"desk-check " + (o.pass ? "is-pass" : "is-fail")} data-check={o.key} title={o.help}>
              <span className="desk-check-head">
                <span className="desk-check-icon" aria-hidden="true">{o.pass ? <Check size={13} /> : <X size={13} />}</span>
                <span className="desk-check-label">{o.label}</span>
                <span className="desk-check-state">{o.pass ? "Passed" : "Not passed"}</span>
              </span>
              <span className="desk-check-actual">{o.actual}</span>
              <span className="desk-check-target">{o.target}</span>
              <span className="desk-bar" aria-hidden="true"><i style={{ width: Math.round(o.progress * 100) + "%" }} /></span>
            </div>
          ))}
        </section>

        <Fold id="stats" title="Statistics" note="trader statistics, and the claim against the real result">
        <section className="desk-card" aria-label="Trader statistics">
          <div className="desk-card-head"><h4>Trader statistics · closed real fills</h4></div>
          <div className="desk-stats">
            <Stat label="Win rate" value={deskPct(s.winRatePct)} sub={`${s.wins ?? 0} wins · ${s.losses ?? 0} losses`} />
            <Stat label="Average win" value={deskMoney(s.avgWin)} tone={deskTone(s.avgWin)} />
            <Stat label="Average loss" value={deskMoney(s.avgLoss)} tone={deskTone(s.avgLoss)} />
            <Stat label="Per trade" value={deskMoney(s.expectancy)} sub="average result of one trade" tone={deskTone(s.expectancy)} />
            <Stat label="Return on money used" value={s.roiPct == null ? "–" : truthPct(s.roiPct)} tone={deskTone(s.roiPct)} />
            <Stat label="Green days" value={`${d.greenDays ?? 0} of ${d.tradingDays ?? 0}`} sub={`${d.redDays ?? 0} red`} />
            <Stat label="Best day" value={deskMoney(d.bestDay?.pnl)} sub={d.bestDay?.day?.slice(5) || ""} tone={deskTone(d.bestDay?.pnl)} />
            <Stat label="Worst day" value={deskMoney(d.worstDay?.pnl)} sub={d.worstDay?.day?.slice(5) || ""} tone={deskTone(d.worstDay?.pnl)} />
          </div>
        </section>

        {claim?.n ? (
          <section className="desk-card desk-claim" aria-label="BOT claim against real result">
            <div className="desk-card-head"><h4>What the BOT page claims against what really happened · {deskTruthLabel(doc)}</h4></div>
            <div className="desk-claim-row">
              <Stat label="BOT page claims" value={truthPct(claim.botRoi)} sub={`${deskMoney(claim.botPnl)} · pretend fills at the mid price`} />
              <Stat label="Real paper result" value={truthPct(claim.paperRoi)} sub={`${deskMoney(claim.paperPnl)} · same ${claim.n} trades`} tone={deskTone(claim.paperRoi)} />
              <p className="desk-note">The Research view scores every signal as if it filled at the mid price. Real orders fill later and at worse prices, and many winners never fill. Trust the real result.</p>
            </div>
          </section>
        ) : null}

        </Fold>

        <Fold id="lessons" title="What the review learned" note="from the losing trades; it suggests, it never changes a rule">
        <section className="desk-card" aria-label="AI lessons from losing trades">
          <div className="desk-card-head">
            <h4>What the AI learned from the losing trades</h4>
            <span className="desk-note">AI never blocks a trade. It studies the losers each evening and suggests.</span>
          </div>
          <p className="desk-note" data-testid="desk-lessons-note">{lessons.note}</p>
          {lessons.rows.length ? (
            <ul className="desk-lessons">
              {lessons.rows.map((x) => (
                <li key={x.key}>
                  <span className="desk-lesson-head"><b>{x.title}</b><span className={"desk-tag" + (x.tone ? " is-" + x.tone : "")}>{x.tag}</span><span className="desk-note">{x.counts}</span></span>
                  <span className="desk-lesson-text">{x.lesson}</span>
                  {x.basis ? <span className="desk-lesson-more">{x.basis}</span> : null}
                  {x.change ? <span className="desk-lesson-more"><b>To try:</b> {x.change}</span> : null}
                  {x.test ? <span className="desk-lesson-more"><b>How to test it:</b> {x.test}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        </Fold>

        <Fold id="rules" title="Each rule, and day by day" note="what each rule earned on real fills">
        <section className="desk-card" aria-label="Rules on real fills">
          <div className="desk-card-head">
            <h4>Rules · what each one earned on real fills</h4>
            <span className="desk-note">Fewer than {doc.smallSample} closed trades is too few to judge.</span>
          </div>
          <div className="desk-scroll">
            <table className="desk-table">
              <thead><tr><th className="is-left">Rule</th><th className="is-left">Side</th><th>Sent</th><th>Filled</th><th>Closed</th><th>Win</th><th>Real P/L</th><th>Return</th></tr></thead>
              <tbody>
                {acct.byRule.map((r) => (
                  <tr key={r.side + r.rule} className={r.smallSample ? "is-small" : ""}>
                    <td className="is-left"><b>{botRuleLabel(r.rule, r.side)}</b>{r.smallSample ? <span className="desk-tag">too few</span> : null}</td>
                    <td className="is-left">{r.side === "bear" ? "Puts" : "Calls"}</td>
                    <td>{r.sent}</td>
                    <td>{r.filled}{r.fillRatePct == null ? "" : ` (${Math.round(r.fillRatePct)}%)`}</td>
                    <td>{r.closed}</td>
                    <td>{deskPct(r.winRatePct)}</td>
                    <td className={deskTone(r.pnl)}>{r.closed ? deskMoney(r.pnl) : "–"}</td>
                    <td className={deskTone(r.roiPct)}>{r.roiPct == null ? "–" : truthPct(r.roiPct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="desk-card" aria-label="Day by day">
          <div className="desk-card-head"><h4>Day by day · real fills</h4></div>
          <div className="desk-scroll">
            <table className="desk-table">
              <thead><tr><th className="is-left">Day</th><th>Sent</th><th>Filled</th><th>Closed</th><th>Day P/L</th><th>Running total</th></tr></thead>
              <tbody>
                {[...acct.days].reverse().map((day) => (
                  <tr key={day.day} className={day.pnl < -limits.dailyLossLimit ? "is-breach" : ""}>
                    <td className="is-left">{day.day}{day.pnl < -limits.dailyLossLimit ? <span className="desk-tag is-bad">over daily limit</span> : null}</td>
                    <td>{day.sent}</td><td>{day.filled}</td><td>{day.closed}</td>
                    <td className={deskTone(day.pnl)}>{day.closed ? deskMoney(day.pnl) : "–"}</td>
                    <td className={deskTone(day.cum)}>{deskMoney(day.cum)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        </Fold>

      </>)}

      <details className="desk-how">
        <summary>How this evaluation works</summary>
        <p data-testid="desk-plan-rules" data-plan={planText.active ? "chart" : "old"}><b>{planText.title}</b> {planText.rules.join(" ")}{planText.note ? <> <b>{planText.note}</b></> : null}</p>
        <p>A prop firm hands a trader an account and reads the real record: a profit target, a daily loss limit and a maximum drawdown. This page does the same to the bot. Every number here is a real Alpaca paper order. An order that never filled earns nothing, and profit lands on the day the trade closed.</p>
        <p>The checks: up {deskDollars(limits.profitTarget)} on a {deskDollars(limits.accountSize)} account, never down more than {deskDollars(limits.dailyLossLimit)} in one day, never more than {deskDollars(limits.maxDrawdown)} below the highest point, at least {limits.minDays} trading days and {limits.minTrades} closed trades, profit factor {limits.minProfitFactor} or more, still in profit without the best {limits.luckTrades} trades, and at least {limits.minFillPct}% of orders filled.</p>
        <p>Safety: Pause stops new paper buys for that account until you resume. {acct?.control?.dailyStop?.on === false ? "There is no daily loss stop any more (switched off 11-10): a losing day does not hold new buys. The Daily loss limit check above still records such a day." : <>The daily loss stop does the same by itself once the account is down {deskDollars(limits.dailyLossLimit)} on the day, until the next day. Neither one sells what is already held; those positions keep their exit plan. A stuck order is a buy the broker never confirmed; Remove checks the broker first and then frees its slot.</>}</p>
        <p>AI: it never blocks a trade. On Paper 5 (the AI bot) it chooses which waiting signal gets a free slot; with no fresh answer the newest signal goes first, the same as Paper 3 (the Rules bot). Every evening it studies the losing real trades of each strategy and writes one lesson, one change to try and how to test it. A lesson is a suggestion; nothing changes until you decide.</p>
        <p>Passing shows the record is good enough to consider real money. It does not switch anything on. This app has no real-money order path for this bot.</p>
      </details>
    </div>
  );
}
