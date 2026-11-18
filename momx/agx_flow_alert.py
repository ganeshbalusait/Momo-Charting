"""AGX Flow phone alert (2026-11-17, "See any features we can add" -> "All six do it").

When a watchlist stock (not Mag7: their flow is constant and would bury the rest) gets an UNUSUAL contract (today's
volume >= 500 and above open interest) worth $1M or more in premium, the trader's phone gets one ntfy push.

Kept quiet on purpose:
  * each contract alerts once a day (the ledger survives a restart);
  * at most one push every PUSH_EVERY_SECONDS, bundling everything new since the last one (top MAX_LINES lines);
  * market hours only, and only inside the account's own phone window (the Momo Alert setting, 9:15-3:30 ET default);
  * per account and OFF unless that account turns it on (the AGX Flow page's switch), the same opt-in rule as the
    Market Turn push: a new kind of alert nobody else asked for. It uses the phone channel set up for Momo Alerts.

It only reads copies the AGX Flow readers already hold: it never asks Schwab by itself. Facts only, not a direction
signal; it never places anything.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from momx import agx_flow, board_filter

ALERT_PREMIUM = 1_000_000
PUSH_EVERY_SECONDS = 10 * 60
CHECK_EVERY_SECONDS = 60
MAX_LINES = 6
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
FILE = "agx_flow_alert.json"

_LOCK = threading.Lock()
_LOOP: dict[str, Any] = {"thread": None}


def _path(root: Path) -> Path:
    return Path(root) / FILE


def _read(root: Path) -> dict:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(root: Path, data: dict) -> None:
    path = _path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        temp.replace(path)
    except OSError:
        pass


def _key(email: Any) -> str:
    return str(email or "").strip().lower()


def is_on(root: Path, email: Any) -> bool:
    accounts = _read(root).get("accounts")
    return bool(isinstance(accounts, dict) and accounts.get(_key(email)) is True)


def set_on(root: Path, email: Any, on: bool) -> bool:
    """This account's switch only; other accounts untouched. Returns the saved value."""
    key = _key(email)
    if not key:
        return False
    with _LOCK:
        data = _read(root)
        accounts = data.get("accounts") if isinstance(data.get("accounts"), dict) else {}
        accounts[key] = bool(on)
        data["accounts"] = accounts
        _write(root, data)
    return bool(on)


def contract_id(row: Mapping) -> str:
    return f"{row.get('ticker')}|{row.get('side')}|{str(row.get('expiry'))[:10]}|{float(row.get('strike') or 0):g}"


def _read_today(doc: Mapping | None, now: datetime) -> bool:
    """Read during TODAY's session: yesterday's copy still on disk at 9:31 must never alert yesterday's volume."""
    return agx_flow.read_this_session(dict(doc or {}), now)


def big_unusual(symbols: Iterable[str], root: Path, now: datetime) -> list[dict]:
    """Every unusual contract of ALERT_PREMIUM+ in today's copies already held for these stocks (no Schwab request),
    biggest premium first. Mag7, ETFs and stocks under $10 are left out."""
    tickers, _ = board_filter.drop_etfs([t for t in agx_flow.parse_symbols(list(symbols)) if t not in MAG7])
    out: list[dict] = []
    for ticker in tickers:
        doc = agx_flow._held(ticker, Path(root))
        if not doc or not _read_today(doc, now) or board_filter.too_small(doc.get("spot")):
            continue
        for contract in doc.get("contracts") or []:
            row = agx_flow._flow_row(ticker, doc.get("spot"), contract)
            if agx_flow.is_unusual(row["volume"], row["oi"]) and row["premium"] >= ALERT_PREMIUM:
                out.append(row)
    out.sort(key=lambda r: (-r["premium"], r["ticker"], r["days"], r["strike"]))
    return out


