// Highest open-interest contract ranking for the chain's "High OI" view.
// Mirrors the MomoX list: strikes above the session anchor can only be call
// walls and strikes below it can only be put walls, then the top N by OI on
// each side are shown far-to-near around spot with a 1-5 importance tier.

// Third Friday of the month holding `fromDate`. Once this month's OPEX is
// inside a week the window rolls to the following month: MomoX still lists the
// September cycle on the Friday before August OPEX, and stopping at an expiry
// one day out would hide every wall a swing trader is watching.
const OPEX_ROLL_FORWARD_DAYS = 7;

export function nextMonthlyOpexDate(fromDate = new Date()) {
  const thirdFriday = (year, month) => {
    const first = new Date(Date.UTC(year, month, 1));
    const offset = (5 - first.getUTCDay() + 7) % 7;
    return new Date(Date.UTC(year, month, 1 + offset + 14));
  };
  const today = new Date(Date.UTC(fromDate.getUTCFullYear(), fromDate.getUTCMonth(), fromDate.getUTCDate()));
  const rollAfter = new Date(today.getTime() + OPEX_ROLL_FORWARD_DAYS * 86_400_000);
  const thisMonth = thirdFriday(today.getUTCFullYear(), today.getUTCMonth());
  return thisMonth >= rollAfter ? thisMonth : thirdFriday(today.getUTCFullYear(), today.getUTCMonth() + 1);
}

// MomoX's "Imp" column: how big a wall is relative to the leading wall on its
// own side. The breaks below reproduce the published MSFT and SPY panels
// exactly (see highOiContractList.test.js), and the side-relative denominator
// is why a 6.9k put can rate 5 while a 16.6k call rates 3 on the same ticker —
// the put side simply carries less size.
export const HIGH_OI_IMPORTANCE_BREAKS = [0.125, 0.225, 0.35, 0.5];

export function highOiImportance(openInterest, sideLeaderOpenInterest) {
  const leader = Number(sideLeaderOpenInterest) || 0;
  const size = Number(openInterest) || 0;
  if (leader <= 0 || size <= 0) return 1;
  const ratio = size / leader;
  return HIGH_OI_IMPORTANCE_BREAKS.reduce((tier, threshold) => (ratio >= threshold ? tier + 1 : tier), 1);
}

