// Strike reach, on the ⚡ ticker card: how far away a strike is, measured in
// the options market's own expected move for that expiry.
//
// Approved 2026-09-22 after six far-out weekly calls lost (SHOP 160C with
// SHOP at $146.43, GOOGL 370C with GOOGL at $361.17): each needed 1.3-1.7x
// the expected move by expiry. This shows that number before the entry.
//
// Two rules that matter:
//   * The PRICE is the scanner's live last (row.last). The chain's
//     underlyingPrice is captured with the chain and can be many minutes old -
//     measured SHOP $137.81 in the chain vs $146.43 live. It is only a
//     fallback, and says so when used.
//   * The chain is the one the card already loaded for the High-OI walls; it
//     is passed in, never fetched here.
//
// All state is local to this component, so typing in the box re-renders
// this block and nothing else.

import { memo, useEffect, useMemo, useState } from "react";

import { formatCompactVolume } from "./highOiContractList.js";
import {
  etMinutesSinceMidnight,
  expiryLabel,
  parseStrikeQuery,
  reachRows,
  strikeReach,
  todayInNewYork,
  upcomingExpiries,
  wallReach,
} from "./strikeReach.js";

const STALE_AFTER_MIN = 15;

function dollars(value) {
  const number = Number(value);
  return Number.isFinite(number) ? "$" + number.toFixed(2) : "--";
}

function etClock(ms, nowMs) {
  try {
    const clock = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(ms));
    // A chain from an earlier day says which day, or "16:44" reads as today.
    if (todayInNewYork(ms) === todayInNewYork(nowMs)) return clock;
    const day = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
    }).format(new Date(ms));
    return day + " " + clock;
  } catch {
    return "";
  }
}

function ageWords(minutes) {
  if (minutes < 1) return "just now";
  if (minutes < 120) return minutes + " min old";
  return Math.round(minutes / 60) + " h old";
}

/**
 * "chain data from 10:42 ET, 3 min old" plus which warning, if any, belongs
 * under it:
 *   "unknown"      - no scannedAt at all, age cannot be judged either way.
 *                    Warn regardless of the server's own `stale` flag - an
 *                    unknown age is never a reason to stay quiet.
 *   "server-stale" - the server marked it stale, but our own clock says it
 *                    is still within STALE_AFTER_MIN. Its own wording, so
 *                    "3 min old" never sits next to "may be out of date" -
 *                    that combination reads as a contradiction.
 *   "old"          - past STALE_AFTER_MIN by our own clock (server flag or
 *                    not - the age alone already explains it).
 */
function chainAge(chain, nowMs) {
  const at = Date.parse(String(chain?.scannedAt || ""));
  const serverStale = chain?.stale === true;
  if (!Number.isFinite(at)) {
    return { text: "chain capture time unknown", minutes: null, warn: "unknown" };
  }
  const minutes = Math.max(0, Math.floor((nowMs - at) / 60000));
  let warn = null;
  if (minutes > STALE_AFTER_MIN) warn = "old";
  else if (serverStale) warn = "server-stale";
  return {
    text: "chain data from " + etClock(at, nowMs) + " ET, " + ageWords(minutes),
    minutes,
    warn,
  };
}

