"""How the trader actually trades, read out of his own closed trades.

Nothing else in the app can do this. The scanner sees the market, the chart
sees a symbol; only the journal knows that his 09:00 entries pay and his 14:00
entries do not. This module turns the closed rows of that journal into a short
list of lessons in his own trading vocabulary.

Two rules shape the design.

1. Arithmetic in Python, interpretation in the model. Win rate, average win,
   average loss and every grouping (setup, hour of day, side, weekday, hold
   time, symbol) are computed here and only the AGGREGATES are sent. Models
   are bad at arithmetic over hundreds of rows and it costs a fortune in
   tokens. The model's job is to say what the numbers mean.

2. Noise is the real failure mode. With twelve trades "you lose on Tuesdays"
   is a coin flip with a story attached. Every lesson must state the number of
   trades behind it, no lesson may rest on fewer than ``MIN_GROUP_TRADES``,
   and anything the model returns that breaks either rule is dropped HERE -
   the prompt asks, the code enforces.

No SDK is imported at module import time and no failure escapes: a missing key,
a dead provider or unparseable JSON all come back as ``available: False`` with a
plain-English ``reason`` that is shown to a trader, not to a developer.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)

EASTERN = ZoneInfo("America/New_York")

# No lesson may be drawn from fewer trades than this. Five is already thin;
# it is the floor at which a pattern stops being a single bad afternoon.
MIN_GROUP_TRADES = 5

# How many lessons are worth reading in one sitting.
MAX_LESSONS = 5

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Hold-time buckets, in minutes. A trader thinks in these, not in seconds.
_HOLD_BUCKETS: tuple[tuple[float, str], ...] = (
    (5.0, "under 5 min"),
    (30.0, "5-30 min"),
    (120.0, "30 min - 2 hr"),
    (390.0, "2 hr - 1 session"),
    (float("inf"), "held overnight or longer"),
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_TRADE_COUNT_RE = re.compile(r"(\d+)\s+(?:of\s+\d+\s+)?trades?\b", re.IGNORECASE)
_N_EQUALS_RE = re.compile(r"\bn\s*=\s*(\d+)", re.IGNORECASE)
_SAMPLE_SIZE_RE = re.compile(r"sample size(?:\s+of)?\s+(\d+)", re.IGNORECASE)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """pandas NaN and empty strings both mean "not set" here."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _as_float(value: Any) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_utc(stamp: Any) -> datetime | None:
    stamp = _clean(stamp)
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
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


def _round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _short_label(value: Any, limit: int = 40) -> str | None:
    """Group labels must be short tokens, never a free-text blob.

    Journal ``notes`` can hold a whole paragraph of plan text. A label that
    long is both useless as a grouping and a way for raw row content to leak
    into the prompt, so anything oversized is refused outright.
    """
    value = _clean(value)
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text or len(text) > limit:
        return None
    return text


