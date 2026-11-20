"""The full report and its parts (2026-11-11, the trader's asks): "NVDA", "full report on NVDA", "NVDA fundamentals",
"NVDA technicals", "should I buy NVDA", "short term TSLA", "TSLA leap", "NVDA analyst".

Everything here is read from what the app already holds: the finished scanner boards, the saved option chain,
the Long Term summary, the chart (the chat's own child process, chart_levels.py), the saved headlines, and the
analyst cache (momx/analyst.py). Every number comes with its time; a missing value is named as missing.

"Should I buy" is NEVER an opinion. It gets "What the app's rules show": each rule's verdict in its own words,
counted ("4 of 6 rule checks point up"), the risks the data shows, and one closing line:
"This is what the app's rules show, not advice." The three views (short, mid, long term) summarise the app's own
signal events inside each window with a stated rule (VERDICT_RULE), labelled "From the app's signals, not advice".

Fast and slow: the first answer uses cached reads only (well under a second; chart numbers only if already worked
out). The rest (chart numbers for every timeframe, Google News and the headline reasons) comes through the one
"more" call (momx/chat_why.py), which returns the whole report again with every section filled.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from momx import chat_facts

ET = chat_facts.ET
ADVICE_LINE = "This is what the app's rules show, not advice."
VIEWS_LABEL = "From the app's signals, not advice."
#: The verdict rule, stated once (code and help line): count the app's own bullish and bearish events inside the
#: view's window. All on one side = strength 4; at least 75% = 3; at least 60% = 2; otherwise 1 ("barely").
#: Even (or no events) = Neutral.
VERDICT_RULE = ("Verdict: the app's own bullish and bearish signal events in the window are counted. All on one side is "
                "strength 4, at least 75% is 3, at least 60% is 2, less is 1; an even count, or none, is Neutral.")
WINDOWS = {"short": ("Short term", "today to about 2 weeks", 14), "mid": ("Mid term", "about 2 weeks to 3 months", 92),
           "long": ("Long term", "months to years", 3 * 365)}
_SHORT_TF = {"5m", "15m", "30m", "1h", "2h", "4h", "15", "30", "1H", "2H", "4H"}
_MID_TF = {"1h", "4h", "D", "1H", "4H", "2D", "3D", "4D"}
_LONG_TF = {"D", "W", "M", "2D", "3D", "4D"}
SECTION_ORDER = ("price", "why", "scanner", "technicals", "options", "fundamentals", "premarket", "earnings", "sentiment", "rules", "bot")
MAX_SECTION_LINES = 24
_BULL_COLORS = {"cyan", "green", "lime", "darkgreen", "dark green", "lightgreen"}
_BEAR_COLORS = {"magenta", "red", "pink", "plum", "lightred", "light red", "darkred"}

_lt_lock = threading.Lock()
_lt_cache: dict = {}


# ---------------------------------------------------------------- small readers
def _num(value: Any) -> float | None:
    return chat_facts._num(value)


def _money(value: Any) -> str:
    number = _num(value)
    return f"{number:,.2f}" if number is not None else "?"


def _pct(value: Any) -> str:
    number = _num(value)
    return f"{number:+.2f}%" if number is not None else "?"


def long_term_row(ticker: str, ctx: Mapping) -> tuple[dict | None, datetime | None]:
    """The ticker's Long Term record from long_term/summary.json (metrics and valuation), re-read when the file changes."""
    path = chat_facts.artifacts_of(ctx) / "long_term" / "summary.json"
    try:
        info = path.stat()
    except OSError:
        return None, None
    stamp = (info.st_mtime_ns, info.st_size)
    with _lt_lock:
        held = _lt_cache.get(str(path))
    if not held or held[0] != stamp:
        try:
            doc = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            return None, None
        rows = doc.get("rows_by_symbol") if isinstance(doc, dict) else None
        held = (stamp, rows if isinstance(rows, dict) else {}, chat_facts.when(doc.get("refreshed_at")) if isinstance(doc, dict) else None)
        with _lt_lock:
            _lt_cache[str(path)] = held
    row = held[1].get(ticker)
    return (row if isinstance(row, dict) else None), held[2]


def long_term_verdict(row: Mapping, price: float | None) -> dict:
    """The Long Term page's own rule (long_term.valuation.signal, margin of safety 50%) on a saved record."""
    metrics, value = row.get("metrics") or {}, row.get("valuation") or {}
    out = {"sticker": _num(value.get("sticker")), "fair": _num(value.get("fair_value")), "buffett": _num(value.get("buffett")),
           "signal": None, "reason": None, "buyBelow": None, "leapsBelow": None}
    try:
        from long_term import service as lt_service, valuation as lt_valuation
        m, v = lt_service._metrics_from(metrics), lt_service._valuation_from(value)     # the page's own converters, no file read
        s = lt_valuation.signal(m, v, price=price, price_ok=bool(price), mos=50)
        out.update(signal=s.get("signal"), reason=s.get("reason"), buyBelow=s.get("buy_below"), leapsBelow=s.get("leaps_below"))
    except Exception:  # noqa: BLE001 - the plain numbers still answer
        if out["fair"]:
            out["buyBelow"] = round(out["fair"] * 0.5, 4)
    return out


def _run(tool: str, args: dict, ctx: Mapping) -> dict:
    from momx import chat_tools
    return chat_tools.run(tool, args, ctx)


def _useful(part: Mapping) -> bool:
    facts = part.get("facts") or {}
    return bool(part.get("lines")) and not any(facts.get(k) for k in ("missing", "error", "needsTicker", "refused"))


def _color(cell: Any, key: str = "bg") -> str:
    return str((cell or {}).get(key) or "").lower() if isinstance(cell, Mapping) else ""


def _lean(color: str) -> int:
    return 1 if color in _BULL_COLORS else -1 if color in _BEAR_COLORS else 0


# ---------------------------------------------------------------- the chart parts (peeked fast, computed slow)
def chart_parts(ticker: str, ctx: Mapping, compute: bool) -> dict:
    """{"ema": report|None, "signals": analyze(1h)|None, "daily": analyze(D)|None}; with compute False only answers already held."""
    from momx import chart_levels, chat_chart
    frames = chart_levels.EMA_TIMEFRAMES
    key = "ema:" + ",".join(frames)
    out: dict = {"ema": None, "signals": None, "busy": False}
    for name, timeframe in (("ema", key), ("signals", "1h")):
        try:
            out[name] = chat_chart.compute(ticker, timeframe, ctx) if compute else chat_chart.peek(ticker, timeframe, ctx)
        except chat_chart.Busy:
            out["busy"] = True
        except Exception:  # noqa: BLE001 - a section is left out, never an error
            out[name] = None
    return out


def _signal_events(parts: Mapping, now: datetime) -> list[dict]:
    """The chart's CALL/PUT and D->M signal events: {at, timeframe, name, side}."""
    found = parts.get("signals") or {}
    rows = [*((found.get("signals") or {}).get("mtf") or []), *((found.get("signals") or {}).get("higher") or [])]
    events = []
    for row in rows:
        try:
            at = datetime.fromtimestamp(int(row.get("sourceTime") or row.get("time")), ET)
        except (TypeError, ValueError, OSError):
            continue
        events.append({"at": at, "timeframe": str(row.get("timeframe") or ""), "name": str(row.get("label") or "signal")
                       + (" (forming)" if row.get("forming") else ""), "side": "bull" if row.get("direction") == "CALL" else "bear"})
    return events


