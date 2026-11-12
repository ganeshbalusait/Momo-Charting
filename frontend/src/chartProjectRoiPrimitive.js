// Project ROI drawn on the chart the way the trader's reference card shows it
// (2026-11-11): from the candle where the plan was made, a green zone up to
// the target and a red zone down to the stop, a line at entry / target / stop,
// and a label box at the left end of each line:
//   [Target 695.16  +497%]   [Entry 690.54  695C $0.21]   [Stop 689.81  −50%]
// Bounded overlay: no extra series, timers or network; native coordinates
// keep it aligned through pan, zoom and pane resize.

const STYLE = {
  target: { line: "#3dff8a", text: "#3dff8a", box: "rgba(6, 40, 20, 0.92)", dash: [6, 4] },
  entry: { line: "#e8f4ff", text: "#e8f4ff", box: "rgba(16, 26, 44, 0.92)", dash: [] },
  stop: { line: "#ff5a6a", text: "#ff8a95", box: "rgba(48, 10, 16, 0.92)", dash: [] },
};

// Bar the plan starts on: the last bar at or before the plan time; the first
// bar when the plan is older than the loaded tape.
export function projectRoiStartTime(bars = [], planTimeSec = 0) {
  if (!Array.isArray(bars) || !bars.length) return null;
  const at = Number(planTimeSec) || 0;
  let found = null;
  for (const bar of bars) {
    const time = Number(bar?.time);
    if (!Number.isFinite(time)) continue;
    if (time <= at) found = time; else break;
  }
  return found ?? Number(bars[0]?.time);
}

// Label boxes stacked so none overlaps another: walk them top to bottom and
// push each one below the previous box when they would touch.
export function layoutProjectRoiLabels(items = [], { height = 18, gap = 2, top = 0, bottom = Infinity } = {}) {
  const placed = items
    .filter((item) => Number.isFinite(item?.y))
    .map((item) => ({ ...item, boxY: item.y - height / 2 }))
    .sort((left, right) => left.boxY - right.boxY);
  let floor = top;
  for (const item of placed) {
    item.boxY = Math.max(item.boxY, floor);
    floor = item.boxY + height + gap;
  }
  const overflow = placed.length ? placed.at(-1).boxY + height - bottom : 0;
  if (overflow > 0) placed.forEach((item) => { item.boxY -= overflow; });
  return placed;
}

export class ChartProjectRoiPrimitive {
  // onDrawn(keys): called with "target|entry|stop" (the label boxes actually
  // drawn inside the pane) whenever that set changes - read by tests.
  constructor({ onDrawn = null } = {}) {
    this.onDrawn = onDrawn;
    this.drawnKeys = null;
    this.model = null;
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.renderer = { draw: (target) => this.draw(target) };
    this.views = [{ zOrder: () => "top", renderer: () => this.renderer }];
  }

  attached({ chart, series, requestUpdate }) {
    this.chart = chart;
    this.series = series;
    this.requestUpdate = requestUpdate;
    requestUpdate();
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  // model: { lines: [{key, price, title}], planTimeSec }
  setModel(model) {
    this.model = model && Array.isArray(model.lines) && model.lines.length ? model : null;
    this.requestUpdate?.();
  }

  updateAllViews() {}

  paneViews() {
    return this.views;
  }

  // App.jsx owns the price range (it keeps the plan's prices in view).
  autoscaleInfo() {
    return null;
  }

  startX(width) {
    const timeScale = this.chart?.timeScale?.();
    const bars = typeof this.series?.data === "function" ? this.series.data() : [];
    const start = projectRoiStartTime(bars, this.model?.planTimeSec);
    const x = start == null ? null : timeScale?.timeToCoordinate?.(start);
    // Older than the visible window: start at the left edge. Never so far
    // right that the zones vanish; the label boxes clamp separately.
    const left = x == null || !Number.isFinite(x) ? 0 : x;
    return Math.max(0, Math.min(left, width - 40));
  }

  draw(target) {
    if (!this.series || !this.model) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize: { width, height } }) => {
      const byKey = Object.fromEntries(this.model.lines.map((line) => [line.key, {
        ...line, y: this.series.priceToCoordinate(line.price),
      }]));
      const entry = byKey.entry;
      if (!entry || entry.y == null) return;
      const x0 = this.startX(width);
      const right = Math.max(x0, width);
      ctx.save();
      // Zones: entry -> target green, entry -> stop red.
      const zone = (line, color) => {
        if (!line || line.y == null) return;
        ctx.fillStyle = color;
        ctx.fillRect(x0, Math.min(entry.y, line.y), right - x0, Math.abs(line.y - entry.y));
      };
      zone(byKey.target, "rgba(61, 255, 138, 0.12)");
      zone(byKey.stop, "rgba(255, 90, 106, 0.12)");
      // Lines.
      for (const line of Object.values(byKey)) {
        if (line.y == null || line.y < -2 || line.y > height + 2) continue;
        const style = STYLE[line.key] || STYLE.entry;
        ctx.strokeStyle = style.line;
        ctx.lineWidth = line.key === "entry" ? 1 : 1.5;
        ctx.setLineDash(style.dash);
        ctx.beginPath();
        ctx.moveTo(x0, Math.round(line.y) + 0.5);
        ctx.lineTo(right, Math.round(line.y) + 0.5);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      // Label boxes at the left end of each line, never overlapping.
      const boxHeight = 18;
      ctx.textBaseline = "middle";
      // The boxes carry the contract prices for the broker, so they are long;
      // on a narrow phone pane step the font down until the widest one fits,
      // then keep every box inside the pane.
      const titles = Object.values(byKey).map((line) => String(line.title || ""));
      let widest = 0;
      for (const size of [11, 10, 9]) {
        ctx.font = `bold ${size}px ui-monospace, Consolas, monospace`;
        widest = Math.max(0, ...titles.map((text) => Math.ceil(ctx.measureText(text).width) + 12));
        if (widest <= width - 8) break;
      }
      const boxX = Math.max(2, Math.min(x0 + 4, width - widest - 4));
      const visible = Object.values(byKey).filter((line) => line.y != null && line.y > -boxHeight && line.y < height + boxHeight);
      const placed = layoutProjectRoiLabels(visible, { height: boxHeight, gap: 2, top: 2, bottom: height - 2 });
      const drawnKeys = placed.filter((item) => item.y >= 0 && item.y <= height).map((item) => item.key).join("|");
      if (drawnKeys !== this.drawnKeys) {
        this.drawnKeys = drawnKeys;
        this.onDrawn?.(drawnKeys);
      }
      for (const item of placed) {
        const style = STYLE[item.key] || STYLE.entry;
        const text = String(item.title || "");
        const boxWidth = Math.ceil(ctx.measureText(text).width) + 12;
        ctx.fillStyle = style.box;
        ctx.fillRect(boxX, item.boxY, boxWidth, boxHeight);
        ctx.strokeStyle = style.line;
        ctx.lineWidth = 1;
        ctx.strokeRect(boxX + 0.5, item.boxY + 0.5, boxWidth - 1, boxHeight - 1);
        ctx.fillStyle = style.text;
        ctx.fillText(text, boxX + 6, item.boxY + boxHeight / 2 + 0.5);
      }
      ctx.restore();
    });
  }
}
