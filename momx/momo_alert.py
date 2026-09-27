"""The Momo Alert: the DKNG-catcher.

The trader's ask (2026-08-30): "Same like DKNG we want trade" - DKNG printed
RVOL 5m 12.3 while the Bull Momo scan passed, and the +4-7% move followed. The
board CAUGHT it (a strip chip), but a chip on one panel is easy to miss while
watching a chart. This module decides when that catch deserves an app-wide
interruption: banner + sound, from any panel.

An alert fires for a symbol when, on a fresh build:

  1. an ARMED timeframe's RVOL crosses that timeframe's OWN threshold
     (edge-triggered via fastlane.rvol_spikes - a symbol sitting elevated
     does not re-fire), AND
  2. the Bull Momo scan PASSES for that symbol (volume alone is one block
     trade; volume + momentum agreement is the DKNG signature), AND
  3. the symbol is not in cooldown (one alert per symbol per 15 minutes,
     so a hot name does not re-alarm every 20-second build), AND
  4. the tape is LIVE (newest bar under 30 minutes old) - a frozen
     weekend/holiday tape can never alarm, and neither can the first build
     after a worker restart re-announcing Friday's readings.

Defaults chosen with the trader (2026-08-30): 5m armed at 3.0x; 15m/30m/1h/2h
present in config but off. 2h/4h/D are deliberately NOT offered: by the time
those cross, the move is old news for an options entry.

Config persists to artifacts/momx_momo_alert.json so a restart keeps the
trader's tuning. All functions are pure except load/save; the worker owns the
ledger dict and passes it back in.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from . import fastlane

__all__ = [
    "DEFAULT_CONFIG", "DEFAULT_USER", "load_config", "save_config",
    "load_user_config", "save_user_config", "all_user_configs",
    "detect", "push_ntfy", "push_window_allows",
]

#: Timeframes the alert may arm. The trader's DKNG screenshot (2026-08-30)
#: settled 2h's inclusion: mid-move, 5m had cooled to 2.5 while 30m/1h/2h sat
#: at 6.3/5.8/5.4 - sustained volume IS the signature, so the sustained
#: timeframes must be armable. 4h/D remain board-only: too slow to interrupt.
ALERT_TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h")

DEFAULT_CONFIG: dict = {
    "timeframes": {
        "5m": {"armed": True, "threshold": 3.0},
        "15m": {"armed": False, "threshold": 3.0},
        "30m": {"armed": False, "threshold": 3.0},
        "1h": {"armed": False, "threshold": 3.0},
        "2h": {"armed": False, "threshold": 3.0},
    },
    "cooldownMinutes": 15,
    "sound": True,
    # ntfy push: when a topic is set, every alert also posts to
    # https://ntfy.sh/<topic> so the trader's PHONE buzzes - locked, browser
    # closed, anywhere. Empty string = push off. The topic name is the secret
    # (anyone knowing it can read the alerts), so it is generated random.
    "ntfyTopic": "",
    # When the PHONE push is allowed to fire, as minutes-since-midnight in
    # America/New_York. The trader (2026-08-31): "ntfy alert momo scanner from
    # 9:15am to 3:30pm est" - so 555 (09:15) to 930 (15:30), half-open
    # [start, end): 9:15 pushes, 15:30 does not. This gates ONLY push_ntfy;
    # in-app banner + sound + detection are untouched.
    "pushWindow": {"start": 9 * 60 + 15, "end": 15 * 60 + 30},
    # The MARKET TURN push (momx/market_turn.py, 2026-09-25): opt-in per
    # account - a new kind of alert nobody else asked for. Uses ntfyTopic.
    "marketTurnPush": False,
}

#: The push window's timezone. Minutes are wall-clock ET so the window tracks
#: DST with the market instead of drifting an hour twice a year.
PUSH_WINDOW_ZONE = ZoneInfo("America/New_York")

#: A tape older than this is "not live": no alerts. Mirrors the board's
#: "Data as of ..." note threshold so the two features agree on what stale is.
FRESH_TAPE_SECONDS = 30 * 60

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "momx_momo_alert.json"

# ---------------------------------------------------------------------------
# per-user configs (2026-08-30: "my user change alert for their account only")
# ---------------------------------------------------------------------------
# One JSON file, {email: config}. Every AGX user tunes their OWN timeframes,
# thresholds, sound and phone topic; a user's change can never touch another
# account. "_default" is the config served to a request whose user is unknown
# (direct :3010 probes; api_server always forwards the session email).
_USERS_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "momx_momo_alert_users.json"
DEFAULT_USER = "_default"


def _read_users(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _user_key(email: Any) -> str:
    key = str(email or "").strip().lower()
    return key if key else DEFAULT_USER


def load_user_config(email: Any, path: Path | None = None) -> dict:
    """This user's config, coerced; an unknown user gets the defaults."""
    users = _read_users(path or _USERS_PATH)
    return _coerce_config(users.get(_user_key(email)))


