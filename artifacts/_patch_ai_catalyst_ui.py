"""Wire /api/ai/catalyst into the news popover.

The explainer has existed and worked since 2026-08-27 (agents/catalyst_explainer.py)
and was referenced ZERO times in frontend/src. Ganesh chose "AI summary of
headlines" explicitly when asked, so this is the delivery of that choice.

ON DEMAND, not per row. It costs a model call, and a board of 358 rows must
never fire 358 of them. The trader opens a headline and presses a button.

The module's own honesty rules do the hard part: when the headlines do not
explain the move it answers UNKNOWN / "No news explains this move." rather than
inventing a reason - which is exactly the failure visible in the competitor's
app, where a generic sentence about GPU compute capacity is attached to a
ticker up 5.83%.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()
assert "api/ai/catalyst" not in s, "already wired"

OLD = '''function MomxNewsPopover({ symbol, news, onClose }) {
  if (!news) return null;
  return createPortal('''
NEW = '''function MomxNewsPopover({ symbol, news, onClose }) {
  // "Why is it moving?" - ON DEMAND, never per row. This costs a model call and
  // a 358-row board must never fire 358 of them, so it is a button the trader
  // presses on a headline he already opened.
  //
  // The endpoint has existed and worked since 2026-08-27 and was referenced
  // ZERO times in the frontend. Its own rule does the hard part: when the
  // headlines do not explain the move it answers "No news explains this move."
  // instead of inventing a reason - which is the failure in the competitor's
  // app, where a generic line about GPU compute capacity sits under a ticker up
  // 5.83% and explains nothing.
  const [aiState, setAiState] = useState("idle");
  const [aiRead, setAiRead] = useState(null);
  useEffect(() => {
    // A different symbol is a different question.
    setAiState("idle");
    setAiRead(null);
  }, [symbol]);
  const askWhy = async () => {
    if (aiState === "loading") return;
    setAiState("loading");
    try {
      const response = await fetch(
        "/api/ai/catalyst?symbol=" + encodeURIComponent(symbol || ""),
      );
      const payload = await response.json();
      setAiRead(payload && typeof payload === "object" ? payload : null);
      setAiState("done");
    } catch {
      // A failed explanation must never be mistaken for "no news explains it".
      setAiRead(null);
      setAiState("error");
    }
  };
  if (!news) return null;
  return createPortal('''
assert s.count(OLD) == 1, "popover anchor"
s = s.replace(OLD, NEW)

OLD2 = '''          {news.url ? (
            <a href={news.url} target="_blank" rel="noopener noreferrer">
              Read
            </a>
          ) : null}
        </div>'''
NEW2 = '''          {news.url ? (
            <a href={news.url} target="_blank" rel="noopener noreferrer">
              Read
            </a>
          ) : null}
          <button
            type="button"
            className="momx-news-why"
            onClick={askWhy}
            disabled={aiState === "loading"}
          >
            {aiState === "loading" ? "Reading..." : "Why is it moving?"}
          </button>
        </div>
        {news.scope === "market-wide" ? (
          // Said here as well as in the badge tooltip, because this panel is
          // where he reads the story and decides what it means.
          <p className="momx-news-pop-scope">
            Market-wide story
            {news.namedCount > 1 ? " naming " + news.namedCount + " tickers" : ""}
            {" \\u2014 it mentions " + symbol + ", it is not about it."}
          </p>
        ) : null}
        {aiState === "done" && aiRead ? (
          <div className="momx-news-ai">
            <b>{aiRead.summary || "No answer."}</b>
            <small>
              {/* Always shown, because "no news explains this" and "we had no
                  news to read" are different answers and the trader must be
                  able to tell them apart. */}
              {aiRead.available === false
                ? aiRead.reason || "AI is not configured."
                : (aiRead.category || "") + " · confidence " + (aiRead.confidence || "?")
                  + " · " + (aiRead.headlinesConsidered || 0) + " headline"
                  + ((aiRead.headlinesConsidered || 0) === 1 ? "" : "s") + " read"}
            </small>
          </div>
        ) : null}
        {aiState === "error" ? (
          <div className="momx-news-ai">
            <small>Could not reach the AI just now - this is not an answer about the news.</small>
          </div>
        ) : null}'''
assert s.count(OLD2) == 1, "meta anchor"
s = s.replace(OLD2, NEW2)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: 'Why is it moving?' wired into the news popover")

# --- styles ----------------------------------------------------------------
p = "frontend/src/index.css"
s = io.open(p, encoding="utf-8", newline="").read()
assert "momx-news-why" not in s
s = s.rstrip("\\n") + """

/* "Why is it moving?" in the news popover, and the AI's answer beneath it.
   On demand only - a board of 358 rows must never fire 358 model calls. */
.momx-news-why {
  background: transparent;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 4px;
  color: #cbd5e1;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  padding: 1px 7px;
}
.momx-news-why:hover:not(:disabled) { border-color: #38bdf8; color: #e0f2fe; }
.momx-news-why:disabled { opacity: 0.55; cursor: default; }
.momx-news-ai {
  border-top: 1px solid rgba(148, 163, 184, 0.22);
  margin-top: 8px;
  padding-top: 7px;
}
.momx-news-ai b { color: #e2e8f0; display: block; font-size: 12.5px; line-height: 1.42; }
.momx-news-ai small { color: rgba(148, 163, 184, 0.85); display: block; font-size: 10.5px; margin-top: 4px; }
/* A round-up that merely names this ticker. Same words as the badge tooltip. */
.momx-news-pop-scope {
  color: #a8a29e;
  font-size: 11px;
  margin: 6px 0 0;
}
"""
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("index.css: popover AI + scope styles")
