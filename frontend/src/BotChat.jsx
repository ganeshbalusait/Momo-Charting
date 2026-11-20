// Ask the app (2026-11-10): a chatbot that needs no AI. His "if I ask see aapl
// it find in scanner history and I will ask why this stocks fails ... why it's
// missed" and "Free AI ... how chatbot is working before AI build same way".
// It answers from the app's own records and never places or changes a trade.
// One chat for both places it shows: the Bot page's Ask tab and, with `compact`,
// the chat window on every page (ChatDock.jsx). `pageTicker` is the ticker on screen.
//
// 2026-11-11, "like a search engine ... It will take me that page or link": an opening screen of cards built
// from what he is doing, buttons that take him to the page, and a tray of questions that never scrolls away.
//
// Later the same day: the full ticker report (collapsible sections, Short term / Long term views), the two-part
// answer (one "more" read for the latest news), and type-ahead in the ask box (no AI, no server call per keystroke).
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, History, Plus, Send, X } from "lucide-react";
import { OPEN_LINK_EVENT, openAppLink } from "./appLinks.js";
import { CHAT_DROPPED, CHAT_MAX, CHAT_OFFLINE, CHAT_SIGNED_OUT, CHAT_URL, chatSend, chatAccount, chatAnswer, chatAppend, chatDockNote, chatHistoryAdd, chatHistoryRead, chatHistorySearch, chatHome, chatHomeBody, chatLineParts, chatPageName, chatPageTicker, chatPlaceholder, chatQuestion, chatRefused, chatRequestBody, chatStarters, chatSubject, chatTable, chatTray, markPageChat } from "./botChat.js";
import { MORE_FAILED, MORE_WAIT, chatMergeMore, chatMoreBody, chatMoreReply, dragText, whyFactLines, whyLayout } from "./chatReport.js";
import { knownTickers, typeahead } from "./chatTypeahead.js";
import ChatTable, { ChatTablePreview } from "./ChatTable.jsx";
import ChatReport, { ChatHeadlines, ChatNumbers, ChatReasons, OwnerSummary, WhyCard, WhyWait } from "./ChatReport.jsx";
import "./chatSearch.css";

// The scanner day sheet is its own chunk, so the scanner's table code is only fetched when it is opened.
function SheetHost({ children }) {
  return createPortal(<div className="chat-sheet-host" data-testid="chat-sheet-host">{children}</div>, document.body);
}
function ScannerDayMissing({ onClose }) {
  return <SheetHost><button type="button" className="chat-sheet-wait" onClick={onClose}>The day table could not be loaded. Tap to go back to the chat.</button></SheetHost>;
}
const ChatScannerDay = lazy(() => import("./ChatScannerDay.jsx").catch(() => ({ default: ScannerDayMissing })));

