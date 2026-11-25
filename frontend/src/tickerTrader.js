// Ticker trader (2026-11-13): helpers for the screen. Pure, so node:test can run them.
// The trading itself is done on the server (momx/ticker_trader.py), simulated only.

export const TICKER_TRADER_URL = "/api/momx-scanner/ticker-trader";
export const TICKER_TRADER_POLL_MS = 10000;
export const TICKER_TRADER_KEY = "bot.tickerTrader";
export const TICKER_CHIPS = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD"];
export const SIMULATED_NOTE = "Simulated inside the app. No order goes to any broker.";

const TICKER = /^[A-Z.]{1,6}$/;

/** What he typed, as the server wants it: capitals, no spaces, no leading $. */
export function cleanTicker(raw) {
  return String(raw ?? "").trim().replace(/^\$/, "").toUpperCase();
}

/** "" when the ticker can be sent, else the plain-words reason. */
export function tickerProblem(raw) {
  const ticker = cleanTicker(raw);
  if (!ticker) return "Type a ticker first.";
  return TICKER.test(ticker) ? "" : "A ticker is letters only, up to 6 (for example NVDA).";
}

export const sideLabel = (side) => (String(side || "").toUpperCase() === "PUT" ? "Put" : "Call");

/** "$10,186" - whole dollars, as on the tiles. */
export function money(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "–";
  return (n < 0 ? "−$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-US");
}

/** "+$186" / "−$142" */
export function signedMoney(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "–";
  const whole = Math.round(n);
  return (whole > 0 ? "+$" : whole < 0 ? "−$" : "$") + Math.abs(whole).toLocaleString("en-US");
}

export function price(value) {
  const n = Number(value);
  return value === null || value === undefined || !Number.isFinite(n) ? "–" : n.toFixed(2);
}

export const tone = (value) => (Number(value) > 0 ? "is-up" : Number(value) < 0 ? "is-down" : "");

