// Chart Time Machine (2026-11-15): "what are the odds from here?" for the
// chart's ROI plan. The server (time_machine.py, GET /api/time-machine)
// replays the rest of today 10,000 times from the stock's own 60-day
// five-minute candles and walks the same plan over every past session; this
// module turns that answer into what the Odds card shows. Read-only: it never
// places, blocks or changes a trade.

import { formatPercent, formatStrike, projectRoiOptionValue } from "./chartProjectRoi.js";

export const TIME_MACHINE_COLORS = {
  target: "oklch(78% 0.16 150)",
  stop: "oklch(70% 0.19 25)",
  neither: "oklch(70% 0.03 256)",
};

const round1 = (value) => Math.round(Number(value) * 10) / 10;
const positive = (value) => (Number(value) > 0 ? Number(value) : 0);

// The request for one plan: the ROI card's live price, target and -50% stop.
export function timeMachineUrl({ symbol = "", side = "CALL", spot = 0, target = 0, stop = 0 } = {}) {
  const ticker = String(symbol || "").trim().toUpperCase();
  if (!ticker || !positive(spot) || !positive(target) || !positive(stop)) return "";
  const params = new URLSearchParams({
    symbol: ticker,
    side: String(side).toUpperCase() === "PUT" ? "PUT" : "CALL",
    spot: Number(spot).toFixed(2),
    target: Number(target).toFixed(2),
    stop: Number(stop).toFixed(2),
  });
  return `/api/time-machine?${params}`;
}

// The plan the card would ask about, from a buildProjectRoi() result, or ""
// with the reason it cannot be asked yet.
export function timeMachinePlan(result, symbol = "") {
  if (!result?.ready) return { url: "", reason: "Waiting for the option chain." };
  if (!positive(result.stopStock)) return { url: "", reason: "The plan has no stop yet (the contract needs a price)." };
  const url = timeMachineUrl({ symbol, side: result.side, spot: result.spot, target: result.target, stop: result.stopStock });
  return url ? { url, reason: "" } : { url: "", reason: "The plan needs a price, a target and a stop." };
}

// Option % at the median close of the paths that hit neither, from the ROI
// card's own contract model (delta-gamma, intrinsic floor).
export function roiAtPrice(result, price) {
  const entry = positive(result?.entry);
  const row = result?.contract;
  if (!entry || !row || !positive(price)) return null;
  const value = projectRoiOptionValue({
    side: result.side, spot: result.spot, price: result.livePrice, delta: row.delta, gamma: row.gamma, strike: result.strike, target: price,
  });
  return value > 0 ? Math.round(((value - entry) / entry) * 1000) / 10 : null;
}

// What the card shows. `roiAtTarget` / `roiAtStop` / `roiAtNeither`: the
// option's % at each outcome (from the ROI card); the average weighs them by
// the replayed odds. Any missing one leaves the average out.
export function timeMachineView(payload, { roiAtTarget = null, roiAtStop = null, roiAtNeither = 0 } = {}) {
  if (!payload || payload.ok !== true) {
    return { ready: false, reason: payload?.reason || "No answer yet." };
  }
  const odds = payload.odds || {};
  const rows = [
    { key: "target", label: "Target hit first", pct: round1(odds.target || 0) },
    { key: "stop", label: "Stop hit first", pct: round1(odds.stop || 0) },
    { key: "neither", label: `Neither by ${payload.flatLabel || "3:55 PM"}`, pct: round1(odds.neither || 0) },
  ].map((row) => ({ ...row, color: TIME_MACHINE_COLORS[row.key] }));
  const counts = payload.historyCounts || {};
  const history = Array.isArray(payload.history) ? payload.history : [];
  const parts = [roiAtTarget, roiAtStop, roiAtNeither];
  const averageRoi = parts.every((value) => value != null && Number.isFinite(Number(value)))
    ? round1((rows[0].pct * roiAtTarget + rows[1].pct * roiAtStop + rows[2].pct * roiAtNeither) / 100)
    : null;
  const when = payload.mode === "next-session"
    ? `market closed: a whole session from the ${payload.startLabel || "9:30"} open`
    : `from ${payload.startLabel} ET to ${payload.flatLabel || "3:55 PM"}`;
  const historyNote = `Same trade at the same time on each of the last ${payload.sessions} sessions`
    + (history.length ? ` (${history[0].day.slice(5)} – ${history.at(-1).day.slice(5)})` : "");
  return {
    ready: true,
    rows,
    when,
    subtitle: `${Number(payload.paths || 0).toLocaleString("en-US")} replays · ${when}`,
    historyNote,
    history,
    historyCounts: { target: counts.target || 0, stop: counts.stop || 0, neither: counts.neither || 0 },
    historyTotal: history.length,
    averageRoi,
    roiAtTarget,
    roiAtStop,
    roiAtNeither,
    verdict: payload.verdict || "",
  };
}

