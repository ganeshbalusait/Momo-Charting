// Highest open-interest wall list for the chain's "High OI" view — the
// MomoX / Trading Alphas daily-sheet rule, validated panel-by-panel against
// their 2026-08-20 sheets (SPY QQQ DIA GLD SLV IBIT AAPL AMZN GOOGL META MSFT
// NVDA TSLA AVGO IWM USO AMD MU all share it):
//
//   * The board splits AT SPOT. A strike above spot is a CALL wall and shows
//     the call contract's OI; a strike at/below spot is a PUT wall and shows
//     the put contract's OI. ITM contracts are positioning history, never
//     walls — MSFT's 480 call held 24K while the sheet printed the 480 PUT's
//     8.4K, and listing that call under "Call" is what made our board
//     disagree with theirs.
//   * One row per strike: the dominant expiry (largest OI, then volume) wins.
//   * Top N per side by OI (the sheet prints 8), no delta filter of any kind;
//     candidates stay within ±6·EM of spot (unbounded ranking drifted to far
//     monthly mega-walls), and the ±EM values also place the sheet's
//     +EM / BMO / -EM separator lines.
//   * Header Call/Put totals are the sum of the PRINTED walls.

// Third Friday of the month holding `fromDate`, rolling to the next month once
// this month's OPEX has passed.
export function nextMonthlyOpexDate(fromDate = new Date()) {
  const thirdFriday = (year, month) => {
    const first = new Date(Date.UTC(year, month, 1));
    const offset = (5 - first.getUTCDay() + 7) % 7;
    return new Date(Date.UTC(year, month, 1 + offset + 14));
  };
  const today = new Date(Date.UTC(fromDate.getUTCFullYear(), fromDate.getUTCMonth(), fromDate.getUTCDate()));
  const thisMonth = thirdFriday(today.getUTCFullYear(), today.getUTCMonth());
  return thisMonth >= today ? thisMonth : thirdFriday(today.getUTCFullYear(), today.getUTCMonth() + 1);
}

// The sheet's expiry window: through the next monthly OPEX — but an OPEX only
// days away is spent positioning, and the 8/20 sheets (OPEX 8/21) list 9/18
// walls beside the weeklies. Under a week out, the window rolls to the
// following monthly.
const NEAR_OPEX_ROLL_DAYS = 7;

export function highOiWindowEndDate(fromDate = new Date()) {
  const opex = nextMonthlyOpexDate(fromDate);
  const today = Date.UTC(fromDate.getUTCFullYear(), fromDate.getUTCMonth(), fromDate.getUTCDate());
  const daysOut = Math.round((opex.getTime() - today) / 86_400_000);
  if (daysOut >= NEAR_OPEX_ROLL_DAYS) return opex;
  return nextMonthlyOpexDate(new Date(opex.getTime() + 86_400_000));
}

// Importance 1-5 vs the side's leading OI (drives line weight / heat, MomoX's
// "Imp" column). No longer approximate: these breaks were solved from the
// 2026-08-20 sheets and reproduce their Imp column exactly — all 16 MSFT rows
// and all 16 SPY rows (see highOiContractList.test.js). The side-relative
// denominator is why a 6.9k MSFT put rates 5 while a 16.6k MSFT call rates 3:
// the put side simply carries less size.
//
// Caveat: this is the CURRENT sheet layout (the grid in the 08-20 images). The
// older single-panel layout with a "52High" row scored Imp differently and is
// not what these breaks reproduce.
export const HIGH_OI_IMPORTANCE_BREAKS = [0.125, 0.225, 0.35, 0.5];

export function highOiImportance(openInterest, leadingOpenInterest) {
  const ratio = Math.max(Number(openInterest) || 0, 0) / Math.max(Number(leadingOpenInterest) || 0, 1);
  return HIGH_OI_IMPORTANCE_BREAKS.reduce((tier, threshold) => (ratio >= threshold ? tier + 1 : tier), 1);
}

// A 1-day expected move for the ±EM separators, from the live ATM straddle
// when the chain has one, else estimated from ATM IV (spot·IV·√(1/252)) so the
// lines still draw overnight — MomoX shows an ExMo overnight the same way.
// An expiry whose expected move is below this fraction of the NEXT expiry's has
// been spent. A live 1-day move sits near 0.7 of the next expiry (sqrt(2) time
// scaling); a dead one reads about 0.1.
const SPENT_EXPIRY_RATIO = 0.35;