/** "10:05 ET" from the server's ET stamp (its own clock digits: no time-zone maths). */
export function timeEt(stamp) {
  const m = /T(\d{2}):(\d{2})/.exec(String(stamp || ""));
  if (!m) return "";
  return `${Number(m[1])}:${m[2]} ET`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Nov 13" from "2026-11-13..." */
export function dayLabel(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(stamp || ""));
  if (!m || !MONTHS[Number(m[2]) - 1]) return "";
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}`;
}

/** "NVDA 165 Call · Nov 14" */
export function contractLabel(trade) {
  if (!trade) return "";
  const strike = Number(trade.strike);
  const parts = [trade.ticker, Number.isFinite(strike) ? String(strike) : "", sideLabel(trade.side)].filter(Boolean).join(" ");
  const expiry = dayLabel(trade.expiry);
  return expiry ? `${parts} · ${expiry}` : parts;
}

/** The ticker's latest trade: the open one, else the last closed one. Null when nothing was bought. */
export function latestTrade(run) {
  if (run?.open) return run.open;
  const trades = run?.trades || [];
  return trades.length ? trades[trades.length - 1] : null;
}

/** When the ticker last did something, for the newest-first order (2026-11-18 "show me latest trade top"). */
export function latestAt(run) {
  const trade = latestTrade(run);
  return String(trade?.closedAt || trade?.boughtAt || run?.noteAt || "");
}

/** Your Tickers, newest trade first; tickers never bought go after the ones with a trade. ET stamps from one
 *  server sort as text. */
export function sortRuns(runs) {
  return [...(runs || [])].sort((a, b) => {
    const traded = Number(Boolean(latestTrade(b))) - Number(Boolean(latestTrade(a)));
    return traded || latestAt(b).localeCompare(latestAt(a));
  });
}

/** The latest trade's contract and times for the list (2026-11-18 "with date and time and expiry, con[tract]"):
 *  contract "410 Call · exp Nov 21" (the ticker is the row's title) and when "Bought Nov 18, 10:05 ET" / "Nov 17, 10:00 → 10:20 ET". */
export function latestTradeFacts(run) {
  const trade = latestTrade(run);
  if (!trade) return null;
  const strike = Number(trade.strike);
  const expiry = dayLabel(trade.expiry);
  const contract = [Number.isFinite(strike) && strike > 0 ? String(strike) : "", sideLabel(trade.side || run?.side)]
    .filter(Boolean).join(" ") + (expiry ? ` · exp ${expiry}` : "");
  const day = dayLabel(trade.boughtAt || trade.day);
  const bought = timeEt(trade.boughtAt);
  const closed = timeEt(trade.closedAt);
  const when = run?.open === trade || !trade.closedAt
    ? `Bought ${day}${bought ? `, ${bought}` : ""}`
    : `${day}, ${bought.replace(" ET", "")} → ${closed}${dayLabel(trade.closedAt) !== day ? ` (${dayLabel(trade.closedAt)})` : ""}`;
  return { contract, when };
}

/** The tiles over Your Tickers (2026-11-18 "This like dashboard" -> "Make it a dashboard"). `asOf` is the server's ET
 *  stamp; "today" is its date. Today = trades closed today + every open trade at its bid (incl. any part already sold).
 *  The account card already shows the all-time Up / Down, so it is not repeated here. */
export function tickersDashboard(runs, asOf) {
  const today = String(asOf || "").slice(0, 10);
  let todayPl = 0, closed = 0, wins = 0, open = 0, openMoney = 0, old = 0;
  const perTicker = [];
  for (const run of runs || []) {
    for (const t of run.trades || []) {
      closed += 1;
      if (Number(t.pl) > 0) wins += 1;
      if (today && String(t.closedAt || "").slice(0, 10) === today) todayPl += Number(t.pl) || 0;
    }
    if (run.open) {
      open += 1;
      if (oldPriceMark(run)) old += 1;
      const live = (openPl(run) || 0) + (Number(run.open.pl) || 0);
      todayPl += live;
      openMoney += live;
    }
    if (latestTrade(run)) perTicker.push({ ticker: run.ticker, money: Number(rowMoney(run)) || 0 });
  }
  perTicker.sort((a, b) => b.money - a.money);
  const best = perTicker[0], worst = perTicker[perTicker.length - 1];
  const round = (n) => Math.round(n * 100) / 100;
  // An open trade whose option has no live price is counted at its OLD bid: the tile says so (2026-11-25).
  const oldNote = !old ? "" : old === open ? ` · ${OLD_PRICE}` : ` · ${old} at an old price`;
  return {
    today: { text: signedMoney(round(todayPl)), tone: tone(todayPl), note: open ? `${signedMoney(round(openMoney))} in ${open} open${oldNote}` : "nothing open" },
    winRate: { text: closed ? `${Math.round((wins / closed) * 100)}%` : "–", note: closed ? `${wins} of ${closed} won` : "no closed trades" },
    trades: { text: String(closed + open), note: `${closed} closed · ${open} open` },
    best: best ? { text: `${best.ticker} ${signedMoney(best.money)}`, tone: tone(best.money),
      note: worst && worst !== best ? `worst ${worst.ticker} ${signedMoney(worst.money)}` : "" } : { text: "–", tone: "", note: "" },
  };
}

/** The pill beside the ticker: what the run is doing, in his words. */
export function statusLabel(run) {
  if (!run) return "";
  if (run.open) return `In a trade · ${sideLabel(run.side).toUpperCase()}`;
  if (run.status === "waiting") return "Waiting to buy";
  if (run.status === "stopped") return "Stopped";
  return "No trade open";
}

/** The small line under "NVDA · Call" in the list. */
/** The ticker's money on the list: closed trades plus the open trade at its last bid (2026-11-18 "show pnl next
 *  to tickers"). Falls back to `net` from a server that does not send liveNet yet. */
export function rowMoney(run) {
  const live = Number(run?.liveNet);
  if (run?.liveNet !== null && run?.liveNet !== undefined && Number.isFinite(live)) return live;
  const open = openPl(run);
  return open === null ? run?.net : Math.round(((Number(run?.net) || 0) + open) * 100) / 100;
}

/** The held contracts at their last bid: the server's openPl, or worked out from the open trade (a server that has
 *  not been restarted since 2026-11-18 sends bid, entry and held but not openPl). Null until a bid has been seen. */
export function openPl(run) {
  if (!run?.open) return null;
  if (run.openPl !== undefined) return run.openPl === null || !Number.isFinite(Number(run.openPl)) ? null : Number(run.openPl);
  const { bid, entry, held } = run.open;
  if (bid === null || bid === undefined || !Number.isFinite(Number(bid)) || Number(bid) < 0 || !(Number(entry) > 0) || !(Number(held) > 0)) return null;
  return Math.round((Number(bid) - Number(entry)) * 100 * Number(held) * 100) / 100;
}

/** "−38%" under the ticker's money (2026-11-18 "I don't see percentage"): the money (closed trades + the open one at
 *  its bid) over what those trades cost to buy. "" when nothing was bought. */
export function rowPercent(run) {
  const costOf = (t) => {
    const cost = Number(t?.cost);
    if (cost > 0) return cost;
    const entry = Number(t?.entry), qty = Number(t?.qty);
    return entry > 0 && qty > 0 ? entry * 100 * qty : 0;
  };
  const spent = (run?.trades || []).reduce((sum, t) => sum + costOf(t), 0) + (run?.open ? costOf(run.open) : 0);
  const value = Number(rowMoney(run));
  if (!(spent > 0) || !Number.isFinite(value)) return "";
  const pct = Math.round((value / spent) * 100);
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`;
}