def message(rows: list[dict]) -> tuple[str, str]:
    """('AGX Flow: 3 new $1M+ unusual', one line per contract). Plain facts, no call to action."""
    def money(value: float) -> str:
        return f"${value / 1e6:.1f}M" if value >= 1e6 else f"${round(value / 1e3)}K"
    lines = []
    for row in rows[:MAX_LINES]:
        side = "Put" if row["side"] == "PUT" else "Call"
        fill = {"ask": " · Bought", "bid": " · Sold"}.get(row.get("fill"), "")
        expiry = datetime.fromisoformat(str(row["expiry"])[:10]).strftime("%b %d").replace(" 0", " ")
        lines.append(f"{row['ticker']} {row['strike']:g} {side} {expiry} · {money(row['premium'])} · "
                     f"vol {row['volume']:,} vs OI {row['oi']:,}{fill}")
    more = len(rows) - MAX_LINES
    if more > 0:
        lines.append(f"+{more} more on AGX Flow")
    lines.append("Facts only, not a direction signal.")
    count = len(rows)
    return f"AGX Flow: {count} new $1M+ unusual contract{'s' if count != 1 else ''}", "\n".join(lines)


def _recipients(root: Path, now: datetime, configs: Mapping[str, Mapping] | None) -> list[str]:
    from momx import momo_alert
    configs = momo_alert.all_user_configs() if configs is None else configs
    topics = []
    for email, config in configs.items():
        topic = str((config or {}).get("ntfyTopic") or "").strip()
        if topic and is_on(root, email) and momo_alert.push_window_allows(config, now) and topic not in topics:
            topics.append(topic)
    return topics


def check(symbols: Iterable[str], root: Path, now: datetime | None = None, *,
          poster: Callable[[str, str, str, str], None] | None = None,
          configs: Mapping[str, Mapping] | None = None, clock: Callable[[], float] = time.time) -> dict:
    """One pass: find contracts not yet alerted today and, when a push is due, send ONE bundled push to every
    opted-in account. Returns what happened (for tests and the log)."""
    now = (now or datetime.now(agx_flow.ET)).astimezone(agx_flow.ET)
    if not agx_flow.market_open(now):
        return {"pushed": 0, "reason": "market closed"}
    day = now.date().isoformat()
    with _LOCK:
        data = _read(root)
        ledger = data.get("ledger") if isinstance(data.get("ledger"), dict) else {}
        if ledger.get("day") != day:
            ledger = {"day": day, "sent": [], "lastPush": 0.0}
        sent = set(ledger.get("sent") or [])
        fresh = [row for row in big_unusual(symbols, root, now) if contract_id(row) not in sent]
        if not fresh:
            return {"pushed": 0, "reason": "nothing new"}
        if clock() - float(ledger.get("lastPush") or 0) < PUSH_EVERY_SECONDS:
            return {"pushed": 0, "reason": "too soon", "waiting": len(fresh)}
        topics = _recipients(root, now, configs)
        title, body = message(fresh)
        delivered = 0
        if topics:
            from momx import momo_alert
            send = poster or momo_alert._default_poster
            for topic in topics:
                try:
                    send(topic, title, body, "whale")
                    delivered += 1
                except Exception:  # noqa: BLE001 - a push is best effort; the page still shows the flow
                    pass
            if not delivered:
                # Nothing went out (ntfy down, no internet): keep them new, so the next pass tries again (review 11-17).
                return {"pushed": 0, "reason": "push failed", "contracts": len(fresh)}
        # Nobody opted in (or inside their window) -> marked seen without a push, so turning it on later does not
        # send a backlog of old contracts.
        ledger["sent"] = sorted(sent | {contract_id(row) for row in fresh})
        ledger["lastPush"] = clock() if delivered else float(ledger.get("lastPush") or 0)
        data["ledger"] = ledger
        _write(root, data)
    return {"pushed": delivered, "contracts": len(fresh), "title": title, "body": body}


def start(get_symbols: Callable[[], list[str]], root: Path) -> None:
    """Check every CHECK_EVERY_SECONDS in the background (api_server starts it next to the AGX Flow warm-up)."""
    def loop() -> None:
        while True:
            try:
                check(get_symbols(), root)
            except Exception:  # noqa: BLE001 - the alert never stops the server
                pass
            time.sleep(CHECK_EVERY_SECONDS)
    with _LOCK:
        if _LOOP["thread"] is not None:
            return
        _LOOP["thread"] = threading.Thread(target=loop, name="agx-flow-alert", daemon=True)
        _LOOP["thread"].start()
