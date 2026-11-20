// "In simple words" on the Long Term page (2026-11-20, Ganesh: "I need
// justification why Warren or Peter chose that stocks. Build like this 10 kid
// can understand or who don't know any stock market").
//
// For one row: what Warren Buffett's checklist (the page's Rule #1 screen) and
// Peter Lynch's checklist (the Peter view) each say about the company, in
// words a ten-year-old can read. No trading words: no ROIC, EPS, PEG or P/E.
// Every sentence is a number already on the row, said plainly; nothing here
// predicts a price. Pure functions, no React.

import { lynchMissing, lynchSheet } from "./longTermLynch.js";

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
    return { headline: "This is a fund (a basket of many companies), not one business, so Warren's checklist does not fit it.", lines: [], todo: null };
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
  if (s === "BUY") {
    return { headline: "Warren would buy this. It is a great business and today it is on sale.", lines, todo: "What his rule says: this is a price where he would buy." };
  }
  if (s === "WAIT") {
    const at = isNum(row.buyBelow) ? ` Look again if the price drops to ${dollars(row.buyBelow)} or less.` : "";
    return { headline: "Warren would wait. It is a good business, but the price is too high today.", lines, todo: `What his rule says: do nothing now.${at}` };
  }
  if (s === "AVOID") {
    if (row.reason) lines.push(line("no", `The rule it failed: ${String(row.reason).replace(/\.$/, "")}.`));
    return { headline: "Warren would skip this one.", lines, todo: "What his rule says: leave it alone." };
  }
  if (s === "NO_PRICE") return { headline: "We cannot say today: there is no fresh price for it.", lines, todo: null };
  return { headline: "We cannot say yet: there is not enough fresh information on this company.", lines, todo: null };
}

const PETER_HEAD = {
  CHEAP: "Peter would like this. It is growing fast and the price is low for that growth.",
  FAIR: "Peter would call the price fair. You pay about what its growth is worth.",
  PRICEY: "Peter would call this expensive. People already pay a lot for its growth.",
};
const PETER_TODO = {
  CHEAP: "What his rule says: worth a closer look. In our test, the cheap ones did not do better than other good companies, so a low price alone is not a reason to buy.",
  FAIR: "What his rule says: fine to own, but it is no bargain.",
  PRICEY: "What his rule says: wait for a lower price.",
  CYCLICAL: "What his rule says: be careful, and do not trust the low price.",
};

function peterSide(row) {
  const sheet = lynchSheet(row, { anyQuality: true });
  if (!sheet) return { headline: `Peter has no opinion here: ${lynchMissing(row) || "it cannot be scored"}.`, lines: [], todo: null };
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
  if (sheet.cyclical) {
    return {
      headline: `Peter would be careful. This kind of business (${sheet.cyclical}) has good years and bad years, so today's profit may not last.`,
      lines, todo: PETER_TODO.CYCLICAL,
    };
  }
  return { headline: PETER_HEAD[sheet.verdict], lines, todo: PETER_TODO[sheet.verdict] };
}

// { warren: { headline, lines: [{ mark: "yes" | "no" | "info", text }], todo }, peter: {...} } or null.
// only = "warren" | "peter" leaves the other side out (2026-11-20, "Peter one don't mixed with
// Warren": the Rule #1 table explains Warren's checklist, the Peter view Peter's).
export function simpleWhy(row, only = null) {
  if (!row || typeof row !== "object") return null;
  return {
    warren: only === "peter" ? null : warrenSide(row),
    peter: only === "warren" || row.signal === "ETF" ? null : peterSide(row),
  };
}