/** " · open +$42 (+12%)" for a trade still held, once a bid has been seen. With no live price on the option the
 *  number comes from an old bid, and says so: " · open +$42 (+12%), price is old". */
export function openPlText(run) {
  const pl = openPl(run);
  if (pl === null) return "";
  const gain = openGain(run.open);
  const old = oldPriceMark(run);
  return ` · open ${signedMoney(pl)}${gain ? ` (${gain})` : ""}${old ? `, ${old}` : ""}`;
}

export function runLine(run) {
  if (!run) return "";
  if (run.open || run.status === "waiting") return (run.note || statusLabel(run)) + openPlText(run);
  const count = Number(run.tradeCount) || 0;
  if (!count) return run.note || "No trades yet";
  const wins = Number(run.wins) || 0;
  return `${count} ${count === 1 ? "trade" : "trades"} · ${wins} ${wins === 1 ? "win" : "wins"}`;
}

export function winsLabel(run) {
  const count = Number(run?.tradeCount) || 0;
  return count ? `${Number(run.wins) || 0} of ${count}` : "–";
}

/** The option stop (2026-11-24): the option price at or under which everything is sold, the highest whole cent that
 *  is down 50% or more from the price paid. The server's own number when it sends one, else worked out from the entry
 *  (a server not restarted since). Null with no entry price. */
export function optionStopPrice(trade) {
  const saved = trade?.stopOption;
  if (saved !== null && saved !== undefined && Number.isFinite(Number(saved)) && Number(saved) >= 0) return Number(saved);
  const entry = Number(trade?.entry);
  return entry > 0 ? Math.floor(entry * 50 + 1e-6) / 100 : null;
}

/** The small words beside the open trade's Stop: where the stock stop stands, and the option's own stop price. */
export function stopNote(trade) {
  const level = optionStopPrice(trade);
  const bid = level === null ? "" : `$${price(level)}`;
  if (trade?.stopKind === "break-even") return bid ? `moved to break-even · or option bid ${bid}` : "moved to break-even";
  if (trade?.stopKind === "T1") return bid ? `moved to T1 · or option bid ${bid}` : "moved to T1";
  return bid ? `or the option's bid at ${bid} (down 50%)` : "option down 50%";
}

const STOP_NOTES = ["Stop:", "Break-even stop:", "Stop at T1:", "Option stop:"];

