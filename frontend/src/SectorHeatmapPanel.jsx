import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, X } from "lucide-react";

import {
  buildSectorGroups,
  cardHeight,
  formatMarketCap,
  formatSignedPct,
  heatLevel,
  squarify,
  tileLabelMode,
  visibleTiles,
} from "./sectorHeatmap";

const POLL_MS = 8000;
const TILE_GAP = 2;

function tileTitle(tile) {
  if (tile.more) return `${tile.more} more - open the full card`;
  const parts = [
    tile.symbol,
    tile.lastPrice !== null ? `Last ${tile.lastPrice.toFixed(2)}` : null,
    tile.change !== null ? `Chg ${tile.change > 0 ? "+" : ""}${tile.change.toFixed(2)}` : null,
    `Chg% ${formatSignedPct(tile.changePct)}`,
    `Mkt cap ${tile.estimatedCap ? "unknown (sized as group median)" : formatMarketCap(tile.marketCap)}`,
  ];
  return parts.filter(Boolean).join("\n");
}

function SectorCard({ group, width, activeSymbol, expanded, onToggle, onSelect }) {
  const tiles = visibleTiles(group.tiles, expanded);
  const height = cardHeight(tiles.length, width, expanded);
  const layout = squarify(tiles, { x: 0, y: 0, width, height });
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
          <ChevronRight size={12} aria-hidden="true" />
          <small>{group.tiles.length}</small>
        </button>
        <span className={`sector-heatmap-card-move is-${groupHeat.tone}`} title={`${group.up} up · ${group.down} down (market-cap weighted move)`}>
          {groupHeat.tone === "up" ? "▲ " : groupHeat.tone === "down" ? "▼ " : ""}
          {formatSignedPct(group.changePct).replace(/^[+-]/, "")}
        </span>
      </header>
      <div className="sector-heatmap-tiles" style={{ height }}>
        {layout.map(({ item, x, y, width: tileWidth, height: tileHeight }) => {
          const heat = heatLevel(item.changePct);
          const mode = tileLabelMode(tileWidth - TILE_GAP, tileHeight - TILE_GAP);
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
                width: `calc(${(tileWidth / width) * 100}% - ${TILE_GAP}px)`,
                height: Math.max(0, tileHeight - TILE_GAP),
              }}
              title={tileTitle(item)}
              aria-label={item.more ? `${item.more} more in ${group.name}` : `${item.symbol} ${formatSignedPct(item.changePct)}`}
              onClick={() => (item.more ? onToggle() : onSelect?.(item.symbol))}
            >
              {mode !== "none" ? <b>{item.symbol}</b> : null}
              {mode === "full" && !item.more ? <span>{formatSignedPct(item.changePct)}</span> : null}
            </button>
          );
        })}
      </div>
    </article>
  );
}

export default function SectorHeatmapPanel({ symbols, activeSymbol, onSelect, onClose }) {
  const [quotes, setQuotes] = useState({});
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [width, setWidth] = useState(0);
  const bodyRef = useRef(null);
  const symbolKey = symbols.join(",");

  useEffect(() => {
    if (!symbolKey) {
      setQuotes({});
      setLoaded(true);
      return undefined;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/watchlist-quotes?profile=1&symbols=${encodeURIComponent(symbolKey)}`);
        const payload = await response.json();
        if (cancelled || !response.ok || !Array.isArray(payload?.rows)) return;
        setQuotes(Object.fromEntries(payload.rows.map((row) => [String(row?.symbol || "").toUpperCase(), row])));
        setLoaded(true);
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
  }, [symbolKey]);

  useEffect(() => {
    const element = bodyRef.current;
    if (!element) return undefined;
    const measure = () => {
      // Card margin (2 x 6px) + padding (2 x 8px) + border (2 x 1px) come off
      // the tile area. Only the aspect ratio depends on this (tiles are
      // positioned in %), so a small mismatch cannot spill past the card.
      const next = Math.max(0, Math.floor(element.clientWidth - 30));
      setWidth((current) => (Math.abs(current - next) >= 1 ? next : current));
    };
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // symbolKey, not symbols: the parent rebuilds the array on every render.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const groups = useMemo(() => buildSectorGroups(symbolKey ? symbolKey.split(",") : [], quotes), [symbolKey, quotes]);

  return (
    <section className="charts-oi-chain-dock-panel sector-heatmap-panel" aria-label="Sector panel">
      <header>
        <b>SECTOR</b>
        <small>{groups.length} groups · {symbols.length}</small>
        <button type="button" onClick={onClose} title="Remove sector panel" aria-label="Remove sector panel"><X size={13} /></button>
      </header>
      <div className="sector-heatmap-body" ref={bodyRef}>
        {!symbols.length ? (
          <span className="charts-oi-chain-dock-empty">Add tickers to your watchlist to see the sector map.</span>
        ) : !loaded ? (
          <span className="charts-oi-chain-dock-empty">Loading sector map…</span>
        ) : width > 0 ? (
          groups.map((group) => (
            <SectorCard
              key={group.name}
              group={group}
              width={width}
              activeSymbol={activeSymbol}
              expanded={Boolean(expanded[group.name])}
              onToggle={() => setExpanded((current) => ({ ...current, [group.name]: !current[group.name] }))}
              onSelect={onSelect}
            />
          ))
        ) : null}
      </div>
    </section>
  );
}
