// The ticker card: one symbol, everything the scan knows about it, in the
// layout he works from (2026-09-02, from a screenshot of WFC).
//
//     WFC  +2.78%  $89.46  [Banks]              Wed Sep 2 10:31 AM ET
//     RV 5 .. RV D  H/L  Sqz 2h .. Sqz Wk  Sk 2h .. Sk Mo
//     ✦ no fresh catalyst on the feed
//     1-hr high 89.47 / 1-hr low 87.85
//     ⚡ 9x20 D · MACD 2D · 4x8 2D · 4x8 3D · MACD W · MACD Mo
//
// Its own file rather than another block inside MomxScannerPanel, which is
// already several thousand lines: this reads one row and renders it, and can
// be understood without the board around it.
//
// EVERY number here is the same cell object the board renders, styled by the
// same cellStyle, so a value can never disagree between the card and the row
// it was opened from. Nothing is recomputed locally - that is how two views
// of one number drift apart.
//
// The High-OI walls block comes from the options chain, a different subsystem
// to this board, so it loads separately and says so while it does. It reuses
// buildHighOiContractList - the chart board's own selection - rather than
// re-deriving "top by OI within ±EM2" here, because two copies of a rule like
// that drift.

import { alertSignalTexts } from "./momxFilters.js";
import { fivePillars } from "./momxPillars.js";
import { useEffect, useMemo, useState } from "react";
import { Zap } from "lucide-react";

import MomxStrikeReach from "./MomxStrikeReach.jsx";
import { buildHighOiContractList, formatCompactVolume } from "./highOiContractList.js";
import { ageOf, cellStyle, firesOf, formatPctChange, formatRvol, hlBlockStyle, newsOf } from "./momxCells.js";

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
//: A symbol the OI finder has never built takes ~15-20s to warm - measured
//: on IBM, 2026-09-02: empty at 0s, 4s, 8s and 12s, filled by 17s. The first
//: cut retried three times over 4.5s and gave up just before the data
//: arrived, which is how a warming chain became "no option-chain walls for
//: this one" on a symbol with plenty. 3s apart for a minute covers it with
//: room to spare; every attempt before the data lands is a tiny response.
const WALLS_RETRIES = 20;
const WALLS_RETRY_MS = 3000;
const wallsCache = new Map();   // SYMBOL -> { at, promise }  (successes only)

function chainHasRows(payload) {
  if (!payload || typeof payload !== "object") return false;
  // Judged on the rows the walls actually rank, or a chain that arrives with
  // only the subset populated would be cached as a usable answer.
  return (
    (payload.selectedExpiryChainRows || []).length > 0
    || (payload.callRows || []).length > 0
    || (payload.putRows || []).length > 0
  );
}

/** Rows AND current. The server sets `stale` itself and we were ignoring it.
 *
 *  2026-09-03: the first call for CRWV returned a full, well-formed chain
 *  from YESTERDAY - spot 80.57 against a live 83.78, a Sep-4 expiry labelled
 *  2 DTE on Sep 3 - with `cached: true, stale: true` right there in the
 *  payload. The card would have drawn yesterday's walls and yesterday's
 *  expected move as today's, and then held them for ten minutes. He trades
 *  options off those levels; a day-old wall is worse than no wall.
 *
 *  Same shape as the miss that cached a warming chain as "no walls": asking
 *  whether the data EXISTS instead of whether it is USABLE.
 */
function chainIsUsable(payload) {
  if (!chainHasRows(payload)) return false;
  return payload.stale !== true;
}