// Pick the expected move from the first expiry that still has time value.
//
// Judged by SHAPE, not by date. A 0-DTE straddle at the 9:15 build still has a
// full session in it - measured premarket: SPY 2.87 against 4.19 for the next
// expiry, ratio 0.68 - and that IS the move the trader wants for today. After
// the close the same contract is worthless: TSLA read 0.915 against 7.525 on
// 2026-08-17, ratio 0.12. Skipping "today" outright would discard SPY's
// legitimate 2.87 every morning.
export function expectedMoveFromExpiries(expiryExpectedMoves) {
  const moves = expiryExpectedMoves && typeof expiryExpectedMoves === "object" ? expiryExpectedMoves : {};
  const dated = Object.keys(moves).filter((key) => Number(moves[key]) > 0).sort();
  for (let index = 0; index < dated.length; index += 1) {
    const move = Number(moves[dated[index]]) || 0;
    const following = index + 1 < dated.length ? Number(moves[dated[index + 1]]) || 0 : 0;
    if (following > 0 && move < following * SPENT_EXPIRY_RATIO) continue;
    return move;
  }
  return Number(moves[dated[0]] || 0) || 0;
}

export function resolveExpectedMove({ expectedMove = 0, impliedVolatilityPercent = 0, underlyingPrice = 0 } = {}) {
  const live = Math.max(0, Number(expectedMove) || 0);
  if (live > 0) return live;
  const iv = Math.max(0, Number(impliedVolatilityPercent) || 0);
  const spot = Math.max(0, Number(underlyingPrice) || 0);
  if (iv > 0 && spot > 0) return (spot * (iv / 100)) / Math.sqrt(252);
  return 0;
}

// --- Display formatters for the board's Delta / Vol / Mark columns (MomoX
// daily-sheet formats). A zero renders "-" in all three: overnight feeds
// report 0 for delta, volume and mark until the market wakes, and a printed
// 0.00 would read as a real quote.

// Two decimals, signed by the raw value (calls 0.43, puts -0.34). A rounded
// "-0.00" (delta between -0.005 and 0) normalises to "0.00" so a minus sign
// never rides a zero.
export function formatDelta(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return "-";
  const text = numeric.toFixed(2);
  return text === "-0.00" ? "0.00" : text;
}

// MomoX compact volume: 863 stays 863, 18_312 prints "18k" (rounded above 999).
export function formatCompactVolume(value) {
  const numeric = Math.round(Number(value) || 0);
  if (numeric <= 0) return "-";
  return numeric > 999 ? `${Math.round(numeric / 1000)}k` : String(numeric);
}

// Option price, two decimals.
export function formatMark(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return "-";
  return numeric.toFixed(2);
}

