// ROI Calc auto-fill: everything the trader used to type by hand on the
// ROI Calc page, read from the selected ticker's live option chain instead.
//
//  - the expected move of the chosen expiry,
//  - the first in-the-money premium (the reference for the "premium ÷ 2 / ÷ 3"
//    shortcut),
//  - a strike ladder of the ATM and out-of-the-money strikes inside ±EM, each
//    with its live price (same picker as the Project ROI card),
//  - a default pick (first OTM strike), its entry price (ask, else mark),
//  - three stock targets (half the move, the full move, the biggest OI level
//    in the trade's direction) and a -50% stop.
//
// Every value is a suggestion: the page lets the trader overwrite any field
// and puts it back to live with one click.

function sideOf(side) {
  return String(side || "CALL").toUpperCase() === "PUT" ? "PUT" : "CALL";
}

function round2(value) {
  return Math.round(Number(value) * 100) / 100;
}

export function roiContractPrice(row) {
  if (!row) return 0;
  const mark = Number(row.mark || 0);
  if (mark > 0) return mark;
  const bid = Number(row.bid || 0);
  const ask = Number(row.ask || 0);
  if (bid > 0 && ask > 0) return (bid + ask) / 2;
  const last = Number(row.last || 0);
  return last > 0 ? last : 0;
}

// What a buyer realistically pays: the ask when there is one, else the mark.
export function roiEntryPrice(row) {
  const ask = Number(row?.ask || 0);
  return ask > 0 ? ask : roiContractPrice(row);
}

export function roiExpiryExpectedMove({ rows = [], expiry = "", expiryExpectedMoves = null } = {}) {
  const key = String(expiry || "").slice(0, 10);
  const fromMap = Number(expiryExpectedMoves?.[key] || 0);
  if (fromMap > 0) return fromMap;
  const row = (Array.isArray(rows) ? rows : []).find((item) => (
    String(item?.expiry || "").slice(0, 10) === key && Number(item?.expected_move || 0) > 0
  ));
  return row ? Number(row.expected_move) : 0;
}

export function buildRoiAutofill({ rows = [], spot = 0, side = "CALL", expiry = "", expiryExpectedMoves = null } = {}) {
  const sideName = sideOf(side);
  const price = Math.max(0, Number(spot) || 0);
  const key = String(expiry || "").slice(0, 10);
  const contracts = (Array.isArray(rows) ? rows : [])
    .filter((row) => (
      String(row?.expiry || "").slice(0, 10) === key
      && sideOf(row?.side) === sideName
      && Number(row?.strike) > 0
    ))
    .sort((left, right) => Number(left.strike) - Number(right.strike));
  const expectedMove = roiExpiryExpectedMove({ rows, expiry: key, expiryExpectedMoves });
  const empty = {
    side: sideName, expiry: key, spot: price, expectedMove, atmStrike: 0,
    itmContract: null, itmPremium: 0, ladder: [], defaultStrike: 0,
  };
  if (!price || !contracts.length) return empty;

  const atm = contracts.reduce((best, row) => (
    Math.abs(Number(row.strike) - price) < Math.abs(Number(best.strike) - price) ? row : best
  ), contracts[0]);
  const atmStrike = Number(atm.strike);
  const isCall = sideName === "CALL";

  // First in-the-money strike with a live price (calls below spot, puts
  // above), skipping the ATM strike itself: a strike 20 cents in the money is
  // the at-the-money contract, not the ITM reference.
  const itmCandidates = contracts.filter((row) => (
    (isCall ? Number(row.strike) < price : Number(row.strike) > price)
    && Number(row.strike) !== atmStrike
    && roiContractPrice(row) > 0
  ));
  const itmContract = isCall ? itmCandidates.at(-1) || null : itmCandidates[0] || null;

  // ATM + OTM strikes inside ±EM (at least the ATM and three OTM strikes when
  // the move is missing or tighter than the strike spacing).
  const otm = contracts.filter((row) => (isCall ? Number(row.strike) > atmStrike : Number(row.strike) < atmStrike));
  const otmOrdered = isCall ? otm : otm.slice().reverse();
  const edge = expectedMove > 0 ? (isCall ? price + expectedMove : price - expectedMove) : null;
  const insideMove = otmOrdered.filter((row) => (
    edge == null ? false : isCall ? Number(row.strike) <= edge : Number(row.strike) >= edge
  ));
  // Capped at 8 OTM strikes so the ladder stays one page section (no inner
  // scroller) even on a wide move.
  const otmShown = (insideMove.length >= 3 ? insideMove : otmOrdered.slice(0, 3)).slice(0, 8);
  const ladder = [atm, ...otmShown].map((row) => ({
    strike: Number(row.strike),
    price: roiContractPrice(row),
    entry: roiEntryPrice(row),
    delta: Number(row.delta || 0),
    gamma: Number(row.gamma || 0),
    openInterest: Number(row.open_interest || 0),
    distance: round2(Number(row.strike) - price),
    isAtm: row === atm,
  }));

  // Default pick: the first OTM strike that trades (the ATM when none does).
  const firstOtm = ladder.find((item) => !item.isAtm && item.entry > 0);
  const defaultStrike = firstOtm ? firstOtm.strike : atmStrike;

  return {
    ...empty,
    atmStrike,
    itmContract,
    itmPremium: itmContract ? round2(roiContractPrice(itmContract)) : 0,
    ladder,
    defaultStrike,
  };
}

