"""Unusual OTM option-activity analysis for the Schwab Individual Trader API.

The Schwab chain endpoint supplies *current* cumulative option volume and
reported open interest.  It does not expose a historical, per-contract option
volume/OI time series, so callers supply retained daily option-chain snapshots
for the prior-day and five-day comparisons.

OAuth is handled by :class:`data.schwab_client.SchwabClient`, which delegates
token creation and refresh to ``schwab-py``.  That keeps client credentials and
refresh tokens out of this module and out of application responses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from data.schwab_client import SchwabClient


LOGGER = logging.getLogger(__name__)


def authenticate() -> SchwabClient:
    """Return an authenticated Schwab client using the local schwab-py token."""
    client = SchwabClient()
    if not client.configured:
        raise RuntimeError("Schwab OAuth is not configured. Re-authenticate in Settings.")
    return client


def get_underlying_price(ticker: str, client: SchwabClient | None = None) -> float:
    """Read the current underlying quote price from Schwab market data."""
    market_client = client or authenticate()
    symbol = str(ticker or "").strip().upper()
    quote = market_client.get_quotes([symbol]).get(symbol, {})
    price = _number(quote.get("last_price"))
    if price <= 0:
        raise RuntimeError(f"Schwab did not return a usable quote for {symbol}.")
    return price


def get_option_chain(ticker: str, expiration_date: str, client: SchwabClient | None = None) -> dict:
    """Fetch a single-leg, all-contract Schwab option chain for one expiry."""
    market_client = client or authenticate()
    expiration = str(expiration_date or "").strip()[:10]
    if not expiration:
        raise ValueError("expiration_date must be an ISO date such as 2026-07-24.")
    chain = market_client.get_option_chain(
        str(ticker or "").strip().upper(),
        contract_type="ALL",
        strike_count=100,
        from_date=_as_datetime(expiration),
        to_date=_as_datetime(expiration),
    )
    if not chain:
        raise RuntimeError("Schwab returned an empty option chain.")
    return chain


def get_atm_strike(current_price: float, strikes: Iterable[float]) -> float | None:
    """Return the strike nearest to the current underlying price."""
    valid = sorted({_number(strike) for strike in strikes if _number(strike) > 0})
    return min(valid, key=lambda strike: abs(strike - current_price)) if valid else None


def classify_strikes(current_price: float, chain: dict, expiration_date: str | None = None) -> dict[str, Any]:
    """Flatten a Schwab chain and classify its ATM, OTM calls, and OTM puts."""
    calls = _flatten_side(chain, "CALL", expiration_date)
    puts = _flatten_side(chain, "PUT", expiration_date)
    strikes = [row["strike"] for row in calls + puts]
    atm = get_atm_strike(current_price, strikes)
    if atm is None:
        return {"atmStrike": None, "atmCalls": [], "atmPuts": [], "otmCalls": [], "otmPuts": []}
    return {
        "atmStrike": atm,
        "atmCalls": [row for row in calls if row["strike"] == atm],
        "atmPuts": [row for row in puts if row["strike"] == atm],
        "otmCalls": [row for row in calls if row["strike"] > current_price],
        "otmPuts": [row for row in puts if row["strike"] < current_price],
    }


def compute_signal_metrics(strike_data: dict, atm_data: dict, historical_data: Iterable[dict]) -> dict:
    """Build comparison metrics without guessing unavailable historical values.

    ``historical_data`` must contain daily snapshots for this exact contract
    (underlying + expiry + strike + option type), ordered or unordered.  Each
    row uses ``snapshot_date``, ``volume``, and ``open_interest``.
    """
    rows = sorted(
        [row for row in historical_data if isinstance(row, dict)],
        key=lambda row: str(row.get("snapshot_date") or row.get("date") or ""),
    )
    today_volume = _number(strike_data.get("volume"))
    today_oi = _number(strike_data.get("openInterest"))
    prior_rows = [row for row in rows if str(row.get("snapshot_date") or row.get("date") or "") < date.today().isoformat()]
    yesterday = prior_rows[-1] if prior_rows else None
    previous_five = prior_rows[-5:]
    yesterday_volume = _number(yesterday.get("volume")) if yesterday else None
    yesterday_oi = _number(yesterday.get("open_interest", yesterday.get("openInterest"))) if yesterday else None
    avg_volume_5d = (
        sum(_number(row.get("volume")) for row in previous_five) / len(previous_five)
        if len(previous_five) == 5 else None
    )
    atm_volume = _number(atm_data.get("volume")) if isinstance(atm_data, dict) else None
    return {
        "today_volume": round(today_volume),
        "yesterday_volume": round(yesterday_volume) if yesterday_volume is not None else None,
        "avg_volume_5d": round(avg_volume_5d, 2) if avg_volume_5d is not None else None,
        "today_oi": round(today_oi),
        "yesterday_oi": round(yesterday_oi) if yesterday_oi is not None else None,
        "atm_volume": round(atm_volume) if atm_volume is not None else None,
        "history_days": len(prior_rows),
        "today_volume_gt_yesterday": today_volume > yesterday_volume if yesterday_volume is not None else None,
        "today_volume_gt_avg_5d": today_volume > avg_volume_5d if avg_volume_5d is not None else None,
        "today_oi_gt_yesterday": today_oi > yesterday_oi if yesterday_oi is not None else None,
        "otm_volume_gt_atm": today_volume > atm_volume if atm_volume is not None else None,
    }


def score_signal(metrics: dict) -> dict[str, Any]:
    """Score only confirmed comparisons; unknown history cannot earn points."""
    comparisons = (
        "today_volume_gt_yesterday",
        "today_volume_gt_avg_5d",
        "today_oi_gt_yesterday",
        "otm_volume_gt_atm",
    )
    score = sum(1 for key in comparisons if metrics.get(key) is True)
    strength = "strong" if score == 4 else "medium" if score >= 2 else "weak"
    return {"score": score, "strength": strength, "comparisonsAvailable": sum(metrics.get(key) is not None for key in comparisons)}


def compute_basic_score(metrics: dict) -> int:
    """Return the transparent 0–4 rule score without awarding unknown data."""
    return int(score_signal(metrics)["score"])


def compute_weighted_score(metrics: dict) -> dict[str, Any]:
    """Return a 0–100 score with 25 points for every confirmed rule.

    Missing daily history is intentionally worth zero and is reported through
    ``comparisonsAvailable``; the caller must display an insufficient-history
    state instead of treating a low score as proof of weak activity.
    """
    rules = (
        ("volumeVsYesterday", "today_volume_gt_yesterday"),
        ("volumeVs5DayAverage", "today_volume_gt_avg_5d"),
        ("oiVsYesterday", "today_oi_gt_yesterday"),
        ("otmVsAtmVolume", "otm_volume_gt_atm"),
    )
    points = {label: 25 if metrics.get(key) is True else 0 for label, key in rules}
    available = sum(metrics.get(key) is not None for _, key in rules)
    return {
        "weightedScore": sum(points.values()),
        "points": points,
        "comparisonsAvailable": available,
        "historyComplete": available == len(rules),
    }


def label_signal(weighted_score: float, history_complete: bool = True) -> str:
    """Label a transparent score while preserving an honest history state."""
    if not history_complete:
        return "Insufficient history"
    if weighted_score >= 80:
        return "Strong"
    if weighted_score >= 60:
        return "Medium"
    return "Weak"


def bucket_by_dte(days_to_expiration: int) -> str | None:
    """Assign a 0–31 DTE expiry to a swing-trade dashboard bucket."""
    dte = int(days_to_expiration)
    if dte == 0:
        return "0DTE"
    if 1 <= dte <= 3:
        return "1-3 DTE"
    if 4 <= dte <= 7:
        return "4-7 DTE"
    if 8 <= dte <= 14:
        return "8-14 DTE"
    if 15 <= dte <= 21:
        return "15-21 DTE"
    if 22 <= dte <= 31:
        return "22-31 DTE"
    return None


def summarize_bucket(bucket_data: Iterable[dict]) -> dict[str, Any]:
    """Summarize live activity while keeping calls and puts independent."""
    rows = list(bucket_data)
    by_side = {side: [row for row in rows if row.get("optionType") == side] for side in ("CALL", "PUT")}
    strongest = {
        side: max(
            by_side[side],
            key=lambda row: (row.get("weightedScore", 0), row.get("basicScore", 0), row.get("metrics", {}).get("today_volume", 0)),
            default=None,
        )
        for side in by_side
    }
    return {
        "totalCallVolume": round(sum(row["metrics"]["today_volume"] for row in by_side["CALL"])),
        "totalPutVolume": round(sum(row["metrics"]["today_volume"] for row in by_side["PUT"])),
        "totalCallOpenInterest": round(sum(row["metrics"]["today_oi"] for row in by_side["CALL"])),
        "totalPutOpenInterest": round(sum(row["metrics"]["today_oi"] for row in by_side["PUT"])),
        "strongestBullish": strongest["CALL"],
        "strongestBearish": strongest["PUT"],
        "strongestOverall": max(rows, key=lambda row: (row.get("weightedScore", 0), row.get("basicScore", 0)), default=None),
        "contracts": len(rows),
    }


def detect_cross_expiry_buildup(all_bucket_data: Iterable[dict], current_price: float) -> dict[str, list[dict]]:
    """Find nearby OTM strike areas active in two or more DTE buckets.

    This identifies *activity clustering*, not trade direction or an entry.
    The grouping width is one percent of the underlying (minimum $1) so the
    same method works for low- and high-priced underlyings.
    """
    width = max(round(float(current_price or 0) * 0.01, 2), 1.0)
    grouped: dict[tuple[str, float], list[dict]] = {}
    for row in all_bucket_data:
        if row.get("signalLabel") not in {"Strong", "Medium"}:
            continue
        center = round(float(row.get("strike") or 0) / width) * width
        grouped.setdefault((str(row.get("optionType") or ""), center), []).append(row)
    output = {"bullish": [], "bearish": []}
    for (side, center), rows in grouped.items():
        buckets = sorted({str(row.get("dteBucket") or "") for row in rows if row.get("dteBucket")})
        if len(buckets) < 2:
            continue
        candidate = {
            "optionType": side,
            "strikeArea": round(center, 4),
            "buckets": buckets,
            "expiries": sorted({str(row.get("expiry") or "") for row in rows}),
            "strikes": sorted({float(row.get("strike") or 0) for row in rows}),
            "averageWeightedScore": round(sum(float(row.get("weightedScore") or 0) for row in rows) / len(rows), 1),
            "contracts": len(rows),
        }
        output["bullish" if side == "CALL" else "bearish"].append(candidate)
    for candidates in output.values():
        candidates.sort(key=lambda item: (len(item["buckets"]), item["averageWeightedScore"], item["contracts"]), reverse=True)
    return output


def build_dashboard_response(
    ticker: str,
    current_price: float,
    chain_payload: dict,
    historical_snapshots: Iterable[dict],
    as_of: date | None = None,
) -> dict[str, Any]:
    """Build the complete 0–14 DTE analysis response from one live chain.

    The function is deliberately independent of FastAPI and SQLite. The caller
    supplies the persisted snapshot rows, making it straightforward to test.
    """
    as_of = as_of or date.today()
    history = list(historical_snapshots or [])
    calls = _flatten_side(chain_payload, "CALL", None)
    puts = _flatten_side(chain_payload, "PUT", None)
    by_expiry: dict[str, dict[str, list[dict]]] = {}
    for contract in calls + puts:
        expiry = str(contract.get("expiry") or "")[:10]
        try:
            dte = (datetime.fromisoformat(expiry).date() - as_of).days
        except ValueError:
            continue
        if not 0 <= dte <= 31:
            continue
        by_expiry.setdefault(expiry, {"CALL": [], "PUT": [], "dte": dte})[contract["optionType"]].append(contract)

    signals: list[dict] = []
    for expiry, sides in by_expiry.items():
        strikes = [contract["strike"] for side_rows in (sides["CALL"], sides["PUT"]) for contract in side_rows]
        atm = get_atm_strike(current_price, strikes)
        if atm is None:
            continue
        for side in ("CALL", "PUT"):
            atm_contract = next((item for item in sides[side] if item["strike"] == atm), {})
            for contract in sides[side]:
                is_otm = contract["strike"] > current_price if side == "CALL" else contract["strike"] < current_price
                if not is_otm:
                    continue
                contract_history = [
                    row for row in history
                    if str(row.get("expiry") or "")[:10] == expiry
                    and str(row.get("side") or row.get("optionType") or "").upper() == side
                    and abs(_number(row.get("strike")) - contract["strike"]) < 0.0001
                ]
                metrics = compute_signal_metrics(contract, atm_contract, contract_history)
                weighted = compute_weighted_score(metrics)
                signals.append({
                    "ticker": str(ticker or "").upper(), "optionType": side, "strike": contract["strike"], "expiry": expiry,
                    "dte": sides["dte"], "dteBucket": bucket_by_dte(sides["dte"]), "atmStrike": atm,
                    "metrics": metrics, "basicScore": compute_basic_score(metrics), **weighted,
                    "signalLabel": label_signal(weighted["weightedScore"], weighted["historyComplete"]),
                })

    bucket_names = ("0DTE", "1-3 DTE", "4-7 DTE", "8-14 DTE", "15-21 DTE", "22-31 DTE")
    bucket_summaries = {name: summarize_bucket(row for row in signals if row.get("dteBucket") == name) for name in bucket_names}
    all_summary = summarize_bucket(signals)
    buildup = detect_cross_expiry_buildup(signals, current_price)
    strongest_swing = max(
        buildup["bullish"] + buildup["bearish"],
        key=lambda item: (len(item["buckets"]), item["averageWeightedScore"]),
        default=None,
    )
    return {
        "ticker": str(ticker or "").upper(), "currentStockPrice": round(float(current_price or 0), 4),
        "asOf": as_of.isoformat(), "signals": signals, "buckets": bucket_summaries,
        "total0To31Dte": all_summary, "crossExpiryBuildup": buildup, "strongestSwingCandidate": strongest_swing,
        "historyNote": "Yesterday and five-day values come only from locally retained daily option-chain snapshots. Missing values remain unavailable.",
    }


def find_strongest_bullish_signal(chain_data: Iterable[dict]) -> dict | None:
    """Return the best scored OTM call; volume/OI alone do not prove direction."""
    return _strongest(chain_data, "CALL")


def find_strongest_bearish_signal(chain_data: Iterable[dict]) -> dict | None:
    """Return the best scored OTM put; volume/OI alone do not prove direction."""
    return _strongest(chain_data, "PUT")


def print_results(results: dict) -> None:
    """Print the requested concise terminal summary for one underlying."""
    print(f"Ticker: {results.get('ticker', '--')}")
    print(f"Current stock price: {results.get('current_stock_price', results.get('currentStockPrice', '--'))}")
    print(f"ATM strike: {results.get('atm_strike', results.get('atmStrike', '--'))}")
    for label, signal in (
        ("Bullish", results.get("strongest_bullish") or results.get("strongestBullish")),
        ("Bearish", results.get("strongest_bearish") or results.get("strongestBearish")),
    ):
        signal = signal or {}
        metrics = signal.get("metrics", {})
        print(f"{label} OTM {str(signal.get('optionType') or '--').lower()} strike: {signal.get('strike', '--')}")
        print(f"  volume today / yesterday / 5d avg: {metrics.get('today_volume', '--')} / {metrics.get('yesterday_volume', '--')} / {metrics.get('avg_volume_5d', '--')}")
        print(f"  OI today / yesterday: {metrics.get('today_oi', '--')} / {metrics.get('yesterday_oi', '--')}")
        print(f"  score / strength: {signal.get('score', '--')} / {signal.get('strength', '--')}")


def _flatten_side(chain: dict, option_type: str, expiration_date: str | None) -> list[dict]:
    key = "callExpDateMap" if option_type == "CALL" else "putExpDateMap"
    selected_expiration = str(expiration_date or "").strip()[:10]
    rows: list[dict] = []
    for expiry_key, strikes in (chain.get(key) or {}).items():
        expiry = str(expiry_key).split(":", 1)[0]
        if selected_expiration and expiry != selected_expiration:
            continue
        if not isinstance(strikes, dict):
            continue
        for contracts in strikes.values():
            for contract in contracts if isinstance(contracts, list) else []:
                strike = _number(contract.get("strikePrice"))
                if strike <= 0:
                    continue
                rows.append({
                    "strike": strike,
                    "optionType": option_type,
                    "expiry": str(contract.get("expirationDate") or expiry)[:10],
                    "volume": _number(contract.get("totalVolume")),
                    "openInterest": _number(contract.get("openInterest")),
                    "bid": _number(contract.get("bid")),
                    "ask": _number(contract.get("ask")),
                    "mark": _number(contract.get("mark")),
                })
    return rows


def _strongest(signals: Iterable[dict], option_type: str) -> dict | None:
    candidates = [signal for signal in signals if str(signal.get("optionType") or "").upper() == option_type]
    return max(
        candidates,
        key=lambda signal: (int(signal.get("score") or 0), _number(signal.get("metrics", {}).get("today_volume")), _number(signal.get("metrics", {}).get("today_oi"))),
        default=None,
    )


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_datetime(iso_date: str):
    from datetime import datetime
    return datetime.fromisoformat(iso_date)