export function buildHighOiContractList({
  rows,
  underlyingPrice,
  topPerSide = 8,
  scope = "monthly",
  frontExpiry = "",
  // The 1-day expected move: places the ±EM separator rows AND bounds the
  // candidate strikes to ±(emBandMultiple × EM). Without it, top-8-by-OI
  // drifts to far monthly mega-walls (MSFT 550/570 calls, 350/330 puts) and
  // starves the near zone the sheet actually prints — with the sheet's own
  // ExMo 8.21 the ±6·EM band reproduces its MSFT panel exactly (530 in,
  // 535 out; 440 in, 430 out).
  expectedMove = 0,
  emBandMultiple = 2,
  now = new Date(),
} = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const spot = Number(underlyingPrice) || 0;
  const limit = Math.max(1, Math.min(50, Math.round(Number(topPerSide) || 8)));
  const windowKey = highOiWindowEndDate(now).toISOString().slice(0, 10);
  const front = String(frontExpiry || "").slice(0, 10);
  const em = Math.max(0, Number(expectedMove) || 0);
  const emBand = em > 0 && spot > 0 ? em * Math.max(1, Number(emBandMultiple) || 2) : 0;

  const inScope = (row) => {
    const expiry = String(row?.expiry || "").slice(0, 10);
    if (!expiry) return false;
    if (scope === "front") return !front || expiry === front;
    return expiry <= windowKey;
  };
  // Tickers whose listed cycles all sit past the window (e.g. quarterly-only
  // chains) would otherwise produce an empty board — fall back to every
  // expiry rather than showing nothing.
  let scopedRows = list.filter(inScope);
  if (!scopedRows.length) scopedRows = list;

  const withOpenInterest = scopedRows
    .map((row) => ({
      side: String(row?.side || "").toUpperCase() === "PUT" ? "PUT" : "CALL",
      strike: Number(row?.strike) || 0,
      delta: Number(row?.delta) || 0,
      volume: Math.max(Number(row?.volume) || 0, 0),
      openInterest: Math.max(Number(row?.open_interest ?? row?.openInterest) || 0, 0),
      last: Number(row?.last) || Number(row?.mark) || 0,
      expiry: String(row?.expiry || "").slice(0, 10),
      dte: Number(row?.days_to_expiration ?? row?.daysToExpiration) || 0,
    }))
    .filter((row) => row.strike > 0 && row.openInterest > 0);

  // The sheet's side rule: above spot only the call contract counts, at/below
  // spot only the put contract (a strike being stood on is support). Without
  // a spot yet (chain still loading) the split is impossible — group by
  // contract type so the panel isn't blank for a poll.
  const sideSplit = spot > 0
    ? withOpenInterest.filter((row) => (row.side === "CALL" ? row.strike > spot : row.strike <= spot))
    : withOpenInterest;
  // The ±N·EM band is applied PER SIDE inside sideList (a side with fewer
  // than `limit` in-band strikes falls back to its full set), so keep every
  // side-split row here.
  const sideCorrect = sideSplit;

  // One row per strike: the dominant expiry (largest OI, then volume) wins.
  // When the dominant wall is a later cycle but this week also holds real size
  // at the same strike (MomoX AAPL example: 315 shows "15k 8/7" stacked over
  // "18k 8/21"), the front figure rides along as `frontAlt` for the chart.
  const nearestListedExpiry = sideCorrect.map((row) => row.expiry).filter(Boolean).sort()[0] || "";
  const sideList = (side) => {
    const byStrike = new Map();
    const frontByStrike = new Map();
    sideCorrect.forEach((row) => {
      if (row.side !== side || row.expiry !== nearestListedExpiry) return;
      const currentFront = frontByStrike.get(row.strike);
      if (!currentFront || row.openInterest > currentFront.openInterest) {
        frontByStrike.set(row.strike, row);
      }
    });
    sideCorrect.filter((row) => row.side === side).forEach((row) => {
      const current = byStrike.get(row.strike);
      if (!current
        || row.openInterest > current.openInterest
        || (row.openInterest === current.openInterest && row.volume > current.volume)) {
        byStrike.set(row.strike, row);
      }
    });
    const withFront = [...byStrike.values()].map((row) => {
      const frontRow = frontByStrike.get(row.strike);
      const frontIsMeaningful = frontRow
        && frontRow.expiry !== row.expiry
        && frontRow.openInterest >= row.openInterest * 0.2;
      return frontIsMeaningful
        ? { ...row, frontAlt: { openInterest: frontRow.openInterest, volume: frontRow.volume, expiry: frontRow.expiry } }
        : row;
    });
    // MomoX / Trading Alphas "Plot Highest OI Levels": the N strikes NEAREST
    // top-N strikes by OI WITHIN a +/-2*EM near-money band (MomoX / Trading
    // Alphas rule, verified against the trader's 2026-08-25 sheet). The band
    // excludes far mega-walls (NVDA 180/170/140 puts) and tiny 2.5-wide
    // fillers between real walls (NVDA 212.5/217.5) never make the top-N. When
    // a side has fewer than N in-band strikes, fall back to the full side so
    // the board still fills to N and reaches the real walls (GOOGL 375, MSFT
    // 525). Kept in step with build_high_oi_walls in oi_auto_alerts.py.
    let candidates = withFront;
    if (spot > 0 && emBand > 0) {
      const inBand = withFront.filter((row) => Math.abs(row.strike - spot) <= emBand);
      if (inBand.length >= limit) {
        candidates = inBand;
      } else {
        // Sparse near band: keep every in-band strike and FILL to `limit`
        // with the NEAREST out-of-band ones (never the biggest far walls), so
        // GOOGL reaches 375 and MSFT 525 while NVDA's far walls stay off.
        const outOfBand = withFront
          .filter((row) => Math.abs(row.strike - spot) > emBand)
          .sort((left, right) => Math.abs(left.strike - spot) - Math.abs(right.strike - spot) || left.strike - right.strike);
        candidates = inBand.concat(outOfBand.slice(0, Math.max(0, limit - inBand.length)));
      }
    }
    const chosen = candidates
      .slice()
      .sort((left, right) => right.openInterest - left.openInterest || right.volume - left.volume)
      .slice(0, limit);
    return chosen.slice().sort((left, right) => right.strike - left.strike);
  };

  // Importance (MomoX "Imp") is scored against the side's leading wall among
  // the chosen levels, then attached to each row.
  const withImportance = (items) => {
    const leadingOi = Math.max(...items.map((item) => item.openInterest), 0);
    return items.map((item) => ({ ...item, importance: highOiImportance(item.openInterest, leadingOi) }));
  };
  const calls = withImportance(sideList("CALL"));
  const puts = withImportance(sideList("PUT"));
  const sum = (items, key) => items.reduce((total, item) => total + item[key], 0);
  // Sheet convention: the header Call/Put figures are the sum of the printed
  // walls, not the whole chain.
  const callOi = sum(calls, "openInterest");
  const putOi = sum(puts, "openInterest");
  return {
    calls,
    puts,
    callOi,
    putOi,
    putCallRatio: callOi > 0 ? putOi / callOi : 0,
    peakOi: Math.max(...calls.map((item) => item.openInterest), ...puts.map((item) => item.openInterest), 0),
    peakVolume: Math.max(...calls.map((item) => item.volume), ...puts.map((item) => item.volume), 0),
    monthlyExpiry: windowKey,
    spot,
    expectedMove: em,
    // ±EM separator lines for the daily-sheet layout (0 when no EM known).
    emUp: em > 0 && spot > 0 ? Math.round((spot + em) * 100) / 100 : 0,
    emDown: em > 0 && spot > 0 ? Math.round((spot - em) * 100) / 100 : 0,
  };
}