export function buildHighOiContractList({
  rows,
  underlyingPrice,
  // The strike/side split. MomoX pins it to BMO (the pre-open price it prints
  // between the call and put blocks) so an intraday swing cannot flip a wall
  // from one side to the other and churn the ladder mid-session. Falls back to
  // the live price when the caller has no BMO.
  anchorPrice = 0,
  // No delta band by default: MomoX ranks pure OI, and its far walls (MSFT 530
  // against a 484 spot) sit well under any workable floor. The panel still
  // exposes the control for traders who want to tighten the list by hand.
  minDelta = 0,
  maxDelta = 1,
  topPerSide = 8,
  scope = "monthly",
  frontExpiry = "",
  now = new Date(),
} = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const spot = Number(underlyingPrice) || 0;
  const anchor = Number(anchorPrice) > 0 ? Number(anchorPrice) : spot;
  const floor = Math.max(0, Number(minDelta) || 0);
  const cap = Math.min(1, Math.max(floor, Number(maxDelta) || 1));
  const limit = Math.max(1, Math.min(50, Math.round(Number(topPerSide) || 8)));
  const monthlyKey = nextMonthlyOpexDate(now).toISOString().slice(0, 10);
  const front = String(frontExpiry || "").slice(0, 10);

  const inScope = (row) => {
    const expiry = String(row?.expiry || "").slice(0, 10);
    if (!expiry) return false;
    if (scope === "front") return !front || expiry === front;
    return expiry <= monthlyKey;
  };

  // Tickers whose listed cycles all sit past the next monthly OPEX (e.g.
  // quarterly-only chains) would otherwise produce an empty list — fall back
  // to every expiry in the payload rather than showing nothing.
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

  // The MomoX rule, applied BEFORE any ranking: a strike above the anchor is
  // resistance and can only appear as a call, a strike below it is support and
  // can only appear as a put. Ranking first and filtering afterwards is what
  // let ITM calls under spot into the list and left the ladder short.
  const facesTheRightWay = (row) => {
    if (anchor <= 0) return true;
    return row.side === "CALL" ? row.strike > anchor : row.strike < anchor;
  };
  const sided = withOpenInterest.filter(facesTheRightWay);

  // MomoX's printed rule: delta band, but "extreme deltas appear only for
  // High OI / High Vol". A dominant wall must never vanish because its own
  // delta collapsed (TSLA 332.5 8/7 held 13.6k OI at 0.009 delta after the
  // close) — size keeps it on the board.
  const sideMaxOi = { CALL: 0, PUT: 0 };
  sided.forEach((row) => {
    sideMaxOi[row.side] = Math.max(sideMaxOi[row.side], row.openInterest);
  });
  // Illiquid names sometimes come back without greeks; when the delta band
  // would filter out every contract, rank the raw OI instead of going blank.
  let normalized = sided.filter((row) => {
    const absDelta = Math.abs(row.delta);
    const inBand = absDelta >= floor && absDelta <= cap;
    const highOiException = row.openInterest >= sideMaxOi[row.side] * 0.3;
    return inBand || highOiException;
  });
  if (!normalized.length) normalized = sided;

  // One row per strike: the dominant expiry (largest OI, then volume) wins,
  // so 85 Aug 7 and 85 Aug 21 never both appear. When the dominant wall is a
  // later cycle but this week also holds real size at the same strike (MomoX
  // AAPL example: 315 shows "15k 8/7" stacked over "18k 8/21"), the front
  // figure rides along as `frontAlt` so the chart can print both.
  // Front-week size is tracked pre-delta-band: a 0DTE wall's delta collapses
  // toward zero late in the day (AAPL 315 8/7 held 19.9k OI at 0.10 delta),
  // but its size at an already-qualified strike must still stack.
  const nearestListedExpiry = withOpenInterest.map((row) => row.expiry).filter(Boolean).sort()[0] || "";
  const sideList = (side) => {
    const byStrike = new Map();
    const frontByStrike = new Map();
    sided.forEach((row) => {
      if (row.side !== side || row.expiry !== nearestListedExpiry) return;
      const currentFront = frontByStrike.get(row.strike);
      if (!currentFront || row.openInterest > currentFront.openInterest) {
        frontByStrike.set(row.strike, row);
      }
    });
    normalized.filter((row) => row.side === side).forEach((row) => {
      const current = byStrike.get(row.strike);
      if (!current
        || row.openInterest > current.openInterest
        || (row.openInterest === current.openInterest && row.volume > current.volume)) {
        byStrike.set(row.strike, row);
      }
    });
    const ranked = [...byStrike.values()]
      .map((row) => {
        const front = frontByStrike.get(row.strike);
        const frontIsMeaningful = front
          && front.expiry !== row.expiry
          && front.openInterest >= row.openInterest * 0.2;
        return frontIsMeaningful
          ? { ...row, frontAlt: { openInterest: front.openInterest, volume: front.volume, expiry: front.expiry } }
          : row;
      })
      .sort((left, right) => right.openInterest - left.openInterest || right.volume - left.volume)
      .slice(0, limit);
    // Importance is scored against the leader of what actually made the list,
    // which is how MomoX's own column behaves.
    const leader = ranked.reduce((peak, row) => Math.max(peak, row.openInterest), 0);
    return ranked
      .map((row) => ({ ...row, imp: highOiImportance(row.openInterest, leader) }))
      .sort((left, right) => right.strike - left.strike);
  };

  const calls = sideList("CALL");
  const puts = sideList("PUT");
  const sum = (items, key) => items.reduce((total, item) => total + item[key], 0);
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
    monthlyExpiry: monthlyKey,
    anchor,
    spot,
  };
}
