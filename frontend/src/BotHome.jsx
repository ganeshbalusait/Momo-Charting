// Bot home page (2026-11-25): one phone-width column that answers, in order,
// "is the bot buying", "what has it made on real paper fills", then Journal,
// Trades, what the review Learned, and the Ready checks. Built because the old
// Bot page hid the one fact that mattered (the paper account was out of money)
// behind 22 cards. Data: GET /api/momx-scanner/bot-desk (momx/bot_desk.py),
// the Scorecard's own read. Read-only: this page sends nothing.
// All words and numbers come from botHome.js so they can be tested without a browser.
import { useEffect, useMemo, useRef, useState } from "react";
import { DESK_ACCOUNT_KEY, DESK_SAVED_KEY, DESK_URL, deskAccountLabel, readSavedDesk } from "./botDesk.js";
import { truthAccountLabel } from "./botTruth.js";
import {
  HOME_ACCOUNTS, HOME_MORE, HOME_PERIODS, HOME_REFRESH_MS, HOME_SECTIONS, HOME_SECTION_KEY,
  homeCalendar, homeCallsPuts, homeChecks, homeClosedTrades, homeDayHead, homeFreshness, homeJournalTiles, homeLessons,
  homeMonths, homeNewestDay, homePeriodRows, homeResult, homeSpark, homeState, homeTiles, homeTopLesson, homeTrades,
  homeVerdict, readHomeAccount, readHomeSection,
} from "./botHome.js";
import "./botHome.css";