def save_user_config(email: Any, raw: Any, path: Path | None = None) -> dict:
    """Validate and persist ONE user's config; other accounts untouched."""
    target = path or _USERS_PATH
    users = _read_users(target)
    merged = _coerce_config(raw)
    users[_user_key(email)] = merged
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(users, indent=2), encoding="utf-8")
    except OSError:
        pass
    return merged


def all_user_configs(path: Path | None = None) -> dict[str, dict]:
    """Every stored account's coerced config - the evaluator walks these so a
    user's phone push fires from the worker even with their browser closed."""
    users = _read_users(path or _USERS_PATH)
    out = {key: _coerce_config(value) for key, value in users.items()}
    out.setdefault(DEFAULT_USER, _coerce_config(None))
    return out


def _coerce_config(raw: Any) -> dict:
    """DEFAULT_CONFIG overlaid with whatever valid fields ``raw`` carries.

    Unknown keys and malformed values are dropped silently: a hand-edited or
    truncated config file degrades to the defaults, never to a crash or - the
    quieter failure - an alert feature that stops firing with no sign why.
    """
    merged = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if not isinstance(raw, Mapping):
        return merged
    frames = raw.get("timeframes")
    if isinstance(frames, Mapping):
        for name in ALERT_TIMEFRAMES:
            held = frames.get(name)
            if not isinstance(held, Mapping):
                continue
            if isinstance(held.get("armed"), bool):
                merged["timeframes"][name]["armed"] = held["armed"]
            try:
                threshold = float(held.get("threshold"))
                if math.isfinite(threshold) and 1.0 <= threshold <= 50.0:
                    merged["timeframes"][name]["threshold"] = threshold
            except (TypeError, ValueError):
                pass
    try:
        cooldown = float(raw.get("cooldownMinutes"))
        if math.isfinite(cooldown) and 1.0 <= cooldown <= 240.0:
            merged["cooldownMinutes"] = cooldown
    except (TypeError, ValueError):
        pass
    if isinstance(raw.get("sound"), bool):
        merged["sound"] = raw["sound"]
    window = raw.get("pushWindow")
    if isinstance(window, Mapping):
        for edge in ("start", "end"):
            value = window.get(edge)
            # bool is an int subclass; True coercing to minute 1 would be a
            # silently wrong window, so it counts as garbage -> default kept.
            if isinstance(value, bool):
                continue
            try:
                minute = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= minute <= 1439:
                merged["pushWindow"][edge] = minute
    topic = raw.get("ntfyTopic")
    if isinstance(topic, str) and len(topic) <= 80:
        # ntfy topic charset; anything else silently keeps the old value's
        # default (off) rather than posting alerts to a malformed URL.
        cleaned = topic.strip()
        if cleaned == "" or all(c.isalnum() or c in "-_" for c in cleaned):
            merged["ntfyTopic"] = cleaned
    if isinstance(raw.get("marketTurnPush"), bool):
        merged["marketTurnPush"] = raw["marketTurnPush"]
    return merged


def load_config(path: Path | None = None) -> dict:
    target = path or _CONFIG_PATH
    try:
        return _coerce_config(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return _coerce_config(None)


def save_config(raw: Any, path: Path | None = None) -> dict:
    """Validate ``raw`` against the schema, persist, and return the result."""
    merged = _coerce_config(raw)
    target = path or _CONFIG_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    except OSError:
        pass  # in-memory config still applies for this process's lifetime
    return merged


def _tape_is_live(payload: Any, now_epoch: float) -> bool:
    stamp = payload.get("tapeAsOf") if isinstance(payload, Mapping) else None
    if not stamp:
        return False
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now_epoch - parsed.timestamp()) <= FRESH_TAPE_SECONDS


def push_window_allows(config: Mapping, now_et: datetime | None = None) -> bool:
    """Is the phone push allowed at ``now_et`` under this user's window?

    Pure: no clock read when ``now_et`` is given, so tests pin exact minutes.
    The window is half-open [start, end) in ET wall-clock minutes: at the
    default 555/930 a 9:15:00 alert pushes and a 15:30:00 alert does not.
    A malformed window in a hand-edited config falls back to the defaults --
    same degrade-never-crash rule as the rest of the schema -- because a
    trader would rather get one off-hours buzz than silently lose the 9:16
    one to a typo.
    """
    window = config.get("pushWindow") if isinstance(config, Mapping) else None
    if not isinstance(window, Mapping):
        window = DEFAULT_CONFIG["pushWindow"]
    try:
        start = int(window.get("start"))
        end = int(window.get("end"))
    except (TypeError, ValueError):
        start = DEFAULT_CONFIG["pushWindow"]["start"]
        end = DEFAULT_CONFIG["pushWindow"]["end"]
    if now_et is None:
        now_et = datetime.now(PUSH_WINDOW_ZONE)
    minute = now_et.hour * 60 + now_et.minute
    return start <= minute < end


