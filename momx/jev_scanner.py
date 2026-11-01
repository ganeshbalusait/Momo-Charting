"""TypeSafe Jev scores every scanner ticker (2026-10-02 TEST, his asks: "scanner each ticker
give me scores" and "scanner score using setup take Jev bot trades").

For each MATCHED row of a board (payload["rows"]), at any hour (09:30-15:30 ET it is
also refreshed every MAX_AGE_SECONDS; outside it only when the picture changes), Jev reads the same
moment-in-words the bot trades use (momx/jev_trade.describe, rule "scanner": no rule
has fired, the trade considered is the nearest short-dated OTM call / put) and answers
take / stand aside. The score is stamped on the row as ``row["jevRow"] = {"p", "at"}``
(the scanner's "Jev" column) and option_track turns a fresh score >= JEV_PICK_CUTOFF
into a paper trade under the rule "jev" (the "Jev pick" line on the Bot page).

Cost / load guard: a ticker is re-asked only when its scanner picture changes
(fingerprint of grade, RVOL / Skittles / squeeze colours, 5m state, momentum colour,
%-change bucket, headline) and at least RESCORE_SECONDS after its last score, or when
the score is older than MAX_AGE_SECONDS; at most MAX_SUBMITS_PER_APPLY per build.
~2-4k calls a day, about $0.10-0.20. Every score is appended to
<root>/momx_jev_scanner/<day>.jsonl so the scores can be checked against what the
stock did next. No key -> nothing is sent (JevTradeJudge). Never raises into a build.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_jev_scanner"
FROM_MIN, UNTIL_MIN = 9 * 60 + 30, 15 * 60 + 30
RESCORE_SECONDS = 10 * 60
MAX_AGE_SECONDS = 30 * 60
MAX_SUBMITS_PER_APPLY = 40
STAMP_FRESH_SECONDS = 45 * 60          # a score older than this is not shown


def _m(v: Any) -> Mapping:
    return v if isinstance(v, Mapping) else {}


def fingerprint(row: Mapping) -> str:
    """What has to change before Jev is asked again."""
    m5 = _m(row.get("m5"))
    rv = _m(row.get("rvol"))
    sk = _m(row.get("skittles"))
    sq = _m(row.get("sqz"))
    parts = [
        str(_m(row.get("grade")).get("letter")),
        ",".join(str(_m(rv.get(tf)).get("bg")) for tf in ("5m", "15m", "30m", "1h")),
        ",".join(str(_m(sk.get(tf)).get("bg")) for tf in ("2h", "4h", "D")),
        ",".join(str(_m(c).get("bg")) for c in sq.values()),
        str(m5.get("chart")), str(m5.get("pattern")), str(m5.get("state")),
        str(_m(m5.get("sqzMom")).get("color")),
        str(round(float(row.get("pctChange") or 0))),
        str(_m(row.get("news")).get("headline"))[:60],
    ]
    return "|".join(parts)


class JevScannerBook:
    def __init__(self, directory: Path, *, direction: str = "bull", judge: Any = None,
                 clock=time.time) -> None:
        self.directory = Path(directory)
        self.side = "bear" if str(direction).lower() == "bear" else "bull"
        self._judge = judge
        self._clock = clock
        self._lock = threading.RLock()
        self._day = ""
        self._scores: dict[tuple[str, str], dict] = {}
        self._inflight: set[tuple[str, str]] = set()

    def _judge_or_new(self) -> Any:
        if self._judge is None:
            from momx.jev_trade import JevTradeJudge
            self._judge = JevTradeJudge()
        return self._judge

    def _log(self, rec: dict) -> None:
        try:
            folder = self.directory / DIRNAME
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / f"{self._day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass

    def score(self, board: str, symbol: str) -> dict | None:
        with self._lock:
            return dict(self._scores.get((board, symbol)) or {}) or None

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            day = local.date().isoformat()
            ts = local.timestamp()
            minute = local.hour * 60 + local.minute
            matched = [r for r in (_m(payload).get("rows") or []) if isinstance(r, dict) and isinstance(r.get("symbol"), str)]
            everyone = matched + [r for r in (_m(payload).get("rest") or []) if isinstance(r, dict)]
            spy = next((r for r in everyone if r.get("symbol") == "SPY"), None)
            with self._lock:
                if day != self._day:
                    self._day, self._scores, self._inflight = day, {}, set()
                # Scored at ANY time (his "I don't see any values", 20:48 ET 10-02):
                # outside the session a row is re-read only when its picture changes,
                # and its last score stays on screen with the time it was read.
                in_session = local.weekday() < 5 and FROM_MIN <= minute <= UNTIL_MIN
                if True:
                    submitted = 0
                    for row in matched:
                        if submitted >= MAX_SUBMITS_PER_APPLY:
                            break
                        key = (str(board), row["symbol"])
                        if key in self._inflight:
                            continue
                        # Stale row (its 5m volume bar is old - the scanner has not refreshed
                        # it yet, APLD 10-02 09:44): wait for the fresh picture.
                        if in_session:
                            from momx.jev_trade import RVOL_STALE_MINUTES, rvol_lag_minutes
                            lag = rvol_lag_minutes(row, local.isoformat())
                            if lag is not None and lag > RVOL_STALE_MINUTES:
                                continue
                        fp = fingerprint(row)
                        prev = self._scores.get(key)
                        age = ts - prev["ts"] if prev else None
                        due = (prev is None or (prev["fp"] != fp and age >= RESCORE_SECONDS)
                               or (in_session and age >= MAX_AGE_SECONDS))
                        if due and self._submit(key, row, spy, local, fp):
                            submitted += 1
                for row in everyone:
                    got = self._scores.get((str(board), row.get("symbol")))
                    if got and (not in_session or ts - got["ts"] <= STAMP_FRESH_SECONDS):
                        row["jevRow"] = {"p": got["p"], "at": got["at"], "price": got.get("price")}
                    else:
                        row.pop("jevRow", None)
        except Exception:  # noqa: BLE001 - Jev never costs a scan
            return

    def _submit(self, key: tuple[str, str], row: Mapping, spy: Mapping | None, local: datetime, fp: str) -> bool:
        from momx.jev_trade import DESCRIBE_VERSION, describe
        stamp = local.replace(microsecond=0).isoformat()
        price = row.get("last")
        trade = {"rule": "scanner", "symbol": key[1], "at": stamp, "stockPrice": price}
        try:
            state = describe(trade, row, spy, self.side, self._day)
        except Exception:  # noqa: BLE001
            return False
        day = self._day

        def done(result: dict) -> None:
            with self._lock:
                self._inflight.discard(key)
                if self._day != day:
                    return
                rec = {"p": round(float(result["jevP"]), 4), "at": stamp, "ts": local.timestamp(), "fp": fp,
                       "price": price, "v": DESCRIBE_VERSION}
                self._scores[key] = rec
                self._log({"board": key[0], "symbol": key[1], "side": self.side, **{k: rec[k] for k in ("p", "at", "price", "v")},
                           "model": result.get("jevModel"),
                           "factors": {k: result.get(k) for k in ("regime", "direction", "pressure", "quality", "risk", "news")}})

        self._inflight.add(key)
        if not self._judge_or_new().submit(state, self.side, done):
            self._inflight.discard(key)
            return False
        return True


__all__ = ["JevScannerBook", "fingerprint"]
