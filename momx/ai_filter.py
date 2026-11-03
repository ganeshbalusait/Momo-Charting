"""Shadow AI filter for the Bot (2026-10-17, his "why we don't use deep learning
... train your self" -> "Yes").

A small, honest model - not deep learning: L2-regularised logistic regression
(numpy only) on what is known AT ENTRY of each Bot trade (rule, side, time of
day, momentum, option price / spread / delta / moneyness, expected move, risk
check, days to expiry, Jev score, OI-wall distance). Label: the trade won at
the mid with Strategy exit (exitPrice). Missing exits remain unpriced.

It scores trades (aiP = estimated chance of a win); it places no orders.
The separate paper entry gate uses its score plus ChatGPT/Ollama approval.
Retrained nightly at RETRAIN_AT in its own process (python -m momx.ai_filter
--train) on the newest TRAIN_WINDOW_DAYS days; graded walk-forward on the newest
WF_TEST_DAYS days (train on the days before D, score D, roll) so the report
never grades a day the model saw.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import numpy as np
from momx import scanner_learning, chart_learning

ET = ZoneInfo("America/New_York")
MIN_TRAIN_DAYS = 5
L2 = 1.0
EPOCHS = 400
LR = 0.2
HOURS = ((9, 30, 10, 0), (10, 0, 11, 0), (11, 0, 12, 0), (12, 0, 14, 0), (14, 0, 16, 0))
#: The market features are switched on or off BY THE DATA (2026-10-18, his "no
#: guess work"): every night the model is graded on unseen days with and without
#: them; the market set takes over after MARKET_ON_NIGHTS nights ahead by
#: MARKET_MARGIN, and steps back after MARKET_OFF_NIGHTS nights behind. First
#: test (15 days): without 0.539, with 0.524 - so it starts OFF.
MARKET_ON_NIGHTS = 3
MARKET_OFF_NIGHTS = 2
MARKET_MARGIN = 0.005
MARKET_KEYS = ("spyPct", "spy30", "spyVwap", "qqqPct", "qqq30", "breadth", "avgPct")
SETUP_KEYS = ("pct", "rvol5m", "rvol15m", "rvol30m", "rvol1h", "skitAvg", "sqzOn", "adx5m", "di5m",
              "adx30m", "di30m", "hiLo", "grade", "hot", "rs3", "reasons")
JEV_KEYS = ("regime", "direction", "pressure", "quality", "risk", "news")
NUMERIC = ("logEntry", "spreadPct", "delta", "moneyness", "emPct", "roi2em", "riskOk", "dte",
           "jevP", "jevMissing", "wallGap",
           # The Record column at entry: this rule's win rate / ROI over the 10 trading days before.
           "recWin", "recRoi", "recN",
           # The Setup column at entry (option_track.setup_snapshot) and Jev's read of the setup.
           *("s_" + k for k in SETUP_KEYS), *("j_" + k for k in JEV_KEYS),
           *scanner_learning.SCANNER_NUMERIC, *chart_learning.NUMERIC,
           )
#: The same plus the MARKET at entry (2026-10-18, "same as tsla": the weather camera).
NUMERIC_MARKET = NUMERIC + tuple("m_" + k for k in MARKET_KEYS)
#: Learning from mistakes (2026-10-17, his "learn from the mistake and improve"):
#: recent days count more (half-life RECENCY_HALF_LIFE days) and the recent
#: trades the first fit got WRONG are weighted MISTAKE_BOOST times on the refit.
RECENCY_HALF_LIFE = 10.0
MISTAKE_DAYS = 5
MISTAKE_BOOST = 2.0
REC_DAYS = 10
#: Bounded nightly work (2026-10-18, before running unattended): the model
#: trains on the newest TRAIN_WINDOW_DAYS days (recency weighting makes older
#: days count ~nothing anyway: 0.5 ** (40 / 10) = 6%) and is graded on the newest
#: WF_TEST_DAYS days. Without these the retrain grew with days x days and the
#: whole 180-day history was loaded into memory - hours and GBs within months.
TRAIN_WINDOW_DAYS = 40
WF_TEST_DAYS = 10
#: When the nightly retrain runs (ET): after the Bot's 15:50 close quotes are
#: priced (they retry until 16:28).
RETRAIN_AT = (16, 40)
#: The trade fields the model reads - everything else is dropped on load.
TRADE_KEYS = ("rule", "symbol", "at", "entryAt", "entry", "entryBid", "entryAsk", "exitPrice", "last", "status",
              "badQuote", "strike", "spot", "stockPrice", "delta", "expectedMove", "roi2em", "riskOk", "expiry",
              "jevP", "jevFactors", "momentum", "board", "callWalls", "putWalls", "setup", "market", "best", "close",
              "scannerInputs", "levelSignal")


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ts(v: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)).astimezone(ET)
    except (TypeError, ValueError):
        return None


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file + rename: a crash or a reader (the backup, the
    scorer) never sees half a file. Windows refuses the rename while another
    process holds the old file open, so it retries briefly."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2 * (attempt + 1))
    os.replace(tmp, path)


def label(t: Mapping) -> int | None:
    """1 = won at the mid with his exit, 0 = did not; None = no result yet."""
    if t.get("badQuote"):
        return None
    entry = _num(t.get("entry"))
    out = _num(t.get("exitPrice"))
    # A closed trade without its strategy-exit quote is unpriced, not a
    # training outcome at an unrelated last mark (which may be much later).
    if not entry or entry <= 0 or out is None:
        return None
    return 1 if out > entry else 0


def features(t: Mapping, side: str) -> dict[str, Any]:
    """Only what is known when the Bot buys - no exit, best or close fields."""
    entry = _num(t.get("entry")) or 0.0
    bid, ask = _num(t.get("entryBid")), _num(t.get("entryAsk"))
    spot = _num(t.get("spot")) or _num(t.get("stockPrice"))
    strike = _num(t.get("strike"))
    bear = side == "bear"
    at = _ts(t.get("entryAt") or t.get("at"))
    hour = "h?"
    if at is not None:
        m = at.hour * 60 + at.minute
        for i, (h1, m1, h2, m2) in enumerate(HOURS):
            if h1 * 60 + m1 <= m < h2 * 60 + m2:
                hour = f"h{i}"
    expiry = _ts(str(t.get("expiry") or "") + "T16:00:00-04:00") if t.get("expiry") else None
    dte = None
    if expiry is not None and at is not None:
        dte = max(0.0, (expiry - at).total_seconds() / 86400)
    walls = t.get("putWalls" if bear else "callWalls") or []
    gap = None
    if spot and walls:
        strikes = [_num(w.get("strike")) for w in walls if isinstance(w, Mapping)]
        strikes = [s for s in strikes if s]
        if strikes:
            gap = min(abs(s - spot) for s in strikes) / spot
    jev = _num(t.get("jevP"))
    setup = t.get("setup") if isinstance(t.get("setup"), Mapping) else {}
    jf = t.get("jevFactors") if isinstance(t.get("jevFactors"), Mapping) else {}
    extra = {"s_" + k: _num(setup.get(k)) for k in SETUP_KEYS}
    extra.update(scanner_learning.features(t, side))
    extra.update(chart_learning.features(t))
    if bear:            # the same picture, read the bear way: a falling stock and a DI- lead are "good"
        for k in ("s_pct", "s_di5m", "s_di30m"):
            if extra.get(k) is not None:
                extra[k] = -extra[k]
    extra.update({"j_" + k: _num(jf.get(k)) for k in JEV_KEYS})
    extra.update(recWin=_num(t.get("_recWin")), recRoi=_num(t.get("_recRoi")), recN=_num(t.get("_recN")))
    mk = dict(t.get("_mkt") or {})
    live = t.get("market") if isinstance(t.get("market"), Mapping) else {}
    for k in ("breadth", "avgPct", "spyPct", "qqqPct"):
        if mk.get(k) is None and live.get(k) is not None:
            mk[k] = live[k]
    m = {"m_" + k: _num(mk.get(k)) for k in MARKET_KEYS}
    if bear:            # a falling market is the "good" weather for puts
        for k in ("m_spyPct", "m_spy30", "m_spyVwap", "m_qqqPct", "m_qqq30", "m_avgPct"):
            if m.get(k) is not None:
                m[k] = -m[k]
        if m.get("m_breadth") is not None:
            m["m_breadth"] = 1 - m["m_breadth"]
    extra.update(m)
    return {**extra,
        "rule": f"{side}:{t.get('rule')}", "side": side, "hour": hour,
        "momentum": str(t.get("momentum") or "?"), "board": str(t.get("board") or "?"),
        "logEntry": math.log(entry) if entry > 0 else 0.0,
        "spreadPct": ((ask - bid) / entry) if (ask is not None and bid is not None and entry > 0) else None,
        "delta": abs(_num(t.get("delta")) or 0.0) or None,
        "moneyness": ((strike / spot - 1) * (-1 if bear else 1)) if (strike and spot) else None,
        "emPct": ((_num(t.get("expectedMove")) or 0) / spot) if spot else None,
        "roi2em": _num(t.get("roi2em")),
        "riskOk": 1.0 if t.get("riskOk") else 0.0,
        "dte": dte,
        "jevP": jev if jev is not None else 0.0,
        "jevMissing": 0.0 if jev is not None else 1.0,
        "wallGap": gap,
    }


CATEGORICAL = ("rule", "side", "hour", "momentum", "board")


class Model:
    """Logistic regression with one-hot categoricals and standardised numerics."""

    def __init__(self, numeric: tuple | list | None = None) -> None:
        self.numeric = tuple(numeric or NUMERIC)
        self.cats: dict[str, list[str]] = {}
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}
        self.w: np.ndarray | None = None
        self.base = 0.5

    def _vector(self, f: Mapping[str, Any]) -> list[float]:
        x = [1.0]
        for c in CATEGORICAL:
            for v in self.cats.get(c, []):
                x.append(1.0 if f.get(c) == v else 0.0)
        for n in self.numeric:
            v = f.get(n)
            x.append(0.0 if v is None else max(-5.0, min(5.0, (v - self.mean[n]) / self.std[n])))
        return x

    def _matrix(self, rows: list[Mapping[str, Any]]) -> np.ndarray:
        """_vector for many rows at once - the same numbers, built with numpy
        (2026-10-18: the per-value Python loops were most of the nightly retrain)."""
        n = len(rows)
        cols = [np.ones((n, 1))]
        for c in CATEGORICAL:
            levels = self.cats.get(c, [])
            if not levels:
                continue
            at = {v: i for i, v in enumerate(levels)}
            idx = np.fromiter((at.get(f.get(c), -1) for f in rows), dtype=np.int64, count=n)
            block = np.zeros((n, len(levels)))
            hit = np.nonzero(idx >= 0)[0]
            block[hit, idx[hit]] = 1.0
            cols.append(block)
        if self.numeric:
            raw = np.array([[f.get(k) for k in self.numeric] for f in rows], dtype=float).reshape(n, len(self.numeric))
            z = np.clip((raw - np.array([self.mean[k] for k in self.numeric]))
                        / np.array([self.std[k] for k in self.numeric]), -5.0, 5.0)
            z[np.isnan(z)] = 0.0                  # a missing value sits at the mean
            cols.append(z)
        return np.hstack(cols)

    def predict_matrix(self, X: np.ndarray) -> np.ndarray:
        if self.w is None:
            return np.full(len(X), self.base)
        return 1 / (1 + np.exp(-np.clip(X @ self.w, -30, 30)))

    def predict_many(self, rows: list[Mapping[str, Any]]) -> list[float]:
        return list(self.predict_matrix(self._matrix(rows)))

    def prepare(self, rows: list[Mapping[str, Any]]) -> "Model":
        """The input encoding learnt from these rows - which categories get a
        column (seen 20+ times), each number's mean and spread. No weights."""
        for c in CATEGORICAL:
            counts: dict[Any, int] = {}
            for f in rows:
                v = f.get(c)
                counts[v] = counts.get(v, 0) + 1
            self.cats[c] = sorted(v for v, k in counts.items() if k >= 20)
        for n in self.numeric:
            vals = np.array([f.get(n) for f in rows], dtype=float)
            vals = vals[~np.isnan(vals)]
            self.mean[n] = float(vals.mean()) if len(vals) else 0.0
            self.std[n] = float(vals.std()) or 1.0 if len(vals) else 1.0
        return self

    def fit_matrix(self, X: np.ndarray, ys: list[int], weights: np.ndarray | None = None) -> "Model":
        """Gradient descent on an already-encoded matrix (rows from prepare())."""
        y = np.array(ys, dtype=float)
        self.base = float(y.mean()) if len(y) else 0.5
        sw = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=float)
        w = np.zeros(X.shape[1])
        for _ in range(EPOCHS):
            p = 1 / (1 + np.exp(-(X @ w)))
            grad = X.T @ ((p - y) * sw) / len(y)
            grad[1:] += L2 * w[1:] / len(y)
            w -= LR * grad
        self.w = w
        return self

    def fit(self, rows: list[dict], ys: list[int], weights: np.ndarray | None = None) -> "Model":
        return self.prepare(rows).fit_matrix(self._matrix(rows), ys, weights)

    def predict(self, f: Mapping[str, Any]) -> float:
        if self.w is None:
            return self.base
        z = float(np.dot(self.w, np.array(self._vector(f))))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))

    def to_json(self) -> dict:
        return {"numeric": list(self.numeric), "cats": self.cats, "mean": self.mean, "std": self.std, "base": self.base,
                "w": None if self.w is None else [round(float(v), 6) for v in self.w]}

    @classmethod
    def from_json(cls, doc: Mapping) -> "Model":
        m = cls(doc.get("numeric") or [k for k in NUMERIC if k in doc["mean"]])
        m.cats, m.mean, m.std = dict(doc["cats"]), dict(doc["mean"]), dict(doc["std"])
        m.base = float(doc.get("base") or 0.5)
        m.w = None if doc.get("w") is None else np.array(doc["w"], dtype=float)
        return m