# --------------------------------------------------------------------------
# normalising journal rows
# --------------------------------------------------------------------------
def normalize_trade(row: Any) -> dict | None:
    """One journal row -> the closed-trade fields the statistics need.

    Accepts both equity rows (``symbol``/``setup_name``) and option rows
    (``underlying_symbol``/``structure``). Returns None for anything that is
    not a finished trade: no close stamp, no P&L, or still open.
    """
    if not isinstance(row, dict):
        return None

    status = str(_clean(row.get("status")) or "").lower()
    if "open" in status and "closed" not in status:
        return None

    opened = _parse_utc(row.get("opened_at") or row.get("openedAt"))
    closed = _parse_utc(row.get("closed_at") or row.get("closedAt"))
    if closed is None:
        return None

    pnl = _as_float(row.get("pnl"))
    if pnl is None:
        return None

    symbol = _clean(row.get("symbol")) or _clean(row.get("underlying_symbol"))
    # The already-normalised keys come first so this is idempotent: the loader
    # normalises, then lessons() normalises again, and a second pass must not
    # quietly downgrade every option row to "unspecified setup" / equity.
    setup = (
        _short_label(row.get("setup"))
        or _short_label(row.get("setup_name"))
        or _short_label(row.get("strategy_family"))
        or _short_label(row.get("structure"))
        or _short_label(row.get("trigger_source"))
        or "unspecified setup"
    )
    exit_reason = (
        _short_label(row.get("exitReason"))
        or _short_label(row.get("notes"))
        or "unspecified exit"
    )
    book = "option" if _clean(row.get("underlying_symbol")) else str(_clean(row.get("book")) or "equity")

    hold_minutes = None
    if opened is not None:
        hold_minutes = max((closed - opened).total_seconds() / 60.0, 0.0)

    reference = opened or closed
    local = reference.astimezone(EASTERN)

    return {
        "id": _clean(row.get("id")) or _clean(row.get("client_order_id")),
        "symbol": str(symbol or "").upper() or "UNKNOWN",
        "side": str(_clean(row.get("side")) or "unspecified").lower(),
        "setup": setup,
        "exitReason": exit_reason,
        "pnl": pnl,
        "openedAt": opened.isoformat() if opened else None,
        "closedAt": closed.isoformat(),
        "hour": local.hour,
        "weekday": _WEEKDAYS[local.weekday()],
        "holdMinutes": hold_minutes,
        "book": book,
    }


def normalize_trades(rows: Iterable[Any]) -> list[dict]:
    normalized = []
    for row in rows or []:
        trade = normalize_trade(row)
        if trade is not None:
            normalized.append(trade)
    return normalized


# --------------------------------------------------------------------------
# the loader (thin, read-only, never writes)
# --------------------------------------------------------------------------
def _default_db_path() -> str:
    try:
        import config  # local import: config binds paths at import time

        return str(config.DATABASE_PATH)
    except Exception:  # pragma: no cover - config is always importable in app
        return "database/trades.db"


def load_closed_trades(limit: int = 500, db_path: str | None = None) -> list[dict]:
    """Closed rows from the real journal, newest first.

    Opened read-only (``mode=ro``) on purpose. This repository has an incident
    where the live trades.db was truncated out of band; a reporting feature has
    no business holding a writable handle on it.
    """
    path = str(db_path or _default_db_path())
    rows: list[dict] = []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        LOGGER.warning("journal lessons could not open %s: %s", path, exc)
        return []
    try:
        connection.row_factory = sqlite3.Row
        for table in ("trades", "option_trades"):
            try:
                cursor = connection.execute(
                    f"SELECT * FROM {table} WHERE closed_at IS NOT NULL "
                    f"ORDER BY closed_at DESC LIMIT {int(limit)}"
                )
            except sqlite3.Error as exc:
                LOGGER.warning("journal lessons could not read %s: %s", table, exc)
                continue
            for record in cursor.fetchall():
                rows.append(dict(record))
    finally:
        connection.close()

    trades = normalize_trades(rows)
    trades.sort(key=lambda item: item.get("closedAt") or "", reverse=True)
    return trades[: int(limit)]


# --------------------------------------------------------------------------
# statistics - computed here, never asked of the model
# --------------------------------------------------------------------------
def _summarize(trades: list[dict]) -> dict:
    pnls = [float(trade["pnl"]) for trade in trades]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    scratches = [value for value in pnls if value == 0]
    decided = len(wins) + len(losses)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "scratches": len(scratches),
        # Win rate is over trades that actually made or lost money; exact
        # scratches are reported separately rather than diluting the rate.
        "winRatePct": _round((len(wins) / decided) * 100.0, 1) if decided else None,
        "avgWin": _round(_mean(wins)),
        "avgLoss": _round(_mean(losses)),
        "avgPnl": _round(_mean(pnls)),
        "totalPnl": _round(sum(pnls)),
        "profitFactor": _round(gross_win / gross_loss) if gross_loss else None,
        "expectancy": _round(sum(pnls) / len(pnls)) if pnls else None,
    }


