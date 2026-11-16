// Chart Time Machine, the "Odds" section of the ROI card (2026-11-15).
// Logic in chartTimeMachine.js; the replay runs on the server
// (time_machine.py). Its own component, so a fetch or a re-render here never
// touches the chart component.

import { useEffect, useMemo, useRef, useState } from "react";
import { roiAtPrice, timeMachineFan, timeMachinePlan, timeMachineView } from "./chartTimeMachine.js";
import "./chartTimeMachine.css";

const pct = (value) => `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(0)}%`;

// What the chart shows for a ROI plan (2026-11-15, Ganesh: "I want only odds
// or ROI in chart or both. Give me that option"): "roi" (the ROI boxes),
// "odds" (only Entry / Target / Stop with their odds) or "both". Kept in the
// plan, per ticker. Old plans have none: ROI.
export const CHART_SHOW_OPTIONS = [["roi", "ROI"], ["odds", "Odds"], ["both", "Both"]];
export function chartShowOf(plan) {
  const value = String(plan?.show || "").toLowerCase();
  return value === "odds" || value === "both" ? value : "roi";
}

// The replay for one plan. Asked again when the plan changes (side, target,
// stop), not on every live tick of the price: the price is read when the
// question is asked. Off (enabled false): asks nothing.
export function useTimeMachine(ticker, result, enabled) {
  const plan = timeMachinePlan(result, ticker);
  const planKey = enabled && plan.url ? plan.url.replace(/&spot=[^&]*/, "") : "";
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
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", payload: { ok: false, reason: `Could not reach the server: ${error.message}` }, key: planKey });
      });
    return () => controller.abort();
  }, [planKey, runs]);
  return {
    plan,
    planKey,
    status: state.status,
    payload: state.key === planKey ? state.payload : null,
    rerun: () => setRuns((count) => count + 1),
  };
}

export default function ChartTimeMachine({ ticker = "", result = null, machine }) {
  const { plan, planKey, payload } = machine;
  const state = { status: machine.status };
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
        <button type="button" className="chart-time-machine-rerun" onClick={machine.rerun} disabled={!planKey || state.status === "loading"} title="Replay again from the price now">↻ Run again</button>
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