def _ema_events(parts: Mapping) -> list[dict]:
    events = []
    for timeframe, row in ((parts.get("ema") or {}).get("ema") or {}).items():
        cross = row.get("cross2150")
        if cross:
            events.append({"at": datetime.fromtimestamp(int(cross["time"]), ET), "timeframe": timeframe,
                           "name": f"21/50 EMA cross {cross['dir']} ({timeframe})", "side": "bull" if cross["dir"] == "up" else "bear"})
    return events


def _board_events(row: Mapping | None, now: datetime) -> list[dict]:
    """Today's events on the ticker's board row: chart 2H/4H signals, squeeze fires with their colour."""
    if not row:
        return []
    events = []
    for item in row.get("chartSignals") or [] if isinstance(row.get("chartSignals"), list) else []:
        if isinstance(item, Mapping):
            at = chat_facts.when(item.get("at") or item.get("time")) or now
            label = str(item.get("label") or item.get("name") or "")
            if label:
                events.append({"at": at, "timeframe": str(item.get("timeframe") or "2H"), "name": label,
                               "side": "bear" if label.upper().startswith(("PUT", "P")) else "bull"})
    squeeze = row.get("chartSqueeze")
    for item in (squeeze if isinstance(squeeze, list) else [squeeze] if isinstance(squeeze, Mapping) else []):
        if isinstance(item, Mapping):
            lean = _lean(str(item.get("color") or item.get("bg") or "").lower())
            if lean:
                events.append({"at": chat_facts.when(item.get("at") or item.get("time")) or now, "timeframe": str(item.get("timeframe") or ""),
                               "name": f"Squeeze fired {item.get('timeframe') or ''}".strip(), "side": "bull" if lean > 0 else "bear"})
    return events


def verdict(view: str, events: list[dict], now: datetime) -> dict:
    """One view's verdict from the app's own events inside its window (VERDICT_RULE)."""
    title, span, days = WINDOWS[view]
    allowed = {"short": _SHORT_TF, "mid": _MID_TF, "long": _LONG_TF}[view]
    inside = [e for e in events if (now - e["at"]).total_seconds() <= days * 86400 and e["timeframe"] in allowed]
    bull = [e for e in inside if e["side"] == "bull"]
    bear = [e for e in inside if e["side"] == "bear"]
    total = len(bull) + len(bear)
    share = max(len(bull), len(bear)) / total if total else 0.5
    if not total or len(bull) == len(bear):
        call, strength = "Neutral", 1
        reason = "No app signal events in this window." if not total else f"Bullish and bearish events are even ({len(bull)} each)."
    else:
        call = "Bullish" if len(bull) > len(bear) else "Bearish"
        strength = 4 if share == 1 else 3 if share >= 0.75 else 2 if share >= 0.6 else 1
        lead, other = (bull, bear) if call == "Bullish" else (bear, bull)
        reason = f"Recent {call.lower()} events outweigh {'bearish' if call == 'Bullish' else 'bullish'} events ({len(lead)} vs {len(other)})."

    def plain(rows: list) -> list:
        return [{"at": e["at"].isoformat(), "timeframe": e["timeframe"], "name": e["name"], "side": e["side"]}
                for e in sorted(rows, key=lambda e: e["at"], reverse=True)[:12]]
    return {"view": view, "title": title, "window": span, "verdict": call, "strength": strength, "reason": reason,
            "bull": plain(bull), "bear": plain(bear), "neutral": [], "label": VIEWS_LABEL}


def mini_chart(parts: Mapping, events: list[dict], earnings_day: str | None, levels: Mapping) -> dict | None:
    """The last ~60 daily and ~52 weekly bars (OHLCV) with the event markers on their bars, an earnings marker, and
    the nearest support and resistance as two lines. None without a chart."""
    report = parts.get("ema") or {}
    daily, weekly = report.get("daily") or [], report.get("weekly") or []
    if not daily:
        return None

    def place(bars: list, at: datetime) -> int | None:
        stamp = at.timestamp()
        best = None
        for bar in bars:
            if bar["time"] <= stamp + 86400:
                best = bar["time"]
        return best if best is not None and stamp >= bars[0]["time"] else None

    markers = []
    for event in events:
        for frame, bars in (("D", daily), ("W", weekly)):
            spot = place(bars, event["at"]) if bars else None
            if spot is not None:
                markers.append({"chart": frame, "time": spot, "side": event["side"], "name": event["name"][:40]})
    if earnings_day:
        try:
            day = datetime.fromisoformat(earnings_day).replace(tzinfo=ET)
            spot = place(daily, day)
            if spot is not None:
                markers.append({"chart": "D", "time": spot, "side": "earnings", "name": "E"})
        except ValueError:
            pass
    return {"daily": daily, "weekly": weekly, "markers": markers[:80], "support": levels.get("support"), "resistance": levels.get("resistance")}


# ---------------------------------------------------------------- sections
def _section(key: str, title: str, lines: list, as_of: str | None = None, links: list | None = None, table: dict | None = None) -> dict | None:
    lines = [str(x) for x in lines if isinstance(x, str) and x.strip()]
    if not lines:
        return None                                    # a section with no data is left out, never shown empty
    return {"key": key, "title": title, "lines": lines[:MAX_SECTION_LINES], "asOf": as_of, "links": list(links or []), "table": table}


def _price_section(ticker: str, ctx: Mapping, now: datetime) -> tuple[dict | None, dict]:
    from momx import chat_why
    found = chat_why.facts_for(ticker, ctx, now)
    lines = [line for line in found["lines"] if not line.startswith("On the scanner now") and "is not on the Mag7" not in line]
    at = found["move"]["at"] if found["move"] else None
    return _section("price", "Price and today's move", lines, f"as of {chat_facts.clock(at, now)}" if at else None,
                    [_chart_link(ticker)]), found


def _chart_link(ticker: str, timeframe: str = "5m") -> dict:
    from momx import chat_pages
    # The daily chart's button says so: one answer showed "Open NVDA chart" twice, for 5m and for D (2026-11-19).
    label = f"Open {ticker} daily chart" if timeframe == "D" else f"Open {ticker} chart"
    return chat_pages.link("Charts & OI", label, ticker=ticker, timeframe=timeframe)


def _scanner_section(ticker: str, ctx: Mapping, now: datetime) -> tuple[dict | None, list, dict | None]:
    from momx import chat_tools
    sides = chat_facts.scanner_sides(ticker, ctx, now)
    row_at = chat_facts.board_row(ticker, ctx, now)
    row = row_at[0] if row_at else None
    lines = []
    for side in sides:
        lines.append(f"{chat_facts.side_text(side)}" + (f"; setups: {', '.join(side['setups'])}" if side["setups"] else "") + ".")
    if not sides:
        lines.append(f"{ticker} is not on the Mag7 or Watchlist calls or puts boards now.")
    if row:
        lines += indicator_lines(row)
    as_of = f"as of {chat_facts.clock(row_at[1], now)}" if row_at else (f"as of {chat_facts.clock(sides[0]['at'], now)}" if sides else None)
    day = now.date().isoformat()
    return _section("scanner", "Scanner now and today", lines, as_of, [chat_tools.history_link(ticker, day)]), sides, row


