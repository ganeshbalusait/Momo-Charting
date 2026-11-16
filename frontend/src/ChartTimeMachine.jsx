// Chart Time Machine, the "Odds" section of the ROI card (2026-11-15).
// Logic in chartTimeMachine.js; the replay runs on the server
// (time_machine.py). Its own component, so a fetch or a re-render here never
// touches the chart component.

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { roiAtPrice, timeMachineFan, timeMachinePlan, timeMachineView } from "./chartTimeMachine.js";
import "./chartTimeMachine.css";

// Which tickers have Odds open: one copy for the page, shared by the toolbar
// button and every chart panel's ROI card. Kept for this page only.
let openTickers = new Set();
const listeners = new Set();
const subscribe = (listener) => { listeners.add(listener); return () => listeners.delete(listener); };
const snapshot = () => openTickers;
export function setOddsOpen(ticker, open) {
  const key = String(ticker || "").toUpperCase();
  if (!key || openTickers.has(key) === Boolean(open)) return;
  const next = new Set(openTickers);
  if (open) next.add(key); else next.delete(key);
  openTickers = next;
  listeners.forEach((listener) => listener());
}
export function useOddsOpen(ticker) {
  const open = useSyncExternalStore(subscribe, snapshot);
  return open.has(String(ticker || "").toUpperCase());
}

// The last answer per ticker, so the chart's lines can show the odds with
// the card closed (withOddsOnLines checks it is for the plan on screen).
let answers = new Map();
const answerListeners = new Set();
const subscribeAnswers = (listener) => { answerListeners.add(listener); return () => answerListeners.delete(listener); };
const answersSnapshot = () => answers;
function saveAnswer(ticker, payload) {
  answers = new Map(answers).set(String(ticker || "").toUpperCase(), payload);
  answerListeners.forEach((listener) => listener());
}
export function useOddsAnswer(ticker) {
  return useSyncExternalStore(subscribeAnswers, answersSnapshot).get(String(ticker || "").toUpperCase()) || null;
}

const pct = (value) => `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(0)}%`;

export default function ChartTimeMachine({ ticker = "", result = null }) {
  const plan = timeMachinePlan(result, ticker);
  // Asked again when the plan changes (side, target, stop), not on every
  // live tick of the price: the price is read when the question is asked.
  const planKey = plan.url ? plan.url.replace(/&spot=[^&]*/, "") : "";
  const urlRef = useRef(plan.url);
  urlRef.current = plan.url;
  const [state, setState] = useState({ status: "idle", payload: null, key: "" });
  const [runs, setRuns] = useState(0);

  useEffect(() => {
    if (!planKey) return undefined;
    const controller = new AbortController();
    setState((prev) => ({ status: "loading", payload: prev.key === planKey ? prev.payload : null, key: planKey }));
    fetch(urlRef.current, { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json().catch(() => null);
        if (!response.ok) throw new Error(body?.error || body?.reason || `HTTP ${response.status}`);
        setState({ status: "done", payload: body, key: planKey });
        saveAnswer(ticker, body);
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", payload: { ok: false, reason: `Could not reach the server: ${error.message}` }, key: planKey });
      });
    return () => controller.abort();
  }, [planKey, runs, ticker]);

  const payload = state.key === planKey ? state.payload : null;
  const view = useMemo(() => timeMachineView(payload, {
    roiAtTarget: result?.roiPercent ?? null,
    roiAtStop: result?.stopPremium && result?.entry ? -50 : null,
    roiAtNeither: payload?.ok ? roiAtPrice(result, payload.neitherMedianClose) ?? 0 : 0,
  }), [payload, result]);
  const fan = useMemo(() => timeMachineFan(payload, { width: 262, height: 120 }), [payload]);

  return (
    <section className="chart-time-machine" aria-label={`Odds from here for ${ticker}`} data-testid="chart-time-machine">
      <header>
        <b>Odds from here</b>
        <button type="button" className="chart-time-machine-rerun" onClick={() => setRuns((count) => count + 1)} disabled={!planKey || state.status === "loading"} title="Replay again from the price now">↻ Run again</button>
      </header>
      {!plan.url ? <p className="chart-time-machine-note" role="status">{plan.reason}</p>
        : state.status === "loading" && !view.ready ? <p className="chart-time-machine-note" role="status">Replaying the rest of the day 10,000 times…</p>
          : !view.ready ? <p className="chart-time-machine-note is-error" role="status">{view.reason}</p>
            : <>
              <small className="chart-time-machine-sub">{view.subtitle}</small>
              {fan ? <svg className="chart-time-machine-fan" viewBox={`0 0 ${fan.width} ${fan.height}`} role="img" aria-label="Replayed price paths to the close">
                <path d={fan.outer} className="is-band" />
                <path d={fan.inner} className="is-band is-inner" />
                {fan.paths.map((path) => <path key={path.key} d={path.d} stroke={path.color} className="is-path" />)}
                <path d={fan.median} className="is-median" />
                {fan.levels.map((level) => <g key={level.key}>
                  <line x1="0" x2={fan.width} y1={level.y} y2={level.y} stroke={level.color} className="is-level" />
                  <text x={fan.width - 3} y={level.y - 3} fill={level.color} textAnchor="end">{level.label}</text>
                </g>)}
              </svg> : null}
              <div className="chart-time-machine-odds" role="list" aria-label="Odds">
                {view.rows.map((row) => <div key={row.key} role="listitem" data-key={row.key}>
                  <span>{row.label}</span>
                  <b style={{ color: row.color }}>{row.pct.toFixed(0)}%</b>
                  <i><em style={{ width: `${Math.min(100, row.pct)}%`, background: row.color }} /></i>
                </div>)}
              </div>
              <div className="chart-time-machine-history" aria-label="Same trade on past days">
                <small>{view.historyNote}</small>
                <div className="chart-time-machine-days">
                  {view.history.map((day) => <i key={day.day} className={`is-${day.outcome}`} title={`${day.day}: ${day.outcome === "neither" ? "neither by 3:55 PM" : `${day.outcome} first`}`} />)}
                </div>
                <small className="chart-time-machine-counts">Target {view.historyCounts.target} · Stop {view.historyCounts.stop} · Neither {view.historyCounts.neither}</small>
              </div>
              {view.averageRoi != null ? <p className="chart-time-machine-option">
                Your option, weighted by these odds: <b className={view.averageRoi >= 0 ? "is-gain" : "is-loss"}>{pct(view.averageRoi)}</b>
                <small>target {pct(view.roiAtTarget)} · stop {pct(view.roiAtStop)} · neither {pct(view.roiAtNeither)} (estimates, no time decay)</small>
              </p> : null}
              <p className="chart-time-machine-verdict"><b>{view.verdict}.</b> Odds only: it never places or blocks a trade.</p>
            </>}
    </section>
  );
}
