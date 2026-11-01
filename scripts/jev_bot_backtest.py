"""Would TypeSafe Jev have improved the BOT's trades? (his ask 2026-10-02: "with Jev AI or without?")

Design copied from aabrole/claude-trading-desk (core/decision.py + the trading-desk-jev
skill) and the "Quant Trading Engineer" sheet he shared:

* The BOT's own rules still find every trade (momx_option_track books, real option
  prices, bull = calls, bear = puts). Jev never finds trades and never flips a side;
  it only answers "take it" or "stand aside".
* THREE arms on the SAME trades:
    rules   every trade the rule took (the BOT today)
    gated   the same trades through hand-written filters fixed BEFORE looking at
            results (FILTERS below) - the honest control: any filter that skips
            trades can lift a win rate, so Jev must beat THIS, not "rules"
    jev     the same trades, approved when P(take) >= cutoff (swept 0.30..0.80)
* One Jev call per trade asks the decision PLUS factor questions (regime, direction,
  buying pressure, setup quality, risk, news) - recorded, never traded on - so we
  can see which factor, if any, ranks winners.
* No hindsight: each trade is described from the History snapshot at or before
  its signal minute (snapshots no older than MAX_SNAPSHOT_AGE_MIN), the trade's
  own price/contract at entry, and SPY's snapshot at that minute.
* Every Jev answer is cached in artifacts/jev_bot_backtest/cache.jsonl keyed by a
  hash of the exact request: a re-run is free and returns the same answers.
* Result = OPTION % (his exit: first 5m close through VWAP/EMA21, else 15:50) and
  the hold-to-close %, per arm, overall and by rule, in-sample (first days) vs
  out-of-sample (last days), plus calibration (does "70%" win ~70%?).

    python scripts/jev_bot_backtest.py            # build, ask Jev (cached), report
    python scripts/jev_bot_backtest.py --dry      # build + count only, no Jev calls

Virtual trades only. NOT proof: one week of signals.
"""
from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import statistics as st
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts" / "jev_bot_backtest"
CACHE = OUT / "cache.jsonl"
API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
MAX_SNAPSHOT_AGE_MIN = 30
CUTOFFS = (0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
BOOKS = (("bull", "artifacts", "artifacts/momx_history"), ("bear", "artifacts/bear", "artifacts/momx_history/bear"))
RULE_NAMES = {
    "mom": "MOM (momentum getting stronger)", "call2h": "CALL2H/CALL4H chart arrow", "rvol": "RVOL push",
    "skit": "Skittles cross", "c2h": "C2H/C4H chart arrow", "zs2": "ZS setup (EMA 9/21)", "zs": "ZS setup",
    "sectorMove": "sector move", "sqz": "squeeze fired", "adx": "ADX trend", "dayReclaim": "daily-line reclaim",
    "openPath": "opening path", "dayLine": "daily line", "dayLine100": "daily line $100+", "sectorHot": "hot sector",
    "star1": "Star #1", "solo": "SOLO big money", "turn": "market turn", "mxaplus": "MX A+", "hotLead": "hot-sector leader",
    "go": "GO (gap and go)", "goRvolMacd": "GO + RVOL + MACD", "daily2": "Daily 2",
}


# ----------------------------------------------------------------------------- data
def _t(s):
    return datetime.fromisoformat(s)


def load_trades() -> list[dict]:
    out = []
    for side, troot, hroot in BOOKS:
        for f in sorted(glob.glob(f"{troot}/momx_option_track/2026-*.json")):
            day = Path(f).stem
            book = json.loads(Path(f).read_text(encoding="utf-8"))
            hist: dict = {}
            spy_rows = None
            p = Path(hroot) / "Watchlist" / f"{day}.json"
            if p.exists():
                spy_rows = (json.loads(p.read_text(encoding="utf-8"))["rows"].get("SPY") or {}).get("snapshots") or []
            for t in book["trades"]:
                if not t.get("entry") or t.get("exitPrice") is None:
                    continue
                board = t.get("board") or "Watchlist"
                if board not in hist:
                    hp = Path(hroot) / board / f"{day}.json"
                    hist[board] = json.loads(hp.read_text(encoding="utf-8"))["rows"] if hp.exists() else {}
                at = _t(t["at"])
                snaps = [s for s in ((hist[board].get(t["symbol"]) or {}).get("snapshots") or []) if _t(s["at"]) <= at]
                if not snaps:
                    continue
                snap = snaps[-1]
                age = (at - _t(snap["at"])).total_seconds() / 60
                if age > MAX_SNAPSHOT_AGE_MIN:
                    continue
                spy = [s for s in (spy_rows or []) if _t(s["at"]) <= at]
                out.append({"side": side, "day": day, "trade": t, "row": snap["row"], "age": age,
                            "spy": spy[-1]["row"] if spy else None})
    return out


def option_return(t: dict, key: str) -> float | None:
    v = t.get(key)
    return None if v is None or not t.get("entry") else (float(v) / float(t["entry"]) - 1) * 100


# ------------------------------------------------------------------------ describe
def describe(x: dict) -> str:
    """The SAME text the live BOT sends (momx/jev_trade.py) - one source for both."""
    from momx.jev_trade import describe as live_describe
    # JEV_DESC_V=1 (default) = the back-tested text; 2 = the rule-definition version.
    return live_describe(x["trade"], x["row"], x.get("spy"), x["side"], x["day"], version=int(os.getenv("JEV_DESC_V", "1")))


def questions(side: str) -> dict:
    from momx.jev_trade import questions as live_questions
    return live_questions(side)


# ------------------------------------------------------------------------ filters
def passes_filters(x: dict) -> bool:
    """Hand-written control arm, fixed before any result was looked at."""
    t, r, up = x["trade"], x["row"], x["side"] == "bull"
    m5 = r.get("m5") or {}
    price = float(t.get("stockPrice") or r.get("last") or 0)
    vwap, ema21 = m5.get("vwap"), m5.get("ema21")
    if not vwap or not ema21:
        return False
    if up and not (price > vwap and price > ema21):
        return False
    if not up and not (price < vwap and price < ema21):
        return False
    rv = r.get("rvol") or {}
    lit = [tf for tf in ("5m", "15m") if (rv.get(tf) or {}).get("bg") not in (None, "black")
           or (rv.get(tf) or {}).get("fg") in (("cyan", "green") if up else ("magenta", "red"))]
    if not lit:
        return False
    spy = x.get("spy") or {}
    sv = (spy.get("m5") or {}).get("vwap")
    if sv:
        above = float(spy.get("last") or 0) >= sv
        if above != up:
            return False
    pct = float(r.get("pctChange") or 0)
    if (up and pct >= 10) or (not up and pct <= -10):
        return False
    hhmm = int(t["at"][11:13]) * 60 + int(t["at"][14:16])
    return hhmm < 15 * 60


# ---------------------------------------------------------------------------- jev
_lock = threading.Lock()


def _key(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def load_cache() -> dict:
    cache = {}
    if CACHE.exists():
        for line in CACHE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                cache[rec["key"]] = rec["response"]
            except Exception:  # noqa: BLE001
                continue
    return cache


def ask(payload: dict, key: str, api_key: str, cache: dict) -> dict | None:
    k = _key(payload)
    if k in cache:
        return cache[k]
    body = json.dumps(payload).encode()
    for attempt in range(5):
        try:
            req = urllib.request.Request(API, data=body, method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                out = json.loads(resp.read().decode())
            with _lock:
                cache[k] = out
                with open(CACHE, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"key": k, "response": out}) + "\n")
            return out
        except urllib.error.HTTPError as e:
            if e.code != 429 and 400 <= e.code < 500:
                return None
        except Exception:  # noqa: BLE001 - resets, timeouts
            pass
        time.sleep(0.5 * 2 ** attempt)
    return None


def parse(resp: dict | None) -> dict:
    a = (resp or {}).get("answers") or {}
    act = a.get("action") or {}
    out = {"p_take": (act.get("probabilities") or {}).get("take"), "conf": act.get("confidence")}
    for q in ("regime", "direction", "pressure", "news"):
        out[q] = (a.get(q) or {}).get("noul")
    for q in ("quality", "risk"):
        out[q] = (a.get(q) or {}).get("score")
    return out


# ------------------------------------------------------------------------- report
def summary(rows: list[dict], key="ret") -> dict:
    v = [r[key] for r in rows if r.get(key) is not None]
    if not v:
        return {"n": 0}
    return {"n": len(v), "win": round(100 * sum(x > 0 for x in v) / len(v), 1), "avg": round(st.mean(v), 1),
            "median": round(st.median(v), 1)}


def welch_t(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return round((st.mean(a) - st.mean(b)) / se, 2) if se else None


def auc(rows: list[dict], score: str) -> float | None:
    pts = [(r[score], r["ret"] > 0) for r in rows if r.get(score) is not None and r.get("ret") is not None]
    pos = [s for s, w in pts if w]
    neg = [s for s, w in pts if not w]
    if not pos or not neg:
        return None
    allv = sorted((s, i) for i, (s, _) in enumerate(pts))
    ranks = {}
    i = 0
    while i < len(allv):                     # average ranks for ties
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[allv[k][1]] = (i + j) / 2 + 1
        i = j + 1
    rpos = sum(ranks[i] for i, (_, w) in enumerate(pts) if w)
    return round((rpos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 3)


def main(dry: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load_trades()
    days = sorted({x["day"] for x in data})
    print(f"{len(data)} BOT trades with a scanner snapshot <= {MAX_SNAPSHOT_AGE_MIN} min old, days {days}")
    if dry:
        print(describe(data[0]))
        print("filters pass:", sum(passes_filters(x) for x in data))
        return
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = (os.getenv("TYPESAFE_API_KEY") or "").strip()
    if not key:
        raise SystemExit("TYPESAFE_API_KEY not set")
    cache = load_cache()
    payloads = [{"model": MODEL, "state": describe(x), "questions": questions(x["side"])} for x in data]
    todo = [p for p in payloads if _key(p) not in cache]
    print(f"Jev: {len(payloads) - len(todo)} cached, {len(todo)} to ask")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = [ex.submit(ask, p, key, key, cache) for p in todo]
        for i, f in enumerate(as_completed(futs), 1):
            f.result()
            if i % 500 == 0:
                print(f"  {i}/{len(todo)} ({i / max(1, time.time() - t0):.1f}/s)")
    rows = []
    for x, p in zip(data, payloads):
        t = x["trade"]
        rows.append({"side": x["side"], "day": x["day"], "rule": t["rule"], "symbol": t["symbol"], "at": t["at"],
                     "ret": option_return(t, "exitPrice"), "retClose": option_return(t, "close"),
                     "gated": passes_filters(x), **parse(cache.get(_key(p)))})
    (OUT / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    asked = [r for r in rows if r["p_take"] is not None]
    print(f"Jev answered {len(asked)} of {len(rows)}")
    split = days[len(days) // 2 + (len(days) % 2)] if len(days) > 1 else days[0]   # out-of-sample = last ~half
    report = {"days": days, "outOfSampleFrom": split, "n": len(rows), "answered": len(asked)}

    def arms(sub: list[dict], label: str) -> dict:
        res = {"rules": summary(sub), "rulesHold": summary(sub, "retClose"), "gated": summary([r for r in sub if r["gated"]])}
        g = [r["ret"] for r in sub if r["gated"] and r["ret"] is not None]
        for c in CUTOFFS:
            j = [r for r in sub if r["p_take"] is not None and r["p_take"] >= c]
            res[f"jev>={c:.2f}"] = {**summary(j), "tVsGated": welch_t([r["ret"] for r in j if r["ret"] is not None], g)}
        res["auc"] = {q: auc(sub, q) for q in ("p_take", "conf", "regime", "direction", "pressure", "quality", "news")}
        res["auc"]["risk(low=good)"] = auc([{**r, "risk": -r["risk"]} if r.get("risk") is not None else r for r in sub], "risk")
        print(f"\n== {label}  ({len(sub)} trades) ==")
        for k, v in res.items():
            if k != "auc":
                print(f"  {k:12s} {v}")
        print("  ranking power (AUC, 0.5 = coin flip):", res["auc"])
        return res

    for side in ("bull", "bear"):
        s = [r for r in asked if r["side"] == side]
        report[side] = {"all": arms(s, f"{side.upper()} all days"),
                        "inSample": arms([r for r in s if r["day"] < split], f"{side.upper()} in-sample (< {split})"),
                        "outOfSample": arms([r for r in s if r["day"] >= split], f"{side.upper()} OUT-of-sample (>= {split})")}
        byrule = {}
        for rule in sorted({r["rule"] for r in s}):
            rr = [r for r in s if r["rule"] == rule]
            if len(rr) < 20:
                continue
            j = [r for r in rr if r["p_take"] is not None and r["p_take"] >= 0.5]
            byrule[rule] = {"rules": summary(rr), "gated": summary([r for r in rr if r["gated"]]), "jev>=0.50": summary(j)}
        report[side]["byRule"] = byrule
        print(f"\n{side.upper()} by rule (>= 20 trades):")
        for rule, v in byrule.items():
            print(f"  {rule:12s} rules {v['rules']}  gated {v['gated']}  jev>=0.5 {v['jev>=0.50']}")
        cal = defaultdict(list)
        for r in s:
            if r["p_take"] is not None and r["ret"] is not None:
                cal[min(9, int(r["p_take"] * 10))].append(r["ret"] > 0)
        report[side]["calibration"] = {f"{b / 10:.1f}-{(b + 1) / 10:.1f}": {"n": len(v), "won": round(100 * sum(v) / len(v), 1)}
                                       for b, v in sorted(cal.items())}
        print(f"  calibration (Jev's P(take) bucket -> % that actually won):", report[side]["calibration"])
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main("--dry" in sys.argv)
