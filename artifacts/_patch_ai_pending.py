import io
p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """  const askWhy = async () => {
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
  };"""
NEW = """  const askWhy = async (attempt = 0) => {
    if (attempt === 0 && aiState === "loading") return;
    setAiState("loading");
    try {
      const response = await fetch(
        "/api/ai/catalyst?symbol=" + encodeURIComponent(symbol || ""),
      );
      const payload = await response.json();
      const answered = payload && typeof payload === "object";
      // THE FIRST CALL USUALLY HAS NO ANSWER YET. The endpoint hands back a
      // placeholder ("The AI is writing this now...") with summary null while a
      // daemon thread does the model call, then serves the real answer from its
      // cache moments later. Treating that as the answer would print "No
      // answer." over a question that is still being worked on - measured on
      // AAPL 2026-09-04. So: keep waiting, and only give up after a bounded
      // number of tries so a stuck build cannot spin forever.
      const pending = answered && payload.summary == null && payload.available !== false;
      if (pending && attempt < 6) {
        setAiRead(payload);
        window.setTimeout(() => askWhy(attempt + 1), 2500);
        return;
      }
      setAiRead(answered ? payload : null);
      setAiState("done");
    } catch {
      // A failed explanation must never be mistaken for "no news explains it".
      setAiRead(null);
      setAiState("error");
    }
  };"""
assert s.count(OLD) == 1, "askWhy anchor"
s = s.replace(OLD, NEW)

# while loading, show the endpoint's own "writing this now" line rather than nothing
OLD2 = """        {aiState === "done" && aiRead ? ("""
NEW2 = """        {aiState === "loading" && aiRead && aiRead.summary == null && aiRead.reason ? (
          <div className="momx-news-ai"><small>{aiRead.reason}</small></div>
        ) : null}
        {aiState === "done" && aiRead ? ("""
assert s.count(OLD2) == 1, "render anchor"
s = s.replace(OLD2, NEW2)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("popover handles the pending answer")
