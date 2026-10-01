// The virtual BOT page (2026-09-28, admin only): which rule is winning, which
// tickers it took, P/L. Data: GET /api/momx-scanner/bot?days=N (momx_worker,
// refused on the server for non-admins). No order is ever sent.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Bot, RefreshCw, X } from "lucide-react";
import { BOT_RULE_HELP, BOT_RULE_LABELS, BOT_RULE_ORDER, botRuleLabel, BOT_TEST_RULES, botContractLabel, botDailyLog, botEntryStrikeLabel, botMomentumMatrix, botPeriodMatrix, botReport, formatBotDollars, formatBotPct, nextBotSort, sortBotRows } from "./momxBot.js";

// Trading days with bot data (his ask 2026-09-28: daily / 2D / 3D / 4D /
// weekly / 2 week / 3 week ... overall).
const RANGES = [
  { days: 1, label: "Today" }, { days: 2, label: "2D" }, { days: 3, label: "3D" }, { days: 4, label: "4D" },
  { days: 5, label: "1W" }, { days: 10, label: "2W" }, { days: 15, label: "3W" }, { days: 20, label: "1M" },
  { days: 90, label: "90D" }, { days: 180, label: "180D" },
];
const HISTORY_REFRESH_MS = 10 * 60 * 1000;
// One view at a time (his ask 2026-09-28: "page is very big, difficult to
// scroll ... make it tab or panel"). The chosen tab is remembered.
const BOT_TABS = [
  { key: "leaders", label: "Leaderboard" },
  { key: "trades", label: "Trades" },
  { key: "periods", label: "Winners by period" },
  { key: "momentum", label: "Momentum at entry" },
  { key: "days", label: "Day by day" },
  { key: "rules", label: "Rules explained" },
];
const BOT_TAB_KEY = "bot.tab";
function readStoredTab() {
  try {
    const v = window.localStorage.getItem(BOT_TAB_KEY);
    return BOT_TABS.some((t) => t.key === v) ? v : "leaders";
  } catch {
    return "leaders";
  }
}
const REFRESH_MS = 60 * 1000;

function tone(value) {
  if (value === null || value === undefined || value === 0) return "";
  return value > 0 ? " is-up" : " is-down";
}

function marketOpenNow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = (type) => (parts.find((p) => p.type === type) || {}).value;
  const minute = Number(get("hour")) * 60 + Number(get("minute"));
  return !["Sat", "Sun"].includes(get("weekday")) && minute >= 9 * 60 + 30 && minute <= 16 * 60 + 5;
}

function Kpi({ label, value, sub, className = "" }) {
  return (
    <div className={"bot-kpi" + className}>
      <span className="bot-kpi-label">{label}</span>
      <span className="bot-kpi-value">{value}</span>
      {sub ? <span className="bot-kpi-sub">{sub}</span> : null}
    </div>
  );
}