# ---------------------------------------------------------------------- data
def load_days(roots: Mapping[str, Path], first: str | None = None,
              dataset: Path | None = None) -> dict[str, list[tuple[str, dict]]]:
    """{day: [(side, trade), ...]} from the Bot's day files - plus, from the
    PERMANENT dataset (his "do permanent data store to train the model"), every
    older day whose Bot file has been pruned (option_track keeps 180 days), so
    the model trains on every day ever recorded."""
    out: dict[str, list[tuple[str, dict]]] = {}
    if dataset is not None:
        import gzip

        for f in sorted(Path(dataset).glob("20*.jsonl.gz")):
            day = f.name[:10]
            if first and day < first:
                continue
            try:
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    rows = [json.loads(line) for line in fh if line.strip()]
            except (OSError, ValueError, EOFError):   # a torn file is skipped, never fatal
                continue
            # Stored rows carry their entry-time features and result; _xy reads them as-is.
            out[day] = [(r.get("side") or "bull", {"_features": r.get("features"), "_win": r.get("win"),
                                                    "entry": 1.0, "exitPrice": 1.0 + (r.get("ret") or 0.0),
                                                    "rule": r.get("rule"), "symbol": r.get("symbol"),
                                                    "at": r.get("at"), "status": "closed"})
                        for r in rows if r.get("features") is not None and r.get("win") is not None]
    bot_days: dict[str, list[tuple[str, dict]]] = {}
    for side, root in roots.items():
        for f in sorted(Path(root).glob("20*.json")):
            day = f.stem
            if first and day < first:
                continue
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for t in doc.get("trades") or []:
                if isinstance(t, dict):
                    bot_days.setdefault(day, []).append((side, {k: t[k] for k in TRADE_KEYS if k in t}))
    out.update(bot_days)            # a day the Bot still has is read from the Bot's own file
    return dict(sorted(out.items()))


