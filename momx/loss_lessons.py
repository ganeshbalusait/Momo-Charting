"""Loss lessons (2026-11-09): every evening the AI studies the paper bot's LOSING
trades, one strategy at a time, and writes down what it sees and what it would
try. His "All strategies use AI improve lost trades using our LLM models. Like
tsla fsd or human robot" - and his "Don't block any trade using AI".

So this is a study loop, not a gate:

* Input: REAL Alpaca paper fills only (the paper-mirror day files), closed, from
  the Scorecard's fresh start day. Per strategy and side: the losers, with the
  winners as the comparison.
* The numbers are counted here in code (where the losses cluster: exit reason,
  time of day, spread, how far the fill was from the order). The AI is given
  those counts and a sample of the losing trades and asked for one lesson, one
  change to try and how to test it. It may not quote a count that is not there.
* Fewer than MIN_LOSSES losing trades = "not enough yet", and no AI call.
* Output: artifacts/momx_ai_filter/paper/lessons.json, shown on the Scorecard.
  A suggestion changes nothing by itself. Nothing in this module is read by any
  entry or exit decision.

At most one AI call per strategy and side per evening (six with the three Level
strategies), through the app's own AI engine (agents/ai_provider.py).
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ACCOUNTS = ("paper3", "paper5")
#: A strategy needs this many losing real fills before the AI is asked.
MIN_LOSSES = 10
#: Losing trades shown to the AI in full (the counts cover all of them).
SAMPLE = 12
#: The evening study starts after this ET time on a weekday (any time on a weekend).
STUDY_AFTER = (17, 0)
CHECK_SECONDS = 1800.0
FILE = "lessons.json"
NOTE = "Suggestions only. Nothing here blocks, changes or places a trade."

SCHEMA = {"type": "object", "properties": {
    "lesson": {"type": "string"}, "proposedChange": {"type": "string"},
    "howToTest": {"type": "string"}, "bucket": {"type": "string"}, "group": {"type": "string"},
    "evidenceCount": {"type": "integer"}},
    "required": ["lesson", "proposedChange", "howToTest", "bucket", "group", "evidenceCount"]}
SYSTEM = ("You review an options paper-trading bot's LOSING trades for one strategy. You are given exact counts and a "
          "sample of the losing trades. Use only those facts. Never invent a number. Say what the losing trades have "
          "in common that the winning trades have less of, in plain words a trader understands, in two sentences at "
          "most. Then propose ONE change to try and ONE way to test it on future paper trades. The change is a "
          "suggestion for the trader; you cannot block or change any trade. If the counts show no clear pattern, say "
          "exactly that and propose to keep collecting trades. Name the ONE table your lesson rests on in 'bucket' "
          "(when, spread, exit or fill) and copy that table's exact group text into 'group'; evidenceCount is that "
          "group's losses. If there is no clear pattern, set bucket to 'none', group to '' and evidenceCount to 0.")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _minutes(a: Any, b: Any) -> float | None:
    try:
        return (datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds() / 60
    except (TypeError, ValueError):
        return None


def load_fills(artifacts: Path, start_day: str) -> list[dict]:
    """Every closed, really-filled paper trade signalled on or after start_day,
    newest file first so a carried order's last state wins. Tagged with its account."""
    out = []
    for account in ACCOUNTS:
        seen: set = set()
        folder = Path(artifacts) / "momx_paper_mirror" / account
        for path in sorted(folder.glob("20*.json"), reverse=True):
            try:
                rows = json.loads(path.read_text(encoding="utf-8")).get("rows")
            except (OSError, ValueError, AttributeError):
                continue
            for key, r in (rows.items() if isinstance(rows, dict) else ()):
                if key in seen or not isinstance(r, dict):
                    continue
                seen.add(key)
                if (r.get("status") == "closed" and _num(r.get("buyFill")) and _num(r.get("pl")) is not None
                        and str(r.get("at") or "")[:10] >= start_day):
                    out.append({**r, "account": account})
    return out


def _when(r: Mapping) -> str:
    """Which part of the session the signal came in."""
    try:
        at = datetime.fromisoformat(str(r.get("at"))).astimezone(ET)
    except (TypeError, ValueError):
        return "unknown time"
    m = at.hour * 60 + at.minute
    return "first 30 minutes" if m < 600 else "10:00-11:00" if m < 660 else "afternoon" if m >= 780 else "midday"


def _spread(r: Mapping) -> str:
    s = _num(r.get("spreadAtOrder"))
    return "spread unknown" if s is None else "spread under 10%" if s < .10 else "spread 10-25%" if s < .25 else "spread over 25%"


def _exit(r: Mapping) -> str:
    sells = [s for s in (r.get("sells") or []) if isinstance(s, dict)]
    return str(sells[-1].get("why") or "exit reason unknown")[:60] if sells else "exit reason unknown"


def _slip(r: Mapping) -> str:
    fill, limit = _num(r.get("buyFill")), _num(r.get("botEntry"))
    if fill is None or not limit or limit <= 0:
        return "fill vs signal unknown"
    over = fill / limit - 1
    return "filled 10%+ above the signal price" if over >= .10 else "filled near the signal price"


