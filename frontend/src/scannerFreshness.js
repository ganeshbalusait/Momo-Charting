import { tapeAsOfNote } from "./tapeAsOfNote.js";

const rowClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
});
const rowDay = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
const rowMonthDay = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "2-digit", day: "2-digit" });

export function scannerRowFreshness(row, nowMs = Date.now()) {
  const raw = row?.m5?.sqzMom?.barAt ?? row?.marketDataAt;
  const start = typeof raw === "number" ? (raw < 1e11 ? raw * 1000 : raw) : Date.parse(raw || "");
  // A 5m bar's start is not its delivery time. Flag older study windows only
  // after the window ended at least ten minutes ago, without hiding the row.
  if (!Number.isFinite(start) || start <= 0 || !Number.isFinite(new Date(start).getTime())
      || nowMs - (start + 300000) < 600000) return null;
  const clock = rowClock.format(start);
  const date = rowDay.format(start), prefix = date === rowDay.format(nowMs) ? "" : `${rowMonthDay.format(start)} `;
  return {text: `Data ${prefix}${clock}`, title: `The latest supplied 5m study candle started ${date} ${clock} ET. `
    + "These study inputs are older; a newer live quote does not refresh momentum studies. "
    + "Quiet trading or delayed/missing candles can cause this. This age alone does not identify a provider delay."};
}

export function scannerFreshness(board, nowMs = Date.now(), receivedAt = null) {
  const f = board?.freshness || {};
  const newest = f.newestDataAt || board?.tapeAsOf;
  const note = tapeAsOfNote(newest, nowMs);
  if (!note) return null;
  const age = value => Math.floor(Math.max(0, nowMs - Date.parse(value)) / 60000);
  const oldestAge = age(f.oldestDataAt);
  const newestAge = age(newest);
  const windowKnown = f.barTimestampRole === "start" && f.barIntervalSeconds === 300;
  const clockTime = value => new Date(value).toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const windowEnd = Date.parse(newest) + 300000;
  const endedMinutes = Math.floor(Math.max(0, nowMs - windowEnd) / 60000);
  const windowText = `5m bar ${clockTime(newest)}–${clockTime(windowEnd)} ET`
    + (endedMinutes >= 5 ? ` · window ended ${endedMinutes}m ago` : "");
  const clock = value => {
    const stamp = typeof value === "number" ? value : Date.parse(value);
    return Number.isFinite(stamp) ? new Date(stamp).toLocaleString("en-US", {
      timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    }) + " ET" : "unknown";
  };
  return {
    tone: note.tone,
    text: (windowKnown ? windowText : `Data ${clockTime(newest)} ET · ${newestAge}m old`)
      + (Number.isFinite(oldestAge) && oldestAge > newestAge ? ` · oldest start ${oldestAge}m ago` : ""),
    title: `Source bars: ${clock(f.oldestDataAt || newest)} to ${clock(newest)}. `
      + `Scan started: ${clock(f.scanStartedAt || board?.generatedAt)}. Completed: ${clock(f.scanCompletedAt || board?.generatedAt)}. `
      + `API served: ${clock(f.servedAt)}. Browser received: ${clock(board?.source === "cache" ? null : receivedAt)}. `
      + (windowKnown ? `Bar timestamps mark the start of a 5-minute window, not when the feed arrived. Newest window ends ${clock(windowEnd)}; its start is ${newestAge}m ago. This window end is not proof that the provider delivered a final or live update. ` : "")
      + (f.providerNote || (board?.dataSource === "tos" ? "TOS history is rate-paced; a fixed provider delay is unconfirmed." : ""))
      + (f.unknownTimeRows ? ` Source time unknown for ${f.unknownTimeRows} rows.` : ""),
  };
}
