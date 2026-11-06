// Rule lab on the BOT page (2026-11-05, his "Setup column all rule have AI
// agentic also, it will improve itself"): one line per Setup rule with its
// half-spread record, its best chart check, and what the AI is testing.
import { botRuleLabel } from './momxBot.js';

export const RULE_LAB_URL = '/api/momx-scanner/rule-lab';

const pct = v => (typeof v === 'number' && Number.isFinite(v) ? `${v > 0 ? '+' : ''}${v.toFixed(1)}%` : '-');
const win = v => (typeof v === 'number' && Number.isFinite(v) ? `${Math.round(v)}%` : '-');

/** Best check = the biggest average lift with 20+ trades on each side. */
export function bestCheck(rule, meanings = {}, side = 'bull') {
  let best = null;
  for (const [key, split] of Object.entries(rule?.checks || {})) {
    const w = split?.with, wo = split?.without;
    if (!w || !wo || w.trades < 20 || wo.trades < 20 || typeof w.avgReturn !== 'number') continue;
    const lift = w.avgReturn - rule.avgReturn;
    if (!best || lift > best.lift) best = { key, lift, with: w, meaning: meanings[key]?.[side] || key };
  }
  return best;
}

export function ruleLabRows(status, side = 'bull') {
  const card = status?.scorecard || {};
  return (card.rules || []).filter(r => r.side === side)
    .sort((a, b) => (b.trades || 0) - (a.trades || 0))
    .map(r => {
      const best = bestCheck(r, card.checks, side);
      const ai = r.aiImproved ? `Improved: take only when ${r.aiImproved.checkMeaning}`
        : r.aiTesting ? `Testing since ${r.aiTesting.startDay}: ${r.aiTesting.checkMeaning}` : 'No test yet';
      return {
        rule: r.rule, label: botRuleLabel(r.rule, side), trades: r.trades, days: r.days,
        win: win(r.winRate), avg: pct(r.avgReturn),
        today: r.lastDay ? `${r.lastDay.day}: ${r.lastDay.trades} · ${win(r.lastDay.winRate)} · ${pct(r.lastDay.avgReturn)}` : '-',
        best: best ? `${best.meaning}: ${win(best.with.winRate)} · ${pct(best.with.avgReturn)} (${best.with.trades})` : 'Not enough trades',
        ai, improved: Boolean(r.aiImproved), testing: Boolean(r.aiTesting),
      };
    });
}

export function ruleLabSummary(status) {
  const exps = status?.experiments || [];
  const count = s => exps.filter(e => e.state === s).length;
  return `${count('running')} testing · ${count('improved')} improved · ${count('rejected')} rejected · ${status?.trials || 0} tried in all`;
}
