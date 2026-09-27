"""The High-OI walls block on the ticker card.

"I dont see the option chain like WFC" / "High OI WALL" (2026-09-02), with
the card he works from:

    HIGH-OI WALLS · WITHIN ±EM2 · OI AS OF 2026-08-31
       90C  39.3k  [==========================]
     ▶ 89.46 last · ±EM2 87.64 – 90.84
       89C   2.2k  [=]
       88C   4.6k  [==]

Reuses buildHighOiContractList - the SAME pure function the chart's High-OI
board runs - with emBandMultiple 2, which is literally the "±EM2" on his
card. Writing a second selection here would have been quicker and would have
drifted from the board within a week; the repo already records one attempt at
a parallel rule (proximity weighting) that leaked NVDA 180 into the list.

Fetched ONCE per card, not polled. Open interest is end-of-day data - the
card itself says "OI AS OF <a past date>" - so a 15-second poll would spend
300KB a time to re-read a number that changes overnight. Cached per symbol
for 10 minutes and shared across every open card.

The chain call is ~500ms and this app has a documented history of option
endpoints saturating the CPU, so it is deliberately lazy: nothing is fetched
until a card is actually open, and a card that is closed cancels nothing but
also asks for nothing more.
"""
import io

p = "frontend/src/MomxTickerCard.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''import { Zap } from "lucide-react";

import { ageOf, cellStyle, firesOf, formatPctChange, formatRvol, newsOf } from "./momxCells.js";'''
NEW = '''import { useEffect, useMemo, useState } from "react";
import { Zap } from "lucide-react";

import { buildHighOiContractList, formatCompactVolume } from "./highOiContractList.js";
import { ageOf, cellStyle, firesOf, formatPctChange, formatRvol, newsOf } from "./momxCells.js";

// ---------------------------------------------------------------------------
// High-OI walls
// ---------------------------------------------------------------------------
//
// Open interest is END-OF-DAY data, which is why his card stamps it "OI AS OF
// <a past date>". So this is fetched once per symbol and held for ten
// minutes, shared by every open card - not polled. The chain call is ~500ms
// and ~300KB, and option endpoints in this app have a recorded history of
// starving the chart engine, so nothing is requested until a card is actually
// open.

const WALLS_TTL_MS = 10 * 60 * 1000;
const wallsCache = new Map();   // SYMBOL -> { at, promise }