def indicator_lines(row: Mapping) -> list[str]:
    """The app's own indicators on the board row, explained from the code (columns.py), not the older help text."""
    lines = []
    hl = row.get("highLow") if isinstance(row.get("highLow"), Mapping) else {}
    if _num(hl.get("value")) is not None:
        lines.append(f"H/L {hl['value']:+.2f}: where price sits in its last 8 two-hour bars (+1 top, -1 bottom).")
    rvol = row.get("rvol") if isinstance(row.get("rvol"), Mapping) else {}
    parts = [f"{tf} {cell.get('value')}" + (f" {cell.get('bg')}" if cell.get("bg") not in (None, "black") else "")
             for tf, cell in rvol.items() if isinstance(cell, Mapping) and cell.get("value") is not None]
    if parts:
        lines.append("RVOL (how unusual each bar's volume is against its last 50 bars; 3+ is very unusual; cyan/green = buyers' bar, "
                     "magenta/red = sellers'): " + ", ".join(parts) + ".")
    sqz = row.get("sqz") if isinstance(row.get("sqz"), Mapping) else {}
    parts = [f"{tf} {cell.get('value')} {cell.get('bg')}" for tf, cell in sqz.items() if isinstance(cell, Mapping) and cell.get("value") not in (None, "-")]
    lines.append("SQZ (white/orange = coiled, number = bars coiled; cyan = released up; magenta = released without upward push): "
                 + (", ".join(parts) if parts else "no squeeze on 2h, 4h, D or W") + ".")
    skit = row.get("skittles") if isinstance(row.get("skittles"), Mapping) else {}
    parts = [f"{tf} {cell.get('value')} ({cell.get('fg') or '-'} text{', ' + str(cell.get('bg')) + ' box' if cell.get('bg') not in (None, 'black') else ''})"
             for tf, cell in skit.items() if isinstance(cell, Mapping) and cell.get("value") is not None]
    if parts:
        lines.append("Skittles (number = stochastic 0-100; box colour = a 9x20, MACD or 4x8 cross on this bar; text colour = trend now): " + ", ".join(parts[:8]) + ".")
    adx = row.get("adx") if isinstance(row.get("adx"), Mapping) else {}
    parts = []
    for tf, cell in adx.items():
        if isinstance(cell, Mapping) and cell.get("adx") is not None:
            parts.append(f"{tf} ADX {cell.get('adx')}, +DI {cell.get('plus', '?')} vs -DI {cell.get('minus', '?')}")
    if parts:
        lines.append("ADX (25+ = strong trend; +DI above -DI = buyers in control): " + "; ".join(parts) + ".")
    m5 = row.get("m5") if isinstance(row.get("m5"), Mapping) else {}
    if m5.get("state") or m5.get("pattern"):
        lines.append(f"Momentum now (5m): {m5.get('state') or '-'}" + (f", pattern {m5.get('pattern')}" if m5.get("pattern") else "") + ".")
    sqz_mom = m5.get("sqzMom")
    if isinstance(sqz_mom, Mapping) and sqz_mom.get("color"):
        lines.append(f"Squeeze momentum (5m): {sqz_mom.get('color')}.")
    grade = row.get("grade")
    if isinstance(grade, Mapping) and grade.get("letter"):
        lines.append(f"Grade {grade.get('letter')} (experimental; on its 22-day record it was close to a coin flip, so it is not a win chance).")
    return lines


def _levels(ticker: str, ctx: Mapping, parts: Mapping, walls: Mapping | None, price: float | None) -> dict:
    """Support and resistance, sorted by distance: pivots, previous day and premarket high/low, High OI walls."""
    found: list[tuple[str, float]] = []
    daily = parts.get("signals") or {}
    levels = daily.get("levels") or {}
    pivots = (levels.get("pivots") or {}).get("DAY") or {}
    for name in ("R2", "R1", "PP", "S1", "S2"):
        if _num(pivots.get(name)):
            found.append((f"Day pivot {name}", float(pivots[name])))
    prev = (levels.get("previous") or {}).get("DAY") or {}
    for name, label in (("high", "Previous day high"), ("low", "Previous day low")):
        if _num(prev.get(name)):
            found.append((label, float(prev[name])))
    pm = (levels.get("session") or {}).get("pm") or {}
    for name, label in (("high", "Premarket high"), ("low", "Premarket low")):
        if _num(pm.get(name)):
            found.append((label, float(pm[name])))
    for side, label in (("calls", "Call wall"), ("puts", "Put wall")):
        for wall in ((walls or {}).get(side) or [])[:2]:
            strike = _num(wall.get("strike")) if isinstance(wall, Mapping) else None
            if strike:
                found.append((f"{label} (High OI board)", strike))
    price = price or _num(levels.get("lastClose"))
    rows = []
    for name, value in found:
        rows.append({"name": name, "value": value, "pct": (value - price) / price * 100 if price else None})
    rows.sort(key=lambda r: abs(r["pct"]) if r["pct"] is not None else 1e9)
    above = next((r for r in rows if price and r["value"] > price), None)
    below = next((r for r in rows if price and r["value"] < price), None)
    return {"rows": rows, "resistance": {"name": above["name"], "value": above["value"]} if above else None,
            "support": {"name": below["name"], "value": below["value"]} if below else None, "price": price}


def _technicals_section(ticker: str, ctx: Mapping, parts: Mapping, walls: Mapping | None, price: float | None, now: datetime) -> tuple[dict | None, dict]:
    from momx import chat_chart
    lines, as_of = [], None
    report = parts.get("ema")
    if report and report.get("ema"):
        ema_lines, _ = chat_chart.ema_lines(ticker, report, ctx)
        lines.append("21 and 50 EMA (the chart's own formula, newest bar's time on each):")
        lines += ema_lines
        lines += chat_chart.ema_boards(ticker, ctx)
        newest = report.get("newestData")
        as_of = f"as of {chat_facts.clock(datetime.fromtimestamp(int(newest), ET), now)}" if newest else None
        for timeframe in ("5m", "D"):
            row = report["ema"].get(timeframe) or {}
            if row.get("ema9") is not None:
                lines.append(f"{timeframe}: EMA 9 {_money(row['ema9'])}" + (f", SMA 200 {_money(row['sma200'])}" if row.get("sma200") is not None else "") + ".")
        volume = _volume_line(report)
        if volume:
            lines.append(volume)
    events = _signal_events(parts, now)
    if events:
        recent = sorted(events, key=lambda e: e["at"], reverse=True)[:6]
        lines.append("Chart signals, newest first: " + "; ".join(f"{e['name']} {chat_facts.clock(e['at'], now)}" for e in recent) + ".")
    pending = ((parts.get("signals") or {}).get("signals") or {}).get("pending") or {}
    if pending and (pending.get("mtfSignalsPending") or pending.get("studiesPending") or not pending.get("historyReady")):
        lines.append("The chart's signals are pending (still being worked out), so this list may be incomplete.")
    levels = _levels(ticker, ctx, parts, walls, price)
    if levels["rows"]:
        lines.append("Support and resistance by distance: " + "; ".join(
            f"{r['name']} {_money(r['value'])} ({_pct(r['pct'])})" for r in levels["rows"][:8]) + ".")
    if parts.get("busy") or not report:
        lines.append("The chart numbers (21/50 EMA on every timeframe, signals) are being worked out; they follow in a moment.")
    lines.append("CoolCandles, Ichimoku, MTF MACD clouds, PersonsPivots and CloudMAX bubbles are shown on the chart only.")
    return _section("technicals", "Technicals", lines, as_of, [_chart_link(ticker, "D")]), levels