BUCKETS = (("when", _when), ("spread", _spread), ("exit", _exit), ("fill", _slip))


def facts(losers: list[dict], winners: list[dict]) -> dict:
    """The counts the lesson must rest on. Each bucket: how many losers and winners fell in it."""
    out: dict[str, Any] = {
        "losses": len(losers), "wins": len(winners),
        "netPnl": round(sum(r["pl"] for r in losers + winners), 2),
        "avgLoss": round(statistics.fmean(r["pl"] for r in losers), 2) if losers else None,
        "avgWin": round(statistics.fmean(r["pl"] for r in winners), 2) if winners else None,
    }
    held = [m for m in (_minutes(r.get("buyFillAt") or r.get("buyAt"), r.get("closedAt")) for r in losers) if m is not None and m >= 0]
    out["medianMinutesHeldLosers"] = round(statistics.median(held)) if held else None
    for name, bucket in BUCKETS:
        table: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in losers:
            table[bucket(r)][0] += 1
        for r in winners:
            table[bucket(r)][1] += 1
        out[name] = [{"group": k, "losses": v[0], "wins": v[1]} for k, v in sorted(table.items(), key=lambda kv: (-kv[1][0], kv[0]))]
    return out


def biggest_cluster(f: Mapping) -> dict | None:
    """The single bucket holding the most losers where losers clearly outnumber
    winners - the plain-numbers lesson used when the AI gives no usable answer."""
    best = None
    for name, _ in BUCKETS:
        for row in f.get(name) or []:
            if "unknown" in row["group"] or row["losses"] < 3 or row["losses"] <= row["wins"]:
                continue
            if best is None or row["losses"] > best["losses"]:
                best = {**row, "bucket": name}
    return best


def _sample(losers: list[dict]) -> list[dict]:
    worst = sorted(losers, key=lambda r: r["pl"])[:SAMPLE]
    return [{"symbol": r.get("symbol"), "account": r.get("account"), "pl": round(r["pl"], 2), "when": _when(r),
             "spread": _spread(r), "exit": _exit(r), "fill": _slip(r), "delta": r.get("delta"),
             "minutesHeld": (lambda m: round(m) if m is not None else None)(
                 _minutes(r.get("buyFillAt") or r.get("buyAt"), r.get("closedAt")))} for r in worst]


def _default_ask(system: str, user: str) -> dict:
    from agents.ai_provider import claude_first_provider
    return claude_first_provider(effort="low").complete(system=system, user=user, max_tokens=700, schema=SCHEMA)


def _text(v: Any, limit: int) -> str:
    return " ".join(str(v or "").split())[:limit]


def study(side: str, rule: str, losers: list[dict], winners: list[dict], ask: Callable[[str, str], dict]) -> dict:
    """One strategy's lesson. Never raises; an AI failure falls back to the plain counts."""
    f = facts(losers, winners)
    base = {"side": side, "rule": rule, "losses": f["losses"], "wins": f["wins"], "netPnl": f["netPnl"], "facts": f}
    if len(losers) < MIN_LOSSES:
        return {**base, "status": "collecting", "source": "none",
                "lesson": f"Not enough losing trades yet ({len(losers)} of {MIN_LOSSES}).", "proposedChange": "", "howToTest": ""}
    cluster = biggest_cluster(f)
    plain = {**base, "status": "numbers", "source": "numbers only",
             "lesson": (f"{cluster['losses']} of the {f['losses']} losing trades were in the group '{cluster['group']}' "
                        f"({cluster['wins']} winning trades were)." if cluster else
                        "The losing trades do not cluster in any one group yet."),
             "proposedChange": "", "howToTest": ""}
    user = json.dumps({"strategy": rule, "side": "puts" if side == "bear" else "calls", "counts": f,
                       "worstLosingTrades": _sample(losers)}, separators=(",", ":"))
    try:
        answer = ask(SYSTEM, user) or {}
    except Exception as exc:  # noqa: BLE001 - the study must never break the worker
        return {**plain, "aiError": f"{type(exc).__name__}"[:60]}
    data = answer.get("data") if isinstance(answer, dict) else None
    if not (isinstance(answer, dict) and answer.get("ok") and isinstance(data, dict)):
        return {**plain, "aiError": _text((answer or {}).get("error") if isinstance(answer, dict) else "no answer", 120) or "no answer"}
    lesson, change, test = _text(data.get("lesson"), 420), _text(data.get("proposedChange"), 320), _text(data.get("howToTest"), 320)
    cited = _num(data.get("evidenceCount"))
    bucket, group = str(data.get("bucket") or ""), str(data.get("group") or "")
    mismatch = {**plain, "aiError": "the AI's answer did not match the counts, so it was not used"}
    if not lesson or cited is None:
        return mismatch
    if bucket == "none":
        # The honest "no clear pattern" answer: allowed, and it may cite nothing.
        if cited != 0:
            return mismatch
        basis = "no single group stands out in the counts"
    else:
        # The lesson must rest on a group that is really in the counts, and may
        # not claim more losing trades for it than the counts show.
        row = next((g for g in (f.get(bucket) or []) if g["group"] == group), None) if bucket in dict(BUCKETS) else None
        if row is None or cited < 1 or cited > row["losses"]:
            return mismatch
        basis = f"{row['losses']} of {f['losses']} losing trades and {row['wins']} of {f['wins']} winning trades were '{group}'"
    return {**base, "status": "lesson", "source": _text(answer.get("provider") or "AI", 40), "lesson": lesson,
            "proposedChange": change, "howToTest": test, "evidenceCount": int(cited), "basis": basis}


