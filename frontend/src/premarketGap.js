// How to label the 04:00-07:00 premarket window when the primary feed is out.
//
// Schwab has no bars before 07:00 ET - verified again live on 2026-08-28, where
// AAPL's earliest bar of the day was exactly 07:00 and the 04:00-07:00 count
// was zero. Tradier fills that window, and when Tradier is refused the server
// falls back to Alpaca SIP, which lands the candles about 15 minutes behind.
//
// Those are two different situations and only one of them is an outage. The
// badge used to call both "PREMARKET FEED DOWN", including on mornings where
// the window was fully populated from the backup. Calling a working backup
// "down" teaches him to ignore the badge; calling a real hole "backup" would
// let him trade off candles that do not exist. So the label follows the state.

const WINDOW = "04:00\u201307:00";

export function premarketGapBadge(state, note) {
  const key = String(state || "").trim().toLowerCase();
  const title = String(note || "");

  if (key === "backup") {
    return { text: `PREMARKET ON BACKUP \u00b7 ${WINDOW}`, tone: "warn", title };
  }
  if (key === "missing") {
    return { text: `PREMARKET FEED DOWN \u00b7 ${WINDOW}`, tone: "down", title };
  }
  // Unknown state. A note still means the server saw something wrong, and
  // deploys are not atomic - an older backend sends the note without the
  // state. Stay loud rather than invent reassurance we have not verified.
  if (title) {
    return { text: `PREMARKET FEED DOWN \u00b7 ${WINDOW}`, tone: "down", title };
  }
  return null;
}
