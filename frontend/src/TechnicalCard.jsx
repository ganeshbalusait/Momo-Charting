// "Technical analysis" card (2026-11-11), in the chat's ticker report and on the Long Term ticker detail: the
// Short / Mid / Long-Term pills, the chosen term's verdict, a 4-part strength bar, its one reason, and a small
// read-only Daily / Weekly chart with the events marked. Every verdict and price is the server's. Not on Charts & OI.
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import MiniEventChart from "./MiniEventChart.jsx";
import { technicalCard } from "./miniEventChart.js";
import "./analysisCards.css";

const ICON = { up: ArrowUp, down: ArrowDown, flat: Minus };

export default function TechnicalCard({ data, initialTerm = "short" }) {
  const card = useMemo(() => technicalCard(data), [data]);
  const [term, setTerm] = useState(initialTerm);
  const [frameKey, setFrameKey] = useState("daily");
  if (!card) return null;
  const chosen = card.pills.find((p) => p.key === term) || card.pills[0] || null;
  const frames = [["daily", "Daily"], ["weekly", "Weekly"]].filter(([k]) => card[k]);
  const shownFrame = card[frameKey] ? frameKey : frames[0]?.[0];
  const v = chosen?.verdict;
  return (
    <section className="ta-card" data-testid="technical-card">
      <header className="ta-head">
        <b>Technical analysis</b>
        {card.asOf ? <span className="ta-time">{card.asOf}</span> : null}
      </header>
      {card.pills.length ? (
        <div className="ta-pills" role="tablist" aria-label="Term">
          {card.pills.map((p) => {
            const Icon = ICON[p.verdict.tone];
            return (
              <button key={p.key} type="button" role="tab" aria-selected={p.key === chosen?.key} data-testid={`ta-pill-${p.key}`}
                className={`ta-pill is-${p.verdict.tone}${p.key === chosen?.key ? " is-on" : ""}`} onClick={() => setTerm(p.key)}>
                {p.label}<Icon size={13} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      ) : null}
      {v ? (
        <div className="ta-verdict" data-testid="ta-verdict">
          <b className={`is-${v.tone}`}>{v.word}</b>
          <span className="ta-strength" aria-label={`Strength ${v.strength} of 4`}>
            {[1, 2, 3, 4].map((n) => <i key={n} className={n <= v.strength ? `is-${v.tone}` : ""} />)}
          </span>
          {v.reason ? <p className="ta-reason">{v.reason}</p> : null}
        </div>
      ) : null}
      {frames.length ? (
        <>
          {frames.length > 1 ? (
            <div className="ta-frames" role="tablist" aria-label="Bars">
              {frames.map(([k, label]) => (
                <button key={k} type="button" role="tab" aria-selected={k === shownFrame} className={k === shownFrame ? "is-on" : ""}
                  data-testid={`ta-frame-${k}`} onClick={() => setFrameKey(k)}>{label}</button>
              ))}
            </div>
          ) : null}
          <MiniEventChart key={shownFrame} frame={card[shownFrame]} />
        </>
      ) : null}
      {card.source ? <p className="ta-foot">Source: {card.source}</p> : null}
    </section>
  );
}
