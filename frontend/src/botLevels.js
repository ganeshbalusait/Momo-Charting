export const BOT_LEVEL_OPTIONS = Object.freeze([
  { value: 'all', label: 'All strategies' },
  { value: 'level1', label: 'Level 1 · RVOL + 3 Setup types' },
  { value: 'level2', label: 'Level 2 · SQZ confirmation + 3 Setup types' },
  { value: 'level3', label: 'Level 3 · Skittles confirmation + 3 Setup types' },
]);
export function filterBotLevel(days, level) {
  if (!BOT_LEVEL_OPTIONS.some(o => o.value === level) || level === 'all') return days || [];
  return (days || []).map(day => ({ ...day, trades: (day.trades || []).filter(t => t.rule === level) }));
}
export const BOT_LEVEL_HELP = 'New virtual call strategies: at least three distinct Setup types, MOM optional. Level 1 requires three cyan/green RVOL timeframes rising versus their previous valid update. Level 2 adds cyan/all-blank SQZ AND active Setup SQZ release; Level 3 adds a cyan/green Skittles box. Each level records its own first signal per ticker/day, so strategies overlap. Missing history does not qualify. Recorded option midpoints are estimates, not confirmed fills. OpenClaw/ChatGPT/Ollama review completed trades afterward; AI cannot block these entries. No historical backfill or broker orders.';
