import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";

import {
  SECTOR_SYMBOLS,
  PAGE_SORT_MODES,
  SORT_MODES,
  buildMarketSectorGroups,
  buildSectorPage,
  sectorPageSymbols,
  sortSectorGroups,
  sortSectorRows,
  cardHeight,
  formatMarketCap,
  formatSignedPct,
  heatLevel,
  rowLayout,
  squarify,
  tileFontSizes,
  tileLabelMode,
} from "./sectorHeatmap";

const POLL_MS = 8000;
const PREFS_KEY = "sectorHeatmapPrefs";
// Robinhood: light rounded cards, 3px gutters. TOS: dark, tight 1px grid.
const SORT_LABELS = { default: "Default", high: "High", low: "Low" };
const SORT_TITLES = {
  default: "Robinhood order",
  high: "Best sector move first",
  low: "Worst sector move first",
};
const PAGE_SORT_LABELS = { size: "Size", high: "High", low: "Low" };
const PAGE_SORT_TITLES = {
  size: "Biggest company first",
  high: "Best move first",
  low: "Worst move first",
};
const STYLES = {
  rh: { label: "Robinhood", short: "RH", gap: 3 },
  tos: { label: "thinkorswim", short: "TOS", gap: 1 },
};
// One request for every card's leaders plus the sector ETFs.
const SYMBOL_QUERY = encodeURIComponent(SECTOR_SYMBOLS.join(","));

function readPrefs() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
    return {
      style: STYLES[parsed?.style] ? parsed.style : "rh",
      sort: SORT_MODES.includes(parsed?.sort) ? parsed.sort : "default",
      pageSort: PAGE_SORT_MODES.includes(parsed?.pageSort) ? parsed.pageSort : "size",
    };
  } catch {
    return { style: "rh", sort: "default", pageSort: "size" };
  }
}

