"""Generate a Trading Alphas-style thinkScript with live highest-OI levels.

Fetches option chains (Tradier first, Schwab fallback), ranks strikes by open
interest, and emits a single paste-ready TOS study covering every requested
ticker:

* Tiered call/put OI walls (call_1..call_5b / put_1..put_5b) using the same
  3-levels-per-tier ranking as frontend/src/oiChartLevels.js.
* Expected move lines (emU/emD) from the nearest-expiry ATM straddle.
* Momox-style reference levels: for the two heaviest-OI expirations, the
  biggest call wall and put wall with "<OI>k M/D" bubbles (e.g. "88k 8/14").

Usage (from repo root, with the project venv):
    python scripts/generate_oi_thinkscript.py
    python scripts/generate_oi_thinkscript.py --tickers SPY,QQQ --dte 21
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config import settings  # noqa: E402  (loads .env)

DEFAULT_TICKERS = (
    "SPY,QQQ,DIA,IWM,GLD,SLV,IBIT,"
    "AAPL,AMZN,GOOGL,META,MSFT,NVDA,TSLA,AVGO"
)
LEVELS_PER_TIER = 3
MAX_LEVELS_PER_SIDE = 15
# Tier for OI-rank index: 0-2 -> 5 (strongest), 3-5 -> 4, 6-8 -> 3, 9-11 -> 2, 12-14 -> 1
TIER_PLOTS = {
    5: ("5", "5a", "5b"),
    4: ("4", "4a", "4b"),
    3: ("3", "3a", "3b"),
    2: ("2", "2a", "2b"),
    1: ("1", "1a", "1b"),
}
BIG_TIERS = {3, 4, 5}


def _num(value, fallback=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed else fallback


def _mark(contract: dict) -> float:
    bid, ask = _num(contract.get("bid")), _num(contract.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return _num(contract.get("last")) or _num(contract.get("mark"))


def fetch_chain(symbol: str, dte: int, provider: str) -> tuple[dict, str]:
    errors = []
    order = {
        "auto": ("tradier", "schwab"),
        "tradier": ("tradier",),
        "schwab": ("schwab",),
    }[provider]
    for name in order:
        try:
            if name == "tradier":
                from data.tradier_client import TradierClient

                client = TradierClient()
                if not client.configured:
                    raise RuntimeError("Tradier token not configured")
                chain = client.get_option_chain_range(
                    symbol, contract_type="ALL", strike_count=120,
                    max_days_to_expiration=dte,
                )
            else:
                from data.schwab_client import SchwabClient

                client = SchwabClient()
                if not client.configured:
                    raise RuntimeError("Schwab credentials not configured")
                now = datetime.now()
                chain = client.get_option_chain(
                    symbol, contract_type="ALL", strike_count=120,
                    from_date=now, to_date=now + timedelta(days=dte),
                )
            if chain and (chain.get("callExpDateMap") or chain.get("putExpDateMap")):
                return chain, name
            errors.append(f"{name}: empty chain")
        except Exception as exc:  # noqa: BLE001 - report every provider failure
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))


def chain_rows(chain: dict) -> list[dict]:
    rows = []
    for side, map_key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        for expiry_key, strikes in (chain.get(map_key) or {}).items():
            expiry = str(expiry_key).split(":")[0]
            for strike_key, contracts in (strikes or {}).items():
                for contract in contracts or []:
                    oi = _num(contract.get("openInterest") or contract.get("open_interest"))
                    strike = _num(contract.get("strikePrice") or strike_key)
                    if strike <= 0:
                        continue
                    rows.append({
                        "side": side,
                        "expiry": expiry,
                        "dte": int(_num(contract.get("daysToExpiration"), 0)),
                        "strike": strike,
                        "oi": max(oi, 0.0),
                        "volume": max(_num(contract.get("totalVolume") or contract.get("volume")), 0.0),
                        "mark": _mark(contract),
                    })
    return rows


def spot_price(chain: dict) -> float:
    spot = _num(chain.get("underlyingPrice"))
    if spot > 0:
        return spot
    quote = chain.get("underlying") or {}
    return _num(quote.get("last")) or _num(quote.get("close")) or _num(quote.get("mark"))


def top_levels(rows: list[dict], side: str, top: int = MAX_LEVELS_PER_SIDE) -> list[dict]:
    """Rank strikes by dominant-expiry OI (Trading Alphas attribution).

    Each strike keeps the single expiry holding the most OI — a wall reads
    "21k 9/18" — rather than summing the strike across expirations.
    """
    by_strike: dict[float, dict] = {}
    for row in rows:
        if row["side"] != side or row["oi"] <= 0:
            continue
        existing = by_strike.get(row["strike"])
        if existing is None or (row["oi"], row["volume"]) > (existing["oi"], existing["volume"]):
            by_strike[row["strike"]] = {
                "strike": row["strike"],
                "oi": row["oi"],
                "volume": row["volume"],
                "expiry": row["expiry"],
            }
    ascending = side == "CALL"
    levels = sorted(
        by_strike.values(),
        key=lambda item: (-item["oi"], -item["volume"], item["strike"] if ascending else -item["strike"]),
    )[:max(1, min(top, MAX_LEVELS_PER_SIDE))]
    for index, level in enumerate(levels):
        level["tier"] = max(1, 5 - index // LEVELS_PER_TIER)
    return levels


def expected_move(rows: list[dict], spot: float) -> tuple[float, float]:
    if spot <= 0:
        return 0.0, 0.0
    expiries = sorted({(row["dte"], row["expiry"]) for row in rows})
    for _dte, expiry in expiries:
        calls = {row["strike"]: row for row in rows if row["expiry"] == expiry and row["side"] == "CALL"}
        puts = {row["strike"]: row for row in rows if row["expiry"] == expiry and row["side"] == "PUT"}
        shared = [strike for strike in calls if strike in puts]
        if not shared:
            continue
        atm = min(shared, key=lambda strike: abs(strike - spot))
        straddle = calls[atm]["mark"] + puts[atm]["mark"]
        if straddle > 0:
            return round(spot + straddle, 2), round(spot - straddle, 2)
    return 0.0, 0.0


def momox_reference_levels(rows: list[dict], count: int = 2) -> list[dict]:
    """Biggest call and put wall for the `count` heaviest-OI expirations."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["expiry"]] = totals.get(row["expiry"], 0.0) + row["oi"]
    heaviest = sorted(sorted(totals, key=totals.get, reverse=True)[:count])
    levels = []
    for expiry in heaviest:
        for side in ("CALL", "PUT"):
            candidates = [row for row in rows if row["expiry"] == expiry and row["side"] == side and row["oi"] > 0]
            if not candidates:
                continue
            best = max(candidates, key=lambda row: (row["oi"], row["volume"]))
            month, day = int(expiry[5:7]), int(expiry[8:10])
            levels.append({
                "side": side,
                "expiry": expiry,
                "label": f"{compact_oi(best['oi'])} {month}/{day}",
                "strike": best["strike"],
                "oi": best["oi"],
            })
    return levels


