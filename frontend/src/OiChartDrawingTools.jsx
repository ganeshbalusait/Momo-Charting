import {
  Crosshair,
  Eye,
  EyeOff,
  LockKeyhole,
  Magnet,
  MousePointer2,
  PanelLeftClose,
  PenLine,
  RectangleHorizontal,
  Redo2,
  Ruler,
  Sigma,
  Spline,
  Trash2,
  Type,
  Undo2,
  ZoomIn,
} from "lucide-react";
import { useState } from "react";
import { FIB_RETRACEMENT_LEVELS } from "./oiChartDrawings";

const DRAWING_TOOL_BUTTONS = Object.freeze([
  { key: "crosshair", label: "Crosshair and chart pan", icon: Crosshair },
  { key: "select", label: "Select and edit drawing", icon: MousePointer2 },
  { key: "trend", label: "Trend line", icon: PenLine },
  { key: "horizontal", label: "Horizontal line", icon: Sigma },
  { key: "fib", label: "Fibonacci retracement", icon: Spline },
  { key: "rectangle", label: "Rectangle", icon: RectangleHorizontal },
  { key: "brush", label: "Brush", icon: PenLine },
  { key: "text", label: "Text note", icon: Type },
  { key: "measure", label: "Price and time measure", icon: Ruler },
]);

const FIB_LEVEL_LABELS = FIB_RETRACEMENT_LEVELS;
const FIB_LEVEL_COLORS = Object.freeze([
  "#787b86",
  "#f23645",
  "#ff9800",
  "#4caf50",
  "#089981",
  "#2962ff",
  "#787b86",
]);

