import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, X } from "lucide-react";

import {
  SECTOR_SYMBOLS,
  buildMarketSectorGroups,
  cardHeight,
  formatMarketCap,
  formatSignedPct,
  heatLevel,
  rowLayout,
  squarify,
  tileFontSizes,
  tileLabelMode,
  visibleTiles,
} from "./sectorHeatmap";

const POLL_MS = 8000;
const PREFS_KEY = "sectorHeatmapPrefs";
// Robinhood: light rounded cards, 3px gutters. TOS: dark, tight 1px grid.
const STYLES = {
  rh: { label: "Robinhood", short: "RH", gap: 3 },
  tos: { label: "thinkorswim", short: "TOS", gap: 1 },
};
// One request for every card's leaders plus the sector ETFs.
const SYMBOL_QUERY = encodeURIComponent(SECTOR_SYMBOLS.join(","));

function readPrefs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    return { style: STYLES[parsed?.style] ? parsed.style : "rh" };
  } catch {
    return { style: "rh" };
  }
}

function tileTitle(tile) {
  if (tile.more) return `${tile.more} more - open the full card`;
  const parts = [
    tile.symbol,
    tile.lastPrice !== null ? `Last ${tile.lastPrice.toFixed(2)}` : null,
    tile.change !== null ? `Chg ${tile.change > 0 ? "+" : ""}${tile.change.toFixed(2)}` : null,
    `Chg% ${formatSignedPct(tile.changePct)}`,
    `Mkt cap ${formatMarketCap(tile.marketCap)}${tile.estimatedCap ? " (approx.)" : ""}`,
  ];
  return parts.filter(Boolean).join("\n");
}