class MarketTape:
    """SPY / QQQ 5-minute bars kept on disk (market_5m.json) so every trade - past
    and live - can be given the market picture at its entry minute: % change on
    the day, the last 30 minutes, distance from the day's VWAP."""

    SYMBOLS = ("SPY", "QQQ")
    #: Bars older than this are dropped on refresh: training reads at most ~60
    #: trading days, and older days live on in the permanent dataset.
    KEEP_DAYS = 120

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "market_5m.json"
        self.bars: dict[str, list[list[float]]] = {}
        try:
            self.bars = {k: v for k, v in json.loads(self.path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            pass
        self._fetched_at = 0.0
        self._index: dict[str, tuple] = {}

    def refresh(self, days: int = 30, min_gap: float = 240.0) -> None:
        """Add new bars from the app's feed (never raises; at most every min_gap s)."""
        if time.time() - self._fetched_at < min_gap:
            return
        self._fetched_at = time.time()
        try:
            from momx import feed
            # 5m keeps only the last few sessions; the 30m tape (3 weeks) fills older days.
            for suffix, res in (("", feed.fetch_5m(list(self.SYMBOLS), days=days, schwab_volume=False)),
                                (":30m", feed.fetch_30m(list(self.SYMBOLS), days=days))):
                self._merge(suffix, res)
            cut = time.time() - self.KEEP_DAYS * 86400
            self.bars = {k: [b for b in v if b[0] >= cut] for k, v in self.bars.items()}
            self._index = {}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.bars), encoding="utf-8")
        except Exception:  # noqa: BLE001 - no market picture just means "unknown"
            pass

    def _merge(self, suffix: str, res: Any) -> None:
            for sym in self.SYMBOLS:
                df = (res.bars or {}).get(sym)
                if df is None or len(df) == 0:
                    continue
                d = df.reset_index()
                cols = {str(c).lower(): c for c in d.columns}
                tcol = cols.get("timestamp") or cols.get("time") or d.columns[0]
                import pandas as pd
                ts = pd.to_datetime(d[tcol], utc=True).astype("int64") // 10**9
                new = {int(a): [int(a), float(o), float(h), float(lo), float(c), float(v)]
                       for a, o, h, lo, c, v in zip(ts, d[cols["open"]], d[cols["high"]], d[cols["low"]],
                                                    d[cols["close"]], d[cols["volume"]])}
                old = {int(b[0]): b for b in self.bars.get(sym + suffix, [])}
                old.update(new)
                self.bars[sym + suffix] = [old[k] for k in sorted(old)]

    def _idx(self, key: str) -> tuple:
        """(bars, open times, sorted ET dates, {date: (first, last + 1)}) for one tape - built once."""
        hit = self._index.get(key)
        if hit is None:
            bars = self.bars.get(key) or []
            spans: dict = {}
            for i, b in enumerate(bars):
                d = datetime.fromtimestamp(b[0], ET).date()
                spans[d] = (spans[d][0], i + 1) if d in spans else (i, i + 1)
            hit = self._index[key] = (bars, [b[0] for b in bars], sorted(spans), spans)
        return hit

    def _prev_close(self, key: str, day: Any) -> float | None:
        """The last REGULAR-session close before `day`: the last bar opened in
        9:30-16:00 on the newest earlier date that had a session. (It used to take
        the last bar before today's 9:30 - with pre-market bars that is 9:25 today,
        so "SPY % on the day" really measured the move since 9:25.)"""
        import bisect

        bars, times, dates, spans = self._idx(key)
        for d in reversed(dates[:bisect.bisect_left(dates, day)]):
            lo, hi = spans[d]
            base = datetime.combine(d, datetime.min.time(), ET)
            a = bisect.bisect_left(times, base.replace(hour=9, minute=30).timestamp(), lo, hi)
            b = bisect.bisect_left(times, base.replace(hour=16).timestamp(), lo, hi)
            if b > a:                       # a weekend's overnight-only bars are skipped
                return bars[b - 1][4]
        return None

    def _view(self, sym: str, ts: float) -> dict:
        import bisect

        day0 = datetime.fromtimestamp(ts, ET).date()
        key, span = sym, 300
        if day0 not in self._idx(sym)[3]:    # 5m keeps only recent sessions; older days read the 30m tape
            key, span = sym + ":30m", 1800
        bars, times, _dates, _spans = self._idx(key)
        if not bars:
            return {}
        i = bisect.bisect_right(times, ts - span) - 1       # the last bar CLOSED by ts
        if i < 0:
            return {}
        day = datetime.fromtimestamp(bars[i][0], ET).date()
        start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30).timestamp()
        prev_close = self._prev_close(key, day)
        if prev_close is None and key == sym:
            prev_close = self._prev_close(sym + ":30m", day)
        c = bars[i][4]
        j = bisect.bisect_right(times, ts - span - 1800) - 1
        today = bars[bisect.bisect_left(times, start):i + 1]
        vol = sum(b[5] for b in today)
        vwap = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in today) / vol if vol else None
        return {"pct": (c / prev_close - 1) * 100 if prev_close else None,
                "m30": (c / bars[j][4] - 1) * 100 if j >= 0 and bars[j][4] else None,
                "vwap": (c / vwap - 1) * 100 if vwap else None}

    def at(self, when: Any) -> dict:
        ts = None
        try:
            ts = datetime.fromisoformat(str(when)).timestamp()
        except (TypeError, ValueError):
            return {}
        spy, qqq = self._view("SPY", ts), self._view("QQQ", ts)
        return {k: v for k, v in {"spyPct": spy.get("pct"), "spy30": spy.get("m30"), "spyVwap": spy.get("vwap"),
                                   "qqqPct": qqq.get("pct"), "qqq30": qqq.get("m30")}.items() if v is not None}


def add_market(days: Mapping[str, list[tuple[str, dict]]], tape: "MarketTape") -> None:
    """Stamp _mkt (the market at entry) on every trade that has no stored features."""
    for items in days.values():
        for _, t in items:
            if "_features" not in t and "_mkt" not in t:
                t["_mkt"] = tape.at(t.get("entryAt") or t.get("at"))


def compact(days: dict[str, list[tuple[str, dict]]]) -> None:
    """Replace every finished trade by its entry-time features + result (the
    permanent dataset's row form), in place: features are computed ONCE, and
    the raw trade (walls, setup, factors) is let go. Run after add_records and
    add_market; trades without a result are dropped (nothing reads them)."""
    for day, items in days.items():
        out = []
        for side, t in items:
            if "_features" in t:
                out.append((side, t))
                continue
            y = label(t)
            if y is None:
                continue
            x = _num(t.get("exitPrice"))
            if x is None:
                x = _num(t.get("last"))
            out.append((side, {"_features": features(t, side), "_win": y, "entry": _num(t.get("entry")),
                               "exitPrice": x, "status": "closed", "rule": t.get("rule"), "symbol": t.get("symbol"),
                               "at": t.get("at"), "jevP": t.get("jevP"), "best": t.get("best"), "close": t.get("close")}))
        days[day] = out


def add_records(days: Mapping[str, list[tuple[str, dict]]], extra_history: Mapping[str, list] | None = None) -> None:
    """Stamp _recWin / _recRoi / _recN on every trade: its (side, rule) win rate
    and ROI over the REC_DAYS trading days BEFORE its day - the Record column,
    never the trade's own day or later. extra_history = older days to look back on."""
    hist = dict(extra_history or {})
    hist.update(days)
    names = sorted(hist)
    per_day: dict[str, dict[tuple, list]] = {}
    for d in names:
        acc: dict[tuple, list] = {}
        for side, t in hist[d]:
            y = label(t) if "_features" not in t else t.get("_win")
            e, x = _num(t.get("entry")), _num(t.get("exitPrice"))
            if x is None:
                x = _num(t.get("last"))
            if y is None or not e or x is None:
                continue
            a = acc.setdefault((side, t.get("rule")), [0, 0, 0.0, 0.0])
            a[0] += 1
            a[1] += int(y)
            a[2] += e
            a[3] += x - e
        per_day[d] = acc
    for k, d in enumerate(names):
        if d not in days:
            continue
        prior = names[max(0, k - REC_DAYS):k]
        for side, t in days[d]:
            if "_features" in t:
                continue
            n = w = 0
            cost = pl = 0.0
            for pd in prior:
                a = per_day[pd].get((side, t.get("rule")))
                if a:
                    n, w, cost, pl = n + a[0], w + a[1], cost + a[2], pl + a[3]
            t["_recN"] = n
            t["_recWin"] = (w / n) if n else None
            t["_recRoi"] = (pl / cost) if cost else None


def learning_weights(day_names: list[str], ys: list[int], preds: list[float] | None, train_days: list[str]) -> np.ndarray:
    """Recency weights, plus the mistake boost on recent wrong calls."""
    order = {d: i for i, d in enumerate(train_days)}
    last = len(train_days) - 1
    w = np.array([0.5 ** ((last - order.get(d, last)) / RECENCY_HALF_LIFE) for d in day_names], dtype=float)
    if preds is not None:
        recent = set(train_days[-MISTAKE_DAYS:])
        for i, (d, y, p) in enumerate(zip(day_names, ys, preds)):
            if d in recent and ((p >= 0.5) != (y == 1)):
                w[i] *= MISTAKE_BOOST
    return w * (len(w) / w.sum()) if w.sum() else w