def _volume_line(report: Mapping) -> str | None:
    daily = report.get("daily") or []
    average = _num(report.get("dailyVolume20"))
    if not daily or not average:
        return None
    last = daily[-1]
    recent = daily[-10:]
    up = sum((b["volume"] or 0) for b in recent if b["close"] >= b["open"])
    down = sum((b["volume"] or 0) for b in recent if b["close"] < b["open"])
    flow = "more volume on up days" if up > down else "more volume on down days" if down > up else "even volume"
    return (f"Daily volume {int(last['volume'] or 0):,} vs its 20-day average {int(average):,} ({(last['volume'] or 0) / average:.1f}x); "
            f"last 10 days: {flow}.")


def _options_section(ticker: str, ctx: Mapping) -> tuple[dict | None, dict | None, dict | None]:
    em = _run("expected_move", {"ticker": ticker}, ctx)
    walls = _run("walls", {"ticker": ticker}, ctx)
    chain = _run("chain", {"ticker": ticker}, ctx)
    lines, links, as_of = [], [], None
    for part in (em, walls, chain):
        if _useful(part):
            lines += part["lines"][:4]
            links += part["links"][:1]
            as_of = as_of or part.get("asOf")
    return _section("options", "Options: expected move, walls, chain", lines, as_of, links), (walls.get("facts") if _useful(walls) else None), \
        (em.get("facts") if _useful(em) else None)


def _fundamentals_section(ticker: str, ctx: Mapping, price: float | None, now: datetime) -> tuple[dict | None, dict | None]:
    from momx import chat_pages
    row, refreshed = long_term_row(ticker, ctx)
    link = chat_pages.link("Long Term", f"Open {ticker} on Long Term", ticker=ticker)
    if not row:
        return _section("fundamentals", "Fundamentals (Long Term)", [f"{ticker} is not on the Long Term screen, so no fundamentals are saved for it."],
                        None, [link]), None
    m, v = row.get("metrics") or {}, row.get("valuation") or {}
    window = m.get("window") or []
    period = f"{str(window[0])[:4]}-{str(window[-1])[:4]} fiscal years" if window else "the years on file"
    growth = m.get("growth") or {}
    when = f"Long Term data refreshed {chat_facts.clock(refreshed, now)}" if refreshed else "Long Term data"
    lines = [f"{when}; growth is per year over {period}."]

    def g(key: str, label: str) -> str:
        value = _num(growth.get(key))
        return f"{label} {value * 100:.0f}%/yr" if value is not None else f"{label} missing"
    lines.append("Growth: " + ", ".join((g("sales", "revenue"), g("eps", "EPS"), g("fcf", "free cash flow"), g("equity", "equity"))) + ".")
    eps, fcf, debt, equity = _num(m.get("eps_ttm_usd") or m.get("eps_ttm_edgar")), _num(m.get("fcf_ttm")), _num(m.get("debt")), _num(m.get("equity"))
    lines.append(f"EPS (trailing 12 months) {_money(eps) if eps is not None else 'missing'}; free cash flow (TTM) "
                 + (f"{fcf / 1e9:,.1f} B" if fcf is not None else "missing") + ".")
    roic, roe = _num(m.get("roic_now")), _num(m.get("roe_now"))
    lines.append(f"ROIC {roic * 100:.0f}%" if roic is not None else "ROIC missing")
    lines[-1] += f", ROE {roe * 100:.0f}%." if roe is not None else ", ROE missing."
    if debt is not None:
        payback = (debt / fcf) if fcf and fcf > 0 else None
        lines.append(f"Debt {debt / 1e9:,.1f} B" + (f", debt/equity {debt / equity:.2f}" if equity and equity > 0 else "")
                     + (f", {payback:.1f} years of free cash flow to pay it" if payback is not None else "") + ".")
    else:
        lines.append("Debt missing.")
    if price and eps and eps > 0:
        lines.append(f"P/E {price / eps:.1f} at {_money(price)}" + (f" (its historical median {_num(m.get('pe_hist_median')):.1f})" if _num(m.get("pe_hist_median")) else "") + ".")
    if m.get("big5") is not None:
        lines.append(f"Rule #1 Big 5 checks passed: {m.get('big5')} of 5.")
    found = long_term_verdict(row, price)
    if found["sticker"]:
        lines.append(f"Intrinsic value (Long Term page): sticker {_money(found['sticker'])}, fair value {_money(found['fair'])}, Buffett value {_money(found['buffett'])}.")
    if found["buyBelow"]:
        away = (price - found["buyBelow"]) / found["buyBelow"] * 100 if price else None
        lines.append(f"Buy price at a 50% margin of safety: {_money(found['buyBelow'])}" + (f"; price is {_pct(away)} from it." if away is not None else "."))
    elif found["fair"]:
        safety = round(found["fair"] * 0.5, 4)
        away = (price - safety) / safety * 100 if price else None
        lines.append(f"Fair value at a 50% margin of safety: {_money(safety)}" + (f" (price is {_pct(away)} from it)" if away is not None else "")
                     + (f"; the Long Term rule shows no buy price while it says {found['signal']}." if found.get("signal") else "."))
    if found["signal"]:
        lines.append(f"Long Term rule: {found['signal']}" + (f" ({found['reason']})" if found.get("reason") else "") + ".")
    return _section("fundamentals", "Fundamentals (Long Term)", lines, f"as of {chat_facts.clock(refreshed, now)}" if refreshed else None, [link]), found


def _premarket_section(ticker: str, ctx: Mapping) -> dict | None:
    pre = _run("premarket", {"ticker": ticker}, ctx)
    sector = _run("sectors", {"ticker": ticker}, ctx)
    lines, links, as_of = [], [], None
    for part in (pre, sector):
        if _useful(part):
            lines += part["lines"][:3]
            links += part["links"][:1]
            as_of = as_of or part.get("asOf")
    return _section("premarket", "Premarket and sector", lines, as_of, links)


def _earnings_section(ticker: str, ctx: Mapping) -> tuple[dict | None, str | None]:
    part = _run("earnings", {"ticker": ticker}, ctx)
    if not part.get("lines"):
        return None, None
    return _section("earnings", "Earnings date", part["lines"][:2], part.get("asOf"), part["links"][:1]), (part.get("facts") or {}).get("date")