const CHAT_REASONS = new Set([CHAT_SIGNED_OUT, CHAT_OFFLINE, CHAT_DROPPED]);
const POST = { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" } };

// Links are buttons that move the screen. They are the only thing on this page that goes anywhere.
function LinkButtons({ links }) {
  return links.length ? (
    <div className="chat-links">
      {links.map((link, i) => (
        <button key={i} type="button" className="chat-link" data-testid="chat-link" onClick={() => openAppLink(link)}>
          {link.label}<ChevronRight size={13} aria-hidden="true" />
        </button>
      ))}
    </div>
  ) : null;
}

function HomeCard({ card, busy, onAsk }) {
  const go = card.question ? () => onAsk(card.question) : card.links[0] ? () => openAppLink(card.links[0]) : null;
  return (
    <div className="chat-card" data-testid="chat-home-card" data-kind={card.kind}>
      <button type="button" className="chat-card-main" onClick={go || undefined} disabled={!go || (busy && Boolean(card.question))}>
        <span className="chat-card-head"><span className="chat-card-title">{card.title}</span>{go ? <ChevronRight size={16} aria-hidden="true" /> : null}</span>
        {card.lines.map((line, i) => <span key={i} className="chat-card-line">{line}</span>)}
        {card.asOf ? <span className="chat-card-time">{card.asOf}</span> : null}
      </button>
      <LinkButtons links={card.links} />
    </div>
  );
}

// Holds what is being typed, so a keystroke re-renders the box and nothing else. The type-ahead lives here too:
// worked out in the browser on each keystroke (his own questions, then built-in questions with his ticker).
function ChatForm({ inputRef, busy, placeholder, onAsk, history, known, isOwner, pageTicker }) {
  const [text, setText] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const [lit, setLit] = useState(-1);
  const suggestions = useMemo(() => typeahead(text, { history, known, isOwner, pageTicker }), [text, history, known, isOwner, pageTicker]);
  const mine = useMemo(() => new Set((history || []).map((item) => item.q.toLowerCase())), [history]);
  const showing = listOpen && suggestions.length > 0;
  const pick = (q) => { if (onAsk(q)) { setText(""); setListOpen(false); setLit(-1); } };

  // Esc closes the list only: caught before the chat window's own Esc, and only while the list is up and the box has focus.
  useEffect(() => {
    if (!showing) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape" || document.activeElement !== inputRef.current) return;
      event.stopPropagation();
      event.preventDefault();
      setListOpen(false);
      setLit(-1);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [showing, inputRef]);

  // A suggestion is lit by the arrow keys or by the mouse really moving over it. A list that opens under a mouse
  // left resting where a chip was must not light a line, or Enter would ask that line instead of what was typed
  // (2026-11-11: "profit or loss" + Enter asked "LOSS call wall").
  const onKeyDown = (event) => {
    if (!showing) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const last = suggestions.length - 1;
      // -1 = back in the box with nothing lit.
      setLit((i) => (event.key === "ArrowDown" ? (i >= last ? -1 : i + 1) : (i <= -1 ? last : i - 1)));
    } else if (event.key === "Enter" && lit >= 0 && lit < suggestions.length) {
      event.preventDefault();
      pick(suggestions[lit]);
    }
  };

  return (
    <div className="chat-form-wrap">
      {showing ? (
        <ul className="chat-suggest" id="chat-suggest" role="listbox" aria-label="Suggested questions" data-testid="chat-suggest">
          {suggestions.map((q, i) => (
            <li key={q} id={`chat-suggest-${i}`} role="option" aria-selected={i === lit} data-testid="chat-suggestion"
              className={mine.has(q.toLowerCase()) ? "is-mine" : undefined}
              onMouseDown={(e) => e.preventDefault()} onMouseMove={(e) => { if (e.movementX || e.movementY) setLit(i); }} onClick={() => pick(q)}>{q}</li>
          ))}
        </ul>
      ) : null}
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); if (onAsk(text)) { setText(""); setListOpen(false); setLit(-1); } }}>
        <input ref={inputRef} className="chat-input" type="text" value={text} maxLength={CHAT_MAX}
          onChange={(e) => { setText(e.target.value); setListOpen(true); setLit(-1); }}
          onKeyDown={onKeyDown} onBlur={() => setListOpen(false)} onFocus={() => setListOpen(true)}
          role="combobox" aria-expanded={showing} aria-controls="chat-suggest" aria-autocomplete="list"
          aria-activedescendant={showing && lit >= 0 ? `chat-suggest-${lit}` : undefined}
          placeholder={placeholder} aria-label="Your question" autoComplete="off" />
        <button type="submit" className="chat-send" disabled={busy || !chatQuestion(text)} aria-label="Ask">
          <Send size={14} aria-hidden="true" /> Ask
        </button>
      </form>
    </div>
  );
}