function loadChain(symbol) {
  const key = String(symbol || "").toUpperCase();
  const hit = wallsCache.get(key);
  if (hit && Date.now() - hit.at < WALLS_TTL_MS) return hit.promise;
  const promise = fetch("/api/oi-finder?symbol=" + encodeURIComponent(key), { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  wallsCache.set(key, { at: Date.now(), promise });
  return promise;
}

/** The walls for one symbol, or null while loading / when the chain is quiet. */
function useHighOiWalls(symbol) {
  const [chain, setChain] = useState(null);
  const [state, setState] = useState("idle");

  useEffect(() => {
    if (!symbol) return undefined;
    let alive = true;
    setState("loading");
    loadChain(symbol).then((payload) => {
      if (!alive) return;
      setChain(payload || null);
      setState(payload ? "ready" : "failed");
    });
    return () => {
      alive = false;
    };
  }, [symbol]);

  const model = useMemo(() => {
    if (!chain) return null;
    const rows = [...(chain.callRows || []), ...(chain.putRows || [])];
    if (rows.length === 0) return null;
    const atm = chain.currentAtm || {};
    const frontExpiry = String(atm.expiry || "").slice(0, 10);
    const expectedMove =
      Number((chain.expiryExpectedMoves || {})[frontExpiry]) || Number(atm.expectedMove) || 0;
    return buildHighOiContractList({
      rows,
      underlyingPrice: Number(chain.underlyingPrice) || 0,
      // THREE a side: his card shows the wall above and the two below, and a
      // card is a glance. The chart board's own eight would not fit.
      topPerSide: 3,
      scope: "monthly",
      frontExpiry,
      expectedMove,
      // "±EM2" on his card IS this multiple. Do not change one without the other.
      emBandMultiple: 2,
    });
  }, [chain]);

  return { model, state };
}

function WallRow({ item, side, peakOi }) {
  const share = peakOi > 0 ? Math.max(3, Math.round((item.openInterest / peakOi) * 100)) : 0;
  return (
    <div className="momx-wall-row">
      <span className={"momx-wall-strike is-" + side}>
        {item.strike}
        {side === "call" ? "C" : "P"}
      </span>
      <span className="momx-wall-oi">{formatCompactVolume(item.openInterest)}</span>
      <span className="momx-wall-bar" aria-hidden="true">
        <i className={"is-" + side} style={{ width: share + "%" }} />
      </span>
    </div>
  );
}

function HighOiWalls({ symbol, fallbackLast }) {
  const { model, state } = useHighOiWalls(symbol);

  if (state === "loading") return <p className="momx-walls-note">reading the option chain…</p>;
  if (!model || (model.calls.length === 0 && model.puts.length === 0)) {
    return <p className="momx-walls-note">no option-chain walls for this one</p>;
  }

  const spot = model.spot || Number(fallbackLast) || 0;
  const band = model.expectedMove * 2;
  const money = (value) => (Number.isFinite(value) ? value.toFixed(2) : "--");

  return (
    <div className="momx-walls">
      <h4>
        High-OI walls <em>within ±EM2</em>
      </h4>
      {model.calls.map((item) => (
        <WallRow key={"c" + item.strike} item={item} side="call" peakOi={model.peakOi} />
      ))}
      <p className="momx-walls-spot">
        <span aria-hidden="true">▶</span> {money(spot)} last
        {band > 0 ? (
          <em>
            {" "}
            · ±EM2 {money(spot - band)} – {money(spot + band)}
          </em>
        ) : null}
      </p>
      {model.puts.map((item) => (
        <WallRow key={"p" + item.strike} item={item} side="put" peakOi={model.peakOi} />
      ))}
    </div>
  );
}'''
assert s.count(OLD) == 1, "imports anchor"
s = s.replace(OLD, NEW)

# --- render it beside the hourly/fires column ------------------------------
OLD = '''      <div className="momx-card-foot">
        <dl className="momx-card-hourly">'''
NEW = '''      <div className="momx-card-foot">
        <HighOiWalls symbol={row.symbol} fallbackLast={row.last} />
        <div className="momx-card-side">
        <dl className="momx-card-hourly">'''
assert s.count(OLD) == 1, "foot anchor"
s = s.replace(OLD, NEW)

OLD = '''          {fires.length ? fires.join(" · ") : "no signals fired on this one"}
        </p>
      </div>'''
NEW = '''          {fires.length ? fires.join(" · ") : "no signals fired on this one"}
        </p>
        </div>
      </div>'''
assert s.count(OLD) == 1, "fires close anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxTickerCard.jsx: High-OI walls block")

# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
css += '''
/* High-OI walls on the ticker card. Same selection as the chart's High-OI
   board (buildHighOiContractList, emBandMultiple 2) - the bars here are
   width-as-share-of-the-biggest-wall, which is presentation, not parity. */
.momx-card-foot { align-items: stretch; }

.momx-card-side {
  display: flex;
  flex: 1 1 300px;
  flex-direction: column;
  gap: 12px;
}

.momx-walls { display: grid; gap: 3px; flex: 1 1 320px; min-width: 280px; }

.momx-walls h4 {
  margin: 0 0 4px;
  color: #74748a;
  font-size: .55rem;
  font-weight: 850;
  letter-spacing: .09em;
  text-transform: uppercase;
}
.momx-walls h4 em { color: #5d5d6b; font-style: normal; }

.momx-wall-row { display: grid; grid-template-columns: 58px 46px 1fr; align-items: center; gap: 8px; }

.momx-wall-strike { font-size: .7rem; font-weight: 850; font-variant-numeric: tabular-nums; text-align: right; }
.momx-wall-strike.is-call { color: #4edcff; }
.momx-wall-strike.is-put { color: #ff7ad9; }

.momx-wall-oi { color: #9a9aa6; font-size: .62rem; font-weight: 800; font-variant-numeric: tabular-nums; text-align: right; }

.momx-wall-bar { display: block; height: 11px; }
.momx-wall-bar i { display: block; height: 100%; border-radius: 2px; }
.momx-wall-bar i.is-call { background: #22d3ee; }
.momx-wall-bar i.is-put { background: #f472b6; }

/* The last price, dashed above and below, exactly where his card puts it. */
.momx-walls-spot {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 5px 0;
  padding: 4px 0;
  border-top: 1px dashed #4a4a58;
  border-bottom: 1px dashed #4a4a58;
  color: #f5c542;
  font-size: .66rem;
  font-weight: 850;
  font-variant-numeric: tabular-nums;
}
.momx-walls-spot em { color: #9a9aa6; font-style: normal; font-weight: 700; }

.momx-walls-note { margin: 0; color: #6f6f7c; font-size: .62rem; font-style: italic; }
'''
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: walls styling")