def _sentiment_section(ticker: str, ctx: Mapping, now: datetime, sides: list, back: Mapping, walls: Mapping | None,
                       em: Mapping | None, analyst_lines: list[str]) -> dict | None:
    from momx import chat_why
    lines = []
    pc = put_call(ticker, ctx, now)
    if pc:
        lines.append(pc["line"])
    calls = sum(_num(w.get("openInterest") or w.get("oi")) or 0 for w in (walls or {}).get("calls") or [] if isinstance(w, Mapping))
    puts = sum(_num(w.get("openInterest") or w.get("oi")) or 0 for w in (walls or {}).get("puts") or [] if isinstance(w, Mapping))
    if calls or puts:
        lines.append(f"High OI walls: call walls hold {int(calls):,} OI, put walls {int(puts):,} OI (positioning, not direction).")
    if em and em.get("ivApproxPercent"):
        lines.append(f"IV about {em['ivApproxPercent']}% for {em.get('expiry')} (approximate, backed out of the expected move).")
    saved, _ = chat_why.saved_headlines(ticker, now)
    if saved:
        tone = {"positive": 0, "negative": 0, "neutral": 0}
        for item in saved:
            word = item.get("sentiment") or ""
            tone["positive" if word.startswith("pos") or word == "bullish" else "negative" if word.startswith("neg") or word == "bearish" else "neutral"] += 1
        lines.append(f"News tone, last 48 hours (the app's own headline tags): {tone['positive']} positive, {tone['negative']} negative, {tone['neutral']} neutral.")
    if sides:
        lean = "; ".join(chat_facts.side_text(s) for s in sides)
        lines.append(f"Scanner lean: {lean}.")
    market = back.get("market") or []
    if market:
        lines.append("Market backdrop: " + ", ".join(f"{m['ticker']} {m['pct']:+.2f}%" for m in market) + ".")
    crowd = _crowd(pc, sides, market)
    if crowd:
        lines.append(crowd)
    lines += analyst_lines
    return _section("sentiment", "Sentiment", lines)


def put_call(ticker: str, ctx: Mapping, now: datetime) -> dict | None:
    """Put/call by open interest and by today's volume, summed from the saved chain's near-term rows, with the expiries used."""
    from momx import chat_market
    chain, problem = chat_market._open_chain(ticker, ctx, "put/call", chat_market._quick(ticker))
    if chain is None or not chain.rows:
        return None
    expiries = chain.quotable_expiries()[:2] or chain.row_expiries()[:2]
    oi = {"CALL": 0.0, "PUT": 0.0}
    volume = {"CALL": 0.0, "PUT": 0.0}
    for row in chain.rows:
        side = str(row.get("side") or row.get("putCall") or row.get("type") or "").upper()
        side = "CALL" if side.startswith("C") else "PUT" if side.startswith("P") else ""
        if side and str(row.get("expiry"))[:10] in expiries:
            oi[side] += _num(row.get("open_interest", row.get("openInterest"))) or 0
            volume[side] += _num(row.get("volume", row.get("totalVolume"))) or 0
    if not oi["CALL"] and not oi["PUT"]:
        return None
    ratio_oi = oi["PUT"] / oi["CALL"] if oi["CALL"] else None
    ratio_vol = volume["PUT"] / volume["CALL"] if volume["CALL"] else None
    line = (f"Put/call ratio, expiries {', '.join(expiries)}: by open interest {ratio_oi:.2f}" if ratio_oi is not None else "Put/call by OI not known")
    line += (f", by today's volume {ratio_vol:.2f}" if ratio_vol is not None else "") + f" (saved chain {chat_facts.clock(chain.at, now)})."
    return {"oi": ratio_oi, "volume": ratio_vol, "line": line, "expiries": expiries}


def _crowd(pc: Mapping | None, sides: list, market: list) -> str | None:
    if not pc or pc.get("oi") is None:
        return None
    ratio = pc["oi"]
    lean = "toward puts (more put than call open interest)" if ratio > 1.2 else "toward calls" if ratio < 0.7 else "fairly even"
    line = f"The crowd is leaning {lean}."
    if ratio >= 1.5:
        line += " A very high put/call ratio is often read the other way (crowded fear) - a contrarian note, not a signal."
    elif ratio <= 0.5:
        line += " A very low put/call ratio is often read the other way (crowded greed) - a contrarian note, not a signal."
    return line


def analyst_lines(ticker: str, price: float | None, wait: float = 0.0) -> tuple[list[str], dict | None]:
    from momx import analyst
    doc, status = analyst.get(ticker, wait=wait)
    return analyst.lines_for(doc, status, price), doc


def _bot_section(ticker: str, ctx: Mapping) -> dict | None:
    if ctx.get("scope") != "owner":
        return None
    part = _run("bot_show", {"ticker": ticker}, ctx)
    return _section("bot", "Bot and paper trades today (owner)", part.get("lines")[:8] if part.get("lines") else [], part.get("asOf"), part.get("links"))


# ---------------------------------------------------------------- what the app's rules show
def _check(name: str, lean: int | None, words: str) -> dict:
    return {"name": name, "lean": lean, "words": words}


def rule_checks(view: str, row: Mapping | None, sides: list, parts: Mapping, lt: Mapping | None, price: float | None, now: datetime) -> list[dict]:
    """Each rule's verdict in its own words. lean +1 up, -1 down, 0 mixed, None no data."""
    ema = ((parts.get("ema") or {}).get("ema")) or {}
    checks = []
    events = _signal_events(parts, now)

    def latest(frames: set) -> dict | None:
        pool = [e for e in events if e["timeframe"] in frames]
        return max(pool, key=lambda e: e["at"]) if pool else None

    def trend(frames: tuple) -> dict:
        rows = [ema.get(tf) for tf in frames if ema.get(tf)]
        if not rows:
            return _check(f"Trend vs 21/50 EMA ({'/'.join(frames)})", None, "chart numbers not worked out yet")
        ups = sum(1 for r in rows if r["ema21"]["above"] and r["ema50"]["above"])
        downs = sum(1 for r in rows if not r["ema21"]["above"] and not r["ema50"]["above"])
        lean = 1 if ups == len(rows) else -1 if downs == len(rows) else 0
        words = "; ".join(f"{r['timeframe']}: {'above' if r['ema21']['above'] else 'below'} 21, {'above' if r['ema50']['above'] else 'below'} 50" for r in rows)
        return _check(f"Trend vs 21/50 EMA ({'/'.join(frames)})", lean, words)

    def signal(frames: set, name: str) -> dict:
        found = latest(frames)
        if not found:
            return _check(name, None, "no signal on the chart in its window")
        return _check(name, 1 if found["side"] == "bull" else -1, f"latest {found['name']} at {chat_facts.clock(found['at'], now)}")

    def colours(cells: Mapping, frames: tuple, key: str, name: str) -> dict:
        leans = [(tf, _lean(_color(cells.get(tf), key))) for tf in frames if isinstance(cells.get(tf), Mapping)]
        if not leans:
            return _check(name, None, "not on a board now")
        total = sum(l for _, l in leans)
        lean = 1 if total > 0 else -1 if total < 0 else 0
        return _check(name, lean, ", ".join(f"{tf} {_color(cells.get(tf), key) or '-'}" for tf, _ in leans))

    row = row or {}
    if view == "short":
        checks.append(signal(_SHORT_TF, "Chart CALL/PUT signals (15m-4H)"))
        if sides:
            bulls = any(s["side"] == "bull" for s in sides)
            bears = any(s["side"] == "bear" for s in sides)
            words = "; ".join(chat_facts.side_text(s) for s in sides)
            checks.append(_check("Scanner side", 0 if bulls and bears else 1 if bulls else -1, words))
        else:
            checks.append(_check("Scanner side", None, "not on the Mag7 or Watchlist boards now"))
        checks.append(trend(("5m", "1h")))
        checks.append(colours(row.get("sqz") or {}, ("2h", "4h"), "bg", "SQZ 2h/4h"))
        checks.append(colours(row.get("rvol") or {}, ("5m", "15m", "30m", "1h"), "bg", "RVOL 5m-1h colour"))
        checks.append(colours(row.get("skittles") or {}, ("2h", "4h"), "fg", "Skittles 2h/4h trend colour"))
    elif view == "mid":
        checks.append(signal(_MID_TF, "Chart signals (1H-D)"))
        checks.append(trend(("D",)))
        daily = ema.get("D")
        checks.append(_check("21 vs 50 EMA, daily", (1 if daily["ema21Above50"] else -1) if daily else None,
                             ("21 above 50" if daily["ema21Above50"] else "21 below 50") if daily else "chart numbers not worked out yet"))
        checks.append(colours(row.get("sqz") or {}, ("D",), "bg", "SQZ daily"))
        checks.append(colours(row.get("skittles") or {}, ("D",), "fg", "Skittles daily trend colour"))
    else:
        if lt and lt.get("buyBelow") and price:
            lean = 1 if price <= lt["buyBelow"] else -1 if lt.get("sticker") and price > lt["sticker"] else 0
            checks.append(_check("Long Term rule", lean, f"{lt.get('signal') or 'no signal'}; price {_money(price)} vs buy price {_money(lt['buyBelow'])}"
                                 + (f", sticker {_money(lt['sticker'])}" if lt.get("sticker") else "")))
        else:
            checks.append(_check("Long Term rule", None, (lt or {}).get("signal") or "not on the Long Term screen"))
        checks.append(signal(_LONG_TF, "D->M signals (4x8, 9x20, MACD)"))
        checks.append(trend(("W", "M")))
        checks.append(colours(row.get("skittles") or {}, ("W", "M"), "fg", "Skittles weekly/monthly trend colour"))
    return checks