// SVG geometry for the fan: price bands per step, a few whole replayed paths
// (coloured by outcome) and the entry / target / stop lines.
export function timeMachineFan(payload, { width = 360, height = 170, pad = 6 } = {}) {
  if (!payload?.ok || !payload.bands) return null;
  const steps = Math.max(1, Number(payload.steps) || 1);
  const prices = [payload.spot, payload.target, payload.stop, ...payload.bands["10"], ...payload.bands["90"]];
  (payload.sample || []).forEach((path) => prices.push(...path.closes));
  const finite = prices.map(Number).filter((value) => Number.isFinite(value) && value > 0);
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  if (!(hi > lo)) { hi = lo + 1; }
  const room = (hi - lo) * 0.06;
  lo -= room; hi += room;
  const x = (step) => pad + (step / steps) * (width - 2 * pad);
  const y = (price) => pad + ((hi - Number(price)) / (hi - lo)) * (height - 2 * pad);
  const line = (values, from = 0) => [[from, payload.spot], ...values.map((v, i) => [i + 1, v])]
    .map(([step, value], index) => `${index ? "L" : "M"}${x(step).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  const band = (upper, lower) => {
    const top = [[0, payload.spot], ...upper.map((v, i) => [i + 1, v])];
    const bottom = [[0, payload.spot], ...lower.map((v, i) => [i + 1, v])].reverse();
    return [...top, ...bottom].map(([step, value], index) => `${index ? "L" : "M"}${x(step).toFixed(1)},${y(value).toFixed(1)}`).join(" ") + " Z";
  };
  return {
    width,
    height,
    outer: band(payload.bands["90"], payload.bands["10"]),
    inner: band(payload.bands["75"], payload.bands["25"]),
    median: line(payload.bands["50"]),
    paths: (payload.sample || []).map((path, index) => ({ key: index, outcome: path.outcome, d: line(path.closes), color: TIME_MACHINE_COLORS[path.outcome] || TIME_MACHINE_COLORS.neither })),
    levels: [
      { key: "target", y: y(payload.target), label: `Target ${Number(payload.target).toFixed(2)}`, color: TIME_MACHINE_COLORS.target },
      { key: "entry", y: y(payload.spot), label: `Now ${Number(payload.spot).toFixed(2)}`, color: "oklch(80% 0.11 230)" },
      { key: "stop", y: y(payload.stop), label: `Stop ${Number(payload.stop).toFixed(2)}`, color: TIME_MACHINE_COLORS.stop },
    ],
  };
}

// Odds on the chart's own lines (2026-11-15, Ganesh: "In chart add odds like
// that ... only target and stop loss and entry"): the plan's Target line gets
// "target first %", the Stop line "stop first %", the Entry line "neither %".
// Only when the answer is for this exact plan (side, target and stop); other
// target lines (T1, T3...) are left as they are.
export function withOddsOnLines(lines = [], payload = null, result = null) {
  if (!payload?.ok || !result || !Array.isArray(lines) || !lines.length) return lines;
  const same = (a, b) => Math.abs(Number(a) - Number(b)) < 0.006;
  if (payload.side !== result.side || !same(payload.target, result.target) || !same(payload.stop, result.stopStock)) return lines;
  const prefix = lines.some((line) => line.key.startsWith("c-") || line.key.startsWith("p-")) ? (result.side === "CALL" ? "c-" : "p-") : "";
  const odds = payload.odds || {};
  const add = {
    entry: ` · ${Math.round(odds.neither || 0)}% neither`,
    [`${prefix}target`]: ` · ${Math.round(odds.target || 0)}%`,
    [`${prefix}stop`]: ` · ${Math.round(odds.stop || 0)}%`,
  };
  return lines.map((line) => (add[line.key] ? { ...line, title: `${line.title}${add[line.key]}` } : line));
}

// "Odds" on the chart (2026-11-15, "I want only odds or ROI in chart or
// both"): just three lines, Entry / Target / Stop, each with its odds and
// the option's % there (2026-11-15: "Odds give me percentage also for
// target and stop loss"). While the answer is on its way: "odds…".
export function oddsOnlyLines(result = null, payload = null) {
  if (!result?.ready || !(result.target > 0) || !(result.stopStock > 0)) return [];
  const same = (a, b) => Math.abs(Number(a) - Number(b)) < 0.006;
  const fresh = payload?.ok && payload.side === result.side && same(payload.target, result.target) && same(payload.stop, result.stopStock);
  const odds = fresh ? payload.odds || {} : null;
  const tag = (value, word) => (odds ? ` · ${Math.round(value || 0)}% ${word}` : " · odds…");
  const entry = result.entrySpot > 0 ? result.entrySpot : result.spot;
  // The option's price at each line, as the ROI boxes show it (2026-11-15:
  // "I don't see option price in odds"): the contract and its entry on Entry,
  // the projected price on Target, the -50% stop price on Stop.
  const money = (value) => (Number(value) > 0 ? `  $${Number(value).toFixed(2)}` : "");
  const contract = result.strike > 0 && result.sideLetter ? `  ${formatStrike(result.strike)}${result.sideLetter}` : "";
  const targetPct = `${result.roiPercent == null ? "" : `  ${formatPercent(result.roiPercent)}`}${money(result.projectedValue)}`;
  const stopPct = result.stopPremium > 0 ? `  −50%${money(result.stopPremium)}` : "";
  return [
    { key: "entry", price: entry, color: "#e8f4ff", title: `Entry ${Number(entry).toFixed(2)}${contract}${money(result.entry)}${tag(odds?.neither, "neither")}` },
    { key: "target", price: result.target, color: "#3dff8a", title: `Target ${Number(result.target).toFixed(2)}${targetPct}${tag(odds?.target, "odds")}` },
    { key: "stop", price: result.stopStock, color: "#ff5a6a", title: `Stop ${Number(result.stopStock).toFixed(2)}${stopPct}${tag(odds?.stop, "odds")}` },
  ];
}

// The lines the chart draws for each choice: "roi" the ROI boxes, "odds" only
// the three odds lines, "both" the ROI boxes with the odds added.
export function chartPlanLines(show, roiLines = [], result = null, payload = null) {
  if (show === "odds") return oddsOnlyLines(result, payload);
  if (show === "both") return withOddsOnLines(roiLines, payload, result);
  return roiLines;
}
