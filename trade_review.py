"""End-of-day trade review: the day's journal trades judged against the
signals that were actually on the chart.

The user's own framing started this whole program: "most of my losses are
from our app". The review makes that measurable per trade: was there a CALL
signal behind the entry, how long after it did the entry come, and did the
with-signal trades outperform the no-signal ones. Template-generated like
the briefing - every sentence traces to a journal row or a signal timestamp,
never to a judgement a model invented.

Pure stdlib, all inputs injected (trades from the journal DB, signals from
the same server-side tape the chart draws), so tests can pin every verdict.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# An entry this long after the most recent CALL signal still counts as
# "with the signal". Two hours: the span of the CALL2H bucket itself.
SIGNAL_BACKING_WINDOW_SECONDS = 2 * 3600


def _parse_utc(stamp: object) -> datetime | None:
    text = str(stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _fmt_et(moment: datetime) -> str:
    return moment.astimezone(EASTERN).strftime("%H:%M")


def _fmt_money(value: float) -> str:
    return f"{'+' if value >= 0 else '-'}${abs(value):,.0f}"


def judge_trade(trade: dict, signals: dict) -> dict | None:
    """One trade's verdict: entry/exit, P&L, and the signal behind it (or not).

    ``signals`` is {"calls": [{"label","time"}...], "fires": [{"label",
    "closeTime"}...]} for the trade's symbol on the trade's day - the same
    rows the scanner and chart share.
    """
    if not isinstance(trade, dict):
        return None
    symbol = str(trade.get("symbol") or "").upper()
    opened = _parse_utc(trade.get("opened_at"))
    if not symbol or opened is None:
        return None
    closed = _parse_utc(trade.get("closed_at"))
    try:
        pnl = float(trade.get("pnl")) if trade.get("pnl") is not None else None
    except (TypeError, ValueError):
        pnl = None

    entry_epoch = opened.timestamp()
    backing = None
    for signal in (signals or {}).get("calls") or []:
        try:
            signal_time = float(signal.get("time"))
        except (TypeError, ValueError):
            continue
        lag = entry_epoch - signal_time
        if 0 <= lag <= SIGNAL_BACKING_WINDOW_SECONDS:
            if backing is None or signal_time > backing[1]:
                backing = (str(signal.get("label") or "CALL"), signal_time)
    fire_backing = None
    if backing is None:
        for fire in (signals or {}).get("fires") or []:
            try:
                fire_time = float(fire.get("closeTime"))
            except (TypeError, ValueError):
                continue
            lag = entry_epoch - fire_time
            if 0 <= lag <= SIGNAL_BACKING_WINDOW_SECONDS:
                if fire_backing is None or fire_time > fire_backing[1]:
                    fire_backing = (str(fire.get("label") or "fire"), fire_time)

    if backing:
        lag_minutes = int((entry_epoch - backing[1]) // 60)
        backing_text = f"{lag_minutes}m after {backing[0]}"
        backed = True
    elif fire_backing:
        lag_minutes = int((entry_epoch - fire_backing[1]) // 60)
        backing_text = f"{lag_minutes}m after fire {fire_backing[0]} (fire only - no CALL)"
        backed = True
    else:
        backing_text = "no signal behind the entry"
        backed = False

    still_open = closed is None
    outcome = "still open" if still_open else (
        "winner" if (pnl or 0) > 0 else ("loser" if (pnl or 0) < 0 else "flat")
    )
    line = f"{symbol}: in {_fmt_et(opened)} ({backing_text})"
    if closed is not None:
        line += f", out {_fmt_et(closed)}"
    if pnl is not None:
        line += f", {_fmt_money(pnl)}"
    line += f" - {outcome}."
    return {
        "symbol": symbol,
        "backed": backed,
        "pnl": pnl,
        "open": still_open,
        "line": line,
    }


def build_trade_review(trades: object, signals_by_symbol: dict, now_et: datetime) -> dict:
    """The whole review: per-trade verdicts plus the pattern summary."""
    verdicts: list[dict] = []
    for trade in trades if isinstance(trades, (list, tuple)) else []:
        verdict = judge_trade(trade, (signals_by_symbol or {}).get(
            str((trade or {}).get("symbol") or "").upper(), {}
        ))
        if verdict:
            verdicts.append(verdict)

    lines: list[str] = []
    if not verdicts:
        lines.append("No trades in the journal today.")
        return {
            "date": now_et.date().isoformat(),
            "generatedAt": now_et.isoformat(),
            "lines": lines,
            "trades": [],
        }

    closed = [v for v in verdicts if not v["open"] and v["pnl"] is not None]
    winners = [v for v in closed if v["pnl"] > 0]
    losers = [v for v in closed if v["pnl"] < 0]
    net = sum(v["pnl"] for v in closed)
    open_count = sum(1 for v in verdicts if v["open"])
    headline = (
        f"{len(verdicts)} trade{'s' if len(verdicts) != 1 else ''} today: "
        f"{len(winners)} winner{'s' if len(winners) != 1 else ''}, "
        f"{len(losers)} loser{'s' if len(losers) != 1 else ''}"
        + (f", {open_count} still open" if open_count else "")
        + (f", net {_fmt_money(net)}." if closed else ".")
    )
    lines.append(headline)
    lines.extend(v["line"] for v in verdicts)

    # The pattern that matters: does trading WITH the signal pay?
    backed_closed = [v for v in closed if v["backed"]]
    unbacked_closed = [v for v in closed if not v["backed"]]
    if backed_closed and unbacked_closed:
        backed_net = sum(v["pnl"] for v in backed_closed)
        unbacked_net = sum(v["pnl"] for v in unbacked_closed)
        lines.append(
            f"Pattern: with-signal trades netted {_fmt_money(backed_net)} "
            f"({len(backed_closed)}), no-signal trades netted "
            f"{_fmt_money(unbacked_net)} ({len(unbacked_closed)})."
        )
        if unbacked_net < 0 <= backed_net:
            lines.append("The losses came from trades the scanner never called.")
    elif unbacked_closed and not backed_closed:
        lines.append("None of today's closed trades had a signal behind the entry.")

    return {
        "date": now_et.date().isoformat(),
        "generatedAt": now_et.isoformat(),
        "lines": lines,
        "trades": verdicts,
    }