def count_line(checks: list[dict]) -> str:
    known = [c for c in checks if c["lean"] is not None]
    up = sum(1 for c in known if c["lean"] > 0)
    down = sum(1 for c in known if c["lean"] < 0)
    return (f"{up} of {len(checks)} rule checks point up, {down} point down, "
            f"{len(checks) - up - down} mixed or not known.")


def risks(view: str, sides: list, lt: Mapping | None, price: float | None, earnings_day: str | None, levels: Mapping, now: datetime) -> list[str]:
    out = []
    if any(s["side"] == "bear" for s in sides):
        out.append("It is on a puts board now.")
    if earnings_day:
        try:
            days = (datetime.fromisoformat(earnings_day).date() - now.date()).days
        except ValueError:
            days = None
        if days is not None and 0 <= days <= (14 if view == "short" else 92):
            out.append(f"Earnings in {days} days ({earnings_day}).")
    if view == "long" and lt and lt.get("buyBelow") and price and price > lt["buyBelow"] * 1.5:
        out.append(f"Price is {(price / lt['buyBelow'] - 1) * 100:.0f}% above the Long Term buy price.")
    resistance = levels.get("resistance") if levels else None
    if resistance and price and "Call wall" in resistance["name"] and (resistance["value"] - price) / price < 0.01:
        out.append(f"A call wall sits just above at {_money(resistance['value'])}.")
    support = levels.get("support") if levels else None
    if support and price and "Put wall" in support["name"]:
        out.append(f"The nearest support below is a put wall at {_money(support['value'])}.")
    return out


def _rules_section(ticker: str, views: list[str], row, sides, parts, lt, price, earnings_day, levels, analysts: list[str], now) -> dict | None:
    lines = ["What the app's rules show:"]
    for view in views:
        title, span, _ = WINDOWS[view]
        checks = rule_checks(view, row, sides, parts, lt, price, now)
        lines.append(f"{title} ({span}): {count_line(checks)}")
        lines += [f"- {c['name']}: {c['words']}." for c in checks]
        found = risks(view, sides, lt, price, earnings_day, levels, now)
        if found:
            lines.append("Risks the data shows: " + " ".join(found))
        if view == "long" and analysts:
            summary = next((line for line in analysts if "rate it buy" in line), None) or (analysts[1] if len(analysts) > 1 else None)
            if summary:
                lines.append(f"Analysts: {summary} (Finnhub and FMP; not one of the app's rule checks).")
    lines.append(ADVICE_LINE)
    return _section("rules", "What the app's rules show", lines)


# ---------------------------------------------------------------- the whole report
def build(ticker: str, ctx: Mapping, views: tuple = ("short", "mid", "long"), parts_wanted: tuple = SECTION_ORDER,
          slow: bool = False) -> dict:
    """{"sections", "views", "miniChart", "lines"} for one ticker. slow=False reads only what is held now."""
    now = ctx["now"]
    sections: dict[str, dict | None] = {}
    price_section, found = _price_section(ticker, ctx, now)
    sections["price"] = price_section
    price = (found["move"] or {}).get("price") if found.get("move") else None
    scanner_section, sides, row = _scanner_section(ticker, ctx, now)
    sections["scanner"] = scanner_section
    options_section, walls, em = _options_section(ticker, ctx) if {"options", "technicals", "rules", "sentiment"} & set(parts_wanted) else (None, None, None)
    sections["options"] = options_section
    parts = chart_parts(ticker, ctx, compute=slow)
    technicals, levels = _technicals_section(ticker, ctx, parts, walls, price, now)
    sections["technicals"] = technicals
    price = price or levels.get("price")
    fundamentals, lt = _fundamentals_section(ticker, ctx, price, now)
    sections["fundamentals"] = fundamentals
    sections["premarket"] = _premarket_section(ticker, ctx) if "premarket" in parts_wanted else None
    earnings, earnings_day = _earnings_section(ticker, ctx)
    sections["earnings"] = earnings
    analysts, _ = analyst_lines(ticker, price, wait=6.0 if slow else 0.0) if "long" in views or "sentiment" in parts_wanted else ([], None)
    sections["sentiment"] = _sentiment_section(ticker, ctx, now, sides, found["backdrop"], walls, em, analysts) if "sentiment" in parts_wanted else None
    sections["rules"] = _rules_section(ticker, list(views), row, sides, parts, lt, price, earnings_day, levels, analysts, now) if "rules" in parts_wanted else None
    sections["bot"] = _bot_section(ticker, ctx) if "bot" in parts_wanted and slow else None    # the Bot's files are big: slow part only
    events = [*_signal_events(parts, now), *_ema_events(parts), *_board_events(row, now)]
    verdicts = [verdict(view, events, now) for view in views]
    chart = mini_chart(parts, events, earnings_day, levels)
    ordered = [sections[key] for key in SECTION_ORDER if key in parts_wanted and sections.get(key)]
    return {"sections": ordered, "views": verdicts, "miniChart": chart, "price": price, "found": found}