def fit_learning(items: list[tuple[str, dict]], day_of: list[str], train_days: list[str],
                 numeric: tuple | None = None) -> "Model":
    """Fit, look at its own recent mistakes, refit with them weighted up."""
    rows, ys, kept = _xy(items)
    days_kept = [day_of[i] for i in range(len(items)) if _keep(items[i])]
    first = Model(numeric).prepare(rows)
    X = first._matrix(rows)                     # built once: both fits read the same rows
    first.fit_matrix(X, ys, learning_weights(days_kept, ys, None, train_days))
    preds = list(first.predict_matrix(X))
    second = Model(numeric)
    second.cats, second.mean, second.std = first.cats, first.mean, first.std
    return second.fit_matrix(X, ys, learning_weights(days_kept, ys, preds, train_days))


def _keep(item: tuple[str, dict]) -> bool:
    t = item[1]
    return ("_features" in t) or label(t) is not None


def _xy(items: Iterable[tuple[str, dict]]) -> tuple[list[dict], list[int], list[tuple[str, dict]]]:
    rows, ys, kept = [], [], []
    for side, t in items:
        if "_features" in t:                     # a dataset row / compacted trade (read-only)
            rows.append(t["_features"])
            ys.append(int(t["_win"]))
            kept.append((side, t))
            continue
        y = label(t)
        if y is None:
            continue
        f = t.get("_fc")
        if f is None:                            # computed once per trade, reused by every fit
            f = t["_fc"] = features(t, side)
        rows.append(f)
        ys.append(y)
        kept.append((side, t))
    return rows, ys, kept


