// Unknown scan results stay hidden when scan matching is enabled.
export function matchingScannerRows(rows, matchesOnly = true) {
  const source = Array.isArray(rows) ? rows : [];
  return matchesOnly ? source.filter(row => row?.scanPass === true) : source.filter(Boolean);
}

// Overnight bars from 20:00 ET belong to the following trading date.
const sessionClock = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",hourCycle:"h23",
});
function sessionDate(ms) {
  const parts = Object.fromEntries(sessionClock.formatToParts(ms).map(p => [p.type,p.value]));
  return new Date(Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day)+(Number(parts.hour)>=20?1:0))).toISOString().slice(0,10);
}
export function currentScannerRows(rows, nowMs = Date.now()) {
  const today = sessionDate(nowMs);
  return (Array.isArray(rows) ? rows : []).filter(row => {
    const raw = row?.m5?.sqzMom?.barAt ?? row?.marketDataAt;
    const start = typeof raw === "number" ? (raw < 1e11 ? raw * 1000 : raw) : Date.parse(raw || "");
    return Number.isFinite(start) && start > 0 && start <= nowMs && sessionDate(start) === today;
  });
}