#: The report's slow part works out the chart on every timeframe; the "more" call may wait this long for it.
REPORT_WAIT_SECONDS = 20.0
_REPORT_LINE = "Full report on {t}: each section below says its time. The chart numbers, Google News and the headline reasons follow in a moment."


def _ticker(args: Mapping, ctx: Mapping) -> str | None:
    word = str(args.get("ticker") or "").strip().lstrip("$").upper()
    return word if re.fullmatch(r"[A-Z][A-Z0-9.]{0,7}", word) else None


def _need_ticker(what: str) -> dict:
    return {"lines": [f"Which ticker do you want {what} for? Type one, like NVDA."], "facts": {"needsTicker": True}, "links": []}


def _tool(kind: str):
    """A report-family tool. kind: report, fundamentals, technicals, sentiment, rules, short, mid, long."""
    plan = {
        "report": (("short", "mid", "long"), SECTION_ORDER, "Full report"),
        "fundamentals": (("long",), ("price", "fundamentals"), "Fundamentals"),
        "technicals": (("short", "mid"), ("price", "technicals", "options"), "Technicals"),
        "sentiment": (("short",), ("price", "sentiment"), "Sentiment"),
        "rules": (("short", "mid", "long"), ("price", "rules"), "What the app's rules show"),
        "short": (("short",), ("price", "why", "scanner", "technicals", "options", "earnings", "rules"), "Short term"),
        "mid": (("mid",), ("price", "technicals", "options", "earnings", "rules"), "Mid term"),
        "long": (("long",), ("price", "fundamentals", "technicals", "sentiment", "earnings", "rules"), "Long term"),
    }[kind]

    def handler(args: Mapping, ctx: Mapping) -> dict:
        from momx import chat_why
        if ctx.get("scope") not in ("owner", "scanner"):
            return {"lines": ["I cannot look that up here."], "facts": {"refused": True}}
        ticker = _ticker(args, ctx)
        if not ticker:
            return _need_ticker(plan[2].lower())
        views, wanted, title = plan
        wanted = tuple(k for k in wanted if k != "bot" or ctx.get("scope") == "owner")
        now = ctx["now"]
        fast = build(ticker, ctx, views, wanted, slow=False)
        why_part, why_lines = None, []
        if "why" in wanted:
            # The report's own job makes the owner's summary, once; the why part inside it makes none.
            why_part = chat_why.why_move({"ticker": ticker, "question": f"why did {ticker} move today"}, {**ctx, "noSummary": True, "whyWait": REPORT_WAIT_SECONDS / 2})
            facts_lines = set(chat_why.facts_for(ticker, ctx, now)["lines"])
            why_lines = [line for line in why_part["lines"] if line not in facts_lines]
            section = _section("why", "Why it moved", why_lines, None, [l for l in why_part.get("links") or [] if l.get("view") == "News Feed"])
            if section:
                fast["sections"].insert(1 if fast["sections"] and fast["sections"][0]["key"] == "price" else 0, section)

        def work() -> dict:
            slow = build(ticker, ctx, views, wanted, slow=True)
            more = chat_why.collect(why_part["more"], str(ctx.get("account") or ""), str(ctx.get("scope") or "")) if why_part and why_part.get("more") else None
            if more:
                kept = [line for line in why_lines if line not in chat_why.PENDING_LINES]
                section = _section("why", "Why it moved", [*kept, *(more.get("lines") or [])], None)
                if section:
                    slow["sections"].insert(1 if slow["sections"] and slow["sections"][0]["key"] == "price" else 0, section)
            return {"lines": [f"{title} on {ticker}, with the chart numbers filled in."], "sections": slow["sections"], "views": slow["views"],
                    "miniChart": slow["miniChart"], "articles": (more or {}).get("articles") or [], "reasons": (more or {}).get("reasons") or []}

        from momx import chat_summary
        token = chat_why.start(work, ctx, wait=REPORT_WAIT_SECONDS, summary=ticker if kind in chat_summary.REPORT_KINDS else None)
        head = _REPORT_LINE.format(t=ticker) if kind == "report" else f"{title} on {ticker}. More follows in a moment."
        lines = [head]
        for section in fast["sections"]:
            lines.append(f"{section['title']}:")
            lines += [line for line in section["lines"][:6] if line != ADVICE_LINE]
        lines = lines[:59]
        if kind in ("rules", "short", "mid", "long", "report"):
            lines.append(ADVICE_LINE)
        from momx import chat_pages
        links = [_chart_link(ticker)]
        if kind in ("rules", "long", "fundamentals", "report"):
            links.append(chat_pages.link("Long Term", f"Open {ticker} on Long Term", ticker=ticker))
        for section in fast["sections"]:
            links += section.get("links") or []
        return {"lines": lines, "facts": {"ticker": ticker, "kind": kind, "sections": [s["key"] for s in fast["sections"]]},
                "links": links[:6], "sections": fast["sections"], "views": fast["views"], "miniChart": fast["miniChart"], "more": token,
                "articles": (why_part or {}).get("articles") or []}
    return handler


def analyst_tool(args: Mapping, ctx: Mapping) -> dict:
    from momx import analyst, chat_pages
    ticker = _ticker(args, ctx)
    if not ticker:
        return _need_ticker("analyst views")
    move = chat_facts.move(ticker, ctx, ctx["now"])
    doc, status = analyst.get(ticker, wait=3.0)
    lines = analyst.lines_for(doc, status, (move or {}).get("price"), chat_facts.clock(move["at"], ctx["now"]) if move else None)
    if not lines:
        lines = [f"Fetching analyst views for {ticker}; ask again in a moment." if status == "busy" else f"No analyst views are saved for {ticker}."]
    return {"lines": lines, "facts": {"ticker": ticker, "status": status}, "links": [chat_pages.link("Long Term", f"Open {ticker} on Long Term", ticker=ticker)]}


# ---------------------------------------------------------------- checks for the route
def clean_sections(raw: Any, scope: str) -> list:
    from momx import chat_pages, chat_tools
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or item.get("key") not in SECTION_ORDER:
            continue
        if item["key"] == "bot" and scope != "owner":
            continue                                  # owner-only, never named to a regular user
        lines = [" ".join(str(x).split())[:400] for x in item.get("lines") or [] if isinstance(x, str) and x.strip()][:MAX_SECTION_LINES]
        if not lines:
            continue
        out.append({"key": item["key"], "title": " ".join(str(item.get("title") or "").split())[:80], "lines": lines,
                    "asOf": str(item["asOf"])[:40] if isinstance(item.get("asOf"), str) else None,
                    "links": chat_pages.clean_links(item.get("links"), scope)[:3], "table": chat_tools.clean_table(item.get("table"), scope)})
    return out


#: The Long Term page's Technical card waits at most this long for the chart child (a wait on an event: the
#: interpreter is free meanwhile). Past it the card is left out and the child carries on for the next visit.
TECHNICAL_WAIT_SECONDS = 4.0
TECHNICAL_SOURCE = "the app's own chart signals, not advice"