function tileTitle(tile) {
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

function moveLabel(changePct) {
  const heat = heatLevel(changePct);
  const arrow = heat.tone === "up" ? "▲ " : heat.tone === "down" ? "▼ " : "";
  return { heat, text: `${arrow}${changePct === null ? "--" : formatSignedPct(changePct).replace(/^[+-]/, "")}` };
}

// The heatmap itself: RH = Robinhood's full-width rows, TOS = thinkorswim's
// squarified treemap. Used by the cards and by a sector's own page.
function SectorTiles({ tiles, gap, style, activeSymbol, onSelect, large = false, label }) {
  const [tilesRef, width] = useElementWidth();
  const height = cardHeight(tiles.length, width, large);
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
  return (
    <div className="sector-heatmap-tiles" ref={tilesRef} style={{ height }}>
      {layout.map(({ item, x, y, width: tileWidth, height: tileHeight, row }) => {
        const heat = heatLevel(item.changePct);
        const innerWidth = Math.max(0, tileWidth - gap);
        const innerHeight = Math.max(0, tileHeight - gap);
        const mode = tileLabelMode(innerWidth, innerHeight, style);
        const fonts = rowFont[row] || tileFontSizes(innerWidth, innerHeight, style);
        const active = item.symbol === activeSymbol;
        return (
          <button
            type="button"
            key={item.symbol}
            className={`sector-heatmap-tile is-${heat.tone} is-level-${heat.level}${active ? " is-active" : ""}`}
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
            aria-label={`${item.symbol} ${formatSignedPct(item.changePct)}${label ? ` in ${label}` : ""}`}
            onClick={() => onSelect?.(item.symbol)}
          >
            {mode !== "none" ? <b style={{ fontSize: fonts.symbol }}>{item.symbol}</b> : null}
            {mode === "full" ? <span style={{ fontSize: fonts.pct }}>{formatSignedPct(item.changePct)}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

function SectorCard({ group, gap, style, activeSymbol, onOpen, onSelect }) {
  const move = moveLabel(group.changePct);
  return (
    <article className="sector-heatmap-card">
      <header>
        <button
          type="button"
          className="sector-heatmap-card-title"
          onClick={() => onOpen(group.name)}
          title={`Open ${group.name}: every big name in the sector, sortable`}
        >
          <b>{group.name}</b>
          <ChevronRight size={16} strokeWidth={2.5} aria-hidden="true" />
          {style === "rh" ? null : <small>{group.tiles.length}</small>}
        </button>
        <span
          className={`sector-heatmap-card-move is-${move.heat.tone}`}
          title={group.moveSource === "etf"
            ? `${group.etf} (sector ETF) today · ${group.up} up / ${group.down} down below`
            : `Weighted move of the names below (${group.etf} has no quote yet)`}
        >
          {move.text}
        </span>
      </header>
      <SectorTiles tiles={group.tiles} gap={gap} style={style} activeSymbol={activeSymbol} onSelect={onSelect} label={group.name} />
    </article>
  );
}

// A sector's own page, like tapping a sector on Robinhood: the sector's move,
// a bigger heatmap of all its big names, and a sortable list of them.
function SectorPage({ group, gap, style, activeSymbol, sort, onSort, onBack, onSelect }) {
  const move = moveLabel(group.changePct);
  const rows = sortSectorRows(group.tiles, sort);
  return (
    <div className="sector-page">
      <button type="button" className="sector-page-back" onClick={onBack}>
        <ChevronLeft size={18} strokeWidth={2.5} aria-hidden="true" />
        <span>All sectors</span>
      </button>
      <article className="sector-heatmap-card">
        <header className="sector-page-head">
          <div>
            <h2>{group.name}</h2>
            <small>
              {group.moveSource === "etf" ? `${group.etf} today` : "Weighted move today"}
              {" · "}{group.up} up · {group.down} down · {group.tiles.length} stocks
            </small>
          </div>
          <span className={`sector-heatmap-card-move is-${move.heat.tone}`}>{move.text}</span>
        </header>
        <SectorTiles tiles={group.tiles} gap={gap} style={style} activeSymbol={activeSymbol} onSelect={onSelect} label={group.name} large />
      </article>
      <article className="sector-heatmap-card sector-page-list">
        <header>
          <b>Stocks</b>
          <span className="sector-heatmap-switch" role="group" aria-label="Sort stocks">
            {PAGE_SORT_MODES.map((mode) => (
              <button key={mode} type="button" aria-pressed={sort === mode} title={PAGE_SORT_TITLES[mode]} onClick={() => onSort(mode)}>
                {PAGE_SORT_LABELS[mode]}
              </button>
            ))}
          </span>
        </header>
        <ul>
          {rows.map((row) => {
            const heat = heatLevel(row.changePct);
            return (
              <li key={row.symbol}>
                <button
                  type="button"
                  className={row.symbol === activeSymbol ? "is-active" : ""}
                  onClick={() => onSelect?.(row.symbol)}
                  title={tileTitle(row)}
                >
                  <b>{row.symbol}</b>
                  <small>{formatMarketCap(row.marketCap)}{row.estimatedCap ? "*" : ""}</small>
                  <span className="sector-page-last">{row.lastPrice !== null ? `$${row.lastPrice.toFixed(2)}` : "--"}</span>
                  <span className={`sector-page-chg is-${heat.tone}`}>{formatSignedPct(row.changePct)}</span>
                </button>
              </li>
            );
          })}
        </ul>
        <p className="sector-page-note">* approximate market cap until the live figure arrives</p>
      </article>
    </div>
  );
}

// variant "dock": a panel stacked above the option chain (desktop "+" menu).
// variant "page": the full Sectors page (phone More sheet, desktop sidebar).
export default function SectorHeatmapPanel({ activeSymbol, onSelect, onClose, variant = "dock" }) {
  const [quotes, setQuotes] = useState({});
  const [pageQuotes, setPageQuotes] = useState({});
  const [openSector, setOpenSector] = useState(null);
  const [prefs, setPrefs] = useState(readPrefs);
  const rootRef = useRef(null);
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

  // A sector's page needs its extra names quoted too - only while it is open,
  // so the grid's polling cost does not change.
  useEffect(() => {
    if (!openSector) {
      setPageQuotes({});
      return undefined;
    }
    const symbols = sectorPageSymbols(openSector);
    if (!symbols.length) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`/api/watchlist-quotes?profile=1&symbols=${encodeURIComponent(symbols.join(","))}`);
        const payload = await response.json();
        if (cancelled || !response.ok || !Array.isArray(payload?.rows)) return;
        if (!payload.rows.some((row) => row?.lastPrice !== null && row?.lastPrice !== undefined)) return;
        setPageQuotes(Object.fromEntries(payload.rows.map((row) => [String(row?.symbol || "").toUpperCase(), row])));
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
  }, [openSector]);

  // Opening or leaving a sector starts at its top, wherever the panel lives
  // (the Sectors page scroller, or the dock panel's own body).
  const scrollToTop = () => {
    const root = rootRef.current;
    if (!root) return;
    root.closest(".sectors-view")?.scrollTo({ top: 0 });
    const body = root.querySelector(".sector-heatmap-body");
    if (body) body.scrollTop = 0;
  };
  const openPage = (name) => { setOpenSector(name); window.requestAnimationFrame(scrollToTop); };
  const closePage = () => { setOpenSector(null); window.requestAnimationFrame(scrollToTop); };

  const groups = useMemo(() => sortSectorGroups(buildMarketSectorGroups(quotes), prefs.sort), [quotes, prefs.sort]);
  const page = useMemo(
    () => (openSector ? buildSectorPage(openSector, { ...quotes, ...pageQuotes }) : null),
    [openSector, quotes, pageQuotes],
  );

  return (
    <section
      ref={rootRef}
      className={`${variant === "page" ? "sector-heatmap-page" : "charts-oi-chain-dock-panel"} sector-heatmap-panel is-style-${prefs.style}${page ? " is-sector-open" : ""}`}
      aria-label="Sector panel"
    >
      <header>
        <b>SECTORS</b>
        <small>{groups.length} sectors · top stocks</small>
        {page ? null : <span className="sector-heatmap-switch" role="group" aria-label="Sort sectors">
          {SORT_MODES.map((mode) => (
            <button key={mode} type="button" aria-pressed={prefs.sort === mode} title={SORT_TITLES[mode]} onClick={() => updatePrefs({ sort: mode })}>
              {SORT_LABELS[mode]}
            </button>
          ))}
        </span>}
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
        {page ? (
          <SectorPage
            group={page}
            gap={styleSpec.gap}
            style={prefs.style}
            activeSymbol={activeSymbol}
            sort={prefs.pageSort}
            onSort={(mode) => updatePrefs({ pageSort: mode })}
            onBack={closePage}
            onSelect={onSelect}
          />
        ) : groups.map((group) => (
          <SectorCard
            key={group.name}
            group={group}
            gap={styleSpec.gap}
            style={prefs.style}
            activeSymbol={activeSymbol}
            onOpen={openPage}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}