/** Ticks once a minute so the age line stays true while the card is open. */
function useMinuteClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function ReachResult({ query, expiries, expiriesWithMoves, moves, spot, symbol }) {
  if (!query.trim()) return null;
  const parsed = parseStrikeQuery(query, expiries, expiriesWithMoves);
  if (!parsed) {
    return (
      <p className="momx-reach-result is-note">
        can&apos;t read that - type a strike, C or P, and an expiry, e.g. 160C Fri or 95P 9/25
      </p>
    );
  }
  if (!parsed.expiry) {
    const listed = expiries.slice(0, 4).map(expiryLabel).join(", ");
    return (
      <p className="momx-reach-result is-note">
        {parsed.unmatchedExpiry
          ? "no " + parsed.unmatchedExpiry.charAt(0).toUpperCase() + parsed.unmatchedExpiry.slice(1)
            + " expiry listed for " + symbol
          : "no expiries listed for " + symbol}
        {listed ? " - listed: " + listed : ""}
      </p>
    );
  }
  const label = expiryLabel(parsed.expiry);
  const em = Number(moves[parsed.expiry]);
  const reach = strikeReach({ strike: parsed.strike, side: parsed.side, spot, em });
  const contract = parsed.strike + parsed.side + " " + label;
  if (!reach) {
    // A listed expiry (from the chain) with no expected move yet - distinct
    // from an unlisted one, which is the `unmatchedExpiry` branch above.
    return (
      <p className="momx-reach-result is-note">
        no expected move available for {label} yet
      </p>
    );
  }
  if (reach.band === "itm") {
    return (
      <p className="momx-reach-result is-itm">
        <b>{contract}</b> already in the money by {dollars(-reach.need)}
      </p>
    );
  }
  const sign = parsed.side === "C" ? "+" : "-";
  return (
    <p className={"momx-reach-result is-" + reach.band}>
      <b>{contract}</b> needs {sign}
      {dollars(reach.need)} ({sign}
      {reach.needPct.toFixed(1)}%) by {label} = {reach.multiple.toFixed(2)}× the expected move
    </p>
  );
}

/**
 * One wall line: where the open interest sits on that side, and how far the
 * price has to travel to reach it. "--" for the multiple when the chain has
 * no expected move to measure against; the wall itself is still worth seeing.
 */
function WallReachRow({ wall }) {
  if (!wall) return null;
  const up = wall.side === "C";
  const sign = up ? "+" : "-";
  const distance = wall.need === null
    ? null
    : wall.need <= 0
      ? "price is there"
      : sign + dollars(wall.need) + " = " + wall.multiple.toFixed(2) + "×";
  return (
    <li className={"momx-reach-wall is-" + (wall.band || "none")}>
      <span className={"momx-reach-wall-strike is-" + (up ? "call" : "put")}>
        {up ? "↑" : "↓"} {wall.strike}
        {up ? "C" : "P"}
      </span>
      <span className="momx-reach-wall-oi">{formatCompactVolume(wall.openInterest)} OI</span>
      {wall.label ? <span className="momx-reach-wall-exp">{wall.label}</span> : null}
      <span className="momx-reach-wall-need">
        {distance === null ? "no expected move to measure it" : distance}
        {/* A monthly wall measured against a weekly move would read as far
            closer than it is - name the expiry that move belongs to. */}
        {distance && wall.emFromOtherExpiry && wall.emLabel
          ? " (" + wall.emLabel + " move)"
          : ""}
      </span>
    </li>
  );
}