def _held_parts(ticker: str, ctx: Mapping, wait: float) -> dict:
    """chart_parts from finished answers only. When one is missing, the existing child path (chat_chart.compute)
    works it out on a side thread and this waits up to `wait` seconds for it; nothing here reads a tape itself."""
    parts = chart_parts(ticker, ctx, compute=False)
    if parts.get("ema") is not None and parts.get("signals") is not None or wait <= 0:
        return parts
    done = threading.Event()

    def run() -> None:
        try:
            chart_parts(ticker, ctx, compute=True)
        finally:
            done.set()
    threading.Thread(target=run, name=f"lt-technical-{ticker}", daemon=True).start()
    done.wait(wait)
    return chart_parts(ticker, ctx, compute=False)


def technical(ticker: str, ctx: Mapping, wait: float = TECHNICAL_WAIT_SECONDS) -> dict | None:
    """The Technical analysis card (Short/Mid/Long pills, verdict, strength, mini chart) in the shape the screen's
    TechnicalCard reads: {symbol, asOf, source, terms: {short|mid|long: {verdict, strength, reason}},
    charts: {daily|weekly: {bars, events, support, resistance}}}. The same verdict() and mini_chart() as the chat
    report, from the chart child's finished answers and boards already in memory: no chart build, no file parsed
    here. Levels are the chart's own pivots and previous-day/premarket highs and lows (the option walls and the
    earnings marker are left to the chat report). None when the app holds no chart numbers for the ticker."""
    now = ctx["now"]
    parts = _held_parts(ticker, ctx, wait)
    if not parts.get("ema") and not parts.get("signals"):
        return None
    row = chat_facts.held_board_row(ticker, now)
    events = [*_signal_events(parts, now), *_ema_events(parts), *_board_events(row, now)]
    terms = {}
    for view in ("short", "mid", "long"):
        found = verdict(view, events, now)
        terms[view] = {"verdict": found["verdict"].lower(), "strength": found["strength"], "reason": found["reason"]}
    levels = _levels(ticker, ctx, parts, None, None)
    chart = clean_mini_chart(mini_chart(parts, events, None, levels) or {})

    def line(raw: Any) -> dict | None:
        return {"price": raw["value"], "label": raw["name"]} if isinstance(raw, Mapping) else None

    def frame(code: str, bars: list) -> dict | None:
        if not bars:
            return None
        return {"bars": bars, "events": [{"kind": m["side"], "time": m["time"], "name": m["name"]} for m in chart.get("markers") or [] if m["chart"] == code],
                "support": line(chart.get("support")), "resistance": line(chart.get("resistance"))}
    newest = (parts.get("ema") or {}).get("newestData") or ((parts.get("signals") or {}).get("tape") or {}).get("newestData")
    try:
        as_of = f"as of {chat_facts.clock(datetime.fromtimestamp(int(newest), ET), now)}" if newest else ""
    except (TypeError, ValueError, OSError):
        as_of = ""
    return {"symbol": ticker, "asOf": as_of, "source": TECHNICAL_SOURCE, "rule": VERDICT_RULE, "terms": terms,
            "charts": {"daily": frame("D", chart.get("daily") or []), "weekly": frame("W", chart.get("weekly") or [])}}


def clean_views(raw: Any) -> list:
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping) or item.get("view") not in WINDOWS or item.get("verdict") not in ("Bullish", "Bearish", "Neutral"):
            continue
        strength = item.get("strength") if isinstance(item.get("strength"), int) and 1 <= item["strength"] <= 4 else 1

        def events(rows: Any) -> list:
            return [{k: str(e.get(k) or "")[:60] for k in ("at", "timeframe", "name", "side")} for e in (rows or [])[:12] if isinstance(e, Mapping)]
        out.append({"view": item["view"], "title": str(item.get("title") or "")[:30], "window": str(item.get("window") or "")[:40],
                    "verdict": item["verdict"], "strength": strength, "reason": " ".join(str(item.get("reason") or "").split())[:200],
                    "bull": events(item.get("bull")), "bear": events(item.get("bear")), "neutral": events(item.get("neutral")), "label": VIEWS_LABEL})
    return out


def clean_mini_chart(raw: Any) -> dict:
    if not isinstance(raw, Mapping):
        return {}

    def bars(rows: Any, limit: int) -> list:
        out = []
        for bar in (rows or [])[-limit:]:
            if isinstance(bar, Mapping) and all(isinstance(bar.get(k), (int, float)) for k in ("time", "open", "high", "low", "close")):
                out.append({k: bar.get(k) if isinstance(bar.get(k), (int, float)) else 0 for k in ("time", "open", "high", "low", "close", "volume")})
        return out

    def line(value: Any) -> dict | None:
        return {"name": str(value.get("name") or "")[:40], "value": value["value"]} if isinstance(value, Mapping) and isinstance(value.get("value"), (int, float)) else None
    markers = [{"chart": m.get("chart") if m.get("chart") in ("D", "W") else "D", "time": m["time"], "side": str(m.get("side") or "")[:10],
                "name": str(m.get("name") or "")[:40]} for m in raw.get("markers") or [] if isinstance(m, Mapping) and isinstance(m.get("time"), (int, float))][:80]
    return {"daily": bars(raw.get("daily"), 60), "weekly": bars(raw.get("weekly"), 52), "markers": markers,
            "support": line(raw.get("support")), "resistance": line(raw.get("resistance"))}


_TICKER_ARG = {"type": "string", "description": "Ticker symbol, for example NVDA."}
_ARGS = {"type": "object", "properties": {"ticker": _TICKER_ARG}}
TOOLS: dict[str, dict] = {
    "report": {"scope": "all", "handler": _tool("report"), "args": _ARGS,
               "description": "Everything the app has on one ticker: move, why it moved, scanner, technicals, options, fundamentals, sentiment, and what the app's rules show (short, mid and long term)."},
    "fundamentals": {"scope": "all", "handler": _tool("fundamentals"), "args": _ARGS,
                     "description": "A ticker's fundamentals from the Long Term page: growth, cash flow, debt, ROIC, P/E, intrinsic value and buy price."},
    "technicals": {"scope": "all", "handler": _tool("technicals"), "args": _ARGS,
                   "description": "A ticker's technicals from the app's own indicators: 21/50 EMA on every timeframe, signals, SQZ, Skittles, RVOL, ADX, support and resistance."},
    "sentiment": {"scope": "all", "handler": _tool("sentiment"), "args": _ARGS,
                  "description": "How traders lean on a ticker: put/call ratio, walls, IV, news tone, scanner lean, market backdrop."},
    "rules": {"scope": "all", "handler": _tool("rules"), "args": _ARGS,
              "description": "What the app's rules show for a ticker (for 'should I buy' or 'is it a buy'): each rule's verdict, counted, with risks. Never advice."},
    "outlook_short": {"scope": "all", "handler": _tool("short"), "args": _ARGS, "description": "Short-term view of a ticker (today to about 2 weeks)."},
    "outlook_mid": {"scope": "all", "handler": _tool("mid"), "args": _ARGS, "description": "Mid-term view of a ticker (about 2 weeks to 3 months)."},
    "outlook_long": {"scope": "all", "handler": _tool("long"), "args": _ARGS, "description": "Long-term (LEAP) view of a ticker (months to years)."},
    "analyst": {"scope": "all", "handler": analyst_tool, "args": _ARGS,
                "description": "Analyst ratings and price targets for a ticker from Finnhub and FMP (not the app's rules)."},
}