// Width of an element, kept current with ResizeObserver.
function useElementWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => {
      const next = Math.floor(element.clientWidth);
      setWidth((current) => (current !== next ? next : current));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function SectorCard({ group, gap, style, activeSymbol, expanded, onToggle, onSelect }) {
  const [tilesRef, width] = useElementWidth();
  const tiles = visibleTiles(group.tiles, expanded);
  const height = cardHeight(tiles.length, width, expanded);
  // RH: Robinhood's full-width rows. TOS: thinkorswim's squarified treemap.
  const layoutTiles = style === "rh" ? rowLayout : squarify;
  const layout = width > 0 ? layoutTiles(tiles, { x: 0, y: 0, width, height }) : [];
  // Robinhood prints every tile in a row at one size: the size that fits the
  // row's narrowest tile.
  const rowFont = {};
  if (style === "rh") {
    for (const tile of layout) {
      const size = tileFontSizes(Math.max(0, tile.width - gap), Math.max(0, tile.height - gap), style);
      if (!rowFont[tile.row] || size.symbol < rowFont[tile.row].symbol) rowFont[tile.row] = size;
    }
  }
  const groupHeat = heatLevel(group.changePct);
  const canExpand = group.tiles.length > tiles.length || expanded;

  return (
    <article className="sector-heatmap-card">
      <header>
        <button
          type="button"
          className={`sector-heatmap-card-title${expanded ? " is-open" : ""}`}
          onClick={canExpand ? onToggle : undefined}
          disabled={!canExpand}
          title={canExpand ? (expanded ? "Show the biggest names only" : "Show every name in this group") : `${group.tiles.length} names`}
        >
          <b>{group.name}</b>
          <ChevronRight size={16} strokeWidth={2.5} aria-hidden="true" />
          {style === "rh" ? null : <small>{group.tiles.length}</small>}
        </button>
        <span
          className={`sector-heatmap-card-move is-${groupHeat.tone}`}
          title={group.moveSource === "etf"
            ? `${group.etf} (sector ETF) today · ${group.up} up / ${group.down} down below`
            : `Weighted move of the names below (${group.etf} has no quote yet)`}
        >
          {groupHeat.tone === "up" ? "▲ " : groupHeat.tone === "down" ? "▼ " : ""}
          {group.changePct === null ? "--" : formatSignedPct(group.changePct).replace(/^[+-]/, "")}
        </span>
      </header>
      <div className="sector-heatmap-tiles" ref={tilesRef} style={{ height }}>
        {layout.map(({ item, x, y, width: tileWidth, height: tileHeight, row }) => {
          const heat = heatLevel(item.changePct);
          const innerWidth = Math.max(0, tileWidth - gap);
          const innerHeight = Math.max(0, tileHeight - gap);
          const mode = tileLabelMode(innerWidth, innerHeight, style);
          const fonts = rowFont[row] || tileFontSizes(innerWidth, innerHeight, style);
          const active = !item.more && item.symbol === activeSymbol;
          return (
            <button
              type="button"
              key={item.symbol}
              className={`sector-heatmap-tile is-${item.more ? "more" : heat.tone} is-level-${heat.level}${active ? " is-active" : ""}`}
              // Horizontal position in % of the tile area, so the layout fits the
              // card exactly even if the measured width is a pixel or two off;
              // the measured width only sets the aspect ratio.
              style={{
                left: `${(x / width) * 100}%`,
                top: y,
                width: `calc(${(tileWidth / width) * 100}% - ${gap}px)`,
                height: innerHeight,
              }}
              title={tileTitle(item)}
              aria-label={item.more ? `${item.more} more in ${group.name}` : `${item.symbol} ${formatSignedPct(item.changePct)}`}
              onClick={() => (item.more ? onToggle() : onSelect?.(item.symbol))}
            >
              {mode !== "none" ? <b style={{ fontSize: fonts.symbol }}>{item.symbol}</b> : null}
              {mode === "full" && !item.more ? <span style={{ fontSize: fonts.pct }}>{formatSignedPct(item.changePct)}</span> : null}
            </button>
          );
        })}
      </div>
    </article>
  );
}

// variant "dock": a panel stacked above the option chain (desktop "+" menu).
// variant "page": the full Sectors page (phone More sheet, desktop sidebar).
export default function SectorHeatmapPanel({ activeSymbol, onSelect, onClose, variant = "dock" }) {
  const [quotes, setQuotes] = useState({});
  const [expanded, setExpanded] = useState({});
  const [prefs, setPrefs] = useState(readPrefs);
  const updatePrefs = (patch) => setPrefs((current) => {
    const next = { ...current, ...patch };
    try { window.localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* storage can be disabled */ }
    return next;
  });
  const styleSpec = STYLES[prefs.style];
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/watchlist-quotes?profile=1&symbols=${SYMBOL_QUERY}`);
        const payload = await response.json();
        if (cancelled || !response.ok || !Array.isArray(payload?.rows)) return;
        // Keep the map empty until at least one price arrives, so an all-null
        // answer (feed down) does not hide every tile as "unquoted".
        if (!payload.rows.some((row) => row?.lastPrice !== null && row?.lastPrice !== undefined)) return;
        setQuotes(Object.fromEntries(payload.rows.map((row) => [String(row?.symbol || "").toUpperCase(), row])));
      } catch {
        // Keep the last map on transient fetch failures.
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const groups = useMemo(() => buildMarketSectorGroups(quotes), [quotes]);

  return (
    <section
      className={`${variant === "page" ? "sector-heatmap-page" : "charts-oi-chain-dock-panel"} sector-heatmap-panel is-style-${prefs.style}`}
      aria-label="Sector panel"
    >
      <header>
        <b>SECTORS</b>
        <small>{groups.length} sectors · top stocks</small>
        <span className="sector-heatmap-switch" role="group" aria-label="Heatmap style">
          {Object.entries(STYLES).map(([key, spec]) => (
            <button key={key} type="button" aria-pressed={prefs.style === key} title={`${spec.label} look`} onClick={() => updatePrefs({ style: key })}>
              {spec.short}
            </button>
          ))}
        </span>
        {onClose ? <button type="button" onClick={onClose} title="Remove sector panel" aria-label="Remove sector panel"><X size={13} /></button> : null}
      </header>
      <div className="sector-heatmap-body">
        {groups.map((group) => (
          <SectorCard
            key={group.name}
            group={group}
            gap={styleSpec.gap}
            style={prefs.style}
            activeSymbol={activeSymbol}
            expanded={Boolean(expanded[group.name])}
            onToggle={() => setExpanded((current) => ({ ...current, [group.name]: !current[group.name] }))}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}
