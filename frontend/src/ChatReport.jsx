// The full ticker report inside a chat answer (2026-11-11). Each section is a collapsible card in the server's
// order: the first three open, the rest closed with a one-line summary. A report with views shows "Short term"
// and "Long term" as two tabs on a wide screen and as two headed groups on a phone, each with its rules count.
// Sections still on their way say "loading..." and fill in from the answer's one "more" read.
// Buttons only move the screen or open an article in a new tab: nothing here can place, change or cancel a trade.
import { useId, useState } from "react";
import { ChevronRight, ExternalLink } from "lucide-react";
import { openAppLink } from "./appLinks.js";
import { SECTIONS_OPEN, TONE_LABELS, WHY_WAIT } from "./chatReport.js";
import { ChatTablePreview } from "./ChatTable.jsx";
import AnalystCard from "./AnalystCard.jsx";
import TechnicalCard from "./TechnicalCard.jsx";

function Links({ links }) {
  return links?.length ? (
    <div className="chat-links">
      {links.map((link, i) => (
        <button key={i} type="button" className="chat-link" data-testid="chat-link" onClick={() => openAppLink(link)}>
          {link.label}<ChevronRight size={13} aria-hidden="true" />
        </button>
      ))}
    </div>
  ) : null;
}

/** A headline's tone for the ticker asked about, as the server's code decided it: "Positive", "Negative" or "Neutral". */
export function ToneChip({ tone }) {
  return TONE_LABELS[tone] ? <span className={`chat-tone is-${tone}`} data-testid="chat-tone" data-tone={tone}>{TONE_LABELS[tone]}</span> : null;
}

/** Headlines with a button that opens the article in a new tab, named by its source, and its tone tag. */
export function ChatHeadlines({ headlines }) {
  return headlines?.length ? (
    <ul className="chat-news" data-testid="chat-headlines">
      {headlines.map((h, i) => (
        <li key={`${h.url || h.title}-${i}`} data-testid="chat-headline">
          <span className="chat-news-title">{h.title}{h.time ? <small> · {h.time}</small> : null}</span>
          <span className="chat-news-side">
            <ToneChip tone={h.tone} />
            {h.url ? (
              <a className="chat-link is-small" data-testid="chat-headline-link" href={h.url} target="_blank" rel="noopener noreferrer">
                {h.source || "Open"}<ExternalLink size={12} aria-hidden="true" />
              </a>
            ) : h.source ? <small className="chat-news-source">{h.source}</small> : null}
          </span>
        </li>
      ))}
    </ul>
  ) : null;
}

/** The top slot while the "why" answer is being written. */
export function WhyWait() {
  return <p className="chat-why-wait" role="status" data-testid="chat-why-wait">{WHY_WAIT}</p>;
}

function WhySource({ s }) {
  const text = [s.source, s.time].filter(Boolean).join(" · ") || "Source";
  return (
    <span className="chat-why-src" data-testid="chat-why-source">
      {s.url ? (
        <a className="chat-why-src-link" data-testid="chat-why-source-link" href={s.url} target="_blank" rel="noopener noreferrer" title={s.title || undefined}>
          {text}<ExternalLink size={11} aria-hidden="true" />
        </a>
      ) : <span className="chat-why-src-link is-plain" title={s.title || undefined}>{text}</span>}
      <ToneChip tone={s.tone} />
    </span>
  );
}

