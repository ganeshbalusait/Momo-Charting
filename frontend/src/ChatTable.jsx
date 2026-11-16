// A table in a chat answer (2026-11-11). In the conversation it is a short stack of ticker cards that wrap and never
// scroll on their own; the whole table opens as a sheet with one scroller, so a scrolling table never sits inside the
// scrolling conversation. Buttons only move the screen: nothing here can place, change or cancel a trade.
import { ChevronRight, X } from "lucide-react";
import { openAppLink } from "./appLinks.js";
import { chatRowLink } from "./botChat.js";

const PREVIEW_ROWS = 5;

export function ChatTablePreview({ table, onOpen }) {
  const [head, ...rest] = table.columns;
  const shown = table.rows.slice(0, PREVIEW_ROWS);
  return (
    <div className="chat-table" data-testid="chat-table">
      {table.title ? <b className="chat-table-title">{table.title}</b> : null}
      {shown.map((row, i) => {
        const link = chatRowLink(table, row);
        return (
          <div key={i} className="chat-row-card" data-testid="chat-row-card">
            <div className="chat-row-head">
              <b>{row[head.key]}</b>
              {link ? (
                <button type="button" className="chat-link is-small" data-testid="chat-row-link" onClick={() => openAppLink(link)}>
                  {link.label}<ChevronRight size={13} aria-hidden="true" />
                </button>
              ) : null}
            </div>
            <dl className="chat-row-cells">
              {rest.filter(({ key }) => row[key]).map(({ key, label }) => <div key={key}><dt>{label}</dt><dd>{row[key]}</dd></div>)}
            </dl>
          </div>
        );
      })}
      {table.note ? <p className="chat-table-note">{table.note}</p> : null}
      <button type="button" className="chat-link" data-testid="chat-table-open" onClick={onOpen}>
        See the whole table ({table.rows.length} {table.rows.length === 1 ? "row" : "rows"})
      </button>
    </div>
  );
}

export default function ChatTable({ table, onClose }) {
  const links = table.rows.map((row) => chatRowLink(table, row));
  const hasLinks = links.some(Boolean);
  return (
    <section className="chat-sheet" data-testid="chat-table-sheet" role="dialog" aria-modal="true" aria-label={table.title || "Table"}>
      <header className="chat-sheet-head">
        <b>{table.title || "Table"}</b>
        <button type="button" className="chat-dock-close" data-testid="chat-sheet-close" aria-label="Close table" onClick={onClose}>
          <X size={17} aria-hidden="true" />
        </button>
      </header>
      {table.note ? <p className="chat-table-note">{table.note}</p> : null}
      <div className="chat-sheet-scroll">
        <table>
          <thead>
            <tr>
              {table.columns.map(({ key, label, align }) => <th key={key} className={`is-${align}`}>{label}</th>)}
              {hasLinks ? <th aria-label="Open" /> : null}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {table.columns.map(({ key, align }) => <td key={key} className={`is-${align}`}>{row[key]}</td>)}
                {hasLinks ? <td>{links[i] ? <button type="button" className="chat-link is-small" onClick={() => openAppLink(links[i])}>{links[i].label}</button> : null}</td> : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.link ? (
        <footer className="chat-sheet-foot">
          <button type="button" className="chat-link" onClick={() => openAppLink(table.link)}>{table.link.label}<ChevronRight size={14} aria-hidden="true" /></button>
        </footer>
      ) : null}
    </section>
  );
}