function DrawingShape({
  item,
  selected,
  activeTool,
  onSelect,
  onDrawingPointerDown,
  onEditText,
  onHandlePointerDown,
}) {
  const points = item.screenPoints || [];
  const first = points[0];
  const second = points[1] || first;
  if (!first) return null;
  const stroke = selected ? "#ffd75e" : item.color;
  const common = {
    fill: "none",
    stroke,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    strokeWidth: selected ? 2 : 1.5,
    vectorEffect: "non-scaling-stroke",
  };
  const selectDrawing = (event) => {
    if (activeTool !== "select") return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(item.id);
    onDrawingPointerDown(event, item.id);
  };
  let body = null;
  if (item.type === "horizontal") {
    body = <g>
      <line {...common} x1="0" x2={item.plotWidth} y1={first.y} y2={first.y} />
      <text
        className="oi-chart-drawing-label"
        fill={stroke}
        textAnchor="end"
        x={Math.max(48, item.plotWidth - 6)}
        y={Math.max(10, first.y - 4)}
      >
        {Number(first.price).toFixed(2)}
      </text>
    </g>;
  } else if (item.type === "rectangle") {
    body = <rect
      {...common}
      x={Math.min(first.x, second.x)}
      y={Math.min(first.y, second.y)}
      width={Math.abs(second.x - first.x)}
      height={Math.abs(second.y - first.y)}
      fill={`${item.color}18`}
    />;
  } else if (item.type === "brush") {
    body = <polyline {...common} points={points.map((point) => `${point.x},${point.y}`).join(" ")} />;
  } else if (item.type === "fib") {
    const fibLeft = Math.min(first.x, second.x);
    // TradingView keeps retracement levels visible as rays to the right. This
    // also makes a mostly vertical high-to-low gesture useful instead of
    // collapsing every level into a one-pixel line.
    const fibRight = Math.max(fibLeft + 1, Number(item.plotWidth || 0));
    body = <g>
      {item.fibLines.slice(0, -1).map((line, index) => {
        const nextLine = item.fibLines[index + 1];
        return <rect
          fill={FIB_LEVEL_COLORS[index]}
          fillOpacity=".075"
          height={Math.abs(nextLine.y - line.y)}
          key={`${item.id}-band-${line.level}`}
          pointerEvents="none"
          stroke="none"
          width={Math.max(1, fibRight - fibLeft)}
          x={fibLeft}
          y={Math.min(line.y, nextLine.y)}
        />;
      })}
      {item.fibLines.map((line, index) => <g key={`${item.id}-${line.level}`}>
        <line
          {...common}
          data-fib-level={line.level}
          stroke={FIB_LEVEL_COLORS[index]}
          strokeOpacity={line.level === 0 || line.level === 1 ? 1 : 0.78}
          x1={fibLeft}
          x2={fibRight}
          y1={line.y}
          y2={line.y}
        />
        <text
          className="oi-chart-drawing-label"
          fill={FIB_LEVEL_COLORS[index]}
          textAnchor="end"
          x={Math.max(fibLeft + 48, fibRight - 6)}
          y={Math.max(10, line.y - 4)}
        >
          {line.levelLabel}
        </text>
      </g>)}
      <line {...common} strokeDasharray="3 4" strokeOpacity=".62" x1={first.x} x2={second.x} y1={first.y} y2={second.y} />
    </g>;
  } else if (item.type === "text") {
    body = <g>
      <circle cx={first.x} cy={first.y} fill={item.color} r="2.5" />
      <text className="oi-chart-drawing-text" fill={stroke} x={first.x + 6} y={first.y - 6}>{item.text || "Note"}</text>
    </g>;
  } else if (item.type === "measure") {
    body = <g>
      <rect
        x={Math.min(first.x, second.x)}
        y={Math.min(first.y, second.y)}
        width={Math.abs(second.x - first.x)}
        height={Math.abs(second.y - first.y)}
        fill="rgba(68, 211, 255, .10)"
        stroke={stroke}
        strokeDasharray="4 4"
      />
      <text
        className="oi-chart-drawing-measure"
        x={(first.x + second.x) / 2}
        y={Math.max(12, Math.min(first.y, second.y) - 7)}
      >
        {item.measureLabel}
      </text>
    </g>;
  } else {
    body = <line {...common} x1={first.x} x2={second.x} y1={first.y} y2={second.y} />;
  }
  return (
    <g
      className={`oi-chart-drawing${selected ? " is-selected" : ""}${item.locked ? " is-locked" : ""}`}
      data-drawing-id={item.id}
      data-drawing-type={item.type}
      onPointerDown={selectDrawing}
      onDoubleClick={(event) => {
        if (activeTool !== "select" || item.type !== "text" || item.locked) return;
        event.preventDefault();
        event.stopPropagation();
        onEditText(item.id);
      }}
    >
      <g className="oi-chart-drawing-visible">{body}</g>
      <g className="oi-chart-drawing-hit-area">{body}</g>
      {selected && !item.locked ? points.slice(0, item.type === "brush" ? 0 : 2).map((point, pointIndex) => (
        <circle
          className="oi-chart-drawing-handle"
          cx={point.x}
          cy={point.y}
          key={`${item.id}-handle-${pointIndex}`}
          r="4.5"
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onHandlePointerDown(event, item.id, pointIndex);
          }}
        />
      )) : null}
    </g>
  );
}

