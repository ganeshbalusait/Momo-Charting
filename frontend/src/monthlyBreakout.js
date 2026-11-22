// Breakout / Breakdown lists (momx/monthly_breakout.py), Monthly, Weekly or
// Daily chart (2026-11-20: the same dropdown as the EMA Touch lists).
// The server sends, per list, `breakout: "up"|"down"`, `timeframe`, and
// `breakouts: {SYMBOL: {pattern, label, status, period, level, target, ...}}`;
// this turns that into the one-line chips under the list bar.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const SHORT_LABELS = Object.freeze({
  BOTTOM: "HL + Resistance",
  SYM: "Triangle",
  MIDDLE: "Range",
  TOP: "LH + Support",
});

// Computed lists (EMA Touch, Monthly Breakout/Breakdown) do not use up one of
// the trader's list slots - the server's create_list skips them, so the
// picker must too. Until 2026-11-20 it counted every list and said "List limit
// reached" at 4 built-ins + 2 of his own while the server would take 2 more.
export function pickerFull(lists, maxLists) {
  if (!Array.isArray(lists) || !Number.isFinite(maxLists)) return false;
  return lists.filter((item) => item && !item.derived).length >= maxLists;
}

export function monthName(period) {
  const match = /^(\d{4})-(\d{2})/.exec(String(period || ""));
  if (!match) return "";
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${match[1].slice(2)}` : "";
}

// "2026-11" -> "Nov 26"; "2026-11-20" -> "Nov 20" (a day, or the week's
// first day on the Weekly chart, shown as "wk Nov 16").
export function periodName(period, timeframe) {
  const day = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(period || ""));
  if (!day) return monthName(period);
  const month = MONTHS[Number(day[2]) - 1];
  if (!month) return "";
  const text = `${month} ${Number(day[3])}`;
  return timeframe === "Weekly" ? `wk ${text}` : text;
}

const BAR_WORDS = Object.freeze({ Monthly: "month", Weekly: "week", Daily: "day" });

function tfOf(meta) {
  const tf = meta && meta.timeframe;
  return tf === "Weekly" || tf === "Daily" ? tf : "Monthly";
}

// The chart a ticker clicked on a Breakout / Breakdown list opens on
// ("D" / "W" / "M"), or null for every other list (they keep the chart's own).
export function chartTimeframeKey(meta) {
  if (!meta || (meta.breakout !== "up" && meta.breakout !== "down")) return null;
  return { Monthly: "M", Weekly: "W", Daily: "D" }[meta.timeframe || "Monthly"] || null;
}

// The body for POST /api/momx-scanner/ema-timeframe, or null.
export function timeframeRequest(meta, timeframe) {
  if (!meta || !timeframe) return null;
  if (meta.breakout === "up" || meta.breakout === "down") return { breakout: meta.breakout, timeframe };
  if (meta.emaLength) return { length: meta.emaLength, timeframe };
  return null;
}

export function breakoutEmpty(meta) {
  const up = meta && meta.breakout === "up";
  return `No ${tfOf(meta).toLowerCase()} ${up ? "breakouts" : "breakdowns"} on the Watchlist right now.`;
}

function price(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  return number >= 100 ? number.toFixed(2) : number >= 1 ? number.toFixed(2) : number.toFixed(4);
}

// One chip per member, live (month not closed) first, then by symbol.
export function breakoutChips(meta) {
  const direction = meta && (meta.breakout === "up" || meta.breakout === "down") ? meta.breakout : null;
  const table = meta && meta.breakouts && typeof meta.breakouts === "object" ? meta.breakouts : {};
  if (!direction) return [];
  const up = direction === "up";
  const tf = tfOf(meta);
  const bar = BAR_WORDS[tf];
  const chips = [];
  for (const [symbol, signal] of Object.entries(table)) {
    if (!signal || typeof signal !== "object") continue;
    const live = signal.status === "live";
    const when = periodName(signal.period, tf);
    const line = price(signal.level);
    const target = price(signal.target);
    const pattern = SHORT_LABELS[signal.pattern] || String(signal.label || signal.pattern || "");
    chips.push({
      symbol,
      live,
      arrow: up ? "▲" : "▼",
      text: `${pattern}${line ? ` ${up ? ">" : "<"} ${line}` : ""}`,
      when: live ? `${when} not closed` : `${when} close`,
      title: [
        `${symbol}: ${signal.label || pattern} - ${tf.toLowerCase()} close ${up ? "above" : "below"} ${line || "the line"}`,
        live ? `${when} has not closed yet, so this can still fail.` : `Confirmed on the ${when} close.`,
        target ? (tf === "Monthly"
          ? `Measured target ${target} (reached within 12 months about half the time in the back-test).`
          : `Measured target ${target}.`) : "",
        up ? `Exit idea: a ${bar} closing below the 50 EMA.` : "",
      ].filter(Boolean).join("\n"),
    });
  }
  chips.sort((a, b) => (a.live === b.live ? a.symbol.localeCompare(b.symbol) : a.live ? -1 : 1));
  return chips;
}

export function breakoutNote(direction, timeframe) {
  const tf = timeframe === "Weekly" || timeframe === "Daily" ? timeframe : "Monthly";
  if (tf !== "Monthly" && (direction === "up" || direction === "down")) {
    const bar = BAR_WORDS[tf];
    return `${tf} close through the line, EMAs agree (${direction === "up" ? "close > 21 > 50" : "close < 21 < 50"}), same four patterns counted in ${bar}s. Caution: the back-test found NO edge on ${tf.toLowerCase()} breakouts - only Monthly beat a random entry. Scan below: ALL of the following = the ${direction === "up" ? "breakout" : "breakdown"} + VOLUME 4h >= 1.0% + CLOSE 1h >= 0.5%.`;
  }
  if (direction === "up") {
    return "Monthly close through the line, EMAs agree (close > 21 > 50). Back-test: 69-78% higher 12 months later vs 62% for any month. Scan below: ALL of the following = the breakout + VOLUME 4h >= 1.0% + CLOSE 1h >= 0.5%.";
  }
  if (direction === "down") {
    return "Info only: monthly breakdowns did NOT predict a fall in the back-test (most were higher a year later). Not a put signal. Scan below: ALL of the following = the breakdown + VOLUME 4h >= 1.0% + a 0.5% CLOSE 1h move (a drop on the BEAR board).";
  }
  return "";
}
