// "In simple words" on the Long Term page (2026-11-20, Ganesh: "I need
// justification why Warren or Peter chose that stocks. Build like this 10 kid
// can understand or who don't know any stock market").
//
// For one row: what Warren Buffett's checklist (the page's Rule #1 screen) and
// Peter Lynch's checklist (the Peter view) each say about the company, in
// words a ten-year-old can read. No trading words: no ROIC, EPS, PEG or P/E.
// Every sentence is a number already on the row, said plainly; nothing here
// predicts a price. Pure functions, no React.

import { lynchMissing, lynchNoSheet, lynchSheet } from "./longTermLynch.js";

export const SIMPLE_FOOT = "These are two famous investors' checklists, worked out from the company's own reports. They explain; they do not tell you what to do with your money.";

const FOOT_TAIL = "worked out from the company's own reports. It explains; it does not tell you what to do with your money.";
export const SIMPLE_FOOT_ONE = {
  warren: `This is Warren Buffett's checklist, ${FOOT_TAIL}`,
  peter: `This is Peter Lynch's checklist, ${FOOT_TAIL}`,
};

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const whole = (v) => Math.round(Math.abs(v) * 100);
const dollars = (v) => (v >= 10 ? `$${Math.round(v).toLocaleString("en-US")}` : `$${v.toFixed(2)}`);
const line = (mark, text) => ({ mark, text });
const owes = (ratio) => `It owes $${Math.round(ratio * 100)} for every $100 it owns.`;

function grows(value, what, wants) {
  if (value >= 0) return `${what} ${whole(value)}% ${what.startsWith("It sells") ? "more every year" : "a year"}.${wants ? ` ${wants}` : ""}`;
  return what.startsWith("It sells") ? `It sells ${whole(value)}% less every year.` : `${what.replace("grows", "shrinks")} ${whole(value)}% a year.`;
}

function warrenSide(row) {
  const s = row.signal;
  if (s === "ETF") {
    return { headline: "This is a fund (a basket of many companies), not one business, so Warren's checklist does not fit it.", lines: [], todo: null, sell: null };
  }
  const lines = [];
  const earns = row.financial ? row.roe : row.roic;
  if (isNum(earns)) {
    const where = row.financial ? "For every $100 its owners have in it" : "For every $100 put into the business";
    lines.push(line(earns >= 0.15 ? "yes" : "no", `${where}, it earns $${Math.round(earns * 100)} a year. Warren wants $15 or more.`));
  }
  if (isNum(row.salesGrowth)) lines.push(line(row.salesGrowth >= 0.1 ? "yes" : "no", grows(row.salesGrowth, "It sells", "Warren wants 10% or more.")));
  if (isNum(row.epsGrowth)) lines.push(line(row.epsGrowth >= 0.1 ? "yes" : "no", grows(row.epsGrowth, "Its profit grows")));
  if (!row.financial && isNum(row.fcfGrowth)) lines.push(line(row.fcfGrowth >= 0.1 ? "yes" : "no", grows(row.fcfGrowth, "The cash it has left over grows")));
  if (!row.financial && isNum(row.debtToEquity) && row.debtToEquity >= 0) {
    const size = row.debtToEquity <= 0.3 ? "That is very little." : row.debtToEquity <= 1 ? "That is a fair amount." : "That is a lot.";
    lines.push(line("info", `${owes(row.debtToEquity)} ${size}`));
  }
  if (isNum(row.sticker) && isNum(row.safetyBuy) && isNum(row.price) && row.price > 0) {
    const sale = row.price <= row.safetyBuy;
    lines.push(line(sale ? "yes" : "no",
      `A fair price is about ${dollars(row.sticker)} a share. Warren only buys at a big discount: ${dollars(row.safetyBuy)} or less. Today it costs ${dollars(row.price)}, so it is ${sale ? "on sale" : "not on sale yet"}.`));
  }
  // "Did he use a profit target?": this rule's only sell point is the fair price.
  const sell = isNum(row.sticker)
    ? `When would he sell? This rule sells when the price reaches the fair price, about ${dollars(row.sticker)}. It has no other profit target, and Warren himself likes to keep a great business for many years.`
    : null;
  if (s === "BUY") {
    return { headline: "Would Warren buy it? Yes. It is a great business and today it is on sale.", lines, todo: "What his rule says: this is a price where he would buy.", sell };
  }
  if (s === "WAIT") {
    const at = isNum(row.buyBelow) ? ` Look again if the price drops to ${dollars(row.buyBelow)} or less.` : "";
    return { headline: "Would Warren buy it? Not yet. It is a good business, but the price is too high today.", lines, todo: `What his rule says: do nothing now.${at}`, sell };
  }
  if (s === "AVOID") {
    if (row.reason) lines.push(line("no", `The rule it failed: ${String(row.reason).replace(/\.$/, "")}.`));
    return { headline: "Would Warren buy it? No. It does not pass his checklist.", lines, todo: "What his rule says: leave it alone.", sell: null };
  }
  if (s === "NO_PRICE") return { headline: "Would Warren buy it? We cannot say today: there is no fresh price for it.", lines, todo: null, sell: null };
  return { headline: "Would Warren buy it? We cannot say yet: there is not enough fresh information on this company.", lines, todo: null, sell: null };
}

