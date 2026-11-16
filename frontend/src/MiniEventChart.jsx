// A small read-only candle chart for the Technical analysis card (2026-11-11), with volume bars, the day's events as
// markers (green dot bullish, red square bearish, grey diamond neutral, "E" earnings) and the support / resistance
// lines labelled with their price. It reuses the chart library the app already ships; it never fetches anything and
// cannot be scrolled or zoomed, so it can sit inside a scrolling chat. Tapping a marker names the event and its time.
import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, HistogramSeries, LineStyle, createChart } from "lightweight-charts";
import { priceText } from "./miniEventChart.js";

const STEP = 11;   // px between stacked markers on one bar

export default function MiniEventChart({ frame, height = 200 }) {
  const box = useRef(null);
  const [spots, setSpots] = useState([]);
  const [picked, setPicked] = useState(null);

  useEffect(() => {
    const el = box.current;
    if (!el || !frame?.bars?.length) return undefined;
    const chart = createChart(el, {
      autoSize: true,
      height,
      layout: { background: { color: "transparent" }, textColor: "#9a9aa6", fontSize: 10, attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(92, 92, 103, .22)" } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.26 } },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true, lockVisibleTimeRangeOnResize: true },
      handleScroll: false,
      handleScale: false,
      crosshair: { vertLine: { visible: false, labelVisible: false }, horzLine: { visible: false, labelVisible: false } },
    });
    // The app's own candle colours: cyan up, magenta down.
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#22d3ee", downColor: "#e879f9", borderUpColor: "#22d3ee", borderDownColor: "#e879f9", wickUpColor: "#22d3ee", wickDownColor: "#e879f9",
      priceLineVisible: false, lastValueVisible: false,
    });
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    candles.setData(frame.bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    volume.setData(frame.bars.map(({ time, volume: v, open, close }) => ({ time, value: v, color: close >= open ? "rgba(34, 211, 238, .35)" : "rgba(232, 121, 249, .35)" })));
    for (const [line, color] of [[frame.support, "#22c55e"], [frame.resistance, "#ef4444"]]) {
      if (line) candles.createPriceLine({ price: line.price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: `${line.label} ${priceText(line.price)}` });
    }
    chart.timeScale().fitContent();

    // Markers are buttons laid over the chart, so a tap can name the event (the library's own markers cannot be tapped).
    let frameId = 0;
    const place = () => {
      cancelAnimationFrame(frameId);
      frameId = requestAnimationFrame(() => {
        const bottom = el.clientHeight - 34;
        setSpots(frame.markers.map((m, i) => {
          const x = chart.timeScale().timeToCoordinate(m.barTime);
          const y = m.side === "bottom" ? bottom - m.stack * STEP : candles.priceToCoordinate(m.price);
          if (x === null || y === null) return null;
          const top = m.side === "below" ? y + 8 + m.stack * STEP : m.side === "above" ? y - 8 - m.stack * STEP : y;
          return { ...m, i, x, y: top };
        }).filter(Boolean));
      });
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(place);
    chart.timeScale().subscribeSizeChange(place);
    place();
    return () => {
      cancelAnimationFrame(frameId);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(place);
      chart.timeScale().unsubscribeSizeChange(place);
      chart.remove();
      setSpots([]);
      setPicked(null);
    };
  }, [frame, height]);

  if (!frame?.bars?.length) return null;
  return (
    <div className="mini-chart" data-testid="mini-chart">
      <div ref={box} className="mini-chart-canvas" style={{ height }} />
      <div className="mini-chart-marks">
        {spots.map((m) => (
          <button key={m.i} type="button" className={`mini-mark is-${m.shape}`} data-testid="mini-chart-marker" data-kind={m.kind}
            style={{ left: m.x, top: m.y, "--mark": m.color }} aria-label={`${m.name}, ${m.when}`} aria-pressed={picked?.i === m.i}
            onClick={() => setPicked((p) => (p?.i === m.i ? null : m))}>
            {m.shape === "earnings" ? "E" : null}
          </button>
        ))}
      </div>
      <p className="mini-chart-pick" data-testid="mini-chart-pick" role="status">
        {picked ? <><b>{picked.name}</b> · {picked.when}</> : frame.markers.length ? "Tap a marker to see the event." : ""}
      </p>
    </div>
  );
}