def _group(trades: list[dict], key) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for trade in trades:
        label = key(trade)
        if label is None:
            continue
        buckets.setdefault(str(label), []).append(trade)
    rows = []
    for label, members in buckets.items():
        summary = _summarize(members)
        summary["group"] = label
        summary["reliable"] = summary["trades"] >= MIN_GROUP_TRADES
        rows.append(summary)
    rows.sort(key=lambda row: (-row["trades"], row["group"]))
    return rows


def _hold_bucket(trade: dict) -> str | None:
    minutes = trade.get("holdMinutes")
    if minutes is None:
        return None
    for ceiling, label in _HOLD_BUCKETS:
        if minutes < ceiling:
            return label
    return _HOLD_BUCKETS[-1][1]


def compute_aggregates(trades: list[dict]) -> dict:
    """Everything the model is allowed to see. No row-level fields, ever."""
    return {
        "overall": _summarize(trades),
        "minTradesPerLesson": MIN_GROUP_TRADES,
        "groups": {
            "setup": _group(trades, lambda t: t.get("setup")),
            "hourOfDay": _group(trades, lambda t: f"{int(t['hour']):02d}:00 ET"),
            "dayOfWeek": _group(trades, lambda t: t.get("weekday")),
            "side": _group(trades, lambda t: t.get("side")),
            "book": _group(trades, lambda t: t.get("book")),
            "holdTime": _group(trades, _hold_bucket),
            "symbol": _group(trades, lambda t: t.get("symbol")),
            "exitReason": _group(trades, lambda t: t.get("exitReason")),
        },
    }


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are a trading coach reading one trader's own closed-trade statistics. "
    "You are given AGGREGATES only - counts, win rates and averages that have "
    "already been computed for you. Do not recompute them and do not invent any "
    "number that is not in the data.\n\n"
    "Hard rules, applied to every lesson you write:\n"
    "1. Every claim MUST state the number of trades it rests on, written as "
    "'N trades', and MUST also be reported in the sampleSize field.\n"
    f"2. NEVER draw a lesson from fewer than {MIN_GROUP_TRADES} trades. A group "
    f"with fewer than {MIN_GROUP_TRADES} trades is noise, not a pattern, no "
    "matter how extreme its win rate looks. Groups are marked reliable:false "
    "when they fall below that floor - do not cite them.\n"
    "3. Never compare or rank two groups unless BOTH clear that floor.\n"
    f"4. Write at most {MAX_LESSONS} lessons. Each of finding, evidence and "
    "suggestion is ONE sentence.\n"
    "5. 'finding' is what the numbers say about how he trades, 'evidence' is "
    "the numbers themselves with the trade count, 'suggestion' is one concrete "
    "change he could make.\n"
    "6. If nothing in the data clears the floor, return an empty lessons list. "
    "An empty list is a correct answer and is far better than a guess.\n"
    "7. Plain trading language. He is a trader, not a developer or a "
    "statistician."
)

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding": {"type": "string"},
                    "evidence": {"type": "string"},
                    "suggestion": {"type": "string"},
                    "sampleSize": {"type": "integer"},
                },
                "required": ["finding", "evidence", "suggestion", "sampleSize"],
            },
        }
    },
    "required": ["lessons"],
}


def build_user_prompt(aggregates: dict) -> str:
    return (
        "Closed-trade statistics for this trader.\n"
        "Dollar figures are realised P&L. avgLoss is negative. "
        "'reliable' is false when a group has fewer than "
        f"{MIN_GROUP_TRADES} trades and must not be cited.\n\n"
        f"{json.dumps(aggregates, indent=2, sort_keys=True, default=str)}\n\n"
        "Write the lessons."
    )


# --------------------------------------------------------------------------
# validating what comes back
# --------------------------------------------------------------------------
def _one_sentence(text: Any, limit: int = 320) -> str:
    collapsed = " ".join(str(text or "").split())
    if not collapsed:
        return ""
    first = _SENTENCE_SPLIT.split(collapsed)[0].strip()
    if len(first) > limit:
        first = first[:limit].rstrip() + "..."
    return first


