// Ask the app (2026-11-10): the screen side of the no-AI chatbot
// (POST /api/bot-chat, momx/chatbot.py). Pure, so node:test can run it.

export const CHAT_URL = "/api/bot-chat";
export const CHAT_MAX = 300;
export const CHAT_STARTERS = [
  "SPY if I take how much it went up",
  "why AAPL failed",
  "why was NVDA missed",
  "see TSLA yesterday",
  "what is the bot doing",
];

/** What may be sent: trimmed, one line, at most CHAT_MAX characters. "" = nothing to send. */
export function chatQuestion(text) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, CHAT_MAX);
}

const KINDS = { what_if: "How far it went", why_failed: "Why it failed", why_missed: "Why it was missed", show: "On file",
  status: "The bot now", help: "What I can answer", error: "Could not answer" };

/** One answer as the screen shows it: a small heading, plain lines, bullet lines apart, follow-up questions. */
export function chatAnswer(reply) {
  const r = reply && typeof reply === "object" ? reply : {};
  const all = Array.isArray(r.lines) ? r.lines.filter((x) => typeof x === "string" && x.trim()) : [];
  const lines = all.map((text) => (text.startsWith("- ") ? { bullet: true, text: text.slice(2) } : { bullet: false, text }));
  const about = [r.ticker, r.day].filter(Boolean).join(" · ");
  return {
    kind: KINDS[r.intent] || KINDS.help,
    about,
    lines: lines.length ? lines : [{ bullet: false, text: "I have no answer for that." }],
    suggest: (Array.isArray(r.suggest) ? r.suggest : []).filter((x) => typeof x === "string" && x.trim()).slice(0, 4),
  };
}

/** The conversation with one more turn; only the last 30 turns are kept on screen. */
export function chatAppend(turns, turn) {
  return [...(Array.isArray(turns) ? turns : []), turn].slice(-30);
}

/** What the conversation is about now: the ticker and day of the newest answer that had one. Sent with the
 * next question, so "profit or loss?" continues with the same ticker. null = nothing yet. */
export function chatContext(turns) {
  const list = Array.isArray(turns) ? turns : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const subject = list[i] && list[i].subject;
    if (subject && typeof subject.ticker === "string" && subject.ticker) {
      const about = { ticker: subject.ticker, day: typeof subject.day === "string" ? subject.day : "" };
      if (subject.side === "bull" || subject.side === "bear") about.side = subject.side;
      return about;
    }
  }
  return null;
}

/** The ticker / day an answer was about, kept on its turn. null when the answer had no ticker. */
export function chatSubject(reply) {
  const r = reply && typeof reply === "object" ? reply : {};
  if (typeof r.ticker !== "string" || !r.ticker) return null;
  const subject = { ticker: r.ticker, day: typeof r.day === "string" ? r.day : "" };
  if (r.side === "bull" || r.side === "bear") subject.side = r.side;       // calls or puts: a follow-up stays on the same side
  return subject;
}