// A sortable header: click = high first, again = low first, again = no sort.
function SortTh({ k, sort, onSort, children, className = "", title }) {
  const on = sort.key === k;
  return (
    <th className={className + " bot-sortable" + (on ? " is-sorted" : "")} title={title || "Sort: high → low → off"}
      aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"} onClick={() => onSort(k)}>
      {children}<span className="bot-sort-arrow">{on ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</span>
    </th>
  );
}

const LEADER_SORT = {
  rule: (r) => r.label, trades: (r) => r.trades, wins: (r) => r.wins, winRate: (r) => r.winRate, avgExit: (r) => r.avgExit,
  dollars: (r) => r.dollars, roi: (r) => r.roi, entryStrike: (r) => r.avgEntryStrike, zero: (r) => r.avgZero,
  max: (r) => r.avgMax, close: (r) => r.avgClose,
  avgWin: (r) => r.avgWin, avgLoss: (r) => r.avgLoss, best: (r) => r.best, worst: (r) => r.worst,
  daysUp: (r) => (r.daysTraded ? r.daysUp / r.daysTraded : null), perDay: (r) => r.perDay,
  pf: (r) => (r.profitFactor === Infinity ? 1e9 : r.profitFactor),
};
const TRADE_SORT = {
  at: (t) => String(t.at || t.day), entryAt: (t) => String(t.entryAt || t.at || ""), exitAt: (t) => String(t.exitAt || ""), rule: (t) => t.label, symbol: (t) => t.symbol, strike: (t) => Number(t.strike),
  entry: (t) => Number(t.entry), now: (t) => Number(t.exitPrice ?? t.last), roi: (t) => t.results.exit,
  dollars: (t) => t.results.exitDollars, why: (t) => t.exitReason || "", eStrike: (t) => Number(t.eStrike),
  ePct: (t) => t.results.entryStrike, zStrike: (t) => Number(t.zStrike), zPct: (t) => t.results.zero,
  max: (t) => t.results.max, status: (t) => t.status || "",
};
const DAY_SORT = {
  day: (d) => d.day, trades: (d) => d.trades, wins: (d) => d.wins, winRate: (d) => d.winRate, avg: (d) => d.avgExit,
  dollars: (d) => d.dollars, roi: (d) => d.roi, best: (d) => (d.best ? d.best.label : ""), worst: (d) => (d.worst ? d.worst.label : ""),
};
// Matrix columns (periods / momentum states) sort their rows by ROI in that column.
const cellSort = (i) => (row) => (row.cells[i] && row.cells[i].priced ? row.cells[i].roi : null);

// "HH:MM" in ET from the bot's ISO stamps (they carry the ET offset).
function botClock(iso) {
  const s = String(iso || "");
  return s.length >= 16 ? s.slice(11, 16) : "-";
}

function statusOf(t) {
  if (t.status === "pending") return "pending";
  if (t.status === "no_contract") return "no contract";
  if (t.exitAt) return "exited";
  return t.status === "closed" ? "closed" : "open";
}

export default function BotPanel() {
  const [days, setDays] = useState(1);
  const [side, setSide] = useState("bull");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [rule, setRule] = useState(null);
  const [search, setSearch] = useState("");
  const [updated, setUpdated] = useState(null);
  const [history, setHistory] = useState(null);   // all kept days, for the period table
  const [tab, setTabState] = useState(readStoredTab);
  const [leaderSort, setLeaderSort] = useState({ key: null, dir: null });
  // "More trades AND more wins" (his ask 2026-09-30): hide rules with few trades.
  const [minTrades, setMinTradesState] = useState(() => {
    try { return Number(localStorage.getItem("bot.minTrades")) || 0; } catch { return 0; }
  });
  const setMinTrades = (n) => {
    setMinTradesState(n);
    try { localStorage.setItem("bot.minTrades", String(n)); } catch { /* private window */ }
  };
  const [tradeSort, setTradeSort] = useState({ key: null, dir: null });
  const [daySort, setDaySort] = useState({ key: null, dir: null });
  const [periodSort, setPeriodSort] = useState({ key: null, dir: null });
  const [momSort, setMomSort] = useState({ key: null, dir: null });
  const cycle = (setter) => (key) => setter((s) => nextBotSort(s, key));
  const setTab = useCallback((key) => {
    setTabState(key);
    try { window.localStorage.setItem(BOT_TAB_KEY, key); } catch { /* private mode */ }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch("/api/momx-scanner/bot?days=180", { credentials: "include", cache: "no-store" });
      if (res.ok) setHistory(await res.json());
    } catch {
      // the main view shows the error; the period table just waits
    }
  }, []);
  useEffect(() => {
    loadHistory();
    const id = window.setInterval(() => { if (marketOpenNow()) loadHistory(); }, HISTORY_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [loadHistory]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/momx-scanner/bot?days=" + days, { credentials: "include", cache: "no-store" });
      if (res.status === 403) throw new Error("The Bot page is for admin accounts only.");
      if (!res.ok) throw new Error("Could not load the bot (HTTP " + res.status + ").");
      setData(await res.json());
      setError("");
      setUpdated(new Date());
    } catch (err) {
      setError(String((err && err.message) || err));
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const id = window.setInterval(() => { if (marketOpenNow()) load(); }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const report = useMemo(() => botReport(data ? data[side] : [], { side }), [data, side]);
  const rows = useMemo(() => {
    const q = search.trim().toUpperCase();
    return report.rows.filter((r) => (!rule || r.rule === rule) && (!q || r.symbol.includes(q)));
  }, [report, rule, search]);
  const { total, leaderboard } = report;
  // Totals for exactly the trades on screen (rule + ticker filters applied).
  const shownTotal = useMemo(() => botReport([{ day: "shown", trades: rows }]).total, [rows]);
  const best = leaderboard.find((r) => r.priced > 0) || null;
  const worst = [...leaderboard].reverse().find((r) => r.priced > 0) || null;
  const matrix = useMemo(() => botPeriodMatrix(history ? history[side] : [], undefined, side), [history, side]);
  const momentum = useMemo(() => botMomentumMatrix(report.rows), [report]);
  const dailyLog = useMemo(() => botDailyLog(history ? history[side] : [], side), [history, side]);
  const ruleLabel = rule ? (leaderboard.find((r) => r.rule === rule) || {}).label || rule : null;

  return (
    <div className="bot-page">
      <header className="bot-head">
        <div className="bot-title">
          <Bot size={18} aria-hidden="true" />
          <h2>Bot</h2>
          <span className="bot-badge">VIRTUAL · no orders</span>
          <span className="bot-badge is-admin">Admin only</span>
        </div>
        <div className="bot-controls">
          <div className="bot-seg" role="group" aria-label="Board">
            {["bull", "bear"].map((s) => (
              <button key={s} type="button" className={side === s ? "is-on" : ""} onClick={() => { setSide(s); setRule(null); }}>
                {s === "bull" ? "BULL · calls" : "BEAR · puts"}
              </button>
            ))}
          </div>
          <div className="bot-seg" role="group" aria-label="Range">
            {RANGES.map((r) => (
              <button key={r.days} type="button" className={days === r.days ? "is-on" : ""} onClick={() => setDays(r.days)}>
                {r.label}
              </button>
            ))}
          </div>
          <button type="button" className="bot-refresh" onClick={load} disabled={loading} title="Refresh">
            <RefreshCw size={14} aria-hidden="true" className={loading ? "is-spinning" : ""} />
            {updated ? updated.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" }) + " ET" : "Load"}
          </button>
        </div>
      </header>

      {error ? <p className="bot-error" role="alert">{error}</p> : null}

      <section className="bot-kpis" aria-label="Totals">
        <Kpi label="P/L · your exit" value={formatBotDollars(total.dollars)} sub="per 1 contract each" className={tone(total.dollars)} />
        <Kpi label="ROI" value={formatBotPct(total.roi, 1)} sub={"on $" + Math.round(total.invested).toLocaleString("en-US") + " of contracts"} className={tone(total.roi)} />
        <Kpi label="Win rate" value={total.winRate === null ? "-" : Math.round(total.winRate) + "%"} sub={total.wins + " W · " + total.losses + " L"} />
        <Kpi label="Avg trade" value={formatBotPct(total.avgExit)} sub={"0DTE " + formatBotPct(total.avgZero) + " · max " + formatBotPct(total.avgMax)} className={tone(total.avgExit)} />
        <Kpi label="Trades" value={String(total.trades)} sub={report.days.length + (report.days.length === 1 ? " day" : " days")} />
        <Kpi label="Best rule" value={best ? best.label : "-"} sub={best ? formatBotPct(best.avgExit) + " avg" : ""} className={best ? tone(best.avgExit) : ""} />
        <Kpi label="Worst rule" value={worst ? worst.label : "-"} sub={worst ? formatBotPct(worst.avgExit) + " avg" : ""} className={worst ? tone(worst.avgExit) : ""} />
      </section>

      <nav className="bot-tabs" role="tablist" aria-label="Bot views">
        {BOT_TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={"bot-tab" + (tab === t.key ? " is-on" : "")} onClick={() => setTab(t.key)}>
            {t.label}{t.key === "trades" && rule ? " · " + botRuleLabel(rule, side) : ""}
          </button>
        ))}
      </nav>

      {tab === "leaders" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Rule leaderboard</h3>
          <span className="bot-hint">Ranked by average % at your exit. Click a rule to open its trades.</span>
          <div className="bot-seg bot-min-trades" role="group" aria-label="Minimum trades">
            <span className="bot-hint">Min trades</span>
            {[0, 20, 50, 100].map((n) => (
              <button key={n} type="button" className={minTrades === n ? "is-on" : ""} aria-pressed={minTrades === n}
                onClick={() => setMinTrades(n)}>{n === 0 ? "All" : n + "+"}</button>
            ))}
            <button type="button" title="Rules with 50+ trades, best win rate first"
              className={minTrades === 50 && leaderSort.key === "winRate" && leaderSort.dir === "desc" ? "is-on" : ""}
              onClick={() => { setMinTrades(50); setLeaderSort({ key: "winRate", dir: "desc" }); }}>Most trades + best win %</button>
          </div>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="rule" sort={leaderSort} onSort={cycle(setLeaderSort)} className="is-left">Rule</SortTh>
                <SortTh k="trades" sort={leaderSort} onSort={cycle(setLeaderSort)}>Trades</SortTh>
                <SortTh k="wins" sort={leaderSort} onSort={cycle(setLeaderSort)}>W / L</SortTh>
                <SortTh k="winRate" sort={leaderSort} onSort={cycle(setLeaderSort)}>Win %</SortTh>
                <SortTh k="avgExit" sort={leaderSort} onSort={cycle(setLeaderSort)}>Your exit</SortTh>
                <SortTh k="dollars" sort={leaderSort} onSort={cycle(setLeaderSort)}>$ P/L</SortTh>
                <SortTh k="roi" sort={leaderSort} onSort={cycle(setLeaderSort)}>ROI</SortTh>
                <SortTh k="pf" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Profit factor: $ won ÷ $ lost. Above 1 = made money; 2 = won twice what it lost.">Profit factor</SortTh>
                <SortTh k="avgWin" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average % of the winning trades">Avg win</SortTh>
                <SortTh k="avgLoss" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average % of the losing trades">Avg loss</SortTh>
                <SortTh k="best" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Best single trade at your exit">Best</SortTh>
                <SortTh k="worst" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Worst single trade at your exit">Worst</SortTh>
                <SortTh k="daysUp" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Days it made money / days it traded">Days +</SortTh>
                <SortTh k="perDay" sort={leaderSort} onSort={cycle(setLeaderSort)} title="Average trades a day">Trades / day</SortTh>
                <SortTh k="entryStrike" sort={leaderSort} onSort={cycle(setLeaderSort)}>ENTRY strike</SortTh>
                <SortTh k="zero" sort={leaderSort} onSort={cycle(setLeaderSort)}>0DTE</SortTh>
                <SortTh k="max" sort={leaderSort} onSort={cycle(setLeaderSort)}>Hold max</SortTh>
                <SortTh k="close" sort={leaderSort} onSort={cycle(setLeaderSort)}>Day close</SortTh>
              </tr>
            </thead>
            <tbody>
              {leaderboard.filter((r) => r.trades >= minTrades).length === 0 ? (
                <tr><td colSpan={18} className="bot-empty">No trades in this range yet. The bot trades 09:30-15:30 ET.</td></tr>
              ) : sortBotRows(leaderboard.filter((r) => r.trades >= minTrades), leaderSort, LEADER_SORT).map((r, i) => (
                <tr key={r.rule} className={"is-click" + (rule === r.rule ? " is-selected" : "")} onClick={() => { setRule(r.rule); setTab("trades"); }}>
                  <td className="is-left" title={BOT_RULE_HELP[r.rule] ? BOT_RULE_HELP[r.rule].what + " Needs: " + BOT_RULE_HELP[r.rule].needs : ""}><span className="bot-rank">{i + 1}</span><b>{r.label}</b>{r.test ? <span className="bot-chip">test</span> : null}</td>
                  <td>{r.trades}</td>
                  <td>{r.wins} / {r.losses}</td>
                  <td>{r.winRate === null ? "-" : Math.round(r.winRate) + "%"}</td>
                  <td className={"bot-num" + tone(r.avgExit)}>{formatBotPct(r.avgExit)}</td>
                  <td className={"bot-num" + tone(r.dollars)}>{formatBotDollars(r.dollars)}</td>
                  <td className={"bot-num" + tone(r.roi)} title={"$" + Math.round(r.invested) + " of contracts"}>{formatBotPct(r.roi, 1)}</td>
                  <td className={"bot-num" + tone(r.profitFactor === null ? null : r.profitFactor - 1)}>{r.profitFactor === null ? "-" : r.profitFactor === Infinity ? "∞" : r.profitFactor.toFixed(2)}</td>
                  <td className={"bot-num" + tone(r.avgWin)}>{formatBotPct(r.avgWin)}</td>
                  <td className={"bot-num" + tone(r.avgLoss)}>{formatBotPct(r.avgLoss)}</td>
                  <td className={"bot-num" + tone(r.best)}>{formatBotPct(r.best)}</td>
                  <td className={"bot-num" + tone(r.worst)}>{formatBotPct(r.worst)}</td>
                  <td>{r.daysTraded ? r.daysUp + " / " + r.daysTraded : "-"}</td>
                  <td>{r.perDay === null ? "-" : r.perDay.toFixed(1)}</td>
                  <td className={"bot-num" + tone(r.avgEntryStrike)}>{r.entryStrikeCount ? formatBotPct(r.avgEntryStrike) : "-"}</td>
                  <td className={"bot-num" + tone(r.avgZero)}>{r.zeroCount ? formatBotPct(r.avgZero) : "-"}</td>
                  <td className={"bot-num" + tone(r.avgMax)}>{formatBotPct(r.avgMax)}</td>
                  <td className={"bot-num" + tone(r.avgClose)}>{formatBotPct(r.avgClose)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "periods" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Winners by period</h3>
          <span className="bot-hint">Each cell: wins - losses · average % at your exit · ROI (profit ÷ contract cost). Highlighted = the best rule in that period. Periods count trading days with bot data.</span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table bot-matrix">
            <thead>
              <tr>
                <th className="is-left">Rule</th>
                {matrix.periods.map((p, i) => (
                  <SortTh key={p.key} k={"c" + i} sort={periodSort} onSort={cycle(setPeriodSort)}
                    title={p.days + (p.days === 1 ? " trading day" : " trading days") + " - click to sort rules by ROI"}>
                    {p.key === "All" ? "All (" + p.days + "d)" : p.key}
                  </SortTh>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix.rows.length === 0 ? (
                <tr><td colSpan={matrix.periods.length + 1} className="bot-empty">No history yet.</td></tr>
              ) : sortBotRows(matrix.rows, periodSort, Object.fromEntries(matrix.periods.map((_, i) => ["c" + i, cellSort(i)]))).map((row) => (
                <tr key={row.rule} className="is-click" onClick={() => { setRule(row.rule); setTab("trades"); }}>
                  <td className="is-left"><b>{row.label}</b>{row.test ? <span className="bot-chip">test</span> : null}</td>
                  {row.cells.map((c, i) => (
                    <td key={matrix.periods[i].key} className={"bot-cell" + (c && matrix.periods[i].best === row.rule ? " is-best" : "")}>
                      {c && c.priced ? (
                        <span>
                          <span className="bot-wl">{c.wins}-{c.losses}</span>
                          <span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span>
                          <span className={"bot-roi" + tone(c.roi)}>ROI {formatBotPct(c.roi, 0)}</span>
                        </span>
                      ) : c ? <span className="bot-dim">{c.trades} open</span> : <span className="bot-dim">-</span>}
                    </td>
                  ))}
                </tr>
              ))}
              {matrix.rows.length ? (
                <tr className="bot-total-row">
                  <td className="is-left"><b>All rules</b></td>
                  {matrix.periods.map((p) => (
                    <td key={p.key} className="bot-cell">
                      <span className="bot-wl">{p.total.wins}-{p.total.losses}</span>
                      <span className={"bot-num" + tone(p.total.avgExit)}> {formatBotPct(p.total.avgExit)}</span>
                      <div className={"bot-num" + tone(p.total.dollars)}>{formatBotDollars(p.total.dollars)}</div>
                      <div className={"bot-roi" + tone(p.total.roi)}>ROI {formatBotPct(p.total.roi, 1)}</div>
                    </td>
                  ))}
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "momentum" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Momentum at entry</h3>
          <span className="bot-hint">
            The 5-minute momentum when the bot entered, for the range above. Each cell: wins - losses · average % at your exit.
            {momentum.unknown ? " " + momentum.unknown + " older trades have no state recorded (before 09-28 16:10)." : ""}
          </span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table bot-matrix">
            <thead>
              <tr>
                <th className="is-left">Rule</th>
                {momentum.states.map((s, i) => (
                  <SortTh key={s} k={"c" + i} sort={momSort} onSort={cycle(setMomSort)} title="Click to sort rules by ROI in this state">
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </SortTh>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortBotRows(momentum.rows, momSort, Object.fromEntries(momentum.states.map((_, i) => ["c" + i, cellSort(i)]))).map((row) => {
                const priced = row.cells.map((c) => (c && c.priced ? c.avgExit : null));
                const bestIdx = priced.reduce((bi, v, i) => (v !== null && (bi < 0 || v > priced[bi]) ? i : bi), -1);
                return (
                  <tr key={row.rule}>
                    <td className="is-left"><b>{row.label}</b>{row.test ? <span className="bot-chip">test</span> : null}</td>
                    {row.cells.map((c, i) => (
                      <td key={momentum.states[i]} className={"bot-cell" + (i === bestIdx && row.cells.filter((x) => x && x.priced).length > 1 ? " is-best" : "")}>
                        {c && c.priced ? (
                          <span><span className="bot-wl">{c.wins}-{c.losses}</span><span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span></span>
                        ) : c ? <span className="bot-dim">{c.trades} open</span> : <span className="bot-dim">-</span>}
                      </td>
                    ))}
                  </tr>
                );
              })}
              {momentum.rows.length ? (
                <tr className="bot-total-row">
                  <td className="is-left"><b>All rules</b></td>
                  {momentum.totals.map((c, i) => (
                    <td key={momentum.states[i]} className="bot-cell">
                      {c && c.priced ? (
                        <span><span className="bot-wl">{c.wins}-{c.losses}</span><span className={"bot-num" + tone(c.avgExit)}> {formatBotPct(c.avgExit)}</span></span>
                      ) : <span className="bot-dim">-</span>}
                    </td>
                  ))}
                </tr>
              ) : (
                <tr><td colSpan={6} className="bot-empty">No trades in this range yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "days" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Day by day</h3>
          <span className="bot-hint">Every trading day the bot ran (kept 180 days).</span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="day" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Day</SortTh>
                <SortTh k="trades" sort={daySort} onSort={cycle(setDaySort)}>Trades</SortTh>
                <SortTh k="wins" sort={daySort} onSort={cycle(setDaySort)}>W / L</SortTh>
                <SortTh k="winRate" sort={daySort} onSort={cycle(setDaySort)}>Win %</SortTh>
                <SortTh k="avg" sort={daySort} onSort={cycle(setDaySort)}>Avg</SortTh>
                <SortTh k="dollars" sort={daySort} onSort={cycle(setDaySort)}>$ P/L</SortTh>
                <SortTh k="roi" sort={daySort} onSort={cycle(setDaySort)}>ROI</SortTh>
                <SortTh k="best" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Best rule</SortTh>
                <SortTh k="worst" sort={daySort} onSort={cycle(setDaySort)} className="is-left">Worst rule</SortTh>
              </tr>
            </thead>
            <tbody>
              {dailyLog.length === 0 ? (
                <tr><td colSpan={9} className="bot-empty">No history yet.</td></tr>
              ) : sortBotRows(dailyLog, daySort, DAY_SORT).map((d) => (
                <tr key={d.day}>
                  <td className="is-left bot-mono">{d.day}</td>
                  <td>{d.trades}</td>
                  <td>{d.wins} / {d.losses}</td>
                  <td>{d.winRate === null ? "-" : Math.round(d.winRate) + "%"}</td>
                  <td className={"bot-num" + tone(d.avgExit)}>{formatBotPct(d.avgExit)}</td>
                  <td className={"bot-num" + tone(d.dollars)}>{formatBotDollars(d.dollars)}</td>
                  <td className={"bot-num" + tone(d.roi)}>{formatBotPct(d.roi, 1)}</td>
                  <td className="is-left">{d.best ? d.best.label + " " + formatBotPct(d.best.avgExit) : "-"}</td>
                  <td className="is-left">{d.worst ? d.worst.label + " " + formatBotPct(d.worst.avgExit) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "trades" ? (
      <section className="bot-card">
        <div className="bot-card-head">
          <h3>Trades{ruleLabel ? " · " + ruleLabel : ""}</h3>
          {rule ? (
            <button type="button" className="bot-clear" onClick={() => setRule(null)}>
              <X size={12} aria-hidden="true" /> all rules
            </button>
          ) : null}
          <input
            className="bot-search"
            placeholder="Ticker"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Filter by ticker"
          />
          <span className="bot-hint">
            {rows.length} shown
            {shownTotal.priced ? (
              <>
                {" · "}{shownTotal.wins}W {shownTotal.losses}L{" · "}
                <b className={"bot-num" + tone(shownTotal.dollars)}>{formatBotDollars(shownTotal.dollars)}</b>
                {" on $" + Math.round(shownTotal.invested).toLocaleString("en-US") + " · ROI "}
                <b className={"bot-num" + tone(shownTotal.roi)}>{formatBotPct(shownTotal.roi, 1)}</b>
              </>
            ) : null}
          </span>
        </div>
        <div className="bot-scroll">
          <table className="bot-table">
            <thead>
              <tr>
                <SortTh k="at" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left" title="When the rule fired (ET)">Signal</SortTh>
                <SortTh k="rule" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Rule</SortTh>
                <SortTh k="symbol" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Ticker</SortTh>
                <SortTh k="strike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Option</SortTh>
                <SortTh k="entry" sort={tradeSort} onSort={cycle(setTradeSort)}>Entry</SortTh>
                <SortTh k="entryAt" sort={tradeSort} onSort={cycle(setTradeSort)} title="When the option was bought (ET) - the first price after the signal">Entry time</SortTh>
                <SortTh k="now" sort={tradeSort} onSort={cycle(setTradeSort)}>Exit / now</SortTh>
                <SortTh k="exitAt" sort={tradeSort} onSort={cycle(setTradeSort)} title="When your exit hit (ET): first 5m close below VWAP / EMA21, else 15:50">Exit time</SortTh>
                <SortTh k="roi" sort={tradeSort} onSort={cycle(setTradeSort)} title="Profit ÷ what the contract cost, at your exit (first 5m close below VWAP / EMA21, else 15:50). Click to sort.">ROI (your exit)</SortTh>
                <SortTh k="dollars" sort={tradeSort} onSort={cycle(setTradeSort)}>$</SortTh>
                <SortTh k="why" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Why out</SortTh>
                <SortTh k="eStrike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">ENTRY</SortTh>
                <SortTh k="ePct" sort={tradeSort} onSort={cycle(setTradeSort)}>ENTRY %</SortTh>
                <SortTh k="zStrike" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">0DTE</SortTh>
                <SortTh k="zPct" sort={tradeSort} onSort={cycle(setTradeSort)}>0DTE %</SortTh>
                <SortTh k="max" sort={tradeSort} onSort={cycle(setTradeSort)}>Hold max</SortTh>
                <SortTh k="status" sort={tradeSort} onSort={cycle(setTradeSort)} className="is-left">Status</SortTh>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr><td colSpan={17} className="bot-empty">Nothing here yet.</td></tr>
              ) : sortBotRows(rows, tradeSort, TRADE_SORT).map((t) => {
                const out = t.exitPrice ?? t.last;
                return (
                  <tr key={t.day + "|" + t.rule + "|" + t.symbol}>
                    <td className="is-left bot-mono">{days > 1 ? t.day.slice(5) + " " : ""}{String(t.at || "").slice(11, 16)}</td>
                    <td className="is-left">{t.label}{t.test ? <span className="bot-chip">test</span> : null}</td>
                    <td className="is-left"><b>{t.symbol}</b></td>
                    <td className="is-left bot-mono">{botContractLabel(t)}</td>
                    <td className="bot-mono">{t.entry === undefined ? "-" : Number(t.entry).toFixed(2)}</td>
                    <td className="bot-mono">{botClock(t.entryAt || t.at)}</td>
                    <td className="bot-mono">{out === undefined || out === null ? "-" : Number(out).toFixed(2)}</td>
                    <td className="bot-mono">{t.exitAt ? botClock(t.exitAt) : <span className="bot-dim">{t.status === "open" ? "holding" : "-"}</span>}</td>
                    <td className={"bot-num" + tone(t.results.exit)}>{formatBotPct(t.results.exit)}</td>
                    <td className={"bot-num" + tone(t.results.exitDollars)}>{formatBotDollars(t.results.exitDollars)}</td>
                    <td className="is-left bot-dim">{t.exitReason || (t.status === "open" ? "holding" : "")}</td>
                    <td className="is-left bot-mono bot-dim">{botEntryStrikeLabel(t)}</td>
                    <td className={"bot-num" + tone(t.results.entryStrike)}>{formatBotPct(t.results.entryStrike)}</td>
                    <td className="is-left bot-mono bot-dim">{botContractLabel(t, true)}</td>
                    <td className={"bot-num" + tone(t.results.zero)}>{formatBotPct(t.results.zero)}</td>
                    <td className={"bot-num" + tone(t.results.max)}>{formatBotPct(t.results.max)}{!t.holdDone && t.entry !== undefined ? "…" : ""}</td>
                    <td className="is-left"><span className={"bot-status is-" + statusOf(t).replace(" ", "-")}>{statusOf(t)}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
      ) : null}

      {tab === "rules" ? (
      <section className="bot-card bot-help">
        <div className="bot-card-head"><h3>Rules explained</h3><span className="bot-hint">what each rule needs - so you can see what a missed trade lacked</span></div>
        <div className="bot-help-list">
          {BOT_RULE_ORDER.map((rule) => {
            const h = BOT_RULE_HELP[rule];
            if (!h) return null;
            return (
              <div key={rule} className="bot-help-item">
                <div className="bot-help-name"><b>{BOT_RULE_LABELS[rule]}</b>{BOT_TEST_RULES.has(rule) ? <span className="bot-chip">test</span> : null}</div>
                <div>{h.what}</div>
                <div className="bot-dim"><b>Needs:</b> {h.needs}</div>
                <div className="bot-dim"><b>Back-test:</b> {h.tested}</div>
              </div>
            );
          })}
        </div>
      </section>
      ) : null}

      <p className="bot-foot">
        <b>How it trades:</b> at each GO+, GO, ⭐1, MX A+, Daily 2 and Market-turn signal (and, as a <i>test</i>, the first 15 a day of
        C2H/C4H, CALL2H/4H, ADX, SKIT, RVOL, SQZ fire and your ZS rule - above EMA 9/21/50, VWAP and the 5m cloud with no level within 2% above) it takes the nearest weekly first out-of-the-money option at the mid price, 09:30-15:30 ET.
        <b> Your exit</b> = the first 5-minute close below VWAP or the 21 EMA, else 15:50. <b>ENTRY strike</b> = the chain's ENTRY strike (the out-of-the-money option with delta just under 0.20, same expiry, same exit). <b>0DTE</b> = the same signal on a same-day option.
        <b> Hold max</b> = the weekly's best price until it expires (… = still being tracked). Mid prices - real fills are a little worse.
        Paper tracking of your rules, not advice. Kept {data && data.keepDays ? data.keepDays : 180} days.
      </p>
    </div>
  );
}