const stored = (key) => { try { return window.localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { window.localStorage.setItem(key, value); } catch { /* private window or full */ } };

/** The Scorecard read: the copy saved on this device shows at once, the server's answer replaces it,
 * and a failed refresh keeps the last numbers and says so. */
function useDesk() {
  const [state, setState] = useState(() => {
    const saved = readSavedDesk(stored(DESK_SAVED_KEY));
    return { doc: saved, failed: false, saved: !!saved };
  });
  useEffect(() => {
    let alive = true;
    const load = () => fetch(DESK_URL, { credentials: "include", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("not ok"))))
      .then((doc) => {
        if (!alive) return;
        if (!doc || typeof doc !== "object" || !doc.accounts) throw new Error("no accounts");
        setState({ doc, failed: false, saved: false });
        if (typeof doc.asOf === "string") store(DESK_SAVED_KEY, JSON.stringify(doc));
      })
      .catch(() => { if (alive) setState((s) => ({ doc: s.doc, failed: true, saved: s.saved })); });
    load();
    // Every minute while the page is on screen; a hidden tab or a locked phone does not ask.
    const id = window.setInterval(() => { if (!document.hidden) load(); }, HOME_REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return state;
}

function Tile({ tile }) {
  return (
    <div className="bh-tile" data-tile={tile.key}>
      <span className="bh-tile-label">{tile.label}</span>
      <b className={"bh-num " + tile.tone}>{tile.value}</b>
      {tile.sub ? <span className="bh-tile-sub bh-num">{tile.sub}</span> : null}
    </div>
  );
}

function Spark({ days }) {
  const s = useMemo(() => homeSpark(days), [days]);
  if (!s) return null;
  return (
    <svg className={"bh-spark " + s.tone} viewBox={`0 0 ${s.width} ${s.height}`} preserveAspectRatio="none" role="img" aria-label={s.label}>
      <line className="bh-spark-zero" x1="0" x2={s.width} y1={s.zeroY} y2={s.zeroY} />
      <polyline className="bh-spark-line" points={s.line} />
    </svg>
  );
}

function TradeRows({ rows, openId, onToggle }) {
  return (
    <div className="bh-rows">
      {rows.map((t) => {
        const isOpen = openId === t.id;
        return (
          <div className="bh-row-wrap" key={t.id}>
            <button type="button" className="bh-row" data-testid="bh-trade-row" data-kind={t.kind} aria-expanded={isOpen} onClick={() => onToggle(t.id)}>
              <span className="bh-row-l">
                <span className="bh-row-t">{t.title}</span>
                <span className="bh-row-s">{t.sub}</span>
              </span>
              <span className="bh-row-r bh-num">
                <span className={"bh-row-t " + t.tone}>{t.value}</span>
                {t.roi ? <span className="bh-row-s">{t.roi}</span> : null}
              </span>
            </button>
            {isOpen ? (
              <div className="bh-detail" data-testid="bh-trade-detail">
                <dl className="bh-kv bh-num">
                  {t.detail.map(([label, value]) => (
                    <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
                  ))}
                </dl>
                {t.sentence ? <p className="bh-soft">{t.sentence}</p> : null}
                {t.stopNote ? <p className="bh-note" data-testid="bh-stop-note">{t.stopNote}</p> : null}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function Empty({ title, line }) {
  return (
    <div className="bh-card bh-empty" data-testid="bh-empty">
      <h3>{title}</h3>
      <p className="bh-mute">{line}</p>
    </div>
  );
}

/** The four ways out of this page. The page that mounts BotHome decides what each one opens. */
function More({ onOpen }) {
  return (
    <>
      <h3 className="bh-h3">More</h3>
      <div className="bh-rows" data-testid="bh-more">
        {HOME_MORE.map((m) => (
          <button type="button" className="bh-row" key={m.name} data-testid={"bh-more-" + m.name} onClick={() => onOpen && onOpen(m.name)}>
            <span className="bh-row-l"><span className="bh-row-t">{m.title}</span><span className="bh-row-s">{m.sub}</span></span>
            <span aria-hidden="true" className="bh-chev">›</span>
          </button>
        ))}
      </div>
    </>
  );
}

function Home({ doc, acct, go, onOpen, openId, onToggle }) {
  const state = homeState(doc, acct);
  const result = homeResult(doc, acct);
  const verdict = homeVerdict(acct);
  const lesson = homeTopLesson(doc);
  const recent = useMemo(() => homeClosedTrades(acct).slice(0, 4), [acct]);
  return (
    <>
      <div className={"bh-state is-" + state.kind} data-testid="bh-state" data-state={state.kind} role="status">
        <b>{state.title}</b>
        <span>{state.line}</span>
        <span className="bh-state-foot bh-num">{[state.lastOrder, state.read].filter(Boolean).join(" · ")}</span>
      </div>

      <div className="bh-card bh-hero" data-testid="bh-result">
        <span className="bh-mute">Result on real paper fills</span>
        <span className={"bh-big bh-num " + result.tone}>{result.value}</span>
        <span className="bh-soft bh-num">{result.line}</span>
        {result.empty ? <p className="bh-mute">No trade has closed yet. The line starts with the first one.</p> : <Spark days={acct && acct.days} />}
        {result.days ? <span className="bh-mute bh-num">{result.days}</span> : null}
        {result.few ? <span className="bh-few" data-testid="bh-few">{result.few}</span> : null}
      </div>

      <button type="button" className={"bh-banner is-" + verdict.tone} data-testid="bh-verdict" onClick={() => go("ready")}>
        <span><b>{verdict.title}</b><span className="bh-soft">{verdict.line}</span></span>
        <span aria-hidden="true" className="bh-chev">›</span>
      </button>

      <div className="bh-tiles">{homeTiles(acct).map((t) => <Tile key={t.key} tile={t} />)}</div>

      {lesson ? (
        <button type="button" className="bh-card bh-teaser" data-testid="bh-teaser" onClick={() => go("learned")}>
          <span className="bh-eyebrow">From the review of the losing trades</span>
          <span>{lesson}</span>
          <span className="bh-link">See all findings ›</span>
        </button>
      ) : null}

      <h3 className="bh-h3">Latest trades</h3>
      {recent.length ? <TradeRows rows={recent} openId={openId} onToggle={onToggle} />
        : <Empty title="No closed trades yet" line="The newest trades show here once the paper account sells one." />}
      {recent.length ? <button type="button" className="bh-textbtn" onClick={() => go("trades")}>All trades ›</button> : null}

      <More onOpen={onOpen} />
    </>
  );
}

function Journal({ doc, acct, openId, onToggle }) {
  const months = useMemo(() => homeMonths(acct), [acct]);
  const [monthPick, setMonth] = useState("");
  const [dayPick, setDay] = useState("");
  const [mode, setMode] = useState("day");
  const closed = useMemo(() => homeClosedTrades(acct), [acct]);
  if (!months.length) return <Empty title="No closed trades yet" line="The journal fills in after the first trade closes." />;
  const month = months.includes(monthPick) ? monthPick : months[months.length - 1];
  const day = dayPick && dayPick.startsWith(month) && homeDayHead(acct, dayPick) ? dayPick : homeNewestDay(acct, month);
  const cal = homeCalendar(acct, month, day);
  const result = homeResult(doc, acct);
  const dayTrades = closed.filter((t) => t.day === day);
  const callsPuts = homeCallsPuts(acct);
  const rows = homePeriodRows(acct, mode);
  const modeName = HOME_PERIODS.find(([key]) => key === mode)[1];
  return (
    <>
      <div className="bh-tiles is-six">{homeJournalTiles(doc, acct).map((t) => <Tile key={t.key} tile={t} />)}</div>
      {result.few ? <span className="bh-few" data-testid="bh-few">{result.few}</span> : null}

      <div className="bh-card">
        <div className="bh-cal-head">
          <button type="button" aria-label="Previous month" disabled={!cal.prev} onClick={() => { setMonth(cal.prev); setDay(""); }}>‹</button>
          <h3 data-testid="bh-cal-month">{cal.label}</h3>
          <button type="button" aria-label="Next month" disabled={!cal.next} onClick={() => { setMonth(cal.next); setDay(""); }}>›</button>
        </div>
        <div className="bh-cal" data-testid="bh-cal">
          {["Mon", "Tue", "Wed", "Thu", "Fri"].map((d) => <span className="bh-cal-dow" key={d}>{d}</span>)}
          {cal.cells.map((c, i) => {
            if (!c) return <span key={"blank" + i} />;
            if (!c.has) return <div className="bh-cal-d" key={c.key}>{c.num}</div>;
            return (
              <button type="button" key={c.key} className={"bh-cal-d has " + c.tone + (c.selected ? " is-sel" : "")} data-testid="bh-cal-day" data-day={c.key}
                aria-pressed={c.selected} aria-label={c.label} onClick={() => setDay(c.key)}>
                <span>{c.num}</span>
                <b className="bh-num">{c.value}</b>
              </button>
            );
          })}
        </div>
        {result.days ? <p className="bh-mute bh-num">{result.days}. Tap a day for its trades.</p> : null}
      </div>

      <h3 className="bh-h3 bh-num" data-testid="bh-day-head">{homeDayHead(acct, day)}</h3>
      {dayTrades.length ? <TradeRows rows={dayTrades} openId={openId} onToggle={onToggle} />
        : <p className="bh-mute">The trades of this day are older than the list kept here.</p>}

      <div className="bh-card">
        <div className="bh-seg is-inset" role="group" aria-label="Period">
          {HOME_PERIODS.map(([key, label]) => (
            <button type="button" key={key} aria-pressed={mode === key} onClick={() => setMode(key)}>{label}</button>
          ))}
        </div>
        <div className="bh-tbl bh-num" data-testid="bh-periods">
          <span className="bh-tbl-h">{modeName}</span><span className="bh-tbl-h is-n">Trades</span><span className="bh-tbl-h is-n">Won</span><span className="bh-tbl-h is-n">Result</span>
          {rows.map((p) => [
            <span key={p.key + "l"}>{p.label}</span>,
            <span key={p.key + "t"} className="is-n">{p.trades}</span>,
            <span key={p.key + "w"} className="is-n">{p.won}</span>,
            <b key={p.key + "r"} className={"is-n " + p.tone}>{p.value}</b>,
          ])}
        </div>
      </div>

      {callsPuts ? (
        <div className="bh-card" data-testid="bh-calls-puts">
          <h3>Calls against puts</h3>
          <dl className="bh-kv bh-num">
            {callsPuts.map((r) => <div key={r.key}><dt>{r.label}</dt><dd className={r.tone}><b>{r.value}</b></dd></div>)}
          </dl>
        </div>
      ) : null}
    </>
  );
}

function Trades({ acct, openId, onToggle }) {
  const [filter, setFilter] = useState("all");
  const view = useMemo(() => homeTrades(acct, filter), [acct, filter]);
  return (
    <>
      <div className="bh-chips" role="group" aria-label="Filter trades">
        {view.filters.map((f) => (
          <button type="button" key={f.key} data-testid={"bh-filter-" + f.key} aria-pressed={f.on} onClick={() => { setFilter(f.key); onToggle(null); }}>
            {f.label} <span className="bh-num">{f.count}</span>
          </button>
        ))}
      </div>
      {view.empty ? <Empty title={view.empty.title} line={view.empty.line} /> : null}
      {view.open.length ? (<><h3 className="bh-h3">Open</h3><TradeRows rows={view.open} openId={openId} onToggle={onToggle} /></>) : null}
      {view.missed.length ? (<><h3 className="bh-h3">Not bought</h3><TradeRows rows={view.missed} openId={openId} onToggle={onToggle} /></>) : null}
      {view.groups.map((g) => (
        <div className="bh-group" key={g.day} data-testid="bh-day-group" data-day={g.day}>
          <h3 className="bh-h3 bh-num">{g.label} · <span className={g.tone}>{g.value}</span></h3>
          <TradeRows rows={g.rows} openId={openId} onToggle={onToggle} />
        </div>
      ))}
      {view.capped ? <p className="bh-mute bh-num">{view.capped}</p> : null}
    </>
  );
}

function Learned({ doc }) {
  const view = homeLessons(doc);
  return (
    <>
      <div className="bh-card" data-testid="bh-learned-head">
        <h3>{view.title}</h3>
        <p className="bh-soft bh-num">{view.ran}</p>
        <p className="bh-soft">These are findings only. {view.applied}</p>
      </div>
      {view.ready && view.empty ? <p className="bh-mute">{view.empty}</p> : null}
      {view.rows.map((l) => (
        <div className="bh-card" key={l.key} data-testid="bh-lesson">
          <span className="bh-pill is-grey bh-num">{l.evidence}</span>
          <span>{l.sentence}</span>
          {l.change ? <span className="bh-soft"><b>Suggested change:</b> {l.change}</span> : null}
          <span className={"bh-pill" + (l.collecting ? " is-grey" : "")} data-testid="bh-lesson-status">{l.status}</span>
        </div>
      ))}
    </>
  );
}

function Ready({ doc, acct }) {
  const verdict = homeVerdict(acct);
  const checks = homeChecks(doc, acct);
  return (
    <>
      <div className={"bh-card bh-verdict is-" + verdict.tone} data-testid="bh-ready-verdict">
        <h3>{verdict.title}</h3>
        <p className="bh-soft">{verdict.tone === "empty" ? verdict.line : `${verdict.line}. Every check must pass on real paper fills first.`}</p>
      </div>
      {checks.length ? (
        <div className="bh-rows">
          {checks.map((c) => (
            <div className="bh-check" key={c.key} data-testid="bh-check" data-check={c.key} data-pass={c.pass ? "yes" : "no"}>
              <span className={"bh-mark" + (c.pass ? " is-ok" : "")} role="img" aria-label={c.mark}>{c.pass ? "✓" : "✕"}</span>
              <span className="bh-check-text"><b>{c.title}</b><span className="bh-mute bh-num">{c.figure}</span></span>
            </div>
          ))}
        </div>
      ) : null}
      <p className="bh-mute">Judged on what the paper account really paid and received.</p>
    </>
  );
}

export default function BotHome({ onOpen, initialSection }) {
  const state = useDesk();
  const { doc } = state;
  const rootRef = useRef(null);
  const [section, setSectionState] = useState(() => readHomeSection(initialSection || stored(HOME_SECTION_KEY)));
  const [accountPick, setAccountPick] = useState(() => stored(DESK_ACCOUNT_KEY) || "");
  const [openId, setOpenId] = useState(null);
  const account = readHomeAccount(accountPick, doc);
  const acct = doc && doc.accounts ? doc.accounts[account] || null : null;
  const fresh = homeFreshness(state);
  const toggle = (id) => setOpenId((cur) => (id && cur !== id ? id : null));
  const go = (name) => {
    setSectionState(name);
    setOpenId(null);
    store(HOME_SECTION_KEY, name);
    const el = rootRef.current;
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "start" });
  };
  const pickAccount = (name) => {
    setAccountPick(name);
    setOpenId(null);
    store(DESK_ACCOUNT_KEY, name);
  };
  const names = HOME_ACCOUNTS.filter((name) => !doc || !doc.accounts || doc.accounts[name]);
  const hasAccounts = !!(doc && doc.accounts && HOME_ACCOUNTS.some((name) => doc.accounts[name]));

  return (
    <div className="bh" ref={rootRef} data-testid="bh-root">
      <header className="bh-top">
        <h2>Bot</h2>
        {fresh.text ? <span className={"bh-fresh bh-num is-" + (fresh.tone || "plain")} data-testid="bh-fresh" role="status">{fresh.text}</span> : null}
      </header>

      {hasAccounts && names.length > 1 ? (
        <div className="bh-seg" role="group" aria-label="Which bot">
          {names.map((name) => (
            <button type="button" key={name} data-testid={"bh-account-" + name} aria-pressed={name === account} onClick={() => pickAccount(name)}>
              {deskAccountLabel(name, doc.accounts[name], truthAccountLabel(name))}
            </button>
          ))}
        </div>
      ) : null}

      <nav className="bh-seg bh-sections" role="group" aria-label="Bot sections">
        {HOME_SECTIONS.map(([key, label]) => (
          <button type="button" key={key} data-testid={"bh-tab-" + key} aria-pressed={section === key} onClick={() => go(key)}>{label}</button>
        ))}
      </nav>

      <section className="bh-body" data-testid={"bh-section-" + section} aria-label={HOME_SECTIONS.find(([key]) => key === section)[1]}>
        {!doc ? (
          <Empty title={state.failed ? "Could not load the bot's numbers" : "Loading the bot's numbers…"}
            line={state.failed ? "The app will try again in a minute." : "This takes a few seconds the first time."} />
        ) : !hasAccounts ? (
          <Empty title="No paper trades yet" line="This page fills in after the bot places its first paper order." />
        ) : section === "journal" ? <Journal key={account} doc={doc} acct={acct} openId={openId} onToggle={toggle} />
          : section === "trades" ? <Trades key={account} acct={acct} openId={openId} onToggle={toggle} />
          : section === "learned" ? <Learned doc={doc} />
          : section === "ready" ? <Ready doc={doc} acct={acct} />
          : <Home doc={doc} acct={acct} go={go} onOpen={onOpen} openId={openId} onToggle={toggle} />}
        {section === "home" && (!doc || !hasAccounts) ? (
          <More onOpen={onOpen} />
        ) : null}
      </section>
    </div>
  );
}
