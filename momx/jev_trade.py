"""TypeSafe Jev's take/skip on each BOT trade - recorded, never traded on (2026-10-02 TEST).

His ask: "with Jev AI or without Jev AI?". The back-test (scripts/jev_bot_backtest.py,
Sep 28 - Oct 2, 4,634 BOT trades) found, on calls, Jev's P(take) >= 0.30 picks won 63%
vs 55% for all trades (one per stock-day), same +29% average at his exit, 54% vs 41%
held to the close; on puts it said yes 10 times - no verdict. Not proven, so this
runs as a SHADOW: the BOT still takes every trade; each trade just carries
``jevP`` (Jev's probability for "take"), and the BOT page shows a "✓ Jev" line per
rule (jevP >= JEV_CUTOFF) next to the rule itself. Decision in ~3 weeks.

Design (aabrole/claude-trading-desk core/decision.py, the trading-desk-jev skill):
the rule finds the trade, Jev never flips a side; gate on the PROBABILITY, never on
``confidence`` (it ranked trades the WRONG way here too: AUC 0.43); factor questions
are recorded for later analysis only. The describe()/questions() text is the SAME
the back-test used, so live and back-test numbers are comparable.

Never blocks the BOT: one daemon thread, one call per trade (~0.2 s, ~$0.00004),
MAX_PER_DAY flood guard, no key (TYPESAFE_API_KEY) or AGX_JEV_BOT=0 -> nothing sent.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
JEV_CUTOFF = 0.30          # fixed before the back-test from the repo's advice ("start near 0.30")
MAX_PER_DAY = 4000

RULE_NAMES = {
    "mom": "MOM (momentum getting stronger)", "momStrong": "MOM strong (momentum + 5m/30m ADX agree)", "momFlip": "MOM FLIP (5m ADX flip + momentum turning)", "call2h": "CALL2H/CALL4H chart arrow", "rvol": "RVOL push",
    "skit": "Skittles cross", "c2h": "C2H/C4H chart arrow", "zs2": "ZS setup (EMA 9/21)", "zs": "ZS setup",
    "sectorMove": "sector move", "sqz": "squeeze fired", "adx": "ADX trend", "dayReclaim": "daily-line reclaim",
    "openPath": "opening path", "dayLine": "daily line", "dayLine100": "daily line $100+", "sectorHot": "hot sector",
    "star1": "Star #1", "solo": "SOLO big money", "turn": "market turn", "mxaplus": "MX A+", "hotLead": "hot-sector leader",
    "go": "GO (gap and go)", "goRvolMacd": "GO + RVOL + MACD", "daily2": "Daily 2",
    "explosive": "explosive volume burst", "steady": "steady climb",
    "scanner": "scanner match (the stock is on the momentum scanner; no specific bot rule has fired)",
    "jev": "Jev pick (Jev scored this scanner ticker as a take)",
}


def _m(v: Any) -> Mapping:
    return v if isinstance(v, Mapping) else {}


def _cell(rvol: Mapping, tf: str) -> str:
    c = _m(rvol.get(tf))
    if c.get("value") is None:
        return f"{tf} n/a"
    lit = c.get("bg") not in (None, "black") or c.get("fg") in ("cyan", "magenta")
    return f"{tf} {c['value']}{' (lit)' if lit else ''}"


#: v2 (2026-10-02, his ask "my rules are defined - Jev see how it works"): Jev also
#: gets the rule's own definition (momx/rule_help.json = the BOT page's "Rules
#: explained", extracted from frontend/src/momxBot.js BOT_RULE_HELP - keep in sync),
#: what our RVOL number means (it is a score, can be negative - Jev read RVOL 6.0
#: "lit" as weak pressure on TSLA 10-02), and the full headline with Jev's own
#: news label (the cut-off TSLA line led with the production MISS and hid the
#: deliveries BEAT). v1 = the back-tested text; every trade stores its jevV so v2
#: is judged only on days it ran live.
DESCRIBE_VERSION = 6
#: v6 (2026-10-03, his "fewer losses - exp move 2x and ROI at least 100%", "don't skip it, just
#: trade, Jev will improve"): + the trade's own risk/reward - what the option bought is worth
#: if the stock reaches 2x the expected move, and whether that clears his +100% rule. The bot
#: still takes every trade; Jev weighs it.
#: v5 (2026-10-02, "add OI walls, pivots and PUT arrows"): + chart levels line (pivots from
#: yesterday's H/L/C, premarket high, the BOT trade's High-OI walls + expected move), the
#: other side's chart arrows, EMA ribbons 5m/30m, last MACD cross, stochastic.
_ARROWS: dict = {}


def register_arrows(side: str, fn) -> None:
    """service.py hands in each side's ChartSignalBook.signals_for."""
    _ARROWS[side] = fn


