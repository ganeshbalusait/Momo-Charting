// Paper 5 rule approval (2026-11-05, his "Yes, add the check"): a rule that
// passed the forward test AND the statistics check waits for his Approve before
// Paper 5 uses it. Plain-language lines for the BOT page box.

export const EXPERIMENT_DECISION_URL = "/api/momx-scanner/paper-experiment-decision";

export function experimentRuleText(spec) {
  if (spec?.kind === "spread_ceiling") return `Only take entries with a spread no wider than ${Math.round(spec.value * 100)}% of the price`;
  if (spec?.kind === "premium_floor") return `Only take options priced at least $${Number(spec.value).toFixed(2)}`;
  if (spec?.kind === "entry_after") {
    const h = String(Math.floor(spec.value / 60)).padStart(2, "0");
    const m = String(spec.value % 60).padStart(2, "0");
    return `Only take entries from ${h}:${m} ET`;
  }
  return "Unknown rule";
}

/** The lines shown above the Approve / Reject buttons, or null when nothing waits. */
export function awaitingLines(awaiting) {
  if (!awaiting || !awaiting.id) return null;
  const check = awaiting.statsCheck || {};
  const ev = awaiting.evaluation || {};
  const money = (v) => (typeof v === "number" && Number.isFinite(v) ? (v < 0 ? "−$" : "$") + Math.abs(Math.round(v)).toLocaleString("en-US") : "-");
  return {
    title: "A new Paper 5 rule is waiting for your OK",
    rule: experimentRuleText(awaiting.screen?.spec),
    test: `Forward test on real Paper 3 fills: ${ev.trades ?? "-"} trades over ${ev.observedDays ?? "-"} days · with the rule ${money(ev.candidateNet)} vs ${money(ev.baselineNet)} without it.`,
    stats: check.passed
      ? `Statistics check passed: still better after counting all ${check.trials} rules the planner has tried (worst-case daily gain ${money(check.lower)}).`
      : `Statistics check did not pass: ${check.reason || "not enough evidence"}.`,
    note: "Paper 5 stays on its current rules until you approve. Approving changes only which Paper 5 paper entries are taken; exits and live money are not affected.",
  };
}
