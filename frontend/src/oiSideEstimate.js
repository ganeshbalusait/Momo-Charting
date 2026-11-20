// The High OI board's "B/S" cell: the app's ESTIMATE of whether a strike's trades were mostly bought or mostly sold
// (server: momx/oi_sides.py). The exchange does not publish this, so the cell is short and its tooltip says how it
// was worked out and how much of the day's volume the app actually saw.

export const OI_SIDE_NOTE = "B/S is an estimate: trades the app saw printing near the ask count as bought, near the bid as sold. It is not exchange data.";

const whole = (value) => Math.max(0, Math.round(Number(value) || 0));

/** { text, tone, title } for one row's estimate; text "-" when there is none. */
export function oiSideCell(est) {
  if (!est || typeof est !== "object") return { text: "-", tone: "", title: "No estimate yet: the app has not watched enough trades on this strike today." };
  const bought = whole(est.bought);
  const sold = whole(est.sold);
  const sorted = bought + sold;
  const share = Number(est.share);
  const parts = [];
  let text = "-";
  let tone = "";
  if (est.label && Number.isFinite(share) && sorted > 0) {
    const soldSide = est.label === "mostly sold";
    const percent = Math.round((soldSide ? 1 - share : share) * 100);
    text = est.label === "mixed" ? "mixed" : `${percent}% ${soldSide ? "S" : "B"}`;
    tone = est.label === "mixed" ? "mixed" : soldSide ? "sold" : "bought";
    parts.push(`${est.label}: ${bought.toLocaleString("en-US")} bought, ${sold.toLocaleString("en-US")} sold of ${whole(est.volume).toLocaleString("en-US")} traded`);
  }
  const change = est.oiChange === null || est.oiChange === undefined ? null : Math.round(Number(est.oiChange));
  if (change !== null && Number.isFinite(change)) {
    parts.push(`open interest ${change > 0 ? "up" : change < 0 ? "down" : "unchanged"}${change ? ` ${Math.abs(change).toLocaleString("en-US")}` : ""} since ${est.oiSince || "the last session"}`);
    if (text === "-") { text = `OI ${change > 0 ? "+" : change < 0 ? "-" : ""}${compact(Math.abs(change))}`; tone = "oi"; }
  }
  if (!parts.length) return { text: "-", tone: "", title: "No estimate yet: the app has not watched enough trades on this strike today." };
  return { text, tone, title: `${parts.join("; ")}${est.day ? ` (${est.day})` : ""}. Estimate, not exchange data.` };
}

function compact(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k`;
  return String(value);
}