/** Which stop sold a closed trade ("stock" / "option" / null) and how far under the price paid that sale was, in whole
 *  percent. The server's own stopFired / stopLossPct when the trade carries them; a trade saved before 2026-11-24
 *  carries neither, so they are worked out from what it did save (the same rule as momx/ticker_journal.py stop_sale):
 *  the stop's own sale note says which stop, and that sale's price against the entry is the loss. */
export function stopSale(t) {
  let fired = t?.stopFired === "option" || t?.stopFired === "stock" ? t.stopFired : null;
  let lost = t?.stopLossPct !== null && t?.stopLossPct !== undefined && Number.isFinite(Number(t.stopLossPct)) ? Number(t.stopLossPct) : null;
  const stops = (t?.events || []).filter((e) => STOP_NOTES.some((s) => String(e?.text || "").startsWith(s)));
  const last = stops[stops.length - 1];
  if (!fired && !last) return { fired: null, lost: null };
  const text = String(last?.text || "");
  if (!fired) fired = text.startsWith("Option stop:") ? "option" : "stock";
  if (lost === null && last) {
    const entry = Number(t.entry);
    const sale = [...(t.sells || [])].reverse().find((s) => last.at && s?.at === last.at && typeof s.price === "number");
    const said = /sold \d+ at \$(\d+(?:\.\d+)?)/.exec(text);
    const sold = sale ? sale.price : said ? Number(said[1]) : null;
    if (sold !== null && entry > 0) lost = (1 - sold / entry) * 100;
    else if (!(t.hits || []).length && typeof t.plPct === "number" && Number.isFinite(t.plPct)) lost = -t.plPct;
  }
  return { fired, lost: lost === null ? null : Math.round(lost) };
}

/** The Stop line of a closed trade in the journal: which stop sold it and the real loss, when a stop did. */
export function closedStopNote(t) {
  const { fired, lost } = stopSale(t);
  const how = lost === null ? "" : lost > 0 ? ` · option down ${lost}%` : lost < 0 ? ` · option up ${Math.abs(lost)}%` : " · option level";
  if (fired === "option") return `option stop sold it${how}`;
  if (fired === "stock") return `stock stop sold it${how}`;
  if (t?.stopOption === null || t?.stopOption === undefined) return "option down 50%";       // bought before the option stop, and no stop sold it
  return `or the option's bid at $${price(optionStopPrice(t))} (down 50%)`;
}

/** "" while the open trade's option has a live price; else the words that say its last bid is old (2026-11-24: an
 *  option with no bid cannot be sold and its option stop cannot be checked, so the screen never shows it as fine). */
export function quoteWarning(run) {
  return run?.open && run.quoteDead ? "no live price since" : "";
}

export const OLD_PRICE = "price is old";

/** "price is old" beside a ticker's money while its open trade has no live price (2026-11-25): the open profit shown
 *  is worked out from the last bid seen, not from a live one. "" otherwise. */
export function oldPriceMark(run) {
  return quoteWarning(run) ? OLD_PRICE : "";
}

/** The same mark for the one account: how many open trades are counted at an old price. "" when none are. */
export function accountOldNote(account) {
  const n = Math.round(Number(account?.oldPriceTrades)) || 0;
  if (n < 1) return "";
  return n === 1 ? "1 open trade's price is old" : `${n} open trades' prices are old`;
}

/** The ladder of the open trade, top to bottom: T3, T2, T1, Entry, Stop. */
export function ladder(trade) {
  if (!trade) return [];
  const hits = new Map((trade.hits || []).map((h) => [h.target, h]));
  const reasons = new Map((trade.targets || []).map((t) => [t.label, t.reason]));
  const rows = ["T3", "T2", "T1"].map((label) => {
    const hit = hits.get(label);
    return {
      key: label, kind: "target", hit: Boolean(hit), label: hit ? `${label} ✓` : label, value: price(trade[label.toLowerCase()]),
      note: hit ? `hit ${timeEt(hit.at)} · ${hit.sold ? `sold ${hit.sold}` : "none sold"}` : reasons.get(label) || "",
    };
  });
  rows.push({ key: "Entry", kind: "entry", hit: false, label: "Entry", value: `${price(trade.entrySpot)} · ask $${price(trade.entry)}`,
    note: `${trade.qty} ${Number(trade.qty) === 1 ? "contract" : "contracts"}` });
  rows.push({ key: "Stop", kind: "stop", hit: false, label: "Stop", value: price(trade.stopStock), note: stopNote(trade) });
  return rows;
}