def run(artifacts: Path, start_day: str, ask: Callable[[str, str], dict] | None = None, now: datetime | None = None) -> dict:
    """Study every strategy that has real closed fills since start_day and save the result."""
    now = (now or datetime.now(ET)).astimezone(ET)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    seen: set = set()
    for r in load_fills(Path(artifacts), start_day):
        # Both accounts trade the same signals: study each signal once (the
        # first account's fill), or every count would be doubled.
        signal = (r.get("side") or "bull", r.get("symbol"), r.get("at"))
        if signal in seen:
            continue
        seen.add(signal)
        # One order counts for every Level it qualified for (paper_mirror row["levels"]).
        levels = r.get("levels")
        for rule in (levels if isinstance(levels, list) and levels else [r.get("rule")]):
            groups[(r.get("side") or "bull", str(rule))].append(r)
    lessons = []
    for (side, rule), rows in sorted(groups.items()):
        losers = [r for r in rows if r["pl"] < 0]
        winners = [r for r in rows if r["pl"] > 0]
        lessons.append({**study(side, rule, losers, winners, ask or _default_ask),
                        "studiedAt": now.replace(microsecond=0).isoformat()})
    doc = {"updatedAt": now.replace(microsecond=0).isoformat(), "day": now.date().isoformat(), "startDay": start_day,
           "minLosses": MIN_LOSSES, "note": NOTE,
           "lessons": sorted(lessons, key=lambda x: (-x["losses"], x["side"], x["rule"]))}
    folder = Path(artifacts) / "momx_ai_filter" / "paper"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (FILE + ".tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, folder / FILE)
            break
        except OSError:
            # Windows refuses the swap while the Scorecard is reading the file.
            # The answers are paid for: wait a moment instead of losing them.
            if attempt == 4:
                raise
            time.sleep(0.2)
    return doc


def read(artifacts: Path) -> dict | None:
    """The saved study for the Scorecard, without the bulky per-bucket counts. None = not run yet."""
    try:
        doc = json.loads((Path(artifacts) / "momx_ai_filter" / "paper" / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("lessons"), list):
        return None
    keep = ("side", "rule", "losses", "wins", "netPnl", "status", "source", "lesson", "proposedChange", "howToTest",
            "evidenceCount", "basis", "studiedAt")
    return {"updatedAt": doc.get("updatedAt"), "startDay": doc.get("startDay"), "minLosses": doc.get("minLosses"),
            "note": doc.get("note") or NOTE,
            "lessons": [{k: x.get(k) for k in keep} for x in doc["lessons"] if isinstance(x, dict)]}


def study_point(now: datetime) -> datetime:
    """The most recent weekday STUDY_AFTER (17:00 ET) at or before `now`: the
    close whose trades the next study should cover."""
    now = now.astimezone(ET)
    point = now.replace(hour=STUDY_AFTER[0], minute=STUDY_AFTER[1], second=0, microsecond=0)
    while point > now or point.weekday() >= 5:
        point -= timedelta(days=1)
    return point


def due(artifacts: Path, now: datetime) -> bool:
    """True once per trading evening: when no study has been saved since the
    latest weekday 17:00 ET. A weekend adds no trades, so it asks nothing new
    (it only catches up a Friday study that did not run)."""
    saved = read(artifacts)
    if not saved:
        return True
    try:
        last = datetime.fromisoformat(str(saved.get("updatedAt")))
        if not last < study_point(now):
            return False
    except (TypeError, ValueError):
        return True
    # A market holiday looks like a weekday: with no paper file touched since
    # the last study there is nothing new to ask the AI about.
    for account in ACCOUNTS:
        for path in (Path(artifacts) / "momx_paper_mirror" / account).glob("20*.json"):
            try:
                if path.stat().st_mtime > last.timestamp():
                    return True
            except OSError:
                continue
    return False


_STARTED = False


def start(artifacts: Path, start_day: Callable[[], str]) -> None:
    """One quiet background thread in the scanner worker. Off with AGX_LOSS_LESSONS=0."""
    global _STARTED
    if _STARTED or os.environ.get("AGX_LOSS_LESSONS", "1").strip() == "0":
        return
    _STARTED = True

    def loop() -> None:
        while True:
            try:
                if due(Path(artifacts), datetime.now(ET)):
                    run(Path(artifacts), start_day())
            except Exception as exc:  # noqa: BLE001 - never take the worker down
                print(f"[loss-lessons] {type(exc).__name__}: {exc}", flush=True)
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=loop, name="loss-lessons", daemon=True).start()