export function OiChartDrawingTools({
  activeTool,
  color,
  collapsed,
  drawingGeometry,
  drawingsHidden,
  magnetEnabled,
  selectedDrawing,
  showToolbar = true,
  canUndo,
  canRedo,
  clipStyle,
  onToolChange,
  onColorChange,
  onToggleCollapsed,
  onToggleMagnet,
  onToggleVisibility,
  onToggleLock,
  onDeleteSelected,
  onUndo,
  onRedo,
  onZoomIn,
  onSelect,
  onDrawingPointerDown,
  onEditText,
  onHandlePointerDown,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onPointerCancel,
  onWheel,
}) {
  const interactive = activeTool !== "crosshair";
  const [visibleTooltip, setVisibleTooltip] = useState(null);
  const tooltipProps = (label) => ({
    title: label,
    onMouseEnter: (event) => {
      const bounds = event.currentTarget.getBoundingClientRect();
      setVisibleTooltip({
        label,
        left: Math.round(bounds.right + 8),
        top: Math.round(bounds.top + bounds.height / 2),
      });
    },
    onMouseLeave: () => setVisibleTooltip(null),
    onFocus: (event) => {
      const bounds = event.currentTarget.getBoundingClientRect();
      setVisibleTooltip({
        label,
        left: Math.round(bounds.right + 8),
        top: Math.round(bounds.top + bounds.height / 2),
      });
    },
    onBlur: () => setVisibleTooltip(null),
  });
  return (
    <>
      {showToolbar ? <aside className={`oi-chart-drawing-toolbar${collapsed ? " is-collapsed" : ""}`} aria-label="Chart drawing tools">
        <button
          className="oi-chart-drawing-toolbar-toggle"
          type="button"
          onClick={onToggleCollapsed}
          {...tooltipProps(collapsed ? "Show drawing tools" : "Hide drawing tools")}
          aria-label={collapsed ? "Show drawing tools" : "Hide drawing tools"}
          aria-expanded={!collapsed}
        >
          <PanelLeftClose size={17} />
        </button>
        {!collapsed ? <>
          <div className="oi-chart-drawing-tool-group">
            {DRAWING_TOOL_BUTTONS.map(({ key, label, icon: Icon }) => (
              <button
                className={activeTool === key ? "is-active" : ""}
                key={key}
                type="button"
                onClick={() => onToolChange(key)}
                {...tooltipProps(label)}
                aria-label={label}
                aria-pressed={activeTool === key}
              >
                <Icon size={17} />
              </button>
            ))}
          </div>
          <div className="oi-chart-drawing-tool-group">
            <button type="button" onClick={onZoomIn} {...tooltipProps("Zoom in")} aria-label="Zoom in"><ZoomIn size={17} /></button>
            <button
              className={magnetEnabled ? "is-active" : ""}
              type="button"
              onClick={onToggleMagnet}
              {...tooltipProps("Magnet: snap to candle OHLC")}
              aria-label="Toggle magnet snapping"
              aria-pressed={magnetEnabled}
            >
              <Magnet size={17} />
            </button>
            <label className="oi-chart-drawing-color" {...tooltipProps("Drawing color")}>
              <input type="color" value={color} onChange={(event) => onColorChange(event.target.value)} aria-label="Drawing color" />
              <i style={{ backgroundColor: color }} />
            </label>
          </div>
          <div className="oi-chart-drawing-tool-group">
            <button type="button" onClick={onUndo} disabled={!canUndo} {...tooltipProps("Undo drawing")} aria-label="Undo drawing"><Undo2 size={17} /></button>
            <button type="button" onClick={onRedo} disabled={!canRedo} {...tooltipProps("Redo drawing")} aria-label="Redo drawing"><Redo2 size={17} /></button>
            <button
              className={selectedDrawing?.locked ? "is-active" : ""}
              type="button"
              onClick={onToggleLock}
              disabled={!selectedDrawing}
              {...tooltipProps(selectedDrawing?.locked ? "Unlock selected drawing" : "Lock selected drawing")}
              aria-label={selectedDrawing?.locked ? "Unlock selected drawing" : "Lock selected drawing"}
            >
              <LockKeyhole size={17} />
            </button>
            <button
              className={drawingsHidden ? "is-active" : ""}
              type="button"
              onClick={onToggleVisibility}
              {...tooltipProps(drawingsHidden ? "Show drawings" : "Hide drawings")}
              aria-label={drawingsHidden ? "Show drawings" : "Hide drawings"}
            >
              {drawingsHidden ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
            <button
              className="is-danger"
              type="button"
              onClick={onDeleteSelected}
              disabled={!selectedDrawing}
              {...tooltipProps("Delete selected drawing")}
              aria-label="Delete selected drawing"
            >
              <Trash2 size={17} />
            </button>
          </div>
        </> : null}
      </aside> : null}
      {showToolbar && visibleTooltip ? (
        <span
          className="oi-chart-drawing-tooltip"
          role="tooltip"
          style={{ left: `${visibleTooltip.left}px`, top: `${visibleTooltip.top}px` }}
        >
          {visibleTooltip.label}
        </span>
      ) : null}
      <svg
        className={`oi-chart-drawing-layer${interactive ? " is-interactive" : ""}${drawingsHidden ? " is-hidden" : ""}`}
        style={clipStyle}
        aria-label="Chart drawings"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        onWheel={onWheel}
      >
        {!drawingsHidden ? drawingGeometry.map((item) => (
          <DrawingShape
            activeTool={activeTool}
            item={item}
            key={item.id}
            selected={selectedDrawing?.id === item.id}
            onSelect={onSelect}
            onDrawingPointerDown={onDrawingPointerDown}
            onEditText={onEditText}
            onHandlePointerDown={onHandlePointerDown}
          />
        )) : null}
      </svg>
    </>
  );
}

export { FIB_LEVEL_LABELS };
