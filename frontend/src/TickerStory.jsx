import { useEffect, useRef, useState } from "react";

import "./simpleWhy.css";
import { MARKS } from "./simpleWhy.js";
import { storyUrl, STORY_POLL_PENDING_MS, STORY_POLL_READY_MS } from "./tickerStory.js";

// One ticker in simple words (2026-11-20, "If I see leap oi scanner like INTC I don't know anything like buyers or
// seller ... then I have to search agx flow ... I feel little confusion"). Shown at the top of LEAP OI Scanner and AGX
// Flow when a ticker is searched: the same card on both pages, built by the server from both (momx/ticker_story.py).
export default function TickerStory({ ticker, reveal = 0 }) {
  const [story, setStory] = useState(null);
  const [error, setError] = useState("");
  const box = useRef(null);
  // Opened by tapping a ticker card lower down (2026-11-20, "If I click anything like msft it's not showing anything"):
  // the page scrolls inside its own box on a phone, so bring the card itself into view.
  useEffect(() => {
    if (reveal && box.current?.scrollIntoView) box.current.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [reveal, ticker]);
  useEffect(() => {
    if (!ticker) return undefined;
    let alive = true;
    let timer = null;
    setStory(null);
    setError("");
    const load = async () => {
      try {
        const response = await fetch(storyUrl(ticker));
        const data = await response.json().catch(() => ({}));
        if (!alive) return;
        if (!response.ok) setError(data?.error || `Could not load ${ticker} (${response.status}).`);
        else { setError(""); setStory(data); }
        timer = window.setTimeout(load, response.ok && (data?.pending || !data?.ready) ? STORY_POLL_PENDING_MS : STORY_POLL_READY_MS);
      } catch {
        if (alive) { setError("Could not reach the app server."); timer = window.setTimeout(load, STORY_POLL_READY_MS); }
      }
    };
    load();
    return () => { alive = false; if (timer) window.clearTimeout(timer); };
  }, [ticker]);
  if (!ticker) return null;
  return (
    <section ref={box} className="simple-why ticker-story" data-testid="ticker-story" aria-label={`${ticker} in simple words`}>
      <h4>{ticker} in simple words</h4>
      {error ? <p className="simple-why-advice" role="alert">{error}</p> : null}
      {!story && !error ? <p>Loading…</p> : null}
      {story ? (
        <>
          <p className="simple-why-verdict" data-testid="ticker-story-verdict">{story.verdict}</p>
          {story.sections.map((section) => (
            <div key={section.title} className="ticker-story-part">
              <h5>{section.title}</h5>
              <ul>
                {section.lines.map((line) => (
                  <li key={line.text} className={`is-${line.mark}`}><span aria-hidden="true">{MARKS[line.mark]}</span> {line.text}</li>
                ))}
              </ul>
            </div>
          ))}
          <p className="simple-why-meaning"><b>What this means: </b>{story.meaning}</p>
          <p className="simple-why-advice">{story.notAdvice}</p>
        </>
      ) : null}
    </section>
  );
}
