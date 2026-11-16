export const BOT_LEVEL_OPTIONS = Object.freeze([
  { value: 'all', label: 'All strategies' },
  // His rule 2026-11-10: the level is how many of RVOL / SQZ / Skittles are on.
  { value: 'level1', label: 'Level 1 · any 1 of RVOL/SQZ/Skittles + 3 Setup types' },
  { value: 'level2', label: 'Level 2 · any 2 of RVOL/SQZ/Skittles + 3 Setup types' },
  { value: 'level3', label: 'Level 3 · all 3 of RVOL/SQZ/Skittles + 3 Setup types' },
]);
export function filterBotLevel(days, level) {
  if (!BOT_LEVEL_OPTIONS.some(o => o.value === level) || level === 'all') return days || [];
  return (days || []).map(day => ({ ...day, trades: (day.trades || []).filter(t => t.rule === level) }));
}
export const BOT_LEVEL_HELP = 'New virtual call strategies: at least three distinct Setup types, MOM optional. Then three confirms, any one is enough (since 2026-11-10): RVOL cyan/green on 3+ timeframes 5m-Daily (the colour counts, rising is not needed), OR ONE of 2h/4h/Daily SQZ cyan, blank or fired today, OR one or more cyan/green Skittles boxes 2h-Monthly. Level 1 = any one, Level 2 = any two, Level 3 = all three. BEAR (puts) mirrors every color: magenta/red RVOL, magenta SQZ, magenta/red/plum Skittles. Each level records its own first signal per ticker/day, so strategies overlap. Recorded option midpoints are estimates, not confirmed fills. OpenClaw/ChatGPT/Ollama review completed trades afterward; AI cannot block these entries. No historical backfill. Since 2026-11-10 these three are the only strategies the Paper 3 and Paper 5 accounts trade, on the 0.20-delta contract.';