// One answer card. A "why did it move" answer (2026-11-14, "like Google ... a crisp clear answer") has the short
// answer and its key reasons on top, or "Writing the answer..." while it is on its way, and its fact lines and other
// headlines under "Show the numbers" (closed). Every other answer is laid out as before.
function Answer({ answer, isOwner, onOpenTable }) {
  const layout = whyLayout(answer);
  const why = answer.why;
  const lines = layout.collapse ? whyFactLines(answer.lines, answer.headlines) : answer.lines;
  const facts = (
    <>
      <LinkButtons links={answer.links} />
      {answer.understoodByAi ? <span className="chat-from">The AI worked out what you were asking; the answer is from the app</span> : null}
      {lines.map((line, i) => {
        // Rows, not a paragraph (2026-11-12): a label on the left edge, its text beside it; the words are the server's.
        if (line.bullet) return <p key={i} className="chat-line is-bullet">{line.text}</p>;
        const part = chatLineParts(line.text, i === 0);
        return (
          <p key={i} className={`chat-line is-${part.kind}${part.tone ? ` is-${part.tone}` : ""}`}>
            {part.label ? <span className="chat-line-label">{part.label}:</span> : null}
            {part.label ? " " : null}
            <span className="chat-line-text">{part.text}{part.kind === "head" ? ":" : ""}</span>
          </p>
        );
      })}
      {why?.drags.length ? <p className="chat-line is-row" data-testid="chat-why-drags"><span className="chat-line-label">Biggest movers among its largest stocks:</span> <span className="chat-line-text">{why.drags.map(dragText).join(", ")}</span></p> : null}
      {answer.table?.kind === "rows" ? <ChatTablePreview table={answer.table} onOpen={() => onOpenTable(answer.table)} /> : null}
      {answer.table?.kind === "scanner_day" ? (
        <button type="button" className="chat-link" data-testid="chat-table-open" onClick={() => onOpenTable(answer.table)}>Open the day table<ChevronRight size={13} aria-hidden="true" /></button>
      ) : null}
      <ChatReport answer={answer} onOpenTable={onOpenTable} />
      <ChatHeadlines headlines={answer.headlines} />
      {why?.reasons.length ? null : <ChatReasons reasons={answer.reasons} />}
      {isOwner && !why ? <OwnerSummary summary={answer.ownerSummary} /> : null}
    </>
  );
  return (
    <div className={layout.top ? "chat-a is-why" : "chat-a"} data-testid="chat-answer">
      <div className="chat-a-head">
        <span className="chat-kind" data-testid="chat-kind">{answer.kind}{answer.about ? ` · ${answer.about}` : ""}</span>
        {answer.asOf ? <span className="chat-as-of" data-testid="chat-as-of">{answer.asOf}</span> : null}
      </div>
      {layout.top === "wait" ? <WhyWait /> : layout.top === "why" ? <WhyCard why={why} /> : null}
      {layout.collapse ? <ChatNumbers>{facts}</ChatNumbers> : facts}
      {answer.moreState === "waiting" && layout.top !== "wait" ? <p className="chat-more-wait" role="status" data-testid="chat-more-wait">{MORE_WAIT}</p> : null}
      {answer.moreState === "failed" ? <p className="chat-more-failed" data-testid="chat-more-failed">{MORE_FAILED}</p> : null}
      {answer.aiNote ? <span className="chat-ai-off" data-testid="chat-ai-off">The AI could not help read this question: {answer.aiNote}.</span> : null}
    </div>
  );
}

function ChatHistory({ list, busy, onPick, onClose }) {
  const [query, setQuery] = useState("");
  const shown = chatHistorySearch(list, query);
  return (
    <div className="chat-history" data-testid="chat-history">
      <div className="chat-history-head">
        <b>Earlier questions</b>
        <span style={{ flex: 1 }} />
        <button type="button" className="chat-tool" data-testid="chat-history-close" aria-label="Close history" onClick={onClose}><X size={16} aria-hidden="true" /></button>
      </div>
      <input className="chat-history-search" data-testid="chat-history-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search your questions" aria-label="Search your questions" autoComplete="off" />
      <div className="chat-history-list">
        {shown.length ? shown.map(({ q, at }) => (
          <button key={q} type="button" className="chat-history-item" data-testid="chat-history-item" disabled={busy} onClick={() => onPick(q)}>
            {q}{at ? <span>{at}</span> : null}
          </button>
        )) : <p className="chat-history-none">{list.length ? "No question matches that." : "Questions you ask on this device show here."}</p>}
      </div>
    </div>
  );
}

