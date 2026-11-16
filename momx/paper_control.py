"""Paper bot safety controls (2026-11-09): pause, daily loss stop, stuck orders.

His "Yes" to a pause button and a daily loss stop, plus "stuck orders give me
button to remove or delete". The Scorecard found the paper bot had no way to be
stopped from the screen and no daily loss limit of its own.

* Pause - per account. New buys stop (first tries and re-sends at the ask);
  positions already held keep their exit plan. The older artifacts/
  paper_mirror_off file is a different, harder switch: it stops exits too.
* Daily loss stop - once the day's real paper P/L is down DAILY_LOSS_STOP, no
  new buys for the rest of that day. It does NOT sell what is held: his exit
  plan stays in charge of exits.
* Stuck order - a buy the broker never confirmed. Removing it is done by the
  mirror itself (PaperMirror.remove_stuck) because it owns the order rows.

This module is the small shared part: the pause file, and the day's P/L sum.
No imports from the scanner, so api_server may read it too.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Down this many dollars on the day -> no new paper buys until tomorrow. The
#: same number the Scorecard's "Daily loss limit" check uses (bot_desk.LIMITS).
DAILY_LOSS_STOP = 500.0
#: OFF since 2026-11-10. The stop was my default; it held both paper accounts by
#: 9:40 AM and they missed AVGO. His ruling: "Why you set daily set loss limit.
#: Trade as much as you can". The Scorecard still MEASURES a day worse than
#: -$500 (its "Daily loss limit" check); nothing holds a buy for it any more.
DAILY_STOP_ON = False
#: A buy still unconfirmed after this long is "stuck" (a normal buy resolves in
#: about 6 minutes: 2 minutes at the mid, then two re-sends at the ask).
STUCK_SECONDS = 600.0
FILE = "control.json"
HOLDING = ("open", "selling", "exit_due")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def load(root: Path) -> dict:
    """{"paused": {account: {"at", "by"}}}. A missing or broken file = nothing paused."""
    try:
        doc = json.loads((Path(root) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"paused": {}}
    paused = doc.get("paused") if isinstance(doc, dict) else None
    return {"paused": {str(k): v for k, v in paused.items() if isinstance(v, dict)} if isinstance(paused, dict) else {}}


def paused(root: Path, account: str) -> dict | None:
    """The pause record for this account, or None when it is buying."""
    return load(root)["paused"].get(account)


def set_paused(root: Path, account: str, pause: bool, who: str, stamp: str) -> dict:
    """Pause or resume one account. Raises OSError when the file cannot be
    written - the caller must never report a pause that was not saved."""
    root = Path(root)
    doc = load(root)
    if pause:
        doc["paused"][account] = {"at": stamp, "by": str(who or "")}
    else:
        doc["paused"].pop(account, None)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (FILE + ".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, root / FILE)
    return doc


def not_found(exc: BaseException) -> bool:
    """True when the broker answered that it has no such order (Alpaca 40410000)."""
    text = str(exc).lower()
    return "40410000" in text or "order not found" in text


def refusal_reason(error: Any) -> str:
    """What the broker said when it refused a buy, in plain words. On 2026-11-10 Paper 3's
    broker account ran out of cash and 22 buys were refused; the app only said "never reached
    the broker" because the broker's answer was thrown away."""
    text = " ".join(str(error or "").split())
    low = text.lower()
    if "insufficient" in low or "buying power" in low or "buying_power" in low:
        return "not enough buying power in the paper account"
    # Alpaca answers {"code": ..., "message": "..."}: the message is the readable part.
    start = text.find("{")
    if start >= 0:
        try:
            said = json.loads(text[start:text.rfind("}") + 1]).get("message")
        except (ValueError, AttributeError):
            said = None
        if said:
            return str(said)[:140]
    return text[:140]


def broker_answered(exc: BaseException) -> bool:
    """True when the error is the broker's own answer (an HTTP status or an Alpaca error code), not a
    network failure on the way - only then can a missing order be called a refusal."""
    if getattr(exc, "status_code", None) or getattr(exc, "code", None):
        return True
    text = str(exc).lower()
    return '"code"' in text or "insufficient" in text or "buying power" in text or "forbidden" in text


def carried(r: Mapping, day: str) -> bool:
    """True for a position bought before `day`. Decided from the row's own buy
    date as well as the mirror's flag, so the mirror and the Scorecard agree
    even before the mirror has written today's file (pre-market, weekend)."""
    if r.get("carried"):
        return True
    bought = str(r.get("buyFillAt") or r.get("buyAt") or r.get("at") or "")[:10]
    return bool(bought) and bought < day


def day_pnl(rows: Iterable[Mapping], day: str) -> float:
    """The account's real paper P/L for `day` (ET date string), in dollars.

    Closed trades count on the day they closed (the Scorecard's rule). A
    position still held counts what it already sold today, plus its marked
    profit or loss when it was bought today. The mark is the live one, or the
    last good one when the quote has dropped out (a dead quote must not hide
    a loss from the stop). A position carried from an earlier day adds no
    marked amount: yesterday's move is not today's loss. A position that never
    had a price adds nothing - no price is never treated as a price of zero.
    """
    total = 0.0
    for r in rows:
        buy = _num(r.get("buyFill"))
        if buy is None or buy <= 0:
            continue
        if r.get("status") == "closed":
            pl = _num(r.get("pl"))
            if pl is not None and str(r.get("closedAt") or "")[:10] == day:
                total += pl
            continue
        if r.get("status") not in HOLDING:
            continue
        for f in r.get("sells") or []:
            price, qty = _num(f.get("price")), _num(f.get("qty"))
            if price is not None and qty and str(f.get("at") or "")[:10] == day:
                total += (price - buy) * qty * 100
        mark, held = _num(r.get("markMid")), _num(r.get("held"))
        if mark is None:
            mark = _num(r.get("stopMark"))
        if not carried(r, day) and mark is not None and held and held > 0:
            total += (mark - buy) * held * 100
    return round(total, 2)