function MomxStrikeReach({ symbol, last, chain, chainState, walls }) {
  const [query, setQuery] = useState("");
  const nowMs = useMinuteClock();

  // A different symbol is a different question.
  useEffect(() => {
    setQuery("");
  }, [symbol]);

  const live = Number(last);
  const chainPrice = Number(chain?.underlyingPrice);
  const usingLive = Number.isFinite(live) && live > 0;
  const spot = usingLive ? live : Number.isFinite(chainPrice) && chainPrice > 0 ? chainPrice : null;

  const today = todayInNewYork(nowMs);
  const nowEtMinutes = etMinutesSinceMidnight(nowMs);
  const moves = useMemo(() => {
    const raw = chain?.expiryExpectedMoves;
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  }, [chain]);
  const chainExpiries = useMemo(
    () => (Array.isArray(chain?.expiries) ? chain.expiries : []),
    [chain],
  );
  // Every expiry the chain lists, PLUS every one with a move (the two do not
  // always agree) - so typing an expiry that exists but has no move yet
  // reads as "no expected move for it", never "no such expiry".
  const expiries = useMemo(
    () => upcomingExpiries([...chainExpiries, ...Object.keys(moves)], today, nowEtMinutes),
    [chainExpiries, moves, today, nowEtMinutes],
  );
  // The subset with a move, soonest first - what a bare "160C" should
  // default to. Listed-but-move-less expiries must never win that default.
  const expiriesWithMoves = useMemo(
    () => upcomingExpiries(Object.keys(moves), today, nowEtMinutes),
    [moves, today, nowEtMinutes],
  );
  const rows = useMemo(
    () => reachRows(moves, spot, today, 3, nowEtMinutes),
    [moves, spot, today, nowEtMinutes],
  );
  const wallRows = useMemo(
    () => wallReach({ walls, spot, expectedMoves: moves, todayIso: today, nowEtMinutes }),
    [walls, spot, moves, today, nowEtMinutes],
  );

  const loading = chainState === "loading" || chainState === "idle";
  const header = (
    <h4>
      Strike reach{" "}
      <em>
        {spot ? (usingLive ? "vs live " : "vs chain price ") + dollars(spot) : ""}
      </em>
    </h4>
  );

  if (loading) {
    return (
      <div className="momx-reach">
        {header}
        <p className="momx-walls-note">reading the option chain…</p>
      </div>
    );
  }
  if (chainState === "failed") {
    // Distinct from "chain loaded fine, just nothing in it" below - this one
    // says try again, that one says there is nothing to try again for.
    return (
      <div className="momx-reach">
        {header}
        <p className="momx-walls-note">couldn&apos;t load the option chain — try reopening the card</p>
      </div>
    );
  }
  if (!chain || rows.length === 0) {
    return (
      <div className="momx-reach">
        {header}
        <p className="momx-walls-note">
          {!spot ? "no price to measure from" : "no expected moves in the option chain for this one"}
        </p>
      </div>
    );
  }

  const age = chainAge(chain, nowMs);

  return (
    <div className="momx-reach">
      {header}
      <ul className="momx-reach-rows">
        {rows.map((r) => (
          <li key={r.expiry}>
            <span className="momx-reach-exp">
              <b>{r.label}</b> · expected ±{dollars(r.em)} ({r.emPct.toFixed(1)}%)
            </span>
            <span className="is-call">
              calls within 1×: up to {dollars(r.call1x)} · 2×: {dollars(r.call2x)}
            </span>
            <span className="is-put">
              puts within 1×: down to {dollars(r.put1x)} · 2×: {dollars(r.put2x)}
            </span>
          </li>
        ))}
      </ul>

      {wallRows && (wallRows.call || wallRows.put) ? (
        <ul className="momx-reach-walls">
          <li className="momx-reach-walls-head">Nearest wall each side</li>
          <WallReachRow wall={wallRows.call} />
          <WallReachRow wall={wallRows.put} />
        </ul>
      ) : null}

      <label className="momx-reach-check">
        <span>Check a strike</span>
        <input
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          placeholder="e.g. 160C Thu"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Check a strike (e.g. 160C Thu)"
        />
      </label>
      {spot ? (
        <ReachResult
          query={query}
          expiries={expiries}
          expiriesWithMoves={expiriesWithMoves}
          moves={moves}
          spot={spot}
          symbol={symbol}
        />
      ) : null}

      <p className="momx-reach-age">
        {age.text}
        {usingLive ? "" : " · price is the chain's, not live"}
      </p>
      {age.warn === "unknown" ? (
        <p className="momx-reach-warn">
          capture time unknown — expected moves may be out of date
        </p>
      ) : age.warn === "server-stale" ? (
        <p className="momx-reach-warn">
          chain data marked stale by the server ({ageWords(age.minutes)})
        </p>
      ) : age.warn === "old" ? (
        <p className="momx-reach-warn">
          chain data is {ageWords(age.minutes)} — expected moves may be out of date
        </p>
      ) : null}
      <p className="momx-reach-define">
        Expected move = the size of move today&apos;s option prices are paying for by that expiry.
        Not a forecast, not a recommendation.
      </p>
    </div>
  );
}

export default memo(MomxStrikeReach);