def _other_side_arrows(symbol: str, side: str, day: str) -> list[str]:
    fn = _ARROWS.get("bear" if side == "bull" else "bull")
    if fn is None:
        return []
    try:
        sigs = fn(symbol) or []
    except Exception:  # noqa: BLE001
        return []
    return sorted({str(_m(c).get("label")) for c in sigs if str(_m(c).get("seenAt") or _m(c).get("at") or "")[:10] == day
                   and not _m(c).get("goneAt") and _m(c).get("label")})


def levels_line(trade: Mapping, row: Mapping, side: str, day: str) -> str:
    """Pivots, premarket high, OI walls (bot trades), EMA ribbons, MACD cross, stochastic, opposite arrows."""
    m5 = _m(row.get("m5"))
    pil = _m(m5.get("pillars"))
    dl = _m(m5.get("dayLine"))

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    h, l, c = num(pil.get("prevHigh") or dl.get("prevHigh")), num(dl.get("prevLow")), num(row.get("prevClose"))
    parts = []
    if h and l and c:
        p = (h + l + c) / 3
        parts.append(f"daily pivots P {p:.2f}, R1 {2 * p - l:.2f}, R2 {p + (h - l):.2f}, S1 {2 * p - h:.2f}, S2 {p - (h - l):.2f}")
    if num(pil.get("premarketHigh")):
        parts.append(f"premarket high {num(pil.get('premarketHigh')):.2f}")
    cw, pw = trade.get("callWalls") or [], trade.get("putWalls") or []
    if cw or pw:
        fmt = lambda ws: ", ".join(f"{_m(w).get('strike')} ({_m(w).get('oi')} OI)" for w in ws[:3])  # noqa: E731
        parts.append("High-OI walls from the option chain: calls " + (fmt(cw) or "none")
                     + "; puts " + (fmt(pw) or "none"))
    if num(trade.get("expectedMove")):
        parts.append(f"1-day expected move +/-{num(trade.get('expectedMove')):.2f}")
    if num(trade.get("roi2em")) is not None and num(trade.get("em2Edge")):
        roi = num(trade.get("roi2em")) * 100
        parts.append(f"risk/reward: the option bought ({trade.get('contract')}, {num(trade.get('entry')):.2f}) would return "
                     f"{roi:+.0f}% if the stock reaches 2x the expected move ({num(trade.get('em2Edge')):.2f}) - "
                     + ("meets" if trade.get("riskOk") else "FAILS") + " the trader's rule of at least +100% at 2x the expected move")
    for tf in ("5m", "30m"):
        rb = _m(pil.get("ribbon" + tf))
        if rb.get("trend"):
            parts.append(f"EMA 9/21/50 ribbon {tf}: {rb.get('trend')}")
    up_at, dn_at = num(pil.get("macdCrossUpAt")), num(pil.get("macdCrossDownAt"))
    if up_at or dn_at:
        last_up = (up_at or 0) > (dn_at or 0)
        when = datetime.fromtimestamp(up_at if last_up else dn_at).strftime("%H:%M")
        parts.append(f"last 5m MACD cross: {'UP' if last_up else 'DOWN'} at {when}")
    st = _m(pil.get("stoch"))
    if num(st.get("k")) is not None:
        parts.append(f"stochastic {num(st.get('k')):.0f}/{num(st.get('d')) or 0:.0f}")
    other = _other_side_arrows(str(trade.get("symbol") or row.get("symbol") or ""), side, day)
    parts.append(("opposite-side chart arrows today (" + ("PUT" if side == "bull" else "CALL") + " side): "
                  + (", ".join(other) if other else "none")))
    return "Chart levels and signals: " + "; ".join(parts) + "."
