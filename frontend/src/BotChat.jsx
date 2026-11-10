// Ask the app (2026-11-10): a chatbot that needs no AI. His "if I ask see aapl
// it find in scanner history and I will ask why this stocks fails ... why it's
// missed" and "Free AI ... how chatbot is working before AI build same way".
// It answers from the app's own records and never places or changes a trade.
import { useEffect, useRef, useState } from "react";
import { Send } from "lucide-react";
import { CHAT_MAX, CHAT_STARTERS, CHAT_URL, chatAnswer, chatAppend, chatContext, chatQuestion, chatSubject } from "./botChat.js";

export default function BotChat() {
  const [turns, setTurns] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  const count = useRef(0);

  useEffect(() => { end.current?.scrollIntoView?.({ block: "nearest" }); }, [turns, busy]);

  const ask = (raw) => {
    const question = chatQuestion(raw);
    if (!question || busy) return;
    const id = ++count.current;
    setText("");
    setBusy(true);
    setTurns((t) => chatAppend(t, { id, question, answer: null }));
    const settle = (reply) => setTurns((t) => t.map((turn) => (turn.id === id ? { ...turn, answer: chatAnswer(reply), subject: chatSubject(reply) } : turn)));
    // What the conversation is about goes along, so a follow-up with no ticker ("profit or loss?") continues it.
    const context = chatContext(turns);
    fetch(CHAT_URL, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(context ? { question, context } : { question }) })
      .then((r) => r.json().catch(() => ({})).then((d) => ({ ok: r.ok, status: r.status, d })))
      .then(({ ok, status, d }) => settle(ok ? d : { intent: "error", lines: [status === 403 ? "Only the owner can ask about the bot's trades." : (d?.error || "The app could not answer that. Nothing was changed.")] }))
      .catch(() => settle({ intent: "error", lines: ["Could not reach the app. Check the connection and ask again."] }))
      .finally(() => setBusy(false));
  };

  return (
    <div className="chat" data-testid="bot-chat">
      <p className="chat-note">Ask about a ticker or about the bot. Answers come straight from the app's records (the Bot's signals, the paper accounts' real orders and the Scorecard). Every number is one the app saved: when the AI is available it writes a short reply on top, and any reply with a number that is not in the records is thrown away. It only reads; it cannot place or change a trade.</p>
      <div className="chat-log" role="log" aria-label="Conversation" aria-live="polite">
        {turns.length === 0 ? (
          <div className="chat-empty">
            <b>Try one of these</b>
            <div className="chat-chips">
              {CHAT_STARTERS.map((q) => <button key={q} type="button" className="chat-chip" onClick={() => ask(q)} disabled={busy}>{q}</button>)}
            </div>
          </div>
        ) : turns.map((turn) => (
          <div key={turn.id} className="chat-turn">
            <p className="chat-q"><span>You</span>{turn.question}</p>
            {turn.answer ? (
              <div className="chat-a" data-testid="chat-answer">
                <span className="chat-kind">{turn.answer.kind}{turn.answer.about ? ` · ${turn.answer.about}` : ""}</span>
                {turn.answer.ai ? (<>
                  <p className="chat-ai" data-testid="chat-ai"><span className="chat-ai-tag">AI</span>{turn.answer.ai}</p>
                  <span className="chat-from">From the records{turn.answer.understoodByAi ? " (the AI worked out what you were asking)" : ""}</span>
                </>) : turn.answer.understoodByAi ? <span className="chat-from">The AI worked out what you were asking; the answer is from the records</span> : null}
                {turn.answer.lines.map((line, i) => <p key={i} className={line.bullet ? "chat-line is-bullet" : "chat-line"}>{line.text}</p>)}
                {turn.answer.aiNote ? <span className="chat-ai-off" data-testid="chat-ai-off">No AI reply: {turn.answer.aiNote}. The answer above is straight from the records.</span> : null}
                {turn.answer.suggest.length ? (
                  <div className="chat-chips">
                    {turn.answer.suggest.map((q) => <button key={q} type="button" className="chat-chip" onClick={() => ask(q)} disabled={busy}>{q}</button>)}
                  </div>
                ) : null}
              </div>
            ) : <p className="chat-a chat-wait" role="status">Looking it up…</p>}
          </div>
        ))}
        <span ref={end} />
      </div>
      <form className="chat-form" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <input className="chat-input" type="text" value={text} maxLength={CHAT_MAX} onChange={(e) => setText(e.target.value)}
          placeholder="For example: SPY if I take how much it went up" aria-label="Your question" autoComplete="off" />
        <button type="submit" className="chat-send" disabled={busy || !chatQuestion(text)} aria-label="Ask">
          <Send size={14} aria-hidden="true" /> Ask
        </button>
      </form>
    </div>
  );
}
