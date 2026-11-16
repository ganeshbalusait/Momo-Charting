// "Analysts" card (2026-11-11), on the Long Term ticker detail (the LEAP page) and in the chat's ticker report:
// a half circle split by Strong Buy / Buy / Hold / Sell / Strong Sell, the consensus word in the middle, the counts,
// "Based on N analysts, updated <date>" with the source, a price-target bar (low / average / high against today's
// price) and a small month-by-month mix. All numbers are the server's (GET /api/long-term/analyst). No data, no card.
import { useMemo } from "react";
import { RATINGS, analystCard, arcPath, monthLabel, signedPct } from "./analystGauge.js";
import "./analysisCards.css";

const CX = 100;
const CY = 100;
const R = 88;
const W = 16;
const money = (v) => `$${v >= 1000 ? v.toFixed(0) : v.toFixed(2)}`;

export default function AnalystCard({ data, price = null }) {
  const card = useMemo(() => analystCard(data, price), [data, price]);
  if (!card) return null;
  const { gauge, target, months } = card;
  const tone = /buy/i.test(gauge.consensus) ? "up" : /sell/i.test(gauge.consensus) ? "down" : "flat";
  return (
    <section className="an-card" data-testid="analyst-card">
      <header className="ta-head"><b>Analysts</b></header>
      <div className="an-gauge">
        <svg viewBox="0 0 200 108" role="img" aria-label={`Consensus ${gauge.consensus}, ${gauge.total} analysts`}>
          {gauge.segments.map((s) => (s.count ? <path key={s.key} d={arcPath(CX, CY, R, W, s.from, s.to)} fill={s.color} data-testid={`an-seg-${s.key}`} /> : null))}
        </svg>
        <span className="an-middle"><b className={`an-word is-${tone}`} data-testid="an-consensus">{gauge.consensus}</b><small>{gauge.total} analysts</small></span>
      </div>
      <ul className="an-counts">
        {[...gauge.segments].reverse().map((s) => (
          <li key={s.key}><i style={{ background: s.color }} />{s.label}<b>{s.count}</b><span>{s.pct}%</span></li>
        ))}
      </ul>
      <p className="an-based" data-testid="an-based">
        Based on {gauge.total} {gauge.total === 1 ? "analyst" : "analysts"}{card.updated ? `, updated ${card.updated}` : ""}{card.source ? ` · ${card.source}` : ""}
      </p>
      {target ? (
        <div className="an-target" data-testid="an-target">
          <b className="an-sub">Price target</b>
          <div className="an-bar">
            <span className="an-track" />
            {target.marks.map((m) => <span key={m.key} className={`an-dot is-${m.key}`} style={{ left: `${m.pos}%` }} title={`${m.label} ${money(m.price)}`} />)}
            <span className="an-today" style={{ left: `${target.today.pos}%` }} title={`Today ${money(target.today.price)}`} />
          </div>
          <div className="an-marks">
            {target.marks.map((m) => (
              <span key={m.key} data-testid={`an-mark-${m.key}`}>{m.label}<b>{money(m.price)}</b><em className={m.pct >= 0 ? "is-up" : "is-down"}>{signedPct(m.pct)}</em></span>
            ))}
            <span>Today<b>{money(target.today.price)}</b></span>
          </div>
          {card.targetSource ? <p className="an-based">Targets · {card.targetSource}</p> : null}
        </div>
      ) : null}
      {months.length > 1 ? (
        <div className="an-months" data-testid="an-months">
          <b className="an-sub">Month by month</b>
          <div className="an-month-row">
            {months.map((m) => (
              <div key={m.period} className="an-month" title={`${m.period}: ${m.total} analysts`}>
                <span className="an-stack">
                  {[...m.parts].reverse().map((p) => (p.pct ? <i key={p.key} style={{ height: `${p.pct}%`, background: p.color }} /> : null))}
                </span>
                <small>{monthLabel(m.period)}</small>
              </div>
            ))}
          </div>
          <span className="an-legend">{[...RATINGS].reverse().map((r) => <span key={r.key}><i style={{ background: r.color }} />{r.label}</span>)}</span>
        </div>
      ) : null}
    </section>
  );
}