// Three stock targets in the trade's direction: half the expected move, the
// full move, and the biggest open-interest strike beyond spot within two
// moves (the OI level the move is likely to stall at). Falls back to 1.5x the
// move when the chain has no OI.
export function roiAutoTargets({ rows = [], spot = 0, side = "CALL", expiry = "", expectedMove = 0 } = {}) {
  const price = Math.max(0, Number(spot) || 0);
  const move = Math.max(0, Number(expectedMove) || 0);
  if (!price || !move) return ["", "", ""];
  const isCall = sideOf(side) === "CALL";
  const sign = isCall ? 1 : -1;
  const key = String(expiry || "").slice(0, 10);
  const half = round2(price + sign * move / 2);
  const full = round2(price + sign * move);
  const wall = (Array.isArray(rows) ? rows : [])
    .filter((row) => {
      const strike = Number(row?.strike || 0);
      return String(row?.expiry || "").slice(0, 10) === key
        && sideOf(row?.side) === sideOf(side)
        && Number(row?.open_interest || 0) > 0
        && (isCall ? strike > full && strike <= price + 2 * move : strike < full && strike >= price - 2 * move);
    })
    .sort((left, right) => Number(right.open_interest) - Number(left.open_interest))[0];
  const third = wall ? round2(Number(wall.strike)) : round2(price + sign * move * 1.5);
  return [half, full, third].map((value) => value.toFixed(2));
}

// The stock price at which the contract is projected to be down `lossFraction`
// (default 50%), using the same Delta + Gamma path as the Gamma ROI column.
export function roiStopLevel({ side = "CALL", spot = 0, entry = 0, delta = 0, gamma = 0, lossFraction = 0.5 } = {}) {
  const price = Number(spot) || 0;
  const cost = Number(entry) || 0;
  const d = Math.abs(Number(delta) || 0);
  if (price <= 0 || cost <= 0 || d <= 0) return null;
  const g = Math.abs(Number(gamma) || 0);
  const isCall = sideOf(side) === "CALL";
  const stopPremium = round2(cost * (1 - lossFraction));
  // Loss when the stock moves `x` dollars against the trade: the average of
  // the entry delta and the gamma-reduced delta at the end of the move.
  const loss = (x) => {
    const endDelta = Math.max(d - g * x, 0);
    return ((d + endDelta) / 2) * x;
  };
  const wanted = cost * lossFraction;
  let low = 0;
  let high = price;
  if (loss(high) < wanted) return { stopPremium, stopStock: null };
  for (let step = 0; step < 60; step += 1) {
    const mid = (low + high) / 2;
    if (loss(mid) < wanted) low = mid; else high = mid;
  }
  const adverse = (low + high) / 2;
  return { stopPremium, stopStock: round2(isCall ? price - adverse : price + adverse) };
}
