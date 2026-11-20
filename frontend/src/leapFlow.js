// LEAP OI Scanner: today's trades on each contract, in beginner words (2026-11-20, "If I see leap oi scanner like INTC
// I don't know anything like buyers or seller ... then I have to search agx flow ... I feel little confusion").
// The numbers are AGX Flow's for the same contract (server: momx/leap_oi_scanner.with_flow), so the pages agree.

const FILL = {
  ask: { word: "Bought", hint: "the last trade paid the asking price, so a buyer was probably eager", tone: "is-bought" },
  bid: { word: "Sold", hint: "the last trade took the bid price, so a seller was probably eager", tone: "is-sold" },
  mid: { word: "Can't tell", hint: "the last trade was between the bid and the ask", tone: "is-mid" },
};

function money(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1000) return `$${Math.round(n / 1000)}K`;
  return `$${Math.round(n)}`;
}

/** {text, word, tone, title, unusual} for a row's flow, or a quiet "none seen" line when it did not trade. */
export function flowCell(row) {
  const flow = row?.flow;
  const volume = Number(flow?.volume);
  if (!flow || !Number.isFinite(volume) || volume <= 0) {
    return { text: "No trades seen today", word: "", tone: "is-none", title: "AGX Flow saw no trades on this contract in its latest read.", unusual: false };
  }
  const fill = FILL[flow.fill] || FILL.mid;
  const at = flow.at ? new Date(flow.at) : null;
  const when = at && !Number.isNaN(at.getTime())
    ? ` (read ${at.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })} ET)` : "";
  return {
    text: `${volume.toLocaleString("en-US")} contracts · ${money(flow.premium)}`,
    word: fill.word,
    tone: fill.tone,
    unusual: Boolean(flow.unusual),
    title: `Today: ${volume.toLocaleString("en-US")} contracts traded, about ${money(flow.premium)}. ${fill.word}: ${fill.hint}.`
      + `${flow.unusual ? " Unusual: more traded today than were open before." : ""} A guess, not exchange data${when}.`,
  };
}
