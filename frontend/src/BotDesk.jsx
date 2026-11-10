// BOT Scorecard (2026-11-09): the paper accounts judged like a prop-firm
// evaluation. His "i dont trust to turn into real money ... see how prop fund
// trading bot will be". One answer at the top (ready / not ready), the checks
// behind it, the real account curve, and what each rule earned on REAL fills.
// Data: GET /api/momx-scanner/bot-desk (momx/bot_desk.py). Read-only.
import { useEffect, useMemo, useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import { DESK_ACCOUNT_KEY, DESK_CONTROL_URL, DESK_URL, deskAccountLabel, deskAiPicks, deskAlerts, deskClock, deskControl, deskCoverage, deskCurve, deskDollars, deskEvaluation, deskLessons, deskMoney, deskObjectives, deskPct, deskStrategies, deskStuckRows, deskTone, deskTruthDays, deskTruthLabel, deskVerdict, deskVersus } from "./botDesk.js";
import { truthAccountLabel, truthPct } from "./botTruth.js";
import { botRuleLabel } from "./momxBot.js";

const REFRESH_MS = 60 * 1000;
const TRUTH_REFRESH_MS = 5 * 60 * 1000;

function useJson(url, everyMs, refreshKey = 0) {
  const [state, setState] = useState({ doc: null, failed: false, loading: true });
  useEffect(() => {
    if (!url) return undefined;          // not known yet (waits for the first Scorecard read)
    let alive = true;
    const load = () => fetch(url, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((doc) => { if (alive) setState({ doc, failed: false, loading: false }); })
      // A failed refresh keeps the last good numbers but says so (failed stays true).
      .catch(() => { if (alive) setState((s) => ({ doc: s.doc, failed: true, loading: false })); });
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

export default function BotDesk() {
  const [refresh, setRefresh] = useState(0);
  const { doc, failed, loading } = useJson(DESK_URL, REFRESH_MS, refresh);
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
            {failed ? "Refresh failed · showing " : "Real paper fills · as of "}{deskClock(doc.asOf)}
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

      {control ? (
        <section className="desk-controls" aria-label="Paper bot controls">
          <span className={"desk-pill is-" + control.tone} data-testid="desk-buying">{control.state}</span>
          <button type="button" className="desk-btn" disabled={busy}
            onClick={() => act({ action: control.action, account }, control.done)}>{control.button}</button>
          <span className={"desk-pill" + (control.stopHit ? " is-bad" : "")} data-testid="desk-stop">{control.stop}</span>
          <span className="desk-note">Pause and the daily loss stop hold new buys only. Positions already held still sell on their plan.</span>
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

        {strategies ? (
          <section className="desk-card" aria-label="The three strategies">
            <div className="desk-card-head">
              <h4>The three strategies · which one is good</h4>
              <span className="desk-note">Real paper fills on the 0.20-delta contract{doc.startDay ? ` since ${doc.startDay.slice(5)}` : ""}.</span>
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
        </div>

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
      </>)}

      <details className="desk-how">
        <summary>How this evaluation works</summary>
        <p>A prop firm hands a trader an account and reads the real record: a profit target, a daily loss limit and a maximum drawdown. This page does the same to the bot. Every number here is a real Alpaca paper order. An order that never filled earns nothing, and profit lands on the day the trade closed.</p>
        <p>The checks: up {deskDollars(limits.profitTarget)} on a {deskDollars(limits.accountSize)} account, never down more than {deskDollars(limits.dailyLossLimit)} in one day, never more than {deskDollars(limits.maxDrawdown)} below the highest point, at least {limits.minDays} trading days and {limits.minTrades} closed trades, profit factor {limits.minProfitFactor} or more, still in profit without the best {limits.luckTrades} trades, and at least {limits.minFillPct}% of orders filled.</p>
        <p>Safety: Pause stops new paper buys for that account until you resume. The daily loss stop does the same by itself once the account is down {deskDollars(limits.dailyLossLimit)} on the day, until the next day. Neither one sells what is already held; those positions keep their exit plan. A stuck order is a buy the broker never confirmed; Remove checks the broker first and then frees its slot.</p>
        <p>AI: it never blocks a trade. On Paper 5 (the AI bot) it chooses which waiting signal gets a free slot; with no fresh answer the newest signal goes first, the same as Paper 3 (the Rules bot). Every evening it studies the losing real trades of each strategy and writes one lesson, one change to try and how to test it. A lesson is a suggestion; nothing changes until you decide.</p>
        <p>Passing shows the record is good enough to consider real money. It does not switch anything on. This app has no real-money order path for this bot.</p>
      </details>
    </div>
  );
}
