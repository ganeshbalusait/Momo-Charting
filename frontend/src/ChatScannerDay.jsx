// "Nvda today scanner" (2026-11-11): one ticker's whole day on the scanner, on
// every board and side, in the scanner's own columns. It opens as a full sheet
// with ONE scroller and never inside the chat's log: nested scrollers that mix
// axes cost MomX four incidents. The server only says which ticker, day and
// boards; the rows are read from the same route the History tab reads and drawn
// by the History table itself, so they cannot differ. It only reads.
import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { openAppLink } from "./appLinks.js";
import { dayLabel, historyRequest } from "./momxHistory.js";
import { MomxDayExplain, MomxDayTable, loadMomxDayData } from "./MomxScannerPanel.jsx";

// <day-sheet-logic> Plain functions; chatScannerDay.test.js lifts this block and runs it.
const MAX_SECTIONS = 8;
const FETCHES_AT_ONCE = 2;
const FETCH_TIMEOUT_MS = 45_000;

/** The boards to show, in the order the server put them: calls first unless he asked for puts. */
function daySections(table) {
  const seen = new Set();
  const out = [];
  for (const raw of Array.isArray(table && table.sections) ? table.sections : []) {
    if (!raw || typeof raw.list !== "string" || !raw.list.trim()) continue;
    const side = raw.side === "bear" ? "bear" : "bull";
    const id = side + "|" + raw.list;
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ id, list: raw.list, side });
    if (out.length >= MAX_SECTIONS) break;
  }
  return out;
}

const sectionName = (section) => section.list + " " + (section.side === "bear" ? "puts" : "calls");

/** The History route's own URL. Puts travel as dir=bear; anything else silently returns the calls. */
function sectionUrl(request, table, section, offset) {
  return request(section.list, { symbol: table.ticker, date: table.day, direction: section.side, offset }).url;
}

const plural = (count, word) => count + " " + word + (count === 1 ? "" : "s");

/** "172 scans, 94 saved rows". Saved rows are not every scan, so both are said. */
function sectionCounts(payload) {
  const rows = Array.isArray(payload && payload.rows) ? payload.rows : [];
  const saved = Number(payload && payload.totalRows) || rows.length;
  let scans = null;
  for (const row of rows) {
    const hits = Number(row && row.hits);
    if (Number.isFinite(hits) && hits > 0 && (scans === null || hits > scans)) scans = hits;
  }
  return (scans === null ? "" : plural(scans, "scan") + ", ") + plural(saved, "saved row");
}

const notOnBoard = (section) => "Not on " + sectionName(section) + " that day";

const sectionIsEmpty = (payload) => !Array.isArray(payload && payload.rows) || payload.rows.length === 0;

/** Where the next page starts, or null when this is all of it. */
function nextOffset(payload) {
  if (!payload || !payload.hasMore) return null;
  return (Number(payload.offset) || 0) + (Array.isArray(payload.rows) ? payload.rows.length : 0);
}

/** A further page added on; the counters are the newest page's. */
function addPage(held, page) {
  return { ...page, offset: 0, rows: [...(held.rows || []), ...(page.rows || [])], hasMore: Boolean(page.hasMore) };
}

/** Runs at most `limit` tasks at a time, first asked first run. */
function createLimiter(limit) {
  const waiting = [];
  let running = 0;
  const next = () => {
    while (running < limit && waiting.length > 0) {
      const { task, resolve, reject } = waiting.shift();
      running += 1;
      Promise.resolve().then(task).then(resolve, reject).finally(() => {
        running -= 1;
        next();
      });
    }
  };
  return (task) => new Promise((resolve, reject) => {
    waiting.push({ task, resolve, reject });
    next();
  });
}

/** The History link for one moment. Clock "24:xx" is how some browsers write just after midnight. */
function momentLink(table, section, clock) {
  const time = String(clock || "").replace(/^24:/, "00:");
  return {
    label: "Open " + table.ticker + " on " + section.list + " History" + (time ? ", " + time : ""),
    view: "MomX Scanner", list: section.list, side: section.side, tab: "history", day: table.day, ticker: table.ticker,
    ...(time ? { time } : {}),
  };
}
// </day-sheet-logic>

