// The virtual BOT page (2026-09-28, admin only): which rule is winning, which
// tickers it took, P/L. Data: GET /api/momx-scanner/bot?days=N (momx_worker,
// refused on the server for non-admins). No order is ever sent.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { paperMetrics, paperTotals, reviewLabel, paperTime } from "./paperBotMetrics.js";
import { BOT_LEVEL_OPTIONS, BOT_LEVEL_HELP, filterBotLevel } from "./botLevels.js";
import { SCANNER_SETUP_TYPES } from "./scannerSetupTypes.js";
import { loadPaperHistory } from "./paperBotHistory.js";
import { Bot, RefreshCw, X } from "lucide-react";
import { BOT_RULE_HELP, BOT_RULE_LABELS, BOT_RULE_ORDER, botRuleLabel, BOT_TEST_RULES, botContractLabel, botDailyLog, botEntryStrikeLabel, botNewsBook, botAiBook, isEarlyDayLine, setBotFills, setBotExit, setBotContract, BOT_EXIT_LABELS, BOT_CONTRACT_LABELS, botExitMatrix, botRiskOk, botRiskRoi, botMomentumMatrix, botPeriodMatrix, botReport, formatBotDollars, formatBotPct, nextBotSort, sortBotRows } from "./momxBot.js";

// Trading days with bot data (his ask 2026-09-28: daily / 2D / 3D / 4D /
// weekly / 2 week / 3 week ... overall).
const RANGES = [
  { days: 1, label: "Today" }, { days: 2, label: "2D" }, { days: 3, label: "3D" }, { days: 4, label: "4D" },
  { days: 5, label: "1W" }, { days: 10, label: "2W" }, { days: 15, label: "3W" }, { days: 20, label: "1M" },
  { days: 90, label: "90D" }, { days: 180, label: "180D" },
];
const HISTORY_REFRESH_MS = 10 * 60 * 1000;
// One view at a time (his ask 2026-09-28: "page is very big, difficult to
// scroll ... make it tab or panel"). The chosen tab is remembered.
const BOT_TABS = [
  { key: "leaders", label: "Leaderboard" },
  { key: "trades", label: "Trades" },
  { key: "periods", label: "Winners by period" },
  { key: "combos", label: "Contract × exit" },
  { key: "momentum", label: "Momentum at entry" },
  { key: "days", label: "Day by day" },
  { key: "rules", label: "Rules explained" },
  // Its own tab after Rules explained (2026-10-17, his "Add there ... next").
  { key: "ai", label: "AI model" },
];
const BOT_TAB_KEY = "bot.tab";
function readStoredTab() {
  try {
    const v = window.localStorage.getItem(BOT_TAB_KEY);
    return BOT_TABS.some((t) => t.key === v) ? v : "leaders";
  } catch {
    return "leaders";
  }
}
const REFRESH_MS = 60 * 1000;

function tone(value) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? " is-up" : " is-down";
}

function marketOpenNow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = (type) => (parts.find((p) => p.type === type) || {}).value;
  const minute = Number(get("hour")) * 60 + Number(get("minute"));
  return !["Sat", "Sun"].includes(get("weekday")) && minute >= 9 * 60 + 30 && minute <= 16 * 60 + 5;
}

// Alpaca PAPER fills of the Jev BOT trades (momx/paper_mirror.py, 2026-10-16):
// real paper orders beside the Bot's mid-price estimates. Polled every 30 s.
// One line per paper account (2026-10-17: paper3 = All trades, paper5 = Jev BOT).
function PaperMirrorCard() {
  const [mirrors, setMirrors] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/momx-scanner/paper-mirror", { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d) setMirrors(Array.isArray(d.mirrors) ? d.mirrors : [d]); })
      .catch(() => {});
    load();
    const id = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  if (!mirrors || !mirrors.length) return null;
  return <>{mirrors.map((m) => <PaperMirrorLine key={m.account} doc={m} />)}</>;
}

