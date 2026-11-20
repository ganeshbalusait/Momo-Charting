// "What is this page?" boxes written so a 10-year-old, or someone who has never traded, can follow them
// (Ganesh 2026-11-20: "Build like this 10 kid can understand or who don't know any stock market"). Short sentences,
// every word explained the first time, and always: facts only, not a pick, paper trading only.

export const GUIDES = {
  agxFlow: {
    title: "What is this page?",
    steps: [
      ["What it shows", "Big bets people placed on stocks today, using options. The biggest dollar amounts are at the top."],
      ["Option", "A ticket that lets you buy (a Call) or sell (a Put) a stock at a set price, called the strike, before a set date, called the expiry."],
      ["Call or Put", "People usually buy a Call if they think the stock will go up, and a Put if they think it will go down. But a buyer can also be protecting other trades, so it is never a sure sign."],
      ["Bought or Sold", "The app's guess. A trade at the asking price was probably a buyer paying up. A trade at the bid price was probably a seller."],
      ["Unusual", "More contracts traded today than were already open. It means a lot of new interest."],
      ["The buttons", "This week, Next week and 30 days show bets ending soon. 1–3, 3–6 and 6–12 months show longer bets (6–12 months are called LEAPs)."],
    ],
    warning: "A big bet does not mean the stock will move that way. This is information, not advice. Practice with paper trading first.",
  },
  leapOi: {
    title: "What is this page?",
    steps: [
      ["What it shows", "Long option bets, 3 to 12 months away, and which ones the most people are holding."],
      ["Open interest (OI)", "How many of these option tickets people are holding right now. It updates once a day, before the market opens."],
      ["Green or red number next to OI", "How many tickets were added (green) or closed (red) since yesterday. A big green number means many new bets."],
      ["Stock price", "What one share of the stock costs right now, and how much it moved today."],
      ["From price", "How far the stock has to move to reach the strike. +20% means the stock has to go up 20%."],
      ["Delta", "Roughly the chance the option ends up worth something. 0.20 is about 1 in 5."],
      ["Mid and 1 contract", "Mid is the fair price between buyer and seller. One contract covers 100 shares, so it costs the mid times 100."],
      ["ENTRY", "The contract our option chain marks as the usual starting choice for that date (delta just under 0.20)."],
    ],
    warning: "The most popular bet is not always the right one. Our test found big open interest guessed direction no better than a coin flip. Information only, not advice. Practice with paper trading first.",
  },
};

/** The guide for a page key, or null. */
export function guideFor(key) {
  return GUIDES[key] || null;
}