def _auc(ps: list[float], ys: list[int]) -> float | None:
    pos = [p for p, y in zip(ps, ys) if y == 1]
    neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return None
    order = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    rank_sum, i = 0.0, 0
    while i < len(order):
        j = i
        while j < len(order) and order[j][0] == order[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rank_sum += avg * sum(1 for k in range(i, j) if order[k][1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def _roi(items: list[tuple[str, dict]]) -> tuple[int, float | None, float | None]:
    cost = pl = 0.0
    wins = n = 0
    for _, t in items:
        e = _num(t.get("entry"))
        x = _num(t.get("exitPrice"))
        if x is None:
            x = _num(t.get("last"))
        if not e or x is None:
            continue
        n += 1
        wins += x > e
        cost += e
        pl += x - e
    return n, (wins / n * 100 if n else None), (pl / cost * 100 if cost else None)


def walk_forward(days: Mapping[str, list[tuple[str, dict]]], cut: float = 0.55, numeric: tuple | None = None) -> dict:
    """Train on the days before each test day; score that day; pool the results."""
    names = list(days)
    ps_all, ys_all, picked, every, jev, sides = [], [], [], [], [], []
    last_day = None
    per_day = []
    for k, day in enumerate(names):
        if k < max(MIN_TRAIN_DAYS, len(names) - WF_TEST_DAYS):
            continue
        window = names[max(0, k - TRAIN_WINDOW_DAYS):k]
        train = [it for d in window for it in days[d]]
        day_of = [d for d in window for _ in days[d]]
        rows, ys, _ = _xy(train)
        if len(set(ys)) < 2:
            continue
        model = fit_learning(train, day_of, window, numeric)
        trows, tys, titems = _xy(days[day])
        ps = model.predict_many(trows)
        ps_all += ps
        ys_all += tys
        sides += [it[0] for it in titems]
        top = [it for it, p in zip(titems, ps) if p >= cut]
        picked += top
        every += titems
        jev += [it for it in titems if (_num(it[1].get("jevP")) or -1) >= 0.10]
        n, w, r = _roi(top)
        last_day = (day, list(zip(titems, ps)))
        # Running score up to and including this test day (the chart's line).
        side_auc = {}
        for sd in ("bull", "bear"):
            idx = [i for i, x in enumerate(sides) if x == sd]
            side_auc[sd] = _auc([ps_all[i] for i in idx], [ys_all[i] for i in idx])
        per_day.append({"day": day, "picked": n, "winRate": w, "roi": r, "all": len(titems),
                        "aucToDate": _auc(ps_all, ys_all), "bullToDate": side_auc["bull"],
                        "bearToDate": side_auc["bear"]})
    n_all, w_all, r_all = _roi(every)
    n_top, w_top, r_top = _roi(picked)
    n_jev, w_jev, r_jev = _roi(jev)
    # Bull and bear graded apart (2026-10-17): an edge on one side only shows here.
    by_side = {}
    for sd in ("bull", "bear"):
        idx = [i for i, x in enumerate(sides) if x == sd]
        n_s, w_s, r_s = _roi([it for it in every if it[0] == sd])
        n_p, w_p, r_p = _roi([it for it in picked if it[0] == sd])
        by_side[sd] = {"auc": _auc([ps_all[i] for i in idx], [ys_all[i] for i in idx]),
                       "all": {"trades": n_s, "winRate": w_s, "roi": r_s},
                       "aiPick": {"trades": n_p, "winRate": w_p, "roi": r_p}}
    return {"cut": cut, "testDays": len(per_day), "auc": _auc(ps_all, ys_all), "bySide": by_side,
            "mistakes": mistakes(last_day),
            "all": {"trades": n_all, "winRate": w_all, "roi": r_all},
            "aiPick": {"trades": n_top, "winRate": w_top, "roi": r_top},
            "jevBot": {"trades": n_jev, "winRate": w_jev, "roi": r_jev},
            "perDay": per_day}


def mistakes(last_day, top: int = 8) -> dict:
    """The newest graded day's worst calls - what the model got wrong and now
    weights up on its next retrain (his "learn from the mistake")."""
    if not last_day:
        return {}
    day, scored = last_day
    rows = []
    for (side, t), p in scored:
        e = _num(t.get("entry"))
        x = _num(t.get("exitPrice"))
        if x is None:
            x = _num(t.get("last"))
        if not e or x is None:
            continue
        rows.append({"side": side, "rule": t.get("rule"), "symbol": t.get("symbol"), "at": t.get("at"),
                     "p": round(p, 3), "ret": round((x / e - 1) * 100, 1)})
    bad_takes = sorted([r for r in rows if r["p"] >= 0.5 and r["ret"] <= 0], key=lambda r: (-r["p"], r["ret"]))[:top]
    missed = sorted([r for r in rows if r["p"] < 0.5 and r["ret"] > 0], key=lambda r: -r["ret"])[:top]
    return {"day": day, "badTakes": bad_takes, "missedWinners": missed,
            "wrong": sum(1 for r in rows if (r["p"] >= 0.5) != (r["ret"] > 0)), "total": len(rows)}


# ------------------------------------------------- neural-network challenger
def nn_fit(X: np.ndarray, y: np.ndarray, hidden=(32, 16), epochs: int = 40, lr: float = 0.01,
           l2: float = 1e-3, seed: int = 0, input_clip: float | None = None, activation: str = "relu"):
    """A small 2-hidden-layer neural network (ReLU, Adam, numpy only). Returns predict(X)."""
    rng = np.random.default_rng(seed)
    if activation not in {"relu", "leaky_relu"}:
        raise ValueError("Unsupported neural activation")
    if input_clip is not None:
        if not math.isfinite(input_clip) or input_clip <= 0:
            raise ValueError("Neural input clipping must be positive")
        X = np.clip(np.nan_to_num(X, nan=0, posinf=input_clip, neginf=-input_clip), -input_clip, input_clip)
    sizes = [X.shape[1], *hidden, 1]
    W = [rng.normal(0, np.sqrt(2 / a), (a, b)) for a, b in zip(sizes, sizes[1:])]
    B = [np.zeros(b) for b in sizes[1:]]
    mW, vW = [np.zeros_like(w) for w in W], [np.zeros_like(w) for w in W]
    mB, vB = [np.zeros_like(b) for b in B], [np.zeros_like(b) for b in B]
    t = 0
    for _ in range(epochs):
        idx = rng.permutation(len(y))
        for st in range(0, len(y), 512):
            bi = idx[st:st + 512]
            acts = [X[bi]]
            for i in range(len(W)):
                z = acts[-1] @ W[i] + B[i]
                acts.append((np.where(z >= 0, z, .01 * z) if activation == "leaky_relu" else np.maximum(z, 0))
                            if i < len(W) - 1 else 1 / (1 + np.exp(-np.clip(z, -30, 30))))
            g = (acts[-1][:, 0] - y[bi])[:, None] / len(bi)
            t += 1
            for i in reversed(range(len(W))):
                gW, gB = acts[i].T @ g + l2 * W[i], g.sum(0)
                if i:
                    derivative = np.where(acts[i] > 0, 1., .01) if activation == "leaky_relu" else acts[i] > 0
                    g = (g @ W[i].T) * derivative
                for P, G, M, V in ((W, gW, mW, vW), (B, gB, mB, vB)):
                    M[i] = 0.9 * M[i] + 0.1 * G
                    V[i] = 0.999 * V[i] + 0.001 * G * G
                    P[i] -= lr * (M[i] / (1 - 0.9 ** t)) / (np.sqrt(V[i] / (1 - 0.999 ** t)) + 1e-8)

    return NeuralNet(W, B, input_clip=input_clip, activation=activation)


class NeuralNet:
    """The trained network's weights - callable on a matrix, saveable to JSON."""

    def __init__(self, W: list, B: list, *, input_clip=None, activation="relu") -> None:
        self.W = [np.asarray(w, dtype=float) for w in W]
        self.B = [np.asarray(b, dtype=float) for b in B]
        self.input_clip, self.activation = input_clip, activation

    def __call__(self, Xt: np.ndarray) -> np.ndarray:
        a = Xt
        if self.input_clip is not None:
            a = np.clip(np.nan_to_num(a, nan=0, posinf=self.input_clip, neginf=-self.input_clip), -self.input_clip, self.input_clip)
        for i in range(len(self.W)):
            z = a @ self.W[i] + self.B[i]
            a = (np.where(z >= 0, z, .01 * z) if self.activation == "leaky_relu" else np.maximum(z, 0)) \
                if i < len(self.W) - 1 else 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        return a[:, 0]

    def to_json(self) -> dict:
        return {"W": [w.round(6).tolist() for w in self.W], "B": [b.round(6).tolist() for b in self.B],
                "inputClip": self.input_clip, "activation": self.activation}

    @classmethod
    def from_json(cls, doc: Mapping) -> "NeuralNet":
        return cls(doc["W"], doc["B"], input_clip=doc.get("inputClip"), activation=doc.get("activation", "relu"))


class NeuralModel:
    """The neural network with its input encoder: the same predict(features) as Model."""

    def __init__(self, enc: Model, net: NeuralNet) -> None:
        self.enc, self.net = enc, net

    def predict(self, f: Mapping[str, Any]) -> float:
        return float(self.net(self.enc._matrix([f]))[0])

    def to_json(self) -> dict:
        return {"enc": self.enc.to_json(), "net": self.net.to_json()}

    @classmethod
    def from_json(cls, doc: Mapping) -> "NeuralModel":
        return cls(Model.from_json(doc["enc"]), NeuralNet.from_json(doc["net"]))


# ------------------------------------------------- champion / challenger
#: His "why you cannot change deep learning model on your own" -> "Yes build it"
#: (2026-10-18): the nightly retrain promotes the neural network by itself when
#: it beats the live model on unseen days PROMOTE_NIGHTS nights in a row by at
#: least MARGIN of score, and demotes it after DEMOTE_NIGHTS nights behind.
PROMOTE_NIGHTS = 5
DEMOTE_NIGHTS = 3
MARGIN = 0.01


def decide_market(state: Mapping | None, base_auc: float | None, market_auc: float | None, stamp: str) -> dict:
    """Whether tonight's model uses the market features - decided by the data. Pure."""
    st = {"useMarket": False, "ahead": 0, "behind": 0, "log": []}
    st.update({k: v for k, v in dict(state or {}).items() if k in st})
    st["log"] = list(st.get("log") or [])
    if base_auc is None or market_auc is None:
        return st
    st["ahead"] = st["ahead"] + 1 if market_auc - base_auc >= MARKET_MARGIN else 0
    st["behind"] = st["behind"] + 1 if market_auc < base_auc else 0
    if not st["useMarket"] and st["ahead"] >= MARKET_ON_NIGHTS:
        st["useMarket"], st["behind"] = True, 0
        st["log"].append({"at": stamp, "event": "market view switched ON", "with": round(market_auc, 3), "without": round(base_auc, 3)})
    elif st["useMarket"] and st["behind"] >= MARKET_OFF_NIGHTS:
        st["useMarket"], st["ahead"] = False, 0
        st["log"].append({"at": stamp, "event": "market view switched OFF", "with": round(market_auc, 3), "without": round(base_auc, 3)})
    st["log"] = st["log"][-50:]
    return st


def decide_champion(state: Mapping | None, lr_auc: float | None, nn_auc: float | None, stamp: str) -> dict:
    """The next champion state from tonight's two unseen-day scores. Pure."""
    st = {"champion": "lr", "nnAhead": 0, "nnBehind": 0, "log": []}
    st.update({k: v for k, v in dict(state or {}).items() if k in st})
    st["log"] = list(st.get("log") or [])
    if lr_auc is None or nn_auc is None:
        return st
    ahead = nn_auc - lr_auc >= MARGIN
    behind = nn_auc < lr_auc
    st["nnAhead"] = st["nnAhead"] + 1 if ahead else 0
    st["nnBehind"] = st["nnBehind"] + 1 if behind else 0
    if st["champion"] == "lr" and st["nnAhead"] >= PROMOTE_NIGHTS:
        st["champion"] = "nn"
        st["log"].append({"at": stamp, "event": "neural net promoted", "nn": round(nn_auc, 3), "lr": round(lr_auc, 3)})
        st["nnBehind"] = 0
    elif st["champion"] == "nn" and st["nnBehind"] >= DEMOTE_NIGHTS:
        st["champion"] = "lr"
        st["log"].append({"at": stamp, "event": "neural net demoted", "nn": round(nn_auc, 3), "lr": round(lr_auc, 3)})
        st["nnAhead"] = 0
    st["log"] = st["log"][-50:]
    return st


def walk_forward_nn(days: Mapping[str, list[tuple[str, dict]]], numeric: tuple | None = None) -> dict:
    """The same walk-forward test as walk_forward(), for the neural network.
    Recorded beside the live model each night; it replaces it only when it
    wins on unseen days (his "store data for neural network", 2026-10-17)."""
    names = list(days)
    ps_all, ys_all, sides = [], [], []
    for k, day in enumerate(names):
        if k < max(MIN_TRAIN_DAYS, len(names) - WF_TEST_DAYS):
            continue
        window = names[max(0, k - TRAIN_WINDOW_DAYS):k]
        rows, ys, _ = _xy(it for d in window for it in days[d])
        if len(set(ys)) < 2:
            continue
        enc = Model(numeric).prepare(rows)          # its one-hot + scaling
        predict = nn_fit(enc._matrix(rows), np.array(ys, dtype=float))
        trows, tys, titems = _xy(days[day])
        if not trows:
            continue
        ps_all += list(predict(enc._matrix(trows)))
        ys_all += tys
        sides += [it[0] for it in titems]
    by_side = {}
    for sd in ("bull", "bear"):
        idx = [i for i, x in enumerate(sides) if x == sd]
        by_side[sd] = {"auc": _auc([ps_all[i] for i in idx], [ys_all[i] for i in idx])}
    return {"auc": _auc(ps_all, ys_all), "bySide": by_side}


# ------------------------------------------------- permanent training data
def save_dataset(days: Mapping[str, list[tuple[str, dict]]], directory: Path) -> int:
    """One gzip file per finished day of (features, label, result) rows. NEVER
    pruned - the Bot's own day files go after 180 days, this does not, so a
    neural network can later train on months of trades. Returns days written."""
    import gzip

    out = Path(directory) / "dataset"
    out.mkdir(parents=True, exist_ok=True)
    written = 0
    for day, items in days.items():
        path = out / f"{day}.jsonl.gz"
        if path.exists():
            continue
        tmp = path.with_name(path.name + ".tmp")     # renamed into place only when complete
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            for side, t in items:
                stored = "_features" in t
                y = t.get("_win") if stored else label(t)
                if y is None:
                    continue
                e = _num(t.get("entry"))
                x = _num(t.get("exitPrice"))
                if x is None:
                    x = _num(t.get("last"))
                fh.write(json.dumps({"day": day, "side": side, "rule": t.get("rule"), "symbol": t.get("symbol"),
                                     "at": t.get("at"), "features": t["_features"] if stored else features(t, side), "win": y,
                                     "ret": round(x / e - 1, 4) if e and x is not None else None,
                                     "best": _num(t.get("best")), "close": _num(t.get("close"))}) + "\n")
        os.replace(tmp, path)
        written += 1
    return written


# ---------------------------------------------------------------- live service
def _roots() -> dict[str, Path]:
    from config import ARTIFACTS_DIR
    base = Path(os.environ.get("AGX_MOMX_GRADE_DIR", "").strip() or ARTIFACTS_DIR)
    return {"bull": base / "momx_option_track", "bear": base / "bear" / "momx_option_track"}


def _dir() -> Path:
    from config import ARTIFACTS_DIR
    return Path(ARTIFACTS_DIR) / "momx_ai_filter"


def trade_key(side: str, t: Mapping) -> str:
    return f"{side}|{t.get('rule')}|{t.get('symbol')}|{t.get('at')}"


def _available_days(roots: Mapping[str, Path], dataset: Path) -> list[str]:
    names: set[str] = set()
    for root in roots.values():
        names.update(f.stem for f in Path(root).glob("20*.json"))
    names.update(f.name[:10] for f in Path(dataset).glob("20*.jsonl.gz"))
    return sorted(names)


def train_now(directory: Path | None = None, roots: Mapping[str, Path] | None = None,
              today: str | None = None, refresh_market: bool = False) -> dict:
    """Fit on the newest finished days, grade walk-forward, save model + report.

    refresh_market: also fetch SPY / QQQ bars through the app's feed. OFF by
    default - the nightly retrain runs in its own process and must never touch
    the broker (two processes refreshing the Schwab login clash); the scanner
    worker refreshes market_5m.json itself before starting it."""
    directory = Path(directory or _dir())
    roots = roots or _roots()
    today = today or datetime.now(ET).date().isoformat()
    names = [d for d in _available_days(roots, directory / "dataset") if d < today]
    need = TRAIN_WINDOW_DAYS + WF_TEST_DAYS + REC_DAYS
    first = names[-need] if len(names) > need else None
    days = load_days(roots, first=first, dataset=directory / "dataset")
    done = {d: v for d, v in days.items() if d < today}
    add_records(done)
    tape = MarketTape(directory)
    if refresh_market:
        tape.refresh()
    add_market(done, tape)
    compact(done)
    learning_coverage = scanner_learning.coverage(done)
    directory.mkdir(parents=True, exist_ok=True)
    base_rep = walk_forward(done, numeric=NUMERIC)
    try:
        market_auc = walk_forward(done, numeric=NUMERIC_MARKET).get("auc")
    except Exception:  # noqa: BLE001 - no market test tonight keeps the last choice
        market_auc = None
    try:
        prev_m = json.loads((directory / "features.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev_m = None
    feat = decide_market(prev_m, base_rep.get("auc"), market_auc, datetime.now(ET).replace(microsecond=0).isoformat())
    feat.update(withoutMarket=base_rep.get("auc"), withMarket=market_auc)
    _write_atomic(directory / "features.json", json.dumps(feat))
    numeric = NUMERIC_MARKET if feat["useMarket"] else NUMERIC
    report = base_rep if not feat["useMarket"] else walk_forward(done, numeric=numeric)
    report["features"] = feat
    report["scannerLearning"] = learning_coverage
    try:
        report["nn"] = walk_forward_nn(done, numeric)   # the deep-learning challenger
    except Exception as exc:  # noqa: BLE001 - the live model must still be saved
        report["nn"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    try:
        # Unsupervised research (market-regime clusters, anomaly autoencoder):
        # fit on earlier days, graded on later ones; never used by entries.
        from momx import unsupervised
        report["unsupervised"] = unsupervised.train_report(
            done, features, label, numeric, tuple("m_" + k for k in MARKET_KEYS))
        _write_atomic(directory / "unsupervised.json", json.dumps(report["unsupervised"], allow_nan=False))
    except Exception as exc:  # noqa: BLE001 - research must never cost the nightly model
        report["unsupervised"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    try:
        report["datasetDaysAdded"] = save_dataset(done, directory)
    except Exception:  # noqa: BLE001
        report["datasetDaysAdded"] = 0
    window = list(done)[-TRAIN_WINDOW_DAYS:]
    all_items = [it for d in window for it in done[d]]
    all_days = [d for d in window for _ in done[d]]
    rows, ys, _ = _xy(all_items)
    model = fit_learning(all_items, all_days, window, numeric) if len(set(ys)) == 2 else Model(numeric)
    stamp = datetime.now(ET).replace(microsecond=0).isoformat()
    report.update(trainedAt=stamp, trainDays=len(window), trainTrades=len(ys), loadedDays=len(done))
    directory.mkdir(parents=True, exist_ok=True)
    _write_atomic(directory / "model.json", json.dumps(model.to_json()))
    # The challenger, trained on everything too, so it is ready the night it wins.
    try:
        if len(set(ys)) == 2:
            enc = Model(numeric).prepare(rows)
            net = nn_fit(enc._matrix(rows), np.array(ys, dtype=float))
            _write_atomic(directory / "nn.json", json.dumps(NeuralModel(enc, net).to_json()))
    except Exception:  # noqa: BLE001 - the live model is already saved
        pass
    try:
        prev = json.loads((directory / "champion.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = None
    if prev and prev.get("champion") == "boosted":
        prev = {**prev, "champion": prev.get("baseChampion", "lr")}
    champ = decide_champion(prev, report.get("auc"), (report.get("nn") or {}).get("auc"), stamp)
    if champ["champion"] == "nn" and not (directory / "nn.json").exists():
        champ["champion"] = "lr"
    if os.getenv("AGX_AI_BOOSTED", "0") == "1":
        try:
            from momx.boosted_ai import train_challenger
            report["boosted"] = train_challenger(done, numeric, directory)
            if report["boosted"].get("qualified"):
                champ["baseChampion"], champ["champion"] = champ["champion"], "boosted"
        except Exception as exc:  # keep the freshly trained legacy model available
            report["boosted"] = {"qualified": False, "error": type(exc).__name__}
    _write_atomic(directory / "champion.json", json.dumps(champ))
    report["champion"] = champ
    try:
        from momx.scanner_neural_research import train as train_scanner_neural
        report['scannerNeural'] = train_scanner_neural(directory/'scanner-learning', roots, today)
    except Exception as exc:
        report['scannerNeural'] = {'state':'unavailable', 'error':type(exc).__name__, 'probabilityValidated':False}
    _write_atomic(directory / "report.json", json.dumps(report))
    # One line per nightly retrain, so the Bot page can show the score day by day.
    brief = {k: report.get(k) for k in ("trainedAt", "trainDays", "trainTrades", "auc", "testDays")}
    brief.update(all=report.get("all"), aiPick=report.get("aiPick"), jevBot=report.get("jevBot"),
                 bySide=report.get("bySide"), nn=report.get("nn"),
                 wrong=(report.get("mistakes") or {}).get("wrong"), graded=(report.get("mistakes") or {}).get("total"),
                 champion=(report.get("champion") or {}).get("champion"),
                 market=(report.get("features") or {}).get("useMarket"),
                 withMarket=(report.get("features") or {}).get("withMarket"))
    with open(directory / "history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(brief) + "\n")
    return report


class Scorer:
    """Scores the Bot's live trades with the saved model (momx_worker thread)."""

    def __init__(self, books: Mapping[str, Any], directory: Path | None = None) -> None:
        self.books = dict(books)
        self.directory = Path(directory or _dir())
        self.model: Any = None
        self.champion = "lr"
        self._model_mtime: Any = None
        self._tape: MarketTape | None = None
        self._day = ""
        self.scores: dict[str, float] = {}
        self.score_meta: dict[str, dict] = {}
        self._trained_for = ""
        self._last_try = 0.0
        from momx.scanner_probability import Forecaster
        self.scanner_forecaster = Forecaster(self.directory/'scanner-learning')
        self.record_forecasts = {}

    def _record_for(self, side: str, rule: Any, day: str) -> dict:
        """The Record column for a live trade: its rule's last REC_DAYS days (cached per day)."""
        if getattr(self, "_rec_day", None) != day:
            self._rec_day, self._rec = day, {}
            try:
                roots = _roots()
                avail = [d for d in _available_days(roots, self.directory / "dataset") if d < day]
                start = avail[-REC_DAYS] if len(avail) >= REC_DAYS else None
                past = {d: v for d, v in load_days(roots, first=start).items() if d < day}
                names = sorted(past)[-REC_DAYS:]
                probe = {day: [(sd, {"rule": r}) for sd in ("bull", "bear")
                               for r in {t.get("rule") for d in names for _, t in past[d]}]}
                add_records(probe, {d: past[d] for d in names})
                self._rec = {(sd, t["rule"]): {k: t.get(k) for k in ("_recWin", "_recRoi", "_recN")}
                             for sd, t in probe[day]}
            except Exception:  # noqa: BLE001 - no record just means "unknown"
                self._rec = {}
        return dict(self._rec.get((side, rule)) or {})

    def _trained_today(self, day: str) -> bool:
        """report.json already holds a retrain from after RETRAIN_AT today."""
        try:
            at = datetime.fromisoformat(json.loads((self.directory / "report.json").read_text(encoding="utf-8"))["trainedAt"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        at = at.astimezone(ET)
        return at.date().isoformat() == day and (at.hour, at.minute) >= RETRAIN_AT

    def _retrain(self, upto: str) -> None:
        """Run train_now in its OWN process: its memory is freed when it ends and
        it cannot hold this (scanner) process's GIL. The SPY / QQQ file is
        refreshed here first - the child never calls the broker. Never raises."""
        import subprocess
        import sys

        try:
            if self._tape is None:
                self._tape = MarketTape(self.directory)
            self._tape.refresh(min_gap=0)
        except Exception:  # noqa: BLE001
            pass
        root = Path(__file__).resolve().parent.parent
        venv = root / ".venv" / "Scripts" / "python.exe"
        exe = str(venv) if venv.exists() else sys.executable
        # Below-normal CPU priority, and no console window popping up on his screen.
        # IDLE never finished on the laptop (2026-11-02: four runs, hours each, none done).
        flags = (subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
        try:
            res = subprocess.run([exe, "-m", "momx.ai_filter", "--train", "--today", upto, "--dir", str(self.directory)],
                                 cwd=str(root), capture_output=True, text=True, timeout=4 * 3600, creationflags=flags)
            log = (res.stdout or "")[-2000:] + (res.stderr or "")[-4000:]
        except Exception as exc:  # noqa: BLE001 - a failed retrain keeps the old model
            log = f"{type(exc).__name__}: {exc}"
        try:
            (self.directory / "last_retrain.log").write_text(
                datetime.now(ET).replace(microsecond=0).isoformat() + "\n" + log, encoding="utf-8")
        except OSError:
            pass

    def _load_model(self) -> None:
        """The CHAMPION (champion.json): the neural net once it has earned it, else the live model."""
        self.scanner_forecaster.refresh()
        try:
            champ = json.loads((self.directory / "champion.json").read_text(encoding="utf-8")).get("champion", "lr")
        except (OSError, ValueError, AttributeError):
            champ = "lr"
        path = self.directory / ("boosted.json" if champ == "boosted" else "nn.json" if champ == "nn" else "model.json")
        try:
            stamp = (champ, path.stat().st_mtime)
            if stamp != self._model_mtime:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if champ == "boosted":
                    from momx.boosted_ai import BoostedModel
                    self.model = BoostedModel(self.directory / "boosted.onnx", path)
                else:
                    self.model = NeuralModel.from_json(doc) if champ == "nn" else Model.from_json(doc)
                self.champion = champ
                self._model_mtime = stamp
                self.model_load_error = None
        except Exception as exc:  # optional model failures retain a working local baseline
            self.model_load_error = type(exc).__name__
            if self.model is None:
                try:
                    self.model = Model.from_json(json.loads((self.directory / "model.json").read_text(encoding="utf-8")))
                    self.champion = "lr"
                except (OSError, ValueError, KeyError):
                    pass

    def tick(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(ET)).astimezone(ET)
        day = now.date().isoformat()
        # Reset before training/model loading: those can block or fail across
        # midnight while board readers continue serving the previous scores.
        path = self.directory / "scores" / f"{day}.json"
        if day != self._day:
            self.record_forecasts = {}
            self._day = day
            try:
                self.scores = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.scores = {}
            try:
                self.score_meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.score_meta = {}
            if not isinstance(self.scores, dict):
                self.scores = {}
            if not isinstance(self.score_meta, dict):
                self.score_meta = {}
        # Nightly retrain after the close (and when no model exists yet - at most hourly).
        after = (now.hour, now.minute) >= RETRAIN_AT
        nightly = now.weekday() < 5 and after and self._trained_for != day
        if nightly and self._trained_today(day):        # a worker restart must not redo tonight's run
            self._trained_for, nightly = day, False
        missing = not (self.directory / "model.json").exists() and time.time() - self._last_try >= 3600
        if nightly or missing:
            self._last_try = time.time()
            # After the close the day that just finished is IN the training set
            # (it used to be left out: train_now trains on days < today).
            from datetime import timedelta
            upto = (now.date() + timedelta(days=1)).isoformat() if after else day
            self._retrain(upto)
            if after:
                self._trained_for = day
        self._load_model()
        if self.model is None:
            return
        changed = False
        for side, book in self.books.items():
            try:
                trades = book.trades()
            except Exception:  # noqa: BLE001
                continue
            for t in trades:
                if t.get("status") == "pending" and isinstance(t.get("aiCandidate"), dict):
                    t = {**t, **t["aiCandidate"]}
                if _trade_day(t.get("at")) != day:
                    continue
                key = trade_key(side, t)
                forecast = (t.get('aiDecision') or {}).get('scannerForecast') or {}
                if forecast.get('validated') is True and self.scanner_forecaster.validated(forecast.get('modelId'), now):
                    proof = self.scanner_forecaster.proof
                    self.record_forecasts[key] = {**forecast, 'validated':True,
                        'validatedAt':proof['validatedAt'], 'validationTrades':proof['validatedTrades'],
                        'validationDays':proof['validatedDays'], 'validationThrough':proof['throughDay'],
                        'symbol':t.get('symbol'), 'side':side}
                else:
                    self.record_forecasts.pop(key, None)
                if key in self.scores or not t.get("entry"):
                    continue
                frozen = (t.get("aiDecision") or {}).get("prediction") or {}
                p = frozen.get("probability")
                if frozen.get("phase") == "before_entry" and isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and 0 <= p <= 1:
                    self.scores[key] = p
                    self.score_meta[key] = {k: frozen[k] for k in ("family", "scoredAt", "phase") if k in frozen}
                    changed = True
                    continue
                t = dict(t)
                t.update(self._record_for(side, t.get("rule"), day))
                if "m_spyPct" in getattr(getattr(self.model, "enc", self.model), "numeric", ()):
                    # The market picture only when the model in charge uses it.
                    if self._tape is None:
                        self._tape = MarketTape(self.directory)
                    self._tape.refresh()
                    t["_mkt"] = self._tape.at(t.get("entryAt") or t.get("at"))
                self.scores[key] = round(self.model.predict(features(t, side)), 4)
                self.score_meta[key] = {"family": self.champion, "scoredAt": now.isoformat()}
                changed = True
        if changed:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(path, json.dumps(self.scores))
                _write_atomic(path.with_suffix(".meta.json"), json.dumps(self.score_meta))
            except OSError:            # kept in memory; written with the next new trade
                pass


_SCORER: Scorer | None = None


def scanner_forecast_at_entry(side, trade, timestamp):
    forecaster = getattr(_SCORER, 'scanner_forecaster', None)
    return forecaster.predict(side, trade, timestamp) if forecaster else {
        'available':False, 'reason':'Learning: probability forecaster warming up'}


def predict_at_entry(side: str, trade: Mapping, timestamp: float) -> dict:
    """Cached local inference only: no files, history loads, training, or LLM calls.

    Stored on the trade before it opens. Unavailable inference never vetoes a
    rule-qualified entry and cannot later be relabelled as an advance forecast.
    """
    scorer = _SCORER
    if scorer is None or getattr(scorer, "model", None) is None:
        return {"available": False, "reason": "Local model warming up"}
    try:
        now = datetime.fromtimestamp(timestamp, ET)
        snapshot = {**trade, "entryAt": now.isoformat()}
        if getattr(scorer, "_rec_day", None) == now.date().isoformat():
            snapshot.update(getattr(scorer, "_rec", {}).get((side, trade.get("rule")), {}))
        values = features(snapshot, side)
        probability = float(scorer.model.predict(values))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid probability")
        return {"available": True, "probability": round(probability, 4), "family": scorer.champion,
                "scoredAt": now.isoformat(), "phase": "before_entry", "features": values,
                "target": "Main option positive Strategy-exit midpoint return before fees; experimental estimate"}
    except Exception:
        return {"available": False, "reason": "Local prediction unavailable; baseline entry continues"}


def start(books: Mapping[str, Any]) -> Scorer:
    """One scoring loop in momx_worker. Idempotent."""
    global _SCORER
    if _SCORER is None:
        scorer = _SCORER = Scorer(books)

        def loop() -> None:
            while True:
                try:
                    scorer.tick()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(30)

        threading.Thread(target=loop, name="momx-ai-filter", daemon=True).start()
    return _SCORER


#: The Setup column's AI tag shows from this score (2026-10-17, his "Yes add it
#: for bull and bear"); the Bot's "AI pick" book uses the same cut.
TAG_CUT = 0.55


def _trade_day(at) -> str | None:
    try:
        stamp = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        return stamp.astimezone(ET).date().isoformat() if stamp.tzinfo else None
    except (ValueError, TypeError):
        return None


def best_scores(side: str, now: datetime | None = None) -> dict[str, dict]:
    """{symbol: {"p": best score, "rule": its rule}} for today's live trades on
    one side - what the scanner row's AI tag shows. {} before the scorer runs."""
    scorer = _SCORER
    if scorer is None:
        return {}
    day = (now or datetime.now(ET)).astimezone(ET).date().isoformat()
    if scorer._day != day:
        return {}
    # tick only appends immutable scores, or replaces the whole daily map.
    # Several board/alert readers share this result; parsing every trade's
    # timestamp again on each HTTP request needlessly stalls the scanner.
    score_meta = getattr(scorer, "score_meta", {})
    signature = (day, id(scorer.scores), len(scorer.scores), id(score_meta), len(score_meta), side)
    cached = getattr(scorer, "_best_scores_cache", {}).get(side)
    if now is None and cached and cached[0] == signature:
        return cached[1]
    out: dict[str, dict] = {}
    ranks: dict[str, float] = {}
    for key, p in list(scorer.scores.items()):
        if not isinstance(key, str) or isinstance(p, bool):
            continue
        sd, rule, symbol, at = (key.split("|", 3) + ["", "", "", ""])[:4]
        if sd != side or not rule or not symbol or _trade_day(at) != day:
            continue
        if datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() > (now or datetime.now(ET)).timestamp():
            continue
        try:
            p = float(p)
        except (TypeError, ValueError):
            continue
        if not 0 <= p <= 1:
            continue
        symbol = symbol.upper()
        if symbol not in ranks or p > ranks[symbol]:
            ranks[symbol] = p
            provenance = score_meta.get(key) or {}
            if not isinstance(provenance, dict):
                provenance = {}
            out[symbol] = {"p": round(float(p), 4), "rule": rule,
                           "symbol": symbol, "side": side, "at": at, "day": day,
                           "model": {"nn": "neural net", "lr": "logistic", "boosted": "local boosted trees"}.get(provenance.get("family"), "saved local score")}
            if provenance.get("scoredAt"):
                out[symbol]["scoredAt"] = provenance["scoredAt"]
    if now is None:
        cache = getattr(scorer, "_best_scores_cache", {})
        cache[side] = (signature, out)
        scorer._best_scores_cache = cache
    return out


def mark_rows(payload: Mapping, side: str, now: datetime | None = None) -> None:
    """Stamp row["aiScore"] = {"p", "rule"} on every board row that has one. Never raises."""
    try:
        best = best_scores(side, now)
    except Exception:
        best = {}
    learning = {'state': 'collecting', 'trainingTrades': 0, 'calibrationTrades': 0,
                'testTrades': 0, 'forwardTrades':0, 'forwardDays':0, 'probabilityValidated': False}
    as_of = now or datetime.now(ET)
    try:
        research = json.loads((_dir()/'scanner-learning'/'report.json').read_text(encoding='utf-8'))
        if datetime.fromisoformat(research['builtAt']) <= (now or datetime.now(ET)):
            learning.update({k: research[k] for k in ('state', 'trainingTrades', 'calibrationTrades', 'testTrades', 'builtAt') if k in research})
            forward = research.get('forward') or {}
            learning.update(forwardTrades=max(forward.get('trades',0),forward.get('validatedTrades',0)),
                            forwardDays=max(forward.get('observedDays',0),forward.get('validatedDays',0)))
            if forward.get('state') in ('validated','withdrawn','rejected','forward'):
                learning['state'] = forward['state']
            forecaster = getattr(_SCORER,'scanner_forecaster',None)
            learning['probabilityValidated'] = bool(forecaster and forecaster.validated(forward.get('modelId'),as_of))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    entry_forecasts = {}
    for f in list(getattr(_SCORER, 'record_forecasts', {}).values()):
        try:
            stamp = datetime.fromisoformat(f['scoredAt'])
            if f.get('side') != side or stamp > as_of or stamp.astimezone(ET).date() != as_of.astimezone(ET).date():
                continue
            symbol = f['symbol']
            if symbol not in entry_forecasts or f['scoredAt'] > entry_forecasts[symbol]['scoredAt']:
                entry_forecasts[symbol] = f
        except (KeyError, TypeError, ValueError):
            continue
    try:
        for key in ("rows", "rest"):
            for row in payload.get(key) or []:
                if isinstance(row, dict):
                    if isinstance(row.get('rvol'), Mapping):
                        row['recordLearning'] = learning
                    row.pop('recordProbability', None)
                    forecast = entry_forecasts.get(row.get('symbol'))
                    current = row.get('scannerInputs') or {}
                    original = (forecast or {}).get('scannerInputs') or {}
                    if (forecast and current.get('highestLevel') in (1,2,3)
                            and current.get('highestLevel') == original.get('highestLevel')
                            and current.get('setupTypes') == original.get('setupTypes')):
                        row['recordProbability'] = forecast
                    hit = best.get(str(row.get("symbol") or ""))
                    if hit:
                        row["aiScore"] = hit
                    else:
                        row.pop("aiScore", None)
    except Exception:  # noqa: BLE001 - a tag must never cost a build
        pass


def scores_for(day: str, directory: Path | None = None) -> dict[str, float]:
    try:
        return json.loads((Path(directory or _dir()) / "scores" / f"{day}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def report(directory: Path | None = None) -> dict:
    try:
        return json.loads((Path(directory or _dir()) / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def history(directory: Path | None = None, limit: int = 30) -> list[dict]:
    """The nightly scores, newest first (last `limit`)."""
    out = []
    try:
        for line in (Path(directory or _dir()) / "history.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return list(reversed(out))[:limit]


def _single_retrain_lock(directory: Path):
    """An OS file lock held for the life of this retrain process, or None if another
    retrain holds it. The OS drops it when the process dies, so a crash never wedges it.

    2026-11-02: every worker restart launched a fresh retrain and the old ones kept
    running as orphans - four at IDLE priority fought each other and none finished
    (no retrain since 10-30, the unsupervised research never ran).
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        handle = open(directory / "retrain.lock", "a+b")
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        return None


def _main(argv: list[str]) -> int:
    """python -m momx.ai_filter --train [--today YYYY-MM-DD] [--dir PATH] - the nightly retrain."""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--today")
    ap.add_argument("--dir")
    ap.add_argument("--refresh-market", action="store_true")
    args = ap.parse_args(argv)
    if not args.train:
        ap.print_help()
        return 2
    lock = _single_retrain_lock(Path(args.dir) if args.dir else _dir())
    if lock is None:
        print(json.dumps({"skipped": "another retrain is already running"}))
        return 0
    rep = train_now(Path(args.dir) if args.dir else None, today=args.today, refresh_market=args.refresh_market)
    print(json.dumps({k: rep.get(k) for k in ("trainedAt", "trainDays", "loadedDays", "trainTrades", "auc")}))
    return 0


if __name__ == "__main__":
    import sys as _sys
    raise SystemExit(_main(_sys.argv[1:]))