def _stated_sample_size(lesson: dict, text: str) -> int | None:
    """The trade count the model claims, from the field or from the sentence."""
    for key in ("sampleSize", "sample_size", "trades", "tradeCount"):
        value = lesson.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    counts = [int(match) for match in _TRADE_COUNT_RE.findall(text)]
    counts += [int(match) for match in _N_EQUALS_RE.findall(text)]
    counts += [int(match) for match in _SAMPLE_SIZE_RE.findall(text)]
    if not counts:
        return None
    return min(counts)


def _group_index(aggregates: dict) -> list[tuple[str, int]]:
    index: dict[str, int] = {}
    for rows in (aggregates.get("groups") or {}).values():
        for row in rows or []:
            label = str(row.get("group") or "")
            if len(label) < 2:
                continue
            count = int(row.get("trades") or 0)
            index[label] = min(index.get(label, count), count)
    return sorted(index.items(), key=lambda item: -len(item[0]))


def _smallest_cited_group(text: str, group_index: list[tuple[str, int]]) -> int | None:
    """The trade count of the thinnest group this lesson names."""
    smallest: int | None = None
    for label, count in group_index:
        pattern = r"(?<![\w])" + re.escape(label) + r"(?![\w])"
        if re.search(pattern, text, re.IGNORECASE):
            smallest = count if smallest is None else min(smallest, count)
    return smallest


def validate_lessons(raw: Any, aggregates: dict) -> tuple[list[dict], list[dict]]:
    """Keep only lessons that state a sample size and clear the floor.

    Returns (kept, dropped). ``dropped`` carries a reason so the caller can say
    honestly that the model over-reached rather than silently hiding it.
    """
    if isinstance(raw, dict):
        raw = raw.get("lessons")
    if not isinstance(raw, list):
        return [], []

    group_index = _group_index(aggregates)
    kept: list[dict] = []
    dropped: list[dict] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        finding = _one_sentence(item.get("finding"))
        evidence = " ".join(str(item.get("evidence") or "").split())
        suggestion = _one_sentence(item.get("suggestion"))
        if not finding:
            continue
        lesson = {"finding": finding, "evidence": evidence, "suggestion": suggestion}
        haystack = f"{finding} {evidence} {suggestion}"

        stated = _stated_sample_size(item, haystack)
        if stated is None:
            dropped.append({**lesson, "droppedBecause": "no sample size stated"})
            continue
        if stated < MIN_GROUP_TRADES:
            dropped.append({
                **lesson,
                "droppedBecause": f"only {stated} trades, below the {MIN_GROUP_TRADES}-trade floor",
            })
            continue

        cited = _smallest_cited_group(haystack, group_index)
        if cited is not None and cited < MIN_GROUP_TRADES:
            dropped.append({
                **lesson,
                "droppedBecause": f"cites a group with only {cited} trades",
            })
            continue

        kept.append(lesson)
        if len(kept) >= MAX_LESSONS:
            break

    return kept, dropped


# --------------------------------------------------------------------------
# provider seams (kept as module functions so tests can replace them)
# --------------------------------------------------------------------------
def _registry_provider(preferred: str | None = None):
    try:
        from agents.ai_provider import get_provider  # noqa: PLC0415
    except Exception:
        return None
    try:
        return get_provider(preferred)
    except Exception as exc:  # a broken registry must not break the panel
        LOGGER.warning("journal lessons could not resolve an AI provider: %s", exc)
        return None


def _registry_status_message() -> str:
    try:
        from agents.ai_provider import provider_status  # noqa: PLC0415

        status = provider_status() or {}
        message = str(status.get("message") or "").strip()
        if message:
            return message
    except Exception:
        pass
    return "No AI key set. Add OPENAI_API_KEY to .env to turn on journal lessons."