// "Would Peter buy it?" comes from sheet.buy (longTermLynch.js). "Did Peter use profit target?"
// (Ganesh 11-20): he had no fixed target. What he wrote in One Up on Wall Street depends on the
// kind of company: a fast grower is kept while it grows, a steady or slow grower is often sold
// after a 30-50% gain, a cyclical before the good years end.
const PETER_HEAD = {
  YES: "Would Peter buy it? Yes.",
  WAIT: "Would Peter buy it? Not at this price.",
  NO: "Would Peter buy it? No.",
  CAREFUL: "Would Peter buy it? Only with care.",
};
const PETER_TODO = {
  YES: "What his rule says: he would buy at this price. In our test, the cheap ones did not do better than other good companies, so this is his checklist, not a promise.",
  NO: "What his rule says: leave it alone.",
  CAREFUL: "What his rule says: be careful, and do not trust the low price.",
};

function peterSell(sheet, row) {
  if (sheet.buy.answer === "NO") return null;
  if (sheet.cyclical) {
    return "When would he sell? He had no profit target. He sold this kind of business when the good years looked close to ending, for example when unsold goods started piling up.";
  }
  if (sheet.grower === "Fast grower") {
    const far = isNum(row.price) && row.price > 0 ? ` (for this one, about ${dollars((row.price / sheet.peg) * 2)})` : "";
    return `When would he sell? He had no profit target. He kept a fast grower for as long as it kept growing, and sold when the growth slowed or the price ran far ahead of the growth${far}.`;
  }
  if (sheet.grower === "Stalwart") {
    return "When would he sell? He had no fixed target, but with a steady grower like this he often took a 30% to 50% gain and moved to another one.";
  }
  return "When would he sell? After a 30% to 50% gain, or sooner if the business got worse. He had no other target.";
}

function peterSide(row) {
  const sheet = lynchSheet(row, { anyQuality: true });
  if (!sheet) {
    const bare = lynchNoSheet(row);
    return {
      headline: `Would Peter buy it? He cannot tell: ${lynchMissing(row) || "it cannot be scored"}.`,
      lines: bare?.pays ? [line("info", bare.pays)] : [], todo: null, sell: null,
    };
  }
  const by = Object.fromEntries(sheet.checks.map((c) => [c.key, c]));
  const mark = (c) => (c.passed === true ? "yes" : c.passed === false ? "no" : "info");
  const growth = Math.round(sheet.growthPct);
  const lines = [];
  if (sheet.lowQuality) lines.push(line("info", "It earns less than $20 a year on every $100 put in, so it is not on the quality list."));
  lines.push(line(mark(by.growth), sheet.growthPct > 50
    ? `Its profit grows ${growth}% a year. That is too fast to last.`
    : `Its profit grows ${growth}% a year. Peter likes 10% to 50%.`));
  if (isNum(row.salesGrowth)) lines.push(line(mark(by.sales), grows(row.salesGrowth, "It sells", "Peter wants 5% or more.")));
  if (row.financial) lines.push(line("info", "It is a bank, so Peter does not judge what it owes this way."));
  else if (isNum(row.debtToEquity)) lines.push(line(mark(by.debt), `${owes(row.debtToEquity)} Peter wants $80 or less.`));
  if (!row.financial && isNum(row.fcfGrowth)) lines.push(line(mark(by.cash), grows(row.fcfGrowth, "The cash it has left over grows")));
  const pe = Math.round(row.pe * 10) / 10;
  const used = Math.min(growth, 50);
  const pays = sheet.dividendPct > 0 ? ` and pays you ${sheet.dividendPct.toFixed(1)}% a year` : "";
  const sum = sheet.dividendPct > 0 ? `${pe} ÷ (${used} + ${sheet.dividendPct.toFixed(1)})` : `${pe} ÷ ${used}`;
  const paying = `You pay $${row.pe.toFixed(2)} for every $1 it earns in a year, and it grows ${growth}% a year${pays}. ${sum} = ${sheet.peg.toFixed(2)}.`;
  if (sheet.cyclical) {
    lines.push(line("info", `${paying} For this kind of business a low price can be a trap: it looks cheapest right before a bad year.`));
  } else {
    lines.push(line(mark(by.price), `${paying} Under 1 is a low price for the growth.`));
  }
  if (sheet.inventoryWarning) lines.push(line("no", "Its shelves are filling up: unsold goods are growing faster than what it sells."));
  const buy = sheet.buy;
  const todo = buy.answer === "WAIT"
    ? `What his rule says: wait.${buy.price != null ? ` Look again at ${dollars(buy.price)} or less.` : ""}`
    : PETER_TODO[buy.answer];
  // buy.why is plain already; "Peter's price: $X or less" is repeated in the todo, so cut it here
  const why = buy.why.replace(/ Peter's price: .*$/, "");
  return { headline: `${PETER_HEAD[buy.answer]} ${why}`, lines, todo, sell: peterSell(sheet, row) };
}

// { warren: { headline, lines: [{ mark: "yes" | "no" | "info", text }], todo, sell }, peter: {...} } or null.
// only = "warren" | "peter" leaves the other side out (2026-11-20, "Peter one don't mixed with
// Warren": the Rule #1 table explains Warren's checklist, the Peter view Peter's).
export function simpleWhy(row, only = null) {
  if (!row || typeof row !== "object") return null;
  return {
    warren: only === "peter" ? null : warrenSide(row),
    peter: only === "warren" || row.signal === "ETF" ? null : peterSide(row),
  };
}
