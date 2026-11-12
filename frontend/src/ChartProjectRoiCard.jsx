// Chart "Project ROI" card + its Entry / Target / Stop lines (2026-11-11).
//
// Lives in its own component so dragging the target slider or typing a fill
// re-renders this card only, never the chart component (thousands of lines;
// a re-render there recomputes studies). The lines go straight onto the live
// candle series through createPriceLine, outside the chart's big render
// effect, and are redrawn after every series rebuild (`seriesVersion`).

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  buildProjectRoi,
  formatDollars,
  formatPercent,
  formatStrike,
  PROJECT_ROI_STORAGE_KEY,
  projectRoiChartLines,
  readProjectRoiPlans,
  writeProjectRoiPlans,
} from "./chartProjectRoi.js";
import { ChartProjectRoiPrimitive } from "./chartProjectRoiPrimitive.js";

// One copy of the saved plans for the whole page. Every chart panel showing
// the same ticker (multi-chart, big screen, the maximized chart) reads and
// writes this copy, so two panels can never overwrite each other's fill. A
// change in another browser tab arrives through the `storage` event.
let sharedPlans = readProjectRoiPlans();
const planListeners = new Set();
function getPlans() {
  return sharedPlans;
}
function savePlans(next) {
  sharedPlans = next;
  writeProjectRoiPlans(next);
  planListeners.forEach((listener) => listener());
}
function subscribePlans(listener) {
  planListeners.add(listener);
  const onStorage = (event) => {
    if (event.key !== PROJECT_ROI_STORAGE_KEY) return;
    sharedPlans = readProjectRoiPlans();
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    planListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function shortExpiry(expiry) {
  const [, month, day] = String(expiry || "").split("-");
  return month && day ? `${Number(month)}/${Number(day)}` : String(expiry || "");
}

export default function ChartProjectRoiCard({
  symbol = "",
  rows: rowsProp = null,
  spot = 0,
  defaultExpiry = "",
  expectedMove = 0,
  walls = [],
  seriesRef,
  seriesVersion = 0,
  // App.jsx's price-range fit reads this, so target and stop stay on screen.
  autoscaleRef = null,
  onPlanPricesChange,
  open = false,
  onClose,
}) {
  const rows = useMemo(() => (Array.isArray(rowsProp) ? rowsProp : []), [rowsProp]);
  const ticker = String(symbol || "").trim().toUpperCase();
  const plans = useSyncExternalStore(subscribePlans, getPlans);
  const [entryDraft, setEntryDraft] = useState(null);
  const [targetDraft, setTargetDraft] = useState(null);
  const savedPlan = plans[ticker] || null;

  // Opening the card on a ticker with no plan starts one at the live price,
  // so the lines appear at once - the same as the card in his screenshot.
  useEffect(() => {
    if (!open || !ticker || savedPlan || !(Number(spot) > 0) || !rows.length) return;
    if (getPlans()[ticker]) return;
    savePlans({ ...getPlans(), [ticker]: { side: "CALL", contracts: 1, entrySpot: Math.round(Number(spot) * 100) / 100, createdAt: Date.now() } });
  }, [open, ticker, savedPlan, spot, rows.length]);

  useEffect(() => {
    setEntryDraft(null);
    setTargetDraft(null);
  }, [ticker]);

  const updatePlan = (changes) => {
    const current = getPlans();
    const base = current[ticker] || { side: "CALL", contracts: 1, entrySpot: Math.round((Number(spot) || 0) * 100) / 100, createdAt: Date.now() };
    savePlans({ ...current, [ticker]: { ...base, ...changes } });
  };
  const deletePlan = () => {
    const next = { ...getPlans() };
    delete next[ticker];
    savePlans(next);
    onClose?.();
  };

  const expiryExpectedMoves = useMemo(
    () => (defaultExpiry && Number(expectedMove) > 0 ? { [String(defaultExpiry).slice(0, 10)]: Number(expectedMove) } : null),
    [defaultExpiry, expectedMove],
  );
  const result = useMemo(() => (savedPlan || open ? buildProjectRoi({
    rows, spot, walls, defaultExpiry, expiryExpectedMoves, plan: savedPlan || {},
  }) : null), [savedPlan, open, rows, spot, walls, defaultExpiry, expiryExpectedMoves]);

  const lines = useMemo(() => (savedPlan && result ? projectRoiChartLines(result) : []), [savedPlan, result]);
  // Which lines exist decides create/remove; their price and label are
  // updated in place, so a live tick never removes and redraws them.
  const lineSet = lines.map((line) => line.key).join("|");
  const planTimeSec = savedPlan?.createdAt ? Math.floor(Number(savedPlan.createdAt) / 1000) : 0;
  const planTimeRef = useRef(planTimeSec);
  planTimeRef.current = planTimeSec;
  const drawnRef = useRef(new Map());
  const linesRef = useRef(lines);
  linesRef.current = lines;
  useEffect(() => {
    const series = seriesRef?.current?.candleSeries;
    if (!series || !lineSet) return undefined;
    const drawn = new Map();
    linesRef.current.forEach((line) => {
      drawn.set(line.key, series.createPriceLine({
        price: line.price,
        color: line.color,
        // The line and its label box are drawn by ChartProjectRoiPrimitive
        // (zones + boxes at the plan's start); this keeps only the colored
        // price tag on the right axis.
        lineVisible: false,
        axisLabelVisible: true,
        axisLabelColor: line.color,
        axisLabelTextColor: "#05070c",
        title: "",
      }));
    });
    drawnRef.current = drawn;
    return () => {
      drawn.forEach((priceLine) => {
        try { series.removePriceLine(priceLine); } catch { /* Series already torn down by a rebuild. */ }
      });
      drawnRef.current = new Map();
    };
  }, [seriesRef, seriesVersion, lineSet]);
  const linesKey = lines.map((line) => `${line.key}:${line.price}:${line.title}`).join("|");
  useEffect(() => {
    linesRef.current.forEach((line) => {
      try { drawnRef.current.get(line.key)?.applyOptions({ price: line.price }); } catch { /* Rebuild in progress. */ }
    });
    if (autoscaleRef) autoscaleRef.current = linesRef.current.map((line) => line.price);
    const pricesKey = linesRef.current.map((line) => line.price).join("|");
    if (pricesKey !== fittedPricesRef.current) {
      fittedPricesRef.current = pricesKey;
      onPlanPricesChangeRef.current?.();
    }
    primitiveRef.current?.setModel(linesRef.current.length ? { lines: linesRef.current, planTimeSec: planTimeSec } : null);
  }, [linesKey, planTimeSec, autoscaleRef]);

  // Zones, lines and label boxes like the reference card, on the candle pane.
  const primitiveRef = useRef(null);
  const markerRef = useRef(null);
  const fittedPricesRef = useRef("");
  const onPlanPricesChangeRef = useRef(onPlanPricesChange);
  onPlanPricesChangeRef.current = onPlanPricesChange;
  useEffect(() => {
    const series = seriesRef?.current?.candleSeries;
    if (!series || !lineSet) return undefined;
    const primitive = new ChartProjectRoiPrimitive({
      onDrawn: (keys) => { if (markerRef.current) markerRef.current.dataset.drawn = keys; },
    });
    primitive.setModel({ lines: linesRef.current, planTimeSec: planTimeRef.current });
    series.attachPrimitive(primitive);
    primitiveRef.current = primitive;
    return () => {
      try { series.detachPrimitive(primitive); } catch { /* Chart teardown already detached it. */ }
      if (primitiveRef.current === primitive) primitiveRef.current = null;
    };
  }, [seriesRef, seriesVersion, lineSet]);
  useEffect(() => () => { if (autoscaleRef) autoscaleRef.current = []; }, [autoscaleRef]);
  useEffect(() => {
    if (lineSet || !autoscaleRef) return;
    autoscaleRef.current = [];
    if (fittedPricesRef.current) {
      fittedPricesRef.current = "";
      onPlanPricesChangeRef.current?.();
    }
  }, [lineSet, autoscaleRef]);

  // Read by tests: the chart draws the lines on a canvas, so this hidden tag
  // lists what was drawn (key:price:label) for the panel.
  // data-drawn: label boxes the overlay drew inside the pane (target|entry|stop).
  const marker = lines.length ? <i hidden ref={markerRef} data-testid="chart-project-roi-lines" data-lines={linesKey} /> : null;
  if (!open || !result) return marker;

  const contractName = result.contract ? `${ticker} ${formatStrike(result.strike)}${result.sideLetter} ${shortExpiry(result.expiry)}` : ticker;
  const gain = result.roiPercent != null && result.roiPercent >= 0;
  const step = result.expectedMove > 0 ? Math.max(0.01, Math.round(result.expectedMove / 50 * 100) / 100) : 0.01;

  return (
    <>{marker}<aside className="chart-project-roi" aria-label={`Project ROI for ${ticker}`} data-testid="chart-project-roi" onPointerDown={(event) => event.stopPropagation()}>
      <header>
        <b>Project ROI</b>
        <button type="button" className="chart-project-roi-close" aria-label="Close Project ROI (lines stay on the chart)" title="Close (lines stay on the chart)" onClick={() => onClose?.()}>×</button>
      </header>
      {!result.ready ? <p className="chart-project-roi-empty">Waiting for {ticker}'s option chain…</p> : <>
        <div className="chart-project-roi-title">
          <b>{contractName}</b> entry ${result.entry.toFixed(2)}
          <small>{result.strikeReason}</small>
        </div>
        <div className={`chart-project-roi-hero ${gain ? "is-gain" : "is-loss"}`}>
          <strong>{result.roiPercent == null ? "—" : formatPercent(result.roiPercent)}</strong>
          <span>{result.profit == null ? "" : formatDollars(result.profit)}</span>
        </div>
        <p className="chart-project-roi-if">if {ticker} is at {result.target.toFixed(2)}</p>
        <input
          className="chart-project-roi-slider"
          type="range"
          aria-label="Target stock price"
          min={result.targetMin}
          max={result.targetMax}
          step={step}
          value={Math.min(Math.max(result.target, result.targetMin), result.targetMax)}
          onChange={(event) => { setTargetDraft(null); updatePlan({ target: Number(event.target.value) }); }}
        />
        <div className="chart-project-roi-row">
          <span className="chart-project-roi-count">
            <button type="button" aria-label="One contract fewer" onClick={() => updatePlan({ contracts: Math.max(1, result.contracts - 1) })}>−</button>
            <b aria-label="Contracts">{result.contracts}</b>
            <button type="button" aria-label="One contract more" onClick={() => updatePlan({ contracts: result.contracts + 1 })}>+</button>
            <small>contract{result.contracts === 1 ? "" : "s"}</small>
          </span>
          <span className="chart-project-roi-side" role="group" aria-label="Calls or puts">
            {["CALL", "PUT"].map((side) => <button
              key={side}
              type="button"
              className={result.side === side ? "is-active" : ""}
              aria-pressed={result.side === side}
              onClick={() => { setEntryDraft(null); setTargetDraft(null); updatePlan({ side, strike: null, entry: null, target: null }); }}
            >{side === "CALL" ? "Call" : "Put"}</button>)}
          </span>
        </div>
        {result.expiries.length > 1 ? <div className="chart-project-roi-expiries" role="group" aria-label="Expiry">
          {result.expiries.map((item) => <button
            key={item.expiry}
            type="button"
            className={item.expiry === result.expiry ? "is-active" : ""}
            aria-pressed={item.expiry === result.expiry}
            onClick={() => { setEntryDraft(null); updatePlan({ expiry: item.expiry, strike: null, entry: null }); }}
          >{shortExpiry(item.expiry)}</button>)}
        </div> : null}
        <small className="chart-project-roi-ladder-note">ATM + OTM inside ±{result.expectedMove > 0 ? result.expectedMove.toFixed(2) : "—"} · {shortExpiry(result.expiry)}</small>
        <div className="chart-project-roi-ladder" role="list" aria-label="Strike ladder">
          {result.ladder.map((item) => {
            const picked = item.strike === result.strike;
            return <button
              key={item.strike}
              type="button"
              role="listitem"
              className={`chart-project-roi-strike${picked ? " is-picked" : ""}`}
              aria-pressed={picked}
              onClick={() => { setEntryDraft(null); updatePlan({ strike: item.strike, entry: null }); }}
            >
              <b>{formatStrike(item.strike)}{result.sideLetter}</b>
              <span>{item.price > 0 ? item.price.toFixed(2).replace(/^0\./, ".") : "—"}</span>
              <span>{item.distance >= 0 ? "+" : "−"}{Math.abs(item.distance).toFixed(2)}</span>
              <em>{picked ? "picked" : item.isAtm ? "ATM" : ""}</em>
            </button>;
          })}
        </div>
        <div className="chart-project-roi-fields">
          <label>
            <span>Entry</span>
            <input
              aria-label="Entry option price"
              inputMode="decimal"
              value={entryDraft ?? result.entry.toFixed(2)}
              onChange={(event) => setEntryDraft(event.target.value)}
              onBlur={() => {
                const value = Number.parseFloat(entryDraft);
                if (entryDraft != null) updatePlan({ entry: value > 0 ? Math.round(value * 100) / 100 : null });
                setEntryDraft(null);
              }}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
            />
            <button type="button" className="chart-project-roi-ask" onClick={() => { setEntryDraft(null); updatePlan({ entry: null }); }} title="Use the live ask">↺ ask</button>
            <small>{result.entryTyped ? "your fill" : "live ask"}</small>
          </label>
          <label>
            <span>Target</span>
            <input
              aria-label="Target stock price input"
              inputMode="decimal"
              value={targetDraft ?? result.target.toFixed(2)}
              onChange={(event) => setTargetDraft(event.target.value)}
              onBlur={() => {
                const value = Number.parseFloat(targetDraft);
                if (targetDraft != null) updatePlan({ target: value > 0 ? Math.round(value * 100) / 100 : null });
                setTargetDraft(null);
              }}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
            />
            <small>{result.targetTyped ? "your exit" : "full expected move"}</small>
          </label>
          <label>
            <span>Stop</span>
            <input aria-label="Stop stock price" readOnly value={result.stopStock ? result.stopStock.toFixed(2) : "—"} />
            <small>auto −50%</small>
          </label>
        </div>
        <p className={`chart-project-roi-line ${gain ? "is-gain" : "is-loss"}`}>
          {result.roiPercent == null ? "No price for this contract yet" : `${formatPercent(result.roiPercent)} (${formatDollars(result.profit)}) if ${ticker} tags ${result.target.toFixed(2)}`}
        </p>
        {result.stopStock ? <p className="chart-project-roi-line is-stop">
          −50% ({formatDollars(result.stopLoss)}) if {ticker} tags {result.stopStock.toFixed(2)}
        </p> : null}
        {/* The contract prices to type into the broker (his ask, 2026-11-11):
            buy at the entry, limit-sell at the projected target price, stop
            at half the entry. */}
        {result.contract ? <div className="chart-project-roi-orders" aria-label="Broker order prices">
          <b>Broker orders · {ticker} {formatStrike(result.strike)}{result.sideLetter} {shortExpiry(result.expiry)}</b>
          <span>Buy <em>${result.entry.toFixed(2)}</em></span>
          <span className="is-gain">Sell (target) <em>{result.projectedValue > 0 ? `$${result.projectedValue.toFixed(2)}` : "—"}</em></span>
          <span className="is-stop">Stop <em>${result.stopPremium.toFixed(2)}</em></span>
        </div> : null}
        <p className="chart-project-roi-note">Estimate from the contract's live price, delta and gamma - not a promise. Time decay is not included.</p>
      </>}
      <button type="button" className="chart-project-roi-delete" onClick={deletePlan}>Delete</button>
    </aside></>
  );
}
