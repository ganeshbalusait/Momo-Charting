// MomX scanner on a phone as two-line cards (Ganesh 2026-11-20: "Build it
// image looks good" - his sample scanner, rebuilt with our columns).
//
//   top line:    % chg · ⚡ · news age · setup tags · RVOL / SQZ dots
//   bottom line: industry chip · SYMBOL · H/L · Skittles 2h 4h D 2D 3D 4D W Mo
//
// The colours are NOT the sample's invented scale: every cell keeps the colour
// his TOS study painted (cellStyle), drawn as a filled pill when the script
// painted a background and as plain coloured text when it did not. A cell
// whose cross fired in the last 15 minutes is "fresh" (the glowing box).
import {
  cellIsLiveSpike,
  cellStyle,
  formatPctChange,
  formatSkittles,
  hasPaintedBackground,
  industryColor,
  isMutedCell,
  newsOf,
} from "./momxCells.js";

export const PHONE_SKITTLES_TFS = Object.freeze([
  ["2h", "2h"], ["4h", "4h"], ["D", "D"], ["2D", "2D"], ["3D", "3D"], ["4D", "4D"], ["Wk", "W"], ["M", "Mo"],
]);

export const PHONE_DOT_GROUPS = Object.freeze([
  { section: "rvol", label: "RVOL 5m-1h", tfs: ["5m", "15m", "30m", "1h"] },
  { section: "rvol", label: "RVOL 2h-D", tfs: ["2h", "4h", "D"] },
  { section: "sqz", label: "SQZ 2h-W", tfs: ["2h", "4h", "D", "Wk"] },
]);

function sectionCell(row, section, tf) {
  const block = row && typeof row === "object" ? row[section] : null;
  const cell = block && typeof block === "object" ? block[tf] : null;
  return cell && typeof cell === "object" ? cell : null;
}

// The sample's value scale (2026-11-20, he chose "Sample colours by value"
// for the Cards; the table keeps the exact TOS colours). kind: "fill" = a
// solid box, "outline" = an outlined box, "text" = a coloured number. A
// number his TOS study painted is always a solid box.
export function phoneSkittlesStyle(value, painted = false) {
  const number = Number(value);
  let style;
  if (number >= 85) style = { kind: "fill", color: "#22d3ee" };
  else if (number >= 75) style = { kind: "fill", color: "#a6ff3a" };
  else if (number >= 65) style = { kind: "outline", color: "#ffd23f" };
  else if (number >= 50) style = { kind: "text", color: "#22d3ee" };
  else if (number >= 35) style = { kind: "text", color: "#ff2bd6" };
  else if (number >= 25) style = { kind: "fill", color: "#ff7a1a" };
  else style = { kind: "fill", color: "#ff2bd6" };
  return painted && style.kind === "text" ? { ...style, kind: "fill" } : style;
}

export function phoneSkittlesCells(row, nowMs = Date.now()) {
  return PHONE_SKITTLES_TFS.map(([tf, label]) => {
    const cell = sectionCell(row, "skittles", tf);
    const text = !cell || isMutedCell(cell) ? "" : formatSkittles(cell.value);
    const painted = hasPaintedBackground(cell);
    const style = text === "" ? { kind: "empty", color: "" } : phoneSkittlesStyle(cell.value, painted);
    return {
      tf,
      label,
      text,
      painted,
      kind: style.kind,
      color: style.color,
      fresh: Boolean(cell) && cellIsLiveSpike(cell, nowMs),
    };
  });
}

export function phoneDots(row) {
  return PHONE_DOT_GROUPS.map((group) => group.tfs.map((tf) => {
    const cell = sectionCell(row, group.section, tf);
    return {
      section: group.section,
      tf,
      color: hasPaintedBackground(cell) ? cellStyle(cell).backgroundColor : null,
    };
  }));
}

export function phoneRowSummary(row, nowMs = Date.now()) {
  if (!row || typeof row !== "object") return null;
  const pct = Number(row.pctChange);
  const hasPct = row.pctChange !== null && row.pctChange !== undefined && Number.isFinite(pct);
  const industry = typeof row.industry === "string" ? row.industry.trim() : "";
  const news = newsOf(row, nowMs);
  return {
    symbol: typeof row.symbol === "string" ? row.symbol : "",
    pctText: hasPct ? formatPctChange(pct) : "",
    tone: hasPct ? (pct >= 0 ? "up" : "down") : "",
    industry,
    industryColor: industry ? industryColor(industry) : "",
    newsAge: news && news.age ? String(news.age).replace(" ago", "").replace("just now", "now") : "",
    news,
    skittles: phoneSkittlesCells(row, nowMs),
    dots: phoneDots(row),
  };
}
