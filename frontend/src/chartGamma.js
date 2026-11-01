// Dollar gamma per 1% underlying move. The call+/put- signs are a proxy,
// NOT observed dealer inventory. Volume is cumulative traded contracts.
export const GAMMA_POSITIVE = "#ffd400";
export const GAMMA_NEGATIVE = "#ffffff";

const number = value => value == null || value === "" ? null
  : Number.isFinite(Number(value)) ? Number(value) : null;
const rootOf = row => String(row?.underlyingSymbol || row?.underlying ||
  String(row?.symbol || "").match(/^(.+?)\s*\d{6}[CP]\d{8}$/)?.[1] || "").trim().toUpperCase();

export function formatGammaDollars(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const abs = Math.abs(n);
  const [scale, suffix] = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"]
    : abs >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${n < 0 ? "−" : "+"}$${(abs / scale).toFixed(scale === 1 ? 0 : 2)}${suffix}`;
}

export function buildChartGamma({ rows, symbol, spot, mode = "oi", expiry = "selected", selectedExpiry, limit = 6 } = {}) {
  const target = String(symbol || "").trim().toUpperCase();
  const owned = (Array.isArray(rows) ? rows : []).filter(row => target && rootOf(row) === target);
  const expiries = [...new Set(owned.map(row => String(row.expiry || "").slice(0, 10)))]
    .filter(day => /^\d{4}-\d{2}-\d{2}$/.test(day)).sort();
  const chosen = expiries.includes(expiry) ? expiry : expiries.includes(selectedExpiry) ? selectedExpiry : expiries[0] || "";
  const metric = mode === "volume" ? "volume" : "oi";
  const model = { symbol: target, mode: metric, expiry: chosen, expiries, levels: [], eligible: 0, priced: 0, missing: 0, totalUnits: 0, grossExposure: 0, emptyReason: "no-chain", spot: number(spot) };
  if (!(model.spot > 0)) return { ...model, emptyReason: "no-spot" };
  if (!chosen) return model;
  // Deduplicate contract snapshots before aggregating; the last row wins.
  const contracts = new Map();
  for (const row of owned) {
    const side = String(row.side || "").toUpperCase();
    const strike = number(row.strike);
    if (String(row.expiry || "").slice(0, 10) !== chosen || !["CALL", "PUT"].includes(side) || !(strike > 0)) continue;
    contracts.set(`${chosen}|${side}|${strike}`, row);
  }
  const levels = new Map();
  for (const row of contracts.values()) {
    model.eligible++;
    const gamma = number(row.gamma);
    const units = number(metric === "oi" ? (row.open_interest ?? row.openInterest) : row.volume);
    const multiplier = number(row.contractMultiplier ?? row.multiplier ?? 100);
    if (gamma === null || gamma < 0 || units === null || units < 0 || !(multiplier > 0)) { model.missing++; continue; }
    const exposure = gamma * units * multiplier * model.spot ** 2 * 0.01;
    if (!Number.isFinite(exposure)) { model.missing++; continue; }
    model.priced++;
    model.totalUnits += units;
    model.grossExposure += exposure;
    const strike = Number(row.strike);
    const level = levels.get(strike) || { strike, call: 0, put: 0, net: 0 };
    if (row.side.toUpperCase() === "CALL") level.call += exposure;
    else level.put += exposure;
    level.net = level.call - level.put;
    levels.set(strike, level);
  }
  model.levels = [...levels.values()].filter(level => Number.isFinite(level.net) && level.net !== 0)
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net) || a.strike - b.strike)
    .slice(0, Math.min(12, Math.max(1, Number(limit) || 6)))
    .map(level => ({ ...level, color: level.net < 0 ? GAMMA_NEGATIVE : GAMMA_POSITIVE }));
  model.emptyReason = model.levels.length ? "" : !model.eligible ? "no-chain"
    : !model.priced ? "missing-inputs" : model.totalUnits === 0 ? `zero-${metric}`
    : !model.grossExposure ? "zero-gamma" : "net-zero";
  return model;
}

export function chartGammaEmptyMessage(model) {
  return ({
    "no-chain": "Waiting for this ticker’s option chain. Gamma levels will appear when its data arrives.",
    "no-spot": "Gamma needs an underlying price before levels can be calculated.",
    "missing-inputs": "The chain is missing gamma or contract counts for this expiry.",
    "zero-volume": "No volume gamma to draw: this snapshot reports zero option volume for the selected expiry.",
    "zero-oi": "No OI gamma to draw: this snapshot reports zero open interest for the selected expiry.",
    "zero-gamma": "No usable gamma exposure: gamma is zero for contracts with reported counts. Try another loaded expiry.",
    "net-zero": "No net gamma levels: the reported call and put estimates cancel at each strike.",
  })[model?.emptyReason] || "";
}

// A bounded overlay: no extra series, history replay, timers or network calls.
// Native price coordinates keep it aligned through pan, zoom and pane resize.
export class ChartGammaPrimitive {
  constructor(model, { glow = true, labels = true } = {}) {
    this.model = model; this.series = null;
    this.glow = glow;
    this.labels = labels;
    this.renderer = { draw: target => this.draw(target) };
    this.views = [{ zOrder: () => "top", renderer: () => this.renderer }];
  }
  attached({ series, requestUpdate }) { this.series = series; requestUpdate(); }
  detached() { this.series = null; }
  updateAllViews() {}
  paneViews() { return this.views; }
  draw(target) {
    if (!this.series) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize: { width, height } }) => {
      const max = Math.max(...this.model.levels.map(level => Math.abs(level.net)), 1);
      const labels = [];
      const compact = width < 480;
      const labelHeight = compact ? 20 : 24;
      const textHeight = compact ? 13 : 16;
      ctx.save();
      ctx.font = "bold 11px monospace";
      for (const level of this.model.levels) {
        const y = this.series.priceToCoordinate(level.strike);
        if (y == null || !Number.isFinite(y) || y < 8 || y > height - 8) continue;
        const barWidth = Math.max(3, Math.min(compact ? 72 : 120, width * 0.2) * Math.abs(level.net) / max);
        if (this.glow && typeof ctx.createLinearGradient === "function") {
          const bandHeight = 12 + 6 * Math.abs(level.net) / max;
          // Keep the halo around its short concentration bar at the right.
          // Extending it over the whole candle pane obscures price action.
          const bandWidth = Math.min(Math.max(0, width - 8), barWidth + 12);
          const bandLeft = Math.max(0, width - 8 - bandWidth);
          ctx.globalAlpha = 0.32; ctx.fillStyle = "#080b13";
          ctx.fillRect(bandLeft, y - bandHeight / 2 - 2, bandWidth, bandHeight + 4);
          const gradient = ctx.createLinearGradient(0, y - bandHeight / 2, 0, y + bandHeight / 2);
          const rgb = level.net < 0 ? "215,220,230" : "215,132,0";
          for (const [stop, alpha] of [[0, 0], [0.18, 0.30], [0.35, 0.48], [0.5, 0.68], [0.65, 0.48], [0.82, 0.30], [1, 0]]) gradient.addColorStop(stop, `rgba(${rgb},${alpha})`);
          ctx.globalAlpha = 0.95; ctx.fillStyle = gradient;
          ctx.fillRect(bandLeft, y - bandHeight / 2, bandWidth, bandHeight);
        }
        ctx.globalAlpha = 0.45;
        ctx.fillStyle = level.color;
        ctx.fillRect(width - 8 - barWidth, y - 5, barWidth, 10);
        ctx.globalAlpha = 0.7;
        ctx.strokeStyle = "#22d3ee";
        ctx.setLineDash([3, 4]);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width - 8, y); ctx.stroke();
        if (!this.labels) continue;
        const boxY = y >= labelHeight + 10 ? y - labelHeight - 6 : y + 6;
        if (width < 110 || boxY + labelHeight > height
          || labels.some(([from, to]) => boxY < to + 4 && boxY + labelHeight > from - 4)) continue;
        const total = level.call + level.put;
        const putShare = total > 0 ? level.put / total : 0;
        let cells = [
          { text: `${level.strike} ${width >= 400 ? "Net " : ""}${formatGammaDollars(level.net)}`, color: "#bfc4ce" },
          { text: `P ${Math.round(putShare * 100)}% ${level.put ? formatGammaDollars(-level.put) : "$0"}`, color: GAMMA_NEGATIVE, share: putShare },
          { text: `C ${formatGammaDollars(level.call)}`, color: GAMMA_POSITIVE, share: 1 - putShare },
        ];
        let widths, rowWidth;
        for (const size of (compact ? [9, 8] : [11, 10, 9])) {
          ctx.font = `bold ${size}px monospace`;
          widths = cells.map(cell => ctx.measureText(cell.text).width + 8);
          rowWidth = widths.reduce((sum, w) => sum + w, 0) + 6;
          if (rowWidth <= width - 12) break;
        }
        // Tiny grid panes keep the net label; component values remain in
        // Method / values rather than being clipped or drawn over the axis.
        if (rowWidth > width - 12) {
          cells = cells.slice(0, 1); widths = widths.slice(0, 1); rowWidth = widths[0];
        }
        if (rowWidth > width - 12) continue;
        labels.push([boxY, boxY + labelHeight]);
        let x = width - rowWidth - 6;
        ctx.setLineDash([]); ctx.lineWidth = 1; ctx.globalAlpha = 0.96;
        cells.forEach((cell, index) => {
          const w = widths[index];
          ctx.fillStyle = "#10121c"; ctx.fillRect(x, boxY, w, textHeight);
          ctx.strokeStyle = cell.color; ctx.strokeRect(x, boxY, w, textHeight);
          ctx.fillStyle = cell.color; ctx.fillText(cell.text, x + 4, boxY + (compact ? 10 : 12));
          if (cell.share != null) {
            ctx.strokeRect(x, boxY + textHeight + 3, w, compact ? 4 : 5);
            ctx.fillRect(x + 1, boxY + textHeight + 4, Math.max(0, (w - 2) * cell.share), compact ? 2 : 3);
          }
          x += w + 3;
        });
      }
      ctx.restore();
    });
  }
}