def push_ntfy(alerts: list, topic: str, *, timeout: float = 6.0) -> None:
    """Post each alert to ntfy so the trader's phone gets a real notification.

    Best-effort by design: a push failure must never break alert detection or
    the board (the banner + sound still work), so every exception is swallowed.
    One POST per alert - they are rare by construction (cooldown + edge
    trigger), so there is no batching to get wrong.
    """
    if not topic:
        return
    import urllib.request

    for alert in alerts:
        try:
            pct = alert.get("pctChange")
            pct_text = ""
            if isinstance(pct, (int, float)) and math.isfinite(float(pct)):
                pct_text = f" {'+' if pct >= 0 else ''}{float(pct):.2f}%"
            value = alert.get("value")
            value_text = f"{float(value):.1f}x" if isinstance(value, (int, float)) else "?"
            body = (
                f"RVOL {alert.get('timeframe')} {value_text}{pct_text}"
                f"  ({alert.get('list') or 'scan'})"
            )
            request = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=body.encode("utf-8"),
                headers={
                    "Title": f"MOMO {alert.get('symbol')}",
                    "Priority": "high",
                    "Tags": "zap",
                },
                method="POST",
            )
            urllib.request.urlopen(request, timeout=timeout).close()
        except Exception:  # noqa: BLE001 - push is best-effort, never fatal
            pass


def detect(
    current: Any,
    previous: Any,
    config: Mapping,
    cooldown_ledger: dict[str, float],
    *,
    now_epoch: float | None = None,
) -> list[dict]:
    """Momo alerts for one fresh build. Mutates ``cooldown_ledger`` in place.

    ``previous`` must be the prior COMPLETED build of the same list; passing
    None (cold start) emits nothing, same rule as the strip - no baseline,
    no events, no restart re-announcements.
    """
    now = time.time() if now_epoch is None else float(now_epoch)
    if previous is None or not isinstance(current, Mapping):
        return []
    if not _tape_is_live(current, now):
        return []

    passing = {
        str(row.get("symbol") or "").upper()
        for row in (current.get("rows") or [])
        if isinstance(row, Mapping) and row.get("scanPass")
    }
    if not passing:
        return []

    cooldown_seconds = float(config.get("cooldownMinutes", 15)) * 60.0
    frames = config.get("timeframes") or {}
    alerts: list[dict] = []
    for name in ALERT_TIMEFRAMES:
        held = frames.get(name) or {}
        if not held.get("armed"):
            continue
        crossings = fastlane.rvol_spikes(
            current,
            threshold=held.get("threshold", 3.0),
            previous=previous,
            timeframes=(name,),
        )
        for event in crossings:
            symbol = event["symbol"]
            if symbol not in passing:
                continue
            fired_at = cooldown_ledger.get(symbol)
            if fired_at is not None and (now - fired_at) < cooldown_seconds:
                continue
            cooldown_ledger[symbol] = now
            alerts.append(
                {
                    "type": "momo_alert",
                    "symbol": symbol,
                    "timeframe": name,
                    "value": event.get("value"),
                    "threshold": held.get("threshold", 3.0),
                    "pctChange": event.get("pctChange"),
                    "list": current.get("list"),
                    "at": event.get("at"),
                }
            )
    return alerts


def _default_poster(topic: str, title: str, body: str, tags: str) -> None:
    """One POST to ntfy. Raises on failure - test_push NEEDS the truth."""
    import urllib.request

    request = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": tags},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=6).close()


def test_push(email: Any, path: Path | None = None, poster=None) -> dict:
    """Send one test notification to THIS user's topic and report honestly.

    Every other push in this module swallows failures on purpose - an alert
    channel must never break alert detection. This one is the opposite: its
    entire job is to answer "is my phone wired up?" at setup time, so a
    failure is a RESULT to report, not noise to hide. A typo'd topic that
    fails silently here fails silently forever.
    """
    config = load_user_config(email, path=path)
    topic = str(config.get("ntfyTopic") or "").strip()
    if not topic:
        return {
            "ok": False,
            "error": (
                "No phone channel saved yet. Press Generate first, then "
                "subscribe to that topic in the ntfy app, then try again."
            ),
        }
    send = poster or _default_poster
    try:
        send(
            topic,
            "AGX test notification",
            "If you can read this on your phone, your AGX alerts are working.",
            "white_check_mark",
        )
    except Exception as error:  # noqa: BLE001 - the verdict IS the product
        return {
            "ok": False,
            "topic": topic,
            "error": f"The push did not go through ({type(error).__name__}). "
                     "Check the server's internet connection and try again.",
        }
    return {"ok": True, "topic": topic}