#: v4 (2026-10-02): + his 4-check quality list (checklist_line).
#: v3 (2026-10-02, "Jev look the setup column and analyse all the columns"): also the
#: Setup column's tags that fired TODAY (setup_tags) - the trader's own signals.
_HERE = Path(__file__).resolve().parent
_RULE_HELP: dict | None = None
_NEWS_CACHE: dict = {"at": 0.0, "items": []}


def _rule_help(rule: str) -> Mapping:
    global _RULE_HELP
    if _RULE_HELP is None:
        try:
            _RULE_HELP = json.loads((_HERE / "rule_help.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _RULE_HELP = {}
    return _m(_RULE_HELP.get(rule))


def _news_label(symbol: str, headline: str) -> str | None:
    """Jev's label for this headline from the news reader's cache (news_jev.py), if judged."""
    now = time.time()
    if now - _NEWS_CACHE["at"] > 120:
        try:
            doc = json.loads((_HERE.parent / "artifacts" / "momx_jev_news.json").read_text(encoding="utf-8"))
            _NEWS_CACHE["items"] = [v for v in _m(doc.get("items")).values() if isinstance(v, Mapping)]
        except (OSError, ValueError):
            _NEWS_CACHE["items"] = []
        _NEWS_CACHE["at"] = now
    head = " ".join(str(headline or "").split())[:120].lower()
    for v in _NEWS_CACHE["items"]:
        if str(v.get("ticker") or "").upper() == symbol.upper() and " ".join(str(v.get("headline") or "").split())[:120].lower() == head:
            return {"bullish": "Bullish", "bearish": "Bearish", "not_important": "Not important"}.get(v.get("label"))
    return None


def describe(trade: Mapping, row: Mapping, spy: Mapping | None, side: str, day: str,
             version: int = DESCRIBE_VERSION) -> str:
    """The moment in words. version=1 is the back-tested text, unchanged."""
    if version >= 2:
        return _describe_v2(trade, row, spy, side, day)
    return _describe_v1(trade, row, spy, side, day)


_DAYLINE_TAGS = {"line": "LINE (daily line)", "line100": "$LINE (daily line $100)", "zs": "ZS", "abvx": "ABVX",
                 "reclaim": "RECLAIM (gap reclaim)"}


def setup_tags(row: Mapping, side: str, day: str) -> list[str]:
    """The Setup column's signals that fired today, in the scanner's own words."""
    up = side == "bull"
    arrow = "↑" if up else "↓"

    def today(iso: Any) -> bool:
        return str(iso or "")[:10] == day

    tags = []
    grade = _m(row.get("grade")).get("letter")
    if grade:
        tags.append(f"grade {grade}")
    strat = _m(row.get("strategy"))
    for key, name in (("v2", "V2"), ("v3", "V3")):
        if strat.get(key):
            tags.append(name)
    if strat.get("daily2"):
        tags.append("Daily 2")
    m5 = _m(row.get("m5"))
    go_at = _m(m5.get("gapGo")).get("goAt")
    if go_at:
        try:
            if datetime.fromtimestamp(float(go_at)).date().isoformat() == day:
                tags.append("GO (gap and go)")
        except (TypeError, ValueError, OSError):
            pass
    if today(_m(row.get("momoxAPlus")).get("at")):
        tags.append("MX A+")
    if today(_m(row.get("marketTurn")).get("at")):
        tags.append(f"Market turn #{_m(row.get('marketTurn')).get('rank')}")
    if row.get("solo"):
        tags.append("SOLO (big money)")
    if row.get("hotLeader"):
        tags.append("hot-sector leader")
    for key, label in _DAYLINE_TAGS.items():
        if today(_m(row.get("dayLines")).get(key)):
            tags.append(label + arrow)
    arrows = sorted({str(_m(c).get("label")) for c in (row.get("chartSignals") or [])
                     if isinstance(c, Mapping) and today(c.get("seenAt") or c.get("at")) and not c.get("goneAt")})
    tags.extend(f"{a} chart arrow" for a in arrows if a and a != "None")
    color = _m(m5.get("sqzMom")).get("color")
    if (up and color == "cyan") or (not up and color == "magenta"):
        tags.append("MOM" + arrow + " (momentum getting stronger)")
        if _adx_strong(row, not up):
            tags.append("MOM" + arrow + " strong (5m and 30m ADX agree: trend strong across timeframes)")
    return tags


def _adx_strong(row: Mapping, bear: bool) -> bool:
    """option_track._adx_background_agrees (kept local: no import cycle)."""
    for tf in ("5m", "30m"):
        c = _m(_m(row.get("adx")).get(tf))
        try:
            mine, theirs = float(c.get("minus" if bear else "plus")), float(c.get("plus" if bear else "minus"))
        except (TypeError, ValueError):
            return False
        if not (mine > 25 and mine > theirs):
            return False
    return True


def rvol_lag_minutes(row: Mapping, at_iso: Any) -> float | None:
    """How old the row's 5m RVOL bar is at ``at_iso`` (minutes after that bar CLOSED).

    2026-10-02 APLD: the 09:44 scanner row still carried 09:05-09:10 volume bars and
    an old price; the real move (RVOL 15m 4.9, +7%) reached the row at 09:47:30.
    """
    bar = _m(_m(row.get("rvol")).get("5m")).get("barAt")
    try:
        at = datetime.fromisoformat(str(at_iso)).timestamp()
        return (at - (float(bar) + 300)) / 60.0 if bar else None
    except (TypeError, ValueError):
        return None


RVOL_STALE_MINUTES = 15


#: His 4-check quality list (2026-10-02, "don't trust only setup column"):
#:   news | RVOL >= 5 on 5m/15m or >= 3 on 30m/1h/2h/4h/D | squeeze firing 2h/4h/D/W |
#:   EMA 4/8, 9/20 + MACD cyan/green (= the Skittles blocks) on 2h-M.
#: Fresh History Sep 2 - Oct 2: all 4 met -> 14.6% reached +2% first BUT 15.2% fell 2%
#: first (big moves both ways); RVOL is the check that matters most. Shown to Jev, not traded on.
RVOL_HI_TFS, RVOL_LO_TFS = ("5m", "15m"), ("30m", "1h", "2h", "4h", "D")
SKIT_TFS = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")


def checklist(row: Mapping, side: str, at_iso: Any = None) -> dict:
    up = side == "bull"
    rv, sq, sk = _m(row.get("rvol")), _m(row.get("sqz")), _m(row.get("skittles"))

    def val(tf):
        try:
            return float(_m(rv.get(tf)).get("value"))
        except (TypeError, ValueError):
            return None
    rvol_hits = [f"{tf} {val(tf)}" for tf in RVOL_HI_TFS if (val(tf) or -9) >= 5] + \
                [f"{tf} {val(tf)}" for tf in RVOL_LO_TFS if (val(tf) or -9) >= 3]
    sqz_hits = [tf for tf in ("2h", "4h", "D", "Wk") if _m(sq.get(tf)).get("bg") in ("cyan", "green")]   # fired
    good = ("cyan", "green", "lime", "dark_green") if up else ("magenta", "red", "light_red", "plum")
    skit_hits = [tf for tf in SKIT_TFS if _m(sk.get(tf)).get("bg") in good
                 or (_m(sk.get(tf)).get("bg") in (None, "black") and _m(sk.get(tf)).get("fg") in good)]
    news = _m(row.get("news"))
    age = None
    try:
        if news.get("at") and at_iso:
            age = (datetime.fromisoformat(str(at_iso)).timestamp()
                   - datetime.fromisoformat(str(news["at"]).replace("Z", "+00:00")).timestamp()) / 3600
    except (TypeError, ValueError):
        age = None
    return {"news": bool(news.get("headline")) and (age is None or age <= 24), "newsAgeH": age,
            "rvol": rvol_hits, "sqz": sqz_hits, "skit": skit_hits}


def checklist_line(row: Mapping, side: str, at_iso: Any = None) -> str:
    c = checklist(row, side, at_iso)
    mark = lambda ok: "MET" if ok else "not met"  # noqa: E731
    colour = "cyan/green" if side == "bull" else "magenta/red"
    met = sum([c["news"], bool(c["rvol"]), bool(c["sqz"]), len(c["skit"]) >= 6])
    return ("The trader's own 4-check quality list (" + str(met) + " of 4 met): "
            f"1) fresh news: {mark(c['news'])}"
            + (f" ({c['newsAgeH']:.1f}h old)" if c["news"] and c["newsAgeH"] is not None else "")
            + f"; 2) RVOL >= 5 on 5m/15m or >= 3 on 30m/1h/2h/4h/D: {mark(bool(c['rvol']))}"
            + (f" ({', '.join(c['rvol'])})" if c["rvol"] else "")
            + f"; 3) squeeze firing on 2h/4h/D/W: {mark(bool(c['sqz']))}" + (f" ({', '.join(c['sqz'])})" if c["sqz"] else "")
            + f"; 4) EMA 4/8, 9/20 and MACD {colour} (Skittles) on 2h-M: {len(c['skit'])} of 8"
            + (f" ({', '.join(c['skit'])})" if c["skit"] else "") + ".")


def _describe_v2(trade: Mapping, row: Mapping, spy: Mapping | None, side: str, day: str) -> str:
    base = _describe_v1(trade, row, spy, side, day).split("\n")
    tags = setup_tags(row, side, day)
    tag_line = ("Setup tags the scanner shows for this stock today (the trader's own signals): "
                + (", ".join(tags) if tags else "none")) + "\n" + checklist_line(row, side, trade.get("at"))
    help_ = _rule_help(str(trade.get("rule")))
    rule_line = ""
    if help_:
        rule_line = (f"What this rule means (the trader's own definition): {help_.get('what')} It needs: {help_.get('needs')}"
                     + (" This is the PUT side of the rule: read every condition the other way round (below instead of "
                        "above, selling instead of buying, magenta/red instead of cyan/green)." if side == "bear" else ""))
    rvol_line = ("How to read RVOL: it is a relative-volume SCORE for the current bar versus normal volume at this time of "
                 "day, not a multiple. Above 1 = more volume than usual, 3 or more = heavy, negative = less than usual; "
                 "'(lit)' = the scanner painted it as unusually strong volume.")
    news = _m(row.get("news"))
    out = []
    for line in base:
        if line.startswith("Latest headline (") and news.get("headline"):
            label = _news_label(str(trade.get("symbol") or ""), str(news["headline"]))
            line = (f"Latest headline ({news.get('source')}, published {str(news.get('at'))[:16]} UTC): "
                    f"{' '.join(str(news['headline']).split())[:400]}"
                    + (f" [news reader's verdict on this headline: {label}]" if label else ""))
        out.append(line)
        if line.startswith("Trade the system wants:"):
            if rule_line:
                out.append(rule_line)
            out.append(tag_line)
        if line.startswith("Today's range so far:"):
            out.append(levels_line(trade, row, side, day))
        if line.startswith("Relative volume (RVOL):"):
            out.append(rvol_line)
            lag = rvol_lag_minutes(row, trade.get("at"))
            if lag is not None and lag > RVOL_STALE_MINUTES:
                out.append(f"WARNING: the scanner had not refreshed this stock's volume yet - these RVOL bars are about "
                           f"{round(lag)} minutes old, so they may miss what is happening now.")
    if not any(x.startswith("Chart levels and signals:") for x in out):     # no "Today's range" line
        at = next((k for k, x in enumerate(out) if x.startswith(("Latest headline (", "No recent headline"))), len(out))
        out.insert(at, levels_line(trade, row, side, day))
    return "\n".join(out)


def _describe_v1(trade: Mapping, row: Mapping, spy: Mapping | None, side: str, day: str) -> str:
    """The moment in words - identical to the back-test's text."""
    t, r = trade, row
    m5 = _m(r.get("m5"))
    price = float(t.get("stockPrice") or r.get("last") or 0)
    word = "CALL (betting the stock goes UP)" if side == "bull" else "PUT (betting the stock goes DOWN)"
    lines = [
        f"Ticker: {t['symbol']} ({r.get('industry') or 'unknown industry'})",
        f"Time: {str(t['at'])[11:16]} ET on {day}",
        f"Trade the system wants: buy a short-dated {word}, triggered by the rule '{RULE_NAMES.get(t['rule'], t['rule'])}'",
        f"Stock price {price:.2f}, {float(r.get('pctChange') or 0):+.2f}% today",
    ]
    vwap, ema21 = m5.get("vwap"), m5.get("ema21")
    if vwap:
        lines.append(f"Price is {(price / vwap - 1) * 100:+.2f}% from VWAP and "
                     f"{((price / ema21 - 1) * 100) if ema21 else 0:+.2f}% from the 5-minute EMA21")
    rv = _m(r.get("rvol"))
    lines.append("Relative volume (RVOL): " + ", ".join(_cell(rv, tf) for tf in ("5m", "15m", "30m", "1h", "2h", "4h", "D")))
    sk = _m(r.get("skittles"))
    if sk:
        lines.append("Skittles trend strength (0-100, cyan/green = bullish, magenta/red = bearish): " + ", ".join(
            f"{tf} {_m(c).get('value')} {_m(c).get('bg') if _m(c).get('bg') not in (None, 'black') else _m(c).get('fg')}"
            for tf, c in sk.items()))
    sq = _m(r.get("sqz"))
    fired = [tf for tf, c in sq.items() if _m(c).get("bg") in ("cyan", "green", "magenta", "red")]
    if fired:
        lines.append("Squeeze fired on: " + ", ".join(fired))
    mom = _m(m5.get("sqzMom"))
    if mom.get("value") is not None:
        lines.append(f"5-minute momentum {mom['value']:+.3f} (previous bar {mom.get('prev') or 0:+.3f})")
    if m5.get("pattern") or m5.get("chart"):
        lines.append(f"5-minute chart: {m5.get('chart') or ''} {m5.get('pattern') or ''}".strip())
    hl = _m(r.get("highLow"))
    if hl.get("hh"):
        lines.append(f"Today's range so far: high {hl['hh']}, low {hl['ll']}")
    dl = _m(m5.get("dayLine"))
    if dl.get("prevHigh"):
        lines.append(f"Yesterday's high {dl['prevHigh']}, low {dl.get('prevLow')}; today's open {dl.get('open')}")
    g = _m(r.get("grade"))
    if g.get("letter"):
        lines.append(f"Scanner grade {g['letter']}")
    sec = _m(r.get("sectorRotation"))
    if sec:
        lines.append(f"Sector: {sec.get('up')} of {sec.get('total')} names up, 3-day relative strength {sec.get('rs3')}")
    if spy:
        sv = _m(spy.get("m5")).get("vwap")
        lines.append(f"Market (SPY): {float(spy.get('pctChange') or 0):+.2f}% today"
                     + (f", {'above' if float(spy.get('last') or 0) >= sv else 'below'} its VWAP" if sv else ""))
    news = _m(r.get("news"))
    if news.get("headline"):
        lines.append(f"Latest headline ({news.get('source')}, published {str(news.get('at'))[:16]} UTC): {str(news['headline'])[:220]}")
    else:
        lines.append("No recent headline for this stock")
    if t.get("contract"):
        lines.append(f"Option: {t.get('contract')}, entry {t.get('entry')}, delta {t.get('delta')}, expiry {t.get('expiry')}")
    else:   # a scanner score (momx/jev_scanner.py): no contract chosen yet
        lines.append(f"Option: the nearest short-dated out-of-the-money {'call' if side == 'bull' else 'put'} (not chosen yet)")
    return "\n".join(lines)


def questions(side: str) -> dict:
    up = side == "bull"
    move = "rise" if up else "fall"
    return {
        "action": {
            "type": "choice",
            "instructions": (
                "A momentum scanner wants to buy the short-dated option described above right now. Many of "
                "these signals fail: the move stalls or reverses within the hour and the option loses value. "
                "Using only the facts above, decide whether to take this trade now or stand aside."),
            "criteria": {
                "take": f"The stock is likely to keep moving to {move} enough over the next hour for this "
                        f"{'call' if up else 'put'} to gain value. Take the trade now.",
                "stand_aside": "The move is likely to stall, reverse, or is already stretched, or the evidence "
                               "is mixed. Do not take the trade.",
            },
        },
        "regime": {"type": "noul", "instructions": f"The overall market today supports stocks that {move}."},
        "direction": {"type": "noul", "instructions": f"The stock's own trend on the intraday charts points to it continuing to {move}."},
        "pressure": {"type": "noul", "instructions": f"Real {'buying' if up else 'selling'} volume is behind the move right now."},
        "quality": {"type": "score", "instructions": "How clean is this setup?",
                    "criteria": ["Messy or late: choppy, extended, or no clear trigger",
                                 "Average", "Clean: clear trigger, fresh move, room to run"]},
        "risk": {"type": "score", "instructions": "How likely is this move to reverse within the hour?",
                 "criteria": ["Unlikely to reverse", "Unclear", "Very likely to reverse"]},
        "news": {"type": "noul", "instructions": f"Recent news about this company supports it to {move} today."},
    }


def parse(resp: Mapping | None) -> dict:
    a = _m(_m(resp).get("answers"))
    act = _m(a.get("action"))
    # The model that ACTUALLY answered (jev-latest resolves to e.g. "jev-1.13.0"): our 10% /
    # 30% cut-offs are calibrated to one model, so every score records which (2026-10-04).
    out = {"jevP": _m(act.get("probabilities")).get("take"), "jevModel": _m(resp).get("model")}
    for q in ("regime", "direction", "pressure", "news"):
        out[q] = _m(a.get(q)).get("noul")
    for q in ("quality", "risk"):
        out[q] = _m(a.get(q)).get("score")
    return out


def _default_post(payload: dict, key: str) -> dict:
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


class JevTradeJudge:
    """Background asker: submit(state, side, done) -> done(result) later. Never raises."""

    def __init__(self, *, key: str | None = None, post: Callable[[dict, str], dict] | None = None,
                 background: bool = True, sleep: Callable[[float], None] = time.sleep) -> None:
        self._key = key if key is not None else (os.getenv("TYPESAFE_API_KEY") or "").strip()
        self.enabled = bool(self._key) and os.getenv("AGX_JEV_BOT", "1") != "0"
        self._post = post or _default_post
        self._background = background
        self._sleep = sleep
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._day, self._calls = "", 0
        self.stats = {"asked": 0, "errors": 0, "lastError": None}

    def submit(self, state: str, side: str, done: Callable[[dict], None]) -> bool:
        if not self.enabled or not state:
            return False
        self._queue.put((state, side, done))
        if not self._background:
            self.drain()
            return True
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="momx-jev-bot", daemon=True)
                self._thread.start()
        return True

    def drain(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            self._ask(*item)

    def _run(self) -> None:
        while True:
            self._ask(*self._queue.get())

    def _ask(self, state: str, side: str, done: Callable[[dict], None]) -> None:
        day = datetime.now().date().isoformat()
        if day != self._day:
            self._day, self._calls = day, 0
        if self._calls >= MAX_PER_DAY:
            return
        payload = {"model": MODEL, "state": state, "questions": questions(side)}
        delay = 1.0
        for _ in range(5):
            try:
                self._calls += 1
                result = parse(self._post(payload, self._key))
                if result.get("jevP") is None:
                    raise ValueError("no take probability")
                result["jevAt"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
                self.stats["asked"] += 1
                try:
                    done(result)
                except Exception:  # noqa: BLE001
                    pass
                return
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 529, 500, 502, 503):
                    self.stats.update(errors=self.stats["errors"] + 1, lastError=f"HTTP {exc.code}")
                    return
            except Exception as exc:  # noqa: BLE001 - resets, timeouts
                self.stats["lastError"] = type(exc).__name__
            self._sleep(delay)
            delay = min(delay * 2, 30.0)
        self.stats.update(errors=self.stats["errors"] + 1)


__all__ = ["JevTradeJudge", "describe", "questions", "parse", "JEV_CUTOFF"]