function PaperMirrorLine({ doc }) {
  const [open, setOpen] = useState(false);
  const t = doc.totals || {};
  const state = doc.refused ? doc.refused : doc.enabled ? "ON" : "OFF (off switch)";
  const money = (v) => (v == null ? "-" : (v < 0 ? "-$" : "$") + Math.abs(Number(v)).toFixed(2));
  return (
    <section className="bot-jevbook-note" aria-label="Alpaca paper orders">
      <button type="button" className="bot-refresh" onClick={() => setOpen(!open)}>
        Alpaca PAPER ({doc.account || "paper5"}) · {doc.rules?.book || ""} · {state} · today {t.entries || 0} orders · {t.filled || 0} filled · {t.missed || 0} missed · {t.open || 0} open · P/L {money(t.pl)} {open ? "▲" : "▼"}
      </button>
      {open ? (
        <div>
          <p className="bot-hint">
            Real PAPER orders (no money) for the Bot's {doc.rules?.book || "All trades"} trades, bull and bear, limit at the mid.
            Size: {doc.rules?.sizing || "-"}. Exit: {doc.rules?.exitPlan || "-"}. {doc.rules?.maxOpen ?? 8} open,
            {doc.rules?.maxEntriesPerDay ?? 25} a day, one per ticker. A buy not filled in 2 min is cancelled. {doc.error ? "Last error: " + doc.error : ""}
          </p>
          {/* Columns always shown (2026-10-17, his "What about the columns?"). */}
          <div className="bot-scroll">
            <table className="bot-table">
              <thead><tr><th>Time</th><th>Side</th><th>Rule</th><th>Ticker</th><th>Contract</th><th>Qty</th><th>Bot mid</th><th>Paper buy</th><th>Sold</th><th>Stop</th><th>P/L</th><th>Status</th></tr></thead>
              <tbody>
                {(doc.rows || []).length ? doc.rows.map((r) => (
                  <tr key={r.buyId || r.at + r.symbol}>
                    <td>{String(r.buyAt || "").slice(11, 16)}</td>
                    <td>{r.side}</td><td>{r.rule}</td><td>{r.symbol}</td><td>{r.contract}</td>
                    <td className="bot-num">{paperMetrics(r).filled ?? "—"} / {r.qty}</td>
                    <td className="bot-num">{money(r.botEntry)}</td>
                    <td className="bot-num">{money(r.buyFill)}</td>
                    <td title={(r.sells || []).map((f) => f.qty + " @ " + money(f.price) + " (" + (f.why || "") + ")").join(" | ")}>
                      {(r.sells || []).map((f) => f.qty + "@" + money(f.price)).join(", ") || "-"}
                    </td>
                    <td className="bot-num">{money(r.stop)}</td>
                    <td className="bot-num">{money(r.pl)}</td>
                    <td title={r.note || ""}>{r.status}</td>
                  </tr>
                )) : (
                  <tr><td colSpan={12} className="bot-hint">No paper orders yet today - they start when the market opens (9:30 AM ET).</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

// "Paper 3" / "Paper 5" tabs (2026-10-17, his "Next news trade add 2 more tab for
// paper trades"): the real Alpaca PAPER orders of one account, on the page's own
// BULL / BEAR and range switches.
const PAPER_MONEY = (v) => (v == null ? "-" : (v < 0 ? "-$" : "$") + Math.abs(Number(v)).toFixed(2));

function PaperTable({ rows, showDay, account, team }) {
  return (
    <div className="bot-scroll">
      <table className="bot-table">
        <thead><tr>{showDay ? <th>Day</th> : null}<th>Order time (ET)</th><th>Fill time (ET)</th><th>Side</th><th>Rule</th><th>Ticker</th><th>Contract</th><th>Filled / ordered</th><th>Held</th><th>Entry mid estimate</th><th>Buy fill</th><th>Sell fills / expiry</th><th>Stop</th><th>Realized P/L</th><th>Open P/L (mid)</th><th>Closed ROI</th><th>Status</th><th>Post-trade review</th></tr></thead>
        <tbody>
          {rows.length ? rows.map((r) => (
            <tr key={(r.day || "") + (r.buyId || r.at + r.symbol)}>
              {showDay ? <td>{String(r.day || "").slice(5)}</td> : null}
              <td>{paperTime(r.buyAt)}</td>
              <td title={r.buyFilledAt || (r.buyFillAt ? "Broker fill timestamp unavailable; this is when the fill was observed" : "No recorded fill time")}>{r.buyFilledAt ? paperTime(r.buyFilledAt) : r.buyFillAt ? paperTime(r.buyFillAt) + " observed" : "—"}</td>
              <td>{r.side}</td><td>{r.rule}</td><td>{r.symbol}</td><td>{r.contract}</td>
              <td className="bot-num">{paperMetrics(r).filled ?? "—"} / {r.qty}</td>
              <td className="bot-num">{paperMetrics(r).held ?? "—"}</td>
              <td className="bot-num">{PAPER_MONEY(r.botEntry)}</td>
              <td className="bot-num">{PAPER_MONEY(r.buyFill)}</td>
              <td title={(r.sells || []).map((f) => f.qty + " @ " + PAPER_MONEY(f.price) + " (" + (f.why || "") + ")").join(" | ")}>
                {(r.sells || []).map((f) => f.qty + "@" + PAPER_MONEY(f.price) + (f.kind === "expiration" ? " expired" : "")).join(", ") || "-"}
              </td>
              <td className="bot-num">{PAPER_MONEY(r.stop)}</td>
              <td className={"bot-num" + (paperMetrics(r).realized > 0 ? " is-up" : paperMetrics(r).realized < 0 ? " is-down" : "")}>{PAPER_MONEY(paperMetrics(r).realized)}</td>
              <td title={r.markAt ? "Quote: " + r.markAt : "No fresh midpoint quote"} className={"bot-num" + (paperMetrics(r).open > 0 ? " is-up" : paperMetrics(r).open < 0 ? " is-down" : "")}>{r.status === "missed" || r.status === "buying" && r.buyFill == null ? "—" : paperMetrics(r).open == null ? "Unpriced" : PAPER_MONEY(paperMetrics(r).open)}</td>
              <td className="bot-num">{formatBotPct(paperMetrics(r).roi, 1)}</td>
              <td title={r.note || ""}>{r.status}{r.sells?.some(f => f.kind === "expiration") ? " · expired" : ""}</td>
              <td title="Review status refers to the batch containing this trade; it is not an entry approval.">{reviewLabel(team, `${account}|${r.buyId}`, r)}</td>
            </tr>
          )) : (
            <tr><td colSpan={showDay ? 18 : 17} className="bot-hint">No paper orders in this range. New entries require an eligible signal and a fresh midpoint quote during the entry windows. AI reviews completed trades afterward.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ExperimentStatus({ lab }) {
  if (!lab) return <p>Experiment runner status unavailable.</p>;
  const e = lab.latest;
  const forward = e?.evaluation;
  const validation = e?.screen?.validation;
  const phases = { forward: "Future paper evaluation", active: "Active on Paper 5", rejected: "Rejected", rolled_back: "Rolled back" };
  const spec = e?.screen?.spec;
  const describe = s => s?.kind === "spread_ceiling" ? `Entry spread no wider than ${Math.round(s.value * 100)}% of premium`
    : s?.kind === "premium_floor" ? `Option premium at least ${PAPER_MONEY(s.value)}`
    : s?.kind === "entry_after" ? `Entries from ${String(Math.floor(s.value / 60)).padStart(2, "0")}:${String(s.value % 60).padStart(2, "0")} ET`
    : "No parameter qualified";
  const rule = describe(spec);
  const actual = e?.screen?.version >= 2;
  const data = e?.screen?.dataset;
  return <div className="bot-experiment-status" aria-label="Automated paper experiments">
    <p><b>Automated paper experiments</b> · Paper 3: baseline · Paper 5: {lab.activeId ? "validated candidate " + lab.activeId : "baseline rules"}</p>
    {lab.registeredActiveId && !lab.monitorHealthy ? <p role="status">Candidate suspended: waiting for fresh monitoring. New Paper 5 entries use baseline rules.</p> : null}
    <p>Monitoring: {lab.monitorHealthy ? "current" : "waiting for a successful check"}. {!lab.busy && !lab.registeredActiveId && e?.state !== "forward" && lab.nextProposalAt ? `Next research eligible: ${new Date(lab.nextProposalAt * 1000).toLocaleString("en-US", { timeZone: "America/New_York" })} ET.` : ""}</p>
    {lab.error ? <p role="status">{lab.error}</p> : null}
    <p>{lab.busy ? "Planner and historical evaluation running…" : e ? `${phases[e.state] || e.state} · ${e.reason}` : "Waiting for a completed agent discussion to propose an experiment."}</p>
    {e ? <details><summary>Experiment evidence · {e.id}</summary>
      <p><b>Planner:</b> {e.proposal?.reason} ({e.proposal?.provider || "unknown provider"})</p>
      <p><b>Candidate:</b> {rule}. Frozen after {e.frozenAfterDay}; later outcomes cannot change its entry decisions.</p>
      {data ? <p><b>Actual paper evidence:</b> {data.usableFills} usable closed trades across {data.entryDays} entry days · {data.missed} unfilled orders · {data.invalidClosed} incomplete closed trades. Training {e.screen.trainingFills} fills; later validation {e.screen.validationFills} fills. {data.legacyInputs} older records lack saved order-quote snapshots; unavailable spreads are never invented.</p> : null}
      {validation ? <p>Historical validation: baseline {PAPER_MONEY(validation.baselineNet)} · candidate {PAPER_MONEY(validation.candidateNet)} · stress {PAPER_MONEY(validation.candidateStressNet)}. {actual ? "Actual Paper 3 fills and quantities with estimated costs; hypothetical selection, not proven Paper 5 performance." : "Exploratory midpoint results, one contract per signal; not confirmed portfolio fills."}</p> : <p>{e.screen?.reason}</p>}
      {actual ? <p>Selection compares supported rules on two earlier training periods, then checks one candidate on later dates. Validation failures do not trigger substitution of another candidate. Future paper evidence is still required before promotion.</p> : null}
      {e?.screen?.candidates?.length ? <details><summary>Training comparisons · {e.screen.candidates.length} bounded candidates</summary>
        <div className="bot-scroll"><table className="bot-table"><thead><tr><th>Candidate</th><th>Training net</th><th>Change vs baseline</th><th>Input coverage</th><th>Training result</th></tr></thead><tbody>
          {e.screen.candidates.map(c => <tr key={`${c.spec.kind}-${c.spec.value}`}><td>{describe(c.spec)}</td><td className={`bot-num ${c.training.candidateNet < 0 ? "is-down" : c.training.candidateNet > 0 ? "is-up" : ""}`}>{PAPER_MONEY(c.training.candidateNet)}</td><td className={`bot-num ${c.training.improvement < 0 ? "is-down" : c.training.improvement > 0 ? "is-up" : ""}`}>{PAPER_MONEY(c.training.improvement)}</td><td>{Math.round(c.training.inputCoverage * 100)}%</td><td>{c.reason}</td></tr>)}
        </tbody></table></div>
      </details> : null}
      {forward ? <>
        <p><b>Future paper evaluation:</b> {forward.observedDays}/{forward.requiredDays} entry days · {forward.trades}/{forward.requiredFills} matched closed trades · {Math.round(forward.decisionCoverage * 100)}% decision coverage.</p>
        <p>Closed-fill evidence: {Math.round((forward.outcomeCoverage || 0) * 100)}% complete · {forward.unpricedClosed || 0} unpriced closed trades. Missing fill evidence prevents promotion; the original evaluation days stay fixed.</p>
        <p>Baseline net {PAPER_MONEY(forward.baselineNet)} · candidate net {PAPER_MONEY(forward.candidateNet)} · stress net {PAPER_MONEY(forward.candidateStressNet)}.</p>
        <p>Daily-close drawdown: baseline {PAPER_MONEY(forward.baselineDrawdown)} · candidate {PAPER_MONEY(forward.candidateDrawdown)}. Avoided losses {forward.avoidedLosses}; missed winners {forward.missedWinners}.</p>
        {Number.isFinite(forward.baselineWinRate) && Number.isFinite(forward.candidateWinRate) ? <p>Before-fee win rate: baseline {(forward.baselineWinRate * 100).toFixed(1)}% ({forward.baselineWins} wins / {forward.baselineLosses} losses / {forward.baselineFlat} flat) · candidate {(forward.candidateWinRate * 100).toFixed(1)}% ({forward.candidateWins} wins / {forward.candidateLosses} losses / {forward.candidateFlat} flat). Total losing-trade losses: baseline {PAPER_MONEY(forward.baselineLossTotal)} · candidate {PAPER_MONEY(forward.candidateLossTotal)}. Measured outcomes before fees, not predicted probabilities.</p> : null}
      </> : null}
      <p>Promotion requires 10 future entry days, at least 100 closed fills, complete closed-fill evidence, 95% recorded decision coverage, at least half the opportunities retained, a higher net win rate and smaller total losing-trade losses, positive net and stressed results, improved P/L and no worse daily-close drawdown. The lower confidence bound of daily improvement must be positive.</p>
      <p>Fees are excluded. Stress testing applies an extra 1% of entry premium. Unused capital stays cash. Future comparisons use Paper 3 fills; Paper 5 fills may differ. Candidate monitoring can revert Paper 5 to baseline after five entry days, or $500 of candidate closed losses in a day. Existing positions keep their exit rules.</p>
      <p>Supported experiments: entry spread, premium floor, opening-time exclusion. Agents cannot execute generated code or change live-money trading.</p>
      {lab.experiments?.map(x => <p key={x.id}>{x.id} · {phases[x.state] || x.state} · {x.reason}</p>)}
      {lab.recentDecisions?.length ? <p>Recent Paper 5 decisions: {lab.recentDecisions.map(x => `${x.symbol} ${x.qualified ? "qualified" : "outside candidate rule"}`).join(" · ")}</p> : null}
    </details> : null}
  </div>;
}

function LearningTeamStatus({ onStatus }) {
  const [team, setTeam] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/momx-scanner/paper-learning", { credentials: "include", cache: "no-store", signal: AbortSignal.timeout(15000) })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(d => {
        if (d?.mode !== "post_trade" || typeof d.enabled !== "boolean") throw new Error("Review service warming up");
        if (alive) { setTeam(d); onStatus(d); setError(""); }
      })
      .catch(() => { if (alive) { onStatus(null); setError("Review status unavailable; entries and exits do not wait for the agents."); } });
    load();
    const id = window.setInterval(load, 30000);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return <section className="bot-jevbook-note" aria-label="Post-trade learning team">
    <b>Trade first · learn afterward</b>
    <p>Scanner specialist → trade reviewer → critic → experiment planner. The app coordinates their recorded findings; {team?.primaryProvider === 'ollama' ? 'local Ollama primary; OpenClaw and ChatGPT requests bypassed' : 'OpenClaw gateway primary, with local Ollama fallback'}. Agents have no order or exit authority.</p>
    <p>Hermes independent research: {team?.hermesEnabled ? "enabled" : "not enabled"}{team?.hermesStatus?.available === true ? " · last request succeeded" : team?.hermesStatus?.available === false ? " · last request failed" : " · not yet verified"}. Execution, RVOL, squeeze, Skittles, regime and anomaly specialists are called when the evidence points at them; entry, exit, risk and news specialists take scheduled turns otherwise. No framework has proven superior trading performance yet.</p>
    <p>{error || (team ? `Reviews ${team.enabled ? "ON" : "OFF"} · ${team.reviewedTrades || 0} trades reviewed · ${team.pendingTrades || 0} queued · ${team.failedTrades || 0} failed` : "Loading review status…")}</p>
    {team?.sessionPacingSeconds ? <p>Agents run around the clock. During the trading day (4 AM-8 PM ET) they take one batch every {Math.round(team.sessionPacingSeconds / 60)} min so the scanner stays fast; overnight and on weekends they run back-to-back.</p> : null}
    {team?.snapshotCoverage ? <p>Entry snapshots: {team.snapshotCoverage.scanner}/{team.snapshotCoverage.trades} with Level/Setup inputs · {team.snapshotCoverage.chart}/{team.snapshotCoverage.trades} with chart inputs. Historical gaps stay unknown.</p> : null}
    {team?.entryPatterns ? <details><summary>Unsupervised entry patterns · {team.entryPatterns.observations} observations</summary>
      <p>{team.entryPatterns.note} {team.entryPatterns.state === 'insufficient_data' ? `At least ${team.entryPatterns.minimum} distinct frozen observations are required.` : 'Agents receive these groups as research evidence; model changes still require later validation.'}</p>
      {(team.entryPatterns.clusters || []).map(cluster => <p key={cluster.id}>Group {cluster.id}: {cluster.observations} observations · {cluster.commonSetupRules.map(key => SCANNER_SETUP_TYPES.find(([id]) => id === key)?.[1] || key).join(', ') || 'No common Setup rule'}</p>)}
    </details> : null}
    {team?.error ? <p role="status">{team.error}</p> : null}
    {team?.capture && Object.keys(team.capture).length ? <details><summary>Scanner → BOT capture</summary>
      <p>Rule-qualified signals received by the BOT, including the scanner's secondary rows. This does not count every market opportunity or prove that an unentered signal would have won. News trade and AI pick are views of the shared virtual book.</p>
      {Object.entries(team.capture).map(([side, c]) => <div key={side}>
        <p><b>{side.toUpperCase()} · {c.day || "Waiting for scanner"}</b>: {c.signals} recorded signals · {c.entered} entered · {c.pending} waiting for quotes · {c.notEntered} not entered.</p>
        {Object.entries(c.reasons || {}).map(([reason, n]) => <p key={reason}>{n} · {reason}</p>)}
      </div>)}
      <p>Incomplete option chains retry while the original signal is fresh, for up to 10 minutes and within entry hours. Expired signals remain visible with a reason; the BOT does not invent an earlier fill.</p>
    </details> : null}
    {team?.scoreboard && Object.keys(team.scoreboard).length ? <details><summary>Specialist scoreboard</summary>
      <p>Each specialist calls whether the next completed batch will win a higher or lower share of trades. The call is saved before that batch exists, then graded; "unclear" is counted, never graded. Specialists see their own record on their next turn.</p>
      {Object.entries(team.scoreboard).sort(([a], [b]) => a.localeCompare(b)).map(([role, s]) => <p key={role}>
        <b>{role}</b>: {s.calls} calls · {s.graded ? `${s.correct}/${s.graded} correct (${s.hitRate}%)` : "none graded yet"}{s.unclear ? ` · ${s.unclear} unclear` : ""}{s.pending ? ` · ${s.pending} waiting for next batch` : ""}</p>)}
      <p>A small sample proves nothing; 50% is a coin flip.</p>
    </details> : null}
    {team?.research && (team.research.regimes?.state || team.research.anomalies?.state) ? <details><summary>Unsupervised research (nightly)</summary>
      <p>Learned without win/loss labels on earlier days, then graded on later days the models never saw. Research only: nothing here filters, sizes or exits a trade.</p>
      {team.research.regimes?.state === "ready" ? <>
        <p><b>Market regimes</b> (k-means on SPY/QQQ/breadth at entry, side-adjusted): held-out win rate {team.research.regimes.testWinRate}%.
          {team.research.regimes.persistence ? ` Best training regime ${team.research.regimes.persistence.held ? "stayed ahead" : "did not stay ahead"} of the worst by ${team.research.regimes.persistence.testSpread} points on later days.` : " Too few later trades per regime to judge."}</p>
        {team.research.regimes.clusters?.map(c => <p key={c.id}>Regime {c.id}: train {c.trainWinRate ?? "–"}% · later {c.testWinRate ?? "–"}% (n={c.testTrades}) · {Object.entries(c.center || {}).map(([k, v]) => `${k.replace(/^m_/, "")} ${v}`).join(", ")}</p>)}
      </> : <p><b>Market regimes:</b> {team.research.regimes?.state || "not trained yet"}.</p>}
      {team.research.anomalies?.state === "ready"
        ? <p><b>Unusual entries</b> (autoencoder neural network): {team.research.anomalies.unusualTrades} unusual later trades won {team.research.anomalies.unusualWinRate ?? "–"}% versus {team.research.anomalies.normalWinRate ?? "–"}% for normal ones. Main differences: {(team.research.anomalies.drivers || []).join(", ") || "–"}.</p>
        : <p><b>Unusual entries:</b> {team.research.anomalies?.state || "not trained yet"}.</p>}
    </details> : null}
    {team?.latest?.critic ? <details><summary>Latest agent discussion</summary>
      {team.latest.scanner ? <p><b>Scanner specialist ({team.latest.scanner.provider}):</b> {team.latest.scanner.summary}</p> : <p>This older discussion predates the scanner specialist.</p>}
      {team.latest.specialist ? <p><b>Additional {team.latest.specialistTask?.role} specialist:</b> {team.latest.specialistTask?.reason}. {team.latest.specialist.summary}</p> : null}
      {Object.entries(team.latest.specialists || {}).map(([role, result]) => <p key={role}><b>{role.replaceAll('_',' ')} specialist ({result.provider}):</b> {result.summary || result.reason || 'Unavailable'}</p>)}
      {team.latest.hermes ? <p><b>Independent Hermes researcher:</b> {team.latest.hermes.summary || team.latest.hermes.reason}</p> : null}
      <p><b>Review agent ({team.latest.reviewer.provider}):</b> {team.latest.reviewer.summary}</p>
      <p><b>Proposed experiment:</b> {team.latest.reviewer.hypothesis}</p>
      <p><b>Critic ({team.latest.critic.provider}):</b> {team.latest.critic.summary}</p>
      <p><b>Critic proposal:</b> {team.latest.critic.hypothesis}</p>
      <p><b>Required validation:</b> {team.latest.critic.validation}</p>
    </details> : <p>Completed trades will be discussed here. No agent proposal has been deployed.</p>}
    <p>Nightly models learn recorded outcomes. The experiment runner tests supported agent proposals; only qualified future-fill results can update Paper 5.</p>
    {team?.memoryError ? <p role="status">{team.memoryError}</p> : null}
    {team?.memory ? <details><summary>Learning memory · {team.memory.patterns?.length || 0} recurring loss patterns</summary>
      <p>Saved trade evidence survives restarts. The reviewer, critic and experiment planner receive the same memory snapshot. A loss alone is not a mistake; these patterns need investigation and do not automatically block entries.</p>
      <p>Latest {team.memory.windowEntryDays} captured entry days per source. {team.memory.capturedTrades} unique captured trades; {team.memory.excludedRows} records excluded for missing or invalid evidence. Views of the same virtual trade count once.</p>
      <p>{team.memory.basis} Patterns require at least six trades across four entry days and losses before fees in both chronological halves. This is a research screen, not proof of a trading edge.</p>
      {Object.entries(team.memory.scopes || {}).map(([scope, s]) => <p key={scope}>{scope === "virtual" ? "Virtual BOT (All trades / News trade / AI pick)" : scope === "paper3" ? "Paper 3" : "Paper 5"}: {s.trades} captured trades across {s.days} entry days{ s.throughDay ? ` · through ${s.throughDay}` : ""}.</p>)}
      {team.memory.patterns?.length ? <div className="bot-scroll"><table className="bot-table"><thead><tr><th>Source</th><th>Side / rule</th><th>Trades / days</th><th>Wins / losses</th><th>Earlier net</th><th>Later net</th><th>Next action</th></tr></thead><tbody>
        {team.memory.patterns.map(p => <tr key={`${p.scope}-${p.side}-${p.rule}`}><td>{p.scope}</td><td>{p.side} · {p.rule}</td><td>{p.trades} / {p.days}</td><td>{p.wins} / {p.losses}</td><td className="bot-num is-down">{PAPER_MONEY(p.earlierNet)}</td><td className="bot-num is-down">{PAPER_MONEY(p.laterNet)}</td><td>Investigate; test on unseen trades</td></tr>)}
      </tbody></table></div> : <p>No captured group meets the recurring-loss screen yet.</p>}
      <p>Prediction models are selected by unseen-data results. A better prediction score does not establish profitable trading. Agent discussions do not retrain ChatGPT or Ollama themselves.</p>
    </details> : null}
    <ExperimentStatus lab={team?.experiments} />
  </section>;
}

function PaperBook({ account, side, days, team, refreshKey }) {
  const [doc, setDoc] = useState(null);
  const [err, setErr] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let alive = true;
    let active;
    setDoc(null); setErr("");
    const load = () => {
      active?.abort();
      const controller = new AbortController();
      active = controller;
      return loadPaperHistory(account, days, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) })
        .then((d) => { if (alive && active === controller) { setDoc(d); setErr(""); } })
        .catch((e) => { if (alive && active === controller) setErr(e.name === "TimeoutError" ? "Paper history took too long to load. Retry to check the saved account records." : "Could not load the paper trades (" + e.message + ")."); });
    };
    load();
    const id = window.setInterval(load, 30000);
    return () => { alive = false; active?.abort(); window.clearInterval(id); };
  }, [account, days, refreshKey, retry]);
  if (err) return <div role="alert"><p className="bot-error">{err}</p><button type="button" className="bot-refresh" onClick={() => setRetry(n => n + 1)}>Retry paper history</button></div>;
  // Never show the previous tab's account while the new one loads.
  if (!doc || doc.account !== account) return <p className="bot-hint">Loading the {account} paper trades...</p>;
  if (doc.snapshotSource === "unavailable") return <div role="status"><p className="bot-hint">Broker reconciliation is busy and no saved account snapshot is available yet. Trade counts and P/L are unavailable.</p><button type="button" className="bot-refresh" onClick={() => setRetry(n => n + 1)}>Retry paper history</button></div>;
  const rows = (doc.history || [{ day: doc.day, rows: doc.rows || [] }])
    .flatMap((h) => (h.rows || []).map((r) => ({ ...r, day: h.day })))
    .filter((r) => r.side === side)
    .sort((a, b) => String(b.buyAt || "").localeCompare(String(a.buyAt || "")));
  const totals = paperTotals(rows);
  const { closed, wins, losses, cost } = totals;
  const pl = totals.realized;
  const picked = (doc.rules?.picked || {})[side] || [];
  const tone = (v) => (v > 0 ? " is-up" : v < 0 ? " is-down" : "");
  return (
    <>
      <h3>{account === "paper3" ? "Paper 3" : "Paper 5"} · {rows.length} orders · {side === "bull" ? "calls" : "puts"}</h3>
      {doc.reconciling ? <p className="bot-hint" role="status">Broker reconciliation in progress. Showing saved account records{doc.snapshotAt ? " · snapshot " + paperTime(doc.snapshotAt) + " ET" : ""}; quote freshness is checked separately.</p> : null}
      <details><summary>Account rules and entry policy · {doc.enabled ? "ON" : "OFF"}</summary>
      <p className="bot-jevbook-note">
        <b>Alpaca PAPER · {account}</b> · {doc.enabled ? "ON" : "OFF"} · real paper orders (no money) for the Bot&apos;s {doc.rules?.book}.
        {" "}{side === "bear" ? "BEAR (puts)" : "BULL (calls)"} rules: {picked.length ? picked.join(", ") : "every rule"}.
        {" "}When: {doc.rules?.entryTimes || "9:30 AM-3:30 PM ET"}. Size: {doc.rules?.sizing}. Exit: {doc.rules?.exitPlan}. {doc.error ? "Last error: " + doc.error : ""}
      </p>
      <p className="bot-hint" role="status">Entry policy: {account === "paper5" && team?.experiments?.activeId ? "validated experiment rule, then immediate entry" : "trade first under baseline rules"}. {doc.ai?.enabled ? "ON · experimental" : "OFF"}. {doc.ai?.reason || "Waiting for status"}. Entries never wait for an AI response. The review and critic agents analyze completed trades afterward. Existing risk limits and midpoint exits stay in force.</p>
      </details>
      <section className="bot-kpis" aria-label="Paper totals">
        <Kpi label="Realized P/L · paper" value={pl == null ? "Incomplete" : PAPER_MONEY(pl)} sub={pl == null ? "Missing fill evidence; total withheld" : "Sell fills and confirmed expirations · before fees"} className={tone(pl)} />
        <Kpi label="Closed ROI" value={formatBotPct(totals.roi, 1)} sub={cost == null ? "Filled cost unavailable" : "On $" + Math.round(cost).toLocaleString("en-US") + " actually filled · closed trades"} className={tone(totals.roi)} />
        <Kpi label="Open P/L · midpoint estimate" value={totals.open == null ? "Unpriced" : PAPER_MONEY(totals.open)} sub={totals.unpriced ? totals.unpriced + " positions need fresh quotes" : "Remaining contracts · not realized · before fees"} className={tone(totals.open)} />
        <Kpi label="Closed win rate" value={totals.unpricedClosed ? "Incomplete" : closed.length ? Math.round(wins / closed.length * 100) + "%" : "-"} sub={wins + " W · " + losses + " L" + (totals.unpricedClosed ? ` · ${totals.unpricedClosed} unpriced closed` : "")} />
        <Kpi label="Orders" value={String(rows.length)} sub={rows.filter((r) => r.buyFill != null).length + " filled · " + rows.filter((r) => r.status === "missed").length + " missed · " + rows.filter((r) => ["open", "selling", "exit_due"].includes(r.status)).length + " open"} />
      </section>
      <p className="bot-hint">Saved-session range includes carried positions. The Day column shows each order&apos;s original date; confirmed expirations may be reconciled later.</p>
      <PaperTable rows={rows} showDay={true} account={account} team={team} />
    </>
  );
}

// The shadow AI model's dashboard (2026-10-17, his "Where do I see the model
// score? Everyday?" -> "better dashboard ... do it now"). Score 0.50 = coin flip;
// it earns a paper account only at 0.55+ on days it never trained on, for weeks.
const AI_PCT = (v) => (v == null ? "-" : (v > 0 ? "+" : "") + Number(v).toFixed(1) + "%");
const AI_SCORE = (v) => (v == null ? "-" : Number(v).toFixed(3));

function AiScoreChart({ history, perDay }) {
  // Two or more nightly retrains: one point per night. Before that, the running
  // score on each test day already graded (2026-10-17, his "I don't see the
  // score chart") - the same walk-forward numbers, never a trained-on day.
  let pts = [...history].reverse().filter((h) => h.auc != null);
  let caption = "Score by nightly retrain";
  if (pts.length < 2) {
    pts = (perDay || []).filter((d) => d.aucToDate != null).map((d) => ({
      trainedAt: d.day + "T", auc: d.aucToDate,
      bySide: { bull: { auc: d.bullToDate }, bear: { auc: d.bearToDate } },
    }));
    caption = "Score by test day so far (running total, days it never trained on)";
  }
  if (pts.length < 2) {
    return <p className="bot-hint">The score chart starts after the second nightly retrain (Monday evening).</p>;
  }
  // Cleaner layout (2026-10-17, his "looks weird"): taller, zoomed to the
  // scores, a score scale on the left, value labels at each line's end and the
  // 0.50 / 0.55 marks named on the scale - nothing drawn on top of a line.
  const W = 340, H = 200, L = 34, R = 74, T = 26, B = 22;
  const vals = pts.flatMap((h) => [h.auc, h.bySide?.bull?.auc, h.bySide?.bear?.auc]).filter((v) => v != null);
  const lo = Math.min(0.45, Math.floor((Math.min(...vals) - 0.01) * 100) / 100);
  const hi = Math.max(0.60, Math.ceil((Math.max(...vals) + 0.01) * 100) / 100);
  const x = (k) => L + (k * (W - L - R)) / Math.max(1, pts.length - 1);
  const y = (v) => T + ((hi - v) / (hi - lo)) * (H - T - B);
  const series = [
    { key: "all", name: "all", color: "#e2e8f0", width: 2.2, get: (h) => h.auc },
    { key: "bull", name: "bull", color: "#22d3ee", width: 1.8, get: (h) => h.bySide?.bull?.auc },
    { key: "bear", name: "bear", color: "#e879f9", width: 1.8, get: (h) => h.bySide?.bear?.auc },
  ];
  const ticks = [];
  for (let v = Math.ceil(lo * 20) / 20; v <= hi + 1e-9; v += 0.05) ticks.push(Math.round(v * 100) / 100);
  // End labels, nudged apart so they never overlap.
  const ends = series.map((sr) => {
    const last = [...pts].reverse().find((h) => sr.get(h) != null);
    return { ...sr, v: last ? sr.get(last) : null };
  }).filter((e) => e.v != null).sort((a, b) => y(a.v) - y(b.v));
  let prev = -Infinity;
  for (const e of ends) {
    e.ly = Math.max(y(e.v), prev + 13);
    prev = e.ly;
  }
  const last = pts.length - 1;
  return (
    <>
    <p className="bot-hint">{caption}</p>
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: 560, display: "block" }} role="img" aria-label="AI score chart">
      {ticks.map((v) => (
        <g key={v}>
          <line x1={L} x2={W - R} y1={y(v)} y2={y(v)}
            stroke={v === 0.55 ? "#2fbf71" : "currentColor"} strokeOpacity={v === 0.55 ? 0.8 : v === 0.5 ? 0.45 : 0.12}
            strokeDasharray={v === 0.5 || v === 0.55 ? "4 3" : undefined} />
          <text x={L - 5} y={y(v) + 3} fontSize="9" textAnchor="end" fill={v === 0.55 ? "#2fbf71" : "currentColor"}
            opacity={v === 0.55 ? 1 : 0.6}>{v.toFixed(2)}</text>
        </g>
      ))}
      <text x={L} y={11} fontSize="9" fill="currentColor" opacity="0.6">0.50 = coin flip</text>
      <text x={L + 90} y={11} fontSize="9" fill="#2fbf71">0.55 = edge</text>
      {series.map((sr) => (
        <g key={sr.key}>
          <polyline points={pts.map((h, k) => (sr.get(h) == null ? null : `${x(k)},${y(sr.get(h))}`)).filter(Boolean).join(" ")}
            fill="none" stroke={sr.color} strokeWidth={sr.width} strokeLinejoin="round" strokeLinecap="round" />
          {pts.map((h, k) => (sr.get(h) == null ? null
            : <circle key={k} cx={x(k)} cy={y(sr.get(h))} r={sr.key === "all" ? 2.6 : 2} fill={sr.color} />))}
        </g>
      ))}
      {ends.map((e) => (
        <text key={e.key} x={x(last) + 7} y={e.ly + 3} fontSize="10" fill={e.color}>{e.name} {e.v.toFixed(3)}</text>
      ))}
      <text x={L} y={H - 6} fontSize="9" fill="currentColor" opacity="0.6">{String(pts[0].trainedAt || "").slice(5, 10)}</text>
      <text x={W - R} y={H - 6} fontSize="9" textAnchor="end" fill="currentColor" opacity="0.6">{String(pts[last].trainedAt || "").slice(5, 10)}</text>
    </svg>
    </>
  );
}

// Which model makes the live AI tags, and how close the neural net is to taking
// over by itself (2026-10-18, his "change deep learning model on your own" -> "Yes").
function AiChampion({ c, lr, nn }) {
  if (!c) return null;
  const isNN = c.champion === "nn";
  return (
    <>
      <p className="bot-hint">
        <b>Model in charge: {isNN ? "neural network (deep learning)" : "logistic regression"}</b> - it scores every live trade and the
        Setup-column AI tags. Tonight: logistic {AI_SCORE(lr)} vs neural net {AI_SCORE(nn)}.{" "}
        {isNN
          ? `The neural net stays in charge until it falls behind 3 nights in a row (now ${c.nnBehind || 0}/3).`
          : `The neural net takes over by itself after beating it by 0.01+ on unseen days 5 nights in a row (now ${c.nnAhead || 0}/5).`}
      </p>
      {(c.log || []).length ? (
        <div className="bot-scroll">
          <table className="bot-table">
            <thead><tr><th>When</th><th>Switch</th><th>Neural net</th><th>Logistic</th></tr></thead>
            <tbody>
              {[...c.log].reverse().map((e, i) => (
                <tr key={i}><td>{String(e.at || "").slice(5, 16).replace("T", " ")}</td><td>{e.event}</td>
                  <td className="bot-num">{AI_SCORE(e.nn)}</td><td className="bot-num">{AI_SCORE(e.lr)}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="bot-hint">No switch yet - every automatic switch will be listed here.</p>}
    </>
  );
}

// Whether the model uses the market picture - decided by the data each night
// (2026-10-18, his "no guess work"), never switched by hand.
function AiMarketView({ f }) {
  if (!f) return null;
  return (
    <p className="bot-hint">
      <b>Market view (SPY / QQQ / breadth): {f.useMarket ? "ON" : "OFF"}</b> - tested every night on unseen days:
      with it {AI_SCORE(f.withMarket)} vs without {AI_SCORE(f.withoutMarket)}.{" "}
      {f.useMarket
        ? `It switches off by itself after 2 nights behind (now ${f.behind || 0}/2).`
        : `It switches on by itself after 3 nights ahead by 0.005+ (now ${f.ahead || 0}/3).`}
      {(f.log || []).length ? " Last switch: " + f.log[f.log.length - 1].event + " (" + String(f.log[f.log.length - 1].at || "").slice(5, 10) + ")." : ""}
    </p>
  );
}

// The newest graded day's worst calls (2026-10-17, his "learn from the mistake
// and improve"): the model weights these up on its next nightly retrain.
function AiMistakes({ m }) {
  if (!m || !m.day) return null;
  const table = (title, rows) => (
    <>
      <p className="bot-hint"><b>{title}</b></p>
      <div className="bot-scroll">
        <table className="bot-table">
          <thead><tr><th>Side</th><th>Rule</th><th>Ticker</th><th>AI said</th><th>Result</th></tr></thead>
          <tbody>
            {rows && rows.length ? rows.map((r, i) => (
              <tr key={i}>
                <td>{r.side}</td><td>{r.rule}</td><td>{r.symbol}</td>
                <td className="bot-num">{Math.round(r.p * 100)}%</td>
                <td className={"bot-num" + (r.ret > 0 ? " is-up" : r.ret < 0 ? " is-down" : "")}>{(r.ret > 0 ? "+" : "") + r.ret}%</td>
              </tr>
            )) : <tr><td colSpan={5} className="bot-hint">None</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
  return (
    <>
      <p className="bot-hint">
        <b>Learning from mistakes · {m.day}</b> - the AI was wrong on {m.wrong} of {m.total} trades that day. Every night it weights
        its recent wrong calls 2x (and recent days more than old ones) when it retrains, and it also learns from each trade&apos;s
        Setup-column picture and its rule&apos;s Record (last 10 days).
      </p>
      {table("It said TAKE, but they lost", m.badTakes)}
      {table("It said SKIP, but they won", m.missedWinners)}
    </>
  );
}

function AiModelCard() {
  const [doc, setDoc] = useState(null);
  // Open by default (2026-10-17, his "I don't see" - the chart was behind a tap). Tap the line to fold it.
  const [open, setOpen] = useState(true);
  useEffect(() => {
    let alive = true;
    fetch("/api/momx-scanner/ai-report", { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).then((d) => { if (alive && d) setDoc(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const latest = doc && doc.latest;
  if (!latest || latest.auc == null) return null;
  const verdict = (v) => (v == null ? "-" : v >= 0.55 ? "edge" : v >= 0.52 ? "weak hint" : "coin flip");
  const sides = latest.bySide || {};
  const paper = doc.paper || {};
  return (
    <section className="bot-jevbook-note" aria-label="AI model score">
      <button type="button" className="bot-refresh" onClick={() => setOpen(!open)}>
        AI model (shadow) · score {AI_SCORE(latest.auc)} · {verdict(latest.auc)} · bull {AI_SCORE(sides.bull?.auc)} · bear {AI_SCORE(sides.bear?.auc)}
        {" "}· neural net {AI_SCORE(latest.nn?.auc)} · AI picks {AI_PCT(latest.aiPick?.roi)} vs all {AI_PCT(latest.all?.roi)} {open ? "▲" : "▼"}
      </button>
      {open ? (
        <div>
          <p className="bot-hint">
            Retrains every night after 4:40 PM ET on the newest {latest.trainDays} days of Bot trades ({latest.trainTrades?.toLocaleString("en-US")} trades),
            {" "}graded only on the last {latest.testDays} days, each scored by a model that never saw it. Score: 0.50 = coin flip, 0.52 = weak hint,
            0.55+ held for weeks suggests predictive value, not guaranteed profit. This model scores entries for comparison; it does not block paper entries. Review and critic agents analyze completed trades. Last trained {String(latest.trainedAt || "").replace("T", " ").slice(0, 16)} ET.
            {" "}A neural network (deep learning) is tested the same way every night as a challenger: it replaces the live model only if it
            scores higher on unseen days. Every finished trade is also saved to a permanent dataset (never deleted) so both keep learning from all days.
          </p>
          <AiScoreChart history={doc.history || []} perDay={latest.perDay || []} />
          <div className="bot-scroll">
            <table className="bot-table">
              <thead><tr><th>Side</th><th>Score</th><th>AI win %</th><th>AI ROI</th><th>All ROI</th></tr></thead>
              <tbody>
                {["bull", "bear"].map((sd) => {
                  const v = sides[sd] || {};
                  return (
                    <tr key={sd}>
                      <td>{sd === "bull" ? "BULL" : "BEAR"}</td>
                      <td className="bot-num" title={verdict(v.auc) + " - " + (v.aiPick?.trades ?? 0) + " AI picks"}>{AI_SCORE(v.auc)}</td>
                      <td className="bot-num">{v.aiPick?.winRate == null ? "-" : Math.round(v.aiPick.winRate) + "%"}</td>
                      <td className="bot-num">{AI_PCT(v.aiPick?.roi)}</td>
                      <td className="bot-num">{AI_PCT(v.all?.roi)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <AiChampion c={latest.champion} lr={latest.auc} nn={latest.nn?.auc} />
          <AiMarketView f={latest.features} />
          <AiMistakes m={latest.mistakes} />
          <p className="bot-hint"><b>AI vs your real paper fills</b> - every closed Paper 3 / Paper 5 trade, split by whether the AI scored it a pick (50%+).</p>
          <div className="bot-scroll">
            <table className="bot-table">
              <thead><tr><th>Account</th><th>AI take</th><th>AI skip</th><th>No score</th></tr></thead>
              <tbody>
                {Object.keys(paper).length ? Object.entries(paper).map(([acct, v]) => {
                  const cell = (c) => (c && c.trades ? `${c.trades} trades · ${Math.round(c.wins / c.trades * 100)}% win · ${c.pl < 0 ? "-$" : "$"}${Math.abs(c.pl).toFixed(0)}` : "-");
                  return <tr key={acct}><td>{acct}</td><td>{cell(v.aiPick)}</td><td>{cell(v.notPick)}</td><td>{cell(v.unscored)}</td></tr>;
                }) : <tr><td colSpan={4} className="bot-hint">No closed paper trades yet - they start Monday 9:30 AM ET.</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="bot-hint"><b>Every nightly retrain</b> (newest first)</p>
          <div className="bot-scroll">
            <table className="bot-table">
              <thead><tr><th>Trained</th><th>In charge</th><th>Score</th><th>Neural net</th><th>Bull</th><th>Bear</th><th>Days</th><th>AI picks win %</th><th>AI picks ROI</th><th>All ROI</th></tr></thead>
              <tbody>
                {(doc.history || []).map((h) => (
                  <tr key={h.trainedAt}>
                    <td>{String(h.trainedAt || "").slice(5, 16).replace("T", " ")}</td>
                    <td>{h.champion === "nn" ? "neural net" : "logistic"}</td>
                    <td className="bot-num">{AI_SCORE(h.auc)}</td>
                    <td className="bot-num">{AI_SCORE(h.nn?.auc)}</td>
                    <td className="bot-num">{AI_SCORE(h.bySide?.bull?.auc)}</td>
                    <td className="bot-num">{AI_SCORE(h.bySide?.bear?.auc)}</td>
                    <td className="bot-num">{h.trainDays}</td>
                    <td className="bot-num">{h.aiPick?.winRate == null ? "-" : Math.round(h.aiPick.winRate) + "%"}</td>
                    <td className="bot-num">{AI_PCT(h.aiPick?.roi)}</td>
                    <td className="bot-num">{AI_PCT(h.all?.roi)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function Kpi({ label, value, sub, className = "" }) {
  return (
    <div className={"bot-kpi" + className}>
      <span className="bot-kpi-label">{label}</span>
      <span className="bot-kpi-value">{value}</span>
      {sub ? <span className="bot-kpi-sub">{sub}</span> : null}
    </div>
  );
}

// A sortable header: click = high first, again = low first, again = no sort.
function SortTh({ k, sort, onSort, children, className = "", title }) {
  const on = sort.key === k;
  return (
    <th className={className + " bot-sortable" + (on ? " is-sorted" : "")} title={title || "Sort: high → low → off"}
      aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"} onClick={() => onSort(k)}>
      {children}<span className="bot-sort-arrow">{on ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
    </th>
  );
}

const LEADER_SORT = {
  rule: (r) => r.label, trades: (r) => r.trades, wins: (r) => r.wins, winRate: (r) => r.winRate, avgExit: (r) => r.avgExit,
  dollars: (r) => r.dollars, roi: (r) => r.roi, entryStrike: (r) => r.avgEntryStrike, zero: (r) => r.avgZero,
  max: (r) => r.avgMax, close: (r) => r.avgClose,
  avgWin: (r) => r.avgWin, avgLoss: (r) => r.avgLoss, best: (r) => r.best, worst: (r) => r.worst,
  daysUp: (r) => (r.daysTraded ? r.daysUp / r.daysTraded : null), perDay: (r) => r.perDay,
  pf: (r) => (r.profitFactor === Infinity ? 1e9 : r.profitFactor),
};
const TRADE_SORT = {
  at: (t) => String(t.at || t.day), entryAt: (t) => String(t.entryAt || t.at || ""), exitAt: (t) => String(t.exitAt || ""), rule: (t) => t.label, symbol: (t) => t.symbol, strike: (t) => Number(t.strike),
  entry: (t) => t.results.cost, now: (t) => t.results.sell, roi: (t) => t.results.exit,
  dollars: (t) => t.results.exitDollars, why: (t) => t.exitReason || "", eStrike: (t) => Number(t.eStrike),
  ePct: (t) => t.results.entryStrike, zStrike: (t) => Number(t.zStrike), zPct: (t) => t.results.zero,
  max: (t) => t.results.max, status: (t) => t.status || "",
};
const DAY_SORT = {
  day: (d) => d.day, trades: (d) => d.trades, wins: (d) => d.wins, winRate: (d) => d.winRate, avg: (d) => d.avgExit,
  dollars: (d) => d.dollars, roi: (d) => d.roi, best: (d) => (d.best ? d.best.label : ""), worst: (d) => (d.worst ? d.worst.label : ""),
};
// Matrix columns (periods / momentum states) sort their rows by ROI in that column.
const cellSort = (i) => (row) => (row.cells[i] && row.cells[i].priced ? row.cells[i].roi : null);

// "HH:MM" in ET from the bot's ISO stamps (they carry the ET offset).
function botClock(iso) {
  const s = String(iso || "");
  return s.length >= 16 ? s.slice(11, 16) : "-";
}

function statusOf(t) {
  if (t.badQuote) return "bad quote";
  if (t.status === "pending") return "pending";
  if (t.status === "no_contract") return "no contract";
  if (t.exitAt) return "exited";
  return t.status === "closed" ? "closed" : "open";
}

export default function BotPanel() {
  const [days, setDays] = useState(1);
  const [side, setSide] = useState("bull");
  const [levelStrategy, setLevelStrategy] = useState('all');
  // "Jev BOT" (2026-10-02): the whole page counts only the trades Jev would take.
  const [book, setBookState] = useState(() => {
    try { const b = window.localStorage.getItem("bot.book"); return ["news", "ai", "paper3", "paper5"].includes(b) ? b : "all"; } catch { return "all"; }
  });
  const [learningTeam, setLearningTeam] = useState(null);
  const paperMode = book === "paper3" || book === "paper5";
  const [paperRefresh, setPaperRefresh] = useState(0);
  const setBook = (b) => {
    setBookState(b);
    setRule(null);
    try { window.localStorage.setItem("bot.book", b); } catch { /* private window */ }
  };
  // Fills: Mid only (his "Remove all use only mid order", 2026-10-16) - he
  // trades with limit orders at the mid, so Real (ask->bid) and Pro were removed.
  const fills = "mid";
  setBotFills(fills);
  // Exit + contract (2026-10-10, after MRNA 10-09): which contract the numbers
  // follow and how it is sold. Defaults = the bot as before (next week, strategy exit).
  const [exitMode, setExitState] = useState("exit");
  const setExitMode = (v) => {
    setExitState(v);
    setOutcome("all");
    try { window.localStorage.setItem("bot.exit", v); } catch { /* private window */ }
  };
  const [contractMode, setContractState] = useState(() => {
    try { const v = window.localStorage.getItem("bot.contract"); return v === "zero" || v === "entry" ? v : "main"; } catch { return "main"; }
  });
  const setContractMode = (v) => {
    setContractState(v);
    try { window.localStorage.setItem("bot.contract", v); } catch { /* private window */ }
  };
  setBotExit(exitMode);
  setBotContract(contractMode);
  const pick = useCallback(
    (days) => (book === "news" ? botNewsBook(days)
      : book === "ai" ? botAiBook(days) : book === "all" && side === "bull" ? filterBotLevel(days, levelStrategy) : days),
    // fills / exit / contract are read inside botTradeResults; listing them here
    // re-runs every memo built on pick
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [book, side, fills, exitMode, contractMode, levelStrategy],
  );
  const [data, setData] = useState(null);
  const [loadedDays, setLoadedDays] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [rule, setRule] = useState(null);
  const [search, setSearch] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [showDetails, setShowDetails] = useState(false);
  const [rowLimit, setRowLimit] = useState(100);
  const [updated, setUpdated] = useState(null);
  const [history, setHistory] = useState(null);   // all kept days, for the period table
  const [tab, setTabState] = useState(readStoredTab);
  const [leaderSort, setLeaderSort] = useState({ key: null, dir: null });
  // "More trades AND more wins" (his ask 2026-09-30): hide rules with few trades.
  const [minTrades, setMinTradesState] = useState(() => {
    try { return Number(localStorage.getItem("bot.minTrades")) || 0; } catch { return 0; }
  });
  const setMinTrades = (n) => {
    setMinTradesState(n);
    try { localStorage.setItem("bot.minTrades", String(n)); } catch { /* private window */ }
  };
  const [tradeSort, setTradeSort] = useState({ key: null, dir: null });
  useEffect(() => { setRowLimit(100); }, [days, side, book, rule, search, outcome, tradeSort, levelStrategy]);
  const [daySort, setDaySort] = useState({ key: null, dir: null });
  const [periodSort, setPeriodSort] = useState({ key: null, dir: null });
  const [momSort, setMomSort] = useState({ key: null, dir: null });
  const cycle = (setter) => (key) => setter((s) => nextBotSort(s, key));
  const setTab = useCallback((key) => {
    setTabState(key);
    try { window.localStorage.setItem(BOT_TAB_KEY, key); } catch { /* private mode */ }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/momx-scanner/bot?days=180", { credentials: "include", cache: "no-store" });
      if (res.ok) setHistory(await res.json());
    } catch {
      // the main view shows the error; the period table just waits
    }
  }, []);
  useEffect(() => {
    if (paperMode || (tab !== "periods" && tab !== "days")) return;
    loadHistory();
    const id = window.setInterval(() => { if (marketOpenNow()) loadHistory(); }, HISTORY_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadHistory, tab, paperMode]);

  // Only the newest request may fill the page (2026-10-16: tapping Today then 1W
  // showed 1W lit over Today's numbers - the slower 1-day answer landed last).
  const loadSeq = useRef(0);
  const loadAbort = useRef(null);
  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    loadAbort.current?.abort();
    setLoading(true);
    setError("");
    const controller = new AbortController();
    loadAbort.current = controller;
    // Multi-day responses contain thousands of contracts; allow the compressed
    // history time to arrive while retaining a bounded, visible failure state.
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    try {
      const res = await fetch("/api/momx-scanner/bot?days=" + days, { credentials: "include", cache: "no-store", signal: controller.signal });
      if (res.status === 403) throw new Error("The Bot page is for admin accounts only.");
      if (!res.ok) throw new Error("Could not load the bot (HTTP " + res.status + ").");
      const body = await res.json();
      if (seq !== loadSeq.current) return;
      setData(body);
      setLoadedDays(days);
      setError("");
      setUpdated(new Date());
    } catch (err) {
      if (seq === loadSeq.current) setError(err?.name === "AbortError" ? "BOT data took too long to load." : String((err && err.message) || err));
    } finally {
      window.clearTimeout(timeout);
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (paperMode) { setLoading(false); return; }
    load();
    return () => { ++loadSeq.current; loadAbort.current?.abort(); };
  }, [load, paperMode]);
  useEffect(() => {
    if (paperMode) return;
    const id = window.setInterval(() => { if (marketOpenNow()) load(); }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load, paperMode]);

  const report = useMemo(() => botReport(pick(data && loadedDays === days ? data[side] : []), { side }), [data, loadedDays, days, side, pick]);
  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return report.rows.filter((r) => (!rule || r.rule === rule || (rule === "dayLineEarly" && isEarlyDayLine(r))) && (!q || r.symbol.includes(q))
      && (outcome === "all" || (outcome === "loss" && r.results.closed && r.results.exit < 0)
        || (outcome === "win" && r.results.closed && r.results.exit > 0)
        || (outcome === "open" && !r.results.closed && r.results.exit !== null)
        || (outcome === "unpriced" && r.results.exit === null)));
  }, [report, rule, search, outcome]);
  const { total, leaderboard } = report;
  // Totals for exactly the trades on screen (rule + ticker filters applied).
  const shownTotal = useMemo(() => botReport([{ day: "shown", trades: rows }]).total, [rows]);
  const best = leaderboard.find((r) => r.priced > 0) || null;
  const worst = [...leaderboard].reverse().find((r) => r.priced > 0) || null;
  const matrix = useMemo(() => botPeriodMatrix(pick(history ? history[side] : []), undefined, side), [history, side, pick]);
  const momentum = useMemo(() => botMomentumMatrix(report.rows), [report]);
  const combos = useMemo(() => botExitMatrix(report.rows, side, fills), [report, side, fills]);
  const badQuotes = useMemo(() => report.rows.filter((r) => r.badQuote).length, [report]);
  const dailyLog = useMemo(() => botDailyLog(pick(history ? history[side] : []), side), [history, side, pick]);
  const ruleLabel = rule ? (leaderboard.find((r) => r.rule === rule) || {}).label || rule : null;

  return (
    <div className="bot-page">
      <header className="bot-head">
        <div className="bot-title">
          <Bot size={18} aria-hidden="true" />
          <h2>BOT workspace</h2>
          <span className="bot-badge">MIDPOINT ONLY · PAPER</span>
          <span className="bot-badge is-admin">Admin only</span>
        </div>
        <div className="bot-controls">
          <div className="bot-seg" role="group" aria-label="Which bot">
            <button type="button" className={book === "all" ? "is-on" : ""} onClick={() => setBook("all")} title="Every trade the rules took">All trades</button>
            <button type="button" className={book === "news" ? "is-on is-jev" : ""} onClick={() => setBook("news")}
              title="The bot's trades on the scanner's NEWS ▲ (BULL) / NEWS ▼ (BEAR) list: scan matches whose news Jev reads bullish / bearish. Paper.">News trade</button>
            <button type="button" className={book === "ai" ? "is-on is-jev" : ""} onClick={() => setBook("ai")}
              title="Display filter for trades scored 55%+ by the local model. This score does not block entries; agents review completed trades afterward.">AI pick</button>
          </div>
          {/* Paper accounts as ONE drop-down (2026-10-17, his "Instead of panel use dropdown for paper 3/paper5"). */}
          {book === 'all' && side === 'bull' ? <label className="bot-level-select">
            <span>Level strategy</span>
            <select className="bot-paper-select" aria-label="Level strategy" title={BOT_LEVEL_HELP}
              value={levelStrategy} onChange={e => { setLevelStrategy(e.target.value); setRule(null); setOutcome('all'); }}>
              {BOT_LEVEL_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label> : null}
          <select className={"bot-paper-select" + (paperMode ? " is-on" : "")} aria-label="Alpaca paper account"
            value={paperMode ? book : ""} onChange={(e) => setBook(e.target.value || "all")}
            title="Alpaca PAPER: trade first using existing rules and midpoint limits. ChatGPT/Ollama agents review completed trades afterward.">
            <option value="">Paper trades ▾</option>
            <option value="paper3">Paper 3 · Trade first</option>
            <option value="paper5">Paper 5 · Trade first</option>
          </select>
          <div className="bot-seg" role="group" aria-label="Board">
            {["bull", "bear"].map((s) => (
              <button key={s} type="button" className={side === s ? "is-on" : ""} onClick={() => { setSide(s); setRule(null); }}>
                {s === "bull" ? "BULL · calls" : "BEAR · puts"}
              </button>
            ))}
          </div>
          {paperMode ? null : (<>
          <div className="bot-seg" role="group" aria-label="Contract" title="Which contract the numbers follow. Same-day and 0.20 delta are bought at the same signal as next week's.">
            {["main", "zero", "entry"].map((v) => (
              <button key={v} type="button" className={contractMode === v ? "is-on" : ""} onClick={() => setContractMode(v)}>
                {v === "main" ? "Contract: " : ""}{BOT_CONTRACT_LABELS[v]}
              </button>
            ))}
          </div>
          <div className="bot-seg bot-exit-options" role="group" aria-label="Exit comparison">
            {["exit", "close", "best"].map((v) => (
              <button key={v} type="button" className={exitMode === v ? "is-on" : ""} aria-pressed={exitMode === v} onClick={() => setExitMode(v)}>
                <span>{BOT_EXIT_LABELS[v]}</span>
                <small>{v === "exit" ? "First 5m close below VWAP or EMA21; otherwise 3:50 PM ET." : v === "close" ? "Hold until 3:50 PM ET, ignoring earlier exit signals." : "Highest midpoint including entry: losses appear as break-even. Hindsight only."}</small>
              </button>
            ))}
          </div>
          </>)}
          <div className="bot-seg" role="group" aria-label="Range">
            {RANGES.map((r) => (
              <button key={r.days} type="button" className={days === r.days ? "is-on" : ""} onClick={() => setDays(r.days)}>
                {r.label}
              </button>
            ))}
          </div>
          <button type="button" className="bot-refresh" onClick={paperMode ? () => setPaperRefresh(n => n + 1) : load} disabled={!paperMode && loading} title="Refresh">
            <RefreshCw size={14} aria-hidden="true" className={!paperMode && loading ? "is-spinning" : ""} />
            {paperMode ? "Refresh paper" : loading ? "Loading…" : updated ? updated.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" }) + " ET" : "Load"}
          </button>
        </div>
      </header>

      {book === 'all' && side === 'bull' ? <details className="bot-level-help">
        <summary>Level strategies · 3+ Setup types · MOM optional · AI review afterward</summary>
        <p>{BOT_LEVEL_HELP} Entries use the existing 09:30–15:30 ET window. New records start with valid future scanner updates.</p>
        <p><b>Setup column rules counted (23):</b> {SCANNER_SETUP_TYPES.map(([, label]) => label).join(' · ')}.</p>
        <p>Any three distinct types above satisfy the Setup requirement for each level. Multiple timeframes of one type count once. MOM is optional; these types include status and warning tags, so three types do not mean three independent bullish confirmations. Each new level trade shows its recorded Setup rules below its strategy name.</p>
        {learningTeam?.capture?.bull?.levelStrategies ? <p role="status">Level engine: {learningTeam.capture.bull.levelStrategies.state} · {learningTeam.capture.bull.levelStrategies.reason}</p> : null}
      </details> : null}
      {!paperMode ? <LearningTeamStatus onStatus={setLearningTeam} /> : null}
      {paperMode ? <><PaperBook account={book} side={side} days={days} team={learningTeam} refreshKey={paperRefresh} /><LearningTeamStatus onStatus={setLearningTeam} /></> : (<>
      {error ? <p className="bot-error" role="alert">{error} Use Refresh to retry.</p> : null}
      {loading ? <p className="bot-hint" role="status">Loading BOT trades…</p> : null}
      {exitMode !== "exit" || contractMode !== "main" ? (
        <p className="bot-jevbook-note">
          <b>{BOT_CONTRACT_LABELS[contractMode]} · {BOT_EXIT_LABELS[exitMode]}</b> · every win, loss, P/L and ROI below follows the
          {contractMode === "main" ? " next week contract" : contractMode === "zero" ? " same-day (0DTE) contract" : " 0.20-delta contract"}
          {exitMode === "exit" ? " sold at strategy exit." : exitMode === "close" ? " held to the 3:50 PM close." : " sold at the day's best price."}
          {exitMode === "close" ? " Close prices are recorded for every trade from Oct 12; a past day without one shows no result." : ""}
          {exitMode === "best" ? " Best recorded price is hindsight, not an executed exit. Quote gaps can miss the true peak. Use Strategy exit to review the strategy's wins and losses." : ""}
        </p>
      ) : null}
      {book === "news" ? (
        <p className="bot-jevbook-note">
          <b>News trade</b> · paper · the bot buys the scanner's {side === "bear" ? "NEWS ▼ list (puts)" : "NEWS ▲ list (calls)"}: a scan match
          whose News cell Jev reads as {side === "bear" ? "bearish" : "bullish"} - the same tickers as the scanner with Scan matches only ticked.
          Starts Oct 8, so earlier days are empty. Not proven yet - compare with All trades.
        </p>
      ) : null}
      {book === "ai" ? (
        <p className="bot-jevbook-note">
          <b>AI pick</b> · TEST · only the trades the shadow AI model scored 55% or more - every number below counts these alone.
          Scores start Oct 17, so earlier days are empty. Calls and puts both - see the AI model tab for each side's score. Not proven yet - compare with All trades.
        </p>
      ) : null}
      <p className="bot-hint">All trades · News trade: eligible signals enter immediately under existing rules. AI pick remains a score-based comparison view, not an entry gate. Configured AI agents review completed trades afterward; historical results remain unchanged.</p>
      <p className="bot-hint">Virtual P/L uses bid/ask midpoints, before fees. A midpoint estimate is not a confirmed fill. Paper orders use midpoint limits and may remain unfilled.</p>
      <section className="bot-kpis" aria-label="Totals">
        <Kpi label={"P/L · " + (exitMode === "exit" && contractMode === "main" ? "strategy exit" : BOT_CONTRACT_LABELS[contractMode] + " · " + BOT_EXIT_LABELS[exitMode])} value={total.priced ? formatBotDollars(total.dollars) : "-"} sub={exitMode === "best" ? "Hindsight only · 1 contract each · before fees" : "Closed " + formatBotDollars(total.realizedDollars) + " · open " + formatBotDollars(total.openDollars) + " · before fees"} className={tone(total.dollars)} />
        <Kpi label="ROI" value={formatBotPct(total.roi, 1)} sub={"on $" + Math.round(total.invested).toLocaleString("en-US") + " of contracts"} className={tone(total.roi)} />
        <Kpi label={exitMode === "best" ? "Hindsight gains / break-even" : "Closed wins / losses"} value={total.priced ? total.wins + " / " + (exitMode === "best" ? total.breakeven : total.losses) : "—"} sub={exitMode === "best" ? "Entry sets the floor at $0. Use Strategy exit for losses." : (total.winRate === null ? "No completed exits yet" : Math.round(total.winRate) + "% win rate") + " · " + total.breakeven + " break-even"} />
        <Kpi label="Avg trade" value={formatBotPct(total.avgExit)} sub={exitMode === "best" ? "hindsight comparison" : "includes open marked P/L"} className={tone(total.avgExit)} />
        <Kpi label="Trades" value={String(total.trades)}
          sub={report.days.length + (report.days.length === 1 ? " day" : " days") + " · " + total.completed + " closed with prices / " + total.openPriced + " open with marks (" + total.openWins + " gaining, " + total.openLosses + " losing) / " + total.unpriced + " unpriced"
            + (badQuotes ? " · " + badQuotes + " bad quote (not counted)" : "")} />
        <Kpi label="Best rule" value={best ? best.label : "-"} sub={best ? formatBotPct(best.avgExit) + " avg" : ""} className={best ? tone(best.avgExit) : ""} />
        <Kpi label="Worst rule" value={worst ? worst.label : "-"} sub={worst ? formatBotPct(worst.avgExit) + " avg" : ""} className={worst ? tone(worst.avgExit) : ""} />
      </section>

      <nav className="bot-tabs" role="tablist" aria-label="Bot views">
        {BOT_TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={"bot-tab" + (tab === t.key ? " is-on" : "")} onClick={() => setTab(t.key)}>
            {t.label}{t.key === "trades" && rule ? " · " + botRuleLabel(rule, side) : ""}
          </button>
        ))}
      </nav>

      {tab === "leaders" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Rule leaderboard</h3>
          <span className="bot-hint">Ranked by the selected exit, including open marks. Win/loss counts use completed results; Best price is hindsight. Click a rule to open its trades.</span>
          <div className="bot-seg bot-min-trades" role="group" aria-label="Minimum trades">
            <span className="bot-hint">Min trades</span>
            {[0, 20, 50, 100].map((n) => (
              <button key={n} type="button" className={minTrades === n ? "is-on" : ""} aria-pressed={minTrades === n}
                onClick={() => setMinTrades(n)}>{n === 0 ? "All" : n + "+"}</button>
            ))}
            <button type="button" title="Rules with 50+ trades, best win rate first"
              className={minTrades === 50 && leaderSort.key === "winRate" && leaderSort.dir === "desc" ? "is-on" : ""}
              onClick={() => { setMinTrades(50); setLeaderSort({ key: "winRate", dir: "desc" }); }}>Most trades + best win %</button>
          </div>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="rule" sort={leaderSort} onSort={cycle(setLeaderSort)} className="is-left">Rule</SortTh>
                <SortTh k="trades" sort={leaderSort} onSort={cycle(setLeaderSort)}>Trades</SortTh>
                <SortTh k="wins" sort={leaderSort} onSort={cycle(setLeaderSort)}>W / L / BE</SortTh>
                <SortTh k="winRate" sort={leaderSort} onSort={cycle(setLeaderSort)}>Win %</SortTh>
                <SortTh k="avgExit" sort={leaderSort} onSort={cycle(setLeaderSort)}>Strategy exit</SortTh>
                <SortTh k="dollars" sort={leaderSort} onSort={cycle(setLeaderSort)}>$ P/L</SortTh>
                <SortTh k="roi" sort={leaderSort} onSort={cycle(setLeaderSort)}>ROI</SortTh>
                <SortTh k="pf" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Profit factor: $ won ÷ $ lost. Above 1 = made money; 2 = won twice what it lost.">Profit factor</SortTh>
                <SortTh k="avgWin" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average % of the winning trades">Avg win</SortTh>
                <SortTh k="avgLoss" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average % of the losing trades">Avg loss</SortTh>
                <SortTh k="best" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Best single trade at strategy exit">Best</SortTh>
                <SortTh k="worst" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Worst single trade at strategy exit">Worst</SortTh>
                <SortTh k="daysUp" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Days it made money / days it traded">Days +</SortTh>
                <SortTh k="perDay" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average trades a day">Trades / day</SortTh>
                <SortTh k="entryStrike" sort={leaderSort} onSort={cycle(setLeaderSort)}>ENTRY strike</SortTh>
                <SortTh k="zero" sort={leaderSort} onSort={cycle(setLeaderSort)}>0DTE</SortTh>
                <SortTh k="max" sort={leaderSort} onSort={cycle(setLeaderSort)}>Hold max</SortTh>
                <SortTh k="close" sort={leaderSort} onSort={cycle(setLeaderSort)}>Day close</SortTh>
              </tr>
            </thead>
            <tbody>
              {leaderboard.filter((r) => r.trades >= minTrades).length === 0 ? (
                <tr><td colSpan={18} className="bot-empty">No trades in this range yet. The bot trades 09:30-15:30 ET.</td></tr>
              ) : sortBotRows(leaderboard.filter((r) => r.trades >= minTrades), leaderSort, LEADER_SORT).map((r, i) => (
                <tr key={r.rule} className={"is-click" + (rule === r.rule ? " is-selected" : "")} onClick={() => { setRule(r.rule); setTab("trades"); }}>
                  <td className="is-left" title={BOT_RULE_HELP[r.rule] ? BOT_RULE_HELP[r.rule].what + " Needs: " + BOT_RULE_HELP[r.rule].needs : ""}><span className="bot-rank">{i + 1}</span><b>{r.label}</b>{r.test ? <span className="bot-chip">test</span> : null}</td>
                  <td>{r.trades}</td>
                  <td>{r.wins} / {r.losses} / {r.breakeven}</td>
                  <td>{r.winRate === null ? "-" : Math.round(r.winRate) + "%"}</td>
                  <td className={"bot-num" + tone(r.avgExit)}>{formatBotPct(r.avgExit)}</td>
                  <td className={"bot-num" + tone(r.dollars)}>{formatBotDollars(r.dollars)}</td>
                  <td className={"bot-num" + tone(r.roi)} title={"$" + Math.round(r.invested) + " of contracts"}>{formatBotPct(r.roi, 1)}</td>
                  <td className={"bot-num" + tone(r.profitFactor === null ? null : r.profitFactor - 1)}>{r.profitFactor === null ? "-" : r.profitFactor === Infinity ? "∞" : r.profitFactor.toFixed(2)}</td>
                  <td className={"bot-num" + tone(r.avgWin)}>{formatBotPct(r.avgWin)}</td>
                  <td className={"bot-num" + tone(r.avgLoss)}>{formatBotPct(r.avgLoss)}</td>
                  <td className={"bot-num" + tone(r.best)}>{formatBotPct(r.best)}</td>
                  <td className={"bot-num" + tone(r.worst)}>{formatBotPct(r.worst)}</td>
                  <td>{r.daysTraded ? r.daysUp + " / " + r.daysTraded : "-"}</td>
                  <td>{r.perDay === null ? "-" : r.perDay.toFixed(1)}</td>
                  <td className={"bot-num" + tone(r.avgEntryStrike)}>{r.entryStrikeCount ? formatBotPct(r.avgEntryStrike) : "-"}</td>
                  <td className={"bot-num" + tone(r.avgZero)}>{r.zeroCount ? formatBotPct(r.avgZero) : "-"}</td>
                  <td className={"bot-num" + tone(r.avgMax)}>{formatBotPct(r.avgMax)}</td>
                  <td className={"bot-num" + tone(r.avgClose)}>{formatBotPct(r.avgClose)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "periods" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Winners by period</h3>
          <span className="bot-hint">Each cell: wins - losses · average % at strategy exit · ROI (profit ÷ contract cost). Highlighted = the best rule in that period. Periods count trading days with bot data.</span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table bot-matrix">
            <thead>
              <tr>
                <th className="is-left">Rule</th>
                {matrix.periods.map((p, i) => (
                  <SortTh key={p.key} k={"c" + i} sort={periodSort} onSort={cycle(setPeriodSort)}
                    title={p.days + (p.days === 1 ? " trading day" : " trading days") + " - click to sort rules by ROI"}>
                    {p.key === "All" ? "All (" + p.days + "d)" : p.key}
                  </SortTh>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.length === 0 ? (
                <tr><td colSpan={matrix.periods.length + 1} className="bot-empty">No history yet.</td></tr>
              ) : sortBotRows(matrix.rows, periodSort, Object.fromEntries(matrix.periods.map((_, i) => ["c" + i, cellSort(i)]))).map((row) => (
                <tr key={row.rule} className="is-click" onClick={() => { setRule(row.rule); setTab("trades"); }}>
                  <td className="is-left"><b>{row.label}</b>{row.test ? <span className="bot-chip">test</span> : null}</td>
                  {row.cells.map((c, i) => (
                    <td key={matrix.periods[i].key} className={"bot-cell" + (c && matrix.periods[i].best === row.rule ? " is-best" : "")}>
                      {c && c.priced ? (
                        <span>
                          <span className="bot-wl">{c.wins}-{c.losses}</span>
                          <span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span>
                          <span className={"bot-roi" + tone(c.roi)}>ROI {formatBotPct(c.roi, 0)}</span>
                        </span>
                      ) : c ? <span className="bot-dim">{c.trades} open</span> : <span className="bot-dim">-</span>}
                    </td>
                  ))}
                </tr>
              ))}
              {matrix.rows.length ? (
                <tr className="bot-total-row">
                  <td className="is-left"><b>All rules</b></td>
                  {matrix.periods.map((p) => (
                    <td key={p.key} className="bot-cell">
                      <span className="bot-wl">{p.total.wins}-{p.total.losses}</span>
                      <span className={"bot-num" + tone(p.total.avgExit)}> {formatBotPct(p.total.avgExit)}</span>
                      <div className={"bot-num" + tone(p.total.dollars)}>{formatBotDollars(p.total.dollars)}</div>
                      <div className={"bot-roi" + tone(p.total.roi)}>ROI {formatBotPct(p.total.roi, 1)}</div>
                    </td>
                  ))}
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "combos" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Which contract, which exit</h3>
          <span className="bot-hint">Every rule&apos;s trades re-scored 9 ways at Mid prices. Each cell: wins - losses · win rate · ROI. Highlighted = that rule&apos;s best ROI. Hold to close counts trades with a recorded close (every trade from Oct 12). Best price is hindsight (no losses possible).</span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table bot-matrix">
            <thead>
              <tr>
                <th className="is-left" rowSpan={2}>Rule</th>
                {["main", "zero", "entry"].map((c) => <th key={c} colSpan={3}>{BOT_CONTRACT_LABELS[c]}</th>)}
              </tr>
              <tr>
                {combos.combos.map((c) => <th key={c.key}>{BOT_EXIT_LABELS[c.exit]}</th>)}
              </tr>
            </thead>
            <tbody>
              {combos.rows.length === 0 ? (
                <tr><td colSpan={10} className="bot-empty">No trades in this range.</td></tr>
              ) : [...combos.rows, { rule: "__all", label: "All rules", cells: combos.total }].map((row) => {
                const rois = row.cells.map((c) => (c.priced ? c.roi : null)).filter((v) => v !== null);
                const top = rois.length ? Math.max(...rois) : null;
                return (
                  <tr key={row.rule} className={row.rule === "__all" ? "bot-total-row" : "is-click"}
                    onClick={row.rule === "__all" ? undefined : () => { setRule(row.rule); setTab("trades"); }}>
                    <td className="is-left"><b>{row.label}</b>{row.test ? <span className="bot-chip">test</span> : null}</td>
                    {row.cells.map((c, i) => (
                      <td key={combos.combos[i].key} className={"bot-cell" + (c.priced && c.roi !== null && c.roi === top ? " is-best" : "")}>
                        {c.priced ? (
                          <span>
                            <span className="bot-wl">{c.wins}-{c.losses}</span>
                            <span className="bot-num"> {Math.round(c.winRate)}%</span>
                            <span className={"bot-roi" + tone(c.roi)}>ROI {formatBotPct(c.roi, 0)}</span>
                          </span>
                        ) : <span className="bot-dim">-</span>}
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "momentum" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Momentum at entry</h3>
          <span className="bot-hint">
            The 5-minute momentum when the bot entered, for the range above. Each cell: wins - losses · average % at strategy exit.
            {momentum.unknown ? " " + momentum.unknown + " older trades have no state recorded (before 09-28 16:10)." : ""}
          </span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table bot-matrix">
            <thead>
              <tr>
                <th className="is-left">Rule</th>
                {momentum.states.map((s, i) => (
                  <SortTh key={s} k={"c" + i} sort={momSort} onSort={cycle(setMomSort)} title="Click to sort rules by ROI in this state">
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </SortTh>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortBotRows(momentum.rows, momSort, Object.fromEntries(momentum.states.map((_, i) => ["c" + i, cellSort(i)]))).map((row) => {
                const priced = row.cells.map((c) => (c && c.priced ? c.avgExit : null));
                const bestIdx = priced.reduce((bi, v, i) => (v !== null && (bi < 0 || v > priced[bi]) ? i : bi), -1);
                return (
                  <tr key={row.rule}>
                    <td className="is-left"><b>{row.label}</b>{row.test ? <span className="bot-chip">test</span> : null}</td>
                    {row.cells.map((c, i) => (
                      <td key={momentum.states[i]} className={"bot-cell" + (i === bestIdx && row.cells.filter((x) => x && x.priced).length > 1 ? " is-best" : "")}>
                        {c && c.priced ? (
                          <span><span className="bot-wl">{c.wins}-{c.losses}</span><span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span></span>
                        ) : c ? <span className="bot-dim">{c.trades} open</span> : <span className="bot-dim">-</span>}
                      </td>
                    ))}
                  </tr>
                );
              })}
              {momentum.rows.length ? (
                <tr className="bot-total-row">
                  <td className="is-left"><b>All rules</b></td>
                  {momentum.totals.map((c, i) => (
                    <td key={momentum.states[i]} className="bot-cell">
                      {c && c.priced ? (
                        <span><span className="bot-wl">{c.wins}-{c.losses}</span><span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span></span>
                      ) : <span className="bot-dim">-</span>}
                    </td>
                  ))}
                </tr>
              ) : (
                <tr><td colSpan={6} className="bot-empty">No trades in this range yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "days" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Day by day</h3>
          <span className="bot-hint">Every trading day the bot ran (kept 180 days).</span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="day" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Day</SortTh>
                <SortTh k="trades" sort={daySort} onSort={cycle(setDaySort)}>Trades</SortTh>
                <SortTh k="wins" sort={daySort} onSort={cycle(setDaySort)}>W / L / BE</SortTh>
                <SortTh k="winRate" sort={daySort} onSort={cycle(setDaySort)}>Win %</SortTh>
                <SortTh k="avg" sort={daySort} onSort={cycle(setDaySort)}>Avg</SortTh>
                <SortTh k="dollars" sort={daySort} onSort={cycle(setDaySort)}>$ P/L</SortTh>
                <SortTh k="roi" sort={daySort} onSort={cycle(setDaySort)}>ROI</SortTh>
                <SortTh k="best" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Best rule</SortTh>
                <SortTh k="worst" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Worst rule</SortTh>
              </tr>
            </thead>
            <tbody>
              {dailyLog.length === 0 ? (
                <tr><td colSpan={9} className="bot-empty">No history yet.</td></tr>
              ) : sortBotRows(dailyLog, daySort, DAY_SORT).map((d) => (
                <tr key={d.day}>
                  <td className="is-left bot-mono">{d.day}</td>
                  <td>{d.trades}</td>
                  <td>{d.wins} / {d.losses} / {d.breakeven}</td>
                  <td>{d.winRate === null ? "-" : Math.round(d.winRate) + "%"}</td>
                  <td className={"bot-num" + tone(d.avgExit)}>{formatBotPct(d.avgExit)}</td>
                  <td className={"bot-num" + tone(d.dollars)}>{formatBotDollars(d.dollars)}</td>
                  <td className={"bot-num" + tone(d.roi)}>{formatBotPct(d.roi, 1)}</td>
                  <td className="is-left">{d.best ? d.best.label + " " + formatBotPct(d.best.avgExit) : "-"}</td>
                  <td className="is-left">{d.worst ? d.worst.label + " " + formatBotPct(d.worst.avgExit) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "trades" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Trades{ruleLabel ? " · " + ruleLabel : ""}</h3>
          {rule ? (
            <button type="button" className="bot-clear" onClick={() => setRule(null)}>
              <X size={12} aria-hidden="true" /> all rules
            </button>
          ) : null}
          <div className="bot-seg" role="group" aria-label="Trade outcome">
            {[["all", "All outcomes"], ["loss", exitMode === "best" ? "Losses" : "Closed losses"], ["win", exitMode === "best" ? "Wins" : "Closed wins"], ...(exitMode === "best" ? [] : [["open", "Open P/L"]]), ["unpriced", "Unpriced"]].map(([key, label]) => (
              <button key={key} type="button" className={outcome === key ? "is-on" : ""} aria-pressed={outcome === key} onClick={() => setOutcome(key)}>{label}</button>
            ))}
          </div>
          <label className="bot-hint"><input type="checkbox" checked={showDetails} onChange={(e) => setShowDetails(e.target.checked)} /> Compare contracts</label>
          <input
            className="bot-search"
            placeholder="Ticker"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Filter by ticker"
          />
          <span className="bot-hint">
            {rows.length} shown
            {shownTotal.priced ? (
              <>
                {exitMode === "best" ? " · hindsight " : " · closed "}{shownTotal.wins}W {shownTotal.losses}L {shownTotal.breakeven}BE{" · "}
                <b className={"bot-num" + tone(shownTotal.dollars)}>{formatBotDollars(shownTotal.dollars)}</b>
                {exitMode === "best" ? "" : " (includes " + formatBotDollars(shownTotal.openDollars) + " open)"}
                {" on $" + Math.round(shownTotal.invested).toLocaleString("en-US") + " · ROI "}
                <b className={"bot-num" + tone(shownTotal.roi)}>{formatBotPct(shownTotal.roi, 1)}</b>
              </>
            ) : null}
          </span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="at" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left" title="When the rule fired (ET)">Signal</SortTh>
                <SortTh k="rule" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Rule</SortTh>
                <SortTh k="symbol" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Ticker</SortTh>
                <th title="Frozen local-model estimate made before this virtual entry. Main option, Strategy exit, before fees. Experimental probability, not a guarantee.">AI at entry</th>
                <SortTh k="strike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Option</SortTh>
                <SortTh k="entry" sort={tradeSort} onSort={cycle(setTradeSort)}>Entry</SortTh>
                <SortTh k="entryAt" sort={tradeSort} onSort={cycle(setTradeSort)} title="When the option was bought (ET) - the first price after the signal">Entry time</SortTh>
                <SortTh k="now" sort={tradeSort} onSort={cycle(setTradeSort)}>Exit / now</SortTh>
                <SortTh k="exitAt" sort={tradeSort} onSort={cycle(setTradeSort)} title={exitMode === "exit" ? "Strategy exit signal time (ET)" : exitMode === "close" ? "Scheduled close time; not the earlier strategy signal" : "Hindsight comparison; no executed exit time"}>Exit time</SortTh>
                <SortTh k="roi" sort={tradeSort} onSort={cycle(setTradeSort)} title={exitMode === "exit" && contractMode === "main"
                  ? "Profit ÷ what the contract cost, at strategy exit (first 5m close below VWAP / EMA21, else 15:50). Click to sort."
                  : "Profit ÷ what the contract cost, for the " + BOT_CONTRACT_LABELS[contractMode] + " contract sold at " + BOT_EXIT_LABELS[exitMode] + ". The Option / Entry / Exit columns follow the selected contract and fill mode. Click to sort."}>
                  {exitMode === "exit" && contractMode === "main" ? "ROI (strategy exit)" : "ROI (" + BOT_CONTRACT_LABELS[contractMode] + " · " + BOT_EXIT_LABELS[exitMode] + ")"}</SortTh>
                <SortTh k="dollars" sort={tradeSort} onSort={cycle(setTradeSort)}>$</SortTh>
                <SortTh k="why" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Why out</SortTh>
                {showDetails ? <>
                <SortTh k="eStrike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">ENTRY</SortTh>
                <SortTh k="ePct" sort={tradeSort} onSort={cycle(setTradeSort)}>ENTRY %</SortTh>
                <SortTh k="zStrike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">0DTE</SortTh>
                <SortTh k="zPct" sort={tradeSort} onSort={cycle(setTradeSort)}>0DTE %</SortTh>
                <SortTh k="max" sort={tradeSort} onSort={cycle(setTradeSort)}>Hold max</SortTh>
                </> : null}
                <SortTh k="status" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Status</SortTh>
                <th className="is-left">Post-trade review</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={showDetails ? 19 : 14} className="bot-empty">{loading ? "Loading trades…" : error ? "Trade data unavailable — retry above." : "No trades match this view. Try a longer range or clear the ticker, rule and outcome filters."}</td></tr>
              ) : sortBotRows(rows, tradeSort, TRADE_SORT).slice(0, rowLimit).map((t) => {
                const out = t.results.sell;
                const forecast = t.aiDecision?.prediction;
                const advance = forecast?.phase === "before_entry" && typeof forecast.probability === "number" && Number.isFinite(forecast.probability) && forecast.probability >= 0 && forecast.probability <= 1;
                const selectedOption = contractMode === "entry" ? botContractLabel({ ...t, strike: t.eStrike, contract: t.eContract }) : botContractLabel(t, contractMode === "zero");
                return (
                  <tr key={t.day + "|" + t.rule + "|" + t.symbol + "|" + t.at}>
                    <td className="is-left bot-mono">{days > 1 ? t.day.slice(5) + " " : ""}{String(t.at || "").slice(11, 16)}</td>
                    <td className="is-left" title={t.levelSignal ? `${t.levelSignal.setupCount} Setup types at signal: ${(t.levelSignal.setupTypes || []).map(s => s.text).join(', ')}. Highest confirmation L${t.levelSignal.highestLevel}; separate strategy L${t.levelSignal.strategyLevel}.` : undefined}>{t.label}{t.test ? <span className="bot-chip">test</span> : null}{t.levelSignal ? <small className="bot-level-setup-rules">Setup ({t.levelSignal.setupCount}): {(t.levelSignal.setupTypes || []).map(s => s.text).join(' · ') || 'Not recorded'}</small> : null}</td>
                    <td className="is-left"><b>{t.symbol}</b>{botRiskOk(t) === null ? null : <span className={"bot-chip" + (botRiskOk(t) ? " is-jev" : "")} title={"At 2x the expected move this option returns " + formatBotPct(botRiskRoi(t) * 100) + " - his rule is +100% or more. The bot still takes the trade."}>{botRiskOk(t) ? "✓ 2×EM" : "✗ 2×EM"}</span>}</td>
                    <td title={advance ? `${forecast.family} · ${forecast.scoredAt} · ${forecast.target}. Uses saved setup, RVOL, squeeze and Skittles when available. Other contract/exit comparisons have no separate prediction.` : forecast?.reason || "No prediction was saved before entry; historical scores are not substituted."}>{advance ? `${Math.round(forecast.probability * 100)}% · main / strategy` : "Not recorded"}</td>
                    <td className="is-left bot-mono">{selectedOption}</td>
                    <td className="bot-mono">{t.results.cost == null ? "-" : Number(t.results.cost).toFixed(4).replace(/0{1,2}$/, "")}</td>
                    <td className="bot-mono">{botClock(t.entryAt || t.at)}</td>
                    <td className="bot-mono">{out === undefined || out === null ? "-" : Number(out).toFixed(4).replace(/0{1,2}$/, "")}</td>
                    <td className="bot-mono">{exitMode === "best" ? "hindsight" : exitMode === "close" ? (t.results.closed ? "15:50 ET close" : "holding") : t.exitAt ? botClock(t.exitAt) : <span className="bot-dim">{t.results.closed ? "-" : "holding"}</span>}</td>
                    <td className={"bot-num" + tone(t.results.exit)}>{formatBotPct(t.results.exit)}</td>
                    <td className={"bot-num" + tone(t.results.exitDollars)}>{formatBotDollars(t.results.exitDollars)}</td>
                    <td className="is-left bot-dim">{exitMode === "best" ? "best recorded midpoint" : exitMode === "close" ? (t.results.closed ? "session close" : "holding to 15:50 ET") : t.exitReason || (t.status === "open" ? "holding" : "")}</td>
                    {showDetails ? <>
                    <td className="is-left bot-mono bot-dim">{botEntryStrikeLabel(t)}</td>
                    <td className={"bot-num" + tone(t.results.entryStrike)}>{formatBotPct(t.results.entryStrike)}</td>
                    <td className="is-left bot-mono bot-dim">{botContractLabel(t, true)}</td>
                    <td className={"bot-num" + tone(t.results.zero)}>{formatBotPct(t.results.zero)}</td>
                    <td className={"bot-num" + tone(t.results.max)}>{formatBotPct(t.results.max)}{!t.holdDone && t.entry !== undefined ? "…" : ""}</td>
                    </> : null}
                    <td className="is-left"><span className={"bot-status is-" + statusOf(t).replace(" ", "-")}>{t.status === "ai_skipped" ? "AI skipped" : t.status === "pending" && t.aiDecision ? "AI pending" : t.results.exit === null ? "unpriced" : exitMode === "best" ? "hindsight" : !t.results.closed ? "open" : t.results.exit > 0 ? "win" : t.results.exit < 0 ? "loss" : "break-even"}</span></td>
                    <td className="is-left" title="Batch review of the original strategy outcome, independent of the exit comparison selected above.">{reviewLabel(learningTeam, `${side}|${t.rule}|${t.symbol}|${t.at}`, t)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "trades" && rows.length > rowLimit ? (
        <button type="button" className="bot-refresh" onClick={() => setRowLimit((n) => n + 100)}>
          Show 100 more · {Math.min(rowLimit, rows.length)} of {rows.length} displayed
        </button>
      ) : null}

      {tab === "ai" ? <AiModelCard /> : null}

      {tab === "rules" ? (
      <section className="bot-card bot-help">
        <div className="bot-card-head"><h3>Rules explained</h3><span className="bot-hint">what each rule needs - so you can see what a missed trade lacked</span></div>
        <div className="bot-help-list">
          {BOT_RULE_ORDER.map((rule) => {
            const h = BOT_RULE_HELP[rule];
            if (!h) return null;
            return (
              <div key={rule} className="bot-help-item">
                <div className="bot-help-name"><b>{BOT_RULE_LABELS[rule]}</b>{BOT_TEST_RULES.has(rule) ? <span className="bot-chip">test</span> : null}</div>
                <div>{h.what}</div>
                <div className="bot-dim"><b>Needs:</b> {h.needs}</div>
                <div className="bot-dim"><b>Back-test:</b> {h.tested}</div>
              </div>
            );
          })}
        </div>
      </section>
      ) : null}

      <p className="bot-foot">
        <b>How it trades:</b> at each GO+, GO, ⭐1, MX A+, Daily 2 and Market-turn signal (and, as a <i>test</i>, the first 15 a day of
        C2H/C4H, CALL2H/4H, ADX, SKIT, RVOL, SQZ fire and your ZS rule - above EMA 9/21/50, VWAP and the 5m cloud with no level within 2% above) it takes the nearest weekly first out-of-the-money option at the mid price, 09:30-15:30 ET.
        <b> Strategy exit</b> = the first 5-minute close below VWAP or the 21 EMA, else 15:50. <b>ENTRY strike</b> = the chain's ENTRY strike (the out-of-the-money option with delta just under 0.20, same expiry, same exit). <b>0DTE</b> = the same signal on a same-day option.
        <b> Hold max</b> = the weekly's best price until it expires (… = still being tracked). Midpoint estimates; actual fills may differ or remain unfilled.
        Paper tracking of your rules, not advice. Kept {data && data.keepDays ? data.keepDays : 180} days.
      </p>
      </>)}
    </div>
  );
}
