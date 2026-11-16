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
  projectRoiOtherPlan,
  projectRoiPlanLines,
  projectRoiSwapSide,
  readProjectRoiPlans,
  writeProjectRoiPlans,
} from "./chartProjectRoi.js";
import { ChartProjectRoiPrimitive } from "./chartProjectRoiPrimitive.js";
import ChartTimeMachine, { setOddsOpen, useOddsAnswer, useOddsOpen } from "./ChartTimeMachine.jsx";
import { withOddsOnLines } from "./chartTimeMachine.js";

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
  // Time Machine "Odds" section (2026-11-15), open per ticker.
  const oddsOpen = useOddsOpen(ticker);
  const oddsAnswer = useOddsAnswer(ticker);
  // The phone's Odds button opens this card itself (2026-11-15: "I don't see
  // in phone" - the phone toolbar has no way to open the card once ROI is on).
  const [selfOpen, setSelfOpen] = useState(false);
  const shown = open || selfOpen;
  const closeCard = () => { setSelfOpen(false); onClose?.(); };

  // Opening the card on a ticker with no plan starts one at the live price,
  // so the lines appear at once - the same as the card in his screenshot.
  useEffect(() => {
    if (!open || !ticker || savedPlan || !(Number(spot) > 0) || !rows.length) return;
    if (getPlans()[ticker]) return;
    savePlans({ ...getPlans(), [ticker]: { side: "CALL", both: false, contracts: 1, entrySpot: Math.round(Number(spot) * 100) / 100, createdAt: Date.now() } });
  }, [open, ticker, savedPlan, spot, rows.length]);

  useEffect(() => {
    setEntryDraft(null);
    setTargetDraft(null);
    setSelfOpen(false);
  }, [ticker]);

  const updatePlan = (changes) => {
    const current = getPlans();
    const base = current[ticker] || { side: "CALL", both: false, contracts: 1, entrySpot: Math.round((Number(spot) || 0) * 100) / 100, createdAt: Date.now() };
    savePlans({ ...current, [ticker]: { ...base, ...changes } });
  };
  // Call <-> Put switches the side being edited; the other side's strike,
  // fill and target are kept (projectRoiSwapSide).
  const editSide = (side) => {
    const current = getPlans();
    if (!current[ticker]) return;
    savePlans({ ...current, [ticker]: projectRoiSwapSide(current[ticker], side) });
  };
  const deletePlan = () => {
    const next = { ...getPlans() };
    delete next[ticker];
    savePlans(next);
    closeCard();
  };

  const expiryExpectedMoves = useMemo(
    () => (defaultExpiry && Number(expectedMove) > 0 ? { [String(defaultExpiry).slice(0, 10)]: Number(expectedMove) } : null),
    [defaultExpiry, expectedMove],
  );
  const result = useMemo(() => (savedPlan || open ? buildProjectRoi({
    rows, spot, walls, defaultExpiry, expiryExpectedMoves, plan: savedPlan || {},
  }) : null), [savedPlan, open, rows, spot, walls, defaultExpiry, expiryExpectedMoves]);

  // With "Both" on, the opposite side is built from the plan's `other` choices.
  // One side by default; Both only when picked on the toolbar (his call,
  // 2026-11-11: "I don't default both ... next to ROI just add call, put,
  // both").
  const showBoth = savedPlan?.both === true;
  const otherResult = useMemo(() => (showBoth ? buildProjectRoi({
    rows, spot, walls, defaultExpiry, expiryExpectedMoves, plan: projectRoiOtherPlan(savedPlan),
  }) : null), [savedPlan, showBoth, rows, spot, walls, defaultExpiry, expiryExpectedMoves]);
  // ROI on/off (his ask, 2026-11-11: "give switch ROI on/off"): off hides
  // every line, box and zone and lets the price range forget the plan, but
  // keeps the plan itself, so On brings it back unchanged.
  const roiOn = savedPlan?.hidden !== true;
  // With Odds on, the Target / Stop / Entry boxes carry the odds (2026-11-15).
  const lines = useMemo(() => (savedPlan && result && roiOn
    ? withOddsOnLines(projectRoiPlanLines(result, otherResult), oddsOpen ? oddsAnswer : null, result)
    : []), [savedPlan, result, otherResult, roiOn, oddsOpen, oddsAnswer]);
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
  // Phone only (CSS): a small Odds button on the chart while ROI is on and the
  // card is closed; it opens the card with the odds.
  const oddsFab = savedPlan && roiOn && !shown ? <button
    type="button"
    className="chart-project-roi-odds-fab"
    aria-label={`Odds from here for ${ticker}`}
    onPointerDown={(event) => event.stopPropagation()}
    onClick={(event) => { event.stopPropagation(); setOddsOpen(ticker, true); setSelfOpen(true); }}
  >Odds</button> : null;
  if (!shown || !result) return <>{marker}{oddsFab}</>;

  const contractName = result.contract ? `${ticker} ${formatStrike(result.strike)}${result.sideLetter} ${shortExpiry(result.expiry)}` : ticker;
  const gain = result.roiPercent != null && result.roiPercent >= 0;
  const step = result.expectedMove > 0 ? Math.max(0.01, Math.round(result.expectedMove / 50 * 100) / 100) : 0.01;

  // Targets, stop and broker prices for one side. With "Both" on, the card
  // shows the edited side first, then the other side's (2026-11-11).
  const sidePlan = (side, heading = "") => <div className="chart-project-roi-sideplan" key={side.side} data-side={side.side}>
    {heading ? <b className="chart-project-roi-side-heading">{heading}</b> : null}
          {/* One line per target, nearest first (his ask: "at least 3 target"). */}
          {side.roiPercent == null ? <p className="chart-project-roi-line is-loss">No price for this contract yet</p>
            : side.targets.map((level) => <p
              key={level.key}
              className={`chart-project-roi-line ${level.roiPercent != null && level.roiPercent >= 0 ? "is-gain" : "is-loss"}`}
              title={level.reason}
            >
              {level.label} {level.roiPercent == null ? "—" : formatPercent(level.roiPercent)} ({level.profit == null ? "—" : formatDollars(level.profit)}) if {ticker} tags {level.stock.toFixed(2)}
            </p>)}
          {side.stopStock ? <p className="chart-project-roi-line is-stop">
            −50% ({formatDollars(side.stopLoss)}) if {ticker} tags {side.stopStock.toFixed(2)}
          </p> : null}
          {/* The contract prices to type into the broker (his ask, 2026-11-11):
              buy at the entry, limit-sell at the projected target price, stop
              at half the entry. */}
          {side.contract ? <div className="chart-project-roi-orders" aria-label="Broker order prices">
            <b>Broker orders · {ticker} {formatStrike(side.strike)}{side.sideLetter} {shortExpiry(side.expiry)}</b>
            <span>Buy <em>${side.entry.toFixed(2)}</em></span>
            <span className="is-stop">Stop <em>${side.stopPremium.toFixed(2)}</em></span>
            {side.targets.map((level) => <span key={level.key} className="is-gain" title={`${level.reason} · ${ticker} ${level.stock.toFixed(2)}`}>
              Sell {level.label} <em>{level.value > 0 ? `$${level.value.toFixed(2)}` : "—"}</em>
            </span>)}
          </div> : null}
  </div>;

  return (
    <>{marker}<aside className="chart-project-roi" aria-label={`Project ROI for ${ticker}`} data-testid="chart-project-roi" onPointerDown={(event) => event.stopPropagation()}>
      <header>
        <b>Project ROI</b>
        <button type="button" className="chart-project-roi-close" aria-label="Close Project ROI (lines stay on the chart)" title="Close (lines stay on the chart)" onClick={closeCard}>×</button>
      </header>
      {!result.ready ? <p className="chart-project-roi-empty">Waiting for {ticker}'s option chain…</p> : <>
        <div className="chart-project-roi-title">
          <b>{contractName}</b> entry ${result.entry.toFixed(2)}
          <small>{result.strikeReason}</small>
        </div>
        {result.quoteMismatch ? <p className="chart-project-roi-warn" role="status">
          ⚠ Option prices were quoted with {ticker} near ${result.quoteSpot.toFixed(2)}; it is ${result.spot.toFixed(2)} now (overnight move or a bad print). They reprice at the open - treat these numbers as rough.
        </p> : null}
        <div className={`chart-project-roi-hero ${gain ? "is-gain" : "is-loss"}`}>
          <strong>{result.roiPercent == null ? "—" : formatPercent(result.roiPercent)}</strong>
          <span>{result.profit == null ? "" : formatDollars(result.profit)}</span>
        </div>
        <p className="chart-project-roi-if">if {ticker} is at {result.target.toFixed(2)}</p>
        <button type="button" className="chart-project-roi-odds-toggle" aria-pressed={oddsOpen} onClick={() => setOddsOpen(ticker, !oddsOpen)} title="Replay the rest of the day 10,000 times from this stock's own candles: odds of target vs stop">{oddsOpen ? "Hide odds" : "Odds from here"}</button>
        {oddsOpen ? <ChartTimeMachine ticker={ticker} result={result} /> : null}
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
              title={showBoth ? `Edit the ${side === "CALL" ? "call" : "put"} side` : `Plan a ${side === "CALL" ? "call" : "put"}`}
              onClick={() => { setEntryDraft(null); setTargetDraft(null); editSide(side); }}
            >{side === "CALL" ? "Call" : "Put"}</button>)}
          </span>
        </div>
        {/* How many targets each side draws, 1-6 (his ask, 2026-11-11:
            "I use 1,2,3,4,5,6 give switch"); T4-T6 are runners past T3. */}
        <div className="chart-project-roi-count-targets" role="group" aria-label="Number of targets">
          <small>Targets</small>
          {[1, 2, 3, 4, 5, 6].map((count) => <button
            key={count}
            type="button"
            className={result.targetCount === count ? "is-active" : ""}
            aria-pressed={result.targetCount === count}
            aria-label={`Show ${count} target${count === 1 ? "" : "s"}`}
            onClick={() => updatePlan({ targetCount: count })}
          >{count}</button>)}
        </div>
        {result.expiries.length > 1 ? <div className="chart-project-roi-expiries" role="group" aria-label="Expiry">
          {result.expiries.map((item) => <button
            key={item.expiry}
            type="button"
            className={item.expiry === result.expiry ? "is-active" : ""}
            aria-pressed={item.expiry === result.expiry}
            onClick={() => { setEntryDraft(null); updatePlan({ expiry: item.expiry, strike: null, entry: null, other: { ...(savedPlan?.other || {}), strike: null, entry: null } }); }}
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
                // A typed fill pins the Entry line (and the stop) to the stock
                // price at the moment it was typed.
                if (entryDraft != null) {
                  updatePlan(value > 0
                    ? { entry: Math.round(value * 100) / 100, entrySpot: Math.round((Number(spot) || 0) * 100) / 100 }
                    : { entry: null });
                }
                setEntryDraft(null);
              }}
              onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }}
            />
            <button type="button" className="chart-project-roi-ask" onClick={() => { setEntryDraft(null); updatePlan({ entry: null }); }} title="Use the chain price (bid/ask middle, same as the Options chain)">↺ live</button>
            <small>{result.entryTyped ? "your fill" : "chain price"}</small>
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
        {sidePlan(result)}
        {otherResult?.ready && otherResult.contract ? sidePlan(otherResult, `${otherResult.side === "CALL" ? "Call" : "Put"} side · ${ticker} ${formatStrike(otherResult.strike)}${otherResult.sideLetter} entry $${otherResult.entry.toFixed(2)}`) : null}
        <p className="chart-project-roi-note">Estimate from the contract's live price, delta and gamma - not a promise. Time decay is not included.</p>
      </>}
      <button type="button" className="chart-project-roi-delete" onClick={deletePlan}>Delete</button>
    </aside></>
  );
}

