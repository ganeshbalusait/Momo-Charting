"""Does a high Jev score on a monthly-EMA-touch ticker give a better LEAP?

Ganesh 2026-10-04 asked to test it ("Yes please"). Forward test only - Jev
scores exist from 2026-10-02, so nothing here can be back-dated.

Entries: the first IN-SESSION (09:30-15:30 ET, weekday) Jev score each ticker
got on the "21 EMA Touch Monthly" / "50 EMA Touch Monthly" boards, once per
ticker per list per month (the touch month), from
artifacts/momx_jev_scanner/<day>.jsonl. Entry price = the price on
that score record.

Outcome at 5 / 10 / 21 / 63 trading days: the stock move, and two modelled
1-year calls (10% ITM and 20% OTM; Black-Scholes, IV fixed at the 60-day
realised vol on the entry day - the conservative pricing from
artifacts/backtest_leap_msft_style.py). Split by Jev >= 10% (the bot's pick
line) vs below. Alpaca SIP daily bars, split-adjusted.

    .venv/Scripts/python.exe scripts/jev_ema_touch_eval.py
writes artifacts/jev_ema_touch_eval.md and prints the same.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")
LISTS = ("21 EMA Touch Monthly", "50 EMA Touch Monthly")
SESSION = (570, 930)  # 09:30-15:30 ET, in minutes
JEV_CUT = 0.10
CHECKPOINTS = (5, 10, 21, 63)
LOG_DIR = ROOT / "artifacts" / "momx_jev_scanner"
OUT = ROOT / "artifacts" / "jev_ema_touch_eval.md"
MIN_N = 30


def entries() -> list[dict]:
    seen, out = set(), []
    for path in sorted(LOG_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("board") not in LISTS or rec.get("side") != "bull" or rec.get("price") is None:
                continue
            at = datetime.fromisoformat(rec["at"]).astimezone(ET)
            minute = at.hour * 60 + at.minute
            if at.weekday() >= 5 or not (SESSION[0] <= minute <= SESSION[1]):
                continue
            key = (rec["board"], rec["symbol"], at.strftime("%Y-%m"))
            if key in seen:
                continue
            seen.add(key)
            out.append({"list": rec["board"], "symbol": rec["symbol"], "day": at.date(),
                        "p": float(rec["p"]), "price": float(rec["price"])})
    return out


def daily_closes(symbols: list[str], start: str) -> dict[str, list[tuple]]:
    import requests

    from momx import feed

    end = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    out: dict[str, list[tuple]] = {}
    for credential in feed.resolve_credentials():
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end,
                  "adjustment": "split", "feed": "sip", "limit": 10000}
        got: dict[str, list[tuple]] = {}
        ok = True
        while True:
            r = requests.get("https://data.alpaca.markets/v2/stocks/bars", params=params,
                             headers=credential.headers, timeout=30)
            if r.status_code in (401, 403):
                ok = False
                break
            r.raise_for_status()
            body = r.json() or {}
            for sym, bars in (body.get("bars") or {}).items():
                got.setdefault(sym, []).extend((b["t"][:10], float(b["c"])) for b in bars)
            if not body.get("next_page_token"):
                break
            params["page_token"] = body["next_page_token"]
        if ok:
            return got
    return out


def bs_call(s, k, t, vol, r=0.04):
    if t <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * n(d1) - k * math.exp(-r * t) * n(d2)


def main() -> None:
    rows = entries()
    lines = [f"# Jev score vs monthly EMA touch LEAPs - {datetime.now(ET):%Y-%m-%d %H:%M} ET", ""]
    if not rows:
        lines.append("No in-session Jev scores on the EMA touch lists yet (they started 2026-10-04).")
    else:
        start = (min(r["day"] for r in rows) - timedelta(days=120)).isoformat()
        closes = daily_closes(sorted({r["symbol"] for r in rows}), start)
        results = []
        for r in rows:
            series = closes.get(r["symbol"]) or []
            days = [d for d, _ in series]
            px = [c for _, c in series]
            day = r["day"].isoformat()
            if day not in days:
                continue
            i = days.index(day)
            rets = [math.log(px[j] / px[j - 1]) for j in range(max(1, i - 59), i + 1)]
            vol = max(0.15, (sum(x * x for x in rets) / max(1, len(rets)) - (sum(rets) / max(1, len(rets))) ** 2) ** 0.5 * math.sqrt(252)) if len(rets) > 20 else None
            res = dict(r)
            for h in CHECKPOINTS:
                if i + h >= len(px) or vol is None:
                    continue
                s0, s1 = r["price"], px[i + h]
                res[f"stk{h}"] = s1 / s0 - 1
                for name, m in (("itm", 0.9), ("otm", 1.2)):
                    c0 = bs_call(s0, s0 * m, 1.0, vol)
                    res[f"{name}{h}"] = bs_call(s1, s0 * m, 1.0 - h / 252, vol) / c0 - 1
            results.append(res)
        lines.append(f"Entries: {len(rows)} (high Jev >= {JEV_CUT:.0%}: {sum(r['p'] >= JEV_CUT for r in rows)}). "
                     f"A group needs {MIN_N}+ finished trades before it says anything.")
        lines.append("")
        lines.append("| after | group | n | stock avg | 10% ITM call: win / avg | 20% OTM call: win / avg |")
        lines.append("|---|---|---|---|---|---|")
        for h in CHECKPOINTS:
            for label, test in (("Jev >= 10%", lambda p: p >= JEV_CUT), ("Jev < 10%", lambda p: p < JEV_CUT)):
                g = [x for x in results if f"stk{h}" in x and test(x["p"])]
                if not g:
                    lines.append(f"| {h}d | {label} | 0 | - | - | - |")
                    continue
                avg = lambda k: sum(x[k] for x in g) / len(g)
                win = lambda k: sum(x[k] > 0 for x in g) / len(g)
                flag = "" if len(g) >= MIN_N else " (too few)"
                lines.append(f"| {h}d | {label} | {len(g)}{flag} | {avg(f'stk{h}'):+.1%} | "
                             f"{win(f'itm{h}'):.0%} / {avg(f'itm{h}'):+.0%} | {win(f'otm{h}'):.0%} / {avg(f'otm{h}'):+.0%} |")
        lines.append("")
        lines.append("Entries so far:")
        for r in rows:
            lines.append(f"- {r['day']} {r['list'][:2]} EMA {r['symbol']} Jev {r['p']:.0%} @ ${r['price']:.2f}")
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