async function fetchChainOnce(symbol) {
  try {
    const response = await fetch("/api/oi-finder?symbol=" + encodeURIComponent(symbol), {
      cache: "no-store",
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** The chain for one symbol. ONLY a payload with rows is cached.
 *
 *  The first request for a cold symbol is answered while the server is still
 *  warming, with no rows. Caching that for ten minutes - which the first cut
 *  of this did - turned a two-second wait into "no option-chain walls for
 *  this one" for ten minutes, on BOIL, which has plenty (2026-09-02).
 */
function loadChain(symbol) {
  const key = String(symbol || "").toUpperCase();
  const hit = wallsCache.get(key);
  if (hit && Date.now() - hit.at < WALLS_TTL_MS) return hit.promise;

  let lastResort = null;
  const promise = (async () => {
    for (let attempt = 0; attempt < WALLS_RETRIES; attempt += 1) {
      const payload = await fetchChainOnce(key);
      if (chainIsUsable(payload)) return payload;
      // Keep the last stale copy: if every attempt is stale (a quiet
      // symbol after hours) it is still better shown WITH its timestamp
      // than not shown at all.
      if (chainHasRows(payload)) lastResort = payload;
      if (attempt < WALLS_RETRIES - 1) {
        await new Promise((resolve) => setTimeout(resolve, WALLS_RETRY_MS));
      }
    }
    return lastResort;
  })();

  // Held provisionally so two cards opened together share one request, then
  // DROPPED unless it found rows - a miss must not outlive its own attempt.
  wallsCache.set(key, { at: Date.now(), promise });
  promise.then((payload) => {
    if (!chainIsUsable(payload) && wallsCache.get(key)?.promise === promise) {
      wallsCache.delete(key);
    }
  });
  return promise;
}

/** The option chain for one symbol, loaded ONCE per card and shared by the
 *  High-OI walls and Strike reach blocks - two consumers, one request. */
function useOptionChain(symbol) {
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

  return { chain, state };
}

/** The walls from a loaded chain, or null when the chain is quiet. */
function useHighOiWalls(chain) {
  const model = useMemo(() => {
    if (!chain) return null;
    // THE FULL CHAIN, not callRows/putRows. Those are a pre-filtered subset
    // (18 rows for KVUE against the chain's 308) built for a different panel,
    // and ranking them produced walls that were simply wrong: 22.5C with an
    // open interest of ONE, while 20C sat on 10,955 and never appeared. He
    // caught it - "High OI 19, 19.5, 20 C / Put - 18.5, 18" - and the full
    // chain reproduces exactly that: 20C(10955) 19.5C(503) 19C(5347) and
    // 18.5P / 18P(5862). This is the same input the chain panel's own High-OI
    // board is handed, which is why they now agree.
    const rows = chain.selectedExpiryChainRows
      || [...(chain.callRows || []), ...(chain.putRows || [])];
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

  return model;
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

function HighOiWalls({ chain, state, fallbackLast }) {
  const model = useHighOiWalls(chain);

  // The note sits INSIDE the walls container so the card keeps its two
  // columns whether or not there are walls; as a bare <p> it collapsed and
  // the hourly numbers slid up beside it (his BOIL screenshot).
  if (state === "loading" || state === "idle") {
    return (
      <div className="momx-walls">
        <h4>High-OI walls</h4>
        <p className="momx-walls-note">
          reading the option chain… a ticker the board has not opened before takes about 20 seconds
        </p>
      </div>
    );
  }
  if (!model || (model.calls.length === 0 && model.puts.length === 0)) {
    return (
      <div className="momx-walls">
        <h4>High-OI walls</h4>
        <p className="momx-walls-note">no option-chain walls for this one</p>
      </div>
    );
  }

  const spot = model.spot || Number(fallbackLast) || 0;
  const band = model.expectedMove * 2;
  // Bare numbers here, unlike the header's money(): his card prints
  // "89.46 last · ±EM2 87.64 - 90.84" without dollar signs.
  const level = (value) => (Number.isFinite(value) ? value.toFixed(2) : "--");

  return (
    <div className="momx-walls">
      <h4>
        High-OI walls <em>within ±EM2</em>
      </h4>
      {model.calls.map((item) => (
        <WallRow key={"c" + item.strike} item={item} side="call" peakOi={model.peakOi} />
      ))}
      <p className="momx-walls-spot">
        <span aria-hidden="true">▶</span> {level(spot)} last
        {band > 0 ? (
          <em>
            {" "}
            · ±EM2 {level(spot - band)} – {level(spot + band)}
          </em>
        ) : null}
      </p>
      {model.puts.map((item) => (
        <WallRow key={"p" + item.strike} item={item} side="put" peakOi={model.peakOi} />
      ))}
    </div>
  );
}

// The strip, in his screenshot's order. Keys are the wire's; labels are his.
const RVOL_CELLS = [
  ["5m", "RV 5"], ["15m", "RV 15"], ["30m", "RV 30"], ["1h", "RV 1h"],
  ["2h", "RV 2h"], ["4h", "RV 4h"], ["D", "RV D"],
];
const SQZ_CELLS = [["2h", "Sqz 2h"], ["4h", "Sqz 4h"], ["D", "Sqz D"], ["Wk", "Sqz W"]];
const SKIT_CELLS = [
  ["2h", "Sk 2h"], ["4h", "Sk 4h"], ["D", "Sk D"], ["2D", "Sk 2D"],
  ["3D", "Sk 3D"], ["4D", "Sk 4D"], ["Wk", "Sk W"], ["M", "Sk Mo"],
];

function money(value) {
  const number = Number(value);
  return Number.isFinite(number) ? "$" + number.toFixed(2) : "--";
}

function plain(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}

/** H/L, drawn the way the board draws it: a centre-anchored bar, not a number.
 *
 *  The board renders this column as a graph on purpose - the value is the
 *  script's degree, -1 at the N-bar low to +1 at the high, and a bar shows the
 *  magnitude where a printed "1.0" only shows the reading. The card was
 *  printing the number, which he spotted against his own card ("High/low give
 *  just bar no number"). Same geometry and the same backend colour as
 *  MomxScannerPanel's bar column, so the two cannot disagree.
 */
function HighLowCell({ cell }) {
  // Same MomoX-style block and colours as the board (momxCells.hlBlockStyle),
  // so the card and the row can never disagree (2026-09-25).
  const raw = cell && typeof cell === "object" ? Number(cell.value) : NaN;
  const block = hlBlockStyle(cell);
  return (
    <div className="momx-card-cell" title={Number.isFinite(raw) ? raw.toFixed(2) : undefined}>
      <span className="momx-card-cell-label">H/L</span>
      <span className="momx-card-cell-value is-bar">
        {block ? (
          <span className="momx-hl-block" data-hl={cell.bg}>
            <span className="momx-hl-block-fill" style={{ backgroundColor: block.color, width: block.widthPct + "%" }} />
          </span>
        ) : null}
      </span>
    </div>
  );
}

/** One measured cell, coloured exactly as the board colours it. */
function Cell({ label, cell, format }) {
  const value = cell && typeof cell === "object" ? cell.value : null;
  const text = format ? format(value) : value === null || value === undefined ? "" : String(value);
  return (
    <div className="momx-card-cell" title={label}>
      <span className="momx-card-cell-label">{label}</span>
      <span className="momx-card-cell-value" style={cellStyle(cell)}>
        {text}
      </span>
    </div>
  );
}

export default function MomxTickerCard({
  row,
  stampLabel,
  nowMs = Date.now(),
  // `frozen` = this is an archived moment from History, not the live board.
  // It suppresses the two things a snapshot cannot honestly carry rather than
  // filling them with today's numbers under yesterday's timestamp.
  frozen = false,
  allowWalls = true,
  // A MomoX-style alert card (auto-opened when a setup fired): {label, cameAt}.
  alert = null,
}) {
  // Before the early return below: hooks run on every render. null = no fetch
  // (a History snapshot, or no row).
  const chainSymbol = allowWalls && row && typeof row === "object" ? row.symbol || null : null;
  const { chain, state: chainState } = useOptionChain(chainSymbol);
  // The walls block below builds this too; both calls are the same useMemo on
  // the same `chain`, so Strike reach reads exactly what the walls draw.
  const walls = useHighOiWalls(chain);

  if (!row || typeof row !== "object") {
    // The symbol left the board while its card was open - it stopped matching,
    // or the list was edited. Say so; a blank card reads as a broken card.
    return (
      <section className="momx-ticker-card is-empty">
        <p className="momx-card-gone">
          This symbol is not on the board right now. It will fill back in if the scan picks it up
          again.
        </p>
      </section>
    );
  }

  const up = Number(row.pctChange) > 0;
  const down = Number(row.pctChange) < 0;
  const news = newsOf(row, nowMs);
  const fires = firesOf(row);
  const hourly = row.hourHighLow && typeof row.hourHighLow === "object" ? row.hourHighLow : {};
  const signals = frozen ? [] : alertSignalTexts(row, nowMs);
  // MomoX Five Pillars checklist - live cards only (a History snapshot has
  // no chain of its own moment to judge the expected move or the walls).
  const pillars = frozen ? null : fivePillars(row, { chain, walls, nowMs });

  return (
    <section className="momx-ticker-card">
      <header className="momx-card-head">
        <div className="momx-card-id">
          <b className={"momx-card-symbol" + (up ? " is-up" : down ? " is-down" : "")}>
            {row.symbol}
          </b>
          <span className={"momx-card-pct" + (up ? " is-up" : down ? " is-down" : "")}>
            {formatPctChange(row.pctChange)}
          </span>
          <span className="momx-card-last">{money(row.last)}</span>
          {row.industry ? <span className="momx-card-sector">{row.industry}</span> : null}
        </div>
        <div className="momx-card-stamp">
          <span>{stampLabel || ""}</span>
          <small>RVOL · SQZ · SKIT live</small>
        </div>
      </header>

      {alert ? (
        <p className="momx-card-alert">
          <span aria-hidden="true">⚡</span> {alert.label}
          {alert.cameAt ? <em> · came {alert.cameAt} ET</em> : null}
        </p>
      ) : null}
      {signals && signals.length ? (
        <p className="momx-card-signals" title="Every tag on this ticker in the Setup column right now">
          <span aria-hidden="true">⚡</span> {signals.join(" · ")}
        </p>
      ) : null}

      <div className="momx-card-strip" aria-label="Measured cells">
        {RVOL_CELLS.map(([key, label]) => (
          <Cell key={"rv-" + key} label={label} cell={(row.rvol || {})[key]} format={formatRvol} />
        ))}
        <HighLowCell cell={row.highLow} />
        {SQZ_CELLS.map(([key, label]) => (
          <Cell key={"sqz-" + key} label={label} cell={(row.sqz || {})[key]} />
        ))}
        {SKIT_CELLS.map(([key, label]) => (
          <Cell key={"sk-" + key} label={label} cell={(row.skittles || {})[key]} />
        ))}
      </div>

      {pillars ? (
        <div className={"momx-pillars" + (pillars.ready ? " is-ready" : "")} aria-label="Five pillars checklist">
          <div className="momx-pillars-head">
            <span>FIVE PILLARS</span>
            <b>{pillars.ready ? "READY ✓" : pillars.passed + " of 5"}</b>
          </div>
          {pillars.rows.map((r) => (
            <div key={r.n} className={"momx-pillar " + (r.pass === true ? "is-pass" : r.pass === false ? "is-fail" : "is-wait")}>
              <span className="momx-pillar-mark" aria-hidden="true">{r.pass === true ? "✅" : r.pass === false ? "✖" : "…"}</span>
              <span className="momx-pillar-name">{"0" + r.n + " " + r.name}</span>
              <span className="momx-pillar-text">{r.text}</span>
            </div>
          ))}
        </div>
      ) : null}

      <p className={"momx-card-catalyst" + (news ? "" : " is-quiet")}>
        <span aria-hidden="true">✦</span>
        {news ? (
          <>
            {news.headline}
            {news.age ? <em> ({news.age})</em> : null}
          </>
        ) : (
          "no fresh catalyst on the feed"
        )}
      </p>

      <div className="momx-card-foot">
        {allowWalls ? (
          <HighOiWalls chain={chain} state={chainState} fallbackLast={row.last} />
        ) : (
          <div className="momx-walls">
            <h4>High-OI walls</h4>
            <p className="momx-walls-note">
              not kept for past days — open interest changes overnight, so today&apos;s walls were
              not these
            </p>
          </div>
        )}
        <div className="momx-card-side">
        {frozen ? null : (
          // Never on a snapshot: the hour's range was not recorded then, and
          // printing the CURRENT hour's under an 07:26 row would be invention.
          <dl className="momx-card-hourly">
            <div>
              <dt>1-hr high</dt>
              <dd>{plain(hourly.high)}</dd>
            </div>
            <div>
              <dt>1-hr low</dt>
              <dd>{plain(hourly.low)}</dd>
            </div>
          </dl>
        )}
        <p className={"momx-card-fires" + (fires.length ? "" : " is-quiet")}>
          <Zap size={12} aria-hidden="true" />
          {fires.length ? fires.join(" · ") : "no signals fired on this one"}
        </p>
        </div>
      </div>

      {allowWalls && !frozen ? (
        // Live cards only: a snapshot's expected moves were yesterday's prices.
        <MomxStrikeReach
          symbol={row.symbol}
          last={row.last}
          chain={chain}
          chainState={chainState}
          // The SAME model the walls above are drawn from, so the "nearest
          // wall" line can never name a strike the walls do not show.
          walls={walls}
        />
      ) : null}

      {row.matchedSince ? (
        <p className="momx-card-since">on the board since {ageOf(row.matchedSince, nowMs)}</p>
      ) : null}
    </section>
  );
}