// `isOwner` false = a regular user: market data only (the server decides what they may ask).
export default function BotChat({ pageTicker = "", pageName = "", compact = false, active = true, isOwner = true, onClose = null }) {
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [home, setHome] = useState(null);
  const [account, setAccount] = useState("");
  const [history, setHistory] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sheet, setSheet] = useState(null);
  const log = useRef(null);
  const end = useRef(null);
  const input = useRef(null);
  const count = useRef(0);
  const historyLayer = useRef(null);
  const activeNow = useRef(active);
  const busyNow = useRef(false);
  activeNow.current = active;
  const ticker = chatPageTicker(pageTicker);
  const page = chatPageName(pageName);
  const homeKey = `${ticker}|${page}|${isOwner ? 1 : 0}`;
  const opening = turns.length === 0;

  useEffect(() => (compact ? undefined : markPageChat()), [compact]);

  useEffect(() => {
    if (!compact) { end.current?.scrollIntoView?.({ block: "nearest" }); return; }
    // In the small window the newest question goes to the top, so a long list is read from its first line.
    const turnsOnScreen = log.current?.querySelectorAll(".chat-turn");
    const newest = turnsOnScreen?.[turnsOnScreen.length - 1];
    if (log.current && newest) log.current.scrollTop = newest.offsetTop;
  }, [turns, busy, compact, active]);

  // Typing can start straight away, except on a touch screen where it would throw up the keyboard.
  useEffect(() => {
    if (compact && active && window.matchMedia?.("(pointer: fine)").matches) input.current?.focus({ preventScroll: true });
  }, [compact, active]);

  // Whose questions these are, so a shared device never shows one person another's. Asked once.
  const accountAsked = useRef(false);
  useEffect(() => {
    if (!active || accountAsked.current) return;
    accountAsked.current = true;
    fetch("/api/auth/status", { credentials: "include" }).then((r) => (r.ok ? r.json() : null)).then((status) => {
      const who = chatAccount(status);
      if (!who) accountAsked.current = false;       // the app could not say: ask again the next time the chat opens
      setAccount(who);
      setHistory(chatHistoryRead(who));
    }).catch(() => { accountAsked.current = false; });
  }, [active]);

  // The opening cards: one request when the chat opens, and again only if the page or its ticker changes. No timer.
  useEffect(() => {
    if (!active || !opening) return undefined;
    let current = true;
    const stop = new AbortController();
    const timer = setTimeout(() => {
      fetch(CHAT_URL, { ...POST, signal: stop.signal, body: JSON.stringify(chatHomeBody(ticker, page)) })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (current) setHome({ key: homeKey, view: chatHome(d?.home, isOwner) }); })
        .catch(() => { if (current) setHome({ key: homeKey, view: chatHome(null, isOwner) }); });
    }, 200);
    return () => { current = false; clearTimeout(timer); stop.abort(); };
  }, [active, opening, homeKey, ticker, page, isOwner]);

  // After a link is followed the screen is on that page, so the chat's own layers get out of the way.
  useEffect(() => {
    const onOpen = () => { setSheet(null); setHistoryOpen(false); };
    window.addEventListener(OPEN_LINK_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_LINK_EVENT, onOpen);
  }, []);

  // Esc closes the top layer only and goes back to the chat; the window, and any chart behind it, stay as they were.
  useEffect(() => {
    if (!sheet && !historyOpen) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (sheet) {
        if (document.querySelector(".chat-scanner-day")) return;   // the day sheet keeps its own Esc
        event.stopPropagation();
        setSheet(null);
        return;
      }
      const node = historyLayer.current?.querySelector(".chat-history");
      const box = node?.getBoundingClientRect();
      if (!box || !node.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2))) return;
      event.stopPropagation();
      setHistoryOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [sheet, historyOpen]);

  // The answer's slower second part (the latest news): ONE read per answer, never repeated, never on a timer.
  const moreAsked = useRef(new Set());
  const askMore = (id, token) => {
    if (moreAsked.current.has(id)) return;
    moreAsked.current.add(id);
    const merge = (reply) => setTurns((t) => t.map((turn) => (turn.id === id && turn.answer ? { ...turn, answer: chatMergeMore(turn.answer, reply, isOwner, chatTable) } : turn)));
    fetch(CHAT_URL, { ...POST, body: JSON.stringify(chatMoreBody(token)) })
      .then((r) => (r.ok ? r.json() : null))
      // The route answers {"more": {done, lines, articles, reasons, ...}}: the part inside "more" is merged.
      .then((d) => merge(chatMoreReply(d && typeof d === "object" ? d : null) || null))
      .catch(() => merge(null));
  };

  const ask = (raw) => {
    const question = chatQuestion(raw);
    if (!question || busyNow.current) return false;
    const id = ++count.current;
    busyNow.current = true;
    setBusy(true);
    setHistoryOpen(false);
    setTurns((t) => chatAppend(t, { id, question, answer: null, pageTicker: ticker }));
    if (account) setHistory(chatHistoryAdd(account, question, new Date().toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })));
    const settle = (reply) => {
      const answer = chatAnswer(reply, isOwner);
      setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, answer, subject: chatSubject(reply) } : turn)));
      // The day table opens by itself, once per answer, and only if the chat is still open.
      if (answer.table?.kind === "scanner_day" && activeNow.current) setSheet(answer.table);
      if (answer.more) askMore(id, answer.more);
    };
    // What the conversation is about goes along, so a follow-up with no ticker ("profit or loss?") continues it.
    chatSend((...args) => fetch(...args), CHAT_URL, { ...POST, body: JSON.stringify(chatRequestBody(question, turns, ticker, page)) })
      .then((r) => r.json().catch(() => ({})).then((d) => ({ ok: r.ok, status: r.status, d })))
      .then(({ ok, status, d }) => settle(ok ? d : { intent: "error", lines: [status === 403 ? chatRefused(isOwner) : (d?.error || "The app could not answer that. Nothing was changed.")] }))
      .catch((error) => settle({ intent: "error", lines: [CHAT_REASONS.has(error?.message) ? error.message : CHAT_DROPPED] }))
      .finally(() => { busyNow.current = false; setBusy(false); });
    return true;
  };

  const closeSheet = useCallback(() => setSheet(null), []);
  const startOver = () => { setTurns([]); setSheet(null); setHistoryOpen(false); };
  const starters = chatStarters(ticker, compact, isOwner, page);
  const homeView = home?.key === homeKey ? home.view : null;
  const tray = chatTray(turns, opening && homeView?.chips.length ? homeView.chips : starters);
  // Tickers the type-ahead completes from: the boards and watchlist (sent with the opening cards), every ticker an
  // answer or card named, and the one on screen. Worked out only when those change, never per keystroke.
  const homeTickers = homeView?.tickers;
  const homeCards = homeView?.cards;
  const known = useMemo(() => knownTickers(
    homeTickers || [],
    (homeCards || []).flatMap((card) => card.links.map((l) => l.ticker)),
    turns.flatMap((turn) => [turn.subject?.ticker, ...(turn.answer?.links || []).map((l) => l.ticker)]),
    ticker,
  ), [homeTickers, homeCards, turns, ticker]);
  const swipeTray = (e) => { if (e.deltaY && e.currentTarget.scrollWidth > e.currentTarget.clientWidth) e.currentTarget.scrollLeft += e.deltaY; };
  const tools = (
    <>
      {!opening ? <button type="button" className="chat-tool" data-testid="chat-new" aria-label="New chat" title="New chat" onClick={startOver}><Plus size={17} aria-hidden="true" /></button> : null}
      {account ? <button type="button" className="chat-tool" data-testid="chat-history-button" aria-label="Earlier questions" title="Earlier questions" aria-pressed={historyOpen} onClick={() => setHistoryOpen((o) => !o)}><History size={17} aria-hidden="true" /></button> : null}
    </>
  );

  const body = (
    <div ref={historyLayer} className={compact ? "chat is-compact" : "chat"} data-testid={compact ? "dock-chat" : "bot-chat"}>
      {compact ? null : (<>
        <p className="chat-note">Ask about a ticker, about the bot, or what the scanner showed at a time of day. Answers come straight from the app's records (the Bot's signals, the paper accounts' real orders, the Scorecard and the scanner history). Every number is one the app saved: when the AI is available it writes a short reply on top, and any reply with a number that is not in the records is thrown away. It only reads; it cannot place or change a trade.</p>
        <div className="chat-top">{tools}</div>
      </>)}
      {ticker ? <p className="chat-looking" data-testid="chat-looking">Looking at <b>{ticker}</b></p> : null}
      <div ref={log} className="chat-log" role="log" aria-label="Conversation" aria-live="polite">
        {opening ? (
          <div className="chat-empty">
            <h3 className="chat-hello">How can I help?</h3>
            {homeView ? (homeView.cards.length ? (
              <div className="chat-cards">
                {homeView.cards.map((card, i) => <HomeCard key={`${card.kind}-${i}`} card={card} busy={busy} onAsk={ask} />)}
              </div>
            ) : null) : active ? <div className="chat-card-wait" data-testid="chat-home-wait" aria-hidden="true" /> : null}
            {compact ? <p className="chat-note">{chatDockNote(isOwner)}</p> : null}
          </div>
        ) : turns.map((turn) => (
          <div key={turn.id} className="chat-turn">
            <p className="chat-q"><span>You</span>{turn.question}</p>
            {turn.answer ? (
              <Answer answer={turn.answer} isOwner={isOwner} onOpenTable={setSheet} />
            ) : <p className="chat-a chat-wait" role="status">Looking it up…</p>}
          </div>
        ))}
        <span ref={end} />
      </div>
      {tray.links.length || tray.chips.length ? (
        <div className="chat-tray" data-testid="chat-tray" onWheel={swipeTray}>
          {tray.links.map((link, i) => <button key={`l${i}`} type="button" className="chat-link" data-testid="chat-tray-link" onClick={() => openAppLink(link)}>{link.label}</button>)}
          {tray.chips.map((q) => <button key={q} type="button" className="chat-chip" onClick={() => ask(q)} disabled={busy}>{q}</button>)}
        </div>
      ) : null}
      <ChatForm inputRef={input} busy={busy} placeholder={chatPlaceholder(ticker)} onAsk={ask} history={history} known={known} isOwner={isOwner} pageTicker={ticker} />
      {historyOpen ? <ChatHistory list={history} busy={busy} onPick={ask} onClose={() => setHistoryOpen(false)} /> : null}
      {sheet?.kind === "rows" ? <SheetHost><ChatTable table={sheet} onClose={closeSheet} /></SheetHost> : null}
      {sheet?.kind === "scanner_day" ? (
        <Suspense fallback={<SheetHost><p className="chat-sheet-wait" role="status">Opening the table…</p></SheetHost>}>
          <ChatScannerDay table={sheet} onClose={closeSheet} />
        </Suspense>
      ) : null}
    </div>
  );

  if (!compact) return body;
  return (
    <>
      <header className="chat-dock-head">
        <b>Ask</b>
        <span className="chat-dock-head-tools">
          {tools}
          <button type="button" className="chat-dock-close" data-testid="chat-dock-close" aria-label="Close" onClick={onClose || undefined}>
            <X size={17} aria-hidden="true" />
          </button>
        </span>
      </header>
      {body}
    </>
  );
}
