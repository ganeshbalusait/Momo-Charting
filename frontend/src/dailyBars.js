// One daily candle per trading day (2026-10-03, his shared_MTF_Squeeze410
// CRDO screenshot: TOS "D" dark, the app orange).
//
// The server's dailyBars mixed Schwab's own daily candles (stamped 01:00 ET)
// with candles built from intraday tapes, stamped at whichever session they
// started in (00:00 overnight, 04:00 / 07:00 premarket, 20:00 the evening
// before). Its merges keyed by timestamp, so one day arrived 2-3 times and
// every daily study (D squeeze, daily ADX, pivots...) read a shortened window.
// Deduping CRDO this way turned the daily squeeze from "mid" into "none" -
// exactly TOS - whichever copy was kept.
//
// Trading day = the candle's own ET calendar date; the fullest copy (most
// volume) wins, equal volume -> the later stamp. A 20:00 overnight stub loses
// to the real day on volume; a weekend evening stub is dropped. (Rolling every
// 20:00+ stamp to the next day, the first version, dropped Friday candles that
// were stamped 20:00 - SPY 2026-10-02.)

const easternParts = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  hourCycle: "h23",
});

export function dailyBarTradingDay(timeSeconds) {
  const time = Number(timeSeconds);
  if (!Number.isFinite(time) || time <= 0) return null;
  const parts = Object.fromEntries(easternParts.formatToParts(new Date(time * 1000)).map((p) => [p.type, p.value]));
  const date = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day)));
  // A weekend evening stub (Sunday 20:00 = Monday's overnight start) is not
  // a session. Any other 20:00+ stamp keeps its own date: some sources stamp
  // the day's FULL candle at 20:00 (SPY 10-02 was lost when it rolled to Sat).
  const weekday = date.getUTCDay();
  if (Number(parts.hour) >= 20 && (weekday === 0 || weekday === 6)) return null;
  return date.toISOString().slice(0, 10);
}

export function dedupeDailyBars(bars) {
  const list = Array.isArray(bars) ? bars : [];
  const byDay = new Map();
  let duplicated = false;
  list.forEach((bar) => {
    const day = dailyBarTradingDay(bar?.time);
    if (!day) {
      duplicated = true;
      return;
    }
    const held = byDay.get(day);
    if (held || (bar.date && bar.date !== day)) duplicated = true;
    const volume = Number(bar?.volume) || 0;
    const heldVolume = Number(held?.volume) || 0;
    if (!held || volume > heldVolume || (volume === heldVolume && Number(bar.time) > Number(held.time))) {
      byDay.set(day, bar);
    }
  });
  // Clean input (the normal case) comes back as the same array, so callers
  // that compare references do not see a change.
  if (!duplicated) return list;
  return [...byDay.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([day, bar]) => (bar.date === day ? bar : { ...bar, date: day }));
}