def compact_oi(value: float) -> str:
    thousands = value / 1000.0
    if thousands >= 10:
        return f"{round(thousands)}k"
    if thousands >= 1:
        return f"{thousands:.1f}".rstrip("0").rstrip(".") + "k"
    return str(int(round(value)))


def fmt_price(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def fmt_oi_bubble(value: float) -> str:
    thousands = value / 1000.0
    text = f"{thousands:.1f}".rstrip("0").rstrip(".")
    return text or "0"


def build_ticker_data(symbol: str, dte: int, provider: str, top: int = MAX_LEVELS_PER_SIDE) -> dict:
    chain, used = fetch_chain(symbol, dte, provider)
    rows = chain_rows(chain)
    if not rows:
        raise RuntimeError("no option rows returned")
    spot = spot_price(chain)
    em_up, em_down = expected_move(rows, spot)
    return {
        "symbol": symbol,
        "provider": used,
        "spot": spot,
        "calls": top_levels(rows, "CALL", top),
        "puts": top_levels(rows, "PUT", top),
        "emU": em_up,
        "emD": em_down,
        "momox": momox_reference_levels(rows),
    }


PLOT_SUFFIXES = [suffix for tier in (1, 2, 3, 4, 5) for suffix in TIER_PLOTS[tier]]
MOMOX_PLOTS = ("oiC1", "oiP1", "oiC2", "oiP2")


def side_assignments(levels: list[dict], prefix: str) -> dict[str, float]:
    values = {f"{prefix}_{suffix}": 0.0 for suffix in PLOT_SUFFIXES}
    slots = {tier: list(TIER_PLOTS[tier]) for tier in TIER_PLOTS}
    for level in levels:
        tier_slots = slots[level["tier"]]
        if tier_slots:
            values[f"{prefix}_{tier_slots.pop(0)}"] = level["strike"]
    return values


def momox_assignments(momox: list[dict]) -> dict[str, float]:
    values = {name: 0.0 for name in MOMOX_PLOTS}
    expiries = sorted({level["expiry"] for level in momox})
    for level in momox:
        slot = expiries.index(level["expiry"]) + 1
        if slot > 2:
            continue
        name = f"oi{'C' if level['side'] == 'CALL' else 'P'}{slot}"
        values[name] = level["strike"]
    return values


def render_thinkscript(tickers: list[dict], dte: int) -> str:
    today = date.today().isoformat()
    symbols = [item["symbol"] for item in tickers]
    out = []
    out.append(
        f"#Highest OI Levels + Momox OI Reference | Generated {today} | DTE window: {dte} | "
        f"Tickers: {','.join(symbols)} | Source: live option chains (AgenticAI generate_oi_thinkscript.py)"
    )
    out.append(
        "input showBubbleEM = yes;input showBubbleBigOI = yes;input showBubbleSmallOI = yes;"
        "input showBubbleOiRef = yes;input showOiRefLines = yes;"
        "def aggregationPeriod = AggregationPeriod.DAY;"
        "def LastPrice = close(priceType = PriceType.LAST);"
        + "".join(f'def is{s} = GetSymbol() == "{s}";' for s in symbols)
        + "def highestBar = BarNumber() == HighestAll(BarNumber());"
        + "".join(f"plot call_{suffix};" for suffix in PLOT_SUFFIXES)
        + "".join(f"plot put_{suffix};" for suffix in PLOT_SUFFIXES)
        + "plot emU;plot emD;"
        + "".join(f"plot {name};" for name in MOMOX_PLOTS)
    )
    out.append("")

    plot_names = (
        [f"call_{suffix}" for suffix in PLOT_SUFFIXES]
        + [f"put_{suffix}" for suffix in PLOT_SUFFIXES]
        + ["emU", "emD", *MOMOX_PLOTS]
    )
    branches = []
    for item in tickers:
        values = side_assignments(item["calls"], "call")
        values.update(side_assignments(item["puts"], "put"))
        values.update({"emU": item["emU"], "emD": item["emD"]})
        values.update(momox_assignments(item["momox"]))
        body = "".join(f"{name}={fmt_price(values[name])};" for name in plot_names)
        branches.append(f"if(is{item['symbol']}){{{body}}}")
    nan_body = "".join(f"{name}=Double.NaN;" for name in plot_names)
    out.append("else ".join(branches) + f"else {{{nan_body}}}")
    out.append("")

    em_bubbles = []
    for item in tickers:
        symbol = item["symbol"]
        if item["emU"] > 0:
            em_bubbles.append(
                f'AddChartBubble(showBubbleEM and is{symbol} and highestBar,emU,"+EM",GlobalColor("emU"), yes);'
                f'AddChartBubble(showBubbleEM and is{symbol} and highestBar,emD,"-EM",GlobalColor("emD"), yes);'
            )
    out.append("".join(em_bubbles))

    for item in tickers:
        symbol = item["symbol"]
        bubbles = []
        levels = (
            [("Call", "CallS", level) for level in item["calls"]]
            + [("Put", "PutS", level) for level in item["puts"]]
        )
        for big_color, small_color, level in sorted(levels, key=lambda entry: -entry[2]["strike"]):
            big = level["tier"] in BIG_TIERS
            flag = "showBubbleBigOI" if big else "showBubbleSmallOI"
            color = big_color if big else small_color
            bubbles.append(
                f'AddChartBubble({flag} and is{symbol} and highestBar,'
                f'{fmt_price(level["strike"])},"{fmt_oi_bubble(level["oi"])}",GlobalColor("{color}"),yes);'
            )
        for level in item["momox"]:
            color = "oiRefC" if level["side"] == "CALL" else "oiRefP"
            bubbles.append(
                f'AddChartBubble(showBubbleOiRef and is{symbol} and highestBar,'
                f'{fmt_price(level["strike"])},"{level["label"]}",GlobalColor("{color}"),yes);'
            )
        out.append("".join(bubbles))
    out.append("")

    weights = {"1": 3, "1a": 3, "1b": 3, "2": 3, "2a": 3, "2b": 3, "3": 2, "3a": 2, "3b": 2,
               "4": 4, "4a": 4, "4b": 4, "5": 5, "5a": 5, "5b": 5}
    out.append("".join(f"call_{s}.SetLineWeight({weights[s]});" for s in PLOT_SUFFIXES))
    out.append("".join(f"put_{s}.SetLineWeight({weights[s]});" for s in PLOT_SUFFIXES))
    out.append(
        "emU.SetLineWeight(3);emD.SetLineWeight(3);"
        "oiC1.SetLineWeight(3);oiP1.SetLineWeight(3);oiC2.SetLineWeight(2);oiP2.SetLineWeight(2);"
        'DefineGlobalColor("Call", CreateColor(0, 153, 204));DefineGlobalColor("Put", CreateColor(204, 0, 102));'
        'DefineGlobalColor("CallS", CreateColor(0, 114, 153));DefineGlobalColor("PutS", CreateColor(155, 0, 76));'
        'DefineGlobalColor("emU", Color.Red);DefineGlobalColor("emD", Color.Green);'
        'DefineGlobalColor("oiRefC", CreateColor(255, 85, 0));DefineGlobalColor("oiRefP", CreateColor(0, 204, 102));'
    )
    small = {"1", "1a", "1b", "2", "2a", "2b"}
    out.append(
        "".join(
            f'call_{s}.SetDefaultColor(GlobalColor("{"CallS" if s in small else "Call"}"));' for s in PLOT_SUFFIXES
        )
        + "".join(
            f'put_{s}.SetDefaultColor(GlobalColor("{"PutS" if s in small else "Put"}"));' for s in PLOT_SUFFIXES
        )
        + 'emU.SetDefaultColor(GlobalColor("emU"));emD.SetDefaultColor(GlobalColor("emD"));'
        + 'oiC1.SetDefaultColor(GlobalColor("oiRefC"));oiC2.SetDefaultColor(GlobalColor("oiRefC"));'
        + 'oiP1.SetDefaultColor(GlobalColor("oiRefP"));oiP2.SetDefaultColor(GlobalColor("oiRefP"));'
    )
    out.append(
        "".join(
            f"call_{s}.SetStyle(Curve.{'SHORT_DASH' if s in small else 'LONG_DASH'});" for s in PLOT_SUFFIXES
        )
        + "".join(
            f"put_{s}.SetStyle(Curve.{'SHORT_DASH' if s in small else 'LONG_DASH'});" for s in PLOT_SUFFIXES
        )
        + "emU.SetStyle(Curve.SHORT_DASH);emD.SetStyle(Curve.SHORT_DASH);"
        + "".join(f"{name}.SetStyle(Curve.SHORT_DASH);" for name in MOMOX_PLOTS)
        + "".join(f"{name}.SetHiding(!showOiRefLines);" for name in MOMOX_PLOTS)
    )
    out.append(
        "input showLine1 = yes;input showLine2 = yes;input showLine3 = yes;input showLine4 = yes;input showLine5 = yes;"
        + "".join(
            f"call_{s}.SetHiding(!showLine{s[0]});put_{s}.SetHiding(!showLine{s[0]});" for s in PLOT_SUFFIXES
        )
    )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tickers", default=DEFAULT_TICKERS)
    parser.add_argument(
        "--dte", type=int, default=46,
        help="Max days to expiration (default 46: weeklies plus the next monthly OPEX)",
    )
    parser.add_argument("--provider", choices=("auto", "tradier", "schwab"), default="auto")
    parser.add_argument("--top", type=int, default=9, help="Max OI levels per side per ticker (1-15, default 9)")
    parser.add_argument("--out", default="", help="Output path (default artifacts/thinkscript/oi_levels_<date>.txt)")
    args = parser.parse_args()

    symbols = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    results, failures = [], []
    for symbol in symbols:
        try:
            data = build_ticker_data(symbol, args.dte, args.provider, args.top)
            results.append(data)
            momox = ", ".join(f'{level["label"]}@{fmt_price(level["strike"])}' for level in data["momox"])
            print(
                f"[ok]   {symbol:6s} spot={data['spot']:.2f} via {data['provider']} | "
                f"calls={len(data['calls'])} puts={len(data['puts'])} | "
                f"EM {data['emD']}-{data['emU']} | momox: {momox}"
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((symbol, str(exc)))
            print(f"[FAIL] {symbol:6s} {exc}")

    if not results:
        print("No ticker succeeded; nothing to generate.", file=sys.stderr)
        return 1

    script = render_thinkscript(results, args.dte)
    out_path = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "artifacts" / "thinkscript" / f"oi_levels_{date.today().isoformat()}.txt"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(script, encoding="utf-8")
    print(f"\nWrote {out_path} ({len(results)} tickers, {len(failures)} failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