// Toolbar controls next to the chart's ROI button (his ask, 2026-11-11:
// "next to ROI just add call, put, both ... ROI select then ON else OFF").
//  - ROI: selected = the plan's lines are ON for this ticker; tap again = OFF
//    (the plan is kept, so ON brings it back unchanged).
//  - Call / Put / Both (shown while ROI is on): which side the chart draws.
//    Tapping the side already picked opens or closes the card.
export function ChartProjectRoiToolbar({ symbol = "", spot = 0, cardOpen = false, onCardOpenChange }) {
  const ticker = String(symbol || "").trim().toUpperCase();
  const plans = useSyncExternalStore(subscribePlans, getPlans);
  const plan = plans[ticker] || null;
  const on = Boolean(plan) && plan.hidden !== true;
  const oddsOpen = useOddsOpen(ticker);
  const view = !plan ? "" : plan.both === true ? "BOTH" : sideOfPlan(plan);
  const save = (next) => savePlans({ ...getPlans(), [ticker]: next });
  const fresh = () => ({ side: "CALL", both: false, contracts: 1, entrySpot: Math.round((Number(spot) || 0) * 100) / 100, createdAt: Date.now() });

  const toggleRoi = () => {
    if (!ticker) return;
    if (on) {
      save({ ...plan, hidden: true });
      onCardOpenChange?.(false);
      return;
    }
    save(plan ? { ...plan, hidden: false } : fresh());
    onCardOpenChange?.(true);
  };
  const pick = (wanted) => {
    if (!plan) return;
    if (wanted === view) {
      onCardOpenChange?.(!cardOpen);
      return;
    }
    if (wanted === "BOTH") save({ ...plan, both: true, hidden: false });
    else save({ ...projectRoiSwapSide(plan, wanted), both: false, hidden: false });
  };

  return (
    <span className={`oi-finder-chart-roi-group${on ? " is-on" : ""}`} role="group" aria-label={`Project ROI for ${ticker}`}>
      <button
        className={`oi-finder-chart-roi${on ? " is-active" : ""}`}
        type="button"
        onClick={(event) => { event.stopPropagation(); toggleRoi(); }}
        title={on ? `ROI on for ${ticker}: tap to turn the lines off (the plan is kept)` : `ROI off: tap to show Entry / Targets / Stop for a ${ticker} option`}
        aria-label={`Project ROI for ${ticker}`}
        aria-pressed={on}
      >ROI</button>
      {on ? [["CALL", "Call"], ["PUT", "Put"], ["BOTH", "Both"]].map(([value, label]) => <button
        key={value}
        className={`oi-finder-chart-roi-side${view === value ? " is-active" : ""}`}
        type="button"
        onClick={(event) => { event.stopPropagation(); pick(value); }}
        title={view === value ? `${label} is drawn: tap to ${cardOpen ? "close" : "open"} the ROI card` : `Draw the ${label.toLowerCase()} plan`}
        aria-label={`ROI ${label.toLowerCase()} side`}
        aria-pressed={view === value}
      >{label}</button>) : null}
      {/* Time Machine (2026-11-15): opens the ROI card with its odds. Desktop
          only; on a phone the card has the same button. */}
      {on ? <button
        className={`oi-finder-chart-roi-odds${oddsOpen && cardOpen ? " is-active" : ""}`}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          const show = !(oddsOpen && cardOpen);
          setOddsOpen(ticker, show);
          if (show) onCardOpenChange?.(true);
        }}
        title={`Odds from here: replay the rest of the day 10,000 times from ${ticker}'s own candles`}
        aria-label={`Odds from here for ${ticker}`}
        aria-pressed={oddsOpen && cardOpen}
      >Odds</button> : null}
      {/* Phone (CSS shows this and hides the three above): the toolbar row has
          room for one more 24 px button, not three - three pushed the row
          into a sideways scroll that slid it under the ticker box. One tap
          steps Call -> Put -> Both. */}
      {on ? <button
        className="oi-finder-chart-roi-cycle"
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          pick(view === "CALL" ? "PUT" : view === "PUT" ? "BOTH" : "CALL");
        }}
        title={`ROI side: ${view === "BOTH" ? "Both" : view === "PUT" ? "Put" : "Call"} (tap for ${view === "CALL" ? "Put" : view === "PUT" ? "Both" : "Call"})`}
        aria-label={`ROI side ${view === "BOTH" ? "both" : view === "PUT" ? "put" : "call"}, tap to change`}
      >{view === "BOTH" ? "B" : view === "PUT" ? "P" : "C"}</button> : null}
    </span>
  );
}

function sideOfPlan(plan) {
  return String(plan?.side || "CALL").toUpperCase() === "PUT" ? "PUT" : "CALL";
}