/** The open trade's gain so far on the last bid, as "+38%"; "" with no bid yet. */
export function openGain(trade) {
  const bid = Number(trade?.bid);
  const entry = Number(trade?.entry);
  if (!trade || trade.bid === null || trade.bid === undefined || !Number.isFinite(bid) || !(entry > 0)) return "";
  const pct = Math.round((bid / entry - 1) * 100);
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`;
}

/** Closed trades, newest first. */
export function tradeRows(run) {
  return [...(run?.trades || [])].reverse().map((t) => ({
    key: t.id, title: `${dayLabel(t.day)} · ${Number(t.strike)} ${sideLabel(t.side)}`, pl: t.pl, plText: signedMoney(t.pl), tone: tone(t.pl),
    reason: t.reason || "", when: `${timeEt(t.boughtAt)} to ${timeEt(t.closedAt)}`, balanceAfter: money(t.balanceAfter),
  }));
}

/** The link that opens the chart on this ticker (appLinks.js openAppLink). */
export function chartLink(ticker) {
  return { label: `${ticker} chart`, view: "Charts & OI", ticker };
}

/** What to say when a read or a button press failed. */
export function failureText(status, body) {
  if (body && typeof body.error === "string" && body.error && status !== 403) return body.error;
  if (status === 403) return "Only the owner can use the Ticker trader.";
  if (status === 404 || status === 503 || status === 502) return "The Ticker trader starts with the next app restart.";
  return "The Ticker trader did not answer. It will try again.";
}

// Account size (2026-11-13, his "So I Change it 10k,5k, 2k or 100K", "What ever I want").
export const SIZE_CHOICES = [2000, 5000, 10000, 100000];
export const DEFAULT_SIZE = 10000;
export const MIN_SIZE = 500;
export const MAX_SIZE = 1000000;
export const SIZE_KEY = "bot.tickerTrader.size";

/** "$2K" / "$100K" on the quick buttons. */
export const sizeChip = (n) => `$${Math.round(Number(n) / 1000)}K`;

/** Whole dollars from "$5,000", "5000" or " 5,000 "; null when it is not a plain amount.
 *  "10k" is NOT accepted: digits, commas and one leading $ only. */
export function parseSize(raw) {
  const text = String(raw ?? "").trim().replace(/^\$/, "").replace(/,/g, "").trim();
  if (!/^\d+(\.\d+)?$/.test(text)) return null;
  const n = Math.round(Number(text));
  return Number.isFinite(n) ? n : null;
}

/** "" when this size can be sent, else the plain-words reason. */
export function sizeProblem(raw) {
  if (!String(raw ?? "").trim()) return "Type an account size (for example 10000).";
  const n = parseSize(raw);
  if (n === null) return "Account size is a dollar amount, digits only (for example 10000 or $10,000).";
  if (n < MIN_SIZE) return `Account size must be at least ${money(MIN_SIZE)}.`;
  if (n > MAX_SIZE) return `Account size can be at most ${money(MAX_SIZE)}.`;
  return "";
}

/** The size this device used last time, else $10,000. Storage can be missing or throw. */
export function savedSize(storage) {
  try {
    const raw = storage ? storage.getItem(SIZE_KEY) : null;
    const n = parseSize(raw);
    return n !== null && !sizeProblem(String(n)) ? n : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

export function saveSize(storage, n) {
  try { if (storage) storage.setItem(SIZE_KEY, String(n)); } catch { /* private window: not remembered */ }
}

// ---- Journal and AI review (2026-11-13, his "need AI model to improve also tradezella journal also so I know how
// I see improve"): GET .../ticker-trader/journal?id=<run|all> (momx/ticker_journal.py). The Scorecard's own tiles,
// daily bars, calendar and curve maths (botDesk.js) draw it; these helpers add what only this journal has.

export const JOURNAL_URL = `${TICKER_TRADER_URL}/journal`;
export const NOTE_URL = `${TICKER_TRADER_URL}/note`;
export const REVIEW_URL = `${TICKER_TRADER_URL}/review`;
export const JOURNAL_POLL_MS = 60000;
export const REVIEW_LABEL = "AI review, can be wrong. Only you see this.";
export const NOTE_MAX = 2000;
export const ALL_SCOPE = "all";

export function journalUrl(scope) {
  return `${JOURNAL_URL}?id=${encodeURIComponent(String(scope || ALL_SCOPE))}`;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** The one account every ticker spends from (2026-11-16): what it is worth now, the cash left, up or down.
 * null = the server has not sent an account (an older server). */
export function accountTiles(account) {
  const a = account || {};
  if (![a.startBalance, a.cash, a.value].every(isNum)) return null;
  const open = Number(a.openTrades) || 0;
  return {
    value: money(a.value), from: `from ${money(a.startBalance)}`,
    cash: money(a.cash), inTrades: open ? `${money(a.invested)} in ${open} open ${open === 1 ? "trade" : "trades"}` : "nothing in trades",
    net: signedMoney(a.net), tone: tone(a.net), start: a.startBalance,
  };
}

/** Average win, average loss, expectancy, best and worst trade: the small tiles under the five big ones. */
export function journalStats(doc) {
  const s = doc?.stats || {};
  const n = s.n || 0;
  const cell = (key, label, value, sub = "") => ({ key, label, value: n && isNum(value) ? signedMoney(value) : "–", tone: n ? tone(value) : "", sub });
  return [
    cell("avgWin", "Average win", s.avgWin, `${s.wins || 0} won`),
    cell("avgLoss", "Average loss", s.avgLoss, `${s.losses || 0} lost`),
    cell("expectancy", "Expectancy", s.expectancy, "per trade"),
    cell("best", "Best trade", s.bestTrade),
    cell("worst", "Worst trade", s.worstTrade),
  ];
}

/** The equity curve as the points botDesk.deskCurve draws: each closed trade, its balance above or below the start. */
export function equityDays(doc) {
  const start = Number(doc?.startBalance) || 0;
  return (doc?.equity || []).filter((p) => p && isNum(p.balance)).map((p, i) => ({
    day: String(p.id || i), cum: Math.round((p.balance - start) * 100) / 100, pnl: isNum(p.pl) ? p.pl : 0, balance: p.balance, at: p.at,
  }));
}

const BREAKDOWN_TITLES = [["side", "Call vs Put"], ["time", "Time of day bought (ET)"], ["outcome", "Which target was reached"],
  ["hold", "Hold time"], ["ticker", "By ticker"]];

/** The breakdown tables: each bucket's trades, win rate and net. A bucket with no trades shows dashes. */
export function breakdownTables(doc) {
  const groups = doc?.breakdowns || {};
  return BREAKDOWN_TITLES.filter(([key]) => Array.isArray(groups[key]) && groups[key].length).map(([key, title]) => ({
    key, title,
    rows: groups[key].map((r) => ({
      key: String(r.key), label: String(r.label ?? r.key), trades: Number(r.trades) || 0,
      win: r.trades && isNum(r.winRatePct) ? `${Math.round(r.winRatePct)}%` : "–",
      net: r.trades ? signedMoney(r.pnl) : "–", tone: r.trades ? tone(r.pnl) : "",
    })),
  }));
}

/** "What to improve": the server's lines with their trade counts; under 10 trades each says it is too few to judge. */
export function improveItems(doc) {
  return (doc?.improve || []).map((x) => ({
    key: String(x.key), title: String(x.title || ""), text: String(x.text || ""),
    count: `${Number(x.trades) || 0} ${Number(x.trades) === 1 ? "trade" : "trades"}`,
    judge: x.enough ? "" : String(x.judge || "Too few trades to judge"),
  }));
}

export const OUTCOME_LABELS = { stopped: "Stopped before T1", T1: "Reached T1", T2: "Reached T2", T3: "Reached T3", early: "Closed before T1" };

/** "12 min" / "1 h 05 min" */
export function holdLabel(minutes) {
  const m = Number(minutes);
  if (minutes === null || minutes === undefined || !Number.isFinite(m) || m < 0) return "–";
  if (m < 1) return "under 1 min";
  if (m < 60) return `${Math.round(m)} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(Math.round(m - h * 60)).padStart(2, "0")} min`;
}

/** The journal's trade list, newest first. */
export function journalTrades(doc) {
  return (doc?.trades || []).map((t) => ({
    key: String(t.id), id: String(t.id), title: `${dayLabel(t.day || t.closedAt)} · ${t.ticker} ${Number(t.strike)} ${sideLabel(t.side)}`,
    when: `${timeEt(t.boughtAt)} to ${timeEt(t.closedAt)}`, pl: t.pl, plText: signedMoney(t.pl), tone: tone(t.pl),
    outcome: OUTCOME_LABELS[t.outcome] || "", hasNote: Boolean(t.note), best: t.id === doc.bestId, worst: t.id === doc.worstId,
  }));
}

/** One trade's card: the plan it was bought on, what was hit and when, every sell, and the chart button for that day. */
export function tradeDetail(t) {
  if (!t) return null;
  const hits = new Map((t.hits || []).map((h) => [h.target, h]));
  const reasons = new Map((t.targets || []).map((x) => [x.label, x.reason]));
  const levels = [
    { key: "Entry", label: "Entry", value: `${price(t.entrySpot)} · ask $${price(t.entry)}`, note: `${timeEt(t.boughtAt)} · ${t.qty} ${Number(t.qty) === 1 ? "contract" : "contracts"}` },
    { key: "Stop", label: "Stop", value: price(t.stopStart ?? t.stopStock), note: closedStopNote(t) },
    ...["T1", "T2", "T3"].map((label) => {
      const hit = hits.get(label);
      return { key: label, label: hit ? `${label} ✓` : label, value: price(t[label.toLowerCase()]), hit: Boolean(hit),
        note: hit ? `hit ${timeEt(hit.at)}${hit.sold ? ` · sold ${hit.sold} at $${price(hit.price)}` : ""}` : reasons.get(label) || "not reached" };
    }),
  ];
  return {
    id: String(t.id), title: contractLabel(t), plText: signedMoney(t.pl), tone: tone(t.pl),
    pct: isNum(t.plPct) ? `${t.plPct > 0 ? "+" : t.plPct < 0 ? "−" : ""}${Math.abs(t.plPct).toFixed(1)}%` : "",
    outcome: OUTCOME_LABELS[t.outcome] || "", hold: holdLabel(t.holdMinutes), levels,
    events: (t.events || []).map((e, i) => ({ key: `${i}`, at: timeEt(e.at), text: String(e.text || "") })),
    link: { label: `${t.ticker} chart`, view: "Charts & OI", ticker: t.ticker, timeframe: "5m", day: String(t.day || "").slice(0, 10) || undefined },
    note: String(t.note || ""), noteAt: t.noteAt || null,
  };
}

/** "" when a note can be saved, else the reason in plain words. */
export function noteProblem(text, max = NOTE_MAX) {
  if (typeof text !== "string") return "A note is plain text.";
  return text.length > max ? `A note can be at most ${max.toLocaleString("en-US")} characters.` : "";
}

/** Ask the server for today's AI review? Only once per opening, only with closed trades, and only when today has
 * neither a paragraph nor a recorded try (the server keeps it to one AI call per run per day either way). */
export function shouldAskReview(doc, asked) {
  return Boolean(doc && !asked && !doc.review && !doc.reviewStatus && (doc.stats?.n || 0) > 0);
}
