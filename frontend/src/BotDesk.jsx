// BOT Scorecard (2026-11-09): the paper accounts judged like a prop-firm
// evaluation. His "i dont trust to turn into real money ... see how prop fund
// trading bot will be". One answer at the top (ready / not ready), the checks
// behind it, the real account curve, and what each rule earned on REAL fills.
// Data: GET /api/momx-scanner/bot-desk (momx/bot_desk.py). Read-only.
import { useEffect, useMemo, useState } from "react";
import { Check, RefreshCw, X } from "lucide-react";
import { DESK_ACCOUNT_KEY, DESK_URL, deskAlerts, deskClock, deskCoverage, deskCurve, deskDollars, deskMoney, deskObjectives, deskPct, deskTone, deskVerdict } from "./botDesk.js";
import { truthAccountLabel, truthPct } from "./botTruth.js";
import { botRuleLabel } from "./momxBot.js";

const REFRESH_MS = 60 * 1000;
const TRUTH_REFRESH_MS = 5 * 60 * 1000;

function useJson(url, everyMs, refreshKey = 0) {
  const [state, setState] = useState({ doc: null, failed: false, loading: true });
  useEffect(() => {
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
  const truth = useJson("/api/momx-scanner/bot-truth?days=30", TRUTH_REFRESH_MS).doc;
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
                <button key={name} type="button" aria-pressed={name === account} onClick={() => pick(name)}>{truthAccountLabel(name)}</button>
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
            <div className="desk-card-head"><h4>What the BOT page claims against what really happened · last 30 days</h4></div>
            <div className="desk-claim-row">
              <Stat label="BOT page claims" value={truthPct(claim.botRoi)} sub={`${deskMoney(claim.botPnl)} · pretend fills at the mid price`} />
              <Stat label="Real paper result" value={truthPct(claim.paperRoi)} sub={`${deskMoney(claim.paperPnl)} · same ${claim.n} trades`} tone={deskTone(claim.paperRoi)} />
              <p className="desk-note">The Research view scores every signal as if it filled at the mid price. Real orders fill later and at worse prices, and many winners never fill. Trust the real result.</p>
            </div>
          </section>
        ) : null}

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
        <p>Passing shows the record is good enough to consider real money. It does not switch anything on. This app has no real-money order path for this bot.</p>
      </details>
    </div>
  );
}
