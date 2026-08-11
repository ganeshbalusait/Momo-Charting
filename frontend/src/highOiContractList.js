// Highest open-interest contract ranking for the chain's "High OI" view.
// Mirrors the MomoX list: an absolute-delta floor removes lottery strikes,
// then the top N contracts by OI per side are shown far-to-near around spot.

// Third Friday of the month holding `fromDate`, rolling to the next month once
// this month's OPEX has passed. Bounds the "to monthly" window.
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

export function buildHighOiContractList({
  rows,
  underlyingPrice,
  minDelta = 0.14,
  // OTM cap: ITM contracts (|delta| above ~0.5) are positioning history, not
  // walls — MomoX's list never shows them.
  maxDelta = 0.5,
  topPerSide = 8,
  scope = "monthly",
  frontExpiry = "",
  now = new Date(),
} = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const spot = Number(underlyingPrice) || 0;
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
  // MomoX's printed rule: delta band, but "extreme deltas appear only for
  // High OI / High Vol". A dominant wall must never vanish because its own
  // delta collapsed (TSLA 332.5 8/7 held 13.6k OI at 0.009 delta after the
  // close) or went deep ITM — size keeps it on the board.
  const sideMaxOi = { CALL: 0, PUT: 0 };
  withOpenInterest.forEach((row) => {
    sideMaxOi[row.side] = Math.max(sideMaxOi[row.side], row.openInterest);
  });
  // Illiquid names sometimes come back without greeks; when the delta band
  // would filter out every contract, rank the raw OI instead of going blank.
  let normalized = withOpenInterest.filter((row) => {
    const absDelta = Math.abs(row.delta);
    const inBand = absDelta >= floor && absDelta <= cap;
    const highOiException = row.openInterest >= sideMaxOi[row.side] * 0.3;
    return inBand || highOiException;
  });
  if (!normalized.length) normalized = withOpenInterest;

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
    withOpenInterest.forEach((row) => {
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
    return [...byStrike.values()]
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
      .slice(0, limit)
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
    spot,
  };
}