/** The answer on top (2026-11-14, "like Google"): the short answer, then "Key reasons", each with its tagged sources. */
export function WhyCard({ why }) {
  if (!why?.answer) return null;
  const when = [why.window.label, why.window.text].filter(Boolean).join(" · ");
  return (
    <section className="chat-why" data-testid="chat-why" data-by={why.by} aria-label="The answer">
      {when ? <p className="chat-why-when" data-testid="chat-why-window">{when}</p> : null}
      <p className="chat-why-answer" data-testid="chat-why-answer">{why.answer}</p>
      {why.reasons.length ? (
        <div className="chat-why-reasons" data-testid="chat-why-reasons">
          <h4 className="chat-why-head">Key reasons</h4>
          <ul>
            {why.reasons.map((r, i) => (
              <li key={i} className="chat-why-reason" data-testid="chat-why-reason">
                <p>{r.title ? <b className="chat-why-title" data-testid="chat-why-title">{r.title}</b> : null}{r.title && r.text ? " " : null}{r.text ? <span>{r.text}</span> : null}</p>
                {r.sources.length ? <div className="chat-why-srcs">{r.sources.map((s, j) => <WhySource key={j} s={s} />)}</div> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <small className="chat-why-note" data-testid="chat-why-note">{why.note}</small>
    </section>
  );
}

/** "Show the numbers": the fact lines and the other headlines, closed until tapped. */
export function ChatNumbers({ children }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="chat-numbers" data-testid="chat-numbers">
      <button type="button" className="chat-numbers-toggle" data-testid="chat-numbers-toggle" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={13} aria-hidden="true" />{open ? "Hide the numbers" : "Show the numbers"}
      </button>
      {open ? <div className="chat-numbers-body" id={id} data-testid="chat-numbers-body">{children}</div> : null}
    </div>
  );
}

/** "What the news points to": the reasons the news gives, as plain lines. */
export function ChatReasons({ reasons }) {
  return reasons?.length ? (
    <div className="chat-reasons" data-testid="chat-reasons">
      <b>What the news points to</b>
      <ul>{reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
    </div>
  ) : null;
}

/**
 * The owner's AI summary (2026-11-11 evening): the one AI-written paragraph in the chat, in its own box apart from the
 * app's facts, always under its label. The server sends it to the owner only; the caller renders this for the owner only.
 */
export function OwnerSummary({ summary }) {
  if (!summary?.text) return null;
  return (
    <aside className="chat-ai-summary" data-testid="chat-ai-summary" aria-label={summary.label}>
      <b className="chat-ai-summary-label" data-testid="chat-ai-summary-label">{summary.label}</b>
      <p>{summary.text}</p>
      {summary.asOf ? <small className="chat-ai-summary-as-of">{summary.asOf}</small> : null}
    </aside>
  );
}

function Section({ section, open, onOpenTable }) {
  const s = section;
  return (
    <details className="chat-sec" data-testid="chat-section" data-key={s.key} open={open}>
      <summary>
        <span className="chat-sec-head"><b>{s.title}</b>{s.asOf ? <small className="chat-as-of">{s.asOf}</small> : null}</span>
        <span className="chat-sec-summary" data-testid="chat-section-summary">{s.pending ? "loading..." : s.summary}</span>
      </summary>
      <div className="chat-sec-body">
        {s.pending ? <p className="chat-more-wait" role="status" data-testid="chat-section-loading">loading...</p> : null}
        {s.failed ? <p className="chat-more-failed">This part could not be loaded right now.</p> : null}
        {s.lines.map((line, i) => <p key={i} className="chat-line">{line}</p>)}
        {s.technical ? <TechnicalCard data={s.technical} initialTerm={s.view === "long" ? "long" : "short"} /> : null}
        {s.analyst ? <AnalystCard data={s.analyst} /> : null}
        {s.table?.kind === "rows" ? <ChatTablePreview table={s.table} onOpen={() => onOpenTable(s.table)} /> : null}
        <ChatHeadlines headlines={s.headlines} />
        <ChatReasons reasons={s.reasons} />
        <Links links={s.links} />
      </div>
    </details>
  );
}

function SectionList({ sections, onOpenTable }) {
  return <div className="chat-secs">{sections.map((s, i) => <Section key={s.key} section={s} open={i < SECTIONS_OPEN} onOpenTable={onOpenTable} />)}</div>;
}

export default function ChatReport({ answer, onOpenTable }) {
  const [view, setView] = useState(answer.views[0]?.key || "");
  if (!answer.sections.length) return null;
  // The server's Short / Mid / Long-Term verdicts and its small chart, above the sections, unless a section has its own.
  const technical = answer.technical && !answer.sections.some((s) => s.technical)
    ? <TechnicalCard key={answer.technical.charts?.daily ? "chart" : "terms"} data={answer.technical} initialTerm="short" />
    : null;
  if (!answer.views.length) return <div className="chat-report" data-testid="chat-report">{technical}<SectionList sections={answer.sections} onOpenTable={onOpenTable} /></div>;
  const chosen = answer.views.some((v) => v.key === view) ? view : answer.views[0].key;
  return (
    <div className="chat-report" data-testid="chat-report">
      {technical}
      <div className="chat-views" role="tablist" aria-label="Report views">
        {answer.views.map((v) => (
          <button key={v.key} type="button" role="tab" aria-selected={v.key === chosen} data-testid={`chat-view-tab-${v.key}`}
            className={v.key === chosen ? "chat-view-tab is-on" : "chat-view-tab"} onClick={() => setView(v.key)}>
            {v.title}{v.rules ? <small>{v.rules}</small> : null}
          </button>
        ))}
      </div>
      {answer.views.map((v) => (
        <section key={v.key} className={v.key === chosen ? "chat-view is-on" : "chat-view"} data-testid={`chat-view-${v.key}`} aria-label={v.title}>
          <h4 className="chat-view-head">{v.title}{v.rules ? <small>{v.rules}</small> : null}</h4>
          {v.sections.length ? <SectionList sections={v.sections} onOpenTable={onOpenTable} /> : <p className="chat-line">Nothing for this view yet.</p>}
        </section>
      ))}
    </div>
  );
}