// The scanner's own stylesheet is scoped to .momx-scanner-panel, so the tables
// wear that; only the sheet's frame is drawn here. Over the chat window (and a
// full-screen chart), under the tap-to-explain box.
const SHEET_STYLE = Object.freeze({ position: "fixed", inset: 0, zIndex: 2400, display: "flex", flexDirection: "column", background: "#0c0e13", color: "#e7e9f0" });
const HEAD_STYLE = Object.freeze({ display: "flex", flex: "0 0 auto", alignItems: "center", gap: 10, padding: "8px 8px 8px 14px", borderBottom: "1px solid rgba(92, 92, 103, .45)" });
const TITLE_STYLE = Object.freeze({ flex: "1 1 auto", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 14 });
const CLOSE_STYLE = Object.freeze({ display: "grid", flex: "0 0 auto", placeItems: "center", width: 36, height: 36, padding: 0, border: 0, borderRadius: 8, background: "transparent", color: "#b8b8c2", cursor: "pointer" });
const VIEW_STYLE = Object.freeze({ flex: "1 1 auto", minHeight: 0 });
const SECTION_STYLE = Object.freeze({ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 10px", marginTop: 6, fontSize: 12 });
const COUNT_STYLE = Object.freeze({ color: "#8b8f9e" });
const LINE_STYLE = Object.freeze({ margin: 0, color: "#8b8f9e", fontSize: 12 });

const DaySection = memo(function DaySection({ table, section, state, dataTick, onOpen, onRetry, onMore }) {
  const name = sectionName(section);
  const openTime = useCallback((entry) => onOpen(momentLink(table, section, entry.timeLabel)), [onOpen, table, section]);
  const attrs = { "data-testid": "chat-day-section", "data-list": section.list, "data-side": section.side };
  if (state.status === "error") {
    return (
      <p {...attrs} style={LINE_STYLE} role="alert">
        Could not load {name}. <button type="button" className="momx-btn" onClick={() => onRetry(section)}>Try again</button>
      </p>
    );
  }
  if (state.status !== "done") return <p {...attrs} style={LINE_STYLE}>Loading {name}...</p>;
  if (sectionIsEmpty(state.payload)) return <p {...attrs} data-folded="true" style={LINE_STYLE}>{notOnBoard(section)}</p>;
  const more = nextOffset(state.payload);
  return (
    <Fragment>
      <div {...attrs} style={SECTION_STYLE}>
        <b>{name}</b>
        <span style={COUNT_STYLE} data-testid="chat-day-counts">{sectionCounts(state.payload)}</span>
        <button type="button" className="momx-btn" onClick={() => onOpen(momentLink(table, section, ""))}>Open on History</button>
      </div>
      <MomxDayTable rows={state.payload.rows} onOpenTime={openTime} dataTick={dataTick} />
      {more === null ? null : (
        <div>
          <button type="button" className="momx-btn" data-testid="chat-day-more" disabled={Boolean(state.busy)} onClick={() => onMore(section, more)}>
            {state.busy ? "Loading..." : "Load more rows"}
          </button>
        </div>
      )}
    </Fragment>
  );
});

export default function ChatScannerDay({ table, onClose }) {
  const sections = useMemo(() => daySections(table), [table]);
  const ticker = table && table.ticker;
  const day = table && table.day;
  const [states, setStates] = useState({});
  const [dataTick, setDataTick] = useState(0);
  const closeRef = useRef(null);
  const reader = useRef(null);
  const close = useCallback(() => { if (onClose) onClose(); }, [onClose]);

  const signature = JSON.stringify([ticker, day, sections]);
  useEffect(() => {
    const limit = createLimiter(FETCHES_AT_ONCE);
    const controllers = new Set();
    let alive = true;
    const patch = (id, change) => {
      if (alive) setStates((current) => ({ ...current, [id]: { ...current[id], ...change } }));
    };
    // One timed read through the limiter. A read that never settles would hold a slot for good.
    const read = (section, offset) => limit(async () => {
      if (!alive) return null;
      const controller = new AbortController();
      controllers.add(controller);
      const timer = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
      try {
        const response = await fetch(sectionUrl(historyRequest, { ticker, day }, section, offset), { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("history answered " + response.status);
        return await response.json();
      } finally {
        window.clearTimeout(timer);
        controllers.delete(controller);
      }
    });
    const load = (section) => {
      patch(section.id, { status: "loading" });
      read(section, 0).then(
        (payload) => { if (payload) patch(section.id, { status: "done", payload }); },
        () => patch(section.id, { status: "error" }),
      );
    };
    const more = (section, offset) => {
      patch(section.id, { busy: true });
      read(section, offset).then(
        (page) => {
          if (!alive || !page) return;
          setStates((current) => {
            const held = current[section.id];
            return { ...current, [section.id]: { ...held, busy: false, payload: addPage(held.payload, page) } };
          });
        },
        () => patch(section.id, { busy: false }),
      );
    };
    reader.current = { load, more };
    // Next tick, so React's trial mount in development does not read the day files twice.
    const start = window.setTimeout(() => {
      sections.forEach(load);
      loadMomxDayData().then((changed) => { if (alive && changed) setDataTick((n) => n + 1); }, () => {});
    }, 0);
    return () => {
      alive = false;
      window.clearTimeout(start);
      controllers.forEach((controller) => controller.abort());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  useEffect(() => {
    const before = document.activeElement;
    closeRef.current?.focus({ preventScroll: true });
    return () => { if (before && typeof before.focus === "function") before.focus({ preventScroll: true }); };
  }, []);

  useEffect(() => {
    // Caught first, so Esc shuts this sheet and nothing behind it. The chat window
    // leaves Esc alone while this covers it.
    const onKey = (event) => {
      if (event.key !== "Escape" || document.querySelector(".momx-tag-explain")) return;
      event.stopPropagation();
      close();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [close]);

  const onOpen = useCallback((link) => { close(); openAppLink(link); }, [close]);
  const onRetry = useCallback((section) => reader.current?.load(section), []);
  const onMore = useCallback((section, offset) => reader.current?.more(section, offset), []);

  if (typeof document === "undefined") return null;
  const title = (table && table.title) || (String(ticker || "") + " on the scanner");
  return createPortal(
    <div className="chat-scanner-day" data-testid="chat-scanner-day" role="dialog" aria-modal="true" aria-label={title} style={SHEET_STYLE}>
      <MomxDayExplain />
      <header style={HEAD_STYLE}>
        <b style={TITLE_STYLE}>{title}{day ? " · " + (dayLabel(day) || day) : ""}</b>
        <button ref={closeRef} type="button" style={CLOSE_STYLE} data-testid="chat-scanner-day-close" aria-label="Close" onClick={close}>
          <X size={18} aria-hidden="true" />
        </button>
      </header>
      <div className="momx-scanner-view" data-testid="chat-scanner-day-scroller" style={VIEW_STYLE}>
        <section className="momx-scanner-panel">
          {sections.map((section) => (
            <DaySection key={section.id} table={table} section={section} state={states[section.id] || {}} dataTick={dataTick}
              onOpen={onOpen} onRetry={onRetry} onMore={onMore} />
          ))}
        </section>
      </div>
    </div>,
    document.body,
  );
}