def _result(available: bool, reason: str, **extra: Any) -> dict:
    payload = {
        "available": bool(available),
        "reason": reason,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "provider": None,
        "lessons": [],
        "sampleSize": 0,
        "stats": None,
        "dropped": [],
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------
# the feature
# --------------------------------------------------------------------------
def lessons(
    trades: list[dict],
    *,
    provider: Any = None,
    min_trades: int = 10,
    preferred: str | None = None,
) -> dict:
    """What his own closed trades say about how he trades.

    ``trades`` are journal rows (equity or option). Statistics are computed
    here; the model only interprets them. Never raises.
    """
    min_trades = max(int(min_trades or 0), 1)
    closed = normalize_trades(trades)
    sample_size = len(closed)

    if sample_size == 0:
        return _result(
            True,
            "No closed trades in your journal yet, so there is nothing to learn from.",
            sampleSize=0,
        )

    aggregates = compute_aggregates(closed)

    # Below the floor we do not call the model at all. Paying for an opinion on
    # nine trades is the exact mistake this feature exists to stop him making.
    if sample_size < min_trades:
        return _result(
            True,
            f"Only {sample_size} closed trades so far. Lessons need at least "
            f"{min_trades} closed trades before a pattern means anything.",
            sampleSize=sample_size,
            stats=aggregates,
        )

    if provider is None:
        provider = _registry_provider(preferred)
    if provider is None:
        return _result(
            False,
            _registry_status_message(),
            sampleSize=sample_size,
            stats=aggregates,
        )

    provider_name = str(getattr(provider, "name", "") or "") or None
    try:
        if hasattr(provider, "available") and not provider.available():
            return _result(
                False,
                _registry_status_message(),
                sampleSize=sample_size,
                stats=aggregates,
                provider=provider_name,
            )
    except Exception as exc:
        return _result(
            False,
            f"The AI provider could not be checked: {exc}",
            sampleSize=sample_size,
            stats=aggregates,
            provider=provider_name,
        )

    user_prompt = build_user_prompt(aggregates)
    try:
        response = provider.complete(
            system=SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=1200,
            schema=_RESPONSE_SCHEMA,
        )
    except Exception as exc:  # the contract says it never raises; trust nothing
        return _result(
            False,
            f"The AI provider failed: {exc}",
            sampleSize=sample_size,
            stats=aggregates,
            provider=provider_name,
        )

    if not isinstance(response, dict):
        return _result(
            False,
            "The AI provider returned an unreadable response.",
            sampleSize=sample_size,
            stats=aggregates,
            provider=provider_name,
        )

    provider_name = str(response.get("provider") or provider_name or "") or None
    if not response.get("ok"):
        error = str(response.get("error") or "").strip() or "unknown error"
        return _result(
            False,
            f"The AI provider could not answer: {error}",
            sampleSize=sample_size,
            stats=aggregates,
            provider=provider_name,
        )

    data = response.get("data")
    if not isinstance(data, (dict, list)):
        try:
            data = json.loads(str(response.get("text") or ""))
        except (TypeError, ValueError):
            data = None
    if data is None:
        return _result(
            False,
            "The AI answered in a format this panel could not read.",
            sampleSize=sample_size,
            stats=aggregates,
            provider=provider_name,
        )

    kept, dropped = validate_lessons(data, aggregates)

    reason = f"Read {sample_size} closed trades."
    if dropped:
        reason += (
            f" {len(dropped)} suggested lesson(s) were dropped for resting on "
            f"fewer than {MIN_GROUP_TRADES} trades."
        )
    if not kept:
        reason += " Nothing in this history clears the evidence bar yet."

    return _result(
        True,
        reason,
        sampleSize=sample_size,
        stats=aggregates,
        lessons=kept,
        dropped=dropped,
        provider=provider_name,
    )


def lessons_from_journal(
    *,
    provider: Any = None,
    min_trades: int = 10,
    limit: int = 500,
    db_path: str | None = None,
    preferred: str | None = None,
) -> dict:
    """``lessons`` wired to the real journal store, for API callers."""
    return lessons(
        load_closed_trades(limit=limit, db_path=db_path),
        provider=provider,
        min_trades=min_trades,
        preferred=preferred,
    )
